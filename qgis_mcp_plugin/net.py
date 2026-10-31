# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""HTTP helpers for the live-data connectors.

Requests go through QGIS's own network manager so the user's proxy and
certificate settings apply, and so the panel's network diagnostics see them.
Everything funnels through :func:`fetch`, which the headless tests replace
with a stub so connector logic can be exercised without the internet.
"""

import json
import re
import urllib.parse

from qgis.core import QgsBlockingNetworkRequest, QgsNetworkAccessManager
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

USER_AGENT = "qgis-mcp/4.1 (+https://github.com/adilmunawar/qgis_mcp)"
DEFAULT_TIMEOUT_MS = 60_000


_SECRET_PATH = re.compile(r"(/api/area/csv/)[^/]+")
_SECRET_QUERY = re.compile(r"([?&](?:key|token|api_key|apikey|map_key|sig)=)[^&]+",
                           re.IGNORECASE)


def redact(url):
    """Hide API keys that travel in a path segment or query string."""
    url = _SECRET_PATH.sub(r"\1***", str(url))
    return _SECRET_QUERY.sub(r"\1***", url)


class HttpError(RuntimeError):
    """A request that did not complete with a 2xx status.

    The message carries the start of the response body, because services
    like Open-Meteo and Overpass explain a 400 there, and the URL is
    redacted so a key never reaches a log or a model.
    """

    def __init__(self, url, status, message, body=""):
        self.url = redact(url)
        self.status = status
        self.body = body or ""
        detail = f"HTTP {status or 'error'} for {self.url}: {message}"
        snippet = self.body.strip().replace("\n", " ")[:300]
        if snippet:
            detail += f" | {snippet}"
        super().__init__(detail)


def build_url(base, params=None):
    if not params:
        return base
    clean = {k: v for k, v in params.items() if v is not None}
    for key, value in list(clean.items()):
        if isinstance(value, bool):
            clean[key] = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            clean[key] = ",".join(str(v) for v in value)
    query = urllib.parse.urlencode(clean, safe=",:/")
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}{query}"


def fetch(url, params=None, headers=None, method="GET", body=None,
          timeout_ms=DEFAULT_TIMEOUT_MS):
    """Perform a request and return ``(status, bytes)``.

    Raises :class:`HttpError` for transport failures and non-2xx replies.
    """
    full = build_url(url, params)
    request = QNetworkRequest(QUrl(full))
    request.setRawHeader(b"User-Agent", USER_AGENT.encode("ascii"))
    request.setRawHeader(b"Accept", b"application/json, */*;q=0.5")
    for key, value in (headers or {}).items():
        request.setRawHeader(str(key).encode("utf-8"), str(value).encode("utf-8"))

    manager = QgsNetworkAccessManager.instance()
    previous_timeout = manager.timeout()
    manager.setTimeout(int(timeout_ms))
    blocking = QgsBlockingNetworkRequest()
    try:
        if method.upper() == "POST":
            payload = body if isinstance(body, (bytes, bytearray)) else \
                (body or "").encode("utf-8")
            if not any(str(k).lower() == "content-type" for k in (headers or {})):
                request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader,
                                  "application/x-www-form-urlencoded")
            code = blocking.post(request, payload)
        else:
            code = blocking.get(request)
    finally:
        manager.setTimeout(previous_timeout)

    reply = blocking.reply()
    status_attr = reply.attribute(
        QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    status = int(status_attr) if status_attr is not None else None
    content = bytes(reply.content().data()) if reply is not None else b""
    if code != QgsBlockingNetworkRequest.ErrorCode.NoError:
        raise HttpError(full, status, blocking.errorMessage() or str(code),
                        content[:500].decode("utf-8", "replace"))
    if status is not None and not 200 <= status < 300:
        raise HttpError(full, status, "non-success status",
                        content[:500].decode("utf-8", "replace"))
    return status, content


def get_json(url, params=None, headers=None, timeout_ms=DEFAULT_TIMEOUT_MS):
    _, content = fetch(url, params, headers, timeout_ms=timeout_ms)
    try:
        return json.loads(content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HttpError(build_url(url, params), None,
                        f"response was not JSON ({exc})",
                        content[:500].decode("utf-8", "replace"))


def post_json(url, body, headers=None, timeout_ms=DEFAULT_TIMEOUT_MS):
    merged = {"Content-Type": "application/json"}
    merged.update(headers or {})
    _, content = fetch(url, None, merged, method="POST",
                       body=json.dumps(body), timeout_ms=timeout_ms)
    try:
        return json.loads(content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HttpError(url, None, f"response was not JSON ({exc})",
                        content[:500].decode("utf-8", "replace"))


def post_form(url, data, headers=None, timeout_ms=DEFAULT_TIMEOUT_MS):
    _, content = fetch(url, None, headers, method="POST",
                       body=urllib.parse.urlencode(data), timeout_ms=timeout_ms)
    try:
        return json.loads(content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HttpError(url, None, f"response was not JSON ({exc})",
                        content[:500].decode("utf-8", "replace"))
