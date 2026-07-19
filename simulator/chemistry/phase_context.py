"""Diagnostic per-species phase/activity context.

Phase 0 is deliberately read-only: callers may log this result, but must not
use it to alter chemistry until the consumer migration gate is opened.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..melt_regime import MeltRegime, melt_regime

LIQUIDUS_MATCH_TOLERANCE_C = 0.05
COMPOSITION_MATCH_EPS = 1.0e-6
MAX_COMPOSITION_DISTANCE = 0.05
CONTROL_MATCH_TOLERANCE = 0.05
DEFAULT_GRIND_CACHE = Path("docs-private/recipe-db/grind-accumulator.db")


class InvalidLiquidFractionError(ValueError):
    """Raised when a supplied liquid fraction is outside its contract."""


def PhaseContext(
    T: float,
    P: float,
    composition: Mapping[str, float],
    fO2: float,
    *,
    molar_masses: Mapping[str, float],
    scalar_liquid_fraction: float | None = None,
    verified_scalar_source: str | None = None,
    grind_cache_path: str | Path | None = None,
    liquidus_temperature_C: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Return phase/activity context for each positive-composition species.

    Tier 1 reads the grind accumulator. Epoch-1 rows are eligible only when
    ``T`` matches their engine liquidus; they never support an isothermal
    assemblage claim. Epoch-2 isothermal retrieval remains fail-closed until
    those rows exist. Tier 2 consumes the caller's already-resolved scalar
    liquid fraction. Its source defaults to caller-supplied unless the caller
    verifies a stronger provenance label. Tier 3 is an explicitly labelled
    unity fallback. Molar masses are injected to keep this helper a leaf.
    """

    temperature_C = _finite(T, "T")
    pressure_bar = _finite(P, "P")
    fO2_log = _finite(fO2, "fO2")
    scalar = _caller_fraction(scalar_liquid_fraction)
    composition_mol = _positive_composition(composition)
    if not composition_mol:
        return {}

    cache_path = Path(grind_cache_path or DEFAULT_GRIND_CACHE)
    cache_result, cache_provenance = _grind_cache_lookup(
        cache_path,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        composition_mol=composition_mol,
        fO2_log=fO2_log,
        liquidus_temperature_C=liquidus_temperature_C,
    )
    if cache_result is not None:
        selected_provenance = {
            "schema": "phase_context_provenance_v1",
            "selected_tier": "grind_cache_assemblage",
            "diagnostic_only": True,
            "behavioral_authority": False,
            "fO2_authority": "caller_supplied_existing_gate",
            "grind_cache": cache_provenance,
        }
        return _cache_species_context(
            composition_mol,
            cache_result,
            selected_provenance,
            molar_masses,
        )

    if scalar is not None:
        provenance = {
            "schema": "phase_context_provenance_v1",
            "selected_tier": "kress_scalar_liquid_fraction",
            "diagnostic_only": True,
            "behavioral_authority": False,
            "fO2_authority": "caller_supplied_existing_gate",
            "scalar_source": (
                verified_scalar_source or "caller_supplied_liquid_fraction"
            ),
            "grind_cache": cache_provenance,
        }
        return {
            species: _species_record(
                scalar,
                activity_basis="existing_gamma_x_activity_basis",
                provenance=provenance,
            )
            for species in composition_mol
        }

    provenance = {
        "schema": "phase_context_provenance_v1",
        "selected_tier": "labeled_unity_fallback",
        "diagnostic_only": True,
        "behavioral_authority": False,
        "fO2_authority": "caller_supplied_existing_gate",
        "grind_cache": cache_provenance,
        "fallback_reason": "no_resolved_scalar_liquid_fraction",
    }
    return {
        species: _species_record(
            1.0,
            activity_basis="unity_assumption",
            provenance=provenance,
        )
        for species in composition_mol
    }


def _grind_cache_lookup(
    path: Path,
    *,
    temperature_C: float,
    pressure_bar: float,
    composition_mol: Mapping[str, float],
    fO2_log: float,
    liquidus_temperature_C: float | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if path.is_file():
        return _cached_grind_cache_lookup(
            str(path.resolve()),
            path.stat().st_mtime_ns,
            temperature_C,
            pressure_bar,
            tuple(sorted(composition_mol.items())),
            fO2_log,
            liquidus_temperature_C,
        )
    return _uncached_grind_cache_lookup(
        path,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        composition_mol=composition_mol,
        fO2_log=fO2_log,
        liquidus_temperature_C=liquidus_temperature_C,
    )


@lru_cache(maxsize=512)
def _cached_grind_cache_lookup(
    path: str,
    _mtime_ns: int,
    temperature_C: float,
    pressure_bar: float,
    composition_items: tuple[tuple[str, float], ...],
    fO2_log: float,
    liquidus_temperature_C: float | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    return _uncached_grind_cache_lookup(
        Path(path),
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        composition_mol=dict(composition_items),
        fO2_log=fO2_log,
        liquidus_temperature_C=liquidus_temperature_C,
    )


def _uncached_grind_cache_lookup(
    path: Path,
    *,
    temperature_C: float,
    pressure_bar: float,
    composition_mol: Mapping[str, float],
    fO2_log: float,
    liquidus_temperature_C: float | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    provenance: dict[str, Any] = {
        "source": "grind-accumulator.db",
        "path": str(path),
        "epoch_1_scope": "liquidus_surface_only",
        "isothermal_status": "empty_pending_epoch_2_regrind",
    }
    if not path.is_file():
        provenance.update(status="unavailable", reason="grind_cache_missing")
        return None, provenance

    try:
        max_epoch = _max_engine_epoch(
            str(path.resolve()),
            path.stat().st_mtime_ns,
        )
        liquidus_hint = (
            None
            if liquidus_temperature_C is None
            else _finite(liquidus_temperature_C, "liquidus_temperature_C")
        )
        off_liquidus_request = (
            liquidus_hint is not None
            and abs(temperature_C - liquidus_hint)
            > LIQUIDUS_MATCH_TOLERANCE_C
        )
        uri = f"file:{path.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            database_id = _metadata_value(connection, "database_id")
            if database_id:
                provenance["database_id"] = database_id
            if max_epoch >= 2:
                provenance["isothermal_status"] = "available_epoch_2"
            candidates = _candidate_rows(
                connection,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                include_isothermal=max_epoch >= 2,
            )
            if off_liquidus_request:
                refused_epoch_1 = sum(
                    int(row["engine_epoch"]) == 1 for row in candidates
                )
                candidates = [
                    row
                    for row in candidates
                    if int(row["engine_epoch"]) != 1
                ]
                provenance.update(
                    epoch_1_candidate_status="refused_off_liquidus_request",
                    epoch_1_candidates_refused=refused_epoch_1,
                    liquidus_temperature_C=liquidus_hint,
                )
    except (OSError, sqlite3.Error) as exc:
        provenance.update(
            status="unavailable",
            reason=f"grind_cache_read_failed:{type(exc).__name__}",
        )
        return None, provenance

    if not candidates:
        reasons = []
        if off_liquidus_request:
            reasons.append("off_liquidus_request")
        reasons.append("no_applicable_assemblage")
        if (
            provenance["isothermal_status"]
            == "empty_pending_epoch_2_regrind"
        ):
            reasons.append("isothermal_tier_empty_pending_epoch_2_regrind")
        provenance.update(
            status="refused",
            reason=";".join(reasons),
        )
        return None, provenance

    query_fraction = _mole_fractions(composition_mol)
    scored: list[tuple[float, int, sqlite3.Row]] = []
    for row in candidates:
        candidate_composition = _json_mapping(row["composition_mol_json"])
        distance = _composition_distance(
            query_fraction,
            _mole_fractions(candidate_composition),
        )
        scored.append((distance, int(row["id"]), row))
    distance, _row_id, selected = min(scored, key=lambda item: (item[0], item[1]))
    if distance > MAX_COMPOSITION_DISTANCE:
        provenance.update(
            status="refused",
            reason="nearest_composition_outside_t171_distance_gate",
            composition_distance=distance,
        )
        return None, provenance

    execution_scope = str(selected["execution_scope"])
    provenance.update(
        status="selected",
        retrieval=(
            "exact" if distance <= COMPOSITION_MATCH_EPS else "nearest"
        ),
        composition_distance=distance,
        output_id=int(selected["id"]),
        engine_epoch=int(selected["engine_epoch"]),
        execution_scope=execution_scope,
        executed_temperature_C=float(selected["applicable_temperature_C"]),
        requested_temperature_C=temperature_C,
    )
    return dict(selected), provenance


@lru_cache(maxsize=8)
def _max_engine_epoch(path: str, _mtime_ns: int) -> int:
    uri = f"file:{path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute(
            "SELECT MAX(engine_epoch) FROM alphamelts_outputs"
        ).fetchone()
    return int(row[0] or 0)


def _metadata_value(connection: sqlite3.Connection, key: str) -> str | None:
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else str(row[0])


def _candidate_rows(
    connection: sqlite3.Connection,
    *,
    temperature_C: float,
    pressure_bar: float,
    fO2_log: float,
    include_isothermal: bool,
) -> list[sqlite3.Row]:
    common_select = """
        SELECT o.id, o.engine_epoch, g.composition_mol_json,
               o.generic_liquid_fraction, o.generic_phase_masses_kg_json,
               o.generic_liquid_composition_wt_pct_json,
               o.generic_activity_coefficients_json,
               {temperature_column} AS applicable_temperature_C,
               {scope_literal} AS execution_scope
          FROM alphamelts_outputs o
          JOIN grid_keys g ON g.id = o.grid_key_id
         WHERE o.status = 'ok'
           AND o.status_kind = 'success'
           AND o.generic_phase_assemblage_available = 1
           AND g.artifact_kind = 'equilibrium'
           AND o.engine_epoch {epoch_predicate}
           AND ABS({temperature_column} - ?) <= ?
           AND ABS(g.pressure_bar - ?) <= ?
           AND ABS(g.fO2_log - ?) <= ?
         ORDER BY ABS({temperature_column} - ?), o.id
    """
    params = (
        temperature_C,
        LIQUIDUS_MATCH_TOLERANCE_C,
        pressure_bar,
        CONTROL_MATCH_TOLERANCE,
        fO2_log,
        CONTROL_MATCH_TOLERANCE,
        temperature_C,
    )
    rows: list[sqlite3.Row] = []
    if include_isothermal:
        rows.extend(
            connection.execute(
                common_select.format(
                    temperature_column="o.generic_temperature_C",
                    scope_literal="'isothermal_epoch_2'",
                    epoch_predicate=">= 2",
                ),
                params,
            ).fetchall()
        )
    rows.extend(
        connection.execute(
            common_select.format(
                temperature_column=(
                    "COALESCE(o.alpha_liquidus_T_C, "
                    "o.generic_liquidus_T_C)"
                ),
                scope_literal="'liquidus_surface_epoch_1'",
                epoch_predicate="= 1",
            ),
            params,
        ).fetchall()
    )
    return rows


def _cache_species_context(
    composition_mol: Mapping[str, float],
    row: Mapping[str, Any],
    provenance: Mapping[str, Any],
    molar_masses: Mapping[str, float],
) -> dict[str, dict[str, Any]]:
    phase_masses = _json_mapping(row.get("generic_phase_masses_kg_json"))
    liquid_mass = sum(
        float(mass)
        for phase_name, mass in phase_masses.items()
        if str(phase_name).strip().lower().startswith("liquid")
    )
    total_phase_mass = sum(phase_masses.values())
    # All-liquid shortcut applies ONLY when the assemblage is genuinely
    # single-phase liquid: any POSITIVE non-liquid phase mass (even a trace
    # crystal) means partitioning is real and must go through the
    # reconstruction path below. Numerical tolerance would silently absorb
    # trace solids (a 5e-11 kg crystal in 0.1 kg melt is a physical phase,
    # not float noise) — the engine writes exact zeros for absent phases,
    # so exact comparison is the honest test.
    non_liquid_mass = sum(
        float(mass)
        for phase_name, mass in phase_masses.items()
        if not str(phase_name).strip().lower().startswith("liquid")
    )
    if (
        total_phase_mass > 0.0
        and liquid_mass > 0.0
        and non_liquid_mass == 0.0
    ):
        return {
            species: _species_record(
                1.0,
                activity_basis="existing_gamma_x_activity_basis",
                provenance=provenance,
            )
            for species in composition_mol
        }

    bulk_mass = {
        species: amount * float(molar_masses.get(species, 0.0) or 0.0)
        for species, amount in composition_mol.items()
    }
    liquid_wt_pct = _json_mapping(
        row.get("generic_liquid_composition_wt_pct_json")
    )
    scalar = _optional_fraction(row.get("generic_liquid_fraction"))
    if scalar is None:
        scalar = 0.0

    total_bulk_mass = sum(bulk_mass.values())
    mass_scale = (
        total_phase_mass / total_bulk_mass
        if total_bulk_mass > 0.0 and total_phase_mass > 0.0
        else 1.0
    )
    fe_species = ("FeO", "Fe2O3")
    bulk_fe_cation_mol = mass_scale * sum(
        composition_mol.get(species, 0.0) * (2.0 if species == "Fe2O3" else 1.0)
        for species in fe_species
    )
    liquid_fe_cation_mol = sum(
        (
            liquid_mass
            * float(liquid_wt_pct.get(species, 0.0) or 0.0)
            / 100.0
            / float(molar_masses.get(species, 1.0))
            * (2.0 if species == "Fe2O3" else 1.0)
        )
        for species in fe_species
    )
    fe_liquid_fraction = (
        max(0.0, min(1.0, liquid_fe_cation_mol / bulk_fe_cation_mol))
        if bulk_fe_cation_mol > 0.0
        else None
    )

    result: dict[str, dict[str, Any]] = {}
    for species in composition_mol:
        species_bulk_mass = bulk_mass.get(species, 0.0) * mass_scale
        if species_bulk_mass > 0.0 and liquid_mass > 0.0:
            liquid_species_mass = liquid_mass * float(
                liquid_wt_pct.get(species, 0.0) or 0.0
            ) / 100.0
            liquid_fraction = max(
                0.0, min(1.0, liquid_species_mass / species_bulk_mass)
            )
        else:
            liquid_fraction = scalar
        species_provenance = dict(provenance)
        if species in fe_species and fe_liquid_fraction is not None:
            liquid_fraction = fe_liquid_fraction
            species_provenance["coupled_fe_cation_basis"] = True
        result[species] = _species_record(
            liquid_fraction,
            activity_basis="existing_gamma_x_activity_basis",
            provenance=species_provenance,
        )
    return result


def _species_record(
    liquid_fraction: float,
    *,
    activity_basis: str,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    regime = melt_regime(liquid_fraction=liquid_fraction, epsilon=0.0)
    phase = {
        MeltRegime.FROZEN: "solid",
        MeltRegime.PARTIAL: "mixed",
        MeltRegime.MOLTEN: "liquid",
    }[regime]
    return {
        "phase": phase,
        "activity_basis": activity_basis,
        "liquid_fraction": float(liquid_fraction),
        "provenance": dict(provenance),
    }


def _positive_composition(composition: Mapping[str, float]) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for species, raw_amount in composition.items():
        amount = _finite(raw_amount, f"composition[{species!r}]")
        if amount > 0.0:
            cleaned[str(species)] = amount
    return cleaned


def _mole_fractions(composition: Mapping[str, float]) -> dict[str, float]:
    total = sum(float(value) for value in composition.values())
    if total <= 0.0:
        return {}
    return {species: float(value) / total for species, value in composition.items()}


def _composition_distance(
    left: Mapping[str, float],
    right: Mapping[str, float],
) -> float:
    species = set(left) | set(right)
    return math.sqrt(
        sum((float(left.get(name, 0.0)) - float(right.get(name, 0.0))) ** 2
            for name in species)
    )


def _json_mapping(value: Any) -> dict[str, float]:
    if isinstance(value, Mapping):
        return {str(key): float(raw) for key, raw in value.items()}
    if value in (None, ""):
        return {}
    parsed = json.loads(str(value))
    if not isinstance(parsed, Mapping):
        return {}
    return {str(key): float(raw) for key, raw in parsed.items()}


def _optional_fraction(value: Any) -> float | None:
    if value is None:
        return None
    try:
        fraction = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
        return None
    return fraction


def _caller_fraction(value: Any) -> float | None:
    if value is None:
        return None
    try:
        fraction = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidLiquidFractionError(
            "scalar_liquid_fraction must be within [0, 1]"
        ) from exc
    if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
        raise InvalidLiquidFractionError(
            f"scalar_liquid_fraction must be within [0, 1]; got {value!r}"
        )
    return fraction


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite; got {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite; got {value!r}")
    return number


__all__ = ["InvalidLiquidFractionError", "PhaseContext"]
