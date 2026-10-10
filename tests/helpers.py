"""Builders for synthetic datasets plus independent reference formulas.

The reference formulas below use plain ``math`` on the WGS 84 ellipsoid, so
they do NOT share any code (or projection library) with the application and
can be used to cross-check its results within a tolerance.
"""
from __future__ import annotations

import io
import math
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)


# -------------------------------------------------- reference calculations
def meridian_radius(lat_deg: float) -> float:
    s = math.sin(math.radians(lat_deg))
    return WGS84_A * (1 - WGS84_E2) / (1 - WGS84_E2 * s * s) ** 1.5


def prime_vertical_radius(lat_deg: float) -> float:
    s = math.sin(math.radians(lat_deg))
    return WGS84_A / math.sqrt(1 - WGS84_E2 * s * s)


def rect_area_m2(center_lat: float, dlon_deg: float, dlat_deg: float) -> float:
    """Area of a small lon/lat rectangle on the ellipsoid (small-extent approximation)."""
    width = prime_vertical_radius(center_lat) * math.cos(math.radians(center_lat)) * math.radians(dlon_deg)
    height = meridian_radius(center_lat) * math.radians(dlat_deg)
    return width * height


def meridian_length_m(lat1: float, lat2: float) -> float:
    return meridian_radius((lat1 + lat2) / 2) * math.radians(abs(lat2 - lat1))


# ------------------------------------------------------------- KML builders
def rect_ring(lon: float, lat: float, dlon: float, dlat: float) -> list[tuple[float, float]]:
    return [(lon, lat), (lon + dlon, lat), (lon + dlon, lat + dlat), (lon, lat + dlat), (lon, lat)]


def _coords(points: Iterable[tuple[float, float]]) -> str:
    return " ".join(f"{x},{y},0" for x, y in points)


def polygon_placemark(name: str, ring: list[tuple[float, float]]) -> str:
    return (
        f"<Placemark><name>{name}</name><description>polygon</description><Polygon><outerBoundaryIs>"
        f"<LinearRing><coordinates>{_coords(ring)}</coordinates></LinearRing>"
        "</outerBoundaryIs></Polygon></Placemark>"
    )


def line_placemark(name: str, points: list[tuple[float, float]]) -> str:
    return (
        f"<Placemark><name>{name}</name><LineString><coordinates>{_coords(points)}</coordinates>"
        "</LineString></Placemark>"
    )


def point_placemark(name: str, lon: float, lat: float) -> str:
    return f"<Placemark><name>{name}</name><Point><coordinates>{lon},{lat},0</coordinates></Point></Placemark>"


def multigeometry_placemark(name: str, lon: float, lat: float) -> str:
    """A MultiGeometry mixing a point and a line -> GeometryCollection (unsupported)."""
    return (
        f"<Placemark><name>{name}</name><MultiGeometry>"
        f"<Point><coordinates>{lon},{lat},0</coordinates></Point>"
        f"<LineString><coordinates>{lon},{lat},0 {lon + 0.01},{lat + 0.01},0</coordinates></LineString>"
        "</MultiGeometry></Placemark>"
    )


def kml_document(*placemarks: str) -> bytes:
    body = "".join(placemarks)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Test</name>'
        f"{body}</Document></kml>"
    ).encode("utf-8")


# -------------------------------------------------------------- ZIP builders
def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def shapefile_zip(gdf, stem: str = "data", folder: str = "", include: Optional[Iterable[str]] = None) -> bytes:
    """Write a GeoDataFrame as a Shapefile and return it zipped."""
    with tempfile.TemporaryDirectory() as tmp:
        gdf.to_file(Path(tmp) / f"{stem}.shp", driver="ESRI Shapefile")
        wanted = {e.lower() for e in include} if include is not None else None
        files: dict[str, bytes] = {}
        for path in sorted(Path(tmp).iterdir()):
            if wanted is not None and path.suffix.lower() not in wanted:
                continue
            name = f"{folder}/{path.name}" if folder else path.name
            files[name] = path.read_bytes()
    return zip_bytes(files)


# ------------------------------------------------------------ HTTP helpers
def post_file(client, filename: str, content: bytes, content_type: str = "application/octet-stream"):
    return client.post("/api/files/", files={"file": (filename, content, content_type)})


def upload_ok(client, filename: str, content: bytes) -> dict[str, Any]:
    response = post_file(client, filename, content)
    assert response.status_code == 201, response.text
    return response.json()


def get_measurements(client, file_id: str) -> dict[str, Any]:
    response = client.get(f"/api/files/{file_id}/measurements/")
    assert response.status_code == 200, response.text
    return response.json()


def feature_name(feature: dict[str, Any]) -> Optional[str]:
    props = feature["properties"]
    return props.get("Name") or props.get("name")


def find_feature(features: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for feature in features:
        if feature_name(feature) == name:
            return feature
    raise AssertionError(f"feature {name!r} not found in {[feature_name(f) for f in features]}")
