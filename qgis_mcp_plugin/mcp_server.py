# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""Command handlers for the QGIS MCP bridge.

Transport, authentication and permission gating live in ``_server_core``;
this module is the command surface itself.
"""

import base64
import io
import os
import sys
import traceback

from qgis.core import (
    Qgis,
    QgsAggregateCalculator,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsExpression,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsLayoutExporter,
    QgsMapLayer,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QBuffer, QSize
from qgis.PyQt.QtGui import QColor
from qgis.utils import active_plugins

from ._server_core import (
    DEFAULT_MAX_SCAN, PLUGIN_VERSION, PROTOCOL_VERSION, ServerCore,
)
from . import net
from .data_commands import DataCommandsMixin
from .live_commands import LiveDataMixin
from .toolbox_commands import ToolboxMixin
from .advanced_commands import AdvancedMixin
from .sensor_commands import SensorsMixin

_EXPORT_SUCCESS = getattr(
    getattr(QgsLayoutExporter, "ExportResult", QgsLayoutExporter), "Success", 0
)

#: Memory-provider field type keywords, for create_memory_layer.
MEMORY_FIELD_TYPES = {
    "string", "integer", "int", "double", "real", "long", "longlong",
    "date", "datetime", "time", "boolean", "bool", "binary",
}

GEOMETRY_KEYWORDS = {
    "point", "linestring", "polygon", "multipoint", "multilinestring",
    "multipolygon", "none", "nogeometry",
}


class QgisMCPServer(SensorsMixin, AdvancedMixin, ToolboxMixin, LiveDataMixin, DataCommandsMixin, ServerCore):
    """The full command surface, mounted on the gated transport."""

    def _handlers(self):
        return {
            # diagnostics
            "ping": self.ping,
            "get_qgis_info": self.get_qgis_info,
            "get_server_info": self.get_server_info,
            "get_audit_log": self.get_audit_log,
            # project
            "load_project": self.load_project,
            "create_new_project": self.create_new_project,
            "get_project_info": self.get_project_info,
            "save_project": self.save_project,
            "set_project_crs": self.set_project_crs,
            # code
            "execute_code": self.execute_code,
            # layers
            "add_vector_layer": self.add_vector_layer,
            "add_raster_layer": self.add_raster_layer,
            "create_memory_layer": self.create_memory_layer,
            "get_layers": self.get_layers,
            "remove_layer": self.remove_layer,
            "reload_layer": self.reload_layer,
            "zoom_to_layer": self.zoom_to_layer,
            "set_layer_visibility": self.set_layer_visibility,
            "set_layer_opacity": self.set_layer_opacity,
            "set_layer_crs": self.set_layer_crs,
            "get_layer_fields": self.get_layer_fields,
            "get_layer_extent": self.get_layer_extent,
            "export_layer": self.export_layer,
            # styling
            "set_layer_style": self.set_layer_style,
            "save_layer_style": self.save_layer_style,
            # features and analysis
            "get_layer_features": self.get_layer_features,
            "count_features": self.count_features,
            "get_unique_values": self.get_unique_values,
            "get_field_statistics": self.get_field_statistics,
            "set_layer_filter": self.set_layer_filter,
            "get_layer_filter": self.get_layer_filter,
            "clear_layer_filter": self.clear_layer_filter,
            "add_features": self.add_features,
            "delete_features": self.delete_features,
            "select_features_by_expression": self.select_features_by_expression,
            "get_selected_features": self.get_selected_features,
            "clear_selection": self.clear_selection,
            "zoom_to_selection": self.zoom_to_selection,
            # processing
            "execute_processing": self.execute_processing,
            "list_processing_algorithms": self.list_processing_algorithms,
            "get_processing_algorithm_help": self.get_processing_algorithm_help,
            # canvas, rendering, layouts
            "get_canvas_state": self.get_canvas_state,
            "set_canvas_extent": self.set_canvas_extent,
            "render_map": self.render_map,
            "render_map_base64": self.render_map_base64,
            "list_layouts": self.list_layouts,
            "export_layout": self.export_layout,
            # editing and schema
            "update_features": self.update_features,
            "add_field": self.add_field,
            "delete_field": self.delete_field,
            "rename_field": self.rename_field,
            "create_spatial_index": self.create_spatial_index,
            # query and diagnostics
            "execute_sql": self.execute_sql,
            "group_statistics": self.group_statistics,
            "check_geometry_validity": self.check_geometry_validity,
            "find_duplicates": self.find_duplicates,
            "detect_layer_crs": self.detect_layer_crs,
            # raster
            "get_raster_statistics": self.get_raster_statistics,
            "sample_raster_values": self.sample_raster_values,
            # live data: discovery and places
            "list_data_sources": self.list_data_sources,
            "resolve_aoi": self.resolve_aoi,
            "set_data_source_key": self.set_data_source_key,
            "geocode": self.geocode,
            "reverse_geocode": self.reverse_geocode,
            "add_place_marker": self.add_place_marker,
            # live data: imagery and elevation
            "search_imagery": self.search_imagery,
            "add_imagery": self.add_imagery,
            "compute_spectral_index": self.compute_spectral_index,
            "get_index_time_series": self.get_index_time_series,
            "add_dem": self.add_dem,
            "derive_terrain": self.derive_terrain,
            # live data: weather and climate
            "get_weather": self.get_weather,
            "get_weather_history": self.get_weather_history,
            "get_climate_series": self.get_climate_series,
            "attach_weather_to_layer": self.attach_weather_to_layer,
            # live data: basemaps, OSM, hazards
            "list_basemaps": self.list_basemaps,
            "add_basemap": self.add_basemap,
            "fetch_osm_features": self.fetch_osm_features,
            "fetch_earthquakes": self.fetch_earthquakes,
            "fetch_active_fires": self.fetch_active_fires,
            # toolbox: datasets, soil, water, boundaries, species, sensors, alerts
            "list_datasets": self.list_datasets,
            "add_dataset": self.add_dataset,
            "summarise_land_cover": self.summarise_land_cover,
            "get_soil_properties": self.get_soil_properties,
            "attach_soil_to_layer": self.attach_soil_to_layer,
            "get_river_discharge": self.get_river_discharge,
            "get_marine_conditions": self.get_marine_conditions,
            "get_elevation": self.get_elevation,
            "add_admin_boundaries": self.add_admin_boundaries,
            "fetch_species_occurrences": self.fetch_species_occurrences,
            "fetch_air_sensors": self.fetch_air_sensors,
            "fetch_disaster_alerts": self.fetch_disaster_alerts,
            "compute_index_change": self.compute_index_change,
            "zonal_index_statistics": self.zonal_index_statistics,
            "add_imagery_series": self.add_imagery_series,
            "export_area_report": self.export_area_report,
            "get_climate_projection": self.get_climate_projection,
            "get_seasonal_forecast": self.get_seasonal_forecast,
            "get_elevation_profile": self.get_elevation_profile,
            "make_3d_buildings": self.make_3d_buildings,
            "add_3d_buildings": self.add_3d_buildings,
            "set_terrain": self.set_terrain,
            "add_point_cloud": self.add_point_cloud,
            "open_3d_view": self.open_3d_view,
            "fetch_natural_events": self.fetch_natural_events,
            "fetch_air_quality_stations": self.fetch_air_quality_stations,
            "start_live_monitor": self.start_live_monitor,
            "stop_live_monitor": self.stop_live_monitor,
            "get_live_monitor_status": self.get_live_monitor_status,
            "fetch_river_gauges": self.fetch_river_gauges,
            "fetch_buoys": self.fetch_buoys,
            "fetch_tide_stations": self.fetch_tide_stations,
            "fetch_aircraft": self.fetch_aircraft,
            "list_sensorthings_servers": self.list_sensorthings_servers,
            "discover_sensorthings": self.discover_sensorthings,
            "add_sensorthings_layer": self.add_sensorthings_layer,
            "estimate_population": self.estimate_population,
            "add_3d_tiles": self.add_3d_tiles,
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _canvas(self):
        if self.iface is None:
            raise RuntimeError("No map canvas available (QGIS running headless)")
        return self.iface.mapCanvas()

    @staticmethod
    def _layer_or_raise(layer_id):
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            names = [f"{l.name()} ({lid})" for lid, l in
                     list(QgsProject.instance().mapLayers().items())[:8]]
            raise LookupError(
                f"Layer not found: {layer_id}. Use get_layers to list ids"
                + (f"; current layers: {names}" if names else "; the project has no layers"))
        return layer

    @classmethod
    def _vector_or_raise(cls, layer_id):
        layer = cls._layer_or_raise(layer_id)
        if layer.type() != QgsMapLayer.LayerType.VectorLayer:
            raise ValueError(f"Layer is not a vector layer: {layer_id}")
        return layer

    @staticmethod
    def _field_index_or_raise(layer, field):
        idx = layer.fields().indexOf(field)
        if idx < 0:
            raise LookupError(
                f"Field not found: {field}. Available: "
                f"{[f.name() for f in layer.fields()]}"
            )
        return idx

    @staticmethod
    def _validate_expression(expression):
        expr = QgsExpression(expression)
        if expr.hasParserError():
            raise ValueError(
                f"Invalid expression '{expression}': {expr.parserErrorString()}")
        return expr

    @staticmethod
    def _layer_type(layer):
        if layer.type() == QgsMapLayer.LayerType.VectorLayer:
            return f"vector_{int(layer.geometryType())}"
        if layer.type() == QgsMapLayer.LayerType.RasterLayer:
            return "raster"
        return str(layer.type())

    @staticmethod
    def _is_visible(layer_id):
        node = QgsProject.instance().layerTreeRoot().findLayer(layer_id)
        return bool(node and node.isVisible())

    @staticmethod
    def _rect_to_dict(rect):
        return {
            "xmin": rect.xMinimum(), "ymin": rect.yMinimum(),
            "xmax": rect.xMaximum(), "ymax": rect.yMaximum(),
        }

    @staticmethod
    def _feature_to_dict(feature, fields, wkt_precision):
        attrs = {field.name(): feature.attribute(field.name()) for field in fields}
        geom = None
        if feature.hasGeometry():
            geometry = feature.geometry()
            gtype = geometry.type()
            geom = {
                "type": int(gtype),
                "type_name": getattr(gtype, "name", str(gtype)),
                "wkt": geometry.asWkt(precision=wkt_precision),
            }
        return {"id": feature.id(), "attributes": attrs, "geometry": geom}

    @staticmethod
    def _count(layer, expression=None, max_scan=DEFAULT_MAX_SCAN):
        """Exact count by iteration.

        ``featureCount()`` is a provider-cached estimate that goes stale the
        moment a subset string is applied, so anything user-visible counts for
        real - bounded by ``max_scan`` so a huge layer cannot hang the GUI.
        """
        request = QgsFeatureRequest()
        request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
        request.setSubsetOfAttributes([])
        if expression:
            request.setFilterExpression(expression)
        total = 0
        for _ in layer.getFeatures(request):
            total += 1
            if total >= max_scan:
                return total, True
        return total, False

    # ------------------------------------------------------------------
    # diagnostics
    # ------------------------------------------------------------------
    def ping(self):
        return {"pong": True, "protocol_version": PROTOCOL_VERSION}

    def get_qgis_info(self):
        return {
            "qgis_version": Qgis.version(),
            "qgis_version_int": Qgis.versionInt(),
            "profile_folder": QgsApplication.qgisSettingsDirPath(),
            "plugins_count": len(active_plugins),
            "python_version": sys.version.split()[0],
        }

    def get_server_info(self):
        return {
            "plugin_version": PLUGIN_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "host": self.host,
            "port": self.port,
            "clients": self.client_count(),
            "client_list": self.client_list(),
            "requests_served": self.requests_served,
            "uptime_seconds": self.uptime_seconds(),
            "requires_token": self.require_token,
            "permissions": self.permissions.snapshot(),
            "commands": sorted(self._handlers()),
        }

    def get_audit_log(self, limit=50, command=None, only_failures=False):
        if self.audit is None:
            raise RuntimeError("Auditing is not enabled on this server")
        rows = self.audit.tail(limit=limit, command=command,
                               only_failures=only_failures)
        return {"count": len(rows), "path": self.audit.path, "entries": rows}

    def get_project_info(self, layer_limit=50):
        project = QgsProject.instance()
        layers = list(project.mapLayers().items())
        return {
            "filename": project.fileName(),
            "title": project.title(),
            "layer_count": len(layers),
            "crs": project.crs().authid(),
            "layers_truncated": len(layers) > layer_limit,
            "layers": [
                {
                    "id": lid,
                    "name": layer.name(),
                    "type": self._layer_type(layer),
                    "visible": self._is_visible(lid),
                }
                for lid, layer in layers[:layer_limit]
            ],
        }

    def save_project(self, path=None):
        project = QgsProject.instance()
        target = path or project.fileName()
        if not target:
            raise ValueError("No project path specified and no current project path")
        if not project.write(target):
            raise IOError(f"Failed to save project to {target}")
        return {"saved": target}

    def load_project(self, path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Project file does not exist: {path}")
        project = QgsProject.instance()
        if not project.read(path):
            raise IOError(f"Failed to load project from {path}")
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        return {"loaded": path, "layer_count": len(project.mapLayers())}

    def create_new_project(self, path):
        project = QgsProject.instance()
        project.clear()
        project.setFileName(path)
        if not project.crs().isValid():
            project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        if not project.write():
            raise IOError(f"Failed to save project to {path}")
        return {"created": path, "layer_count": len(project.mapLayers())}

    def set_project_crs(self, authid):
        crs = QgsCoordinateReferenceSystem(authid)
        if not crs.isValid():
            raise ValueError(f"Invalid CRS: {authid}")
        QgsProject.instance().setCrs(crs)
        return {"crs": crs.authid(), "description": crs.description()}

    # ------------------------------------------------------------------
    # code
    # ------------------------------------------------------------------
    def execute_code(self, code):
        stdout_capture, stderr_capture = io.StringIO(), io.StringIO()
        original_stdout, original_stderr = sys.stdout, sys.stderr
        namespace = {
            "qgis": Qgis,
            "iface": self.iface,
            "QgsProject": QgsProject,
            "QgsApplication": QgsApplication,
            "QgsVectorLayer": QgsVectorLayer,
            "QgsRasterLayer": QgsRasterLayer,
            "QgsCoordinateReferenceSystem": QgsCoordinateReferenceSystem,
            "QgsRectangle": QgsRectangle,
            "QgsFeature": QgsFeature,
            "QgsGeometry": QgsGeometry,
        }
        try:
            sys.stdout, sys.stderr = stdout_capture, stderr_capture
            exec(code, namespace)
            return {
                "executed": True,
                "stdout": stdout_capture.getvalue(),
                "stderr": stderr_capture.getvalue(),
                "result": namespace.get("result"),
            }
        except Exception as exc:
            return {
                "executed": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
                "stdout": stdout_capture.getvalue(),
                "stderr": stderr_capture.getvalue(),
            }
        finally:
            sys.stdout, sys.stderr = original_stdout, original_stderr

    # ------------------------------------------------------------------
    # layers
    # ------------------------------------------------------------------
    def add_vector_layer(self, path, name=None, provider="ogr"):
        layer = QgsVectorLayer(path, name or os.path.basename(path), provider)
        if not layer.isValid():
            raise ValueError(f"Layer is not valid: {path}")
        QgsProject.instance().addMapLayer(layer)
        return {
            "id": layer.id(), "name": layer.name(),
            "type": self._layer_type(layer),
            "feature_count": layer.featureCount(),
            "crs": layer.crs().authid(),
        }

    def add_raster_layer(self, path, name=None, provider="gdal"):
        layer = QgsRasterLayer(path, name or os.path.basename(path), provider)
        if not layer.isValid():
            raise ValueError(f"Layer is not valid: {path}")
        QgsProject.instance().addMapLayer(layer)
        return {
            "id": layer.id(), "name": layer.name(), "type": "raster",
            "width": layer.width(), "height": layer.height(),
            "band_count": layer.bandCount(), "crs": layer.crs().authid(),
        }

    def create_memory_layer(self, name, geometry_type="Point", crs="EPSG:4326",
                            fields=None, add_to_project=True):
        """Create a scratch layer, e.g. fields={"name": "string", "n": "integer"}."""
        if geometry_type.lower() not in GEOMETRY_KEYWORDS:
            raise ValueError(
                f"Unsupported geometry_type '{geometry_type}'. "
                f"Use one of: {sorted(GEOMETRY_KEYWORDS)}")
        parts = [geometry_type, f"crs={crs}"]
        for fname, ftype in (fields or {}).items():
            if str(ftype).lower() not in MEMORY_FIELD_TYPES:
                raise ValueError(
                    f"Unsupported field type '{ftype}' for '{fname}'. "
                    f"Use one of: {sorted(MEMORY_FIELD_TYPES)}")
            parts.append(f"field={fname}:{ftype}")
        uri = parts[0] + "?" + "&".join(parts[1:])
        layer = QgsVectorLayer(uri, name, "memory")
        if not layer.isValid():
            raise ValueError(f"Could not create memory layer from uri: {uri}")
        if add_to_project:
            QgsProject.instance().addMapLayer(layer)
        return {
            "id": layer.id(), "name": layer.name(), "uri": uri,
            "crs": layer.crs().authid(),
            "fields": [f.name() for f in layer.fields()],
        }

    def get_layers(self):
        layers = []
        for layer_id, layer in QgsProject.instance().mapLayers().items():
            info = {
                "id": layer_id,
                "name": layer.name(),
                "type": self._layer_type(layer),
                "visible": self._is_visible(layer_id),
                "crs": layer.crs().authid(),
                "source": net.redact(layer.source()),
            }
            if layer.type() == QgsMapLayer.LayerType.VectorLayer:
                info["feature_count"] = layer.featureCount()
                info["geometry_type"] = int(layer.geometryType())
                info["field_count"] = len(layer.fields())
                info["filter"] = layer.subsetString()
            elif layer.type() == QgsMapLayer.LayerType.RasterLayer:
                info["width"] = layer.width()
                info["height"] = layer.height()
                info["band_count"] = layer.bandCount()
            layers.append(info)
        return layers

    def remove_layer(self, layer_id):
        self._layer_or_raise(layer_id)
        QgsProject.instance().removeMapLayer(layer_id)
        return {"removed": layer_id}

    def reload_layer(self, layer_id):
        layer = self._layer_or_raise(layer_id)
        layer.reload()
        layer.updateExtents() if hasattr(layer, "updateExtents") else None
        layer.triggerRepaint()
        return {"reloaded": layer_id, "crs": layer.crs().authid()}

    def zoom_to_layer(self, layer_id):
        layer = self._layer_or_raise(layer_id)
        canvas = self._canvas()
        self.iface.setActiveLayer(layer)
        self.iface.zoomToActiveLayer()
        return {"zoomed_to": layer_id, "extent": self._rect_to_dict(canvas.extent())}

    def set_layer_visibility(self, layer_id, visible=True):
        self._layer_or_raise(layer_id)
        node = QgsProject.instance().layerTreeRoot().findLayer(layer_id)
        if node is None:
            raise LookupError(f"Layer not in layer tree: {layer_id}")
        node.setItemVisibilityChecked(bool(visible))
        return {"layer_id": layer_id, "visible": bool(visible)}

    def set_layer_opacity(self, layer_id, opacity):
        layer = self._layer_or_raise(layer_id)
        value = float(opacity)
        if not 0.0 <= value <= 1.0:
            raise ValueError("opacity must be between 0.0 and 1.0")
        layer.setOpacity(value)
        layer.triggerRepaint()
        return {"layer_id": layer_id, "opacity": value}

    def set_layer_crs(self, layer_id, authid):
        """Declare a layer's CRS. Does NOT reproject - it relabels."""
        layer = self._layer_or_raise(layer_id)
        crs = QgsCoordinateReferenceSystem(authid)
        if not crs.isValid():
            raise ValueError(f"Invalid CRS: {authid}")
        previous = layer.crs().authid()
        layer.setCrs(crs)
        layer.triggerRepaint()
        return {
            "layer_id": layer_id,
            "previous_crs": previous,
            "crs": crs.authid(),
            "note": "Coordinates were relabelled, not reprojected.",
        }

    def get_layer_fields(self, layer_id):
        layer = self._vector_or_raise(layer_id)
        return {
            "layer_id": layer_id,
            "fields": [
                {
                    "name": f.name(),
                    "type": f.typeName(),
                    "length": f.length(),
                    "precision": f.precision(),
                    "comment": f.comment(),
                }
                for f in layer.fields()
            ],
        }

    def get_layer_extent(self, layer_id):
        layer = self._layer_or_raise(layer_id)
        return {
            "layer_id": layer_id,
            "crs": layer.crs().authid(),
            "extent": self._rect_to_dict(layer.extent()),
        }

    def export_layer(self, layer_id, path, driver="GPKG", only_selected=False,
                     layer_name=None, crs=None):
        layer = self._vector_or_raise(layer_id)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = driver
        options.onlySelectedFeatures = bool(only_selected)
        if layer_name:
            options.layerName = layer_name
        if crs:
            target = QgsCoordinateReferenceSystem(crs)
            if not target.isValid():
                raise ValueError(f"Invalid CRS: {crs}")
            options.ct = None
            options.destCRS = target
        directory = os.path.dirname(os.path.abspath(path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, path, QgsProject.instance().transformContext(), options)
        error_code, message = result[0], result[1]
        if error_code != QgsVectorFileWriter.WriterError.NoError:
            raise IOError(f"Export failed ({error_code}): {message}")
        return {"exported": path, "driver": driver,
                "only_selected": bool(only_selected)}

    # ------------------------------------------------------------------
    # styling
    # ------------------------------------------------------------------
    def set_layer_style(self, layer_id, qml_path):
        layer = self._layer_or_raise(layer_id)
        if not os.path.exists(qml_path):
            raise FileNotFoundError(f"Style file does not exist: {qml_path}")
        message, ok = layer.loadNamedStyle(qml_path)
        if not ok:
            raise IOError(f"Failed to load style: {message}")
        layer.triggerRepaint()
        return {"layer_id": layer_id, "style": qml_path, "message": message}

    def save_layer_style(self, layer_id, qml_path):
        layer = self._layer_or_raise(layer_id)
        message, ok = layer.saveNamedStyle(qml_path)
        if not ok:
            raise IOError(f"Failed to save style: {message}")
        return {"layer_id": layer_id, "style": qml_path, "message": message}

    # ------------------------------------------------------------------
    # features and analysis
    # ------------------------------------------------------------------
    def get_layer_features(self, layer_id, limit=10, expression=None,
                           wkt_precision=4, fields=None):
        layer = self._vector_or_raise(layer_id)
        all_fields = list(layer.fields())
        wanted = all_fields
        request = QgsFeatureRequest()
        if expression:
            self._validate_expression(expression)
            request.setFilterExpression(expression)
        if fields:
            wanted = [f for f in all_fields if f.name() in set(fields)]
            missing = set(fields) - {f.name() for f in wanted}
            if missing:
                raise LookupError(f"Fields not found: {sorted(missing)}")
            request.setSubsetOfAttributes([f.name() for f in wanted],
                                          layer.fields())
        features = []
        for i, feature in enumerate(layer.getFeatures(request)):
            if limit is not None and i >= limit:
                break
            features.append(self._feature_to_dict(feature, wanted, wkt_precision))
        return {
            "layer_id": layer_id,
            "feature_count": layer.featureCount(),
            "returned": len(features),
            "fields": [f.name() for f in wanted],
            "features": features,
        }

    def count_features(self, layer_id, expression=None, max_scan=DEFAULT_MAX_SCAN):
        layer = self._vector_or_raise(layer_id)
        if expression:
            self._validate_expression(expression)
        total, capped = self._count(layer, expression, max_scan)
        return {
            "layer_id": layer_id,
            "expression": expression,
            "count": total,
            "capped_at_max_scan": capped,
        }

    def get_unique_values(self, layer_id, field, limit=100, include_counts=True,
                          max_scan=DEFAULT_MAX_SCAN):
        layer = self._vector_or_raise(layer_id)
        idx = self._field_index_or_raise(layer, field)
        if not include_counts:
            values = list(layer.uniqueValues(idx, limit or -1))
            return {"layer_id": layer_id, "field": field,
                    "unique_count": len(values), "values": values}

        request = QgsFeatureRequest()
        request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
        request.setSubsetOfAttributes([idx])
        counts = {}
        scanned = 0
        for feature in layer.getFeatures(request):
            value = feature.attribute(idx)
            key = "<NULL>" if value is None else str(value)
            counts[key] = counts.get(key, 0) + 1
            scanned += 1
            if scanned >= max_scan:
                break
        ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        truncated = limit is not None and len(ordered) > limit
        if truncated:
            ordered = ordered[:limit]
        return {
            "layer_id": layer_id,
            "field": field,
            "unique_count": len(counts),
            "scanned": scanned,
            "truncated": truncated,
            "values": [{"value": v, "count": n} for v, n in ordered],
        }

    def get_field_statistics(self, layer_id, field):
        layer = self._vector_or_raise(layer_id)
        self._field_index_or_raise(layer, field)
        aggregates = {
            "count": QgsAggregateCalculator.Aggregate.Count,
            "count_distinct": QgsAggregateCalculator.Aggregate.CountDistinct,
            "count_missing": QgsAggregateCalculator.Aggregate.CountMissing,
            "min": QgsAggregateCalculator.Aggregate.Min,
            "max": QgsAggregateCalculator.Aggregate.Max,
            "sum": QgsAggregateCalculator.Aggregate.Sum,
            "mean": QgsAggregateCalculator.Aggregate.Mean,
            "median": QgsAggregateCalculator.Aggregate.Median,
            "stdev": QgsAggregateCalculator.Aggregate.StDev,
        }
        stats = {}
        for label, aggregate in aggregates.items():
            try:
                value, ok = layer.aggregate(aggregate, field)
            except Exception:
                continue
            # Non-numeric fields simply have no mean; report only what computed.
            if ok and value is not None:
                stats[label] = value
        return {"layer_id": layer_id, "field": field, "statistics": stats}

    def get_layer_filter(self, layer_id):
        layer = self._vector_or_raise(layer_id)
        return {"layer_id": layer_id, "filter": layer.subsetString()}

    def set_layer_filter(self, layer_id, expression, max_scan=DEFAULT_MAX_SCAN):
        """Apply a provider-level subset filter and report the true match count."""
        layer = self._vector_or_raise(layer_id)
        self._validate_expression(expression)
        previous = layer.subsetString()
        before, _ = self._count(layer, None, max_scan)
        if not layer.setSubsetString(expression):
            layer.setSubsetString(previous)
            raise ValueError(
                f"Provider rejected the filter expression: {expression}")
        after, capped = self._count(layer, None, max_scan)
        layer.updateExtents()
        layer.triggerRepaint()
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        return {
            "layer_id": layer_id,
            "previous_filter": previous,
            "filter": layer.subsetString(),
            "count_before": before,
            "count_after": after,
            "capped_at_max_scan": capped,
        }

    def clear_layer_filter(self, layer_id, max_scan=DEFAULT_MAX_SCAN):
        layer = self._vector_or_raise(layer_id)
        previous = layer.subsetString()
        layer.setSubsetString("")
        after, capped = self._count(layer, None, max_scan)
        layer.updateExtents()
        layer.triggerRepaint()
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        return {"layer_id": layer_id, "previous_filter": previous,
                "count_after": after, "capped_at_max_scan": capped}

    def add_features(self, layer_id, features):
        """features: [{"attributes": {...}, "wkt": "POINT(1 2)"}, ...]"""
        layer = self._vector_or_raise(layer_id)
        provider = layer.dataProvider()
        fields = layer.fields()
        to_add = []
        for i, spec in enumerate(features or []):
            feature = QgsFeature(fields)
            for key, value in (spec.get("attributes") or {}).items():
                if fields.indexOf(key) < 0:
                    raise LookupError(f"Field not found on layer: {key}")
                feature.setAttribute(key, value)
            wkt = spec.get("wkt")
            if wkt:
                geometry = QgsGeometry.fromWkt(wkt)
                if geometry.isNull():
                    raise ValueError(f"Feature {i}: could not parse WKT: {wkt}")
                feature.setGeometry(geometry)
            to_add.append(feature)
        ok, added = provider.addFeatures(to_add)
        if not ok:
            raise IOError(f"Provider rejected the features: {provider.lastError()}")
        layer.updateExtents()
        layer.triggerRepaint()
        return {"layer_id": layer_id, "added": len(added)}

    def delete_features(self, layer_id, expression, confirm=False,
                        max_scan=DEFAULT_MAX_SCAN):
        """Delete features matching an expression. Destructive, so opt-in."""
        layer = self._vector_or_raise(layer_id)
        if not expression:
            raise ValueError("An expression is required; refusing to delete all")
        self._validate_expression(expression)
        matched, capped = self._count(layer, expression, max_scan)
        if not confirm:
            return {
                "layer_id": layer_id,
                "expression": expression,
                "would_delete": matched,
                "deleted": 0,
                "confirmed": False,
                "note": "Dry run. Re-issue with confirm=true to delete.",
            }
        request = QgsFeatureRequest().setFilterExpression(expression)
        request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
        request.setSubsetOfAttributes([])
        ids = [f.id() for f in layer.getFeatures(request)]
        if not layer.dataProvider().deleteFeatures(ids):
            raise IOError("Provider rejected the delete")
        layer.updateExtents()
        layer.triggerRepaint()
        return {"layer_id": layer_id, "expression": expression,
                "deleted": len(ids), "confirmed": True,
                "capped_at_max_scan": capped}

    def select_features_by_expression(self, layer_id, expression):
        layer = self._vector_or_raise(layer_id)
        self._validate_expression(expression)
        layer.selectByExpression(expression)
        return {"layer_id": layer_id, "selected_count": layer.selectedFeatureCount()}

    def get_selected_features(self, layer_id, limit=10, wkt_precision=4):
        layer = self._vector_or_raise(layer_id)
        fields = list(layer.fields())
        features = [
            self._feature_to_dict(f, fields, wkt_precision)
            for f in list(layer.selectedFeatures())[:limit]
        ]
        return {
            "layer_id": layer_id,
            "selected_count": layer.selectedFeatureCount(),
            "returned": len(features),
            "features": features,
        }

    def clear_selection(self, layer_id=None):
        if layer_id:
            self._vector_or_raise(layer_id).removeSelection()
            return {"cleared": [layer_id]}
        cleared = []
        for lid, layer in QgsProject.instance().mapLayers().items():
            if layer.type() == QgsMapLayer.LayerType.VectorLayer:
                layer.removeSelection()
                cleared.append(lid)
        return {"cleared": cleared}

    def zoom_to_selection(self, layer_id):
        layer = self._vector_or_raise(layer_id)
        if layer.selectedFeatureCount() == 0:
            raise ValueError(f"No features selected on layer: {layer_id}")
        canvas = self._canvas()
        self.iface.setActiveLayer(layer)
        canvas.zoomToSelected(layer)
        return {"layer_id": layer_id,
                "selected_count": layer.selectedFeatureCount(),
                "extent": self._rect_to_dict(canvas.extent())}

    # ------------------------------------------------------------------
    # processing
    # ------------------------------------------------------------------
    @staticmethod
    def _processing_registry():
        try:
            from processing.core.Processing import Processing
            Processing.initialize()
        except Exception:
            pass
        return QgsApplication.processingRegistry()

    def list_processing_algorithms(self, filter_text=None, limit=100):
        registry = self._processing_registry()
        needle = (filter_text or "").lower()
        results = []
        for alg in registry.algorithms():
            if needle and needle not in f"{alg.id()} {alg.displayName()}".lower():
                continue
            provider = alg.provider()
            results.append({
                "id": alg.id(),
                "name": alg.displayName(),
                "provider": provider.name() if provider else None,
            })
            if limit is not None and len(results) >= limit:
                break
        return {"count": len(results), "algorithms": results}

    def get_processing_algorithm_help(self, algorithm_id):
        alg = self._processing_registry().algorithmById(algorithm_id)
        if alg is None:
            raise LookupError(f"Algorithm not found: {algorithm_id}")
        return {
            "id": alg.id(),
            "name": alg.displayName(),
            "help": alg.shortHelpString(),
            "parameters": [
                {
                    "name": p.name(),
                    "description": p.description(),
                    "type": p.type(),
                }
                for p in alg.parameterDefinitions()
            ],
        }

    def execute_processing(self, algorithm, parameters=None):
        import processing
        self._processing_registry()
        result = processing.run(algorithm, parameters or {})
        return {
            "algorithm": algorithm,
            "result": {
                k: (v if isinstance(v, (int, float, bool, type(None))) else str(v))
                for k, v in result.items()
            },
        }

    # ------------------------------------------------------------------
    # canvas, rendering, layouts
    # ------------------------------------------------------------------
    def get_canvas_state(self):
        canvas = self._canvas()
        return {
            "extent": self._rect_to_dict(canvas.extent()),
            "scale": canvas.scale(),
            "rotation": canvas.rotation(),
            "crs": canvas.mapSettings().destinationCrs().authid(),
            "size": {"width": canvas.width(), "height": canvas.height()},
            "layer_count": len(canvas.layers()),
        }

    def set_canvas_extent(self, xmin, ymin, xmax, ymax):
        canvas = self._canvas()
        canvas.setExtent(
            QgsRectangle(float(xmin), float(ymin), float(xmax), float(ymax)))
        canvas.refresh()
        return {"extent": self._rect_to_dict(canvas.extent())}

    def _render_image(self, width, height, layer_ids=None, extent=None, dpi=96):
        project = QgsProject.instance()
        settings = QgsMapSettings()

        if layer_ids:
            layers = [self._layer_or_raise(lid) for lid in layer_ids]
        elif self.iface is not None and self.iface.mapCanvas().layers():
            # Respect canvas order and visibility rather than dumping every
            # layer in the project, which is what the user actually sees.
            layers = list(self.iface.mapCanvas().layers())
        else:
            layers = [
                layer for lid, layer in project.mapLayers().items()
                if self._is_visible(lid)
            ] or list(project.mapLayers().values())
        settings.setLayers(layers)

        if extent:
            rect = QgsRectangle(
                float(extent["xmin"]), float(extent["ymin"]),
                float(extent["xmax"]), float(extent["ymax"]))
        elif self.iface is not None:
            rect = self.iface.mapCanvas().extent()
        else:
            # Global tiled basemaps would make this the whole world; frame
            # the data layers instead and let the basemap fill behind them.
            rect = QgsRectangle()
            transform_context = project.transformContext()
            for layer in layers:
                if layer.providerType() in ("wms", "xyz", "wmts"):
                    continue
                extent = layer.extent()
                if extent.isNull() or extent.isEmpty():
                    continue
                if layer.crs().isValid() and project.crs().isValid() \
                        and layer.crs() != project.crs():
                    from qgis.core import QgsCoordinateTransform
                    extent = QgsCoordinateTransform(
                        layer.crs(), project.crs(), transform_context
                    ).transformBoundingBox(extent)
                rect.combineExtentWith(extent)
            if rect.isNull():
                for layer in layers:
                    rect.combineExtentWith(layer.extent())
        settings.setExtent(rect)

        settings.setOutputSize(QSize(int(width), int(height)))
        settings.setBackgroundColor(QColor(255, 255, 255))
        settings.setOutputDpi(dpi)
        settings.setDestinationCrs(project.crs())

        job = QgsMapRendererParallelJob(settings)
        job.start()
        job.waitForFinished()
        return job.renderedImage(), rect, layers

    def render_map(self, path, width=800, height=600, layer_ids=None,
                   extent=None, dpi=96):
        image, rect, layers = self._render_image(width, height, layer_ids,
                                                 extent, dpi)
        directory = os.path.dirname(os.path.abspath(path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        if not image.save(path):
            raise IOError(f"Failed to save rendered image to {path}")
        return {
            "rendered": True, "path": path, "width": width, "height": height,
            "layer_count": len(layers), "extent": self._rect_to_dict(rect),
        }

    def render_map_base64(self, width=800, height=600, layer_ids=None,
                          extent=None, dpi=96, max_bytes=4_000_000):
        """Render straight to base64 PNG so a client can display the map."""
        image, rect, layers = self._render_image(width, height, layer_ids,
                                                 extent, dpi)
        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise IOError("Failed to encode the rendered image as PNG")
        raw = bytes(buffer.data())
        buffer.close()
        if len(raw) > max_bytes:
            raise ValueError(
                f"Rendered image is {len(raw)} bytes, over the {max_bytes} limit. "
                "Request a smaller width/height.")
        return {
            "format": "png",
            "width": width,
            "height": height,
            "bytes": len(raw),
            "layer_count": len(layers),
            "extent": self._rect_to_dict(rect),
            "base64": base64.b64encode(raw).decode("ascii"),
        }

    def list_layouts(self):
        manager = QgsProject.instance().layoutManager()
        return {
            "layouts": [
                {"name": layout.name(), "type": type(layout).__name__}
                for layout in manager.layouts()
            ]
        }

    def export_layout(self, layout_name, path, dpi=300):
        layout = QgsProject.instance().layoutManager().layoutByName(layout_name)
        if layout is None:
            raise LookupError(f"Layout not found: {layout_name}")
        exporter = QgsLayoutExporter(layout)
        if os.path.splitext(path)[1].lower() == ".pdf":
            settings = QgsLayoutExporter.PdfExportSettings()
            settings.dpi = dpi
            result = exporter.exportToPdf(path, settings)
        else:
            settings = QgsLayoutExporter.ImageExportSettings()
            settings.dpi = dpi
            result = exporter.exportToImage(path, settings)
        if result != _EXPORT_SUCCESS:
            raise IOError(f"Layout export failed for {layout_name} (code {result})")
        return {"exported": path, "layout": layout_name, "dpi": dpi}
