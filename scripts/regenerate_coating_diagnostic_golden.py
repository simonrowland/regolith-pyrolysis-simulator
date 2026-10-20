#!/usr/bin/env python3
"""Regenerate the coating-diagnostic default-output SHA pin from the executable.

The producer uses the same engine-config-free default as the test, independent
of the host's engines.local.toml. Run through scripts/studio-regen.sh on a
studio for the supported golden update route.

Producer matches tests/test_coating_rate.py::
test_coating_diagnostic_default_output_is_byte_identical_to_golden.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_PATH = ROOT / "tests" / "test_coating_rate.py"
PIN_RE = re.compile(
    r"(def test_coating_diagnostic_default_output_is_byte_identical_to_golden\b"
    r"[\s\S]*?"
    r"assert hashlib\.sha256\(actual_bytes\)\.hexdigest\(\) == \(\s*\n"
    r'\s*")([0-9a-f]{64})(")',
    re.MULTILINE,
)


def produce_bytes() -> bytes:
    from tests.coating_diagnostic_output import (
        produce_default_coating_diagnostic,
    )

    actual_bytes, _engagement = produce_default_coating_diagnostic()
    return actual_bytes


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
