"""Area and length calculation for a single geometry.

Rules (see README "Measurement-calculation workflow"):

* Points: no measurement required.
* Polygon / MultiPolygon -> area in square metres.
* LineString / MultiLineString -> length in metres.
* Anything else (GeometryCollection, ...) -> UNSUPPORTED_GEOMETRY.
* Geographic degrees are NEVER used directly: geometries are first
  transformed to a projected CRS (local UTM), or measured on the ellipsoid
  when a feature is too large for a single UTM zone.
* No CRS -> CRS_MISSING; the CRS is never guessed.
* No exception escapes: failures become a status + message for that feature.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import shapely
from pyproj import CRS, Geod
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from app.schemas.measurement import MeasurementStatus
from app.services.crs_utils import (
    UTM_MAX_LAT,
    UTM_MIN_LAT,
    CrsContext,
    choose_utm_epsg,
    get_transformer,
)

logger = logging.getLogger("geospatial_api")

AREA_UNIT = "square_metres"
LENGTH_UNIT = "metres"

_POLYGONAL = frozenset({"Polygon", "MultiPolygon"})
_LINEAR = frozenset({"LineString", "MultiLineString"})
_POINTS = frozenset({"Point", "MultiPoint"})


@dataclass
class MeasurementResult:
    status: MeasurementStatus
    measurement_crs: Optional[str] = None
    area: Optional[float] = None
    length: Optional[float] = None
    message: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


class _MeasurementError(Exception):
    def __init__(self, status: MeasurementStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ------------------------------------------------------------------ helpers
def _ensure_finite(geometry: BaseGeometry, message: str) -> None:
    if not all(math.isfinite(value) for value in geometry.bounds):
        raise _MeasurementError(MeasurementStatus.MEASUREMENT_FAILED, message)


def _repair_polygonal(geometry: BaseGeometry) -> Optional[BaseGeometry]:
    """Repair an invalid polygon with ``make_valid`` and keep polygonal parts only."""
    fixed = shapely.make_valid(geometry)
    if fixed.geom_type in _POLYGONAL:
        return None if fixed.is_empty else fixed
    polygons = []
    for part in shapely.get_parts(fixed):
        if part.geom_type == "Polygon":
            polygons.append(part)
        elif part.geom_type == "MultiPolygon":
            polygons.extend(shapely.get_parts(part))
    polygons = [poly for poly in polygons if not poly.is_empty]
    if not polygons:
        return None
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def _geodesic_measure(geometry: BaseGeometry, geographic_crs: CRS, want_area: bool) -> tuple[float, str]:
    """Ellipsoidal area/length for features too large for one UTM zone."""
    ellipsoid = geographic_crs.ellipsoid
    geod = Geod(a=ellipsoid.semi_major_metre, b=ellipsoid.semi_minor_metre)
    total = 0.0
    for part in shapely.get_parts(geometry):
        if want_area:
            lons, lats = part.exterior.coords.xy
            area = abs(geod.polygon_area_perimeter(list(lons), list(lats))[0])
            for ring in part.interiors:
                hole_lons, hole_lats = ring.coords.xy
                area -= abs(geod.polygon_area_perimeter(list(hole_lons), list(hole_lats))[0])
            total += area
        else:
            lons, lats = part.coords.xy
            total += geod.line_length(list(lons), list(lats))
    return total, f"GEODESIC:{ellipsoid.name}"


def _measure_geographic(
    geometry: BaseGeometry,
    geographic_crs: CRS,
    want_area: bool,
    max_utm_lon_span: float,
) -> tuple[float, str, list[str]]:
    """Measure a geometry whose coordinates are lon/lat degrees."""
    minx, miny, maxx, maxy = geometry.bounds
    if not all(math.isfinite(v) for v in (minx, miny, maxx, maxy)):
        raise _MeasurementError(MeasurementStatus.MEASUREMENT_FAILED, "The geometry contains non-finite coordinates.")
    if miny < -90 or maxy > 90 or minx < -360 or maxx > 360:
        raise _MeasurementError(
            MeasurementStatus.MEASUREMENT_FAILED,
            "Coordinates are outside the valid range for a geographic CRS "
            "(latitude -90..90, longitude -360..360). The file may use a different CRS than declared.",
        )

    lon_span = maxx - minx
    fits_one_zone = lon_span <= max_utm_lon_span and miny >= UTM_MIN_LAT and maxy <= UTM_MAX_LAT
    if fits_one_zone:
        epsg = choose_utm_epsg((minx + maxx) / 2.0, (miny + maxy) / 2.0)
        transformer = get_transformer(geographic_crs, f"EPSG:{epsg}")
        projected = shapely_transform(transformer.transform, geometry)
        _ensure_finite(projected, "The geometry could not be transformed to the measurement CRS.")
        value = projected.area if want_area else projected.length
        return value, f"EPSG:{epsg}", []

    value, label = _geodesic_measure(geometry, geographic_crs, want_area)
    warning = (
        "The feature is too large for a single UTM zone (or lies outside UTM's latitude range, "
        f"{UTM_MIN_LAT:g} to {UTM_MAX_LAT:g} degrees), so it was measured on the ellipsoid "
        "(geodesic) instead of in a projected CRS."
    )
    return value, label, [warning]


def _measure(geometry: BaseGeometry, geometry_type: str, ctx: CrsContext, max_utm_lon_span: float) -> MeasurementResult:
    want_area = geometry_type in _POLYGONAL
    planar = shapely.force_2d(geometry)  # Z values are irrelevant for planar area/length
    warnings: list[str] = []

    if want_area and not planar.is_valid:
        repaired = _repair_polygonal(planar)
        if repaired is None:
            raise _MeasurementError(
                MeasurementStatus.INVALID_GEOMETRY, "The polygon is invalid and could not be repaired."
            )
        planar = repaired
        warnings.append(
            "The geometry was invalid (e.g. self-intersecting). It was repaired with make_valid "
            "before measuring, so the area refers to the repaired shape."
        )

    note: Optional[str] = None
    if ctx.kind == "projected":
        _ensure_finite(planar, "The geometry contains non-finite coordinates.")
        factor = ctx.unit_factor
        raw = planar.area if want_area else planar.length
        value = raw * (factor * factor if want_area else factor)
        measurement_crs = ctx.label
        if not math.isclose(factor, 1.0, rel_tol=1e-12):
            note = (
                f"Measured in the source projected CRS; converted from '{ctx.linear_unit_name}' "
                f"to metres (1 unit = {factor:.10g} m)."
            )
    elif ctx.kind == "web_mercator":
        to_geographic = get_transformer(ctx.crs, ctx.geographic_crs)
        lonlat = shapely_transform(to_geographic.transform, planar)
        _ensure_finite(lonlat, "The Web Mercator geometry could not be converted to geographic coordinates.")
        value, measurement_crs, extra = _measure_geographic(lonlat, ctx.geographic_crs, want_area, max_utm_lon_span)
        warnings.extend(extra)
        note = (
            "The source CRS (Web Mercator) heavily distorts area and length away from the equator, "
            "so the geometry was converted to geographic coordinates and re-measured."
        )
    elif ctx.kind == "geographic":
        value, measurement_crs, extra = _measure_geographic(planar, ctx.geographic_crs, want_area, max_utm_lon_span)
        warnings.extend(extra)
    else:  # pragma: no cover - guarded by caller
        raise _MeasurementError(MeasurementStatus.MEASUREMENT_FAILED, "Unsupported CRS type.")

    if not math.isfinite(value) or value < 0:
        raise _MeasurementError(MeasurementStatus.MEASUREMENT_FAILED, "The calculation produced an invalid value.")
    value = round(float(value), 4)
    return MeasurementResult(
        status=MeasurementStatus.MEASURED,
        measurement_crs=measurement_crs,
        area=value if want_area else None,
        length=None if want_area else value,
        message=note,
        warnings=warnings,
    )


# --------------------------------------------------------------- public API
def measure_geometry(
    geometry: Optional[BaseGeometry],
    ctx: CrsContext,
    max_utm_lon_span: float = 6.0,
) -> MeasurementResult:
    """Measure one geometry; never raises."""
    if geometry is None:
        return MeasurementResult(MeasurementStatus.NULL_GEOMETRY, message="The feature has no geometry.")
    if geometry.is_empty:
        return MeasurementResult(MeasurementStatus.EMPTY_GEOMETRY, message="The feature has an empty geometry.")

    geometry_type = geometry.geom_type
    if geometry_type in _POINTS:
        return MeasurementResult(
            MeasurementStatus.NOT_REQUIRED, message="No area or length is defined for point geometries."
        )
    if geometry_type not in _POLYGONAL and geometry_type not in _LINEAR:
        return MeasurementResult(
            MeasurementStatus.UNSUPPORTED_GEOMETRY,
            message=f"Geometry type '{geometry_type}' is not supported for measurement.",
        )
    if ctx.kind == "missing":
        return MeasurementResult(
            MeasurementStatus.CRS_MISSING,
            message="The dataset does not declare a CRS. Measurements were not calculated because the CRS is never guessed.",
        )
    if ctx.kind == "unsupported":
        return MeasurementResult(
            MeasurementStatus.MEASUREMENT_FAILED,
            message=f"The source CRS cannot be used for measurement: {ctx.problem}",
        )

    try:
        return _measure(geometry, geometry_type, ctx, max_utm_lon_span)
    except _MeasurementError as exc:
        return MeasurementResult(exc.status, message=exc.message)
    except Exception:  # keep one bad feature from failing the whole request
        logger.exception("Unexpected failure while measuring a %s", geometry_type)
        return MeasurementResult(
            MeasurementStatus.MEASUREMENT_FAILED, message="The measurement failed unexpectedly for this feature."
        )
