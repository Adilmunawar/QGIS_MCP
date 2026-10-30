"""Build the live-data test project with real network calls.

Creates ``tests/projects/live_data_test.qgz`` (ignored by git) holding one
layer from every live-data connector over Multan, Pakistan, then renders it
to ``live_data_test.png`` so a reviewer can look at the result.

    "C:/Program Files/QGIS 4.2.2/bin/python-qgis.bat" tests/projects/build_live_data_project.py [--aoi "Place name"]
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _bootstrap  # noqa: E402

_bootstrap.start_qgis()

from qgis.core import QgsProject  # noqa: E402

from qgis_mcp_plugin.mcp_server import QgisMCPServer  # noqa: E402
from qgis_mcp_plugin.permissions import MASTER, PermissionManager  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
check = _bootstrap.Checker()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--aoi", default="Multan")
    parser.add_argument("--radius-km", type=float, default=3.0)
    parser.add_argument("--out", default=os.path.join(HERE, "live_data_test.qgz"))
    args = parser.parse_args()

    server = QgisMCPServer(port=9970, iface=None,
                           permissions=PermissionManager(MASTER))
    aoi = {"place": args.aoi, "radius_km": args.radius_km}
    log = []

    def run(cmd, **params):
        started = time.time()
        response = server.execute_command({"type": cmd, "params": params})
        elapsed = round(time.time() - started, 1)
        ok = response["status"] == "success"
        log.append({"command": cmd, "ok": ok, "seconds": elapsed,
                    "message": response.get("message")})
        check(f"{cmd} ({elapsed}s)", ok, response.get("message"))
        return response.get("result") if ok else None

    run("create_new_project", path=args.out)
    run("add_basemap", basemap="osm")
    run("add_basemap", basemap="viirs-snpp-truecolor", opacity=0.6)
    place = run("add_place_marker", query=args.aoi)
    scenes = run("search_imagery", aoi=aoi, limit=3)
    if scenes and scenes["items"]:
        item = scenes["items"][0]["id"]
        run("add_imagery", item_id=item)
        ndvi = run("compute_spectral_index", aoi=aoi, item_id=item,
                   breaks=[0.2, 0.4, 0.6])
        run("compute_spectral_index", aoi=aoi, item_id=item, index="NDWI")
        if ndvi:
            check("NDVI mean in plausible range",
                  ndvi["statistics"]["mean"] is not None
                  and -0.2 < ndvi["statistics"]["mean"] < 0.9,
                  ndvi["statistics"])
    dem = run("add_dem", aoi=aoi)
    if dem:
        run("derive_terrain", layer_id=dem["layer_id"], product="hillshade")
        run("derive_terrain", layer_id=dem["layer_id"], product="slope")
    hospitals = run("fetch_osm_features", aoi=aoi, tags={"amenity": "hospital"})
    if hospitals and hospitals["layers"]:
        run("attach_weather_to_layer", layer_id=hospitals["layers"][0]["layer_id"])
    run("fetch_osm_features", aoi=aoi, tags={"highway": ["primary", "trunk"]},
        geometry="full", limit=2000)
    run("fetch_earthquakes", period="month", min_magnitude=4.5)
    wx = run("get_weather", aoi=aoi)
    if wx:
        check("weather has a temperature", wx["current"].get("temperature_2m") is not None)
    run("get_weather", aoi=aoi, kind="forecast", days=3)
    run("get_climate_series", aoi=aoi, start_date="2026-09-01", end_date="2026-09-30")
    series = run("get_index_time_series", aoi={"place": args.aoi, "radius_km": 1},
                 start_date="2026-08-01", end_date="2026-10-31", max_scenes=4)
    if series:
        check("time series has at least two scenes",
              sum(1 for r in series["series"] if "mean" in r) >= 2, series["series"])

    layers = run("get_layers")
    check("project has at least 10 layers", layers is not None and len(layers) >= 10,
          len(layers or []))
    for layer in QgsProject.instance().mapLayers().values():
        if not layer.isValid():
            check(f"layer valid: {layer.name()}", False, layer.source()[:80])
    run("save_project")
    png = os.path.splitext(args.out)[0] + ".png"
    run("render_map", path=png, width=1200, height=900)
    with open(os.path.splitext(args.out)[0] + "_log.json", "w", encoding="utf-8") as fh:
        json.dump(log, fh, indent=2)
    print(f"\nProject: {args.out}\nRender:  {png}")
    return check.report()


if __name__ == "__main__":
    sys.exit(main())
