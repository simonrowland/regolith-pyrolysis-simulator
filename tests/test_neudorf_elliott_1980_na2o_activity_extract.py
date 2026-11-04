from __future__ import annotations

import copy
import csv
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    EvidenceClass,
    Phase,
    Polymorph,
    ReferenceStateConvention,
    ValueKind,
)
from simulator.battery.migrate import migrate
from literature_extract_test_utils import isolated_root

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/neudorf-elliott-1980-na2o-cao-silicate-activities.yaml"
CELL_AUDIT = ROOT / "data/literature/extracts/audits/neudorf-elliott-1980-na2o-cao-silicate-activities.cell-audit.csv"

TABLE_I = (
    ("25", "34.4", "-7,650 ± 350", "-4.37 ± 0.27"),
    ("30", "29.4", "-8,390 ± 210", "-3.31 ± 0.16"),
    ("35", "34.3", "-6,830 ± 210", "-3.80 ± 0.16"),
    ("40", "39.3", "-6,140 ± 610", "-3.67 ± 0.46"),
)
TABLE_II = (
    ("1273", "5.923", "-12.31", "0.244", "695", "-0.620"),
    ("1323", "5.748", "-12.01", "0.294", "530", "-0.694"),
    ("1373", "5.592", "-11.74", "0.343", "243", "-0.763"),
    (None, None, None, "0.393", "-182", "-0.863"),
)
TABLE_III = (
    ("24.4", "257.2", "2.39", "-146.5", "13.3", "83.7", "11.9", "64.6", "25.7", "24.4"),
    ("29.4", "244.5", "7.42", "-160.6", "10.2", "63.4", "13.3", "77.1", "40.0", "28.5"),
    ("34.3", "227.1", "14.67", "-130.8", "4.66", "72.8", "14.6", "87.5", "41.8", "34.6"),
    ("39.3", "210.6", "25.34", "-117.6", "-3.48", "70.3", "16.5", "98.1", "48.3", "37.6"),
)
TABLE_IV = (
    ("0.35", "0.00", "1.00", None, "8.82"),
    ("0.33", "0.02", "0.971", "8.93", "8.89"),
    ("0.31", "0.04", "0.939", "9.05", "9.00"),
    ("0.29", "0.06", "0.906", "9.16", "9.14"),
    ("0.27", "0.08", "0.871", "9.29", "9.27"),
    ("0.25", "0.10", "0.833", "9.41", "9.41"),
)

TABLE_HEADERS = {
    "I": ("Na2O w/o", "Na2O m/o", "A, K ± standard deviation", "B ± standard deviation"),
    "II": ("left T, K", "left C", "left D", "right NNa2O", "right A, K", "right B"),
    "III": ("m/o Na2O", "-GM(i) Na2O", "-GM(i) SiO2", "HM(i) Na2O", "HM(i) SiO2", "SM(i) Na2O", "SM(i) SiO2", "-GM", "-HM", "SM"),
    "IV": ("NNa2O", "NCaO", "NNa+", "calculated by Eq. [8]", "experimental interpolated"),
}
TABLES = {"I": TABLE_I, "II": TABLE_II, "III": TABLE_III, "IV": TABLE_IV}
NA2O_LIQUID_STANDARD_STATE = {
    "convention": "raoultian_pure_endmember",
    "component_basis": "Na2O",
    "endmember": {
        "formula": "Na2O",
        "phase": {"tag": "value", "value": "l"},
        "polymorph": {"tag": "not_applicable", "reason": "not crystal"},
        "charge": {"tag": "value", "value": 0},
    },
}
SIO2_TRIDYMITE_STANDARD_STATE = {
    "convention": "raoultian_pure_endmember",
    "component_basis": "SiO2",
    "endmember": {
        "formula": "SiO2",
        "phase": {"tag": "value", "value": "cr"},
        "polymorph": {"tag": "value", "value": "tridymite"},
        "charge": {"tag": "value", "value": 0},
    },
}


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block.get("observations", [])
    }


def _table3_rows(document: dict) -> list[dict]:
    return sorted(
        (
            row
            for row in document["species"]["Na2O"]["observations"]
            if row["observation_id"].startswith("neudorf_elliott_1980_table3_row")
        ),
        key=lambda row: row["observation_id"],
    )


def _assert_migrated_conditions(
    row: object,
    *,
    temperature_K: str | None,
    composition: dict[str, str] | None,
) -> None:
    identity = row.identity
    if temperature_K is None:
        assert identity.temperature_K is None or identity.temperature_K.is_unknown
    else:
        assert identity.temperature_K.is_value
        assert identity.temperature_K.value == Decimal(temperature_K)
    if composition is None:
        assert identity.composition is None or identity.composition.is_unknown
    else:
        assert identity.composition.is_value
        assert identity.composition.value.as_map() == {
            formula: Decimal(amount) for formula, amount in composition.items()
        }
    assert identity.fO2_Pa.is_value
    assert identity.fO2_Pa.value == Decimal("21278.25")
    assert identity.total_pressure_Pa is None or identity.total_pressure_Pa.is_unknown


def _assert_mapped_numeric_activity_points(document: dict, points: dict[str, object]) -> None:
    expected = {
        "neudorf_elliott_1980_abstract_log_a_na2o_25wt_1050c": {
            "formula": "Na2O",
            "phase": Phase.L,
            "composition": {"Na2O": "0.244", "SiO2": "0.756"},
            "printed_partial": {"Na2O": "0.244"},
            "temperature_C": "1050",
            "standard_state": NA2O_LIQUID_STANDARD_STATE,
            "standard_state_as_printed": (
                "The standard state of Na2O is chosen to be pure liquid Na2O at 1 atm pressure."
            ),
            "standard_state_locator": {
                "page": 2,
                "pdf_page_index": 1,
                "published_page": 608,
                "section": "Thermodynamic Considerations",
            },
        },
        "neudorf_elliott_1980_abstract_log_a_na2o_40wt_1050c": {
            "formula": "Na2O",
            "phase": Phase.L,
            "composition": {"Na2O": "0.393", "SiO2": "0.607"},
            "printed_partial": {"Na2O": "0.393"},
            "temperature_C": "1050",
            "standard_state": NA2O_LIQUID_STANDARD_STATE,
            "standard_state_as_printed": (
                "The standard state of Na2O is chosen to be pure liquid Na2O at 1 atm pressure."
            ),
            "standard_state_locator": {
                "page": 2,
                "pdf_page_index": 1,
                "published_page": 608,
                "section": "Thermodynamic Considerations",
            },
        },
        "neudorf_elliott_1980_sio2_activity_na_binary_at_1070c": {
            "formula": "SiO2",
            "phase": Phase.CR,
            "polymorph": Polymorph.TRIDYMITE,
            "composition": {"Na2O": "0.35", "SiO2": "0.65"},
            "printed_partial": {"SiO2": "0.65"},
            "temperature_C": "1070",
            "standard_state": SIO2_TRIDYMITE_STANDARD_STATE,
            "standard_state_as_printed": "The activity of SiO2 is relative to tridymite.",
            "standard_state_locator": None,
        },
    }
    source_rows = _observations(document)
    for observation_id, pin in expected.items():
        source = source_rows[observation_id]
        source_values = source["values"]
        assert source["standard_state"] == pin["standard_state"]
        assert source["standard_state_as_printed"] == pin["standard_state_as_printed"]
        if pin["standard_state_locator"] is None:
            assert source["standard_state_locator"] == source["locator"]
        else:
            assert source["standard_state_locator"] == pin["standard_state_locator"]
        assert source_values["T_C"] == pin["temperature_C"]
        assert "binary Na2O-SiO2 system" in source_values["composition_as_published"]

        conditions = source["point_conditions"]
        assert conditions["temperature_C"]["state"]["tag"] == "value"
        assert conditions["temperature_C"]["locator"]
        composition = conditions["composition"]
        assert composition["state"]["value"]["basis"] == "binary_complement_mole_fraction"
        assert composition["state"]["value"]["amount_basis"] == "mole_fraction"
        assert dict(composition["state"]["value"]["components"]) == pin["composition"]
        assert composition["locator"]
        inference = composition["inference"]
        assert inference["relation"] == "binary per abstract, complement"
        partial_formula, partial_amount = next(iter(pin["printed_partial"].items()))
        partial = inference["parameters"][0][1]
        assert inference["parameters"][0][0] == f"X_{partial_formula}"
        assert partial["state"]["value"] == partial_amount
        assert partial["locator"] == composition["locator"]

        identity = points[observation_id].identity
        assert identity.composition.is_value
        assert identity.composition.value.as_map() == {
            formula: Decimal(amount) for formula, amount in pin["composition"].items()
        }
        reference_state = identity.reference_state
        assert reference_state is not None and reference_state.is_value
        assert reference_state.value.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        endmember = reference_state.value.endmember
        assert endmember.formula == pin["formula"]
        assert endmember.phase.is_value and endmember.phase.value is pin["phase"]
        if "polymorph" in pin:
            assert endmember.polymorph.is_value
            assert endmember.polymorph.value is pin["polymorph"]


def _assert_extracted_table_cells(document: dict) -> None:
    observations = _observations(document)
    for index, row in enumerate(TABLE_I, start=1):
        values = observations[f"neudorf_elliott_1980_table1_row{index}_log_activity_temperature_fit"]["values"]
        assert (
            values["melt_composition_wt_percent_as_printed"],
            values["melt_composition_mole_percent_as_printed"],
            values["A_cell_as_printed"],
            values["B_cell_as_printed"],
        ) == row

    for index, row in enumerate(TABLE_II, start=1):
        if row[0] is not None:
            values = observations[f"neudorf_elliott_1980_table2_na2o_fit_row{index}"]["values"]
            assert (values["temperature_K_as_printed"], values["C_as_printed"], values["D_as_printed"]) == row[:3]
        values = observations[f"neudorf_elliott_1980_table2_sio2_fit_row{index}"]["values"]
        assert (values["NNa2O_as_printed"], values["A_as_printed"], values["B_as_printed"]) == row[3:]

    assert tuple(
        tuple(row[field] for field in (
            "composition_mole_percent_as_printed",
            "minus_partial_GM_Na2O_kJ_per_mol_as_printed",
            "minus_partial_GM_SiO2_kJ_per_mol_as_printed",
            "partial_HM_Na2O_kJ_per_mol_as_printed",
            "partial_HM_SiO2_kJ_per_mol_as_printed",
            "partial_SM_Na2O_J_per_mol_K_as_printed",
            "partial_SM_SiO2_J_per_mol_K_as_printed",
            "minus_integral_GM_kJ_per_mol_as_printed",
            "minus_integral_HM_kJ_per_mol_as_printed",
            "integral_SM_J_per_mol_K_as_printed",
        ))
        for row in (observation["values"] for observation in _table3_rows(document))
    ) == TABLE_III

    for index, row in enumerate(TABLE_IV, start=1):
        base = f"neudorf_elliott_1980_table4_row{index}"
        interpolated = observations[f"{base}_interpolated_log_a_star_na2o"]["values"]
        assert (
            interpolated["NNa2O_as_printed"],
            interpolated["NCaO_as_printed"],
            interpolated["NNa_plus_as_printed"],
            interpolated["log_a_star_as_printed"],
        ) == (*row[:3], row[4])
        assert interpolated["quantity"] == "activity"
        assert interpolated["admission_status"] == "typed_refusal"
        assert interpolated["method_class"] == "author_derived"
        interpolated_observation = observations[
            f"{base}_interpolated_log_a_star_na2o"
        ]
        assert interpolated_observation["standard_state_as_printed"] == (
            "The standard state of Na2O is chosen to be pure liquid Na2O at 1 atm pressure."
        )
        reference = interpolated_observation["standard_state"]
        assert reference["state"]["tag"] == "value"
        assert reference["state"]["value"] == {
            **NA2O_LIQUID_STANDARD_STATE,
            "reference_pressure_bar": "1.01325",
        }
        assert reference["inferred"] is False
        assert reference["locator"]["published_page"] == 608
        assert "Lin and Elliott [24]" in reference["note"]
        assert (
            interpolated_observation["method_class"] == "author_derived"
        )


def _assert_sc456_relabels(document: dict) -> None:
    observations = _observations(document)
    changed_ids = {
        "neudorf_elliott_1980_abstract_log_a_na2o_25wt_1050c",
        "neudorf_elliott_1980_abstract_log_a_na2o_40wt_1050c",
        *(f"neudorf_elliott_1980_table1_row{i}_log_activity_temperature_fit" for i in range(1, 5)),
        *(f"neudorf_elliott_1980_table2_na2o_fit_row{i}" for i in range(1, 4)),
        *(f"neudorf_elliott_1980_table2_sio2_fit_row{i}" for i in range(1, 5)),
    }
    for observation_id in changed_ids:
        values = observations[observation_id]["values"]
        assert values["method_class"] == "measured_reduced"
        assert values["evidence_class"] == "measured_reduced"

    assert observations[
        "neudorf_elliott_1980_abstract_log_a_na2o_25wt_1050c"
    ]["derived_from"] == ["neudorf_elliott_1980_table1_row1_log_activity_temperature_fit"]
    assert observations[
        "neudorf_elliott_1980_abstract_log_a_na2o_40wt_1050c"
    ]["derived_from"] == ["neudorf_elliott_1980_table1_row4_log_activity_temperature_fit"]
    for index in range(1, 5):
        row = observations[f"neudorf_elliott_1980_table1_row{index}_log_activity_temperature_fit"]
        raw_inputs = row["derivation"]["raw_inputs"]
        assert raw_inputs["state"]["value"] == "not_printed"
        assert raw_inputs["locator"]["figure"] == "3"
    table1_ids = [
        f"neudorf_elliott_1980_table1_row{index}_log_activity_temperature_fit"
        for index in range(1, 5)
    ]
    for index in range(1, 4):
        row = observations[f"neudorf_elliott_1980_table2_na2o_fit_row{index}"]
        assert row["derivation"]["inputs"] == table1_ids
    for index in range(1, 5):
        row = observations[f"neudorf_elliott_1980_table2_sio2_fit_row{index}"]
        assert row["derivation"]["references"] == [
            "Kracek [26]: J. Phys. Chem., 1930, vol. 34, p. 1583."
        ]


def test_sc456_evidence_class_and_lineage_are_pinned() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_sc456_relabels(document)

    reverted = copy.deepcopy(document)
    _observations(reverted)[
        "neudorf_elliott_1980_table1_row1_log_activity_temperature_fit"
    ]["values"]["evidence_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_sc456_relabels(reverted)
        assert "evidence_class" not in interpolated
        assert interpolated["column_as_printed"] == "Interpolated (†) as printed in the Table IV footnote"
        assert "activity" not in interpolated
        assert interpolated["table_reference_footnote_as_printed"] == (
            "* Relative to pure solid silica (tridymite)."
        )
        if row[3] is None:
            assert f"{base}_calculated_log_a_star_na2o" not in observations
        else:
            calculated = observations[f"{base}_calculated_log_a_star_na2o"]["values"]
            assert (
                calculated["NNa2O_as_printed"],
                calculated["NCaO_as_printed"],
                calculated["NNa_plus_as_printed"],
                calculated["log_a_star_as_printed"],
            ) == (*row[:3], row[3])
            assert calculated["quantity"] == "activity"
            assert calculated["admission_status"] == "typed_refusal"
            assert calculated["method_class"] == "model_derived"
            calculated_observation = observations[
                f"{base}_calculated_log_a_star_na2o"
            ]
            assert calculated_observation["method_class"] == "model_derived"
            context_id = (
                "neudorf-elliott-1980-na2o-cao-silicate-activities::context::"
                "neudorf_elliott_1980_richardson_eq8_model_context"
            )
            assert calculated_observation["derived_from"] == [context_id]
            derivation = calculated_observation["derivation"]
            assert derivation["inputs"] == [context_id]
            assert "Richardson ideal-mixing relation" in derivation["relation"]
            assert derivation["method"]["state"]["value"] == (
                "By Eq. [8]¹¹ as printed in the Table IV header."
            )
            assert derivation["method"]["locator"] == {
                "page": 7,
                "pdf_page_index": 6,
                "published_page": 613,
                "table": "IV",
                "equation": "8",
            }
            assert any(
                "Richardson [11]" in reference
                for reference in derivation["references"]
            )
            assert "evidence_class" not in calculated
            assert calculated["column_as_printed"] == "By Eq. [8]¹¹ as printed in the Table IV header"
            assert "activity" not in calculated


def _expected_audit_cells() -> list[tuple[str, str, str, str, str]]:
    cells = []
    for table, rows in TABLES.items():
        page = "7" if table == "IV" else "5"
        for row_number, row in enumerate(rows, start=1):
            for column, value in enumerate(row, start=1):
                if value is None:
                    continue
                cells.append((page, table, str(row_number), TABLE_HEADERS[table][column - 1], value))
    return cells


def test_complete_table_transcription_counts_and_mutation_guard() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_extracted_table_cells(document)

    with CELL_AUDIT.open(encoding="utf-8", newline="") as stream:
        audit = list(csv.DictReader(stream))
    assert len(audit) == 106
    observed = [
        (row["page"], row["table"], row["printed_data_row"], row["column_header"], row["value_as_printed"])
        for row in audit
    ]
    assert observed == _expected_audit_cells()
    assert all(row["MinerU_read"] == row["pdftotext_read"] == row["value_as_printed"] for row in audit)
    assert all("render" in row["confirmation"] for row in audit)
    assert {table: len(rows) for table, rows in TABLES.items()} == {"I": 4, "II": 4, "III": 4, "IV": 6}
    assert sum(value is None for row in TABLES["II"] for value in row) == 3
    assert sum(value is None for row in TABLES["IV"] for value in row) == 1

    assert len(document["fidelity_samples"]) >= 5
    pins = {
        "neudorf_elliott_1980_abstract_log_a_na2o_25wt_1050c": "-10.2",
        "neudorf_elliott_1980_table1_row1_log_activity_temperature_fit": "-7,650 ± 350",
        "neudorf_elliott_1980_table2_na2o_fit_row1": "5.923",
        "neudorf_elliott_1980_table3_row2_mixing_properties": "-160.6",
        "neudorf_elliott_1980_table4_row4_interpolated_log_a_star_na2o": "9.14",
    }
    samples = {sample["observation_id"]: sample for sample in document["fidelity_samples"]}
    for observation_id, expected in pins.items():
        assert str(samples[observation_id]["value"]) == expected
    assert [samples[key]["locator"]["page"] for key in pins] == [1, 5, 5, 5, 7]

    changed = copy.deepcopy(document)
    _table3_rows(changed)[1]["values"]["partial_HM_Na2O_kJ_per_mol_as_printed"] = "-160.5"
    with pytest.raises(AssertionError):
        _assert_extracted_table_cells(changed)
    changed_method = copy.deepcopy(document)
    calculated_row = _observations(changed_method)[
        "neudorf_elliott_1980_table4_row2_calculated_log_a_star_na2o"
    ]
    calculated_row["values"]["method_class"] = "author_derived"
    calculated_row["method_class"] = "author_derived"
    with pytest.raises(AssertionError):
        _assert_extracted_table_cells(changed_method)


def test_isolated_migration_preserves_identity_with_zero_hard_issues(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = migrate(isolated_root(tmp_path, document), write=False)
    assert result.validation is not None

    migrated = {row.observation_id.rsplit("::", 1)[-1]: row for row in result.observations.values()}
    assert len(migrated) == 29
    assert sum(row.value.kind is ValueKind.POINT for row in migrated.values()) == 3
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 26
    assert sum(row.admission.status.value == "admitted" for row in migrated.values()) == 3
    assert sum(row.admission.status.value == "rejected" for row in migrated.values()) == 26

    expected_unknown = {
        *(f"neudorf_elliott_1980_table1_row{index}_log_activity_temperature_fit" for index in range(1, 5)),
        *(f"neudorf_elliott_1980_table2_na2o_fit_row{index}" for index in range(1, 4)),
        *(f"neudorf_elliott_1980_table2_sio2_fit_row{index}" for index in range(1, 5)),
        *(f"neudorf_elliott_1980_table3_row{index}_mixing_properties" for index in range(1, 5)),
    }
    unknown_quantities = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity is None or row.identity.quantity.is_unknown
    }
    assert unknown_quantities == expected_unknown
    assert all(row.identity.species.phase.is_value for row in migrated.values())
    assert all(row.identity.species.phase.value.value == "l" for row in migrated.values())

    assert not result.validation.hard_issues

    points = {key: row for key, row in migrated.items() if row.value.kind is ValueKind.POINT}
    assert set(points) == {
        "neudorf_elliott_1980_abstract_log_a_na2o_25wt_1050c",
        "neudorf_elliott_1980_abstract_log_a_na2o_40wt_1050c",
        "neudorf_elliott_1980_sio2_activity_na_binary_at_1070c",
    }
    assert points["neudorf_elliott_1980_abstract_log_a_na2o_25wt_1050c"].value.point == Decimal("6.309573444801932494343601366E-11")
    assert points["neudorf_elliott_1980_abstract_log_a_na2o_40wt_1050c"].value.point == Decimal("5.011872336272722850015541869E-9")
    assert points["neudorf_elliott_1980_sio2_activity_na_binary_at_1070c"].value.point == Decimal("0.24")
    abstract_25 = points["neudorf_elliott_1980_abstract_log_a_na2o_25wt_1050c"]
    abstract_40 = points["neudorf_elliott_1980_abstract_log_a_na2o_40wt_1050c"]
    silica_activity = points["neudorf_elliott_1980_sio2_activity_na_binary_at_1070c"]
    _assert_mapped_numeric_activity_points(document, points)

    mutated_composition = copy.deepcopy(document)
    _observations(mutated_composition)[
        "neudorf_elliott_1980_abstract_log_a_na2o_25wt_1050c"
    ]["point_conditions"]["composition"]["state"]["value"]["components"][1][1] = "0.755"
    with pytest.raises(AssertionError):
        _assert_mapped_numeric_activity_points(mutated_composition, points)
    mutated_reference_state = copy.deepcopy(document)
    _observations(mutated_reference_state)[
        "neudorf_elliott_1980_sio2_activity_na_binary_at_1070c"
    ]["standard_state"] = "pure solid upper cristobalite"
    with pytest.raises(AssertionError):
        _assert_mapped_numeric_activity_points(mutated_reference_state, points)

    silica_source = _observations(document)["neudorf_elliott_1980_sio2_activity_na_binary_at_1070c"]
    assert silica_source["values"]["method_class"] == "measured_reduced"
    assert silica_source["values"]["evidence_class"] == "measured_reduced"
    assert silica_source["derived_from"] == [
        "neudorf_elliott_1980_table2_sio2_fit_row3",
        "neudorf_elliott_1980_table2_sio2_fit_row4",
    ]
    assert "Gibbs-Duhem equation [8]" in silica_source["derivation"]["relation"]
    abstract_25_source = _observations(document)[
        "neudorf_elliott_1980_abstract_log_a_na2o_25wt_1050c"
    ]
    abstract_25_composition = abstract_25_source["point_conditions"]["composition"]
    assert abstract_25_composition["state"]["value"]["components"] == [
        ["Na2O", "0.244"],
        ["SiO2", "0.756"],
    ]
    assert abstract_25_composition["inference"]["parameters"][0][1]["state"]["value"] == "0.244"
    assert abstract_25_composition["locator"] == {
        "page": 3,
        "pdf_page_index": 2,
        "published_page": 609,
        "section": "Results and Discussion",
        "note": "Results text maps 25 w/o Na2O to 24.4 m/o Na2O.",
    }
    assert "Table I prints 34.4 m/o" in abstract_25_source["values"][
        "composition_discrepancy_note"
    ]
    _assert_migrated_conditions(
        abstract_25,
        temperature_K="1323.15",
        composition={"Na2O": "0.244", "SiO2": "0.756"},
    )
    _assert_migrated_conditions(
        abstract_40,
        temperature_K="1323.15",
        composition={"Na2O": "0.393", "SiO2": "0.607"},
    )
    _assert_migrated_conditions(
        silica_activity,
        temperature_K="1343.15",
        composition={"Na2O": "0.35", "SiO2": "0.65"},
    )

    for index, table_row in enumerate(TABLE_I, start=1):
        _assert_migrated_conditions(
            migrated[f"neudorf_elliott_1980_table1_row{index}_log_activity_temperature_fit"],
            temperature_K=None,
            composition={"Na2O": str(Decimal(table_row[1]) / 100)},
        )
    for index, table_row in enumerate(TABLE_II, start=1):
        if table_row[0] is not None:
            _assert_migrated_conditions(
                migrated[f"neudorf_elliott_1980_table2_na2o_fit_row{index}"],
                temperature_K=table_row[0],
                composition=None,
            )
        _assert_migrated_conditions(
            migrated[f"neudorf_elliott_1980_table2_sio2_fit_row{index}"],
            temperature_K=None,
            composition={"Na2O": table_row[3]},
        )
    for index, table_row in enumerate(TABLE_III, start=1):
        table3_observation = migrated[f"neudorf_elliott_1980_table3_row{index}_mixing_properties"]
        source_table3 = _observations(document)[f"neudorf_elliott_1980_table3_row{index}_mixing_properties"]
        assert source_table3["values"]["method_class"] == "measured_reduced"
        assert source_table3["values"]["evidence_class"] == "measured_reduced"
        assert source_table3["values"]["derived_from"] == [
            f"neudorf_elliott_1980_table1_row{index}_log_activity_temperature_fit",
            "neudorf_elliott_1980_table2_na2o_fit_row2",
            f"neudorf_elliott_1980_table2_sio2_fit_row{index}",
        ]
        assert table3_observation.value.kind is ValueKind.UNAVAILABLE
        _assert_migrated_conditions(
            table3_observation,
            temperature_K="1323.15",
            composition={"Na2O": str(Decimal(table_row[0]) / 100)},
        )
    for index, table_row in enumerate(TABLE_IV, start=1):
        interpolated_id = f"neudorf_elliott_1980_table4_row{index}_interpolated_log_a_star_na2o"
        interpolated = migrated[interpolated_id]
        assert interpolated.value.kind is ValueKind.UNAVAILABLE
        assert interpolated.evidence.class_.is_unknown
        assert interpolated.identity.reference_state.is_value
        reference_state = interpolated.identity.reference_state.value
        assert reference_state.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        assert reference_state.endmember.formula == "Na2O"
        assert reference_state.endmember.phase.value is Phase.L
        assert reference_state.reference_pressure_bar == Decimal("1.01325")
        _assert_migrated_conditions(
            interpolated,
            temperature_K="1343.15",
            composition={"Na2O": table_row[0], "CaO": table_row[1]},
        )
        if table_row[3] is not None:
            calculated = migrated[f"neudorf_elliott_1980_table4_row{index}_calculated_log_a_star_na2o"]
            assert calculated.value.kind is ValueKind.UNAVAILABLE
            assert calculated.evidence.class_.is_value
            assert calculated.evidence.class_.value is EvidenceClass.MODEL_DERIVED
            assert calculated.evidence.model == "model_derived"
            assert calculated.identity.reference_state.is_value
            assert calculated.identity.reference_state.value.convention is (
                ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
            )
            _assert_migrated_conditions(
                calculated,
                temperature_K="1343.15",
                composition={"Na2O": table_row[0], "CaO": table_row[1]},
            )

    refused = migrated["neudorf_elliott_1980_table4_row4_interpolated_log_a_star_na2o"]
    assert refused.value.kind is ValueKind.UNAVAILABLE
    assert refused.identity.quantity.is_value
    assert refused.identity.quantity.value.value == "activity"
    assert refused.identity.species.phase.value.value == "l"
    assert refused.identity.temperature_K.value == Decimal("1343.15")
    assert refused.identity.composition.value.as_map() == {"Na2O": Decimal("0.29"), "CaO": Decimal("0.06")}
    assert refused.identity.fO2_Pa.value == Decimal("21278.25")
    assert refused.identity.total_pressure_Pa is None or refused.identity.total_pressure_Pa.is_unknown

    contexts = [context for rows in result.context_by_work.values() for context in rows]
    assert len(contexts) == 3
    reference_context = document["species"]["Na2O"]["context"][0]["values"]
    assert reference_context["reference_melt_equations_attributed_as_printed"] == "Lin and Elliott [24]"
    assert reference_context["reference_melt_GM_conditions_as_printed"] == "1200 K and 0.53 < NWO3 < 0.8"
