# -*- coding: utf-8 -*-
"""One-line descriptions for every command, for the panel's Commands tab.

Kept beside the permission table rather than inside the handlers so the panel
can list commands without importing QGIS classes, and so a command missing a
description is easy to spot.
"""

COMMAND_HELP = {
    # diagnostics
    "ping": "Check the bridge is alive.",
    "get_qgis_info": "QGIS version, profile folder, Python version.",
    "get_server_info": "Server state, permissions, and the command list.",
    "get_audit_log": "Read recent entries from the audit trail.",
    "execute_code": "Run arbitrary PyQGIS code inside QGIS.",
    # project
    "load_project": "Open a .qgz project, replacing the current one.",
    "create_new_project": "Clear the session and start a new project.",
    "get_project_info": "Project file, CRS and its layers.",
    "save_project": "Write the project to disk.",
    "set_project_crs": "Change the project CRS.",
    # layers
    "add_vector_layer": "Load a vector file into the project.",
    "add_raster_layer": "Load a raster file into the project.",
    "create_memory_layer": "Create a scratch layer with typed fields.",
    "get_layers": "List every layer with type, CRS, counts and filter.",
    "remove_layer": "Drop a layer from the project.",
    "reload_layer": "Re-read a layer from its source.",
    "zoom_to_layer": "Zoom the canvas to a layer's extent.",
    "set_layer_visibility": "Show or hide a layer.",
    "set_layer_opacity": "Set layer opacity from 0 to 1.",
    "set_layer_crs": "Declare a layer's CRS (relabels, never reprojects).",
    "get_layer_fields": "List a vector layer's fields and types.",
    "get_layer_extent": "A layer's bounding box and CRS.",
    "export_layer": "Write a layer to GPKG, GeoJSON, Shapefile or CSV.",
    # styling
    "set_layer_style": "Apply a .qml style file.",
    "save_layer_style": "Save the current style to .qml.",
    # features
    "get_layer_features": "Read features, optionally filtered by expression.",
    "count_features": "Exact feature count, optionally filtered.",
    "get_unique_values": "Distinct values of a field, with counts.",
    "get_field_statistics": "min/max/sum/mean/median/stdev for a field.",
    "set_layer_filter": "Filter a layer, reporting true before/after counts.",
    "get_layer_filter": "Read a layer's current filter.",
    "clear_layer_filter": "Remove a layer's filter.",
    "add_features": "Append features from attributes and WKT.",
    "delete_features": "Delete matching features. Dry-runs unless confirmed.",
    "update_features": "Set field values on matching features. Dry-runs first.",
    "select_features_by_expression": "Select features matching an expression.",
    "get_selected_features": "Read the current selection.",
    "clear_selection": "Clear selection on one or all layers.",
    "zoom_to_selection": "Zoom the canvas to selected features.",
    # schema
    "add_field": "Add an attribute field.",
    "delete_field": "Drop a field and its values. Dry-runs unless confirmed.",
    "rename_field": "Rename an attribute field.",
    "create_spatial_index": "Build a spatial index to speed a layer up.",
    # query and diagnostics
    "execute_sql": "Run SQL across layers via a virtual layer.",
    "group_statistics": "Aggregate a field grouped by another.",
    "check_geometry_validity": "Report invalid or empty geometries.",
    "find_duplicates": "Find duplicate attribute values or geometries.",
    "detect_layer_crs": "Infer a layer's true CRS from its coordinates.",
    # raster
    "get_raster_statistics": "Band min/max/mean/stdev for a raster.",
    "sample_raster_values": "Read band values at given points.",
    # processing
    "execute_processing": "Run a Processing algorithm.",
    "list_processing_algorithms": "List available algorithms.",
    "get_processing_algorithm_help": "Parameters and help for an algorithm.",
    # canvas and output
    "get_canvas_state": "Canvas extent, scale, rotation and CRS.",
    "set_canvas_extent": "Move the canvas to an extent.",
    "render_map": "Render the map to an image file.",
    "render_map_base64": "Render the map and return it inline as PNG.",
    "list_layouts": "List print layouts in the project.",
    "export_layout": "Export a layout to PDF or image.",
    # live data
    "list_data_sources": "Catalogue of live data sources by category.",
    "resolve_aoi": "Preview how an area-of-interest spec is interpreted.",
    "set_data_source_key": "Store an API key for a source that needs one.",
    "geocode": "Place name to coordinates (Nominatim).",
    "reverse_geocode": "Coordinates to a place (Nominatim).",
    "add_place_marker": "Add a geocoded place as a layer.",
    "search_imagery": "Find Sentinel-2, Landsat or Sentinel-1 scenes over an area.",
    "add_imagery": "Stream a satellite scene's bands into the project.",
    "compute_spectral_index": "NDVI, NDWI, NBR... over an area from a scene.",
    "get_index_time_series": "Mean index per scene across a date range.",
    "add_dem": "Stream Copernicus DEM 30 m for an area.",
    "derive_terrain": "Slope, aspect, hillshade from a DEM.",
    "get_weather": "Current weather, forecast or air quality (Open-Meteo).",
    "get_weather_history": "Historical daily/hourly weather since 1940.",
    "get_climate_series": "NASA POWER daily agro-climatology.",
    "attach_weather_to_layer": "Add weather fields to every feature.",
    "list_basemaps": "Available basemaps including NASA GIBS daily layers.",
    "add_basemap": "Add an XYZ basemap, GIBS layers by date.",
    "fetch_osm_features": "OpenStreetMap features by tag inside an area.",
    "fetch_earthquakes": "USGS earthquakes for the last hour to month.",
    "fetch_active_fires": "NASA FIRMS active fire detections (needs key).",
    # toolbox
    "list_datasets": "Global datasets the toolbox can add: land cover, land use, surface water, NASADEM.",
    "add_dataset": "Add a global dataset clipped to the area, with a class summary.",
    "summarise_land_cover": "Share of the area per land cover class.",
    "get_soil_properties": "SoilGrids texture, pH, carbon at a point.",
    "attach_soil_to_layer": "Add topsoil properties to features at centroids.",
    "get_river_discharge": "GloFAS river discharge forecast and history.",
    "get_marine_conditions": "Waves, swell and sea temperature at a point.",
    "get_elevation": "Elevation at points or the area centre.",
    "add_admin_boundaries": "Country admin units from geoBoundaries.",
    "fetch_species_occurrences": "GBIF species records in the area.",
    "fetch_air_sensors": "Sensor.Community air quality stations nearby.",
    "fetch_disaster_alerts": "GDACS disaster alerts, optionally in the area.",
    "compute_index_change": "Index difference between two periods: loss and gain.",
    "zonal_index_statistics": "Mean index per polygon of a layer.",
    "add_imagery_series": "Several dated scenes for the temporal controller.",
    "export_area_report": "HTML or PDF briefing: map, weather, land cover, soil, NDVI.",
    # advanced toolbox
    "get_climate_projection": "CMIP6 yearly temperature and rainfall to 2050, seven models.",
    "get_seasonal_forecast": "Ensemble outlook up to nine months, daily and weekly.",
    "get_elevation_profile": "Elevation along a line with gain, loss and a chart.",
    "make_3d_buildings": "Extrude a footprint layer by height or levels for 3D views.",
    "add_3d_buildings": "Fetch OpenStreetMap buildings in the area and extrude them.",
    "set_terrain": "Use a DEM layer as the 3D terrain and profile ground.",
    "add_point_cloud": "USGS 3DEP lidar tiles for the area, or any COPC URL.",
    "open_3d_view": "Open a new 3D map view (confirm; may crash without a GPU).",
    "fetch_natural_events": "NASA EONET natural events as a point layer.",
    "fetch_air_quality_stations": "OpenAQ monitors with latest readings (needs a key).",
    "start_live_monitor": "Refresh feeds for the area on a timer and raise alerts.",
    "stop_live_monitor": "Stop the live monitor.",
    "get_live_monitor_status": "Monitor configuration, counts and recent alerts.",
}


def describe(command):
    return COMMAND_HELP.get(command, "")
