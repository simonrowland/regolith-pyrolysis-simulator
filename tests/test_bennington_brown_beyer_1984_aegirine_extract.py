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
    assert point_count == 614
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
            "page": 16,
            "published_page": 11,
            "section": "Enthalpies and Gibbs Energies of Formation and Reaction",
        }
        assert "raw_inputs" not in derived["derivation"]

    abstract_s = observations["aegirine_abstract_standard_entropy"]
    assert abstract_s["values"]["points"][0]["value"] == "38.03"
    assert abstract_s["uncertainty"]["value"] == ".14"
    assert abstract_s["uncertainty"]["verbatim"] == "±.14 cal/mol/K"
    assert abstract_s["locator"]["page"] == 11
    assert "table" not in abstract_s["locator"]

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
        "aegirine_table9_delta_fH_from_oxides": [
            ("1", "847.00"),
            ("2", "960.00"),
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
    assert len(migrated) == 614
    assert sum(row.value.kind.value == "unavailable" for row in migrated) == 257
    assert sum(not row.identity.quantity.is_value for row in migrated) == 257
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
