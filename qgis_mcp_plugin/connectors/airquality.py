# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenAQ v3 reference grade air quality stations (needs a free API key)."""

import re
import time

from .. import net

OPENAQ = "https://api.openaq.org/v3"
REGISTER_URL = "https://explore.openaq.org/register"


def _get(path, params, key):
    try:
        return net.get_json(OPENAQ + path, params, headers={"X-API-Key": key},
                            timeout_ms=60_000)
    except net.HttpError as exc:
        if exc.status in (401, 403):
            raise ValueError("OpenAQ rejected the key; get a free key at "
                             "https://explore.openaq.org/register")
        raise


def _name(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _sub(obj, *path):
    for step in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(step)
    return obj


def _latest_values(location_id, sensors, key):
    """Map parameter name to latest value for one location."""
    data = _get(f"/locations/{location_id}/latest", {"limit": 100}, key)
    by_sensor = {}
    for s in sensors:
        if isinstance(s, dict) and s.get("id") is not None:
            by_sensor[s["id"]] = _name(_sub(s, "parameter", "name"))
    values = {}
    for item in data.get("results") or []:
        name = by_sensor.get(item.get("sensorsId"))
        value = item.get("value")
        if name and isinstance(value, (int, float)):
            values[name] = value
    return values


def stations(lon, lat, radius_km, key, limit=100, max_latest=20):
    if not key:
        raise ValueError("OpenAQ needs a free API key; get one at " + REGISTER_URL)
    radius_m = int(max(1, min(float(radius_km), 25)) * 1000)
    data = _get("/locations", {
        "coordinates": f"{float(lat):.5f},{float(lon):.5f}",
        "radius": radius_m, "limit": max(1, min(int(limit), 1000)),
    }, key)
    features = []
    with_latest = 0
    fetched = 0
    for loc in data.get("results") or []:
        coords = loc.get("coordinates") or {}
        try:
            slon, slat = float(coords["longitude"]), float(coords["latitude"])
        except (KeyError, TypeError, ValueError):
            continue
        sensors = loc.get("sensors") or []
        names = [_sub(s, "parameter", "name") for s in sensors]
        props = {
            "id": loc.get("id"), "name": loc.get("name"),
            "locality": loc.get("locality"),
            "country": _sub(loc, "country", "code"),
            "provider": _sub(loc, "provider", "name"),
            "is_monitor": bool(loc.get("isMonitor")),
            "last_seen": _sub(loc, "datetimeLast", "utc"),
            "parameters": ",".join(sorted({str(n) for n in names if n})),
        }
        if fetched < max_latest and loc.get("id") is not None:
            fetched += 1
            try:
                latest = _latest_values(loc["id"], sensors, key)
            except net.HttpError:
                latest = {}
            props.update(latest)
            with_latest += 1 if latest else 0
            time.sleep(0.2)
        features.append({"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [slon, slat]},
                         "properties": props})
    pm25 = [f["properties"]["pm25"] for f in features
            if isinstance(f["properties"].get("pm25"), (int, float))
            and f["properties"]["pm25"] >= 0]
    summary = {"stations": len(features), "with_latest": with_latest,
               "pm25_mean": round(sum(pm25) / len(pm25), 1) if pm25 else None,
               "pm25_max": max(pm25) if pm25 else None}
    return {"features": features, "summary": summary,
            "source": "OpenAQ (CC BY 4.0)"}
