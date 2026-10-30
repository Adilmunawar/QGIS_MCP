# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the plugin version
is the one declared in `qgis_mcp_plugin/metadata.txt`.

## [Unreleased]

### Planned
- Sensor feeds (OGC SensorThings, OpenAQ, river gauges), change detection, 3D buildings and point clouds, background jobs with progress. See the roadmap in the README.

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
