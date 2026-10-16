from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AdmissionStatus,
    EvidenceClass,
    IdentityEqualKind,
    Phase,
    Quantity,
    Rail,
    RefusalReason,
    ValenceDenominatorKind,
    ValenceValueForm,
    ValueKind,
)
from simulator.battery.identity import (
    Identity,
    IdentityRefusal,
    ValenceBasis,
    _same_row_redox_identity_equal,
    identity_equal,
    parse_valence_basis,
    profile_for,
)
from simulator.battery.migrate import Migrator, _identity_from_plain, to_plain
from simulator.battery.records import Species, State
from simulator.battery.score import (
    SCORE_ENGINE_SET,
    ScoreContext,
    score_store,
)


ROOT = Path(__file__).resolve().parents[2]
SOSSI_CR = ROOT / "data/literature/extracts/kems-045-sossi-2018-pnas-cr.yaml"
TRANELL_TI = ROOT / "data/literature/extracts/tranell-2002-titanium-valence.yaml"


def _identity(basis: ValenceBasis) -> Identity:
    return Identity(
        quantity=Quantity.VALENCE_RATIO,
        species=Species("Cr", Phase.L),
        valence_basis=State.of(basis),
    )


@pytest.mark.parametrize(
    ("printed", "denominator_kind", "denominator_state", "value_form"),
    (
        (
            "Cr2plus_over_total_Cr",
            ValenceDenominatorKind.TOTAL_ELEMENT,
            None,
            ValenceValueForm.FRACTION,
        ),
        (
            "Cr3plus_over_Cr2plus",
            ValenceDenominatorKind.OXIDATION_STATE,
            2,
            ValenceValueForm.RATIO,
        ),
        (
            "log10_Cr3plus_over_Cr2plus",
            ValenceDenominatorKind.OXIDATION_STATE,
            2,
            ValenceValueForm.LOG10_RATIO,
        ),
    ),
)
def test_printed_valence_forms_parse_into_typed_identity(
    printed, denominator_kind, denominator_state, value_form
):
    basis = parse_valence_basis(printed, species_formula="Cr")
    identity = _identity(basis)

    assert basis.numerator_oxidation_states == (2 if value_form is ValenceValueForm.FRACTION else 3,)
    assert basis.denominator_kind is denominator_kind
    assert basis.denominator_oxidation_state == denominator_state
    assert basis.value_form is value_form
    assert "valence_basis" in profile_for(identity).required


def test_malformed_valence_form_has_typed_refusal():
    with pytest.raises(IdentityRefusal) as refused:
        parse_valence_basis("Cr2plus_over_total_Fe", species_formula="Cr")

    assert refused.value.reason is RefusalReason.INVALID_SOURCE


def test_identity_requires_valence_basis_only_for_valence_ratio():
    species = Species("Cr", Phase.L)
    basis = State.of(parse_valence_basis("Cr2plus_over_total_Cr"))

    with pytest.raises(IdentityRefusal) as missing:
        Identity(quantity=Quantity.VALENCE_RATIO, species=species)
    assert missing.value.reason is RefusalReason.INVALID_IDENTITY

    with pytest.raises(IdentityRefusal) as extraneous:
        Identity(quantity=Quantity.ACTIVITY, species=species, valence_basis=basis)
    assert extraneous.value.reason is RefusalReason.INVALID_IDENTITY

    old_fe_identity = Identity(
        quantity=Quantity.FE3_FE2_RATIO,
        species=Species("Fe", Phase.L),
    )
    assert "valence_basis" not in to_plain(old_fe_identity)


def test_valence_basis_serializes_and_round_trips_as_a_typed_sibling():
    identity = _identity(parse_valence_basis("Cr2plus_over_total_Cr"))
    payload = to_plain(identity)
    assert isinstance(payload, dict)
    assert "valence_basis" in payload

    decoded = _identity_from_plain(payload)
    assert decoded.valence_basis == identity.valence_basis


def test_valence_basis_participates_in_identity_and_same_row_comparison():
    fraction = _identity(parse_valence_basis("Cr2plus_over_total_Cr"))
    ratio = _identity(parse_valence_basis("Cr3plus_over_Cr2plus"))

    for compare in (identity_equal, _same_row_redox_identity_equal):
        outcome = compare(fraction, ratio)
        assert outcome.kind is IdentityEqualKind.IDENTITY_MISMATCH
        assert "valence_basis" in outcome.fields


def _migrate_sossi_cr():
    migrator = Migrator(ROOT)
    migrator._migrate_extract(SOSSI_CR)
    migrator.finalize()
    return migrator.result


def test_sossi_2018_quoted_cr_rows_migrate_as_numeric_valence_fractions():
    result = _migrate_sossi_cr()
    rows = [
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.VALENCE_RATIO
    ]

    assert len(rows) == 2
    assert {row.value.point for row in rows if row.value.kind is ValueKind.POINT} == {
        Decimal("0.32"),
        Decimal("0.91"),
    }
    assert all(row.value.kind is ValueKind.POINT for row in rows)
    assert all(row.identity.species.phase.value is Phase.L for row in rows)
    assert all(
        row.identity.valence_basis.value
        == ValenceBasis(
            numerator_oxidation_states=(2,),
            denominator_kind=ValenceDenominatorKind.TOTAL_ELEMENT,
            denominator_oxidation_state=None,
            value_form=ValenceValueForm.FRACTION,
        )
        for row in rows
    )
    assert all(row.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED for row in rows)
    assert all(row.admission.status is AdmissionStatus.ADMITTED for row in rows)
    assert all(
        row.evidence.attribution
        == "Berry, O'Neill, Scott, Foran and Shelley 2006 Am. Mineral. 91:1901-1908 (ref. 34)"
        for row in rows
    )
    control_by_value = {
        row.value.point: row.point_conditions["fO2_control"].state.value
        for row in rows
    }
    assert control_by_value == {
        Decimal("0.32"): "near FMQ",
        Decimal("0.91"): "~1 log unit below IW",
    }
    for row in rows:
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1673.15")
        point_temperature = row.point_conditions["temperature_K"]
        assert point_temperature.state.value == Decimal("1673.15")
        assert point_temperature.locator is not None
        control = row.point_conditions["fO2_control"]
        assert control.state.is_value
        assert control.state.value in {"near FMQ", "~1 log unit below IW"}
        assert control.locator is not None
        assert control.locator.published_page == 10921


def test_tranell_2002_table_i_rows_migrate_with_render_pinned_values_and_conditions():
    migrator = Migrator(ROOT)
    migrator._migrate_extract(TRANELL_TI)
    migrator.finalize()
    result = migrator.result
    rows = [
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.VALENCE_RATIO
    ]

    assert len(rows) == 57
    by_locator = {row.locator.note: row for row in rows}
    rendered_pins = {
        "Table I row 1, slag CST1": ("0.023", "1843", "-8"),
        "Table I row 27, slag CST7": ("0.068", "1903", "-8"),
        "Table I row 57, slag CST14": ("0.452", "1873", "-12"),
    }
    for locator_note, (value, temperature, oxygen_exponent) in rendered_pins.items():
        row = by_locator[locator_note]
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(value)
        assert row.locator.published_page == 63
        assert row.locator.pdf_page_index == 2
        assert row.identity.species.formula == "Ti"
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.valence_basis.value == ValenceBasis(
            numerator_oxidation_states=(3,),
            denominator_kind=ValenceDenominatorKind.OXIDATION_STATE,
            denominator_oxidation_state=4,
            value_form=ValenceValueForm.RATIO,
        )
        assert row.identity.temperature_K.value == Decimal(temperature)
        assert row.point_conditions["fO2_Pa"].state.value.point == (
            Decimal("101325") * (Decimal(10) ** Decimal(oxygen_exponent))
        )

    assert all(row.value.kind is ValueKind.POINT for row in rows)
    assert not result.validation.hard_issues
    assert not result.queue
    assert all(
        row.admission.status is AdmissionStatus.ADMITTED
        and row.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        and row.identity.temperature_K.is_value
        for row in rows
    )
    for row in rows:
        conditions = row.point_conditions
        assert conditions is not None
        for key in (
            "temperature_K",
            "fO2_Pa",
            "fO2_control",
            "atmosphere",
            "total_pressure_Pa",
            "printed_composition",
            "CaO_SiO2_mass_pct_ratio",
        ):
            condition = conditions[key]
            assert condition.locator.published_page == 63
        assert conditions["temperature_K"].state.value == row.identity.temperature_K.value
        assert conditions["fO2_Pa"].state.value.kind is ValueKind.POINT
        assert conditions["total_pressure_Pa"].state.is_unknown
        assert conditions["printed_composition"].state.is_value
        assert conditions["CaO_SiO2_mass_pct_ratio"].state.is_value


def test_valence_ratio_scores_redox_and_counts_each_engine_refusal():
    result = _migrate_sossi_cr()
    context = ScoreContext(
        result.works,
        result.experiments,
        result.observations,
        origins={key: SOSSI_CR.name for key in result.observations},
        benches=result.benches,
    )

    residuals, _candidates = score_store(
        context,
        engines=SCORE_ENGINE_SET,
        rail=Rail.REDOX,
    )

    assert len(residuals) == 2 * len(SCORE_ENGINE_SET)
    assert len({residual.reference for residual in residuals}) == 2
    assert all(
        sum(residual.key.endswith(engine.value) for residual in residuals) == 2
        for engine in SCORE_ENGINE_SET
    )
    for residual in residuals:
        assert residual.rail is Rail.REDOX
        assert residual.numeric is None
        assert residual.refusal is not None
        assert residual.refusal.reason is RefusalReason.UNSUPPORTED
