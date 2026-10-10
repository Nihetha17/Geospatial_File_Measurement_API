import pytest
import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

from tests.helpers import (
    find_feature,
    get_measurements,
    kml_document,
    line_placemark,
    meridian_length_m,
    multigeometry_placemark,
    point_placemark,
    polygon_placemark,
    post_file,
    rect_area_m2,
    rect_ring,
    shapefile_zip,
    upload_ok,
)


def test_polygon_area_from_kml(client):
    ring = rect_ring(80.20, 13.00, 0.01, 0.01)
    uploaded = upload_ok(client, "poly.kml", kml_document(polygon_placemark("lot", ring)))
    body = get_measurements(client, uploaded["id"])

    feature = find_feature(body["features"], "lot")
    assert feature["geometry_type"] == "Polygon"
    assert feature["measurement_status"] == "MEASURED"
    assert feature["area_unit"] == "square_metres"
    assert feature["length"] is None
    # UTM scale distortion is <= ~0.2 % for area, so allow 0.5 %.
    assert feature["area"] == pytest.approx(rect_area_m2(13.005, 0.01, 0.01), rel=5e-3)
    assert feature["area"] > 1_000_000  # ~1.2 km2; a value in square degrees would be ~0.0001


def test_linestring_length_from_kml(client):
    line = [(80.2, 13.0), (80.2, 13.1)]  # runs along a meridian
    uploaded = upload_ok(client, "line.kml", kml_document(line_placemark("road", line)))
    feature = find_feature(get_measurements(client, uploaded["id"])["features"], "road")

    assert feature["geometry_type"] == "LineString"
    assert feature["measurement_status"] == "MEASURED"
    assert feature["length_unit"] == "metres"
    assert feature["area"] is None
    assert feature["length"] == pytest.approx(meridian_length_m(13.0, 13.1), rel=2e-3)


def test_point_has_no_measurement(client):
    uploaded = upload_ok(client, "pt.kml", kml_document(point_placemark("pin", 80.2, 13.0)))
    feature = find_feature(get_measurements(client, uploaded["id"])["features"], "pin")

    assert feature["geometry_type"] == "Point"
    assert feature["measurement_status"] == "NOT_REQUIRED"
    assert feature["area"] is None and feature["length"] is None
    assert feature["geometry"]["type"] == "Point"
    assert feature["geometry"]["coordinates"][:2] == [80.2, 13.0]


def test_measurement_response_structure(client):
    uploaded = upload_ok(
        client,
        "mix.kml",
        kml_document(
            polygon_placemark("lot", rect_ring(80.2, 13.0, 0.01, 0.01)),
            line_placemark("road", [(80.2, 13.0), (80.2, 13.05)]),
            point_placemark("pin", 80.2, 13.0),
        ),
    )
    body = get_measurements(client, uploaded["id"])
    assert body["file_id"] == uploaded["id"]
    assert body["filename"] == "mix.kml"
    assert "4326" in body["source_crs"]
    assert body["measurement_crs"] == "EPSG:32644"
    assert body["feature_count"] == 3 and body["returned_count"] == 3
    assert body["summary"] == {"MEASURED": 2, "NOT_REQUIRED": 1}
    for index, feature in enumerate(body["features"]):
        assert feature["feature_index"] == index  # source order preserved
        for key in ("feature_id", "geometry_type", "geometry", "properties", "measurement_status", "warnings"):
            assert key in feature


def test_measurements_pagination(client):
    placemarks = [point_placemark(f"p{i}", 80.2 + i * 0.001, 13.0) for i in range(5)]
    uploaded = upload_ok(client, "pts.kml", kml_document(*placemarks))
    response = client.get(f"/api/files/{uploaded['id']}/measurements/", params={"limit": 2, "offset": 1})
    body = response.json()
    assert response.status_code == 200
    assert body["feature_count"] == 5 and body["returned_count"] == 2 and body["offset"] == 1
    assert [f["feature_index"] for f in body["features"]] == [1, 2]
    assert client.get(f"/api/files/{uploaded['id']}/measurements/", params={"limit": 0}).status_code == 422


def test_shapefile_projected_metres_measured_directly(client):
    # A Shapefile holds a single geometry type, so polygons and lines are uploaded separately.
    parcels = gpd.GeoDataFrame(
        {"label": ["parcel"]},
        geometry=[Polygon([(500000, 1440000), (500100, 1440000), (500100, 1440200), (500000, 1440200)])],
        crs="EPSG:32644",
    )
    roads = gpd.GeoDataFrame(
        {"label": ["road"]},
        geometry=[LineString([(500000, 1440000), (500300, 1440400)])],
        crs="EPSG:32644",
    )
    parcel_upload = upload_ok(client, "parcels.zip", shapefile_zip(parcels))
    road_upload = upload_ok(client, "roads.zip", shapefile_zip(roads))

    parcel_body = get_measurements(client, parcel_upload["id"])
    parcel = parcel_body["features"][0]
    assert parcel["properties"]["label"] == "parcel"
    assert parcel["area"] == pytest.approx(20000.0, rel=1e-6)
    assert "UTM" in parcel_body["source_crs"] or "32644" in parcel_body["source_crs"]

    road = get_measurements(client, road_upload["id"])["features"][0]
    assert road["length"] == pytest.approx(500.0, rel=1e-6)


def test_empty_and_null_geometries_are_reported(client):
    gdf = gpd.GeoDataFrame(
        {"label": ["ok", "nothing"]},
        geometry=[Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), None],
        crs="EPSG:32644",
    )
    uploaded = upload_ok(client, "nullgeom.zip", shapefile_zip(gdf))
    body = get_measurements(client, uploaded["id"])
    ok, nothing = body["features"]
    assert ok["measurement_status"] == "MEASURED"
    assert ok["area"] == pytest.approx(100.0)
    assert nothing["measurement_status"] == "NULL_GEOMETRY"
    assert nothing["geometry"] is None and nothing["geometry_type"] is None
    assert nothing["area"] is None
    assert uploaded["status"] == "COMPLETED_WITH_WARNINGS"


def test_unsupported_geometry_type_does_not_crash(client):
    uploaded = upload_ok(
        client,
        "collection.kml",
        kml_document(multigeometry_placemark("mixed", 80.2, 13.0), polygon_placemark("lot", rect_ring(80.2, 13.0, 0.01, 0.01))),
    )
    body = get_measurements(client, uploaded["id"])
    mixed = find_feature(body["features"], "mixed")
    assert mixed["geometry_type"] == "GeometryCollection"
    assert mixed["measurement_status"] == "UNSUPPORTED_GEOMETRY"
    assert mixed["area"] is None and mixed["length"] is None
    assert find_feature(body["features"], "lot")["measurement_status"] == "MEASURED"


def test_invalid_self_intersecting_polygon_is_repaired_with_warning(client):
    s = 0.01
    bowtie = [(80.2, 13.0), (80.2 + s, 13.0 + s), (80.2 + s, 13.0), (80.2, 13.0 + s), (80.2, 13.0)]
    uploaded = upload_ok(client, "bowtie.kml", kml_document(polygon_placemark("bowtie", bowtie)))
    feature = find_feature(get_measurements(client, uploaded["id"])["features"], "bowtie")
    assert feature["measurement_status"] == "MEASURED"
    assert any("invalid" in w.lower() for w in feature["warnings"])
    # Two triangles, each a quarter of the bounding square -> half the square's area.
    assert feature["area"] == pytest.approx(0.5 * rect_area_m2(13.005, s, s), rel=5e-3)
