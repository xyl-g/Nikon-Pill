"""Render the 💊 app icon to build/icon.icns (macOS) and build/icon.ico (Windows).

Apple Color Emoji is a bitmap font, so each target size is rendered at its own
resolution instead of downscaling one master image.
"""
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath

MAC_SIZES = [16, 32, 64, 128, 256, 512, 1024]
WIN_SIZES = [16, 24, 32, 48, 64, 128, 256]
OUT = Path(__file__).resolve().parent / 'build'


def render(size: int) -> QImage:
    """Pill emoji on a macOS-style rounded-square tile."""
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    # Rounded tile with a subtle vertical gradient, like a native app icon.
    tile = QRectF(size * 0.055, size * 0.055, size * 0.89, size * 0.89)
    path = QPainterPath()
    path.addRoundedRect(tile, size * 0.225, size * 0.225)
    from PySide6.QtGui import QLinearGradient
    grad = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    grad.setColorAt(0.0, QColor('#ffffff'))
    grad.setColorAt(1.0, QColor('#f1f2f5'))
    p.fillPath(path, grad)
    p.setPen(QColor('#e2e4ea'))
    p.drawPath(path)

    # Center the emoji, slightly enlarged so it fills the tile.
    # Apple Color Emoji on macOS, Segoe UI Emoji on Windows.
    font = QFont('Apple Color Emoji' if sys.platform == 'darwin' else 'Segoe UI Emoji')
    font.setPixelSize(int(size * 0.70))
    p.setFont(font)
    p.drawText(tile, Qt.AlignCenter, '💊')
    p.end()
    return img


def main():
    app = QGuiApplication(sys.argv)     # fonts need an app instance
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
