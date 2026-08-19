#!/usr/bin/env python3
"""Script to take many biomass field measurements and combine them spatially and temporally.

Takes a biomass dataset consisting of many field measurements (field, date, label) and
aggregates it spatially (combining nearby fields into one shape and spatial cluster)
and temporally, going from many measurement dates of a cluster of fields to (multiple)
time ranges with start and end date.
This is done to allow easy search of satellite date for that dataset and allowing filtering
for a single shape and a time range.
"""
import argparse
from pathlib import Path
from typing import List, Tuple, Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from pyproj import CRS
from datetime import timedelta

def build_clusters(
    gdf: gpd.GeoDataFrame, buffer_size_meters: float = 0.0
) -> List[int]:
    """
    Assign each feature a cluster ID based on spatial adjacency.
    Buffers geometries by buffer_size, unions them, then
    splits into connected "cluster" polygons via unary_union.

    Returns a list of cluster IDs aligned with gdf.index order.
    """
    buffered = gdf.geometry.buffer(buffer_size_meters)

    # union all buffered geometries into multi-polygon of clusters
    unioned = unary_union(buffered)

    # extract individual cluster polygons
    if isinstance(unioned, Polygon):
        clusters_polys: List[Polygon] = [unioned]
    elif isinstance(unioned, MultiPolygon):
        # use .geoms to access individual polygons in a MultiPolygon
        clusters_polys = list(unioned.geoms)
    else:
        raise ValueError("Unexpected geometry type returned by unary_union: %s" % type(unioned))

    # assign each original feature to a cluster
    cluster_ids = [-1] * len(gdf)
    for cid, poly in enumerate(clusters_polys):
        # find all features whose (buffered) geometry intersects this cluster polygon
        intersects = buffered.intersects(poly)
        for idx in gdf.index[intersects]:
            cluster_ids[gdf.index.get_loc(idx)] = cid

    return cluster_ids

def split_periods(
    dates: List[pd.Timestamp], max_gap_months: int = 6
) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    """
    Given a sorted list of pandas Timestamps, split into
    (start, end) pairs whenever gap > max_gap_months.
    """
    if not dates:
        return []

    periods: List[Tuple[pd.Timestamp, pd.Timestamp]] = []
    start = end = dates[0]
    for d in dates[1:]:
        if d - end > timedelta(days=max_gap_months*30):
            periods.append((start, end))
            start = d
        end = d
    assert start <= end, ("Start after end!", start, end)
    periods.append((start, end))
    return periods

def aggregate_fields(
    input_path: Path,
    output_path: Path,
    buffer_size_meters: float = 0.0,
    max_gap_months: int = 6,
    date_column_name: str = "measured_date",
) -> int:
    # load and check input
    gdf = gpd.read_file(str(input_path))
    if date_column_name not in gdf.columns:
        raise ValueError(f"Input GeoJSON must have a '{date_column_name}' column. Only present: {gdf.columns}")
    gdf[date_column_name] = pd.to_datetime(gdf[date_column_name])
    gdf = gdf.sort_values(date_column_name)

    # reproject to have coordinates in meter
    utm_crs = calculate_utm_crs(gdf)
    gdf = gdf.to_crs(utm_crs)

    gdf["cluster"] = build_clusters(gdf, buffer_size_meters=buffer_size_meters)  # assign each field its own cluster

    # dissolve geometries per cluster
    dissolved = gdf.dissolve(by="cluster", as_index=False)
    dissolved = dissolved[dissolved.geometry.astype(bool)]  # remove clusters without geometry (this happened for rothamsted)
    # collect date lists per cluster
    dates_series = gdf.groupby("cluster")[date_column_name].agg(list)

    # build output records with split date periods
    records = []
    for _, row in dissolved.iterrows():
        geom = row.geometry
        # geom_simple = geom.buffer(10) # to remove small holes
        # geom_simple = geom_simple.simplify
        for start, end in split_periods(dates_series.loc[row["cluster"]], max_gap_months):
            records.append({
                "geometry": geom,
                "date_start": start.date().isoformat(),
                "date_end": end.date().isoformat(),
                "cluster_idx": row.loc["cluster"]
            })

    out = gpd.GeoDataFrame(records, crs=dissolved.crs)
    out['geometry'] = out['geometry'].buffer(10)  # remove small holes
    out['geometry'] = out['geometry'].simplify(5)
    out = out.to_crs(epsg=4326)
    out.to_file(output_path, driver="GeoJSON")
    return len(out)

def calculate_utm_crs(gdf: gpd.GeoDataFrame) -> CRS:
    """
    Determine appropriate UTM CRS based on the centroid of the data (in lon/lat).
    """
    # ensure geographic for centroid
    lonlat = gdf.to_crs(epsg=4326)
    centroid = lonlat.union_all().centroid
    lon, lat = centroid.x, centroid.y
    zone = int((lon + 180) // 6) + 1
    if lat >= 0:
        epsg_code = 32600 + zone
    else:
        epsg_code = 32700 + zone
    return CRS.from_epsg(epsg_code)


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate field polygons into clusters with date intervals")
    parser.add_argument("--input-geojson", type=Path, help="Input GeoJSON file with 'measured_date'")
    parser.add_argument(
        "--output-geojson",
        type=Path,
        help="Output GeoJSON file path",
    )
    parser.add_argument(
        "--buffer-meters", "-b",
        type=float,
        default=10e3,
        help="Buffer size in meters to apply before clustering/spatial aggregation",
    )
    parser.add_argument(
        "--max-gap-months", "-g",
        type=int,
        default=6,
        help="Maximum gap in months to keep dates in one period",
    )
    parser.add_argument("--date-column-name", type=str, help="Name of the column containing the measurement dates.", default="measurement_date")
    args = parser.parse_args()

    assert args.input_geojson.is_file(), ("Not a file!", args.input_geojson)
    # would work, but is not reasonable
    assert args.buffer_meters >= 0, ("Negative buffer not allowed.", args.buffer_meters)
    assert args.max_gap_months >= 0, ("Negative max gap months not allowed.", args.args.max_gap_months)

    return args


def main() -> None:
    args = get_args()
    count = aggregate_fields(
        input_path=args.input_geojson,
        output_path=args.output_geojson,
        buffer_size_meters=args.buffer_meters,
        max_gap_months=args.max_gap_months,
        date_column_name=args.date_column_name,
    )
    print(f"Wrote {count} aggregated features to {args.output_geojson}")


if __name__ == "__main__":
    main()
