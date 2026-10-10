"""Reading validated KML / Shapefile files into ``ParsedDataset`` objects.

GDAL (through ``pyogrio``) does the actual parsing. Everything GDAL returns is
converted to plain Python so later layers do not depend on pandas/GeoPandas.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pyogrio
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.core.config import Settings
from app.core.exceptions import AppError
from app.models.domain import ParsedDataset, SourceFeature
from app.services.serialization import to_json_value

logger = logging.getLogger("geospatial_api")

_UNREADABLE = (
    "The file could not be read as a valid geospatial dataset. It may be corrupt or use an unsupported structure."
)


def _too_many(limit: int) -> AppError:
    return AppError(
        413,
        "TOO_MANY_FEATURES",
        f"The dataset contains more than the maximum of {limit} features.",
        {"max_features": limit},
    )


def _read_layer(path: Path, layer: Optional[str], max_features: int):
    try:
        return pyogrio.read_dataframe(str(path), layer=layer, fid_as_index=True, max_features=max_features)
    except Exception as exc:  # GDAL raises many exception types for bad data
        logger.warning("Could not read dataset layer %r: %s", layer, exc)
        raise AppError(422, "UNREADABLE_DATASET", _UNREADABLE) from None


def _append_features(gdf, layer_label: Optional[str], features: list[SourceFeature]) -> None:
    geometry_column = gdf.geometry.name
    records = gdf.drop(columns=[geometry_column]).to_dict(orient="records")
    geometries = list(gdf.geometry)
    fids = list(gdf.index)
    for fid, geometry, record in zip(fids, geometries, records):
        index = len(features)
        features.append(
            SourceFeature(
                index=index,
                feature_id=str(fid),
                layer=layer_label,
                geometry=geometry if isinstance(geometry, BaseGeometry) else None,
                properties={str(key): to_json_value(value) for key, value in record.items()},
            )
        )


def read_kml(path: Path, settings: Settings) -> ParsedDataset:
    """Read every layer (KML folder) of a KML file, preserving feature order."""
    try:
        layers = [str(row[0]) for row in pyogrio.list_layers(str(path))]
    except Exception as exc:
        logger.warning("Could not list KML layers: %s", exc)
        raise AppError(422, "UNREADABLE_DATASET", _UNREADABLE) from None

    features: list[SourceFeature] = []
    warnings: list[str] = []
    crs: Optional[CRS] = None
    for layer in layers:
        remaining = settings.max_features - len(features)
        gdf = _read_layer(path, layer, remaining + 1)
        if crs is None and gdf.crs is not None:
            crs = CRS.from_user_input(gdf.crs)
        _append_features(gdf, layer, features)
        if len(features) > settings.max_features:
            raise _too_many(settings.max_features)

    if crs is None:
        # The KML specification fixes coordinates to WGS 84 longitude/latitude,
        # so this is a documented format rule, not a guess.
        crs = CRS.from_epsg(4326)
        warnings.append("KML coordinates are defined by the KML specification as WGS 84 (EPSG:4326).")
    return ParsedDataset(file_type="kml", crs=crs, features=features, warnings=warnings)


def read_shapefile(path: Path, settings: Settings) -> ParsedDataset:
    """Read an extracted Shapefile. A missing ``.prj`` yields ``crs=None``."""
    gdf = _read_layer(path, None, settings.max_features + 1)
    features: list[SourceFeature] = []
    _append_features(gdf, None, features)
    if len(features) > settings.max_features:
        raise _too_many(settings.max_features)
    crs = CRS.from_user_input(gdf.crs) if gdf.crs is not None else None
    return ParsedDataset(file_type="shapefile", crs=crs, features=features)
