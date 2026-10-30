# Live Data Connectors: Implementation Reference

Researched 2026-10-31. Legend: **[V]** = verified live today with an HTTP request (status 200 / real payload seen); **[K]** = from documentation/knowledge, not re-fetched today; **UNVERIFIED** = could not confirm. Always send a descriptive `User-Agent` (e.g. `qgis-mcp/1.x (contact@example)`) on every request; several services (Nominatim, Overpass, OpenAQ) reject or throttle anonymous UAs.

## 0. Cross-cutting implementation notes

- **Raster loading in QGIS**: COG over HTTP is `/vsicurl/https://...tif`; for public S3 buckets `/vsis3/<bucket>/<key>` with `AWS_NO_SIGN_REQUEST=YES`. Create with `QgsRasterLayer("/vsicurl/<url>", name, "gdal")`. Set GDAL options once: `CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif,.tiff`, `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`, `GDAL_HTTP_MULTIPLEX=YES`.
- **XYZ/WMTS in QGIS**: `QgsRasterLayer("type=xyz&url=https://host/{z}/{x}/{y}.png&zmax=9&zmin=0", name, "wms")`. WMTS: `QgsRasterLayer("crs=EPSG:3857&dpiMode=7&format=image/jpeg&layers=<id>&styles=default&tileMatrixSet=GoogleMapsCompatible_Level9&url=https://host/wmts.cgi?SERVICE%3DWMTS%26REQUEST%3DGetCapabilities", name, "wms")`.
- **GeoJSON**: `QgsVectorLayer(url_or_path, name, "ogr")` (OGR reads `https://` GeoJSON directly, or via `/vsicurl/`). For API JSON that is not GeoJSON, convert to features in Python and write to a GeoPackage/memory layer.
- **CSV with points**: `QgsVectorLayer("file:///path.csv?delimiter=,&xField=lon&yField=lat&crs=EPSG:4326", name, "delimitedtext")`.
- **Point cloud EPT**: `QgsPointCloudLayer("https://.../ept.json", name, "ept")`. Local COPC `.copc.laz` via provider `"copc"`.
- **3D Tiles**: QGIS 3.34+ reads Cesium 3D Tiles as a tiled-scene layer: `QgsTiledSceneLayer(uri, name, "cesiumtiles")`, uri = `https://.../tileset.json`. Tiled-scene layers render in the 3D map view.
- **STAC in QGIS**: no scripting-level STAC provider; do the search in Python (`requests`/`pystac-client`), then add assets as `/vsicurl/` COGs. (QGIS 3.40+ also has a GUI STAC browser connection.)
- **Parquet**: GDAL >= 3.5 reads Parquet when built with Arrow (`gdal.GetDriverByName("Parquet")`); recent QGIS builds expose it natively. Fallback: DuckDB in Python exporting to GeoPackage/GeoJSON.
- **Caching and politeness**: cache by full request URL with a TTL per source; retry with backoff on 429/5xx; never parallelise against Nominatim/Overpass.

---

## 1. STAC catalogues

### 1.1 Element84 Earth Search v1 [V]
- **Base URL**: `https://earth-search.aws.element84.com/v1` (`/collections`, `/collections/{id}`, `/search`, `/collections/{id}/items`, `/queryables`). Live and unchanged.
- **Auth**: none. Data buckets are public (S3 `us-west-2`; some are requester-pays: check item `storage:requester_pays`).
- **Search**: `POST /search` (JSON) or `GET /search?collections=...&bbox=...&datetime=...&limit=...`. Params: `collections` (list), `bbox` [w,s,e,n], `intersects` (GeoJSON), `datetime` (`start/end` RFC3339, open ends with `..`), `ids`, `limit` (max 1000), `query` (`{"eo:cloud_cover":{"lt":20}}`), `filter` + `filter-lang=cql2-json`, `sortby` (`[{"field":"properties.datetime","direction":"desc"}]`), `fields`, paging by following `links[rel=next]` (body contains a `token`).
- **Collections [V]**: `sentinel-2-l2a`, `sentinel-2-c1-l2a` (Collection 1 reprocessing; the newest items e.g. `S2C_T19TEN_20261030T153552_L2A` live here), `sentinel-2-pre-c1-l2a`, `sentinel-2-l1c`, `sentinel-1-grd`, `landsat-c2-l2`, `cop-dem-glo-30`, `cop-dem-glo-90`, `naip`. **There is no `nasadem` or `modis` collection on Earth Search** (use Planetary Computer).
- **Gotcha**: `sentinel-2-l2a` items point at the legacy `sentinel-cogs` bucket (e.g. `https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/43/S/CS/2026/10/S2A_43SCS_20261028_1_L2A/B04.tif`, https-readable). `sentinel-2-c1-l2a` items are in `e84-earth-search-sentinel-data`. Use each asset `href` as given; convert `s3://bucket/key` to `/vsis3/bucket/key`. Recommended default: `sentinel-2-c1-l2a`, fall back to `sentinel-2-l2a`.
- **Sentinel-2 L2A asset keys [V]**: `coastal`(B01) `blue`(B02) `green`(B03) `red`(B04) `rededge1`(B05) `rededge2`(B06) `rededge3`(B07) `nir`(B08) `nir08`(B8A) `nir09`(B09) `swir16`(B11) `swir22`(B12) `scl` `aot` `wvp` `cloud` `snow` `visual` (8-bit RGB TCI) `preview` `thumbnail` `granule_metadata` `tileinfo_metadata` `product_metadata`. SCL classes: 0 nodata, 3 cloud shadow, 6 water, 8/9 cloud medium/high, 10 thin cirrus, 11 snow/ice.
- **Sentinel-1 GRD keys [V]**: `hh`, `hv` (EW mode seen) or `vv`, `vh` (IW mode), plus `thumbnail`, `schema-*`, `safe-manifest`. Not terrain-corrected; for flood mapping prefer Planetary Computer `sentinel-1-rtc`.
- **Landsat C2 L2 keys [V]**: `coastal blue green red nir08 swir16 swir22 lwir11 qa_pixel qa_radsat qa_aerosol atran cdist drad urad trad emis emsd mtl.json mtl.txt mtl.xml ang thumbnail`. Scale: SR `DN*0.0000275 - 0.2`; ST (`lwir11`) `DN*0.00341802 + 149.0` K.
- **cop-dem-glo-30 [V]**: single asset `data` (COG), 1x1 degree tiles.
- **Response** (abridged):
```json
{"type":"FeatureCollection","features":[{"type":"Feature","stac_version":"1.0.0","id":"S2C_T19TEN_20261030T153552_L2A","collection":"sentinel-2-c1-l2a","bbox":[-70.9,44.0,-69.5,45.0],"properties":{"datetime":"2026-10-30T15:35:52Z","eo:cloud_cover":12.3,"proj:epsg":32619,"s2:tile_id":"..."},"assets":{"red":{"href":"https://.../B04.tif","type":"image/tiff; application=geotiff; profile=cloud-optimized"}}}],"links":[{"rel":"next","method":"POST","href":".../search","body":{"token":"next:..."}}],"numberReturned":1}
```
- **Rate limits**: none published (be reasonable). **Licence**: Sentinel/Copernicus free and open; Landsat public domain; Copernicus DEM free with attribution ("Produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2022 provided under COPERNICUS by the European Union and ESA; all rights reserved").
- **Load**: `QgsRasterLayer("/vsicurl/" + href, "S2 red", "gdal")`; multi-band composites via `gdal.BuildVRT(out, [...], separate=True)` or processing `gdal:buildvirtualraster`.

### 1.2 Microsoft Planetary Computer [V]
- **STAC base**: `https://planetarycomputer.microsoft.com/api/stac/v1` (`/collections`, `/search`). Anonymous search allowed.
- **Auth**: Azure Blob data needs a **SAS token**; anonymous tokens are free (rate-limited), a free subscription key raises limits (header `Ocp-Apim-Subscription-Key`).
- **Signing [V]**: `GET https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection_id}` returns `{"msft:expiry":"2026-10-30T19:47:49Z","token":"st=...&se=...&sp=rl&sv=...&sr=c&sig=..."}`; use `href + "?" + token`. Also `GET /api/sas/v1/sign?href=<blob url>` returns `{"href":"<signed>","msft:expiry":"..."}` [K]. Cache the token until `msft:expiry` (refresh about 5 min early).
- **Collections [V]**: `sentinel-2-l2a`, `sentinel-1-grd`, `sentinel-1-rtc`, `landsat-c2-l2`, `landsat-c2-l1`, `cop-dem-glo-30`, `cop-dem-glo-90`, `nasadem`, `alos-dem`, `esa-worldcover`, `io-lulc-annual-v02`, `jrc-gsw`, `ms-buildings`, `esa-cci-lc`, `sentinel-5p-l2-netcdf`, `sentinel-3-*`; MODIS: `modis-13A1-061` (500 m 16-day VI), `modis-13Q1-061` (250 m), `modis-11A1-061` (daily LST 1 km), `modis-11A2-061`, `modis-09A1-061`, `modis-10A1-061` (snow), `modis-14A1-061` (fire), `modis-15A2H-061` (LAI), `modis-17A2H-061` (GPP), `modis-43A4-061` (NBAR).
- **Asset keys [V]**: Sentinel-2 L2A uses band-number keys: `B01 B02 B03 B04 B05 B06 B07 B08 B8A B09 B11 B12 AOT SCL WVP visual` (+ `rendered_preview`, `tilejson`); not the same as Earth Search. Sentinel-1 RTC: `vv`, `vh`. Landsat: `red green blue nir08 swir16 swir22 lwir11 qa_pixel qa_radsat ...`. NASADEM: `elevation`. MODIS 13A1: `500m_16_days_NDVI`, `500m_16_days_EVI`, `500m_16_days_pixel_reliability`, plus `hdf`. cop-dem: `data`. ms-buildings and esa-worldcover: `data`.
- **Search params**: standard STAC (`collections`, `bbox`, `datetime`, `query`, `sortby`, `limit` max 1000), CQL2 supported.
- **Rate limits**: anonymous tier throttled, exact numbers UNVERIFIED. **Licence**: per collection (Sentinel open; Landsat PD; NASADEM/MODIS NASA open; WorldCover CC BY 4.0; MS Buildings ODbL).
- **Load**: `/vsicurl/<href>?<sas_token>` works in GDAL (query string stays in the path).
- **Optional tile service [K]**: `https://planetarycomputer.microsoft.com/api/data/v1/item/tiles/WebMercatorQuad/{z}/{x}/{y}?collection=sentinel-2-l2a&item=<id>&assets=visual&asset_bidx=visual|1,2,3` serves XYZ tiles without local signing.

### 1.3 Copernicus Data Space Ecosystem (CDSE) [V]
- **STAC**: `https://stac.dataspace.copernicus.eu/v1` (collections are paginated: `/collections?offset=10`). Standard mission ids include `sentinel-2-l2a` (confirmed [V]), `sentinel-1-grd`, `sentinel-3-*`, `sentinel-5p-*`, `sentinel-2-global-mosaics`; the first page [V] shows Copernicus Contributing Mission collections (`ccm-optical`, `ccm-sar`, `ccm-thermal-lst-hr`, ...) and `clms_*` products. Page through `/collections` at runtime instead of hardcoding. Item assets [V] have `s3://eodata/...` hrefs with keys like `B02_10m`, `B03_10m`, `B04_10m`, `B08_10m`, `SCL_20m`, `TCI_10m`, `AOT_10m`.
- **Asset access**: `s3://eodata/...` requires the CDSE S3 endpoint with free S3 keys (generate at `https://eodata-s3keysmanager.dataspace.copernicus.eu`). GDAL: `AWS_S3_ENDPOINT=eodata.dataspace.copernicus.eu`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_VIRTUAL_HOSTING=FALSE`, `AWS_HTTPS=YES`; path `/vsis3/eodata/Sentinel-2/MSI/L2A/...`.
- **OData catalogue [V]**: `GET https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter=Collection/Name eq 'SENTINEL-2' and OData.CSC.Intersects(area=geography'SRID=4326;POLYGON((...))') and ContentDate/Start gt 2026-10-01T00:00:00.000Z&$top=10&$orderby=ContentDate/Start desc`. Cloud cover: add `and Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' and att/OData.CSC.DoubleAttribute/Value lt 20)`. Response: `{"@odata.context":"$metadata#Products","value":[{"Id":"<uuid>","Name":"S2B_MSIL2A_...SAFE","S3Path":"/eodata/...","ContentDate":{"Start":"...","End":"..."},"GeoFootprint":{},"Online":true,"ContentLength":0}]}`.
- **Download [K]**: `GET https://download.dataspace.copernicus.eu/odata/v1/Products(<Id>)/$value` with `Authorization: Bearer <token>` (redirects; re-send the header; handling UNVERIFIED).
- **OAuth token flow [endpoint V]**: `POST https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token`, form body `client_id=cdse-public&grant_type=password&username=<email>&password=<pw>` -> `{"access_token":"...","expires_in":600,"refresh_token":"...","refresh_expires_in":3600,"token_type":"Bearer"}`. Refresh with `grant_type=refresh_token&refresh_token=...&client_id=cdse-public`. For unattended use create an OAuth client (client_credentials) in the CDSE dashboard. Read credentials from env vars or the QGIS auth DB; never persist the password.
- **Other [K]**: Sentinel Hub Process API `https://sh.dataspace.copernicus.eu/api/v1/process` and WMS/WMTS `https://sh.dataspace.copernicus.eu/ogc/wms/<instance-id>` (instance configured in the dashboard, free quotas).
- **Limits**: STAC search open without auth; download concurrency and quota limits exist (exact numbers UNVERIFIED). **Licence**: free, full and open (Copernicus Sentinel data terms).

---

## 2. NASA imagery and fire

### 2.1 GIBS [V capabilities]
- **WMTS EPSG:3857**: `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/wmts.cgi` (GetCapabilities 200 [V]). Also `/epsg4326/best/`, `/epsg3413/` (Arctic), `/epsg3031/` (Antarctic); `/std/` and `/nrt/` variants.
- **REST tile template**: `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{LayerId}/default/{Time}/{TileMatrixSet}/{z}/{y}/{x}.{ext}` (**order is TileMatrix/TileRow/TileCol, i.e. z/y/x**). `Time` = `YYYY-MM-DD`; static layers omit the time segment (`default/{TileMatrixSet}/...`).
- **Common layers (TMS, ext)** [K; confirm in GetCapabilities at runtime]: `MODIS_Terra_CorrectedReflectance_TrueColor` (GoogleMapsCompatible_Level9, jpg), `MODIS_Aqua_CorrectedReflectance_TrueColor` (L9, jpg), `VIIRS_SNPP_CorrectedReflectance_TrueColor` (L9, jpg), `VIIRS_NOAA20_CorrectedReflectance_TrueColor` (L9, jpg), `VIIRS_SNPP_DayNightBand_At_Sensor_Radiance` (L8, png), `VIIRS_Black_Marble` (L8, png), `MODIS_Terra_NDVI_8Day` (L9, png), `MODIS_Terra_Land_Surface_Temp_Day` (L7, png), `BlueMarble_ShadedRelief_Bathymetry` (L8, jpg), `GHRSST_L4_MUR_Sea_Surface_Temperature` (L7, png), `VIIRS_SNPP_Thermal_Anomalies_375m_All` (L8, png), `IMERG_Precipitation_Rate` (L6, png).
- **QGIS XYZ**: `type=xyz&url=https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/2026-10-30/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg&zmax=9&zmin=0` (QGIS substitutes `{z}`,`{x}`,`{y}` by name, so GIBS order is fine).
- **WMS**: `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS=MODIS_Terra_CorrectedReflectance_TrueColor&CRS=EPSG:4326&BBOX=<s,w,n,e>&WIDTH=1024&HEIGHT=512&FORMAT=image/jpeg&TIME=2026-10-30` (1.3.0 with EPSG:4326 uses lat,lon axis order) [K].
- **Auth**: none. **Limits**: not stated, be modest. **Licence**: NASA open data; credit "NASA EOSDIS GIBS".
- **Time series**: loop `TIME`; for QGIS temporal controller set fixed temporal ranges per layer or use WMS-T.

### 2.2 NASA FIRMS (active fire) [partially V]
- **Auth**: free MAP_KEY (`https://firms.modaps.eosdis.nasa.gov/api/map_key/`); invalid key returns `Invalid MAP_KEY.` [V]. Limit: 5000 transactions per 10 minutes per key [K].
- **Area CSV**: `GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{AREA}/{DAY_RANGE}[/{DATE}]`. `SOURCE`: `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`, `MODIS_NRT`, `VIIRS_SNPP_SP`, `LANDSAT_NRT` (US/Canada). `AREA`: `world` or `west,south,east,north`. `DAY_RANGE` 1-5. Also `/api/country/csv/{KEY}/{SOURCE}/{ISO3}/{days}` and `/api/data_availability/csv/{KEY}/ALL`.
- **Response** (CSV): `latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight` (VIIRS; confidence l/n/h). Load with delimitedtext xField=longitude, yField=latitude.
- **WMS/WFS [K]**: `https://firms.modaps.eosdis.nasa.gov/mapserver/wms/fires/{MAP_KEY}/`. **Licence**: NASA open data.

---

## 3. Weather and climate

### 3.1 Open-Meteo [V all five endpoints returned data]
- **No key** for non-commercial use (fair use: < 10 000 calls/day, 5 000/hour, 600/min [K]); commercial needs an API key and `customer-` subdomain. Licence CC BY 4.0 (attribute Open-Meteo.com).
- **Common params**: `latitude`, `longitude` (comma lists for multi-point), `hourly=` / `daily=` / `current=`, `timezone` (`auto`), `start_date`/`end_date`, `forecast_days` (0-16), `past_days`, `temperature_unit`, `wind_speed_unit`, `precipitation_unit`, `models`, `format=json|csv|flatbuffers`, `elevation`, `cell_selection`.
- **Endpoints**:
  - Forecast `https://api.open-meteo.com/v1/forecast`. Hourly vars: `temperature_2m, relative_humidity_2m, dew_point_2m, apparent_temperature, precipitation, rain, showers, snowfall, snow_depth, weather_code, pressure_msl, surface_pressure, cloud_cover(_low/_mid/_high), visibility, et0_fao_evapotranspiration, vapour_pressure_deficit, wind_speed_10m/80m/120m/180m, wind_direction_10m, wind_gusts_10m, soil_temperature_0cm/6cm/18cm/54cm, soil_moisture_0_to_1cm..27_to_81cm, shortwave_radiation, direct_radiation, diffuse_radiation, direct_normal_irradiance, uv_index, is_day, cape, freezing_level_height`. Daily: `temperature_2m_max/min, sunrise, sunset, daylight_duration, precipitation_sum, precipitation_hours, precipitation_probability_max, wind_speed_10m_max, wind_gusts_10m_max, shortwave_radiation_sum, et0_fao_evapotranspiration, uv_index_max`.
  - Historical (ERA5/ERA5-Land, 1940-) `https://archive-api.open-meteo.com/v1/archive` (same variables, `start_date`/`end_date` required) [V].
  - Air quality `https://air-quality-api.open-meteo.com/v1/air-quality` hourly `pm10, pm2_5, carbon_monoxide, nitrogen_dioxide, sulphur_dioxide, ozone, aerosol_optical_depth, dust, uv_index, european_aqi, us_aqi`, pollen vars (Europe) [V].
  - Marine `https://marine-api.open-meteo.com/v1/marine` hourly `wave_height, wave_direction, wave_period, wind_wave_height, swell_wave_height, swell_wave_period, ocean_current_velocity, sea_level_height_msl, sea_surface_temperature` [V].
  - Flood (GloFAS) `https://flood-api.open-meteo.com/v1/flood` daily `river_discharge, river_discharge_mean/median/max/min/p25/p75`, `ensemble=true`, `past_days`, `forecast_days` [V].
  - Others [K]: `https://climate-api.open-meteo.com/v1/climate` (CMIP6), `https://geocoding-api.open-meteo.com/v1/search?name=Berlin&count=5` (returns `results[{id,name,latitude,longitude,country,admin1,elevation,timezone}]`), `https://ensemble-api.open-meteo.com/v1/ensemble`.
- **Response**:
```json
{"latitude":52.0,"longitude":13.0,"elevation":40.0,"timezone":"GMT","hourly_units":{"time":"iso8601","temperature_2m":"°C"},"hourly":{"time":["2026-10-31T00:00","..."],"temperature_2m":[7.1,6.8]}}
```
- **QGIS**: multi-point request builds a point layer per timestamp; for a series use `format=csv` and delimitedtext with a datetime field + temporal controller.

### 3.2 NASA POWER [V]
- `GET https://power.larc.nasa.gov/api/temporal/{hourly|daily|monthly|climatology}/point?parameters=T2M,PRECTOTCORR,ALLSKY_SFC_SW_DWN&community=AG|RE|SB&longitude=..&latitude=..&start=YYYYMMDD&end=YYYYMMDD&format=JSON|CSV|GEOJSON`. Regional: `/api/temporal/daily/regional?...&latitude-min=&latitude-max=&longitude-min=&longitude-max=` (max 10x10 degrees). No key; fair use (per-IP limits unpublished: UNVERIFIED). Licence NASA open. Response is a GeoJSON Feature: `properties.parameter.T2M = {"20240101": 2.3, ...}`; missing = `-999`. Grid cell is 0.5 x 0.625 degrees (MERRA-2).

### 3.3 CHIRPS [partially V]
- Root `https://data.chc.ucsb.edu/products/`. **Old v2.0 paths (`CHIRPS-2.0/global_daily/...`) return 404 [V]**. **CHIRPS v3.0 [V 200]**: daily `https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/sat/{YYYY}/chirps-v3.0.sat.{YYYY}.{MM}.{DD}.tif` (0.05 deg, global) and `.../daily/final/rnl/` (ERA5-based variant, directory listing 200). Monthly and prelim paths UNVERIFIED. No key. Licence: open/public (credit CHC UCSB). Load `/vsicurl/<tif>` (nodata -9999).

### 3.4 ERA5 via CDS API [K, UNVERIFIED today]
- Auth: free ECMWF account, personal access token. Since 2024 the CDS is at `https://cds.climate.copernicus.eu/api` (legacy `/api/v2`, `uid:key` retired). `~/.cdsapirc`: `url: https://cds.climate.copernicus.eu/api` and `key: <PAT>`; licences must be accepted per dataset on the website.
- Python: `cdsapi.Client().retrieve("reanalysis-era5-single-levels", {"product_type":["reanalysis"],"variable":["2m_temperature"],"year":["2024"],"month":["01"],"day":["01"],"time":["12:00"],"data_format":"netcdf","download_format":"unarchived","area":[N,W,S,E]}, "out.nc")`. Datasets: `reanalysis-era5-single-levels`, `reanalysis-era5-land`, `reanalysis-era5-pressure-levels`. Jobs queue (minutes to hours). Licence Copernicus (free, attribution). Load NetCDF via GDAL `NETCDF:"out.nc":t2m` or `QgsMeshLayer` (provider `mdal`). For quick needs prefer Open-Meteo archive.

### 3.5 Meteostat [K; partly V]
- `https://api.meteostat.net/v2/` now returns 403 [V]. Current API is RapidAPI-hosted: `https://meteostat.p.rapidapi.com/point/{hourly|daily|monthly}?lat=&lon=&start=&end=` with `x-rapidapi-key` and `x-rapidapi-host: meteostat.p.rapidapi.com` (key error seen [V]); also `/stations/nearby`, `/stations/meta`. Free tier roughly 500 calls/month [K]. Data licence CC BY-NC 4.0. **Recommend deprioritising**; use Open-Meteo archive. (The `dev.meteostat.net/api/` doc path returned 404, so docs have moved.)

---

## 4. Sensors and real-time

### 4.1 OGC SensorThings API (FROST) [partially V]
- Base path `/v1.1` (or `/v1.0`). Entities: `Things`, `Locations`, `Datastreams`, `Observations`, `ObservedProperties`, `Sensors`, `FeaturesOfInterest`. OData query: `$top`, `$filter`, `$expand`, `$select`, `$orderby=phenomenonTime desc`, `$count=true`, spatial `$filter=st_within(Locations/location, geography'POLYGON((...))')`. Follow `@iot.nextLink`.
- Public servers: Hamburg `https://iot.hamburg.de/v1.1` [V 200]; Fraunhofer IOSB `https://airquality-frost.k8s.ilt-dmz.iosb.fraunhofer.de/v1.1` and Polish geoportal `https://sensorthings.geoportal.gov.pl` did not respond from this network (UNVERIFIED). Treat the server URL as a user parameter and test with `GET {base}/Things?$top=1`.
- Response: `{"@iot.count":N,"value":[{"@iot.id":1,"name":"...","properties":{},"@iot.selfLink":"..."}],"@iot.nextLink":"..."}`. Observation: `{"phenomenonTime":"...","result":12.3,"resultTime":"..."}`. Location is GeoJSON in `Locations[].location`.
- Load: convert Things+Locations to a point layer; latest value via `Datastreams?$expand=Observations($top=1;$orderby=phenomenonTime desc)`.

### 4.2 OpenAQ v3 [V requires key]
- `https://api.openaq.org/v3/`; **free API key** in header `X-API-Key` (401 otherwise [V]). v1/v2 retired. Endpoints: `/locations?bbox=minx,miny,maxx,maxy` (or `coordinates=lat,lon&radius=m`, `parameters_id=2` for PM2.5, `limit` up to 1000, `page`), `/locations/{id}/latest`, `/sensors/{id}/measurements?datetime_from=&datetime_to=`, `/parameters`, `/countries`. Rate about 60/min and 2000/hour per key [K].
- Response: `{"meta":{"found":123,"limit":100,"page":1},"results":[{"id":8118,"name":"...","coordinates":{"latitude":0,"longitude":0},"sensors":[{"id":1,"parameter":{"name":"pm25","units":"µg/m³"}}],"datetimeLast":{"utc":"..."}}]}`. Licence per source, mostly CC BY 4.0.

### 4.3 Sensor.Community [V]
- No key. All current data `https://data.sensor.community/static/v2/data.json` (very large), `data.1h.json`, `data.24h.json` (5-minute and 24h averages); filtered `https://data.sensor.community/airrohr/v1/filter/box=lat1,lon1,lat2,lon2`, `area=lat,lon,distance_km`, `type=SDS011` [K]. Response `[{"id":31208466643,"timestamp":"2026-10-30 19:00:03","location":{"latitude":"48.53","longitude":"..."},"sensor":{"sensor_type":{"name":"SDS011"}},"sensordatavalues":[{"value_type":"P1","value":"4.0"}]}]` (lat/lon are strings). Licence ODbL. Archive CSVs `https://archive.sensor.community/`. Do not poll faster than every 5 minutes.

### 4.4 USGS NWIS instantaneous values [V]
- `https://waterservices.usgs.gov/nwis/iv/?format=json&sites=01646500&parameterCd=00060,00065&period=P1D`; also `&bBox=west,south,east,north` (<= 25 sq. degrees), `&stateCd=VA`, `startDT/endDT`, `siteStatus=active`. `parameterCd`: 00060 discharge (ft3/s), 00065 gage height (ft), 00010 water temp. Site metadata `.../nwis/site/?format=rdb&bBox=...&siteType=ST`. Response is WaterML-JSON: `value.timeSeries[].sourceInfo.geoLocation.geogLocation{latitude,longitude}`, `values[0].value[{value,dateTime}]`, `variable.noDataValue=-999999`. No key. Licence: US public domain. USGS is migrating to `https://api.waterdata.usgs.gov/` OGC APIs; legacy `waterservices` is being phased out (timeline UNVERIFIED).

### 4.5 NDBC buoys [V]
- Latest observations, all stations: `https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt` (fixed-width text; header `#STN LAT LON YYYY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES PTDY ATMP WTMP DEWP VIS TIDE`; `MM` = missing). Per-station `https://www.ndbc.noaa.gov/data/realtime2/{STATION}.txt`. Stations `https://www.ndbc.noaa.gov/activestations.xml` [K]. Public domain. Parse by whitespace split, skip `#` lines, build points.

### 4.6 OpenSky Network [V]
- `GET https://opensky-network.org/api/states/all?lamin=45&lomin=5&lamax=47&lomax=7` works anonymously [V]. Credits: anonymous 400/day, registered 4000/day, 10 s time resolution anonymously; authenticated via OAuth2 client credentials [K]. Response `{"time":1793386854,"states":[["icao24","callsign","origin_country",time_position,last_contact,lon,lat,baro_altitude,on_ground,velocity,true_track,vertical_rate,sensors,geo_altitude,squawk,spi,position_source]]}`. Other: `/api/flights/aircraft?icao24=&begin=&end=`, `/api/tracks`. Licence: non-commercial research use; attribute OpenSky. Poll no faster than 10 s.

### 4.7 USGS earthquake GeoJSON [V]
- Feeds `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{all|significant|4.5|2.5|1.0}_{hour|day|week|month}.geojson` (updated every minute). Query API `https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime=&endtime=&minmagnitude=5&minlatitude=&maxlatitude=&minlongitude=&maxlongitude=&limit=` (max 20000 rows; `orderby=time|magnitude`; `format=csv`). Feature: `{"properties":{"mag":4.5,"place":"...","time":1793386000000,"tsunami":0,"sig":312,"url":"...","magType":"mb"},"geometry":{"type":"Point","coordinates":[lon,lat,depth_km]},"id":"us..."}`. Public domain. Load directly via `QgsVectorLayer("/vsicurl/<feed>.geojson", ..., "ogr")`.

### 4.8 GDACS [V]
- **Event list JSON [V]**: `https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?eventlist=EQ;TC;FL;VO;DR;WF&fromdate=2026-10-01&todate=2026-10-30&alertlevel=Green;Orange;Red` returns a GeoJSON FeatureCollection with `properties{eventtype,eventid,episodeid,name,alertlevel,alertscore,country,fromdate,todate,severitydata{severity,severitytext,severityunit},url{geometry,report,details}}`. Host `api.gdacs.org` returned 404; use `www.gdacs.org` [V]. RSS `https://www.gdacs.org/xml/rss.xml` (GeoRSS) [V]; `rss_7d.xml`, `rss_24h.xml` [K]. No key; attribute GDACS (EC JRC / UN OCHA).

---

## 5. Elevation, 3D, buildings

### 5.1 OpenTopography [V needs key]
- **Free API key** required (401 "API Key required" [V]). Global DEM API: `GET https://portal.opentopography.org/API/globaldem?demtype=COP30&south=&north=&west=&east=&outputFormat=GTiff&API_Key=KEY`. `demtype` [K]: `SRTMGL3`, `SRTMGL1`, `SRTMGL1_E`, `AW3D30`, `AW3D30_E`, `SRTM15Plus`, `NASADEM`, `COP30`, `COP90`, `EU_DTM`, `GEDI_L3`. `outputFormat` GTiff|AAIGrid|HFA. Area caps per DEM (e.g. ~450 000 km2 for COP30/SRTM; smaller for AW3D30) [K]. Rate limits (about 500/day academic, 50/day otherwise) UNVERIFIED.
- Catalog [V]: `https://portal.opentopography.org/API/otCatalog?productFormat=PointCloud&minx=&miny=&maxx=&maxy=&detail=false&outputFormat=json&include_federated=true` returns `{"Datasets":[{"Dataset":{"name":"...","identifier":{"propertyID":"opentopoID","value":"OTLAS.042020.6339.2"}}}]}`. Raster-from-lidar: `/API/usgsdem` [K]. Licence per dataset.
- Load: download GTiff to cache dir, `QgsRasterLayer(path)`; then `native:hillshade`, `gdal:contour`.

### 5.2 USGS 3DEP Entwine EPT [V]
- Bucket `usgs-lidar-public` (S3 `us-west-2`, public; listing works [V], host `https://usgs-lidar-public.s3.amazonaws.com`). Each project is a prefix with `ept.json`, e.g. `https://s3-us-west-2.amazonaws.com/usgs-lidar-public/AK_BrooksCamp_2012/ept.json` (URL taken from the index [V]). **No root `ept.json` [V 404]**; old `usgs.entwine.io` is dead [V 404]; guessed project names return 404.
- **Index [V]**: `https://raw.githubusercontent.com/hobuinc/usgs-lidar/master/boundaries/resources.geojson` (features with `properties{name,id,count,url}` and MultiPolygon footprints). Workflow: load index, intersect with bbox, pick project(s), `QgsPointCloudLayer(url, name, "ept")`.
- Public domain (USGS). Counts reach billions: stream as a layer, never download.

### 5.3 Overture Maps [V layout]
- Bucket `s3://overturemaps-us-west-2/release/{RELEASE}/theme={theme}/type={type}/*.parquet` (public; HTTP `https://overturemaps-us-west-2.s3.amazonaws.com/...`). Releases listed [V]: `2026-09-23.0`, `2026-09-23.1`, `2026-10-21.0` (newest). Themes [V]: `addresses, base, buildings, divisions, places, transportation`. Buildings types [V]: `building`, `building_part`. Files named `part-00000-<uuid>-c000.zstd.parquet` (~530 MB each). Discover the latest release by listing `?list-type=2&prefix=release/&delimiter=/`; old releases are retired after about 60 days.
- **Query by bbox**: GeoParquet carries a `bbox` struct column (`bbox.xmin/xmax/ymin/ymax`) enabling row-group pushdown. DuckDB:
```sql
INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';
COPY (SELECT id, names.primary AS name, height, num_floors, class, geometry
      FROM read_parquet('s3://overturemaps-us-west-2/release/2026-10-21.0/theme=buildings/type=building/*', hive_partitioning=1)
      WHERE bbox.xmin > 73.0 AND bbox.xmax < 73.2 AND bbox.ymin > 33.5 AND bbox.ymax < 33.7)
TO 'buildings.gpkg' WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326');
```
  Or the CLI (`pip install overturemaps`): `overturemaps download --bbox=w,s,e,n -f geoparquet|geojson|geojsonseq -t building -o out.geojson`.
- Licence: mixed per theme (buildings and places CDLA Permissive 2.0 / ODbL by source, transportation/base ODbL where OSM-derived); per-feature `sources` field; attribution required.

### 5.4 Google Open Buildings [UNVERIFIED]
- v3 polygons published at `https://sites.research.google/gr/open-buildings/`; data in S2-level-4 cell CSV.gz tiles on Google Cloud Storage (`gs://open-buildings-data/v3/polygons_s2_level_4_gzip/{cell}_buildings.csv.gz`; the HTTPS directory probe returned 404, which is expected for a prefix; individual object URLs not tested). CSV columns: WKT `geometry`, `confidence`, `area_in_meters`, `full_plus_code`. A community GeoParquet mirror (VIDA, Source Cooperative "google-microsoft-osm-open-buildings") exists; path UNVERIFIED. Licence CC BY 4.0 (ODbL for the combined Microsoft/OSM product).

### 5.5 Microsoft Global Building Footprints [V link index]
- Index CSV [V 200]: `https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv` (columns `Location,QuadKey,Url,Size`; each Url is a gzipped line-delimited GeoJSON tile keyed by zoom-9 quadkey). Compute quadkeys for the bbox (`mercantile`), download matching `Url`s, read with OGR via `/vsigzip/`. Also Planetary Computer `ms-buildings` (GeoParquet via STAC) [V]. Licence ODbL.

### 5.6 Cesium ion 3D Tiles [K, UNVERIFIED today]
- Auth: free ion account + access token. `GET https://api.cesium.com/v1/assets/{id}/endpoint` with `Authorization: Bearer <token>` returns `{"type":"3DTILES","url":"https://assets.ion.cesium.com/<id>/tileset.json","accessToken":"<short token>"}`. Asset IDs [K]: Cesium World Terrain `1`, Cesium OSM Buildings `96188`, Google Photorealistic 3D Tiles `2275207` (Google terms; use inside QGIS is restricted/UNVERIFIED). Free tier has monthly streaming quotas.
- QGIS: tiled-scene provider `cesiumtiles` with the tileset URL and access token; QGIS also has a Cesium ion data-source integration (3.38+) using an authentication configuration [K].

### 5.7 OSM Buildings
- Simplest: Overpass (`way["building"](bbox); out geom;`) or the Overture route. `data.osmbuildings.org` tiles are UNVERIFIED and meant for visualisation only. Cesium OSM Buildings (5.6) is the 3D-tiles option. ODbL.

---

## 6. Land, agriculture, population

### 6.1 ESA WorldCover [V]
- Bucket `s3://esa-worldcover/` (region `eu-central-1`; HTTPS `https://esa-worldcover.s3.eu-central-1.amazonaws.com/`). Layout [V]: `v200/2021/map/ESA_WorldCover_10m_2021_v200_{TILE}_Map.tif` (TILE = 3x3 degree grid by SW corner, e.g. `N48E012`; COG-style, 200 OK), plus `v200/2021/macrotiles/`, `inputquality/`, `docs/`; v100 2020: `v100/2020/map/ESA_WorldCover_10m_2020_v100_{TILE}_Map.tif` [V]. Tile index `esa_worldcover_grid.geojson` at the bucket root [K]. Classes: 10 tree, 20 shrub, 30 grass, 40 crop, 50 built, 60 bare, 70 snow/ice, 80 water, 90 wetland, 95 mangrove, 100 moss/lichen. CC BY 4.0. Also on Planetary Computer (`esa-worldcover`).
- Load `/vsicurl/<url>`; style as paletted/unique values; stats via `native:zonalstatisticsfb`.

### 6.2 Dynamic World [K]
- Only via Google Earth Engine (`GOOGLE/DYNAMICWORLD/V1`, 10 m, near-real-time, 9 classes + probabilities); needs GEE auth (`ee.Authenticate`, Cloud project; non-commercial free). Not reachable with plain HTTP. Open alternatives: `io-lulc-annual-v02` on Planetary Computer [V], ESA WorldCover. CC BY 4.0.

### 6.3 SoilGrids [V REST]
- REST point query [V]: `https://rest.isric.org/soilgrids/v2.0/properties/query?lon=9&lat=46&property=clay&property=sand&depth=0-5cm&depth=5-15cm&value=mean`. Properties: `bdod cec cfvo clay nitrogen ocd ocs phh2o sand silt soc`; depths `0-5cm,5-15cm,15-30cm,30-60cm,60-100cm,100-200cm`; values `Q0.05,Q0.5,Q0.95,mean,uncertainty`. Response GeoJSON Point with `properties.layers[{name,unit_measure{d_factor,mapped_units},depths[{label,values{mean}}]}]` (divide by `d_factor`). Rate limit about 5 calls/min [K].
- **Maps [K]**: WCS `https://maps.isric.org/mapserv?map=/map/clay.map` (WCS 2.0.1, coverage ids like `clay_0-5cm_mean`); VRT/COG at `https://files.isric.org/soilgrids/latest/data/clay/clay_0-5cm_mean.vrt` (`/vsicurl/` works). Native 250 m in Homolosine (ESRI:54052); reproject with `gdal:warpreproject` or use WCS `SUBSETTINGCRS`. CC BY 4.0.

### 6.4 FAO WaPOR v3 [partially V]
- Catalog API [V]: `https://data.apps.fao.org/gismgr/api/v2/catalog/workspaces/WAPOR-3/mapsets` (JSON envelope `{"requestId","timestamp","status":200,"message":"OK","response":{...}}`, paginated). Rasters: `.../mapsets/{code}/rasters` [K]. Mapset codes [K]: `L1-AETI-D` (daily actual ET, 300 m global), `L2-AETI-D` (100 m), `L3-AETI-D` (20 m, selected areas), `L1-NPP-D`, `L1-RET-D`, `L1-PCP-D`, `L1-T-D`, `L1-E-D`, `L1-I-D`; confirm from the mapsets list. Raster items carry a `downloadUrl` to a COG GeoTIFF [K]. No key for reading; the `/wapor-3/` job/query endpoints need an API token (UNVERIFIED). CC BY 4.0. Load `downloadUrl` via `/vsicurl/`.

### 6.5 Global Forest Watch Data API [V reachable]
- `https://data-api.globalforestwatch.org/` (ReDoc docs [V]). Auth [K]: `POST /auth/token` (username/password) then `POST /auth/apikey` (`alias`, `email`, `organization`, `domains`); send `x-api-key`. Datasets [K]: `umd_tree_cover_loss`, `umd_glad_landsat_alerts`, `gfw_integrated_alerts`, `umd_tree_cover_density_2000`. Query: `POST /dataset/{dataset}/{version}/query/json` body `{"sql":"SELECT SUM(area__ha) FROM data WHERE umd_tree_cover_loss__year>=2020","geometry":{GeoJSON polygon}}`. Tile example (version UNVERIFIED): `https://tiles.globalforestwatch.org/umd_tree_cover_loss/v1.12/tcd_30/{z}/{x}/{y}.png`. Licence CC BY 4.0.

### 6.6 WorldPop [V]
- REST catalogue [V]: `https://hub.worldpop.org/rest/data/pop/wpgp?iso3=PAK` returns `{"data":[{"id":"1442","title":"The spatial distribution of population in 2000, Pakistan","desc":"...","files":[...]}]}`. File pattern [K]: `https://data.worldpop.org/GIS/Population/Global_2000_2020/{year}/{ISO3}/{iso3}_ppp_{year}.tif` (100 m); constrained `.../Global_2000_2020_Constrained/2020/maxar_v1/{ISO3}/{iso3}_ppp_2020_constrained.tif`; 1 km `.../Global_2000_2020_1km/`. A newer R2025A series likely exists (UNVERIFIED). Take real URLs from the `files` array. The `api.worldpop.org/v1/services` stats service answered with a PHP deprecation notice [V] (flaky). CC BY 4.0.

### 6.7 GHSL [V]
- JRC open data: `https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/` e.g. `GHS_POP_GLOBE_R2023A/GHS_POP_E2020_GLOBE_R2023A_4326_30ss/V1-0/GHS_POP_E2020_GLOBE_R2023A_4326_30ss_V1_0.zip` [V 200]. Products: `GHS_BUILT_S_*` (built-up surface), `GHS_BUILT_H_*` (height), `GHS_SMOD_*` (degree of urbanisation), `GHS_POP_*`. Global files are zip (tif inside reachable via `/vsizip//vsicurl/...`). Tiled downloads from `https://ghsl.jrc.ec.europa.eu/download.php` (tile ids `R{r}_C{c}`) [K]. Newer R2025A releases may exist (UNVERIFIED). CC BY 4.0. No streaming COG.

### 6.8 VIIRS nightlights [K, UNVERIFIED]
- NASA Black Marble (VNP46A2/A3) via LAADS DAAC needs an Earthdata bearer token (`https://ladsweb.modaps.eosdis.nasa.gov/archive/allData/5200/VNP46A2/`). Visual-only via GIBS `VIIRS_Black_Marble` (section 2.1). EOG annual VNL: `https://eogdata.mines.edu/products/vnl/` requires a free EOG account/token. Recommend GIBS for display and EOG for analysis.

---

## 7. Vector and reference

### 7.1 Overpass API [partially V]
- Public instances: `https://overpass-api.de/api/interpreter` (main; returned **502 "at capacity"** and 406 on bare probes today [V]; send a real User-Agent and `Accept: */*`, retry with backoff), `https://overpass.kumi.systems/api/interpreter` and `https://overpass.private.coffee/api/interpreter` (not reachable from this network: UNVERIFIED), `https://maps.mail.ru/osm/tools/overpass/api/interpreter` (504 today). Implement multi-instance failover. Status: `GET /api/status`.
- Call: `POST` with form field `data=<query>` (or `GET ?data=`). Query language:
```
[out:json][timeout:60][bbox:33.5,73.0,33.7,73.2];
(node["amenity"="hospital"];way["amenity"="hospital"];relation["amenity"="hospital"];);
out center tags;      // alternatives: out geom;  or  out body; >; out skel qt;
```
  Output `{"version":0.6,"generator":"Overpass API","osm3s":{"timestamp_osm_base":"..."},"elements":[{"type":"node","id":1,"lat":0,"lon":0,"tags":{}},{"type":"way","id":2,"center":{"lat":0,"lon":0},"tags":{}}]}`. bbox order is **south,west,north,east**. Area filter: `area["name"="Berlin"]["admin_level"=4]->.a; nwr(area.a)["amenity"="cafe"];`. Convert with `osm2geojson` (pip) or `[out:xml]` + OGR `osm` driver.
- **Fair use**: under about 10 000 queries/day and 1 GB/day per IP; honour `[timeout]`/`[maxsize]`; 429 means wait; no parallel requests. Licence ODbL (attribute "(c) OpenStreetMap contributors").

### 7.2 Nominatim [V status]
- `https://nominatim.openstreetmap.org/` (v5.3.0 [V]). Endpoints: `/search?q=...&format=jsonv2|geojson&limit=&countrycodes=&viewbox=&bounded=1&addressdetails=1&polygon_geojson=1`, `/reverse?lat=&lon=&format=jsonv2&zoom=`, `/lookup?osm_ids=R146656`, `/status.php`. **Usage policy**: absolute maximum 1 request/second, **valid identifying User-Agent or Referer required** (library defaults are blocked), no bulk geocoding, cache results, show attribution; heavy use means self-host or a commercial provider. Response (jsonv2): `[{"place_id":1,"osm_type":"relation","osm_id":1,"lat":"52.51","lon":"13.38","display_name":"Berlin, Germany","boundingbox":["52.3","52.6","13.0","13.7"],"category":"boundary","type":"administrative"}]` (bbox is S,N,W,E as strings).

### 7.3 GeoNames [K]
- `http://api.geonames.org/searchJSON?q=...&maxRows=&country=&username=USER` (free account; enable web services on the account; shared `demo` is unusable). Limits 20 000 credits/day, 1 000/hour for free. Others: `findNearbyJSON`, `findNearbyPlaceNameJSON`, `postalCodeSearchJSON`, `countryInfoJSON`, `hierarchyJSON`. Bulk dumps `https://download.geonames.org/export/dump/` (`cities15000.zip`, `allCountries.zip`, tab-separated). The host did not respond from this network (UNVERIFIED). CC BY 4.0.

### 7.4 geoBoundaries [V]
- `GET https://www.geoboundaries.org/api/current/gbOpen/{ISO3}/{ADM0..ADM5|ALL}/` [V] returns `{"boundaryID":"PAK-ADM1-70912109","boundaryName":"Pakistan","boundaryISO":"PAK","boundaryYearRepresented":"2019","boundaryType":"ADM1","boundaryLicense":"...","gjDownloadURL":"https://github.com/wmgeolab/geoBoundaries/raw/<hash>/releaseData/gbOpen/PAK/ADM1/geoBoundaries-PAK-ADM1.geojson","simplifiedGeometryGeoJSON":"...","tjDownloadURL":"..."}`. Variants `gbHumanitarian`, `gbAuthoritative`. Fetch `gjDownloadURL` (follow redirects). CC BY 4.0 (check `boundaryLicense`).

### 7.5 GADM [V]
- v4.1 per country: `https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{ISO3}_{level}.json` [V 200]; also `gpkg/gadm41_{ISO3}.gpkg` (all levels), `shp/`, world `gadm_410.gpkg` [K]. Levels 0..5. Academic/non-commercial use only; no redistribution without permission. No API.

### 7.6 HDX (CKAN) [V]
- `https://data.humdata.org/api/3/action/package_search?q=pakistan flood&fq=groups:pak&rows=10&start=0` [V]; `package_show?id=<name>`; `resource_show?id=<id>`. Response `{"success":true,"result":{"count":2901,"results":[{"name":"...","title":"...","license_id":"cc-by-igo","resources":[{"name":"...","format":"GeoJSON|SHP|CSV","download_url":"https://data.humdata.org/dataset/.../resource/.../download/..."}]}]}}`. No key to read. HDX HAPI (`https://hapi.humdata.org/api/v2/...`, needs `app_identifier`) UNVERIFIED. Licences vary: always read `license_id`. Rate limits unpublished. HXL tags appear in row 2 of many CSVs.

### 7.7 GBIF occurrences [V]
- `GET https://api.gbif.org/v1/occurrence/search?scientificName=Panthera%20leo&country=KE&hasCoordinate=true&hasGeospatialIssue=false&geometry=POLYGON((...))&year=2020,2024&limit=300&offset=0` [V]. `limit` max 300 and `offset+limit <= 100 000`; larger pulls use the async download API (`POST https://api.gbif.org/v1/occurrence/download/request`, basic auth with a GBIF account) [K]. Response `{"count":3887212831,"endOfRecords":false,"results":[{"key":182368733,"scientificName":"...","decimalLatitude":0,"decimalLongitude":0,"eventDate":"...","basisOfRecord":"HUMAN_OBSERVATION","datasetKey":"..."}]}`. Species match `/v1/species/match?name=`. Density tiles [K]: `https://api.gbif.org/v2/map/occurrence/density/{z}/{x}/{y}@1x.png?srs=EPSG:3857&taxonKey=`. Licences per record (CC0, CC BY, CC BY-NC); cite download DOIs.

### 7.8 iNaturalist [V]
- `GET https://api.inaturalist.org/v1/observations?taxon_id=&place_id=&nelat=&nelng=&swlat=&swlng=&quality_grade=research&d1=2026-01-01&per_page=200&page=1&geo=true` [V]. `per_page` max 200; paging capped near 10 000 results (use `id_above` for more) [K]. Response `{"total_results":397229349,"page":1,"per_page":1,"results":[{"id":1,"geojson":{"type":"Point","coordinates":[0,0]},"location":"lat,lon","observed_on":"...","taxon":{"name":"...","preferred_common_name":"..."},"quality_grade":"research","license_code":"cc-by-nc","obscured":false}]}`. Rate guidance about 60 req/min and under 10k/day; send a UA. Per-observation licence; obscured coordinates are fuzzed. Also `/v1/observations/species_counts`, `/v1/places/autocomplete`, `/v1/taxa/autocomplete`.

---

## 8. Marine and hydrology

### 8.1 Copernicus Marine Service [WMTS V; toolbox K]
- **Toolbox** (`pip install copernicusmarine`): `copernicusmarine.subset(dataset_id="cmems_mod_glo_phy_anfc_0.083deg_P1D-m", variables=["thetao"], minimum_longitude=..., maximum_longitude=..., minimum_latitude=..., maximum_latitude=..., start_datetime="2026-10-01", end_datetime="2026-10-07", minimum_depth=0, maximum_depth=1, output_filename="x.nc")`; `copernicusmarine.login()` once (free account; credentials stored under `~/.copernicusmarine/`); `describe()` lists products; `get()` downloads files. Dataset id is an example (UNVERIFIED; resolve with `describe(contains=[...])` at runtime). Open licence with attribution. Load NetCDF via MDAL mesh or GDAL `NETCDF:` subdatasets.
- **WMTS [V capabilities]**: `https://wmts.marine.copernicus.eu/teroWmts`. Layer pattern `{product}/{dataset}/{variable}`; no auth for WMTS.

### 8.2 EMODnet Bathymetry [V]
- WMS `https://ows.emodnet-bathymetry.eu/wms` [V GetCapabilities]; WCS `.../wcs` and WFS `.../wfs` [K]. Layers [K, confirm in capabilities]: `emodnet:mean_atlas_land`, `emodnet:mean_multicolour`, `emodnet:mean_rainbowcolour`, `emodnet:contours`, `emodnet:quality_index`, `emodnet:source_references`. QGIS: `QgsRasterLayer("crs=EPSG:4326&format=image/png&layers=emodnet:mean_atlas_land&styles&url=https://ows.emodnet-bathymetry.eu/wms", "EMODnet", "wms")`. Use WCS for real depth values. No key; CC BY 4.0.

### 8.3 GloFAS
- Easiest: Open-Meteo Flood API (3.1) [V], which serves GloFAS river discharge. Full grids: CDS datasets `cems-glofas-forecast` / `cems-glofas-historical` via `cdsapi`, or the EWDS portal (`https://ewds.climate.copernicus.eu/api`; recent host change, UNVERIFIED); needs a token and licence acceptance [K]. Copernicus/CEMS licence, attribution.

### 8.4 HydroSHEDS
- Product pages [V 200]: `https://www.hydrosheds.org/products/hydrorivers` and siblings (`hydrosheds`, `hydrobasins`, `hydrolakes`). Direct download URLs change (hosted under `https://data.hydrosheds.org/file/...`); exact paths UNVERIFIED, so resolve links from product pages. Shapefile zips load via `/vsizip/`. Licence: free for non-commercial and commercial use with attribution (verify on site). Google Earth Engine also hosts `WWF/HydroSHEDS/*`.

---

# Advanced feature ideas beyond data fetching

All use PyQGIS (`qgis.core`, `qgis.analysis`, `processing.run`) behind MCP tool calls. Section numbers refer to the sources above.

1. **Sentinel-2 change detection (before/after)**. Sources: Earth Search `sentinel-2-c1-l2a` (1.1), SCL mask. Steps: STAC-pick least-cloudy scenes per window, build `/vsicurl/` VRTs, mask via SCL (`native:rastercalculator`/`gdal:rastercalculator`), dNDVI/dNBR, threshold, `gdal:polygonize`, filter by area (`native:extractbyexpression`), style with `QgsSingleBandPseudoColorRenderer`. Output: change polygons plus area summary.
2. **Flood mapping with Sentinel-1**. Sources: PC `sentinel-1-rtc` (vv/vh), `jrc-gsw` permanent water, COP-DEM, WorldPop (5.5, 6.6). Steps: pre/post-event VV to dB, threshold (about -18 dB) or Otsu, speckle filter, subtract permanent water, drop steep slopes (`native:slope`), polygonize, intersect buildings/population (`native:zonalstatisticsfb`, `native:joinbylocationsummary`) for exposure counts.
3. **Urban heat island with Landsat thermal**. Sources: `landsat-c2-l2` `lwir11` + `qa_pixel` (1.1), boundaries (7.4/Overture divisions), WorldCover. Steps: scale to Kelvin/Celsius, QA cloud mask, summer median composite, zonal means per neighbourhood, correlate with NDVI/impervious fraction, rank hotspots (`qgis:heatmapkerneldensityestimation`), tree-planting priority map.
4. **Crop monitoring pipeline (NDVI time series per field)**. Sources: Sentinel-2 STAC, field polygons (user/OSM `landuse=farmland`/Overture), Open-Meteo archive, NASA POWER. Steps: biweekly cloud-masked NDVI composites, `native:zonalstatisticsfb` per field and date, anomaly vs multi-year mean, DataPlotly/`QgsVectorLayerTemporalProperties`, flag stressed fields, export GeoPackage, temporal animation.
5. **Wildfire detection, burn severity and exposure**. Sources: FIRMS (2.2), Sentinel-2 `nir08`/`swir22`, WorldCover, Overture buildings/places. Steps: ingest hotspots, cluster (`native:dbscanclustering`), hulls/buffers, dNBR severity classes, intersect assets, acreage and population report (WorldPop zonal sums).
6. **Real-time hazard alerting**. Sources: USGS feed (4.7), GDACS (4.8), Open-Meteo Flood, NWIS. Steps: poll on a schedule (`QTimer`/`QgsTask`), diff against stored event ids, filter to AOI (`native:extractbylocation`), notify (`QgsMessageBar`, optional webhook/email), auto-snapshot a map via `QgsPrintLayout` + `QgsLayoutExporter`.
7. **Automated map-report generation (PDF/atlas)**. Sources: any above plus admin boundaries (7.4/7.5/7.6). Steps: `QgsPrintLayout` with map, legend, scale bar, chart items; `QgsLayoutAtlas` over admin units; stats table from zonal stats; `QgsLayoutExporter.exportToPdf`; attribution lines generated from connector licence metadata.
8. **3D scene builder (terrain, buildings, point cloud)**. Sources: COP30 (1.1/5.1), Overture buildings with `height`/`num_floors` (5.3), USGS EPT (5.2), Cesium OSM Buildings (5.6), Sentinel-2 `visual` drape. Steps: `QgsRasterDemTerrainGenerator` via `QgsProject.elevationProperties`/`Qgs3DMapSettings`, `QgsVectorLayer3DRenderer` + `QgsPolygon3DSymbol` (extrusion), `QgsPointCloudLayer` EPT with 3D renderer, camera keyframes with `Qgs3DAnimationSettings`, export image/animation.
9. **Time-series animation export**. Sources: GIBS daily layers (2.1), Open-Meteo hourly grids (3.1), FIRMS dated hotspots (2.2). Steps: `QgsRasterLayerTemporalProperties`/`QgsVectorLayerTemporalProperties`, `QgsTemporalNavigationObject`, render frames with `QgsMapRendererParallelJob`, stitch to MP4/GIF with ffmpeg.
10. **Viewshed, line-of-sight and solar potential**. Sources: COP30/NASADEM, Overture buildings, Open-Meteo radiation, NASA POWER `ALLSKY_SFC_SW_DWN`. Steps: `gdal:viewshed`, `native:hillshade`/`native:aspect`/`native:slope` for roof and terrain suitability, zonal stats of irradiance per roof (kWh/m2).
11. **Watershed delineation and flood-risk screening**. Sources: COP30/HydroSHEDS, Open-Meteo Flood, NWIS gauges, WorldPop, Overture. Steps: `grass7:r.watershed`, `r.fill.dir`, `r.water.outlet` from a pour point, stream order, HAND, join gauge discharge percentiles, exposure counts.
12. **Air-quality interpolation and exposure map**. Sources: OpenAQ v3 (4.2), Sensor.Community (4.3), Open-Meteo air quality (3.1), WorldPop. Steps: QC outliers, `qgis:idwinterpolation`/`native:tininterpolation`, blend with Open-Meteo background, population-weighted exposure per admin unit, daily-mean time-lapse, WHO threshold alerts.
13. **Land-cover change and urban expansion tracking**. Sources: WorldCover 2020 vs 2021 (6.1), `io-lulc-annual-v02` (1.2), GHSL built-up (6.7), Sentinel-2. Steps: align rasters (`gdal:warpreproject`), `native:rasterlayeruniquevaluesreport` transition matrix, polygonize "to built" pixels, expansion rate per admin region, transition chart.
14. **Biodiversity hotspot / species-distribution screening**. Sources: GBIF (7.7), iNaturalist (7.8), WorldCover, SoilGrids (6.3), COP-DEM, Open-Meteo climate. Steps: clean records (`hasGeospatialIssue=false`, `native:removeduplicatesbyattribute`), `native:creategrid` hex richness + `native:countpointsinpolygon`, kernel density, `native:rastersampling` of environment layers, export MaxEnt-ready CSV.
15. **Drought and vegetation anomaly monitor**. Sources: CHIRPS v3 (3.3), MODIS NDVI/LST (`modis-13A1-061`, `modis-11A1-061`), Open-Meteo ET0, WaPOR AETI (6.4). Steps: SPI-style standardisation with `gdal:rastercalculator`, VCI/TCI from NDVI/LST min-max, composite drought index, district zonal stats (geoBoundaries ADM2), PDF bulletin.
16. **Coastal and marine conditions dashboard**. Sources: Open-Meteo Marine, NDBC (4.5), Copernicus Marine (8.1), EMODnet (8.2), GIBS GHRSST. Steps: live buoy point layer with wave-height styling, NetCDF currents as `QgsMeshLayer` with `QgsMeshRendererVectorSettings` arrows, bathymetry `gdal:contour`, nearshore wave-risk polygons.
17. **Accessibility and facility catchment analysis (isochrones)**. Sources: Overpass/Overture POIs and roads (7.1/5.3), WorldPop, Nominatim. Steps: `native:serviceareafromlayer` and `native:shortestpathpointtolayer` on a road graph, population covered per catchment (`native:zonalstatisticsfb`), site suggestion maximising uncovered population.
18. **Disaster damage assessment at building level**. Sources: Sentinel-1/2 pre/post, Overture/MS buildings, GDACS footprint, HDX UNOSAT layers (7.6), WorldPop. Steps: change raster, zonal stats over footprints, classify damaged/likely-damaged, per-ward summaries, atlas report.
19. **Provenance-aware analysis recipes**. Every connector call records source URL, parameters, licence and timestamp in project/layer metadata (`QgsProjectMetadata`, `QgsLayerMetadata`) enabling reproducible reports, automatic attribution lines, and a "re-run on newer data" tool (scheduled via `QgsTask`/OS scheduler).
20. **Elevation profile and corridor analysis**. Sources: COP30/3DEP, Overpass roads/pipelines, SoilGrids (6.3). Steps: `native:pointsalonglines` + `native:rastersampling` or the elevation profile API (`QgsProfileRequest`, 3.30+), slope-class summaries along corridors, landslide-prone segments from slope and clay content.

---

## Changes and caveats to flag for connector authors

- Earth Search now has **`sentinel-2-c1-l2a`** (Collection 1) beside legacy `sentinel-2-l2a`; neither `nasadem` nor `modis` exist there.
- Planetary Computer S2 asset keys are `B04` etc. (not `red`); MODIS collections are `modis-*-061`.
- CDSE STAC base is `stac.dataspace.copernicus.eu/v1`; assets are `s3://eodata/...` needing CDSE S3 keys.
- GDACS: use `www.gdacs.org` (`api.gdacs.org` returned 404).
- CHIRPS: v2.0 directory paths return 404; use `CHIRPS/v3.0/...`.
- Meteostat: `api.meteostat.net/v2` returns 403; now RapidAPI-based.
- OpenAQ: v3 only, `X-API-Key` mandatory.
- USGS 3DEP EPT: no root `ept.json`; use the hobuinc `resources.geojson` index. Overture releases rotate fast (latest seen `2026-10-21.0`); resolve at runtime.
- CDS (ERA5/GloFAS): new `cds.climate.copernicus.eu/api` and EWDS endpoints with personal access tokens (UNVERIFIED today).
- Overpass main instance was at capacity (502) during testing; implement failover and backoff.
- USGS waterservices is being migrated to `api.waterdata.usgs.gov` (timeline UNVERIFIED).
- Anything marked UNVERIFIED should be checked by a live test in each connector's own smoke test before shipping.
