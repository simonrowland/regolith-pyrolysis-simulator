"""Shared geometric identities used by process and residue models."""

from __future__ import annotations

import math


def _cylinder_volume_m3(diameter_m: float, height_m: float) -> float:
    return math.pi * (float(diameter_m) / 2.0) ** 2 * float(height_m)


def _sphere_surface_area_m2_from_volume(volume_m3: float) -> float:
    radius_m = _sphere_radius_m_from_volume(volume_m3)
    return _sphere_surface_area_m2_from_radius(radius_m)


def _sphere_surface_area_m2_from_mass_density(
    mass_kg: float,
    density_kg_m3: float,
) -> float:
    radius_m = _sphere_radius_m_from_mass_density(mass_kg, density_kg_m3)
    return _sphere_surface_area_m2_from_radius(radius_m)


def _sphere_radius_m_from_volume(volume_m3: float) -> float:
    return (3.0 * float(volume_m3) / (4.0 * math.pi)) ** (1.0 / 3.0)


def _sphere_radius_m_from_mass_density(
    mass_kg: float,
    density_kg_m3: float,
) -> float:
    return (
        3.0 * float(mass_kg) / (4.0 * math.pi * float(density_kg_m3))
    ) ** (1.0 / 3.0)


def _sphere_surface_area_m2_from_radius(radius_m: float) -> float:
    return 4.0 * math.pi * float(radius_m) ** 2
