# GitHub repository settings (manual steps)

These settings cannot be set from the repository files. Apply them by hand as the repository owner to maximise discoverability on GitHub and in search engines.

## 1. About box

Open the repository page, click the gear next to **About**.

**Description** (under 350 characters, rich in keywords). Apply it from a terminal after `gh auth login`:

```bash
gh repo edit adilmunawar/qgis_mcp --description "Open data toolbox for QGIS. Explore live satellite imagery, weather, elevation and OpenStreetMap data with a few clicks, and let Claude, Cursor, VS Code and other AI agents drive QGIS through a permission gated MCP server with an approval queue and audit log." --add-topic qgis --add-topic qgis-plugin --add-topic mcp --add-topic mcp-server --add-topic model-context-protocol --add-topic claude --add-topic gis --add-topic geospatial --add-topic pyqgis --add-topic remote-sensing --add-topic sentinel-2 --add-topic ndvi --add-topic open-data --add-topic ai-agent --add-topic llm-agents --add-topic python --add-topic pyqt6 --add-topic geoai --add-topic earth-observation --add-topic openstreetmap --enable-discussions
```

Text for the About box if you prefer the web form:

> Open data toolbox for QGIS. Explore live satellite imagery, weather, elevation and OpenStreetMap data with a few clicks, and let Claude, Cursor, VS Code and other AI agents drive QGIS through a permission gated MCP server with an approval queue and audit log.

**Website:** the repository's docs or release page, or leave blank until you have a project site.

**Topics** (add all; GitHub allows up to 20):

```
qgis, qgis-plugin, qgis4, mcp, mcp-server, model-context-protocol, claude, claude-code,
gis, geospatial, pyqgis, python, ai-agent, llm-agents, remote-sensing, automation,
cursor, pyqt6, geoai, llm-tools
```

Tick **Releases** and **Packages** off if unused; keep **Releases** on once you publish one.

## 2. Features

Settings > General > Features:

- Enable **Discussions** (the issue template config links to it). Create categories: Q&A, Ideas, Show and tell.
- Keep **Issues** on. Enable **Projects** only if you plan a public roadmap board.
- Disable **Wiki** (docs live in the repo).

## 3. Social preview image

Settings > General > **Social preview** > Upload.

- Size 1280 x 640 px (minimum 640 x 320), PNG or JPG, under 1 MB.
- Include the project name "QGIS MCP", the tagline, the plugin icon (`qgis_mcp_plugin/icon.svg`) and a map or panel screenshot. Keep text large and centred, leaving a safe margin.

## 4. Security and community

- Settings > Code security: enable **Private vulnerability reporting** (SECURITY.md and the issue config link to it), **Dependabot alerts**, and **Dependabot security updates**.
- Community standards page (Insights > Community Standards) should show green ticks for README, LICENSE, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, issue templates and the PR template. If the Code of Conduct or License is not detected, recheck the filenames.
- Optionally protect `main` (Settings > Branches): require pull requests before merging.

## 5. Release and tag strategy

- Use [Semantic Versioning](https://semver.org/). The tag mirrors the plugin version in `qgis_mcp_plugin/metadata.txt` (for example `v4.0.0`).
- For each release: update `metadata.txt` and `CHANGELOG.md`, commit, then tag and create a GitHub Release with the changelog section as notes.
- Attach a plugin zip containing the `qgis_mcp_plugin/` folder as the top level directory (required by the QGIS plugin repository).
- Keep prereleases for live data connectors until they are stable.

```bash
git tag -a v4.0.0 -m "QGIS MCP 4.0.0"
git push origin v4.0.0
```

## 6. Get listed: MCP directories

**Official MCP registry** (registry.modelcontextprotocol.io):

1. Publish the MCP server as a package (for example to PyPI) and add a `server.json` describing it, with a name such as `io.github.adilmunawar/qgis_mcp`.
2. Install the `mcp-publisher` CLI from the modelcontextprotocol/registry repository, run `mcp-publisher login github`, then `mcp-publisher publish`.
3. Follow the current instructions in the registry's quickstart; the process changes, so check the docs.

**awesome-mcp-servers lists** (for example punkpeye/awesome-mcp-servers and appcypher/awesome-mcp-servers):

1. Fork the list and read its CONTRIBUTING file for the entry format and category (look for a geospatial, maps or location category).
2. Add one line: `[adilmunawar/qgis_mcp](https://github.com/adilmunawar/qgis_mcp) - Open data toolbox and permission gated MCP server for QGIS with approval queue and audit log.`
3. Open a pull request following that list's rules.

Also consider Smithery, Glama and PulseMCP, which index MCP servers from GitHub.

## 7. QGIS plugin repository submission

1. Create an account at <https://plugins.qgis.org> (an OSGeo ID).
2. Fix `metadata.txt` before the first upload: set a real author name and email (the current email is a placeholder), and point `homepage`, `tracker` and `repository` to `https://github.com/adilmunawar/qgis_mcp`. Add `tags=` and a longer `about`. Set `experimental=True` for early releases.
3. Zip the plugin so the top level folder is `qgis_mcp_plugin/` and contains `metadata.txt`, `__init__.py` and `icon.png`; do not include `__pycache__`.
4. Upload via `Plugins > Upload a plugin`. New plugins wait for staff approval.
5. Read the plugin requirements at <https://plugins.qgis.org/publish/> (licence file, no binaries, valid metadata).
6. For later releases, increase `version` in `metadata.txt` and upload a new zip.

## 8. Checklist

- [ ] About description and topics set
- [ ] Discussions enabled; Wiki disabled
- [ ] Social preview image uploaded (1280 x 640)
- [ ] Private vulnerability reporting and Dependabot enabled
- [ ] Community Standards page fully green
- [ ] `v4.0.0` tag and GitHub Release published with changelog notes
- [ ] `metadata.txt` author, email, URLs corrected
- [ ] Submitted to the official MCP registry
- [ ] PRs opened to awesome-mcp-servers lists
- [ ] Plugin uploaded to plugins.qgis.org
- [ ] Repo pinned on your GitHub profile
- [ ] Short demo GIF or video added to the README
