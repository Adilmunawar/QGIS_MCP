# -*- coding: utf-8 -*-
"""Global land cover, land use, surface water and elevation rasters.

ESA WorldCover streams straight from its public AWS bucket, tile names being
a function of latitude and longitude. The others come through Planetary
Computer, whose STAC search is keyless and whose asset URLs are signed on
demand (the signature lasts about an hour, long enough to add a layer and
read a window; a saved project should be refreshed with the same command).
"""

import math
import os

import numpy as np
from osgeo import gdal

from . import indices, stac

WORLDCOVER_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
                  "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")

WORLDCOVER_CLASSES = {
    10: ("Tree cover", "#006400"),
    20: ("Shrubland", "#ffbb22"),
    30: ("Grassland", "#ffff4c"),
    40: ("Cropland", "#f096ff"),
    50: ("Built-up", "#fa0000"),
    60: ("Bare / sparse vegetation", "#b4b4b4"),
    70: ("Snow and ice", "#f0f0f0"),
    80: ("Permanent water bodies", "#0064c8"),
    90: ("Herbaceous wetland", "#0096a0"),
    95: ("Mangroves", "#00cf75"),
    100: ("Moss and lichen", "#fae6a0"),
}

IO_LULC_CLASSES = {
    1: ("Water", "#419bdf"), 2: ("Trees", "#397d49"), 4: ("Flooded vegetation", "#7a87c6"),
    5: ("Crops", "#e49635"), 7: ("Built area", "#c4281b"), 8: ("Bare ground", "#a59b8f"),
    9: ("Snow / ice", "#a8ebff"), 10: ("Clouds", "#616161"), 11: ("Rangeland", "#e3e2c3"),
}

#: Named datasets the toolbox can add for an area. ``catalogue`` None means
#: the tiles are addressed directly (WorldCover on AWS).
DATASETS = {
    "worldcover": {
        "name": "ESA WorldCover 2021 (10 m land cover)",
        "catalogue": None, "classes": WORLDCOVER_CLASSES, "nodata": 0,
        "licence": "CC BY 4.0", "attribution": "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium",
        "description": "Eleven land cover classes at 10 m for 2021: trees, shrubs, grass, crops, built-up, bare, snow, water, wetland, mangroves, moss.",
    },
    "land-use": {
        "name": "Esri / Impact Observatory annual land use (10 m)",
        "catalogue": "planetary-computer", "collection": "io-lulc-annual-v02",
        "asset": "data", "classes": IO_LULC_CLASSES, "nodata": 0,
        "licence": "CC BY 4.0", "attribution": "Impact Observatory, Microsoft, Esri",
        "description": "Nine land use classes at 10 m, one map per year since 2017 (latest year is used).",
    },
    "surface-water": {
        "name": "JRC Global Surface Water occurrence (30 m)",
        "catalogue": "planetary-computer", "collection": "jrc-gsw",
        "asset": "occurrence", "classes": None, "nodata": 255,
        "licence": "CC BY 4.0", "attribution": "EC JRC / Google (Pekel et al. 2016)",
        "description": "Share of months with water present, 1984 to 2021, 0-100 %: where water has ever stood, and how often.",
    },
    "nasadem": {
        "name": "NASADEM elevation (30 m)",
        "catalogue": "planetary-computer", "collection": "nasadem",
        "asset": "elevation", "classes": None, "nodata": -32768,
        "licence": "Public domain (NASA)", "attribution": "NASA JPL NASADEM",
        "description": "Reprocessed SRTM elevation, 30 m, an alternative to Copernicus DEM.",
    },
}


def worldcover_tiles(bbox):
    """Names of the 3 by 3 degree WorldCover tiles covering a WGS84 bbox."""
    west, south, east, north = bbox
    tiles = []
    lat = math.floor(south / 3) * 3
    while lat < north:
        lon = math.floor(west / 3) * 3
        while lon < east:
            tiles.append(f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"
                         f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}")
            lon += 3
        lat += 3
    return tiles


def dataset_hrefs(dataset_id, bbox):
    """Resolve the remote files covering ``bbox`` for a named dataset."""
    spec = DATASETS.get(dataset_id)
    if spec is None:
        raise ValueError(f"Unknown dataset '{dataset_id}'. Use one of {sorted(DATASETS)}")
    if spec["catalogue"] is None:
        return spec, [WORLDCOVER_URL.format(tile=t) for t in worldcover_tiles(bbox)]
    found = stac.search(bbox, spec["collection"], None, None, None, 100,
                        spec["catalogue"], keep_assets=True)
    items = found["items"]
    if dataset_id == "land-use" and items:
        # One item per year per UTM zone: keep the latest year only.
        latest = max((i["datetime"] or "") for i in items)
        items = [i for i in items if (i["datetime"] or "") == latest]
    hrefs = []
    for summary in items:
        item = {"id": summary["id"], "collection": summary["collection"],
                "assets": summary.get("raw_assets") or {}}
        if not item["assets"]:
            item = stac.get_item(summary["id"], spec["collection"], spec["catalogue"])
        href, _ = stac.asset_href(item, spec["asset"], spec["catalogue"])
        hrefs.append(href)
    if not hrefs:
        raise LookupError(f"No {spec['name']} tiles cover this area")
    return spec, hrefs


MAX_DATASET_PIXELS = 25_000_000


def build_vrt(path, hrefs, bbox, nodata=None, materialise=False):
    """Mosaic remote tiles clipped to the bbox.

    Tiles may be geographic (WorldCover) or projected in several UTM zones
    (Esri land use), so every source is warped into one target grid: the
    first tile's own CRS and resolution when it is geographic, otherwise the
    UTM zone of the bbox centre. Tiles that do not exist (ocean) are skipped.
    With ``materialise`` the result is a GeoTIFF rather than a VRT, which is
    what signed Planetary Computer URLs need, since they expire within hours.
    """
    from osgeo import osr
    sources = []
    first = None
    for href in hrefs:
        src = stac.gdal_path(href)
        ds = gdal.Open(src)
        if ds is None:
            continue
        if first is None:
            first = ds
        else:
            ds = None
        sources.append(src)
    if first is None:
        raise LookupError("None of the tiles could be opened; the area may be open sea "
                          "or outside the dataset's coverage")
    srs = osr.SpatialReference(wkt=first.GetProjection())
    gt = first.GetGeoTransform()
    first = None
    west, south, east, north = bbox
    if srs.IsGeographic():
        dst_srs = srs.ExportToWkt()
        xres, yres = abs(gt[1]), abs(gt[5])
    else:
        cx, cy = (west + east) / 2, (south + north) / 2
        zone = int((cx + 180) // 6) + 1
        dst_srs = f"EPSG:{(32600 if cy >= 0 else 32700) + zone}"
        xres, yres = abs(gt[1]), abs(gt[5])
    # Bound the output so a large area cannot exhaust memory or disk.
    if srs.IsGeographic():
        pixels = ((east - west) / xres) * ((north - south) / yres)
    else:
        pixels = ((east - west) * 111_320 / xres) * ((north - south) * 110_540 / yres)
    if pixels > MAX_DATASET_PIXELS:
        factor = (pixels / MAX_DATASET_PIXELS) ** 0.5
        xres, yres = xres * factor, yres * factor
    if materialise and path.endswith(".vrt"):
        path = path[:-4] + ".tif"
    options = gdal.WarpOptions(
        format="GTiff" if materialise else "VRT",
        outputBounds=[west, south, east, north], outputBoundsSRS="EPSG:4326",
        dstSRS=dst_srs, xRes=xres, yRes=yres, resampleAlg="near",
        srcNodata=nodata, dstNodata=nodata, multithread=True,
        creationOptions=["COMPRESS=DEFLATE", "TILED=YES"] if materialise else None)
    result = gdal.Warp(path, sources, options=options)
    if result is None:
        raise IOError("GDAL could not build the mosaic")
    result = None
    return path


def class_summary(vrt_path, classes, nodata=None, max_pixels=4_000_000):
    """Share of the area per class, reading the VRT at a bounded resolution."""
    ds = gdal.Open(vrt_path)
    if ds is None:
        raise IOError(f"Could not open {vrt_path}")
    pixels = ds.RasterXSize * ds.RasterYSize
    scale = max(1.0, (pixels / max_pixels) ** 0.5)
    width = max(1, int(ds.RasterXSize / scale))
    height = max(1, int(ds.RasterYSize / scale))
    band = ds.GetRasterBand(1)
    data = band.ReadAsArray(buf_xsize=width, buf_ysize=height,
                            resample_alg=gdal.GRIORA_NearestNeighbour)
    nd = band.GetNoDataValue() if nodata is None else nodata
    valid = data[data != nd] if nd is not None else data.ravel()
    total = int(valid.size)
    values, counts = np.unique(valid, return_counts=True)
    rows = []
    for value, count in zip(values.tolist(), counts.tolist()):
        label, colour = (classes or {}).get(int(value), (f"class {int(value)}", "#888888"))
        rows.append({"value": int(value), "label": label, "colour": colour,
                     "pixels": count, "fraction": round(count / total, 4) if total else 0})
    rows.sort(key=lambda r: r["pixels"], reverse=True)
    gt = ds.GetGeoTransform()
    from osgeo import osr
    srs = osr.SpatialReference(wkt=ds.GetProjection())
    if srs.IsGeographic():
        # Pixel area in km2 for a geographic grid, at the centre latitude.
        centre_lat = gt[3] + gt[5] * ds.RasterYSize / 2
        px_km2 = (abs(gt[1]) * 111.32 * math.cos(math.radians(centre_lat))) * (abs(gt[5]) * 110.54)
    else:
        px_km2 = abs(gt[1]) * abs(gt[5]) / 1_000_000
    for row in rows:
        row["area_km2"] = round(row["fraction"] * total * px_km2 * scale * scale, 3)
    ds = None
    return {"sampled_pixels": total, "downsampled_by": round(scale, 2), "classes": rows}


def surface_water_summary(vrt_path, nodata=255):
    """Occurrence raster: how much of the area is ever, sometimes, always wet."""
    ds = gdal.Open(vrt_path)
    if ds is None:
        raise IOError(f"Could not open {vrt_path}")
    band = ds.GetRasterBand(1)
    data = band.ReadAsArray(buf_xsize=min(ds.RasterXSize, 2000),
                            buf_ysize=min(ds.RasterYSize, 2000))
    nd = band.GetNoDataValue()
    nd = nodata if nd is None else nd
    valid = data[(data != nd)] if nd is not None else data.ravel()
    total = int(valid.size) or 1
    return {
        "ever_water_fraction": round(float((valid > 0).sum()) / total, 4),
        "seasonal_fraction": round(float(((valid > 0) & (valid < 75)).sum()) / total, 4),
        "permanent_fraction": round(float((valid >= 75).sum()) / total, 4),
    }
