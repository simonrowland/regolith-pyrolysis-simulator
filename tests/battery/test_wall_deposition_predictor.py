"""Chunk 2 of d-275: wall-deposition prediction and typed source limits."""

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    Authority,
    Engine,
    ExecutionState,
    NoticeKind,
    Quantity,
    RefusalReason,
)
from simulator.battery.identity import WallIdentity
from simulator.battery.records import (
    Apparatus,
    ApparatusGeometry,
    Located,
    Locator,
    State,
    Value,
)
from simulator.battery.score import (
    _wall_deposition_formula_registry,
    authority_allowed,
    metric_operation,
    predict_with_engine,
)
from simulator.battery.enums import MetricOperation
from simulator.trace import wall_deposit_by_segment_species_kg
from simulator.wall_deposition import _wall_deposition_flux_budget_kg_hr
from tests.battery import factories as F


def _located(value):
    return Located(State.of(value), Locator(page=2, table="3"))


def _wall_row(
    quantity: Quantity,
    *,
    formula: str = "Na",
    wall_temperature_K: Decimal = Decimal("600"),
    local_pressure_Pa: Decimal = Decimal("17.5"),
    source_kind: str = "melt",
):
    identity = replace(
        F.wall_deposit_identity(formula=formula, wall_T=wall_temperature_K),
        quantity=quantity,
        wall=State.of(
            WallIdentity(
                State.of(wall_temperature_K),
                State.of("SiO2"),
                State.of(Decimal("0.02")),
                State.of("duct-1"),
            )
        ),
        total_pressure_Pa=State.of(Decimal("100000")),
    )
    conditions = {
        "wall_species_partial_pressure_Pa": _located(Value.point_of(local_pressure_Pa)),
        "vapour_source_kind": _located(source_kind),
        "gas_temperature_K": _located(Value.point_of(Decimal("900"))),
    }
    if source_kind == "upstream_partial_pressure":
        conditions.update(
            {
                "source_partial_pressure_Pa": _located(Decimal("20")),
                "source_location": _located("melt-outlet"),
            }
        )
    observation = replace(
        F.observation(
            f"wall-{quantity.value}",
            "exp-wall",
            identity,
            Decimal("1"),
        ),
        point_conditions=conditions,
    )
    geometry = ApparatusGeometry(
        pipe_inner_diameter_m=_located(Decimal("0.1")),
        segment_length_m=_located(Decimal("0.2")),
        segment_position_m=_located(Decimal("0.3")),
        flow_orientation=_located("upstream_to_downstream"),
        line_of_sight_view_factor=_located(Decimal("0.5")),
    )
    experiment = replace(
        F.tabulation_experiment("exp-wall"),
        apparatus=Apparatus(geometry=geometry),
    )
    return observation, experiment


def test_wall_flux_converts_owner_molar_flux_with_registry_mass_and_local_inputs(
    monkeypatch,
):
    import simulator.condensation as condensation

    observation, experiment = _wall_row(
        Quantity.WALL_DEPOSITION_FLUX,
        wall_temperature_K=Decimal("1000"),
    )
    calls = []

    def fixed_molar_flux(species, P_local_pa, T_surface_K, alpha_s, **kwargs):
        calls.append((species, P_local_pa, T_surface_K, alpha_s, kwargs))
        return 1.0e-4

    monkeypatch.setattr(
        condensation,
        "_series_resistance_deposition_flux_mol_m2_s",
        fixed_molar_flux,
    )
    monkeypatch.setattr(condensation, "_knudsen_number", lambda *args, **kwargs: 0.001)

    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, observation, experiment=experiment)

    # Independent CIAAW/NIST sodium mass: 22.98976928 g/mol = 0.02298976928 kg/mol.
    # Premise: the owner returns 1e-4 mol/m²/s. Algebra: J_mass = J_molar × M.
    # Units: (mol/m²/s)(kg/mol) = kg/m²/s. Sanity: 1e-4 × 0.02298976928 = 2.298976928e-6.
    assert prediction.execution.state is ExecutionState.PRODUCED
    assert float(prediction.value) == pytest.approx(2.298976928e-6, rel=1e-15)
    assert prediction.unit == "kg_per_m2_per_s"
    assert calls[0][0:3] == ("Na", 17.5, 1000.0)
    assert prediction.authority is Authority.CERTIFIED
    assert authority_allowed(Quantity.WALL_DEPOSITION_FLUX, prediction.authority)
    assert any("local_regime=viscous" in notice.reason for notice in prediction.notices)
    assert any("alpha_s_source=" in notice.reason for notice in prediction.notices)


@pytest.mark.parametrize(
    ("knudsen_number", "expected_flag"),
    [
        (0.1, "transitional_wall_smooth_interpolation"),
        (100.0, "free_molecular_line_of_sight_area_proxy"),
    ],
)
def test_wall_flux_exposes_regime_approximations_and_range_provenance(
    monkeypatch,
    knudsen_number,
    expected_flag,
):
    import simulator.condensation as condensation

    observation, experiment = _wall_row(
        Quantity.WALL_DEPOSITION_FLUX,
        wall_temperature_K=Decimal("1000"),
    )
    monkeypatch.setattr(
        condensation,
        "_series_resistance_deposition_flux_mol_m2_s",
        lambda *args, **kwargs: 1.0e-6,
    )
    monkeypatch.setattr(
        condensation,
        "_knudsen_number",
        lambda *args, **kwargs: knudsen_number,
    )

    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, observation, experiment=experiment)

    reasons = tuple(notice.reason for notice in prediction.notices)
    expected_regime = "transitional" if knudsen_number == 0.1 else "free_molecular"
    assert any(f"local_regime={expected_regime}" in reason for reason in reasons)
    assert any(expected_flag in reason for reason in reasons)
    assert any("poiseuille_only_pipe_capacity" in reason for reason in reasons)
    assert any("alpha_s_source=" in reason for reason in reasons)


@pytest.mark.parametrize("source_kind", ["melt", "upstream_partial_pressure"])
def test_source_derived_flux_refuses_until_process_context_exists(source_kind):
    observation, experiment = _wall_row(
        Quantity.WALL_DEPOSITION_FLUX,
        source_kind=source_kind,
    )
    conditions = dict(observation.point_conditions)
    conditions.pop("wall_species_partial_pressure_Pa")
    observation = replace(observation, point_conditions=conditions)

    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, observation, experiment=experiment)

    assert prediction.execution.state is ExecutionState.UNSUPPORTED
    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.refusal_detail["reason"] == "requires_process_run_context"


def test_simulated_areal_mass_refuses_without_ledger_or_reported_constant_rate():
    observation, experiment = _wall_row(Quantity.WALL_DEPOSIT_AREAL_MASS)

    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, observation, experiment=experiment)

    assert prediction.execution.state is ExecutionState.UNSUPPORTED
    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.refusal_detail["reason"] == "requires_process_run_context"


def test_segment_ledger_mass_is_divided_by_its_wall_area():
    class Ledger:
        def kg_by_account(self):
            return ("process.wall_deposit_segment_duct-1",)

        def project_account_kg(self, account):
            assert account == "process.wall_deposit_segment_duct-1"
            return {"Na": 0.3}

    ledger = Ledger()
    assert wall_deposit_by_segment_species_kg(ledger)[("duct-1", "Na")] == pytest.approx(0.3)

    from simulator.trace import _wall_deposit_areal_mass_kg_m2

    # Premise: the committed segment ledger contains 0.3 kg over 0.02 m².
    # Algebra: m_A = m_segment / A_wall. Units: kg/m². Sanity: 0.3 / 0.02 = 15.
    assert _wall_deposit_areal_mass_kg_m2(
        ledger,
        segment="duct-1",
        species="Na",
        wall_area_m2=0.02,
    ) == pytest.approx(15.0)


def test_beerkens_rate_checks_production_molar_to_mass_flux_conversion():
    # Published Beerkens rate: 0.38 mg cm⁻² h⁻¹.
    # Algebra: 0.38 × 0.01 kg/m² ÷ 3600 s/h = 1.055555...e-6 kg/m²/s.
    # Independent Na₂SO₄ mass is 142.04253856 g/mol = 0.14204253856 kg/mol, so divide by M to
    # give the molar flux consumed by the production budget converter below.
    target_kg_m2_s = Decimal("0.38") * Decimal("0.01") / Decimal("3600")
    independent_molar_mass_kg_mol = Decimal("0.14204253856")
    molar_flux = float(target_kg_m2_s / independent_molar_mass_kg_mol)
    kg_hr_for_one_square_metre = _wall_deposition_flux_budget_kg_hr(
        species="Na2SO4",
        flux_mol_m2_s=molar_flux,
        surface_area_m2=1.0,
        formula_registry=_wall_deposition_formula_registry(),
    )
    # The production owner reports kg/h for A=1 m²; divide by 3600 s/h to recover kg/m²/s.
    # The published 0.38 rate has two significant figures; allow its rounding
    # plus the independent registry-vs-reference molar-mass precision.
    assert kg_hr_for_one_square_metre / 3600.0 == pytest.approx(
        float(target_kg_m2_s),
        rel=1e-4,
    )


def test_pure_species_dew_inverse_recovers_wall_saturation_pressure():
    import simulator.condensation as condensation

    temperature_K = 1000.0
    partial_pressure_Pa = condensation._antoine_psat_pa("Na", temperature_K)
    observation, _ = _wall_row(
        Quantity.WALL_DEW_TEMPERATURE,
        local_pressure_Pa=Decimal(str(partial_pressure_Pa)),
    )

    prediction = predict_with_engine(Engine.MAGEMIN, observation)

    assert prediction.execution.state is ExecutionState.PRODUCED
    assert 924.0 <= float(prediction.value) <= 1118.0
    assert prediction.authority is Authority.CERTIFIED
    assert authority_allowed(Quantity.WALL_DEW_TEMPERATURE, prediction.authority)
    assert not any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        for notice in prediction.notices
    )
    assert prediction.value == pytest.approx(Decimal(str(temperature_K)), abs=1e-8)
    predicted_pressure = condensation._antoine_psat_pa("Na", float(prediction.value))
    assert predicted_pressure == pytest.approx(partial_pressure_Pa, rel=1e-10)


def test_high_pressure_dew_bracket_continues_until_target_is_bracketed():
    import simulator.condensation as condensation

    target_pressure = 10.0 * condensation._antoine_psat_pa("Na", 1600.0)
    observation, _ = _wall_row(
        Quantity.WALL_DEW_TEMPERATURE,
        local_pressure_Pa=Decimal(str(target_pressure)),
    )

    prediction = predict_with_engine(Engine.MAGEMIN, observation)

    assert prediction.execution.state is ExecutionState.PRODUCED
    dew_temperature = float(prediction.value)
    assert dew_temperature > 1600.0
    assert condensation._antoine_psat_pa("Na", dew_temperature) == pytest.approx(
        target_pressure,
        rel=1e-10,
    )
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        for notice in prediction.notices
    )


def test_dew_prediction_flags_root_outside_pure_component_wall_band():
    import simulator.condensation as condensation

    target_pressure = condensation._antoine_psat_pa("Na", 1200.0)
    diagnostic = condensation.antoine_dew_temperature_diagnostic(
        "Na",
        target_pressure,
    )
    observation, _ = _wall_row(
        Quantity.WALL_DEW_TEMPERATURE,
        local_pressure_Pa=Decimal(str(target_pressure)),
    )

    prediction = predict_with_engine(Engine.MAGEMIN, observation)

    assert diagnostic["valid_range_K"] == [924.0, 1118.0]
    assert diagnostic["status"] == "extrapolated"
    assert diagnostic["temperature_K"] == pytest.approx(1200.0, abs=1e-8)
    assert prediction.execution.state is ExecutionState.PRODUCED
    assert prediction.authority is Authority.EXTRAPOLATED
    assert authority_allowed(Quantity.WALL_DEW_TEMPERATURE, prediction.authority)
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        for notice in prediction.notices
    )


def test_wall_flux_copies_extrapolated_psat_authority_from_wall_owner():
    observation, experiment = _wall_row(Quantity.WALL_DEPOSITION_FLUX)

    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
    )

    assert prediction.execution.state is ExecutionState.PRODUCED
    assert prediction.authority is Authority.EXTRAPOLATED
    assert authority_allowed(Quantity.WALL_DEPOSITION_FLUX, prediction.authority)
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        for notice in prediction.notices
    )


@pytest.mark.parametrize("pressure_scale", [0.5, 2.0])
def test_out_of_band_dew_is_extrapolated_and_flagged(pressure_scale):
    import simulator.condensation as condensation

    in_band_pressure = condensation._antoine_psat_pa("Na", 800.0)
    valid_range = condensation.antoine_dew_temperature_diagnostic(
        "Na",
        in_band_pressure,
    )["valid_range_K"]
    low_K = float(valid_range[0])
    high_K = float(valid_range[1])
    edge_temperature_K = high_K if pressure_scale > 1.0 else low_K
    edge_pressure = condensation._antoine_psat_pa("Na", edge_temperature_K)
    observation, _ = _wall_row(
        Quantity.WALL_DEW_TEMPERATURE,
        local_pressure_Pa=Decimal(str(edge_pressure * pressure_scale)),
    )

    prediction = predict_with_engine(Engine.MAGEMIN, observation)

    assert prediction.execution.state is ExecutionState.PRODUCED
    if pressure_scale > 1.0:
        assert float(prediction.value) > high_K
    else:
        assert float(prediction.value) < float(valid_range[0])
    assert any(notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND for notice in prediction.notices)


@pytest.mark.parametrize("engine", [Engine.ALPHAMELTS, Engine.MAGEMIN])
def test_species_without_wall_psat_refuses_with_typed_reason(engine):
    observation, _ = _wall_row(
        Quantity.WALL_DEW_TEMPERATURE,
        formula="Na2SO4",
    )

    prediction = predict_with_engine(engine, observation)

    assert prediction.execution.state is ExecutionState.UNSUPPORTED
    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.refusal_detail["reason"] == "no_certified_wall_p_sat"


@pytest.mark.parametrize("engine", [Engine.ALPHAMELTS, Engine.MAGEMIN])
def test_reactive_product_flux_refuses_without_certified_wall_psat(engine):
    observation, experiment = _wall_row(
        Quantity.WALL_DEPOSITION_FLUX,
        formula="Na2SO4",
    )

    prediction = predict_with_engine(engine, observation, experiment=experiment)

    assert prediction.execution.state is ExecutionState.UNSUPPORTED
    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.refusal_detail["reason"] == "no_certified_wall_p_sat"


def test_source_derived_dew_refuses_without_local_pressure_profile():
    observation, _ = _wall_row(
        Quantity.WALL_DEW_TEMPERATURE,
        source_kind="upstream_partial_pressure",
    )
    conditions = dict(observation.point_conditions)
    conditions.pop("wall_species_partial_pressure_Pa")
    observation = replace(observation, point_conditions=conditions)

    prediction = predict_with_engine(Engine.MAGEMIN, observation)

    assert prediction.execution.state is ExecutionState.UNSUPPORTED
    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.refusal_detail["reason"] == "requires_process_run_context"


def test_wall_observables_have_scoring_metrics():
    assert metric_operation(Quantity.WALL_DEPOSITION_FLUX) is MetricOperation.RELATIVE
    assert metric_operation(Quantity.WALL_DEPOSIT_AREAL_MASS) is MetricOperation.RELATIVE
    assert metric_operation(Quantity.WALL_DEW_TEMPERATURE) is MetricOperation.ABSOLUTE
