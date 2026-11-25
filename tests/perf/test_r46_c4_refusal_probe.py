"""Temporary r46 receipt probe for the optimizer C4 terminal refusal."""

import json
from collections.abc import Mapping
from importlib import import_module


def test_optimizer_c4_refusal_diagnostic(monkeypatch) -> None:
    import benchmarks.engine_throughput_bench as bench
    evaluate_module = import_module("simulator.optimize.evaluate")

    captured = []
    original_evaluate = evaluate_module.evaluate

    def capture_evaluate(*args, **kwargs):
        result = original_evaluate(*args, **kwargs)
        reference = result.run_reference
        trace = reference.trace if reference is not None else None
        diagnostic = (
            trace.get("refusal_diagnostic")
            if isinstance(trace, Mapping)
            else None
        )
        if isinstance(diagnostic, Mapping):
            captured.append(dict(diagnostic))
            print("R46_C4_REFUSAL=" + json.dumps(dict(diagnostic), sort_keys=True))
        return result

    monkeypatch.setattr(evaluate_module, "evaluate", capture_evaluate)
    monkeypatch.setattr(
        bench,
        "_assert_optimizer_campaign_work",
        lambda *, step_calls, **_kwargs: step_calls,
    )

    bench._measure_optimizer_profile_campaign_chain_stage(trial_count=1)

    c4 = [item for item in captured if item.get("campaign") == "C4"]
    assert c4 and c4[-1].get("status") == "refused"
