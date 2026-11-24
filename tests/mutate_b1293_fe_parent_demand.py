"""Swap the furnace Fe parent demand back to the exponent inversion.

Does not run pytest. ``--restore`` puts the ledger-q return back.
The marker is the Fe(g) return in ``simulator/langmuir_surface_oxygen.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PATH = (
    Path(__file__).resolve().parents[1]
    / "simulator"
    / "langmuir_surface_oxygen.py"
)
_OWNER = (
    "return iron_mean_oxygen_per_atom(fe3_over_sum_fe) * n_fe"
    "  # b1293-fe-obar\n"
)
_INVERTED = (
    "return oxygen_atoms - 2.0 * exponent  # b1293-fe-obar mutated\n"
)


def main() -> int:
    text = _PATH.read_text()
    restore = "--restore" in sys.argv[1:]
    source = _INVERTED if restore else _OWNER
    target = _OWNER if restore else _INVERTED
    count = text.count(source)
    if count != 1:
        print(f"marker count is {count}, expected 1", file=sys.stderr)
        return 1
    _PATH.write_text(text.replace(source, target, 1))
    print("restored" if restore else "mutated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
