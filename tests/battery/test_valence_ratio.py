import copy
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    AmountBasis,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    IdentityEqualKind,
    NoticeKind,
    Phase,
    QuenchDirection,
    QuenchState,
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
from simulator.battery.migrate import (
    Migrator,
    _identity_from_plain,
    fe3_fe2_ratio_from_ferric_fraction,
    map_quantity,
    observation_from_plain,
    to_plain,
)
from simulator.battery.records import (
    Composition,
    Execution,
    Located,
    Locator,
    Species,
    State,
    Value,
    phase_token,
)
from simulator.battery.score import (
    SCORE_ENGINE_SET,
    EnginePrediction,
    ScoreContext,
    headline_rows,
    paired_quench_diagnostics,
    flagged_strata,
    render_score_report,
    residual_to_plain,
    render_score_report_from_payloads,
    selected_lineage_level,
    score_store,
)


ROOT = Path(__file__).resolve().parents[2]
SOSSI_CR = ROOT / "data/literature/extracts/kems-045-sossi-2018-pnas-cr.yaml"
TRANELL_TI = ROOT / "data/literature/extracts/tranell-2002-titanium-valence.yaml"
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


def test_average_oxidation_state_has_total_element_basis_and_round_trips():
    basis = parse_valence_basis(
        "average_oxidation_state", species_formula="V"
    )
    quantity, reason = map_quantity(
        "concentration_series", {"quantity": "average_oxidation_state"}
    )
    identity = Identity(
        quantity=Quantity.VALENCE_RATIO,
        species=Species("V", Phase.L),
        valence_basis=State.of(basis),
    )

    assert basis.numerator_oxidation_states == ()
    assert basis.denominator_kind is ValenceDenominatorKind.TOTAL_ELEMENT
    assert basis.denominator_oxidation_state is None
    assert basis.value_form is ValenceValueForm.AVERAGE_OXIDATION_STATE
    assert quantity.is_value and quantity.value is Quantity.VALENCE_RATIO
    assert reason is None
    payload = to_plain(identity)
    assert isinstance(payload, dict)
    decoded = _identity_from_plain(payload)
    assert decoded.species.formula == "V"
    assert decoded.valence_basis == identity.valence_basis


def test_malformed_valence_form_has_typed_refusal():
    with pytest.raises(IdentityRefusal) as refused:
        parse_valence_basis("Cr2plus_over_total_Fe", species_formula="Cr")

    assert refused.value.reason is RefusalReason.INVALID_SOURCE


def test_multi_state_fraction_numerator_has_typed_refusal():
    with pytest.raises(IdentityRefusal) as refused:
        ValenceBasis(
            numerator_oxidation_states=(3, 4, 5),
            denominator_kind=ValenceDenominatorKind.TOTAL_ELEMENT,
            denominator_oxidation_state=None,
            value_form=ValenceValueForm.FRACTION,
        )

    assert refused.value.reason is RefusalReason.INVALID_IDENTITY
    assert refused.value.field == "valence_basis.numerator_oxidation_states"


def test_sulfide_to_sulfate_printed_ratio_accepts_negative_state():
    sulfide_to_sulfate = parse_valence_basis(
        "S2minus_over_S6plus", species_formula="S"
    )
    sulfate_fraction = parse_valence_basis(
        "S6plus_over_sum_S", species_formula="S"
    )

    assert sulfide_to_sulfate.numerator_oxidation_states == (-2,)
    assert sulfide_to_sulfate.denominator_kind is ValenceDenominatorKind.OXIDATION_STATE
    assert sulfide_to_sulfate.denominator_oxidation_state == 6
    assert sulfide_to_sulfate.value_form is ValenceValueForm.RATIO
    assert sulfate_fraction.numerator_oxidation_states == (6,)
    assert sulfate_fraction.denominator_kind is ValenceDenominatorKind.TOTAL_ELEMENT
    assert sulfate_fraction.denominator_oxidation_state is None
    assert sulfate_fraction.value_form is ValenceValueForm.FRACTION


def test_sulfur_valence_forms_are_recognized_and_migrated_as_printed(tmp_path: Path):
    printed_values = (
        ("S2minus_over_S6plus", "0.32"),
        ("S2minus_over_total_S", "0.50"),
        ("log10_S2minus_over_S6plus", "-2.0"),
    )
    for printed, _value in printed_values:
        quantity, reason = map_quantity(
            "concentration_series", {"quantity": printed}
        )
        assert quantity.is_value and quantity.value is Quantity.VALENCE_RATIO
        assert reason is None

    doc = yaml.safe_load(SOSSI_CR.read_text(encoding="utf-8"))
    observations = []
    for index, (printed, value) in enumerate(printed_values):
        observations.append(
            {
                "observation_id": f"fixture_sulfur_valence_{index}",
                "type": "concentration_series",
                "locator": {"page": 1, "table": "1"},
                "phase": "liquid",
                "units": "fraction",
                "values": {
                    "method_class": "measured_direct",
                    "quantity": printed,
                    "admission_status": "admitted",
                    "value": value,
                },
            }
        )
    doc["species"] = {"S": {"observations": observations}}
    extract = tmp_path / SOSSI_CR.name
    extract.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    migrator = Migrator(ROOT)
    migrator._migrate_extract(extract)
    migrator.finalize()
    rows = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
        if "fixture_sulfur_valence_" in row.observation_id
    }

    assert set(rows) == {
        "fixture_sulfur_valence_0",
        "fixture_sulfur_valence_1",
        "fixture_sulfur_valence_2",
    }
    assert [rows[f"fixture_sulfur_valence_{i}"].value.point for i in range(3)] == [
        Decimal("0.32"),
        Decimal("0.50"),
        Decimal("-2.0"),
    ]
    assert all(
        rows[f"fixture_sulfur_valence_{i}"].identity.quantity.value
        is Quantity.VALENCE_RATIO
        for i in range(3)
    )


def test_fe_plus_spelling_stays_on_existing_fe_ratio_quantity():
    quantity, reason = map_quantity(
        "ratio", {"quantity": "Fe3plus_over_Fe2plus"}
    )

    assert quantity.is_value
    assert quantity.value is Quantity.FE3_FE2_RATIO
    assert reason is None


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
        "berry_2004_table3_ad_r21": Decimal("0.936"),
        "berry_2004_table3_ad_fo_r21": Decimal("0.919"),
    }
    for local_id, expected in render_pins.items():
        row = by_local_id[local_id]
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == expected
        assert row.locator.published_page == 794

    duplicate_author_row = {
        local_id: by_local_id[local_id]
        for local_id in (
            "berry_2004_table3_ad_r21",
            "berry_2004_table3_ad_fo_r21",
        )
    }
    assert {
        local_id: row.value.point for local_id, row in duplicate_author_row.items()
    } == {
        "berry_2004_table3_ad_r21": Decimal("0.936"),
        "berry_2004_table3_ad_fo_r21": Decimal("0.919"),
    }
    assert duplicate_author_row["berry_2004_table3_ad_r21"].locator.record.endswith(
        "AD column; Cr2+/SumCr cell"
    )
    assert duplicate_author_row[
        "berry_2004_table3_ad_fo_r21"
    ].locator.record.endswith("AD + Fo column; Cr2+/SumCr cell")

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

    for row in fractions:
        assert row.derivation is not None
        assert len(row.derivation.parameters) == 1
        name, area = row.derivation.parameters[0]
        assert name == "A(1s/4s)"
        assert area.state.is_value
        assert area.locator is not None
        assert area.locator.published_page == 794
        assert "A(1s/4s)" in area.locator.record
        assert not row.derived_from
        assert not row.derivation.inputs
        assert row.derivation.raw_inputs is not None
        assert row.derivation.raw_inputs.state.value == "not_printed"
        assert row.derivation.raw_inputs.locator is not None
        assert row.derivation.raw_inputs.locator.table is None
        assert row.derivation.raw_inputs.locator.section == "Experimental methods"
        assert "Raw XANES spectra" in row.derivation.raw_inputs.locator.record
        assert selected_lineage_level(row, set(result.observations))

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


def test_refused_printed_valence_forms_do_not_select_scalar(tmp_path: Path):
    doc = yaml.safe_load(SOSSI_CR.read_text(encoding="utf-8"))
    template = doc["species"]["Cr"]["observations"][0]
    printed_forms = (
        "log10_Cr2plus_over_total_Cr",
        "not_a_plus_over_real",
    )
    observations = []
    for index, printed in enumerate(printed_forms):
        row = copy.deepcopy(template)
        row.update(
            {
                "observation_id": f"fixture_refused_valence_{index}",
                "phase": "liquid",
                "values": {
                    "method_class": "measured_direct",
                    "quantity": printed,
                    "admission_status": "admitted",
                    "value": "0.32",
                },
            }
        )
        observations.append(row)
    doc["species"]["Cr"]["observations"] = observations
    extract = tmp_path / SOSSI_CR.name
    extract.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    migrator = Migrator(ROOT)
    migrator._migrate_extract(extract)
    migrator.finalize()
    rows = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
        if "fixture_refused_valence_" in row.observation_id
    }

    assert set(rows) == {
        "fixture_refused_valence_0",
        "fixture_refused_valence_1",
    }
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in rows.values())

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


def _migrate_valence_fixture(tmp_path: Path, quantity: str, value: str):
    document = yaml.safe_load(SOSSI_CR.read_text(encoding="utf-8"))
    row = copy.deepcopy(document["species"]["Cr"]["observations"][0])
    row.update(
        {
            "observation_id": "invalid_valence_fixture",
            "phase": "l",
            "values": {
                "method_class": "measured_direct",
                "quantity": quantity,
                "admission_status": "admitted",
                "value": value,
            },
        }
    )
    document["species"]["Cr"]["observations"] = [row]
    extract = tmp_path / SOSSI_CR.name
    extract.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    migrator = Migrator(ROOT)
    migrator._migrate_extract(extract)
    migrator.finalize()
    observation = next(
        row
        for row in migrator.result.observations.values()
        if row.observation_id.endswith("::invalid_valence_fixture")
    )
    return migrator, observation


@pytest.mark.parametrize(
    ("value", "excess", "metric_domain_refusal"),
    (
        ("1.2", "0.2", False),
        ("1.01", "0.01", False),
        ("-0.02", "0.02", True),
        ("-0.04", "0.04", True),
    ),
)
def test_out_of_range_fraction_valence_values_are_kept_with_notice(
    tmp_path: Path, value: str, excess: str, metric_domain_refusal: bool
):
    migrator, observation = _migrate_valence_fixture(
        tmp_path, "Cr2plus_over_total_Cr", value
    )

    assert observation.identity.quantity.is_value
    assert observation.identity.quantity.value is Quantity.VALENCE_RATIO
    assert observation.value.kind is ValueKind.POINT
    assert observation.value.point == Decimal(value)
    notices = [
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.SOURCE_VALUE_OUTSIDE_PHYSICAL_RANGE
    ]
    assert len(notices) == 1
    assert "range [0, 1]" in notices[0].reason
    assert f"by {excess}" in notices[0].reason

    score_observation = _quench_row(
        observation,
        observation_id=observation.observation_id,
        phase=Phase.L,
        value=Decimal(value),
    )
    calls = []
    residuals, _candidates = score_store(
        _quench_context(migrator.result, (score_observation,)),
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
        predict=_numeric_quench_predictor(calls),
    )
    assert calls == [score_observation]
    assert len(residuals) == 1
    assert any(
        notice.kind is NoticeKind.SOURCE_VALUE_OUTSIDE_PHYSICAL_RANGE
        for notice in residuals[0].notices
    )
    if metric_domain_refusal:
        assert residuals[0].numeric is None
        assert residuals[0].refusal is not None
        assert residuals[0].refusal.reason is RefusalReason.METRIC_DOMAIN
        assert (
            residuals[0].refusal.detail["reason"]
            == "observed_value_outside_metric_domain"
        )
        assert residuals[0].refusal.detail["observed_value"] == value
    else:
        assert residuals[0].numeric is not None


def test_average_oxidation_state_without_named_bounds_gets_no_range_notice(
    tmp_path: Path,
):
    _migrator, observation = _migrate_valence_fixture(
        tmp_path, "average_oxidation_state", "4.1"
    )

    assert observation.value.kind is ValueKind.POINT
    assert observation.value.point == Decimal("4.1")
    assert observation.identity.valence_basis.is_value
    basis = observation.identity.valence_basis.value
    assert basis.value_form is ValenceValueForm.AVERAGE_OXIDATION_STATE
    assert basis.numerator_oxidation_states == ()
    assert basis.denominator_oxidation_state is None
    assert not any(
        notice.kind is NoticeKind.SOURCE_VALUE_OUTSIDE_PHYSICAL_RANGE
        for notice in observation.notices
    )


def test_out_of_range_fraction_prediction_keeps_numeric_with_notice():
    result = _migrate_sossi_cr()
    template = next(
        observation
        for observation in result.observations.values()
        if observation.value.kind is ValueKind.POINT
        and observation.value.point == Decimal("0.32")
    )
    observation = _quench_row(
        template,
        observation_id=template.observation_id,
        phase=Phase.L,
        value=Decimal("0.32"),
    )
    calls = []
    residuals, _candidates = score_store(
        _quench_context(result, (observation,)),
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
        predict=_numeric_quench_predictor(calls, value=Decimal("1.2")),
    )

    assert calls == [observation]
    assert len(residuals) == 1
    residual = residuals[0]
    assert residual.numeric is not None
    assert float(residual.numeric.value) == pytest.approx(0.5740312677277188)
    notice = next(
        notice
        for notice in residual.notices
        if notice.kind is NoticeKind.PREDICTION_OUTSIDE_PHYSICAL_RANGE
    )
    assert "prediction" in notice.reason
    assert "range [0, 1]" in notice.reason
    assert "by 0.2" in notice.reason


def test_negative_ratio_valence_value_remains_a_typed_refusal(tmp_path: Path):
    migrator, observation = _migrate_valence_fixture(
        tmp_path, "Cr3plus_over_Cr2plus", "-0.1"
    )

    assert observation.identity.quantity.is_unknown
    assert "invalid_source: value:" in observation.identity.quantity.reason
    assert observation.value.kind is ValueKind.UNAVAILABLE
    refusal = next(
        entry
        for entry in migrator.result.queue
        if entry.observation_id == observation.observation_id
        and entry.axes == ["value"]
    )
    assert refusal.axes == ["value"]
    assert "ratio valence value" in refusal.why

    calls = []
    score_store(
        _quench_context(migrator.result, (observation,)),
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
        predict=_numeric_quench_predictor(calls),
    )
    assert calls == []
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


def test_glass_valence_rows_keep_quench_labels_on_counted_refusals():
    result = _migrate_sossi_cr()
    template = _valence_template(result)
    rows = tuple(
        _quench_row(
            template,
            observation_id=f"glass-refused-{index}",
            phase=Phase.GLASS,
            value=value,
            quench_state=QuenchState.ASSERTED_PRESERVED,
        )
        for index, value in enumerate(("0.32", "0.91"))
    )
    context = _quench_context(result, rows)
    residuals, _candidates = score_store(
        context,
        engines=SCORE_ENGINE_SET,
        rail=Rail.REDOX,
    )

    assert len(residuals) == len(rows) * len(SCORE_ENGINE_SET)
    for residual in residuals:
        assert residual.numeric is None
        assert residual.refusal is not None
        assert residual.refusal.reason is RefusalReason.UNSUPPORTED
        assert residual.quench_state is QuenchState.ASSERTED_PRESERVED
        assert residual_to_plain(residual)["quench_state"] == "asserted_preserved"

    redox_headline = [
        row
        for row in headline_rows(
            residuals,
            context=context,
            engines=SCORE_ENGINE_SET,
        )
        if row["rail"] == Rail.REDOX.value
    ]
    assert sum(row["n_candidates"] for row in redox_headline) == len(residuals)
    assert sum(row["n_refused"] for row in redox_headline) == len(residuals)


def _valence_template(result):
    return next(
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.VALENCE_RATIO
    )


def _quench_row(
    template,
    *,
    observation_id,
    phase,
    value,
    experiment_id=None,
    quench_state=None,
    quench_direction=None,
):
    composition = Composition(
        basis="oxide_mole_fraction",
        components=(("SiO2", Decimal("0.5")), ("FeO", Decimal("0.5"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = replace(
        template.identity,
        species=replace(template.identity.species, phase=phase),
        temperature_K=State.of(Decimal("1673.15")),
        fO2_Pa=State.of(Decimal("1e-5")),
        total_pressure_Pa=State.of(Decimal("1e-4")),
        composition=State.of(composition),
    )
    point_conditions = dict(template.point_conditions or {})
    point_conditions["total_pressure_Pa"] = Located(
        State.of(Decimal("1e-4")), locator=Locator(page=12)
    )
    if quench_state is not None:
        point_conditions["quench_state"] = Located(
            State.of(quench_state), locator=Locator(page=12)
        )
    if quench_direction is not None:
        point_conditions["quench_direction"] = Located(
            State.of(quench_direction), locator=Locator(page=12)
        )
    return replace(
        template,
        observation_id=observation_id,
        experiment_id=experiment_id or template.experiment_id,
        identity=identity,
        value=Value.point_of(value),
        evidence=replace(
            template.evidence, class_=State.of(EvidenceClass.MEASURED_DIRECT)
        ),
        point_conditions=point_conditions,
    )


def _quench_context(result, rows):
    experiments = dict(result.experiments)
    template_experiment = next(iter(experiments.values()))
    for experiment_id in {row.experiment_id for row in rows}:
        experiments.setdefault(
            experiment_id,
            replace(template_experiment, experiment_id=experiment_id),
        )
    observations = {row.observation_id: row for row in rows}
    return ScoreContext(
        result.works,
        experiments,
        observations,
        origins={row.observation_id: "quench-fixture" for row in rows},
        benches=result.benches,
        root=ROOT,
    )


def _numeric_quench_predictor(calls, *, value=Decimal("0.5")):
    def predict(engine, reference, **_kwargs):
        calls.append(reference)
        return EnginePrediction(
            engine=engine,
            channel=engine.value,
            execution=Execution(
                state=ExecutionState.PRODUCED, call_evidence="test:quench"
            ),
            value=value,
            unit="dimensionless",
            authority=Authority.CERTIFIED,
            identity=reference.identity,
        )

    return predict


def test_quench_state_is_closed_typed_located_and_round_trips():
    template = _valence_template(_migrate_sossi_cr())
    for state in QuenchState:
        row = _quench_row(
            template,
            observation_id=f"quench-{state.value}",
            phase=Phase.GLASS,
            value="0.4",
            quench_state=state,
        )
        assert row.quench_state is state
        encoded = to_plain(row)
        decoded = observation_from_plain(encoded)
        assert decoded.quench_state is state
        assert decoded.point_conditions["quench_state"].locator.page == 12

    with pytest.raises(ValueError, match="quench_state must be one of"):
        _quench_row(
            template,
            observation_id="quench-invalid",
            phase=Phase.GLASS,
            value="0.4",
            quench_state="assumed_preserved",
        )
    with pytest.raises(ValueError, match="requires a source locator"):
        replace(
            template,
            point_conditions={
                **(template.point_conditions or {}),
                "quench_state": Located(State.of("not_stated")),
            },
        )


def test_extract_quench_state_uses_the_typed_point_condition_key(tmp_path: Path):
    document = yaml.safe_load(SOSSI_CR.read_text(encoding="utf-8"))
    row = copy.deepcopy(document["species"]["Cr"]["observations"][0])
    row["observation_id"] = "quench_condition_fixture"
    row["point_conditions"] = {
        "quench_state": {
            "state": {"tag": "value", "value": "asserted_preserved"},
            "locator": {"page": 12, "table": "3"},
        }
    }
    document["species"]["Cr"]["observations"] = [row]
    extract = tmp_path / SOSSI_CR.name
    extract.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    migrator = Migrator(ROOT)
    migrator._migrate_extract(extract)
    migrator.finalize()
    migrated_rows = [
        row
        for row in migrator.result.observations.values()
        if "quench_condition_fixture" in row.observation_id
    ]
    assert migrated_rows
    assert all(
        row.quench_state is QuenchState.ASSERTED_PRESERVED
        and row.point_conditions["quench_state"].locator.page == 12
        for row in migrated_rows
    )


@pytest.mark.parametrize(
    ("condition_key", "invalid_value"),
    (
        ("quench_state", "assumed_preserved"),
        ("quench_direction", "sideways"),
    ),
)
def test_extract_malformed_quench_condition_is_a_row_refusal(
    tmp_path: Path, condition_key: str, invalid_value: str
):
    document = yaml.safe_load(SOSSI_CR.read_text(encoding="utf-8"))
    template = document["species"]["Cr"]["observations"][0]
    invalid = copy.deepcopy(template)
    invalid["observation_id"] = "quench_invalid_fixture"
    invalid["point_conditions"] = {
        condition_key: {
            "state": {"tag": "value", "value": invalid_value},
            "locator": {"page": 12, "table": "3"},
        }
    }
    valid = copy.deepcopy(template)
    valid["observation_id"] = "quench_valid_fixture"
    document["species"]["Cr"]["observations"] = [invalid, valid]
    extract = tmp_path / SOSSI_CR.name
    extract.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    migrator = Migrator(ROOT)
    migrator._migrate_extract(extract)
    migrator.finalize()

    assert not any(
        "quench_invalid_fixture" in observation.observation_id
        for observation in migrator.result.observations.values()
    )
    assert any(
        "quench_valid_fixture" in observation.observation_id
        for observation in migrator.result.observations.values()
    )
    refusal = next(
        issue
        for issue in migrator.result.validation.hard_issues
        if "quench_invalid_fixture" in issue.path
    )
    assert refusal.reason is RefusalReason.INVALID_SOURCE
    assert refusal.path.endswith(f".point_conditions.{condition_key}")
    assert f"{condition_key} must be one of" in refusal.detail


@pytest.mark.parametrize(
    (
        "extract_name",
        "observation_id",
        "ferric_fraction",
        "glass_rows",
        "unknown_rows",
    ),
    (
        (
            "aithala-2026-magmatic-iron-redox-to-2100c.yaml",
            "aithala_2026_vf271_xanes",
            "0.816",
            16,
            12,
        ),
        (
            "matzen-2022-martian-basalt-redox.yaml",
            "matzen_2022_run_7_method_a",
            "0.69",
            24,
            0,
        ),
        (
            "sossi-2020-magma-ocean-redox.yaml",
            "sossi_2020_per_f_o2_1_xanes",
            "0.44",
            12,
            0,
        ),
    ),
)
def test_quenched_glass_fe_valence_extracts_migrate_as_glass(
    extract_name: str,
    observation_id: str,
    ferric_fraction: str,
    glass_rows: int,
    unknown_rows: int,
):
    extract = ROOT / "data/literature/extracts" / extract_name
    migrator = Migrator(ROOT)
    migrator._migrate_extract(extract)
    migrator.finalize()
    rows = list(migrator.result.observations.values())
    phases = Counter(phase_token(row.identity.species) for row in rows)
    assert phases == Counter({Phase.GLASS: glass_rows, None: unknown_rows})

    row = next(
        row
        for row in rows
        if row.observation_id.endswith(f"::{observation_id}")
    )
    assert row.identity.quantity.value is Quantity.FE3_FE2_RATIO
    assert phase_token(row.identity.species) is Phase.GLASS
    assert row.value.kind is ValueKind.POINT
    assert row.value.point == fe3_fe2_ratio_from_ferric_fraction(
        Decimal(ferric_fraction)
    )
    assert row.quench_state is QuenchState.NOT_STATED

    context = _quench_context(migrator.result, (row,))
    calls = []
    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
        predict=_numeric_quench_predictor(calls),
    )
    assert len(residuals) == 1
    assert residuals[0].quench_state is QuenchState.NOT_STATED
    assert len(calls) == 1
    assert calls[0].identity.species.phase.value is Phase.L


def test_glass_valence_is_scored_against_the_run_melt_and_labelled():
    result = _migrate_sossi_cr()
    template = _valence_template(result)
    glass = _quench_row(
        template,
        observation_id="glass-valence",
        phase=Phase.GLASS,
        value="0.25",
        quench_state=QuenchState.NOT_STATED,
    )
    in_situ = _quench_row(
        template,
        observation_id="in-situ-valence",
        phase=Phase.L,
        value="0.25",
    )
    context = _quench_context(result, (glass, in_situ))
    calls = []
    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
        predict=_numeric_quench_predictor(calls),
    )

    assert {row.reference for row in residuals} == {
        "glass-valence",
        "in-situ-valence",
    }
    assert all(row.numeric is not None for row in residuals)
    glass_residual = next(row for row in residuals if row.reference == "glass-valence")
    assert glass_residual.quench_state is QuenchState.NOT_STATED
    assert template.identity.species.phase.value is Phase.L
    assert glass.identity.species.phase.value is Phase.GLASS
    glass_input = next(
        call for call in calls if call.observation_id == "glass-valence"
    )
    assert glass_input.identity.species.phase.value is Phase.L
    assert glass_input.identity.temperature_K == glass.identity.temperature_K
    assert glass_input.identity.fO2_Pa == glass.identity.fO2_Pa
    assert glass_input.identity.composition == glass.identity.composition


def test_asserted_altered_is_flagged_out_of_headline_but_kept_in_report():
    result = _migrate_sossi_cr()
    template = _valence_template(result)
    not_stated = _quench_row(
        template,
        observation_id="glass-not-stated",
        phase=Phase.GLASS,
        value="0.25",
        quench_state=QuenchState.NOT_STATED,
    )
    altered = _quench_row(
        template,
        observation_id="glass-altered",
        phase=Phase.GLASS,
        value="0.5",
        quench_state=QuenchState.ASSERTED_ALTERED,
    )
    context = _quench_context(result, (not_stated, altered))
    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
        predict=_numeric_quench_predictor([]),
    )

    altered_residual = next(
        row for row in residuals if row.reference == "glass-altered"
    )
    assert altered_residual.numeric is not None
    assert altered_residual.quench_state is QuenchState.ASSERTED_ALTERED
    assert any(
        notice.kind is NoticeKind.QUENCH_ALTERED
        for notice in altered_residual.notices
    )
    assert "quench_altered" in flagged_strata(altered_residual.notices)
    payload = residual_to_plain(altered_residual)
    assert payload["quench_state"] == "asserted_altered"
    assert any(
        notice["kind"] == "quench_altered" for notice in payload["notices"]
    )

    headline = headline_rows(
        residuals,
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    redox = [
        row
        for row in headline
        if row["rail"] == Rail.REDOX.value
        and row["engine"] == Engine.INTERNAL_ANALYTICAL.value
    ]
    assert sum(row["n"] for row in redox) == 1
    assert any(row.quench_state is QuenchState.NOT_STATED for row in residuals)
    report = render_score_report(
        residuals,
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        root=ROOT,
    )
    assert "quench_altered" in report


def test_paired_quench_report_has_log10_difference_and_identical_ratio_zero():
    result = _migrate_sossi_cr()
    template = _valence_template(result)
    first_experiment = template.experiment_id
    second_experiment = f"{first_experiment}-paired-2"
    rows = (
        _quench_row(
            template,
            observation_id="pair-1-glass",
            phase=Phase.GLASS,
            value="0.5",
            experiment_id=first_experiment,
            quench_state=QuenchState.ASSERTED_PRESERVED,
        ),
        _quench_row(
            template,
            observation_id="pair-1-liquid",
            phase=Phase.L,
            value="0.25",
            experiment_id=first_experiment,
        ),
        _quench_row(
            template,
            observation_id="pair-2-glass",
            phase=Phase.GLASS,
            value="0.4",
            experiment_id=second_experiment,
        ),
        _quench_row(
            template,
            observation_id="pair-2-liquid",
            phase=Phase.L,
            value="0.4",
            experiment_id=second_experiment,
        ),
    )
    context = _quench_context(result, rows)
    diagnostics = paired_quench_diagnostics(context)
    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
        predict=_numeric_quench_predictor([]),
    )
    by_glass_id = {row["glass_observation"]: row for row in diagnostics}

    assert {row.reference for row in residuals} == {row.observation_id for row in rows}
    assert by_glass_id["pair-1-glass"]["delta_log10"] == Decimal("2").log10()
    assert by_glass_id["pair-2-glass"]["delta_log10"] == Decimal(0)
    report = render_score_report(
        residuals,
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        root=ROOT,
    )
    assert "## Paired in-situ / quenched valence diagnostics" in report
    assert str(Decimal("2").log10()) in report
    payload_report = render_score_report_from_payloads(
        [residual_to_plain(row) for row in residuals],
        engines=(Engine.INTERNAL_ANALYTICAL,),
        hostname="test",
        observations=context.observations,
        origins=context.origins,
    )
    assert "## Paired in-situ / quenched valence diagnostics" in payload_report
    assert str(Decimal("2").log10()) in payload_report


def test_paired_quench_diagnostics_requires_matching_source_id():
    result = _migrate_sossi_cr()
    template = _valence_template(result)
    glass = _quench_row(
        template,
        observation_id="cross-source-glass",
        phase=Phase.GLASS,
        value="0.5",
    )
    in_situ = _quench_row(
        template,
        observation_id="cross-source-liquid",
        phase=Phase.L,
        value="0.25",
    )
    foreign_in_situ = replace(in_situ, source_id="different-source")

    assert glass.experiment_id == foreign_in_situ.experiment_id
    assert glass.source_id != foreign_in_situ.source_id
    assert paired_quench_diagnostics(
        _quench_context(result, (glass, foreign_in_situ))
    ) == ()


def test_oxidising_cr_glass_carries_lower_bound_notice_without_gating_score():
    result = _migrate_sossi_cr()
    template = _valence_template(result)
    identity = replace(
        template.identity,
        valence_basis=State.of(
            parse_valence_basis("Cr2plus_over_total_Cr", species_formula="Cr")
        ),
    )
    cr_template = replace(template, identity=identity)
    glass = _quench_row(
        cr_template,
        observation_id="cr-glass-oxidising-quench",
        phase=Phase.GLASS,
        value="0.25",
        quench_direction=QuenchDirection.OXIDISING,
    )
    context = _quench_context(result, (glass,))
    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
        predict=_numeric_quench_predictor([]),
    )

    residual = next(row for row in residuals if row.reference == glass.observation_id)
    assert residual.numeric is not None
    notice = next(
        notice
        for notice in residual.notices
        if notice.kind is NoticeKind.QUENCH_OXIDISING
    )
    assert "lower bound" in notice.reason
    assert "quench_oxidising" not in flagged_strata(residual.notices)
