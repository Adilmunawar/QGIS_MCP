# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adil Munawar. See LICENSE and NOTICE; keep this notice in copies.
# SPDX-License-Identifier: GPL-3.0-or-later
"""WorldPop population statistics for a bounding box (asynchronous task API).

Keyless. The server prefixes its JSON with PHP deprecation HTML, so replies
are parsed by scanning for the first brace that starts valid JSON; notices
may also trail the JSON.
"""

import json
import math
import time

from .. import net

STATS = "https://api.worldpop.org/v1/services/stats"
TASKS = "https://api.worldpop.org/v1/tasks/{taskid}"
SOURCE = "WorldPop (CC BY 4.0)"


def _parse(content):
    text = (content.decode("utf-8", "replace")
            if isinstance(content, bytes) else str(content))
    decoder = json.JSONDecoder()
    pos = text.find("{")
    while pos != -1:
        try:
            # raw_decode tolerates the PHP notices that follow the JSON too
            return decoder.raw_decode(text, pos)[0]
        except ValueError:
            pos = text.find("{", pos + 1)
    raise ValueError("WorldPop returned no JSON")


def _check_bbox(bbox):
    try:
        west, south, east, north = [float(v) for v in bbox]
    except (TypeError, ValueError):
        raise ValueError("bbox must be [west, south, east, north] in degrees")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox must satisfy west < east and south < north "
                         "within WGS84 limits")
    return west, south, east, north


def _area_km2(bbox):
    west, south, east, north = bbox
    mid = math.radians((south + north) / 2)
    return (north - south) * 111.32 * (east - west) * 111.32 * math.cos(mid)


def _geojson(bbox):
    w, s, e, n = bbox
    ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
    return json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [ring]}}]},
        separators=(",", ":"))


def _run(bbox, dataset, year, timeout_s):
    year = max(2000, min(int(year), 2020))
    params = {"dataset": dataset, "year": year, "geojson": _geojson(bbox)}
    _status, content = net.fetch(STATS, params, timeout_ms=60_000)
    created = _parse(content)
    taskid = created.get("taskid")
    if created.get("data") and str(created.get("status") or "").lower() == "finished":
        return created["data"], taskid, year  # some replies are synchronous
    if not taskid:
        raise ValueError("WorldPop did not return a task id: "
                         f"{created.get('error_message') or created}")
    deadline = time.time() + float(timeout_s)
    last_error = None
    while time.time() < deadline:
        remaining = max(5, int(deadline - time.time()))
        try:
            _status, content = net.fetch(TASKS.format(taskid=taskid),
                                         timeout_ms=min(60, remaining) * 1000)
            reply = _parse(content)
        except (net.HttpError, ValueError) as exc:  # one flaky poll must not abort the task
            last_error = str(exc)[:160]
            time.sleep(3)
            continue
        state = str(reply.get("status") or "").lower()
        if state == "finished":
            return reply.get("data") or {}, taskid, year
        if reply.get("error") or state == "failed":
            raise ValueError(f"WorldPop task {taskid} failed: "
                             f"{reply.get('error_message') or 'unknown error'}")
        time.sleep(3)
    raise TimeoutError(f"WorldPop task {taskid} did not finish within {timeout_s} seconds"
                       + (f" (last poll error: {last_error})" if last_error else "")
                       + "; try a smaller area")


def worldpop_total(bbox, year=2020, dataset="wpgppop", timeout_s=120):
    box = _check_bbox(bbox)
    data, taskid, year = _run(box, dataset, year, timeout_s)
    try:
        total = int(round(float(data.get("total_population"))))
    except (TypeError, ValueError):
        raise ValueError(f"WorldPop returned no total_population for task {taskid}")
    area = _area_km2(box)
    return {"total_population": total, "year": year, "dataset": dataset,
            "taskid": taskid, "bbox": list(box), "area_km2": round(area, 2),
            "density_per_km2": round(total / area, 1) if area > 0 else None,
            "source": SOURCE}


def _age_from(item):
    for key in ("age", "class"):
        try:
            return int(float(item.get(key)))
        except (TypeError, ValueError):
            continue
    return None


def worldpop_age_sex(bbox, year=2020, timeout_s=180):
    box = _check_bbox(bbox)
    data, taskid, year = _run(box, "wpgpas", year, timeout_s)
    rows = []
    for item in data.get("agesexpyramid") or []:
        if not isinstance(item, dict):
            continue
        age = _age_from(item)
        try:
            male, female = float(item.get("male")), float(item.get("female"))
        except (TypeError, ValueError):
            continue
        if age is not None:
            rows.append({"age_from": age, "male": round(male, 1),
                         "female": round(female, 1)})
    rows.sort(key=lambda r: r["age_from"])
    if not rows:
        raise ValueError(f"WorldPop returned no age and sex data for task {taskid}")

    def band(lo, hi):
        return round(sum(r["male"] + r["female"] for r in rows
                         if lo <= r["age_from"] < hi))

    male = sum(r["male"] for r in rows)
    female = sum(r["female"] for r in rows)
    return {"total": round(male + female), "male": round(male),
            "female": round(female), "children_under_15": band(0, 15),
            "working_age_15_64": band(15, 65),
            "elderly_65_plus": band(65, 1000), "rows": rows, "year": year,
            "taskid": taskid, "source": SOURCE}
