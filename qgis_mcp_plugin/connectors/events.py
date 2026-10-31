# -*- coding: utf-8 -*-
"""NASA EONET v3 natural events: wildfires, storms, floods, volcanoes and more.

Keyless. Each event keeps a track of geometries over time; the feature is
placed at the most recent one.
"""

from .. import net

EONET = "https://eonet.gsfc.nasa.gov/api/v3/events"
CATEGORIES = {
    "drought": "Drought",
    "dustHaze": "Dust and Haze",
    "earthquakes": "Earthquakes",
    "floods": "Floods",
    "landslides": "Landslides",
    "manmade": "Manmade",
    "seaLakeIce": "Sea and Lake Ice",
    "severeStorms": "Severe Storms",
    "snow": "Snow",
    "tempExtremes": "Temperature Extremes",
    "volcanoes": "Volcanoes",
    "waterColor": "Water Color",
    "wildfires": "Wildfires",
}
STATUSES = ("open", "closed", "all")


def _centroid(ring):
    pts = [p for p in ring if len(p) >= 2]
    if not pts:
        return None
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _position(geometry):
    """Return (lon, lat) for one EONET geometry entry, or None."""
    coords = geometry.get("coordinates")
    try:
        if geometry.get("type") == "Polygon":
            return _centroid(coords[0])
        return float(coords[0]), float(coords[1])
    except (TypeError, IndexError, ValueError):
        return None


def events(bbox=None, categories=None, status="open", days=30, limit=200):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    params = {"status": status, "days": max(1, min(int(days), 365)),
              "limit": max(1, min(int(limit), 1000))}
    if categories:
        if isinstance(categories, str):
            categories = [categories]
        bad = [c for c in categories if c not in CATEGORIES]
        if bad:
            raise ValueError(f"Unknown EONET categories {bad}; valid ids are "
                             f"{', '.join(CATEGORIES)}")
        params["category"] = list(categories)
    if bbox:
        # EONET wants upper left then lower right: minLon,maxLat,maxLon,minLat
        params["bbox"] = (f"{float(bbox[0])},{float(bbox[3])},"
                          f"{float(bbox[2])},{float(bbox[1])}")
    data = net.get_json(EONET, params, timeout_ms=90_000)
    features = []
    by_category = {}
    for event in data.get("events") or []:
        geoms = event.get("geometry") or []
        pos = _position(geoms[-1]) if geoms else None
        if pos is None:
            continue
        last = geoms[-1]
        cats = event.get("categories") or []
        category = cats[0].get("title") if cats else None
        label = category or "unknown"
        by_category[label] = by_category.get(label, 0) + 1
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [pos[0], pos[1]]},
            "properties": {
                "id": event.get("id"), "title": event.get("title"),
                "category": category, "link": event.get("link"),
                "closed": bool(event.get("closed")),
                "track_points": len(geoms),
                "first_date": geoms[0].get("date"),
                "last_date": last.get("date"),
                "magnitude_value": last.get("magnitudeValue"),
                "magnitude_unit": last.get("magnitudeUnit"),
                "sources": ",".join(str(s.get("id")) for s in event.get("sources") or []
                                    if s.get("id")),
            },
        })
    return {"features": features, "by_category": by_category,
            "source": "NASA EONET"}
