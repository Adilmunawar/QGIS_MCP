# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Advanced toolbox commands: climate projections and seasonal outlooks,
elevation profiles, 3D buildings, terrain and point clouds, natural events,
OpenAQ stations, and the live monitor.

Mixed into :class:`QgisMCPServer` beside the other command mixins and used
by the Data Explorer through the same ``*_prepare`` / ``*_apply`` splits.
"""

import datetime as _dt
import hashlib
import json
import math
import os
import re

from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
    QgsMapLayer, QgsPointXY, QgsProject, QgsProperty, QgsPropertyCollection,
    QgsRasterLayer,
)
from qgis.PyQt.QtCore import QTimer

from . import monitor as monitor_mod
from .connectors import airquality, events, stac, weather
from .live_commands import _memory_layer_from_features, _safe_name, cache_dir

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
MAX_PROFILE_SAMPLES = 100
POINT_CLOUD_COLLECTION = "3dep-lidar-copc"


def _haversine_km(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def _height_expression(fields, height_field=None, levels_field=None,
                       default_height=10.0, metres_per_level=3.0):
    """A QGIS expression giving a building height in metres from its tags."""
    names = {f.lower(): f for f in fields}
    height_field = height_field or next((names[n] for n in ("height", "building:height", "building_height")
                                         if n in names), None)
    levels_field = levels_field or next((names[n] for n in ("building:levels", "building_levels", "levels")
                                         if n in names), None)
    parts = []
    if height_field:
        if height_field not in fields:
            raise ValueError(f"Field '{height_field}' is not on the layer")
        parts.append(f"to_real(nullif(regexp_substr(to_string(\"{height_field}\"), '[0-9]+\\\\.?[0-9]*'), ''))")
    if levels_field:
        if levels_field not in fields:
            raise ValueError(f"Field '{levels_field}' is not on the layer")
        parts.append(f"nullif(to_real(nullif(regexp_substr(to_string(\"{levels_field}\"), '[0-9]+\\\\.?[0-9]*'), '')) * {float(metres_per_level):g}, 0)")
    parts.append(f"{float(default_height):g}")
    expr = f"coalesce({', '.join(parts)})" if len(parts) > 1 else parts[0]
    return expr, height_field, levels_field


def opengl_capability():
    """Probe the OpenGL version Qt can give us; 3D views hang without a real context."""
    try:
        from qgis.PyQt.QtGui import QOffscreenSurface, QOpenGLContext
        context = QOpenGLContext()
        if not context.create():
            return {"ok": False, "detail": "no OpenGL context could be created"}
        fmt = context.format()
        surface = QOffscreenSurface()
        surface.setFormat(fmt)
        surface.create()
        current = context.makeCurrent(surface)
        major, minor = fmt.majorVersion(), fmt.minorVersion()
        renderer = None
        if current:
            try:
                from qgis.PyQt.QtOpenGL import QOpenGLVersionFunctionsFactory  # noqa: F401
            except Exception:
                pass
            context.doneCurrent()
        detail = f"OpenGL {major}.{minor}" + (f" ({renderer})" if renderer else "")
        return {"ok": bool(current) and major >= 3, "detail": detail, "major": major, "minor": minor}
    except Exception as exc:
        return {"ok": False, "detail": f"OpenGL probe failed: {exc}"[:200]}


class AdvancedMixin:
    """Climate, profiles, 3D, events, OpenAQ and the live monitor."""

    # ------------------------------------------------------------------
    # climate projections and seasonal outlook
    # ------------------------------------------------------------------
    def get_climate_projection(self, aoi=None, lat=None, lon=None, start_year=2030,
                               end_year=2050, variables=None, models=None):
        """CMIP6 HighResMIP projections (seven models) with an ensemble mean per year."""
        point, label = self._point_or_aoi(aoi, lat, lon)
        row = weather.climate_projection([point], int(start_year), int(end_year),
                                         variables, models)[0]
        row["place"] = label
        row["source"] = "Open-Meteo climate API, CMIP6 HighResMIP (CC BY 4.0)"
        row["interpretation"] = ("Yearly ensemble means across the models; the trend compares the "
                                 "first and last decade of the window. Single years are noisy, "
                                 "decades are the signal.")
        return row

    def get_seasonal_forecast(self, aoi=None, lat=None, lon=None, days=90, variables=None):
        """Ensemble seasonal outlook up to nine months ahead, daily and weekly."""
        point, label = self._point_or_aoi(aoi, lat, lon)
        row = weather.seasonal_forecast([point], int(days), variables)[0]
        row["place"] = label
        row["source"] = "Open-Meteo seasonal API (NCEP CFSv2 ensemble, CC BY 4.0)"
        return row

    # ------------------------------------------------------------------
    # elevation profile
    # ------------------------------------------------------------------
    def profile_points(self, line=None, crs="EPSG:4326", layer_id=None, selected=True,
                       points=None, aoi=None, direction="west-east", samples=50):
        """GUI half: turn a line, a layer feature, points or the AOI into WGS84 samples."""
        samples = max(2, min(int(samples), MAX_PROFILE_SAMPLES))
        geom, label = None, None
        if line:
            geom = QgsGeometry.fromWkt(str(line))
            if geom.isNull() or geom.type() != Qgis.GeometryType.Line:
                raise ValueError("line must be LINESTRING WKT")
            if crs and crs != "EPSG:4326":
                src = QgsCoordinateReferenceSystem(str(crs))
                if not src.isValid():
                    raise ValueError(f"Unknown CRS {crs}")
                geom.transform(QgsCoordinateTransform(src, WGS84, QgsProject.instance()))
            label = "line"
        elif layer_id:
            layer = self._vector_or_raise(layer_id)
            if layer.geometryType() != Qgis.GeometryType.Line:
                raise ValueError("profile needs a line layer; pick a road, river or track layer")
            features = (layer.selectedFeatures() if selected else list(layer.getFeatures()))
            features = [f for f in features if f.hasGeometry()]
            if not features:
                raise ValueError("Select a line feature first (or pass selected=false)")
            geom = QgsGeometry(features[0].geometry())
            if layer.crs() != WGS84:
                geom.transform(QgsCoordinateTransform(layer.crs(), WGS84, QgsProject.instance()))
            label = f"{layer.name()} feature {features[0].id()}"
        elif points:
            pts = [QgsPointXY(float(p[0]), float(p[1])) for p in points]
            if len(pts) < 2:
                raise ValueError("points needs at least two [lon, lat] pairs")
            geom = QgsGeometry.fromPolylineXY(pts)
            label = f"{len(pts)} points"
        else:
            area = self._resolve_aoi(aoi) if aoi else None
            if area is None:
                raise ValueError("Give a line (WKT), a layer_id, points, or an aoi")
            w, s, e, n = area.bbox
            cx, cy = area.center
            if direction == "south-north":
                geom = QgsGeometry.fromPolylineXY([QgsPointXY(cx, s), QgsPointXY(cx, n)])
            else:
                geom = QgsGeometry.fromPolylineXY([QgsPointXY(w, cy), QgsPointXY(e, cy)])
            label = f"{area.label or 'area'}, {direction.replace('-', ' to ')}"
        if geom.isMultipart():
            parts = sorted(geom.asGeometryCollection(), key=lambda g: g.length(), reverse=True)
            geom = parts[0]
        length = geom.length()
        if length <= 0:
            raise ValueError("The line has no length")
        out = []
        for i in range(samples):
            p = geom.interpolate(length * i / (samples - 1)).asPoint()
            out.append((p.x(), p.y()))
        return {"points": out, "label": label, "wkt": geom.asWkt(6)}

    def profile_fetch(self, prepared):
        """Worker half: elevations from Open-Meteo plus distances and statistics."""
        pts = prepared["points"]
        rows = weather.elevation(pts)
        distance = 0.0
        series = []
        for i, row in enumerate(rows):
            if i:
                distance += _haversine_km(pts[i - 1], pts[i])
            series.append({"distance_km": round(distance, 4), "lon": round(row["lon"], 6),
                           "lat": round(row["lat"], 6), "elevation_m": row["elevation_m"]})
        valid = [r for r in series if isinstance(r["elevation_m"], (int, float))]
        gain = loss = 0.0
        for a, b in zip(valid, valid[1:]):
            d = b["elevation_m"] - a["elevation_m"]
            gain += max(d, 0)
            loss += max(-d, 0)
        stats = {}
        if valid:
            elev = [r["elevation_m"] for r in valid]
            stats = {"min_m": min(elev), "max_m": max(elev),
                     "start_m": elev[0], "end_m": elev[-1],
                     "gain_m": round(gain, 1), "loss_m": round(loss, 1),
                     "length_km": round(distance, 3),
                     "mean_grade_percent": round((elev[-1] - elev[0]) / (distance * 1000) * 100, 2)
                     if distance > 0 else None}
        return {"label": prepared["label"], "wkt": prepared["wkt"], "samples": series,
                "statistics": stats,
                "source": "Open-Meteo elevation API (Copernicus DEM 90 m)"}

    def profile_chart(self, result, width=720, height=240):
        """Draw the profile to a PNG in the cache folder (GUI thread)."""
        from qgis.PyQt.QtCore import QPointF, Qt
        from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
        rows = [r for r in result["samples"] if isinstance(r["elevation_m"], (int, float))]
        if len(rows) < 2:
            return None
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right, top, bottom = 56, 12, 12, 28
        lo = min(r["elevation_m"] for r in rows)
        hi = max(r["elevation_m"] for r in rows)
        if hi - lo < 1:
            hi = lo + 1
        span = rows[-1]["distance_km"] or 1
        def xy(r):
            x = left + (width - left - right) * r["distance_km"] / span
            y = top + (height - top - bottom) * (1 - (r["elevation_m"] - lo) / (hi - lo))
            return QPointF(x, y)
        poly = QPolygonF([QPointF(left, height - bottom)] + [xy(r) for r in rows]
                         + [QPointF(width - right, height - bottom)])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(120, 170, 90, 120))
        painter.drawPolygon(poly)
        painter.setPen(QPen(QColor(40, 100, 40), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(QPolygonF([xy(r) for r in rows]))
        painter.setPen(QPen(QColor(90, 90, 90), 1))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawLine(left, top, left, height - bottom)
        painter.drawLine(left, height - bottom, width - right, height - bottom)
        painter.drawText(2, top + 8, f"{hi:.0f} m")
        painter.drawText(2, height - bottom, f"{lo:.0f} m")
        painter.drawText(left, height - 8, "0 km")
        painter.drawText(width - right - 60, height - 8, f"{span:.2f} km")
        painter.end()
        digest = hashlib.sha1(json.dumps([result["wkt"], [r["elevation_m"] for r in rows]]).encode()).hexdigest()[:8]
        path = os.path.join(cache_dir(), f"profile_{digest}.png")
        if not image.save(path):
            return None
        return path

    def get_elevation_profile(self, line=None, crs="EPSG:4326", layer_id=None, selected=True,
                              points=None, aoi=None, direction="west-east", samples=50,
                              add_to_project=False, name=None):
        """Elevation along a line: samples, gain, loss, grade and a chart image."""
        prepared = self.profile_points(line, crs, layer_id, selected, points, aoi, direction, samples)
        result = self.profile_fetch(prepared)
        result["chart_path"] = self.profile_chart(result)
        if add_to_project:
            features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                         "properties": {"distance_km": r["distance_km"], "elevation_m": r["elevation_m"]}}
                        for r in result["samples"]]
            layer, _ = _memory_layer_from_features(name or f"profile_{_safe_name(result['label'])[:30]}", features)
            layer = self._add_layer(layer, "open-meteo", {"profile": result["wkt"][:200]})
            result["layer_id"] = layer.id()
            if self.iface is not None:
                self.iface.mapCanvas().refresh()
        return result

    # ------------------------------------------------------------------
    # 3D: buildings, terrain, point clouds, view
    # ------------------------------------------------------------------
    def make_3d_buildings(self, layer_id, height_field=None, levels_field=None,
                          default_height=10.0, metres_per_level=3.0, colour="#d9c7a3"):
        """Extrude a polygon layer in 3D views and elevation profiles by height tags."""
        from qgis._3d import (QgsPhongMaterialSettings, QgsPolygon3DSymbol,
                              QgsVectorLayer3DRenderer)
        from qgis.PyQt.QtGui import QColor
        layer = self._vector_or_raise(layer_id)
        if layer.geometryType() != Qgis.GeometryType.Polygon:
            raise ValueError("3D buildings need a polygon layer (building footprints)")
        fields = [f.name() for f in layer.fields()]
        expr, hf, lf = _height_expression(fields, height_field, levels_field,
                                          default_height, metres_per_level)
        symbol = QgsPolygon3DSymbol()
        symbol.setExtrusionHeight(float(default_height))
        symbol.setAltitudeClamping(Qgis.AltitudeClamping.Terrain)
        symbol.setAltitudeBinding(Qgis.AltitudeBinding.Centroid)
        props = QgsPropertyCollection()
        props.setProperty(QgsPolygon3DSymbol.PropertyExtrusionHeight, QgsProperty.fromExpression(expr))
        symbol.setDataDefinedProperties(props)
        material = QgsPhongMaterialSettings()
        material.setDiffuse(QColor(colour))
        material.setAmbient(QColor(colour).darker(140))
        symbol.setMaterialSettings(material)
        renderer = QgsVectorLayer3DRenderer(symbol)
        renderer.setLayer(layer)
        layer.setRenderer3D(renderer)
        # The 2D elevation profile tool reads the layer's elevation properties.
        try:
            from qgis.core import QgsMapLayerElevationProperties
            elevation = layer.elevationProperties()
            elevation.setClamping(Qgis.AltitudeClamping.Terrain)
            elevation.setExtrusionEnabled(True)
            elevation.setExtrusionHeight(float(default_height))
            ddp = QgsPropertyCollection()
            ddp.setProperty(QgsMapLayerElevationProperties.Property.ExtrusionHeight,
                            QgsProperty.fromExpression(expr))
            elevation.setDataDefinedProperties(ddp)
        except Exception:  # older API; the 3D renderer alone is still useful
            pass
        layer.triggerRepaint()
        return {"layer_id": layer_id, "name": layer.name(), "features": layer.featureCount(),
                "height_expression": expr, "height_field": hf, "levels_field": lf,
                "default_height_m": float(default_height),
                "note": "Open View > 3D Map Views > New 3D Map View (or open_3d_view) to see the buildings; "
                        "set a DEM as terrain first so they stand on the ground."}

    def add_3d_buildings(self, aoi, name=None, default_height=10.0, metres_per_level=3.0):
        """Fetch OpenStreetMap building footprints for the area and extrude them."""
        result = self.fetch_osm_features(aoi, {"building": True}, geometry="full",
                                         name=name or "buildings")
        polygons = [l for l in result["layers"] if l["geometry"] == "Polygon"]
        if not polygons:
            raise LookupError("OpenStreetMap has no building footprints in this area")
        extruded = self.make_3d_buildings(polygons[0]["layer_id"], None, None,
                                          default_height, metres_per_level)
        extruded.update({"aoi": result["aoi"], "elements": result["elements"],
                         "attribution": result["attribution"], "truncated": result["truncated"]})
        return extruded

    def set_terrain(self, layer_id, vertical_scale=1.0, offset=0.0):
        """Use a raster layer as the project's 3D terrain and profile ground."""
        from qgis.core import QgsRasterDemTerrainProvider
        layer = self._layer_or_raise(layer_id)
        if layer.type() != QgsMapLayer.LayerType.RasterLayer:
            raise ValueError("Terrain needs a raster DEM layer (add_dem or add_dataset nasadem)")
        provider = QgsRasterDemTerrainProvider()
        provider.setLayer(layer)
        provider.setScale(float(vertical_scale))
        provider.setOffset(float(offset))
        QgsProject.instance().elevationProperties().setTerrainProvider(provider)
        try:
            layer.elevationProperties().setEnabled(True)
        except Exception:
            pass
        return {"layer_id": layer_id, "name": layer.name(), "crs": layer.crs().authid(),
                "vertical_scale": float(vertical_scale), "offset": float(offset),
                "note": "New 3D map views and the Elevation Profile panel now use this layer as ground."}

    def point_cloud_prepare(self, bbox, max_tiles=4):
        """Worker half: find USGS 3DEP COPC tiles over the bbox and sign their URLs."""
        found = stac.search(bbox, POINT_CLOUD_COLLECTION, None, None, None,
                            max(1, min(int(max_tiles), 12)), "planetary-computer", keep_assets=True)
        tiles = []
        footprints = set()
        for summary in found["items"]:
            key = tuple(summary.get("bbox") or [summary["id"]])
            if key in footprints:
                continue  # resurveys of the same ground: keep the newest only
            footprints.add(key)
            item = {"id": summary["id"], "collection": summary["collection"],
                    "assets": summary.get("raw_assets") or {}}
            href, _ = stac.asset_href(item, "data", "planetary-computer")
            tiles.append({"id": summary["id"], "datetime": summary["datetime"], "href": href,
                          "bbox": summary["bbox"]})
        if not tiles:
            raise LookupError("No 3DEP lidar point clouds cover this area (United States only); "
                              "pass url= for a COPC file from elsewhere")
        return {"tiles": tiles, "bbox": list(bbox), "matched": found["matched"]}

    def point_cloud_apply(self, prepared, name=None):
        """GUI half: add each COPC tile as a point cloud layer with a 3D renderer."""
        from qgis.core import QgsPointCloudLayer
        added = []
        for tile in prepared["tiles"]:
            layer = QgsPointCloudLayer(tile["href"], name or tile["id"], "copc")
            if not layer.isValid():
                added.append({"id": tile["id"], "error": layer.error().summary()[:200]})
                continue
            self._attach_3d_point_renderer(layer)
            layer = self._add_layer(layer, "usgs-3dep", {"item": tile["id"]}, persist=False)
            added.append({"layer_id": layer.id(), "id": tile["id"], "datetime": tile["datetime"],
                          "points": layer.pointCount()})
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        return added

    @staticmethod
    def _attach_3d_point_renderer(layer):
        try:
            from qgis._3d import QgsPointCloudLayer3DRenderer
            renderer = QgsPointCloudLayer3DRenderer()
            renderer.setLayer(layer)
            renderer.convertFrom2DRenderer(layer.renderer())
            layer.setRenderer3D(renderer)
        except Exception:
            pass

    def add_point_cloud(self, aoi=None, url=None, name=None, max_tiles=4):
        """Add lidar point clouds: USGS 3DEP tiles for the area, or any COPC URL."""
        if url:
            url = str(url)
            if not url.lower().startswith("https://"):
                raise ValueError("url must be an https COPC (.copc.laz) or EPT location")
            prepared = {"tiles": [{"id": name or os.path.basename(url)[:60], "datetime": None,
                                   "href": url, "bbox": None}], "bbox": None, "matched": 1}
        else:
            if aoi is None:
                raise ValueError("Give an aoi (United States) or a url")
            area = self._resolve_aoi(aoi)
            prepared = self.point_cloud_prepare(area.bbox, max_tiles)
        layers = self.point_cloud_apply(prepared, name if url else None)
        ok = [l for l in layers if "layer_id" in l]
        if not ok:
            raise IOError(f"No point cloud could be opened: {layers}")
        return {"layers": layers, "matched": prepared["matched"],
                "note": ("Signed Planetary Computer URLs last about an hour; rerun the command "
                         "after reopening the project." if not url else None),
                "attribution": "USGS 3D Elevation Program" if not url else None}

    def open_3d_view(self, name="QGIS MCP 3D", terrain_layer_id=None, confirm=False):
        """Open a new 3D map view in the QGIS window (needs the GUI).

        Creating a 3D canvas from a script has crashed QGIS on remote desktop
        sessions, so the call is refused until ``confirm`` is true; the same
        view is always available from View > 3D Map Views > New 3D Map View.
        """
        if self.iface is None or not hasattr(self.iface, "createNewMapCanvas3D"):
            raise RuntimeError("A 3D view needs the QGIS window; this bridge is running headless")
        if not confirm:
            raise PermissionError(
                "Opening a 3D view from a script can crash QGIS on machines without a real "
                "GPU (remote desktops). Save the project, then call again with confirm=true, "
                "or use View > 3D Map Views > New 3D Map View in QGIS.")
        gl = opengl_capability()
        if not gl["ok"]:
            raise RuntimeError("3D views need hardware OpenGL 3 or newer; this session reports "
                               f"{gl['detail']}. Remote desktop sessions often lack it: run QGIS "
                               "on the console or set QT_OPENGL=software before starting QGIS.")
        if terrain_layer_id:
            self.set_terrain(terrain_layer_id)
        iface = self.iface
        state = self._view3d_state()
        state.update({"requested": str(name), "opened": None, "error": None})

        def create():
            try:
                widget = iface.createNewMapCanvas3D(str(name))
                state["opened"] = type(widget).__name__ if widget is not None else "unknown"
            except Exception as exc:
                from qgis.core import Qgis as _Qgis, QgsMessageLog
                state["error"] = str(exc)[:200]
                QgsMessageLog.logMessage(f"3D view failed: {exc}", "QGIS MCP", _Qgis.MessageLevel.Warning)
                if iface is not None:
                    iface.messageBar().pushWarning("QGIS MCP", f"3D view failed: {str(exc)[:160]}")

        # Deferred so the bridge answers at once: creating a 3D canvas can take
        # seconds (OpenGL context) and may show a dialog on machines without it.
        QTimer.singleShot(0, create)
        return {"scheduled": True, "name": str(name),
                "terrain": QgsProject.instance().elevationProperties().terrainProvider().type(),
                "note": "The view opens in the QGIS window within a few seconds. Use the mouse "
                        "wheel to zoom and drag with the middle button to tilt. A machine "
                        "without OpenGL (some remote desktops) cannot show 3D views."}

    def _view3d_state(self):
        if not hasattr(self, "_view3d"):
            self._view3d = {}
        return self._view3d

    # ------------------------------------------------------------------
    # natural events and OpenAQ
    # ------------------------------------------------------------------
    def fetch_natural_events(self, aoi=None, categories=None, status="open", days=30,
                             add_to_project=True, name=None):
        """NASA EONET natural events: storms, fires, floods, volcanoes, ice, dust."""
        bbox = self._resolve_aoi(aoi).bbox if aoi else None
        result = events.events(bbox, categories, status, days)
        features = result["features"]
        response = {"count": len(features), "by_category": result["by_category"],
                    "source": result["source"], "days": days, "status": status,
                    "events": [f["properties"] for f in features][:40]}
        if add_to_project and features:
            layer, _ = _memory_layer_from_features(name or "eonet_events", features)
            layer = self._add_layer(layer, "nasa-eonet", {"bbox": bbox, "categories": categories,
                                                          "status": status, "days": days})
            response["layer_id"] = layer.id()
            if self.iface is not None:
                self.iface.mapCanvas().refresh()
        return response

    def fetch_air_quality_stations(self, aoi=None, lat=None, lon=None, radius_km=25,
                                   name=None, limit=100):
        """Reference grade and low cost monitors from OpenAQ (needs a free key)."""
        (lon, lat), label = self._point_or_aoi(aoi, lat, lon)
        key = self._settings().value("qgis_mcp/keys/openaq", "")
        if not key:
            raise PermissionError("OpenAQ needs a free API key: register at "
                                  f"{airquality.REGISTER_URL} and store it with "
                                  "set_data_source_key('openaq', key) or in the Sources tab")
        result = airquality.stations(lon, lat, radius_km, key, limit)
        features = result["features"]
        response = {"centre": [lon, lat], "place": label, "radius_km": radius_km,
                    "summary": result["summary"], "source": result["source"]}
        if features:
            layer, _ = _memory_layer_from_features(name or "openaq_stations", features)
            layer = self._add_layer(layer, "openaq", {"lon": lon, "lat": lat, "radius_km": radius_km})
            response["layer_id"] = layer.id()
            if self.iface is not None:
                self.iface.mapCanvas().refresh()
        else:
            response["hint"] = "No OpenAQ stations within that radius (25 km maximum); try a city centre."
        return response

    # ------------------------------------------------------------------
    # live monitor
    # ------------------------------------------------------------------
    def _monitor_state(self):
        if not hasattr(self, "_monitor"):
            self._monitor = {"config": None, "timer": None, "seen": set(), "alerts": [],
                             "runs": 0, "last_run": None, "last_errors": {}, "layers": {},
                             "last_counts": {}}
        return self._monitor

    def monitor_configure(self, aoi, sources=None, interval_minutes=10, rules=None):
        """Store a configuration (GUI thread); returns it for the fetch half."""
        area = self._resolve_aoi(aoi)
        config = monitor_mod.make_config(area.bbox, area.center, sources, interval_minutes,
                                         rules, area.label)
        state = self._monitor_state()
        project = QgsProject.instance()
        for layer_id in list(state["layers"].values()):
            if project.mapLayer(layer_id) is not None:
                project.removeMapLayer(layer_id)
        state.update({"config": config, "seen": set(), "layers": {}, "last_counts": {},
                      "last_errors": {}, "runs": 0, "last_run": None})
        return config

    def monitor_apply(self, fetched, config=None):
        """GUI half of a tick: refresh layers, evaluate rules, notify.

        ``config`` is the configuration the fetch was made with; a result for
        a configuration that has since been replaced is ignored.
        """
        state = self._monitor_state()
        if state["config"] is None:
            raise RuntimeError("No live monitor configured; call start_live_monitor first")
        if config is not None and config is not state["config"]:
            return {"alerts": [], "counts": dict(state["last_counts"]), "errors": {},
                    "run": state["runs"], "at": state["last_run"], "stale": True}
        config = state["config"]
        project = QgsProject.instance()
        for source, features in fetched["sources"].items():
            old = state["layers"].get(source)
            if old and project.mapLayer(old) is not None:
                project.removeMapLayer(old)
                state["layers"].pop(source, None)
            state["last_counts"][source] = len(features)
            if features:
                layer, _ = _memory_layer_from_features(f"monitor_{source}", features)
                layer = self._add_layer(layer, "live-monitor", {"source": source,
                                                                "bbox": config["bbox"]},
                                        persist=False)
                state["layers"][source] = layer.id()
        alerts = monitor_mod.evaluate(config, fetched, state["seen"])
        state["alerts"] = (state["alerts"] + alerts)[-monitor_mod.MAX_HISTORY:]
        state["runs"] += 1
        state["last_run"] = fetched["fetched_at"]
        state["last_errors"] = fetched.get("errors", {})
        self._notify(alerts)
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        return {"alerts": alerts, "counts": dict(state["last_counts"]),
                "errors": state["last_errors"], "run": state["runs"], "at": state["last_run"]}

    def _notify(self, alerts):
        """One message bar notice per tick for medium and high alerts; all to the audit."""
        audit = getattr(self, "audit", None)
        if audit:
            for alert in alerts:
                try:
                    audit.record("live_monitor_alert", "allow", status="success",
                                 detail={"severity": alert["severity"], "source": alert["source"],
                                         "message": alert["message"][:200]})
                except Exception:
                    pass
        loud = [a for a in alerts if a["severity"] in ("medium", "high")]
        if not loud or self.iface is None:
            return
        text = "; ".join(f"[{a['source']}] {a['message']}" for a in loud[:3])
        if len(loud) > 3:
            text += f"; and {len(loud) - 3} more"
        bar = self.iface.messageBar()
        if any(a["severity"] == "high" for a in loud):
            bar.pushCritical("Live monitor", text[:240])
        else:
            bar.pushWarning("Live monitor", text[:240])

    def _monitor_tick(self, sync=False):
        """Fetch and apply once. Synchronous for the first run and tests; the
        timer runs the fetch in a QgsTask so the window never freezes."""
        state = self._monitor_state()
        config = state["config"]
        if config is None:
            return None
        if sync:
            try:
                return self.monitor_apply(monitor_mod.fetch(config), config)
            except Exception as exc:
                state["last_errors"] = {"tick": str(exc)[:200]}
                raise
        from qgis.core import QgsApplication, QgsTask
        owner = self

        class _Tick(QgsTask):
            def __init__(self):
                super().__init__("Live monitor refresh", QgsTask.Flag.CanCancel)
                self.fetched = None
                self.error = None

            def run(self):
                try:
                    self.fetched = monitor_mod.fetch(config)
                    return True
                except Exception as exc:
                    self.error = str(exc)[:200]
                    return False

            def finished(self, ok):
                try:
                    if ok and self.fetched is not None:
                        owner.monitor_apply(self.fetched, config)
                    else:
                        owner._monitor_state()["last_errors"] = {"tick": self.error or "cancelled"}
                except Exception as exc:
                    owner._monitor_state()["last_errors"] = {"tick": str(exc)[:200]}

        task = _Tick()
        state["task"] = task
        QgsApplication.taskManager().addTask(task)
        return None

    def start_live_monitor(self, aoi, sources=None, interval_minutes=10, rules=None,
                           run_now=True):
        """Refresh hazard, sensor and weather feeds for the area on a timer
        and raise alerts when thresholds are crossed. One monitor at a time."""
        area = self._resolve_aoi(aoi)
        monitor_mod.make_config(area.bbox, area.center, sources, interval_minutes, rules)
        self.stop_live_monitor()
        config = self.monitor_configure({"bbox": area.bbox}, sources, interval_minutes, rules)
        config["label"] = area.label
        state = self._monitor_state()
        timer = QTimer()
        timer.setInterval(config["interval_minutes"] * 60_000)
        timer.timeout.connect(self._monitor_tick)
        timer.start()
        state["timer"] = timer
        first = self._monitor_tick(sync=True) if run_now else None
        return {"started": True, "config": config, "first_run": first,
                "note": "Alerts appear in the QGIS message bar, the audit log and "
                        "get_live_monitor_status. stop_live_monitor ends it."}

    def stop_live_monitor(self):
        state = self._monitor_state()
        timer = state.get("timer")
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        state["timer"] = None
        task = state.pop("task", None)
        if task is not None:
            try:
                task.cancel()
            except Exception:
                pass
        was_running = state["config"] is not None
        state["config"] = None
        return {"stopped": was_running, "runs": state["runs"],
                "alerts_total": len(state["alerts"])}

    def get_live_monitor_status(self, limit=50):
        state = self._monitor_state()
        return {"running": state["config"] is not None and state.get("timer") is not None,
                "config": state["config"], "runs": state["runs"], "last_run": state["last_run"],
                "last_counts": state["last_counts"], "last_errors": state["last_errors"],
                "layers": state["layers"],
                "alerts": state["alerts"][-max(1, min(int(limit), monitor_mod.MAX_HISTORY)):][::-1]}
