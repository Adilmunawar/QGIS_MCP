# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless integration test for the QGIS MCP plugin.

Run with QGIS's own interpreter::

    "C:/Program Files/QGIS 4.2.0/bin/python-qgis.bat" tests/test_plugin_headless.py

Covers the failure modes that motivated the v2 rewrite: pipelined requests in
one TCP segment, several simultaneous clients, oversized responses, and the
execute_code permission gate.
"""

import json
import os
import socket
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PLUGIN_DIR = os.path.join(
    os.environ["APPDATA"], "QGIS", "QGIS4", "profiles", "default",
    "python", "plugins",
)
sys.path.insert(0, PLUGIN_DIR)

from qgis.core import QgsApplication, QgsVectorLayer, QgsProject, QgsFeature, QgsGeometry, QgsPointXY  # noqa: E402

app = QgsApplication([], True)
QgsApplication.initQgis()

from qgis_mcp_plugin.qgis_mcp_plugin import ICON_PATH as ICON  # noqa: E402
from qgis_mcp_plugin.mcp_server import QgisMCPServer  # noqa: E402
from qgis_mcp_plugin.panel import QgisMCPDockWidget  # noqa: E402
from qgis_mcp_plugin.permissions import MASTER, PermissionManager  # noqa: E402

PORT = 9899
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"{'PASS' if condition else 'FAIL'}  {name}{(' -> ' + str(detail)) if detail else ''}",
          flush=True)


def pump(seconds=0.4):
    """Advance time. Only the main thread may touch the Qt event loop."""
    if threading.current_thread() is threading.main_thread():
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)
    else:
        time.sleep(seconds)


class Client:
    """Minimal NDJSON client mirroring the MCP-side transport."""

    def __init__(self, port=PORT, timeout=10):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.buf = bytearray()

    def send_raw(self, data: bytes):
        self.sock.sendall(data)

    def request(self, cmd, params=None, request_id=None):
        msg = {"type": cmd, "params": params or {}}
        if request_id is not None:
            msg["id"] = request_id
        self.sock.sendall(json.dumps(msg).encode() + b"\n")
        return self.read()

    def read(self):
        while True:
            idx = self.buf.find(b"\n")
            if idx >= 0:
                line = bytes(self.buf[:idx])
                del self.buf[: idx + 1]
                if line.strip():
                    return json.loads(line.decode())
                continue
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("server closed")
            self.buf.extend(chunk)

    def close(self):
        self.sock.close()


def make_layer(feature_count=500):
    """A memory layer big enough that its response spans many TCP segments."""
    layer = QgsVectorLayer("Point?crs=epsg:4326&field=name:string&field=value:integer",
                           "test_points", "memory")
    provider = layer.dataProvider()
    feats = []
    for i in range(feature_count):
        f = QgsFeature(layer.fields())
        f.setAttributes([f"point_{i}", i])
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(i * 0.01, i * 0.02)))
        feats.append(f)
    provider.addFeatures(feats)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return layer


def run(layer, server):
    # Runs on a worker thread: the main thread must stay free to pump the Qt
    # event loop, otherwise the server never sees incoming data.

    # --- basic round trip -------------------------------------------
    c = Client()
    resp = c.request("ping")
    check("ping", resp.get("status") == "success" and resp["result"]["pong"] is True, resp)
    check("protocol v3 advertised", resp["result"].get("protocol_version") == 3,
          resp["result"].get("protocol_version"))

    # --- request id correlation -------------------------------------
    resp = c.request("ping", request_id="abc-123")
    check("request id echoed", resp.get("id") == "abc-123", resp.get("id"))

    # --- pipelining: three requests in ONE write --------------------
    # This is exactly what corrupted the v1 buffer.
    batch = b"".join(
        json.dumps({"type": t, "params": {}, "id": t}).encode() + b"\n"
        for t in ("ping", "get_qgis_info", "get_server_info")
    )
    c.send_raw(batch)
    pump(0.6)
    got = [c.read() for _ in range(3)]
    check("pipelined requests all answered", len(got) == 3, len(got))
    check("pipelined responses in order",
          [g.get("id") for g in got] == ["ping", "get_qgis_info", "get_server_info"],
          [g.get("id") for g in got])
    check("pipelined all successful", all(g["status"] == "success" for g in got))

    # --- v1 fallback: no trailing newline ---------------------------
    legacy = Client()
    pump()
    legacy.sock.sendall(json.dumps({"type": "ping", "params": {}}).encode())
    pump(0.6)
    check("v1 client without newline still works",
          legacy.read().get("status") == "success")
    legacy.close()

    # --- concurrent clients -----------------------------------------
    results, errors = [], []

    def worker(n):
        try:
            wc = Client()
            for _ in range(5):
                r = wc.request("get_project_info", request_id=f"w{n}")
                results.append(r.get("status"))
            wc.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"worker {n}: {exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    check("5 concurrent clients, no errors", not errors, errors[:3])
    check("all 25 concurrent responses succeeded",
          len(results) == 25 and set(results) == {"success"},
          f"{len(results)} responses, statuses={set(results)}")

    # --- large payload spanning many segments ------------------------
    big = c.request("get_layer_features", {"layer_id": layer.id(), "limit": 500})
    check("large response intact",
          big["status"] == "success" and big["result"]["returned"] == 500,
          big.get("result", {}).get("returned"))

    # --- expression filtering ----------------------------------------
    filtered = c.request("get_layer_features",
                         {"layer_id": layer.id(), "limit": 100,
                          "expression": "value < 10"})
    check("expression filter applied",
          filtered["status"] == "success" and filtered["result"]["returned"] == 10,
          filtered.get("result", {}).get("returned"))

    sel = c.request("select_features_by_expression",
                    {"layer_id": layer.id(), "expression": "value >= 490"})
    check("select by expression",
          sel["status"] == "success" and sel["result"]["selected_count"] == 10,
          sel.get("result"))

    # --- error handling ----------------------------------------------
    bad = c.request("no_such_command")
    check("unknown command is a clean error",
          bad["status"] == "error" and bad["error_type"] == "UnknownCommand", bad)

    missing = c.request("get_layer_features", {"layer_id": "does_not_exist"})
    check("missing layer is a clean error",
          missing["status"] == "error" and missing["error_type"] == "LookupError",
          missing.get("error_type"))

    badparams = c.request("get_layer_extent", {"wrong_kwarg": 1})
    check("bad params reported as InvalidParameters",
          badparams["status"] == "error"
          and badparams["error_type"] == "InvalidParameters",
          badparams.get("error_type"))

    c.sock.sendall(b"{not json at all}\n")
    pump(0.4)
    check("malformed JSON does not kill the connection",
          c.read().get("error_type") == "JSONDecodeError")
    check("connection survives malformed input",
          c.request("ping")["status"] == "success")

    # --- execute_code (gating itself is covered in the permissions suite) ---
    allowed = c.request("execute_code", {"code": "print('hi'); result = 6 * 7"})
    check("execute_code works when enabled",
          allowed["status"] == "success" and allowed["result"]["result"] == 42,
          allowed.get("result"))
    check("execute_code captures stdout",
          allowed["result"]["stdout"].strip() == "hi")

    failing = c.request("execute_code", {"code": "1/0"})
    check("execute_code reports errors without dying",
          failing["status"] == "success"
          and failing["result"]["executed"] is False
          and failing["result"]["error_type"] == "ZeroDivisionError",
          failing.get("result", {}).get("error_type"))

    # --- rendering ------------------------------------------------------
    out_png = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "_render_test.png")
    rendered = c.request("render_map", {"path": out_png, "width": 200, "height": 150})
    check("render_map writes a file",
          rendered["status"] == "success" and os.path.exists(out_png), rendered)
    if os.path.exists(out_png):
        os.remove(out_png)

    # --- v3: analysis helpers -------------------------------------------
    uniq = c.request("get_unique_values", {"layer_id": layer.id(), "field": "name",
                                           "limit": 5})
    check("get_unique_values returns counts",
          uniq["status"] == "success" and uniq["result"]["unique_count"] == 500
          and uniq["result"]["truncated"] is True
          and len(uniq["result"]["values"]) == 5,
          uniq.get("result", {}).get("unique_count"))

    stats = c.request("get_field_statistics", {"layer_id": layer.id(),
                                               "field": "value"})
    s = stats.get("result", {}).get("statistics", {})
    check("get_field_statistics computes numerics",
          stats["status"] == "success" and s.get("min") == 0
          and s.get("max") == 499 and abs(s.get("mean", 0) - 249.5) < 0.001,
          {k: s.get(k) for k in ("min", "max", "mean")})

    bad_field = c.request("get_field_statistics", {"layer_id": layer.id(),
                                                   "field": "nope"})
    check("unknown field is a clean error",
          bad_field["status"] == "error"
          and bad_field["error_type"] == "LookupError",
          bad_field.get("error_type"))

    cnt = c.request("count_features", {"layer_id": layer.id(),
                                       "expression": "value >= 250"})
    check("count_features with expression",
          cnt["status"] == "success" and cnt["result"]["count"] == 250,
          cnt.get("result", {}).get("count"))

    bad_expr = c.request("count_features", {"layer_id": layer.id(),
                                            "expression": "value >>> "})
    check("invalid expression rejected before use",
          bad_expr["status"] == "error" and bad_expr["error_type"] == "ValueError",
          bad_expr.get("error_type"))

    # --- v3: filters -----------------------------------------------------
    filt = c.request("set_layer_filter", {"layer_id": layer.id(),
                                          "expression": "\"value\" < 100"})
    check("set_layer_filter reports true counts",
          filt["status"] == "success" and filt["result"]["count_before"] == 500
          and filt["result"]["count_after"] == 100,
          filt.get("result"))

    got_filter = c.request("get_layer_filter", {"layer_id": layer.id()})
    check("get_layer_filter round trips",
          got_filter["result"]["filter"] == "\"value\" < 100",
          got_filter.get("result"))

    cleared = c.request("clear_layer_filter", {"layer_id": layer.id()})
    check("clear_layer_filter restores all features",
          cleared["status"] == "success" and cleared["result"]["count_after"] == 500,
          cleared.get("result", {}).get("count_after"))

    # --- v3: memory layers, editing --------------------------------------
    mem = c.request("create_memory_layer", {
        "name": "scratch_test", "geometry_type": "Point", "crs": "EPSG:4326",
        "fields": {"site": "string", "yield": "double"}})
    check("create_memory_layer",
          mem["status"] == "success" and mem["result"]["fields"] == ["site", "yield"],
          mem.get("result"))
    mem_id = mem["result"]["id"]

    added = c.request("add_features", {"layer_id": mem_id, "features": [
        {"attributes": {"site": "a", "yield": 1.5}, "wkt": "POINT(71.5 30.1)"},
        {"attributes": {"site": "b", "yield": 2.5}, "wkt": "POINT(71.6 30.2)"},
        {"attributes": {"site": "c", "yield": 3.5}, "wkt": "POINT(71.7 30.3)"},
    ]})
    check("add_features", added["status"] == "success"
          and added["result"]["added"] == 3, added.get("result"))

    bad_wkt = c.request("add_features", {"layer_id": mem_id, "features": [
        {"attributes": {"site": "x"}, "wkt": "NOT A GEOMETRY"}]})
    check("bad WKT rejected", bad_wkt["status"] == "error"
          and bad_wkt["error_type"] == "ValueError", bad_wkt.get("error_type"))

    dry = c.request("delete_features", {"layer_id": mem_id,
                                        "expression": "\"yield\" > 2"})
    check("delete_features dry-runs by default",
          dry["status"] == "success" and dry["result"]["would_delete"] == 2
          and dry["result"]["deleted"] == 0
          and dry["result"]["confirmed"] is False, dry.get("result"))

    real = c.request("delete_features", {"layer_id": mem_id,
                                         "expression": "\"yield\" > 2",
                                         "confirm": True})
    check("delete_features with confirm deletes",
          real["status"] == "success" and real["result"]["deleted"] == 2,
          real.get("result", {}).get("deleted"))

    remaining = c.request("count_features", {"layer_id": mem_id})
    check("deletion actually applied",
          remaining["result"]["count"] == 1, remaining.get("result"))

    no_expr = c.request("delete_features", {"layer_id": mem_id, "expression": "",
                                            "confirm": True})
    check("delete_features refuses empty expression",
          no_expr["status"] == "error" and no_expr["error_type"] == "ValueError",
          no_expr.get("error_type"))

    # --- v3: CRS relabel --------------------------------------------------
    crs_res = c.request("set_layer_crs", {"layer_id": mem_id,
                                          "authid": "EPSG:32642"})
    check("set_layer_crs relabels",
          crs_res["status"] == "success"
          and crs_res["result"]["crs"] == "EPSG:32642"
          and crs_res["result"]["previous_crs"] == "EPSG:4326",
          crs_res.get("result"))

    bad_crs = c.request("set_layer_crs", {"layer_id": mem_id, "authid": "EPSG:0"})
    check("invalid CRS rejected", bad_crs["status"] == "error", bad_crs.get("error_type"))

    opacity = c.request("set_layer_opacity", {"layer_id": mem_id, "opacity": 0.5})
    check("set_layer_opacity", opacity["status"] == "success"
          and opacity["result"]["opacity"] == 0.5)
    bad_op = c.request("set_layer_opacity", {"layer_id": mem_id, "opacity": 5})
    check("out-of-range opacity rejected", bad_op["status"] == "error")

    # --- v3: export --------------------------------------------------------
    out_gpkg = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_export_test.gpkg")
    if os.path.exists(out_gpkg):
        os.remove(out_gpkg)
    exported = c.request("export_layer", {"layer_id": mem_id, "path": out_gpkg,
                                          "driver": "GPKG"})
    check("export_layer writes a file",
          exported["status"] == "success" and os.path.exists(out_gpkg),
          exported.get("result") or exported.get("message"))
    if os.path.exists(out_gpkg):
        os.remove(out_gpkg)

    # --- v3: base64 rendering ---------------------------------------------
    img = c.request("render_map_base64", {"width": 160, "height": 120})
    ok_img = (img["status"] == "success"
              and img["result"]["format"] == "png"
              and img["result"]["bytes"] > 100)
    png_magic = False
    if ok_img:
        import base64 as _b64
        png_magic = _b64.b64decode(img["result"]["base64"])[:8] == b"\x89PNG\r\n\x1a\n"
    check("render_map_base64 returns a real PNG", ok_img and png_magic,
          img.get("result", {}).get("bytes"))

    too_big = c.request("render_map_base64", {"width": 4000, "height": 4000,
                                              "max_bytes": 1000})
    check("oversized render is refused, not truncated",
          too_big["status"] == "error" and too_big["error_type"] == "ValueError",
          too_big.get("error_type"))

    # --- v3: server info ---------------------------------------------------
    info = c.request("get_server_info")
    check("get_server_info reports v4 and counters",
          info["result"]["plugin_version"].startswith("4.")
          and info["result"]["requests_served"] > 0
          and len(info["result"]["commands"]) >= 40,
          f"{info['result']['plugin_version']}, "
          f"{len(info['result']['commands'])} commands")

    c.request("remove_layer", {"layer_id": mem_id})

    c.close()
    pump()


def main():
    layer = make_layer()
    server = QgisMCPServer(port=PORT, iface=None,
                           permissions=PermissionManager(profile=MASTER))
    check("server starts", server.start())
    pump()

    error = []

    def worker():
        try:
            run(layer, server)
        except Exception as exc:  # noqa: BLE001
            import traceback
            error.append(traceback.format_exc())

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.time() + 180
    while thread.is_alive() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.005)
    thread.join(5)
    if error:
        print("WORKER EXCEPTION:\n" + error[0], flush=True)
        FAILED.append("worker raised")

    check("client count returns to zero after disconnect",
          server.client_count() == 0, server.client_count())

    # --- dock widget lifetime and UI (Qt widgets are main-thread only) ---
    dock = QgisMCPDockWidget(None)
    check("dock builds five tabs", dock.tabs.count() == 5,
          [dock.tabs.tabText(i) for i in range(dock.tabs.count())])
    check("status starts as Stopped", dock.status_pill.text() == "Stopped",
          dock.status_pill.text())
    dock.log_row("--", "unit_test", "success", "")
    check("activity table records rows", dock.log_table.rowCount() == 1,
          dock.log_table.rowCount())
    dock.attach(server)
    check("status reflects a running server",
          dock.status_pill.text().startswith("Running"),
          dock.status_pill.text())
    check("metrics show served requests",
          int(dock.tile_requests.value_label.text()) > 0,
          dock.tile_requests.value_label.text())
    dock.close()
    pump()
    check("closing the dock leaves the server running", server.running)

    check("icon file present and non-trivial",
          os.path.exists(ICON) and os.path.getsize(ICON) > 400,
          os.path.getsize(ICON) if os.path.exists(ICON) else "missing")

    server.stop()
    pump()
    check("server stops cleanly", not server.running)


try:
    main()
finally:
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed", flush=True)
    if FAILED:
        print("FAILED: " + ", ".join(FAILED), flush=True)
    QgsApplication.exitQgis()
    sys.exit(1 if FAILED else 0)
