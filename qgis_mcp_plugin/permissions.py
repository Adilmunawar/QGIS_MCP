# -*- coding: utf-8 -*-
"""Permission profiles, temporary grants and the approval queue.

Every command carries a risk category. A profile maps each category to one of
three verdicts - allow outright, require a human approval, or refuse - and the
dispatcher consults that map before a handler ever runs.

Approvals are deliberately *non-blocking*. A modal dialog would freeze the GUI
thread that the server itself runs on, so a pending command parks its reply
instead: the socket stays open, the panel shows the request, and the response
is written only once a person answers or the request times out.
"""

import time
import uuid

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal

# --- risk categories --------------------------------------------------
READ = "read"
WRITE = "write"
DESTRUCTIVE = "destructive"
CODE = "code"

# --- verdicts ---------------------------------------------------------
ALLOW = "allow"
APPROVE = "approve"
DENY = "deny"

# --- profiles ---------------------------------------------------------
READ_ONLY = "read_only"
STANDARD = "standard"
FULL = "full"
MASTER = "master"

PROFILES = {
    READ_ONLY: {
        "label": "Read-only",
        "blurb": "Inspect only. Every command that changes anything is refused.",
        "rules": {READ: ALLOW, WRITE: DENY, DESTRUCTIVE: DENY, CODE: DENY},
    },
    STANDARD: {
        "label": "Standard",
        "blurb": "Edits allowed. Deletions and overwrites ask first. No code execution.",
        "rules": {READ: ALLOW, WRITE: ALLOW, DESTRUCTIVE: APPROVE, CODE: DENY},
    },
    FULL: {
        "label": "Full",
        "blurb": "Everything available, but destructive actions and code ask first.",
        "rules": {READ: ALLOW, WRITE: ALLOW, DESTRUCTIVE: APPROVE, CODE: APPROVE},
    },
    MASTER: {
        "label": "Master Control",
        "blurb": "Unrestricted. Nothing is refused and nothing asks for approval.",
        "rules": {READ: ALLOW, WRITE: ALLOW, DESTRUCTIVE: ALLOW, CODE: ALLOW},
    },
}

PROFILE_ORDER = [READ_ONLY, STANDARD, FULL, MASTER]

#: Command name -> risk category. Anything absent is treated as DESTRUCTIVE,
#: so a newly added command fails closed rather than open.
COMMAND_CATEGORIES = {
    # read
    "ping": READ,
    "get_qgis_info": READ,
    "get_server_info": READ,
    "get_project_info": READ,
    "get_layers": READ,
    "get_layer_fields": READ,
    "get_layer_extent": READ,
    "get_layer_features": READ,
    "get_layer_filter": READ,
    "get_selected_features": READ,
    "count_features": READ,
    "get_unique_values": READ,
    "get_field_statistics": READ,
    "get_canvas_state": READ,
    "list_layouts": READ,
    "list_processing_algorithms": READ,
    "get_processing_algorithm_help": READ,
    "render_map_base64": READ,
    "get_audit_log": READ,
    "execute_sql": READ,
    "group_statistics": READ,
    "check_geometry_validity": READ,
    "find_duplicates": READ,
    "detect_layer_crs": READ,
    "get_raster_statistics": READ,
    "sample_raster_values": READ,
    "list_data_sources": READ,
    "resolve_aoi": READ,
    "geocode": READ,
    "reverse_geocode": READ,
    "search_imagery": READ,
    "get_weather": READ,
    "get_weather_history": READ,
    "get_climate_series": READ,
    "get_index_time_series": READ,
    "list_basemaps": READ,
    # write - changes the session or the view, reversible in practice
    "add_vector_layer": WRITE,
    "add_raster_layer": WRITE,
    "create_memory_layer": WRITE,
    "set_layer_visibility": WRITE,
    "set_layer_opacity": WRITE,
    "set_layer_crs": WRITE,
    "set_layer_filter": WRITE,
    "clear_layer_filter": WRITE,
    "set_layer_style": WRITE,
    "save_layer_style": WRITE,
    "select_features_by_expression": WRITE,
    "clear_selection": WRITE,
    "zoom_to_layer": WRITE,
    "zoom_to_selection": WRITE,
    "set_canvas_extent": WRITE,
    "set_project_crs": WRITE,
    "reload_layer": WRITE,
    "add_features": WRITE,
    "render_map": WRITE,
    "export_layer": WRITE,
    "export_layout": WRITE,
    "add_field": WRITE,
    "rename_field": WRITE,
    "create_spatial_index": WRITE,
    "add_place_marker": WRITE,
    "add_imagery": WRITE,
    "compute_spectral_index": WRITE,
    "add_dem": WRITE,
    "derive_terrain": WRITE,
    "attach_weather_to_layer": WRITE,
    "add_basemap": WRITE,
    "fetch_osm_features": WRITE,
    "fetch_earthquakes": WRITE,
    "fetch_active_fires": WRITE,
    "set_data_source_key": WRITE,
    # destructive - discards data, or overwrites something on disk
    "delete_features": DESTRUCTIVE,
    "remove_layer": DESTRUCTIVE,
    "save_project": DESTRUCTIVE,
    "create_new_project": DESTRUCTIVE,
    "load_project": DESTRUCTIVE,
    "execute_processing": DESTRUCTIVE,
    "update_features": DESTRUCTIVE,
    "delete_field": DESTRUCTIVE,
    # code
    "execute_code": CODE,
}

#: Commands that never need approval regardless of profile, because refusing
#: them would leave a client unable to discover why it was refused.
ALWAYS_ALLOWED = {"ping", "get_server_info"}


def category_for(command):
    return COMMAND_CATEGORIES.get(command, DESTRUCTIVE)


class ApprovalRequest:
    """One parked command awaiting a human answer."""

    def __init__(self, command, params, category, client, on_resolved, timeout_s):
        self.id = uuid.uuid4().hex[:12]
        self.command = command
        self.params = params or {}
        self.category = category
        self.client = client
        self.on_resolved = on_resolved
        self.timeout_s = timeout_s
        self.created = time.time()
        self.resolved = False

    def summary(self):
        """A short human-readable description of what is being asked."""
        bits = []
        for key in ("layer_id", "path", "expression", "algorithm", "layout_name"):
            value = self.params.get(key)
            if value:
                text = str(value)
                bits.append(f"{key}={text[:60]}")
        return ", ".join(bits) if bits else "no parameters"

    def seconds_left(self):
        return max(0, int(self.timeout_s - (time.time() - self.created)))


class PermissionManager(QObject):
    """Decides whether a command may run, and owns the approval queue."""

    profile_changed = pyqtSignal(str)
    queue_changed = pyqtSignal()
    decision_made = pyqtSignal(str, str, str)  # command, verdict, reason

    def __init__(self, profile=STANDARD, approval_timeout=60, parent=None):
        super().__init__(parent)
        self._profile = profile if profile in PROFILES else STANDARD
        self.approval_timeout = approval_timeout
        self.pending = {}
        self._grants = {}       # command -> expiry timestamp
        self.denied_count = 0
        self.approved_count = 0

    # -- profile -------------------------------------------------------
    @property
    def profile(self):
        return self._profile

    def set_profile(self, profile):
        if profile not in PROFILES:
            raise ValueError(f"Unknown profile: {profile}")
        if profile != self._profile:
            self._profile = profile
            self.profile_changed.emit(profile)

    def profile_label(self):
        return PROFILES[self._profile]["label"]

    def is_master(self):
        return self._profile == MASTER

    # -- temporary grants ----------------------------------------------
    def grant(self, command, minutes=10):
        self._grants[command] = time.time() + minutes * 60
        self.queue_changed.emit()

    def revoke_grants(self):
        self._grants.clear()
        self.queue_changed.emit()

    def active_grants(self):
        now = time.time()
        self._grants = {k: v for k, v in self._grants.items() if v > now}
        return {k: int(v - now) for k, v in self._grants.items()}

    def has_grant(self, command):
        return command in self.active_grants()

    # -- evaluation ----------------------------------------------------
    def evaluate(self, command):
        """Return (verdict, reason) for a command under the current profile."""
        if command in ALWAYS_ALLOWED:
            return ALLOW, "always allowed"
        category = category_for(command)
        verdict = PROFILES[self._profile]["rules"][category]
        if verdict != ALLOW and self.has_grant(command):
            return ALLOW, "temporary grant"
        if verdict == DENY:
            return DENY, (
                f"'{command}' is a {category} command and the current profile is "
                f"'{self.profile_label()}'. Raise the profile in the QGIS MCP "
                f"panel to permit it."
            )
        return verdict, f"{category} under {self.profile_label()}"

    # -- approval queue ------------------------------------------------
    def request_approval(self, command, params, client, on_resolved):
        request = ApprovalRequest(
            command, params, category_for(command), client, on_resolved,
            self.approval_timeout)
        self.pending[request.id] = request
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda rid=request.id: self.resolve(rid, False, "timed out"))
        timer.start(int(self.approval_timeout * 1000))
        request._timer = timer
        self.queue_changed.emit()
        return request

    def resolve(self, request_id, approved, reason=""):
        request = self.pending.pop(request_id, None)
        if request is None or request.resolved:
            return False
        request.resolved = True
        timer = getattr(request, "_timer", None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        if approved:
            self.approved_count += 1
        else:
            self.denied_count += 1
        self.queue_changed.emit()
        self.decision_made.emit(request.command,
                                "approved" if approved else "denied", reason)
        request.on_resolved(approved, reason)
        return True

    def resolve_all(self, approved, reason="bulk action"):
        for request_id in list(self.pending):
            self.resolve(request_id, approved, reason)

    def snapshot(self):
        return {
            "profile": self._profile,
            "profile_label": self.profile_label(),
            "master_control": self.is_master(),
            "approval_timeout": self.approval_timeout,
            "pending": len(self.pending),
            "approved_count": self.approved_count,
            "denied_count": self.denied_count,
            "grants": self.active_grants(),
        }
