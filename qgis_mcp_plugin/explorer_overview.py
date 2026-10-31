# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""Data Explorer Overview tab: one click answers for the current area.

The other tabs are full control panels. This one is the front door: with
the area set (it defaults to the map view), each button pulls real data and
either adds a layer or shows the key numbers, without any settings.
"""

import datetime as _dt
import html

from qgis.core import QgsRasterLayer
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QGridLayout, QGroupBox, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from .connectors import landcover, soil, stac, weather
from .explorer_toolbox import _table
from .live_commands import LiveDataMixin

QUICK_BUTTONS = [
    ("Quick look", "Weather now, forecast, elevation, land cover shares, population and soil "
                   "of the area in one card", "_quick_look"),
    ("Latest satellite image", "Newest clear Sentinel-2 true colour scene streamed over the area", "_quick_image"),
    ("Vegetation (NDVI)", "Crop and vegetation vigour from the newest clear scene, styled", "_quick_ndvi"),
    ("Land cover map", "ESA WorldCover 10 m classes with their shares", "_quick_landcover"),
    ("Elevation model", "Copernicus DEM 30 m for the area", "_quick_dem"),
    ("Surface temperature", "MODIS land surface temperature, latest 8 day period", "_quick_lst"),
    ("Population grid", "Meta 30 m population density with the total for the area", "_quick_population"),
    ("Rainfall, last 30 days", "Daily rain and temperature from the Open-Meteo archive", "_quick_rain"),
    ("Full report (PDF)", "Map, weather, elevation, land cover, soil and NDVI in one document", "_quick_report"),
]


class OverviewTabsMixin:
    """The Overview tab."""

    def _build_overview_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        intro = QLabel("Pick an area above (your map view is used until you choose one), then click. "
                       "Each button adds real data to the project or shows the numbers here. "
                       "The other tabs hold the full controls for every source.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        box = QGroupBox("One click")
        grid = QGridLayout(box)
        for i, (label, tip, slot) in enumerate(QUICK_BUTTONS):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.setMinimumHeight(34)
            button.clicked.connect(getattr(self, slot))
            grid.addWidget(button, i // 3, i % 3)
        layout.addWidget(box)
        self.overview_view = QTextBrowser()
        self.overview_view.setOpenExternalLinks(True)
        self.overview_view.setHtml("<p>Results appear here. Try <b>Quick look</b> first.</p>")
        layout.addWidget(self.overview_view, 1)
        return widget

    def _area_or_view(self):
        """The chosen area, else the current map view."""
        if self._aoi is None and self.iface is not None:
            try:
                self._aoi_from_canvas()
            except Exception:
                pass
        return self._require_aoi()

    # ------------------------------------------------------------------
    def _quick_look(self):
        area = self._area_or_view()
        if area is None:
            return
        lon, lat = area.center
        commands = self._commands

        def work(task):
            out = {"errors": {}}
            steps = [
                ("weather", lambda: weather.current([(lon, lat)])[0]),
                ("forecast", lambda: weather.forecast([(lon, lat)], days=7)[0]),
                ("elevation", lambda: weather.elevation([(lon, lat), (area.bbox[0], area.bbox[1]), (area.bbox[2], area.bbox[3])])),
                ("landcover", lambda: commands.dataset_prepare(area.bbox, "worldcover")["summary"]),
                ("population", lambda: commands.dataset_prepare(area.bbox, "population")["summary"]),
                ("soil", lambda: soil.query(lon, lat)),
            ]
            for key, fn in steps:
                if task.isCanceled():
                    break
                try:
                    out[key] = fn()
                except Exception as exc:
                    out["errors"][key] = str(exc)[:160]
            return out

        def apply(out):
            esc = html.escape
            parts = [f"<h2>{esc(area.label or 'Map view area')}</h2>",
                     f"<p>{esc(self.aoi_label.text().splitlines()[-1])}</p>"]
            w = out.get("weather")
            if w:
                cur = w.get("current") or {}
                units = w.get("units") or {}
                parts.append("<h3>Weather now</h3><p>"
                             f"<b>{cur.get('temperature_2m')} {units.get('temperature_2m', '')}</b>, "
                             f"{esc(str(cur.get('weather_description', '')))}, humidity {cur.get('relative_humidity_2m')} %, "
                             f"wind {cur.get('wind_speed_10m')} {units.get('wind_speed_10m', '')}, "
                             f"rain {cur.get('precipitation')} {units.get('precipitation', '')}</p>")
            f = out.get("forecast")
            if f and f.get("daily"):
                rows = [{"day": r["time"], "max": r.get("temperature_2m_max"), "min": r.get("temperature_2m_min"),
                         "rain mm": r.get("precipitation_sum"), "ET0 mm": r.get("et0_fao_evapotranspiration")}
                        for r in f["daily"]]
                parts.append("<h3>Next 7 days</h3>" + _table(rows, ["day", "max", "min", "rain mm", "ET0 mm"]))
            e = out.get("elevation")
            if e:
                vals = [r["elevation_m"] for r in e if r.get("elevation_m") is not None]
                if vals:
                    parts.append(f"<h3>Elevation</h3><p>Centre <b>{vals[0]:.0f} m</b>; corners {min(vals):.0f} to {max(vals):.0f} m</p>")
            lc = out.get("landcover")
            if lc and lc.get("classes"):
                rows = [{"class": c["label"], "share": f"{c['fraction'] * 100:.1f} %", "km²": c["area_km2"]}
                        for c in lc["classes"][:6]]
                parts.append("<h3>Land cover (ESA WorldCover 2021)</h3>" + _table(rows, ["class", "share", "km²"]))
            pop = out.get("population")
            if pop and pop.get("total") is not None:
                parts.append(f"<h3>Population</h3><p>About <b>{pop['total']:,}</b> people "
                             f"(Meta 30 m grid, {pop.get('area_km2', 0):.1f} km² of inhabited cells)</p>")
            s = out.get("soil")
            if s and s.get("rows"):
                top = {r["property"]: r["value"] for r in s["rows"] if r.get("depth") == "0-5cm" and r.get("value") is not None}
                if top:
                    parts.append(f"<h3>Topsoil (SoilGrids)</h3><p>Texture <b>{esc(str(s.get('texture_class') or 'n/a'))}</b>: "
                                 f"clay {top.get('clay')} %, sand {top.get('sand')} %, silt {top.get('silt')} %, "
                                 f"pH {top.get('phh2o')}, organic carbon {top.get('soc')} g/kg</p>")
            if out["errors"]:
                parts.append("<p><small>Not available: " + esc("; ".join(f"{k}: {v}" for k, v in out["errors"].items())) + "</small></p>")
            parts.append("<p><small>Sources: Open-Meteo, Copernicus DEM, ESA WorldCover, Meta Data for Good, ISRIC SoilGrids.</small></p>")
            self.overview_view.setHtml("".join(parts))
            self.log("Quick look ready", "success")

        self._run("Quick look", work, apply)

    def _quick_image(self):
        area = self._area_or_view()
        if area is None:
            return
        end = _dt.date.today()
        start = end - _dt.timedelta(days=60)

        def work(task):
            found = stac.search(area.bbox, "sentinel-2-l2a", start.isoformat(), end.isoformat(), 25, 5)
            if not found["items"]:
                found = stac.search(area.bbox, "sentinel-2-l2a", (end - _dt.timedelta(days=180)).isoformat(),
                                    end.isoformat(), 40, 5)
            if not found["items"]:
                raise LookupError("No Sentinel-2 scene under 40% cloud in the last six months")
            scene = found["items"][0]
            item = stac.get_item(scene["id"], "sentinel-2-l2a")
            href, _ = stac.asset_href(item, "visual")
            return scene, stac.gdal_path(href)

        def apply(result):
            scene, path = result
            layer = QgsRasterLayer(path, f"Sentinel-2 {scene['datetime'][:10]} true colour", "gdal")
            if not layer.isValid():
                raise IOError("Could not open the scene")
            self._add_layer(layer, "earth-search", {"item": scene["id"], "asset": "visual"})
            self.overview_view.setHtml(
                f"<h3>Sentinel-2 true colour, {html.escape(scene['datetime'][:10])}</h3>"
                f"<p>Scene {html.escape(scene['id'])}, cloud cover {scene['cloud_cover']} %. The layer streams; "
                "zoom in for 10 m detail. Use the Imagery tab to pick other dates or bands.</p>")
            self.log(f"Sentinel-2 scene of {scene['datetime'][:10]} added", "success")
            self._zoom_to_aoi()

        self._run("Latest satellite image", work, apply)

    def _quick_ndvi(self):
        area = self._area_or_view()
        if area is None:
            return
        commands = self._commands

        def work(task):
            end = _dt.date.today()
            try:
                return commands.compute_spectral_index({"bbox": area.bbox}, start_date=(end - _dt.timedelta(days=60)).isoformat(),
                                                       end_date=end.isoformat(), max_cloud=25, breaks=[0.2, 0.4, 0.6],
                                                       add_to_project=False)
            except LookupError:
                return commands.compute_spectral_index({"bbox": area.bbox}, start_date=(end - _dt.timedelta(days=180)).isoformat(),
                                                       end_date=end.isoformat(), max_cloud=40, breaks=[0.2, 0.4, 0.6],
                                                       add_to_project=False)

        def apply(result):
            layer = QgsRasterLayer(result["path"], f"NDVI {(result.get('datetime') or '')[:10]}", "gdal")
            if not layer.isValid():
                raise IOError("Could not open the NDVI raster")
            self._add_layer(layer, "earth-search", {"item": result["item"], "index": "NDVI"})
            LiveDataMixin._style_index_layer(layer, "NDVI", result["statistics"])
            stats = result["statistics"]
            rows = []
            for c in result.get("classes") or []:
                lo, hi = c.get("from"), c.get("to")
                rows.append({"NDVI": (f"below {hi}" if lo is None else f"{lo} and above" if hi is None else f"{lo} to {hi}"),
                             "share": f"{c['fraction'] * 100:.1f} %"})
            self.overview_view.setHtml(
                f"<h3>NDVI from {html.escape((result.get('datetime') or '')[:10])}</h3>"
                f"<p>Mean <b>{stats['mean']:.2f}</b>, median {stats['median']:.2f} (cloud {result.get('cloud_cover')} %). "
                "Above 0.6 dense crop, 0.2 to 0.4 sparse, below 0.1 bare soil or water.</p>" + _table(rows, ["NDVI", "share"]))
            self.log("NDVI layer added", "success")
            self._zoom_to_aoi()

        self._run("Vegetation (NDVI)", work, apply)

    def _quick_dataset(self, dataset, title):
        area = self._area_or_view()
        if area is None:
            return
        commands = self._commands

        def apply(prepared):
            commands.dataset_apply(prepared)
            self._show_dataset_summary(prepared["spec"], prepared["summary"])
            self.overview_view.setHtml(self.land_view.toHtml())
            self.log(f"{prepared['spec']['name']} added", "success")
            self._zoom_to_aoi()

        self._run(title, lambda task: commands.dataset_prepare(area.bbox, dataset), apply)

    def _quick_landcover(self):
        self._quick_dataset("worldcover", "Land cover map")

    def _quick_lst(self):
        self._quick_dataset("land-surface-temperature", "Surface temperature")

    def _quick_population(self):
        self._quick_dataset("population", "Population grid")

    def _quick_dem(self):
        area = self._area_or_view()
        if area is None:
            return
        self._add_dem()

    def _quick_rain(self):
        area = self._area_or_view()
        if area is None:
            return
        lon, lat = area.center
        end = _dt.date.today() - _dt.timedelta(days=1)
        start = end - _dt.timedelta(days=30)

        def work(task):
            row = weather.history([(lon, lat)], start.isoformat(), end.isoformat())[0]
            daily = row.get("daily") or []
            keys = [k for k in (daily[0] if daily else {}) if k != "time"]
            row["daily"] = daily
            row["summary"] = {k: weather.summarise_daily(daily, k) for k in keys}
            return row

        def apply(row):
            s = row["summary"]
            daily = row["daily"]
            rain = s.get("precipitation_sum") or {}
            tmax = s.get("temperature_2m_max") or {}
            wet_days = sum(1 for r in daily if (r.get("precipitation_sum") or 0) > 0.5)
            parts = [f"<h3>Last 30 days at {lat:.3f}, {lon:.3f}</h3>",
                     f"<p>Rain total <b>{rain.get('sum') or 0:.1f} mm</b> on {wet_days} days; "
                     f"hottest day {tmax.get('max')} °C, mean maximum {tmax.get('mean')} °C</p>",
                     _table(daily, ["time", "temperature_2m_max", "temperature_2m_min", "precipitation_sum", "et0_fao_evapotranspiration"])
                     if daily else "<p>No daily archive rows came back for this point.</p>",
                     "<p><small>Open-Meteo archive (ERA5)</small></p>"]
            self.overview_view.setHtml("".join(parts))
            self.log("Last 30 days fetched", "success")

        self._run("Rainfall, last 30 days", work, apply)

    def _quick_report(self):
        area = self._area_or_view()
        if area is None:
            return
        commands = self._commands
        import os
        from .live_commands import _safe_name, cache_dir
        pdf = os.path.join(cache_dir(), f"area_report_{_safe_name(area.label or 'map_view')[:30]}.pdf")

        def work(task):
            return commands.export_area_report({"bbox": area.bbox}, path=pdf[:-4] + ".html", render_map=False,
                                               title=area.label or "Area report")

        def apply(result):
            from .toolbox_commands import report_to_pdf
            report_to_pdf(result["html_path"], pdf)
            self.overview_view.setHtml(f"<h3>Report written</h3><p>{html.escape(pdf)}</p>"
                                       f"<p>Sections: {html.escape(', '.join(result['sections']))}</p>")
            QDesktopServices.openUrl(QUrl.fromLocalFile(pdf))
            self.log(f"PDF report written to {pdf}", "success")

        self._run("Full report (PDF)", work, apply)
