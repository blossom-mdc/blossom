Slurm job scripts
====================

Different shell scripts to run on a Slurm HPC cluster using ``sbatch``. 

* ``process-s1-TF-TC.sh``: process a single S1 GRD scene into a tif file using SNAP and gdal. Run it with 
  ``INPUT_FILE=S1GRD.SAFE.zip OUTPUT_FILE=output.tif sbatch process-s1-grd-to-db-tif.sh``. Is typically called 
  by ``process-s1-TF-TC-array.sh``.
* ``process-s2-raw-to-tif.sh``: process a single S2 L2A product into a tif image. Similar to the S1 script uses 
  ``$INPUT_FILE`` and ``$OUTPUT_FILE`` 
* ``process-s1-TF-TC-array.sh`` and ``process-s2-array.sh``: scripts that start Slurm array jobs for parallel processing of 
  the images. They shouldn't be run on their own, but instead using ``submit-array-processing-jobs.py``!
* ``submit-array-processing-jobs.py``: run the array jobs to process many S1 or S2 images. Checks, if an image is not
  yet processed, and if one is unprocessed, uses the provided job script ot run it. For using Slurm array jobs, the
  ``--files_list`` is provided. An example is given below.
* ``41_all_processed_images_to_tiles.sh``: small wrapper to start ``41_all_processed_images_to_tiles.py`` as a Slurm
  job with many cores. 

Example how to use ``submit-array-processing-jobs.py``:

.. code-block:: shell

    python3 submit-array-processing-jobs.py \
        --raw-dir="satellite-imagery/" \  # where are the unprocessed images
        --processed-dir="processed-satellite-imagery" \  # where are/should the unprocessed images be
        --job-script="process-s1-TF-TC-array.sh" \  # which array processing script should be used
        --file-pattern="S1*_GRDH_*.SAFE.zip" \   # what is the file pattern in the unprocessed images dir?
        --num_parallel_jobs 5 \   # how many Slurm jobs to run in parallel
        --files_list="s1-array-processing-files.txt"  # file to provide the image list to the array job script


