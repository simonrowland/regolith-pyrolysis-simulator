from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.ceramic_classifier import (
    DEFAULT_CERAMIC_TYPES_PATH,
    classify_ceramic_rump,
    classify_industrial_glass,
    load_ceramic_types,
    load_glass_types,
)
from simulator.terminal_product_taxonomy import DEFAULT_TAXONOMY_PATH


CERAMIC_LEAF_WITNESSES = {
    "monocalcium_aluminate_CA": {"CaO": 35.0, "Al2O3": 65.0},
    "calcium_dialuminate_CA2": {"CaO": 22.0, "Al2O3": 78.0},
    "calcium_hexaluminate_CA6": {"CaO": 8.0, "Al2O3": 92.0},
    "tricalcium_aluminate_C3A": {"CaO": 62.3, "Al2O3": 37.7},
    "mayenite_C12A7": {"CaO": 48.5, "Al2O3": 51.5},
    "mullite": {"Al2O3": 73.0, "SiO2": 27.0},
    "anorthite": {"CaO": 20.0, "Al2O3": 37.0, "SiO2": 43.0},
    "cordierite_mullite": {"MgO": 6.0, "Al2O3": 40.0, "SiO2": 54.0},
    "sillimanite_group": {"Al2O3": 62.9, "SiO2": 37.1},
    "cordierite_pure": {"MgO": 13.8, "Al2O3": 34.9, "SiO2": 51.4},
    "doloma": {"CaO": 58.2, "MgO": 41.8},
    "magnesium_aluminate_spinel": {"MgO": 28.3, "Al2O3": 71.7},
    "forsterite": {"MgO": 57.3, "SiO2": 42.7},
    "periclase_mgo": {"MgO": 100.0},
    "enstatite": {"MgO": 40.2, "SiO2": 59.8},
    "wollastonite": {"CaO": 48.0, "SiO2": 52.0},
    "diopside": {"CaO": 25.9, "MgO": 18.6, "SiO2": 55.5},
    "akermanite_melilite": {
        "CaO": 41.0,
        "MgO": 10.0,
        "Al2O3": 20.0,
        "SiO2": 29.0,
    },
    "merwinite": {"CaO": 51.2, "MgO": 12.3, "SiO2": 36.6},
    "monticellite": {"CaO": 35.8, "MgO": 25.8, "SiO2": 38.4},
    "dicalcium_silicate_C2S": {"CaO": 65.0, "SiO2": 35.0},
    "tricalcium_silicate_C3S": {"CaO": 74.0, "SiO2": 26.0},
    "perovskite_catito3": {"CaO": 41.3, "TiO2": 58.7},
    "ree_aluminate_silicate_family": {
        "La2O3": 20.0,
        "Al2O3": 40.0,
        "SiO2": 40.0,
    },
    "cr_spinel_chromite": {"Cr2O3": 70.0, "MgO": 30.0},
    "ree_titanate_pyrochlore_family": {"La2O3": 20.0, "TiO2": 80.0},
    "cmas_glass_ceramic": {
        "CaO": 35.0,
        "MgO": 6.3,
        "Al2O3": 21.2,
        "SiO2": 37.5,
    },
    "gehlenite_anorthite_path": {
        "CaO": 30.0,
        "MgO": 5.0,
        "Al2O3": 35.0,
        "SiO2": 30.0,
    },
}


def _write_canonical_hierarchy(
    path: Path,
    entries: dict[str, dict],
) -> None:
    normalized_entries = {}
    for entry_id, source in entries.items():
        entry = dict(source)
        parent = entry.get("parent")
        normalized_entries[entry_id] = {
            "canonical_node_id": None,
            "parent": parent,
            "level": "parent" if parent is None else "subtype",
            "label": entry["label"],
            "composition": entry["composition"],
            "service_temp": entry.get(
                "service_temp",
                {
                    "value_C": None,
                    "kind": "uncharacterized",
                    "citations": [],
                    "note": "",
                },
            ),
            "liner_suitability": entry.get(
                "liner_suitability",
                {"verdict": "not-assessed", "citations": [], "note": ""},
            ),
            "strength": {
                "status": "sourced_qualitative_text",
                "text": "Fixture qualitative strength text.",
                "source_ids": ["fixture_source"],
            },
            "datasheet": {},
        }
    path.write_text(
        yaml.safe_dump(
            {
                "version": "fixture",
                "taxonomy_name": "terminal_product_taxonomy",
                "product_classes": {
                    "oxide_ceramic": {"label": "Oxide ceramic"},
                },
                "match_policy": {"policy_id": "fixture"},
                "ceramic_hierarchy": {
                    "schema_version": 1,
                    "policy_id": "fixture",
                    "source_ids": ["fixture_source"],
                    "ignored_identity_oxides": [],
                    "analytical_tolerance_wt_pct": 0.5,
                    "entries": normalized_entries,
                },
                "nodes": [
                    {
                        "id": "fixture_node",
                        "product_class": "oxide_ceramic",
                        "label": "Fixture node",
                        "match": {"oxide_only_match_allowed": False},
                        "properties": {},
                        "evidence_tier": "C",
                        "sources": ["fixture_source"],
                    }
                ],
                "sources": {
                    "fixture_source": {
                        "title": "Fixture source",
                        "path": "fixture",
                    }
                },
            },
            sort_keys=False,
        )
    )


def test_ceramic_loader_delegates_to_the_only_canonical_taxonomy() -> None:
    assert DEFAULT_CERAMIC_TYPES_PATH == DEFAULT_TAXONOMY_PATH
    assert not DEFAULT_TAXONOMY_PATH.with_name("ceramic_types.yaml").exists()
    assert len(load_ceramic_types()["ceramics"]) == 35


def test_ceramic_loader_projects_canonical_strength_without_new_authority() -> None:
    canonical_entries = yaml.safe_load(DEFAULT_TAXONOMY_PATH.read_text())[
        "ceramic_hierarchy"
    ]["entries"]
    projected_entries = load_ceramic_types()["ceramics"]

    assert projected_entries.keys() == canonical_entries.keys()
    for ceramic_id, canonical in canonical_entries.items():
        projected = projected_entries[ceramic_id]
        assert "mechanical_properties" not in canonical["datasheet"]
        assert projected["datasheet"] == {
            **canonical["datasheet"],
            "mechanical_properties": canonical["strength"]["text"],
        }
        assert {
            key: value
            for key, value in projected.items()
            if key != "datasheet"
        } == {
            key: value
            for key, value in canonical.items()
            if key != "datasheet"
        }


def test_forsterite_point_anchor_classifies_with_explicit_tolerance():
    result = classify_ceramic_rump(
        {"MgO": 57.3, "SiO2": 42.7},
        tolerance_wt_pct=0.1,
    )

    assert result.status == "match"
    assert result.match is not None
    assert result.match.ceramic_id == "forsterite"
    assert result.match.composition_kind == "point-anchor"
    assert result.match.parent_id == "basic_mgo_refractory"
    assert result.match.match_level == "subtype"
    assert result.match.hierarchy == ("basic_mgo_refractory", "forsterite")
    assert result.match.datasheet["everyday_analog"]


def test_parent_fallback_when_no_calcium_aluminate_subtype_matches():
    result = classify_ceramic_rump(
        {"CaO": 30.0, "Al2O3": 70.0},
        tolerance_wt_pct=0.1,
    )

    assert result.status == "match"
    assert result.match is not None
    assert result.match.ceramic_id == "calcium_aluminate_refractory"
    assert result.match.match_level == "parent"
    assert "no subtype predicate matched" in result.reason


@pytest.mark.parametrize(
    ("ceramic_id", "composition"),
    CERAMIC_LEAF_WITNESSES.items(),
)
def test_every_catalog_ceramic_leaf_is_reachable(ceramic_id, composition):
    result = classify_ceramic_rump(composition, tolerance_wt_pct=0.1)

    assert result.status == "match", result.reason
    assert result.match is not None
    assert result.match.ceramic_id == ceramic_id
    assert result.match.match_level == "subtype"


def test_catalog_ceramic_leaf_reachability_sweep_is_complete():
    catalog = load_ceramic_types()["ceramics"]
    catalog_leaves = {
        ceramic_id
        for ceramic_id, entry in catalog.items()
        if entry.get("parent") is not None
    }

    assert set(CERAMIC_LEAF_WITNESSES) == catalog_leaves


def test_off_window_composition_returns_no_match():
    result = classify_ceramic_rump(
        {"Na2O": 100.0},
        tolerance_wt_pct=0.5,
    )

    assert result.status == "no-match"
    assert result.match is None


def test_broad_extra_oxide_composition_is_ambiguous_between_parents():
    result = classify_ceramic_rump(
        {"MgO": 57.3, "SiO2": 40.7, "Al2O3": 2.0},
        tolerance_wt_pct=0.5,
    )

    assert result.status == "ambiguous"
    assert result.match is None
    assert "parent matches" in result.reason


def test_doloma_product_window_accepts_source_allowed_impurity_remainder():
    result = classify_ceramic_rump(
        {"CaO": 42.0, "MgO": 32.0, "SiO2": 26.0},
        tolerance_wt_pct=0.5,
    )

    assert result.status == "match"
    assert result.match is not None
    assert result.match.ceramic_id == "doloma"


def test_stoichiometric_dolime_anchor_selects_doloma_not_silicate_parent():
    result = classify_ceramic_rump(
        {"CaO": 58.2, "MgO": 41.8},
        tolerance_wt_pct=0.1,
    )

    assert result.status == "match"
    assert result.match is not None
    assert result.match.ceramic_id == "doloma"


def test_extra_magnesia_falls_back_from_mullite_to_parent():
    result = classify_ceramic_rump(
        {"Al2O3": 72.0, "SiO2": 27.0, "MgO": 20.0},
        tolerance_wt_pct=0.5,
    )

    assert result.status == "match"
    assert result.match is not None
    assert result.match.ceramic_id == "aluminosilicate_ceramic"
    assert result.match.match_level == "parent"


def test_overlapping_source_windows_return_ambiguous(tmp_path):
    data_path = tmp_path / "ceramics_taxonomy.yaml"
    _write_canonical_hierarchy(
        data_path,
        {
            "alpha_window": {
                "label": "Alpha window",
                "composition": {
                    "kind": "window",
                    "defining_oxides": ["CaO", "Al2O3"],
                    "wt_pct_window": {
                        "CaO": [20.0, 30.0],
                        "Al2O3": [70.0, 80.0],
                    },
                },
            },
            "beta_anchor": {
                "label": "Beta anchor",
                "composition": {
                    "kind": "point-anchor",
                    "defining_oxides": ["CaO", "Al2O3"],
                    "wt_pct": {"CaO": 25.0, "Al2O3": 75.0},
                },
            },
        },
    )

    result = classify_ceramic_rump(
        {"CaO": 25.0, "Al2O3": 75.0},
        data_path=data_path,
    )

    assert result.status == "ambiguous"
    assert result.match is None
    assert "alpha_window" in result.reason
    assert "beta_anchor" in result.reason


def test_equal_specificity_sibling_collision_falls_back_to_parent(tmp_path):
    data_path = tmp_path / "ceramics_taxonomy.yaml"
    _write_canonical_hierarchy(
        data_path,
        {
            "parent": {
                "label": "Parent",
                "composition": {
                    "kind": "window",
                    "defining_oxides": ["CaO", "Al2O3"],
                    "wt_pct_window": {"CaO": [20, 30], "Al2O3": [70, 80]},
                },
                "service_temp": {
                    "value_C": None,
                    "kind": "uncharacterized",
                },
                "liner_suitability": {},
            },
            "alpha": {
                "parent": "parent",
                "label": "Alpha",
                "composition": {
                    "kind": "window",
                    "defining_oxides": ["CaO", "Al2O3"],
                    "wt_pct_window": {"CaO": [24, 26], "Al2O3": [74, 76]},
                },
            },
            "beta": {
                "parent": "parent",
                "label": "Beta",
                "composition": {
                    "kind": "window",
                    "defining_oxides": ["CaO", "Al2O3"],
                    "wt_pct_window": {"CaO": [24, 26], "Al2O3": [74, 76]},
                },
            },
        },
    )

    result = classify_ceramic_rump(
        {"CaO": 25.0, "Al2O3": 75.0},
        data_path=data_path,
    )

    assert result.status == "match"
    assert result.match is not None
    assert result.match.ceramic_id == "parent"
    assert result.match.match_level == "parent"
    assert "subtype predicates tied" in result.reason


def test_melting_only_service_temp_is_not_usable_service_rating():
    result = classify_ceramic_rump(
        {"MgO": 57.3, "SiO2": 42.7},
        tolerance_wt_pct=0.1,
    )

    assert result.match is not None
    assert result.match.service_temp.kind == "melting-only"
    assert result.match.service_temp.value_C == 1890
    assert result.match.service_temp.usable_service_C is None


def test_industrial_glass_selects_container_subtype_and_low_iron_clarity():
    result = classify_industrial_glass(
        {"SiO2": 72.0, "Na2O": 12.0, "CaO": 12.0, "Al2O3": 4.0},
        tolerance_wt_pct=0.1,
    )

    assert result.status == "match"
    assert result.match is not None
    assert result.match.family_id == "container_sls_analog"
    assert result.match.parent_id == "soda_lime_analog"
    assert result.clarity_grade == "optical_clear"
    assert result.colour_estimate == "colourless"
    assert "container" in result.use_grade_optical
    assert len(result.match.datasheet) == 11


@pytest.mark.parametrize(
    ("family_id", "composition"),
    [
        (
            "container_sls_analog",
            {"SiO2": 72.0, "Na2O": 12.0, "CaO": 12.0, "Al2O3": 4.0},
        ),
        (
            "float_sls_analog",
            {
                "SiO2": 72.0,
                "Na2O": 13.0,
                "CaO": 9.5,
                "MgO": 4.0,
                "Al2O3": 1.5,
            },
        ),
    ],
)
def test_every_catalog_glass_leaf_is_reachable(family_id, composition):
    result = classify_industrial_glass(composition, tolerance_wt_pct=0.1)

    assert result.status == "match", result.reason
    assert result.match is not None
    assert result.match.family_id == family_id
    assert result.match.match_level == "subtype"


def test_catalog_glass_leaf_reachability_sweep_is_complete():
    catalog = load_glass_types()["glass_types"]
    catalog_leaves = {
        family_id
        for family_id, entry in catalog.items()
        if entry.get("parent") is not None
    }

    assert catalog_leaves == {"container_sls_analog", "float_sls_analog"}


def test_industrial_glass_fe_content_and_speciation_estimate_clarity_and_colour():
    result = classify_industrial_glass(
        {
            "SiO2": 49.0,
            "Al2O3": 15.0,
            "CaO": 12.0,
            "MgO": 10.0,
            "FeO": 10.0,
            "TiO2": 4.0,
        },
        tolerance_wt_pct=0.1,
    )

    assert result.status == "match"
    assert result.match is not None
    assert result.match.family_id == "basalt_high_fe_glass"
    assert result.total_fe2o3_wt_pct == 11.113
    assert result.fe2_fraction == 1.0
    assert result.redox_source == "ledger_speciation"
    assert result.clarity_grade == "opaque_dark"
    assert result.colour_estimate == "dark_brown_black"


def test_industrial_glass_mixed_iron_speciation_uses_molar_iron_atoms():
    result = classify_industrial_glass(
        {
            "SiO2": 70.0,
            "Na2O": 12.0,
            "CaO": 12.0,
            "Al2O3": 4.5,
            "FeO": 0.5,
            "Fe2O3": 1.0,
        },
        tolerance_wt_pct=0.1,
    )

    assert result.fe2_fraction == pytest.approx(0.3572, abs=1e-4)
    assert result.redox_source == "ledger_speciation"
    assert result.colour_estimate == "green"


def test_industrial_glass_fe2o3_only_is_oxidized_ledger_speciation():
    result = classify_industrial_glass(
        {
            "SiO2": 71.9,
            "Na2O": 12.0,
            "CaO": 12.0,
            "Al2O3": 4.0,
            "Fe2O3": 0.1,
        }
    )

    assert result.status == "match"
    assert result.fe2_fraction == 0.0
    assert result.redox_source == "ledger_speciation"
    assert result.colour_estimate == "yellow"


def test_industrial_glass_ledger_speciation_overrides_po2_inputs():
    composition = {
        "SiO2": 71.9,
        "Na2O": 12.0,
        "CaO": 12.0,
        "Al2O3": 4.0,
        "Fe2O3": 0.1,
    }
    reduced = classify_industrial_glass(
        composition,
        pO2_mbar=1e-9,
        temperature_C=1400,
        pressure_mbar=100,
    )
    oxidized = classify_industrial_glass(
        composition,
        pO2_mbar=100,
        temperature_C=1400,
        pressure_mbar=100,
    )

    assert reduced.redox_source == oxidized.redox_source == "ledger_speciation"
    assert reduced.fe2_fraction == oxidized.fe2_fraction == 0.0
    assert reduced.colour_estimate == "yellow"
    assert oxidized.colour_estimate == "yellow"
    assert reduced.clarity_grade == oxidized.clarity_grade == "standard_clear_tinted"
