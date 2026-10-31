# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Global rasters for an area: land cover, land use, forest, biomass, surface
water, elevation, land surface temperature, vegetation, snow, burned area,
evapotranspiration, productivity, biodiversity and electrification.

ESA WorldCover streams straight from its public AWS bucket, tile names being
a function of latitude and longitude. The others come through Planetary
Computer, whose STAC search is keyless and whose asset URLs are signed on
demand (the signature lasts about an hour, long enough to add a layer and
read a window; a saved project should be refreshed with the same command).

Every dataset is described once in :data:`DATASETS`. Categorical datasets
carry ``classes``; continuous ones carry ``scale``, ``offset``, ``unit``,
``valid_range`` (in scaled units) and a colour ``ramp``. Datasets whose
items are dated (MODIS, Esri land use, CCI) set ``latest`` so only the most
recent date is mosaicked.
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

CCI_CLASSES = {
    10: ("Cropland, rainfed", "#ffff64"), 11: ("Cropland, herbaceous cover", "#ffff64"),
    12: ("Cropland, tree or shrub cover", "#ffff00"), 20: ("Cropland, irrigated", "#aaf0f0"),
    30: ("Mosaic cropland (>50%) / natural vegetation", "#dcf064"),
    40: ("Mosaic natural vegetation (>50%) / cropland", "#c8c864"),
    50: ("Tree cover, broadleaved evergreen", "#006400"),
    60: ("Tree cover, broadleaved deciduous", "#00a000"), 61: ("Tree cover, broadleaved deciduous, closed", "#00a000"),
    62: ("Tree cover, broadleaved deciduous, open", "#aac800"),
    70: ("Tree cover, needleleaved evergreen", "#003c00"), 71: ("Tree cover, needleleaved evergreen, closed", "#003c00"),
    72: ("Tree cover, needleleaved evergreen, open", "#005000"),
    80: ("Tree cover, needleleaved deciduous", "#285000"), 81: ("Tree cover, needleleaved deciduous, closed", "#285000"),
    82: ("Tree cover, needleleaved deciduous, open", "#286400"),
    90: ("Tree cover, mixed leaf type", "#788200"), 100: ("Mosaic tree and shrub (>50%) / herbaceous", "#8ca000"),
    110: ("Mosaic herbaceous (>50%) / tree and shrub", "#be9600"), 120: ("Shrubland", "#966400"),
    121: ("Shrubland, evergreen", "#784b00"), 122: ("Shrubland, deciduous", "#966400"),
    130: ("Grassland", "#ffb432"), 140: ("Lichens and mosses", "#ffdcd2"),
    150: ("Sparse vegetation", "#ffebaf"), 151: ("Sparse tree", "#ffc864"), 152: ("Sparse shrub", "#ffd278"),
    153: ("Sparse herbaceous", "#ffebaf"), 160: ("Tree cover, flooded, fresh water", "#00785a"),
    170: ("Tree cover, flooded, saline water", "#009678"), 180: ("Shrub or herbaceous cover, flooded", "#00dc82"),
    190: ("Urban areas", "#c31400"), 200: ("Bare areas", "#fff5d7"), 201: ("Bare areas, consolidated", "#dcdcdc"),
    202: ("Bare areas, unconsolidated", "#fff5d7"), 210: ("Water bodies", "#0046c8"), 220: ("Permanent snow and ice", "#ffffff"),
}

FNF_CLASSES = {
    1: ("Forest, dense (canopy over 90%)", "#006400"), 2: ("Forest, open (canopy 10 to 90%)", "#55aa00"),
    3: ("Non forest", "#f0e68c"), 4: ("Water", "#0064c8"),
}

RAMP_GREEN = ["#f7fcf5", "#c7e9c0", "#74c476", "#238b45", "#00441b"]
RAMP_HEAT = ["#313695", "#74add1", "#ffffbf", "#f46d43", "#a50026"]
RAMP_BLUE = ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"]
RAMP_TERRAIN = ["#2c7bb6", "#abd9e9", "#ffffbf", "#fdae61", "#d7191c"]
RAMP_FIRE = ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]
RAMP_GREY = ["#000000", "#404040", "#808080", "#c0c0c0", "#ffffff"]

PC = "planetary-computer"

#: Named datasets the toolbox can add for an area. ``catalogue`` None means
#: the tiles are addressed directly (WorldCover on AWS).
DATASETS = {
    "worldcover": {
        "name": "ESA WorldCover 2021 (10 m land cover)", "category": "land cover",
        "catalogue": None, "classes": WORLDCOVER_CLASSES, "nodata": 0,
        "licence": "CC BY 4.0", "attribution": "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium",
        "description": "Eleven land cover classes at 10 m for 2021: trees, shrubs, grass, crops, built-up, bare, snow, water, wetland, mangroves, moss.",
    },
    "land-use": {
        "name": "Esri / Impact Observatory annual land use (10 m)", "category": "land cover",
        "catalogue": PC, "collection": "io-lulc-annual-v02", "latest": True,
        "asset": "data", "classes": IO_LULC_CLASSES, "nodata": 0,
        "licence": "CC BY 4.0", "attribution": "Impact Observatory, Microsoft, Esri",
        "description": "Nine land use classes at 10 m, one map per year since 2017 (latest year is used).",
    },
    "cci-land-cover": {
        "name": "ESA CCI land cover 2020 (300 m, 22 classes)", "category": "land cover",
        "catalogue": PC, "collection": "esa-cci-lc", "latest": True,
        "asset": "lccs_class", "classes": CCI_CLASSES, "nodata": 0,
        "licence": "ESA CCI / Copernicus C3S licence (free, attribution)", "attribution": "ESA Climate Change Initiative, Copernicus Climate Change Service",
        "description": "Consistent global land cover with 22 classes, every year 1992 to 2020 (2020 is used); best for long term change.",
    },
    "forest": {
        "name": "ALOS PALSAR forest / non forest 2020 (25 m)", "category": "land cover",
        "catalogue": PC, "collection": "alos-fnf-mosaic", "latest": True,
        "asset": "C", "classes": FNF_CLASSES, "nodata": 0,
        "licence": "JAXA terms (free, attribution)", "attribution": "JAXA ALOS-2 PALSAR-2 Forest / Non Forest map",
        "description": "Radar derived forest map: dense forest, open forest, non forest and water at 25 m.",
    },
    "surface-water": {
        "name": "JRC Global Surface Water occurrence (30 m)", "category": "water",
        "catalogue": PC, "collection": "jrc-gsw",
        "asset": "occurrence", "classes": None, "nodata": 255, "unit": "% of months",
        "valid_range": (0, 100), "ramp": RAMP_BLUE, "summary": "water",
        "licence": "CC BY 4.0", "attribution": "EC JRC / Google (Pekel et al. 2016)",
        "description": "Share of months with water present, 1984 to 2021, 0 to 100 %: where water has ever stood, and how often.",
    },
    "nasadem": {
        "name": "NASADEM elevation (30 m)", "category": "elevation",
        "catalogue": PC, "collection": "nasadem",
        "asset": "elevation", "classes": None, "nodata": -32768, "unit": "m", "ramp": RAMP_TERRAIN,
        "licence": "Public domain (NASA)", "attribution": "NASA JPL NASADEM",
        "description": "Reprocessed SRTM elevation, 30 m, an alternative to Copernicus DEM.",
    },
    "alos-dem": {
        "name": "ALOS World 3D elevation (30 m)", "category": "elevation",
        "catalogue": PC, "collection": "alos-dem",
        "asset": "data", "classes": None, "nodata": -9999, "unit": "m", "ramp": RAMP_TERRAIN,
        "licence": "JAXA terms (free, attribution)", "attribution": "JAXA AW3D30",
        "description": "Digital surface model from ALOS PRISM stereo imagery, 30 m, third global elevation option.",
    },
    "copernicus-dem-90": {
        "name": "Copernicus DEM GLO-90 (90 m)", "category": "elevation",
        "catalogue": PC, "collection": "cop-dem-glo-90",
        "asset": "data", "classes": None, "nodata": None, "unit": "m", "ramp": RAMP_TERRAIN,
        "licence": "Copernicus DEM licence (free, attribution)", "attribution": "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH, provided under COPERNICUS by the European Union and ESA",
        "description": "Coarser Copernicus DEM for regional overviews where 30 m is more than needed.",
    },
    "height-above-ground": {
        "name": "USGS 3DEP lidar height above ground (2 m, USA)", "category": "elevation",
        "catalogue": PC, "collection": "3dep-lidar-hag", "latest": True,
        "asset": "data", "classes": None, "nodata": -9999, "unit": "m", "valid_range": (0, 150),
        "ramp": RAMP_GREEN,
        "licence": "Public domain (USGS)", "attribution": "USGS 3D Elevation Program",
        "description": "Lidar derived height of buildings and trees above the ground, 2 m, United States only.",
    },
    "land-surface-temperature": {
        "name": "MODIS land surface temperature, 8 day daytime (1 km)", "category": "climate",
        "catalogue": PC, "collection": "modis-11A2-061", "latest": True,
        "asset": "LST_Day_1km", "classes": None, "nodata": 0, "scale": 0.02, "offset": -273.15,
        "unit": "°C", "valid_range": (-60, 70), "ramp": RAMP_HEAT,
        "licence": "Public domain (NASA LP DAAC)", "attribution": "NASA MODIS MOD11A2 / MYD11A2 v061",
        "description": "Daytime skin temperature of the land surface averaged over the latest 8 day period, in degrees Celsius.",
    },
    "ndvi-modis": {
        "name": "MODIS NDVI, 16 day (250 m)", "category": "vegetation",
        "catalogue": PC, "collection": "modis-13Q1-061", "latest": True,
        "asset": "250m_16_days_NDVI", "classes": None, "nodata": -3000, "scale": 0.0001,
        "unit": "index", "valid_range": (-0.2, 1.0), "ramp": RAMP_GREEN,
        "licence": "Public domain (NASA LP DAAC)", "attribution": "NASA MODIS MOD13Q1 v061",
        "description": "Vegetation greenness composite for the latest 16 day period; coarser than Sentinel-2 but cloud free and consistent since 2000.",
    },
    "leaf-area-index": {
        "name": "MODIS leaf area index, 8 day (500 m)", "category": "vegetation",
        "catalogue": PC, "collection": "modis-15A2H-061", "latest": True,
        "asset": "Lai_500m", "classes": None, "nodata": 255, "scale": 0.1,
        "unit": "m² leaf per m² ground", "valid_range": (0, 10), "ramp": RAMP_GREEN,
        "licence": "Public domain (NASA LP DAAC)", "attribution": "NASA MODIS MOD15A2H / MYD15A2H v061",
        "description": "One sided green leaf area per unit ground area, a canopy density measure used in crop and forest models.",
    },
    "evapotranspiration": {
        "name": "MODIS annual evapotranspiration (500 m)", "category": "climate",
        "catalogue": PC, "collection": "modis-16A3GF-061", "latest": True,
        "asset": "ET_500m", "classes": None, "nodata": 32767, "scale": 0.1,
        "unit": "mm per year", "valid_range": (0, 3270), "ramp": RAMP_BLUE,
        "licence": "Public domain (NASA LP DAAC)", "attribution": "NASA MODIS MOD16A3GF / MYD16A3GF v061",
        "description": "Water lost to the atmosphere from soil and plants over the latest complete year, in millimetres.",
    },
    "net-primary-productivity": {
        "name": "MODIS annual net primary productivity (500 m)", "category": "vegetation",
        "catalogue": PC, "collection": "modis-17A3HGF-061", "latest": True,
        "asset": "Npp_500m", "classes": None, "nodata": 32767, "scale": 0.0001,
        "unit": "kg carbon per m² per year", "valid_range": (0, 3.27), "ramp": RAMP_GREEN,
        "licence": "Public domain (NASA LP DAAC)", "attribution": "NASA MODIS MOD17A3HGF / MYD17A3HGF v061",
        "description": "Carbon fixed by vegetation over the latest complete year, a proxy for biomass growth and yield potential.",
    },
    "snow-cover": {
        "name": "MODIS daily snow cover (500 m)", "category": "climate",
        "catalogue": PC, "collection": "modis-10A1-061", "latest": True,
        "asset": "NDSI_Snow_Cover", "classes": None, "nodata": 255,
        "unit": "% snow cover (NDSI)", "valid_range": (0, 100), "ramp": RAMP_GREY, "threshold": 0,
        "licence": "Public domain (NASA NSIDC)", "attribution": "NASA MODIS MOD10A1 / MYD10A1 v061",
        "description": "Snow cover for the latest day; cloud, night and water pixels are masked out.",
    },
    "burned-area": {
        "name": "MODIS monthly burned area (500 m)", "category": "hazards",
        "catalogue": PC, "collection": "modis-64A1-061", "latest": True,
        "asset": "Burn_Date", "classes": None, "nodata": -1,
        "unit": "day of year burned (0 = unburned)", "valid_range": (0, 366), "ramp": RAMP_FIRE, "threshold": 0,
        "licence": "Public domain (NASA LP DAAC)", "attribution": "NASA MODIS MCD64A1 v061",
        "description": "Pixels that burned in the latest available month, with the day of year of the burn.",
    },
    "biomass": {
        "name": "Harmonized global above ground biomass 2010 (300 m)", "category": "vegetation",
        "catalogue": PC, "collection": "hgb",
        "asset": "aboveground", "classes": None, "nodata": None,
        "unit": "Mg carbon per hectare", "valid_range": (0, 1000), "ramp": RAMP_GREEN,
        "licence": "Public domain (ORNL DAAC)", "attribution": "Spawn and Gibbs 2020, ORNL DAAC",
        "description": "Above ground living biomass carbon density, harmonised from several maps for 2010.",
    },
    "biodiversity-intactness": {
        "name": "Biodiversity Intactness Index 2020 (100 m)", "category": "biodiversity",
        "catalogue": PC, "collection": "io-biodiversity", "latest": True,
        "asset": "data", "classes": None, "nodata": None,
        "unit": "share of original biodiversity (0 to 1)", "valid_range": (0, 1), "ramp": RAMP_GREEN,
        "licence": "CC BY 4.0", "attribution": "Impact Observatory, Vizzuality (Biodiversity Intactness Index)",
        "description": "How much of the original species community is estimated to remain, from 0 (none) to 1 (intact).",
    },
    "population": {
        "name": "Meta high resolution population density (30 m)", "category": "settlements",
        "catalogue": "direct", "url": "https://dataforgood-fb-data.s3.amazonaws.com/hrsl-cogs/hrsl_general/hrsl_general-latest.vrt",
        "classes": None, "nodata": None, "unit": "people per 30 m cell", "valid_range": (0, 100000),
        "ramp": RAMP_HEAT, "summary": "sum", "source_id": "meta-hrsl",
        "licence": "CC BY 4.0", "attribution": "Meta Data for Good, High Resolution Settlement Layer (with CIESIN)",
        "description": "Estimated residents per 30 m cell from satellite building detection and census totals; summing the cells gives the population of the area.",
    },
    "electrification": {
        "name": "High Resolution Electricity Access 2019 (lightscore)", "category": "settlements",
        "catalogue": PC, "collection": "hrea", "latest": True,
        "asset": "lightscore", "classes": None, "nodata": None,
        "unit": "probability of electricity access (0 to 1)", "valid_range": (0, 1), "ramp": RAMP_HEAT,
        "licence": "CC BY 4.0", "attribution": "University of Michigan HREA (Min et al.)",
        "description": "Likelihood that a settlement is electrified, estimated from VIIRS night lights, for low and middle income countries.",
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
    if spec["catalogue"] == "direct":
        return spec, [spec["url"]]
    found = stac.search(bbox, spec["collection"], None, None, None, 100,
                        spec["catalogue"], keep_assets=True)
    items = found["items"]
    if spec.get("latest") and items:
        # One item per date per tile: keep the most recent date only. MODIS
        # products from two satellites share dates, so a tile may still
        # appear twice; the first (Terra, as served) wins.
        latest = max((i["datetime"] or "") for i in items)
        items = [i for i in items if (i["datetime"] or "") == latest]
        seen_tiles = set()
        unique = []
        for item in items:
            key = tuple(item.get("bbox") or [item["id"]])
            if key not in seen_tiles:
                seen_tiles.add(key)
                unique.append(item)
        items = unique
    if len(items) > MAX_TILES:
        raise ValueError(f"{len(items)} tiles cover this area; the limit is {MAX_TILES}. "
                         "Shrink the area of interest.")
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
MAX_TILES = 30
OUTPUT_NODATA = -9999.0


def build_vrt(path, hrefs, bbox, nodata=None, materialise=False, spec=None):
    """Mosaic remote tiles clipped to the bbox.

    Tiles may be geographic (WorldCover) or projected in several UTM zones
    (Esri land use) or MODIS sinusoidal, so every source is warped into one
    target grid: the first tile's own CRS and resolution when it is
    geographic, otherwise the UTM zone of the bbox centre. Tiles that do not
    exist (ocean) are skipped. With ``materialise`` the result is a GeoTIFF
    rather than a VRT, which is what signed Planetary Computer URLs need,
    since they expire within hours. When ``spec`` describes a scale, offset
    or valid range the GeoTIFF is converted to physical units (Float32).
    """
    from osgeo import osr
    sources = []
    first = None
    last_error = None
    for href in hrefs:
        src = stac.gdal_path(href)
        try:
            ds = gdal.Open(src)
        except RuntimeError as exc:  # GDAL exceptions enabled: 403, 404, timeouts
            last_error = str(exc)[:160]
            ds = None
        if ds is None:
            continue
        if first is None:
            first = ds
        else:
            ds = None
        sources.append(src)
    if first is None:
        raise LookupError("None of the tiles could be opened; the area may be open sea "
                          "or outside the dataset's coverage"
                          + (f" (last error: {last_error})" if last_error else ""))
    srs = osr.SpatialReference(wkt=first.GetProjection())
    gt = first.GetGeoTransform()
    if nodata is None:
        nodata = first.GetRasterBand(1).GetNoDataValue()
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
    # Without a source nodata the fill outside tile footprints would be 0,
    # a legitimate value for most continuous rasters; mark it explicitly.
    dst_nodata = nodata if nodata is not None else (OUTPUT_NODATA if materialise else None)
    options = gdal.WarpOptions(
        format="GTiff" if materialise else "VRT",
        outputBounds=[west, south, east, north], outputBoundsSRS="EPSG:4326",
        dstSRS=dst_srs, xRes=xres, yRes=yres,
        resampleAlg="sum" if (spec or {}).get("summary") == "sum" else "near",
        srcNodata=nodata, dstNodata=dst_nodata, multithread=True,
        creationOptions=["COMPRESS=DEFLATE", "TILED=YES"] if materialise else None)
    result = gdal.Warp(path, sources, options=options)
    if result is None:
        raise IOError("GDAL could not build the mosaic")
    result = None
    if materialise and spec and _needs_conversion(spec):
        path = to_physical_units(path, spec)
    return path


def _needs_conversion(spec):
    return (spec.get("scale") not in (None, 1, 1.0) or spec.get("offset")
            or spec.get("valid_range") is not None or spec.get("transform"))


def to_physical_units(path, spec):
    """Rewrite a raw mosaic as Float32 in physical units, masking invalid pixels.

    Applies ``value * scale + offset`` (or a decibel transform), drops the
    source nodata and anything outside ``valid_range``, and writes
    :data:`OUTPUT_NODATA`. Processed in row blocks so memory stays bounded.
    """
    src = gdal.Open(path)
    if src is None:
        raise IOError(f"Could not open {path}")
    band = src.GetRasterBand(1)
    src_nodata = band.GetNoDataValue()
    if src_nodata is None:
        src_nodata = spec.get("nodata")
    out_path = path[:-4] + "_phys.tif"
    driver = gdal.GetDriverByName("GTiff")
    out = driver.Create(out_path, src.RasterXSize, src.RasterYSize, 1, gdal.GDT_Float32,
                        options=["COMPRESS=DEFLATE", "TILED=YES"])
    out.SetGeoTransform(src.GetGeoTransform())
    out.SetProjection(src.GetProjection())
    out_band = out.GetRasterBand(1)
    out_band.SetNoDataValue(OUTPUT_NODATA)
    scale = float(spec.get("scale") or 1.0)
    offset = float(spec.get("offset") or 0.0)
    valid = spec.get("valid_range")
    rows = 1024
    for y in range(0, src.RasterYSize, rows):
        h = min(rows, src.RasterYSize - y)
        raw = band.ReadAsArray(0, y, src.RasterXSize, h).astype("float64")
        mask = ~np.isfinite(raw)
        if src_nodata is not None:
            mask |= raw == src_nodata
        if spec.get("transform") == "db":
            with np.errstate(divide="ignore", invalid="ignore"):
                data = 10.0 * np.log10(np.where(raw > 0, raw, np.nan))
            mask |= ~np.isfinite(data)
        else:
            data = raw * scale + offset
        if valid is not None:
            mask |= (data < valid[0]) | (data > valid[1])
        data[mask] = OUTPUT_NODATA
        out_band.WriteArray(data.astype("float32"), 0, y)
    if spec.get("unit"):
        out_band.SetUnitType(str(spec["unit"]))
    out_band.FlushCache()
    out_band = None
    out = None
    band = None
    src = None
    try:
        os.remove(path)
    except OSError:
        pass
    return out_path


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
    px_km2 = _pixel_km2(ds)
    for row in rows:
        row["area_km2"] = round(row["fraction"] * total * px_km2 * scale * scale, 3)
    ds = None
    return {"sampled_pixels": total, "downsampled_by": round(scale, 2), "classes": rows}


def _pixel_km2(ds):
    from osgeo import osr
    gt = ds.GetGeoTransform()
    srs = osr.SpatialReference(wkt=ds.GetProjection())
    if srs.IsGeographic():
        # Pixel area in km2 for a geographic grid, at the centre latitude.
        centre_lat = gt[3] + gt[5] * ds.RasterYSize / 2
        return (abs(gt[1]) * 111.32 * math.cos(math.radians(centre_lat))) * (abs(gt[5]) * 110.54)
    return abs(gt[1]) * abs(gt[5]) / 1_000_000


def continuous_summary(path, nodata=None, threshold=None, max_pixels=4_000_000):
    """Statistics of a continuous raster: range, mean, median, percentiles.

    ``threshold`` adds the share and area of valid pixels strictly above it
    (burned area, snow cover and similar presence rasters).
    """
    ds = gdal.Open(path)
    if ds is None:
        raise IOError(f"Could not open {path}")
    pixels = ds.RasterXSize * ds.RasterYSize
    scale = max(1.0, (pixels / max_pixels) ** 0.5)
    width = max(1, int(ds.RasterXSize / scale))
    height = max(1, int(ds.RasterYSize / scale))
    band = ds.GetRasterBand(1)
    data = band.ReadAsArray(buf_xsize=width, buf_ysize=height).astype("float64")
    nd = band.GetNoDataValue() if nodata is None else nodata
    mask = np.isfinite(data)
    if nd is not None:
        mask &= data != nd
    valid = data[mask]
    px_km2 = _pixel_km2(ds) * scale * scale
    ds = None
    total = int(data.size)
    if valid.size == 0:
        return {"valid_pixels": 0, "valid_fraction": 0.0, "note": "No valid pixels in the area"}
    pct = np.percentile(valid, [2, 25, 50, 75, 98])
    out = {
        "valid_pixels": int(valid.size), "valid_fraction": round(valid.size / total, 4),
        "sampled_pixels": total, "downsampled_by": round(scale, 2),
        "min": round(float(valid.min()), 4), "max": round(float(valid.max()), 4),
        "mean": round(float(valid.mean()), 4), "median": round(float(pct[2]), 4),
        "p2": round(float(pct[0]), 4), "p25": round(float(pct[1]), 4),
        "p75": round(float(pct[3]), 4), "p98": round(float(pct[4]), 4),
        "area_km2": round(valid.size * px_km2, 3),
    }
    out["sum"] = round(float(valid.sum()) * scale * scale, 2)
    if threshold is not None:
        above = int((valid > threshold).sum())
        out["above_threshold"] = threshold
        out["above_fraction"] = round(above / valid.size, 4)
        out["above_area_km2"] = round(above * px_km2, 3)
    return out


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


def summarise(path, spec):
    """The right summary for a dataset: classes, water occurrence or statistics."""
    if spec.get("classes"):
        return landcover_summary_label("classes", class_summary(path, spec["classes"], spec.get("nodata")))
    if spec.get("summary") == "water":
        out = surface_water_summary(path, spec.get("nodata") if spec.get("nodata") is not None else 255)
        return landcover_summary_label("water", out)
    nodata = OUTPUT_NODATA if _needs_conversion(spec) else spec.get("nodata")
    summary = continuous_summary(path, nodata, spec.get("threshold"))
    if spec.get("summary") == "sum":
        summary["sum"] = raster_sum(path, nodata)
        summary["total"] = int(round(summary["sum"]))
        summary["note"] = ("total is the exact sum of the cells in the area; a very large area is "
                           "coarsened with sum resampling, which keeps the total")
    return landcover_summary_label("continuous", summary)


def raster_sum(path, nodata=None, rows=1024):
    """Exact sum of valid cells, read in row blocks at native resolution."""
    ds = gdal.Open(path)
    if ds is None:
        raise IOError(f"Could not open {path}")
    band = ds.GetRasterBand(1)
    nd = band.GetNoDataValue() if nodata is None else nodata
    total = 0.0
    for y in range(0, ds.RasterYSize, rows):
        h = min(rows, ds.RasterYSize - y)
        block = band.ReadAsArray(0, y, ds.RasterXSize, h).astype("float64")
        mask = np.isfinite(block)
        if nd is not None:
            mask &= block != nd
        total += float(block[mask].sum())
    ds = None
    return round(total, 2)


def landcover_summary_label(kind, summary):
    summary["kind"] = kind
    return summary
