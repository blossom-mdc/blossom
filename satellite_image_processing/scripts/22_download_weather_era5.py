"""Download ERA5 weather data for a list of places and dates.

This script downloads the daily statistics of the ERA5 weather data.
* data lag: 5 days to real time
* variables:
  * Mean surface downward short-wave radiation flux -> how much light is available for photosynthesis
  * Evaporation

Requirements:
* register ecmwf account
* get token and put it into  ~/.cdsapirc
* accept CC-BY licence in profile

Documentation:
* https://confluence.ecmwf.int/display/CKB/How+to+download+ERA5

"""

import argparse
import cdsapi
from pathlib import Path
from typing import List
from osgeo import gdal
import shutil

gdal.UseExceptions()


def set_crs_to_epsg4326(nc_path: Path):
    assert nc_path.is_file(), ("Not a file!", nc_path)
    nc_crs_path = nc_path.parent / (nc_path.name + "_epsg4326" + nc_path.suffix)
    shutil.copyfile(src=nc_path, dst=nc_crs_path)

    with gdal.Open(nc_crs_path, gdal.GA_Update) as ds:
        if ds is None:
            raise RuntimeError(f"Could not open {nc_path}")
        ds.SetProjection('EPSG:4326')
    print(f"CRS set to EPSG:4326 for {nc_path}")


def get_arguments():
    parser = argparse.ArgumentParser(
        description=__doc__,  # take the docstring
        formatter_class=argparse.RawDescriptionHelpFormatter,  # preserve newlines when printing with --help
    )
    parser.add_argument("--download-dir", help="File to save the image IDs to", type=Path, required=True)
    parser.add_argument("--years", help="For which years to download data", type=int, nargs="+")
    return parser.parse_args()


def get_weather_data(download_dir: Path, years: List[str]
    ):
    """
    example usage:
    poetry run python3 satellite_image_processing/scripts/22_download_weather_era5.py
    """
    client = cdsapi.Client()
    # https://cds.climate.copernicus.eu/datasets/derived-era5-single-levels-daily-statistics?tab=overview
    dataset = "derived-era5-single-levels-daily-statistics"
    area_british_isles = [60, -10.8, 49.9, 1.8] # north, west, south, east
    requests_generic = {
        "product_type": "reanalysis", # other option: "ensemble_members", "ensemble_mean"
        "day": list(map(str, range(1, 32))),
        "month": list(map(str, range(1, 13))),
        "time_zone": "utc+00:00",
        "frequency": "1_hourly",
        "area": area_british_isles,
        "format": "netcdf",
    }


    # maybe also:
    # Mean evaporation rate (amount of water that has evaporated from the Earth's surface, including a simplified
    #      representation of transpiration (from vegetation) ...
    # some solar flux?

    variables_statistics = [
        # (output filename, era5 variable name, daily aggregation method)
        ("2m_temperature-min", "2m_temperature", "daily_minimum"),
        ("2m_temperature-max", "2m_temperature", "daily_maximum"),
        ("soil_temperature_level_1-min", "soil_temperature_level_1", "daily_minimum"),
        ("soil_temperature_level_1-max", "soil_temperature_level_1", "daily_maximum"),
        ("volumetric_soil_water_layer_1-mean", "volumetric_soil_water_layer_1", "daily_mean"),
        ("skin_reservoir_content-mean", "skin_reservoir_content", "daily_mean"),
        ("precipitation-sum", ["total_precipitation"], "daily_sum"),
        ("mean_surface_downward_short_wave_radiation_flux-mean", "mean_surface_downward_short_wave_radiation_flux", "daily_mean"),
        ("evaporation-sum", "evaporation", "daily_sum"),
    ]
    for year in years:
        request = requests_generic.copy()
        for variables_shortname, variables_to_download, statistics in variables_statistics:

            output_name = f"era5-reanalysis-british-isles-{year}-{variables_shortname}.nc"
            output_path = download_dir / output_name
            if output_path.is_file():
                print(f"{variables_shortname} of {year} already downloaded. Skipping! ({str(output_path)})")
                continue

            request.update({
                "variable": variables_to_download,
                "year": [year],
                "daily_statistic": statistics,

            })
            client.retrieve(dataset, request).download(output_path)
            set_crs_to_epsg4326(nc_path=output_path)

def main():
    args = get_arguments()
    get_weather_data(download_dir=args.download_dir, years=args.years)


if __name__ == "__main__":
    main()
