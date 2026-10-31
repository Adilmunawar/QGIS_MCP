# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
"""Tiled basemaps, including NASA GIBS daily imagery selectable by date."""

import datetime as _dt
import urllib.parse

GIBS_WMTS = ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/"
             "{date}/GoogleMapsCompatible_Level{level}/{{z}}/{{y}}/{{x}}.{ext}")

GIBS_LAYERS = {
    "modis-terra-truecolor": ("MODIS_Terra_CorrectedReflectance_TrueColor", 9, "jpg",
                              "MODIS Terra true colour, daily since 2000"),
    "modis-aqua-truecolor": ("MODIS_Aqua_CorrectedReflectance_TrueColor", 9, "jpg",
                             "MODIS Aqua true colour, daily since 2002"),
    "viirs-snpp-truecolor": ("VIIRS_SNPP_CorrectedReflectance_TrueColor", 9, "jpg",
                             "VIIRS true colour, daily since 2015, 375 m"),
    "viirs-noaa20-truecolor": ("VIIRS_NOAA20_CorrectedReflectance_TrueColor", 9, "jpg",
                               "VIIRS NOAA-20 true colour, daily"),
    "modis-terra-ndvi-8day": ("MODIS_Terra_NDVI_8Day", 9, "png",
                              "MODIS NDVI 8-day composite"),
    "modis-lst-day": ("MODIS_Terra_Land_Surface_Temp_Day", 7, "png",
                      "MODIS land surface temperature, daytime"),
    "viirs-nightlights": ("VIIRS_SNPP_DayNightBand_At_Sensor_Radiance", 8, "png",
                          "VIIRS night-time lights"),
    "modis-fires": ("MODIS_Combined_Thermal_Anomalies_All", 9, "png",
                    "MODIS thermal anomalies / fires"),
    "modis-snow-cover": ("MODIS_Terra_NDSI_Snow_Cover", 8, "png",
                         "MODIS snow cover"),
    "modis-aod": ("MODIS_Combined_Value_Added_AOD", 6, "png",
                  "Aerosol optical depth (dust, smoke, haze)"),
    "gpm-precipitation": ("IMERG_Precipitation_Rate", 6, "png",
                          "GPM IMERG precipitation rate, half-hourly"),
}

STATIC_BASEMAPS = {
    "osm": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", 0, 19,
            "OpenStreetMap standard", "© OpenStreetMap contributors"),
    "osm-humanitarian": ("https://a.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png", 0, 19,
                         "Humanitarian OSM style", "© OpenStreetMap contributors, HOT"),
    "esri-imagery": ("https://server.arcgisonline.com/ArcGIS/rest/services/"
                     "World_Imagery/MapServer/tile/{z}/{y}/{x}", 0, 19,
                     "Esri World Imagery",
                     "Esri, Maxar, Earthstar Geographics, GIS User Community"),
    "esri-topo": ("https://server.arcgisonline.com/ArcGIS/rest/services/"
                  "World_Topo_Map/MapServer/tile/{z}/{y}/{x}", 0, 19,
                  "Esri World Topographic", "Esri"),
    "opentopomap": ("https://a.tile.opentopomap.org/{z}/{x}/{y}.png", 0, 17,
                    "OpenTopoMap", "© OpenStreetMap contributors, SRTM; OpenTopoMap (CC-BY-SA)"),
    "carto-light": ("https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png", 0, 20,
                    "CARTO Positron light", "© OpenStreetMap contributors, © CARTO"),
    "carto-dark": ("https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png", 0, 20,
                   "CARTO Dark Matter", "© OpenStreetMap contributors, © CARTO"),
}


def licence_for(basemap_id):
    if basemap_id in GIBS_LAYERS:
        return "Public domain (NASA GIBS)"
    return {
        "osm": "ODbL (data); tile usage policy applies",
        "osm-humanitarian": "ODbL (data); HOT tile usage policy applies",
        "esri-imagery": "Esri terms of use; attribution required",
        "esri-topo": "Esri terms of use; attribution required",
        "opentopomap": "CC BY-SA 3.0 (OpenTopoMap)",
        "carto-light": "CARTO basemap terms; attribution required",
        "carto-dark": "CARTO basemap terms; attribution required",
    }.get(basemap_id, "See provider terms")


def list_basemaps():
    rows = [{"id": k, "name": v[3], "kind": "static", "attribution": v[4]}
            for k, v in STATIC_BASEMAPS.items()]
    rows += [{"id": k, "name": v[3], "kind": "gibs-daily", "dated": True,
              "attribution": "NASA GIBS"} for k, v in GIBS_LAYERS.items()]
    return rows


def xyz_uri(url, zmin=0, zmax=19):
    encoded = urllib.parse.quote(url, safe=":/?=&")
    return f"type=xyz&url={encoded}&zmin={zmin}&zmax={zmax}"


def basemap_uri(basemap_id, date=None):
    """Return ``(uri, display_name, attribution)`` for a basemap id."""
    if basemap_id in STATIC_BASEMAPS:
        url, zmin, zmax, name, attribution = STATIC_BASEMAPS[basemap_id]
        return xyz_uri(url, zmin, zmax), name, attribution
    if basemap_id in GIBS_LAYERS:
        layer, level, ext, name = GIBS_LAYERS[basemap_id]
        if date:
            try:
                parsed = _dt.date.fromisoformat(date)
            except ValueError:
                raise ValueError("date must be YYYY-MM-DD")
            if parsed > _dt.date.today():
                raise ValueError("date is in the future; GIBS has no imagery yet")
        else:
            date = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
        url = GIBS_WMTS.format(layer=layer, date=date, level=level, ext=ext)
        return xyz_uri(url, 0, level), f"{name} {date}", "NASA GIBS"
    raise ValueError(
        f"Unknown basemap '{basemap_id}'. Use one of "
        f"{sorted(STATIC_BASEMAPS) + sorted(GIBS_LAYERS)}")
