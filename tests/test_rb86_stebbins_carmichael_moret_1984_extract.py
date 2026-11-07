from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, SequenceNode, ScalarNode

from simulator.battery.enums import PolymorphRejectionKind, ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "rb86-stebbins-etal-1984"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"

RENDER_PINS = {
    "rb86_scm84_table3_feab_1": ("104233", 7, "3", 3, 2),
    "rb86_scm84_table4_li2sio3_liq_linear_fit": ("93.05", 8, "4", 5, 3),
    "rb86_scm84_table7_feo_liquid_partial_molar_cp": ("78.9", 10, "7", 6, 2),
    "rb86_scm84_table10_fe2sio4_tf_entropy_of_fusion": (
        "8.56",
        13,
        "10",
        2,
        4,
    ),
}

APPENDIX_RENDER_PINS = {
    "rb86_scm84_appendix1_mgsio3_heat_of_fusion_wagner_based_value": "77.4",
    "rb86_scm84_appendix1_mgsio3_heat_of_fusion_high_pressure_curve": "81.2",
    "rb86_scm84_appendix1_mgsio3_heat_of_fusion_authors_solid_data": "66.0",
}

NEPHELINE_TABLE10_FOOTNOTE = (
    'Table 10 footnote: “m” after Tf indicates a metastable, congruent melting point. '
    "Tf for NaAlSiO4 is for nepheline, all others are for the stable polymorph at the liquidus."
)

NEPHELINE_TABLE10_FOOTNOTE_LOCATOR = {
    "page": 13,
    "published_page": 143,
    "pdf_page_index": 12,
    "table": "10",
    "note": "Table 10 footnote below the table.",
}

UNRECOGNISED_NEPHELINE = {
    "tag": "unknown",
    "reason": "unrecognised polymorph 'nepheline' is not a closed Polymorph token",
    "rejection_kind": "unrecognised",
}

TYPED_REFUSAL_COUNTS = {
    "relative_enthalpy_increment_as_printed": 137,
    "glass_cp_fit_coefficients_context": 29,
    "constant_volume_heat_capacity_over_3R_context": 18,
    "glass_partial_molar_cp_coefficients_context": 9,
    "cp_temperature_dependent_equation_context": 2,
    "relative_enthalpy_fit_equation_context": 1,
    "heat_capacity_coefficient_per_oxide_mixture_gfw_as_printed": 17,
    "partial_molar_oxide_component_heat_capacity_in_silicate_liquid_as_printed": 12,
    "entropy_of_fusion_per_g_atom_as_printed": 47,
    "heat_of_fusion_with_literal_J_per_mol_unit_discrepancy": 3,
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _raw_observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for body in document["species"].values()
        for row in body.get("observations", [])
    }


def _migrated_observations() -> tuple[dict, Migrator]:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return (
        {
            row.observation_id.rsplit("::", 1)[-1]: row
            for row in migrator.result.observations.values()
        },
        migrator,
    )


def _assert_unique_yaml_mapping_keys(node: object, path: str = "$") -> None:
    if isinstance(node, MappingNode):
        keys: set[tuple[str, str]] = set()
        for key_node, value_node in node.value:
            key = (key_node.tag, key_node.value) if isinstance(key_node, ScalarNode) else (key_node.tag, "")
            assert key not in keys, f"duplicate YAML key at {path}: {key[1]}"
            keys.add(key)
            _assert_unique_yaml_mapping_keys(value_node, f"{path}.{key[1]}")
    elif isinstance(node, SequenceNode):
        for index, value_node in enumerate(node.value):
            _assert_unique_yaml_mapping_keys(value_node, f"{path}[{index}]")


def test_render_pins_match_values_and_locators_and_detect_mutation() -> None:
    text = EXTRACT.read_text(encoding="utf-8")
    _assert_unique_yaml_mapping_keys(yaml.compose(text))
    document = yaml.safe_load(text)
    raw = _raw_observations(document)
    samples = {sample["observation_id"]: sample for sample in document["fidelity_samples"]}

    assert check_all_fidelity_samples_match(document) == []
    for observation_id, (value, page, table, row, column) in RENDER_PINS.items():
        observation = raw[observation_id]
        assert observation["values"]["value"] == value
        assert observation["locator"]["page"] == page
        assert observation["locator"]["table"] == table
        assert observation["locator"]["row"] == row
        assert observation["locator"]["column"] == column
        assert samples[observation_id]["value"] == value
        assert samples[observation_id]["locator"] == {
            "page": page,
            "table": table,
            "row": row,
            "column": column,
        }

    for observation_id, value in APPENDIX_RENDER_PINS.items():
        observation = raw[observation_id]
        assert observation["values"]["value"] == value
        assert observation["locator"]["page"] == 16
        assert samples[observation_id]["value"] == value
        assert samples[observation_id]["locator"]["page"] == 16
        assert "Appendix 1" in samples[observation_id]["locator"]["note"]

    changed = deepcopy(document)
    raw_changed = _raw_observations(changed)
    raw_changed["rb86_scm84_table3_feab_1"]["values"]["value"] = "104234"
    assert check_all_fidelity_samples_match(changed)


def test_table_counts_glass_70_and_ab_an_di_composition_assignment() -> None:
    document = _document()
    raw = _raw_observations(document)
    counts = document["extraction"]["table_context"]["printed_row_counts"]
    assert counts == {
        "table_1_sample_compositions": 23,
        "table_2_glass_fit_rows": 29,
        "table_3_relative_enthalpy_points": 136,
        "table_4_linear_fit_rows": 17,
        "table_5_glass_component_rows": 9,
        "table_7_liquid_component_rows": 12,
        "table_8_fit_comparison_rows": 7,
        "table_9_liquid_Cv_over_3R_rows": 18,
        "table_10_species_rows": 17,
    }

    glass_70 = raw["rb86_scm84_table2_4_70"]
    printed_components = dict(
        glass_70["point_conditions"]["composition"]["state"]["value"]["components"]
    )
    assert printed_components == {
        "SiO2": "48.83",
        "TiO2": "8.51",
        "Al2O3": "8.29",
        "MgO": "34.34",
    }
    assert sum(map(Decimal, printed_components.values())) == Decimal("99.97")

    table1 = document["extraction"]["table_context"]["table_1_compositions"]
    table1_by_sample = {row["sample_as_printed"]: row for row in table1}
    inline_component_pins = {
        "$Li_2SiO_3$": {"SiO2": "49.33", "Li2O": "50.67"},
        "$Rb_2Si_2O_5$": {"SiO2": "71.42", "Rb2O": "28.58"},
        "$BaSi_2O_5$": {"SiO2": "66.64", "BaO": "32.35", "SrO": "1.01"},
    }
    for sample, expected in inline_component_pins.items():
        assert dict(table1_by_sample[sample]["composition_mol_percent_as_printed"]) == expected
    for observation_id, expected in (
        (
            "rb86_scm84_table4_li2sio3_liq_linear_fit",
            {"SiO2": "49.33", "Li2O": "50.67"},
        ),
        (
            "rb86_scm84_table4_rb2si2o5_linear_fit",
            {"SiO2": "71.42", "Rb2O": "28.58"},
        ),
        (
            "rb86_scm84_table4_basi2o5_linear_fit",
            {"SiO2": "66.64", "BaO": "32.35", "SrO": "1.01"},
        ),
    ):
        assert dict(
            raw[observation_id]["point_conditions"]["composition"]["state"]["value"]["components"]
        ) == expected
        assert dict(raw[observation_id]["values"]["composition_as_printed"]) == expected

    ternary_groups = {
        "ab75an0di25": (1, 7, "ab 75 an 0 di 25", "75", "0", "25"),
        "ab50an0di50": (1, 9, "ab 50 an 0 di 50", "50", "0", "50"),
        "ab0an35di65": (1, 11, "ab 0 an 35 di 65", "0", "35", "65"),
        "ab40an35di25": (19, 7, "ab 40 an 35 di 25", "40", "35", "25"),
        "ab40an20di40": (19, 9, "ab 40 an 20 di 40", "40", "20", "40"),
        "ab15an10di75": (19, 11, "ab 15 an 10 di 75", "15", "10", "75"),
        "ab20an60di20": (32, 7, "ab 20 an 60 di 20", "20", "60", "20"),
        "ab0an50di50": (32, 9, "ab 0 an 50 di 50", "0", "50", "50"),
        "ab25an0di75": (32, 11, "ab 25 an 0 di 75", "25", "0", "75"),
    }
    for series, (header_row, header_column, heading, ab, an, di) in ternary_groups.items():
        observations = [
            row
            for observation_id, row in raw.items()
            if observation_id.startswith(f"rb86_scm84_table3_{series}_")
        ]
        assert observations
        for observation in observations:
            assert observation["values"]["composition_as_printed"] == heading
            composition_locator = observation["point_conditions"]["composition"]["locator"]
            assert composition_locator["page"] == 7
            assert composition_locator["table"] == "3"
            assert composition_locator["row"] == header_row
            assert composition_locator["column"] == header_column
            derivation = observation["values"]["composition_derivation"]
            assert f"Ab {ab} mol %, An {an} mol %, Di {di} mol %" in derivation

    ternary_derived = {
        observation_id: row
        for observation_id, row in raw.items()
        if row.get("values", {}).get("composition_derivation")
        and row.get("point_conditions", {}).get("composition", {})
        .get("state", {})
        .get("value", {})
        .get("basis")
        == "derived oxide mole percent from Ab-An-Di endmember mol %"
    }
    assert len(ternary_derived) == 103
    for row in ternary_derived.values():
        inference = row["point_conditions"]["composition"]["inference"]
        assert "closed Ab-An-Di ternary" in inference["relation"]
        assert len(inference["inputs"]) == 9
        assert any(
            "Di " in item and ("closure" in item or "closes" in item)
            for item in inference["inputs"]
        )
        assert any("Ab NaAlSi3O8" in item for item in inference["inputs"])
        assert any("An CaAl2Si2O8" in item for item in inference["inputs"])
        assert any("Di CaMgSi2O6" in item for item in inference["inputs"])
        assert all("PDF page" in item for item in inference["inputs"])


def test_isolated_migration_has_only_typed_refusals_and_preserves_axes() -> None:
    document = _document()
    raw = _raw_observations(document)
    migrated, migrator = _migrated_observations()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(migrated) == 275
    assert sum(row.value.kind is ValueKind.POINT for row in migrated.values()) == 0
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 275

    expected_unknown = {
        observation_id
        for observation_id, observation in raw.items()
        if observation["values"].get("quantity") in TYPED_REFUSAL_COUNTS
    }
    actual_unknown = {
        observation_id
        for observation_id, observation in migrated.items()
        if observation.identity.quantity is None or observation.identity.quantity.is_unknown
    }
    assert actual_unknown == expected_unknown
    assert {
        quantity: sum(
            raw[observation_id]["values"].get("quantity") == quantity
            for observation_id in actual_unknown
        )
        for quantity in TYPED_REFUSAL_COUNTS
    } == TYPED_REFUSAL_COUNTS
    assert all(
        observation.identity.species.phase is not None
        and observation.identity.species.phase.is_value
        for observation in migrated.values()
    )

    fusion_ids = {
        observation_id
        for observation_id in raw
        if observation_id.startswith("rb86_scm84_table10_")
        or (
            observation_id.startswith("rb86_scm84_appendix1_")
            and "heat_of_fusion" in observation_id
        )
    }
    assert len(fusion_ids) == 50
    for observation_id in fusion_ids:
        assert raw[observation_id]["phase"] == "cr"
        assert migrated[observation_id].identity.species.phase.value.value == "cr"

    naalsio4_table_context = next(
        row
        for row in document["extraction"]["table_context"][
            "table_10_liquid_entropy_rows"
        ]
        if row["formula_as_printed"] == "NaAlSiO_4"
    )
    assert naalsio4_table_context["Tf_as_printed"] == "1750 m"

    for observation_id in (
        "rb86_scm84_table10_naalsio4_tf_entropy_of_fusion",
        "rb86_scm84_table10_naalsio4_1600k_entropy_of_fusion",
        "rb86_scm84_table10_naalsio4_tg_entropy_of_fusion",
    ):
        source_form = raw[observation_id]["condensed_form"]
        assert source_form["state"] == "crystalline"
        assert source_form["polymorph"] == UNRECOGNISED_NEPHELINE
        assert source_form["note"] == NEPHELINE_TABLE10_FOOTNOTE
        assert source_form["locator"] == NEPHELINE_TABLE10_FOOTNOTE_LOCATOR

        migrated_polymorph = migrated[observation_id].identity.species.polymorph
        assert migrated_polymorph.is_unknown
        assert migrated_polymorph.reason == UNRECOGNISED_NEPHELINE["reason"]
        assert migrated_polymorph.rejection_kind is PolymorphRejectionKind.UNRECOGNISED

    ternary_derived = {
        observation_id
        for observation_id, source in raw.items()
        if source.get("values", {}).get("composition_derivation")
        and source.get("point_conditions", {})
        .get("composition", {})
        .get("state", {})
        .get("value", {})
        .get("basis")
        == "derived oxide mole percent from Ab-An-Di endmember mol %"
    }
    assert len(ternary_derived) == 103
    for observation_id in ternary_derived:
        composition = migrated[observation_id].point_conditions["composition"]
        assert composition.inference is not None
        assert composition.inference.relation.startswith("Formula-unit oxide split")
        assert len(composition.inference.inputs) == 9

    for observation_id, source in raw.items():
        conditions = source["point_conditions"]
        assert set(conditions) >= {
            "temperature_K",
            "fO2_Pa",
            "total_pressure_Pa",
            "composition",
        }
        for axis in ("temperature_K", "fO2_Pa", "total_pressure_Pa", "composition"):
            condition = conditions[axis]
            assert condition["state"]["tag"] in {"value", "unknown"}
            assert condition["locator"]["page"]

        # This paper prints no total pressure or absolute oxygen fugacity.
        for axis in ("fO2_Pa", "total_pressure_Pa"):
            assert conditions[axis]["state"]["tag"] == "unknown"
            identity_state = getattr(migrated[observation_id].identity, axis)
            assert identity_state is None or not identity_state.is_value

    glass_rows = [
        row
        for observation_id, row in raw.items()
        if observation_id.startswith("rb86_scm84_table2_")
    ]
    assert len(glass_rows) == 29
    for observation in glass_rows:
        quench = observation["point_conditions"]["quench_state"]
        assert quench["state"]["value"] == "not_stated"
        assert quench["locator"]["page"] == 3

    glass_70_condition = migrated["rb86_scm84_table2_4_70"].point_conditions[
        "composition"
    ]
    assert glass_70_condition.state.is_unknown
    assert glass_70_condition.state.reason == (
        "mole-percent composition does not close to 100 (sum=99.97)"
    )

    ternary = migrated["rb86_scm84_table2_19_ab_12_5_an_12_5"]
    composition = ternary.point_conditions["composition"].state.value.as_map()
    assert composition == {
        "SiO2": Decimal("0.53125"),
        "Na2O": Decimal("0.015625"),
        "Al2O3": Decimal("0.046875"),
        "CaO": Decimal("0.21875"),
        "MgO": Decimal("0.1875"),
    }
    assert sum(composition.values()) == Decimal(1)

    table4_ids = {
        observation_id
        for observation_id, source in raw.items()
        if observation_id.startswith("rb86_scm84_table4_")
        and source.get("locator", {}).get("table") == "4"
        and source.get("values", {}).get("quantity")
        == "heat_capacity_coefficient_per_oxide_mixture_gfw_as_printed"
    }
    assert len(table4_ids) == 17
    for observation_id in table4_ids:
        source = raw[observation_id]
        assert source["units"] == "J gfw^-1 K^-1"
        assert "per" not in source
        assert source["values"]["status"] == "typed_refusal"
        assert source["values"]["units_as_printed"] == "J gfw^-1 K^-1"
        assert migrated[observation_id].identity.quantity is None or migrated[
            observation_id
        ].identity.quantity.is_unknown
        assert source["locator"]["column_header"] == "b"

    table7_ids = {
        observation_id
        for observation_id, source in raw.items()
        if observation_id.startswith("rb86_scm84_table7_")
        and source.get("locator", {}).get("table") == "7"
    }
    assert len(table7_ids) == 12
    for observation_id in table7_ids:
        source = raw[observation_id]
        assert source["values"]["status"] == "typed_refusal"
        assert source["values"]["quantity"] == (
            "partial_molar_oxide_component_heat_capacity_in_silicate_liquid_as_printed"
        )
        assert source["units"] == "J gfw^-1 K^-1"
        assert "per" not in source

    for observation_id, (fit_id, oxide_component) in {
        "rb86_scm84_table7_bao_liquid_partial_molar_cp": (
            "rb86_scm84_table4_basi2o5_linear_fit",
            "BaO",
        ),
        "rb86_scm84_table7_li2o_liquid_partial_molar_cp": (
            "rb86_scm84_table4_li2sio3_liq_linear_fit",
            "Li2O",
        ),
        "rb86_scm84_table7_rb2o_liquid_partial_molar_cp": (
            "rb86_scm84_table4_rb2si2o5_linear_fit",
            "Rb2O",
        ),
    }.items():
        source = raw[observation_id]
        expected_parents = [fit_id, "rb86_scm84_table7_sio2_liquid_partial_molar_cp"]
        assert source["derived_from"] == expected_parents
        assert source["derivation"]["inputs"] == expected_parents
        assert "derived from the linear regression" in source["values"]["source_method_note"]
        assert source["values"]["oxide_component_as_printed"] == oxide_component
        assert "fit_sample_count_as_printed" not in source["values"]
        assert "fit_method_as_printed" not in source["values"]
        assert "fit_statistics_as_printed" not in source["values"]
        assert "no separate uncertainty calculation" in source["uncertainty"]["note"]

    fayalite_migrated = migrated[
        "rb86_scm84_table10_fe2sio4_tf_entropy_of_fusion"
    ]
    assert fayalite_migrated.point_conditions["composition"].state.is_unknown
    assert fayalite_migrated.identity.composition.is_unknown
    assert fayalite_migrated.identity.temperature_K.is_value
    assert fayalite_migrated.identity.temperature_K.value == Decimal("1490")
    assert fayalite_migrated.identity.total_pressure_Pa is None
    assert fayalite_migrated.identity.fO2_Pa is None

    for observation_id, temperature in (
        ("rb86_scm84_table10_fe2sio4_tf_entropy_of_fusion", "1490"),
        ("rb86_scm84_table10_fe2sio4_1600k_entropy_of_fusion", "1600"),
        ("rb86_scm84_table10_mgsio3_tf_entropy_of_fusion", "1840"),
    ):
        identity = migrated[observation_id].identity
        assert identity.quantity is None or identity.quantity.is_unknown
        assert identity.composition.is_unknown
        assert identity.total_pressure_Pa is None
        assert identity.fO2_Pa is None
        assert identity.temperature_K.value == Decimal(temperature)

    mgsio3_fusion = migrated[
        "rb86_scm84_appendix1_mgsio3_heat_of_fusion_authors_solid_data"
    ]
    assert mgsio3_fusion.point_conditions["composition"].state.value.as_map() == {
        "MgO": Decimal("0.5"),
        "SiO2": Decimal("0.5"),
    }

    appendix_fusion_ids = {
        "rb86_scm84_appendix1_mgsio3_heat_of_fusion_wagner_based_value",
        "rb86_scm84_appendix1_mgsio3_heat_of_fusion_high_pressure_curve",
        "rb86_scm84_appendix1_mgsio3_heat_of_fusion_authors_solid_data",
    }
    for observation_id in appendix_fusion_ids:
        source = raw[observation_id]
        assert source["locator"]["page"] == 16
        assert source["locator"]["published_page"] == 146
        assert source["locator"]["pdf_page_index"] == 15
        assert source["values"]["quantity"] == (
            "heat_of_fusion_with_literal_J_per_mol_unit_discrepancy"
        )
        assert source["values"]["units_as_printed"] == "J mol^-1"
        assert source["values"]["status"] == "typed_refusal"

    fayalite = raw["rb86_scm84_table10_fe2sio4_tf_entropy_of_fusion"]
    assert fayalite["values"]["value_as_printed"] == "$8.56±0.11^1$"
    assert fayalite["values"]["quantity"] == "entropy_of_fusion_per_g_atom_as_printed"
    assert fayalite["values"]["units_as_printed"] == "J g-atom^-1 K^-1"
    assert fayalite["per"] == "mol_atom"
    assert fayalite["values"]["status"] == "typed_refusal"
    assert fayalite["uncertainty"]["value"] == "0.11"
    assert fayalite["uncertainty"]["unit"] == "J/mol/K"
    assert fayalite["uncertainty"]["confidence_level"] == "not_stated"
    assert fayalite["values"]["source_attribution_as_printed"] == (
        "Stebbins & Carmichael 1983 (fayalite)"
    )
    assert "FeAl2O4" not in document["species"]
