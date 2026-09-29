"""Open-data enrichment for the DataCollectorAgent: geocoding via Nominatim and
POI density via the Overpass API (OpenStreetMap, ODbL). Rate limited and cached."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from agents.tools.files import _load_table
from agents.tools.registry import ToolContext, tool
from agents.tools.web import _get, _record_source

OSM_LICENSE = "ODbL 1.0 (© OpenStreetMap contributors)"


class GeocodeArgs(BaseModel):
    input_file: str = Field(description="workspace table with an address column")
    address_column: str
    output_file: str = Field("raw/geocoded.csv")
    country_codes: str = Field("", description="ISO codes to restrict results, e.g. 'ch' or 'de,at'")


@tool("geocode_addresses", "Geocode unique addresses with OpenStreetMap Nominatim (1 request/second, capped per run). "
      "Writes address, lat, lon, display_name to output_file.", GeocodeArgs, network=True)
def geocode_addresses(tc: ToolContext, a: GeocodeArgs) -> Any:
    store = tc.ctx.store
    df = _load_table(store.resolve(a.input_file, must_exist=True))
    if a.address_column not in df.columns:
        return f"column {a.address_column!r} not found; columns: {list(df.columns)[:40]}"
    cap = int(tc.ctx.cfg.get("web.max_geocode_rows", 400))
    addresses = df[a.address_column].dropna().astype(str).str.strip().unique().tolist()
    cache_rel = ".cache/geocode.json"
    cache = store.read_json(cache_rel, default={}) or {}
    base = tc.ctx.cfg.get("web.nominatim_url")
    todo = [x for x in addresses if x not in cache][:cap]
    for addr in todo:
        params = {"q": addr, "format": "jsonv2", "limit": 1}
        if a.country_codes:
            params["countrycodes"] = a.country_codes
        try:
            resp = _get(tc, f"{base}?{urlencode(params)}")
            hits = resp.json()
            cache[addr] = ({"lat": float(hits[0]["lat"]), "lon": float(hits[0]["lon"]),
                            "display_name": hits[0].get("display_name", "")} if hits else None)
        except Exception as exc:  # keep going; report at the end
            cache[addr] = {"error": str(exc)[:120]}
    store.write_json(cache_rel, cache)
    rows = [{"address": k, **(v or {})} for k, v in cache.items() if k in set(addresses)]
    out = pd.DataFrame(rows)
    store.write_bytes(a.output_file, out.to_csv(index=False).encode())
    _record_source(tc, a.output_file, {
        "url": base, "source_name": "OpenStreetMap Nominatim", "license": OSM_LICENSE,
        "license_url": "https://www.openstreetmap.org/copyright", "description": f"geocoded {a.address_column}",
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    found = int(out["lat"].notna().sum()) if "lat" in out else 0
    return {"saved": a.output_file, "unique_addresses": len(addresses), "geocoded": found,
            "skipped_due_to_cap": max(0, len(addresses) - len(cache))}


class PoiArgs(BaseModel):
    input_file: str
    lat_column: str
    lon_column: str
    key_column: str = Field("", description="column to carry over for joining (e.g. listing id)")
    categories: list[str] = Field(description="OSM tags like 'amenity=school', 'public_transport=station', "
                                              "'shop=supermarket', 'leisure=park'", max_length=8)
    radius_m: int = Field(500, ge=100, le=3000)
    output_file: str = Field("raw/poi_counts.csv")


def _haversine_counts(lat: np.ndarray, lon: np.ndarray, plat: np.ndarray, plon: np.ndarray, radius: float) -> np.ndarray:
    if len(plat) == 0:
        return np.zeros(len(lat), dtype=int)
    counts = np.zeros(len(lat), dtype=int)
    rl, rp = np.radians(lat)[:, None], np.radians(plat)[None, :]
    for start in range(0, len(lat), 500):
        sl = slice(start, start + 500)
        dlat = rp - rl[sl]
        dlon = np.radians(plon)[None, :] - np.radians(lon[sl])[:, None]
        h = np.sin(dlat / 2) ** 2 + np.cos(rl[sl]) * np.cos(rp) * np.sin(dlon / 2) ** 2
        dist = 2 * 6_371_000 * np.arcsin(np.sqrt(h))
        counts[sl] = (dist <= radius).sum(axis=1)
    return counts


@tool("osm_poi_counts", "Count OpenStreetMap points of interest within radius_m of each row's coordinates "
      "(one Overpass query per category over the bounding box).", PoiArgs, network=True)
def osm_poi_counts(tc: ToolContext, a: PoiArgs) -> Any:
    store = tc.ctx.store
    df = _load_table(store.resolve(a.input_file, must_exist=True))
    for col in (a.lat_column, a.lon_column):
        if col not in df.columns:
            return f"column {col!r} not found"
    pts = df[[c for c in (a.key_column, a.lat_column, a.lon_column) if c]].copy()
    pts[a.lat_column] = pd.to_numeric(pts[a.lat_column], errors="coerce")
    pts[a.lon_column] = pd.to_numeric(pts[a.lon_column], errors="coerce")
    pts = pts.dropna(subset=[a.lat_column, a.lon_column])
    if pts.empty:
        return "no valid coordinates"
    lat, lon = pts[a.lat_column].to_numpy(float), pts[a.lon_column].to_numpy(float)
    margin = a.radius_m / 111_000
    s, n = lat.min() - margin, lat.max() + margin
    w, e = lon.min() - margin / max(math.cos(math.radians(lat.mean())), 0.2), \
        lon.max() + margin / max(math.cos(math.radians(lat.mean())), 0.2)
    if (n - s) * (e - w) > 1.5:
        return "bounding box too large (> 1.5 deg²) — filter to one city/region first"
    base = tc.ctx.cfg.get("web.overpass_url")
    summary = {}
    for cat in a.categories:
        if "=" not in cat:
            summary[cat] = "skipped (use key=value)"
            continue
        k, v = cat.split("=", 1)
        query = f'[out:json][timeout:90];(node["{k}"="{v}"]({s},{w},{n},{e});way["{k}"="{v}"]({s},{w},{n},{e}););out center;'
        try:
            resp = _get(tc, f"{base}?{urlencode({'data': query})}")
            elements = resp.json().get("elements", [])
        except Exception as exc:
            summary[cat] = f"error: {str(exc)[:120]}"
            continue
        plat = np.array([el.get("lat", el.get("center", {}).get("lat")) for el in elements], dtype=float)
        plon = np.array([el.get("lon", el.get("center", {}).get("lon")) for el in elements], dtype=float)
        ok = ~(np.isnan(plat) | np.isnan(plon))
        col = f"poi_{v}_{a.radius_m}m".replace(":", "_")
        pts[col] = _haversine_counts(lat, lon, plat[ok], plon[ok], a.radius_m)
        summary[cat] = {"pois_in_bbox": int(ok.sum()), "column": col, "mean_count": float(pts[col].mean())}
    store.write_bytes(a.output_file, pts.to_csv(index=False).encode())
    _record_source(tc, a.output_file, {
        "url": base, "source_name": "OpenStreetMap Overpass API", "license": OSM_LICENSE,
        "license_url": "https://www.openstreetmap.org/copyright",
        "description": f"POI counts within {a.radius_m} m: {', '.join(a.categories)}",
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return {"saved": a.output_file, "rows": int(len(pts)), "categories": json.loads(json.dumps(summary))}
