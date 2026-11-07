"""Render the Nikon Pill app icon (build/icon.icns + build/icon.ico).

The pill is hand-drawn with QPainter instead of relying on a platform emoji
font — Apple Color Emoji and Segoe UI Emoji use different palettes, which made
the Windows build come out pink. Hand-drawing keeps every platform identical.
"""
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath,
)

MAC_SIZES = [16, 32, 64, 128, 256, 512, 1024]
OUT = Path(__file__).resolve().parent / 'build'


def render(size: int) -> QImage:
    """Pill capsule on a macOS-style rounded-square tile."""
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)

    # Rounded tile with a subtle vertical gradient, like a native app icon.
    tile = QRectF(size * 0.055, size * 0.055, size * 0.89, size * 0.89)
    tile_path = QPainterPath()
    tile_path.addRoundedRect(tile, size * 0.225, size * 0.225)
    grad = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    grad.setColorAt(0.0, QColor('#ffffff'))
    grad.setColorAt(1.0, QColor('#eef0f4'))
    p.fillPath(tile_path, grad)
    p.setPen(QColor('#e2e4ea'))
    p.drawPath(tile_path)

    # Capsule along the 45° diagonal: red upper-left half, yellow lower-right.
    p.save()
    p.translate(tile.center())
    p.rotate(45)
    length, thick = size * 0.66, size * 0.34
    cap = QPainterPath()
    cap.addRoundedRect(QRectF(-length / 2, -thick / 2, length, thick),
                       thick / 2, thick / 2)
    p.setClipPath(cap)
    red = QLinearGradient(0, -thick / 2, 0, thick / 2)
    red.setColorAt(0.0, QColor('#f2665c'))
    red.setColorAt(0.55, QColor('#e23b34'))
    red.setColorAt(1.0, QColor('#c9281f'))
    p.fillRect(QRectF(-length / 2, -thick / 2, length / 2, thick), red)
    yellow = QLinearGradient(0, -thick / 2, 0, thick / 2)
    yellow.setColorAt(0.0, QColor('#ffe985'))
    yellow.setColorAt(0.55, QColor('#ffd541'))
    yellow.setColorAt(1.0, QColor('#eab000'))
    p.fillRect(QRectF(0, -thick / 2, length / 2, thick), yellow)
    p.setClipping(False)
    p.setPen(QColor(0, 0, 0, 45))
    p.drawPath(cap)

    # Glossy reflection streak on the red half.
    hl = QPainterPath()
    hl.addEllipse(QRectF(-length * 0.38, -thick * 0.36, thick * 0.62, thick * 0.26))
    p.fillPath(hl, QColor(255, 255, 255, 150))
    p.restore()

    p.end()
    return img


def main():
    app = QGuiApplication(sys.argv)     # Qt requires an app instance
    OUT.mkdir(exist_ok=True)

    if sys.platform == 'darwin':
        # macOS .icns via iconutil (needs an .iconset dir with exact names).
        iconset = OUT / 'icon.iconset'
        subprocess.run(['rm', '-rf', str(iconset)], check=True)
        iconset.mkdir()
        for s in MAC_SIZES:
            img = render(s)
            img.save(str(iconset / f'icon_{s}x{s}.png'), 'PNG')
            if s <= 512:
                img.save(str(iconset / f'icon_{s // 2}x{s // 2}@2x.png'), 'PNG')
        subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(OUT / 'icon.icns')],
                       check=True)

    # Windows .ico — the largest frame carries the artwork; Windows scales it
    # down for small sizes in the taskbar and Explorer.
    render(256).save(str(OUT / 'icon.ico'), 'ICO')

    print('wrote', *(p for p in (OUT / 'icon.icns', OUT / 'icon.ico') if p.exists()))


if __name__ == '__main__':
    main()
