# LG Simple Smart TV (NetCast 2016) for Home Assistant

Custom integration for the 2016 non-webOS LG "Smart TV" line (Simple Smart / NetCast 4.0,
MStar M2, codename *arabica*), e.g. **32LH570B**. These TVs have no webOS SSAP and
no NetCast ROAP/UDAP API, so neither `webostv` nor `lg_netcast` works with them.

## What it does

| Feature | How |
|---|---|
| On/off state | DIAL `GET :56789/apps/YouTube` returns 200 only while the TV is on |
| Turn on | Wake-on-LAN magic packet (needs **Settings > Network > Mobile TV On**) |
| Volume / mute | UPnP RenderingControl SOAP |
| Sources | DIAL apps: YouTube, Netflix, Deezer (launch, status, stop) |
| Play media | URL / media source via AVTransport; YouTube links via DIAL `v=<id>` |
| Turn off | **Not possible over the network** (see below) |

Install through HACS (custom repository) or copy `custom_components/lg_simplesmart`, restart, then
add **LG Simple Smart TV** with the TV's IP while the TV is on. The MAC used for wake comes
from the TV's own DIAL SSDP `WAKEUP` header.

## Reverse engineering notes (firmware 04.10.06)

Firmware `simplesmart-atsc-m2-builds-arabica-m2-15-04.10.06-prodkey_usb_V2_SECURED.epk`
(LG support site), decrypted with [epk2extract](https://github.com/openlgtv/epk2extract)
using the `m2 - MStar SimpleSmart` AES key; DIAL server analysed with REA + Ghidra.

Listening ports on a running TV (full TCP scan): only these four.

| Port | Process | Notes |
|---|---|---|
| 1160 | Platinum/Neptune GENA sink | event callback of the TV's own DMP; NOTIFY → 200 |
| 1457* | `dmr2` (Platinum DMR) | MediaRenderer; *port chosen at boot, found via SSDP |
| 56789 | `dialserver` (Mongoose) | DIAL 2.0 REST, `/apps/{YouTube,Netflix,Deezer}` |
| 56790 | `dialserver` | `dd.xml` / `ee.xml` DIAL descriptions |

- `request_handler` (`dialserver` @0xd404) returns early while `_isTVPowerOff` is set, so in
  standby DIAL answers nothing useful. That is the on/off probe.
- `handle_mcast` (`quick_ssdp.c`, @0x100d8) answers DIAL M-SEARCH with `ee.xml` +
  `WAKEUP: MAC=<wlan mac>;Timeout=60` when `isTVSettingOn_MobileTVOn` is set (DIAL 2.x wake).
- No ROAP/UDAP/HDCP (`/roap/api`, `/udap/api`) strings exist anywhere in the rootfs. The
  services `alljoyn` and `remote_service` are referenced in `svclist_alljoyn*.rc` but live in
  the downloadable `/mnt/addon` partition, absent from the EPK and not listening on the TV.
- Standby is only reachable through `CECP_SetStandby` (HDMI-CEC/SimpLink) or the IR remote.
  To turn it off from HA, use a CEC-capable device on the TV's HDMI or an IR blaster.
