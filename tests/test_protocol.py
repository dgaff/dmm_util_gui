"""End-to-end test of the protocol layer against the fake meter.

Run with:  venv/bin/python tests/test_protocol.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_meter import FakeSerial
from dmm_gui import protocol
from dmm_gui.protocol import Fluke28x, DmmCommandError


class SlowAckSerial(FakeSerial):
    """Delivers the ACK ('0\\r') immediately but holds the rest of the
    response back for 10 ms — the timing of a real meter, where the ACK
    arrives before the payload. Regression test for the framing race that
    made qddb return an ASCII list ('a bytes-like object is required')."""

    _pending = b''
    _release_at = 0.0

    def write(self, data):
        super().write(data)
        self._pending = self._rx[2:]
        self._rx = self._rx[:2]
        self._release_at = time.time() + 0.01

    def _maybe_release(self):
        if self._pending and time.time() >= self._release_at:
            self._rx += self._pending
            self._pending = b''

    @property
    def in_waiting(self):
        self._maybe_release()
        return len(self._rx)

    def read(self, n):
        self._maybe_release()
        return super().read(n)


def main():
    protocol.serial.Serial = FakeSerial   # monkeypatch

    dmm = Fluke28x()
    dmm.connect('/dev/cu.fake', 0.01)

    info = dmm.id()
    assert info['model_number'] == 'FLUKE 289', info
    assert info['serial_number'] == '99990000'
    print('PASS id:', info)

    live = dmm.qddb()
    assert live['prim_function'] == 'V_DC'
    assert abs(live['readings']['LIVE']['value'] - 1.2345) < 1e-9
    assert live['readings']['PRIMARY']['unit'] == 'VDC'
    print('PASS qddb live value:', live['readings']['LIVE']['value'])

    counts = dmm.counts()
    assert counts == {'recordings': 1, 'minmax': 1, 'peak': 1, 'measurements': 1}
    print('PASS counts:', counts)

    rec = dmm.recording_info(0)
    assert rec['name'] == 'Rec-1'
    assert rec['num_samples'] == 5
    samples = [dmm.recording_sample(rec['reading_index'], k)
               for k in range(rec['num_samples'])]
    assert len(samples) == 5
    assert abs(samples[2]['readings2']['PRIMARY']['value'] - 1.2) < 1e-9
    assert samples[0]['record_type'] == 'INTERVAL'
    assert samples[0]['duration'] == 4
    print('PASS recording download:', rec['name'], len(samples), 'samples')

    mm = dmm.minmax_info(0)
    assert mm['name'] == 'MinMax-1'
    assert abs(mm['readings']['MAXIMUM']['value'] - 1.5) < 1e-9
    pk = dmm.peak_info(0)
    assert pk['name'] == 'Peak-1'
    meas = dmm.saved_measurement(0)
    assert meas['name'] == 'Meas-1'
    assert abs(meas['readings']['PRIMARY']['value'] - 3.3) < 1e-9
    print('PASS minmax/peak/measurement')

    cfg = dmm.config()
    assert cfg['Company'] == 'Acme'
    assert cfg['Language'] == 'ENGLISH'
    print('PASS config:', cfg['Model'], cfg['Current meter time'])

    names = dmm.names()
    assert names[0] == 'SAVE1'
    dmm.set_name(0, 'NewName')
    assert dmm.names()[0] == 'NewName'
    print('PASS save names')

    dmm.set_string_property('operator', 'Tester')
    assert dmm.get_string_property('operator') == 'Tester'
    dmm.sync_clock()
    print('PASS property set / clock sync')

    for cmd in ('DS', 'RI', 'RMP'):
        assert dmm.command(cmd) == []
    print('PASS reset commands ack')

    try:
        dmm.command('bogus')
        raise AssertionError('expected DmmCommandError')
    except DmmCommandError as err:
        assert err.status == '1'
    print('PASS error handling')

    qm = dmm.qm()
    assert qm['unit'] == 'VDC'
    print('PASS qm:', qm)

    # regression: ACK arrives before the payload (real meter timing)
    protocol.serial.Serial = SlowAckSerial
    slow = Fluke28x()
    slow.connect('/dev/cu.fake', 0.05)
    for _ in range(5):
        live = slow.qddb()
        assert abs(live['readings']['LIVE']['value'] - 1.2345) < 1e-9
    assert slow.id()['model_number'] == 'FLUKE 289'
    assert slow.command('DS') == []          # bare ACKs still work
    print('PASS split-ACK timing (qddb with delayed payload)')

    print('\nALL PROTOCOL TESTS PASSED')


if __name__ == '__main__':
    main()
