"""Download soil moisture data from the CDSE.

Soil moisture is used from the Copernicus Land Monitoring Service (CLMS).

data options:
* ssm_europe_1km_daily_v1
* soil water index

The password can be provided using the 'CDSE_PASSWORD' environment variable. If not provided, ask for it
interactively.
CDSE_PASSWORD=secret_password python3 23_query_download_clms_soil_moisture.py ...
"""

import argparse
import getpass
import json
import os
import zipfile
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import List, Optional

import geopandas as gpd
import pandas as pd
import requests
import shapely
from tqdm.auto import tqdm

TOKEN_PATH = Path.home() / ".config" / "cdse_token.json"
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"


def refresh_token(refresh_expires_at_str: str, refresh_token: str) -> Optional[str]:
    now = datetime.utcnow()
    refresh_expires_at_str = datetime.fromisoformat(refresh_expires_at_str)
    if refresh_expires_at_str > now:
        request_data = {
            "grant_type": "refresh_token",
            "client_id": "cdse-public",
            "refresh_token": refresh_token,
        }
        r = requests.post(TOKEN_URL, data=request_data)
        if r.ok:
            token_data = r.json()
            expires_at = now + timedelta(seconds=int(token_data["expires_in"]))
            refresh_expires_at_str = now + timedelta(seconds=int(token_data.get("refresh_expires_in", 0)))
            TOKEN_PATH.write_text(json.dumps({
                "access_token": token_data["access_token"],
                "refresh_token": token_data["refresh_token"],
                "expires_at": expires_at.isoformat(),
                "refresh_expires_at": refresh_expires_at_str.isoformat(),
            }))
            return token_data["access_token"]
    return None


def request_new_access_token(loginname: Optional[str]) -> str:
    if not loginname:
        loginname = input("Login name for CDSE:")
    if "CDSE_PASSWORD" in os.environ:
        password = os.getenv("CDSE_PASSWORD")
    else:
        password = getpass.getpass("Password for CDSE:")

    request_data = {
        "grant_type": "password",
        "client_id": "cdse-public",
        "username": loginname,
        "password": password,
    }
    r = requests.post(TOKEN_URL, data=request_data)
    r.raise_for_status()
    token_data = r.json()
    now = datetime.utcnow()
    TOKEN_PATH.write_text(json.dumps({
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": (now + timedelta(seconds=int(token_data["expires_in"]))).isoformat(),
        "refresh_expires_at": (now + timedelta(seconds=int(token_data.get("refresh_expires_in", 0)))).isoformat(),
    }))
    return token_data["access_token"]


def get_access_token(loginname: Optional[str]) -> str:
    """Retrieve or refresh CDSE access token with refresh token support."""
    if TOKEN_PATH.exists():
        data = json.loads(TOKEN_PATH.read_text())
        now = datetime.utcnow()

        # access token still valid?
        if datetime.fromisoformat(data["expires_at"]) > now:
            return data["access_token"]

        # refresh token still valid? → refresh
        if "refresh_token" in data and "refresh_expires_at" in data:
            maybe_access_token = refresh_token(refresh_expires_at_str=data["refresh_expires_at"],
                                               refresh_token=data["refresh_token"])
            if maybe_access_token:
                return maybe_access_token

    access_token = request_new_access_token(loginname)
    return access_token


def download(product_url: str, output_path: Path, loginname: Optional[str] = None) -> Optional[Path]:
    """Download file with proper name and skipping if already present."""
    if output_path.exists():
        print(f"Skipping {output_path}, already downloaded.")
        return None
    # the token can expire when downloading many images
    access_token = get_access_token(loginname=loginname)

    headers = {"Authorization": f"Bearer {access_token}"}
    with requests.get(product_url, headers=headers, stream=True) as r:
        if r.status_code == 200:
            with open(output_path, "wb") as f:
                # use chunked reading, to use less memory
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                return output_path
        else:
            print(f"Failed to download {product_url}: {r.status_code} {r.text}")
    return None


def get_ids(date_start: datetime,
            date_end: datetime,
            geom: shapely.Polygon,
            loginname: Optional[str] = None,
            dataset_name: str = "swi_europe_1km_daily_v1",
            ) -> pd.DataFrame:
    access_token = get_access_token(loginname=loginname)

    # Nice to have: filter for the mediaContentType to be tiff, not netcdf. but it was not straightforward, so this
    # is added in a later step.
    filter = (
        "Collection/Name eq 'CLMS' "
        f"and Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'datasetIdentifier' and att/OData.CSC.StringAttribute/Value eq '{dataset_name}') "
        f"and ContentDate/Start gt {date_start.isoformat(timespec='milliseconds')}Z "  # todo check if the Z is need and what it means
        f"and ContentDate/Start lt {date_end.isoformat(timespec='milliseconds')}Z "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{str(geom)}')"
    )

    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter={filter}&$top=200"  # top default: 20, max: 1000
    all_items = []

    # the API use pagination. if there is a nextLink set, request it too. if nextLink is empty, we got all data.
    while url:
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
        all_items.extend(data.get("value", []))
        url = data.get("@odata.nextLink")

    df = pd.DataFrame.from_dict(all_items)
    df_tif = df[df["@odata.mediaContentType"] == "application/tiff"]
    print(f"Found {len(df_tif)} tifs for {date_start.year}.")
    assert "Id" in df_tif, ("Column 'Id' is expected!", df_tif.columns)
    assert "Name" in df_tif, ("Column 'Name' is expected!", df_tif.columns)
    return df_tif


def get_soil_moisture_images(
        download_dir: Path,
        loginname: Optional[str],
        date_start: datetime,
        date_end: datetime,
        geom: shapely.Polygon,
        unzipped_dir: Optional[Path],
        dataset_name: str = "swi_europe_1km_daily_v1",  # Soil Water Index
    ):
    download_dir.mkdir(exist_ok=True, parents=True)
    if unzipped_dir: unzipped_dir.mkdir(exist_ok=True, parents=True)


    product_ids = get_ids(date_start, date_end, geom, dataset_name=dataset_name, loginname=loginname)

    for i, row in tqdm(product_ids.iterrows()):
        pid = row.Id
        pname = row.Name
        product_url = f"https://download.dataspace.copernicus.eu/odata/v1/Products({pid})/$value"
        output_path = download_dir / f"{pid}_{pname}.zip"
        download(product_url, output_path=output_path, loginname=loginname)

def get_arguments():
    parser = argparse.ArgumentParser(
        description=__doc__,  # take the docstring
        formatter_class=argparse.RawDescriptionHelpFormatter,  # preserve newlines when printing with --help
    )
    parser.add_argument("--polygon-file", help="Path to input shapefile or GeoJSON", type=Path, required=True)
    parser.add_argument("--download-dir", help="File to save the image IDs to", type=Path, required=True)
    parser.add_argument("--unzipped-dir", help="Where to unzip the images to", type=Path, required=False)
    #        date_start = datetime(year, 1, 1, 0, 0, 0)
    # date_end = datetime(year, 12, 31, 23, 59, 59)
    parser.add_argument("--date-start", help="First date to download data for", type=datetime.fromisoformat, default="2024-06-01")
    parser.add_argument("--date-end", help="Last date to download data for", type=datetime.fromisoformat, default="2024-06-15")
    parser.add_argument("--loginname", help="Loginname for CDSE", type=str, required=False)
    datasets = ["swi_europe_1km_daily_v1",  # Soil Water Index
                "ssm_europe_1km_daily_v1",  # S1 Surface Soil Moisture
                ]
    parser.add_argument("--dataset", choices=datasets, help="Which dataset to download.", default="swi_europe_1km_daily_v1")
    return parser.parse_args()


def main():
    args = get_arguments()

    polygons = gpd.read_file(args.polygon_file)
    assert polygons.crs.to_epsg() == 4326
    polygon = polygons.geometry.unique().union_all()
    # the polygon can be big with many points. this can lead to problems with the API
    # therefore: simplify. tolerance in CRS units, so lat/lon. was tried for one example and worked.
    polygon = polygon.simplify(tolerance=0.001)

    get_soil_moisture_images(
        download_dir=args.download_dir,
        date_start=args.date_start,
        date_end=args.date_end,
        geom=polygon,
        unzipped_dir=args.unzipped_dir,
        loginname=args.loginname,
        dataset_name=args.dataset,
    )


if __name__ == "__main__":
    main()
