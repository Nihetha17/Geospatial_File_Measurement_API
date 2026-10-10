"""Creates samples/sample_parcels.zip - a zipped Shapefile for trying the API.

Run from the project root (with the virtual environment active):

    python samples/generate_sample_shapefile.py

The dataset is in EPSG:32644 (WGS 84 / UTM zone 44N, metres) and contains:
  * a 100 m x 200 m rectangle  -> area 20 000 m2
  * a 50 m x 50 m square       -> area  2 500 m2
"""
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon

OUTPUT = Path(__file__).resolve().parent / "sample_parcels.zip"

parcels = gpd.GeoDataFrame(
    {"name": ["Rectangle 100x200", "Square 50x50"], "owner": ["A. Kumar", "B. Devi"]},
    geometry=[
        Polygon([(500000, 1440000), (500100, 1440000), (500100, 1440200), (500000, 1440200)]),
        Polygon([(500200, 1440000), (500250, 1440000), (500250, 1440050), (500200, 1440050)]),
    ],
    crs="EPSG:32644",
)

with tempfile.TemporaryDirectory() as tmp:
    parcels.to_file(Path(tmp) / "sample_parcels.shp", driver="ESRI Shapefile")
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for part in sorted(Path(tmp).iterdir()):
            archive.write(part, part.name)

print(f"Created {OUTPUT}")
