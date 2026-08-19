import argparse
import warnings
from typing import Optional, List
from pathlib import Path

import cdsetool.query
import cdsetool.download
from cdsetool.query import query_features
from cdsetool.credentials import Credentials
from cdsetool.monitor import StatusMonitor
from datetime import date, datetime, timedelta
from tqdm import tqdm
from tqdm.contrib.concurrent import thread_map as tqdm_process_map
import geopandas as gpd


def search_feature_on_cdse(feature: gpd.GeoSeries) -> cdsetool.query.FeatureQuery:
    """Takes one feature and searches for it in the CDSE.

    Uses both the unique identifier, its geometry and the acquisition date, as this seemed to improve search performance.
    """
    image_id = feature["item_id"]
    sensor_id = image_id[:2]
    id_to_collection = {
        "S1": "Sentinel1",
        "S2": "Sentinel2",
    }
    collection = id_to_collection[sensor_id]
    image_date = feature["datetime"]
    if isinstance(image_date, str):
        image_date = datetime.fromisoformat(image_date)

    td = timedelta(hours=1)
    search_terms = {
        "productIdentifier": image_id,
        "startDate": image_date - td,
        "completionDate": image_date + td,
        "geometry": feature.geometry,
    }
    features = query_features(collection=collection, search_terms=search_terms)
    # features can be no, one, or multiple images
    return features


def download_images(input_shapefile: Path, download_dir: Path, dry_run: bool = False):
    """First searches for each image and then bulk downloads all of them.

    First querying all features allows for concurrent downloads with this tool. When one would just iterate
    over the areas, a single image would be downloaded before starting a new one. This could be fixed
    with introducing parallelism ourselves, but thats not great.
    """
    areas = gpd.read_file(input_shapefile)
    download_dir.mkdir(exist_ok=True, parents=True)
    creds = Credentials() # should be read from ~/.netrc
    download_options = {"concurrency": 4, "credentials": creds, "monitor": StatusMonitor()}

    # Get a FeatureQuery for all images in the geojson file
    features = [search_feature_on_cdse(idx_and_row[1]) for idx_and_row in areas.iterrows()]
    features_flat = list()
    tqdm_process_map(lambda f: features_flat.extend(f), features, max_workers=8)

    # and then downloading the images.
    if dry_run:
        print(f"Would download {len(features_flat)} images into {download_dir}.")
        image_ids = [f["properties"]["title"] for f in features_flat]
        print(f"First 3 images: {image_ids[:3]}")
    else:
        downloads = list(cdsetool.download.download_features(
            features_flat,  # this probably works by accident (we provide list, it accepts FeatureQuery)
            path=str(download_dir),
            options=download_options,
        ))
        print(f"Downloaded {len(downloads)} features:", downloads)


def get_and_check_args():
    parser = argparse.ArgumentParser(description="Download the scenes specified by their image IDs in the input shapefile.")
    parser.add_argument("--input-shapefile", help="Path to input shapefile or GeoJSON", type=Path, required=True)
    parser.add_argument("--download-dir", help="File to save the image IDs to", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="Don't actually download the data.")
    args = parser.parse_args()

    assert args.input_shapefile.is_file(), ("Input shapefile doesn't exists!", args.input_shapefile)
    return args

def main() -> None:
    args = get_and_check_args()
    if not Path("~/.netrc").expanduser().is_file():
        print("File '~/.netrc' should exists with the credentials for CDSE!")
        print("It has to be only accessible by the users, so run `chmod 0600 ~/.netrc` after creating this file.")
        print("Example content:")
        print("machine https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token")
        print("login EMAIL")
        print("password PASSWORD")
    download_images(input_shapefile=args.input_shapefile, download_dir=args.download_dir, dry_run=args.dry_run)

if __name__ == '__main__':
    main()