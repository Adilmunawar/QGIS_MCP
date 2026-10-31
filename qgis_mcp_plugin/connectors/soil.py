# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""SoilGrids (ISRIC) point queries: texture, pH, organic carbon and more."""

from .. import net

ENDPOINT = "https://rest.isric.org/soilgrids/v2.0/properties/query"

PROPERTIES = {
    "clay": "Clay content", "sand": "Sand content", "silt": "Silt content",
    "phh2o": "pH in water", "soc": "Soil organic carbon", "nitrogen": "Total nitrogen",
    "cec": "Cation exchange capacity", "bdod": "Bulk density", "cfvo": "Coarse fragments",
    "ocd": "Organic carbon density", "wv0010": "Water content at 10 kPa",
    "wv0033": "Water content at 33 kPa (field capacity)",
    "wv1500": "Water content at 1500 kPa (wilting point)",
}
DEFAULT_PROPERTIES = ["clay", "sand", "silt", "phh2o", "soc", "nitrogen", "cec", "bdod"]
DEPTHS = ["0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm"]


def query(lon, lat, properties=None, depths=None, value="mean"):
    props = properties or DEFAULT_PROPERTIES
    unknown = [p for p in props if p not in PROPERTIES]
    if unknown:
        raise ValueError(f"Unknown soil properties {unknown}. Use {sorted(PROPERTIES)}")
    depths = depths or ["0-5cm", "5-15cm", "15-30cm"]
    bad = [d for d in depths if d not in DEPTHS]
    if bad:
        raise ValueError(f"Unknown depths {bad}. Use {DEPTHS}")
    params = [("lon", f"{float(lon):.5f}"), ("lat", f"{float(lat):.5f}"), ("value", value)]
    params += [("property", p) for p in props] + [("depth", d) for d in depths]
    url = ENDPOINT + "?" + "&".join(f"{k}={v}" for k, v in params)
    data = net.get_json(url, timeout_ms=90_000)
    rows = []
    for layer in (data.get("properties") or {}).get("layers", []):
        unit = layer.get("unit_measure") or {}
        factor = unit.get("d_factor") or 1
        for depth in layer.get("depths", []):
            raw = (depth.get("values") or {}).get(value)
            rows.append({
                "property": layer["name"],
                "label": PROPERTIES.get(layer["name"], layer["name"]),
                "depth": depth.get("label"),
                "value": None if raw is None else round(raw / factor, 3),
                "unit": unit.get("target_units") or unit.get("mapped_units"),
            })
    return {"lon": lon, "lat": lat, "rows": rows,
            "texture_class": texture_class(rows),
            "source": "SoilGrids 2.0, ISRIC (CC BY 4.0)"}


def query_many(points, properties=None, depth="0-5cm", spacing_s=2.0, retries=2):
    """Query several points politely: paced, with retries on 429, partial on failure.

    Returns ``(values, skipped)`` where ``values`` has one dict per point in
    order, and ``skipped`` counts points that failed after the retries.
    """
    import time
    values, skipped = [], 0
    for i, (lon, lat) in enumerate(points):
        if i:
            time.sleep(spacing_s)
        attempt = 0
        while True:
            try:
                result = query(lon, lat, properties, [depth])
                row = {r["property"]: r["value"] for r in result["rows"]}
                row["texture"] = result["texture_class"]
                values.append(row)
                break
            except net.HttpError as exc:
                attempt += 1
                if exc.status == 429 and attempt <= retries:
                    time.sleep(10 * attempt)
                    continue
                skipped += 1
                values.append({})
                break
    return values, skipped


def texture_class(rows):
    """USDA texture triangle class from topsoil clay/sand/silt, when present."""
    top = {r["property"]: r["value"] for r in rows if r["depth"] == "0-5cm"}
    clay, sand, silt = top.get("clay"), top.get("sand"), top.get("silt")
    if None in (clay, sand, silt):
        return None
    if clay >= 40:
        if sand >= 45:
            return "sandy clay"
        return "silty clay" if silt >= 40 else "clay"
    if clay >= 27:
        if sand >= 45:
            return "sandy clay loam"
        return "silty clay loam" if silt >= 40 else "clay loam"
    if silt >= 80:
        return "silt"
    if silt >= 50:
        return "silt loam"
    if sand >= 85:
        return "sand"
    if sand >= 70:
        return "loamy sand"
    if clay >= 20 and sand >= 52:
        return "sandy clay loam"
    if sand >= 52:
        return "sandy loam"
    return "loam"
