# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""Live hazard feeds: USGS earthquakes and NASA FIRMS active fires."""

import csv
import io

from .. import net

USGS_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{level}_{period}.geojson"
USGS_LEVELS = {"all": "all", "1.0": "1.0", "2.5": "2.5", "4.5": "4.5",
               "significant": "significant"}
USGS_PERIODS = ("hour", "day", "week", "month")

FIRMS_AREA = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{area}/{days}"
FIRMS_SOURCES = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT",
                 "MODIS_NRT")


def _level_for(min_magnitude):
    if min_magnitude is None:
        return "all"
    for threshold, level in ((4.5, "4.5"), (2.5, "2.5"), (1.0, "1.0")):
        if float(min_magnitude) >= threshold:
            return level
    return "all"


def earthquakes(period="day", min_magnitude=None, bbox=None):
    if period not in USGS_PERIODS:
        raise ValueError(f"period must be one of {USGS_PERIODS}")
    url = USGS_FEED.format(level=_level_for(min_magnitude), period=period)
    data = net.get_json(url)
    features = []
    for f in data.get("features", []):
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        props = f.get("properties", {})
        if min_magnitude is not None and (props.get("mag") or 0) < float(min_magnitude):
            continue
        if bbox and not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            continue
        features.append({
            "type": "Feature", "id": f.get("id"),
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "magnitude": props.get("mag"),
                "place": props.get("place"),
                "time_ms": props.get("time"),
                "depth_km": coords[2] if len(coords) > 2 else None,
                "tsunami": props.get("tsunami"),
                "alert": props.get("alert"),
                "felt": props.get("felt"),
                "url": props.get("url"),
            },
        })
    return {"source": url, "generated": (data.get("metadata") or {}).get("generated"),
            "features": features}


def active_fires(key, bbox, days=1, source="VIIRS_SNPP_NRT"):
    if not key:
        raise PermissionError(
            "NASA FIRMS needs a free MAP_KEY. Request one at "
            "https://firms.modaps.eosdis.nasa.gov/api/map_key/ and paste it in "
            "the QGIS MCP panel's Data Sources tab.")
    if source not in FIRMS_SOURCES:
        raise ValueError(f"source must be one of {FIRMS_SOURCES}")
    days = max(1, min(int(days), 10))
    area = ",".join(f"{float(v):.4f}" for v in bbox)
    url = FIRMS_AREA.format(key=key, source=source, area=area, days=days)
    _, content = net.fetch(url, timeout_ms=120_000)
    text = content.decode("utf-8", "replace")
    if text.startswith("Invalid") or "Error" in text[:80]:
        raise PermissionError(f"FIRMS rejected the request: {text[:200]}")
    features = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            lon, lat = float(row["longitude"]), float(row["latitude"])
        except (KeyError, ValueError):
            continue
        props = {k: _num(v) for k, v in row.items() if k not in ("latitude", "longitude")}
        features.append({"type": "Feature", "geometry":
                         {"type": "Point", "coordinates": [lon, lat]},
                         "properties": props})
    return {"source": source, "days": days, "features": features}


def _num(value):
    try:
        return float(value) if "." in value else int(value)
    except (ValueError, TypeError):
        return value
