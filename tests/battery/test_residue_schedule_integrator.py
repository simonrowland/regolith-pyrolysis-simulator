from __future__ import annotations

import math
from dataclasses import replace

import pytest

from simulator.battery.residue import (
    ResidueAlphaObservation,
    ResidueChannel,
    ResidueGeometryInput,
    ResidueIntegrationInput,
    ResidueInventoryRefusal,
    ResiduePressureRegime,
    ResidueThermalSegment,
    _channel_terms,
    _finite_step_fluxes,
    _self_similar_area_m2,
    estimate_loo_evaporation_alpha,
    integrate_residue_schedule,
    select_residue_channel_alphas,
)


def _channel(alpha: float | None = 1.0) -> ResidueChannel:
    return ResidueChannel("Fe", "Fe", "FeO", alpha, "measured")


def _pressure_model(
    _inventory: dict[str, float],
    _temperature_K: float,
    _log10_pO2_bar: float,
    _regime: ResiduePressureRegime,
) -> dict[str, float]:
    return {"Fe": 1.0e-3}


def _request(**changes) -> ResidueIntegrationInput:
    request = ResidueIntegrationInput(
        initial_inventory_mol={"FeO": 1.0e-4, "SiO2": 1.0e-4},
        channels=(_channel(),),
        thermal_schedule=(ResidueThermalSegment(1800.0, 100.0),),
        pressure_regime=ResiduePressureRegime(
            "buffered_fO2", 100.0, 0.0, -10.0
        ),
        geometry=ResidueGeometryInput(1.0e-3, "constant_area"),
        pressure_model=_pressure_model,
    )
    return replace(request, **changes)


def test_zero_duration_returns_the_starting_inventory_without_pressure_calls() -> None:
    calls = 0

    def pressure_model(*_args):
        nonlocal calls
        calls += 1
        return {"Fe": 1.0e-3}

    initial = {"FeO": 1.0e-4, "SiO2": 1.0e-4}
    result = integrate_residue_schedule(_request(
        initial_inventory_mol=initial,
        thermal_schedule=(ResidueThermalSegment(1800.0, 0.0),),
        pressure_model=pressure_model,
    ))

    assert result.residue_mol == initial
    assert result.evaporated_mol == {}
    assert result.atom_closure_mol == {"Fe": 0.0, "O": 0.0, "Si": 0.0}
    assert calls == 0


def test_zero_duration_segment_inside_positive_schedule_is_skipped() -> None:
    baseline_pressure_calls: list[float] = []
    mixed_pressure_calls: list[float] = []

    def pressure_model(calls: list[float]):
        def evaluate(_inventory, _temperature_K, logp, _regime):
            calls.append(logp)
            return {"Fe": 1.0e-3}

        return evaluate

    before = ResidueThermalSegment(1800.0, 40.0)
    after = ResidueThermalSegment(1800.0, 60.0)
    baseline = integrate_residue_schedule(_request(
        thermal_schedule=(before, after),
        pressure_model=pressure_model(baseline_pressure_calls),
    ))
    mixed = integrate_residue_schedule(_request(
        thermal_schedule=(
            before,
            ResidueThermalSegment(1800.0, 0.0),
            after,
        ),
        pressure_model=pressure_model(mixed_pressure_calls),
    ))

    assert mixed.residue_mol == baseline.residue_mol
    assert mixed_pressure_calls == baseline_pressure_calls


def test_buffered_oxygen_exchange_closes_with_exhaustion_accounting() -> None:
    result = integrate_residue_schedule(
        _request(exhaustion_floor_mol=1.0e-15)
    )

    assert result.refusal is None
    assert result.buffer_oxygen_exchange_mol != 0.0
    assert result.component_exhaustion_notices == ()
    assert result.atom_closure_mol["O"] == pytest.approx(0.0, abs=1.0e-18)


def test_zero_alpha_has_no_loss() -> None:
    initial = {"FeO": 1.0e-4, "SiO2": 1.0e-4}
    result = integrate_residue_schedule(_request(
        initial_inventory_mol=initial,
        channels=(_channel(0.0),),
    ))

    assert result.residue_mol == initial
    assert result.evaporated_mol == {}


def test_single_volatile_isothermal_step_matches_the_analytic_solution() -> None:
    initial_feo_mol = 1.0e-4
    area_m2 = 1.0e-3
    temperature_K = 1800.0
    duration_s = 1000.0
    pressure_Pa = 1.0e-3
    channel = _channel(0.8)
    inventory = {"FeO": initial_feo_mol, "SiO2": 1.0e-4}
    term = _channel_terms((channel,), require_oxygen_channels=False)[0]

    # The independent constant-pressure HKL resolver supplies the rate; the
    # closed-form first-order inventory solution supplies the time integration.
    mass_rate_kg_s = _finite_step_fluxes(
        (term,),
        inventory,
        temperature_K=temperature_K,
        area_m2=area_m2,
        pressures_Pa={channel.species: pressure_Pa},
    )[channel.species]
    parent_draw_mol_s = (
        mass_rate_kg_s / term.molar_mass_kg_mol * term.parent_moles_per_product
    )
    expected_feo_mol = initial_feo_mol * math.exp(
        -parent_draw_mol_s * duration_s / initial_feo_mol
    )

    result = integrate_residue_schedule(_request(
        initial_inventory_mol=inventory,
        channels=(channel,),
        thermal_schedule=(
            ResidueThermalSegment(temperature_K, duration_s),
        ),
        geometry=ResidueGeometryInput(area_m2, "constant_area"),
        pressure_model=lambda *_args: {channel.species: pressure_Pa},
    ))

    assert result.residue_mol["FeO"] == pytest.approx(
        expected_feo_mol, rel=2.0e-15, abs=0.0
    )


def test_self_similar_area_includes_density_ratio() -> None:
    initial_area = 3.0e-5
    # Halving mass and density preserves volume, so a sphere's physical area
    # must stay fixed without copying the production area equation here.
    actual = _self_similar_area_m2(
        initial_area,
        remaining_mass_kg=0.5,
        initial_mass_kg=1.0,
        initial_density_kg_m3=3_000.0,
        remaining_density_kg_m3=1_500.0,
    )

    assert actual == initial_area


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"initial_inventory_mol": None}, "residue_inventory_missing"),
        ({"channels": None}, "residue_channels_missing"),
        ({"thermal_schedule": None}, "residue_temperature_schedule_missing"),
        (
            {"thermal_schedule": (ResidueThermalSegment(None, 1.0),)},
            "residue_temperature_missing",
        ),
        (
            {"thermal_schedule": (ResidueThermalSegment(1800.0, None),)},
            "residue_duration_missing",
        ),
        ({"geometry": None}, "melt_surface_area_evolution_missing"),
        (
            {"geometry": ResidueGeometryInput(None, "constant_area")},
            "melt_surface_area_evolution_missing",
        ),
        ({"pressure_regime": None}, "residue_pressure_regime_missing"),
        (
            {
                "pressure_regime": ResiduePressureRegime(
                    "buffered_fO2", None, 0.0, -10.0
                )
            },
            "residue_total_pressure_missing",
        ),
        ({"pressure_model": None}, "residue_pressure_model_missing"),
    ],
)
def test_missing_required_inputs_refuse_with_typed_reasons(changes, reason) -> None:
    with pytest.raises(ResidueInventoryRefusal) as error:
        integrate_residue_schedule(_request(**changes))

    assert error.value.reason == reason


def test_loo_alpha_excludes_target_and_collapses_rows_by_source() -> None:
    pool = (
        ResidueAlphaObservation(
            "target", "Fe", 0.99, "measured_tabulated", "target-alpha"
        ),
        ResidueAlphaObservation(
            "source-a", "Fe", 0.2, "measured_tabulated", "a-alpha-1"
        ),
        ResidueAlphaObservation(
            "source-a", "Fe", 0.4, "measured_tabulated", "a-alpha-2"
        ),
        ResidueAlphaObservation(
            "source-b", "Fe", 0.8, "measured_direct", "b-alpha"
        ),
    )

    estimate = estimate_loo_evaporation_alpha(
        pool,
        species="Fe",
        melt_family="basalt",
        temperature_K=1800.0,
        exclude_source_id="target",
    )

    assert estimate.value == pytest.approx(0.55)
    assert tuple(source for source, _value in estimate.source_values) == (
        "source-a", "source-b"
    )
    assert tuple(value for _source, value in estimate.source_values) == pytest.approx(
        (0.3, 0.8)
    )
    assert estimate.contributing_source_ids == ("source-a", "source-b")
    assert estimate.band == pytest.approx((0.3, 0.8))
    assert estimate.melt_family_fallback is True


def test_alpha_selection_uses_loo_pool_or_flagged_unity_upper_bound() -> None:
    pool = (
        ResidueAlphaObservation(
            "source-a", "Fe", 0.2, "measured_tabulated", "a-alpha"
        ),
        ResidueAlphaObservation(
            "source-b", "Fe", 0.8, "measured_direct", "b-alpha"
        ),
    )
    loo_channels, loo_notices = select_residue_channel_alphas(
        (_channel(None),),
        pool,
        target_source_id="target",
        melt_family=None,
        temperature_K=1800.0,
    )
    upper_channels, upper_notices = select_residue_channel_alphas(
        (_channel(None),),
        pool[:1],
        target_source_id="target",
        melt_family=None,
        temperature_K=1800.0,
    )

    assert loo_channels[0].alpha == 0.5
    assert loo_channels[0].alpha_source == "LOO measured-source median: source-a,source-b"
    assert loo_notices == ("alpha_loo_species_only_fallback:Fe",)
    assert upper_channels[0].alpha == 1.0
    assert "upper bound" in upper_channels[0].alpha_source
    assert upper_notices[0].startswith("alpha_upper_bound:Fe:")


def test_missing_target_source_refuses_when_loo_alpha_is_needed() -> None:
    with pytest.raises(ResidueInventoryRefusal) as error:
        select_residue_channel_alphas(
            (_channel(None),), (), target_source_id=None,
            melt_family=None, temperature_K=1800.0,
        )

    assert error.value.reason == "residue_alpha_target_source_missing"
