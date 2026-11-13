from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import simulator.optimize.objective as objective_module
from simulator import thermal_train
from simulator.cost_energy import carrier_gas_run_cost
from simulator.cost_parameters import cost_parameters_from_mapping
from simulator.optimize.knob_saturation import compute_knob_saturation
from simulator.optimize.objective import (
    ObjectiveDefinition,
    _carrier_gas_cost_summary,
    _carrier_refrigeration_kWh_per_kg,
    pareto_front,
)
from simulator.optimize.recipe import RecipePatch, RecipeSchema

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load_yaml(name: str):
    return yaml.safe_load((DATA_DIR / name).read_text(encoding="utf-8"))


def test_carrier_cost_matches_hand_computed_hero_literal() -> None:
    result = carrier_gas_run_cost(
        carrier_mass_used_kg=44.0,
        carrier_species="Kr",
        refrigeration_recycle_energy_kWh=43.56,
        carrier_gas_earth_price_per_kg={"Kr": 290.0},
        import_delivery_cost_per_kg=10000.0,
        makeup_loss_fraction_per_run=0.01,
        electricity_cost_per_kWh=0.15,
    )

    assert result["makeup_mass_kg"] == pytest.approx(0.44)
    assert result["recirculated_mass_kg"] == pytest.approx(43.56)
    assert result["makeup_cost_usd"] == pytest.approx(4527.60)
    assert result["recycle_electricity_cost_usd"] == pytest.approx(6.534)
    assert result["carrier_cost_usd"] == pytest.approx(4534.134)
    assert result["earth_price_source_tag"].startswith(
        "FLAGGED dated estimate 2026-11-11"
    )


@pytest.mark.parametrize("species", ["N2", "Ar"])
def test_imported_carrier_makeup_includes_delivery_when_earth_price_is_pending(
    species: str,
) -> None:
    prices = cost_parameters_from_mapping().carrier_gas_earth_price_per_kg
    result = carrier_gas_run_cost(
        carrier_mass_used_kg=44.0,
        carrier_species=species,
        carrier_body="moon",
        refrigeration_recycle_energy_kWh=0.0,
        carrier_gas_earth_price_per_kg=prices,
        import_delivery_cost_per_kg=10000.0,
        makeup_loss_fraction_per_run=0.01,
        electricity_cost_per_kWh=0.15,
    )

    assert result["earth_price_per_kg"] == pytest.approx(0.0)
    assert result["makeup_cost_usd"] == pytest.approx(4400.0)
    assert result["earth_price_pending"] is True
    assert "earth_price_pending_immaterial_vs_delivery" in result["evidence_flags"]
    assert result["makeup_loss_source_tag"] == (
        "FLAGGED: controller assumption pending owner number (d-389)"
    )


def test_carrier_pressure_cost_moves_small_optimizer_fixture_off_high_bound() -> None:
    costs = cost_parameters_from_mapping()
    profile = {"run": {"mass_kg": 1.0, "hours": 150}}
    definitions = (
        ObjectiveDefinition("oxygen_kg", "maximize", "kg", ordinal=0),
        ObjectiveDefinition(
            "throughput_cost_owner_ratify_usd",
            "minimize",
            "USD",
            ordinal=1,
        ),
    )

    def candidate(identifier: str, pressure_mbar: float) -> dict:
        sim = SimpleNamespace(
            melt=SimpleNamespace(
                background_gas_species="Kr",
                initial_mass_kg=1.0,
                p_total_mbar=pressure_mbar,
            ),
            record=SimpleNamespace(total_hours=150),
        )
        carrier_cost = _carrier_gas_cost_summary(
            sim,
            None,
            profile=profile,
            cost_parameters=costs,
        )
        return {
            "id": identifier,
            "pressure_mbar": pressure_mbar,
            "objectives": {
                "oxygen_kg": 1.0,
                "throughput_cost_owner_ratify_usd": carrier_cost["carrier_cost_usd"],
            },
        }

    low = candidate("low-carrier-pressure", 5.0)
    high = candidate("high-carrier-pressure", 15.0)
    front = pareto_front(
        (high, low),
        definitions,
        objective_getter=lambda item: item["objectives"],
    )

    assert [item["id"] for item in front] == ["low-carrier-pressure"]
    assert low["objectives"]["throughput_cost_owner_ratify_usd"] < high[
        "objectives"
    ]["throughput_cost_owner_ratify_usd"]

    pressure_path = ("campaigns", "C2A_continuous", "p_total_mbar")
    schema = RecipeSchema()
    high_trace = compute_knob_saturation(
        RecipePatch({pressure_path: 15.0}).validated(schema),
        schema,
        active_objective_metrics=(
            "oxygen_kg",
            "throughput_cost_owner_ratify_usd",
        ),
    )
    row = next(
        knob
        for knob in high_trace["knobs"]
        if knob["key"] == "campaigns.C2A_continuous.p_total_mbar"
    )
    assert row["pinned"] == "high"
    assert row["has_opposing_cost"] is True
    assert row["opposing_cost_metrics"] == ["throughput_cost_owner_ratify_usd"]

    winner_trace = compute_knob_saturation(
        RecipePatch({pressure_path: low["pressure_mbar"]}).validated(schema),
        schema,
        active_objective_metrics=tuple(definition.metric for definition in definitions),
    )
    winner_row = next(
        knob
        for knob in winner_trace["knobs"]
        if knob["key"] == "campaigns.C2A_continuous.p_total_mbar"
    )
    assert winner_row["pinned"] == "low"
    assert winner_row["has_opposing_cost"] is True


@pytest.mark.parametrize("species", ["Ar", "N2", "CO2"])
def test_mars_atmospheric_makeup_is_not_mispriced_as_an_earth_import(
    species: str,
) -> None:
    result = carrier_gas_run_cost(
        carrier_mass_used_kg=44.0,
        carrier_species=species,
        carrier_body="Mars",
        refrigeration_recycle_energy_kWh=43.56,
        carrier_gas_earth_price_per_kg={"Ar": 0.0, "Kr": 290.0, "N2": 0.0},
        import_delivery_cost_per_kg=10000.0,
        makeup_loss_fraction_per_run=0.01,
        electricity_cost_per_kWh=0.15,
    )

    assert result["makeup_cost_usd"] == 0.0
    assert result["recycle_electricity_cost_usd"] == pytest.approx(6.534)
    assert result["typed_todo"] == {
        "code": "carrier_local_source_price_pending",
        "species": species,
        "body": "Mars",
        "detail": (
            f"TODO: model local {species} sourcing on Mars; "
            "no Earth import price is charged."
        ),
    }


def test_ar_mars_todo_uses_explicit_recipe_body_from_simulator_state() -> None:
    sim = SimpleNamespace(
        melt=SimpleNamespace(
            background_gas_species="Ar",
            body="Mars",
            initial_mass_kg=1.0,
            p_total_mbar=10.0,
        ),
        record=SimpleNamespace(total_hours=150.0),
    )

    summary = _carrier_gas_cost_summary(
        sim,
        None,
        profile={"run": {"mass_kg": 1.0, "hours": 150}},
        cost_parameters=cost_parameters_from_mapping(),
    )

    assert summary["carrier_mass_used_kg"] == pytest.approx(44.0)
    assert summary["earth_price_per_kg"] is None
    assert summary["carrier_cost_usd"] == pytest.approx(
        summary["recycle_electricity_cost_usd"]
    )
    assert summary["typed_todo"]["species"] == "Ar"
    assert summary["typed_todo"]["body"] == "Mars"


def test_co2_mars_carrier_cost_reaches_local_source_branch() -> None:
    sim = SimpleNamespace(
        melt=SimpleNamespace(
            background_gas_species="CO2",
            body="Mars",
            initial_mass_kg=1.0,
            p_total_mbar=10.0,
        ),
        record=SimpleNamespace(total_hours=150.0),
    )

    summary = _carrier_gas_cost_summary(
        sim,
        None,
        profile={"run": {"mass_kg": 1.0, "hours": 150}},
        cost_parameters=cost_parameters_from_mapping(),
    )

    assert summary["refrigeration_recycle_energy_kWh"] > 0.0
    assert summary["makeup_cost_usd"] == 0.0
    assert summary["typed_todo"]["code"] == "carrier_local_source_price_pending"
    assert summary["typed_todo"]["species"] == "CO2"
    assert summary["typed_todo"]["body"] == "Mars"
    assert "carrier_co2_cp_held_at_300K_below_shomate_range" in summary[
        "evidence_flags"
    ]


@pytest.mark.parametrize("species", ["He", "Xe"])
def test_carrier_without_condensation_basis_keeps_typed_unavailable_energy_and_makeup(
    species: str,
) -> None:
    sim = SimpleNamespace(
        melt=SimpleNamespace(
            background_gas_species=species,
            body="Moon",
            initial_mass_kg=1.0,
            p_total_mbar=10.0,
        ),
        record=SimpleNamespace(total_hours=150.0),
    )

    summary = _carrier_gas_cost_summary(
        sim,
        None,
        profile={"run": {"mass_kg": 1.0, "hours": 150}},
        cost_parameters=cost_parameters_from_mapping(),
    )

    assert summary["status"] == "unavailable"
    assert summary["pricing_status"] == "unavailable"
    assert summary["typed_todo"]["code"] == "carrier_recycle_basis_unavailable"
    assert summary["refrigeration_recycle_energy_kWh"]["status"] == "unavailable"
    assert summary["refrigeration_recycle_energy_kWh"]["value"] is None
    assert summary["recycle_electricity_cost_usd"]["status"] == "unavailable"
    assert summary["earth_price_pending"] is True
    assert summary["earth_price_source_tag"].startswith(
        "FLAGGED: Earth carrier-gas price pending"
    )
    assert "carrier_earth_price_pending" in summary["evidence_flags"]
    assert "carrier_recycle_energy_unavailable" in summary["evidence_flags"]
    assert summary["makeup_cost_usd"] == pytest.approx(4400.0)
    assert summary["carrier_cost_usd"] == pytest.approx(summary["makeup_cost_usd"])


@pytest.mark.parametrize(
    ("species", "expected_makeup_cost_usd"),
    [("Ar", 4400.0), ("Kr", 4527.60), ("N2", 4400.0)],
)
def test_unknown_carrier_body_assumes_import_and_flags_evidence(
    species: str,
    expected_makeup_cost_usd: float,
) -> None:
    costs = cost_parameters_from_mapping()
    result = carrier_gas_run_cost(
        carrier_mass_used_kg=44.0,
        carrier_species=species,
        carrier_body="",
        refrigeration_recycle_energy_kWh=0.0,
        carrier_gas_earth_price_per_kg=costs.carrier_gas_earth_price_per_kg,
        import_delivery_cost_per_kg=costs.import_delivery_cost_per_kg,
        makeup_loss_fraction_per_run=costs.carrier_makeup_loss_fraction_per_run,
        electricity_cost_per_kWh=costs.electricity_cost_per_kWh,
    )

    assert result["makeup_cost_usd"] == pytest.approx(expected_makeup_cost_usd)
    assert "carrier_body_unknown_assumed_import" in result["evidence_flags"]


@pytest.mark.parametrize(
    ("species", "expected_kWh_per_kg", "relative_tolerance"),
    [("Kr", 0.2134, 1e-3), ("N2", 1.15, 0.03), ("Ar", 0.61, 0.03)],
)
def test_carrier_refrigeration_uses_species_condensation_properties(
    species: str,
    expected_kWh_per_kg: float,
    relative_tolerance: float,
) -> None:
    # Premise: 1 kg Kr cools from 300 K to 119.73 K, condenses, and is lifted
    # to the 300 K reject temperature at eta=0.30. Algebra: q_cold =
    # [(2.5*8.314462618 J/mol/K)*(300-119.73 K)+9080 J/mol]/0.083798 kg/mol
    # = 153071.916 J/kg; W = q_cold*(300/119.73-1)/0.30 = 768236.152 J/kg;
    # W/(3.6e6 J/kWh) = 0.213398931 kWh/kg. Units reduce to J/kg then kWh/kg;
    # at T_reject == 119.73 K the lift work tends to zero.
    assert _carrier_refrigeration_kWh_per_kg(species) == pytest.approx(
        expected_kWh_per_kg,
        rel=relative_tolerance,
    )


def test_o2_carrier_row_reuses_thermal_train_phase_constants_by_identity() -> None:
    oxygen_row = objective_module._CARRIER_CONDENSATION_PROPERTIES["O2"]

    assert oxygen_row[0] is thermal_train.OXYGEN_NORMAL_BOILING_POINT_K
    assert oxygen_row[1] is thermal_train.OXYGEN_VAPORIZATION_ENTHALPY_J_PER_MOL
    assert _carrier_refrigeration_kWh_per_kg("O2") > 0.0


def test_hero_default_n2_carrier_cost_is_priced_and_flagged() -> None:
    profile = _load_yaml("optimize_profiles/hero/lunar_mare_low_ti.yaml")
    run = profile["run"]
    sim = SimpleNamespace(
        melt=SimpleNamespace(
            background_gas_species="N2",
            body="",
            initial_mass_kg=run["mass_kg"],
            hour=run["hours"],
            p_total_mbar=10.0,
        ),
        record=SimpleNamespace(total_hours=run["hours"]),
    )

    summary = _carrier_gas_cost_summary(
        sim,
        None,
        profile=profile,
        cost_parameters=cost_parameters_from_mapping(),
    )

    assert summary["carrier_mass_used_kg"] == pytest.approx(66.0)
    assert summary["makeup_cost_usd"] == pytest.approx(6600.0)
    assert summary["carrier_cost_usd"] == pytest.approx(6611.27, abs=0.01)
    assert summary["earth_price_pending"] is True
    assert summary["carrier_mass_basis"] == "scaled_reference_estimate"
    assert summary["carrier_mass_scale"]["total_scale"] == pytest.approx(1.5)
    assert summary["earth_price_source_tag"] == (
        "earth_price_pending_immaterial_vs_delivery"
    )
    assert "earth_price_pending_immaterial_vs_delivery" in summary["evidence_flags"]
    assert "carrier_body_unknown_assumed_import" in summary["evidence_flags"]
