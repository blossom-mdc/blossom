#!/usr/bin/env python3
"""
This script takes a geojson file and gets all the Sentinel-1 and -2 image ids to download.

It expects each geojson feature to have a valid geometry and two properties `date_start` and `date_end`.
With these, the SentinelHub cataloge is searched and the retrieved images are stored.
"""
import argparse
import datetime
from typing import List, Dict

import pandas as pd
import geopandas as gpd
import json

from tqdm.auto import tqdm
from shapely import Polygon, MultiPolygon
from shapely.geometry import shape
import sentinelhub
from sentinelhub import SentinelHubCatalog, SHConfig, CRS
from pathlib import Path


def check_sentinelhub_config_existing():
    shconfig_path = SHConfig.get_config_location()
    if not Path(shconfig_path).is_file():
        msg = f"The config file for sentinelhub does not exist!\n"
        msg += f"Expected at {shconfig_path}.\n"
        msg += "\n"
        msg += "Example content:\n"
        msg += '[default-profile]\n'
        msg += 'sh_client_id = "YYY"\n'
        msg += 'sh_client_secret = "XXX"\n'
        msg += 'sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"\n'
        msg += 'sh_base_url = "https://sh.dataspace.copernicus.eu"\n'
        print(msg)
        exit(-1)


def query_collections():
    """Returns the name of all available collections in the catalog."""
    sh_config = SHConfig()
    catalog = SentinelHubCatalog(config=sh_config)
    collections = catalog.get_collections()
    collection_ids = [c["id"] for c in collections]
    return collection_ids

def remove_duplicate_image_ids(results: List[dict]) -> List[dict]:
    """
    Remove results that are basically the same images/duplicates.

    Sometimes there are two images with the same data (same place, same aquisition time) but slight differences in
    the processing, which results in changes of the `s1:timeliness` or the Bbox.
    eg: [-6.153850964998745, 49.508187739360935, -2.2132492291147043,  51.51258563359161] vs
        [-6.153832635566234, 49.50818416828538,  -2.2132317111269675,  51.51258246851211]


    The dicts in `results` look like this:
    ```json
    {'stac_version': '1.0.0',
     'stac_extensions': [...],
     'id': 'S1A_IW_GRDH_1SDV_20150307T174855_20150307T174920_004929_006280_45B1_COG.SAFE',
     'type': 'Feature',
     'geometry': {...},
     'bbox': [...],
     'properties': {'datetime': '2015-03-07T17:48:55Z',
      'platform': 'sentinel-1a',
      'sat:orbit_state': 'ascending',
      'sar:product_type': 'GRD',
      's1:timeliness': 'Fast24h',
      ...
      },
     'assets': {...},
     'collection': 'sentinel-1-grd',
     'links': [...]}
    ```
    """
    # item ids differ by the CRC sum:
    # S1A_IW_GRDH_1SDV_20180921T180538_20180921T180607_023800_0298AF_8796_COG.SAFE vs.
    # S1A_IW_GRDH_1SDV_20180921T180538_20180921T180607_023800_0298AF_7EB0_COG.SAFE

    # so, first gather all the results with the same id ignoring the CRC.
    groups: Dict[str, List[Dict]] = {}
    for item in results:
        # split off the CRC and the trailing suffix
        prefix = item["id"].rsplit("_", 2)[0]
        groups.setdefault(prefix, []).append(item)

    deduped: List[Dict] = []
    for prefix, items in groups.items():
        if len(items) == 1: # no duplicate
            deduped.append(items[0])
            continue

        # take the item with the best s1:timeliness, which can be one of [ArchNormal, Reprocessing, Offline,
        # Fast24h, NRT3h, NRT1h, NRT10m]. As I don't know what Reprocessing and Offline, ignore them.
        # this works by first sorting all items by their timeliness...
        timelinesses = ["ArchNormal", "Fast24h", "NRT3h", "NRT1h", "NRT10m"]
        t_ranks = {t: i for i, t in enumerate(timelinesses)}
        items_sorted = sorted(items,  # sorted() returns ascending order.
            key=lambda it: t_ranks.get(it["properties"].get("s1:timeliness"), len(t_ranks))
        )
        # ...and then taking the first one.
        deduped.append(items_sorted[0])
    return deduped

def query_metadata(input_path: Path, output_path: Path, stac_collections: list, stac_collection_properties: dict, date_start=None, date_end=None, max_allowed_cloud_coverage: float = 70, prelook_period_days: int = 14,
):
    assert input_path.is_file(), ("Not a file!", input_path)
    sh_config = SHConfig()
    catalog = SentinelHubCatalog(config=sh_config)
    areas = gpd.read_file(input_path)
    metadata_records = list()

    num_collections = len(stac_collections)
    num_areas = len(areas)
    pbar = tqdm(total=num_collections * num_areas)

    if date_start:
        date_start = datetime.datetime.fromisoformat(date_start)

    if date_end:
        date_end = datetime.datetime.fromisoformat(date_end)


    # for each collection
    # look at all areas and get the bbox
    for collection in stac_collections:
        properties_to_save = stac_collection_properties[collection]
        for idx, area in areas.iterrows():
            geometry = sentinelhub.Geometry(area.geometry, crs=CRS.WGS84)

            # IMPROVEMENT: document/remove hard coded column name
            _date_start = date_start if date_start else area["date_start"]
            _date_end = date_end if date_end else area["date_end"]

            # the start date is defined as the first biomass measurement
            # as satellite already before this date should be downloaded, set the start date a little earlier
            # using that, images N days before the first measurement are also regarded.
            _date_start = _date_start - pd.Timedelta(prelook_period_days, unit="days")

            time_range = _date_start, _date_end

            search_args = {
                "geometry": geometry,
                "time": time_range,
            }
            if "sentinel-2" in collection:
                search_args["filter"] = f"eo:cloud_cover<{max_allowed_cloud_coverage}"
            if "sentinel-1" in collection:
                search_args["filter"] = f"s1:polarization = 'DV' and sar:instrument_mode = 'IW'"

            search_iter = catalog.search(collection=collection, **search_args)

            results = list(search_iter)
            if "sentinel-1" in collection:
                results = remove_duplicate_image_ids(results)

            for item in results:
                properties = {
                    "area_id": idx,
                    "item_id": item["id"],
                    "collection": collection,
                }
                if "sentinel-1" in collection:
                    assert item["properties"]["sar:polarizations"] == ["VV", "VH"], ("Wrong polarizations!", item["properties"]["sar:polarizations"])
                additional_properties = {p: item['properties'][p] for p in properties_to_save}
                properties.update(**additional_properties)
                geometry = shape(item["geometry"])
                if isinstance(geometry, Polygon):
                    geometry = MultiPolygon([geometry])
                properties["geometry"] = geometry
                metadata_records.append(properties)
            pbar.update()
    pbar.close()

    gdf = gpd.GeoDataFrame(metadata_records, crs="EPSG:4326")
    gdf = gdf.drop_duplicates("item_id")  # Remove duplicate image ids.

    # also to be sure: truncate the item_id by the CRC sum and check that no duplicate is found

    s1 = gdf[gdf.collection == "sentinel-1-grd"].copy()
    s1["base_id"] = s1["item_id"].str.rsplit("_", n=2).str[0]
    dup_mask = s1["base_id"].duplicated(keep=False)
    n_dups = dup_mask.sum()
    assert n_dups == 0, (n_dups, "Duplicates with just differing CRC found!", s1[dup_mask])

    duplicate_count = len(metadata_records) - len(gdf)

    with open(output_path, "w") as f:
        f.write(gdf.to_json(na="drop", indent=2))

    print(f"Saved image IDs to {output_path} with {len(gdf)} features ({duplicate_count} duplicates removed).")


def parse_and_check_args():
    parser = argparse.ArgumentParser(description="Get image IDs and metadata of Sentinel-1/-2 for areas and durations defined in a geojson file.")
    parser.add_argument("--input-shapes", help="Path to input shapefile or GeoJSON", type=Path, required=True)
    parser.add_argument("--output-path", help="File to save the image IDs to", type=Path, required=True)
    parser.add_argument("--stac-api", default="https://catalogue.dataspace.copernicus.eu/stac")
    parser.add_argument("--max-allowed-cloud-coverage", default=70, help="Only S2 scenes clouded less than this value will be used.")
    parser.add_argument("--date-start", help="Overwrite start date of geojson features.", default=None)
    parser.add_argument("--date-end", help="Overwrite end date of geojson features.", default=None)
    parser.add_argument("--prelook-period-days", type=int, default=14, help="How many days before the first biomass measurement satellite data should be included.")
    args = parser.parse_args()

    assert args.input_shapes.is_file(), ("Input Shapefile not found!", args.input_shapes)
    assert args.output_path.suffix == ".geojson", ("Output path should end with .geojson!", args.output_path)
    return args


def main() -> None:
    stac_collections = ["sentinel-2-l2a", "sentinel-1-grd"]
    stac_collection_properties = {
        "sentinel-2-l2a": ["datetime", "platform", "constellation", "gsd", "eo:cloud_cover", "proj:epsg"],
        "sentinel-1-grd": ["datetime", "platform", "constellation", "sat:orbit_state", "sat:relative_orbit", "sar:instrument_mode",
                           "sar:product_type"],
    }
    args = parse_and_check_args()

    check_sentinelhub_config_existing()
    available_collections = query_collections()
    for coll_name in stac_collection_properties.keys():
        assert coll_name in available_collections, ("Requested collection not available.", coll_name, available_collections)
    query_metadata(
        input_path=args.input_shapes,
        output_path=args.output_path,
        stac_collections=stac_collections,
        stac_collection_properties=stac_collection_properties,
        date_start=args.date_start,
        date_end=args.date_end,
        max_allowed_cloud_coverage=args.max_allowed_cloud_coverage,
        prelook_period_days=args.prelook_period_days,
    )


if __name__ == '__main__':
    main()
