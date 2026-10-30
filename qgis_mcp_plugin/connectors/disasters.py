# -*- coding: utf-8 -*-
"""GDACS global disaster alerts (earthquakes, cyclones, floods, drought,
volcanoes, wildfires) from the public RSS feed."""

import xml.etree.ElementTree as ET

from .. import net

RSS = "https://www.gdacs.org/xml/rss.xml"
NS = {
    "gdacs": "http://www.gdacs.org",
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
    "georss": "http://www.georss.org/georss",
    "dc": "http://purl.org/dc/elements/1.1/",
}
EVENT_TYPES = {"EQ": "earthquake", "TC": "tropical cyclone", "FL": "flood",
               "DR": "drought", "VO": "volcano", "WF": "wildfire", "TS": "tsunami"}


def _text(item, path):
    node = item.find(path, NS)
    return node.text.strip() if node is not None and node.text else None


def alerts(bbox=None, event_types=None, alert_levels=None, only_current=True):
    _, content = net.fetch(RSS, timeout_ms=90_000)
    if len(content) > 20_000_000 or b"<!DOCTYPE" in content[:2000] or b"<!ENTITY" in content:
        raise ValueError("GDACS feed looks malformed; refusing to parse it")
    root = ET.fromstring(content)
    wanted_types = {t.upper() for t in event_types} if event_types else None
    wanted_levels = {l.lower() for l in alert_levels} if alert_levels else None
    features = []
    for item in root.iter("item"):
        etype = _text(item, "gdacs:eventtype") or (_text(item, "dc:subject") or "")[:2]
        level = (_text(item, "gdacs:alertlevel") or "").lower()
        if not level:
            title = (_text(item, "title") or "").lower()
            level = next((l for l in ("red", "orange", "green") if title.startswith(l)), "")
        if wanted_types and etype not in wanted_types:
            continue
        if wanted_levels and level not in wanted_levels:
            continue
        if only_current and (_text(item, "gdacs:iscurrent") or "true").lower() != "true":
            continue
        lat, lon = _text(item, "geo:Point/geo:lat"), _text(item, "geo:Point/geo:long")
        if lat is None or lon is None:
            point = _text(item, "georss:point")
            if not point:
                continue
            lat, lon = point.split()
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        if bbox and not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "event_type": EVENT_TYPES.get(etype, etype), "code": etype,
                "alert_level": level, "title": _text(item, "title"),
                "description": (_text(item, "description") or "")[:400],
                "country": _text(item, "gdacs:country"),
                "from": _text(item, "gdacs:fromdate"), "to": _text(item, "gdacs:todate"),
                "severity": _text(item, "gdacs:severity"),
                "population": _text(item, "gdacs:population"),
                "link": _text(item, "link"), "event_id": _text(item, "gdacs:eventid"),
            },
        })
    by_level = {}
    for f in features:
        by_level[f["properties"]["alert_level"] or "unknown"] = \
            by_level.get(f["properties"]["alert_level"] or "unknown", 0) + 1
    return {"features": features, "by_level": by_level, "source": "GDACS (EC JRC / UN OCHA)"}
