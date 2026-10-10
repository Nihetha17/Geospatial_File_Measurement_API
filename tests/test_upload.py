import geopandas as gpd
from shapely.geometry import Polygon

from tests.helpers import (
    kml_document,
    point_placemark,
    polygon_placemark,
    post_file,
    rect_ring,
    shapefile_zip,
)


def test_successful_kml_upload(client):
    content = kml_document(polygon_placemark("lot", rect_ring(80.20, 13.00, 0.01, 0.01)), point_placemark("pin", 80.2, 13.0))
    response = post_file(client, "survey.kml", content)
    assert response.status_code == 201
    body = response.json()
    assert len(body["id"]) >= 16
    assert body["filename"] == "survey.kml"
    assert body["feature_count"] == 2
    assert "4326" in body["crs"]
    assert body["status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}


def test_successful_shapefile_zip_upload(client):
    gdf = gpd.GeoDataFrame(
        {"name": ["a", "b"]},
        geometry=[Polygon([(0, 0), (100, 0), (100, 50), (0, 50)]), Polygon([(200, 0), (300, 0), (300, 50), (200, 50)])],
        crs="EPSG:32644",
    )
    response = post_file(client, "parcels.zip", shapefile_zip(gdf))
    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "parcels.zip"
    assert body["feature_count"] == 2
    assert body["crs"] is not None
    assert body["status"] == "COMPLETED"


def test_shapefile_inside_subfolder_is_accepted(client):
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[Polygon([(0, 0), (10, 0), (10, 10)])], crs="EPSG:32644")
    response = post_file(client, "nested.zip", shapefile_zip(gdf, folder="my_data"))
    assert response.status_code == 201
    assert response.json()["feature_count"] == 1


def test_unsafe_filename_is_sanitised(client):
    content = kml_document(point_placemark("pin", 80.2, 13.0))
    response = post_file(client, "../../etc/evil<script>.kml", content)
    assert response.status_code == 201
    name = response.json()["filename"]
    assert "/" not in name and "<" not in name and ".." not in name
    assert name.endswith(".kml")
