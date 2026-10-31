# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""Data Explorer tabs for the toolbox commands: land and soil, water,
boundaries and species, sensors and alerts, and the analysis workflows.

Mixed into :class:`ExplorerDock`. Each action follows the dock's rule: the
``work`` closure runs on a worker thread and touches only connectors and
GDAL; ``apply`` runs on the GUI thread and creates layers.
"""

import datetime as _dt
import hashlib
import html
import json
import os

from qgis.core import QgsMapLayerProxyModel, QgsProject, QgsRasterLayer
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import QDate, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTextBrowser, QVBoxLayout, QWidget,
)

from .connectors import (boundaries, disasters, indices, landcover, sensors,
                         soil, species, weather)
from .live_commands import _memory_layer_from_features, _safe_name, cache_dir
from .toolbox_commands import _paletted_style


def _table(rows, columns):
    esc = lambda v: html.escape("" if v is None else str(v))  # noqa: E731
    out = "<table cellpadding='3'><tr>" + "".join(f"<th align='left'>{esc(c)}</th>" for c in columns) + "</tr>"
    for r in rows:
        out += "<tr>" + "".join(f"<td>{esc(r.get(c))}</td>" for c in columns) + "</tr>"
    return out + "</table>"


class ToolboxTabsMixin:
    """Extra tabs for :class:`ExplorerDock`."""

    def _build_toolbox_tabs(self):
        self.tabs.addTab(self._build_land_tab(), "Land and Soil")
        self.tabs.addTab(self._build_water_tab(), "Water")
        self.tabs.addTab(self._build_boundaries_tab(), "Boundaries and Species")
        self.tabs.addTab(self._build_analysis_tab(), "Analysis")

    # ------------------------------------------------------------------
    # land and soil
    # ------------------------------------------------------------------
    def _build_land_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        box = QGroupBox("Global datasets for the area")
        form = QFormLayout(box)
        self.dataset_combo = QComboBox()
        for key, spec in sorted(landcover.DATASETS.items(), key=lambda kv: (kv[1].get("category", ""), kv[1]["name"])):
            self.dataset_combo.addItem(f"{spec.get('category', 'other').title()}: {spec['name']}", key)
        form.addRow("Dataset", self.dataset_combo)
        row = QHBoxLayout()
        add = QPushButton("Add layer")
        add.clicked.connect(self._add_dataset)
        summarise = QPushButton("Summarise only")
        summarise.clicked.connect(self._summarise_dataset)
        population = QPushButton("Population in area")
        population.setToolTip("WorldPop total for 2020 and the sum of Meta's 30 m grid, with an age pyramid")
        population.clicked.connect(self._population)
        row.addWidget(add)
        row.addWidget(summarise)
        row.addWidget(population)
        form.addRow(row)
        layout.addWidget(box)

        soil_box = QGroupBox("Soil (SoilGrids)")
        sform = QFormLayout(soil_box)
        self.soil_depth = QComboBox()
        for d in soil.DEPTHS:
            self.soil_depth.addItem(d, d)
        sform.addRow("Depth", self.soil_depth)
        srow = QHBoxLayout()
        query = QPushButton("Soil at area centre")
        query.clicked.connect(self._soil_query)
        self.soil_layer_combo = QgsMapLayerComboBox()
        self.soil_layer_combo.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
        attach = QPushButton("Attach to layer (25 max)")
        attach.clicked.connect(self._attach_soil)
        srow.addWidget(query)
        srow.addWidget(self.soil_layer_combo, 1)
        srow.addWidget(attach)
        sform.addRow(srow)
        layout.addWidget(soil_box)

        self.land_view = QTextBrowser()
        layout.addWidget(self.land_view, 1)
        return widget

    def _add_dataset(self):
        area = self._require_aoi()
        if area is None:
            return
        dataset = self.dataset_combo.currentData()
        commands = self._commands

        def work(task):
            return commands.dataset_prepare(area.bbox, dataset)

        def apply(prepared):
            commands.dataset_apply(prepared)
            self._show_dataset_summary(prepared["spec"], prepared["summary"])
            self.log(f"{prepared['spec']['name']} added ({prepared['tiles']} tile(s))", "success")
            self._zoom_to_aoi()

        self._run(f"Adding {dataset}", work, apply)

    def _summarise_dataset(self):
        area = self._require_aoi()
        if area is None:
            return
        dataset = self.dataset_combo.currentData()
        commands = self._commands

        def work(task):
            return commands.dataset_prepare(area.bbox, dataset)

        def apply(prepared):
            self._show_dataset_summary(prepared["spec"], prepared["summary"])
            self.log(f"{prepared['spec']['name']} summarised", "success")

        self._run(f"Summarising {dataset}", work, apply)

    def _show_dataset_summary(self, spec, summary):
        parts = [f"<h3>{html.escape(spec['name'])}</h3>",
                 f"<p>{html.escape(spec.get('description') or '')}</p>"]
        kind = (summary or {}).get("kind")
        if kind == "classes":
            rows = [dict(r, percent=f"{r['fraction'] * 100:.1f}") for r in summary["classes"]]
            parts.append(_table(rows, ["label", "percent", "area_km2"]))
        elif kind == "water":
            parts.append(_table([{"measure": k.replace("_", " "), "value": f"{v * 100:.1f} %"}
                                 for k, v in summary.items() if k != "kind"], ["measure", "value"]))
        elif summary:
            unit = spec.get("unit") or ""
            keep = ("min", "p2", "p25", "median", "mean", "p75", "p98", "max", "valid_fraction",
                    "area_km2", "above_fraction", "above_area_km2", "note")
            rows = [{"measure": k.replace("_", " "), "value": summary[k],
                     "unit": unit if k in ("min", "p2", "p25", "median", "mean", "p75", "p98", "max") else
                     ("km²" if k.endswith("km2") else "")}
                    for k in keep if k in summary]
            parts.append(_table(rows, ["measure", "value", "unit"]))
        parts.append(f"<p><small>{html.escape(spec['attribution'])} ({html.escape(spec['licence'])})</small></p>")
        self.land_view.setHtml("".join(parts))

    def _soil_query(self):
        area = self._require_aoi()
        if area is None:
            return
        lon, lat = area.center
        depth = self.soil_depth.currentData()

        def work(task):
            return soil.query(lon, lat, None, [depth, "0-5cm"] if depth != "0-5cm" else ["0-5cm"])

        def apply(result):
            parts = [f"<h3>Soil at {lat:.4f}, {lon:.4f}</h3>",
                     f"<p>Topsoil texture class: <b>{html.escape(str(result['texture_class']))}</b></p>",
                     _table(result["rows"], ["label", "depth", "value", "unit"]),
                     f"<p><small>{html.escape(result['source'])}</small></p>"]
            self.land_view.setHtml("".join(parts))
            self.log("Soil properties fetched", "success")

        self._run("Querying SoilGrids", work, apply)

    def _attach_soil(self):
        layer = self.soil_layer_combo.currentLayer()
        if layer is None:
            self.log("Pick a vector layer first", "error")
            return
        if layer.isEditable():
            self.log("Finish editing the layer first", "error")
            return
        layer_id = layer.id()
        depth = self.soil_depth.currentData()
        try:
            _, points, ids = self._commands._layer_points(layer_id, limit=25)
        except Exception as exc:
            self.log(str(exc), "error")
            return
        props = ["clay", "sand", "silt", "phh2o", "soc"]

        def work(task):
            values, skipped = soil.query_many(points, props, depth)
            return values, skipped

        def apply(result):
            values, skipped = result
            from qgis.core import QgsField
            from qgis.PyQt.QtCore import QMetaType
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer is None:
                raise LookupError("The layer was removed while fetching")
            provider = layer.dataProvider()
            new_fields = [QgsField(f"soil_{k}", QMetaType.Type.QString if k == "texture"
                                   else QMetaType.Type.Double)
                          for k in props + ["texture"] if layer.fields().indexOf(f"soil_{k}") < 0]
            if new_fields and not provider.addAttributes(new_fields):
                raise IOError("Layer provider refused new fields")
            layer.updateFields()
            changes = {fid: {layer.fields().indexOf(f"soil_{k}"): row.get(k)
                             for k in props + ["texture"]} for fid, row in zip(ids, values)}
            if not provider.changeAttributeValues(changes):
                raise IOError(provider.lastError())
            layer.triggerRepaint()
            self.log(f"Soil fields attached to {len(changes)} features of {layer.name()}"
                     + (f"; {skipped} point(s) skipped after SoilGrids errors" if skipped else ""),
                     "success")

        self._run(f"SoilGrids for {len(points)} features", work, apply)

    # ------------------------------------------------------------------
    # water
    # ------------------------------------------------------------------
    def _build_water_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        info = QLabel("River discharge (GloFAS), sea state and elevation for the area centre; "
                      "surface water occurrence is under Land and Soil.")
        info.setWordWrap(True)
        layout.addWidget(info)
        row = QHBoxLayout()
        for label, fn in (("River discharge", self._river), ("Marine conditions", self._marine),
                          ("Elevation", self._elevation_point)):
            button = QPushButton(label)
            button.clicked.connect(fn)
            row.addWidget(button)
        layout.addLayout(row)
        self.water_view = QTextBrowser()
        layout.addWidget(self.water_view, 1)
        return widget

    def _river(self):
        area = self._require_aoi()
        if area is None:
            return
        lon, lat = area.center

        def work(task):
            return weather.river_discharge([(lon, lat)], 7, 30)[0]

        def apply(row):
            parts = [f"<h3>River discharge near {lat:.3f}, {lon:.3f}</h3>",
                     f"<p>Today {row['today']} m³/s, end of forecast {row['latest_forecast']} m³/s, "
                     f"window maximum {row['max_in_window']} m³/s</p>",
                     _table(row["daily"] or [], ["time", "river_discharge", "river_discharge_mean", "river_discharge_max"]),
                     "<p><small>Open-Meteo flood API (GloFAS)</small></p>"]
            self.water_view.setHtml("".join(parts))
            self.log("River discharge fetched", "success")

        self._run("Fetching river discharge", work, apply)

    def _marine(self):
        area = self._require_aoi()
        if area is None:
            return
        lon, lat = area.center

        def work(task):
            return weather.marine([(lon, lat)], 3)[0]

        def apply(row):
            cur = row.get("current") or {}
            parts = [f"<h3>Sea state at {lat:.3f}, {lon:.3f}</h3>",
                     _table([{"variable": k, "value": v} for k, v in cur.items() if k not in ("time", "interval")],
                            ["variable", "value"]),
                     _table(row.get("daily") or [], ["time", "wave_height_max", "wave_direction_dominant", "wave_period_max"]),
                     "<p><small>Open-Meteo marine API. Inland points return no data.</small></p>"]
            self.water_view.setHtml("".join(parts))
            self.log("Marine conditions fetched", "success")

        self._run("Fetching marine conditions", work, apply)

    def _elevation_point(self):
        area = self._require_aoi()
        if area is None:
            return
        w, s, e, n = area.bbox
        cx, cy = area.center
        pts = [(cx, cy), (w, s), (e, s), (e, n), (w, n)]

        def work(task):
            return weather.elevation(pts)

        def apply(rows):
            labels = ["centre", "south west", "south east", "north east", "north west"]
            parts = ["<h3>Elevation</h3>",
                     _table([dict(r, where=l) for r, l in zip(rows, labels)],
                            ["where", "lon", "lat", "elevation_m"]),
                     "<p><small>Open-Meteo elevation (Copernicus DEM 90 m)</small></p>"]
            self.water_view.setHtml("".join(parts))
            self.log("Elevation fetched", "success")

        self._run("Fetching elevation", work, apply)

    # ------------------------------------------------------------------
    # boundaries, species, sensors, alerts
    # ------------------------------------------------------------------
    def _build_boundaries_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        bbox = QGroupBox("Administrative boundaries (geoBoundaries)")
        bform = QFormLayout(bbox)
        self.iso3_edit = QLineEdit("PAK")
        self.iso3_edit.setMaxLength(3)
        self.adm_level = QComboBox()
        for lvl, label in (("ADM0", "ADM0 country"), ("ADM1", "ADM1 provinces / states"),
                           ("ADM2", "ADM2 districts"), ("ADM3", "ADM3 tehsils / subdistricts")):
            self.adm_level.addItem(label, lvl)
        self.adm_level.setCurrentIndex(2)
        self.adm_in_aoi = QCheckBox("Only units touching the area of interest")
        self.adm_in_aoi.setChecked(True)
        bform.addRow("Country (ISO3)", self.iso3_edit)
        bform.addRow("Level", self.adm_level)
        bform.addRow("", self.adm_in_aoi)
        add = QPushButton("Add boundaries")
        add.clicked.connect(self._add_boundaries)
        bform.addRow(add)
        layout.addWidget(bbox)

        gbox = QGroupBox("Species observations (GBIF)")
        gform = QFormLayout(gbox)
        self.taxon_edit = QLineEdit()
        self.taxon_edit.setPlaceholderText("Aves, Mammalia, Panthera, Mangifera indica, or empty for all")
        self.gbif_limit = QSpinBox()
        self.gbif_limit.setRange(50, species.MAX_RECORDS)
        self.gbif_limit.setValue(500)
        gform.addRow("Taxon", self.taxon_edit)
        gform.addRow("Max records", self.gbif_limit)
        fetch = QPushButton("Fetch occurrences in area")
        fetch.clicked.connect(self._fetch_species)
        gform.addRow(fetch)
        layout.addWidget(gbox)

        sbox = QGroupBox("Live sensors and alerts")
        sform = QFormLayout(sbox)
        self.sensor_radius = QSpinBox()
        self.sensor_radius.setRange(1, 200)
        self.sensor_radius.setValue(25)
        self.sensor_radius.setSuffix(" km")
        srow = QHBoxLayout()
        srow.addWidget(self.sensor_radius)
        sensors_button = QPushButton("Air quality sensors")
        sensors_button.clicked.connect(self._fetch_sensors)
        srow.addWidget(sensors_button)
        openaq_button = QPushButton("OpenAQ monitors")
        openaq_button.setToolTip("Reference grade monitors; needs a free OpenAQ key from the Sources tab")
        openaq_button.clicked.connect(self._fetch_openaq)
        srow.addWidget(openaq_button)
        self.alert_in_aoi = QCheckBox("only in area")
        alerts_button = QPushButton("GDACS disaster alerts")
        alerts_button.clicked.connect(self._fetch_alerts)
        srow.addWidget(self.alert_in_aoi)
        srow.addWidget(alerts_button)
        sform.addRow(srow)
        layout.addWidget(sbox)

        self.boundaries_view = QTextBrowser()
        layout.addWidget(self.boundaries_view, 1)
        return widget

    def _add_boundaries(self):
        iso3 = self.iso3_edit.text().strip().upper()
        level = self.adm_level.currentData()
        area = self._aoi if self.adm_in_aoi.isChecked() else None
        if self.adm_in_aoi.isChecked() and area is None:
            self.log("Select an area, or untick the area filter", "error")
            return

        def work(task):
            meta = boundaries.metadata(iso3, level)
            feats = boundaries.fetch_geojson(meta["simplified_url"] or meta["geojson_url"])
            if area is not None:
                feats = boundaries.within(feats, area.bbox)
            if not feats:
                raise LookupError("No boundary units intersect the area")
            return meta, feats

        def apply(result):
            meta, feats = result
            layer, count = _memory_layer_from_features(
                f"{meta['iso3']}_{meta['level']}_{_safe_name(meta['canonical'] or '')}", feats, "Polygon")
            self._add_layer(layer, "geoboundaries", {"iso3": iso3, "level": level})
            names = sorted(f["properties"]["name"] for f in feats if f["properties"].get("name"))
            self.boundaries_view.setHtml(
                f"<h3>{html.escape(meta['name'] or iso3)} {level}: {count} units</h3>"
                f"<p>{html.escape(', '.join(names[:80]))}</p>"
                f"<p><small>{html.escape(str(meta['licence']))}, {html.escape(str(meta['year']))}</small></p>")
            self.log(f"{count} {level} units added for {iso3}", "success")

        self._run(f"Fetching {iso3} {level}", work, apply)

    def _fetch_species(self):
        area = self._require_aoi()
        if area is None:
            return
        taxon = self.taxon_edit.text().strip() or None
        limit = self.gbif_limit.value()

        def work(task):
            return species.occurrences(area.bbox, taxon, limit)

        def apply(result):
            feats = result["features"]
            if not feats:
                self.boundaries_view.setHtml("<p>No GBIF records here.</p>")
                self.log("No species records found", "error")
                return
            layer, count = _memory_layer_from_features(
                "gbif_" + _safe_name(taxon) if taxon else "gbif_occurrences", feats)
            self._add_layer(layer, "gbif", {"bbox": area.bbox, "taxon": taxon})
            top = species.species_counts(feats)
            self.boundaries_view.setHtml(
                f"<h3>{count} records ({result['matched']} available)</h3>"
                + _table(top, ["species", "records"]) + "<p><small>GBIF.org</small></p>")
            self.log(f"{count} species records added", "success")

        self._run("Fetching GBIF occurrences", work, apply)

    def _fetch_sensors(self):
        area = self._require_aoi()
        if area is None:
            return
        lon, lat = area.center
        radius = self.sensor_radius.value()

        def work(task):
            return sensors.air_sensors(lon, lat, radius)

        def apply(result):
            feats = result["features"]
            if not feats:
                self.boundaries_view.setHtml("<p>No community sensors within that radius.</p>")
                self.log("No sensors found; widen the radius", "error")
                return
            layer, count = _memory_layer_from_features("air_quality_sensors", feats)
            self._add_layer(layer, "sensor-community", {"lon": lon, "lat": lat, "radius_km": radius})
            summary = result["summary"]
            self.boundaries_view.setHtml(
                f"<h3>{count} air quality stations</h3><p>PM2.5 mean {summary['pm2_5_mean']} µg/m³, "
                f"max {summary['pm2_5_max']} µg/m³</p><p><small>Sensor.Community (ODbL)</small></p>")
            self.log(f"{count} sensor stations added", "success")

        self._run("Fetching air quality sensors", work, apply)

    def _fetch_alerts(self):
        bbox = None
        if self.alert_in_aoi.isChecked():
            area = self._require_aoi()
            if area is None:
                return
            bbox = area.bbox

        def work(task):
            return disasters.alerts(bbox)

        def apply(result):
            feats = result["features"]
            if not feats:
                self.boundaries_view.setHtml("<p>No current alerts.</p>")
                self.log("No current GDACS alerts", "success")
                return
            layer, count = _memory_layer_from_features("gdacs_alerts", feats)
            self._add_layer(layer, "gdacs", {"bbox": bbox})
            rows = [f["properties"] for f in feats][:40]
            self.boundaries_view.setHtml(
                f"<h3>{count} GDACS alerts</h3>" + _table(rows, ["alert_level", "event_type", "country", "title"])
                + "<p><small>GDACS (EC JRC / UN OCHA)</small></p>")
            self.log(f"{count} disaster alerts added", "success")

        self._run("Fetching GDACS alerts", work, apply)

    # ------------------------------------------------------------------
    # analysis
    # ------------------------------------------------------------------
    def _build_analysis_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        today = QDate.currentDate()

        cbox = QGroupBox("Change detection between two periods")
        cform = QFormLayout(cbox)
        self.change_index = QComboBox()
        for name, (_, _, _, blurb) in indices.INDICES.items():
            self.change_index.addItem(f"{name}  -  {blurb}", name)
        self.before_from = QDateEdit(today.addDays(-90))
        self.before_to = QDateEdit(today.addDays(-46))
        self.after_from = QDateEdit(today.addDays(-45))
        self.after_to = QDateEdit(today)
        for edit in (self.before_from, self.before_to, self.after_from, self.after_to):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
        before = QHBoxLayout()
        before.addWidget(self.before_from)
        before.addWidget(QLabel("to"))
        before.addWidget(self.before_to)
        after = QHBoxLayout()
        after.addWidget(self.after_from)
        after.addWidget(QLabel("to"))
        after.addWidget(self.after_to)
        cform.addRow("Index", self.change_index)
        cform.addRow("Before", before)
        cform.addRow("After", after)
        change = QPushButton("Compute change")
        change.clicked.connect(self._change_detection)
        cform.addRow(change)
        layout.addWidget(cbox)

        zbox = QGroupBox("Per polygon statistics")
        zform = QFormLayout(zbox)
        self.zonal_layer_combo = QgsMapLayerComboBox()
        self.zonal_layer_combo.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)
        self.zonal_index = QComboBox()
        for name in indices.INDICES:
            self.zonal_index.addItem(name, name)
        zrow = QHBoxLayout()
        zrow.addWidget(self.zonal_layer_combo, 1)
        zrow.addWidget(self.zonal_index)
        zonal = QPushButton("Compute per polygon")
        zonal.clicked.connect(self._zonal)
        zrow.addWidget(zonal)
        zform.addRow(zrow)
        layout.addWidget(zbox)

        tbox = QGroupBox("Time lapse and report")
        tform = QFormLayout(tbox)
        trow = QHBoxLayout()
        self.series_scenes = QSpinBox()
        self.series_scenes.setRange(2, 24)
        self.series_scenes.setValue(6)
        self.series_scenes.setSuffix(" scenes")
        series = QPushButton("Add dated scenes for the Temporal Controller")
        series.clicked.connect(self._series)
        trow.addWidget(self.series_scenes)
        trow.addWidget(series, 1)
        tform.addRow(trow)
        report = QPushButton("Build area report (HTML)")
        report.setToolTip("Weather, elevation, land cover, soil and NDVI for the area; opens in your browser")
        report.clicked.connect(self._report)
        tform.addRow(report)
        layout.addWidget(tbox)

        self.analysis_view = QTextBrowser()
        layout.addWidget(self.analysis_view, 1)
        return widget

    def _change_detection(self):
        area = self._require_aoi()
        if area is None:
            return
        index = self.change_index.currentData()
        b0, b1 = self._dates(self.before_from, self.before_to)
        a0, a1 = self._dates(self.after_from, self.after_to)
        commands = self._commands

        def work(task):
            return commands.compute_index_change({"bbox": area.bbox}, index, b0, b1, a0, a1,
                                                 add_to_project=False)

        def apply(result):
            layer = commands.add_change_layer(result)
            rows = [dict(c, percent=f"{c['fraction'] * 100:.1f}") for c in result["classes"]]
            fmt = lambda v, sign="": "n/a" if v is None else f"{v:{sign}.3f}"  # noqa: E731
            self.analysis_view.setHtml(
                f"<h3>{html.escape(result['index'])} change</h3>"
                f"<p>Before: {(result['before']['datetime'] or '')[:10]} (mean {fmt(result['before']['mean'])}); "
                f"after: {(result['after']['datetime'] or '')[:10]} (mean {fmt(result['after']['mean'])}); "
                f"mean change {fmt(result['change']['mean_change'], '+')}</p>"
                + _table(rows, ["label", "from", "to", "percent"])
                + f"<p><small>{html.escape(result['interpretation'])}</small></p>")
            self.log(f"{result['index']} change layer added: {layer.name()}", "success")
            self._zoom_to_aoi()

        self._run(f"Change detection ({index})", work, apply)

    def _zonal(self):
        layer = self.zonal_layer_combo.currentLayer()
        if layer is None:
            self.log("Pick a polygon layer first", "error")
            return
        if layer.isEditable():
            self.log("Finish editing the layer first", "error")
            return
        index = self.zonal_index.currentData()
        commands = self._commands
        layer_id = layer.id()
        try:
            area = commands.zonal_area(layer_id)
        except Exception as exc:
            self.log(str(exc), "error")
            return

        def work(task):
            return commands.zonal_prepare(layer_id, index, area=area)

        def apply(prepared):
            result = commands.zonal_apply(prepared)
            note = result.get("coverage_note")
            self.analysis_view.setHtml(
                f"<h3>{html.escape(result['index'])} per polygon ({result['features']} features)</h3>"
                f"<p>Scene {html.escape(result['item'])}, fields {', '.join(result['fields_added'])}</p>"
                + (f"<p><b>{html.escape(note)}</b></p>" if note else "")
                + "<h4>Lowest</h4>" + _table(result["lowest"], ["id", "name", "mean"])
                + "<h4>Highest</h4>" + _table(result["highest"], ["id", "name", "mean"])
                + f"<p><small>{html.escape(result['interpretation'])}</small></p>")
            self.log(f"{result['index']} statistics added to {layer.name()}", "success")

        self._run(f"Zonal {index} for {layer.name()}", work, apply)

    def _series(self):
        area = self._require_aoi()
        if area is None:
            return
        collection = self.collection_combo.currentData()
        start, end = self._dates(self.date_from, self.date_to)
        cloud = self.cloud_spin.value()
        n = self.series_scenes.value()
        commands = self._commands

        def work(task):
            return commands.series_prepare({"bbox": area.bbox}, collection, start, end, cloud, n)

        def apply(prepared):
            result = commands.series_apply(prepared)
            self.analysis_view.setHtml(
                f"<h3>{result['scenes']} dated scenes in group '{html.escape(result['group'])}'</h3>"
                + _table(result["layers"], ["datetime", "cloud_cover"])
                + f"<p>{html.escape(result['note'])}</p>")
            self.log(result["note"], "success")

        self._run(f"Loading {n} dated scenes", work, apply)

    def _report(self):
        area = self._require_aoi()
        if area is None:
            return
        commands = self._commands

        def work(task):
            return commands.export_area_report({"bbox": area.bbox}, render_map=False,
                                               title=area.label)

        def apply(result):
            path = result["path"]
            self.analysis_view.setHtml(
                f"<h3>Report written</h3><p>{html.escape(path)}</p>"
                f"<p>Sections: {', '.join(result['sections'])}</p>"
                + ("<p>Not available: " + html.escape(json.dumps(result["errors"])) + "</p>"
                   if result["errors"] else ""))
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            self.log(f"Report written to {path}", "success")

        self._run("Building area report", work, apply)
