# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""Nominatim geocoding, with a small cache and the fair-use pause.

Nominatim's policy is one request per second and a real User-Agent, which
:mod:`net` always sends. Results are cached for the session so repeated
mentions of the same place do not cost another request.
"""

import threading
import time

from .. import net

SEARCH = "https://nominatim.openstreetmap.org/search"
REVERSE = "https://nominatim.openstreetmap.org/reverse"

_cache = {}
_last_call = 0.0
_lock = threading.Lock()


def _throttle():
    global _last_call
    with _lock:
        wait = 1.0 - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _summarise(row):
    bbox = row.get("boundingbox")
    south, north, west, east = (float(v) for v in bbox) if bbox else (None,) * 4
    return {
        "name": row.get("name") or row.get("display_name"),
        "display_name": row.get("display_name"),
        "lat": float(row["lat"]), "lon": float(row["lon"]),
        "bbox": [west, south, east, north] if bbox else None,
        "type": f"{row.get('category')}/{row.get('type')}",
        "importance": row.get("importance"),
        "osm": f"{row.get('osm_type')}/{row.get('osm_id')}",
        "address": row.get("address"),
    }


def search(query, limit=5, country_codes=None, polygon=False):
    key = ("s", query.strip().lower(), limit, country_codes, polygon)
    if key in _cache:
        return _cache[key]
    _throttle()
    params = {"q": query, "format": "jsonv2", "limit": int(limit),
              "addressdetails": 1}
    if country_codes:
        params["countrycodes"] = country_codes
    if polygon:
        params["polygon_geojson"] = 1
    rows = net.get_json(SEARCH, params)
    results = []
    for row in rows:
        entry = _summarise(row)
        if polygon and row.get("geojson"):
            entry["geojson"] = row["geojson"]
        results.append(entry)
    _cache[key] = results
    return results


def reverse(lat, lon, zoom=14):
    key = ("r", round(float(lat), 5), round(float(lon), 5), zoom)
    if key in _cache:
        return _cache[key]
    _throttle()
    row = net.get_json(REVERSE, {"lat": f"{float(lat):.6f}",
                                  "lon": f"{float(lon):.6f}",
                                  "format": "jsonv2", "zoom": int(zoom),
                                  "addressdetails": 1})
    if "error" in row:
        raise LookupError(f"No place found at {lat}, {lon}: {row['error']}")
    result = _summarise(row)
    _cache[key] = result
    return result


def bbox_for(place):
    """Geocoder callback for :func:`aoi.resolve`: ``(bbox, label)``."""
    results = search(place, limit=1)
    if not results:
        raise LookupError(f"Place not found: {place}")
    best = results[0]
    if not best["bbox"]:
        raise LookupError(f"No extent for place: {place}")
    return best["bbox"], best["display_name"]
