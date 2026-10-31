# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: LicenseRef-QGIS-MCP-Source-Available-1.0
"""STAC catalogue search and cloud-optimised GeoTIFF access.

Earth Search is the default because its Sentinel-2, Landsat and Copernicus
DEM assets are public COGs on AWS that GDAL streams through ``/vsicurl/``
without credentials. Planetary Computer is the fallback for collections
Earth Search lacks; its assets need a signed URL, obtained on demand.
"""

import urllib.parse

from .. import net

EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"
PLANETARY_COMPUTER = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"

CATALOGUES = {
    "earth-search": EARTH_SEARCH,
    "planetary-computer": PLANETARY_COMPUTER,
}

#: Collection aliases an agent is likely to use, mapped per catalogue.
COLLECTIONS = {
    "sentinel-2-c1-l2a": {
        "earth-search": "sentinel-2-c1-l2a",
        "planetary-computer": "sentinel-2-l2a",
        "description": "Sentinel-2 Collection 1 reprocessed L2A (newer baseline)",
        "cloud_property": "eo:cloud_cover",
        "bands": {
            "blue": "blue", "green": "green", "red": "red", "nir": "nir",
            "nir08": "nir08", "rededge1": "rededge1", "rededge2": "rededge2",
            "rededge3": "rededge3", "swir16": "swir16", "swir22": "swir22",
            "scl": "scl", "visual": "visual",
        },
        "display": "visual",
    },
    "sentinel-2-l2a": {
        "earth-search": "sentinel-2-l2a",
        "planetary-computer": "sentinel-2-l2a",
        "description": "Sentinel-2 surface reflectance, 10-60 m, 5-day revisit",
        "cloud_property": "eo:cloud_cover",
        "bands": {
            "blue": "blue", "green": "green", "red": "red", "nir": "nir",
            "nir08": "nir08", "rededge1": "rededge1", "rededge2": "rededge2",
            "rededge3": "rededge3", "swir16": "swir16", "swir22": "swir22",
            "scl": "scl", "visual": "visual",
        },
        "display": "visual",
    },
    "sentinel-1-grd": {
        "earth-search": "sentinel-1-grd",
        "planetary-computer": "sentinel-1-grd",
        "description": "Sentinel-1 C-band SAR, VV/VH, all-weather",
        "cloud_property": None,
        "bands": {"vv": "vv", "vh": "vh"},
        "display": "vv",
    },
    "landsat-c2-l2": {
        "earth-search": "landsat-c2-l2",
        "planetary-computer": "landsat-c2-l2",
        "description": "Landsat 8/9 surface reflectance and temperature, 30 m",
        "cloud_property": "eo:cloud_cover",
        "bands": {
            "blue": "blue", "green": "green", "red": "red", "nir": "nir08",
            "swir16": "swir16", "swir22": "swir22", "lwir11": "lwir11",
            "qa_pixel": "qa_pixel",
        },
        "display": "red",
    },
    "cop-dem-glo-30": {
        "earth-search": "cop-dem-glo-30",
        "planetary-computer": "cop-dem-glo-30",
        "description": "Copernicus DEM 30 m global elevation",
        "cloud_property": None,
        "bands": {"data": "data"},
        "display": "data",
    },
}


def catalogue_url(catalogue):
    try:
        return CATALOGUES[catalogue]
    except KeyError:
        raise ValueError(
            f"Unknown catalogue '{catalogue}'. Use one of: {sorted(CATALOGUES)}")


def collection_id(alias, catalogue):
    spec = COLLECTIONS.get(alias)
    if spec is None:
        # Allow raw collection ids the registry may know but we do not.
        return alias, None
    return spec[catalogue], spec


def search(aoi_bbox, collection="sentinel-2-l2a", start=None, end=None,
           max_cloud=None, limit=10, catalogue="earth-search",
           intersects=None, keep_assets=False):
    """Return STAC items as compact dicts, least cloudy first when relevant."""
    base = catalogue_url(catalogue)
    cid, spec = collection_id(collection, catalogue)
    body = {"collections": [cid], "limit": int(limit)}
    if intersects is not None:
        body["intersects"] = intersects
    else:
        body["bbox"] = [float(v) for v in aoi_bbox]
    if start or end:
        body["datetime"] = f"{start or '..'}T00:00:00Z/{end or '..'}T23:59:59Z" \
            if start and end else \
            (f"{start}T00:00:00Z/.." if start else f"../{end}T23:59:59Z")
    cloud_prop = spec["cloud_property"] if spec else "eo:cloud_cover"
    if max_cloud is not None and cloud_prop:
        body["query"] = {cloud_prop: {"lt": float(max_cloud)}}
        body["sortby"] = [{"field": f"properties.{cloud_prop}", "direction": "asc"}]
    else:
        body["sortby"] = [{"field": "properties.datetime", "direction": "desc"}]
    response = net.post_json(f"{base}/search", body)
    items = [summarise_item(f, catalogue, keep_assets) for f in response.get("features", [])]
    return {
        "catalogue": catalogue,
        "collection": cid,
        "count": len(items),
        "matched": (response.get("context") or {}).get("matched")
        or response.get("numberMatched"),
        "items": items,
    }


def summarise_item(feature, catalogue="earth-search", keep_assets=False):
    props = feature.get("properties", {})
    assets = feature.get("assets", {})
    bbox = feature.get("bbox")
    extra = {"raw_assets": assets} if keep_assets else {}
    return {**extra,
        "id": feature.get("id"),
        "collection": feature.get("collection"),
        "catalogue": catalogue,
        "datetime": props.get("datetime") or props.get("start_datetime"),
        "end_datetime": props.get("end_datetime"),
        "cloud_cover": props.get("eo:cloud_cover"),
        "platform": props.get("platform"),
        "epsg": props.get("proj:epsg") or props.get("proj:code"),
        "bbox": [round(v, 5) for v in bbox] if bbox else None,
        "assets": sorted(k for k in assets if not k.endswith("-jp2")),
        "thumbnail": (assets.get("thumbnail") or {}).get("href"),
        "self": next((l.get("href") for l in feature.get("links", [])
                      if l.get("rel") == "self"), None),
    }


def get_item(item_id, collection, catalogue="earth-search"):
    base = catalogue_url(catalogue)
    cid, _ = collection_id(collection, catalogue)
    safe_id = urllib.parse.quote(str(item_id), safe="")
    safe_cid = urllib.parse.quote(str(cid), safe="")
    return net.get_json(f"{base}/collections/{safe_cid}/items/{safe_id}")


#: Planetary Computer spells Sentinel-2 assets by band number.
PC_ASSET_ALIASES = {
    "blue": "B02", "green": "B03", "red": "B04", "nir": "B08", "nir08": "B8A",
    "rededge1": "B05", "rededge2": "B06", "rededge3": "B07",
    "swir16": "B11", "swir22": "B12", "scl": "SCL", "visual": "visual",
}

#: Hosts a STAC item may legitimately point GDAL at. Anything else is refused
#: so a tampered catalogue response cannot make QGIS open local files or
#: internal addresses.
ALLOWED_ASSET_HOST_SUFFIXES = (
    ".amazonaws.com", ".blob.core.windows.net", "planetarycomputer.microsoft.com",
    ".element84.com", ".usgs.gov", ".copernicus.eu", ".dataspace.copernicus.eu",
    "data.lpdaac.earthdatacloud.nasa.gov",
)


def asset_href(item, asset_key, catalogue="earth-search"):
    """Resolve an asset to a URL GDAL can open, signing it when required."""
    assets = item.get("assets", {})
    asset = assets.get(asset_key)
    if asset is None:
        # Accept common names on collections that spell them differently.
        spec = COLLECTIONS.get(item.get("collection"), {})
        mapped = (spec.get("bands") or {}).get(asset_key)
        asset = assets.get(mapped) if mapped else None
    if asset is None and catalogue == "planetary-computer":
        asset = assets.get(PC_ASSET_ALIASES.get(asset_key, ""))
    if asset is None:
        raise LookupError(
            f"Asset '{asset_key}' not on item {item.get('id')}. Available: "
            f"{sorted(k for k in assets if not k.endswith('-jp2'))}")
    href = check_href(asset["href"])
    if catalogue == "planetary-computer":
        href = check_href(sign_href(href, item.get("collection")))
    return href, asset


PC_TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}"
_TOKENS = {}
_TOKEN_FAILED = {}
TOKEN_RETRY_AFTER_S = 600
SIGN_RETRIES = (2, 5, 10)


def _retrying(fn):
    """Planetary Computer throttles anonymous signing; back off on 429."""
    import time
    for pause in SIGN_RETRIES + (None,):
        try:
            return fn()
        except net.HttpError as exc:
            if exc.status != 429 or pause is None:
                raise
            time.sleep(pause)


def _token_works(href, token):
    """One ranged byte read: the token service has served stale tokens."""
    try:
        net.fetch(f"{href}{'&' if '?' in href else '?'}{token}",
                  headers={"Range": "bytes=0-0"}, timeout_ms=20_000)
        return True
    except net.HttpError:
        return False


def sign_href(href, collection=None):
    """Sign a Planetary Computer asset URL.

    One SAS token per collection is fetched and cached until shortly before
    it expires, so mosaicking many tiles costs one signing call rather than
    one per tile. Falls back to the per URL signing endpoint when the
    collection is unknown or the token endpoint refuses.
    """
    import datetime as _dt
    if "?" in href and "sig=" in href:
        return href
    import time as _time
    if collection and _time.monotonic() - _TOKEN_FAILED.get(collection, -1e9) < TOKEN_RETRY_AFTER_S:
        collection = None  # the token service failed for it recently; sign per URL
    if collection:
        entry = _TOKENS.get(collection)
        now = _dt.datetime.now(_dt.timezone.utc)
        if entry is None or entry[1] <= now + _dt.timedelta(minutes=5):
            try:
                data = _retrying(lambda: net.get_json(PC_TOKEN.format(collection=collection)))
                expiry = data.get("msft:expiry")
                when = (_dt.datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                        if expiry else now + _dt.timedelta(minutes=45))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=_dt.timezone.utc)
                # The token service sometimes hands out an already expired
                # token for a collection; those are useless, so fall through
                # to per URL signing instead of caching them.
                entry = (data.get("token"), when) if when > now + _dt.timedelta(minutes=5) else None
                if entry and not _token_works(href, entry[0]):
                    entry = None
                if entry:
                    _TOKENS[collection] = entry
                else:
                    _TOKEN_FAILED[collection] = _time.monotonic()
            except (net.HttpError, ValueError):
                entry = None
                _TOKEN_FAILED[collection] = _time.monotonic()
        if entry and entry[0]:
            return f"{href}{'&' if '?' in href else '?'}{entry[0]}"
    signed = _retrying(lambda: net.get_json(PC_SIGN, {"href": href}))
    return signed.get("href", href)


def check_href(href):
    """Refuse asset locations that are not remote objects on a known host."""
    href = str(href)
    parsed = urllib.parse.urlparse(href)
    if parsed.scheme == "s3":
        return href
    if parsed.scheme != "https":
        raise ValueError(f"Refusing asset with scheme '{parsed.scheme or 'none'}': "
                         f"{href[:100]}")
    host = (parsed.hostname or "").lower()
    if not any(host == s.lstrip(".") or host.endswith(s)
               for s in ALLOWED_ASSET_HOST_SUFFIXES):
        raise ValueError(f"Refusing asset on unexpected host '{host}'")
    return href


def gdal_path(href):
    """Map an asset href to a GDAL virtual path.

    Public AWS buckets are reached over HTTPS rather than ``/vsis3/`` so no
    credentials, and no unsigned-request switch, are needed.
    """
    if href.startswith("s3://"):
        bucket, _, key = href[len("s3://"):].partition("/")
        return f"/vsicurl/https://{bucket}.s3.amazonaws.com/{key}"
    if href.startswith("https://"):
        return "/vsicurl/" + href
    raise ValueError(f"Unsupported asset location: {href[:100]}")


def asset_scale_offset(asset, default_scale=1.0, default_offset=0.0):
    bands = asset.get("raster:bands") or []
    if bands:
        band = bands[0]
        return (float(band.get("scale", default_scale)),
                float(band.get("offset", default_offset)),
                band.get("nodata"))
    return default_scale, default_offset, None
