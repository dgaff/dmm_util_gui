"""Screen view: live capture of the meter's LCD, with pause and export.

Capture protocol (qlcdbm) adapted from fluke-live.py, attributed to vsilves
on eevblog: https://www.eevblog.com/forum/reviews/going-further-with-the-fluke-289/25/
The view asks the worker to poll the screen only while it is visible,
connected and not paused, so other tabs aren't slowed by the capture.
"""

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

FPS_WINDOW = 5.0   # seconds of frame timestamps kept for the fps readout


class ScreenView(QWidget):
    request_screen_polling = Signal(bool)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.connected = False
        self.playing = True
        self.image = None              # last frame as QImage
        self.frame_count = 0
        self._frame_times = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.screen_label = QLabel('Not connected')
        self.screen_label.setAlignment(Qt.AlignCenter)
        self.screen_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.screen_label.setMinimumSize(320, 240)
        self.screen_label.setToolTip('Live capture of the meter LCD')
        layout.addWidget(self.screen_label, stretch=1)

        controls = QHBoxLayout()

        self.play_btn = QPushButton('⏸ Pause')
        self.play_btn.setToolTip('Pause or resume the live screen capture')
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self.toggle_play)
        controls.addWidget(self.play_btn)

        self.export_btn = QPushButton('Export Image…')
        self.export_btn.setToolTip('Save the current screen image as a PNG file')
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_image)
        controls.addWidget(self.export_btn)

        controls.addStretch(1)
        self.status_label = QLabel('')
        self.status_label.setStyleSheet('color: palette(mid);')
        controls.addWidget(self.status_label)

        layout.addLayout(controls)

    # ------------------------------------------------------------------

    def set_connected(self, connected):
        self.connected = connected
        self.play_btn.setEnabled(connected)
        if connected:
            if self.playing:
                self.status_label.setText('Waiting for screen…')
        else:
            self._frame_times = []
            self.status_label.setText('Not connected')
            if self.image is None:
                self.screen_label.setText('Not connected')
        self._update_polling()

    def toggle_play(self):
        self.playing = not self.playing
        self.play_btn.setText('⏸ Pause' if self.playing else '▶ Play')
        if not self.playing:
            self._frame_times = []
            self.status_label.setText('Paused')
        self._update_polling()

    def _update_polling(self):
        self.request_screen_polling.emit(
            self.connected and self.playing and self.isVisible())

    # The page only polls the meter while it is the visible tab.
    def showEvent(self, event):
        super().showEvent(event)
        self._update_polling()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.request_screen_polling.emit(False)

    # -- incoming frames ---------------------------------------------------

    def on_frame(self, data):
        if not self.playing:
            return   # frame was already in flight when pause was pressed
        image = QImage.fromData(bytes(data))
        if image.isNull():
            self.status_label.setText('Could not decode screen image')
            return
        self.image = image
        self.frame_count += 1
        self.export_btn.setEnabled(True)
        now = time.time()
        self._frame_times = [t for t in self._frame_times if now - t < FPS_WINDOW]
        self._frame_times.append(now)
        status = f'{image.width()}×{image.height()}'
        if len(self._frame_times) >= 2:
            fps = (len(self._frame_times) - 1) / (now - self._frame_times[0])
            status += f'  •  {fps:.1f} fps'
        self.status_label.setText(status)
        self._redraw()

    def on_error(self, msg):
        self.status_label.setText(f'Screen capture: {msg}')

    def _redraw(self):
        if self.image is None:
            return
        # Nearest-neighbour scaling keeps the LCD pixels crisp; cap the
        # upscale at 2x native, the low-res screen just blurs beyond that.
        target = self.screen_label.size().boundedTo(self.image.size() * 2)
        self.screen_label.setPixmap(QPixmap.fromImage(self.image).scaled(
            target, Qt.KeepAspectRatio, Qt.FastTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._redraw()

    # -- export --------------------------------------------------------------

    def export_image(self):
        if self.image is None:
            return
        default = time.strftime('fluke_screen_%Y%m%d_%H%M%S.png')
        start_dir = self.settings.value('last_dir', '')
        path, _ = QFileDialog.getSaveFileName(
            self, 'Export screen image',
            f'{start_dir}/{default}' if start_dir else default,
            'PNG image (*.png)')
        if not path:
            return
        self.settings.setValue('last_dir', path.rsplit('/', 1)[0])
        if not self.image.save(path):
            QMessageBox.critical(self, 'Export failed', f'Could not write {path}')
            return
        self.status_label.setText(f'Saved screen image to {path}')
