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
}


def describe(command):
    return COMMAND_HELP.get(command, "")
