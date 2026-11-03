"""Mutation: one JANAF fusion crystal is not the d-144 identity.

Widens the closed constant to every fusion-pair solid that already has
a polymorph binding (Al-096 corundum included) and requires the Al2O3
refusal to fail. Run from the repository root. No absolute paths.
"""

from __future__ import annotations

import pytest

from simulator.battery.generators import janaf

for _oxide, (solid_table, _liquid_table) in janaf._FUSION_TABLES.items():
    polymorph = janaf._JANAF_POLYMORPH_BY_TABLE.get(solid_table)
    if polymorph is not None:
        janaf._D144_SINGLE_POLYMORPH_TABLES[solid_table] = polymorph

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "--timeout=300",
        "tests/battery/test_t1616_d144_single_polymorph.py::"
        "test_alumina_unprinted_solid_is_not_a_single_polymorph_identity",
    ]
)
print("t1616 one JANAF crystal table MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
