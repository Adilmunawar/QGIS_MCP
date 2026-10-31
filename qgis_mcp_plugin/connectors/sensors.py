# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""Real time sensor networks: Sensor.Community air quality stations."""

import math

from .. import net

SENSOR_COMMUNITY = "https://data.sensor.community/airrohr/v1/filter/area={lat},{lon},{km}"
VALUE_LABELS = {
    "P1": "pm10", "P2": "pm2_5", "P0": "pm1", "temperature": "temperature",
    "humidity": "humidity", "pressure": "pressure", "noise_LAeq": "noise_laeq",
}


def air_sensors(lon, lat, radius_km=25):
    radius_km = max(1, min(int(radius_km), 100))
    url = SENSOR_COMMUNITY.format(lat=f"{float(lat):.4f}", lon=f"{float(lon):.4f}",
                                  km=radius_km)
    rows = net.get_json(url, timeout_ms=90_000)
    stations = {}
    for entry in rows:
        loc = entry.get("location") or {}
        try:
            slon, slat = float(loc["longitude"]), float(loc["latitude"])
        except (KeyError, TypeError, ValueError):
            continue
        key = loc.get("id")
        station = stations.setdefault(key, {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [slon, slat]},
            "properties": {"location_id": key, "country": loc.get("country"),
                           "indoor": bool(loc.get("indoor")),
                           "timestamp": entry.get("timestamp"), "sensors": ""},
        })
        sensor_name = ((entry.get("sensor") or {}).get("sensor_type") or {}).get("name")
        if sensor_name and sensor_name not in station["properties"]["sensors"]:
            station["properties"]["sensors"] = (
                station["properties"]["sensors"] + "," + sensor_name).strip(",")
        for value in entry.get("sensordatavalues", []):
            label = VALUE_LABELS.get(value.get("value_type"))
            if label is None:
                continue
            try:
                number = float(value.get("value"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                station["properties"][label] = number
    features = list(stations.values())
    pm25 = [f["properties"]["pm2_5"] for f in features
            if "pm2_5" in f["properties"] and not f["properties"]["indoor"]]
    summary = {
        "stations": len(features),
        "pm2_5_mean": round(sum(pm25) / len(pm25), 1) if pm25 else None,
        "pm2_5_max": max(pm25) if pm25 else None,
    }
    return {"features": features, "summary": summary,
            "source": "Sensor.Community (ODbL)"}
