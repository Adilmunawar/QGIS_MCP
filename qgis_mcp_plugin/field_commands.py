# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""Field analytics: per field vegetation vigour, change and weather for a
polygon layer of fields, parcels or harvest blocks.

Works on a subset of a layer (the selection, the features inside the area of
interest, or the first ``limit`` features) so a layer of a million parcels
stays usable. Results go to a new layer with the metrics as fields and a
graduated style; the source layer is never modified unless asked.

Split for the dashboard: :meth:`fields_subset` (GUI thread, reads the
layer), :meth:`fields_fetch` (worker: scenes, index rasters, weather) and
:meth:`fields_apply` (GUI thread: zonal statistics, layer, style, ranking).
"""

import datetime as _dt
import hashlib
import json
import os

from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDistanceArea,
    QgsFeature, QgsFeatureRequest, QgsField, QgsGeometry, QgsProject, QgsRasterLayer,
    QgsRectangle, QgsVectorLayer,
)
from qgis.PyQt.QtCore import QMetaType

from . import aoi as aoi_mod
from .connectors import indices, stac, weather
from .live_commands import _output_tif, _safe_name, cache_dir, persist_layer

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
MAX_FIELDS = 2000
NAME_CANDIDATES = ("name", "Name", "NAME", "field", "field_name", "block", "mouza_name",
                   "parcel_no", "parcel_id", "parcel_Id", "id", "fid")
SQ_M_PER_ACRE = 4046.8564224


def _name_fields(layer):
    present = {f.name() for f in layer.fields()}
    return [c for c in NAME_CANDIDATES if c in present][:2]


def _label(attrs, name_fields):
    bits = [str(attrs.get(n)) for n in name_fields if attrs.get(n) not in (None, "")]
    return " / ".join(bits) if bits else None


class FieldsMixin:
    """Per field analytics for polygon layers."""

    # ------------------------------------------------------------------
    # subset (GUI thread)
    # ------------------------------------------------------------------
    def fields_subset(self, layer_id, selected=None, aoi=None, limit=300):
        """Pick the fields to analyse and copy their geometry as WGS84 WKT.

        ``selected`` None means: use the selection when there is one, else
        the features inside ``aoi`` when given, else the first ``limit``
        features. Returns a plain dict the worker can use without the layer.
        """
        layer = self._vector_or_raise(layer_id)
        if layer.geometryType() != Qgis.GeometryType.Polygon:
            raise ValueError("Field analytics needs a polygon layer (fields, parcels, blocks)")
        limit = max(1, min(int(limit), MAX_FIELDS))
        use_selection = layer.selectedFeatureCount() > 0 if selected is None else bool(selected)
        if use_selection and layer.selectedFeatureCount() == 0:
            raise ValueError("No features are selected on that layer; select some, or pass selected=false")
        transform = (QgsCoordinateTransform(layer.crs(), WGS84, QgsProject.instance())
                     if layer.crs() != WGS84 else None)
        measure = QgsDistanceArea()
        measure.setSourceCrs(WGS84, QgsProject.instance().transformContext())
        measure.setEllipsoid("WGS84")
        name_fields = _name_fields(layer)
        request = QgsFeatureRequest()
        area = None
        if use_selection:
            request.setFilterFids(layer.selectedFeatureIds())
            how = "selection"
        elif aoi:
            area = self._resolve_aoi(aoi)
            rect = QgsRectangle(*area.bbox)
            if transform is not None:
                rect = QgsCoordinateTransform(WGS84, layer.crs(), QgsProject.instance()).transformBoundingBox(rect)
            request.setFilterRect(rect)
            how = "area"
        else:
            how = "first"
        rows = []
        total_seen = 0
        seen_geometry = set()
        duplicates = 0
        for feature in layer.getFeatures(request):
            if not feature.hasGeometry():
                continue
            total_seen += 1
            if len(rows) >= limit:
                continue
            geom = QgsGeometry(feature.geometry())
            if transform is not None:
                geom.transform(transform)
            if area is not None and not geom.intersects(QgsGeometry.fromRect(QgsRectangle(*area.bbox))):
                continue
            wkt = geom.asWkt(7)
            if wkt in seen_geometry:  # the same polygon twice would double count its acres
                duplicates += 1
                continue
            seen_geometry.add(wkt)
            attrs = dict(zip([f.name() for f in layer.fields()], feature.attributes()))
            rows.append({"fid": feature.id(), "wkt": wkt,
                         "label": _label(attrs, name_fields),
                         "acres": round(measure.measureArea(geom) / SQ_M_PER_ACRE, 3),
                         "attrs": {k: v for k, v in attrs.items()
                                   if isinstance(v, (str, int, float, bool)) or v is None}})
        if not rows:
            raise LookupError("No polygons to analyse" + (" inside the area" if area else ""))
        xmin = min(QgsGeometry.fromWkt(r["wkt"]).boundingBox().xMinimum() for r in rows)
        xmax = max(QgsGeometry.fromWkt(r["wkt"]).boundingBox().xMaximum() for r in rows)
        ymin = min(QgsGeometry.fromWkt(r["wkt"]).boundingBox().yMinimum() for r in rows)
        ymax = max(QgsGeometry.fromWkt(r["wkt"]).boundingBox().yMaximum() for r in rows)
        bbox = aoi_mod.from_bbox([xmin, ymin, xmax, ymax], source="fields", label=layer.name()).bbox
        return {"layer_id": layer_id, "layer_name": layer.name(), "how": how, "limit": limit,
                "truncated": total_seen > len(rows) + duplicates, "candidates": total_seen,
                "duplicates_skipped": duplicates, "fields": rows, "bbox": bbox, "name_fields": name_fields,
                "total_acres": round(sum(r["acres"] for r in rows), 2)}

    # ------------------------------------------------------------------
    # fetch (worker thread)
    # ------------------------------------------------------------------
    def fields_fetch(self, subset, index="NDVI", days=45, compare_days=30, collection="sentinel-2-l2a",
                     max_cloud=25, catalogue="earth-search", mask_clouds=True, weather_days=30):
        """Newest clear scene and one about ``compare_days`` earlier, as index rasters, plus weather."""
        bbox = subset["bbox"]
        area = aoi_mod.from_bbox(bbox, source="fields")
        today = _dt.date.today()
        now_start = (today - _dt.timedelta(days=int(days))).isoformat()
        scene_now = self._best_scene(area, collection, now_start, today.isoformat(), max_cloud, catalogue)
        now_date = _dt.date.fromisoformat(scene_now["datetime"][:10])
        prev_end = (now_date - _dt.timedelta(days=max(7, int(compare_days)) - 10)).isoformat()
        prev_start = (now_date - _dt.timedelta(days=int(compare_days) + 25)).isoformat()
        try:
            scene_prev = self._best_scene(area, collection, prev_start, prev_end, max_cloud, catalogue)
        except LookupError:
            scene_prev = None
        out = {"subset": subset, "index": str(index).upper(), "now": None, "prev": None, "weather": {},
               "errors": {}}
        item = stac.get_item(scene_now["id"], collection, catalogue)
        result = indices.compute(item, bbox, index, None, mask_clouds, catalogue)
        digest = hashlib.sha1(json.dumps([bbox, scene_now["id"]]).encode()).hexdigest()[:8]
        path = _output_tif(None, f"fields_{_safe_name(out['index'])}_{digest}")
        indices.write_geotiff(path, result["array"], result["transform"], result["projection"])
        out["now"] = {"item": scene_now["id"], "datetime": scene_now["datetime"], "cloud_cover": scene_now["cloud_cover"],
                      "path": path, "mean": result["statistics"]["mean"], "resolution_m": result["resolution"]}
        if scene_prev is not None:
            try:
                item_p = stac.get_item(scene_prev["id"], collection, catalogue)
                result_p = indices.compute(item_p, bbox, index, None, mask_clouds, catalogue, result["resolution"])
                digest_p = hashlib.sha1(json.dumps([bbox, scene_prev["id"]]).encode()).hexdigest()[:8]
                path_p = _output_tif(None, f"fields_{_safe_name(out['index'])}_{digest_p}")
                indices.write_geotiff(path_p, result_p["array"], result_p["transform"], result_p["projection"])
                out["prev"] = {"item": scene_prev["id"], "datetime": scene_prev["datetime"],
                               "cloud_cover": scene_prev["cloud_cover"], "path": path_p,
                               "mean": result_p["statistics"]["mean"]}
            except Exception as exc:
                out["errors"]["previous_scene"] = str(exc)[:200]
        else:
            out["errors"]["previous_scene"] = "no clear scene in the comparison window"
        lon, lat = area.center
        try:
            end = today - _dt.timedelta(days=1)
            start = end - _dt.timedelta(days=int(weather_days))
            hist = weather.history([(lon, lat)], start.isoformat(), end.isoformat())[0]
            daily = hist.get("daily") or []
            out["weather"]["history"] = {
                "days": len(daily),
                "rain_mm": round(sum(r.get("precipitation_sum") or 0 for r in daily), 1),
                "et0_mm": round(sum(r.get("et0_fao_evapotranspiration") or 0 for r in daily), 1),
                "tmax_mean": round(sum(r.get("temperature_2m_max") or 0 for r in daily) / len(daily), 1) if daily else None,
                "rows": daily}
        except Exception as exc:
            out["errors"]["weather_history"] = str(exc)[:200]
        try:
            fc = weather.forecast([(lon, lat)], days=7)[0]
            daily = fc.get("daily") or []
            out["weather"]["forecast"] = {
                "rain_mm": round(sum(r.get("precipitation_sum") or 0 for r in daily), 1),
                "tmax_max": max((r.get("temperature_2m_max") or 0) for r in daily) if daily else None,
                "rows": daily}
        except Exception as exc:
            out["errors"]["weather_forecast"] = str(exc)[:200]
        return out

    # ------------------------------------------------------------------
    # apply (GUI thread)
    # ------------------------------------------------------------------
    def fields_apply(self, fetched, name=None, style=True, low=0.3, drop=-0.1, bare=0.15):
        """Zonal statistics per field, a result layer with metrics, a ranked table."""
        from qgis.analysis import QgsZonalStatistics
        subset, label = fetched["subset"], fetched["index"]
        key = label.lower()
        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", name or f"fields_{key}_{(fetched['now']['datetime'] or '')[:10]}", "memory")
        provider = layer.dataProvider()
        attr_keys = []
        for row in subset["fields"]:
            for k in row["attrs"]:
                if k not in attr_keys and k not in ("fid",):
                    attr_keys.append(k)
        attr_keys = attr_keys[:20]
        fields = [QgsField("source_fid", QMetaType.Type.LongLong), QgsField("label", QMetaType.Type.QString),
                  QgsField("acres", QMetaType.Type.Double)]
        for k in attr_keys:
            sample = next((r["attrs"].get(k) for r in subset["fields"] if r["attrs"].get(k) is not None), None)
            t = (QMetaType.Type.Double if isinstance(sample, float) else QMetaType.Type.LongLong
                 if isinstance(sample, int) and not isinstance(sample, bool) else QMetaType.Type.QString)
            fields.append(QgsField(f"src_{k}"[:40], t))
        provider.addAttributes(fields)
        layer.updateFields()
        feats = []
        for row in subset["fields"]:
            f = QgsFeature(layer.fields())
            f.setGeometry(QgsGeometry.fromWkt(row["wkt"]))
            values = [row["fid"], row["label"], row["acres"]] + [row["attrs"].get(k) for k in attr_keys]
            f.setAttributes(values)
            feats.append(f)
        provider.addFeatures(feats)
        layer.updateExtents()

        def zonal(prefix, path):
            raster = QgsRasterLayer(path, prefix, "gdal")
            if not raster.isValid():
                raise IOError(f"Could not open {path}")
            z = QgsZonalStatistics(layer, raster, prefix, 1,
                                   QgsZonalStatistics.Statistic.Mean | QgsZonalStatistics.Statistic.Count
                                   | QgsZonalStatistics.Statistic.Min | QgsZonalStatistics.Statistic.Max)
            if z.calculateStatistics(None) != QgsZonalStatistics.Result.Success:
                raise RuntimeError("Zonal statistics failed")

        zonal(f"{key}_now_", fetched["now"]["path"])
        if fetched.get("prev"):
            zonal(f"{key}_prev_", fetched["prev"]["path"])
        provider = layer.dataProvider()
        provider.addAttributes([QgsField(f"{key}_change", QMetaType.Type.Double),
                                QgsField("status", QMetaType.Type.QString),
                                QgsField("attention", QMetaType.Type.Int)])
        layer.updateFields()
        idx_now = layer.fields().indexOf(f"{key}_now_mean")
        idx_prev = layer.fields().indexOf(f"{key}_prev_mean")
        idx_change = layer.fields().indexOf(f"{key}_change")
        idx_status = layer.fields().indexOf("status")
        idx_att = layer.fields().indexOf("attention")
        rows = []
        changes = {}
        for f in layer.getFeatures():
            now_v = f[idx_now]
            prev_v = f[idx_prev] if idx_prev >= 0 else None
            now_v = float(now_v) if isinstance(now_v, (int, float)) else None
            prev_v = float(prev_v) if isinstance(prev_v, (int, float)) else None
            change = round(now_v - prev_v, 4) if now_v is not None and prev_v is not None else None
            if now_v is None:
                status, score = "no data (clouds or outside scene)", 0
            elif now_v < bare:
                status, score = "bare or harvested", 3
            elif change is not None and change < drop:
                status, score = "declining", 3 if now_v < low else 2
            elif now_v < low:
                status, score = "low vigour", 2
            elif change is not None and change >= -drop - 1e-9:
                status, score = "greening", 0
            else:
                status, score = "healthy", 0
            changes[f.id()] = {idx_change: change, idx_status: status, idx_att: score}
            rows.append({"id": f.id(), "source_fid": f["source_fid"], "label": f["label"], "acres": f["acres"],
                         f"{key}_now": round(now_v, 3) if now_v is not None else None,
                         f"{key}_prev": round(prev_v, 3) if prev_v is not None else None,
                         "change": change, "status": status, "attention": score})
        provider.changeAttributeValues(changes)
        layer.updateFields()
        layer = persist_layer(layer, layer.name())
        if style:  # after persisting: the stored layer is a new object
            self._style_fields(layer, f"{key}_now_mean")
        from .live_commands import _stamp_provenance
        _stamp_provenance(layer, "earth-search", {"index": label, "now": fetched["now"]["item"],
                                                  "prev": (fetched.get("prev") or {}).get("item"),
                                                  "source_layer": subset["layer_id"]})
        QgsProject.instance().addMapLayer(layer)
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        valid = [r for r in rows if r[f"{key}_now"] is not None]
        attention = sorted([r for r in rows if r["attention"] > 0],
                           key=lambda r: (-r["attention"], r[f"{key}_now"] if r[f"{key}_now"] is not None else 9))
        by_status = {}
        for r in rows:
            s = by_status.setdefault(r["status"], {"fields": 0, "acres": 0.0})
            s["fields"] += 1
            s["acres"] = round(s["acres"] + (r["acres"] or 0), 2)
        mean_now = round(sum(r[f"{key}_now"] for r in valid) / len(valid), 3) if valid else None
        with_change = [r for r in valid if r["change"] is not None]
        mean_change = round(sum(r["change"] for r in with_change) / len(with_change), 3) if with_change else None
        return {
            "layer_id": layer.id(), "layer_name": layer.name(), "index": label,
            "fields": len(rows), "acres": subset["total_acres"], "how": subset["how"],
            "truncated": subset["truncated"], "candidates": subset["candidates"],
            "duplicates_skipped": subset.get("duplicates_skipped", 0),
            "now": {k: v for k, v in fetched["now"].items() if k != "path"},
            "previous": ({k: v for k, v in fetched["prev"].items() if k != "path"} if fetched.get("prev") else None),
            "mean_now": mean_now, "mean_change": mean_change, "by_status": by_status,
            "attention": attention[:25], "attention_count": len(attention), "rows": rows,
            "weather": {k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in fetched["weather"].items()},
            "errors": fetched["errors"],
            "thresholds": {"bare_below": bare, "low_below": low, "decline_below": drop},
            "interpretation": (f"{label} per field from the newest clear scene"
                               + (" and the change since the earlier scene" if fetched.get("prev") else "")
                               + f". Fields below {bare} are bare or harvested, below {low} low vigour, a drop of "
                               f"more than {abs(drop)} is declining. Clouds and scene edges leave fields without data."),
        }

    @staticmethod
    def _style_fields(layer, field):
        from qgis.core import QgsFillSymbol, QgsGraduatedSymbolRenderer, QgsRendererRange
        from qgis.PyQt.QtGui import QColor
        breaks = [(-1.0, 0.15, "#d7301f", "bare / harvested"), (0.15, 0.3, "#fc8d59", "low"),
                  (0.3, 0.45, "#fee08b", "moderate"), (0.45, 0.6, "#a6d96a", "good"), (0.6, 1.1, "#1a9641", "dense")]
        ranges = []
        for lo, hi, colour, label in breaks:
            symbol = QgsFillSymbol.createSimple({"color": colour, "outline_color": "#333333", "outline_width": "0.2"})
            ranges.append(QgsRendererRange(lo, hi, symbol, f"{label} ({lo:g} to {hi:g})"))
        renderer = QgsGraduatedSymbolRenderer(field, ranges)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    def analyse_fields(self, layer_id, index="NDVI", selected=None, aoi=None, limit=300, days=45,
                       compare_days=30, collection="sentinel-2-l2a", max_cloud=25, name=None,
                       style=True):
        """Per field vigour, change and status for a polygon layer, as a new styled layer."""
        subset = self.fields_subset(layer_id, selected, aoi, limit)
        fetched = self.fields_fetch(subset, index, days, compare_days, collection, max_cloud)
        return self.fields_apply(fetched, name, style)

    def field_time_series(self, layer_id, feature_id=None, index="NDVI", months=6, max_scenes=10,
                          max_cloud=30, collection="sentinel-2-l2a"):
        """Index history of one field (the selected feature or ``feature_id``), with a chart."""
        from . import charts
        layer = self._vector_or_raise(layer_id)
        if layer.geometryType() != Qgis.GeometryType.Polygon:
            raise ValueError("Pick a polygon layer")
        if feature_id is None:
            ids = layer.selectedFeatureIds()
            if len(ids) != 1:
                raise ValueError("Select exactly one field, or pass feature_id")
            feature_id = ids[0]
        feature = layer.getFeature(int(feature_id))
        if not feature.isValid() or not feature.hasGeometry():
            raise LookupError(f"Feature {feature_id} not found on {layer.name()}")
        geom = QgsGeometry(feature.geometry())
        if layer.crs() != WGS84:
            geom.transform(QgsCoordinateTransform(layer.crs(), WGS84, QgsProject.instance()))
        end = _dt.date.today()
        start = end - _dt.timedelta(days=int(months) * 30)
        series = self.get_index_time_series({"wkt": geom.asWkt(7), "crs": "EPSG:4326"}, collection, index, None,
                                            start.isoformat(), end.isoformat(), max_cloud, max_scenes)
        attrs = dict(zip([f.name() for f in layer.fields()], feature.attributes()))
        label = _label(attrs, _name_fields(layer)) or f"feature {feature_id}"
        series["field"] = label
        series["feature_id"] = int(feature_id)
        series["chart_path"] = charts.chart([((r.get("datetime") or "")[:10], r.get("mean")) for r in series["series"]],
                                            f"{series['index']} of {label}", "index")
        return series

    def export_field_report(self, layer_id, path=None, index="NDVI", selected=None, aoi=None, limit=300,
                            days=45, compare_days=30, title=None):
        """Analyse the fields and write an HTML or PDF report with the ranked table."""
        result = self.analyse_fields(layer_id, index, selected, aoi, limit, days, compare_days)
        return self.write_field_report(result, path, title)

    def write_field_report(self, result, path=None, title=None):
        import html
        from . import charts
        from .toolbox_commands import report_to_pdf
        ext = os.path.splitext(str(path))[1].lower() if path else ".html"
        if ext not in (".html", ".htm", ".pdf"):
            raise ValueError("path must end in .html or .pdf")
        path = path or os.path.join(cache_dir(), f"field_report_{_safe_name(result['layer_name'])[:30]}.html")
        html_path = path[:-4] + ".html" if ext == ".pdf" else path
        key = result["index"].lower()
        esc = lambda v: html.escape("" if v is None else str(v))  # noqa: E731

        def table(rows, cols):
            out = "<table><tr>" + "".join(f"<th>{esc(c)}</th>" for c in cols) + "</tr>"
            for r in rows:
                out += "<tr>" + "".join(f"<td>{esc(r.get(c))}</td>" for c in cols) + "</tr>"
            return out + "</table>"

        status_chart = charts.chart([(k, v["acres"]) for k, v in result["by_status"].items()],
                                    "Acres by status", "acres", "bar", 640, 200)
        now_vals = sorted(r[f"{key}_now"] for r in result["rows"] if r.get(f"{key}_now") is not None)
        dist_chart = charts.chart([(str(i), v) for i, v in enumerate(now_vals)], f"{result['index']} per field, sorted", "", "line", 640, 200) if len(now_vals) > 1 else None
        wx = result.get("weather", {})
        parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title or 'Field report')}</title>",
                 "<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:900px;margin:2em auto;color:#222}"
                 "table{border-collapse:collapse;margin:.5em 0}td,th{border:1px solid #ccc;padding:3px 7px;text-align:left}"
                 "h2{border-bottom:1px solid #ddd;margin-top:1.4em}.small{color:#666;font-size:.9em}</style></head><body>",
                 f"<h1>{esc(title or 'Field report: ' + result['layer_name'])}</h1>",
                 f"<p class='small'>Generated {_dt.datetime.now():%Y-%m-%d %H:%M} by QGIS MCP toolbox. "
                 f"{result['fields']} fields, {result['acres']} acres. Scene {esc(result['now']['item'])} of "
                 f"{esc((result['now']['datetime'] or '')[:10])}"
                 + (f", compared with {esc((result['previous']['datetime'] or '')[:10])}" if result.get("previous") else "") + ".</p>",
                 f"<h2>Summary</h2><p>Mean {esc(result['index'])} <b>{esc(result['mean_now'])}</b>"
                 + (f", mean change <b>{result['mean_change']:+.3f}</b>" if result.get("mean_change") is not None else "") + "</p>",
                 table([dict(status=k, **v) for k, v in result["by_status"].items()], ["status", "fields", "acres"]),
                 charts.img_tag(status_chart, 640), charts.img_tag(dist_chart, 640)]
        if wx.get("history"):
            h = wx["history"]
            parts.append(f"<h2>Weather</h2><p>Last {h.get('days')} days: rain {h.get('rain_mm')} mm, reference evapotranspiration "
                         f"{h.get('et0_mm')} mm, mean daily maximum {h.get('tmax_mean')} °C.</p>")
        if wx.get("forecast"):
            f = wx["forecast"]
            parts.append(f"<p>Next 7 days: rain {f.get('rain_mm')} mm, hottest day {f.get('tmax_max')} °C.</p>")
        cols = ["label", "acres", f"{key}_now", f"{key}_prev", "change", "status"]
        parts.append(f"<h2>Fields needing attention: {result['attention_count']}"
                     + (f" (worst {len(result['attention'])} listed)" if result["attention_count"] > len(result["attention"]) else "")
                     + "</h2>" + (table(result["attention"], cols) if result["attention"]
                                                           else "<p>None: every field with data is healthy or greening.</p>"))
        parts.append("<h2>All fields</h2>" + table(result["rows"], cols))
        parts.append(f"<p class='small'>{esc(result['interpretation'])}</p>")
        if result.get("errors"):
            parts.append("<p class='small'>Not available: " + esc(json.dumps(result["errors"])) + "</p>")
        parts.append("<p class='small'>Sources: Copernicus Sentinel-2 via Earth Search, Open-Meteo. QGIS MCP toolbox by Adil Munawar.</p></body></html>")
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write("".join(parts))
        if ext == ".pdf":
            report_to_pdf(html_path, path)
        return {"path": path, "html_path": html_path, "layer_id": result["layer_id"], "fields": result["fields"],
                "attention": result["attention_count"], "mean_now": result["mean_now"]}
