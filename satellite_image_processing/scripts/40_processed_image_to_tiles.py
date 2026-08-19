import argparse
import datetime
import warnings
from pathlib import Path
from typing import Tuple, Optional, List
import re
import os

import rasterio
from rasterio.mask import mask
import shapely
from shapely.geometry import shape, mapping
from shapely.ops import transform as shapely_transform
import pyproj
from osgeo import gdal
import numpy as np
import geopandas as gpd
import pandas as pd
from tqdm.contrib.concurrent import process_map

from utils import (Modality, copy_tif_metadata, get_modality_from_filepath, get_geotiff_band_names, get_cloud_mask_scl,
                   try_parse_date_from_filepath,
                   BandnamesProcessedS1, BandnamesProcessedS2, BandnamesProcessedSWI, BandnamesProcessedDSM)
from write_bandnames_colorinterpretations_into_geotiff import write_bandnames_colorinterpretations_into_tif
gdal.UseExceptions()


def reproject_geometry(geom, src_crs, dst_crs):
    project = pyproj.Transformer.from_crs(src_crs, dst_crs, always_xy=True).transform
    return shapely_transform(project, geom)


def reproject_raster_gdal(in_raster_path, output_bounds, output_epsg_code: str) -> Tuple[np.ndarray, rasterio.transform.Affine]:
    with gdal.Open(in_raster_path) as raster_in_ds:
        assert isinstance(raster_in_ds, gdal.Dataset), ("Type error!", type(raster_in_ds))
        # Output bounds in destination CRS (e.g., UTM)
        # IMPORTANT: these must be in the CRS of `dstSRS`
        output_srs = output_epsg_code
        warp_options = gdal.WarpOptions(format="MEM", dstSRS=output_srs, outputBounds=output_bounds,
                                        # resampleAlg='bilinear', # or 'near', 'cubic', etc.
                                        )
        output_ds = gdal.Warp(
            '',  # '' + format='MEM' = in-memory
            raster_in_ds,
            options=warp_options,
        )

        tile_array = output_ds.ReadAsArray()

        gt = output_ds.GetGeoTransform()
        transform = rasterio.transform.Affine.from_gdal(*gt)
    return tile_array, transform


def is_feature_inside_of_raster(geom_in_raster_crs: shapely.Geometry,
                                src_bounds: rasterio.coords.BoundingBox) -> bool:
    raster_outline = shape({'type': 'Polygon', 'coordinates': [
        [(src_bounds.left, src_bounds.bottom),
         (src_bounds.left, src_bounds.top),
         (src_bounds.right, src_bounds.top),
         (src_bounds.right, src_bounds.bottom),
         (src_bounds.left, src_bounds.bottom)]
    ]})
    return geom_in_raster_crs.intersects(raster_outline)


def get_geometries_overlapping_rasterfile(features: gpd.GeoDataFrame, raster_bounds, raster_crs, shapefile_crs) -> gpd.GeoDataFrame:
    features_overlapping = list()

    for _, feature in features.iterrows():
        feature_geom = feature.geometry
        #feature_geom = shape(feature['geometry'])
        # skip tiles not overlapping raster
        # Reproject geometry to raster CRS for overlap check
        geom_in_raster_crs = reproject_geometry(feature_geom, shapefile_crs, raster_crs)
        if is_feature_inside_of_raster(geom_in_raster_crs, raster_bounds):
            features_overlapping.append(feature)

    gdf_out = gpd.GeoDataFrame(features_overlapping)
    return gdf_out

def is_tile_empty(tile_img: np.ndarray, nodata_value=None) -> bool:
    # tile_img shape: (band, width, height) or (..., height, width)
    zero_mask = (tile_img == 0.)
    if np.all(zero_mask):
        return True
    nan_mask = np.isnan(tile_img)
    if np.all(nan_mask):
        return True
    if np.all(np.logical_or(zero_mask, nan_mask)):
        return True
    if nodata_value:
        if np.all(tile_img == nodata_value):
            return True

        if np.all(np.logical_or(tile_img == nodata_value, zero_mask)):
            return True

    return False


def is_s2_tile_clouded(scl_img: np.ndarray) -> bool:
    mask = get_cloud_mask_scl(scl_img=scl_img)
    return np.all(mask) # whole tile is masked


def get_tile_data(feature: pd.Series, feature_crs, output_epsg_code: str, in_raster_path) -> Tuple[np.ndarray, rasterio.transform.Affine]:
    """Reproject and mask area using gdal.Warp()."""

    feature_geom = feature.geometry
    geom_in_out_crs = reproject_geometry(feature_geom, src_crs=feature_crs, dst_crs=output_epsg_code)

    reprojected_tile, tile_transform = reproject_raster_gdal(in_raster_path, output_bounds=geom_in_out_crs.bounds, output_epsg_code=output_epsg_code)
    if reprojected_tile.ndim == 2:
        reprojected_tile = reprojected_tile[np.newaxis]

    # the returned tile is not perfectly aligned to the quadkey grid, so remove border areas
    # "removing" means replacing the pixel value with no data...
    with rasterio.io.MemoryFile() as memfile:
        with memfile.open(
            driver='GTiff',
            height=reprojected_tile.shape[1],
            width=reprojected_tile.shape[2],
            count=reprojected_tile.shape[0],
            dtype=reprojected_tile.dtype,
            transform=tile_transform,
        ) as dataset:
            dataset.write(reprojected_tile)
        with memfile.open() as dataset:
            nodata = np.nan if np.issubdtype(reprojected_tile.dtype, np.floating) else 0
            reprojected_tile, tile_mask_transform = rasterio.mask.mask(dataset, shapes=[mapping(geom_in_out_crs)], crop=True, filled=True, nodata=nodata)

    return reprojected_tile, tile_transform


def check_expected_bandnames_of_processed_image(actual_bandnames: List[str], modality: Modality) -> None:
    if modality == Modality.s1: expected_bandnames = BandnamesProcessedS1
    elif modality == Modality.s2: expected_bandnames = BandnamesProcessedS2
    elif modality == Modality.SWI: expected_bandnames = BandnamesProcessedSWI
    elif modality == Modality.DSM: expected_bandnames = BandnamesProcessedDSM
    else: raise ValueError("unexpected modality!", modality)
    if actual_bandnames != expected_bandnames:
        warnings.warn("Bandnames unexpected!", actual_bandnames, expected_bandnames)


def get_scl_raster_path(s2_raster_path: Path) -> Path:
    """This will get the path to the SCL raster when given a reflectance|ndvi|ndmi path."""
    raster_dir = s2_raster_path.parent
    feature_filename = s2_raster_path.name
    featurename, remainder = feature_filename.split("_", maxsplit=1)
    scl_filename = "scl" + "_" + remainder
    scl_path = raster_dir / scl_filename
    assert scl_path.is_file(), ("Not scl raster found!", scl_path, s2_raster_path)
    return scl_path

def check_expected_band_count(band_no: int, modality: Modality):
    if modality == Modality.s1: expected_bandcount = len(BandnamesProcessedS1)
    elif modality == Modality.s2: expected_bandcount = len(BandnamesProcessedS2)
    elif modality == Modality.SWI: expected_bandcount = len(BandnamesProcessedSWI)
    elif modality == Modality.DSM: expected_bandcount = len(BandnamesProcessedDSM)
    else: raise ValueError("unexpected modality!", modality)
    assert expected_bandcount == band_no, ("Bandcount unexpected", band_no, expected_bandcount)


def _process_tile_task_argstuple(args):
    return _process_tile_task(*args)

def _process_tile_task(feature, feature_crs, raster_path: str, date_yyyymmdd: Optional[str], modality: Modality, raster_profile, nodata_value, output_dir: Path, verbose: bool):
    quadkey = feature["quadkey"]
    utm_epsg_code = feature["utm_crs"]  # example: 'EPSG:32629'

    tile_img, tile_transform = get_tile_data(
        feature=feature,
        feature_crs=feature_crs,
        output_epsg_code=utm_epsg_code,
        in_raster_path=raster_path,
    )

    if is_tile_empty(tile_img, nodata_value=nodata_value):
        if verbose: print(f"Skipping completely empty tile {quadkey}.")
        return

    bandnames = get_geotiff_band_names(raster_path=Path(raster_path))
    check_expected_bandnames_of_processed_image(bandnames, modality)
    check_expected_band_count(tile_img.shape[0], modality)

    # mask pixels where all bands are zero (=don't contain data)
    all_zero_mask = np.sum(tile_img, axis=0) == 0
    nodata_value = np.nan if np.isdtype(tile_img.dtype, np.floating) else 0
    tile_img[:, all_zero_mask] = nodata_value

    if modality == "s2":  # special cloud handling
        scl_band = BandnamesProcessedS2.index("quality_scene_classification")
        scl_tile_data = tile_img[scl_band]
        if is_s2_tile_clouded(scl_img=scl_tile_data):
            if verbose: print(f"Skipping completely clouded tile {quadkey}.")
            return

        # mask cloudy pixels, as they are not needed
        cloud_mask = get_cloud_mask_scl(scl_tile_data)
        tile_img[:, cloud_mask] = nodata_value


    out_meta = raster_profile.copy()
    out_meta.update({
        "crs": utm_epsg_code,
        "driver": "GTiff",
        "height": tile_img.shape[1],
        "width": tile_img.shape[2],
        "transform": tile_transform,
        "nodata": nodata_value,
        "compress": "ZSTD",
        "tiled": False,
    })

    feature_raster_filename_without_ext = Path(raster_path).stem
    if date_yyyymmdd:
        out_filename = f"{quadkey}_{date_yyyymmdd}_{feature_raster_filename_without_ext}.tif"
    else:
        out_filename = f"{quadkey}_{feature_raster_filename_without_ext}.tif"

    out_path = Path(output_dir) / quadkey / out_filename
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # TODO add no data value
    with rasterio.open(out_path, "w", **out_meta) as dst:
        dst.write(tile_img)

    bandnames_feature_img = get_geotiff_band_names(Path(raster_path))
    if bandnames_feature_img:
        bandnames_feature_img_str = ",".join(bandnames_feature_img)
        write_bandnames_colorinterpretations_into_tif(out_path, bandnames=bandnames_feature_img_str, color_interpretations=None)

    copy_tif_metadata(src_tif=Path(raster_path), dst_tif=out_path)
    if verbose: print(f"Saved image to {out_path}.")
    return tile_img


def extract_tiles_from_raster(processed_raster_img_path, tiling_polygon_vectorfile, output_dir, max_workers=None, verbose=False, disable_progress_bar=False):
    modality = get_modality_from_filepath(processed_raster_img_path)
    date = try_parse_date_from_filepath(processed_raster_img_path, modality)
    date_yyyymmdd = date.strftime("%Y%m%d")


    with rasterio.open(processed_raster_img_path) as src:
        raster_crs = src.crs
        raster_profile = src.profile.copy()
        nodata_value = src.nodata
        raster_bounds = src.bounds

    gdf = gpd.read_file(tiling_polygon_vectorfile)
    features_crs = gdf.crs  # e.g. {'init': 'epsg:4326'}

    features_overlapping_raster = get_geometries_overlapping_rasterfile(features=gdf, raster_bounds=raster_bounds, raster_crs=raster_crs, shapefile_crs=features_crs)
    if verbose:
        print(f"{len(features_overlapping_raster)} of {len(gdf)} features/tiles overlap with the raster.")

    tasks = []
    for _, feature in features_overlapping_raster.iterrows():
        tasks.append((feature, features_crs, str(processed_raster_img_path),
                      date_yyyymmdd, modality, raster_profile,
                      nodata_value, str(output_dir), verbose))


    if not max_workers: max_workers = os.cpu_count() - 1  # all but one

    written_tiles = process_map(
        _process_tile_task_argstuple,
        tasks,
        max_workers=max_workers,
        chunksize=1,
        desc="Extracting tiles",
        disable=disable_progress_bar,

    )
    if verbose:
        print(f"Saved {len(written_tiles)} images/tiles to disk.")


def parse_and_check_args():
    parser = argparse.ArgumentParser(description="Tile a raster based on GeoJSON features")
    parser.add_argument("--raster-path", help="Path to input GeoTIFF raster", type=Path, required=True)
    parser.add_argument("--shapefile-path", help="Path to input shapefile or GeoJSON", type=Path, required=True)
    parser.add_argument("--output-dir", help="Directory to save output tiles", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=0, help="0 means all but one core.")
    parser.add_argument("--verbose", action="store_true", default=False)
    parser.add_argument("--disable-progress-bar", action="store_true", default=False)
    args = parser.parse_args()

    assert args.raster_path.is_file(), ("Raster file not found!", args.raster_path)
    assert args.shapefile_path.is_file(), ("Tiling shapefile not found!", args.shapefile_path)
    return args


def main():
    args = parse_and_check_args()
    args.output_dir.mkdir(exist_ok=True)

    extract_tiles_from_raster(
        processed_raster_img_path=args.raster_path,
        tiling_polygon_vectorfile=args.shapefile_path,
        output_dir=args.output_dir,
        max_workers=args.max_workers,
        verbose=args.verbose,
        disable_progress_bar=args.disable_progress_bar,
    )


if __name__ == "__main__":
    main()
