#!/bin/bash
#SBATCH --ntasks=1 # Number of tasks (=cpus on possibly multiple nodes). Default is 1.
#SBATCH --cpus-per-task=12 # CPUs on a single node
#SBATCH --mem=16G # Total memory pool in MB (one run showed only 32GB utilized)
#SBATCH -t 60 # Maximum Runtime in mins
#SBATCH -o /home/trossbe/slurm-jobs/job_%A_%a_%x.log # File to which stdout/err will be written.
# %A=parent job id, %a array id, %x=job name

###########################
# Process many Sentinel-2 L2A archives
# Basically wraps process-s2-raw-to-tif.sh
# Should be started using submit-array-processing-jobs.py
# otherwise something like
# `INPUT_FILES_LIST=... sbatch --array=0-XY%5 process-s2-array.sh` should work.
###########################

: "${CONTAINER_CMD:?Need to set CONTAINER_CMD}"
: "${BLOSSOM_CODE_DIR:?Need to set BLOSSOM_CODE_DIR}"
: "${DATA_DIR:?Need to set DATA_DIR}"
set -euxo pipefail  # safeguard for shell scripts. has to happen after guix profile setup

echo "Running Sentinel-2 array job No ${SLURM_ARRAY_TASK_ID}. Extracting path from ${INPUT_FILES_LIST}."
export INPUT_FILE=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" ${INPUT_FILES_LIST})
export OUTPUT_FILE=$(echo "$INPUT_FILE" | sed 's/.SAFE//' | sed 's/.zip$/.tif/' | sed 's|/satellite-imagery/|/processed-satellite-imagery/|')
bash $BLOSSOM_CODE_DIR/satellite_image_processing/slurm-batch-jobs/process-s2-raw-to-tif.sh
