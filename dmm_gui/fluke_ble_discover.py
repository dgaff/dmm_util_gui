#!/usr/bin/env python3
"""
fluke_ble_discover.py — Recon tool for the Fluke ir3000FC BLE-IR adapter.

Phases:
  1. SCAN     - Find BLE devices, flag anything that looks like Fluke FC gear
                (vendor UUID base b698xxxx-7562-11e2-b50d-00163e46f8fe, or a
                name containing 'FC' / 'Fluke' / '289').
  2. EXPLORE  - Connect and dump every service / characteristic / descriptor,
                including properties and current values for readable chars.
  3. LISTEN   - Subscribe to every notify/indicate characteristic and print
                whatever arrives (hex + ASCII) for a while.
  4. PROBE    - (opt-in, --probe) Write a serial command (default 'ID\\r') to
                each writable characteristic, one at a time, and watch for a
                response on the notify characteristics. If the 289's IR serial
                protocol is tunneled, you'll see a familiar reply here.

Usage:
  pip install bleak
  python3 fluke_ble_discover.py                       # scan only
  python3 fluke_ble_discover.py --connect             # scan + explore + listen
  python3 fluke_ble_discover.py --connect --probe     # ... + write 'ID\\r' probes
  python3 fluke_ble_discover.py --address <UUID> --connect
  python3 fluke_ble_discover.py --connect --probe --cmd 'QM\\r' --listen 20

Notes:
  * On macOS, bleak reports devices by a CoreBluetooth UUID, not a MAC address.
  * Make sure the ir3000FC is awake (button pressed / LED active) and clipped
    onto the 289 with the meter powered on before scanning.
  * macOS will prompt for Bluetooth permission for your terminal app the first
    time; grant it in System Settings > Privacy & Security > Bluetooth.
"""

import argparse
import asyncio
import sys
from datetime import datetime

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("bleak is not installed. Run: pip install bleak")

FLUKE_UUID_FRAGMENT = "7562-11e2-b50d-00163e46f8fe"  # Fluke FC vendor base
NAME_HINTS = ("fluke", "fc", "289", "287", "ir3000")

LOGFILE = None  # set in main() if --log


def log(msg=""):
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{stamp}] {msg}" if msg else ""
    print(line)
    if LOGFILE:
        LOGFILE.write(line + "\n")
        LOGFILE.flush()


def fmt_bytes(data: bytes) -> str:
    """Render bytes as hex plus a printable-ASCII gloss."""
    hexpart = " ".join(f"{b:02x}" for b in data)
    asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{hexpart}  |{asciipart}|"


def looks_like_fluke(device, adv) -> bool:
    name = (device.name or adv.local_name or "").lower()
    if any(h in name for h in NAME_HINTS):
        return True
    for uuid in (adv.service_uuids or []):
        if FLUKE_UUID_FRAGMENT in uuid.lower():
            return True
    return False


async def scan(timeout: float):
    log(f"Scanning for {timeout:.0f}s ... (make sure the ir3000FC is awake)")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    candidates = []
    log(f"Found {len(found)} device(s):")
    for address, (device, adv) in sorted(found.items()):
        fluke = looks_like_fluke(device, adv)
        tag = "  <-- POSSIBLE FLUKE" if fluke else ""
        log(f"  {address}  rssi={adv.rssi:>4}  name={device.name or adv.local_name!r}{tag}")
        if adv.service_uuids:
            for u in adv.service_uuids:
                log(f"      advertised service: {u}")
        if adv.manufacturer_data:
            for cid, payload in adv.manufacturer_data.items():
                log(f"      mfr data (company 0x{cid:04x}): {fmt_bytes(payload)}")
        if fluke:
            candidates.append(device)
    return candidates


async def explore(client: BleakClient):
    log("=" * 70)
    log("GATT DATABASE")
    log("=" * 70)
    notifiable = []
    writable = []
    for service in client.services:
        log(f"[Service] {service.uuid}  ({service.description})")
        for char in service.characteristics:
            props = ",".join(char.properties)
            log(f"  [Char] {char.uuid}  handle={char.handle}  props={props}")
            if "read" in char.properties:
                try:
                    value = await client.read_gatt_char(char)
                    log(f"         value: {fmt_bytes(bytes(value))}")
                except Exception as e:
                    log(f"         read failed: {e}")
            if "notify" in char.properties or "indicate" in char.properties:
                notifiable.append(char)
            if "write" in char.properties or "write-without-response" in char.properties:
                writable.append(char)
            for desc in char.descriptors:
                try:
                    dval = await client.read_gatt_descriptor(desc.handle)
                    log(f"    [Desc] {desc.uuid}  value: {fmt_bytes(bytes(dval))}")
                except Exception:
                    log(f"    [Desc] {desc.uuid}")
    log("")
    log(f"Summary: {len(notifiable)} notifiable char(s), {len(writable)} writable char(s)")
    return notifiable, writable


async def listen_and_probe(client, notifiable, writable, listen_secs, probe, cmd_bytes):
    if not notifiable:
        log("No notifiable characteristics found; nothing to listen on.")
        return

    def make_handler(uuid):
        def handler(_sender, data: bytearray):
            log(f"NOTIFY {uuid}: {fmt_bytes(bytes(data))}")
        return handler

    log("=" * 70)
    log(f"Subscribing to {len(notifiable)} characteristic(s)...")
    subscribed = []
    for char in notifiable:
        try:
            await client.start_notify(char, make_handler(char.uuid))
            subscribed.append(char)
            log(f"  subscribed: {char.uuid}")
        except Exception as e:
            log(f"  subscribe failed for {char.uuid}: {e}")

    if probe and writable:
        log("-" * 70)
        log(f"PROBE MODE: writing {cmd_bytes!r} to each writable characteristic")
        log("Watch for NOTIFY lines after each write.")
        for char in writable:
            use_response = "write" in char.properties
            log(f"  -> writing to {char.uuid} (response={use_response})")
            try:
                await client.write_gatt_char(char, cmd_bytes, response=use_response)
            except Exception as e:
                log(f"     write failed: {e}")
            await asyncio.sleep(2.0)  # give the meter time to answer

    log("-" * 70)
    log(f"Listening for {listen_secs}s. Change modes / turn the dial on the 289")
    log("to see if anything streams. Ctrl-C to stop early.")
    try:
        await asyncio.sleep(listen_secs)
    except asyncio.CancelledError:
        pass

    for char in subscribed:
        try:
            await client.stop_notify(char)
        except Exception:
            pass


async def main():
    global LOGFILE
    p = argparse.ArgumentParser(description="Fluke ir3000FC BLE discovery tool")
    p.add_argument("--scan-time", type=float, default=10.0, help="scan duration in seconds (default 10)")
    p.add_argument("--address", help="connect to this device address/UUID directly, skip auto-pick")
    p.add_argument("--connect", action="store_true", help="connect and explore GATT after scanning")
    p.add_argument("--listen", type=int, default=30, help="seconds to listen for notifications (default 30)")
    p.add_argument("--probe", action="store_true", help="write a probe command to writable characteristics")
    p.add_argument("--cmd", default="ID\r", help=r"probe command string (default 'ID\r'; '\r' and '\n' escapes ok)")
    p.add_argument("--log", help="also append output to this file")
    args = p.parse_args()

    if args.log:
        LOGFILE = open(args.log, "a")

    cmd_bytes = args.cmd.encode().decode("unicode_escape").encode("latin-1")

    target = None
    if args.address:
        log(f"Looking for device {args.address} ...")
        target = await BleakScanner.find_device_by_address(args.address, timeout=args.scan_time)
        if target is None:
            sys.exit("Device not found. Is the adapter awake?")
    else:
        candidates = await scan(args.scan_time)
        if not args.connect:
            log("")
            log("Scan-only mode done. Re-run with --connect (and optionally --address) to explore.")
            return
        if not candidates:
            sys.exit("No Fluke-looking devices found. Re-run with --address <addr> to force one.")
        target = candidates[0]
        if len(candidates) > 1:
            log(f"Multiple candidates; using the first: {target.address}")

    log(f"Connecting to {target.address} ({target.name}) ...")
    async with BleakClient(target, timeout=20.0) as client:
        log(f"Connected. MTU: {client.mtu_size}")
        notifiable, writable = await explore(client)
        await listen_and_probe(client, notifiable, writable, args.listen, args.probe, cmd_bytes)
    log("Disconnected. Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
