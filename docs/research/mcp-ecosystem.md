# MCP ecosystem research for qgis_mcp (as of 2026-10-31)

Method: WebFetch/WebSearch of official docs plus a local `curl` of PyPI JSON and `py.sdk.modelcontextprotocol.io/llms-full.txt`. Items I could not confirm are marked UNVERIFIED. Third-party directory pages (Glama, LobeHub, etc.) are secondary and often stale.

## 0. Headline finding: the SDK moved to v2 and FastMCP was renamed

- PyPI `mcp`: latest **2.3.0** (2026-10-02). Releases: 2.0.0 (2026-07-28), 2.1.0/2.1.1 (Aug 24-25), 2.0.1 (Aug 26), 2.2.0 (2026-09-07). The 1.x line is still maintained: **1.30.0** (2026-09-07), 1.29.x before it. PyPI metadata says `requires_python >=3.10`, but the v2 docs say "Python 3.11+" (conflict; trust the docs; our `>=3.12` is fine).
- Repo state: `pyproject.toml` has `mcp[cli]>=1.3.0` (open-ended) and `uv.lock` pins 1.3.0. The only SDK import is `from mcp.server.fastmcp import Context, FastMCP, Image` in `src/qgis_mcp/qgis_mcp_server.py`. **An unlocked resolve (for example `uvx --from <zip>` or `uv lock --upgrade`) will pull 2.x, where `mcp.server.fastmcp` raises `ModuleNotFoundError`.** Add an upper bound (`mcp[cli]>=1.30,<2`) now, then migrate deliberately.
- v2 renames: `FastMCP` -> `MCPServer`; `mcp.server.fastmcp.*` -> `mcp.server.mcpserver.*`; `ctx.fastmcp` -> `ctx.mcp_server`; `FastMCPError` -> `MCPServerError`. The docs show two import spellings for the class: `from mcp.server import MCPServer` (README, whats-new, tool tutorials) and `from mcp.server.mcpserver import MCPServer, Context` (migration guide). Both appear in the docs; confirm on the installed version. UNVERIFIED which is canonical for `Context`/`Image`.
- Structured output, elicitation and Streamable HTTP all arrived well after 1.3.0 (from memory, exact versions UNVERIFIED), so upgrading from 1.3.0 is needed for most of section 1 regardless of v1 vs v2.

## 1. SDK / FastMCP API (v2 unless noted)

### Server and transports
```python
from mcp.server import MCPServer          # v1: from mcp.server.fastmcp import FastMCP
mcp = MCPServer("qgis", instructions="Drives a running QGIS session.", version="x.y.z")  # pass version; default is now ""

if __name__ == "__main__":
    mcp.run()                                                        # stdio (default)
    # mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)
```
- v2: `host`, `port`, `stateless_http`, `json_response`, `streamable_http_path`, `transport_security`, `max_request_body_size` (default 4 MiB) are `run()` kwargs, not constructor args (`MCPServer("x", port=9000)` is a `TypeError`). `mount_path` removed. `MCP_*` env vars and `.env` are no longer read.
- v1: `FastMCP("x", host=..., port=...)` then `mcp.run(transport="streamable-http")`.
- CLI: `uv run mcp run server.py --transport streamable-http`.
- The default HTTP bind has DNS-rebinding protection (Host must be localhost). Behind a real hostname pass `transport_security=TransportSecuritySettings(allowed_hosts=[...], allowed_origins=[...])` from `mcp.server.transport_security`.
- **"stdio server also serves Streamable HTTP":** per the docs one `run()` call serves one transport. What v2 does serve at once is both protocol revisions (2025-era `initialize` clients and 2026-07-28 clients) on the same app/stdio server. To offer both transports from one entry point, add a CLI flag or env var (`QGIS_MCP_TRANSPORT=stdio|http`) that selects the `run()` arguments, or mount the ASGI app:
```python
import contextlib
from starlette.applications import Starlette
from starlette.routing import Mount

@contextlib.asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield

app = Starlette(routes=[Mount("/", app=mcp.streamable_http_app(json_response=True))], lifespan=lifespan)
```
  Running stdio and HTTP concurrently in one process is not documented: UNVERIFIED. gis-mcp uses the env-flag approach (`GIS_MCP_TRANSPORT=http`, Docker on :9010/mcp).

### Tool annotations (hints only, never security)
```python
from mcp.types import ToolAnnotations

@mcp.tool(
    title="Search the catalog",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False,
                                destructive_hint=False, idempotent_hint=True),
)
def search_books(query: str) -> str: ...
```
- Python attributes are snake_case; wire names are `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`. Per the docs, destructive/idempotent are only meaningful when the tool is not read-only. Spec: clients MUST treat annotations as untrusted unless the server is trusted.

### Structured output / output schemas
- The return annotation drives it: `-> int`, `-> dict`, `-> list[T]`, a Pydantic model or TypedDict produce `outputSchema` and `structuredContent`; returns are validated against the schema. Scalars/lists are wrapped (`{"result": 3}`). Opt out with `@mcp.tool(structured_output=False)`.
- Spec: if `outputSchema` is declared the server MUST conform; a tool returning structured content SHOULD also serialise it into a TextContent block.
- Images: `Image` helper (we import it from `mcp.server.fastmcp` for `render_map`); v2 path UNVERIFIED.

### Resources, templates, prompts
```python
@mcp.resource("qgis://project/layers", mime_type="application/json")
def layers() -> str: ...

@mcp.resource("qgis://layer/{layer_id}/schema")      # template: URI vars map to function args
def layer_schema(layer_id: str) -> str: ...

@mcp.prompt()
def style_layer(layer_name: str) -> str:
    return f"Inspect {layer_name}, then propose a categorized style."
```
- Missing resource returns JSON-RPC `-32602` (`ResourceNotFoundError`). v2 adds an optional `security=` kwarg on `@mcp.resource()`. A static URI plus a Context-only handler now errors at decoration time (use a query template such as `"notes://recent{?limit}"`).

### Context: progress, logging, elicitation (v2 changes matter)
```python
from mcp.server.mcpserver import Context     # v1: mcp.server.fastmcp

@mcp.tool()
async def run_algo(name: str, ctx: Context) -> str:     # ctx must be a parameter; get_context() removed
    await ctx.report_progress(1, 4, "buffering")         # absolute values, not deltas
    return "ok"
```
- **Progress**: supported, server-to-client only.
- **Logging**: `ctx.info/debug/warning/error/log` still work with 2025-era sessions but are **deprecated (SEP-2577)** and emit `MCPDeprecationWarning`; first arg is now `data: Any`, `extra=` removed; `logging/setLevel` removed at 2026-07-28. Use stdlib `logging` (stderr) for server logs; keep `ctx.info` only as a legacy nicety. Note nkarasiak's fork reports long-running progress via MCP logging, which is on the deprecation path.
- **Elicitation**: preferred v2 pattern is a `Resolve(fn)` parameter; the SDK uses live elicitation for legacy clients and multi-round-trip (`InputRequiredResult`) for 2026-07-28 clients:
```python
from typing import Annotated
from pydantic import BaseModel
from mcp.server.mcpserver import Elicit, ElicitationResult, AcceptedElicitation, Resolve

class Confirm(BaseModel):
    proceed: bool

async def ask() -> Elicit[Confirm]:
    return Elicit("This deletes the layer. Continue?", Confirm)

@mcp.tool()
async def remove_layer(layer_id: str, ok: Annotated[ElicitationResult[Confirm], Resolve(ask)]) -> str:
    if isinstance(ok, AcceptedElicitation) and ok.data.proceed:
        ...
```
  `ctx.elicit()` raises `NoBackChannelError` on 2026-07-28 connections; `Client(mcp)` in tests negotiates 2026 by default, so pin `mode="legacy"` in tests that still use it. Sampling and roots are also deprecated. How many real clients support elicitation is UNVERIFIED, so keep our own plugin-side permission gate as the real control.
- Other v2 items: sync (`def`) tools run on a worker thread (no `get_running_loop()`); `ToolError` / `CallToolResult(is_error=True)` for model-visible failures while `MCPError` becomes a JSON-RPC error; lifespan is entered once and shared across sessions; `ServerSession` proxy is per message (do not key state on it); optional `subscriptions/listen` change notifications (`ctx.notify_tools_changed()`, `notify_resource_updated(uri)`); `cache_hints=` (ttlMs/cacheScope); `x-mcp-header` parameter mirroring; 2026 requests carry no `Mcp-Session-Id`.

## 2. Other geospatial MCP servers

| Project | Shape | Tool surface (as reported) | Notes |
|---|---|---|---|
| jjsantos01/qgis_mcp (upstream of our fork) | QGIS plugin socket server + FastMCP server | small set (ping, layers, execute_code, render, processing); counts vary by directory (UNVERIFIED) | our code descends from this; plugin metadata still names jjsantos01 and a placeholder email |
| nkarasiak/qgis-mcp | same architecture, FastMCP, QGIS 3.28-4.x | **125 granular tools** (Layers 17, Features 10, Processing 11, Layouts 11, Rendering 7, Bookmarks/themes 7, Editing 6, Styling 6, Connections 6, Layer tree 5, Project 5, Session 4, Fields 4, Analysis 4, Query 3, Plugins 3, Atlas 2, System 14) and a **compound mode** (`QGIS_MCP_TOOL_MODE=compound`, about 25 `action`+`params` group tools) | has `execute_processing_batch`, async `start/get/cancel_processing_job`, `batch_commands`, `create/restore_checkpoint`, `undo_edits/redo_edits`, `get_3d_screenshot`, multi-instance (`QGIS_MCP_INSTANCES`, per-tool `instance` arg), `diagnose` version-match check, `QGIS_MCP_TOKEN` shared secret (constant-time compare; plugin refuses non-loopback without token), `QGIS_MCP_AUTO_CONFIRM`, read-only/destructive/idempotent annotations, `execute_code` called out as most sensitive. Distributed via QGIS Plugin Manager plus `uvx --from <github zip>`. 402 stars. No registry listing mentioned. |
| mahdin75/gis-mcp | standalone library wrapper, no QGIS | Shapely 29, PyProj 13, GeoPandas 13, Rasterio 20, PySAL 18, viz 2 (Matplotlib, Folium), plus small boundary/climate/ecology/movement/land-cover/satellite groups; "92+" claimed, categories sum to 104 | stdio default, HTTP via env var, Docker :9010/mcp, upload/download/list storage endpoints; no resources/prompts mentioned |
| Esri ArcGIS Location Services MCP (official, hosted, beta) | remote server | geocoding, routing, elevation, static maps | Esri says Enterprise/Online MCP is coming; no official ArcGIS Pro MCP found. Third-party `arcgis-mcp-server` (PyPI, Esri Saudi Arabia namespace; official status UNVERIFIED) searches portal content with env credentials. Third-party ArcGIS Pro MCP plugins exist. |
| FrontierDevelopmentLab/gee-mcp (Python), Dhenenjay/earth-engine-mcp (Node), cameronking4/google-earth-engine-mcp | Earth Engine | dataset search/metadata, statistics, NDVI workflows, export to GCS, code generation | service-account auth required |
| BnJam/stac-mcp (PyPI `stac-mcp`; wayfinder-foundry fork also listed) | STAC client | search collections/items, get item, estimate data size, asset/variable metadata | uvx/Docker/pip; canonical repo UNVERIFIED |
| GRABOSM/osm-mcp (30 tools), ogSINGH/open-streetmap-mcp-v2, RigaOnTheRocks/overpass-mcp (single `overpass_query`), Pipeworx hosted Overpass | OSM | geocoding, routing, QA, `execute_overpass_query` | all recommend an own/mirror Overpass for heavy use |

### What we lack relative to peers
1. Annotations on every tool and a documented read-only/destructive taxonomy (verify what exists; 1.3.0-era code likely predates them).
2. Compound/grouped tool mode to cut schema tokens, and a way to scope the visible tool set.
3. Async processing jobs with progress and cancel, batch processing, checkpoint/undo for edits.
4. Shared-secret token on the plugin socket and refusal to bind non-loopback without it; `diagnose` version-mismatch tool.
5. Multi-instance support.
6. Layout/atlas/print tools, bookmarks/themes, layer-tree tools, connection management (PostGIS, WFS, etc.).
7. MCP resources/prompts (no peer seems to use them, so this is a differentiator): project summary, layer schema templates, CRS help, workflow prompts.
8. Data acquisition (STAC/Overpass/Earth Engine): leave to dedicated servers and document composition rather than reimplementing.
9. Registry presence (MCP registry, Glama, Smithery) and a QGIS Hub listing.

## 3. Discovery and distribution

### Official MCP Registry (registry.modelcontextprotocol.io, preview status)
1. Publish a PyPI package (the registry hosts metadata only; supported package registries: PyPI, npm, NuGet, crates.io, OCI/Docker, MCPB).
2. Prove ownership: the PyPI README must contain `mcp-name: <server name>` (an HTML comment works on PyPI), for example `<!-- mcp-name: io.github.adilmunawar/qgis-mcp -->`.
3. Install `mcp-publisher` (release binary, or `brew install mcp-publisher`; Windows: `mcp-publisher_windows_amd64.tar.gz` from the registry releases).
4. `mcp-publisher init` creates `server.json`; `mcp-publisher login github` (device flow); `mcp-publisher publish`. Verify with `curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=<name>"`.
5. With GitHub auth the name MUST start with `io.github.<github-username>/`; custom domains need DNS auth. GitHub Actions automation is documented.
```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.github.adilmunawar/qgis-mcp",
  "title": "QGIS MCP",
  "description": "Permission-gated MCP server that drives a running QGIS session.",
  "repository": {"url": "https://github.com/adilmunawar/qgis_mcp", "source": "github"},
  "version": "1.0.0",
  "packages": [{
    "registryType": "pypi", "identifier": "qgis-mcp-server", "version": "1.0.0",
    "runtimeHint": "uvx", "transport": {"type": "stdio"},
    "environmentVariables": [{"name": "QGIS_MCP_TOKEN", "description": "Shared secret for the QGIS plugin socket", "isSecret": true, "isRequired": false}]
  }]
}
```
`name` must equal the README `mcp-name`. The package identifier, version numbers and GitHub username are my placeholders (verify). `runtimeHint: uvx` is shown for PyPI in the docs' runtime-model discussion but I did not see it in the PyPI example (UNVERIFIED). Remote servers use `remotes[]` (`type: streamable-http`, `url`) and must be publicly reachable; a local QGIS bridge is not a remote candidate. PyPI name availability not checked (UNVERIFIED). Current `pyproject.toml` still says name `qgis-mcp`, version 0.1.0, description "Add your description here".

### Directories
- awesome-mcp-servers (punkpeye): only public GitHub repos that users install and run themselves; one line per server, name linked to the repo plus a short description; place under the right category (alphabetical within category; exact geospatial category name UNVERIFIED); match existing formatting; fork, branch, edit README.md, open PR; agents may add the three-robot emoji to the PR title to opt into fast-track. The emoji legend and any Glama-badge requirement could not be fetched: UNVERIFIED, read the README before submitting.
- Glama: auto-indexes open-source GitHub MCP servers; claim via `/.well-known/glama.json` on your domain (from Glama FAQ via a secondary source). Smithery: `smithery mcp publish "<url>" -n org/name` or dashboard (secondary source, UNVERIFIED; a local stdio-only server may need a different route). PulseMCP: sources conflict (submit button, GitHub PR, or auto-sync from the official registry), UNVERIFIED; publish to the official registry first and see whether it appears.
- GitHub topics (practice, not verified data): `mcp`, `mcp-server`, `model-context-protocol`, `qgis`, `qgis-plugin`, `gis`, `geospatial`, `pyqgis`, `claude`, `llm`. Also a social-preview image, 30-second install in README, demo GIF, tagged releases.

### QGIS official plugin repository (plugins.qgis.org)
- Need an OSGeo ID; upload a zip with a single top-level folder (ASCII letters, digits, underscore, hyphen; not starting with a digit). Mandatory files: `metadata.txt`, `__init__.py`, `LICENSE` (exact name, no extension). No generated/junk files (`.gitignore`, `ui_*.py`, `resources_rc.py`). Replace the default icon. `version` must be unique per upload. Uploads by non-trusted users are unapproved until staff approve; staff can grant `plugins.can_approve`. Avoid names similar to existing plugins.
- metadata.txt (docs table): required `name, qgisMinimumVersion, description, about, version, author, email, repository`; optional `qgisMaximumVersion, tracker, homepage, icon, tags, category, experimental, deprecated, server, hasProcessingProvider, changelog, plugin_dependencies`. Category must be one of Raster, Vector, Database, Mesh, Web (ours is `Plugins`: fix). `supportsQt6` is not in the docs table I fetched: UNVERIFIED as an official field.
- Gaps in `qgis_mcp_plugin/metadata.txt`: placeholder email `tu_correo@example.com`; author `jjsantos`; homepage/tracker/repository point at jjsantos01's repo; `about` says "Patched locally"; no `tags`, no `changelog`; plugin `version=4.0.0` unrelated to server `0.1.0`; `qgisMinimumVersion=3.22` (confirm real minimum); `about` sits in an `[about]` section while the docs table lists it as a normal field (UNVERIFIED how the repo parser treats it). The zip needs a `LICENSE` file and the upstream license must be honoured. A "QGIS MCP" plugin already exists, so pick a distinct name.

## 4. LLM tool-design best practices
Sources: Anthropic "Writing effective tools for agents" (fetched) and the MCP tools spec.
- Few, high-impact, non-overlapping tools; merge chained steps into one call; prefer targeted search to list-everything. nkarasiak's 125 tools sit at the other extreme and it added compound mode to cut schema overhead. There is no official tool-count limit that I found (UNVERIFIED); a practical target is a default surface under about 30-40 tools with the long tail grouped or opt-in. Some clients cap total tools (which ones UNVERIFIED).
- Names: snake_case, verb_noun, shared prefixes (`layer_*`, `processing_*`); spec: 1-128 chars, `[A-Za-z0-9_.-]`, case-sensitive, unique; aggregating clients may prefix the server name. Test prefix vs suffix naming with the target model.
- Descriptions: write like onboarding a new teammate (units, CRS expectations, ID formats, side effects, what to call first/next). Unambiguous parameter names (`layer_id`, not `id`). Keep tool ordering deterministic (spec SHOULD) for prompt-cache hits. Measure description edits with an eval.
- Responses: human-readable names; IDs only where follow-up calls need them; a `response_format: "concise"|"detailed"` enum; sensible defaults and caps (Claude Code caps tool output at 25,000 tokens by default); on truncation tell the model how to narrow the query. Add `limit`/`offset` or cursors to layer, feature, field-value and algorithm listings; never return whole attribute tables by default.
- Resources vs tools: resources for read-only, addressable, cacheable context (project summary, `qgis://layer/{id}/schema`, CRS info, algorithm help); tools for actions and for parameterised queries. Many clients do not surface resources to the model automatically (UNVERIFIED), so keep a tool fallback for anything essential.
- Errors: the spec separates protocol errors (unknown tool, malformed request) from tool execution errors (`isError: true`) that the model can act on. Say what failed, why, and a corrected example ("Layer 'roads' not found. Available: roads_2020, rivers. Call get_layers."). No stack traces to the model; log them server-side. In the SDK use `ToolError`.
- Stateful handles (jobs, checkpoints): per the spec's non-normative guidance, return opaque random IDs, state their lifetime in the creating tool's description, and return a clear "expired or unknown" execution error.

## 5. Security for servers that execute code or touch files
Spec sources: Tools page ("User Interaction Model", "Security Considerations"); Security Best Practices page ("Local MCP Server Compromise", "Token Passthrough", "Confused Deputy", "SSRF", "State Handle Hijacking", "Scope Minimization").
- Tools spec: servers MUST validate all inputs, implement access controls, rate-limit invocations, and sanitize outputs. Clients SHOULD prompt for confirmation on sensitive operations, show tool inputs to the user, validate results, apply timeouts and log tool usage for audit. There SHOULD always be a human in the loop who can deny invocations. Annotations are untrusted unless the server is trusted.
- Local servers: use stdio where possible; if HTTP, require an authorization token or use a Unix domain socket or other restricted IPC; run with minimal privileges (the server has the client's privileges); DNS rebinding against localhost servers is a named attack. Our plugin's TCP socket inside QGIS is exactly this surface (any local process can connect). Match nkarasiak: loopback only, mandatory token with constant-time compare, refuse non-loopback without a token, and state plainly that it is not a sandbox against same-user processes.
- Token passthrough is forbidden: if HTTP auth is ever added, validate that tokens were issued for this server (audience) and never forward client tokens downstream.
- State handles are not credentials: random, expiring, bound to the caller.
- Practical controls for `execute_code`-style tools (my recommendations, not spec text): allowed roots for every path argument (resolve symlinks, reject `..`, deny outside project/data dirs); arbitrary PyQGIS behind an explicit env opt-in and marked destructive + open-world; in-QGIS confirmation dialog for destructive, code-exec and file-writing actions (do not depend on client elicitation or annotations); append-only audit log (timestamp, tool, argument hash, decision, status; redact secrets), and verify `audit.py` and `permissions.py` cover every command path; rate limits, output size caps, timeouts and cancellation; progressive least-privilege permissions (mirrors the spec's Scope Minimization guidance).
- SSRF: tools that fetch URLs (WFS/XYZ/connection tools) should require HTTPS, block private/link-local ranges and cloud metadata endpoints, and not follow redirects blindly (spec lists these as client mitigations; apply server-side too). Do not hand-roll IP parsing.
- Supply chain: the registry points users at your PyPI package, so use trusted publishing, pinned dependencies and signed tags.

## Top 10 recommendations
1. **Bound the dependency now**: `mcp[cli]>=1.30,<2` (the lock is 1.3.0 and `>=1.3.0` is open-ended; v2 removes `mcp.server.fastmcp`). Then plan a v2 migration (`MCPServer`, `ctx: Context` parameter, `run(host=, port=)`), perhaps via a small import shim.
2. **Upgrade off 1.3.0** to get structured output/outputSchema, annotations, Streamable HTTP and elicitation; add `ToolAnnotations` (`read_only_hint`, `destructive_hint`, `idempotent_hint`, `open_world_hint`) and `title` to every tool, with tests asserting them.
3. **Harden the plugin socket**: `QGIS_MCP_TOKEN`, constant-time compare, refuse non-loopback without a token; document the same-user limitation. Keep and extend the in-QGIS permission gate and audit log so nothing relies on client hints.
4. **Gate `execute_code`**: off by default, explicit opt-in, always confirmed in the QGIS UI; allowed roots for all file tools; output and time limits.
5. **Cut tool surface and tokens**: audit the 60 decorated tools, merge overlaps, use consistent `domain_verb` names, add a compound/profile mode and `response_format` concise/detailed, and paginate every list tool with truncation hints.
6. **Typed results and actionable errors**: Pydantic returns, `ToolError` messages naming valid alternatives, no stack traces.
7. **Add resources and prompts** (project summary, layer schema template, CRS/algorithm help, "inspect then style" workflows) as a differentiator, with tool fallbacks.
8. **Long-running jobs**: async processing start/status/cancel with `ctx.report_progress`, stdlib logging instead of deprecated `ctx.info`, checkpoint/undo for edits.
9. **Ship properly**: unique name; real author/email/repo, valid `category`, tags, changelog and LICENSE in `metadata.txt`; PyPI package with the `mcp-name` marker; `server.json` and `mcp-publisher publish`; QGIS Hub upload (OSGeo ID); awesome-mcp-servers PR; Glama claim; GitHub topics.
10. **Optional Streamable HTTP** behind an env/CLI switch (stdio stays default) with a DNS-rebinding `transport_security` allowlist and token auth, plus `ping`/`diagnose` tools reporting plugin and server versions and the active transport.
