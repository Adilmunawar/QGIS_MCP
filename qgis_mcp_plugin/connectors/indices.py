# -*- coding: utf-8 -*-
"""Spectral indices and cloud masking on streamed imagery.

Reads only the window covering the area of interest from each cloud-optimised
GeoTIFF, so a 10 km field costs a few megabytes rather than a whole 100 km
Sentinel-2 tile. Computation is plain numpy; the result is written as a
GeoTIFF with a proper nodata value so QGIS styles it correctly.
"""

import ast
import os
import re

import numpy as np
from osgeo import gdal, osr

from . import stac

gdal.UseExceptions()

# Streaming COGs over HTTP: skip directory listings, merge range requests,
# and cache blocks. Without these a single band open can take 20 seconds.
# Scoped to /vsicurl/ so the user's local rasters keep sidecar discovery.
_REMOTE_OPTIONS = (
    ("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR"),
    ("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES"),
    ("GDAL_HTTP_MULTIRANGE", "YES"),
    ("GDAL_HTTP_MAX_RETRY", "3"),
    ("GDAL_HTTP_RETRY_DELAY", "1"),
    ("VSI_CACHE", "TRUE"),
    ("VSI_CACHE_SIZE", "268435456"),
    ("CPL_VSIL_CURL_CACHE_SIZE", "268435456"),
    ("GDAL_INGESTED_BYTES_AT_OPEN", "65536"),
)
if hasattr(gdal, "SetPathSpecificOption"):
    for _key, _value in _REMOTE_OPTIONS:
        gdal.SetPathSpecificOption("/vsicurl/", _key, _value)
else:  # GDAL < 3.6
    for _key, _value in _REMOTE_OPTIONS:
        if gdal.GetConfigOption(_key) is None:
            gdal.SetConfigOption(_key, _value)

#: Largest window read into memory per band. Above this the read is
#: coarsened automatically and the response reports the resolution used.
MAX_PIXELS = 16_000_000

#: name -> (expression over band names, bands needed, valid range)
INDICES = {
    "NDVI": ("(nir - red) / (nir + red)", ["red", "nir"], (-1, 1),
             "Vegetation vigour. >0.6 dense crop, 0.2-0.4 sparse, <0.1 bare or water"),
    "EVI": ("2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1)",
            ["blue", "red", "nir"], (-1, 1),
            "Vegetation, less saturated than NDVI over dense canopy"),
    "SAVI": ("1.5 * (nir - red) / (nir + red + 0.5)", ["red", "nir"], (-1, 1),
             "Vegetation corrected for bright soil background"),
    "NDWI": ("(green - nir) / (green + nir)", ["green", "nir"], (-1, 1),
             "Open water (McFeeters). >0 water"),
    "MNDWI": ("(green - swir16) / (green + swir16)", ["green", "swir16"], (-1, 1),
              "Open water, suppresses built-up noise. >0 water"),
    "NDMI": ("(nir - swir16) / (nir + swir16)", ["nir", "swir16"], (-1, 1),
             "Vegetation moisture; falling values signal crop water stress"),
    "NDBI": ("(swir16 - nir) / (swir16 + nir)", ["nir", "swir16"], (-1, 1),
             "Built-up areas. >0 urban"),
    "NBR": ("(nir - swir22) / (nir + swir22)", ["nir", "swir22"], (-1, 1),
            "Burn severity; difference pre/post fire to map burn scars"),
    "BSI": ("((swir16 + red) - (nir + blue)) / ((swir16 + red) + (nir + blue))",
            ["blue", "red", "nir", "swir16"], (-1, 1), "Bare soil"),
}

#: Sentinel-2 scene classification values that count as clear ground.
S2_CLEAR_CLASSES = (4, 5, 6, 7)

_ALLOWED_FUNCS = {"sqrt", "abs", "log", "exp", "minimum", "maximum", "where",
                  "clip", "power"}
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)
_ALLOWED_COMPARE = (ast.Gt, ast.GtE, ast.Lt, ast.LtE, ast.Eq, ast.NotEq)
_NON_BAND_ASSETS = {"visual", "scl", "qa_pixel", "data", "thumbnail"}
MAX_EXPONENT = 8


def parse_expression(expression, known_bands):
    """Validate a custom expression with the AST and return its bands.

    Only arithmetic, comparisons, numeric literals, band names and a few
    numpy functions are allowed. Attributes, subscripts, calls to anything
    else and large exponents are rejected, so the expression cannot reach
    Python internals or stall the GUI thread.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("expression must be a non-empty string")
    if len(expression) > 500:
        raise ValueError("expression is too long (500 characters max)")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Could not parse expression: {exc.msg}")
    usable = [b for b in known_bands if b not in _NON_BAND_ASSETS]
    bands = set()

    def visit(node):
        if isinstance(node, ast.Expression):
            visit(node.body)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
            if isinstance(node.op, ast.Pow):
                if not (isinstance(node.right, ast.Constant)
                        and isinstance(node.right.value, (int, float))
                        and abs(node.right.value) <= MAX_EXPONENT):
                    raise ValueError(
                        f"Exponents must be numeric and at most {MAX_EXPONENT}")
            visit(node.left)
            visit(node.right)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, _ALLOWED_UNARY):
            visit(node.operand)
        elif isinstance(node, ast.Compare):
            if not all(isinstance(op, _ALLOWED_COMPARE) for op in node.ops):
                raise ValueError("Unsupported comparison in expression")
            visit(node.left)
            for comparator in node.comparators:
                visit(comparator)
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise ValueError("Only numeric literals are allowed")
        elif isinstance(node, ast.Name):
            if node.id in usable:
                bands.add(node.id)
            elif node.id in _ALLOWED_FUNCS:
                raise ValueError(f"'{node.id}' must be called, e.g. {node.id}(nir)")
            else:
                raise ValueError(
                    f"Unknown name '{node.id}'. Use band names {sorted(usable)} "
                    f"or functions {sorted(_ALLOWED_FUNCS)}")
        elif isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_FUNCS):
                raise ValueError(f"Only these functions are allowed: "
                                 f"{sorted(_ALLOWED_FUNCS)}")
            if node.keywords:
                raise ValueError("Keyword arguments are not allowed")
            for arg in node.args:
                visit(arg)
        else:
            raise ValueError(
                f"Unsupported syntax in expression: {type(node).__name__}")

    visit(tree)
    if not bands:
        raise ValueError("Expression references no bands")
    return sorted(bands)


def resolve_index(index, expression=None, known_bands=()):
    if expression:
        return expression, parse_expression(expression, known_bands), None
    key = (index or "").upper()
    if key not in INDICES:
        raise ValueError(f"Unknown index '{index}'. Use one of {sorted(INDICES)} "
                         "or pass a custom expression")
    expr, bands, _, _ = INDICES[key]
    return expr, bands, key


def _window_pixels(src, bbox, resolution):
    """Estimate the pixel count of a bbox window at the given resolution."""
    gt = src.GetGeoTransform()
    native = abs(gt[1]) if gt else None
    res = resolution or native
    if not res:
        return None, res
    # Degrees to metres, roughly, when the source is projected in metres.
    width_m = abs(bbox[2] - bbox[0]) * 111_320
    height_m = abs(bbox[3] - bbox[1]) * 110_540
    srs = osr.SpatialReference(wkt=src.GetProjection())
    if srs.IsGeographic():
        return (abs(bbox[2] - bbox[0]) / res) * (abs(bbox[3] - bbox[1]) / res), res
    return (width_m / res) * (height_m / res), res


def read_window(href, bbox, resolution=None, resample="bilinear",
                src_nodata=None):
    """Read the part of a COG covering ``bbox`` (WGS84) into memory.

    Returns ``(array, geotransform, projection_wkt, nodata, resolution)``.
    The window is reprojected only to the asset's own CRS; nothing is warped
    to WGS84, so pixel values stay native. Windows above ``MAX_PIXELS`` are
    coarsened so a large area cannot exhaust memory.
    """
    src = gdal.Open(stac.gdal_path(href))
    srs = src.GetProjection()
    if not srs:
        raise ValueError("Asset has no projection (GCP-only imagery such as "
                         "Sentinel-1 GRD is not supported for indices)")
    pixels, res = _window_pixels(src, bbox, resolution)
    if pixels and pixels > MAX_PIXELS:
        res = res * (pixels / MAX_PIXELS) ** 0.5
    options = gdal.WarpOptions(
        format="MEM",
        outputBounds=[float(v) for v in bbox],
        outputBoundsSRS="EPSG:4326",
        dstSRS=srs,
        xRes=res, yRes=res,
        resampleAlg=resample,
        srcNodata=src_nodata,
        dstNodata=src_nodata,
        multithread=True,
    )
    window = gdal.Warp("", src, options=options)
    band = window.GetRasterBand(1)
    array = band.ReadAsArray()
    nodata = band.GetNoDataValue()
    transform = window.GetGeoTransform()
    return array, transform, srs, nodata, res


def _target_resolution(item, bands):
    """Pick the coarsest native resolution among the bands so they align."""
    gsd = []
    for key in bands:
        asset = item.get("assets", {}).get(key) or {}
        value = asset.get("gsd") or (asset.get("raster:bands") or [{}])[0].get(
            "spatial_resolution")
        if value:
            gsd.append(float(value))
    return max(gsd) if gsd else None


def compute(item, bbox, index=None, expression=None, mask_clouds=True,
            catalogue="earth-search", resolution=None):
    """Compute an index over ``bbox`` for one STAC item.

    Returns a dict with the float32 array, georeferencing, and statistics.
    """
    spec = stac.COLLECTIONS.get(item.get("collection"), {})
    band_map = spec.get("bands") or {}
    expr, needed, name = resolve_index(index, expression, list(band_map))
    target = resolution or _target_resolution(
        item, [band_map.get(b, b) for b in needed])

    arrays = {}
    transform = srs = None
    used_resolution = target
    for logical in needed:
        href, asset = stac.asset_href(item, band_map.get(logical, logical),
                                      catalogue)
        scale, offset, nodata_declared = stac.asset_scale_offset(asset)
        raw, transform, srs, nodata, used_resolution = read_window(
            href, bbox, target, src_nodata=nodata_declared)
        data = raw.astype("float32")
        invalid = np.zeros(data.shape, dtype=bool)
        nd = nodata if nodata is not None else nodata_declared
        if nd is not None:
            invalid |= raw == nd
        data = data * scale + offset
        if scale != 1.0 or offset != 0.0:
            # Reflectance: anything below zero is noise from the offset.
            data = np.clip(data, 0.0, None)
        data[invalid] = np.nan
        arrays[logical] = data

    namespace = {k: v for k, v in arrays.items()}
    namespace.update({f: getattr(np, f) for f in _ALLOWED_FUNCS})
    with np.errstate(divide="ignore", invalid="ignore"):
        result = eval(expr, {"__builtins__": {}}, namespace)  # noqa: S307
    result = np.asarray(result, dtype="float32")
    result[~np.isfinite(result)] = np.nan
    if name and INDICES[name][2]:
        lo, hi = INDICES[name][2]
        # Division by a near-zero sum produces absurd values; they are not data.
        result[(result < lo) | (result > hi)] = np.nan

    masked_fraction = None
    if mask_clouds and "scl" in band_map:
        href, _ = stac.asset_href(item, band_map["scl"], catalogue)
        scl, _, _, _, _ = read_window(href, bbox, used_resolution, resample="near")
        clear = np.isin(scl, S2_CLEAR_CLASSES)
        if clear.shape == result.shape:
            masked_fraction = float(1 - clear.mean())
            result[~clear] = np.nan

    valid = result[np.isfinite(result)]
    stats = {
        "valid_pixels": int(valid.size),
        "total_pixels": int(result.size),
        "mean": float(valid.mean()) if valid.size else None,
        "median": float(np.median(valid)) if valid.size else None,
        "min": float(valid.min()) if valid.size else None,
        "max": float(valid.max()) if valid.size else None,
        "stdev": float(valid.std()) if valid.size else None,
        "cloud_masked_fraction": masked_fraction,
    }
    return {
        "index": name or "custom",
        "expression": expr,
        "array": result,
        "transform": transform,
        "projection": srs,
        "resolution": used_resolution,
        "coarsened": bool(target and used_resolution and used_resolution > target * 1.01),
        "statistics": stats,
    }


def align_to(array, transform, projection, target_transform, target_projection, shape):
    """Resample ``array`` onto another grid (used to difference two scenes)."""
    rows, cols = array.shape
    src = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gdal.GDT_Float32)
    src.SetGeoTransform(transform)
    src.SetProjection(projection)
    band = src.GetRasterBand(1)
    band.SetNoDataValue(-9999.0)
    band.WriteArray(np.where(np.isfinite(array), array, -9999.0).astype("float32"))
    t_rows, t_cols = shape
    gt = target_transform
    bounds = [gt[0], gt[3] + gt[5] * t_rows, gt[0] + gt[1] * t_cols, gt[3]]
    out = gdal.Warp("", src, options=gdal.WarpOptions(
        format="MEM", dstSRS=target_projection, outputBounds=bounds,
        width=t_cols, height=t_rows, resampleAlg="bilinear",
        srcNodata=-9999.0, dstNodata=-9999.0))
    data = out.GetRasterBand(1).ReadAsArray().astype("float32")
    data[data == -9999.0] = np.nan
    return data


def write_geotiff(path, array, transform, projection, nodata=-9999.0):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    data = np.where(np.isfinite(array), array, nodata).astype("float32")
    driver = gdal.GetDriverByName("GTiff")
    rows, cols = data.shape
    out = driver.Create(path, cols, rows, 1, gdal.GDT_Float32,
                        options=["COMPRESS=DEFLATE", "TILED=YES"])
    out.SetGeoTransform(transform)
    srs = osr.SpatialReference()
    srs.SetFromUserInput(projection)
    out.SetProjection(srs.ExportToWkt())
    band = out.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    band.WriteArray(data)
    band.ComputeStatistics(False)
    out.FlushCache()
    out = None
    return path


def classify(array, breaks):
    """Share of valid pixels in each interval defined by ascending ``breaks``."""
    try:
        breaks = [float(b) for b in breaks]
    except (TypeError, ValueError):
        raise ValueError("breaks must be a list of numbers, e.g. [0.2, 0.4, 0.6]")
    if breaks != sorted(breaks):
        raise ValueError("breaks must be ascending")
    valid = array[np.isfinite(array)]
    if valid.size == 0:
        return []
    edges = [-np.inf] + list(breaks) + [np.inf]
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        count = int(((valid >= lo) & (valid < hi)).sum())
        rows.append({"from": None if lo == -np.inf else lo,
                     "to": None if hi == np.inf else hi,
                     "pixels": count,
                     "fraction": round(count / valid.size, 4)})
    return rows
