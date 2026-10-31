# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""Shared bootstrap for the headless suites.

Imports the plugin from this checkout, not from the QGIS profile, so a test
run exercises the code being edited. Run every suite with QGIS's interpreter::

    "C:/Program Files/QGIS 4.2.2/bin/python-qgis.bat" tests/<suite>.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt" and os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from qgis.core import QgsApplication  # noqa: E402

_app = None


def start_qgis():
    global _app
    if _app is None:
        _app = QgsApplication([], True)
        QgsApplication.initQgis()
    return _app


class Checker:
    def __init__(self):
        self.passed = []
        self.failed = []

    def __call__(self, name, condition, detail=""):
        (self.passed if condition else self.failed).append(name)
        print(f"{'PASS' if condition else 'FAIL'}  {name}"
              + (f"  -- {detail}" if detail and not condition else ""))
        return condition

    def report(self):
        total = len(self.passed) + len(self.failed)
        print(f"\n{len(self.passed)}/{total} checks passed")
        if self.failed:
            print("FAILED:", ", ".join(self.failed))
        return 0 if not self.failed else 1
