"""Meter setup view: identity/config, owner strings, clock sync, save-name
slots and reset commands."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

INFO_KEYS = [
    'Model', 'Software Version', 'Serial Number', 'Current meter time',
]
MISC_KEYS = [
    'Autohold Threshold', 'Language', 'Date Format', 'Time Format', 'Digits',
    'Beeper', 'Temperature Offset Shift', 'Numeric Format',
    'Auto Backlight Timeout', 'Auto Power Off',
]
STRING_PROPS = {'Company': 'company', 'Contact': 'contact',
                'Operator': 'operator', 'Site': 'site'}


class SetupView(QWidget):
    request_refresh = Signal()
    request_apply_strings = Signal(dict)
    request_sync_clock = Signal()
    request_set_name = Signal(int, str)
    request_simple = Signal(str)

    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)
        layout = QVBoxLayout(body)

        top_bar = QHBoxLayout()
        self.refresh_btn = QPushButton('Read From Meter')
        self.refresh_btn.setToolTip('Read identity, configuration and save names from the meter')
        self.refresh_btn.clicked.connect(self.request_refresh)
        top_bar.addWidget(self.refresh_btn)
        top_bar.addStretch(1)
        layout.addLayout(top_bar)

        # --- identity ---
        info_box = QGroupBox('Meter')
        info_form = QFormLayout(info_box)
        self.info_labels = {}
        for key in INFO_KEYS:
            label = QLabel('—')
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.info_labels[key] = label
            info_form.addRow(key + ':', label)
        self.sync_btn = QPushButton('Sync Clock to This Mac')
        self.sync_btn.setToolTip('Set the meter date and time to this computer’s current time\n(mp clock)')
        self.sync_btn.clicked.connect(self.request_sync_clock)
        info_form.addRow('', self.sync_btn)
        layout.addWidget(info_box)

        # --- owner strings ---
        strings_box = QGroupBox('Owner Info')
        strings_form = QFormLayout(strings_box)
        self.string_edits = {}
        for label, prop in STRING_PROPS.items():
            edit = QLineEdit()
            edit.setToolTip(f'Stored in the meter as the "{prop}" property (mpq {prop})')
            self.string_edits[prop] = edit
            strings_form.addRow(label + ':', edit)
        apply_btn = QPushButton('Apply Owner Info')
        apply_btn.setToolTip('Write company, contact, operator and site to the meter')
        apply_btn.clicked.connect(self._apply_strings)
        strings_form.addRow('', apply_btn)
        layout.addWidget(strings_box)

        # --- save name slots ---
        names_box = QGroupBox('Save Names (offered on the meter when saving data)')
        names_grid = QGridLayout(names_box)
        self.name_edits = []
        for i in range(8):
            names_grid.addWidget(QLabel(f'{i + 1}:'), i % 4, (i // 4) * 3)
            edit = QLineEdit()
            edit.setToolTip(f'Save-name slot {i + 1} (savname {i})')
            btn = QPushButton('Set')
            btn.setToolTip(f'Write slot {i + 1} to the meter')
            btn.clicked.connect(lambda _=False, slot=i: self._set_name(slot))
            names_grid.addWidget(edit, i % 4, (i // 4) * 3 + 1)
            names_grid.addWidget(btn, i % 4, (i // 4) * 3 + 2)
            self.name_edits.append(edit)
        layout.addWidget(names_box)

        # --- misc config (read only) ---
        misc_box = QGroupBox('Meter Configuration (read-only, set on the meter itself)')
        misc_form = QFormLayout(misc_box)
        self.misc_labels = {}
        for key in MISC_KEYS:
            label = QLabel('—')
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.misc_labels[key] = label
            misc_form.addRow(key + ':', label)
        layout.addWidget(misc_box)

        # --- resets ---
        reset_box = QGroupBox('Reset Commands')
        reset_row = QHBoxLayout(reset_box)
        for cmd, label, tip, confirm in [
            ('DS', 'Default Setup (DS)',
             'Reset Hz trigger edge, pulse width/duty cycle polarity and\n'
             'continuity beeper settings to defaults.', False),
            ('RMP', 'Reset Properties (RMP)',
             'Reset meter properties (owner info, save names, ...) to factory\n'
             'defaults. Same as Reset Setup on the meter.', True),
            ('RI', 'Reset Instrument (RI)',
             'Reset ALL instrument settings to factory settings (except\n'
             'calibration). Same as Reset Meter on the meter.', True),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _=False, c=cmd, t=tip, conf=confirm: self._reset(c, t, conf))
            reset_row.addWidget(btn)
        layout.addWidget(reset_box)

        layout.addStretch(1)

    # ------------------------------------------------------------------

    def set_connected(self, connected):
        self.setEnabled(connected)

    def on_setup(self, payload):
        cfg, names = payload['config'], payload['names']
        for key in INFO_KEYS:
            self.info_labels[key].setText(cfg.get(key, '—'))
        for key in MISC_KEYS:
            self.misc_labels[key].setText(cfg.get(key, '—'))
        for label, prop in STRING_PROPS.items():
            self.string_edits[prop].setText(cfg.get(label, ''))
        for edit, name in zip(self.name_edits, names):
            edit.setText(name)

    def _apply_strings(self):
        values = {prop: edit.text() for prop, edit in self.string_edits.items()}
        self.request_apply_strings.emit(values)

    def _set_name(self, slot):
        self.request_set_name.emit(slot, self.name_edits[slot].text())

    def _reset(self, cmd, tip, confirm):
        if confirm:
            answer = QMessageBox.question(
                self, f'Send {cmd}?',
                f'This will send {cmd} to the meter:\n\n{tip}\n\nContinue?')
            if answer != QMessageBox.Yes:
                return
        self.request_simple.emit(cmd)
