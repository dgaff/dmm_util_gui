"""Launch the real DMM Utility GUI, drive a BLE rescan, and screenshot it.

This builds the actual MainWindow (real QApplication, real BleWorker, real
Bluetooth radio) — not a stub — then clicks the rescan button, waits long
enough to span the ir3000FC's bursty advertising gaps, dumps the port dropdown
and status-bar text to stdout, saves a PNG, and quits.

Run from the project root with the venv interpreter:

    ./venv/bin/python .claude/skills/run-dmm-util-gui/driver.py

Options:
    --shot PATH   where to save the screenshot   (default /tmp/dmm_window.png)
    --wait SECS   seconds to scan before report  (default 15; > SCAN_SECONDS)
    --no-click    just show the window, don't trigger a rescan
"""
import argparse
import sys
from pathlib import Path

# Make `dmm_gui` importable no matter the cwd: repo root is 4 levels up
# (.claude/skills/run-dmm-util-gui/driver.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtGui import QPalette
import pyqtgraph as pg

from dmm_gui.app import MainWindow

p = argparse.ArgumentParser()
p.add_argument('--shot', default='/tmp/dmm_window.png')
p.add_argument('--wait', type=float, default=15.0)
p.add_argument('--no-click', action='store_true')
args = p.parse_args()

app = QApplication(sys.argv)
app.setApplicationName('DMM Utility')
app.setOrganizationName('dmm_util_gui')
palette = app.palette()
pg.setConfigOptions(antialias=True,
                    background=palette.color(QPalette.Base),
                    foreground=palette.color(QPalette.Text))

window = MainWindow()
window.show()


def kick():
    if args.no_click:
        return
    print('clicking rescan...', flush=True)
    window.rescan_btn.click()   # -> refresh_all -> scan_ble -> ble.scan()


def report():
    combo = window.port_combo
    print('--- port dropdown ---', flush=True)
    for i in range(combo.count()):
        print(f'  [{i}] {combo.itemText(i)!r}  data={combo.itemData(i)!r}', flush=True)
    print('status:', window.statusBar().currentMessage(), flush=True)
    window.grab().save(args.shot)
    print('saved screenshot ->', args.shot, flush=True)
    app.quit()


QTimer.singleShot(300, kick)                 # let the window paint first
QTimer.singleShot(int(args.wait * 1000), report)
sys.exit(app.exec())
