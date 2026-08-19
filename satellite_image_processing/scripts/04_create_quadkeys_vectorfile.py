"""
This script creates a shapefile with a tiling grid based on quadkeys/xyz location.
For a given bounding box it creates a single geojson file containing the outlines of all
xyz tiles for a given zoom level. All features contain the quadkey and the epsg code of
the local UTM grid.

Generated mostly AI tools.
"""

import argparse
import math
from pathlib import Path
from typing import Tuple

import geojson
from shapely.geometry import box, mapping
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_utm_crs_info


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> Tuple[int, int]:
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile


def tile_bounds(x: int, y: int, z: int) -> Tuple[float, float, float, float]:
    n = 2.0 ** z
    lon_min = x / n * 360.0 - 180.0
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon_min, lat_min, lon_max, lat_max


def tile_to_quadkey(x: int, y: int, z: int) -> str:
    quadkey = ""
    for i in range(z, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if (x & mask) != 0:
            digit += 1
        if (y & mask) != 0:
            digit += 2
        quadkey += str(digit)
    return quadkey


def find_utm_crs_by_latlon(lat: float, lon: float) -> str:
    # copied from https://pyproj4.github.io/pyproj/stable/examples.html#find-utm-crs-by-latitude-and-longitude
    utm_crs_list = query_utm_crs_info(
        datum_name="WGS 84",
        area_of_interest=AreaOfInterest(
            west_lon_degree=lon,
            south_lat_degree=lat,
            east_lon_degree=lon,
            north_lat_degree=lat,
        ),
    )
    utm_crs = utm_crs_list[0]
    utm_crs_str = f"{utm_crs.auth_name}:{utm_crs.code}"
    return utm_crs_str


def generate_tiles_geojson(bbox: list, zoom: int) -> geojson.FeatureCollection:
    min_x, min_y = lonlat_to_tile(bbox[0], bbox[3], zoom)
    max_x, max_y = lonlat_to_tile(bbox[2], bbox[1], zoom)

    features = []
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            quadkey = tile_to_quadkey(x, y, zoom)
            bounds = tile_bounds(x, y, zoom)
            polygon = box(bounds[0], bounds[1], bounds[2], bounds[3])
            center_lon, center_lat = polygon.centroid.xy
            center_lon, center_lat = center_lon[0], center_lat[0]
            utm_crs = find_utm_crs_by_latlon(lat=center_lat, lon=center_lon)
            features.append(
                geojson.Feature(
                    geometry=mapping(polygon),
                    properties={"x": x, "y": y, "z": zoom, "quadkey": quadkey, "utm_crs": utm_crs}
                )
            )
    return geojson.FeatureCollection(features)


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a geojson shapefile with all quadkey outlines of the British Isles at a given zoom level.")
    parser.add_argument("--zoom", type=int, default=10)
    parser.add_argument("--output-file", type=Path, help="Output GeoJSON file path")
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        help="Bounding box as 4 floats: min_lon min_lat max_lon max_lat",
        required=True,
        default=[-11.0, 49.5, 2.1, 61.0],  # British Isles
    )
    args = parser.parse_args()
    return args


if __name__ == '__main__':
    args = get_args()
    output_geojson = generate_tiles_geojson(bbox=args.bbox, zoom=args.zoom)
    with open(args.output_file, 'w') as f:
        geojson.dump(output_geojson, f, indent=2)
    print(f"GeoJSON saved to {args.output_file} with {len(output_geojson)} features.")
