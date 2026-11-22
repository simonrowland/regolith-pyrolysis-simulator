#!/usr/bin/env python3
"""t-1917 determinism probe. No arguments.

Run from the repository root. The filename selects the measured class
in scripts/t1917_refusal_probe.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "t1917_refusal_probe",
    ROOT / "scripts" / "t1917_refusal_probe.py",
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load scripts/t1917_refusal_probe.py")
_PROBE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PROBE)


if __name__ == "__main__":
    raise SystemExit(_PROBE.main_for_script(Path(__file__).name))
