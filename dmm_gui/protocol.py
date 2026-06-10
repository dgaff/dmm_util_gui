"""Protocol layer for Fluke 287/289 DMMs.

Refactored from the fluke_28x_dmm_util CLI into a thread-safe library that
raises exceptions instead of printing and exiting. The wire protocol is
115200 8N1 over the IR serial cable; commands are short ASCII strings
terminated by <CR>, responses are an ACK digit + <CR> followed by either
ASCII (comma separated) or binary (prefixed with '#0') data.
"""

import calendar
import datetime
import struct
import threading
import time

import serial

OVERLOAD_VALUE = 9.99999999e+37

CMD_STATUS = {
    '0': 'OK',
    '1': 'Syntax error',
    '2': 'Execution error',
    '5': 'No data available',
}

# unit_multiplier exponent -> SI prefix
PREFIXES = {-9: 'n', -6: 'µ', -3: 'm', 0: '', 3: 'k', 6: 'M', 9: 'G'}


class DmmError(Exception):
    """Base error for DMM communication problems."""


class DmmNoData(DmmError):
    def __init__(self, cmd):
        super().__init__(f'No response from meter for command {cmd!r}')


class DmmCommandError(DmmError):
    def __init__(self, cmd, status):
        self.status = status
        desc = CMD_STATUS.get(status, 'Unknown error')
        super().__init__(f'Command {cmd!r} failed: {desc} (status {status})')


# ---------------------------------------------------------------------------
# Binary helpers (the meter sends word-swapped little-endian data)

def get_u16(data, offset):
    return struct.unpack_from('<H', data, offset)[0]


def get_s16(data, offset):
    return struct.unpack_from('<h', data, offset)[0]


def get_double(data, offset):
    # 8-byte double stored as two 4-byte little-endian words, low word first
    raw = data[offset + 4:offset + 8] + data[offset:offset + 4]
    return round(struct.unpack('<d', raw)[0], 8)


def parse_time(t):
    """Meter epoch -> struct_time. The meter clock is set to local wall time
    interpreted as UTC (see sync_clock), so gmtime round-trips it."""
    return time.gmtime(t)


def get_time(data, offset):
    return parse_time(get_double(data, offset))


def format_ts(ts):
    return time.strftime('%Y-%m-%d %H:%M:%S', ts)


def format_duration(start_ts, end_ts):
    seconds = time.mktime(end_ts) - time.mktime(start_ts)
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return f'{d:02d}:{h:02d}:{m:02d}:{s:02d}'


def format_reading(reading):
    """Render a parsed reading dict as ('5.029', 'mVAC') style strings.

    Overload/invalid states render as the state name ('OL', 'INVALID', ...).
    """
    state = reading.get('state', 'NORMAL')
    unit = reading.get('unit', '')
    mult = reading.get('unit_multiplier', 0)
    prefixed_unit = PREFIXES.get(mult, f'e{mult} ') + unit
    if state not in ('NORMAL',) or abs(reading['value']) >= OVERLOAD_VALUE:
        label = 'OL' if state in ('OL', 'OL_MINUS') or abs(reading['value']) >= OVERLOAD_VALUE else state
        return label, prefixed_unit
    decimals = max(0, reading.get('decimals', 4))
    scaled = reading['value'] / (10 ** mult)
    return f'{scaled:.{decimals}f}', prefixed_unit


class Fluke28x:
    """Connection to a Fluke 287/289. All methods are blocking; a lock makes
    individual transactions safe if called from multiple threads."""

    def __init__(self):
        self.ser = None
        self.port = None
        self.timeout = 0.09
        self._lock = threading.Lock()
        self._map_cache = {}

    # -- connection -----------------------------------------------------

    def connect(self, port, timeout=0.09):
        self.close()
        self.port = port
        self.timeout = timeout
        self._map_cache = {}
        self.ser = serial.Serial(
            port=port, baudrate=115200, bytesize=8, parity='N', stopbits=1,
            timeout=timeout, rtscts=False, dsrdtr=False)

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    @property
    def is_connected(self):
        return self.ser is not None and self.ser.is_open

    # -- low-level transaction -------------------------------------------

    @staticmethod
    def _response_complete(data):
        if len(data) < 2:
            return False
        if len(data) == 2 and data[0:1] == b'0' and data[1:2] == b'\r':
            return True  # bare ACK
        if data[0:1] != b'0':
            return len(data) >= 2 and data[1:2] == b'\r'  # error ACK
        if not data.startswith(b'0\r'):
            return False
        return len(data) >= 4 and data[-1:] == b'\r'

    # Quiet time required after a response looks complete before trusting it.
    # The framing check can match early: a bare '0\r' ACK is also the prefix
    # of a longer response (the meter sends the ACK before the payload), and
    # binary payloads may contain 0x0D bytes. ~20 ms is >200 byte times at
    # 115200 baud.
    GRACE = 0.02

    def _transact(self, cmd):
        payload = cmd.encode() + b'\r'
        for _attempt in range(3):
            self.ser.reset_input_buffer()
            self.ser.write(payload)
            data = b''
            idle_reads = 0
            while idle_reads < 20:
                chunk = self.ser.read(self.ser.in_waiting or 1)
                if chunk:
                    data += chunk
                    idle_reads = 0
                    if self._response_complete(data):
                        time.sleep(self.GRACE)
                        if self.ser.in_waiting:
                            continue   # more coming: that wasn't the end
                        return data
                else:
                    if self._response_complete(data):
                        return data
                    idle_reads += 1
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        raise DmmNoData(cmd)

    def command(self, cmd):
        """Send a command. Returns a list of ASCII fields, or bytes for a
        binary response, or [] for a bare acknowledge."""
        if not self.is_connected:
            raise DmmError('Not connected')
        with self._lock:
            data = self._transact(cmd)
        status = chr(data[0])
        if status != '0':
            raise DmmCommandError(cmd, status)
        if len(data) == 2:
            return []
        body = data[2:]
        if body[:2] == b'#0':
            return bytes(body[2:-1])
        return body[:-1].decode(errors='replace').split(',')

    # -- enum maps --------------------------------------------------------

    def qemap(self, map_name):
        if map_name in self._map_cache:
            return self._map_cache[map_name]
        res = self.command('qemap ' + map_name)
        entry_count = int(res.pop(0))
        if len(res) != entry_count * 2:
            raise DmmError(f'Error parsing qemap {map_name}')
        dmm_map = {res[i]: res[i + 1] for i in range(0, len(res), 2)}
        self._map_cache[map_name] = dmm_map
        return dmm_map

    def _map_value(self, map_name, data, offset):
        dmm_map = self.qemap(map_name)
        value = str(get_u16(data, offset))
        if value not in dmm_map:
            raise DmmError(f'Cannot find key {value} in map {map_name}')
        return dmm_map[value]

    def _parse_readings(self, reading_bytes):
        readings = {}
        for i in range(0, len(reading_bytes), 30):
            r = reading_bytes[i:i + 30]
            readings[self._map_value('readingid', r, 0)] = {
                'value': get_double(r, 2),
                'unit': self._map_value('unit', r, 10),
                'unit_multiplier': get_s16(r, 12),
                'decimals': get_s16(r, 14),
                'display_digits': get_s16(r, 16),
                'state': self._map_value('state', r, 18),
                'attribute': self._map_value('attribute', r, 20),
                'ts': get_time(r, 22),
            }
        return readings

    # -- identity / config -------------------------------------------------

    def id(self):
        res = self.command('ID')
        return {'model_number': res[0], 'software_version': res[1],
                'serial_number': res[2]}

    def clock(self):
        return self.command('qmp clock')[0]

    def sync_clock(self):
        """Set the meter clock to the host's current local wall time."""
        lt = calendar.timegm(datetime.datetime.now().utctimetuple())
        self.command('mp clock,' + str(lt))

    def get_property(self, prop):
        return self.command('qmp ' + prop)[0].strip("'")

    def set_property(self, prop, value):
        self.command(f'mp {prop},{value}')

    def get_string_property(self, prop):
        return self.command('qmpq ' + prop)[0].strip("'")

    def set_string_property(self, prop, value):
        self.command(f"mpq {prop},'{value}'")

    def config(self):
        info = self.id()
        cfg = {
            'Model': info['model_number'],
            'Software Version': info['software_version'],
            'Serial Number': info['serial_number'],
            'Current meter time': format_ts(time.gmtime(int(self.clock()))),
            'Company': self.get_string_property('company'),
            'Contact': self.get_string_property('contact'),
            'Operator': self.get_string_property('operator'),
            'Site': self.get_string_property('site'),
            'Autohold Threshold': self.get_property('aheventTh'),
            'Language': self.get_property('lang'),
            'Date Format': self.get_property('dateFmt'),
            'Time Format': self.get_property('timeFmt'),
            'Digits': self.get_property('digits'),
            'Beeper': self.get_property('beeper'),
            'Temperature Offset Shift': self.get_property('tempOS'),
            'Numeric Format': self.get_property('numFmt'),
            'Auto Backlight Timeout': self.get_property('ablto'),
            'Auto Power Off': self.get_property('apoffto'),
        }
        return cfg

    # -- save-name slots ----------------------------------------------------

    def names(self):
        out = []
        for i in range(8):
            res = self.command(f'qsavname {i}')
            out.append(res[0].split('\r')[0].strip("'"))
        return out

    def set_name(self, index, name):
        """index is 0-based slot number."""
        self.command(f'savname {index},"{name}"')

    # -- live data ------------------------------------------------------------

    def qddb(self):
        """Query displayed data (binary form). Returns dict with readings."""
        data = b''
        for _retry in range(3):
            data = self.command('qddb')
            if isinstance(data, bytes) and len(data) >= 34 \
                    and len(data) == get_u16(data, 32) * 30 + 34:
                break
        else:
            raise DmmError('qddb parse error, got %s of length %d'
                           % (type(data).__name__, len(data)))
        return {
            'prim_function': self._map_value('primfunction', data, 0),
            'sec_function': self._map_value('secfunction', data, 2),
            'auto_range': self._map_value('autorange', data, 4),
            'unit': self._map_value('unit', data, 6),
            'range_max': get_double(data, 8),
            'unit_multiplier': get_s16(data, 16),
            'bolt': self._map_value('bolt', data, 18),
            'mode': self._map_value('mode', data, 28),
            'readings': self._parse_readings(data[34:]),
        }

    def qm(self):
        """Query primary measurement (simple ASCII form)."""
        res = self.command('QM')
        return {'value': float(res[0]), 'unit': res[1],
                'state': res[2], 'attribute': res[3]}

    # -- stored data ------------------------------------------------------------

    def counts(self):
        res = self.command('qsls')
        return {'recordings': int(res[0]), 'minmax': int(res[1]),
                'peak': int(res[2]), 'measurements': int(res[3])}

    def recording_info(self, idx):
        """idx is 0-based. Returns recording session header."""
        res = self.command('qrsi ' + str(idx))
        reading_count = get_u16(res, 76)
        if len(res) < reading_count * 30 + 78:
            raise DmmError('qrsi parse error, expected at least %d bytes, got %d'
                           % (reading_count * 30 + 78, len(res)))
        return {
            'seq_no': get_u16(res, 0),
            'start_ts': get_time(res, 4),
            'end_ts': get_time(res, 12),
            'sample_interval': get_double(res, 20),
            'event_threshold': get_double(res, 28),
            'reading_index': get_u16(res, 36),
            'num_samples': get_u16(res, 40),
            'prim_function': self._map_value('primfunction', res, 44),
            'sec_function': self._map_value('secfunction', res, 46),
            'auto_range': self._map_value('autorange', res, 48),
            'unit': self._map_value('unit', res, 50),
            'range_max': get_double(res, 52),
            'unit_multiplier': get_s16(res, 60),
            'bolt': self._map_value('bolt', res, 62),
            'mode': self._map_value('mode', res, 72),
            'readings': self._parse_readings(res[78:78 + reading_count * 30]),
            'name': res[78 + reading_count * 30:].decode(errors='replace'),
        }

    def recording_sample(self, reading_idx, sample_idx):
        """One interval sample of a recording session."""
        res = b''
        for _retry in range(20):
            res = self.command(f'qsrr {reading_idx},{sample_idx}')
            if len(res) == 146:
                return {
                    'start_ts': get_time(res, 0),
                    'end_ts': get_time(res, 8),
                    'readings': self._parse_readings(res[16:16 + 30 * 3]),
                    'duration': round(get_u16(res, 106), 5),
                    'readings2': self._parse_readings(res[110:110 + 30]),
                    'record_type': self._map_value('recordtype', res, 140),
                    'stable': self._map_value('isstableflag', res, 142),
                    'transient_state': self._map_value('transientstate', res, 144),
                }
        raise DmmError('Invalid qsrr block size: %d, expected 146' % len(res))

    def minmax_info(self, idx):
        return self._min_max_cmd('qmmsi', idx)

    def peak_info(self, idx):
        return self._min_max_cmd('qpsi', idx)

    def _min_max_cmd(self, cmd, idx):
        res = self.command(f'{cmd} {idx}')
        reading_count = get_u16(res, 52)
        if len(res) < reading_count * 30 + 54:
            raise DmmError('%s parse error, expected at least %d bytes, got %d'
                           % (cmd, reading_count * 30 + 54, len(res)))
        return {
            'seq_no': get_u16(res, 0),
            'start_ts': get_time(res, 4),
            'end_ts': get_time(res, 12),
            'prim_function': self._map_value('primfunction', res, 20),
            'sec_function': self._map_value('secfunction', res, 22),
            'auto_range': self._map_value('autorange', res, 24),
            'unit': self._map_value('unit', res, 26),
            'range_max': get_double(res, 28),
            'unit_multiplier': get_s16(res, 36),
            'bolt': self._map_value('bolt', res, 38),
            'ts3': get_time(res, 40),
            'mode': self._map_value('mode', res, 48),
            'readings': self._parse_readings(res[54:54 + reading_count * 30]),
            'name': res[54 + reading_count * 30:].decode(errors='replace'),
        }

    def saved_measurement(self, idx):
        res = self.command('qsmr ' + str(idx))
        reading_count = get_u16(res, 36)
        if len(res) < reading_count * 30 + 38:
            raise DmmError('qsmr parse error, expected at least %d bytes, got %d'
                           % (reading_count * 30 + 38, len(res)))
        return {
            'seq_no': get_u16(res, 0),
            'prim_function': self._map_value('primfunction', res, 4),
            'sec_function': self._map_value('secfunction', res, 6),
            'auto_range': self._map_value('autorange', res, 8),
            'unit': self._map_value('unit', res, 10),
            'range_max': get_double(res, 12),
            'unit_multiplier': get_s16(res, 20),
            'bolt': self._map_value('bolt', res, 22),
            'mode': self._map_value('mode', res, 32),
            'readings': self._parse_readings(res[38:38 + reading_count * 30]),
            'name': res[38 + reading_count * 30:].decode(errors='replace'),
        }
