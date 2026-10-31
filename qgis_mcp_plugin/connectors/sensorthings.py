# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: GPL-3.0-or-later
"""OGC SensorThings API servers: live sensor networks published by cities,
environment agencies and research projects.

QGIS ships a native ``sensorthings`` data provider, so a server becomes a
layer that keeps paging and filtering on the server side. This module keeps
the list of verified public servers and a small discovery helper; the layer
itself is built in the command mixin.
"""

from .. import net

ENTITIES = ("Location", "Thing", "Datastream", "FeatureOfInterest",
            "ObservedProperty", "Sensor", "Observation", "HistoricalLocation")
#: Entities that carry a geometry and so can be shown on the map.
GEOMETRY_ENTITIES = ("Location", "Datastream", "FeatureOfInterest")

#: Public servers checked on 2026-10-31. Keys are what a user types.
PRESETS = {
    "hamburg": {
        "name": "Hamburg Urban Data Platform",
        "url": "https://iot.hamburg.de/v1.1",
        "description": "Traffic counters, bike share stations, parking, weather and environment sensors across Hamburg.",
        "licence": "Datenlizenz Deutschland (attribution)",
        "region": "Hamburg, Germany",
    },
    "samenmeten": {
        "name": "RIVM Samen Meten (citizen air quality, Netherlands)",
        "url": "https://api-samenmeten.rivm.nl/v1.0",
        "description": "Thousands of citizen air quality sensors (PM2.5, PM10, NO2) across the Netherlands.",
        "licence": "CC0",
        "region": "Netherlands",
        "snapshot": True,  # GOST server: no location/type filter, so the native provider shows nothing
    },
}

MAX_SNAPSHOT = 20_000


def extent_filter_for(bbox):
    """OData spatial filter for a WGS84 bbox, with the SRID GOST servers need."""
    w, s, e, n = [float(v) for v in bbox]
    ring = f"{w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}"
    return f"geo.intersects(location, geography'SRID=4326;POLYGON (({ring}))')"


def fetch_locations(url, bbox=None, limit=1000, page_size=500):
    """Locations as GeoJSON like point features through plain REST paging.

    A snapshot rather than a live layer; used for servers whose OData dialect
    the QGIS provider cannot drive, and as a fallback when the provider
    returns nothing.
    """
    url = check_url(url)
    limit = max(1, min(int(limit), MAX_SNAPSHOT))
    params = {"$top": min(int(page_size), limit), "$count": "true"}
    if bbox:
        params["$filter"] = extent_filter_for(bbox)
    next_url = url + "/Locations"
    features = []
    total = None
    while next_url and len(features) < limit:
        data = net.get_json(next_url, params, timeout_ms=90_000)
        params = None  # nextLink carries its own query string
        if total is None:
            total = data.get("@iot.count")
        for entry in data.get("value") or []:
            loc = entry.get("location") or {}
            geom = loc.get("geometry") if loc.get("type") == "Feature" else loc
            if not isinstance(geom, dict) or geom.get("type") != "Point":
                continue
            coords = geom.get("coordinates") or []
            if len(coords) < 2:
                continue
            extra = entry.get("properties") or {}
            props = {"id": str(entry.get("@iot.id")), "name": entry.get("name"),
                     "description": entry.get("description"),
                     "self_link": entry.get("@iot.selfLink")}
            for key, value in extra.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    props[f"prop_{key}"[:60]] = value
            features.append({"type": "Feature",
                             "geometry": {"type": "Point", "coordinates": [float(coords[0]), float(coords[1])]},
                             "properties": props})
        next_url = data.get("@iot.nextLink")
    return {"features": features[:limit], "matched": total, "truncated": bool(next_url) or len(features) > limit,
            "source": f"OGC SensorThings server {url}"}


def preset(key):
    spec = PRESETS.get(str(key).lower())
    if spec is None:
        raise ValueError(f"Unknown SensorThings preset '{key}'. Use one of {sorted(PRESETS)} "
                         "or pass url=")
    return spec


def check_url(url):
    url = str(url or "").strip().rstrip("/")
    if not url.lower().startswith("https://"):
        raise ValueError("SensorThings url must start with https:// and end with the API "
                         "version, for example https://iot.hamburg.de/v1.1")
    return url


def discover(url):
    """Entity sets a server offers, with a count of locations when cheap."""
    url = check_url(url)
    try:
        root = net.get_json(url + "/", timeout_ms=60_000)
    except net.HttpError:
        root = net.get_json(url, timeout_ms=60_000)  # some servers refuse the trailing slash
    sets = []
    for entry in (root.get("value") or []):
        name = entry.get("name")
        if name:
            sets.append(name)
    out = {"url": url, "entity_sets": sets, "server_settings": root.get("serverSettings")}
    try:
        counted = net.get_json(url + "/Locations", {"$top": 1, "$count": "true"}, timeout_ms=60_000)
        out["locations"] = counted.get("@iot.count")
        sample = (counted.get("value") or [{}])[0]
        out["sample_location"] = {k: sample.get(k) for k in ("name", "description")}
    except Exception as exc:  # a server may refuse $count; the sets are still useful
        out["locations_error"] = str(exc)[:160]
    return out


GEOMETRY_TYPES = {"point": "Point", "multipoint": "MultiPoint", "line": "LineString",
                  "multiline": "MultiLineString", "polygon": "Polygon", "multipolygon": "MultiPolygon"}


def layer_uri(url, entity="Location", feature_limit=1000, page_size=200, extent_filter=None,
              expand_to=None, geometry_type="point"):
    """Build the QGIS ``sensorthings`` provider URI.

    A SensorThings location can be any GeoJSON geometry, so the provider
    needs the type to expose; without it the layer has no geometry at all.
    """
    url = check_url(url)
    if entity not in ENTITIES:
        raise ValueError(f"entity must be one of {ENTITIES}")
    if entity not in GEOMETRY_ENTITIES:
        raise ValueError(f"{entity} has no geometry; use Location, Datastream or FeatureOfInterest "
                         "to put it on the map")
    geometry_type = str(geometry_type or "point").lower()
    if geometry_type not in GEOMETRY_TYPES:
        raise ValueError(f"geometry_type must be one of {sorted(GEOMETRY_TYPES)}")
    # The provider reads the geometry as the unquoted ``type=`` token.
    parts = [f"type={GEOMETRY_TYPES[geometry_type]}", f"entity='{entity}'",
             f"featureLimit='{max(1, min(int(feature_limit), 100_000))}'",
             f"pageSize='{max(1, min(int(page_size), 1000))}'"]
    if expand_to:
        parts.append(f"expandTo='{expand_to}'")
    parts.append(f"url='{url}'")
    uri = " " + " ".join(parts)
    if extent_filter:
        # The provider reads its OData filter from the trailing sql token,
        # unquoted, so the geography literal's own quotes survive.
        uri += f" sql={extent_filter}"
    return uri
