# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""Data Explorer Fields tab: per field vigour, change, status, time series and
a report for any polygon layer of fields, parcels or blocks.

Mixed into :class:`ExplorerDock`. The layer is read on the GUI thread
(``fields_subset``), scenes and weather are fetched on a worker
(``fields_fetch``), and the result layer is built on the GUI thread
(``fields_apply``).
"""

import html

from qgis.core import QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices, QPixmap
from qgis.PyQt.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QTextBrowser, QVBoxLayout, QWidget,
)

from . import charts
from .connectors import indices
from .explorer_toolbox import _table


class FieldTabsMixin:
    """The Fields tab."""

    def _build_fields_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        intro = QLabel("Pick a polygon layer of fields or parcels. Selected features are analysed when there "
                       "is a selection, otherwise the features inside the area above (capped). The source "
                       "layer is not changed; a new styled layer carries the numbers.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        box = QGroupBox("Fields")
        form = QFormLayout(box)
        self.fields_layer_combo = QgsMapLayerComboBox()
        self.fields_layer_combo.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)
        form.addRow("Layer", self.fields_layer_combo)
        row = QHBoxLayout()
        self.fields_index = QComboBox()
        for name in ("NDVI", "EVI", "SAVI", "NDMI", "NDWI"):
            if name in indices.INDICES:
                self.fields_index.addItem(name, name)
        self.fields_days = QSpinBox()
        self.fields_days.setRange(7, 180)
        self.fields_days.setValue(45)
        self.fields_days.setPrefix("newest within ")
        self.fields_days.setSuffix(" days")
        self.fields_compare = QSpinBox()
        self.fields_compare.setRange(7, 365)
        self.fields_compare.setValue(30)
        self.fields_compare.setPrefix("compare ")
        self.fields_compare.setSuffix(" days earlier")
        self.fields_limit = QSpinBox()
        self.fields_limit.setRange(1, 2000)
        self.fields_limit.setValue(300)
        self.fields_limit.setPrefix("up to ")
        self.fields_limit.setSuffix(" fields")
        for w in (self.fields_index, self.fields_days, self.fields_compare, self.fields_limit):
            row.addWidget(w)
        form.addRow(row)
        buttons = QHBoxLayout()
        analyse = QPushButton("Analyse fields")
        analyse.setToolTip("Vigour now, change since the earlier scene, status per field, ranked list")
        analyse.clicked.connect(self._fields_analyse)
        series = QPushButton("Time series of selected field")
        series.setToolTip("Select exactly one field first")
        series.clicked.connect(self._fields_series)
        report = QPushButton("Field report (PDF)")
        report.clicked.connect(self._fields_report)
        for b in (analyse, series, report):
            buttons.addWidget(b)
        form.addRow(buttons)
        layout.addWidget(box)
        self.fields_view = QTextBrowser()
        self.fields_view.setOpenExternalLinks(True)
        layout.addWidget(self.fields_view, 1)
        self.fields_chart = QLabel("")
        layout.addWidget(self.fields_chart)
        return widget

    def _fields_subset_or_log(self):
        layer = self.fields_layer_combo.currentLayer()
        if layer is None:
            self.log("Pick a polygon layer of fields first", "error")
            return None
        aoi = {"bbox": self._aoi.bbox} if self._aoi is not None else None
        if layer.selectedFeatureCount() == 0 and aoi is None:
            self.log("Select some fields, or set an area of interest to pick the fields inside it", "error")
            return None
        try:
            subset = self._commands.fields_subset(layer.id(), None, aoi, self.fields_limit.value())
        except Exception as exc:
            self.log(str(exc), "error")
            return None
        if subset["truncated"]:
            self.log(f"{subset['candidates']} fields found, analysing the first {len(subset['fields'])}; "
                     "select fewer or raise the cap")
        return subset

    def _render_fields_result(self, result):
        key = result["index"].lower()
        esc = html.escape
        cols = ["label", "acres", f"{key}_now", f"{key}_prev", "change", "status"]
        wx = result.get("weather") or {}
        parts = [f"<h3>{result['fields']} fields, {result['acres']} acres: {esc(result['index'])} "
                 f"{esc((result['now']['datetime'] or '')[:10])}"
                 + (f" vs {esc((result['previous']['datetime'] or '')[:10])}" if result.get("previous") else "") + "</h3>",
                 f"<p>Mean {esc(result['index'])} <b>{result['mean_now']}</b>"
                 + (f", mean change <b>{result['mean_change']:+.3f}</b>" if result.get("mean_change") is not None else "")
                 + f". Layer <b>{esc(result['layer_name'])}</b> added, styled by vigour."
                 + (f" {result['duplicates_skipped']} duplicate polygons were skipped."
                    if result.get("duplicates_skipped") else "") + "</p>",
                 _table([dict(status=k, **v) for k, v in result["by_status"].items()], ["status", "fields", "acres"])]
        if wx.get("history"):
            h = wx["history"]
            parts.append(f"<p>Last {h.get('days')} days: rain {h.get('rain_mm')} mm, ET0 {h.get('et0_mm')} mm, "
                         f"mean daily maximum {h.get('tmax_mean')} °C"
                         + (f"; next 7 days rain {wx['forecast'].get('rain_mm')} mm" if wx.get("forecast") else "") + "</p>")
        parts.append(f"<h4>Needing attention: {result['attention_count']}"
                     + (f" (worst {len(result['attention'])} listed; all are in the layer)"
                        if result["attention_count"] > len(result["attention"]) else "")
                     + "</h4>" + (_table(result["attention"], cols) if result["attention"]
                                                     else "<p>None: every field with data is healthy or greening.</p>"))
        if result.get("errors"):
            parts.append(f"<p><small>Not available: {esc(str(result['errors']))}</small></p>")
        parts.append(f"<p><small>{esc(result['interpretation'])}</small></p>")
        self.fields_view.setHtml("".join(parts))
        chart = charts.chart([(k, v["acres"]) for k, v in result["by_status"].items()], "Acres by status", "acres",
                             "bar", max(320, self.tabs.width() - 48), 170)
        self.fields_chart.setPixmap(QPixmap(chart) if chart else QPixmap())

    def _fields_analyse(self):
        subset = self._fields_subset_or_log()
        if subset is None:
            return
        commands = self._commands
        index, days, compare = self.fields_index.currentData(), self.fields_days.value(), self.fields_compare.value()

        def work(task):
            return commands.fields_fetch(subset, index, days, compare)

        def apply(fetched):
            result = commands.fields_apply(fetched)
            self._last_fields_result = result
            self._render_fields_result(result)
            self.log(f"{result['fields']} fields analysed: {result['attention_count']} need attention", "success")

        self._run(f"Analysing {len(subset['fields'])} fields", work, apply)

    def _fields_series(self):
        layer = self.fields_layer_combo.currentLayer()
        if layer is None:
            self.log("Pick a polygon layer of fields first", "error")
            return
        if layer.selectedFeatureCount() != 1:
            self.log("Select exactly one field for its time series", "error")
            return
        fid = layer.selectedFeatureIds()[0]
        layer_id = layer.id()
        commands = self._commands
        index = self.fields_index.currentData()

        def work(task):
            return commands.field_time_series(layer_id, fid, index, 6)

        def apply(series):
            rows = [{"date": (r.get("datetime") or "")[:10], "mean": r.get("mean"), "cloud %": r.get("cloud_cover")}
                    for r in series["series"] if "mean" in r]
            self.fields_view.setHtml(
                f"<h3>{html.escape(series['index'])} of {html.escape(series['field'])}, last 6 months</h3>"
                + _table(rows, ["date", "mean", "cloud %"])
                + (f"<p>Change first to last <b>{series['change_first_to_last']:+.3f}</b></p>"
                   if series.get("change_first_to_last") is not None else "")
                + f"<p><small>{html.escape(series.get('interpretation') or '')}</small></p>")
            self.fields_chart.setPixmap(QPixmap(series["chart_path"]) if series.get("chart_path") else QPixmap())
            self.log(f"Time series of {series['field']}: {len(rows)} scenes", "success")

        self._run("Field time series", work, apply)

    def _fields_report(self):
        result = getattr(self, "_last_fields_result", None)
        if result is None:
            self.log("Run Analyse fields first; the report uses that result", "error")
            return
        import os
        from .live_commands import _safe_name, cache_dir
        pdf = os.path.join(cache_dir(), f"field_report_{_safe_name(result['layer_name'])[:30]}.pdf")
        try:
            written = self._commands.write_field_report(result, pdf)
        except Exception as exc:
            self.log(str(exc), "error")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(written["path"]))
        self.log(f"Field report written to {written['path']}", "success")
