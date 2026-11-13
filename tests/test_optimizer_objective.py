from __future__ import annotations

import inspect
import math
from dataclasses import replace
from itertools import permutations
from types import MethodType, SimpleNamespace
from typing import Mapping

import pytest

from simulator.account_ids import SPENT_REDUCTANT_RESIDUE_ACCOUNT
from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa
from simulator.optimize import study
import simulator.optimize.objective as objective_module
from scripts import sso_r_validation_map as validation_map
from simulator.cost_parameters import CostParameters
from simulator.core import PyrolysisSimulator
from simulator.evaporation import (
    EvaporationMixin,
    attach_evaluated_transport_pO2,
    attach_wall_supply_interval_physical_zero,
    evaporation_flux_for_campaign_hour,
    note_wall_supply_interval_physical_zero,
    project_publisher_vapour_rates,
    publish_wall_supply_on_flux,
    remember_evaluated_transport_pO2,
)
from simulator.optimize.strategy import bayesian as bayesian_strategy
from simulator.optimize.strategy import staged as staged_strategy
from simulator.melt_backend.base import EquilibriumResult
from simulator.vapour_rail.batch import (
    FluxDiagnosticUpperBound,
    FluxEligible,
    FluxRefusal,
    PressureValue,
    VapourAnswer,
    VapourBatch,
)
import simulator.optimize.evaluate as evaluate_module
from simulator.optimize.evaluate import (
    INVENTORY_CAPPED,
    MISSING_PARENT_OXIDE_INVENTORY,
    MISSING_PARENT_OXIDE_STOICHIOMETRY,
    SUPPLY_INTERVAL_UPPER_BOUND_USED,
    SUPPLY_UNAVAILABLE,
    RunReference,
    ScoredResult,
    _WallSupplyInterval,
    _classify_wall_supply_interval,
    _optimizer_refused_flux_bounds,
    _run_reference,
)
from simulator.optimize.objective import (
    CAPTURED_PRODUCT_BOOKKEEPING_SPECIES_PATTERNS,
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    FURNACE_AMORTIZATION_STATUS_UNGROUNDED,
    LEGACY_ENERGY_KWH_METRIC,
    ObjectiveComputationError,
    ObjectiveDefinition,
    ObjectiveProfileError,
    PRODUCT_CLASS_MASS_METRIC_PATHS,
    composition_targets_require_coating,
    composition_target_eval_metadata,
    cost_adjusted_objective_scores,
    compute_objectives,
    dominates,
    furnace_amortization_cost_per_run,
    objective_definitions,
    objective_metric_aliases,
    objective_scores,
    objective_importance_evidence,
    pareto_front,
    product_summary,
    target_spec_digest,
    _metric_value,
)
from simulator.optimize.sso2_evidence import (
    SSO2_CERTIFIED_PN2_MBAR,
    SSO2_CERTIFIED_PO2_MBAR,
    SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR,
    SSO2_CHUNK3B_READER_HANDOFF,
    SSO2_OWNER_RECIPE_ID,
    build_sso2_owner_recipe_execution,
    sso2_owner_recipe_evidence,
    sso2_owner_recipe_objective_reader,
    sso2_owner_recipe_setpoints_patch,
)
from scripts.sso2_owner_recipe_report import _markdown_report
from simulator.pumping_cost import MARS_DATUM_AMBIENT_PA, estimate_subambient_pump_cost


DEFINITIONS = (
    ObjectiveDefinition("oxygen_kg", "maximize", "kg", ordinal=0),
    ObjectiveDefinition(ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC, "minimize", "kWh", ordinal=1),
)


def _available_furnace_summary(penalty: float) -> dict[str, float | str]:
    return {
        "furnace_amortization_status": "available",
        "furnace_amortization_batch_cost_equivalents": penalty,
    }


def _furnace_test_cost_parameters() -> CostParameters:
    return CostParameters(
        electricity_cost_per_kWh=1.0,
        furnace_resinter_cost_usd=1.0,
        depreciation_expense_per_run=1.0,
        generic_reagent_cost_per_kg=1.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 1.0,
            "K": 1.0,
            "Mg": 1.0,
            "Ca": 1.0,
        },
        furnace_lifetime_cost_multiplier=500.0,
        min_fouling_penalty=1.0,
    )


def test_dominates_requires_strict_improvement() -> None:
    assert dominates(
        {"oxygen_kg": 2.0, "energy_kWh": 4.0},
        {"oxygen_kg": 1.0, "energy_kWh": 5.0},
        DEFINITIONS,
    )


def test_equal_objective_vectors_do_not_dominate() -> None:
    left = {"oxygen_kg": 1.0, "energy_kWh": 5.0}
    right = {"oxygen_kg": 1.0, "energy_kWh": 5.0}

    assert not dominates(left, right, DEFINITIONS)
    assert not dominates(right, left, DEFINITIONS)


def test_dominates_honors_mixed_minimize_maximize_directions() -> None:
    lower_energy = {"oxygen_kg": 1.0, "energy_kWh": 4.0}
    higher_energy = {"oxygen_kg": 1.0, "energy_kWh": 5.0}

    assert dominates(lower_energy, higher_energy, DEFINITIONS)
    assert not dominates(higher_energy, lower_energy, DEFINITIONS)


def test_nullable_objective_scores_preserve_metric_positions() -> None:
    definitions = (
        ObjectiveDefinition("primary", "maximize", "kg", ordinal=0),
        ObjectiveDefinition("middle", "minimize", "kWh", ordinal=1),
        ObjectiveDefinition("last", "maximize", "kg", ordinal=2),
    )

    assert objective_scores(
        {"primary": None, "middle": 4.0, "last": 9.0},
        definitions,
    ) == (None, -4.0, 9.0)
    assert objective_scores(
        {"primary": 2.0, "middle": None, "last": 9.0},
        definitions,
    ) == (2.0, None, 9.0)
    assert objective_scores(
        {"primary": 2.0, "middle": 4.0, "last": None},
        definitions,
    ) == (2.0, -4.0, None)


def test_dominates_skips_unavailable_objectives_by_metric_not_position() -> None:
    definitions = (
        ObjectiveDefinition("primary", "maximize", "kg", ordinal=0),
        ObjectiveDefinition("middle", "minimize", "kWh", ordinal=1),
        ObjectiveDefinition("last", "maximize", "kg", ordinal=2),
    )

    assert dominates(
        {"primary": None, "middle": 4.0, "last": 10.0},
        {"primary": 100.0, "middle": 5.0, "last": 10.0},
        definitions,
    )
    assert dominates(
        {"primary": 10.0, "middle": None, "last": 10.0},
        {"primary": 9.0, "middle": 1.0, "last": 10.0},
        definitions,
    )
    assert dominates(
        {"primary": 10.0, "middle": 4.0, "last": None},
        {"primary": 10.0, "middle": 5.0, "last": 100.0},
        definitions,
    )


def test_target_spec_digest_excludes_derived_thermal_window_disposition() -> None:
    target = {
        "pool": "residual_rump_at_stop",
        "species_vector": {"Ca": "retain"},
        "composition_window": {
            "pool": "residual_rump_at_stop",
            "basis": "oxide_wt_pct",
            "mode": "hard_window",
            "oxides": {"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
        },
        "maturity": {"mode": "campaign_hours", "campaign": "C2B", "hours": 24},
    }
    with_display_disposition = {
        **target,
        "thermal_window": "campaign-without-explicit-temperature-window",
    }

    assert target_spec_digest(with_display_disposition) == target_spec_digest(target)
    assert target_spec_digest(
        {"targets": ({"id": "a", "target": with_display_disposition},)}
    ) == target_spec_digest({"targets": ({"id": "a", "target": target},)})


def test_missing_or_nonfinite_objectives_raise() -> None:
    with pytest.raises(
        ObjectiveComputationError,
        match=f"{ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC}.*missing",
    ):
        dominates({"oxygen_kg": 1.0}, {"oxygen_kg": 1.0, "energy_kWh": 5.0}, DEFINITIONS)

    with pytest.raises(ObjectiveComputationError, match="oxygen_kg is non-finite"):
        dominates(
            {"oxygen_kg": math.nan, "energy_kWh": 5.0},
            {"oxygen_kg": 1.0, "energy_kWh": 5.0},
            DEFINITIONS,
        )


def test_legacy_energy_objective_alias_scores_under_canonical_definition() -> None:
    profile = {
        "objectives": [
            {
                "metric": LEGACY_ENERGY_KWH_METRIC,
                "sense": "minimize",
                "units": "kWh",
            }
        ]
    }

    definitions = objective_definitions(profile)

    assert definitions[0].metric == ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC
    assert objective_scores({LEGACY_ENERGY_KWH_METRIC: 4.0}, definitions) == (-4.0,)
    assert objective_scores(
        {ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC: 4.0},
        (ObjectiveDefinition(LEGACY_ENERGY_KWH_METRIC, "minimize", "kWh"),),
    ) == (-4.0,)


@pytest.mark.parametrize(
    ("metric", "expected_units"),
    (("Fe_kg", "kg"), ("electrical_energy_kWh", "kWh")),
)
def test_objective_definitions_reject_wrong_units_for_dynamic_metrics(
    metric: str,
    expected_units: str,
) -> None:
    profile = {
        "objectives": [
            {"metric": metric, "sense": "maximize", "units": "USD"}
        ]
    }

    with pytest.raises(ObjectiveProfileError, match=expected_units):
        objective_definitions(profile)


def test_energy_alias_set_is_derived_from_canonical_alias_mapping() -> None:
    expected = frozenset(
        {
            ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
            *(
                alias
                for alias, canonical in objective_module._OBJECTIVE_METRIC_ALIASES.items()
                if canonical == ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC
            ),
        }
    )
    assert objective_module.ENERGY_ELECTRICAL_PLUS_EVAPORATION_ALIASES == expected
    assert set(objective_module.objective_metric_aliases(LEGACY_ENERGY_KWH_METRIC)) == expected


@pytest.mark.parametrize(
    ("metric", "class_name", "expected"),
    [
        ("industrial_mixed_glass_kg", "industrial_mixed_glass", 12.5),
        ("refractory_ceramic_rump_kg", "refractory_ceramic_rump", 34.75),
    ],
)
def test_product_class_mass_metrics_read_exact_class_total_path(
    metric: str,
    class_name: str,
    expected: float,
) -> None:
    product_classes = {
        class_name: {
            "class_total_kg": expected,
            "mixed_melt_residual_kg": 999.0,
            "rump_total_kg": 888.0,
        }
    }

    assert _metric_value(
        metric,
        SimpleNamespace(),
        {metric.removesuffix("_kg"): 777.0},
        product_classes,
    ) == pytest.approx(expected)
    assert objective_metric_aliases(metric) == (metric,)


@pytest.mark.parametrize(
    ("metric", "class_name"),
    [
        ("industrial_mixed_glass_kg", "industrial_mixed_glass"),
        ("refractory_ceramic_rump_kg", "refractory_ceramic_rump"),
    ],
)
@pytest.mark.parametrize(
    "class_payload",
    [
        None,
        {},
        {"class_total_kg": "not-a-number"},
        {"class_total_kg": False},
        {"class_total_kg": -1.0},
        {"class_total_kg": math.inf},
    ],
)
def test_product_class_mass_metrics_fail_closed_on_missing_or_malformed_class_data(
    metric: str,
    class_name: str,
    class_payload: object,
) -> None:
    with pytest.raises(
        ObjectiveComputationError,
        match="objective source missing|not numeric|negative|non-finite",
    ):
        _metric_value(
            metric,
            SimpleNamespace(),
            {},
            {class_name: class_payload},
        )


def test_product_class_mass_evidence_serializes_metric_and_source_path(
    monkeypatch,
) -> None:
    product_classes = {
        "industrial_mixed_glass": {"class_total_kg": 12.5},
        "refractory_ceramic_rump": {"class_total_kg": 34.75},
    }
    monkeypatch.setattr(
        objective_module,
        "classify_products",
        lambda sim, *, early_tap_mode=False: product_classes,
    )
    profile = {
        "objectives": [
            {
                "metric": metric,
                "sense": "maximize",
                "units": "kg",
                "weight": 1.0,
                "rationale": "product class mass",
            }
            for metric in (
                "industrial_mixed_glass_kg",
                "refractory_ceramic_rump_kg",
            )
        ]
    }

    objectives = compute_objectives(
        profile,
        SimpleNamespace(product_ledger=lambda: {}),
    )

    from simulator.optimize.results_store import _serialize_objectives

    serialized = {
        row["metric"]: row
        for row in _serialize_objectives(objectives)
    }
    for metric, path in PRODUCT_CLASS_MASS_METRIC_PATHS.items():
        assert serialized[metric]["evidence"] == {
            "metric": metric,
            "source": "classify_products",
            "source_class_path": ".".join(path),
        }


def test_pure_silica_objective_uses_route_classified_product_not_raw_capture() -> None:
    no_switch = {
        "pure_silica_glass": {
            "stage_3_capture_kg": 4.5,
            "class_total_kg": 0.0,
        }
    }
    flagged_switched_product = {
        "pure_silica_glass": {
            "stage_3_capture_kg": 4.5,
            "class_total_kg": 4.5,
            "flag": {
                "status": "flagged prediction",
                "authority": "extrapolated",
                "band": [1400.0, 2200.0],
                "reason": "outside certified SiO source band",
            },
        }
    }

    assert _metric_value(
        "pure_silica_glass_kg", SimpleNamespace(), {}, no_switch
    ) == pytest.approx(0.0)
    assert _metric_value(
        "pure_silica_glass_kg", SimpleNamespace(), {}, flagged_switched_product
    ) == pytest.approx(4.5)


def test_partial_silica_objective_evidence_keeps_exclusions_and_lower_bound() -> None:
    from simulator.optimize.objective import _silica_objective_evidence

    excluded = [{
        "species": "Si",
        "account": "process.cleaned_melt",
        "reason": "Si: EvaporationFluxConfigurationError: missing LJ row",
        "upper_bound_kg": "unbounded",
    }]
    availability_flag = {
        "status": "partial",
        "authority": "unavailable",
        "reason": excluded[0]["reason"],
        "excluded": excluded,
        "total_basis": "lower_bound",
    }
    evidence = _silica_objective_evidence(
        {
            "pure_silica_glass": {
                "stage_3_capture_kg": 4.5,
                "class_total_kg": 4.5,
                "status": "partial",
                "availability_flag": availability_flag,
                "excluded": excluded,
            }
        },
        4.5,
    )

    assert evidence["value_status"] == "partial"
    assert evidence["total_basis"] == "lower_bound"
    assert evidence["availability_flag"] == availability_flag
    assert evidence["excluded"] == excluded


def test_partial_rump_objective_evidence_keeps_exclusions_and_lower_bound() -> None:
    from simulator.optimize.objective import (
        _product_class_objective_evidence,
    )

    excluded = [{
        "species": "Si",
        "account": "process.cleaned_melt",
        "reason": "missing LJ row",
        "upper_bound_kg": "unbounded",
    }]
    flag = {"status": "partial", "reason": "missing LJ row"}
    evidence = _product_class_objective_evidence(
        "refractory_ceramic_rump_kg",
        {
            "refractory_ceramic_rump": {
                "status": "partial",
                "availability_flag": flag,
                "excluded": excluded,
            }
        },
    )

    assert evidence["value_status"] == "partial"
    assert evidence["availability_flag"] == flag
    assert evidence["excluded"] == excluded
    assert evidence["total_basis"] == "lower_bound"


def test_partial_metal_objective_evidence_keeps_exclusions_and_lower_bound() -> None:
    from simulator.optimize.objective import _product_class_objective_evidence

    excluded = [{
        "species": "Si",
        "account": "process.cleaned_melt",
        "reason": "missing LJ row",
        "upper_bound_kg": "unbounded",
    }]
    flag = {"status": "partial", "reason": "missing LJ row"}
    evidence = _product_class_objective_evidence(
        "metals_total_kg",
        {
            "metals_plus_O2": {
                "status": "partial",
                "availability_flag": flag,
                "excluded": excluded,
            }
        },
    )

    assert evidence["source_class_path"] == "metals_plus_O2"
    assert evidence["value_status"] == "partial"
    assert evidence["availability_flag"] == flag
    assert evidence["excluded"] == excluded
    assert evidence["total_basis"] == "lower_bound"


def test_missing_stage_3_capture_refuses_silica_objective_with_reason() -> None:
    from simulator.three_product_report import classify_products

    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    product_classes = classify_products(sim)
    product_kg = _metric_value(
        "pure_silica_glass_kg", sim, {}, product_classes
    )

    assert product_kg is None
    evidence = objective_module._silica_objective_evidence(
        product_classes, product_kg
    )
    assert evidence["value_status"] == "unavailable"
    assert evidence["certification_flag"]["status"] == "unavailable"
    assert evidence["certification_flag"]["reason"] == "stage_3_missing"


def test_energy_component_and_per_product_metrics_read_scoped_energy() -> None:
    sim = SimpleNamespace(
        energy_electrical_plus_evaporation_cumulative_kWh=18.0,
        energy_cumulative_breakdown_kWh={
            "electrical": 6.0,
            "evaporation_thermal": 12.0,
            "latent": 5.0,
            "dissociation": 7.0,
            "electrical_plus_evaporation": 18.0,
        },
        record=SimpleNamespace(
            energy_electrical_plus_evaporation_kWh=18.0,
            energy_electrical_kWh=6.0,
            energy_evaporation_thermal_kWh=12.0,
            energy_latent_kWh=5.0,
            energy_dissociation_kWh=7.0,
        ),
    )
    product_classes = {"metals_plus_O2": {"class_total_kg": 3.0}}

    assert _metric_value(
        ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC, sim, {}, product_classes
    ) == pytest.approx(18.0)
    assert _metric_value(LEGACY_ENERGY_KWH_METRIC, sim, {}, product_classes) == pytest.approx(18.0)
    assert _metric_value(
        "electrical_energy_kWh", sim, {}, product_classes
    ) == pytest.approx(6.0)
    assert _metric_value(
        "evaporation_thermal_energy_kWh", sim, {}, product_classes
    ) == pytest.approx(12.0)
    assert _metric_value(
        "energy_electrical_plus_evaporation_per_product_kWh_per_kg",
        sim,
        {},
        product_classes,
    ) == pytest.approx(6.0)
    assert _metric_value(
        "dissociation_energy_per_product_kWh_per_kg",
        sim,
        {},
        product_classes,
    ) == pytest.approx(7.0 / 3.0)


def test_energy_objective_adds_staged_pumping_once_without_readding_turbine() -> None:
    # Helper anchor: Mars datum 610 Pa -> 500 Pa, 0.01 mol/s, 300 K, 1 h.
    # Its staged electrical result is ~0.008100986 kWh (the 5 mbar anchor in
    # test_pumping_cost), and it is disjoint from turbine-compressed O2.
    pumping = estimate_subambient_pump_cost(
        target_pressure_pa=500.0,
        offgas_mol_per_s=0.01,
        duration_s=3600.0,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
        gas_temperature_K=300.0,
    )
    sim = SimpleNamespace(
        energy_electrical_plus_evaporation_cumulative_kWh=18.0,
        energy_cumulative_breakdown_kWh={
            "electrical": 6.0,
            "evaporation_thermal": 12.0,
            "electrical_plus_evaporation": 18.0,
        },
        record=SimpleNamespace(
            cost_rollup={
                "pumping_diagnostic": {
                    "status": "pumping_feasibility_unresolved",
                    "pumping_electrical_kWh": pumping.energy_kWh,
                },
                # This aggregate already includes 2 kWh turbine work. Reading
                # it would re-add turbine energy already present in the 6 kWh
                # electrical baseline; the objective must read pump only.
                "auxiliary_electrical_diagnostic": {
                    "components_kWh": {
                        "turbine": 2.0,
                        "pumping": pumping.energy_kWh,
                    },
                    "auxiliary_electrical_kWh": 2.0 + pumping.energy_kWh,
                },
            },
        ),
    )
    product_classes = {"metals_plus_O2": {"class_total_kg": 3.0}}

    assert pumping.energy_kWh == pytest.approx(0.008100986135507714)
    assert _metric_value(
        ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
        sim,
        {},
        product_classes,
    ) == pytest.approx(18.0 + pumping.energy_kWh)
    assert _metric_value(
        "electrical_energy_kWh", sim, {}, product_classes
    ) == pytest.approx(6.0 + pumping.energy_kWh)
    assert _metric_value(
        "evaporation_thermal_energy_kWh", sim, {}, product_classes
    ) == pytest.approx(12.0)
    assert _metric_value(
        "energy_electrical_plus_evaporation_per_product_kWh_per_kg",
        sim,
        {},
        product_classes,
    ) == pytest.approx((18.0 + pumping.energy_kWh) / 3.0)


def test_certified_pumping_diagnostic_without_energy_fails_closed() -> None:
    sim = SimpleNamespace(
        energy_electrical_plus_evaporation_cumulative_kWh=18.0,
    )
    execution = SimpleNamespace(
        certified_pumping_diagnostic={"status": "ok", "feasible": True},
    )

    with pytest.raises(
        ObjectiveComputationError,
        match="pumping_diagnostic.pumping_electrical_kWh is not numeric",
    ):
        _metric_value(
            ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
            sim,
            {},
            {},
            run_execution=execution,
        )


def test_unavailable_pumping_energy_is_not_ranked_as_zero() -> None:
    from simulator.cost_energy import unavailable_quantity

    sim = SimpleNamespace(
        energy_electrical_plus_evaporation_cumulative_kWh=18.0,
    )
    execution = SimpleNamespace(
        certified_pumping_diagnostic={
            "status": "refused",
            "pumping_electrical_kWh": unavailable_quantity(
                reason="missing-o2-vented-flow", units="kWh"
            ),
        },
    )

    with pytest.raises(
        ObjectiveComputationError,
        match="pumping_diagnostic.pumping_electrical_kWh is unavailable: "
        "missing-o2-vented-flow",
    ):
        _metric_value(
            ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
            sim,
            {},
            {},
            run_execution=execution,
        )


def test_throughput_cost_metrics_read_cost_rollup_and_lifespan_rate(monkeypatch) -> None:
    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: 2.0)
    cost_parameters = CostParameters(
        electricity_cost_per_kWh=2.0,
        furnace_resinter_cost_usd=100.0,
        depreciation_expense_per_run=7.0,
        generic_reagent_cost_per_kg=5.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 1.0,
            "K": 1.0,
            "Mg": 1.0,
            "Ca": 1.0,
        },
    )
    sim = SimpleNamespace(
        energy_electrical_plus_evaporation_cumulative_kWh=10.0,
        energy_cumulative_breakdown_kWh={
            "electrical": 10.0,
            "evaporation_thermal": 0.0,
        },
        record=SimpleNamespace(
            cost_rollup={
                "run_input_cost": {
                    "physical_cost": {
                        "thermal_flux_h": 1234.0,
                        "furnace_h": 6.0,
                    },
                    "owner_ratify_money_projection": 1294.0,
                }
            }
        )
    )
    run_execution = SimpleNamespace(
        simulator=sim,
        trace=SimpleNamespace(
            wall_deposit_by_segment_species_kg={
                ("stage_0_to_stage_1", "SiO"): 0.20,
                ("stage_0_to_stage_1", "Na"): 0.05,
            }
        ),
    )
    product_classes = {"metals_plus_O2": {"class_total_kg": 1.0}}

    assert _metric_value(
        "solar_thermal_flux_h",
        sim,
        {},
        product_classes,
        run_execution=run_execution,
    ) == pytest.approx(1234.0)
    assert _metric_value(
        "furnace_time_h",
        sim,
        {},
        product_classes,
        run_execution=run_execution,
    ) == pytest.approx(6.0)
    assert _metric_value(
        "throughput_cost_owner_ratify_usd",
        sim,
        {"consumed_Fe_reagent": 2.0},
        product_classes,
        run_execution=run_execution,
        cost_parameters=cost_parameters,
    ) == pytest.approx(42.5)
    assert _metric_value(
        "furnace_lifespan_consumed_fraction",
        sim,
        {},
        product_classes,
        run_execution=run_execution,
    ) == pytest.approx(0.125)


def test_lifespan_cost_metric_is_not_costed_when_threshold_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: None)
    sim = SimpleNamespace()
    run_execution = SimpleNamespace(
        simulator=sim,
        trace=SimpleNamespace(
            wall_deposit_by_segment_species_kg={
                ("stage_0_to_stage_1", "SiO"): 0.20,
                ("stage_0_to_stage_1", "Na"): 0.05,
            }
        ),
    )
    product_classes = {"metals_plus_O2": {"class_total_kg": 1.0}}

    assert _metric_value(
        "furnace_lifespan_consumed_fraction",
        sim,
        {},
        product_classes,
        run_execution=run_execution,
    ) is None
    summary = objective_module._furnace_lifespan_cost_summary(run_execution, sim)
    assert summary["lifespan_cost_status"] == "threshold_unavailable"
    assert summary["furnace_lifespan_consumed_fraction"] is None
    assert summary["wall_deposit_total_kg"] == pytest.approx(0.25)
    assert summary["wall_deposit_kg_by_species"] == {
        "Na": pytest.approx(0.05),
        "SiO": pytest.approx(0.20),
    }


def test_multisegment_lifespan_and_economics_normalize_cumulative_campaign_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: 10.0)
    deposits = {
        ("hot_duct", "SiO"): 8.0,
        ("cold_duct", "Na"): 0.8,
    }
    sim = SimpleNamespace()
    run_execution = SimpleNamespace(
        simulator=sim,
        campaigns_elapsed=4.0,
        trace=SimpleNamespace(wall_deposit_by_segment_species_kg=deposits),
    )
    cost_parameters = CostParameters(
        electricity_cost_per_kWh=0.0,
        furnace_resinter_cost_usd=100.0,
        depreciation_expense_per_run=999.0,
        generic_reagent_cost_per_kg=0.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 0.0,
            "K": 0.0,
            "Mg": 0.0,
            "Ca": 0.0,
        },
    )

    assert objective_module._campaigns_to_resinter(
        deposits,
        campaigns_elapsed=4.0,
    ) == pytest.approx(5.0)
    assert objective_module._aggregate_campaigns_to_resinter(
        deposits,
        campaigns_elapsed=4.0,
    ) == pytest.approx(10.0 / 2.2)

    lifespan = objective_module._furnace_lifespan_cost_summary(run_execution, sim)
    assert lifespan["wall_deposit_total_kg"] == pytest.approx(2.2)
    assert lifespan["wall_deposit_cumulative_total_kg"] == pytest.approx(8.8)
    assert lifespan["furnace_lifespan_consumed_fraction"] == pytest.approx(0.22)

    depreciation = objective_module._depreciation_expense_per_run_summary(
        run_execution,
        sim,
        cost_parameters=cost_parameters,
    )
    assert depreciation["campaigns_to_resinter"] == pytest.approx(10.0 / 2.2)
    assert depreciation["campaigns_to_resinter"].hex() == (
        "0x1.22e8ba2e8ba2ep+2"
    )
    assert depreciation["depreciation_expense_per_run_usd"] == pytest.approx(22.0)
    assert depreciation["depreciation_expense_per_run_usd"].hex() == (
        "0x1.6000000000001p+4"
    )


def test_marginal_cost_uses_depreciation_default_and_shuttle_replacement_rate() -> None:
    cost_parameters = CostParameters(
        electricity_cost_per_kWh=7.0,
        furnace_resinter_cost_usd=999.0,
        depreciation_expense_per_run=11.0,
        generic_reagent_cost_per_kg=5.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 13.0,
            "K": 17.0,
            "Mg": 19.0,
            "Ca": 23.0,
        },
    )
    sim = SimpleNamespace(
        energy_electrical_plus_evaporation_cumulative_kWh=1.0,
        energy_cumulative_breakdown_kWh={
            "electrical": 1.0,
            "evaporation_thermal": 0.0,
        },
        record=SimpleNamespace(cost_rollup={}),
    )

    assert _metric_value(
        "throughput_cost_owner_ratify_usd",
        sim,
        {
            "consumed_Fe_reagent": 2.0,
            "consumed_Na_reagent": 3.0,
        },
        {},
        run_execution=SimpleNamespace(simulator=sim),
        cost_parameters=cost_parameters,
    ) == pytest.approx(67.0)


def test_marginal_cost_prices_electrical_and_evaporation_thermal_separately() -> None:
    cost_parameters = CostParameters(
        electricity_cost_per_kWh=10.0,
        solar_heat_cost_per_kWh=4.0,
        furnace_resinter_cost_usd=0.0,
        depreciation_expense_per_run=0.0,
        generic_reagent_cost_per_kg=0.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 0.0,
            "K": 0.0,
            "Mg": 0.0,
            "Ca": 0.0,
        },
    )
    sim = SimpleNamespace(
        energy_cumulative_breakdown_kWh={
            "electrical": 2.0,
            "evaporation_thermal": 3.0,
        },
        record=SimpleNamespace(cost_rollup={}),
    )

    assert _metric_value(
        "throughput_cost_owner_ratify_usd",
        sim,
        {},
        {},
        run_execution=SimpleNamespace(simulator=sim),
        cost_parameters=cost_parameters,
    ) == pytest.approx(32.0)


def test_cost_objective_ranking_prices_pumping_sidecar() -> None:
    definitions = (
        ObjectiveDefinition(
            "throughput_cost_owner_ratify_usd",
            "minimize",
            "usd",
            ordinal=0,
        ),
    )

    cost_parameters = CostParameters(
        electricity_cost_per_kWh=1.0,
        furnace_resinter_cost_usd=0.0,
        depreciation_expense_per_run=0.0,
        generic_reagent_cost_per_kg=0.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 0.0,
            "K": 0.0,
            "Mg": 0.0,
            "Ca": 0.0,
        },
    )

    def sim_with(base_energy_kWh: float, *, pumping_electrical_kWh: float | None = None):
        cost_rollup = {
            "run_input_cost": {
                "physical_cost": {
                    "thermal_flux_h": 1234.0,
                    "furnace_h": 6.0,
                },
                "owner_ratify_money_projection": 0.0,
            }
        }
        if pumping_electrical_kWh is not None:
            cost_rollup["pumping_diagnostic"] = {
                "status": "ok",
                "pumping_electrical_kWh": pumping_electrical_kWh,
            }
        return SimpleNamespace(
            energy_electrical_plus_evaporation_cumulative_kWh=base_energy_kWh,
            energy_cumulative_breakdown_kWh={
                "electrical": base_energy_kWh,
                "evaporation_thermal": 0.0,
            },
            record=SimpleNamespace(cost_rollup=cost_rollup),
        )

    def score(sim: SimpleNamespace) -> tuple[float, ...]:
        value = _metric_value(
            "throughput_cost_owner_ratify_usd",
            sim,
            {},
            {},
            run_execution=SimpleNamespace(simulator=sim),
            cost_parameters=cost_parameters,
        )
        return objective_scores(
            {"throughput_cost_owner_ratify_usd": value},
            definitions,
        )

    without_pumping = (
        ("lower_cost", sim_with(100.0)),
        ("higher_cost", sim_with(200.0)),
    )
    with_pumping = (
        ("lower_cost", sim_with(100.0, pumping_electrical_kWh=1_000_000.0)),
        ("higher_cost", sim_with(200.0)),
    )

    rank_without = [
        name for name, sim in sorted(
            without_pumping,
            key=lambda item: score(item[1]),
            reverse=True,
        )
    ]
    rank_with = [
        name for name, sim in sorted(
            with_pumping,
            key=lambda item: score(item[1]),
            reverse=True,
        )
    ]

    assert rank_without == ["lower_cost", "higher_cost"]
    assert rank_with == ["higher_cost", "lower_cost"]


def test_incomplete_objective_importance_evidence_raises_insufficient_evidence() -> None:
    profile = {
        "objectives": [
            {
                "metric": "oxygen_kg",
                "sense": "maximize",
                "units": "kg",
                "weight": 1.0,
            }
        ]
    }

    with pytest.raises(
        ObjectiveProfileError,
        match="insufficient-evidence: objectives\\[0\\] 'oxygen_kg' missing rationale",
    ):
        objective_importance_evidence(profile)


def test_pareto_front_preserves_stable_non_dominated_order() -> None:
    items = (
        {"id": "d", "objectives": {"oxygen_kg": 1.5, "energy_kWh": 3.0}},
        {"id": "a", "objectives": {"oxygen_kg": 1.0, "energy_kWh": 5.0}},
        {"id": "c", "objectives": {"oxygen_kg": 2.0, "energy_kWh": 4.0}},
        {"id": "b", "objectives": {"oxygen_kg": 2.0, "energy_kWh": 5.0}},
    )

    front = pareto_front(
        items,
        DEFINITIONS,
        objective_getter=lambda item: item["objectives"],
    )

    assert [item["id"] for item in front] == ["d", "c"]


def test_pareto_front_with_empty_score_rows_preserves_prior_empty_front() -> None:
    items = ("a", "b")

    front = pareto_front(
        items,
        (),
        objective_getter=lambda item: (),
        score_getter=lambda item: (),
    )

    assert front == ()


def test_furnace_amortization_cost_trades_continuously_against_yield() -> None:
    assert furnace_amortization_cost_per_run(
        20.0, 1.0, 500.0, 1.0, has_positive_fouling=True
    ) == pytest.approx(10_000.0)
    assert furnace_amortization_cost_per_run(
        20.0, 500.0, 500.0, 1.0, has_positive_fouling=True
    ) == pytest.approx(20.0)
    assert furnace_amortization_cost_per_run(
        20.0, math.inf, 500.0, 1.0, has_positive_fouling=True
    ) == pytest.approx(20.0)
    assert furnace_amortization_cost_per_run(
        20.0, math.inf, 500.0, 1.0, has_positive_fouling=False
    ) == 0.0
    assert objective_module._has_positive_wall_deposit({("wall", "SiO"): 1e-30})
    assert not objective_module._has_positive_wall_deposit({("wall", "SiO"): 0.0})
    lifetime = objective_module._coating_lifetime_projection(
        {},
        coating_margin=SimpleNamespace(
            status_payload={
                "campaigns_to_resinter_worst_segment": 1e30,
                "wall_deposit_kg_per_campaign": 1e-30,
            }
        ),
    )
    assert lifetime["campaigns_to_resinter_worst_segment"] == pytest.approx(1e30)
    assert lifetime["has_positive_qualified_fouling"] is True

    params = CostParameters(
        electricity_cost_per_kWh=1.0,
        furnace_resinter_cost_usd=1.0,
        depreciation_expense_per_run=1.0,
        generic_reagent_cost_per_kg=1.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 1.0,
            "K": 1.0,
            "Mg": 1.0,
            "Ca": 1.0,
        },
        furnace_lifetime_cost_multiplier=10.0,
        min_fouling_penalty=3.0,
    )
    sim = SimpleNamespace(
        record=SimpleNamespace(
            cost_rollup={
                "run_input_cost": {"owner_ratify_money_projection": 20.0}
            }
        )
    )
    barely_fouling = objective_module._furnace_amortization_summary(
        sim,
        math.inf,
        has_positive_fouling=True,
        cost_parameters=params,
    )
    zero_fouling = objective_module._furnace_amortization_summary(
        sim,
        math.inf,
        has_positive_fouling=False,
        cost_parameters=params,
    )
    assert barely_fouling["furnace_amortization_cost_per_run_usd"] == 60.0
    assert barely_fouling["furnace_amortization_batch_cost_equivalents"] == 3.0
    assert zero_fouling["furnace_amortization_cost_per_run_usd"] == 0.0
    unavailable = objective_module._furnace_amortization_summary(
        SimpleNamespace(
            record=SimpleNamespace(
                cost_rollup={
                    "run_input_cost": {
                        "allocation_status": "unavailable_after_cost_observation_error",
                        "owner_ratify_money_projection": 0.0,
                    }
                }
            )
        ),
        1.0,
        has_positive_fouling=True,
        cost_parameters=params,
    )
    with pytest.raises(ObjectiveComputationError, match="batch_cost_unavailable"):
        cost_adjusted_objective_scores(
            {"oxygen_kg": 1.0, "energy_kWh": 1.0},
            DEFINITIONS,
            product_summary=unavailable,
        )
    items = (
        {
            "id": "violates",
            "objectives": {"oxygen_kg": 100.0, "energy_kWh": 2.0},
            "summary": _available_furnace_summary(2_500.0),
        },
        {
            "id": "satisfies",
            "objectives": {"oxygen_kg": 10.0, "energy_kWh": 2.0},
            "summary": _available_furnace_summary(0.0),
        },
    )

    front = pareto_front(
        items,
        DEFINITIONS,
        objective_getter=lambda item: item["objectives"],
        score_getter=lambda item: cost_adjusted_objective_scores(
            item["objectives"],
            DEFINITIONS,
            product_summary=item["summary"],
        ),
    )

    assert [item["id"] for item in front] == ["satisfies"]

    low_multiplier_items = tuple(
        {**item, "summary": _available_furnace_summary(penalty)}
        for item, penalty in zip(items, (1.0, 0.0))
    )
    low_multiplier_front = pareto_front(
        low_multiplier_items,
        DEFINITIONS,
        objective_getter=lambda item: item["objectives"],
        score_getter=lambda item: cost_adjusted_objective_scores(
            item["objectives"], DEFINITIONS, product_summary=item["summary"]
        ),
    )
    assert [item["id"] for item in low_multiplier_front] == ["violates", "satisfies"]
    assert cost_adjusted_objective_scores(
        low_multiplier_items[0]["objectives"],
        DEFINITIONS,
        product_summary=low_multiplier_items[0]["summary"],
    )[0] > cost_adjusted_objective_scores(
        low_multiplier_items[1]["objectives"],
        DEFINITIONS,
        product_summary=low_multiplier_items[1]["summary"],
    )[0]

    bounded = (ObjectiveDefinition("quality", "maximize", "score_0_1", ordinal=0),)
    assert cost_adjusted_objective_scores(
        {"quality": 1.0},
        bounded,
        product_summary=_available_furnace_summary(1.0),
    )[0] > cost_adjusted_objective_scores(
        {"quality": 0.4},
        bounded,
        product_summary=_available_furnace_summary(0.0),
    )[0]


def test_refused_wall_evidence_is_unavailable_not_zero_cost() -> None:
    authority_summary = objective_module._coating_authority_summary({
        "output_status": "status_bearing",
        "status_reason": "carrier wall partial refused",
        "code": "wall_saturation_pressure_refused",
        "wall_saturation_pressure_refused_species": ["Na"],
    })
    params = CostParameters(
        electricity_cost_per_kWh=1.0,
        furnace_resinter_cost_usd=1.0,
        depreciation_expense_per_run=1.0,
        generic_reagent_cost_per_kg=1.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 1.0,
            "K": 1.0,
            "Mg": 1.0,
            "Ca": 1.0,
        },
        furnace_lifetime_cost_multiplier=10.0,
        min_fouling_penalty=1.0,
    )
    cost_summary = objective_module._furnace_amortization_summary(
        SimpleNamespace(
            record=SimpleNamespace(
                cost_rollup={
                    "run_input_cost": {
                        "owner_ratify_money_projection": 20.0,
                    }
                }
            )
        ),
        math.inf,
        has_positive_fouling=False,
        cost_parameters=params,
        coating_status=authority_summary["coating_status"],
        coating_status_reason=authority_summary["coating_status_reason"],
    )
    summary = {**authority_summary, **cost_summary}

    assert summary["coating_status"] == "unavailable"
    assert summary["coating_authoritative"] is False
    assert summary["furnace_amortization_status"] == "unavailable"
    assert summary["furnace_amortization_cost_per_run_usd"] is None
    assert summary["furnace_amortization_batch_cost_equivalents"] is None
    with pytest.raises(ObjectiveComputationError, match="furnace amortization unavailable"):
        cost_adjusted_objective_scores(
            {"oxygen_kg": 1.0, "energy_kWh": 1.0},
            DEFINITIONS,
            product_summary=summary,
        )


def test_refused_empty_wall_deposit_is_unavailable_for_pricing_and_ranking() -> None:
    run_execution = _refused_empty_wall_deposit_execution()
    reference = _run_reference(
        run_execution,
        {},
        cost_parameters=_furnace_test_cost_parameters(),
    )
    summary = reference.product_summary

    assert summary["campaigns_to_resinter"] is None
    assert summary["aggregate_campaigns_to_resinter"] is None
    assert summary["campaigns_to_resinter_worst_segment"] is None
    assert summary["has_positive_qualified_fouling"] is None
    assert summary["furnace_amortization_status"] == "batch_cost_unavailable"
    assert "furnace_amortization_cost_per_run_usd" not in summary
    assert summary["furnace_amortization_batch_cost_equivalents"] is None
    with pytest.raises(ObjectiveComputationError, match="batch_cost_unavailable"):
        cost_adjusted_objective_scores(
            {"oxygen_kg": 10.0, "energy_kWh": 1.0},
            DEFINITIONS,
            product_summary=summary,
        )

    refused_record = SimpleNamespace(
        candidate_id="refused",
        cache_key="refused",
        objectives={"oxygen_kg": 10.0, "energy_kWh": 1.0},
        product_summary=summary,
    )
    clean_record = SimpleNamespace(
        candidate_id="clean",
        cache_key="clean",
        objectives={"oxygen_kg": 1.0, "energy_kWh": 1.0},
        product_summary=_available_furnace_summary(0.0),
    )
    assert study._record_objective_scores(refused_record, DEFINITIONS) == (
        None,
        None,
    )
    assert study._rank_key(refused_record, DEFINITIONS) > study._rank_key(
        clean_record,
        DEFINITIONS,
    )


def test_nonnegligible_refused_wall_species_uses_supply_bound_and_stays_ranked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: 4.5)
    execution = _refused_empty_wall_deposit_execution()
    execution.trace.wall_zone_by_segment = {"hot_wall": "Hot"}
    execution.trace.wall_deposit_sticking_authority = {
        "wall_saturation_pressure_refusals_by_species": {
            "SiO": {
                "hot_wall": {
                    "status": "refused",
                    "output_status": "status_bearing",
                    "reason": "antoine domain refused for SiO",
                }
            }
        }
    }
    execution.snapshots = (
        SimpleNamespace(
            duration_h=1.0,
            evap_flux=SimpleNamespace(species_kg_hr={"SiO": 0.25}),
        ),
    )
    reference = _run_reference(
        execution,
        {},
        cost_parameters=_furnace_test_cost_parameters(),
    )
    summary = reference.product_summary

    assert summary["furnace_amortization_status"] == "available"
    assert summary["wall_deposit_upper_bound_used"] is True
    booking = next(
        item
        for item in summary["wall_supply_bound_bookings"]
        if item["species"] == "SiO"
    )
    assert booking["reason"] == "wall_deposit_upper_bound_used"
    assert booking["flag"] == "wall_deposit_upper_bound_used"
    assert booking["deposit_kg"] == pytest.approx(0.25)
    assert booking["supply_upper_bound_kg"] == pytest.approx(0.25)
    assert "antoine domain refused" in booking["refusal_reason"]
    authority = summary["wall_deposit_sticking_authority"]
    assert "SiO" in authority["wall_saturation_pressure_refused_species"]
    assert summary["wall_deposit_kg_by_species"]["SiO"] == pytest.approx(0.25)
    adjusted = cost_adjusted_objective_scores(
        {"oxygen_kg": 10.0, "energy_kWh": 1.0},
        DEFINITIONS,
        product_summary=summary,
    )
    unpenalised = cost_adjusted_objective_scores(
        {"oxygen_kg": 10.0, "energy_kWh": 1.0},
        DEFINITIONS,
        product_summary=_available_furnace_summary(0.0),
    )
    assert adjusted[0] is not None and unpenalised[0] is not None
    assert adjusted[0] < unpenalised[0]
    # The penalty multiplies minimized energy too, so a same-energy twin is
    # not dominated. This twin has less oxygen and more energy than the
    # penalised scores, and the bounded candidate still owns the front.
    penalty = summary["furnace_amortization_batch_cost_equivalents"]
    bounded = {
        "id": "bounded",
        "objectives": {"oxygen_kg": 10.0, "energy_kWh": 1.0},
        "summary": summary,
    }
    dominated = {
        "id": "dominated",
        "objectives": {
            "oxygen_kg": 0.1,
            "energy_kWh": (1.0 + penalty) + 1.0,
        },
        "summary": _available_furnace_summary(0.0),
    }
    front = pareto_front(
        (bounded, dominated),
        DEFINITIONS,
        objective_getter=lambda item: item["objectives"],
        score_getter=lambda item: cost_adjusted_objective_scores(
            item["objectives"],
            DEFINITIONS,
            product_summary=item["summary"],
        ),
    )
    assert [item["id"] for item in front] == ["bounded"]
    record = SimpleNamespace(
        candidate_id="bounded",
        cache_key="bounded",
        objectives=bounded["objectives"],
        product_summary=summary,
    )
    assert study._record_objective_scores(record, DEFINITIONS) == adjusted


# Invariant at ``_book_wall_deposits_at_vapour_supply``, the only place a
# refused wall species becomes a priced deposit. Every scoring channel
# (the booking itself, the coating product summary, the furnace-lifespan
# summary, and the tap-hour projection) goes through it, and every reader
# reports that mass.
#
# - No computed flux, a missing flux interval, or no charge mass to fix
#   the ledger resolution: not booked. A partial integral is not a supply.
#   The measured entries stay, and the refusal still blocks the figure.
# - Supply at or below the ledger resolution: authorized zero. Existing
#   entries of that species are removed. The scored mass is 0, which is
#   the booking's deposit_kg and is <= the supply.
# - Supply above that resolution: the priced aggregate equals the supply,
#   once, on one eligible segment. A partial measurement is removed
#   first, so the score is not the partial and not partial + supply.
#   A later reader must not replace that positive mass with zero.
# - The maximum segment load equals the closed form
#   max_s (existing_load_s + sum of non-negligible supplies eligible at s).
#   Species order does not change it. Every supply is booked wholly on
#   the eligible segment with the highest closed-form load. The smaller
#   segment id wins only when those loads are equal. A species eligible
#   at the argmax s* therefore lands on s*. A species that cannot use s*
#   lands on its own best closed-form segment, not on its smallest id.
_WALL_BOUND_UPPER = objective_module.WALL_DEPOSIT_UPPER_BOUND_USED
_WALL_BOUND_BELOW = objective_module.BELOW_WALL_SUPPLY_UPPER_BOUND
_WALL_BOUND_CASES = (
    {
        "deposit": (),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("hot_wall", "SiO"), 1.0),),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    {
        "deposit": ((("cold_wall", "SiO"), 0.1),),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("hot_wall", "SiO"), 1.0),),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    {
        "deposit": ((("hot_wall", "SiO"), 0.1),),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("hot_wall", "SiO"), 1.0),),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    {
        "deposit": (
            (("cold_wall", "SiO"), 0.04),
            (("hot_wall", "SiO"), 0.06),
        ),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("hot_wall", "SiO"), 1.0),),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    {
        "deposit": (
            (("cold_wall", "SiO"), 0.4),
            (("hot_wall", "SiO"), 0.6),
        ),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("hot_wall", "SiO"), 1.0),),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    {
        "deposit": ((("cold_wall", "SiO"), 2.0),),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("hot_wall", "SiO"), 1.0),),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    {
        "deposit": (
            (("cold_wall", "SiO"), 0.1),
            (("cold_wall", "Na"), 3.0),
        ),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": (
            (("hot_wall", "SiO"), 1.0),
            (("cold_wall", "Na"), 3.0),
        ),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    {
        "deposit": (),
        "flux": (("SiO", 1.0), ("Mg", 0.5)),
        "refused": (("SiO", "hot_wall"), ("Mg", "hot_wall")),
        "charge": 1000.0,
        "entries": (
            (("hot_wall", "SiO"), 1.0),
            (("hot_wall", "Mg"), 0.5),
        ),
        "species": (
            ("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),
            ("Mg", _WALL_BOUND_UPPER, 0.5, 0.5),
        ),
    },
    {
        "deposit": ((("cold_wall", "SiO"), 0.1),),
        "flux": (("SiO", 1.0), ("CrO2", 1.0e-12)),
        "refused": (("SiO", "hot_wall"), ("CrO2", "hot_wall")),
        "charge": 1000.0,
        "entries": ((("hot_wall", "SiO"), 1.0),),
        "species": (
            ("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),
            ("CrO2", _WALL_BOUND_BELOW, 0.0, 1.0e-12),
        ),
    },
    {
        "deposit": ((("cold_wall", "CrO2"), 0.2),),
        "flux": (("CrO2", 1.0e-12),),
        "refused": (("CrO2", "hot_wall"),),
        "charge": 1000.0,
        "entries": (),
        "species": (("CrO2", _WALL_BOUND_BELOW, 0.0, 1.0e-12),),
    },
    {
        "deposit": (
            (("A", "CrO2"), 0.15),
            (("B", "CrO2"), 0.05),
            (("B", "Na"), 3.0),
        ),
        "flux": (("CrO2", 1.0e-12),),
        "refused": (("CrO2", "A"), ("CrO2", "B")),
        "charge": 1000.0,
        "entries": ((("B", "Na"), 3.0),),
        "species": (("CrO2", _WALL_BOUND_BELOW, 0.0, 1.0e-12),),
    },
    {
        "deposit": ((("B", "Na"), 3.0),),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "A"), ("SiO", "B")),
        "charge": 1000.0,
        "entries": (
            (("B", "SiO"), 1.0),
            (("B", "Na"), 3.0),
        ),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    {
        "deposit": ((("A", "Na"), 3.0), (("B", "K"), 3.0)),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "B"), ("SiO", "A")),
        "charge": 1000.0,
        "entries": (
            (("A", "SiO"), 1.0),
            (("A", "Na"), 3.0),
            (("B", "K"), 3.0),
        ),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    {
        "deposit": ((("C", "Na"), 10.0), (("B", "K"), 3.0)),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "A"), ("SiO", "B")),
        "charge": 1000.0,
        "entries": (
            (("B", "SiO"), 1.0),
            (("B", "K"), 3.0),
            (("C", "Na"), 10.0),
        ),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    {
        "deposit": (),
        "flux": (("CrO2", 1.0e-12),),
        "refused": (("CrO2", "hot_wall"),),
        "charge": 1000.0,
        "entries": (),
        "species": (("CrO2", _WALL_BOUND_BELOW, 0.0, 1.0e-12),),
    },
    {
        "deposit": ((("cold_wall", "SiO"), 0.1),),
        "flux": (),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("cold_wall", "SiO"), 0.1),),
        "species": (("SiO", None, None, None),),
    },
    # Ledger-committed species_kg_hr omits the carrier. The published rate
    # condensation was shown is still the supply.
    {
        "deposit": (),
        "flux": (),
        "published_flux": (("CrO2", 1.0e-12),),
        "refused": (("CrO2", "hot_wall"),),
        "charge": 1000.0,
        "entries": (),
        "species": (("CrO2", _WALL_BOUND_BELOW, 0.0, 1.0e-12),),
    },
    {
        "deposit": ((("cold_wall", "SiO"), 0.1),),
        "flux": (),
        "published_flux": (("SiO", 1.0),),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("hot_wall", "SiO"), 1.0),),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    {
        "deposit": ((("cold_wall", "SiO"), 0.1),),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "hot_wall"),),
        "charge": None,
        "entries": ((("cold_wall", "SiO"), 0.1),),
        "species": (("SiO", None, None, None),),
    },
    # Both hours publish SiO. The supply is the sum, not either hour alone.
    {
        "deposit": (),
        "flux_snapshots": (
            {"hour": 1, "duration_h": 1.0, "flux": (("SiO", 1.0),)},
            {"hour": 2, "duration_h": 1.0, "flux": (("SiO", 0.5),)},
        ),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("hot_wall", "SiO"), 1.5),),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.5, 1.5),),
    },
    # One hour records SiO. The next hour has no evaporation flux. The
    # first hour's mass is not the supply, and the refusal stays.
    {
        "deposit": ((("cold_wall", "SiO"), 0.1),),
        "flux_snapshots": (
            {"hour": 1, "duration_h": 1.0, "flux": (("SiO", 1.0),)},
            {"hour": 2, "duration_h": 1.0, "unavailable": True},
        ),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("cold_wall", "SiO"), 0.1),),
        "species": (("SiO", None, None, None),),
    },
    # The gap is the window, not one species. Mg was recorded on the same
    # hour as SiO and is still not a supply.
    {
        "deposit": ((("cold_wall", "Na"), 3.0),),
        "flux_snapshots": (
            {
                "hour": 1,
                "duration_h": 1.0,
                "flux": (("SiO", 1.0), ("Mg", 0.5)),
            },
            {"hour": 2, "duration_h": 1.0, "unavailable": True},
        ),
        "refused": (("SiO", "hot_wall"), ("Mg", "hot_wall")),
        "charge": 1000.0,
        "entries": ((("cold_wall", "Na"), 3.0),),
        "species": (
            ("SiO", None, None, None),
            ("Mg", None, None, None),
        ),
    },
    # An explicit zero rate is evidence. It contributes no mass.
    {
        "deposit": (),
        "flux_snapshots": (
            {"hour": 1, "duration_h": 1.0, "flux": (("SiO", 1.0),)},
            {"hour": 2, "duration_h": 1.0, "flux": (("SiO", 0.0),)},
        ),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("hot_wall", "SiO"), 1.0),),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    # Hour 1 publishes SiO at 1 kg/h. Hour 2's map names Mg and carries
    # no SiO physical-zero record. That omission is not a zero hour, so
    # the 1 kg partial sum is not the supply. The refusal stays.
    {
        "deposit": ((("cold_wall", "SiO"), 0.1),),
        "flux_snapshots": (
            {"hour": 1, "duration_h": 1.0, "flux": (("SiO", 1.0),)},
            {"hour": 2, "duration_h": 1.0, "flux": (("Mg", 0.4),)},
        ),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("cold_wall", "SiO"), 0.1),),
        "species": (("SiO", None, None, None),),
    },
    # Hour 1 publishes SiO at 1 kg/h. Hour 2 publishes Mg and records a
    # SiO transport refusal. The 1 kg partial sum is not SiO's supply.
    # The measured kilogram stays, and the availability refusal stays.
    {
        "deposit": ((("cold_wall", "SiO"), 0.1),),
        "flux_snapshots": (
            {"hour": 1, "duration_h": 1.0, "flux": (("SiO", 1.0),)},
            {
                "hour": 2,
                "duration_h": 1.0,
                "flux": (),
                "published_flux": (("Mg", 0.4),),
                "carrier_refusals": ("SiO",),
            },
        ),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("cold_wall", "SiO"), 0.1),),
        "species": (("SiO", None, None, None),),
    },
    # Zero duration adds no mass, so a missing map there is not a gap.
    {
        "deposit": (),
        "flux_snapshots": (
            {"hour": 1, "duration_h": 1.0, "flux": (("SiO", 1.0),)},
            {"hour": 2, "duration_h": 0.0, "unavailable": True},
        ),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
        "entries": ((("hot_wall", "SiO"), 1.0),),
        "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
    },
    # 1 kg charge: ledger resolution is 5e-14 kg. 5e-13 kg is above that
    # resolution and below the 1e-12 kg/h debit floor. Scoring must keep
    # 5e-13 kg once the rate is on the supply map. This row injects the
    # rate into the snapshot. The production projector, which used to
    # drop it before the map existed, is
    # test_publisher_projection_keeps_every_computed_rate and
    # test_mixed_subthreshold_hours_book_the_full_integral.
    {
        "deposit": (),
        "flux": (("SiO", 5.0e-13),),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1.0,
        "entries": ((("hot_wall", "SiO"), 5.0e-13),),
        "species": (("SiO", _WALL_BOUND_UPPER, 5.0e-13, 5.0e-13),),
    },
)
_WALL_BOUND_CHANNELS = ("book", "product_summary", "lifespan", "tap")


def _wall_bound_flux_windows(case: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw = case.get("flux_snapshots")
    if raw is None:
        return (
            {
                "hour": 1,
                "duration_h": 1.0,
                "flux": case["flux"],
                "published_flux": case.get("published_flux"),
            },
        )
    return tuple(raw)


def _transport_refusal_channel(species: str) -> dict[str, object]:
    """Channel ``_calculate_evaporation`` writes for one unavailable carrier.

    The run stays up. The other species are still published. This species
    has no rate, and the authority records the refusal.
    """

    reason = "transport input unavailable"
    refusal_type = "EvaporationFluxConfigurationError"
    return {
        "species_id": species,
        "flux": {
            "kind": "refusal",
            "code": refusal_type,
            "detail": reason,
        },
        "refusal_code": refusal_type,
        "is_refused": True,
        "reason": reason,
        "authority_level": "unavailable",
        "extra": {
            "status": "unavailable",
            "refusal_type": refusal_type,
            "policy": "fail_loud_missing_transport_parameters",
            "reason": reason,
        },
    }


def _wall_bound_evap_flux(window: Mapping[str, object]) -> SimpleNamespace | None:
    if window.get("unavailable"):
        return None
    flux = {
        str(species): float(kg)
        for species, kg in window.get("flux", ())
    }
    published = window.get("published_flux")
    refused = tuple(window.get("carrier_refusals") or ())
    fields: dict[str, object] = {"species_kg_hr": flux}
    if published is not None:
        fields["published_species_kg_hr"] = {
            str(species): float(kg) for species, kg in published
        }
    channels = {
        str(species): _transport_refusal_channel(str(species))
        for species in refused
    }
    for species in tuple(window.get("eligible_channels") or ()):
        channels[str(species)] = _eligible_channel_record(str(species))
    if channels:
        fields["carrier_authority_by_species"] = channels
    interval_zero = window.get("interval_physical_zero")
    if interval_zero:
        fields["wall_supply_interval_physical_zero"] = {
            "reason": str(interval_zero),
            "evidence": "authorized_zero",
        }
    return SimpleNamespace(**fields)


def _wall_bound_execution(case: Mapping[str, object]):
    deposit = {
        (segment, species): float(kg)
        for (segment, species), kg in case["deposit"]
    }
    refused_by_species: dict[str, list[str]] = {}
    for species, segment in case["refused"]:
        segments = refused_by_species.setdefault(str(species), [])
        if segment not in segments:
            segments.append(segment)
    zones: dict[str, str] = {}
    for segment, _species in deposit:
        zones[segment] = "Rest" if "cold" in segment else "Hot"
    for segments in refused_by_species.values():
        for segment in segments:
            zones.setdefault(segment, "Hot")
    snapshots = tuple(
        SimpleNamespace(
            hour=int(window.get("hour", index)),
            duration_h=float(window.get("duration_h", 1.0)),
            evap_flux=_wall_bound_evap_flux(window),
            **(
                {"wall_deposit_by_segment_species_delta": dict(deposit)}
                if index == 1
                else {}
            ),
        )
        for index, window in enumerate(_wall_bound_flux_windows(case), start=1)
    )
    authority = {
        "wall_saturation_pressure_refusals_by_species": {
            species: {
                segment: {
                    "status": "refused",
                    "output_status": "status_bearing",
                    "reason": f"antoine domain refused for {species}",
                }
                for segment in segments
            }
            for species, segments in refused_by_species.items()
        }
    }
    sim = _FakeProductSim()
    sim.record.cost_rollup = {
        "run_input_cost": {"owner_ratify_money_projection": 20.0}
    }
    if case["charge"] is None:
        sim.record.batch_mass_kg = None
    else:
        sim.record.batch_mass_kg = float(case["charge"])
    execution = SimpleNamespace(
        simulator=sim,
        snapshots=snapshots,
        trace=SimpleNamespace(
            wall_deposit_by_segment_species_kg=dict(deposit),
            wall_zone_by_segment=zones,
            wall_deposit_sticking_authority=authority,
            snapshots=snapshots,
        ),
    )
    return execution, snapshots, deposit


def _wall_bound_refused_names(authority: Mapping[str, object]) -> set[str]:
    names: set[str] = set()
    raw = authority.get("wall_saturation_pressure_refused_species")
    if isinstance(raw, str):
        names.add(raw)
    elif isinstance(raw, (list, tuple, set, frozenset)):
        names.update(str(item) for item in raw if str(item))
    records = authority.get("wall_saturation_pressure_refusals_by_species")
    if isinstance(records, Mapping):
        names.update(str(item) for item in records if str(item))
    return names


def _wall_bound_entries(deposit: Mapping[object, object]) -> dict[tuple[str, str], float]:
    entries: dict[tuple[str, str], float] = {}
    for key, kg in deposit.items():
        if not isinstance(key, tuple) or len(key) != 2 or kg is None:
            continue
        amount = float(kg)
        if amount <= 0.0:
            continue
        entries[(str(key[0]), str(key[1]))] = amount
    return entries


def _wall_bound_flatten_segments(
    by_segment: Mapping[str, Mapping[str, float]],
) -> dict[tuple[str, str], float]:
    entries: dict[tuple[str, str], float] = {}
    for segment, species_kg in by_segment.items():
        for species, kg in species_kg.items():
            amount = float(kg)
            if amount <= 0.0:
                continue
            entries[(str(segment), str(species))] = amount
    return entries


def _wall_bound_zone_species_kg(
    by_zone: Mapping[str, Mapping[str, float]] | None,
) -> dict[str, float]:
    totals: dict[str, float] = {}
    if not isinstance(by_zone, Mapping):
        return totals
    for species_kg in by_zone.values():
        if not isinstance(species_kg, Mapping):
            continue
        for species, kg in species_kg.items():
            amount = float(kg)
            if amount <= 0.0:
                continue
            totals[str(species)] = totals.get(str(species), 0.0) + amount
    return totals


def _oracle_payload_kind(record: Mapping[str, object], key: str) -> str:
    payload = record.get(key)
    if isinstance(payload, Mapping):
        return str(payload.get("kind") or "")
    return ""


def _oracle_transport_refusal(record: Mapping[str, object]) -> bool:
    extra = record.get("extra")
    extra = extra if isinstance(extra, Mapping) else {}
    policy = record.get("policy") or extra.get("policy")
    status = record.get("status") or extra.get("status")
    refusal_type = (
        record.get("refusal_type")
        or extra.get("refusal_type")
        or record.get("refusal_code")
        or extra.get("refusal_code")
    )
    return (
        policy == "fail_loud_missing_transport_parameters"
        and status == "unavailable"
        and bool(refusal_type)
    )


def _oracle_record_blocks_physical_zero(record: Mapping[str, object]) -> bool:
    """Refusal and upper-bound shapes. Written from the interval rule.

    Not from the classifier. A pressure or flux refusal, a diagnostic
    upper bound, and a pressure upper bound are missing input. None of
    them is a zero hour, and a whole-interval certificate does not
    make them one.
    """

    if _oracle_payload_kind(record, "flux") in {
        "diagnostic_upper_bound",
        "refusal",
    }:
        return True
    if _oracle_payload_kind(record, "pressure") in {"refusal", "upper_bound"}:
        return True
    if _oracle_payload_kind(record, "selected_runtime_pressure") == "refusal":
        return True
    if bool(record.get("is_refused", False)):
        return True
    return _oracle_transport_refusal(record)


def _approx_kg(value: float):
    """Supply comparison. Default ``abs=1e-12`` accepts 5e-13 kg as zero."""

    return pytest.approx(value, rel=1e-9, abs=0.0)


def _oracle_channel_blocks_interval_zero(flux: object, species: str) -> bool:
    """A present refusal or upper bound is not covered by the certificate.

    Written from the interval rule. No channel means the certificate
    may still cover the species. A non-mapping channel is not this
    block. A computed rate is handled by the caller before this.
    """

    channels = getattr(flux, "carrier_authority_by_species", None)
    if not isinstance(channels, Mapping) or species not in channels:
        return False
    record = channels[species]
    if not isinstance(record, Mapping):
        return False
    return _oracle_record_blocks_physical_zero(record)


def _oracle_interval_physical_zero(flux: object) -> str:
    """Whole-interval physical zero, written from the interval rule.

    The hour evaporates no species. A species absent from the rate map
    contributes that zero when its channel does not block it. A
    computed rate still wins. This is not the species-specific channel
    record, and it is not an unexplained omission. The field name and
    the evidence token are the rule's.
    """

    record = getattr(flux, "wall_supply_interval_physical_zero", None)
    if not isinstance(record, Mapping):
        return ""
    if str(record.get("evidence") or "") != "authorized_zero":
        return ""
    return str(record.get("reason") or "").strip()


def _oracle_physical_zero_evidence(flux: object, species: str) -> str:
    """Typed physical-zero evidence, or ``""``.

    Authorized zero: ``pressure.kind == zero_by_physics`` and
    ``flux.kind == eligible`` for this species, with no refusal on the
    record. Below-bound zero: ``zero_because == proven_below_threshold``
    and ``doctrine_category == 3`` for this species, with no refusal.
    No record, and an eligible or authoritative channel, are neither.
    """

    channels = getattr(flux, "carrier_authority_by_species", None)
    if not isinstance(channels, Mapping) or species not in channels:
        return ""
    record = channels[species]
    if not isinstance(record, Mapping):
        return ""
    if _oracle_record_blocks_physical_zero(record):
        return ""
    named = str(record.get("species_id") or record.get("species") or "").strip()
    if named != species:
        return ""
    pressure_kind = _oracle_payload_kind(record, "pressure")
    flux_kind = _oracle_payload_kind(record, "flux")
    if pressure_kind == "zero_by_physics" and flux_kind == "eligible":
        return "authorized_zero"
    if str(record.get("zero_because") or "") != "proven_below_threshold":
        return ""
    category = record.get("doctrine_category")
    if isinstance(category, bool) or not isinstance(category, int | float):
        return ""
    if int(category) != 3:
        return ""
    return "below_bound_zero"


def _oracle_positive_bar(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    pressure = float(raw)
    if not math.isfinite(pressure) or pressure <= 0.0:
        return None
    return pressure


def _oracle_transport_pO2_bar(snapshot: object) -> float | None:
    """The pO2 vapour evaluation consumed, written from d-401.

    A record on the flux named ``evaluated_transport_pO2`` with evidence
    ``vapour_evaluation`` is that pressure, including a recorded absence.
    The snapshot oxygen reservoir is a later state and is not read when
    that record is present. A fixture with no record may still name the
    pressure on the reservoir or on ``transport_pO2_bar``,
    ``overhead_pO2_bar``, or ``interval_pO2_bar``. Not melt fO2 and not
    ``p_ref``.
    """

    flux = getattr(snapshot, "evap_flux", None)
    record = getattr(flux, "evaluated_transport_pO2", None)
    if (
        isinstance(record, Mapping)
        and str(record.get("evidence") or "") == "vapour_evaluation"
    ):
        return _oracle_positive_bar(record.get("pressure_bar"))
    reservoir = getattr(snapshot, "oxygen_reservoir", None)
    if isinstance(reservoir, Mapping) and "headspace_transport_pO2_bar" in reservoir:
        recorded = _oracle_positive_bar(reservoir.get("headspace_transport_pO2_bar"))
        if recorded is not None:
            return recorded
    for name in ("transport_pO2_bar", "overhead_pO2_bar", "interval_pO2_bar"):
        recorded = _oracle_positive_bar(getattr(snapshot, name, None))
        if recorded is not None:
            return recorded
    return None


def _oracle_nonnegative(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    value = float(raw)
    if not math.isfinite(value) or value < 0.0:
        return None
    return value


def _oracle_handwritten_hk_kg_hr(
    *,
    pressure_pa: float,
    molar_mass_kg_per_mol: float,
    temperature_K: float,
    area_m2: float,
    alpha: float,
) -> float | None:
    """J = alpha * P * sqrt(M / (2 * pi * R * T)) * area * 3600.

    R is the publisher's 8.314462618 J/(mol K), written here. This does
    not call the publisher coefficient.
    """

    gas_constant = 8.314462618
    if (
        pressure_pa < 0.0
        or molar_mass_kg_per_mol <= 0.0
        or temperature_K <= 0.0
        or area_m2 <= 0.0
        or alpha <= 0.0
        or alpha > 1.0
    ):
        return None
    coefficient = math.sqrt(
        molar_mass_kg_per_mol / (2.0 * math.pi * gas_constant * temperature_K)
    )
    rate = alpha * pressure_pa * coefficient * area_m2 * 3600.0
    if not math.isfinite(rate) or rate < 0.0:
        return None
    return rate


def _oracle_ceiling_kg_hr(
    snapshot: object,
    species: str,
    recorded_pO2_bar: tuple[float, ...],
) -> float | None:
    """Activity-1 Hertz-Knudsen rate, or None when the bound cannot be formed.

    Written from d-401. It does not call the classifier or the catalog
    evaluator. Explicit ``activity_one_psat_pa`` is the ceiling. Otherwise
    ``P = P_unit * (pO2 / p_ref)^e`` at activity 1. ``e == 0`` does not
    need a recorded pO2. ``e != 0`` uses this hour's transport pO2, else
    the run extremum that maximises P, else None. ``p_ref`` is not that
    extremum. ``n < 0`` has no finite activity ceiling.
    """

    raw_temperature = getattr(snapshot, "temperature_C", None)
    if isinstance(raw_temperature, bool) or not isinstance(raw_temperature, int | float):
        return None
    temperature_K = float(raw_temperature) + 273.15
    if not math.isfinite(temperature_K) or temperature_K <= 0.0:
        return None
    flux = getattr(snapshot, "evap_flux", None)
    area_m2 = None
    for raw_area in (
        getattr(flux, "melt_surface_area_m2", None),
        getattr(snapshot, "melt_surface_area_m2", None),
    ):
        if isinstance(raw_area, bool) or not isinstance(raw_area, int | float):
            continue
        candidate = float(raw_area)
        if math.isfinite(candidate) and candidate > 0.0:
            area_m2 = candidate
            break
    if area_m2 is None:
        return None
    channels = getattr(flux, "carrier_authority_by_species", None)
    record = None
    if isinstance(channels, Mapping) and species in channels:
        candidate_record = channels[species]
        if isinstance(candidate_record, Mapping):
            record = candidate_record
    pressure_pa: float | None
    molar_mass: float | None
    if record is not None and "activity_one_psat_pa" in record:
        if record.get("activity_one_psat_pa") is None:
            return None
        pressure_pa = _oracle_nonnegative(record.get("activity_one_psat_pa"))
        if pressure_pa is None:
            return None
    else:
        catalog = getattr(snapshot, "vapour_catalog", None)
        if catalog is None or not hasattr(catalog, "evaluator_for_hot_train"):
            return None
        try:
            law = catalog.evaluator_for_hot_train(species)
        except (ValueError, KeyError, AttributeError, TypeError):
            return None
        try:
            activity_exponent = float(getattr(law, "activity_exponent", 0.0))
            oxygen_exponent = float(getattr(law, "pO2_exponent", 0.0))
            reference_bar = float(getattr(law, "pO2_reference_bar", 1.0))
            unit_pa = float(getattr(law, "p_unit_pa"))
        except (TypeError, ValueError, AttributeError):
            return None
        if activity_exponent < 0.0 or not math.isfinite(unit_pa) or unit_pa < 0.0:
            return None
        if oxygen_exponent == 0.0:
            oxygen_bar = reference_bar
        else:
            hour_pO2 = _oracle_transport_pO2_bar(snapshot)
            if hour_pO2 is not None:
                oxygen_bar = hour_pO2
            elif recorded_pO2_bar:
                oxygen_bar = (
                    min(recorded_pO2_bar)
                    if oxygen_exponent < 0.0
                    else max(recorded_pO2_bar)
                )
            else:
                return None
        if not math.isfinite(oxygen_bar) or oxygen_bar <= 0.0 or reference_bar <= 0.0:
            return None
        pressure_pa = unit_pa * (oxygen_bar / reference_bar) ** oxygen_exponent
        if not math.isfinite(pressure_pa) or pressure_pa < 0.0:
            return None
    if record is not None and "molar_mass_kg_per_mol" in record:
        molar_mass = _oracle_positive_bar(record.get("molar_mass_kg_per_mol"))
    else:
        catalog = getattr(snapshot, "vapour_catalog", None)
        molar_mass = None
        if catalog is not None and hasattr(catalog, "legacy_view"):
            try:
                legacy = catalog.legacy_view()
            except (ValueError, AttributeError, TypeError):
                legacy = None
            oxides = legacy.get("oxide_vapors") if isinstance(legacy, Mapping) else None
            row = oxides.get(species) if isinstance(oxides, Mapping) else None
            grams = row.get("molar_mass_g_mol") if isinstance(row, Mapping) else None
            if not isinstance(grams, bool) and isinstance(grams, int | float):
                molar_mass = float(grams) / 1000.0
                if not math.isfinite(molar_mass) or molar_mass <= 0.0:
                    molar_mass = None
    if molar_mass is None:
        return None
    alpha = 1.0
    if record is not None and "cited_alpha" in record:
        cited = _oracle_positive_bar(record.get("cited_alpha"))
        if cited is None or cited > 1.0:
            return None
        alpha = cited
    return _oracle_handwritten_hk_kg_hr(
        pressure_pa=pressure_pa,
        molar_mass_kg_per_mol=molar_mass,
        temperature_K=temperature_K,
        area_m2=area_m2,
        alpha=alpha,
    )


def _oracle_complete_flux_supply_kg(
    snapshots: tuple[object, ...],
    species_names: tuple[str, ...],
) -> dict[str, float]:
    """Independent supply oracle, written from the interval rule.

    Each positive-duration interval contributes one of: the computed
    rate (any non-negative magnitude, including a rate at or below
    1e-12 kg/h), an evidenced physical zero, the activity-1
    Hertz-Knudsen ceiling, or unavailable. Unavailable drops the
    species. The partial sum of the other intervals is not a supply.
    A missing rate map is not itself unavailable: the ceiling is tried
    first. A zero-duration interval adds nothing and is not a gap. A
    species that never contributes is not a fabricated zero.
    """

    recorded_pO2 = tuple(
        pressure
        for pressure in (
            _oracle_transport_pO2_bar(snapshot) for snapshot in snapshots
        )
        if pressure is not None
    )
    totals = {species: 0.0 for species in species_names}
    observed: set[str] = set()
    withheld: set[str] = set()
    for snapshot in snapshots:
        duration_h = float(getattr(snapshot, "duration_h", 1.0))
        flux = getattr(snapshot, "evap_flux", None)
        published = getattr(flux, "published_species_kg_hr", None)
        rates = published if isinstance(published, Mapping) else getattr(
            flux, "species_kg_hr", None
        )
        for species in species_names:
            if duration_h <= 0.0:
                if isinstance(rates, Mapping) and species in rates:
                    float(rates[species])
                continue
            if isinstance(rates, Mapping) and species in rates:
                totals[species] += float(rates[species]) * duration_h
                observed.add(species)
                continue
            if not _oracle_channel_blocks_interval_zero(flux, species):
                if _oracle_interval_physical_zero(flux):
                    observed.add(species)
                    continue
                if _oracle_physical_zero_evidence(flux, species):
                    observed.add(species)
                    continue
            ceiling = _oracle_ceiling_kg_hr(snapshot, species, recorded_pO2)
            if ceiling is None:
                withheld.add(species)
                continue
            totals[species] += ceiling * duration_h
            observed.add(species)
    return {
        species: totals[species]
        for species in species_names
        if species in observed and species not in withheld
    }


def _wall_bound_view(channel: str, case: Mapping[str, object]) -> dict[str, object]:
    execution, snapshots, deposit = _wall_bound_execution(case)
    if channel == "book":
        authority = objective_module._coating_authority_status(deposit, execution)
        booked = objective_module._book_wall_deposits_at_vapour_supply(
            dict(deposit),
            authority,
            execution,
        )
        entries = _wall_bound_entries(booked["deposit"])
        fields = objective_module._wall_supply_booking_fields(booked)
        return {
            "entries": entries,
            "species_kg": _wall_bound_species_totals(entries),
            "bookings": booked["bookings"],
            "scored": not _wall_bound_refused_names(
                booked["availability_authority"]
            ),
            "refused_species": _wall_bound_refused_names(booked["notice_authority"]),
            "upper_flag": bool(fields.get("wall_deposit_upper_bound_used")),
        }
    if channel == "product_summary":
        summary = objective_module._coating_product_summary(execution)
    elif channel == "lifespan":
        summary = objective_module._furnace_lifespan_cost_summary(
            execution,
            execution.simulator,
        )
    elif channel == "tap":
        summary = objective_module._tap_coating_product_summary(
            execution,
            snapshots,
            max(objective_module._snapshot_hour(snapshot) for snapshot in snapshots),
        )
    else:
        raise AssertionError(channel)
    bookings = tuple(summary.get("wall_supply_bound_bookings") or ())
    authority = summary.get("wall_deposit_sticking_authority")
    if channel == "lifespan":
        cumulative = summary.get("wall_deposit_cumulative_kg_by_species")
        species_kg = (
            None
            if cumulative is None
            else {str(species): float(kg) for species, kg in cumulative.items()}
        )
        entries = None
        refused = None
        scored = cumulative is not None
    else:
        entries = _wall_bound_flatten_segments(
            summary["wall_deposit_kg_by_segment_species"]
        )
        species_kg = _wall_bound_species_totals(entries)
        refused = (
            _wall_bound_refused_names(authority)
            if isinstance(authority, Mapping)
            else set()
        )
        scored = summary.get("campaigns_to_resinter") is not None
    view = {
        "entries": entries,
        "species_kg": species_kg,
        "bookings": bookings,
        "scored": scored,
        "refused_species": refused,
        "upper_flag": bool(summary.get("wall_deposit_upper_bound_used")),
    }
    if "campaigns_to_resinter" in summary:
        view["lifetime"] = summary["campaigns_to_resinter"]
    if "wall_deposit_kg_by_zone_species" in summary:
        view["zone_kg"] = _wall_bound_zone_species_kg(
            summary["wall_deposit_kg_by_zone_species"]
        )
    return view


def _wall_bound_species_totals(
    entries: Mapping[tuple[str, str], float],
) -> dict[str, float]:
    totals: dict[str, float] = {}
    for (_segment, species), kg in entries.items():
        totals[species] = totals.get(species, 0.0) + float(kg)
    return totals


@pytest.mark.parametrize("channel", _WALL_BOUND_CHANNELS)
@pytest.mark.parametrize("case", _WALL_BOUND_CASES, ids=(
    "empty_supply",
    "partial_other_segment",
    "partial_same_segment",
    "split_across_segments",
    "measured_equals_supply",
    "measured_exceeds_supply",
    "neighbour_kept",
    "two_nonnegligible",
    "upper_and_negligible",
    "negligible_clamps_measured",
    "negligible_clamps_every_segment",
    "binding_load_not_name_order",
    "binding_load_tie_smaller_id",
    "off_star_books_best_closed_form_segment",
    "negligible_empty",
    "missing_flux",
    "published_not_committed_negligible",
    "published_not_committed_partial",
    "missing_charge",
    "two_hours_both_observed",
    "mixed_observed_and_missing_flux",
    "missing_interval_blocks_every_species",
    "explicit_zero_rate_is_evidence",
    "omitted_key_on_present_map",
    "two_hour_transport_refusal_is_not_partial_supply",
    "zero_duration_missing_map_is_not_a_gap",
    "supply_above_resolution_below_old_cutoff",
))
def test_refused_wall_deposit_aggregate_follows_the_supply_bound(
    channel: str,
    case: Mapping[str, object],
) -> None:
    execution, snapshots, _deposit = _wall_bound_execution(case)
    refused_names = tuple(dict.fromkeys(str(species) for species, _segment in case["refused"]))
    produced_bounds = _optimizer_refused_flux_bounds(
        snapshots,
        refused_names,
        campaigns_elapsed=1.0,
    )
    oracle_bounds = _oracle_complete_flux_supply_kg(snapshots, refused_names)
    assert set(produced_bounds) == set(oracle_bounds)
    for species, supply_kg in oracle_bounds.items():
        assert produced_bounds[species] == _approx_kg(supply_kg)
    view = _wall_bound_view(channel, case)
    expected_entries = {
        (segment, species): float(kg)
        for (segment, species), kg in case["entries"]
    }
    expected_totals = _wall_bound_species_totals(expected_entries)
    bookings = {item["species"]: item for item in view["bookings"]}
    if view["entries"] is not None:
        assert set(view["entries"]) == set(expected_entries)
        for key, kg in expected_entries.items():
            assert view["entries"][key] == _approx_kg(kg)
    if view["species_kg"] is not None:
        for species, kg in expected_totals.items():
            assert view["species_kg"].get(species, 0.0) == _approx_kg(kg)
        for species, _reason, _booking_kg, _supply in case["species"]:
            assert view["species_kg"].get(species, 0.0) == _approx_kg(
                expected_totals.get(species, 0.0)
            )
    for species, reason, booking_kg, supply in case["species"]:
        booking = bookings.get(species)
        if reason is None:
            assert booking is None
            continue
        assert booking is not None
        assert booking["reason"] == reason
        assert booking["deposit_kg"] == _approx_kg(booking_kg)
        assert booking["supply_upper_bound_kg"] == _approx_kg(supply)
        assert species in booking["refusal_reason"]
        if reason == _WALL_BOUND_UPPER:
            assert booking["flag"] == _WALL_BOUND_UPPER
        priced = None if view["species_kg"] is None else view["species_kg"].get(
            species,
            0.0,
        )
        if priced is not None and supply is not None:
            # deposit_wall,i <= supply_i, and the scored mass is the booking.
            assert priced <= float(supply) + 1.0e-12
            assert priced == _approx_kg(float(booking_kg))
            if case["charge"] is not None:
                resolution = objective_module._mass_balance_ledger_resolution_kg(
                    float(case["charge"])
                )
                if float(supply) <= resolution:
                    assert priced == _approx_kg(0.0)
                else:
                    assert priced == _approx_kg(float(supply))
        if view.get("zone_kg") is not None and booking_kg is not None:
            assert view["zone_kg"].get(species, 0.0) == _approx_kg(
                float(booking_kg)
            )
        if (
            "lifetime" in view
            and view["scored"]
            and booking_kg is not None
            and float(booking_kg) > 0.0
        ):
            assert view["lifetime"] not in (None, "infinite")
    expects_upper = any(
        reason == _WALL_BOUND_UPPER
        for _species, reason, _booking_kg, _supply in case["species"]
    )
    assert view["upper_flag"] is expects_upper
    expects_scored = all(
        reason is not None for _species, reason, _booking_kg, _supply in case["species"]
    )
    assert view["scored"] is expects_scored
    if view["refused_species"] is not None:
        for species, _segment in case["refused"]:
            assert species in view["refused_species"]


def _sio_interval_authority(kind: str) -> object:
    """One carrier-authority shape for an omitted SiO interval."""

    species = "SiO"
    eligible_flux = {
        "kind": "eligible",
        "alpha_ref": "alpha:SiO",
        "reaction_id": "cao",
    }
    if kind == "proven_zero":
        return {
            "species_id": species,
            "pressure": {"kind": "zero_by_physics", "evidence_ref": "no liquid"},
            "flux": eligible_flux,
        }
    if kind == "eligible":
        return {
            "species_id": species,
            "pressure": {"kind": "value", "pa": 1.0},
            "flux": eligible_flux,
        }
    if kind == "authoritative":
        return {
            "species_id": species,
            "pressure": {"kind": "value", "pa": 1.0},
            "flux": eligible_flux,
            "verdict_status": "authoritative",
            "certification_ceiling": "measured",
            "validation_status": "validated",
            "is_flux_active": True,
        }
    if kind == "transport":
        return _transport_refusal_channel(species)
    if kind == "is_refused_flag":
        return {
            "species_id": species,
            "pressure": {"kind": "value", "pa": 1.0},
            "flux": eligible_flux,
            "is_refused": True,
        }
    if kind == "flux_refusal":
        return {
            "species_id": species,
            "pressure": {"kind": "value", "pa": 1.0},
            "flux": {"kind": "refusal", "code": "x", "detail": "flux refused"},
            "is_refused": False,
        }
    if kind == "pressure_refusal":
        return {
            "species_id": species,
            "pressure": {"kind": "refusal", "code": "x", "detail": "pressure refused"},
            "flux": eligible_flux,
        }
    if kind == "runtime_refusal":
        return {
            "species_id": species,
            "pressure": {"kind": "value", "pa": 1.0},
            "selected_runtime_pressure": {
                "kind": "refusal",
                "code": "x",
                "detail": "runtime pressure refused",
            },
            "flux": eligible_flux,
        }
    if kind == "transport_extra":
        return {
            "species_id": species,
            "pressure": {"kind": "value", "pa": 1.0},
            "flux": eligible_flux,
            "extra": {
                "policy": "fail_loud_missing_transport_parameters",
                "status": "unavailable",
                "refusal_type": "EvaporationFluxConfigurationError",
            },
        }
    if kind == "authority_missing":
        return {"species_id": species, "authority_status": "missing"}
    if kind == "empty_record":
        return {}
    if kind == "non_mapping":
        return "refused"
    if kind == "species_mismatch":
        return {
            "species_id": "Mg",
            "pressure": {"kind": "value", "pa": 1.0},
            "flux": {
                "kind": "eligible",
                "alpha_ref": "alpha:Mg",
                "reaction_id": "mgo",
            },
        }
    if kind == "diagnostic":
        return {
            "species_id": species,
            "pressure": {
                "kind": "upper_bound",
                "pa": 1.0,
                "evidence_ref": "diagnostic alpha",
            },
            "flux": {
                "kind": "diagnostic_upper_bound",
                "alpha_ref": "alpha:SiO",
                "reaction_id": "cao",
            },
        }
    raise AssertionError(kind)


def _interval_flux_snapshot(
    hour: int,
    duration_h: float,
    rates: Mapping[str, float],
    records: Mapping[str, object] | None = None,
    *,
    committed: Mapping[str, float] | None = None,
    publish: bool = True,
    unavailable: bool = False,
    interval_physical_zero: str | None = None,
) -> SimpleNamespace:
    if unavailable:
        return SimpleNamespace(hour=hour, duration_h=duration_h, evap_flux=None)
    fields: dict[str, object] = {}
    if publish:
        fields["published_species_kg_hr"] = {
            species: float(rate) for species, rate in rates.items()
        }
        source = rates if committed is None else committed
        fields["species_kg_hr"] = {
            species: float(rate) for species, rate in source.items()
        }
    else:
        fields["species_kg_hr"] = {
            species: float(rate) for species, rate in rates.items()
        }
    if records:
        fields["carrier_authority_by_species"] = dict(records)
    if interval_physical_zero:
        fields["wall_supply_interval_physical_zero"] = {
            "reason": interval_physical_zero,
            "evidence": "authorized_zero",
        }
    return SimpleNamespace(
        hour=hour,
        duration_h=duration_h,
        evap_flux=SimpleNamespace(**fields),
    )


# Invariant: a priced supply sums a computed rate or an evidenced physical
# zero over every positive-duration hour. Each row is one interval class
# for SiO on hour 2. Hour 1 publishes SiO at 1 kg/h and Mg at 0.5 kg/h.
# Hour 2 publishes Mg at 0.4 kg/h unless the row says otherwise. None means
# that species' bound is unavailable. An omitted SiO with no typed
# physical-zero record is unavailable: the 1 kg hour is not the supply.
_FLUX_INTERVAL_CASES = (
    ("computed_positive", "rate_positive", 1.25, 0.9),
    ("explicit_zero", "explicit_zero", 1.0, 0.9),
    ("omitted_no_authority", "omitted", None, 0.9),
    ("omitted_proven_zero", "proven_zero", 1.0, 0.9),
    # Eligible or authoritative, and absent from the map: not a typed
    # authorized zero or below-bound zero. A computed rate at or below
    # 1e-12 kg/h is not this row. The generative supply test keeps that rate.
    ("omitted_eligible_no_computed_rate", "eligible", None, 0.9),
    ("omitted_authoritative_no_computed_rate", "authoritative", None, 0.9),
    ("omitted_transport_refusal", "transport", None, 0.9),
    ("omitted_is_refused_flag", "is_refused_flag", None, 0.9),
    ("omitted_flux_kind_refusal", "flux_refusal", None, 0.9),
    ("omitted_pressure_kind_refusal", "pressure_refusal", None, 0.9),
    ("omitted_runtime_pressure_refusal", "runtime_refusal", None, 0.9),
    ("omitted_transport_extra_only", "transport_extra", None, 0.9),
    ("omitted_authority_missing", "authority_missing", None, 0.9),
    ("omitted_empty_record", "empty_record", None, 0.9),
    ("omitted_non_mapping_record", "non_mapping", None, 0.9),
    ("omitted_species_id_mismatch", "species_mismatch", None, 0.9),
    ("omitted_diagnostic_upper_bound", "diagnostic", None, 0.9),
    ("published_rate_wins_over_colocated_refusal", "rate_and_refusal", 1.25, 0.9),
    ("zero_duration_refusal_is_not_a_gap", "zero_duration_refusal", 1.0, 0.5),
    ("missing_map_blocks_every_species", "missing_map", None, None),
    ("committed_rate_under_published_refusal", "committed_hidden", None, 0.9),
    ("fallback_map_transport_refusal", "fallback_refusal", None, 0.9),
    ("opposite_hours_each_species_refused", "opposite", None, None),
)


def _flux_interval_snapshots(kind: str) -> tuple[SimpleNamespace, ...]:
    hour_1 = _interval_flux_snapshot(1, 1.0, {"SiO": 1.0, "Mg": 0.5})
    if kind == "rate_positive":
        return (
            hour_1,
            _interval_flux_snapshot(2, 1.0, {"SiO": 0.25, "Mg": 0.4}),
        )
    if kind == "explicit_zero":
        return (
            hour_1,
            _interval_flux_snapshot(2, 1.0, {"SiO": 0.0, "Mg": 0.4}),
        )
    if kind == "omitted":
        return (hour_1, _interval_flux_snapshot(2, 1.0, {"Mg": 0.4}))
    if kind == "rate_and_refusal":
        return (
            hour_1,
            _interval_flux_snapshot(
                2,
                1.0,
                {"SiO": 0.25, "Mg": 0.4},
                {"SiO": _sio_interval_authority("transport")},
            ),
        )
    if kind == "zero_duration_refusal":
        return (
            hour_1,
            _interval_flux_snapshot(
                2,
                0.0,
                {"Mg": 0.4},
                {"SiO": _sio_interval_authority("transport")},
            ),
        )
    if kind == "missing_map":
        return (
            hour_1,
            _interval_flux_snapshot(2, 1.0, {}, unavailable=True),
        )
    if kind == "committed_hidden":
        return (
            hour_1,
            _interval_flux_snapshot(
                2,
                1.0,
                {"Mg": 0.4},
                {"SiO": _sio_interval_authority("transport")},
                committed={"SiO": 5.0, "Mg": 0.4},
            ),
        )
    if kind == "fallback_refusal":
        return (
            hour_1,
            _interval_flux_snapshot(
                2,
                1.0,
                {"Mg": 0.4},
                {"SiO": _sio_interval_authority("transport")},
                publish=False,
            ),
        )
    if kind == "opposite":
        return (
            _interval_flux_snapshot(
                1,
                1.0,
                {"SiO": 1.0},
                {"Mg": _transport_refusal_channel("Mg")},
            ),
            _interval_flux_snapshot(
                2,
                1.0,
                {"Mg": 0.4},
                {"SiO": _transport_refusal_channel("SiO")},
            ),
        )
    return (
        hour_1,
        _interval_flux_snapshot(
            2,
            1.0,
            {"Mg": 0.4},
            {"SiO": _sio_interval_authority(kind)},
        ),
    )


def _book_interval_window(snapshots: tuple[SimpleNamespace, ...]) -> dict[str, object]:
    deposit = {("cold_wall", "SiO"): 0.1, ("cold_wall", "Mg"): 0.2}
    authority = {
        "wall_saturation_pressure_refusals_by_species": {
            species: {
                "hot_wall": {
                    "status": "refused",
                    "output_status": "status_bearing",
                    "reason": f"antoine domain refused for {species}",
                }
            }
            for species in ("SiO", "Mg")
        }
    }
    sim = _FakeProductSim()
    sim.record.batch_mass_kg = 1000.0
    execution = SimpleNamespace(
        simulator=sim,
        snapshots=snapshots,
        trace=SimpleNamespace(
            wall_deposit_by_segment_species_kg=dict(deposit),
            wall_zone_by_segment={"cold_wall": "Cold", "hot_wall": "Hot"},
            wall_deposit_sticking_authority=authority,
            snapshots=snapshots,
        ),
    )
    status = objective_module._coating_authority_status(deposit, execution)
    return objective_module._book_wall_deposits_at_vapour_supply(
        dict(deposit),
        status,
        execution,
    )


@pytest.mark.parametrize("species_order", (("SiO", "Mg"), ("Mg", "SiO")))
@pytest.mark.parametrize(
    ("case_id", "kind", "expect_sio", "expect_mg"),
    _FLUX_INTERVAL_CASES,
    ids=[case[0] for case in _FLUX_INTERVAL_CASES],
)
def test_vapour_supply_integral_classifies_every_flux_interval(
    case_id: str,
    kind: str,
    expect_sio: float | None,
    expect_mg: float | None,
    species_order: tuple[str, str],
) -> None:
    """d-367 at the supply chokepoint: no omitted interval becomes a partial sum.

    ``_optimizer_refused_flux_bounds`` is the only integral. Product
    summary, furnace lifespan, and the tap projection book whatever it
    returns. A species with an unavailable hour stays out, in either
    species order, unless that hour is a computed rate or a typed
    physical zero. Mg's complete integral is not cleared by SiO's gap,
    and a missing map clears both.
    """

    snapshots = _flux_interval_snapshots(kind)
    bounds = _optimizer_refused_flux_bounds(
        snapshots,
        species_order,
        campaigns_elapsed=1.0,
    )
    expected = {
        species: supply
        for species, supply in (("SiO", expect_sio), ("Mg", expect_mg))
        if supply is not None
    }
    assert set(bounds) == set(expected), case_id
    for species, supply in expected.items():
        assert bounds[species] == pytest.approx(supply)
    oracle = _oracle_complete_flux_supply_kg(snapshots, species_order)
    assert set(oracle) == set(expected)
    for species, supply in expected.items():
        assert oracle[species] == pytest.approx(supply)

    booked = _book_interval_window(snapshots)
    bookings = {
        item["species"]: item
        for item in booked["bookings"]
        if isinstance(item, Mapping)
    }
    deposit = booked["deposit"]
    measured = {"SiO": 0.1, "Mg": 0.2}
    availability = _wall_bound_refused_names(booked["availability_authority"])
    notice = _wall_bound_refused_names(booked["notice_authority"])
    assert notice == {"SiO", "Mg"}
    for species, supply in (("SiO", expect_sio), ("Mg", expect_mg)):
        if supply is None:
            assert species not in bookings
            assert deposit.get(("cold_wall", species)) == pytest.approx(
                measured[species]
            )
            assert species in availability
            continue
        assert species not in availability
        booking = bookings[species]
        assert booking["reason"] == _WALL_BOUND_UPPER
        assert booking["deposit_kg"] == pytest.approx(supply)
        assert booking["supply_upper_bound_kg"] == pytest.approx(supply)
        priced = sum(
            float(kg)
            for key, kg in deposit.items()
            if isinstance(key, tuple) and len(key) == 2 and str(key[1]) == species
        )
        assert priced == pytest.approx(supply)
        assert priced <= float(supply) + 1.0e-12


# Publisher rate classes. The supply oracle is rate * liquid fraction.
# It does not apply the 1e-12 kg/h debit floor. "both" names the same
# rate in the published bucket and the below-threshold bucket; the
# supply counts it once. The species list is the wall-refusal set this
# ticket books, plus the carriers the debit floor already pins.
_COMPUTED_RATE_GRID = (0.0, 5.0e-13, 1.0e-12, 2.0e-12, 1.0)
_COMPUTED_BUCKETS = ("published", "below", "both")
_COMPUTED_LIQUID_FRACTIONS = (1.0, 0.25)
_COMPUTED_SPECIES = (
    "SiO",
    "Mg",
    "Fe",
    "CrO2",
    "Al2O",
    "Al2O2",
    "AlO",
    "CaO_gas",
    "CrO",
    "K2",
    "K2O_gas",
    "MgO_gas",
    "Na2",
    "Na2O_gas",
    "SiO2_gas",
    "TiO",
    "TiO2_gas",
)
_DEBIT_FLOOR_KG_HR = 1.0e-12


def _publisher_diagnostic(
    rate: float,
    bucket: str,
    species: str = "SiO",
) -> dict[str, object]:
    published: dict[str, float] = {}
    below: dict[str, float] = {}
    if bucket in ("published", "both"):
        published[species] = float(rate)
    if bucket in ("below", "both"):
        below[species] = float(rate)
    diagnostic: dict[str, object] = {"evaporation_flux_kg_hr": published}
    if below:
        diagnostic["below_threshold_evaporation_flux"] = {
            "threshold_kg_hr": _DEBIT_FLOOR_KG_HR,
            "fluxes_kg_hr": below,
        }
    return diagnostic


def _independent_supply_rate(rate: float, liquid: float) -> float:
    """Gated publisher rate. The debit floor is not an operand."""

    return float(rate) * float(liquid)


def _independent_debit_rate(rate: float, bucket: str, liquid: float) -> float | None:
    """Melt debit. The below-threshold bucket never debits.

    A published rate debits only after the liquid-fraction gate, and
    only above the historical floor. That floor is the debit rule, not
    the supply rule.
    """

    if bucket == "below":
        return None
    gated = _independent_supply_rate(rate, liquid)
    if gated > _DEBIT_FLOOR_KG_HR:
        return gated
    return None


@pytest.mark.parametrize("species", _COMPUTED_SPECIES)
@pytest.mark.parametrize("liquid", _COMPUTED_LIQUID_FRACTIONS)
@pytest.mark.parametrize("bucket", _COMPUTED_BUCKETS)
@pytest.mark.parametrize("rate", _COMPUTED_RATE_GRID)
def test_publisher_projection_keeps_every_computed_rate(
    rate: float,
    bucket: str,
    liquid: float,
    species: str,
) -> None:
    """Every computed vapour rate reaches the supply integral.

    The grid is the publisher's two buckets, the debit floor, a liquid
    fraction that can move a published rate across that floor, and each
    species the wall bound can book. The supply oracle is the gated
    rate. It does not drop a rate for being at or below 1e-12 kg/h.
    The debit oracle still does.
    """

    projected = project_publisher_vapour_rates(
        _publisher_diagnostic(rate, bucket, species),
        liquid_fraction_factor=liquid,
    )
    expected_supply = _independent_supply_rate(rate, liquid)
    expected_debit = _independent_debit_rate(rate, bucket, liquid)
    assert projected.supply_kg_hr[species] == _approx_kg(expected_supply)
    assert set(projected.supply_kg_hr) == {species}
    if expected_debit is None:
        assert species not in projected.debit_kg_hr
    else:
        assert projected.debit_kg_hr[species] == _approx_kg(expected_debit)
        assert set(projected.debit_kg_hr) == {species}
    # Both buckets naming one rate must not sum it.
    assert projected.supply_kg_hr[species] == _approx_kg(expected_supply)

    flux = SimpleNamespace(species_kg_hr=dict(projected.debit_kg_hr))
    publish_wall_supply_on_flux(
        flux,
        projected.supply_kg_hr,
        debit_kg_hr=projected.debit_kg_hr,
    )
    assert flux.species_kg_hr == projected.debit_kg_hr
    assert flux.published_species_kg_hr[species] == _approx_kg(expected_supply)

    bounds = _optimizer_refused_flux_bounds(
        (
            SimpleNamespace(
                hour=1,
                duration_h=1.0,
                evap_flux=flux,
            ),
        ),
        (species,),
        campaigns_elapsed=1.0,
    )
    assert bounds[species] == _approx_kg(expected_supply)

    if expected_supply <= _DEBIT_FLOOR_KG_HR:
        # Mutation: the old publication gate. Dropping the sub-floor
        # rate removes the species, so the integral returns no supply.
        mutated = SimpleNamespace(
            species_kg_hr={
                name: kg
                for name, kg in flux.published_species_kg_hr.items()
                if float(kg) > _DEBIT_FLOOR_KG_HR
            },
            published_species_kg_hr={
                name: kg
                for name, kg in flux.published_species_kg_hr.items()
                if float(kg) > _DEBIT_FLOOR_KG_HR
            },
        )
        mutated_bounds = _optimizer_refused_flux_bounds(
            (SimpleNamespace(hour=1, duration_h=1.0, evap_flux=mutated),),
            (species,),
            campaigns_elapsed=1.0,
        )
        assert species not in mutated_bounds


def test_mixed_subthreshold_hours_book_the_full_integral() -> None:
    """1 kg charge, resolution 5e-14 kg. Hour one 2e-12, hour two 5e-13.

    The debit floor keeps hour one and drops hour two. The supply
    integral is 2.5e-12 kg, which is above ledger resolution, so the
    refused species is booked at that supply and stays scored. A map
    that keeps only the above-floor hour books 2e-12 kg.
    """

    hour_one = project_publisher_vapour_rates(
        {"evaporation_flux_kg_hr": {"SiO": 2.0e-12}},
        liquid_fraction_factor=1.0,
    )
    hour_two = project_publisher_vapour_rates(
        {
            "evaporation_flux_kg_hr": {},
            "below_threshold_evaporation_flux": {
                "threshold_kg_hr": _DEBIT_FLOOR_KG_HR,
                "fluxes_kg_hr": {"SiO": 5.0e-13},
            },
        },
        liquid_fraction_factor=1.0,
    )
    assert hour_one.debit_kg_hr["SiO"] == pytest.approx(2.0e-12)
    assert "SiO" not in hour_two.debit_kg_hr
    assert hour_two.supply_kg_hr["SiO"] == pytest.approx(5.0e-13)

    def _flux(projected):
        flux = SimpleNamespace(species_kg_hr=dict(projected.debit_kg_hr))
        publish_wall_supply_on_flux(
            flux,
            projected.supply_kg_hr,
            debit_kg_hr=projected.debit_kg_hr,
        )
        return flux

    snapshots = (
        SimpleNamespace(hour=1, duration_h=1.0, evap_flux=_flux(hour_one)),
        SimpleNamespace(hour=2, duration_h=1.0, evap_flux=_flux(hour_two)),
    )
    # Independent of either map: the two computed rates, no floor.
    oracle = 2.0e-12 + 5.0e-13
    for order in (("SiO",), ("SiO", "Mg")):
        bounds = _optimizer_refused_flux_bounds(
            snapshots,
            order,
            campaigns_elapsed=1.0,
        )
        assert bounds["SiO"] == pytest.approx(oracle)
        assert "Mg" not in bounds

    resolution = objective_module._mass_balance_ledger_resolution_kg(1.0)
    assert resolution == pytest.approx(5.0e-14)
    assert oracle > resolution
    execution, _snapshots, deposit = _wall_bound_execution({
        "deposit": (),
        "flux_snapshots": (
            {
                "hour": 1,
                "duration_h": 1.0,
                "flux": tuple(hour_one.debit_kg_hr.items()),
                "published_flux": tuple(hour_one.supply_kg_hr.items()),
            },
            {
                "hour": 2,
                "duration_h": 1.0,
                "flux": (),
                "published_flux": tuple(hour_two.supply_kg_hr.items()),
            },
        ),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1.0,
    })
    booked = objective_module._book_wall_deposits_at_vapour_supply(
        dict(deposit),
        objective_module._coating_authority_status(deposit, execution),
        execution,
    )
    booking = booked["bookings"][0]
    assert booking["species"] == "SiO"
    assert booking["reason"] == _WALL_BOUND_UPPER
    assert booking["deposit_kg"] == pytest.approx(oracle)
    assert booking["supply_upper_bound_kg"] == pytest.approx(oracle)
    assert _wall_bound_refused_names(booked["availability_authority"]) == set()
    assert _wall_bound_refused_names(booked["notice_authority"]) == {"SiO"}

    # Mutation: delete the sub-floor rate and leave no physical-zero
    # record. The rejected reading keeps hour one's 2e-12 kg. That
    # partial sum is not the supply. The bound is unavailable.
    mutated_snapshots = (
        snapshots[0],
        SimpleNamespace(
            hour=2,
            duration_h=1.0,
            evap_flux=SimpleNamespace(
                species_kg_hr={},
                published_species_kg_hr={},
            ),
        ),
    )
    mutated = _optimizer_refused_flux_bounds(
        mutated_snapshots,
        ("SiO",),
        campaigns_elapsed=1.0,
    )
    rejected_partial_kg = 2.0e-12
    assert "SiO" not in mutated
    assert rejected_partial_kg != oracle
    assert _oracle_complete_flux_supply_kg(mutated_snapshots, ("SiO",)) == {}


@pytest.mark.parametrize("order", (("Mg", "SiO"), ("SiO", "Mg")))
def test_subthreshold_supply_does_not_depend_on_species_order(order: tuple[str, str]) -> None:
    diagnostic = {
        "evaporation_flux_kg_hr": {"SiO": 2.0e-12},
        "below_threshold_evaporation_flux": {
            "threshold_kg_hr": _DEBIT_FLOOR_KG_HR,
            "fluxes_kg_hr": {"Mg": 5.0e-13},
        },
    }
    projected = project_publisher_vapour_rates(
        diagnostic,
        liquid_fraction_factor=1.0,
    )
    flux = SimpleNamespace(species_kg_hr=dict(projected.debit_kg_hr))
    publish_wall_supply_on_flux(
        flux,
        projected.supply_kg_hr,
        debit_kg_hr=projected.debit_kg_hr,
    )
    bounds = _optimizer_refused_flux_bounds(
        (SimpleNamespace(hour=1, duration_h=1.0, evap_flux=flux),),
        order,
        campaigns_elapsed=1.0,
    )
    assert bounds["SiO"] == pytest.approx(2.0e-12)
    assert bounds["Mg"] == pytest.approx(5.0e-13)
    assert list(bounds) == [species for species in order if species in bounds]


def test_all_subthreshold_hours_still_return_a_supply_bound() -> None:
    projected = project_publisher_vapour_rates(
        {
            "evaporation_flux_kg_hr": {},
            "below_threshold_evaporation_flux": {
                "threshold_kg_hr": _DEBIT_FLOOR_KG_HR,
                "fluxes_kg_hr": {"SiO": 5.0e-13},
            },
        },
        liquid_fraction_factor=1.0,
    )
    flux = SimpleNamespace(species_kg_hr={})
    publish_wall_supply_on_flux(flux, projected.supply_kg_hr)
    bounds = _optimizer_refused_flux_bounds(
        (
            SimpleNamespace(hour=1, duration_h=1.0, evap_flux=flux),
            SimpleNamespace(hour=2, duration_h=1.0, evap_flux=flux),
        ),
        ("SiO",),
        campaigns_elapsed=1.0,
    )
    assert bounds["SiO"] == _approx_kg(1.0e-12)
    assert bounds["SiO"] > objective_module._mass_balance_ledger_resolution_kg(1.0)


# Generative wall-supply intervals. The oracle reads snapshot bytes.
# This table is the same rule applied to the kind names, so a shared
# mistake in the snapshot builder cannot agree with itself.
_WALL_SUPPLY_INTERVAL_KINDS = (
    "computed_above_threshold",
    "computed_below_threshold",
    "authorized_zero",
    "below_bound_zero",
    "interval_physical_zero",
    "interval_physical_zero_with_channel",
    "interval_physical_zero_refused",
    "interval_physical_zero_upper_bound",
    "refused",
    "absent_no_record",
    "absent_flux_map",
    "absent_map_explicit_ceiling",
    "catalog_m0_no_recorded_po2",
    "catalog_oxygen_no_recorded_po2",
)
# Handwritten ceiling operands. R is the publisher gas constant written
# out, not a call to the Hertz-Knudsen helper. The two pressures differ
# so an explicit P_sat and an oxygen-free catalog law cannot pass by
# sharing one number.
_CEILING_T_K = 1500.0
_CEILING_T_C = _CEILING_T_K - 273.15
_CEILING_AREA_M2 = 0.2
_CEILING_MASS_KG_PER_MOL = 0.070
_EXPLICIT_CEILING_PA = 1.0e-3
_M0_P_UNIT_PA = 2.0e-3


def _handwritten_ceiling_kg_hr(pressure_pa: float) -> float:
    coefficient = math.sqrt(
        _CEILING_MASS_KG_PER_MOL / (2.0 * math.pi * 8.314462618 * _CEILING_T_K)
    )
    return pressure_pa * coefficient * _CEILING_AREA_M2 * 3600.0


_EXPLICIT_CEILING_KG_HR = _handwritten_ceiling_kg_hr(_EXPLICIT_CEILING_PA)
_M0_CEILING_KG_HR = _handwritten_ceiling_kg_hr(_M0_P_UNIT_PA)


class _CeilingLaw:
    """P = P_unit * a^n * (pO2 / p_ref)^e. The oracle does not call this."""

    def __init__(
        self,
        *,
        activity_exponent: float,
        pO2_exponent: float,
        pO2_reference_bar: float,
        p_unit_pa: float,
    ) -> None:
        self.activity_exponent = activity_exponent
        self.pO2_exponent = pO2_exponent
        self.pO2_reference_bar = pO2_reference_bar
        self.p_unit_pa = p_unit_pa

    def evaluate(self, temperature_K: float, *, source_activity: float, pO2_bar: float):
        del temperature_K
        pressure = (
            self.p_unit_pa
            * (float(source_activity) ** self.activity_exponent)
            * (float(pO2_bar) / self.pO2_reference_bar) ** self.pO2_exponent
        )
        return SimpleNamespace(pressure_pa=pressure)


class _CeilingCatalog:
    def __init__(
        self,
        laws: Mapping[str, _CeilingLaw],
        masses_g_per_mol: Mapping[str, float],
    ) -> None:
        self._laws = dict(laws)
        self._masses = dict(masses_g_per_mol)

    def evaluator_for_hot_train(self, species: str) -> _CeilingLaw:
        try:
            return self._laws[species]
        except KeyError as exc:
            raise ValueError(species) from exc

    def legacy_view(self) -> dict[str, dict[str, dict[str, float]]]:
        return {
            "oxide_vapors": {
                species: {"molar_mass_g_mol": grams}
                for species, grams in self._masses.items()
            }
        }


def _species_ceiling_catalog(species: str, oxygen_exponent: float, p_unit_pa: float) -> _CeilingCatalog:
    return _CeilingCatalog(
        {
            species: _CeilingLaw(
                activity_exponent=1.0,
                pO2_exponent=oxygen_exponent,
                pO2_reference_bar=1.0,
                p_unit_pa=p_unit_pa,
            )
        },
        {species: _CEILING_MASS_KG_PER_MOL * 1000.0},
    )
_WALL_SUPPLY_ABOVE_KG_HR = 1.0
_WALL_SUPPLY_BELOW_KG_HR = 5.0e-13
_WALL_SUPPLY_SIBLING_KG_HR = 0.4
_WALL_SUPPLY_WINDOWS = (
    ("computed_above_threshold",),
    ("computed_below_threshold",),
    ("authorized_zero",),
    ("below_bound_zero",),
    ("interval_physical_zero",),
    ("interval_physical_zero_with_channel",),
    ("refused",),
    ("absent_no_record",),
    ("absent_flux_map",),
    ("computed_above_threshold", "absent_no_record"),
    ("computed_above_threshold", "interval_physical_zero"),
    ("interval_physical_zero_with_channel", "computed_above_threshold"),
    ("computed_below_threshold", "interval_physical_zero"),
    ("interval_physical_zero", "absent_no_record"),
    ("interval_physical_zero", "refused"),
    ("interval_physical_zero_refused",),
    ("interval_physical_zero_upper_bound",),
    ("computed_above_threshold", "interval_physical_zero_refused"),
    ("interval_physical_zero_upper_bound", "computed_below_threshold"),
    ("interval_physical_zero_refused", "authorized_zero"),
    ("computed_above_threshold", "computed_below_threshold"),
    ("computed_below_threshold", "authorized_zero"),
    ("computed_below_threshold", "below_bound_zero"),
    ("authorized_zero", "below_bound_zero"),
    ("authorized_zero", "refused"),
    ("below_bound_zero", "absent_flux_map"),
    ("refused", "computed_above_threshold"),
    ("absent_no_record", "computed_above_threshold"),
    ("absent_flux_map", "computed_below_threshold"),
    ("absent_map_explicit_ceiling",),
    ("catalog_m0_no_recorded_po2",),
    ("catalog_oxygen_no_recorded_po2",),
    ("computed_above_threshold", "absent_map_explicit_ceiling"),
    ("computed_above_threshold", "catalog_m0_no_recorded_po2"),
    ("computed_above_threshold", "catalog_oxygen_no_recorded_po2"),
    ("computed_above_threshold", "authorized_zero", "absent_no_record"),
    ("computed_below_threshold", "below_bound_zero", "computed_above_threshold"),
    ("absent_no_record", "authorized_zero", "computed_below_threshold"),
    ("refused", "below_bound_zero", "computed_above_threshold"),
)


def _supply_sibling(species: str) -> str:
    return "Mg" if species != "Mg" else "SiO"


def _authorized_zero_record(species: str) -> dict[str, object]:
    return {
        "species_id": species,
        "pressure": {"kind": "zero_by_physics", "evidence_ref": "no liquid"},
        "flux": {
            "kind": "eligible",
            "alpha_ref": f"alpha:{species}",
            "reaction_id": "test",
        },
    }


def _eligible_channel_record(species: str) -> dict[str, object]:
    return {
        "species_id": species,
        "pressure": {"kind": "value", "pa": 1.0},
        "flux": {
            "kind": "eligible",
            "alpha_ref": f"alpha:{species}",
            "reaction_id": "test",
        },
    }


def _below_bound_zero_record(species: str) -> dict[str, object]:
    return {
        "species_id": species,
        "species": species,
        "zero_because": "proven_below_threshold",
        "doctrine_category": 3,
        "field": "P_effective_Pa",
    }


def _supply_kind_snapshot(
    hour: int,
    species: str,
    sibling: str,
    kind: str,
) -> SimpleNamespace:
    if kind == "absent_flux_map":
        return SimpleNamespace(hour=hour, duration_h=1.0, evap_flux=None)
    rates: dict[str, float] = {sibling: _WALL_SUPPLY_SIBLING_KG_HR}
    records: dict[str, object] = {}
    interval_zero: str | None = None
    if kind == "computed_above_threshold":
        rates[species] = _WALL_SUPPLY_ABOVE_KG_HR
    elif kind == "computed_below_threshold":
        rates[species] = _WALL_SUPPLY_BELOW_KG_HR
    elif kind == "explicit_zero":
        rates[species] = 0.0
    elif kind == "authorized_zero":
        records[species] = _authorized_zero_record(species)
    elif kind == "below_bound_zero":
        records[species] = _below_bound_zero_record(species)
    elif kind == "interval_physical_zero":
        # C5 / zero-liquid shape: empty of this species, no channel.
        interval_zero = "non_evaporation_campaign"
    elif kind == "interval_physical_zero_with_channel":
        # Frozen hour: the batch channel was copied, then the gate
        # discarded the rate. The interval record still covers it.
        records[species] = _eligible_channel_record(species)
        interval_zero = "frozen_before_evaporation"
    elif kind == "interval_physical_zero_refused":
        # PressureValue fills batch_pa. FluxRefusal produces no flux.
        # A whole-interval certificate must not turn that into a zero.
        records[species] = {
            "species_id": species,
            "pressure": {"kind": "value", "pa": 1.0},
            "flux": {
                "kind": "refusal",
                "code": "flux_refused",
                "detail": "refused",
            },
        }
        interval_zero = "frozen_before_evaporation"
    elif kind == "interval_physical_zero_upper_bound":
        # Pressure upper bound below 1050 C is not a physical zero,
        # with or without a whole-interval certificate.
        records[species] = {
            "species_id": species,
            "pressure": {
                "kind": "upper_bound",
                "pa": 1.0,
                "evidence_ref": "diagnostic alpha",
            },
            "flux": {
                "kind": "eligible",
                "alpha_ref": f"alpha:{species}",
                "reaction_id": "test",
            },
        }
        interval_zero = "frozen_before_evaporation"
    elif kind == "refused":
        records[species] = _transport_refusal_channel(species)
    elif kind == "absent_no_record":
        pass
    elif kind == "absent_map_explicit_ceiling":
        record = _transport_refusal_channel(species)
        record["activity_one_psat_pa"] = _EXPLICIT_CEILING_PA
        record["molar_mass_kg_per_mol"] = _CEILING_MASS_KG_PER_MOL
        return SimpleNamespace(
            hour=hour,
            duration_h=1.0,
            temperature_C=_CEILING_T_C,
            evap_flux=SimpleNamespace(
                carrier_authority_by_species={species: record},
                melt_surface_area_m2=_CEILING_AREA_M2,
            ),
        )
    elif kind in {"catalog_m0_no_recorded_po2", "catalog_oxygen_no_recorded_po2"}:
        oxygen_exponent = 0.0 if kind == "catalog_m0_no_recorded_po2" else -1.0
        return SimpleNamespace(
            hour=hour,
            duration_h=1.0,
            temperature_C=_CEILING_T_C,
            vapour_catalog=_species_ceiling_catalog(
                species,
                oxygen_exponent,
                _M0_P_UNIT_PA,
            ),
            evap_flux=SimpleNamespace(
                published_species_kg_hr={sibling: _WALL_SUPPLY_SIBLING_KG_HR},
                species_kg_hr={sibling: _WALL_SUPPLY_SIBLING_KG_HR},
                melt_surface_area_m2=_CEILING_AREA_M2,
            ),
        )
    else:
        raise AssertionError(kind)
    return _interval_flux_snapshot(
        hour,
        1.0,
        rates,
        records or None,
        interval_physical_zero=interval_zero,
    )


def _supply_window_snapshots(
    species: str,
    sibling: str,
    window: tuple[str, ...],
) -> tuple[SimpleNamespace, ...]:
    return tuple(
        _supply_kind_snapshot(index, species, sibling, kind)
        for index, kind in enumerate(window, start=1)
    )


def _kind_contribution_kg(kind: str) -> float | None:
    """The rule, applied to a kind name. None means the species is unavailable."""

    if kind == "computed_above_threshold":
        return _WALL_SUPPLY_ABOVE_KG_HR
    if kind == "computed_below_threshold":
        return _WALL_SUPPLY_BELOW_KG_HR
    if kind in {
        "authorized_zero",
        "below_bound_zero",
        "explicit_zero",
        "interval_physical_zero",
        "interval_physical_zero_with_channel",
    }:
        return 0.0
    if kind == "absent_map_explicit_ceiling":
        return _EXPLICIT_CEILING_KG_HR
    if kind == "catalog_m0_no_recorded_po2":
        return _M0_CEILING_KG_HR
    if kind in {
        "refused",
        "absent_no_record",
        "absent_flux_map",
        "interval_physical_zero_refused",
        "interval_physical_zero_upper_bound",
        "catalog_oxygen_no_recorded_po2",
    }:
        return None
    raise AssertionError(kind)


def _window_species_supply_kg(window: tuple[str, ...]) -> float | None:
    total = 0.0
    for kind in window:
        part = _kind_contribution_kg(kind)
        if part is None:
            return None
        total += part
    return total


def _window_sibling_supply_kg(window: tuple[str, ...]) -> float | None:
    # A missing rate map withholds the sibling. An explicit ceiling is
    # on the target species only, so the sibling still has no law.
    if "absent_flux_map" in window or "absent_map_explicit_ceiling" in window:
        return None
    return _WALL_SUPPLY_SIBLING_KG_HR * len(window)


def _assert_supply_map(
    bounds: Mapping[str, float],
    species: str,
    sibling: str,
    species_kg: float | None,
    sibling_kg: float | None,
) -> None:
    expected = {
        name: kg
        for name, kg in ((species, species_kg), (sibling, sibling_kg))
        if kg is not None
    }
    assert set(bounds) == set(expected)
    for name, kg in expected.items():
        assert bounds[name] == _approx_kg(kg)


def _mutate_classified_interval(kind: str, species: str):
    """Classifier mutation for one interval kind.

    The fixture stays the true kind. The wrapper changes only what the
    classifier returns for ``species``. A rate above the debit floor
    becomes a gap. A subthreshold rate becomes 0. A typed zero becomes
    a gap. A gap, including a certificate sitting on a refusal or an
    upper bound, becomes a physical zero. The unchanged oracle rejects
    each of those.
    """

    real = _classify_wall_supply_interval

    def mutated(
        snapshot: object,
        name: str,
        catalog: object = None,
        recorded_pO2_bar: tuple[float, ...] = (),
    ) -> _WallSupplyInterval:
        interval = real(snapshot, name, catalog, recorded_pO2_bar)
        if name != species:
            return interval
        if (
            kind == "computed_above_threshold"
            and interval.kind == "computed"
            and interval.rate_kg_hr is not None
            and interval.rate_kg_hr > _DEBIT_FLOOR_KG_HR
        ):
            return _WallSupplyInterval(
                "unavailable",
                evidence="absent_no_record",
            )
        if (
            kind == "computed_below_threshold"
            and interval.kind == "computed"
            and interval.rate_kg_hr is not None
            and interval.rate_kg_hr <= _DEBIT_FLOOR_KG_HR
        ):
            return replace(interval, rate_kg_hr=0.0)
        if kind in {
            "authorized_zero",
            "below_bound_zero",
            "interval_physical_zero",
            "interval_physical_zero_with_channel",
        } and interval.kind == "physical_zero":
            return _WallSupplyInterval(
                "unavailable",
                evidence="absent_no_record",
            )
        if kind in {
            "absent_map_explicit_ceiling",
            "catalog_m0_no_recorded_po2",
        } and interval.kind == "supply_interval_upper_bound":
            return _WallSupplyInterval(
                "unavailable",
                evidence="missing_recorded_pO2",
            )
        if kind in {
            "refused",
            "absent_no_record",
            "absent_flux_map",
            "interval_physical_zero_refused",
            "interval_physical_zero_upper_bound",
            "catalog_oxygen_no_recorded_po2",
        } and interval.kind == "unavailable":
            return _WallSupplyInterval(
                "physical_zero",
                evidence="authorized_zero",
            )
        return interval

    return mutated


def test_wall_supply_integral_uses_only_the_interval_classifier() -> None:
    """The integral does not grow a second reader of the flux maps."""

    source = inspect.getsource(_optimizer_refused_flux_bounds)
    assert "_classify_wall_supply_interval(" in source
    for token in (
        "published_species_kg_hr",
        "species_kg_hr",
        "carrier_authority_by_species",
        "zero_by_physics",
        "proven_below_threshold",
    ):
        assert token not in source
    covered = {kind for window in _WALL_SUPPLY_WINDOWS for kind in window}
    assert covered == set(_WALL_SUPPLY_INTERVAL_KINDS)


@pytest.mark.parametrize("species", _COMPUTED_SPECIES)
@pytest.mark.parametrize(
    "window",
    _WALL_SUPPLY_WINDOWS,
    ids=["+".join(window) for window in _WALL_SUPPLY_WINDOWS],
)
def test_wall_supply_interval_contributes_exactly_one_class(
    species: str,
    window: tuple[str, ...],
) -> None:
    """Every refused species and interval is a rate, a typed zero, or unavailable.

    Absence from a flux map without an authorized-zero or below-bound
    record is unavailable. The partial sum of the other intervals is not
    the supply. A missing map withholds every species. Species order
    does not change the values.
    """

    sibling = _supply_sibling(species)
    snapshots = _supply_window_snapshots(species, sibling, window)
    species_kg = _window_species_supply_kg(window)
    sibling_kg = _window_sibling_supply_kg(window)
    for order in ((species, sibling), (sibling, species)):
        bounds = _optimizer_refused_flux_bounds(
            snapshots,
            order,
            campaigns_elapsed=1.0,
        )
        _assert_supply_map(bounds, species, sibling, species_kg, sibling_kg)
        oracle = _oracle_complete_flux_supply_kg(snapshots, order)
        _assert_supply_map(oracle, species, sibling, species_kg, sibling_kg)
    if window == ("computed_above_threshold", "absent_no_record"):
        # Hour 1 at 1 kg/h plus an omitted hour is not a 1 kg supply.
        assert species not in _optimizer_refused_flux_bounds(
            snapshots,
            (species,),
            campaigns_elapsed=1.0,
        )
    if window == (
        "computed_above_threshold",
        "interval_physical_zero_refused",
    ):
        # The certificate on the refused hour must not price the 1 kg.
        assert species not in _optimizer_refused_flux_bounds(
            snapshots,
            (species,),
            campaigns_elapsed=1.0,
        )


# Activity-1 Hertz-Knudsen pin. 1500 K, 1e-3 Pa, 0.068 kg/mol, 0.2 m^2.
# The expected kilogram rate is alpha * P_sat * k_HK * area * 3600, with
# k_HK from the publisher. It is not a copied lunar magnitude.
_ACTIVITY_ONE_T_K = 1500.0
_ACTIVITY_ONE_T_C = _ACTIVITY_ONE_T_K - 273.15
_ACTIVITY_ONE_AREA_M2 = 0.2
_ACTIVITY_ONE_MASS_KG_PER_MOL = 0.068
_ACTIVITY_ONE_PSAT_PA = 1.0e-3


def _activity_one_j_max_kg_hr(
    *,
    temperature_K: float = _ACTIVITY_ONE_T_K,
    pressure_pa: float = _ACTIVITY_ONE_PSAT_PA,
    molar_mass_kg_per_mol: float = _ACTIVITY_ONE_MASS_KG_PER_MOL,
    area_m2: float = _ACTIVITY_ONE_AREA_M2,
    alpha: float = 1.0,
) -> float:
    coefficient = hertz_knudsen_k_kg_s_m2_pa(
        temperature_K,
        molar_mass_kg_per_mol,
    )
    return alpha * pressure_pa * coefficient * area_m2 * 3600.0


def _activity_one_refusal_snapshot(
    species: str,
    *,
    hour: int,
    duration_h: float = 1.0,
    temperature_C: float | None = _ACTIVITY_ONE_T_C,
    area_m2: float | None = _ACTIVITY_ONE_AREA_M2,
    pressure_pa: float | None = _ACTIVITY_ONE_PSAT_PA,
    molar_mass_kg_per_mol: float | None = _ACTIVITY_ONE_MASS_KG_PER_MOL,
    cited_alpha: float | None = None,
    omit_psat: bool = False,
    omit_molar_mass: bool = False,
    rates: Mapping[str, float] | None = None,
) -> SimpleNamespace:
    """One refused carrier hour. ``pressure.pa`` is not the saturation pressure."""

    record = _transport_refusal_channel(species)
    if not omit_psat:
        record["activity_one_psat_pa"] = pressure_pa
    if not omit_molar_mass and molar_mass_kg_per_mol is not None:
        record["molar_mass_kg_per_mol"] = molar_mass_kg_per_mol
    if cited_alpha is not None:
        record["cited_alpha"] = cited_alpha
    fields: dict[str, object] = {
        "published_species_kg_hr": dict(rates or {}),
        "species_kg_hr": dict(rates or {}),
        "carrier_authority_by_species": {species: record},
    }
    if area_m2 is not None:
        fields["melt_surface_area_m2"] = area_m2
    snapshot_fields: dict[str, object] = {
        "hour": hour,
        "duration_h": duration_h,
        "evap_flux": SimpleNamespace(**fields),
    }
    if temperature_C is not None:
        snapshot_fields["temperature_C"] = temperature_C
    return SimpleNamespace(**snapshot_fields)


def _refused_supply_execution(
    snapshots: tuple[object, ...],
    refused: tuple[tuple[str, str], ...],
    *,
    charge_kg: float = 1000.0,
) -> SimpleNamespace:
    refused_by_species: dict[str, list[str]] = {}
    for species, segment in refused:
        segments = refused_by_species.setdefault(species, [])
        if segment not in segments:
            segments.append(segment)
    authority = {
        "wall_saturation_pressure_refusals_by_species": {
            species: {
                segment: {
                    "status": "refused",
                    "output_status": "status_bearing",
                    "reason": f"antoine domain refused for {species}",
                }
                for segment in segments
            }
            for species, segments in refused_by_species.items()
        }
    }
    zones = {
        segment: "Hot"
        for segments in refused_by_species.values()
        for segment in segments
    }
    sim = _FakeProductSim()
    sim.record.batch_mass_kg = charge_kg
    return SimpleNamespace(
        simulator=sim,
        snapshots=snapshots,
        trace=SimpleNamespace(
            wall_deposit_by_segment_species_kg={},
            wall_zone_by_segment=zones,
            wall_deposit_sticking_authority=authority,
        ),
    )


def test_refused_interval_contributes_the_activity_one_bound() -> None:
    """A refused hour adds exactly J_max and is flagged.

    J_max = alpha_ub * P_sat * k_HK * area * 3600. alpha_ub is 1 unless
    the channel carries a cited alpha. P_sat of 0 is a bound of 0, still
    flagged, not an unavailable species. The interval count is not
    divided when the kilogram map is reported per campaign.
    """

    species = "SiO"
    expected = _activity_one_j_max_kg_hr()
    snapshot = _activity_one_refusal_snapshot(species, hour=1)
    interval = _classify_wall_supply_interval(snapshot, species)
    assert interval.kind == "supply_interval_upper_bound"
    assert interval.evidence == SUPPLY_INTERVAL_UPPER_BOUND_USED
    assert interval.rate_kg_hr == _approx_kg(expected)
    bounds = _optimizer_refused_flux_bounds(
        (snapshot,),
        (species,),
        campaigns_elapsed=1.0,
    )
    assert bounds[species] == _approx_kg(expected)
    assert bounds.supply_interval_upper_bound_used == {species: 1}
    per_campaign = _optimizer_refused_flux_bounds(
        (snapshot,),
        (species,),
        campaigns_elapsed=2.0,
    )
    assert per_campaign[species] == _approx_kg(expected / 2.0)
    assert per_campaign.supply_interval_upper_bound_used == {species: 1}

    cited = _activity_one_refusal_snapshot(species, hour=1, cited_alpha=0.5)
    cited_interval = _classify_wall_supply_interval(cited, species)
    assert cited_interval.rate_kg_hr == _approx_kg(
        _activity_one_j_max_kg_hr(alpha=0.5)
    )

    zero = _activity_one_refusal_snapshot(species, hour=1, pressure_pa=0.0)
    zero_interval = _classify_wall_supply_interval(zero, species)
    assert zero_interval.kind == "supply_interval_upper_bound"
    assert zero_interval.rate_kg_hr == 0.0
    zero_bounds = _optimizer_refused_flux_bounds(
        (zero,),
        (species,),
        campaigns_elapsed=1.0,
    )
    assert zero_bounds[species] == 0.0
    assert zero_bounds.supply_interval_upper_bound_used == {species: 1}


# Sanity value from the parent-oxide debit. M(K) = 39.0983 g/mol and
# M(K2O) = 94.1956 g/mol, so 1 kg of K2O is 0.8301513021839662 kg of K.
# M(Na) = 22.98976928 g/mol and M(O) = 15.999 g/mol, so M(Na2O) is
# 61.97853856 g/mol and 1 kg of Na2O is that oxide over 2 M(Na) of Na.
_K_SANITY_OXIDE_PER_VAPOUR = 94.1956 / (2.0 * 39.0983)
_K_SANITY_ELEMENT_FRACTION = (2.0 * 39.0983) / 94.1956
_NA_SANITY_OXIDE_PER_VAPOUR = (2.0 * 22.98976928 + 15.999) / (
    2.0 * 22.98976928
)


def _alkali_inventory_channel(
    species: str,
    *,
    pressure_pa: float,
    parent_oxide: str,
    stoich_oxide_per_vapor: float | None,
) -> dict[str, object]:
    record = _transport_refusal_channel(species)
    record["activity_one_psat_pa"] = pressure_pa
    record["molar_mass_kg_per_mol"] = _ACTIVITY_ONE_MASS_KG_PER_MOL
    record["parent_oxide"] = parent_oxide
    if stoich_oxide_per_vapor is not None:
        record["stoich_oxide_per_vapor"] = stoich_oxide_per_vapor
    return record


def _alkali_inventory_snapshot(
    hour: int,
    channels: Mapping[str, Mapping[str, object]],
    melt_oxide_kg: Mapping[str, float],
) -> SimpleNamespace:
    """Refused carriers over one melt inventory. The map is not debited."""

    return SimpleNamespace(
        hour=hour,
        duration_h=1.0,
        temperature_C=_ACTIVITY_ONE_T_C,
        inventory=SimpleNamespace(melt_oxide_kg=dict(melt_oxide_kg)),
        evap_flux=SimpleNamespace(
            published_species_kg_hr={},
            species_kg_hr={},
            melt_surface_area_m2=_ACTIVITY_ONE_AREA_M2,
            carrier_authority_by_species={
                species: dict(record) for species, record in channels.items()
            },
        ),
    )


def test_shared_k_budget_never_exceeds_the_charge_potassium() -> None:
    """K2O_gas, K2, and K spend one K2O inventory (d-401 amendment 2).

    The kinetic ceiling is the same for each carrier and is far above the
    charge, so an uncapped integral would book the ceiling three times.
    The oxide is split in proportion to each carrier's oxide demand.
    Elemental K has no stored stoich: the parent-oxide debit derives
    M(K2O) / (2 * M(K)). K2 carries that same ratio explicitly. K2O_gas
    is 1 kg oxide per kg vapour. Their element sum is the potassium in
    the charge, and a second hour that still shows the undepleted melt
    cannot spend it again. Na2O is a separate budget. SiO2 stays above
    its own demand, so SiO keeps the kinetic mass and is not flagged.
    """

    from simulator.state import MOLAR_MASS

    stoich_k = float(MOLAR_MASS["K2O"]) / (2.0 * float(MOLAR_MASS["K"]))
    element_fraction = (2.0 * float(MOLAR_MASS["K"])) / float(MOLAR_MASS["K2O"])
    assert stoich_k == pytest.approx(_K_SANITY_OXIDE_PER_VAPOUR, rel=1e-12)
    assert element_fraction == pytest.approx(_K_SANITY_ELEMENT_FRACTION, rel=1e-12)
    pressure_pa = 1.0
    kinetic_kg_hr = _activity_one_j_max_kg_hr(pressure_pa=pressure_pa)
    k2o_kg = 0.01
    na2o_kg = 0.02
    sio2_kg = 100.0
    assert kinetic_kg_hr * (1.0 + 2.0 * stoich_k) > k2o_kg
    assert kinetic_kg_hr > na2o_kg
    assert 2.0 * kinetic_kg_hr * 2.0 < sio2_kg
    channels = {
        # No stoich_oxide_per_vapor: elemental K uses the derived ratio.
        "K": _alkali_inventory_channel(
            "K",
            pressure_pa=pressure_pa,
            parent_oxide="K2O",
            stoich_oxide_per_vapor=None,
        ),
        "K2": _alkali_inventory_channel(
            "K2",
            pressure_pa=pressure_pa,
            parent_oxide="K2O",
            stoich_oxide_per_vapor=stoich_k,
        ),
        "K2O_gas": _alkali_inventory_channel(
            "K2O_gas",
            pressure_pa=pressure_pa,
            parent_oxide="K2O",
            stoich_oxide_per_vapor=1.0,
        ),
        "Na2O_gas": _alkali_inventory_channel(
            "Na2O_gas",
            pressure_pa=pressure_pa,
            parent_oxide="Na2O",
            stoich_oxide_per_vapor=1.0,
        ),
        "SiO": _alkali_inventory_channel(
            "SiO",
            pressure_pa=pressure_pa,
            parent_oxide="SiO2",
            stoich_oxide_per_vapor=2.0,
        ),
    }
    inventory = {"K2O": k2o_kg, "Na2O": na2o_kg, "SiO2": sio2_kg}
    snapshots = (
        _alkali_inventory_snapshot(1, channels, inventory),
        _alkali_inventory_snapshot(2, channels, inventory),
    )
    order = ("K2O_gas", "K2", "K", "Na2O_gas", "SiO")
    forward = _optimizer_refused_flux_bounds(
        snapshots,
        order,
        campaigns_elapsed=1.0,
    )
    reverse = _optimizer_refused_flux_bounds(
        snapshots,
        tuple(reversed(order)),
        campaigns_elapsed=1.0,
    )
    shared_vapour_kg = k2o_kg / (1.0 + 2.0 * stoich_k)
    expected = {
        "K": shared_vapour_kg,
        "K2": shared_vapour_kg,
        "K2O_gas": shared_vapour_kg,
        "Na2O_gas": na2o_kg,
        "SiO": 2.0 * kinetic_kg_hr,
    }
    for bounds in (forward, reverse):
        for species, mass_kg in expected.items():
            assert bounds[species] == _approx_kg(mass_kg)
        potassium_kg = (
            bounds["K"] + bounds["K2"] + bounds["K2O_gas"] * element_fraction
        )
        assert potassium_kg == _approx_kg(k2o_kg * element_fraction)
        oxide_kg = (
            bounds["K"] * stoich_k
            + bounds["K2"] * stoich_k
            + bounds["K2O_gas"]
        )
        assert oxide_kg == _approx_kg(k2o_kg)
        assert bounds.inventory_capped == {
            "K": True,
            "K2": True,
            "K2O_gas": True,
            "Na2O_gas": True,
        }
        assert bounds.supply_interval_upper_bound_used == {
            species: 2 for species in order
        }
    one_hour = _optimizer_refused_flux_bounds(
        snapshots[:1],
        order,
        campaigns_elapsed=1.0,
    )
    for species in ("K", "K2", "K2O_gas", "Na2O_gas"):
        assert forward[species] == _approx_kg(one_hour[species])
    assert forward["SiO"] == _approx_kg(2.0 * one_hour["SiO"])
    per_campaign = _optimizer_refused_flux_bounds(
        snapshots,
        order,
        campaigns_elapsed=2.0,
    )
    assert per_campaign["K2O_gas"] == _approx_kg(forward["K2O_gas"] / 2.0)
    assert per_campaign.supply_interval_upper_bound_used == {
        species: 2 for species in order
    }
    alone = _alkali_inventory_snapshot(
        1,
        {"K2O_gas": channels["K2O_gas"]},
        {"K2O": k2o_kg},
    )
    alone_bounds = _optimizer_refused_flux_bounds(
        (alone,),
        ("K2O_gas",),
        campaigns_elapsed=1.0,
    )
    assert alone_bounds["K2O_gas"] == _approx_kg(k2o_kg)
    assert alone_bounds["K2O_gas"] > forward["K2O_gas"]
    assert alone_bounds.inventory_capped == {"K2O_gas": True}
    execution = _refused_supply_execution(
        snapshots,
        tuple((species, "hot_wall") for species in order),
    )
    booked = objective_module._book_wall_deposits_at_vapour_supply(
        {},
        execution.trace.wall_deposit_sticking_authority,
        execution,
    )
    assert booked["notice_authority"][INVENTORY_CAPPED] == {
        "K": True,
        "K2": True,
        "K2O_gas": True,
        "Na2O_gas": True,
    }


# Invariant. For each parent oxide, computed debits plus refused bounds
# stay within the inventory at the start of the window. That start is
# ``window_start_kg`` when the first snapshot is already post-debit, and
# the first interval's recorded inventory otherwise. The oracle is the
# closed form D + min(R, max(0, C - D)), split by kinetic oxide demand.
# It does not walk intervals subtracting a running balance, and it does
# not call the supply integral or its helpers.
_OXIDE_HK_PRESSURE_PA = 10.0
_OXIDE_HK_R_J_MOL_K = 8.314462618


def _oracle_hk_kg_hr(pressure_pa: float) -> float:
    """Handwritten activity-1 Hertz-Knudsen rate. R is written out."""

    coefficient = math.sqrt(
        _ACTIVITY_ONE_MASS_KG_PER_MOL
        / (2.0 * math.pi * _OXIDE_HK_R_J_MOL_K * _ACTIVITY_ONE_T_K)
    )
    return pressure_pa * coefficient * _ACTIVITY_ONE_AREA_M2 * 3600.0


def _oracle_window_start_kg(case: Mapping[str, object]) -> dict[str, float]:
    """Window-start oxide, named by the case. Not reconstructed."""

    named = case.get("window_start_kg")
    if isinstance(named, Mapping):
        return {str(oxide): float(kg) for oxide, kg in named.items()}
    inventory = case["intervals"][0]["inventory"]
    return {str(oxide): float(kg) for oxide, kg in inventory.items()}


def _oracle_shared_oxide_vapour(case: Mapping[str, object]) -> dict[str, float]:
    """Closed-form split of one window-start charge.

    C is the window-start oxide. D is every computed rate times its
    stoich. R is every refused kinetic ceiling times its stoich. The
    booked oxide is D + min(R, max(0, C - D)). Carriers of one oxide
    split the refused part in proportion to kinetic oxide demand.
    Interval order and the snapshot remainder are not inputs.
    """

    stoich = {str(name): float(ratio) for name, ratio in case["stoich"].items()}
    parent_of = {str(name): str(oxide) for name, oxide in case["parent"].items()}
    charge = _oracle_window_start_kg(case)
    computed = {name: 0.0 for name in stoich}
    refused_kinetic = {name: 0.0 for name in stoich}
    for interval in case["intervals"]:
        duration_h = float(interval["duration_h"])
        for name, rate in interval["computed"].items():
            computed[str(name)] += float(rate) * duration_h
        for name, pressure in interval["refused_pressure_pa"].items():
            refused_kinetic[str(name)] += (
                _oracle_hk_kg_hr(float(pressure)) * duration_h
            )
    vapour = dict(computed)
    for parent in set(parent_of.values()):
        names = [name for name, oxide in parent_of.items() if oxide == parent]
        spent = sum(computed[name] * stoich[name] for name in names)
        demands = {
            name: refused_kinetic[name] * stoich[name]
            for name in names
            if refused_kinetic[name] > 0.0
        }
        kinetic_oxide = sum(demands.values())
        room = float(charge.get(parent, 0.0)) - spent
        if room < 0.0:
            room = 0.0
        refused_oxide = kinetic_oxide if kinetic_oxide < room else room
        if kinetic_oxide <= 0.0:
            continue
        for name, demand in demands.items():
            vapour[name] += (demand / kinetic_oxide) * refused_oxide / stoich[name]
    return vapour


def _shared_oxide_snapshots(case: Mapping[str, object]) -> tuple[SimpleNamespace, ...]:
    stoich = dict(case["stoich"])
    parent_of = dict(case["parent"])
    built: list[SimpleNamespace] = []
    for index, interval in enumerate(case["intervals"], start=1):
        refused = dict(interval["refused_pressure_pa"])
        computed = dict(interval["computed"])
        rates: dict[str, float] = {}
        channels: dict[str, dict[str, object]] = {}
        for name, ratio in stoich.items():
            channels[name] = _alkali_inventory_channel(
                name,
                pressure_pa=float(refused.get(name, _OXIDE_HK_PRESSURE_PA)),
                parent_oxide=parent_of[name],
                stoich_oxide_per_vapor=float(ratio),
            )
            if name in refused:
                continue
            rates[name] = float(computed.get(name, 0.0))
        flux = SimpleNamespace(
            published_species_kg_hr=rates,
            species_kg_hr=dict(rates),
            melt_surface_area_m2=_ACTIVITY_ONE_AREA_M2,
            carrier_authority_by_species=channels,
        )
        pre = interval.get("pre_evaporation_kg")
        if isinstance(pre, Mapping):
            flux.pre_evaporation_melt_oxide_kg = dict(pre)
        built.append(
            SimpleNamespace(
                hour=index,
                duration_h=float(interval["duration_h"]),
                temperature_C=_ACTIVITY_ONE_T_C,
                inventory=SimpleNamespace(
                    melt_oxide_kg=dict(interval["inventory"])
                ),
                evap_flux=flux,
            )
        )
    return tuple(built)


def _parent_oxide_kg(
    masses: Mapping[str, float],
    case: Mapping[str, object],
    parent: str,
) -> float:
    stoich = case["stoich"]
    parent_of = case["parent"]
    return sum(
        float(masses[name]) * float(stoich[name])
        for name, oxide in parent_of.items()
        if oxide == parent
    )


_SHARED_OXIDE_CASES = (
    {
        "name": "refused_then_computed",
        "stoich": {"K2O_gas": 1.0},
        "parent": {"K2O_gas": "K2O"},
        "capped": ("K2O_gas",),
        "mutation_exceeds": ("K2O",),
        "intervals": (
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {"K2O_gas": 0.25},
                "refused_pressure_pa": {},
            },
        ),
    },
    {
        "name": "computed_then_refused",
        "stoich": {"K2O_gas": 1.0},
        "parent": {"K2O_gas": "K2O"},
        "capped": ("K2O_gas",),
        "mutation_exceeds": ("K2O",),
        "intervals": (
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {"K2O_gas": 0.25},
                "refused_pressure_pa": {},
            },
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
        ),
    },
    {
        "name": "refused_computed_refused",
        "stoich": {"K2O_gas": 1.0},
        "parent": {"K2O_gas": "K2O"},
        "capped": ("K2O_gas",),
        "mutation_exceeds": ("K2O",),
        "intervals": (
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {"K2O_gas": 0.25},
                "refused_pressure_pa": {},
            },
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
        ),
    },
    {
        "name": "computed_duration_reserves_rate_times_dt",
        "stoich": {"K2O_gas": 1.0},
        "parent": {"K2O_gas": "K2O"},
        "capped": ("K2O_gas",),
        "mutation_exceeds": ("K2O",),
        "intervals": (
            {
                "duration_h": 2.0,
                "inventory": {"K2O": 1.0},
                "computed": {"K2O_gas": 0.25},
                "refused_pressure_pa": {},
            },
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
        ),
    },
    {
        "name": "same_hour_computed_k2_and_refused_k2o",
        "stoich": {"K2": 2.0, "K2O_gas": 1.0},
        "parent": {"K2": "K2O", "K2O_gas": "K2O"},
        "capped": ("K2O_gas",),
        "mutation_exceeds": ("K2O",),
        "intervals": (
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {"K2": 0.25},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
        ),
    },
    {
        "name": "k2_computed_then_k2o_refused",
        "stoich": {"K2": 2.0, "K2O_gas": 1.0},
        "parent": {"K2": "K2O", "K2O_gas": "K2O"},
        "capped": ("K2O_gas",),
        "mutation_exceeds": ("K2O",),
        "intervals": (
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {"K2": 0.25},
                "refused_pressure_pa": {},
            },
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
        ),
    },
    {
        "name": "k2o_refused_then_k2_computed",
        "stoich": {"K2": 2.0, "K2O_gas": 1.0},
        "parent": {"K2": "K2O", "K2O_gas": "K2O"},
        "capped": ("K2O_gas",),
        "mutation_exceeds": ("K2O",),
        "intervals": (
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {"K2": 0.25},
                "refused_pressure_pa": {},
            },
        ),
    },
    {
        "name": "remainder_split_across_two_refused_carriers",
        "stoich": {"K2": 2.0, "K2O_gas": 1.0},
        "parent": {"K2": "K2O", "K2O_gas": "K2O"},
        "capped": ("K2", "K2O_gas"),
        "mutation_exceeds": ("K2O",),
        "intervals": (
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {},
                "refused_pressure_pa": {
                    "K2": _OXIDE_HK_PRESSURE_PA,
                    "K2O_gas": _OXIDE_HK_PRESSURE_PA,
                },
            },
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {"K2O_gas": 0.25},
                "refused_pressure_pa": {},
            },
        ),
    },
    {
        "name": "refused_only",
        "stoich": {"K2O_gas": 1.0},
        "parent": {"K2O_gas": "K2O"},
        "capped": ("K2O_gas",),
        "mutation_exceeds": (),
        "intervals": (
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
        ),
    },
    {
        "name": "computed_only",
        "stoich": {"K2O_gas": 1.0},
        "parent": {"K2O_gas": "K2O"},
        "capped": (),
        "mutation_exceeds": (),
        "intervals": (
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {"K2O_gas": 0.25},
                "refused_pressure_pa": {},
            },
        ),
    },
    {
        "name": "na_computed_does_not_spend_k",
        "stoich": {"K2O_gas": 1.0, "Na2O_gas": 1.0},
        "parent": {"K2O_gas": "K2O", "Na2O_gas": "Na2O"},
        "capped": ("K2O_gas",),
        "mutation_exceeds": (),
        "intervals": (
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0, "Na2O": 0.5},
                "computed": {"Na2O_gas": 0.2},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
        ),
    },
    {
        "name": "depleted_snapshot_is_not_charged_twice",
        "stoich": {"K2O_gas": 1.0},
        "parent": {"K2O_gas": "K2O"},
        "capped": ("K2O_gas",),
        "mutation_exceeds": (),
        "intervals": (
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 1.0},
                "computed": {},
                "refused_pressure_pa": {"K2O_gas": 0.0},
            },
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 0.6},
                "computed": {"K2O_gas": 0.4},
                "refused_pressure_pa": {},
            },
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 0.6},
                "computed": {},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
        ),
    },
    {
        # The first snapshot is already the post-debit remainder. The
        # undepleted charge lives on the pre-evaporation map, not on
        # that remainder. 1 kg at the start, 0.4 kg computed, 0.6 kg left.
        "name": "first_snapshot_is_post_debit",
        "stoich": {"K2O_gas": 1.0},
        "parent": {"K2O_gas": "K2O"},
        "capped": ("K2O_gas",),
        "mutation_exceeds": (),
        "window_start_kg": {"K2O": 1.0},
        "intervals": (
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 0.6},
                "pre_evaporation_kg": {"K2O": 1.0},
                "computed": {"K2O_gas": 0.4},
                "refused_pressure_pa": {},
            },
            {
                "duration_h": 1.0,
                "inventory": {"K2O": 0.6},
                "computed": {},
                "refused_pressure_pa": {"K2O_gas": _OXIDE_HK_PRESSURE_PA},
            },
        ),
    },
)


@pytest.mark.parametrize(
    "case",
    _SHARED_OXIDE_CASES,
    ids=[case["name"] for case in _SHARED_OXIDE_CASES],
)
def test_computed_parent_oxide_is_reserved_before_refused_bounds(
    case: Mapping[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused bounds cannot spend oxide a computed draw already claimed.

    The kinetic ceiling is above the charge, so an allocator that ignores
    the computed draw books more parent oxide than the melt held. Dropping
    the computed reserve is the mutation: every kind that mixes a computed
    draw with a refused demand then exceeds the charge. A post-debit
    snapshot is not charged a second time. Na2O does not spend K2O.
    Species order does not change the kilograms.
    """

    assert _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA) > 1.0
    bare = _activity_one_refusal_snapshot(
        "K2O_gas",
        hour=1,
        pressure_pa=_OXIDE_HK_PRESSURE_PA,
    )
    bare_bounds = _optimizer_refused_flux_bounds((bare,), ("K2O_gas",))
    assert bare_bounds["K2O_gas"] == _approx_kg(
        _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA)
    )
    snapshots = _shared_oxide_snapshots(case)
    species = tuple(case["stoich"])
    oracle = _oracle_shared_oxide_vapour(case)
    orders = (species, tuple(reversed(species)))
    for order in orders:
        bounds = _optimizer_refused_flux_bounds(snapshots, order)
        assert set(bounds) == set(species)
        for name, mass_kg in oracle.items():
            assert bounds[name] == _approx_kg(mass_kg)
        assert bounds.inventory_capped == {name: True for name in case["capped"]}
        window_start = _oracle_window_start_kg(case)
        for parent in set(case["parent"].values()):
            assert _parent_oxide_kg(bounds, case, parent) <= (
                float(window_start.get(parent, 0.0)) + 1e-12
            )
    monkeypatch.setattr(
        evaluate_module,
        "_computed_parent_oxide_draws",
        lambda *args, **kwargs: evaluate_module._empty_oxide_draws(),
    )
    for order in orders:
        mutated = _optimizer_refused_flux_bounds(snapshots, order)
        if case["mutation_exceeds"]:
            window_start = _oracle_window_start_kg(case)
            for parent in case["mutation_exceeds"]:
                charge = float(window_start[parent])
                assert _parent_oxide_kg(mutated, case, parent) > charge
        else:
            for name, mass_kg in oracle.items():
                assert mutated[name] == _approx_kg(mass_kg)


def test_first_post_debit_snapshot_keeps_the_pre_evaporation_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1 kg at the start, 0.4 kg computed, 0.6 kg still in the melt.

    Production copies the snapshot after evaporation, so the first melt
    map is already the 0.6 kg remainder. The refused hour must receive
    that 0.6 kg. Treating the remainder as the charge and subtracting
    the 0.4 kg debit again leaves 0.2 kg. Dropping the pre-evaporation
    map is that defect.
    """

    assert _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA) > 1.0
    channel = _alkali_inventory_channel(
        "K2O_gas",
        pressure_pa=_OXIDE_HK_PRESSURE_PA,
        parent_oxide="K2O",
        stoich_oxide_per_vapor=1.0,
    )
    first = SimpleNamespace(
        hour=1,
        duration_h=1.0,
        temperature_C=_ACTIVITY_ONE_T_C,
        inventory=SimpleNamespace(melt_oxide_kg={"K2O": 0.6}),
        evap_flux=SimpleNamespace(
            published_species_kg_hr={"K2O_gas": 0.4},
            species_kg_hr={"K2O_gas": 0.4},
            melt_surface_area_m2=_ACTIVITY_ONE_AREA_M2,
            carrier_authority_by_species={"K2O_gas": dict(channel)},
            pre_evaporation_melt_oxide_kg={"K2O": 1.0},
        ),
    )
    second = SimpleNamespace(
        hour=2,
        duration_h=1.0,
        temperature_C=_ACTIVITY_ONE_T_C,
        inventory=SimpleNamespace(melt_oxide_kg={"K2O": 0.6}),
        evap_flux=SimpleNamespace(
            published_species_kg_hr={},
            species_kg_hr={},
            melt_surface_area_m2=_ACTIVITY_ONE_AREA_M2,
            carrier_authority_by_species={"K2O_gas": dict(channel)},
        ),
    )
    bounds = _optimizer_refused_flux_bounds((first, second), ("K2O_gas",))
    assert bounds["K2O_gas"] == _approx_kg(1.0)
    assert bounds["K2O_gas"] - 0.4 == _approx_kg(0.6)
    assert bounds["K2O_gas"] < 0.4 + _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA)
    assert bounds.inventory_capped == {"K2O_gas": True}
    monkeypatch.setattr(
        evaluate_module,
        "_pre_evaporation_oxide_kg",
        lambda _snapshot: None,
    )
    stripped = _optimizer_refused_flux_bounds((first, second), ("K2O_gas",))
    assert stripped["K2O_gas"] == _approx_kg(0.6)
    assert stripped["K2O_gas"] - 0.4 == _approx_kg(0.2)


def test_missing_vapour_stoichiometry_does_not_publish_the_kinetic_ceiling() -> None:
    """A parent without a ratio, or a ratio without a parent, is not a mass.

    K2 from K2O has no elemental fallback. The refused ceiling is above
    the 1 kg charge, and publishing it would spend more oxide than the
    melt held. Elemental K on the same melt still uses the owner's
    ratio and books that 1 kg. A stored ratio with the parent removed
    is the same typed gap, and it does not take K's budget.
    """

    host = EvaporationMixin.__new__(EvaporationMixin)
    host.species_formula_registry = {}
    owned = host._evaporation_stoich("K", {"parent_oxide": "K2O"})
    owner_ratio = float(owned["oxide_per_product_kg"])
    assert owner_ratio == pytest.approx(_K_SANITY_OXIDE_PER_VAPOUR, rel=1e-12)
    kinetic = _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA)
    assert kinetic > 1.0
    charge = 1.0
    k2 = _alkali_inventory_channel(
        "K2",
        pressure_pa=_OXIDE_HK_PRESSURE_PA,
        parent_oxide="K2O",
        stoich_oxide_per_vapor=None,
    )
    metal = _alkali_inventory_channel(
        "K",
        pressure_pa=_OXIDE_HK_PRESSURE_PA,
        parent_oxide="K2O",
        stoich_oxide_per_vapor=None,
    )
    snapshot = SimpleNamespace(
        hour=1,
        duration_h=1.0,
        temperature_C=_ACTIVITY_ONE_T_C,
        inventory=SimpleNamespace(melt_oxide_kg={"K2O": charge}),
        evap_flux=SimpleNamespace(
            published_species_kg_hr={},
            species_kg_hr={},
            melt_surface_area_m2=_ACTIVITY_ONE_AREA_M2,
            carrier_authority_by_species={"K2": k2, "K": metal},
        ),
    )
    for order in (("K2", "K"), ("K", "K2")):
        bounds = _optimizer_refused_flux_bounds((snapshot,), order)
        assert "K2" not in bounds
        assert bounds.supply_unavailable["K2"] == (
            MISSING_PARENT_OXIDE_STOICHIOMETRY
        )
        assert bounds["K"] == _approx_kg(charge / owner_ratio)
        assert bounds["K"] * owner_ratio == _approx_kg(charge)
        assert bounds["K"] < kinetic
        assert bounds.inventory_capped == {"K": True}
    ratio_without_parent = dict(k2)
    ratio_without_parent["stoich_oxide_per_vapor"] = 2.0
    del ratio_without_parent["parent_oxide"]
    gap = SimpleNamespace(
        hour=1,
        duration_h=1.0,
        temperature_C=_ACTIVITY_ONE_T_C,
        inventory=SimpleNamespace(melt_oxide_kg={"K2O": charge}),
        evap_flux=SimpleNamespace(
            published_species_kg_hr={},
            species_kg_hr={},
            melt_surface_area_m2=_ACTIVITY_ONE_AREA_M2,
            carrier_authority_by_species={
                "K2": ratio_without_parent,
                "K": dict(metal),
            },
        ),
    )
    bounds = _optimizer_refused_flux_bounds((gap,), ("K2", "K"))
    assert "K2" not in bounds
    assert bounds.supply_unavailable["K2"] == MISSING_PARENT_OXIDE_STOICHIOMETRY
    assert bounds["K"] * owner_ratio == _approx_kg(charge)
    assert bounds["K"] < kinetic


def test_computed_missing_stoichiometry_withholds_the_parent() -> None:
    """A computed K2 rate with no ratio makes K2O unreservable.

    The 0.4 kg/h rate is a real debit whose oxide mass cannot be named.
    Booking the refused K2O_gas at the 1 kg charge would spend that
    oxide with the debit left out. Both carriers are typed unavailable.
    """

    assert _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA) > 1.0
    k2 = _alkali_inventory_channel(
        "K2",
        pressure_pa=_OXIDE_HK_PRESSURE_PA,
        parent_oxide="K2O",
        stoich_oxide_per_vapor=None,
    )
    k2o = _alkali_inventory_channel(
        "K2O_gas",
        pressure_pa=_OXIDE_HK_PRESSURE_PA,
        parent_oxide="K2O",
        stoich_oxide_per_vapor=1.0,
    )
    snapshot = SimpleNamespace(
        hour=1,
        duration_h=1.0,
        temperature_C=_ACTIVITY_ONE_T_C,
        inventory=SimpleNamespace(melt_oxide_kg={"K2O": 1.0}),
        evap_flux=SimpleNamespace(
            published_species_kg_hr={"K2": 0.4},
            species_kg_hr={"K2": 0.4},
            melt_surface_area_m2=_ACTIVITY_ONE_AREA_M2,
            carrier_authority_by_species={"K2": k2, "K2O_gas": k2o},
        ),
    )
    for order in (("K2", "K2O_gas"), ("K2O_gas", "K2")):
        bounds = _optimizer_refused_flux_bounds((snapshot,), order)
        assert "K2" not in bounds
        assert "K2O_gas" not in bounds
        assert bounds.supply_unavailable["K2"] == (
            MISSING_PARENT_OXIDE_STOICHIOMETRY
        )
        assert bounds.supply_unavailable["K2O_gas"] == (
            MISSING_PARENT_OXIDE_STOICHIOMETRY
        )
        assert bounds.inventory_capped == {}


# Withheld oxide-gas debit on the computed hour. The metals stay refused
# on every hour. 0.20 and 0.10 are kilograms of vapour; the oxide-gas
# stoich is 1, so they are also kilograms of oxide.
_ELEMENT_COMPUTED_K2O_GAS_KG = 0.20
_ELEMENT_COMPUTED_NA2O_GAS_KG = 0.10
_ELEMENT_CHARGE_KG = {"K2O": 1.0, "Na2O": 1.0}
_ELEMENT_CARRIERS = ("K2O_gas", "K2", "K", "Na2O_gas", "Na2", "Na")
_ELEMENT_INTERVAL_ORDERS = tuple(
    permutations(("computed", "refused", "withheld"))
)
_ELEMENT_WINDOWS = tuple(
    (mode, order)
    for mode in ("pre_debit", "post_debit")
    for order in _ELEMENT_INTERVAL_ORDERS
)


def _oracle_element_vapour_kg() -> dict[str, float]:
    """Closed form for one K budget and one Na budget.

    The withheld computed debit is 0.20 kg of K2O and 0.10 kg of Na2O.
    What remains of the 1 kg charge is split between the elemental metal
    and the dimer. Those two carriers have the same kinetic rate, so
    each vapour mass is ``remainder / (element_stoich + dimer_stoich)``.
    Interval order, the snapshot remainder, and species order are not
    inputs. This does not walk hours or subtract a running balance.
    """

    k_room = _ELEMENT_CHARGE_KG["K2O"] - _ELEMENT_COMPUTED_K2O_GAS_KG
    na_room = _ELEMENT_CHARGE_KG["Na2O"] - _ELEMENT_COMPUTED_NA2O_GAS_KG
    k_vapour = k_room / (_K_SANITY_OXIDE_PER_VAPOUR + 2.0)
    na_vapour = na_room / (_NA_SANITY_OXIDE_PER_VAPOUR + 2.0)
    return {"K": k_vapour, "K2": k_vapour, "Na": na_vapour, "Na2": na_vapour}


def _element_budget_snapshots(
    mode: str,
    order: tuple[str, ...],
) -> tuple[SimpleNamespace, ...]:
    """One window. The first post-debit snapshot carries the start charge."""

    charge = dict(_ELEMENT_CHARGE_KG)
    final = {
        "K2O": charge["K2O"] - _ELEMENT_COMPUTED_K2O_GAS_KG,
        "Na2O": charge["Na2O"] - _ELEMENT_COMPUTED_NA2O_GAS_KG,
    }
    built: list[SimpleNamespace] = []
    for index, kind in enumerate(order, start=1):
        rates: dict[str, float] = {}
        hour_debit = {"K2O": 0.0, "Na2O": 0.0}
        if kind == "computed":
            rates = {
                "K2O_gas": _ELEMENT_COMPUTED_K2O_GAS_KG,
                "Na2O_gas": _ELEMENT_COMPUTED_NA2O_GAS_KG,
            }
            hour_debit = {
                "K2O": _ELEMENT_COMPUTED_K2O_GAS_KG,
                "Na2O": _ELEMENT_COMPUTED_NA2O_GAS_KG,
            }
        channels: dict[str, dict[str, object]] = {}
        for species, parent, stoich in (
            ("K2O_gas", "K2O", 1.0),
            ("K2", "K2O", 2.0),
            ("K", "K2O", None),
            ("Na2O_gas", "Na2O", 1.0),
            ("Na2", "Na2O", 2.0),
            ("Na", "Na2O", None),
        ):
            record = _alkali_inventory_channel(
                species,
                pressure_pa=_OXIDE_HK_PRESSURE_PA,
                parent_oxide=parent,
                stoich_oxide_per_vapor=stoich,
            )
            if kind != "computed" and species.endswith("_gas"):
                record["activity_one_psat_pa"] = None
            channels[species] = record
        if mode == "pre_debit":
            recorded = dict(charge)
            pre = None
        elif index == 1:
            recorded = {
                oxide: charge[oxide] - hour_debit[oxide] for oxide in charge
            }
            pre = dict(charge)
        else:
            recorded = dict(final)
            pre = None
        flux = SimpleNamespace(
            published_species_kg_hr=dict(rates),
            species_kg_hr=dict(rates),
            melt_surface_area_m2=_ACTIVITY_ONE_AREA_M2,
            carrier_authority_by_species=channels,
        )
        if pre is not None:
            flux.pre_evaporation_melt_oxide_kg = pre
        built.append(
            SimpleNamespace(
                hour=index,
                duration_h=1.0,
                temperature_C=_ACTIVITY_ONE_T_C,
                inventory=SimpleNamespace(melt_oxide_kg=recorded),
                evap_flux=flux,
            )
        )
    return tuple(built)


@pytest.mark.parametrize(
    ("mode", "order"),
    _ELEMENT_WINDOWS,
    ids=[
        f"{mode}-" + "-".join(order) for mode, order in _ELEMENT_WINDOWS
    ],
)
def test_shared_element_mass_stays_inside_the_pre_evaporation_charge(
    mode: str,
    order: tuple[str, ...],
) -> None:
    """K and Na each have one charge, shared by every carrier.

    Computed, refused, and withheld hours run in every order. The first
    snapshot is either the pre-evaporation charge or the post-debit
    remainder with that charge stored beside it. K2O_gas, K2, and K
    share K2O. Na2O_gas, Na2, and Na share Na2O. The oxide gases are
    withheld after a computed debit, and that debit stays in the sum.
    The expected vapour is the closed form above, not a running balance.
    """

    host = EvaporationMixin.__new__(EvaporationMixin)
    host.species_formula_registry = {}
    owned_k = host._evaporation_stoich("K", {"parent_oxide": "K2O"})
    owned_na = host._evaporation_stoich("Na", {"parent_oxide": "Na2O"})
    assert float(owned_k["oxide_per_product_kg"]) == pytest.approx(
        _K_SANITY_OXIDE_PER_VAPOUR,
        rel=1e-12,
    )
    assert float(owned_na["oxide_per_product_kg"]) == pytest.approx(
        _NA_SANITY_OXIDE_PER_VAPOUR,
        rel=1e-12,
    )
    assert _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA) > 1.0
    expected = _oracle_element_vapour_kg()
    snapshots = _element_budget_snapshots(mode, order)
    species_orders = (_ELEMENT_CARRIERS, tuple(reversed(_ELEMENT_CARRIERS)))
    results = [
        _optimizer_refused_flux_bounds(snapshots, species_order)
        for species_order in species_orders
    ]
    for bounds in results:
        assert "K2O_gas" not in bounds
        assert "Na2O_gas" not in bounds
        for name, mass_kg in expected.items():
            assert bounds[name] == _approx_kg(mass_kg)
        potassium_oxide = (
            bounds["K"] * _K_SANITY_OXIDE_PER_VAPOUR
            + bounds["K2"] * 2.0
            + _ELEMENT_COMPUTED_K2O_GAS_KG
        )
        sodium_oxide = (
            bounds["Na"] * _NA_SANITY_OXIDE_PER_VAPOUR
            + bounds["Na2"] * 2.0
            + _ELEMENT_COMPUTED_NA2O_GAS_KG
        )
        assert potassium_oxide == _approx_kg(_ELEMENT_CHARGE_KG["K2O"])
        assert sodium_oxide == _approx_kg(_ELEMENT_CHARGE_KG["Na2O"])
        assert bounds.inventory_capped == {
            "K": True,
            "K2": True,
            "Na": True,
            "Na2": True,
        }
    assert dict(results[0]) == dict(results[1])


def test_stripping_the_pre_evaporation_map_understates_a_leading_debit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the pre-evaporation map the first remainder becomes the charge.

    Computed hour first, then refused, then withheld. The snapshot shows
    0.80 kg of K2O after the 0.20 kg debit. Subtracting that debit again
    leaves 0.60 kg for the metals. The withheld 0.20 kg is still real, so
    the element sum is 0.80 kg against a 1 kg charge.
    """

    order = ("computed", "refused", "withheld")
    snapshots = _element_budget_snapshots("post_debit", order)
    species_orders = (_ELEMENT_CARRIERS, tuple(reversed(_ELEMENT_CARRIERS)))
    for species_order in species_orders:
        held = _optimizer_refused_flux_bounds(snapshots, species_order)
        held_oxide = (
            held["K"] * _K_SANITY_OXIDE_PER_VAPOUR
            + held["K2"] * 2.0
            + _ELEMENT_COMPUTED_K2O_GAS_KG
        )
        assert held_oxide == _approx_kg(1.0)
    monkeypatch.setattr(
        evaluate_module,
        "_pre_evaporation_oxide_kg",
        lambda _snapshot: None,
    )
    for species_order in species_orders:
        stripped = _optimizer_refused_flux_bounds(snapshots, species_order)
        refused_oxide = (
            stripped["K"] * _K_SANITY_OXIDE_PER_VAPOUR + stripped["K2"] * 2.0
        )
        element_oxide = refused_oxide + _ELEMENT_COMPUTED_K2O_GAS_KG
        assert refused_oxide == _approx_kg(0.60)
        assert element_oxide == _approx_kg(0.80)
        assert element_oxide < 1.0
        sodium_oxide = (
            stripped["Na"] * _NA_SANITY_OXIDE_PER_VAPOUR
            + stripped["Na2"] * 2.0
            + _ELEMENT_COMPUTED_NA2O_GAS_KG
        )
        assert sodium_oxide == _approx_kg(0.90)
        assert sodium_oxide < 1.0


# Invariant. For one parent oxide, the oxide-equivalent of every computed
# vapour debit (published or withheld) plus every published refused bound
# is at most the first recorded inventory, unless the computed debit alone
# already exceeds it. A withheld species keeps its computed debit in that
# sum. A snapshot that omits its inventory does not publish the kinetic
# ceiling. A refused bound whose parent was never recorded is absent and
# named missing_parent_oxide_inventory. The checks below sum the case
# inputs and the returned map. They do not call the allocator or scale a
# demand by the remaining budget.
_OXIDE_CARRIERS = ("K2O_gas", "K2", "Na2O_gas")
_OXIDE_PARENT = {"K2O_gas": "K2O", "K2": "K2O", "Na2O_gas": "Na2O"}
_OXIDE_COMPUTED_RATE_KG_HR = 0.25
_OXIDE_TINY_RATE_KG_HR = 1.0e-15
_OXIDE_NA_RATE_KG_HR = 0.1
_OXIDE_CHARGE_KG = {"K2O": 1.0, "Na2O": 10.0}
_OXIDE_KINDS = (
    "computed",
    "computed_tiny",
    "refused",
    "unavailable",
    "physical_zero",
)


def _oxide_inventory_fields(mode: str) -> dict[str, object]:
    """Snapshot fields for one inventory mode. Kilograms are exact."""

    if mode == "missing":
        return {}
    if mode == "empty":
        return {"inventory": SimpleNamespace(melt_oxide_kg={})}
    if mode == "recorded":
        return {
            "inventory": SimpleNamespace(melt_oxide_kg=dict(_OXIDE_CHARGE_KG))
        }
    if mode == "composition":
        # 50/50 of 22 kg is 11 kg of K2O and 11 kg of Na2O, not the
        # recorded 1 kg / 10 kg pair. The conservation check uses this
        # composition's own kilograms. 50 and 22 are exact in binary.
        return {
            "melt_mass_kg": 22.0,
            "composition_wt_pct": {"K2O": 50.0, "Na2O": 50.0},
        }
    raise AssertionError(mode)


def _oxide_charge_kg(mode: str) -> dict[str, float]:
    if mode == "recorded":
        return dict(_OXIDE_CHARGE_KG)
    if mode == "composition":
        return {"K2O": 11.0, "Na2O": 11.0}
    return {}


def _oxide_channel(species: str, *, unavailable: bool) -> dict[str, object]:
    record = _alkali_inventory_channel(
        species,
        pressure_pa=_OXIDE_HK_PRESSURE_PA,
        parent_oxide=_OXIDE_PARENT[species],
        stoich_oxide_per_vapor=1.0,
    )
    if unavailable:
        record["activity_one_psat_pa"] = None
    return record


def _oxide_conservation_snapshot(
    hour: int,
    interval: Mapping[str, object],
) -> SimpleNamespace:
    kinds = dict(interval["kinds"])
    duration_h = float(interval["duration_h"])
    rates: dict[str, float] = {}
    channels: dict[str, dict[str, object]] = {}
    physical = False
    for name, kind in kinds.items():
        if kind == "computed":
            rate = (
                _OXIDE_NA_RATE_KG_HR
                if name == "Na2O_gas"
                else _OXIDE_COMPUTED_RATE_KG_HR
            )
            rates[name] = rate
            channels[name] = _oxide_channel(name, unavailable=False)
        elif kind == "computed_tiny":
            rates[name] = _OXIDE_TINY_RATE_KG_HR
            channels[name] = _oxide_channel(name, unavailable=False)
        elif kind == "refused":
            channels[name] = _oxide_channel(name, unavailable=False)
        elif kind == "unavailable":
            channels[name] = _oxide_channel(name, unavailable=True)
        elif kind == "physical_zero":
            physical = True
        else:
            raise AssertionError(kind)
    flux: dict[str, object] = {
        "published_species_kg_hr": rates,
        "species_kg_hr": dict(rates),
        "melt_surface_area_m2": _ACTIVITY_ONE_AREA_M2,
        "carrier_authority_by_species": channels,
    }
    if physical:
        flux["wall_supply_interval_physical_zero"] = {
            "reason": "frozen_melt",
            "evidence": "authorized_zero",
        }
    snapshot = SimpleNamespace(
        hour=hour,
        duration_h=duration_h,
        temperature_C=_ACTIVITY_ONE_T_C,
        evap_flux=SimpleNamespace(**flux),
        **_oxide_inventory_fields(str(interval["inventory_mode"])),
    )
    return snapshot


def _oxide_case_snapshots(
    case: Mapping[str, object],
) -> tuple[SimpleNamespace, ...]:
    return tuple(
        _oxide_conservation_snapshot(index, interval)
        for index, interval in enumerate(case["intervals"], start=1)
    )


def _oxide_case_facts(
    case: Mapping[str, object],
) -> dict[str, object]:
    """Charge, computed debit, and interval classes, read off the case."""

    charge: dict[str, float] = {}
    recorded = False
    computed = {name: 0.0 for name in _OXIDE_CARRIERS}
    refused = {name: False for name in _OXIDE_CARRIERS}
    unavailable = {name: False for name in _OXIDE_CARRIERS}
    physical = {name: False for name in _OXIDE_CARRIERS}
    missing_refused = {name: False for name in _OXIDE_CARRIERS}
    positive_hours = 0
    for interval in case["intervals"]:
        mode = str(interval["inventory_mode"])
        if mode != "missing":
            recorded = True
        for oxide, kg in _oxide_charge_kg(mode).items():
            charge.setdefault(oxide, kg)
        duration_h = float(interval["duration_h"])
        if duration_h == 0.0:
            continue
        positive_hours += 1
        for name, kind in interval["kinds"].items():
            if kind == "computed":
                rate = (
                    _OXIDE_NA_RATE_KG_HR
                    if name == "Na2O_gas"
                    else _OXIDE_COMPUTED_RATE_KG_HR
                )
                computed[name] += rate * duration_h
            elif kind == "computed_tiny":
                computed[name] += _OXIDE_TINY_RATE_KG_HR * duration_h
            elif kind == "refused":
                refused[name] = True
                if mode == "missing":
                    missing_refused[name] = True
            elif kind == "unavailable":
                unavailable[name] = True
            elif kind == "physical_zero":
                physical[name] = True
    return {
        "charge": charge,
        "recorded": recorded,
        "computed": computed,
        "refused": refused,
        "unavailable": unavailable,
        "physical": physical,
        "missing_refused": missing_refused,
        "positive_hours": positive_hours,
    }


def _accounted_parent_oxide_kg(
    bounds: Mapping[str, float],
    computed: Mapping[str, float],
    parent: str,
) -> float:
    """Published oxide plus the computed debit of unpublished carriers."""

    total = 0.0
    for name, oxide in _OXIDE_PARENT.items():
        if oxide != parent:
            continue
        if name in bounds:
            total += float(bounds[name])
        else:
            total += float(computed[name])
    return total


def _assert_shared_parent_oxide_invariant(
    bounds: object,
    case: Mapping[str, object],
) -> None:
    facts = _oxide_case_facts(case)
    charge = facts["charge"]
    computed = facts["computed"]
    for parent in set(_OXIDE_PARENT.values()):
        computed_oxide = sum(
            float(computed[name])
            for name, oxide in _OXIDE_PARENT.items()
            if oxide == parent
        )
        accounted = _accounted_parent_oxide_kg(bounds, computed, parent)
        if parent in charge:
            limit = max(float(charge[parent]), computed_oxide)
        elif facts["recorded"]:
            # A recorded map that omits the oxide is a known zero.
            limit = computed_oxide
        else:
            limit = None
        if limit is not None:
            assert accounted <= limit + 1.0e-9
    for name in _OXIDE_CARRIERS:
        if facts["unavailable"][name]:
            assert name not in bounds
            continue
        # A recorded map, including an empty one, is a known charge.
        # Withholding is only a window that never recorded an inventory.
        withheld_for_inventory = (
            facts["refused"][name]
            and facts["missing_refused"][name]
            and not facts["recorded"]
        )
        if withheld_for_inventory:
            assert name not in bounds
            assert (
                bounds.supply_unavailable[name]
                == MISSING_PARENT_OXIDE_INVENTORY
            )
            continue
        if (
            facts["computed"][name] > 0.0
            or facts["refused"][name]
            or facts["physical"][name]
        ):
            assert name in bounds
        if name in bounds:
            assert float(bounds[name]) + 1.0e-15 >= float(
                facts["computed"][name]
            )
    na_hours = facts["positive_hours"]
    # Na2O_gas is computed on every positive hour of these cases.
    if all(
        interval["kinds"]["Na2O_gas"] == "computed"
        for interval in case["intervals"]
    ):
        assert bounds["Na2O_gas"] == _approx_kg(
            _OXIDE_NA_RATE_KG_HR * na_hours
        )


def _oxide_interval(
    mode: str,
    kinds: Mapping[str, str],
    *,
    duration_h: float = 1.0,
) -> dict[str, object]:
    return {
        "duration_h": duration_h,
        "inventory_mode": mode,
        "kinds": dict(kinds),
    }


def _oxide_conservation_cases() -> tuple[dict[str, object], ...]:
    cases: list[dict[str, object]] = []
    for mode in ("recorded", "missing", "empty", "composition"):
        for kind in _OXIDE_KINDS:
            cases.append(
                {
                    "name": f"one-{mode}-{kind}",
                    "intervals": (
                        _oxide_interval(
                            mode,
                            {
                                "K2O_gas": kind,
                                "K2": "refused",
                                "Na2O_gas": "computed",
                            },
                        ),
                    ),
                }
            )
    for first_mode, second_mode in (
        ("recorded", "recorded"),
        ("recorded", "missing"),
        ("missing", "recorded"),
        ("composition", "missing"),
        ("empty", "recorded"),
        ("empty", "missing"),
    ):
        for first_kind in _OXIDE_KINDS:
            for second_kind in _OXIDE_KINDS:
                cases.append(
                    {
                        "name": (
                            f"{first_mode}-{first_kind}"
                            f"__{second_mode}-{second_kind}"
                        ),
                        "intervals": (
                            _oxide_interval(
                                first_mode,
                                {
                                    "K2O_gas": first_kind,
                                    "K2": "refused",
                                    "Na2O_gas": "computed",
                                },
                            ),
                            _oxide_interval(
                                second_mode,
                                {
                                    "K2O_gas": second_kind,
                                    "K2": "refused",
                                    "Na2O_gas": "computed",
                                },
                            ),
                        ),
                    }
                )
    cases.append(
        {
            "name": "zero-duration-missing-hour-is-outside-the-window",
            "intervals": (
                _oxide_interval(
                    "recorded",
                    {
                        "K2O_gas": "refused",
                        "K2": "refused",
                        "Na2O_gas": "computed",
                    },
                ),
                _oxide_interval(
                    "missing",
                    {
                        "K2O_gas": "refused",
                        "K2": "refused",
                        "Na2O_gas": "computed",
                    },
                    duration_h=0.0,
                ),
            ),
        }
    )
    return tuple(cases)


_OXIDE_CONSERVATION_CASES = _oxide_conservation_cases()


@pytest.mark.parametrize(
    "case",
    _OXIDE_CONSERVATION_CASES,
    ids=[case["name"] for case in _OXIDE_CONSERVATION_CASES],
)
def test_shared_parent_oxide_conservation(
    case: Mapping[str, object],
) -> None:
    """Every carrier of one oxide spends that oxide once.

    Computed, refused, unavailable, physical-zero, and below-threshold
    hours are included, as are recorded, composition, empty, and missing
    inventories, in both orders. Sodium does not spend potassium. A
    zero-duration hour is not a gap and not a debit. Species order does
    not change the kilograms.
    """

    assert _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA) > 1.0
    snapshots = _oxide_case_snapshots(case)
    orders = (_OXIDE_CARRIERS, tuple(reversed(_OXIDE_CARRIERS)))
    results = [
        _optimizer_refused_flux_bounds(snapshots, order) for order in orders
    ]
    for bounds in results:
        _assert_shared_parent_oxide_invariant(bounds, case)
    assert dict(results[0]) == dict(results[1])
    assert results[0].inventory_capped == results[1].inventory_capped
    assert results[0].supply_unavailable == results[1].supply_unavailable


def _k2o_channel(
    species: str,
    *,
    stoich: float,
    unavailable: bool = False,
) -> dict[str, object]:
    record = _alkali_inventory_channel(
        species,
        pressure_pa=_OXIDE_HK_PRESSURE_PA,
        parent_oxide="K2O",
        stoich_oxide_per_vapor=stoich,
    )
    if unavailable:
        record["activity_one_psat_pa"] = None
    return record


def _k2o_budget_snapshot(
    hour: int,
    *,
    rates: Mapping[str, float],
    channels: Mapping[str, Mapping[str, object]],
    inventory_kg: float | None,
) -> SimpleNamespace:
    snapshot = SimpleNamespace(
        hour=hour,
        duration_h=1.0,
        temperature_C=_ACTIVITY_ONE_T_C,
        evap_flux=SimpleNamespace(
            published_species_kg_hr=dict(rates),
            species_kg_hr=dict(rates),
            melt_surface_area_m2=_ACTIVITY_ONE_AREA_M2,
            carrier_authority_by_species={
                name: dict(record) for name, record in channels.items()
            },
        ),
    )
    if inventory_kg is not None:
        snapshot.inventory = SimpleNamespace(
            melt_oxide_kg={"K2O": inventory_kg}
        )
    return snapshot


def _withheld_k2o_gas_snapshots() -> tuple[SimpleNamespace, SimpleNamespace]:
    """0.25 kg computed K2O_gas, then a typed gap. K2 stays refused.

    Stoich of K2 is 2 kg oxide per kg vapour. The 0.25 kg computed
    draw is 0.25 kg of the 1 kg charge. K2's refused bound has the
    other 0.75 kg of oxide, which is 0.375 kg of vapour.
    """

    k2o = _k2o_channel("K2O_gas", stoich=1.0)
    k2 = _k2o_channel("K2", stoich=2.0)
    k2o_gap = _k2o_channel("K2O_gas", stoich=1.0, unavailable=True)
    return (
        _k2o_budget_snapshot(
            1,
            rates={"K2O_gas": 0.25},
            channels={"K2O_gas": k2o, "K2": k2},
            inventory_kg=1.0,
        ),
        _k2o_budget_snapshot(
            2,
            rates={},
            channels={"K2O_gas": k2o_gap, "K2": k2},
            inventory_kg=1.0,
        ),
    )


def test_withheld_computed_debit_stays_in_the_shared_reserve() -> None:
    """A gap in K2O_gas does not give its measured draw back to K2.

    1 kg of K2O. Hour 1 computes 0.25 kg of K2O_gas and refuses K2.
    Hour 2 has no flux law for K2O_gas, so K2O_gas is not published.
    K2's oxide is 0.75 kg, not the whole charge. Both species orders
    agree. The kinetic ceiling is above that remainder.
    """

    assert _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA) > 1.0
    snapshots = _withheld_k2o_gas_snapshots()
    for order in (("K2O_gas", "K2"), ("K2", "K2O_gas")):
        bounds = _optimizer_refused_flux_bounds(snapshots, order)
        assert "K2O_gas" not in bounds
        assert bounds["K2"] == _approx_kg(0.375)
        assert bounds["K2"] * 2.0 == _approx_kg(0.75)
        assert bounds["K2"] * 2.0 + 0.25 == _approx_kg(1.0)
        assert bounds.inventory_capped == {"K2": True}
    execution = _refused_supply_execution(
        snapshots,
        (("K2O_gas", "hot_wall"), ("K2", "hot_wall")),
    )
    booked = objective_module._book_wall_deposits_at_vapour_supply(
        {},
        execution.trace.wall_deposit_sticking_authority,
        execution,
    )
    assert booked["notice_authority"][INVENTORY_CAPPED] == {"K2": True}
    assert SUPPLY_UNAVAILABLE not in booked["notice_authority"]


def test_dropping_a_withheld_computed_debit_respends_the_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reserve is what stops K2 at 0.75 kg of oxide.

    Replacing the computed-debit reader with an empty map is the defect.
    K2 then receives the whole 1 kg of oxide (0.5 kg of vapour at stoich
    2). With the 0.25 kg already computed, the melt would owe 1.25 kg.
    """

    monkeypatch.setattr(
        evaluate_module,
        "_computed_parent_oxide_draws",
        lambda *args, **kwargs: evaluate_module._empty_oxide_draws(),
    )
    bounds = _optimizer_refused_flux_bounds(
        _withheld_k2o_gas_snapshots(),
        ("K2O_gas", "K2"),
    )
    assert bounds["K2"] == _approx_kg(0.5)
    assert bounds["K2"] * 2.0 + 0.25 == _approx_kg(1.25)
    assert bounds["K2"] * 2.0 + 0.25 > 1.0


def _exhausted_then_unrecorded_snapshots(
    mode: str,
) -> tuple[SimpleNamespace, ...]:
    channel = _k2o_channel("K2O_gas", stoich=1.0)
    first = _k2o_budget_snapshot(
        1,
        rates={},
        channels={"K2O_gas": channel},
        inventory_kg=None,
    )
    if mode == "recorded":
        first.inventory = SimpleNamespace(melt_oxide_kg={"K2O": 1.0})
    elif mode == "composition":
        first.melt_mass_kg = 1.0
        first.composition_wt_pct = {"K2O": 100.0}
    elif mode == "empty":
        first.inventory = SimpleNamespace(melt_oxide_kg={})
    elif mode == "missing":
        pass
    else:
        raise AssertionError(mode)
    if mode == "missing":
        return (first,)
    second = _k2o_budget_snapshot(
        2,
        rates={},
        channels={"K2O_gas": channel},
        inventory_kg=None,
    )
    return (first, second)


@pytest.mark.parametrize("mode", ("recorded", "composition", "empty", "missing"))
def test_missing_inventory_cannot_publish_the_kinetic_ceiling(mode: str) -> None:
    """A later hour with no inventory spends only the remaining charge.

    Recorded and composition each establish 1 kg and the refused bound
    takes it. The next hour has a kinetic ceiling and no inventory, and
    adds nothing. An empty map is a known zero, so the bound is 0 and
    flagged. A run that never records the oxide is typed unavailable.
    """

    kinetic = _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA)
    assert kinetic > 1.0
    bounds = _optimizer_refused_flux_bounds(
        _exhausted_then_unrecorded_snapshots(mode),
        ("K2O_gas",),
    )
    if mode == "missing":
        assert "K2O_gas" not in bounds
        assert (
            bounds.supply_unavailable["K2O_gas"]
            == MISSING_PARENT_OXIDE_INVENTORY
        )
        execution = _refused_supply_execution(
            _exhausted_then_unrecorded_snapshots(mode),
            (("K2O_gas", "hot_wall"),),
        )
        booked = objective_module._book_wall_deposits_at_vapour_supply(
            {},
            execution.trace.wall_deposit_sticking_authority,
            execution,
        )
        assert booked["notice_authority"][SUPPLY_UNAVAILABLE] == {
            "K2O_gas": MISSING_PARENT_OXIDE_INVENTORY
        }
        return
    if mode == "empty":
        assert bounds["K2O_gas"] == 0.0
    else:
        assert bounds["K2O_gas"] == _approx_kg(1.0)
    assert bounds["K2O_gas"] < kinetic
    assert bounds.inventory_capped == {"K2O_gas": True}


def test_missing_inventory_keeps_a_partial_computed_draw() -> None:
    """0.25 kg computed, then a refused hour with no inventory, is 1 kg.

    The second hour's kinetic ceiling is not added beside the charge.
    The computed draw is published because this species has no typed gap.
    """

    kinetic = _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA)
    assert kinetic > 1.0
    channel = _k2o_channel("K2O_gas", stoich=1.0)
    snapshots = (
        _k2o_budget_snapshot(
            1,
            rates={"K2O_gas": 0.25},
            channels={"K2O_gas": channel},
            inventory_kg=1.0,
        ),
        _k2o_budget_snapshot(
            2,
            rates={},
            channels={"K2O_gas": channel},
            inventory_kg=None,
        ),
    )
    bounds = _optimizer_refused_flux_bounds(snapshots, ("K2O_gas",))
    assert bounds["K2O_gas"] == _approx_kg(1.0)
    assert bounds["K2O_gas"] < 0.25 + kinetic
    assert bounds.inventory_capped == {"K2O_gas": True}


def _unrestricted_parent_oxide_allocation(classified_window, catalog):
    """The defect: every refused hour contributes its kinetic mass."""

    del catalog
    rows = []
    for snapshot, classified in classified_window:
        duration_h = float(getattr(snapshot, "duration_h", 1.0) or 0.0)
        row = {}
        for species, contribution in classified:
            if (
                contribution.kind == "supply_interval_upper_bound"
                and contribution.rate_kg_hr is not None
            ):
                row[species] = float(contribution.rate_kg_hr) * duration_h
        rows.append(row)
    return evaluate_module._ParentOxideAllocation(
        tuple(rows),
        frozenset(),
        frozenset(),
        {},
    )


@pytest.mark.parametrize("mode", ("recorded", "composition", "empty", "missing"))
def test_unrestricted_refused_mass_breaks_the_parent_oxide_budget(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publishing the kinetic ceiling on these hours exceeds the charge.

    The mutation replaces the one allocator. Recorded and composition
    then book the ceiling twice. An empty melt books the ceiling instead
    of zero. A run with no inventory publishes a number instead of the
    typed gap.
    """

    monkeypatch.setattr(
        evaluate_module,
        "_allocate_parent_oxide_supply",
        _unrestricted_parent_oxide_allocation,
    )
    kinetic = _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA)
    bounds = _optimizer_refused_flux_bounds(
        _exhausted_then_unrecorded_snapshots(mode),
        ("K2O_gas",),
    )
    assert "K2O_gas" in bounds
    assert bounds["K2O_gas"] > 1.0 or mode == "empty"
    assert bounds["K2O_gas"] >= kinetic


class _SnapshotParentCatalog:
    """Vapour law and parent stoich live only on the snapshot catalog."""

    def __init__(self) -> None:
        self._law = _CeilingLaw(
            activity_exponent=0.0,
            pO2_exponent=0.0,
            pO2_reference_bar=1.0,
            p_unit_pa=_OXIDE_HK_PRESSURE_PA,
        )

    def evaluator_for_hot_train(self, species: str) -> _CeilingLaw:
        if species != "K2O_gas":
            raise ValueError(species)
        return self._law

    def legacy_view(self) -> dict[str, dict[str, dict[str, float | str]]]:
        return {
            "oxide_vapors": {
                "K2O_gas": {
                    "parent_oxide": "K2O",
                    "stoich_oxide_per_vapor": 1.0,
                    "molar_mass_g_mol": _ACTIVITY_ONE_MASS_KG_PER_MOL * 1000.0,
                }
            }
        }


def _snapshot_catalog_hour(
    hour: int,
    *,
    computed_kg_hr: float | None,
    inventory_kg: float,
    catalog: _SnapshotParentCatalog,
) -> SimpleNamespace:
    """Bare refusal channel: no parent, no stoich, no explicit ceiling."""

    rates = {} if computed_kg_hr is None else {"K2O_gas": computed_kg_hr}
    return SimpleNamespace(
        hour=hour,
        duration_h=1.0,
        temperature_C=_ACTIVITY_ONE_T_C,
        vapour_catalog=catalog,
        inventory=SimpleNamespace(melt_oxide_kg={"K2O": inventory_kg}),
        evap_flux=SimpleNamespace(
            published_species_kg_hr=rates,
            species_kg_hr=dict(rates),
            melt_surface_area_m2=_ACTIVITY_ONE_AREA_M2,
            carrier_authority_by_species={
                "K2O_gas": _transport_refusal_channel("K2O_gas"),
            },
        ),
    )


def test_snapshot_catalog_stoich_caps_a_refused_to_computed_transition() -> None:
    """Parent and stoich are on the snapshot catalog, not the carrier.

    Pressure bounding already reads that catalog. The inventory cap must
    use the same object, or a refused hour books the whole charge and the
    later computed hour books it again. Both interval orders stay inside
    the 1 kg of K2O. The kinetic ceiling is the handwritten rate above.
    """

    catalog = _SnapshotParentCatalog()
    kinetic = _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA)
    assert kinetic > 1.0
    charge = 1.0
    computed = 0.25
    orders = (
        (
            _snapshot_catalog_hour(
                1, computed_kg_hr=None, inventory_kg=charge, catalog=catalog
            ),
            _snapshot_catalog_hour(
                2, computed_kg_hr=computed, inventory_kg=charge, catalog=catalog
            ),
        ),
        (
            _snapshot_catalog_hour(
                1, computed_kg_hr=computed, inventory_kg=charge, catalog=catalog
            ),
            _snapshot_catalog_hour(
                2, computed_kg_hr=None, inventory_kg=charge, catalog=catalog
            ),
        ),
    )
    for snapshots in orders:
        bounds = _optimizer_refused_flux_bounds(snapshots, ("K2O_gas",))
        assert bounds["K2O_gas"] == _approx_kg(charge)
        assert bounds["K2O_gas"] < kinetic
        assert bounds.inventory_capped == {"K2O_gas": True}


class _WrongElementalMassCatalog:
    """Accepted bound catalog whose K mass is not the stoich owner's."""

    def evaluator_for_hot_train(self, species: str) -> _CeilingLaw:
        raise ValueError(species)

    def legacy_view(self) -> dict[str, dict[str, dict[str, float]]]:
        return {"metals": {"K": {"molar_mass_g_mol": 50.0}}}


def test_elemental_inventory_cap_uses_evaporation_stoich_not_catalog_mass() -> None:
    """Elemental K debits K2O through ``_evaporation_stoich``.

    The catalog row says K is 50 g/mol. Production routing does not.
    The owner's ratio is ``STOICH_RATIOS`` for K2O, the handwritten
    M(K2O) / (2 * M(K)). A computed K2O_gas draw is reserved first, and
    the remaining oxide is converted to K with that ratio, not with 50.
    """

    host = EvaporationMixin.__new__(EvaporationMixin)
    host.species_formula_registry = {}
    owned = host._evaporation_stoich("K", {"parent_oxide": "K2O"})
    owner_ratio = float(owned["oxide_per_product_kg"])
    assert owner_ratio == pytest.approx(_K_SANITY_OXIDE_PER_VAPOUR, rel=1e-12)
    catalog_ratio = (94.1956 / 2.0) / 50.0
    assert catalog_ratio != pytest.approx(owner_ratio, rel=1e-6)
    charge = 0.01
    computed_oxide = 0.004
    remainder = charge - computed_oxide
    assert _oracle_hk_kg_hr(_OXIDE_HK_PRESSURE_PA) > remainder / owner_ratio
    catalog = _WrongElementalMassCatalog()
    k_channel = _alkali_inventory_channel(
        "K",
        pressure_pa=_OXIDE_HK_PRESSURE_PA,
        parent_oxide="K2O",
        stoich_oxide_per_vapor=None,
    )
    k2o_channel = _alkali_inventory_channel(
        "K2O_gas",
        pressure_pa=_OXIDE_HK_PRESSURE_PA,
        parent_oxide="K2O",
        stoich_oxide_per_vapor=1.0,
    )
    snapshot = SimpleNamespace(
        hour=1,
        duration_h=1.0,
        temperature_C=_ACTIVITY_ONE_T_C,
        vapour_catalog=catalog,
        inventory=SimpleNamespace(melt_oxide_kg={"K2O": charge}),
        evap_flux=SimpleNamespace(
            published_species_kg_hr={"K2O_gas": computed_oxide},
            species_kg_hr={"K2O_gas": computed_oxide},
            melt_surface_area_m2=_ACTIVITY_ONE_AREA_M2,
            carrier_authority_by_species={
                "K": k_channel,
                "K2O_gas": k2o_channel,
            },
        ),
    )
    expected_k = remainder / owner_ratio
    catalog_k = remainder / catalog_ratio
    for order in (("K", "K2O_gas"), ("K2O_gas", "K")):
        bounds = _optimizer_refused_flux_bounds(
            (snapshot,),
            order,
            catalog=catalog,
        )
        assert bounds["K2O_gas"] == _approx_kg(computed_oxide)
        assert bounds["K"] == _approx_kg(expected_k)
        assert bounds["K"] != _approx_kg(catalog_k)
        oxide_kg = bounds["K"] * owner_ratio + bounds["K2O_gas"]
        assert oxide_kg == _approx_kg(charge)
        assert bounds.inventory_capped == {"K": True}


def test_computed_activity_beats_activity_one_and_the_cap_is_the_floor() -> None:
    """A computed activity replaces activity 1. The inventory cap does not.

    The catalog law is ``P = P_unit * a^n`` with n = 1 and no oxygen
    dependence, so activity 0.25 is a quarter of the activity-1 Hertz-
    Knudsen rate. A K2O inventory above that rate leaves the estimate.
    An inventory below it binds, flags ``inventory_capped``, and books
    the oxide rather than the activity-scaled ceiling. A refusal is not
    an activity. An explicit ceiling with no activity exponent stays at
    activity 1, because ``a^n`` is not defined.
    """

    species = "K2O_gas"
    activity = 0.25
    activity_one = _activity_one_j_max_kg_hr()
    scaled = activity * activity_one
    oxide_cap_kg = 1.0e-5
    assert scaled > oxide_cap_kg
    catalog = _CeilingCatalog(
        {
            species: _CeilingLaw(
                activity_exponent=1.0,
                pO2_exponent=0.0,
                pO2_reference_bar=1.0,
                p_unit_pa=_ACTIVITY_ONE_PSAT_PA,
            )
        },
        {species: _ACTIVITY_ONE_MASS_KG_PER_MOL * 1000.0},
    )

    def snapshot(
        *,
        activity_payload: Mapping[str, object] | None,
        inventory_kg: float | None = None,
    ) -> SimpleNamespace:
        record = _transport_refusal_channel(species)
        record["molar_mass_kg_per_mol"] = _ACTIVITY_ONE_MASS_KG_PER_MOL
        if activity_payload is not None:
            record["source_reaction_activity"] = dict(activity_payload)
        if inventory_kg is not None:
            record["parent_oxide"] = "K2O"
            record["stoich_oxide_per_vapor"] = 1.0
        built = SimpleNamespace(
            hour=1,
            duration_h=1.0,
            temperature_C=_ACTIVITY_ONE_T_C,
            vapour_catalog=catalog,
            evap_flux=SimpleNamespace(
                published_species_kg_hr={},
                species_kg_hr={},
                melt_surface_area_m2=_ACTIVITY_ONE_AREA_M2,
                carrier_authority_by_species={species: record},
            ),
        )
        if inventory_kg is not None:
            built.inventory = SimpleNamespace(
                melt_oxide_kg={"K2O": inventory_kg},
            )
        return built

    point = {"verdict": "Point", "value": activity}
    interval = _classify_wall_supply_interval(snapshot(activity_payload=point), species)
    assert interval.kind == "supply_interval_upper_bound"
    assert interval.rate_kg_hr == _approx_kg(scaled)
    unbounded = _optimizer_refused_flux_bounds(
        (snapshot(activity_payload=point),),
        (species,),
        campaigns_elapsed=1.0,
    )
    assert unbounded[species] == _approx_kg(scaled)
    assert unbounded[species] < activity_one
    assert unbounded.inventory_capped == {}
    at_activity_one = _optimizer_refused_flux_bounds(
        (snapshot(activity_payload=None),),
        (species,),
        campaigns_elapsed=1.0,
    )
    assert at_activity_one[species] == _approx_kg(activity_one)
    loose = _optimizer_refused_flux_bounds(
        (snapshot(activity_payload=point, inventory_kg=1.0),),
        (species,),
        campaigns_elapsed=1.0,
    )
    assert loose[species] == _approx_kg(scaled)
    assert loose.inventory_capped == {}
    capped = _optimizer_refused_flux_bounds(
        (snapshot(activity_payload=point, inventory_kg=oxide_cap_kg),),
        (species,),
        campaigns_elapsed=1.0,
    )
    assert capped[species] == _approx_kg(oxide_cap_kg)
    assert capped[species] < scaled
    assert capped.inventory_capped == {species: True}
    refused_activity = _optimizer_refused_flux_bounds(
        (
            snapshot(
                activity_payload={"verdict": "Refusal", "value": activity},
            ),
        ),
        (species,),
        campaigns_elapsed=1.0,
    )
    assert refused_activity[species] == _approx_kg(activity_one)
    explicit = _activity_one_refusal_snapshot(species, hour=1)
    explicit_record = explicit.evap_flux.carrier_authority_by_species[species]
    explicit_record["computed_activity"] = activity
    explicit_record["activity_exponent"] = 1.0
    explicit_interval = _classify_wall_supply_interval(explicit, species)
    assert explicit_interval.rate_kg_hr == _approx_kg(scaled)
    unknown_exponent = _activity_one_refusal_snapshot(species, hour=1)
    unknown_record = unknown_exponent.evap_flux.carrier_authority_by_species[species]
    unknown_record["computed_activity"] = activity
    unknown_interval = _classify_wall_supply_interval(unknown_exponent, species)
    assert unknown_interval.rate_kg_hr == _approx_kg(activity_one)


def test_one_refused_interval_among_computed_hours_is_booked() -> None:
    """One bounded refusal does not drop the computed hours."""

    species = "SiO"
    sibling = "Mg"
    expected_bound = _activity_one_j_max_kg_hr()
    snapshots = (
        _interval_flux_snapshot(1, 1.0, {species: 1.0, sibling: 0.5}),
        _activity_one_refusal_snapshot(
            species,
            hour=2,
            rates={sibling: 0.4},
        ),
    )
    bounds = _optimizer_refused_flux_bounds(
        snapshots,
        (species, sibling),
        campaigns_elapsed=1.0,
    )
    assert bounds[species] == _approx_kg(1.0 + expected_bound)
    assert bounds[sibling] == _approx_kg(0.9)
    assert bounds.supply_interval_upper_bound_used == {species: 1}

    execution = _refused_supply_execution(
        snapshots,
        ((species, "hot_wall"),),
    )
    booked = objective_module._book_wall_deposits_at_vapour_supply(
        {},
        execution.trace.wall_deposit_sticking_authority,
        execution,
    )
    booking = {item["species"]: item for item in booked["bookings"]}[species]
    assert booking["reason"] == _WALL_BOUND_UPPER
    assert booking["flag"] == _WALL_BOUND_UPPER
    assert booking["deposit_kg"] == _approx_kg(bounds[species])
    assert booking[SUPPLY_INTERVAL_UPPER_BOUND_USED] == 1
    assert booked["deposit"][("hot_wall", species)] == _approx_kg(bounds[species])


def test_sio_refused_wall_psat_books_a_flagged_coating_lifetime() -> None:
    """SiO's refused wall saturation pressure books the supply.

    The deposit is the supply, flagged wall_deposit_upper_bound_used.
    resinter_threshold_kg is null, so the campaign count stays the
    ungrounded ratio and the priced lifetime load is wall_deposit_total_kg.
    """

    species = "SiO"
    snapshot = _activity_one_refusal_snapshot(species, hour=1)
    supply = _activity_one_j_max_kg_hr()
    execution = _refused_supply_execution(
        (snapshot,),
        ((species, "hot_wall"),),
    )
    summary = objective_module._coating_product_summary(execution)
    assert summary["lifespan_cost_status"] == "threshold_unavailable"
    assert summary["wall_deposit_total_kg"] == _approx_kg(supply)
    assert summary["wall_deposit_upper_bound_used"] is True
    assert summary[SUPPLY_INTERVAL_UPPER_BOUND_USED] == {species: 1}
    assert str(summary["campaigns_to_resinter"]).startswith(
        "resinter_threshold_kg / "
    )
    # The refusal stays on the notice, so the readout is non-authoritative.
    # The quantity itself is the flagged supply, not an unavailable wall.
    assert summary["coating_status"] == "warning"
    booking = summary["wall_supply_bound_bookings"][0]
    assert booking["species"] == species
    assert booking["deposit_kg"] == _approx_kg(supply)
    assert booking["flag"] == _WALL_BOUND_UPPER
    lifespan = objective_module._furnace_lifespan_cost_summary(
        execution,
        execution.simulator,
    )
    assert lifespan["lifespan_cost_status"] == "threshold_unavailable"
    assert lifespan["wall_deposit_total_kg"] == _approx_kg(supply)
    assert lifespan["wall_deposit_upper_bound_used"] is True
    assert lifespan[SUPPLY_INTERVAL_UPPER_BOUND_USED] == {species: 1}


def test_unavailable_supply_is_only_no_flux_law_and_no_psat() -> None:
    """A typed gap is no flux law and no saturation pressure.

    An explicit absent activity-1 pressure does not fall through to the
    catalog. One such hour withholds the species, including a computed
    hour beside it. A refused hour that does have P_sat is not that gap.
    """

    species = "SiO"
    gap = _activity_one_refusal_snapshot(species, hour=2, pressure_pa=None)
    interval = _classify_wall_supply_interval(gap, species)
    assert interval.kind == "unavailable"
    assert interval.evidence == "no_flux_law_and_no_psat"
    snapshots = (
        _interval_flux_snapshot(1, 1.0, {species: 1.0}),
        gap,
    )
    bounds = _optimizer_refused_flux_bounds(
        snapshots,
        (species,),
        campaigns_elapsed=1.0,
    )
    assert species not in bounds
    execution = _refused_supply_execution(
        snapshots,
        ((species, "hot_wall"),),
    )
    summary = objective_module._coating_product_summary(execution)
    assert summary["coating_unavailable_reason"] == (
        "wall saturation pressure refused for SiO; "
        "vapour flux upper bound unavailable"
    )
    assert not summary.get("wall_supply_bound_bookings")

    bounded = _activity_one_refusal_snapshot(species, hour=1)
    bounded_interval = _classify_wall_supply_interval(bounded, species)
    assert bounded_interval.kind == "supply_interval_upper_bound"
    assert bounded_interval.evidence == SUPPLY_INTERVAL_UPPER_BOUND_USED


def _po2_hour(
    hour: int,
    pO2_bar: float | None,
    *,
    species: str | None = None,
    catalog: object | None = None,
    refused: bool = False,
) -> SimpleNamespace:
    """One hour. A refused carrier has no rate. A pO2 of None is unrecorded."""

    flux_fields: dict[str, object] = {
        "published_species_kg_hr": {},
        "species_kg_hr": {},
        "melt_surface_area_m2": _CEILING_AREA_M2,
    }
    if refused and species is not None:
        flux_fields["carrier_authority_by_species"] = {
            species: _transport_refusal_channel(species)
        }
    else:
        flux_fields["wall_supply_interval_physical_zero"] = {
            "reason": "non_evaporation_campaign",
            "evidence": "authorized_zero",
        }
    fields: dict[str, object] = {
        "hour": hour,
        "duration_h": 1.0,
        "temperature_C": _CEILING_T_C,
        "evap_flux": SimpleNamespace(**flux_fields),
    }
    if pO2_bar is not None:
        fields["oxygen_reservoir"] = {"headspace_transport_pO2_bar": pO2_bar}
    if catalog is not None:
        fields["vapour_catalog"] = catalog
    return SimpleNamespace(**fields)


def _literal_hk_kg_hr(pressure_pa: float, molar_mass_kg_per_mol: float) -> float:
    """Independent J_max. R and the square root are written here."""

    gas_constant = 8.314462618
    coefficient = math.sqrt(
        molar_mass_kg_per_mol
        / (2.0 * math.pi * gas_constant * _CEILING_T_K)
    )
    return pressure_pa * coefficient * _CEILING_AREA_M2 * 3600.0


def test_al2o_refused_hour_at_100_pa_overhead_is_1000x_one_bar() -> None:
    """Al2O (m=-1) at 100 Pa overhead, not at p_ref = 1 bar (d-401).

    P = P_unit * a^n * (pO2 / p_ref)^m with n=2, m=-1, p_ref=1 bar,
    P_unit=1e-4 Pa. 100 Pa is 1e-3 bar, so the oxygen factor is
    (1e-3)^(-1) = 1000. A later hour at 1e-6 bar must not replace this
    hour's own transport pO2. The kilogram rate is the handwritten
    Hertz-Knudsen literal at that ceiling, not the publisher helper
    and not the shipped Al2O fit.
    """

    overhead_pa = 100.0
    hour_pO2_bar = overhead_pa / 1.0e5
    p_unit_pa = 1.0e-4
    p_ref_bar = 1.0
    oxygen_exponent = -1.0
    activity_exponent = 2.0
    mass_kg_per_mol = 0.070
    pressure_pa = (
        p_unit_pa
        * (1.0 ** activity_exponent)
        * (hour_pO2_bar / p_ref_bar) ** oxygen_exponent
    )
    pressure_at_one_bar_pa = p_unit_pa * (1.0 / p_ref_bar) ** oxygen_exponent
    assert pressure_pa / pressure_at_one_bar_pa == pytest.approx(1000.0)
    expected = _literal_hk_kg_hr(pressure_pa, mass_kg_per_mol)
    one_bar = _literal_hk_kg_hr(pressure_at_one_bar_pa, mass_kg_per_mol)
    assert expected / one_bar == pytest.approx(1000.0)
    catalog = _CeilingCatalog(
        {
            "Al2O": _CeilingLaw(
                activity_exponent=activity_exponent,
                pO2_exponent=oxygen_exponent,
                pO2_reference_bar=p_ref_bar,
                p_unit_pa=p_unit_pa,
            )
        },
        {"Al2O": mass_kg_per_mol * 1000.0},
    )
    snapshots = (
        _po2_hour(1, hour_pO2_bar, species="Al2O", catalog=catalog, refused=True),
        _po2_hour(2, 1.0e-6, catalog=catalog),
    )
    bounds = _optimizer_refused_flux_bounds(
        snapshots,
        ("Al2O",),
        campaigns_elapsed=1.0,
    )
    assert bounds["Al2O"] == _approx_kg(expected)
    assert bounds["Al2O"] != _approx_kg(one_bar)
    assert bounds.supply_interval_upper_bound_used == {"Al2O": 1}


def test_positive_oxygen_exponent_uses_the_max_recorded_po2_hour() -> None:
    """CrO2-shaped m>0 with no pO2 on the refused hour uses the maximum.

    Recorded hours are 1e-6 bar and 1e-2 bar. P_unit is 1e-3 Pa,
    m=0.25, p_ref=1 bar. The ceiling is P_unit * (1e-2)^0.25, not the
    minimum and not the 1 bar reference (d-401).
    """

    p_unit_pa = 1.0e-3
    oxygen_exponent = 0.25
    low_bar = 1.0e-6
    high_bar = 1.0e-2
    mass_kg_per_mol = 0.070
    pressure_pa = p_unit_pa * (high_bar ** oxygen_exponent)
    pressure_at_min_pa = p_unit_pa * (low_bar ** oxygen_exponent)
    pressure_at_ref_pa = p_unit_pa
    assert pressure_pa != pytest.approx(pressure_at_min_pa)
    assert pressure_pa != pytest.approx(pressure_at_ref_pa)
    expected = _literal_hk_kg_hr(pressure_pa, mass_kg_per_mol)
    catalog = _CeilingCatalog(
        {
            "CrO2": _CeilingLaw(
                activity_exponent=1.0,
                pO2_exponent=oxygen_exponent,
                pO2_reference_bar=1.0,
                p_unit_pa=p_unit_pa,
            )
        },
        {"CrO2": mass_kg_per_mol * 1000.0},
    )
    snapshots = (
        _po2_hour(1, None, species="CrO2", catalog=catalog, refused=True),
        _po2_hour(2, low_bar, catalog=catalog),
        _po2_hour(3, high_bar, catalog=catalog),
    )
    forward = _optimizer_refused_flux_bounds(
        snapshots,
        ("CrO2",),
        campaigns_elapsed=1.0,
    )
    reverse = _optimizer_refused_flux_bounds(
        tuple(reversed(snapshots)),
        ("CrO2",),
        campaigns_elapsed=1.0,
    )
    assert forward["CrO2"] == _approx_kg(expected)
    assert reverse["CrO2"] == _approx_kg(expected)
    assert forward["CrO2"] != _approx_kg(
        _literal_hk_kg_hr(pressure_at_min_pa, mass_kg_per_mol)
    )
    assert forward["CrO2"] != _approx_kg(
        _literal_hk_kg_hr(pressure_at_ref_pa, mass_kg_per_mol)
    )


def test_no_recorded_po2_withholds_only_oxygen_dependent_laws() -> None:
    """No transport pO2 anywhere: m != 0 is unavailable and m = 0 is not.

    The gap is typed. It is not the 1e-9 guard and not p_ref (d-401).
    A CrO hour with temperature, area, and a transport pO2 but no run
    catalog stays unavailable: the shipped vapour file is not the law.
    """

    mass_kg_per_mol = 0.070
    p_unit_pa = 1.0e-3
    catalog = _CeilingCatalog(
        {
            "Al2O": _CeilingLaw(
                activity_exponent=2.0,
                pO2_exponent=-1.0,
                pO2_reference_bar=1.0,
                p_unit_pa=p_unit_pa,
            ),
            "M0Carrier": _CeilingLaw(
                activity_exponent=1.0,
                pO2_exponent=0.0,
                pO2_reference_bar=1.0,
                p_unit_pa=p_unit_pa,
            ),
        },
        {
            "Al2O": mass_kg_per_mol * 1000.0,
            "M0Carrier": mass_kg_per_mol * 1000.0,
        },
    )
    flux = SimpleNamespace(
        published_species_kg_hr={},
        species_kg_hr={},
        melt_surface_area_m2=_CEILING_AREA_M2,
        carrier_authority_by_species={
            "Al2O": _transport_refusal_channel("Al2O"),
            "M0Carrier": _transport_refusal_channel("M0Carrier"),
        },
    )
    snapshot = SimpleNamespace(
        hour=1,
        duration_h=1.0,
        temperature_C=_CEILING_T_C,
        vapour_catalog=catalog,
        evap_flux=flux,
    )
    al2o = _classify_wall_supply_interval(snapshot, "Al2O")
    assert al2o.kind == "unavailable"
    assert al2o.evidence == "missing_recorded_pO2"
    m0 = _classify_wall_supply_interval(snapshot, "M0Carrier")
    assert m0.kind == "supply_interval_upper_bound"
    assert m0.rate_kg_hr == _approx_kg(_literal_hk_kg_hr(p_unit_pa, mass_kg_per_mol))
    bounds = _optimizer_refused_flux_bounds(
        (snapshot,),
        ("Al2O", "M0Carrier"),
        campaigns_elapsed=1.0,
    )
    assert "Al2O" not in bounds
    assert bounds["M0Carrier"] == _approx_kg(m0.rate_kg_hr)

    cro = SimpleNamespace(
        hour=1,
        duration_h=1.0,
        temperature_C=_CEILING_T_C,
        oxygen_reservoir={"headspace_transport_pO2_bar": 1.0e-3},
        evap_flux=SimpleNamespace(
            published_species_kg_hr={},
            species_kg_hr={},
            melt_surface_area_m2=_CEILING_AREA_M2,
            carrier_authority_by_species={"CrO": _transport_refusal_channel("CrO")},
        ),
    )
    cro_interval = _classify_wall_supply_interval(cro, "CrO")
    assert cro_interval.kind == "unavailable"
    assert cro_interval.evidence == "no_flux_law_and_no_psat"


def _provenance_hour(
    *,
    record: object = None,
    reservoir_bar: float | None = None,
    transport_bar: float | None = None,
    overhead_bar: float | None = None,
    interval_bar: float | None = None,
) -> SimpleNamespace:
    """One interval. ``record is None`` means vapour evaluation did not run."""

    flux_fields: dict[str, object] = {}
    if record is not None:
        flux_fields["evaluated_transport_pO2"] = record
    fields: dict[str, object] = {"evap_flux": SimpleNamespace(**flux_fields)}
    if reservoir_bar is not None:
        fields["oxygen_reservoir"] = {
            "headspace_transport_pO2_bar": reservoir_bar
        }
    if transport_bar is not None:
        fields["transport_pO2_bar"] = transport_bar
    if overhead_bar is not None:
        fields["overhead_pO2_bar"] = overhead_bar
    if interval_bar is not None:
        fields["interval_pO2_bar"] = interval_bar
    return SimpleNamespace(**fields)


# Independent expected pressures. The evaluation record wins, including
# a recorded absence. Fixture fields apply only when that record is absent.
_PO2_PROVENANCE_CASES = (
    (
        "evaluated_beats_later_reservoir",
        {
            "record": {"pressure_bar": 1.0e-2, "evidence": "vapour_evaluation"},
            "reservoir_bar": 1.0e-3,
        },
        1.0e-2,
    ),
    (
        "recorded_absence_is_not_the_reservoir",
        {
            "record": {"pressure_bar": None, "evidence": "vapour_evaluation"},
            "reservoir_bar": 1.0e-3,
        },
        None,
    ),
    (
        "nonpositive_record_is_absence",
        {
            "record": {"pressure_bar": 0.0, "evidence": "vapour_evaluation"},
            "reservoir_bar": 1.0e-3,
        },
        None,
    ),
    (
        "wrong_evidence_is_not_an_evaluation",
        {
            "record": {"pressure_bar": 1.0e-2, "evidence": "reservoir"},
            "reservoir_bar": 1.0e-3,
        },
        1.0e-3,
    ),
    (
        "fixture_reservoir",
        {"reservoir_bar": 1.0e-3},
        1.0e-3,
    ),
    (
        "fixture_transport",
        {"transport_bar": 2.0e-3},
        2.0e-3,
    ),
    (
        "fixture_overhead",
        {"overhead_bar": 4.0e-3},
        4.0e-3,
    ),
    (
        "fixture_interval",
        {"interval_bar": 5.0e-3},
        5.0e-3,
    ),
    (
        "nothing_recorded",
        {},
        None,
    ),
    (
        "evaluated_without_a_reservoir",
        {"record": {"pressure_bar": 1.0e-2, "evidence": "vapour_evaluation"}},
        1.0e-2,
    ),
)


@pytest.mark.parametrize(
    ("_name", "fields", "expected_bar"),
    _PO2_PROVENANCE_CASES,
    ids=[case[0] for case in _PO2_PROVENANCE_CASES],
)
def test_ceiling_hour_po2_is_the_evaluated_pressure(
    _name: str,
    fields: dict[str, object],
    expected_bar: float | None,
) -> None:
    """Every oxygen-state class resolves to the pressure the flux used.

    The expected bar is written here. The reader and the oracle must
    both match it. A record with evidence ``vapour_evaluation`` is that
    pressure, and a recorded absence stays absent. Fixture fields are
    the author's pressure only when vapour evaluation left no record.
    """

    snapshot = _provenance_hour(**fields)
    assert evaluate_module._snapshot_transport_pO2_bar(snapshot) == expected_bar
    assert _oracle_transport_pO2_bar(snapshot) == expected_bar


def _reservoir_first_pO2_bar(snapshot: object) -> float | None:
    """The pre-fix reader: the post-bleed reservoir wins over the stamp."""

    reservoir = getattr(snapshot, "oxygen_reservoir", None)
    if isinstance(reservoir, Mapping) and "headspace_transport_pO2_bar" in reservoir:
        recorded = _oracle_positive_bar(reservoir.get("headspace_transport_pO2_bar"))
        if recorded is not None:
            return recorded
    for name in ("transport_pO2_bar", "overhead_pO2_bar", "interval_pO2_bar"):
        recorded = _oracle_positive_bar(getattr(snapshot, name, None))
        if recorded is not None:
            return recorded
    return None


@pytest.mark.parametrize(
    ("_name", "fields", "expected_bar"),
    [
        case
        for case in _PO2_PROVENANCE_CASES
        if case[2] != _reservoir_first_pO2_bar(_provenance_hour(**case[1]))
    ],
    ids=[
        case[0]
        for case in _PO2_PROVENANCE_CASES
        if case[2] != _reservoir_first_pO2_bar(_provenance_hour(**case[1]))
    ],
)
def test_reservoir_first_reader_fails_when_the_states_differ(
    _name: str,
    fields: dict[str, object],
    expected_bar: float | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading the reservoir after the bleed is not the evaluated pO2."""

    snapshot = _provenance_hour(**fields)
    legacy = _reservoir_first_pO2_bar(snapshot)
    assert legacy != expected_bar
    assert evaluate_module._snapshot_transport_pO2_bar(snapshot) == expected_bar
    monkeypatch.setattr(
        evaluate_module,
        "_snapshot_transport_pO2_bar",
        _reservoir_first_pO2_bar,
    )
    assert evaluate_module._snapshot_transport_pO2_bar(snapshot) == legacy
    assert evaluate_module._snapshot_transport_pO2_bar(snapshot) != expected_bar


def _m025_catalog() -> _CeilingCatalog:
    return _CeilingCatalog(
        {
            "M025": _CeilingLaw(
                activity_exponent=0.0,
                pO2_exponent=0.25,
                pO2_reference_bar=1.0,
                p_unit_pa=1.0,
            )
        },
        {"M025": 70.0},
    )


def _m025_refused_hour(flux: object, reservoir_bar: float) -> SimpleNamespace:
    return SimpleNamespace(
        hour=1,
        duration_h=1.0,
        temperature_C=_CEILING_T_C,
        oxygen_reservoir={"headspace_transport_pO2_bar": reservoir_bar},
        vapour_catalog=_m025_catalog(),
        evap_flux=flux,
    )


def _m025_hk_at(pO2_bar: float) -> float:
    """Handwritten ceiling. P = 1 Pa * (pO2 / 1 bar)^0.25, activity exponent 0."""

    pressure_pa = 1.0 * (pO2_bar ** 0.25)
    return _literal_hk_kg_hr(pressure_pa, 0.070)


def test_post_bleed_reservoir_does_not_shrink_the_activity_one_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """m=+0.25 evaluated at 1e-2 bar, snapshotted after bleeding at 1e-3.

    (1e-3 / 1e-2)^0.25 = 0.1^0.25 is about 0.562 of the required
    pressure bound. The ceiling keeps the pressure vapour evaluation
    consumed. A reader that uses the later reservoir books the smaller
    literal, and this test rejects that rate.
    """

    evaluated_bar = 1.0e-2
    bled_bar = 1.0e-3
    correct = _m025_hk_at(evaluated_bar)
    wrong = _m025_hk_at(bled_bar)
    assert wrong / correct == pytest.approx(0.1 ** 0.25)
    host = SimpleNamespace()
    remember_evaluated_transport_pO2(host, evaluated_bar)
    flux = SimpleNamespace(
        published_species_kg_hr={},
        species_kg_hr={},
        melt_surface_area_m2=_CEILING_AREA_M2,
        carrier_authority_by_species={"M025": _transport_refusal_channel("M025")},
    )
    attach_evaluated_transport_pO2(flux, host)
    snapshot = _m025_refused_hour(flux, bled_bar)
    bounds = _optimizer_refused_flux_bounds(
        (snapshot,),
        ("M025",),
        campaigns_elapsed=1.0,
    )
    assert bounds["M025"] == _approx_kg(correct)
    assert bounds["M025"] != _approx_kg(wrong)

    monkeypatch.setattr(
        evaluate_module,
        "_snapshot_transport_pO2_bar",
        _reservoir_first_pO2_bar,
    )
    mutated = _optimizer_refused_flux_bounds(
        (snapshot,),
        ("M025",),
        campaigns_elapsed=1.0,
    )
    assert mutated["M025"] == _approx_kg(wrong)
    assert mutated["M025"] != _approx_kg(correct)


def _kernel_vapour_host(
    diagnostic: dict[str, object],
    dispatch_bar: float,
    *,
    temperature_C: float,
) -> tuple[SimpleNamespace, list[object]]:
    """The real refresh, with the kernel replaced by this diagnostic."""

    from simulator.chemistry.kernel import ChemistryIntent
    from simulator.chemistry.kernel.dto import IntentResult

    calls: list[object] = []

    def _dispatch_only(intent: object, **kwargs: object) -> IntentResult:
        calls.append((intent, kwargs))
        return IntentResult(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            status="ok",
            diagnostic=dict(diagnostic),
            warnings=(),
        )

    host = SimpleNamespace(
        melt=SimpleNamespace(temperature_C=temperature_C),
        liquid_feo_domain_at=lambda _T_K: None,
        _allow_fallback_vapor=False,
        _last_backend_diagnostics={},
        _vapor_pressure_dispatch_pO2_bar=lambda: dispatch_bar,
        _dispatch_only=_dispatch_only,
        _kernel_vapor_pressure_source=(
            PyrolysisSimulator._kernel_vapor_pressure_source
        ),
        _vapor_pressure_values_agree=(
            PyrolysisSimulator._vapor_pressure_values_agree
        ),
        _evaluated_transport_pO2_bar=None,
        _evaluated_transport_pO2_recorded=False,
    )
    return host, calls


def test_kernel_refresh_keeps_the_evaluated_po2_after_the_reservoir_changes() -> None:
    """The stamp is written by vapour evaluation, then the reservoir moves.

    The provider's ``pO2_bar`` wins over the dispatch argument. A
    diagnostic that omits it uses the dispatch argument. A sub-400 K
    hour clears a stale stamp and does not dispatch.
    """

    from simulator.chemistry.kernel import ChemistryIntent

    evaluated_bar = 1.0e-2
    dispatch_bar = 1.0e-3
    host, calls = _kernel_vapour_host(
        {
            "vapor_pressures_Pa": {"M025": 1.0},
            "vapor_pressures_source": {"M025": "builtin_authoritative"},
            "pO2_bar": evaluated_bar,
        },
        dispatch_bar,
        temperature_C=1600.0,
    )
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={},
    )
    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(host, result)
    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]
    assert host._evaluated_transport_pO2_bar == evaluated_bar
    dispatched = calls[0][1]["control_inputs"]["pO2_bar"]
    assert dispatched == dispatch_bar
    flux = SimpleNamespace(
        published_species_kg_hr={},
        species_kg_hr={},
        melt_surface_area_m2=_CEILING_AREA_M2,
        carrier_authority_by_species={"M025": _transport_refusal_channel("M025")},
    )
    attach_evaluated_transport_pO2(flux, host)
    bled = _m025_refused_hour(flux, dispatch_bar)
    interval = _classify_wall_supply_interval(bled, "M025")
    assert interval.kind == "supply_interval_upper_bound"
    assert interval.rate_kg_hr == _approx_kg(_m025_hk_at(evaluated_bar))
    assert interval.rate_kg_hr != _approx_kg(_m025_hk_at(dispatch_bar))

    omitted, omitted_calls = _kernel_vapour_host(
        {
            "vapor_pressures_Pa": {"M025": 1.0},
            "vapor_pressures_source": {"M025": "builtin_authoritative"},
        },
        2.0e-2,
        temperature_C=1600.0,
    )
    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(
        omitted,
        EquilibriumResult(
            temperature_C=1600.0,
            pressure_bar=1e-6,
            liquid_fraction=1.0,
            vapor_pressures_Pa={},
        ),
    )
    assert omitted_calls
    assert omitted._evaluated_transport_pO2_recorded is True
    assert omitted._evaluated_transport_pO2_bar == 2.0e-2

    cold, cold_calls = _kernel_vapour_host(
        {"pO2_bar": 1.0e-2},
        1.0e-2,
        temperature_C=25.0,
    )
    cold._evaluated_transport_pO2_bar = 1.0e-2
    cold._evaluated_transport_pO2_recorded = True
    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(
        cold,
        EquilibriumResult(
            temperature_C=25.0,
            pressure_bar=1e-6,
            liquid_fraction=1.0,
            vapor_pressures_Pa={},
        ),
    )
    assert cold_calls == []
    assert cold._evaluated_transport_pO2_recorded is True
    assert cold._evaluated_transport_pO2_bar is None


# Cache consumption has no live dispatch argument. A positive pO2_bar on
# the restored diagnostic is the evaluation. Anything else is a recorded
# absence. The post-bleed reservoir is not an input of this oracle.
_CACHE_CONSUMPTION_DIAGNOSTICS = (
    ("provider_po2", {"pO2_bar": 1.0e-2}),
    ("omitted_po2", {"vapor_pressures_Pa": {"M025": 1.0}}),
    ("nonpositive_po2", {"pO2_bar": 0.0}),
    ("empty_diagnostic", {}),
)
_CACHE_CONSUMPTION_ENTRIES = ("replay", "write_through")
_CACHE_BLED_BAR = 1.0e-3


def _oracle_cached_evaluation_bar(diagnostic: Mapping[str, object]) -> float | None:
    """Pressure a cached vapour evaluation consumed.

    Written from d-401. This does not call the stamp helper. Cache
    consumption does not hold the live dispatch argument, so a missing
    or non-positive ``pO2_bar`` is absence. It is not the reservoir the
    hour bleeds to afterwards.
    """

    raw = diagnostic.get("pO2_bar")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    pressure = float(raw)
    if not math.isfinite(pressure) or pressure <= 0.0:
        return None
    return pressure


def _cache_stamp_host() -> SimpleNamespace:
    """Hour state after the reset and before cache consumption."""

    return SimpleNamespace(
        melt=SimpleNamespace(fO2_log=-9.0, melt_fO2_log=-9.0),
        _evaluated_transport_pO2_bar=None,
        _evaluated_transport_pO2_recorded=False,
    )


def _cache_equilibrium_payload(diagnostic: Mapping[str, object]) -> dict[str, object]:
    return {
        "equilibrium_result": {
            "temperature_C": 1600.0,
            "pressure_bar": 1.0e-6,
            "liquid_fraction": 1.0,
            "status": "ok",
        },
        "last_vapor_pressures_source": {},
        "last_vapor_pressure_diagnostic": dict(diagnostic),
    }


def _consume_cached_equilibrium(
    entry: str,
    host: SimpleNamespace,
    payload: Mapping[str, object],
    db_path: object,
) -> object:
    """Drive the real replay or write-through return, not a copied stamp."""

    from simulator.reduced_real_determinism import PT0DeterminismStore

    if entry == "replay":
        store = PT0DeterminismStore("replay")
        setattr(store, "_authorize_cached_real_replay", lambda sim, **kwargs: None)
        setattr(store, "_equilibrium_key", lambda sim: {"namespace_id": "stamp"})
        setattr(store, "_lookup", lambda artifact, key, sim=None: payload)
        return store.replay_equilibrium(host)
    if entry != "write_through":
        raise AssertionError(entry)
    store = PT0DeterminismStore("capture", db_path=db_path)
    assert store.write_through_enabled
    setattr(store, "_cached_real_binding_eligible", lambda sim, **kwargs: True)
    setattr(store, "_equilibrium_key", lambda sim: {"namespace_id": "stamp"})
    setattr(store, "_lookup_optional", lambda artifact, key, **kwargs: payload)
    result = store.cached_equilibrium(host)
    assert result is not None
    return result


def _m025_refused_flux() -> SimpleNamespace:
    return SimpleNamespace(
        published_species_kg_hr={},
        species_kg_hr={},
        melt_surface_area_m2=_CEILING_AREA_M2,
        carrier_authority_by_species={"M025": _transport_refusal_channel("M025")},
    )


@pytest.mark.parametrize("entry", _CACHE_CONSUMPTION_ENTRIES)
@pytest.mark.parametrize(
    ("_name", "diagnostic"),
    _CACHE_CONSUMPTION_DIAGNOSTICS,
    ids=[case[0] for case in _CACHE_CONSUMPTION_DIAGNOSTICS],
)
def test_cache_consumption_stamps_the_evaluated_po2(
    entry: str,
    _name: str,
    diagnostic: dict[str, object],
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay and the write-through hit stamp the cached evaluation.

    The hour reset has cleared the stamp. Both returns adopt the
    restored diagnostic before the headspace bleed. An m=+0.25 carrier
    evaluated at 1e-2 bar and bled to 1e-3 bar keeps the 1e-2 ceiling.
    (1e-3 / 1e-2)^0.25 is about 0.562 of that bound. A diagnostic with
    no positive pO2 records absence, and the ceiling does not fall
    through to the bled reservoir. Skipping the stamp books the bled
    ceiling for every class.
    """

    expected_bar = _oracle_cached_evaluation_bar(diagnostic)
    host = _cache_stamp_host()
    _consume_cached_equilibrium(
        entry,
        host,
        _cache_equilibrium_payload(diagnostic),
        tmp_path / "cache-stamp.sqlite",
    )
    assert host._evaluated_transport_pO2_recorded is True
    assert host._evaluated_transport_pO2_bar == expected_bar
    flux = _m025_refused_flux()
    attach_evaluated_transport_pO2(flux, host)
    snapshot = _m025_refused_hour(flux, _CACHE_BLED_BAR)
    assert evaluate_module._snapshot_transport_pO2_bar(snapshot) == expected_bar
    if expected_bar is None:
        interval = _classify_wall_supply_interval(snapshot, "M025")
        assert interval.kind == "unavailable"
        assert interval.evidence == "missing_recorded_pO2"
        bounds = _optimizer_refused_flux_bounds(
            (snapshot,),
            ("M025",),
            campaigns_elapsed=1.0,
        )
        assert "M025" not in bounds
    else:
        correct = _m025_hk_at(expected_bar)
        wrong = _m025_hk_at(_CACHE_BLED_BAR)
        assert wrong / correct == pytest.approx(0.1 ** 0.25)
        bounds = _optimizer_refused_flux_bounds(
            (snapshot,),
            ("M025",),
            campaigns_elapsed=1.0,
        )
        assert bounds["M025"] == _approx_kg(correct)
        assert bounds["M025"] != _approx_kg(wrong)

    import simulator.evaporation as evaporation_module

    monkeypatch.setattr(
        evaporation_module,
        "remember_evaluated_transport_pO2",
        lambda host, pressure_bar: None,
    )
    bare = _cache_stamp_host()
    _consume_cached_equilibrium(
        entry,
        bare,
        _cache_equilibrium_payload(diagnostic),
        tmp_path / "cache-stamp-mutated.sqlite",
    )
    assert bare._evaluated_transport_pO2_recorded is False
    mutated_flux = _m025_refused_flux()
    attach_evaluated_transport_pO2(mutated_flux, bare)
    mutated_snapshot = _m025_refused_hour(mutated_flux, _CACHE_BLED_BAR)
    mutated = _optimizer_refused_flux_bounds(
        (mutated_snapshot,),
        ("M025",),
        campaigns_elapsed=1.0,
    )
    assert mutated["M025"] == _approx_kg(_m025_hk_at(_CACHE_BLED_BAR))
    if expected_bar is None:
        assert "M025" in mutated
    else:
        assert mutated["M025"] != _approx_kg(_m025_hk_at(expected_bar))


def test_snapshot_temperature_uses_the_celsius_offset_constant() -> None:
    """The kelvin conversion is the owned offset, not a second 273.15."""

    source = inspect.getsource(evaluate_module._snapshot_temperature_K)
    assert "CELSIUS_TO_KELVIN_OFFSET" in source
    assert "273.15" not in source


def test_ungrounded_resinter_threshold_keeps_physical_scores_on_the_pareto() -> None:
    """A null resinter threshold does not drop a feasible candidate.

    The furnace penalty is ungrounded. The physical scores stay, and
    the candidate can sit on the Pareto. A missing objective is still
    unscored. No campaign count is invented.
    """

    ungrounded = {
        "furnace_amortization_status": "resinter_threshold_ungrounded",
        "coating_status": "warning",
    }
    priced = SimpleNamespace(
        candidate_id="priced",
        cache_key="priced",
        objectives={"oxygen_kg": 10.0, "energy_kWh": 1.0},
        product_summary=ungrounded,
    )
    weaker = SimpleNamespace(
        candidate_id="weaker",
        cache_key="weaker",
        objectives={"oxygen_kg": 1.0, "energy_kWh": 2.0},
        product_summary=ungrounded,
    )
    scores = study._record_objective_scores(priced, DEFINITIONS)
    assert scores == objective_scores(priced.objectives, DEFINITIONS)
    assert all(score is not None for score in scores)
    front = pareto_front(
        (weaker, priced),
        DEFINITIONS,
        objective_getter=lambda item: item.objectives,
        score_getter=lambda item: study._record_objective_scores(item, DEFINITIONS),
    )
    assert [item.candidate_id for item in front] == ["priced"]
    missing = SimpleNamespace(
        candidate_id="missing",
        cache_key="missing",
        objectives={"oxygen_kg": 10.0},
        product_summary=ungrounded,
    )
    assert study._record_objective_scores(missing, DEFINITIONS) == (None, None)


_RANKER_NAMES = ("study", "bayesian", "staged")
_FURNACE_STATUSES = (
    FURNACE_AMORTIZATION_STATUS_UNGROUNDED,
    "available",
    "batch_cost_unavailable",
    "unavailable",
)
_SCORE_OXYGEN_KG = 10.0
_SCORE_ENERGY_KWH = 1.0
_SCORE_PENALTY = 1.0


def _ranker_scored(feasible: bool, summary: dict[str, object]) -> SimpleNamespace:
    values = {
        "oxygen_kg": _SCORE_OXYGEN_KG,
        "energy_kWh": _SCORE_ENERGY_KWH,
    }
    return SimpleNamespace(
        feasible=feasible,
        candidate_id="case",
        cache_key="case",
        objectives=SimpleNamespace(as_mapping=lambda: dict(values)),
        product_summary=summary,
        run_reference=SimpleNamespace(product_summary=summary),
    )


def _furnace_summary(status: str) -> dict[str, object]:
    summary: dict[str, object] = {"furnace_amortization_status": status}
    if status == "available":
        summary["furnace_amortization_batch_cost_equivalents"] = _SCORE_PENALTY
    return summary


def _call_ranker(
    ranker: str,
    scored: SimpleNamespace,
) -> tuple[float, ...] | tuple[None, ...] | None:
    if ranker == "study":
        return study._record_objective_scores(scored, DEFINITIONS)
    if ranker == "bayesian":
        return bayesian_strategy._objective_values_for_definitions(
            scored,
            DEFINITIONS,
        )
    if ranker == "staged":
        return staged_strategy._scored_objective_scores(scored, DEFINITIONS)
    raise AssertionError(ranker)


def _oracle_ranker_scores(
    ranker: str,
    status: str,
    feasible: bool,
) -> tuple[float, ...] | tuple[None, ...] | None:
    """Independent scores. Does not call the objective helpers.

    Physical maximize-native scores are oxygen 10 and energy -1.
    Available furnace evidence with penalty 1 divides a maximize score
    by 2 and multiplies a minimize score by 2. A null resinter
    threshold keeps the physical scores and invents no campaign count.
    Any other non-available status is unscoreable for a feasible
    result. Study has no feasible bit, so that status is unscoreable
    there either way. Staged and Bayesian keep the physical scores for
    an infeasible result. Bayesian then flips the minimize sign again,
    because the sampler minimises.
    """

    oxygen = _SCORE_OXYGEN_KG
    energy = _SCORE_ENERGY_KWH
    physical = (oxygen, -energy)
    if status == "available":
        factor = 1.0 + _SCORE_PENALTY
        native: tuple[float, ...] | None = (oxygen / factor, -energy * factor)
    elif status == FURNACE_AMORTIZATION_STATUS_UNGROUNDED:
        native = physical
    elif ranker == "study" or (ranker == "staged" and feasible) or (
        ranker == "bayesian" and feasible
    ):
        return (None, None) if ranker != "bayesian" else None
    else:
        native = physical
    assert native is not None
    if ranker == "bayesian":
        return (native[0], -native[1])
    return native


@pytest.mark.parametrize("ranker", _RANKER_NAMES)
@pytest.mark.parametrize("status", _FURNACE_STATUSES)
@pytest.mark.parametrize("feasible", (True, False))
def test_ungrounded_threshold_scores_are_the_same_for_every_ranker(
    ranker: str,
    status: str,
    feasible: bool,
) -> None:
    """The furnace-status policy lives in the scoring owner.

    Study, Bayesian, and staged all ask that owner. A feasible
    ungrounded result is physical scores on every ranker. Other
    non-available statuses stay unscoreable when the result is feasible.
    """

    scored = _ranker_scored(feasible, _furnace_summary(status))
    expected = _oracle_ranker_scores(ranker, status, feasible)
    assert _call_ranker(ranker, scored) == expected


@pytest.mark.parametrize("ranker", _RANKER_NAMES)
def test_ranker_that_skips_the_owner_drops_the_ungrounded_score(
    ranker: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patching the owner's binding must change the ungrounded result.

    A ranker that still special-cases the status and never calls
    ``cost_adjusted_objective_scores`` keeps the physical scores, and
    this assertion fails.
    """

    def refuse(*_args: object, **_kwargs: object) -> tuple[float, ...]:
        raise ObjectiveComputationError("mutated owner")

    if ranker == "study":
        monkeypatch.setattr(study, "cost_adjusted_objective_scores", refuse)
    elif ranker == "bayesian":
        monkeypatch.setattr(
            bayesian_strategy,
            "cost_adjusted_objective_scores",
            refuse,
        )
    else:
        monkeypatch.setattr(
            staged_strategy,
            "cost_adjusted_objective_scores",
            refuse,
        )
    scored = _ranker_scored(
        True,
        _furnace_summary(FURNACE_AMORTIZATION_STATUS_UNGROUNDED),
    )
    expected = _oracle_ranker_scores(
        ranker,
        FURNACE_AMORTIZATION_STATUS_UNGROUNDED,
        True,
    )
    mutated = _call_ranker(ranker, scored)
    assert mutated != expected
    if ranker == "bayesian":
        assert mutated is None
    else:
        assert mutated == (None, None)


def test_bayesian_available_malformed_penalty_still_propagates() -> None:
    """Once status is available, a missing penalty is not swallowed."""

    summary = {"furnace_amortization_status": "available"}
    scored = _ranker_scored(True, summary)
    with pytest.raises(ObjectiveComputationError, match="penalty is missing"):
        bayesian_strategy._objective_values_for_definitions(scored, DEFINITIONS)
    assert study._record_objective_scores(scored, DEFINITIONS) == (None, None)
    assert staged_strategy._scored_objective_scores(scored, DEFINITIONS) == (
        None,
        None,
    )


@pytest.mark.parametrize("kind", _WALL_SUPPLY_INTERVAL_KINDS)
def test_each_interval_kind_mutation_fails_the_unmutated_oracle(
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A classifier that mis-classes this kind is rejected by the oracle.

    The snapshot stays the true kind. The mutation is inside the
    classifier. The oracle is not patched, so it still scores the
    fixture from the interval rule.
    """

    species = "SiO"
    sibling = "Mg"
    snapshots = _supply_window_snapshots(species, sibling, (kind,))
    species_kg = _window_species_supply_kg((kind,))
    sibling_kg = _window_sibling_supply_kg((kind,))
    live = _optimizer_refused_flux_bounds(
        snapshots,
        (species, sibling),
        campaigns_elapsed=1.0,
    )
    oracle = _oracle_complete_flux_supply_kg(snapshots, (species, sibling))
    _assert_supply_map(live, species, sibling, species_kg, sibling_kg)
    _assert_supply_map(oracle, species, sibling, species_kg, sibling_kg)
    monkeypatch.setattr(
        evaluate_module,
        "_classify_wall_supply_interval",
        _mutate_classified_interval(kind, species),
    )
    mutated = _optimizer_refused_flux_bounds(
        snapshots,
        (species, sibling),
        campaigns_elapsed=1.0,
    )
    assert mutated != oracle
    with pytest.raises(AssertionError):
        _assert_supply_map(
            mutated,
            species,
            sibling,
            species_kg,
            sibling_kg,
        )


def test_subthreshold_floor_mutation_fails_the_supply_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing a 5e-13 kg/h computed rate with 0 fails the oracle.

    The mutation is inside the classifier, not the fixture. Default
    pytest absolute tolerance 1e-12 would treat 5e-13 as equal to 0.
    """

    species = "SiO"
    sibling = "Mg"
    window = ("computed_below_threshold",)
    snapshots = _supply_window_snapshots(species, sibling, window)
    live = _optimizer_refused_flux_bounds(
        snapshots,
        (species, sibling),
        campaigns_elapsed=1.0,
    )
    oracle = _oracle_complete_flux_supply_kg(snapshots, (species, sibling))
    _assert_supply_map(live, species, sibling, oracle[species], oracle[sibling])
    assert oracle[species] == _approx_kg(_WALL_SUPPLY_BELOW_KG_HR)
    assert live[species] != _approx_kg(0.0)

    real = _classify_wall_supply_interval

    def floor_subthreshold(
        snapshot: object,
        name: str,
        catalog: object = None,
        recorded_pO2_bar: tuple[float, ...] = (),
    ) -> _WallSupplyInterval:
        interval = real(snapshot, name, catalog, recorded_pO2_bar)
        if (
            interval.kind == "computed"
            and interval.rate_kg_hr is not None
            and interval.rate_kg_hr <= _DEBIT_FLOOR_KG_HR
        ):
            return replace(interval, rate_kg_hr=0.0)
        return interval

    monkeypatch.setattr(
        evaluate_module,
        "_classify_wall_supply_interval",
        floor_subthreshold,
    )
    mutated = _optimizer_refused_flux_bounds(
        snapshots,
        (species, sibling),
        campaigns_elapsed=1.0,
    )
    assert mutated[species] == 0.0
    with pytest.raises(AssertionError):
        _assert_supply_map(
            mutated,
            species,
            sibling,
            oracle[species],
            oracle[sibling],
        )


def test_dropping_interval_physical_zero_fails_the_supply_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A classifier that ignores a known-zero hour withholds the supply."""

    species = "SiO"
    sibling = "Mg"
    window = ("computed_above_threshold", "interval_physical_zero")
    snapshots = _supply_window_snapshots(species, sibling, window)
    live = _optimizer_refused_flux_bounds(
        snapshots,
        (species, sibling),
        campaigns_elapsed=1.0,
    )
    oracle = _oracle_complete_flux_supply_kg(snapshots, (species, sibling))
    _assert_supply_map(live, species, sibling, oracle[species], oracle[sibling])
    assert oracle[species] == _approx_kg(_WALL_SUPPLY_ABOVE_KG_HR)

    real = _classify_wall_supply_interval

    def drop_interval_zero(
        snapshot: object,
        name: str,
        catalog: object = None,
        recorded_pO2_bar: tuple[float, ...] = (),
    ) -> _WallSupplyInterval:
        interval = real(snapshot, name, catalog, recorded_pO2_bar)
        if str(interval.evidence).startswith("interval_physical_zero"):
            return _WallSupplyInterval("unavailable", evidence="absent_no_record")
        return interval

    monkeypatch.setattr(
        evaluate_module,
        "_classify_wall_supply_interval",
        drop_interval_zero,
    )
    mutated = _optimizer_refused_flux_bounds(
        snapshots,
        (species,),
        campaigns_elapsed=1.0,
    )
    assert species not in mutated
    with pytest.raises(AssertionError):
        _assert_supply_map(
            _optimizer_refused_flux_bounds(
                snapshots,
                (species, sibling),
                campaigns_elapsed=1.0,
            ),
            species,
            sibling,
            oracle[species],
            oracle[sibling],
        )


def test_non_evaporation_campaign_keeps_an_earlier_supply() -> None:
    """Positive SiO, then a C5 hour. The empty map is a typed zero."""

    host = SimpleNamespace()
    campaign_flux = evaporation_flux_for_campaign_hour(
        evaporation_campaign=False,
        host=host,
    )
    replaced = SimpleNamespace(species_kg_hr={})
    attach_wall_supply_interval_physical_zero(replaced, host)
    publish_wall_supply_on_flux(replaced, {})
    assert replaced.wall_supply_interval_physical_zero["reason"] == (
        "non_evaporation_campaign"
    )
    assert campaign_flux.species_kg_hr == {}
    evaporating = evaporation_flux_for_campaign_hour(
        evaporation_campaign=True,
        host=SimpleNamespace(),
    )
    assert not hasattr(evaporating, "wall_supply_interval_physical_zero")
    bounds = _optimizer_refused_flux_bounds(
        (
            _interval_flux_snapshot(1, 1.0, {"SiO": 1.0}),
            SimpleNamespace(hour=2, duration_h=1.0, evap_flux=replaced),
        ),
        ("SiO",),
        campaigns_elapsed=1.0,
    )
    assert bounds["SiO"] == _approx_kg(1.0)
    bare = SimpleNamespace(
        hour=2,
        duration_h=1.0,
        evap_flux=SimpleNamespace(
            species_kg_hr={},
            published_species_kg_hr={},
        ),
    )
    withheld = _optimizer_refused_flux_bounds(
        (
            _interval_flux_snapshot(1, 1.0, {"SiO": 1.0}),
            bare,
        ),
        ("SiO",),
        campaigns_elapsed=1.0,
    )
    assert "SiO" not in withheld


def test_zero_liquid_hour_stamps_interval_physical_zero() -> None:
    sim = SimpleNamespace(
        melt=SimpleNamespace(temperature_C=1500.0),
        setpoints={},
    )
    flux = EvaporationMixin._calculate_evaporation(
        sim,
        SimpleNamespace(liquid_fraction=0.0),
    )
    assert flux.species_kg_hr == {}
    assert flux.wall_supply_interval_physical_zero == {
        "reason": "zero_liquid",
        "evidence": "authorized_zero",
    }
    assert sim._wall_supply_interval_physical_zero["reason"] == "zero_liquid"
    publish_wall_supply_on_flux(flux, {})
    bounds = _optimizer_refused_flux_bounds(
        (
            _interval_flux_snapshot(1, 1.0, {"SiO": 1.0}),
            SimpleNamespace(hour=2, duration_h=1.0, evap_flux=flux),
        ),
        ("SiO",),
        campaigns_elapsed=1.0,
    )
    assert bounds["SiO"] == _approx_kg(1.0)


def test_zero_net_bucket_is_supply_zero_and_not_a_debit() -> None:
    projected = project_publisher_vapour_rates(
        {
            "evaporation_flux_kg_hr": {"Mg": 0.4},
            "zero_net_evaporation_flux": {"SiO": 0.0, "Na": 3.0},
        },
        liquid_fraction_factor=1.0,
    )
    assert projected.debit_kg_hr == {"Mg": pytest.approx(0.4)}
    assert projected.supply_kg_hr["Mg"] == _approx_kg(0.4)
    assert projected.supply_kg_hr["SiO"] == _approx_kg(0.0)
    assert "Na" not in projected.supply_kg_hr
    flux = SimpleNamespace(species_kg_hr=dict(projected.debit_kg_hr))
    publish_wall_supply_on_flux(flux, projected.supply_kg_hr)
    # Hour 1 publishes SiO only. Mg is absent and has no physical-zero
    # record, so the classifier withholds Mg (absent_no_record). The
    # 0.4 kg/h hour is not a partial supply (d-367).
    withheld = _optimizer_refused_flux_bounds(
        (
            _interval_flux_snapshot(1, 1.0, {"SiO": 1.0}),
            SimpleNamespace(hour=2, duration_h=1.0, evap_flux=flux),
        ),
        ("SiO", "Mg"),
        campaigns_elapsed=1.0,
    )
    assert withheld["SiO"] == _approx_kg(1.0)
    assert "Mg" not in withheld
    # The same published hour, with hour 1 also naming Mg at a computed
    # zero. SiO's zero-net rate stays a supply of 0, not a debit, and
    # Mg's complete integral is the debit rate.
    covered = _optimizer_refused_flux_bounds(
        (
            _interval_flux_snapshot(1, 1.0, {"SiO": 1.0, "Mg": 0.0}),
            SimpleNamespace(hour=2, duration_h=1.0, evap_flux=flux),
        ),
        ("SiO", "Mg"),
        campaigns_elapsed=1.0,
    )
    assert covered["SiO"] == _approx_kg(1.0)
    assert covered["Mg"] == _approx_kg(0.4)


def _pre_rg_channel_answer(species: str, kind: str) -> VapourAnswer:
    """One catalog channel that fills batch_pa and debits nothing."""

    pressure = PressureValue(pa=1.0)
    if kind == "refusal":
        flux = FluxRefusal(code="flux_refused", detail="refused")
    elif kind == "upper_bound":
        flux = FluxDiagnosticUpperBound(alpha_ref=f"alpha:{species}")
    elif kind == "dormant":
        flux = FluxEligible(alpha_ref=f"alpha:{species}")
    else:
        raise AssertionError(kind)
    return VapourAnswer(
        species_id=species,
        pressure=pressure,
        selected_runtime_pressure=pressure,
        flux=flux,
        source_label="test",
        formula_id=species,
        source_account="process.cleaned_melt",
        solve_group_id=f"test:{species}",
        state_fingerprint="test",
        validation_status="validated",
    )


@pytest.mark.parametrize("kind", ("refusal", "upper_bound", "dormant"))
def test_pre_rg_empty_map_below_1050_does_not_price_a_partial_supply(
    kind: str,
) -> None:
    """Below 1050 C an empty effective-pressure map is not a wall-supply zero.

    ``batch_pa_by_species`` holds any finite PressureValue, including one
    paired with FluxRefusal or a diagnostic upper bound. Stamping that
    hour as a physical zero would book Na2 from the other hour. Na2 is
    absent from this batch. The answered channel stays unavailable too.
    """

    answered = "SiO"
    batch = VapourBatch(
        requested_species_ids=frozenset({answered}),
        channels_by_species={
            answered: _pre_rg_channel_answer(answered, kind),
        },
        flux_active_species_ids=frozenset(),
    )
    model = SimpleNamespace(
        melt=SimpleNamespace(temperature_C=900.0),
        setpoints={},
        vapor_pressures={},
        _last_vapour_batch_resolve_error={},
        _resolve_evaporation_vapour_batch=(
            lambda equilibrium, temperature_K, effective_pressure_source: batch
        ),
    )
    model._resolve_evaporation_batch_flux_state = MethodType(
        EvaporationMixin._resolve_evaporation_batch_flux_state,
        model,
    )
    equilibrium = EquilibriumResult(
        temperature_C=900.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={},
        status="ok",
    )
    flux = EvaporationMixin._calculate_evaporation(model, equilibrium)
    assert flux.species_kg_hr == {}
    assert not hasattr(flux, "wall_supply_interval_physical_zero")
    assert model._last_vapour_batch_flux_overlay["batch_pa_by_species"][answered] == (
        1.0
    )
    assert model._last_vapour_batch_flux_overlay[
        "effective_pressure_zero_reason"
    ] == "pre_rg_backend_below_effective_pressure_threshold"
    later = _interval_flux_snapshot(2, 1.0, {answered: 1.0, "Na2": 1.0})
    bounds = _optimizer_refused_flux_bounds(
        (
            SimpleNamespace(hour=1, duration_h=1.0, evap_flux=flux),
            later,
        ),
        (answered, "Na2"),
        campaigns_elapsed=1.0,
    )
    assert answered not in bounds
    assert "Na2" not in bounds


def test_unknown_interval_physical_zero_reason_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown wall-supply"):
        note_wall_supply_interval_physical_zero(
            SimpleNamespace(),
            reason="not_a_producer",
        )


def test_known_zero_interval_still_prices_the_refused_wall() -> None:
    """C5 after SiO, and a frozen hour before SiO, stay on the supply."""

    def _case(snapshots: tuple[object, ...]) -> Mapping[str, object]:
        return {
            "deposit": (),
            "flux_snapshots": snapshots,
            "refused": (("SiO", "hot_wall"),),
            "charge": 1000.0,
            "entries": ((("hot_wall", "SiO"), 1.0),),
            "species": (("SiO", _WALL_BOUND_UPPER, 1.0, 1.0),),
        }

    after_c5 = _case((
        {"hour": 1, "duration_h": 1.0, "flux": (("SiO", 1.0),)},
        {
            "hour": 2,
            "duration_h": 1.0,
            "flux": (),
            "interval_physical_zero": "non_evaporation_campaign",
        },
    ))
    before_evaporation = _case((
        {
            "hour": 1,
            "duration_h": 1.0,
            "flux": (),
            "interval_physical_zero": "frozen_before_evaporation",
            "eligible_channels": ("SiO",),
        },
        {"hour": 2, "duration_h": 1.0, "flux": (("SiO", 1.0),)},
    ))
    for case in (after_c5, before_evaporation):
        view = _wall_bound_view("book", case)
        assert view["scored"] is True
        assert view["upper_flag"] is True
        assert view["species_kg"]["SiO"] == _approx_kg(1.0)


def test_fe_residual_cap_applies_to_supply_without_changing_the_record() -> None:
    projected = project_publisher_vapour_rates(
        {"evaporation_flux_kg_hr": {"Fe": 1.0}},
        liquid_fraction_factor=1.0,
        fe_residual_kg_hr=0.25,
    )
    assert projected.debit_kg_hr["Fe"] == pytest.approx(0.25)
    assert projected.supply_kg_hr["Fe"] == pytest.approx(0.25)
    assert projected.fe_allocation["melt_fe_raw_kg_hr"] == pytest.approx(1.0)
    assert projected.fe_allocation["melt_fe_applied_kg_hr"] == pytest.approx(0.25)
    below_only = project_publisher_vapour_rates(
        {
            "evaporation_flux_kg_hr": {},
            "below_threshold_evaporation_flux": {
                "fluxes_kg_hr": {"Fe": 5.0e-13},
            },
        },
        liquid_fraction_factor=1.0,
        fe_residual_kg_hr=1.0e-16,
    )
    assert below_only.fe_allocation is None
    assert "Fe" not in below_only.debit_kg_hr
    assert below_only.supply_kg_hr["Fe"] == pytest.approx(1.0e-16)


class _DepletionParent:
    """Stoich stand-in. The depletion method only reads these two hooks."""

    def __init__(self, *, o2_per_product_kg: float = 0.0) -> None:
        self.vapor_pressures = {
            "metals": {"SiO": {"present": True}},
            "oxide_vapors": {},
        }
        self._o2_per_product_kg = o2_per_product_kg

    def _evaporation_stoich(self, species: str, sp_data: Mapping[str, object]) -> dict:
        return {
            "parent_oxide": "SiO2",
            "oxide_per_product_kg": 1.0,
            "O2_per_product_kg": self._o2_per_product_kg,
        }


def test_depletion_keeps_a_rate_the_debit_floor_never_enters() -> None:
    supply_out: dict[str, float] = {}
    debit = EvaporationMixin._analytic_evaporation_depletion_rates(
        _DepletionParent(),
        {"SiO": 5.0e-13},
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg={"SiO2": 1.0},
        available_o2_kg=1.0,
        supply_out=supply_out,
    )
    assert debit == {}
    assert supply_out["SiO"] == _approx_kg(5.0e-13)


def test_depletion_keeps_a_positive_product_below_the_debit_floor() -> None:
    """Parent inventory 5e-13 kg, raw demand 2e-12 kg/h.

    k = 2e-12 / 5e-13 = 4 / h. Over one hour the drawn parent mass is
    5e-13 * (1 - exp(-4)) kg, which is below the debit floor and is
    still the computed vapour.
    """

    available_kg = 5.0e-13
    raw_kg_hr = 2.0e-12
    drawn_kg = available_kg * -math.expm1(-raw_kg_hr / available_kg)
    supply_out: dict[str, float] = {}
    debit = EvaporationMixin._analytic_evaporation_depletion_rates(
        _DepletionParent(),
        {"SiO": raw_kg_hr},
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg={"SiO2": available_kg},
        available_o2_kg=1.0,
        supply_out=supply_out,
    )
    assert "SiO" not in debit
    assert supply_out["SiO"] == _approx_kg(drawn_kg)
    assert drawn_kg <= _DEBIT_FLOOR_KG_HR
    assert drawn_kg > 0.0


def test_depletion_inventory_exhaustion_is_an_explicit_supply_zero() -> None:
    supply_out: dict[str, float] = {}
    debit = EvaporationMixin._analytic_evaporation_depletion_rates(
        _DepletionParent(),
        {"SiO": 1.0},
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg={"SiO2": 0.0},
        available_o2_kg=1.0,
        supply_out=supply_out,
    )
    assert debit == {}
    assert supply_out["SiO"] == 0.0


def _independent_depletion(
    raw_kg_hr: float,
    available_parent_kg: float,
    *,
    phase: float,
    available_o2_kg: float,
    o2_per_product_kg: float,
    dt_hr: float = 1.0,
) -> tuple[float | None, float]:
    """Debit rate, or None, and the supply rate.

    The first-order draw is ``available * (1 - exp(-k dt))``, capped
    just under the inventory. A rate at or below 1e-12 kg/h does not
    enter that draw, so its supply is the computed rate. Empty parent
    inventory is an explicit zero. The debit keeps a product only when
    the parent mass, the draw, and the product each exceed 1e-12 kg.
    Oxygen limiting applies only to that debit.
    """

    rate = float(raw_kg_hr) * float(phase)
    if rate <= _DEBIT_FLOOR_KG_HR:
        return None, (rate if rate > 0.0 else 0.0)
    parent_draw = rate
    if parent_draw <= _DEBIT_FLOOR_KG_HR:
        return None, rate
    if available_parent_kg <= 0.0 or parent_draw <= 0.0:
        return None, 0.0
    cap = math.nextafter(1.0, 0.0)
    fraction = -math.expm1(-parent_draw / available_parent_kg * dt_hr)
    fraction = max(0.0, min(cap, fraction))
    product_kg = available_parent_kg * fraction
    supply = product_kg / dt_hr if product_kg > 0.0 else 0.0
    if not (
        available_parent_kg > _DEBIT_FLOOR_KG_HR
        and parent_draw > _DEBIT_FLOOR_KG_HR
        and product_kg > _DEBIT_FLOOR_KG_HR
    ):
        return None, supply
    if o2_per_product_kg >= -1.0e-12:
        return supply, supply
    required = supply * abs(o2_per_product_kg)
    if required <= _DEBIT_FLOOR_KG_HR:
        return supply, supply
    if available_o2_kg <= _DEBIT_FLOOR_KG_HR:
        return None, 0.0
    o2_fraction = -math.expm1(-required / available_o2_kg * dt_hr)
    o2_fraction = max(0.0, min(cap, o2_fraction))
    allowed_rate = supply * ((available_o2_kg * o2_fraction) / required)
    if allowed_rate <= _DEBIT_FLOOR_KG_HR:
        kept = allowed_rate if allowed_rate > 0.0 else 0.0
        return None, kept
    applied = min(supply, allowed_rate)
    return applied, applied


@pytest.mark.parametrize("phase", (1.0, 0.5))
@pytest.mark.parametrize(
    ("o2_per_product_kg", "available_o2_kg"),
    ((0.0, 0.0), (-1.0, 0.0), (-1.0, 100.0)),
)
@pytest.mark.parametrize("available_parent_kg", (0.0, 5.0e-13, 100.0))
@pytest.mark.parametrize("raw_kg_hr", _COMPUTED_RATE_GRID)
def test_depletion_supply_keeps_every_computed_draw(
    raw_kg_hr: float,
    available_parent_kg: float,
    o2_per_product_kg: float,
    available_o2_kg: float,
    phase: float,
) -> None:
    """Supply follows the draw. The shadow debit does not see supply_out."""

    expected_debit, expected_supply = _independent_depletion(
        raw_kg_hr,
        available_parent_kg,
        phase=phase,
        available_o2_kg=available_o2_kg,
        o2_per_product_kg=o2_per_product_kg,
    )
    parent = _DepletionParent(o2_per_product_kg=o2_per_product_kg)
    kwargs = {
        "dt_hr": 1.0,
        "phase_scalar": phase,
        "cleaned_melt_kg": {"SiO2": available_parent_kg},
        "available_o2_kg": available_o2_kg,
    }
    shadow = EvaporationMixin._analytic_evaporation_depletion_rates(
        parent,
        {"SiO": raw_kg_hr},
        **kwargs,
    )
    supply_out: dict[str, float] = {}
    live = EvaporationMixin._analytic_evaporation_depletion_rates(
        parent,
        {"SiO": raw_kg_hr},
        supply_out=supply_out,
        **kwargs,
    )
    assert live == shadow
    if expected_debit is None:
        assert "SiO" not in live
    else:
        assert live["SiO"] == _approx_kg(expected_debit)
    assert supply_out["SiO"] == _approx_kg(expected_supply)


def test_subthreshold_sibling_does_not_change_the_parent_debit() -> None:
    parent = _DepletionParent()
    raw = {"Mg": 5.0e-13, "SiO": 1.0}
    kwargs = {
        "dt_hr": 1.0,
        "phase_scalar": 1.0,
        "cleaned_melt_kg": {"SiO2": 100.0},
        "available_o2_kg": 1.0,
    }
    shadow = EvaporationMixin._analytic_evaporation_depletion_rates(
        parent,
        raw,
        **kwargs,
    )
    supply_out: dict[str, float] = {}
    live = EvaporationMixin._analytic_evaporation_depletion_rates(
        parent,
        {"SiO": 1.0, "Mg": 5.0e-13},
        supply_out=supply_out,
        **kwargs,
    )
    assert live == shadow
    assert "Mg" not in live
    assert supply_out["Mg"] == _approx_kg(5.0e-13)
    assert supply_out["SiO"] == _approx_kg(live["SiO"])


def test_physical_scale_keeps_a_rate_below_the_debit_reporting_floor() -> None:
    host = SimpleNamespace(
        _wall_supply_computed_kg_hr={"SiO": 1.0e-15, "Mg": 0.0},
    )
    EvaporationMixin._scale_wall_supply_computed(host, 0.5)
    assert host._wall_supply_computed_kg_hr["SiO"] == pytest.approx(5.0e-16)
    assert host._wall_supply_computed_kg_hr["Mg"] == 0.0
    assert not (1.0e-15 * 0.5 > 1.0e-15)


def test_republish_does_not_scale_the_supply_map_again() -> None:
    flux = SimpleNamespace(species_kg_hr={"SiO": 2.0e-12})
    publish_wall_supply_on_flux(
        flux,
        {"SiO": 2.0e-12, "Mg": 5.0e-13},
        debit_kg_hr={"SiO": 5.0e-13},
        phase_scale=1.0,
    )
    # Post-depletion debit wins over the pre-depletion computed rate.
    assert flux.published_species_kg_hr["SiO"] == pytest.approx(5.0e-13)
    assert flux.published_species_kg_hr["Mg"] == pytest.approx(5.0e-13)
    first = dict(flux.published_species_kg_hr)
    publish_wall_supply_on_flux(
        flux,
        {"SiO": 2.0e-12, "Mg": 5.0e-13},
        debit_kg_hr={"SiO": 99.0},
        phase_scale=0.5,
    )
    assert flux.published_species_kg_hr == first


def test_depletion_without_oxygen_zeros_the_supply_and_the_debit() -> None:
    supply_out: dict[str, float] = {}
    debit = EvaporationMixin._analytic_evaporation_depletion_rates(
        _DepletionParent(o2_per_product_kg=-1.0),
        {"SiO": 1.0},
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg={"SiO2": 100.0},
        available_o2_kg=0.0,
        supply_out=supply_out,
    )
    assert debit == {}
    assert supply_out["SiO"] == 0.0


def test_wall_supply_map_has_one_writer() -> None:
    writer = inspect.getsource(publish_wall_supply_on_flux)
    assert "published_species_kg_hr =" in writer
    route = inspect.getsource(EvaporationMixin._route_to_condensation)
    calculate = inspect.getsource(EvaporationMixin._calculate_evaporation)
    # step() is the poison/rollback wrapper (since 6d727259c). The
    # snapshot publish is in the hour body it calls.
    step = inspect.getsource(PyrolysisSimulator.step)
    hour = inspect.getsource(PyrolysisSimulator._step_one_hour)
    assert "publish_wall_supply_on_flux(" in route
    assert "published_species_kg_hr =" not in route
    assert "project_publisher_vapour_rates(" in calculate
    assert "_step_one_hour(" in step
    assert "publish_wall_supply_on_flux(" not in step
    assert "publish_wall_supply_on_flux(" in hour
    evaporation_text = inspect.getsource(inspect.getmodule(publish_wall_supply_on_flux))
    assert evaporation_text.count("published_species_kg_hr =") == 1


def test_pre_evaporation_oxide_is_noted_before_the_snapshot_remainder() -> None:
    """The snapshot inventory is the post-debit remainder.

    The note runs before the evaporation projection. The attach runs
    after condensation may have replaced the flux, and before the
    snapshot stores that flux. The hour-start clear is not the note.
    """

    hour = inspect.getsource(PyrolysisSimulator._step_one_hour)
    pause = hour.index("if self.paused_for_decision:")
    clear = hour.index("self._pre_evaporation_melt_oxide_kg = None")
    note = hour.index("note_pre_evaporation_melt_oxide_kg(")
    update = hour.index("_update_melt_composition(")
    attach = hour.index("attach_pre_evaporation_melt_oxide_kg(")
    stored = hour.index("snapshot.evap_flux = evap_flux")
    assert pause < clear < note < update < attach < stored


def test_refused_supply_is_booked_on_the_segment_that_maximises_binding_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SiO refused in A and B, supply 1 kg, B already holding 3 kg.

    Alphabetical placement books A and leaves the maximum at 3 kg, so a
    4.5 kg threshold reports 1.5 campaigns. The conservative booking puts
    the supply in B. The maximum is 4 kg and the lifetime is 1.125
    campaigns, which is the amortization that ranks the candidate.
    """

    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: 4.5)
    execution, _snapshots, _deposit = _wall_bound_execution({
        "deposit": ((("B", "Na"), 3.0),),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "A"), ("SiO", "B")),
        "charge": 1000.0,
    })
    reference = _run_reference(
        execution,
        {},
        cost_parameters=_furnace_test_cost_parameters(),
    )
    summary = reference.product_summary
    segments = summary["wall_deposit_kg_by_segment_species"]

    assert segments["B"]["SiO"] == pytest.approx(1.0)
    assert segments["B"]["Na"] == pytest.approx(3.0)
    assert "SiO" not in segments.get("A", {})
    assert summary["wall_deposit_kg_by_species"]["SiO"] == pytest.approx(1.0)
    assert summary["campaigns_to_resinter"] == pytest.approx(4.5 / 4.0)
    assert summary["campaigns_to_resinter"] != pytest.approx(4.5 / 3.0)
    conservative = furnace_amortization_cost_per_run(
        20.0,
        4.5 / 4.0,
        500.0,
        1.0,
        has_positive_fouling=True,
    )
    alphabetical = furnace_amortization_cost_per_run(
        20.0,
        4.5 / 3.0,
        500.0,
        1.0,
        has_positive_fouling=True,
    )
    assert summary["furnace_amortization_cost_per_run_usd"] == pytest.approx(
        conservative
    )
    assert conservative > alphabetical
    assert summary["furnace_amortization_status"] == "available"
    assert summary["wall_deposit_upper_bound_used"] is True


def _closed_form_segment_loads(
    existing: Mapping[str, float],
    supplies: Mapping[str, float],
    eligible: Mapping[str, tuple[str, ...]],
) -> dict[str, float]:
    """Independent closed form, not a call into the booking helper.

    worst_max_load = max over s of (
        existing_s + sum of supplies eligible at s
    )
    """

    segments = set(existing)
    for candidates in eligible.values():
        segments.update(candidates)
    return {
        segment: float(existing.get(segment, 0.0)) + sum(
            float(supplies[species])
            for species, candidates in eligible.items()
            if segment in candidates
        )
        for segment in segments
    }


def _oracle_binding_assignment(
    existing: Mapping[str, float],
    supplies: Mapping[str, float],
    eligible: Mapping[str, tuple[str, ...]],
) -> dict[str, object]:
    """Species placement the closed form requires.

    chosen(i) is the argmax of the closed form over segments where i is
    eligible. The smaller segment id wins a tie. That is s* whenever i
    is eligible at s*, and it is i's own best segment otherwise. The
    smallest eligible id is not a placement rule.
    """

    closed = _closed_form_segment_loads(existing, supplies, eligible)
    binding = min(closed, key=lambda segment: (-closed[segment], segment))
    assignment = {
        species: min(
            candidates,
            key=lambda segment: (-closed[segment], segment),
        )
        for species, candidates in eligible.items()
    }
    loads = {
        segment: float(kg) for segment, kg in existing.items() if float(kg) > 0.0
    }
    for species, segment in assignment.items():
        loads[segment] = loads.get(segment, 0.0) + float(supplies[species])
    return {
        "binding_segment": binding,
        "closed_form": closed,
        "assignment": assignment,
        "loads": loads,
        "worst_max_load": closed[binding],
    }


def _existing_loads_excluding_supplies(
    existing_rows: tuple[tuple[str, str, float], ...],
    supplies: Mapping[str, float],
) -> dict[str, float]:
    loads: dict[str, float] = {}
    for segment, species, kg in existing_rows:
        if species in supplies:
            continue
        loads[segment] = loads.get(segment, 0.0) + float(kg)
    return loads


def _binding_execution_case(case: Mapping[str, object]) -> dict[str, object]:
    order = tuple(case["order"])
    supplies = case["supplies"]
    eligible = case["eligible"]
    return {
        "deposit": tuple(
            ((segment, species), float(kg))
            for segment, species, kg in case["existing"]
        ),
        "flux": tuple((species, float(supplies[species])) for species in order),
        "refused": tuple(
            (species, segment)
            for species in order
            for segment in eligible[species]
        ),
        "charge": 1000.0,
    }


def _binding_cases() -> tuple[dict[str, object], ...]:
    eligibility = (("A",), ("B",), ("A", "B"))
    supply_pairs = (
        (("Mg", 1.0), ("SiO", 3.0)),
        (("Mg", 3.0), ("SiO", 1.0)),
    )
    existing_rows = (
        ("empty", ()),
        ("b_na", (("B", "Na", 3.0),)),
        ("tie", (("A", "Na", 3.0), ("B", "K", 3.0))),
        ("outsider", (("C", "Na", 10.0), ("B", "K", 3.0))),
        ("measured", (("A", "Mg", 0.4), ("B", "SiO", 0.25))),
        ("measured_neighbour", (("A", "Mg", 0.4), ("B", "Na", 3.0))),
    )
    cases: list[dict[str, object]] = []
    for pair in supply_pairs:
        supplies = dict(pair)
        for existing_id, existing in existing_rows:
            for mg_eligible in eligibility:
                for sio_eligible in eligibility:
                    pin = (
                        supplies["Mg"] == 1.0
                        and supplies["SiO"] == 3.0
                        and mg_eligible == ("A", "B")
                        and sio_eligible == ("B",)
                    )
                    for order in (("Mg", "SiO"), ("SiO", "Mg")):
                        cases.append({
                            "supplies": dict(supplies),
                            "eligible": {"Mg": mg_eligible, "SiO": sio_eligible},
                            "existing": existing,
                            "existing_id": existing_id,
                            "order": order,
                            "channel_pin": pin,
                        })
    # A third species eligible only where the binding segment is not.
    # Fe on A, Mg on A and B, SiO only on B. Sequential booking leaves
    # the maximum at 3 kg; the closed form is 4 kg at B.
    fe_supplies = {"Fe": 2.0, "Mg": 1.0, "SiO": 3.0}
    fe_eligible = {"Fe": ("A",), "Mg": ("A", "B"), "SiO": ("B",)}
    for existing_id, existing in existing_rows:
        for order in (("Fe", "Mg", "SiO"), ("SiO", "Mg", "Fe")):
            cases.append({
                "supplies": dict(fe_supplies),
                "eligible": dict(fe_eligible),
                "existing": existing,
                "existing_id": existing_id,
                "order": order,
                "channel_pin": False,
            })
    cases.extend(_off_star_cases())
    return tuple(cases)


def _off_star_cases() -> tuple[dict[str, object], ...]:
    """Off-s* eligibility, preload, companion subset, and species order.

    Fe's 100 kg sits on one segment, so that segment is s*. Mg is eligible
    only on the other two segments and therefore never at s*. Zn's
    eligibility is every non-empty subset of A, B, and C, plus the cases
    with no Zn. A preload of 5 kg on the later Mg segment makes Mg's best
    closed-form segment differ from its smallest id. Equal 5 kg preloads
    make those closed forms tie, so the smaller id wins. The canonical
    unequal case is pinned on every scoring channel.
    """

    segments = ("A", "B", "C")
    companion_sets = tuple(
        tuple(
            segment
            for index, segment in enumerate(segments)
            if mask & (1 << index)
        )
        for mask in range(1, 1 << len(segments))
    )
    cases: list[dict[str, object]] = []
    for anchor in segments:
        off_segments = tuple(segment for segment in segments if segment != anchor)
        later = off_segments[-1]
        earlier = off_segments[0]
        preloads = (
            ("off_empty", ()),
            (
                "off_heavy_later",
                ((later, "Na", 5.0),),
            ),
            (
                "off_tied",
                ((earlier, "Na", 5.0), (later, "K", 5.0)),
            ),
        )
        rosters = (
            (("Fe", "Mg"), {"Fe": 100.0, "Mg": 1.0}, None),
            (
                ("Fe", "Mg", "Zn"),
                {"Fe": 100.0, "Mg": 1.0, "Zn": 4.0},
                companion_sets,
            ),
        )
        for roster, supplies, companions in rosters:
            companion_options: tuple[tuple[str, ...] | None, ...] = (
                (None,) if companions is None else companions
            )
            for companion in companion_options:
                eligible: dict[str, tuple[str, ...]] = {
                    "Fe": (anchor,),
                    "Mg": off_segments,
                }
                if companion is not None:
                    eligible["Zn"] = companion
                for existing_id, existing in preloads:
                    orders = (roster, tuple(reversed(roster)))
                    for order in orders:
                        canonical = (
                            anchor == "C"
                            and existing_id == "off_heavy_later"
                            and companion is None
                        )
                        cases.append({
                            "supplies": dict(supplies),
                            "eligible": dict(eligible),
                            "existing": existing,
                            "existing_id": f"{existing_id}-{anchor}",
                            "order": order,
                            "channel_pin": canonical,
                        })
    return tuple(cases)


def _binding_case_id(case: Mapping[str, object]) -> str:
    supplies = case["supplies"]
    eligible = case["eligible"]
    bits = [
        f"{name}{supplies[name]:g}@{''.join(eligible[name])}"
        for name in case["order"]
    ]
    return f"{case['existing_id']}-" + "-".join(bits)


_BINDING_BOOK_CASES = _binding_cases()
_BINDING_BOOK_IDS = tuple(_binding_case_id(case) for case in _BINDING_BOOK_CASES)
_BINDING_CHANNEL_CASES = tuple(
    case for case in _BINDING_BOOK_CASES if case["channel_pin"]
)
_BINDING_CHANNEL_IDS = tuple(
    _binding_case_id(case) for case in _BINDING_CHANNEL_CASES
)


def _assert_binding_booking_matches_oracle(
    booked: Mapping[str, object],
    case: Mapping[str, object],
) -> None:
    supplies = case["supplies"]
    eligible = case["eligible"]
    existing = _existing_loads_excluding_supplies(case["existing"], supplies)
    oracle = _oracle_binding_assignment(existing, supplies, eligible)
    closed = oracle["closed_form"]
    worst = max(closed.values())
    assert worst == pytest.approx(oracle["worst_max_load"])
    assert booked["worst_max_load_kg"] == pytest.approx(worst)
    assert booked["binding_segment"] == oracle["binding_segment"]
    assert set(booked["closed_form_kg"]) == set(closed)
    for segment, kg in closed.items():
        assert booked["closed_form_kg"][segment] == pytest.approx(kg)
    loads = booked["segment_loads_kg"]
    assert max(loads.values()) == pytest.approx(worst)
    assert set(loads) == set(oracle["loads"])
    for segment, kg in oracle["loads"].items():
        assert loads[segment] == pytest.approx(kg)
    priced: dict[tuple[str, str], float] = {}
    for key, kg in booked["deposit"].items():
        amount = float(kg)
        if amount <= 1.0e-12:
            continue
        priced[(str(key[0]), str(key[1]))] = amount
    assignment = oracle["assignment"]
    for species, segment in assignment.items():
        assert segment in eligible[species]
        species_keys = [key for key in priced if key[1] == species]
        assert species_keys == [(segment, species)]
        assert priced[(segment, species)] == pytest.approx(float(supplies[species]))
        assert priced[(segment, species)] <= float(supplies[species]) + 1.0e-12
    for segment, species, kg in case["existing"]:
        if species in supplies:
            continue
        assert priced[(segment, species)] == pytest.approx(float(kg))
    if (
        case["existing_id"] == "empty"
        and supplies.get("Mg") == 1.0
        and supplies.get("SiO") == 3.0
        and eligible.get("Mg") == ("A", "B")
        and eligible.get("SiO") == ("B",)
        and "Fe" not in supplies
    ):
        # Report the ruling's case: s* is B, per-segment load is 4 kg
        # there, and both supplies sit on B in either species order.
        assert booked["binding_segment"] == "B"
        assert booked["segment_loads_kg"]["B"] == pytest.approx(4.0)
        assert booked["segment_loads_kg"].get("A", 0.0) == pytest.approx(0.0)
        assert booked["closed_form_kg"]["A"] == pytest.approx(1.0)
        assert booked["closed_form_kg"]["B"] == pytest.approx(4.0)
        assert assignment == {"Mg": "B", "SiO": "B"}
    if (
        case["existing_id"] == "off_heavy_later-C"
        and "Zn" not in supplies
    ):
        # C holds Fe's 100 kg, so s* is C. Mg is eligible at A and B only.
        # B already holds 5 kg, so Mg's closed form is 1 at A and 6 at B.
        # The smallest id is A. The booked segment is B.
        assert booked["binding_segment"] == "C"
        assert booked["worst_max_load_kg"] == pytest.approx(100.0)
        assert booked["segment_loads_kg"]["C"] == pytest.approx(100.0)
        assert booked["segment_loads_kg"]["B"] == pytest.approx(6.0)
        assert "A" not in booked["segment_loads_kg"]
        assert booked["closed_form_kg"]["A"] == pytest.approx(1.0)
        assert booked["closed_form_kg"]["B"] == pytest.approx(6.0)
        assert booked["closed_form_kg"]["C"] == pytest.approx(100.0)
        assert assignment == {"Fe": "C", "Mg": "B"}


@pytest.mark.parametrize("case", _BINDING_BOOK_CASES, ids=_BINDING_BOOK_IDS)
def test_refused_wall_binding_load_matches_the_closed_form_at_the_chokepoint(
    case: Mapping[str, object],
) -> None:
    """Every eligibility, preload, supply pair, and species order.

    The chokepoint is ``_book_wall_deposits_at_vapour_supply``. Product
    summary, lifespan, and tap read its map. The oracle is the closed
    form itself: the booked maximum equals that maximum, and each supply
    is wholly on one eligible segment.
    """

    execution, _snapshots, deposit = _wall_bound_execution(
        _binding_execution_case(case)
    )
    booked = objective_module._book_wall_deposits_at_vapour_supply(
        dict(deposit),
        execution.trace.wall_deposit_sticking_authority,
        execution,
    )
    _assert_binding_booking_matches_oracle(booked, case)


def test_off_star_enumeration_covers_best_segment_and_id_tie_break() -> None:
    """The off-s* suite is not vacuous and not the smallest-id rule.

    Mg is never eligible at s*. Some cases put Mg on a segment other than
    its smallest id, which is the defect class. Some cases tie, and the
    smaller id still wins those.
    """

    differed = 0
    tied = 0
    for case in _off_star_cases():
        supplies = case["supplies"]
        eligible = case["eligible"]
        existing = _existing_loads_excluding_supplies(case["existing"], supplies)
        oracle = _oracle_binding_assignment(existing, supplies, eligible)
        assert oracle["binding_segment"] not in eligible["Mg"]
        chosen = oracle["assignment"]["Mg"]
        candidates = eligible["Mg"]
        closed = oracle["closed_form"]
        smallest = min(candidates)
        top = max(closed[segment] for segment in candidates)
        winners = [
            segment for segment in candidates if closed[segment] == top
        ]
        assert chosen == min(winners)
        if chosen != smallest:
            differed += 1
        elif len(winners) >= 2:
            tied += 1
    assert differed > 0
    assert tied > 0


@pytest.mark.parametrize("channel", _WALL_BOUND_CHANNELS)
@pytest.mark.parametrize("case", _BINDING_CHANNEL_CASES, ids=_BINDING_CHANNEL_IDS)
def test_closed_form_binding_load_holds_on_every_scoring_channel(
    channel: str,
    case: Mapping[str, object],
) -> None:
    """Every scoring channel reads the closed-form booking.

    Empty walls put Mg 1 kg and SiO 3 kg on B. The off-s* pin puts Fe's
    100 kg on C and Mg's 1 kg on B, where B already holds 5 kg. Mg's
    smallest eligible id is A.
    """

    view = _wall_bound_view(channel, _binding_execution_case(case))
    supplies = case["supplies"]
    eligible = case["eligible"]
    existing = _existing_loads_excluding_supplies(case["existing"], supplies)
    oracle = _oracle_binding_assignment(existing, supplies, eligible)
    worst = max(oracle["closed_form"].values())
    if view["entries"] is not None:
        loads: dict[str, float] = {}
        for (segment, _species), kg in view["entries"].items():
            loads[segment] = loads.get(segment, 0.0) + float(kg)
        assert max(loads.values()) == pytest.approx(worst)
        for species, segment in oracle["assignment"].items():
            assert view["entries"][(segment, species)] == pytest.approx(
                float(supplies[species])
            )
    if view["species_kg"] is not None:
        for species, supply in supplies.items():
            assert view["species_kg"][species] == pytest.approx(float(supply))
    assert view["upper_flag"] is True
    assert view["scored"] is True
    if case["existing_id"] == "empty" and view["entries"] is not None:
        assert view["entries"][("B", "Mg")] == pytest.approx(1.0)
        assert view["entries"][("B", "SiO")] == pytest.approx(3.0)
        assert ("A", "Mg") not in view["entries"]
        assert ("A", "SiO") not in view["entries"]
    if (
        case["existing_id"] == "off_heavy_later-C"
        and view["entries"] is not None
    ):
        assert view["entries"][("C", "Fe")] == pytest.approx(100.0)
        assert view["entries"][("B", "Mg")] == pytest.approx(1.0)
        assert view["entries"][("B", "Na")] == pytest.approx(5.0)
        assert ("A", "Mg") not in view["entries"]
        assert ("A", "Fe") not in view["entries"]


@pytest.mark.parametrize("order", (("Mg", "SiO"), ("SiO", "Mg")))
def test_empty_walls_book_mg_and_sio_at_four_kg_on_b(
    order: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty walls; Mg 1 kg eligible at A and B; SiO 3 kg eligible only at B.

    s* is B. The closed form is 1 kg at A and 4 kg at B. Both supplies go
    to B, so the booked load is 4 kg and the lifetime is 4.5/4 campaigns.
    The same booking is produced for both species orders.
    """

    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: 4.5)
    case = {
        "supplies": {"Mg": 1.0, "SiO": 3.0},
        "eligible": {"Mg": ("A", "B"), "SiO": ("B",)},
        "existing": (),
        "existing_id": "empty",
        "order": order,
    }
    execution, _snapshots, deposit = _wall_bound_execution(
        _binding_execution_case(case)
    )
    booked = objective_module._book_wall_deposits_at_vapour_supply(
        dict(deposit),
        execution.trace.wall_deposit_sticking_authority,
        execution,
    )
    _assert_binding_booking_matches_oracle(booked, case)
    reference = _run_reference(
        execution,
        {},
        cost_parameters=_furnace_test_cost_parameters(),
    )
    summary = reference.product_summary
    segments = summary["wall_deposit_kg_by_segment_species"]

    assert booked["binding_segment"] == "B"
    assert booked["worst_max_load_kg"] == pytest.approx(4.0)
    assert booked["segment_loads_kg"] == {"B": pytest.approx(4.0)}
    assert booked["closed_form_kg"]["A"] == pytest.approx(1.0)
    assert booked["closed_form_kg"]["B"] == pytest.approx(4.0)
    assert segments["B"]["Mg"] == pytest.approx(1.0)
    assert segments["B"]["SiO"] == pytest.approx(3.0)
    assert "Mg" not in segments.get("A", {})
    assert "SiO" not in segments.get("A", {})
    assert summary["campaigns_to_resinter"] == pytest.approx(4.5 / 4.0)
    assert summary["furnace_amortization_status"] == "available"
    assert summary["wall_deposit_upper_bound_used"] is True


@pytest.mark.parametrize("order", (("Mg", "SiO", "CrO2"), ("CrO2", "SiO", "Mg")))
def test_negligible_refused_mass_is_outside_the_closed_form(
    order: tuple[str, ...],
) -> None:
    """10 kg of measured CrO2 against a 1e-12 kg supply is not existing load.

    Leaving that measurement in the closed form would make A the binding
    segment. The authorized zero removes it, and Mg plus SiO still book
    4 kg at B.
    """

    eligible = {
        "Mg": ("A", "B"),
        "SiO": ("B",),
        "CrO2": ("A", "B"),
    }
    supplies = {"Mg": 1.0, "SiO": 3.0, "CrO2": 1.0e-12}
    execution, _snapshots, _deposit = _wall_bound_execution({
        "deposit": ((("A", "CrO2"), 10.0),),
        "flux": tuple((species, supplies[species]) for species in order),
        "refused": tuple(
            (species, segment)
            for species in order
            for segment in eligible[species]
        ),
        "charge": 1000.0,
    })
    booked = objective_module._book_wall_deposits_at_vapour_supply(
        dict(execution.trace.wall_deposit_by_segment_species_kg),
        execution.trace.wall_deposit_sticking_authority,
        execution,
    )
    upper_case = {
        "supplies": {"Mg": 1.0, "SiO": 3.0},
        "eligible": {"Mg": ("A", "B"), "SiO": ("B",)},
        "existing": (),
        "existing_id": "empty",
        "order": tuple(species for species in order if species != "CrO2"),
    }
    _assert_binding_booking_matches_oracle(booked, upper_case)
    priced_species = {
        str(key[1])
        for key, kg in booked["deposit"].items()
        if float(kg) > 1.0e-12
    }
    assert "CrO2" not in priced_species
    cro2 = next(item for item in booked["bookings"] if item["species"] == "CrO2")
    assert cro2["reason"] == "below_wall_supply_upper_bound"
    assert cro2["deposit_kg"] == 0.0
    assert cro2["supply_upper_bound_kg"] == pytest.approx(1.0e-12)


@pytest.mark.parametrize("order", (("Mg", "SiO"), ("SiO", "Mg")))
def test_missing_vapour_flux_stays_out_of_the_supply_sum(
    order: tuple[str, str],
) -> None:
    """SiO with no computed flux keeps its measured 2 kg and is not a zero.

    That 2 kg is existing load. Mg's 1 kg supply, eligible at A and B,
    then books on B. The closed form is 3 kg at B. SiO stays refused.
    """

    flux_species = tuple(species for species in order if species != "SiO")
    execution, _snapshots, deposit = _wall_bound_execution({
        "deposit": ((("B", "SiO"), 2.0),),
        "flux": tuple((species, 1.0) for species in flux_species),
        "refused": tuple(
            (species, segment)
            for species, segments in (
                ("Mg", ("A", "B")),
                ("SiO", ("B",)),
            )
            for segment in segments
            if species in order
        ),
        "charge": 1000.0,
    })
    # Refusal records follow the requested species order.
    by_species = {}
    for species in order:
        segments = ("A", "B") if species == "Mg" else ("B",)
        by_species[species] = {
            segment: {
                "status": "refused",
                "output_status": "status_bearing",
                "reason": f"antoine domain refused for {species}",
            }
            for segment in segments
        }
    execution.trace.wall_deposit_sticking_authority = {
        "wall_saturation_pressure_refusals_by_species": by_species,
    }
    booked = objective_module._book_wall_deposits_at_vapour_supply(
        dict(deposit),
        execution.trace.wall_deposit_sticking_authority,
        execution,
    )
    case = {
        "supplies": {"Mg": 1.0},
        "eligible": {"Mg": ("A", "B")},
        "existing": (("B", "SiO", 2.0),),
        "existing_id": "kept_sio",
        "order": ("Mg",),
    }
    _assert_binding_booking_matches_oracle(booked, case)
    assert booked["binding_segment"] == "B"
    assert booked["worst_max_load_kg"] == pytest.approx(3.0)
    assert booked["segment_loads_kg"]["B"] == pytest.approx(3.0)
    assert {item["species"] for item in booked["bookings"]} == {"Mg"}
    notice = booked["notice_authority"][
        "wall_saturation_pressure_refusals_by_species"
    ]
    availability = booked["availability_authority"][
        "wall_saturation_pressure_refusals_by_species"
    ]
    assert "SiO" in notice and "Mg" in notice
    assert "SiO" in availability
    assert "Mg" not in availability


def test_below_resolution_wall_booking_scores_the_authorized_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.2 kg of booked CrO2 against a 1e-12 kg supply scores as 0.

    The booking's deposit_kg was already the authorized zero. The priced
    map, the lifetime, and a pre-booking coating margin that still quotes
    the 0.2 kg must follow that zero.
    """

    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: 4.5)
    execution, _snapshots, _deposit = _wall_bound_execution({
        "deposit": ((("cold_wall", "CrO2"), 0.2),),
        "flux": (("CrO2", 1.0e-12),),
        "refused": (("CrO2", "hot_wall"),),
        "charge": 1000.0,
    })
    reference = _run_reference(
        execution,
        {},
        cost_parameters=_furnace_test_cost_parameters(),
        coating_margin=SimpleNamespace(
            status="warning",
            status_reason="wall saturation pressure refused for CrO2",
            status_payload={
                "coating_verdict": "warning",
                "campaigns_to_resinter_worst_segment": 4.5 / 0.2,
                "wall_deposit_kg_per_campaign": 0.2,
                "wall_saturation_pressure_refusals_by_species": {
                    "CrO2": {
                        "hot_wall": {
                            "status": "refused",
                            "output_status": "status_bearing",
                            "reason": "antoine domain refused for CrO2",
                        }
                    }
                },
            },
        ),
    )
    summary = reference.product_summary
    segments = summary["wall_deposit_kg_by_segment_species"]

    assert "CrO2" not in segments.get("cold_wall", {})
    assert "CrO2" not in segments.get("hot_wall", {})
    assert summary["wall_deposit_kg_by_species"].get("CrO2", 0.0) == pytest.approx(0.0)
    assert summary["campaigns_to_resinter"] == "infinite"
    assert summary["campaigns_to_resinter"] != pytest.approx(4.5 / 0.2)
    booking = next(
        item
        for item in summary["wall_supply_bound_bookings"]
        if item["species"] == "CrO2"
    )
    assert booking["reason"] == "below_wall_supply_upper_bound"
    assert booking["deposit_kg"] == 0.0
    assert booking["supply_upper_bound_kg"] == pytest.approx(1.0e-12)
    assert summary["furnace_amortization_cost_per_run_usd"] == pytest.approx(0.0)
    assert summary["furnace_amortization_status"] == "available"
    assert "CrO2" in summary["wall_deposit_sticking_authority"][
        "wall_saturation_pressure_refused_species"
    ]
    adjusted = cost_adjusted_objective_scores(
        {"oxygen_kg": 10.0, "energy_kWh": 1.0},
        DEFINITIONS,
        product_summary=summary,
    )
    assert all(score is not None for score in adjusted)


def test_mixed_measured_and_refused_wall_deposit_scores_the_full_supply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1 kg of vapour and 0.1 kg already deposited scores 1 kg, not 0.1 or 1.1."""

    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: 4.5)
    execution, _snapshots, _deposit = _wall_bound_execution({
        "deposit": ((("cold_wall", "SiO"), 0.1),),
        "flux": (("SiO", 1.0),),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
    })
    reference = _run_reference(
        execution,
        {},
        cost_parameters=_furnace_test_cost_parameters(),
    )
    summary = reference.product_summary
    segments = summary["wall_deposit_kg_by_segment_species"]

    assert summary["furnace_amortization_status"] == "available"
    assert summary["wall_deposit_upper_bound_used"] is True
    assert segments["hot_wall"]["SiO"] == pytest.approx(1.0)
    assert "SiO" not in segments.get("cold_wall", {})
    assert summary["wall_deposit_kg_by_species"]["SiO"] == pytest.approx(1.0)
    assert summary["campaigns_to_resinter"] == pytest.approx(4.5)
    booking = next(
        item
        for item in summary["wall_supply_bound_bookings"]
        if item["species"] == "SiO"
    )
    assert booking["deposit_kg"] == pytest.approx(1.0)
    assert booking["deposit_kg"] != pytest.approx(0.1)
    assert booking["deposit_kg"] != pytest.approx(1.1)
    full_supply_cost = furnace_amortization_cost_per_run(
        20.0,
        4.5,
        500.0,
        1.0,
        has_positive_fouling=True,
    )
    partial_cost = furnace_amortization_cost_per_run(
        20.0,
        45.0,
        500.0,
        1.0,
        has_positive_fouling=True,
    )
    assert summary["furnace_amortization_cost_per_run_usd"] == pytest.approx(
        full_supply_cost
    )
    assert full_supply_cost > partial_cost
    assert "SiO" in summary["wall_deposit_sticking_authority"][
        "wall_saturation_pressure_refused_species"
    ]
    adjusted = cost_adjusted_objective_scores(
        {"oxygen_kg": 10.0, "energy_kWh": 1.0},
        DEFINITIONS,
        product_summary=summary,
    )
    clean = cost_adjusted_objective_scores(
        {"oxygen_kg": 10.0, "energy_kWh": 1.0},
        DEFINITIONS,
        product_summary=_available_furnace_summary(0.0),
    )
    assert adjusted[0] is not None and clean[0] is not None
    assert adjusted[0] < clean[0]
    penalty = summary["furnace_amortization_batch_cost_equivalents"]
    front = pareto_front(
        (
            {
                "id": "bounded",
                "objectives": {"oxygen_kg": 10.0, "energy_kWh": 1.0},
                "summary": summary,
            },
            {
                "id": "dominated",
                # The supply-bound penalty divides a maximize score. 0.1 kg
                # of oxygen then beats the penalised 10 kg and both points
                # stay on the front. Sit below the penalised score so the
                # bounded candidate is the only non-dominated point.
                "objectives": {
                    "oxygen_kg": (10.0 / (1.0 + penalty)) / 2.0,
                    "energy_kWh": (1.0 + penalty) + 1.0,
                },
                "summary": _available_furnace_summary(0.0),
            },
        ),
        DEFINITIONS,
        objective_getter=lambda item: item["objectives"],
        score_getter=lambda item: cost_adjusted_objective_scores(
            item["objectives"],
            DEFINITIONS,
            product_summary=item["summary"],
        ),
    )
    assert [item["id"] for item in front] == ["bounded"]


def test_booked_refusal_keeps_furnace_price_when_coating_margin_rate_is_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-booking null coating rate must not unprice a supply-bound deposit.

    The coating margin is the gate's view of the unbooked wall. Once the
    saturation refusal it names is booked, the null rate is stale. The
    furnace price is the booked supply, and the candidate stays scored.
    """

    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: 4.5)
    execution, _snapshots, _deposit = _wall_bound_execution({
        "deposit": ((("cold_wall", "SiO"), 0.1),),
        "flux": (),
        "published_flux": (("SiO", 1.0),),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
    })
    reference = _run_reference(
        execution,
        {},
        cost_parameters=_furnace_test_cost_parameters(),
        coating_margin=SimpleNamespace(
            status="unavailable",
            status_reason="wall saturation pressure refused for SiO",
            status_payload={
                "coating_verdict": "unavailable",
                "campaigns_to_resinter_worst_segment": None,
                "wall_deposit_kg_per_campaign": None,
                "wall_saturation_pressure_refusals_by_species": {
                    "SiO": {
                        "hot_wall": {
                            "status": "refused",
                            "output_status": "status_bearing",
                            "reason": "antoine domain refused for SiO",
                        }
                    }
                },
            },
        ),
    )
    summary = reference.product_summary

    assert summary["furnace_amortization_status"] == "available"
    assert summary["wall_deposit_upper_bound_used"] is True
    assert summary["has_positive_qualified_fouling"] is True
    assert summary["campaigns_to_resinter"] == pytest.approx(4.5)
    assert "wall_deposit_refusal" not in summary
    booking = next(
        item
        for item in summary["wall_supply_bound_bookings"]
        if item["species"] == "SiO"
    )
    assert booking["deposit_kg"] == pytest.approx(1.0)
    assert booking["supply_upper_bound_kg"] == pytest.approx(1.0)
    assert "SiO" in booking["refusal_reason"]
    adjusted = cost_adjusted_objective_scores(
        {"oxygen_kg": 10.0, "energy_kWh": 1.0},
        DEFINITIONS,
        product_summary=summary,
    )
    assert all(score is not None for score in adjusted)


def test_tap_supply_bound_stops_at_the_tap_hour() -> None:
    """Later vapour is outside the tap-hour integral and does not raise it."""

    early = SimpleNamespace(
        hour=1,
        duration_h=1.0,
        evap_flux=SimpleNamespace(species_kg_hr={"SiO": 0.25}),
        wall_deposit_by_segment_species_delta={("cold_wall", "SiO"): 0.1},
    )
    later = SimpleNamespace(
        hour=2,
        duration_h=1.0,
        evap_flux=SimpleNamespace(species_kg_hr={"SiO": 10.0}),
        wall_deposit_by_segment_species_delta={},
    )
    execution, _snapshots, _deposit = _wall_bound_execution({
        "deposit": ((("cold_wall", "SiO"), 0.1),),
        "flux": (("SiO", 0.25),),
        "refused": (("SiO", "hot_wall"),),
        "charge": 1000.0,
    })
    execution.snapshots = (early, later)
    execution.trace.wall_deposit_by_segment_species_kg = {
        ("cold_wall", "SiO"): 0.1,
    }
    terminal = objective_module._coating_product_summary(execution)
    tapped = objective_module._tap_coating_product_summary(
        execution,
        (early, later),
        1,
    )

    assert terminal["wall_deposit_kg_by_segment_species"]["hot_wall"][
        "SiO"
    ] == pytest.approx(10.25)
    assert tapped["wall_deposit_kg_by_segment_species"]["hot_wall"][
        "SiO"
    ] == pytest.approx(0.25)
    assert "SiO" not in terminal["wall_deposit_kg_by_segment_species"].get(
        "cold_wall",
        {},
    )
    assert "SiO" not in tapped["wall_deposit_kg_by_segment_species"].get(
        "cold_wall",
        {},
    )


def test_finite_threshold_null_coating_rate_stays_unavailable_in_run_reference() -> None:
    reference = _run_reference(
        _refused_empty_wall_deposit_execution(),
        {},
        cost_parameters=_furnace_test_cost_parameters(),
        coating_margin=SimpleNamespace(
            status_payload={
                "campaigns_to_resinter_worst_segment": None,
                "wall_deposit_kg_per_campaign": None,
                "resinter_threshold_kg": 4.5,
            }
        ),
    )

    assert reference.product_summary["campaigns_to_resinter_worst_segment"] is None
    assert reference.product_summary["has_positive_qualified_fouling"] is None
    assert reference.product_summary["furnace_amortization_status"] == (
        "batch_cost_unavailable"
    )


def test_finite_threshold_refused_empty_deposit_is_unavailable_for_lifespan_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: 4.5)
    run_execution = _refused_empty_wall_deposit_execution()
    sim = run_execution.simulator

    summary = objective_module._furnace_lifespan_cost_summary(run_execution, sim)

    assert summary["furnace_lifespan_consumed_fraction"] is None
    assert summary["lifespan_cost_status"] == "wall_deposit_unavailable"
    assert summary["wall_deposit_total_kg"] is None
    assert summary["wall_deposit_cumulative_total_kg"] is None
    assert objective_module._furnace_lifespan_consumed_fraction(
        run_execution,
        sim,
    ) is None


def test_finite_threshold_refused_empty_deposit_is_unavailable_for_depreciation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: 4.5)
    run_execution = _refused_empty_wall_deposit_execution()
    sim = run_execution.simulator
    sim.energy_cumulative_breakdown_kWh = {
        "electrical": 0.0,
        "evaporation_thermal": 0.0,
    }
    cost_parameters = _furnace_test_cost_parameters()

    depreciation = objective_module._depreciation_expense_per_run_summary(
        run_execution,
        sim,
        cost_parameters=cost_parameters,
    )
    marginal = objective_module._marginal_extraction_cost_summary(
        sim,
        objective_module._product_ledger(sim),
        run_execution=run_execution,
        cost_parameters=cost_parameters,
    )
    metric_value = _metric_value(
        "throughput_cost_owner_ratify_usd",
        sim,
        objective_module._product_ledger(sim),
        {},
        run_execution=run_execution,
        cost_parameters=cost_parameters,
    )
    reference = _run_reference(
        run_execution,
        {},
        cost_parameters=cost_parameters,
    )
    record = SimpleNamespace(
        objectives={"throughput_cost_owner_ratify_usd": None},
        product_summary=reference.product_summary,
    )

    assert depreciation["depreciation_expense_per_run_usd"] is None
    assert depreciation["status"] == "wall_deposit_unavailable"
    assert depreciation["wall_deposit_total_kg"] is None
    assert marginal["marginal_cost_usd"] is None
    assert metric_value is None
    assert study._record_objective_scores(
        record,
        (ObjectiveDefinition("throughput_cost_owner_ratify_usd", "minimize", "USD"),),
    ) == (None,)


def test_furnace_amortization_clean_recipe_skips_missing_lifetime() -> None:
    assert furnace_amortization_cost_per_run(
        20.0, None, 0.0, 1.0, has_positive_fouling=False
    ) == 0.0


@pytest.mark.parametrize("campaigns", (math.nan, -1.0))
@pytest.mark.parametrize("has_positive_fouling", (False, True))
def test_furnace_amortization_degenerate_lifetime_respects_fouling_bright_line(
    campaigns: float,
    has_positive_fouling: bool,
) -> None:
    cost = furnace_amortization_cost_per_run(
        20.0,
        campaigns,
        500.0,
        1.0,
        has_positive_fouling=has_positive_fouling,
    )

    if has_positive_fouling:
        assert cost == furnace_amortization_cost_per_run(
            20.0, 0.0, 500.0, 1.0, has_positive_fouling=True
        )
    else:
        assert cost == 0.0


@pytest.mark.parametrize(
    "campaigns",
    (
        pytest.param(None, id="none"),
        pytest.param(math.nan, id="nan"),
        pytest.param(math.inf, id="positive-infinity"),
        pytest.param(-1.0, id="negative"),
        pytest.param(0.0, id="instant-death"),
        pytest.param(1.0, id="one-campaign"),
        pytest.param(1e9, id="long-lived"),
    ),
)
@pytest.mark.parametrize("has_positive_fouling", (False, True))
@pytest.mark.parametrize("multiplier", (0.0, 500.0))
def test_furnace_amortization_degenerate_input_matrix(
    campaigns: float | None,
    has_positive_fouling: bool,
    multiplier: float,
) -> None:
    cost = furnace_amortization_cost_per_run(
        20.0,
        campaigns,
        multiplier,
        1.0,
        has_positive_fouling=has_positive_fouling,
    )

    assert math.isfinite(cost)
    if not has_positive_fouling:
        assert cost == 0.0
    elif campaigns is None or math.isinf(campaigns):
        assert cost == 20.0
    elif campaigns == 0.0 or (
        isinstance(campaigns, float) and (math.isnan(campaigns) or campaigns < 0.0)
    ):
        assert cost == max(
            20.0,
            multiplier
            * 20.0
            / objective_module.FURNACE_MIN_MEANINGFUL_LIFETIME_CAMPAIGNS,
        ) * objective_module.FURNACE_INSTANT_DEATH_FACTOR
    else:
        assert cost == max(20.0, multiplier * 20.0 / campaigns)


def test_furnace_amortization_cost_is_monotonic_across_instant_death() -> None:
    campaigns = (0.0, 1e-6, 0.01, 1.0, 500.0, math.inf)
    costs = tuple(
        furnace_amortization_cost_per_run(
            20.0,
            lifetime,
            500.0,
            1.0,
            has_positive_fouling=True,
        )
        for lifetime in campaigns
    )

    assert all(left >= right for left, right in zip(costs, costs[1:]))
    assert costs[3:] == (10_000.0, 20.0, 20.0)


@pytest.mark.parametrize("batch_cost", (0.0, -1.0))
def test_furnace_amortization_summary_rejects_nonpositive_fouling_batch_cost(
    batch_cost: float,
) -> None:
    params = CostParameters(
        electricity_cost_per_kWh=1.0,
        furnace_resinter_cost_usd=1.0,
        depreciation_expense_per_run=1.0,
        generic_reagent_cost_per_kg=1.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 1.0,
            "K": 1.0,
            "Mg": 1.0,
            "Ca": 1.0,
        },
        furnace_lifetime_cost_multiplier=500.0,
        min_fouling_penalty=1.0,
    )
    sim = SimpleNamespace(
        record=SimpleNamespace(
            cost_rollup={
                "run_input_cost": {
                    "owner_ratify_money_projection": batch_cost,
                }
            }
        )
    )

    fouling = objective_module._furnace_amortization_summary(
        sim,
        1.0,
        has_positive_fouling=True,
        cost_parameters=params,
    )
    clean = objective_module._furnace_amortization_summary(
        sim,
        1.0,
        has_positive_fouling=False,
        cost_parameters=params,
    )

    assert fouling["furnace_amortization_status"] == "batch_cost_unavailable"
    if batch_cost == 0.0:
        assert clean["furnace_amortization_status"] == "available"
        assert clean["furnace_amortization_cost_per_run_usd"] == 0.0
    else:
        assert clean["furnace_amortization_status"] == "batch_cost_unavailable"


def test_cost_adjustment_bounds_tiny_lifetime_and_rejects_overflow() -> None:
    penalty = furnace_amortization_cost_per_run(
        20.0,
        1e-304,
        500.0,
        1.0,
        has_positive_fouling=True,
    ) / 20.0
    adjusted = cost_adjusted_objective_scores(
        {"cost": 100.0},
        (ObjectiveDefinition("cost", "minimize", "usd"),),
        product_summary=_available_furnace_summary(penalty),
    )

    assert math.isfinite(adjusted[0])
    with pytest.raises(ObjectiveComputationError, match="adjusted objective"):
        cost_adjusted_objective_scores(
            {"cost": 1e308},
            (ObjectiveDefinition("cost", "minimize", "usd"),),
            product_summary=_available_furnace_summary(1.0),
        )


def test_cost_adjustment_rejects_positive_maximize_underflow() -> None:
    with pytest.raises(ObjectiveComputationError, match="underflow"):
        cost_adjusted_objective_scores(
            {"yield": 1e-300},
            (ObjectiveDefinition("yield", "maximize", "kg"),),
            product_summary=_available_furnace_summary(1e100),
        )


@pytest.mark.parametrize(
    "summary",
    (
        {},
        {"furnace_amortization_status": None},
        {"furnace_amortization_status": "available"},
    ),
)
def test_cost_adjustment_requires_complete_furnace_evidence(
    summary: dict[str, object],
) -> None:
    with pytest.raises(ObjectiveComputationError, match="furnace amortization"):
        cost_adjusted_objective_scores(
            {"oxygen_kg": 1.0},
            (ObjectiveDefinition("oxygen_kg", "maximize", "kg"),),
            product_summary=summary,
        )


@pytest.mark.parametrize("sense", ("maximize", "minimize"))
def test_cost_adjustment_rejects_negative_objective_values(sense: str) -> None:
    definition = (ObjectiveDefinition("metric", sense, "unit"),)
    with pytest.raises(ObjectiveComputationError, match="must be non-negative"):
        cost_adjusted_objective_scores(
            {"metric": -1.0},
            definition,
            product_summary=_available_furnace_summary(1.0),
        )

    assert cost_adjusted_objective_scores(
        {"metric": 0.0},
        definition,
        product_summary=_available_furnace_summary(1.0),
    ) == (0.0,)


def test_furnace_amortization_instant_death_is_worst_with_zero_multiplier() -> None:
    instant_death = furnace_amortization_cost_per_run(
        20.0, 0.0, 0.0, 1.0, has_positive_fouling=True
    )
    one_campaign = furnace_amortization_cost_per_run(
        20.0, 1.0, 0.0, 1.0, has_positive_fouling=True
    )
    assert math.isfinite(instant_death)
    assert instant_death > one_campaign


def test_furnace_amortization_none_reaches_summary_as_infinite_lifetime() -> None:
    params = CostParameters(
        electricity_cost_per_kWh=1.0,
        furnace_resinter_cost_usd=1.0,
        depreciation_expense_per_run=1.0,
        generic_reagent_cost_per_kg=1.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 1.0,
            "K": 1.0,
            "Mg": 1.0,
            "Ca": 1.0,
        },
        furnace_lifetime_cost_multiplier=500.0,
        min_fouling_penalty=1.0,
    )
    sim = SimpleNamespace(
        record=SimpleNamespace(
            cost_rollup={
                "run_input_cost": {"owner_ratify_money_projection": 20.0}
            }
        )
    )

    clean = objective_module._furnace_amortization_summary(
        sim,
        None,
        has_positive_fouling=False,
        cost_parameters=params,
    )
    fouling = objective_module._furnace_amortization_summary(
        sim,
        None,
        has_positive_fouling=True,
        cost_parameters=params,
    )

    assert clean["furnace_amortization_cost_per_run_usd"] == 0.0
    assert clean["furnace_amortization_batch_cost_equivalents"] == 0.0
    assert fouling["furnace_amortization_cost_per_run_usd"] == 20.0
    assert fouling["furnace_amortization_batch_cost_equivalents"] == 1.0
    clean_nan = objective_module._furnace_amortization_summary(
        sim,
        math.nan,
        has_positive_fouling=False,
        cost_parameters=params,
    )
    fouling_nan = objective_module._furnace_amortization_summary(
        sim,
        math.nan,
        has_positive_fouling=True,
        cost_parameters=params,
    )
    assert clean_nan["furnace_amortization_cost_per_run_usd"] == 0.0
    assert fouling_nan["furnace_amortization_cost_per_run_usd"] == (
        furnace_amortization_cost_per_run(
            20.0, 0.0, 500.0, 1.0, has_positive_fouling=True
        )
    )


def test_ungrounded_resinter_lifetime_returns_typed_amortization_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: None)
    campaigns_to_resinter = objective_module._campaigns_to_resinter(
        {("hot_duct", "Rb"): 1.66e-4}
    )
    assert campaigns_to_resinter == "resinter_threshold_kg / 0.000166"
    params = CostParameters(
        electricity_cost_per_kWh=1.0,
        furnace_resinter_cost_usd=1.0,
        depreciation_expense_per_run=1.0,
        generic_reagent_cost_per_kg=1.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 1.0,
            "K": 1.0,
            "Mg": 1.0,
            "Ca": 1.0,
        },
        furnace_lifetime_cost_multiplier=500.0,
        min_fouling_penalty=1.0,
    )
    sim = SimpleNamespace(
        record=SimpleNamespace(
            cost_rollup={
                "run_input_cost": {"owner_ratify_money_projection": 20.0}
            }
        )
    )

    summary = objective_module._furnace_amortization_summary(
        sim,
        campaigns_to_resinter,
        has_positive_fouling=True,
        cost_parameters=params,
    )

    assert summary["furnace_amortization_status"] == "resinter_threshold_ungrounded"
    assert summary["furnace_amortization_batch_cost_equivalents"] == 0.0
    assert summary["campaigns_to_resinter"] == campaigns_to_resinter


def test_instant_death_furnace_cost_stays_finite_through_pareto_scoring() -> None:
    params = CostParameters(
        electricity_cost_per_kWh=1.0,
        furnace_resinter_cost_usd=1.0,
        depreciation_expense_per_run=1.0,
        generic_reagent_cost_per_kg=1.0,
        shuttle_reagent_replacement_cost_per_kg={
            "Na": 1.0,
            "K": 1.0,
            "Mg": 1.0,
            "Ca": 1.0,
        },
        furnace_lifetime_cost_multiplier=500.0,
        min_fouling_penalty=1.0,
    )
    sim = SimpleNamespace(
        record=SimpleNamespace(
            cost_rollup={
                "run_input_cost": {"owner_ratify_money_projection": 20.0}
            }
        )
    )
    instant_summary = objective_module._furnace_amortization_summary(
        sim,
        0.0,
        has_positive_fouling=True,
        cost_parameters=params,
    )
    definitions = (
        ObjectiveDefinition("quality", "maximize", "score_0_1", ordinal=0),
        ObjectiveDefinition("waste", "minimize", "kg", ordinal=1),
    )
    items = (
        {
            "id": "instant-death",
            "objectives": {"quality": 1.0, "waste": 0.0},
            "summary": instant_summary,
        },
        {
            "id": "clean",
            "objectives": {"quality": 1.0, "waste": 0.0},
            "summary": _available_furnace_summary(0.0),
        },
    )

    adjusted = tuple(
        cost_adjusted_objective_scores(
            item["objectives"], definitions, product_summary=item["summary"]
        )
        for item in items
    )
    front = pareto_front(
        items,
        definitions,
        objective_getter=lambda item: item["objectives"],
        score_getter=lambda item: cost_adjusted_objective_scores(
            item["objectives"], definitions, product_summary=item["summary"]
        ),
    )

    assert all(math.isfinite(score) for scores in adjusted for score in scores)
    assert [item["id"] for item in front] == ["clean"]


def test_furnace_amortization_requires_positive_floor_when_multiplier_relaxed() -> None:
    with pytest.raises(
        ObjectiveComputationError, match="min_fouling_penalty must be positive"
    ):
        furnace_amortization_cost_per_run(
            20.0, math.inf, 0.0, 0.0, has_positive_fouling=True
        )

    clean = furnace_amortization_cost_per_run(
        20.0, None, 0.0, 1.0, has_positive_fouling=False
    )
    fouling = furnace_amortization_cost_per_run(
        20.0, math.inf, 0.0, 1.0, has_positive_fouling=True
    )
    assert clean == 0.0
    assert fouling > clean


_MALFORMED_WALL_DEPOSIT_REFUSAL = (
    "malformed wall_deposit_by_segment_species_delta"
)


def _wall_delta_snapshot(hour: int, delta: object | None, *, present: bool = True):
    if not present:
        return SimpleNamespace(hour=hour)
    return SimpleNamespace(hour=hour, wall_deposit_by_segment_species_delta=delta)


@pytest.mark.parametrize(
    "kwargs",
    (
        pytest.param({"wall_deposit_by_segment_species_delta": None}, id="none"),
        pytest.param({"wall_deposit_by_segment_species_delta": "x"}, id="non-mapping"),
        pytest.param({"wall_deposit_by_segment_species_delta": [1, 2]}, id="list"),
    ),
)
def test_malformed_wall_deposit_delta_cannot_be_treated_as_zero_deposit(
    kwargs: dict[str, object],
) -> None:
    """A field that is PRESENT but unreadable is corrupt input and must refuse.

    Treating it as "no deposits" is fail-open: it prices the furnace as
    never-fouling on the strength of data we could not read.
    """
    snapshot = SimpleNamespace(hour=1, **kwargs)
    with pytest.raises(ObjectiveComputationError, match=_MALFORMED_WALL_DEPOSIT_REFUSAL):
        objective_module._cumulative_wall_deposit_by_segment_species_kg((snapshot,), 1)


def test_absent_wall_deposit_delta_is_representable_not_a_refusal() -> None:
    """★ ABSENT is NOT malformed, and must NOT raise.

    A snapshot that never carried the field is UNKNOWN COVERAGE. The authority
    layer represents that first-class (wall_deposit_coverage_unknown,
    non-authoritative) and does not hard-block, because coating is a continuous
    rate-to-lifespan model rather than a gate. Raising here would convert a
    representable unknown into an abort -- the over-correction the
    three-category doctrine warns against, and it would break the seam contract
    that absence yields {} so the authority layer can mark it.
    """
    absent = SimpleNamespace(hour=1)
    assert objective_module._cumulative_wall_deposit_by_segment_species_kg(
        (absent,), 1
    ) == {}


def test_measured_zero_wall_deposit_still_prices_as_never_fouling() -> None:
    empty = _wall_delta_snapshot(1, {})
    zero_entry = _wall_delta_snapshot(1, {("duct", "SiO"): 0.0})
    empty_deposits = objective_module._cumulative_wall_deposit_by_segment_species_kg(
        (empty,), 1
    )
    zero_deposits = objective_module._cumulative_wall_deposit_by_segment_species_kg(
        (zero_entry,), 1
    )
    assert empty_deposits == {}
    # A MEASURED zero now carries an explicit key; an ABSENT measurement carries
    # none. This line used to assert both were {} -- i.e. that a measured zero and
    # a never-measured segment were indistinguishable, which is precisely the
    # conflation b-306/b-311 exist to remove, and which the sibling test
    # test_unmeasured_wall_deposit_delta_is_distinguishable_from_measured_zero
    # asserts must NOT hold. The old value was produced by the pre-3f0491ac
    # consumer, which dropped near-zero amounts instead of recording them;
    # after 3f0491ac (signed wall-deposit deltas) it is unreachable. The subject
    # of this test -- that a measured zero still prices as never-fouling -- is
    # unchanged and is asserted below.
    assert zero_deposits == {("duct", "SiO"): 0.0}
    assert objective_module._campaigns_to_resinter(empty_deposits) == "infinite"
    assert objective_module._has_positive_wall_deposit(empty_deposits) is False

    never_fouling = furnace_amortization_cost_per_run(
        20.0, math.inf, 500.0, 1.0, has_positive_fouling=False
    )
    measured_infinite = furnace_amortization_cost_per_run(
        20.0, math.inf, 500.0, 1.0, has_positive_fouling=True
    )
    assert never_fouling == 0.0
    assert measured_infinite == 20.0

    summary = objective_module._tap_coating_product_summary(SimpleNamespace(), (empty,), 1)
    assert summary["campaigns_to_resinter"] == "infinite"
    assert summary["has_positive_qualified_fouling"] is False


def test_unmeasured_wall_deposit_delta_is_distinguishable_from_measured_zero() -> None:
    measured_zero = objective_module._cumulative_wall_deposit_by_segment_species_kg(
        (_wall_delta_snapshot(1, {}),), 1
    )
    assert measured_zero == {}
    malformed = (
        _wall_delta_snapshot(1, None),
        _wall_delta_snapshot(1, "x"),
    )
    for snapshot in malformed:
        with pytest.raises(
            ObjectiveComputationError, match=_MALFORMED_WALL_DEPOSIT_REFUSAL
        ):
            objective_module._cumulative_wall_deposit_by_segment_species_kg(
                (snapshot,), 1
            )
    # ABSENT is the third case and behaves differently on purpose: it yields {}
    # so the authority layer can mark it wall_deposit_coverage_unknown, rather
    # than aborting the run.
    assert objective_module._cumulative_wall_deposit_by_segment_species_kg(
        (_wall_delta_snapshot(1, None, present=False),), 1
    ) == {}


def test_malformed_in_window_hour_refuses_and_later_hour_does_not_poison() -> None:
    measured_zero = _wall_delta_snapshot(1, {})
    later_malformed = _wall_delta_snapshot(2, None)
    assert objective_module._cumulative_wall_deposit_by_segment_species_kg(
        (measured_zero, later_malformed), 1
    ) == {}
    control = _wall_delta_snapshot(1, {("duct", "SiO"): 0.75})
    assert objective_module._cumulative_wall_deposit_by_segment_species_kg(
        (control,), 1
    ) == {("duct", "SiO"): 0.75}
    with pytest.raises(ObjectiveComputationError, match=_MALFORMED_WALL_DEPOSIT_REFUSAL):
        objective_module._cumulative_wall_deposit_by_segment_species_kg(
            (control, later_malformed), 2
        )
    with pytest.raises(ObjectiveComputationError, match=_MALFORMED_WALL_DEPOSIT_REFUSAL):
        objective_module._tap_coating_product_summary(
            SimpleNamespace(),
            (later_malformed,),
            2,
        )


def test_pareto_front_keeps_nullable_objective_identity() -> None:
    definitions = (
        ObjectiveDefinition("primary", "maximize", "kg", ordinal=0),
        ObjectiveDefinition("middle", "minimize", "kWh", ordinal=1),
        ObjectiveDefinition("last", "maximize", "kg", ordinal=2),
    )
    items = (
        {"id": "primary-null", "objectives": {"primary": None, "middle": 4.0, "last": 1.0}},
        {"id": "middle-null", "objectives": {"primary": 10.0, "middle": None, "last": 1.0}},
        {"id": "last-null", "objectives": {"primary": 10.0, "middle": 4.0, "last": None}},
        {"id": "dominated", "objectives": {"primary": 9.0, "middle": 5.0, "last": 1.0}},
    )

    front = pareto_front(
        items,
        definitions,
        objective_getter=lambda item: item["objectives"],
    )

    assert [item["id"] for item in front] == [
        "primary-null",
        "middle-null",
        "last-null",
    ]


class _FakeLedger:
    # Keep the product summary fixture source-backed: tapped Fe is an ingot,
    # while staged SiO and H2O come from the condensation account.
    def kg_by_account(self, account: str) -> dict[str, float]:
        if account == "process.condensation_train":
            return {"SiO": 40.0, "H2O": 5.0}
        if account == "terminal.drain_tap_material":
            return {"Fe": 50.0}
        return {}

    project_account_kg = kg_by_account


class _FakeProductSim:
    atom_ledger = _FakeLedger()

    def __init__(self) -> None:
        self._feedstock_recovered_reagent_kg_by_species = {}
        self._non_feedstock_reagent_element_kg_by_account = {}
        self.train = SimpleNamespace(
            stages=(
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={"SiO": 40.0}),
            )
        )
        self.record = SimpleNamespace(
            feedstock_key="lunar_mare_low_ti",
            batch_mass_kg=1000.0,
            additives_kg={"CaO": 1.5},
            initial_inventory=SimpleNamespace(
                melt_oxide_kg={"FeO": 80.0, "SiO2": 100.0},
            ),
            snapshots=(
                SimpleNamespace(
                    c2a_staged_gas={
                        "stage_name": "alkali_early_fe",
                        "gas_cover_mode": "po2_hold",
                    },
                    condensed_by_stage_species_delta={},
                    evap_flux=SimpleNamespace(carrier_authority_by_species={}),
                ),
                SimpleNamespace(
                    mass_in_kg=1001.5,
                    mass_out_kg=1001.5,
                    mass_balance_error_pct=0.0,
                    c2a_staged_gas={
                        "stage_name": "sio_window",
                        "gas_cover_mode": "pn2_sweep",
                    },
                    condensed_by_stage_species_delta={(3, "SiO"): 40.0},
                    evap_flux=SimpleNamespace(
                        carrier_authority_by_species={
                            "SiO": {
                                "species_id": "SiO",
                                "pressure": {"kind": "value", "pa": 1.0},
                                "flux": {"kind": "eligible"},
                                "verdict_status": "status_bearing_non_authoritative",
                                "certification_ceiling": "never",
                                "validation_status": "pending_validation",
                                "is_flux_active": True,
                                "authority_level": "extrapolated",
                                "valid_range_K": [1400.0, 2200.0],
                                "reason": "outside certified SiO source band",
                            },
                        },
                    ),
                ),
            ),
        )

    def product_ledger(self) -> dict[str, float]:
        return {"Fe": 50.0, "SiO": 40.0, "H2O": 5.0}

    def _terminal_rump_by_species(self) -> dict[str, float]:
        return {"Al2O3": 80.0}

    def _oxygen_terminal_partition_kg(self) -> dict[str, float]:
        return {
            "stored": 20.0,
            "vented": 0.0,
            "total": 20.0,
            "mre_anode_stored": 20.0,
        }

    def _unspent_additive_reagents_kg(self) -> dict[str, float]:
        return {}

    def _c3_alkali_credit_outstanding_kg_by_species(self) -> dict[str, float]:
        return {}


def _refused_empty_wall_deposit_execution() -> SimpleNamespace:
    sim = _FakeProductSim()
    sim.record.cost_rollup = {
        "run_input_cost": {"owner_ratify_money_projection": 20.0}
    }
    return SimpleNamespace(
        simulator=sim,
        trace=SimpleNamespace(
            wall_deposit_by_segment_species_kg={},
            wall_zone_by_segment={},
            wall_deposit_sticking_authority={
                "wall_saturation_pressure_refusals_by_species": {
                    "SiO": {
                        "hot_wall": {
                            "status": "refused",
                            "output_status": "status_bearing",
                        }
                    }
                }
            },
        ),
    )


class _FakeUnclassifiedProductSim(_FakeProductSim):
    def product_ledger(self) -> dict[str, float]:
        ledger = dict(super().product_ledger())
        ledger["MysteryOxide"] = 7.0
        return ledger


class _FakeSso2Transition:
    def __init__(self, name: str = "native_fe_saturation_split") -> None:
        self.name = name


class _FakeSso2Ledger:
    def __init__(
        self,
        accounts: dict[str, dict[str, float]],
        *,
        split_present: bool = True,
        transition_name: str = "native_fe_saturation_split",
    ) -> None:
        self._accounts = accounts
        self.transitions = (
            (_FakeSso2Transition(transition_name),) if split_present else ()
        )

    def kg_by_account(self, account: str) -> dict[str, float]:
        return dict(self._accounts.get(account, {}))

    project_account_kg = kg_by_account


def _fake_sso2_stage_gas() -> dict[str, float | str]:
    return {
        "stage_name": "sio_window",
        "gas_cover_mode": "pn2_sweep",
        "atmosphere": "PN2_SWEEP",
        "pO2_mbar": SSO2_CERTIFIED_PO2_MBAR,
        "pN2_mbar": SSO2_CERTIFIED_PN2_MBAR,
        "p_total_mbar": SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR,
        "requested_p_total_mbar": SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR,
    }


def _fake_sso2_partition_basis(
    *,
    native_fe_source_account: str = "process.cleaned_melt",
    native_fe_split_commit_status: str = "ok",
) -> dict[str, float | str]:
    return {
        "native_fe_pool_mol": 12.0,
        "native_fe_tap_mol": 11.5,
        "native_fe_vapor_mol": 0.5,
        "native_fe_vapor_escape_fraction_of_pool": 0.5 / 12.0,
        "native_fe_vapor_capacity_mol_hr": 0.5,
        "temperature_K": 1923.15,
        "overhead_pressure_pa": SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR * 100.0,
        "overhead_pressure_source": "melt.p_total_mbar",
        "carrier_gas": "N2",
        "source_label": "fake native Fe partition basis",
        "native_fe_source_account": native_fe_source_account,
        "native_fe_split_commit_status": native_fe_split_commit_status,
        "native_fe_vapor_route_status": "committed",
    }


def _sso2_execution(
    *,
    condensed_delta: dict[tuple[int, str], float] | None = None,
    ledger: _FakeSso2Ledger | None = None,
    snapshot_partition_present: bool | None = None,
    snapshot_partition_source_account: str = "process.cleaned_melt",
    snapshot_partition_commit_status: str = "ok",
):
    split_present = bool(getattr(ledger, "transitions", ())) if ledger is not None else False
    if snapshot_partition_present is None:
        snapshot_partition_present = split_present
    snapshot = SimpleNamespace(
        hour=1,
        campaign="C2A_staged",
        mass_balance_error_pct=1.2e-13,
        c2a_staged_gas=_fake_sso2_stage_gas(),
        fe_redox_split=(
            {"native_fe_partition": _fake_sso2_partition_basis(
                native_fe_source_account=snapshot_partition_source_account,
                native_fe_split_commit_status=snapshot_partition_commit_status,
            )}
            if snapshot_partition_present
            else {}
        ),
    )
    trace = SimpleNamespace(
        snapshots=(snapshot,),
        condensed_by_stage_species_delta=(
            dict(condensed_delta or {}),
        ),
        wall_deposit_by_segment_species_delta=({},),
        wall_deposit_by_segment_species_kg={},
        wall_zone_by_segment={},
        wall_deposit_sticking_authority={},
    )
    sim = SimpleNamespace(
        atom_ledger=ledger,
        species_formula_registry={},
        product_ledger=lambda: {"Fe": 8.0},
    )
    return SimpleNamespace(
        simulator=sim,
        snapshots=(snapshot,),
        trace=trace,
    )


def test_sso2_owner_recipe_patch_is_named_allowlisted_fe_then_sio() -> None:
    patch = sso2_owner_recipe_setpoints_patch()
    staged = patch["campaigns"]["C2A_staged"]
    stages = {stage["name"]: stage for stage in staged["stages"]}

    assert staged["order"] == "fe_then_sio"
    assert stages["fe_hot_hold"]["gas_cover_mode"] == "pn2_sweep"
    assert stages["fe_hot_hold"]["pO2_mbar"] == pytest.approx(SSO2_CERTIFIED_PO2_MBAR)
    assert stages["fe_hot_hold"]["p_total_mbar"] == pytest.approx(
        SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR
    )
    assert stages["sio_window"]["target_C"] == pytest.approx(1650.0)
    assert stages["sio_window"]["gas_cover_mode"] == "pn2_sweep"
    assert stages["sio_window"]["pO2_mbar"] == pytest.approx(SSO2_CERTIFIED_PO2_MBAR)
    assert stages["sio_window"]["p_total_mbar"] == pytest.approx(
        SSO2_CERTIFIED_TOTAL_PRESSURE_MBAR
    )
    assert (
        stages["sio_window"]["p_total_mbar"] - stages["sio_window"]["pO2_mbar"]
    ) == pytest.approx(SSO2_CERTIFIED_PN2_MBAR)


def _sso2_sim_alpha_fallback_disabled(*, hours: int = 3):
    """Same SSO-2 owner recipe with the prototype alpha fallback left at its
    production default (disabled) — the refusal-direction leg of the b-314
    both-directions pin. Steps hour by hour and collects each hour's
    missing-alpha refusal records, because the last-diagnostic slot is
    volatile (later pipeline stages overwrite it) and a species only appears
    in an hour where its pressure is positive."""
    from simulator.optimize import sso2_evidence as _sso2
    from simulator.runner import PyrolysisRun
    from simulator.session import SimSession

    patch = _sso2.sso2_owner_recipe_setpoints_patch()
    dose_kg = _sso2._certified_full_feo_equiv_na_dose_kg(
        mass_kg=1000.0,
        backend_name=_sso2.ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        setpoints_patch=patch,
    )
    run = PyrolysisRun(
        feedstock_id=_sso2.SSO2_FEEDSTOCK_ID,
        campaign="C2A_staged",
        hours=int(hours),
        mass_kg=1000.0,
        backend_name=_sso2.ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        additives_kg={_sso2.SSO2_CERTIFIED_DOSE_SPECIES: dose_kg},
        setpoints_patch=patch,
    )
    session = SimSession().start(run._session_config())
    _sso2._apply_certified_na_dose(session.simulator)
    sim = session.simulator
    missing_alpha_by_hour = []
    for _ in range(int(hours)):
        sim.step()
        diag = getattr(sim, "_last_evaporation_flux_diagnostic", {}) or {}
        missing_alpha_by_hour.append(diag.get("missing_alpha") or {})
    return sim, missing_alpha_by_hour


def test_sso2_owner_execution_uses_certified_na_dose_and_partition_path() -> None:
    execution = build_sso2_owner_recipe_execution(hours=9)
    evidence = sso2_owner_recipe_evidence(execution)
    calibration = validation_map.calibrate_dose()

    surface = evidence["certified_sso_r_surface"]
    partition = evidence["fe_drain_vapor_partition_dependency"]
    stage_gas = surface["stage_gas_snapshot"]
    partition_basis = partition["native_fe_partition_basis"]

    assert execution.status in {"ok", "partial"}
    assert surface["dose_species"] == "Na"
    assert surface["dose_kg"] == pytest.approx(calibration.full_feo_equiv_dose_kg)
    assert surface["dose_transition_count"] >= 1
    assert surface["declared_pO2_mbar"] == pytest.approx(SSO2_CERTIFIED_PO2_MBAR)
    assert surface["declared_pN2_mbar"] == pytest.approx(SSO2_CERTIFIED_PN2_MBAR)
    assert surface["pO2_mbar"] == pytest.approx(SSO2_CERTIFIED_PO2_MBAR)
    assert surface["pN2_mbar"] == pytest.approx(SSO2_CERTIFIED_PN2_MBAR)
    assert surface["pO2_mbar"] == pytest.approx(stage_gas["pO2_mbar"])
    assert surface["pN2_mbar"] == pytest.approx(stage_gas["pN2_mbar"])
    # adf1059's authoritative metallic-tap provider commits
    # native_fe_metal_partition from process.metal_phase, while the hour
    # snapshot carries the matching drain/vapor basis.  Both ledger commit and
    # snapshot basis are required before this evidence may report available.
    assert partition["status"] == "available"
    assert partition["status_reason"] == ""
    assert partition["stage_gas_snapshot"]["pO2_mbar"] == pytest.approx(
        surface["pO2_mbar"]
    )
    assert partition_basis["status"] == "available"
    assert partition_basis["native_fe_source_account"] == "process.metal_phase"
    assert partition_basis["native_fe_split_commit_status"] == "ok"
    assert partition_basis["native_fe_vapor_route_status"] == "committed"
    assert partition["native_fe_saturation_split_count"] == 0
    assert partition["native_fe_metal_partition_count"] >= 1
    assert partition["native_fe_partition_transition_count"] >= 1
    assert partition["tap_basis"]["status"] == "available"
    assert evidence["fe_tap"]["status"] == "available"
    # The tap drained the source metal phase, so the terminal tap and aggregate
    # product ledger now carry Fe while the source account is empty.
    assert evidence["metal_product_path"]["status"] == "drained_to_tap"
    assert evidence["metal_product_path"]["Fe_kg"] == pytest.approx(0.0)
    assert evidence["metal_product_path"]["product_ledger_Fe_kg"] > 0.0
    fallback = evidence["prototype_alpha_fallback_provenance"]
    # ce14fd3 (VR-11; DESIGN-REV5 §1.2/§7.4) made the typed VapourBatch
    # the eligibility authority: CrO2's policy-only alpha row is a missing
    # channel contract, not an executable alpha value.
    # 0c6d9811 (b-314) then wired the broad_proxy_not_intrinsic predicate into
    # flux admission: CrO2's tier-2 proxy (inherited from Cr metal) no longer
    # satisfies the measured-alpha requirement, so the explicitly gated
    # alpha=1.0 prototype fallback — which SSO-2 opts into for exactly this
    # trace species — now engages instead of the proxy silently driving flux
    # as if it were a measurement.  The engagement is the designed visible
    # signal; the evidence surface must report it, not hide it.
    assert fallback == {
        "severity": "warning",
        "status": "engaged",
        "policy": "alpha=1.0 prototype fallback",
        "scope": "SSO-2 trace CrO2 species lacking grounded evaporation alpha",
        "permitted_species": ["CrO2"],
        "engaged_species": ["CrO2"],
        "total_engagement_count": 9,
    }
    report = _markdown_report(evidence, execution)
    assert "WARNING prototype_alpha_fallback" in report
    assert "alpha=1.0 prototype fallback" in report
    assert "status=`engaged`" in report
    assert "permitted_species=`CrO2`" in report
    assert "engaged_species=`CrO2`" in report
    assert "engagement_count=`9`" in report

    # Refusal direction of the same b-314 pin: with the fallback control at
    # its production default (disabled), the same recipe must NOT engage the
    # prototype path and must NOT flux CrO2 on the strength of an unmeasured
    # alpha — the SC-67 fail-loud refusal names CrO2 in missing_alpha. The
    # engagement pinned above is therefore strictly the deliberate,
    # allowlisted, evidence-reported opt-in, never absence resolving toward
    # the confident answer. (Allowlist boundedness is pinned on the engaged
    # side by _unmeasured_alpha_fallback_notice itself: it raises if any
    # non-permitted species engages, and the engaged run's own diagnostic
    # still records CrO — unmeasured but NOT allowlisted — in missing_alpha.)
    refusal_sim, refusal_missing_by_hour = _sso2_sim_alpha_fallback_disabled(
        hours=3
    )
    refusal_sim.setpoints.setdefault("chemistry_kernel", {})[
        "unmeasured_alpha_fallback_species"
    ] = ["CrO2"]
    refusal_missing = {}
    for hourly in refusal_missing_by_hour:
        refusal_missing.update(hourly)
    assert "CrO2" in refusal_missing
    assert refusal_missing["CrO2"]["policy"] == "fail_loud_missing_alpha"
    # No CrO2 flux transition may exist anywhere in the refusal run.
    assert not any(
        transition.reason == "evaporate_CrO2"
        for transition in refusal_sim.atom_ledger.transitions
    )
    from simulator.optimize.sso2_evidence import _unmeasured_alpha_fallback_notice

    assert _unmeasured_alpha_fallback_notice(refusal_sim) == {
        "severity": "warning",
        "status": "not_engaged",
        "policy": "alpha=1.0 prototype fallback",
        "scope": "SSO-2 trace CrO2 species lacking grounded evaporation alpha",
        "permitted_species": ["CrO2"],
        "engaged_species": [],
        "total_engagement_count": 0,
    }


def test_sso2_owner_gate_timeout_uses_flagged_floor_and_completes(monkeypatch):
    from simulator.chemistry.kernel.capabilities import ChemistryIntent
    from simulator.core import PyrolysisSimulator
    from simulator.engine_pool import EngineWorkerTimeout

    original_dispatch = PyrolysisSimulator._dispatch_only
    original_gate_curve = PyrolysisSimulator._melt_redox_liquidus_gate_curve
    gate_calls = []
    timeout_curves = []

    def timeout_gate(self, intent, **kwargs):
        if intent is ChemistryIntent.GATE_LIQUID_FRACTION:
            gate_calls.append(intent)
            raise EngineWorkerTimeout('magemin', 0.01, phase='call')
        return original_dispatch(self, intent, **kwargs)

    def capture_gate_curve(self, *args, **kwargs):
        curve = original_gate_curve(self, *args, **kwargs)
        if isinstance(curve, dict) and 'gate_provider_refusal' in curve:
            timeout_curves.append(curve)
        return curve

    monkeypatch.setattr(PyrolysisSimulator, '_dispatch_only', timeout_gate)
    monkeypatch.setattr(
        PyrolysisSimulator,
        '_melt_redox_liquidus_gate_curve',
        capture_gate_curve,
    )
    execution = build_sso2_owner_recipe_execution(hours=9)

    assert gate_calls
    assert execution.status in {'ok', 'partial'}
    assert timeout_curves
    floor = next(
        curve for curve in timeout_curves
        if curve['gate_provider_refusal']['authority'] == 'extrapolated (lower-rung)'
    )
    assert floor['source'] == 'kress91_liquid_calibration_floor'
    assert floor['gate_provider_refusal']['reason'] == 'engine_worker_timeout'


def test_sso2_fallback_notice_ignores_unscoped_generic_run_species() -> None:
    from simulator.optimize.sso2_evidence import _unmeasured_alpha_fallback_notice

    generic_sim = SimpleNamespace(
        setpoints={
            "chemistry_kernel": {"allow_unmeasured_alpha_fallback": True}
        },
        _unmeasured_alpha_fallback_species_engaged=("Al2", "CrO"),
        _degraded_path_engagement_summary=lambda: {
            "unmeasured_alpha_evaporation_fallback": {"total_count": 7}
        },
    )

    assert _unmeasured_alpha_fallback_notice(generic_sim) == {
        "severity": "warning",
        "status": "not_engaged",
        "policy": "alpha=1.0 prototype fallback",
        "scope": "SSO-2 trace CrO2 species lacking grounded evaporation alpha",
        "permitted_species": ["CrO2"],
        "engaged_species": [],
        "total_engagement_count": 0,
    }

    scoped_sim = SimpleNamespace(
        setpoints={
            "chemistry_kernel": {
                "unmeasured_alpha_fallback_species": ["CrO2"]
            }
        },
        _unmeasured_alpha_fallback_species_engaged=("CrO",),
        _degraded_path_engagement_summary=lambda: {
            "unmeasured_alpha_evaporation_fallback": {"total_count": 1}
        },
    )
    with pytest.raises(RuntimeError, match="escaped its trace-species scope: CrO"):
        _unmeasured_alpha_fallback_notice(scoped_sim)


def test_sso2_evidence_reports_stage3_fe_and_delivered_purity_margin() -> None:
    execution = _sso2_execution(
        condensed_delta={
            (1, "Fe"): 5.0,
            (1, "SiO"): 0.25,
            (3, "SiO2"): 9.0,
            (3, "Si"): 1.0,
            (3, "Fe"): 1.0,
        },
        ledger=_FakeSso2Ledger({
            "terminal.drain_tap_material": {"Fe": 7.0, "Si": 0.2},
            "process.metal_phase": {"Fe": 3.0},
        }),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["status"] == "stage_stream_purity_failed"
    assert evidence["stage_3"]["accepted_species"] == ["Si", "SiO", "SiO2"]
    assert evidence["stage_3"]["silica_species_kg"]["SiO2"] == pytest.approx(9.0)
    assert evidence["stage_3"]["silica_species_mol"]["SiO2"] > 0.0
    assert evidence["stage_3"]["Fe_kg"] == pytest.approx(1.0)
    assert evidence["stage_3"]["Fe_wt_pct"] == pytest.approx(100.0 / 11.0)
    assert evidence["delivered_stream_purity"]["feasible"] is False
    assert evidence["delivered_stream_purity"]["observed"] == pytest.approx(10.0 / 11.0)
    assert evidence["fe_tap"]["Fe_kg"] == pytest.approx(7.0)
    assert evidence["fe_tap"]["SiO_Si_impurity_wt_pct"] == pytest.approx(0.2 / 7.2 * 100.0)
    assert "chunk 3b" in evidence["reader_handoff_chunk3b"]
    assert "Stage 3 Fe contamination" in SSO2_CHUNK3B_READER_HANDOFF


def test_sso2_reader_fails_on_runtime_silica_exposure_finding() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 10.0},
        ledger=_FakeSso2Ledger({
            "terminal.drain_tap_material": {"Fe": 7.0},
            "process.metal_phase": {"Fe": 3.0},
        }),
    )
    execution.trace.coating_diagnostics = {
        "silica_exposed_to_alkali_findings": [{
            "key": "silica_exposed_to_alkali",
            "stage_number": 3,
            "species": "Na",
        }],
    }

    evidence = sso2_owner_recipe_evidence(execution)
    score, reader = sso2_owner_recipe_objective_reader(execution)

    assert evidence["wall_coating"]["feasible"] is False
    assert evidence["wall_coating"]["coating_verdict"] == "violated"
    assert evidence["wall_coating"]["coating_violation_reasons"][0]["reason"] == (
        "silica_exposed_to_alkali"
    )
    assert score == pytest.approx(0.0)
    assert reader["status"] == "wall_coating_failed"


def test_sso2_evidence_empty_fe_tap_account_fails_closed_without_zero_alias() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger({"process.metal_phase": {"Fe": 1.0}}),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["status"] == "missing_fe_tap_evidence"
    assert evidence["fe_tap"]["status"] == "missing_fe_tap_evidence"
    assert evidence["fe_tap"]["Fe_kg"] is None
    assert evidence["fe_tap"]["total_kg"] is None
    assert evidence["fe_tap"]["species_kg"] == {}


def test_sso2_evidence_missing_partition_preempts_empty_fe_tap_status() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger({"process.metal_phase": {"Fe": 1.0}}, split_present=False),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["status"] == "missing_fe_drain_vapor_partition"
    assert evidence["fe_tap"]["status"] == "missing_fe_tap_evidence"
    assert evidence["fe_tap"]["Fe_kg"] is None


def test_sso2_partition_basis_requires_committed_partition_transition() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger(
            {"terminal.drain_tap_material": {"Fe": 1.0}},
            split_present=False,
        ),
        snapshot_partition_present=True,
    )

    evidence = sso2_owner_recipe_evidence(execution)
    dependency = evidence["fe_drain_vapor_partition_dependency"]

    assert dependency["status"] == "missing_fe_drain_vapor_partition"
    assert dependency["native_fe_partition_transition_count"] == 0
    assert (
        dependency["native_fe_partition_basis"]["status"]
        == "missing_fe_drain_vapor_partition"
    )
    assert "no committed native Fe partition transition" in (
        dependency["native_fe_partition_basis"]["status_reason"]
    )


def test_sso2_partition_basis_accepts_committed_metallic_tap_transition() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger(
            {"terminal.drain_tap_material": {"Fe": 1.0}},
            transition_name="native_fe_metal_partition",
        ),
        snapshot_partition_source_account="process.metal_phase",
    )

    dependency = sso2_owner_recipe_evidence(execution)[
        "fe_drain_vapor_partition_dependency"
    ]

    assert dependency["status"] == "available"
    assert dependency["native_fe_saturation_split_count"] == 0
    assert dependency["native_fe_metal_partition_count"] == 1
    assert dependency["native_fe_partition_transition_count"] == 1
    assert dependency["native_fe_partition_basis"]["status"] == "available"


def test_sso2_partition_basis_rejects_mismatched_transition_source() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger({
            "terminal.drain_tap_material": {"Fe": 1.0},
        }),
        snapshot_partition_source_account="process.metal_phase",
    )

    dependency = sso2_owner_recipe_evidence(execution)[
        "fe_drain_vapor_partition_dependency"
    ]

    assert dependency["status"] == "missing_fe_drain_vapor_partition"
    assert dependency["native_fe_saturation_split_count"] == 1
    assert dependency["native_fe_metal_partition_count"] == 0
    assert (
        dependency["native_fe_partition_basis"]["status"]
        == "missing_fe_drain_vapor_partition"
    )
    assert "no committed native_fe_metal_partition transition" in (
        dependency["native_fe_partition_basis"]["status_reason"]
    )


def test_sso2_partition_basis_rejects_uncommitted_snapshot_diagnostic() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger({
            "terminal.drain_tap_material": {"Fe": 1.0},
        }),
        snapshot_partition_commit_status="refused",
    )

    dependency = sso2_owner_recipe_evidence(execution)[
        "fe_drain_vapor_partition_dependency"
    ]

    assert dependency["status"] == "missing_fe_drain_vapor_partition"
    assert dependency["native_fe_saturation_split_count"] == 1
    assert (
        dependency["native_fe_partition_basis"]["status"]
        == "missing_fe_drain_vapor_partition"
    )
    assert dependency["native_fe_partition_basis"][
        "native_fe_split_commit_status"
    ] == "refused"
    assert "lacks a committed split" in (
        dependency["native_fe_partition_basis"]["status_reason"]
    )


def test_sso2_evidence_negative_condensed_kg_fails_closed_without_raise() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): -1.0},
        ledger=_FakeSso2Ledger({"terminal.drain_tap_material": {"Fe": 1.0}}),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["status"] == "invalid_stage_purity_trace"
    assert evidence["stage_3"]["status"] == "invalid_stage_purity_trace"
    assert evidence["stage_3"]["Fe_kg"] is None


def test_sso2_evidence_negative_fe_tap_kg_fails_closed_without_raise() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 1.0},
        ledger=_FakeSso2Ledger({"terminal.drain_tap_material": {"Fe": -1.0}}),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["status"] == "missing_fe_tap_evidence"
    assert evidence["fe_tap"]["status"] == "missing_fe_tap_evidence"
    assert evidence["fe_tap"]["Fe_kg"] is None
    assert evidence["fe_tap"]["species_kg"] == {}


def test_sso2_evidence_negative_product_fe_kg_fails_closed_without_raise() -> None:
    # SC-49 sibling: the optional product-ledger field must not escape a bare
    # ValueError from the evidence surface on a corrupt (negative) product kg.
    snapshot = SimpleNamespace(hour=1, mass_balance_error_pct=1.2e-13)
    execution = SimpleNamespace(
        simulator=SimpleNamespace(
            atom_ledger=_FakeSso2Ledger({"terminal.drain_tap_material": {"Fe": 1.0}}),
            species_formula_registry={},
            product_ledger=lambda: {"Fe": -1.0},
        ),
        snapshots=(snapshot,),
        trace=SimpleNamespace(
            snapshots=(snapshot,),
            condensed_by_stage_species_delta=({(3, "SiO2"): 1.0},),
        ),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["metal_product_path"]["product_ledger_Fe_kg"] is None


def test_sso2_evidence_nonfinite_mass_balance_fails_closed_without_raise() -> None:
    # SC-49 sibling: a non-finite mass-balance value is invalid evidence, not a
    # pass, and must fail closed rather than raise out of the evidence surface.
    snapshot = SimpleNamespace(hour=1, mass_balance_error_pct=float("nan"))
    execution = SimpleNamespace(
        simulator=SimpleNamespace(
            atom_ledger=_FakeSso2Ledger({"terminal.drain_tap_material": {"Fe": 1.0}}),
            species_formula_registry={},
            product_ledger=lambda: {"Fe": 1.0},
        ),
        snapshots=(snapshot,),
        trace=SimpleNamespace(
            snapshots=(snapshot,),
            condensed_by_stage_species_delta=({(3, "SiO2"): 1.0},),
        ),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["mass_balance"]["status"] == "invalid_mass_balance_trace"
    assert evidence["mass_balance"]["max_abs_error_pct"] is None


def test_sso2_evidence_missing_stage_trace_fails_closed_without_zero_alias() -> None:
    snapshot = SimpleNamespace(hour=1, mass_balance_error_pct=0.0)
    execution = SimpleNamespace(
        simulator=SimpleNamespace(
            atom_ledger=_FakeSso2Ledger({"terminal.drain_tap_material": {"Fe": 1.0}}),
            species_formula_registry={},
            product_ledger=lambda: {"Fe": 1.0},
        ),
        snapshots=(snapshot,),
        trace=SimpleNamespace(snapshots=(snapshot,)),
    )

    evidence = sso2_owner_recipe_evidence(execution)

    assert evidence["status"] == "missing_stage_purity_trace"
    assert evidence["stage_3"]["Fe_kg"] is None
    assert evidence["stage_3"]["silica_species_kg"] == {
        "SiO": None,
        "SiO2": None,
        "Si": None,
    }
    assert evidence["delivered_stream_purity"]["detail"].startswith("fail-closed:")


def _sso2_objective_profile(*, stream_purity_min: float = 0.95) -> dict:
    return {
        "objectives": [
            {
                "metric": SSO2_OWNER_RECIPE_ID,
                "sense": "maximize",
                "units": "score_0_1",
                "weight": 1.0,
                "rationale": "SSO-2 Fe-free Stage 3 silica and Fe tap purity reader",
            }
        ],
        "constraints": {
            "gates": ["delivered_stream_purity"],
            "stream_purity_min": stream_purity_min,
        },
    }


def test_sso2_objective_reader_score_changes_with_stage3_fe_contamination() -> None:
    clean = _sso2_execution(
        condensed_delta={(3, "SiO2"): 10.0},
        ledger=_FakeSso2Ledger({
            "terminal.drain_tap_material": {"Fe": 7.0},
            "process.metal_phase": {"Fe": 3.0},
        }),
    )
    contaminated = _sso2_execution(
        condensed_delta={(3, "SiO2"): 10.0, (3, "Fe"): 0.1},
        ledger=_FakeSso2Ledger({
            "terminal.drain_tap_material": {"Fe": 7.0},
            "process.metal_phase": {"Fe": 3.0},
        }),
    )

    clean_objectives = compute_objectives(_sso2_objective_profile(), clean)
    dirty_objectives = compute_objectives(_sso2_objective_profile(), contaminated)

    clean_score = clean_objectives.as_mapping()[SSO2_OWNER_RECIPE_ID]
    dirty_score = dirty_objectives.as_mapping()[SSO2_OWNER_RECIPE_ID]
    assert clean_score == pytest.approx(1.0)
    assert 0.0 < dirty_score < clean_score
    reader = dirty_objectives.evidence[SSO2_OWNER_RECIPE_ID]
    assert reader["reader"] == SSO2_OWNER_RECIPE_ID
    assert reader["status"] == "stage_3_fe_contamination_penalized"
    assert reader["score_components"]["stage_3_fe_kg"] == pytest.approx(0.1)
    assert reader["score_components"]["fe_tap_Fe_kg"] == pytest.approx(7.0)
    assert "delivered_stream_purity.margin" in reader["consumed_fields"]


def test_sso2_objective_reader_fails_closed_on_missing_fe_tap_evidence() -> None:
    execution = _sso2_execution(
        condensed_delta={(3, "SiO2"): 10.0},
        ledger=_FakeSso2Ledger({"process.metal_phase": {"Fe": 3.0}}),
    )

    objectives = compute_objectives(_sso2_objective_profile(), execution)

    assert objectives.as_mapping()[SSO2_OWNER_RECIPE_ID] == pytest.approx(0.0)
    reader = objectives.evidence[SSO2_OWNER_RECIPE_ID]
    assert reader["status"] == "missing_fe_tap_evidence"
    assert reader["evidence"]["fe_tap"]["Fe_kg"] is None


def test_sso2_objective_reader_fails_closed_on_missing_stage_purity() -> None:
    snapshot = SimpleNamespace(hour=1, mass_balance_error_pct=0.0)
    execution = SimpleNamespace(
        simulator=SimpleNamespace(
            atom_ledger=_FakeSso2Ledger({"terminal.drain_tap_material": {"Fe": 1.0}}),
            species_formula_registry={},
            product_ledger=lambda: {"Fe": 1.0},
        ),
        snapshots=(snapshot,),
        trace=SimpleNamespace(snapshots=(snapshot,)),
    )

    score, reader = sso2_owner_recipe_objective_reader(execution)

    assert score == pytest.approx(0.0)
    assert reader["status"] == "missing_stage_purity_trace"
    assert reader["evidence"]["stage_3"]["Fe_kg"] is None


def test_product_summary_includes_input_output_yield_table_and_mass_closure() -> None:
    summary = product_summary(
        SimpleNamespace(simulator=_FakeProductSim(), trace=None),
        {"feedstock": "lunar_mare_low_ti"},
    )

    table = summary["product_yield_table"]
    outputs = {row["id"]: row for row in table["outputs"]}

    assert [row["id"] for row in table["inputs"]] == ["feedstock", "additive:CaO"]
    assert set(outputs) == {
        "ingots_metals",
        "glass",
        "oxygen",
        "captured_volatiles",
        "refractory_ceramic_rump",
    }
    assert outputs["ingots_metals"]["kg"] == pytest.approx(50.0)
    assert outputs["glass"]["yield_pct"] == pytest.approx(40.0 / 1001.5 * 100.0)
    assert summary["product_classes"]["pure_silica_glass"]["flag"]["authority"] == (
        "extrapolated"
    )
    assert outputs["oxygen"]["partition_kg"]["mre_anode_stored"] == pytest.approx(20.0)
    assert outputs["captured_volatiles"]["kg_by_species"] == {"H2O": 5.0}
    assert table["mass_closure"]["status"] == "closed"
    assert table["mass_closure"]["tolerance_pct"] == pytest.approx(5e-12)
    target_yield = summary["target_species_yield_report"]
    assert target_yield["gate_status"] == "skipped_pending_physics"
    assert "product_summary.target_species_yield_report" in target_yield["consumer"]
    assert target_yield["targets"]["K"]["status"] == "not-applicable"
    assert target_yield["targets"]["Fe"]["yield_fraction"] is not None


def test_product_summary_preserves_high_t_melt_activity_notice() -> None:
    notice = {
        "kind": "high_t_melt_activity_fallback",
        "authority": "extrapolated",
        "reason": "imcc_nonconvergence",
        "campaigns": ["C2A", "C4"],
    }
    sim = _FakeProductSim()
    sim.high_t_melt_activity_run_notice = lambda: notice

    summary = product_summary(
        SimpleNamespace(simulator=sim, trace=None),
        {"feedstock": "lunar_mare_low_ti"},
    )

    assert summary["high_t_melt_activity_fallback_notice"] == notice


def test_unclassified_product_mass_makes_yield_table_inconclusive() -> None:
    summary = product_summary(
        SimpleNamespace(simulator=_FakeUnclassifiedProductSim(), trace=None),
        {"feedstock": "lunar_mare_low_ti"},
    )

    table = summary["product_yield_table"]

    assert table["mass_closure"]["status"] == "closed"
    assert table["status"] == "inconclusive"
    assert table["unclassified_product_mass"]["total_kg"] == pytest.approx(7.0)
    assert table["unclassified_product_mass"]["kg_by_species"]["MysteryOxide"] == (
        pytest.approx(7.0)
    )
    diagnostics = {row["id"]: row for row in table["diagnostics"]}
    assert diagnostics["unclassified_product_mass"]["kind"] == "diagnostic"
    assert "unclassified_product_mass" not in {
        row["id"] for row in table["outputs"]
    }


class _CompositionLedger:
    registry = {}

    def __init__(
        self,
        cleaned_melt: dict[str, float],
        extra_accounts: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self._balances = {"process.cleaned_melt": cleaned_melt}
        self._balances.update(extra_accounts or {})

    def mol_by_account(self, account: str | None = None):
        if account is None:
            return {key: dict(value) for key, value in self._balances.items()}
        return dict(self._balances.get(account, {}))


class _CompositionSim:
    def __init__(
        self,
        cleaned_melt: dict[str, float],
        stage3_kg: dict[str, float],
        *,
        product_kg: dict[str, float] | None = None,
        extra_accounts: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.atom_ledger = _CompositionLedger(cleaned_melt, extra_accounts)
        self._product_kg = dict(product_kg or {})
        self.train = SimpleNamespace(
            stages=(
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg={}),
                SimpleNamespace(collected_kg=stage3_kg),
            )
        )
        self.record = SimpleNamespace(
            feedstock_key="lunar_mare_low_ti",
            batch_mass_kg=1000.0,
            products_kg={},
            oxygen_stored_kg=0.0,
            oxygen_vented_kg=0.0,
            energy_electrical_plus_evaporation_kWh=1.0,
            total_hours=1,
        )
        self.melt = SimpleNamespace(hour=1)
        self.energy_electrical_plus_evaporation_cumulative_kWh = 1.0

    def product_ledger(self) -> dict[str, float]:
        return dict(self._product_kg)

    def _oxygen_terminal_partition_kg(self) -> dict[str, float]:
        return {"stored": 0.0, "vented": 0.0, "total": 0.0}


def test_composition_target_uses_declared_pool_for_hard_window() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 10.0},
            stage3_kg={"SiO2": 100.0},
        ),
        trace=None,
    )

    captured = compute_objectives(
        _composition_score_profile("captured_stage_3_silica"),
        run,
    )
    residual = compute_objectives(
        _composition_score_profile("residual_rump_at_stop"),
        run,
    )

    assert captured.as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert residual.as_mapping()["composition_target:pool-test"] == pytest.approx(0.0)


def test_composition_target_hard_window_passes_or_fails() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 10.0, "CaO": 10.0},
            stage3_kg={},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 40.0, "max": 60.0, "weight": 1.0},
            "CaO": {"min": 40.0, "max": 60.0, "weight": 1.0},
        },
    )
    failing = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={"SiO2": {"min": 90.0, "max": 100.0, "weight": 1.0}},
    )

    assert compute_objectives(profile, run).as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert compute_objectives(failing, run).as_mapping()["composition_target:pool-test"] == pytest.approx(0.0)


def test_residual_rump_pool_includes_spent_reductant_residue_account() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0},
            stage3_kg={},
            extra_accounts={
                SPENT_REDUCTANT_RESIDUE_ACCOUNT: {"Na2O": 1.0},
            },
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={"Na2O": {"min": 40.0, "max": 100.0, "strict": True, "weight": 1.0}},
    )

    objectives = compute_objectives(profile, run)
    evidence = objectives.evidence["composition_target:pool-test"]["composition_target"]

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert evidence["resolved_composition"]["oxide_wt_pct"]["Na2O"] > 40.0


def test_composition_target_hard_window_miss_zeroes_extraction_branch() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 10.0, "CaO": 10.0},
            stage3_kg={},
            product_kg={"SiO2": 1000.0},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        species_vector={"Si": "extract"},
        extraction={
            "basis": "input_element_mol",
            "captured_pool": "captured_products",
            "completeness_min": {"Si": 0.01},
        },
        oxides={"SiO2": {"min": 90.0, "max": 100.0, "weight": 1.0}},
        score_weights={"extraction": 0.6, "composition": 0.4},
    )

    assert compute_objectives(profile, run).as_mapping()["composition_target:pool-test"] == pytest.approx(0.0)


def test_composition_target_soft_rows_rank_only_after_strict_pass() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 1.0, "Al2O3": 1.0},
            stage3_kg={},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0},
            "Al2O3": {"min": 15.0, "max": 20.0, "strict": False, "weight": 2.0},
        },
    )

    objectives = compute_objectives(profile, run)
    evidence = objectives.evidence["composition_target:pool-test"]["composition_target"]
    soft_row = next(row for row in evidence["rows"] if row["id"] == "Al2O3")

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(
        soft_row["score"]
    )
    assert evidence["certified_envelope"][0]["id"] == "SiO2"
    assert evidence["preference_score"] == pytest.approx(soft_row["score"])


def test_composition_target_hard_miss_skips_soft_rows() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 1.0, "Al2O3": 1.0},
            stage3_kg={},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 99.0, "max": 100.0, "strict": True, "weight": 1.0},
            "Al2O3": {"min": 15.0, "max": 20.0, "strict": False, "weight": 2.0},
        },
    )

    objectives = compute_objectives(profile, run)
    evidence = objectives.evidence["composition_target:pool-test"]["composition_target"]
    soft_row = next(row for row in evidence["rows"] if row["id"] == "Al2O3")

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(0.0)
    assert soft_row["score"] is None
    assert soft_row["reason"] == "hard_gate_failed_soft_not_computed"


def test_composition_target_ratio_row_scores_after_pool_projection() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0, "Al2O3": 1.0},
            stage3_kg={},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0}},
        ratios=[
            {
                "ratio": {
                    "numerator": "CaO",
                    "denominator": "Al2O3",
                    "min": 0.45,
                    "max": 0.75,
                    "strict": True,
                    "weight": 1.0,
                }
            }
        ],
    )

    objectives = compute_objectives(profile, run)
    evidence = objectives.evidence["composition_target:pool-test"]["composition_target"]

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert evidence["resolved_composition"]["ratios"]["CaO/Al2O3"] == pytest.approx(
        0.54999,
        rel=1e-5,
    )


def test_composition_target_ratio_zero_denominator_fails_loud() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0},
            stage3_kg={},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0}},
        ratios=[
            {
                "ratio": {
                    "numerator": "CaO",
                    "denominator": "Al2O3",
                    "min": 0.45,
                    "max": 0.75,
                    "strict": True,
                    "weight": 1.0,
                }
            }
        ],
    )

    with pytest.raises(ObjectiveComputationError, match="denominator.*missing"):
        compute_objectives(profile, run)


def test_terminal_rump_pool_refuses_completed_run_without_positive_evidence() -> None:
    # SC-109: backend_status=="ok" alone is not positive terminal proof.
    # Missing rump_terminal must refuse rather than score residual as completed.
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0},
            stage3_kg={},
        ),
        trace=None,
        backend_status="ok",
    )
    profile = _composition_score_profile(
        "terminal_rump_earned",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0}},
    )

    with pytest.raises(ObjectiveComputationError, match="positive completion evidence"):
        compute_objectives(profile, run)


def test_sc109_terminal_rump_pool_scores_with_earned_rump_terminal() -> None:
    """Positive control: earned rump_terminal still unlocks the pool."""
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0},
            stage3_kg={},
            extra_accounts={"terminal.slag": {"CaO": 2.0}},
        ),
        trace=SimpleNamespace(
            rump_terminal={"status": "earned", "reason": "test_fixture"},
            backend_status="ok",
        ),
        backend_status="ok",
    )
    profile = _composition_score_profile(
        "terminal_rump_earned",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0}},
    )

    objectives = compute_objectives(profile, run)
    evidence = objectives.evidence["composition_target:pool-test"]["composition_target"]
    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert evidence["terminal_rump_source"] == "earned_crash"


def test_terminal_rump_pool_rejects_trace_only_out_of_domain_as_completed_run() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0},
            stage3_kg={},
            extra_accounts={"terminal.slag": {"CaO": 10.0}},
        ),
        trace=SimpleNamespace(backend_status="out_of_domain"),
    )
    profile = _composition_score_profile(
        "terminal_rump_earned",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0}},
    )

    with pytest.raises(ObjectiveComputationError, match="cannot use completed_run"):
        compute_objectives(profile, run)


def test_terminal_rump_pool_unknown_completion_status_fails_closed() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"CaO": 1.0},
            stage3_kg={},
            extra_accounts={"terminal.slag": {"CaO": 10.0}},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "terminal_rump_earned",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "strict": True, "weight": 1.0}},
    )

    with pytest.raises(ObjectiveComputationError, match="positive completion evidence"):
        compute_objectives(profile, run)


@pytest.mark.parametrize(
    ("product_kg", "oxides"),
    [
        (
            {"SiO2": 1000.0},
            {
                "SiO2": {"min": 40.0, "max": 60.0, "weight": 1.0},
                "CaO": {"min": 40.0, "max": 60.0, "weight": 1.0},
            },
        ),
        (
            {},
            {
                "SiO2": {"min": 40.0, "max": 60.0, "weight": 1.0},
                "CaO": {"min": 40.0, "max": 60.0, "weight": 1.0},
            },
        ),
        (
            {"SiO2": 1000.0},
            {"SiO2": {"min": 90.0, "max": 100.0, "weight": 1.0}},
        ),
    ],
)
def test_composition_target_valid_unit_weights_keep_score_0_1(
    product_kg: dict[str, float],
    oxides: dict[str, dict[str, float]],
) -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 10.0, "CaO": 10.0},
            stage3_kg={},
            product_kg=product_kg,
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        species_vector={"Si": "extract", "Ca": "retain"},
        extraction={
            "basis": "input_element_mol",
            "captured_pool": "captured_products",
            "completeness_min": {"Si": 0.01},
        },
        oxides=oxides,
        score_weights={"extraction": 0.5, "composition": 0.5},
    )

    score = compute_objectives(profile, run).as_mapping()["composition_target:pool-test"]

    assert math.isfinite(score)
    assert 0.0 <= score <= 1.0


def test_composition_target_extraction_skips_unknown_product_bookkeeping_species() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 10.0},
            stage3_kg={},
            product_kg={"SiO2": 1000.0, "unspent_K_reagent": 1.0},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        species_vector={"Si": "extract"},
        extraction={
            "basis": "input_element_mol",
            "captured_pool": "captured_products",
            "completeness_min": {"Si": 0.01},
        },
        oxides={"SiO2": {"min": 0.0, "max": 100.0, "weight": 1.0}},
        score_weights={"extraction": 1.0, "composition": 0.0},
    )

    objectives = compute_objectives(profile, run)

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert objectives.evidence["composition_target:pool-test"][
        "captured_product_bookkeeping_exclusions"
    ] == ("unspent_K_reagent",)
    assert objectives.evidence["composition_target:pool-test"]["notes"] == (
        "excluded captured-products bookkeeping species from extraction credit: "
        "unspent_K_reagent",
    )


def test_composition_target_unknown_non_bookkeeping_product_species_raises() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={"SiO2": 10.0},
            stage3_kg={},
            product_kg={"SiO2": 1000.0, "MysteryProduct": 1.0},
        ),
        trace=None,
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        species_vector={"Si": "extract"},
        extraction={
            "basis": "input_element_mol",
            "captured_pool": "captured_products",
            "completeness_min": {"Si": 0.01},
        },
        oxides={"SiO2": {"min": 0.0, "max": 100.0, "weight": 1.0}},
        score_weights={"extraction": 1.0, "composition": 0.0},
    )

    with pytest.raises(ObjectiveComputationError, match="MysteryProduct"):
        compute_objectives(profile, run)


def test_captured_product_bookkeeping_pattern_constant_pinned() -> None:
    assert CAPTURED_PRODUCT_BOOKKEEPING_SPECIES_PATTERNS == ("unspent_*_reagent",)


def test_residual_rump_pool_missing_data_does_not_alias_terminal_trace() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(cleaned_melt={}, stage3_kg={}),
        trace=SimpleNamespace(
            rump_terminal={"status": "earned"},
            terminal_rump_by_species_kg={"SiO2": 100.0},
        ),
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={"SiO2": {"min": 0.0, "max": 100.0, "weight": 1.0}},
    )

    with pytest.raises(ObjectiveComputationError, match="residual_rump_at_stop"):
        compute_objectives(profile, run)


def test_terminal_rump_pool_requires_earned_status_in_compute_objectives() -> None:
    run = SimpleNamespace(
        simulator=_CompositionSim(
            cleaned_melt={},
            stage3_kg={},
            extra_accounts={"terminal.slag": {"CaO": 10.0}},
        ),
        trace=SimpleNamespace(
            rump_terminal={"status": "not_earned"},
            terminal_rump_by_species_kg={"CaO": 10.0},
        ),
    )
    profile = _composition_score_profile(
        "terminal_rump_earned",
        species_vector={"Ca": "retain"},
        oxides={"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
    )

    with pytest.raises(ObjectiveComputationError, match="terminal rump is not earned"):
        compute_objectives(profile, run)


def test_composition_target_eval_metadata_carries_tier_resolution_provenance() -> None:
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        species_vector={"Fe": "retain"},
        oxides={"Fe2O3": {"tier": "clear_container"}},
    )
    profile["objectives"][0]["target"]["thermal_window"] = "C2B window 1260-1480 C"

    metadata = composition_target_eval_metadata(profile)
    row = metadata["target_provenance"]["composition_window"]["oxides"]["Fe2O3"]

    assert metadata["target_provenance"]["thermal_window"] == "C2B window 1260-1480 C"
    assert row["tier"] == "clear_container"
    assert row["needs_experiment"] is True
    assert row["min"] == pytest.approx(0.0)
    assert row["max"] == pytest.approx(1.0)
    assert "design-composition-target-objective-2026-06-10" in row["provenance"]


def test_composition_targets_require_coating_defaults_true_for_arbitrary_target_id() -> None:
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        target_id="glass-clear-post-fe-v1",
    )

    assert composition_targets_require_coating(profile) is True


def test_composition_targets_require_coating_honors_explicit_opt_out() -> None:
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        target_id="glass-clear-post-fe-v1",
    )
    profile["objectives"][0]["target"]["require_coating_gate"] = False

    assert composition_targets_require_coating(profile) is False


def test_best_tap_digest_changes_when_enabled() -> None:
    base = _composition_score_profile("residual_rump_at_stop")
    enabled = _composition_score_profile(
        "residual_rump_at_stop",
        maturity={"best_tap": {"enabled": True}},
    )

    assert composition_target_eval_metadata(base)["target_spec_digest"] != (
        composition_target_eval_metadata(enabled)["target_spec_digest"]
    )


@pytest.mark.parametrize("carrier", ("N2", "Kr", "Ar", "He", "CO2"))
def test_operator_instruction_preserves_pn2_schema_for_stamped_carrier(
    carrier: str,
) -> None:
    composition = {"O2": 0.25, carrier: 10.0}
    if carrier != "N2":
        composition["N2"] = 1.0
    snapshot = SimpleNamespace(
        campaign=SimpleNamespace(name="C2A"),
        temperature_C=1200.0,
        overhead=SimpleNamespace(composition=composition),
    )

    instruction = objective_module._operator_instruction(
        snapshot,
        tap_hour=1,
        configured_hours=1,
        profile={"run": {"campaign": "C2A"}},
        stamped_carrier=carrier,
    )

    assert instruction["pN2_mbar"] == pytest.approx(10.0)


def test_operator_instruction_does_not_substitute_for_missing_stamped_carrier() -> None:
    snapshot = SimpleNamespace(
        campaign=SimpleNamespace(name="C2A"),
        temperature_C=1200.0,
        overhead=SimpleNamespace(composition={"O2": 0.25, "N2": 1.0}),
    )

    instruction = objective_module._operator_instruction(
        snapshot,
        tap_hour=1,
        configured_hours=1,
        profile={"run": {"campaign": "C2A"}},
        stamped_carrier="Kr",
    )

    assert "pN2_mbar" not in instruction


@pytest.mark.parametrize(
    ("composition", "expected_pressure"),
    [
        ({"O2": 0.25, "N2": 10.0}, 10.0),
        ({"O2": 0.25, "N2": 1.0, "Kr": 10.0}, None),
    ],
)
def test_operator_instruction_unstamped_carrier_requires_unambiguous_composition(
    composition: dict[str, float],
    expected_pressure: float | None,
) -> None:
    snapshot = SimpleNamespace(
        campaign=SimpleNamespace(name="C2A"),
        temperature_C=1200.0,
        overhead=SimpleNamespace(composition=composition),
    )

    instruction = objective_module._operator_instruction(
        snapshot,
        tap_hour=1,
        configured_hours=1,
        profile={"run": {"campaign": "C2A"}},
    )

    if expected_pressure is None:
        assert "pN2_mbar" not in instruction
    else:
        assert instruction["pN2_mbar"] == pytest.approx(expected_pressure)


def test_best_tap_selects_single_intermediate_hour_with_grade_report() -> None:
    snapshots = (
        _tap_snapshot(1, {"SiO2": 80.0, "CaO": 20.0}, stage_delta={(1, "Fe"): 1.0}),
        _tap_snapshot(
            2,
            {"SiO2": 50.0, "CaO": 50.0},
            stage_delta={(3, "SiO2"): 3.0, (4, "Na"): 1.0, (4, "Mg"): 1.0},
        ),
        _tap_snapshot(3, {"SiO2": 20.0, "CaO": 80.0}),
    )
    run = _tap_run(snapshots, configured_hours=3)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 3)

    objectives = compute_objectives(profile, run)
    evidence = objectives.evidence["composition_target:pool-test"]["composition_target"]
    grade = evidence["tap_grade_report"]

    assert objectives.as_mapping()["composition_target:pool-test"] == pytest.approx(1.0)
    assert evidence["tap_hour"] == 2
    assert evidence["configured_hours"] == 3
    assert evidence["tap_provenance"] == "tap_truncated"
    assert evidence["pool_snapshot_hour"] == 2
    assert evidence["operator_instruction"]["phase_at_tap"] == "C2A"
    assert evidence["operator_instruction"]["pN2_mbar"] == pytest.approx(10.0)
    assert evidence["operator_instruction"]["sweep_setting"] == "millibar_sweep"
    assert evidence["knife_edge"] is True
    assert evidence["certified"] is False
    assert [entry["hour"] for entry in evidence["tap_score_curve"]] == [1, 2, 3]
    assert sum(grade["melt_tap"]["oxide_wt_pct"].values()) == pytest.approx(100.0)
    assert "2" not in grade["distillation_train_taps"]
    stage3 = grade["distillation_train_taps"]["3"]
    assert stage3["dominant_species"] == "SiO2"
    assert stage3["dominant_species_purity_pct"] == pytest.approx(100.0)
    stage4 = grade["distillation_train_taps"]["4"]
    assert sum(stage4["species_wt_pct"].values()) == pytest.approx(100.0)

    truncated_profile = {
        **profile,
        "run": {**profile["run"], "hours": 2},
        "fidelities": {"internal-analytical": {**profile["fidelities"]["internal-analytical"], "hours": 2}},
    }
    truncated_run = _tap_run(snapshots[:2], configured_hours=2)
    reproduced = compute_objectives(truncated_profile, truncated_run)
    reproduced_evidence = reproduced.evidence["composition_target:pool-test"][
        "composition_target"
    ]
    assert reproduced.as_mapping()["composition_target:pool-test"] == pytest.approx(
        objectives.as_mapping()["composition_target:pool-test"]
    )
    assert reproduced_evidence["resolved_composition"] == evidence["resolved_composition"]
    assert [row["pass"] for row in reproduced_evidence["rows"]] == [
        row["pass"] for row in evidence["rows"]
    ]


def test_best_tap_dwell_can_prefer_later_certified_tie() -> None:
    snapshots = (
        _tap_snapshot(1, {"SiO2": 50.0, "CaO": 50.0}),
        _tap_snapshot(2, {"SiO2": 50.0, "CaO": 50.0}),
        _tap_snapshot(3, {"SiO2": 50.0, "CaO": 50.0}),
        _tap_snapshot(4, {"SiO2": 80.0, "CaO": 20.0}),
    )
    run = _tap_run(snapshots, configured_hours=4)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 4)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]

    assert evidence["tap_hour"] == 3
    assert evidence["certified"] is True
    assert evidence["knife_edge"] is False


def test_best_tap_nonterminal_captured_pool_fails_loud_without_note() -> None:
    snapshots = (
        _tap_snapshot(1, {}, melt_mass_kg=0.0, stage_delta={(3, "SiO2"): 10.0}),
        _tap_snapshot(2, {}, melt_mass_kg=0.0),
        _tap_snapshot(3, {}, melt_mass_kg=0.0),
    )
    run = _tap_run(snapshots, configured_hours=3)
    profile = _captured_extraction_profile(
        {"best_tap": {"enabled": True, "tap_stability_hours": 2}},
    )
    _set_profile_hours(profile, 3)

    with pytest.raises(ObjectiveComputationError, match="non-terminal captured-pool"):
        compute_objectives(profile, run)


def test_best_tap_nonterminal_captured_pool_can_emit_explicit_note() -> None:
    snapshots = (
        _tap_snapshot(1, {}, melt_mass_kg=0.0, stage_delta={(3, "SiO2"): 10.0}),
        _tap_snapshot(2, {}, melt_mass_kg=0.0),
        _tap_snapshot(3, {}, melt_mass_kg=0.0),
    )
    run = _tap_run(snapshots, configured_hours=3)
    profile = _captured_extraction_profile(
        {
            "best_tap": {
                "enabled": True,
                "tap_stability_hours": 2,
                "captured_pool_nonterminal_policy": "allow_with_note",
            }
        },
    )
    _set_profile_hours(profile, 3)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]

    assert evidence["tap_hour"] == 2
    assert evidence["tap_provenance"] == "tap_truncated"
    assert evidence["nonterminal_captured_pool_note"] == (
        "target pool-test selected captured-pool tap for pool captured_products "
        "at hour 2 of configured 3"
    )


def test_best_tap_coating_summary_uses_tap_hour_not_terminal_deposit() -> None:
    snapshots = (
        _tap_snapshot(
            1,
            {"SiO2": 80.0, "CaO": 20.0},
            wall_delta={("stage_1_to_stage_2", "SiO"): 0.001},
        ),
        _tap_snapshot(2, {"SiO2": 50.0, "CaO": 50.0}),
        _tap_snapshot(
            3,
            {"SiO2": 20.0, "CaO": 80.0},
            wall_delta={("stage_1_to_stage_2", "SiO"): 100.0},
        ),
    )
    run = _tap_run(snapshots, configured_hours=3)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 3)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]
    coating = evidence["tap_coating_product_summary"]

    assert evidence["tap_hour"] == 2
    assert coating["wall_deposit_kg_by_segment_species"]["stage_1_to_stage_2"]["SiO"] == pytest.approx(0.001)
    assert coating["wall_deposit_kg_by_zone_species"]["Hot"]["SiO"] == pytest.approx(0.001)
    assert "0.001" in coating["campaigns_to_resinter"]
    assert "100" not in coating["campaigns_to_resinter"]


def test_cumulative_wall_deposit_preserves_signed_deltas_zero_and_absence() -> None:
    positive = ("stage_1_to_stage_2", "Na")
    consumed_substrate = ("stage_1_to_stage_2", "SiO2")
    proven_zero = ("stage_1_to_stage_2", "K")
    absent = ("stage_1_to_stage_2", "Fe")
    snapshots = (
        SimpleNamespace(
            hour=1,
            wall_deposit_by_segment_species_delta={
                positive: 0.75,
                consumed_substrate: 1.0,
                proven_zero: 0.0,
            },
        ),
        SimpleNamespace(
            hour=2,
            wall_deposit_by_segment_species_delta={consumed_substrate: -0.75},
        ),
    )

    cumulative = objective_module._cumulative_wall_deposit_by_segment_species_kg(
        snapshots,
        hour=2,
    )

    assert cumulative[positive] == pytest.approx(0.75)
    assert cumulative[proven_zero] == 0.0
    assert cumulative[consumed_substrate] == pytest.approx(0.25)
    assert absent not in cumulative


def test_best_tap_coating_summary_normalizes_through_partial_campaign(
    monkeypatch,
) -> None:
    monkeypatch.setattr(objective_module, "_wall_resinter_threshold_kg", lambda: 10.0)
    snapshots = (
        _tap_snapshot(
            1,
            {"SiO2": 50.0, "CaO": 50.0},
            wall_delta={("stage_1_to_stage_2", "SiO"): 0.25},
        ),
        _tap_snapshot(2, {"SiO2": 70.0, "CaO": 30.0}),
        _tap_snapshot(3, {"SiO2": 80.0, "CaO": 20.0}),
        _tap_snapshot(
            4,
            {"SiO2": 20.0, "CaO": 80.0},
            wall_delta={("stage_1_to_stage_2", "SiO"): 100.0},
        ),
    )
    run = _tap_run(snapshots, configured_hours=4)
    run.campaigns_elapsed = 1.5
    run.simulator.campaign_mgr = SimpleNamespace(_max_hold_hr=lambda campaign: 4)
    run.session = SimpleNamespace(
        _step_results=(
            SimpleNamespace(campaign_summary=None),
            SimpleNamespace(campaign_summary={"campaign": "C0", "duration_h": 2}),
            SimpleNamespace(campaign_summary=None),
            SimpleNamespace(campaign_summary=None),
        )
    )
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True, "tap_grid": [1]}},
    )
    _set_profile_hours(profile, 4)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]
    coating = evidence["tap_coating_product_summary"]

    assert evidence["tap_hour"] == 1
    assert coating["wall_deposit_cumulative_total_kg"] == pytest.approx(0.25)
    assert coating["wall_deposit_total_kg"] == pytest.approx(1.0)
    assert coating["campaigns_to_resinter"] == pytest.approx(10.0)
    assert coating["aggregate_campaigns_to_resinter"] == pytest.approx(10.0)


def test_best_tap_clean_coating_summary_emits_complete_empty_fields() -> None:
    snapshots = (
        _tap_snapshot(1, {"SiO2": 50.0, "CaO": 50.0}),
        _tap_snapshot(
            2,
            {"SiO2": 20.0, "CaO": 80.0},
            wall_delta={("stage_1_to_stage_2", "SiO"): 100.0},
        ),
    )
    run = _tap_run(snapshots, configured_hours=2)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 2)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]
    coating = evidence["tap_coating_product_summary"]

    assert evidence["tap_hour"] == 1
    assert coating["campaigns_to_resinter"] == "infinite"
    assert coating["wall_deposit_kg_by_segment_species"] == {}
    assert coating["wall_deposit_kg_by_zone_species"] == {}


def test_best_tap_measured_zero_coating_projection_survives_grounding() -> None:
    snapshots = (
        _tap_snapshot(
            1,
            {"SiO2": 50.0, "CaO": 50.0},
            wall_delta={("stage_1_to_stage_2", "SiO"): 0.0},
        ),
        _tap_snapshot(2, {"SiO2": 20.0, "CaO": 80.0}),
    )
    run = _tap_run(snapshots, configured_hours=2)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 2)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]
    coating = evidence["tap_coating_product_summary"]

    def stripped_summary(summary: dict[str, object]) -> Mapping[str, object]:
        light = study._strip_heavy_result(
            ScoredResult(
                candidate_id=None,
                eval_spec=None,
                cache_key=None,
                feasible=False,
                run_reference=RunReference(status="ok", product_summary=summary),
            )
        )
        assert light.run_reference is not None
        return light.run_reference.product_summary

    grounded = stripped_summary(dict(coating))
    unknown = stripped_summary({"coating_authoritative": True})

    assert coating["wall_deposit_kg_by_segment_species"] == {
        "stage_1_to_stage_2": {"SiO": 0.0}
    }
    assert grounded["coating_authoritative"] is True
    assert unknown["coating_authoritative"] is False


def test_best_tap_coating_summary_carries_violation_present_at_tap_hour() -> None:
    snapshots = (
        _tap_snapshot(
            1,
            {"SiO2": 80.0, "CaO": 20.0},
            wall_delta={("stage_1_to_stage_2", "SiO"): 100.0},
        ),
        _tap_snapshot(2, {"SiO2": 50.0, "CaO": 50.0}),
        _tap_snapshot(3, {"SiO2": 20.0, "CaO": 80.0}),
    )
    run = _tap_run(snapshots, configured_hours=3)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 3)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]
    coating = evidence["tap_coating_product_summary"]

    assert evidence["tap_hour"] == 2
    assert coating["wall_deposit_kg_by_segment_species"]["stage_1_to_stage_2"]["SiO"] == pytest.approx(100.0)
    assert "150" in coating["campaigns_to_resinter"]


def test_best_tap_missing_requested_grid_hour_fails_loud() -> None:
    snapshots = (_tap_snapshot(1, {"SiO2": 50.0, "CaO": 50.0}),)
    run = _tap_run(snapshots, configured_hours=3)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True, "tap_grid": [1, 2]}},
    )
    _set_profile_hours(profile, 3)

    with pytest.raises(ObjectiveComputationError, match=r"missing hours: \[2\]"):
        compute_objectives(profile, run)


def test_best_tap_grade_basis_divergence_fails_loud() -> None:
    snapshots = (
        _tap_snapshot(
            1,
            {"SiO2": 50.0, "CaO": 50.0},
            inventory_melt_oxide_kg={"SiO2": 90.0, "CaO": 10.0},
        ),
    )
    run = _tap_run(snapshots, configured_hours=1)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 1)

    with pytest.raises(ObjectiveComputationError, match="grade basis diverges"):
        compute_objectives(profile, run)


def test_best_tap_grade_basis_tolerates_float_noise_edge() -> None:
    snapshots = (
        _tap_snapshot(
            1,
            {"SiO2": 50.0, "CaO": 50.0},
            inventory_melt_oxide_kg={"SiO2": 50.000002, "CaO": 49.999998},
        ),
    )
    run = _tap_run(snapshots, configured_hours=1)
    profile = _composition_score_profile(
        "residual_rump_at_stop",
        oxides={
            "SiO2": {"min": 45.0, "max": 55.0, "weight": 1.0},
            "CaO": {"min": 45.0, "max": 55.0, "weight": 1.0},
        },
        maturity={"best_tap": {"enabled": True}},
    )
    _set_profile_hours(profile, 1)

    evidence = compute_objectives(profile, run).evidence["composition_target:pool-test"][
        "composition_target"
    ]

    assert evidence["tap_hour"] == 1


def _tap_snapshot(
    hour: int,
    composition_wt_pct: dict[str, float],
    *,
    melt_mass_kg: float = 100.0,
    stage_delta: dict[tuple[int, str], float] | None = None,
    wall_delta: dict[tuple[str, str], float] | None = None,
    inventory_melt_oxide_kg: dict[str, float] | None = None,
):
    return SimpleNamespace(
        hour=hour,
        campaign=SimpleNamespace(name="C2A"),
        temperature_C=1200.0 + hour,
        melt_mass_kg=melt_mass_kg,
        composition_wt_pct=composition_wt_pct,
        inventory=SimpleNamespace(melt_oxide_kg=dict(inventory_melt_oxide_kg or {})),
        overhead=SimpleNamespace(composition={"O2": 0.25, "N2": 10.0}),
        condensed_by_stage_species_delta=dict(stage_delta or {}),
        wall_deposit_by_segment_species_delta=dict(wall_delta or {}),
        sweep_setting="millibar_sweep",
    )


def _tap_run(snapshots, *, configured_hours: int):
    sim = _CompositionSim(cleaned_melt={"SiO2": 1.0}, stage3_kg={})
    sim.record.total_hours = configured_hours
    return SimpleNamespace(
        simulator=sim,
        snapshots=tuple(snapshots),
        trace=SimpleNamespace(
            snapshots=tuple(snapshots),
            wall_zone_by_segment={"stage_1_to_stage_2": "Hot"},
        ),
        per_hour=tuple(
            {
                "hour": snapshot.hour,
                "campaign": snapshot.campaign.name,
                "T_C": snapshot.temperature_C,
                "reduced_real_cache_state": "cached_exact",
            }
            for snapshot in snapshots
        ),
        backend_status="ok",
    )


def _captured_extraction_profile(maturity: dict[str, object]) -> dict:
    profile = _composition_score_profile(
        "captured_products",
        species_vector={"Si": "extract"},
        extraction={
            "basis": "input_element_mol",
            "captured_pool": "captured_products",
            "completeness_min": {"Si": 0.0001},
        },
        oxides=None,
        score_weights={"extraction": 1.0, "composition": 0.0},
        maturity=maturity,
    )
    del profile["objectives"][0]["target"]["composition_window"]
    return profile


def _set_profile_hours(profile: dict, hours: int) -> None:
    profile["run"]["hours"] = hours
    profile["fidelities"]["internal-analytical"]["hours"] = hours


def _composition_score_profile(
    pool: str,
    *,
    target_id: str = "pool-test",
    oxides: dict[str, dict[str, float]] | None = None,
    ratios: list[dict] | None = None,
    species_vector: dict[str, str] | None = None,
    extraction: dict[str, object] | None = None,
    score_weights: dict[str, float] | None = None,
    maturity: dict[str, object] | None = None,
) -> dict:
    window = {
        "pool": pool,
        "basis": "oxide_wt_pct",
        "mode": "hard_window",
        "oxides": oxides
        or {"SiO2": {"min": 99.0, "max": 100.0, "weight": 1.0}},
    }
    if ratios is not None:
        window["ratios"] = ratios
    target = {
        "pool": pool,
        "species_vector": species_vector or {"Si": "retain"},
        "composition_window": window,
        "score_weights": score_weights or {"extraction": 0.0, "composition": 1.0},
    }
    if extraction is not None:
        target["extraction"] = extraction
    if maturity is not None:
        target["maturity"] = maturity
    return {
        "profile_id": "composition-target-score-test",
        "profile_schema_version": "profile-schema-v1",
        "feedstock": "lunar_mare_low_ti",
        "objectives": [
            {
                "type": "composition_target",
                "id": target_id,
                "metric": f"composition_target:{target_id}",
                "sense": "maximize",
                "units": "score_0_1",
                "weight": 1.0,
                "rationale": "test composition target score",
                "target": target,
            }
        ],
        "constraints": {"gates": ["delivered_stream_purity"]},
        "run": {"campaign": "C0", "hours": 1, "mass_kg": 1000.0, "backend_name": "internal-analytical"},
        "fidelities": {"internal-analytical": {"backend_name": "internal-analytical", "hours": 1}},
        "seed_recipes": [{"id": "seed", "source_campaign": "C0", "patch": {}}],
    }
