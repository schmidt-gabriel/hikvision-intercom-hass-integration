#!/usr/bin/env python3
"""Channel 2 experiment: does the device deliver call events via httpHosts?

Starts an HTTP server that logs everything it receives raw, points the device's
/ISAPI/Event/notification/httpHosts at it, waits for you to ring the doorbell,
and RESTORES the device configuration at the end.

Usage:
    HIK_PASSWORD=... python tools/capture_httphost.py 10.0.20.26 \
        --listen-ip 10.0.10.12 --port 8199 --seconds 120

Everything received is written to docs/captures/ to become a test fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import functools
import os
import pathlib
import sys

# Import the isapi subpackage directly, bypassing the component __init__.py
# (which pulls Home Assistant in). isapi/ is HA-free on purpose.
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "hikvision_intercom")
)

from aiohttp import web
from isapi import const as c
from isapi.client import IsapiClient, IsapiError

CAPTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "docs" / "captures"
WEBHOOK_PATH = "/hikvision"

received: list[dict] = []
# Rings observed by polling during the window. Without this you cannot tell
# "push does not deliver" apart from "nobody rang the doorbell".
rang: list[str] = []

# Unbuffered: this script is meant to be watched live.
print = functools.partial(
    __builtins__["print"] if isinstance(__builtins__, dict) else __builtins__.print, flush=True
)


async def handle(request: web.Request) -> web.Response:
    """Log whatever request the device sends."""
    body = await request.read()
    stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    idx = len(received) + 1

    print(f"\n{'=' * 70}")
    print(f"[{stamp}]  #{idx}  {request.method} {request.path_qs}  de {request.remote}")
    print(f"{'=' * 70}")
    for k, v in request.headers.items():
        print(f"  {k}: {v}")
    if body:
        print(f"\n  --- body ({len(body)} bytes) ---")
        try:
            print("  " + body.decode("utf-8").replace("\n", "\n  "))
        except UnicodeDecodeError:
            print(f"  <binary> {body[:200].hex()}")
    else:
        print("\n  <no body>")

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    ext = (
        "xml"
        if body.lstrip().startswith(b"<")
        else "json"
        if body.lstrip().startswith(b"{")
        else "bin"
    )
    (CAPTURE_DIR / f"httphost-{idx:02d}.{ext}").write_bytes(body)

    received.append({"method": request.method, "path": request.path_qs, "body": body})
    # The device expects 200; anything else and it may stop sending.
    return web.Response(text="OK")


def build_config(url: str, ip: str, port: int) -> str:
    """Build the HttpHostNotificationList XML pointing at us."""
    return (
        '<HttpHostNotificationList version="2.0" '
        'xmlns="http://www.isapi.org/ver20/XMLSchema">'
        '<HttpHostNotification version="2.0">'
        "<id>1</id>"
        f"<url>{url}</url>"
        "<protocolType>HTTP</protocolType>"
        "<parameterFormatType>JSON</parameterFormatType>"
        "<addressingFormatType>ipaddress</addressingFormatType>"
        f"<ipAddress>{ip}</ipAddress>"
        f"<portNo>{port}</portNo>"
        "<httpAuthenticationMethod>none</httpAuthenticationMethod>"
        "</HttpHostNotification>"
        "</HttpHostNotificationList>"
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--username", default="admin")
    ap.add_argument(
        "--listen-ip", required=True, help="IP of this machine that the device can reach"
    )
    ap.add_argument("--port", type=int, default=8199)
    ap.add_argument("--seconds", type=int, default=120)
    args = ap.parse_args()

    password = os.environ.get("HIK_PASSWORD")
    if not password:
        print("Set HIK_PASSWORD in the environment.", file=sys.stderr)
        return 2

    client = IsapiClient(args.host, args.username, password)

    # 1. Save the original config so we can restore it later.
    #
    # With no entry registered (the state after a DELETE) the GET answers 400
    # badParameters. That is not an error: it is the free slot, which is
    # exactly the state we will want to return to at the end.
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    backup = CAPTURE_DIR / "httphosts-original.xml"
    try:
        original = await client.request("GET", c.HTTP_HOSTS, raw=True)
        backup.write_bytes(original)
        print(f"Original httpHosts config saved to {backup}")
    except IsapiError:
        print("No notification configured on the device (slot is free).")

    # 2. Start the capture server.
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", args.port)
    await site.start()
    print(f"Listening on http://{args.listen_ip}:{args.port}{WEBHOOK_PATH}")

    configured = False
    try:
        # 3. Point the device at us.
        config = build_config(WEBHOOK_PATH, args.listen_ip, args.port)
        await client.request("PUT", c.HTTP_HOSTS, data=config)
        configured = True
        print("httpHosts configured on the device.\n")

        readback = await client.request("GET", c.HTTP_HOSTS, raw=True)
        print("Read back from the device:")
        print("  " + readback.decode().replace("\n", "\n  ").strip())

        print(f"\n>>> RING THE DOORBELL NOW. Listening for {args.seconds}s. <<<\n")

        # In parallel, follow callStatus so both channels can be compared.
        async def poll_status():
            last = None
            while True:
                try:
                    status = await client.get_call_status()
                    if status != last:
                        stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        print(f"[{stamp}]  callStatus (channel 3): {last} -> {status}")
                        if status in ("ring", "onCall"):
                            rang.append(stamp)
                        last = status
                except IsapiError:
                    pass
                await asyncio.sleep(0.5)

        poller = asyncio.create_task(poll_status())
        try:
            await asyncio.sleep(args.seconds)
        finally:
            poller.cancel()

    finally:
        # 4. Always restore.
        #
        # The original XML cannot simply be sent back: the device answers 400
        # badXmlFormat to an empty <url></url>. That "blank slot" is generated
        # by the firmware and is not writable through the API. DELETE removes
        # the entry, which is functionally "no notification configured".
        if configured:
            try:
                await client.request("DELETE", c.HTTP_HOSTS)
                print("\nhttpHosts cleared (DELETE). No notification configured.")
            except IsapiError as err:
                print(f"\nWARNING: failed to clear httpHosts: {err}", file=sys.stderr)
                print("The device is still pointing at this machine.", file=sys.stderr)
                print(
                    f"Clear it with: curl --digest -u admin:PASSWORD -X DELETE "
                    f"http://{args.host}/ISAPI/Event/notification/httpHosts",
                    file=sys.stderr,
                )
        await runner.cleanup()
        await client.close()

    print(f"\n{'=' * 70}")
    print(f"RESULT: {len(received)} request(s) from the device, {len(rang)} ring(s) observed.")

    if received:
        print("\nChannel 2 (httpHosts) WORKS: real push is available.")
    elif not rang:
        print("\nINCONCLUSIVE: the doorbell was not rung during the window.")
        print("callStatus stayed 'idle' throughout, so there was nothing to push.")
        print("Run it again and ring the doorbell while the window is open.")
    else:
        print(f"\nChannel 2 does NOT deliver. A real ring happened ({', '.join(rang)}) with the")
        print("device configured, and no request reached the webhook.")
        print("Channel 3 (callStatus polling) is the way: it already caught the ring above.")
        print("\n(If the webhook is on another subnet, rule out firewall/VLAN")
        print(" blocking before treating this as definitive for your environment.)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
