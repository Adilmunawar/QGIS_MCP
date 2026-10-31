# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""Area-of-interest resolution.

Every live-data command takes an ``aoi`` and none of them should care how it
was spelled. A caller may pass:

* ``{"bbox": [west, south, east, north]}``  - degrees, WGS84
* ``{"wkt": "POLYGON((...))", "crs": "EPSG:4326"}``
* ``{"layer_id": "...", "selected": true}``  - a layer's (selected) extent
* ``{"canvas": true}``  - what the user is looking at
* ``{"place": "Multan"}``  - geocoded, see :mod:`connectors.geocode`
* ``{"point": [lon, lat], "radius_km": 10}``
* a plain ``[west, south, east, north]`` list, or a bare WKT / place string

The result is always a :class:`Aoi` whose rectangle is in EPSG:4326.
"""

import math

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
    QgsMapLayer, QgsProject, QgsRectangle,
)

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
MAX_AOI_DEGREES2 = 25.0  # ~5x5 degrees; larger pulls are a mistake, not a request
MIN_SIDE_DEGREES = 0.002  # ~200 m; smaller boxes are padded to 1 km


class Aoi:
    __slots__ = ("rect", "geometry", "source", "label")

    def __init__(self, rect, geometry=None, source="bbox", label=None):
        self.rect = rect
        self.geometry = geometry or QgsGeometry.fromRect(rect)
        self.source = source
        self.label = label

    @property
    def bbox(self):
        r = self.rect
        return [r.xMinimum(), r.yMinimum(), r.xMaximum(), r.yMaximum()]

    @property
    def center(self):
        return [self.rect.center().x(), self.rect.center().y()]

    def area_degrees2(self):
        return self.rect.width() * self.rect.height()

    def to_dict(self):
        return {
            "bbox": [round(v, 6) for v in self.bbox],
            "center": [round(v, 6) for v in self.center],
            "source": self.source,
            "label": self.label,
            "crs": "EPSG:4326",
        }

    def geojson(self):
        import json
        return json.loads(self.geometry.asJson())


def _to_wgs84(rect_or_geom, crs):
    if crs is None or crs == WGS84:
        return rect_or_geom
    if not crs.isValid():
        raise ValueError("Invalid CRS for the area of interest")
    transform = QgsCoordinateTransform(crs, WGS84, QgsProject.instance())
    if isinstance(rect_or_geom, QgsRectangle):
        return transform.transformBoundingBox(rect_or_geom)
    geom = QgsGeometry(rect_or_geom)
    geom.transform(transform)
    return geom


def _validate(rect):
    if rect.isNull():
        raise ValueError("Area of interest is empty")
    if rect.width() < MIN_SIDE_DEGREES or rect.height() < MIN_SIDE_DEGREES:
        # A single point, a straight line, or a geocoded node has no useful
        # area; give it a 1 km box rather than refusing or reading 3 pixels.
        centre = rect.center()
        lat_deg = 1.0 / 111.32
        lon_deg = 1.0 / (111.32 * max(math.cos(math.radians(centre.y())), 0.01))
        rect = QgsRectangle(centre.x() - lon_deg, centre.y() - lat_deg,
                            centre.x() + lon_deg, centre.y() + lat_deg)
    if not (-180 <= rect.xMinimum() <= 180 and -180 <= rect.xMaximum() <= 180
            and -90 <= rect.yMinimum() <= 90 and -90 <= rect.yMaximum() <= 90):
        raise ValueError(
            f"Area of interest is outside WGS84 bounds: {rect.toString(4)}. "
            "Is the layer or project CRS declared correctly?")
    if rect.width() * rect.height() > MAX_AOI_DEGREES2:
        raise ValueError(
            f"Area of interest is {rect.width():.1f} x {rect.height():.1f} "
            f"degrees, above the {math.sqrt(MAX_AOI_DEGREES2):.0f}-degree "
            "limit. Narrow it to the region you actually need, for example "
            '{"place": "<town>", "radius_km": 10} or an explicit bbox')
    return rect


def from_bbox(values, source="bbox", label=None):
    if len(values) != 4:
        raise ValueError("bbox must be [west, south, east, north]")
    w, s, e, n = (float(v) for v in values)
    if w > e or s > n:
        raise ValueError("bbox must be [west, south, east, north] with west<east "
                         "and south<north")
    return Aoi(_validate(QgsRectangle(w, s, e, n)), source=source, label=label)


def from_point(lon, lat, radius_km=5.0, label=None):
    lat_deg = radius_km / 111.32
    lon_deg = radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.01))
    rect = QgsRectangle(lon - lon_deg, lat - lat_deg, lon + lon_deg, lat + lat_deg)
    return Aoi(_validate(rect), source="point", label=label)


def from_wkt(wkt, crs="EPSG:4326"):
    geometry = QgsGeometry.fromWkt(wkt)
    if geometry.isNull():
        raise ValueError(f"Could not parse WKT: {wkt[:80]}")
    source_crs = QgsCoordinateReferenceSystem(crs) if crs else WGS84
    geometry = _to_wgs84(geometry, source_crs)
    rect = _validate(geometry.boundingBox())
    if rect != geometry.boundingBox():
        geometry = QgsGeometry.fromRect(rect)
    return Aoi(rect, geometry, source="wkt")


def from_layer(layer_id, selected=False):
    layer = QgsProject.instance().mapLayer(layer_id)
    if layer is None:
        raise LookupError(f"Layer not found: {layer_id}")
    if selected:
        if layer.type() != QgsMapLayer.LayerType.VectorLayer:
            raise ValueError("selected=true needs a vector layer")
        if layer.selectedFeatureCount() == 0:
            raise ValueError(f"No features selected on layer {layer_id}")
        rect = layer.boundingBoxOfSelected()
        source = "selection"
    else:
        rect = layer.extent()
        source = "layer"
    if rect.isNull():
        raise ValueError(f"Layer {layer_id} has no extent")
    rect = _to_wgs84(rect, layer.crs())
    return Aoi(_validate(rect), source=source, label=layer.name())


def from_canvas(iface):
    if iface is None:
        raise RuntimeError("No map canvas available (QGIS running headless)")
    canvas = iface.mapCanvas()
    rect = _to_wgs84(canvas.extent(), canvas.mapSettings().destinationCrs())
    return Aoi(_validate(rect), source="canvas")


def resolve(spec, iface=None, geocoder=None):
    """Turn any accepted spelling of an AOI into an :class:`Aoi`.

    ``geocoder`` is a callable ``(place) -> (bbox, label)`` so this module does
    not depend on the network layer; the live-data mixin supplies it.
    """
    if spec is None:
        raise ValueError("An area of interest (aoi) is required")
    if isinstance(spec, Aoi):
        return spec
    if isinstance(spec, (list, tuple)):
        return from_bbox(spec)
    if isinstance(spec, str):
        text = spec.strip()
        upper = text.upper()
        if upper.startswith(("POLYGON", "MULTIPOLYGON", "POINT", "LINESTRING")):
            return from_wkt(text)
        if geocoder is None:
            raise ValueError("Place names need a geocoder; pass a bbox instead")
        bbox, label = geocoder(text)
        return from_bbox(bbox, source="place", label=label)
    if not isinstance(spec, dict):
        raise ValueError(f"Unsupported aoi type: {type(spec).__name__}")

    if "bbox" in spec:
        crs = spec.get("crs")
        if crs and str(crs).upper() not in ("EPSG:4326", "WGS84", "CRS:84"):
            w, s, e, n = (float(v) for v in spec["bbox"])
            return from_wkt(f"POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))",
                            crs)
        return from_bbox(spec["bbox"])
    if "wkt" in spec:
        return from_wkt(spec["wkt"], spec.get("crs", "EPSG:4326"))
    if "layer_id" in spec:
        return from_layer(spec["layer_id"], bool(spec.get("selected", False)))
    if spec.get("canvas"):
        return from_canvas(iface)
    if "point" in spec:
        lon, lat = spec["point"]
        return from_point(float(lon), float(lat),
                          float(spec.get("radius_km", 5.0)))
    if "place" in spec:
        if geocoder is None:
            raise ValueError("Place names need a geocoder; pass a bbox instead")
        bbox, label = geocoder(str(spec["place"]))
        aoi = from_bbox(bbox, source="place", label=label)
        if "radius_km" in spec:
            cx, cy = aoi.center
            aoi = from_point(cx, cy, float(spec["radius_km"]), label=label)
        return aoi
    raise ValueError(
        "aoi must contain one of: bbox, wkt, layer_id, canvas, point, place")
