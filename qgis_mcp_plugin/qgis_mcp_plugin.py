# -*- coding: utf-8 -*-
"""QGIS MCP - plugin entry point.

The pieces live in their own modules:

===================  ==========================================================
``_server_core``     transport, authentication, permission gating, dispatch
``mcp_server``       the command surface
``permissions``      profiles, temporary grants, the approval queue
``audit``            append-only JSONL trail
``panel``            the dock widget
===================  ==========================================================
"""

import os

from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMenu, QToolButton

from .audit import AuditLog
from .explorer import ExplorerDock
from .mcp_server import QgisMCPServer
from .panel import (
    SETTINGS_APPROVAL_TIMEOUT, SETTINGS_AUDIT, SETTINGS_AUTOSTART,
    SETTINGS_PORT, SETTINGS_PROFILE, SETTINGS_REQUIRE_TOKEN,
    QgisMCPDockWidget, as_bool,
)
from .permissions import PermissionManager, STANDARD
from ._server_core import PLUGIN_VERSION, PROTOCOL_VERSION

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(PLUGIN_DIR, "icon.svg")

__all__ = [
    "QgisMCPPlugin", "QgisMCPServer", "QgisMCPDockWidget", "classFactory",
    "ICON_PATH", "PLUGIN_VERSION", "PROTOCOL_VERSION",
]


def audit_path():
    return os.path.join(QgsApplication.qgisSettingsDirPath(),
                        "qgis_mcp_audit.jsonl")


class QgisMCPPlugin:
    """Owns the server's lifetime; the panel only drives it."""

    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.explorer = None
        self.explorer_action = None
        self.server = None
        self.action = None
        self.tool_button = None
        self.status_label = None

    # ------------------------------------------------------------------
    # QGIS hooks
    # ------------------------------------------------------------------
    def initGui(self):
        icon = QIcon(ICON_PATH) if os.path.exists(ICON_PATH) else QIcon()
        self.action = QAction(icon, "QGIS MCP", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip("QGIS MCP - bridge QGIS to an MCP client")
        self.action.triggered.connect(self.toggle_dock)

        # A split button keeps the common actions one click away without
        # forcing the panel open first.
        menu = QMenu()
        self.start_action = menu.addAction("Start server", self.start_server)
        self.stop_action = menu.addAction("Stop server", self.stop_server)
        menu.addSeparator()
        menu.addAction("Copy client config", self._copy_config)
        menu.addAction("Open audit log", self._open_audit)

        self.tool_button = QToolButton()
        self.tool_button.setDefaultAction(self.action)
        self.tool_button.setMenu(menu)
        self.tool_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.iface.addToolBarWidget(self.tool_button)
        self.iface.addPluginToMenu("QGIS MCP", self.action)

        self.explorer_action = QAction(icon, "Data Explorer", self.iface.mainWindow())
        self.explorer_action.setCheckable(True)
        self.explorer_action.setToolTip(
            "Data Explorer - pull satellite, weather, elevation and OpenStreetMap "
            "data for an area with clicks")
        self.explorer_action.triggered.connect(self.toggle_explorer)
        menu.addSeparator()
        menu.addAction(self.explorer_action)
        self.iface.addPluginToMenu("QGIS MCP", self.explorer_action)

        self._install_status_widget()
        self._refresh_status_widget()

        if as_bool(QgsSettings().value(SETTINGS_AUTOSTART, False)):
            self._ensure_dock()
            self.start_server()
            self.action.setChecked(True)

    def unload(self):
        self.stop_server()
        if self.dock:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.explorer:
            self.explorer.cleanup()
            self.iface.removeDockWidget(self.explorer)
            self.explorer.deleteLater()
            self.explorer = None
        if self.explorer_action:
            self.iface.removePluginMenu("QGIS MCP", self.explorer_action)
            self.explorer_action = None
        if self.status_label is not None:
            self.iface.statusBarIface().removeWidget(self.status_label)
            self.status_label.deleteLater()
            self.status_label = None
        if self.action:
            self.iface.removePluginMenu("QGIS MCP", self.action)
            self.action = None
        if self.tool_button is not None:
            self.tool_button.deleteLater()
            self.tool_button = None

    # ------------------------------------------------------------------
    # status bar presence
    # ------------------------------------------------------------------
    def _install_status_widget(self):
        try:
            from qgis.PyQt.QtWidgets import QLabel
            self.status_label = QLabel()
            self.status_label.setToolTip("QGIS MCP server state")
            self.iface.statusBarIface().addPermanentWidget(self.status_label, 0)
        except Exception:
            self.status_label = None

    def _refresh_status_widget(self):
        if self.status_label is None:
            return
        if self.server and self.server.running:
            profile = self.server.permissions.profile_label()
            colour = "#c0392b" if self.server.permissions.is_master() else "#1f8a5f"
            self.status_label.setText(
                f"MCP {self.server.port} - {profile}")
            self.status_label.setStyleSheet(f"color:{colour}; font-weight:600;")
        else:
            self.status_label.setText("MCP off")
            self.status_label.setStyleSheet("color: palette(mid);")

    # ------------------------------------------------------------------
    # panel
    # ------------------------------------------------------------------
    def _ensure_dock(self):
        if self.dock is None:
            self.dock = QgisMCPDockWidget(self.iface, icon_path=ICON_PATH)
            self.iface.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.dock.closed.connect(self._dock_closed)
            self.dock.start_requested.connect(self.start_server)
            self.dock.stop_requested.connect(self.stop_server)
            if self.server is not None:
                self.dock.attach(self.server)
        return self.dock

    def _ensure_explorer(self):
        if self.explorer is None:
            self.explorer = ExplorerDock(self.iface, icon_path=ICON_PATH)
            self.iface.addDockWidget(
                Qt.DockWidgetArea.LeftDockWidgetArea, self.explorer)
            self.explorer.visibilityChanged.connect(self._explorer_visibility)
        return self.explorer

    def _explorer_visibility(self, visible):
        if self.explorer_action is not None \
                and self.explorer_action.isChecked() != visible:
            self.explorer_action.setChecked(visible)

    def toggle_explorer(self, checked):
        if checked:
            dock = self._ensure_explorer()
            dock.show()
            dock.raise_()
        elif self.explorer:
            self.explorer.hide()

    def toggle_dock(self, checked):
        if checked:
            self._ensure_dock().show()
        elif self.dock:
            self.dock.hide()

    def _dock_closed(self):
        if self.action:
            self.action.setChecked(False)

    # ------------------------------------------------------------------
    # server lifetime
    # ------------------------------------------------------------------
    def start_server(self):
        if self.server and self.server.running:
            return True
        settings = QgsSettings()
        dock = self._ensure_dock()
        port = dock.port_spin.value()

        permissions = PermissionManager(
            profile=settings.value(SETTINGS_PROFILE, STANDARD),
            approval_timeout=int(settings.value(SETTINGS_APPROVAL_TIMEOUT, 60)))
        audit = AuditLog(audit_path(),
                         enabled=as_bool(settings.value(SETTINGS_AUDIT, True), True))

        self.server = QgisMCPServer(
            port=port, iface=self.iface, permissions=permissions, audit=audit,
            require_token=as_bool(settings.value(SETTINGS_REQUIRE_TOKEN, False)))

        if not self.server.start():
            free = self._suggest_free_port(port)
            dock.log_row("--", "start", "error",
                         f"port {port} unavailable; try {free}")
            if self.iface is not None:
                self.iface.messageBar().pushCritical(
                    "QGIS MCP",
                    f"Could not bind port {port}. Port {free} looks free.")
            self.server = None
            dock.refresh()
            self._refresh_status_widget()
            return False

        settings.setValue(SETTINGS_PORT, port)
        dock.attach(self.server)
        dock.build_config_snippet()
        dock.log_row("--", "start", "ok", f"127.0.0.1:{port}")
        self._refresh_status_widget()
        return True

    def stop_server(self):
        if self.server:
            self.server.stop()
            self.server = None
        if self.dock:
            self.dock.detach()
            self.dock.log_row("--", "stop", "ok", "")
        self._refresh_status_widget()

    @staticmethod
    def _suggest_free_port(start):
        import socket
        for candidate in range(start + 1, start + 40):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind(("127.0.0.1", candidate))
                    return candidate
                except OSError:
                    continue
        return start + 1

    # ------------------------------------------------------------------
    # menu actions
    # ------------------------------------------------------------------
    def _copy_config(self):
        self._ensure_dock()._copy_config()

    def _open_audit(self):
        self._ensure_dock()._open_audit_file()


def classFactory(iface):
    return QgisMCPPlugin(iface)
