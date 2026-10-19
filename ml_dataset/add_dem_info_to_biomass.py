"""Add elevation, slope, and aspect information of a DSM to each geometry in a shapefile."""

import argparse
from pathlib import Path
from typing import Dict, Optional

import geopandas as gpd
import numpy as np
import rasterio

from extract_feature_timeseries import (
    get_geometries,
    get_image_data_for_geom,
    get_quadkey_from_geometry,
    load_quadkeys,
    save_timeseries,
)


DEM_PATTERN = "*Copernicus_DSM*_Slope_Aspect.tif"


def _compute_dem_stats(dem_data: np.ndarray) -> Dict[str, float]:
    """Compute mean elevation, slope and aspect, guarding against all-nan data."""
    dem_data = np.where(dem_data == 0, np.nan, dem_data)
    assert dem_data.shape[0] == 3, ("Expected 3 DEM bands (elevation/slope/aspect)!", dem_data.shape)
    elevation_band, slope_band, aspect_band = dem_data

    def _safe_mean(arr: np.ndarray) -> float:
        return float(np.nanmean(arr)) if np.isfinite(arr).any() else float("nan")

    dem_info_dict = {
        "mean_elevation_m": _safe_mean(elevation_band),
        "mean_slope_deg": _safe_mean(slope_band),
        "mean_aspect_deg": _safe_mean(aspect_band),
    }
    return dem_info_dict


def get_elevation_slope_aspect_of_field(
    quadkey_dir: Path, geometry, geom_crs
) -> Optional[Dict[str, float]]:
    """Return DEM statistics for a field if a DEM tile exists."""
    dem_filepaths = list(quadkey_dir.glob(DEM_PATTERN))
    if not dem_filepaths:
        raise ValueError(f"Could not find any DEM file! In {quadkey_dir} with pattern '{DEM_PATTERN}'.", )
    if len(dem_filepaths) > 1:
        raise ValueError(f"Expected a single DEM file in {quadkey_dir}, found {dem_filepaths}")
    dem_path = dem_filepaths[0]

    with rasterio.open(dem_path) as ds:
        dem_crs = ds.crs

    if not dem_crs.to_string().startswith("EPSG:32"):
        raise ValueError(f"DEM must be in a UTM CRS, got {dem_crs}")

    dem_data = get_image_data_for_geom(dem_path, geometry, geom_crs)
    if dem_data is None:  # no pixels could be read
        return None
    dem_dict = _compute_dem_stats(dem_data)
    dem_dict["geometry"] = geometry
    return dem_dict


def add_dem_info_to_shapefile(
    input_shapefile: Path,
    quadkey_polygon_file: Path,
    output_path: Path,
    tiles_dir: Path,
) -> None:
    """Augment the shapefile with DEM statistics.
    First, extract the DEM information for each geometry, then add it to the labels/features.
    Typically, there are multiple features/labels with the same geometry (eg the same field has multiple measurements).
    """
    shapes = gpd.read_file(input_shapefile)
    geom_crs = shapes.crs
    geometries = get_geometries(shapes)
    quadkeys = load_quadkeys(quadkey_polygon_file)

    # 1. For each geometry, retrieve the DEM statistics like mean elevation
    dem_infos = list()
    for geom in geometries:
        quadkey = get_quadkey_from_geometry(geom, quadkeys)
        if not quadkey:
            print("Warning: geometry skipped because it does not overlap any quadkey")
            continue
        dem_info_geom = get_elevation_slope_aspect_of_field(
            quadkey_dir=tiles_dir / quadkey,
            geometry=geom,
            geom_crs=geom_crs,
        )
        if dem_info_geom:
            dem_infos.append(dem_info_geom)

    # 2. For each feature/label, add the DEM info by joining.
    dem_df = gpd.GeoDataFrame(dem_infos).set_index("geometry")
    shapes = shapes.join(dem_df, on="geometry")
    save_timeseries(shapes, output_path=output_path)


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser("Add DEM statistics (elevation/slope/aspect) to a shapefile.")
    parser.add_argument("--input-shapefile", type=Path, required=True, help="Path to the input shapefile")
    parser.add_argument("--images-dir", type=Path, required=True, help="Directory containing DEM tiles grouped by quadkey")
    parser.add_argument("--quadkey-polygons", type=Path, required=True, help="Vector file describing the quadkey footprints")
    parser.add_argument("--output", type=Path, required=True, help="Path to the output shapefile/parquet ('.parquet' added if missing)")
    return parser.parse_args()


def main() -> None:
    args = get_args()
    add_dem_info_to_shapefile(
        input_shapefile=args.input_shapefile,
        quadkey_polygon_file=args.quadkey_polygons,
        tiles_dir=args.images_dir,
        output_path=args.output,
    )

if __name__ == "__main__":
    main()
