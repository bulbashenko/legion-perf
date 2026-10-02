"""Иконка трея: температура CPU числом на цветном фоне."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap

WARM = 70.0
HOT = 85.0

GREEN = QColor("#2e7d32")
YELLOW = QColor("#f9a825")
RED = QColor("#c62828")
GREY = QColor("#616161")
COOLING = QColor("#4fc3f7")


def temp_color(temp: float | None) -> QColor:
    if temp is None:
        return GREY
    if temp < WARM:
        return GREEN
    return YELLOW if temp < HOT else RED


def temp_icon(temp: float | None, extreme_cooling: bool = False, size: int = 64) -> QIcon:
    """Extreme Cooling отмечается голубой рамкой."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    rect = QRectF(0, 0, size, size)
    border = size * 0.09
    p.setBrush(temp_color(temp))
    if extreme_cooling:
        p.setPen(QPen(COOLING, border))
        rect = rect.adjusted(border / 2, border / 2, -border / 2, -border / 2)
    else:
        p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(rect, size * 0.2, size * 0.2)

    text = "--" if temp is None else str(min(round(temp), 99))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.62))
    p.setFont(font)
    p.setPen(QColor("black") if temp_color(temp) == YELLOW else QColor("white"))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return QIcon(pixmap)
