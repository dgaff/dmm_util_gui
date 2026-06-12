#!/usr/bin/env python3
"""
fluke289_decode.py — Decode the Fluke ir3000FC live-measurement record (b698290f).

The 16-byte record is TWO 8-byte frames with identical layout:
    bytes 0..7   = primary reading (the main display)
    bytes 8..15  = secondary reading (the meter's small secondary display; e.g.
                   the AC volts of the signal while the primary shows Hz/dB).
A frame is "present" when its function code is non-zero.

Frame layout (8 bytes):
  byte 0..2  magnitude, 24-bit little-endian unsigned integer (the raw digits)
  byte 3     flags:
               0x80  sign      (set => negative)
               0x70  prefix    metric-prefix code = (b3 & 0x70) >> 4
                               1=G 2=M 3=k  0=none  4=m 5=u 6=n 7=p
                               (2,3,4,5,6 confirmed; 1 and 7 inferred)
               0x0e  decimals  decimal places = (b3 & 0x0e) >> 1
               0x01  unused (always 0 observed)
  byte 4     function / quantity code (see FUNCTION)
  byte 5     range index (informational; the prefix already comes from byte 3)
  byte 6     reserved (always 0 observed)
  byte 7     status flags:
               0x10  frequency mode active
               0x08  "high" state: continuity closed/beeping, or Hz rising edge
               0x04  "low"  state: continuity open/OL,        or Hz falling edge

Overload (OL) is signaled by magnitude == 0x9FFFFF (bytes FF FF 9F).

REL mode does NOT change the record — the meter subtracts the reference in its
display layer, so this decoder always reports the absolute measurement.

Displayed value = (sign) * magnitude / 10^decimals, shown with unit
prefix(b3)+base(b4). The SI/base value is value * prefix_factor.

Derivation confirmed against two labeled captures from a Fluke 289
(serial 71800125, FW 01.00.01) — every clean sample matches exactly.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Optional

OVERLOAD_RAW = 0x9FFFFF

# byte 4 -> (base unit, AC/DC or None)
FUNCTION = {
    1:  ("V", "AC"),
    2:  ("V", "DC"),     # diode test also reports here (range index 46)
    3:  ("A", "AC"),
    4:  ("A", "DC"),
    5:  ("Hz", None),    # frequency
    7:  ("\u00b0C", None),  # temperature, Celsius
    8:  ("\u00b0F", None),  # temperature, Fahrenheit
    11: ("\u03a9", None),   # ohms (resistance / continuity)
    12: ("S", None),        # siemens (conductance, typically nS)
    13: ("%", None),        # percentage
    14: ("s", None),        # seconds (of pulse width - will have a frequency prefix)
    15: ("F", None),        # farads (capacitance)
    17: ("dBm", None),
    31: ("dBV", None),
    32: ("Crest Factor", None),
}

# (b3 & 0x70) >> 4  ->  (symbol, multiplier-to-SI). The codes form a clean SI
# ladder: 1..3 = G,M,k (+9,+6,+3); 0 = base; 4..7 = m,µ,n,p (-3,-6,-9,-12).
# Confirmed by capture: 2(M),3(k),4(m),5(µ),6(n). Inferred from the ladder and
# not yet observed: 1(G) (289 maxes at 500 MΩ) and 7(p).
PREFIX = {
    0: ("", 1.0),
    1: ("G", 1e9),          # inferred, unobserved
    2: ("M", 1e6),          # confirmed (MΩ)
    3: ("k", 1e3),          # confirmed (kHz)
    4: ("m", 1e-3),         # confirmed
    5: ("\u00b5", 1e-6),    # confirmed
    6: ("n", 1e-9),         # confirmed
    7: ("p", 1e-12),        # inferred, unobserved
}

# byte 5 -> human range label. NOT required for decoding (prefix comes from
# byte 3) and its meaning is context-dependent on the function. Informational.
RANGE_HINT = {
    1:  "mV AC",   2:  "V AC / range-2",  7:  "V AC (coarse)",
    11: "mV DC",   12: "V DC",
    13: "mA AC",   14: "A AC",            50: "uA AC",
    19: "uA DC",   20: "mA DC",           21: "A DC",
    39: "ohm (continuity)", 40: "ohm",    42: "ohm (low)",
    41: "nS (conductance)",
    45: "uF (capacitance)",
    46: "V (diode)",
}

# byte 7 status bits we understand
STATUS_FREQ_MODE = 0x10
STATUS_HIGH      = 0x08   # continuity closed/beep, or Hz rising edge
STATUS_LOW       = 0x04   # continuity open/OL,     or Hz falling edge


@dataclass
class Reading:
    raw: int             # magnitude integer (bytes 0..2 of the frame)
    negative: bool
    overload: bool
    decimals: int
    function_code: int
    range_code: int
    base_unit: str       # "V","A","Ω","S","F","Hz","dBm","dBV", or "?"
    ac_dc: Optional[str] # "AC","DC", or None
    prefix: str          # symbol: "", "k","m","µ","n",...
    prefix_factor: float
    status: int          # raw byte 7
    status_flags: list   # decoded status strings we recognize
    value: Optional[float]     # displayed value (None on overload)
    value_si: Optional[float]  # value * prefix_factor (None on overload)
    unit: str            # e.g. "mV","µA","kHz","Ω"
    display: str         # e.g. "99.61 mA DC", "1.0000 kHz", "OL Ω"
    present: bool        # False for an empty/absent frame (function_code == 0)
    secondary: Optional["Reading"] = field(default=None)

    def as_dict(self):
        return asdict(self)


def _decode_status(status: int) -> list:
    flags = []
    if status & STATUS_FREQ_MODE:
        flags.append("freq")
    if status & STATUS_HIGH:
        flags.append("high/beep/rising")
    if status & STATUS_LOW:
        flags.append("low/open/falling")
    return flags


def decode_frame(frame: bytes) -> Reading:
    """Decode one 8-byte frame (tolerates >=6 bytes; status defaults to 0)."""
    if len(frame) < 6:
        raise ValueError(f"frame too short: {len(frame)} bytes")

    raw = int.from_bytes(frame[0:3], "little")
    flags = frame[3]
    fcode = frame[4]
    rcode = frame[5]
    status = frame[7] if len(frame) >= 8 else 0

    negative = bool(flags & 0x80)
    overload = (raw == OVERLOAD_RAW)
    decimals = (flags & 0x0e) >> 1
    pcode = (flags & 0x70) >> 4
    psym, pfac = PREFIX.get(pcode, ("?", 1.0))

    base_unit, ac_dc = FUNCTION.get(fcode, ("?", None))
    present = (fcode != 0)
    unit = psym + base_unit

    if not present:
        value = value_si = None
        display = "(none)"
    elif overload:
        value = value_si = None
        display = f"OL {unit}" + (f" {ac_dc}" if ac_dc else "")
    else:
        magnitude = raw / (10 ** decimals)
        value = (-magnitude if negative else magnitude)
        value_si = value * pfac
        display = f"{value:.{decimals}f} {unit}" + (f" {ac_dc}" if ac_dc else "")

    return Reading(
        raw=raw, negative=negative, overload=overload, decimals=decimals,
        function_code=fcode, range_code=rcode, base_unit=base_unit, ac_dc=ac_dc,
        prefix=psym, prefix_factor=pfac, status=status,
        status_flags=_decode_status(status),
        value=value, value_si=value_si, unit=unit,
        display=display.strip(), present=present,
    )


def decode_record(data: bytes) -> Reading:
    """Decode a full b698290f record. Returns the primary Reading with its
    .secondary attribute set to the secondary frame's Reading when present."""
    if len(data) < 6:
        raise ValueError(f"record too short: {len(data)} bytes")
    primary = decode_frame(data[0:8])
    if len(data) >= 14:
        sec = decode_frame(data[8:16])
        primary.secondary = sec if sec.present else None
    return primary


# ---------------------------------------------------------------------------
# Self-test: the labeled captures this format was derived from. Labels are what
# the operator eyeballed off a jittering display, so we assert on the decoded
# numeric string + unit, not the raw label.
_SELFTEST = [
    # (hex_record, primary_value_or_OL, primary_unit, secondary_value_or_None)
    ("14270008020c00000000000000000000", "1.0004",  "V",  None),
    ("8b270006020c00000000000000000000", "10.123",  "V",  None),
    ("6ac30088020c00000000000000000000", "-5.0026", "V",  None),
    ("a9370006010200000000000000000000", "14.249",  "V",  None),
    ("2f000046020b00000000000000000000", "0.047",   "mV", None),
    ("00000046010100000000000000000000", "0.000",   "mV", None),
    ("ffff9f640c2900000000000000000000", "OL",      "nS", None),
    ("22170008022e00100000000000000000", "0.5922",  "V",  None),
    ("f30100520f2d00000000000000000000", "49.9",    "\u00b5F", None),
    ("be040008041500000000000000000000", "0.1214",  "A",  None),
    ("e9260044041400000000000000000000", "99.61",   "mA", None),
    ("86280054041300000000000000000000", "103.74",  "\u00b5A", None),
    ("ef070008030e00000000000000000000", "0.2031",  "A",  None),
    ("36440044030d00000000000000000000", "174.62",  "mA", None),
    ("ffff9f52033200000000000000000000", "OL",      "\u00b5A", None),
    ("370400060b2a00000000000000000000", "1.079",   "\u03a9",  None),
    ("b42800280b2800000000000000000000", "1.0420",  "M\u03a9", None),  # Mega prefix (code 2)
    ("09030002082200000000000000000000", "77.7",    "\u00b0F", None),  # temperature F
    ("fd000002072200000000000000000000", "25.3",    "\u00b0C", None),  # temperature C
    # dataset 2: Hz / dB, with a populated secondary frame (AC volts of signal)
    ("60ea000605020018894e000801020000", "60.000",  "Hz",  "2.0105"),
    ("1027003805020018854e000801020000", "1.0000",  "kHz", "2.0101"),
    ("1027003805020014864e000801020000", "1.0000",  "kHz", "2.0102"),
    ("3b03000411020000864e000801020000", "8.27",    "dBm", "2.0102"),
    ("5e0200041f020000844e000801020000", "6.06",    "dBV", "2.0100"),
]


def _run_selftest() -> int:
    ok = fail = 0
    for hexrec, pv, pu, sv in _SELFTEST:
        r = decode_record(bytes.fromhex(hexrec))
        got = "OL" if r.overload else f"{r.value:.{r.decimals}f}"
        sec_got = (None if r.secondary is None
                   else f"{r.secondary.value:.{r.secondary.decimals}f}")
        good = (got == pv) and (r.unit == pu) and (sec_got == sv)
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        sec_str = "" if r.secondary is None else f"  || 2nd: {r.secondary.display}"
        print(f"  [{'ok ' if good else 'FAIL'}] {hexrec[:12]}...  "
              f"expect {pv:>8} {pu:<3}  got {r.display}{sec_str}")
    print(f"\n{ok} passed, {fail} failed")
    return fail


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        rec = bytes.fromhex(sys.argv[1].replace(" ", ""))
        r = decode_record(rec)
        print(r.display + (f"   || 2nd: {r.secondary.display}" if r.secondary else ""))
    else:
        sys.exit(_run_selftest())
