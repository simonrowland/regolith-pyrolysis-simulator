#!/usr/bin/env bash
# Verify the MACE MPS hotfix (0001-mps-device-aware-energy-accumulation) is
# APPLIED in the mace package importable by the given python. Exit 0 = applied,
# 1 = not applied, 2 = cannot determine (mace missing). "Cannot determine" is
# an error on purpose: a box that cannot be checked must never read as patched.
#
#   patches/mace/verify-applied.sh /path/to/venv/bin/python
set -u
PY="${1:?usage: verify-applied.sh <venv-python>}"
"$PY" - <<'EOF'
import sys
try:
    import mace.modules.models as m
except Exception as exc:
    print(f"mace not importable: {exc}")
    sys.exit(2)
import pathlib
src = pathlib.Path(m.__file__).read_text()
if "MPS-hotfix 2026-08-19" in src:
    print(f"hotfix APPLIED in {m.__file__}")
    sys.exit(0)
print(f"hotfix ABSENT in {m.__file__} — an MPS forward pass will crash on float64")
sys.exit(1)
EOF
