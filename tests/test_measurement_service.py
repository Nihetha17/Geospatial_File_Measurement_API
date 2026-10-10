"""Pure unit tests of geometry classification (no HTTP, no files)."""
import pytest
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)

from app.schemas.measurement import MeasurementStatus
from app.services.crs_utils import build_crs_context
from app.services.measurement_service import measure_geometry

UTM = build_crs_context(CRS.from_epsg(32644))


def test_null_and_empty_geometries():
    assert measure_geometry(None, UTM).status == MeasurementStatus.NULL_GEOMETRY
    for empty in (Polygon(), LineString(), Point()):
        assert measure_geometry(empty, UTM).status == MeasurementStatus.EMPTY_GEOMETRY


def test_points_need_no_measurement():
    for geometry in (Point(1, 2), MultiPoint([(0, 0), (1, 1)])):
        result = measure_geometry(geometry, UTM)
        assert result.status == MeasurementStatus.NOT_REQUIRED
        assert result.area is None and result.length is None


def test_geometry_collection_is_unsupported():
    result = measure_geometry(GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])]), UTM)
    assert result.status == MeasurementStatus.UNSUPPORTED_GEOMETRY
    assert "GeometryCollection" in result.message


def test_multipolygon_with_hole_area():
    outer = [(0, 0), (100, 0), (100, 100), (0, 100)]
    hole = [(10, 10), (20, 10), (20, 20), (10, 20)]
    multi = MultiPolygon([Polygon(outer, [hole]), Polygon([(200, 0), (210, 0), (210, 10), (200, 10)])])
    assert measure_geometry(multi, UTM).area == pytest.approx(10000 - 100 + 100)


def test_multilinestring_length():
    multi = MultiLineString([[(0, 0), (30, 40)], [(100, 100), (100, 200)]])
    assert measure_geometry(multi, UTM).length == pytest.approx(150.0)


def test_invalid_polygon_is_repaired_with_warning():
    bowtie = Polygon([(0, 0), (100, 100), (100, 0), (0, 100)])
    assert not bowtie.is_valid
    result = measure_geometry(bowtie, UTM)
    assert result.status == MeasurementStatus.MEASURED
    assert result.area == pytest.approx(5000.0)
    assert result.warnings


def test_z_coordinates_are_ignored():
    result = measure_geometry(LineString([(0, 0, 500), (30, 40, 900)]), UTM)
    assert result.length == pytest.approx(50.0)


def test_unsupported_crs_type_is_reported_not_raised():
    ctx = build_crs_context(CRS.from_epsg(4978))  # geocentric
    assert ctx.kind == "unsupported"
    result = measure_geometry(Polygon([(0, 0), (1, 0), (1, 1)]), ctx)
    assert result.status == MeasurementStatus.MEASUREMENT_FAILED
