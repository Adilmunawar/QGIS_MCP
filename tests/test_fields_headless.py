# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""Offline tests for field analytics: subset selection, zonal metrics, status
ranking, styling, the result layer, reports and the chart helper.

    "C:/Program Files/QGIS 4.2.2/bin/python-qgis.bat" tests/test_fields_headless.py
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
from qgis.core import QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer  # noqa: E402

from qgis_mcp_plugin import net  # noqa: E402
from qgis_mcp_plugin.command_help import COMMAND_HELP  # noqa: E402
from qgis_mcp_plugin.connectors import registry  # noqa: E402
from qgis_mcp_plugin.mcp_server import QgisMCPServer  # noqa: E402
from qgis_mcp_plugin.permissions import COMMAND_CATEGORIES, MASTER, PermissionManager  # noqa: E402

check = _bootstrap.Checker()
net.fetch = lambda *a, **k: (_ for _ in ()).throw(net.HttpError(a[0], 404, "offline"))

server = QgisMCPServer(port=9970, iface=None, permissions=PermissionManager(MASTER))
handlers = server._handlers()
new = ["analyse_fields", "field_time_series", "export_field_report"]
check("wiring: field commands registered", all(c in handlers for c in new) and len(handlers) == 121, len(handlers))
check("wiring: categories and help", all(c in COMMAND_CATEGORIES and c in COMMAND_HELP for c in new))
check("wiring: registry commands exist", all(c in handlers for e in registry.REGISTRY.values() for c in e["commands"]))

# a parcel layer in a projected CRS with name candidates like the user's data --------------
layer = QgsVectorLayer("Polygon?crs=EPSG:32642&field=parcel_no:integer&field=mouza_name:string&field=acres:double",
                       "parcels", "memory")
provider = layer.dataProvider()
# EPSG:32642 (UTM 42N) squares near 71.5E 30.2N; each about 200 m
import math  # noqa: E402
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY  # noqa: E402
to_utm = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), QgsCoordinateReferenceSystem("EPSG:32642"), QgsProject.instance())
origin = to_utm.transform(QgsPointXY(71.5, 30.2))
feats = []
for i in range(5):
    x0 = origin.x() + i * 250
    y0 = origin.y()
    f = QgsFeature(layer.fields())
    f.setGeometry(QgsGeometry.fromWkt(f"POLYGON(({x0} {y0},{x0 + 200} {y0},{x0 + 200} {y0 + 200},{x0} {y0 + 200},{x0} {y0}))"))
    f.setAttributes([i + 1, "Jalal Abad", 9.88])
    feats.append(f)
provider.addFeatures(feats)
layer.updateExtents()
QgsProject.instance().addMapLayer(layer)

r = server.execute_command({"type": "analyse_fields", "params": {"layer_id": layer.id(), "selected": True}})
check("subset: selection required when asked", r["status"] == "error" and "selected" in r["message"], r)
subset = server.fields_subset(layer.id(), None, None, 300)
check("subset: first features, WGS84 wkt, acres, labels", subset["how"] == "first" and len(subset["fields"]) == 5
      and subset["fields"][0]["wkt"].startswith("Polygon") and 9.5 < subset["fields"][0]["acres"] < 10.3
      and subset["fields"][0]["label"] == "Jalal Abad / 1" and subset["name_fields"] == ["mouza_name", "parcel_no"], subset["fields"][0])
subset_cap = server.fields_subset(layer.id(), None, None, 2)
check("subset: cap and truncation reported", len(subset_cap["fields"]) == 2 and subset_cap["truncated"] and subset_cap["candidates"] == 5)
layer.selectByIds([2, 3])
subset_sel = server.fields_subset(layer.id())
check("subset: selection used automatically", subset_sel["how"] == "selection" and len(subset_sel["fields"]) == 2)
layer.removeSelection()
first_bbox = QgsGeometry.fromWkt(subset["fields"][0]["wkt"]).boundingBox()
subset_aoi = server.fields_subset(layer.id(), None, {"bbox": [first_bbox.xMinimum() - 0.0005, first_bbox.yMinimum() - 0.0005,
                                                              first_bbox.xMaximum() + 0.0005, first_bbox.yMaximum() + 0.0005]}, 300)
check("subset: area filter keeps the fields near the box (the area is padded to 1 km)", subset_aoi["how"] == "area" and 1 <= len(subset_aoi["fields"]) < 5, len(subset_aoi["fields"]))
line = QgsVectorLayer("LineString?crs=EPSG:4326", "line", "memory")
QgsProject.instance().addMapLayer(line)
r = server.execute_command({"type": "analyse_fields", "params": {"layer_id": line.id()}})
check("subset: line layer refused", r["status"] == "error" and "polygon" in r["message"])

twin = QgsVectorLayer("Polygon?crs=EPSG:4326&field=parcel_no:integer", "twins", "memory")
for number in (1, 2, 3):
    f = QgsFeature(twin.fields())
    x = 71.5 if number < 3 else 71.51
    f.setGeometry(QgsGeometry.fromWkt(f"POLYGON(({x} 30.2,{x + 0.001} 30.2,{x + 0.001} 30.201,{x} 30.201,{x} 30.2))"))
    f.setAttributes([number])
    twin.dataProvider().addFeatures([f])
QgsProject.instance().addMapLayer(twin)
twins = server.fields_subset(twin.id())
check("subset: identical polygons counted once", len(twins["fields"]) == 2 and twins["duplicates_skipped"] == 1
      and not twins["truncated"], (len(twins["fields"]), twins["duplicates_skipped"], twins["truncated"]))

# synthetic index rasters over the subset bbox -----------------------------------------------
tmp = tempfile.mkdtemp()
w, s, e, n = subset["bbox"]
cols, rows = 60, 12
gt = (w, (e - w) / cols, 0, n, 0, -(n - s) / rows)
wgs = 'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'


def write(path, values_per_field):
    ds = gdal.GetDriverByName("GTiff").Create(path, cols, rows, 1, gdal.GDT_Float32)
    ds.SetGeoTransform(gt)
    ds.SetProjection(wgs)
    arr = np.full((rows, cols), -9999.0, dtype="float32")
    for i, v in enumerate(values_per_field):
        # each field spans roughly cols/5 columns; fill the matching band of pixels
        c0 = int(cols * i / 5)
        c1 = int(cols * (i + 1) / 5)
        if v is not None:
            arr[:, c0:c1] = v
    ds.GetRasterBand(1).WriteArray(arr)
    ds.GetRasterBand(1).SetNoDataValue(-9999.0)
    ds = None
    return path


now = write(os.path.join(tmp, "now.tif"), [0.70, 0.25, 0.10, 0.50, None])
prev = write(os.path.join(tmp, "prev.tif"), [0.60, 0.45, 0.55, 0.52, 0.4])
fetched = {"subset": subset, "index": "NDVI",
           "now": {"item": "S2_NOW", "datetime": "2026-10-28T06:00:00Z", "cloud_cover": 1.0, "path": now, "mean": 0.39, "resolution_m": 10},
           "prev": {"item": "S2_PREV", "datetime": "2026-09-28T06:00:00Z", "cloud_cover": 2.0, "path": prev, "mean": 0.5},
           "weather": {"history": {"days": 30, "rain_mm": 12.5, "et0_mm": 110.0, "tmax_mean": 33.1, "rows": []},
                       "forecast": {"rain_mm": 0.0, "tmax_max": 34.0, "rows": []}},
           "errors": {}}
result = server.fields_apply(fetched)
status = {r["label"]: r["status"] for r in result["rows"]}
check("apply: statuses from vigour and change", status["Jalal Abad / 1"] == "greening" and status["Jalal Abad / 2"] == "declining"
      and status["Jalal Abad / 3"] == "bare or harvested" and status["Jalal Abad / 4"] == "healthy"
      and status["Jalal Abad / 5"].startswith("no data"), status)
check("apply: attention ranked bare first", [r["label"] for r in result["attention"]][:2] == ["Jalal Abad / 3", "Jalal Abad / 2"], result["attention"])
check("apply: change computed", abs(result["rows"][1]["change"] - (-0.2)) < 0.02, result["rows"][1])
check("apply: by status acres", abs(result["by_status"]["declining"]["acres"] - 9.88) < 0.1 and result["by_status"]["declining"]["fields"] == 1, result["by_status"])
out_layer = QgsProject.instance().mapLayer(result["layer_id"])
check("apply: result layer persisted to GeoPackage with source attributes", out_layer is not None
      and out_layer.providerType() == "ogr" and out_layer.fields().indexOf("src_mouza_name") >= 0
      and out_layer.fields().indexOf("ndvi_now_mean") >= 0 and out_layer.fields().indexOf("status") >= 0, [f.name() for f in out_layer.fields()] if out_layer else None)
check("apply: graduated style by vigour", out_layer.renderer().type() == "graduatedSymbol" and out_layer.renderer().classAttribute() == "ndvi_now_mean")
check("apply: source layer untouched", layer.fields().indexOf("ndvi_now_mean") < 0 and layer.featureCount() == 5)
check("apply: weather summarised without rows", result["weather"]["history"]["rain_mm"] == 12.5 and "rows" not in result["weather"]["history"])
check("apply: mean now and change", result["mean_now"] is not None and result["mean_change"] is not None and result["mean_change"] < 0, (result["mean_now"], result["mean_change"]))

# report --------------------------------------------------------------------------------------
pdf = os.path.join(tmp, "fields.pdf")
written = server.write_field_report(result, pdf, "Jalal Abad blocks")
check("report: pdf and html written", os.path.exists(pdf) and os.path.getsize(pdf) > 1000 and os.path.exists(written["html_path"])
      and written["attention"] == 2, written)
html_text = open(written["html_path"], encoding="utf-8").read()
check("report: ranked table and charts in html", "Fields needing attention" in html_text and "<img" in html_text and "declining" in html_text)
r = server.execute_command({"type": "export_field_report", "params": {"layer_id": layer.id(), "path": "x.txt"}})
check("report: extension checked", r["status"] == "error")

# time series guards ----------------------------------------------------------------------------
r = server.execute_command({"type": "field_time_series", "params": {"layer_id": layer.id()}})
check("series: needs exactly one selected field", r["status"] == "error" and "exactly one" in r["message"])
r = server.execute_command({"type": "field_time_series", "params": {"layer_id": layer.id(), "feature_id": 99}})
check("series: missing feature", r["status"] == "error" and "not found" in r["message"])
r = server.execute_command({"type": "field_time_series", "params": {"layer_id": layer.id(), "feature_id": 1}})
check("series: offline network reported cleanly", r["status"] == "error", r)

sys.exit(check.report())
