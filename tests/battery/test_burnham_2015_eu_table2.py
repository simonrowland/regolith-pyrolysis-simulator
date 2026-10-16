from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import (
    EvidenceClass,
    Phase,
    Quantity,
    ValenceDenominatorKind,
    ValenceValueForm,
)
from simulator.battery.identity import ValenceBasis
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[2]
EXTRACT = ROOT / "data/literature/extracts/burnham-2015-europium-redox.yaml"
SOURCE_ID = "burnham-2015-europium-redox"


def _migrate_burnham_table2():
    migrator = Migrator(ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    rows = [
        row
        for row in migrator.result.observations.values()
        if row.source_id == SOURCE_ID
    ]
    return migrator.result, rows


def test_burnham_table2_render_pins_and_row_count():
    result, rows = _migrate_burnham_table2()
    assert len(rows) == 151
    assert all(
        row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.VALENCE_RATIO
        for row in rows
    )
    assert not result.validation.hard_issues
    assert all(
        row.identity.valence_basis.is_value
        and row.identity.valence_basis.value
        == ValenceBasis(
            numerator_oxidation_states=(3,),
            denominator_kind=ValenceDenominatorKind.TOTAL_ELEMENT,
            denominator_oxidation_state=None,
            value_form=ValenceValueForm.FRACTION,
        )
        for row in rows
    )

    pins = {
        ("burnham_2015_eu_glasses_ad_1300_f0p0", 34): Decimal("1.00"),
        ("burnham_2015_eu_glasses_cas_4_1400_fm13p0", 36): Decimal("-0.02"),
        ("burnham_2015_eu_melts_morb_1450_fm9p5", 37): Decimal("0.26"),
        ("burnham_2015_eu_glasses_mas_1_1400_fm13p0", 36): Decimal("-0.04"),
    }
    by_id = {row.observation_id.split("::", 2)[1]: row for row in rows}
    for (observation_id, pdf_page), expected in pins.items():
        row = by_id[observation_id]
        assert row.value.point == expected
        assert row.locator.pdf_page_index == pdf_page - 1
        assert row.locator.page == pdf_page
        assert row.evidence.class_.value is EvidenceClass.MEASURED_REDUCED

    # Render audit: Table 2's printed Eu3+/SumEu column, PDF pp. 34, 36, and 37.
    assert by_id["burnham_2015_eu_glasses_ad_1300_f0p0"].value.point == Decimal("1.00")
    assert by_id["burnham_2015_eu_glasses_cas_4_1400_fm13p0"].value.point == Decimal("-0.02")
    assert by_id["burnham_2015_eu_melts_morb_1450_fm9p5"].value.point == Decimal("0.26")

    negative = [row for row in rows if row.value.point is not None and row.value.point < 0]
    assert len(negative) == 2
    assert all(
        not row.notices
        and "printed fraction below the physical bound 0; consistent with ~0 within the method's precision"
        in row.locator.note
        for row in negative
    )
    assert all(row.identity.species.phase.value is Phase.L for row in rows)
    assert all(
        row.identity.temperature_K.is_value
        and "fO2_log" in row.point_conditions
        and row.point_conditions["fO2_log"].state.is_value
        and row.point_conditions["total_pressure_Pa"].state.is_value
        and row.point_conditions["total_pressure_Pa"].state.value == Decimal("101325")
        and row.point_conditions["composition"].state.is_value
        for row in rows
    )
    assert not result.registry_issues
