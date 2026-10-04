"""b-692: pin silent drop of malformed composition_mol amounts.

PIN (never squash): generic ``composition_mol`` currently calls
``_as_dec_or_none`` and ``continue`` when an amount is unparseable, so a
malformed declared amount is dropped and the remaining oxides still form a
``Composition``. No ``ValidationIssue``. Captures tip behaviour before the
typed-hard-issue + single amount-parse owner change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simulator.battery.enums import RefusalReason
from simulator.battery.migrate import (
    _mole_fraction_composition_from_values,
    migrate,
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


def test_b692_pin_helper_silently_drops_malformed_declared_amount() -> None:
    """Malformed MgO is skipped; SiO2+CaO still map; omitted stays empty."""

    composition, omitted = _mole_fraction_composition_from_values(_MALFORMED)
    assert composition is not None
    assert composition.as_map() == {
        "SiO2": as_decimal("0.6"),
        "CaO": as_decimal("0.4"),
    }
    assert omitted == ()


def test_b692_pin_migrate_keeps_partial_composition_without_hard_issue(
    tmp_path: Path,
) -> None:
    """Full migrate: partial composition lands; no malformed-amount hard issue."""

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
    assert obs.identity.composition.is_value
    assert obs.identity.composition.value.as_map() == {
        "SiO2": as_decimal("0.6"),
        "CaO": as_decimal("0.4"),
    }
    hard = result.validation.hard_issues if result.validation is not None else ()
    assert not any("malformed declared amount" in (issue.detail or "") for issue in hard)
    assert not any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and "composition_mol" in (issue.path or "")
        for issue in hard
    )


@pytest.mark.parametrize(
    "bad_amount",
    ["not-a-number", True, None, "", {"nested": 1}, ["1"]],
)
def test_b692_pin_helper_drops_each_unparseable_shape(bad_amount: object) -> None:
    values = {
        "composition_mol": {
            "SiO2": 0.55,
            "MgO": bad_amount,
            "CaO": 0.45,
        }
    }
    composition, omitted = _mole_fraction_composition_from_values(values)
    assert composition is not None
    assert "MgO" not in composition.as_map()
    assert composition.as_map() == {
        "SiO2": as_decimal("0.55"),
        "CaO": as_decimal("0.45"),
    }
    assert omitted == ()
