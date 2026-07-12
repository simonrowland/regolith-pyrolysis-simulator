from __future__ import annotations

import ast
import json
import math
import sqlite3
from pathlib import Path

import pytest

from simulator.chemistry.phase_context import (
    InvalidLiquidFractionError,
    PhaseContext,
)
from simulator.state import MOLAR_MASS


def _phase_context(*args, **kwargs):
    return PhaseContext(*args, molar_masses=MOLAR_MASS, **kwargs)


def _write_epoch_1_cache(
    path: Path,
    composition: dict[str, float] | None = None,
) -> dict[str, float]:
    composition = composition or {"SiO2": 1.0, "MgO": 1.0}
    bulk_masses = {
        species: mol * MOLAR_MASS[species]
        for species, mol in composition.items()
    }
    total_mass = sum(bulk_masses.values())
    liquid_wt_pct = {
        species: mass / total_mass * 100.0
        for species, mass in bulk_masses.items()
    }
    with sqlite3.connect(path) as connection:
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
                generic_phase_assemblage_available INTEGER NOT NULL,
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
        connection.execute(
            "INSERT INTO grid_keys VALUES (?, ?, ?, ?, ?)",
            (1, 1.0, -9.0, json.dumps(composition), "equilibrium"),
        )
        connection.execute(
            "INSERT INTO alphamelts_outputs VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                7,
                1,
                1,
                "ok",
                "success",
                1,
                0.5,
                json.dumps({"liquid": total_mass * 0.5, "solid": total_mass * 0.5}),
                json.dumps(liquid_wt_pct),
                json.dumps({"SiO2": 0.8}),
                900.0,
                1299.0,
                1300.0,
            ),
        )
    return composition


def test_phase_context_selects_epoch_1_only_on_liquidus_surface(tmp_path):
    cache = tmp_path / "grind-accumulator.db"
    composition = _write_epoch_1_cache(cache)

    context = _phase_context(
        1300.0,
        1.0,
        composition,
        -9.0,
        scalar_liquid_fraction=0.2,
        grind_cache_path=cache,
    )

    assert set(context) == {"SiO2", "MgO"}
    assert context["SiO2"]["liquid_fraction"] == pytest.approx(0.5)
    assert context["MgO"]["liquid_fraction"] == pytest.approx(0.5)
    assert context["SiO2"]["activity_basis"] == (
        "existing_gamma_x_activity_basis"
    )
    provenance = context["SiO2"]["provenance"]
    assert provenance["selected_tier"] == "grind_cache_assemblage"
    assert provenance["grind_cache"]["execution_scope"] == (
        "liquidus_surface_epoch_1"
    )
    assert provenance["grind_cache"]["executed_temperature_C"] == 1300.0


def test_epoch_1_refuses_isothermal_claim_and_selects_scalar(tmp_path):
    cache = tmp_path / "grind-accumulator.db"
    composition = _write_epoch_1_cache(cache)

    context = _phase_context(
        900.0,
        1.0,
        composition,
        -9.0,
        scalar_liquid_fraction=0.25,
        grind_cache_path=cache,
        liquidus_temperature_C=1300.0,
    )

    record = context["SiO2"]
    assert record == {
        "phase": "mixed",
        "activity_basis": "existing_gamma_x_activity_basis",
        "liquid_fraction": 0.25,
        "provenance": record["provenance"],
    }
    provenance = record["provenance"]
    assert provenance["selected_tier"] == "kress_scalar_liquid_fraction"
    assert provenance["diagnostic_only"] is True
    assert provenance["behavioral_authority"] is False
    assert provenance["grind_cache"]["status"] == "refused"
    assert provenance["grind_cache"]["isothermal_status"] == (
        "empty_pending_epoch_2_regrind"
    )
    assert "isothermal_tier_empty_pending_epoch_2_regrind" in (
        provenance["grind_cache"]["reason"]
    )


def test_epoch_2_presence_does_not_unlock_epoch_1_liquidus_surface(tmp_path):
    cache = tmp_path / "grind-accumulator.db"
    composition = _write_epoch_1_cache(cache)
    with sqlite3.connect(cache) as connection:
        connection.execute(
            "UPDATE alphamelts_outputs "
            "SET generic_temperature_C = 1500.0, "
            "generic_liquidus_T_C = 900.0, alpha_liquidus_T_C = 900.0 "
            "WHERE id = 7"
        )
        connection.execute(
            "INSERT INTO grid_keys VALUES (?, ?, ?, ?, ?)",
            (2, 1.0, -9.0, json.dumps({"Other": 1.0}), "equilibrium"),
        )
        connection.execute(
            "INSERT INTO alphamelts_outputs VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                8,
                2,
                2,
                "ok",
                "success",
                1,
                0.5,
                "{}",
                "{}",
                "{}",
                1200.0,
                1300.0,
                1300.0,
            ),
        )

    context = _phase_context(
        900.0,
        1.0,
        composition,
        -9.0,
        scalar_liquid_fraction=0.25,
        grind_cache_path=cache,
        liquidus_temperature_C=1300.0,
    )

    provenance = context["SiO2"]["provenance"]
    assert provenance["selected_tier"] == "kress_scalar_liquid_fraction"
    assert provenance["grind_cache"]["status"] == "refused"
    assert "off_liquidus_request" in provenance["grind_cache"]["reason"]


def test_scalar_provenance_defaults_to_caller_supplied(tmp_path):
    context = _phase_context(
        1200.0,
        1.0,
        {"SiO2": 1.0},
        -9.0,
        scalar_liquid_fraction=0.25,
        grind_cache_path=tmp_path / "missing.db",
    )

    assert context["SiO2"]["provenance"]["scalar_source"] == (
        "caller_supplied_liquid_fraction"
    )


def test_scalar_provenance_accepts_verified_source(tmp_path):
    context = _phase_context(
        1200.0,
        1.0,
        {"SiO2": 1.0},
        -9.0,
        scalar_liquid_fraction=0.25,
        verified_scalar_source="hour_pinned_melt_redox_gate",
        grind_cache_path=tmp_path / "missing.db",
    )

    assert context["SiO2"]["provenance"]["scalar_source"] == (
        "hour_pinned_melt_redox_gate"
    )


@pytest.mark.parametrize(
    "value",
    [math.nan, -0.01, 1.01, "bad", 10**10000],
    ids=["nan", "below-zero", "above-one", "string", "overflow"],
)
def test_invalid_scalar_liquid_fraction_raises(value, tmp_path):
    with pytest.raises(
        InvalidLiquidFractionError, match="scalar_liquid_fraction"
    ):
        _phase_context(
            1200.0,
            1.0,
            {"SiO2": 1.0},
            -9.0,
            scalar_liquid_fraction=value,
            grind_cache_path=tmp_path / "missing.db",
        )


@pytest.mark.parametrize(
    ("value", "expected_phase"),
    [(0.0, "solid"), (1.0, "liquid")],
)
def test_scalar_liquid_fraction_boundaries_are_valid(
    value, expected_phase, tmp_path
):
    context = _phase_context(
        1200.0,
        1.0,
        {"SiO2": 1.0},
        -9.0,
        scalar_liquid_fraction=value,
        grind_cache_path=tmp_path / "missing.db",
    )

    assert context["SiO2"]["liquid_fraction"] == value
    assert context["SiO2"]["phase"] == expected_phase
    assert context["SiO2"]["provenance"]["selected_tier"] == (
        "kress_scalar_liquid_fraction"
    )


def test_phase_context_module_has_no_upward_simulator_imports():
    module_path = Path(__file__).parents[2] / "simulator/chemistry/phase_context.py"
    tree = ast.parse(module_path.read_text())
    simulator_imports = [
        node
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("simulator")
        )
        or (
            isinstance(node, ast.Import)
            and any(alias.name.startswith("simulator") for alias in node.names)
        )
    ]

    assert simulator_imports == []


def test_liquidus_nearest_match_is_bounded_and_provenanced(tmp_path):
    cache = tmp_path / "grind-accumulator.db"
    _write_epoch_1_cache(cache)

    context = _phase_context(
        1300.0,
        1.0,
        {"SiO2": 1.01, "MgO": 0.99},
        -9.0,
        scalar_liquid_fraction=0.2,
        grind_cache_path=cache,
    )

    provenance = context["SiO2"]["provenance"]["grind_cache"]
    assert provenance["retrieval"] == "nearest"
    assert 0.0 < provenance["composition_distance"] < 0.05
    assert provenance["output_id"] == 7


def test_phase_context_uses_labeled_unity_when_cache_and_scalar_absent(tmp_path):
    context = _phase_context(
        1200.0,
        1.0,
        {"SiO2": 2.0},
        -8.0,
        grind_cache_path=tmp_path / "missing.db",
    )

    assert context["SiO2"]["phase"] == "liquid"
    assert context["SiO2"]["liquid_fraction"] == 1.0
    assert context["SiO2"]["activity_basis"] == "unity_assumption"
    provenance = context["SiO2"]["provenance"]
    assert provenance["selected_tier"] == "labeled_unity_fallback"
    assert provenance["fallback_reason"] == (
        "no_resolved_scalar_liquid_fraction"
    )
    assert provenance["grind_cache"]["reason"] == "grind_cache_missing"
    assert not (tmp_path / "missing.db").exists()


def test_cache_tier_couples_feo_and_fe2o3_on_fe_cation_basis(tmp_path):
    cache = tmp_path / "grind-accumulator.db"
    composition = _write_epoch_1_cache(
        cache,
        composition={"FeO": 1.0, "Fe2O3": 1.0},
    )

    context = _phase_context(
        1300.0,
        1.0,
        composition,
        -9.0,
        grind_cache_path=cache,
    )

    assert context["FeO"]["liquid_fraction"] == pytest.approx(0.5)
    assert context["Fe2O3"]["liquid_fraction"] == pytest.approx(0.5)
    assert context["FeO"]["provenance"]["coupled_fe_cation_basis"] is True
    assert context["Fe2O3"]["provenance"]["coupled_fe_cation_basis"] is True


def test_cache_tier_accepts_liquid_prefixed_phase_names(tmp_path):
    cache = tmp_path / "grind-accumulator.db"
    composition = _write_epoch_1_cache(cache)
    with sqlite3.connect(cache) as connection:
        phase_masses = json.loads(
            connection.execute(
                "SELECT generic_phase_masses_kg_json FROM alphamelts_outputs"
            ).fetchone()[0]
        )
        phase_masses["liquid_0"] = phase_masses.pop("liquid")
        connection.execute(
            "UPDATE alphamelts_outputs "
            "SET generic_phase_masses_kg_json = ?",
            (json.dumps(phase_masses),),
        )

    context = _phase_context(
        1300.0,
        1.0,
        composition,
        -9.0,
        grind_cache_path=cache,
    )

    assert context["SiO2"]["liquid_fraction"] == pytest.approx(0.5)


def test_cache_tier_honors_all_liquid_source_row_1000000009(tmp_path):
    cache = tmp_path / "grind-accumulator.db"
    composition = {
        "Al2O3": 0.0,
        "CaO": 352.36324337583187,
        "CoO": 0.0,
        "Cr2O3": 3.1465398628219132,
        "Fe2O3": 86.64055638762974,
        "FeO": 239.26844457371593,
        "K2O": 0.0,
        "MgO": 245.13042872651008,
        "MnO": 1.3232551399517822,
        "Na2O": 0.0,
        "NiO": 5.373207885168243,
        "P2O5": 1.614641774492423,
        "SiO2": 657.739247334072,
        "TiO2": 0.0,
    }
    liquid_wt_pct = {
        "Al2O3": 0.0,
        "CaO": 19.49,
        "CoO": 0.0,
        "Cr2O3": 0.47,
        "Fe2O3": 13.65,
        "FeO": 16.95,
        "H2O": 0.0,
        "K2O": 0.0,
        "MgO": 9.74,
        "MnO": 0.09,
        "Na2O": 0.0,
        "NiO": 0.4,
        "P2O5": 0.23,
        "SiO2": 38.98,
        "TiO2": 0.0,
    }
    _write_epoch_1_cache(cache, composition=composition)
    with sqlite3.connect(cache) as connection:
        connection.execute(
            "UPDATE grid_keys SET id = ?, composition_mol_json = ?",
            (1000000009, json.dumps(composition)),
        )
        connection.execute(
            "UPDATE alphamelts_outputs SET "
            "id = ?, grid_key_id = ?, generic_temperature_C = ?, "
            "generic_liquidus_T_C = ?, alpha_liquidus_T_C = ?, "
            "generic_liquid_fraction = ?, generic_phase_masses_kg_json = ?, "
            "generic_liquid_composition_wt_pct_json = ?",
            (
                1000000009,
                1000000009,
                1225.0,
                1350.59,
                1350.59,
                1.0,
                json.dumps({"liquid": 0.1}),
                json.dumps(liquid_wt_pct),
            ),
        )

    context = _phase_context(
        1350.59,
        1.0,
        composition,
        -9.0,
        grind_cache_path=cache,
    )

    assert context["Cr2O3"]["liquid_fraction"] == 1.0
    assert context["FeO"]["liquid_fraction"] == 1.0
    assert context["Fe2O3"]["liquid_fraction"] == 1.0
    assert context["MgO"]["liquid_fraction"] == 1.0
    assert context["MnO"]["liquid_fraction"] == 1.0
    assert {record["phase"] for record in context.values()} == {"liquid"}
    assert context["Cr2O3"]["provenance"]["grind_cache"]["output_id"] == (
        1000000009
    )


def test_liquid_plus_trace_solid_uses_reconstruction_not_all_liquid_shortcut():
    # A positive non-liquid phase mass — even a trace crystal (5e-11 kg in
    # 0.1 kg melt) — is a physical phase, not float noise: the all-liquid
    # shortcut must NOT fire, and per-species availability must come from the
    # reconstruction path (i.e. not uniformly 1.0 with the all-liquid basis).
    from simulator.chemistry.phase_context import _cache_species_context

    row = {
        "generic_phase_masses_kg_json": (
            '{"liquid": 0.1, "olivine": 5e-11}'
        ),
        "generic_liquid_composition_wt_pct_json": (
            '{"SiO2": 49.4, "MgO": 9.9, "FeO": 19.3}'
        ),
    }
    context = _cache_species_context(
        {"SiO2": 1.0, "MgO": 0.5, "FeO": 0.3},
        row,
        {"grind_cache": {"output_id": 999}},
        {"SiO2": 0.060, "MgO": 0.040, "FeO": 0.072},
    )

    # The all-liquid shortcut would label every species phase="liquid" with
    # liquid_fraction 1.0; the reconstruction path yields sub-unity fractions
    # (phase "mixed") for at least some species.
    assert any(
        record["liquid_fraction"] < 1.0 or record["phase"] != "liquid"
        for record in context.values()
    )
