"""Regression pins for the corrected chlorine-capacity workbook."""

from __future__ import annotations

from copy import deepcopy
import re
import subprocess
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook
import pytest
import yaml

from simulator.battery.enums import (
    AmountBasis,
    MethodToken,
    NoticeKind,
    Phase,
    PhaseRole,
    Quantity,
    ValueKind,
)
from simulator.battery.input_provenance import PRINTED_STARTING_BULK
from simulator.battery.migrate import (
    _wt_percent_conversion_or_refusal,
    wt_pct_to_mole_fraction,
)
from tests.battery.test_migrate import REPO_ROOT, _migrate_real_extract
from tests.helpers.corpus_paths import regolith_corpus_root


SOURCE_ID = "rusiecka-2026-chlorine-data"
EXTRACT_NAME = f"{SOURCE_ID}.yaml"
EXTRACT_PATH = REPO_ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME
WORKBOOK_NAME = "rusiecka-2026-chlorine-data.xlsx"


def _phase_assay_product(wt: dict[str, Decimal]):
    result = _wt_percent_conversion_or_refusal(
        wt,
        closure_context={
            "quantity": Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value,
        },
        declared_component_map=wt,
    )
    assert result.composition is not None
    assert result.refusal is None
    return result.composition


def _assert_phase_assay_product(actual, expected) -> None:
    actual_components = dict(actual.components)
    expected_components = dict(expected.components)
    assert actual_components.keys() == expected_components.keys()
    assert all(
        abs(actual_components[name] - expected_components[name]) < Decimal("1e-24")
        for name in actual_components
    )


def _workbook_or_skip() -> Path:
    workbook = regolith_corpus_root() / "raw" / SOURCE_ID / WORKBOOK_NAME
    if not workbook.is_file():
        pytest.skip(f"source workbook is absent: {workbook}")
    return workbook


def _displayed_decimal(cell) -> Decimal:
    value = Decimal(str(cell.value))
    if cell.number_format == "0.00":
        return Decimal(f"{value:.2f}")
    if cell.number_format == "0.00E+00":
        return Decimal(f"{value:.2E}")
    return Decimal(format(value, ".15g"))


def _assert_projected_compositions_without_workbook(doc: dict, result, tmp_path: Path) -> None:
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
    }
    mapped = []
    mapped_rows = set()
    for container in doc["species"].values():
        for source in container.get("observations", []):
            values = source.get("values", {})
            if (
                values.get("quantity") != "equilibrium_phase_composition"
                or "composition_wt_pct" not in values
            ):
                continue
            row_number = int(
                re.search(r"row(\d+)_", source["observation_id"]).group(1)
            )
            mapped_rows.add(row_number)
            mapped.append((row_number, source, migrated[source["observation_id"]]))
    assert len(mapped) == 1241
    assert len(mapped_rows) == 73

    for row_number, source, row in mapped:
        source_components = source["values"]["composition_wt_pct"]
        printed_composition = row.point_conditions["printed_composition"]
        assert printed_composition.state.is_value
        assert printed_composition.state.value == source_components
        assert printed_composition.locator is not None
        assert printed_composition.locator.record == (
            source["locator"]["record"]
        )
        point_composition = row.point_conditions["composition"]
        assert point_composition.locator is not None
        assert point_composition.locator.record == (
            f"sheet data, cells B{row_number}:R{row_number}"
        )
        if row_number in {58, 59}:
            excluded_re = "4.36" if row_number == 58 else "4.32"
            analysis_total = "94.66" if row_number == 58 else "93.53"
            printed_components = source["values"][
                "composition_wt_pct_as_published"
            ]
            assert printed_components["Re"] == excluded_re
            assert "Re" not in source_components
            assert row.identity.composition.is_unknown
            assert f"kept sum={analysis_total} wt% is below 95 wt%" in (
                row.identity.composition.reason or ""
            )
            assert point_composition.state.is_unknown
            assert point_composition.locator is not None
            notices = [
                notice
                for notice in row.notices
                if notice.kind is NoticeKind.COMPOSITION_PROJECTED
            ]
            assert len(notices) == 1
            assert notices[0].dropped == ("Re",)
            assert f"Re={excluded_re} wt%" in notices[0].reason
            assert "physical page 7" in notices[0].reason
            assert f"N{row_number}" in notices[0].reason
            assert f"{analysis_total} wt%" in notices[0].reason
            assert "no mole-fraction normalization was applied" in notices[0].reason
            continue

        assert row.identity.composition.is_value
        composition = row.identity.composition.value
        assert composition.basis == "printed_oxides"
        assert composition.amount_basis is AmountBasis.MOLE_FRACTION
        projected = dict(composition.components)
        assert abs(sum(projected.values(), Decimal("0")) - Decimal("1")) < Decimal("1e-24")
        if composition.input_provenance == PRINTED_STARTING_BULK:
            initial = result.experiments[row.experiment_id].sample.initial_composition
            assert initial is not None and initial.state.is_value
            assert composition.components == initial.state.value.components
        elif Decimal(source_components["Cl"]) > 0:
            assert projected["Cl"] > 0
        expected_product = _phase_assay_product(
            {
                name: Decimal(str(amount))
                for name, amount in printed_composition.state.value.items()
            }
        )
        assert point_composition.state.is_value
        assert point_composition.state.value.amount_basis is AmountBasis.MOLE_FRACTION
        _assert_phase_assay_product(
            point_composition.state.value, expected_product
        )
        if row_number == 81:
            assert Decimal(str(printed_composition.state.value["Cl"])) == Decimal("1.83")
            assert dict(point_composition.state.value.components)["Cl"].quantize(
                Decimal("0.000000000001")
            ) == Decimal("0.034009940171")
        assert point_composition.inference is not None
        assert point_composition.inference.relation == "wt_pct_to_mole_fraction"
        assert point_composition.inference.output_unit == "mole_fraction"
        assert "x_i = (w_i / M_i) / Σ_j (w_j / M_j)" in point_composition.inference.inputs
        assert any(
            name == "original_Cl_wt_pct"
            for name, _value in point_composition.inference.parameters
        )
        assert row.identity.temperature_K.is_value
        fO2_condition_name = (
            "fO2_Pa" if 81 <= row_number <= 96 else "fO2_bar"
        )
        for condition_name in (
            "total_pressure_Pa",
            fO2_condition_name,
            "fCl2_bar",
        ):
            condition = row.point_conditions[condition_name]
            assert condition.state.is_value
            assert condition.locator is not None
        if 81 <= row_number <= 96:
            assert row.identity.fO2_Pa.is_value
            expected_fO2_pa = Decimal(
                source["values"]["point_conditions"]["fO2_Pa"]["state"]["value"]
            )
            assert row.identity.fO2_Pa.value == expected_fO2_pa
            assert row.point_conditions["fO2_Pa"].state.value == expected_fO2_pa
            assert row.point_conditions["fO2_Pa"].locator is not None
            assert "fO2_bar" not in row.point_conditions
            if row_number == 81:
                assert row.identity.fO2_Pa.value == Decimal("0.0000554")
    mutated_doc = deepcopy(doc)
    mutated_si = next(
        observation
        for container in mutated_doc["species"].values()
        for observation in container.get("observations", [])
        if observation["observation_id"] == "rusiecka_2026_data_row6_sio2"
    )
    mutated_components = mutated_si["values"]["composition_wt_pct"]
    assert mutated_components is mutated_si["point_conditions"]["composition"]["state"]["value"]["components"]
    mutated_components["Cl"] = "0.92"
    mutated_result = _migrate_real_extract(
        tmp_path / "chloride-mutated", EXTRACT_NAME, extract_doc=mutated_doc
    )
    base_projection = dict(
        migrated["rusiecka_2026_data_row6_sio2"].identity.composition.value.components
    )
    changed = next(
        row
        for row in mutated_result.observations.values()
        if row.observation_id.endswith("::rusiecka_2026_data_row6_sio2")
    )
    changed_projection = dict(changed.identity.composition.value.components)
    assert changed_projection["Cl"] > base_projection["Cl"]
    assert changed_projection["SiO2"] < base_projection["SiO2"]


def test_corrected_rows_migrate_and_pin_workbook_cells(tmp_path: Path) -> None:
    workbook = regolith_corpus_root() / "raw" / SOURCE_ID / WORKBOOK_NAME
    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    result = _migrate_real_extract(tmp_path / "migration", EXTRACT_NAME)
    assert result.validation is not None
    assert not result.validation.hard_issues, result.validation.hard_issues
    if not workbook.is_file():
        _assert_projected_compositions_without_workbook(doc, result, tmp_path)
        return
    ws = load_workbook(workbook, data_only=True, read_only=False)["data"]

    raw_rows = {
        observation["observation_id"]: (formula, observation)
        for formula, container in doc["species"].items()
        for observation in container.get("observations", [])
    }
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
    }
    capacities = [
        (formula, observation)
        for formula, observation in raw_rows.values()
        if observation["values"].get("quantity") == "chloride_capacity"
    ]
    compositions = [
        (formula, observation)
        for formula, observation in raw_rows.values()
        if observation["values"].get("quantity") == "equilibrium_phase_composition"
    ]
    emitted_rows = (
        set(range(6, 7))
        | set(range(9, 22))
        | set(range(23, 27))
        | set(range(34, 40))
        | set(range(40, 56))
        | set(range(57, 71))
        | set(range(75, 78))
    )
    expected_component_cells = sum(
        ws.cell(row, column).value is not None
        for row in (*sorted(emitted_rows), *range(81, 129))
        for column in range(2, 19)
    )
    emitted_experiments = {
        observation["experiment"]
        for _, observation in compositions
        if int(re.search(r"row(\d+)_", observation["observation_id"]).group(1)) in emitted_rows
    }
    assert len(emitted_experiments) == 57
    assert len(capacities) == 126
    assert len(compositions) == expected_component_cells == 1785
    assert len(raw_rows) == len(migrated) == 1911

    for formula, observation in capacities:
        row_number = int(re.search(r"row(\d+)_chloride_capacity$", observation["observation_id"]).group(1))
        row = migrated[observation["observation_id"]]
        assert formula == row.identity.species.formula == "Cl"
        assert row.identity.quantity.value is Quantity.CHLORIDE_CAPACITY
        assert row.identity.phase_role.value is PhaseRole.MELT
        assert row.identity.species.phase.value is Phase.L
        assert observation["type"] == "chloride_capacity"
        assert row.value.kind is ValueKind.POINT
        assert not row.notices
        assert row.value.point == Decimal(f"{ws.cell(row_number, 28).value:.2f}")
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal(str(ws.cell(row_number, 20).value)) + Decimal("273.15")
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        if 81 <= row_number <= 96:
            assert "fO2_Pa" not in row.point_conditions
        assert row.experiment_id.endswith(f"::experiment::worksheet-row-{row_number}")
        assert row.point_conditions["total_pressure_Pa"].state.value == (
            _displayed_decimal(ws.cell(row_number, 21)) * Decimal("1000000000")
        )
        assert row.point_conditions["total_pressure_Pa"].locator is not None
        assert row.point_conditions["fCl2_bar"].state.is_value
        assert row.point_conditions["fCl2_bar"].locator is not None
        assert row.point_conditions["fCl2_bar"].state.value == _displayed_decimal(
            ws.cell(row_number, 24)
        )
        assert row.point_conditions["fO2_bar"].state.is_value
        assert row.point_conditions["fO2_bar"].locator is not None
        assert row.point_conditions["fO2_bar"].state.value == _displayed_decimal(
            ws.cell(row_number, 25)
        )
        if row_number == 81:
            assert row.point_conditions["fO2_bar"].state.value == Decimal("5.54E-10")
        assert "fO2_Pa" not in row.point_conditions
        assert "fO2_log" not in row.point_conditions

    for formula, observation in compositions:
        match = re.search(r"cell ([A-Z]+)(\d+)$", observation["locator"]["record"])
        assert match is not None
        column_letters, row_text = match.groups()
        row_number = int(row_text)
        raw_id = observation["observation_id"]
        row = migrated[raw_id]
        source_cell = ws[f"{column_letters}{row_number}"]
        displayed_component = _displayed_decimal(source_cell)
        assert row.identity.species.formula == formula
        assert row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert row.identity.phase_role.value is PhaseRole.GLASS
        assert row.identity.species.phase.value is Phase.GLASS
        assert row.identity.subtype.value == "wt_percent"
        assert row.identity.temperature_K.value == Decimal(str(ws.cell(row_number, 20).value)) + Decimal("273.15")
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == displayed_component
        assert row.identity.total_pressure_Pa.is_value
        assert row.identity.total_pressure_Pa.value == (
            _displayed_decimal(ws.cell(row_number, 21)) * Decimal("1000000000")
        )
        if 81 <= row_number <= 96:
            expected_fO2_pa = _displayed_decimal(ws.cell(row_number, 25)) * Decimal(
                "100000"
            )
            assert row.identity.fO2_Pa.is_value
            assert row.identity.fO2_Pa.value == expected_fO2_pa
            assert row.point_conditions["fO2_Pa"].state.value == expected_fO2_pa
            assert row.point_conditions["fO2_Pa"].locator is not None
            assert row.point_conditions["fO2_Pa"].locator.record == (
                f"sheet data, cell Y{row_number}"
            )
            assert "fO2_bar" not in row.point_conditions
            if row_number == 81:
                assert row.identity.fO2_Pa.value == Decimal("0.0000554")
        else:
            assert row.identity.fO2_Pa.is_unknown
        assert row.experiment_id.endswith(f"::experiment::worksheet-row-{row_number}")
        assert row.point_conditions["total_pressure_Pa"].state.value == (
            _displayed_decimal(ws.cell(row_number, 21)) * Decimal("1000000000")
        )
        assert row.point_conditions["total_pressure_Pa"].locator is not None
        if 81 <= row_number <= 96:
            assert row.point_conditions["fO2_Pa"].state.value == (
                _displayed_decimal(ws.cell(row_number, 25)) * Decimal("100000")
            )
            assert row.point_conditions["fO2_Pa"].locator is not None
        else:
            assert row.point_conditions["fO2_bar"].state.value == _displayed_decimal(
                ws.cell(row_number, 25)
            )
            assert row.point_conditions["fO2_bar"].locator is not None
        assert row.point_conditions["fCl2_bar"].state.value == _displayed_decimal(
            ws.cell(row_number, 24)
        )
        assert row.point_conditions["fCl2_bar"].locator is not None
        assert all(
            notice.kind
            in {NoticeKind.COMPOSITION_PROJECTED, NoticeKind.TOTAL_IRON_REPORTED_AS_FEO}
            for notice in row.notices
        )
        if row_number in {58, 59}:
            projection_notices = [
                notice
                for notice in row.notices
                if notice.kind is NoticeKind.COMPOSITION_PROJECTED
            ]
            assert len(projection_notices) == 1
            projection_notice = projection_notices[0]
            excluded_re = "4.36" if row_number == 58 else "4.32"
            analysis_total = "94.66" if row_number == 58 else "93.53"
            assert projection_notice.dropped == ("Re",)
            assert f"Re={excluded_re} wt%" in projection_notice.reason
            assert "physical page 7" in projection_notice.reason
            assert f"N{row_number}" in projection_notice.reason
            assert f"{analysis_total} wt%" in projection_notice.reason
            assert "no mole-fraction normalization was applied" in projection_notice.reason

    bound_compositions = [
        observation
        for _, observation in compositions
        if "composition_wt_pct" in observation["values"]
    ]
    assert len(bound_compositions) == 1241
    bound_rows = set()
    for observation in bound_compositions:
        row_number = int(re.search(r"row(\d+)_", observation["observation_id"]).group(1))
        expected_composition = {
            str(ws.cell(2, column).value).removesuffix("*"): str(
                _displayed_decimal(ws.cell(row_number, column))
            )
            for column in range(2, 19)
        }
        assert observation["values"].get(
            "composition_wt_pct_as_published",
            observation["values"]["composition_wt_pct"],
        ) == expected_composition
        assert observation["values"]["composition_locator"] == {
            "source_path": (
                "corpus/raw/rusiecka-2026-chlorine-data/"
                "rusiecka-2026-chlorine-data.xlsx"
            ),
            "record": f"sheet data, cells B{row_number}:R{row_number}",
        }
        row = migrated[observation["observation_id"]]
        printed = row.point_conditions["printed_composition"]
        assert printed.state.is_value
        assert printed.state.value == observation["values"]["composition_wt_pct"]
        assert printed.locator is not None
        assert printed.locator.record == observation["locator"]["record"]
        bound_rows.add(row_number)
        located = row.point_conditions["composition"]
        assert located.locator is not None
        assert located.locator.record == (
            f"sheet data, cells B{row_number}:R{row_number}"
        )
        if row_number in {58, 59}:
            analysis_total = "94.66" if row_number == 58 else "93.53"
            assert row.identity.composition.is_unknown
            assert f"kept sum={analysis_total} wt% is below 95 wt%" in (
                row.identity.composition.reason or ""
            )
            continue
        assert row.identity.composition.is_value
        composition = row.identity.composition.value
        assert composition.basis == "printed_oxides"
        assert composition.amount_basis is AmountBasis.MOLE_FRACTION
        projected = dict(composition.components)
        assert abs(sum(projected.values(), Decimal("0")) - Decimal("1")) < Decimal("1e-24")
        if composition.input_provenance == PRINTED_STARTING_BULK:
            initial = result.experiments[row.experiment_id].sample.initial_composition
            assert initial is not None and initial.state.is_value
            assert composition.components == initial.state.value.components
        elif Decimal(expected_composition["Cl"]) > 0:
            assert "Cl" in projected
        assert located.state.is_value
        assert located.state.value.amount_basis is AmountBasis.MOLE_FRACTION
        expected_product = _phase_assay_product(
            {
                name: Decimal(str(amount))
                for name, amount in printed.state.value.items()
            }
        )
        _assert_phase_assay_product(located.state.value, expected_product)
        if row_number == 81:
            assert Decimal(str(printed.state.value["Cl"])) == Decimal("1.83")
            assert dict(located.state.value.components)["Cl"].quantize(
                Decimal("0.000000000001")
            ) == Decimal("0.034009940171")
        assert located.inference is not None
        assert located.inference.relation == "wt_pct_to_mole_fraction"
        assert located.inference.output_unit == "mole_fraction"
        assert "x_i = (w_i / M_i) / Σ_j (w_j / M_j)" in located.inference.inputs
        assert any(name == "original_Cl_wt_pct" for name, _value in located.inference.parameters)
        if row_number in {58, 59}:
            assert "Re" not in projected
            assert not any(name == "original_Re_wt_pct" for name, _value in located.inference.parameters)
    assert len(bound_rows) == 73

    # Independent cell pins: Lunar-1 is a named legacy composition row, while AND-1 is this study.
    assert ws["AC22"].value == "Lunar-1"
    assert ws["B22"].value == 45.77
    assert ws["K22"].value == 2.03
    assert ws["T22"].value == 1400
    assert ws["U22"].value == 1.63
    assert round(ws["AB22"].value, 2) == 0.20
    assert "rusiecka_2026_data_row22_sio2" not in migrated
    assert "rusiecka_2026_data_row22_cl" not in migrated
    assert migrated["rusiecka_2026_data_row22_chloride_capacity"].value.point == Decimal("0.20")

    # The emitted blocks are independently pinned to their source cells.
    for row_number in (6, 9, 23, 34, 40, 70):
        observation_id = f"rusiecka_2026_data_row{row_number}_sio2"
        assert migrated[observation_id].value.point == _displayed_decimal(ws.cell(row_number, 2))
    assert ws["AC40"].value == "AgI/Cl-002"
    assert ws["B40"].value == 45.27
    assert migrated["rusiecka_2026_data_row40_sio2"].value.point == Decimal("45.27")
    assert ws["AC55"].value == "AgI/Cl-026"
    assert ws["B55"].value == 37.23
    assert migrated["rusiecka_2026_data_row55_sio2"].value.point == Decimal("37.23")
    assert "rusiecka_2026_data_row56_sio2" not in migrated
    assert "rusiecka_2026_data_row56_cl" not in migrated
    assert ws["AC75"].value == "ICB-1"
    assert ws["B75"].value == 51.17
    assert ws["K75"].value == 1.7
    assert migrated["rusiecka_2026_data_row75_sio2"].value.point == Decimal("51.17")
    assert migrated["rusiecka_2026_data_row75_cl"].value.point == Decimal("1.7")
    assert migrated["rusiecka_2026_data_row75_i2o"].value.point == Decimal("0.3")
    assert migrated["rusiecka_2026_data_row75_ag2o"].value.point == Decimal("0.1")
    assert "rusiecka_2026_data_row71_sio2" not in migrated
    assert ws["AC71"].value == "ICB-4"

    assert ws["AC81"].value == "AND-1"
    assert ws["T81"].value == 1200
    assert ws["U81"].value == 1.05
    assert f"{ws['X81'].value:.2E}" == "2.80E-05"
    assert f"{ws['Y81'].value:.2E}" == "5.54E-10"
    assert round(ws["AA81"].value, 2) == 1.68
    assert round(ws["AB81"].value, 2) == 0.23
    and1 = migrated["rusiecka_2026_data_row81_chloride_capacity"]
    assert and1.value.point == Decimal("0.23")
    assert and1.point_conditions["total_pressure_Pa"].state.value == Decimal("1050000000.00")
    assert and1.point_conditions["fO2_bar"].state.value == Decimal("5.54E-10")

    mutated_doc = deepcopy(doc)
    mutated_si = next(
        observation
        for container in mutated_doc["species"].values()
        for observation in container.get("observations", [])
        if observation["observation_id"] == "rusiecka_2026_data_row6_sio2"
    )
    mutated_components = mutated_si["values"]["composition_wt_pct"]
    assert mutated_components is mutated_si["point_conditions"]["composition"]["state"]["value"]["components"]
    mutated_components["Cl"] = "0.92"
    mutated_result = _migrate_real_extract(
        tmp_path / "chloride-mutated", EXTRACT_NAME, extract_doc=mutated_doc
    )
    base_projection = dict(migrated["rusiecka_2026_data_row6_sio2"].identity.composition.value.components)
    mutated_row = next(
        row
        for row in mutated_result.observations.values()
        if row.observation_id.endswith("::rusiecka_2026_data_row6_sio2")
    )
    changed_projection = dict(mutated_row.identity.composition.value.components)
    assert changed_projection["Cl"] > base_projection["Cl"]
    assert changed_projection["SiO2"] < base_projection["SiO2"]


def test_this_study_experiments_migrate_with_reported_method(tmp_path: Path) -> None:
    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    result = _migrate_real_extract(tmp_path / "method-migration", EXTRACT_NAME)
    rows = range(81, 97)
    experiments = {
        experiment_id.rsplit("::", 1)[-1]: experiment
        for experiment_id, experiment in result.experiments.items()
    }
    source_experiments = {
        experiment["experiment_id"]: experiment for experiment in doc["experiments"]
    }

    for row in rows:
        experiment_id = f"worksheet-row-{row}"
        experiment = experiments[experiment_id]
        assert experiment.method.is_value
        assert experiment.method.value is MethodToken.PISTON_CYLINDER
        assert experiment.locator.source_path == (
            "corpus/raw/rusiecka-2026-chlorine-data/"
            "rusiecka-2026-chlorine-data.xlsx"
        )
        assert experiment.locator.record == f"sheet data, cell AC{row}"
        assert source_experiments[experiment_id]["locator"] == {
            "source_path": (
                "corpus/raw/rusiecka-2026-chlorine-data/"
                "rusiecka-2026-chlorine-data.xlsx"
            ),
            "record": f"sheet data, cell AC{row}",
        }
        assert source_experiments[experiment_id]["method_locator"] == {
            "source_path": (
                "corpus/raw/rusiecka-wood-2026-chlorine-hcl/"
                "rusiecka-wood-2026-chlorine-hcl.pdf"
            ),
            "page": 3,
            "section": "Experimental procedure",
        }

    mutated_doc = deepcopy(doc)
    experiment = next(
        item
        for item in mutated_doc["experiments"]
        if item["experiment_id"] == "worksheet-row-81"
    )
    experiment["method"] = {"tag": "unknown", "reason": "not_published"}
    mutated_result = _migrate_real_extract(
        tmp_path / "method-mutated", EXTRACT_NAME, extract_doc=mutated_doc
    )
    mutated = next(
        item
        for experiment_id, item in mutated_result.experiments.items()
        if experiment_id.endswith("::experiment::worksheet-row-81")
    )
    assert mutated.method.is_unknown


def test_companion_table2_locator_and_sample_list_disagreement() -> None:
    workbook = _workbook_or_skip()
    corpus = regolith_corpus_root()
    pdf = corpus / "raw" / "rusiecka-wood-2026-chlorine-hcl" / "rusiecka-wood-2026-chlorine-hcl.pdf"
    if not pdf.is_file():
        pytest.skip(f"companion paper PDF is absent: {pdf}")
    ws = load_workbook(workbook, data_only=True, read_only=False)["data"]
    result = subprocess.run(
        ["pdftotext", "-f", "5", "-l", "5", "-layout", str(pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    page = re.sub(r"\s+", " ", result.stdout)
    worksheet_ids = {ws.cell(row, 29).value for row in range(81, 97)}
    assert "AND-7" not in worksheet_ids
    assert "DCB-6" in worksheet_ids
    assert "AND-7" in page
    assert "DCB-6" not in page
    assert "6.08E-06" in page
    assert "4.44E-11" in page


def test_starting_materials_bind_to_all_2026_experiments(tmp_path: Path) -> None:
    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    article_pdf = (
        regolith_corpus_root()
        / "raw"
        / "rusiecka-wood-2026-chlorine-hcl"
        / "rusiecka-wood-2026-chlorine-hcl.pdf"
    )
    if not article_pdf.is_file():
        pytest.skip(f"companion paper PDF is absent: {article_pdf}")
    table_page = subprocess.run(
        ["pdftotext", "-f", "2", "-l", "2", "-layout", str(article_pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    quench_page = subprocess.run(
        ["pdftotext", "-f", "3", "-l", "3", "-layout", str(article_pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Table 1 Anhydrous compositions of the starting materials" in table_page
    assert "Page 2 of 15" in table_page
    for render_pin in ("62.49", "77.30", "5.36", "22.65"):
        assert render_pin in table_page
    assert "The charges contained glass" in quench_page
    assert "Page 3 of 15" in quench_page
    expected_rows = {
        81: "AND",
        82: "HSR",
        83: "AND",
        84: "AND",
        85: "AND",
        86: "AND",
        87: "AND",
        88: "DCB",
        89: "DCB",
        90: "DCB-Al",
        91: "DCB",
        92: "DCB",
        93: "HSR",
        94: "HSR",
        95: "DCB",
        96: "DCB",
    }
    printed = {
        "AND": {
            "SiO2": "62.49", "TiO2": "0.62", "Al2O3": "18.20", "FeOT": "3.99",
            "MgO": "3.47", "CaO": "6.85", "Na2O": "3.35", "K2O": "1.03",
        },
        "HSR": {
            "SiO2": "77.30", "TiO2": "0.13", "Al2O3": "12.77", "FeOT": "1.62",
            "MgO": "0.12", "CaO": "1.02", "Na2O": "3.52", "K2O": "3.46",
        },
        "DCB": {
            "SiO2": "66.76", "TiO2": "1.02", "Al2O3": "14.62", "FeOT": "5.36",
            "MgO": "1.42", "CaO": "4.02", "Na2O": "4.64", "K2O": "2.16",
        },
        "DCB-Al": {
            "SiO2": "64.49", "TiO2": "1.01", "Al2O3": "22.65", "FeOT": "1.24",
            "MgO": "1.67", "CaO": "4.14", "Na2O": "3.33", "K2O": "1.47",
        },
    }
    source_experiments = {
        int(run["experiment_id"].removeprefix("worksheet-row-")): run
        for run in doc["experiments"]
        if run["experiment_id"].startswith("worksheet-row-")
        and int(run["experiment_id"].removeprefix("worksheet-row-")) in expected_rows
    }
    assert set(source_experiments) == set(expected_rows)

    for row_number, sample_code in expected_rows.items():
        initial = source_experiments[row_number]["sample"]["initial_composition"]
        assert initial["state"]["value"]["components"] == printed[sample_code]
        assert initial["locator"]["page"] == 2
        assert initial["locator"]["pdf_page_index"] == 1
        assert initial["locator"]["published_page"] == 2
        assert initial["locator"]["table"] == "1"
        assert initial["locator"]["row"] == sample_code

    result = _migrate_real_extract(tmp_path / "starting-composition", EXTRACT_NAME)
    assert result.validation is not None
    assert not result.validation.hard_issues, result.validation.hard_issues
    assert len(result.observations) == 1911
    target_runs = []
    expected_start_by_experiment = {}
    for row_number, sample_code in expected_rows.items():
        experiment = next(
            migrated
            for migrated in result.experiments.values()
            if migrated.experiment_id.endswith(
                f"::experiment::worksheet-row-{row_number}"
            )
        )
        initial = experiment.sample.initial_composition
        assert initial is not None and initial.state.is_value
        assert initial.locator is not None
        assert initial.locator.page == 2 and initial.locator.table == "1"
        assert initial.state.value.amount_basis is AmountBasis.MOLE_FRACTION
        expected = wt_pct_to_mole_fraction(
            {name: Decimal(value) for name, value in printed[sample_code].items()}
        )
        assert initial.state.value.components == expected.components
        target_runs.append(experiment.experiment_id)
        expected_start_by_experiment[experiment.experiment_id] = expected

    phase_rows = [
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        and row.experiment_id in target_runs
    ]
    assert len(phase_rows) == 272
    target_source_rows = {
        f"worksheet-row-{row_number}" for row_number in expected_rows
    }
    source_glass_rows = [
        raw
        for container in doc["species"].values()
        for raw in container.get("observations", [])
        if raw.get("experiment") in target_source_rows
        and raw.get("type") == "composition_series"
        and raw.get("values", {}).get("phase") == "glass"
    ]
    assert len(source_glass_rows) == 272
    source_glass_by_id = {
        raw["observation_id"]: raw for raw in source_glass_rows
    }
    for raw in source_glass_rows:
        quench = raw["values"]["quench_statement"]
        assert quench["preservation"] == "not_stated"
        assert quench["quench_direction"] == "not_stated"
        assert quench["locator"]["page"] == 3
        assert quench["locator"]["pdf_page_index"] == 2
        assert quench["locator"]["published_page"] == 3
        assert "no quench or glass-preservation statement located" in (
            quench["locator"]["note"]
        )
    for row in phase_rows:
        composition = row.identity.composition
        assert composition is not None and composition.is_value
        assert composition.value.components == expected_start_by_experiment[
            row.experiment_id
        ].components
        assert composition.value.input_provenance == PRINTED_STARTING_BULK
        raw_id = row.observation_id.rsplit("::", 1)[-1]
        source_glass = source_glass_by_id[raw_id]
        printed_product = row.point_conditions["printed_composition"]
        assert printed_product.state.is_value
        assert printed_product.state.value == source_glass["values"]["composition_wt_pct"]
        assert printed_product.locator is not None
        assert printed_product.locator.record == source_glass["locator"]["record"]
        product_composition = row.point_conditions["composition"]
        assert product_composition.state.is_value
        assert product_composition.state.value.amount_basis is AmountBasis.MOLE_FRACTION
        assert product_composition.locator is not None
        assert product_composition.locator.record == source_glass["values"][
            "composition_locator"
        ]["record"]
        expected_product = _phase_assay_product(
            {
                name: Decimal(str(amount))
                for name, amount in printed_product.state.value.items()
            }
        )
        _assert_phase_assay_product(product_composition.state.value, expected_product)
        if raw_id == "rusiecka_2026_data_row81_sio2":
            assert Decimal(str(printed_product.state.value["Cl"])) == Decimal("1.83")
            assert dict(product_composition.state.value.components)["Cl"].quantize(
                Decimal("0.000000000001")
            ) == Decimal("0.034009940171")
        assert row.value.point == Decimal(
            source_glass["values"]["component_amount"]
        )

    mutated_doc = deepcopy(doc)
    mutated_and = next(
        run
        for run in mutated_doc["experiments"]
        if run["experiment_id"] == "worksheet-row-81"
    )
    mutated_and["sample"]["initial_composition"]["state"]["value"]["components"][
        "SiO2"
    ] = "62.48"
    changed_result = _migrate_real_extract(
        tmp_path / "starting-composition-mutated",
        EXTRACT_NAME,
        extract_doc=mutated_doc,
    )
    changed_experiment = next(
        migrated
        for migrated in changed_result.experiments.values()
        if migrated.experiment_id.endswith("::experiment::worksheet-row-81")
    )
    original_experiment = next(
        migrated
        for migrated in result.experiments.values()
        if migrated.experiment_id.endswith("::experiment::worksheet-row-81")
    )
    assert changed_experiment.sample.initial_composition.state.value.components != (
        original_experiment.sample.initial_composition.state.value.components
    )
    changed_phase_row = next(
        row
        for row in changed_result.observations.values()
        if row.observation_id.endswith("::rusiecka_2026_data_row81_sio2")
    )
    original_phase_row = next(
        row
        for row in result.observations.values()
        if row.observation_id.endswith("::rusiecka_2026_data_row81_sio2")
    )
    assert changed_phase_row.identity.composition.value.components != (
        original_phase_row.identity.composition.value.components
    )
    assert changed_phase_row.identity.composition.value.components == (
        changed_experiment.sample.initial_composition.state.value.components
    )
    assert (
        changed_phase_row.identity.composition.value.input_provenance
        == PRINTED_STARTING_BULK
    )
    assert changed_phase_row.value.point == original_phase_row.value.point


def test_catalog_proxy_start_keeps_row_phase_composition(tmp_path: Path) -> None:
    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    experiment = next(
        item for item in doc["experiments"]
        if item["experiment_id"] == "worksheet-row-81"
    )
    initial = experiment["sample"]["initial_composition"]
    printed_components = initial["state"]["value"]["components"]
    proxy_composition = wt_pct_to_mole_fraction(
        {name: Decimal(value) for name, value in printed_components.items()}
    )
    initial["state"]["value"] = {
        "basis": "sample_catalog_proxy",
        "amount_basis": "mole_fraction",
        "components": [
            [name, str(amount)] for name, amount in proxy_composition.components
        ],
        "proxy_flag": "composition_from_sample_catalog",
        "proxy_source": "Rusiecka regression probe",
        "analysis_selection_rule": "retain the typed catalogue proxy",
        "input_provenance": None,
    }

    result = _migrate_real_extract(
        tmp_path / "catalog-proxy-start", EXTRACT_NAME, extract_doc=doc
    )
    row = next(
        observation
        for observation in result.observations.values()
        if observation.observation_id.endswith("::rusiecka_2026_data_row81_sio2")
    )
    identity_composition = row.identity.composition
    assert identity_composition is not None and identity_composition.is_value
    product_composition = row.point_conditions["composition"]
    assert product_composition.state.is_value
    assert identity_composition.value.components == (
        product_composition.state.value.components
    )
    assert identity_composition.value.proxy_flag is None
    assert identity_composition.value.components != proxy_composition.components
