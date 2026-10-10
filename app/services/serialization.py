"""Conversion of attribute values to JSON-safe Python types.

Dataset attributes come back from pandas/numpy and may contain ``NaN``,
``NaT``, numpy scalars, timestamps or bytes - none of which can be emitted
as strict JSON directly.
"""
from __future__ import annotations

import math
import numbers
from typing import Any

import numpy as np
import pandas as pd


def to_json_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):  # NaN, NaT, pd.NA (scalars only)
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        return number if math.isfinite(number) else None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (ValueError, TypeError):
            return str(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)
