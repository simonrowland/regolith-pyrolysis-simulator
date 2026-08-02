#!/usr/bin/env python3
"""Regenerate the coating-diagnostic default-output SHA pin from the executable.

Machine-sensitive: run under the STUDIO CI engine config (see scripts/studio-regen.sh).
Laptop regens of this pin have red-on-gate history (train11 class).

Producer matches tests/test_coating_rate.py::
test_coating_diagnostic_default_output_is_byte_identical_to_golden.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.runner import PyrolysisRun  # noqa: E402

TEST_PATH = ROOT / "tests" / "test_coating_rate.py"
PIN_RE = re.compile(
    r"(def test_coating_diagnostic_default_output_is_byte_identical_to_golden\b"
    r"[\s\S]*?"
    r"assert hashlib\.sha256\(actual_bytes\)\.hexdigest\(\) == \(\s*\n"
    r'\s*")([0-9a-f]{64})(")',
    re.MULTILINE,
)


def produce_bytes() -> bytes:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=24,
        additives_kg={},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "goal-18-fixture",
        },
    )
    return (
        json.dumps(
            run.run(),
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def main() -> int:
    digest = hashlib.sha256(produce_bytes()).hexdigest()
    text = TEST_PATH.read_text(encoding="utf-8")
    match = PIN_RE.search(text)
    if match is None:
        raise RuntimeError(
            f"coating diagnostic SHA pin not found in {TEST_PATH.relative_to(ROOT)}"
        )
    old = match.group(2)
    new_text = PIN_RE.sub(rf"\g<1>{digest}\g<3>", text, count=1)
    if new_text != text:
        TEST_PATH.write_text(new_text, encoding="utf-8")
    print(f"coating_diagnostic_sha old={old}")
    print(f"coating_diagnostic_sha new={digest}")
    print(f"changed={'yes' if old != digest else 'no'}")
    print(TEST_PATH.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
