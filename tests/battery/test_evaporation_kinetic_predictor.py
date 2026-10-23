from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from engines.builtin.evaporation_flux import _series_resistance_evaporation_flux_kg_m2_s
from simulator.accounting.formulas import parse_formula
from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    MethodToken,
    NoticeKind,
    PerBasis,
    Phase,
    Quantity,
    RefusalReason,
)
from simulator.battery.identity import Exposure, Identity
from simulator.battery.records import (
    Apparatus,
    ApparatusGeometry,
    Composition,
    Execution,
    Located,
    Species,
    State,
)
from simulator.battery.residue import (
    EvaporationAlphaObservation,
    EvaporationAlphaEstimate,
    ResidueInventoryRefusal,
    collect_measured_evaporation_alphas,
    estimate_loo_evaporation_alpha,
)
from simulator.battery.score import (
    EnginePrediction,
    ScoreContext,
    _predict_evaporation_rate,
    compile_residual,
)
from tests.battery import factories as F


T_K = 2000.0
MELT = State.of(Composition(
    basis="ordered_complete_mole_inventory",
    components=(("MgO", Decimal("0.3")), ("SiO2", Decimal("0.7"))),
    amount_basis=AmountBasis.MOLE_FRACTION,
))


def _alpha(source: str, species: str, value: float, *, family: str = "cai"):
    return EvaporationAlphaObservation(
        source_id=source,
        species=species,
        alpha=value,
        evidence_class=EvidenceClass.MEASURED_DIRECT.value,
        observation_id=f"{source}::{species}",
        temperature_K=T_K,
        melt_family=family,
    )


def _kinetic_row(species: str, *, per: PerBasis | None = PerBasis.AREA_TIME):
    identity = Identity(
        quantity=Quantity.EVAPORATION_RATE,
        species=Species(species, Phase.G),
        temperature_K=State.of(Decimal(str(T_K))),
        per=(State.unknown("source does not state a rate basis") if per is None else State.of(per)),
        reservoir=State.unknown("not needed by the HKL pressure request"),
        composition=MELT,
        fO2_Pa=State.of(Decimal("1e-8")),
        total_pressure_Pa=State.of(Decimal("1e-5")),
        sweep_gas=State.unknown("not stated"),
        exposure=State.of(Exposure(
            area_m2=State.of(Decimal("0.02")),
            duration_s=State.of(Decimal("1")),
        )),
        sample_mass_kg=State.of(Decimal("0.01")),
    )
    return F.observation(
        f"rate-{species}", "kinetic-experiment", identity, Decimal("1e-8"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="target-paper",
    )


def _pressure_prediction(engine, observation, **_kwargs):
    assert observation.identity.quantity.value is Quantity.P_PARTIAL
    return EnginePrediction(
        engine=engine,
        channel="internal-analytical",
        execution=Execution(state=ExecutionState.PRODUCED, call_evidence="test P_partial"),
        value=Decimal("100"),
        unit="Pa",
        authority=Authority.CERTIFIED,
        coefficient_sources=("test-pressure-source",),
        lineage_complete=True,
        identity=observation.identity,
    )


def test_loo_alpha_excludes_row_source_and_bands_one_value_per_paper():
    records = (
        _alpha("target-paper", "Mg", 0.9),
        _alpha("other-paper-b", "Mg", 0.1),
        _alpha("other-paper-c", "Mg", 0.3),
        _alpha("other-paper-c", "Mg", 0.5),
    )
    estimate = estimate_loo_evaporation_alpha(
        records,
        species="Mg",
        melt_family="cai",
        temperature_K=T_K,
        exclude_source_id="target-paper",
    )

    assert isinstance(estimate, EvaporationAlphaEstimate)
    assert estimate.value == pytest.approx(0.25)
    assert estimate.band == pytest.approx((0.1, 0.4))
    assert estimate.contributing_source_ids == ("other-paper-b", "other-paper-c")
    assert estimate.melt_family_fallback is False


def test_loo_alpha_refuses_fewer_than_two_other_measured_sources():
    result = estimate_loo_evaporation_alpha(
        (_alpha("target-paper", "Mg", 0.9), _alpha("one-other-paper", "Mg", 0.2)),
        species="Mg",
        melt_family="cai",
        temperature_K=T_K,
        exclude_source_id="target-paper",
    )

    assert isinstance(result, ResidueInventoryRefusal)
    assert result.reason == "alpha_loo_insufficient_sources"


def test_loo_alpha_uses_measured_scalar_without_printed_temperature():
    records = (
        replace(_alpha("scalar-paper", "Mg", 0.2), temperature_K=None),
        _alpha("point-paper", "Mg", 0.4),
    )

    estimate = estimate_loo_evaporation_alpha(
        records,
        species="Mg",
        melt_family="cai",
        temperature_K=T_K,
        exclude_source_id="target-paper",
    )

    assert isinstance(estimate, EvaporationAlphaEstimate)
    assert estimate.value == pytest.approx(0.3)
    assert estimate.contributing_source_ids == ("point-paper", "scalar-paper")


def test_score_compiler_routes_non_alkali_rate_without_headline_rail():
    row = _kinetic_row("Fe")
    experiment = replace(
        F.tabulation_experiment(
            "kinetic-experiment", method=MethodToken.LANGMUIR_FREE_EVAPORATION
        ),
        apparatus=Apparatus(
            geometry=ApparatusGeometry(
                exposed_area_m2=Located(State.of(Decimal("0.02")))
            )
        ),
    )
    context = ScoreContext(
        works={},
        experiments={experiment.experiment_id: experiment},
        observations={row.observation_id: row},
        _evaporation_alpha_observations=(_alpha("one-other-paper", "Fe", 0.2),),
    )

    residual, _candidate = compile_residual(
        row, Engine.INTERNAL_ANALYTICAL, context=context
    )

    assert residual.rail is None
    assert residual.refusal is not None
    assert residual.refusal.detail["reason"] == "alpha_loo_insufficient_sources"


def test_alpha_collection_rejects_unmeasured_context_forms():
    source_document = {
        "source_id": "model-paper",
        "species": {
            "Mg": {
                "context": [{
                    "observation_id": "model-fit",
                    "type": "best_fit_model_context",
                    "T_range_K": [1800, 2200],
                    "values": {
                        "gas_species": "Mg(g)",
                        "method_class": "model_derived",
                        "evidence_class": "measured_direct",
                        "alpha_form": {"type": "arrhenius", "gamma_o": 1.0,
                                       "E_J_per_mol": 1000.0},
                    },
                }, {
                    "observation_id": "condensation-fit",
                    "type": "measured_alpha_context",
                    "values": {
                        "gas_species": "Mg(g)",
                        "method_class": "measured_direct",
                        "alpha_kind": "condensation_growth_not_evaporation",
                        "alpha_form": {"type": "arrhenius", "gamma_o": 1.0,
                                       "E_J_per_mol": 1000.0},
                    },
                }]
            }
        },
    }

    assert collect_measured_evaporation_alphas((), (source_document,)) == ()


def test_alpha_collection_accepts_measured_store_observation():
    identity = Identity(
        quantity=Quantity.EVAPORATION_COEFFICIENT_ALPHA,
        species=Species("Mg", Phase.G),
        temperature_K=State.of(Decimal(str(T_K))),
    )
    observation = F.observation(
        "printed-alpha",
        "alpha-experiment",
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="alpha-paper",
    )

    collected = collect_measured_evaporation_alphas((observation,))

    assert len(collected) == 1
    assert collected[0].source_id == "alpha-paper"
    assert collected[0].species == "Mg"
    assert collected[0].alpha == pytest.approx(0.2)
    assert collected[0].temperature_K == T_K


def test_measured_context_arrhenius_is_evaluated_at_row_temperature():
    form = {
        "type": "arrhenius",
        "gamma_o": 0.5,
        "E_J_per_mol": 1000.0,
        "R_J_per_mol_K": 8.314,
    }
    source_document = {
        "source_id": "arrhenius-paper",
        "species": {
            "Mg": {
                "context": [{
                    "observation_id": "measured-alpha-fit",
                    "T_range_K": [1900.0, 2100.0],
                    "values": {
                        "gas_species": "Mg(g)",
                        "evidence_class": "measured_direct",
                        "alpha_form": form,
                    },
                }]
            }
        },
    }
    context_rows = collect_measured_evaporation_alphas((), (source_document,))
    other_point = _alpha("point-paper", "Mg", 0.3)
    estimate = estimate_loo_evaporation_alpha(
        (*context_rows, other_point),
        species="Mg",
        melt_family=None,
        temperature_K=T_K,
        exclude_source_id="target-paper",
    )
    from simulator.alpha_kinetics import _evaluate_extracted_arrhenius_alpha

    expected = _evaluate_extracted_arrhenius_alpha(form, T_K)[0]

    assert isinstance(estimate, EvaporationAlphaEstimate)
    assert tuple(source for source, _value in estimate.source_values) == (
        "arrhenius-paper", "point-paper"
    )
    assert tuple(value for _source, value in estimate.source_values) == pytest.approx(
        (expected, 0.3)
    )
    assert estimate.value == pytest.approx((expected + 0.3) / 2.0)


@pytest.mark.parametrize("formula", ["Na", "K"])
def test_alkali_rate_rows_are_typed_refusals(formula):
    result = _predict_evaporation_rate(
        Engine.INTERNAL_ANALYTICAL,
        _kinetic_row(formula),
        experiment=F.tabulation_experiment(
            "kinetic-experiment", method=MethodToken.LANGMUIR_FREE_EVAPORATION
        ),
        handles=None,
        isolated=None,
        bench=None,
        alpha_observations=(),
        partial_pressure_observations=(),
        pressure_predictor=_pressure_prediction,
    )

    assert result.refusal_reason is RefusalReason.UNSUPPORTED
    assert result.refusal_detail["reason"] == "alkali_alpha_not_measured"


@pytest.mark.parametrize("formula", ["Mg", "SiO", "Fe", "Ca", "Al"])
def test_evaporation_rate_uses_engine_pressure_and_hkl_flux(formula):
    row = _kinetic_row(formula)
    estimate_rows = (_alpha("alpha-paper-b", formula, 0.1),
                     _alpha("alpha-paper-c", formula, 0.3))
    experiment = F.tabulation_experiment(
        "kinetic-experiment", method=MethodToken.LANGMUIR_FREE_EVAPORATION
    )

    prediction = _predict_evaporation_rate(
        Engine.INTERNAL_ANALYTICAL,
        row,
        experiment=experiment,
        handles=None,
        isolated=None,
        bench=None,
        alpha_observations=estimate_rows,
        partial_pressure_observations=(),
        pressure_predictor=_pressure_prediction,
    )

    molar_mass = parse_formula(formula, species=formula).molar_mass_kg_per_mol()
    production_hkl = _series_resistance_evaporation_flux_kg_m2_s(
        formula,
        100.0,
        0.0,
        T_K,
        molar_mass,
        0.2,
        knudsen_number=float("inf"),
        gas_resistance_enabled=False,
        melt_resistance_enabled=False,
    ).flux_kg_s_m2
    assert float(prediction.value) == pytest.approx(production_hkl)
    assert prediction.unit == "kg_per_m2_s"
    assert prediction.identity is row.identity
    assert prediction.coefficient_basis == "leave_one_paper_out_hkl"
    assert any(notice.kind is NoticeKind.INPUT_OMITTED for notice in prediction.notices)


def test_non_vacuum_evaporation_requires_printed_ambient_pressure():
    row = _kinetic_row("Mg")
    prediction = _predict_evaporation_rate(
        Engine.INTERNAL_ANALYTICAL,
        row,
        experiment=F.tabulation_experiment(
            "kinetic-experiment", method=MethodToken.TABULATION
        ),
        handles=None,
        isolated=None,
        bench=None,
        alpha_observations=(_alpha("alpha-paper-b", "Mg", 0.1),
                            _alpha("alpha-paper-c", "Mg", 0.3)),
        partial_pressure_observations=(),
        pressure_predictor=_pressure_prediction,
    )

    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == "ambient_partial_pressure_not_stated"


def test_printed_ambient_partial_pressure_is_subtracted():
    row = _kinetic_row("Mg")
    ambient_identity = replace(row.identity, quantity=Quantity.P_PARTIAL)
    ambient = F.observation(
        "ambient-partial",
        "kinetic-experiment",
        ambient_identity,
        Decimal("30"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="target-paper",
    )
    prediction = _predict_evaporation_rate(
        Engine.INTERNAL_ANALYTICAL,
        row,
        experiment=F.tabulation_experiment(
            "kinetic-experiment", method=MethodToken.TABULATION
        ),
        handles=None,
        isolated=None,
        bench=None,
        alpha_observations=(_alpha("alpha-paper-b", "Mg", 0.1),
                            _alpha("alpha-paper-c", "Mg", 0.3)),
        partial_pressure_observations=(ambient,),
        pressure_predictor=_pressure_prediction,
    )
    molar_mass = parse_formula("Mg", species="Mg").molar_mass_kg_per_mol()
    expected = _series_resistance_evaporation_flux_kg_m2_s(
        "Mg", 100.0, 30.0, T_K, molar_mass, 0.2,
        knudsen_number=float("inf"),
        gas_resistance_enabled=False,
        melt_resistance_enabled=False,
    ).flux_kg_s_m2

    assert float(prediction.value) == pytest.approx(expected)
    assert not any(notice.kind is NoticeKind.INPUT_OMITTED for notice in prediction.notices)


def test_total_evaporation_rate_multiplies_hkl_flux_by_printed_area():
    row = _kinetic_row("Mg", per=None)
    experiment = F.tabulation_experiment(
        "kinetic-experiment", method=MethodToken.LANGMUIR_FREE_EVAPORATION
    )
    prediction = _predict_evaporation_rate(
        Engine.INTERNAL_ANALYTICAL,
        row,
        experiment=experiment,
        handles=None,
        isolated=None,
        bench=None,
        alpha_observations=(_alpha("alpha-paper-b", "Mg", 0.1),
                            _alpha("alpha-paper-c", "Mg", 0.3)),
        partial_pressure_observations=(),
        pressure_predictor=_pressure_prediction,
    )
    areal_flux = _series_resistance_evaporation_flux_kg_m2_s(
        "Mg", 100.0, 0.0, T_K,
        parse_formula("Mg", species="Mg").molar_mass_kg_per_mol(),
        0.2,
        knudsen_number=float("inf"),
        gas_resistance_enabled=False,
        melt_resistance_enabled=False,
    ).flux_kg_s_m2

    assert float(prediction.value) == pytest.approx(areal_flux * 0.02)
    assert prediction.unit == "kg_per_s"


def test_explicit_non_mass_rate_per_basis_is_refused():
    prediction = _predict_evaporation_rate(
        Engine.INTERNAL_ANALYTICAL,
        _kinetic_row("Mg", per=PerBasis.MOL_SPECIES),
        experiment=F.tabulation_experiment(
            "kinetic-experiment", method=MethodToken.LANGMUIR_FREE_EVAPORATION
        ),
        handles=None,
        isolated=None,
        bench=None,
        alpha_observations=(_alpha("alpha-paper-b", "Mg", 0.1),
                            _alpha("alpha-paper-c", "Mg", 0.3)),
        partial_pressure_observations=(),
        pressure_predictor=_pressure_prediction,
    )

    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.refusal_detail["reason"] == "evaporation_rate_per_basis_not_supported"


def test_species_only_alpha_fallback_is_flagged():
    estimate = estimate_loo_evaporation_alpha(
        (
            replace(_alpha("source-b", "Mg", 0.1), melt_family=None),
            replace(_alpha("source-c", "Mg", 0.3), melt_family=None),
        ),
        species="Mg",
        melt_family=None,
        temperature_K=T_K,
        exclude_source_id="target-paper",
    )

    assert isinstance(estimate, EvaporationAlphaEstimate)
    assert estimate.melt_family_fallback is True
