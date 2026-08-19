#!/usr/bin/env python3
"""Extract all quadkeys/tiles for which a polygon shape overlaps with."""
import argparse
from pathlib import Path

import geopandas as gpd


def select_quadkeys_with_fields(quadkeys_path: Path, fields_path: Path, output_path: Path) -> None:
    gdf_tiles = gpd.read_file(quadkeys_path)
    gdf_fields = gpd.read_file(fields_path)

    if gdf_tiles.crs != gdf_fields.crs:
        gdf_fields = gdf_fields.to_crs(gdf_tiles.crs)

    # join all fields with the quadkeys -> only keeps tiles that have fields in them
    gdf_filtered = gpd.sjoin(gdf_tiles, gdf_fields, how="inner", predicate="intersects")
    gdf_filtered =  gdf_filtered[gdf_tiles.columns]  # keep only columns of the original quadkey shapefile
    # remove all the duplicates where multiple fields with the same quadkey/located on one tile
    gdf_filtered = gdf_filtered.drop_duplicates("quadkey")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    gdf_filtered.to_file(output_path, driver="GeoJSON")
    print(f"Saved {len(gdf_filtered)} of {len(gdf_tiles)} tiles with overlap to {output_path}")


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select quadkeys, for which shapes overlap with.")
    parser.add_argument("--quadkeys-file", type=Path, help="File with all quadkey tiles.", required=True)
    parser.add_argument("--fields-file", type=Path, help="File with fields/shapes.", required=True)
    parser.add_argument("--output-file", type=Path, help="Output GeoJSON file path", required=True)
    args = parser.parse_args()

    assert args.quadkeys_file.is_file(), ("Not a file!", args.quadkeys_file)
    assert args.fields_file.is_file(), ("Not a file!", args.fields_file)

    return args


def main() -> None:
    args = get_args()
    select_quadkeys_with_fields(
        quadkeys_path=args.quadkeys_file,
        fields_path=args.fields_file,
        output_path=args.output_file,
    )


if __name__ == "__main__":
    main()
