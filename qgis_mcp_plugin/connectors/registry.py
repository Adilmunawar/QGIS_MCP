# -*- coding: utf-8 -*-
"""Catalogue of live-data sources, grouped by category.

Each entry records what the agent needs to choose a source responsibly: the
licence, whether a key is needed, and which commands use it. Keys live in
QGIS settings, written by ``set_data_source_key`` or the panel, and are never
echoed back in any response.
"""

IMAGERY = "imagery"
WEATHER = "weather"
ELEVATION = "elevation"
VECTOR = "vector"
HAZARDS = "hazards"
BASEMAPS = "basemaps"
SENSORS = "sensors"

CATEGORIES = {
    IMAGERY: "Satellite imagery and derived spectral indices",
    WEATHER: "Weather, climate and agro-meteorology",
    ELEVATION: "Digital elevation models and terrain derivatives",
    VECTOR: "Reference vector data: places, boundaries, OpenStreetMap",
    HAZARDS: "Live hazard feeds: earthquakes, fires, disasters",
    BASEMAPS: "Tiled basemaps and daily global imagery",
    SENSORS: "Real-time sensor and observation networks",
}

REGISTRY = {}


def source(id, name, category, licence, auth="none", description="",
           commands=(), docs=None, attribution=None, key_setting=None):
    """Register a source. Returns the entry so modules can keep a handle."""
    entry = {
        "id": id,
        "name": name,
        "category": category,
        "licence": licence,
        "auth": auth,
        "key_setting": key_setting,
        "description": description,
        "commands": list(commands),
        "docs": docs,
        "attribution": attribution or name,
    }
    REGISTRY[id] = entry
    return entry


def describe_sources(category=None, settings=None):
    """List sources, marking whether each one is ready to use."""
    rows = []
    for entry in REGISTRY.values():
        if category and entry["category"] != category:
            continue
        row = dict(entry)
        if entry["auth"] == "none":
            row["ready"] = True
        elif settings is not None and entry["key_setting"]:
            row["ready"] = bool(settings.value(entry["key_setting"], ""))
        else:
            row["ready"] = False
        rows.append(row)
    rows.sort(key=lambda r: (r["category"], r["name"]))
    return rows


# -- registrations --------------------------------------------------------

source("earth-search", "Element84 Earth Search (AWS Open Data)", IMAGERY,
       licence="Sentinel: CC BY-SA 3.0 IGO; Landsat: public domain",
       description="STAC catalogue of Sentinel-2 L2A, Sentinel-1 GRD, Landsat "
                   "Collection 2 and Copernicus DEM, served as cloud-optimised "
                   "GeoTIFFs that stream straight into QGIS.",
       commands=["search_imagery", "add_imagery", "add_dem",
                 "compute_spectral_index", "get_index_time_series"],
       docs="https://earth-search.aws.element84.com/v1",
       attribution="Contains modified Copernicus Sentinel data")

source("planetary-computer", "Microsoft Planetary Computer", IMAGERY,
       licence="Varies per collection",
       auth="none",
       description="STAC catalogue with signed asset URLs; used as a fallback "
                   "when Earth Search lacks a collection.",
       commands=["search_imagery"],
       docs="https://planetarycomputer.microsoft.com/api/stac/v1")

source("nasa-gibs", "NASA GIBS daily imagery", BASEMAPS,
       licence="Public domain (NASA)",
       description="Daily global MODIS and VIIRS true-colour and science "
                   "layers as WMTS tiles, selectable by date.",
       commands=["add_basemap"],
       docs="https://gibs.earthdata.nasa.gov/")

source("osm-tiles", "OpenStreetMap standard tiles", BASEMAPS,
       licence="ODbL (data), CC BY-SA (tiles)",
       description="Standard OSM raster basemap.",
       commands=["add_basemap"], docs="https://www.openstreetmap.org/copyright",
       attribution="© OpenStreetMap contributors")

source("esri-imagery", "Esri World Imagery", BASEMAPS,
       licence="Esri terms of use; attribution required",
       description="High-resolution aerial and satellite basemap.",
       commands=["add_basemap"],
       attribution="Esri, Maxar, Earthstar Geographics, and the GIS User Community")

source("open-meteo", "Open-Meteo", WEATHER,
       licence="CC BY 4.0; non-commercial free tier",
       description="Current conditions, 16-day forecasts, and an hourly "
                   "archive back to 1940, no key needed.",
       commands=["get_weather", "get_weather_history", "attach_weather_to_layer"],
       docs="https://open-meteo.com/en/docs")

source("nasa-power", "NASA POWER agro-climatology", WEATHER,
       licence="Public domain (NASA)",
       description="Daily solar radiation, temperature, humidity, wind and "
                   "precipitation for any point since 1981, tuned for agriculture.",
       commands=["get_climate_series"],
       docs="https://power.larc.nasa.gov/docs/services/api/")

source("nominatim", "Nominatim geocoder (OpenStreetMap)", VECTOR,
       licence="ODbL; 1 request/second fair use",
       description="Place name to coordinates and back.",
       commands=["geocode", "reverse_geocode"],
       docs="https://nominatim.org/release-docs/latest/api/Overview/",
       attribution="© OpenStreetMap contributors")

source("overpass", "Overpass API (OpenStreetMap features)", VECTOR,
       licence="ODbL",
       description="Any OpenStreetMap feature type inside an area, by tag.",
       commands=["fetch_osm_features"],
       docs="https://wiki.openstreetmap.org/wiki/Overpass_API",
       attribution="© OpenStreetMap contributors")

source("usgs-earthquakes", "USGS earthquake feeds", HAZARDS,
       licence="Public domain (USGS)",
       description="Earthquakes of the last hour, day, week or month as GeoJSON.",
       commands=["fetch_earthquakes"],
       docs="https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php")

source("nasa-firms", "NASA FIRMS active fires", HAZARDS,
       licence="Public domain (NASA)", auth="free key",
       key_setting="qgis_mcp/keys/firms",
       description="VIIRS and MODIS active fire detections, last 1-10 days.",
       commands=["fetch_active_fires"],
       docs="https://firms.modaps.eosdis.nasa.gov/api/")

source("copernicus-dem", "Copernicus DEM GLO-30", ELEVATION,
       licence="Copernicus DEM licence (free, attribution)",
       description="30 m global elevation, streamed from Earth Search.",
       commands=["add_dem", "derive_terrain"],
       attribution="© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 "
                   "provided under COPERNICUS by the European Union and ESA")
