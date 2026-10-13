#!/usr/bin/env python3
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
from typing import Any, AsyncIterator, Dict, Optional

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
DEFAULT_TIMEOUT = float(os.environ.get("QGIS_MCP_TIMEOUT", "120.0"))
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


def _call(command: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Run one command and render the response as JSON text for the model."""
    try:
        result = get_qgis_connection().send_command(command, params)
    except QgisConnectionError as exc:
        return json.dumps({"status": "error", "message": str(exc)}, indent=2)
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
    instructions="QGIS integration through the Model Context Protocol",
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
# entrypoint - keep last: mcp.run() blocks, so any @mcp.tool() defined
# below this point would never be registered when run as a script.
# ---------------------------------------------------------------------
def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
