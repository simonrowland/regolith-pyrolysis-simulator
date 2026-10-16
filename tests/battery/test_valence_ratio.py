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
BERRY_CR = ROOT / "data/literature/extracts/berry-oneill-2004-chromium-xanes.yaml"


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


def _migrate_berry_cr():
    migrator = Migrator(ROOT)
    migrator._migrate_extract(BERRY_CR)
    migrator.finalize()
    return migrator.result


def test_berry_oneill_2004_table3_valence_fractions_and_render_pins():
    result = _migrate_berry_cr()
    rows = list(result.observations.values())
    fractions = [
        row
        for row in rows
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.VALENCE_RATIO
        and row.identity.valence_basis.is_value
        and row.identity.valence_basis.value.value_form is ValenceValueForm.FRACTION
    ]
    log_ratios = [
        row
        for row in rows
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.VALENCE_RATIO
        and row.identity.valence_basis.is_value
        and row.identity.valence_basis.value.value_form is ValenceValueForm.LOG10_RATIO
    ]

    # Table 3 has 133 numeric Cr2+/SumCr cells; these values were checked on
    # the 400 dpi render of printed p. 794 (PDF page 5).
    assert len(rows) == 145
    assert len(fractions) == 133
    assert len(log_ratios) == 7
    assert result.validation is not None
    assert result.validation.hard_issues == ()

    by_local_id = {
        row.observation_id.split("::", 1)[1].split("::", 1)[0]: row
        for row in rows
    }
    render_pins = {
        "berry_2004_table3_ad_r01": Decimal("0.050"),
        "berry_2004_table3_ad_wo_r13": Decimal("0.453"),
        "berry_2004_table3_ad_rt20_r24": Decimal("0.951"),
    }
    for local_id, expected in render_pins.items():
        row = by_local_id[local_id]
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == expected
        assert row.locator.published_page == 794

    assert all(row.identity.species.formula == "Cr" for row in fractions)
    assert all(row.identity.species.phase.value is Phase.L for row in fractions)
    assert all(row.evidence.class_.value is EvidenceClass.MEASURED_REDUCED for row in fractions)
    assert all(
        row.identity.valence_basis.value.numerator_oxidation_states == (2,)
        and row.identity.valence_basis.value.denominator_kind
        is ValenceDenominatorKind.TOTAL_ELEMENT
        for row in fractions
    )
    required_conditions = (
        "temperature_K",
        "fO2_log",
        "fO2_control",
        "total_pressure_Pa",
        "composition",
    )
    assert all(row.identity.temperature_K.is_value for row in rows)
    assert all(
        all(
            key in row.point_conditions
            and row.point_conditions[key].state is not None
            and row.point_conditions[key].locator is not None
            for key in required_conditions
        )
        for row in rows
    )

    pressure_cells = {
        "berry_2004_table3_ad_r25",
        "berry_2004_table3_ad_fo_r25",
        "berry_2004_table3_ad_wo_r25",
        "berry_2004_table3_ad_en_r25",
        "berry_2004_table3_ad_qz_r25",
    }
    assert {
        local_id
        for local_id, row in by_local_id.items()
        if row.identity.total_pressure_Pa is not None
        and row.identity.total_pressure_Pa.is_value
    } == pressure_cells
    for local_id in pressure_cells:
        row = by_local_id[local_id]
        pressure = row.identity.total_pressure_Pa
        point_pressure = row.point_conditions["total_pressure_Pa"]
        assert pressure is not None and pressure.is_value
        assert pressure.value == Decimal("100000")
        assert point_pressure.state.is_value
        assert point_pressure.state.value == Decimal("100000")

    intermediate_calibration = {
        Decimal("0.936"): "berry_2004_table4_logkprime_ad",
        Decimal("0.919"): "berry_2004_table4_logkprime_ad_fo",
    }
    for row in fractions:
        assert row.derivation is not None
        assert len(row.derivation.parameters) == 1
        name, area = row.derivation.parameters[0]
        assert name == "A(1s/4s)"
        assert area.state.is_value
        assert area.locator is not None
        assert area.locator.published_page == 794
        assert "A(1s/4s)" in area.locator.record
        assert len(row.derived_from) == 1
        assert row.derivation.inputs == row.derived_from
        assert all(not parent.startswith("pdf:") for parent in row.derived_from)
        parent = result.observations[row.derived_from[0]]
        assert parent in log_ratios
        local_parent = parent.observation_id.split("::", 1)[1].split("::", 1)[0]
        row_composition = row.point_conditions["composition"].state
        if row_composition.is_value:
            assert (
                parent.point_conditions["composition"].state.value
                == row_composition.value
            )
        else:
            assert local_parent == intermediate_calibration[row.value.point]

    area_pins = {
        "berry_2004_table3_ad_r01": Decimal("0.005"),
        "berry_2004_table3_ad_wo_r13": Decimal("0.174"),
        "berry_2004_table3_ad_rt20_r24": Decimal("0.382"),
    }
    for local_id, expected in area_pins.items():
        assert by_local_id[local_id].derivation.parameters[0][1].state.value == expected

    slope_rows = [row for row in rows if "slope" in row.observation_id]
    assert len(slope_rows) == 5
    table4_rows = [*log_ratios, *slope_rows]
    assert len(table4_rows) == 12
    for row in table4_rows:
        assert row.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert row.evidence.attribution == "Berry and O'Neill (2004), Table 4"
        assert not row.derived_from
        pressure = row.identity.total_pressure_Pa
        point_pressure = row.point_conditions["total_pressure_Pa"]
        if row in log_ratios:
            assert pressure is not None and pressure.is_unknown
        else:
            assert pressure is None or pressure.is_unknown
        assert point_pressure.state.is_unknown
    for row in slope_rows:
        control = row.point_conditions["fO2_control"]
        assert control.state.is_unknown
        assert control.locator == row.locator
        assert row.point_conditions["fO2_log"].locator == row.locator
        assert row.point_conditions["total_pressure_Pa"].locator == row.locator
    assert {
        row.value.point for row in log_ratios
    } == {
        Decimal("1.871"),
        Decimal("1.921"),
        Decimal("2.056"),
        Decimal("1.844"),
        Decimal("1.650"),
        Decimal("2.274"),
        Decimal("2.371"),
    }
    unavailable = [row for row in rows if row.value.kind is ValueKind.UNAVAILABLE]
    assert len(unavailable) == 5
    assert {row.observation_id for row in unavailable} == {
        row.observation_id for row in slope_rows
    }


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
