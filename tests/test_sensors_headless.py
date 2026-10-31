"""Offline tests for the 4.4.0 toolbox: river gauges, buoys, tides, aircraft,
SensorThings URIs, WorldPop parsing, the population dataset and 3D Tiles.

    "C:/Program Files/QGIS 4.2.2/bin/python-qgis.bat" tests/test_sensors_headless.py
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
from qgis.core import QgsProject  # noqa: E402

from qgis_mcp_plugin import net  # noqa: E402
from qgis_mcp_plugin.command_help import COMMAND_HELP  # noqa: E402
from qgis_mcp_plugin.connectors import (  # noqa: E402
    aircraft, gauges, landcover, population, registry, sensorthings,
)
from qgis_mcp_plugin.mcp_server import QgisMCPServer  # noqa: E402
from qgis_mcp_plugin.permissions import COMMAND_CATEGORIES, MASTER, PermissionManager  # noqa: E402

check = _bootstrap.Checker()

USGS = {"value": {"timeSeries": [
    {"sourceInfo": {"siteName": "DUTCH CR NEAR LITTLETON, CO", "siteCode": [{"value": "06709910"}],
                    "geoLocation": {"geogLocation": {"latitude": 39.6001, "longitude": -105.0419}}},
     "variable": {"variableCode": [{"value": "00060"}], "unit": {"unitCode": "ft3/s"}},
     "values": [{"value": [{"value": "3.53", "dateTime": "2026-10-31T00:50:00.000-06:00"}]}]},
    {"sourceInfo": {"siteName": "DUTCH CR NEAR LITTLETON, CO", "siteCode": [{"value": "06709910"}],
                    "geoLocation": {"geogLocation": {"latitude": 39.6001, "longitude": -105.0419}}},
     "variable": {"variableCode": [{"value": "00065"}], "unit": {"unitCode": "ft"}},
     "values": [{"value": [{"value": "1.20", "dateTime": "2026-10-31T00:55:00.000-06:00"}]}]},
    {"sourceInfo": {"siteName": "BROKEN", "siteCode": [{"value": "1"}],
                    "geoLocation": {"geogLocation": {"latitude": 39.7, "longitude": -105.0}}},
     "variable": {"variableCode": [{"value": "00060"}], "unit": {"unitCode": "ft3/s"}},
     "values": [{"value": [{"value": "-999999", "dateTime": "2026-10-31T00:50:00.000-06:00"}]}]},
]}}
NDBC = """#STN       LAT      LON  YYYY MM DD hh mm WDIR WSPD   GST WVHT  DPD APD MWD   PRES  PTDY  ATMP  WTMP  DEWP  VIS   TIDE
#text      deg      deg   yr mo day hr mn degT  m/s   m/s   m   sec sec degT   hPa   hPa  degC  degC  degC  nmi     ft
15009     0.000   -3.051 2026 10 31 06 00 159   6.2    MM   MM  MM   MM  MM 1013.7    MM  27.2  27.6    MM   MM     MM
22101    37.24   126.02  2026 10 31 07 00 330   6.0    MM  0.5   0   MM  MM     MM    MM  13.0  19.4    MM   MM     MM
"""
TIDES = {"count": 2, "stations": [
    {"id": "8454000", "name": "Providence", "lat": 41.8072, "lng": -71.4007, "state": "RI"},
    {"id": "1611400", "name": "Nawiliwili", "lat": 21.9544, "lng": -159.3561, "state": "HI"}]}
TIDE_READING = {"metadata": {"id": "8454000", "name": "Providence", "lat": "41.8072", "lon": "-71.4007"},
                "data": [{"t": "2026-10-31 07:36", "v": "0.581", "s": "0.014", "f": "0,0,0,0", "q": "p"}]}
OPENSKY = {"time": 1793432515, "states": [
    ["801640", "AIC4YQ  ", "India", 1793432514, 1793432514, 76.7873, 29.4164, 5204.46, False, 172.56, 177.09, -4.88, None, 5440.68, None, False, 0],
    ["801648", "IGO35AV ", "India", 1793432489, 1793432489, 77.1019, 28.5649, None, True, 10.29, 104.06, None, None, None, None, False, 0],
    ["dead", "X", "Nowhere", 1, 1, None, None, None, False, None, None, None, None, None, None, False, 0]]}
NOISE = b"<br />\n<b>Deprecated</b>: noise {not json} in <b>/srv/x.php</b> on line <b>1</b><br />\n"
WP_CREATED = NOISE + json.dumps({"status": "created", "status_code": 200, "error": False, "error_message": None,
                                 "taskid": "abc-123"}).encode() + NOISE
WP_STARTED = NOISE + json.dumps({"status": "started", "error": False, "taskid": "abc-123"}).encode() + NOISE
WP_DONE = NOISE + json.dumps({"taskid": "abc-123", "status": "finished", "error": False, "error_message": None,
                              "data": {"total_population": 1234567.8}}).encode() + NOISE
WP_AGE = json.dumps({"taskid": "age-1", "status": "finished", "error": False,
                     "data": {"agesexpyramid": [{"class": "0", "age": "0 to 1", "male": 10.0, "female": 9.0},
                                                {"class": "1", "age": "1 to 5", "male": 40.0, "female": 38.0},
                                                {"class": "15", "age": "15 to 20", "male": 300.0, "female": 290.0},
                                                {"class": "65", "age": "65 to 70", "male": 20.0, "female": 25.0},
                                                {"class": "5", "age": "5 to 10", "male": 1.0, "female": 1.0}]}}).encode()
polls = {"n": 0}


def fake_fetch(url, params=None, headers=None, method="GET", body=None, timeout_ms=0):
    if "waterservices.usgs.gov" in url:
        return 200, json.dumps(USGS).encode()
    if "ndbc.noaa.gov" in url:
        return 200, NDBC.encode()
    if "mdapi/prod/webapi/stations" in url:
        return 200, json.dumps(TIDES).encode()
    if "datagetter" in url:
        return 200, json.dumps(TIDE_READING).encode()
    if "opensky" in url:
        return 200, json.dumps(OPENSKY).encode()
    if "api.worldpop.org/v1/services/stats" in url:
        if (params or {}).get("dataset") == "wpgpas":
            return 200, NOISE + json.dumps({"status": "created", "taskid": "age-1", "error": False}).encode()
        return 200, WP_CREATED
    if "api.worldpop.org/v1/tasks/age-1" in url:
        return 200, WP_AGE
    if "api.worldpop.org/v1/tasks/" in url:
        polls["n"] += 1
        return 200, WP_DONE if polls["n"] >= 2 else WP_STARTED
    if "iot.hamburg.de" in url and url.rstrip("/").endswith("v1.1"):
        return 200, json.dumps({"value": [{"name": "Things", "url": "x"}, {"name": "Locations", "url": "y"}]}).encode()
    if "iot.hamburg.de" in url and "Locations" in url:
        return 200, json.dumps({"@iot.count": 42, "value": [{"name": "A station", "description": ""}]}).encode()
    if "gost.example/v1.0/Locations" in url:
        if "page2" in url:
            return 200, json.dumps({"value": [{"@iot.id": 3, "name": "C", "location": {"type": "Point", "coordinates": [4.95, 52.4]}}]}).encode()
        assert "SRID=4326" in (params or {}).get("$filter", "SRID=4326"), params
        return 200, json.dumps({"@iot.count": 3, "@iot.nextLink": "https://gost.example/v1.0/Locations?page2",
                                "value": [{"@iot.id": 1, "name": "A", "description": "d", "@iot.selfLink": "https://gost.example/v1.0/Locations(1)",
                                           "location": {"type": "Feature", "geometry": {"type": "Point", "coordinates": [4.9, 52.35]}},
                                           "properties": {"owner": "x", "nested": {"a": 1}}},
                                          {"@iot.id": 2, "name": "B", "location": {"type": "Polygon", "coordinates": []}}]}).encode()
    raise net.HttpError(url, 404, "no stub")


net.fetch = fake_fetch

# gauges, buoys, tides --------------------------------------------------------------
g = gauges.usgs_gauges([-105.3, 39.6, -104.8, 40.0])
props = {f["properties"]["site_no"]: f["properties"] for f in g["features"]}
check("usgs: series merged per site, units converted", props["06709910"]["discharge_cfs"] == 3.53
      and abs(props["06709910"]["discharge_m3s"] - 0.1) < 0.01 and props["06709910"]["gage_height_ft"] == 1.2
      and abs(props["06709910"]["gage_height_m"] - 0.366) < 0.01, props)
check("usgs: site with only missing values dropped", "1" not in props and g["summary"]["sites"] == 1, (list(props), g["summary"]))
try:
    gauges.usgs_gauges([-110, 30, -100, 40])
    check("usgs: large box refused", False)
except ValueError:
    check("usgs: large box refused", True)
b = gauges.ndbc_buoys([120, 30, 130, 40])
check("ndbc: parsed, MM is None, bbox filter", len(b["features"]) == 1
      and b["features"][0]["properties"]["station"] == "22101" and b["features"][0]["properties"]["wave_height_m"] == 0.5
      and "gust_ms" not in b["features"][0]["properties"] and b["features"][0]["properties"]["dominant_period_s"] == 0 and b["features"][0]["properties"]["time"].startswith("2026-10-31T07:00"), b)
check("ndbc: all stations without bbox", len(gauges.ndbc_buoys(None)["features"]) == 2)
t = gauges.noaa_tide_stations([-72, 41, -71, 42])
check("tides: filtered and latest reading", len(t["features"]) == 1
      and t["features"][0]["properties"]["water_level_m"] == 0.581 and t["summary"]["with_reading"] == 1, t)

# aircraft ------------------------------------------------------------------------------
a = aircraft.opensky_states([60, 24, 78, 37])
check("opensky: rows parsed, positionless skipped", a["summary"]["aircraft"] == 2 and a["summary"]["airborne"] == 1
      and a["features"][0]["properties"]["callsign"] == "AIC4YQ"
      and abs(a["features"][0]["properties"]["speed_kmh"] - 621.2) < 0.2, a)
try:
    aircraft.opensky_states([0, 0, 50, 50])
    check("opensky: huge box refused", False)
except ValueError:
    check("opensky: huge box refused", True)

# sensorthings ---------------------------------------------------------------------------
uri = sensorthings.layer_uri("https://iot.hamburg.de/v1.1/", "Location", 50, 200, "geo.intersects(location, geography'POINT(1 1)')")
check("sensorthings: uri built", "entity='Location'" in uri and "featureLimit='50'" in uri and uri.startswith(" type=Point entity=")
      and "url='https://iot.hamburg.de/v1.1'" in uri and uri.endswith(" sql=geo.intersects(location, geography'POINT(1 1)')"), uri)
for bad in (("http://x/v1.1", "Location"), ("https://x/v1.1", "Thing"), ("https://x/v1.1", "Nope"),
            ("https://x/v1.1", "Location", 10, 200, None, None, "cube")):
    try:
        sensorthings.layer_uri(*bad)
        check(f"sensorthings: {bad} refused", False)
    except ValueError:
        check(f"sensorthings: {bad} refused", True)
d = sensorthings.discover("https://iot.hamburg.de/v1.1")
check("sensorthings: discover", d["entity_sets"] == ["Things", "Locations"] and d["locations"] == 42, d)
check("sensorthings: presets have urls", all(v["url"].startswith("https://") for v in sensorthings.PRESETS.values()))
snap = sensorthings.fetch_locations("https://gost.example/v1.0", [4.8, 52.3, 5.0, 52.45], 10)
check("sensorthings: snapshot pages, keeps points, flattens properties", len(snap["features"]) == 2
      and snap["features"][0]["properties"]["prop_owner"] == "x" and "prop_nested" not in snap["features"][0]["properties"]
      and snap["matched"] == 3 and snap["features"][1]["properties"]["name"] == "C", snap)
check("sensorthings: extent filter carries the SRID", sensorthings.extent_filter_for([1, 2, 3, 4]).startswith("geo.intersects(location, geography'SRID=4326;POLYGON ((1.0 2.0, 3.0 2.0"))

# population ------------------------------------------------------------------------------
check("worldpop: json extracted from noisy html", population._parse(WP_CREATED)["taskid"] == "abc-123")
polls["n"] = 0
wp = population.worldpop_total([71.4, 30.1, 71.6, 30.3], 2020)
check("worldpop: polled to finished total", wp["total_population"] == 1234568 and wp["taskid"] == "abc-123"
      and 380 < wp["area_km2"] < 440 and wp["density_per_km2"] > 2000, wp)
try:
    population._parse(b"<html>no json here</html>")
    check("worldpop: no json raises", False)
except ValueError:
    check("worldpop: no json raises", True)

# population dataset summary ---------------------------------------------------------------
tmp = tempfile.mkdtemp()
pop = os.path.join(tmp, "pop.tif")
ds = gdal.GetDriverByName("GTiff").Create(pop, 10, 10, 1, gdal.GDT_Float32)
ds.SetGeoTransform((71.0, 0.0003, 0, 30.1, 0, -0.0003))
ds.SetProjection('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]')
arr = np.full((10, 10), 2.5, dtype="float32")
arr[0, :] = np.nan
ds.GetRasterBand(1).WriteArray(arr)
ds.GetRasterBand(1).SetNoDataValue(float("nan"))
ds = None
spec = landcover.DATASETS["population"]
check("population dataset: direct url resolved", landcover.dataset_hrefs("population", [71, 30, 71.1, 30.1])[1] == [spec["url"]])
phys = landcover.to_physical_units(pop, spec)
summary = landcover.summarise(phys, spec)
check("population dataset: cells summed, nan excluded", summary["kind"] == "continuous" and summary["valid_pixels"] == 90
      and summary["total"] == 225 and "sum" in summary, summary)

# wiring ------------------------------------------------------------------------------------
server = QgisMCPServer(port=9966, iface=None, permissions=PermissionManager(MASTER))
handlers = server._handlers()
new = ["fetch_river_gauges", "fetch_buoys", "fetch_tide_stations", "fetch_aircraft", "list_sensorthings_servers",
       "discover_sensorthings", "add_sensorthings_layer", "estimate_population", "add_3d_tiles"]
check("wiring: 9 sensor commands registered", all(c in handlers for c in new), [c for c in new if c not in handlers])
check("wiring: categories and help", all(c in COMMAND_CATEGORIES and c in COMMAND_HELP for c in new))
check("wiring: registry commands exist", all(c in handlers for e in registry.REGISTRY.values() for c in e["commands"]),
      [c for e in registry.REGISTRY.values() for c in e["commands"] if c not in handlers])
check("wiring: 118 commands", len(handlers) == 118, len(handlers))
check("wiring: google tiles need a key", registry.REGISTRY["google-3d-tiles"]["key_setting"] == "qgis_mcp/keys/google_maps")


def run(cmd, **p):
    return server.execute_command({"type": cmd, "params": p})


r = run("fetch_river_gauges", aoi={"bbox": [-105.3, 39.6, -104.8, 40.0]})
check("cmd: gauges layer", r["status"] == "success" and r["result"]["features"] == 1 and "layer_id" in r["result"]
      and r["result"]["gauges"][0]["site_no"] == "06709910", r)
r = run("fetch_buoys", aoi={"bbox": [125, 36, 127, 38]})
check("cmd: buoys layer", r["status"] == "success" and r["result"]["features"] == 1)
r = run("fetch_buoys", aoi={"bbox": [0, 0, 1, 1]})
check("cmd: no buoys gives a hint", r["status"] == "success" and r["result"]["features"] == 0 and "hint" in r["result"])
r = run("fetch_tide_stations", aoi={"bbox": [-72, 41, -71, 42]})
check("cmd: tide layer", r["status"] == "success" and r["result"]["features"] == 1)
r = run("fetch_aircraft", aoi={"bbox": [76, 28, 78, 30]})
check("cmd: aircraft layer", r["status"] == "success" and r["result"]["features"] == 2)
r = run("list_sensorthings_servers")
check("cmd: sensorthings presets", r["status"] == "success" and len(r["result"]["presets"]) >= 2)
r = run("discover_sensorthings", preset="hamburg")
check("cmd: discover by preset", r["status"] == "success" and r["result"]["locations"] == 42)
r = run("discover_sensorthings", url="ftp://nope")
check("cmd: discover refuses bad url", r["status"] == "error" and "https" in r["message"])
r = run("add_sensorthings_layer", url="https://x/v1.1", entity="Thing")
check("cmd: sensorthings refuses entity without geometry", r["status"] == "error" and "geometry" in r["message"])
r = run("add_sensorthings_layer", url="https://gost.example/v1.0", aoi={"bbox": [4.8, 52.3, 5.0, 52.45]}, snapshot=True)
check("cmd: sensorthings snapshot layer", r["status"] == "success" and r["result"]["live"] is False
      and r["result"]["features"] == 2 and QgsProject.instance().mapLayer(r["result"]["layer_id"]).providerType() == "ogr", r)
r = run("add_sensorthings_layer", url="https://gost.example/v1.0", entity="Datastream", snapshot=True)
check("cmd: snapshot maps locations only", r["status"] == "error" and "Locations" in r["message"])
r = run("add_3d_tiles")
check("cmd: tiles need url or preset", r["status"] == "error")
r = run("add_3d_tiles", preset="google")
check("cmd: google tiles need a key", r["status"] == "error" and "set_data_source_key" in r["message"])
r = run("add_3d_tiles", url="http://x/tileset.json")
check("cmd: tiles url must be https", r["status"] == "error" and "https" in r["message"])
r = run("add_3d_tiles", preset="nope")
check("cmd: unknown tiles preset", r["status"] == "error" and "cesium-sample" in r["message"])
r = run("estimate_population", aoi={"bbox": [70, 28, 72.5, 30.5]})
check("cmd: population refuses huge area", r["status"] == "error" and "2 by 2" in r["message"])
polls["n"] = 0
r = run("estimate_population", aoi={"bbox": [71.4, 30.1, 71.6, 30.3]}, age_sex=True)
res = r.get("result") or {}
check("cmd: population from worldpop with age pyramid", r["status"] == "success"
      and res["worldpop"]["total_population"] == 1234568 and res["best_estimate"]
      and res["age_sex"]["children_under_15"] == 99 and res["age_sex"]["elderly_65_plus"] == 45
      and [r["age_from"] for r in res["age_sex"]["rows"]] == [0, 1, 5, 15, 65]
      and ("meta_hrsl" in res or "meta_hrsl" in res.get("errors", {})), r)
check("cmd: population best estimate averages the sources when both answer",
      "meta_hrsl" not in res or res["best_estimate"] == int(round((1234568 + res["meta_hrsl"]["total_population"]) / 2)), res.get("best_estimate"))

sys.exit(check.report())
