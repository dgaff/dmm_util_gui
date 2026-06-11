"""A fake Fluke 289 serial port for testing the GUI without hardware.

FakeSerial mimics the subset of pyserial's API the app uses and answers
protocol commands the way a real meter does (binary framing included).
"""

import gzip
import struct
import time

MAPS = {
    'primfunction': {'0': 'V_DC', '1': 'V_AC', '2': 'OHMS'},
    'secfunction': {'0': 'NONE', '1': 'HERTZ'},
    'autorange': {'0': 'AUTO', '1': 'MANUAL'},
    'unit': {'0': 'VDC', '1': 'VAC', '2': 'OHM'},
    'bolt': {'0': 'OFF', '1': 'ON'},
    'mode': {'0': 'NONE', '1': 'RECORD'},
    'readingid': {'0': 'LIVE', '1': 'PRIMARY', '2': 'MINIMUM',
                  '3': 'MAXIMUM', '4': 'AVERAGE'},
    'state': {'0': 'NORMAL', '1': 'OL', '2': 'INVALID'},
    'attribute': {'0': 'NONE'},
    'recordtype': {'0': 'INTERVAL', '1': 'INPUT'},
    'isstableflag': {'0': 'STABLE', '1': 'UNSTABLE'},
    'transientstate': {'0': 'NONE'},
}

READING_IDS = {name: int(k) for k, name in MAPS['readingid'].items()}


def enc_u16(v):
    return struct.pack('<H', v & 0xFFFF)


def enc_double(v):
    le = struct.pack('<d', v)
    return le[4:] + le[:4]


def enc_reading(reading_id, value, unit=0, mult=0, decimals=4, digits=5,
                state=0, attribute=0, ts=None):
    ts = time.time() if ts is None else ts
    return (enc_u16(reading_id) + enc_double(value) + enc_u16(unit) +
            enc_u16(mult) + enc_u16(decimals) + enc_u16(digits) +
            enc_u16(state) + enc_u16(attribute) + enc_double(ts))


def make_bmp(width=32, height=32):
    """A 24-bit BMP with a noisy pattern so its gzip stream spans several
    qlcdbm chunks."""
    rows = b''
    for y in range(height):
        row = bytes((x * 7 + y * 13 + c * 29) % 251
                    for x in range(width) for c in range(3))
        rows += row + b'\x00' * ((4 - len(row) % 4) % 4)
    header = b'BM' + struct.pack('<IHHI', 14 + 40 + len(rows), 0, 0, 54)
    info = struct.pack('<IiiHHIIiiII', 40, width, height, 1, 24, 0,
                       len(rows), 2835, 2835, 0, 0)
    return header + info + rows


class FakeSerial:
    """Drop-in stand-in for serial.Serial connected to a fake Fluke 289."""

    def __init__(self, port=None, timeout=0.09, **kwargs):
        self.port = port
        self.timeout = timeout
        self.is_open = True
        self._rx = b''      # bytes waiting for the host to read
        self._cmd = b''
        self.props = {'clock': str(int(time.time())), 'aheventTh': '0.5',
                      'lang': 'ENGLISH', 'dateFmt': 'MM_DD', 'timeFmt': '24',
                      'digits': '5', 'beeper': 'ON', 'tempOS': 'OFF',
                      'numFmt': 'POINT', 'ablto': 'OFF', 'apoffto': 'OFF'}
        self.string_props = {'company': 'Acme', 'contact': 'Doug',
                             'operator': 'Doug', 'site': 'Lab'}
        self.savnames = [f'SAVE{i + 1}' for i in range(8)]
        self.live_value = 1.2345
        self.num_rec_samples = 5
        self.counts = {'RECORDED': 1, 'MIN_MAX': 1, 'PEAK': 1, 'MEASUREMENT': 1}
        self.screen_bmp = make_bmp()
        self.screen_gz = gzip.compress(self.screen_bmp)
        self.screen_chunk = 512

    # -- pyserial API ------------------------------------------------------

    @property
    def in_waiting(self):
        return len(self._rx)

    def write(self, data):
        self._cmd += data
        while b'\r' in self._cmd:
            cmd, self._cmd = self._cmd.split(b'\r', 1)
            self._handle(cmd.decode())

    def read(self, n):
        if not self._rx:
            time.sleep(min(self.timeout, 0.001))
            return b''
        out, self._rx = self._rx[:n], self._rx[n:]
        return out

    def reset_input_buffer(self):
        self._rx = b''

    def reset_output_buffer(self):
        pass

    def close(self):
        self.is_open = False

    def open(self):
        self.is_open = True

    # -- meter behaviour --------------------------------------------------

    def _ascii(self, text):
        self._rx += b'0\r' + text.encode() + b'\r'

    def _binary(self, payload):
        self._rx += b'0\r#0' + payload + b'\r'

    def _ack(self):
        self._rx += b'0\r'

    def _error(self, status='1'):
        self._rx += status.encode() + b'\r'

    def _handle(self, cmd):
        cmd = cmd.strip()
        low = cmd.lower()
        if low == 'id':
            self._ascii('FLUKE 289,V1.10,99990000')
        elif low.startswith('qemap '):
            name = low.split()[1]
            m = MAPS.get(name)
            if m is None:
                self._error()
                return
            parts = [str(len(m))]
            for k, v in m.items():
                parts += [k, v]
            self._ascii(','.join(parts))
        elif low == 'qddb':
            self._binary(self._qddb())
        elif low.startswith('qlcdbm '):
            offset = int(cmd.split()[1])
            chunk = self.screen_gz[offset:offset + self.screen_chunk]
            self._rx += b'0\r' + str(offset).encode() + b' #0' + chunk + b'\r'
        elif low == 'qsls':
            c = self.counts
            self._ascii(f"{c['RECORDED']},{c['MIN_MAX']},{c['PEAK']},{c['MEASUREMENT']}")
        elif low.startswith('csd '):
            category = cmd.split()[1].upper()
            if category == 'ALL':
                self.counts = dict.fromkeys(self.counts, 0)
            elif category in self.counts:
                self.counts[category] = 0
            else:
                self._error()
                return
            self._ack()
        elif low.startswith('qrsi '):
            self._binary(self._qrsi())
        elif low.startswith('qsrr '):
            idx = int(cmd.split()[1].split(',')[1])
            self._binary(self._qsrr(idx))
        elif low.startswith('qmmsi ') or low.startswith('qpsi '):
            self._binary(self._session(b'MinMax-1' if 'qmmsi' in low else b'Peak-1'))
        elif low.startswith('qsmr '):
            self._binary(self._qsmr())
        elif low.startswith('qsavname '):
            self._ascii(self.savnames[int(cmd.split()[1])])
        elif low.startswith('savname '):
            slot, name = cmd[8:].split(',', 1)
            self.savnames[int(slot)] = name.strip('"')
            self._ack()
        elif low.startswith('qmp '):
            prop = cmd.split()[1]
            if prop in self.props:
                self._ascii(self.props[prop])
            else:
                self._error()
        elif low.startswith('mp '):
            prop, value = cmd[3:].split(',', 1)
            self.props[prop] = value
            self._ack()
        elif low.startswith('qmpq '):
            prop = cmd.split()[1]
            if prop in self.string_props:
                self._ascii(f"'{self.string_props[prop]}'")
            else:
                self._error()
        elif low.startswith('mpq '):
            prop, value = cmd[4:].split(',', 1)
            self.string_props[prop] = value.strip("'")
            self._ack()
        elif low in ('ds', 'ri', 'rmp'):
            self._ack()
        elif low == 'qm':
            self._ascii(f'{self.live_value}E0,VDC,NORMAL,NONE')
        else:
            self._error()

    def _qddb(self):
        readings = (enc_reading(READING_IDS['LIVE'], self.live_value, mult=0) +
                    enc_reading(READING_IDS['PRIMARY'], self.live_value, mult=0))
        return (enc_u16(0) + enc_u16(0) + enc_u16(0) + enc_u16(0) +
                enc_double(5.0) + enc_u16(0) + enc_u16(0) +
                enc_double(time.time()) + enc_u16(0) + enc_u16(0) +
                enc_u16(2) + readings)

    def _qrsi(self):
        now = time.time()
        head = (enc_u16(1) + enc_u16(0) +                      # seq, un2
                enc_double(now - 600) + enc_double(now) +      # start, end
                enc_double(1.0) + enc_double(0.5) +            # interval, threshold
                enc_u16(0) + enc_u16(0) +                      # reading_index, un3
                enc_u16(self.num_rec_samples) + enc_u16(0) +   # num_samples, un4
                enc_u16(0) + enc_u16(0) + enc_u16(0) + enc_u16(0) +  # prim,sec,ar,unit
                enc_double(5.0) + enc_u16(0) + enc_u16(0) +    # range, mult, bolt
                enc_u16(0) * 4 +                               # un8..un11
                enc_u16(1) + enc_u16(0) +                      # mode, un12
                enc_u16(1))                                    # reading count
        return head + enc_reading(READING_IDS['PRIMARY'], 1.0) + b'Rec-1'

    def _qsrr(self, idx):
        t0 = time.time() - 600 + idx
        v = 1.0 + idx * 0.1
        readings = (enc_reading(READING_IDS['MAXIMUM'], v + 0.05, ts=t0) +
                    enc_reading(READING_IDS['MINIMUM'], v - 0.05, ts=t0) +
                    enc_reading(READING_IDS['AVERAGE'], v * 4, ts=t0))
        body = (enc_double(t0) + enc_double(t0 + 1) + readings +
                enc_u16(4) + enc_u16(0) +                       # duration(#samples), un2
                enc_reading(READING_IDS['PRIMARY'], v, ts=t0) +
                enc_u16(0) + enc_u16(0) + enc_u16(0))           # type, stable, transient
        assert len(body) == 146, len(body)
        return body

    def _session(self, name):
        now = time.time()
        readings = (enc_reading(READING_IDS['PRIMARY'], 1.0) +
                    enc_reading(READING_IDS['MINIMUM'], 0.5) +
                    enc_reading(READING_IDS['MAXIMUM'], 1.5) +
                    enc_reading(READING_IDS['AVERAGE'], 1.1))
        return (enc_u16(1) + enc_u16(0) +
                enc_double(now - 60) + enc_double(now) +
                enc_u16(0) + enc_u16(0) + enc_u16(0) + enc_u16(0) +
                enc_double(5.0) + enc_u16(0) + enc_u16(0) +
                enc_double(now) + enc_u16(0) + enc_u16(0) +
                enc_u16(4) + readings + name)

    def _qsmr(self):
        return (enc_u16(1) + enc_u16(0) +
                enc_u16(0) + enc_u16(0) + enc_u16(0) + enc_u16(0) +
                enc_double(5.0) + enc_u16(0) + enc_u16(0) +
                enc_u16(0) + enc_u16(0) + enc_u16(0) + enc_u16(0) +
                enc_u16(0) + enc_u16(0) +
                enc_u16(1) + enc_reading(READING_IDS['PRIMARY'], 3.3) +
                b'Meas-1')
