# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""The QGIS MCP control panel.

Design notes
------------
Colours come from the active QGIS palette rather than hardcoded hex, so the
panel holds up in dark themes instead of stamping light-theme chips onto a dark
ground. Only the semantic accents (ok / warn / danger) are fixed, and each is
chosen to clear 4.5:1 against both grounds.

The panel is scanned, not read: state is encoded in shape and colour as well as
number, so "is it safe right now" is answerable at a glance rather than by
reading three labels.
"""

import os

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QSize, Qt, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDockWidget,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from .command_help import describe
from .permissions import (
    ALLOW, APPROVE, COMMAND_CATEGORIES, DENY, MASTER, PROFILE_ORDER,
    PROFILES, READ_ONLY, STANDARD, category_for,
)

OK = "#1f8a5f"
WARN = "#b7791f"
DANGER = "#c0392b"
MUTED_ALPHA = "color: palette(mid);"

SETTINGS_PORT = "qgis_mcp/port"
SETTINGS_PROFILE = "qgis_mcp/profile"
SETTINGS_AUTOSTART = "qgis_mcp/autostart"
SETTINGS_REQUIRE_TOKEN = "qgis_mcp/require_token"
SETTINGS_APPROVAL_TIMEOUT = "qgis_mcp/approval_timeout"
SETTINGS_AUDIT = "qgis_mcp/audit_enabled"
SETTINGS_SERVER_SCRIPT = "qgis_mcp/server_script"


def as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def pill(text, colour, filled=True):
    label = QLabel(text)
    if filled:
        label.setStyleSheet(
            f"background:{colour}; color:#ffffff; border-radius:9px;"
            "padding:3px 10px; font-weight:600;")
    else:
        label.setStyleSheet(
            f"border:1px solid {colour}; color:{colour}; border-radius:9px;"
            "padding:2px 9px; font-weight:600;")
    return label


def caption(text):
    label = QLabel(text)
    label.setStyleSheet("color: palette(mid); font-size:10px;")
    label.setWordWrap(True)
    return label


def hline():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: palette(mid);")
    return line


class MetricTile(QWidget):
    """A single scannable number with its label underneath."""

    def __init__(self, caption_text, value="0", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(0)
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet("font-size:17px; font-weight:700;")
        self.caption_label = QLabel(caption_text.upper())
        self.caption_label.setStyleSheet(
            "color: palette(mid); font-size:9px; letter-spacing:1px;")
        layout.addWidget(self.value_label)
        layout.addWidget(self.caption_label)
        self.setStyleSheet(
            "MetricTile { background: palette(alternate-base); border-radius:5px; }")

    def set_value(self, value, emphasis=None):
        self.value_label.setText(str(value))
        colour = f"color:{emphasis};" if emphasis else ""
        self.value_label.setStyleSheet(
            f"font-size:17px; font-weight:700; {colour}")


class ApprovalCard(QFrame):
    """One pending request, with the three answers a person actually needs."""

    answered = pyqtSignal(str, bool, bool)  # request_id, approved, remember

    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.request = request
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"ApprovalCard {{ border:1px solid {WARN}; border-radius:5px; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 8, 9, 8)
        layout.setSpacing(5)

        head = QHBoxLayout()
        head.addWidget(pill(request.category.upper(), WARN))
        title = QLabel(f"<b>{request.command}</b>")
        head.addWidget(title)
        head.addStretch()
        self.countdown = QLabel("")
        self.countdown.setStyleSheet("color: palette(mid); font-size:10px;")
        head.addWidget(self.countdown)
        layout.addLayout(head)

        detail = QLabel(request.summary())
        detail.setWordWrap(True)
        detail.setStyleSheet("font-family: monospace; font-size:10px;")
        layout.addWidget(detail)

        origin = caption(f"requested by {request.client}")
        layout.addWidget(origin)

        buttons = QHBoxLayout()
        approve = QPushButton("Approve")
        approve.setStyleSheet(
            f"background:{OK}; color:#fff; font-weight:600; padding:4px;")
        approve.clicked.connect(
            lambda: self.answered.emit(self.request.id, True, False))
        deny = QPushButton("Deny")
        deny.clicked.connect(
            lambda: self.answered.emit(self.request.id, False, False))
        allow_10 = QPushButton("Allow 10 min")
        allow_10.setToolTip(
            f"Approve, and stop asking about '{request.command}' for 10 minutes")
        allow_10.clicked.connect(
            lambda: self.answered.emit(self.request.id, True, True))
        buttons.addWidget(approve)
        buttons.addWidget(deny)
        buttons.addWidget(allow_10)
        layout.addLayout(buttons)

    def tick(self):
        self.countdown.setText(f"{self.request.seconds_left()}s left")


class QgisMCPDockWidget(QDockWidget):
    """Control panel. Closing it hides the panel; it does not stop the server."""

    closed = pyqtSignal()
    start_requested = pyqtSignal()
    explorer_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    MAX_LOG_BLOCKS = 800

    def __init__(self, iface, icon_path=None, parent=None):
        super().__init__("QGIS MCP", parent)
        self.iface = iface
        self.icon_path = icon_path
        self.server = None
        self.permissions = None
        self._cards = {}
        self._settings = QgsSettings()
        if icon_path and os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self._build()
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)

    # ==================================================================
    # construction
    # ==================================================================
    def _build(self):
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_control_tab(), "Control")
        self.tabs.addTab(self._build_approvals_tab(), "Approvals")
        self.tabs.addTab(self._build_activity_tab(), "Activity")
        self.tabs.addTab(self._build_commands_tab(), "Commands")
        self.tabs.addTab(self._build_setup_tab(), "Setup")
        self.setWidget(self.tabs)
        self.refresh()

    # -- control -------------------------------------------------------
    def _build_control_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(9)

        head = QHBoxLayout()
        if self.icon_path and os.path.exists(self.icon_path):
            logo = QLabel()
            logo.setPixmap(QIcon(self.icon_path).pixmap(QSize(30, 30)))
            head.addWidget(logo)
        title = QLabel("QGIS MCP server")
        title.setStyleSheet("font-size:15px; font-weight:700;")
        head.addWidget(title)
        head.addStretch()
        self.status_pill = QLabel("Stopped")
        head.addWidget(self.status_pill)
        layout.addLayout(head)

        explorer_row = QHBoxLayout()
        explorer_hint = caption("Looking for the data toolbox? That is the Data Explorer.")
        self.explorer_button = QPushButton("Open Data Explorer")
        self.explorer_button.setStyleSheet("font-weight:600;")
        self.explorer_button.setToolTip("Area of interest, imagery, weather, land, water, species, "
                                        "sensors, 3D, population and a live monitor, all by clicking")
        self.explorer_button.clicked.connect(self.explorer_requested.emit)
        explorer_row.addWidget(explorer_hint, 1)
        explorer_row.addWidget(self.explorer_button)
        layout.addLayout(explorer_row)

        self.address_label = caption("not listening")
        layout.addWidget(self.address_label)
        from ._server_core import PLUGIN_VERSION, PROJECT_AUTHOR, PROJECT_NAME, PROJECT_URL
        credit = QLabel(f"<a href='{PROJECT_URL}' style='color:#7aa7d9;text-decoration:none'>"
                        f"{PROJECT_NAME} {PLUGIN_VERSION}</a> by {PROJECT_AUTHOR}")
        credit.setOpenExternalLinks(True)
        credit.setStyleSheet("color:#8a8a8a; font-size:11px;")
        layout.addWidget(credit)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.start_requested.emit)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.stop_button.setEnabled(False)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(int(self._settings.value(SETTINGS_PORT, 9876)))
        self.port_spin.setToolTip("TCP port on 127.0.0.1")
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(QLabel("Port"))
        controls.addWidget(self.port_spin)
        layout.addLayout(controls)

        layout.addWidget(hline())

        access = QLabel("ACCESS LEVEL")
        access.setStyleSheet(
            "color: palette(mid); font-size:9px; letter-spacing:1px;")
        layout.addWidget(access)

        self.profile_combo = QComboBox()
        for key in PROFILE_ORDER:
            self.profile_combo.addItem(PROFILES[key]["label"], key)
        stored = self._settings.value(SETTINGS_PROFILE, STANDARD)
        index = self.profile_combo.findData(stored)
        self.profile_combo.setCurrentIndex(index if index >= 0 else 1)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        layout.addWidget(self.profile_combo)

        self.profile_blurb = caption("")
        layout.addWidget(self.profile_blurb)

        self.master_banner = QLabel(
            "Master Control is on. Every command runs immediately, including "
            "deletions and arbitrary Python. Nothing will ask you first.")
        self.master_banner.setWordWrap(True)
        self.master_banner.setStyleSheet(
            f"background:{DANGER}; color:#ffffff; border-radius:4px;"
            "padding:6px; font-weight:600; font-size:10px;")
        self.master_banner.hide()
        layout.addWidget(self.master_banner)

        layout.addWidget(hline())

        metrics = QGridLayout()
        metrics.setSpacing(5)
        self.tile_requests = MetricTile("Requests")
        self.tile_clients = MetricTile("Clients")
        self.tile_uptime = MetricTile("Uptime", "-")
        self.tile_pending = MetricTile("Pending")
        self.tile_denied = MetricTile("Denied")
        self.tile_grants = MetricTile("Grants")
        for i, tile in enumerate((self.tile_requests, self.tile_clients,
                                  self.tile_uptime)):
            metrics.addWidget(tile, 0, i)
        for i, tile in enumerate((self.tile_pending, self.tile_denied,
                                  self.tile_grants)):
            metrics.addWidget(tile, 1, i)
        layout.addLayout(metrics)

        self.last_command = caption("no requests yet")
        layout.addWidget(self.last_command)

        layout.addStretch()
        return page

    # -- approvals -----------------------------------------------------
    def _build_approvals_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.approval_header = QLabel("Nothing waiting")
        self.approval_header.setStyleSheet("font-weight:600;")
        layout.addWidget(self.approval_header)
        layout.addWidget(caption(
            "Destructive commands park here instead of running. "
            "No answer within the timeout counts as a refusal."))

        self.cards_host = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(6)
        layout.addWidget(self.cards_host)

        bulk = QHBoxLayout()
        approve_all = QPushButton("Approve all")
        approve_all.clicked.connect(lambda: self._resolve_all(True))
        deny_all = QPushButton("Deny all")
        deny_all.clicked.connect(lambda: self._resolve_all(False))
        bulk.addWidget(approve_all)
        bulk.addWidget(deny_all)
        layout.addLayout(bulk)

        layout.addWidget(hline())
        grants_caption = QLabel("ACTIVE GRANTS")
        grants_caption.setStyleSheet(
            "color: palette(mid); font-size:9px; letter-spacing:1px;")
        layout.addWidget(grants_caption)
        self.grants_label = caption("none")
        layout.addWidget(self.grants_label)
        revoke = QPushButton("Revoke all grants")
        revoke.clicked.connect(self._revoke_grants)
        layout.addWidget(revoke)

        layout.addStretch()
        return page


    # -- commands ------------------------------------------------------
    CATEGORY_COLOURS = {"read": OK, "write": "#2f6f9f",
                        "destructive": DANGER, "code": DANGER}

    def _build_commands_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.commands_summary = QLabel("")
        self.commands_summary.setStyleSheet("font-weight:600;")
        layout.addWidget(self.commands_summary)
        layout.addWidget(caption(
            "Every command the bridge exposes, and what the current access "
            "level does with it. Changing the level updates this list."))

        search = QHBoxLayout()
        self.command_search = QLineEdit()
        self.command_search.setPlaceholderText("Search commands...")
        self.command_search.textChanged.connect(self._filter_commands)
        self.blocked_only = QCheckBox("Blocked only")
        self.blocked_only.stateChanged.connect(self._filter_commands)
        search.addWidget(self.command_search)
        search.addWidget(self.blocked_only)
        layout.addLayout(search)

        self.commands_table = QTableWidget(0, 4)
        self.commands_table.setHorizontalHeaderLabels(
            ["Command", "Risk", "Right now", "What it does"])
        self.commands_table.verticalHeader().setVisible(False)
        self.commands_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.commands_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.commands_table.setStyleSheet("font-size:10px;")
        header = self.commands_table.horizontalHeader()
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.commands_table)

        copy_list = QPushButton("Copy command list")
        copy_list.clicked.connect(self._copy_command_list)
        layout.addWidget(copy_list)

        self.populate_commands()
        return page

    def populate_commands(self):
        """Rebuild the command list against the current access level."""
        commands = sorted(COMMAND_CATEGORIES)
        if self.server is not None:
            # Prefer what the running server really exposes.
            live = self.server.execute_command({"type": "__unknown__"})
            commands = sorted(live.get("available", commands))
        self.commands_table.setRowCount(0)
        counts = {ALLOW: 0, APPROVE: 0, DENY: 0}
        for command in commands:
            verdict = self._verdict_for(command)
            counts[verdict] = counts.get(verdict, 0) + 1
            row = self.commands_table.rowCount()
            self.commands_table.insertRow(row)

            name_item = QTableWidgetItem(command)
            category = category_for(command)
            cat_item = QTableWidgetItem(category)
            colour = self.CATEGORY_COLOURS.get(category)
            if colour:
                cat_item.setForeground(QColor(colour))

            label = {ALLOW: "allowed", APPROVE: "asks first",
                     DENY: "refused"}[verdict]
            verdict_item = QTableWidgetItem(label)
            verdict_item.setForeground(QColor(
                {ALLOW: OK, APPROVE: WARN, DENY: DANGER}[verdict]))

            for col, item in enumerate((name_item, cat_item, verdict_item,
                                        QTableWidgetItem(describe(command)))):
                self.commands_table.setItem(row, col, item)
        self.commands_table.resizeColumnsToContents()
        self.commands_summary.setText(
            f"{len(commands)} commands  -  {counts[ALLOW]} allowed, "
            f"{counts[APPROVE]} ask first, {counts[DENY]} refused")
        self._filter_commands()

    def _verdict_for(self, command):
        if self.permissions is not None:
            return self.permissions.evaluate(command)[0]
        profile = self.selected_profile()
        if command in ("ping", "get_server_info"):
            return ALLOW
        return PROFILES[profile]["rules"][category_for(command)]

    def _filter_commands(self):
        needle = self.command_search.text().strip().lower()
        blocked_only = self.blocked_only.isChecked()
        for row in range(self.commands_table.rowCount()):
            name = self.commands_table.item(row, 0).text().lower()
            blurb = self.commands_table.item(row, 3).text().lower()
            verdict = self.commands_table.item(row, 2).text()
            hide = (needle and needle not in name and needle not in blurb) or                    (blocked_only and verdict == "allowed")
            self.commands_table.setRowHidden(row, bool(hide))

    def _copy_command_list(self):
        lines = []
        for row in range(self.commands_table.rowCount()):
            lines.append("{:<32} {:<12} {:<10} {}".format(
                self.commands_table.item(row, 0).text(),
                self.commands_table.item(row, 1).text(),
                self.commands_table.item(row, 2).text(),
                self.commands_table.item(row, 3).text()))
        QApplication.clipboard().setText(os.linesep.join(lines))
        if self.iface is not None:
            self.iface.messageBar().pushInfo(
                "QGIS MCP", f"Copied {len(lines)} commands")

    # -- activity ------------------------------------------------------
    def _build_activity_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        filters = QHBoxLayout()
        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("Filter by command...")
        self.filter_box.textChanged.connect(self._apply_log_filter)
        self.failures_only = QCheckBox("Failures only")
        self.failures_only.stateChanged.connect(self._apply_log_filter)
        filters.addWidget(self.filter_box)
        filters.addWidget(self.failures_only)
        layout.addLayout(filters)

        self.log_table = QTableWidget(0, 4)
        self.log_table.setHorizontalHeaderLabels(["Time", "Command", "Result", "ms"])
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.log_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.log_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.log_table.setStyleSheet("font-size:10px;")
        layout.addWidget(self.log_table)

        buttons = QHBoxLayout()
        clear = QPushButton("Clear view")
        clear.clicked.connect(lambda: self.log_table.setRowCount(0))
        export = QPushButton("Export audit CSV")
        export.clicked.connect(self._export_audit)
        open_log = QPushButton("Open log file")
        open_log.clicked.connect(self._open_audit_file)
        buttons.addWidget(clear)
        buttons.addWidget(export)
        buttons.addWidget(open_log)
        layout.addLayout(buttons)
        return page

    # -- setup ---------------------------------------------------------
    def _build_setup_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel("<b>Pairing</b>"))
        self.token_check = QCheckBox("Require a pairing token")
        self.token_check.setChecked(
            as_bool(self._settings.value(SETTINGS_REQUIRE_TOKEN, False)))
        self.token_check.setToolTip(
            "When on, a client must send the token below before any command runs.")
        self.token_check.stateChanged.connect(self._on_token_toggled)
        layout.addWidget(self.token_check)

        token_row = QHBoxLayout()
        self.token_field = QLineEdit()
        self.token_field.setReadOnly(True)
        self.token_field.setEchoMode(QLineEdit.EchoMode.Password)
        show = QToolButton()
        show.setText("Show")
        show.setCheckable(True)
        show.toggled.connect(
            lambda on: self.token_field.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        copy_token = QToolButton()
        copy_token.setText("Copy")
        copy_token.clicked.connect(self._copy_token)
        rotate = QToolButton()
        rotate.setText("New")
        rotate.setToolTip("Generate a new token; existing clients must reconnect")
        rotate.clicked.connect(self._rotate_token)
        token_row.addWidget(self.token_field)
        token_row.addWidget(show)
        token_row.addWidget(copy_token)
        token_row.addWidget(rotate)
        layout.addLayout(token_row)

        layout.addWidget(hline())
        layout.addWidget(QLabel("<b>Client configuration</b>"))
        layout.addWidget(caption(
            "Detects the Python and server script on this machine and builds a "
            "config you can paste straight in."))
        self.config_preview = QPlainTextEdit()
        self.config_preview.setReadOnly(True)
        self.config_preview.setMaximumHeight(120)
        self.config_preview.setStyleSheet("font-family: monospace; font-size:10px;")
        layout.addWidget(self.config_preview)
        config_buttons = QHBoxLayout()
        detect = QPushButton("Detect paths")
        detect.clicked.connect(self.build_config_snippet)
        browse = QPushButton("Locate manually")
        browse.setToolTip("Point at qgis_mcp_server.py yourself if detection misses it")
        browse.clicked.connect(self.browse_for_script)
        copy_config = QPushButton("Copy config")
        copy_config.clicked.connect(self._copy_config)
        config_buttons.addWidget(detect)
        config_buttons.addWidget(browse)
        config_buttons.addWidget(copy_config)
        layout.addLayout(config_buttons)

        layout.addWidget(hline())
        form = QFormLayout()
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 600)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setValue(
            int(self._settings.value(SETTINGS_APPROVAL_TIMEOUT, 60)))
        self.timeout_spin.valueChanged.connect(self._on_timeout_changed)
        form.addRow("Approval timeout:", self.timeout_spin)
        layout.addLayout(form)

        self.autostart_check = QCheckBox("Start server when QGIS opens")
        self.autostart_check.setChecked(
            as_bool(self._settings.value(SETTINGS_AUTOSTART, False)))
        self.autostart_check.stateChanged.connect(
            lambda: self._settings.setValue(
                SETTINGS_AUTOSTART, self.autostart_check.isChecked()))
        layout.addWidget(self.autostart_check)

        self.audit_check = QCheckBox("Write an audit log to disk")
        self.audit_check.setChecked(
            as_bool(self._settings.value(SETTINGS_AUDIT, True), True))
        self.audit_check.stateChanged.connect(self._on_audit_toggled)
        layout.addWidget(self.audit_check)

        folder = QPushButton("Open plugin folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(os.path.dirname(os.path.abspath(__file__)))))
        layout.addWidget(folder)

        layout.addStretch()
        return page

    # ==================================================================
    # wiring
    # ==================================================================
    def attach(self, server):
        """Bind a live server to the panel."""
        self.server = server
        self.permissions = server.permissions
        # The running server is the authority on the access level. Adopt it
        # rather than asserting the combo's value, so the panel can never show
        # a level the server is not actually enforcing.
        index = self.profile_combo.findData(server.permissions.profile)
        if index >= 0 and index != self.profile_combo.currentIndex():
            blocked = self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(index)
            self.profile_combo.blockSignals(blocked)
        server.clients_changed.connect(self._on_clients_changed)
        server.request_handled.connect(self._on_request_handled)
        server.approval_needed.connect(self.add_approval)
        server.permissions.queue_changed.connect(self.refresh_approvals)
        server.permissions.decision_made.connect(self._on_decision)
        self.token_field.setText(server.token)
        self._tick.start()
        self.populate_commands()
        self.refresh()

    def detach(self):
        self.server = None
        self._tick.stop()
        self._clear_cards()
        self.refresh()

    # ==================================================================
    # state
    # ==================================================================
    def selected_profile(self):
        return self.profile_combo.currentData()

    def refresh(self):
        running = bool(self.server and self.server.running)
        profile = self.selected_profile()
        self.profile_blurb.setText(PROFILES[profile]["blurb"])
        self.master_banner.setVisible(profile == MASTER)

        if running and profile == MASTER:
            self._set_pill("Running - Master Control", DANGER)
        elif running and profile == READ_ONLY:
            self._set_pill("Running - read-only", OK)
        elif running:
            self._set_pill("Running", OK)
        else:
            self._set_pill("Stopped", "#6b7280")

        if running:
            self.address_label.setText(
                f"listening on {self.server.host}:{self.server.port}"
                + ("  -  token required" if self.server.require_token else ""))
        else:
            self.address_label.setText("not listening")

        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.port_spin.setEnabled(not running)
        self._refresh_metrics()

    def _set_pill(self, text, colour):
        self.status_pill.setText(text)
        self.status_pill.setStyleSheet(
            f"background:{colour}; color:#ffffff; border-radius:9px;"
            "padding:3px 10px; font-weight:600;")

    def _refresh_metrics(self):
        if not self.server:
            self.tile_requests.set_value(0)
            self.tile_clients.set_value(0)
            self.tile_uptime.set_value("-")
            self.tile_pending.set_value(0)
            self.tile_denied.set_value(0)
            self.tile_grants.set_value(0)
            return
        perms = self.server.permissions
        self.tile_requests.set_value(self.server.requests_served)
        self.tile_clients.set_value(self.server.client_count())
        seconds = self.server.uptime_seconds()
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        self.tile_uptime.set_value(f"{hours}:{minutes:02d}:{secs:02d}")
        pending = len(perms.pending)
        self.tile_pending.set_value(pending, WARN if pending else None)
        self.tile_denied.set_value(perms.denied_count,
                                   DANGER if perms.denied_count else None)
        grants = perms.active_grants()
        self.tile_grants.set_value(len(grants), WARN if grants else None)

    def _on_tick(self):
        self._refresh_metrics()
        for card in self._cards.values():
            card.tick()

    # ==================================================================
    # approvals
    # ==================================================================
    def add_approval(self, request):
        self.refresh_approvals()
        self.tabs.setCurrentIndex(1)
        if self.iface is not None:
            self.iface.messageBar().pushWarning(
                "QGIS MCP", f"'{request.command}' is waiting for your approval")

    def _on_card_answered(self, request_id, approved, remember):
        if not self.permissions:
            return
        request = self.permissions.pending.get(request_id)
        if remember and request is not None:
            self.permissions.grant(request.command, minutes=10)
        self.permissions.resolve(request_id, approved,
                                 "allowed for 10 minutes" if remember else "by user")

    def _resolve_all(self, approved):
        if self.permissions:
            self.permissions.resolve_all(approved, "bulk action")

    def _revoke_grants(self):
        if self.permissions:
            self.permissions.revoke_grants()
            self.log_row("--", "revoke_grants", "ok", "")

    def _clear_cards(self):
        for card in self._cards.values():
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

    def refresh_approvals(self):
        """Sync cards to the queue.

        Driven off the queue itself rather than only the server's signal, so a
        request parked by any path still surfaces here.
        """
        pending = self.permissions.pending if self.permissions else {}
        live = set(pending)
        for request_id in list(self._cards):
            if request_id not in live:
                card = self._cards.pop(request_id)
                card.setParent(None)
                card.deleteLater()
        for request_id, request in pending.items():
            if request_id not in self._cards:
                card = ApprovalCard(request)
                card.answered.connect(self._on_card_answered)
                self._cards[request_id] = card
                self.cards_layout.addWidget(card)
                card.tick()
        count = len(self._cards)
        self.approval_header.setText(
            "Nothing waiting" if not count
            else f"{count} request{'s' if count != 1 else ''} waiting for you")
        if self.permissions:
            grants = self.permissions.active_grants()
            self.grants_label.setText(
                "none" if not grants else
                ", ".join(f"{k} ({v}s)" for k, v in grants.items()))
        self._refresh_metrics()

    def _on_decision(self, command, verdict, reason):
        self.log_row("--", command, verdict, reason)

    # ==================================================================
    # activity
    # ==================================================================
    def log_row(self, when, command, result, extra=""):
        from qgis.PyQt.QtCore import QTime
        stamp = when if when != "--" else QTime.currentTime().toString("HH:mm:ss")
        row = self.log_table.rowCount()
        if row >= self.MAX_LOG_BLOCKS:
            self.log_table.removeRow(0)
            row -= 1
        self.log_table.insertRow(row)
        for col, text in enumerate((stamp, command, result, str(extra))):
            item = QTableWidgetItem(str(text))
            if col == 2 and result not in ("success", "ok", "approved"):
                item.setForeground(Qt.GlobalColor.red)
            self.log_table.setItem(row, col, item)
        self.log_table.scrollToBottom()
        self._apply_log_filter()

    def _apply_log_filter(self):
        needle = self.filter_box.text().strip().lower()
        failures = self.failures_only.isChecked()
        for row in range(self.log_table.rowCount()):
            command_item = self.log_table.item(row, 1)
            result_item = self.log_table.item(row, 2)
            command = command_item.text().lower() if command_item else ""
            result = result_item.text() if result_item else ""
            hide = (needle and needle not in command) or \
                   (failures and result in ("success", "ok", "approved"))
            self.log_table.setRowHidden(row, bool(hide))

    def _on_clients_changed(self, count):
        self._refresh_metrics()
        self.log_row("--", "connection", "ok", f"{count} client(s)")

    def _on_request_handled(self, command, ok):
        self.last_command.setText(f"last: {command}")
        self.log_row("--", command, "success" if ok else "error", "")
        self._refresh_metrics()

    def _export_audit(self):
        if not (self.server and self.server.audit):
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Export audit log", "qgis_mcp_audit.csv", "CSV (*.csv)")
        if not target:
            return
        written = self.server.audit.export_csv(target)
        if self.iface is not None:
            self.iface.messageBar().pushInfo(
                "QGIS MCP", f"Exported {written} audit rows")

    def _open_audit_file(self):
        if self.server and self.server.audit and \
                os.path.exists(self.server.audit.path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.server.audit.path))

    # ==================================================================
    # setup helpers
    # ==================================================================
    @staticmethod
    def _python_for_repo(repo):
        for relative in ((".venv", "Scripts", "python.exe"),
                         (".venv", "bin", "python"),
                         ("venv", "Scripts", "python.exe"),
                         ("venv", "bin", "python")):
            candidate = os.path.join(repo, *relative)
            if os.path.exists(candidate):
                return candidate
        return None

    def detect_paths(self):
        """Locate the MCP server script and its interpreter.

        The installed plugin lives in the QGIS profile, nowhere near the repo,
        so walking up from ``__file__`` finds nothing. Search the places a
        checkout actually lives instead, breadth-first and budgeted so this
        never turns into a full disk scan, and let a saved override win.
        """
        saved = self._settings.value(SETTINGS_SERVER_SCRIPT, "")
        if saved and os.path.exists(saved):
            repo = os.path.dirname(os.path.dirname(os.path.dirname(saved)))
            return self._python_for_repo(repo), saved

        home = os.path.expanduser("~")
        roots = [home]
        for name in ("Documents", "Desktop", "Downloads", "Projects", "src",
                     "repos", "git"):
            candidate = os.path.join(home, name)
            if os.path.isdir(candidate):
                roots.append(candidate)
        here = os.path.dirname(os.path.abspath(__file__))
        for _ in range(4):
            here = os.path.dirname(here)
            if here and os.path.isdir(here):
                roots.append(here)
        env_hint = os.environ.get("QGIS_MCP_REPO")
        if env_hint and os.path.isdir(env_hint):
            roots.insert(0, env_hint)
        # Checkouts often live on a second drive; scan each drive's top
        # levels last, still within the same budget.
        if os.name == "nt":
            for letter in "CDEFGH":
                drive = f"{letter}:\\"
                if os.path.isdir(drive) and drive.lower() != home[:3].lower():
                    roots.append(drive)

        budget = 3000
        seen = set()
        for root in roots:
            queue = [(root, 0)]
            while queue and budget > 0:
                directory, depth = queue.pop(0)
                real = os.path.normcase(os.path.abspath(directory))
                if real in seen or depth > 3:
                    continue
                seen.add(real)
                budget -= 1
                script = os.path.join(directory, "src", "qgis_mcp",
                                      "qgis_mcp_server.py")
                if os.path.exists(script):
                    return self._python_for_repo(directory), script
                try:
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            if entry.is_dir(follow_symlinks=False) \
                                    and not entry.name.startswith("."):
                                queue.append((entry.path, depth + 1))
                except OSError:
                    continue
        return None, None

    def browse_for_script(self):
        """Let the user point at qgis_mcp_server.py when detection comes up dry."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select qgis_mcp_server.py", os.path.expanduser("~"),
            "Python (*.py)")
        if path:
            self._settings.setValue(SETTINGS_SERVER_SCRIPT, path)
            self.build_config_snippet()
        return path

    def build_config_snippet(self):
        import json as _json
        python_path, script_path = self.detect_paths()
        entry = {
            "command": python_path or "<python not found - set this manually>",
            "args": [script_path or "<qgis_mcp_server.py not found>"],
            "env": {"QGIS_MCP_PORT": str(self.port_spin.value())},
        }
        if self.token_check.isChecked() and self.server:
            entry["env"]["QGIS_MCP_TOKEN"] = self.server.token
        snippet = _json.dumps({"mcpServers": {"qgis": entry}}, indent=2)
        self.config_preview.setPlainText(snippet)
        if python_path and script_path:
            self.log_row("--", "detect_paths", "ok", os.path.basename(script_path))
        else:
            self.log_row("--", "detect_paths", "partial",
                         "could not find venv or server script")
        return snippet

    def _copy_config(self):
        snippet = self.config_preview.toPlainText() or self.build_config_snippet()
        QApplication.clipboard().setText(snippet)
        if self.iface is not None:
            self.iface.messageBar().pushInfo("QGIS MCP", "Config copied")

    def _copy_token(self):
        if self.server:
            QApplication.clipboard().setText(self.server.token)
            if self.iface is not None:
                self.iface.messageBar().pushInfo("QGIS MCP", "Token copied")

    def _rotate_token(self):
        if not self.server:
            return
        answer = QMessageBox.question(
            self, "Rotate token",
            "Generate a new token? Connected clients will have to reconnect "
            "with the new value.")
        if answer == QMessageBox.StandardButton.Yes:
            self.token_field.setText(self.server.rotate_token())
            self.build_config_snippet()
            self.log_row("--", "rotate_token", "ok", "")

    # ==================================================================
    # settings changes
    # ==================================================================
    def _on_profile_changed(self):
        profile = self.selected_profile()
        self._settings.setValue(SETTINGS_PROFILE, profile)
        if self.permissions:
            self.permissions.set_profile(profile)
        self.log_row("--", "set_profile", "ok", PROFILES[profile]["label"])
        self.populate_commands()
        self.refresh()

    def _on_token_toggled(self):
        required = self.token_check.isChecked()
        self._settings.setValue(SETTINGS_REQUIRE_TOKEN, required)
        if self.server:
            self.server.require_token = required
        self.build_config_snippet()
        self.refresh()

    def _on_timeout_changed(self, value):
        self._settings.setValue(SETTINGS_APPROVAL_TIMEOUT, value)
        if self.permissions:
            self.permissions.approval_timeout = value

    def _on_audit_toggled(self):
        enabled = self.audit_check.isChecked()
        self._settings.setValue(SETTINGS_AUDIT, enabled)
        if self.server and self.server.audit:
            self.server.audit.enabled = enabled

    def closeEvent(self, event):
        # Deliberately does NOT stop the server: the panel is a control
        # surface, not the server's lifetime owner. unload() cleans up.
        self.closed.emit()
        super().closeEvent(event)
