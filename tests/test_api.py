"""Parser checks against bytes captured from a 32LH570B (fw 04.10.06)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "lg_simplesmart"))
import api  # noqa: E402

SSDP = (
    b"HTTP/1.1 200 OK\r\nLOCATION: http://192.168.1.50:56790/ee.xml\r\n"
    b"ST: urn:dial-multiscreen-org:service:dial:1\r\n"
    b"WAKEUP: MAC=AA:BB:CC:DD:EE:FF;Timeout=60\r\n\r\n"
)
RUNNING = (
    '<?xml version="1.0" encoding="UTF-8"?>\r\n<service xmlns="urn:dial-multiscreen-org:'
    'schemas:dial" dialVer="2.0">\r\n  <name>YouTube</name>\r\n  <options allowStop="true"/>'
    '\r\n  <state>running</state>\r\n  <link rel="run" href="run"/>\r\n</service>\r\n'
)
DESC = (
    '<root xmlns="urn:schemas-upnp-org:device-1-0"><device><serviceList><service>'
    "<serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>"
    "<controlURL>/RenderingControl/u/control.xml</controlURL></service></serviceList>"
    "</device></root>"
)


def test_parsers():
    headers = api.parse_ssdp(SSDP)
    assert headers["location"].endswith("/ee.xml")
    assert api.wake_mac(headers) == "aa:bb:cc:dd:ee:ff"
    assert api.wake_mac({}) is None
    assert api.parse_dial_state(RUNNING)
    assert not api.parse_dial_state(RUNNING.replace("running", "stopped"))
    assert api.control_urls("http://tv:1457/", DESC) == {
        "RenderingControl": "http://tv:1457/RenderingControl/u/control.xml"
    }
    assert 'protocolInfo="http-get:*:audio/mpeg:*"' in api.didl("http://x/a&b.mp3", "t", "audio/mpeg")
    assert "a&amp;b" in api.didl("http://x/a&b.mp3", "t", "audio/mpeg")


async def _launch_retries_503():
    import asyncio

    from aiohttp import ClientSession, web

    hits = []
    statuses = [503, 503, 201, 404]

    async def handler(request):
        hits.append(request.method)
        return web.Response(status=statuses.pop(0))

    app = web.Application()
    app.router.add_post("/apps/YouTube", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", api.DIAL_PORT).start()
    real_sleep, api.asyncio.sleep = asyncio.sleep, lambda _: real_sleep(0)
    try:
        async with ClientSession() as session:
            client = api.LGSimpleSmart(session, "127.0.0.1", None)
            await client.launch("YouTube")
            assert hits == ["POST"] * 3
            try:  # anything but 503 fails at once
                await client.launch("YouTube")
            except api.LGError as err:
                assert "404" in str(err) and len(hits) == 4, err
            else:
                raise AssertionError("expected LGError")
    finally:
        api.asyncio.sleep = real_sleep
        await runner.cleanup()


def test_launch_retries_503():
    import asyncio

    asyncio.run(_launch_retries_503())


if __name__ == "__main__":
    test_parsers()
    test_launch_retries_503()
    print("ok")
