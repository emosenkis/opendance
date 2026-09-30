"""Render the existing SVG as a native Windows or macOS icon."""

import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


root = Path(__file__).resolve().parents[1]
output = Path(sys.argv[1])
output.parent.mkdir(parents=True, exist_ok=True)
size = 1024 if output.suffix.casefold() == ".icns" else 256
canvas = QImage(size, size, QImage.Format.Format_ARGB32)
canvas.fill(0)
painter = QPainter(canvas)
QSvgRenderer(str(root / "src/opendance/assets/icon.svg")).render(painter, QRectF(0, 0, size, size))
painter.end()
png = output.with_suffix(".png")
canvas.save(str(png))
if output.suffix.casefold() == ".icns":
    Image.open(png).save(output, format="ICNS")
else:
    Image.open(png).save(output, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
png.unlink()
