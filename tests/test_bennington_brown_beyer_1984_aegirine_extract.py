from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/bennington-brown-beyer-1984-aegirine-ri8912.yaml"
)
NATURAL = "Aegirine natural sample"


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def _assert_render_pins(document: dict) -> None:
    observations = _observations(document)
    point_count = sum(
        len(row["values"]["points"]) for row in observations.values()
    )
    assert point_count == 616
    assert len(observations["aegirine_table5_low_temperature_cp"]["values"]["points"]) == 52
    assert len(observations["aegirine_table6_smoothed_cp"]["values"]["points"]) == 54
    assert len(observations["aegirine_table7_high_temperature_cp"]["values"]["points"]) == 18
    for column in (
        "cp",
        "S",
        "negative_gibbs_function_over_T_from_H298",
        "H_minus_H298",
        "delta_fH",
        "delta_fG",
        "log10_Kf",
    ):
        assert len(observations[f"aegirine_table8_{column}"]["values"]["points"]) == 26
    for column in (
        "cp",
        "S",
        "negative_gibbs_function_over_T_from_H298",
        "H_minus_H298",
        "delta_fH_from_oxides",
        "delta_fG_from_oxides",
        "log10_Kf",
    ):
        assert len(observations[f"aegirine_table9_{column}"]["values"]["points"]) == 15

    low_first = observations["aegirine_table5_low_temperature_cp"]["values"]["points"][0]
    assert low_first["T_K"] == "19.10"
    assert low_first["value"] == "0.989"
    assert low_first["locator"] == {
        "page": 14,
        "published_page": 9,
        "row": 3,
        "table": "5",
        "column": 2,
    }

    table6 = observations["aegirine_table6_negative_gibbs_function_from_H0_over_T"]
    table6_at_298 = next(p for p in table6["values"]["points"] if p["T_K"] == "298.15")
    assert table6_at_298["value"] == "16.735"
    assert table6_at_298["locator"] == {
        "page": 16,
        "published_page": 11,
        "row": 34,
        "table": "6",
        "column": 4,
    }
    table6_enthalpy = observations["aegirine_table6_enthalpy_increment_from_H0"]
    enthalpy_at_298 = next(
        p for p in table6_enthalpy["values"]["points"] if p["T_K"] == "298.15"
    )
    assert enthalpy_at_298["value"] == "6.350"
    assert enthalpy_at_298["locator"]["page"] == 16
    assert enthalpy_at_298["locator"]["table"] == "6"
    assert enthalpy_at_298["locator"]["row"] == 34
    assert enthalpy_at_298["locator"]["column"] == 5

    table3 = observations["aegirine_table3_reaction_10"]["values"]["points"][0]
    assert table3["value"] == "-105.263"
    assert table3["locator"]["page"] == 11
    assert table3["locator"]["table"] == "3"
    assert table3["locator"]["row"] == 11
    assert table3["locator"]["column"] == 3
    reaction10 = observations["aegirine_table3_reaction_10"]
    for equation in (
        table3["reaction_as_printed"],
        reaction10["values"]["reaction_as_printed"],
    ):
        assert "Fe^{3+}_{0.7362}Fe^{2+}_{0.16}" in equation

    reaction11 = observations["aegirine_table3_overall_reaction_11"]
    reaction11_point = reaction11["values"]["points"][0]
    for equation in (
        reaction11_point["reaction_as_printed"],
        reaction11["values"]["reaction_as_printed"],
    ):
        product_side = equation.split("->", maxsplit=1)[1]
        assert "0.806H2SO4·7H2O(l)" in product_side
        assert "0.806H2SO4·7H2O(l)" not in equation.split("->", maxsplit=1)[0]

    elemental = observations["aegirine_table8_delta_fH"]["values"]["points"][0]
    assert elemental["T_K"] == "298.15"
    assert elemental["value"] == "-641.120"
    assert elemental["locator"]["page"] == 17
    assert elemental["locator"]["table"] == "8"
    assert elemental["locator"]["row"] == 3
    assert elemental["locator"]["column"] == 6

    oxide = observations["aegirine_table9_delta_fH_from_oxides"]["values"]["points"][0]
    assert oxide["value"] == "-37.136"
    assert oxide["locator"]["page"] == 18
    assert oxide["locator"]["table"] == "9"
    assert oxide["locator"]["row"] == 3
    assert oxide["locator"]["column"] == 6

    for observation_id, parent in (
        ("aegirine_table8_delta_fH", "aegirine_table4_reaction_24_formation_from_elements"),
        ("aegirine_table9_delta_fH_from_oxides", "aegirine_table4_reaction_25_formation_from_oxides"),
    ):
        derived = observations[observation_id]
        assert derived["derived_from"] == ["aegirine_table6_smoothed_cp", parent]
        assert derived["derivation"]["inputs"] == derived["derived_from"]
        assert derived["derivation"]["references"] == [
            "Pankratz, L. B. (1982), Thermodynamic Properties of Elements and Oxides, BuMines B 672, reference 19."
        ]
        assert derived["derivation"]["method"]["locator"] == {
            "page": 15,
            "published_page": 10,
            "section": "Enthalpies and Gibbs Energies of Formation and Reaction",
        }
        assert "raw_inputs" not in derived["derivation"]

    method_rows = [
        row
        for observation_id, row in observations.items()
        if observation_id.startswith(("aegirine_table8_", "aegirine_table9_"))
    ]
    assert len(method_rows) == 14
    assert all(
        row["derivation"]["method"]["locator"]["page"] == 15
        and row["derivation"]["method"]["locator"]["published_page"] == 10
        for row in method_rows
    )

    abstract_s = observations["aegirine_abstract_standard_entropy"]
    assert abstract_s["values"]["points"][0]["value"] == "38.03"
    assert abstract_s["uncertainty"]["value"] == ".14"
    assert abstract_s["uncertainty"]["verbatim"] == "±.14 cal/mol/K"
    assert abstract_s["locator"]["page"] == 6
    assert abstract_s["locator"]["published_page"] == 1
    assert "table" not in abstract_s["locator"]
    assert abstract_s["values"]["points"][0]["locator"]["page"] == 6
    assert abstract_s["derivation"]["raw_inputs"]["locator"]["page"] == 15
    assert abstract_s["derivation"]["raw_inputs"]["locator"]["published_page"] == 10

    abstract_rows = [
        row for observation_id, row in observations.items()
        if observation_id.startswith("aegirine_abstract_")
    ]
    assert len(abstract_rows) == 4
    assert all(row["locator"]["page"] == 6 and row["locator"]["published_page"] == 1 for row in abstract_rows)
    assert all(point["locator"]["page"] == 6 for row in abstract_rows for point in row["values"]["points"])

    table6_fidelity = next(
        sample for sample in document["fidelity_samples"]
        if "aegirine_table6_smoothed_cp" in sample["path"]
    )
    assert table6_fidelity["value"] == "40.499"
    assert table6_fidelity["locator"]["row"] == 34

    adjusted_solution = observations["aegirine_pure_endmember_adjusted_heat_of_solution"]
    adjusted_solution_point = adjusted_solution["values"]["points"][0]
    assert adjusted_solution_point["value"] == "-99.368"
    assert adjusted_solution_point["uncertainty"]["value"] == "0.200"
    assert adjusted_solution_point["uncertainty"]["verbatim"] == "±0.200 kcal/mol"
    assert adjusted_solution_point["locator"]["page"] == 18
    assert adjusted_solution_point["locator"]["published_page"] == 13
    assert adjusted_solution["derivation"]["inputs"] == [
        "aegirine_table3_reaction_10",
        "aegirine_table1_sample_characterization",
    ]

    adjusted_reaction = observations["aegirine_pure_endmember_overall_calorimetric_reaction"]
    adjusted_reaction_point = adjusted_reaction["values"]["points"][0]
    assert adjusted_reaction_point["value"] == "+13.214"
    assert adjusted_reaction_point["uncertainty"]["value"] == "0.206"
    assert adjusted_reaction_point["uncertainty"]["verbatim"] == "±0.206 kcal"
    assert adjusted_reaction_point["point_conditions"]["temperature_K"]["state"] == {
        "tag": "unknown",
        "reason": "not_single_temperature",
    }
    assert adjusted_reaction["derivation"]["inputs"] == [
        "aegirine_pure_endmember_adjusted_heat_of_solution",
        "aegirine_table3_reaction_1",
        "aegirine_table3_reaction_3",
        "aegirine_table3_reaction_8",
    ]

    pure = observations["aegirine_discussion_pure_endmember_delta_fH"]
    pure_point = pure["values"]["points"][0]
    assert pure_point["value"] == "-615.649"
    assert pure_point["uncertainty"]["value"] == "0.737"
    assert pure_point["uncertainty"]["verbatim"] == "±0.737 kcal/mol"
    assert pure_point["locator"]["page"] == 19
    assert pure_point["locator"]["published_page"] == 14
    expected_pure_lineage = [
        "aegirine_pure_endmember_overall_calorimetric_reaction",
        "aegirine_table4_reaction_21_constituent_delta_fH",
        "aegirine_table4_reaction_20_constituent_delta_fH",
        "aegirine_table4_reaction_12_constituent_delta_fH",
        "aegirine_table4_reaction_15_constituent_delta_fH",
        "aegirine_table4_reaction_23_constituent_delta_fH",
    ]
    assert pure["derived_from"] == expected_pure_lineage
    assert pure["derivation"]["inputs"] == expected_pure_lineage
    assert pure["derivation"]["references"] == [
        "Pankratz (19): reactions 12 (SiO2), 15 (Fe2O3), 20 (H2O(l)), and 21 (Na2SO4-III).",
        "Wagman (21): reaction 23 (H2SO4·7H2O(l)).",
    ]
    assert "raw_inputs" not in pure["derivation"]
    assert pure["derivation"]["method"]["locator"] == {
        "page": 19,
        "published_page": 14,
        "section": "Discussion",
    }

    expected_marked_temperatures = {
        "aegirine_table8_delta_fH": [
            ("1", "371.00"),
            ("2", "720.00"),
            ("3", "933.61"),
            ("4", "980.00"),
            ("5", "1043.00"),
            ("6", "1112.00"),
            ("7", "1177.00"),
            ("8", "1185.00"),
        ],
    }
    for observation_id, expected in expected_marked_temperatures.items():
        points = observations[observation_id]["values"]["points"]
        marked = [
            (point["transition_marker"], point["T_K"])
            for point in points
            if "transition_marker" in point
        ]
        assert marked == expected

    table9_transition_meanings = {
        "1": ("847.00", "α–β transition point for SiO2"),
        "2": ("960.00", "Transition point for Fe2O3"),
    }
    table9_ids = (
        "cp",
        "S",
        "negative_gibbs_function_over_T_from_H298",
        "enthalpy_increment_from_H298",
        "delta_fH_from_oxides",
        "delta_fG_from_oxides",
        "log10_Kf",
    )
    table9_annotations = []
    for column in table9_ids:
        observation_id = f"aegirine_table9_{column}"
        points = observations[observation_id]["values"]["points"]
        marked = [point for point in points if "transition_marker" in point]
        assert len(marked) == 2
        assert all(point["locator"]["page"] == 18 for point in marked)
        assert all(point["locator"]["table"] == "9" for point in marked)
        table9_annotations.extend(
            (
                point["transition_marker"],
                point["T_K"],
                point["transition_as_footnoted"],
            )
            for point in marked
        )
    assert table9_annotations == [
        (marker, temperature, meaning)
        for _ in table9_ids
        for marker, (temperature, meaning) in table9_transition_meanings.items()
    ]

    sample = next(
        row["values"]
        for row in document["species"][NATURAL]["context"]
        if row["observation_id"] == "aegirine_table1_sample_characterization"
    )
    assert len(sample["analysis_wt_percent"]) == 14
    assert sample["analysis_wt_percent"]["SiO2"] == "51.10"
    assert sample["analysis_wt_percent"]["Fe2O3"] == "24.21"
    assert sample["analysis_wt_percent"]["FeO"] == "4.94"
    assert sample["analysis_wt_percent"]["Na2O"] == "11.59"
    assert sample["iron_ions_per_adjusted_formula"] == {"Fe3+": "0.7362", "Fe2+": "0.160"}


def test_render_pins_and_row_count_reject_mutations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)

    changed_value = deepcopy(document)
    changed_value["species"][NATURAL]["observations"][0]["values"]["points"][0][
        "value"
    ] = "-105.262"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_value)

    changed_count = deepcopy(document)
    changed_low = next(
        row
        for row in changed_count["species"][NATURAL]["observations"]
        if row["observation_id"] == "aegirine_table5_low_temperature_cp"
    )
    changed_low["values"]["points"].pop()
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_count)

    changed_pure_value = deepcopy(document)
    pure_row = next(
        row
        for row in changed_pure_value["species"]["NaFeSi2O6"]["observations"]
        if row["observation_id"] == "aegirine_discussion_pure_endmember_delta_fH"
    )
    pure_row["values"]["points"][0]["value"] = "-999.999"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_pure_value)

    changed_pure_uncertainty = deepcopy(document)
    pure_row = next(
        row
        for row in changed_pure_uncertainty["species"]["NaFeSi2O6"]["observations"]
        if row["observation_id"] == "aegirine_discussion_pure_endmember_delta_fH"
    )
    pure_row["values"]["points"][0]["uncertainty"]["value"] = "0.738"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_pure_uncertainty)

    changed_pure_lineage = deepcopy(document)
    pure_row = next(
        row
        for row in changed_pure_lineage["species"]["NaFeSi2O6"]["observations"]
        if row["observation_id"] == "aegirine_discussion_pure_endmember_delta_fH"
    )
    pure_row["derived_from"].remove("aegirine_table4_reaction_21_constituent_delta_fH")
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_pure_lineage)

    changed_table9_footnote = deepcopy(document)
    table9_row = next(
        row
        for row in changed_table9_footnote["species"][NATURAL]["observations"]
        if row["observation_id"] == "aegirine_table9_cp"
    )
    next(
        point
        for point in table9_row["values"]["points"]
        if point.get("transition_marker") == "1"
    )["transition_as_footnoted"] = "Na melting point"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_table9_footnote)


def test_isolated_migration_preserves_rows_units_conditions_and_schema_gaps() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)
    source_observations = _observations(document)

    for row in source_observations.values():
        for point in row["values"]["points"]:
            conditions = point["point_conditions"]
            for axis in ("temperature_K", "total_pressure_Pa", "fO2_Pa"):
                assert "state" in conditions[axis]
                assert conditions[axis]["locator"]["page"]
            assert conditions["total_pressure_Pa"]["state"] == {
                "tag": "unknown",
                "reason": "not_published",
            }
            assert conditions["fO2_Pa"]["state"] == {
                "tag": "unknown",
                "reason": "not_published",
            }
            if "T_K" in point:
                assert conditions["temperature_K"]["state"] == {
                    "tag": "value",
                    "value": point["T_K"],
                }
            else:
                assert conditions["temperature_K"]["state"] == {
                    "tag": "unknown",
                    "reason": "not_single_temperature",
                }

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    migrated = list(result.observations.values())
    assert result.validation.hard_issues == ()
    assert len(migrated) == 616
    assert sum(row.value.kind.value == "unavailable" for row in migrated) == 259
    assert sum(not row.identity.quantity.is_value for row in migrated) == 259
    assert sum(not row.identity.species.phase.is_value for row in migrated) == 0

    unknown_quantities = Counter(
        row.observation_id.split("::")[1]
        for row in migrated
        if not row.identity.quantity.is_value
    )
    expected_unknown = Counter(
        {
            "aegirine_table6_entropy_increment_from_S0": 54,
            "aegirine_table6_negative_gibbs_function_from_H0_over_T": 54,
            "aegirine_table6_enthalpy_increment_from_H0": 54,
            "aegirine_table8_negative_gibbs_function_over_T_from_H298": 26,
            "aegirine_table9_negative_gibbs_function_over_T_from_H298": 15,
            "aegirine_table9_delta_fH_from_oxides": 15,
            "aegirine_table9_delta_fG_from_oxides": 15,
            "aegirine_solution_measurements": 6,
            "aegirine_solution_correction_terms": 5,
            "aegirine_table4_reaction_25_formation_from_oxides": 1,
            "aegirine_abstract_delta_fH_oxides": 1,
            "aegirine_pure_endmember_adjusted_heat_of_solution": 1,
            "aegirine_pure_endmember_overall_calorimetric_reaction": 1,
            **{f"aegirine_table3_reaction_{number}": 1 for number in range(1, 10)},
            "aegirine_table3_reaction_10": 1,
            "aegirine_table3_overall_reaction_11": 1,
        }
    )
    assert unknown_quantities == expected_unknown
    assert Counter(
        row.observation_id.split("::")[1]
        for row in migrated
        if row.value.kind.value == "unavailable"
    ) == expected_unknown

    for row in migrated:
        if row.identity.quantity.is_value:
            assert row.identity.temperature_K is not None
            assert row.identity.temperature_K.is_value
            assert row.point_conditions is not None
            assert row.point_conditions["temperature_K"].locator is not None
        for axis in ("total_pressure_Pa", "fO2_Pa", "standard_pressure_Pa"):
            state = getattr(row.identity, axis, None)
            if state is not None:
                assert not state.is_value

    def migrated_for(source_id: str, temperature: Decimal):
        return next(
            row
            for row in migrated
            if row.observation_id.split("::")[1] == source_id
            and row.identity.temperature_K is not None
            and row.identity.temperature_K.is_value
            and row.identity.temperature_K.value == temperature
        )

    cp = migrated_for("aegirine_table5_low_temperature_cp", Decimal("19.1"))
    assert cp.identity.quantity.value is Quantity.CP
    assert cp.value.point == Decimal("4.137976")
    formation = migrated_for("aegirine_table8_delta_fH", Decimal("298.15"))
    assert formation.identity.quantity.value is Quantity.DELTA_FH
    assert formation.value.point == Decimal("-2682.446080")
    entropy = migrated_for("aegirine_abstract_standard_entropy", Decimal("298.15"))
    assert entropy.identity.quantity.value is Quantity.S
    assert entropy.value.point == Decimal("159.11752")
    assert entropy.uncertainty.verbatim["value"] == ".14"
    assert entropy.uncertainty.verbatim["verbatim"] == "±.14 cal/mol/K"

    for row in (cp, formation, entropy):
        assert row.identity.composition is not None
        assert row.identity.composition.tag.value == "not_applicable"
        assert row.identity.total_pressure_Pa is not None
        assert row.identity.total_pressure_Pa.tag.value == "not_applicable"
        assert row.identity.fO2_Pa is not None
        assert row.identity.fO2_Pa.tag.value == "not_applicable"
        assert row.identity.standard_pressure_Pa is not None
        assert row.identity.standard_pressure_Pa.tag.value == "unknown"
