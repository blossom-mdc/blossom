#!/bin/bash
#SBATCH --ntasks=1 # Number of tasks (=cpus on possibly multiple nodes). Default is 1.
#SBATCH --cpus-per-task=24 # CPUs on a single node
#SBATCH --mem=16G # Total memory pool in MB
#SBATCH -t 60 # Maximum Runtime in minutes
#SBATCH -o /home/trossbe/slurm-jobs/job_%j_%x.log # File to which stdout/err will be written. %j=job id, %x=job name

###########################
# Process raw Sentinel-2 L2A archive into one N-channel tif file
###########################
#
# example job submission:
# INPUT_FILE=/fast/project/PG_GW_Schmidt_Ruumi/satellite-imagery/S2A_MSIL2A_20230507T113321_N0509_R080_T30UUB_20230507T151700.SAFE.zip \
# OUTPUT_FILE=/fast/project/PG_GW_Schmidt_Ruumi/processed-satellite-imagery/S2A_MSIL2A_20230507T113321_N0509_R080_T30UUB_20230507T151700.SAFE.tif \
# sbatch $HOME/blossom/slurm-batch-jobs/process-s2-raw-to-tif.sh

: "${CONTAINER_CMD:?Need to set CONTAINER_CMD}"
: "${BLOSSOM_CODE_DIR:?Need to set BLOSSOM_CODE_DIR}"
: "${DATA_DIR:?Need to set DATA_DIR}"
set -euxo pipefail  # safeguard for shell scripts. has to happen after guix profile setup

NUM_CPUS="${NUM_CPUS:-24}"
SNAP_CONTAINER_PATH=/home/trossbe/esa-snap12.sif
echo "Sentinel-2 processing '$INPUT_FILE' into '$OUTPUT_FILE'."
echo "To run this job this should work: 'INPUT_FILE=$INPUT_FILE OUTPUT_FILE=$OUTPUT_FILE sbatch $HOME/blossom/slurm-batch-jobs/process-s2-raw-to-tif.sh'"

SNAP_GPT_ARGS="-J-Xmx15g -q ${NUM_CPUS}"

if [ -n "${SLURM_JOB_ID+x}" ]; then
  # gpt cannot run in parallel without problems, therefore create a directory for each run ...
  DIR_USER=${TMPDIR}/snap_$(uuidgen)
  mkdir --parents "$DIR_USER"
  ls $DIR_USER  # just to ensure it exists
  # but this leads to redownloading orbits and DEMs. so: link the original Orbits and dem folders into the auxdata
  # directory. linking the whole auxdata directory is not working (?) because this also includes the gdal dir,
  # which is changed by each parallel gpt instances and the reason specific userdirs are created.
  mkdir $DIR_USER/auxdata
  ln -s $HOME/.snap/auxdata/dem $DIR_USER/auxdata/dem
  ln -s $HOME/.snap/auxdata/Orbits $DIR_USER/auxdata/Orbits
  ls $DIR_USER/auxdata
  SNAP_GPT_ARGS="$SNAP_GPT_ARGS -J-Dsnap.userdir=${DIR_USER}"
fi


# use default (MAX cluster) apptainer commands if not set
SNAP_CONTAINER_CMD="${SNAP_CONTAINER_CMD:-apptainer run --bind ${DATA_DIR}:${DATA_DIR} $SNAP_CONTAINER_PATH}"
GDAL_CONTAINER_CMD="${GDAL_CONTAINER_CMD:-apptainer run docker://ghcr.io/osgeo/gdal:alpine-small-3.11.0}"
SUBSET_REGION="${SUBSET_REGION:-0,0,0,0}"  #  x,y,width,height

$SNAP_CONTAINER_CMD /usr/local/snap/bin/gpt ${SNAP_GPT_ARGS} --diag  # get version (among others) for logging it

TMP_OUTFILE=${TMPDIR}/$(basename $OUTPUT_FILE)
$SNAP_CONTAINER_CMD \
  /usr/local/snap/bin/gpt ${SNAP_GPT_ARGS} \
  $BLOSSOM_CODE_DIR/satellite_image_processing/snap-graphs/s2-to-10mtif.xml \
  -Psubset_region="$SUBSET_REGION" \
  -Pinput="$INPUT_FILE" \
  -Poutput="$TMP_OUTFILE"

$GDAL_CONTAINER_CMD gdal_translate \
  -of GTiff \
  -co "COMPRESS=ZSTD" \
  -co "BIGTIFF=YES" \
  "${TMP_OUTFILE}" \
  "${TMP_OUTFILE}_comp.tif"


# generate overviews for faster displaying
# GDAL_PAM_ENABLED=NO to avoid creating useless *.aux.xml files
GDAL_PAM_ENABLED=NO GDAL_NUM_THREADS=ALL_CPUS $GDAL_CONTAINER_CMD \
  gdaladdo \
  "${TMP_OUTFILE}_comp.tif"  8 16 32 64 128 256 512


# write band names and color interpretations into the tif file
# 1) gdal 3.8 doesn't support GCI_NIRBand or GCI_SWIRBand, whereas 3.10 does...
# 2) use the blossom container, because there ZSTD compression is supported
${CONTAINER_CMD} \
  python3 $BLOSSOM_CODE_DIR/satellite_image_processing/scripts/write_bandnames_colorinterpretations_into_geotiff.py \
  --names B2,B3,B4,B5,B6,B7,B8,B8A,B11,B12,quality_scene_classification \
  --interpretations BlueBand,GreenBand,RedBand,Undefined,Undefined,Undefined,Undefined,Undefined,Undefined,Undefined,PaletteIndex \
  "${TMP_OUTFILE}_comp.tif"

mkdir -p "$(dirname $OUTPUT_FILE)"  # create output directory if not existing

mv "${TMP_OUTFILE}_comp.tif" $OUTPUT_FILE