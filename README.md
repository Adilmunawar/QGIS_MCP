# QGISMCP - QGIS Model Context Protocol Integration

QGISMCP connects [QGIS](https://qgis.org/) to [Claude AI](https://claude.ai/chat) through the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/docs/getting-started/intro), allowing Claude to directly interact with and control QGIS. This integration enables prompt assisted project creation, layer loading, code execution and more.

This project is strongly based on the [BlenderMCP](https://github.com/ahujasid/blender-mcp/tree/main) project by [Siddharth Ahuja](https://x.com/sidahuj)

## Features

- **Two-way communication**: Connect Claude AI to QGIS through a socket-based server.
- **Project manipulation**: Create, load and save projects in QGIS.
- **Layer manipulation**: Add and remove vector or raster layers to a project.
- **Execute processing**: Execute processing algorithms ([Processing Toolbox](https://docs.qgis.org/3.40/en/docs/user_manual/processing/toolbox.html)).
- **Code execution**: Run arbitrary Python code in QGIS from Claude. Very powerful, but also be very cautious using this tool.

## Components

The system consists of two main components:

1. **[QGIS plugin](/qgis_mcp_plugin/)**: A QGIS plugin that creates a socket server within QGIS to receive and execute commands.
2. **[MCP Server](/src/qgis_mcp/qgis_mcp_server.py)**: A Python server that implements the Model Context Protocol and connects to the QGIS plugin.

## Installation

### Prerequisites

- QGIS 3.X (only tested on 3.22)
- Claude desktop
- Python 3.10 or newer
- uv package manager:

If you're on Mac, please install uv as

```bash
brew install uv
```

On Windows Powershell

```bash
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Otherwise installation instructions are on their website: [Install uv](https://docs.astral.sh/uv/getting-started/installation/)

**⚠️ Do not proceed before installing UV**

### Download code

Download this repo to your computer. You can clone it with:

```bash
git clone git@github.com:jjsantos01/qgis_mcp.git
```

### QGIS plugin

You need to copy the folder [qgis_mcp_plugin](/qgis_mcp_plugin/) and its content on your QGIS profile plugins folder.

You can get your profile folder in QGIS going to menu `Settings` -> `User profiles` -> `Open active profile folder` Then, go to `Python/plugins` and paste the folder `qgis_mcp_plugin`.

> On a Windows machine the plugins folder is usually located at: `C:\Users\USER\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins` 

and on MacOS: `~/Library/Application\ Support/QGIS/QGIS3/profiles/default/python/plugins`

 Then close QGIS and open it again. Go to the menu option `Plugins` > `Installing and Managing Plugins`, select the `All` tab and search for "QGIS MCP", then mark the QGIS MCP checkbox.

### Claude for Desktop Integration

Go to `Claude` > `Settings` > `Developer` > `Edit Config` > `claude_desktop_config.json` to include the following:

> If you can't find the "Developers tab" or the `claude_desktop_config.json` look at this [documentation](https://modelcontextprotocol.io/quickstart/user#2-add-the-filesystem-mcp-server).

```json
{
    "mcpServers": {
        "qgis": {
            "command": "uv",
            "args": [
                "--directory",
                "/ABSOLUTE/PATH/TO/PARENT/REPO/FOLDER/qgis_mcp/src/qgis_mcp",
                "run",
                "qgis_mcp_server.py"
            ]
        }

    }
}
```

## Usage

### Starting the Connection

1. In QGIS, go to `plugins` > `QGIS MCP` > `QGIS MCP`
    ![plugins menu](/assets/imgs/qgis-plugins-menu.png)
2. Click "Start Server"
    ![start server](/assets/imgs/qgis-mcp-start-server.png)

### Using with Claude

Once the config file has been set on Claude, and the server is running on QGIS, you will see a hammer icon with tools for the QGIS MCP.

![Claude tools](assets/imgs/claude-available-tools.png)

#### Tools

- `ping` - Simple ping command to check server connectivity
- `get_qgis_info` - Get QGIS information about the current installation
- `load_project` - Load a QGIS project from the specified path
- `create_new_project` - Create a new project and save it
- `get_project_info` - Get current project information
- `add_vector_layer` - Add a vector layer to the project
- `add_raster_layer` - Add a raster layer to the project
- `get_layers` - Retrieve all layers in the current project
- `remove_layer` - Remove a layer from the project by its ID
- `zoom_to_layer` - Zoom to the extent of a specified layer
- `get_layer_features` - Retrieve features from a vector layer with an optional limit
- `execute_processing` - Execute a processing algorithm with the given parameters
- `save_project` - Save the current project to the given path
- `render_map` - Render the current map view to an image file
- `execute_code` - Execute arbitrary PyQGIS code provided as a string

### Example Commands

This is the example I used for the [demo](https://x.com/jjsantoso/status/1900293848271667395):

```plain
You have access to the tools to work with QGIS. You will do the following:
	1. Ping to check the connection. If it works, continue with the following steps.
	2. Create a new project and save it at: "C:/Users/USER/GitHub/qgis_mcp/data/cdmx.qgz"
	3. Load the vector layer: ""C:/Users/USER/GitHub/qgis_mcp/data/cdmx/mgpc_2019.shp" and name it "Colonias".
	4. Load the raster layer: "C:/Users/USER/GitHub/qgis_mcp/data/09014.tif" and name it "BJ"
	5. Zoom to the "BJ" layer.
	6. Execute the centroid algorithm on the "Colonias" layer. Skip the geometry check. Save the output to "colonias_centroids.geojson".
	7. Execute code to create a choropleth map using the "POB2010" field in the "Colonias" layer. Use the quantile classification method with 5 classes and the Spectral color ramp.
	8. Render the map to "C:/Users/USER/GitHub/qgis_mcp/data/cdmx.png"
	9. Save the project.
```

---

## Local fork notes (v3.0.0)

This checkout has been modified from upstream to run on **QGIS 4.x / PyQt6**
and to harden the socket bridge. Verified against QGIS 4.2.0 (Python 3.12,
PyQt6) with `tests/test_plugin_headless.py` — 55 checks, all passing.

### Protocol

Newline-delimited JSON (NDJSON), one document per message, in both directions:

    request   {"type": "<command>", "params": {...}, "id": <optional>}\n
    response  {"status": "success"|"error", "result": ..., "id": <echoed>}\n

v1 clients that send a bare JSON document with no trailing newline are still
accepted, so the change is backward compatible.

### Why the framing changed

Without a delimiter, two requests arriving in one TCP segment look identical
to one truncated request. The v1 loop called `json.loads` on the whole buffer
and treated a parse failure as "incomplete", so concurrent or pipelined
requests corrupted the buffer and the connection had to be rebuilt.

### Other changes

| Area | Change |
| --- | --- |
| Transport | `QTcpServer` (event driven, multi-client) replaces a 100 ms polling loop over a raw non-blocking socket |
| Qt6 | `Qt.DockWidgetArea.RightDockWidgetArea`; scoped enums throughout |
| Metadata | `supportsQt6=True`, `qgisMaximumVersion=4.99` (QGIS defaults the max to `<major>.99`, which marks the plugin incompatible with QGIS 4) |
| Panel | Closing the dock no longer stops the server; the panel is a control surface, not the server's lifetime owner |
| Safety | `execute_code` is gated behind an opt-in checkbox, persisted in `QgsSettings`; the listener binds `127.0.0.1` explicitly |
| Settings | Port, code-execution consent and autostart persist across sessions |
| Robustness | 32 MB per-client buffer cap; JSON encoder for Qt date/NULL/enum values; structured errors carrying `error_type` and a traceback |
| Client | Fixed a liveness check on a non-existent attribute (`.sock` vs `.socket`) that silently forced a reconnect on every call; added timeouts, a lock, and one transparent retry |
| Tools | 15 -> 29 (fields, extents, expression selection, canvas state, processing discovery, layouts) |

### Running the tests

```bash
"C:/Program Files/QGIS 4.2.0/bin/python-qgis.bat" tests/test_plugin_headless.py
```

### v3.0.0 - interface and capability release

**Branding.** A purpose-drawn `icon.svg` (map-sheet stack + MCP node graph),
rasterised to 16/24/32/48/128 px. Used for the toolbar action, plugin menu,
dock title and the Plugin Manager listing via `icon=` in `metadata.txt`.

**Redesigned panel**, now three tabs:

- *Server* - logo header, colour-coded status pill (green running, amber when
  code execution is enabled, red stopped), port and host, start/stop, live
  Clients / Requests / Uptime counters, last command, and a one-click
  "Copy MCP client config" button.
- *Activity* - timestamped rolling log of connections, errors and toggles,
  capped at 500 lines.
- *Settings* - the `execute_code` consent toggle with an inline warning,
  autostart, and "Open plugin folder".

**New commands (15).** 31 -> 46 total:

| Command | Purpose |
| --- | --- |
| `create_memory_layer` | scratch layers with typed fields |
| `add_features` / `delete_features` | editing; delete dry-runs unless `confirm=true` |
| `set_layer_filter` / `get_layer_filter` / `clear_layer_filter` | subset filters with true before/after counts |
| `count_features` | exact counts, bounded by `max_scan` |
| `get_unique_values` | distinct values with counts, most common first |
| `get_field_statistics` | count/distinct/missing/min/max/sum/mean/median/stdev |
| `set_layer_crs` | declare a CRS (relabels, never reprojects) |
| `export_layer` | write to GPKG/GeoJSON/Shapefile/CSV |
| `set_layer_style` / `save_layer_style` | load and save `.qml` |
| `set_layer_opacity`, `reload_layer`, `zoom_to_selection` | display control |
| `render_map_base64` | PNG returned inline, so a client can *see* the map |

`render_map_image` on the MCP side returns real `ImageContent`, letting the
model look at the map rather than writing a file for a human to open.

**Correctness notes.** Expressions are parsed and rejected before use rather
than failing mid-iteration. Counting iterates with `NoGeometry` and no
attributes instead of trusting `featureCount()`, which is a provider-cached
estimate that goes stale the moment a subset string is applied.
