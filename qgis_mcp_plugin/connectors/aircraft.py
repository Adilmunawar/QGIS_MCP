# -*- coding: utf-8 -*-
"""Live aircraft positions from The OpenSky Network (anonymous access)."""

import time

from .. import net

OPENSKY = "https://opensky-network.org/api/states/all"


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(epoch):
    number = _f(epoch)
    if number is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(number))


def _at(row, i):
    return row[i] if len(row) > i else None


def opensky_states(bbox, max_aircraft=500):
    try:
        west, south, east, north = [float(v) for v in bbox]
    except (TypeError, ValueError):
        raise ValueError("bbox must be [west, south, east, north] in degrees")
    if not (west < east and south < north):
        raise ValueError("bbox must satisfy west < east and south < north")
    if east - west > 20 or north - south > 20:
        raise ValueError("OpenSky boxes are limited to 20 by 20 degrees; "
                         "use a smaller bbox")
    params = {"lamin": south, "lomin": west, "lamax": north, "lomax": east}
    try:
        data = net.get_json(OPENSKY, params, timeout_ms=60_000)
    except net.HttpError as exc:
        if getattr(exc, "status", None) == 429:
            raise ValueError("OpenSky rate limit reached for anonymous use; "
                             "try again in a few minutes")
        raise
    if not isinstance(data, dict):
        data = {}
    limit = max(1, int(max_aircraft))
    features = []
    for row in data.get("states") or []:
        if not isinstance(row, (list, tuple)):
            continue
        lon, lat = _f(_at(row, 5)), _f(_at(row, 6))
        if lon is None or lat is None:
            continue
        altitude = _f(_at(row, 7))
        if altitude is None:
            altitude = _f(_at(row, 13))
        speed = _f(_at(row, 9))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "icao24": _at(row, 0),
                "callsign": (_at(row, 1) or "").strip(),
                "country": _at(row, 2),
                "altitude_m": altitude,
                "on_ground": bool(_at(row, 8)),
                "speed_kmh": round(speed * 3.6, 1) if speed is not None else None,
                "heading_deg": _f(_at(row, 10)),
                "vertical_rate_ms": _f(_at(row, 11)),
                "squawk": _at(row, 14),
                "last_contact": _iso(_at(row, 4)),
            },
        })
        if len(features) >= limit:
            break
    grounded = sum(1 for f in features if f["properties"]["on_ground"])
    summary = {"aircraft": len(features), "airborne": len(features) - grounded,
               "on_ground": grounded, "snapshot_time": _iso(data.get("time"))}
    return {"features": features, "summary": summary,
            "source": "The OpenSky Network (CC BY-SA 4.0; anonymous use "
                      "limited to a few hundred requests a day)"}
