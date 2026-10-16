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
from simulator.battery.migrate import Migrator, load_yaml, wt_pct_to_mole_fraction


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
    extract = load_yaml(EXTRACT)
    source_points = {
        observation["observation_id"]: observation["values"]["points"][0]
        for observation in extract["species"]["Eu"]["observations"]
    }
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

    glass_rows = [row for row in rows if "_glasses_" in row.observation_id]
    melt_rows = [row for row in rows if "_melts_" in row.observation_id]
    assert len(glass_rows) == 113
    assert len(melt_rows) == 38
    assert all(row.identity.species.phase.value is Phase.GLASS for row in glass_rows)
    assert all(row.identity.species.phase.value is Phase.L for row in melt_rows)

    table1_join_compositions = {
        "ad_fo": {
            "SiO2": Decimal("48.62"),
            "Al2O3": Decimal("13.18"),
            "MgO": Decimal("16.49"),
            "CaO": Decimal("20.77"),
        },
        "ad_wo": {
            "SiO2": Decimal("49.95"),
            "Al2O3": Decimal("9.58"),
            "MgO": Decimal("6.55"),
            "CaO": Decimal("33.35"),
        },
        "ad_en": {
            "SiO2": Decimal("52.85"),
            "Al2O3": Decimal("10.85"),
            "MgO": Decimal("18.81"),
            "CaO": Decimal("17.17"),
        },
        "ad_qz": {
            "SiO2": Decimal("63.21"),
            "Al2O3": Decimal("11.11"),
            "MgO": Decimal("7.79"),
            "CaO": Decimal("17.18"),
        },
    }
    join_counts = {"ad_fo": 8, "ad_wo": 18, "ad_en": 8, "ad_qz": 17}
    for join, composition_wt_pct in table1_join_compositions.items():
        expected_composition = wt_pct_to_mole_fraction(composition_wt_pct)
        join_rows = [row for row in rows if f"_{join}_" in row.observation_id]
        assert len(join_rows) == join_counts[join]
        for row in join_rows:
            identity_composition = row.identity.composition
            point_composition = row.point_conditions["composition"]
            assert identity_composition.is_value
            assert point_composition.state.is_value
            assert dict(identity_composition.value.components) == dict(
                expected_composition.components
            )
            assert dict(point_composition.state.value.components) == dict(
                expected_composition.components
            )

    assert all(
        row.identity.composition.is_value
        and row.identity.composition.value.components
        == row.point_conditions["composition"].state.value.components
        for row in rows
    )
    for row in rows:
        source_point = source_points[row.observation_id.split("::", 2)[1]]
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == (
            Decimal(str(source_point["T_C"])) + Decimal("273.15")
        )
        for key, _condition in source_point["point_conditions"].items():
            migrated_condition = row.point_conditions[key]
            assert migrated_condition.state.is_value
            assert migrated_condition.locator is not None
        assert "Eu3+ peak column only" in row.locator.note
        assert "Table 3 logK prime averages" in row.locator.note

    assert "Cibin, G. & Mosselmans, J. F. W." in extract["source"]["citation"]
    assert "10.1016/j.chemgeo.2015.07.002" in extract["source"]["citation"]
    assert any(
        "high-temperature ratio is not preserved in Fe-bearing melts" in note
        for note in extract["notes"]
    )

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
