#!/usr/bin/env bash
#SBATCH --job-name=tile-all-processed-images
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --mem=128G
#SBATCH --time=4:00:00
#SBATCH -o /home/trossbe/slurm-jobs/job_%j_%x.log

set -euxo pipefail

: "${CONTAINER_CMD:?Need to set CONTAINER_CMD}"
: "${DATA_DIR:?Missing data dir}"
: "${PROCESSED_IMAGE_TILES_DIR:?Missing processed image tiles dir}"
: "${PROCESSED_IMAGES_DIR:?Missing processed image dir}"
: "${TILE_SELECTION_FILE:?Missing tile shape file}"


${CONTAINER_CMD} python3 satellite_image_processing/scripts/41_all_processed_images_to_tiles.py \
		--input-dir=$PROCESSED_IMAGES_DIR \
		--shapefile=$TILE_SELECTION_FILE \
		--output-dir=$PROCESSED_IMAGE_TILES_DIR \
    --max-workers 93  # overprovision in hope it is slightly faster/uses the available cores fully
