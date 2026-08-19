import math
from enum import StrEnum, IntEnum
from pathlib import Path
from typing import List, Optional, Literal
import rasterio
import re
from datetime import datetime
import numpy as np
from osgeo import gdal

gdal.UseExceptions()


def save_multiband_tif(output_path, array_chw: np.ndarray, profile: rasterio.profiles.Profile, band_names: Optional[List[str]] = None):
    """chw is rasterio reading convention"""
    assert array_chw.ndim == 3, ("Expected 3-dim array (C, H, W)", array_chw.shape)
    if band_names:
        assert array_chw.shape[0] == len(band_names), ("Band number and name mismatch!", array_chw.shape, len(band_names))
    # todo type conversion?!
    # TODO check that all dtypes are equal
    band_count = array_chw.shape[0]
    profile.update(count=band_count)
    # Tiling doesn't typically help with faster data loading
    profile.update(tiled=False)
    # don't used predictor, because it doesn't really decrease filesize, but makes loading longer
    profile.update(compress="ZSTD")
    profile.update(dtype=array_chw.dtype)
    with rasterio.open(output_path, 'w', **profile) as dst:
        for band_idx in range(band_count):
            dst.write(array_chw[band_idx], band_idx + 1)
            if band_names:
                dst.set_band_description(band_idx + 1, band_names[band_idx])


def get_geotiff_band_names(raster_path: Path) -> Optional[List[str]]:
    with gdal.Open(raster_path, gdal.GA_ReadOnly) as raster_ds:
        band_count = raster_ds.RasterCount
        bands = [raster_ds.GetRasterBand(i).GetDescription() for i in range(1, band_count + 1)]
    # if any([(b is None) for b in bands]):
    #     return None
    return bands


class SCLClass(IntEnum):
    NO_DATA = 0  # "No Data (Missing data)",  	#000000
    SATURATED = 1  # "Saturated or defective pixel", #ff0000
    DARK_AREA_PIXELS = 2  # "Topographic casted shadows", #2f2f2f
    CLOUD_SHADOWS = 3  # "Cloud shadows", #643200
    VEGETATION = 4  # "Vegetation", #00a000
    BARE_SOILS = 5  # "Not-vegetated", #ffe65a
    WATER = 6  # "Water", #0000ff
    CLOUD_LOW_PROB = 7  # "Unclassified", #808080
    CLOUD_MEDIUM_PROB = 8  # "Cloud medium probability", #c0c0c0
    CLOUD_HIGH_PROB = 9  # "Cloud high probability", #ffffff
    THIN_CIRRUS = 10  # "Thin cirrus", #64c8ff
    SNOW_ICE = 11  # "Snow or ice", #ff96ff


def get_cloud_mask_scl(scl_img: np.ndarray) -> np.ndarray:
    """Take an SCL image array and return a mask of all pixels that are clouded."""
    # tile_img shape: (band, width, height)
    masked_classes = [
        SCLClass.CLOUD_HIGH_PROB,
        SCLClass.CLOUD_MEDIUM_PROB,
        SCLClass.CLOUD_LOW_PROB,
        SCLClass.CLOUD_SHADOWS,
        SCLClass.THIN_CIRRUS,
    ]
    mask = np.full_like(scl_img, fill_value=False, dtype=bool)
    for scl_class_to_mask in masked_classes:
        mask_class = scl_img == scl_class_to_mask
        mask = np.logical_or(mask, mask_class)
    return mask


def _get_no_data_values(tif_path: Path) -> list:
    """Iterate over all bands of a raster file and return the no data values of them."""
    no_data_values = list()
    with gdal.Open(tif_path, gdal.GA_ReadOnly) as ds:
        for band_no in range(1, ds.RasterCount + 1):
            band_src = ds.GetRasterBand(band_no)
            no_data_values.append(band_src.GetNoDataValue())
    return no_data_values

def _all_nodata_values_identical(nodata_values: list):
    # need to check nan seperatly, as float("nan") != float("nan")
    # possibilities: [0, 0] -> True, [0, 1] -> False; [0, nan] -> False; [nan, nan] -> True
    unique_values_count = set(nodata_values)
    all_nans = all([math.isnan(e) for e in nodata_values if e is not None])
    return (len(unique_values_count) > 1) or all_nans


def copy_tif_metadata(src_tif: Path, dst_tif: Path):
    """Copy tif image metadata and metadata of each band from src to dst tif."""
    ds_src = gdal.Open(src_tif, gdal.GA_ReadOnly)
    ds_dst = gdal.Open(dst_tif, gdal.GA_Update)
    assert ds_src.RasterCount == ds_dst.RasterCount

    tif_metadata_src = ds_src.GetMetadata()
    ds_dst.SetMetadata(tif_metadata_src)

    # gdal only supports one no data value per tif dataset. therefore: if the no data value is not the same for all
    # bands, avoid setting it.
    # otherwise an error "file.tif, band 1: Setting nodata to nan on band 1, but band 2 has nodata at 0. The
    # TIFFTAG_GDAL_NODATA only support one value per dataset. This value of nan will be used for all bands on re-opening"
    no_data_values_src = _get_no_data_values(src_tif)
    no_data_values_dst = _get_no_data_values(dst_tif)
    identical_nodata_values_src = _all_nodata_values_identical(no_data_values_src)
    identical_nodata_values_dst = _all_nodata_values_identical(no_data_values_dst)
    if not identical_nodata_values_src or not identical_nodata_values_dst:
        print(f"Muliple no data values found! {identical_nodata_values_src} {identical_nodata_values_dst}")

    for band_no in range(1, ds_src.RasterCount + 1):
        band_src = ds_src.GetRasterBand(band_no)
        band_dst = ds_dst.GetRasterBand(band_no)

        band_dst.SetDescription(band_src.GetDescription())
        band_dst.SetMetadata(band_src.GetMetadata())

        # Copy per-band nodata / scale / offset / colorinterp if present
        nodata = band_src.GetNoDataValue()
        if nodata and identical_nodata_values_src and identical_nodata_values_dst:
            band_dst.SetNoDataValue(nodata)

        scale = band_src.GetScale()
        if scale:
            band_dst.SetScale(scale)

        offset = band_src.GetOffset()
        if offset:
            band_dst.SetOffset(offset)

        band_dst.SetColorInterpretation(band_src.GetColorInterpretation())

    ds_dst.FlushCache()
    ds_dst = None
    ds_src = None


class Modality(StrEnum):
    s1 = "s1"
    s2 = "s2"
    worldcover = "worldcover"
    DSM = "COP_DEM"
    SWI = "SWI"  # CLMS soil water index

BandnamesProcessedS1 = ["VV_gamma0_lin", "VH_gamma0_lin", "local_incidence_angle", "layover_shaddow_mask"]
BandnamesProcessedS2 = ["B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12", "quality_scene_classification"]
BandnamesProcessedSWI = ["SWI", "QFLAG", "SSF"]
BandnamesProcessedDSM = ["Elevation [m]", "Slope [°]", "Aspect [°]"]


def get_modality_from_filepath(filepath: Path) -> Modality:
    """Returns the `Modality` for a given filepath.
    S1C_IW_GRDH_1SDV_20250522T181408_20250522T181433_002447_0051DB_0875.tif
    S2A_MSIL2A_20250411T114401_N0511_R123_T29UPA_20250411T194412.SAFE.tif
    reflectance_S2B_MSIL2A_20220910T115359_N0510_R023_T29UPA_20240719T054002.tif
    Copernicus_DSM_10_UK_Ireland.vrt
    c_gls_SWI1km_202106021200_CEURO_SCATSAR_V1.0.1_cog_SWI002_QFLAG002_SSF.tif
    """
    # a more complicated regular expression matching is used instead of just checking for
    # S1|S2 in the filename in hope that this is more robust...
    fname = filepath.stem
    if "Copernicus_DSM_10_" in fname:
        return Modality.DSM
    elif "c_gls_SWI1km_" in fname   and "SWI002_QFLAG002_SSF" in fname:
        return Modality.SWI

    match = re.search(r"((_?)|^)(S1|S2)[ABCD]", fname)
    if not match:
        raise ValueError(f"Could not extract modality from filename '{fname}'!")
    modality_str = match.group(0)
    modality_str = modality_str.replace("_", "")  # remove eventually existing '_'

    if modality_str.startswith("S1"):
        return Modality.s1
    elif modality_str.startswith("S2"):
        return Modality.s2
    else:
        raise ValueError(f"Cannot guess modality from filename '{fname}'!")

def try_parse_date_from_filepath(filepath: Path, modality: Modality) -> Optional[datetime]:
    """Takes the filepath and tries to path the date+time into a YYYYmmdd string.
    """
    name = filepath.name

    if modality == Modality.SWI:
        # c_gls_SWI1km_202106021200_CEURO_SCATSAR_V1.0.1_cog_SWI002_QFLAG002_SSF.tif
        # 0313130331_20210602_c_gls_SWI1km_202106021200_CEURO_SCATSAR_V1.0.1_cog_SWI002_QFLAG002_SSF.tif
        match = re.search(r'_\d{8}1200_', name)
        if not match:
            raise ValueError(f"Could not extract date from filename '{name}' as {modality}!")
        datestr = match.group(0)
        datestr = datestr.replace("_", "")  # remove '_'
        datestr = datestr.replace("1200", "")  # remove constant '1200' time
        filedate = datetime.fromisoformat(datestr)
    elif modality in {Modality.s1, Modality.s2}:
        # S1A_IW_GRDH_1SDV_20240606T063203_20240606T063228_054199_069770_6351_COG_TF_TC.tif
        # S2B_MSIL2A_20240602T112119_N0510_R037_T30UVB_20240602T125034.tif
        match = re.search(r'\d{8}T\d{6}', name)
        if not match:
            raise ValueError(f"Could not extract date from filename '{name}' as {modality}!")
        datestr = match.group(0)
        filedate = datetime.fromisoformat(datestr)
    else:
        filedate = None
    return filedate
