# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""Data Explorer tabs and actions for the advanced toolbox: 3D (buildings,
terrain, point clouds, view), the live monitor, climate projections and
seasonal outlooks, and elevation profiles.

Mixed into :class:`ExplorerDock`. ``work`` closures run on a worker thread
and touch only connectors; ``apply`` closures run on the GUI thread.
"""

import datetime as _dt
import html

from qgis.core import QgsMapLayerProxyModel, QgsProject
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import QTimer, QUrl
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from . import monitor as monitor_mod
from .connectors import airquality, events, osm, weather
from .explorer_toolbox import _table
from .live_commands import _memory_layer_from_features, _safe_name, _split_by_geometry

SOURCE_LABELS = {
    "earthquakes": "Earthquakes (USGS)", "air_sensors": "Air sensors (Sensor.Community)",
    "weather": "Weather at centre (Open-Meteo)", "disaster_alerts": "Disaster alerts (GDACS)",
    "natural_events": "Natural events (NASA EONET)",
}


class AdvancedTabsMixin:
    """3D, Monitor tabs plus climate and profile actions."""

    def _build_advanced_tabs(self):
        self.tabs.addTab(self._build_3d_tab(), "3D")
        self.tabs.addTab(self._build_monitor_tab(), "Monitor")

    # ------------------------------------------------------------------
    # climate and seasonal (buttons live on the Weather tab)
    # ------------------------------------------------------------------
    def _climate_widgets(self):
        row = QHBoxLayout()
        self.climate_from = QSpinBox()
        self.climate_from.setRange(1950, 2050)
        self.climate_from.setValue(2030)
        self.climate_to = QSpinBox()
        self.climate_to.setRange(1950, 2050)
        self.climate_to.setValue(2050)
        climate = QPushButton("Climate projection")
        climate.setToolTip("Seven CMIP6 models, yearly ensemble means and the decade trend")
        climate.clicked.connect(self._climate)
        self.seasonal_days = QSpinBox()
        self.seasonal_days.setRange(7, 274)
        self.seasonal_days.setValue(90)
        self.seasonal_days.setSuffix(" days")
        seasonal = QPushButton("Seasonal outlook")
        seasonal.clicked.connect(self._seasonal)
        row.addWidget(self.climate_from)
        row.addWidget(QLabel("to"))
        row.addWidget(self.climate_to)
        row.addWidget(climate)
        row.addWidget(self.seasonal_days)
        row.addWidget(seasonal)
        return row

    def _climate(self):
        area = self._require_aoi()
        if area is None:
            return
        lon, lat = area.center
        y0, y1 = self.climate_from.value(), self.climate_to.value()
        if y1 < y0:
            self.log("The end year must not be before the start year", "error")
            return
        if y1 - y0 + 1 > 30:
            self.log("Keep the projection window to 30 years or fewer", "error")
            return

        def work(task):
            return weather.climate_projection([(lon, lat)], y0, y1)[0]

        def apply(row):
            trend = row.get("trend") or {}
            trows = [{"variable": k.replace("_", " "), "first decade": v.get("first_decade_mean"),
                      "last decade": v.get("last_decade_mean"), "change": v.get("change"),
                      "unit": (row.get("units") or {}).get(k)} for k, v in trend.items()]
            yearly = [{k: v for k, v in y.items() if not k.endswith("_by_model")} for y in row["yearly"]]
            cols = ["year"] + [k for k in (yearly[0] if yearly else {}) if k != "year"]
            self.weather_view.setHtml(
                f"<h3>Climate projection {y0} to {y1} near {lat:.3f}, {lon:.3f}</h3>"
                f"<p>Ensemble of {len(row['models'])} CMIP6 HighResMIP models.</p>"
                + _table(trows, ["variable", "first decade", "last decade", "change", "unit"])
                + "<h4>Yearly ensemble means</h4>" + _table(yearly, cols)
                + f"<p><small>{html.escape(row.get('source') or 'Open-Meteo climate API')}</small></p>")
            self.log(f"Climate projection {y0} to {y1} fetched", "success")

        self._run(f"Climate projection {y0} to {y1}", work, apply)

    def _seasonal(self):
        area = self._require_aoi()
        if area is None:
            return
        lon, lat = area.center
        days = self.seasonal_days.value()

        def work(task):
            return weather.seasonal_forecast([(lon, lat)], days)[0]

        def apply(row):
            weekly = row.get("weekly") or []
            cols = list(weekly[0]) if weekly else []
            daily = row.get("daily") or []
            dcols = list(daily[0]) if daily else []
            self.weather_view.setHtml(
                f"<h3>Seasonal outlook, next {days} days, near {lat:.3f}, {lon:.3f}</h3>"
                "<h4>Weekly</h4>" + _table(weekly, cols)
                + "<h4>Daily (ensemble mean with member minimum and maximum)</h4>"
                + _table(daily[:60], dcols)
                + "<p><small>Open-Meteo seasonal API. Skill drops beyond a few weeks; "
                  "use the spread between minimum and maximum as the uncertainty.</small></p>")
            self.log(f"Seasonal outlook ({days} days) fetched", "success")

        self._run(f"Seasonal outlook {days} days", work, apply)

    # ------------------------------------------------------------------
    # elevation profile (buttons live on the Elevation tab)
    # ------------------------------------------------------------------
    def _profile_widgets(self):
        box = QGroupBox("Elevation profile")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.profile_direction = QComboBox()
        self.profile_direction.addItem("west to east through the centre", "west-east")
        self.profile_direction.addItem("south to north through the centre", "south-north")
        across = QPushButton("Profile across area")
        across.clicked.connect(lambda: self._profile(from_layer=False))
        row.addWidget(self.profile_direction, 1)
        row.addWidget(across)
        form.addRow(row)
        row2 = QHBoxLayout()
        self.profile_layer_combo = QgsMapLayerComboBox()
        self.profile_layer_combo.setFilters(QgsMapLayerProxyModel.Filter.LineLayer)
        along = QPushButton("Profile along selected line")
        along.setToolTip("Select one feature of a road, river or track layer first")
        along.clicked.connect(lambda: self._profile(from_layer=True))
        row2.addWidget(self.profile_layer_combo, 1)
        row2.addWidget(along)
        form.addRow(row2)
        self.profile_view = QTextBrowser()
        self.profile_view.setMinimumHeight(220)
        form.addRow(self.profile_view)
        return box

    def _profile(self, from_layer):
        commands = self._commands
        try:
            if from_layer:
                layer = self.profile_layer_combo.currentLayer()
                if layer is None:
                    self.log("Pick a line layer first", "error")
                    return
                prepared = commands.profile_points(layer_id=layer.id(), selected=True, samples=60)
            else:
                area = self._require_aoi()
                if area is None:
                    return
                direction = self.profile_direction.currentData()
                prepared = commands.profile_points(aoi={"bbox": area.bbox}, direction=direction,
                                                   samples=60)
                prepared["label"] = f"{area.label or 'area'}, {direction.replace('-', ' to ')}"
        except Exception as exc:
            self.log(str(exc), "error")
            return

        def work(task):
            return commands.profile_fetch(prepared)

        def apply(result):
            width = max(320, self.profile_view.viewport().width() - 24)
            chart = commands.profile_chart(result, width=width, height=max(160, width // 3))
            stats = result.get("statistics") or {}
            img = (f"<p><img src='{QUrl.fromLocalFile(chart).toString()}' width='{width}'></p>"
                   if chart else "")
            self.profile_view.setHtml(
                f"<h3>Profile: {html.escape(result['label'])}</h3>" + img
                + _table([{"measure": k.replace("_", " "), "value": v} for k, v in stats.items()],
                         ["measure", "value"])
                + f"<p><small>{html.escape(result['source'])}</small></p>")
            self.log(f"Elevation profile ({stats.get('length_km')} km) ready", "success")

        self._run("Elevation profile", work, apply)

    # ------------------------------------------------------------------
    # natural events (Hazards tab) and OpenAQ (Boundaries and Species tab)
    # ------------------------------------------------------------------
    def _fetch_events(self):
        days = self.event_days.value()
        bbox = None
        if self.event_in_aoi.isChecked():
            area = self._require_aoi()
            if area is None:
                return
            bbox = area.bbox

        def work(task):
            return events.events(bbox, None, "open", days)

        def apply(result):
            feats = result["features"]
            if not feats:
                self.hazard_result.setText("No open natural events" + (" in the area" if bbox else ""))
                self.log("No natural events found", "error")
                return
            layer, count = _memory_layer_from_features("eonet_events", feats)
            self._add_layer(layer, "nasa-eonet", {"bbox": bbox, "days": days})
            cats = ", ".join(f"{k} {v}" for k, v in result["by_category"].items())
            text = f"{count} natural events added ({cats})"
            self.hazard_result.setText(text)
            self.log(text, "success")

        self._run("Fetching natural events", work, apply)

    def _fetch_openaq(self):
        area = self._require_aoi()
        if area is None:
            return
        key = self._settings.value("qgis_mcp/keys/openaq", "")
        if not key:
            self.log("OpenAQ needs a free key: paste it in the Sources tab (openaq)", "error")
            return
        lon, lat = area.center
        radius = min(self.sensor_radius.value(), 25)

        def work(task):
            return airquality.stations(lon, lat, radius, key)

        def apply(result):
            feats = result["features"]
            if not feats:
                self.boundaries_view.setHtml("<p>No OpenAQ stations within 25 km.</p>")
                self.log("No OpenAQ stations found", "error")
                return
            layer, count = _memory_layer_from_features("openaq_stations", feats)
            self._add_layer(layer, "openaq", {"lon": lon, "lat": lat, "radius_km": radius})
            summary = result["summary"]
            self.boundaries_view.setHtml(
                f"<h3>{count} OpenAQ stations</h3><p>PM2.5 mean {summary.get('pm25_mean')} µg/m³, "
                f"max {summary.get('pm25_max')} µg/m³ ({summary.get('with_latest')} with latest readings)</p>"
                + _table([f["properties"] for f in feats][:30], ["name", "provider", "pm25", "pm10", "last_seen"])
                + f"<p><small>{html.escape(result['source'])}</small></p>")
            self.log(f"{count} OpenAQ stations added", "success")

        self._run("Fetching OpenAQ stations", work, apply)

    # ------------------------------------------------------------------
    # 3D tab
    # ------------------------------------------------------------------
    def _build_3d_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        info = QLabel("Buildings, terrain and lidar for 3D map views and the Elevation Profile panel.")
        info.setWordWrap(True)
        layout.addWidget(info)

        bbox = QGroupBox("Buildings")
        bform = QFormLayout(bbox)
        self.building_height = QDoubleSpinBox()
        self.building_height.setRange(1, 500)
        self.building_height.setValue(10)
        self.building_height.setSuffix(" m default")
        self.building_levels = QDoubleSpinBox()
        self.building_levels.setRange(1, 10)
        self.building_levels.setValue(3)
        self.building_levels.setSuffix(" m per storey")
        hrow = QHBoxLayout()
        hrow.addWidget(self.building_height)
        hrow.addWidget(self.building_levels)
        bform.addRow(hrow)
        fetch = QPushButton("Fetch OpenStreetMap buildings in area and extrude")
        fetch.setToolTip("Small areas only (a town centre); heights come from height and building:levels tags")
        fetch.clicked.connect(self._fetch_buildings)
        bform.addRow(fetch)
        erow = QHBoxLayout()
        self.building_layer_combo = QgsMapLayerComboBox()
        self.building_layer_combo.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)
        extrude = QPushButton("Extrude this polygon layer")
        extrude.clicked.connect(self._extrude_layer)
        erow.addWidget(self.building_layer_combo, 1)
        erow.addWidget(extrude)
        bform.addRow(erow)
        layout.addWidget(bbox)

        tbox = QGroupBox("Terrain and view")
        tform = QFormLayout(tbox)
        trow = QHBoxLayout()
        self.terrain_layer_combo = QgsMapLayerComboBox()
        self.terrain_layer_combo.setFilters(QgsMapLayerProxyModel.Filter.RasterLayer)
        terrain = QPushButton("Use as terrain")
        terrain.setToolTip("Any DEM: Copernicus (Elevation tab) or NASADEM / ALOS (Land and Soil)")
        terrain.clicked.connect(self._set_terrain)
        trow.addWidget(self.terrain_layer_combo, 1)
        trow.addWidget(terrain)
        tform.addRow(trow)
        view = QPushButton("Open a 3D map view")
        view.setToolTip("Asks first: on remote desktops without a GPU a 3D view can crash QGIS")
        view.clicked.connect(self._open_3d)
        tform.addRow(view)
        layout.addWidget(tbox)
        layout.addWidget(self._tiles_box())

        pbox = QGroupBox("Lidar point clouds")
        pform = QFormLayout(pbox)
        lidar = QPushButton("Add USGS 3DEP lidar tiles for area (United States)")
        lidar.clicked.connect(self._add_lidar)
        pform.addRow(lidar)
        urow = QHBoxLayout()
        self.copc_url = QLineEdit()
        self.copc_url.setPlaceholderText("https://.../file.copc.laz")
        copc = QPushButton("Add COPC URL")
        copc.clicked.connect(self._add_copc)
        urow.addWidget(self.copc_url, 1)
        urow.addWidget(copc)
        pform.addRow(urow)
        layout.addWidget(pbox)

        self.view3d_result = QLabel("")
        self.view3d_result.setWordWrap(True)
        layout.addWidget(self.view3d_result)
        layout.addStretch(1)
        return widget

    def _fetch_buildings(self):
        area = self._require_aoi()
        if area is None:
            return
        if area.area_degrees2() > 0.05:
            self.log("Shrink the area to a few square kilometres for buildings", "error")
            return
        height, per_level = self.building_height.value(), self.building_levels.value()
        commands = self._commands

        def work(task):
            query, features, info = osm.fetch(area.bbox, {"building": True}, "full", 20000)
            return features, info

        def apply(result):
            features, info = result
            groups = _split_by_geometry(features)
            polygons = groups.get("Polygon") or []
            if not polygons:
                raise LookupError("OpenStreetMap has no building footprints here")
            layer, count = _memory_layer_from_features("buildings", polygons, "Polygon")
            layer = self._add_layer(layer, "overpass", {"tags": {"building": True}, "bbox": area.bbox})
            extruded = commands.make_3d_buildings(layer.id(), None, None, height, per_level)
            self.building_layer_combo.setLayer(layer)
            self.terrain_layer_combo.setCurrentIndex(max(0, self.terrain_layer_combo.currentIndex()))
            text = (f"{count} buildings extruded ({'truncated, ' if info.get('truncated') else ''}"
                    f"height from {extruded['height_field'] or 'default'}"
                    f"{', storeys from ' + extruded['levels_field'] if extruded['levels_field'] else ''}). "
                    "Set a terrain and open a 3D view.")
            self.view3d_result.setText(text)
            self.log(text, "success")
            self._zoom_to_aoi()

        self._run("Fetching buildings", work, apply)

    def _extrude_layer(self):
        layer = self.building_layer_combo.currentLayer()
        if layer is None:
            self.log("Pick a polygon layer first", "error")
            return
        try:
            result = self._commands.make_3d_buildings(layer.id(), None, None,
                                                      self.building_height.value(),
                                                      self.building_levels.value())
        except Exception as exc:
            self.log(str(exc), "error")
            return
        text = f"{layer.name()} extruded: {result['height_expression']}"
        self.view3d_result.setText(text)
        self.log(text, "success")

    def _set_terrain(self):
        layer = self.terrain_layer_combo.currentLayer()
        if layer is None:
            self.log("Pick a DEM raster layer first", "error")
            return
        try:
            result = self._commands.set_terrain(layer.id())
        except Exception as exc:
            self.log(str(exc), "error")
            return
        self.view3d_result.setText(result["note"])
        self.log(f"{layer.name()} is now the terrain", "success")

    def _open_3d(self):
        if self.iface is None:
            self.log("A 3D view needs the QGIS window; this bridge is running headless", "error")
            return
        from qgis.PyQt.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self, "Open a 3D map view",
            "Opening a 3D view from the panel can crash QGIS on machines without a real GPU "
            "(remote desktops). Save your project first. Open the view now? "
            "(View > 3D Map Views > New 3D Map View does the same by hand.)")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self._commands.open_3d_view("Data Explorer 3D", confirm=True)
        except Exception as exc:
            self.log(str(exc), "error")
            return
        self.log(f"3D view requested (terrain: {result['terrain']})", "success")

    def _add_lidar(self):
        area = self._require_aoi()
        if area is None:
            return
        commands = self._commands

        def work(task):
            return commands.point_cloud_prepare(area.bbox, 4)

        def apply(prepared):
            layers = commands.point_cloud_apply(prepared)
            ok = [l for l in layers if "layer_id" in l]
            text = f"{len(ok)} of {len(layers)} lidar tiles added" + (
                f"; first has {ok[0]['points']:,} points" if ok else "")
            self.view3d_result.setText(text)
            self.log(text, "success" if ok else "error")

        self._run("Searching 3DEP lidar", work, apply)

    def _add_copc(self):
        url = self.copc_url.text().strip()
        if not url:
            self.log("Paste a COPC URL first", "error")
            return
        try:
            result = self._commands.add_point_cloud(url=url)
        except Exception as exc:
            self.log(str(exc), "error")
            return
        text = f"Point cloud added: {result['layers'][0].get('points', 0):,} points"
        self.view3d_result.setText(text)
        self.log(text, "success")

    # ------------------------------------------------------------------
    # monitor tab
    # ------------------------------------------------------------------
    def _build_monitor_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        info = QLabel("Refresh live feeds for the area on a timer; alerts go to the QGIS "
                      "message bar and the table below. Layers are named monitor_*.")
        info.setWordWrap(True)
        layout.addWidget(info)
        sbox = QGroupBox("Sources and thresholds")
        form = QFormLayout(sbox)
        self.monitor_sources = {}
        for key in monitor_mod.SOURCES:
            cb = QCheckBox(SOURCE_LABELS[key])
            cb.setChecked(True)
            self.monitor_sources[key] = cb
            form.addRow(cb)
        self.monitor_interval = QSpinBox()
        self.monitor_interval.setRange(1, 1440)
        self.monitor_interval.setValue(10)
        self.monitor_interval.setSuffix(" min")
        self.monitor_mag = QDoubleSpinBox()
        self.monitor_mag.setRange(0, 9)
        self.monitor_mag.setValue(4.5)
        self.monitor_pm = QDoubleSpinBox()
        self.monitor_pm.setRange(1, 1000)
        self.monitor_pm.setValue(50)
        self.monitor_temp = QDoubleSpinBox()
        self.monitor_temp.setRange(-50, 60)
        self.monitor_temp.setValue(40)
        self.monitor_wind = QDoubleSpinBox()
        self.monitor_wind.setRange(1, 300)
        self.monitor_wind.setValue(60)
        trow = QHBoxLayout()
        for label, w in (("every", self.monitor_interval), ("M ≥", self.monitor_mag),
                         ("PM2.5 >", self.monitor_pm), ("°C >", self.monitor_temp),
                         ("wind km/h >", self.monitor_wind)):
            trow.addWidget(QLabel(label))
            trow.addWidget(w)
        form.addRow(trow)
        brow = QHBoxLayout()
        self.monitor_start = QPushButton("Start monitoring")
        self.monitor_start.clicked.connect(self._monitor_start)
        self.monitor_stop = QPushButton("Stop")
        self.monitor_stop.setEnabled(False)
        self.monitor_stop.clicked.connect(self._monitor_stop)
        refresh = QPushButton("Refresh now")
        refresh.clicked.connect(self._monitor_tick)
        brow.addWidget(self.monitor_start)
        brow.addWidget(self.monitor_stop)
        brow.addWidget(refresh)
        form.addRow(brow)
        layout.addWidget(sbox)
        self.monitor_status = QLabel("Not running")
        self.monitor_status.setWordWrap(True)
        layout.addWidget(self.monitor_status)
        self.monitor_table = QTableWidget(0, 4)
        self.monitor_table.setHorizontalHeaderLabels(["Time", "Source", "Severity", "Alert"])
        self.monitor_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.monitor_table.verticalHeader().setVisible(False)
        layout.addWidget(self.monitor_table, 1)
        self._monitor_timer = QTimer(self)
        self._monitor_timer.timeout.connect(self._monitor_tick)
        return widget

    def _monitor_rules(self):
        return {"earthquakes": {"min_magnitude": self.monitor_mag.value()},
                "air_sensors": {"pm2_5": self.monitor_pm.value()},
                "weather": {"temperature_2m": self.monitor_temp.value(),
                            "wind_speed_10m": self.monitor_wind.value()}}

    def _monitor_start(self):
        area = self._require_aoi()
        if area is None:
            return
        sources = [k for k, cb in self.monitor_sources.items() if cb.isChecked()]
        if not sources:
            self.log("Tick at least one source", "error")
            return
        try:
            config = self._commands.monitor_configure({"bbox": area.bbox}, sources,
                                                      self.monitor_interval.value(),
                                                      self._monitor_rules())
        except Exception as exc:
            self.log(str(exc), "error")
            return
        config["label"] = area.label
        self._monitor_timer.start(config["interval_minutes"] * 60_000)
        self.monitor_start.setEnabled(False)
        self.monitor_stop.setEnabled(True)
        self.monitor_table.setRowCount(0)
        self.monitor_status.setText(f"Running every {config['interval_minutes']} min over "
                                    f"{area.label or 'the area'}")
        self.log(f"Live monitor started ({', '.join(sources)})", "success")
        self._monitor_tick()

    def _monitor_stop(self):
        self._monitor_timer.stop()
        self._commands.stop_live_monitor()
        self.monitor_start.setEnabled(True)
        self.monitor_stop.setEnabled(False)
        self.monitor_status.setText("Stopped")
        self.log("Live monitor stopped")

    def _monitor_tick(self):
        if getattr(self, "_closed", False):
            return
        state = self._commands._monitor_state()
        config = state.get("config")
        if config is None:
            self.log("Start the monitor first", "error")
            return
        commands = self._commands

        def work(task):
            return monitor_mod.fetch(config)

        def apply(fetched):
            result = commands.monitor_apply(fetched, config)
            if result.get("stale"):
                return
            for alert in result["alerts"]:
                row = self.monitor_table.rowCount()
                self.monitor_table.insertRow(row)
                for col, value in enumerate((alert["time"][11:19], alert["source"],
                                             alert["severity"], alert["message"])):
                    self.monitor_table.setItem(row, col, QTableWidgetItem(str(value)))
            self.monitor_table.scrollToBottom()
            counts = ", ".join(f"{k} {v}" for k, v in result["counts"].items())
            errors = "; ".join(f"{k}: {v}" for k, v in result["errors"].items())
            self.monitor_status.setText(
                f"Run {result['run']} at {result['at'][11:19]}: {counts}. "
                f"{len(result['alerts'])} new alert(s)." + (f" Errors: {errors}" if errors else ""))
            if result["alerts"]:
                self.log(f"{len(result['alerts'])} monitor alert(s)", "success")

        self._run("Live monitor refresh", work, apply)
