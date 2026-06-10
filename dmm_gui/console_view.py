"""Console view: send any remote command to the meter and inspect the raw
response. Known commands are offered with tooltips from the catalog."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from .commands import COMMANDS, tooltip
from .protocol import DmmError

HEX_LIMIT = 4096


def hexdump(data, limit=HEX_LIMIT):
    lines = []
    shown = data[:limit]
    for off in range(0, len(shown), 16):
        chunk = shown[off:off + 16]
        hexpart = ' '.join(f'{b:02x}' for b in chunk)
        asciipart = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f'{off:06x}  {hexpart:<47}  {asciipart}')
    if len(data) > limit:
        lines.append(f'… {len(data) - limit} more bytes')
    return '\n'.join(lines)


class ConsoleView(QWidget):
    request_command = Signal(str)

    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.setToolTip('Known commands. Hover an entry for details;\n'
                              'placeholders like <index> must be replaced.')
        self.combo.addItem('— known commands —', None)
        for cmd in COMMANDS:
            tag = '' if cmd['documented'] else '  (unofficial)'
            self.combo.addItem(f"{cmd['template']} — {cmd['summary']}{tag}", cmd)
            self.combo.setItemData(self.combo.count() - 1, tooltip(cmd), Qt.ToolTipRole)
        self.combo.currentIndexChanged.connect(self._command_picked)
        row.addWidget(self.combo, stretch=1)
        layout.addLayout(row)

        self.detail_label = QLabel('Pick a command above or type one below. '
                                   'Commands are terminated with <CR> automatically.')
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet('color: palette(mid);')
        layout.addWidget(self.detail_label)

        send_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText('Command, e.g. ID or qmp clock')
        self.input.setToolTip('Raw command sent to the meter (a trailing <CR> is added)')
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self.input.setFont(mono)
        self.input.returnPressed.connect(self._send)
        send_row.addWidget(self.input, stretch=1)
        self.send_btn = QPushButton('Send')
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._send)
        send_row.addWidget(self.send_btn)
        clear_btn = QPushButton('Clear Log')
        clear_btn.clicked.connect(lambda: self.log.clear())
        send_row.addWidget(clear_btn)
        layout.addLayout(send_row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(mono)
        self.log.setToolTip('Command/response log. Binary responses are shown as a hex dump.')
        layout.addWidget(self.log, stretch=1)

    # ------------------------------------------------------------------

    def set_connected(self, connected):
        self.send_btn.setEnabled(connected)

    def _command_picked(self, index):
        cmd = self.combo.itemData(index)
        if cmd is None:
            return
        self.detail_label.setText(tooltip(cmd).replace('\n', ' ').replace('  ', ' '))
        self.input.setText(cmd['template'])
        self.input.setFocus()

    def _send(self):
        cmd = self.input.text().strip()
        if not cmd or not self.send_btn.isEnabled():
            return
        if '<' in cmd and '>' in cmd:
            self.log.appendPlainText(f'> {cmd}\n! Replace the <placeholders> with real values first.\n')
            return
        self.log.appendPlainText(f'> {cmd}')
        self.request_command.emit(cmd)

    def on_result(self, cmd, result):
        if isinstance(result, DmmError):
            self.log.appendPlainText(f'! {result}\n')
        elif isinstance(result, bytes):
            self.log.appendPlainText(f'binary response, {len(result)} bytes:')
            self.log.appendPlainText(hexdump(result) + '\n')
        elif isinstance(result, list):
            if not result:
                self.log.appendPlainText('OK (acknowledged, no data)\n')
            else:
                self.log.appendPlainText('\n'.join(result) + '\n')
        else:
            self.log.appendPlainText(f'{result}\n')
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
