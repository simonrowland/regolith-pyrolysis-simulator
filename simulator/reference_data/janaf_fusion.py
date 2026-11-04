"""Hash-checked JANAF solid/liquid Gibbs chords shared by chemistry consumers."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from simulator.reference_data.janaf import (
    JANAF_R_J_MOL_K as JANAF_R_J_PER_MOL_K,
    SIDECAR_PATH,
    TABLES_DIR,
    load_table_document,
)
from simulator.yaml_cache import load_cached_safe_yaml


# Preserve the fusion module's existing name while sharing janaf.py's value.

_FUSION_TABLES = {
    "CaO": ("Ca-027", "Ca-028"),
    "Al2O3": ("Al-096", "Al-100"),
    "MgO": ("Mg-008", "Mg-009"),
    "SiO2": ("O-035", "O-038"),
}

# d-144 closed single-polymorph identity: Ca-027 is lime, Mg-008 is periclase.
# These are the same table IDs used by battery scoring; values are tokens here
# so the reference-data layer does not import battery's domain enums.
_D144_SINGLE_POLYMORPH_TABLES: dict[str, str] = {
    "Ca-027": "lime",
    "Mg-008": "periclase",
}

# Pin the extracted temperature nodes as well as source hashes so a dropped
# compiled row cannot silently widen interpolation.
_FUSION_NODE_SET_SHA256 = {
    "Al-096": "86986e6bc3d75c0fec4c3a034a1deab91e966c42d5e0f1cb4080a33d10aa6bed",
    "Al-097": "86986e6bc3d75c0fec4c3a034a1deab91e966c42d5e0f1cb4080a33d10aa6bed",
    "Al-098": "86986e6bc3d75c0fec4c3a034a1deab91e966c42d5e0f1cb4080a33d10aa6bed",
    "Al-099": "86986e6bc3d75c0fec4c3a034a1deab91e966c42d5e0f1cb4080a33d10aa6bed",
    "Al-100": "b61dfea8f1da3f3a90ba7c7ceedc7b1cd04048c46fb4cf20bf5e5c3c4f0c0b18",
    "Ca-027": "0b4adb5cf7b86a053c456688e9e635947ca38276f7b703cb00062ca5f00dbb5c",
    "Ca-028": "4a9048a895bd8b3e499110d27edbbbe984c8c130881687ee8654a2f750afb765",
    "Mg-008": "b61dfea8f1da3f3a90ba7c7ceedc7b1cd04048c46fb4cf20bf5e5c3c4f0c0b18",
    "Mg-009": "e7c4becbfe7033b2b739a3b12f1404808d4fa05c33c4ee04d15cc1e5c114d8d4",
    "O-035": "86986e6bc3d75c0fec4c3a034a1deab91e966c42d5e0f1cb4080a33d10aa6bed",
    "O-038": "4a9048a895bd8b3e499110d27edbbbe984c8c130881687ee8654a2f750afb765",
}


def _sidecar_hashes(path: Path = SIDECAR_PATH) -> dict[str, str]:
    payload = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("files"), list):
        raise ValueError(f"{path}: invalid JANAF source sidecar")
    return {
        str(row["name"]): str(row["sha256"])
        for row in payload["files"]
        if isinstance(row, Mapping) and row.get("name") and row.get("sha256")
    }


def _checked_fusion_table_rows(table_id: str) -> tuple[list[Mapping], str]:
    document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
    extraction = document.get("extraction")
    table = document.get("table")
    if not isinstance(extraction, Mapping) or not isinstance(table, Mapping):
        raise ValueError(f"{table_id}: JANAF cached table is incomplete")
    digest = str(extraction.get("source_sha256") or "")
    expected = _sidecar_hashes().get(f"{table_id}.txt")
    if not digest or digest != expected:
        raise ValueError(f"{table_id}: JANAF cache hash does not match source sidecar")
    rows = table.get("values")
    if not isinstance(rows, list):
        raise ValueError(f"{table_id}: JANAF table values are missing")
    return rows, digest


@lru_cache(maxsize=2 * len(_FUSION_TABLES))
def _fusion_table_points(
    table_id: str,
) -> tuple[tuple[tuple[Decimal, Decimal], ...], str]:
    rows, digest = _checked_fusion_table_rows(table_id)
    points: list[tuple[Decimal, Decimal]] = []
    temperatures: set[Decimal] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        temperature = row.get("temperature")
        gibbs = row.get("formation_gibbs_energy")
        if not isinstance(temperature, Mapping):
            continue
        t_value = temperature.get("value")
        if t_value is None:
            continue
        t = Decimal(str(t_value))
        temperatures.add(t)
        g_value = gibbs.get("value") if isinstance(gibbs, Mapping) else None
        if g_value is not None:
            points.append((t, Decimal(str(g_value))))
    node_set_sha256 = hashlib.sha256(
        "\n".join(
            str(temperature.normalize()) for temperature in sorted(temperatures)
        ).encode()
    ).hexdigest()
    expected_node_set_sha256 = _FUSION_NODE_SET_SHA256.get(table_id)
    if (
        expected_node_set_sha256 is not None
        and node_set_sha256 != expected_node_set_sha256
    ):
        raise ValueError(f"{table_id}: needed JANAF table node row missing or changed")
    points.sort()
    if len(points) < 2:
        raise ValueError(f"{table_id}: JANAF table has fewer than two Gibbs points")
    return tuple(points), digest


@lru_cache(maxsize=2 * len(_FUSION_TABLES))
def _fusion_missing_gibbs_temperatures(table_id: str) -> tuple[Decimal, ...]:
    """Return table nodes with a temperature but no formation-Gibbs value."""

    document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
    table = document.get("table")
    if not isinstance(table, Mapping):
        raise ValueError(f"{table_id}: JANAF cached table is incomplete")
    rows = table.get("values")
    if not isinstance(rows, list):
        raise ValueError(f"{table_id}: JANAF table values are missing")
    missing: set[Decimal] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        temperature = row.get("temperature")
        gibbs = row.get("formation_gibbs_energy")
        if not isinstance(temperature, Mapping):
            continue
        temperature_value = temperature.get("value")
        gibbs_value = gibbs.get("value") if isinstance(gibbs, Mapping) else None
        if temperature_value is not None and gibbs_value is None:
            missing.add(Decimal(str(temperature_value)))
    return tuple(sorted(missing))


def _missing_node_for_interpolation(
    points: tuple[tuple[Decimal, Decimal], ...],
    missing_nodes: tuple[Decimal, ...],
    temperature_K: Decimal,
) -> Decimal | None:
    if any(node_temperature == temperature_K for node_temperature, _ in points):
        return None
    temperatures = tuple(temperature for temperature, _ in points)
    gaps = tuple(right - left for left, right in zip(temperatures, temperatures[1:]))
    for gap_index, ((left, _), (right, _)) in enumerate(zip(points, points[1:])):
        if left < temperature_K < right:
            absent_grid_nodes = tuple(
                Decimal(candidate)
                for candidate in range(
                    int(left // 100) * 100 + 100,
                    int(right // 100) * 100 + 1,
                    100,
                )
                if left < Decimal(candidate) < right
            )
            for node in sorted(
                {node for node in missing_nodes if left < node < right}
                | set(absent_grid_nodes)
            ):
                run_start = gap_index
                run_end = gap_index + 1
                while run_start > 0 and gaps[run_start - 1] in (
                    Decimal("200"),
                    Decimal("400"),
                ):
                    run_start -= 1
                while run_end < len(gaps) and gaps[run_end] in (
                    Decimal("200"),
                    Decimal("400"),
                ):
                    run_end += 1
                sparse_ladder = (
                    left >= Decimal("3000")
                    and right - left in (Decimal("200"), Decimal("400"))
                    and run_end - run_start >= 2
                )
                reference_interval = (
                    left == Decimal("298.15") and right == Decimal("500")
                )
                if not sparse_ladder and not reference_interval:
                    return node
            return None
    return None


def _complete_fusion_overlap_nodes(
    oxide: str,
    solid_table_id: str,
    liquid_table_id: str,
    solid_points: tuple[tuple[Decimal, Decimal], ...],
    liquid_points: tuple[tuple[Decimal, Decimal], ...],
    temperature_K: Decimal | None,
) -> tuple[
    tuple[Decimal, ...],
    tuple[Decimal, ...],
    tuple[Decimal, ...],
]:
    """Validate shared solid/liquid nodes and target-T Gibbs interpolation."""

    solid_missing = _fusion_missing_gibbs_temperatures(solid_table_id)
    liquid_missing = _fusion_missing_gibbs_temperatures(liquid_table_id)
    overlap = sorted(
        {
            temperature
            for temperature, _ in solid_points
            if liquid_points[0][0] <= temperature <= liquid_points[-1][0]
        }
        | {
            temperature
            for temperature, _ in liquid_points
            if solid_points[0][0] <= temperature <= solid_points[-1][0]
        }
    )
    if not overlap:
        raise ValueError(f"{oxide}: JANAF crystal/liquid tables do not overlap")
    overlap = [
        node
        for node in overlap
        if _missing_node_for_interpolation(solid_points, solid_missing, node) is None
        and _missing_node_for_interpolation(liquid_points, liquid_missing, node) is None
    ]
    if len(overlap) < 2:
        raise ValueError(
            f"{oxide}: JANAF tables have fewer than two complete overlap nodes"
        )
    if temperature_K is not None:
        for table_id, points, missing in (
            (solid_table_id, solid_points, solid_missing),
            (liquid_table_id, liquid_points, liquid_missing),
        ):
            missing_node = _missing_node_for_interpolation(
                points, missing, temperature_K
            )
            if missing_node is not None:
                raise ValueError(
                    f"{table_id}: needed JANAF formation Gibbs row missing at "
                    f"{missing_node} K for interpolation at {temperature_K} K"
                )
    return tuple(overlap), solid_missing, liquid_missing


def _fusion_crossing_temperature(
    oxide: str,
    solid_table_id: str,
    liquid_table_id: str,
    *,
    target_temperature_K: Decimal | None = None,
) -> Decimal:
    """Resolve the unique solid/liquid Gibbs crossing from JANAF nodes."""

    solid_points, _solid_digest = _fusion_table_points(solid_table_id)
    liquid_points, _liquid_digest = _fusion_table_points(liquid_table_id)
    overlap, solid_missing, liquid_missing = _complete_fusion_overlap_nodes(
        oxide,
        solid_table_id,
        liquid_table_id,
        solid_points,
        liquid_points,
        target_temperature_K,
    )

    def difference(temperature_K: Decimal) -> Decimal:
        return _fusion_gibbs_difference_from_points(
            solid_points, liquid_points, temperature_K
        )

    crossings: list[Decimal] = []
    for left, right in zip(overlap, overlap[1:]):
        d_left, d_right = difference(left), difference(right)
        if d_left == 0:
            crossings.append(left)
        elif d_left * d_right < 0:
            crossings.append(
                left - d_left * (right - left) / (d_right - d_left)
            )
    if overlap and difference(overlap[-1]) == 0:
        crossings.append(overlap[-1])
    if len(crossings) != 1:
        raise ValueError(f"{oxide}: expected one JANAF cr/l crossing, got {crossings}")
    crossing = crossings[0]
    for table_id, points, missing_nodes in (
        (solid_table_id, solid_points, solid_missing),
        (liquid_table_id, liquid_points, liquid_missing),
    ):
        missing_node = _missing_node_for_interpolation(
            points, missing_nodes, crossing
        )
        if missing_node is not None:
            raise ValueError(
                f"{table_id}: needed JANAF formation Gibbs row missing at "
                f"{missing_node} K for interpolation at {crossing} K"
            )
    return crossing


def _interpolate_table_points(
    points: tuple[tuple[Decimal, Decimal], ...],
    temperature_K: Decimal,
    *,
    extrapolate: bool = False,
) -> Decimal:
    # Inside a table, linear interpolation uses JANAF's tabulated G values,
    # including the heat-capacity contribution already accumulated between
    # nodes. For a temperature outside a table, the terminal chord is
    # continued: G_ext(T) = G1 + m(T-T1), where m=(G2-G1)/(T2-T1) has units
    # kJ mol^-1 K^-1. Since dG/dT=-S, this fixes S; H=G+TS=G-T(dG/dT) is
    # also constant, so Cp=dH/dT=0 on that continuation. When both phase
    # values are in-table, ΔG is the interpolated G_liquid-G_solid, not the
    # constant-ΔH_fus approximation ΔH_fus(1-T/T_fus). For MgO at 4500 K,
    # the solid uses its 3900-4000 K terminal chord while the liquid remains
    # in-table at its 4400-4500 K interval.
    if temperature_K < points[0][0]:
        if not extrapolate:
            raise ValueError(f"{temperature_K} K is outside the JANAF table range")
        left, right = points[0], points[1]
    elif temperature_K > points[-1][0]:
        if not extrapolate:
            raise ValueError(f"{temperature_K} K is outside the JANAF table range")
        left, right = points[-2], points[-1]
    else:
        left, right = next(
            (a, b)
            for a, b in zip(points, points[1:])
            if a[0] <= temperature_K <= b[0]
        )
    return left[1] + (right[1] - left[1]) * (temperature_K - left[0]) / (
        right[0] - left[0]
    )


def _interpolate_formation_gibbs(
    points: tuple[tuple[Decimal, Decimal], ...], temperature_K: Decimal
) -> Decimal:
    if temperature_K < points[0][0] or temperature_K > points[-1][0]:
        raise ValueError(f"{temperature_K} K is outside the JANAF table range")
    missing_node = _missing_node_for_interpolation(points, (), temperature_K)
    if missing_node is not None:
        raise ValueError(
            f"JANAF formation Gibbs interpolation spans missing grid node "
            f"{missing_node} K at {temperature_K} K"
        )
    return _interpolate_table_points(points, temperature_K)


def _fusion_gibbs_difference_from_points(
    solid_points: tuple[tuple[Decimal, Decimal], ...],
    liquid_points: tuple[tuple[Decimal, Decimal], ...],
    temperature_K: Decimal,
    *,
    extrapolate: bool = False,
) -> Decimal:
    """Return liquid-minus-solid Gibbs energy, strict for scored rows by default."""
    if extrapolate:
        return _interpolate_table_points(
            liquid_points, temperature_K, extrapolate=True
        ) - _interpolate_table_points(
            solid_points, temperature_K, extrapolate=True
        )
    return _interpolate_formation_gibbs(
        liquid_points, temperature_K
    ) - _interpolate_formation_gibbs(solid_points, temperature_K)


def d144_fusion_table_pair(oxide: str) -> tuple[str, str] | None:
    """Return an oxide's d-144 solid/liquid pair, if its solid is identified."""
    pair = _FUSION_TABLES.get(str(oxide))
    if pair is None or pair[0] not in _D144_SINGLE_POLYMORPH_TABLES:
        return None
    return pair


def d144_fusion_delta_g_kJ_per_mol(
    oxide: str, temperature_K: Decimal
) -> Decimal:
    """Return the JANAF liquid-minus-solid Gibbs chord for the d-144 pair."""

    pair = d144_fusion_table_pair(oxide)
    if pair is None:
        raise ValueError(f"no d-144 JANAF fusion pair for {oxide}")
    temperature = Decimal(str(temperature_K))
    solid_points, _solid_digest = _fusion_table_points(pair[0])
    liquid_points, _liquid_digest = _fusion_table_points(pair[1])
    _complete_fusion_overlap_nodes(
        oxide,
        pair[0],
        pair[1],
        solid_points,
        liquid_points,
        temperature,
    )
    return _fusion_gibbs_difference_from_points(
        solid_points, liquid_points, temperature, extrapolate=True
    )
