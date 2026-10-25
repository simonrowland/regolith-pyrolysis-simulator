from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from simulator.battery.records import ValueKind

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/rb86-rogez-etal-1983.yaml"

# Table VI values transcribed from the render of PDF page 9 (published p. 126).
TABLE_VI_RENDER_GRID = (
    ("0", "1", "0"),
    ("0.1", "0,847", "0,010"),
    ("0.2", "0,674", "0,038"),
    ("0.3", "0,513", "0,086"),
    ("0.4", "0,380", "0,152"),
    ("0.5", "0,253", "0,250"),
    ("0.6", "0,152", "0,379"),
    ("0.7", "0,079", "0,537"),
    ("0.8", "0,035", "0,706"),
    ("0.9", "0,010", "0,871"),
    ("1", "0", "1"),
)

# The printed xOr column and both row mole fractions from Table VI, in order.
TABLE_VI_COMPOSITION_GRID = (
    ("0", "1", "0"),
    ("0,1", "0.9", "0.1"),
    ("0,2", "0.8", "0.2"),
    ("0,3", "0.7", "0.3"),
    ("0,4", "0.6", "0.4"),
    ("0,5", "0.5", "0.5"),
    ("0,6", "0.4", "0.6"),
    ("0,7", "0.3", "0.7"),
    ("0,8", "0.2", "0.8"),
    ("0,9", "0.1", "0.9"),
    ("1", "0", "1"),
)

TABLE_V_RENDER_GRID = (
    ("0,003", "0,366"),
    ("0,025", "-0,096"),
    ("0,1", "0,637"),
    ("0,3", "1,807"),
    ("0,5", "2,840"),
    ("0,6", "3,434"),
    ("0,7", "3,752"),
    ("0,9", "5,242"),
    ("0,975", "6,496"),
    ("0,997", "5,113"),
)

# Corrected appendix values were checked against the PDF page renders.
APPENDIX_RENDER_BINDINGS = (
    ((10, "0,025", "2", "17"), "ln_I23_over_I39_as_printed", "-2,237"),
    ((10, "0,1", "1", "6"), "ln_I23_over_I39_as_printed", "-1,034"),
    ((10, "0,1", "1", "7"), "ln_I23_over_I39_as_printed", "-0,935"),
    ((10, "0,1", "1", "8"), "ln_I23_over_I39_as_printed", "-0,668"),
    ((10, "0,1", "1", "9"), "ln_I23_over_I39_as_printed", "-0,451"),
    ((10, "0,1", "2", "1"), "ln_I23_over_I39_as_printed", "-0,405"),
    ((10, "0,1", "2", "2"), "ln_I23_over_I39_as_printed", "-0,762"),
    ((10, "0,1", "2", "3"), "ln_I23_over_I39_as_printed", "-0,808"),
    ((10, "0,1", "2", "4"), "ln_I23_over_I39_as_printed", "-0,936"),
    ((10, "0,1", "2", "5"), "ln_I23_over_I39_as_printed", "-1,043"),
    ((10, "0,1", "2", "6"), "ln_I23_over_I39_as_printed", "-1,119"),
    ((10, "0,1", "2", "7"), "ln_I23_over_I39_as_printed", "-1,176"),
    ((10, "0,1", "2", "8"), "ln_I23_over_I39_as_printed", "-1,325"),
    ((10, "0,1", "2", "9"), "ln_I23_over_I39_as_printed", "-1,262"),
    ((10, "0,1", "2", "10"), "ln_I23_over_I39_as_printed", "-1,373"),
    ((10, "0,1", "2", "11"), "ln_I23_over_I39_as_printed", "-1,375"),
    ((10, "0,1", "2", "24"), "ln_I23_over_I39_as_printed", "-0,744"),
    ((10, "0,7", "2", "20"), "ln_I23_over_I39_as_printed", "2,066"),
    ((11, "0,997", "2", "9"), "10^4_over_T_K_inverse_as_printed", "5,65"),
)


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _context(document, table):
    return next(
        row
        for row in document["species"]["Na2Al2Si6O16"]["context"]
        if row["locator"]["table"] == table
    )


def _activity_grid(document):
    rows = {}
    for formula, bundle in document["species"].items():
        for observation in bundle["observations"]:
            if observation.get("type") != "activity_coefficient":
                continue
            values = observation["values"]
            x_or = str(observation["locator"]["row_label"]).replace(",", ".")
            rows.setdefault(x_or, {})[formula] = values["activity_as_printed"]
    return tuple(
        (x_or, cells["Na2Al2Si6O16"], cells["K2Al2Si6O16"])
        for x_or, cells in sorted(rows.items(), key=lambda item: Decimal(item[0]))
    )


def _unprinted_pressure_absent(observations):
    return all(
        "total_pressure_Pa" not in observation.point_conditions
        for observation in observations
    )


def test_render_grids_and_printed_table_row_counts():
    document = _document()
    assert _activity_grid(document) == TABLE_VI_RENDER_GRID
    assert all(
        observation["locator"]["page"] == 9
        for bundle in document["species"].values()
        for observation in bundle["observations"]
        if observation.get("type") == "activity_coefficient"
    )

    table_v = _context(document, "V")
    table_v_grid = tuple(
        (
            row["xAb_as_printed"],
            row["ln_I23_squared_xOr_over_I39_squared_xAb_as_printed"],
        )
        for row in table_v["values"]["rows"]
    )
    assert table_v_grid == TABLE_V_RENDER_GRID
    assert table_v["locator"]["page"] == 8
    assert _context(document, "VI")["locator"]["page"] == 9

    expected_rows = {"I": 18, "II": 7, "III": 6, "IV": 6, "V": 10, "VI": 11}
    for table, row_count in expected_rows.items():
        assert len(_context(document, table)["values"]["rows"]) == row_count
    table_iv = _context(document, "IV")
    assert (
        table_iv["values"]["rows"][-1][
            "mixing_enthalpy_plusminus_width_kJ_mol_as_printed"
        ]
        == "0"
    )
    assert table_iv["values"]["uncertainty_notice"] == "confidence level not stated"
    assert len(_context(document, "Annexe")["values"]["rows"]) == 318

    appendix_rows = _context(document, "Annexe")["values"]["rows"]
    by_binding = {
        (
            row["pdf_page"],
            row["xAb_series_as_assigned"],
            row["assay_as_printed"],
            row["run_as_printed"],
        ): row
        for row in appendix_rows
    }
    for binding, column, expected in APPENDIX_RENDER_BINDINGS:
        assert by_binding[binding][column] == expected

    printed_typo = by_binding[(10, "0,975", "2", "18")]
    assert printed_typo["xAb_as_printed"] == "0,875"
    assert "apparent source typo" in printed_typo["xAb_series_assignment_note"]
    table_vi = _context(document, "VI")["values"]["rows"]
    x_or_09 = next(row for row in table_vi if row["xOr_as_printed"] == "0,9")
    assert x_or_09["partial_excess_G_Ab_kJ_mol_as_printed"] == "-24,63"

    addendum = _context(document, "Addendum")
    assert addendum["locator"] == {
        "page": 11,
        "pdf_page_index": 10,
        "published_page": 128,
        "table": "Addendum",
    }
    assert addendum["values"]["calorimetry_component_basis"] == (
        "NaAlSi3O8 / KAlSi3O8"
    )
    assert addendum["values"]["spectrometry_component_basis"] == (
        "Na2O, Al2O3, 6SiO2 / K2O, Al2O3, 6SiO2"
    )
    assert addendum["values"]["spectrometry_formula_units"] == {
        "albite": "Na2Al2Si6O16",
        "orthose": "K2Al2Si6O16",
    }

    mutated = deepcopy(document)
    first_activity = mutated["species"]["Na2Al2Si6O16"]["observations"][0]
    first_activity["values"]["activity_as_printed"] = "0,999"
    assert _activity_grid(mutated) != TABLE_VI_RENDER_GRID


def test_isolated_migration_maps_printed_conditions_and_omits_unprinted_pressure():
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    observations = list(result.observations.values())
    assert len(observations) == 22
    assert result.validation.hard_issues == ()
    assert result.queue == []
    assert all(
        observation.value.kind is not ValueKind.UNAVAILABLE
        for observation in observations
    )

    for observation in observations:
        source_id = observation.observation_id.rsplit("::", 1)[-1]
        row_number = int(source_id.rsplit("_r", 1)[1].split("_c", 1)[0])
        column_number = int(source_id.rsplit("_c", 1)[1])
        row_index = row_number - 2
        printed_x_or, x_ab, x_or = TABLE_VI_COMPOSITION_GRID[row_index]
        rendered_x_or, activity_ab, activity_or = TABLE_VI_RENDER_GRID[row_index]
        expected_species, expected_basis, expected_activity = (
            ("Na2Al2Si6O16", "Na2O, Al2O3, 6SiO2", activity_ab)
            if column_number == 2
            else ("K2Al2Si6O16", "K2O, Al2O3, 6SiO2", activity_or)
        )
        assert rendered_x_or.replace(".", ",") == printed_x_or
        assert observation.identity.species.formula == expected_species
        assert observation.identity.reference_state.is_value
        assert (
            observation.identity.reference_state.value.endmember.formula
            == expected_species
        )
        assert (
            observation.identity.reference_state.value.component_basis
            == expected_basis
        )
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(expected_activity.replace(",", "."))
        assert observation.identity.composition.is_value
        assert observation.identity.composition.value.as_map() == {
            "Na2Al2Si6O16": Decimal(x_ab),
            "K2Al2Si6O16": Decimal(x_or),
        }
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == Decimal("1300")
        assert observation.identity.total_pressure_Pa.is_unknown
        assert observation.identity.fO2_Pa.is_unknown
        assert observation.point_conditions["temperature_K"].state.is_value
        assert observation.point_conditions["composition"].state.is_value
        assert observation.point_conditions["fO2_Pa"].state.is_unknown
        assert observation.locator.page == 9
        assert observation.locator.pdf_page_index == 8
        assert observation.locator.published_page == 126
        assert observation.locator.table == "VI"
        expected_row_locator = f"row={printed_x_or}"
        assert expected_row_locator in (observation.locator.note or "")
        for condition_name in ("temperature_K", "composition", "fO2_Pa"):
            condition = observation.point_conditions[condition_name]
            assert condition.locator is not None
            assert condition.locator.page == 9
            assert condition.locator.pdf_page_index == 8
            assert condition.locator.published_page == 126
            assert condition.locator.table == "VI"
            assert expected_row_locator in (condition.locator.note or "")
        assert "total_pressure_Pa" not in observation.point_conditions
    assert _unprinted_pressure_absent(observations)

    mutated = deepcopy(observations)
    first = mutated[0]
    mutated[0] = replace(
        first,
        point_conditions={
            **first.point_conditions,
            "total_pressure_Pa": first.point_conditions["fO2_Pa"],
        },
    )
    with pytest.raises(AssertionError):
        assert _unprinted_pressure_absent(mutated)
