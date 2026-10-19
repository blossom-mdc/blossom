"""
Extract ERA5 weather timeseries for each provided geometry.

Expects the ERA5 data as a single .nc file for the whole area with all variables.
This file is created by merging the single-year single-variable files downloaded by
`satellite_image_processing/scripts/22_download_weather_era5.py`.
This merging was done using pandas in `notebooks/2025-09-09-era5-data-exploration.ipynb`.
"""
import argparse
from typing import Dict
from pathlib import Path
import xarray as xr
import geopandas as gpd
import numpy as np
import pandas as pd


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser("Create timeseries dataset from ERA5 data and field labels.")
    parser.add_argument("--labels", type=Path, required=True, help="GeoJSON with 'measurement_date' and geometry")
    parser.add_argument("--era5", type=Path, required=True, help=".nc file with ERA5 weather data")
    parser.add_argument("--output", type=Path, required=True, help="Path to output parquet file")
    return parser.parse_args()

# def get_weather_for_geometry_at_date(ds: xr.Dataset, geom, date) -> Dict[str, np.ndarray]:
#     longitude, latitude = tuple(geom.centroid.coords)[0]  # centroid is enough, because weather data is 9km resolution...
#     weather = ds.sel(latitude=latitude, longitude=longitude, method="nearest").sel(valid_time=date, method="ffill")
#     weather_vars = list(ds.data_vars)
#     weather_values_np = list(np.array(weather[weather_vars].to_dataarray()))
#     weather_values = map(lambda v: float(v), weather_values_np) # convert numpy float arrays with a single value to native python floats
#     weather_dict = {k: v for k, v in zip(weather_vars, weather_values)}
#     return weather_dict

def extract_save_era5_weather_of_labels(era5_file: Path, labels_file: Path, out_parquet_path: Path):
    assert era5_file.is_file(), ("Not a file!", era5_file)
    assert labels_file.is_file(), ("Not a file!", labels_file)

    era5_ds = xr.load_dataset(era5_file)
    labels = gpd.read_file(labels_file)
    #labels["centroid_coords"] = labels["geometry"].apply(lambda r: r.centroid.coords)

    weather_ts_list = list()
    fields = labels.geometry.unique()
    for field in fields:
        if len(field.centroid.coords) == 0:
            print(f"Skipping Polygon! {field=}")
            continue

        # use only centroids, because ERA5 resolution is large (kilometers) compared to the typical fields (<500m)
        longitude, latitude = tuple(field.centroid.coords)[0]
        weather_ts = era5_ds.sel(latitude=latitude, longitude=longitude, method="nearest")
        weather_ts = weather_ts.to_dataframe()
        weather_ts["geometry"] = field
        weather_ts_list.append(weather_ts)
    weather_ts_df = pd.concat(weather_ts_list)
    weather_ts_df = gpd.GeoDataFrame(weather_ts_df).set_geometry("geometry")
    print(weather_ts_df.shape)

    weather_ts_df.to_parquet(out_parquet_path)
    print(f"Wrote weather timeseries of {len(fields)} fields to '{out_parquet_path}'.")


def main() -> None:
    args = get_args()
    extract_save_era5_weather_of_labels(
        labels_file=args.labels,
        era5_file=args.era5,
        out_parquet_path=args.output,
        )


if __name__ == "__main__":
    main()
