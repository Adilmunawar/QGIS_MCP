# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""OpenStreetMap features through the Overpass API.

Builds a query from tag filters, so the agent never has to write Overpass
QL, and turns the answer into GeoJSON-like dicts ready for a memory layer.
"""

import re

from .. import net

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
MAX_ELEMENTS = 20_000
DEFAULT_TIMEOUT_S = 20
_KEY = re.compile(r"^[A-Za-z0-9_:\-]+$")


def _tag_filter(tags):
    parts = []
    for key, value in (tags or {}).items():
        if not _KEY.match(key):
            raise ValueError(f"Invalid OSM tag key: {key}")
        if value in (None, "", "*", True):
            parts.append(f'["{key}"]')
        elif isinstance(value, (list, tuple)):
            joined = "|".join(re.escape(str(v)) for v in value)
            parts.append(f'["{key}"~"^({joined})$"]')
        else:
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            parts.append(f'["{key}"="{escaped}"]')
    if not parts:
        raise ValueError("At least one tag is required, e.g. {'amenity': 'hospital'}")
    return "".join(parts)


def build_query(bbox, tags, element_types=("node", "way", "relation"),
                timeout=DEFAULT_TIMEOUT_S, limit=MAX_ELEMENTS, geometry="center"):
    west, south, east, north = bbox
    box = f"({south:.6f},{west:.6f},{north:.6f},{east:.6f})"
    selector = _tag_filter(tags)
    body = "".join(f"{t}{selector}{box};" for t in element_types)
    out = "out geom" if geometry == "full" else "out center tags"
    return (f"[out:json][timeout:{int(timeout)}][maxsize:536870912];"
            f"({body});{out} {int(limit)};")


def fetch(bbox, tags, geometry="center", limit=MAX_ELEMENTS,
          timeout=DEFAULT_TIMEOUT_S):
    """Run the query, failing over across public mirrors.

    Returns ``(query, features, info)`` where ``info`` notes the endpoint
    used, whether the result hit ``limit``, and any server remark.
    """
    if not isinstance(tags, dict):
        raise ValueError("tags must be a mapping, e.g. {'amenity': 'hospital'}")
    query = build_query(bbox, tags, geometry=geometry, limit=limit,
                        timeout=timeout)
    errors = []
    data = None
    for endpoint in ENDPOINTS:
        try:
            data = net.post_form(endpoint, {"data": query},
                                 timeout_ms=(timeout + 5) * 1000)
        except net.HttpError as exc:
            if exc.status == 400:
                raise ValueError(f"Overpass rejected the query: {exc.body[:300]}")
            errors.append(f"{endpoint.split('/')[2]}: {str(exc)[:120]}")
            continue
        remark = str(data.get("remark") or "")
        if "error" in remark.lower():
            errors.append(f"{endpoint.split('/')[2]}: {remark[:160]}")
            data = None
            continue
        break
    if data is None:
        raise RuntimeError(
            "Every Overpass mirror failed. They are shared public servers; "
            "wait a minute and retry, or narrow the area. Details: "
            + "; ".join(errors))
    elements = data.get("elements", [])
    info = {
        "endpoint": endpoint,
        "truncated": len(elements) >= limit,
        "remark": data.get("remark"),
    }
    return query, [to_feature(e, geometry) for e in elements], info


def _way_coords(element):
    return [[n["lon"], n["lat"]] for n in element.get("geometry", [])]


def to_feature(element, geometry="center"):
    tags = element.get("tags", {}) or {}
    etype = element.get("type")
    geom = None
    if etype == "node":
        geom = {"type": "Point", "coordinates": [element["lon"], element["lat"]]}
    elif geometry == "full" and etype == "way" and element.get("geometry"):
        coords = _way_coords(element)
        closed = len(coords) > 3 and coords[0] == coords[-1]
        is_area = closed and (tags.get("area") != "no") and any(
            k in tags for k in ("building", "landuse", "natural", "amenity",
                                "leisure", "water", "boundary"))
        geom = {"type": "Polygon", "coordinates": [coords]} if is_area else \
            {"type": "LineString", "coordinates": coords}
    elif geometry == "full" and etype == "relation" and element.get("members"):
        rings = [_way_coords(m) for m in element["members"]
                 if m.get("type") == "way" and m.get("geometry")]
        rings = [r for r in rings if len(r) > 3 and r[0] == r[-1]]
        if rings:
            geom = {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}
    if geom is None and element.get("center"):
        c = element["center"]
        geom = {"type": "Point", "coordinates": [c["lon"], c["lat"]]}
    return {
        "type": "Feature",
        "id": f"{etype}/{element.get('id')}",
        "geometry": geom,
        "properties": dict(tags, osm_id=element.get("id"), osm_type=etype),
    }
