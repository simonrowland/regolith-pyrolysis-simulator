from __future__ import annotations

from pathlib import Path

from simulator.battery.enums import (
    Authority,
    Engine,
    QUANTITY_UNITS,
    Quantity,
    Rail,
)
from tests.battery import factories as F


def test_janaf_comparison_writer_uses_run_filters_and_species_hook(
    tmp_path: Path, monkeypatch
) -> None:
    import simulator.battery.compilation_tier as compilation_tier
    from simulator.battery.compilation_tier import (
        _write_compilation_comparisons_jsonl,
        iter_compilation_comparisons_jsonl,
    )
    from tests.battery.test_compilation_tier import _context, _mgo_janaf_observation

    observation = _mgo_janaf_observation()
    context = _context(
        observation,
        origins={observation.observation_id: "compilations-janaf/janaf-Mg.yaml"},
    )
    monkeypatch.setattr(compilation_tier, "_nasa9_fit_comparisons", lambda *_a, **_k: [])

    full_path = tmp_path / "full.jsonl"
    matched_path = tmp_path / "matched.jsonl"
    assert _write_compilation_comparisons_jsonl(
        context, full_path, engines=(), invoke_pure_phase=False
    ) > 0
    assert _write_compilation_comparisons_jsonl(
        context,
        matched_path,
        engines=(),
        invoke_pure_phase=False,
        work_id="work-1",
        rail=Rail.THERMOCHEMISTRY,
        observation_filter=lambda _row: True,
    ) > 0
    assert full_path.read_bytes() == matched_path.read_bytes()

    unmatched_path = tmp_path / "unmatched.jsonl"
    assert _write_compilation_comparisons_jsonl(
        context,
        unmatched_path,
        engines=(),
        invoke_pure_phase=False,
        work_id="unrelated-work",
    ) == 0
    assert tuple(iter_compilation_comparisons_jsonl(unmatched_path)) == ()

    wrong_rail_path = tmp_path / "wrong-rail.jsonl"
    assert _write_compilation_comparisons_jsonl(
        context,
        wrong_rail_path,
        engines=(),
        invoke_pure_phase=False,
        rail=Rail.REDOX,
    ) == 0

    wrong_species_path = tmp_path / "wrong-species.jsonl"
    assert _write_compilation_comparisons_jsonl(
        context,
        wrong_species_path,
        engines=(),
        invoke_pure_phase=False,
        observation_filter=lambda _row: False,
    ) == 0


def test_nasa9_fit_comparisons_filter_source_points(
    monkeypatch,
) -> None:
    import simulator.battery.compilation_tier as compilation_tier
    from simulator.battery.compilation_tier import ThermoAttempt, _nasa9_fit_comparisons
    from tests.battery.test_compilation_tier import _nasa9_fit_test_case

    record, context, _printed_cell = _nasa9_fit_test_case()

    def return_printed_value(_engine, observation, **_kwargs):
        return ThermoAttempt(
            value=observation.value.point,
            unit=QUANTITY_UNITS[Quantity.CP],
            authority=Authority.BRIDGE,
            notices=(),
            refusal_reason=None,
            refusal_detail={},
            call_evidence="comparison-filter-test",
            coefficient_sources=(),
        )

    monkeypatch.setattr(
        compilation_tier, "predict_thermo_attempt", return_printed_value
    )
    common = {
        "engines": (Engine.THERMOENGINE,),
        "invoke_pure_phase": False,
        "fit_records": (record,),
    }

    selected = _nasa9_fit_comparisons(
        context,
        **common,
        work_id="work-1",
        rail=Rail.THERMOCHEMISTRY,
        observation_filter=lambda _row: True,
    )
    assert selected
    assert not _nasa9_fit_comparisons(
        context, **common, work_id="unrelated-work"
    )
    assert not _nasa9_fit_comparisons(context, **common, rail=Rail.REDOX)
    assert not _nasa9_fit_comparisons(
        context, **common, observation_filter=lambda _row: False
    )
