# DMM Utility GUI — Project Summary

A native-feeling Mac GUI for the Fluke 287/289 multimeter over the USB IR
serial cable, built per [prd.md](prd.md) as a free replacement for Fluke
Connect (which has no Mac version). Built June 10, 2026.

## Design decisions

These were confirmed up front before building:

| Decision | Choice | Rationale |
| --- | --- | --- |
| Tech stack | Python + PySide6 + pyqtgraph | Reuses the existing Python protocol code directly; Qt looks good on macOS; pyqtgraph handles fast live plotting |
| Live recording | App-side logging | The remote protocol can only poll live readings (`qddb`); it cannot start the meter's internal recording. The app polls at a chosen rate, plots, and saves CSV |
| Command coverage (PRD goal 8) | Dedicated UI for main features + raw Console tab | Console exposes every known command with tooltips, including reverse-engineered ones |
| Distribution | Run from source (`venv/bin/python -m dmm_gui`) | Simplest; a .app bundle (py2app/PyInstaller) can be added later |

## What was built

A sidebar-navigation main window with five views:

- **Live** ([dmm_gui/live_view.py](dmm_gui/live_view.py)) — large readout
  (value, unit, function, mode, range), rolling time-series plot, Record/Stop
  with configurable sample interval (0.1 s–1 hr) and optional auto-stop
  duration, CSV export of recorded sessions. Overload readings display as
  "OL" and become gaps in the plot rather than 1e37 spikes.
- **Screen** ([dmm_gui/screen_view.py](dmm_gui/screen_view.py)) — live
  capture of the meter LCD via the undocumented `qlcdbm` command (chunked,
  gzipped bitmap; protocol from vsilves' fluke-live.py on eevblog), with
  Pause/Play and PNG export. Captures only while the tab is visible so
  other views aren't slowed by the ~0.5 s screen transfers.
- **Memory** ([dmm_gui/memory_view.py](dmm_gui/memory_view.py)) — tree of
  everything stored on the meter (recordings, min/max sessions, peak
  sessions, saved measurements); downloads recordings sample-by-sample with
  progress bar and Cancel; plots Primary/Min/Max curves; detail tables; CSV
  export for every item type.
- **Meter** ([dmm_gui/setup_view.py](dmm_gui/setup_view.py)) — identity and
  configuration readout, one-click clock sync to the Mac, editable owner info
  (company/contact/operator/site), the 8 save-name slots, and the DS/RMP/RI
  reset commands (destructive ones require confirmation).
- **Console** ([dmm_gui/console_view.py](dmm_gui/console_view.py)) — send any
  raw protocol command. A picker lists all 6 documented commands plus 14
  reverse-engineered ones with explanatory tooltips
  ([dmm_gui/commands.py](dmm_gui/commands.py)); binary responses render as
  hex dumps.

Connection bar: serial port picker (FTDI/`cu.usbserial` ports listed first,
macOS `tty.*` duplicates hidden), rescan, Connect/Disconnect, and a
"Connect at launch" option. Port, auto-connect, sample interval, last export
directory, and window geometry persist between launches via QSettings
(PRD goal 10). Tooltips throughout explain what each control sends to the
meter (PRD goal 9).

## Architecture

```text
dmm_gui/
  protocol.py      Fluke28x: serial transport + binary parsing (no Qt)
  worker.py        DmmWorker (QThread) + Requests signal bundle
  commands.py      command catalog for the Console (tooltips, doc status)
  app.py           MainWindow, connection bar, sidebar, wiring, QSettings
  live_view.py     Live readout / plot / recording
  screen_view.py   live LCD capture (qlcdbm) with pause and PNG export
  memory_view.py   stored-data browser / download / export
  setup_view.py    meter identity, properties, names, resets
  console_view.py  raw command console with hex dump
  __main__.py      python -m dmm_gui entry point
tests/
  fake_meter.py    wire-level Fluke 289 simulator (FakeSerial)
  test_protocol.py protocol-layer test against the simulator
  test_gui.py      end-to-end GUI test (offscreen) against the simulator
```

### Protocol layer

[dmm_gui/protocol.py](dmm_gui/protocol.py) is a refactor of the proven CLI
code in [fluke_28x_dmm_util/dmm_util.py](fluke_28x_dmm_util/dmm_util.py)
(which is untouched) into a thread-safe `Fluke28x` class:

- Exceptions (`DmmError`, `DmmCommandError`, `DmmNoData`) instead of
  `print` + `sys.exit`; no globals; a lock around each transaction.
- Wire format: commands are ASCII + `<CR>` at 115200 8N1. Responses are an
  ACK digit (`0` ok, `1` syntax, `2` execution, `5` no data) + `<CR>`,
  followed by either comma-separated ASCII or `#0`-prefixed binary, ending
  in `<CR>`.
- The meter's binary numbers are little-endian u16/s16 and *word-swapped*
  little-endian doubles. The original index-gymnastics decoders were replaced
  with `struct` calls and **verified bit-identical against the originals over
  20,000 random buffers** before being trusted.
- Enum fields (function, unit, state, …) are decoded via `qemap` tables
  fetched from the meter and cached per connection.
- Covers: `ID`, `QM`, `qddb` (live), `qsls` (counts), `qrsi`/`qsrr`
  (recordings), `qmmsi`/`qpsi` (min-max/peak), `qsmr` (saved measurements),
  `qsavname`/`savname`, `qmp`/`mp`, `qmpq`/`mpq`, clock sync, DS/RI/RMP.

Three protocol quirks worth remembering:

- **The ACK arrives before the payload.** A real meter transmits `0\r` and
  then the response body with a measurable gap, and a bare `0\r` is also a
  complete response for no-data commands — so framing alone can't tell
  "done" from "more coming". `_transact` therefore waits a ~20 ms grace
  window (`Fluke28x.GRACE`) of line silence before trusting a
  complete-looking response. Removing it brings back the
  "a bytes-like object is required, not 'list'" failure seen on first real
  hardware contact (the fake meter never hits this because it responds
  atomically; `SlowAckSerial` in tests/test_protocol.py is the regression
  test).

- **Meter clock convention**: `set datetime` writes
  `timegm(local_time_tuple)` — i.e. local wall time stored *as if* UTC — and
  all meter timestamps are therefore decoded with `gmtime`. This matches the
  CLI and FlukeView behavior; don't "fix" it to real UTC.
- **AVERAGE is a sum**: in recording samples (`qsrr`), the stored AVERAGE
  reading is a running sum and the `duration` field is the sample count; the
  true average is `sum / count` (see `_avg_value` in memory_view.py).

### Threading model

All serial I/O runs on one worker `QThread`
([dmm_gui/worker.py](dmm_gui/worker.py)) so the UI never blocks on the slow
optical link:

- The GUI emits signals on a `Requests` bundle; Qt's queued connections run
  the corresponding `DmmWorker` slots on the worker thread. Results come back
  via worker signals connected to **bound methods** of GUI objects.
- Live polling is a `QTimer` owned by the worker (created lazily on the
  worker thread). Long operations (recording downloads) naturally serialize
  with polling because the worker is single-threaded; timer ticks coalesce.
- Download cancellation is a plain boolean on the worker set directly from
  the GUI thread (atomic under the GIL), checked per sample.
- Shutdown: `worker_thread.finished.connect(worker.deleteLater)` ensures the
  worker and its timer are destroyed on their own thread.

Two real threading bugs were caught and fixed during testing:

1. Worker signals were initially connected to **lambdas** in `MainWindow`.
   Qt runs receiver-less callables on the *emitting* thread, so status-bar
   updates ran on the worker thread ("Timers cannot be started from another
   thread"). Fix: connect to bound methods so Qt queues to the GUI thread.
   Rule of thumb for this codebase: never connect a worker signal to a
   lambda.
2. The worker's poll timer was garbage-collected on the main thread after
   the worker thread exited. Fixed with the `deleteLater` pattern above.

### Testing without hardware

No meter was available during development, so
[tests/fake_meter.py](tests/fake_meter.py) implements a wire-level Fluke 289
simulator: a `FakeSerial` drop-in for `serial.Serial` that answers every
command with correctly framed ASCII/binary responses (including the
word-swapped doubles and `qemap` tables). Two test scripts run against it:

```sh
venv/bin/python tests/test_protocol.py                      # protocol layer
QT_QPA_PLATFORM=offscreen venv/bin/python tests/test_gui.py # full app E2E
```

The GUI test exercises connect → live polling → recording → inventory →
download → min/max detail → console (ASCII + binary) → owner-info
round-trip → disconnect. All views were also screenshot-verified offscreen.

**Caveat**: the binary layouts for `qrsi`/`qsrr`/`qmmsi`/`qsmr` are
reverse-engineered (from the CLI), and the simulator was built to those same
layouts — so the first Refresh/Download against a real meter is the true
validation of that path. If something mis-parses, the Console hex dump is
the debugging tool.

## How to run

```sh
python3 -m venv venv                      # once
venv/bin/pip install -r requirements.txt  # once (pyserial, PySide6, pyqtgraph)
venv/bin/python -m dmm_gui
```

## App bundle

`packaging/build_app.sh` builds `dist/DMM Utility.app` with PyInstaller
(install with `venv/bin/pip install pyinstaller` first). The script first
runs `packaging/gen_icon.py`, which renders the app icon with Qt at all
required sizes and packs it into `icon.icns` via `iconutil` — no image
assets are checked in; the icon is code. The bundle is ad-hoc signed
(`com.douggaff.dmm-utility`, arm64) and shares QSettings with the
run-from-source version. Build artifacts (`build/`, `dist/`, the generated
spec and icon) are gitignored; `launcher.py` is the PyInstaller entry point.

## Possible future work

- Validate reverse-engineered parsers against real hardware, then handle any
  model/firmware variations found.
- Surface `QDDA`/secondary readings in the Live view (currently primary/LIVE
  only).
- Dark-mode-aware plot colors are set at startup from the palette; live
  theme switching would require reacting to palette change events.
