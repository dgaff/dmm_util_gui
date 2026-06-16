---
name: run-dmm-util-gui
description: Run, launch, build, or screenshot the DMM Utility desktop GUI (PySide6/Qt app for Fluke 287/289 multimeters over serial or the ir3000FC BLE adapter). Use to start the app, drive a BLE rescan, or verify a change works in the real window.
---

# Run the DMM Utility GUI

A PySide6/Qt desktop app that talks to Fluke 287/289 multimeters over a USB-IR
serial cable or the Fluke ir3000FC Bluetooth-LE adapter (`'IR 3000 FC'`). It is
**macOS-native** here (Core Bluetooth, real radio) — there is no headless mode;
launching opens a real window and scans the real Bluetooth adapter.

Because it's interactive, drive it with the committed Qt driver
[.claude/skills/run-dmm-util-gui/driver.py](.claude/skills/run-dmm-util-gui/driver.py),
which builds the real `MainWindow`, clicks the rescan button, waits out the
adapter's bursty advertising, then dumps the port dropdown + status bar and
saves a screenshot. **Paths below are relative to the repo root.**

## Prerequisites

- macOS with a working Bluetooth adapter (for BLE features) and/or an FTDI
  USB-IR serial cable (for serial). First BLE run prompts for Bluetooth
  permission — grant it in System Settings → Privacy & Security → Bluetooth.
- Python 3.10+ (3.14 used this session).

## Build

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Confirm deps import:

```bash
./venv/bin/python -c "import bleak, PySide6, pyqtgraph, serial; print('deps import OK')"
```

## Run (agent path) — driver + screenshot

```bash
./venv/bin/python .claude/skills/run-dmm-util-gui/driver.py --shot /tmp/dmm_window.png --wait 14
```

It prints the port dropdown contents and status bar, then saves the PNG. Expected
output when the ir3000FC adapter is awake and in range:

```
clicking rescan...
--- port dropdown ---
  [0] '/dev/cu.Bluetooth-Incoming-Port'  data='/dev/cu.Bluetooth-Incoming-Port'
  [1] '/dev/cu.debug-console'  data='/dev/cu.debug-console'
  [2] 'BLE: IR 3000 FC (FC49085B…)'  data='ble:FC49085B-...'
status: BLE scan: 1 'IR 3000 FC' adapter(s) found
saved screenshot -> /tmp/dmm_window.png
```

**Always open the screenshot and look at it** — a rendered window shows the
Live/Screen/Memory/Meter/Console sidebar and an empty plot; a blank frame means
the app didn't actually launch.

Driver options:
- `--shot PATH` — screenshot path (default `/tmp/dmm_window.png`)
- `--wait SECS` — seconds to scan before reporting (default 15; keep it above
  `SCAN_SECONDS`=12 in [dmm_gui/ble_worker.py](dmm_gui/ble_worker.py) so a cold
  scan has time to land a burst)
- `--no-click` — just show the window, skip the rescan (use when there's no BLE
  hardware and you only want to prove the UI renders)

## Run (human path)

```bash
./venv/bin/python launcher.py
```

Opens the window and runs until you close it. Use this for hands-on testing;
it's not scriptable, so prefer the driver for verification.

## Gotchas

- **The ir3000FC is a bursty advertiser** — it goes silent up to ~15 s between
  advertising bursts. A short scan window can see nothing; this is why the worker
  keeps a `BleakScanner` running continuously and reports anything seen within
  `FRESH_SECONDS`=30. Set the driver `--wait` above ~12 s or a cold scan may
  report zero adapters even though the device is present.
- **No BLE hardware / adapter asleep** → the dropdown simply has no `BLE:` entry
  and the status reads `0 ... adapter(s) found`. The app still launches fine;
  use `--no-click` to verify rendering without depending on the radio.
- **`ModuleNotFoundError: No module named 'dmm_gui'`** — only happens if you
  bypass the driver. The driver self-adds the repo root to `sys.path`; running
  the package directly from another cwd needs `PYTHONPATH=<repo root>`.
- **`setup.py` is for a different package** (`fluke_28x_dmm_util`, the CLI). The
  GUI's dependencies live in `requirements.txt` — install from there, not
  `pip install -e .`.

## Troubleshooting

- **`ModuleNotFoundError: No module named 'PySide6'`** — you used the system
  Python. Use `./venv/bin/python`, not `python3`.
