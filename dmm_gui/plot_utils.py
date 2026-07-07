"""Shared plotting helpers."""

import math

import pyqtgraph as pg

# Meter base unit -> SI unit for pyqtgraph's automatic prefix scaling
# (axis then reads mV, µA, kΩ, ... as you zoom). Units absent here (dB,
# percent, crest factor) get a plain text label with no prefix scaling.
SI_UNITS = {
    'VDC': 'V', 'VAC': 'V', 'V': 'V', 'VAC_PLUS_DC': 'V',
    'ADC': 'A', 'AAC': 'A', 'A': 'A', 'AAC_PLUS_DC': 'A',
    'OHM': 'Ω', 'SIE': 'S', 'Hz': 'Hz', 'S': 's', 'F': 'F',
    'CEL': '°C', 'FAR': '°F',
}


class ReadingAxis(pg.AxisItem):
    """Left axis that shows at least as many decimal places as the meter's
    own reading, so tick labels don't drop digits the display shows (e.g.
    a "121.43" reading gets "121.43"-precision ticks, not "121.4")."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._resolution = None  # least-significant-digit step, in base SI units

    def set_resolution(self, resolution):
        """Set the meter's display resolution (value of its least-significant
        digit, in base SI units). None restores pyqtgraph's default labels."""
        self._resolution = resolution if resolution and resolution > 0 else None

    def tickStrings(self, values, scale, spacing):
        if self._resolution is None:
            return super().tickStrings(values, scale, spacing)
        # `scale` folds in pyqtgraph's SI-prefix scaling, so the resolution in
        # the *displayed* unit is resolution * scale.
        disp_res = self._resolution * scale
        if disp_res <= 0:
            return super().tickStrings(values, scale, spacing)
        places = max(0, int(round(-math.log10(disp_res))))
        # Never show fewer digits than the tick spacing itself needs.
        if spacing * scale > 0:
            places = max(places, int(math.ceil(-math.log10(spacing * scale))))
        return [f'{v * scale:.{places}f}' for v in values]


def set_axis_unit(plot, unit, resolution=None):
    """Label a PlotWidget's left axis for a meter base unit, with SI prefix
    scaling (mV, µA, ...) when the unit supports it. `resolution` (least-
    significant-digit step in base SI units) forces tick labels to match the
    meter's own decimal precision when the axis is a ReadingAxis."""
    axis = plot.getAxis('left')
    if isinstance(axis, ReadingAxis):
        axis.set_resolution(resolution)
    si = SI_UNITS.get(unit)
    if si is not None:
        axis.enableAutoSIPrefix(True)
        plot.setLabel('left', units=si)
    else:
        axis.enableAutoSIPrefix(False)
        plot.setLabel('left', unit or '', units=None)
