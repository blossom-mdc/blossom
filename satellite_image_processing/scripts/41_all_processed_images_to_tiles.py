#!/usr/bin/env python3
import traceback
from typing import List
from pathlib import Path
from functools import partial
from argparse import ArgumentParser
from multiprocessing import cpu_count

from tqdm.contrib.concurrent import process_map
tiling_module = __import__('40_processed_image_to_tiles')


def parse_args():
    parser = ArgumentParser(description="Tile all feature rasters in parallel inside a single SLURM job.")
    parser.add_argument("--input-dir", type=Path, required=True, help="Directory with feature .tif images")
    parser.add_argument("--shapefile", type=Path, required=True, help="GeoJSON or shapefile with quadkey tile geometries")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to save output tiles")
    parser.add_argument("--max-workers", type=int, default=0, help="Number of parallel workers (default: all but one core)")
    parser.add_argument("--force-tiling", action="store_true", help="Forcing tiling of all processed images, don't skip images that have already >=1 tile.")
    return parser.parse_args()


def tile_one_image(raster_path: Path, shapefile_path: Path, output_dir: Path):
    try:
        tiling_module.extract_tiles_from_raster(
            processed_raster_img_path=raster_path,
            tiling_polygon_vectorfile=shapefile_path,
            output_dir=output_dir,
            disable_progress_bar=True,
            max_workers=1,
            verbose=False,
        )
    except Exception as e:
        e.add_note(f"{raster_path=}")
        e.add_note(f"{shapefile_path=}")
        e.add_note(f"{output_dir=}")
        print("-"*60)
        print(traceback.format_exc())
        print("-"*60)

def get_imageid_from_tile_path(img_path: Path) -> str:
    stem = img_path.stem
    qk, date, imageid = stem.split("_", maxsplit=2)
    return imageid


def get_untiled_tifs(all_processed_tifs: List[Path],
                     tiles_dir: Path,
                     ) -> List[Path]:
    """Filter out all processed tifs, that have at least one existing tile."""
    if not tiles_dir.is_dir():
        return all_processed_tifs
    paths_all_tiles = list(tiles_dir.glob("**/*.tif"))
    imageids_all_tiles = {get_imageid_from_tile_path(p) for p in paths_all_tiles}

    # Filter out all processed tifs that have at least one existing tile.
    untiled_paths = filter(lambda p: p.stem not in imageids_all_tiles, all_processed_tifs)
    return list(untiled_paths)



def main():
    args = parse_args()
    assert args.input_dir.is_dir(), ("Not a directory!", args.input_dir)
    assert args.shapefile.is_file(), ("Not a file!", args.shapefile)

    all_processed_tifs = sorted(args.input_dir.glob("*.tif"))
    print(f"Found {len(all_processed_tifs)} images to tile.")

    if args.force_tiling:
        all_tifs_to_tile = all_processed_tifs
    else:
        all_untiled_tifs = get_untiled_tifs(all_processed_tifs, tiles_dir=args.output_dir)
        print(f"Found {len(all_untiled_tifs)} images that are not yet tiled.")
        all_tifs_to_tile = all_untiled_tifs

    max_workers = args.max_workers or max(cpu_count() - 1, 1)
    print(f"Using {max_workers} worker processes.")

    task_fn = partial(tile_one_image,
                      shapefile_path=args.shapefile,
                      output_dir=args.output_dir)
    process_map(task_fn, all_tifs_to_tile, max_workers=max_workers, chunksize=1, desc="Tiling rasterfiles")


if __name__ == "__main__":
    main()