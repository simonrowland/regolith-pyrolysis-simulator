from __future__ import annotations

import pytest

from simulator.feedstock_composition import (
    UNKNOWN_FERRIC_UPPER_BOUND_REASON,
    fe_metal,
    measured_fe2o3,
    measured_feo,
    resolve_feedstock_composition,
    split_known,
    total_fe,
    total_oxygen_bounds,
)
from simulator.fe_redox import feot_equivalent_wt_pct


def _measured_split() -> dict:
    return {
        "composition_wt_pct": {"SiO2": 70.0, "FeO": 2.0, "Fe2O3": 1.0},
        "composition_basis": {
            "FeO": {"method": "wet chemistry", "source": "synthetic assay"},
            "Fe2O3": {"method": "Mössbauer", "source": "synthetic assay"},
        },
        "elemental_composition_wt_pct": {"Fe": 0.25},
    }


def _unknown_split() -> dict:
    return {
        "composition_wt_pct": {"SiO2": 70.0, "FeO": 3.0},
        "fe_redox_split_unknown": True,
        "composition_basis": {
            "fe_reporting_convention": "total Fe as FeO",
        },
        "elemental_composition_wt_pct": {"Fe": 0.25},
    }


def test_resolver_exposes_canonical_map_provenance_and_explicit_iron_accessors() -> None:
    entry = _measured_split()
    resolved = resolve_feedstock_composition(entry)

    assert resolved.canonical_wt_pct == entry["composition_wt_pct"]
    assert resolved.provenance == entry["composition_basis"]
    assert resolved.fe_redox_split_unknown is False
    assert resolved.split_known is True
    assert resolved.total_fe_basis == (
        "FeO-equivalent wt% on the declared composition basis"
    )
    assert resolved.total_fe == feot_equivalent_wt_pct(entry["composition_wt_pct"])
    assert total_fe(entry["composition_wt_pct"]) == resolved.total_fe
    assert measured_feo(entry) == 2.0
    assert measured_fe2o3(entry) == 1.0
    assert fe_metal(entry) == 0.25
    assert split_known(entry) is True


def test_unknown_split_is_all_ferrous_with_typed_oxygen_upper_absence() -> None:
    entry = _unknown_split()
    resolved = resolve_feedstock_composition(entry)
    bounds = total_oxygen_bounds(entry)

    assert resolved.fe_redox_split_unknown is True
    assert resolved.total_fe == 3.0
    assert resolved.measured_feo is None
    assert resolved.measured_fe2o3 is None
    assert measured_feo(entry) is None
    assert measured_fe2o3(entry) is None
    assert fe_metal(entry) == 0.25
    assert split_known(entry) is False
    assert bounds.lower.value_wt_pct is not None
    assert bounds.lower.value_wt_pct > 0.0
    assert bounds.upper.value_wt_pct is None
    assert bounds.upper.refused_reason == UNKNOWN_FERRIC_UPPER_BOUND_REASON


@pytest.mark.parametrize(
    "entry, message",
    [
        (
            {
                **_unknown_split(),
                "composition_wt_pct": {"FeO": 2.0, "Fe2O3": 1.0},
            },
            "cannot be combined with Fe2O3",
        ),
        (
            {
                "composition_wt_pct": {"SiO2": 70.0},
                "fe_redox_split_unknown": True,
                "composition_basis": {
                    "fe_reporting_convention": "total Fe as FeO",
                },
            },
            "requires FeO",
        ),
        (
            {
                "composition_wt_pct": {"FeO": 3.0},
                "fe_redox_split_unknown": True,
                "composition_basis": {},
            },
            "fe_reporting_convention",
        ),
        (
            {
                **_measured_split(),
                "composition_basis": {
                    "FeO": {"method": "wet chemistry"},
                    "Fe2O3": {"method": "Mössbauer", "source": "synthetic assay"},
                },
            },
            "FeO.method and source",
        ),
        (
            {
                **_measured_split(),
                "composition_wt_pct": {"Fe2O3": 1.0},
            },
            "requires both FeO and Fe2O3",
        ),
        (
            {
                "composition_wt_pct": {"Fe": 1.0},
            },
            "metallic iron must be outside",
        ),
        (
            {
                "composition_wt_pct": {"FeO": 3.0},
                "fe_redox_split_unknown": "true",
                "composition_basis": {
                    "fe_reporting_convention": "total Fe as FeO",
                },
            },
            "must be a boolean",
        ),
    ],
)
def test_resolver_rejects_invalid_iron_representations(
    entry: dict, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_feedstock_composition(entry)
