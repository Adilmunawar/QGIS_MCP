# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Water and ocean gauges: USGS river gauges, NOAA NDBC buoys, NOAA tide stations.

All keyless and public domain. Each function returns point features plus a
short summary.
"""

import time

from .. import net

USGS_IV = "https://waterservices.usgs.gov/nwis/iv/"
NDBC_LATEST = "https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt"
TIDE_STATIONS = ("https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/"
                 "stations.json")
TIDE_DATA = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
CFS_TO_M3S = 0.0283168
FT_TO_M = 0.3048
MISSING = -999999.0


def _check_bbox(bbox):
    try:
        west, south, east, north = [float(v) for v in bbox]
    except (TypeError, ValueError):
        raise ValueError("bbox must be [west, south, east, north] in degrees")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox must satisfy west < east and south < north "
                         "within WGS84 limits")
    return west, south, east, north


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= MISSING:
        return None
    return number


def usgs_gauges(bbox, parameters=("00060", "00065", "00010")):
    west, south, east, north = _check_bbox(bbox)
    if (east - west) * (north - south) > 25:
        raise ValueError("USGS refuses boxes larger than 25 square degrees; "
                         "use a smaller bbox")
    params = {"format": "json",
              "bBox": f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}",
              "parameterCd": ",".join(str(p) for p in parameters),
              "siteStatus": "active"}
    data = net.get_json(USGS_IV, params, timeout_ms=90_000)
    sites = {}
    for series in ((data.get("value") or {}).get("timeSeries") or []):
        info = series.get("sourceInfo") or {}
        codes = info.get("siteCode") or [{}]
        site_no = codes[0].get("value")
        loc = (info.get("geoLocation") or {}).get("geogLocation") or {}
        lon, lat = _num(loc.get("longitude")), _num(loc.get("latitude"))
        if not site_no or lon is None or lat is None:
            continue
        var = series.get("variable") or {}
        pcode = ((var.get("variableCode") or [{}])[0]).get("value")
        blocks = series.get("values") or [{}]
        readings = (blocks[0] or {}).get("value") or []
        if not readings:
            continue
        last = readings[-1] or {}
        number = _num(last.get("value"))
        if number is None:
            continue
        site = sites.setdefault(site_no, {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"site_no": site_no, "name": info.get("siteName"),
                           "time": None,
                           "url": "https://waterdata.usgs.gov/"
                                  f"monitoring-location/{site_no}"},
        })
        props = site["properties"]
        if pcode == "00060":
            props["discharge_cfs"] = number
            props["discharge_m3s"] = round(number * CFS_TO_M3S, 3)
        elif pcode == "00065":
            props["gage_height_ft"] = number
            props["gage_height_m"] = round(number * FT_TO_M, 3)
        elif pcode == "00010":
            props["water_temp_c"] = number
        stamp = last.get("dateTime")
        if stamp and (props["time"] is None or stamp > props["time"]):
            props["time"] = stamp
    features = list(sites.values())
    flows = [(f["properties"]["discharge_m3s"], f["properties"]["name"])
             for f in features if "discharge_m3s" in f["properties"]]
    top = max(flows, key=lambda x: x[0]) if flows else (None, None)
    summary = {"sites": len(features), "with_discharge": len(flows),
               "max_discharge_m3s": top[0], "max_site": top[1]}
    return {"features": features, "summary": summary,
            "source": "USGS National Water Information System (public domain)"}


# Column index of each field in latest_obs.txt (STN LAT LON YYYY MM DD hh mm ...)
NDBC_FIELDS = (
    ("wind_dir_deg", 8), ("wind_speed_ms", 9), ("gust_ms", 10),
    ("wave_height_m", 11), ("dominant_period_s", 12), ("average_period_s", 13),
    ("wave_dir_deg", 14), ("pressure_hpa", 15), ("pressure_tendency_hpa", 16),
    ("air_temp_c", 17), ("water_temp_c", 18), ("dewpoint_c", 19),
    ("visibility_nmi", 20), ("tide_ft", 21),
)


def _ndbc_time(cols):
    try:
        y, mo, d, h, mi = [int(c) for c in cols[3:8]]
    except ValueError:
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:00Z"


def ndbc_buoys(bbox=None):
    box = _check_bbox(bbox) if bbox else None
    _status, content = net.fetch(NDBC_LATEST, timeout_ms=90_000)
    features = []
    for line in content.decode("utf-8", "replace").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split()
        if len(cols) < 8:
            continue
        lat, lon = _num(cols[1]), _num(cols[2])
        if lat is None or lon is None:
            continue
        if box and not (box[0] <= lon <= box[2] and box[1] <= lat <= box[3]):
            continue
        props = {"station": cols[0], "time": _ndbc_time(cols)}
        for name, idx in NDBC_FIELDS:
            if idx < len(cols):
                value = None if cols[idx] == "MM" else _num(cols[idx])
                if value is not None:
                    props[name] = value
        props["url"] = ("https://www.ndbc.noaa.gov/station_page.php?"
                        f"station={cols[0]}")
        features.append({"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [lon, lat]},
                         "properties": props})
    waves = [f["properties"]["wave_height_m"] for f in features
             if "wave_height_m" in f["properties"]]
    winds = [f["properties"]["wind_speed_ms"] for f in features
             if "wind_speed_ms" in f["properties"]]
    summary = {"stations": len(features),
               "max_wave_height_m": max(waves) if waves else None,
               "max_wind_ms": max(winds) if winds else None}
    return {"features": features, "summary": summary,
            "source": "NOAA National Data Buoy Center (public domain)"}


def _tide_reading(station_id):
    params = {"date": "latest", "station": station_id, "product": "water_level",
              "datum": "MLLW", "units": "metric", "time_zone": "gmt",
              "format": "json"}
    try:
        data = net.get_json(TIDE_DATA, params, timeout_ms=30_000)
    except (net.HttpError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("error"):
        return None
    rows = data.get("data") or []
    if not rows:
        return None
    value = _num((rows[0] or {}).get("v"))
    if value is None:
        return None
    return value, (rows[0] or {}).get("t")


def noaa_tide_stations(bbox, max_latest=20):
    west, south, east, north = _check_bbox(bbox)
    data = net.get_json(TIDE_STATIONS, {"type": "waterlevels"}, timeout_ms=90_000)
    features = []
    for st in data.get("stations") or []:
        lat, lon = _num(st.get("lat")), _num(st.get("lng"))
        if lat is None or lon is None or not st.get("id"):
            continue
        if not (west <= lon <= east and south <= lat <= north):
            continue
        sid = str(st["id"])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"station_id": sid, "name": st.get("name"),
                           "state": st.get("state"), "datum": "MLLW",
                           "url": "https://tidesandcurrents.noaa.gov/"
                                  f"stationhome.html?id={sid}"},
        })
    with_reading = 0
    for feature in features[:max(0, int(max_latest))]:
        reading = _tide_reading(feature["properties"]["station_id"])
        if reading:
            feature["properties"]["water_level_m"] = reading[0]
            feature["properties"]["time"] = reading[1]
            with_reading += 1
        time.sleep(0.1)
    return {"features": features,
            "summary": {"stations": len(features), "with_reading": with_reading},
            "source": "NOAA CO-OPS (public domain)"}
