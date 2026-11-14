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
    _InvalidDecimalValue,
    _as_dec_or_none,
    _mole_fraction_composition_from_values,
    _parse_declared_decimal,
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


def test_mixed_map_decimal_predicate_keeps_nonnumeric_text_as_none() -> None:
    assert _as_dec_or_none(None) is None
    assert _as_dec_or_none("") is None
    assert _as_dec_or_none("not_reported_individually") is None
    assert _as_dec_or_none("AD") is None
    assert _as_dec_or_none("not-a-number") is None
    with pytest.raises(_InvalidDecimalValue, match="invalid numeric value"):
        _parse_declared_decimal("not-a-number")


def test_mixed_status_and_phase_labels_keep_numeric_observation(
    tmp_path: Path,
) -> None:
    extract = _scalar_extract(
        quantity="pure_Psat",
        units="atm",
        obs_type="psat_series",
        values={
            "quantity": "pure_Psat",
            "method_class": "measured_direct",
            "admission_status": "admitted",
            "series": [
                {
                    "sample": "AD",
                    "T_K": 1200,
                    "pressure_atm": "0.038",
                    "point_uncertainty_status": "not_reported_individually",
                }
            ],
        },
    )
    extract["species"]["Na"]["observations"][0]["phase"] = "l"
    result = migrate(_write_min_tree(tmp_path, extract), write=False)

    stored = [
        observation
        for observation in result.observations.values()
        if observation.observation_id.startswith("fixture-source::na_psat")
    ]
    assert len(stored) == 1
    assert stored[0].value.kind.value == "point"
    assert stored[0].value.point is not None
    assert stored[0].value.point > 0
    assert not any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and "not_reported_individually" in (issue.detail or "")
        for issue in result.validation.hard_issues
    )


def test_malformed_optional_decimal_becomes_invalid_source_issue(
    tmp_path: Path,
) -> None:
    values = {
        "quantity": "residue_component_composition",
        "value": "12",
        "method_class": "measured_direct",
        "admission_status": "admitted",
        "starting_measured_ppm": "not-a-number",
        "composition_wt_pct": {"SiO2": "50", "MgO": "50"},
    }
    root = _write_min_tree(
        tmp_path,
        _scalar_extract(
            quantity="residue_component_composition",
            units="wt%",
            obs_type="residue_component_composition",
            values=values,
        ),
    )

    result = migrate(root, write=False)
    hard = result.validation.hard_issues if result.validation is not None else ()
    matches = [
        issue
        for issue in hard
        if issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path is not None
        and "observation[" in issue.path
        and issue.detail is not None
        and "not-a-number" in issue.detail
    ]
    assert len(matches) == 1
    assert not any(
        observation.observation_id.startswith("fixture-source::")
        for observation in result.observations.values()
    )


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
    ("quantity", "units", "obs_type"),
    [
        ("p_partial", "Pa", "rate_series"),
        ("activity", "dimensionless", "activity_coefficient"),
        ("activity_coefficient", "dimensionless", "activity_coefficient"),
    ],
)
def test_b744_malformed_amount_takes_precedence_over_point_oxide_map(
    tmp_path: Path,
    quantity: str,
    units: str,
    obs_type: str,
) -> None:
    values = {
        "quantity": quantity,
        "method_class": "measured_direct",
        "admission_status": "admitted",
        "series": [
            {
                "T_K": 1933.0,
                "pressure_atm": "1.0e-8",
                **_MALFORMED,
                "composition_wt_pct": {"SiO2": "50", "MgO": "50"},
            }
        ],
    }
    extract = _scalar_extract(
        quantity=quantity, units=units, values=values, obs_type=obs_type
    )
    extract["species"]["Na"]["observations"][0]["observation_id"] = (
        f"b744_malformed_{quantity}"
    )
    root = _write_min_tree(tmp_path, extract)

    result = migrate(root, write=False)
    obs = next(
        item
        for item in result.observations.values()
        if item.observation_id.startswith(
            f"fixture-source::b744_malformed_{quantity}::"
        )
    )

    assert obs.identity.composition is not None
    assert obs.identity.composition.is_unknown
    assert obs.identity.composition.reason == (
        "malformed declared amount: 'not-a-number'"
    )
    hard = result.validation.hard_issues if result.validation is not None else ()
    matches = [
        issue
        for issue in hard
        if issue.reason is RefusalReason.INVALID_SOURCE
        and "composition_mol" in (issue.path or "")
        and issue.detail == "malformed declared amount: 'not-a-number'"
    ]
    assert len(matches) == 1


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
