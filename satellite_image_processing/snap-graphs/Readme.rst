SNAP/gpt processing graphs
============================

For satellite image processing, SNAP (ESA's Sentinel toolbox, https://step.esa.int/main/toolboxes/snap/) is
used. SNAP is mainly a processing tool using a GUI, but also allows to specify processing graphs (consisting
of several steps) as xml files and run them with different parameters.
This is done using ``gpt``, the graph processing tool.

The processing pipeline uses four processing graphs. The image processing save data in beam-dimap
format and a second steps transforms them into GeoTiff, which is done to increase performance. Processing
and writing directly as Geotiff is usually slower than processing, saving as beam-dimap, and converting to
geotiff.
Sentinel-1 processing is split up into two graphs, because this improves performance (see also the
pyroSAR documentation https://pyrosar.readthedocs.io/en/latest/general/snap.html#workflow-splitting)

* Sentinel-2 processing (``s2-to-10mtif.xml``)

  * takes a Sentinel-2 image archive, resamples all bands to 10m (upsampling the
    20m and 60m bands), takes a subset of the bands, and save the processed file in beam-dimap.

* Sentinel-1 processing split into two graphs

  1. ``s1-grd-processing-db-TFonly.xml``: read Sentinel-1 archive, apply Orbit, Thermal Noise removal,
     GRD Border noise removal, Calibration, Terrain Flattening (TF), save as beam-dimap.
  2. ``s1-grd-processing-db-TConly.xml``: read beam-dimap with TF, apply geometric terrain correction (TC),
     save as beam-dimap.

* ``beam-dimap-to-tif.xml``: takes an image in beam-dimap format (one ``imagename.dim`` file and
  according ``imagename.data`` folder) and converts it into a single GeoTiff file.