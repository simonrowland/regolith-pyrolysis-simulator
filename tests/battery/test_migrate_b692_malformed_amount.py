"""b-692: malformed composition_mol amounts are typed hard issues.

PIN commit 895881ac7 captured the pre-fix silent-drop behaviour. This file
now asserts the refusing contract: one owner ``parse_declared_amount``; a
malformed declared amount raises and lands as ``ValidationIssue``
(``INVALID_SOURCE``), never a silent partial composition.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simulator.battery.enums import RefusalReason
from simulator.battery.migrate import (
    _mole_fraction_composition_from_values,
    migrate,
    parse_declared_amount,
)
from simulator.battery.records import as_decimal
from tests.battery.test_migrate import _scalar_extract, _write_min_tree


_MALFORMED = {
    "composition_mol": {
        "SiO2": "0.6",
        "MgO": "not-a-number",
        "CaO": "0.4",
    }
}


def test_b692_parse_declared_amount_is_single_owner() -> None:
    assert parse_declared_amount("0.6") == as_decimal("0.6")
    assert parse_declared_amount(0.4) == as_decimal("0.4")
    with pytest.raises(ValueError, match="malformed declared amount"):
        parse_declared_amount("not-a-number")


def test_b692_helper_raises_on_malformed_declared_amount() -> None:
    with pytest.raises(ValueError, match="malformed declared amount: 'not-a-number'"):
        _mole_fraction_composition_from_values(_MALFORMED)


def test_b692_migrate_emits_typed_hard_issue_not_partial_composition(
    tmp_path: Path,
) -> None:
    values = {
        "quantity": "activity",
        "activity": 0.2,
        "method_class": "measured_direct",
        **_MALFORMED,
    }
    root = _write_min_tree(
        tmp_path,
        _scalar_extract(
            quantity="activity",
            units="dimensionless",
            values=values,
            obs_type="activity_coefficient",
        ),
    )
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert obs.identity.composition is not None
    assert obs.identity.composition.is_unknown
    assert "malformed declared amount" in (obs.identity.composition.reason or "")

    hard = result.validation.hard_issues if result.validation is not None else ()
    matches = [
        issue
        for issue in hard
        if issue.reason is RefusalReason.INVALID_SOURCE
        and "composition_mol" in (issue.path or "")
        and "malformed declared amount" in (issue.detail or "")
    ]
    assert len(matches) == 1
    assert matches[0].detail == "malformed declared amount: 'not-a-number'"


@pytest.mark.parametrize(
    "bad_amount",
    ["not-a-number", True, None, "", {"nested": 1}, ["1"]],
)
def test_b692_each_unparseable_shape_is_hard_refusal(bad_amount: object) -> None:
    values = {
        "composition_mol": {
            "SiO2": 0.55,
            "MgO": bad_amount,
            "CaO": 0.45,
        }
    }
    with pytest.raises(ValueError, match="malformed declared amount"):
        _mole_fraction_composition_from_values(values)


def test_b692_well_formed_composition_mol_unchanged() -> None:
    composition, omitted = _mole_fraction_composition_from_values(
        {"composition_mol": {"SiO2": 0.5, "MgO": 0.5}}
    )
    assert composition is not None
    assert composition.as_map() == {
        "SiO2": as_decimal("0.5"),
        "MgO": as_decimal("0.5"),
    }
    assert omitted == ()


def test_b692_non_formula_omission_still_typed_partial() -> None:
    composition, omitted = _mole_fraction_composition_from_values(
        {
            "composition_mol": {
                "SiO2": 0.79,
                "Na2O": 0.07,
                "B2O3": 0.10,
                "Al2O3": 0.03,
                "minor constituents": 0.01,
            }
        }
    )
    assert composition is None
    assert "minor constituents" in omitted
