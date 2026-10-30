# -*- coding: utf-8 -*-
"""Live-data commands: imagery, indices, weather, places, OSM, hazards.

Mixed into :class:`QgisMCPServer`. Each command resolves an area of interest
through :mod:`aoi`, calls one connector, and turns the answer into either a
QGIS layer or plain JSON. Fetched layers carry provenance in their metadata
so a map an agent builds can be traced back to its sources.
"""

import datetime as _dt
import hashlib
import json
import os
import re
import time

from osgeo import gdal
from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFeature, QgsField, QgsGeometry, QgsMapLayer, QgsProject,
    QgsRasterLayer, QgsSettings, QgsVectorLayer,
)
from qgis.PyQt.QtCore import QMetaType

from . import aoi as aoi_mod
from .connectors import (basemaps, geocode, hazards, indices, osm, registry,
                         stac, weather)

CACHE_DIRNAME = "qgis_mcp_cache"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def cache_dir():
    path = os.path.join(QgsApplication.qgisSettingsDirPath(), CACHE_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


def _safe_name(text):
    return _SAFE.sub("_", str(text)).strip("_")[:80] or "layer"


MAX_ROWS = 400


def _output_tif(output_path, default_name):
    """Choose a GeoTIFF path: the caller's, or a cache file that is free."""
    if output_path:
        if os.path.splitext(str(output_path))[1].lower() not in (".tif", ".tiff"):
            raise ValueError("output_path must end in .tif")
        return str(output_path)
    base = os.path.join(cache_dir(), default_name)
    candidate = base + ".tif"
    n = 1
    while os.path.exists(candidate):
        try:
            os.remove(candidate)
            break
        except OSError:  # still open in a layer; pick a sibling name
            candidate = f"{base}_{n}.tif"
            n += 1
    return candidate


def _cap_rows(rows, include_rows=True):
    if not rows or not include_rows:
        return None, bool(rows)
    if len(rows) > MAX_ROWS:
        return rows[:MAX_ROWS], True
    return rows, False


def _stamp_provenance(layer, source_id, query, licence=None, attribution=None):
    """Record where a layer came from, in metadata and custom properties."""
    entry = registry.REGISTRY.get(source_id, {})
    meta = layer.metadata()
    meta.setIdentifier(f"qgis-mcp:{source_id}")
    meta.setTitle(layer.name())
    meta.setAbstract(
        f"Fetched by QGIS MCP from {entry.get('name', source_id)} on "
        f"{_dt.datetime.now().isoformat(timespec='seconds')}.\n"
        f"Query: {json.dumps(query, default=str)}")
    meta.setRights([licence or entry.get("licence", "")])
    layer.setMetadata(meta)
    _set_attribution(layer, attribution or entry.get("attribution", ""))
    layer.setCustomProperty("qgis_mcp/source", source_id)
    layer.setCustomProperty("qgis_mcp/query", json.dumps(query, default=str))
    layer.setCustomProperty("qgis_mcp/fetched", time.time())


def _set_attribution(layer, text):
    props = getattr(layer, "serverProperties", None)
    if callable(props):
        props().setAttribution(text)
    else:  # QGIS < 3.38
        layer.setAttribution(text)


def _memory_layer_from_features(name, features, geometry_type="Point",
                                crs="EPSG:4326"):
    """Build a memory layer from GeoJSON-like features with mixed properties."""
    keys = []
    seen = set()
    for f in features:
        for k in (f.get("properties") or {}):
            if k not in seen:
                seen.add(k)
                keys.append(k)
    fields = []
    for key in keys:
        sample = next((f["properties"][key] for f in features
                       if f.get("properties", {}).get(key) is not None), None)
        if isinstance(sample, bool):
            ftype = QMetaType.Type.Bool
        elif isinstance(sample, int):
            ftype = QMetaType.Type.LongLong
        elif isinstance(sample, float):
            ftype = QMetaType.Type.Double
        else:
            ftype = QMetaType.Type.QString
        fields.append(QgsField(key, ftype))
    layer = QgsVectorLayer(f"{geometry_type}?crs={crs}", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes(fields)
    layer.updateFields()
    out = []
    for f in features:
        geom = f.get("geometry")
        if geom is None:
            continue
        feature = QgsFeature(layer.fields())
        qgeom = QgsGeometry.fromWkt(_geojson_to_wkt(geom))
        if qgeom.isNull():
            continue
        feature.setGeometry(qgeom)
        props = f.get("properties") or {}
        feature.setAttributes([
            _coerce(props.get(k), fields[i].type()) for i, k in enumerate(keys)])
        out.append(feature)
    provider.addFeatures(out)
    layer.updateExtents()
    return layer, len(out)


def persist_layer(layer, stem):
    """Write a memory layer to a GeoPackage in the cache and reload it.

    Memory layers vanish when a project is saved and reopened; a fetched
    dataset should still be there tomorrow.
    """
    from qgis.core import QgsVectorFileWriter
    path = os.path.join(cache_dir(), f"{_safe_name(stem)}.gpkg")
    n = 1
    while os.path.exists(path):
        path = os.path.join(cache_dir(), f"{_safe_name(stem)}_{n}.gpkg")
        n += 1
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = _safe_name(stem)[:60]
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, path, QgsProject.instance().transformContext(), options)
    if result[0] != QgsVectorFileWriter.WriterError.NoError:
        return layer  # keep the memory layer rather than lose the data
    stored = QgsVectorLayer(f"{path}|layername={options.layerName}", layer.name(), "ogr")
    return stored if stored.isValid() else layer


def _coerce(value, ftype):
    if value is None:
        return None
    if ftype == QMetaType.Type.QString and not isinstance(value, str):
        return json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    return value


def _geojson_to_wkt(geom):
    kind = geom["type"]
    coords = geom["coordinates"]

    def ring(points):
        return "(" + ", ".join(f"{p[0]} {p[1]}" for p in points) + ")"

    if kind == "Point":
        return f"POINT({coords[0]} {coords[1]})"
    if kind == "LineString":
        return "LINESTRING" + ring(coords)
    if kind == "Polygon":
        return "POLYGON(" + ", ".join(ring(r) for r in coords) + ")"
    if kind == "MultiPolygon":
        return "MULTIPOLYGON(" + ", ".join(
            "(" + ", ".join(ring(r) for r in poly) + ")" for poly in coords) + ")"
    if kind == "MultiPoint":
        return "MULTIPOINT(" + ", ".join(f"({p[0]} {p[1]})" for p in coords) + ")"
    if kind == "MultiLineString":
        return "MULTILINESTRING(" + ", ".join(ring(l) for l in coords) + ")"
    raise ValueError(f"Unsupported geometry type {kind}")


def _split_by_geometry(features):
    groups = {"Point": [], "LineString": [], "Polygon": []}
    for f in features:
        geom = f.get("geometry")
        if not geom:
            continue
        kind = geom["type"]
        if kind in ("Point", "MultiPoint"):
            groups["Point"].append(f)
        elif kind in ("LineString", "MultiLineString"):
            groups["LineString"].append(f)
        else:
            groups["Polygon"].append(f)
    return groups


class LiveDataMixin:
    """Commands that bring outside data into the project."""

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _resolve_aoi(self, spec):
        return aoi_mod.resolve(spec, iface=self.iface, geocoder=geocode.bbox_for)

    def _layer_points(self, layer_id, limit=weather.MAX_POINTS):
        """Centroids of a vector layer's features in WGS84, with their ids."""
        layer = self._vector_or_raise(layer_id)
        transform = None
        if layer.crs().isValid() and layer.crs() != aoi_mod.WGS84:
            transform = QgsCoordinateTransform(layer.crs(), aoi_mod.WGS84,
                                               QgsProject.instance())
        points, ids = [], []
        for feature in layer.getFeatures():
            if not feature.hasGeometry():
                continue
            centroid = feature.geometry().centroid()
            if transform is not None:
                centroid.transform(transform)
            p = centroid.asPoint()
            points.append((p.x(), p.y()))
            ids.append(feature.id())
            if len(points) >= limit:
                break
        if not points:
            raise ValueError(f"Layer {layer_id} has no features with geometry")
        return layer, points, ids

    def _add_layer(self, layer, source_id, query, name=None, persist=True):
        if name:
            layer.setName(name)
        if persist and layer.type() == QgsMapLayer.LayerType.VectorLayer \
                and layer.providerType() == "memory":
            layer = persist_layer(layer, layer.name())
        _stamp_provenance(layer, source_id, query)
        QgsProject.instance().addMapLayer(layer)
        return layer

    @staticmethod
    def _settings():
        return QgsSettings()

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------
    def list_data_sources(self, category=None):
        if category and category not in registry.CATEGORIES:
            raise ValueError(f"Unknown category '{category}'. "
                             f"Use one of {sorted(registry.CATEGORIES)}")
        rows = registry.describe_sources(category, self._settings())
        return {"categories": registry.CATEGORIES, "count": len(rows),
                "sources": rows}

    def resolve_aoi(self, aoi):
        """Preview how an area-of-interest spec will be interpreted."""
        result = self._resolve_aoi(aoi)
        return result.to_dict()

    # ------------------------------------------------------------------
    # places
    # ------------------------------------------------------------------
    def geocode(self, query, limit=5, country_codes=None):
        results = geocode.search(query, limit=limit, country_codes=country_codes)
        response = {"query": query, "count": len(results), "results": results,
                    "attribution": "© OpenStreetMap contributors (ODbL)"}
        if not results:
            response["hint"] = ("No match. Check the spelling, try a broader name "
                                "(city or district), or add the country.")
        return response

    def reverse_geocode(self, lat, lon, zoom=14):
        return geocode.reverse(float(lat), float(lon), zoom=zoom)

    def add_place_marker(self, query, name=None):
        results = geocode.search(query, limit=1, polygon=True)
        if not results:
            raise LookupError(f"Place not found: {query}")
        best = results[0]
        feature = {"type": "Feature",
                   "geometry": best.get("geojson") or
                   {"type": "Point", "coordinates": [best["lon"], best["lat"]]},
                   "properties": {"name": best["name"],
                                  "display_name": best["display_name"],
                                  "type": best["type"], "osm": best["osm"]}}
        groups = _split_by_geometry([feature])
        gtype = next(k for k, v in groups.items() if v)
        layer, count = _memory_layer_from_features(
            name or _safe_name(best["name"]), [feature], gtype)
        layer = self._add_layer(layer, "nominatim", {"query": query})
        return {"layer_id": layer.id(), "name": layer.name(),
                "geometry": gtype, "place": best}

    # ------------------------------------------------------------------
    # imagery
    # ------------------------------------------------------------------
    def search_imagery(self, aoi, collection="sentinel-2-l2a", start_date=None,
                       end_date=None, max_cloud=20, limit=10,
                       catalogue="earth-search"):
        area = self._resolve_aoi(aoi)
        if collection not in stac.COLLECTIONS:
            raise ValueError(f"Unknown collection '{collection}'. Use one of "
                             f"{sorted(stac.COLLECTIONS)}")
        if start_date is None and end_date is None:
            end_date = _dt.date.today().isoformat()
            start_date = (_dt.date.today() - _dt.timedelta(days=60)).isoformat()
        result = stac.search(area.bbox, collection, start_date, end_date,
                             max_cloud, limit, catalogue)
        if not result["items"]:
            result["hint"] = ("No scenes. Widen the date range, raise max_cloud, "
                              "or check the AOI with resolve_aoi.")
        result["aoi"] = area.to_dict()
        result["date_range"] = [start_date, end_date]
        result["collections_available"] = {
            k: v["description"] for k, v in stac.COLLECTIONS.items()}
        return result

    def add_imagery(self, item_id, collection="sentinel-2-l2a", assets=None,
                    catalogue="earth-search", name=None):
        item = stac.get_item(item_id, collection, catalogue)
        spec = stac.COLLECTIONS.get(item.get("collection"), {})
        keys = assets or [spec.get("display", "visual")]
        added = []
        for key in keys:
            href, asset = stac.asset_href(item, key, catalogue)
            layer = QgsRasterLayer(stac.gdal_path(href),
                                   name or f"{item_id} {key}", "gdal")
            if not layer.isValid():
                raise ValueError(f"GDAL could not open asset {key}: {href}")
            layer = self._add_layer(layer, catalogue,
                            {"item": item_id, "collection": collection,
                             "asset": key, "href": href})
            added.append({"layer_id": layer.id(), "name": layer.name(),
                          "asset": key, "title": asset.get("title"),
                          "crs": layer.crs().authid(),
                          "width": layer.width(), "height": layer.height(),
                          "href": href})
        if self.iface is not None and added:
            self.iface.mapCanvas().refresh()
        return {"item": item_id, "datetime": item.get("properties", {}).get("datetime"),
                "cloud_cover": item.get("properties", {}).get("eo:cloud_cover"),
                "layers": added,
                "note": "Streamed from the cloud; zoom in for full resolution."}

    def compute_spectral_index(self, aoi, item_id=None, collection="sentinel-2-l2a",
                               index="NDVI", expression=None, mask_clouds=True,
                               add_to_project=True, output_path=None,
                               catalogue="earth-search", max_cloud=20,
                               start_date=None, end_date=None, breaks=None):
        """Compute an index over the AOI for one scene, or the best recent one."""
        # Validate cheap inputs before the network is touched.
        spec = stac.COLLECTIONS.get(collection)
        if spec is None:
            raise ValueError(f"Unknown collection '{collection}'. Use one of "
                             f"{sorted(stac.COLLECTIONS)}")
        indices.resolve_index(index, expression, list(spec.get("bands") or {}))
        if breaks is not None:
            indices.classify(__import__("numpy").zeros((1, 1)), breaks)
        area = self._resolve_aoi(aoi)
        if item_id is None:
            if start_date is None and end_date is None:
                end_date = _dt.date.today().isoformat()
                start_date = (_dt.date.today() - _dt.timedelta(days=90)).isoformat()
            found = stac.search(area.bbox, collection, start_date, end_date,
                                max_cloud, 1, catalogue)
            if not found["items"]:
                raise LookupError("No scene matches; widen the dates or raise max_cloud")
            item_id = found["items"][0]["id"]
        item = stac.get_item(item_id, collection, catalogue)
        result = indices.compute(item, area.bbox, index, expression,
                                 mask_clouds, catalogue)
        label = result["index"]
        digest = hashlib.sha1(json.dumps(
            [area.bbox, result["expression"], bool(mask_clouds)]).encode()).hexdigest()[:8]
        path = _output_tif(output_path,
                           f"{_safe_name(label)}_{_safe_name(item_id)}_{digest}")
        indices.write_geotiff(path, result["array"], result["transform"],
                              result["projection"])
        response = {
            "index": label, "expression": result["expression"],
            "item": item_id, "datetime": item.get("properties", {}).get("datetime"),
            "cloud_cover": item.get("properties", {}).get("eo:cloud_cover"),
            "aoi": area.to_dict(), "resolution_m": result["resolution"],
            "coarsened": result["coarsened"],
            "path": path, "statistics": result["statistics"],
        }
        if breaks:
            response["classes"] = indices.classify(result["array"], breaks)
        if label in indices.INDICES:
            response["interpretation"] = indices.INDICES[label][3]
        if add_to_project:
            layer = QgsRasterLayer(path, f"{label} {item_id}", "gdal")
            if not layer.isValid():
                raise IOError(f"Could not open the written index at {path}")
            layer = self._add_layer(layer, catalogue, {"item": item_id, "index": label,
                                               "expression": result["expression"]})
            self._style_index_layer(layer, label, result["statistics"])
            response["layer_id"] = layer.id()
            if self.iface is not None:
                self.iface.mapCanvas().refresh()
        return response

    @staticmethod
    def _style_index_layer(layer, label, stats):
        """A sensible colour ramp so the result reads at a glance."""
        from qgis.core import (QgsColorRampShader, QgsRasterShader,
                               QgsSingleBandPseudoColorRenderer)
        from qgis.PyQt.QtGui import QColor
        vegetation = label in ("NDVI", "EVI", "SAVI", "NDMI")
        water = label in ("NDWI", "MNDWI")
        if vegetation:
            stops = [(-0.2, "#a50026"), (0.1, "#f46d43"), (0.3, "#fee08b"),
                     (0.5, "#a6d96a"), (0.7, "#1a9850"), (0.9, "#006837")]
        elif water:
            stops = [(-0.5, "#f7f7f7"), (0.0, "#c6dbef"), (0.3, "#4292c6"),
                     (0.6, "#08306b")]
        else:
            lo = stats.get("min") if stats.get("min") is not None else -1
            hi = stats.get("max") if stats.get("max") is not None else 1
            stops = [(lo, "#2c7bb6"), ((lo + hi) / 2, "#ffffbf"), (hi, "#d7191c")]
        shader = QgsRasterShader()
        ramp = QgsColorRampShader(stops[0][0], stops[-1][0])
        ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
        ramp.setColorRampItemList([
            QgsColorRampShader.ColorRampItem(v, QColor(c), f"{v:.2f}")
            for v, c in stops])
        shader.setRasterShaderFunction(ramp)
        renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
        renderer.setClassificationMin(stops[0][0])
        renderer.setClassificationMax(stops[-1][0])
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    def get_index_time_series(self, aoi, collection="sentinel-2-l2a",
                              index="NDVI", expression=None, start_date=None,
                              end_date=None, max_cloud=30, max_scenes=8,
                              mask_clouds=True, catalogue="earth-search",
                              time_budget_s=150):
        """Mean index per scene across a date range, oldest first.

        Stops at ``time_budget_s`` and reports ``truncated`` so the client
        never waits past its own timeout.
        """
        area = self._resolve_aoi(aoi)
        max_scenes = max(1, min(int(max_scenes), 24))
        started = time.monotonic()
        if end_date is None:
            end_date = _dt.date.today().isoformat()
        if start_date is None:
            start_date = (_dt.date.fromisoformat(end_date)
                          - _dt.timedelta(days=180)).isoformat()
        found = stac.search(area.bbox, collection, start_date, end_date,
                            max_cloud, max_scenes * 2, catalogue)
        # Reprocessed scenes share a datetime (suffix _0, _1, ...); keep the
        # highest processing number, which is the latest reprocessing.
        by_time = {}
        for summary in found["items"]:
            key = (summary["datetime"] or "")[:16]
            if key not in by_time or summary["id"] > by_time[key]["id"]:
                by_time[key] = summary
        items = [by_time[k] for k in sorted(by_time)]
        rows = []
        truncated = False
        failures = 0
        for summary in items:
            if len(rows) >= max_scenes:
                truncated = True
                break
            if time.monotonic() - started > time_budget_s:
                truncated = True
                break
            if failures >= 2:
                break
            try:
                item = stac.get_item(summary["id"], collection, catalogue)
                result = indices.compute(item, area.bbox, index, expression,
                                         mask_clouds, catalogue)
            except Exception as exc:  # one bad scene must not sink the series
                failures += 1
                rows.append({"item": summary["id"], "datetime": summary["datetime"],
                             "error": str(exc)[:200]})
                continue
            stats = result["statistics"]
            if stats["valid_pixels"] == 0:
                continue
            rows.append({
                "item": summary["id"], "datetime": summary["datetime"],
                "cloud_cover": summary["cloud_cover"],
                "mean": round(stats["mean"], 4), "median": round(stats["median"], 4),
                "min": round(stats["min"], 4), "max": round(stats["max"], 4),
                "valid_fraction": round(stats["valid_pixels"] / stats["total_pixels"], 3),
            })
        valid = [r for r in rows if "mean" in r]
        trend = None
        if len(valid) >= 2:
            trend = round(valid[-1]["mean"] - valid[0]["mean"], 4)
        return {"index": index if not expression else "custom",
                "aoi": area.to_dict(), "date_range": [start_date, end_date],
                "scenes": len(rows), "series": rows,
                "truncated": truncated or len(items) > len(rows),
                "scenes_available": len(items),
                "elapsed_s": round(time.monotonic() - started, 1),
                "change_first_to_last": trend,
                "interpretation": indices.INDICES.get(str(index).upper(), ("", [], (), ""))[3]}

    # ------------------------------------------------------------------
    # elevation
    # ------------------------------------------------------------------
    def add_dem(self, aoi, name=None, catalogue="earth-search"):
        """Stream Copernicus DEM 30 m tiles covering the AOI as one VRT."""
        area = self._resolve_aoi(aoi)
        found = stac.search(area.bbox, "cop-dem-glo-30", None, None, None, 50,
                            catalogue)
        hrefs = []
        for summary in found["items"]:
            item = stac.get_item(summary["id"], "cop-dem-glo-30", catalogue)
            href, _ = stac.asset_href(item, "data", catalogue)
            hrefs.append(stac.gdal_path(href))
        if not hrefs:
            raise LookupError("No DEM tiles cover this area")
        west, south, east, north = area.bbox
        vrt_path = os.path.join(cache_dir(),
                                f"dem_{_safe_name(f'{west:.3f}_{south:.3f}_{east:.3f}_{north:.3f}')}.vrt")
        gdal.BuildVRT(vrt_path, hrefs,
                      options=gdal.BuildVRTOptions(outputBounds=[west, south, east, north]))
        layer = QgsRasterLayer(vrt_path, name or "Copernicus DEM 30m", "gdal")
        if not layer.isValid():
            raise IOError("Could not open the DEM mosaic")
        layer = self._add_layer(layer, "copernicus-dem", {"bbox": area.bbox, "tiles": len(hrefs)})
        from qgis.core import QgsRasterBandStats
        stats = layer.dataProvider().bandStatistics(
            1, QgsRasterBandStats.Stats.Min | QgsRasterBandStats.Stats.Max,
            layer.extent(), 250_000)
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        return {"layer_id": layer.id(), "name": layer.name(), "tiles": len(hrefs),
                "path": vrt_path, "crs": layer.crs().authid(),
                "elevation_min": stats.minimumValue, "elevation_max": stats.maximumValue,
                "aoi": area.to_dict()}

    def derive_terrain(self, layer_id, product="slope", output_path=None,
                       add_to_project=True):
        """Slope, aspect, hillshade, roughness, TRI or TPI from a DEM layer.

        A geographic DEM is projected to its UTM zone first, so horizontal
        and vertical units agree and slopes are true at any latitude.
        """
        products = ("slope", "aspect", "hillshade", "roughness", "TRI", "TPI")
        if product not in products:
            raise ValueError(f"product must be one of {products}")
        layer = self._layer_or_raise(layer_id)
        if layer.type() != QgsMapLayer.LayerType.RasterLayer:
            raise ValueError("derive_terrain needs a raster (DEM) layer")
        src = layer.source()
        path = _output_tif(output_path, f"{product}_{_safe_name(layer.name())}")
        projected_crs = None
        if layer.crs().isGeographic():
            centre = layer.extent().center()
            zone = int((centre.x() + 180) // 6) + 1
            epsg = (32600 if centre.y() >= 0 else 32700) + zone
            projected_crs = f"EPSG:{epsg}"
            utm_path = _output_tif(None, f"utm_{_safe_name(layer.name())}")
            gdal.Warp(utm_path, src, options=gdal.WarpOptions(
                dstSRS=projected_crs, resampleAlg="bilinear", format="GTiff",
                creationOptions=["COMPRESS=DEFLATE", "TILED=YES"]))
            src = utm_path
        opts = gdal.DEMProcessingOptions(
            computeEdges=True, format="GTiff",
            creationOptions=["COMPRESS=DEFLATE", "TILED=YES"])
        gdal.DEMProcessing(path, src, product, options=opts)
        response = {"product": product, "source_layer": layer_id, "path": path,
                    "crs": projected_crs or layer.crs().authid()}
        if add_to_project:
            out = QgsRasterLayer(path, f"{product} of {layer.name()}", "gdal")
            if not out.isValid():
                raise IOError(f"Could not open {path}")
            self._add_layer(out, "copernicus-dem", {"product": product, "from": layer_id})
            response["layer_id"] = out.id()
            if self.iface is not None:
                self.iface.mapCanvas().refresh()
        return response

    # ------------------------------------------------------------------
    # weather
    # ------------------------------------------------------------------
    def _point_or_aoi(self, aoi, lat, lon):
        if lat is not None and lon is not None:
            return (float(lon), float(lat)), None
        if aoi is None:
            raise ValueError("Pass lat and lon, or an aoi such as "
                             '{"place": "Multan"} or {"bbox": [w, s, e, n]}')
        area = self._resolve_aoi(aoi)
        return tuple(area.center), area.label

    def get_weather(self, aoi=None, lat=None, lon=None, kind="current",
                    days=7, variables=None, hourly=None):
        """Current conditions or a forecast for a point or an AOI centre."""
        if kind not in ("current", "forecast", "air_quality"):
            raise ValueError("kind must be current, forecast or air_quality")
        point, label = self._point_or_aoi(aoi, lat, lon)
        if kind == "current":
            rows = weather.current([point], variables)
        elif kind == "forecast":
            rows = weather.forecast([point], variables, hourly, days)
        elif kind == "air_quality":
            rows = weather.air_quality([point], variables)
        else:
            raise ValueError("kind must be current, forecast or air_quality")
        row = rows[0]
        row["place"] = label
        row["source"] = "Open-Meteo (CC BY 4.0)"
        return row

    def get_weather_history(self, start_date, end_date, aoi=None, lat=None,
                            lon=None, daily=None, hourly=None, summarise=True,
                            include_rows=True):
        point, label = self._point_or_aoi(aoi, lat, lon)
        row = weather.history([point], start_date, end_date, daily, hourly)[0]
        row["place"] = label
        row["source"] = "Open-Meteo ERA5/ECMWF archive (CC BY 4.0)"
        if summarise and row.get("daily"):
            keys = [k for k in row["daily"][0] if k != "time"]
            row["summary"] = {k: weather.summarise_daily(row["daily"], k) for k in keys}
        row["daily"], row["daily_truncated"] = _cap_rows(row.get("daily"), include_rows)
        row["hourly"], row["hourly_truncated"] = _cap_rows(row.get("hourly"), include_rows)
        return row

    def get_climate_series(self, start_date, end_date, aoi=None, lat=None,
                           lon=None, parameters=None, include_rows=True):
        """NASA POWER daily agro-climatology, with per-parameter summaries."""
        (lon, lat), label = self._point_or_aoi(aoi, lat, lon)
        result = weather.power_daily(lon, lat, start_date, end_date, parameters)
        result["place"] = label
        result["summary"] = {p: weather.summarise_daily(result["rows"], p)
                             for p in result["parameters"]}
        result["row_count"] = len(result["rows"])
        result["rows"], result["rows_truncated"] = _cap_rows(result["rows"], include_rows)
        result["source"] = "NASA POWER (public domain)"
        return result

    def attach_weather_to_layer(self, layer_id, kind="current", variables=None,
                                date=None, prefix="wx_"):
        """Add weather fields to every feature of a layer, by its centroid."""
        layer, points, ids = self._layer_points(layer_id)
        total_features = layer.featureCount()
        if kind == "current":
            rows = weather.current(points, variables)
            values = [r["current"] or {} for r in rows]
        elif kind == "history":
            if not date:
                raise ValueError("kind='history' needs a date (YYYY-MM-DD)")
            rows = weather.history(points, date, date, variables)
            values = [(r["daily"] or [{}])[0] for r in rows]
        else:
            raise ValueError("kind must be current or history")
        keys = [k for k in (values[0] if values else {}) if k not in ("time", "interval")]
        provider = layer.dataProvider()
        new_fields = []
        for key in keys:
            fname = f"{prefix}{key}"[:60]
            if layer.fields().indexOf(fname) < 0:
                sample = values[0].get(key)
                ftype = QMetaType.Type.Double if isinstance(sample, (int, float)) \
                    else QMetaType.Type.QString
                new_fields.append(QgsField(fname, ftype))
        if new_fields and not provider.addAttributes(new_fields):
            raise IOError("Provider refused new fields; export to GPKG first")
        layer.updateFields()
        changes = {}
        for fid, row in zip(ids, values):
            changes[fid] = {layer.fields().indexOf(f"{prefix}{k}"[:60]): row.get(k)
                            for k in keys}
        if not provider.changeAttributeValues(changes):
            raise IOError(f"Provider rejected the update: {provider.lastError()}")
        layer.triggerRepaint()
        return {"layer_id": layer_id, "features_updated": len(changes),
                "features_total": total_features,
                "truncated": total_features > len(changes),
                "fields_added": [f.name() for f in new_fields],
                "fields": [f"{prefix}{k}"[:60] for k in keys],
                "units": rows[0].get("units") or rows[0].get("daily_units"),
                "source": "Open-Meteo (CC BY 4.0)"}

    # ------------------------------------------------------------------
    # basemaps
    # ------------------------------------------------------------------
    def list_basemaps(self):
        return {"basemaps": basemaps.list_basemaps(),
                "note": "GIBS layers take a date (YYYY-MM-DD); default is yesterday."}

    def add_basemap(self, basemap="osm", date=None, name=None, opacity=1.0):
        uri, display, attribution = basemaps.basemap_uri(basemap, date)
        layer = QgsRasterLayer(uri, name or display, "wms")
        if not layer.isValid():
            raise ValueError(f"Could not create basemap layer: {layer.error().summary()}")
        layer.setOpacity(float(opacity))
        source_id = "nasa-gibs" if basemap in basemaps.GIBS_LAYERS else \
            ("esri-imagery" if basemap.startswith("esri") else "osm-tiles")
        layer = self._add_layer(layer, source_id, {"basemap": basemap, "date": date})
        _set_attribution(layer, attribution)
        meta = layer.metadata()
        meta.setRights([basemaps.licence_for(basemap)])
        layer.setMetadata(meta)
        # Basemaps belong underneath everything else.
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer.id())
        if node is not None:
            clone = node.clone()
            root.insertChildNode(-1, clone)
            node.parent().removeChildNode(node)
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        return {"layer_id": layer.id(), "name": layer.name(),
                "basemap": basemap, "date": date, "attribution": attribution}

    # ------------------------------------------------------------------
    # OpenStreetMap features
    # ------------------------------------------------------------------
    def fetch_osm_features(self, aoi, tags, geometry="center", name=None,
                           limit=5000):
        """OSM features by tag inside the AOI, as memory layers."""
        area = self._resolve_aoi(aoi)
        if not isinstance(tags, dict) or not tags:
            raise ValueError("tags must be a mapping, e.g. {'amenity': 'hospital'}")
        if area.area_degrees2() > (1 if geometry == "full" else 4):
            raise ValueError("That area is too large for Overpass; narrow the "
                             "AOI (a few km for geometry='full').")
        limit = max(1, min(int(limit), osm.MAX_ELEMENTS))
        query, features, info = osm.fetch(area.bbox, tags, geometry, limit)
        label = name or "osm_" + "_".join(
            f"{k}_{v}" if isinstance(v, str) else k for k, v in tags.items())
        groups = _split_by_geometry(features)
        layers = []
        for gtype, group in groups.items():
            if not group:
                continue
            layer, count = _memory_layer_from_features(
                f"{_safe_name(label)}_{gtype.lower()}" if geometry == "full"
                else _safe_name(label), group, gtype)
            layer = self._add_layer(layer, "overpass", {"tags": tags, "bbox": area.bbox,
                                                 "query": query})
            layers.append({"layer_id": layer.id(), "name": layer.name(),
                           "geometry": gtype, "features": count})
        if self.iface is not None and layers:
            self.iface.mapCanvas().refresh()
        hint = None
        if not features:
            hint = ("No OSM features with those tags here. Check the tag spelling "
                    "on wiki.openstreetmap.org, widen the area, or try a related "
                    "tag (e.g. healthcare=* for amenity=hospital).")
        return {"tags": tags, "aoi": area.to_dict(), "elements": len(features),
                "hint": hint,
                "truncated": info["truncated"], "limit": limit,
                "endpoint": info["endpoint"],
                "layers": layers, "attribution": "© OpenStreetMap contributors (ODbL)",
                "overpass_query": query}

    # ------------------------------------------------------------------
    # hazards
    # ------------------------------------------------------------------
    def fetch_earthquakes(self, period="day", min_magnitude=None, aoi=None,
                          name=None, add_to_project=True):
        bbox = self._resolve_aoi(aoi).bbox if aoi else None
        result = hazards.earthquakes(period, min_magnitude, bbox)
        features = result["features"]
        response = {"period": period, "min_magnitude": min_magnitude,
                    "count": len(features), "source": result["source"],
                    "strongest": sorted(
                        (f["properties"] for f in features),
                        key=lambda p: p.get("magnitude") or 0, reverse=True)[:5]}
        if add_to_project and features:
            layer, count = _memory_layer_from_features(
                name or f"earthquakes_{period}", features)
            layer = self._add_layer(layer, "usgs-earthquakes", {"period": period,
                                                        "min_magnitude": min_magnitude})
            response["layer_id"] = layer.id()
            if self.iface is not None:
                self.iface.mapCanvas().refresh()
        return response

    def fetch_active_fires(self, aoi, days=1, source="VIIRS_SNPP_NRT", name=None):
        area = self._resolve_aoi(aoi)
        key = self._settings().value("qgis_mcp/keys/firms", "")
        result = hazards.active_fires(key, area.bbox, days, source)
        features = result["features"]
        response = {"aoi": area.to_dict(), "days": result["days"],
                    "source": source, "count": len(features)}
        if features:
            layer, count = _memory_layer_from_features(
                name or f"fires_{source.lower()}_{days}d", features)
            layer = self._add_layer(layer, "nasa-firms", {"bbox": area.bbox, "days": days,
                                                  "source": source})
            response["layer_id"] = layer.id()
            if self.iface is not None:
                self.iface.mapCanvas().refresh()
        return response

    # ------------------------------------------------------------------
    # keys
    # ------------------------------------------------------------------
    def set_data_source_key(self, source_id, key):
        """Store an API key for a source. Keys never appear in responses."""
        entry = registry.REGISTRY.get(source_id)
        if entry is None or not entry.get("key_setting"):
            raise ValueError(f"'{source_id}' does not take a key")
        key = str(key).strip()
        if not re.fullmatch(r"[A-Za-z0-9_\-]{8,128}", key):
            raise ValueError("Key must be 8-128 letters, digits, '-' or '_'")
        self._settings().setValue(entry["key_setting"], key)
        return {"source": source_id, "stored": True}
