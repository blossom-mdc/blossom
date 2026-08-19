"""
python3 satellite_image_processing/scripts/23_download_dem.py \
    --input-shape /home/tom/scratch/blossom/biomass-measurements/2025-08-25-timeseries-dataset-example-quadkey0313130331.geojson \
    --download-dir /home/tom/scratch/blossom/dem-data
"""

import argparse
from pathlib import Path

import cdsetool.download
from cdsetool.query import query_features
from cdsetool.credentials import Credentials
from cdsetool.monitor import StatusMonitor
from tqdm.contrib.concurrent import thread_map as tqdm_process_map
import geopandas as gpd


def download_dem(input_shapefile: Path, download_dir: Path, dry_run: bool = False):
    """First searches for each image and then bulk downloads all of them.

    First querying all features allows for concurrent downloads with this tool. When one would just iterate
    over the areas, a single image would be downloaded before starting a new one. This could be fixed
    """
    areas = gpd.read_file(input_shapefile)
    geometries = areas.geometry.unique()
    geometries = geometries.buffer(0.1).simplify(0.01)  # to reduce geometry size when querying

    download_dir.mkdir(exist_ok=True, parents=True)
    creds = Credentials() # should be read from ~/.netrc
    download_options = {"concurrency": 4, "credentials": creds, "monitor": StatusMonitor()}

    collection = "COP-DEM"
    def _search(geometry):
        search_terms = {"maxRecords": "200", "resolution": "30", "productType": "DGE_30", "geometry": geometry}
        return query_features(collection, search_terms)

    # Get a FeatureQuery for all images in the geojson file
    features = [_search(geom) for geom in geometries]
    features_flat = list()
    tqdm_process_map(lambda f: features_flat.extend(f), features, max_workers=8)

    features_filtered = {f["id"]: f for f in features_flat}  # keeps only one of each id
    features_filtered = list(features_filtered.values())

    # downloading the images
    downloads = list(cdsetool.download.download_features(
        features_filtered,
        path=str(download_dir),
        options=download_options,
    ))
    print(f"Downloaded {len(downloads)} features:", downloads)


def get_and_check_args():
    parser = argparse.ArgumentParser(description="Download the scenes specified by their image IDs in the input shapefile.")
    parser.add_argument("--input-shapefile", help="Path to input shapefile or GeoJSON", type=Path, required=True)
    parser.add_argument("--download-dir", help="File to save the image IDs to", type=Path, required=True)
    args = parser.parse_args()

    assert args.input_shapefile.is_file(), ("Input shapefile doesn't exists!", args.input_shapefile)
    return args

def main() -> None:
    args = get_and_check_args()
    if not Path("~/.netrc").expanduser().is_file():
        print("File '~/.netrc' should exists with the credentials for CDSE!")
        print("Example content:")
        print("machine https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token")
        print("login EMAIL")
        print("password PASSWORD")
    download_dem(input_shapefile=args.input_shapefile, download_dir=args.download_dir)

if __name__ == '__main__':
    main()