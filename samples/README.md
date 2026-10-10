# Sample data

| File | Description |
|------|-------------|
| `sample_survey.kml` | KML (WGS 84) with one polygon (~1.2 km²), one north–south line (~5.5 km) and one point near Chennai. |
| `generate_sample_shapefile.py` | Script that builds `sample_parcels.zip`, a zipped Shapefile in EPSG:32644 (metres) with two polygons of exactly 20 000 m² and 2 500 m². |

Create the zipped Shapefile once (virtual environment active, project root):

```powershell
python samples\generate_sample_shapefile.py
```

Then upload either file:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/files/" -F "file=@samples\sample_survey.kml"
curl.exe -X POST "http://127.0.0.1:8000/api/files/" -F "file=@samples\sample_parcels.zip"
```

`sample_parcels.zip` is generated rather than committed because a Shapefile is a binary, multi-file format.
