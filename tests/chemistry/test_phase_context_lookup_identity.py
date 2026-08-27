"""Differential identity tests for the t-735 sargable grind-cache lookup.

The grind-cache row selected by PhaseContext feeds evaporation-path
phase/activity context. A faster query that picks a different row is a
physics change. This module freezes the pre-t-735 ABS() SQL and asserts
the rewritten BETWEEN form returns the same selected output_id.

Coverage is a named boundary matrix (residual-threshold neighbors,
cancellation centers, negative zero, epoch-1-only, empty results,
cross-thread), not a volume count. A smaller dense sweep is kept as
secondary evidence only.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from pathlib import Path

import pytest

from scripts.grid_pregrind_writer import (
    GridCacheWriter,
    install_phase_context_lookup_indexes,
)
from scripts.replay_phase_context_lookup_identity import _copy_and_index
from simulator.chemistry import phase_context as pc
from simulator.chemistry.phase_context import (
    CONTROL_MATCH_TOLERANCE,
    LIQUIDUS_MATCH_TOLERANCE_C,
    MAX_COMPOSITION_DISTANCE,
    _uncached_grind_cache_lookup,
)


# Frozen snapshot of _candidate_rows as of 19a7ab13, before the sargable rewrite.
_LEGACY_SELECT = """
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
def _legacy_candidate_rows(
    connection: sqlite3.Connection,
    *,
    temperature_C: float,
    pressure_bar: float,
    fO2_log: float,
    include_isothermal: bool,
) -> list[sqlite3.Row]:
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
                _LEGACY_SELECT.format(
                    temperature_column="o.generic_temperature_C",
                    scope_literal="'isothermal_epoch_2'",
                    epoch_predicate=">= 2",
                ),
                params,
            ).fetchall()
        )
    rows.extend(
        connection.execute(
            _LEGACY_SELECT.format(
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


def _select_row(
    candidates: list[sqlite3.Row],
    composition_mol: dict[str, float],
    *,
    off_liquidus_request: bool,
) -> int | None:
    if off_liquidus_request:
        candidates = [
            row for row in candidates if int(row["engine_epoch"]) != 1
        ]
    if not candidates:
        return None
    query_fraction = pc._mole_fractions(composition_mol)
    scored: list[tuple[float, int, sqlite3.Row]] = []
    for row in candidates:
        candidate_composition = pc._json_mapping(row["composition_mol_json"])
        distance = pc._composition_distance(
            query_fraction,
            pc._mole_fractions(candidate_composition),
        )
        scored.append((distance, int(row["id"]), row))
    distance, _row_id, selected = min(
        scored, key=lambda item: (item[0], item[1])
    )
    if distance > MAX_COMPOSITION_DISTANCE:
        return None
    return int(selected["id"])


def _legacy_selected_id(
    connection: sqlite3.Connection,
    *,
    temperature_C: float,
    pressure_bar: float,
    fO2_log: float,
    composition_mol: dict[str, float],
    liquidus_temperature_C: float | None,
    max_epoch: int,
) -> int | None:
    off_liquidus_request = (
        liquidus_temperature_C is not None
        and abs(temperature_C - liquidus_temperature_C)
        > LIQUIDUS_MATCH_TOLERANCE_C
    )
    candidates = _legacy_candidate_rows(
        connection,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        fO2_log=fO2_log,
        include_isothermal=max_epoch >= 2,
    )
    return _select_row(
        candidates,
        composition_mol,
        off_liquidus_request=off_liquidus_request,
    )


def _new_selected_id(
    path: Path,
    *,
    temperature_C: float,
    pressure_bar: float,
    fO2_log: float,
    composition_mol: dict[str, float],
    liquidus_temperature_C: float | None,
) -> int | None:
    result, _provenance = _uncached_grind_cache_lookup(
        path,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        composition_mol=composition_mol,
        fO2_log=fO2_log,
        liquidus_temperature_C=liquidus_temperature_C,
    )
    if result is None:
        return None
    return int(result["id"])


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE grid_keys (
            id INTEGER PRIMARY KEY,
            pressure_bar REAL NOT NULL,
            fO2_log REAL NOT NULL,
            composition_mol_json TEXT NOT NULL,
            artifact_kind TEXT NOT NULL
        );
        CREATE TABLE alphamelts_outputs (
            id INTEGER PRIMARY KEY,
            grid_key_id INTEGER NOT NULL,
            engine_epoch INTEGER NOT NULL,
            status TEXT NOT NULL,
            status_kind TEXT NOT NULL,
            generic_phase_assemblage_available INTEGER,
            generic_liquid_fraction REAL,
            generic_phase_masses_kg_json TEXT,
            generic_liquid_composition_wt_pct_json TEXT,
            generic_activity_coefficients_json TEXT,
            generic_temperature_C REAL,
            generic_liquidus_T_C REAL,
            alpha_liquidus_T_C REAL
        );
        """
    )


def _insert_row(
    connection: sqlite3.Connection,
    *,
    row_id: int,
    composition: dict[str, float],
    pressure_bar: float,
    fO2_log: float,
    engine_epoch: int,
    generic_temperature_C: float,
    generic_liquidus_T_C: float | None,
    alpha_liquidus_T_C: float | None,
    status: str = "ok",
    status_kind: str = "success",
    assemblage_available: int | None = 1,
    artifact_kind: str = "equilibrium",
) -> None:
    connection.execute(
        "INSERT INTO grid_keys VALUES (?, ?, ?, ?, ?)",
        (
            row_id,
            pressure_bar,
            fO2_log,
            json.dumps(composition),
            artifact_kind,
        ),
    )
    connection.execute(
        "INSERT INTO alphamelts_outputs VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row_id,
            row_id,
            engine_epoch,
            status,
            status_kind,
            assemblage_available,
            0.5,
            json.dumps({"liquid": 1.0}),
            json.dumps({species: 50.0 for species in composition}),
            "{}",
            generic_temperature_C,
            generic_liquidus_T_C,
            alpha_liquidus_T_C,
        ),
    )


def _write_identity_fixture(
    path: Path, *, with_indexes: bool, include_epoch_2: bool = True
) -> dict[str, dict]:
    compositions = {
        "near": {"SiO2": 1.0, "MgO": 1.0},
        "far": {"SiO2": 1.0, "CaO": 1.0},
        "mid": {"SiO2": 1.0, "MgO": 0.6, "CaO": 0.4},
    }
    with sqlite3.connect(path) as connection:
        _create_schema(connection)
        rows = [
            # Epoch-1 liquidus surface, alpha set.
            dict(
                row_id=1,
                composition=compositions["near"],
                pressure_bar=1.0,
                fO2_log=-9.0,
                engine_epoch=1,
                generic_temperature_C=900.0,
                generic_liquidus_T_C=1299.0,
                alpha_liquidus_T_C=1300.0,
            ),
            # Same T/P/fO2 window, different composition (nearest-match fork).
            dict(
                row_id=2,
                composition=compositions["far"],
                pressure_bar=1.0,
                fO2_log=-9.0,
                engine_epoch=1,
                generic_temperature_C=900.0,
                generic_liquidus_T_C=1300.04,
                alpha_liquidus_T_C=1300.04,
            ),
            # COALESCE fallback: alpha NULL, generic liquidus in window.
            dict(
                row_id=3,
                composition=compositions["mid"],
                pressure_bar=1.0,
                fO2_log=-9.0,
                engine_epoch=1,
                generic_temperature_C=910.0,
                generic_liquidus_T_C=1400.0,
                alpha_liquidus_T_C=None,
            ),
            # Tolerance-edge liquidus at 1200 ± 0.05.
            dict(
                row_id=4,
                composition=compositions["near"],
                pressure_bar=1.0,
                fO2_log=-9.0,
                engine_epoch=1,
                generic_temperature_C=800.0,
                generic_liquidus_T_C=1200.0,
                alpha_liquidus_T_C=1200.0,
            ),
            # Different P.
            dict(
                row_id=5,
                composition=compositions["near"],
                pressure_bar=0.01,
                fO2_log=-9.0,
                engine_epoch=1,
                generic_temperature_C=900.0,
                generic_liquidus_T_C=1300.0,
                alpha_liquidus_T_C=1300.0,
            ),
            # Different fO2.
            dict(
                row_id=6,
                composition=compositions["near"],
                pressure_bar=1.0,
                fO2_log=-8.0,
                engine_epoch=1,
                generic_temperature_C=900.0,
                generic_liquidus_T_C=1300.0,
                alpha_liquidus_T_C=1300.0,
            ),
            # Rows that must never be selected.
            dict(
                row_id=9,
                composition=compositions["near"],
                pressure_bar=1.0,
                fO2_log=-9.0,
                engine_epoch=1,
                generic_temperature_C=900.0,
                generic_liquidus_T_C=1300.0,
                alpha_liquidus_T_C=1300.0,
                status="error",
                status_kind="failure",
                assemblage_available=None,
            ),
            dict(
                row_id=10,
                composition=compositions["near"],
                pressure_bar=1.0,
                fO2_log=-9.0,
                engine_epoch=1,
                generic_temperature_C=900.0,
                generic_liquidus_T_C=1300.0,
                alpha_liquidus_T_C=1300.0,
                artifact_kind="diagnostic",
            ),
            dict(
                row_id=11,
                composition=compositions["near"],
                pressure_bar=1.0,
                fO2_log=-9.0,
                engine_epoch=1,
                generic_temperature_C=900.0,
                generic_liquidus_T_C=1600.0,
                alpha_liquidus_T_C=1600.0,
                assemblage_available=0,
            ),
        ]
        if include_epoch_2:
            rows.extend(
                [
                    dict(
                        row_id=7,
                        composition=compositions["near"],
                        pressure_bar=1.0,
                        fO2_log=-9.0,
                        engine_epoch=2,
                        generic_temperature_C=1500.0,
                        generic_liquidus_T_C=1300.0,
                        alpha_liquidus_T_C=1300.0,
                    ),
                    dict(
                        row_id=8,
                        composition=compositions["far"],
                        pressure_bar=1.0,
                        fO2_log=-9.0,
                        engine_epoch=2,
                        generic_temperature_C=1500.04,
                        generic_liquidus_T_C=1300.0,
                        alpha_liquidus_T_C=1300.0,
                    ),
                ]
            )
        for row in rows:
            _insert_row(connection, **row)
        if with_indexes:
            connection.execute(
                """
                CREATE INDEX idx_alphamelts_outputs_phase_context_liquidus
                ON alphamelts_outputs(
                    status,
                    status_kind,
                    engine_epoch,
                    generic_phase_assemblage_available,
                    COALESCE(alpha_liquidus_T_C, generic_liquidus_T_C)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX idx_alphamelts_outputs_phase_context_isothermal
                ON alphamelts_outputs(
                    status,
                    status_kind,
                    generic_phase_assemblage_available,
                    generic_temperature_C
                )
                WHERE engine_epoch >= 2
                """
            )
        connection.commit()
    return compositions


def _reset_lookup_caches() -> None:
    pc._close_grind_cache_connections()
    pc._max_engine_epoch.cache_clear()
    pc._cached_grind_cache_lookup.cache_clear()


def _residual_threshold_neighbors(stored: float, tolerance: float) -> list[float]:
    """Representable query centers on both sides of |stored - q| <= tol."""
    closed = (stored - tolerance, stored + tolerance)
    neighbors = [stored, -0.0, 0.0]
    for edge in closed:
        neighbors.extend(
            [
                edge,
                math.nextafter(edge, float("-inf")),
                math.nextafter(edge, float("inf")),
            ]
        )
    return neighbors


def _compare_selected_ids(path: Path, cases: list[dict]) -> int:
    _reset_lookup_caches()
    uri = f"file:{path.resolve()}?mode=ro"
    compared = 0
    mismatches: list[str] = []
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        max_epoch = int(
            connection.execute(
                "SELECT MAX(engine_epoch) FROM alphamelts_outputs"
            ).fetchone()[0]
            or 0
        )
        for case in cases:
            legacy = _legacy_selected_id(
                connection, max_epoch=max_epoch, **case
            )
            new = _new_selected_id(path, **case)
            compared += 1
            if legacy != new:
                mismatches.append(f"legacy={legacy} new={new} case={case}")
                if len(mismatches) >= 5:
                    break
    pc._close_grind_cache_connections()
    assert mismatches == [], (
        f"{len(mismatches)} identity mismatches in first failures: "
        + "; ".join(mismatches)
    )
    return compared


def _secondary_sweep_inputs(
    compositions: dict[str, dict[str, float]],
) -> list[dict]:
    """SECONDARY evidence only: a small Cartesian product, not a coverage claim."""
    temperatures = (
        1200.0,
        1200.0 - LIQUIDUS_MATCH_TOLERANCE_C,
        1300.0,
        1300.04,
        1400.0,
        1500.0,
        900.0,
        1700.0,
    )
    pressures = (1.0, 0.01, 1.0 + CONTROL_MATCH_TOLERANCE, 2.0)
    fo2_values = (-9.0, -8.0, -9.0 + CONTROL_MATCH_TOLERANCE, -12.0)
    cases: list[dict] = []
    for temperature_C in temperatures:
        for pressure_bar in pressures:
            for fO2_log in fo2_values:
                for liquidus_temperature_C in (None, temperature_C, 1300.0):
                    for composition in compositions.values():
                        cases.append(
                            {
                                "temperature_C": temperature_C,
                                "pressure_bar": pressure_bar,
                                "fO2_log": fO2_log,
                                "composition_mol": composition,
                                "liquidus_temperature_C": liquidus_temperature_C,
                            }
                        )
    return cases


def _write_control_row(
    path: Path,
    *,
    pressure_bar: float,
    fO2_log: float,
    liquidus_T_C: float = 1300.0,
    composition: dict[str, float] | None = None,
) -> dict[str, float]:
    composition = composition or {"SiO2": 1.0, "MgO": 1.0}
    with sqlite3.connect(path) as connection:
        _create_schema(connection)
        _insert_row(
            connection,
            row_id=1,
            composition=composition,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            engine_epoch=1,
            generic_temperature_C=900.0,
            generic_liquidus_T_C=liquidus_T_C,
            alpha_liquidus_T_C=liquidus_T_C,
        )
        connection.commit()
    return composition


def test_sargable_bounds_overapprox_review_counterexamples():
    """BETWEEN lo/hi must contain every residual-accepted finite x.

    Codex P1: stored 1e-18, center -0.05, τ=0.05. One-ULP of the cancelled
    endpoint is 5e-324 and rejects; ulp(τ) expansion must accept.
    """
    lo, hi = pc._sargable_bounds(-0.05, CONTROL_MATCH_TOLERANCE)
    assert lo <= 1e-18 <= hi
    assert abs(1e-18 - (-0.05)) <= CONTROL_MATCH_TOLERANCE
    stored_p = 0.009999999999999992
    lo, hi = pc._sargable_bounds(0.06, CONTROL_MATCH_TOLERANCE)
    assert lo <= stored_p <= hi
    assert abs(stored_p - 0.06) <= CONTROL_MATCH_TOLERANCE


@pytest.mark.parametrize("with_indexes", [False, True], ids=["no-index", "indexed"])
def test_boundary_matrix_residual_neighbors_cancellation_negzero_empty(
    tmp_path, with_indexes
):
    """Named coverage classes. Volume is not a substitute."""
    cache = tmp_path / "grind-accumulator.db"
    compositions = _write_identity_fixture(cache, with_indexes=with_indexes)
    near = compositions["near"]
    cases: list[dict] = []
    for temperature_C in _residual_threshold_neighbors(
        1300.0, LIQUIDUS_MATCH_TOLERANCE_C
    ):
        cases.append(
            {
                "temperature_C": temperature_C,
                "pressure_bar": 1.0,
                "fO2_log": -9.0,
                "composition_mol": near,
                "liquidus_temperature_C": None,
            }
        )
    for pressure_bar in _residual_threshold_neighbors(
        1.0, CONTROL_MATCH_TOLERANCE
    ):
        cases.append(
            {
                "temperature_C": 1300.0,
                "pressure_bar": pressure_bar,
                "fO2_log": -9.0,
                "composition_mol": near,
                "liquidus_temperature_C": None,
            }
        )
    for fO2_log in _residual_threshold_neighbors(
        -9.0, CONTROL_MATCH_TOLERANCE
    ):
        cases.append(
            {
                "temperature_C": 1300.0,
                "pressure_bar": 1.0,
                "fO2_log": fO2_log,
                "composition_mol": near,
                "liquidus_temperature_C": None,
            }
        )
    cases.extend(
        [
            {
                "temperature_C": 3000.0,
                "pressure_bar": 1.0,
                "fO2_log": -9.0,
                "composition_mol": near,
                "liquidus_temperature_C": None,
            },
            {
                "temperature_C": 1300.0,
                "pressure_bar": 99.0,
                "fO2_log": -9.0,
                "composition_mol": near,
                "liquidus_temperature_C": None,
            },
            {
                "temperature_C": 1300.0,
                "pressure_bar": 1.0,
                "fO2_log": 0.0,
                "composition_mol": near,
                "liquidus_temperature_C": None,
            },
        ]
    )
    _compare_selected_ids(cache, cases)

    cancel = tmp_path / "cancel.db"
    composition = _write_control_row(
        cancel, pressure_bar=1.0, fO2_log=1e-18, liquidus_T_C=1300.0
    )
    _compare_selected_ids(
        cancel,
        [
            {
                "temperature_C": 1300.0,
                "pressure_bar": 1.0,
                "fO2_log": -0.05,
                "composition_mol": composition,
                "liquidus_temperature_C": None,
            },
            {
                "temperature_C": 1300.0,
                "pressure_bar": 1.0,
                "fO2_log": 0.05,
                "composition_mol": composition,
                "liquidus_temperature_C": None,
            },
        ],
    )

    pressure_cancel = tmp_path / "p-cancel.db"
    composition = _write_control_row(
        pressure_cancel,
        pressure_bar=0.009999999999999992,
        fO2_log=-9.0,
        liquidus_T_C=1300.0,
    )
    _compare_selected_ids(
        pressure_cancel,
        [
            {
                "temperature_C": 1300.0,
                "pressure_bar": 0.06,
                "fO2_log": -9.0,
                "composition_mol": composition,
                "liquidus_temperature_C": None,
            }
        ],
    )

    negzero = tmp_path / "negzero.db"
    composition = _write_control_row(
        negzero, pressure_bar=1.0, fO2_log=-0.0, liquidus_T_C=1300.0
    )
    _compare_selected_ids(
        negzero,
        [
            {
                "temperature_C": 1300.0,
                "pressure_bar": 1.0,
                "fO2_log": 0.0,
                "composition_mol": composition,
                "liquidus_temperature_C": None,
            },
            {
                "temperature_C": 1300.0,
                "pressure_bar": 1.0,
                "fO2_log": -0.0,
                "composition_mol": composition,
                "liquidus_temperature_C": None,
            },
            {
                "temperature_C": 1300.0,
                "pressure_bar": -0.0,
                "fO2_log": -0.0,
                "composition_mol": composition,
                "liquidus_temperature_C": None,
            },
        ],
    )


def test_boundary_matrix_epoch_1_only_isothermal_unavailable(tmp_path):
    cache = tmp_path / "epoch1-only.db"
    compositions = _write_identity_fixture(
        cache, with_indexes=False, include_epoch_2=False
    )
    _reset_lookup_caches()
    uri = f"file:{cache.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        max_epoch = int(
            connection.execute(
                "SELECT MAX(engine_epoch) FROM alphamelts_outputs"
            ).fetchone()[0]
            or 0
        )
    assert max_epoch == 1

    on_liquidus = dict(
        temperature_C=1300.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        composition_mol=compositions["near"],
        liquidus_temperature_C=None,
    )
    off_liquidus_empty = dict(
        temperature_C=1500.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        composition_mol=compositions["near"],
        liquidus_temperature_C=1300.0,
    )
    _compare_selected_ids(cache, [on_liquidus, off_liquidus_empty])

    _reset_lookup_caches()
    result, provenance = _uncached_grind_cache_lookup(cache, **off_liquidus_empty)
    pc._close_grind_cache_connections()
    assert result is None
    assert provenance["isothermal_status"] == "empty_pending_epoch_2_regrind"
    assert "isothermal_tier_empty_pending_epoch_2_regrind" in provenance["reason"]


def test_boundary_matrix_cross_thread_distinct_query_keys(tmp_path):
    cache = tmp_path / "grind-accumulator.db"
    compositions = _write_identity_fixture(cache, with_indexes=False)
    _reset_lookup_caches()
    near = compositions["near"]
    keys = {
        "a": dict(
            temperature_C=1300.00,
            pressure_bar=1.0,
            composition_mol=near,
            fO2_log=-9.0,
            liquidus_temperature_C=None,
        ),
        "b": dict(
            temperature_C=1500.00,
            pressure_bar=1.0,
            composition_mol=near,
            fO2_log=-9.0,
            liquidus_temperature_C=None,
        ),
    }
    selected: dict[str, int | None] = {}
    provenance: dict[str, dict] = {}
    errors: dict[str, BaseException] = {}
    idents: dict[str, int] = {}
    both_alive = threading.Barrier(2)
    first_lookup_done = threading.Event()
    keep_alive = threading.Barrier(2)

    def _worker(name: str) -> None:
        idents[name] = threading.get_ident()
        try:
            both_alive.wait(timeout=10)
            if name != "a":
                if not first_lookup_done.wait(timeout=10):
                    raise TimeoutError("first lookup did not complete")
            result, prov = _uncached_grind_cache_lookup(cache, **keys[name])
            selected[name] = None if result is None else int(result["id"])
            provenance[name] = prov
        except Exception as exc:
            errors[name] = exc
        finally:
            first_lookup_done.set()
            try:
                keep_alive.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass

    first = threading.Thread(target=_worker, args=("a",))
    second = threading.Thread(target=_worker, args=("b",))
    first.start()
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)
    assert not first.is_alive()
    assert not second.is_alive()
    assert idents.get("a") != idents.get("b")
    assert errors == {}
    for name in ("a", "b"):
        assert provenance[name].get("status") != "unavailable"
        assert "grind_cache_read_failed" not in str(
            provenance[name].get("reason", "")
        )

    uri = f"file:{cache.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for name, case in keys.items():
            legacy = _legacy_selected_id(connection, max_epoch=2, **case)
            assert selected[name] == legacy
    pc._close_grind_cache_connections()


@pytest.mark.parametrize("with_indexes", [False, True], ids=["no-index", "indexed"])
def test_secondary_dense_sweep_legacy_identity(tmp_path, with_indexes):
    """SECONDARY evidence: smaller Cartesian product, not a coverage claim."""
    cache = tmp_path / "grind-accumulator.db"
    compositions = _write_identity_fixture(cache, with_indexes=with_indexes)
    compared = _compare_selected_ids(
        cache, _secondary_sweep_inputs(compositions)
    )
    assert compared == 8 * 4 * 4 * 3 * 3


def test_limit_1_on_temperature_order_selects_the_wrong_row(tmp_path):
    cache = tmp_path / "grind-accumulator.db"
    compositions = _write_identity_fixture(cache, with_indexes=False)
    # Query T is exactly row 1's liquidus. Row 2 is 0.04 C away (still inside
    # the 0.05 C window) but is the composition-nearest match. LIMIT 1 on the
    # temperature ORDER BY would return row 1 and starve the composition post-filter.
    case = dict(
        temperature_C=1300.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        composition_mol=compositions["far"],
        liquidus_temperature_C=None,
    )
    pc._close_grind_cache_connections()
    pc._max_engine_epoch.cache_clear()
    new_id = _new_selected_id(cache, **case)
    uri = f"file:{cache.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        legacy_id = _legacy_selected_id(connection, max_epoch=2, **case)
        limited = connection.execute(
            _LEGACY_SELECT.format(
                temperature_column=(
                    "COALESCE(o.alpha_liquidus_T_C, o.generic_liquidus_T_C)"
                ),
                scope_literal="'liquidus_surface_epoch_1'",
                epoch_predicate="= 1",
            )
            + " LIMIT 1",
            (
                1300.0,
                LIQUIDUS_MATCH_TOLERANCE_C,
                1.0,
                CONTROL_MATCH_TOLERANCE,
                -9.0,
                CONTROL_MATCH_TOLERANCE,
                1300.0,
            ),
        ).fetchone()
    pc._close_grind_cache_connections()
    assert new_id == legacy_id == 2
    assert int(limited["id"]) == 1
    assert int(limited["id"]) != new_id


def test_uncached_lookup_reuses_sqlite_connection(tmp_path, monkeypatch):
    cache = tmp_path / "grind-accumulator.db"
    compositions = _write_identity_fixture(cache, with_indexes=False)
    pc._close_grind_cache_connections()
    pc._max_engine_epoch.cache_clear()
    original_connect = sqlite3.connect
    calls: list[object] = []

    def _counting_connect(*args, **kwargs):
        calls.append(1)
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(pc.sqlite3, "connect", _counting_connect)
    kwargs = dict(
        temperature_C=1300.0,
        pressure_bar=1.0,
        composition_mol=compositions["near"],
        fO2_log=-9.0,
        liquidus_temperature_C=None,
    )
    _uncached_grind_cache_lookup(cache, **kwargs)
    kwargs["temperature_C"] = 1300.01
    _uncached_grind_cache_lookup(cache, **kwargs)
    pc._close_grind_cache_connections()
    assert len(calls) == 1


def test_phase_context_lookup_indexes_created_on_fresh_and_existing_db(tmp_path):
    database = tmp_path / "grid.db"
    with GridCacheWriter(database):
        pass
    names = {
        row[0]
        for row in sqlite3.connect(database).execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "idx_alphamelts_outputs_phase_context_liquidus" in names
    assert "idx_alphamelts_outputs_phase_context_isothermal" in names

    with sqlite3.connect(database) as connection:
        connection.execute(
            "DROP INDEX idx_alphamelts_outputs_phase_context_liquidus"
        )
    with GridCacheWriter(database, existing_only=True):
        pass
    names = {
        row[0]
        for row in sqlite3.connect(database).execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "idx_alphamelts_outputs_phase_context_liquidus" in names


def test_install_phase_context_lookup_indexes_on_schema_without_v2_provenance(
    tmp_path,
):
    """Writer validation cannot open a pre-v2 cache; the DDL helper can."""
    source = tmp_path / "legacy.db"
    with sqlite3.connect(source) as connection:
        _create_schema(connection)
        connection.commit()
    with pytest.raises(ValueError):
        with GridCacheWriter(source, existing_only=True):
            pass
    dest = _copy_and_index(source, tmp_path / "indexed")
    names = {
        row[0]
        for row in sqlite3.connect(dest).execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "idx_alphamelts_outputs_phase_context_liquidus" in names
    assert "idx_alphamelts_outputs_phase_context_isothermal" in names
    with sqlite3.connect(source) as connection:
        install_phase_context_lookup_indexes(connection)
        connection.commit()
    source_names = {
        row[0]
        for row in sqlite3.connect(source).execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "idx_alphamelts_outputs_phase_context_liquidus" in source_names


def test_cached_miss_is_invalidated_by_a_commit_into_a_still_live_wal(tmp_path):
    """b-289: a cached refusal must not outlive the row that answers it.

    The grind accumulator runs WAL with synchronous=NORMAL, so a writer that
    commits while staying OPEN appends to <db>-wal and never touches the main
    file's mtime. The lookup LRU keyed only on that mtime, so a cached MISS kept
    being served after the row existed -- a long-lived web or background process
    would strand its own producer until checkpoint, LRU eviction, or an explicit
    cache clear.

    This is the case the existing coverage could not reach: the identity matrix
    exercises row selection and connection reuse, but nothing performed a cached
    miss followed by a commit into a live WAL. Note the writer is deliberately
    NOT closed -- closing it checkpoints, which moves the main mtime and hides
    the bug.
    """

    cache = tmp_path / "grind-accumulator.db"
    writer = sqlite3.connect(str(cache))
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA synchronous=NORMAL")
        _create_schema(writer)
        writer.commit()

        composition = {"SiO2": 45.0, "MgO": 30.0, "FeO": 15.0, "Al2O3": 10.0}
        # ON the liquidus surface. Epoch 1 is liquidus-surface-only and the
        # isothermal tier is empty pending the epoch-2 regrind, so an off-liquidus
        # query refuses for a legitimate reason that has nothing to do with the
        # cache -- which would make this test pass for the wrong reason, or rather
        # fail for the wrong one.
        query = dict(
            temperature_C=1600.0,
            pressure_bar=1.0,
            composition_mol=composition,
            fO2_log=-8.0,
            liquidus_temperature_C=1600.0,
        )

        _reset_lookup_caches()
        mtime_before = cache.stat().st_mtime_ns

        # 1. miss, and the miss is now in the LRU
        missed, _ = pc._grind_cache_lookup(cache, **query)
        assert missed is None

        # 2. the producer commits, and stays open -- the live-WAL case
        _insert_row(
            writer,
            row_id=1,
            composition=composition,
            pressure_bar=1.0,
            fO2_log=-8.0,
            engine_epoch=1,
            generic_temperature_C=1600.0,
            generic_liquidus_T_C=1600.0,
            alpha_liquidus_T_C=1600.0,
        )
        writer.commit()

        # The premise of the bug: the main file did not move.
        assert cache.stat().st_mtime_ns == mtime_before, (
            "this test only proves anything while the main-file mtime is "
            "unchanged; if a checkpoint moved it, the stale-cache path is not "
            "being exercised"
        )

        # 3. the same query must now see the row, from the same process
        found, _ = pc._grind_cache_lookup(cache, **query)
        assert found is not None, (
            "cached refusal outlived the committed row -- the lookup key cannot "
            "see the WAL"
        )
    finally:
        writer.close()
        _reset_lookup_caches()


def test_readonly_connection_cache_is_deliberately_not_wal_keyed(tmp_path):
    """b-289 non-target: only the DATA caches became WAL-aware.

    Passes before and after the fix. It exists so that a later "unify these three
    call sites" cleanup goes red. A read-only connection opened BEFORE a WAL
    commit still returns the new row, because each autocommit execute() starts a
    fresh read transaction -- so this cache does not go stale, and keying it on
    the WAL would rebuild the connection on every single write for no benefit.
    Same syntactic shape as the two caches above it, opposite requirement.
    """

    cache = tmp_path / "conn.db"
    writer = sqlite3.connect(str(cache))
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA synchronous=NORMAL")
        _create_schema(writer)
        writer.commit()

        reader = pc._grind_cache_readonly_connection(cache)
        assert reader.execute("SELECT count(*) FROM alphamelts_outputs").fetchone()[0] == 0

        _insert_row(
            writer,
            row_id=1,
            composition={"SiO2": 45.0},
            pressure_bar=1.0,
            fO2_log=-8.0,
            engine_epoch=1,
            generic_temperature_C=1500.0,
            generic_liquidus_T_C=1600.0,
            alpha_liquidus_T_C=1600.0,
        )
        writer.commit()

        # Same cached connection object, and it sees the commit unaided.
        again = pc._grind_cache_readonly_connection(cache)
        assert again is reader
        assert again.execute("SELECT count(*) FROM alphamelts_outputs").fetchone()[0] == 1
    finally:
        writer.close()
        _reset_lookup_caches()
