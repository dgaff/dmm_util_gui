"""Background BLE worker for the Fluke ir3000FC adapter ('IR 3000 FC').

bleak needs an asyncio event loop, so one runs forever on a dedicated daemon
thread; GUI-thread calls schedule coroutines onto it with
run_coroutine_threadsafe. Qt signals emitted from that thread are delivered
to GUI receivers through queued connections — connect them to bound methods
of GUI objects, never lambdas (see project notes).

The adapter streams the live-measurement record on the b698290f
characteristic; fluke289_bt_decode.decode_record turns it into a Reading
(with .secondary set when the meter's small display is active).
"""

import asyncio
import threading
import time

from PySide6.QtCore import QObject, Signal

from .fluke289_bt_decode import decode_record

DEVICE_NAME = 'IR 3000 FC'
LIVE_CHAR = 'b698290f-7562-11e2-b50d-00163e46f8fe'
FLUKE_UUID_FRAGMENT = '7562-11e2-b50d-00163e46f8fe'  # Fluke FC vendor base

# The ir3000FC is a bursty, low-duty-cycle advertiser: it goes silent for as
# long as ~15 s between bursts, so a one-shot discover() window can easily land
# entirely in a quiet gap and see nothing. Instead we keep a scanner running
# continuously and report any adapter seen within FRESH_SECONDS — long enough to
# bridge the gaps.
#
# SCAN_SECONDS only caps how long a refresh waits when nothing has been seen yet
# (first scan after launch, or the adapter went quiet for longer than
# FRESH_SECONDS). A warm set returns instantly, so this cap is irrelevant to the
# common case — but it MUST comfortably exceed the worst-case advertising gap, or
# a refresh that happens to start inside a long quiet gap gives up too early and
# falsely reports "no adapter". Measured gaps run to ~15 s, so leave generous
# margin.
SCAN_SECONDS = 25.0
FRESH_SECONDS = 30.0


class BleWorker(QObject):
    scan_finished = Signal(list)         # [(name, address), ...]
    scan_failed = Signal(str)
    connected = Signal(dict)             # {'name': ..., 'address': ...}
    connect_failed = Signal(str)
    disconnected = Signal()
    ble_reading = Signal(float, object)  # host timestamp, decoded Reading

    def __init__(self):
        super().__init__()
        self._devices = {}     # address -> (BLEDevice, name, last-seen monotonic)
        self._scanner = None   # the continuously-running BleakScanner, if any
        self._scanner_lock = asyncio.Lock()
        self._client = None
        self._closing = False
        self._shutting_down = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name='ble-worker', daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # -- GUI-thread API ----------------------------------------------------

    def scan(self):
        asyncio.run_coroutine_threadsafe(self._scan(), self._loop)

    def connect_device(self, address):
        asyncio.run_coroutine_threadsafe(self._connect(address), self._loop)

    def disconnect_device(self):
        asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)

    def shutdown(self):
        async def _bye():
            self._shutting_down = True
            await self._disconnect()
            await self._stop_scanner()
            self._loop.stop()
        asyncio.run_coroutine_threadsafe(_bye(), self._loop)
        self._thread.join(timeout=3.0)

    # -- continuous scanner --------------------------------------------------

    async def _ensure_scanner(self):
        """Start the background scanner if it isn't already running. Raises on
        import/start failure so the caller can decide whether to surface it."""
        async with self._scanner_lock:
            if self._scanner is not None:
                return
            from bleak import BleakScanner
            scanner = BleakScanner(detection_callback=self._on_detect)
            await scanner.start()
            self._scanner = scanner

    async def _stop_scanner(self):
        async with self._scanner_lock:
            scanner, self._scanner = self._scanner, None
        if scanner is not None:
            try:
                await scanner.stop()
            except Exception:
                pass

    async def _resume_scanner(self):
        """Restart scanning after a disconnect/failed connect, quietly."""
        if self._shutting_down:
            return
        try:
            await self._ensure_scanner()
        except Exception:
            pass

    def _on_detect(self, device, adv):
        """bleak detection callback (asyncio thread): remember any adapter that
        matches by name or by the Fluke FC vendor UUID, with a fresh timestamp."""
        name = (device.name or adv.local_name or '').strip()
        is_fluke = name.lower() == DEVICE_NAME.lower() or any(
            FLUKE_UUID_FRAGMENT in u.lower() for u in (adv.service_uuids or []))
        if not is_fluke:
            return
        self._devices[device.address] = (
            device, name or DEVICE_NAME, time.monotonic())

    def _fresh_results(self):
        cutoff = time.monotonic() - FRESH_SECONDS
        results = [(name, address)
                   for address, (_dev, name, ts) in self._devices.items()
                   if ts >= cutoff]
        return sorted(results, key=lambda r: r[1])

    # -- coroutines (run on the asyncio thread) ------------------------------

    async def _scan(self):
        # While connected the scanner is paused to keep the radio free; just
        # report what we already know rather than fighting the connection.
        if self._client is not None:
            self.scan_finished.emit(self._fresh_results())
            return
        try:
            await self._ensure_scanner()
        except ImportError:
            self.scan_failed.emit('bleak is not installed (pip install bleak)')
            return
        except Exception as err:
            self.scan_failed.emit(str(err))
            return
        # The scanner keeps a warm set across bursts, so this usually returns at
        # once; only a cold set (just-launched, first burst not yet arrived)
        # waits, and it bails the moment the adapter shows up.
        deadline = time.monotonic() + SCAN_SECONDS
        while time.monotonic() < deadline and not self._fresh_results():
            await asyncio.sleep(0.25)
        self.scan_finished.emit(self._fresh_results())

    async def _connect(self, address):
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            self.connect_failed.emit('bleak is not installed (pip install bleak)')
            return
        if self._client is not None:
            self.connect_failed.emit('Already connected to a BLE adapter')
            return
        # Free the radio for the connection: the continuous scanner and an
        # active connect can contend, so pause scanning first.
        await self._stop_scanner()
        try:
            entry = self._devices.get(address)
            target = entry[0] if entry else None
            if target is None:
                target = await BleakScanner.find_device_by_address(
                    address, timeout=10.0)
                if target is None:
                    raise RuntimeError(
                        f'{DEVICE_NAME} not found — is the adapter awake?')
            client = BleakClient(target, timeout=20.0,
                                 disconnected_callback=self._on_ble_drop)
            await client.connect()
            await client.start_notify(LIVE_CHAR, self._on_notify)
        except Exception as err:
            self.connect_failed.emit(str(err))
            await self._resume_scanner()
            return
        self._closing = False
        self._client = client
        self.connected.emit({'name': getattr(target, 'name', None) or DEVICE_NAME,
                             'address': address})

    async def _disconnect(self):
        client, self._client = self._client, None
        if client is None:
            return
        self._closing = True
        try:
            await client.disconnect()
        except Exception:
            pass
        self.disconnected.emit()
        await self._resume_scanner()

    # -- bleak callbacks (asyncio thread) ------------------------------------

    def _on_notify(self, _sender, data: bytearray):
        try:
            reading = decode_record(bytes(data))
        except Exception:
            return     # short/garbled record; the next notification will do
        self.ble_reading.emit(time.time(), reading)

    def _on_ble_drop(self, _client):
        if self._closing:
            return     # deliberate disconnect already reported
        self._client = None
        self.disconnected.emit()
        self._loop.create_task(self._resume_scanner())
