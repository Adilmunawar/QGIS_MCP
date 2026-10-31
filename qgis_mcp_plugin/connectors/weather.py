# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Weather and climate: Open-Meteo and NASA POWER.

Both are keyless. Open-Meteo answers for several points in one call, which
``attach_weather_to_layer`` relies on to stay within fair use.
"""

import datetime as _dt

from .. import net

FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
AIR_QUALITY = "https://air-quality-api.open-meteo.com/v1/air-quality"
FLOOD = "https://flood-api.open-meteo.com/v1/flood"
MARINE = "https://marine-api.open-meteo.com/v1/marine"
ELEVATION = "https://api.open-meteo.com/v1/elevation"
POWER_DAILY = "https://power.larc.nasa.gov/api/temporal/daily/point"
CLIMATE = "https://climate-api.open-meteo.com/v1/climate"
SEASONAL = "https://seasonal-api.open-meteo.com/v1/seasonal"

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
CLIMATE_MODELS = ["CMCC_CM2_VHR4", "FGOALS_f3_H", "HiRAM_SIT_HR",
                  "MRI_AGCM3_2_S", "EC_Earth3P_HR", "MPI_ESM1_2_XR",
                  "NICAM16_8S"]
CLIMATE_DEFAULT = ["temperature_2m_mean", "temperature_2m_max",
                   "precipitation_sum"]
SEASONAL_DEFAULT = ["temperature_2m_max", "temperature_2m_min",
                    "precipitation_sum"]
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


def river_discharge(points, days=7, past_days=30):
    """GloFAS river discharge (m3/s) forecast and recent history per point."""
    lats, lons = _points_param(points)
    data = net.get_json(FLOOD, {
        "latitude": lats, "longitude": lons,
        "daily": ["river_discharge", "river_discharge_mean", "river_discharge_max"],
        "forecast_days": max(1, min(int(days), 210)),
        "past_days": max(0, min(int(past_days), 92)),
    })
    today = _dt.date.today().isoformat()
    rows = []
    for (lon, lat), entry in zip(points, _as_list(data)):
        daily = _columns_to_rows(entry.get("daily")) or []
        values = [r["river_discharge"] for r in daily if r.get("river_discharge") is not None]
        todays = next((r["river_discharge"] for r in daily if r.get("time") == today), None)
        rows.append({"lon": lon, "lat": lat, "units": entry.get("daily_units"),
                     "daily": daily,
                     "today": todays,
                     "latest_forecast": values[-1] if values else None,
                     "max_in_window": max(values) if values else None})
    return rows


def marine(points, days=3):
    """Waves, swell and sea surface temperature for coastal or offshore points."""
    lats, lons = _points_param(points)
    data = net.get_json(MARINE, {
        "latitude": lats, "longitude": lons,
        "current": ["wave_height", "wave_direction", "wave_period",
                    "sea_surface_temperature", "ocean_current_velocity"],
        "daily": ["wave_height_max", "wave_direction_dominant", "wave_period_max"],
        "forecast_days": max(1, min(int(days), 8)),
        "timezone": "auto",
    })
    return [{"lon": lon, "lat": lat, "units": e.get("current_units"),
             "current": e.get("current"), "daily_units": e.get("daily_units"),
             "daily": _columns_to_rows(e.get("daily"))}
            for (lon, lat), e in zip(points, _as_list(data))]


def elevation(points):
    """Elevation in metres (Copernicus DEM 90 m) for up to 100 points."""
    lats, lons = _points_param(points)
    data = net.get_json(ELEVATION, {"latitude": lats, "longitude": lons})
    values = data.get("elevation") or []
    return [{"lon": lon, "lat": lat, "elevation_m": v}
            for (lon, lat), v in zip(points, values)]


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


def climate_projection(points, start_year, end_year, variables=None,
                       models=None):
    """CMIP6 HighResMIP projections (1950 to 2050), aggregated per year.

    One request per point. Per model yearly values are averaged (or summed
    for ``*_sum`` variables), then averaged across models. Models with no
    data for a year (missing or all zero days) are left out of the ensemble.
    """
    try:
        start_year, end_year = int(start_year), int(end_year)
    except (TypeError, ValueError):
        raise ValueError("start_year and end_year must be integers")
    start_year, end_year = max(1950, start_year), min(2050, end_year)
    if start_year > end_year:
        raise ValueError("start_year must not be after end_year (range 1950 to 2050)")
    if end_year - start_year + 1 > 30:
        raise ValueError("Request at most 30 years per call")
    models = list(models or CLIMATE_MODELS)
    bad = [m for m in models if m not in CLIMATE_MODELS]
    if bad:
        raise ValueError(f"Unknown climate models {bad}; use {CLIMATE_MODELS}")
    variables = list(variables or CLIMATE_DEFAULT)
    rows = []
    for lon, lat in points[:MAX_POINTS]:
        data = net.get_json(CLIMATE, {
            "latitude": f"{float(lat):.5f}", "longitude": f"{float(lon):.5f}",
            "start_date": f"{start_year}-01-01", "end_date": f"{end_year}-12-31",
            "models": models, "daily": variables,
        }, timeout_ms=120_000)
        rows.append(_aggregate_climate(lon, lat, start_year, end_year,
                                       models, variables, data))
    return rows


def _aggregate_climate(lon, lat, start_year, end_year, models, variables, data):
    daily = data.get("daily") or {}
    years = [int(t[:4]) for t in daily.get("time") or []]
    yearly = {y: {"year": y} for y in sorted(set(years))}
    units = {}
    # A model with no data for a year reports zeros for every variable at
    # once; a dry year legitimately sums to zero rainfall but not zero heat.
    dead = set()
    for model in models:
        zero_by_year = {}
        for var in variables:
            series = daily.get(f"{var}_{model}")
            if series is None and len(models) == 1:
                series = daily.get(var)
            for y, v in zip(years, series or []):
                zero_by_year.setdefault(y, True)
                if v not in (None, 0.0):
                    zero_by_year[y] = False
        dead.update((model, y) for y, is_zero in zero_by_year.items() if is_zero)
    for var in variables:
        is_sum = var.endswith("_sum")
        units[var] = next((u for k, u in (data.get("daily_units") or {}).items()
                           if k == var or k.startswith(var + "_")), None)
        per_year = {}
        for model in models:
            buckets = {}
            series = daily.get(f"{var}_{model}")
            if series is None and len(models) == 1:
                series = daily.get(var)  # one model: the API drops the suffix
            for y, v in zip(years, series or []):
                buckets.setdefault(y, []).append(v)
            for y, vals in buckets.items():
                real = [v for v in vals if v is not None]
                if not real or (model, y) in dead:
                    continue
                value = sum(real) if is_sum else sum(real) / len(real)
                per_year.setdefault(y, {})[model] = round(value, 2)
        for y, by_model in per_year.items():
            yearly[y][var] = round(sum(by_model.values()) / len(by_model), 2)
            yearly[y][var + "_by_model"] = by_model
    ordered = [yearly[y] for y in sorted(yearly)]
    return {"lon": lon, "lat": lat, "start_year": start_year,
            "end_year": end_year, "models": models, "variables": variables,
            "units": units, "yearly": ordered,
            "trend": _climate_trend(ordered, variables)}


def _climate_trend(yearly, variables):
    trend = {}
    for var in variables:
        values = [r[var] for r in yearly if r.get(var) is not None]
        if len(values) < 2:
            continue
        span = min(10, max(1, len(values) // 2))
        first = sum(values[:span]) / span
        last = sum(values[-span:]) / span
        trend[var] = {"first_decade_mean": round(first, 2),
                      "last_decade_mean": round(last, 2),
                      "change": round(last - first, 2)}
    return trend


def seasonal_forecast(points, days=90, variables=None):
    """Seasonal ensemble forecast (up to 274 days) with member spread.

    Daily rows carry the ensemble mean plus the min and max across members.
    Weekly rows average temperature like variables and sum ``*_sum`` ones.
    """
    days = max(7, min(int(days), 274))
    variables = list(variables or SEASONAL_DEFAULT)
    rows = []
    for lon, lat in points[:MAX_POINTS]:
        data = net.get_json(SEASONAL, {
            "latitude": f"{float(lat):.5f}", "longitude": f"{float(lon):.5f}",
            "daily": variables, "forecast_days": days,
        }, timeout_ms=120_000)
        block = data.get("daily") or {}
        daily = []
        for i, t in enumerate(block.get("time") or []):
            row = {"time": t}
            for var in variables:
                members = [block[k][i] for k in block
                           if k.startswith(var + "_member")
                           and i < len(block[k]) and block[k][i] is not None]
                mean = block.get(var) or []
                row[var] = mean[i] if i < len(mean) else None
                row[var + "_min"] = min(members) if members else None
                row[var + "_max"] = max(members) if members else None
            daily.append(row)
        units = {v: (data.get("daily_units") or {}).get(v) for v in variables}
        rows.append({"lon": lon, "lat": lat, "units": units, "days": days,
                     "daily": daily, "weekly": _weekly(daily, variables)})
    return rows


def _weekly(daily, variables):
    weeks = []
    for start in range(0, len(daily), 7):
        chunk = daily[start:start + 7]
        row = {"week_start": chunk[0]["time"], "days": len(chunk)}
        for var in variables:
            vals = [r[var] for r in chunk if r.get(var) is not None]
            if not vals:
                row[var] = None
            elif var.endswith("_sum"):
                row[var] = round(sum(vals), 2)
            else:
                row[var] = round(sum(vals) / len(vals), 2)
        weeks.append(row)
    return weeks
