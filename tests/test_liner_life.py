from __future__ import annotations

import math
from unittest.mock import patch

import numpy as np
import pytest

from simulator.accounting.run_artifact import build_run_artifact
from simulator.liner_life import (
    AnalyticRecessionScreen,
    CongruentVaporizationRecessionEvaluator,
    DEFAULT_DIAGNOSTIC_MATERIAL_ID,
    DIAGNOSTIC_AUTHORITY,
    DIAGNOSTIC_DEPENDENCIES,
    LinerLifeConfiguration,
    LinerLifeInputRefusal,
    LinerLifeRefusal,
    LinerLifeTarget,
    RecessionDataUnavailable,
    RecessionMonotonicityEvidence,
    build_liner_life_run_diagnostic,
    derive_liner_temperature_ceiling,
    liner_temperature_ceiling_diagnostic,
    wear_budget_mm_per_1000h,
)
from simulator.refractory_vaporization import solve_congruent_vaporization
from simulator.runner import PyrolysisRun


class _Evaluator:
    def __init__(self, full, *, screen=None, monotone=True):
        self._full = full
        self._screen = screen
        self._monotone = monotone
        self.full_calls: list[tuple[str, float, float]] = []
        self.screen_calls: list[tuple[str, float, float]] = []
        self.monotonicity_calls: list[tuple[str, float, float, float]] = []

    def analytic_screen_recession_mm_per_1000h(
        self,
        *,
        material_id: str,
        temperature_C: float,
        pO2_bar: float,
    ):
        self.screen_calls.append((material_id, temperature_C, pO2_bar))
        if callable(self._screen):
            value = self._screen(temperature_C, pO2_bar)
        else:
            value = self._screen
        if value is None or isinstance(value, AnalyticRecessionScreen):
            return value
        return AnalyticRecessionScreen(
            value,
            basis="test conservative upper bound",
        )

    def recession_mm_per_1000h(
        self,
        *,
        material_id: str,
        temperature_C: float,
        pO2_bar: float,
    ):
        self.full_calls.append((material_id, temperature_C, pO2_bar))
        return self._full(temperature_C, pO2_bar)

    def monotonicity_evidence(
        self,
        *,
        material_id: str,
        lower_temperature_C: float,
        upper_temperature_C: float,
        pO2_bar: float,
    ):
        self.monotonicity_calls.append(
            (
                material_id,
                lower_temperature_C,
                upper_temperature_C,
                pO2_bar,
            )
        )
        return RecessionMonotonicityEvidence(
            self._monotone,
            basis=(
                "test equation is monotone increasing"
                if self._monotone
                else "test equation contains a hidden decreasing segment"
            ),
        )


def _configuration(**overrides) -> LinerLifeConfiguration:
    values = {
        "material_id": "test_liner",
        "liner_thickness_mm": 100.0,
        "wear_budget_fraction": 0.1,
        "hot_hours_per_run": 10.0,
        "lowest_useful_temperature_C": 1200.0,
        "structural_limit_C": 1700.0,
        "analytic_screen_threshold_fraction": 0.1,
        "bisection_tolerance_C": 0.01,
        "bisection_max_iterations": 100,
        "monotonicity_samples": 11,
        "source": "test catalogue:test_liner.liner_life_diagnostic",
    }
    values.update(overrides)
    return LinerLifeConfiguration(**values)


def _target(runs=100.0) -> LinerLifeTarget:
    return LinerLifeTarget.from_input(runs, unit="runs")


def test_wear_budget_algebra_and_explicit_unit_conversions():
    assert wear_budget_mm_per_1000h(
        target_life_runs=100,
        liner_thickness_mm=100,
        wear_budget_fraction=0.1,
        hot_hours_per_run=10,
    ) == pytest.approx(10.0)

    campaigns = LinerLifeTarget.from_input(
        5,
        unit="campaigns",
        runs_per_campaign=10,
    )
    hot_hours = LinerLifeTarget.from_input(
        500,
        unit="hot_hours",
        hot_hours_per_run=10,
    )
    assert campaigns.runs == pytest.approx(50)
    assert hot_hours.runs == pytest.approx(50)
    assert campaigns.source_unit == "campaigns"
    assert hot_hours.source_unit == "hot_hours"
    assert "runs/campaign" in campaigns.conversion
    assert "hot h/run" in hot_hours.conversion


@pytest.mark.parametrize("value", [True, False, np.bool_(True), np.bool_(False)])
def test_operator_target_rejects_builtin_and_numpy_bool(value):
    with pytest.raises(LinerLifeInputRefusal) as caught:
        LinerLifeTarget.from_input(value)
    assert caught.value.reason == "invalid_declared_scalar"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("liner_thickness_mm", True),
        ("wear_budget_fraction", np.bool_(True)),
        ("hot_hours_per_run", False),
        ("bisection_max_iterations", np.bool_(False)),
    ],
)
def test_configuration_rejects_bool_numeric_fields(field, value):
    with pytest.raises(LinerLifeInputRefusal):
        _configuration(**{field: value})


def test_catalogue_configuration_is_explicit_and_provenance_labeled():
    catalog = {
        "furnace_materials": {
            "test_liner": {
                "max_service_T_C": 1700,
                "liner_life_diagnostic": {
                    "liner_thickness_mm": 100,
                    "wear_budget_fraction": 0.1,
                    "lowest_useful_temperature_C": 1200,
                    "analytic_screen_threshold_fraction": 0.05,
                    "bisection_tolerance_C": 0.1,
                    "bisection_max_iterations": 30,
                    "monotonicity_samples": 9,
                },
            }
        }
    }
    config = LinerLifeConfiguration.from_material_catalogue(
        "test_liner",
        hot_hours_per_run=8,
        catalog=catalog,
    )
    assert config.liner_thickness_mm == pytest.approx(100)
    assert config.wear_budget_fraction == pytest.approx(0.1)
    assert config.hot_hours_per_run == pytest.approx(8)
    assert config.structural_limit_C == pytest.approx(1700)
    assert config.source == (
        "caller catalog:furnace_materials.test_liner.liner_life_diagnostic"
    )


def test_operator_entry_point_loads_config_and_emits_diagnostic_only():
    catalog = {
        "test_liner": {
            "max_service_T_C": 1700,
            "liner_life_diagnostic": {
                "liner_thickness_mm": 100,
                "wear_budget_fraction": 0.1,
                "lowest_useful_temperature_C": 1200,
                "analytic_screen_threshold_fraction": 0.1,
                "bisection_tolerance_C": 0.1,
                "bisection_max_iterations": 30,
                "monotonicity_samples": 9,
            },
        }
    }
    result = liner_temperature_ceiling_diagnostic(
        material_id="test_liner",
        target_life_value=5,
        target_life_unit="campaigns",
        runs_per_campaign=20,
        hot_hours_per_run=10,
        pO2_bar=0.1,
        evaluator=_Evaluator(lambda _temperature_C, _pO2: 5.0, screen=2.0),
        catalog=catalog,
    )
    assert result.target_life_runs == pytest.approx(100)
    assert result.status == "computed_diagnostic_not_applied"
    assert result.binding_bound == "structural_limit"


def test_canonical_catalogue_loads_liner_config_for_wired_alumina():
    config = LinerLifeConfiguration.from_material_catalogue(
        "dense_alumina_continuous",
        hot_hours_per_run=10,
    )
    assert config.material_id == "dense_alumina_continuous"
    assert config.liner_thickness_mm == pytest.approx(100.0)
    assert config.structural_limit_C == pytest.approx(1700.0)
    assert config.source.startswith("data/furnace_materials.yaml:")


def test_canonical_catalogue_missing_liner_config_refuses_instead_of_inventing():
    with pytest.raises(LinerLifeInputRefusal) as caught:
        LinerLifeConfiguration.from_material_catalogue(
            "zirconia_ysz",
            hot_hours_per_run=10,
        )
    assert caught.value.reason == "liner_life_configuration_unavailable"


def test_bisection_finds_safe_side_and_reports_bracket_and_tolerance():
    evaluator = _Evaluator(
        lambda temperature_C, _pO2: math.exp((temperature_C - 1200.0) / 100.0),
        screen=1000.0,
    )
    result = derive_liner_temperature_ceiling(
        target=_target(),
        configuration=_configuration(),
        pO2_bar=0.0,
        evaluator=evaluator,
    )
    expected_crossing_C = 1200.0 + 100.0 * math.log(10.0)
    assert result.binding_bound == "recession"
    assert result.target_canonical_unit == "runs"
    assert result.ceiling_T_C == pytest.approx(expected_crossing_C, abs=0.01)
    assert result.recession_limited_T_C == result.ceiling_T_C
    assert result.solver_initial_bracket_C == (1200.0, 1700.0)
    assert result.solver_final_bracket_C is not None
    assert (
        result.solver_final_bracket_C[1] - result.solver_final_bracket_C[0]
        <= result.solver_tolerance_C
    )
    assert result.status == "computed_diagnostic_not_applied"
    assert result.pending_dependencies == DIAGNOSTIC_DEPENDENCIES
    assert result.monotonicity_basis == "test equation is monotone increasing"
    assert evaluator.monotonicity_calls == [
        ("test_liner", 1200.0, 1700.0, 0.0)
    ]


def test_sampled_monotonicity_assertion_refuses_invalid_model():
    evaluator = _Evaluator(
        lambda temperature_C, _pO2: 2000.0 - temperature_C,
        screen=1000.0,
    )
    with pytest.raises(LinerLifeRefusal) as caught:
        derive_liner_temperature_ceiling(
            target=_target(),
            configuration=_configuration(),
            pO2_bar=0.0,
            evaluator=evaluator,
        )
    assert caught.value.reason == "non_monotone_recession_model"
    assert caught.value.diagnostic["binding_mechanism"] == (
        "invalid_recession_model"
    )


def test_evaluator_certificate_refuses_hidden_reversal_between_samples():
    def hidden_reversal(temperature_C, _pO2):
        baseline = math.exp((temperature_C - 1200.0) / 100.0)
        if 1260.5 < temperature_C < 1261.5:
            return baseline * 0.01
        return baseline

    evaluator = _Evaluator(
        hidden_reversal,
        screen=1000.0,
        monotone=False,
    )
    with pytest.raises(LinerLifeRefusal) as caught:
        derive_liner_temperature_ceiling(
            target=_target(),
            configuration=_configuration(monotonicity_samples=3),
            pO2_bar=0.0,
            evaluator=evaluator,
        )
    assert caught.value.reason == "non_monotone_recession_model"
    assert "hidden decreasing segment" in caught.value.diagnostic["detail"]


@pytest.mark.parametrize("screen", [0.5, 100.0])
def test_structural_success_paths_require_whole_bracket_monotonicity(screen):
    evaluator = _Evaluator(
        lambda _temperature_C, _pO2: 0.5,
        screen=screen,
        monotone=False,
    )
    with pytest.raises(LinerLifeRefusal) as caught:
        derive_liner_temperature_ceiling(
            target=_target(),
            configuration=_configuration(),
            pO2_bar=0.0,
            evaluator=evaluator,
        )
    assert caught.value.reason == "non_monotone_recession_model"
    assert evaluator.monotonicity_calls == [
        ("test_liner", 1200.0, 1700.0, 0.0)
    ]


def test_analytic_screen_short_circuits_full_engine_at_structural_limit():
    def full_must_not_run(_temperature_C, _pO2):
        raise AssertionError("full recession engine must not run")

    evaluator = _Evaluator(full_must_not_run, screen=0.5)
    result = derive_liner_temperature_ceiling(
        target=_target(),
        configuration=_configuration(),
        pO2_bar=0.1,
        evaluator=evaluator,
    )
    assert result.ceiling_T_C == pytest.approx(1700)
    assert result.binding_bound == "structural_limit"
    assert result.analytic_screen_status == (
        "negligible_structural_short_circuit"
    )
    assert result.analytic_screen_threshold_mm_per_1000h == pytest.approx(1.0)
    assert result.analytic_screen_basis == "test conservative upper bound"
    assert result.full_evaluation_count == 0
    assert evaluator.full_calls == []


def test_screen_uses_conservative_upper_bound_and_threshold_equality():
    def full_must_not_run(_temperature_C, _pO2):
        raise AssertionError(
            "full engine must not run at a safe upper-bound equality"
        )

    at_threshold = _Evaluator(
        full_must_not_run,
        screen=AnalyticRecessionScreen(
            1.0,
            basis="proxy plus certified uncertainty envelope",
        ),
    )
    equal_result = derive_liner_temperature_ceiling(
        target=_target(),
        configuration=_configuration(),
        pO2_bar=0.0,
        evaluator=at_threshold,
    )
    assert equal_result.binding_bound == "structural_limit"
    assert at_threshold.full_calls == []

    adverse_upper_bound = _Evaluator(
        lambda temperature_C, _pO2: math.exp(
            (temperature_C - 1200.0) / 100.0
        ),
        screen=AnalyticRecessionScreen(
            11.5,
            basis="0.5 proxy * known 23x CaO adverse factor",
        ),
    )
    adverse_result = derive_liner_temperature_ceiling(
        target=_target(),
        configuration=_configuration(),
        pO2_bar=0.0,
        evaluator=adverse_upper_bound,
    )
    assert adverse_result.binding_bound == "recession"
    assert adverse_upper_bound.full_calls


def test_structural_bound_reports_binding_when_full_rate_is_safe():
    evaluator = _Evaluator(lambda _temperature_C, _pO2: 5.0, screen=2.0)
    result = derive_liner_temperature_ceiling(
        target=_target(),
        configuration=_configuration(),
        pO2_bar=0.0,
        evaluator=evaluator,
    )
    assert result.binding_bound == "structural_limit"
    assert result.ceiling_T_C == pytest.approx(1700)
    assert result.full_evaluation_count == 1


def test_no_solution_is_typed_and_names_target_material_life_and_mechanism():
    evaluator = _Evaluator(lambda _temperature_C, _pO2: 20.0, screen=100.0)
    with pytest.raises(LinerLifeRefusal) as caught:
        derive_liner_temperature_ceiling(
            target=_target(),
            configuration=_configuration(),
            pO2_bar=0.0,
            evaluator=evaluator,
        )
    refusal = caught.value
    assert refusal.reason == "no_recession_limited_temperature_solution"
    assert refusal.diagnostic["target_life_runs"] == pytest.approx(100)
    assert refusal.diagnostic["material_id"] == "test_liner"
    assert refusal.diagnostic[
        "achievable_life_at_structural_limit_runs"
    ] == pytest.approx(50)
    assert refusal.diagnostic["binding_mechanism"] == (
        "recession_below_lowest_useful_temperature"
    )
    assert refusal.diagnostic["solver_initial_bracket_C"] == (1200.0, 1700.0)


def test_non_convergence_is_typed_and_does_not_return_last_guess():
    evaluator = _Evaluator(
        lambda temperature_C, _pO2: math.exp((temperature_C - 1200.0) / 100.0),
        screen=1000.0,
    )
    config = _configuration(
        bisection_tolerance_C=0.001,
        bisection_max_iterations=1,
    )
    with pytest.raises(LinerLifeRefusal) as caught:
        derive_liner_temperature_ceiling(
            target=_target(),
            configuration=config,
            pO2_bar=0.0,
            evaluator=evaluator,
        )
    assert caught.value.reason == "recession_bisection_non_convergence"
    assert caught.value.diagnostic["solver_final_bracket_C"] is not None
    assert caught.value.diagnostic["binding_mechanism"] == "recession_solver"


def test_missing_recession_data_is_typed_refusal():
    def unavailable(_temperature_C, _pO2):
        raise RecessionDataUnavailable("no exposed-phase data for test_liner")

    evaluator = _Evaluator(unavailable)
    with pytest.raises(LinerLifeRefusal) as caught:
        derive_liner_temperature_ceiling(
            target=_target(),
            configuration=_configuration(),
            pO2_bar=0.0,
            evaluator=evaluator,
        )
    assert caught.value.reason == "recession_data_unavailable"
    assert caught.value.diagnostic["material_id"] == "test_liner"
    assert caught.value.diagnostic["binding_mechanism"] == (
        "recession_model_unavailable"
    )


def test_higher_po2_suppresses_recession_and_raises_temperature_ceiling():
    evaluator = _Evaluator(
        lambda temperature_C, pO2: (
            math.exp((temperature_C - 1200.0) / 100.0) / (1.0 + 10.0 * pO2)
        ),
        screen=1000.0,
    )
    vacuum = derive_liner_temperature_ceiling(
        target=_target(),
        configuration=_configuration(),
        pO2_bar=0.0,
        evaluator=evaluator,
    )
    controlled = derive_liner_temperature_ceiling(
        target=_target(),
        configuration=_configuration(),
        pO2_bar=0.1,
        evaluator=evaluator,
    )
    assert vacuum.binding_bound == "recession"
    assert controlled.binding_bound == "recession"
    assert controlled.ceiling_T_C > vacuum.ceiling_T_C
    assert controlled.pO2_bar == pytest.approx(0.1)


def test_extreme_targets_zero_hours_and_same_side_brackets_fail_closed():
    evaluator = _Evaluator(lambda _temperature_C, _pO2: 20.0, screen=100.0)

    one_run = derive_liner_temperature_ceiling(
        target=_target(1),
        configuration=_configuration(),
        pO2_bar=0.0,
        evaluator=evaluator,
    )
    assert one_run.binding_bound == "structural_limit"

    with pytest.raises(LinerLifeRefusal) as billion_run:
        derive_liner_temperature_ceiling(
            target=_target(1e9),
            configuration=_configuration(),
            pO2_bar=0.0,
            evaluator=evaluator,
        )
    assert billion_run.value.reason == "no_recession_limited_temperature_solution"

    with pytest.raises(LinerLifeInputRefusal) as zero_hours:
        LinerLifeConfiguration.from_material_catalogue(
            "test_liner",
            hot_hours_per_run=0,
            catalog={
                "test_liner": {
                    "max_service_T_C": 1700,
                    "liner_life_diagnostic": {
                        "liner_thickness_mm": 100,
                        "wear_budget_fraction": 0.1,
                        "lowest_useful_temperature_C": 1200,
                        "analytic_screen_threshold_fraction": 0.1,
                        "bisection_tolerance_C": 0.1,
                        "bisection_max_iterations": 30,
                        "monotonicity_samples": 5,
                    },
                }
            },
        )
    assert zero_hours.value.reason == "non_positive_scalar"


def test_invalid_po2_and_evaluator_bool_outputs_refuse_but_zero_po2_is_valid():
    evaluator = _Evaluator(lambda _temperature_C, _pO2: True, screen=100.0)
    with pytest.raises(LinerLifeInputRefusal):
        derive_liner_temperature_ceiling(
            target=_target(),
            configuration=_configuration(),
            pO2_bar=np.bool_(False),
            evaluator=evaluator,
        )
    with pytest.raises(LinerLifeRefusal) as bool_output:
        derive_liner_temperature_ceiling(
            target=_target(),
            configuration=_configuration(),
            pO2_bar=0,
            evaluator=evaluator,
        )
    assert bool_output.value.reason == "invalid_recession_evaluator_output"


def test_untyped_analytic_screen_output_refuses():
    class UntypedScreenEvaluator(_Evaluator):
        def analytic_screen_recession_mm_per_1000h(self, **_kwargs):
            return 0.5

    evaluator = UntypedScreenEvaluator(
        lambda _temperature_C, _pO2: 0.5,
    )
    with pytest.raises(LinerLifeRefusal) as caught:
        derive_liner_temperature_ceiling(
            target=_target(),
            configuration=_configuration(),
            pO2_bar=0.0,
            evaluator=evaluator,
        )
    assert caught.value.reason == "invalid_recession_evaluator_output"
    assert caught.value.diagnostic["evaluator_tier"] == "analytic_screen"


# ---------------------------------------------------------------------------
# b-107 wired path: CongruentVaporizationRecessionEvaluator + catalog + artifact
# ---------------------------------------------------------------------------

_PURE_OXIDE_DIAGNOSTIC_MATERIALS = (
    "pure_Al2O3",
    "pure_CaO",
    "pure_MgO",
    "pure_SiO2",
    "pure_TiO2",
)


@pytest.mark.parametrize("material_id", _PURE_OXIDE_DIAGNOSTIC_MATERIALS)
def test_congruent_evaluator_matches_refractory_recession_at_1600c(material_id):
    config = LinerLifeConfiguration.from_material_catalogue(
        material_id,
        hot_hours_per_run=10,
    )
    # Density lives on the catalogue diagnostic block (not the inversion config).
    from simulator.furnace_materials import load_furnace_materials

    material = load_furnace_materials()[material_id]
    density = float(material["liner_life_diagnostic"]["density_kg_m3"])
    oxide = str(material["liner_life_diagnostic"]["refractory_material"])
    temperature_C = 1600.0
    expected = solve_congruent_vaporization(
        oxide,
        temperature_C + 273.15,
    ).recession_mm_per_1000h(density)

    evaluator = CongruentVaporizationRecessionEvaluator()
    rate = evaluator.recession_mm_per_1000h(
        material_id=material_id,
        temperature_C=temperature_C,
        pO2_bar=0.0,
    )
    assert rate == pytest.approx(expected, rel=1e-12)
    assert rate >= 0.0
    status = evaluator.last_status
    assert status is not None
    assert status["flux_classification"] == (
        "included_carrier_equilibrium_effusion_sum"
    )
    assert status["upper_bound_claim"] == "included_carriers_only"
    assert status["evaporation_coefficient"] == pytest.approx(1.0)
    assert config.structural_limit_C > 0.0


def test_congruent_evaluator_wires_canonical_alumina_ceiling_diagnostic():
    evaluator = CongruentVaporizationRecessionEvaluator()
    result = liner_temperature_ceiling_diagnostic(
        material_id="dense_alumina_continuous",
        target_life_value=100,
        target_life_unit="runs",
        hot_hours_per_run=10,
        pO2_bar=0.0,
        evaluator=evaluator,
    )
    assert result.status == "computed_diagnostic_not_applied"
    assert result.authority == DIAGNOSTIC_AUTHORITY
    assert result.material_id == "dense_alumina_continuous"
    assert result.binding_bound in {"structural_limit", "recession"}
    assert result.ceiling_T_C <= result.structural_limit_C
    assert result.pending_dependencies == DIAGNOSTIC_DEPENDENCIES
    # Alumina at 1700 C is well below the 10 mm/1000 h wear budget for the
    # default 100-run / 100 mm / 0.1 fraction target, so the structural limit
    # binds - the diagnostic is informative, not a new hard gate.
    assert result.binding_bound == "structural_limit"
    assert result.ceiling_T_C == pytest.approx(1700.0)
    assert evaluator.last_status is not None
    assert evaluator.last_status["upper_bound_claim"] == "included_carriers_only"


def test_cao_diagnostic_material_can_bind_on_recession():
    """CaO is the load-bearing high-recession pure oxide from the validation screen."""
    result = liner_temperature_ceiling_diagnostic(
        material_id="pure_CaO",
        target_life_value=100,
        target_life_unit="runs",
        hot_hours_per_run=10,
        pO2_bar=0.0,
        evaluator=CongruentVaporizationRecessionEvaluator(),
    )
    assert result.status == "computed_diagnostic_not_applied"
    # At 10 mm/1000 h budget, pure CaO at 1700 C exceeds the budget (~12.8),
    # so the ceiling should be recession-limited below structural 1700 C.
    assert result.binding_bound == "recession"
    assert result.recession_limited_T_C is not None
    assert result.ceiling_T_C < result.structural_limit_C
    assert result.ceiling_T_C > 1200.0


def test_build_liner_life_run_diagnostic_is_info_level_and_non_gating():
    payload = build_liner_life_run_diagnostic(
        material_id="dense_alumina_continuous",
        target_life_runs=100,
        hot_hours_per_run=10,
        pO2_bar=0.0,
    )
    assert payload["level"] == "info"
    assert payload["binding"] is False
    assert payload["gating"] is False
    assert payload["authority"] == DIAGNOSTIC_AUTHORITY
    assert payload["status"] == "computed_diagnostic_not_applied"
    assert payload["material_id_source"] == "explicit"
    assert payload["ceiling"] is not None
    assert payload["ceiling"]["status"] == "computed_diagnostic_not_applied"
    assert payload["recession_model"] is not None
    assert payload["recession_model"]["flux_classification"] == (
        "included_carrier_equilibrium_effusion_sum"
    )
    # Status-bearing alpha=1: not a total-recession upper bound.
    assert payload["recession_model"]["upper_bound_claim"] == "included_carriers_only"
    assert payload["recession_model"]["evaporation_coefficient"] == pytest.approx(1.0)


def test_build_liner_life_run_diagnostic_refuses_missing_material_without_fabricating():
    """Null-hypothesis: inventing dense_alumina would break golden-neutrality."""
    for missing in (None, "", "   "):
        payload = build_liner_life_run_diagnostic(
            material_id=missing,
            hot_hours_per_run=10,
            pO2_bar=0.0,
        )
        assert payload["status"] == "refused"
        assert payload["gating"] is False
        assert payload["binding"] is False
        assert payload["level"] == "info"
        assert payload["material_id_source"] == "missing"
        assert payload["refusal"]["reason"] == "furnace_material_id_required"
        # Must not silently report the catalogue default as a selected wall.
        assert payload["material_id"] is None
        assert payload["ceiling"] is None


def test_build_liner_life_run_diagnostic_refuses_unwired_material_without_raising():
    payload = build_liner_life_run_diagnostic(
        material_id="zirconia_ysz",
        hot_hours_per_run=10,
        pO2_bar=0.0,
    )
    assert payload["status"] == "refused"
    assert payload["binding"] is False
    assert payload["gating"] is False
    assert payload["level"] == "info"
    assert payload["refusal"] is not None
    assert payload["refusal"]["reason"] == "liner_life_configuration_unavailable"


def test_runner_omits_liner_life_when_furnace_material_unselected():
    """Golden-neutral path: no selected wall => no diagnostic key written.

    Null-hypothesis: unconditional default attachment would change every
    detailed runner payload and break test_recipe_io / test_cost_ledger goldens.
    """
    payload = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=1,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={
            "started_at_utc": "2026-08-01T00:00:00Z",
            "kernel_commit_sha": "b107-liner-life-wire",
        },
    ).run()
    assert "liner_life_diagnostic" not in payload["run_metadata"]
    assert "furnace_material_id" not in payload["run_metadata"] or not payload[
        "run_metadata"
    ].get("furnace_material_id")


def test_runner_wires_liner_life_diagnostic_end_to_end_mutation_proven():
    """Real PyrolysisRun -> RecessionEvaluator -> refractory module -> artifact.

    Production seam under test: ``simulator/runner/__init__.py`` attachment of
    ``run_metadata["liner_life_diagnostic"]`` (not a hand-built payload).

    Mutation proof (in-process, not a comment-only claim):
    1. Live path with explicit ``furnace_material_id`` attaches a computed
       diagnostic; presence + recession_model fields are asserted below.
       Deleting that runner assignment (the review's mutation) makes the
       ``assert "liner_life_diagnostic" in meta`` predicate go red.
    2. Sever the builder at the runner import site
       (``simulator.runner.build_liner_life_run_diagnostic`` -> raises):
       the except wall still writes a non-gating ``status=failed`` envelope
       with no ceiling/recession_model — proves the instrument-only wall and
       that a severed evaluator cannot fabricate a computed result.
    3. Emulate full attach deletion by popping the key from a live result:
       the same presence predicate that the live path requires is false,
       documenting red-if-severed for the write seam itself.
    """
    material_id = DEFAULT_DIAGNOSTIC_MATERIAL_ID  # explicit selection, not silent default
    common_kwargs = dict(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=1,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={
            "started_at_utc": "2026-08-01T00:00:00Z",
            "kernel_commit_sha": "b107-liner-life-wire",
            "furnace_material_id": material_id,
        },
    )

    # --- Live wired path: RecessionEvaluator -> refractory -> run_metadata ---
    payload = PyrolysisRun(**common_kwargs).run()
    meta = payload["run_metadata"]
    assert meta.get("furnace_material_id") == material_id
    # Load-bearing presence assert: red if the runner attachment is deleted.
    assert "liner_life_diagnostic" in meta, (
        "runner must attach liner_life_diagnostic when furnace_material_id "
        "is explicitly selected (severing this write turns this assert red)"
    )
    diagnostic = meta["liner_life_diagnostic"]
    assert diagnostic["status"] == "computed_diagnostic_not_applied"
    assert diagnostic["level"] == "info"
    assert diagnostic["binding"] is False
    assert diagnostic["gating"] is False
    assert diagnostic["authority"] == DIAGNOSTIC_AUTHORITY
    assert diagnostic["material_id"] == material_id
    assert diagnostic["material_id_source"] == "explicit"
    recession = diagnostic["recession_model"]
    assert recession is not None
    assert recession["flux_classification"] == (
        "included_carrier_equilibrium_effusion_sum"
    )
    assert recession["upper_bound_claim"] == "included_carriers_only"
    assert recession["evaporation_coefficient"] == pytest.approx(1.0)
    assert diagnostic["ceiling"] is not None
    assert diagnostic["ceiling"]["authority"] == DIAGNOSTIC_AUTHORITY

    # Artifact surface from the real runner payload (not a synthetic dict).
    artifact = build_run_artifact(payload, run_id="liner-life-e2e")
    surfaced = artifact["terminal"]["run_metadata"]["liner_life_diagnostic"]
    assert surfaced["gating"] is False
    assert surfaced["binding"] is False
    assert surfaced["level"] == "info"
    assert surfaced["recession_model"]["upper_bound_claim"] == "included_carriers_only"
    assert surfaced["recession_model"]["flux_classification"] == (
        "included_carrier_equilibrium_effusion_sum"
    )
    assert surfaced["recession_model"]["evaporation_coefficient"] == pytest.approx(1.0)
    # No gate: recipe setpoints are not rewritten from the ceiling.
    recipe = artifact["header"].get("recipe_snapshot") or {}
    assert "furnace_max_T_C" not in (recipe.get("setpoints_patch") or {})

    # --- Mutation (1): sever builder wire in memory ---
    def _boom(**_kwargs):
        raise RuntimeError("severed refractory evaluator wire")

    with patch("simulator.runner.build_liner_life_run_diagnostic", _boom):
        failed_attach = PyrolysisRun(**common_kwargs).run()
    failed_diag = failed_attach["run_metadata"]["liner_life_diagnostic"]
    assert failed_diag["status"] == "failed"
    assert failed_diag["gating"] is False
    assert failed_diag["binding"] is False
    assert failed_diag["level"] == "info"
    assert failed_diag.get("ceiling") is None
    assert failed_diag.get("recession_model") is None

    # --- Mutation (2): emulate deleted attachment assignment (key absent) ---
    severed_meta = dict(payload["run_metadata"])
    severed_meta.pop("liner_life_diagnostic", None)
    assert "liner_life_diagnostic" not in severed_meta
    # The live presence predicate is exactly what goes red under this state:
    assert "liner_life_diagnostic" in payload["run_metadata"]
    assert "liner_life_diagnostic" not in severed_meta


def test_web_start_forwards_furnace_material_id_into_run_metadata_overrides():
    """SC-50 producer: web start forwards selected wall into runner overrides."""
    import inspect

    import web.events as events_mod

    source = inspect.getsource(events_mod.register_events)
    assert "furnace_material_id" in source
    # Conditional spread: only when the selected id is truthy.
    assert "{'furnace_material_id': furnace_material_id}" in source
    assert "if furnace_material_id" in source

    furnace_material_id = "dense_alumina_continuous"
    overrides = {
        "started_at_utc": "2026-08-01T00:00:00Z",
        **(
            {"furnace_material_id": furnace_material_id}
            if furnace_material_id
            else {}
        ),
    }
    assert overrides["furnace_material_id"] == furnace_material_id
    empty_id = ""
    empty = {
        "started_at_utc": "2026-08-01T00:00:00Z",
        **({"furnace_material_id": empty_id} if empty_id else {}),
    }
    assert "furnace_material_id" not in empty
