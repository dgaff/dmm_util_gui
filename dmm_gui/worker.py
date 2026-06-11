"""Background serial worker.

All meter I/O happens on a dedicated QThread so the UI never blocks on the
(slow) optical serial link. The GUI talks to the worker through the queued
signals on `Requests`; results come back via the signals on `DmmWorker`.
"""

import time

import serial
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from . import protocol
from .protocol import Fluke28x, DmmError


class Requests(QObject):
    """GUI-side signal bundle; connected to DmmWorker slots with queued
    connections so emitting them runs the slot on the worker thread."""
    open_port = Signal(str, float)
    close_port = Signal()
    set_polling = Signal(bool, float)     # enabled, interval seconds
    set_screen_polling = Signal(bool)     # live LCD capture on/off
    refresh_inventory = Signal()
    download_recording = Signal(int, str)  # 0-based index, name
    fetch_session = Signal(str, int)       # 'minmax'|'peak', 0-based index
    fetch_setup = Signal()
    apply_strings = Signal(dict)           # {property: value}
    sync_clock = Signal()
    set_name = Signal(int, str)            # 0-based slot, name
    raw_command = Signal(str)
    simple_command = Signal(str)           # DS / RI / RMP
    clear_memory = Signal(str)             # csd category (RECORDED, ALL, ...)


class DmmWorker(QObject):
    connected = Signal(dict)               # ID info
    connect_failed = Signal(str)
    disconnected = Signal()
    live_reading = Signal(float, dict)     # host timestamp, qddb dict
    live_error = Signal(str)
    screen_frame = Signal(object)          # decompressed LCD image bytes
    screen_error = Signal(str)
    inventory_ready = Signal(dict)
    download_progress = Signal(int, int)
    recording_ready = Signal(dict)         # {'index':..., 'info':..., 'samples':[...]}
    session_ready = Signal(str, dict)      # kind, session dict
    setup_ready = Signal(dict)             # {'config': {...}, 'names': [...]}
    raw_result = Signal(str, object)
    memory_cleared = Signal(str)           # category that was cleared
    op_done = Signal(str)
    op_failed = Signal(str)

    MAX_POLL_FAILURES = 5
    SCREEN_INTERVAL_MS = 250   # capture itself takes ~0.5 s; this just paces

    def __init__(self):
        super().__init__()
        self.dmm = Fluke28x()
        self._timer = None
        self._poll_interval = 1.0
        self._poll_enabled = False
        self._poll_failures = 0
        self._screen_timer = None
        self._screen_enabled = False
        self.cancel_requested = False      # set directly from the GUI thread

    def bind(self, req: Requests):
        req.open_port.connect(self.open_port)
        req.close_port.connect(self.close_port)
        req.set_polling.connect(self.set_polling)
        req.set_screen_polling.connect(self.set_screen_polling)
        req.refresh_inventory.connect(self.refresh_inventory)
        req.download_recording.connect(self.download_recording)
        req.fetch_session.connect(self.fetch_session)
        req.fetch_setup.connect(self.fetch_setup)
        req.apply_strings.connect(self.apply_strings)
        req.sync_clock.connect(self.sync_clock)
        req.set_name.connect(self.set_name)
        req.raw_command.connect(self.raw_command)
        req.simple_command.connect(self.simple_command)
        req.clear_memory.connect(self.do_clear_memory)

    # ------------------------------------------------------------------

    def _ensure_timer(self):
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._poll_once)

    def _ensure_screen_timer(self):
        if self._screen_timer is None:
            self._screen_timer = QTimer(self)
            self._screen_timer.timeout.connect(self._capture_screen)

    def _fail(self, what, err):
        self.op_failed.emit(f'{what}: {err}')

    def _serial_lost(self, err):
        if self._timer:
            self._timer.stop()
        if self._screen_timer:
            self._screen_timer.stop()
        self.dmm.close()
        self.disconnected.emit()
        self.op_failed.emit(f'Connection lost: {err}')

    # -- slots (run on worker thread) ------------------------------------

    @Slot(str, float)
    def open_port(self, port, timeout):
        try:
            self.dmm.connect(port, timeout)
            info = self.dmm.id()
        except Exception as err:
            self.dmm.close()
            self.connect_failed.emit(str(err))
            return
        self.connected.emit(info)
        self._ensure_timer()
        if self._poll_enabled:
            self._timer.start(int(self._poll_interval * 1000))
        self._ensure_screen_timer()
        if self._screen_enabled:
            self._screen_timer.start(self.SCREEN_INTERVAL_MS)

    @Slot()
    def close_port(self):
        if self._timer:
            self._timer.stop()
        if self._screen_timer:
            self._screen_timer.stop()
        self.dmm.close()
        self.disconnected.emit()

    @Slot(bool, float)
    def set_polling(self, enabled, interval_s):
        self._poll_enabled = enabled
        self._poll_interval = max(0.1, interval_s)
        self._ensure_timer()
        if enabled and self.dmm.is_connected:
            self._timer.start(int(self._poll_interval * 1000))
            self._poll_once()
        else:
            self._timer.stop()

    def _poll_once(self):
        if not self.dmm.is_connected:
            return
        try:
            data = self.dmm.qddb()
        except (serial.SerialException, OSError) as err:
            self._serial_lost(err)
            return
        except Exception as err:
            # Meter between dial positions, popup open, framing glitch, etc.
            # Keep polling, but give up if the meter stops answering at all.
            self._poll_failures += 1
            if self._poll_failures >= self.MAX_POLL_FAILURES:
                self._serial_lost(err)
            else:
                self.live_error.emit(str(err))
            return
        self._poll_failures = 0
        self.live_reading.emit(time.time(), data)

    @Slot(bool)
    def set_screen_polling(self, enabled):
        self._screen_enabled = enabled
        self._ensure_screen_timer()
        if enabled and self.dmm.is_connected:
            self._screen_timer.start(self.SCREEN_INTERVAL_MS)
            self._capture_screen()
        else:
            self._screen_timer.stop()

    def _capture_screen(self):
        if not self.dmm.is_connected:
            return
        try:
            image = self.dmm.qlcdbm()
        except (serial.SerialException, OSError) as err:
            self._serial_lost(err)
            return
        except Exception as err:
            self.screen_error.emit(str(err))
            return
        self.screen_frame.emit(image)

    @Slot()
    def refresh_inventory(self):
        try:
            counts = self.dmm.counts()
            inv = {'recordings': [], 'minmax': [], 'peak': [], 'measurements': []}
            for i in range(counts['recordings']):
                inv['recordings'].append(self.dmm.recording_info(i))
            for i in range(counts['minmax']):
                inv['minmax'].append(self.dmm.minmax_info(i))
            for i in range(counts['peak']):
                inv['peak'].append(self.dmm.peak_info(i))
            for i in range(counts['measurements']):
                inv['measurements'].append(self.dmm.saved_measurement(i))
        except (serial.SerialException, OSError) as err:
            self._serial_lost(err)
            return
        except Exception as err:
            self._fail('Reading memory list', err)
            return
        self.inventory_ready.emit(inv)

    @Slot(int, str)
    def download_recording(self, index, name):
        self.cancel_requested = False
        try:
            info = self.dmm.recording_info(index)
            total = info['num_samples']
            samples = []
            for k in range(total):
                if self.cancel_requested:
                    self.op_done.emit('Download cancelled')
                    return
                samples.append(self.dmm.recording_sample(info['reading_index'], k))
                self.download_progress.emit(k + 1, total)
        except (serial.SerialException, OSError) as err:
            self._serial_lost(err)
            return
        except Exception as err:
            self._fail(f'Downloading recording "{name}"', err)
            return
        self.recording_ready.emit({'index': index, 'info': info, 'samples': samples})

    @Slot(str, int)
    def fetch_session(self, kind, index):
        try:
            if kind == 'minmax':
                session = self.dmm.minmax_info(index)
            else:
                session = self.dmm.peak_info(index)
        except (serial.SerialException, OSError) as err:
            self._serial_lost(err)
            return
        except Exception as err:
            self._fail(f'Reading {kind} session', err)
            return
        self.session_ready.emit(kind, session)

    @Slot()
    def fetch_setup(self):
        try:
            cfg = self.dmm.config()
            names = self.dmm.names()
        except (serial.SerialException, OSError) as err:
            self._serial_lost(err)
            return
        except Exception as err:
            self._fail('Reading meter setup', err)
            return
        self.setup_ready.emit({'config': cfg, 'names': names})

    @Slot(dict)
    def apply_strings(self, values):
        try:
            for prop, value in values.items():
                self.dmm.set_string_property(prop, value)
        except (serial.SerialException, OSError) as err:
            self._serial_lost(err)
            return
        except Exception as err:
            self._fail('Writing meter info', err)
            return
        self.op_done.emit('Meter info updated')

    @Slot()
    def sync_clock(self):
        try:
            self.dmm.sync_clock()
        except (serial.SerialException, OSError) as err:
            self._serial_lost(err)
            return
        except Exception as err:
            self._fail('Syncing clock', err)
            return
        self.op_done.emit('Meter clock synced to this Mac')

    @Slot(int, str)
    def set_name(self, slot, name):
        try:
            self.dmm.set_name(slot, name)
        except (serial.SerialException, OSError) as err:
            self._serial_lost(err)
            return
        except Exception as err:
            self._fail(f'Setting name slot {slot + 1}', err)
            return
        self.op_done.emit(f'Save name {slot + 1} set to "{name}"')

    @Slot(str)
    def raw_command(self, cmd):
        try:
            result = self.dmm.command(cmd)
        except (serial.SerialException, OSError) as err:
            self._serial_lost(err)
            return
        except Exception as err:
            self.raw_result.emit(cmd, err)
            return
        self.raw_result.emit(cmd, result)

    @Slot(str)
    def do_clear_memory(self, category):
        try:
            self.dmm.command('csd ' + category)
        except (serial.SerialException, OSError) as err:
            self._serial_lost(err)
            return
        except Exception as err:
            self._fail(f'Clearing {category}', err)
            return
        self.memory_cleared.emit(category)

    @Slot(str)
    def simple_command(self, cmd):
        try:
            self.dmm.command(cmd)
        except (serial.SerialException, OSError) as err:
            self._serial_lost(err)
            return
        except Exception as err:
            self._fail(cmd, err)
            return
        self.op_done.emit(f'{cmd} acknowledged by meter')
