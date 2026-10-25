from __future__ import annotations

import math
from dataclasses import replace

import pytest

from simulator.battery.residue import (
    ResidueAlphaObservation,
    ResidueChannel,
    ResidueFractionIntegrationInput,
    ResidueFractionSegment,
    ResidueGeometryInput,
    ResidueIntegrationInput,
    ResidueInventoryRefusal,
    ResiduePressureRegime,
    ResidueThermalSegment,
    _channel_terms,
    _finite_step_fluxes,
    _formula_terms,
    _self_similar_area_m2,
    estimate_loo_evaporation_alpha,
    integrate_residue_fraction,
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


def _fraction_request(**changes) -> ResidueFractionIntegrationInput:
    request = ResidueFractionIntegrationInput(
        initial_inventory_mol={"FeO": 1.0e-4, "SiO2": 1.0e-4},
        channels=(ResidueChannel("FeO", "FeO", "FeO", 1.0, "measured"),),
        temperature_path=(ResidueFractionSegment(0.0, 1.0, 1800.0),),
        pressure_regime=ResiduePressureRegime(
            "buffered_fO2", 100.0, 0.0, -10.0
        ),
        pressure_model=lambda *_args: {"FeO": 1.0e-3},
        mass_loss_fraction=0.1,
        target_source_id="target",
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


def test_fraction_integrator_at_zero_returns_starting_inventory_without_flux_calls() -> None:
    calls = 0

    def pressure_model(*_args):
        nonlocal calls
        calls += 1
        return {"FeO": 1.0e-3}

    initial = {"FeO": 1.0e-4, "SiO2": 1.0e-4}
    result = integrate_residue_fraction(_fraction_request(
        initial_inventory_mol=initial,
        mass_loss_fraction=0.0,
        pressure_model=pressure_model,
    ))

    assert result.residue_mol == initial
    assert result.mass_loss_fraction == 0.0
    assert calls == 0


def test_fraction_integrator_forwards_accepted_pressure_diagnostics() -> None:
    accepted: list[float] = []

    def pressure_model(_inventory, _temperature_K, log10_pO2_bar, _regime):
        pO2_bar = 10.0**log10_pO2_bar
        return {
            "Fe": 5.0e-8 * pO2_bar**-0.5,
            "O2": 100_000.0 * pO2_bar,
        }

    pressure_model._on_accepted_evaluation = accepted.append
    result = integrate_residue_fraction(_fraction_request(
        channels=(
            ResidueChannel("Fe", "Fe", "FeO", 1.0, "pinned"),
            ResidueChannel("O2", "O2", None, 1.0, "pinned"),
        ),
        pressure_regime=ResiduePressureRegime("vacuum_balance", 100.0, 0.0),
        pressure_model=pressure_model,
        mass_loss_fraction=0.01,
    ))

    assert result.residue_mol["FeO"] < 1.0e-4
    assert accepted
    assert all(math.isfinite(value) for value in accepted)


def test_one_volatile_parent_with_refractory_remainder_matches_rayleigh_limit() -> None:
    initial = {"FeO": 1.0e-4, "SiO2": 1.0e-4}
    endpoint = 0.1
    initial_mass = math.fsum(
        amount * _formula_terms(oxide)[1] for oxide, amount in initial.items()
    )
    expected_feo = initial["FeO"] - (
        endpoint * initial_mass / _formula_terms("FeO")[1]
    )

    result = integrate_residue_fraction(_fraction_request(
        initial_inventory_mol=initial,
        mass_loss_fraction=endpoint,
    ))

    assert result.residue_mol["FeO"] == pytest.approx(expected_feo, rel=1.0e-9)
    assert result.residue_mol["SiO2"] == initial["SiO2"]
    assert result.mass_loss_fraction == endpoint


def test_equal_relative_volatility_preserves_parent_mole_ratio() -> None:
    initial = {"FeO": 1.0e-4, "MgO": 2.0e-4}
    molar_mass = {
        species: _formula_terms(species)[1] for species in ("FeO", "MgO")
    }

    def equal_relative_pressure(inventory, _temperature, _logp, _regime):
        total = math.fsum(inventory.values())
        return {
            species: inventory.get(species, 0.0) / total
            * math.sqrt(molar_mass[species])
            for species in molar_mass
        }

    channels = (
        ResidueChannel("FeO", "FeO", "FeO", 1.0, "measured"),
        ResidueChannel("MgO", "MgO", "MgO", 1.0, "measured"),
    )
    result = integrate_residue_fraction(_fraction_request(
        initial_inventory_mol=initial,
        channels=channels,
        pressure_model=equal_relative_pressure,
    ))
    uniformly_scaled = integrate_residue_fraction(_fraction_request(
        initial_inventory_mol=initial,
        channels=tuple(
            ResidueChannel(
                channel.species,
                channel.formula,
                channel.parent_oxide,
                0.25,
                "same relative volatility at lower absolute alpha",
            )
            for channel in channels
        ),
        pressure_model=equal_relative_pressure,
    ))

    assert result.residue_mol["FeO"] / result.residue_mol["MgO"] == pytest.approx(
        initial["FeO"] / initial["MgO"], rel=1.0e-9
    )
    assert uniformly_scaled.residue_mol == pytest.approx(
        result.residue_mol, rel=1.0e-9, abs=1.0e-20
    )


def test_fraction_integrator_uses_printed_mass_units_and_scales_start_composition() -> None:
    initial = {"FeO": 1.0, "SiO2": 1.0}
    composition_mass = math.fsum(
        amount * _formula_terms(oxide)[1] for oxide, amount in initial.items()
    )
    printed_start_mass = composition_mass * 2.0
    printed_loss = printed_start_mass * 0.1

    result = integrate_residue_fraction(_fraction_request(
        initial_inventory_mol=initial,
        mass_loss_fraction=None,
        mass_loss_kg=printed_loss,
        initial_mass_kg=printed_start_mass,
    ))

    assert result.mass_loss_fraction == pytest.approx(0.1)
    assert result.residue_mol["FeO"] == pytest.approx(
        2.0 - printed_loss / _formula_terms("FeO")[1], rel=1.0e-9
    )


def test_fraction_integrator_converts_printed_final_mass() -> None:
    initial = {"FeO": 1.0, "SiO2": 1.0}
    initial_mass = math.fsum(
        amount * _formula_terms(oxide)[1] for oxide, amount in initial.items()
    )

    result = integrate_residue_fraction(_fraction_request(
        initial_inventory_mol=initial,
        mass_loss_fraction=None,
        final_mass_kg=0.9 * initial_mass,
        initial_mass_kg=initial_mass,
    ))

    assert result.mass_loss_fraction == pytest.approx(0.1)
    assert result.residue_mol["FeO"] == pytest.approx(
        initial["FeO"] - 0.1 * initial_mass / _formula_terms("FeO")[1],
        rel=1.0e-9,
    )


def test_fraction_integrator_refuses_missing_and_excessive_printed_endpoints() -> None:
    with pytest.raises(ResidueInventoryRefusal) as missing:
        integrate_residue_fraction(_fraction_request(mass_loss_fraction=None))
    assert missing.value.reason == "residue_mass_loss_endpoint_missing"

    with pytest.raises(ResidueInventoryRefusal) as missing_initial_mass:
        integrate_residue_fraction(_fraction_request(
            mass_loss_fraction=None,
            mass_loss_kg=1.0e-8,
            initial_mass_kg=None,
        ))
    assert missing_initial_mass.value.reason == "residue_initial_sample_mass_missing"

    with pytest.raises(ResidueInventoryRefusal) as excessive:
        integrate_residue_fraction(_fraction_request(mass_loss_fraction=0.8))
    assert excessive.value.reason == "residue_mass_loss_exceeds_volatile_inventory"
    assert "printed mass loss" in excessive.value.detail
    assert "total volatile inventory" in excessive.value.detail
    initial_mass = math.fsum(
        amount * _formula_terms(oxide)[1]
        for oxide, amount in {"FeO": 1.0e-4, "SiO2": 1.0e-4}.items()
    )
    volatile_mass = 1.0e-4 * _formula_terms("FeO")[1]
    assert f"{0.8 * initial_mass:.17g} kg" in excessive.value.detail
    assert f"{volatile_mass:.17g} kg" in excessive.value.detail
    assert f"initial mass {initial_mass:.17g} kg" in excessive.value.detail


def test_fraction_integrator_surfaces_the_alpha_unity_upper_bound_notice() -> None:
    result = integrate_residue_fraction(_fraction_request(
        channels=(ResidueChannel("FeO", "FeO", "FeO", None, "unresolved"),),
    ))

    assert any(notice.startswith("alpha_upper_bound:FeO:") for notice in result.notices)


def test_fraction_integrator_uses_piecewise_printed_temperature_path() -> None:
    seen_temperatures = set()

    def pressure_model(_inventory, temperature, _logp, _regime):
        seen_temperatures.add(temperature)
        return {"FeO": 1.0e-3}

    result = integrate_residue_fraction(_fraction_request(
        temperature_path=(
            ResidueFractionSegment(0.0, 0.05, 1700.0),
            ResidueFractionSegment(0.05, 1.0, 1800.0),
        ),
        pressure_model=pressure_model,
    ))

    assert result.residue_mol["FeO"] < 1.0e-4
    assert seen_temperatures == {1700.0, 1800.0}


def test_fraction_path_reproduces_time_domain_composition_at_its_mass_loss_endpoint() -> None:
    initial = {"FeO": 1.0e-4, "SiO2": 1.0e-4}
    channel = ResidueChannel("FeO", "FeO", "FeO", 1.0, "measured")
    pressure = lambda *_args: {"FeO": 100.0}
    initial_mass = math.fsum(
        amount * _formula_terms(oxide)[1] for oxide, amount in initial.items()
    )
    term = _channel_terms((channel,), require_oxygen_channels=False)
    rate_kg_s = _finite_step_fluxes(
        term,
        initial,
        temperature_K=1800.0,
        area_m2=1.0,
        pressures_Pa={"FeO": 100.0},
    )["FeO"]
    duration_s = 0.1 * initial_mass / rate_kg_s

    timed = integrate_residue_schedule(_request(
        initial_inventory_mol=initial,
        channels=(channel,),
        thermal_schedule=(ResidueThermalSegment(1800.0, duration_s, 256),),
        geometry=ResidueGeometryInput(1.0, "constant_area", initial_mass),
        pressure_model=pressure,
    ))
    timed_mass = math.fsum(
        amount * _formula_terms(oxide)[1]
        for oxide, amount in timed.residue_mol.items()
    )
    printed_endpoint = (initial_mass - timed_mass) / initial_mass
    area_free = integrate_residue_fraction(_fraction_request(
        initial_inventory_mol=initial,
        channels=(channel,),
        mass_loss_fraction=printed_endpoint,
        pressure_model=pressure,
    ))

    # DOP853 uses rtol=1e-10; the time path uses 256 finite steps with the
    # same constant HKL pressure. 2e-10 covers both numerical error budgets.
    assert area_free.residue_mol == pytest.approx(
        timed.residue_mol, rel=2.0e-10, abs=1.0e-20
    )


def test_missing_target_source_refuses_when_loo_alpha_is_needed() -> None:
    with pytest.raises(ResidueInventoryRefusal) as error:
        select_residue_channel_alphas(
            (_channel(None),), (), target_source_id=None,
            melt_family=None, temperature_K=1800.0,
        )

    assert error.value.reason == "residue_alpha_target_source_missing"
