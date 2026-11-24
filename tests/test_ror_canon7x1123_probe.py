from dataclasses import replace
from decimal import Decimal
import json

import pytest

from simulator.battery import fo2_ladder, score
from simulator.battery.enums import (
    AmbientPressureEvidenceClass,
    Engine,
    NoticeKind,
    Quantity,
    ValueKind,
)
from simulator.battery.migrate import _BARE_LOG_SCALE_UNKNOWN
from simulator.battery.oxygen_identity import _air_composition_quote
from simulator.battery.records import (
    AmbientPressureEvidence,
    Located,
    Locator,
    State,
    Value,
)
from tests.battery import factories as F
from tests.battery.test_score_workers import (
    _score_rows_and_summary,
    _worker_fixture,
    _worker_test_predict,
)


def air_quote(quote):
    evidence = AmbientPressureEvidence(
        AmbientPressureEvidenceClass.ATMOSPHERIC_PRESSURE_STATEMENT,
        quote,
        Locator(note="review fixture"),
    )
    return _air_composition_quote(evidence)


def test_sensor_subject_before_air_is_not_sample_atmosphere():
    quote = "The furnace sensor recorded air during equilibration."
    assert air_quote(quote) is None, quote


def test_atmosphere_log_commands_resolved_pa():
    # A located log10(fO2/atm) of zero corresponds to exactly 101325 Pa.
    row = frame_row(State.of(Decimal("101325")))
    resolved, engine_log, _notices = score._resolve_row_fo2(
        row.identity, row, None, {"FeO": 100.0}, Decimal(1400), Quantity.ACTIVITY
    )
    assert resolved.fO2_Pa.value == Decimal("101325")
    assert float(engine_log) == pytest.approx(0.0057166124137292)


def frame_row(frame):
    identity = replace(F.activity_identity(T_K=Decimal(1400)), fO2_Pa=frame)
    row = F.observation("frame-row", "frame-exp", identity, Decimal(1))
    return replace(
        row,
        point_conditions={
            **(row.point_conditions or {}),
            "fO2_log": Located(State.of(Decimal(0)), locator=Locator(note="printed log")),
        },
    )


def test_vapour_unknown_frame_carries_typed_DP010(monkeypatch):
    row = frame_row(State.unknown(_BARE_LOG_SCALE_UNKNOWN))
    monkeypatch.setattr(score, "oxygen_is_scorer_input", lambda *_: (True, None, ()))
    notices = []
    request = score._oxygen_input_request(
        engine=Engine.INTERNAL_ANALYTICAL,
        channel=Engine.INTERNAL_ANALYTICAL.value,
        sources=(),
        identity=row.identity,
        requested=None,
        quantity=Quantity.P_PARTIAL,
        composition_value=None,
        composition_wt={"SiO2": 100.0},
        temperature_K=1400.0,
        observation=row,
        input_notices=notices,
    )
    assert request.po2_bar == pytest.approx(1e-9)
    assert any(
        notice.policy is not None
        and notice.policy.id == "DP-010"
        and notice.policy.value == "-9.0"
        and "printed_fO2_log=0" in notice.reason
        for notice in notices
    ), notices


def test_frame_mutation_restores_wrong_bar_command(monkeypatch):
    row = frame_row(State.unknown(_BARE_LOG_SCALE_UNKNOWN))

    def require_policy():
        _identity, log, notices = score._resolve_row_fo2(
            row.identity, row, None, {"SiO2": 100.0}, Decimal(1400), Quantity.ACTIVITY
        )
        assert log != 0 and any(
            notice.policy is not None and notice.policy.id == "DP-010"
            for notice in notices
        ), "unknown log became a clean bar command"

    require_policy()
    monkeypatch.setattr(fo2_ladder, "absolute_oxygen_frame", lambda _identity: True)
    monkeypatch.setattr(score, "_has_printed_fo2", lambda *_: True)
    with pytest.raises(AssertionError, match="unknown log became a clean bar command"):
        require_policy()


def _power_of_ten_context():
    """Printed 1000 against a prediction of 1.

    The dex is log10(0.001). Its exact token is ``-3`` and the legacy float
    text is ``-3.0``, so a dropped sidecar moves median_dex. Real vapour
    pressures already float-roundtrip and do not.
    """

    context = _worker_fixture(experiment_count=2)
    observations = {
        observation_id: replace(
            observation,
            value=Value(ValueKind.POINT, point=Decimal("1000")),
        )
        for observation_id, observation in context.observations.items()
    }
    return replace(context, observations=observations)


def summary_differences(tmp_path):
    context = _power_of_ten_context()
    serial_rows, serial_summary = _score_rows_and_summary(
        context,
        tmp_path / "serial.jsonl",
        1,
        predict=_worker_test_predict,
    )
    parallel_rows, parallel_summary = _score_rows_and_summary(
        context,
        tmp_path / "parallel.jsonl",
        3,
        predict=_worker_test_predict,
    )
    assert serial_rows == parallel_rows
    serial = json.loads(serial_summary)
    parallel = json.loads(parallel_summary)
    return {
        key: (serial.get(key), parallel.get(key))
        for key in sorted(serial.keys() | parallel.keys())
        if serial.get(key) != parallel.get(key)
    }


def test_decoded_summary_parity(tmp_path):
    assert summary_differences(tmp_path) == {}


def test_summary_mutation_drops_exact_index_text(tmp_path, monkeypatch):
    parse = score.parse_residual_order_entry

    def discard_exact_text(value):
        entry = parse(value)
        return None if entry is None else replace(entry, exact=False)

    monkeypatch.setattr(score, "parse_residual_order_entry", discard_exact_text)
    differences = summary_differences(tmp_path)
    assert differences, "legacy float text did not change the decoded summary"
    print("legacy summary changed keys:", tuple(differences))
