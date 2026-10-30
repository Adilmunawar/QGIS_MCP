# -*- coding: utf-8 -*-
"""Live-data connectors.

A connector is a thin adapter from one public data service to the shapes the
rest of the plugin understands: WGS84 bounding boxes in, layers or plain JSON
out. The registry lists what exists so an agent can discover sources by
category instead of memorising them.
"""

from .registry import CATEGORIES, REGISTRY, describe_sources, source  # noqa: F401
