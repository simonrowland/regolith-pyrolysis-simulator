from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import (
    EvidenceClass,
    Phase,
    Quantity,
    UncertaintyKind,
    ValenceDenominatorKind,
    ValenceValueForm,
    ValueKind,
)
from simulator.battery.identity import ValenceBasis
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/berry-2006-chromium-composition.yaml"


def test_berry_2006_table1_migrates_all_rows_and_render_pinned_values():
    migrator = Migrator(ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    observations = [
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.VALENCE_RATIO
    ]

    # Table 1 has 27 composition rows and three log fO2 columns. Five rows
    # repeat three italicized Berry and O'Neill (2004) values in addition to
    # the 2006 measurements. The seven unavailable table cells remain rows.
    assert len(observations) == 96
    assert len({row.locator.note for row in observations}) == 27
    assert sum(row.value.kind is ValueKind.POINT for row in observations) == 89
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations) == 7
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert result.registry_issues == []

    expected_basis = ValenceBasis(
        numerator_oxidation_states=(2,),
        denominator_kind=ValenceDenominatorKind.TOTAL_ELEMENT,
        denominator_oxidation_state=None,
        value_form=ValenceValueForm.FRACTION,
    )
    composition_signatures = set()
    for row in observations:
        assert row.identity.species.formula == "Cr"
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.valence_basis.value == expected_basis
        assert row.identity.temperature_K.value == Decimal("1673.15")

        conditions = row.point_conditions
        assert conditions is not None
        temperature = conditions["T_C"]
        assert temperature.state.value == Decimal("1400")
        assert temperature.locator.published_page == 1902
        oxygen = conditions["fO2_log"]
        assert oxygen.state.is_value
        assert oxygen.state.value in {
            Decimal("-2"),
            Decimal("-7"),
            Decimal("-7.5"),
            Decimal("-9"),
        }
        assert oxygen.locator.published_page == 1902
        pressure = conditions["P_bar"]
        assert pressure.state.is_unknown
        assert pressure.state.reason == "not_published"
        assert pressure.locator.published_page == 1902
        composition = conditions["composition"]
        assert composition.state.is_value
        assert composition.locator.published_page == 1902
        composition_signatures.add(composition.state.value.components)

        assert row.uncertainty.kind is UncertaintyKind.PRINTED
        assert row.uncertainty.verbatim["value"] == 0.03
        assert row.uncertainty.verbatim["basis"] == "fraction"
        assert row.uncertainty.verbatim["as_printed"] == "±0.03"
        assert row.uncertainty.verbatim["confidence_level"] == "one_sigma"

    assert len(composition_signatures) == 27
    stored_fractions = {
        row.value.point
        for row in observations
        if row.value.kind is ValueKind.POINT
    }
    assert Decimal("0.32") not in stored_fractions
    assert Decimal("0.91") not in stored_fractions
    assert sum(
        row.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        for row in observations
    ) == 81
    quoted = [
        row
        for row in observations
        if row.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    ]
    assert len(quoted) == 15
    assert all(row.evidence.attribution == "Berry and O'Neill (2004)" for row in quoted)

    def cell(row_note: str, fO2: Decimal, evidence_class: EvidenceClass):
        matches = [
            row
            for row in observations
            if row.locator.note == row_note
            and row.point_conditions["fO2_log"].state.value == fO2
            and row.evidence.class_.value is evidence_class
        ]
        assert len(matches) == 1
        return matches[0]

    # Table 1 cells checked against the page 1902 render, including an italic
    # comparison value. The table prints fractions; no Cr2+/Cr3+ conversion.
    pins = (
        ("Table 1 row 1 AD", Decimal("-2"), EvidenceClass.MEASURED_REDUCED, Decimal("0.038")),
        ("Table 1 row 8 Longhi3", Decimal("-9"), EvidenceClass.MEASURED_REDUCED, Decimal("0.871")),
        ("Table 1 row 1 AD", Decimal("-9"), EvidenceClass.QUOTED_ATTRIBUTED, Decimal("0.709")),
        ("Table 1 row 5 AD+Qz", Decimal("-7.5"), EvidenceClass.MEASURED_REDUCED, Decimal("0.597")),
    )
    for row_note, fO2, evidence_class, value in pins:
        observation = cell(row_note, fO2, evidence_class)
        assert observation.locator.published_page == 1902
        assert observation.value.point == value

