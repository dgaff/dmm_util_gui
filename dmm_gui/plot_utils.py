"""Shared plotting helpers."""

# Meter base unit -> SI unit for pyqtgraph's automatic prefix scaling
# (axis then reads mV, µA, kΩ, ... as you zoom). Units absent here (dB,
# percent, crest factor) get a plain text label with no prefix scaling.
SI_UNITS = {
    'VDC': 'V', 'VAC': 'V', 'V': 'V', 'VAC_PLUS_DC': 'V',
    'ADC': 'A', 'AAC': 'A', 'A': 'A', 'AAC_PLUS_DC': 'A',
    'OHM': 'Ω', 'SIE': 'S', 'Hz': 'Hz', 'S': 's', 'F': 'F',
    'CEL': '°C', 'FAR': '°F',
}


def set_axis_unit(plot, unit):
    """Label a PlotWidget's left axis for a meter base unit, with SI prefix
    scaling (mV, µA, ...) when the unit supports it."""
    axis = plot.getAxis('left')
    si = SI_UNITS.get(unit)
    if si is not None:
        axis.enableAutoSIPrefix(True)
        plot.setLabel('left', units=si)
    else:
        axis.enableAutoSIPrefix(False)
        plot.setLabel('left', unit or '', units=None)
