# -*- coding: utf-8 -*-
"""Toolbox commands: land cover, soil, water, boundaries, species, sensors,
disaster alerts, and the analysis workflows built on the imagery engine
(change detection, zonal statistics, temporal series, area reports).

Mixed into :class:`QgisMCPServer` beside :class:`LiveDataMixin`, and used
by the Data Explorer dashboard through the same methods.
"""

import datetime as _dt
import hashlib
import html
import json
import os
import time

import numpy as np
from osgeo import gdal
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDateTimeRange,
    QgsLayerTreeGroup, QgsMapLayer, QgsProject, QgsRasterLayer,
)
from qgis.PyQt.QtCore import QDateTime, Qt

from . import aoi as aoi_mod
from .connectors import (boundaries, disasters, indices, landcover, sensors,
                         soil, species, stac, weather)
from .live_commands import (_cap_rows, _memory_layer_from_features, _output_tif,
                            _safe_name, cache_dir)


def _paletted_style(layer, classes, nodata=None):
    from qgis.core import QgsPalettedRasterRenderer
    from qgis.PyQt.QtGui import QColor
    entries = [QgsPalettedRasterRenderer.Class(int(v), QColor(c), label)
               for v, (label, c) in classes.items()]
    renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, entries)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def _diverging_style(layer, lo, hi):
    from qgis.core import (QgsColorRampShader, QgsRasterShader,
                           QgsSingleBandPseudoColorRenderer)
    from qgis.PyQt.QtGui import QColor
    stops = [(lo, "#a50026"), (lo / 2, "#f46d43"), (0.0, "#f7f7f7"),
             (hi / 2, "#74add1"), (hi, "#313695")]
    shader = QgsRasterShader()
    ramp = QgsColorRampShader(lo, hi)
    ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
    ramp.setColorRampItemList([
        QgsColorRampShader.ColorRampItem(v, QColor(c), f"{v:+.2f}") for v, c in stops])
    shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    renderer.setClassificationMin(lo)
    renderer.setClassificationMax(hi)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def _ramp_style(layer, lo, hi, colours, unit=None):
    """Single band pseudocolour between lo and hi over a list of hex colours."""
    from qgis.core import (QgsColorRampShader, QgsRasterShader,
                           QgsSingleBandPseudoColorRenderer)
    from qgis.PyQt.QtGui import QColor
    if hi <= lo:
        hi = lo + 1.0
    n = len(colours)
    items = [QgsColorRampShader.ColorRampItem(lo + (hi - lo) * i / (n - 1), QColor(c),
                                              f"{lo + (hi - lo) * i / (n - 1):.3g}"
                                              + (f" {unit}" if unit and i == n - 1 else ""))
             for i, c in enumerate(colours)]
    shader = QgsRasterShader()
    ramp = QgsColorRampShader(lo, hi)
    ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
    ramp.setColorRampItemList(items)
    shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    renderer.setClassificationMin(lo)
    renderer.setClassificationMax(hi)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def report_to_pdf(html_path, pdf_path):
    """Render a report's HTML to an A4 PDF with Qt (GUI thread only)."""
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QPageSize, QTextDocument
    from qgis.PyQt.QtPrintSupport import QPrinter
    with open(html_path, encoding="utf-8") as handle:
        text = handle.read().replace("width='900'", "width='640'")
    doc = QTextDocument()
    doc.setBaseUrl(QUrl.fromLocalFile(html_path))
    doc.setHtml(text)
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setOutputFileName(pdf_path)
    printing = getattr(doc, "print", None) or getattr(doc, "print_")
    printing(printer)
    if not os.path.exists(pdf_path):
        raise IOError(f"Qt did not write {pdf_path}")
    return pdf_path


class ToolboxMixin:
    """Land, soil, water, boundaries, species, sensors, alerts and analysis."""

    # ------------------------------------------------------------------
    # datasets: land cover, land use, surface water, NASADEM
    # ------------------------------------------------------------------
    def list_datasets(self, category=None):
        rows = []
        for key, spec in landcover.DATASETS.items():
            if category and spec.get("category") != category:
                continue
            row = {k: v for k, v in spec.items() if k not in ("classes", "ramp")}
            row["id"] = key
            row["kind"] = "categorical" if spec.get("classes") else "continuous"
            row["classes"] = [label for label, _ in (spec.get("classes") or {}).values()]
            if row.get("valid_range") is not None:
                row["valid_range"] = list(row["valid_range"])
            rows.append(row)
        return {"count": len(rows), "categories": sorted({r["category"] for r in rows}),
                "datasets": rows}

    def dataset_prepare(self, bbox, dataset="worldcover"):
        """Worker half: resolve tiles, mosaic, convert units and summarise."""
        spec, hrefs = landcover.dataset_hrefs(dataset, bbox)
        # The name follows the source files (without their signatures), so a
        # newer MODIS period gets a new file instead of rewriting one that an
        # open layer may still be reading.
        stable = sorted(h.split("?")[0] for h in hrefs)
        digest = hashlib.sha1(json.dumps([bbox, stable]).encode()).hexdigest()[:8]
        path = os.path.join(cache_dir(), f"{_safe_name(dataset)}_{digest}.vrt")
        # Planetary Computer URLs are signed and expire, so those are copied
        # into a local GeoTIFF; WorldCover on AWS is stable and stays streamed.
        path = landcover.build_vrt(path, hrefs, bbox, spec.get("nodata"),
                                   materialise=spec["catalogue"] is not None, spec=spec)
        summary = landcover.summarise(path, spec)
        return {"dataset": dataset, "spec": spec, "tiles": len(hrefs), "path": path,
                "bbox": list(bbox), "summary": summary}

    def dataset_apply(self, prepared, name=None):
        """GUI half: load the mosaic, style it and record provenance."""
        spec, path, dataset = prepared["spec"], prepared["path"], prepared["dataset"]
        layer = QgsRasterLayer(path, name or spec["name"], "gdal")
        if not layer.isValid():
            raise IOError(f"Could not open the {spec['name']} mosaic")
        summary = prepared.get("summary") or {}
        if spec.get("classes"):
            _paletted_style(layer, spec["classes"])
        elif spec.get("ramp"):
            lo, hi = summary.get("p2"), summary.get("p98")
            if lo is None or hi is None or lo == hi:
                lo, hi = (spec.get("valid_range") or (0, 1))
            _ramp_style(layer, float(lo), float(hi), spec["ramp"], spec.get("unit"))
        source_id = "esa-worldcover" if dataset == "worldcover" else "planetary-computer"
        layer = self._add_layer(layer, source_id, {"dataset": dataset, "bbox": prepared["bbox"],
                                                   "tiles": prepared["tiles"]})
        meta = layer.metadata()
        meta.setRights([spec["licence"]])
        layer.setMetadata(meta)
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        return layer

    def add_dataset(self, aoi, dataset="worldcover", name=None, summarise=True):
        """Add a named global dataset clipped to the area, with a summary."""
        area = self._resolve_aoi(aoi)
        prepared = self.dataset_prepare(area.bbox, dataset)
        layer = self.dataset_apply(prepared, name)
        spec = prepared["spec"]
        response = {"layer_id": layer.id(), "name": layer.name(), "dataset": dataset,
                    "kind": "categorical" if spec.get("classes") else "continuous",
                    "unit": spec.get("unit"), "tiles": prepared["tiles"],
                    "path": prepared["path"], "aoi": area.to_dict(),
                    "licence": spec["licence"], "attribution": spec["attribution"],
                    "description": spec["description"]}
        if summarise:
            response["summary"] = prepared["summary"]
        return response

    def summarise_land_cover(self, aoi, dataset="worldcover"):
        """Statistics or class shares for a dataset over the area, no layer added."""
        area = self._resolve_aoi(aoi)
        prepared = self.dataset_prepare(area.bbox, dataset)
        summary = dict(prepared["summary"])
        spec = prepared["spec"]
        summary.update({"dataset": dataset, "name": spec["name"], "unit": spec.get("unit"),
                        "aoi": area.to_dict(), "attribution": spec["attribution"],
                        "description": spec["description"]})
        return summary

    # ------------------------------------------------------------------
    # soil
    # ------------------------------------------------------------------
    def get_soil_properties(self, aoi=None, lat=None, lon=None, properties=None,
                            depths=None):
        (lon, lat), label = self._point_or_aoi(aoi, lat, lon)
        result = soil.query(lon, lat, properties, depths)
        result["place"] = label
        return result

    def attach_soil_to_layer(self, layer_id, properties=None, depth="0-5cm",
                             prefix="soil_", max_features=25):
        """Add topsoil properties to features, sampled at centroids (slow API)."""
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QMetaType
        max_features = max(1, min(int(max_features), 25))
        layer, points, ids = self._layer_points(layer_id, limit=max_features)
        props = properties or ["clay", "sand", "silt", "phh2o", "soc"]
        values, skipped = soil.query_many(points, props, depth)
        ids = ids[:len(values)]
        provider = layer.dataProvider()
        new_fields = []
        for key in props + ["texture"]:
            fname = f"{prefix}{key}"[:60]
            if layer.fields().indexOf(fname) < 0:
                ftype = QMetaType.Type.QString if key == "texture" else QMetaType.Type.Double
                new_fields.append(QgsField(fname, ftype))
        if new_fields and not provider.addAttributes(new_fields):
            raise IOError("Provider refused new fields; export to GeoPackage first")
        layer.updateFields()
        changes = {fid: {layer.fields().indexOf(f"{prefix}{k}"[:60]): row.get(k)
                         for k in props + ["texture"]} for fid, row in zip(ids, values)}
        if not provider.changeAttributeValues(changes):
            raise IOError(f"Provider rejected the update: {provider.lastError()}")
        layer.triggerRepaint()
        return {"layer_id": layer_id, "features_updated": len(changes),
                "features_total": layer.featureCount(), "skipped": skipped,
                "truncated": layer.featureCount() > len(changes),
                "fields": [f"{prefix}{k}"[:60] for k in props + ["texture"]],
                "source": "SoilGrids 2.0, ISRIC (CC BY 4.0)"}

    # ------------------------------------------------------------------
    # water, marine, elevation points
    # ------------------------------------------------------------------
    def get_river_discharge(self, aoi=None, lat=None, lon=None, days=7, past_days=30):
        point, label = self._point_or_aoi(aoi, lat, lon)
        row = weather.river_discharge([point], days, past_days)[0]
        row["place"] = label
        row["source"] = "Open-Meteo flood API (GloFAS, CC BY 4.0)"
        return row

    def get_marine_conditions(self, aoi=None, lat=None, lon=None, days=3):
        point, label = self._point_or_aoi(aoi, lat, lon)
        row = weather.marine([point], days)[0]
        row["place"] = label
        row["source"] = "Open-Meteo marine API (CC BY 4.0)"
        return row

    def get_elevation(self, points=None, aoi=None, lat=None, lon=None):
        """Elevation at points, or at the AOI centre / lat-lon."""
        if points:
            pts = [(float(p[0]), float(p[1])) if isinstance(p, (list, tuple))
                   else (float(p["lon"]), float(p["lat"])) for p in points]
        else:
            point, _ = self._point_or_aoi(aoi, lat, lon)
            pts = [point]
        return {"count": len(pts), "samples": weather.elevation(pts),
                "source": "Open-Meteo elevation API (Copernicus DEM 90 m)"}

    # ------------------------------------------------------------------
    # boundaries, species, sensors, alerts
    # ------------------------------------------------------------------
    def add_admin_boundaries(self, iso3, level="ADM1", aoi=None, name=None,
                             simplified=True):
        """Administrative units of a country, optionally only those touching the AOI."""
        meta = boundaries.metadata(iso3, level)
        url = (meta["simplified_url"] if simplified else None) or meta["geojson_url"]
        features = boundaries.fetch_geojson(url)
        area = self._resolve_aoi(aoi) if aoi else None
        if area is not None:
            features = boundaries.within(features, area.bbox)
        if not features:
            raise LookupError("No boundary units intersect the area")
        layer, count = _memory_layer_from_features(
            name or f"{meta['iso3']}_{meta['level']}_{_safe_name(meta['canonical'] or '')}",
            features, "Polygon")
        layer = self._add_layer(layer, "geoboundaries",
                                {"iso3": meta["iso3"], "level": meta["level"],
                                 "bbox": area.bbox if area else None})
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        return {"layer_id": layer.id(), "name": layer.name(), "features": count,
                "country": meta["name"], "level": meta["level"],
                "units": meta["canonical"], "year": meta["year"],
                "licence": meta["licence"], "names": sorted(
                    f["properties"]["name"] for f in features if f["properties"].get("name"))[:60]}

    def fetch_species_occurrences(self, aoi, taxon=None, limit=1000, year_from=None,
                                  year_to=None, name=None):
        area = self._resolve_aoi(aoi)
        result = species.occurrences(area.bbox, taxon, limit, year_from, year_to)
        features = result["features"]
        response = {"aoi": area.to_dict(), "taxon": result["taxon"],
                    "matched": result["matched"], "returned": len(features),
                    "top_species": species.species_counts(features),
                    "source": "GBIF occurrence API (CC0 / CC BY per dataset)"}
        if features:
            label = name or ("gbif_" + _safe_name(taxon) if taxon else "gbif_occurrences")
            layer, count = _memory_layer_from_features(label, features)
            layer = self._add_layer(layer, "gbif", {"bbox": area.bbox, "taxon": taxon})
            response["layer_id"] = layer.id()
            if self.iface is not None:
                self.iface.mapCanvas().refresh()
        else:
            response["hint"] = "No records here; try a broader taxon, a larger area, or no taxon."
        return response

    def fetch_air_sensors(self, aoi=None, lat=None, lon=None, radius_km=25, name=None):
        (lon, lat), label = self._point_or_aoi(aoi, lat, lon)
        result = sensors.air_sensors(lon, lat, radius_km)
        features = result["features"]
        response = {"centre": [lon, lat], "place": label, "radius_km": radius_km,
                    "summary": result["summary"], "source": result["source"]}
        if features:
            layer, count = _memory_layer_from_features(name or "air_quality_sensors", features)
            layer = self._add_layer(layer, "sensor-community",
                                    {"lon": lon, "lat": lat, "radius_km": radius_km})
            response["layer_id"] = layer.id()
            if self.iface is not None:
                self.iface.mapCanvas().refresh()
        else:
            response["hint"] = "No community sensors within that radius; widen it."
        return response

    def fetch_disaster_alerts(self, aoi=None, event_types=None, alert_levels=None,
                              add_to_project=True, name=None):
        bbox = self._resolve_aoi(aoi).bbox if aoi else None
        result = disasters.alerts(bbox, event_types, alert_levels)
        features = result["features"]
        response = {"count": len(features), "by_level": result["by_level"],
                    "source": result["source"],
                    "alerts": [f["properties"] for f in features][:30]}
        if add_to_project and features:
            layer, count = _memory_layer_from_features(name or "gdacs_alerts", features)
            layer = self._add_layer(layer, "gdacs", {"bbox": bbox, "types": event_types,
                                                     "levels": alert_levels})
            response["layer_id"] = layer.id()
            if self.iface is not None:
                self.iface.mapCanvas().refresh()
        return response

    # ------------------------------------------------------------------
    # analysis workflows
    # ------------------------------------------------------------------
    def _best_scene(self, area, collection, start, end, max_cloud, catalogue):
        found = stac.search(area.bbox, collection, start, end, max_cloud, 1, catalogue)
        if not found["items"]:
            raise LookupError(f"No {collection} scene between {start} and {end} "
                              f"under {max_cloud}% cloud; widen the window")
        return found["items"][0]

    def compute_index_change(self, aoi, index="NDVI", before_start=None,
                             before_end=None, after_start=None, after_end=None,
                             collection="sentinel-2-l2a", max_cloud=20,
                             mask_clouds=True, catalogue="earth-search",
                             breaks=None, add_to_project=True, expression=None):
        """Difference of an index between two periods, with loss and gain shares."""
        area = self._resolve_aoi(aoi)
        today = _dt.date.today()
        after_end = after_end or today.isoformat()
        after_start = after_start or (_dt.date.fromisoformat(after_end)
                                      - _dt.timedelta(days=45)).isoformat()
        before_end = before_end or (_dt.date.fromisoformat(after_start)
                                    - _dt.timedelta(days=1)).isoformat()
        before_start = before_start or (_dt.date.fromisoformat(before_end)
                                        - _dt.timedelta(days=45)).isoformat()
        scene_a = self._best_scene(area, collection, before_start, before_end,
                                   max_cloud, catalogue)
        scene_b = self._best_scene(area, collection, after_start, after_end,
                                   max_cloud, catalogue)
        item_a = stac.get_item(scene_a["id"], collection, catalogue)
        item_b = stac.get_item(scene_b["id"], collection, catalogue)
        spec = stac.COLLECTIONS.get(collection, {})
        res = indices._target_resolution(item_a, [spec.get("bands", {}).get(b, b)
                                                  for b in indices.INDICES.get(str(index).upper(), ("", [], (), ""))[1]])
        a = indices.compute(item_a, area.bbox, index, expression, mask_clouds, catalogue, res)
        b = indices.compute(item_b, area.bbox, index, expression, mask_clouds, catalogue,
                            a["resolution"])
        if (a["array"].shape != b["array"].shape or a["projection"] != b["projection"]
                or any(abs(x - y) > 1e-6 for x, y in zip(a["transform"], b["transform"]))):
            b["array"] = indices.align_to(b["array"], b["transform"], b["projection"],
                                          a["transform"], a["projection"], a["array"].shape)
        diff = b["array"] - a["array"]
        valid = diff[np.isfinite(diff)]
        breaks = breaks or [-0.2, -0.1, 0.1, 0.2]
        if len(breaks) != 4 or list(breaks) != sorted(breaks):
            raise ValueError("breaks must be four ascending values, e.g. [-0.2, -0.1, 0.1, 0.2]")
        classes = indices.classify(diff, breaks)
        labels = ["strong loss", "loss", "stable", "gain", "strong gain"]
        for row, label in zip(classes, labels):
            row["label"] = label
        stats = {
            "valid_pixels": int(valid.size),
            "mean_change": float(valid.mean()) if valid.size else None,
            "loss_fraction": round(float((valid < breaks[1]).sum()) / valid.size, 4) if valid.size else None,
            "gain_fraction": round(float((valid > breaks[2]).sum()) / valid.size, 4) if valid.size else None,
        }
        label = a["index"]
        response = {
            "index": label, "aoi": area.to_dict(),
            "before": {"item": scene_a["id"], "datetime": scene_a["datetime"],
                       "cloud_cover": scene_a["cloud_cover"], "mean": a["statistics"]["mean"]},
            "after": {"item": scene_b["id"], "datetime": scene_b["datetime"],
                      "cloud_cover": scene_b["cloud_cover"], "mean": b["statistics"]["mean"]},
            "change": stats, "classes": classes, "resolution_m": a["resolution"],
            "interpretation": (f"Negative values mean {label} fell between the two dates "
                               "(vegetation loss, drying, harvest, clearing); positive "
                               "means it rose (greening, flooding for water indices)."),
        }
        digest = hashlib.sha1(json.dumps([area.bbox, scene_a["id"], scene_b["id"]]).encode()).hexdigest()[:8]
        path = _output_tif(None, f"{_safe_name(label)}_change_{digest}")
        indices.write_geotiff(path, diff, a["transform"], a["projection"])
        response["path"] = path
        response["layer_name"] = (f"{label} change {scene_a['datetime'][:10]} to "
                                  f"{scene_b['datetime'][:10]}")
        if add_to_project:
            response["layer_id"] = self.add_change_layer(response, catalogue).id()
        return response

    def add_change_layer(self, result, catalogue="earth-search"):
        """GUI thread half of change detection: load and style the raster."""
        layer = QgsRasterLayer(result["path"], result["layer_name"], "gdal")
        if not layer.isValid():
            raise IOError(f"Could not open {result['path']}")
        layer = self._add_layer(layer, catalogue, {"index": result["index"],
                                                   "before": result["before"]["item"],
                                                   "after": result["after"]["item"]})
        _diverging_style(layer, -0.4, 0.4)
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        return layer

    def zonal_index_statistics(self, layer_id, index="NDVI", item_id=None,
                               collection="sentinel-2-l2a", max_cloud=20,
                               start_date=None, end_date=None, mask_clouds=True,
                               catalogue="earth-search", expression=None,
                               prefix=None, limit=200):
        """Mean, min, max of an index per polygon of a layer (fields are added)."""
        prepared = self.zonal_prepare(layer_id, index, item_id, collection, max_cloud,
                                      start_date, end_date, mask_clouds, catalogue,
                                      expression)
        return self.zonal_apply(prepared, prefix, limit)

    def zonal_area(self, layer_id):
        """GUI thread helper: check the layer and return its WGS84 extent."""
        layer = self._vector_or_raise(layer_id)
        if layer.geometryType() != Qgis.GeometryType.Polygon:
            raise ValueError("zonal statistics need a polygon layer")
        return aoi_mod.from_layer(layer_id)

    def zonal_prepare(self, layer_id, index="NDVI", item_id=None,
                      collection="sentinel-2-l2a", max_cloud=20, start_date=None,
                      end_date=None, mask_clouds=True, catalogue="earth-search",
                      expression=None, area=None):
        """Network and GDAL half: pick a scene and write the index raster.

        ``area`` comes from :meth:`zonal_area`, so a worker thread never
        touches the layer itself.
        """
        area = area or self.zonal_area(layer_id)
        if item_id is None:
            if start_date is None and end_date is None:
                end_date = _dt.date.today().isoformat()
                start_date = (_dt.date.today() - _dt.timedelta(days=90)).isoformat()
            item_id = self._best_scene(area, collection, start_date, end_date,
                                       max_cloud, catalogue)["id"]
        item = stac.get_item(item_id, collection, catalogue)
        result = indices.compute(item, area.bbox, index, expression, mask_clouds, catalogue)
        label = result["index"]
        digest = hashlib.sha1(json.dumps([area.bbox, item_id, result["expression"]]).encode()).hexdigest()[:8]
        path = _output_tif(None, f"{_safe_name(label)}_zonal_{digest}")
        indices.write_geotiff(path, result["array"], result["transform"], result["projection"])
        coverage = None
        scene_bbox = item.get("bbox")
        if scene_bbox:
            w, s, e, n = area.bbox
            iw = max(0.0, min(e, scene_bbox[2]) - max(w, scene_bbox[0]))
            ih = max(0.0, min(n, scene_bbox[3]) - max(s, scene_bbox[1]))
            own = (e - w) * (n - s)
            coverage = round(iw * ih / own, 3) if own > 0 else None
        return {"layer_id": layer_id, "index": label, "item": item_id,
                "datetime": item.get("properties", {}).get("datetime"), "path": path,
                "scene_covers_layer_fraction": coverage,
                "coverage_note": (None if coverage is None or coverage >= 0.95 else
                                  f"The scene covers about {coverage * 100:.0f}% of the layer's "
                                  "extent; polygons outside it get no value and those on the "
                                  "edge are partial. Use a smaller layer or several runs."),
                "interpretation": indices.INDICES.get(label, ("", [], (), ""))[3]}

    def zonal_apply(self, prepared, prefix=None, limit=200):
        """GUI thread half: run QgsZonalStatistics and summarise the fields."""
        from qgis.analysis import QgsZonalStatistics
        layer = self._vector_or_raise(prepared["layer_id"])
        label, item_id, path = prepared["index"], prepared["item"], prepared["path"]
        raster = QgsRasterLayer(path, f"{label} {item_id}", "gdal")
        if not raster.isValid():
            raise IOError(f"Could not open {path}")
        when = (prepared.get("datetime") or "")[:10].replace("-", "")
        prefix = prefix or f"{label.lower()}_{when}_"
        for existing in [f.name() for f in layer.fields() if f.name().startswith(prefix)]:
            layer.dataProvider().deleteAttributes([layer.fields().indexOf(existing)])
        layer.updateFields()
        zonal = QgsZonalStatistics(
            layer, raster, prefix, 1,
            QgsZonalStatistics.Statistic.Mean | QgsZonalStatistics.Statistic.Min
            | QgsZonalStatistics.Statistic.Max | QgsZonalStatistics.Statistic.Count)
        outcome = zonal.calculateStatistics(None)
        if outcome != QgsZonalStatistics.Result.Success:
            raise RuntimeError(f"Zonal statistics failed ({outcome}); is the layer editable "
                               "and does it support new fields?")
        layer.triggerRepaint()
        mean_field = f"{prefix}mean"
        rows = []
        for feature in layer.getFeatures():
            attrs = feature.attributes()
            idx = layer.fields().indexOf(mean_field)
            value = attrs[idx] if idx >= 0 else None
            rows.append({"id": feature.id(), "mean": value,
                         "name": next((feature[n] for n in ("name", "Name", "NAME")
                                       if layer.fields().indexOf(n) >= 0), None)})
        valid = [r for r in rows if isinstance(r["mean"], (int, float))]
        valid.sort(key=lambda r: r["mean"])
        return {"layer_id": prepared["layer_id"], "index": label, "item": item_id,
                "datetime": prepared["datetime"],
                "scene_covers_layer_fraction": prepared.get("scene_covers_layer_fraction"),
                "coverage_note": prepared.get("coverage_note"),
                "fields_added": [f"{prefix}{s}" for s in ("count", "mean", "min", "max")],
                "features": len(rows), "lowest": valid[:10], "highest": valid[-10:][::-1],
                "rows": rows[:limit], "rows_truncated": len(rows) > limit,
                "interpretation": prepared["interpretation"]}

    def add_imagery_series(self, aoi, collection="sentinel-2-l2a", start_date=None,
                           end_date=None, max_cloud=20, max_scenes=6, asset=None,
                           catalogue="earth-search", group_name=None):
        """Add several scenes with time ranges so the temporal controller animates them."""
        prepared = self.series_prepare(aoi, collection, start_date, end_date, max_cloud,
                                       max_scenes, asset, catalogue)
        return self.series_apply(prepared, group_name)

    def series_prepare(self, aoi, collection="sentinel-2-l2a", start_date=None,
                       end_date=None, max_cloud=20, max_scenes=6, asset=None,
                       catalogue="earth-search"):
        area = self._resolve_aoi(aoi)
        if end_date is None:
            end_date = _dt.date.today().isoformat()
        if start_date is None:
            start_date = (_dt.date.fromisoformat(end_date) - _dt.timedelta(days=180)).isoformat()
        n = max(1, min(int(max_scenes), 24))
        found = stac.search(area.bbox, collection, start_date, end_date, max_cloud,
                            n * 3, catalogue)
        by_time = {}
        for s in found["items"]:
            if not s.get("datetime"):
                continue
            key = s["datetime"][:10]
            if key not in by_time or s["id"] > by_time[key]["id"]:
                by_time[key] = s
        # The search is least cloudy first: keep the n clearest, then order by date.
        clearest = list(by_time.values())[:n]
        scenes = sorted(clearest, key=lambda s: s["datetime"])
        if not scenes:
            raise LookupError("No scenes in that window; widen it or raise max_cloud")
        spec = stac.COLLECTIONS.get(collection, {})
        asset = asset or spec.get("display", "visual")
        resolved = []
        for scene in scenes:
            item = stac.get_item(scene["id"], collection, catalogue)
            href, _ = stac.asset_href(item, asset, catalogue)
            path = stac.gdal_path(href)
            ds = gdal.Open(path)  # warms the remote cache off the GUI thread
            if ds is None:
                continue
            ds = None
            resolved.append(dict(scene, path=path, asset=asset))
        return {"aoi": area.to_dict(), "collection": collection, "asset": asset,
                "catalogue": catalogue,
                "start_date": start_date, "end_date": end_date, "scenes": resolved}

    def series_apply(self, prepared, group_name=None):
        scenes = prepared["scenes"]
        asset = prepared["asset"]
        root = QgsProject.instance().layerTreeRoot()
        group = root.insertGroup(0, group_name or
                                 f"{prepared['collection']} {prepared['start_date']} to "
                                 f"{prepared['end_date']}")
        added = []
        for i, scene in enumerate(scenes):
            layer = QgsRasterLayer(scene["path"], f"{scene['datetime'][:10]} {asset}", "gdal")
            if not layer.isValid():
                continue
            start = QDateTime.fromString(scene["datetime"][:19], Qt.DateFormat.ISODate)
            start.setTimeSpec(Qt.TimeSpec.UTC)
            next_dt = (QDateTime.fromString(scenes[i + 1]["datetime"][:19], Qt.DateFormat.ISODate)
                       if i + 1 < len(scenes) else start.addDays(10))
            next_dt.setTimeSpec(Qt.TimeSpec.UTC)
            props = layer.temporalProperties()
            props.setIsActive(True)
            props.setMode(Qgis.RasterTemporalMode.FixedTemporalRange)
            props.setFixedTemporalRange(QgsDateTimeRange(start, next_dt))
            from .live_commands import _stamp_provenance
            _stamp_provenance(layer, prepared.get("catalogue", "earth-search"),
                              {"item": scene["id"], "asset": asset})
            QgsProject.instance().addMapLayer(layer, False)
            group.addLayer(layer)
            added.append({"layer_id": layer.id(), "datetime": scene["datetime"],
                          "cloud_cover": scene["cloud_cover"]})
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        return {"group": group.name(), "scenes": len(added), "layers": added,
                "aoi": prepared["aoi"],
                "note": "Open View > Panels > Temporal Controller and press play to animate."}

    def export_area_report(self, aoi, path=None, title=None, include_index=True,
                           include_land_cover=True, include_soil=True,
                           include_weather=True, render_map=True):
        """An HTML briefing for the area: map, weather, elevation, land cover,
        soil, and vegetation index statistics."""
        area = self._resolve_aoi(aoi)
        started = time.monotonic()
        sections = []
        errors = {}

        def section(name, fn):
            try:
                sections.append((name, fn()))
            except Exception as exc:
                errors[name] = str(exc)[:200]

        lon, lat = area.center
        if include_weather:
            section("Weather now", lambda: weather.current([(lon, lat)])[0])
            section("Elevation", lambda: weather.elevation([(lon, lat)])[0])
        if include_land_cover:
            section("Land cover", lambda: self.summarise_land_cover({"bbox": area.bbox}))
        if include_soil:
            section("Soil (topsoil)", lambda: soil.query(lon, lat))
        if include_index:
            section("Vegetation (NDVI)", lambda: self.compute_spectral_index(
                {"bbox": area.bbox}, breaks=[0.2, 0.4, 0.6], add_to_project=False))
        image_path = None
        if render_map:
            try:
                digest = hashlib.sha1(json.dumps(area.bbox).encode()).hexdigest()[:8]
                image_path = os.path.join(cache_dir(), f"report_map_{digest}.png")
                self.render_map(image_path, 900, 600, None,
                                {"xmin": area.bbox[0], "ymin": area.bbox[1],
                                 "xmax": area.bbox[2], "ymax": area.bbox[3]})
            except Exception as exc:
                errors["map"] = str(exc)[:200]
                image_path = None
        ext = os.path.splitext(str(path))[1].lower() if path else ".html"
        if ext not in (".html", ".htm", ".pdf"):
            raise ValueError("path must end in .html or .pdf")
        path = path or os.path.join(cache_dir(), f"area_report_{_safe_name(area.label or 'aoi')[:30]}.html")
        html_path = path[:-4] + ".html" if ext == ".pdf" else path
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(_report_html(title or (area.label or "Area report"), area,
                                      sections, errors, image_path))
        response = {"path": path, "html_path": html_path, "sections": [s for s, _ in sections],
                    "errors": errors, "elapsed_s": round(time.monotonic() - started, 1),
                    "aoi": area.to_dict()}
        if ext == ".pdf":
            report_to_pdf(html_path, path)
        return response


def _fmt(value, digits=3):
    return "n/a" if value is None else f"{value:.{digits}f}"


def _report_html(title, area, sections, errors, image_path):
    esc = lambda v: html.escape(str(v))  # noqa: E731
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)}</title>",
             "<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:900px;margin:2em auto;"
             "color:#222}table{border-collapse:collapse;margin:.5em 0}td,th{border:1px solid #ccc;"
             "padding:4px 8px;text-align:left}h2{border-bottom:1px solid #ddd;margin-top:1.5em}"
             ".small{color:#666;font-size:.9em}</style></head><body>",
             f"<h1>{esc(title)}</h1>",
             f"<p class='small'>Generated {_dt.datetime.now():%Y-%m-%d %H:%M} by QGIS MCP toolbox. "
             f"Centre {area.center[1]:.4f}, {area.center[0]:.4f}; bbox "
             f"{', '.join(f'{v:.4f}' for v in area.bbox)}.</p>"]
    if image_path and os.path.exists(image_path):
        parts.append(f"<img src='file:///{esc(image_path).replace(os.sep, '/')}' width='900' "
                     "alt='map of the area'>")

    def table(rows, columns):
        out = "<table><tr>" + "".join(f"<th>{esc(c)}</th>" for c in columns) + "</tr>"
        for r in rows:
            out += "<tr>" + "".join(f"<td>{'' if r.get(c) is None else esc(r.get(c))}</td>"
                                    for c in columns) + "</tr>"
        return out + "</table>"

    for name, data in sections:
        parts.append(f"<h2>{esc(name)}</h2>")
        if name == "Weather now":
            cur = data.get("current") or {}
            units = data.get("units") or {}
            parts.append(table([{"variable": k, "value": v, "unit": units.get(k, "")}
                                for k, v in cur.items() if k not in ("time", "interval")],
                               ["variable", "value", "unit"]))
        elif name == "Elevation":
            parts.append(f"<p>{esc(data.get('elevation_m'))} m above sea level at the centre.</p>")
        elif name == "Land cover":
            parts.append(table(data.get("classes", []), ["label", "fraction", "area_km2"]))
            parts.append(f"<p class='small'>{esc(data.get('attribution'))}</p>")
        elif name == "Soil (topsoil)":
            rows = [r for r in data.get("rows", []) if r.get("value") is not None]
            if not rows:
                parts.append("<p>SoilGrids has no values at this point (built up land, "
                             "water or outside coverage).</p>")
            else:
                parts.append(f"<p>Texture class: <b>{esc(data.get('texture_class') or 'not derived')}</b></p>")
                parts.append(table(rows, ["label", "depth", "value", "unit"]))
        elif name == "Vegetation (NDVI)":
            stats = data.get("statistics", {})
            parts.append(f"<p>Scene {esc(data.get('item'))} of {esc((data.get('datetime') or '')[:10])}, "
                         f"cloud {esc(data.get('cloud_cover'))}%. Mean NDVI "
                         f"<b>{_fmt(stats.get('mean'))}</b>, median {_fmt(stats.get('median'))}.</p>")
            if data.get("classes"):
                rows = []
                for c in data["classes"]:
                    lo, hi = c.get("from"), c.get("to")
                    label = (f"below {hi}" if lo is None else f"{lo} and above" if hi is None
                             else f"{lo} to {hi}")
                    rows.append({"range": label, "share": f"{c['fraction'] * 100:.1f} %"})
                parts.append(table(rows, ["range", "share"]))
            parts.append(f"<p class='small'>{esc(data.get('interpretation'))}</p>")
    if errors:
        parts.append("<h2>Not available</h2><ul>" + "".join(
            f"<li><b>{esc(k)}</b>: {esc(v)}</li>" for k, v in errors.items()) + "</ul>")
    parts.append("<p class='small'>Sources: Open-Meteo, ESA WorldCover, SoilGrids (ISRIC), "
                 "Copernicus Sentinel-2 via Earth Search, OpenStreetMap contributors.</p></body></html>")
    return "".join(parts)
