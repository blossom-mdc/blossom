"""Extract soil moisture (SWI) time series for each field geometry."""

import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import geopandas as gpd
import numpy as np
import rasterio
import rasterio.mask
from tqdm import tqdm

from extract_feature_timeseries import (
    get_geometries,
    get_quadkey_from_geometry,
    load_quadkeys,
    get_image_data_for_geom
)
from satellite_image_processing.scripts.utils import try_parse_date_from_filepath, Modality

SWI_PATTERN = "*_c_gls_SWI1km_*_cog_SWI002_QFLAG002_SSF.tif"
SWI_SCALE = 0.5  # this could also be read from the tif band metadata. convert DN -> physical value/SWI. for SWI and QFlag (Table 9, Product user manual)


def _get_date_from_sm_filename(fname: str) -> datetime:
    # filename example: 'c_gls_SWI1km_202106021200_CEURO_SCATSAR_V1.0.1_cog_SWI002_QFLAG002_SSF.tif'
    # or:
    datestr = fname.split("_")[3]
    if not datestr.endswith("1200"):
        raise ValueError(f"Unexpected timestamp in SWI filename: {fname}. Should be '1200'.")
    return datetime.fromisoformat(datestr[:8])


def _mask_and_scale(
    img_arr: np.ndarray, shapes: List[dict]
) -> np.ndarray:
    assert img_arr.ndim == 2
    masked = np.where(img_arr == 255, np.nan, img_arr)
    # Conversion to physical values using `SWI_SCALE`
    # see: https://land.copernicus.eu/en/technical-library/product-user-manual-soil-water-index/@@download/file
    # PhyVal = DN * Scale_factor
    # only applied when DN is in valid range ([0, 200] for SWI, see Table 5)

    # remaining values >200 are flags (Table 10)
    # 241 ExceedingMin Exceeding local pixel’s dry extreme
    # 242 ExceedingMax Exceeding local pixel’s wet extreme
    # 251 WaterMask Masked water surface
    # 252 SensitivityMask Masked for low sensitivity S40
    # 253 SlopeMask Masked for extreme slope (topography)
    # 254 LowQFLAG QFLAG value is low, indicating SWI value is less reliable
    scaled = np.where(masked <= 200, masked * SWI_SCALE, masked)
    return scaled


def _get_state_flag(img_arr, shapes: List[dict]) -> np.ndarray:
    assert img_arr.ndim == 3, ("Expected 3 dims!", img_arr.shape)
    assert img_arr.shape[0] == 3, ("Expected 3 bands!", img_arr.shape)
    masked = img_arr[2]
    state_flag = np.where(masked == 255, np.nan, masked)
    return state_flag


def _get_data_for_geometry(
    swi_tif_path: Path, geometry, geometry_crs
) -> Dict[str, float]:
    """Extract SWI statistics for a single geometry."""
    date = try_parse_date_from_filepath(swi_tif_path, modality=Modality.SWI)
    img_data = get_image_data_for_geom(image_path=swi_tif_path, geometry=geometry, geom_crs=geometry_crs, all_touched=True)
    swi_arr = _mask_and_scale(img_data[0], geometry)
    qi_arr = _mask_and_scale(img_data[1], geometry)
    state_flag_arr = _get_state_flag(img_data, geometry)

    if np.all(np.isnan(swi_arr)):
        soil_moisture_mean = np.nan
        qi_min = np.nan
        state_flag = np.nan
    else:
        soil_moisture_mean = float(np.nanmean(swi_arr))
        # use min(quality) for conservative approach, outputting lower quality
        qi_min = float(np.nanmin(qi_arr))
        # State flag: 1 Unfrozen , 2 Frozen, 3 Temporary water.
        # use max() for conservative approach, tend to worse state = higher value
        state_flag = float(np.nanmax(state_flag_arr))

    return {
        "geometry": geometry,
        "date": date,
        "soil_moisture": soil_moisture_mean,
        "quality_indicator": qi_min,
        "soil_state": state_flag,
    }


def _find_swi_tiles(tiles_dir: Path, quadkey: str) -> List[Path]:
    quadkey_dir = tiles_dir / quadkey
    if not quadkey_dir.is_dir():
        return []
    return list(quadkey_dir.glob(SWI_PATTERN))


def extract_swi_timeseries(
    tiles_dir: Path,
    polygon_file: Path,
    out_parquet_path: Path,
    quadkey_polygons: Path,
) -> None:
    """Extract SWI time series for each geometry and save to parquet."""
    if not tiles_dir.is_dir():
        raise ValueError(f"Not a directory: {tiles_dir}")
    if not polygon_file.is_file():
        raise ValueError(f"Not a file: {polygon_file}")

    labels = gpd.read_file(polygon_file)
    geometries = get_geometries(labels)
    quadkeys = load_quadkeys(quadkey_polygons)

    all_data: List[Dict[str, float]] = []
    for geom in tqdm(geometries, desc="Processing SWI tiles"):
        quadkey = get_quadkey_from_geometry(geom, quadkeys)
        if not quadkey:
            print("Warning: geometry skipped because it does not overlap any quadkey")
            continue

        swi_tiles = _find_swi_tiles(tiles_dir, quadkey)
        if not swi_tiles:
            print(f"Warning: no SWI tile found for quadkey {quadkey}")
            continue

        for swi_path in swi_tiles:
            swi_data_geom = _get_data_for_geometry(swi_tif_path=swi_path, geometry=geom, geometry_crs=labels.crs)
            all_data.append(swi_data_geom)

    if not all_data:
        raise ValueError("No SWI data extracted for the provided geometries")

    sm_df = gpd.GeoDataFrame(all_data, crs=labels.crs)
    sm_df.to_parquet(out_parquet_path)
    print(f"Saved SWI time series to {out_parquet_path}")


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser("Create time series dataset from SWI data and field labels.")
    parser.add_argument("--polygon-file", type=Path, required=True, help="GeoJSON with geometries")
    parser.add_argument("--swi-tiles-dir", type=Path, required=True, help="Directory with SWI tiles")
    parser.add_argument("--quadkey-polygons", type=Path, required=True, help="Vector file with quadkey shapes")
    parser.add_argument("--output", type=Path, required=True, help="Path to output parquet file")
    return parser.parse_args()


def main() -> None:
    args = get_args()
    extract_swi_timeseries(
        tiles_dir=args.swi_tiles_dir,
        polygon_file=args.polygon_file,
        out_parquet_path=args.output,
        quadkey_polygons=args.quadkey_polygons,
    )


if __name__ == "__main__":
    main()
