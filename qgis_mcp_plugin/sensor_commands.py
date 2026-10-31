# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""Sensor network, population and 3D Tiles commands: USGS river gauges, NOAA
buoys and tide stations, OpenSky aircraft, OGC SensorThings servers through
the native QGIS provider, WorldPop population estimates, and Cesium 3D Tiles
(Google Photorealistic with a key, or any tileset URL).

Mixed into :class:`QgisMCPServer`; the Data Explorer uses the same methods.
"""

from qgis.core import QgsProject, QgsVectorLayer

from .connectors import aircraft, gauges, population, sensorthings
from .live_commands import _memory_layer_from_features, _safe_name

GOOGLE_3D_TILES = "https://tile.googleapis.com/v1/3dtiles/root.json"
TILES_PRESETS = {
    "google": {"name": "Google Photorealistic 3D Tiles", "key_setting": "qgis_mcp/keys/google_maps",
               "attribution": "Google, data providers shown in the view",
               "licence": "Google Maps Platform terms; attribution required"},
    "cesium-sample": {"name": "Cesium 3D Tiles 1.1 sample tileset",
                      "url": "https://raw.githubusercontent.com/CesiumGS/3d-tiles-samples/main/1.1/TilesetWithFullMetadata/tileset.json",
                      "attribution": "Cesium GS sample data", "licence": "Apache 2.0"},
}


class SensorsMixin:
    """Gauges, buoys, tides, aircraft, SensorThings, population, 3D Tiles."""

    def _points_layer(self, name, result, source_id, query, response):
        features = result["features"]
        response.update({"summary": result["summary"], "source": result["source"]})
        if features:
            layer, count = _memory_layer_from_features(name, features)
            layer = self._add_layer(layer, source_id, query)
            response["layer_id"] = layer.id()
            response["features"] = count
            if self.iface is not None:
                self.iface.mapCanvas().refresh()
        else:
            response["features"] = 0
        return response

    # ------------------------------------------------------------------
    # water and marine gauges
    # ------------------------------------------------------------------
    def fetch_river_gauges(self, aoi, name=None):
        """USGS stream gauges with current discharge and stage (United States)."""
        area = self._resolve_aoi(aoi)
        result = gauges.usgs_gauges(area.bbox)
        response = {"aoi": area.to_dict(),
                    "gauges": sorted((f["properties"] for f in result["features"]),
                                     key=lambda p: p.get("discharge_m3s") or 0, reverse=True)[:20]}
        if not result["features"]:
            response["hint"] = "No active USGS gauges here; the network covers the United States only."
        return self._points_layer(name or "river_gauges", result, "usgs-nwis",
                                  {"bbox": area.bbox}, response)

    def fetch_buoys(self, aoi=None, name=None):
        """NOAA NDBC buoys and coastal stations with their latest observation."""
        bbox = self._resolve_aoi(aoi).bbox if aoi else None
        result = gauges.ndbc_buoys(bbox)
        response = {"bbox": bbox, "buoys": [f["properties"] for f in result["features"]][:20]}
        if not result["features"]:
            response["hint"] = "No buoys report inside that box; widen it towards the coast or open sea."
        return self._points_layer(name or "ndbc_buoys", result, "ndbc", {"bbox": bbox}, response)

    def fetch_tide_stations(self, aoi, name=None, max_latest=20):
        """NOAA tide stations with the latest water level (United States coasts)."""
        area = self._resolve_aoi(aoi)
        result = gauges.noaa_tide_stations(area.bbox, max(0, min(int(max_latest), 50)))
        response = {"aoi": area.to_dict(), "stations": [f["properties"] for f in result["features"]][:20]}
        if not result["features"]:
            response["hint"] = "No NOAA tide stations in the area; they line the coasts of the United States."
        return self._points_layer(name or "tide_stations", result, "noaa-coops",
                                  {"bbox": area.bbox}, response)

    # ------------------------------------------------------------------
    # aircraft
    # ------------------------------------------------------------------
    def fetch_aircraft(self, aoi, name=None, max_aircraft=500):
        """Aircraft positions right now from the OpenSky Network."""
        area = self._resolve_aoi(aoi)
        result = aircraft.opensky_states(area.bbox, max_aircraft)
        response = {"aoi": area.to_dict(),
                    "aircraft": [f["properties"] for f in result["features"]][:30]}
        if not result["features"]:
            response["hint"] = "No aircraft tracked in the box right now (coverage depends on volunteer receivers)."
        return self._points_layer(name or "aircraft_now", result, "opensky", {"bbox": area.bbox}, response)

    # ------------------------------------------------------------------
    # SensorThings
    # ------------------------------------------------------------------
    def list_sensorthings_servers(self):
        return {"presets": [dict(id=k, **v) for k, v in sensorthings.PRESETS.items()],
                "entities_with_geometry": list(sensorthings.GEOMETRY_ENTITIES),
                "note": "Any OGC SensorThings v1.0 or v1.1 server works through url=; "
                        "discover_sensorthings lists what one offers."}

    def discover_sensorthings(self, url=None, preset=None):
        if preset:
            url = sensorthings.preset(preset)["url"]
        return sensorthings.discover(url)

    def add_sensorthings_layer(self, url=None, preset=None, entity="Location", aoi=None,
                               feature_limit=1000, expand_to=None, name=None, geometry_type="point",
                               snapshot=None):
        """Add a SensorThings entity set as a live layer through the QGIS provider.

        With an ``aoi`` the server filters to that extent, so a national
        network stays light. ``expand_to`` such as ``Thing:$top=1`` pulls
        related entities into extra fields. Servers whose OData dialect the
        provider cannot drive (GOST, such as Samen Meten) get a snapshot
        layer of their Locations fetched over plain REST instead; pass
        ``snapshot=true`` to force that, ``false`` to forbid it.
        """
        spec = sensorthings.preset(preset) if preset else None
        url = sensorthings.check_url(url or (spec or {}).get("url"))
        area = self._resolve_aoi(aoi) if aoi else None
        if snapshot is None:
            snapshot = bool((spec or {}).get("snapshot"))
        label = name or f"{_safe_name((spec or {}).get('name') or url.split('//')[-1].split('/')[0])}_{entity.lower()}"
        provenance = {"url": url, "entity": entity, "bbox": area.bbox if area else None,
                      "expand_to": expand_to}
        if not snapshot:
            extent_filter = sensorthings.extent_filter_for(area.bbox) if area else None
            uri = sensorthings.layer_uri(url, entity, feature_limit, 200, extent_filter, expand_to,
                                         geometry_type)
            layer = QgsVectorLayer(uri, label, "sensorthings")
            if not layer.isValid():
                raise IOError(f"The SensorThings server refused: {layer.error().summary()[:200] or 'unknown error'}. "
                              "Check the url ends with the API version (v1.0 or v1.1).")
            count = layer.featureCount()
            if count >= 0 or snapshot is False:
                layer = self._add_layer(layer, "sensorthings", provenance, persist=False)
                if self.iface is not None:
                    self.iface.mapCanvas().refresh()
                return {"layer_id": layer.id(), "name": layer.name(), "url": url, "entity": entity,
                        "geometry_type": geometry_type, "features": count,
                        "fields": [f.name() for f in layer.fields()][:30],
                        "live": True, "licence": (spec or {}).get("licence"),
                        "note": "The layer reads from the server as you pan; reload it to refresh readings."}
            # The provider could not count: the server rejects its type filter.
        if entity != "Location":
            raise ValueError(f"This server needs the snapshot path, which maps Locations only (not {entity})")
        result = sensorthings.fetch_locations(url, area.bbox if area else None, feature_limit)
        if not result["features"]:
            raise LookupError("The server returned no point locations"
                              + (" in the area" if area else "") + "; widen the area or check the url")
        layer, count = _memory_layer_from_features(label, result["features"])
        layer = self._add_layer(layer, "sensorthings", dict(provenance, snapshot=True))
        if self.iface is not None:
            self.iface.mapCanvas().refresh()
        return {"layer_id": layer.id(), "name": layer.name(), "url": url, "entity": entity,
                "geometry_type": "point", "features": count, "matched": result["matched"],
                "truncated": result["truncated"], "fields": [f.name() for f in layer.fields()][:30],
                "live": False, "licence": (spec or {}).get("licence"),
                "note": "Snapshot of the server's Locations fetched over REST (the QGIS provider "
                        "cannot drive this server's filters); run the command again to refresh."}

    # ------------------------------------------------------------------
    # population
    # ------------------------------------------------------------------
    def estimate_population(self, aoi, year=2020, age_sex=False):
        """People living in the area from WorldPop, with Meta's 30 m grid as a
        cross check; optional age and sex pyramid."""
        import time as _time
        area = self._resolve_aoi(aoi)
        if area.area_degrees2() > 4:
            raise ValueError("Keep population estimates under 2 by 2 degrees; WorldPop times out above that")
        year = int(year)
        if not 2000 <= year <= 2020:
            raise ValueError("WorldPop covers 2000 to 2020; pick a year in that range")
        started = _time.monotonic()
        budget = 200.0  # seconds; the MCP client gives up at 300
        response = {"aoi": area.to_dict(), "year": year}
        errors = {}
        try:
            response["worldpop"] = population.worldpop_total(area.bbox, year, timeout_s=90)
        except Exception as exc:
            errors["worldpop"] = str(exc)[:200]
        try:
            prepared = self.dataset_prepare(area.bbox, "population")
            summary = prepared["summary"]
            response["meta_hrsl"] = {"total_population": summary.get("total"),
                                     "valid_fraction": summary.get("valid_fraction"),
                                     "max_per_cell": summary.get("max"), "path": prepared["path"],
                                     "source": "Meta High Resolution Settlement Layer (CC BY 4.0)"}
        except Exception as exc:
            errors["meta_hrsl"] = str(exc)[:200]
        if age_sex:
            left = budget - (_time.monotonic() - started)
            if left < 30:
                errors["age_sex"] = "skipped: the other sources used the time budget; call again with age_sex only"
            else:
                try:
                    response["age_sex"] = population.worldpop_age_sex(area.bbox, year, timeout_s=int(left))
                except Exception as exc:
                    errors["age_sex"] = str(exc)[:200]
        if errors:
            response["errors"] = errors
        if "worldpop" not in response and "meta_hrsl" not in response:
            raise RuntimeError(f"No population source answered: {errors}")
        totals = [v["total_population"] for k, v in response.items()
                  if k in ("worldpop", "meta_hrsl") and v.get("total_population") is not None]
        response["best_estimate"] = int(round(sum(totals) / len(totals))) if totals else None
        response["interpretation"] = ("WorldPop spreads census totals over 100 m cells; Meta's layer "
                                      "detects buildings at 30 m. Agreement within 20% is typical; "
                                      "dense informal settlements and new suburbs differ more.")
        return response

    # ------------------------------------------------------------------
    # 3D Tiles
    # ------------------------------------------------------------------
    def add_3d_tiles(self, url=None, preset=None, name=None):
        """Add a Cesium 3D Tiles scene: Google Photorealistic (needs a Google
        Maps Platform key), any tileset URL, or the Cesium sample."""
        from qgis.core import QgsTiledSceneLayer
        preset = str(preset).lower() if preset else None
        spec = TILES_PRESETS.get(preset) if preset else None
        if preset and spec is None:
            raise ValueError(f"Unknown 3D Tiles preset '{preset}'; use {sorted(TILES_PRESETS)} or url=")
        if spec and spec.get("key_setting"):
            key = self._settings().value(spec["key_setting"], "")
            if not key:
                raise PermissionError("Google Photorealistic 3D Tiles need a Google Maps Platform key "
                                      "(Map Tiles API enabled). Store it with "
                                      "set_data_source_key('google-3d-tiles', key) or in the Sources tab.")
            url = f"{GOOGLE_3D_TILES}?key={key}"
        elif spec:
            url = spec["url"]
        url = str(url or "").strip()
        if not url.lower().startswith("https://"):
            raise ValueError("url must be an https tileset.json location")
        layer = QgsTiledSceneLayer(f"url={url}", name or (spec or {}).get("name") or "3D Tiles", "cesiumtiles")
        if not layer.isValid():
            raise IOError(f"QGIS could not open the tileset: {layer.error().summary()[:200] or 'unknown error'}")
        layer = self._add_layer(layer, "google-3d-tiles" if preset == "google" else "cesium-3d-tiles",
                                {"url": url.split("?")[0], "preset": preset}, persist=False)
        extent = layer.extent()
        return {"layer_id": layer.id(), "name": layer.name(), "crs": layer.crs().authid(),
                "extent": [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()],
                "attribution": (spec or {}).get("attribution"), "licence": (spec or {}).get("licence"),
                "note": "3D Tiles render in a 3D map view (View > 3D Map Views) and as footprints in 2D. "
                        "Google tiles are billed per session; keep the view small."}
