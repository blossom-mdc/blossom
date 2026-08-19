"""Add the slope and aspect to the DEM as additional bands using gdal.

Example usage:
python3 24_add_slope_aspect_to_dem.py \
    --input dem_file
    --output dem_aspect_slope_file
"""

import argparse
from pathlib import Path
import tempfile
import subprocess
import traceback

from write_bandnames_colorinterpretations_into_geotiff import write_bandnames_colorinterpretations_into_tif, get_supported_color_interpretations_gdal

DEM_SLOPE_ASPECT_BANDNAMES = ["Elevation [m]", "Slope [°]", "Aspect [°]"]

def add_slope_aspect_to_dem(input_path: Path, output_path: Path):
    assert input_path.is_file(), ("Not a file/Not found!", input_path)
    with tempfile.NamedTemporaryFile() as aspect_file, tempfile.NamedTemporaryFile() as slope_file:
        subprocess.run(["gdaldem", "aspect", "-q", input_path.resolve(), aspect_file.name], check=True)
        subprocess.run(["gdaldem", "slope", "-q", input_path.resolve(), slope_file.name], check=True)
        merge_inputs = [input_path.resolve(), slope_file.name, aspect_file.name]
        subprocess.run(["gdal_merge.py", "-separate", "-q", "-co", "COMPRESS=ZSTD", "-o", output_path.resolve()] + merge_inputs, check=True)

    bandnames_str = ",".join(DEM_SLOPE_ASPECT_BANDNAMES)
    write_bandnames_colorinterpretations_into_tif(output_path, bandnames=bandnames_str, color_interpretations=None)

def get_and_check_args():
    parser = argparse.ArgumentParser(description="Add the slope and aspect to the DEM as additional bands using gdal.")
    parser.add_argument("--input", help="Path to input shapefile or GeoJSON", type=Path, required=True)
    parser.add_argument("--output", help="File to save the image IDs to", type=Path, required=True)
    args = parser.parse_args()

    assert args.input.is_file(), ("Input file doesn't exists!", args.input)
    return args

def is_gdaldem_available() -> bool:
    # set `check` so that an exception is thrown when a non-zero exit code is returned
    # set `capture_output` to avoid printing to stdout when it is successful
    try:
        r = subprocess.run(["gdaldem", "--help"], check=True, capture_output=True, text=True)
        return True
    except Exception:
        print("Calling gdaldem failed.")
        traceback.print_exc()
        print(r.stdout)
        print(r.stderr)
        return False


def main() -> None:
    args = get_and_check_args()
    assert is_gdaldem_available(), ("`gdaldem` seems to be unavailable.")
    add_slope_aspect_to_dem(input_path=args.input, output_path=args.output)

if __name__ == "__main__":
    main()