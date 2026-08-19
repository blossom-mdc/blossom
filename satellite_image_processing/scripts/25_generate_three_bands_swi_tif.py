"""From a zip archive of the SWI, generate a three band SWI tif file."""

from argparse import ArgumentParser
from osgeo import gdal
import zipfile
from pathlib import Path
import numpy as np
import tempfile
from utils import BandnamesProcessedSWI

gdal.UseExceptions()

def unzip_product(zipped_product_path: Path, unzipped_dir: Path):
    with zipfile.ZipFile(zipped_product_path, 'r') as zip_ref:
        zip_ref.extractall(unzipped_dir)

def _combine_tif_metadata(m1: dict, m2: dict, m3: dict) -> dict:
    """Combine the metadata of three tifs into one common one. Output contains only stuff that is in all three inputs and equal."""
    common_keys = set(m1.keys()) & set(m2.keys()) & set(m3.keys())  # keys, that are in each of the three dicts
    all_keys = set(m1.keys()) | set(m2.keys()) | set(m3.keys())  # keys that are at least on dict
    diverging_keys = all_keys - common_keys   # keys that are only found in one or two dicts

    out = dict()
    for k in common_keys:
        if m1[k] == m2[k] == m3[k]:
            out[k] = m1[k]
        else:  # value different in the dicts
            diverging_keys.add(k)
    diverging_keys.remove('identifier')
    if diverging_keys:
        print(f"Metadata not the same for the three bands. Keys with different values: {diverging_keys}.")
    return out

def _assert_datasets_equal(ds1, ds2, ds3) -> None:
    assert ds1.RasterCount == 1, ("SWI files are expected to have exactly one band!", ds1.RasterCount)
    assert ds2.RasterCount == 1, ("SWI files are expected to have exactly one band!", ds3.RasterCount)
    assert ds3.RasterCount == 1, ("SWI files are expected to have exactly one band!", ds2.RasterCount)
    assert ds1.RasterXSize == ds2.RasterXSize, ("SWI files are expected to have the same dimensions!", ds1.RasterXSize, ds2.RasterXSize)
    assert ds2.RasterXSize == ds3.RasterXSize, ("SWI files are expected to have the same dimensions!", ds2.RasterXSize, ds3.RasterXSize)
    assert ds1.RasterYSize == ds2.RasterYSize, ("SWI files are expected to have the same dimensions!", ds1.RasterYSize, ds2.RasterYSize)
    assert ds2.RasterYSize == ds3.RasterYSize, ("SWI files are expected to have the same dimensions!", ds2.RasterYSize, ds3.RasterYSize)
    assert ds1.GetGeoTransform() == ds2.GetGeoTransform(), ("SWI files are expected to have the same transform!", ds1.GetGeoTransform(), ds2.GetGeoTransform())
    assert ds2.GetGeoTransform() == ds3.GetGeoTransform(), ("SWI files are expected to have the same transform!", ds2.GetGeoTransform(), ds3.GetGeoTransform())
    assert ds1.GetProjection() == ds2.GetProjection(), ("SWI files are expected to have the same projection!", ds1.GetProjection(), ds2.GetProjection())
    assert ds2.GetProjection() == ds3.GetProjection(), ("SWI files are expected to have the same projection!", ds2.GetProjection(), ds3.GetProjection())


def combine_swi_single_band_tifs(swi_path, qflag_path, ssf_path, out_path):
    ds1 = gdal.Open(swi_path, gdal.GA_ReadOnly)
    ds2 = gdal.Open(qflag_path, gdal.GA_ReadOnly)
    ds3 = gdal.Open(ssf_path, gdal.GA_ReadOnly)
    _assert_datasets_equal(ds1, ds2, ds3)

    meta1 = ds1.GetMetadata()
    meta2 = ds2.GetMetadata()
    meta3 = ds3.GetMetadata()
    meta_out = _combine_tif_metadata(meta1, meta2, meta3)

    band_array1 = ds1.GetRasterBand(1).ReadAsArray()
    band_array2 = ds2.GetRasterBand(1).ReadAsArray()
    band_array3 = ds3.GetRasterBand(1).ReadAsArray()
    assert np.isdtype(band_array1.dtype, np.uint8), ("Wrong dtype!", band_array1.dtype)
    assert np.isdtype(band_array2.dtype, np.uint8), ("Wrong dtype!", band_array2.dtype)
    assert np.isdtype(band_array3.dtype, np.uint8), ("Wrong dtype!", band_array3.dtype)

    drv = gdal.GetDriverByName("GTiff")
    out_ds: gdal.Dataset = drv.Create(out_path, ds1.RasterXSize, ds1.RasterYSize, 3, gdal.GDT_Byte, options=["PREDICTOR=2", "COMPRESS=DEFLATE"])

    out_ds.SetGeoTransform(ds1.GetGeoTransform())
    out_ds.SetProjection(ds1.GetProjection())
    out_ds.SetMetadata(meta_out)

    ds_arrays_list = [(ds1, band_array1), (ds2, band_array2), (ds3, band_array3)]
    for out_band_no, (src_ds, A) in enumerate(ds_arrays_list, start=1):
        rb_src = src_ds.GetRasterBand(1)
        rb_dst = out_ds.GetRasterBand(out_band_no)
        rb_dst.WriteArray(A)
        rb_dst.SetDescription(BandnamesProcessedSWI[out_band_no - 1])


        # Copy per-band metadata
        rb_dst.SetMetadata(rb_src.GetMetadata())

        # Copy per-band nodata / scale / offset / colorinterp if present
        nd = rb_src.GetNoDataValue()
        if nd is not None:
            rb_dst.SetNoDataValue(float(nd))

        scl = rb_src.GetScale()
        if scl is not None:
            rb_dst.SetScale(scl)

        off = rb_src.GetOffset()
        if off is not None:
            rb_dst.SetOffset(off)

        assert rb_src.GetColorInterpretation()
        if out_band_no == 3:
            color_interpret = gdal.GCI_PaletteIndex
        else:
            color_interpret =rb_src.GetColorInterpretation()
        rb_dst.SetColorInterpretation(color_interpret)

    out_ds.FlushCache()
    out_ds = None

def parse_args():
    parser = ArgumentParser(description="Read a zipped SWI product and save it into a three band tif file.")
    parser.add_argument("--zipped-swi", type=Path, required=True, help="Zipped SWI archive path.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory path.")
    parser.add_argument("-T", type=int, default=2, help="parameter T of the SWI product. basically the depth. T=2 is at the surface, T=100 is deep soil.")
    return parser.parse_args()

def zipped_swi_to_tif(zipped_swi_path: Path, output_dir: Path, T: int):
    T = f"{T:03}" # eg. "002"

    with tempfile.TemporaryDirectory() as unzip_dir_tmp:
        unzip_dir_path = Path(unzip_dir_tmp)
        unzip_product(zipped_swi_path, unzip_dir_path)

        unzipped_swi_dirs = list(unzip_dir_path.glob("c_gls_SWI1km*"))
        assert len(unzipped_swi_dirs) == 1, ("No expected file found!", list(unzip_dir_path.glob("*")))
        unzipped_swi_dir = unzipped_swi_dirs[0]
        swi_path = list(unzipped_swi_dir.glob(f"*SWI{T}*.tiff"))[0]
        qflag_path = list(unzipped_swi_dir.glob(f"*QFLAG{T}*.tiff"))[0]
        ssf_path = list(unzipped_swi_dir.glob(f"*SSF*.tiff"))[0]

        out_path = output_dir / f"{unzipped_swi_dir.name}_SWI{T}_QFLAG{T}_SSF.tif"

        combine_swi_single_band_tifs(swi_path=swi_path, qflag_path=qflag_path, ssf_path=ssf_path, out_path=out_path)



def main():
    args = parse_args()
    zipped_swi_to_tif(zipped_swi_path=args.zipped_swi, output_dir=args.output_dir, T=args.T)


if __name__ == "__main__":
    main()
