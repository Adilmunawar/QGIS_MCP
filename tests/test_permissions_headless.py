# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""Headless tests for permission profiles, approvals, tokens and auditing.

Run with QGIS's own interpreter::

    "C:/Program Files/QGIS 4.2.0/bin/python-qgis.bat" tests/test_permissions_headless.py

The client half runs on a worker thread while the main thread pumps the Qt
event loop, because the server, the approval timers and the panel all live on
that loop.
"""

import json
import os
import socket
import sys
import tempfile
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

from qgis_mcp_plugin.audit import AuditLog  # noqa: E402
from qgis_mcp_plugin.mcp_server import QgisMCPServer  # noqa: E402
from qgis_mcp_plugin.panel import QgisMCPDockWidget  # noqa: E402
from qgis_mcp_plugin.permissions import (  # noqa: E402
    FULL, MASTER, PermissionManager, READ_ONLY, STANDARD, category_for,
)

PORT = 9901
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
    def __init__(self, port=PORT, timeout=15):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.buf = bytearray()

    def request(self, cmd, params=None):
        self.sock.sendall(
            json.dumps({"type": cmd, "params": params or {}}).encode() + b"\n")
        return self.read()

    def send_only(self, cmd, params=None):
        self.sock.sendall(
            json.dumps({"type": cmd, "params": params or {}}).encode() + b"\n")

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


def make_layer():
    layer = QgsVectorLayer(
        "Point?crs=epsg:4326&field=name:string&field=value:integer",
        "perm_points", "memory")
    feats = []
    for i in range(20):
        f = QgsFeature(layer.fields())
        f.setAttributes([f"p{i}", i])
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(i * 0.01, i * 0.01)))
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return layer


# ======================================================================
# unit-level checks (main thread, no sockets)
# ======================================================================
def unit_checks():
    check("unknown commands fail closed as destructive",
          category_for("some_future_command") == "destructive")

    pm = PermissionManager(profile=READ_ONLY)
    verdict, _ = pm.evaluate("get_layers")
    check("read-only allows reads", verdict == "allow", verdict)
    verdict, reason = pm.evaluate("add_features")
    check("read-only denies writes", verdict == "deny", verdict)
    check("denial explains how to fix it", "panel" in reason.lower(), reason)
    verdict, _ = pm.evaluate("execute_code")
    check("read-only denies code", verdict == "deny", verdict)

    pm.set_profile(STANDARD)
    check("standard allows writes", pm.evaluate("add_features")[0] == "allow")
    check("standard asks for deletes",
          pm.evaluate("delete_features")[0] == "approve")
    check("standard refuses code", pm.evaluate("execute_code")[0] == "deny")

    pm.set_profile(FULL)
    check("full asks for code", pm.evaluate("execute_code")[0] == "approve")
    check("full asks for deletes",
          pm.evaluate("delete_features")[0] == "approve")

    pm.set_profile(MASTER)
    check("master allows deletes outright",
          pm.evaluate("delete_features")[0] == "allow")
    check("master allows code outright",
          pm.evaluate("execute_code")[0] == "allow")
    check("master reports itself", pm.is_master() is True)

    pm.set_profile(STANDARD)
    pm.grant("delete_features", minutes=10)
    check("a grant converts approve to allow",
          pm.evaluate("delete_features")[0] == "allow")
    check("grant is listed", "delete_features" in pm.active_grants())
    pm.revoke_grants()
    check("revoking restores the prompt",
          pm.evaluate("delete_features")[0] == "approve")

    check("ping is always allowed even read-only",
          PermissionManager(profile=READ_ONLY).evaluate("ping")[0] == "allow")

    # audit log
    tmp = os.path.join(tempfile.gettempdir(), "qgis_mcp_audit_test.jsonl")
    if os.path.exists(tmp):
        os.remove(tmp)
    log = AuditLog(tmp)
    log.record("delete_features", "denied", client="tester", status="error",
               params={"layer_id": "abc"}, duration_ms=1.2)
    log.record("ping", "allow", client="tester", status="success")
    rows = log.tail()
    check("audit writes rows", len(rows) == 2, len(rows))
    check("audit records verdict", rows[0]["verdict"] == "denied", rows[0])
    check("audit filters failures",
          len(log.tail(only_failures=True)) == 1)
    check("audit filters by command",
          len(log.tail(command="ping")) == 1)
    csv_target = tmp.replace(".jsonl", ".csv")
    written = log.export_csv(csv_target)
    check("audit exports CSV", written == 2 and os.path.exists(csv_target), written)
    disabled = AuditLog(tmp, enabled=False)
    before = len(log.tail())
    disabled.record("ping", "allow")
    check("disabled audit writes nothing", len(log.tail()) == before)
    for path in (tmp, csv_target):
        if os.path.exists(path):
            os.remove(path)


# ======================================================================
# socket-level checks (worker thread)
# ======================================================================
def socket_checks(server, layer, results):
    c = Client()

    # -- read-only ----------------------------------------------------
    server.permissions.set_profile(READ_ONLY)
    r = c.request("get_layers")
    check("read-only: reads succeed", r["status"] == "success")
    r = c.request("add_features", {"layer_id": layer.id(), "features": []})
    check("read-only: writes refused",
          r["status"] == "error" and r["error_type"] == "PermissionDenied",
          r.get("error_type"))
    check("refusal names the profile", r.get("profile") == READ_ONLY, r.get("profile"))
    check("refusal names the category", r.get("category") == "write", r.get("category"))
    r = c.request("execute_code", {"code": "result = 1"})
    check("read-only: code refused", r["status"] == "error"
          and r["error_type"] == "PermissionDenied")

    # -- standard -----------------------------------------------------
    server.permissions.set_profile(STANDARD)
    r = c.request("set_layer_filter", {"layer_id": layer.id(),
                                       "expression": "\"value\" < 5"})
    check("standard: filter allowed",
          r["status"] == "success" and r["result"]["count_after"] == 5,
          r.get("result", {}).get("count_after"))
    c.request("clear_layer_filter", {"layer_id": layer.id()})
    r = c.request("execute_code", {"code": "result = 1"})
    check("standard: code still refused", r["status"] == "error"
          and r["error_type"] == "PermissionDenied")

    # -- approval: denied ---------------------------------------------
    results["deny_started"] = time.time()
    c.send_only("delete_features", {"layer_id": layer.id(),
                                    "expression": "\"value\" < 3",
                                    "confirm": True})
    time.sleep(1.2)                      # let the request park
    while len(server.permissions.pending):   # main thread denies it
        time.sleep(0.1)
    r = c.read()
    check("denied approval returns ApprovalDenied",
          r["status"] == "error" and r["error_type"] == "ApprovalDenied",
          r.get("error_type"))
    remaining = c.request("count_features", {"layer_id": layer.id()})
    check("denial left the data untouched",
          remaining["result"]["count"] == 20, remaining.get("result"))

    # -- approval: approved -------------------------------------------
    c.send_only("delete_features", {"layer_id": layer.id(),
                                    "expression": "\"value\" < 3",
                                    "confirm": True})
    time.sleep(1.2)
    results["approve_id"] = next(iter(server.permissions.pending), None)
    results["approve_ready"] = True
    while len(server.permissions.pending):
        time.sleep(0.1)
    r = c.read()
    check("approved delete runs",
          r["status"] == "success" and r["result"]["deleted"] == 3,
          r.get("result"))
    remaining = c.request("count_features", {"layer_id": layer.id()})
    check("approved delete really deleted",
          remaining["result"]["count"] == 17, remaining.get("result"))

    # -- master control -----------------------------------------------
    server.permissions.set_profile(MASTER)
    started = time.time()
    r = c.request("execute_code", {"code": "result = 7 * 6"})
    elapsed = time.time() - started
    check("master: code runs with no approval",
          r["status"] == "success" and r["result"]["result"] == 42,
          r.get("result", {}).get("result"))
    check("master: no approval delay", elapsed < 2.0, f"{elapsed:.2f}s")
    check("master: queue stayed empty", len(server.permissions.pending) == 0)
    r = c.request("delete_features", {"layer_id": layer.id(),
                                      "expression": "\"value\" = 5",
                                      "confirm": True})
    check("master: delete runs immediately",
          r["status"] == "success" and r["result"]["deleted"] == 1,
          r.get("result", {}).get("deleted"))

    # -- server info reports the state --------------------------------
    info = c.request("get_server_info")
    perms = info["result"]["permissions"]
    check("server_info exposes the profile",
          perms["profile"] == MASTER and perms["master_control"] is True, perms)
    check("server_info counts denials", perms["denied_count"] >= 1, perms)

    # -- audit --------------------------------------------------------
    log = c.request("get_audit_log", {"limit": 200})
    entries = log["result"]["entries"]
    verdicts = {e["verdict"] for e in entries}
    check("audit captured denials", "denied" in verdicts, verdicts)
    check("audit captured approvals", "approved" in verdicts, verdicts)
    commands = {e["command"] for e in entries}
    check("audit captured the delete", "delete_features" in commands)
    failures = c.request("get_audit_log", {"limit": 200, "only_failures": True})
    check("audit filters failures over the wire",
          all(e.get("status") != "success"
              for e in failures["result"]["entries"]),
          len(failures["result"]["entries"]))

    c.close()
    results["done"] = True


def token_checks(port, results):
    c = Client(port=port)
    r = c.request("get_layers")
    check("token required: command refused before hello",
          r["status"] == "error" and r["error_type"] == "Unauthorized",
          r.get("error_type"))
    r = c.request("ping")
    check("ping works without a token", r["status"] == "success")
    r = c.request("hello", {"client": "tester", "token": "wrong-token"})
    check("bad token rejected",
          r["status"] == "error" and r["error_type"] == "Unauthorized")
    r = c.request("get_layers")
    check("still locked out after a bad token",
          r["status"] == "error" and r["error_type"] == "Unauthorized")
    r = c.request("hello", {"client": "tester", "token": results["token"]})
    check("correct token authenticates",
          r["status"] == "success" and r["result"]["hello"] is True,
          r.get("result"))
    r = c.request("get_layers")
    check("commands work after handshake", r["status"] == "success")
    c.close()
    results["token_done"] = True


# ======================================================================
def main():
    unit_checks()

    layer = make_layer()
    audit_file = os.path.join(tempfile.gettempdir(), "qgis_mcp_test_audit.jsonl")
    if os.path.exists(audit_file):
        os.remove(audit_file)

    server = QgisMCPServer(port=PORT, iface=None,
                           permissions=PermissionManager(profile=STANDARD,
                                                         approval_timeout=30),
                           audit=AuditLog(audit_file))
    check("server starts", server.start())
    pump()

    results = {}
    errors = []

    def worker():
        try:
            socket_checks(server, layer, results)
        except Exception:
            import traceback
            errors.append(traceback.format_exc())
            results["done"] = True

    thread = threading.Thread(target=worker)
    thread.start()

    denied_once = approved_once = False
    deadline = time.time() + 120
    while not results.get("done") and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
        pending = server.permissions.pending
        if pending:
            results["pending_seen"] = max(results.get("pending_seen", 0),
                                          len(pending))
        if pending and not denied_once:
            server.permissions.resolve(next(iter(pending)), False, "test denial")
            denied_once = True
        elif pending and denied_once and results.get("approve_ready") \
                and not approved_once:
            server.permissions.resolve(next(iter(pending)), True, "test approval")
            approved_once = True
    thread.join(5)
    if errors:
        print("WORKER EXCEPTION:\n" + errors[0], flush=True)
        FAILED.append("socket worker raised")

    check("a request actually parked for approval",
          results.get("pending_seen", 0) == 1, results.get("pending_seen"))

    # -- approval timeout ---------------------------------------------
    server.permissions.set_profile(STANDARD)
    server.permissions.approval_timeout = 1
    timed_out = {}

    def on_resolved(approved, reason):
        timed_out["approved"] = approved
        timed_out["reason"] = reason

    server.permissions.request_approval("delete_features", {}, "tester",
                                        on_resolved)
    end = time.time() + 5
    while "approved" not in timed_out and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    check("an unanswered request times out as a denial",
          timed_out.get("approved") is False
          and "timed out" in timed_out.get("reason", ""), timed_out)

    server.stop()
    pump()

    # -- token handshake on a second server ---------------------------
    token_server = QgisMCPServer(
        port=PORT + 1, iface=None,
        permissions=PermissionManager(profile=STANDARD), require_token=True)
    check("token server starts", token_server.start())
    pump()
    results["token"] = token_server.token
    check("token is long enough to matter", len(token_server.token) >= 20,
          len(token_server.token))

    t2 = threading.Thread(target=token_checks, args=(PORT + 1, results))
    t2.start()
    deadline = time.time() + 60
    while not results.get("token_done") and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    t2.join(5)

    old_token = token_server.token
    new_token = token_server.rotate_token()
    check("rotating changes the token", new_token != old_token)
    token_server.stop()
    pump()

    # -- panel --------------------------------------------------------
    dock = QgisMCPDockWidget(None)
    check("panel builds five tabs", dock.tabs.count() == 5,
          [dock.tabs.tabText(i) for i in range(dock.tabs.count())])
    check("panel starts stopped", dock.status_pill.text() == "Stopped",
          dock.status_pill.text())

    panel_server = QgisMCPServer(
        port=PORT + 2, iface=None,
        permissions=PermissionManager(profile=STANDARD))
    panel_server.start()
    dock.attach(panel_server)
    pump()
    check("panel shows running state",
          dock.status_pill.text().startswith("Running"), dock.status_pill.text())

    index = dock.profile_combo.findData(MASTER)
    dock.profile_combo.setCurrentIndex(index)
    pump(0.2)
    check("choosing Master Control warns loudly",
          not dock.master_banner.isHidden()
          and "Master Control" in dock.status_pill.text(),
          dock.status_pill.text())
    check("panel pushed the profile to the server",
          panel_server.permissions.profile == MASTER,
          panel_server.permissions.profile)

    index = dock.profile_combo.findData(STANDARD)
    dock.profile_combo.setCurrentIndex(index)
    pump(0.2)
    check("leaving Master Control hides the banner",
          dock.master_banner.isHidden())

    # approval card lifecycle
    captured = {}
    _req = panel_server.permissions.request_approval(
        "delete_features", {"layer_id": "abc"}, "tester",
        lambda ok, reason: captured.update(ok=ok, reason=reason))
    panel_server.approval_needed.emit(_req)
    pump(0.3)
    check("an approval card appears", len(dock._cards) == 1, len(dock._cards))
    check("panel jumps to the Approvals tab", dock.tabs.currentIndex() == 1,
          dock.tabs.currentIndex())
    card = next(iter(dock._cards.values()))
    card.answered.emit(card.request.id, True, True)
    pump(0.3)
    check("approving clears the card", len(dock._cards) == 0, len(dock._cards))
    check("approval reached the caller", captured.get("ok") is True, captured)
    check("'Allow 10 min' created a grant",
          "delete_features" in panel_server.permissions.active_grants(),
          panel_server.permissions.active_grants())

    dock.log_row("--", "unit_test", "success", "")
    check("activity table records rows", dock.log_table.rowCount() > 0,
          dock.log_table.rowCount())
    dock.filter_box.setText("nothing_matches_this")
    hidden = all(dock.log_table.isRowHidden(r)
                 for r in range(dock.log_table.rowCount()))
    check("activity filter hides non-matching rows", hidden)
    dock.filter_box.setText("")

    snippet = dock.build_config_snippet()
    check("config snippet is valid JSON and names the port",
          json.loads(snippet)["mcpServers"]["qgis"]["env"]["QGIS_MCP_PORT"]
          == str(dock.port_spin.value()), snippet[:80])
    python_path, script_path = dock.detect_paths()
    check("path detection found the MCP server script",
          bool(script_path) and script_path.endswith("qgis_mcp_server.py"),
          script_path)
    check("path detection found a venv python",
          bool(python_path) and os.path.exists(python_path or ""), python_path)

    dock.close()
    pump()
    check("closing the panel leaves the server running", panel_server.running)
    panel_server.stop()
    pump()
    check("server stops cleanly", not panel_server.running)

    if os.path.exists(audit_file):
        os.remove(audit_file)


try:
    main()
finally:
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed", flush=True)
    if FAILED:
        print("FAILED: " + ", ".join(FAILED), flush=True)
    QgsApplication.exitQgis()
    sys.exit(1 if FAILED else 0)
