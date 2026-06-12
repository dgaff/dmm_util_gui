#!/usr/bin/env python3
"""
NOTE: this is a utility for decoding the BLE binary protocol. It's not used in the app.

fluke_decode_lab.py — Live monitor + capture tool for the ir3000FC record (b698290f).

The 16-byte record is two 8-byte frames (see fluke289_decode.py):
    bytes 0..7  = primary reading,  bytes 8..15 = secondary reading.
Both are now decoded. This tool shows the fully-decoded primary reading (and the
secondary when present) live, with the raw bytes underneath, and still captures
labeled samples to CSV — handy for filling remaining gaps (the byte[5] range
table and the byte[7] status bits in modes we haven't exercised yet).

Per-frame layout:
    60 ea 00 06 05 02 00 18  ‖  89 4e 00 08 01 02 00 00
    |__mag__| f  fn r  rsv st  |  (same layout, secondary)
  bytes 0..2 magnitude LE, byte3 flags(sign/prefix/decimals),
  byte4 function, byte5 range, byte6 reserved, byte7 status.

Live aids while you work:
  * Continuous line showing the DECODED reading (primary, plus secondary when
    present), then the raw 16 bytes with a '‖' at the frame boundary and any
    bytes that changed since the last packet HIGHLIGHTED.
  * Capture labeled samples to CSV.

Controls (type at the prompt, then Enter):
  <text>   capture current packet, label it with <text>  (e.g. "3.142 V DC")
  <Enter>  capture with an empty label (quick grab)
  m        toggle continuous monitor printing on/off
  d        print which byte positions have varied across all samples so far
  q        quit

Usage:
  python3 fluke_decode_lab.py --address <UUID>
  python3 fluke_decode_lab.py --address <UUID> --csv 289_decode.csv
  python3 fluke_decode_lab.py --address <UUID> --char b698290f

Requires fluke289_decode.py in the same directory (or on PYTHONPATH).
"""

import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime

try:
    from bleak import BleakClient
except ImportError:
    sys.exit("bleak is not installed. Run: pip install bleak")

try:
    from fluke289_bt_decode import decode_record
except ImportError:
    sys.exit("fluke289_decode.py not found. Keep it in the same directory as this script.")

PRIMARY_CHAR = "b698290f-7562-11e2-b50d-00163e46f8fe"

# ANSI for highlighting changed bytes (degrade gracefully if not a TTY).
HL = "\033[7m" if sys.stdout.isatty() else ""
RST = "\033[0m" if sys.stdout.isatty() else ""


def hexrow(data: bytes, prev: bytes | None) -> str:
    """Render bytes as hex, highlighting changes, with '‖' marking the boundary
    between the primary frame (bytes 0..7) and the secondary frame (8..15)."""
    cells = []
    for i, b in enumerate(data):
        changed = prev is not None and i < len(prev) and prev[i] != b
        cell = f"{b:02x}"
        cell = f"{HL}{cell}{RST}" if changed else cell
        if i == 8:
            cells.append("\u2016")  # frame boundary: primary | secondary
        cells.append(cell)
    return " ".join(cells)


def decode_line(data: bytes) -> str:
    """One-line decoded reading using the real decoder; never raises.
    Shows the primary reading, plus the secondary frame when present.
    Uses getattr for the newer fields so a stale fluke289_decode.py degrades
    gracefully instead of crashing the BLE notification callback."""
    try:
        r = decode_record(data)
    except Exception as e:
        return f"(decode error: {e})"
    parts = [f"{r.display:<14}"]
    parts.append(f"raw={r.raw}")
    parts.append(f"dec={r.decimals}")
    parts.append(f"fn=0x{r.function_code:02x}")
    parts.append(f"rng=0x{r.range_code:02x}")
    status = getattr(r, "status", 0)
    if status:
        parts.append(f"st=0x{status:02x}")
    if r.negative:
        parts.append("NEG")
    if r.overload:
        parts.append("OL")
    line = "  ".join(parts)
    secondary = getattr(r, "secondary", None)
    if secondary is not None:
        line += f"   \u2016 2nd: {secondary.display}"
    return line


class Lab:
    def __init__(self, csv_path):
        self.latest = None
        self.prev = None
        self.samples = []          # list of (label, bytes)
        self.monitor = True
        self.csv_path = csv_path
        self._init_csv()

    def _init_csv(self):
        new = not os.path.exists(self.csv_path)
        self.csv_file = open(self.csv_path, "a", newline="")
        self.writer = csv.writer(self.csv_file)
        if new:
            cols = ["timestamp", "label", "hex"] + [f"b{i}" for i in range(16)]
            self.writer.writerow(cols)
            self.csv_file.flush()

    def on_packet(self, data: bytes):
        self.prev = self.latest
        self.latest = bytes(data)
        if self.monitor:
            row = hexrow(self.latest, self.prev)
            print(f"\r  {decode_line(self.latest)}   [{row}]   ", end="", flush=True)

    def capture(self, label: str):
        if self.latest is None:
            print("\n  (no packet yet — is the meter on and streaming?)")
            return
        pkt = self.latest
        self.samples.append((label, pkt))
        stamp = datetime.now().isoformat(timespec="milliseconds")
        bytecols = list(pkt) + [""] * (16 - len(pkt))
        self.writer.writerow([stamp, label, pkt.hex()] + bytecols[:16])
        self.csv_file.flush()
        print(f"\n  captured #{len(self.samples)}: {pkt.hex()}  label={label!r}")
        print(f"           decode: {decode_line(pkt)}")
        if len(pkt) >= 16:
            # secondary frame (bytes 8..15) shown raw, as requested, alongside
            # byte 6 (reserved) and byte 7 (primary status).
            print(f"           b6=0x{pkt[6]:02x} (reserved)  b7=0x{pkt[7]:02x} (status)")
            sec = " ".join(f"{b:02x}" for b in pkt[8:16])
            print(f"           secondary frame [8:15]: {sec}")

    def variance_report(self):
        if not self.samples:
            print("\n  no samples yet.")
            return
        n = max(len(p) for _, p in self.samples)
        varied = []
        for i in range(n):
            vals = {p[i] for _, p in self.samples if i < len(p)}
            if len(vals) > 1:
                varied.append((i, sorted(vals)))
        print(f"\n  across {len(self.samples)} samples, these byte positions varied:")
        for i, vals in varied:
            shown = " ".join(f"{v:02x}" for v in vals[:12])
            more = " ..." if len(vals) > 12 else ""
            print(f"    byte[{i:2}]: {shown}{more}")
        steady = [i for i in range(n) if i not in {v[0] for v in varied}]
        print(f"    steady positions: {steady}")

    def close(self):
        self.csv_file.close()


async def stdin_loop(lab: Lab):
    loop = asyncio.get_event_loop()
    print("\nReady. Set a known reading on the 289, then type what it shows and Enter.")
    print("Commands: <label>=capture  m=monitor toggle  d=variance  q=quit\n")
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        cmd = line.rstrip("\n")
        if cmd == "q":
            return
        elif cmd == "m":
            lab.monitor = not lab.monitor
            print(f"  monitor {'on' if lab.monitor else 'off'}")
        elif cmd == "d":
            lab.variance_report()
        else:
            lab.capture(cmd)


async def main():
    p = argparse.ArgumentParser(description="ir3000FC live-record decode lab")
    p.add_argument("--address", required=True, help="device address / CoreBluetooth UUID")
    p.add_argument("--char", default=PRIMARY_CHAR, help="characteristic to capture (default b698290f)")
    p.add_argument("--csv", default="fluke_decode.csv", help="CSV dataset path (appended to)")
    args = p.parse_args()

    lab = Lab(args.csv)
    print(f"Connecting to {args.address} ...")
    async with BleakClient(args.address, timeout=20.0) as client:
        print(f"Connected. MTU: {client.mtu_size}. Logging to {args.csv}")

        def handler(_sender, data: bytearray):
            lab.on_packet(data)

        await client.start_notify(args.char, handler)
        try:
            await stdin_loop(lab)
        finally:
            try:
                await client.stop_notify(args.char)
            except Exception:
                pass
    lab.variance_report()
    lab.close()
    print("\nDataset saved. Send me the CSV and we'll crack the field layout.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
