import os

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from tests.helpers import kml_document, point_placemark, post_file, shapefile_zip, zip_bytes


def _error(response):
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    return body["error"]


@pytest.mark.parametrize("filename", ["notes.txt", "data.geojson", "overlay.kmz", "archive", "image.PNG"])
def test_unsupported_extension_is_rejected(client, filename):
    response = post_file(client, filename, b"whatever")
    assert response.status_code == 415
    assert _error(response)["code"] == "UNSUPPORTED_FILE_TYPE"


def test_empty_file_is_rejected(client):
    response = post_file(client, "empty.kml", b"")
    assert response.status_code == 400
    assert _error(response)["code"] == "EMPTY_FILE"


def test_corrupt_kml_truncated_xml(client):
    response = post_file(client, "broken.kml", b"<?xml version='1.0'?><kml><Document><Placemark>")
    assert response.status_code == 422
    assert _error(response)["code"] == "INVALID_KML"


def test_kml_with_wrong_root_element(client):
    response = post_file(client, "wrong.kml", b"<html><body>hello</body></html>")
    assert response.status_code == 422
    assert _error(response)["code"] == "INVALID_KML"


def test_binary_garbage_as_kml(client):
    response = post_file(client, "garbage.kml", os.urandom(512) + b"\x00\x01")
    assert response.status_code == 422
    assert _error(response)["code"] == "INVALID_KML"


def test_kml_with_entity_declaration_is_rejected(client):
    evil = b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaa">]><kml><Document>&a;</Document></kml>'
    response = post_file(client, "bomb.kml", evil)
    assert response.status_code == 422
    assert _error(response)["code"] == "INVALID_KML"


def test_invalid_zip_archive(client):
    response = post_file(client, "fake.zip", b"this is definitely not a zip file")
    assert response.status_code == 400
    assert _error(response)["code"] == "INVALID_ZIP"


def test_zip_without_shapefile_components(client):
    response = post_file(client, "nothing.zip", zip_bytes({"readme.txt": b"hello", "photo.jpg": b"\xff\xd8"}))
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "MISSING_SHAPEFILE_COMPONENTS"
    assert set(error["details"]["missing_components"]) == {".shp", ".shx", ".dbf"}


def test_zip_with_incomplete_shapefile_reports_missing_parts(client):
    gdf = gpd.GeoDataFrame({"a": [1]}, geometry=[Polygon([(0, 0), (1, 0), (1, 1)])], crs="EPSG:32644")
    response = post_file(client, "partial.zip", shapefile_zip(gdf, include=[".shp", ".prj"]))
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "MISSING_SHAPEFILE_COMPONENTS"
    assert set(error["details"]["missing_components"]) == {".shx", ".dbf"}


def test_zip_with_corrupt_shapefile_parts(client):
    files = {"data.shp": os.urandom(300), "data.shx": os.urandom(100), "data.dbf": os.urandom(200)}
    response = post_file(client, "corrupt.zip", zip_bytes(files))
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "UNREADABLE_DATASET"
    assert "/" not in error["message"] and "\\" not in error["message"]  # no internal paths leaked


def test_zip_with_two_shapefiles_is_ambiguous(client):
    gdf = gpd.GeoDataFrame({"a": [1]}, geometry=[Polygon([(0, 0), (1, 0), (1, 1)])], crs="EPSG:32644")
    first = shapefile_zip(gdf, stem="one")
    second = shapefile_zip(gdf, stem="two")
    import io
    import zipfile

    merged = {}
    for blob in (first, second):
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for name in zf.namelist():
                merged[name] = zf.read(name)
    response = post_file(client, "two.zip", zip_bytes(merged))
    assert response.status_code == 422
    assert _error(response)["code"] == "MULTIPLE_DATASETS"


def test_missing_file_field_uses_consistent_error_format(client):
    response = client.post("/api/files/")
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_feature_count_limit(settings, make_client):
    from dataclasses import replace

    limited = make_client(replace(settings, max_features=2))
    content = kml_document(*[point_placemark(f"p{i}", 80.2 + i * 0.01, 13.0) for i in range(3)])
    response = post_file(limited, "many.kml", content)
    assert response.status_code == 413
    assert _error(response)["code"] == "TOO_MANY_FEATURES"
