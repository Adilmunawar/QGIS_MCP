# -*- coding: utf-8 -*-
"""Administrative boundaries from geoBoundaries (public domain / CC BY)."""

import re
import urllib.parse

from .. import net

ALLOWED_HOSTS = ("github.com", "raw.githubusercontent.com", "media.githubusercontent.com",
                 "www.geoboundaries.org", "geoboundaries.org", "objects.githubusercontent.com")

API = "https://www.geoboundaries.org/api/current/gbOpen/{iso3}/{level}/"
LEVELS = ("ADM0", "ADM1", "ADM2", "ADM3", "ADM4", "ADM5")
_ISO = re.compile(r"^[A-Za-z]{3}$")


def metadata(iso3, level="ADM1"):
    iso3 = str(iso3).upper()
    if not _ISO.match(iso3):
        raise ValueError("iso3 must be a three letter country code, e.g. PAK")
    level = str(level).upper()
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    try:
        data = net.get_json(API.format(iso3=iso3, level=level))
    except net.HttpError as exc:
        if exc.status == 404:
            raise LookupError(
                f"geoBoundaries has no data for '{iso3}' at {level}. Check that the "
                "country code is ISO 3166 alpha 3 (PAK, IND, KEN), or try ADM1.")
        raise
    return {
        "iso3": iso3, "level": level,
        "name": data.get("boundaryName"),
        "canonical": data.get("boundaryCanonical"),
        "year": data.get("boundaryYearRepresented"),
        "licence": data.get("boundaryLicense"),
        "source": data.get("boundarySource"),
        "geojson_url": data.get("gjDownloadURL"),
        "simplified_url": data.get("simplifiedGeometryGeoJSON"),
        "count": data.get("admUnitCount"),
    }


def fetch_geojson(url):
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise ValueError(f"Refusing boundary download from {parsed.hostname or url}")
    data = net.get_json(url, timeout_ms=180_000)
    features = data.get("features", [])
    for f in features:
        props = f.get("properties") or {}
        f["properties"] = {
            "name": props.get("shapeName"), "iso": props.get("shapeISO"),
            "shape_id": props.get("shapeID"), "group": props.get("shapeGroup"),
            "level": props.get("shapeType"),
        }
    return features


def feature_bbox(feature):
    coords = []

    def walk(c):
        if isinstance(c[0], (int, float)):
            coords.append(c)
        else:
            for sub in c:
                walk(sub)

    walk(feature["geometry"]["coordinates"])
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return min(xs), min(ys), max(xs), max(ys)


def within(features, bbox):
    """Features whose geometry really intersects the bbox (not just its box)."""
    from qgis.core import QgsGeometry, QgsRectangle
    west, south, east, north = bbox
    box = QgsGeometry.fromRect(QgsRectangle(west, south, east, north))
    out = []
    for f in features:
        try:
            w, s, e, n = feature_bbox(f)
        except (KeyError, IndexError, TypeError):
            continue
        if not (e >= west and w <= east and n >= south and s <= north):
            continue
        from ..live_commands import _geojson_to_wkt
        try:
            geom = QgsGeometry.fromWkt(_geojson_to_wkt(f["geometry"]))
        except (ValueError, KeyError):
            geom = None
        if geom is None or geom.isNull() or geom.intersects(box):
            out.append(f)
    return out
