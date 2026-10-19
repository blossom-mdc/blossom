.. _ml_dataset:

ML datasets
===========

To create datasets start by extracting the time series for the field geometries. You can run
``extract_feature_timeseries.py`` to orchestrate the complete workflow (DEM values, S1/S2 features and SWI), or call
the modality specific scripts individually:

* ``extract_feature_timeseries_s1_s2.py`` to build Sentinel-1/Sentinel-2 feature time series.
* ``extract_feature_timeseries_swi.py`` to extract soil moisture (SWI) time series.
* ``add_dem_info_to_biomass.py`` to enrich the shapefile with elevation, slope and aspect (mainly for biomass labels).

TODO: add conversion script for biomass labels to parquet

Using the time series files, different ML datasets can be created:

* using only a single acquisition date
* incorporating a temporal history

