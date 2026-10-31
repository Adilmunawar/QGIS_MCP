# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the plugin version
is the one declared in `qgis_mcp_plugin/metadata.txt`.

## [Unreleased]

### Planned
- Copernicus Data Space, Overture buildings, FAO WaPOR, CHIRPS, more SensorThings presets. See the roadmap in the README.

## [4.4.0] - Sensors release

### Added
- **Data Explorer `Sensors` tab** (`qgis_mcp_plugin/explorer_sensors.py`): USGS river gauges, NOAA buoys and tide stations, OpenSky aircraft, and any OGC SensorThings server (presets for Hamburg and the Dutch Samen Meten network) added as a live layer through the native QGIS provider, whole server or area only. A `3D Tiles` box on the 3D tab and a `Population in area` button on Land and Soil. Fifteen tabs.
- Connectors `gauges.py` (USGS NWIS instantaneous values, NDBC latest observations, NOAA CO-OPS water levels), `aircraft.py` (OpenSky states), `sensorthings.py` (presets, discovery, provider URIs) and `population.py` (WorldPop statistics API with its noisy HTML wrapper handled, totals and age and sex pyramids).
- A twentieth global raster: Meta's high resolution population density (30 m) streamed from AWS Open Data, with the sum of the cells reported as the population of the area.
- `qgis_mcp_plugin/sensor_commands.py`: 9 new commands and MCP tools (118 total): `fetch_river_gauges`, `fetch_buoys`, `fetch_tide_stations`, `fetch_aircraft`, `list_sensorthings_servers`, `discover_sensorthings`, `add_sensorthings_layer`, `estimate_population` (WorldPop and Meta combined, optional age pyramid) and `add_3d_tiles` (Google Photorealistic 3D Tiles with a stored key, the Cesium sample, or any tileset URL).
- `tests/test_sensors_headless.py` (45 offline checks); the explorer suite grows to 52.
- **Overview tab** (`qgis_mcp_plugin/explorer_overview.py`), first in the Data Explorer: the area defaults to the current map view and nine one click buttons pull a quick look card, the latest Sentinel-2 image, NDVI, land cover, a DEM, surface temperature, the population grid, 30 days of rain or a PDF report. Sixteen tabs.
- The Data Explorer gets its own toolbar button (placed first, with its own icon), an `Open Data Explorer` button at the top of the server panel, and it opens by itself the first time QGIS starts after an install or upgrade.

### Changed
- `pyproject.toml`, `metadata.txt` and the plugin report version 4.4.0.

## [4.3.0] - Advanced release

### Added
- **Data Explorer tabs** `3D` and `Monitor` (`qgis_mcp_plugin/explorer_advanced.py`), climate projection and seasonal outlook buttons on the Weather tab, an elevation profile box on the Elevation tab, a natural events box on the Hazards tab and an OpenAQ button beside the community sensors. Fourteen tabs in all.
- **15 more global rasters** in `connectors/landcover.py`, all through Planetary Computer: ESA CCI land cover (22 classes), ALOS PALSAR forest / non forest, ALOS World 3D and Copernicus 90 m elevation, USGS 3DEP height above ground, MODIS land surface temperature, NDVI, leaf area index, evapotranspiration, net primary productivity, snow cover and burned area, harmonised above ground biomass, Biodiversity Intactness Index and HREA electrification. Continuous datasets are converted to physical units (scale, offset, valid range, decibels) and styled with a colour ramp; `summarise_land_cover` returns statistics, class shares or wet fractions as appropriate.
- Planetary Computer signing now uses one cached SAS token per collection, verified with a ranged read and refreshed before expiry, with backoff on throttling.
- `connectors/weather.py`: `climate_projection` (seven CMIP6 HighResMIP models, yearly ensemble means and decade trend) and `seasonal_forecast` (ensemble mean, minimum and maximum, weekly blocks). `connectors/events.py`: NASA EONET natural events. `connectors/airquality.py`: OpenAQ v3 with a stored key.
- `qgis_mcp_plugin/advanced_commands.py` and `monitor.py`: 13 new commands and MCP tools (109 total): `get_climate_projection`, `get_seasonal_forecast`, `get_elevation_profile` (chart image, gain, loss, grade; from WKT, a layer feature, points or a transect through the area), `make_3d_buildings`, `add_3d_buildings`, `set_terrain`, `add_point_cloud` (USGS 3DEP COPC tiles or any COPC URL), `open_3d_view`, `fetch_natural_events`, `fetch_air_quality_stations`, `start_live_monitor`, `stop_live_monitor`, `get_live_monitor_status`.
- `export_area_report` writes a PDF when the path ends in `.pdf`.
- `open_3d_view` is categorised destructive and needs `confirm=true`: creating a 3D canvas from a script crashed QGIS on a remote desktop session during testing; the Data Explorer asks before opening one.
- `tests/test_advanced_headless.py` (66 offline checks); the explorer suite grows to 47 checks covering the new tabs.

### Changed
- `list_datasets` takes a `category` and reports the kind, unit and valid range of each dataset; `add_dataset` is split into `dataset_prepare` and `dataset_apply` so the dashboard and the MCP server share one code path.
- `pyproject.toml`, `metadata.txt` and the plugin report version 4.3.0.

## [4.2.0] - Toolbox release

### Added
- **Data Explorer tabs** `Land and Soil`, `Water`, `Boundaries and Species` and `Analysis` (`qgis_mcp_plugin/explorer_toolbox.py`), so every source and workflow is usable with clicks and no AI client.
- Connectors: `landcover.py` (ESA WorldCover from its public AWS bucket, Esri annual land use, JRC Global Surface Water and NASADEM through Planetary Computer with signed URLs, class shares in km²), `soil.py` (SoilGrids with USDA texture class), `boundaries.py` (geoBoundaries), `species.py` (GBIF with taxon matching and pagination), `sensors.py` (Sensor.Community), `disasters.py` (GDACS RSS), and GloFAS river discharge, marine and elevation functions in `weather.py`.
- 16 new commands and MCP tools (96 total): `list_datasets`, `add_dataset`, `summarise_land_cover`, `get_soil_properties`, `attach_soil_to_layer`, `get_river_discharge`, `get_marine_conditions`, `get_elevation`, `add_admin_boundaries`, `fetch_species_occurrences`, `fetch_air_sensors`, `fetch_disaster_alerts`, `compute_index_change`, `zonal_index_statistics`, `add_imagery_series`, `export_area_report`.
- Analysis workflows: change detection with diverging styling and loss and gain classes; per polygon statistics through `QgsZonalStatistics`; dated scene groups with temporal ranges for the Temporal Controller; an HTML area report.
- `tests/test_toolbox_headless.py` (40 offline checks) and dashboard screenshots with real fonts under `docs/images/`.

### Changed
- Dataset mosaics are clipped through a warped VRT, so projected tiles (UTM land use) and geographic tiles both work; pixel areas are computed for either grid.
- `pyproject.toml`, `metadata.txt` and the plugin report version 4.2.0.

## [4.1.0] - Live data release

### Added
- **Data Explorer dashboard** (`qgis_mcp_plugin/explorer.py`): a dock panel with an area-of-interest picker (place search with radius, draw on map, map view, layer extent, selection), tabs for imagery search and streaming, spectral indices and time series, weather and climate with attach-to-layer, Copernicus DEM and terrain products, OpenStreetMap presets and custom tags, earthquakes and fires, basemaps, source catalogue with key entry, and a log. Fetches run as `QgsTask` workers with progress and cancel; layers are created on the GUI thread. Opened from the toolbar button menu or `Plugins > QGIS MCP > Data Explorer`.
- `tests/test_explorer_headless.py`: 30 offscreen checks driving the dashboard through the real task manager with stubbed HTTP, plus an `--online` scenario, with screenshots of every tab written to `tests/projects/`.
- A live data layer in the plugin: `net.py` (HTTP through the QGIS network manager), `aoi.py` (one area-of-interest resolver for place names, bboxes, layers, selections, canvas, points and WKT), `live_commands.py` and a `connectors/` package with a source registry.
- 21 new commands and MCP tools: `list_data_sources`, `resolve_aoi`, `geocode`, `reverse_geocode`, `add_place_marker`, `search_imagery`, `add_imagery`, `compute_spectral_index`, `get_index_time_series`, `add_dem`, `derive_terrain`, `get_weather`, `get_weather_history`, `get_climate_series`, `attach_weather_to_layer`, `list_basemaps`, `add_basemap`, `fetch_osm_features`, `fetch_earthquakes`, `fetch_active_fires`, `set_data_source_key`. Tool count 59 -> 80.
- STAC search on Element84 Earth Search (Sentinel-2 L2A and C1, Landsat C2 L2, Sentinel-1 GRD, Copernicus DEM) with Planetary Computer as a fallback; assets stream through GDAL `/vsicurl/`.
- Nine spectral indices plus sandboxed custom expressions, Sentinel-2 SCL cloud masking, reflectance scale/offset handling, class breakdowns, GeoTIFF output with nodata and an automatic colour ramp.
- Open-Meteo current, forecast, air quality and 1940+ archive; NASA POWER agro climatology; batch weather fields on layer features.
- Nominatim geocoding with session cache and 1 request/second throttle; Overpass OSM fetch with failover and geometry-typed memory layers.
- USGS earthquake feeds; NASA FIRMS active fires with a stored key; NASA GIBS dated WMTS basemaps and seven static basemaps.
- Provenance metadata on every fetched layer (source, query, timestamp, licence, attribution).
- `tests/test_live_data_headless.py` (86 offline checks with stubbed HTTP) and `tests/projects/build_live_data_project.py`, which builds a real test project over any place.
- `docs/research/` with verified source references and MCP ecosystem notes.

### Security
- API keys are redacted from HTTP error messages and from the audit log; keys are validated before storage.
- STAC asset locations are restricted to HTTPS on known data hosts (or S3 mapped to HTTPS), so a tampered catalogue response cannot point GDAL at local files.
- Custom index expressions are validated with the AST: arithmetic, comparisons, band names and a few numpy functions only; exponents are capped.
- Index raster reads are capped at 16 Mpx per band and coarsened automatically above that.

### Fixed
- The MCP client drops its socket after a timeout so a late reply can never be read as the answer to the next command; default timeout raised to 300 s.
- Commands that spin a nested event loop no longer let a second client command run mid-command: incoming messages are queued until the handler returns.
- Parameter mismatches are reported before the handler runs, naming the accepted parameters, so a TypeError inside a handler keeps its traceback.
- Index output files include a hash of the area, expression and mask, and a locked file gets a sibling name instead of a Windows permission error.
- Fetched vector layers are written to GeoPackage in the plugin cache instead of memory layers, so they survive saving and reopening the project.
- Headless `render_map` frames the data layers rather than the global basemaps.
- Terrain products from geographic DEMs are computed in the local UTM zone.
- Overpass failover reports every mirror tried, treats a server `remark` error as a failure, and flags truncated results.

### Changed
- `pyproject.toml`: real metadata, `qgis-mcp` console script, and `mcp[cli]>=1.3.0,<2` because the 2.x SDK removed `FastMCP`.
- `metadata.txt`: author, email, tags, changelog; plugin version 4.1.0.
- GDAL HTTP tuning (range merging, block cache, no directory listing) so a remote COG opens in seconds rather than tens of seconds.

## [4.0.0] - Permission gated release

### Added
- Permission profiles: Read only, Standard, Full and Master Control, mapping four risk categories (read, write, destructive, code) to allow, approve or deny.
- Non-blocking approval queue for commands that need a human decision, with timeouts and temporary per command grants.
- Optional pairing token and a `hello` handshake; the MCP server reads `QGIS_MCP_TOKEN`, `QGIS_MCP_HOST`, `QGIS_MCP_PORT`, `QGIS_MCP_TIMEOUT` and `QGIS_MCP_CLIENT_NAME`.
- Append-only JSONL audit log and the `get_audit_log` tool.
- Data-analysis and editing tools: `execute_sql`, `group_statistics`, `check_geometry_validity`, `find_duplicates`, `detect_layer_crs`, `update_features`, `add_field`, `rename_field`, `delete_field`, `create_spatial_index`.
- Raster tools: `get_raster_statistics` and `sample_raster_values`.
- Tool count raised to 59.
- Protocol version 3.

### Security
- Unknown commands are treated as destructive so new commands fail closed.
- Destructive edits dry run unless `confirm=true`.

## [3.0.0] - Interface and capability release

### Added
- Redesigned dock panel with Server, Activity and Settings tabs and a "Copy MCP client config" button.
- Purpose-drawn plugin icon in several sizes.
- 15 new commands (46 total), including `create_memory_layer`, `add_features`, `delete_features`, layer filters, `count_features`, `get_unique_values`, `get_field_statistics`, `set_layer_crs`, `export_layer`, style load/save, and `render_map_base64`.
- `render_map_image` returns real image content so the model can look at the map.

## [2.0.0] - QGIS 4 / PyQt6 port

### Changed
- Ported to QGIS 4 and PyQt6 (`supportsQt6=True`, `qgisMaximumVersion=4.99`).
- Newline-delimited JSON (NDJSON) framing, backward compatible with v1 clients; event-driven `QTcpServer` replaces a polling loop.
- `execute_code` gated behind an explicit opt in; the listener binds to `127.0.0.1`.
- 32 MB per-client buffer cap, structured error responses, client timeouts and a transparent retry.
- Tool count raised from 15 to 29.

## [1.0.0] - Upstream baseline

- Initial plugin and MCP server from [jjsantos01/qgis_mcp](https://github.com/jjsantos01/qgis_mcp), inspired by BlenderMCP.
