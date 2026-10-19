"""Utilities and orchestration helpers for feature time-series extraction."""

import argparse
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Set

import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
import rasterio
import rasterio.mask
from osgeo import gdal
from shapely import geometry as shapely_geometry, ops
from shapely.geometry.base import BaseGeometry

from satellite_image_processing.scripts.utils import (
    Modality,
    get_modality_from_filepath,
    try_parse_date_from_filepath,
)
from ml_dataset.image_feature import FeaturesDict, get_image_features


gdal.UseExceptions()


def load_biomass_labels(path: Path) -> gpd.GeoDataFrame:
    """Load label geometries and validate the measurement date column."""
    gdf = gpd.read_file(path)
    if "measurement_date" not in gdf.columns:
        raise ValueError("Labels must contain 'measurement_date'")
    gdf["measurement_date"] = pd.to_datetime(
        gdf["measurement_date"], utc=True, errors="coerce"
    )
    if gdf["measurement_date"].isna().any():
        raise ValueError("Labels contain invalid 'measurement_date' values")

    gdf["crs"] = gdf.crs  # keep CRS explicitly as a column for downstream users
    return gdf

def load_quadkeys(path: Path) -> gpd.GeoDataFrame:
    """Load quadkey polygons and index them by the quadkey id."""
    quadkey_df = gpd.read_file(path)
    if "quadkey" not in quadkey_df.columns:
        raise ValueError("Quadkeys file must contain 'quadkey'")
    return quadkey_df.set_index("quadkey")

def get_geometries(labels: gpd.GeoDataFrame) -> gpd.GeoSeries:
    """Return the unique non-empty geometries of the provided labels."""
    if "geometry" not in labels.columns:
        raise ValueError("Labels must contain a 'geometry' column")
    geometries = labels.geometry
    geometries = geometries[~geometries.is_empty]
    return geometries.drop_duplicates().reset_index(drop=True)


def calculate_pixel_statistics(pixels: np.ndarray) -> Optional[Dict[str, float]]:
    """Calculate descriptive statistics for an array of pixels."""
    pixels = pixels.flatten()
    pixels = pixels[~np.isnan(pixels)]
    if pixels.size == 0:
        return None

    stats: Dict[str, float] = {
        "mean": float(np.mean(pixels)),
        "min": float(np.min(pixels)),
        "max": float(np.max(pixels)),
        "std": float(np.std(pixels)),
        "pixelcount": float(pixels.size),
    }
    percentiles = [5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95]
    percentile_values = np.percentile(pixels, percentiles)
    for percentile, value in zip(percentiles, percentile_values):
        stats[f"p{percentile:02d}"] = float(value)
    stats["median"] = stats["p50"]
    return stats


def get_feature_stats(features: FeaturesDict) -> Dict[str, float]:
    """Apply ``calculate_pixel_statistics`` to each feature band."""
    stats: Dict[str, float] = {}
    for feature_name, feature_data in features.items():
        pixel_stats = calculate_pixel_statistics(feature_data)
        if not pixel_stats:
            continue
        for key, value in pixel_stats.items():
            stats[f"{feature_name}_{key}"] = value
    return stats


def get_image_data_for_geom(
    image_path: Path, geometry: BaseGeometry, geom_crs, all_touched: bool = False
) -> Optional[np.ndarray]:
    """Return the pixels of ``image_path`` that intersect ``geometry``."""
    with rasterio.open(image_path) as src:
        raster_crs = src.crs

        transformer = pyproj.Transformer.from_crs(
            crs_from=geom_crs, crs_to=raster_crs, always_xy=True
        )
        geom_in_raster_crs = ops.transform(transformer.transform, geometry)
        raster_bounds = shapely_geometry.box(*src.bounds)
        if not geom_in_raster_crs.intersects(raster_bounds):
            return None

        arr, _ = rasterio.mask.mask(
            src, [shapely_geometry.mapping(geom_in_raster_crs)], crop=True, all_touched=all_touched,
        )
    return arr


def are_all_pixels_nan_or_zero(img: np.ndarray) -> bool:
    """Return True when the full cube is either filled with NaNs or zeros."""
    if img.ndim != 3:
        raise ValueError(f"Expected a 3D array, got shape {img.shape}")
    zero_or_nan = np.isnan(img) | (img == 0)
    return bool(np.all(zero_or_nan))


def get_quadkey_from_geometry(
    geometry_obj: BaseGeometry, quadkeys: gpd.GeoDataFrame
) -> Optional[str]:
    """Return the quadkey identifier for the provided geometry."""
    overlapping_keys = [
        qk_name
        for qk_name, qk in quadkeys.iterrows()
        if geometry_obj.intersects(qk.geometry)
    ]
    if len(overlapping_keys) == 1:
        return overlapping_keys[0]
    if not overlapping_keys:
        return None

    warnings.warn(
        "Field overlaps multiple quadkeys; skipping it to avoid duplication.",
        RuntimeWarning,
        stacklevel=2,
    )
    return None


def get_geotiff_band_names(raster_path: Path) -> List[str]:
    """Return a list of band descriptions for ``raster_path``."""
    with gdal.Open(raster_path, gdal.GA_ReadOnly) as raster_ds:
        band_count = raster_ds.RasterCount
        return [
            raster_ds.GetRasterBand(i).GetDescription() or f"band_{i}"
            for i in range(1, band_count + 1)
        ]


def get_timeseries_of_geometry(
    geometry: BaseGeometry,
    geom_crs,
    tiles_dir: Path,
    quadkeys: gpd.GeoDataFrame,
    used_modalities: Optional[Set[Modality]] = None
) -> Optional[List[Dict[str, object]]]:
    """Extract feature statistics for all images intersecting ``geometry``."""
    quadkey = get_quadkey_from_geometry(geometry, quadkeys)
    if not quadkey:
        return None

    quadkey_dir = tiles_dir / quadkey
    images_for_geom = list(quadkey_dir.glob("*.tif"))

    datalist: List[Dict[str, object]] = []
    for img_path in images_for_geom:
        modality = get_modality_from_filepath(filepath=img_path)
        if used_modalities and modality not in used_modalities:
            continue

        cropped_modality_img = get_image_data_for_geom(
            image_path=img_path,
            geometry=geometry,
            geom_crs=geom_crs,
        )
        if cropped_modality_img is None:
            continue
        if are_all_pixels_nan_or_zero(cropped_modality_img):
            continue

        bandnames = get_geotiff_band_names(img_path)
        img_date = try_parse_date_from_filepath(img_path, modality=modality)
        feature_pixels = get_image_features(
            modality, cropped_img=cropped_modality_img, bandnames=bandnames
        )
        feature_stats = get_feature_stats(feature_pixels)
        if not feature_stats:
            continue
        feature_stats.update(
            {
                "image_path": str(img_path),
                "image_date": img_date,  # datetime
                "geometry": geometry,
                "modality": str(modality),
                "quadkey": quadkey,
            }
        )
        datalist.append(feature_stats)
    return datalist


def save_timeseries(all_timeseries: gpd.GeoDataFrame, output_path: Path) -> None:
    """Persist the GeoDataFrame as a parquet file."""
    if output_path.suffix != ".parquet":
        print(
            "Warning: expected a parquet path, adding '.parquet' suffix to"
            f" {output_path}"
        )
        output_path = output_path.with_suffix(".parquet")
    all_timeseries.to_parquet(output_path)
    print(f"Saved timeseries data to {output_path}. Columns: {list(all_timeseries.columns)}")



def extract_timeseries(
    labels_path: Path,
    processed_tiles_dir: Path,
    quadkey_polygon_file: Path,
    output_path_pattern: str,
    s1_image_id_file: Optional[Path] = None,
    max_workers: int = 2,
) -> None:
    """Run the DEM, S1/S2 and SWI extraction pipeline."""
    from add_dem_info_to_biomass import add_dem_info_to_shapefile
    from extract_feature_timeseries_s1_s2 import extract_s12_timeseries
    from extract_feature_timeseries_swi import extract_swi_timeseries

    if "{}" not in output_path_pattern:
        raise ValueError("output_path_pattern must contain '{}' placeholder")
    swi_out_path = Path(output_path_pattern.format("swi"))
    s12_out_path = Path(output_path_pattern.format("s1-s2"))
    labels_dem_added_path = Path(output_path_pattern.format("labels-dem"))

    print("Adding DEM information to labels...")
    add_dem_info_to_shapefile(
        input_shapefile=labels_path,
        quadkey_polygon_file=quadkey_polygon_file,
        output_path=labels_dem_added_path,
        tiles_dir=processed_tiles_dir,
    )

    print("Extracting S1/S2 time series...")
    extract_s12_timeseries(
        labels_path=labels_path,
        processed_tiles_dir=processed_tiles_dir,
        quadkey_polygon_file=quadkey_polygon_file,
        output_path=s12_out_path,
        s1_image_id_file=s1_image_id_file,
        max_workers=max_workers,
    )

    print("Extracting SWI time series...")
    extract_swi_timeseries(
        tiles_dir=processed_tiles_dir,
        polygon_file=labels_path,
        out_parquet_path=swi_out_path,
        quadkey_polygons=quadkey_polygon_file,
    )




    # # convert biomass labels into timeseries format
    # label_columns = ["geometry", "field_id", "measured_biomass_kg_ha", "measurement_date", "source", "layer", "path"]
    # label_columns_existing = set(label_columns).intersection(set(labels.columns))
    # label_columns_existing = list(label_columns_existing)
    # labels_selection = labels[label_columns_existing].copy()
    # labels_selection["measurement_date"] = pd.to_datetime(labels_selection["measurement_date"], utc=True)
    #
    # labels_output_path = output_path.parent / (output_path.stem + "_labels" + output_path.suffix)
    # save_timeseries(labels_selection, output_path=labels_output_path)



def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser("Create timeseries dataset from processed S1/S2 images and field labels.")
    parser.add_argument("--labels", type=Path, required=True, help="GeoJSON with 'measurement_date' and geometry")
    parser.add_argument("--images-dir", type=Path, required=True, help="Directory with processed S1/S2 tiles")
    parser.add_argument("--quadkey-polygons", type=Path, required=True, help="Vectorfile, where the quadkey shapes are defined.")
    parser.add_argument("--output-pattern", type=str, required=True, help="Pattern such as '/tmp/timeseries-{}.parquet' that contains '{}' placeholder.")
    parser.add_argument("--max-workers", type=int, default=4, help="Number of parallel jobs.")
    parser.add_argument("--s1-image-id-file", type=Path, default=None, required=False, help="Path to the file with the image ids to add the orbit direction and relative orbit")
    args = parser.parse_args()
    return args


def main() -> None:
    args = get_args()
    extract_timeseries(
        labels_path=args.labels,
        processed_tiles_dir=args.images_dir,
        quadkey_polygon_file=args.quadkey_polygons,
        s1_image_id_file=args.s1_image_id_file,
        output_path_pattern=args.output_pattern,
        max_workers=args.max_workers,
    )
    #logging.info(f"Saved ML dataset to {args.output}")


if __name__ == "__main__":
    main()
