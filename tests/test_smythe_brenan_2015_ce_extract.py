from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    Quantity,
    UncertaintyKind,
    ValenceValueForm,
    ValueKind,
)
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/smythe-brenan-2015-cerium-redox.yaml"
RENDER_PINS = {
    # Table 3, PDF page 39, render row -> log(Ce4+/Ce3+).
    "DS11-C38": (Decimal("-0.15"), 38),
    "DS09-C2-13": (Decimal("-0.41"), 38),
    # Table 3, PDF page 40.
    "DS11-C66": (Decimal("-2.57"), 39),
    "DS09-C3-15": (Decimal("-1.93"), 39),
    # Table 3, PDF page 41.
    "DS11-C22": (Decimal("-1.68"), 40),
    "DS09-C3-14": (Decimal("-2.98"), 40),
    "DS09-C3-04": (Decimal("-1.12"), 40),
}
RENDER_WIDTH_PINS = {
    "DS11-C38": Decimal("0.13"),
    "DS09-C3-15": Decimal("0.11"),
    "DS09-C3-04": Decimal("0.10"),
}


def test_table3_ce_log_ratio_rows_match_render_and_migrate_with_conditions():
    extract = yaml.safe_load(EXTRACT.read_text())
    observation = extract["species"]["Ce"]["observations"][0]
    rows = observation["values"]["series"]
    assert len(rows) == 45

    for sample, (value, pdf_page_index) in RENDER_PINS.items():
        row = next(item for item in rows if item["sample"] == sample)
        assert Decimal(str(row["value"])) == value
        assert row["locator"]["pdf_page_index"] == pdf_page_index
        assert row["locator"]["table"] == "3"

    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = list(migrator.result.observations.values())
    assert len(migrated) == 45
    assert all(row.value.kind is ValueKind.POINT for row in migrated)
    assert all(row.identity.species.formula == "Ce" for row in migrated)
    assert all(row.identity.species.phase.value.value == "glass" for row in migrated)
    assert all(row.identity.quantity.value is Quantity.VALENCE_RATIO for row in migrated)
    assert all(
        row.identity.valence_basis.value.value_form is ValenceValueForm.LOG10_RATIO
        for row in migrated
    )
    assert all(row.uncertainty.kind is UncertaintyKind.PRINTED for row in migrated)

    # The paper's printed calibration turns XANES into the reported ratio.
    # Its calibration input rows are not stored, so the existing validator's
    # measured_reduced ancestry issue is explicit and consistent for all rows.
    hard = migrator.result.validation.hard_issues
    assert len(hard) == 45
    assert {issue.detail for issue in hard} == {
        "derived observation requires derived_from"
    }

    for row in migrated:
        conditions = row.point_conditions or {}
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == conditions["temperature_K"].state.value
        for condition in (
            "temperature_K",
            "composition",
            "fO2_log",
            "fO2_control",
            "total_pressure_Pa",
        ):
            assert conditions[condition].state.is_value
            assert conditions[condition].locator is not None

    for sample, (value, _page) in RENDER_PINS.items():
        migrated_row = next(
            row for row in migrated if f"row={sample.casefold()}" in row.observation_id
        )
        assert migrated_row.value.point == value

    for sample, width in RENDER_WIDTH_PINS.items():
        migrated_row = next(
            row for row in migrated if f"row={sample.casefold()}" in row.observation_id
        )
        assert Decimal(str(migrated_row.uncertainty.verbatim["sigma"])) == width
