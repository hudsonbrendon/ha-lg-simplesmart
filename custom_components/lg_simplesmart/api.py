"""Client for LG "Simple Smart" TVs (2016 non-webOS, e.g. 32LH570B).

Reverse engineered from firmware 04.10.06 (simplesmart-atsc-m2-builds-arabica):
the TV has no ROAP/UDAP/SSAP remote API. The only network surfaces are

* DIAL 2.0 REST (`dialserver`, port 56789): launch/stop/status of YouTube,
  Netflix and Deezer. It answers 404 while the TV is in standby.
* UPnP MediaRenderer (`dmr2`, Platinum, port chosen at boot): volume, mute and
  URL playback through RenderingControl/AVTransport SOAP.
* Wake: with "Mobile TV On" enabled the DIAL SSDP reply carries
  `WAKEUP: MAC=..;Timeout=60` and the TV powers on from a WoL magic packet.

There is no network power off; the firmware only goes to standby via the remote
or HDMI-CEC.
"""

from __future__ import annotations

import asyncio
import socket
import time
import xml.etree.ElementTree as ET
from html import escape
from urllib.parse import urljoin, urlparse

import aiohttp

DIAL_PORT = 56789  # ponytail: fixed in dialserver (gDialPort), dd.xml port is not
APPS = ("YouTube", "Netflix", "Deezer")  # every DIAL app registered by this firmware
ST_DIAL = "urn:dial-multiscreen-org:service:dial:1"
ST_DMR = "urn:schemas-upnp-org:device:MediaRenderer:1"
RC = "RenderingControl"
AVT = "AVTransport"
TIMEOUT = aiohttp.ClientTimeout(total=6)  # TV Wi-Fi often has ~1 s RTT
LAUNCH_WAIT = 45  # seconds from standby to a launchable app (1st POST wakes, then 503s)


class LGError(Exception):
    """TV unreachable or rejected the request."""


def parse_ssdp(data: bytes) -> dict[str, str]:
    """Header dict (lowercase keys) from one SSDP response."""
    lines = data.decode(errors="replace").split("\r\n")[1:]
    return {
        k.strip().lower(): v.strip()
        for k, _, v in (line.partition(":") for line in lines)
        if k.strip()
    }


def wake_mac(headers: dict[str, str]) -> str | None:
    """MAC from a DIAL `WAKEUP: MAC=..;Timeout=..` header."""
    for part in headers.get("wakeup", "").split(";"):
        key, _, value = part.partition("=")
        if key.strip().upper() == "MAC":
            return value.strip().lower()
    return None


def ssdp_search(host: str, st: str, timeout: float = 6.0) -> dict[str, str] | None:
    """Blocking M-SEARCH; first response from `host`. Run in an executor."""
    msg = (
        "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
        f'MAN: "ssdp:discover"\r\nMX: 2\r\nST: {st}\r\n\r\n'
    ).encode()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.5)
        end = time.monotonic() + timeout
        next_send = 0.0
        while (now := time.monotonic()) < end:
            if now >= next_send:  # the TV drops packets while its Wi-Fi dozes
                sock.sendto(msg, ("239.255.255.250", 1900))
                next_send = now + 1.5
            try:
                data, addr = sock.recvfrom(4096)
            except TimeoutError:
                continue
            if addr[0] == host:
                return parse_ssdp(data)
    return None


def send_magic_packet(mac: str, host: str | None = None) -> None:
    """WoL to broadcast (and the last known IP, for routers that relay it)."""
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    packet = b"\xff" * 6 + raw * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in ("255.255.255.255", host):
            if target:
                sock.sendto(packet, (target, 9))


def parse_dial_state(xml: str) -> bool:
    """True when the DIAL app status document says running."""
    node = ET.fromstring(xml).find("{urn:dial-multiscreen-org:schemas:dial}state")
    return node is not None and node.text == "running"


def control_urls(location: str, xml: str) -> dict[str, str]:
    """{'RenderingControl': url, 'AVTransport': url} from a device description."""
    ns = {"d": "urn:schemas-upnp-org:device-1-0"}
    urls = {}
    for svc in ET.fromstring(xml).iterfind(".//d:service", ns):
        kind = svc.findtext("d:serviceType", "", ns).split(":")[-2]
        urls[kind] = urljoin(location, svc.findtext("d:controlURL", "", ns))
    return urls


def didl(url: str, title: str, mime: str) -> str:
    """Minimal DIDL-Lite; dmr2 rejects SetAVTransportURI without protocolInfo."""
    upnp_class = {"audio": "audioItem.musicTrack", "image": "imageItem.photo"}.get(
        mime.split("/")[0], "videoItem"
    )
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        f'<item id="0" parentID="-1" restricted="1"><dc:title>{escape(title)}</dc:title>'
        f"<upnp:class>object.item.{upnp_class}</upnp:class>"
        f'<res protocolInfo="http-get:*:{mime}:*">{escape(url)}</res></item></DIDL-Lite>'
    )


class LGSimpleSmart:
    """Async client; one instance per TV."""

    def __init__(self, session: aiohttp.ClientSession, host: str, mac: str | None):
        self.session = session
        self.host = host
        self.mac = mac
        self._controls: dict[str, str] = {}

    # --- DIAL -------------------------------------------------------------
    def _dial(self, app: str) -> str:
        return f"http://{self.host}:{DIAL_PORT}/apps/{app}"

    async def is_on(self) -> bool:
        try:
            async with self.session.get(self._dial(APPS[0]), timeout=TIMEOUT) as r:
                return r.status == 200
        except (aiohttp.ClientError, TimeoutError):
            return False

    async def running_app(self) -> str | None:
        for app in APPS:
            try:
                async with self.session.get(self._dial(app), timeout=TIMEOUT) as r:
                    if r.status == 200 and parse_dial_state(await r.text()):
                        return app
            except (aiohttp.ClientError, TimeoutError, ET.ParseError):
                continue
        return None

    async def launch(self, app: str, payload: str = "", wait: float = LAUNCH_WAIT) -> None:
        """POST /apps/<app>; YouTube takes `v=<video id>` as payload.

        From standby (Mobile TV On) the first POST wakes the TV but answers 503
        while the app launcher boots, so retry until `wait` runs out.
        """
        deadline = time.monotonic() + wait
        while True:
            try:
                async with self.session.post(
                    self._dial(app),
                    data=payload.encode(),
                    headers={"Content-Type": "text/plain; charset=utf-8"},
                    timeout=TIMEOUT,
                ) as r:
                    if r.status in (200, 201):
                        return
                    if r.status != 503:
                        raise LGError(f"DIAL launch {app}: HTTP {r.status}")
                    error = f"HTTP {r.status}"
            except (aiohttp.ClientError, TimeoutError) as err:
                error = str(err) or type(err).__name__
            if time.monotonic() >= deadline:
                raise LGError(f"DIAL launch {app}: {error} (TV still waking up?)")
            await asyncio.sleep(3)

    async def stop(self, app: str) -> None:
        try:
            async with self.session.delete(f"{self._dial(app)}/run", timeout=TIMEOUT):
                pass
        except (aiohttp.ClientError, TimeoutError) as err:
            raise LGError(f"DIAL stop {app}: {err}") from err

    # --- UPnP DMR ---------------------------------------------------------
    async def _discover_dmr(self) -> None:
        loop = asyncio.get_running_loop()
        headers = await loop.run_in_executor(None, ssdp_search, self.host, ST_DMR)
        if not headers or "location" not in headers:
            raise LGError("MediaRenderer not found via SSDP")
        location = headers["location"]
        if urlparse(location).hostname != self.host:
            raise LGError(f"unexpected DMR location {location}")
        try:
            async with self.session.get(location, timeout=TIMEOUT) as r:
                self._controls = control_urls(location, await r.text())
        except (aiohttp.ClientError, TimeoutError, ET.ParseError) as err:
            raise LGError(f"DMR description: {err}") from err

    async def _soap(self, service: str, action: str, **args: str | int) -> dict[str, str]:
        body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args.items())
        urn = f"urn:schemas-upnp-org:service:{service}:1"
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{urn}">{body}</u:{action}></s:Body></s:Envelope>'
        )
        for attempt in (0, 1):  # port changes on every TV boot: rediscover once
            if not self._controls or attempt:
                await self._discover_dmr()
            try:
                async with self.session.post(
                    self._controls[service],
                    data=envelope.encode(),
                    headers={
                        "Content-Type": 'text/xml; charset="utf-8"',
                        "SOAPACTION": f'"{urn}#{action}"',
                    },
                    timeout=TIMEOUT,
                ) as r:
                    text = await r.text()
                    if r.status != 200:
                        raise LGError(f"{action}: HTTP {r.status}")
            except (aiohttp.ClientError, TimeoutError) as err:
                if attempt:
                    raise LGError(f"{action}: {err}") from err
                continue
            resp = ET.fromstring(text).find(f".//{{{urn}}}{action}Response")
            return {c.tag: c.text or "" for c in (resp if resp is not None else [])}
        raise LGError(f"{action}: unreachable")  # pragma: no cover

    async def get_volume(self) -> int:
        r = await self._soap(RC, "GetVolume", InstanceID=0, Channel="Master")
        return int(r["CurrentVolume"])

    async def set_volume(self, volume: int) -> None:
        await self._soap(
            RC, "SetVolume", InstanceID=0, Channel="Master", DesiredVolume=volume
        )

    async def get_mute(self) -> bool:
        r = await self._soap(RC, "GetMute", InstanceID=0, Channel="Master")
        return r["CurrentMute"] == "1"

    async def set_mute(self, mute: bool) -> None:
        await self._soap(
            RC, "SetMute", InstanceID=0, Channel="Master", DesiredMute=int(mute)
        )

    async def transport_state(self) -> str:
        r = await self._soap(AVT, "GetTransportInfo", InstanceID=0)
        return r["CurrentTransportState"]

    async def play_url(self, url: str, mime: str, title: str = "Home Assistant") -> None:
        await self._soap(
            AVT,
            "SetAVTransportURI",
            InstanceID=0,
            CurrentURI=url,
            CurrentURIMetaData=didl(url, title, mime),
        )
        await self.play()

    async def play(self) -> None:
        await self._soap(AVT, "Play", InstanceID=0, Speed=1)

    async def pause(self) -> None:
        await self._soap(AVT, "Pause", InstanceID=0)

    async def stop_media(self) -> None:
        await self._soap(AVT, "Stop", InstanceID=0)

    # --- power ------------------------------------------------------------
    def wake(self) -> None:
        if not self.mac:
            raise LGError("MAC unknown; enable Mobile TV On and reconfigure")
        send_magic_packet(self.mac, self.host)
