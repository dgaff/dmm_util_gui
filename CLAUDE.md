# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A PySide6/Qt desktop app (`dmm_gui/`) for the Fluke 287/289 digital multimeter,
plus the original `fluke_28x_dmm_util/` CLI it was forked from. Two ways to reach
the meter:

- **USB IR cable (Fluke IR189USB)** — FTDI serial, 115200 8N1. Full text/binary
  command protocol; unlocks every screen in the app. This is the primary path.
- **BLE IR adapter (Fluke ir3000FC, advertises as `IR 3000 FC`)** — an
  undocumented binary streaming interface that only broadcasts live readings.
  When connected over BLE, only the Live view is usable; everything else is
  greyed out.

## Commands

```sh
# Run the app (from source)
venv/bin/python -m dmm_gui

# Tests — no meter needed; both run against a simulated Fluke 289
venv/bin/python tests/test_protocol.py
QT_QPA_PLATFORM=offscreen venv/bin/python tests/test_gui.py

# One-time setup
python3 -m venv venv && venv/bin/pip install -r requirements.txt

# Build macOS .app bundle (needs pyinstaller in venv)
packaging/build_app.sh          # -> dist/DMM Utility.app

# Build Windows .exe (must run on 64-bit x86 Windows; no cross-compile)
powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1
```

There is no lint config and no pytest — the two test files are plain scripts with
`assert`s and a `main()`; run them directly. To exercise a single case, edit the
relevant `main()` or call the individual `test_*`/assertion block.

There is also a `run-dmm-util-gui` skill for launching the app against real
hardware and taking screenshots.

## Architecture

Three layers, strictly separated by thread:

1. **`protocol.py` — `Fluke28x`**: blocking, synchronous meter I/O. Knows nothing
   about Qt. `command()` sends an ASCII command + `\r` and returns a list of ASCII
   fields, raw `bytes` for a binary (`#0`-prefixed) response, or `[]` for a bare
   ACK. Binary parsers (`qddb`, `qrsi`, `qsrr`, `qmmsi`/`qpsi`, `qsmr`, `qlcdbm`)
   decode word-swapped little-endian records — see the binary helpers at the top
   (`get_double` swaps the two 4-byte words).

2. **`worker.py` — `DmmWorker` + `Requests`**: runs on a dedicated `QThread`
   (created in `MainWindow._build_worker`). The GUI never calls `Fluke28x`
   directly. Instead it *emits* a signal on `Requests` (queued connection ⇒ the
   slot runs on the worker thread) and receives results via signals on
   `DmmWorker`. Live polling and screen capture are driven by `QTimer`s owned by
   the worker. **`cancel_requested` is a plain bool written directly from the GUI
   thread** (the one exception to the signal rule), read by long download loops.

3. **`app.py` — `MainWindow`**: the connection bar, sidebar, and five stacked
   pages (`live_view`, `screen_view`, `memory_view`, `setup_view`,
   `console_view`). `_wire()` is the single place all worker↔view signals are
   connected — read it to see the full data flow.

**BLE is a separate stack**: `ble_worker.py` — `BleWorker` runs a bleak asyncio
event loop forever on its own daemon thread. GUI calls schedule coroutines with
`run_coroutine_threadsafe`. It keeps a *continuously running* scanner (the
adapter is a bursty, ~15 s-gap advertiser, so a one-shot discover often sees
nothing) and reports adapters seen within `FRESH_SECONDS`. Incoming notifications
go through `fluke289_bt_decode.decode_record`.

### Threading rules (breaking these reintroduces real bugs)

- **Never connect a `DmmWorker` or `BleWorker` signal to a lambda.** Qt runs
  receiver-less callables on the *emitting* (worker/asyncio) thread; use bound
  methods of GUI objects so the call queues onto the GUI thread. The lambdas in
  `_wire()` are the deliberate exceptions — they only touch `self.req`/settings,
  which is safe.
- In the worker, only `serial.SerialException`/`OSError` mean the connection is
  lost (`_serial_lost`). Every other exception is recoverable (meter between dial
  positions, popup open, framing glitch) — surface it and keep going.
- `Fluke28x._transact` keeps a ~20 ms `GRACE` quiet-window after a response
  *looks* complete. The meter sends a `0\r` ACK **before** the payload, and a bare
  `0\r` is itself a valid complete response, so the framing check can match too
  early. Removing GRACE breaks live reads on real hardware (regression:
  `SlowAckSerial` in `tests/test_protocol.py`).

### Domain conventions (not derivable from code or the spec PDF)

- **Meter clock stores local wall time as-if-UTC.** `sync_clock` writes
  `calendar.timegm(local_tuple)`; `parse_time` reads it back with `time.gmtime`.
  This matches FlukeView/the CLI — do **not** "fix" it to real UTC.
- In `qsrr` recording samples the stored `AVERAGE` reading is a **running sum**;
  the true average is `sum / duration`, where the `duration` field is the sample
  count.
- **Memory deletion is category-wide only.** The undocumented `csd
  <ALL|RECORDED|MIN_MAX|PEAK|MEASUREMENT>` clears a whole category; there is no
  per-item delete. Exposed in the Memory view behind a confirmation.
- The binary layouts for `qrsi`/`qsrr`/`qmmsi`/`qsmr` are **reverse-engineered
  and not yet validated against real hardware.** `tests/fake_meter.py` simulates
  the meter at the wire level *using these same layouts*, so a passing test does
  not prove the layout is correct.

### Command catalog

`commands.py` is the single source of truth for the Console view's command
picker: each entry marks `documented` (from `Fluke289_remote_spec28X.pdf`) vs.
reverse-engineered, and whether the response is `binary`. Add new commands here
so they get tooltips and correct hex-dump handling.

## Testing model

`tests/fake_meter.py` — `FakeSerial` — implements enough of pyserial's API to
answer protocol commands with correct binary framing. Tests monkeypatch
`protocol.serial.Serial = FakeSerial` **before importing the app**. `test_gui.py`
also redirects `QSettings` to a temp ini file so it never touches the user's real
macOS preferences. When adding a protocol command, teach `FakeSerial` to answer
it or GUI tests that reach it will fail.

## Not part of the app

`dmm_gui/fluke-live.py`, `fluke_decode_lab.py`, and `fluke_ble_discover.py` are
standalone reverse-engineering / recon utilities (screen capture demo, BLE
record decoder lab, BLE service dumper). They're kept for reference and are not
imported by the app.
