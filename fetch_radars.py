#!/usr/bin/env python3
"""Fetch radar/warning POIs from NaviExpert traffic API for Poland."""

import json
import math
import time
import tomllib
import logging
from datetime import timedelta
from pathlib import Path
from collections import Counter
from typing import Any

import requests

type POI = dict[str, Any]
type POIKey = tuple[Any, float, float, float, float]
type Tile = dict[str, Any]
type BBox = dict[str, float]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.toml"

with open(CONFIG_PATH, "rb") as f:
    CONFIG: dict[str, Any] = tomllib.load(f)

HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Origin": "https://traffic.naviexpert.pl",
    "Referer": "https://traffic.naviexpert.pl/",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Leaflet / OSM slippy-map tile math
# ---------------------------------------------------------------------------


def lat_to_tile_y(lat_deg: float, zoom: int) -> int:
    """Convert latitude to tile Y coordinate (top edge)."""
    lat_rad = math.radians(lat_deg)
    n = 2 ** zoom
    return int(n * (1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2)


def lon_to_tile_x(lon_deg: float, zoom: int) -> int:
    """Convert longitude to tile X coordinate (left edge)."""
    n = 2 ** zoom
    return int(n * ((lon_deg + 180) / 360))


def tile_y_to_lat(y: int, zoom: int) -> float:
    """Convert tile Y coordinate back to latitude (NW corner)."""
    n = 2 ** zoom
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))


def tile_x_to_lon(x: int, zoom: int) -> float:
    """Convert tile X coordinate back to longitude (NW corner)."""
    n = 2 ** zoom
    return x / n * 360 - 180


def build_tiles(bbox: BBox, zoom: int, group_size: int) -> list[Tile]:
    """Build query bboxes by grouping Leaflet tiles into chunks."""
    # Find tile coordinate range covering the bbox
    x_min = lon_to_tile_x(bbox["west"], zoom)
    x_max = lon_to_tile_x(bbox["east"], zoom)
    y_min = lat_to_tile_y(bbox["north"], zoom)  # north = smaller y
    y_max = lat_to_tile_y(bbox["south"], zoom)  # south = larger y

    leaflet_tiles_x = x_max - x_min + 1
    leaflet_tiles_y = y_max - y_min + 1
    total_leaflet = leaflet_tiles_x * leaflet_tiles_y

    tiles: list[Tile] = []
    y = y_min
    while y <= y_max:
        x = x_min
        y_end = min(y + group_size, y_max + 1)
        while x <= x_max:
            x_end = min(x + group_size, x_max + 1)

            south = tile_y_to_lat(y_end, zoom)
            north = tile_y_to_lat(y, zoom)
            west = tile_x_to_lon(x, zoom)
            east = tile_x_to_lon(x_end, zoom)

            tiles.append(
                {
                    "leftCornerPoint": {"latitude": south, "longitude": west},
                    "rightCornerPoint": {"latitude": north, "longitude": east},
                    "zoomMeters": zoom,
                }
            )
            x = x_end
        y = y_end

    log.info(
        "Zoom %d: %d×%d = %d Leaflet tiles → %d requests (group %d×%d)",
        zoom, leaflet_tiles_x, leaflet_tiles_y, total_leaflet,
        len(tiles), group_size, group_size,
    )
    return tiles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def poi_key(poi: POI) -> POIKey:
    """Unique key for deduplication (position + type + direction)."""
    pos = poi.get("position", {})
    dir_ = poi.get("direction", {})
    return (
        poi.get("type"),
        round(pos.get("latitude", 0), 7),
        round(pos.get("longitude", 0), 7),
        round(dir_.get("latitude", 0), 7),
        round(dir_.get("longitude", 0), 7),
    )


def load_existing(path: Path) -> dict[POIKey, POI]:
    """Load previously saved POIs (if any) and return dict keyed by poi_key."""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data: list[POI] = json.load(f)
        return {poi_key(p): p for p in data}
    return {}


def save_pois(path: Path, pois_dict: dict[POIKey, POI]) -> None:
    """Write deduplicated POIs list to JSON, sorted for stable diffs."""
    sorted_pois = sorted(pois_dict.values(), key=lambda p: (
        p.get("type", 0),
        p.get("position", {}).get("latitude", 0),
        p.get("position", {}).get("longitude", 0),
    ))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted_pois, f, ensure_ascii=False, indent=2, sort_keys=True)


RETRY_DELAYS = [30, 120, 360, 360, 360]


def fetch_tile(session: requests.Session, tile: Tile, api_url: str, headers: dict[str, str]) -> list[POI]:
    """POST one tile and return the list of POIs (or empty list on error)."""
    for attempt in range(1 + len(RETRY_DELAYS)):
        try:
            resp = session.post(api_url, json=tile, headers=headers, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < len(RETRY_DELAYS):
                delay = RETRY_DELAYS[attempt]
                log.warning("Request failed (attempt %d/5): %s – retrying in %ds", attempt + 1, e, delay)
                time.sleep(delay)
            else:
                log.error("Request failed after 5 retries: %s", e)
                return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    log.info("=== Starting fetch session ===")

    fetch = CONFIG["fetch"]
    output_path = Path(fetch["output_file"])
    api_url: str = fetch["api_url"]
    headers = HEADERS
    delay: int = fetch["request_delay"]
    zoom: int = fetch["zoom_level"]
    group_size: int = fetch["tile_group_size"]

    previous = load_existing(output_path)
    prev_count = len(previous)
    log.info("Previously stored POIs: %d", prev_count)

    tiles = build_tiles(CONFIG["bbox"], zoom, group_size)
    total_tiles = len(tiles)

    current: dict[POIKey, POI] = {}
    session = requests.Session()
    elapsed_times: list[float] = []

    for idx, tile in enumerate(tiles, 1):
        t0 = time.monotonic()
        pois = fetch_tile(session, tile, api_url, headers)
        elapsed = time.monotonic() - t0
        elapsed_times.append(elapsed)

        new_in_tile = 0
        for p in pois:
            k = poi_key(p)
            if k not in current:
                new_in_tile += 1
            current[k] = p

        avg_time = sum(elapsed_times) / len(elapsed_times)
        remaining = total_tiles - idx
        eta_seconds = remaining * (avg_time + delay)
        eta = str(timedelta(seconds=int(eta_seconds)))

        log.info(
            "[%d/%d] Got %d POIs (%d new) | total unique so far: %d | ETA: %s",
            idx,
            total_tiles,
            len(pois),
            new_in_tile,
            len(current),
            eta,
        )

        if idx < total_tiles:
            time.sleep(delay)

    # ---- Compare with previous run ----
    prev_keys = set(previous.keys())
    curr_keys = set(current.keys())
    added = curr_keys - prev_keys
    removed = prev_keys - curr_keys

    # ---- Save ----
    save_pois(output_path, current)
    log.info("Saved %d unique POIs to %s", len(current), output_path)

    # ---- Summary ----
    type_counts = Counter(p.get("type") for p in current.values())
    icon_counts = Counter(p.get("iconId") for p in current.values())

    log.info("=== Session summary ===")
    log.info("Tiles fetched       : %d", total_tiles)
    log.info("Total unique POIs   : %d", len(current))
    log.info("Previously stored   : %d", prev_count)
    log.info("New (added)         : %d", len(added))
    log.info("Removed since last  : %d", len(removed))
    log.info("--- POI counts by type ---")
    for t, c in type_counts.most_common():
        log.info("  type %-4s : %d", t, c)
    log.info("--- POI counts by iconId ---")
    for icon, c in icon_counts.most_common():
        log.info("  icon %-6s : %d", icon, c)
    log.info("=== Session complete ===")


if __name__ == "__main__":
    main()
