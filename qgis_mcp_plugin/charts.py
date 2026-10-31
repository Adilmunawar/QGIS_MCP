# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""Small charts drawn with Qt for the Data Explorer and the reports.

No plotting library is needed: a line or bar chart is painted onto a QImage
and saved as PNG, which QTextBrowser, QLabel and the HTML reports can show.
Painting on a QImage is safe off the GUI thread, but the dock calls these
from ``apply`` anyway.
"""

import hashlib
import os

from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF

from .live_commands import cache_dir

LINE = "#2f7d3f"
FILL = QColor(120, 170, 90, 90)
BAR = "#3b7dd8"
AXIS = QColor(90, 90, 90)


def chart(series, title="", unit="", kind="line", width=720, height=240, path=None,
          colour=None):
    """Draw ``[(label, value), ...]`` and return the PNG path (None if nothing to draw).

    ``kind`` is ``line`` (with a soft fill) or ``bar``. Missing values are
    skipped; labels are strings such as dates and the first, middle and last
    are printed under the axis.
    """
    points = [(str(label), float(value)) for label, value in series
              if value is not None and value == value]
    if len(points) < 2:
        return None
    image = QImage(int(width), int(height), QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    left, right, top, bottom = 58, 14, 26 if title else 12, 30
    lo = min(v for _, v in points)
    hi = max(v for _, v in points)
    if kind == "bar":
        lo = min(lo, 0.0)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    span_x = max(1, len(points) - 1)
    plot_w, plot_h = width - left - right, height - top - bottom

    def xy(i, v):
        return QPointF(left + plot_w * i / span_x, top + plot_h * (1 - (v - lo) / (hi - lo)))

    painter.setPen(QPen(AXIS, 1))
    painter.setFont(QFont("Segoe UI", 8))
    if title:
        painter.drawText(QRectF(left, 2, plot_w, 18), Qt.AlignmentFlag.AlignLeft, title)
    painter.drawLine(left, top, left, height - bottom)
    painter.drawLine(left, height - bottom, width - right, height - bottom)
    for frac in (0.0, 0.5, 1.0):
        value = lo + (hi - lo) * frac
        y = top + plot_h * (1 - frac)
        painter.setPen(QPen(QColor(220, 220, 220), 1, Qt.PenStyle.DashLine))
        painter.drawLine(left, int(y), width - right, int(y))
        painter.setPen(QPen(AXIS, 1))
        painter.drawText(QRectF(0, y - 8, left - 6, 16), Qt.AlignmentFlag.AlignRight, f"{value:.3g}")
    if unit:
        painter.drawText(QRectF(0, top - 22, left - 6, 16), Qt.AlignmentFlag.AlignRight, unit)
    for i in sorted({0, len(points) // 2, len(points) - 1}):
        painter.drawText(QRectF(left + plot_w * i / span_x - 50, height - bottom + 4, 100, 18),
                         Qt.AlignmentFlag.AlignHCenter, points[i][0][:10])
    if kind == "bar":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour or BAR))
        base_y = xy(0, 0.0).y()
        bar_w = max(2.0, plot_w / len(points) * 0.7)
        for i, (_, v) in enumerate(points):
            p = xy(i, v)
            painter.drawRect(QRectF(p.x() - bar_w / 2, min(p.y(), base_y), bar_w, abs(base_y - p.y())))
    else:
        poly = QPolygonF([QPointF(left, height - bottom)] + [xy(i, v) for i, (_, v) in enumerate(points)]
                         + [QPointF(left + plot_w, height - bottom)])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(FILL)
        painter.drawPolygon(poly)
        painter.setPen(QPen(QColor(colour or LINE), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(QPolygonF([xy(i, v) for i, (_, v) in enumerate(points)]))
        painter.setBrush(QColor(colour or LINE))
        for i, (_, v) in enumerate(points):
            painter.drawEllipse(xy(i, v), 2.5, 2.5)
    painter.end()
    if path is None:
        digest = hashlib.sha1(repr((title, kind, points)).encode()).hexdigest()[:10]
        path = os.path.join(cache_dir(), f"chart_{digest}.png")
    if not image.save(path):
        return None
    return path


def img_tag(path, width=700):
    """An HTML image tag for a chart file, for QTextBrowser and the reports."""
    if not path:
        return ""
    from qgis.PyQt.QtCore import QUrl
    return f"<p><img src='{QUrl.fromLocalFile(path).toString()}' width='{int(width)}'></p>"
