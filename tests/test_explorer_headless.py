"""Offscreen tests for the Data Explorer dock.

Builds the dock without a QGIS main window, drives it with stubbed network
responses through the real task manager, and grabs screenshots so a
reviewer can look at the panel. Also runs one real-network scenario when
``--online`` is passed.

    "C:/Program Files/QGIS 4.2.2/bin/python-qgis.bat" tests/test_explorer_headless.py [--online]
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap  # noqa: E402

app = _bootstrap.start_qgis()

from qgis.core import QgsApplication, QgsProject  # noqa: E402
from qgis.PyQt.QtCore import QDate  # noqa: E402

from qgis_mcp_plugin import net  # noqa: E402
from qgis_mcp_plugin.explorer import ExplorerDock, OSM_PRESETS  # noqa: E402

check = _bootstrap.Checker()
ONLINE = "--online" in sys.argv
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects")

STAC_ITEM = {
    "id": "S2A_42RYU_20261028_0_L2A", "collection": "sentinel-2-l2a",
    "bbox": [71.06, 29.70, 72.23, 30.71],
    "properties": {"datetime": "2026-10-28T06:00:51Z", "eo:cloud_cover": 0.2,
                   "platform": "sentinel-2a"},
    "assets": {"visual": {"href": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/x/TCI.tif"}},
    "links": [],
}
NOMINATIM = [{"osm_type": "node", "osm_id": 1, "lat": "30.1978", "lon": "71.4719",
              "boundingbox": ["30.03", "30.35", "71.31", "71.63"], "name": "Multan",
              "display_name": "Multan, Punjab, Pakistan", "category": "place",
              "type": "city", "importance": 0.5}]
METEO = {"latitude": 30.2, "longitude": 71.47, "timezone": "Asia/Karachi",
         "current_units": {"temperature_2m": "°C"},
         "current": {"time": "2026-10-31T00:00", "interval": 900,
                     "temperature_2m": 23.2, "weather_code": 0}}
OVERPASS = {"elements": [
    {"type": "node", "id": 1, "lat": 30.2, "lon": 71.47, "tags": {"amenity": "hospital", "name": "A"}},
    {"type": "node", "id": 2, "lat": 30.21, "lon": 71.48, "tags": {"amenity": "clinic", "name": "B"}},
]}
USGS = {"metadata": {}, "features": [
    {"id": "q", "geometry": {"type": "Point", "coordinates": [71.5, 30.2, 10]},
     "properties": {"mag": 5.1, "place": "near Multan", "time": 1}}]}


def fake_fetch(url, params=None, headers=None, method="GET", body=None, timeout_ms=0):
    if "nominatim" in url:
        return 200, json.dumps(NOMINATIM).encode()
    if url.endswith("/search"):
        return 200, json.dumps({"features": [STAC_ITEM], "context": {"matched": 1}}).encode()
    if "open-meteo" in url:
        return 200, json.dumps(METEO).encode()
    if "overpass" in url:
        return 200, json.dumps(OVERPASS).encode()
    if "earthquake" in url:
        return 200, json.dumps(USGS).encode()
    raise net.HttpError(url, 404, "no stub")


if not ONLINE:
    net.fetch = fake_fetch


def pump(seconds=30):
    """Run the event loop until the dock has no running task."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        if not dock._tasks and dock.progress.format() == "Idle":
            # one more spin so finished() callbacks have been delivered
            app.processEvents()
            return True
        time.sleep(0.05)
    return False


dock = ExplorerDock(iface=None)
dock.resize(520, 900)
dock.show()
app.processEvents()
check("dock builds with 8 tabs", dock.tabs.count() == 8, dock.tabs.count())
check("sources table populated", dock.sources_table.rowCount() >= 10)
check("basemap list populated", dock.basemap_list.count() >= 15)
check("osm presets populated", dock.osm_preset.count() == len(OSM_PRESETS))

# AOI from place -----------------------------------------------------------
def stubbed_scenario():
    global dock
    dock.place_edit.setText("Multan")
    dock.radius_spin.setValue(3)
    dock._aoi_from_place()
    check("place lookup finished", pump(60))
    check("aoi set from place", dock._aoi is not None and dock._aoi.source == "point"
          and "Multan" in (dock._aoi.label or ""), dock.aoi_label.text())
    check("aoi persisted to settings", "bbox" in dock._settings.value("qgis_mcp/explorer_aoi", ""))

    # AOI from layer -----------------------------------------------------------
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer  # noqa: E402
    lyr = QgsVectorLayer("Point?crs=EPSG:4326", "pts", "memory")
    f = QgsFeature()
    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(71.5, 30.2)))
    lyr.dataProvider().addFeatures([f])
    lyr.updateExtents()
    QgsProject.instance().addMapLayer(lyr)
    app.processEvents()
    dock.layer_combo.setLayer(lyr)
    dock._aoi_from_layer(False)
    check("aoi from single-point layer padded to 1 km", dock._aoi.source == "layer"
          and 0.015 < dock._aoi.rect.width() < 0.025, dock._aoi.rect.width())
    dock._aoi_from_layer(True)
    check("selection without features logs an error", "No features selected" in dock.log_view.toPlainText())

    # Imagery search -----------------------------------------------------------
    dock.place_edit.setText("Multan")
    dock._aoi_from_place()
    pump(60)
    dock._search_imagery()
    check("imagery search finished", pump(60))
    check("scene table filled", dock.scene_table.rowCount() == 1
          and dock.scene_table.item(0, 3).text() == STAC_ITEM["id"])
    check("first scene selected", dock._selected_scene() is not None)

    # Weather --------------------------------------------------------------------
    dock._get_weather()
    check("weather finished", pump(60))
    check("weather rendered as html", "23.2" in dock.weather_view.toPlainText()
          and "clear sky" in dock.weather_view.toPlainText(), dock.weather_view.toPlainText()[:200])

    # OSM --------------------------------------------------------------------------
    dock.osm_preset.setCurrentIndex(0)
    dock._fetch_osm()
    check("osm finished", pump(60))
    osm_layers = [l for l in QgsProject.instance().mapLayers().values() if "Hospitals" in l.name()]
    check("osm layer added and persisted to gpkg", len(osm_layers) == 1
          and osm_layers[0].providerType() == "ogr" and osm_layers[0].featureCount() == 2,
          [(l.name(), l.providerType()) for l in QgsProject.instance().mapLayers().values()])
    check("osm layer has provenance", osm_layers[0].customProperty("qgis_mcp/source") == "overpass")

    # Custom tag --------------------------------------------------------------------
    dock.osm_preset.setCurrentIndex(dock.osm_preset.count() - 1)
    check("custom preset enables key/value", dock.osm_key.isEnabled())
    dock._fetch_osm()
    check("missing key logs an error, no task", "Enter a tag key" in dock.log_view.toPlainText())

    # Hazards -------------------------------------------------------------------------
    dock.quake_in_aoi.setChecked(True)
    dock._fetch_quakes()
    check("quakes finished", pump(60))
    check("quake layer added", any("earthquakes" in l.name() for l in QgsProject.instance().mapLayers().values()))
    check("hazard summary shown", "M5.1" in dock.hazard_result.text(), dock.hazard_result.text())

    # Attach weather to the OSM layer ------------------------------------------------
    dock.weather_layer_combo.setLayer(osm_layers[0])
    dock._attach_weather()
    check("attach weather finished", pump(60))
    check("weather fields attached", osm_layers[0].fields().indexOf("wx_temperature_2m") >= 0,
          [f.name() for f in osm_layers[0].fields()])

    # Double-click guard and cancel ----------------------------------------------------
    first = dock._search_imagery()
    second = dock._search_imagery()
    check("duplicate task refused", second is None and "Already running" in dock.log_view.toPlainText())
    pump(60)

    # Basemaps ----------------------------------------------------------------------
    dock.basemap_list.setCurrentRow(0)
    dock._add_basemap()
    check("basemap added at bottom of tree",
          QgsProject.instance().layerTreeRoot().children()[-1].layer().providerType() == "wms")

    # Sources / keys ------------------------------------------------------------------
    rows = [r for r in range(dock.sources_table.rowCount())
            if dock.sources_table.item(r, 0).data(0x0100) == "nasa-firms"]
    dock.sources_table.selectRow(rows[0])
    dock.key_edit.setText("short")
    dock._save_key()
    check("short key refused", "8-128" in dock.log_view.toPlainText())
    dock.key_edit.setText("abcdefghijklmnop")
    dock._save_key()
    check("key saved and source ready", dock.sources_table.item(rows[0], 2).text() == "yes")

    # Screenshots -----------------------------------------------------------------------
    os.makedirs(SHOTS, exist_ok=True)
    for idx in range(dock.tabs.count()):
        dock.tabs.setCurrentIndex(idx)
        app.processEvents()
        name = dock.tabs.tabText(idx).lower().replace(" ", "_")
        dock.grab().save(os.path.join(SHOTS, f"explorer_{idx}_{name}.png"))
    check("screenshots written", os.path.exists(os.path.join(SHOTS, "explorer_0_imagery.png")))

    # Cancel / idle state ---------------------------------------------------------------
    check("progress idle after work", dock.progress.format() == "Idle" and not dock.cancel_button.isEnabled())


if not ONLINE:
    stubbed_scenario()
else:
    dock.place_edit.setText("Multan")
    dock.radius_spin.setValue(3)
    dock._aoi_from_place()
    check("online: place found", pump(60) and dock._aoi is not None, dock.aoi_label.text())
    dock._search_imagery()
    check("online: scenes found", pump(90) and dock.scene_table.rowCount() > 0)
    dock._get_weather()
    check("online: weather shown", pump(60) and "temperature" in dock.weather_view.toPlainText())
    dock.quake_in_aoi.setChecked(False)
    dock._fetch_quakes()
    check("online: quakes fetched", pump(60) and "earthquakes" in dock.hazard_result.text(),
          dock.hazard_result.text())
    dock.tabs.setCurrentIndex(0)
    dock._compute_index()
    check("online: index computed", pump(240))
    check("online: index result text", "mean" in dock.index_result.text(), dock.index_result.text())
    dock._add_dem()
    check("online: dem added", pump(240) and "DEM added" in dock.dem_result.text(), dock.dem_result.text())
    dock._derive_terrain()
    check("online: terrain derived", pump(240) and "derived" in dock.dem_result.text(), dock.dem_result.text())
    dock.grab().save(os.path.join(SHOTS, "explorer_online.png"))
    check("online: at least 4 layers in project", len(QgsProject.instance().mapLayers()) >= 4,
          [l.name() for l in QgsProject.instance().mapLayers().values()])

sys.exit(check.report())
