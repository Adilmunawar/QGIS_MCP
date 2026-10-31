# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""Append-only audit trail.

The panel's activity view is a widget, and widgets die with the window. If an
agent ran unattended, the only way to answer "what did it change?" afterwards
is a record on disk, so every dispatched command lands here as one JSON line.

JSONL rather than JSON: an append is a single write with no read-modify-parse
cycle, and a truncated final line costs one record instead of the whole file.
"""

import json
import os
import time

MAX_PARAM_CHARS = 400
SECRET_KEYS = {"key", "token", "password", "secret", "api_key", "apikey"}


def _redact(value):
    if isinstance(value, dict):
        return {k: ("***" if str(k).lower() in SECRET_KEYS else _redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


class AuditLog:
    def __init__(self, path, enabled=True, max_bytes=8 * 1024 * 1024):
        self.path = path
        self.enabled = enabled
        self.max_bytes = max_bytes
        self._failed_once = False

    # -- writing -------------------------------------------------------
    def record(self, command, verdict, client=None, status=None, detail=None,
               params=None, duration_ms=None):
        if not self.enabled:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "command": command,
            "verdict": verdict,
            "client": client or "unknown",
            "status": status,
        }
        if params:
            text = json.dumps(_redact(params), default=str)
            entry["params"] = (text[:MAX_PARAM_CHARS] + "...(truncated)"
                               if len(text) > MAX_PARAM_CHARS else text)
        if detail:
            entry["detail"] = str(detail)[:400]
        if duration_ms is not None:
            entry["duration_ms"] = round(duration_ms, 1)
        self._append(entry)

    def _append(self, entry):
        try:
            self._rotate_if_needed()
            directory = os.path.dirname(self.path)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            # Auditing must never take the server down with it; report once.
            if not self._failed_once:
                self._failed_once = True

    def _rotate_if_needed(self):
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > self.max_bytes:
                backup = self.path + ".1"
                if os.path.exists(backup):
                    os.remove(backup)
                os.replace(self.path, backup)
        except OSError:
            pass

    # -- reading -------------------------------------------------------
    def tail(self, limit=100, command=None, only_failures=False):
        if not os.path.exists(self.path):
            return []
        rows = []
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if command and entry.get("command") != command:
                        continue
                    if only_failures and entry.get("status") == "success":
                        continue
                    rows.append(entry)
        except OSError:
            return []
        return rows[-limit:]

    def export_csv(self, target):
        import csv
        rows = self.tail(limit=1_000_000)
        fields = ["ts", "client", "command", "verdict", "status", "duration_ms",
                  "params", "detail"]
        with open(target, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return len(rows)

    def clear(self):
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
            return True
        except OSError:
            return False
