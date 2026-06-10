"""Memory view: browse the meter's stored recordings, min/max and peak
sessions, and saved measurements; download, plot and export them as CSV."""

import csv
import math
import time

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox,
    QProgressBar, QPushButton, QSplitter, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .plot_utils import set_axis_unit
from .protocol import (
    OVERLOAD_VALUE, format_duration, format_reading, format_ts,
)

KIND_LABELS = {
    'recordings': 'Recordings',
    'minmax': 'Min/Max sessions',
    'peak': 'Peak sessions',
    'measurements': 'Saved measurements',
}

# label shown in the Clear menu -> argument for the (reverse engineered,
# category-wide) 'csd' clear-saved-data command
CLEAR_CATEGORIES = [
    ('All recordings', 'RECORDED'),
    ('All min/max sessions', 'MIN_MAX'),
    ('All peak sessions', 'PEAK'),
    ('All saved measurements', 'MEASUREMENT'),
    ('Everything in memory', 'ALL'),
]


def _avg_value(sample):
    """The meter stores the running sum in AVERAGE and the sample count in
    'duration'; the true average is sum / count (matches FlukeView)."""
    duration = sample['duration']
    avg = sample['readings']['AVERAGE']
    if not duration:
        return 0.0
    return round(avg['value'] / duration, max(0, avg['decimals']))


def _cell(text):
    item = QTableWidgetItem(str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


class MemoryView(QWidget):
    request_refresh = Signal()
    request_download = Signal(int, str)
    request_clear = Signal(str)     # csd category

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.inventory = None
        self.downloaded = {}            # recording index -> downloaded payload
        self.current_recording = None   # {'index':..., 'info':..., 'samples':[...]}
        self.current_session = None     # minmax/peak/measurement dict
        self.current_kind = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.refresh_btn = QPushButton('Refresh List')
        self.refresh_btn.setToolTip('Read the list of stored data from the meter (qsls/qrsi/qmmsi/qpsi/qsmr)')
        self.refresh_btn.clicked.connect(self.request_refresh)
        toolbar.addWidget(self.refresh_btn)

        self.download_btn = QPushButton('Download')
        self.download_btn.setToolTip('Download all samples of the selected recording from the meter')
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._download_selected)
        toolbar.addWidget(self.download_btn)

        self.export_btn = QPushButton('Export CSV…')
        self.export_btn.setToolTip('Save the displayed data to a CSV file')
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_csv)
        toolbar.addWidget(self.export_btn)

        self.clear_btn = QPushButton('Clear Meter Memory')
        self.clear_btn.setToolTip(
            'Delete stored data from the meter (csd command, reverse engineered).\n'
            'The meter only supports deleting whole categories, not single items.\n'
            'This cannot be undone — download anything you want to keep first.')
        clear_menu = QMenu(self.clear_btn)
        for label, category in CLEAR_CATEGORIES:
            action = clear_menu.addAction(label + '…')
            action.triggered.connect(
                lambda _=False, lab=label, cat=category: self._confirm_clear(lab, cat))
        self.clear_btn.setMenu(clear_menu)
        toolbar.addWidget(self.clear_btn)

        toolbar.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setFixedWidth(220)
        toolbar.addWidget(self.progress)

        self.cancel_btn = QPushButton('Cancel')
        self.cancel_btn.setVisible(False)
        toolbar.addWidget(self.cancel_btn)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Name', 'Start', 'End', 'Duration', 'Samples / Value'])
        self.tree.setRootIsDecorated(True)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.setToolTip('Data stored in the meter memory. Press Refresh List to load.')
        splitter.addWidget(self.tree)

        # --- detail stack ---
        self.detail = QStackedWidget()

        placeholder = QLabel('Select an item.\nRecordings must be downloaded to view their samples.')
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet('color: palette(mid);')
        self.detail.addWidget(placeholder)            # page 0

        # page 1: recording detail (plot + table)
        rec_page = QWidget()
        rec_layout = QVBoxLayout(rec_page)
        self.rec_header = QLabel('')
        rec_layout.addWidget(self.rec_header)
        axis = pg.DateAxisItem(orientation='bottom')
        self.rec_plot = pg.PlotWidget(axisItems={'bottom': axis})
        self.rec_plot.showGrid(x=True, y=True, alpha=0.25)
        self.rec_plot.addLegend()
        rec_layout.addWidget(self.rec_plot, stretch=2)
        self.rec_table = QTableWidget()
        self.rec_table.setColumnCount(8)
        self.rec_table.setHorizontalHeaderLabels(
            ['#', 'Start Time', 'Primary', 'Maximum', 'Average', 'Minimum', '#Samples', 'Type'])
        header = self.rec_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.rec_table.verticalHeader().setVisible(False)
        rec_layout.addWidget(self.rec_table, stretch=1)
        self.detail.addWidget(rec_page)               # page 1

        # page 2: min/max/peak/measurement detail
        sess_page = QWidget()
        sess_layout = QVBoxLayout(sess_page)
        self.sess_header = QLabel('')
        sess_layout.addWidget(self.sess_header)
        self.sess_table = QTableWidget()
        self.sess_table.setColumnCount(3)
        self.sess_table.setHorizontalHeaderLabels(['Reading', 'Value', 'Time'])
        self.sess_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.sess_table.verticalHeader().setVisible(False)
        sess_layout.addWidget(self.sess_table)
        self.detail.addWidget(sess_page)              # page 2

        splitter.addWidget(self.detail)
        splitter.setSizes([460, 540])
        layout.addWidget(splitter, stretch=1)

    # ------------------------------------------------------------------

    def set_connected(self, connected):
        self.refresh_btn.setEnabled(connected)
        self.clear_btn.setEnabled(connected)
        if not connected:
            self.download_btn.setEnabled(False)

    def _confirm_clear(self, label, category):
        answer = QMessageBox.warning(
            self, 'Clear meter memory?',
            f'Delete {label.lower()} from the meter?\n\n'
            'This deletes the data on the meter itself and cannot be undone.\n'
            'Download anything you want to keep first.',
            QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel)
        if answer == QMessageBox.Yes:
            self.request_clear.emit(category)

    def set_busy(self, busy):
        self.refresh_btn.setEnabled(not busy)
        self.download_btn.setEnabled(not busy and self._selected_recording() is not None)
        self.progress.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        if not busy:
            self.progress.setValue(0)

    def on_progress(self, done, total):
        self.progress.setMaximum(total)
        self.progress.setValue(done)

    # -- inventory tree -----------------------------------------------------

    def on_inventory(self, inv):
        self.inventory = inv
        # indexes may have shifted; cached downloads are no longer trustworthy
        self.downloaded = {}
        self.current_recording = None
        self.current_session = None
        self.detail.setCurrentIndex(0)
        self.export_btn.setEnabled(False)
        self.tree.clear()
        for kind, label in KIND_LABELS.items():
            top = QTreeWidgetItem([f'{label} ({len(inv[kind])})'])
            top.setFlags(top.flags() & ~Qt.ItemIsSelectable)
            self.tree.addTopLevelItem(top)
            for i, item in enumerate(inv[kind]):
                if kind == 'measurements':
                    primary = item['readings']['PRIMARY']
                    value_str, unit = format_reading(primary)
                    row = QTreeWidgetItem([
                        item['name'], format_ts(primary['ts']), '', '',
                        f'{value_str} {unit}'])
                elif kind == 'recordings':
                    row = QTreeWidgetItem([
                        item['name'], format_ts(item['start_ts']),
                        format_ts(item['end_ts']),
                        format_duration(item['start_ts'], item['end_ts']),
                        str(item['num_samples'])])
                else:
                    row = QTreeWidgetItem([
                        item['name'], format_ts(item['start_ts']),
                        format_ts(item['end_ts']),
                        format_duration(item['start_ts'], item['end_ts']), ''])
                row.setData(0, Qt.UserRole, (kind, i))
                top.addChild(row)
            top.setExpanded(True)
        for col in range(5):
            self.tree.resizeColumnToContents(col)

    def _selected(self):
        items = self.tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.UserRole)

    def _selected_recording(self):
        sel = self._selected()
        if sel and sel[0] == 'recordings':
            return sel
        return None

    def _selection_changed(self):
        sel = self._selected()
        if sel is None:
            self.download_btn.setEnabled(False)
            return
        kind, index = sel
        self.download_btn.setEnabled(kind == 'recordings')
        if kind == 'recordings':
            if index in self.downloaded:
                self._show_recording(self.downloaded[index])
            else:
                self.current_recording = None
                self.detail.setCurrentIndex(0)
                self.export_btn.setEnabled(False)
        else:
            self._show_session(kind, self.inventory[kind][index])

    def _download_selected(self):
        sel = self._selected_recording()
        if sel is None:
            return
        _, index = sel
        name = self.inventory['recordings'][index]['name']
        self.request_download.emit(index, name)

    # -- detail pages -------------------------------------------------------

    def on_recording(self, payload):
        self.downloaded[payload['index']] = payload
        self._show_recording(payload)

    def _show_recording(self, payload):
        self.current_recording = payload
        self.current_session = None
        self.current_kind = 'recordings'
        info, samples = payload['info'], payload['samples']

        self.rec_header.setText(
            f"<b>{info['name']}</b> — {info['prim_function'].replace('_', ' ')}, "
            f"{format_ts(info['start_ts'])} → {format_ts(info['end_ts'])}, "
            f"interval {info['sample_interval']:g} s, {info['num_samples']} samples")

        xs, prim, vmin, vmax = [], [], [], []
        self.rec_table.setRowCount(len(samples))
        for row, s in enumerate(samples):
            p = s['readings2']['PRIMARY']
            mx = s['readings']['MAXIMUM']
            mn = s['readings']['MINIMUM']
            avg_val = _avg_value(s)
            xs.append(time.mktime(s['start_ts']))
            prim.append(math.nan if abs(p['value']) >= OVERLOAD_VALUE else p['value'])
            vmax.append(math.nan if abs(mx['value']) >= OVERLOAD_VALUE else mx['value'])
            vmin.append(math.nan if abs(mn['value']) >= OVERLOAD_VALUE else mn['value'])
            rec_type = 'INTERVAL' if s['record_type'] == 'INTERVAL' else s['stable']
            for col, text in enumerate([
                    str(row + 1),
                    format_ts(s['start_ts']),
                    f"{p['value']:g} {p['unit']}",
                    f"{mx['value']:g} {mx['unit']}",
                    f"{avg_val:g} {s['readings']['AVERAGE']['unit']}",
                    f"{mn['value']:g} {mn['unit']}",
                    str(s['duration']), rec_type]):
                self.rec_table.setItem(row, col, _cell(text))

        self.rec_plot.clear()
        self.rec_plot.addLegend()
        unit = samples[0]['readings2']['PRIMARY']['unit'] if samples else info['unit']
        set_axis_unit(self.rec_plot, unit)
        self.rec_plot.plot(xs, vmax, pen=pg.mkPen('#e5534b', width=1), name='Max', connect='finite')
        self.rec_plot.plot(xs, prim, pen=pg.mkPen('#2f81f7', width=2), name='Primary', connect='finite')
        self.rec_plot.plot(xs, vmin, pen=pg.mkPen('#57ab5a', width=1), name='Min', connect='finite')

        self.detail.setCurrentIndex(1)
        self.export_btn.setEnabled(True)

    def _show_session(self, kind, session):
        self.current_session = session
        self.current_recording = None
        self.current_kind = kind

        if kind == 'measurements':
            order = ['PRIMARY']
            self.sess_header.setText(
                f"<b>{session['name']}</b> — {session['prim_function'].replace('_', ' ')}, saved measurement")
        else:
            order = ['PRIMARY', 'MAXIMUM', 'AVERAGE', 'MINIMUM']
            self.sess_header.setText(
                f"<b>{session['name']}</b> — {session['prim_function'].replace('_', ' ')}, "
                f"{format_ts(session['start_ts'])} → {format_ts(session['end_ts'])}")

        rows = [k for k in order if k in session['readings']]
        self.sess_table.setRowCount(len(rows))
        for row, key in enumerate(rows):
            r = session['readings'][key]
            value_str, unit = format_reading(r)
            self.sess_table.setItem(row, 0, _cell(key.title()))
            self.sess_table.setItem(row, 1, _cell(f'{value_str} {unit}'))
            self.sess_table.setItem(row, 2, _cell(format_ts(r['ts'])))
        self.detail.setCurrentIndex(2)
        self.export_btn.setEnabled(True)

    # -- CSV export ----------------------------------------------------------

    def export_csv(self):
        if self.current_recording is not None:
            name = self.current_recording['info']['name']
        elif self.current_session is not None:
            name = self.current_session['name']
        else:
            return
        safe = ''.join(c if c.isalnum() or c in '-_ ' else '_' for c in name).strip() or 'export'
        start_dir = self.settings.value('last_dir', '')
        path, _ = QFileDialog.getSaveFileName(
            self, 'Export CSV', f'{start_dir}/{safe}.csv' if start_dir else f'{safe}.csv',
            'CSV files (*.csv)')
        if not path:
            return
        self.settings.setValue('last_dir', path.rsplit('/', 1)[0])
        try:
            with open(path, 'w', newline='') as f:
                w = csv.writer(f)
                if self.current_recording is not None:
                    self._export_recording(w)
                else:
                    self._export_session(w)
        except OSError as err:
            QMessageBox.critical(self, 'Export failed', str(err))

    def _export_recording(self, w):
        info = self.current_recording['info']
        w.writerow(['name', info['name']])
        w.writerow(['function', info['prim_function']])
        w.writerow(['start', format_ts(info['start_ts'])])
        w.writerow(['end', format_ts(info['end_ts'])])
        w.writerow(['sample_interval_s', info['sample_interval']])
        w.writerow([])
        w.writerow(['start_time', 'end_time', 'primary', 'primary_unit',
                    'maximum', 'maximum_unit', 'average', 'average_unit',
                    'minimum', 'minimum_unit', 'samples', 'type'])
        for s in self.current_recording['samples']:
            p = s['readings2']['PRIMARY']
            mx = s['readings']['MAXIMUM']
            mn = s['readings']['MINIMUM']
            rec_type = 'INTERVAL' if s['record_type'] == 'INTERVAL' else s['stable']
            w.writerow([format_ts(s['start_ts']), format_ts(s['end_ts']),
                        p['value'], p['unit'],
                        mx['value'], mx['unit'],
                        _avg_value(s), s['readings']['AVERAGE']['unit'],
                        mn['value'], mn['unit'],
                        s['duration'], rec_type])

    def _export_session(self, w):
        session = self.current_session
        w.writerow(['name', session['name']])
        w.writerow(['function', session['prim_function']])
        if 'start_ts' in session:
            w.writerow(['start', format_ts(session['start_ts'])])
            w.writerow(['end', format_ts(session['end_ts'])])
        w.writerow([])
        w.writerow(['reading', 'value', 'unit', 'time'])
        for key, r in session['readings'].items():
            w.writerow([key, r['value'], r['unit'], format_ts(r['ts'])])
