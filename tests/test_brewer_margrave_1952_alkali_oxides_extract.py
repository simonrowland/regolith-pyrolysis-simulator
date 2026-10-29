from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity, StateTag, ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/brewer-margrave-1952-alkali-oxides-ucrl1864.yaml"


def test_table3_printed_total_mass_loss_rows_and_quoted_pressure_migrate() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    issues = validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert issues == []

    points = document["species"]["Na2O"]["observations"][0]["values"]["points"]
    assert len(points) == 7
    # Independent render pins from the printed Table 3 on PDF page 17.
    assert points[0]["T_K"] == "918"
    assert points[0]["total_weight_loss_mg"] == "6.2"
    assert points[5]["total_weight_loss_mg"] == "134.4"
    assert points[6]["effusion_hole_area_cm2"] == "0.0804"
    assert points[6]["duration_s"] == "600"
    assert all("mass_loss_rate" not in point for point in points)

    pressure_quote = document["species"]["Na"]["observations"][0]
    assert pressure_quote["values"]["method_class"] == "quoted_attributed"
    assert pressure_quote["values"]["pressure_range_atm_as_printed"] == "10^-3–10^-4"
    assert "assumed in all the calculations" in pressure_quote["values"]["quote"]
    assert pressure_quote["values"]["assumption_as_printed"].startswith(
        "it is believed that the Na/O ratio ≈ 2/1"
    )
    assert pressure_quote["locator"]["page"] == 19
    assert pressure_quote["locator"]["pdf_page_index"] == 18

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert migrator.result.validation.hard_issues == ()
    migrated = list(migrator.result.observations.values())
    mass_loss_rows = [
        row for row in migrated if row.identity.quantity.value is Quantity.MASS_LOSS_RATE
    ]
    assert len(mass_loss_rows) == 7
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in mass_loss_rows)
    assert all(
        row.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        for row in mass_loss_rows
    )
    expected_temperature = {
        Decimal(point["T_K"]) for point in points
    }
    assert {
        row.identity.temperature_K.value
        for row in mass_loss_rows
        if row.identity.temperature_K.tag is StateTag.VALUE
    } == expected_temperature
    for row in mass_loss_rows:
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.tag is StateTag.VALUE
        assert temperature.locator is not None
        assert temperature.locator.table == "3"
        assert temperature.locator.pdf_page_index == 16
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_unknown

    quoted = [
        row
        for row in migrated
        if row.observation_id.endswith(
            "::brewer_margrave_1952_approximate_high_temperature_na_pressure"
        )
    ]
    assert len(quoted) == 1
    assert quoted[0].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert len(migrated) == 8
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in migrated)
