from pathlib import Path

import pytest

from scripts import run_fidelity_doe as doe
from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN


@pytest.mark.parametrize(
    "env_name",
    (
        "FIDELITY_DIAGNOSTIC_INTERNAL_ANALYTICAL_HIGH",
        "FIDELITY_DIAGNOSTIC_STUB_HIGH",
    ),
)
def test_diagnostic_high_env_names_are_dual_aliases(env_name: str) -> None:
    assert doe._diagnostic_internal_analytical_high_from_env({env_name: "1"}) is True


@pytest.mark.parametrize(
    "backend_alias",
    ("stub", "internal-analytical", "Internal_Analytical"),
)
def test_analytical_high_backend_aliases_require_diagnostic_opt_in(
    backend_alias: str,
) -> None:
    high_backend = doe._high_backend_from_env(
        {"FIDELITY_HIGH_BACKEND": backend_alias},
        diagnostic_internal_analytical_high=False,
    )

    assert high_backend == ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
    with pytest.raises(RuntimeError, match="diagnostic only"):
        doe._validate_high_backend_selection(
            high_backend,
            diagnostic_internal_analytical_high=False,
        )


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
