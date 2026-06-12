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
SCAN_SECONDS = 5.0


class BleWorker(QObject):
    scan_finished = Signal(list)         # [(name, address), ...]
    scan_failed = Signal(str)
    connected = Signal(dict)             # {'name': ..., 'address': ...}
    connect_failed = Signal(str)
    disconnected = Signal()
    ble_reading = Signal(float, object)  # host timestamp, decoded Reading

    def __init__(self):
        super().__init__()
        self._devices = {}     # address -> BLEDevice from the last scan
        self._client = None
        self._closing = False
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
            await self._disconnect()
            self._loop.stop()
        asyncio.run_coroutine_threadsafe(_bye(), self._loop)
        self._thread.join(timeout=3.0)

    # -- coroutines (run on the asyncio thread) ------------------------------

    async def _scan(self):
        try:
            from bleak import BleakScanner
            found = await BleakScanner.discover(timeout=SCAN_SECONDS,
                                                return_adv=True)
        except ImportError:
            self.scan_failed.emit('bleak is not installed (pip install bleak)')
            return
        except Exception as err:
            self.scan_failed.emit(str(err))
            return
        self._devices = {}
        results = []
        for address, (device, adv) in found.items():
            name = (device.name or adv.local_name or '').strip()
            if name.lower() == DEVICE_NAME.lower():
                self._devices[address] = device
                results.append((name, address))
        self.scan_finished.emit(sorted(results, key=lambda r: r[1]))

    async def _connect(self, address):
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            self.connect_failed.emit('bleak is not installed (pip install bleak)')
            return
        if self._client is not None:
            self.connect_failed.emit('Already connected to a BLE adapter')
            return
        try:
            target = self._devices.get(address)
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
