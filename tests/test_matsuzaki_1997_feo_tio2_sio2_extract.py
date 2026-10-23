"""Independent table pins and isolated migration for Matsuzaki & Ito (1997)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import yaml

from simulator.battery.enums import EvidenceClass, NoticeKind, Quantity, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT
from tools.validate_literature_extracts import validate_extract_document


EXTRACT = REPO_ROOT / "data/literature/extracts/matsuzaki-1997-feo-tio2-sio2-activities.yaml"
TABLE1 = (
    ("1", "1623", "-10.32", "0.039"), ("2", "1623", "-10.32", "0.049"),
    ("3", "1623", "-10.32", "0.046"), ("4", "1623", "-10.32", "0.021"),
    ("5", "1623", "-10.32", "0.030"), ("6", "1623", "-10.57", "0.047"),
    ("7", "1623", "-10.57", "0.036"), ("8", "1623", "-10.57", "0.032"),
    ("9", "1623", "-10.57", "0.032"), ("10", "1623", "-10.57", "0.007"),
    ("11", "1673", "-9.95", "0.070"), ("12", "1673", "-9.95", "0.061"),
    ("13", "1673", "-9.95", "0.054"), ("14", "1673", "-10.20", "0.055"),
    ("15", "1673", "-10.20", "0.025"), ("16", "1673", "-10.20", "0.016"),
    ("17", "1673", "-10.36", "0.028"), ("18", "1673", "-10.36", "0.023"),
    ("19", "1673", "-10.36", "0.013"), ("20", "1673", "-10.55", "0.025"),
    ("21", "1673", "-10.55", "0.033"), ("22", "1673", "-10.55", "0.012"),
    ("23", "1673", "-10.80", "0.035"), ("24", "1673", "-10.80", "0.035"),
    ("25", "1673", "-10.80", "0.016"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_table1_ratio_rows_migrate_with_temperature_and_printed_oxygen() -> None:
    document = _document()
    assert validate_extract_document(
        document, expected_source_id=EXTRACT.stem, pre_policy_ids=set(), check_fidelity_match=True
    ) == []
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    rows = document["species"]["Fe"]["observations"]
    assert len(rows) == len(TABLE1) == len(migrator.result.observations)
    assert Counter(row["values"]["quantity"] for row in rows) == {"fe3_fe2_ratio": 25}
    for source_row, expected in zip(rows, TABLE1, strict=True):
        sample, temperature, logp, ratio = expected
        values = source_row["values"]
        assert values["sample_id_as_printed"] == sample
        assert values["Fe3_over_Fe2"] == ratio
        assert values["log_fO2"] == {"value": logp, "units": "log10(atm)"}
        assert values["quench_statement"]["quench_direction"] == "not_stated"
        migrated = migrator.result.observations[f"{EXTRACT.stem}::{source_row['observation_id']}"]
        assert migrated.identity.quantity.value is Quantity.FE3_FE2_RATIO
        assert migrated.identity.species.formula == "Fe"
        assert migrated.value.kind is ValueKind.POINT
        assert migrated.value.point == Decimal(ratio)
        assert migrated.identity.temperature_K.value == Decimal(temperature)
        assert migrated.point_conditions["fO2_log"].state.value == Decimal(logp)
        assert migrated.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        assert migrated.point_conditions["T_K"].locator.page == 2
        assert all(notice.kind is not NoticeKind.SOURCE_VALUE_OUTSIDE_PHYSICAL_RANGE for notice in migrated.notices)


def test_table1_unscored_columns_are_preserved_and_figures_are_deferred() -> None:
    document = _document()
    context = document["species"]["Fe_tO"]["context"][0]
    assert context["type"] == "table_context"
    assert context["values"]["schema_gap"].find("t-1385") >= 0
    rows = context["values"]["rows"]
    assert len(rows) == 25
    assert rows[0]["slag_composition_wt_percent"] == {
        "FeO": "74.9", "Fe2O3": "3.2", "TiO2": "14.5", "SiO2": "7.3"
    }
    assert rows[0]["Fe_tO_activity_as_printed"] == "0.85"
    assert rows[0]["Fe_tO_activity_coefficient_as_printed"] == "1.08"
    figures = [
        row for species in document["species"].values() for row in species["context"]
        if row["type"] == "figure_record"
    ]
    assert {row["locator"]["figure"] for row in figures} == {"1", "2", "3(a)", "3(b)"}
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("series" in row["values"] and "axes_as_printed" in row["values"] for row in figures)
