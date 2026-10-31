# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""Live monitor: refresh hazard, sensor and weather feeds for an area on a
schedule and raise alerts when thresholds are crossed.

The module is split like the rest of the engine: :func:`fetch` touches only
connectors and may run on a worker thread; :func:`evaluate` is pure Python;
layer updates and message bar notices happen in the command mixin on the
GUI thread.
"""

import datetime as _dt

from .connectors import disasters, events, hazards, sensors, weather

SOURCES = ("earthquakes", "air_sensors", "weather", "disaster_alerts", "natural_events")

#: Thresholds a user is likely to want; every rule can be overridden.
DEFAULT_RULES = {
    "earthquakes": {"min_magnitude": 4.5, "period": "day"},
    "air_sensors": {"pm2_5": 50.0, "pm10": 100.0, "radius_km": 25},
    "weather": {"temperature_2m": 40.0, "wind_speed_10m": 60.0, "precipitation": 10.0},
    "disaster_alerts": {"levels": ["orange", "red"]},
    "natural_events": {"days": 7},
}

MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 24 * 60
MAX_HISTORY = 200


def make_config(bbox, center, sources=None, interval_minutes=10, rules=None, label=None):
    """Validate and normalise a monitor configuration."""
    chosen = list(sources or SOURCES)
    unknown = [s for s in chosen if s not in SOURCES]
    if unknown:
        raise ValueError(f"Unknown monitor sources {unknown}; choose from {list(SOURCES)}")
    interval = max(MIN_INTERVAL_MINUTES, min(int(interval_minutes), MAX_INTERVAL_MINUTES))
    merged = {k: dict(v) for k, v in DEFAULT_RULES.items()}
    for key, value in (rules or {}).items():
        if key not in merged:
            raise ValueError(f"Unknown rule group '{key}'; choose from {list(merged)}")
        if not isinstance(value, dict):
            raise ValueError(f"rules['{key}'] must be a mapping of threshold names to values")
        for name, threshold in value.items():
            merged[key][name] = _coerce_rule(key, name, threshold)
    return {"bbox": [float(v) for v in bbox], "center": [float(center[0]), float(center[1])],
            "sources": chosen, "interval_minutes": interval, "rules": merged,
            "label": label, "created": _dt.datetime.now().isoformat(timespec="seconds")}


def _coerce_rule(group, name, value):
    """Thresholds are numbers, ``levels`` a list of names, ``period`` a word."""
    if name == "levels":
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
            raise ValueError("rules['disaster_alerts']['levels'] must be a list such as ['orange', 'red']")
        return [v.lower() for v in value]
    if name == "period":
        if value not in ("hour", "day", "week", "month"):
            raise ValueError("rules['earthquakes']['period'] must be hour, day, week or month")
        return value
    if isinstance(value, bool):
        raise ValueError(f"rules['{group}']['{name}'] must be a number")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"rules['{group}']['{name}'] must be a number, got {value!r}")


def fetch(config):
    """Pull every configured source once. Failures are reported per source."""
    bbox = config["bbox"]
    lon, lat = config["center"]
    rules = config["rules"]
    out = {"fetched_at": _dt.datetime.now().isoformat(timespec="seconds"), "sources": {},
           "errors": {}}
    for source in config["sources"]:
        try:
            if source == "earthquakes":
                rule = rules["earthquakes"]
                result = hazards.earthquakes(rule.get("period", "day"), None, bbox)
                out["sources"][source] = result["features"]
            elif source == "air_sensors":
                result = sensors.air_sensors(lon, lat, rules["air_sensors"].get("radius_km", 25))
                out["sources"][source] = result["features"]
            elif source == "weather":
                row = weather.current([(lon, lat)])[0]
                current = dict(row.get("current") or {})
                out["sources"][source] = [{
                    "type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {k: v for k, v in current.items() if not isinstance(v, (dict, list))},
                }]
            elif source == "disaster_alerts":
                result = disasters.alerts(bbox, None, None)
                out["sources"][source] = result["features"]
            elif source == "natural_events":
                result = events.events(bbox, None, "open", rules["natural_events"].get("days", 7))
                out["sources"][source] = result["features"]
        except Exception as exc:  # one failing feed must not stop the others
            out["errors"][source] = str(exc)[:200]
    return out


def _alert(source, severity, message, feature, key, value=None):
    coords = ((feature or {}).get("geometry") or {}).get("coordinates") or [None, None]
    return {"source": source, "severity": severity, "message": message, "key": key,
            "value": value, "lon": coords[0], "lat": coords[1],
            "time": _dt.datetime.now().isoformat(timespec="seconds")}


def evaluate(config, fetched, seen):
    """Turn fetched features into alerts, skipping keys already in ``seen``.

    ``seen`` is a set the caller keeps between runs so an earthquake or
    disaster alert is announced once, while weather and sensor thresholds
    re-fire whenever a reading is above the limit.
    """
    rules = config["rules"]
    alerts = []
    for feature in fetched["sources"].get("earthquakes", []):
        p = feature["properties"]
        mag = p.get("magnitude") or p.get("mag") or 0
        key = f"eq:{feature.get('id') or p.get('id') or p.get('time_ms') or p.get('time')}"
        if mag >= rules["earthquakes"]["min_magnitude"] and key not in seen:
            seen.add(key)
            alerts.append(_alert("earthquakes", "high" if mag >= 6 else "medium",
                                 f"M{mag:.1f} earthquake {p.get('place') or ''}".strip(),
                                 feature, key, mag))
    for feature in fetched["sources"].get("air_sensors", []):
        p = feature["properties"]
        if p.get("indoor"):
            continue
        for name in ("pm2_5", "pm10"):
            limit = rules["air_sensors"].get(name)
            value = p.get(name)
            if limit is not None and value is not None and value > limit:
                key = f"air:{p.get('location_id')}:{name}"
                alerts.append(_alert("air_sensors", "medium",
                                     f"{name.replace('_', '.').upper()} {value:.0f} µg/m³ at sensor "
                                     f"{p.get('location_id')} (limit {limit:g})", feature, key, value))
    for feature in fetched["sources"].get("weather", []):
        p = feature["properties"]
        for name, limit in rules["weather"].items():
            value = p.get(name)
            if isinstance(value, (int, float)) and limit is not None and value > limit:
                alerts.append(_alert("weather", "medium",
                                     f"{name.replace('_', ' ')} is {value:g} (limit {limit:g})",
                                     feature, f"wx:{name}", value))
    wanted = {l.lower() for l in rules["disaster_alerts"].get("levels", [])}
    for feature in fetched["sources"].get("disaster_alerts", []):
        p = feature["properties"]
        key = f"gdacs:{p.get('event_id') or p.get('title')}"
        if (p.get("alert_level") or "") in wanted and key not in seen:
            seen.add(key)
            alerts.append(_alert("disaster_alerts", "high" if p.get("alert_level") == "red" else "medium",
                                 f"GDACS {p.get('alert_level')} {p.get('event_type')}: {p.get('title')}",
                                 feature, key, p.get("alert_level")))
    for feature in fetched["sources"].get("natural_events", []):
        p = feature["properties"]
        key = f"eonet:{p.get('id')}"
        if key not in seen:
            seen.add(key)
            alerts.append(_alert("natural_events", "low",
                                 f"{p.get('category')}: {p.get('title')}", feature, key,
                                 p.get("magnitude_value")))
    return alerts
