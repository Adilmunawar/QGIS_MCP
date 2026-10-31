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
LAND = "land"
BIODIVERSITY = "biodiversity"
THREE_D = "3d"
CLIMATE = "climate"

CATEGORIES = {
    IMAGERY: "Satellite imagery and derived spectral indices",
    WEATHER: "Weather, climate and agro-meteorology",
    ELEVATION: "Digital elevation models and terrain derivatives",
    VECTOR: "Reference vector data: places, boundaries, OpenStreetMap",
    HAZARDS: "Live hazard feeds: earthquakes, fires, disasters",
    BASEMAPS: "Tiled basemaps and daily global imagery",
    SENSORS: "Real time sensor and observation networks",
    LAND: "Land cover, land use and soil",
    BIODIVERSITY: "Species observations and ecology",
    THREE_D: "3D: terrain, buildings and lidar point clouds",
    CLIMATE: "Climate projections and seasonal outlooks",
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
       description="STAC catalogue with signed asset URLs: Esri land use, CCI land "
                   "cover, JRC surface water, NASADEM, ALOS DEM, MODIS temperature, "
                   "NDVI, snow, burned area, evapotranspiration, biomass, "
                   "biodiversity, electrification and 3DEP lidar.",
       commands=["search_imagery", "add_dataset", "summarise_land_cover", "add_point_cloud"],
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

source("esa-worldcover", "ESA WorldCover 2021 land cover", LAND,
       licence="CC BY 4.0",
       description="Eleven land cover classes at 10 m, streamed from the public AWS bucket.",
       commands=["add_dataset", "summarise_land_cover", "export_area_report"],
       docs="https://esa-worldcover.org/",
       attribution="© ESA WorldCover project 2021")

source("soilgrids", "SoilGrids 2.0 (ISRIC)", LAND,
       licence="CC BY 4.0",
       description="Clay, sand, silt, pH, organic carbon, nitrogen, bulk density at six depths, 250 m.",
       commands=["get_soil_properties", "attach_soil_to_layer", "export_area_report"],
       docs="https://www.isric.org/explore/soilgrids",
       attribution="ISRIC World Soil Information")

source("open-meteo-flood", "Open-Meteo flood (GloFAS)", WEATHER,
       licence="CC BY 4.0",
       description="River discharge forecast up to 7 months and history, 5 km grid.",
       commands=["get_river_discharge"],
       docs="https://open-meteo.com/en/docs/flood-api")

source("open-meteo-marine", "Open-Meteo marine", WEATHER,
       licence="CC BY 4.0",
       description="Wave height, period, direction and sea surface temperature.",
       commands=["get_marine_conditions"],
       docs="https://open-meteo.com/en/docs/marine-weather-api")

source("geoboundaries", "geoBoundaries", VECTOR,
       licence="Public domain or CC BY per country",
       description="Administrative boundaries ADM0 to ADM5 for every country.",
       commands=["add_admin_boundaries"],
       docs="https://www.geoboundaries.org/",
       attribution="geoBoundaries (Runfola et al.)")

source("gbif", "GBIF species occurrences", BIODIVERSITY,
       licence="CC0 / CC BY per dataset",
       description="Two billion species observation records, searchable by area and taxon.",
       commands=["fetch_species_occurrences"],
       docs="https://techdocs.gbif.org/en/openapi/",
       attribution="GBIF.org")

source("sensor-community", "Sensor.Community air sensors", SENSORS,
       licence="ODbL",
       description="Live PM2.5, PM10, temperature, humidity and pressure from citizen sensors.",
       commands=["fetch_air_sensors"],
       docs="https://sensor.community/en/")

source("gdacs", "GDACS disaster alerts", HAZARDS,
       licence="Free for non commercial use",
       description="Earthquakes, cyclones, floods, droughts, volcanoes and wildfires with alert levels.",
       commands=["fetch_disaster_alerts"],
       docs="https://www.gdacs.org/",
       attribution="GDACS (EC JRC / UN OCHA)")

source("nasa-modis", "NASA MODIS land products (via Planetary Computer)", LAND,
       licence="Public domain (NASA LP DAAC, NSIDC)",
       description="Land surface temperature, NDVI, leaf area, evapotranspiration, "
                   "productivity, snow cover and burned area, 250 m to 1 km, latest period.",
       commands=["add_dataset", "summarise_land_cover", "list_datasets"],
       docs="https://lpdaac.usgs.gov/", attribution="NASA MODIS (LP DAAC / NSIDC)")

source("usgs-3dep", "USGS 3DEP lidar (COPC point clouds, height above ground)", THREE_D,
       licence="Public domain (USGS)",
       description="Lidar point clouds streamed as COPC and 2 m height above ground, United States.",
       commands=["add_point_cloud", "add_dataset"],
       docs="https://www.usgs.gov/3d-elevation-program", attribution="USGS 3D Elevation Program")

source("openstreetmap-3d", "OpenStreetMap building footprints, extruded", THREE_D,
       licence="ODbL",
       description="Buildings from Overpass extruded by height or storey tags for 3D views.",
       commands=["add_3d_buildings", "make_3d_buildings", "open_3d_view", "set_terrain"],
       attribution="© OpenStreetMap contributors")

source("open-meteo-climate", "Open-Meteo climate projections (CMIP6 HighResMIP)", CLIMATE,
       licence="CC BY 4.0",
       description="Seven downscaled climate models, daily 1950 to 2050, for any point.",
       commands=["get_climate_projection"],
       docs="https://open-meteo.com/en/docs/climate-api")

source("open-meteo-seasonal", "Open-Meteo seasonal forecast", CLIMATE,
       licence="CC BY 4.0",
       description="Ensemble outlook up to nine months ahead with member spread.",
       commands=["get_seasonal_forecast"],
       docs="https://open-meteo.com/en/docs/seasonal-forecast-api")

source("nasa-eonet", "NASA EONET natural events", HAZARDS,
       licence="Public domain (NASA)",
       description="Curated natural events (storms, wildfires, floods, volcanoes, ice, dust) with tracks.",
       commands=["fetch_natural_events", "start_live_monitor"],
       docs="https://eonet.gsfc.nasa.gov/docs/v3")

source("openaq", "OpenAQ air quality monitors", SENSORS,
       licence="CC BY 4.0", auth="free key", key_setting="qgis_mcp/keys/openaq",
       description="Reference grade and low cost monitors worldwide with latest readings.",
       commands=["fetch_air_quality_stations"],
       docs="https://docs.openaq.org/")

source("live-monitor", "Live monitor (earthquakes, sensors, weather, alerts, events)", HAZARDS,
       licence="Per underlying source",
       description="Timer driven refresh of the feeds above for an area, with threshold alerts.",
       commands=["start_live_monitor", "stop_live_monitor", "get_live_monitor_status"])

source("copernicus-dem", "Copernicus DEM GLO-30", ELEVATION,
       licence="Copernicus DEM licence (free, attribution)",
       description="30 m global elevation, streamed from Earth Search.",
       commands=["add_dem", "derive_terrain"],
       attribution="© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 "
                   "provided under COPERNICUS by the European Union and ESA")
