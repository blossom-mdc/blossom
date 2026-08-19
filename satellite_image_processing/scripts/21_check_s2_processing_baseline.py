"""Check, that all Sentinel-2 image archives have processing baseline 5.0.

With baseline 4.*, the reflectance values in the archives were normalized to [0, 10000] for 0 to 100% reflectance.
Now, with baseline 5.*, the value range is shifted by 1000 to be in [1000, 11000].
Later scripts rely on the data being in the new value range, so this has to be checked for all images.
"""

import argparse
import xml.etree.ElementTree as ET
import zipfile
import warnings
from pathlib import Path
from typing import Tuple
from tqdm.auto import tqdm


def get_arguments():
    parser = argparse.ArgumentParser(
        description=__doc__,  # take the docstring
        formatter_class=argparse.RawDescriptionHelpFormatter,  # preserve newlines when printing with --help
    )
    parser.add_argument("--s2-archive-dir", type=Path, required=True,
                        help="Path to the directory containing all unprocessed S2 scenes. Used to check the processing baseline (required >= 5.0)")
    return parser.parse_args()


def get_processing_baseline(s2_archive_path: Path) -> Tuple[int, int]:
    """Open a Sentinel-2 scene archive and return the baseline processing version.

    This will open the archive, search for `MTD_MSIL2A.xml` and read <PROCESSING_BASELINE> from it.

    ```xml
    <?xml version="1.0" encoding="UTF-8" standalone="no"?>
    <n1:Level-2A_User_Product xmlns:n1="https://psd-15.sentinel2.eo.esa.int/PSD/User_Product_Level-2A.xsd"
        xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xsi:schemaLocation="https://psd-15.sentinel2.eo.esa.int/PSD/User_Product_Level-2A.xsd">
    <n1:General_Info>
        <Product_Info>
            <PRODUCT_START_TIME>2025-04-11T11:44:01.024Z</PRODUCT_START_TIME>
            <...>
           <PROCESSING_BASELINE>05.11</PROCESSING_BASELINE>
           <...>
        </Product_info>
    <...>
     ```
     """
    # open the zip and find MTD_MSIL2A.xml
    with zipfile.ZipFile(s2_archive_path, 'r') as z:
        mtd_path = next((f for f in z.namelist() if "MTD_MSIL2A.xml" in f), None)
        if not mtd_path:
            raise FileNotFoundError("MTD_MSIL2A.xml not found in archive.")

        # parse the XML and find the processing baseline version string
        with z.open(mtd_path) as mtd_file:
            tree = ET.parse(mtd_file)
            root = tree.getroot()
            baseline_str = root.findtext('.//PROCESSING_BASELINE')
        assert baseline_str, ("No baseline str found!", baseline_str)

    baseline_major, baseline_minor = baseline_str.split(".")
    baseline_major, baseline_minor = int(baseline_major), int(baseline_minor)
    return baseline_major, baseline_minor


def check_processing_baseline_downloaded_s2_images(image_archive_dir: Path):
    s2_image_archives = list(image_archive_dir.glob("S2*.zip"))
    assert len(s2_image_archives) >= 1, ("Couldn't find any S2 image archives!", image_archive_dir)

    outdated_baseline_images = list()
    for s2_archive in tqdm(s2_image_archives, desc="Checking baseline version"):
        major, minor = get_processing_baseline(s2_archive_path=s2_archive)
        if major == 5:
            pass
        elif major <= 4:
            outdated_baseline_images.append(s2_archive)
            print(f"Old processing baseline <= 4 for {s2_archive=}!")
            print(f"  Found processing baseline {major}.{minor}.")
        else:
            warnings.warn(f"Unexpected processing baseline for {s2_archive=}! Found {major}.{minor}.")

    if not outdated_baseline_images:
        print("All S2 images are correct!")


def main():
    args = get_arguments()
    check_processing_baseline_downloaded_s2_images(image_archive_dir=args.s2_archive_dir)


if __name__ == "__main__":
    main()
