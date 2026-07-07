"""Live view: big readout, rolling plot, and session recording to CSV."""

import csv
import math
import time
from collections import deque

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .plot_utils import ReadingAxis, set_axis_unit
from .protocol import OVERLOAD_VALUE, format_reading

LIVE_BUFFER = 3600  # points kept on the rolling plot while not recording


class LiveView(QWidget):
    poll_interval_changed = Signal(float)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.recording = False
        self.record_rows = []          # (epoch, value, unit, function, state)
        self.record_started = None
        self.live_x = deque(maxlen=LIVE_BUFFER)
        self.live_y = deque(maxlen=LIVE_BUFFER)
        self.last_unit = ''
        self.last_resolution = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- readout ---
        self.value_label = QLabel('—')
        font = QFont()
        font.setPointSize(64)
        font.setBold(True)
        self.value_label.setFont(font)
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setToolTip('Live primary reading from the meter')

        self.secondary_label = QLabel('')
        sec_font = QFont()
        sec_font.setPointSize(24)
        self.secondary_label.setFont(sec_font)
        self.secondary_label.setAlignment(Qt.AlignCenter)
        self.secondary_label.setStyleSheet('color: palette(mid);')
        self.secondary_label.setToolTip("The meter's secondary display (BLE connection)")
        self.secondary_label.setVisible(False)

        self.function_label = QLabel('Not connected')
        self.function_label.setAlignment(Qt.AlignCenter)
        self.function_label.setStyleSheet('color: palette(mid); font-size: 14px;')

        layout.addWidget(self.value_label)
        layout.addWidget(self.secondary_label)
        layout.addWidget(self.function_label)

        # --- plot ---
        axis = pg.DateAxisItem(orientation='bottom')
        self.plot = pg.PlotWidget(axisItems={
            'bottom': axis, 'left': ReadingAxis(orientation='left')})
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setToolTip('Live readings. While recording, shows the recorded session.')
        self.curve = self.plot.plot(pen=pg.mkPen('#2f81f7', width=2))
        layout.addWidget(self.plot, stretch=1)

        # --- controls ---
        controls = QHBoxLayout()

        controls.addWidget(QLabel('Sample every'))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.0, 3600.0)
        self.interval_spin.setDecimals(1)
        self.interval_spin.setSuffix(' s')
        # 0 (below the 0.1 minimum step) means "poll as fast as the meter
        # answers" rather than on a fixed interval.
        self.interval_spin.setSpecialValueText('as fast as possible')
        self.interval_spin.setValue(float(self.settings.value('poll_interval', 0.0)))
        self.interval_spin.setMinimumWidth(
            self.interval_spin.fontMetrics().horizontalAdvance('as fast as possible') + 40)
        self.interval_spin.setToolTip('How often the app polls the meter for a live reading.\n'
                                      'Also the sampling rate while recording. Set to\n'
                                      '"as fast as possible" (below 0.1 s) to poll the meter\n'
                                      'back-to-back with no fixed interval.')
        self.interval_spin.valueChanged.connect(self._interval_changed)
        controls.addWidget(self.interval_spin)

        controls.addSpacing(16)
        controls.addWidget(QLabel('Stop after'))
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(0, 100000)
        self.duration_spin.setSpecialValueText('manual stop')
        self.duration_spin.setSuffix(' min')
        self.duration_spin.setMinimumWidth(
            self.duration_spin.fontMetrics().horizontalAdvance('manual stop') + 40)
        self.duration_spin.setValue(int(self.settings.value('record_duration', 0)))
        self.duration_spin.setToolTip('Optional recording duration. "manual stop" records until\n'
                                      'you press Stop.')
        controls.addWidget(self.duration_spin)

        controls.addStretch(1)

        self.status_label = QLabel('')
        self.status_label.setStyleSheet('color: palette(mid);')
        controls.addWidget(self.status_label)

        controls.addSpacing(12)
        self.record_btn = QPushButton('● Record')
        self.record_btn.setToolTip('Start logging live readings to a session that can be saved as CSV')
        self.record_btn.setEnabled(False)
        self.record_btn.clicked.connect(self.toggle_recording)
        controls.addWidget(self.record_btn)

        self.clear_btn = QPushButton('Clear')
        self.clear_btn.setToolTip('Clear the plot and the recorded session')
        self.clear_btn.clicked.connect(self.clear)
        controls.addWidget(self.clear_btn)

        self.save_btn = QPushButton('Save CSV…')
        self.save_btn.setToolTip('Save the recorded session to a CSV file')
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_csv)
        controls.addWidget(self.save_btn)

        layout.addLayout(controls)

    # ------------------------------------------------------------------

    def _interval_changed(self, value):
        self.settings.setValue('poll_interval', value)
        self.poll_interval_changed.emit(value)

    def poll_interval(self):
        return self.interval_spin.value()

    def set_connected(self, connected):
        self.record_btn.setEnabled(connected)
        if connected:
            self._clear_plot()
        else:
            if self.recording:
                self.toggle_recording()
            self.value_label.setText('—')
            self.secondary_label.setVisible(False)
            self.function_label.setText('Not connected')

    def _clear_plot(self):
        self.live_x.clear()
        self.live_y.clear()
        self.last_unit = ''
        self.last_resolution = None
        self.curve.setData([], [])

    # -- incoming data ---------------------------------------------------

    def on_live_reading(self, host_ts, data):
        reading = data['readings'].get('LIVE') or data['readings'].get('PRIMARY')
        if reading is None:
            return
        value_str, unit = format_reading(reading)
        self.value_label.setText(f'{value_str} {unit}'.strip())
        self.secondary_label.setVisible(False)

        parts = [data['prim_function'].replace('_', ' ')]
        if data.get('sec_function') and data['sec_function'] != 'NONE':
            parts.append(data['sec_function'].replace('_', ' '))
        mode = data.get('mode', '')
        if mode and mode != 'NONE':
            parts.append(mode.replace('_', ' '))
        rng = f"{data.get('auto_range', '')} range".lower()
        self.function_label.setText('   •   '.join(parts + [rng]))

        value = reading['value']
        plot_value = math.nan if abs(value) >= OVERLOAD_VALUE else value
        # Least-significant digit of the meter's display, in base SI units.
        resolution = 10 ** (reading.get('unit_multiplier', 0)
                            - max(0, reading.get('decimals', 0)))
        self._ingest(host_ts, value, plot_value, reading['unit'],
                     data['prim_function'], reading['state'], resolution)

    def on_ble_reading(self, host_ts, reading):
        """Decoded ir3000FC record (fluke289_bt_decode.Reading)."""
        if not reading.present:
            return
        self.value_label.setText(reading.display)
        if reading.secondary is not None:
            self.secondary_label.setText(reading.secondary.display)
            self.secondary_label.setVisible(True)
        else:
            self.secondary_label.setVisible(False)

        function = (reading.base_unit
                    + (f' {reading.ac_dc}' if reading.ac_dc else ''))
        parts = [function] + reading.status_flags + ['BLE']
        self.function_label.setText('   •   '.join(parts))

        if reading.overload or reading.value_si is None:
            value, plot_value, state = OVERLOAD_VALUE, math.nan, 'OL'
        else:
            value = plot_value = reading.value_si
            state = 'NORMAL'
        # Least-significant digit of the meter's display, in base SI units.
        resolution = reading.prefix_factor * 10 ** -reading.decimals
        self._ingest(host_ts, value, plot_value, reading.base_unit,
                     function, state, resolution)

    def _ingest(self, host_ts, value, plot_value, unit, function, state,
                resolution=None):
        if unit != self.last_unit:
            # new quantity: a mixed-unit trace is meaningless
            self.live_x.clear()
            self.live_y.clear()
        self.last_unit = unit
        self.last_resolution = resolution
        self.live_x.append(host_ts)
        self.live_y.append(plot_value)
        if self.recording:
            self.record_rows.append((host_ts, value, unit, function, state))
            elapsed = host_ts - self.record_started
            self.status_label.setText(
                f'Recording: {len(self.record_rows)} samples, {int(elapsed)} s')
            limit = self.duration_spin.value()
            if limit and elapsed >= limit * 60:
                self.toggle_recording()
        self._redraw()

    def _redraw(self):
        self.curve.setData(list(self.live_x), list(self.live_y), connect='finite')
        set_axis_unit(self.plot, self.last_unit, self.last_resolution)

    # -- recording ---------------------------------------------------------

    def toggle_recording(self):
        if not self.recording:
            self.recording = True
            self.record_rows = []
            self.record_started = time.time()
            self.live_x = deque()   # unlimited while recording
            self.live_y = deque()
            self.record_btn.setText('■ Stop')
            self.settings.setValue('record_duration', self.duration_spin.value())
            self.status_label.setText('Recording: 0 samples')
        else:
            self.recording = False
            self.record_btn.setText('● Record')
            self.status_label.setText(
                f'Recorded {len(self.record_rows)} samples — use Save CSV…')
            self.save_btn.setEnabled(bool(self.record_rows))

    def clear(self):
        self.live_x = deque(maxlen=None if self.recording else LIVE_BUFFER)
        self.live_y = deque(maxlen=None if self.recording else LIVE_BUFFER)
        self.record_rows = []
        self.curve.setData([], [])
        if not self.recording:
            self.status_label.setText('')
            self.save_btn.setEnabled(False)

    def save_csv(self):
        if not self.record_rows:
            return
        default = time.strftime('live_recording_%Y%m%d_%H%M%S.csv',
                                time.localtime(self.record_started))
        start_dir = self.settings.value('last_dir', '')
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save recorded session', f'{start_dir}/{default}' if start_dir else default,
            'CSV files (*.csv)')
        if not path:
            return
        self.settings.setValue('last_dir', path.rsplit('/', 1)[0])
        try:
            with open(path, 'w', newline='') as f:
                w = csv.writer(f)
                w.writerow(['timestamp', 'epoch', 'value', 'unit', 'function', 'state'])
                for ts, value, unit, function, state in self.record_rows:
                    w.writerow([
                        time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
                        + f'.{int((ts % 1) * 1000):03d}',
                        f'{ts:.3f}', value, unit, function, state])
        except OSError as err:
            QMessageBox.critical(self, 'Save failed', str(err))
            return
        self.status_label.setText(f'Saved {len(self.record_rows)} samples to {path}')
