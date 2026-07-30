from __future__ import annotations

import math

import numpy as np
import pytest

from simulator.liner_life import (
    AnalyticRecessionScreen,
    DIAGNOSTIC_DEPENDENCIES,
    LinerLifeConfiguration,
    LinerLifeInputRefusal,
    LinerLifeRefusal,
    LinerLifeTarget,
    RecessionDataUnavailable,
    RecessionMonotonicityEvidence,
    derive_liner_temperature_ceiling,
    liner_temperature_ceiling_diagnostic,
    wear_budget_mm_per_1000h,
)


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


def test_canonical_catalogue_missing_liner_config_refuses_instead_of_inventing():
    with pytest.raises(LinerLifeInputRefusal) as caught:
        LinerLifeConfiguration.from_material_catalogue(
            "dense_alumina_continuous",
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
