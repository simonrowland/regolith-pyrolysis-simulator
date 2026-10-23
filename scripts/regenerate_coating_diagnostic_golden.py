#!/usr/bin/env python3
"""Regenerate the coating-diagnostic default-output SHA pin from the executable.

Machine-sensitive: run under the STUDIO CI engine config (see scripts/studio-regen.sh).
Laptop regens of this pin have red-on-gate history (train11 class).

Producer matches tests/test_coating_rate.py::
test_coating_diagnostic_default_output_is_byte_identical_to_golden.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.coating_golden import produce_coating_golden  # noqa: E402

TEST_PATH = ROOT / "tests" / "test_coating_rate.py"
PIN_RE = re.compile(
    r"(def test_coating_diagnostic_default_output_is_byte_identical_to_golden\b"
    r"[\s\S]*?"
    r"assert hashlib\.sha256\(actual_bytes\)\.hexdigest\(\) == \(\s*\n"
    r'\s*")([0-9a-f]{64})(")',
    re.MULTILINE,
)


def produce_bytes() -> bytes:
    return produce_coating_golden()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check the pin without writing")
    args = parser.parse_args()
    digest = hashlib.sha256(produce_bytes()).hexdigest()
    text = TEST_PATH.read_text(encoding="utf-8")
    match = PIN_RE.search(text)
    if match is None:
        raise RuntimeError(
            f"coating diagnostic SHA pin not found in {TEST_PATH.relative_to(ROOT)}"
        )
    old = match.group(2)
    new_text = PIN_RE.sub(rf"\g<1>{digest}\g<3>", text, count=1)
    if not args.check and new_text != text:
        TEST_PATH.write_text(new_text, encoding="utf-8")
    print(f"coating_diagnostic_sha old={old}")
    print(f"coating_diagnostic_sha new={digest}")
    print(f"changed={'yes' if old != digest else 'no'}")
    print(TEST_PATH.relative_to(ROOT))
    return int(args.check and old != digest)


if __name__ == "__main__":
    raise SystemExit(main())
