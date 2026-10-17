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
    score_store,
)


ROOT = Path(__file__).resolve().parents[2]
SOSSI_CR = ROOT / "data/literature/extracts/kems-045-sossi-2018-pnas-cr.yaml"


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


def _numeric_quench_predictor(calls):
    def predict(engine, reference, **_kwargs):
        calls.append(reference)
        return EnginePrediction(
            engine=engine,
            channel=engine.value,
            execution=Execution(
                state=ExecutionState.PRODUCED, call_evidence="test:quench"
            ),
            value=Decimal("0.5"),
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


def test_extract_malformed_quench_state_is_a_row_refusal(tmp_path: Path):
    document = yaml.safe_load(SOSSI_CR.read_text(encoding="utf-8"))
    template = document["species"]["Cr"]["observations"][0]
    invalid = copy.deepcopy(template)
    invalid["observation_id"] = "quench_invalid_fixture"
    invalid["point_conditions"] = {
        "quench_state": {
            "state": {"tag": "value", "value": "assumed_preserved"},
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
    assert refusal.path.endswith(".point_conditions.quench_state")
    assert "quench_state must be one of" in refusal.detail


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
