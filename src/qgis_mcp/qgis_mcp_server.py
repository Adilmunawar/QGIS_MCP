#!/usr/bin/env python3
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""
QGIS MCP server
===============

Exposes a running QGIS instance (via the ``qgis_mcp_plugin`` socket server) as
MCP tools.

Speaks protocol v3: newline-delimited JSON with a hello handshake, one document per message, so a
response is read to its delimiter instead of being guessed at by repeatedly
attempting to parse a partial buffer.

Configuration via environment:
    QGIS_MCP_HOST     (default 127.0.0.1)
    QGIS_MCP_PORT     (default 9876)
    QGIS_MCP_TIMEOUT  (default 30.0 seconds)
    QGIS_MCP_TOKEN    (pairing token, when the panel requires one)
    QGIS_MCP_CLIENT_NAME (shown in the panel and audit log)
"""

import base64
import json
import logging
import os
import socket
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

from mcp.server.fastmcp import Context, FastMCP, Image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("QgisMCPServer")

DEFAULT_HOST = os.environ.get("QGIS_MCP_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("QGIS_MCP_PORT", "9876"))
# Longer than the plugin's default 60s approval window, so a command parked
# for human approval can actually be answered instead of the client giving up
# first and leaving an orphaned request in the queue.
DEFAULT_TIMEOUT = float(os.environ.get("QGIS_MCP_TIMEOUT", "300.0"))
DEFAULT_TOKEN = os.environ.get("QGIS_MCP_TOKEN", "")
CLIENT_NAME = os.environ.get("QGIS_MCP_CLIENT_NAME", "claude-mcp")

NOT_RUNNING_HINT = (
    "Could not reach QGIS. Open QGIS, then Plugins > QGIS MCP > QGIS MCP and "
    "click 'Start Server'."
)


class QgisConnectionError(RuntimeError):
    """Raised when QGIS cannot be reached or the exchange fails."""


class QgisTimeoutError(QgisConnectionError):
    """QGIS accepted the request but did not answer in time.

    Usually means the command is parked awaiting approval in the QGIS MCP
    panel. Retrying would queue a second approval request, so we never do.
    """


class QgisClient:
    """Blocking NDJSON client for the QGIS plugin's socket server.

    A single MCP server process may issue tool calls concurrently, so every
    exchange holds a lock: one request and its response travel the socket
    without interleaving with another.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: float = DEFAULT_TIMEOUT, token: str = DEFAULT_TOKEN,
                 client_name: str = CLIENT_NAME):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.token = token
        self.client_name = client_name
        self._sock: Optional[socket.socket] = None
        self._buffer = bytearray()
        self._lock = threading.Lock()

    # -- connection management ----------------------------------------
    def connect(self) -> None:
        if self._sock is not None:
            return
        try:
            self._sock = socket.create_connection((self.host, self.port),
                                                  timeout=self.timeout)
            self._sock.settimeout(self.timeout)
            self._buffer.clear()
            logger.info("Connected to QGIS at %s:%s", self.host, self.port)
        except OSError as exc:
            self._sock = None
            raise QgisConnectionError(f"{NOT_RUNNING_HINT} ({exc})") from exc
        try:
            self._handshake()
        except (OSError, ValueError) as exc:
            self.disconnect()
            raise QgisConnectionError(f"Handshake failed: {exc}") from exc

    def _handshake(self) -> None:
        """Introduce ourselves, and pair if the server demands a token.

        Sent on every connect: the server uses the name to attribute audit
        entries, and a token-required server rejects everything else until it
        arrives.
        """
        payload = json.dumps({
            "type": "hello",
            "params": {"client": self.client_name, "token": self.token},
        })
        assert self._sock is not None
        self._sock.sendall(payload.encode("utf-8") + b"\n")
        reply = self._read_message()
        if reply.get("status") != "success":
            message = reply.get("message", "unknown error")
            raise QgisConnectionError(
                f"QGIS refused the connection: {message} "
                "Set QGIS_MCP_TOKEN to the token shown in the QGIS MCP panel."
            )

    def disconnect(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buffer.clear()

    def is_alive(self) -> bool:
        """Cheap liveness probe that does not consume a request slot."""
        if self._sock is None:
            return False
        try:
            self._sock.setblocking(False)
            try:
                # A readable socket returning b"" means the peer closed it.
                if self._sock.recv(1, socket.MSG_PEEK) == b"":
                    return False
            except BlockingIOError:
                pass  # No data pending: healthy.
            except OSError:
                return False
            return True
        finally:
            if self._sock is not None:
                self._sock.setblocking(True)
                self._sock.settimeout(self.timeout)

    # -- request/response ---------------------------------------------
    def send_command(self, command_type: str,
                     params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = json.dumps({"type": command_type, "params": params or {}})
        with self._lock:
            try:
                return self._exchange(payload)
            except QgisTimeoutError:
                # Do not retry: the request is very likely parked for approval,
                # and a retry would queue a second one.
                raise
            except (QgisConnectionError, OSError, ValueError) as first_error:
                # One transparent retry: QGIS may have been restarted, or the
                # socket may have gone stale between calls.
                logger.warning("Retrying %s after: %s", command_type, first_error)
                self.disconnect()
                try:
                    return self._exchange(payload)
                except (OSError, ValueError) as exc:
                    self.disconnect()
                    raise QgisConnectionError(
                        f"{command_type} failed: {exc}. {NOT_RUNNING_HINT}"
                    ) from exc

    def _exchange(self, payload: str) -> Dict[str, Any]:
        self.connect()
        assert self._sock is not None
        self._sock.sendall(payload.encode("utf-8") + b"\n")
        return self._read_message()

    def _read_message(self) -> Dict[str, Any]:
        assert self._sock is not None
        while True:
            idx = self._buffer.find(b"\n")
            if idx >= 0:
                line = bytes(self._buffer[:idx])
                del self._buffer[: idx + 1]
                if line.strip():
                    return json.loads(line.decode("utf-8"))
                continue
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout as exc:
                # A reply may still arrive later; it must not be mistaken for
                # the answer to the next command, so the socket is discarded.
                self.disconnect()
                raise QgisTimeoutError(
                    f"No answer within {self.timeout}s. If the QGIS MCP panel "
                    "is set to Standard or Full, this command may be waiting "
                    "for approval there - check the Approvals tab. Master "
                    "Control runs everything without asking."
                ) from exc
            if not chunk:
                raise QgisConnectionError("QGIS closed the connection")
            self._buffer.extend(chunk)


_client: Optional[QgisClient] = None
_client_lock = threading.Lock()


def get_qgis_connection() -> QgisClient:
    """Return the shared client, reconnecting if the socket went stale."""
    global _client
    with _client_lock:
        if _client is not None and not _client.is_alive():
            logger.warning("Existing connection is no longer valid; reconnecting")
            _client.disconnect()
        if _client is None:
            _client = QgisClient()
        _client.connect()
        return _client


class QgisToolError(RuntimeError):
    """A command the plugin refused or that failed inside QGIS.

    Raised so the MCP client marks the result as an error, which the model
    notices without having to parse JSON for a status field.
    """


def _call(command: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Run one command and render the response as JSON text for the model."""
    try:
        result = get_qgis_connection().send_command(command, params)
    except QgisConnectionError as exc:
        raise QgisToolError(str(exc)) from exc
    if result.get("status") == "error":
        # The SDK already prefixes "Error executing tool <name>:".
        bits = [str(result.get("message", "unknown error"))]
        if result.get("error_type") in ("PermissionDenied", "ApprovalDenied",
                                        "Unauthorized", "UnknownCommand"):
            bits.append(f"({result['error_type']})")
        if result.get("profile"):
            bits.append(f"profile={result['profile']} category={result.get('category')}")
        if result.get("available"):
            bits.append(f"available={result['available']}")
        raise QgisToolError(". ".join(bits))
    return json.dumps(result, indent=2)


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    logger.info("QGIS MCP server starting up")
    try:
        try:
            get_qgis_connection()
            logger.info("Connected to QGIS on startup")
        except QgisConnectionError as exc:
            # Not fatal: QGIS may simply not be running yet. Tools reconnect.
            logger.warning("QGIS not reachable at startup: %s", exc)
        yield {}
    finally:
        global _client
        if _client is not None:
            logger.info("Disconnecting from QGIS on shutdown")
            _client.disconnect()
            _client = None
        logger.info("QGIS MCP server shut down")


mcp = FastMCP(
    "Qgis_mcp",
    instructions="QGIS MCP toolbox by Adil Munawar (https://github.com/Adilmunawar/QGIS_MCP): "
                 "QGIS integration and live open data through the Model Context Protocol",
    lifespan=server_lifespan,
)


# ---------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------
@mcp.tool()
def ping(ctx: Context) -> str:
    """Check connectivity to the QGIS plugin."""
    return _call("ping")


@mcp.tool()
def get_qgis_info(ctx: Context) -> str:
    """Get QGIS version, profile folder and Python version."""
    return _call("get_qgis_info")


@mcp.tool()
def get_server_info(ctx: Context) -> str:
    """Get plugin version, protocol version, and which commands are available."""
    return _call("get_server_info")


# ---------------------------------------------------------------------
# project
# ---------------------------------------------------------------------
@mcp.tool()
def load_project(ctx: Context, path: str) -> str:
    """Load a QGIS project from the specified path."""
    return _call("load_project", {"path": path})


@mcp.tool()
def create_new_project(ctx: Context, path: str) -> str:
    """Create a new project and save it. Clears the currently loaded project."""
    return _call("create_new_project", {"path": path})


@mcp.tool()
def get_project_info(ctx: Context, layer_limit: int = 50) -> str:
    """Get current project information, including its layers."""
    return _call("get_project_info", {"layer_limit": layer_limit})


@mcp.tool()
def save_project(ctx: Context, path: str = None) -> str:
    """Save the project to the given path, or to its current path if omitted."""
    return _call("save_project", {"path": path} if path else {})


@mcp.tool()
def set_project_crs(ctx: Context, authid: str) -> str:
    """Set the project CRS, e.g. 'EPSG:4326'."""
    return _call("set_project_crs", {"authid": authid})


# ---------------------------------------------------------------------
# layers
# ---------------------------------------------------------------------
@mcp.tool()
def add_vector_layer(ctx: Context, path: str, provider: str = "ogr",
                     name: str = None) -> str:
    """Add a vector layer to the project."""
    params = {"path": path, "provider": provider}
    if name:
        params["name"] = name
    return _call("add_vector_layer", params)


@mcp.tool()
def add_raster_layer(ctx: Context, path: str, provider: str = "gdal",
                     name: str = None) -> str:
    """Add a raster layer to the project."""
    params = {"path": path, "provider": provider}
    if name:
        params["name"] = name
    return _call("add_raster_layer", params)


@mcp.tool()
def get_layers(ctx: Context) -> str:
    """Retrieve all layers in the current project with their metadata."""
    return _call("get_layers")


@mcp.tool()
def remove_layer(ctx: Context, layer_id: str) -> str:
    """Remove a layer from the project by its ID."""
    return _call("remove_layer", {"layer_id": layer_id})


@mcp.tool()
def set_layer_visibility(ctx: Context, layer_id: str, visible: bool = True) -> str:
    """Show or hide a layer in the layer tree."""
    return _call("set_layer_visibility",
                 {"layer_id": layer_id, "visible": visible})


@mcp.tool()
def get_layer_fields(ctx: Context, layer_id: str) -> str:
    """List the attribute fields of a vector layer, with types."""
    return _call("get_layer_fields", {"layer_id": layer_id})


@mcp.tool()
def get_layer_extent(ctx: Context, layer_id: str) -> str:
    """Get a layer's bounding box and CRS."""
    return _call("get_layer_extent", {"layer_id": layer_id})


@mcp.tool()
def zoom_to_layer(ctx: Context, layer_id: str) -> str:
    """Zoom the map canvas to the extent of a specified layer."""
    return _call("zoom_to_layer", {"layer_id": layer_id})


# ---------------------------------------------------------------------
# features
# ---------------------------------------------------------------------
@mcp.tool()
def get_layer_features(ctx: Context, layer_id: str, limit: int = 10,
                       expression: str = None) -> str:
    """Retrieve features from a vector layer, optionally filtered.

    ``expression`` is a QGIS expression, e.g. "population > 1000".
    """
    params: Dict[str, Any] = {"layer_id": layer_id, "limit": limit}
    if expression:
        params["expression"] = expression
    return _call("get_layer_features", params)


@mcp.tool()
def select_features_by_expression(ctx: Context, layer_id: str,
                                  expression: str) -> str:
    """Select features matching a QGIS expression, and report how many matched."""
    return _call("select_features_by_expression",
                 {"layer_id": layer_id, "expression": expression})


@mcp.tool()
def get_selected_features(ctx: Context, layer_id: str, limit: int = 10) -> str:
    """Retrieve the currently selected features of a vector layer."""
    return _call("get_selected_features", {"layer_id": layer_id, "limit": limit})


@mcp.tool()
def clear_selection(ctx: Context, layer_id: str = None) -> str:
    """Clear the selection on one layer, or on all vector layers if omitted."""
    return _call("clear_selection", {"layer_id": layer_id} if layer_id else {})


# ---------------------------------------------------------------------
# processing
# ---------------------------------------------------------------------
@mcp.tool()
def list_processing_algorithms(ctx: Context, filter_text: str = None,
                               limit: int = 100) -> str:
    """List available processing algorithms, optionally filtered by substring."""
    params: Dict[str, Any] = {"limit": limit}
    if filter_text:
        params["filter_text"] = filter_text
    return _call("list_processing_algorithms", params)


@mcp.tool()
def get_processing_algorithm_help(ctx: Context, algorithm_id: str) -> str:
    """Get the help text and parameter list for a processing algorithm.

    Call this before execute_processing to get parameter names right.
    """
    return _call("get_processing_algorithm_help", {"algorithm_id": algorithm_id})


@mcp.tool()
def execute_processing(ctx: Context, algorithm: str, parameters: dict) -> str:
    """Execute a processing algorithm with the given parameters."""
    return _call("execute_processing",
                 {"algorithm": algorithm, "parameters": parameters})


# ---------------------------------------------------------------------
# canvas, rendering and layouts
# ---------------------------------------------------------------------
@mcp.tool()
def get_canvas_state(ctx: Context) -> str:
    """Get the map canvas extent, scale, rotation, CRS and size."""
    return _call("get_canvas_state")


@mcp.tool()
def set_canvas_extent(ctx: Context, xmin: float, ymin: float,
                      xmax: float, ymax: float) -> str:
    """Set the map canvas extent, in project CRS coordinates."""
    return _call("set_canvas_extent",
                 {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax})


@mcp.tool()
def render_map(ctx: Context, path: str, width: int = 800, height: int = 600,
               layer_ids: list = None, dpi: int = 96) -> str:
    """Render the map to an image file.

    Renders the visible canvas layers by default; pass ``layer_ids`` to render
    a specific subset instead.
    """
    params: Dict[str, Any] = {"path": path, "width": width, "height": height,
                              "dpi": dpi}
    if layer_ids:
        params["layer_ids"] = layer_ids
    return _call("render_map", params)


@mcp.tool()
def list_layouts(ctx: Context) -> str:
    """List the print layouts defined in the current project."""
    return _call("list_layouts")


@mcp.tool()
def export_layout(ctx: Context, layout_name: str, path: str,
                  dpi: int = 300) -> str:
    """Export a print layout to PDF or an image, chosen by the file extension."""
    return _call("export_layout",
                 {"layout_name": layout_name, "path": path, "dpi": dpi})


# ---------------------------------------------------------------------
# code execution
# ---------------------------------------------------------------------
@mcp.tool()
def execute_code(ctx: Context, code: str) -> str:
    """Execute arbitrary PyQGIS code inside QGIS.

    Disabled unless 'Allow execute_code' is ticked in the QGIS MCP panel.
    Assign to a variable named ``result`` to return a value.
    """
    return _call("execute_code", {"code": code})


# ---------------------------------------------------------------------
# layer management
# ---------------------------------------------------------------------
@mcp.tool()
def create_memory_layer(ctx: Context, name: str, geometry_type: str = "Point",
                        crs: str = "EPSG:4326", fields: dict = None) -> str:
    """Create an in-memory scratch layer.

    ``fields`` maps name to type, e.g. {"site": "string", "yield": "double"}.
    """
    params: Dict[str, Any] = {"name": name, "geometry_type": geometry_type,
                              "crs": crs}
    if fields:
        params["fields"] = fields
    return _call("create_memory_layer", params)


@mcp.tool()
def reload_layer(ctx: Context, layer_id: str) -> str:
    """Reload a layer from its source, picking up changes made on disk."""
    return _call("reload_layer", {"layer_id": layer_id})


@mcp.tool()
def set_layer_opacity(ctx: Context, layer_id: str, opacity: float) -> str:
    """Set layer opacity, 0.0 (transparent) to 1.0 (opaque)."""
    return _call("set_layer_opacity", {"layer_id": layer_id, "opacity": opacity})


@mcp.tool()
def set_layer_crs(ctx: Context, layer_id: str, authid: str) -> str:
    """Declare a layer's CRS, e.g. 'EPSG:32642'.

    This RELABELS the coordinates; it does not reproject them. Use it to repair
    a layer whose CRS is missing or wrong, only once you know the true CRS.
    """
    return _call("set_layer_crs", {"layer_id": layer_id, "authid": authid})


@mcp.tool()
def export_layer(ctx: Context, layer_id: str, path: str, driver: str = "GPKG",
                 only_selected: bool = False, crs: str = None) -> str:
    """Export a vector layer to a file. Drivers: GPKG, GeoJSON, ESRI Shapefile, CSV."""
    params: Dict[str, Any] = {"layer_id": layer_id, "path": path,
                              "driver": driver, "only_selected": only_selected}
    if crs:
        params["crs"] = crs
    return _call("export_layer", params)


@mcp.tool()
def set_layer_style(ctx: Context, layer_id: str, qml_path: str) -> str:
    """Apply a QGIS style (.qml) file to a layer."""
    return _call("set_layer_style", {"layer_id": layer_id, "qml_path": qml_path})


@mcp.tool()
def save_layer_style(ctx: Context, layer_id: str, qml_path: str) -> str:
    """Save a layer's current style to a .qml file."""
    return _call("save_layer_style", {"layer_id": layer_id, "qml_path": qml_path})


# ---------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------
@mcp.tool()
def count_features(ctx: Context, layer_id: str, expression: str = None) -> str:
    """Count features exactly, optionally matching a QGIS expression.

    Prefer this over the cached feature_count in get_layers, which goes stale
    once a filter is applied.
    """
    params: Dict[str, Any] = {"layer_id": layer_id}
    if expression:
        params["expression"] = expression
    return _call("count_features", params)


@mcp.tool()
def get_unique_values(ctx: Context, layer_id: str, field: str,
                      limit: int = 100, include_counts: bool = True) -> str:
    """List a field's distinct values with occurrence counts, most common first.

    Use this before writing a filter, so the values are matched exactly as
    spelled in the data.
    """
    return _call("get_unique_values", {"layer_id": layer_id, "field": field,
                                       "limit": limit,
                                       "include_counts": include_counts})


@mcp.tool()
def get_field_statistics(ctx: Context, layer_id: str, field: str) -> str:
    """Compute count, distinct, missing, min, max, sum, mean, median and stdev."""
    return _call("get_field_statistics", {"layer_id": layer_id, "field": field})


# ---------------------------------------------------------------------
# filters and editing
# ---------------------------------------------------------------------
@mcp.tool()
def get_layer_filter(ctx: Context, layer_id: str) -> str:
    """Get the layer's current subset filter expression, if any."""
    return _call("get_layer_filter", {"layer_id": layer_id})


@mcp.tool()
def set_layer_filter(ctx: Context, layer_id: str, expression: str) -> str:
    """Filter a layer to features matching a QGIS expression.

    Reports true before/after counts. Example: "crop_class" = 'Sugarcane'.
    """
    return _call("set_layer_filter", {"layer_id": layer_id,
                                      "expression": expression})


@mcp.tool()
def clear_layer_filter(ctx: Context, layer_id: str) -> str:
    """Remove a layer's subset filter, restoring all features."""
    return _call("clear_layer_filter", {"layer_id": layer_id})


@mcp.tool()
def add_features(ctx: Context, layer_id: str, features: list) -> str:
    """Add features to a writable layer.

    Each entry: {"attributes": {...}, "wkt": "POINT(71.5 30.1)"}.
    """
    return _call("add_features", {"layer_id": layer_id, "features": features})


@mcp.tool()
def delete_features(ctx: Context, layer_id: str, expression: str,
                    confirm: bool = False) -> str:
    """Delete features matching an expression. DESTRUCTIVE.

    Defaults to a dry run reporting how many would go; pass confirm=true to
    actually delete. Always dry-run first and show the user the count.
    """
    return _call("delete_features", {"layer_id": layer_id,
                                     "expression": expression,
                                     "confirm": confirm})


@mcp.tool()
def zoom_to_selection(ctx: Context, layer_id: str) -> str:
    """Zoom the canvas to the selected features of a layer."""
    return _call("zoom_to_selection", {"layer_id": layer_id})


# ---------------------------------------------------------------------
# visual output
# ---------------------------------------------------------------------
@mcp.tool()
def render_map_image(ctx: Context, width: int = 800, height: int = 600,
                    layer_ids: list = None, dpi: int = 96):
    """Render the current map and return it as an image you can actually look at.

    Use this to visually verify a result - that a filter selected the right
    area, or that a layer lines up after a CRS repair - instead of saving a
    file the user has to open.
    """
    params: Dict[str, Any] = {"width": width, "height": height, "dpi": dpi}
    if layer_ids:
        params["layer_ids"] = layer_ids
    try:
        response = get_qgis_connection().send_command("render_map_base64", params)
    except QgisConnectionError as exc:
        return json.dumps({"status": "error", "message": str(exc)}, indent=2)
    if response.get("status") != "success":
        return json.dumps(response, indent=2)
    return Image(data=base64.b64decode(response["result"]["base64"]), format="png")




# ---------------------------------------------------------------------
# editing and schema (Phase 3)
# ---------------------------------------------------------------------
@mcp.tool()
def update_features(ctx: Context, layer_id: str, field_values: dict,
                    expression: str = None, confirm: bool = False) -> str:
    """Set field values on matching features. DESTRUCTIVE.

    Dry-runs by default and reports how many rows would change; pass
    confirm=true to apply. Show the user the count before confirming.
    Omit ``expression`` to target every feature.
    """
    params: Dict[str, Any] = {"layer_id": layer_id, "field_values": field_values,
                              "confirm": confirm}
    if expression:
        params["expression"] = expression
    return _call("update_features", params)


@mcp.tool()
def add_field(ctx: Context, layer_id: str, name: str,
              field_type: str = "string", length: int = 0,
              precision: int = 0) -> str:
    """Add an attribute field. Types: string, integer, longlong, double,
    boolean, date, datetime."""
    return _call("add_field", {"layer_id": layer_id, "name": name,
                               "field_type": field_type, "length": length,
                               "precision": precision})


@mcp.tool()
def delete_field(ctx: Context, layer_id: str, name: str,
                 confirm: bool = False) -> str:
    """Drop a field and every value in it. DESTRUCTIVE; dry-runs unless confirmed."""
    return _call("delete_field", {"layer_id": layer_id, "name": name,
                                  "confirm": confirm})


@mcp.tool()
def rename_field(ctx: Context, layer_id: str, old_name: str,
                 new_name: str) -> str:
    """Rename an attribute field."""
    return _call("rename_field", {"layer_id": layer_id, "old_name": old_name,
                                  "new_name": new_name})


@mcp.tool()
def create_spatial_index(ctx: Context, layer_id: str) -> str:
    """Build a spatial index, which speeds up rendering and spatial queries."""
    return _call("create_spatial_index", {"layer_id": layer_id})


# ---------------------------------------------------------------------
# query and diagnostics (Phase 3)
# ---------------------------------------------------------------------
@mcp.tool()
def execute_sql(ctx: Context, query: str, layer_ids: list = None,
                limit: int = 100) -> str:
    """Run SQL across project layers, joining and aggregating them.

    Reference layers by name, e.g. "SELECT district, count(*) FROM crops
    GROUP BY district". The response lists the exposed layer names.
    """
    params: Dict[str, Any] = {"query": query, "limit": limit}
    if layer_ids:
        params["layer_ids"] = layer_ids
    return _call("execute_sql", params)


@mcp.tool()
def group_statistics(ctx: Context, layer_id: str, group_field: str,
                     value_field: str = None, aggregate: str = "count",
                     limit: int = 100) -> str:
    """Aggregate a field grouped by another.

    aggregate: count, sum, mean, min, max, median or stdev. Everything except
    count needs a value_field.
    """
    params: Dict[str, Any] = {"layer_id": layer_id, "group_field": group_field,
                              "aggregate": aggregate, "limit": limit}
    if value_field:
        params["value_field"] = value_field
    return _call("group_statistics", params)


@mcp.tool()
def check_geometry_validity(ctx: Context, layer_id: str, limit: int = 50) -> str:
    """Report invalid or empty geometries, with the reason for each."""
    return _call("check_geometry_validity", {"layer_id": layer_id,
                                             "limit": limit})


@mcp.tool()
def find_duplicates(ctx: Context, layer_id: str, fields: list = None,
                    limit: int = 50) -> str:
    """Find duplicates by attribute values, or by geometry if fields is omitted."""
    params: Dict[str, Any] = {"layer_id": layer_id, "limit": limit}
    if fields:
        params["fields"] = fields
    return _call("find_duplicates", params)


@mcp.tool()
def detect_layer_crs(ctx: Context, layer_id: str,
                     reference_layer_ids: list = None, top: int = 5) -> str:
    """Infer a layer's true CRS from its coordinates when it is missing or wrong.

    Ranks candidates by how well each places the layer over the other layers in
    the project. Confirm visually before applying the answer with set_layer_crs,
    which relabels rather than reprojects.
    """
    params: Dict[str, Any] = {"layer_id": layer_id, "top": top}
    if reference_layer_ids:
        params["reference_layer_ids"] = reference_layer_ids
    return _call("detect_layer_crs", params)


# ---------------------------------------------------------------------
# raster (Phase 3)
# ---------------------------------------------------------------------
@mcp.tool()
def get_raster_statistics(ctx: Context, layer_id: str, band: int = 1) -> str:
    """Band minimum, maximum, mean, standard deviation, range and sum."""
    return _call("get_raster_statistics", {"layer_id": layer_id, "band": band})


@mcp.tool()
def sample_raster_values(ctx: Context, layer_id: str, points: list,
                         band: int = None) -> str:
    """Read raster values at points.

    Points are [{"x": 71.5, "y": 30.1}, ...] or WKT strings, in project CRS.
    Omitting band reads every band.
    """
    params: Dict[str, Any] = {"layer_id": layer_id, "points": points}
    if band:
        params["band"] = band
    return _call("sample_raster_values", params)


@mcp.tool()
def get_audit_log(ctx: Context, limit: int = 50, command: str = None,
                  only_failures: bool = False) -> str:
    """Read the audit trail of commands the bridge has run.

    Use it to answer "what did you change?" after a session, or to see why a
    command was refused.
    """
    params: Dict[str, Any] = {"limit": limit, "only_failures": only_failures}
    if command:
        params["command"] = command
    return _call("get_audit_log", params)


# ---------------------------------------------------------------------
# live data: discovery and places
# ---------------------------------------------------------------------
AOI_DOC = """
``aoi`` accepts any of:
    {"place": "Multan"}  |  {"place": "Multan", "radius_km": 15}
    {"bbox": [west, south, east, north]}  (WGS84 degrees)
    {"layer_id": "..."}  |  {"layer_id": "...", "selected": true}
    {"canvas": true}  |  {"point": [lon, lat], "radius_km": 10}
    {"wkt": "POLYGON((...))", "crs": "EPSG:4326"}
Areas above 5x5 degrees are refused; narrow to the region you need.
"""


def with_aoi_doc(fn):
    """Append the shared area of interest reference to a tool's docstring.

    Applied under ``@mcp.tool()`` so the SDK reads the extended text; a bare
    string concatenation at the top of a function is an expression, not a
    docstring, and the SDK would publish the tool with no description.
    """
    fn.__doc__ = (fn.__doc__ or "").rstrip() + "\n" + AOI_DOC
    return fn



@mcp.tool()
def list_data_sources(ctx: Context, category: Optional[str] = None) -> str:
    """List live data sources the bridge can pull from, grouped by category.

    Categories: imagery, weather, elevation, vector, hazards, basemaps,
    sensors. Each entry says whether it needs a key and what licence applies.
    """
    return _call("list_data_sources", {"category": category} if category else {})


@mcp.tool()
@with_aoi_doc
def resolve_aoi(ctx: Context, aoi: dict) -> str:
    """Preview how an area-of-interest will be interpreted before fetching.
    """
    return _call("resolve_aoi", {"aoi": aoi})


@mcp.tool()
def geocode(ctx: Context, query: str, limit: int = 5,
            country_codes: Optional[str] = None) -> str:
    """Find places by name. Returns lat/lon, bounding box and type for each.

    ``country_codes`` is a comma-separated ISO list, e.g. "pk,in".
    """
    params: Dict[str, Any] = {"query": query, "limit": limit}
    if country_codes:
        params["country_codes"] = country_codes
    return _call("geocode", params)


@mcp.tool()
def reverse_geocode(ctx: Context, lat: float, lon: float, zoom: int = 14) -> str:
    """Describe the place at a coordinate. zoom 10 = city, 14 = suburb, 18 = building."""
    return _call("reverse_geocode", {"lat": lat, "lon": lon, "zoom": zoom})


@mcp.tool()
def add_place_marker(ctx: Context, query: str, name: Optional[str] = None) -> str:
    """Geocode a place and add it as a layer (its boundary polygon when known)."""
    params: Dict[str, Any] = {"query": query}
    if name:
        params["name"] = name
    return _call("add_place_marker", params)


# ---------------------------------------------------------------------
# live data: satellite imagery and indices
# ---------------------------------------------------------------------
@mcp.tool()
@with_aoi_doc
def search_imagery(ctx: Context, aoi: dict, collection: str = "sentinel-2-l2a",
                   start_date: Optional[str] = None,
                   end_date: Optional[str] = None,
                   max_cloud: float = 20, limit: int = 10) -> str:
    """Find satellite scenes over an area, least cloudy first.

    Collections: sentinel-2-l2a (10 m optical, default), sentinel-2-c1-l2a,
    landsat-c2-l2 (30 m, includes thermal), sentinel-1-grd (radar, sees
    through cloud), cop-dem-glo-30. Dates are YYYY-MM-DD; default is the
    last 60 days.
    """
    params: Dict[str, Any] = {"aoi": aoi, "collection": collection,
                              "max_cloud": max_cloud, "limit": limit}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    return _call("search_imagery", params)


@mcp.tool()
def add_imagery(ctx: Context, item_id: str, collection: str = "sentinel-2-l2a",
                assets: Optional[List[str]] = None,
                name: Optional[str] = None) -> str:
    """Stream a scene's bands into the project as cloud-optimised GeoTIFF layers.

    Default asset is the true-colour "visual" image. Other Sentinel-2 assets:
    red, green, blue, nir, swir16, swir22, scl (cloud classes). Nothing is
    downloaded until rendered. Takes 10-20 s for the first open.
    """
    params: Dict[str, Any] = {"item_id": item_id, "collection": collection}
    if assets:
        params["assets"] = assets
    if name:
        params["name"] = name
    return _call("add_imagery", params)


@mcp.tool()
@with_aoi_doc
def compute_spectral_index(ctx: Context, aoi: dict, index: str = "NDVI",
                           item_id: Optional[str] = None,
                           collection: str = "sentinel-2-l2a",
                           expression: Optional[str] = None,
                           mask_clouds: bool = True,
                           start_date: Optional[str] = None,
                           end_date: Optional[str] = None,
                           max_cloud: float = 20,
                           breaks: Optional[List[float]] = None,
                           add_to_project: bool = True) -> str:
    """Compute a spectral index over an area and add it as a styled raster.

    Indices: NDVI, EVI, SAVI (vegetation), NDWI, MNDWI (water), NDMI (crop
    water stress), NDBI (built-up), NBR (burn), BSI (bare soil), or a custom
    ``expression`` over band names, e.g. "(nir - red) / (nir + red + 0.16)".
    Omit item_id to use the least cloudy scene of the last 90 days (not
    necessarily the newest; pass start_date/end_date to pin a period).
    ``breaks`` such as [0.2, 0.4, 0.6] returns the share of pixels per class.
    Reads only the AOI window; expect 10-60 s depending on area.
    """
    params: Dict[str, Any] = {"aoi": aoi, "index": index, "collection": collection,
                              "mask_clouds": mask_clouds, "max_cloud": max_cloud,
                              "add_to_project": add_to_project}
    for key, value in (("item_id", item_id), ("expression", expression),
                       ("start_date", start_date), ("end_date", end_date),
                       ("breaks", breaks)):
        if value is not None:
            params[key] = value
    return _call("compute_spectral_index", params)


@mcp.tool()
@with_aoi_doc
def get_index_time_series(ctx: Context, aoi: dict, index: str = "NDVI",
                          start_date: Optional[str] = None,
                          end_date: Optional[str] = None,
                          collection: str = "sentinel-2-l2a",
                          max_cloud: float = 30, max_scenes: int = 8) -> str:
    """Mean index per scene across a date range - crop growth, drought, flooding.

    Default range is the last 180 days. Each scene costs several seconds and
    the call stops at a time budget, reporting ``truncated`` when it does, so
    keep the AOI small (a few km) and max_scenes modest. Returns the change
    from first to last scene.
    """
    params: Dict[str, Any] = {"aoi": aoi, "index": index, "collection": collection,
                              "max_cloud": max_cloud, "max_scenes": max_scenes}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    return _call("get_index_time_series", params)


# ---------------------------------------------------------------------
# live data: elevation
# ---------------------------------------------------------------------
@mcp.tool()
@with_aoi_doc
def add_dem(ctx: Context, aoi: dict, name: Optional[str] = None) -> str:
    """Stream Copernicus DEM 30 m elevation covering an area, as one layer.
    """
    params: Dict[str, Any] = {"aoi": aoi}
    if name:
        params["name"] = name
    return _call("add_dem", params)


@mcp.tool()
def derive_terrain(ctx: Context, layer_id: str, product: str = "slope",
                   add_to_project: bool = True) -> str:
    """Derive slope, aspect, hillshade, roughness, TRI or TPI from a DEM layer.

    Geographic DEMs are projected to their UTM zone first so slopes are true.
    """
    return _call("derive_terrain", {"layer_id": layer_id, "product": product,
                                    "add_to_project": add_to_project})


# ---------------------------------------------------------------------
# live data: weather and climate
# ---------------------------------------------------------------------
@mcp.tool()
@with_aoi_doc
def get_weather(ctx: Context, aoi: Optional[dict] = None,
                lat: Optional[float] = None, lon: Optional[float] = None,
                kind: str = "current", days: int = 7,
                variables: Optional[List[str]] = None) -> str:
    """Current weather, a forecast, or air quality for a point or area centre.

    kind: current (temperature, humidity, rain, wind, cloud, pressure),
    forecast (daily max/min, rain, wind, ET0, radiation for ``days`` days),
    air_quality (PM2.5, PM10, NO2, O3, dust, US AQI). Pass lat/lon or an aoi.
    ``variables`` are Open-Meteo names for the chosen kind.
    """
    params: Dict[str, Any] = {"kind": kind, "days": days}
    if aoi is not None:
        params["aoi"] = aoi
    if lat is not None and lon is not None:
        params["lat"], params["lon"] = lat, lon
    if variables:
        params["variables"] = variables
    return _call("get_weather", params)


@mcp.tool()
@with_aoi_doc
def get_weather_history(ctx: Context, start_date: str, end_date: str,
                        aoi: Optional[dict] = None, lat: Optional[float] = None,
                        lon: Optional[float] = None,
                        daily: Optional[List[str]] = None,
                        include_rows: bool = True) -> str:
    """Historical daily weather (1940 to now) with per-variable summaries.

    Default variables: temperature max/min, precipitation sum, wind max,
    reference evapotranspiration, shortwave radiation. Dates YYYY-MM-DD.
    Rows are capped at 400; set include_rows=false for the summary alone.
    """
    params: Dict[str, Any] = {"start_date": start_date, "end_date": end_date,
                              "include_rows": include_rows}
    if aoi is not None:
        params["aoi"] = aoi
    if lat is not None and lon is not None:
        params["lat"], params["lon"] = lat, lon
    if daily:
        params["daily"] = daily
    return _call("get_weather_history", params)


@mcp.tool()
@with_aoi_doc
def get_climate_series(ctx: Context, start_date: str, end_date: str,
                       aoi: Optional[dict] = None, lat: Optional[float] = None,
                       lon: Optional[float] = None,
                       parameters: Optional[List[str]] = None,
                       include_rows: bool = True) -> str:
    """NASA POWER daily agro-climatology since 1981: T2M, PRECTOTCORR, RH2M,
    WS2M, ALLSKY_SFC_SW_DWN and more. Built for crop and irrigation work.
    Rows are capped at 400; set include_rows=false for the summary alone.
    """
    params: Dict[str, Any] = {"start_date": start_date, "end_date": end_date,
                              "include_rows": include_rows}
    if aoi is not None:
        params["aoi"] = aoi
    if lat is not None and lon is not None:
        params["lat"], params["lon"] = lat, lon
    if parameters:
        params["parameters"] = parameters
    return _call("get_climate_series", params)


@mcp.tool()
def attach_weather_to_layer(ctx: Context, layer_id: str, kind: str = "current",
                            date: Optional[str] = None,
                            variables: Optional[List[str]] = None,
                            prefix: str = "wx_") -> str:
    """Add weather fields to every feature of a layer, sampled at its centroid.

    kind=current for live conditions, kind=history with a date for that day.
    Up to 100 features per call; the response says if the layer was larger.
    """
    params: Dict[str, Any] = {"layer_id": layer_id, "kind": kind, "prefix": prefix}
    if date:
        params["date"] = date
    if variables:
        params["variables"] = variables
    return _call("attach_weather_to_layer", params)


# ---------------------------------------------------------------------
# live data: basemaps, OpenStreetMap, hazards
# ---------------------------------------------------------------------
@mcp.tool()
def list_basemaps(ctx: Context) -> str:
    """List basemaps: OSM, Esri imagery, topo, CARTO, and NASA GIBS daily
    satellite layers (true colour, NDVI, fires, snow, night lights) by date."""
    return _call("list_basemaps")


@mcp.tool()
def add_basemap(ctx: Context, basemap: str = "osm", date: Optional[str] = None,
                name: Optional[str] = None, opacity: float = 1.0) -> str:
    """Add a tiled basemap underneath the other layers.

    GIBS ids (e.g. viirs-snpp-truecolor, modis-fires) take a date YYYY-MM-DD.
    """
    params: Dict[str, Any] = {"basemap": basemap, "opacity": opacity}
    if date:
        params["date"] = date
    if name:
        params["name"] = name
    return _call("add_basemap", params)


@mcp.tool()
@with_aoi_doc
def fetch_osm_features(ctx: Context, aoi: dict, tags: dict,
                       geometry: str = "center", name: Optional[str] = None,
                       limit: int = 5000) -> str:
    """Fetch OpenStreetMap features by tag inside an area, as a layer.

    tags examples: {"amenity": "hospital"}, {"highway": ["primary",
    "secondary"]}, {"building": "*"}, {"landuse": "farmland"}.
    geometry=center gives one point per feature (fast); geometry=full gives
    real lines and polygons, split into one layer per geometry type.
    Overpass is a shared public service: expect 5-60 s, and narrow the area
    if it reports a timeout.
    """
    params: Dict[str, Any] = {"aoi": aoi, "tags": tags, "geometry": geometry,
                              "limit": limit}
    if name:
        params["name"] = name
    return _call("fetch_osm_features", params)


@mcp.tool()
@with_aoi_doc
def fetch_earthquakes(ctx: Context, period: str = "day",
                      min_magnitude: Optional[float] = None,
                      aoi: Optional[dict] = None,
                      add_to_project: bool = True) -> str:
    """USGS earthquakes for the last hour, day, week or month, optionally
    within an area and above a magnitude. Adds a point layer."""
    params: Dict[str, Any] = {"period": period, "add_to_project": add_to_project}
    if min_magnitude is not None:
        params["min_magnitude"] = min_magnitude
    if aoi is not None:
        params["aoi"] = aoi
    return _call("fetch_earthquakes", params)


@mcp.tool()
@with_aoi_doc
def fetch_active_fires(ctx: Context, aoi: dict, days: int = 1,
                       source: str = "VIIRS_SNPP_NRT") -> str:
    """NASA FIRMS active fire detections for the last 1-10 days in an area.

    Needs a free FIRMS map key stored with set_data_source_key("nasa-firms").
    """
    return _call("fetch_active_fires", {"aoi": aoi, "days": days, "source": source})


@mcp.tool()
def set_data_source_key(ctx: Context, source_id: str, key: str) -> str:
    """Store an API key for a data source that needs one (e.g. nasa-firms).

    The key is saved in QGIS settings, redacted from the audit log, and
    never returned. Prefer pasting it in the QGIS MCP panel when possible.
    """
    return _call("set_data_source_key", {"source_id": source_id, "key": key})


# ---------------------------------------------------------------------
# toolbox: land, soil, water, boundaries, species, sensors, alerts
# ---------------------------------------------------------------------
@mcp.tool()
@with_aoi_doc
def add_dataset(ctx: Context, aoi: dict, dataset: str = "worldcover",
                name: Optional[str] = None, summarise: bool = True) -> str:
    """Add one of the global rasters from list_datasets clipped to the area,
    styled, with a summary: class shares and km² for categorical datasets
    (worldcover, land-use, cci-land-cover, forest), statistics for continuous
    ones (nasadem, alos-dem, copernicus-dem-90, height-above-ground,
    land-surface-temperature, ndvi-modis, leaf-area-index, evapotranspiration,
    net-primary-productivity, snow-cover, burned-area, biomass,
    biodiversity-intactness, electrification) and wet fractions for
    surface-water. WorldCover takes seconds; others 10 to 60 s.
    """
    params: Dict[str, Any] = {"aoi": aoi, "dataset": dataset, "summarise": summarise}
    if name:
        params["name"] = name
    return _call("add_dataset", params)


@mcp.tool()
@with_aoi_doc
def summarise_land_cover(ctx: Context, aoi: dict, dataset: str = "worldcover") -> str:
    """Share and area of each land cover class in the area, without adding a layer.
    """
    return _call("summarise_land_cover", {"aoi": aoi, "dataset": dataset})


@mcp.tool()
@with_aoi_doc
def get_soil_properties(ctx: Context, aoi: Optional[dict] = None,
                        lat: Optional[float] = None, lon: Optional[float] = None,
                        properties: Optional[List[str]] = None,
                        depths: Optional[List[str]] = None) -> str:
    """SoilGrids soil properties at a point: clay, sand, silt, pH, organic
    carbon, nitrogen, CEC, bulk density, water retention, by depth, plus the
    USDA texture class. depths like "0-5cm", "5-15cm", "15-30cm". The service
    can take 10 to 20 s, and returns null values over built up land or water.
    """
    params: Dict[str, Any] = {}
    if aoi is not None:
        params["aoi"] = aoi
    if lat is not None and lon is not None:
        params["lat"], params["lon"] = lat, lon
    if properties:
        params["properties"] = properties
    if depths:
        params["depths"] = depths
    return _call("get_soil_properties", params)


@mcp.tool()
def attach_soil_to_layer(ctx: Context, layer_id: str,
                         properties: Optional[List[str]] = None,
                         depth: str = "0-5cm", max_features: int = 25) -> str:
    """Add topsoil properties and texture class to features, sampled at their
    centroids. SoilGrids is slow, so up to 25 features per call."""
    params: Dict[str, Any] = {"layer_id": layer_id, "depth": depth,
                              "max_features": max_features}
    if properties:
        params["properties"] = properties
    return _call("attach_soil_to_layer", params)


@mcp.tool()
@with_aoi_doc
def get_river_discharge(ctx: Context, aoi: Optional[dict] = None,
                        lat: Optional[float] = None, lon: Optional[float] = None,
                        days: int = 7, past_days: int = 30) -> str:
    """GloFAS river discharge (m3/s) forecast and recent history for the nearest
    river cell. Useful for flood watch and irrigation planning.
    """
    params: Dict[str, Any] = {"days": days, "past_days": past_days}
    if aoi is not None:
        params["aoi"] = aoi
    if lat is not None and lon is not None:
        params["lat"], params["lon"] = lat, lon
    return _call("get_river_discharge", params)


@mcp.tool()
@with_aoi_doc
def get_marine_conditions(ctx: Context, aoi: Optional[dict] = None,
                          lat: Optional[float] = None, lon: Optional[float] = None,
                          days: int = 3) -> str:
    """Wave height, period, direction, sea surface temperature and currents
    for a coastal or offshore point, now and for the next days.
    """
    params: Dict[str, Any] = {"days": days}
    if aoi is not None:
        params["aoi"] = aoi
    if lat is not None and lon is not None:
        params["lat"], params["lon"] = lat, lon
    return _call("get_marine_conditions", params)


@mcp.tool()
@with_aoi_doc
def get_elevation(ctx: Context, points: Optional[List[List[float]]] = None,
                  aoi: Optional[dict] = None, lat: Optional[float] = None,
                  lon: Optional[float] = None) -> str:
    """Elevation in metres at up to 100 [lon, lat] points, or at the area centre."""
    params: Dict[str, Any] = {}
    if points:
        params["points"] = points
    if aoi is not None:
        params["aoi"] = aoi
    if lat is not None and lon is not None:
        params["lat"], params["lon"] = lat, lon
    return _call("get_elevation", params)


@mcp.tool()
@with_aoi_doc
def add_admin_boundaries(ctx: Context, iso3: str, level: str = "ADM1",
                         aoi: Optional[dict] = None, name: Optional[str] = None,
                         simplified: bool = True) -> str:
    """Add a country's administrative units from geoBoundaries as a polygon
    layer. iso3 like PAK, IND, KEN; level ADM0 (country) to ADM3. Pass an aoi
    to keep only units touching it. Returns the unit names.
    """
    params: Dict[str, Any] = {"iso3": iso3, "level": level, "simplified": simplified}
    if aoi is not None:
        params["aoi"] = aoi
    if name:
        params["name"] = name
    return _call("add_admin_boundaries", params)


@mcp.tool()
@with_aoi_doc
def fetch_species_occurrences(ctx: Context, aoi: dict, taxon: Optional[str] = None,
                              limit: int = 1000, year_from: Optional[int] = None,
                              year_to: Optional[int] = None) -> str:
    """GBIF species observation records in the area as a point layer, with the
    most recorded species. taxon is any name: "Aves", "Panthera", "Mangifera indica".
    """
    params: Dict[str, Any] = {"aoi": aoi, "limit": limit}
    if taxon:
        params["taxon"] = taxon
    if year_from:
        params["year_from"] = year_from
    if year_to:
        params["year_to"] = year_to
    return _call("fetch_species_occurrences", params)


@mcp.tool()
@with_aoi_doc
def fetch_air_sensors(ctx: Context, aoi: Optional[dict] = None,
                      lat: Optional[float] = None, lon: Optional[float] = None,
                      radius_km: int = 25) -> str:
    """Live PM2.5, PM10, temperature, humidity and pressure from Sensor.Community
    citizen stations within a radius, as a point layer with a summary.
    """
    params: Dict[str, Any] = {"radius_km": radius_km}
    if aoi is not None:
        params["aoi"] = aoi
    if lat is not None and lon is not None:
        params["lat"], params["lon"] = lat, lon
    return _call("fetch_air_sensors", params)


@mcp.tool()
@with_aoi_doc
def fetch_disaster_alerts(ctx: Context, aoi: Optional[dict] = None,
                          event_types: Optional[List[str]] = None,
                          alert_levels: Optional[List[str]] = None,
                          add_to_project: bool = True) -> str:
    """Current GDACS disaster alerts worldwide or in the area. event_types from
    EQ, TC, FL, DR, VO, WF, TS; alert_levels from green, orange, red.
    """
    params: Dict[str, Any] = {"add_to_project": add_to_project}
    if aoi is not None:
        params["aoi"] = aoi
    if event_types:
        params["event_types"] = event_types
    if alert_levels:
        params["alert_levels"] = alert_levels
    return _call("fetch_disaster_alerts", params)


# ---------------------------------------------------------------------
# toolbox: analysis workflows
# ---------------------------------------------------------------------
@mcp.tool()
@with_aoi_doc
def compute_index_change(ctx: Context, aoi: dict, index: str = "NDVI",
                         before_start: Optional[str] = None,
                         before_end: Optional[str] = None,
                         after_start: Optional[str] = None,
                         after_end: Optional[str] = None,
                         collection: str = "sentinel-2-l2a", max_cloud: float = 20,
                         mask_clouds: bool = True,
                         breaks: Optional[List[float]] = None,
                         add_to_project: bool = True) -> str:
    """Change detection: the least cloudy scene in each of two periods, the
    index difference (after minus before) as a styled raster, and the share of
    the area in loss, stable and gain classes. Takes 30 to 60 s. Defaults: the last 45 days
    against the 45 days before that. Use NDVI for crops and forest, NDWI or
    MNDWI for flooding, NBR for burn scars, NDBI for construction.
    """
    params: Dict[str, Any] = {"aoi": aoi, "index": index, "collection": collection,
                              "max_cloud": max_cloud, "mask_clouds": mask_clouds,
                              "add_to_project": add_to_project}
    for key, value in (("before_start", before_start), ("before_end", before_end),
                       ("after_start", after_start), ("after_end", after_end),
                       ("breaks", breaks)):
        if value is not None:
            params[key] = value
    return _call("compute_index_change", params)


@mcp.tool()
def zonal_index_statistics(ctx: Context, layer_id: str, index: str = "NDVI",
                           item_id: Optional[str] = None,
                           collection: str = "sentinel-2-l2a", max_cloud: float = 20,
                           start_date: Optional[str] = None,
                           end_date: Optional[str] = None,
                           limit: int = 200) -> str:
    """Mean, min, max of an index for every polygon of a layer (fields are added
    to the layer), with the lowest and highest features listed. Takes 20 to 40 s.
    One scene is used, so keep the layer within one Sentinel-2 tile (about
    100 km); the response reports the covered fraction. Ideal for
    per field crop health or per district vegetation. Omit item_id to use the
    least cloudy scene of the last 90 days.
    """
    params: Dict[str, Any] = {"layer_id": layer_id, "index": index,
                              "collection": collection, "max_cloud": max_cloud,
                              "limit": limit}
    for key, value in (("item_id", item_id), ("start_date", start_date),
                       ("end_date", end_date)):
        if value is not None:
            params[key] = value
    return _call("zonal_index_statistics", params)


@mcp.tool()
@with_aoi_doc
def add_imagery_series(ctx: Context, aoi: dict, collection: str = "sentinel-2-l2a",
                       start_date: Optional[str] = None,
                       end_date: Optional[str] = None, max_cloud: float = 20,
                       max_scenes: int = 6, asset: Optional[str] = None) -> str:
    """Add several dated scenes in a group with time ranges, so the QGIS
    Temporal Controller can animate them. Default: the last 180 days.
    """
    params: Dict[str, Any] = {"aoi": aoi, "collection": collection,
                              "max_cloud": max_cloud, "max_scenes": max_scenes}
    for key, value in (("start_date", start_date), ("end_date", end_date),
                       ("asset", asset)):
        if value is not None:
            params[key] = value
    return _call("add_imagery_series", params)


@mcp.tool()
@with_aoi_doc
def export_area_report(ctx: Context, aoi: dict, path: Optional[str] = None,
                       title: Optional[str] = None, include_index: bool = True,
                       include_land_cover: bool = True, include_soil: bool = True,
                       include_weather: bool = True, render_map: bool = True) -> str:
    """Write an HTML briefing for the area: a map, current weather, elevation,
    land cover shares, topsoil properties and NDVI statistics. Takes about a
    minute with the index; returns the file path.
    """
    params: Dict[str, Any] = {"aoi": aoi, "include_index": include_index,
                              "include_land_cover": include_land_cover,
                              "include_soil": include_soil,
                              "include_weather": include_weather,
                              "render_map": render_map}
    if path:
        params["path"] = path
    if title:
        params["title"] = title
    return _call("export_area_report", params)


# ---------------------------------------------------------------------
# advanced toolbox: climate, profiles, 3D, events, monitor
# ---------------------------------------------------------------------
@mcp.tool()
def list_datasets(ctx: Context, category: Optional[str] = None) -> str:
    """List the global rasters add_dataset can clip to an area: land cover
    (WorldCover, Esri land use, CCI, forest), elevation (NASADEM, ALOS, Copernicus
    90 m, 3DEP height above ground), MODIS land surface temperature, NDVI, leaf
    area, evapotranspiration, productivity, snow and burned area, biomass,
    biodiversity intactness, electrification and surface water. Each entry
    gives the id, unit, licence and whether it is categorical or continuous.
    Categories: land cover, elevation, climate, vegetation, water, hazards,
    biodiversity, settlements.
    """
    return _call("list_datasets", {"category": category} if category else {})


@mcp.tool()
@with_aoi_doc
def get_climate_projection(ctx: Context, aoi: Optional[dict] = None,
                           lat: Optional[float] = None, lon: Optional[float] = None,
                           start_year: int = 2030, end_year: int = 2050,
                           variables: Optional[List[str]] = None,
                           models: Optional[List[str]] = None) -> str:
    """Climate projections for a point: yearly means of temperature and rainfall
    from seven CMIP6 HighResMIP models (1950 to 2050) with an ensemble mean and
    a first decade versus last decade trend. Up to 30 years per call.
    """
    params: Dict[str, Any] = {"start_year": start_year, "end_year": end_year}
    if aoi:
        params["aoi"] = aoi
    if lat is not None and lon is not None:
        params.update(lat=lat, lon=lon)
    if variables:
        params["variables"] = variables
    if models:
        params["models"] = models
    return _call("get_climate_projection", params)


@mcp.tool()
@with_aoi_doc
def get_seasonal_forecast(ctx: Context, aoi: Optional[dict] = None,
                          lat: Optional[float] = None, lon: Optional[float] = None,
                          days: int = 90, variables: Optional[List[str]] = None) -> str:
    """Seasonal outlook up to 274 days ahead: ensemble mean, minimum and maximum
    per day plus weekly aggregates, for planting, harvest and water planning.
    """
    params: Dict[str, Any] = {"days": days}
    if aoi:
        params["aoi"] = aoi
    if lat is not None and lon is not None:
        params.update(lat=lat, lon=lon)
    if variables:
        params["variables"] = variables
    return _call("get_seasonal_forecast", params)


@mcp.tool()
@with_aoi_doc
def get_elevation_profile(ctx: Context, line: Optional[str] = None, crs: str = "EPSG:4326",
                          layer_id: Optional[str] = None, selected: bool = True,
                          points: Optional[List[List[float]]] = None,
                          aoi: Optional[dict] = None, direction: str = "west-east",
                          samples: int = 50, add_to_project: bool = False,
                          name: Optional[str] = None) -> str:
    """Elevation along a line: pass LINESTRING WKT, a line layer (its selected
    feature), a list of [lon, lat] points, or an aoi (a west to east or south to
    north transect through its centre). Returns distance and elevation samples,
    gain, loss, mean grade and the path of a chart image; optionally adds the
    sample points as a layer.
    """
    params: Dict[str, Any] = {"crs": crs, "selected": selected, "direction": direction,
                              "samples": samples, "add_to_project": add_to_project}
    for key, value in (("line", line), ("layer_id", layer_id), ("points", points),
                       ("aoi", aoi), ("name", name)):
        if value:
            params[key] = value
    return _call("get_elevation_profile", params)


@mcp.tool()
def make_3d_buildings(ctx: Context, layer_id: str, height_field: Optional[str] = None,
                      levels_field: Optional[str] = None, default_height: float = 10.0,
                      metres_per_level: float = 3.0, colour: str = "#d9c7a3") -> str:
    """Give a building footprint layer a 3D renderer that extrudes each polygon
    by its height tag, or storeys times metres_per_level, or a default. Fields
    are detected automatically (height, building:levels). Shows in 3D map views
    and the elevation profile tool.
    """
    params: Dict[str, Any] = {"layer_id": layer_id, "default_height": default_height,
                              "metres_per_level": metres_per_level, "colour": colour}
    if height_field:
        params["height_field"] = height_field
    if levels_field:
        params["levels_field"] = levels_field
    return _call("make_3d_buildings", params)


@mcp.tool()
@with_aoi_doc
def add_3d_buildings(ctx: Context, aoi: dict, name: Optional[str] = None,
                     default_height: float = 10.0, metres_per_level: float = 3.0) -> str:
    """Fetch OpenStreetMap building footprints for a small area (a town centre,
    a campus) and extrude them for 3D views. Overpass limits the area to about
    one degree squared; a few square kilometres works best.
    """
    params: Dict[str, Any] = {"aoi": aoi, "default_height": default_height,
                              "metres_per_level": metres_per_level}
    if name:
        params["name"] = name
    return _call("add_3d_buildings", params)


@mcp.tool()
def set_terrain(ctx: Context, layer_id: str, vertical_scale: float = 1.0,
                offset: float = 0.0) -> str:
    """Use a raster DEM layer (from add_dem or add_dataset) as the project's
    terrain for 3D views and the Elevation Profile panel.
    """
    return _call("set_terrain", {"layer_id": layer_id, "vertical_scale": vertical_scale,
                                 "offset": offset})


@mcp.tool()
@with_aoi_doc
def add_point_cloud(ctx: Context, aoi: Optional[dict] = None, url: Optional[str] = None,
                    name: Optional[str] = None, max_tiles: int = 4) -> str:
    """Add lidar point clouds: USGS 3DEP COPC tiles covering the area (United
    States) or any https COPC file by url. Layers stream, nothing is downloaded.
    """
    params: Dict[str, Any] = {"max_tiles": max_tiles}
    for key, value in (("aoi", aoi), ("url", url), ("name", name)):
        if value:
            params[key] = value
    return _call("add_point_cloud", params)


@mcp.tool()
def open_3d_view(ctx: Context, name: str = "QGIS MCP 3D",
                 terrain_layer_id: Optional[str] = None, confirm: bool = False) -> str:
    """Open a new 3D map view in the QGIS window, optionally setting a DEM layer
    as terrain first. Refused until confirm=true: creating a 3D canvas from a
    script has crashed QGIS on remote desktops without a real GPU, so ask the
    person to save the project first. Fails when QGIS runs headless.
    """
    params: Dict[str, Any] = {"name": name, "confirm": confirm}
    if terrain_layer_id:
        params["terrain_layer_id"] = terrain_layer_id
    return _call("open_3d_view", params)


@mcp.tool()
@with_aoi_doc
def fetch_natural_events(ctx: Context, aoi: Optional[dict] = None,
                         categories: Optional[List[str]] = None, status: str = "open",
                         days: int = 30, add_to_project: bool = True,
                         name: Optional[str] = None) -> str:
    """NASA EONET natural events (severeStorms, wildfires, floods, volcanoes,
    seaLakeIce, dustHaze, drought, earthquakes, landslides, snow, tempExtremes,
    waterColor, manmade) with their latest position, magnitude and track length.
    status is open, closed or all; days up to 365.
    """
    params: Dict[str, Any] = {"status": status, "days": days, "add_to_project": add_to_project}
    for key, value in (("aoi", aoi), ("categories", categories), ("name", name)):
        if value:
            params[key] = value
    return _call("fetch_natural_events", params)


@mcp.tool()
@with_aoi_doc
def fetch_air_quality_stations(ctx: Context, aoi: Optional[dict] = None,
                               lat: Optional[float] = None, lon: Optional[float] = None,
                               radius_km: float = 25, name: Optional[str] = None,
                               limit: int = 100) -> str:
    """OpenAQ monitoring stations within a radius (25 km maximum) with their
    latest PM2.5, PM10, NO2, O3 and other readings. Needs a free OpenAQ key
    stored with set_data_source_key("openaq", key).
    """
    params: Dict[str, Any] = {"radius_km": radius_km, "limit": limit}
    if aoi:
        params["aoi"] = aoi
    if lat is not None and lon is not None:
        params.update(lat=lat, lon=lon)
    if name:
        params["name"] = name
    return _call("fetch_air_quality_stations", params)


@mcp.tool()
@with_aoi_doc
def start_live_monitor(ctx: Context, aoi: dict, sources: Optional[List[str]] = None,
                       interval_minutes: int = 10, rules: Optional[dict] = None,
                       run_now: bool = True) -> str:
    """Watch an area: refresh earthquakes, air sensors, weather, GDACS alerts and
    NASA events on a timer, keep them as monitor_* layers, and raise alerts in
    the QGIS message bar when thresholds are crossed. rules override defaults,
    e.g. {"earthquakes": {"min_magnitude": 5}, "weather": {"temperature_2m": 38},
    "air_sensors": {"pm2_5": 35}, "disaster_alerts": {"levels": ["red"]}}.
    sources default to all five. One monitor runs at a time.
    """
    params: Dict[str, Any] = {"aoi": aoi, "interval_minutes": interval_minutes, "run_now": run_now}
    if sources:
        params["sources"] = sources
    if rules:
        params["rules"] = rules
    return _call("start_live_monitor", params)


@mcp.tool()
def stop_live_monitor(ctx: Context) -> str:
    """Stop the live monitor timer; monitor_* layers stay in the project."""
    return _call("stop_live_monitor")


@mcp.tool()
def get_live_monitor_status(ctx: Context, limit: int = 50) -> str:
    """Whether the live monitor runs, its configuration, feature counts per
    source, feed errors and the most recent alerts.
    """
    return _call("get_live_monitor_status", {"limit": limit})


# ---------------------------------------------------------------------
# sensors, population and 3D Tiles
# ---------------------------------------------------------------------
@mcp.tool()
@with_aoi_doc
def fetch_river_gauges(ctx: Context, aoi: dict, name: Optional[str] = None) -> str:
    """USGS stream gauges in the area with current discharge (m³/s and ft³/s),
    stage and water temperature, as a point layer. United States only; the
    area must be under 25 square degrees.
    """
    params: Dict[str, Any] = {"aoi": aoi}
    if name:
        params["name"] = name
    return _call("fetch_river_gauges", params)


@mcp.tool()
@with_aoi_doc
def fetch_buoys(ctx: Context, aoi: Optional[dict] = None, name: Optional[str] = None) -> str:
    """NOAA NDBC buoys and coastal stations with their latest wind, wave,
    pressure and sea temperature observation, worldwide; omit aoi for all.
    """
    params: Dict[str, Any] = {}
    if aoi:
        params["aoi"] = aoi
    if name:
        params["name"] = name
    return _call("fetch_buoys", params)


@mcp.tool()
@with_aoi_doc
def fetch_tide_stations(ctx: Context, aoi: dict, name: Optional[str] = None,
                        max_latest: int = 20) -> str:
    """NOAA tide stations in the area with the latest water level above MLLW
    (United States coasts). Readings are fetched for the first max_latest stations.
    """
    params: Dict[str, Any] = {"aoi": aoi, "max_latest": max_latest}
    if name:
        params["name"] = name
    return _call("fetch_tide_stations", params)


@mcp.tool()
@with_aoi_doc
def fetch_aircraft(ctx: Context, aoi: dict, name: Optional[str] = None,
                   max_aircraft: int = 500) -> str:
    """Aircraft positions right now from the OpenSky Network: callsign, country,
    altitude, speed, heading. Anonymous access is rate limited, so call sparingly.
    """
    params: Dict[str, Any] = {"aoi": aoi, "max_aircraft": max_aircraft}
    if name:
        params["name"] = name
    return _call("fetch_aircraft", params)


@mcp.tool()
def list_sensorthings_servers(ctx: Context) -> str:
    """Verified public OGC SensorThings servers (presets) and which entity
    sets can be mapped. Any other server works by url."""
    return _call("list_sensorthings_servers")


@mcp.tool()
def discover_sensorthings(ctx: Context, url: Optional[str] = None,
                          preset: Optional[str] = None) -> str:
    """What a SensorThings server offers: entity sets, location count and a
    sample. Pass url (ending in v1.0 or v1.1) or a preset id.
    """
    params: Dict[str, Any] = {}
    if url:
        params["url"] = url
    if preset:
        params["preset"] = preset
    return _call("discover_sensorthings", params)


@mcp.tool()
@with_aoi_doc
def add_sensorthings_layer(ctx: Context, url: Optional[str] = None, preset: Optional[str] = None,
                           entity: str = "Location", aoi: Optional[dict] = None,
                           feature_limit: int = 1000, expand_to: Optional[str] = None,
                           name: Optional[str] = None, geometry_type: str = "point",
                           snapshot: Optional[bool] = None) -> str:
    """Add a SensorThings entity set (Location, Datastream or FeatureOfInterest)
    as a live layer through QGIS's own provider; with an aoi the server filters
    to the area. expand_to such as "Thing:$top=1" adds related fields.
    geometry_type picks which geometries to show (point, line, polygon and
    their multi forms); stations are points. Servers the provider cannot
    drive (GOST, such as Samen Meten) get a REST snapshot of their Locations
    instead; snapshot=true forces that, false forbids it.
    """
    params: Dict[str, Any] = {"entity": entity, "feature_limit": feature_limit,
                              "geometry_type": geometry_type}
    if snapshot is not None:
        params["snapshot"] = snapshot
    for key, value in (("url", url), ("preset", preset), ("aoi", aoi),
                       ("expand_to", expand_to), ("name", name)):
        if value:
            params[key] = value
    return _call("add_sensorthings_layer", params)


@mcp.tool()
@with_aoi_doc
def estimate_population(ctx: Context, aoi: dict, year: int = 2020, age_sex: bool = False) -> str:
    """How many people live in the area: WorldPop total for the year (2000 to
    2020) and the sum of Meta's 30 m population grid, with a combined estimate
    and density; age_sex adds the WorldPop age and sex pyramid. WorldPop is
    slow: expect one to three minutes, and call once per area.
    """
    return _call("estimate_population", {"aoi": aoi, "year": year, "age_sex": age_sex})


@mcp.tool()
def add_3d_tiles(ctx: Context, url: Optional[str] = None, preset: Optional[str] = None,
                 name: Optional[str] = None) -> str:
    """Add a Cesium 3D Tiles scene for 3D map views: preset "google" (Google
    Photorealistic 3D Tiles, needs a Google Maps Platform key stored with
    set_data_source_key("google-3d-tiles", key)), preset "cesium-sample", or
    any https tileset.json url.
    """
    params: Dict[str, Any] = {}
    for key, value in (("url", url), ("preset", preset), ("name", name)):
        if value:
            params[key] = value
    return _call("add_3d_tiles", params)


# ---------------------------------------------------------------------
# field analytics
# ---------------------------------------------------------------------
@mcp.tool()
@with_aoi_doc
def analyse_fields(ctx: Context, layer_id: str, index: str = "NDVI", selected: Optional[bool] = None,
                   aoi: Optional[dict] = None, limit: int = 300, days: int = 45, compare_days: int = 30,
                   collection: str = "sentinel-2-l2a", max_cloud: int = 25,
                   name: Optional[str] = None, style: bool = True) -> str:
    """Per field analytics for a polygon layer of fields, parcels or blocks:
    mean index now from the newest clear Sentinel-2 scene within `days`, the
    value about `compare_days` earlier, the change, acres, a status (bare or
    harvested, low vigour, declining, healthy, greening) and an attention
    rank, plus 30 day rain and ET0 and a 7 day forecast. Analyses the
    selection when there is one, else the features inside aoi, else the first
    `limit` (max 2000). Writes a NEW styled layer; the source is untouched.
    Takes one to three minutes.
    """
    params: Dict[str, Any] = {"layer_id": layer_id, "index": index, "limit": limit, "days": days,
                              "compare_days": compare_days, "collection": collection,
                              "max_cloud": max_cloud, "style": style}
    if selected is not None:
        params["selected"] = selected
    if aoi:
        params["aoi"] = aoi
    if name:
        params["name"] = name
    return _call("analyse_fields", params)


@mcp.tool()
def field_time_series(ctx: Context, layer_id: str, feature_id: Optional[int] = None,
                      index: str = "NDVI", months: int = 6, max_scenes: int = 10,
                      max_cloud: int = 30, collection: str = "sentinel-2-l2a") -> str:
    """Index history of one field: the selected feature, or feature_id, over the
    last `months`, one value per clear scene, with the change first to last and
    a chart image path.
    """
    params: Dict[str, Any] = {"layer_id": layer_id, "index": index, "months": months,
                              "max_scenes": max_scenes, "max_cloud": max_cloud, "collection": collection}
    if feature_id is not None:
        params["feature_id"] = feature_id
    return _call("field_time_series", params)


@mcp.tool()
@with_aoi_doc
def export_field_report(ctx: Context, layer_id: str, path: Optional[str] = None, index: str = "NDVI",
                        selected: Optional[bool] = None, aoi: Optional[dict] = None, limit: int = 300,
                        days: int = 45, compare_days: int = 30, title: Optional[str] = None) -> str:
    """Run analyse_fields and write an HTML or PDF report (path ending .html or
    .pdf): summary, acres by status chart, weather, the fields needing
    attention and the full table.
    """
    params: Dict[str, Any] = {"layer_id": layer_id, "index": index, "limit": limit, "days": days,
                              "compare_days": compare_days}
    for key, value in (("path", path), ("aoi", aoi), ("title", title)):
        if value:
            params[key] = value
    if selected is not None:
        params["selected"] = selected
    return _call("export_field_report", params)


# ---------------------------------------------------------------------
# entrypoint - keep last: mcp.run() blocks, so any @mcp.tool() defined
# below this point would never be registered when run as a script.
# ---------------------------------------------------------------------
def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
