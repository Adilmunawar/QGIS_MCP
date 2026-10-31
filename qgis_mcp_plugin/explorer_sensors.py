# -*- coding: utf-8 -*-
"""Data Explorer Sensors tab (gauges, buoys, tides, aircraft, SensorThings),
the 3D Tiles box on the 3D tab and the population button on Land and Soil.

Mixed into :class:`ExplorerDock`. ``work`` closures touch only connectors;
``apply`` closures create layers on the GUI thread.
"""

import html

from qgis.PyQt.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QTextBrowser, QVBoxLayout, QWidget,
)

from .connectors import aircraft, gauges, population, sensorthings
from .explorer_toolbox import _table
from .live_commands import _memory_layer_from_features
from .sensor_commands import TILES_PRESETS


class SensorTabsMixin:
    """Sensors tab plus the 3D Tiles and population actions."""

    def _build_sensor_tabs(self):
        index = next((i for i in range(self.tabs.count()) if self.tabs.tabText(i) == "Monitor"), None)
        widget = self._build_sensors_tab()
        if index is None:
            self.tabs.addTab(widget, "Sensors")
        else:
            self.tabs.insertTab(index, widget, "Sensors")

    # ------------------------------------------------------------------
    # Sensors tab
    # ------------------------------------------------------------------
    def _build_sensors_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        info = QLabel("Live observation networks for the area. Gauges and tides cover the United "
                      "States; buoys, aircraft and SensorThings servers are worldwide.")
        info.setWordWrap(True)
        layout.addWidget(info)

        row = QHBoxLayout()
        for label, fn, tip in (("River gauges", self._gauges, "USGS discharge and stage (United States)"),
                               ("Buoys", self._buoys, "NOAA NDBC latest observations"),
                               ("Tide stations", self._tides, "NOAA water levels (United States coasts)"),
                               ("Aircraft now", self._aircraft, "OpenSky positions; rate limited")):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(fn)
            row.addWidget(button)
        layout.addLayout(row)

        box = QGroupBox("OGC SensorThings server (live layer)")
        form = QFormLayout(box)
        self.sta_preset = QComboBox()
        for key, spec in sensorthings.PRESETS.items():
            self.sta_preset.addItem(spec["name"], key)
        self.sta_preset.addItem("Custom URL", "")
        self.sta_url = QLineEdit()
        self.sta_url.setPlaceholderText("https://server/FROST-Server/v1.1")
        self.sta_entity = QComboBox()
        for entity in sensorthings.GEOMETRY_ENTITIES:
            self.sta_entity.addItem(entity, entity)
        self.sta_limit = QSpinBox()
        self.sta_limit.setRange(10, 100000)
        self.sta_limit.setValue(1000)
        form.addRow("Server", self.sta_preset)
        form.addRow("Custom URL", self.sta_url)
        erow = QHBoxLayout()
        erow.addWidget(self.sta_entity)
        erow.addWidget(QLabel("limit"))
        erow.addWidget(self.sta_limit)
        discover = QPushButton("Discover")
        discover.clicked.connect(self._sta_discover)
        add_all = QPushButton("Add layer (whole server)")
        add_all.clicked.connect(lambda: self._sta_add(in_area=False))
        add_area = QPushButton("Add layer (area only)")
        add_area.clicked.connect(lambda: self._sta_add(in_area=True))
        erow.addWidget(discover)
        erow.addWidget(add_all)
        erow.addWidget(add_area)
        form.addRow(erow)
        layout.addWidget(box)

        self.sensors_view = QTextBrowser()
        self.sensors_view.setOpenExternalLinks(True)
        layout.addWidget(self.sensors_view, 1)
        return widget

    def _show_points(self, title, result, columns, source_id, query, layer_name, hint):
        feats = result["features"]
        if not feats:
            self.sensors_view.setHtml(f"<h3>{html.escape(title)}</h3><p>{html.escape(hint)}</p>")
            self.log(hint, "error")
            return
        layer, count = _memory_layer_from_features(layer_name, feats)
        self._add_layer(layer, source_id, query)
        rows = [f["properties"] for f in feats][:40]
        self.sensors_view.setHtml(
            f"<h3>{count} {html.escape(title)}</h3>"
            + _table([{"measure": k.replace("_", " "), "value": v} for k, v in result["summary"].items()],
                     ["measure", "value"])
            + _table(rows, columns) + f"<p><small>{html.escape(result['source'])}</small></p>")
        self.log(f"{count} {title} added", "success")
        if self._aoi is not None:
            self._zoom_to_aoi()

    def _gauges(self):
        area = self._require_aoi()
        if area is None:
            return
        self._run("Fetching river gauges", lambda task: gauges.usgs_gauges(area.bbox),
                  lambda r: self._show_points("river gauges", r, ["name", "discharge_m3s", "gage_height_m", "time"],
                                              "usgs-nwis", {"bbox": area.bbox}, "river_gauges",
                                              "No active USGS gauges here (United States only)."))

    def _buoys(self):
        area = self._aoi  # optional: without an area every buoy in the world is listed
        bbox = area.bbox if area else None
        self._run("Fetching buoys", lambda task: gauges.ndbc_buoys(bbox),
                  lambda r: self._show_points("buoys", r, ["station", "wave_height_m", "wind_speed_ms", "water_temp_c", "time"],
                                              "ndbc", {"bbox": bbox}, "ndbc_buoys",
                                              "No buoys inside the area; widen it towards the sea."))

    def _tides(self):
        area = self._require_aoi()
        if area is None:
            return
        self._run("Fetching tide stations", lambda task: gauges.noaa_tide_stations(area.bbox, 20),
                  lambda r: self._show_points("tide stations", r, ["name", "state", "water_level_m", "time"],
                                              "noaa-coops", {"bbox": area.bbox}, "tide_stations",
                                              "No NOAA tide stations here (United States coasts only)."))

    def _aircraft(self):
        area = self._require_aoi()
        if area is None:
            return
        self._run("Fetching aircraft", lambda task: aircraft.opensky_states(area.bbox),
                  lambda r: self._show_points("aircraft", r, ["callsign", "country", "altitude_m", "speed_kmh", "heading_deg"],
                                              "opensky", {"bbox": area.bbox}, "aircraft_now",
                                              "No aircraft tracked in the area right now."))

    def _sta_url_value(self):
        key = self.sta_preset.currentData()
        if key:
            return sensorthings.PRESETS[key]["url"], key
        return self.sta_url.text().strip(), None

    def _sta_discover(self):
        url, _ = self._sta_url_value()
        if not url:
            self.log("Pick a preset or paste a server URL", "error")
            return

        def apply(info):
            self.sensors_view.setHtml(
                f"<h3>{html.escape(info['url'])}</h3>"
                f"<p>Locations: {html.escape(str(info.get('locations', 'unknown')))}</p>"
                f"<p>Entity sets: {html.escape(', '.join(info.get('entity_sets') or []))}</p>"
                f"<p>Sample: {html.escape(str(info.get('sample_location')))}</p>")
            self.log("SensorThings server discovered", "success")

        self._run("Discovering SensorThings server", lambda task: sensorthings.discover(url), apply)

    def _sta_add(self, in_area):
        url, preset = self._sta_url_value()
        if not url:
            self.log("Pick a preset or paste a server URL", "error")
            return
        aoi = None
        if in_area:
            area = self._require_aoi()
            if area is None:
                return
            aoi = {"bbox": area.bbox}
        try:
            result = self._commands.add_sensorthings_layer(url, preset, self.sta_entity.currentData(),
                                                           aoi, self.sta_limit.value())
        except Exception as exc:
            self.log(str(exc), "error")
            return
        self.sensors_view.setHtml(
            f"<h3>{html.escape(result['name'])}</h3><p>{result['features']} features from "
            f"{html.escape(result['url'])} ({html.escape(result['entity'])}, "
            f"{'live layer' if result.get('live') else 'snapshot'})</p>"
            f"<p>Fields: {html.escape(', '.join(result['fields']))}</p><p>{html.escape(result['note'])}</p>")
        self.log(f"SensorThings layer added: {result['name']}", "success")
        if in_area:
            self._zoom_to_aoi()

    # ------------------------------------------------------------------
    # 3D Tiles (box on the 3D tab)
    # ------------------------------------------------------------------
    def _tiles_box(self):
        box = QGroupBox("3D Tiles")
        form = QFormLayout(box)
        self.tiles_preset = QComboBox()
        for key, spec in TILES_PRESETS.items():
            self.tiles_preset.addItem(spec["name"], key)
        self.tiles_preset.addItem("Custom tileset URL", "")
        self.tiles_url = QLineEdit()
        self.tiles_url.setPlaceholderText("https://.../tileset.json")
        row = QHBoxLayout()
        row.addWidget(self.tiles_preset, 1)
        row.addWidget(self.tiles_url, 1)
        add = QPushButton("Add 3D Tiles")
        add.setToolTip("Google needs a Maps Platform key in the Sources tab (google-3d-tiles)")
        add.clicked.connect(self._add_tiles)
        row.addWidget(add)
        form.addRow(row)
        return box

    def _add_tiles(self):
        preset = self.tiles_preset.currentData() or None
        url = self.tiles_url.text().strip() or None
        if not preset and not url:
            self.log("Pick a 3D Tiles preset or paste a tileset URL", "error")
            return
        try:
            result = self._commands.add_3d_tiles(url=None if preset else url, preset=preset)
        except Exception as exc:
            self.log(str(exc), "error")
            return
        self.view3d_result.setText(f"{result['name']} added. {result['note']}")
        self.log(f"3D Tiles added: {result['name']}", "success")

    # ------------------------------------------------------------------
    # population (button on Land and Soil)
    # ------------------------------------------------------------------
    def _population(self):
        area = self._require_aoi()
        if area is None:
            return
        if area.area_degrees2() > 4:
            self.log("Keep population estimates under 2 by 2 degrees", "error")
            return
        commands = self._commands

        def work(task):
            out = {"errors": {}}
            try:
                out["worldpop"] = population.worldpop_total(area.bbox, 2020)
            except Exception as exc:
                out["errors"]["worldpop"] = str(exc)[:200]
            try:
                out["hrsl"] = commands.dataset_prepare(area.bbox, "population")
            except Exception as exc:
                out["errors"]["meta_hrsl"] = str(exc)[:200]
            try:
                out["age_sex"] = population.worldpop_age_sex(area.bbox, 2020)
            except Exception as exc:
                out["errors"]["age_sex"] = str(exc)[:200]
            return out

        def apply(out):
            parts = [f"<h3>Population of {html.escape(area.label or 'the area')}</h3>"]
            wp = out.get("worldpop")
            if wp:
                parts.append(f"<p>WorldPop 2020: <b>{wp['total_population']:,}</b> people, "
                             f"{wp['density_per_km2']:,.0f} per km²</p>")
            if out.get("hrsl"):
                try:
                    commands.dataset_apply(out["hrsl"])
                    total = out["hrsl"]["summary"].get("total")
                    parts.append(f"<p>Meta 30 m grid: <b>{total:,}</b> people (layer added)</p>" if total is not None
                                 else "<p>Meta 30 m grid: no data in the area</p>")
                except Exception as exc:
                    out["errors"]["meta_hrsl"] = str(exc)[:200]
            ages = out.get("age_sex")
            if ages:
                parts.append(f"<p>Under 15: {ages['children_under_15']:,}; 15 to 64: {ages['working_age_15_64']:,}; "
                             f"65 and over: {ages['elderly_65_plus']:,}</p>")
                parts.append(_table(ages["rows"], ["age_from", "male", "female"]))
            if out["errors"]:
                parts.append("<p><small>Not available: " + html.escape(str(out["errors"])) + "</small></p>")
            parts.append("<p><small>WorldPop (CC BY 4.0); Meta Data for Good HRSL (CC BY 4.0)</small></p>")
            self.land_view.setHtml("".join(parts))
            self.log("Population estimated", "success" if (wp or out.get("hrsl")) else "error")

        self._run("Estimating population", work, apply)
