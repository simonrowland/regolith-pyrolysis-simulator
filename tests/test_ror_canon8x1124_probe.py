"""Score oxygen is one typed admission, and air is the sample atmosphere."""

from dataclasses import replace
from decimal import Decimal
import inspect
import runpy
import textwrap

import pytest

from simulator.battery import fo2_ladder, oxygen_identity, score
from simulator.battery.enums import (
    AmbientPressureEvidenceClass,
    NoticeKind,
    Quantity,
)
from simulator.battery.oxygen_identity import _air_composition_quote
from simulator.battery.records import (
    AmbientPressureEvidence,
    Located,
    Locator,
    Notice,
    NoticePolicy,
    State,
)
from tests.battery import factories as F

EXACT_PA = Decimal("3")
RUN_FIELDS = ("fO2_Pa", "oxygen_partial_pressure_Pa")


def _run_row(field: str, pressure: Decimal = EXACT_PA):
    temperature = Decimal("1400")
    identity = replace(F.activity_identity(T_K=temperature), fO2_Pa=None)
    experiment = F.tabulation_experiment(experiment_id="run", T_K=temperature)
    experiment = replace(
        experiment,
        conditions={
            **experiment.conditions,
            field: Located(
                State.of(pressure), locator=Locator(note="run oxygen")
            ),
        },
    )
    row = F.observation("row", "run", identity, Decimal(1))
    return identity, row, experiment, temperature


def _ladder(experiment, row, temperature):
    """The activity generator's call: routes come from the waypoint owner."""

    from simulator.battery.waypoints import oxygen_condition

    oxygen = oxygen_condition(experiment, None, row)
    return fo2_ladder.resolve_row_fo2(
        experiment,
        None,
        row,
        temperature_K=float(temperature),
        oxygen=oxygen,
        quantity=Quantity.ACTIVITY,
    )


@pytest.mark.parametrize("quote", [
    "Before the run, the sample was held in air. Equilibration was under argon.",
    "The reference gas equilibrated in air. The sample equilibrated under argon.",
])
def test_air_must_describe_sample_equilibration(quote):
    evidence = AmbientPressureEvidence(
        AmbientPressureEvidenceClass.ATMOSPHERIC_PRESSURE_STATEMENT,
        quote,
        Locator(note="review fixture"),
    )
    assert _air_composition_quote(evidence) is None


@pytest.mark.parametrize("quote", [
    "Samples were equilibrated in air.",
    "The sample was equilibrated in air.",
])
def test_sample_equilibrated_in_air_still_matches(quote):
    evidence = AmbientPressureEvidence(
        AmbientPressureEvidenceClass.ATMOSPHERIC_PRESSURE_STATEMENT,
        quote,
        Locator(note="review fixture"),
    )
    assert _air_composition_quote(evidence) == quote


@pytest.mark.parametrize("field", RUN_FIELDS)
def test_phase_path_preserves_exact_pascals_for_printed_run_condition(field):
    identity, row, experiment, temperature = _run_row(field)
    resolved, log, notices = score._resolve_row_fo2(
        identity, row, experiment, {"FeO": 100.0}, temperature, Quantity.ACTIVITY
    )
    assert log is not None
    assert not notices
    assert resolved.fO2_Pa.value == EXACT_PA


@pytest.mark.parametrize("field", RUN_FIELDS)
def test_ladder_preserves_exact_pascals_for_printed_run_condition(field):
    identity, row, experiment, temperature = _run_row(field)
    resolved = _ladder(experiment, row, temperature)
    assert resolved is not None
    assert resolved.pressure_pa == EXACT_PA
    assert not resolved.notices


def _dp112(reason: str, *, policy: bool) -> Notice:
    return Notice(
        kind=NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED,
        affected_quantities=(Quantity.ACTIVITY,),
        reason=reason,
        origin="score:DP-112" if policy else "migration",
        policy=(
            NoticePolicy(
                id="DP-112",
                version=1,
                status="proposed",
                value="11",
                units="Pa",
            )
            if policy
            else None
        ),
    )


def test_receipted_buffer_precedes_a_different_run_pressure():
    identity, row, experiment, temperature = _run_row("fO2_Pa", Decimal("3"))
    identity = replace(identity, fO2_Pa=State.of(Decimal("11")))
    row = replace(row, identity=identity, notices=(_dp112("buffer=NNO offset=0", policy=True),))
    resolved, log, notices = score._resolve_row_fo2(
        identity, row, experiment, {"FeO": 100.0}, temperature, Quantity.ACTIVITY
    )
    assert resolved.fO2_Pa.value == Decimal("11")
    assert log is not None
    assert any(
        notice.policy is not None and notice.policy.id == "DP-112" for notice in notices
    )
    ladder = _ladder(experiment, row, temperature)
    assert ladder is not None
    assert ladder.pressure_pa == Decimal("11")
    assert ladder.route == "identity_fO2_Pa"
    assert any(
        notice.policy is not None and notice.policy.id == "DP-112"
        for notice in ladder.notices
    )


def test_unresolved_marker_uses_the_buffer_resolver(monkeypatch):
    identity, row, experiment, temperature = _run_row("fO2_Pa", Decimal("3"))
    # No located run pressure, so the marker is the only oxygen evidence.
    # Temperature stays: an experiment with no conditions is not a record.
    experiment = replace(
        experiment,
        conditions={
            key: value
            for key, value in experiment.conditions.items()
            if key not in RUN_FIELDS
        },
    )
    row = replace(
        row,
        notices=(
            _dp112("buffer=NNO offset=0 calibration=unresolved", policy=False),
        ),
    )
    calls = []
    real = oxygen_identity._resolve_score_time_buffer_identity

    def spy(observation, **kwargs):
        calls.append(observation.observation_id)
        resolved_identity = replace(observation.identity, fO2_Pa=State.of(Decimal("7")))
        return replace(
            observation,
            identity=resolved_identity,
            notices=(
                *observation.notices,
                _dp112("buffer=NNO offset=0", policy=True),
            ),
        )

    del real
    # Phase keeps a module-level binding. The ladder imports the owner at the call.
    monkeypatch.setattr(oxygen_identity, "_resolve_score_time_buffer_identity", spy)
    monkeypatch.setattr(score, "_resolve_score_time_buffer_identity", spy)
    resolved, _log, notices = score._resolve_row_fo2(
        identity, row, experiment, {"SiO2": 100.0}, temperature, Quantity.ACTIVITY
    )
    ladder = _ladder(experiment, row, temperature)
    assert calls == [row.observation_id, row.observation_id]
    assert resolved.fO2_Pa.value == Decimal("7")
    assert any(notice.policy is not None and notice.policy.id == "DP-112" for notice in notices)
    assert ladder is not None
    assert ladder.pressure_pa == Decimal("7")
    assert ladder.route == "score:DP-112"


def test_sensor_guard_turns_red_with_prior_matcher(monkeypatch):
    quote = "The furnace sensor recorded air during equilibration."
    evidence = AmbientPressureEvidence(
        AmbientPressureEvidenceClass.ATMOSPHERIC_PRESSURE_STATEMENT,
        quote,
        Locator(note="review fixture"),
    )

    def require_sample_atmosphere():
        assert oxygen_identity._air_composition_quote(evidence) is None, (
            "sensor was mistaken for the sample"
        )

    require_sample_atmosphere()
    driver = runpy.run_path("tests/mutations/canon_fold6_restore.py")
    _path, anchor, _replacement = driver["_AIR_FREE"][0]
    old = '''    air = re.search(
        r"\\bair\\b.{0,60}\\b(?:during|throughout)\\s+equilibrat\\w*",
        normalized_quote,
    )
    return source_quote if air is not None else None
'''
    source = textwrap.dedent(inspect.getsource(oxygen_identity._air_composition_quote))
    assert source.count(anchor) == 1
    symbols = dict(vars(oxygen_identity))
    exec("from __future__ import annotations\n" + source.replace(anchor, old), symbols)
    monkeypatch.setattr(
        oxygen_identity, "_air_composition_quote", symbols["_air_composition_quote"]
    )
    with pytest.raises(AssertionError, match="sensor was mistaken for the sample"):
        require_sample_atmosphere()
