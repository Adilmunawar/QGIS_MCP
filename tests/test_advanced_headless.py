"""Offline tests for the 4.3.0 toolbox: datasets catalogue and unit conversion,
climate and seasonal, EONET, OpenAQ parsing, elevation profiles, 3D
buildings and terrain, point clouds, the live monitor and PDF reports.

    "C:/Program Files/QGIS 4.2.2/bin/python-qgis.bat" tests/test_advanced_headless.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap  # noqa: E402

_bootstrap.start_qgis()

import numpy as np  # noqa: E402
from osgeo import gdal  # noqa: E402
from qgis.core import (  # noqa: E402
    QgsExpression, QgsExpressionContext, QgsExpressionContextUtils, QgsFeature, QgsGeometry,
    QgsProject, QgsRasterLayer, QgsVectorLayer,
)

from qgis_mcp_plugin import monitor, net  # noqa: E402
from qgis_mcp_plugin.command_help import COMMAND_HELP  # noqa: E402
from qgis_mcp_plugin.connectors import (  # noqa: E402
    airquality, events, landcover, registry, stac, weather,
)
from qgis_mcp_plugin.mcp_server import QgisMCPServer  # noqa: E402
from qgis_mcp_plugin.permissions import COMMAND_CATEGORIES, MASTER, PermissionManager  # noqa: E402

check = _bootstrap.Checker()

ELEV = [100.0 + i for i in range(100)]
CLIM = {"daily_units": {"time": "iso8601", "temperature_2m_mean_CMCC_CM2_VHR4": "°C",
                        "precipitation_sum_CMCC_CM2_VHR4": "mm",
                        "temperature_2m_mean_EC_Earth3P_HR": "°C", "precipitation_sum_EC_Earth3P_HR": "mm"},
        "daily": {"time": [f"2040-01-{d:02d}" for d in range(1, 11)] + [f"2041-01-{d:02d}" for d in range(1, 11)],
                  "temperature_2m_mean_CMCC_CM2_VHR4": [14.0] * 10 + [16.0] * 10,
                  "precipitation_sum_CMCC_CM2_VHR4": [1.0] * 20,
                  "temperature_2m_mean_EC_Earth3P_HR": [0.0] * 20,
                  "precipitation_sum_EC_Earth3P_HR": [0.0] * 20}}
SEAS = {"daily_units": {"temperature_2m_max": "°C", "precipitation_sum": "mm"},
        "daily": {"time": [f"2026-11-{d:02d}" for d in range(1, 15)],
                  "temperature_2m_max": [30.0] * 14, "temperature_2m_max_member01": [29.0] * 14,
                  "temperature_2m_max_member02": [31.0] * 14,
                  "precipitation_sum": [1.0] * 14, "precipitation_sum_member01": [0.0] * 14,
                  "precipitation_sum_member02": [2.0] * 14}}
EONET = {"events": [
    {"id": "EONET_1", "title": "Wildfire test", "link": "x", "closed": None,
     "categories": [{"id": "wildfires", "title": "Wildfires"}], "sources": [{"id": "IRWIN"}],
     "geometry": [{"magnitudeValue": 10.0, "magnitudeUnit": "acres", "date": "2026-10-28T18:02:00Z",
                   "type": "Point", "coordinates": [71.5, 30.2]}]},
    {"id": "EONET_2", "title": "Flood test", "link": "y", "closed": "2026-10-28T00:00:00Z",
     "categories": [{"id": "floods", "title": "Floods"}], "sources": [{"id": "GDACS"}],
     "geometry": [{"date": "2026-10-20T00:00:00Z", "type": "Point", "coordinates": [10, 10]},
                  {"date": "2026-10-27T00:00:00Z", "type": "Polygon",
                   "coordinates": [[[27.0, 44.0], [28.0, 44.0], [28.0, 45.0], [27.0, 45.0], [27.0, 44.0]]]}]},
]}
OPENAQ_LOC = {"results": [{"id": 7, "name": "Lahore US Consulate", "locality": "Lahore",
                           "country": {"code": "PK"}, "provider": {"name": "AirNow"}, "isMonitor": True,
                           "coordinates": {"latitude": 31.56, "longitude": 74.33},
                           "datetimeLast": {"utc": "2026-10-31T09:00:00Z"},
                           "sensors": [{"id": 70, "parameter": {"name": "pm25"}}]}]}
OPENAQ_LATEST = {"results": [{"sensorsId": 70, "value": 180.5, "datetime": {"utc": "2026-10-31T09:00:00Z"}}]}
USGS = {"features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [71.5, 30.2, 10]},
                      "properties": {"mag": 5.1, "place": "near Multan", "time": 1, "id": "us1", "url": "",
                                     "type": "earthquake"}}]}
METEO = {"current_units": {"temperature_2m": "°C"},
         "current": {"time": "2026-10-31T10:00", "temperature_2m": 41.0, "wind_speed_10m": 5.0,
                     "precipitation": 0.0, "weather_code": 0}}
calls = {"sign": 0, "token": 0}


def fake_fetch(url, params=None, headers=None, method="GET", body=None, timeout_ms=0):
    if "/v1/elevation" in url:
        n = len((params or {}).get("latitude", "").split(","))
        return 200, json.dumps({"elevation": ELEV[:n]}).encode()
    if "climate-api" in url:
        return 200, json.dumps(CLIM).encode()
    if "seasonal-api" in url:
        return 200, json.dumps(SEAS).encode()
    if "eonet" in url:
        return 200, json.dumps(EONET).encode()
    if "earthquake" in url:
        return 200, json.dumps(USGS).encode()
    if "api.open-meteo.com/v1/forecast" in url:
        return 200, json.dumps(METEO).encode()
    if "sensor.community" in url:
        return 200, b"[]"
    if "gdacs" in url:
        return 200, b'<?xml version="1.0"?><rss><channel></channel></rss>'
    if "openaq.org/v3/locations/7/latest" in url:
        return 200, json.dumps(OPENAQ_LATEST).encode()
    if "openaq.org/v3/locations" in url:
        if (headers or {}).get("X-API-Key") != "goodkey12345678":
            raise net.HttpError(url, 401, "unauthorized")
        return 200, json.dumps(OPENAQ_LOC).encode()
    if "blob.core.windows.net" in url and (headers or {}).get("Range"):
        return 206, b"x"
    if "/sas/v1/token/" in url:
        calls["token"] += 1
        return 200, json.dumps({"token": "sv=1&sig=abc", "msft:expiry": "2099-01-01T00:00:00Z"}).encode()
    if "/sas/v1/sign" in url:
        calls["sign"] += 1
        return 200, json.dumps({"href": params["href"] + "?sig=x"}).encode()
    raise net.HttpError(url, 404, "no stub")


net.fetch = fake_fetch

# datasets catalogue ---------------------------------------------------------------
check("datasets: 19 entries with categories", len(landcover.DATASETS) == 19
      and all(d.get("category") for d in landcover.DATASETS.values()))
check("datasets: every continuous entry has a ramp and unit",
      all(d.get("ramp") and d.get("unit") for d in landcover.DATASETS.values() if not d.get("classes")),
      [k for k, d in landcover.DATASETS.items() if not d.get("classes") and not (d.get("ramp") and d.get("unit"))])
check("datasets: no hyphen word in descriptions",
      not any(" - " in d["description"] for d in landcover.DATASETS.values()))

# unit conversion ---------------------------------------------------------------------
tmp = tempfile.mkdtemp()
raw = os.path.join(tmp, "lst.tif")
ds = gdal.GetDriverByName("GTiff").Create(raw, 4, 4, 1, gdal.GDT_UInt16)
ds.SetGeoTransform((71.0, 0.01, 0, 30.1, 0, -0.01))
ds.SetProjection('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]')
arr = np.full((4, 4), 15000, dtype="uint16")  # 15000 * 0.02 - 273.15 = 26.85 C
arr[0, 0] = 0          # nodata
arr[0, 1] = 65000      # 1026 C, outside valid range
ds.GetRasterBand(1).WriteArray(arr)
ds.GetRasterBand(1).SetNoDataValue(0)
ds = None
spec = landcover.DATASETS["land-surface-temperature"]
phys = landcover.to_physical_units(raw, spec)
summary = landcover.continuous_summary(phys, landcover.OUTPUT_NODATA)
check("units: scale and offset applied, nodata and invalid masked",
      summary["valid_pixels"] == 14 and abs(summary["mean"] - 26.85) < 0.01, summary)
check("units: raw file replaced by physical file", not os.path.exists(raw) and phys.endswith("_phys.tif"))
lyr = QgsRasterLayer(phys, "lst", "gdal")
check("units: unit type recorded", lyr.isValid() and "C" in lyr.dataProvider().bandUnitType(1) if hasattr(lyr.dataProvider(), "bandUnitType") else lyr.isValid())
burn = os.path.join(tmp, "burn.tif")
ds = gdal.GetDriverByName("GTiff").Create(burn, 4, 4, 1, gdal.GDT_Int16)
ds.SetGeoTransform((71.0, 0.01, 0, 30.1, 0, -0.01))
ds.SetProjection(QgsRasterLayer(phys, "x", "gdal").crs().toWkt())
b = np.zeros((4, 4), dtype="int16"); b[0, :] = 200; b[3, 3] = -2
ds.GetRasterBand(1).WriteArray(b); ds.GetRasterBand(1).SetNoDataValue(-1); ds = None
bphys = landcover.to_physical_units(burn, landcover.DATASETS["burned-area"])
bsum = landcover.summarise(bphys, landcover.DATASETS["burned-area"])
check("units: burned fraction above threshold", bsum["kind"] == "continuous" and bsum["valid_pixels"] == 15
      and abs(bsum["above_fraction"] - 4 / 15) < 1e-3, bsum)
check("summarise: classes kind", landcover.summarise(bphys, {"classes": {0: ("none", "#000")}, "nodata": -9999})["kind"] == "classes")

# signing cache -----------------------------------------------------------------------
stac._TOKENS.clear()
item = {"id": "i", "collection": "modis-11A2-061", "assets": {"LST_Day_1km": {"href": "https://modiseuwest.blob.core.windows.net/a.tif"}}}
h1, _ = stac.asset_href(item, "LST_Day_1km", "planetary-computer")
h2, _ = stac.asset_href(item, "LST_Day_1km", "planetary-computer")
check("signing: one token per collection, appended as query", calls["token"] == 1 and calls["sign"] == 0
      and h1.endswith("?sv=1&sig=abc") and h1 == h2, (calls, h1))
check("signing: already signed href untouched", stac.sign_href(h1, "modis-11A2-061") == h1)

# climate and seasonal ----------------------------------------------------------------
row = weather.climate_projection([(71.47, 30.2)], 2040, 2041)[0]
y = {r["year"]: r for r in row["yearly"]}
check("climate: yearly means per model and ensemble", y[2040]["temperature_2m_mean"] == 14.0
      and y[2041]["temperature_2m_mean"] == 16.0 and "EC_Earth3P_HR" not in y[2040]["temperature_2m_mean_by_model"], y)
check("climate: precipitation summed per year", y[2040]["precipitation_sum"] == 10.0, y[2040])
check("climate: trend computed", row["trend"]["temperature_2m_mean"]["change"] == 2.0, row["trend"])
for bad in ((2040, 2039), (2000, 2050)):
    try:
        weather.climate_projection([(0, 0)], *bad)
        check(f"climate: {bad} refused", False)
    except ValueError:
        check(f"climate: {bad} refused", True)
s = weather.seasonal_forecast([(71.47, 30.2)], 14)[0]
check("seasonal: member spread", s["daily"][0]["temperature_2m_max_min"] == 29.0
      and s["daily"][0]["temperature_2m_max_max"] == 31.0 and s["daily"][0]["precipitation_sum_max"] == 2.0, s["daily"][0])
check("seasonal: weekly blocks", len(s["weekly"]) == 2 and s["weekly"][0]["precipitation_sum"] == 7.0, s["weekly"])

# EONET ---------------------------------------------------------------------------------
ev = events.events(None, None, "all", 30)
props = {f["properties"]["id"]: f["properties"] for f in ev["features"]}
check("eonet: last geometry used, polygon centroid", len(ev["features"]) == 2
      and props["EONET_2"]["track_points"] == 2 and props["EONET_2"]["closed"] is True
      and abs(ev["features"][1]["geometry"]["coordinates"][0] - 27.5) < 0.01, ev)
check("eonet: by category", ev["by_category"] == {"Wildfires": 1, "Floods": 1})
try:
    events.events(None, ["lava"])
    check("eonet: unknown category refused", False)
except ValueError:
    check("eonet: unknown category refused", True)

# OpenAQ ---------------------------------------------------------------------------------
aq = airquality.stations(74.33, 31.56, 25, "goodkey12345678")
check("openaq: station parsed with latest value", aq["summary"]["stations"] == 1
      and aq["features"][0]["properties"].get("pm25") == 180.5, aq)
try:
    airquality.stations(74.33, 31.56, 25, "badkey")
    check("openaq: bad key explained", False)
except ValueError as exc:
    check("openaq: bad key explained", "register" in str(exc))

# monitor rules ----------------------------------------------------------------------------
cfg = monitor.make_config([71.4, 30.1, 71.6, 30.3], [71.5, 30.2], None, 0, {"weather": {"temperature_2m": 35}})
check("monitor: interval clamped and rules merged", cfg["interval_minutes"] == 1
      and cfg["rules"]["weather"]["temperature_2m"] == 35 and cfg["rules"]["weather"]["wind_speed_10m"] == 60.0)
fetched = monitor.fetch(cfg)
seen = set()
alerts = monitor.evaluate(cfg, fetched, seen)
check("monitor: quake, heat and event alerts", sorted(a["source"] for a in alerts)
      == ["earthquakes", "natural_events", "natural_events", "weather"], alerts)
again = monitor.evaluate(cfg, fetched, seen)
check("monitor: one off alerts not repeated, threshold alerts repeat",
      [a["source"] for a in again] == ["weather"], again)
coerced = monitor.make_config([0, 0, 1, 1], [0.5, 0.5], rules={"weather": {"temperature_2m": "38"},
                                                           "disaster_alerts": {"levels": "Red"}})
check("monitor: thresholds coerced", coerced["rules"]["weather"]["temperature_2m"] == 38.0
      and coerced["rules"]["disaster_alerts"]["levels"] == ["red"])
for bad in ({"sources": ["volcano"]}, {"rules": {"x": {}}}, {"rules": {"weather": 3}},
            {"rules": {"weather": {"temperature_2m": "hot"}}}, {"rules": {"earthquakes": {"period": "year"}}}):
    try:
        monitor.make_config([0, 0, 1, 1], [0.5, 0.5], **bad)
        check(f"monitor: {bad} refused", False)
    except ValueError:
        check(f"monitor: {bad} refused", True)

# wiring -----------------------------------------------------------------------------------
server = QgisMCPServer(port=9963, iface=None, permissions=PermissionManager(MASTER))
handlers = server._handlers()
new = ["get_climate_projection", "get_seasonal_forecast", "get_elevation_profile", "make_3d_buildings",
       "add_3d_buildings", "set_terrain", "add_point_cloud", "open_3d_view", "fetch_natural_events",
       "fetch_air_quality_stations", "start_live_monitor", "stop_live_monitor", "get_live_monitor_status"]
check("wiring: 13 advanced commands registered", all(c in handlers for c in new), [c for c in new if c not in handlers])
check("wiring: categories and help", all(c in COMMAND_CATEGORIES and c in COMMAND_HELP for c in new))
check("wiring: registry commands exist",
      all(c in handlers for e in registry.REGISTRY.values() for c in e["commands"]),
      [c for e in registry.REGISTRY.values() for c in e["commands"] if c not in handlers])
check("wiring: 109 commands", len(handlers) == 109, len(handlers))
check("wiring: openaq source needs a key", registry.REGISTRY["openaq"]["key_setting"] == "qgis_mcp/keys/openaq")


def run(cmd, **p):
    return server.execute_command({"type": cmd, "params": p})


r = run("list_datasets", category="elevation")
check("cmd: list_datasets filters by category", r["status"] == "success" and r["result"]["count"] == 4
      and all(d["kind"] == "continuous" for d in r["result"]["datasets"]), r)
r = run("get_climate_projection", lat=30.2, lon=71.47, start_year=2040, end_year=2041)
check("cmd: climate projection", r["status"] == "success" and "place" in r["result"] and r["result"]["trend"])
r = run("get_seasonal_forecast", aoi={"bbox": [71.4, 30.1, 71.6, 30.3]}, days=14)
check("cmd: seasonal", r["status"] == "success" and len(r["result"]["daily"]) == 14)

# elevation profile -------------------------------------------------------------------------
r = run("get_elevation_profile", aoi={"point": [71.47, 30.2], "radius_km": 5}, samples=20, add_to_project=True)
res = r["result"]
check("cmd: profile across area", r["status"] == "success" and len(res["samples"]) == 20 and "west to east" in res["label"]
      and res["statistics"]["gain_m"] == 19.0 and res["statistics"]["length_km"] > 9, res.get("statistics"))
check("cmd: profile chart written", os.path.exists(res["chart_path"]))
check("cmd: profile layer added", QgsProject.instance().mapLayer(res["layer_id"]) is not None)
r = run("get_elevation_profile", line="LINESTRING(71.4 30.1, 71.6 30.3)", samples=3)
check("cmd: profile from WKT", r["status"] == "success" and abs(r["result"]["statistics"]["length_km"] - 29.39) < 0.1)
road = QgsVectorLayer("LineString?crs=EPSG:3857", "road", "memory")
f = QgsFeature()
f.setGeometry(QgsGeometry.fromWkt("LINESTRING(7955000 3530000, 7956000 3531000)"))
road.dataProvider().addFeatures([f])
QgsProject.instance().addMapLayer(road)
r = run("get_elevation_profile", layer_id=road.id(), selected=True, samples=5)
check("cmd: profile needs a selection", r["status"] == "error" and "Select" in r["message"], r)
road.selectAll()
r = run("get_elevation_profile", layer_id=road.id(), selected=True, samples=5)
check("cmd: profile from projected layer feature", r["status"] == "success"
      and 71 < r["result"]["samples"][0]["lon"] < 72, r)
r = run("get_elevation_profile", line="POINT(1 1)")
check("cmd: profile refuses non line WKT", r["status"] == "error")
r = run("get_elevation_profile")
check("cmd: profile without input explains", r["status"] == "error" and "aoi" in r["message"])

# 3D buildings ---------------------------------------------------------------------------------
bld = QgsVectorLayer("Polygon?crs=EPSG:4326&field=height:string&field=building:levels:string", "bld", "memory")
f = QgsFeature(bld.fields())
f.setGeometry(QgsGeometry.fromWkt("POLYGON((71.5 30.2,71.501 30.2,71.501 30.201,71.5 30.201,71.5 30.2))"))
f.setAttributes(["12 m", "3"])
f2 = QgsFeature(bld.fields())
f2.setGeometry(QgsGeometry.fromWkt("POLYGON((71.51 30.2,71.511 30.2,71.511 30.201,71.51 30.201,71.51 30.2))"))
f2.setAttributes([None, "4"])
f3 = QgsFeature(bld.fields())
f3.setGeometry(QgsGeometry.fromWkt("POLYGON((71.52 30.2,71.521 30.2,71.521 30.201,71.52 30.201,71.52 30.2))"))
f3.setAttributes(["tall", "0"])
bld.dataProvider().addFeatures([f, f2, f3])
QgsProject.instance().addMapLayer(bld)
r = run("make_3d_buildings", layer_id=bld.id())
check("cmd: 3d buildings renderer set", r["status"] == "success" and bld.renderer3D() is not None
      and bld.renderer3D().type() == "vector" and r["result"]["height_field"] == "height"
      and r["result"]["levels_field"] == "building:levels", r)
ctx = QgsExpressionContext()
ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(bld))
expr = QgsExpression(r["result"]["height_expression"])
values = []
for feat in bld.getFeatures():
    ctx.setFeature(feat)
    values.append(expr.evaluate(ctx))
check("cmd: height expression uses tag, storeys, then default", values == [12.0, 12.0, 10.0] and not expr.hasEvalError(), (values, expr.evalErrorString()))
check("cmd: elevation properties extruded", bld.elevationProperties().extrusionEnabled())
r = run("make_3d_buildings", layer_id=bld.id(), height_field="nope")
check("cmd: unknown height field refused", r["status"] == "error" and "nope" in r["message"])
r = run("make_3d_buildings", layer_id=road.id())
check("cmd: line layer refused for buildings", r["status"] == "error")

# terrain --------------------------------------------------------------------------------------
r = run("set_terrain", layer_id=bld.id())
check("cmd: terrain refuses vector", r["status"] == "error")
r = run("set_terrain", layer_id=QgsProject.instance().addMapLayer(QgsRasterLayer(phys, "dem", "gdal")).id(), vertical_scale=2)
check("cmd: terrain set from raster", r["status"] == "success"
      and QgsProject.instance().elevationProperties().terrainProvider().type() == "raster"
      and r["result"]["vertical_scale"] == 2.0, r)
r = run("open_3d_view")
check("cmd: 3d view needs the window", r["status"] == "error" and "headless" in r["message"])
r = run("add_point_cloud")
check("cmd: point cloud needs aoi or url", r["status"] == "error")
r = run("add_point_cloud", url="http://x/y.laz")
check("cmd: point cloud url must be https", r["status"] == "error" and "https" in r["message"])

# events and OpenAQ commands -----------------------------------------------------------------------
r = run("fetch_natural_events", aoi={"bbox": [71, 30, 72, 31]}, status="all")
check("cmd: natural events layer", r["status"] == "success" and r["result"]["count"] == 2 and "layer_id" in r["result"])
r = run("fetch_air_quality_stations", lat=31.5, lon=74.3)
check("cmd: openaq without key explains", r["status"] == "error" and "set_data_source_key" in r["message"])
run("set_data_source_key", source_id="openaq", key="goodkey12345678")
r = run("fetch_air_quality_stations", lat=31.5, lon=74.3)
check("cmd: openaq with key adds layer", r["status"] == "success" and r["result"]["summary"]["stations"] == 1
      and "layer_id" in r["result"], r)
server._settings().remove("qgis_mcp/keys/openaq")

# live monitor --------------------------------------------------------------------------------------
r = run("start_live_monitor", aoi={"bbox": [71.4, 30.1, 71.6, 30.3]}, interval_minutes=5,
        rules={"weather": {"temperature_2m": 35}})
check("cmd: monitor started with first run", r["status"] == "success" and r["result"]["first_run"]["run"] == 1
      and len(r["result"]["first_run"]["alerts"]) == 4, r)
r = run("get_live_monitor_status")
check("cmd: monitor status running with layers", r["status"] == "success" and r["result"]["running"]
      and set(r["result"]["layers"]) == {"earthquakes", "weather", "natural_events"}
      and all(QgsProject.instance().mapLayer(l).name().startswith("monitor_") for l in r["result"]["layers"].values()), r)
before = set(r["result"]["layers"].values())
r = run("start_live_monitor", aoi={"bbox": [71.4, 30.1, 71.6, 30.3]}, rules={"bogus": {}})
check("cmd: bad rules refused without stopping the monitor", r["status"] == "error"
      and run("get_live_monitor_status")["result"]["running"])
server._monitor_tick(sync=True)
r = run("get_live_monitor_status")
check("cmd: tick replaces monitor layers", r["result"]["runs"] == 2
      and not (set(r["result"]["layers"].values()) & before)
      and all(QgsProject.instance().mapLayer(l) is None for l in before), r["result"]["layers"])
r = run("stop_live_monitor")
check("cmd: monitor stopped", r["status"] == "success" and r["result"]["stopped"]
      and not run("get_live_monitor_status")["result"]["running"])
r = run("stop_live_monitor")
check("cmd: stopping twice is harmless", r["status"] == "success" and not r["result"]["stopped"])

# PDF report ------------------------------------------------------------------------------------------
pdf = os.path.join(tmp, "report.pdf")
r = run("export_area_report", aoi={"bbox": [71.4, 30.1, 71.6, 30.3]}, path=pdf, include_index=False,
        include_land_cover=False, include_soil=False, render_map=False)
check("cmd: pdf report written", r["status"] == "success" and os.path.exists(pdf) and os.path.getsize(pdf) > 1000
      and os.path.exists(r["result"]["html_path"]), r)
with open(pdf, "rb") as handle:
    check("cmd: pdf header", handle.read(5) == b"%PDF-")
r = run("export_area_report", aoi={"bbox": [71.4, 30.1, 71.6, 30.3]}, path="x.txt")
check("cmd: report extension checked", r["status"] == "error" and ".pdf" in r["message"])

sys.exit(check.report())
