#!/usr/bin/env bash
#SBATCH --job-name=extract-feature-timeseries
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --mem=128G
#SBATCH --time=4:00:00
#SBATCH -o /home/trossbe/slurm-jobs/job_%j_%x.log

set -euxo pipefail

: "${CONTAINER_CMD:?Need to set CONTAINER_CMD}"
: "${PROCESSED_IMAGE_TILES_DIR:?Missing processed image tiles dir}"
: "${TILE_SELECTION_FILE:?Missing tile shape file}"
: "${LABELS_VECTORFILE:?Missing definition of biomass measurements file}"
: "${IMAGE_IDS_FILE:?Missing definition of image ids file}"
: "${TIMESERIES_PARQUET_PATH_TEMPLATE:?Template for the output parquet files not defined!}"


${CONTAINER_CMD} python ml_dataset/extract_feature_timeseries.py \
    --labels $LABELS_VECTORFILE \
    --images-dir $PROCESSED_IMAGE_TILES_DIR \
    --quadkey-polygons $TILE_SELECTION_FILE \
    --s1-image-id-file $IMAGE_IDS_FILE \
    --output-pattern $TIMESERIES_PARQUET_PATH_TEMPLATE \
    --max-workers 94  # overprovision, as probably data loading is the bottleneck, not compute


