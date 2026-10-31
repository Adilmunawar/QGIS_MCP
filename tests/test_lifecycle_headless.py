# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin lifecycle tests against a stub iface.

    "C:/Program Files/QGIS 4.2.0/bin/python-qgis.bat" tests/test_lifecycle_headless.py

initGui() and unload() touch the real QGIS iface API - toolbars, menus, the
status bar, dock widgets. Nothing else in the suite exercises them, so a typo
there would ship happily with every other test green. The stub records calls so
the teardown can be checked for symmetry: whatever initGui adds, unload removes.
"""

import os
import socket
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(
    os.environ["APPDATA"], "QGIS", "QGIS4", "profiles", "default",
    "python", "plugins"))

from qgis.core import QgsApplication, QgsSettings  # noqa: E402

app = QgsApplication([], True)
QgsApplication.initQgis()

from qgis.PyQt.QtWidgets import QMainWindow  # noqa: E402

from qgis_mcp_plugin.panel import (  # noqa: E402
    SETTINGS_AUTOSTART, SETTINGS_PORT, SETTINGS_PROFILE,
    SETTINGS_REQUIRE_TOKEN,
)
from qgis_mcp_plugin.permissions import MASTER, READ_ONLY, STANDARD  # noqa: E402
from qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPPlugin, classFactory  # noqa: E402

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"{'PASS' if condition else 'FAIL'}  {name}"
          f"{(' -> ' + str(detail)) if detail else ''}", flush=True)


def pump(seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


class StubMessageBar:
    def __init__(self):
        self.messages = []

    def pushInfo(self, title, text):
        self.messages.append(("info", title, text))

    def pushWarning(self, title, text):
        self.messages.append(("warning", title, text))

    def pushCritical(self, title, text):
        self.messages.append(("critical", title, text))


class StubStatusBar:
    def __init__(self):
        self.widgets = []

    def addPermanentWidget(self, widget, stretch=0):
        self.widgets.append(widget)

    def removeWidget(self, widget):
        if widget in self.widgets:
            self.widgets.remove(widget)


class StubIface:
    """Records what the plugin does to the QGIS shell."""

    def __init__(self):
        self.window = QMainWindow()
        self.menu_actions = []
        self.toolbar_widgets = []
        self.docks = []
        self.status = StubStatusBar()
        self.bar = StubMessageBar()

    def mainWindow(self):
        return self.window

    def messageBar(self):
        return self.bar

    def statusBarIface(self):
        return self.status

    def addPluginToMenu(self, name, action):
        self.menu_actions.append((name, action))

    def removePluginMenu(self, name, action):
        self.menu_actions = [(n, a) for n, a in self.menu_actions
                             if a is not action]

    def addToolBarWidget(self, widget):
        self.toolbar_widgets.append(widget)
        return widget

    def addDockWidget(self, area, dock):
        self.docks.append(dock)
        self.window.addDockWidget(area, dock)

    def removeDockWidget(self, dock):
        if dock in self.docks:
            self.docks.remove(dock)
        self.window.removeDockWidget(dock)

    def mapCanvas(self):
        return None


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def main():
    settings = QgsSettings()
    settings.setValue(SETTINGS_AUTOSTART, False)
    settings.setValue(SETTINGS_REQUIRE_TOKEN, False)
    settings.setValue(SETTINGS_PROFILE, STANDARD)
    port = free_port()
    settings.setValue(SETTINGS_PORT, port)

    iface = StubIface()
    plugin = classFactory(iface)
    check("classFactory returns the plugin", isinstance(plugin, QgisMCPPlugin))

    plugin.initGui()
    pump()
    check("initGui adds the panel and Data Explorer menu entries",
          len(iface.menu_actions) == 2, iface.menu_actions)
    check("initGui adds two toolbar widgets (Data Explorer first)", len(iface.toolbar_widgets) == 2
          and iface.toolbar_widgets[0].defaultAction().text() == "Data Explorer",
          [w.defaultAction().text() for w in iface.toolbar_widgets])
    check("initGui adds a status bar widget", len(iface.status.widgets) == 1)
    check("status widget starts as off",
          "off" in iface.status.widgets[0].text().lower(),
          iface.status.widgets[0].text())
    check("the toolbar button carries the icon",
          not plugin.action.icon().isNull())
    check("the toolbar button has a dropdown menu",
          plugin.tool_button.menu() is not None)

    # -- panel appears on demand ---------------------------------------
    plugin.toggle_dock(True)
    pump()
    check("toggling adds the dock", len(iface.docks) == 1, len(iface.docks))
    check("the dock has all five tabs", plugin.dock.tabs.count() == 5,
          plugin.dock.tabs.count())

    # -- start / stop ---------------------------------------------------
    started = plugin.start_server()
    pump()
    check("start_server starts a server", started and plugin.server is not None
          and plugin.server.running)
    check("the panel was attached to the server",
          plugin.dock.server is plugin.server)
    check("status widget shows the port and profile",
          str(port) in iface.status.widgets[0].text()
          and "Standard" in iface.status.widgets[0].text(),
          iface.status.widgets[0].text())
    check("the panel detected paths for the config snippet",
          "mcpServers" in plugin.dock.config_preview.toPlainText(),
          plugin.dock.config_preview.toPlainText()[:60])

    # a real client can reach it
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(b'{"type": "ping", "params": {}}\n')
            pump(0.5)
            reply = sock.recv(4096)
        check("a client can ping the started server", b'"pong": true' in reply,
              reply[:60])
    except OSError as exc:
        check("a client can ping the started server", False, exc)

    # -- port conflict --------------------------------------------------
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    taken = blocker.getsockname()[1]
    blocker.listen(1)
    second_iface = StubIface()
    second = classFactory(second_iface)
    second.initGui()
    second.dock = second._ensure_dock()
    second.dock.port_spin.setValue(taken)
    ok = second.start_server()
    pump()
    check("binding a taken port fails cleanly", ok is False and second.server is None)
    check("the failure is reported to the user",
          any(level == "critical" for level, _, _ in second_iface.bar.messages),
          second_iface.bar.messages[-1:] if second_iface.bar.messages else None)
    check("the failure suggests a free port",
          any("looks free" in text for _, _, text in second_iface.bar.messages),
          [t for _, _, t in second_iface.bar.messages][-1:])
    suggested = QgisMCPPlugin._suggest_free_port(taken)
    check("the suggested port is actually free", suggested != taken)
    second.unload()
    blocker.close()
    pump()

    # -- profile changes propagate --------------------------------------
    index = plugin.dock.profile_combo.findData(MASTER)
    plugin.dock.profile_combo.setCurrentIndex(index)
    pump()
    check("switching to Master Control reaches the server",
          plugin.server.permissions.profile == MASTER,
          plugin.server.permissions.profile)
    plugin._refresh_status_widget()
    check("the status widget warns during Master Control",
          "Master" in iface.status.widgets[0].text(),
          iface.status.widgets[0].text())
    plugin.dock.profile_combo.setCurrentIndex(
        plugin.dock.profile_combo.findData(STANDARD))
    pump()

    # -- closing the panel must not stop the server ----------------------
    plugin.dock.close()
    pump()
    check("closing the panel leaves the server running", plugin.server.running)

    plugin.stop_server()
    pump()
    check("stop_server stops it", plugin.server is None)
    check("status widget returns to off",
          "off" in iface.status.widgets[0].text().lower(),
          iface.status.widgets[0].text())

    # -- unload undoes everything initGui did ----------------------------
    plugin.start_server()
    pump()
    running_port = plugin.server.port
    plugin.unload()
    pump()
    check("unload removes the menu entry", not iface.menu_actions)
    check("unload removes the status widget", not iface.status.widgets)
    check("unload removes the dock", not iface.docks)
    check("unload clears plugin state",
          plugin.server is None and plugin.dock is None
          and plugin.action is None)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", running_port))
            freed = True
        except OSError:
            freed = False
    check("unload released the port", freed, running_port)

    # -- autostart -------------------------------------------------------
    auto_port = free_port()
    settings.setValue(SETTINGS_AUTOSTART, True)
    settings.setValue(SETTINGS_PORT, auto_port)
    settings.setValue(SETTINGS_PROFILE, READ_ONLY)
    auto_iface = StubIface()
    auto = classFactory(auto_iface)
    auto.initGui()
    pump()
    check("autostart starts the server on load",
          auto.server is not None and auto.server.running)
    check("autostart honours the stored profile",
          auto.server.permissions.profile == READ_ONLY,
          auto.server.permissions.profile if auto.server else None)
    check("autostart checks the toolbar action", auto.action.isChecked())
    auto.unload()
    pump()
    settings.setValue(SETTINGS_AUTOSTART, False)
    settings.setValue(SETTINGS_PROFILE, STANDARD)
    check("unload after autostart leaves nothing running", auto.server is None)


try:
    main()
finally:
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed", flush=True)
    if FAILED:
        print("FAILED: " + ", ".join(FAILED), flush=True)
    QgsApplication.exitQgis()
    sys.exit(1 if FAILED else 0)
