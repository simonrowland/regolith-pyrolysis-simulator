"""Mutation proof that all dictionary-bound alumina crystals are considered."""

from __future__ import annotations

import pytest

from simulator.battery.generators import janaf


for table_id in ("Al-097", "Al-098", "Al-099"):
    janaf._JANAF_POLYMORPH_BY_TABLE.pop(table_id)

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "--timeout=300",
        "tests/battery/test_compilation_tier.py::"
        "test_fusion_stability_uses_all_dictionary_alumina_crystals",
    ]
)
print("t1240 omitted alumina crystals MUTATION_EXIT", result)
assert result == pytest.ExitCode.TESTS_FAILED
