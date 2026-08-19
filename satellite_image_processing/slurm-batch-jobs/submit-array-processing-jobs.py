#!/usr/bin/env python3
"""This script submits jobs to process raw images into tif files.

Array jobs are used to schedule a large number of jobs. Potentially, thousands of images have to be processed.
When starting all processing at the same time this overloads the cluster (24 cores per processing * 1000
processed images = 24k cores, while the cluster has like 10k).
Therefore, only tens of processings are done in parallel. Scheduling this is done using SLURM array jobs,
which allow to say: run these massive amount of jobs, but only tens at the same time. This is achieved with
this script.



Example usage S1:
python3 $HOME/blossom/slurm-batch-jobs/submit-array-processing-jobs.py \
     --raw-dir="/fast/project/PG_GW_Schmidt_Ruumi/satellite-imagery/" \
    --processed-dir="/fast/project/PG_GW_Schmidt_Ruumi/processed-satellite-imagery" \
    --job-script="$HOME/blossom/slurm-batch-jobs/process-s1-TF-TC-array.sh" \
    --file-pattern="S1*_GRDH_*.SAFE.zip" \
    --num_parallel_jobs 5 \
    --files_list="s1-array-processing-files.txt"

python3 $HOME/blossom/slurm-batch-jobs/submit-array-processing-jobs.py \
     --raw-dir="/fast/project/PG_GW_Schmidt_Ruumi/satellite-imagery/" \
    --processed-dir="/fast/project/PG_GW_Schmidt_Ruumi/processed-satellite-imagery" \
    --job-script="$HOME/blossom/slurm-batch-jobs/process-s2-array.sh" \
    --file-pattern="S2*_MSIL2A_*.SAFE.zip" \
    --num_parallel_jobs 5 \
    --files_list="s2-array-processing-files.txt"
"""
import os
import subprocess
import argparse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Submit Slurm jobs for unprocessed satellite images.")
    parser.add_argument("--raw-dir", type=Path,
                        default=Path("/fast/project/PG_GW_Schmidt_Ruumi/satellite-imagery/"),
                        help="Directory with raw satellite images")
    parser.add_argument("--processed-dir", type=Path,
                        default=Path("/fast/project/PG_GW_Schmidt_Ruumi/processed-satellite-imagery"),
                        help="Directory with processed files")
    parser.add_argument("--job-script", type=Path, required=True,
                        default=Path("$HOME/blossom/slurm-batch-jobs/process_image.sh"),
                        help="Path to the Slurm job script for processing.")
    parser.add_argument("--file-pattern", type=str, default="*", required=True,
                        help="Pattern which files should be processed. Examples: 'S1*.zip', 'S2*'.")
    parser.add_argument("--num_parallel_jobs", type=int, default=10,
                        help="Number of jobs allowed to run in parallel.")
    parser.add_argument("--files_list", type=Path, default="array_job_file_list.txt",
                        help="Path to the text file which will be used to start the Slurm array job.")
    parser.add_argument("--force-reprocessing", action="store_true", default=False,
                        help="Don't check for already processed files. Instead, reprocess all available images.")
    parser.add_argument("--dry-run", action="store_true", default=False)
    return parser.parse_args()

def is_processing_needed(raw_file: Path, processed_dir: Path) -> bool:
    # S1A_IW_GRDH_1SDV_20230401T063043_20230401T063108_047899_05C16C_628E_COG.SAFE.zip
    # S1A_IW_GRDH_1SDV_20240813T181545_20240813T181610_055198_06BA51_4FC2_COG_TF_TC.tif
    # S2B_MSIL2A_20240920T112109_N0511_R037_T29UQS_20240920T140316.SAFE.zip
    # S2A_MSIL2A_20231103T113321_N0510_R080_T30UVD_20241108T160418.tif
    processed_filename_sar = raw_file.stem.replace(".SAFE", "") + "_TF_TC.tif"
    processed_filepath_sar = processed_dir / processed_filename_sar
    processed_filename_opt = raw_file.name.replace(".SAFE.zip", ".tif")
    processed_filepath_opt = processed_dir / processed_filename_opt
    is_image_processed = processed_filepath_sar.is_file() or processed_filepath_opt.is_file()
    is_processing_needed = not is_image_processed
    return is_processing_needed


def submit_processing_array_job(unprocessed_images_dir: Path, file_pattern, processed_images_dir: Path, num_parallel_jobs: int, job_script_path: Path, files_list_path: Path, force_reprocessing: bool = False,
dry_run: bool = False):
    unprocessed_filepaths = list(unprocessed_images_dir.glob(file_pattern))
    assert len(unprocessed_filepaths) > 0, ("No file for processing found!", unprocessed_images_dir, file_pattern)
    print(f"Found {len(unprocessed_filepaths)} raw images.")

    if force_reprocessing:
        files_to_process = unprocessed_filepaths
        print(f"Will (re-)process {len(files_to_process)} images.")
    else:
        files_to_process = [raw_file for raw_file in unprocessed_filepaths if is_processing_needed(raw_file, processed_images_dir)]
        assert len(files_to_process) > 0, "No files have to be processed!"
        print(f"Found {len(files_to_process)} images which are not yet processed.")

    with open(files_list_path, "w") as f:
        for raw_file in files_to_process:
            f.write(f"{str(raw_file)}\n")

    num_jobs_total = len(files_to_process)
    print(f"Starting array job for '{job_script_path.name}' starting {num_jobs_total} jobs ({num_parallel_jobs} in parallel).")
    print(f"Files are read from '{files_list_path}'.")
    os.environ["INPUT_FILES_LIST"] = str(files_list_path)


    # apparently, job arrays can't be considerably longer than 2000 jobs. so start multiple array jobs...
    max_array_elems_of_array_job = 2000
    # reduce number of jobs if multiple array jobs are started
    num_parallel_jobs = num_parallel_jobs // ((num_jobs_total // max_array_elems_of_array_job) + 1)

    start = 0
    end = min(num_jobs_total - 1, max_array_elems_of_array_job)

    commands = list()
    while start < num_jobs_total:
        cmd = ["sbatch", f"--array={start}-{end}%{num_parallel_jobs}", str(job_script_path)]
        commands.append(cmd)
        start += 2000
        end = min(num_jobs_total - 1 , end + 2000)


    if dry_run:
        for cmd in commands:
            cmd_str = " ".join(cmd)
            manual_run_cmd = f"INPUT_FILES_LIST={os.environ['INPUT_FILES_LIST']} {cmd_str}"
            print(f"Rerun command with '{manual_run_cmd}'.")
    else:
        for cmd in commands:
            subprocess.run(cmd)


def main():
    args = parse_args()
    print(args)
    submit_processing_array_job(
        unprocessed_images_dir=args.raw_dir,
        file_pattern=args.file_pattern,
        processed_images_dir=args.processed_dir,
        num_parallel_jobs=args.num_parallel_jobs,
        job_script_path=args.job_script,
        files_list_path=args.files_list,
        force_reprocessing=args.force_reprocessing,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
