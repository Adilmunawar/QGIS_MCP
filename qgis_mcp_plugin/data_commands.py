# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""Phase 3 - editing, schema, query and diagnostics.

Mixed into :class:`QgisMCPServer`. These close the gap that previously forced
``execute_code`` - the least safe tool available - for ordinary work like
recoding a column or adding a field.

Anything that changes stored data dry-runs first and needs ``confirm=True``,
matching ``delete_features``: an agent should always be able to show a person
the row count before the rows change.
"""

from qgis.core import (
    QgsAggregateCalculator,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsField,
    QgsGeometry,
    QgsMapLayer,
    QgsPointXY,
    QgsProject,
    QgsProviderRegistry,
    QgsRectangle,
    QgsVectorLayer,
    QgsVirtualLayerDefinition,
)
from qgis.PyQt.QtCore import QMetaType

FIELD_TYPES = {
    "string": QMetaType.Type.QString,
    "text": QMetaType.Type.QString,
    "integer": QMetaType.Type.Int,
    "int": QMetaType.Type.Int,
    "longlong": QMetaType.Type.LongLong,
    "double": QMetaType.Type.Double,
    "real": QMetaType.Type.Double,
    "boolean": QMetaType.Type.Bool,
    "bool": QMetaType.Type.Bool,
    "date": QMetaType.Type.QDate,
    "datetime": QMetaType.Type.QDateTime,
}

#: Northern-hemisphere UTM zones plus the usual web/geographic suspects.
#: Used by detect_layer_crs to rank candidates for an undeclared layer.
def _crs_candidates():
    candidates = ["EPSG:4326", "EPSG:3857"]
    candidates += [f"EPSG:{32600 + zone}" for zone in range(1, 61)]
    candidates += [f"EPSG:{32700 + zone}" for zone in range(1, 61)]
    return candidates


class DataCommandsMixin:
    """Editing, schema, query and diagnostic commands."""

    # ------------------------------------------------------------------
    # editing
    # ------------------------------------------------------------------
    def update_features(self, layer_id, field_values, expression=None,
                        confirm=False, max_scan=None):
        """Set field values on matching features. Dry-runs unless confirmed."""
        layer = self._vector_or_raise(layer_id)
        if not field_values:
            raise ValueError("field_values is required, e.g. {'crop': 'Sugarcane'}")
        indexes = {}
        for name in field_values:
            indexes[name] = self._field_index_or_raise(layer, name)
        if expression:
            self._validate_expression(expression)

        request = QgsFeatureRequest()
        request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
        request.setSubsetOfAttributes([])
        if expression:
            request.setFilterExpression(expression)
        ids = [f.id() for f in layer.getFeatures(request)]

        if not confirm:
            return {
                "layer_id": layer_id,
                "expression": expression,
                "field_values": field_values,
                "would_update": len(ids),
                "updated": 0,
                "confirmed": False,
                "note": "Dry run. Re-issue with confirm=true to apply.",
            }

        changes = {fid: {indexes[name]: value
                         for name, value in field_values.items()}
                   for fid in ids}
        if not layer.dataProvider().changeAttributeValues(changes):
            raise IOError(
                f"Provider rejected the update: {layer.dataProvider().lastError()}")
        layer.triggerRepaint()
        return {"layer_id": layer_id, "expression": expression,
                "field_values": field_values, "updated": len(ids),
                "confirmed": True}

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------
    def add_field(self, layer_id, name, field_type="string", length=0,
                  precision=0):
        layer = self._vector_or_raise(layer_id)
        key = str(field_type).lower()
        if key not in FIELD_TYPES:
            raise ValueError(
                f"Unsupported field type '{field_type}'. "
                f"Use one of: {sorted(set(FIELD_TYPES))}")
        if layer.fields().indexOf(name) >= 0:
            raise ValueError(f"Field already exists: {name}")
        field = QgsField(name, FIELD_TYPES[key])
        if length:
            field.setLength(int(length))
        if precision:
            field.setPrecision(int(precision))
        if not layer.dataProvider().addAttributes([field]):
            raise IOError(f"Provider rejected the new field: {name}")
        layer.updateFields()
        return {"layer_id": layer_id, "added": name, "type": field_type,
                "fields": [f.name() for f in layer.fields()]}

    def delete_field(self, layer_id, name, confirm=False):
        layer = self._vector_or_raise(layer_id)
        index = self._field_index_or_raise(layer, name)
        if not confirm:
            return {"layer_id": layer_id, "would_delete": name, "deleted": None,
                    "confirmed": False,
                    "note": "Dry run. Re-issue with confirm=true to drop the "
                            "field and all its values."}
        if not layer.dataProvider().deleteAttributes([index]):
            raise IOError(f"Provider rejected dropping the field: {name}")
        layer.updateFields()
        return {"layer_id": layer_id, "deleted": name, "confirmed": True,
                "fields": [f.name() for f in layer.fields()]}

    def rename_field(self, layer_id, old_name, new_name):
        layer = self._vector_or_raise(layer_id)
        index = self._field_index_or_raise(layer, old_name)
        if layer.fields().indexOf(new_name) >= 0:
            raise ValueError(f"Field already exists: {new_name}")
        if not layer.dataProvider().renameAttributes({index: new_name}):
            raise IOError(
                "Provider does not support renaming fields on this layer")
        layer.updateFields()
        return {"layer_id": layer_id, "renamed": {old_name: new_name},
                "fields": [f.name() for f in layer.fields()]}

    def create_spatial_index(self, layer_id):
        layer = self._vector_or_raise(layer_id)
        if not layer.dataProvider().createSpatialIndex():
            raise IOError("Provider could not create a spatial index")
        return {"layer_id": layer_id, "indexed": True}

    # ------------------------------------------------------------------
    # query
    # ------------------------------------------------------------------
    @staticmethod
    def _virtual_available():
        return "virtual" in QgsProviderRegistry.instance().providerList()

    def execute_sql(self, query, layer_ids=None, limit=100, wkt_precision=4):
        """Run SQL across project layers using a virtual layer.

        Reference layers by name in the SQL; pass ``layer_ids`` to control which
        are exposed (all vector layers by default).
        """
        if not self._virtual_available():
            raise RuntimeError(
                "The 'virtual' provider is not available in this QGIS install, "
                "so cross-layer SQL cannot run. Use get_layer_features with an "
                "expression instead.")
        project = QgsProject.instance()
        if layer_ids:
            layers = [self._vector_or_raise(lid) for lid in layer_ids]
        else:
            layers = [l for l in project.mapLayers().values()
                      if l.type() == QgsMapLayer.LayerType.VectorLayer]
        if not layers:
            raise ValueError("No vector layers available to query")

        definition = QgsVirtualLayerDefinition()
        exposed = []
        for layer in layers:
            alias = layer.name().replace(" ", "_").replace("-", "_")
            definition.addSource(alias, layer.id())
            exposed.append(alias)
        definition.setQuery(query)
        result = QgsVectorLayer(definition.toString(), "qgis_mcp_query", "virtual")
        if not result.isValid():
            raise ValueError(
                f"Query failed. Exposed layer names: {exposed}. "
                f"Provider said: {result.error().summary() or 'no detail'}")

        fields = list(result.fields())
        rows = []
        for i, feature in enumerate(result.getFeatures()):
            if limit is not None and i >= limit:
                break
            rows.append(self._feature_to_dict(feature, fields, wkt_precision))
        return {"query": query, "exposed_layers": exposed,
                "columns": [f.name() for f in fields],
                "returned": len(rows), "rows": rows}

    def group_statistics(self, layer_id, group_field, value_field=None,
                         aggregate="count", limit=100):
        """Aggregate one field grouped by another - mean yield per district."""
        layer = self._vector_or_raise(layer_id)
        self._field_index_or_raise(layer, group_field)
        if value_field:
            self._field_index_or_raise(layer, value_field)
        names = {
            "count": QgsAggregateCalculator.Aggregate.Count,
            "sum": QgsAggregateCalculator.Aggregate.Sum,
            "mean": QgsAggregateCalculator.Aggregate.Mean,
            "min": QgsAggregateCalculator.Aggregate.Min,
            "max": QgsAggregateCalculator.Aggregate.Max,
            "median": QgsAggregateCalculator.Aggregate.Median,
            "stdev": QgsAggregateCalculator.Aggregate.StDev,
        }
        if aggregate not in names:
            raise ValueError(f"Unknown aggregate '{aggregate}'. "
                             f"Use one of: {sorted(names)}")
        if aggregate != "count" and not value_field:
            raise ValueError(f"aggregate '{aggregate}' needs a value_field")

        groups = {}
        request = QgsFeatureRequest()
        request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
        for feature in layer.getFeatures(request):
            key = feature.attribute(group_field)
            key = "<NULL>" if key is None else str(key)
            # Append for every feature, value_field or not: the per-group count
            # is len(values), so skipping the append would report every group
            # as empty in plain count mode.
            groups.setdefault(key, []).append(
                feature.attribute(value_field) if value_field else None)

        def compute(values):
            numbers = [v for v in values if isinstance(v, (int, float))]
            if aggregate == "count":
                return len(values)
            if not numbers:
                return None
            if aggregate == "sum":
                return sum(numbers)
            if aggregate == "mean":
                return sum(numbers) / len(numbers)
            if aggregate == "min":
                return min(numbers)
            if aggregate == "max":
                return max(numbers)
            if aggregate == "median":
                ordered = sorted(numbers)
                mid = len(ordered) // 2
                return (ordered[mid] if len(ordered) % 2
                        else (ordered[mid - 1] + ordered[mid]) / 2)
            if aggregate == "stdev":
                if len(numbers) < 2:
                    return 0.0
                mean = sum(numbers) / len(numbers)
                variance = sum((n - mean) ** 2 for n in numbers) / (len(numbers) - 1)
                return variance ** 0.5
            return None

        rows = [{"group": key, "count": len(values), aggregate: compute(values)}
                for key, values in groups.items()]
        rows.sort(key=lambda r: (r[aggregate] is None, r[aggregate]), reverse=True)
        truncated = limit is not None and len(rows) > limit
        return {
            "layer_id": layer_id, "group_field": group_field,
            "value_field": value_field, "aggregate": aggregate,
            "group_count": len(groups), "truncated": truncated,
            "groups": rows[:limit] if limit else rows,
        }

    # ------------------------------------------------------------------
    # diagnostics
    # ------------------------------------------------------------------
    def check_geometry_validity(self, layer_id, limit=50, max_scan=None):
        layer = self._vector_or_raise(layer_id)
        scan_cap = max_scan or 200_000
        problems = []
        scanned = empty = 0
        for feature in layer.getFeatures():
            scanned += 1
            if scanned > scan_cap:
                break
            geometry = feature.geometry()
            if geometry is None or geometry.isNull() or geometry.isEmpty():
                empty += 1
                continue
            errors = list(geometry.validateGeometry())
            if errors and len(problems) < limit:
                problems.append({
                    "id": feature.id(),
                    "reasons": [e.what() for e in errors][:3],
                })
        return {
            "layer_id": layer_id, "scanned": scanned,
            "empty_geometries": empty, "invalid_reported": len(problems),
            "capped": scanned > scan_cap, "invalid": problems,
        }

    def find_duplicates(self, layer_id, fields=None, limit=50, max_scan=None):
        """Duplicates by attribute values, or by geometry when fields is empty."""
        layer = self._vector_or_raise(layer_id)
        scan_cap = max_scan or 200_000
        if fields:
            for name in fields:
                self._field_index_or_raise(layer, name)
        seen = {}
        scanned = 0
        for feature in layer.getFeatures():
            scanned += 1
            if scanned > scan_cap:
                break
            if fields:
                key = tuple(str(feature.attribute(n)) for n in fields)
            else:
                geometry = feature.geometry()
                if geometry is None or geometry.isNull():
                    continue
                key = geometry.asWkb().toHex().data().decode("ascii")
            seen.setdefault(key, []).append(feature.id())
        duplicates = [
            {"key": list(k) if isinstance(k, tuple) else "<geometry>",
             "count": len(ids), "ids": ids[:10]}
            for k, ids in seen.items() if len(ids) > 1
        ]
        duplicates.sort(key=lambda d: d["count"], reverse=True)
        return {
            "layer_id": layer_id, "compared_by": fields or "geometry",
            "scanned": scanned, "duplicate_groups": len(duplicates),
            "duplicate_features": sum(d["count"] for d in duplicates),
            "groups": duplicates[:limit],
        }

    def detect_layer_crs(self, layer_id, reference_layer_ids=None, top=5):
        """Infer a plausible CRS for a layer from its coordinate ranges.

        Ranks candidates by how well the layer's extent, interpreted in each
        candidate, lands inside the reference layers' footprint - the same
        reasoning used by hand to identify an undeclared UTM layer.
        """
        layer = self._layer_or_raise(layer_id)
        extent = layer.extent()
        if extent.isNull():
            raise ValueError("Layer extent is unset; nothing to infer from")
        # A single point, or points along one line, gives a zero-width or
        # zero-height extent. That is legitimate data, so widen it slightly
        # rather than refusing - the overlap maths needs a non-zero area.
        if extent.width() <= 0 or extent.height() <= 0:
            pad = max(abs(extent.xMinimum()), abs(extent.yMinimum()), 1.0) * 1e-6
            extent = QgsRectangle(extent.xMinimum() - pad, extent.yMinimum() - pad,
                                  extent.xMaximum() + pad, extent.yMaximum() + pad)

        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        project = QgsProject.instance()

        reference = QgsRectangle()
        sources = reference_layer_ids or [
            lid for lid, other in project.mapLayers().items()
            if lid != layer_id and other.crs().isValid()
        ]
        for lid in sources:
            other = project.mapLayer(lid)
            if other is None or not other.crs().isValid():
                continue
            try:
                transform = QgsCoordinateTransform(other.crs(), wgs84, project)
                reference.combineExtentWith(
                    transform.transformBoundingBox(other.extent()))
            except Exception:
                continue
        has_reference = not reference.isEmpty()

        looks_geographic = (abs(extent.xMinimum()) <= 180
                            and abs(extent.xMaximum()) <= 180
                            and abs(extent.yMinimum()) <= 90
                            and abs(extent.yMaximum()) <= 90)

        scored = []
        for authid in _crs_candidates():
            crs = QgsCoordinateReferenceSystem(authid)
            if not crs.isValid():
                continue
            if looks_geographic != crs.isGeographic():
                continue
            try:
                transform = QgsCoordinateTransform(crs, wgs84, project)
                projected = transform.transformBoundingBox(extent)
            except Exception:
                continue
            if not (-180 <= projected.xMinimum() and projected.xMaximum() <= 180
                    and -90 <= projected.yMinimum()
                    and projected.yMaximum() <= 90):
                continue
            entry = {
                "crs": authid,
                "description": crs.description(),
                "wgs84_extent": self._rect_to_dict(projected),
            }
            if has_reference:
                # A non-overlapping intersect() comes back as a null rectangle
                # whose width/height are huge sentinels, so test before dividing
                # or every disjoint candidate scores infinity.
                overlap = projected.intersect(reference)
                if overlap.isNull() or overlap.isEmpty() \
                        or overlap.width() <= 0 or overlap.height() <= 0:
                    area = 0.0
                else:
                    area = overlap.width() * overlap.height()
                own = projected.width() * projected.height()
                fraction = area / own if own > 0 else 0.0
                entry["overlap_fraction"] = round(fraction, 4)
                entry["score"] = fraction
            else:
                # With nothing to compare against, prefer the candidate whose
                # footprint is most plausibly sized for real data.
                entry["score"] = 0.0
            scored.append(entry)

        scored.sort(key=lambda e: e["score"], reverse=True)
        best = scored[0] if scored else None
        return {
            "layer_id": layer_id,
            "declared_crs": layer.crs().authid() or None,
            "looks_geographic": looks_geographic,
            "compared_against_references": has_reference,
            "reference_extent": self._rect_to_dict(reference) if has_reference else None,
            "best_guess": best["crs"] if best and best["score"] > 0 else None,
            "confident": bool(best and best["score"] > 0.5),
            "candidates": scored[:top],
            "note": "Ranked by overlap with other layers in the project. "
                    "Verify visually before applying with set_layer_crs.",
        }

    # ------------------------------------------------------------------
    # raster
    # ------------------------------------------------------------------
    def _raster_or_raise(self, layer_id):
        layer = self._layer_or_raise(layer_id)
        if layer.type() != QgsMapLayer.LayerType.RasterLayer:
            raise ValueError(f"Layer is not a raster layer: {layer_id}")
        return layer

    def get_raster_statistics(self, layer_id, band=1):
        layer = self._raster_or_raise(layer_id)
        if band < 1 or band > layer.bandCount():
            raise ValueError(
                f"Band {band} out of range; layer has {layer.bandCount()}")
        stats = layer.dataProvider().bandStatistics(band)
        return {
            "layer_id": layer_id, "band": band,
            "band_name": layer.bandName(band),
            "width": layer.width(), "height": layer.height(),
            "crs": layer.crs().authid(),
            "statistics": {
                "minimum": stats.minimumValue,
                "maximum": stats.maximumValue,
                "mean": stats.mean,
                "stdev": stats.stdDev,
                "range": stats.range,
                "sum": stats.sum,
            },
        }

    def sample_raster_values(self, layer_id, points, band=None):
        """Read band values at points, given as [{"x":..,"y":..}] or WKT."""
        layer = self._raster_or_raise(layer_id)
        provider = layer.dataProvider()
        project = QgsProject.instance()
        transform = None
        if project.crs().isValid() and project.crs() != layer.crs():
            transform = QgsCoordinateTransform(project.crs(), layer.crs(), project)

        samples = []
        for i, spec in enumerate(points or []):
            if isinstance(spec, dict) and "x" in spec and "y" in spec:
                point = QgsPointXY(float(spec["x"]), float(spec["y"]))
            elif isinstance(spec, str):
                geometry = QgsGeometry.fromWkt(spec)
                if geometry.isNull():
                    raise ValueError(f"Point {i}: could not parse WKT: {spec}")
                point = geometry.asPoint()
            else:
                raise ValueError(
                    f"Point {i}: expected {{'x':..,'y':..}} or a WKT string")
            if transform is not None:
                point = transform.transform(point)
            values, ok = provider.sample(point, band or 1)
            entry = {"x": point.x(), "y": point.y(), "valid": bool(ok)}
            if band:
                entry["value"] = values if ok else None
            else:
                readings = {}
                for b in range(1, layer.bandCount() + 1):
                    value, valid = provider.sample(point, b)
                    readings[layer.bandName(b)] = value if valid else None
                entry["values"] = readings
            samples.append(entry)
        return {"layer_id": layer_id, "sampled": len(samples),
                "samples": samples}
