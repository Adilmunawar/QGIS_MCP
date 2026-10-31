# Contributing to QGIS MCP

Thank you for helping improve QGIS MCP, a permission gated [Model Context Protocol](https://modelcontextprotocol.io/) server for [QGIS](https://qgis.org/). Bug reports, tool ideas, docs and code are all welcome. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Project layout

| Path | What it is |
| --- | --- |
| `qgis_mcp_plugin/` | The QGIS plugin: socket server, permissions, audit log, panel, command handlers |
| `qgis_mcp_plugin/permissions.py` | Risk categories, profiles and the table mapping commands to categories |
| `qgis_mcp_plugin/command_help.py` | One-line description for every command |
| `src/qgis_mcp/qgis_mcp_server.py` | The MCP server and its `@mcp.tool()` definitions |
| `tests/` | Headless tests that run inside QGIS's Python |

## Getting set up

1. Fork and clone:
   ```bash
   git clone git@github.com:YOUR-USERNAME/qgis_mcp.git
   cd qgis_mcp
   ```
2. Install prerequisites: **QGIS 4.x**, **Python 3.12+**, [uv](https://docs.astral.sh/uv/getting-started/installation/), and an MCP client such as Claude Desktop.
3. Symlink the plugin into your QGIS 4 profile.

   Windows (PowerShell):
   ```powershell
   $src = "$(pwd)\qgis_mcp_plugin"
   $dst = "$env:APPDATA\QGIS\QGIS4\profiles\default\python\plugins\qgis_mcp_plugin"
   New-Item -ItemType SymbolicLink -Path $dst -Target $src
   ```
   macOS:
   ```bash
   ln -s "$(pwd)/qgis_mcp_plugin" "$HOME/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins/qgis_mcp_plugin"
   ```
   Linux:
   ```bash
   ln -s "$(pwd)/qgis_mcp_plugin" "$HOME/.local/share/QGIS/QGIS4/profiles/default/python/plugins/qgis_mcp_plugin"
   ```
4. Restart QGIS, enable **QGIS MCP** in `Plugins` > `Manage and Install Plugins`, and click **Start Server** in the panel.
5. Point your MCP client at `src/qgis_mcp/qgis_mcp_server.py` (see the README for config snippets and the `QGIS_MCP_*` environment variables).

## Running the tests

Tests are headless and run in QGIS's own Python. On Windows:

```bat
"C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat" tests\test_plugin_headless.py
"C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat" tests\test_permissions_headless.py
"C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat" tests\test_data_commands_headless.py
"C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat" tests\test_lifecycle_headless.py
"C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat" tests\test_live_data_headless.py
"C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat" tests\test_explorer_headless.py
"C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat" tests\test_toolbox_headless.py
"C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat" tests\test_advanced_headless.py
```

The tests import the plugin from your installed QGIS 4 profile, so make sure the symlink points at your working copy. Please run all four before opening a pull request.

## Adding or changing a tool

A new command touches several places; keep them consistent:

1. Implement the handler in the plugin (`qgis_mcp_plugin/`).
2. Add it to `COMMAND_CATEGORIES` in `permissions.py` with the **lowest accurate** risk category (read, write, destructive, code). Anything missing defaults to destructive.
3. Add a short description in `command_help.py`.
4. Add the `@mcp.tool()` wrapper in `src/qgis_mcp/qgis_mcp_server.py`, above the `main()` entrypoint.
5. Add or update headless tests.
6. Update the README tool table and the CHANGELOG.

Design rules:

- Destructive operations should **dry run by default** and require `confirm=true`.
- Never block the GUI thread; approvals are non blocking by design.
- Validate expressions and inputs before use and return structured errors.
- Keep the loopback only default and do not weaken authentication.

## Pull requests

- Keep each PR focused on one change.
- Write clear commit messages (imperative mood, explain the why).
- Update docs and the CHANGELOG when behaviour changes.
- Fill in the pull request template and link related issues.

## Reporting issues

Use [GitHub Issues](https://github.com/adilmunawar/qgis_mcp/issues) and the templates. Include your OS, QGIS version, plugin version and relevant audit-log or panel output. For security problems, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Credits

This project is a fork of [jjsantos01/qgis_mcp](https://github.com/jjsantos01/qgis_mcp), itself inspired by [BlenderMCP](https://github.com/ahujasid/blender-mcp). Contributions are licensed under the MIT License.
