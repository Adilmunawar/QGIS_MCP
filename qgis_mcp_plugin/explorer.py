# -*- coding: utf-8 -*-
"""Data Explorer: a click-driven dashboard over the live-data engine.

The same connectors that serve the MCP tools are exposed here as a dock
panel: pick an area of interest by drawing it, typing a place, or using the
current view; then pull imagery, indices, weather, elevation, OpenStreetMap
features, hazards and basemaps with buttons. Network and GDAL work runs in
:class:`QgsTask` workers, so QGIS stays responsive; layers are created on
the GUI thread when the worker finishes.
"""

import datetime as _dt
import hashlib
import html
import json
import math
import os
import traceback

from osgeo import gdal
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsField, QgsGeometry, QgsMapLayer, QgsMapLayerProxyModel, QgsProject,
    QgsRasterLayer, QgsRectangle, QgsSettings, QgsTask, QgsWkbTypes,
)
from qgis.gui import QgsMapLayerComboBox, QgsMapToolExtent, QgsRubberBand
from qgis.PyQt.QtCore import QDate, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDockWidget,
    QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextBrowser, QToolButton, QVBoxLayout,
    QWidget,
)

from . import aoi as aoi_mod
from .connectors import (basemaps, geocode, hazards, indices, osm, registry,
                         stac, weather)
from .live_commands import (
    LiveDataMixin, _memory_layer_from_features, _safe_name, _split_by_geometry,
    cache_dir,
)

OSM_PRESETS = [
    ("Hospitals and clinics", {"amenity": ["hospital", "clinic", "doctors"]}),
    ("Schools and universities", {"amenity": ["school", "college", "university"]}),
    ("Pharmacies", {"amenity": "pharmacy"}),
    ("Places of worship", {"amenity": "place_of_worship"}),
    ("Fuel stations", {"amenity": "fuel"}),
    ("Banks and ATMs", {"amenity": ["bank", "atm"]}),
    ("Markets and shops", {"shop": "*"}),
    ("Main roads", {"highway": ["motorway", "trunk", "primary", "secondary"]}),
    ("All roads", {"highway": "*"}),
    ("Railways", {"railway": "rail"}),
    ("Buildings", {"building": "*"}),
    ("Farmland", {"landuse": "farmland"}),
    ("Orchards", {"landuse": "orchard"}),
    ("Industrial areas", {"landuse": "industrial"}),
    ("Residential areas", {"landuse": "residential"}),
    ("Rivers and streams", {"waterway": ["river", "stream", "canal"]}),
    ("Lakes and reservoirs", {"natural": "water"}),
    ("Forests", {"landuse": "forest"}),
    ("Parks", {"leisure": "park"}),
    ("Power lines", {"power": "line"}),
    ("Admin boundaries", {"boundary": "administrative"}),
    ("Custom tag...", None),
]


class ExplorerTask(QgsTask):
    """Run ``work()`` off the GUI thread, then ``apply(result)`` on it."""

    def __init__(self, description, work, apply, on_error, owner=None):
        super().__init__(description, QgsTask.Flag.CanCancel)
        self._work = work
        self._apply = apply
        self._on_error = on_error
        self._owner = owner
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self._work(self)
            return True
        except Exception as exc:  # reported in finished()
            self.error = exc
            self.error_text = traceback.format_exc()
            return False

    def finished(self, ok):
        owner = self._owner
        if owner is not None and getattr(owner, "_closed", False):
            return
        if self.isCanceled():
            if owner is not None:
                owner.log(f"Cancelled: {self.description()}")
                owner._task_done()
            return
        if ok:
            try:
                self._apply(self.result)
            except Exception as exc:
                self._on_error(f"{self.description()}: {exc}", traceback.format_exc())
        elif self.error is not None:
            self._on_error(f"{self.description()}: {self.error}",
                           getattr(self, "error_text", ""))
        else:
            self._on_error(f"{self.description()}: failed", "")


class ExplorerDock(QDockWidget):
    """The Data Explorer panel."""

    closed = pyqtSignal()

    def __init__(self, iface, icon_path=None, parent=None):
        super().__init__("Data Explorer", parent)
        self.setObjectName("QgisMcpDataExplorer")
        self.iface = iface
        self._settings = QgsSettings()
        self._aoi = None
        self._tasks = []
        self._rubber = None
        self._extent_tool = None
        self._scenes = []
        self._closed = False
        from .mcp_server import QgisMCPServer
        from .permissions import MASTER, PermissionManager
        # Never started: used only for its layer helpers on the GUI thread.
        self._commands = QgisMCPServer(iface=iface,
                                       permissions=PermissionManager(MASTER))
        if icon_path and os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self._build()
        self._restore_aoi()

    # ==================================================================
    # layout
    # ==================================================================
    def _build(self):
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        outer.addWidget(self._build_aoi_box())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_imagery_tab(), "Imagery")
        self.tabs.addTab(self._build_weather_tab(), "Weather")
        self.tabs.addTab(self._build_elevation_tab(), "Elevation")
        self.tabs.addTab(self._build_osm_tab(), "OpenStreetMap")
        self.tabs.addTab(self._build_hazards_tab(), "Hazards")
        self.tabs.addTab(self._build_basemaps_tab(), "Basemaps")
        self.tabs.addTab(self._build_sources_tab(), "Sources")
        self.tabs.addTab(self._build_log_tab(), "Log")
        outer.addWidget(self.tabs, 1)

        status = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("Idle")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_tasks)
        status.addWidget(self.progress, 1)
        status.addWidget(self.cancel_button)
        outer.addLayout(status)

        self.setWidget(root)

    def _build_aoi_box(self):
        box = QGroupBox("Area of interest")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(4)

        self.place_edit = QLineEdit()
        self.place_edit.setPlaceholderText("Type a place, e.g. Multan or Lahore District")
        self.place_edit.returnPressed.connect(self._aoi_from_place)
        self.radius_spin = QDoubleSpinBox()
        self.radius_spin.setRange(0.5, 200)
        self.radius_spin.setValue(5)
        self.radius_spin.setSuffix(" km")
        self.radius_spin.setToolTip("Radius around the place centre; 0.5-200 km")
        go = QPushButton("Find")
        go.clicked.connect(self._aoi_from_place)
        grid.addWidget(self.place_edit, 0, 0, 1, 2)
        grid.addWidget(self.radius_spin, 0, 2)
        grid.addWidget(go, 0, 3)

        self.draw_button = QPushButton("Draw on map")
        self.draw_button.setCheckable(True)
        self.draw_button.setToolTip("Drag a rectangle on the map canvas")
        self.draw_button.toggled.connect(self._toggle_draw)
        view_button = QPushButton("Use map view")
        view_button.clicked.connect(self._aoi_from_canvas)
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setAllowEmptyLayer(True)
        self.layer_combo.setToolTip("Use a layer's extent, or its selected features")
        layer_button = QPushButton("Layer extent")
        layer_button.clicked.connect(lambda: self._aoi_from_layer(False))
        sel_button = QPushButton("Selection")
        sel_button.clicked.connect(lambda: self._aoi_from_layer(True))
        grid.addWidget(self.draw_button, 1, 0)
        grid.addWidget(view_button, 1, 1)
        grid.addWidget(self.layer_combo, 1, 2)
        grid.addWidget(layer_button, 1, 3)
        grid.addWidget(sel_button, 2, 3)

        self.aoi_label = QLabel("No area selected. Find a place, draw, or use the map view.")
        self.aoi_label.setWordWrap(True)
        self.aoi_label.setStyleSheet("color: palette(mid);")
        zoom_button = QToolButton()
        zoom_button.setText("Zoom to")
        zoom_button.clicked.connect(self._zoom_to_aoi)
        clear_button = QToolButton()
        clear_button.setText("Clear")
        clear_button.clicked.connect(self._clear_aoi)
        grid.addWidget(self.aoi_label, 2, 0, 1, 3)
        row = QHBoxLayout()
        row.addWidget(zoom_button)
        row.addWidget(clear_button)
        row.addStretch(1)
        grid.addLayout(row, 3, 0, 1, 4)
        return box

    def _build_imagery_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        self.collection_combo = QComboBox()
        for key, spec in stac.COLLECTIONS.items():
            if key == "cop-dem-glo-30":
                continue
            self.collection_combo.addItem(f"{key}  -  {spec['description']}", key)
        self.collection_combo.setCurrentIndex(
            max(0, self.collection_combo.findData("sentinel-2-l2a")))
        self.collection_combo.currentIndexChanged.connect(self._clear_scenes)
        today = QDate.currentDate()
        self.date_from = QDateEdit(today.addDays(-60))
        self.date_to = QDateEdit(today)
        for edit in (self.date_from, self.date_to):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
        self.cloud_spin = QSpinBox()
        self.cloud_spin.setRange(0, 100)
        self.cloud_spin.setValue(20)
        self.cloud_spin.setSuffix(" % max cloud")
        dates = QHBoxLayout()
        dates.addWidget(self.date_from)
        dates.addWidget(QLabel("to"))
        dates.addWidget(self.date_to)
        dates.addWidget(self.cloud_spin)
        form.addRow("Collection", self.collection_combo)
        form.addRow("Dates", dates)
        layout.addLayout(form)

        search = QPushButton("Search scenes")
        search.clicked.connect(self._search_imagery)
        layout.addWidget(search)

        self.scene_table = QTableWidget(0, 4)
        self.scene_table.setHorizontalHeaderLabels(["Date", "Cloud %", "Platform", "Scene"])
        self.scene_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.scene_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.scene_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.scene_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.scene_table.verticalHeader().setVisible(False)
        layout.addWidget(self.scene_table, 1)

        actions = QGridLayout()
        add_visual = QPushButton("Add true colour")
        add_visual.clicked.connect(lambda: self._add_imagery(None))
        self.bands_edit = QLineEdit("red,nir")
        self.bands_edit.setToolTip("Comma-separated asset names, e.g. red,nir,swir16,scl")
        add_bands = QPushButton("Add bands")
        add_bands.clicked.connect(lambda: self._add_imagery(self.bands_edit.text()))
        actions.addWidget(add_visual, 0, 0)
        actions.addWidget(self.bands_edit, 0, 1)
        actions.addWidget(add_bands, 0, 2)

        self.index_combo = QComboBox()
        for name, (_, _, _, blurb) in indices.INDICES.items():
            self.index_combo.addItem(f"{name}  -  {blurb}", name)
        self.index_combo.addItem("Custom expression...", "custom")
        self.expression_edit = QLineEdit()
        self.expression_edit.setPlaceholderText("(nir - red) / (nir + red + 0.16)")
        self.mask_check = QCheckBox("Mask clouds")
        self.mask_check.setChecked(True)
        compute = QPushButton("Compute index")
        compute.clicked.connect(self._compute_index)
        series = QPushButton("Time series")
        series.setToolTip("Mean index per scene across the date range (several seconds per scene)")
        series.clicked.connect(self._time_series)
        actions.addWidget(self.index_combo, 1, 0, 1, 2)
        actions.addWidget(self.mask_check, 1, 2)
        actions.addWidget(self.expression_edit, 2, 0, 1, 3)
        actions.addWidget(compute, 3, 0)
        actions.addWidget(series, 3, 1)
        layout.addLayout(actions)

        self.index_result = QLabel("")
        self.index_result.setWordWrap(True)
        layout.addWidget(self.index_result)
        return widget

    def _build_weather_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        self.weather_kind = QComboBox()
        for label, key in (("Current conditions", "current"),
                           ("7-day forecast", "forecast"),
                           ("Air quality now", "air_quality"),
                           ("History (Open-Meteo, daily)", "history"),
                           ("Agro-climate (NASA POWER, daily)", "power")):
            self.weather_kind.addItem(label, key)
        today = QDate.currentDate()
        self.weather_from = QDateEdit(today.addDays(-30))
        self.weather_to = QDateEdit(today.addDays(-1))
        for edit in (self.weather_from, self.weather_to):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
        dates = QHBoxLayout()
        dates.addWidget(self.weather_from)
        dates.addWidget(QLabel("to"))
        dates.addWidget(self.weather_to)
        form.addRow("Request", self.weather_kind)
        form.addRow("Dates (history)", dates)
        layout.addLayout(form)
        fetch = QPushButton("Get weather for the area centre")
        fetch.clicked.connect(self._get_weather)
        layout.addWidget(fetch)
        self.weather_view = QTextBrowser()
        self.weather_view.setOpenExternalLinks(True)
        layout.addWidget(self.weather_view, 1)
        attach_row = QHBoxLayout()
        self.weather_layer_combo = QgsMapLayerComboBox()
        self.weather_layer_combo.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
        attach = QPushButton("Attach current weather to layer")
        attach.setToolTip("Adds wx_* fields to every feature (first 100), sampled at centroids")
        attach.clicked.connect(self._attach_weather)
        attach_row.addWidget(self.weather_layer_combo, 1)
        attach_row.addWidget(attach)
        layout.addLayout(attach_row)
        return widget

    def _build_elevation_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        info = QLabel("Copernicus DEM GLO-30 (30 m) streamed for the area of interest.")
        info.setWordWrap(True)
        layout.addWidget(info)
        add = QPushButton("Add DEM for area")
        add.clicked.connect(self._add_dem)
        layout.addWidget(add)
        form = QFormLayout()
        self.dem_layer_combo = QgsMapLayerComboBox()
        self.dem_layer_combo.setFilters(QgsMapLayerProxyModel.Filter.RasterLayer)
        self.terrain_combo = QComboBox()
        for product, blurb in (("hillshade", "Hillshade (shaded relief)"),
                               ("slope", "Slope (degrees)"),
                               ("aspect", "Aspect (degrees from north)"),
                               ("roughness", "Roughness"),
                               ("TRI", "Terrain ruggedness index"),
                               ("TPI", "Topographic position index")):
            self.terrain_combo.addItem(blurb, product)
        form.addRow("DEM layer", self.dem_layer_combo)
        form.addRow("Product", self.terrain_combo)
        layout.addLayout(form)
        derive = QPushButton("Derive terrain product")
        derive.clicked.connect(self._derive_terrain)
        layout.addWidget(derive)
        self.dem_result = QLabel("")
        self.dem_result.setWordWrap(True)
        layout.addWidget(self.dem_result)
        layout.addStretch(1)
        return widget

    def _build_osm_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        self.osm_preset = QComboBox()
        for label, tags in OSM_PRESETS:
            self.osm_preset.addItem(label, tags)
        self.osm_preset.currentIndexChanged.connect(self._osm_preset_changed)
        self.osm_key = QLineEdit()
        self.osm_key.setPlaceholderText("key, e.g. amenity")
        self.osm_value = QLineEdit()
        self.osm_value.setPlaceholderText("value, * for any, a|b for several")
        custom = QHBoxLayout()
        custom.addWidget(self.osm_key)
        custom.addWidget(self.osm_value)
        self.osm_geometry = QComboBox()
        self.osm_geometry.addItem("Points (centre of each feature, fast)", "center")
        self.osm_geometry.addItem("Full geometry (lines and polygons)", "full")
        self.osm_limit = QSpinBox()
        self.osm_limit.setRange(100, osm.MAX_ELEMENTS)
        self.osm_limit.setValue(5000)
        form.addRow("Preset", self.osm_preset)
        form.addRow("Custom tag", custom)
        form.addRow("Geometry", self.osm_geometry)
        form.addRow("Max features", self.osm_limit)
        layout.addLayout(form)
        fetch = QPushButton("Fetch from OpenStreetMap")
        fetch.clicked.connect(self._fetch_osm)
        layout.addWidget(fetch)
        self.osm_result = QLabel("")
        self.osm_result.setWordWrap(True)
        layout.addWidget(self.osm_result)
        layout.addStretch(1)
        self._osm_preset_changed()
        return widget

    def _build_hazards_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        quake_box = QGroupBox("Earthquakes (USGS)")
        qf = QFormLayout(quake_box)
        self.quake_period = QComboBox()
        for p in hazards.USGS_PERIODS:
            self.quake_period.addItem(f"last {p}", p)
        self.quake_period.setCurrentIndex(2)
        self.quake_mag = QDoubleSpinBox()
        self.quake_mag.setRange(0, 9)
        self.quake_mag.setValue(4.5)
        self.quake_mag.setSingleStep(0.5)
        self.quake_in_aoi = QCheckBox("Only inside the area of interest")
        qf.addRow("Period", self.quake_period)
        qf.addRow("Min magnitude", self.quake_mag)
        qf.addRow("", self.quake_in_aoi)
        quake_button = QPushButton("Fetch earthquakes")
        quake_button.clicked.connect(self._fetch_quakes)
        qf.addRow(quake_button)
        layout.addWidget(quake_box)

        fire_box = QGroupBox("Active fires (NASA FIRMS, needs a free key)")
        ff = QFormLayout(fire_box)
        self.fire_days = QSpinBox()
        self.fire_days.setRange(1, 10)
        self.fire_days.setValue(2)
        self.fire_source = QComboBox()
        for src in hazards.FIRMS_SOURCES:
            self.fire_source.addItem(src, src)
        ff.addRow("Days back", self.fire_days)
        ff.addRow("Sensor", self.fire_source)
        fire_button = QPushButton("Fetch active fires in area")
        fire_button.clicked.connect(self._fetch_fires)
        ff.addRow(fire_button)
        layout.addWidget(fire_box)
        self.hazard_result = QLabel("")
        self.hazard_result.setWordWrap(True)
        layout.addWidget(self.hazard_result)
        layout.addStretch(1)
        return widget

    def _build_basemaps_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.basemap_list = QListWidget()
        for row in basemaps.list_basemaps():
            label = row["name"] + ("  (daily, pick a date)" if row.get("dated") else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setToolTip(row["attribution"])
            self.basemap_list.addItem(item)
        self.basemap_list.setCurrentRow(0)
        layout.addWidget(self.basemap_list, 1)
        form = QFormLayout()
        self.basemap_date = QDateEdit(QDate.currentDate().addDays(-1))
        self.basemap_date.setCalendarPopup(True)
        self.basemap_date.setDisplayFormat("yyyy-MM-dd")
        self.basemap_opacity = QSpinBox()
        self.basemap_opacity.setRange(10, 100)
        self.basemap_opacity.setValue(100)
        self.basemap_opacity.setSuffix(" %")
        form.addRow("Date (GIBS layers)", self.basemap_date)
        form.addRow("Opacity", self.basemap_opacity)
        layout.addLayout(form)
        add = QPushButton("Add basemap")
        add.clicked.connect(self._add_basemap)
        layout.addWidget(add)
        return widget

    def _build_sources_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.sources_table = QTableWidget(0, 4)
        self.sources_table.setHorizontalHeaderLabels(["Source", "Category", "Ready", "Licence"])
        self.sources_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.sources_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.sources_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sources_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sources_table.verticalHeader().setVisible(False)
        layout.addWidget(self.sources_table, 1)
        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("API key for the selected source")
        save_key = QPushButton("Save key")
        save_key.clicked.connect(self._save_key)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(save_key)
        layout.addLayout(key_row)
        self.populate_sources()
        return widget

    def _build_log_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        layout.addWidget(self.log_view)
        clear = QPushButton("Clear log")
        clear.clicked.connect(self.log_view.clear)
        layout.addWidget(clear)
        return widget

    # ==================================================================
    # helpers
    # ==================================================================
    def log(self, text, level="info"):
        stamp = _dt.datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"{stamp}  {text}")
        if level == "error" and self.iface is not None:
            self.iface.messageBar().pushWarning("Data Explorer", text[:200])
        elif level == "success" and self.iface is not None:
            self.iface.messageBar().pushSuccess("Data Explorer", text[:200])

    def _error(self, text, trace=""):
        self.log(text, "error")
        if trace:
            self.log_view.appendPlainText(trace.rstrip())
        self._task_done()

    def _run(self, description, work, apply):
        running = [t.description() for t in self._tasks
                   if t.status() in (QgsTask.TaskStatus.Queued, QgsTask.TaskStatus.Running)]
        if description in running:
            self.log(f"Already running: {description}", "error")
            return None
        task = ExplorerTask(description, work, self._wrap_apply(apply), self._error,
                            owner=self)
        self._tasks.append(task)
        self.progress.setRange(0, 0)
        self.progress.setFormat(description)
        self.cancel_button.setEnabled(True)
        self.log(f"Started: {description}")
        QgsApplication.taskManager().addTask(task)
        return task

    def _wrap_apply(self, apply):
        def inner(result):
            try:
                apply(result)
            finally:
                self._task_done()
        return inner

    def _task_done(self):
        self._tasks = [t for t in self._tasks
                       if t.status() in (QgsTask.TaskStatus.Queued, QgsTask.TaskStatus.Running)]
        if not self._tasks:
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self.progress.setFormat("Idle")
            self.cancel_button.setEnabled(False)

    def _cancel_tasks(self):
        for task in self._tasks:
            task.cancel()
        self.log("Cancel requested; a fetch already in flight stops at its next step")

    def _require_aoi(self):
        if self._aoi is None:
            self.log("Select an area of interest first", "error")
            return None
        return self._aoi

    def _add_layer(self, layer, source_id, query, persist=True):
        layer = self._commands._add_layer(layer, source_id, query, persist=persist)
        self._refresh_canvas()
        return layer

    def _refresh_canvas(self):
        if self.iface is not None:
            self.iface.mapCanvas().refresh()

    @staticmethod
    def _dates(edit_from, edit_to):
        return (edit_from.date().toString("yyyy-MM-dd"),
                edit_to.date().toString("yyyy-MM-dd"))

    # ==================================================================
    # AOI
    # ==================================================================
    def set_aoi(self, area, announce=True):
        self._aoi = area
        w_km = area.rect.width() * 111.32 * max(
            0.05, abs(math.cos(math.radians(area.center[1]))))
        h_km = area.rect.height() * 110.54
        label = area.label or f"{area.source} area"
        self.aoi_label.setText(
            f"{label}\n{w_km:.1f} x {h_km:.1f} km  |  centre "
            f"{area.center[1]:.4f}, {area.center[0]:.4f}  |  bbox "
            f"{', '.join(f'{v:.4f}' for v in area.bbox)}")
        self.aoi_label.setStyleSheet("")
        self._draw_rubber(area)
        self._settings.setValue("qgis_mcp/explorer_aoi", json.dumps(area.to_dict()))
        if announce:
            self._clear_scenes()
            self.log(f"AOI set from {area.source}: {label}")

    def _clear_scenes(self):
        self._scenes = []
        if hasattr(self, "scene_table"):
            self.scene_table.setRowCount(0)

    def _restore_aoi(self):
        saved = self._settings.value("qgis_mcp/explorer_aoi", "")
        if not saved:
            return
        try:
            data = json.loads(saved)
            area = aoi_mod.from_bbox(data["bbox"], source=data.get("source", "bbox"),
                                     label=data.get("label"))
            self.set_aoi(area, announce=False)
        except Exception:
            pass

    def _clear_aoi(self):
        self._aoi = None
        self.aoi_label.setText("No area selected. Find a place, draw, or use the map view.")
        self.aoi_label.setStyleSheet("color: palette(mid);")
        if self._rubber is not None:
            self._rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        self._settings.remove("qgis_mcp/explorer_aoi")

    def _draw_rubber(self, area):
        if self.iface is None:
            return
        canvas = self.iface.mapCanvas()
        if self._rubber is None:
            self._rubber = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
            self._rubber.setColor(QColor(255, 120, 0, 160))
            self._rubber.setFillColor(QColor(255, 120, 0, 30))
            self._rubber.setWidth(2)
        geometry = QgsGeometry.fromRect(area.rect)
        dest = canvas.mapSettings().destinationCrs()
        if dest.isValid() and dest != aoi_mod.WGS84:
            geometry.transform(QgsCoordinateTransform(aoi_mod.WGS84, dest, QgsProject.instance()))
        self._rubber.setToGeometry(geometry, None)

    def _zoom_to_aoi(self):
        area = self._require_aoi()
        if area is None or self.iface is None:
            return
        canvas = self.iface.mapCanvas()
        rect = QgsRectangle(area.rect)
        dest = canvas.mapSettings().destinationCrs()
        if dest.isValid() and dest != aoi_mod.WGS84:
            rect = QgsCoordinateTransform(aoi_mod.WGS84, dest, QgsProject.instance()
                                          ).transformBoundingBox(rect)
        canvas.setExtent(rect)
        canvas.refresh()

    def _aoi_from_place(self):
        query = self.place_edit.text().strip()
        if not query:
            return
        radius = self.radius_spin.value()

        def work(task):
            bbox, label = geocode.bbox_for(query)
            return bbox, label

        def apply(result):
            bbox, label = result
            cx = (bbox[0] + bbox[2]) / 2
            cy = (bbox[1] + bbox[3]) / 2
            self.set_aoi(aoi_mod.from_point(cx, cy, radius, label=label))

        self._run(f"Finding {query}", work, apply)

    def _aoi_from_canvas(self):
        try:
            self.set_aoi(aoi_mod.from_canvas(self.iface))
        except Exception as exc:
            self.log(str(exc), "error")

    def _aoi_from_layer(self, selected):
        layer = self.layer_combo.currentLayer()
        if layer is None:
            self.log("Pick a layer first", "error")
            return
        try:
            self.set_aoi(aoi_mod.from_layer(layer.id(), selected))
        except Exception as exc:
            self.log(str(exc), "error")

    def _toggle_draw(self, checked):
        if self.iface is None:
            return
        canvas = self.iface.mapCanvas()
        if checked:
            if self._extent_tool is None:
                self._extent_tool = QgsMapToolExtent(canvas)
                self._extent_tool.extentChanged.connect(self._extent_drawn)
                canvas.mapToolSet.connect(self._map_tool_changed)
            canvas.setMapTool(self._extent_tool)
        elif self._extent_tool is not None and canvas.mapTool() is self._extent_tool:
            canvas.unsetMapTool(self._extent_tool)

    def _map_tool_changed(self, new_tool, old_tool=None):
        if new_tool is not self._extent_tool and self.draw_button.isChecked():
            self.draw_button.blockSignals(True)
            self.draw_button.setChecked(False)
            self.draw_button.blockSignals(False)

    def _extent_drawn(self, rect):
        if rect.isEmpty():
            return
        canvas = self.iface.mapCanvas()
        crs = canvas.mapSettings().destinationCrs()
        wgs = rect
        if crs.isValid() and crs != aoi_mod.WGS84:
            wgs = QgsCoordinateTransform(crs, aoi_mod.WGS84, QgsProject.instance()
                                         ).transformBoundingBox(rect)
        try:
            self.set_aoi(aoi_mod.from_bbox(
                [wgs.xMinimum(), wgs.yMinimum(), wgs.xMaximum(), wgs.yMaximum()],
                source="drawn"))
        except Exception as exc:
            self.log(str(exc), "error")
        self.draw_button.setChecked(False)

    # ==================================================================
    # imagery
    # ==================================================================
    def _search_imagery(self):
        area = self._require_aoi()
        if area is None:
            return
        collection = self.collection_combo.currentData()
        start, end = self._dates(self.date_from, self.date_to)
        cloud = self.cloud_spin.value()

        def work(task):
            return stac.search(area.bbox, collection, start, end, cloud, 25)

        def apply(result):
            self._scenes = result["items"]
            self.scene_table.setRowCount(0)
            for item in self._scenes:
                row = self.scene_table.rowCount()
                self.scene_table.insertRow(row)
                cloud_text = "-" if item["cloud_cover"] is None else f"{item['cloud_cover']:.1f}"
                for col, text in enumerate([(item["datetime"] or "")[:10], cloud_text,
                                            item["platform"] or "", item["id"]]):
                    self.scene_table.setItem(row, col, QTableWidgetItem(text))
            if self._scenes:
                self.scene_table.selectRow(0)
                self.log(f"{len(self._scenes)} scenes found "
                         f"({result.get('matched')} match in total)", "success")
            else:
                self.log("No scenes; widen the dates or raise the cloud limit", "error")

        self._run(f"Searching {collection}", work, apply)

    def _selected_scene(self):
        rows = self.scene_table.selectionModel().selectedRows() if self._scenes else []
        if not rows:
            self.log("Search and select a scene first", "error")
            return None
        return self._scenes[rows[0].row()]

    def _add_imagery(self, assets_text):
        scene = self._selected_scene()
        if scene is None:
            return
        collection = scene["collection"]
        spec = stac.COLLECTIONS.get(collection, {})
        keys = [a.strip() for a in assets_text.split(",") if a.strip()] if assets_text \
            else [spec.get("display", "visual")]

        def work(task):
            item = stac.get_item(scene["id"], collection)
            resolved = []
            for key in keys:
                if task.isCanceled():
                    break
                href, asset = stac.asset_href(item, key)
                path = stac.gdal_path(href)
                ds = gdal.Open(path)  # warms the HTTP cache off the GUI thread
                if ds is None:
                    raise IOError(f"GDAL could not open {key}: {href}")
                size = (ds.RasterXSize, ds.RasterYSize)
                ds = None
                resolved.append((key, href, path, asset.get("title"), size))
            return item, resolved

        def apply(result):
            item, resolved = result
            for key, href, path, title, size in resolved:
                layer = QgsRasterLayer(path, f"{scene['id']} {key}", "gdal")
                if not layer.isValid():
                    self.log(f"Could not open {key}: {href}", "error")
                    continue
                self._add_layer(layer, "earth-search",
                                {"item": scene["id"], "asset": key, "href": href})
                self.log(f"Added {key} ({title}) {size[0]}x{size[1]}", "success")

        self._run(f"Streaming {scene['id']} {', '.join(keys)}", work, apply)

    def _compute_index(self):
        area = self._require_aoi()
        scene = self._selected_scene()
        if area is None or scene is None:
            return
        index = self.index_combo.currentData()
        expression = self.expression_edit.text().strip() or None
        if index == "custom" and not expression:
            self.log("Enter a custom expression, e.g. (nir - red) / (nir + red)", "error")
            return
        if index != "custom":
            expression = None
        mask = self.mask_check.isChecked()
        collection = scene["collection"]

        def work(task):
            item = stac.get_item(scene["id"], collection)
            result = indices.compute(item, area.bbox, index if not expression else None,
                                     expression, mask)
            digest = hashlib.sha1(json.dumps(
                [area.bbox, result["expression"], mask]).encode()).hexdigest()[:8]
            label = result["index"]
            path = os.path.join(cache_dir(),
                                f"{_safe_name(label)}_{_safe_name(scene['id'])}_{digest}.tif")
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    path = path[:-4] + f"_{int(_dt.datetime.now().timestamp())}.tif"
            indices.write_geotiff(path, result["array"], result["transform"],
                                  result["projection"])
            classes = indices.classify(result["array"], [0.2, 0.4, 0.6]) \
                if label in ("NDVI", "EVI", "SAVI") else None
            return label, path, result["statistics"], result["resolution"], classes

        def apply(result):
            label, path, stats, resolution, classes = result
            layer = QgsRasterLayer(path, f"{label} {scene['id'][:25]}", "gdal")
            if not layer.isValid():
                raise IOError(f"Could not open {path}")
            self._add_layer(layer, "earth-search", {"item": scene["id"], "index": label})
            LiveDataMixin._style_index_layer(layer, label, stats)
            text = (f"{label}: mean {stats['mean']:.3f}, median {stats['median']:.3f}, "
                    f"range {stats['min']:.2f} to {stats['max']:.2f}, "
                    f"{stats['valid_pixels']} valid px at {resolution:.0f} m")
            if stats.get("cloud_masked_fraction"):
                text += f", {stats['cloud_masked_fraction'] * 100:.0f}% cloud masked"
            if classes:
                text += "\nClasses: " + ", ".join(
                    f"{'<' + str(c['to']) if c['from'] is None else str(c['from']) + ('-' + str(c['to']) if c['to'] is not None else '+')}: "
                    f"{c['fraction'] * 100:.0f}%" for c in classes)
            self.index_result.setText(text)
            self.log(text, "success")
            self._zoom_to_aoi()

        self._run(f"Computing {index if not expression else 'custom index'}", work, apply)

    def _time_series(self):
        area = self._require_aoi()
        if area is None:
            return
        index = self.index_combo.currentData()
        expression = self.expression_edit.text().strip() or None
        if index != "custom":
            expression = None
        elif not expression:
            self.log("Enter a custom expression first", "error")
            return
        collection = self.collection_combo.currentData()
        start, end = self._dates(self.date_from, self.date_to)
        cloud = self.cloud_spin.value()
        mask = self.mask_check.isChecked()
        commands = self._commands

        def work(task):
            return commands.get_index_time_series(
                {"bbox": area.bbox}, collection, index if not expression else None,
                expression, start, end, cloud, 8, mask)

        def apply(result):
            lines = []
            for row in result["series"]:
                if "mean" in row:
                    cloud = row.get("cloud_cover")
                    cloud_text = f"{cloud:.0f}%" if cloud is not None else "n/a"
                    lines.append(f"{(row.get('datetime') or '')[:10]}  mean "
                                 f"{row['mean']:.3f}  (cloud {cloud_text})")
                else:
                    lines.append(f"{(row.get('datetime') or '')[:10]}  error: {row['error'][:60]}")
            summary = (f"{result['index']} series, {result['scenes']} scenes"
                       + (", truncated" if result.get("truncated") else "")
                       + (f", change {result['change_first_to_last']:+.3f}"
                          if result.get("change_first_to_last") is not None else ""))
            self.index_result.setText(summary + "\n" + "\n".join(lines))
            self.log(summary + "\n" + "\n".join(lines), "success")
            self.tabs.setCurrentIndex(self.tabs.count() - 1)

        self._run(f"Time series of {index}", work, apply)

    # ==================================================================
    # weather
    # ==================================================================
    def _get_weather(self):
        area = self._require_aoi()
        if area is None:
            return
        kind = self.weather_kind.currentData()
        lon, lat = area.center
        start, end = self._dates(self.weather_from, self.weather_to)

        def work(task):
            if kind == "current":
                return weather.current([(lon, lat)])[0]
            if kind == "forecast":
                return weather.forecast([(lon, lat)], days=7)[0]
            if kind == "air_quality":
                return weather.air_quality([(lon, lat)])[0]
            if kind == "history":
                row = weather.history([(lon, lat)], start, end)[0]
                keys = [k for k in (row["daily"] or [{}])[0] if k != "time"]
                row["summary"] = {k: weather.summarise_daily(row["daily"], k) for k in keys}
                return row
            return weather.power_daily(lon, lat, start, end)

        def apply(result):
            self.weather_view.setHtml(self._weather_html(kind, result, area))
            self.log(f"Weather ({kind}) fetched for {area.label or 'area centre'}", "success")

        self._run(f"Fetching weather ({kind})", work, apply)

    @staticmethod
    def _weather_html(kind, result, area):
        html_escape = lambda v: html.escape(str(v))  # noqa: E731
        title = html.escape(area.label or f"{area.center[1]:.4f}, {area.center[0]:.4f}")
        parts = [f"<h3>{title}</h3>"]

        def table(rows, columns):
            html = "<table border='0' cellspacing='0' cellpadding='3'><tr>"
            html += "".join(f"<th align='left'>{c}</th>" for c in columns) + "</tr>"
            for row in rows:
                html += "<tr>" + "".join(
                    f"<td>{'' if row.get(c) is None else html_escape(row.get(c))}</td>"
                    for c in columns) + "</tr>"
            return html + "</table>"

        if kind in ("current", "air_quality"):
            units = result.get("units") or {}
            current = result.get("current") or {}
            rows = [{"variable": k.replace("_", " "), "value": v, "unit": units.get(k, "")}
                    for k, v in current.items() if k not in ("time", "interval")]
            parts.append(f"<p>As of {current.get('time')} ({result.get('timezone', 'UTC')})</p>")
            parts.append(table(rows, ["variable", "value", "unit"]))
        elif kind in ("forecast", "history"):
            daily = result.get("daily") or []
            if daily:
                parts.append(table(daily, list(daily[0].keys())))
            if result.get("summary"):
                parts.append("<h4>Summary</h4>")
                parts.append(table(
                    [dict(variable=k, **v) for k, v in result["summary"].items() if v],
                    ["variable", "mean", "min", "max", "sum"]))
            parts.append(f"<p><small>Units: {result.get('daily_units')}</small></p>")
        else:
            rows = result.get("rows") or []
            parts.append(f"<p>{len(rows)} days, parameters {', '.join(result.get('parameters', []))}</p>")
            summary = {p: weather.summarise_daily(rows, p) for p in result.get("parameters", [])}
            parts.append(table([dict(parameter=k, **v) for k, v in summary.items() if v],
                               ["parameter", "mean", "min", "max", "sum"]))
            parts.append(table(rows[-31:], ["date"] + result.get("parameters", [])))
            parts.append(f"<p><small>Units: {result.get('units')}</small></p>")
        parts.append("<p><small>Source: Open-Meteo (CC BY 4.0) / NASA POWER</small></p>")
        return "".join(parts)

    def _attach_weather(self):
        layer = self.weather_layer_combo.currentLayer()
        if layer is None:
            self.log("Pick a vector layer first", "error")
            return
        if layer.isEditable():
            self.log("Finish editing the layer first (toggle editing off)", "error")
            return
        commands = self._commands
        layer_id = layer.id()
        try:
            _, points, ids = commands._layer_points(layer_id)
        except Exception as exc:
            self.log(str(exc), "error")
            return

        def work(task):
            return weather.current(points)

        def apply(rows):
            if QgsProject.instance().mapLayer(layer_id) is None:
                raise LookupError("The layer was removed while fetching")
            values = [r["current"] or {} for r in rows]
            keys = [k for k in (values[0] if values else {}) if k not in ("time", "interval")]
            provider = layer.dataProvider()
            new_fields = []
            for key in keys:
                fname = f"wx_{key}"[:60]
                if layer.fields().indexOf(fname) < 0:
                    from qgis.PyQt.QtCore import QMetaType
                    sample = values[0].get(key)
                    ftype = QMetaType.Type.Double if isinstance(sample, (int, float)) \
                        else QMetaType.Type.QString
                    new_fields.append(QgsField(fname, ftype))
            if new_fields and not provider.addAttributes(new_fields):
                raise IOError("Layer provider refused new fields; export to GeoPackage first")
            layer.updateFields()
            changes = {fid: {layer.fields().indexOf(f"wx_{k}"[:60]): row.get(k) for k in keys}
                       for fid, row in zip(ids, values)}
            if not provider.changeAttributeValues(changes):
                raise IOError(f"Provider rejected the update: {provider.lastError()}")
            layer.triggerRepaint()
            self.log(f"Attached {len(keys)} weather fields to {len(changes)} features "
                     f"of {layer.name()}", "success")

        self._run(f"Weather for {len(points)} features", work, apply)

    # ==================================================================
    # elevation
    # ==================================================================
    def _add_dem(self):
        area = self._require_aoi()
        if area is None:
            return

        def work(task):
            found = stac.search(area.bbox, "cop-dem-glo-30", None, None, None, 50)
            hrefs = []
            for summary in found["items"]:
                if task.isCanceled():
                    raise RuntimeError("cancelled")
                item = stac.get_item(summary["id"], "cop-dem-glo-30")
                href, _ = stac.asset_href(item, "data")
                hrefs.append(stac.gdal_path(href))
            if not hrefs:
                raise LookupError("No DEM tiles cover this area")
            w, s, e, n = area.bbox
            path = os.path.join(cache_dir(), f"dem_{_safe_name(f'{w:.3f}_{s:.3f}_{e:.3f}_{n:.3f}')}.vrt")
            gdal.BuildVRT(path, hrefs, options=gdal.BuildVRTOptions(outputBounds=[w, s, e, n]))
            ds = gdal.Open(path)
            band = ds.GetRasterBand(1)
            stats = band.ComputeStatistics(True)
            ds = None
            return path, len(hrefs), stats

        def apply(result):
            path, tiles, stats = result
            layer = QgsRasterLayer(path, "Copernicus DEM 30m", "gdal")
            if not layer.isValid():
                raise IOError("Could not open the DEM mosaic")
            self._add_layer(layer, "copernicus-dem", {"bbox": area.bbox, "tiles": tiles})
            self.dem_layer_combo.setLayer(layer)
            text = f"DEM added: {tiles} tile(s), elevation {stats[0]:.0f} to {stats[1]:.0f} m"
            self.dem_result.setText(text)
            self.log(text, "success")

        self._run("Streaming Copernicus DEM", work, apply)

    def _derive_terrain(self):
        layer = self.dem_layer_combo.currentLayer()
        if layer is None:
            self.log("Add or pick a DEM layer first", "error")
            return
        product = self.terrain_combo.currentData()
        source = layer.source()
        geographic = layer.crs().isGeographic()
        centre = layer.extent().center()
        name = layer.name()

        def work(task):
            src = source
            crs = None
            if geographic:
                zone = int((centre.x() + 180) // 6) + 1
                crs = f"EPSG:{(32600 if centre.y() >= 0 else 32700) + zone}"
                utm = os.path.join(cache_dir(), f"utm_{_safe_name(name)}.tif")
                gdal.Warp(utm, src, options=gdal.WarpOptions(
                    dstSRS=crs, resampleAlg="bilinear", format="GTiff",
                    creationOptions=["COMPRESS=DEFLATE", "TILED=YES"]))
                src = utm
            out = os.path.join(cache_dir(), f"{product}_{_safe_name(name)}.tif")
            if os.path.exists(out):
                try:
                    os.remove(out)
                except OSError:
                    out = out[:-4] + f"_{int(_dt.datetime.now().timestamp())}.tif"
            gdal.DEMProcessing(out, src, product, options=gdal.DEMProcessingOptions(
                computeEdges=True, format="GTiff",
                creationOptions=["COMPRESS=DEFLATE", "TILED=YES"]))
            return out, crs

        def apply(result):
            out, crs = result
            new = QgsRasterLayer(out, f"{product} of {name}", "gdal")
            if not new.isValid():
                raise IOError(f"Could not open {out}")
            self._add_layer(new, "copernicus-dem", {"product": product, "from": name})
            text = f"{product} derived" + (f" in {crs}" if crs else "")
            self.dem_result.setText(text)
            self.log(text, "success")

        self._run(f"Deriving {product}", work, apply)

    # ==================================================================
    # OpenStreetMap
    # ==================================================================
    def _osm_preset_changed(self):
        custom = self.osm_preset.currentData() is None
        self.osm_key.setEnabled(custom)
        self.osm_value.setEnabled(custom)

    def _fetch_osm(self):
        area = self._require_aoi()
        if area is None:
            return
        tags = self.osm_preset.currentData()
        if tags is None:
            key = self.osm_key.text().strip()
            value = self.osm_value.text().strip() or "*"
            if not key:
                self.log("Enter a tag key, e.g. amenity", "error")
                return
            tags = {key: value.split("|") if "|" in value else value}
        geometry = self.osm_geometry.currentData()
        limit = self.osm_limit.value()
        if area.area_degrees2() > (1 if geometry == "full" else 4):
            self.log("Area too large for Overpass; shrink it (a few km for full geometry)", "error")
            return
        label = self.osm_preset.currentText() if self.osm_preset.currentData() else \
            "osm_" + "_".join(
                f"{k}_{'any' if v == '*' else (v if isinstance(v, str) else '_'.join(v))}"
                for k, v in tags.items())

        def work(task):
            return osm.fetch(area.bbox, tags, geometry, limit)

        def apply(result):
            query, features, info = result
            if not features:
                self.osm_result.setText("No features with those tags here.")
                self.log("No OSM features found; try another preset or a larger area", "error")
                return
            groups = _split_by_geometry(features)
            made = []
            for gtype, group in groups.items():
                if not group:
                    continue
                layer, count = _memory_layer_from_features(
                    _safe_name(f"{label}_{gtype.lower()}") if geometry == "full" else _safe_name(label),
                    group, gtype)
                layer = self._add_layer(layer, "overpass",
                                        {"tags": tags, "bbox": area.bbox, "query": query})
                made.append(f"{count} {gtype.lower()}s")
            text = f"{label}: {', '.join(made)}" + (" (truncated at limit)" if info["truncated"] else "")
            self.osm_result.setText(text)
            self.log(text, "success")

        self._run(f"Fetching {label} from OpenStreetMap", work, apply)

    # ==================================================================
    # hazards
    # ==================================================================
    def _fetch_quakes(self):
        period = self.quake_period.currentData()
        mag = self.quake_mag.value()
        bbox = None
        if self.quake_in_aoi.isChecked():
            area = self._require_aoi()
            if area is None:
                return
            bbox = area.bbox

        def work(task):
            return hazards.earthquakes(period, mag, bbox)

        def apply(result):
            features = result["features"]
            if not features:
                self.hazard_result.setText("No earthquakes match.")
                self.log("No earthquakes match", "error")
                return
            layer, count = _memory_layer_from_features(f"earthquakes_{period}", features)
            self._add_layer(layer, "usgs-earthquakes", {"period": period, "min_magnitude": mag})
            strongest = max(features, key=lambda f: f["properties"].get("magnitude") or 0)
            text = (f"{count} earthquakes (M>={mag}) in the last {period}; strongest "
                    f"M{strongest['properties']['magnitude']} {strongest['properties']['place']}")
            self.hazard_result.setText(text)
            self.log(text, "success")

        self._run(f"Fetching earthquakes ({period})", work, apply)

    def _fetch_fires(self):
        area = self._require_aoi()
        if area is None:
            return
        key = self._settings.value("qgis_mcp/keys/firms", "")
        days = self.fire_days.value()
        source = self.fire_source.currentData()

        def work(task):
            return hazards.active_fires(key, area.bbox, days, source)

        def apply(result):
            features = result["features"]
            if not features:
                self.hazard_result.setText(f"No active fires in the last {days} day(s).")
                self.log("No active fires detected", "success")
                return
            layer, count = _memory_layer_from_features(f"fires_{source.lower()}_{days}d", features)
            self._add_layer(layer, "nasa-firms", {"bbox": area.bbox, "days": days, "source": source})
            text = f"{count} fire detections in the last {days} day(s)"
            self.hazard_result.setText(text)
            self.log(text, "success")

        self._run("Fetching active fires", work, apply)

    # ==================================================================
    # basemaps and sources
    # ==================================================================
    def _add_basemap(self):
        item = self.basemap_list.currentItem()
        if item is None:
            return
        basemap_id = item.data(Qt.ItemDataRole.UserRole)
        date = self.basemap_date.date().toString("yyyy-MM-dd") \
            if basemap_id in basemaps.GIBS_LAYERS else None
        try:
            self._commands.add_basemap(basemap_id, date, None,
                                       self.basemap_opacity.value() / 100.0)
            self.log(f"Basemap {item.text()} added", "success")
        except Exception as exc:
            self.log(str(exc), "error")

    def populate_sources(self):
        rows = registry.describe_sources(None, self._settings)
        self.sources_table.setRowCount(0)
        for entry in rows:
            row = self.sources_table.rowCount()
            self.sources_table.insertRow(row)
            cells = [entry["name"], entry["category"],
                     "yes" if entry["ready"] else f"needs {entry['auth']}", entry["licence"]]
            for col, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setToolTip(entry["description"])
                cell.setData(Qt.ItemDataRole.UserRole, entry["id"])
                self.sources_table.setItem(row, col, cell)

    def _save_key(self):
        rows = self.sources_table.selectionModel().selectedRows()
        if not rows:
            self.log("Select a source row first", "error")
            return
        source_id = self.sources_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        try:
            self._commands.set_data_source_key(source_id, self.key_edit.text())
            self.key_edit.clear()
            self.populate_sources()
            self.log(f"Key saved for {source_id}", "success")
        except Exception as exc:
            self.log(str(exc), "error")

    # ==================================================================
    # lifecycle
    # ==================================================================
    def closeEvent(self, event):
        if self.draw_button.isChecked():
            self.draw_button.setChecked(False)
        self.closed.emit()
        super().closeEvent(event)

    def hideEvent(self, event):
        if self.draw_button.isChecked():
            self.draw_button.setChecked(False)
        super().hideEvent(event)

    def cleanup(self):
        self._closed = True
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        if self._rubber is not None and self.iface is not None:
            self.iface.mapCanvas().scene().removeItem(self._rubber)
            self._rubber = None
        if self._extent_tool is not None and self.iface is not None:
            canvas = self.iface.mapCanvas()
            try:
                canvas.mapToolSet.disconnect(self._map_tool_changed)
            except TypeError:
                pass
            canvas.unsetMapTool(self._extent_tool)
            self._extent_tool.deleteLater()
            self._extent_tool = None
