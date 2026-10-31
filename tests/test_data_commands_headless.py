# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless tests for the Phase 3 data commands and the Commands tab.

    "C:/Program Files/QGIS 4.2.0/bin/python-qgis.bat" tests/test_data_commands_headless.py
"""

import json
import os
import socket
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(
    os.environ["APPDATA"], "QGIS", "QGIS4", "profiles", "default",
    "python", "plugins"))

from qgis.core import (  # noqa: E402
    QgsApplication, QgsFeature, QgsGeometry, QgsPointXY, QgsProject,
    QgsVectorLayer,
)

app = QgsApplication([], True)
QgsApplication.initQgis()

from qgis_mcp_plugin.command_help import COMMAND_HELP, describe  # noqa: E402
from qgis_mcp_plugin.mcp_server import QgisMCPServer  # noqa: E402
from qgis_mcp_plugin.panel import QgisMCPDockWidget  # noqa: E402
from qgis_mcp_plugin.permissions import (  # noqa: E402
    COMMAND_CATEGORIES, MASTER, PermissionManager, READ_ONLY, STANDARD,
)

PORT = 9911
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"{'PASS' if condition else 'FAIL'}  {name}"
          f"{(' -> ' + str(detail)) if detail else ''}", flush=True)


def pump(seconds=0.4):
    if threading.current_thread() is threading.main_thread():
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)
    else:
        time.sleep(seconds)


class Client:
    def __init__(self, port=PORT, timeout=20):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.buf = bytearray()

    def request(self, cmd, params=None):
        self.sock.sendall(
            json.dumps({"type": cmd, "params": params or {}}).encode() + b"\n")
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


def make_layers():
    """A crop layer with deliberate duplicates, plus a georeferenced reference."""
    crops = QgsVectorLayer(
        "Point?crs=epsg:4326&field=crop:string&field=district:string"
        "&field=yield:double",
        "crops", "memory")
    rows = [
        ("Sugarcane", "RYK", 60.0), ("Sugarcane", "RYK", 70.0),
        ("Sugarcane", "Bahawalnagar", 50.0), ("Cotton", "RYK", 20.0),
        ("Cotton", "Bahawalnagar", 25.0), ("Wheat", "RYK", 40.0),
        ("Wheat", "RYK", 40.0),
    ]
    feats = []
    for i, (crop, district, yld) in enumerate(rows):
        f = QgsFeature(crops.fields())
        f.setAttributes([crop, district, yld])
        # last two share a location, to be found as geometry duplicates
        x = 71.0 + (0.0 if i >= 5 else i * 0.1)
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, 29.0)))
        feats.append(f)
    crops.dataProvider().addFeatures(feats)
    crops.updateExtents()
    QgsProject.instance().addMapLayer(crops)

    # A reference layer in EPSG:4326 covering the same ground, so
    # detect_layer_crs has something to rank candidates against.
    ref = QgsVectorLayer("Polygon?crs=epsg:4326&field=name:string",
                         "reference", "memory")
    f = QgsFeature(ref.fields())
    f.setAttributes(["punjab"])
    f.setGeometry(QgsGeometry.fromWkt(
        "POLYGON((69 27.5, 73 27.5, 73 31, 69 31, 69 27.5))"))
    ref.dataProvider().addFeatures([f])
    ref.updateExtents()
    QgsProject.instance().addMapLayer(ref)

    # A layer whose coordinates are UTM 42N metres but which declares nothing.
    utm = QgsVectorLayer("Point?crs=&field=id:integer", "undeclared", "memory")
    feats = []
    for i, (x, y) in enumerate([(535000, 3100000), (700000, 3250000),
                                (860000, 3390000)]):
        f = QgsFeature(utm.fields())
        f.setAttributes([i])
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        feats.append(f)
    utm.dataProvider().addFeatures(feats)
    utm.updateExtents()
    QgsProject.instance().addMapLayer(utm)
    return crops, ref, utm


def socket_checks(crops, utm, results):
    c = Client()

    # ---- update_features -------------------------------------------
    dry = c.request("update_features", {
        "layer_id": crops.id(), "expression": "\"crop\" = 'Cotton'",
        "field_values": {"crop": "Cotton (recoded)"}})
    check("update_features dry-runs by default",
          dry["status"] == "success" and dry["result"]["would_update"] == 2
          and dry["result"]["updated"] == 0
          and dry["result"]["confirmed"] is False, dry.get("result"))

    real = c.request("update_features", {
        "layer_id": crops.id(), "expression": "\"crop\" = 'Cotton'",
        "field_values": {"crop": "Cotton (recoded)"}, "confirm": True})
    check("update_features applies when confirmed",
          real["status"] == "success" and real["result"]["updated"] == 2,
          real.get("result", {}).get("updated"))

    verify = c.request("count_features", {
        "layer_id": crops.id(), "expression": "\"crop\" = 'Cotton (recoded)'"})
    check("update actually changed the data",
          verify["result"]["count"] == 2, verify.get("result"))

    bad = c.request("update_features", {"layer_id": crops.id(),
                                        "field_values": {"nope": 1}})
    check("update_features rejects unknown fields",
          bad["status"] == "error" and bad["error_type"] == "LookupError",
          bad.get("error_type"))

    empty = c.request("update_features", {"layer_id": crops.id(),
                                          "field_values": {}})
    check("update_features requires field_values",
          empty["status"] == "error" and empty["error_type"] == "ValueError")

    # ---- schema -----------------------------------------------------
    added = c.request("add_field", {"layer_id": crops.id(), "name": "note",
                                    "field_type": "string"})
    check("add_field", added["status"] == "success"
          and "note" in added["result"]["fields"], added.get("result"))

    dupe = c.request("add_field", {"layer_id": crops.id(), "name": "note"})
    check("add_field refuses duplicates", dupe["status"] == "error"
          and dupe["error_type"] == "ValueError")

    badtype = c.request("add_field", {"layer_id": crops.id(), "name": "x",
                                      "field_type": "nonsense"})
    check("add_field validates the type", badtype["status"] == "error"
          and badtype["error_type"] == "ValueError")

    renamed = c.request("rename_field", {"layer_id": crops.id(),
                                         "old_name": "note",
                                         "new_name": "remark"})
    check("rename_field", renamed["status"] == "success"
          and "remark" in renamed["result"]["fields"],
          renamed.get("result", {}).get("fields"))

    drop_dry = c.request("delete_field", {"layer_id": crops.id(),
                                          "name": "remark"})
    check("delete_field dry-runs by default",
          drop_dry["result"]["confirmed"] is False
          and drop_dry["result"]["deleted"] is None, drop_dry.get("result"))
    drop = c.request("delete_field", {"layer_id": crops.id(),
                                      "name": "remark", "confirm": True})
    check("delete_field removes when confirmed",
          drop["status"] == "success"
          and "remark" not in drop["result"]["fields"],
          drop.get("result", {}).get("fields"))

    # ---- group statistics -------------------------------------------
    grouped = c.request("group_statistics", {
        "layer_id": crops.id(), "group_field": "district",
        "value_field": "yield", "aggregate": "mean"})
    by_group = {g["group"]: g["mean"] for g in grouped["result"]["groups"]}
    check("group_statistics computes per-group means",
          grouped["status"] == "success"
          and abs(by_group.get("RYK", 0) - 46.0) < 0.001,
          by_group)
    counted = c.request("group_statistics", {
        "layer_id": crops.id(), "group_field": "crop", "aggregate": "count"})
    check("group_statistics counts groups",
          counted["result"]["group_count"] == 3,
          counted["result"]["group_count"])
    # Regression: with no value_field nothing was appended per group, so every
    # count came back as 0 while group_count still looked right.
    tallies = {g["group"]: g["count"] for g in counted["result"]["groups"]}
    check("count mode reports real per-group totals",
          tallies.get("Sugarcane") == 3 and sum(tallies.values()) == 7,
          tallies)
    bad_agg = c.request("group_statistics", {"layer_id": crops.id(),
                                             "group_field": "crop",
                                             "aggregate": "mean"})
    check("aggregate without a value field is refused",
          bad_agg["status"] == "error" and bad_agg["error_type"] == "ValueError")

    # ---- duplicates --------------------------------------------------
    dupes = c.request("find_duplicates", {"layer_id": crops.id(),
                                          "fields": ["crop", "district"]})
    check("find_duplicates by attributes",
          dupes["status"] == "success"
          and dupes["result"]["duplicate_groups"] == 2,
          dupes.get("result", {}).get("duplicate_groups"))
    geom_dupes = c.request("find_duplicates", {"layer_id": crops.id()})
    check("find_duplicates by geometry",
          geom_dupes["result"]["duplicate_groups"] >= 1,
          geom_dupes["result"]["duplicate_groups"])

    # ---- geometry validity -------------------------------------------
    validity = c.request("check_geometry_validity", {"layer_id": crops.id()})
    check("check_geometry_validity scans cleanly",
          validity["status"] == "success"
          and validity["result"]["scanned"] == 7
          and validity["result"]["invalid_reported"] == 0,
          validity.get("result"))

    # ---- detect_layer_crs --------------------------------------------
    detected = c.request("detect_layer_crs", {"layer_id": utm.id()})
    result = detected.get("result", {})
    check("detect_layer_crs identifies UTM 42N",
          detected["status"] == "success" and result.get("best_guess") == "EPSG:32642",
          result.get("best_guess"))
    check("detect_layer_crs is confident given a reference",
          result.get("confident") is True, result.get("confident"))
    check("detect_layer_crs reports it is not geographic",
          result.get("looks_geographic") is False)
    check("detect_layer_crs returns ranked candidates",
          len(result.get("candidates", [])) >= 1,
          len(result.get("candidates", [])))

    geographic = c.request("detect_layer_crs", {"layer_id": crops.id()})
    check("a degrees layer is recognised as geographic",
          geographic["result"]["looks_geographic"] is True)

    # ---- spatial index ------------------------------------------------
    indexed = c.request("create_spatial_index", {"layer_id": crops.id()})
    check("create_spatial_index", indexed["status"] == "success"
          and indexed["result"]["indexed"] is True, indexed.get("result"))

    # ---- execute_sql (may be unavailable headless) ---------------------
    sql = c.request("execute_sql", {"query": "SELECT crop FROM crops LIMIT 2"})
    if sql["status"] == "success":
        check("execute_sql runs a query",
              sql["result"]["returned"] <= 2, sql.get("result"))
    else:
        check("execute_sql fails with an actionable message",
              "virtual" in sql.get("message", "").lower(),
              sql.get("message", "")[:80])
        results["sql_unavailable"] = True

    # ---- raster errors are clean ---------------------------------------
    not_raster = c.request("get_raster_statistics", {"layer_id": crops.id()})
    check("raster command on a vector layer is a clean error",
          not_raster["status"] == "error"
          and not_raster["error_type"] == "ValueError",
          not_raster.get("error_type"))

    c.close()
    results["done"] = True


def main():
    crops, ref, utm = make_layers()

    # every command should be categorised and described
    server = QgisMCPServer(port=PORT, iface=None,
                           permissions=PermissionManager(profile=MASTER))
    commands = set(server._handlers())
    uncategorised = sorted(commands - set(COMMAND_CATEGORIES))
    check("every command has a risk category", not uncategorised, uncategorised)
    undescribed = sorted(c for c in commands if not describe(c))
    check("every command has a description", not undescribed, undescribed)
    stale = sorted(set(COMMAND_HELP) - commands)
    check("no descriptions for commands that no longer exist", not stale, stale)

    check("server starts", server.start())
    pump()

    results, errors = {}, []

    def worker():
        try:
            socket_checks(crops, utm, results)
        except Exception:
            import traceback
            errors.append(traceback.format_exc())
            results["done"] = True

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.time() + 180
    while not results.get("done") and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    thread.join(5)
    if errors:
        print("WORKER EXCEPTION:\n" + errors[0], flush=True)
        FAILED.append("worker raised")

    # ---- Commands tab --------------------------------------------------
    dock = QgisMCPDockWidget(None)
    check("panel has five tabs including Commands",
          dock.tabs.count() == 5
          and "Commands" in [dock.tabs.tabText(i)
                             for i in range(dock.tabs.count())],
          [dock.tabs.tabText(i) for i in range(dock.tabs.count())])
    check("commands table is populated",
          dock.commands_table.rowCount() >= len(COMMAND_CATEGORIES) - 2,
          dock.commands_table.rowCount())
    check("commands summary counts them",
          "commands" in dock.commands_summary.text(),
          dock.commands_summary.text())

    dock.attach(server)
    pump(0.3)
    check("attached panel lists the live command set",
          dock.commands_table.rowCount() == len(commands),
          f"{dock.commands_table.rowCount()} vs {len(commands)}")

    def verdicts():
        return {dock.commands_table.item(r, 0).text():
                dock.commands_table.item(r, 2).text()
                for r in range(dock.commands_table.rowCount())}

    server.permissions.set_profile(READ_ONLY)
    dock.profile_combo.setCurrentIndex(dock.profile_combo.findData(READ_ONLY))
    pump(0.2)
    v = verdicts()
    check("read-only marks writes as refused",
          v.get("add_features") == "refused", v.get("add_features"))
    check("read-only still allows reads",
          v.get("get_layers") == "allowed", v.get("get_layers"))

    dock.profile_combo.setCurrentIndex(dock.profile_combo.findData(STANDARD))
    pump(0.2)
    v = verdicts()
    check("standard marks deletes as asking first",
          v.get("delete_features") == "asks first", v.get("delete_features"))
    check("standard marks writes allowed",
          v.get("add_features") == "allowed", v.get("add_features"))

    dock.profile_combo.setCurrentIndex(dock.profile_combo.findData(MASTER))
    pump(0.2)
    v = verdicts()
    check("master marks everything allowed",
          set(v.values()) == {"allowed"}, set(v.values()))

    dock.blocked_only.setChecked(True)
    pump(0.1)
    hidden = sum(1 for r in range(dock.commands_table.rowCount())
                 if dock.commands_table.isRowHidden(r))
    check("'blocked only' hides everything under Master Control",
          hidden == dock.commands_table.rowCount(), hidden)
    dock.blocked_only.setChecked(False)

    dock.command_search.setText("raster")
    pump(0.1)
    visible = [dock.commands_table.item(r, 0).text()
               for r in range(dock.commands_table.rowCount())
               if not dock.commands_table.isRowHidden(r)]
    check("command search filters the list",
          visible and all("raster" in name for name in visible), visible)
    dock.command_search.setText("")

    dock.close()
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
