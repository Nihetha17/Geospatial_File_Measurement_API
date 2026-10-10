"""CRS identification and measurement-projection selection.

``build_crs_context`` inspects the dataset CRS once and classifies it:

* ``missing``      - no CRS declared -> we refuse to guess.
* ``projected``    - measure directly in the CRS, converting its linear unit
                     (metre, foot, US survey foot, ...) to metres.
* ``web_mercator`` - projected, but area/length are heavily distorted; the
                     geometry is converted back to geographic coordinates and
                     re-measured like a geographic CRS.
* ``geographic``   - degrees; geometry is transformed to a local UTM zone (or
                     measured geodesically if the feature is too large).
* ``unsupported``  - anything else (geocentric, engineering, non-degree angular
                     units, ...).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Union

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

# UTM is defined between 80 degrees S and 84 degrees N.
UTM_MIN_LAT = -80.0
UTM_MAX_LAT = 84.0

_WEB_MERCATOR_EPSG = {3857, 900913, 3785, 102100, 102113}


@dataclass(frozen=True)
class CrsContext:
    kind: str
    crs: Optional[CRS] = None  # horizontal CRS used for measuring
    label: Optional[str] = None  # e.g. "EPSG:32644"
    name: Optional[str] = None
    linear_unit_name: Optional[str] = None
    unit_factor: float = 1.0  # metres per linear unit (projected CRS)
    geographic_crs: Optional[CRS] = None
    problem: Optional[str] = None

    @property
    def crs_type(self) -> Optional[str]:
        return {
            "projected": "projected",
            "web_mercator": "projected",
            "geographic": "geographic",
            "unsupported": "other",
        }.get(self.kind)


def describe_crs(crs: CRS) -> tuple[str, str]:
    """Return ``(label, name)``; label is ``EPSG:xxxx`` when identifiable."""
    name = crs.name or "Unnamed CRS"
    label: Optional[str] = None
    try:
        epsg = crs.to_epsg(min_confidence=70)
        if epsg:
            label = f"EPSG:{epsg}"
        else:
            authority = crs.to_authority(min_confidence=70)
            if authority:
                label = f"{authority[0]}:{authority[1]}"
    except Exception:  # identification is best-effort only
        label = None
    return (label or name), name


def _horizontal_crs(crs: CRS) -> CRS:
    horizontal = crs
    if getattr(horizontal, "is_bound", False):
        horizontal = horizontal.source_crs
    if horizontal.is_compound:
        horizontal = horizontal.sub_crs_list[0]
    return horizontal


def is_web_mercator(crs: CRS) -> bool:
    try:
        if crs.to_epsg(min_confidence=70) in _WEB_MERCATOR_EPSG:
            return True
    except Exception:
        pass
    normalized_name = re.sub(r"[_\s]+", " ", (crs.name or "").lower())
    if "web mercator" in normalized_name or "pseudo-mercator" in normalized_name:
        return True
    operation = getattr(crs, "coordinate_operation", None)
    method = re.sub(r"[_\s]+", " ", (operation.method_name if operation else "").lower())
    return "popular visualisation pseudo mercator" in method or "mercator auxiliary sphere" in method


def _linear_unit(crs: CRS) -> tuple[float, str]:
    axes = crs.axis_info[:2]
    if len(axes) < 2:
        raise ValueError("The CRS has fewer than two horizontal axes.")
    factors = [axis.unit_conversion_factor for axis in axes]
    if not math.isclose(factors[0], factors[1], rel_tol=1e-9):
        raise ValueError("The CRS horizontal axes use different units.")
    factor = float(factors[0])
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("The CRS linear unit has an invalid conversion factor.")
    return factor, str(axes[0].unit_name)


def _require_degrees(crs: CRS) -> None:
    for axis in crs.axis_info[:2]:
        if "degree" not in str(axis.unit_name).lower() or not math.isclose(
            axis.unit_conversion_factor, math.pi / 180.0, rel_tol=1e-6
        ):
            raise ValueError("Geographic CRS with angular units other than degrees are not supported.")


def build_crs_context(crs: Optional[CRS]) -> CrsContext:
    if crs is None:
        return CrsContext(kind="missing")
    label, name = describe_crs(crs)
    problem = "The CRS type is not supported for area/length measurement."
    try:
        horizontal = _horizontal_crs(crs)
        if horizontal.is_projected:
            if is_web_mercator(horizontal):
                return CrsContext(
                    kind="web_mercator",
                    crs=horizontal,
                    label=label,
                    name=name,
                    linear_unit_name="metre",
                    geographic_crs=horizontal.geodetic_crs,
                )
            factor, unit_name = _linear_unit(horizontal)
            return CrsContext(
                kind="projected",
                crs=horizontal,
                label=label,
                name=name,
                linear_unit_name=unit_name,
                unit_factor=factor,
            )
        if horizontal.is_geographic:
            _require_degrees(horizontal)
            return CrsContext(
                kind="geographic",
                crs=horizontal,
                label=label,
                name=name,
                geographic_crs=horizontal,
            )
    except (CRSError, ValueError, AttributeError) as exc:
        problem = str(exc)
    return CrsContext(kind="unsupported", label=label, name=name, problem=problem)


def choose_utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the WGS 84 / UTM zone containing the given lon/lat."""
    lon = ((lon + 180.0) % 360.0) - 180.0
    zone = min(60, max(1, int((lon + 180.0) // 6) + 1))
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=128)
def get_transformer(source: Union[CRS, str], target: Union[CRS, str]) -> Transformer:
    """Cached transformer using x=lon/easting, y=lat/northing axis order."""
    return Transformer.from_crs(source, target, always_xy=True)
