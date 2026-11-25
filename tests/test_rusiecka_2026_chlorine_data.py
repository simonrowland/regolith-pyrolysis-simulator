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
    Engine,
    MethodToken,
    NoticeKind,
    Phase,
    PhaseRole,
    Quantity,
    ValueKind,
)
from simulator.battery.composition import (
    composition_wt_pct,
    excluded_component_notice_reason,
    partition_engine_components,
)
from simulator.battery.migrate import _mass_percent_composition_located
from simulator.battery.score import (
    _COMPOSITION_COMPONENTS_RETAINED_BY_DEFAULT,
    _engine_bulk_component_names,
)
from tests.battery.test_migrate import REPO_ROOT, _migrate_real_extract
from tests.helpers.corpus_paths import regolith_corpus_root


SOURCE_ID = "rusiecka-2026-chlorine-data"
EXTRACT_NAME = f"{SOURCE_ID}.yaml"
EXTRACT_PATH = REPO_ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME
WORKBOOK_NAME = "rusiecka-2026-chlorine-data.xlsx"


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

    phase_rows = [
        (formula, source)
        for formula, container in doc["species"].items()
        for source in container.get("observations", [])
        if source.get("values", {}).get("quantity")
        == "equilibrium_phase_composition"
    ]
    assert len(phase_rows) == 1785
    refused_ids = [
        source["observation_id"]
        for _formula, source in phase_rows
        if not migrated[source["observation_id"]].identity.composition.is_value
    ]
    assert len(refused_ids) == 34, refused_ids

    for row_number, source, row in mapped:
        source_components = source["values"]["composition_wt_pct"]
        point_composition = row.point_conditions["composition"]
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

        assert row.identity.composition.is_value, row.observation_id
        composition = row.identity.composition.value
        assert composition.basis == "printed_oxides"
        assert composition.amount_basis is AmountBasis.MOLE_FRACTION
        projected = dict(composition.components)
        assert abs(sum(projected.values(), Decimal("0")) - Decimal("1")) < Decimal("1e-24")
        if Decimal(source_components["Cl"]) > 0:
            assert projected["Cl"] > 0
        assert point_composition.locator is not None
        assert point_composition.locator.record == (
            f"sheet data, cells B{row_number}:R{row_number}"
        )
        assert point_composition.inference is not None
        assert point_composition.inference.relation == "source_composition_projection", row.observation_id
        assert point_composition.inference.output_unit == "mole_fraction"
        assert "x_i = (w_i / M_i) / Σ_j (w_j / M_j)" in point_composition.inference.inputs
        assert point_composition.inference.method is not None
        assert point_composition.inference.method.state.value == (
            "partial oxide analysis with Cl/S/F"
        )
        assert any(
            name == "original_analytical_total_wt_pct"
            for name, _value in point_composition.inference.parameters
        )
        assert any(
            name == "original_Cl_wt_pct"
            for name, _value in point_composition.inference.parameters
        )
        assert row.identity.temperature_K.is_value
        for condition_name in ("total_pressure_Pa", "fO2_bar", "fCl2_bar"):
            condition = row.point_conditions[condition_name]
            assert condition.state.is_value
            assert condition.locator is not None
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
    for container in mutated_doc["species"].values():
        for observation in container.get("observations", []):
            if (
                observation.get("experiment") == "worksheet-row-6"
                and observation.get("values", {}).get("quantity")
                == "equilibrium_phase_composition"
                and observation.get("values", {}).get(
                    "source_column_header_as_printed"
                )
                == "Cl"
            ):
                observation["values"]["component_amount"] = "0.92"
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


def test_row6_projection_components_match_current_mass_percent_resolver(
    tmp_path: Path,
) -> None:
    """Keep the source projection numerically aligned with the shared resolver."""

    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    source = next(
        observation
        for container in doc["species"].values()
        for observation in container.get("observations", [])
        if observation["observation_id"] == "rusiecka_2026_data_row6_sio2"
    )
    result = _migrate_real_extract(
        tmp_path / "row6-projection-baseline", EXTRACT_NAME, extract_doc=doc
    )
    row = next(
        observation
        for observation in result.observations.values()
        if observation.observation_id.endswith("::rusiecka_2026_data_row6_sio2")
    )
    values = source["values"]
    printed = values["composition_wt_pct"]
    expected = _mass_percent_composition_located(
        {name: Decimal(amount) for name, amount in printed.items()},
        basis="printed_phase_composition",
        locator=row.point_conditions["composition"].locator,
        closure_context=values,
        declared_component_map=printed,
    )
    assert dict(row.identity.composition.value.components) == dict(
        expected.state.value.components
    )


def test_row97_links_located_component_rows_and_flags_cl_without_filling_s_or_f(
    tmp_path: Path,
) -> None:
    """Rebuild an omitted duplicate vector from its complete located row."""

    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    row_sources = [
        (formula, observation)
        for formula, container in doc["species"].items()
        for observation in container.get("observations", [])
        if observation.get("experiment") == "worksheet-row-97"
        and observation.get("values", {}).get("quantity")
        == "equilibrium_phase_composition"
    ]
    assert len(row_sources) == 17
    assert all("composition_wt_pct" not in source["values"] for _, source in row_sources)
    expected_wt = {
        formula: Decimal(source["values"]["component_amount"])
        for formula, source in row_sources
    }
    result = _migrate_real_extract(
        tmp_path / "row97-linked-projection", EXTRACT_NAME, extract_doc=doc
    )
    row = next(
        observation
        for observation in result.observations.values()
        if observation.observation_id.endswith("::rusiecka_2026_data_row97_sio2")
    )
    assert row.identity.composition.is_value
    projected = dict(row.identity.composition.value.components)
    assert projected["Cl"] > 0
    assert "S" not in projected
    assert "F" not in projected
    wt_percent = composition_wt_pct(row.identity.composition.value)
    assert wt_percent is not None
    kept, excluded = partition_engine_components(
        wt_percent,
        engine_components=_engine_bulk_component_names(Engine.OPENIMCC),
        retain_components=_COMPOSITION_COMPONENTS_RETAINED_BY_DEFAULT,
    )
    assert "Cl" not in dict(kept)
    assert dict(excluded)["Cl"] > 0
    excluded_reason = excluded_component_notice_reason(excluded, unit="wt%")
    assert "Cl " in excluded_reason
    assert "wt%" in excluded_reason
    located = row.point_conditions["composition"]
    assert located.inference is not None
    assert located.inference.relation == "source_composition_projection"
    assert located.inference.method is not None
    assert located.inference.method.state.value == "partial oxide analysis with Cl/S/F"
    assert (
        "source row interpretation: same-experiment located component observations"
        in located.inference.inputs
    )
    total_flag = dict(located.inference.parameters)[
        "original_analytical_total_wt_pct"
    ]
    assert total_flag.state.value == sum(expected_wt.values(), Decimal("0"))
    assert total_flag.locator == located.locator
    assert any(name == "original_Cl_wt_pct" for name, _ in located.inference.parameters)


def test_rusiecka_refuses_when_required_silica_observation_is_missing(
    tmp_path: Path,
) -> None:
    doc = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    for container in doc["species"].values():
        container["observations"] = [
            observation
            for observation in container.get("observations", [])
            if observation.get("observation_id") != "rusiecka_2026_data_row97_sio2"
        ]
    result = _migrate_real_extract(
        tmp_path / "row97-missing-silica", EXTRACT_NAME, extract_doc=doc
    )
    row = next(
        observation
        for observation in result.observations.values()
        if observation.observation_id.endswith("::rusiecka_2026_data_row97_al2o3")
    )
    assert row.identity.composition.is_unknown


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
        assert row.identity.fO2_Pa.is_unknown
        assert row.experiment_id.endswith(f"::experiment::worksheet-row-{row_number}")
        assert row.point_conditions["total_pressure_Pa"].state.value == (
            _displayed_decimal(ws.cell(row_number, 21)) * Decimal("1000000000")
        )
        assert row.point_conditions["total_pressure_Pa"].locator is not None
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
        if Decimal(expected_composition["Cl"]) > 0:
            assert "Cl" in projected
        located = row.point_conditions["composition"]
        assert located.locator is not None
        assert located.locator.record == f"sheet data, cells B{row_number}:R{row_number}"
        assert located.inference is not None
        assert located.inference.relation == "source_composition_projection"
        assert located.inference.output_unit == "mole_fraction"
        assert "x_i = (w_i / M_i) / Σ_j (w_j / M_j)" in located.inference.inputs
        assert any(name == "original_Cl_wt_pct" for name, _value in located.inference.parameters)
        if row_number in {58, 59}:
            assert "Re" not in projected
            assert not any(name == "original_Re_wt_pct" for name, _value in located.inference.parameters)
        bound_rows.add(row_number)
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
