"""Generate the app icon (.icns) for the DMM Utility bundle.

Renders a meter-style icon with Qt at all required sizes and packs them
with iconutil. Run:  QT_QPA_PLATFORM=offscreen venv/bin/python packaging/gen_icon.py
"""

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QPolygonF,
)

HERE = Path(__file__).resolve().parent


def render(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 1024.0  # design coordinates are for a 1024px canvas

    # macOS-style rounded square, full bleed with a small margin
    margin = 100 * s
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    radius = 185 * s
    bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
    bg.setColorAt(0.0, QColor('#3b4252'))
    bg.setColorAt(1.0, QColor('#1c2128'))
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.fillPath(path, bg)
    p.setClipPath(path)

    # LCD-ish readout band
    lcd = QRectF(rect.left() + 90 * s, rect.top() + 110 * s,
                 rect.width() - 180 * s, 250 * s)
    lcd_path = QPainterPath()
    lcd_path.addRoundedRect(lcd, 40 * s, 40 * s)
    p.fillPath(lcd_path, QColor('#dde8d8'))
    p.setPen(QColor('#1c2128'))
    font = QFont('Menlo')
    font.setPixelSize(int(125 * s))
    font.setBold(True)
    p.setFont(font)
    p.drawText(lcd, Qt.AlignCenter, '1.234 V')

    # trend line
    pts = [(0.00, 0.82), (0.18, 0.80), (0.32, 0.84), (0.45, 0.62),
           (0.58, 0.30), (0.66, 0.45), (0.78, 0.22), (0.90, 0.50), (1.00, 0.46)]
    poly = QPolygonF([
        QPointF(rect.left() + 90 * s + x * (rect.width() - 180 * s),
                rect.top() + 430 * s + y * 290 * s)
        for x, y in pts])
    p.setPen(QPen(QColor('#4da6ff'), 34 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPolyline(poly)
    p.setBrush(QColor('#ff5c57'))
    p.setPen(Qt.NoPen)
    p.drawEllipse(poly.last(), 26 * s, 26 * s)

    p.end()
    return pm


def main():
    app = QGuiApplication(sys.argv)
    iconset = HERE / 'icon.iconset'
    iconset.mkdir(exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        render(base).save(str(iconset / f'icon_{base}x{base}.png'))
        render(base * 2).save(str(iconset / f'icon_{base}x{base}@2x.png'))
    subprocess.run(['iconutil', '-c', 'icns', str(iconset),
                    '-o', str(HERE / 'icon.icns')], check=True)
    print('wrote', HERE / 'icon.icns')


if __name__ == '__main__':
    main()
