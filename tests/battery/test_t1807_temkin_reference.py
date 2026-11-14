"""Regression pins for Distin's typed Temkin reference frame."""

import ast
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import ReferenceStateConvention
from simulator.battery.records import StandardState
from simulator.battery.score import fusion_comparison_reference
from tests.battery.test_t1243_feo_reference import (
    _DISTIN,
    _conversion_notice,
    _feo_rows,
    _independent_factor,
    migration,
)


_CONSUMER_MODULES = (
    "simulator/battery/migrate.py",
    "simulator/battery/score.py",
    "simulator/battery/generators/bench.py",
    "simulator/battery/fo2_ladder.py",
    "simulator/battery/records.py",
)


def _has_temkin_arm(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == "TEMKIN"
        and isinstance(node.value, ast.Name)
        and node.value.id == "ReferenceStateConvention"
        for node in ast.walk(tree)
    )


def test_distin_migrates_as_temkin_and_keeps_baseline_conversion_and_band(
    migration,
) -> None:
    """A Temkin→Raoultian mutation must fail before it can change scoring."""
    rows = tuple(row for row in _feo_rows(migration) if row.source_id == _DISTIN)
    assert len(rows) == 24
    assert all(
        row.identity.reference_state.value.convention
        is ReferenceStateConvention.TEMKIN
        for row in rows
    )
    row = next(
        item
        for item in rows
        if item.observation_id.endswith("distin1971_t1_1880_1_4")
    )
    standard = row.identity.reference_state.value
    assert isinstance(standard, StandardState)
    assert standard.convention is ReferenceStateConvention.TEMKIN

    converted = fusion_comparison_reference(row)
    notice = _conversion_notice(converted.notices)
    source, conversion = notice.reason.split("source_value=", 1)[1].split(
        "; converted_value=", 1
    )
    converted_value, _details = conversion.split("; factor=", 1)
    expected = Decimal(source) * _independent_factor(Decimal("2153.15"))
    assert Decimal(converted_value) == expected
    assert converted.value.point == expected
    assert (
        converted.identity.reference_state.value.convention
        is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
    )

    saturation = standard.iron_saturation_composition
    assert saturation is not None
    assert saturation.n_fe2_plus == Decimal(
        "0.9861097841165973149618546920"
    )
    assert saturation.band_dex == Decimal(
        "0.007436620909554859980899190015"
    )
    saturation_notice = next(
        item
        for item in converted.notices
        if item.reason.startswith("composition_derived_from_source_analyses")
    )
    assert saturation_notice.band == "0.007436620909554859980899190015 dex"


def test_every_reference_state_consumer_has_a_temkin_arm() -> None:
    root = Path(__file__).resolve().parents[2]
    for convention in ReferenceStateConvention:
        assert convention.value
    for relative_path in _CONSUMER_MODULES:
        assert _has_temkin_arm(root / relative_path), relative_path

    # __init__.py is the public export boundary; exposing the enum exposes its
    # closed member set without adding a second token-level export.
    from simulator.battery import ReferenceStateConvention as PublicConvention

    assert PublicConvention is ReferenceStateConvention
    assert PublicConvention.TEMKIN is ReferenceStateConvention.TEMKIN
