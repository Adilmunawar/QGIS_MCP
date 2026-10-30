"""Offline tests for the live-data layer.

Every network call is stubbed through ``qgis_mcp_plugin.net.fetch`` with
recorded responses, so this runs without internet and proves the connector
logic: AOI resolution, query building, response parsing, index maths,
layer creation, permissions and help coverage.

    "C:/Program Files/QGIS 4.2.2/bin/python-qgis.bat" tests/test_live_data_headless.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap  # noqa: E402

_bootstrap.start_qgis()

import numpy as np  # noqa: E402
from qgis.core import (  # noqa: E402
    QgsFeature, QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayer,
)

from qgis_mcp_plugin import aoi, net  # noqa: E402
from qgis_mcp_plugin.command_help import COMMAND_HELP  # noqa: E402
from qgis_mcp_plugin.connectors import (  # noqa: E402
    basemaps, geocode, hazards, indices, osm, registry, stac, weather,
)
from qgis_mcp_plugin.mcp_server import QgisMCPServer  # noqa: E402
from qgis_mcp_plugin.permissions import (  # noqa: E402
    COMMAND_CATEGORIES, MASTER, PermissionManager, READ_ONLY,
)

check = _bootstrap.Checker()

# ---------------------------------------------------------------------
# recorded responses
# ---------------------------------------------------------------------
NOMINATIM_MULTAN = [{
    "place_id": 1, "osm_type": "node", "osm_id": 307617596,
    "lat": "30.1978380", "lon": "71.4719683",
    "boundingbox": ["30.0378380", "30.3578380", "71.3119683", "71.6319683"],
    "name": "Multan", "display_name": "Multan, Punjab, Pakistan",
    "category": "place", "type": "city", "importance": 0.56,
    "address": {"city": "Multan", "country": "Pakistan"},
}]

STAC_ITEM = {
    "id": "S2A_42RYU_20261028_0_L2A", "collection": "sentinel-2-l2a",
    "bbox": [71.06, 29.70, 72.23, 30.71],
    "properties": {"datetime": "2026-10-28T06:00:51Z", "eo:cloud_cover": 0.2,
                   "platform": "sentinel-2a", "proj:epsg": 32642},
    "assets": {
        "red": {"href": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/x/B04.tif", "gsd": 10,
                "raster:bands": [{"scale": 0.0001, "offset": -0.1, "nodata": 0}]},
        "nir": {"href": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/x/B08.tif", "gsd": 10,
                "raster:bands": [{"scale": 0.0001, "offset": -0.1, "nodata": 0}]},
        "scl": {"href": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/x/SCL.tif", "gsd": 20},
        "visual": {"href": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/x/TCI.tif", "title": "True color image"},
        "thumbnail": {"href": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/x/thumb.jpg"},
        "red-jp2": {"href": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/x/B04.jp2"},
    },
    "links": [{"rel": "self", "href": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/x/item"}],
}

OPEN_METEO_CURRENT = {
    "latitude": 30.2, "longitude": 71.47, "elevation": 126.0,
    "timezone": "Asia/Karachi",
    "current_units": {"temperature_2m": "°C"},
    "current": {"time": "2026-10-31T00:00", "interval": 900,
                "temperature_2m": 23.2, "weather_code": 0},
}

USGS_FEED = {
    "metadata": {"generated": 1},
    "features": [
        {"id": "a", "geometry": {"type": "Point", "coordinates": [71.5, 30.2, 10]},
         "properties": {"mag": 5.1, "place": "near Multan", "time": 1, "tsunami": 0}},
        {"id": "b", "geometry": {"type": "Point", "coordinates": [10.0, 50.0, 5]},
         "properties": {"mag": 2.0, "place": "far away", "time": 2, "tsunami": 0}},
    ],
}

OVERPASS = {"elements": [
    {"type": "node", "id": 1, "lat": 30.2, "lon": 71.47,
     "tags": {"amenity": "hospital", "name": "Nishtar"}},
    {"type": "way", "id": 2, "center": {"lat": 30.21, "lon": 71.48},
     "tags": {"amenity": "hospital", "name": "Civil", "beds": "200"}},
    {"type": "way", "id": 3,
     "geometry": [{"lat": 30.0, "lon": 71.0}, {"lat": 30.0, "lon": 71.1},
                  {"lat": 30.1, "lon": 71.1}, {"lat": 30.0, "lon": 71.0}],
     "tags": {"building": "yes"}},
]}

CALLS = []


def fake_fetch(url, params=None, headers=None, method="GET", body=None,
               timeout_ms=0):
    CALLS.append((url, params, method, body))
    if "nominatim" in url and "/search" in url:
        return 200, json.dumps(NOMINATIM_MULTAN).encode()
    if "nominatim" in url and "/reverse" in url:
        return 200, json.dumps(NOMINATIM_MULTAN[0]).encode()
    if url.endswith("/search"):
        return 200, json.dumps({"features": [STAC_ITEM],
                                "context": {"matched": 1}}).encode()
    if "/items/" in url:
        return 200, json.dumps(STAC_ITEM).encode()
    if "open-meteo" in url and "forecast" in url:
        return 200, json.dumps(OPEN_METEO_CURRENT).encode()
    if "earthquake.usgs.gov" in url:
        return 200, json.dumps(USGS_FEED).encode()
    if "overpass" in url:
        return 200, json.dumps(OVERPASS).encode()
    if "firms" in url:
        return 200, b"latitude,longitude,frp,acq_date\n30.2,71.5,12.5,2026-10-30\n"
    raise net.HttpError(url, 404, "no stub")


net.fetch = fake_fetch

# ---------------------------------------------------------------------
# AOI
# ---------------------------------------------------------------------
a = aoi.resolve([71.4, 30.1, 71.6, 30.3])
check("aoi: bare bbox list", a.bbox == [71.4, 30.1, 71.6, 30.3] and a.source == "bbox")
a = aoi.resolve({"point": [71.47, 30.2], "radius_km": 10})
check("aoi: point radius ~0.09 deg lat", abs(a.rect.height() - 0.1797) < 0.01,
      a.rect.height())
a = aoi.resolve("POLYGON((71 30, 72 30, 72 31, 71 31, 71 30))")
check("aoi: bare WKT string", a.bbox == [71.0, 30.0, 72.0, 31.0])
a = aoi.resolve({"wkt": "POINT(699960 3400020)", "crs": "EPSG:32642"})
check("aoi: WKT reprojected from UTM", 71 < a.center[0] < 72 and 30 < a.center[1] < 31,
      a.center)
try:
    aoi.resolve({"bbox": [0, 0, 20, 20]})
    check("aoi: oversized bbox refused", False)
except ValueError as exc:
    check("aoi: oversized bbox refused", "limit" in str(exc))
try:
    aoi.resolve({"bbox": [2, 1, 1, 2]})
    check("aoi: inverted bbox refused", False)
except ValueError:
    check("aoi: inverted bbox refused", True)
try:
    aoi.resolve({"nonsense": 1})
    check("aoi: unknown spec refused", False)
except ValueError as exc:
    check("aoi: unknown spec refused", "must contain" in str(exc))
a = aoi.resolve({"place": "Multan"}, geocoder=geocode.bbox_for)
check("aoi: place via geocoder", a.source == "place" and "Multan" in a.label)
a = aoi.resolve({"place": "Multan", "radius_km": 2}, geocoder=geocode.bbox_for)
check("aoi: place + radius shrinks to point box", a.rect.width() < 0.1)

layer = QgsVectorLayer("Point?crs=EPSG:32642&field=n:integer", "utm_pts", "memory")
f = QgsFeature(layer.fields())
f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(699960, 3400020)))
f.setAttributes([1])
layer.dataProvider().addFeatures([f])
layer.updateExtents()
QgsProject.instance().addMapLayer(layer)
a = aoi.resolve({"layer_id": layer.id()})
check("aoi: layer extent reprojected to WGS84", 71 < a.center[0] < 72, a.center)
try:
    aoi.resolve({"layer_id": layer.id(), "selected": True})
    check("aoi: empty selection refused", False)
except ValueError:
    check("aoi: empty selection refused", True)

# ---------------------------------------------------------------------
# geocode
# ---------------------------------------------------------------------
geocode._cache.clear()
geocode._last_call = 0.0
rows = geocode.search("Multan")
check("geocode: parsed bbox west,south,east,north",
      rows[0]["bbox"] == [71.3119683, 30.037838, 71.6319683, 30.357838], rows[0]["bbox"])
before = len(CALLS)
geocode.search("multan ")
check("geocode: cache hit, no second request", len(CALLS) == before)
r = geocode.reverse(30.2, 71.47)
check("geocode: reverse returns display_name", "Multan" in r["display_name"])

# ---------------------------------------------------------------------
# STAC
# ---------------------------------------------------------------------
result = stac.search([71.4, 30.1, 71.6, 30.3], "sentinel-2-l2a", "2026-09-01",
                     "2026-10-30", max_cloud=20, limit=5)
url, params, method, body = CALLS[-1]
sent = json.loads(body)
check("stac: POST search with bbox, datetime, cloud query",
      method == "POST" and sent["bbox"] == [71.4, 30.1, 71.6, 30.3]
      and sent["datetime"].startswith("2026-09-01T00:00:00Z/2026-10-30")
      and sent["query"] == {"eo:cloud_cover": {"lt": 20.0}}, sent)
check("stac: sorted by cloud asc", sent["sortby"][0]["direction"] == "asc")
check("stac: summary drops -jp2 assets", "red-jp2" not in result["items"][0]["assets"]
      and "red" in result["items"][0]["assets"])
check("stac: matched count surfaced", result["matched"] == 1)
try:
    stac.search([0, 0, 1, 1], "sentinel-2-l2a", catalogue="nope")
    check("stac: unknown catalogue refused", False)
except ValueError:
    check("stac: unknown catalogue refused", True)
href, asset = stac.asset_href(STAC_ITEM, "red")
check("stac: asset href resolved", href.endswith("B04.tif"))
try:
    stac.asset_href(STAC_ITEM, "swir99")
    check("stac: missing asset lists available", False)
except LookupError as exc:
    check("stac: missing asset lists available", "visual" in str(exc))
check("stac: s3 href becomes https vsicurl",
      stac.gdal_path("s3://copernicus-dem-30m/a/b.tif")
      == "/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/a/b.tif")
check("stac: scale/offset read from raster:bands",
      stac.asset_scale_offset(STAC_ITEM["assets"]["red"]) == (0.0001, -0.1, 0))

# ---------------------------------------------------------------------
# indices (pure maths, no COG reads)
# ---------------------------------------------------------------------
expr, bands, name = indices.resolve_index("ndvi")
check("indices: case-insensitive lookup", name == "NDVI" and bands == ["red", "nir"])
expr, bands, name = indices.resolve_index(None, "(nir - red) / (nir + red + 0.16)",
                                          ["red", "nir", "blue"])
check("indices: custom expression bands", sorted(bands) == ["nir", "red"] and name is None)
for bad in ("__import__('os')", "red[0]", "nir + unknown_band", "open('x')",
            "red + 9**9**9**9", "red.real", "scl + 1", "sqrt", "(nir-red)/(nir+red) if 1 else 0"):
    try:
        indices.parse_expression(bad, ["red", "nir", "scl"])
        check(f"indices: rejects {bad!r}", False)
    except ValueError:
        check(f"indices: rejects {bad!r}", True)
for good in ("(nir - red) / (nir + red + 1e-3)", "sqrt(abs(nir - red)) ** 2",
             "where(nir > red, 1, 0)", "-red + 0.5 * nir"):
    try:
        indices.parse_expression(good, ["red", "nir"])
        check(f"indices: accepts {good!r}", True)
    except ValueError as exc:
        check(f"indices: accepts {good!r}", False, str(exc))
try:
    stac.check_href("file:///etc/passwd")
    check("stac: file href refused", False)
except ValueError:
    check("stac: file href refused", True)
try:
    stac.check_href("https://evil.example.org/x.tif")
    check("stac: unknown host refused", False)
except ValueError:
    check("stac: unknown host refused", True)
check("stac: known host accepted",
      stac.check_href("https://sentinel-cogs.s3.us-west-2.amazonaws.com/a.tif").endswith("a.tif"))
check("net: key redacted from error url",
      "***" in net.redact("https://firms.modaps.eosdis.nasa.gov/api/area/csv/SECRET123/VIIRS/1,2,3,4/1")
      and "SECRET123" not in net.redact("https://x/api/area/csv/SECRET123/VIIRS/1/1"))
err = net.HttpError("https://api.open-meteo.com/v1/forecast?key=ABC", 400, "bad",
                    '{"reason":"Latitude must be in range"}')
check("net: error carries body and hides key", "Latitude must be" in str(err) and "ABC" not in str(err))
from qgis_mcp_plugin import audit as audit_mod  # noqa: E402
check("audit: secrets redacted",
      audit_mod._redact({"source_id": "x", "key": "SECRET", "nested": {"token": "T"}})
      == {"source_id": "x", "key": "***", "nested": {"token": "***"}})
arr = np.array([[0.1, 0.5, np.nan, 0.9, -0.2]], dtype="float32")
classes = indices.classify(arr, [0.2, 0.6])
check("indices: classify shares sum to 1",
      abs(sum(c["fraction"] for c in classes) - 1.0) < 1e-6 and classes[0]["pixels"] == 2,
      classes)

# Drive compute() through a stubbed read_window to prove the maths and masking.
def fake_read_window(href, bbox, resolution=None, resample="bilinear", src_nodata=None):
    if "B04" in href:
        raw = np.array([[2000, 1000, 0], [3000, 1000, 1500]], dtype="uint16")
    elif "B08" in href:
        raw = np.array([[6000, 1000, 0], [1000, 1000, 1500]], dtype="uint16")
    else:  # SCL: 4 = vegetation, 9 = cloud high prob
        raw = np.array([[4, 4, 4], [9, 4, 4]], dtype="uint8")
    return raw, (0, 10, 0, 0, 0, -10), "EPSG:32642", (0 if "SCL" not in href else None), 10


indices.read_window = fake_read_window
out = indices.compute(STAC_ITEM, [71.4, 30.1, 71.6, 30.3], "NDVI")
ndvi = out["array"]
# pixel (0,0): red=(2000*1e-4-0.1)=0.1, nir=0.5 -> (0.5-0.1)/(0.6)=0.667
check("indices: NDVI value correct with scale/offset", abs(ndvi[0, 0] - 0.6667) < 1e-3,
      ndvi[0, 0])
check("indices: nodata pixel is NaN", np.isnan(ndvi[0, 2]))
check("indices: cloud pixel masked", np.isnan(ndvi[1, 0]))
check("indices: zero-sum pixel (both 0 reflectance) is NaN", np.isnan(ndvi[0, 1]))
# valid: (0,0)=0.667 and (1,2)=0.0; the rest are nodata, cloud, or zero-sum.
check("indices: stats computed over valid only", out["statistics"]["valid_pixels"] == 2,
      out["statistics"])
check("indices: cloud fraction reported",
      abs(out["statistics"]["cloud_masked_fraction"] - 1 / 6) < 1e-6)
out2 = indices.compute(STAC_ITEM, [71.4, 30.1, 71.6, 30.3], mask_clouds=False,
                       expression="nir - red")
check("indices: custom expression unmasked", out2["statistics"]["valid_pixels"] == 5
      and abs(out2["array"][0, 0] - 0.4) < 1e-6)

import tempfile  # noqa: E402
tif = os.path.join(tempfile.mkdtemp(), "ndvi.tif")
indices.write_geotiff(tif, ndvi, out["transform"], out["projection"])
from osgeo import gdal  # noqa: E402
ds = gdal.Open(tif)
check("indices: GeoTIFF written with nodata", ds.GetRasterBand(1).GetNoDataValue() == -9999.0)
ds = None

# ---------------------------------------------------------------------
# weather
# ---------------------------------------------------------------------
rows = weather.current([(71.47, 30.2)])
check("weather: current parsed with WMO description",
      rows[0]["current"]["weather_description"] == "clear sky")
url, params, _, _ = CALLS[-1]
check("weather: lat/lon formatted", params["latitude"] == "30.20000")
try:
    weather._points_param([(0, 0)] * 101)
    check("weather: >100 points refused", False)
except ValueError:
    check("weather: >100 points refused", True)
for bad in (("2026-13-01", "2026-12-01"), ("2026-02-01", "2026-01-01"),
            ("2000-01-01", "2026-01-01")):
    try:
        weather._check_dates(*bad)
        check(f"weather: bad dates {bad} refused", False)
    except ValueError:
        check(f"weather: bad dates {bad} refused", True)
check("weather: summarise_daily",
      weather.summarise_daily([{"x": 1}, {"x": 3}, {"x": None}], "x")["mean"] == 2.0)
check("weather: columns to rows",
      weather._columns_to_rows({"time": ["a", "b"], "t": [1, 2]})[1] == {"time": "b", "t": 2})

# ---------------------------------------------------------------------
# OSM / Overpass
# ---------------------------------------------------------------------
q = osm.build_query([71.4, 30.1, 71.6, 30.3], {"amenity": "hospital"})
check("osm: query has bbox south,west,north,east",
      '["amenity"="hospital"](30.100000,71.400000,30.300000,71.600000)' in q, q)
q = osm.build_query([0, 0, 1, 1], {"highway": ["primary", "secondary"], "name": "*"})
check("osm: list becomes regex, * becomes presence",
      '["highway"~"^(primary|secondary)$"]["name"]' in q, q)
try:
    osm.build_query([0, 0, 1, 1], {"bad key!": "x"})
    check("osm: invalid key refused", False)
except ValueError:
    check("osm: invalid key refused", True)
feats = [osm.to_feature(e, "full") for e in OVERPASS["elements"]]
check("osm: node -> Point", feats[0]["geometry"]["type"] == "Point")
check("osm: way with center -> Point", feats[1]["geometry"]["type"] == "Point")
check("osm: closed building way -> Polygon", feats[2]["geometry"]["type"] == "Polygon")
check("osm: tags become properties with osm_id", feats[1]["properties"]["beds"] == "200"
      and feats[1]["properties"]["osm_id"] == 2)

# ---------------------------------------------------------------------
# hazards
# ---------------------------------------------------------------------
r = hazards.earthquakes("day", 4.5, [71, 30, 72, 31])
check("hazards: level 4.5 feed chosen", "4.5_day" in r["source"])
check("hazards: bbox + magnitude filter", len(r["features"]) == 1
      and r["features"][0]["properties"]["depth_km"] == 10)
try:
    hazards.active_fires("", [0, 0, 1, 1])
    check("hazards: FIRMS without key explains how to get one", False)
except PermissionError as exc:
    check("hazards: FIRMS without key explains how to get one", "map_key" in str(exc))
r = hazards.active_fires("KEY", [71, 30, 72, 31], days=3)
check("hazards: FIRMS CSV parsed", r["features"][0]["properties"]["frp"] == 12.5)

# ---------------------------------------------------------------------
# basemaps
# ---------------------------------------------------------------------
uri, name, attribution = basemaps.basemap_uri("osm")
check("basemaps: xyz uri", uri.startswith("type=xyz&url=https://tile.openstreetmap.org")
      and "zmax=19" in uri, uri)
uri, name, _ = basemaps.basemap_uri("viirs-snpp-truecolor", "2026-10-01")
check("basemaps: GIBS dated url", "2026-10-01" in uri and "Level9" in uri and "2026-10-01" in name)
try:
    basemaps.basemap_uri("viirs-snpp-truecolor", "yesterday")
    check("basemaps: bad date refused", False)
except ValueError:
    check("basemaps: bad date refused", True)

# ---------------------------------------------------------------------
# registry, permissions, help
# ---------------------------------------------------------------------
rows = registry.describe_sources()
check("registry: sources listed", len(rows) >= 10)
check("registry: keyless sources ready", all(r["ready"] for r in rows if r["auth"] == "none"))
check("registry: keyed sources not ready without settings",
      not any(r["ready"] for r in rows if r["auth"] != "none"))

server = QgisMCPServer(port=9960, iface=None, permissions=PermissionManager(MASTER))
handlers = server._handlers()
live = [c for c in handlers if c in (
    "list_data_sources", "resolve_aoi", "set_data_source_key", "geocode",
    "reverse_geocode", "add_place_marker", "search_imagery", "add_imagery",
    "compute_spectral_index", "get_index_time_series", "add_dem", "derive_terrain",
    "get_weather", "get_weather_history", "get_climate_series",
    "attach_weather_to_layer", "list_basemaps", "add_basemap",
    "fetch_osm_features", "fetch_earthquakes", "fetch_active_fires")]
check("wiring: all 21 live commands registered", len(live) == 21, len(live))
check("wiring: every live command has a category",
      all(c in COMMAND_CATEGORIES for c in live),
      [c for c in live if c not in COMMAND_CATEGORIES])
check("wiring: every live command has help", all(c in COMMAND_HELP for c in live),
      [c for c in live if c not in COMMAND_HELP])
check("wiring: every registry command exists as a handler",
      all(c in handlers for e in registry.REGISTRY.values() for c in e["commands"]),
      [c for e in registry.REGISTRY.values() for c in e["commands"] if c not in handlers])
ro = PermissionManager(READ_ONLY)
check("permissions: search is read, fetch is write",
      ro.evaluate("search_imagery")[0] == "allow" and ro.evaluate("add_imagery")[0] == "deny")

# ---------------------------------------------------------------------
# commands end to end (stubbed network)
# ---------------------------------------------------------------------
def run(cmd, **params):
    return server.execute_command({"type": cmd, "params": params})


r = run("list_data_sources", category="weather")
check("cmd: list_data_sources filters", r["status"] == "success"
      and all(s["category"] == "weather" for s in r["result"]["sources"]))
r = run("list_data_sources", category="bogus")
check("cmd: unknown category is an error", r["status"] == "error")
r = run("geocode", query="Multan")
check("cmd: geocode", r["status"] == "success" and r["result"]["count"] == 1)
r = run("search_imagery", aoi={"place": "Multan"})
check("cmd: search_imagery with place aoi", r["status"] == "success"
      and r["result"]["items"][0]["id"] == STAC_ITEM["id"] and r["result"]["aoi"]["source"] == "place")
r = run("get_weather", lat=30.2, lon=71.47)
check("cmd: get_weather current", r["status"] == "success"
      and r["result"]["current"]["temperature_2m"] == 23.2)
r = run("get_weather", lat=30.2, lon=71.47, kind="bogus")
check("cmd: bad weather kind is an error", r["status"] == "error")
r = run("fetch_earthquakes", period="day", min_magnitude=4.5, aoi={"bbox": [71, 30, 72, 31]})
check("cmd: fetch_earthquakes adds a layer", r["status"] == "success"
      and r["result"]["count"] == 1 and "layer_id" in r["result"])
lyr = QgsProject.instance().mapLayer(r["result"]["layer_id"])
check("cmd: quake layer has provenance", lyr.customProperty("qgis_mcp/source") == "usgs-earthquakes")
check("cmd: quake layer typed fields", lyr.fields().field("magnitude").typeName() in ("double", "Real"),
      lyr.fields().field("magnitude").typeName())
r = run("fetch_osm_features", aoi={"bbox": [71.4, 30.1, 71.6, 30.3]},
        tags={"amenity": "hospital"}, geometry="full")
check("cmd: fetch_osm_features splits by geometry", r["status"] == "success"
      and sorted(l["geometry"] for l in r["result"]["layers"]) == ["Point", "Polygon"], r)
check("cmd: osm result reports truncation and endpoint",
      r["result"]["truncated"] is False and "overpass" in r["result"]["endpoint"])
r = run("fetch_osm_features", aoi={"bbox": [71.4, 30.1, 71.6, 30.3]}, tags="hospital")
check("cmd: string tags refused with hint", r["status"] == "error" and "mapping" in r["message"])
r = run("compute_spectral_index", index="FOO")
check("cmd: missing aoi lists accepted parameters", r["status"] == "error"
      and "Accepted parameters" in r["message"], r)
r = run("get_layer_features", layer_id=lyr.id(), bogus=1)
check("cmd: unknown parameter is InvalidParameters", r["error_type"] == "InvalidParameters")
r = run("add_basemap", basemap="osm")
check("cmd: add_basemap", r["status"] == "success")
root = QgsProject.instance().layerTreeRoot()
check("cmd: basemap moved to bottom of layer tree",
      root.children()[-1].layerId() == r["result"]["layer_id"])
r = run("attach_weather_to_layer", layer_id=lyr.id())
check("cmd: attach_weather_to_layer adds fields", r["status"] == "success"
      and "wx_temperature_2m" in r["result"]["fields"]
      and lyr.fields().indexOf("wx_temperature_2m") >= 0)
r = run("set_data_source_key", source_id="nasa-firms", key="K")
check("cmd: too-short key refused", r["status"] == "error")
r = run("set_data_source_key", source_id="nasa-firms", key="abcdefgh12345678")
check("cmd: set key", r["status"] == "success")
r = run("fetch_active_fires", aoi={"bbox": [71, 30, 72, 31]})
check("cmd: fetch_active_fires with stored key", r["status"] == "success"
      and r["result"]["count"] == 1, r)
r = run("set_data_source_key", source_id="open-meteo", key="K")
check("cmd: keyless source refuses a key", r["status"] == "error")
r = run("resolve_aoi", aoi={"canvas": True})
check("cmd: canvas aoi headless is an error, not a crash", r["status"] == "error")
r = run("derive_terrain", layer_id=lyr.id())
check("cmd: derive_terrain on vector refused", r["status"] == "error")

sys.exit(check.report())
