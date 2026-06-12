"""DMM Utility main window and application entry point."""

import sys

import pyqtgraph as pg
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QPushButton, QStackedWidget, QStatusBar,
    QVBoxLayout, QWidget,
)
from serial.tools import list_ports

from PySide6.QtCore import QThread

from .ble_worker import BleWorker, DEVICE_NAME as BLE_DEVICE_NAME
from .console_view import ConsoleView
from .live_view import LiveView
from .memory_view import MemoryView
from .screen_view import ScreenView
from .setup_view import SetupView
from .worker import DmmWorker, Requests

DEFAULT_TIMEOUT = 0.09


def known_ports():
    """Serial ports, FTDI/USB-serial first; on macOS prefer /dev/cu.* over
    /dev/tty.* (cu devices don't wait for DCD)."""
    ports = []
    for p in list_ports.comports():
        if sys.platform == 'darwin' and p.device.startswith('/dev/tty.'):
            continue
        score = 0
        if p.vid == 0x0403:                      # FTDI (the Fluke IR cable)
            score -= 2
        if 'usbserial' in p.device.lower():
            score -= 1
        ports.append((score, p))
    ports.sort(key=lambda x: (x[0], x[1].device))
    return [p for _score, p in ports]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('DMM Utility — Fluke 287/289')
        self.settings = QSettings('dmm_util_gui', 'DMM Utility')
        self.connected = False
        self.conn_type = None              # 'serial' | 'ble' while connected
        self._ble_autoconnect = None       # 'ble:<addr>' awaiting scan result

        self._build_worker()
        self._build_ui()
        self._wire()

        geometry = self.settings.value('geometry')
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1100, 720)

        self.refresh_ports()
        if self.autoconnect_check.isChecked():
            saved = str(self.settings.value('port', '') or '')
            if saved.startswith('ble:'):
                self._ble_autoconnect = saved
                self.scan_ble()
            elif self.port_combo.currentData():
                QTimer.singleShot(100, self.toggle_connect)

    # ------------------------------------------------------------------

    def _build_worker(self):
        self.req = Requests()
        self.worker = DmmWorker()
        self.worker_thread = QThread(self)
        self.worker.moveToThread(self.worker_thread)
        self.worker.bind(self.req)
        # destroy the worker (and its poll timer) on its own thread at exit
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.start()

        # BLE runs on its own asyncio thread inside BleWorker
        self.ble = BleWorker()

    def _build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- connection bar ---
        bar = QWidget()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 8)

        bar_layout.addWidget(QLabel('Port:'))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(280)
        self.port_combo.setToolTip('Serial port of the USB IR cable, or a BLE adapter.\n'
                                   'The Fluke cable uses an FTDI chip and shows up as\n'
                                   '/dev/cu.usbserial-XXXX; the ir3000FC BLE adapter\n'
                                   f"advertises as '{BLE_DEVICE_NAME}'.")
        bar_layout.addWidget(self.port_combo)

        self.rescan_btn = QPushButton('⟳')
        self.rescan_btn.setFixedWidth(32)
        self.rescan_btn.setToolTip('Rescan serial ports and scan for BLE adapters (~5 s)')
        self.rescan_btn.clicked.connect(self.refresh_all)
        bar_layout.addWidget(self.rescan_btn)

        self.connect_btn = QPushButton('Connect')
        self.connect_btn.setToolTip('Open the selected port and identify the meter.\n'
                                    'Make sure the meter is on and the IR cable is attached.')
        self.connect_btn.clicked.connect(self.toggle_connect)
        bar_layout.addWidget(self.connect_btn)

        self.autoconnect_check = QCheckBox('Connect at launch')
        self.autoconnect_check.setToolTip('Automatically connect to the last used port when the app starts')
        self.autoconnect_check.setChecked(self.settings.value('autoconnect', 'false') == 'true')
        self.autoconnect_check.toggled.connect(
            lambda on: self.settings.setValue('autoconnect', 'true' if on else 'false'))
        bar_layout.addWidget(self.autoconnect_check)

        bar_layout.addStretch(1)
        self.conn_label = QLabel('○ Not connected')
        self.conn_label.setStyleSheet('color: palette(mid);')
        bar_layout.addWidget(self.conn_label)
        outer.addWidget(bar)

        # --- sidebar + pages ---
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(170)
        self.sidebar.setStyleSheet(
            'QListWidget { border: none; padding-top: 8px; font-size: 14px; }'
            'QListWidget::item { padding: 10px 14px; border-radius: 6px; margin: 2px 8px;'
            ' color: palette(text); }'
            'QListWidget::item:selected { background: palette(highlight);'
            ' color: palette(highlighted-text); }'
            'QListWidget::item:disabled { color: palette(mid); background: transparent; }')
        for label, tip in [
            ('📈  Live', 'Live readings from the meter, with recording to CSV'),
            ('🖥  Screen', 'Live capture of the meter LCD screen'),
            ('💾  Memory', 'Recordings and measurements stored in the meter'),
            ('🔧  Meter', 'Meter identity, clock, owner info and save names'),
            ('⌨️  Console', 'Send raw protocol commands'),
        ]:
            item = QListWidgetItem(label)
            item.setToolTip(tip)
            self.sidebar.addItem(item)

        self.pages = QStackedWidget()
        self.live_view = LiveView(self.settings)
        self.screen_view = ScreenView(self.settings)
        self.memory_view = MemoryView(self.settings)
        self.setup_view = SetupView()
        self.console_view = ConsoleView()
        for page in (self.live_view, self.screen_view, self.memory_view,
                     self.setup_view, self.console_view):
            self.pages.addWidget(page)

        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        body.addWidget(self.sidebar)
        body.addWidget(self.pages, stretch=1)
        outer.addLayout(body, stretch=1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self._set_views_connected(False)

    def _wire(self):
        w, req = self.worker, self.req

        w.connected.connect(self.on_connected)
        w.connect_failed.connect(self.on_connect_failed)
        w.disconnected.connect(self.on_disconnected)
        w.op_done.connect(self.on_op_done)
        w.op_failed.connect(self.on_op_failed)
        w.live_error.connect(self.on_live_error)

        # BLE (signals arrive from the asyncio thread; bound methods only)
        b = self.ble
        b.scan_finished.connect(self.on_ble_scan_finished)
        b.scan_failed.connect(self.on_ble_scan_failed)
        b.connected.connect(self.on_ble_connected)
        b.connect_failed.connect(self.on_connect_failed)
        b.disconnected.connect(self.on_ble_disconnected)
        b.ble_reading.connect(self.live_view.on_ble_reading)

        # Live
        w.live_reading.connect(self.live_view.on_live_reading)
        self.live_view.poll_interval_changed.connect(
            lambda s: self.req.set_polling.emit(self.connected, s))

        # Screen
        self.screen_view.request_screen_polling.connect(self.req.set_screen_polling)
        w.screen_frame.connect(self.screen_view.on_frame)
        w.screen_error.connect(self.screen_view.on_error)

        # Memory
        self.memory_view.request_refresh.connect(self.on_refresh_inventory)
        self.memory_view.request_download.connect(self.on_download)
        self.memory_view.cancel_btn.clicked.connect(self.cancel_download)
        self.memory_view.request_clear.connect(self.req.clear_memory)
        w.memory_cleared.connect(self.on_memory_cleared)
        w.inventory_ready.connect(self.on_inventory)
        w.download_progress.connect(self.memory_view.on_progress)
        w.recording_ready.connect(self.on_recording)

        # Setup
        self.setup_view.request_refresh.connect(self.req.fetch_setup)
        self.setup_view.request_apply_strings.connect(self.req.apply_strings)
        self.setup_view.request_sync_clock.connect(self.req.sync_clock)
        self.setup_view.request_set_name.connect(self.req.set_name)
        self.setup_view.request_simple.connect(self.req.simple_command)
        w.setup_ready.connect(self.setup_view.on_setup)

        # Console
        self.console_view.request_command.connect(self.req.raw_command)
        w.raw_result.connect(self.console_view.on_result)

    # -- connection ------------------------------------------------------

    def refresh_ports(self):
        current = self.port_combo.currentData() or self.settings.value('port', '')
        self.port_combo.clear()
        for p in known_ports():
            desc = f' — {p.description}' if p.description and p.description != 'n/a' else ''
            self.port_combo.addItem(f'{p.device}{desc}', p.device)
        if current:
            idx = self.port_combo.findData(current)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
        if self.port_combo.count() == 0:
            self.port_combo.addItem('No ports found', None)

    def refresh_all(self):
        self.refresh_ports()
        self.scan_ble()

    def scan_ble(self):
        self.rescan_btn.setEnabled(False)
        self.statusBar().showMessage(f"Scanning for '{BLE_DEVICE_NAME}' BLE adapters…")
        self.ble.scan()

    def on_ble_scan_finished(self, results):
        self.rescan_btn.setEnabled(True)
        for i in range(self.port_combo.count() - 1, -1, -1):
            data = self.port_combo.itemData(i)
            if isinstance(data, str) and data.startswith('ble:'):
                self.port_combo.removeItem(i)
        if results and self.port_combo.count() == 1 and self.port_combo.itemData(0) is None:
            self.port_combo.clear()    # replace the "No ports found" stub
        for name, address in results:
            self.port_combo.addItem(f'BLE: {name} ({address[:8]}…)', f'ble:{address}')
        self.statusBar().showMessage(
            f"BLE scan: {len(results)} '{BLE_DEVICE_NAME}' adapter(s) found", 10000)

        saved = str(self.settings.value('port', '') or '')
        if saved.startswith('ble:') and not self.connected:
            idx = self.port_combo.findData(saved)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
        if self._ble_autoconnect:
            pending, self._ble_autoconnect = self._ble_autoconnect, None
            if self.port_combo.findData(pending) >= 0 and not self.connected:
                self.toggle_connect()

    def on_ble_scan_failed(self, err):
        self.rescan_btn.setEnabled(True)
        self._ble_autoconnect = None
        self.statusBar().showMessage(f'BLE scan failed: {err}', 8000)

    def toggle_connect(self):
        if self.connected:
            if self.conn_type == 'ble':
                self.ble.disconnect_device()
            else:
                self.req.set_polling.emit(False, self.live_view.poll_interval())
                self.req.close_port.emit()
            return
        port = self.port_combo.currentData()
        if not port:
            self.statusBar().showMessage('No port selected', 5000)
            return
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText('Connecting…')
        if port.startswith('ble:'):
            self.ble.connect_device(port[len('ble:'):])
        else:
            self.req.open_port.emit(port, DEFAULT_TIMEOUT)

    def on_connected(self, info):
        self.connected = True
        self.conn_type = 'serial'
        self.settings.setValue('port', self.port_combo.currentData())
        self.connect_btn.setText('Disconnect')
        self.connect_btn.setEnabled(True)
        self.conn_label.setText(
            f"● {info['model_number']}  S/N {info['serial_number']}  ({info['software_version']})")
        self.conn_label.setStyleSheet('color: #57ab5a; font-weight: bold;')
        self._set_views_connected(True)
        self.req.set_polling.emit(True, self.live_view.poll_interval())
        self.req.fetch_setup.emit()
        self.statusBar().showMessage(f"Connected to {info['model_number']}", 5000)

    def on_connect_failed(self, err):
        self.connect_btn.setText('Connect')
        self.connect_btn.setEnabled(True)
        self.statusBar().showMessage(f'Connection failed: {err}', 8000)

    def on_disconnected(self):
        self.connected = False
        self.conn_type = None
        self.connect_btn.setText('Connect')
        self.connect_btn.setEnabled(True)
        self.conn_label.setText('○ Not connected')
        self.conn_label.setStyleSheet('color: palette(mid);')
        self._set_views_connected(False)
        self.memory_view.set_busy(False)

    def on_ble_connected(self, info):
        self.connected = True
        self.conn_type = 'ble'
        self.settings.setValue('port', self.port_combo.currentData())
        self.connect_btn.setText('Disconnect')
        self.connect_btn.setEnabled(True)
        self.conn_label.setText(f"● {info['name']}  (BLE — live readings only)")
        self.conn_label.setStyleSheet('color: #57ab5a; font-weight: bold;')
        # the BLE adapter only streams the live record: Live is the one
        # usable page, so park the UI there and grey out the rest
        self._set_views_connected(False)
        self.live_view.set_connected(True)
        self.sidebar.setCurrentRow(0)
        self._set_other_pages_enabled(False)
        self.statusBar().showMessage(f"Connected to {info['name']} over BLE", 5000)

    def on_ble_disconnected(self):
        self.connected = False
        self.conn_type = None
        self.connect_btn.setText('Connect')
        self.connect_btn.setEnabled(True)
        self.conn_label.setText('○ Not connected')
        self.conn_label.setStyleSheet('color: palette(mid);')
        self._set_views_connected(False)
        self._set_other_pages_enabled(True)

    def _set_other_pages_enabled(self, enabled):
        for row in range(1, self.sidebar.count()):
            item = self.sidebar.item(row)
            if enabled:
                item.setFlags(item.flags() | Qt.ItemIsEnabled)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)

    def _set_views_connected(self, connected):
        self.live_view.set_connected(connected)
        self.screen_view.set_connected(connected)
        self.memory_view.set_connected(connected)
        self.setup_view.set_connected(connected)
        self.console_view.set_connected(connected)

    # -- memory operations --------------------------------------------------

    def on_refresh_inventory(self):
        self.statusBar().showMessage('Reading memory list from meter…')
        self.memory_view.refresh_btn.setEnabled(False)
        self.req.refresh_inventory.emit()

    def on_inventory(self, inv):
        self.memory_view.refresh_btn.setEnabled(True)
        self.memory_view.on_inventory(inv)
        total = sum(len(v) for v in inv.values())
        self.statusBar().showMessage(f'Memory list loaded: {total} items', 5000)

    def on_download(self, index, name):
        self.statusBar().showMessage(f'Downloading recording "{name}"…')
        self.memory_view.set_busy(True)
        self.req.download_recording.emit(index, name)

    def on_recording(self, payload):
        self.memory_view.set_busy(False)
        self.memory_view.on_recording(payload)
        self.statusBar().showMessage(
            f"Downloaded \"{payload['info']['name']}\" "
            f"({len(payload['samples'])} samples)", 5000)

    def cancel_download(self):
        self.worker.cancel_requested = True

    def on_memory_cleared(self, category):
        self.statusBar().showMessage(f'Meter memory cleared ({category})', 6000)
        self.on_refresh_inventory()

    def on_op_done(self, msg):
        self.statusBar().showMessage(msg, 6000)

    def on_live_error(self, msg):
        self.statusBar().showMessage(f'Live reading: {msg}', 3000)

    def on_op_failed(self, msg):
        self.memory_view.set_busy(False)
        self.memory_view.refresh_btn.setEnabled(self.connected)
        self.statusBar().showMessage(msg, 10000)

    # -- lifecycle ---------------------------------------------------------

    def closeEvent(self, event):
        self.settings.setValue('geometry', self.saveGeometry())
        self.worker.cancel_requested = True
        self.ble.shutdown()
        self.req.close_port.emit()
        self.worker_thread.quit()
        self.worker_thread.wait(3000)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('DMM Utility')
    app.setOrganizationName('dmm_util_gui')

    palette = app.palette()
    pg.setConfigOptions(
        antialias=True,
        background=palette.color(QPalette.Base),
        foreground=palette.color(QPalette.Text),
    )

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
