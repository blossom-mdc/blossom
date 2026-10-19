"""
reflectance [0., 100.]  float16
ndvi        [-1, 1]     float16
ndmi        [-1, 1]     float16
"""
import numpy as np
from typing import List, Dict
from satellite_image_processing.scripts.utils import Modality, BandnamesProcessedS1, BandnamesProcessedS2

FeaturesDict = Dict[str, np.ndarray]


def calculate_features_s1(img: np.ndarray, bandnames: List[str]) -> FeaturesDict:
    """Processed images should have four bands:
        vv and vh, both gamma0 in linear scale, then local incidence angle and layover shadow mask.

    should only return single band images! so that bandname = featurename.
    """
    assert img.shape[0] == 4, ("Expected 4 S1 bands!", img.shape)
    assert bandnames == BandnamesProcessedS1, ("Unexpected bandnames!", bandnames, BandnamesProcessedS1)

    vv = img[bandnames.index("VV_gamma0_lin")]
    vh = img[bandnames.index("VH_gamma0_lin")]
    lia = img[bandnames.index("local_incidence_angle")]

    # mask all pixels with NaN, if any of the three channels has NaN or "0"
    # for VV/VH, 0 in linear scale results in missing data in log scale
    # and also a LIA of 0° is not physically meaningful
    invalid_data_mask = np.isnan(vv) | np.isnan(vh) | np.isnan(lia) | (lia == 0) | (vv == 0.) | (vh == 0.)
    vv[invalid_data_mask] = np.nan
    vh[invalid_data_mask] = np.nan
    lia[invalid_data_mask] = np.nan

    vv_db = lin_to_db(vv[np.newaxis, ...])
    vh_db = lin_to_db(vh[np.newaxis, ...])
    rvi = calculate_rvi(vv, vh)
    cr = calculate_crossratio(band1=vv, band2=vh)
    lia = lia[np.newaxis, ...]

    feature_dict = {"vv_db": vv_db, "vh_db": vh_db, "rvi": rvi, "cr": cr, "local_incidence_angle": lia}
    return feature_dict


def calculate_features_s2(img: np.ndarray, bandnames: List[str]) -> FeaturesDict:
    """should only return single band images! so that bandname = featurename."""
    assert img.shape[0] == 11, ("Expected 11 S2 bands!", img.shape)
    assert bandnames == BandnamesProcessedS2, ("Unexpected bandnames!", bandnames, BandnamesProcessedS2)
    reflectance_data = img[:-1,...]
    reflectance = calculate_reflectance(reflectance_data)  # float32
    all_zero_pixel_mask = get_all_bands_zero_mask(reflectance_data)
    reflectance[:, all_zero_pixel_mask] = np.nan

    ndvi = calculate_ndvi(red=reflectance[bandnames.index("B4")], nir=reflectance[bandnames.index("B8")])
    ndvi[:, all_zero_pixel_mask] = np.nan

    ndmi = calculate_ndmi(nir=reflectance[bandnames.index("B8")], swir=reflectance[bandnames.index("B11")])
    ndmi[:, all_zero_pixel_mask] = np.nan

    evi = calculate_evi(red=reflectance[bandnames.index("B4")],
                        nir=reflectance[bandnames.index("B8")],
                        blue=reflectance[bandnames.index("B2")])
    evi[:, all_zero_pixel_mask] = np.nan

    s2rep = calculate_s2rep(b7=reflectance[bandnames.index("B7")],
                            b4=reflectance[bandnames.index("B4")],
                            b5=reflectance[bandnames.index("B5")],
                            b6=reflectance[bandnames.index("B6")],)
    s2rep[:, all_zero_pixel_mask] = np.nan

    ireci = calculate_ireci(b7=reflectance[bandnames.index("B7")],
                            b4=reflectance[bandnames.index("B4")],
                            b5=reflectance[bandnames.index("B5")],
                            b6=reflectance[bandnames.index("B6")],)
    ireci[:, all_zero_pixel_mask] = np.nan

    feature_dict = {"ndvi": ndvi, "ndmi": ndmi, "evi": evi, "s2rep": s2rep, "ireci": ireci}
    for refl_band, bandname in zip(reflectance, bandnames[:-1]):
        feature_dict[f"reflectance_{bandname}"] = refl_band[np.newaxis, ...]  # make it chw

    return feature_dict


def get_image_features(modality: Modality, cropped_img: np.ndarray, bandnames: List[str]):
    if modality == Modality.s1:
        features = calculate_features_s1(cropped_img, bandnames)
    elif modality == Modality.s2:
        features = calculate_features_s2(cropped_img, bandnames)
    else: raise ValueError("modality not found!", modality, Modality)
    for feature_name, feature_data in features.items():
        assert feature_data.ndim == 3, ("Features should be chw images with 1 channel!", feature_data.shape)
        assert feature_data.shape[0] == 1, ("Features should be single band images!", feature_data.shape)
    return features


def calculate_rvi(vv_lin: np.ndarray, vh_lin: np.ndarray) -> np.ndarray:
    """where the linear data is zero, the db is not defined and replaced with NaN.
    so, also here all zeros should be replaced with NaN."""
    eps = 1e-12  # avoid denominator becoming 0
    num = 4 * vh_lin
    denom = vv_lin + vh_lin

    with np.errstate(divide='ignore', invalid='ignore'):
        rvi = np.true_divide(num, denom + eps)
    rvi[denom == 0] = np.nan
    rvi_chw = rvi[np.newaxis, ...]
    return rvi_chw

def calculate_crossratio(band1: np.ndarray, band2: np.ndarray) -> np.ndarray:
    """Calculate the cross-ratio (CR) of two image bands.

    This is used in our case at least for the two S1 SAR backscatter polarizations."""
    eps = 1e-9  # to avoid division by zero

    cr = band1 / (band2 + eps)
    cr[(band2 == 0)] = np.nan  # mask pixels where the CR is not defined.
    cr = cr[np.newaxis, ...]
    return cr

def lin_to_db(data_lin: np.ndarray) -> np.ndarray:
    """Convert linear data to db. Should be intensity.
    Fail at negative values.
    Replace zero values with NaN to indicate missing data.
    """
    assert not np.any(data_lin < 0), ("Non-negative values encountered!")
    assert np.issubdtype(data_lin.dtype, np.floating)
    zero_mask = (data_lin == 0)
    with np.errstate(divide='ignore'): # ignore division by zero
        data_db = 10 * np.log10(data_lin)
    data_db[zero_mask] = np.nan
    return data_db


def calculate_ndvi(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """Calculate the NDVI from red and NIR Sentinel-2 reflectance (0. - 1.)."""
    # asserts to check, that data is reflectance in [0, 1]
    assert np.all(np.where(np.isnan(red), True, red >= 0.)),  ("Not all values >= 0 (or nan)!")
    assert np.all(np.where(np.isnan(red), True, red <= 1.)), ("Not all values >= 0 (or nan)!")
    assert np.issubdtype(red.dtype, np.floating)
    assert red.ndim == nir.ndim == 2, ("Two dims expected!", red.shape, nir.shape)

    eps = 1e-9 # for numerical stability
    ndvi = (nir - red) / (nir + red + eps)
    ndvi = ndvi[np.newaxis, ...]
    return ndvi


def calculate_evi(nir: np.ndarray, red: np.ndarray, blue: np.ndarray) -> np.ndarray:
    """General formula: 2.5 * (NIR - RED) / ((NIR + 6*RED - 7.5*BLUE) + 1)
    Sentinel-2 bands (typically): B8, B4, B2
    """
    # let evi = 2.5 * (samples.B08 - samples.B04) / ((samples.B08 + 6.0 * samples.B04 - 7.5 * samples.B02) + 1.0);
    evi = 2.5 * (nir - red) / ((nir + 6 * red - 7.5 * blue) + 1.)
    evi = evi[np.newaxis, ...]
    return evi

def calculate_s2rep(b7: np.ndarray, b4: np.ndarray, b5: np.ndarray, b6: np.ndarray) -> np.ndarray:
    """
    S2REP - Sentinel-2 Red-Edge Position Index
    S2REP = 705 + 35 * ((Red1 + NIR)/2) - Red2 / (Red3 - Red2))
    for s2: 705 + 35 * ((B4 + B7)/2) - B5 / (B6 - B5)),
    https://eo4geocourses.github.io/IGIK_Sentinel2-Data-and-Vegetation-Indices/#/23
    Guyot and Baret, 1988
    or 700 + 40 * (...)
    """
    eps = 1e-9
    s2rep = 705 + 35 * (((b4 + b7)/2) - b5) / (b6 - b5 + eps)
    s2rep = s2rep[np.newaxis, ...]
    return s2rep


def calculate_ireci(b7: np.ndarray, b4: np.ndarray, b5: np.ndarray, b6: np.ndarray) -> np.ndarray:
    """
    IRECI - Inverted Red-Edge Chlorophyll Index algorithm
    incorporates the reflectance in four bands to estimate canopy chlorophyll content (Guyot and Baret, 1988; Clevers et al., 2000).
    IRECI = (NIR - Red1) / (Red2 / Red3)
    for S2: (B7 - B4) / (B5 / B6), where: B7 = 783 nm, B6 = 740 nm, B5 = 705 nm, B4 = 665 nm
    (https://eo4geocourses.github.io/IGIK_Sentinel2-Data-and-Vegetation-Indices/#/24)
    """
    eps = 1e-9
    ireci = (b7 - b4) / (b5 / b6 + eps)
    ireci = ireci[np.newaxis, ...]
    return ireci


def calculate_ndmi(nir: np.ndarray, swir: np.ndarray) -> np.ndarray:
    """Calculate NDMI (leaf moisture) from reflectance (0. - 1.)."""
    assert np.all(np.where(np.isnan(nir), True, nir >= 0.)),  ("Not all values >= 0 (or nan)!")
    assert np.all(np.where(np.isnan(nir), True, nir <= 1.)), ("Not all values >= 0 (or nan)!")
    assert np.issubdtype(nir.dtype, np.floating)
    assert swir.ndim == nir.ndim == 2, ("Two dims expected!", swir.shape, nir.shape)

    eps = 1e-9  # for numerical stability
    ndmi = (nir - swir) / (nir + swir + eps)
    ndmi = ndmi[np.newaxis, ...]
    return ndmi


def calculate_reflectance(s2_image: np.ndarray) -> np.ndarray:
    """Calculate the reflectance ([0 - 1]) from a S2 data."""
    data = np.clip(s2_image, 1_000, 11_000) - 1_000
    reflectance = data.astype(np.float32) / 10_000
    return reflectance


def mask_all_zero_pixels(reflectance_img: np.ndarray) -> np.ndarray:
    # mask pixels where all bands are zero (=don't contain data)
    # last band is excluded, because its the scl
    all_zero_mask = np.sum(reflectance_img, axis=0) == 0
    reflectance_img[:, all_zero_mask] = np.nan
    return reflectance_img


def get_all_bands_zero_mask(reflectance_img: np.ndarray) -> np.ndarray:
    # mask pixels where all bands are zero (=don't contain data)
    # last band is excluded, because its the scl
    sum_of_all_bands = np.sum(reflectance_img, axis=0)
    all_zero_mask = np.array(sum_of_all_bands == 0)
    return all_zero_mask
