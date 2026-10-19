#!/usr/bin/env python3
"""Apply a negative buffer to polygons."""
from typing import Optional
import argparse
import geopandas as gpd


def apply_buffer_meters(gdf: gpd.GeoDataFrame, buffer_m: float, crs: Optional[str] = None) -> gpd.GeoDataFrame:
    """
    Apply a buffer in meters (negative for inwards) to the geometries in a GeoDataFrame.
    """
    assert gdf.crs, ("No CRS set in input shapefile!")
    gdf = gdf.copy()
    original_crs = gdf.crs
    utm_crs = gdf.estimate_utm_crs()
    gdf = gdf.to_crs(utm_crs)
    assert not gdf.crs.is_geographic, (f"CRS {gdf.crs} is geographic (degrees), not in meters")
    assert gdf.crs.is_projected
    gdf["geometry"] = gdf.geometry.buffer(buffer_m)
    gdf = gdf.to_crs(original_crs)
    return gdf


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a metric buffer to polygons using GeoPandas.")
    parser.add_argument("input", help="Input vector filepath")
    parser.add_argument("output", help="Output vector filepath")
    parser.add_argument("--buffer", type=float, required=True, help="Buffer distance in meters (negative = inward)")
    return parser.parse_args()


def main() -> None:
    args = get_args()
    gdf = gpd.read_file(args.input)
    gdf_buffered = apply_buffer_meters(gdf, args.buffer)
    gdf_buffered.to_file(args.output)


if __name__ == "__main__":
    main()
