# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""Offline tests for the toolbox connectors and commands (stubbed HTTP).

    "C:/Program Files/QGIS 4.2.2/bin/python-qgis.bat" tests/test_toolbox_headless.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap  # noqa: E402

_bootstrap.start_qgis()

from qgis.core import QgsProject  # noqa: E402

from qgis_mcp_plugin import net  # noqa: E402
from qgis_mcp_plugin.command_help import COMMAND_HELP  # noqa: E402
from qgis_mcp_plugin.connectors import (  # noqa: E402
    boundaries, disasters, landcover, registry, sensors, soil, species, weather,
)
from qgis_mcp_plugin.mcp_server import QgisMCPServer  # noqa: E402
from qgis_mcp_plugin.permissions import COMMAND_CATEGORIES, MASTER, PermissionManager  # noqa: E402

check = _bootstrap.Checker()

SOIL = {"type": "Feature", "properties": {"layers": [
    {"name": "clay", "unit_measure": {"d_factor": 10, "target_units": "%"},
     "depths": [{"label": "0-5cm", "values": {"mean": 220}}]},
    {"name": "sand", "unit_measure": {"d_factor": 10, "target_units": "%"},
     "depths": [{"label": "0-5cm", "values": {"mean": 450}}]},
    {"name": "silt", "unit_measure": {"d_factor": 10, "target_units": "%"},
     "depths": [{"label": "0-5cm", "values": {"mean": 330}}]},
    {"name": "phh2o", "unit_measure": {"d_factor": 10, "target_units": "-"},
     "depths": [{"label": "0-5cm", "values": {"mean": 78}}]},
]}}
GEOB_META = {"boundaryName": "Pakistan", "boundaryISO": "PAK", "boundaryType": "ADM2",
             "boundaryCanonical": "Districts", "boundaryYearRepresented": "2019",
             "boundaryLicense": "Public Domain", "boundarySource": "x",
             "gjDownloadURL": "https://github.com/x/full.geojson",
             "simplifiedGeometryGeoJSON": "https://github.com/x/simple.geojson",
             "admUnitCount": "2"}
GEOB_GJ = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"shapeName": "Multan", "shapeISO": "", "shapeID": "1",
                                       "shapeGroup": "PAK", "shapeType": "ADM2"},
     "geometry": {"type": "Polygon", "coordinates": [[[71.0, 29.5], [72.0, 29.5], [72.0, 30.5], [71.0, 30.5], [71.0, 29.5]]]}},
    {"type": "Feature", "properties": {"shapeName": "Karachi", "shapeID": "2", "shapeGroup": "PAK", "shapeType": "ADM2"},
     "geometry": {"type": "Polygon", "coordinates": [[[66.5, 24.5], [67.5, 24.5], [67.5, 25.5], [66.5, 25.5], [66.5, 24.5]]]}},
]}
GBIF_MATCH = {"matchType": "EXACT", "usageKey": 212, "scientificName": "Aves", "rank": "CLASS",
              "kingdom": "Animalia", "confidence": 99}
GBIF_PAGE = {"count": 2, "endOfRecords": True, "results": [
    {"key": 1, "decimalLongitude": 71.5, "decimalLatitude": 30.2, "species": "Passer domesticus",
     "scientificName": "Passer domesticus", "kingdom": "Animalia", "year": 2025, "eventDate": "2025-03-01T00:00",
     "basisOfRecord": "HUMAN_OBSERVATION"},
    {"key": 2, "decimalLongitude": 71.52, "decimalLatitude": 30.21, "species": "Passer domesticus",
     "scientificName": "Passer domesticus", "kingdom": "Animalia", "year": 2024},
    {"key": 3, "decimalLongitude": None, "decimalLatitude": None, "species": "ghost"},
]}
SENSORS = [
    {"location": {"id": 1, "latitude": "31.5", "longitude": "74.3", "country": "PK", "indoor": 0},
     "timestamp": "2026-10-30 20:42:24", "sensor": {"sensor_type": {"name": "SDS011"}},
     "sensordatavalues": [{"value_type": "P1", "value": "40.5"}, {"value_type": "P2", "value": "22.1"}]},
    {"location": {"id": 1, "latitude": "31.5", "longitude": "74.3", "country": "PK", "indoor": 0},
     "timestamp": "2026-10-30 20:42:24", "sensor": {"sensor_type": {"name": "BME280"}},
     "sensordatavalues": [{"value_type": "temperature", "value": "23.7"}]},
    {"location": {"id": 2, "latitude": "31.6", "longitude": "74.4", "country": "PK", "indoor": 1},
     "sensor": {"sensor_type": {"name": "SDS011"}},
     "sensordatavalues": [{"value_type": "P2", "value": "80"}]},
]
GDACS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:gdacs="http://www.gdacs.org" xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#"
 xmlns:georss="http://www.georss.org/georss" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<item><title>Orange flood in Pakistan</title><description>Flooding</description>
<gdacs:eventtype>FL</gdacs:eventtype><gdacs:alertlevel>Orange</gdacs:alertlevel><gdacs:iscurrent>true</gdacs:iscurrent>
<gdacs:country>Pakistan</gdacs:country><geo:Point><geo:lat>30.2</geo:lat><geo:long>71.5</geo:long></geo:Point></item>
<item><title>Green earthquake in Vanuatu</title><description>Quake</description>
<gdacs:eventtype>EQ</gdacs:eventtype><gdacs:alertlevel>Green</gdacs:alertlevel><gdacs:iscurrent>true</gdacs:iscurrent>
<georss:point>-15.1 167.4</georss:point></item>
<item><title>Red cyclone old</title><gdacs:eventtype>TC</gdacs:eventtype><gdacs:alertlevel>Red</gdacs:alertlevel>
<gdacs:iscurrent>false</gdacs:iscurrent><georss:point>10 120</georss:point></item>
</channel></rss>"""
FLOOD = {"latitude": 30.2, "longitude": 71.47, "daily_units": {"river_discharge": "m³/s"},
         "daily": {"time": ["2026-10-30", "2026-10-31"], "river_discharge": [120.5, 130.0],
                   "river_discharge_mean": [100, 110], "river_discharge_max": [150, 160]}}
ELEV = {"elevation": [126.0, 210.5]}


def fake_fetch(url, params=None, headers=None, method="GET", body=None, timeout_ms=0):
    if "isric" in url:
        return 200, json.dumps(SOIL).encode()
    if "geoboundaries.org/api" in url:
        return 200, json.dumps(GEOB_META).encode()
    if url.endswith(".geojson"):
        return 200, json.dumps(GEOB_GJ).encode()
    if "species/match" in url:
        return 200, json.dumps(GBIF_MATCH).encode()
    if "occurrence/search" in url:
        return 200, json.dumps(GBIF_PAGE).encode()
    if "sensor.community" in url:
        return 200, json.dumps(SENSORS).encode()
    if "gdacs.org" in url:
        return 200, GDACS.encode("utf-8")
    if "flood-api" in url:
        return 200, json.dumps(FLOOD).encode()
    if "/v1/elevation" in url:
        return 200, json.dumps(ELEV).encode()
    raise net.HttpError(url, 404, "no stub")


net.fetch = fake_fetch

# land cover tiles ------------------------------------------------------------
check("worldcover: single tile", landcover.worldcover_tiles([71.4, 30.1, 71.6, 30.3]) == ["N30E069"])
check("worldcover: spans tiles", landcover.worldcover_tiles([71.9, 29.9, 72.1, 30.1])
      == ["N27E069", "N27E072", "N30E069", "N30E072"], landcover.worldcover_tiles([71.9, 29.9, 72.1, 30.1]))
check("worldcover: southern/western naming", landcover.worldcover_tiles([-46.5, -23.6, -46.4, -23.5]) == ["S24W048"],
      landcover.worldcover_tiles([-46.5, -23.6, -46.4, -23.5]))
try:
    landcover.dataset_hrefs("bogus", [0, 0, 1, 1])
    check("datasets: unknown refused", False)
except ValueError:
    check("datasets: unknown refused", True)
import numpy as np  # noqa: E402
from osgeo import gdal  # noqa: E402
import tempfile  # noqa: E402
tmp = tempfile.mkdtemp()
tif = os.path.join(tmp, "lc.tif")
drv = gdal.GetDriverByName("GTiff")
ds = drv.Create(tif, 10, 10, 1, gdal.GDT_Byte)
ds.SetGeoTransform((71.0, 0.01, 0, 30.1, 0, -0.01))
ds.SetProjection('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]')
arr = np.full((10, 10), 40, dtype="uint8"); arr[:5, :] = 10; arr[0, 0] = 0
ds.GetRasterBand(1).WriteArray(arr); ds.GetRasterBand(1).SetNoDataValue(0); ds = None
summary = landcover.class_summary(tif, landcover.WORLDCOVER_CLASSES)
check("land cover summary: nodata excluded, shares sum to 1",
      summary["sampled_pixels"] == 99 and abs(sum(r["fraction"] for r in summary["classes"]) - 1) < 1e-6
      and summary["classes"][0]["label"] == "Cropland", summary)
# 50 cropland pixels of 0.01 deg at 30 N: about 1.07 km2 each
check("land cover summary: area in km2 plausible", 45 < summary["classes"][0]["area_km2"] < 60,
      summary["classes"][0]["area_km2"])

# soil ----------------------------------------------------------------------------
result = soil.query(71.47, 30.2)
check("soil: values scaled by d_factor", {r["property"]: r["value"] for r in result["rows"]}["clay"] == 22.0)
check("soil: texture class derived", result["texture_class"] == "loam", result["texture_class"])
check("soil: texture sandy", soil.texture_class([
    {"property": "clay", "depth": "0-5cm", "value": 5}, {"property": "sand", "depth": "0-5cm", "value": 90},
    {"property": "silt", "depth": "0-5cm", "value": 5}]) == "sand")
try:
    soil.query(0, 0, ["gold"])
    check("soil: unknown property refused", False)
except ValueError:
    check("soil: unknown property refused", True)

# boundaries ----------------------------------------------------------------------
meta = boundaries.metadata("pak", "adm2")
check("boundaries: metadata parsed", meta["iso3"] == "PAK" and meta["canonical"] == "Districts")
feats = boundaries.fetch_geojson(meta["simplified_url"])
check("boundaries: properties normalised", feats[0]["properties"]["name"] == "Multan")
check("boundaries: within bbox filter", [f["properties"]["name"] for f in boundaries.within(feats, [71.4, 30.1, 71.6, 30.3])] == ["Multan"])
for bad in (("PAKISTAN", "ADM1"), ("PAK", "ADM9")):
    try:
        boundaries.metadata(*bad)
        check(f"boundaries: {bad} refused", False)
    except ValueError:
        check(f"boundaries: {bad} refused", True)

# species -----------------------------------------------------------------------
occ = species.occurrences([71.3, 30.1, 71.6, 30.3], "Aves", limit=100)
check("species: taxon matched and records without coordinates dropped",
      occ["taxon"]["key"] == 212 and len(occ["features"]) == 2)
check("species: counts", species.species_counts(occ["features"])[0] == {"species": "Passer domesticus", "records": 2})

# sensors -------------------------------------------------------------------------
air = sensors.air_sensors(74.3, 31.5, 25)
check("sensors: merged per station", air["summary"]["stations"] == 2)
st = {f["properties"]["location_id"]: f["properties"] for f in air["features"]}
check("sensors: values merged across sensors", st[1]["pm2_5"] == 22.1 and st[1]["temperature"] == 23.7
      and "SDS011" in st[1]["sensors"] and "BME280" in st[1]["sensors"], st[1])
check("sensors: pm2.5 summary excludes indoor stations", air["summary"]["pm2_5_max"] == 22.1, air["summary"])

# disasters -------------------------------------------------------------------------
al = disasters.alerts()
check("disasters: current alerts parsed, stale dropped", len(al["features"]) == 2
      and al["features"][0]["properties"]["alert_level"] == "orange", al)
check("disasters: bbox filter", len(disasters.alerts([71, 30, 72, 31])["features"]) == 1)
check("disasters: type and level filters", len(disasters.alerts(None, ["EQ"], ["green"])["features"]) == 1)

# weather extras ----------------------------------------------------------------------
rd = weather.river_discharge([(71.47, 30.2)])[0]
check("river discharge: forecast end and max", rd["latest_forecast"] == 130.0 and rd["max_in_window"] == 130.0)
check("elevation: two points", [r["elevation_m"] for r in weather.elevation([(71.47, 30.2), (74.3, 31.5)])] == [126.0, 210.5])

# wiring -------------------------------------------------------------------------------
server = QgisMCPServer(port=9961, iface=None, permissions=PermissionManager(MASTER))
handlers = server._handlers()
new = ["list_datasets", "add_dataset", "summarise_land_cover", "get_soil_properties",
       "attach_soil_to_layer", "get_river_discharge", "get_marine_conditions", "get_elevation",
       "add_admin_boundaries", "fetch_species_occurrences", "fetch_air_sensors",
       "fetch_disaster_alerts", "compute_index_change", "zonal_index_statistics",
       "add_imagery_series", "export_area_report"]
check("wiring: 16 toolbox commands registered", all(c in handlers for c in new),
      [c for c in new if c not in handlers])
check("wiring: categories and help", all(c in COMMAND_CATEGORIES and c in COMMAND_HELP for c in new))
check("wiring: registry commands exist", all(c in handlers for e in registry.REGISTRY.values() for c in e["commands"]),
      [c for e in registry.REGISTRY.values() for c in e["commands"] if c not in handlers])
check("wiring: total commands >= 96", len(handlers) >= 96, len(handlers))


def run(cmd, **p):
    return server.execute_command({"type": cmd, "params": p})


r = run("list_datasets")
check("cmd: list_datasets", r["status"] == "success" and len(r["result"]["datasets"]) == 20)
r = run("get_soil_properties", lat=30.2, lon=71.47)
check("cmd: soil", r["status"] == "success" and r["result"]["texture_class"] == "loam")
r = run("add_admin_boundaries", iso3="PAK", level="ADM2", aoi={"bbox": [71.4, 30.1, 71.6, 30.3]})
check("cmd: boundaries layer persisted", r["status"] == "success" and r["result"]["features"] == 1
      and QgsProject.instance().mapLayer(r["result"]["layer_id"]).providerType() == "ogr", r)
bl = r["result"]["layer_id"]
r = run("fetch_species_occurrences", aoi={"bbox": [71.4, 30.1, 71.6, 30.3]}, taxon="Aves")
check("cmd: species layer", r["status"] == "success" and r["result"]["returned"] == 2 and "layer_id" in r["result"])
r = run("fetch_air_sensors", lat=31.5, lon=74.3)
check("cmd: sensors layer", r["status"] == "success" and r["result"]["summary"]["stations"] == 2)
r = run("fetch_disaster_alerts", aoi={"bbox": [71, 30, 72, 31]})
check("cmd: alerts in area", r["status"] == "success" and r["result"]["count"] == 1)
r = run("get_river_discharge", aoi={"bbox": [71.4, 30.1, 71.6, 30.3]})
check("cmd: river discharge", r["status"] == "success" and r["result"]["latest_forecast"] == 130.0)
r = run("get_elevation", points=[[71.47, 30.2], [74.3, 31.5]])
check("cmd: elevation points", r["status"] == "success" and r["result"]["count"] == 2)
r = run("get_marine_conditions")
check("cmd: marine without location explains", r["status"] == "error" and "lat and lon" in r["message"])
r = run("zonal_index_statistics", layer_id="nope")
check("cmd: zonal on missing layer", r["status"] == "error")
r = run("add_admin_boundaries", iso3="PAK", level="ADM2", aoi={"bbox": [10, 10, 11, 11]})
check("cmd: boundaries outside area is a clear error", r["status"] == "error" and "intersect" in r["message"])

sys.exit(check.report())
