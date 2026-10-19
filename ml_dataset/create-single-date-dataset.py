import marimo

__generated_with = "0.18.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import argparse
    from pathlib import Path
    import pandas as pd
    import geopandas as gpd
    from datetime import timedelta
    import seaborn as sns
    return Path, argparse, gpd, mo, pd, timedelta


@app.cell
def _():
    ALLOWED_TEMPORAL_DISTANCE_DAYS = 2
    return (ALLOWED_TEMPORAL_DISTANCE_DAYS,)


@app.cell
def _():
    default_dataset = "all-buffer10"
    ts_dir_template = f"/data/outputs-snakemake/timeseries-{default_dataset}-{{}}.parquet"
    return default_dataset, ts_dir_template


@app.cell
def _(mo):
    default_out_dir = mo.ui.text(
        label="Output directory path",
        full_width=True,
        value="/data/snakemake-outputs/",
    )

    mo.vstack([default_out_dir])
    return (default_out_dir,)


@app.cell
def _(Path, argparse, default_dataset, default_out_dir, ts_dir_template):
    parser = argparse.ArgumentParser()
    parser.add_argument("--s1-input", default=ts_dir_template.format("s1-s2_s1"), type=Path)
    parser.add_argument("--s2-input", default=ts_dir_template.format("s1-s2_s2"), type=Path)
    parser.add_argument("--swi-input", default=ts_dir_template.format("swi"), type=Path)
    parser.add_argument("--era5-input", default=ts_dir_template.format("era5"), type=Path)
    parser.add_argument("--labels-input", default=ts_dir_template.format("labels-dem"), type=Path)
    parser.add_argument("--output-dir", 
                        default=Path(default_out_dir.value), 
                        type=Path)
    parser.add_argument("--dataset-name", default=default_dataset)
    args = parser.parse_args()

    print(f"Creating dataset of dataset '{args.dataset_name}'.")
    print(f"using timeseries like {args.s1_input} and labels from {args.labels_input}.")
    print("Saving to:", args.output_dir)

    assert args.s1_input.is_file(), ("Not a file!", args.s1_input)
    assert args.s2_input.is_file(), ("Not a file!", args.s2_input)
    assert args.swi_input.is_file(), ("Not a file!", args.swi_input)
    assert args.era5_input.is_file(), ("Not a file!", args.era5_input)
    assert args.labels_input.is_file(), ("Not a file!", args.labels_input)
    return (args,)


@app.cell
def _(mo):
    mo.md(r"""
    ## Load and merge time series data
    """)
    return


@app.cell
def _(args, gpd, pd):
    ts_label = gpd.read_parquet(args.labels_input)
    ts_s1 = gpd.read_parquet(args.s1_input)
    ts_s2 = gpd.read_parquet(args.s2_input)
    ts_swi = gpd.read_parquet(args.swi_input)
    ts_era5 = gpd.read_parquet(args.era5_input)

    print(f"Advance biomass measurement time by 12 hours if it is at midngith to have it a noon instead.")
    midnight_measurements = (ts_label.measurement_date.dt.hour != 0)
    shifted_measurement_times = ts_label.measurement_date[midnight_measurements] + pd.Timedelta(hours=12)
    ts_label.measurement_date = ts_label.measurement_date.where(~midnight_measurements, other=shifted_measurement_times)

    if ts_label.measurement_date.dt.tz is None:
        ts_label.measurement_date = ts_label.measurement_date.dt.tz_localize("utc")
    return ts_era5, ts_label, ts_s1, ts_s2, ts_swi


@app.cell
def _(gpd, pd):
    def merge_s1_with_soil_moisture(
        s1_df: gpd.GeoDataFrame, 
        sm_df: gpd.GeoDataFrame
    ) -> gpd.GeoDataFrame:
        s1_df.image_date = pd.to_datetime(s1_df.image_date, utc=True)
        sm_df.date = pd.to_datetime(sm_df.date, utc=True)

        s1_df = s1_df.sort_values("image_date")
        sm_df = sm_df.sort_values("date")

        s1_merged = pd.merge_asof(left=s1_df, right=sm_df, left_on="image_date", right_on="date", direction="nearest", suffixes=("", "_sm"))
        s1_merged_cols = set(s1_merged.columns) - {"geometry_sm"}
        s1_merged = s1_merged[list(s1_merged_cols)]
        print(f"Merged S1 with SWI. shape changed from {s1_merged.shape} to {s1_df.shape}.")
        return s1_merged
    return (merge_s1_with_soil_moisture,)


@app.cell
def _(merge_s1_with_soil_moisture, ts_s1, ts_swi):
    s1_sm_merged = merge_s1_with_soil_moisture(s1_df=ts_s1, sm_df=ts_swi)
    return (s1_sm_merged,)


@app.cell
def _(gpd, pd):
    def merge_labels_with_era5weather(
        labels_df: gpd.GeoDataFrame, 
        era5_df: gpd.GeoDataFrame
    ) -> gpd.GeoDataFrame:
        # s1_df.image_date = pd.to_datetime(s1_df.image_date, utc=True)
        # sm_df.date = pd.to_datetime(sm_df.date, utc=True)

        labels_df = labels_df.sort_values("measurement_date")
        era5_df = era5_df.sort_index()

        if labels_df.measurement_date.dtype != era5_df.index.dtype:
            era5_df.index = era5_df.index.tz_localize("utc")
            era5_df.index = era5_df.index.astype(labels_df.measurement_date.dtype)

        labels_merged = pd.merge_asof(left=labels_df, right=era5_df, 
                                      left_on="measurement_date", right_on="valid_time", direction="nearest", suffixes=("", "_era5"))

        labels_merged_cols = set(labels_merged.columns) - {"geometry_era5"}
        labels_merged = labels_merged[list(labels_merged_cols)]
        print(f"merged labels with weather. shape: {labels_df.shape} -> {labels_merged.shape}")
        return labels_merged
    return (merge_labels_with_era5weather,)


@app.cell
def _(merge_labels_with_era5weather, ts_era5, ts_label):
    ts_labels_with_era5 = merge_labels_with_era5weather(
        labels_df=ts_label, 
        era5_df=ts_era5,
    )
    return (ts_labels_with_era5,)


@app.cell
def _(gpd):
    def add_geometry_hash_column(df: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        assert "geometry" in df.columns
        df_geomhash = df.copy()
        df_geomhash["geometry_hash"] = df_geomhash.geometry.apply(lambda g: hash(g))
        return df_geomhash
    return (add_geometry_hash_column,)


@app.cell
def _(add_geometry_hash_column, s1_sm_merged, ts_labels_with_era5):
    # performance optimization: use geometry hash instead of raw geometry
    ts_label_filtered_geomhash = add_geometry_hash_column(ts_labels_with_era5)
    s1_sm_merged_geomhash = add_geometry_hash_column(s1_sm_merged)
    return s1_sm_merged_geomhash, ts_label_filtered_geomhash


@app.cell
def _(mo):
    mo.md(r"""
    ## Create single modalitiy datasets
    """)
    return


@app.cell
def _(ALLOWED_TEMPORAL_DISTANCE_DAYS, gpd, mo, pd, timedelta):
    def join_biomass_with_satellite_data(bm_df, rs_df):
        assert "geometry_hash" in bm_df.columns
        assert "geometry_hash" in rs_df.columns
        dt = timedelta(days=ALLOWED_TEMPORAL_DISTANCE_DAYS)

        out_ds = list()
        for row_idx, row in mo.status.progress_bar(bm_df.iterrows(), total=len(bm_df)):
            bm_date = row.measurement_date
            bm_geom_hash = row.geometry_hash
            row.name = "biomass"

            rs_filt = rs_df[rs_df.geometry_hash == bm_geom_hash]
            date_start = (bm_date - dt)
            date_end = (bm_date + dt)
            if date_start.tz is None:
                date_start = date_start.tz_localize("UTC")
            if date_end.tz is None:
                date_end = date_end.tz_localize("UTC")

            rs_filt = rs_filt[(rs_filt.image_date > date_start) & (rs_filt.image_date < date_end)]

            assert len(rs_filt) < 10, ("Unexpected many data pairs.", len(rs_filt))
            if len(rs_filt) == 0:  # no satellite data recent to biomass measurement
                continue
            assert len(rs_filt) >= 1
            if len(rs_filt.geometry.unique()) == 0: 
                continue
            assert row.geometry == rs_filt.geometry.unique()

            row_as_df = pd.DataFrame([row] * len(rs_filt)).reset_index(drop=True)
            columns_to_copy = set(row_as_df.columns) - {"geometry", "geometry_hash"} # remove geometry columns
            row_as_df = row_as_df[list(columns_to_copy)]
            rs_filt_bm = rs_filt.reset_index(drop=True).join(row_as_df)

            ## this is a another approach that should work the same as the join()-based approach
            # both row and rs_filt are already filtered for geom and date
            # rs_filt_bm = pd.DataFrame([row]).drop("geometry", axis="columns").merge(rs_filt, on="geometry_hash", suffixes=("", "_rs"))
            # rs_filt_bm = rs_filt_bm.reset_index(drop=True)

            out_ds.append(rs_filt_bm)
        bm_satellite_ds = pd.concat(out_ds)
        bm_satellite_ds = gpd.GeoDataFrame(bm_satellite_ds)
        return bm_satellite_ds
    return (join_biomass_with_satellite_data,)


@app.cell
def _(
    Path,
    args,
    join_biomass_with_satellite_data,
    s1_sm_merged_geomhash,
    ts_label_filtered_geomhash,
):
    bm_s1_ds = join_biomass_with_satellite_data(bm_df=ts_label_filtered_geomhash, rs_df=s1_sm_merged_geomhash)
    out_path_bm_s1 = Path(args.output_dir) / f"dataset-{args.dataset_name}-paired-biomass-s1-single-date.parquet"
    bm_s1_ds.to_parquet(out_path_bm_s1)
    print(f"Saved {len(bm_s1_ds)} S1-BM pairs to '{out_path_bm_s1}'.")
    return (bm_s1_ds,)


@app.cell
def _(
    Path,
    add_geometry_hash_column,
    args,
    join_biomass_with_satellite_data,
    ts_label_filtered_geomhash,
    ts_s2,
):
    s2_geomhash = add_geometry_hash_column(ts_s2)
    bm_s2_ds = join_biomass_with_satellite_data(bm_df=ts_label_filtered_geomhash, rs_df=s2_geomhash)
    out_path_bm_s2 = Path(args.output_dir) / f"dataset-{args.dataset_name}-paired-biomass-s2-single-date.parquet"
    bm_s2_ds.to_parquet(out_path_bm_s2)
    print(f"Saved {len(bm_s2_ds)} S2-BM pairs to '{out_path_bm_s2}'.")
    return bm_s2_ds, s2_geomhash


@app.cell
def _(mo):
    mo.md(r"""
    ## SAR+optical dataset
    """)
    return


@app.cell
def _(ALLOWED_TEMPORAL_DISTANCE_DAYS, gpd, mo, pd, timedelta):
    def join_biomass_with_s1_s2_data(bm_df, s1_df, s2_df):
        assert "geometry_hash" in bm_df.columns
        assert "geometry_hash" in s1_df.columns
        assert "geometry_hash" in s2_df.columns
        dt = timedelta(days=ALLOWED_TEMPORAL_DISTANCE_DAYS)

        out_ds = list()
        for row_idx, row in mo.status.progress_bar(bm_df.iterrows(), total=len(bm_df)):
            bm_date = row.measurement_date
            bm_geom_hash = row.geometry_hash
            row.name = "biomass"
            date_start = (bm_date - dt)
            date_end = (bm_date + dt)
            if date_start.tz is None:
                date_start = date_start.tz_localize("UTC")
            if date_end.tz is None:
                date_end = date_end.tz_localize("UTC")

            s1_filt = s1_df[s1_df.geometry_hash == bm_geom_hash]
            s2_filt = s2_df[s2_df.geometry_hash == bm_geom_hash]

            s1_filt = s1_filt[(s1_filt.image_date > date_start) & (s1_filt.image_date < date_end)]
            s2_filt = s2_filt[(s2_filt.image_date > date_start) & (s2_filt.image_date < date_end)]

            assert len(s1_filt) < 10, ("Unexpected many data pairs.", len(s1_filt))
            assert len(s2_filt) < 10, ("Unexpected many data pairs.", len(s2_filt))
            if len(s1_filt) == 0:  # no satellite data recent to biomass measurement
                continue
            if len(s2_filt) == 0:  # no satellite data recent to biomass measurement
                continue

            assert len(s1_filt) >= 1
            assert len(s2_filt) >= 1
            if len(s1_filt.geometry.unique()) == 0: 
                continue
            if len(s2_filt.geometry.unique()) == 0: 
                continue
            assert row.geometry == s1_filt.geometry.unique()
            assert row.geometry == s2_filt.geometry.unique()

            # using this join()-based approach is wrong, as there are not enough samples created.
            # there should be more samples in the S1+S2+BM dataset then in the S2+BM dataset due to different 
            # combinations of S1 with S2+BM
            # row_as_df = pd.DataFrame([row] * (len(s1_filt) * len(s2_filt))).reset_index(drop=True)      
            # columns_to_copy = set(row_as_df.columns) - {"geometry", "geometry_hash"} # remove geometry columns       
            # row_as_df = row_as_df[list(columns_to_copy)]
            # bm_with_s1 = s1_filt.reset_index(drop=True).join(row_as_df)
            # bm_with_s1_s2 = s2_filt.reset_index(drop=True).join(bm_with_s1, lsuffix="_s2", rsuffix="_s1")

            # drop 'geometry' to avoid multiple 'geometry_x' columns
            row_as_df = pd.DataFrame([row]).drop("geometry", axis="columns")
            bm_with_s2 = row_as_df.merge(s2_filt, on="geometry_hash")
            bm_with_s2 = bm_with_s2.drop("geometry", axis="columns")
            bm_with_s2_s1 = bm_with_s2.merge(s1_filt, on="geometry_hash", suffixes=("_s2", "_s1"))
            bm_with_s2_s1 = bm_with_s2_s1.reset_index(drop=True)

            out_ds.append(bm_with_s2_s1)
        bm_satellite_ds = pd.concat(out_ds)
        bm_satellite_ds = gpd.GeoDataFrame(bm_satellite_ds)
        return bm_satellite_ds
    return (join_biomass_with_s1_s2_data,)


@app.cell
def _(
    Path,
    args,
    join_biomass_with_s1_s2_data,
    s1_sm_merged_geomhash,
    s2_geomhash,
    ts_label_filtered_geomhash,
):
    bm_s1_s2_ds = join_biomass_with_s1_s2_data(bm_df=ts_label_filtered_geomhash, s1_df=s1_sm_merged_geomhash, s2_df=s2_geomhash)
    out_path_bm_s1_s2 = Path(args.output_dir) / f"dataset-{args.dataset_name}-paired-biomass-s1-s2-single-date.parquet"
    bm_s1_s2_ds.to_parquet(out_path_bm_s1_s2)
    print(f"Saved {len(bm_s1_s2_ds)} S1-S2-BM pairs to '{out_path_bm_s1_s2}'.")
    return (bm_s1_s2_ds,)


@app.cell
def _(mo):
    mo.md(r"""
    ## Checks
    """)
    return


@app.cell
def _(bm_s1_ds, bm_s1_s2_ds, bm_s2_ds, ts_label_filtered_geomhash):
    print(f"")
    print(f"{len(ts_label_filtered_geomhash)} biomass measurements.")
    print(f"S1-BM dataset has \t\t{len(bm_s1_ds)} samples.")
    print(f"S2-BM dataset has \t\t{len(bm_s2_ds)} samples.")
    print(f"S1-S2-BM dataset has \t{len(bm_s1_s2_ds)} samples.")
    return


if __name__ == "__main__":
    app.run()
