"""Shared crucible sizing geometry for equipment design and stated defaults."""

from __future__ import annotations

import math
from typing import NamedTuple


# Basaltic melt ~2500-2900 kg/m³.
MELT_DENSITY_KG_M3 = 2700.0
FREEBOARD_PERCENT = 20.0
HEIGHT_TO_DIAMETER = 1.5


class CrucibleGeometry(NamedTuple):
    volume_m3: float
    diameter_m: float
    height_m: float
    melt_surface_area_m2: float
    freeboard_pct: float


def size_crucible_geometry(
    mass_kg: float,
    melt_density_kg_m3: float = MELT_DENSITY_KG_M3,
) -> CrucibleGeometry:
    """Size the melt vessel from charge mass using EQ-1.

    Melt volume is mass/density with the declared freeboard added. For the
    fixed cylinder ratio, total volume is ``HEIGHT_TO_DIAMETER * π * d³ / 4``;
    the returned melt face is ``π * d² / 4``. The volume is m³, diameter m,
    and area m²; with fixed density and geometry, area scales as mass**(2/3).
    """

    melt_volume_m3 = mass_kg / melt_density_kg_m3
    total_volume_m3 = melt_volume_m3 * (1.0 + FREEBOARD_PERCENT / 100.0)
    diameter_m = (
        total_volume_m3 * 4.0 / (HEIGHT_TO_DIAMETER * math.pi)
    ) ** (1.0 / 3.0)
    height_m = HEIGHT_TO_DIAMETER * diameter_m
    melt_surface_area_m2 = math.pi * (diameter_m / 2) ** 2
    return CrucibleGeometry(
        volume_m3=total_volume_m3,
        diameter_m=diameter_m,
        height_m=height_m,
        melt_surface_area_m2=melt_surface_area_m2,
        freeboard_pct=FREEBOARD_PERCENT,
    )
