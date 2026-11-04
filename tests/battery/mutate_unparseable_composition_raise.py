#!/usr/bin/env python3
"""Restore the composition-parse raise and show the store test goes red.

The production catch lives in ``_engine_prediction_or_unparseable_component``.
This script replaces that function with a direct call, runs the store test,
and writes the original file back. Exit 0 means the test failed because
``UnparseableCompositionComponent`` left ``score_store``. Exit 1 means the
test still passed, or it failed for another reason.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCORE = ROOT / "simulator" / "battery" / "score.py"
START = "def _engine_prediction_or_unparseable_component("
END = "\ndef predict_with_engine("
REPLACEMENT = '''def _engine_prediction_or_unparseable_component(
    engine: Engine,
    observation: Observation,
    *,
    handles: Mapping[str, object] | None = None,
    isolated: bool | None = None,
    experiment: Experiment | None = None,
    bench: Bench | None = None,
    fo2_mode: Fo2Mode = Fo2Mode.SHARED_LADDER,
    phase_siblings: Mapping[str, Decimal] | None = None,
    phase_sibling_refusal: str | None = None,
) -> EnginePrediction:
    return _predict_with_engine(
        engine,
        observation,
        handles=handles,
        isolated=isolated,
        experiment=experiment,
        bench=bench,
        fo2_mode=fo2_mode,
        phase_siblings=phase_siblings,
        phase_sibling_refusal=phase_sibling_refusal,
    )
'''


def main() -> int:
    original = SCORE.read_text(encoding="utf-8")
    start = original.find(START)
    end = original.find(END, start)
    if start < 0 or end < 0:
        print("mutation target is missing", file=sys.stderr)
        return 1
    mutated = original[:start] + REPLACEMENT + original[end + 1 :]
    if mutated == original:
        print("mutation did not change the predictor", file=sys.stderr)
        return 1
    SCORE.write_text(mutated, encoding="utf-8")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-n",
                "0",
                "-p",
                "no:cacheprovider",
                "tests/battery/test_unparseable_composition_component.py",
                "-k",
                "score_store_refuses_one_unparseable_name",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    finally:
        SCORE.write_text(original, encoding="utf-8")
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    failed = completed.returncode != 0
    raised = "UnparseableCompositionComponent" in completed.stdout + completed.stderr
    if failed and raised:
        print("store test went red when the raise was restored")
        return 0
    print(
        f"expected a red store test, pytest exit {completed.returncode}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
