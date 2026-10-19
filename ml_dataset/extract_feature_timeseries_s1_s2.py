"""Extract Sentinel-1/2 feature time series for each field geometry."""

# from __future__ import annotations

import argparse
import functools
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
from tqdm.contrib.concurrent import process_map

from satellite_image_processing.scripts.utils import Modality
from extract_feature_timeseries import (
    load_biomass_labels,
    load_quadkeys,
    get_geometries,
    get_timeseries_of_geometry,
    save_timeseries,
)



def filter_s1_timeseries_keep_max_pixelcount(df: pd.DataFrame,
                                             dt_seconds: int = 600,
                                             ) -> pd.DataFrame:
    """
    Collapse near-duplicate S1 rows per geometry: if two consecutive rows are within
    `dt_seconds`, keep the one with the higher `vv_db_pixelcount`.

    The DataFrame must contain: 'geometry', 'image_date', 'vv_db_pixelcount'.

    >>> import pandas as pd
    >>> df = pd.DataFrame({
    ...     "geometry": ["A","A","A","B","B"],
    ...     "image_date": [
    ...         "2021-01-01T10:00:00",
    ...         "2021-01-01T10:05:00",  # within 5 min of previous -> duplicate cluster with previous
    ...         "2021-01-01T10:20:00",  # 15 min gap -> new cluster
    ...         "2021-01-01T11:00:00",
    ...         "2021-01-01T11:09:00",  # within 9 min of previous -> duplicate cluster with previous
    ...     ],
    ...     "vv_db_pixelcount": [50, 60, 55, 5, 7],
    ... })
    >>> out = filter_s1_timeseries_keep_max_pixelcount(df, dt_seconds=600)
    >>> # Expect: for geometry A, keep 10:05 (pixelcount 60) and 10:20 (55); for B, keep 11:09 (7)
    >>> list(out[["geometry", "image_date", "vv_db_pixelcount"]].itertuples(index=False))
    ... # doctest: +NORMALIZE_WHITESPACE
    [Pandas(geometry='A', image_date=Timestamp('2021-01-01 10:20:00+0000', tz='UTC'), vv_db_pixelcount=55),
     Pandas(geometry='A', image_date=Timestamp('2021-01-01 10:05:00+0000', tz='UTC'), vv_db_pixelcount=60),
     Pandas(geometry='B', image_date=Timestamp('2021-01-01 11:09:00+0000', tz='UTC'), vv_db_pixelcount=7)]

    Also check behavior when a gap exceeds the threshold: both rows are kept.

    >>> df2 = pd.DataFrame({
    ...     "geometry": ["C","C"],
    ...     "image_date": ["2021-01-02T08:00:00", "2021-01-02T08:20:01"],  # 1201s gap (> 600s)
    ...     "vv_db_pixelcount": [10, 20],
    ... })
    >>> out2 = filter_s1_timeseries_keep_max_pixelcount(df2, dt_seconds=600)
    >>> len(out2)
     """
    df = df.copy()
    df = df.sort_values(["geometry", "image_date"])

    df_grouped = df.groupby("geometry")
    df["prev_date"] = df_grouped["image_date"].shift()
    df["time_diff_sec"] = (df["image_date"] - df["prev_date"]).dt.total_seconds()
    dup_mask = (df["time_diff_sec"] <= dt_seconds)  # contains only the second occurence withing dt_seconds
    dup_mask |= dup_mask.groupby(df["geometry"]).shift(-1).astype("boolean").fillna(False)  # this also gets the first one

    # now, for each potential duplicate, get the one with the highest pixel count
    prev_dup = dup_mask.groupby(df["geometry"]).shift().fillna(False)
    dup_start = dup_mask & ~prev_dup # marks only the first row of each duplicate run
    df["_dup_group"] = dup_start.groupby(df["geometry"]).cumsum().where(dup_mask)

    # keep the highest pixelcount within each duplicate group
    best_idx = (
        df.loc[dup_mask]
        .groupby(["geometry", "_dup_group"])["vv_db_pixelcount"]
        .idxmax()
    )
    df_better_field_of_duplicates = df.loc[best_idx]

    df_filtered = pd.concat([df.loc[~dup_mask], df_better_field_of_duplicates])
    df_filtered = df_filtered.drop(columns=["prev_date", "time_diff_sec", "_dup_group"])
    return df_filtered


def add_s1_orbit_information_to_timeseries(image_id_file: Path, s1_df: pd.DataFrame):
    """Adds the orbit state (ascending/descending) and relative orbit to the dataframe."""
    assert image_id_file.is_file(), ("Not a file!", image_id_file)
    image_ids = gpd.read_file(image_id_file)
    needed_columns = ["item_id", "sat:orbit_state", "sat:relative_orbit"]
    assert set(needed_columns).issubset(set(image_ids.columns)), ("Required columns not found!", image_ids.columns)
    image_ids = image_ids[needed_columns]
    _s1_df = s1_df.copy()

    # item_id: S1A_IW_GRDH_1SDV_20200313T061511_20200313T061536_031653_03A602_1757_COG.SAFE
    image_ids["item_id_truncated"] = image_ids.item_id.apply(lambda s: s.replace(".SAFE", ""))
    _s1_df["image_name"] = _s1_df.image_path.apply(lambda p: Path(p).stem)
    # image_name: 0313130331_20170403_S1A_IW_GRDH_1SDV_20170403T062305_20170403T062330_015976_01A590_4792_COG_TF_TC
    _s1_df["image_item_id"] = _s1_df.image_name.apply(lambda s: s.split("_", maxsplit=2)[2].replace("_TF_TC", ""))

    merged = pd.merge(right=_s1_df, right_on="image_item_id", left=image_ids, left_on="item_id_truncated")
    assert len(merged["sat:orbit_state"].dropna()) == len(merged)
    out_columns = list(s1_df.columns) + ["sat:orbit_state", "sat:relative_orbit"]
    merged_selected = merged[out_columns]
    merged_gdf = gpd.GeoDataFrame(merged_selected)
    return merged_gdf


def filter_timeseries(
        df: pd.DataFrame,
        modality: Modality,
        dt_seconds: int = 600,
        ) -> pd.DataFrame:
    """Filter out S1 values, that are from the same datatake.
    why not filter with image id: because they are different because of the different sensing time start...
    """
    if modality == Modality.s2:
        return df
    df_filtered = filter_s1_timeseries_keep_max_pixelcount(df, dt_seconds)
    return df_filtered


def extract_s12_timeseries(
    labels_path: Path,
    processed_tiles_dir: Path,
    quadkey_polygon_file: Path,
    output_path: Path,
    s1_image_id_file: Optional[Path] = None,
    max_workers: int = 2,
) -> None:
    """Extract Sentinel‑1 and Sentinel‑2 time series for geometry in the provided labels file."""
    labels = load_biomass_labels(labels_path)
    quadkeys = load_quadkeys(quadkey_polygon_file)
    geometries = get_geometries(labels)
    geom_crs = labels.crs

    if s1_image_id_file and not s1_image_id_file.is_file():
        raise ValueError(f"provided s1_image_id_file does not exist: {s1_image_id_file}")

    requested_modality = {Modality.s1, Modality.s2}
    get_timeseries = functools.partial(
        get_timeseries_of_geometry,
        geom_crs=geom_crs,
        tiles_dir=processed_tiles_dir,
        quadkeys=quadkeys,
        used_modalities=requested_modality,
    )
    timeseries_lists = process_map(
        get_timeseries, geometries, max_workers=max_workers, chunksize=1
    )

    all_timeseries = []
    for timeseries in timeseries_lists:
        if timeseries:
            all_timeseries.extend(timeseries)

    if not all_timeseries:
        raise ValueError("No time series extracted for the provided geometries")

    timeseries_df = gpd.GeoDataFrame(all_timeseries, crs=labels.crs)
    timeseries_df["image_date"] = pd.to_datetime(timeseries_df["image_date"], utc=True)

    # Postprocessing and saving of timeseries
    output_path = Path(output_path)
    for modality in timeseries_df.modality.unique():
        modality_enum = Modality(modality)
        modality_df = timeseries_df[timeseries_df.modality == modality].copy()
        modality_df = filter_timeseries(modality_df, modality=modality_enum)
        modality_df = modality_df.dropna(how="all", axis=1)

        if modality_enum == Modality.s1 and s1_image_id_file:
            modality_df = add_s1_orbit_information_to_timeseries(
                s1_image_id_file, modality_df
            )

        modality_output_path = (
            output_path.parent / f"{output_path.stem}_{modality}{output_path.suffix}"
        )
        save_timeseries(modality_df, output_path=modality_output_path)
        print(f"Saved {modality} time series to {modality_output_path}")



def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser("Create timeseries dataset from processed S1/S2 images and field labels.")
    parser.add_argument("--labels", type=Path, required=True, help="GeoJSON with 'measurement_date' and geometry")
    parser.add_argument("--images-dir", type=Path, required=True, help="Directory with processed S1/S2 tiles")
    parser.add_argument("--quadkey-polygons", type=Path, required=True, help="Vectorfile, where the quadkey shapes are defined.")
    parser.add_argument("--output", type=Path, required=True, help="Path to output XXX file") # TODO
    parser.add_argument("--max-workers", type=int, default=4, help="Number of parallel jobs.")
    parser.add_argument("--s1-image-id-file", type=Path, default=None, required=False, help="Path to the file with the image ids to add the orbit direction and relative orbit")
    return parser.parse_args()


def main() -> None:
    args = get_args()
    extract_s12_timeseries(
        labels_path=args.labels,
        processed_tiles_dir=args.images_dir,
        quadkey_polygon_file=args.quadkey_polygons,
        s1_image_id_file=args.s1_image_id_file,
        output_path=args.output,
        max_workers=args.max_workers,
    )
    print(f"Finished extracting S1/S2 time series to base path {args.output}")


if __name__ == "__main__":
    main()
