# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""Transport, authentication, permission gating and dispatch.

Assembled with the command handlers in ``mcp_server.py``; this module holds
everything that decides *whether* a command runs, and the wire mechanics that
carry the answer back.
"""

import base64
import collections
import enum
import inspect
import json
import secrets
import time
import traceback

from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtCore import (
    QByteArray, QDate, QDateTime, QElapsedTimer, QObject, Qt, QTime, pyqtSignal,
)
from qgis.PyQt.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from .permissions import APPROVE, DENY, PermissionManager, category_for

LOG_TAG = "QGIS MCP"
PROTOCOL_VERSION = 3
PLUGIN_VERSION = "4.4.0"
PROJECT_NAME = "QGIS MCP toolbox"
PROJECT_AUTHOR = "Adil Munawar"
PROJECT_URL = "https://github.com/Adilmunawar/QGIS_MCP"

MAX_BUFFER_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_SCAN = 2_000_000

#: Answered before the pairing handshake. Keep this set minimal: anything here
#: is reachable by any local process that can open the socket.
PRE_AUTH_COMMANDS = {"ping"}


def _log(message, level=None):
    QgsMessageLog.logMessage(message, LOG_TAG, level or Qgis.MessageLevel.Info)


def generate_token():
    return secrets.token_urlsafe(18)


def _json_default(obj):
    """Coerce QGIS/Qt values that ``json`` cannot serialise on its own."""
    if isinstance(obj, (bytes, bytearray)):
        return base64.b64encode(bytes(obj)).decode("ascii")
    if isinstance(obj, QByteArray):
        return base64.b64encode(bytes(obj.data())).decode("ascii")
    if isinstance(obj, (QDate, QDateTime, QTime)):
        return obj.toString(Qt.DateFormat.ISODate)
    if isinstance(obj, enum.Enum):
        return obj.value if isinstance(obj.value, (int, float, str)) else str(obj)
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    is_null = getattr(obj, "isNull", None)
    if callable(is_null):
        try:
            if is_null():
                return None
        except Exception:
            pass
    return str(obj)


def _dumps(payload):
    return json.dumps(payload, default=_json_default)


class ServerCore(QObject):
    """NDJSON server with authentication and permission-gated dispatch."""

    clients_changed = pyqtSignal(int)
    request_handled = pyqtSignal(str, bool)
    approval_needed = pyqtSignal(object)

    def __init__(self, host="127.0.0.1", port=9876, iface=None,
                 permissions=None, audit=None, token=None, require_token=False,
                 parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port
        self.iface = iface
        self.permissions = permissions or PermissionManager()
        self.audit = audit
        self.token = token or generate_token()
        self.require_token = require_token
        self.requests_served = 0
        self._server = None
        self._buffers = {}
        self._clients = {}
        self._uptime = QElapsedTimer()
        # Commands run on the GUI thread. A handler that spins a nested
        # event loop (blocking HTTP, GDAL over HTTP) lets readyRead fire
        # again mid-command, so messages arriving then are queued.
        self._busy = False
        self._queued = collections.deque()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    @property
    def running(self):
        return self._server is not None and self._server.isListening()

    def uptime_seconds(self):
        return self._uptime.elapsed() // 1000 if self._uptime.isValid() else 0

    def start(self):
        if self.running:
            return True
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        if not self._server.listen(QHostAddress(self.host), self.port):
            reason = self._server.errorString()
            _log(f"Failed to listen on {self.host}:{self.port} - {reason}",
                 Qgis.MessageLevel.Critical)
            self._server.deleteLater()
            self._server = None
            return False
        self._uptime.start()
        _log(f"Server listening on {self.host}:{self.port} "
             f"(protocol v{PROTOCOL_VERSION}, profile {self.permissions.profile})")
        if self.audit:
            self.audit.record("server_start", "allow", status="success",
                              detail=f"{self.host}:{self.port}")
        return True

    def stop(self):
        self.permissions.resolve_all(False, "server stopped")
        stop_monitor = getattr(self, "stop_live_monitor", None)
        if stop_monitor is not None:
            try:
                stop_monitor()
            except Exception:
                pass
        for sock in list(self._buffers):
            try:
                sock.disconnected.disconnect()
            except Exception:
                pass
            sock.abort()
            sock.deleteLater()
        self._buffers.clear()
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        self._uptime.invalidate()
        self.clients_changed.emit(0)
        if self.audit:
            self.audit.record("server_stop", "allow", status="success")
        _log("Server stopped")

    def client_count(self):
        return len(self._buffers)

    def client_list(self):
        return [
            {
                "name": info.get("name", "unnamed"),
                "authenticated": info.get("auth", False),
                "requests": info.get("requests", 0),
            }
            for info in self._clients.values()
        ]

    def rotate_token(self):
        self.token = generate_token()
        if self.audit:
            self.audit.record("rotate_token", "allow", status="success")
        return self.token

    def disconnect_all_clients(self):
        count = len(self._buffers)
        for sock in list(self._buffers):
            sock.disconnectFromHost()
        return count

    # ------------------------------------------------------------------
    # connections
    # ------------------------------------------------------------------
    def _on_new_connection(self):
        while self._server is not None and self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._buffers[sock] = bytearray()
            self._clients[sock] = {"name": "unnamed",
                                   "auth": not self.require_token,
                                   "requests": 0}
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))
            self.clients_changed.emit(self.client_count())

    def _on_disconnected(self, sock):
        self._buffers.pop(sock, None)
        self._clients.pop(sock, None)
        sock.deleteLater()
        self.clients_changed.emit(self.client_count())

    def _on_ready_read(self, sock):
        buf = self._buffers.get(sock)
        if buf is None:
            return
        buf.extend(bytes(sock.readAll().data()))
        if len(buf) > MAX_BUFFER_BYTES:
            _log("Client exceeded buffer limit; dropping connection",
                 Qgis.MessageLevel.Warning)
            self._send(sock, {"status": "error", "error_type": "RequestTooLarge",
                              "message": "Request exceeded buffer limit"})
            sock.disconnectFromHost()
            return
        for message in self._extract_messages(buf):
            if self._busy:
                self._queued.append((sock, message))
            else:
                self._handle_message(sock, message)

    @staticmethod
    def _extract_messages(buf):
        """Pull every complete message out of ``buf``, mutating it in place."""
        messages = []
        while True:
            idx = buf.find(b"\n")
            if idx < 0:
                break
            line = bytes(buf[:idx]).strip()
            del buf[: idx + 1]
            if line:
                messages.append(line)
        if not messages and buf:
            # v1 fallback: a client that never sends a delimiter, but whose
            # buffer already holds exactly one complete JSON document.
            try:
                json.loads(bytes(buf).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                pass
            else:
                messages.append(bytes(buf))
                del buf[:]
        return messages

    def _send(self, sock, payload):
        try:
            data = _dumps(payload).encode("utf-8") + b"\n"
        except Exception as exc:
            data = _dumps({
                "status": "error",
                "error_type": "SerializationError",
                "message": f"Response could not be encoded: {exc}",
            }).encode("utf-8") + b"\n"
        if sock is not None and sock.state() == QTcpSocket.SocketState.ConnectedState:
            sock.write(data)
            sock.flush()

    def _reply(self, sock, payload, request_id):
        if request_id is not None:
            payload["id"] = request_id
        self.requests_served += 1
        self._send(sock, payload)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------
    def _handle_message(self, sock, raw):
        try:
            command = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._reply(sock, {"status": "error", "error_type": "JSONDecodeError",
                               "message": f"Malformed JSON: {exc}"}, None)
            return
        if not isinstance(command, dict):
            self._reply(sock, {"status": "error", "error_type": "InvalidRequest",
                               "message": "Expected a JSON object"}, None)
            return

        request_id = command.get("id")
        cmd = command.get("type")
        params = command.get("params") or {}
        info = self._clients.get(sock, {})
        info["requests"] = info.get("requests", 0) + 1
        client_name = info.get("name", "unnamed")

        if cmd == "hello":
            return self._handle_hello(sock, params, request_id)

        # 'ping' answers before authentication so a client can check liveness
        # without holding the token. It discloses nothing but the protocol
        # version; everything else, get_server_info included, stays gated.
        if self.require_token and not info.get("auth", False) \
                and cmd not in PRE_AUTH_COMMANDS:
            self._audit(cmd, "unauthorized", client_name, "error", params=params)
            self.request_handled.emit(cmd or "<missing>", False)
            self._reply(sock, {
                "status": "error", "error_type": "Unauthorized",
                "message": "Send a 'hello' command with the pairing token first. "
                           "The token is shown in the QGIS MCP panel.",
            }, request_id)
            return

        verdict, reason = self.permissions.evaluate(cmd)
        if verdict == DENY:
            self._audit(cmd, "denied", client_name, "error", detail=reason,
                        params=params)
            self.request_handled.emit(cmd, False)
            self._reply(sock, {
                "status": "error", "error_type": "PermissionDenied",
                "message": reason,
                "profile": self.permissions.profile,
                "category": category_for(cmd),
            }, request_id)
            return

        if verdict == APPROVE:
            self._park_for_approval(sock, cmd, params, request_id, client_name)
            return

        self._run_and_reply(sock, cmd, params, request_id, client_name, "allow")

    def _handle_hello(self, sock, params, request_id):
        info = self._clients.setdefault(sock, {})
        name = str(params.get("client") or "unnamed")[:60]
        info["name"] = name
        if self.require_token:
            supplied = str(params.get("token") or "")
            if not secrets.compare_digest(supplied, self.token):
                info["auth"] = False
                self._audit("hello", "unauthorized", name, "error",
                            detail="bad token")
                self._reply(sock, {
                    "status": "error", "error_type": "Unauthorized",
                    "message": "Invalid pairing token.",
                }, request_id)
                return
        info["auth"] = True
        self._audit("hello", "allow", name, "success")
        self.clients_changed.emit(self.client_count())
        self._reply(sock, {
            "status": "success",
            "result": {
                "hello": True,
                "client": name,
                "protocol_version": PROTOCOL_VERSION,
                "plugin_version": PLUGIN_VERSION,
                "project": PROJECT_NAME,
                "author": PROJECT_AUTHOR,
                "repository": PROJECT_URL,
                "profile": self.permissions.profile,
                "requires_token": self.require_token,
            },
        }, request_id)

    def _park_for_approval(self, sock, cmd, params, request_id, client_name):
        def resolved(approved, reason):
            if not approved:
                self._audit(cmd, "denied", client_name, "error", detail=reason,
                            params=params)
                self.request_handled.emit(cmd, False)
                self._reply(sock, {
                    "status": "error", "error_type": "ApprovalDenied",
                    "message": f"A person declined this request ({reason}).",
                }, request_id)
                return
            self._run_and_reply(sock, cmd, params, request_id, client_name,
                                "approved")

        request = self.permissions.request_approval(cmd, params, client_name,
                                                    resolved)
        self._audit(cmd, "pending", client_name, params=params)
        self.approval_needed.emit(request)

    def _run_and_reply(self, sock, cmd, params, request_id, client_name, verdict):
        started = time.perf_counter()
        self._busy = True
        try:
            response = self.execute_command({"type": cmd, "params": params})
        finally:
            self._busy = False
        elapsed = (time.perf_counter() - started) * 1000
        self._drain_queue()
        ok = response.get("status") == "success"
        self._audit(cmd, verdict, client_name, response.get("status"),
                    detail=response.get("message"), params=params,
                    duration_ms=elapsed)
        self.request_handled.emit(cmd, ok)
        self._reply(sock, response, request_id)

    def _drain_queue(self):
        while self._queued and not self._busy:
            sock, message = self._queued.popleft()
            if sock in self._buffers:
                self._handle_message(sock, message)

    def _audit(self, command, verdict, client, status=None, detail=None,
               params=None, duration_ms=None):
        if self.audit:
            self.audit.record(command, verdict, client=client, status=status,
                              detail=detail, params=params,
                              duration_ms=duration_ms)

    def execute_command(self, command):
        cmd_type = command.get("type")
        params = command.get("params") or {}
        handler = self._handlers().get(cmd_type)
        if handler is None:
            return {
                "status": "error",
                "error_type": "UnknownCommand",
                "message": f"Unknown command type: {cmd_type}",
                "available": sorted(self._handlers()),
            }
        if not isinstance(params, dict):
            return {"status": "error", "error_type": "InvalidParameters",
                    "message": "params must be a JSON object"}
        try:
            inspect.signature(handler).bind(**params)
        except TypeError as exc:
            # A signature mismatch, reported before the handler runs so a
            # TypeError raised inside a handler keeps its traceback below.
            return {"status": "error", "error_type": "InvalidParameters",
                    "message": f"{cmd_type}: {exc}. Accepted parameters: "
                               f"{list(inspect.signature(handler).parameters)}"}
        try:
            return {"status": "success", "result": handler(**params)}
        except Exception as exc:
            _log(f"{cmd_type} failed: {exc}", Qgis.MessageLevel.Critical)
            return {"status": "error", "error_type": type(exc).__name__,
                    "message": str(exc), "traceback": traceback.format_exc()}

    def _handlers(self):  # pragma: no cover - overridden in QgisMCPServer
        return {}
