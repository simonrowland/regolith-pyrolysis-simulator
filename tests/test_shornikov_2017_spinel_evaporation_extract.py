from __future__ import annotations

import copy
import sys
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import UncertaintyKind, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT, convert_pressure_to_pa

TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import validate_literature_extracts as validator  # noqa: E402


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/shornikov-2017-spinel-evaporation.yaml"
)
EXPECTED_COMPOSITION = {"MgO": Decimal("0.5"), "Al2O3": Decimal("0.5")}


def _observations(document: dict) -> list[dict]:
    return [
        observation
        for block in document["species"].values()
        for observation in block["observations"]
    ]


def test_table_cells_and_render_pins_are_complete_and_mutation_sensitive() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = _observations(document)
    table_2 = [row for row in observations if "_table2_" in row["observation_id"]]
    table_3 = [row for row in observations if "_table3_" in row["observation_id"]]

    assert len(document["species"]["MgAl2O4"]["context"]) == 8
    assert sum(len(row["values"].get("points", [])) for row in table_2) == 73
    assert sum(not row["values"].get("points") for row in table_2) == 11
    assert sum(len(row["values"].get("points", [])) for row in table_3) == 58
    assert sum(not row["values"].get("points") for row in table_3) == 2
    eq14 = next(
        row
        for row in table_3
        if row["observation_id"] == "shornikov_2017_table3_o_pressure_eq14"
    )
    assert "K10(T)" in eq14["derivation"]["inputs"][2]
    assert "[10]" in eq14["derivation"]["inputs"][2]
    assert "K14(T)" not in eq14["derivation"]["inputs"][2]
    eq8_9 = next(
        row
        for row in table_3
        if row["observation_id"] == "shornikov_2017_table3_o_pressure_eq8_9"
    )
    assert eq8_9["uncertainty"]["bound_value"] == "8%"
    assert "uncertainty" not in eq14

    samples = document["fidelity_samples"]
    assert len(samples) == 3
    for sample in samples:
        assert sample["locator"]["page"] == 4
        assert "render" in sample["locator"]["note"]
        assert not validator.check_fidelity_sample_matches(document, sample)

        changed = copy.deepcopy(document)
        point = validator.resolve_field_path(
            changed, sample["path"].rsplit(".", 1)[0]
        )
        point["pressure_atm"] = "9.99e-99"
        errors = validator.check_fidelity_sample_matches(changed, sample)
        assert any("mismatch" in error for error in errors)

    assert not validator.validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )


def test_isolated_migration_keeps_table_conditions_identity_and_unavailable_rows() -> None:
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    records = list(result.observations.values())

    assert len(records) == 144
    assert sum(len(rows) for rows in result.context_by_work.values()) == 8
    assert sum(record.value.kind is ValueKind.UNAVAILABLE for record in records) == 13
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert not result.registry_issues

    for record in records:
        assert record.identity.temperature_K.is_value
        assert record.identity.composition.is_value
        assert dict(record.identity.composition.value.components) == EXPECTED_COMPOSITION
        assert record.identity.fO2_Pa.is_unknown
        assert record.identity.total_pressure_Pa.is_unknown
        condition = record.point_conditions["temperature_K"]
        assert condition.state.is_value
        assert condition.locator is not None
        assert condition.locator.table in {"2", "3"}

    identity_pins = (
        ("Mg", Decimal("1851"), "4.68e-7"),
        ("MgAlO", Decimal("2089"), "8.89e-10"),
        ("O", Decimal("1914"), "4.12e-8"),
    )
    for formula, temperature, pressure_atm in identity_pins:
        pressure_pa, relation = convert_pressure_to_pa(pressure_atm, "atm")
        assert relation == "atm_to_Pa"
        record = next(
            row
            for row in records
            if row.identity.species.formula == formula
            and row.identity.temperature_K.value == temperature
            and row.value.point == pressure_pa
        )
        assert record.identity.temperature_K.is_value
        assert record.identity.composition.is_value
        assert record.identity.total_pressure_Pa.is_unknown
        assert record.identity.fO2_Pa.is_unknown

    table2_mg = next(
        row
        for row in records
        if row.identity.species.formula == "Mg"
        and row.identity.temperature_K.value == Decimal("1851")
    )
    assert table2_mg.uncertainty.kind is UncertaintyKind.PRINTED
    assert table2_mg.uncertainty.verbatim["form"] == "relative_bound"
    assert table2_mg.uncertainty.verbatim["bound_value"] == "8%"
    assert table2_mg.uncertainty.verbatim["confidence_level"] == "not_stated"
    assert table2_mg.uncertainty.verbatim["notice"] == "confidence level not stated"

    eq8_9_pressure, _ = convert_pressure_to_pa("1.21e-8", "atm")
    table3_reduced_o = next(
        row
        for row in records
        if row.identity.species.formula == "O"
        and row.identity.temperature_K.value == Decimal("1851")
        and row.value.point == eq8_9_pressure
    )
    assert table3_reduced_o.uncertainty.kind is UncertaintyKind.PRINTED
    assert table3_reduced_o.uncertainty.verbatim["bound_value"] == "8%"

    eq14_pressure, _ = convert_pressure_to_pa("1.14e-8", "atm")
    table3_eq14_o = next(
        row
        for row in records
        if row.identity.species.formula == "O"
        and row.identity.temperature_K.value == Decimal("1851")
        and row.value.point == eq14_pressure
    )
    assert table3_eq14_o.uncertainty.kind is UncertaintyKind.NONE
