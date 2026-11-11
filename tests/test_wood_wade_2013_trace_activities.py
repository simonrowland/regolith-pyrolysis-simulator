from __future__ import annotations

import copy
import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    EvidenceClass,
    PerBasis,
    Phase,
    Quantity,
    ReferenceStateConvention,
    UncertaintyKind,
)
from simulator.battery.identity import profile_for
from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "wood-wade-2013-trace-activities"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
CELL_AUDIT = (
    ROOT
    / "data/literature/extracts/audits"
    / f"{SOURCE_ID}.table1-cell-audit.csv"
)
LOCATOR_AUDIT = (
    ROOT
    / "data/literature/extracts/audits"
    / f"{SOURCE_ID}.audit.md"
)
TABLE_MAPPING = (
    ROOT
    / "data/literature/extracts/audits"
    / f"{SOURCE_ID}.mapping.yaml"
)

EXPECTED_OBSERVATIONS = {
    "wood_wade_2013_pbo_activity_coefficient": {
        "species": "PbO",
        "value": "0.22",
        "proof": "0.22 at 1,923 K",
        "method_class": EvidenceClass.MODEL_DERIVED,
    },
    "wood_wade_2013_cuo05_activity_coefficient": {
        "species": "CuO0.5",
        "value": "3.5",
        "proof": "3.5 in the silicate melt at 1,923 K",
        "method_class": EvidenceClass.MODEL_DERIVED,
    },
    "wood_wade_2013_ino15_activity_coefficient": {
        "species": "InO1.5",
        "value": "0.02",
        "proof": "0.02 in the SiO2-rich liquid at 1,923 K",
        "method_class": EvidenceClass.MODEL_DERIVED,
    },
}

PRINTED_TABLE_ROWS = (
    "Identifier",
    "Silicate",
    "SiO2",
    "Al2O3",
    "FeO",
    "MgO",
    "CaO",
    "Total",
    "gamma FeO^a",
    "Trace (ppm)",
    "Cu",
    "Ag",
    "In",
    "Pb",
    "Metal",
    "Fe",
    "Trace (ppm)",
    "Cu",
    "Ag",
    "In",
    "Pb",
)

EXPECTED_TABLE_CELLS = (
    (3, "Silicate", "SiO2", "59.4 (0.4)"),
    (4, "Silicate", "Al2O3", "10.7 (0.4)"),
    (5, "Silicate", "FeO", "11.3 (0.2)"),
    (6, "Silicate", "MgO", "10.4 (0.2)"),
    (7, "Silicate", "CaO", "7.2 (0.5)"),
    (8, "Silicate", "Total", "99.0 (0.3)"),
    (9, "Silicate", "gamma FeO^a", "0.92"),
    (11, "Silicate", "Cu", "51 (7)"),
    (12, "Silicate", "Ag", "31 (6)"),
    (13, "Silicate", "In", "91 (18)"),
    (14, "Silicate", "Pb", "139 (31)"),
    (16, "Metal", "Fe", "99.7 (0.7)"),
    (18, "Metal", "Cu", "2,270 (37)"),
    (19, "Metal", "Ag", "289 (14)"),
    (20, "Metal", "In", "250 (7)"),
    (21, "Metal", "Pb", "200 (5)"),
)


def _document() -> dict:
    payload = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _page_from_grep(proof: str) -> tuple[int, int, int]:
    checks = {}
    in_page_checks = False
    for line in LOCATOR_AUDIT.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if in_page_checks:
                break
            in_page_checks = line == "## Locator page checks"
            continue
        if not in_page_checks:
            continue
        if not line.startswith("|"):
            continue
        columns = [value.strip().strip("`") for value in line.strip("|").split("|")]
        if len(columns) != 5 or not all(value.isdigit() for value in columns[2:]):
            continue
        checks[columns[1]] = (int(columns[2]), int(columns[3]), int(columns[4]))
    return checks[proof]


def _observation_by_id(document: dict, observation_id: str) -> dict:
    matches = [
        observation
        for block in document["species"].values()
        for observation in block.get("observations", [])
        if observation["observation_id"] == observation_id
    ]
    assert len(matches) == 1
    return matches[0]


def _table_input_context(document: dict) -> dict:
    contexts = document["species"]["PbO"]["context"]
    matches = [
        context
        for context in contexts
        if context["observation_id"]
        == "wood_wade_2013_bw1102_table1_partitioning_inputs"
    ]
    assert len(matches) == 1
    return matches[0]


def _method_constants_context(document: dict) -> dict:
    contexts = document["species"]["PbO"]["context"]
    matches = [
        context
        for context in contexts
        if context["observation_id"]
        == "wood_wade_2013_metal_silicate_method_and_constants"
    ]
    assert len(matches) == 1
    return matches[0]


def _stored_table_cells(document: dict) -> dict[tuple[int, str, str], str]:
    values = _table_input_context(document)["values"]
    cells: dict[tuple[int, str, str], str] = {}
    for row, column, species in (
        (3, "Silicate", "SiO2"),
        (4, "Silicate", "Al2O3"),
        (5, "Silicate", "FeO"),
        (6, "Silicate", "MgO"),
        (7, "Silicate", "CaO"),
    ):
        cells[(row, column, species)] = values[
            "silicate_major_oxide_cells_as_printed"
        ][species]["value_as_published"]
    cells[(8, "Silicate", "Total")] = values["silicate_major_oxide_sum"][
        "value_as_published"
    ]
    cells[(9, "Silicate", "gamma FeO^a")] = values["gamma_FeO_as_printed"]
    for row, element, amount, sigma in (
        (11, "Cu", "51", "7"),
        (12, "Ag", "31", "6"),
        (13, "In", "91", "18"),
        (14, "Pb", "139", "31"),
    ):
        entry = values["trace_concentrations"]["silicate"][element]
        assert entry["value"] == amount
        assert entry["sigma"] == sigma
        cells[(row, "Silicate", element)] = entry["value_as_published"]
    for row, element, amount, sigma, spelling in (
        (16, "Fe", "99.7", "0.7", "99.7 (0.7)"),
        (18, "Cu", "2270", "37", "2,270 (37)"),
        (19, "Ag", "289", "14", "289 (14)"),
        (20, "In", "250", "7", "250 (7)"),
        (21, "Pb", "200", "5", "200 (5)"),
    ):
        entry = values["trace_concentrations"]["metal"][element]
        assert entry["value"] == amount
        assert entry["sigma"] == sigma
        assert entry["value_as_published"] == spelling
        cells[(row, "Metal", element)] = entry["value_as_published"]
    return cells


def _assert_source_value_pins(document: dict) -> None:
    assert len(EXPECTED_OBSERVATIONS) == 3
    stored_ids = set()
    for block in document["species"].values():
        stored_ids.update(
            row["observation_id"] for row in block.get("observations", [])
        )
    assert stored_ids == set(EXPECTED_OBSERVATIONS)
    for observation_id, expected in EXPECTED_OBSERVATIONS.items():
        row = _observation_by_id(document, observation_id)
        assert row["type"] == "activity_coefficient"
        assert row["phase"] == "l"
        page, pdf_page_index, published_page = _page_from_grep(expected["proof"])
        assert row["locator"]["page"] == page
        assert row["locator"]["pdf_page_index"] == pdf_page_index
        assert row["locator"]["published_page"] == published_page
        assert expected["proof"] in row["locator"]["note"]
        assert row["values"]["quantity"] == "activity_coefficient"
        assert row["values"]["gamma"] == expected["value"]
        assert row["values"]["gamma_as_printed"] == expected["value"]
        assert row["values"]["printed_form_as_published"] == "linear"
        assert "printed_form" not in row["values"]
        assert row["values"]["T_K"] == "1923"
        assert row["values"]["experimental_T_C_as_printed"] == "1650"
        assert row["values"]["method_class"] == expected["method_class"].value
        assert row["values"]["evidence_class"] == expected["method_class"].value
        assert "gamma_FeO = 0.92 calculated from Eq. (4)" in row["derivation"]["relation"]
        if observation_id == "wood_wade_2013_ino15_activity_coefficient":
            assert "26.25 J.K-1 at 2,183 K" in row["derivation"]["relation"]
        assert row["experiment"] == "wood_wade_2013_bw1102"
        assert row["units"] == "dimensionless"
        assert row["standard_state"]["state"]["value"]["convention"] == (
            "raoultian_pure_endmember"
        )
        assert row["standard_state"]["state"]["value"]["reference_pressure_bar"] == "1"
        assert row["standard_state"]["inferred"] is False
        assert row["standard_state"]["locator"]["page"] == _page_from_grep(
            "standard state of pure liquid at 1 bar"
        )[0]
        assert row["point_conditions"]["T_K"]["state"]["value"] == "1923"
        assert row["point_conditions"]["T_C"]["state"]["value"] == "1650"
        assert row["point_conditions"]["T_C"]["locator"]["page"] == (
            _page_from_grep("1.5 GPa and 1,650 °C")[0]
        )
        assert row["point_conditions"]["total_pressure_Pa"]["state"]["value"] == (
            "1500000000"
        )
        assert row["point_conditions"]["total_pressure_Pa"]["value_as_published"] == "1.5 GPa"
        assert row["point_conditions"]["total_pressure_Pa"]["locator"]["page"] == (
            _page_from_grep("Compositions of phases produced at 1.5 GPa/1,650 °C")[0]
        )
        assert row["point_conditions"]["fO2_log"]["state"]["tag"] == "unknown"
        assert row["point_conditions"]["composition"]["state"]["tag"] == "unknown"
        assert "No uncertainty width is printed" in row["uncertainty"]["note"]


def _assert_migrated_pins(result) -> None:
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 3
    assert len(result.works) == 1
    assert sum(row.value.kind.value == "point" for row in result.observations.values()) == 3
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 0
    quantity_unknown = {
        row.observation_id
        for row in result.observations.values()
        if not row.identity.quantity.is_value
    }
    phase_unknown = {
        row.observation_id
        for row in result.observations.values()
        if row.identity.species is None or not row.identity.species.phase.is_value
    }
    assert quantity_unknown == set()
    assert phase_unknown == set()

    for observation_id, expected in EXPECTED_OBSERVATIONS.items():
        row = result.observations[f"{SOURCE_ID}::{observation_id}"]
        assert row.experiment_id in result.experiments
        expected_parents = (
            f"{SOURCE_ID}::context::wood_wade_2013_bw1102_table1_partitioning_inputs",
            f"{SOURCE_ID}::context::wood_wade_2013_metal_silicate_method_and_constants",
        )
        assert row.derived_from == expected_parents
        assert row.derivation is not None
        assert row.derivation.inputs == expected_parents
        assert row.value.kind.value == "point"
        assert row.value.point == Decimal(expected["value"])
        assert row.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
        assert row.identity.species is not None
        assert row.identity.species.formula == expected["species"]
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.per is not None
        assert row.identity.per.value is PerBasis.DIMENSIONLESS
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1923")
        assert row.identity.total_pressure_Pa.is_value
        assert row.identity.total_pressure_Pa.value == Decimal("1500000000")
        assert row.identity.fO2_Pa.is_unknown
        assert row.identity.composition.is_unknown
        profile = profile_for(row.identity)
        assert "temperature_K" in profile.required
        assert "composition" in profile.required
        assert "fO2_Pa" in profile.required
        assert "total_pressure_Pa" in profile.required
        assert row.identity.reference_state.is_value
        standard_state = row.identity.reference_state.value
        assert standard_state.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        assert standard_state.endmember.formula == expected["species"]
        assert standard_state.endmember.phase.value is Phase.L
        assert standard_state.reference_pressure_bar == Decimal("1")
        assert row.evidence.class_.value is expected["method_class"]
        output_page = _page_from_grep(expected["proof"])[0]
        assert row.locator.page == output_page
        assert row.point_conditions["T_K"].state.value == Decimal("1923")
        assert row.point_conditions["T_K"].locator.page == output_page
        run_page = _page_from_grep("1.5 GPa and 1,650 °C")[0]
        table_page = _page_from_grep(
            "Compositions of phases produced at 1.5 GPa/1,650 °C"
        )[0]
        assert row.point_conditions["T_C"].state.value == Decimal("1650")
        assert row.point_conditions["T_C"].locator.page == run_page
        assert row.point_conditions["total_pressure_Pa"].locator.page == table_page
        assert row.point_conditions["total_pressure_Pa"].state.value == Decimal(
            "1500000000"
        )
        assert row.point_conditions["fO2_log"].state.is_unknown
        assert row.point_conditions["fO2_log"].locator.page == run_page
        assert row.point_conditions["composition"].state.is_unknown
        assert row.point_conditions["composition"].locator.page == table_page

    uncertainty_kinds = Counter(row.uncertainty.kind for row in result.observations.values())
    assert uncertainty_kinds == {UncertaintyKind.PRINTED: 3}
    confidence_levels = Counter(
        (row.uncertainty.verbatim or {}).get("confidence_level", "not_present")
        for row in result.observations.values()
    )
    assert confidence_levels == Counter({"not_present": 3})


def test_wood_wade_2013_pins_three_prose_coefficients_and_table_census() -> None:
    document = _document()
    _assert_source_value_pins(document)
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    assert len(PRINTED_TABLE_ROWS) == 21
    mapping = yaml.safe_load(TABLE_MAPPING.read_text(encoding="utf-8"))
    assert mapping["tables"][0]["table_row_count"] == 21
    table_context = _table_input_context(document)
    assert table_context["values"]["experiment_id_as_printed"] == "BW1102"
    assert table_context["values"]["gamma_FeO_as_printed"] == "0.92"
    assert "Calculated from Eq. 4" in table_context["values"]["gamma_FeO_note"]
    assert table_context["values"]["table_input_uncertainty_confidence_level"] == "one_sigma"
    assert table_context["values"]["table_footnote_as_printed"] == (
        "Value in brackets is 1 std dev."
    )
    method_values = _method_constants_context(document)["values"]
    assert method_values["gamma_FeO_activity_calculation_as_printed"] == (
        "The activity coefficient of FeO in this experiment at 1,650 °C "
        "is calculated to be 0.92 from Eq. (4)."
    )
    calculated_page = _page_from_grep("calculated to be 0.92 from Eq. (4)")
    assert (
        method_values["gamma_FeO_activity_calculation_locator"]["page"],
        method_values["gamma_FeO_activity_calculation_locator"]["pdf_page_index"],
        method_values["gamma_FeO_activity_calculation_locator"]["published_page"],
    ) == calculated_page
    model_fit = method_values["gamma_FeO_model_fit_provenance"]
    assert model_fit["literature_data_count_as_printed"] == (
        "83 data from the literature"
    )
    assert model_fit["fit_description_as_printed"] == (
        "The best-fit equation, excluding terms which are not significant"
    )
    assert model_fit["fitted_composition_range_as_printed"] == (
        "39 to 64 % SiO2, 0.3 to 30 % FeO and 0 to14.8 % Na2O"
    )
    model_fit_page = _page_from_grep("83 data from the literature")
    assert (
        model_fit["locator"]["page"],
        model_fit["locator"]["pdf_page_index"],
        model_fit["locator"]["published_page"],
    ) == model_fit_page
    in_entropy = method_values["inO1.5_logK_melting_entropy_assumption"]
    assert in_entropy["assumption_as_printed"] == (
        "assume an entropy of melting of InO1.5 at the melting point of 2,183 K"
    )
    assert in_entropy["value_as_printed"] == "26.25 J.K-1"
    assert in_entropy["analogues_as_printed"] == (
        "GaO1.5 (26.3) and VO1.5 (25.05)"
    )
    entropy_page = _page_from_grep("26.25 J.K-1")
    assert (
        in_entropy["locator"]["page"],
        in_entropy["locator"]["pdf_page_index"],
        in_entropy["locator"]["published_page"],
    ) == entropy_page
    expected_cells = {
        (row, phase, label): value
        for row, phase, label, value in EXPECTED_TABLE_CELLS
    }
    assert len(expected_cells) == 16
    assert _stored_table_cells(document) == expected_cells

    with CELL_AUDIT.open(encoding="utf-8", newline="") as stream:
        audited = list(csv.DictReader(stream))
    assert len(audited) == 16
    assert {
        (int(row["table_row"]), row["phase"], row["column_label"]): row[
            "cell_as_printed"
        ]
        for row in audited
    } == expected_cells
    render_pins = {
        (int(row["table_row"]), row["phase"], row["column_label"]): row[
            "cell_as_printed"
        ]
        for row in audited
        if "digits" in row["render_check"]
    }
    assert render_pins == {
        (3, "Silicate", "SiO2"): "59.4 (0.4)",
        (11, "Silicate", "Cu"): "51 (7)",
        (18, "Metal", "Cu"): "2,270 (37)",
    }
    assert {row["verification_method"][1] for row in audited} == {"a"}
    table_page, table_pdf_page_index, _ = _page_from_grep(
        "Compositions of phases produced at 1.5 GPa/1,650 °C"
    )
    assert {int(row["physical_pdf_page"]) for row in audited} == {table_page}
    assert {int(row["pdf_page_index"]) for row in audited} == {table_pdf_page_index}


def test_wood_wade_2013_isolated_migration_reaches_required_identity_axes(
    tmp_path: Path,
) -> None:
    document = _document()
    result = _migrate_real_extract(
        tmp_path / "original",
        EXTRACT.name,
        write=False,
        extract_doc=document,
    )
    _assert_migrated_pins(result)

    contexts = [
        row
        for rows in result.context_by_work.values()
        for row in rows
    ]
    assert {row.get("experiment") for row in contexts} == {
        "wood_wade_2013_bw1102"
    }
    context_ids = {row["context_id"] for row in contexts}
    assert len(context_ids) == 3
    assert f"{SOURCE_ID}::context::wood_wade_2013_bw1102_table1_partitioning_inputs" in context_ids
    assert f"{SOURCE_ID}::context::wood_wade_2013_metal_silicate_method_and_constants" in context_ids
    assert f"{SOURCE_ID}::context::wood_wade_2013_volatility_figure_context" in context_ids

    assert len(result.experiments) == 1
    experiment = next(iter(result.experiments.values()))
    assert experiment.experiment_id == (
        "10.1007/s00410-013-0896-z::experiment::wood_wade_2013_bw1102"
    )
    assert experiment.method.is_unknown
    assert experiment.conditions["T_C"].state.value == Decimal("1650")
    assert experiment.conditions["T_C"].locator.page == _page_from_grep(
        "1.5 GPa and 1,650 °C"
    )[0]
    assert experiment.pressure_environment.total_pressure_Pa.state.value.point == (
        Decimal("1500000000")
    )
    assert experiment.pressure_environment.total_pressure_Pa.locator.page == (
        _page_from_grep("Compositions of phases produced at 1.5 GPa/1,650 °C")[0]
    )
    assert experiment.sample.initial_composition is not None
    assert experiment.sample.initial_composition.state.is_unknown
    assert experiment.sample.initial_composition.locator.page == _page_from_grep(
        "starting silicate composition of Diopside28–Forsterite22–Anorthite50"
    )[0]
    assert experiment.sample.printed_composition is not None
    assert experiment.sample.printed_composition.state.value == (
        "Initial bulk recipe: Diopside28–Forsterite22–Anorthite50"
    )
    assert experiment.apparatus is not None
    assert experiment.apparatus.cell_material_and_liner.state.value == "SiO2 glass"

    mutated = copy.deepcopy(document)
    mutated_row = _observation_by_id(
        mutated, "wood_wade_2013_pbo_activity_coefficient"
    )
    mutated_row["values"]["gamma"] = "0.23"
    mutated_row["values"]["gamma_as_printed"] = "0.23"
    with pytest.raises(AssertionError):
        _assert_source_value_pins(mutated)
