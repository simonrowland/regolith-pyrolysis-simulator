from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.cost_energy import carrier_gas_run_cost
from simulator.cost_parameters import cost_parameters_from_mapping
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
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


def _sim_from_real_feedstock(feedstock_id: str) -> PyrolysisSimulator:
    simulator = PyrolysisSimulator(
        InternalAnalyticalBackend(),
        _load_yaml("setpoints.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    additives = {"C": 0.03} if feedstock_id == "mars_basalt" else {}
    simulator.load_batch(feedstock_id, mass_kg=1.0, additives_kg=additives)
    return simulator


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


@pytest.mark.parametrize("feedstock_id", ["mars_global_mgs1", "mars_basalt"])
def test_ar_mars_todo_uses_body_from_real_feedstock(feedstock_id: str) -> None:
    sim = _sim_from_real_feedstock(feedstock_id)
    sim.melt.background_gas_species = "Ar"
    sim.melt.hour = 150.0
    sim.melt.p_total_mbar = 10.0
    sim.record.total_hours = 150.0
    assert sim.melt.body == "mars"

    summary = _carrier_gas_cost_summary(
        sim,
        None,
        profile={"run": {"mass_kg": 1.0, "hours": 150}},
        cost_parameters=cost_parameters_from_mapping(),
    )

    assert summary["carrier_mass_used_kg"] == pytest.approx(44.0)
    assert summary["carrier_body"] == "mars"
    assert summary["earth_price_per_kg"] is None
    assert summary["carrier_cost_usd"] == pytest.approx(
        summary["recycle_electricity_cost_usd"]
    )
    assert summary["typed_todo"]["species"] == "Ar"
    assert summary["typed_todo"]["body"] == "mars"
    assert summary["carrier_mass_basis"] == "scaled_reference_estimate"
    assert "FLAGGED: controller assumption pending owner number (d-389)" in summary[
        "evidence_flags"
    ]


@pytest.mark.parametrize(
    ("species", "expected_kWh_per_kg"),
    [("Kr", 0.21), ("N2", 1.15), ("Ar", 0.61)],
)
def test_carrier_refrigeration_uses_species_condensation_properties(
    species: str,
    expected_kWh_per_kg: float,
) -> None:
    assert _carrier_refrigeration_kWh_per_kg(species) == pytest.approx(
        expected_kWh_per_kg,
        rel=0.03,
    )


def test_hero_default_n2_carrier_cost_is_priced_and_flagged() -> None:
    profile = _load_yaml("optimize_profiles/hero/lunar_mare_low_ti.yaml")
    run = profile["run"]
    sim = SimpleNamespace(
        melt=SimpleNamespace(
            background_gas_species="N2",
            body="moon",
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
