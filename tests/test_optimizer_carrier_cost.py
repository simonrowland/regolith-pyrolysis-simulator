from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.cost_energy import carrier_gas_run_cost
from simulator.cost_parameters import cost_parameters_from_mapping
from simulator.optimize.knob_saturation import compute_knob_saturation
from simulator.optimize.objective import (
    ObjectiveDefinition,
    _carrier_gas_cost_summary,
    pareto_front,
)
from simulator.optimize.recipe import RecipePatch, RecipeSchema


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


def test_ar_makeup_is_not_mispriced_as_an_earth_import() -> None:
    result = carrier_gas_run_cost(
        carrier_mass_used_kg=44.0,
        carrier_species="Ar",
        carrier_body="Mars",
        refrigeration_recycle_energy_kWh=43.56,
        carrier_gas_earth_price_per_kg={"Kr": 290.0},
        import_delivery_cost_per_kg=10000.0,
        makeup_loss_fraction_per_run=0.01,
        electricity_cost_per_kWh=0.15,
    )

    assert result["makeup_cost_usd"] == 0.0
    assert result["recycle_electricity_cost_usd"] == pytest.approx(6.534)
    assert result["typed_todo"] == {
        "code": "carrier_local_source_price_pending",
        "species": "Ar",
        "body": "Mars",
        "detail": "TODO: model local Ar sourcing on Mars; no Earth import price is charged.",
    }


def test_ar_mars_todo_uses_recipe_body_from_simulator_state() -> None:
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
    assert summary["carrier_cost_usd"] == pytest.approx(
        summary["recycle_electricity_cost_usd"]
    )
    assert summary["typed_todo"]["species"] == "Ar"
    assert summary["typed_todo"]["body"] == "Mars"
