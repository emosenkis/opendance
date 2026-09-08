"""Render the existing SVG as a multi-resolution Windows icon."""

import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


root = Path(__file__).resolve().parents[1]
output = Path(sys.argv[1])
output.parent.mkdir(parents=True, exist_ok=True)
canvas = QImage(256, 256, QImage.Format.Format_ARGB32)
canvas.fill(0)
painter = QPainter(canvas)
QSvgRenderer(str(root / "src/opendance/assets/icon.svg")).render(painter, QRectF(0, 0, 256, 256))
painter.end()
png = output.with_suffix(".png")
canvas.save(str(png))
Image.open(png).save(output, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
png.unlink()
