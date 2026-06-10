"""End-to-end GUI test against the fake meter (offscreen).

Run with:  QT_QPA_PLATFORM=offscreen venv/bin/python tests/test_gui.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_meter import FakeSerial
from dmm_gui import protocol
protocol.serial.Serial = FakeSerial   # monkeypatch before app import

import tempfile

from PySide6.QtCore import QCoreApplication, QEventLoop, QSettings, QTimer
from PySide6.QtWidgets import QApplication

# Keep test QSettings out of the user's real macOS preferences: the
# QSettings(org, app) constructor always uses the native plist store, so
# point the app module at an ini file in a temp dir instead.
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                  tempfile.mkdtemp(prefix='dmm_gui_test_settings_'))

import dmm_gui.app
dmm_gui.app.QSettings = lambda org, app: QSettings(
    QSettings.Format.IniFormat, QSettings.Scope.UserScope, org, app)

from dmm_gui.app import MainWindow


def spin(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(predicate, timeout_ms=5000, what='condition'):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if predicate():
            return
        spin(50)
    raise AssertionError(f'Timed out waiting for {what}')


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()

    # connect to a fake port
    win.port_combo.clear()
    win.port_combo.addItem('/dev/cu.fake — Fake IR cable', '/dev/cu.fake')
    win.live_view.interval_spin.setValue(0.1)
    win.toggle_connect()
    wait_for(lambda: win.connected, what='connection')
    assert 'FLUKE 289' in win.conn_label.text()
    print('PASS connect:', win.conn_label.text())

    # live polling updates the readout and plot
    wait_for(lambda: win.live_view.value_label.text() != '—', what='live reading')
    wait_for(lambda: len(win.live_view.live_x) >= 3, what='3 plot points')
    assert '1.2345' in win.live_view.value_label.text()
    assert 'VDC' in win.live_view.value_label.text()
    assert win.live_view.plot.getAxis('left').labelUnits == 'V'  # SI prefixes on
    print('PASS live readout:', win.live_view.value_label.text(),
          '|', win.live_view.function_label.text())

    # setup view auto-populated on connect
    wait_for(lambda: win.setup_view.info_labels['Model'].text() == 'FLUKE 289',
             what='setup info')
    assert win.setup_view.string_edits['company'].text() == 'Acme'
    assert win.setup_view.name_edits[0].text() == 'SAVE1'
    print('PASS setup view populated')

    # recording in the live view
    win.live_view.toggle_recording()
    wait_for(lambda: len(win.live_view.record_rows) >= 5, what='5 recorded samples')
    win.live_view.toggle_recording()
    n = len(win.live_view.record_rows)
    assert n >= 5 and win.live_view.save_btn.isEnabled()
    print(f'PASS live recording: {n} samples captured')

    # memory inventory
    win.memory_view.request_refresh.emit()
    wait_for(lambda: win.memory_view.inventory is not None, what='inventory')
    inv = win.memory_view.inventory
    assert [len(inv[k]) for k in ('recordings', 'minmax', 'peak', 'measurements')] == [1, 1, 1, 1]
    assert win.memory_view.tree.topLevelItemCount() == 4
    print('PASS inventory tree')

    # download the recording
    win.memory_view.request_download.emit(0, 'Rec-1')
    wait_for(lambda: win.memory_view.current_recording is not None, what='download')
    payload = win.memory_view.current_recording
    assert payload['info']['name'] == 'Rec-1'
    assert len(payload['samples']) == 5
    assert win.memory_view.rec_table.rowCount() == 5
    assert win.memory_view.export_btn.isEnabled()
    print('PASS recording download + table')

    # min/max session detail straight from inventory
    top = win.memory_view.tree.topLevelItem(1)  # Min/Max group
    win.memory_view.tree.setCurrentItem(top.child(0))
    spin(50)
    assert win.memory_view.current_kind == 'minmax'
    assert win.memory_view.sess_table.rowCount() == 4
    print('PASS min/max detail')

    # switching back to the recording shows the cached download, no re-fetch
    downloads = []
    win.req.download_recording.connect(lambda *args: downloads.append(args))
    rec_top = win.memory_view.tree.topLevelItem(0)  # Recordings group
    win.memory_view.tree.setCurrentItem(rec_top.child(0))
    spin(50)
    assert win.memory_view.detail.currentIndex() == 1, 'cached recording not shown'
    assert win.memory_view.current_recording is not None
    assert downloads == [], 'cache miss: recording was re-downloaded'
    print('PASS recording cache on re-selection')

    # clear meter memory (csd) empties the recordings category
    win.memory_view.request_clear.emit('RECORDED')
    wait_for(lambda: win.memory_view.inventory is not None
             and len(win.memory_view.inventory['recordings']) == 0,
             what='csd RECORDED + auto refresh')
    assert len(win.memory_view.inventory['minmax']) == 1  # others untouched
    print('PASS clear memory (csd RECORDED)')

    # console raw command
    win.console_view.input.setText('ID')
    win.console_view._send()
    wait_for(lambda: 'FLUKE 289' in win.console_view.log.toPlainText(), what='console reply')
    win.console_view.input.setText('qddb')
    win.console_view._send()
    wait_for(lambda: 'binary response' in win.console_view.log.toPlainText(), what='hex dump')
    print('PASS console (ascii + binary hex dump)')

    # owner info write round-trip
    win.setup_view.string_edits['operator'].setText('GUI Test')
    win.setup_view._apply_strings()
    spin(300)
    win.setup_view.request_refresh.emit()
    wait_for(lambda: win.setup_view.string_edits['operator'].text() == 'GUI Test',
             what='operator round-trip')
    print('PASS owner info round-trip')

    # disconnect
    win.toggle_connect()
    wait_for(lambda: not win.connected, what='disconnect')
    assert not win.live_view.record_btn.isEnabled()
    print('PASS disconnect')

    win.close()
    print('\nALL GUI TESTS PASSED')


if __name__ == '__main__':
    main()
