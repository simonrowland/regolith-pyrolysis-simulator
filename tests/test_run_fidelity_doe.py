from pathlib import Path

import pytest

from scripts import run_fidelity_doe as doe


def test_timing_log_failure_does_not_replace_evaluator_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EvaluatorFailure(RuntimeError):
        pass

    def fail_evaluation(*_args, **_kwargs):
        raise EvaluatorFailure("primary evaluator failure")

    monkeypatch.setattr(doe, "_evaluate", fail_evaluation)
    monkeypatch.setattr(doe, "TIMING_LOG", str(tmp_path / "missing" / "timings.jsonl"))

    with pytest.raises(EvaluatorFailure) as caught:
        doe._timed_evaluate({}, "feedstock", "tier")

    assert caught.value.__notes__ == [
        "timing-log reporting failed: FileNotFoundError"
    ]
