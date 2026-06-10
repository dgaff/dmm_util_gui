"""Catalog of known Fluke 28x remote commands, used by the Console view.

'documented' commands come from the official Fluke 289/287 Remote Interface
Specification. The rest are reverse-engineered commands used by FlukeView
Forms (and the fluke_28x_dmm_util CLI); they work but are unsupported by
Fluke. 'binary' indicates the response payload is binary rather than ASCII.
"""

COMMANDS = [
    # --- documented in Fluke289_remote_spec28X.pdf ---
    {'template': 'ID', 'documented': True, 'binary': False,
     'summary': 'Identification',
     'detail': 'Returns model, software version and serial number.\n'
               'Example response: FLUKE 289,V1.00,95081087'},
    {'template': 'QM', 'documented': True, 'binary': False,
     'summary': 'Query primary measurement',
     'detail': 'Returns the primary display value as\n'
               'READING_VALUE, UNIT, STATE, ATTRIBUTE.\n'
               'Overload/invalid readings return 9.99999999e+37.'},
    {'template': 'QDDA', 'documented': True, 'binary': False,
     'summary': 'Query displayed data (ASCII)',
     'detail': 'Returns everything on the LCD (except the bargraph) as one\n'
               'long ASCII record: functions, range, modes and all readings.'},
    {'template': 'DS', 'documented': True, 'binary': False,
     'summary': 'Default setup',
     'detail': 'Resets Hz trigger edge, pulse width / duty cycle polarity,\n'
               'and continuity beeper settings to defaults.'},
    {'template': 'RI', 'documented': True, 'binary': False,
     'summary': 'Reset instrument',
     'detail': 'Resets ALL instrument settings to factory settings\n'
               '(except calibration constants). Same as Reset Meter\n'
               'on the front panel Setup menu.'},
    {'template': 'RMP', 'documented': True, 'binary': False,
     'summary': 'Reset meter properties',
     'detail': 'Resets meter properties (operator, company, ...) to factory\n'
               'defaults. Same as Reset Setup on the front panel.'},

    # --- reverse engineered (used by FlukeView Forms) ---
    {'template': 'qddb', 'documented': False, 'binary': True,
     'summary': 'Query displayed data (binary)',
     'detail': 'Binary form of QDDA: functions, range, mode and all current\n'
               'readings with timestamps. Used for live monitoring.'},
    {'template': 'qsls', 'documented': False, 'binary': False,
     'summary': 'Query saved list summary',
     'detail': 'Returns the number of stored recordings, min/max sessions,\n'
               'peak sessions and single measurements.'},
    {'template': 'qrsi <index>', 'documented': False, 'binary': True,
     'summary': 'Query recording session info',
     'detail': 'Header for stored recording <index> (0-based): name, start/end\n'
               'time, sample interval, number of samples, function and units.'},
    {'template': 'qsrr <reading_index>,<sample>', 'documented': False, 'binary': True,
     'summary': 'Query recording sample',
     'detail': 'One interval record of a recording: start/end time and\n'
               'primary/min/max/avg readings. <reading_index> comes from qrsi.'},
    {'template': 'qmmsi <index>', 'documented': False, 'binary': True,
     'summary': 'Query min/max session info',
     'detail': 'Stored MIN MAX AVG session <index> (0-based) with primary,\n'
               'minimum, maximum and average readings.'},
    {'template': 'qpsi <index>', 'documented': False, 'binary': True,
     'summary': 'Query peak session info',
     'detail': 'Stored Peak session <index> (0-based) with primary,\n'
               'minimum, maximum and average readings.'},
    {'template': 'qsmr <index>', 'documented': False, 'binary': True,
     'summary': 'Query saved measurement',
     'detail': 'Single saved measurement <index> (0-based): name, value,\n'
               'unit and timestamp.'},
    {'template': 'qemap <map>', 'documented': False, 'binary': False,
     'summary': 'Query enum map',
     'detail': 'Returns the id->name table used to decode binary fields.\n'
               'Maps: primfunction, secfunction, autorange, unit, bolt, mode,\n'
               'readingid, state, attribute, recordtype, isstableflag,\n'
               'transientstate.'},
    {'template': 'csd <category>', 'documented': False, 'binary': False,
     'summary': 'Clear saved data (delete!)',
     'detail': 'Deletes a whole category of stored data from the meter.\n'
               'Categories: RECORDED (recordings), MIN_MAX, PEAK,\n'
               'MEASUREMENT (saved measurements), ALL (everything).\n'
               'There is no per-item delete; this cannot be undone.'},
    {'template': 'qsavname <slot>', 'documented': False, 'binary': False,
     'summary': 'Query save-name slot',
     'detail': 'Returns the name stored in slot <slot> (0-7). These names are\n'
               'offered on the meter when saving data.'},
    {'template': 'savname <slot>,"<name>"', 'documented': False, 'binary': False,
     'summary': 'Set save-name slot',
     'detail': 'Stores <name> in save-name slot <slot> (0-7).'},
    {'template': 'qmp <property>', 'documented': False, 'binary': False,
     'summary': 'Query meter property',
     'detail': 'Reads a meter property. Known properties: clock, aheventTh,\n'
               'lang, dateFmt, timeFmt, digits, beeper, tempOS, numFmt,\n'
               'ablto, apoffto, recEventTh.'},
    {'template': 'mp <property>,<value>', 'documented': False, 'binary': False,
     'summary': 'Set meter property',
     'detail': 'Writes a meter property, e.g. mp clock,1700000000 sets the\n'
               'meter clock (seconds since 1970).'},
    {'template': 'qmpq <property>', 'documented': False, 'binary': False,
     'summary': 'Query quoted (string) property',
     'detail': "Reads a string property: company, contact, operator, site."},
    {'template': "mpq <property>,'<value>'", 'documented': False, 'binary': False,
     'summary': 'Set quoted (string) property',
     'detail': "Writes a string property, e.g. mpq operator,'Doug'.\n"
               'Properties: company, contact, operator, site.'},
]


def tooltip(cmd):
    tag = 'Documented' if cmd['documented'] else 'Reverse engineered (unsupported by Fluke)'
    binary = ' Binary response.' if cmd['binary'] else ''
    return f"{cmd['summary']}\n\n{cmd['detail']}\n\n[{tag}.{binary}]"
