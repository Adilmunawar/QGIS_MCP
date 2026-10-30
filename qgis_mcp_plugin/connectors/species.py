# -*- coding: utf-8 -*-
"""Species occurrence records from GBIF (CC0 / CC BY per dataset)."""

from .. import net

SEARCH = "https://api.gbif.org/v1/occurrence/search"
MATCH = "https://api.gbif.org/v1/species/match"
PAGE = 300
MAX_RECORDS = 3000


def match_taxon(name):
    data = net.get_json(MATCH, {"name": name})
    if data.get("matchType") in (None, "NONE"):
        raise LookupError(f"GBIF could not match the name '{name}'")
    if data.get("matchType") == "HIGHERRANK":
        raise LookupError(
            f"GBIF only matched '{name}' to the higher rank {data.get('scientificName')} "
            f"({data.get('rank')}); check the spelling or use that name explicitly")
    return {"key": data.get("usageKey"), "scientific_name": data.get("scientificName"),
            "rank": data.get("rank"), "kingdom": data.get("kingdom"),
            "confidence": data.get("confidence"), "match_type": data.get("matchType")}


def occurrences(bbox, taxon=None, limit=1000, year_from=None, year_to=None,
                has_coordinate=True):
    west, south, east, north = bbox
    limit = max(1, min(int(limit), MAX_RECORDS))
    params = {
        "decimalLatitude": f"{south},{north}",
        "decimalLongitude": f"{west},{east}",
        "hasCoordinate": "true" if has_coordinate else "false",
        "hasGeospatialIssue": "false",
        "limit": PAGE,
    }
    taxon_info = None
    if taxon:
        taxon_info = match_taxon(taxon)
        params["taxonKey"] = taxon_info["key"]
    if year_from or year_to:
        params["year"] = f"{year_from or '*'},{year_to or '*'}"
    features = []
    offset = 0
    total = None
    while len(features) < limit:
        params["offset"] = offset
        params["limit"] = min(PAGE, limit - len(features))
        data = net.get_json(SEARCH, params, timeout_ms=90_000)
        total = data.get("count")
        for r in data.get("results", []):
            lon, lat = r.get("decimalLongitude"), r.get("decimalLatitude")
            if lon is None or lat is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "species": r.get("species"), "scientific_name": r.get("scientificName"),
                    "kingdom": r.get("kingdom"), "class": r.get("class"),
                    "family": r.get("family"), "year": r.get("year"),
                    "date": (r.get("eventDate") or "")[:10] or None,
                    "basis": r.get("basisOfRecord"), "dataset": r.get("datasetName"),
                    "recorded_by": r.get("recordedBy"), "gbif_id": r.get("key"),
                    "country": r.get("country"),
                },
            })
        if data.get("endOfRecords") or not data.get("results"):
            break
        offset += PAGE
    return {"taxon": taxon_info, "matched": total, "features": features}


def species_counts(features, top=20):
    counts = {}
    for f in features:
        name = f["properties"].get("species") or f["properties"].get("scientific_name") or "unknown"
        counts[name] = counts.get(name, 0) + 1
    rows = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [{"species": k, "records": v} for k, v in rows[:top]]
