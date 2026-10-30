# -*- coding: utf-8 -*-
"""Weather and climate: Open-Meteo and NASA POWER.

Both are keyless. Open-Meteo answers for several points in one call, which
``attach_weather_to_layer`` relies on to stay within fair use.
"""

import datetime as _dt

from .. import net

FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
AIR_QUALITY = "https://air-quality-api.open-meteo.com/v1/air-quality"
POWER_DAILY = "https://power.larc.nasa.gov/api/temporal/daily/point"

CURRENT_DEFAULT = ["temperature_2m", "relative_humidity_2m", "precipitation",
                   "wind_speed_10m", "wind_direction_10m", "weather_code",
                   "cloud_cover", "surface_pressure"]
DAILY_DEFAULT = ["temperature_2m_max", "temperature_2m_min",
                 "precipitation_sum", "wind_speed_10m_max",
                 "et0_fao_evapotranspiration", "shortwave_radiation_sum"]
HOURLY_DEFAULT = ["temperature_2m", "relative_humidity_2m", "precipitation",
                  "wind_speed_10m", "soil_temperature_0cm", "soil_moisture_0_to_1cm"]
AIR_DEFAULT = ["pm10", "pm2_5", "carbon_monoxide", "nitrogen_dioxide",
               "ozone", "us_aqi", "dust"]
POWER_DEFAULT = ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M", "WS2M",
                 "ALLSKY_SFC_SW_DWN"]
MAX_POINTS = 100

WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog", 51: "light drizzle",
    53: "moderate drizzle", 55: "dense drizzle", 61: "slight rain",
    63: "moderate rain", 65: "heavy rain", 71: "slight snow",
    73: "moderate snow", 75: "heavy snow", 80: "slight rain showers",
    81: "moderate rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


def _points_param(points):
    if len(points) > MAX_POINTS:
        raise ValueError(f"At most {MAX_POINTS} points per weather call; "
                         "sample or dissolve the layer first")
    lats = ",".join(f"{float(p[1]):.5f}" for p in points)
    lons = ",".join(f"{float(p[0]):.5f}" for p in points)
    return lats, lons


def _as_list(response):
    return response if isinstance(response, list) else [response]


def _decode_codes(block):
    if block and "weather_code" in block:
        code = block["weather_code"]
        block["weather_description"] = WMO_CODES.get(code, f"code {code}")
    return block


def current(points, variables=None, timezone="auto"):
    """Current conditions for ``[(lon, lat), ...]``."""
    lats, lons = _points_param(points)
    data = net.get_json(FORECAST, {
        "latitude": lats, "longitude": lons,
        "current": variables or CURRENT_DEFAULT,
        "timezone": timezone,
    })
    rows = []
    for (lon, lat), entry in zip(points, _as_list(data)):
        rows.append({
            "lon": lon, "lat": lat,
            "elevation": entry.get("elevation"),
            "timezone": entry.get("timezone"),
            "units": entry.get("current_units"),
            "current": _decode_codes(entry.get("current")),
        })
    return rows


def forecast(points, daily=None, hourly=None, days=7, timezone="auto"):
    lats, lons = _points_param(points)
    params = {"latitude": lats, "longitude": lons, "timezone": timezone,
              "forecast_days": int(days)}
    if daily or not hourly:
        params["daily"] = daily or DAILY_DEFAULT
    if hourly:
        params["hourly"] = hourly
    data = net.get_json(FORECAST, params)
    rows = []
    for (lon, lat), entry in zip(points, _as_list(data)):
        rows.append({
            "lon": lon, "lat": lat, "timezone": entry.get("timezone"),
            "daily_units": entry.get("daily_units"),
            "daily": _columns_to_rows(entry.get("daily")),
            "hourly_units": entry.get("hourly_units"),
            "hourly": _columns_to_rows(entry.get("hourly")),
        })
    return rows


def history(points, start_date, end_date, daily=None, hourly=None,
            timezone="auto"):
    _check_dates(start_date, end_date)
    lats, lons = _points_param(points)
    params = {"latitude": lats, "longitude": lons, "timezone": timezone,
              "start_date": start_date, "end_date": end_date}
    if daily or not hourly:
        params["daily"] = daily or DAILY_DEFAULT
    if hourly:
        params["hourly"] = hourly
    data = net.get_json(ARCHIVE, params)
    rows = []
    for (lon, lat), entry in zip(points, _as_list(data)):
        rows.append({
            "lon": lon, "lat": lat,
            "daily_units": entry.get("daily_units"),
            "daily": _columns_to_rows(entry.get("daily")),
            "hourly_units": entry.get("hourly_units"),
            "hourly": _columns_to_rows(entry.get("hourly")),
        })
    return rows


def air_quality(points, variables=None, timezone="auto"):
    lats, lons = _points_param(points)
    data = net.get_json(AIR_QUALITY, {
        "latitude": lats, "longitude": lons, "timezone": timezone,
        "current": variables or AIR_DEFAULT,
    })
    return [{"lon": lon, "lat": lat, "units": e.get("current_units"),
             "current": e.get("current")}
            for (lon, lat), e in zip(points, _as_list(data))]


def power_daily(lon, lat, start_date, end_date, parameters=None,
                community="AG"):
    """NASA POWER daily agro-climatology for one point."""
    _check_dates(start_date, end_date)
    data = net.get_json(POWER_DAILY, {
        "parameters": parameters or POWER_DEFAULT,
        "community": community,
        "longitude": f"{float(lon):.4f}", "latitude": f"{float(lat):.4f}",
        "start": start_date.replace("-", ""), "end": end_date.replace("-", ""),
        "format": "JSON",
    }, timeout_ms=120_000)
    series = (data.get("properties") or {}).get("parameter") or {}
    units = ((data.get("parameters") or {}))
    dates = sorted({d for values in series.values() for d in values})
    rows = []
    for date in dates:
        row = {"date": f"{date[:4]}-{date[4:6]}-{date[6:]}"}
        for name, values in series.items():
            value = values.get(date)
            row[name] = None if value in (-999, -999.0) else value
        rows.append(row)
    return {
        "lon": lon, "lat": lat,
        "units": {k: v.get("units") for k, v in units.items()} if units else None,
        "parameters": list(series),
        "rows": rows,
    }


def summarise_daily(rows, variable):
    values = [r.get(variable) for r in rows if r.get(variable) is not None]
    if not values:
        return None
    return {"count": len(values), "sum": round(sum(values), 3),
            "mean": round(sum(values) / len(values), 3),
            "min": min(values), "max": max(values)}


def _columns_to_rows(block):
    if not block or "time" not in block:
        return None
    keys = [k for k in block if k != "time"]
    return [dict({"time": t}, **{k: block[k][i] for k in keys})
            for i, t in enumerate(block["time"])]


def _check_dates(start, end):
    try:
        s = _dt.date.fromisoformat(start)
        e = _dt.date.fromisoformat(end)
    except (TypeError, ValueError):
        raise ValueError("Dates must be ISO format YYYY-MM-DD")
    if s > e:
        raise ValueError("start_date must not be after end_date")
    if (e - s).days > 366 * 5:
        raise ValueError("Limit requests to five years at a time")
