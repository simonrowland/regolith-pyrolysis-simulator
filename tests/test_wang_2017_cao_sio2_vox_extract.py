from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import yaml

from simulator.battery.enums import NoticeKind, PhaseRole, Quantity
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/wang-2017-cao-sio2-vox-tables-s1-s32.yaml"
)
SOURCE_ID = "wang-2017-cao-sio2-vox-tables-s1-s32"
EXPECTED_PO2_PA = Decimal("0.000006991425")


def _observations(document: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    result: list[tuple[str, dict[str, object]]] = []
    species = document["species"]
    assert isinstance(species, dict)
    for container, body in species.items():
        if not isinstance(body, dict):
            continue
        for row in body.get("observations", []):
            result.append((str(container), row))
    return result


def test_wang_s1_s32_rows_migrate_with_component_and_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = _observations(document)
    assert len(source_rows) == 516

    expected_first_point = {
        "CaO": Decimal("33.69"),
        "SiO2": Decimal("56.87"),
        "V2O3": Decimal("5.38"),
    }
    for formula, expected in expected_first_point.items():
        raw_id = f"wang_2017_s1_sample101_liquid_point_1_{formula.casefold()}"
        raw = next(row for _, row in source_rows if row["observation_id"] == raw_id)
        assert raw["values"]["component_amount"] == str(expected)
        assert raw["values"]["formula"] == formula

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    migrated = migrator.result.observations
    assert len(migrated) == 516
    assert not migrator.result.registry_issues

    by_sample: dict[str, set[str]] = {}
    for container, raw in source_rows:
        raw_id = str(raw["observation_id"])
        observation = migrated[f"{SOURCE_ID}::{raw_id}"]
        values = raw["values"]
        formula = str(values["formula"])
        printed_sum = Decimal(str(values["printed_sum"]))
        assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.identity.species.formula == formula
        assert observation.identity.phase_role.value is PhaseRole.SLAG
        assert observation.identity.subtype.value == "wt_percent"
        assert observation.identity.temperature_K.value == Decimal("1873")
        assert observation.identity.fO2_Pa.value == EXPECTED_PO2_PA
        assert observation.value.point == Decimal(str(values["component_amount"]))
        assert not any(
            notice.kind is NoticeKind.SOURCE_VALUE_OUTSIDE_PHYSICAL_RANGE
            for notice in observation.notices
        )
        assert observation.point_conditions["pO2_Pa"].state.value == EXPECTED_PO2_PA
        assert observation.point_conditions["printed_sum"].state.value == printed_sum
        assert observation.point_conditions["pO2_Pa"].locator.section == "Article title"
        assert observation.locator.table == raw["locator"]["table"]
        assert container in {"CaO", "SiO2", "V2O3"}
        by_sample.setdefault(str(raw["experiment"]), set()).add(
            observation.experiment_id
        )
    assert all(len(experiment_ids) == 1 for experiment_ids in by_sample.values())
    assert len(by_sample) == 29

    contexts = [
        row
        for rows in migrator.result.context_by_work.values()
        for row in rows
    ]
    assert len(contexts) == 147
    assert all(
        len(row["values"]["components_as_printed"]) == 3
        and row["values"]["total_as_printed"]
        for row in contexts
    )


def test_wang_fidelity_cells_reject_an_extract_cell_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []

    mutated = deepcopy(document)
    rows = _observations(mutated)
    row = next(
        row
        for _, row in rows
        if row["observation_id"]
        == "wang_2017_s1_sample101_liquid_point_1_cao"
    )
    row["values"]["component_amount"] = "33.68"
    errors = check_all_fidelity_samples_match(mutated, label="mutated extract")
    assert errors
    assert any("33.69" in error and "33.68" in error for error in errors)
