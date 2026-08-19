"""
Script rename the input bands and color interpretation of a geotiff file.

Example usage:
write_bandnames_colorinterpretations_into_geotiff.py \
    --names B2,B3,B4,B8,B8A,B11,B12,quality_scene_classification \
    --interpretations BlueBand,GreenBand,RedBand,NIRBand,NIRBand,SWIRBand,SWIRBand,PaletteIndex
    $Processed_S2_tif

Adding color interpretations can also be done using gdal_edit.py:
gdal_edit.py -colorinterp_1 blue -colorinterp_2 green -colorinterp_3 red -colorinterp_4 nir -colorinterp_5 nir -colorinterp_6 swir -colorinterp_7 swir -colorinterp_8 palette
But adding the band names/descriptions cannot be done using that way.
"""

from argparse import ArgumentParser
from pathlib import Path
from typing import Optional
from osgeo import gdal


def get_args():
    supported_color_interprets = get_supported_color_interpretations_gdal()
    supported_color_interprets_str = ",".join(supported_color_interprets)

    parser = ArgumentParser(description="Add band names and color interpretations to a geotiff file.")
    parser.add_argument("rasterfile", help="Path of the geotiff file.", type=Path)
    parser.add_argument("--names", help="Comma-seperated list of band names to add.", type=str)
    parser.add_argument("--interpretations", help=f"Comma seperated list of color interpretations. Possible values: {supported_color_interprets_str}", type=str)
    args = parser.parse_args()
    return args

def get_supported_color_interpretations_gdal():
    supported_color_interprets = [p for p in dir(gdal) if "GCI" in p]
    supported_color_interprets = list(map(lambda p: p.replace("GCI_", ""), supported_color_interprets))
    return supported_color_interprets


def write_bandnames_colorinterpretations_into_tif(rasterfile: Path, bandnames: str, color_interpretations: Optional[str]):
    """bandanmes and color_interpretations should be a comma-seperated string like 'B1,B2,B3,B4'."""
    assert rasterfile.is_file(), ("Geotif file does not exists!", rasterfile)
    gdal.UseExceptions()

    band_names = bandnames.split(",")
    if color_interpretations:
        color_interpretations = color_interpretations.split(",")
        assert len(band_names) == len(color_interpretations), ("Different number of band names and color interpretations!")

        # translate str color interpretations into gdal properties
        possible_color_interpretations = [p for p in dir(gdal) if "GCI" in p]
        for col_interp in color_interpretations:
            assert "GCI_" + col_interp in possible_color_interpretations, ("Color interpretation not found!", "GCI_"+col_interp, possible_color_interpretations)
        color_interpretations = [getattr(gdal, "GCI_" + ci) for ci in color_interpretations]

    # actually update geotiff
    with gdal.Open(rasterfile, gdal.GA_Update) as ds:
        num_bands = ds.RasterCount
        for band_index in range(num_bands):
            band = ds.GetRasterBand(band_index + 1)
            band.SetDescription(band_names[band_index])
            if color_interpretations and color_interpretations[band_index]:
                band.SetColorInterpretation(color_interpretations[band_index])
            # band.SetStatistics(min, max, mean, std)

            # color table cannot be set:
            # "RuntimeError: filename.tif, band 4: SetColorTable() can only be called on band 1."
            #if band_index == 3:
            #    ct = gdal.ColorTable()
            #    for val in range(0, 101):
            #        gray = int((val / 100) * 255)
            #        ct.SetColorEntry(val, (gray, gray, gray, 255))
            #    band.SetRasterColorTable(ct)


if __name__ == "__main__":
    args = get_args()
    write_bandnames_colorinterpretations_into_tif(rasterfile=args.rasterfile, bandnames=args.names, color_interpretations=args.interpretations)
