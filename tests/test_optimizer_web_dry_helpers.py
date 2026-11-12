from __future__ import annotations

from pathlib import Path

import pytest

from simulator.ceramic_classifier import CeramicServiceTemperature
from simulator.optimize import job_runner
from simulator.optimize.evaluate import FailureCategory, ScoredResult
from simulator.optimize.import_bundle import _optional_bool as optimizer_optional_bool
from simulator.optimize.job_runner import OptimizerJobRunner
from simulator.optimize.recipe import RecipeSchema
from simulator.optimize.strategy import OptunaNSGA2Strategy, OptunaTPEStrategy
from simulator.optimize.strategy.bayesian import (
    _CANDIDATE_ID_ATTR,
    _INFEASIBLE_ATTR,
    _UNSCOREABLE_OBJECTIVES_ATTR,
)
from simulator.optimize.study import _ceramic_service_temp_payload
from web import advisory, events, routes


PROFILE = {"objectives": [{"metric": "yield", "sense": "maximize", "units": "kg"}]}


@pytest.mark.parametrize(
    ("value", "web_expected", "optimizer_expected"),
    [
        (None, None, None),
        ("", False, None),
        ("true", True, True),
        ("false", False, False),
        (0, False, False),
        (1, True, True),
        ("garbage", False, None),
        ("yes", False, True),
    ],
)
def test_optional_bool_current_input_table(
    value: object,
    web_expected: bool | None,
    optimizer_expected: bool | None,
) -> None:
    assert routes._optional_bool(value) is web_expected
    assert optimizer_optional_bool(value) is optimizer_expected


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (
            None,
            {"status": "unknown", "label": "version unknown", "stored_version": None, "current_version": "v-now"},
        ),
        (
            "v-now",
            {"status": "current", "label": "current", "stored_version": "v-now", "current_version": "v-now"},
        ),
        (
            "v-old",
            {"status": "stale", "label": "stale version", "stored_version": "v-old", "current_version": "v-now"},
        ),
    ],
)
def test_version_badge_current_payloads(monkeypatch, stored, expected) -> None:
    monkeypatch.setattr(routes, "current_code_version", lambda: "v-now")
    monkeypatch.setattr(job_runner, "current_code_version", lambda: "v-now")
    assert routes._version_badge(stored) == expected
    runner = OptimizerJobRunner.__new__(OptimizerJobRunner)
    assert runner._version_badge(stored) == expected


@pytest.mark.parametrize(
    ("service_temp", "expected"),
    [
        (
            CeramicServiceTemperature(1800, "certified", 1600, (), "source note"),
            {"value_C": 1800, "kind": "certified", "usable_service_C": 1600, "usable_service": True, "display": "Usable service: 1600 C", "note": "source note"},
        ),
        (
            CeramicServiceTemperature(2200, "fusion", None, (), "not a service rating"),
            {"value_C": 2200, "kind": "fusion", "usable_service_C": None, "usable_service": False, "display": "fusion: 2200 C; not a usable service rating", "note": "not a service rating"},
        ),
        (
            CeramicServiceTemperature(None, "unknown", None, (), "no data"),
            {"value_C": None, "kind": "unknown", "usable_service_C": None, "usable_service": False, "display": "unknown; not a usable service rating", "note": "no data"},
        ),
    ],
)
def test_ceramic_service_temperature_current_payloads(service_temp, expected) -> None:
    assert advisory._ceramic_service_temp_payload(service_temp) == expected
    assert _ceramic_service_temp_payload(service_temp) == expected


def test_web_yaml_loaders_current_payloads(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sample.yaml").write_text("sample:\n  value: 7\n", encoding="utf-8")
    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(events, "DATA_DIR", data_dir)

    assert routes._load_yaml("sample.yaml") == {"sample": {"value": 7}}
    assert events._load_yaml("sample.yaml") == {"sample": {"value": 7}}
    assert routes._load_yaml("missing.yaml") == {}
    assert events._load_yaml("missing.yaml") == {}


@pytest.mark.parametrize("strategy_type", [OptunaTPEStrategy, OptunaNSGA2Strategy])
def test_tell_current_state_after_fixed_sequence(strategy_type) -> None:
    strategy = strategy_type(RecipeSchema(), seed=907, objective_profile=PROFILE)
    candidates = strategy.ask(2)
    results = [
        (
            candidate,
            ScoredResult(
                candidate_id=candidate.id,
                eval_spec=None,
                cache_key=None,
                feasible=False,
                failure_category=FailureCategory.INFEASIBLE_RECIPE,
            ),
        )
        for candidate in candidates
    ]

    strategy.tell(results)

    trials = strategy.study.trials
    assert strategy.tell_count == 2
    assert strategy.results == tuple(results)
    assert [
        (
            trial.state.name,
            trial.user_attrs[_CANDIDATE_ID_ATTR],
            trial.user_attrs[_INFEASIBLE_ATTR],
            trial.user_attrs.get(_UNSCOREABLE_OBJECTIVES_ATTR, False),
        )
        for trial in trials
    ] == [("COMPLETE", candidate.id, True, False) for candidate in candidates]
