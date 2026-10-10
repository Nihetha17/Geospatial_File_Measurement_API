"""CRS handling: geographic -> UTM, non-metre units, Web Mercator, geodesic fallback, missing CRS."""
import math

import geopandas as gpd
import pytest
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import transform

from app.schemas.measurement import MeasurementStatus
from app.services.crs_utils import build_crs_context, choose_utm_epsg
from app.services.measurement_service import measure_geometry
from tests.helpers import (
    WGS84_A,
    find_feature,
    get_measurements,
    kml_document,
    polygon_placemark,
    post_file,
    rect_area_m2,
    rect_ring,
    shapefile_zip,
    upload_ok,
)

US_SURVEY_FOOT = 1200 / 3937  # metres
INTL_FOOT = 0.3048


# ------------------------------------------------------------- UTM selection
@pytest.mark.parametrize(
    "lon, lat, expected",
    [
        (80.2, 13.0, 32644),  # Chennai, India
        (-0.1, 51.5, 32630),  # London
        (-74.0, 40.7, 32618),  # New York
        (151.2, -33.9, 32756),  # Sydney (southern hemisphere)
        (179.99, 0.0, 32660),
        (-179.99, 0.0, 32601),
    ],
)
def test_utm_zone_selection(lon, lat, expected):
    assert choose_utm_epsg(lon, lat) == expected


# ------------------------------------------------- geographic -> projected
def test_epsg4326_is_transformed_before_measuring(client):
    """Area must be in m2 from a projected CRS, never in squared degrees."""
    uploaded = upload_ok(client, "deg.kml", kml_document(polygon_placemark("lot", rect_ring(77.0, 28.0, 0.02, 0.02))))
    body = get_measurements(client, uploaded["id"])
    feature = find_feature(body["features"], "lot")
    assert feature["measurement_crs"] == "EPSG:32643"
    assert "4326" in body["source_crs"]
    assert feature["area"] == pytest.approx(rect_area_m2(28.01, 0.02, 0.02), rel=5e-3)


def test_geographic_service_level_matches_independent_formula():
    ctx = build_crs_context(CRS.from_epsg(4326))
    assert ctx.kind == "geographic"
    polygon = Polygon(rect_ring(-0.05, 51.50, 0.02, 0.01))
    result = measure_geometry(polygon, ctx)
    assert result.status == MeasurementStatus.MEASURED
    assert result.measurement_crs == "EPSG:32630"
    assert result.area == pytest.approx(rect_area_m2(51.505, 0.02, 0.01), rel=5e-3)


# ------------------------------------------------------ projected non-metres
def test_us_survey_feet_shapefile_is_converted_to_metres(client):
    # EPSG:2263 = NAD83 / New York Long Island (US survey feet). One geometry type per Shapefile.
    lots = gpd.GeoDataFrame(
        {"label": ["lot"]},
        geometry=[Polygon([(1000000, 200000), (1001000, 200000), (1001000, 201000), (1000000, 201000)])],
        crs="EPSG:2263",
    )
    fences = gpd.GeoDataFrame(
        {"label": ["fence"]},
        geometry=[LineString([(1000000, 200000), (1003000, 204000)])],
        crs="EPSG:2263",
    )
    lot = get_measurements(client, upload_ok(client, "lots.zip", shapefile_zip(lots))["id"])["features"][0]
    fence = get_measurements(client, upload_ok(client, "fences.zip", shapefile_zip(fences))["id"])["features"][0]
    assert lot["area"] == pytest.approx(1_000_000 * US_SURVEY_FOOT**2, rel=1e-6)  # ~92 903 m2
    assert fence["length"] == pytest.approx(5000 * US_SURVEY_FOOT, rel=1e-6)  # ~1 524 m
    assert lot["area_unit"] == "square_metres" and fence["length_unit"] == "metres"


def test_international_foot_projected_crs_service_level():
    crs = CRS.from_proj4("+proj=utm +zone=44 +datum=WGS84 +units=ft +no_defs")
    ctx = build_crs_context(crs)
    assert ctx.kind == "projected"
    assert ctx.unit_factor == pytest.approx(INTL_FOOT)
    area = measure_geometry(Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)]), ctx)
    length = measure_geometry(LineString([(0, 0), (3000, 4000)]), ctx)
    assert area.area == pytest.approx(1_000_000 * INTL_FOOT**2, rel=1e-9)  # 92 903.04 m2
    assert length.length == pytest.approx(5000 * INTL_FOOT, rel=1e-9)  # 1 524 m
    assert "feet" in (area.message or "").lower() or "foot" in (area.message or "").lower()


def test_projected_metre_crs_is_not_rescaled():
    ctx = build_crs_context(CRS.from_epsg(32644))
    result = measure_geometry(Polygon([(0, 0), (100, 0), (100, 200), (0, 200)]), ctx)
    assert result.area == pytest.approx(20000.0)
    assert result.measurement_crs == "EPSG:32644"


# ------------------------------------------------------------ Web Mercator
def test_web_mercator_is_re_measured_not_used_directly():
    """EPSG:3857 inflates area by ~1/cos^2(lat) (x4 at 60 N); we must not use it directly."""
    lonlat = Polygon(rect_ring(10.0, 60.0, 0.01, 0.01))
    to_mercator = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    mercator_polygon = transform(to_mercator.transform, lonlat)
    assert mercator_polygon.area > 3.5 * rect_area_m2(60.005, 0.01, 0.01)  # sanity: raw 3857 is badly inflated

    ctx = build_crs_context(CRS.from_epsg(3857))
    assert ctx.kind == "web_mercator"
    result = measure_geometry(mercator_polygon, ctx)
    assert result.status == MeasurementStatus.MEASURED
    assert result.area == pytest.approx(rect_area_m2(60.005, 0.01, 0.01), rel=5e-3)
    assert result.measurement_crs == "EPSG:32632"
    assert result.message and "Web Mercator" in result.message


# --------------------------------------------------- large-extent fallback
def test_large_polygon_uses_geodesic_not_utm():
    ctx = build_crs_context(CRS.from_epsg(4326))
    polygon = Polygon([(10, 0), (18, 0), (18, 2), (10, 2)])  # 8 degrees wide (> one 6-degree UTM zone)
    result = measure_geometry(polygon, ctx)
    assert result.status == MeasurementStatus.MEASURED
    assert result.measurement_crs.startswith("GEODESIC:")
    assert result.warnings
    # Spherical (authalic radius) reference. Differences: sphere vs ellipsoid (~0.5 %) and geodesic
    # polygon edges vs parallels (~0.2 %), so allow 1.5 %.
    authalic_radius = 6371007.2
    expected = authalic_radius**2 * math.radians(8) * math.sin(math.radians(2))
    assert result.area == pytest.approx(expected, rel=0.015)


def test_large_line_length_along_equator_is_exact():
    ctx = build_crs_context(CRS.from_epsg(4326))
    result = measure_geometry(LineString([(0, 0), (20, 0)]), ctx)
    assert result.measurement_crs.startswith("GEODESIC:")
    assert result.length == pytest.approx(WGS84_A * math.radians(20), rel=1e-6)


def test_polygon_crossing_antimeridian_is_measured_geodesically():
    ctx = build_crs_context(CRS.from_epsg(4326))
    polygon = Polygon([(179.0, 0.0), (-179.0, 0.0), (-179.0, 2.0), (179.0, 2.0)])
    result = measure_geometry(polygon, ctx)
    assert result.status == MeasurementStatus.MEASURED
    assert result.measurement_crs.startswith("GEODESIC:")
    assert result.area == pytest.approx(2 * 111_320 * 2 * 110_574, rel=0.03)  # ~2 deg x 2 deg near the equator


def test_out_of_range_geographic_coordinates_fail_gracefully():
    ctx = build_crs_context(CRS.from_epsg(4326))
    result = measure_geometry(Polygon([(0, 95), (1, 95), (1, 96), (0, 96)]), ctx)
    assert result.status == MeasurementStatus.MEASUREMENT_FAILED
    assert result.area is None and result.message


# --------------------------------------------------------------- missing CRS
def test_missing_crs_is_never_assumed(client):
    gdf = gpd.GeoDataFrame(
        {"label": ["a", "b"]},
        geometry=[Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), Polygon([(20, 0), (30, 0), (30, 10), (20, 10)])],
        crs=None,
    )
    response = post_file(client, "nocrs.zip", shapefile_zip(gdf))
    assert response.status_code == 201
    uploaded = response.json()
    assert uploaded["crs"] is None
    assert uploaded["status"] == "COMPLETED_WITH_WARNINGS"
    assert any("coordinate reference system" in w for w in uploaded["warnings"])

    info = client.get(f"/api/files/{uploaded['id']}/").json()
    assert info["crs"] is None

    body = get_measurements(client, uploaded["id"])
    assert body["source_crs"] is None and body["measurement_crs"] is None
    for feature in body["features"]:
        assert feature["measurement_status"] == "CRS_MISSING"
        assert feature["area"] is None and feature["measurement_crs"] is None
        assert "never guessed" in feature["message"]


def test_missing_crs_service_level():
    ctx = build_crs_context(None)
    assert ctx.kind == "missing"
    assert measure_geometry(LineString([(0, 0), (1, 1)]), ctx).status == MeasurementStatus.CRS_MISSING
    assert measure_geometry(Polygon([(0, 0), (1, 0), (1, 1)]), ctx).status == MeasurementStatus.CRS_MISSING
    assert measure_geometry(Point(1, 1), ctx).status == MeasurementStatus.NOT_REQUIRED  # points need no CRS
