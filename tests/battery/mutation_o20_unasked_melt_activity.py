"""Drop the unasked melt-activity branch and prove the new test goes red.

Run from the seat with PYTHONPATH set and MPLCONFIGDIR set:
``python tests/battery/mutation_o20_unasked_melt_activity.py``.
"""

from __future__ import annotations

import inspect
import textwrap

import pytest

import simulator.battery.score as score


_BRANCH = """\
        called = isinstance(execution, Mapping) and bool(
            execution.get("call_evidence")
        )
        if not called and reason in {
            RefusalReason.UNSUPPORTED.value,
            "unsupported_species",
        }:
            return False
"""

_TEST = (
    "tests/battery/test_score.py::"
    "test_magemin_melt_activity_call_stays_observable"
)


def main() -> None:
    original = score._residual_engine_observable
    source = textwrap.dedent(inspect.getsource(original))
    if source.count(_BRANCH) != 1:
        raise SystemExit("unasked melt-activity branch was not found once")
    mutated = source.replace(_BRANCH, "")
    namespace: dict[str, object] = {}
    exec(compile(mutated, "<o20-unasked-melt-activity-mutation>", "exec"), score.__dict__, namespace)
    score._residual_engine_observable = namespace["_residual_engine_observable"]
    try:
        result = pytest.main(
            ["-q", "-n", "0", "-p", "no:cacheprovider", "--timeout=300", _TEST]
        )
    finally:
        score._residual_engine_observable = original
    print("o20 unasked melt-activity mutation exit", result)
    if result != pytest.ExitCode.TESTS_FAILED:
        raise SystemExit(
            f"mutation did not fail {_TEST}; pytest exit {result}"
        )


if __name__ == "__main__":
    main()
