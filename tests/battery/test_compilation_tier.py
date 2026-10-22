"""Compilation tier: series cells as points, engine thermo, separate headline.

Printed anchors are JANAF Na-014 Na2O(l) at 2200 K: ΔfG° = −3.886 kJ/mol,
log10 Kf = 0.092. The Ellingham segment at that temperature is
4 Na(g)+O2 → 2 Na2O(l), dG = −7.4056 kJ/mol O2.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from simulator.battery.compilation_tier import (
    _parse_product,
    _iter_compilation_series_cells,
    compilation_family,
    predict_thermo_attempt,
)
from simulator.battery.enums import (
    AdmissionStatus,
    AssetRole,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    NoticeKind,
    PerBasis,
    Phase,
    Polymorph,
    Quantity,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.identity import (
    log10K_from_delta_fG_kJ_mol,
    standard_pressure_delta_g_kJ_per_mol,
)
from simulator.battery.records import (
    SourceFile,
    SourceFiles,
    Species,
    State,
    Uncertainty,
    Value,
)
from simulator.battery.score import (
    EnginePrediction,
    ScoreContext,
    compile_residual,
    comparison_candidates,
    decision_band_for,
    headline_rows,
    predict_with_engine,
    render_score_report,
)
from simulator.chemistry.ellingham_thermo import ELLINGHAM_FIT_SEGMENTS
from tests.battery import factories as F

_PRINTED_DFG = Decimal("-3.886")
_PRINTED_LOGK = Decimal("0.092")
_T = Decimal("2200")


def _na2o_liquid(quantity: Quantity = Quantity.DELTA_FG):
    identity = F.oxide_identity(
        "Na2O",
        Phase.L,
        T_K=_T,
        per=PerBasis.MOL_SPECIES,
        metal_formula="Na",
        nu_metal=Fraction(4),
        nu_o2=Fraction(1),
    )
    if quantity is not Quantity.DELTA_FG:
        identity = replace(identity, quantity=quantity)
    return identity


def _pure_phase_identity(
    quantity: Quantity = Quantity.CP,
    *,
    polymorph: str | None = Polymorph.PERICLASE.value,
):
    identity = F.oxide_identity(
        "MgO",
        Phase.CR,
        T_K=Decimal("1000"),
        per=PerBasis.MOL_SPECIES,
        metal_formula="Mg",
        polymorph=(
            Polymorph.PERICLASE.value if polymorph is None else polymorph
        ),
    )
    if polymorph is None:
        identity = replace(
            identity,
            species=replace(
                identity.species,
                polymorph=State.unknown("printed polymorph unknown"),
            ),
        )
    return _pure_standard_identity(identity, quantity)


def _pure_standard_identity(identity, quantity: Quantity):
    return replace(
        identity,
        quantity=quantity,
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )


def _fusion_identity(
    quantity: Quantity,
    temperature: Decimal,
    *,
    formula: str = "MgO",
    polymorph: Polymorph = Polymorph.PERICLASE,
    phase: Phase = Phase.CR,
):
    identity = F.oxide_identity(
        formula,
        phase,
        T_K=temperature,
        per=PerBasis.MOL_SPECIES,
        polymorph=polymorph.value,
    )
    return replace(
        identity,
        quantity=quantity,
        per=State.not_applicable("not a fusion basis"),
        standard_pressure_Pa=State.not_applicable("not a pressure-dependent property"),
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
        subtype=State.of("solid_to_liquid"),
    )


def test_fusion_predictor_selects_the_stated_silica_polymorph_table() -> None:
    from simulator.battery.score import predict_with_engine

    experiment = F.tabulation_experiment()
    for polymorph, solid_table in (
        (Polymorph.QUARTZ, "O-037"),
        (Polymorph.CRISTOBALITE_HIGH, "O-035"),
    ):
        identity = _fusion_identity(
            Quantity.ENTHALPY_OF_FUSION,
            Decimal("1986"),
            formula="SiO2",
            polymorph=polymorph,
        )
        observation = F.observation(
            f"silica-fusion-{polymorph.value}",
            experiment.experiment_id,
            identity,
            Decimal("9.4"),
        )
        attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
        assert attempt.value is not None
        assert attempt.coefficient_sources == (solid_table, "O-038")
        assert f"janaf-fusion:{solid_table}/O-038:" in attempt.call_evidence
        prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, observation)
        assert prediction.coefficient_sources == (solid_table, "O-038")


def test_fusion_predictor_refuses_unavailable_stated_polymorph() -> None:
    from simulator.battery.compilation_tier import _prediction_from_attempt
    from simulator.battery.score import predict_with_engine

    experiment = F.tabulation_experiment()
    identity = _fusion_identity(
        Quantity.ENTHALPY_OF_FUSION,
        Decimal("1986"),
        formula="SiO2",
        polymorph=Polymorph.TRIDYMITE,
    )
    observation = F.observation(
        "silica-fusion-tridymite",
        experiment.experiment_id,
        identity,
        Decimal("9.4"),
    )
    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert attempt.coefficient_sources == ()
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, observation)
    assert prediction.coefficient_sources == ()
    predictions = []

    def predict_with_tier(engine, row, **_kwargs):
        prediction = _prediction_from_attempt(engine, row, attempt)
        predictions.append(prediction)
        return prediction

    residual, _ = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=_context(observation),
        comparison_ids=set(),
        predict=predict_with_tier,
    )
    assert residual.status is ResidualStatus.REFUSED
    assert predictions[0].coefficient_sources == ()


def test_fusion_predictor_refuses_a_stated_polymorph_on_an_unlisted_table(
    monkeypatch,
) -> None:
    from simulator.battery.generators import janaf
    from simulator.battery.score import predict_with_engine

    key = ("SiO2", "Tridymite")
    monkeypatch.setitem(janaf.JANAF_CRYSTAL_TABLE_IDS, key, ("O-999",))
    monkeypatch.setitem(janaf.JANAF_CRYSTAL_DICTIONARY, key, Polymorph.TRIDYMITE)

    experiment = F.tabulation_experiment()
    identity = _fusion_identity(
        Quantity.ENTHALPY_OF_FUSION,
        Decimal("1986"),
        formula="SiO2",
        polymorph=Polymorph.TRIDYMITE,
    )
    observation = F.observation(
        "silica-fusion-unlisted-table",
        experiment.experiment_id,
        identity,
        Decimal("9.4"),
    )
    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, observation
    ).coefficient_sources == ()


def test_fusion_predictor_flags_an_unstated_polymorph_from_stable_tables() -> None:
    experiment = F.tabulation_experiment()
    identity = _fusion_identity(
        Quantity.ENTHALPY_OF_FUSION,
        Decimal("1986"),
        formula="SiO2",
        polymorph=Polymorph.CRISTOBALITE_HIGH,
    )
    identity = replace(
        identity,
        species=replace(
            identity.species,
            polymorph=State.unknown("printed polymorph unknown"),
        ),
    )
    observation = F.observation(
        "silica-fusion-unstated",
        experiment.experiment_id,
        identity,
        Decimal("9.4"),
    )
    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
    assert attempt.value is not None
    assert attempt.refusal_reason is None
    assert attempt.notices[0].kind is NoticeKind.DERIVATION_USES_COMPILATION
    assert "predictor used O-035 (cristobalite_high)" in attempt.notices[0].reason
    assert "alternatives: quartz" in attempt.notices[0].reason


def test_fusion_predictor_refuses_a_noncrystalline_solid_identity() -> None:
    experiment = F.tabulation_experiment()
    identity = _fusion_identity(
        Quantity.ENTHALPY_OF_FUSION,
        Decimal("3000"),
        phase=Phase.L,
    )
    observation = F.observation(
        "fusion-liquid-identity",
        experiment.experiment_id,
        identity,
        Decimal("77.028"),
    )
    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH


_VAPOROCK_GAS_TABLE = {
    "K": "K-005",
    "Fe": "Fe-008",
    "O2": "O-029",
    "Co": "Co-005",
}


def _gas_thermo(
    formula: str = "K",
    quantity: Quantity = Quantity.CP,
    temperature_K: Decimal = Decimal("1200"),
):
    identity = F.o2_identity(temperature_K)
    identity = replace(
        identity,
        quantity=quantity,
        species=Species(formula, Phase.G),
        reaction=State.not_applicable("pure standard-state thermo"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )
    return identity


def _same_table_gas(
    observation_id: str,
    formula: str,
    quantity: Quantity,
    temperature_K: Decimal,
    value: Decimal,
    **kwargs,
):
    """JANAF gas-thermo observation whose locator is that species' gas table."""

    from simulator.battery.records import Locator

    table_id = _VAPOROCK_GAS_TABLE[formula]
    return replace(
        F.observation(
            observation_id,
            "exp-1",
            _gas_thermo(formula, quantity, temperature_K),
            value,
            **kwargs,
        ),
        locator=Locator(table=table_id, record=table_id),
    )


def _context(*observations, works=None, experiments=None, origins=None):
    work = replace(
        F.work(),
        source_ids=("work-1", "janaf-4th", "nist-webbook", "nasa-glenn"),
    )
    experiment = F.tabulation_experiment()
    return ScoreContext(
        works=works or {work.work_id: work},
        experiments=experiments or {experiment.experiment_id: experiment},
        observations={obs.observation_id: obs for obs in observations},
        origins=origins or {},
        extract_review={obs.source_id or "": None for obs in observations},
        hostname="test",
    )


def test_compilation_no_band_label_uses_source_band_owners_reason() -> None:
    from simulator.battery.score import (
        _compilation_band_value_fields,
        _source_band_catalog,
    )

    experiment = F.tabulation_experiment()
    identity = replace(
        _na2o_liquid(Quantity.CP),
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )
    observations = tuple(
        F.observation(
            f"source-band-{index}",
            experiment.experiment_id,
            identity,
            Decimal(value),
            evidence=EvidenceClass.COMPILATION_ASSESSED,
            source_id=f"source-{index}",
        )
        for index, value in enumerate(("9", "10", "11"))
    )
    bands, _records, reasons = _source_band_catalog(_context(*observations), {})
    assert len(bands) == 1
    assert "n_sources=3" in next(iter(bands.values())).rule
    assert reasons == {}
    # The report probe has no emitted row band despite the owner's valid
    # three-source band. Rendering must use the owner's reason, not row count.
    assert _compilation_band_value_fields([], [], ())[
        "no_band_reason"
    ] is None

    _bands, _records, insufficient = _source_band_catalog(
        _context(observations[0]), {}
    )
    reason = next(iter(insufficient.values()))
    assert reason == "derived_band_insufficient_sources"
    assert _compilation_band_value_fields([], [], (reason,))[
        "no_band_reason"
    ] == reason


def _mgo_janaf_observation():
    experiment = F.tabulation_experiment()
    identity = F.oxide_identity(
        "MgO",
        Phase.CR,
        T_K=Decimal("1100"),
        per=PerBasis.MOL_SPECIES,
        polymorph=Polymorph.PERICLASE.value,
        metal_formula="Mg",
        nu_metal=Fraction(1),
        nu_o2=Fraction(1, 2),
    )
    return F.observation(
        "janaf-4th::Mg-008:T=1100",
        experiment.experiment_id,
        identity,
        Decimal("-481.399"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="janaf-4th",
    )


def _nasa9_fit_test_case():
    from simulator.battery.compilation_tier import _nasa9_fit_phase_label
    from simulator.battery.generators.compilation_thermo_fits import load_sidecar
    from simulator.battery.generators.janaf import generate_table
    from simulator.reference_data.janaf import TABLES_DIR, load_table_document

    table_id = "O-037"
    record = next(
        row
        for row in load_sidecar()
        if row["source_record_id"] == table_id
        and row["status"] == "accepted"
        and row["phase"] == "cr:beta"
        and row["residual_summary"][Quantity.CP.value]["max_abs"]
        <= row["residual_summary"][Quantity.CP.value]["gate_tolerance"]
    )
    table_path = TABLES_DIR / f"{table_id}.yaml"
    accepted_owner = generate_table(
        load_table_document(table_path), source_path=str(table_path)
    )
    observation = next(
        observation
        for observation in accepted_owner.observations
        if observation.locator is not None
        and observation.locator.record == table_id
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.CP
        and _nasa9_fit_phase_label(observation.identity) == record["phase"]
    )
    experiment = replace(
        F.tabulation_experiment(), experiment_id=observation.experiment_id
    )
    context = _context(
        observation,
        experiments={experiment.experiment_id: experiment},
    )
    point = next(
        point
        for point in _iter_compilation_series_cells(observation)
        if point.identity.temperature_K.is_value
        and record["validity"]["T_min_K"]
        <= float(point.identity.temperature_K.value)
        <= record["validity"]["T_max_K"]
    )
    return record, context, point


def test_nasa9_fit_channel_uses_accepted_cells_and_shared_residuals(
    tmp_path, monkeypatch
) -> None:
    import simulator.battery.compilation_tier as compilation_tier
    import simulator.battery.generators.compilation_thermo_fits as fit_generator
    import simulator.battery.score as score
    from simulator.battery.compilation_tier import (
        ThermoAttempt,
        _nasa9_fit_comparisons,
        iter_compilation_comparisons_jsonl,
        write_compilation_comparisons_jsonl,
    )
    from simulator.battery.enums import QUANTITY_UNITS
    from simulator.battery.generators.compilation_thermo_fits import (
        sidecar_element_reference_curves,
    )

    record, context, printed_cell = _nasa9_fit_test_case()
    temperature = printed_cell.identity.temperature_K.value
    fit_value, reason = fit_generator.evaluate_sidecar_fit(
        record,
        Quantity.CP,
        float(temperature),
        reference_curves=sidecar_element_reference_curves([record]),
    )
    assert reason is None and fit_value is not None
    gate_tolerance = record["residual_summary"][Quantity.CP.value]["gate_tolerance"]
    assert abs(fit_value - float(printed_cell.value.point)) <= gate_tolerance

    def echo_fit(engine, observation, **_kwargs):
        return ThermoAttempt(
            value=observation.value.point,
            unit=QUANTITY_UNITS[Quantity.CP],
            authority=Authority.BRIDGE,
            notices=(),
            refusal_reason=None,
            refusal_detail={},
            call_evidence="pinned-fit-test",
            coefficient_sources=(),
        )

    monkeypatch.setattr(compilation_tier, "predict_thermo_attempt", echo_fit)
    comparisons = _nasa9_fit_comparisons(
        context,
        engines=(Engine.THERMOENGINE,),
        invoke_pure_phase=False,
        fit_records=(record,),
    )
    scored = next(
        item
        for item in comparisons
        if item.quantity is Quantity.CP and item.temperature_K == temperature
    )
    assert scored.residual.numeric is not None
    assert scored.residual.numeric.value == 0

    monkeypatch.setattr(fit_generator, "load_sidecar", lambda: [record])
    monkeypatch.setattr(score, "SCORE_ENGINE_SET", (Engine.THERMOENGINE,))
    output_path = tmp_path / "compilation-comparisons.jsonl"
    assert write_compilation_comparisons_jsonl(context, output_path) == len(comparisons)
    written = tuple(iter_compilation_comparisons_jsonl(output_path))
    fit_row = next(
        row
        for row in written
        if str(row["comparison_channel"]).startswith("nasa9_fit:")
    )
    assert fit_row["comparison_channel"] == (
        f"nasa9_fit:{Engine.THERMOENGINE.value}"
    )
    assert fit_row["reference_id"].startswith("nasa9-fit::")
    assert set(fit_row) == {
        "reference_id",
        "quantity",
        "comparison_channel",
        "comparison_key",
        "comparison_quantity",
        "status",
        "value",
        "band",
        "refusal_reason",
        "refusal_detail",
    }
    assert fit_row["refusal_reason"] is None
    assert fit_row["refusal_detail"] == {}


def test_nasa9_fit_writer_serializes_typed_refusal_fields(tmp_path, monkeypatch) -> None:
    import simulator.battery.compilation_tier as compilation_tier
    import simulator.battery.generators.compilation_thermo_fits as fit_generator
    from simulator.battery.compilation_tier import (
        ThermoAttempt,
        _write_compilation_comparisons_jsonl,
        iter_compilation_comparisons_jsonl,
    )

    record, context, _printed_cell = _nasa9_fit_test_case()
    detail = {"reason": "fit-test-refusal", "engine": Engine.THERMOENGINE.value}

    def refuse_fit(_engine, _observation, **_kwargs):
        return ThermoAttempt(
            value=None,
            unit=None,
            authority=Authority.REFUSED,
            notices=(),
            refusal_reason=RefusalReason.UNSUPPORTED,
            refusal_detail=detail,
            call_evidence="fit-refusal-test",
            coefficient_sources=(),
        )

    monkeypatch.setattr(compilation_tier, "predict_thermo_attempt", refuse_fit)
    monkeypatch.setattr(fit_generator, "load_sidecar", lambda: [record])
    path = tmp_path / "refused-compilation-comparisons.jsonl"
    assert _write_compilation_comparisons_jsonl(
        context,
        path,
        engines=(Engine.THERMOENGINE,),
        invoke_pure_phase=False,
    ) > 0
    fit_row = next(
        row
        for row in iter_compilation_comparisons_jsonl(path)
        if str(row["comparison_channel"]).startswith("nasa9_fit:")
    )
    assert fit_row["status"] == ResidualStatus.REFUSED.value
    assert fit_row["refusal_reason"] == RefusalReason.UNSUPPORTED.value
    assert fit_row["refusal_detail"] == detail


def test_nasa9_fit_writer_respects_selected_engines_and_policy(tmp_path, monkeypatch) -> None:
    import simulator.battery.compilation_tier as compilation_tier
    import simulator.battery.generators.compilation_thermo_fits as fit_generator
    from simulator.battery.compilation_tier import (
        ThermoAttempt,
        _write_compilation_comparisons_jsonl,
        iter_compilation_comparisons_jsonl,
    )
    from simulator.battery.enums import QUANTITY_UNITS

    record, context, _printed_cell = _nasa9_fit_test_case()
    calls = []

    def score_selected(engine, observation, **kwargs):
        calls.append((engine, kwargs["invoke_pure_phase"]))
        assert engine is Engine.THERMOENGINE
        assert kwargs["invoke_pure_phase"] is False
        return ThermoAttempt(
            value=observation.value.point,
            unit=QUANTITY_UNITS[Quantity.CP],
            authority=Authority.BRIDGE,
            notices=(),
            refusal_reason=None,
            refusal_detail={},
            call_evidence="selected-fit-test",
            coefficient_sources=(),
        )

    monkeypatch.setattr(compilation_tier, "predict_thermo_attempt", score_selected)
    monkeypatch.setattr(fit_generator, "load_sidecar", lambda: [record])
    path = tmp_path / "selected-compilation-comparisons.jsonl"

    assert _write_compilation_comparisons_jsonl(
        context,
        path,
        engines=(Engine.THERMOENGINE,),
        invoke_pure_phase=False,
    ) > 0

    fit_rows = tuple(
        row
        for row in iter_compilation_comparisons_jsonl(path)
        if str(row["comparison_channel"]).startswith("nasa9_fit:")
    )
    assert fit_rows
    assert {row["comparison_channel"] for row in fit_rows} == {
        f"nasa9_fit:{Engine.THERMOENGINE.value}"
    }
    assert calls and set(calls) == {(Engine.THERMOENGINE, False)}


@pytest.mark.parametrize("mutation", ("outside_validity", "rejected"))
def test_nasa9_fit_mutations_produce_no_comparison(mutation, monkeypatch) -> None:
    from copy import deepcopy

    import simulator.battery.compilation_tier as compilation_tier

    record, context, _printed_cell = _nasa9_fit_test_case()
    mutated = deepcopy(record)
    if mutation == "outside_validity":
        mutated["validity"] = {"T_min_K": 10000.0, "T_max_K": 11000.0}
    else:
        mutated["status"] = "rejected"

    def no_engine_call(*_args, **_kwargs):
        pytest.fail("a fit mutation outside the accepted domain reached scoring")

    monkeypatch.setattr(compilation_tier, "predict_thermo_attempt", no_engine_call)
    assert (
        compilation_tier._nasa9_fit_comparisons(
            context,
            engines=(Engine.THERMOENGINE,),
            invoke_pure_phase=False,
            fit_records=(mutated,),
        )
        == []
    )


def test_al091_h_minus_h298_fit_restores_all_qualified_comparisons() -> None:
    from simulator.battery.compilation_tier import _nasa9_fit_comparisons
    from simulator.battery.generators.compilation_thermo_fits import load_sidecar
    from simulator.battery.generators.janaf import generate_table
    from simulator.reference_data.janaf import TABLES_DIR, load_table_document

    record = next(
        item
        for item in load_sidecar()
        if item["record_id"] == "nist-janaf-4th:Al-091:l:2500:4000"
    )
    table_path = TABLES_DIR / "Al-091.yaml"
    source = generate_table(
        load_table_document(table_path), source_path=str(table_path)
    )
    observations = tuple(
        observation
        for observation in source.observations
        if observation.locator is not None
        and observation.locator.record == "Al-091"
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value
        in {Quantity.H_MINUS_H298, Quantity.DELTA_FH}
    )
    experiments = {
        observation.experiment_id: replace(
            F.tabulation_experiment(), experiment_id=observation.experiment_id
        )
        for observation in observations
    }
    context = _context(*observations, experiments=experiments)

    comparisons = _nasa9_fit_comparisons(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        invoke_pure_phase=False,
        fit_records=(record,),
    )

    h_comparisons = [
        comparison
        for comparison in comparisons
        if comparison.quantity is Quantity.H_MINUS_H298
    ]
    assert len(h_comparisons) == 16


def test_nasa9_fit_census_is_separate_and_lists_regolith_species() -> None:
    from simulator.battery.compilation_tier import compilation_tier_census

    record, context, _printed_cell = _nasa9_fit_test_case()
    census = compilation_tier_census(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        invoke_pure_phase=False,
        audit_compile_residual=False,
    )
    fit_row = next(
        row
        for row in census["fit_rows"]
        if row["formula"] == record["formula"]
        and row["phase"] == record["phase"]
        and row["quantity"] == Quantity.CP.value
        and row["engine"] == Engine.INTERNAL_ANALYTICAL.value
    )
    assert fit_row["reachable"] > 0
    assert fit_row["numeric"] == 0
    assert fit_row["refused"]
    assert fit_row in census["regolith_species_table"]["rows"]


def test_compilation_comparison_sidecar_is_additive_and_streamed(tmp_path) -> None:
    import hashlib
    from pathlib import Path

    from simulator.battery.compilation_tier import (
        iter_compilation_comparisons_jsonl,
        write_compilation_comparisons_jsonl,
    )
    from simulator.battery.score import (
        render_score_report,
        score_store,
        write_headline_summary_json,
        write_residuals_jsonl,
    )
    from simulator.battery.pins import PinBandRecord, pin_failures
    from simulator.diagnostic_helpers.species_rail_differential import (
        KeyedTablePoint,
        PHASE_SOLID,
        score_cea_point,
        score_ellingham_point,
    )

    observation = _mgo_janaf_observation()
    context = _context(observation)
    engines = (Engine.INTERNAL_ANALYTICAL,)
    residuals, candidates = score_store(context, engines=engines)
    residuals_path = tmp_path / "residuals.jsonl"
    summary_path = tmp_path / "score-summary.json"
    report_path = tmp_path / "score-report.md"
    comparison_path = tmp_path / "compilation-comparisons.jsonl"
    root = Path(__file__).resolve().parents[2]
    write_residuals_jsonl(residuals, candidates, residuals_path, root=root)
    write_headline_summary_json(
        residuals,
        summary_path,
        context=context,
        engines=engines,
        root=root,
    )
    legacy_pin = PinBandRecord(
        key=(
            f"{observation.observation_id}::delta_fG::thermochemistry::"
            "nasa_cea_9"
        ),
        expected_outcome="match",
        evidence="fixture",
        centre=Decimal("-481.399"),
        pin_band_value=Decimal("0.05"),
    )
    legacy_pin_failures = pin_failures(residuals, (legacy_pin,))
    report_path.write_text(
        render_score_report(
            residuals,
            context=context,
            engines=engines,
            root=root,
            pin_failures=legacy_pin_failures,
        ),
        encoding="utf-8",
    )
    outputs = (residuals_path, summary_path, report_path)
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in outputs
    }

    assert write_compilation_comparisons_jsonl(context, comparison_path) == 2

    rows = tuple(iter_compilation_comparisons_jsonl(comparison_path))
    point = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Mg-008:T=1100",
        formula="MgO",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=1100.0,
        delta_fG_kJ_mol=-481.399,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
        note="JANAF compilation point",
    )
    expected_scores = (score_cea_point(point), score_ellingham_point(point))
    assert [
        (row["comparison_channel"], row["value"], row["status"], row["band"])
        for row in rows
    ] == [
        ("nasa_cea_9", "-0.5409627066909479", "match", "0.05"),
        ("ellingham", "0.0006669999999076026", "match", "0.05"),
    ]
    assert [row["reference_id"] for row in rows] == [observation.observation_id] * 2
    assert [row["comparison_channel"] for row in rows] == [
        score.engine_channel for score in expected_scores if score is not None
    ]
    assert [row["value"] for row in rows] == [
        str(score.residual_kJ_mol) for score in expected_scores if score is not None
    ]
    assert [row["status"] for row in rows] == [
        score.status for score in expected_scores if score is not None
    ]
    assert [row["band"] for row in rows] == [
        str(score.band_kJ_mol) for score in expected_scores if score is not None
    ]
    nasa, ellingham = rows
    pins = (
        PinBandRecord(
            key=(
                f"{observation.observation_id}::delta_fG::thermochemistry::"
                "nasa_cea_9"
            ),
            expected_outcome="match",
            evidence="fixture",
            centre=Decimal(str(nasa["value"])),
            pin_band_value=Decimal("0.05"),
        ),
        PinBandRecord(
            key=(
                f"{observation.observation_id}::delta_fG::thermochemistry::"
                "ellingham"
            ),
            expected_outcome="match",
            evidence="fixture",
            centre=Decimal(str(ellingham["value"])) + Decimal("1"),
            pin_band_value=Decimal("0.05"),
        ),
    )
    compared: list[dict[str, str]] = []
    failures = pin_failures(
        [], pins, compilation_comparisons=rows, comparisons=compared
    )
    assert len(compared) == 2
    assert [row["within_pin_band"] for row in compared] == ["true", "false"]
    assert [failure["reason"] for failure in failures] == ["outside_pin_band"]
    assert failures[0]["source"] == "ellingham"
    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in outputs
    }
    assert after == before


def test_janaf_comparison_writer_streams_series_cell_references(tmp_path) -> None:
    from simulator.battery.compilation_tier import (
        iter_compilation_comparisons_jsonl,
        write_compilation_comparisons_jsonl,
    )

    parent = _mgo_janaf_observation()
    series = replace(
        parent,
        identity=replace(parent.identity, temperature_K=State.unknown("series")),
        value=Value(
            ValueKind.SERIES,
            series=((Decimal("1100"), Decimal("-481.399")),),
        ),
    )
    path = tmp_path / "series-comparisons.jsonl"

    assert write_compilation_comparisons_jsonl(_context(series), path) == 2
    rows = tuple(iter_compilation_comparisons_jsonl(path))
    assert [row["reference_id"] for row in rows] == [
        "janaf-4th::Mg-008:T=1100#t1100v-481.399"
    ] * 2


def test_real_janaf_rows_join_real_compilation_pins(tmp_path) -> None:
    from pathlib import Path

    from simulator.battery.compilation_tier import (
        iter_compilation_comparisons_jsonl,
        write_compilation_comparisons_jsonl,
    )
    from simulator.battery.migrate import load_yaml, observation_from_plain
    from simulator.battery.pins import load_pins, pin_failures

    root = Path(__file__).resolve().parents[2]
    requested = {"Mg-008": {"1100", "1200"}, "Al-096": {"1100", "1200"}}
    observations = []
    origins = {}
    for element, table in (("Mg", "Mg-008"), ("Al", "Al-096")):
        relative = f"compilations-janaf/janaf-{element}.yaml"
        document = load_yaml(root / "data" / "literature" / "observations-v2" / relative)
        raw = next(
            row
            for row in document["observations"]
            if row.get("locator", {}).get("record") == table
            and row.get("identity", {}).get("quantity", {}).get("value") == "delta_fG"
            and requested[table]
            <= {temperature for temperature, _ in row.get("value", {}).get("series", ())}
        )
        observation = observation_from_plain(raw)
        observations.append(observation)
        origins[observation.observation_id] = relative

    context = _context(*observations, origins=origins)
    loaded = load_pins(root / "data" / "battery" / "pins.yaml")
    aliases = (
        f"janaf::{table}:T={temperature}::{channel}::{quantity}"
        for table in requested
        for temperature in sorted(requested[table])
        for channel, quantity in (
            ("nasa_cea_9", "delta_fG_kJ_mol"),
            ("ellingham", "delta_fG_kJ_per_mol_O2"),
        )
    )
    pins = []
    for alias in aliases:
        pin = next(
            record
            for record in loaded["pin_band_records"]
            if alias in record.aliases or record.old_key == alias
        )
        assert pin.aliases == (alias,)
        assert pin.old_key == alias
        pins.append(pin)

    sidecar = tmp_path / "compilation-comparisons.jsonl"
    assert write_compilation_comparisons_jsonl(context, sidecar) > 0
    comparisons: list[dict[str, str]] = []
    failures = pin_failures(
        [],
        pins,
        compilation_comparisons=iter_compilation_comparisons_jsonl(sidecar),
        comparisons=comparisons,
    )

    assert {row["key"] for row in comparisons} == {pin.key for pin in pins}
    assert len(comparisons) == len(pins)
    assert not any(
        failure["reason"]
        in {
            "no_compilation_comparison_for_reference",
            "ambiguous_compilation_comparison",
        }
        for failure in failures
    )
    target_alias = "janaf::Al-096:T=1100::nasa_cea_9::delta_fG_kJ_mol"
    target_pin = next(pin for pin in pins if target_alias in pin.aliases)
    legacy_failure = pin_failures([], (target_pin,))
    legacy_report = render_score_report(
        [],
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        pin_failures=legacy_failure,
        root=root,
    )
    legacy_row = next(
        line
        for line in legacy_report.splitlines()
        if line.startswith(f"| `{target_pin.key}` |")
    )
    assert legacy_row.split("|")[2].strip() == "no_live_residual_for_reference"
    comparison_report = render_score_report(
        [],
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        pin_failures=failures,
        root=root,
    )
    assert not any(
        line.startswith(f"| `{target_pin.key}` |")
        for line in comparison_report.splitlines()
    )


def test_real_compilation_pin_score_report_reason_before_sidecar() -> None:
    from pathlib import Path

    from simulator.battery.pins import load_pins, pin_failures

    root = Path(__file__).resolve().parents[2]
    target_alias = "janaf::Al-096:T=1100::nasa_cea_9::delta_fG_kJ_mol"
    pin = next(
        record
        for record in load_pins(root / "data" / "battery" / "pins.yaml")[
            "pin_band_records"
        ]
        if target_alias in record.aliases
    )
    failures = pin_failures([], (pin,))
    report = render_score_report(
        [],
        context=_context(),
        engines=(Engine.INTERNAL_ANALYTICAL,),
        pin_failures=failures,
        root=root,
    )

    row = next(line for line in report.splitlines() if line.startswith(f"| `{pin.key}` |"))
    assert row.split("|")[2].strip() == "no_live_residual_for_reference"


def test_compilation_pin_checker_exit_and_counts(tmp_path, monkeypatch, capsys) -> None:
    import json

    from scripts import check_compilation_pin_sidecar as checker
    from simulator.battery.pins import PinBandRecord

    key = "janaf::Al-096:T=1100::nasa_cea_9::delta_fG_kJ_mol"
    pin = PinBandRecord(
        key=f"{key}::delta_fG::thermochemistry::nasa_cea_9",
        expected_outcome="match",
        evidence="fixture",
        aliases=(key,),
        centre=Decimal("1"),
        pin_band_value=Decimal("0.05"),
    )
    monkeypatch.setattr(
        checker,
        "load_pins",
        lambda _path: {"pin_band_records": (pin,)},
    )
    residuals = tmp_path / "residuals.jsonl"
    residuals.write_text("", encoding="utf-8")
    sidecar = tmp_path / "compilation-comparisons.jsonl"
    args = [
        "--residuals",
        str(residuals),
        "--sidecar",
        str(sidecar),
        "--pins",
        str(tmp_path / "pins.yaml"),
    ]

    def write_comparison(value: str) -> None:
        sidecar.write_text(
            json.dumps(
                {
                    "reference_id": "nist-janaf-4th:Al-096:delta_fG:T=1100",
                    "quantity": "delta_fG",
                    "comparison_channel": "nasa_cea_9",
                    "comparison_key": key,
                    "status": "match",
                    "value": value,
                    "band": "0.05",
                }
            )
            + "\n",
            encoding="utf-8",
        )

    write_comparison("1")
    assert checker.main(args) == 0
    output = capsys.readouterr().out
    assert "pins_total=1 matched=1 in_band=1 outside_band=0 unmatched=0" in output

    write_comparison("1.1")
    assert checker.main(args) == 1
    output = capsys.readouterr().out
    assert "pins_total=1 matched=1 in_band=0 outside_band=1 unmatched=0" in output

    sidecar.write_text("", encoding="utf-8")
    assert checker.main(args) == 1
    output = capsys.readouterr().out
    assert "pins_total=1 matched=0 in_band=0 outside_band=0 unmatched=1" in output
    assert '"no_compilation_comparison_for_reference": 1' in output


def test_compilation_comparison_sidecar_failure_preserves_target(tmp_path, monkeypatch) -> None:
    import pytest

    from simulator.battery import compilation_tier
    from simulator.diagnostic_helpers import species_rail_differential

    observation = _mgo_janaf_observation()
    target = tmp_path / "compilation-comparisons.jsonl"
    target.write_bytes(b"previous sidecar\n")

    def fail(_point):
        raise RuntimeError("comparison failed")

    monkeypatch.setattr(species_rail_differential, "score_cea_point", fail)

    with pytest.raises(RuntimeError, match="comparison failed"):
        compilation_tier.write_compilation_comparisons_jsonl(
            _context(observation), target
        )

    assert target.read_bytes() == b"previous sidecar\n"
    assert list(tmp_path.iterdir()) == [target]


def test_every_ellingham_segment_names_one_product() -> None:
    for metal, segments in ELLINGHAM_FIT_SEGMENTS.items():
        for segment in segments:
            product = _parse_product(segment)
            assert product is not None, (metal, segment.phase_basis)
            assert product.formula


def test_series_cells_are_scalar_views_and_intervals_are_not_midpoints() -> None:
    from simulator.battery.records import Observation

    identity = _na2o_liquid()
    experiment = F.tabulation_experiment()
    series = F.observation(
        "na2o-series",
        experiment.experiment_id,
        replace(identity, temperature_K=State.unknown("series")),
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    series = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=(
                (_T, _PRINTED_DFG),
                (Decimal("2100"), Decimal("-10")),
            ),
        ),
    )
    cells = tuple(_iter_compilation_series_cells(series, "compilations-janaf/janaf-Na.yaml"))
    assert [cell.observation_id for cell in cells] == [
        "na2o-series#t2200v-3.886",
        "na2o-series#t2100v-10",
    ]
    assert all(not isinstance(cell, Observation) for cell in cells)
    assert all(cell.parent is series for cell in cells)
    swapped = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=(
                (Decimal("2100"), Decimal("-10")),
                (_T, _PRINTED_DFG),
            ),
        ),
    )
    assert [cell.observation_id for cell in _iter_compilation_series_cells(swapped, "compilations-janaf/janaf-Na.yaml")] == [
        "na2o-series#t2100v-10",
        "na2o-series#t2200v-3.886",
    ]
    assert cells[0].value.point == _PRINTED_DFG
    assert cells[1].value.point == Decimal("-10")
    assert cells[0].identity.temperature_K.value == _T
    # Not the mean of −3.886 and −10.
    assert cells[0].value.point != ( _PRINTED_DFG + Decimal("-10") ) / 2
    ctx = _context(series)
    assert comparison_candidates(ctx) == ()


def test_compilation_series_score_pins_cell_results_and_partial_refusal(monkeypatch) -> None:
    from simulator.battery.compilation_tier import ThermoAttempt, compilation_tier_census
    from simulator.battery.enums import QUANTITY_UNITS, MetricOperation
    from simulator.battery.records import DecisionBand, Execution
    from simulator.battery.score import ENGINE_CHANNELS, score_store

    identity = replace(_na2o_liquid(), temperature_K=State.unknown("series"))
    series = F.observation(
        "cell-pin-series",
        "exp-1",
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    pairs = (
        *((Decimal(1000 + 100 * index), Decimal(-20 + index)) for index in range(10)),
        (Decimal("1900"), Decimal("-11")),
        (Decimal("2000"), Decimal("-10")),
    )
    series = replace(series, value=Value(ValueKind.SERIES, series=pairs))
    origin = "compilations-janaf/janaf-Na.yaml"
    ctx = _context(series, origins={series.observation_id: origin})
    offsets = {
        Decimal(1000 + 100 * index): Decimal(index + 1)
        for index in range(10)
    }

    def predict(engine, cell, **kwargs):
        del kwargs
        temperature = cell.identity.temperature_K.value
        if temperature == Decimal("2000"):
            return EnginePrediction(
                engine,
                ENGINE_CHANNELS[engine],
                Execution(state=ExecutionState.NOT_PROBED),
                coefficient_sources=("janaf-4th",),
                lineage_complete=True,
                refusal_reason=RefusalReason.UNSUPPORTED,
                refusal_detail={"reason": "synthetic-cell-unavailable"},
                identity=cell.identity,
            )
        offset = offsets[temperature]
        return EnginePrediction(
            engine,
            ENGINE_CHANNELS[engine],
            Execution(
                state=ExecutionState.PRODUCED,
                call_evidence=f"synthetic-cell-{temperature}",
            ),
            value=cell.value.point + offset,
            unit=QUANTITY_UNITS[Quantity.DELTA_FG],
            coefficient_sources=("janaf-4th",),
            lineage_complete=True,
            identity=cell.identity,
        )

    residuals, _candidates = score_store(
        ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=predict,
    )
    expected_references = [
        *(f"cell-pin-series#t{1000 + 100 * index}v{-20 + index}" for index in range(10)),
        "cell-pin-series#t1900v-11n1",
        "cell-pin-series#t2000v-10",
    ]
    assert [row.reference for row in residuals] == expected_references
    assert [row.status for row in residuals] == [
        *(ResidualStatus.NO_BAND for _ in range(11)),
        ResidualStatus.REFUSED,
    ]
    for index, row in enumerate(residuals[:-1], start=1):
        assert row.numeric is not None
        assert (
            row.numeric.value,
            row.numeric.unit,
            row.numeric.operation,
            row.numeric.metric_uncertainty,
            row.numeric.verdict,
            row.numeric.decision_band,
        ) == (
            Decimal(index if index <= 10 else 10),
            QUANTITY_UNITS[Quantity.DELTA_FG],
            MetricOperation.ABSOLUTE,
            None,
            None,
            None,
        )
        assert [
            (notice.kind, notice.reason, notice.origin)
            for notice in row.notices
        ] == [
            (
                NoticeKind.DERIVATION_USES_COMPILATION,
                "Do not validate an engine against a compilation it consumes. "
                "same-source compilation=nist-janaf-4th uncertainty=none",
                row.reference,
            ),
            (
                NoticeKind.FALLBACK,
                "derived_band_insufficient_sources",
                "decision-band",
            ),
        ]
    refused = residuals[-1]
    assert refused.numeric is None
    assert refused.refusal is not None
    assert (
        refused.refusal.reason,
        dict(refused.refusal.detail),
        refused.candidate_request.engine,
    ) == (
        RefusalReason.UNSUPPORTED,
        {"reason": "synthetic-cell-unavailable"},
        Engine.INTERNAL_ANALYTICAL,
    )

    def attempt(engine, cell, **kwargs):
        del engine, kwargs
        temperature = cell.identity.temperature_K.value
        if temperature == Decimal("2000"):
            return ThermoAttempt(
                None,
                None,
                Authority.REFUSED,
                (),
                RefusalReason.UNSUPPORTED,
                {"reason": "synthetic-cell-unavailable"},
                "synthetic-refusal",
            )
        offset = offsets[temperature]
        return ThermoAttempt(
            cell.value.point + offset,
            QUANTITY_UNITS[Quantity.DELTA_FG],
            Authority.BRIDGE,
            (),
            None,
            {},
            f"synthetic-{temperature}",
        )

    monkeypatch.setattr(
        "simulator.battery.compilation_tier.predict_thermo_attempt", attempt
    )
    census = compilation_tier_census(
        ctx,
        engines=(Engine.MAGEMIN,),
        invoke_pure_phase=True,
        audit_compile_residual=False,
    )
    assert (
        census["reachable_points"],
        census["series_cells_expanded"],
        census["banded_series_cells_expanded"],
        census["transition_temperature_series_cells_left"],
    ) == (12, 12, 12, 0)
    row = census["rows"][0]
    assert (
        row["reachable"],
        row["engine_values"],
        row["numeric"],
        row["median_abs_residual"],
        row["refused"],
    ) == (
        12,
        11,
        11,
        "6",
        {"unsupported:synthetic-cell-unavailable": 1},
    )


def test_transition_temperature_and_empirical_series_stay_series() -> None:
    identity = _na2o_liquid(Quantity.TRANSITION_TEMPERATURE)
    experiment = F.tabulation_experiment()
    transition = F.observation(
        "transition",
        experiment.experiment_id,
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
    )
    transition = replace(
        transition,
        value=Value(ValueKind.SERIES, series=((Decimal("1405"), Decimal("1405")),)),
    )
    assert tuple(_iter_compilation_series_cells(transition, "compilations-janaf/x.yaml")) == (
        transition,
    )
    empirical = replace(
        transition,
        observation_id="empirical-series",
        evidence=replace(
            transition.evidence, class_=State.of(EvidenceClass.MEASURED_DIRECT)
        ),
        value=Value(ValueKind.SERIES, series=((Decimal("1"), Decimal("2")), (Decimal("3"), Decimal("4")))),
    )
    assert tuple(_iter_compilation_series_cells(empirical, "kems.yaml")) == (empirical,)


def test_ellingham_na2o_matches_printed_janaf_within_fit_bound() -> None:
    identity = _na2o_liquid()
    observation = F.observation("na2o", "exp-1", identity, _PRINTED_DFG)
    attempt = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        observation,
    )
    assert attempt.value is not None
    assert attempt.unit == "kJ_per_declared_mol_basis"
    residual = attempt.value - _PRINTED_DFG
    # 0.442 kJ/mol O2 / n_ox 2 = 0.221 kJ/mol oxide.
    assert abs(residual - Decimal("0.1832")) < Decimal("0.001")
    assert abs(residual) < Decimal("0.221")
    assert attempt.refusal_reason is None

    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, observation)
    assert prediction.value is not None
    assert float(prediction.value).hex() == "-0x1.d9f559b3d0780p+1"
    assert prediction.unit == attempt.unit


def test_ellingham_transforms_formation_values_to_printed_standard_pressure() -> None:
    temperature = Decimal("1200")
    bar_identity = F.cao_identity(
        Phase.CR,
        T_K=temperature,
        metal_phase=Phase.L,
        per=PerBasis.MOL_SPECIES,
        p_std=Decimal("100000"),
    )
    atm_identity = replace(
        bar_identity,
        standard_pressure_Pa=State.of(Decimal("101325")),
    )

    def prediction(identity, observation_id: str, quantity: Quantity) -> Decimal:
        attempt = predict_thermo_attempt(
            Engine.INTERNAL_ANALYTICAL,
            F.observation(
                observation_id,
                "exp-1",
                replace(identity, quantity=quantity),
                Decimal("0"),
            ),
        )
        assert attempt.value is not None
        assert attempt.refusal_reason is None
        return attempt.value

    bar_dfg = prediction(bar_identity, "cao-bar-dfg", Quantity.DELTA_FG)
    atm_dfg = prediction(atm_identity, "cao-atm-dfg", Quantity.DELTA_FG)
    pressure_log_ratio = (Decimal("101325") / Decimal("100000")).ln()
    expected_dfg_shift = standard_pressure_delta_g_kJ_per_mol(
        Fraction(-1, 2), temperature, Decimal("100000"), Decimal("101325")
    )
    assert abs((atm_dfg - bar_dfg) - expected_dfg_shift) < Decimal("1e-12")
    assert Decimal("-0.0657") < expected_dfg_shift < Decimal("-0.0656")

    bar_log_kf = prediction(bar_identity, "cao-bar-log-kf", Quantity.LOG10_KF)
    atm_log_kf = prediction(atm_identity, "cao-atm-log-kf", Quantity.LOG10_KF)
    expected_log_kf_shift = Decimal("0.5") * pressure_log_ratio / Decimal("10").ln()
    assert abs((atm_log_kf - bar_log_kf) - expected_log_kf_shift) < Decimal("1e-12")

    assert isinstance(bar_identity.reaction, State) and bar_identity.reaction.is_value
    reaction = bar_identity.reaction.value
    assert reaction is not None
    scaled_reaction = replace(
        reaction,
        terms=tuple(
            replace(term, coefficient=term.coefficient * 2)
            for term in reaction.terms
        ),
    )
    scaled_identity = replace(atm_identity, reaction=State.of(scaled_reaction))
    scaled_dfg = prediction(scaled_identity, "cao-atm-scaled-reaction", Quantity.DELTA_FG)
    assert scaled_dfg == atm_dfg

    o2_bar_identity = F.cao_identity(
        Phase.CR,
        T_K=temperature,
        metal_phase=Phase.L,
        per=PerBasis.MOL_O2,
        p_std=Decimal("100000"),
    )
    o2_atm_identity = replace(
        o2_bar_identity,
        standard_pressure_Pa=State.of(Decimal("101325")),
    )
    o2_bar_dfg = prediction(o2_bar_identity, "cao-o2-bar-dfg", Quantity.DELTA_FG)
    o2_atm_dfg = prediction(o2_atm_identity, "cao-o2-atm-dfg", Quantity.DELTA_FG)
    expected_o2_shift = standard_pressure_delta_g_kJ_per_mol(
        Fraction(-1), temperature, Decimal("100000"), Decimal("101325")
    )
    assert abs((o2_atm_dfg - o2_bar_dfg) - expected_o2_shift) < Decimal("1e-12")


def test_ellingham_rejects_element_reference_phase_mismatch() -> None:
    condensed = F.cao_identity(
        Phase.CR,
        T_K=Decimal("1800"),
        metal_phase=Phase.CR,
        per=PerBasis.MOL_SPECIES,
        p_std=Decimal("101325"),
    )
    refused = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("cao-condensed-reference", "exp-1", condensed, Decimal("0")),
    )
    assert refused.value is None
    assert refused.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert refused.refusal_detail["reason"] == "identity-mismatch-element-reference-phase"
    assert refused.refusal_detail["element"] == "Ca"
    assert refused.refusal_detail["cell_phase"] == Phase.CR.value
    assert refused.refusal_detail["segment_phase"] == Phase.G.value

    gas_bar = F.cao_identity(
        Phase.CR,
        T_K=Decimal("1800"),
        metal_phase=Phase.G,
        per=PerBasis.MOL_SPECIES,
        p_std=Decimal("100000"),
    )
    gas_atm = replace(gas_bar, standard_pressure_Pa=State.of(Decimal("101325")))
    bar_attempt = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("cao-gas-bar", "exp-1", gas_bar, Decimal("0")),
    )
    atm_attempt = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("cao-gas-atm", "exp-1", gas_atm, Decimal("0")),
    )
    assert bar_attempt.value is not None
    assert atm_attempt.value is not None
    expected_shift = standard_pressure_delta_g_kJ_per_mol(
        Fraction(-3, 2), Decimal("1800"), Decimal("100000"), Decimal("101325")
    )
    assert abs((atm_attempt.value - bar_attempt.value) - expected_shift) < Decimal("1e-12")


def test_ellingham_pressure_transform_refuses_unknown_reaction_or_phase() -> None:
    identity = F.cao_identity(
        Phase.CR,
        T_K=Decimal("1200"),
        metal_phase=Phase.CR,
        per=PerBasis.MOL_SPECIES,
        p_std=Decimal("101325"),
    )
    no_reaction = replace(identity, reaction=State.unknown("not printed"))
    refused_reaction = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("cao-no-reaction", "exp-1", no_reaction, Decimal("0")),
    )
    assert refused_reaction.value is None
    assert refused_reaction.refusal_reason is RefusalReason.IDENTITY_UNKNOWN
    assert refused_reaction.refusal_detail["reason"] == (
        "standard-pressure-transform-reaction-unknown"
    )

    assert isinstance(identity.reaction, State) and identity.reaction.is_value
    reaction = identity.reaction.value
    assert reaction is not None
    unknown_phase_term = replace(
        reaction.terms[0],
        species=replace(reaction.terms[0].species, phase=State.unknown("not printed")),
    )
    unknown_phase_reaction = replace(
        reaction,
        terms=(unknown_phase_term, *reaction.terms[1:]),
    )
    no_phase = replace(identity, reaction=State.of(unknown_phase_reaction))
    refused_phase = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("cao-no-phase", "exp-1", no_phase, Decimal("0")),
    )
    assert refused_phase.value is None
    assert refused_phase.refusal_reason is RefusalReason.IDENTITY_UNKNOWN
    assert refused_phase.refusal_detail["reason"] == (
        "standard-pressure-transform-phase-unknown"
    )


def test_log10_kf_follows_delta_fg_and_has_no_kj_band() -> None:
    identity = _na2o_liquid(Quantity.LOG10_KF)
    attempt = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("na2o-k", "exp-1", identity, _PRINTED_LOGK),
    )
    assert attempt.value is not None
    gibbs = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("na2o-g", "exp-1", _na2o_liquid(), _PRINTED_DFG),
    )
    assert attempt.value == log10K_from_delta_fG_kJ_mol(gibbs.value, _T)
    assert abs(attempt.value - _PRINTED_LOGK) < Decimal("0.01")
    assert decision_band_for(Quantity.LOG10_KF, SourceRelation.INDEPENDENT) is None
    assert decision_band_for(Quantity.DELTA_FG, SourceRelation.INDEPENDENT) is None
    for quantity in (
        Quantity.CP,
        Quantity.S,
        Quantity.DELTA_FH,
        Quantity.H_MINUS_H298,
    ):
        assert decision_band_for(quantity, SourceRelation.INDEPENDENT) is None
        assert decision_band_for(quantity, SourceRelation.SAME_INPUT) is None


def test_engine_prediction_mutation_cannot_create_a_source_band(
    monkeypatch,
) -> None:
    from simulator.battery import score as score_module
    from simulator.battery.records import DecisionBand, Derivation, Execution, Locator
    from simulator.battery.score import EnginePrediction, score_store
    from simulator.battery.validity import GateOutcome

    monkeypatch.setattr(
        score_module, "run_validity_gates", lambda *args, **kwargs: GateOutcome(True)
    )

    experiment = F.tabulation_experiment()
    identity = replace(
        _na2o_liquid(Quantity.CP),
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )
    offsets = ("-2", "-1", "0", "1", "10", "-2", "-1", "0", "1", "10", "0.4")
    observations = []
    origins = {}
    for index, offset in enumerate(offsets):
        observation = F.observation(
            f"cp-cell-{index}",
            experiment.experiment_id,
            identity,
            Decimal(10 + index),
            evidence=EvidenceClass.COMPILATION_ASSESSED,
            source_id="janaf-4th",
        )
        if index == 10:
            observation = replace(
                observation,
                uncertainty=Uncertainty(
                    kind=UncertaintyKind.PRINTED, verbatim="0.1"
                ),
                locator=Locator(
                    record="printed cp cell", note="unit='J/mol·K'"
                ),
                derivation=Derivation(
                    relation="source unit normalization",
                    inputs=(observation.observation_id,),
                    parameters=(),
                    output_unit="J_per_declared_mol_basis_per_K",
                ),
            )
        observations.append(observation)
        origins[observation.observation_id] = "compilations-usgs-b1452/grid.yaml"
    ctx = _context(*observations, origins=origins)
    offset_by_id = {
        observation.observation_id: Decimal(offset)
        for observation, offset in zip(observations, offsets)
    }

    def predict(engine, observation, **kwargs):
        del kwargs
        scale = Decimal("10") if engine is Engine.VAPOROCK else Decimal("1")
        return EnginePrediction(
            engine=engine,
            channel="internal-analytical",
            execution=Execution(ExecutionState.PRODUCED, "test prediction"),
            value=observation.value.point + offset_by_id[observation.observation_id] * scale,
            unit="J_per_declared_mol_basis_per_K",
            authority=Authority.CERTIFIED,
            coefficient_sources=("unmapped-test-source",),
            lineage_complete=False,
            identity=observation.identity,
        )

    residuals, _ = score_store(
        ctx,
        engines=(Engine.INTERNAL_ANALYTICAL, Engine.VAPOROCK),
        predict=predict,
    )
    by_reference = {
        (residual.reference, residual.key.rsplit("::", 1)[-1]): residual
        for residual in residuals
    }
    for observation, offset in zip(observations[:10], offsets[:10]):
        residual = by_reference[(observation.observation_id, Engine.INTERNAL_ANALYTICAL.value)]
        assert residual.source_relation is SourceRelation.UNKNOWN
        assert residual.numeric is not None
        assert residual.numeric.decision_band is None
        assert residual.status is ResidualStatus.NO_BAND
        vaporock_residual = by_reference[(observation.observation_id, Engine.VAPOROCK.value)]
        assert vaporock_residual.numeric is not None
        assert vaporock_residual.numeric.decision_band is None
        assert vaporock_residual.status is ResidualStatus.NO_BAND
    printed = by_reference[(observations[10].observation_id, Engine.INTERNAL_ANALYTICAL.value)]
    assert printed.source_relation is SourceRelation.UNKNOWN
    assert printed.numeric is not None
    assert printed.numeric.decision_band is not None
    assert printed.numeric.decision_band.rule.startswith("printed_unstated:")
    assert printed.numeric.decision_band.value == Decimal("0.1")
    assert printed.status is ResidualStatus.MISMATCH
    vaporock = by_reference[(observations[0].observation_id, Engine.VAPOROCK.value)]
    assert vaporock.numeric is not None
    assert vaporock.numeric.decision_band is None
    small_ctx = _context(
        *observations[:9],
        origins={item.observation_id: origins[item.observation_id] for item in observations[:9]},
    )
    small, _ = score_store(
        small_ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=predict,
    )
    assert len(small) == 9
    assert all(item.status is ResidualStatus.NO_BAND for item in small)
    assert all(item.numeric is not None and item.numeric.decision_band is None for item in small)


def test_log10_k_star_stays_typed_outside_formation_ellingham() -> None:
    attempt = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation(
            "na2o-k-star",
            "exp-1",
            _na2o_liquid(Quantity.LOG10_K_STAR),
            Decimal("-4.25"),
        ),
    )
    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.UNSUPPORTED
    assert attempt.refusal_detail["reason"] == "ellingham-emits-reaction-dg-only"


def test_zero_kelvin_row_does_not_blank_later_series_points() -> None:
    from simulator.battery.compilation_tier import compilation_tier_census

    identity = replace(_na2o_liquid(), temperature_K=State.unknown("series"))
    experiment = F.tabulation_experiment()
    series = F.observation(
        "na2o-series",
        experiment.experiment_id,
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    series = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=((Decimal("0"), Decimal("0")), (_T, _PRINTED_DFG)),
        ),
    )
    out = compilation_tier_census(
        _context(series),
        engines=(Engine.VAPOROCK,),
        audit_compile_residual=False,
    )
    row = out["rows"][0]
    assert row["reachable"] == 2
    assert row["refused"]["identity_unknown:temperature-not-positive"] == 1
    assert row["refused"]["unsupported:engine-thermo-does-not-emit"] == 1


def test_compilation_census_counts_measured_candidates_without_a_rail() -> None:
    from simulator.battery.compilation_tier import compilation_tier_census

    identity = _na2o_liquid(Quantity.VISCOSITY)
    observation = F.observation(
        "viscosity-no-rail",
        "exp-1",
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    result = compilation_tier_census(
        _context(observation),
        engines=(Engine.MAGEMIN,),
        audit_compile_residual=False,
    )

    assert result["comparison_candidates"] == 1
    assert result["measured_candidates_by_rail"] == {"none": 1}

def test_census_keeps_unknown_relation_decision_when_band_exists(monkeypatch) -> None:
    from simulator.battery import score as score_module
    from simulator.battery.compilation_tier import compilation_tier_census
    from simulator.battery.records import Derivation, Execution, Locator

    experiment = F.tabulation_experiment()
    identity = replace(
        _na2o_liquid(Quantity.CP),
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )
    observation = F.observation(
        "unknown-census-cp",
        experiment.experiment_id,
        identity,
        Decimal("10"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="janaf-4th",
    )
    observation = replace(
        observation,
        uncertainty=Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="0.1"),
        locator=Locator(record="printed cp cell", note="unit='J/mol·K'"),
        derivation=Derivation(
            relation="source unit normalization",
            inputs=(observation.observation_id,),
            parameters=(),
            output_unit="J_per_declared_mol_basis_per_K",
        ),
    )
    monkeypatch.setattr(
        score_module,
        "resolve_source_relation",
        lambda *args, **kwargs: SourceRelation.UNKNOWN,
    )
    def predict(engine, obs, **kwargs):
        del kwargs
        return EnginePrediction(
            engine=engine,
            channel=score_module.ENGINE_CHANNELS[engine],
            execution=Execution(ExecutionState.PRODUCED, "test"),
            value=obs.value.point + Decimal("1"),
            unit="J_per_declared_mol_basis_per_K",
            authority=Authority.CERTIFIED,
            coefficient_sources=("unmapped-test-source",),
            lineage_complete=False,
            identity=obs.identity,
        )

    monkeypatch.setattr(score_module, "predict_with_engine", predict)
    out = compilation_tier_census(
        _context(observation),
        engines=(Engine.INTERNAL_ANALYTICAL,),
        audit_compile_residual=False,
    )
    stratum = out["rows"][0]["decision_strata"][0]
    assert stratum["relation"] == "unknown"
    assert stratum["band_kind"] == "printed_unstated"
    assert stratum["mismatch_count"] == 1
    unprinted = replace(
        observation,
        uncertainty=Uncertainty(kind=UncertaintyKind.NONE),
        locator=None,
        derivation=None,
    )
    no_band = compilation_tier_census(
        _context(unprinted),
        engines=(Engine.INTERNAL_ANALYTICAL,),
        audit_compile_residual=False,
    )["rows"][0]["decision_strata"][0]
    assert no_band["band_kind"] == "no_band"
    assert no_band["no_band_reason"] == "derived_band_insufficient_sources"
    assert no_band["band_derived_n"] is None
    assert no_band["n"] == 1
    assert no_band["signed_median_residual"] == "1"
    assert no_band["mad"] == "0"
    assert no_band["bias_to_scatter_ratio"] is None
    assert no_band["max_abs_residual"] == "1"
    assert no_band["tail_in_count"] is None


def test_census_strata_use_scorer_pool_for_refusals_and_observation_flags(monkeypatch) -> None:
    from simulator.battery import score as score_module
    from simulator.battery.compilation_tier import ThermoAttempt, compilation_tier_census
    from simulator.battery.records import Execution, Notice
    from simulator.battery.score import score_store
    from simulator.battery.validity import GateOutcome

    monkeypatch.setattr(score_module, "run_validity_gates", lambda *args, **kwargs: GateOutcome(True))
    monkeypatch.setattr(
        "simulator.battery.validity.run_validity_gates",
        lambda *args, **kwargs: GateOutcome(True),
    )
    monkeypatch.setattr(score_module, "resolve_source_relation", lambda *args, **kwargs: SourceRelation.UNKNOWN)
    experiment = F.tabulation_experiment()
    identity = replace(
        _na2o_liquid(Quantity.CP),
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )
    offsets = [Decimal(value) for value in range(10)] + [Decimal("100"), Decimal("100")]
    observations = []
    for index, offset in enumerate(offsets):
        observation = F.observation(
            f"shared-pool-{index}",
            experiment.experiment_id,
            identity,
            Decimal(10 + index),
            evidence=EvidenceClass.COMPILATION_ASSESSED,
            source_id="janaf-4th",
        )
        if index == 11:
            observation = replace(
                observation,
                notices=(
                    Notice(
                        kind=NoticeKind.UNVERIFIED_APPARATUS,
                        affected_quantities=(Quantity.CP,),
                        reason="apparatus_unverified:test",
                        origin=observation.observation_id,
                    ),
                ),
            )
        observations.append(observation)
    context = _context(*observations)
    offset_by_id = {
        observation.observation_id: offset
        for observation, offset in zip(observations, offsets)
    }

    def predict(engine, observation, **kwargs):
        del kwargs
        candidate_identity = observation.identity
        if observation.observation_id == observations[10].observation_id:
            candidate_identity = replace(
                candidate_identity,
                species=Species("MgO", Phase.L),
            )
        return EnginePrediction(
            engine=engine,
            channel=score_module.ENGINE_CHANNELS[engine],
            execution=Execution(ExecutionState.PRODUCED, "test prediction"),
            value=observation.value.point + offset_by_id[observation.observation_id],
            unit="J_per_declared_mol_basis_per_K",
            authority=Authority.CERTIFIED,
            coefficient_sources=("unmapped-test-source",),
            lineage_complete=False,
            identity=candidate_identity,
        )

    monkeypatch.setattr(score_module, "predict_with_engine", predict)
    monkeypatch.setattr(
        "simulator.battery.compilation_tier.predict_thermo_attempt",
        lambda engine, obs, **kwargs: ThermoAttempt(
            value=obs.value.point + offset_by_id[obs.observation_id],
            unit="J_per_declared_mol_basis_per_K",
            authority=Authority.CERTIFIED,
            notices=(),
            refusal_reason=None,
            refusal_detail={},
            call_evidence="test",
        ),
    )
    residuals, _ = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=predict,
    )
    identity_refusal = next(
        residual for residual in residuals if residual.reference == observations[10].observation_id
    )
    flagged = next(
        residual for residual in residuals if residual.reference == observations[11].observation_id
    )
    assert identity_refusal.status is ResidualStatus.REFUSED
    assert flagged.status is ResidualStatus.NO_BAND
    assert any(notice.kind is NoticeKind.UNVERIFIED_APPARATUS for notice in flagged.notices)
    scorer_stratum = score_module._compilation_decision_strata(residuals, context)[0]

    census = compilation_tier_census(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        audit_compile_residual=False,
    )
    census_stratum = census["rows"][0]["decision_strata"][0]
    for key in (
        "n",
        "signed_median_residual",
        "band_value",
        "bias_to_scatter_ratio",
        "match_count",
    ):
        assert census_stratum[key] == scorer_stratum[key]
    assert census_stratum["n"] == 10
    assert census_stratum["signed_median_residual"] == "4.5"
    assert census_stratum["bias_to_scatter_ratio"] == "1.8"
    assert census_stratum["band_value"] == []
    assert census_stratum["band_kind"] == "no_band"
    assert census_stratum["band_derived_n"] is None
    assert census_stratum["match_count"] == 0
    assert census_stratum["no_band_count"] == 10


def test_census_decision_counters_partition_same_source_no_band_rows(monkeypatch) -> None:
    from simulator.battery import score as score_module
    from simulator.battery.compilation_tier import compilation_tier_census
    from simulator.battery.records import Execution
    from simulator.battery.validity import GateOutcome

    monkeypatch.setattr(score_module, "run_validity_gates", lambda *args, **kwargs: GateOutcome(True))
    monkeypatch.setattr(score_module, "resolve_source_relation", lambda *args, **kwargs: SourceRelation.SAME_INPUT)
    experiment = F.tabulation_experiment()
    identity = replace(
        _na2o_liquid(Quantity.CP),
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )
    observation = F.observation(
        "same-source-no-band",
        experiment.experiment_id,
        identity,
        Decimal("10"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="janaf-4th",
    )

    def predict(engine, reference, **kwargs):
        del kwargs
        return EnginePrediction(
            engine=engine,
            channel=score_module.ENGINE_CHANNELS[engine],
            execution=Execution(ExecutionState.PRODUCED, "test prediction"),
            value=reference.value.point + Decimal("1"),
            unit="J_per_declared_mol_basis_per_K",
            authority=Authority.CERTIFIED,
            coefficient_sources=("unmapped-test-source",),
            lineage_complete=False,
            identity=reference.identity,
        )

    monkeypatch.setattr(score_module, "predict_with_engine", predict)
    row = compilation_tier_census(
        _context(observation),
        engines=(Engine.INTERNAL_ANALYTICAL,),
        audit_compile_residual=False,
    )["rows"][0]
    assert row["numeric"] == 1
    assert row["no_band"] == 1
    assert row["same_source"] == 0
    assert row["independent"] == 0
    assert row["unknown"] == 0
    assert row["no_band"] + row["same_source"] + row["independent"] + row["unknown"] == row["numeric"]
    stratum = row["decision_strata"][0]
    assert stratum["n"] == 1
    assert stratum["no_band_count"] + stratum["match_count"] + stratum["mismatch_count"] == stratum["n"]


def test_headline_decision_strata_are_scoped_to_each_rail() -> None:
    from simulator.battery.enums import MetricOperation, Rail
    from simulator.battery.records import DecisionBand, ResidualNumeric
    from simulator.battery.score import headline_payload_records, residual_to_plain

    experiment = F.tabulation_experiment()
    cp = replace(
        _na2o_liquid(Quantity.CP),
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )
    cp_observation = F.observation(
        "headline-cp-compilation",
        experiment.experiment_id,
        cp,
        Decimal("100"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="janaf-4th",
    )
    psat_observation = F.observation(
        "headline-psat-compilation",
        experiment.experiment_id,
        F.psat_identity("Na"),
        Decimal("101325"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="janaf-4th",
    )
    context = _context(cp_observation, psat_observation)
    cp_residual = F.residual(
        "cp::thermochemistry::internal-analytical",
        cp_observation.observation_id,
        candidate="engine-cp",
        status=ResidualStatus.MATCH,
        rail=Rail.THERMOCHEMISTRY,
        quantity=Quantity.CP,
        numeric=ResidualNumeric(
            operation=MetricOperation.ABSOLUTE,
            unit="J_per_declared_mol_basis_per_K",
            value=Decimal("1"),
            decision_band=DecisionBand(
                Decimal("2"),
                "J_per_declared_mol_basis_per_K",
                "derived_scatter: source-only sigma=1.4826×MAD; n_sources=10; mad=1",
            ),
        ),
    )
    psat_residual = F.residual(
        "psat::vapour::internal-analytical",
        psat_observation.observation_id,
        candidate="engine-psat",
        status=ResidualStatus.MATCH,
        rail=Rail.VAPOUR,
        quantity=Quantity.P_SAT,
        numeric=ResidualNumeric(
            operation=MetricOperation.ABSOLUTE,
            unit="Pa",
            value=Decimal("1"),
            decision_band=DecisionBand(
                Decimal("2"), "Pa", "derived_scatter: source-only sigma=1.4826×MAD; n_sources=10; mad=1"
            ),
        ),
    )
    residuals = (cp_residual, psat_residual)
    headlines = headline_rows(
        residuals,
        context=context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        tier="compilation",
    )
    by_rail = {
        row["rail"]: {stratum["quantity"] for stratum in row["decision_strata"]}
        for row in headlines
        if row["engine"] == Engine.INTERNAL_ANALYTICAL.value
    }
    assert by_rail[Rail.THERMOCHEMISTRY.value] == {Quantity.CP.value}
    assert by_rail[Rail.VAPOUR.value] == {Quantity.P_SAT.value}

    payload_rows = [residual_to_plain(residual) for residual in residuals]
    payload_headlines = headline_payload_records(
        payload_rows,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        observations=context.observations,
        origins=context.origins,
    )
    payload_by_rail = {
        row["rail"]: {stratum["quantity"] for stratum in row["decision_strata"]}
        for row in payload_headlines
        if row["engine"] == Engine.INTERNAL_ANALYTICAL.value
        and row["tier"] == "compilation"
    }
    assert payload_by_rail[Rail.THERMOCHEMISTRY.value] == {Quantity.CP.value}
    assert payload_by_rail[Rail.VAPOUR.value] == {Quantity.P_SAT.value}


def test_non_positive_temperature_is_a_refusal_not_an_exception() -> None:
    identity = replace(_na2o_liquid(), temperature_K=State.of(Decimal("0")))
    attempt = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("na2o-0", "exp-1", identity, _PRINTED_DFG),
    )
    assert attempt.value is None
    assert attempt.refusal_detail["reason"] == "temperature-not-positive"


def test_ellingham_does_not_emit_zero_for_cp_or_uncovered_formula() -> None:
    cp = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("na2o-cp", "exp-1", _na2o_liquid(Quantity.CP), Decimal("104.6")),
    )
    assert cp.value is None
    assert cp.refusal_detail["reason"] == "ellingham-emits-reaction-dg-only"
    argon = replace(
        _na2o_liquid(),
        species=Species("Ar", Phase.G),
    )
    missing = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("ar", "exp-1", argon, Decimal("0")),
    )
    assert missing.value is None
    assert missing.value != Decimal("0")
    assert missing.refusal_detail["reason"] == "engine-thermo-does-not-emit"


def test_melt_engines_refuse_apparent_gibbs_as_delta_fg() -> None:
    attempt = predict_thermo_attempt(
        Engine.MAGEMIN,
        F.observation("na2o", "exp-1", _na2o_liquid(), _PRINTED_DFG),
    )
    assert attempt.value is None
    assert attempt.refusal_detail["reason"] == "apparent-gibbs-is-not-delta-fG"
    vaporock = predict_thermo_attempt(
        Engine.VAPOROCK,
        F.observation("na2o", "exp-1", _na2o_liquid(), _PRINTED_DFG),
    )
    assert vaporock.value is None
    assert vaporock.refusal_detail["reason"] == "engine-thermo-does-not-emit"


def test_pure_phase_enthalpy_increment_uses_injected_accessor() -> None:
    identity = F.oxide_identity(
        "MgO",
        Phase.CR,
        T_K=Decimal("1000"),
        per=PerBasis.MOL_SPECIES,
        metal_formula="Mg",
        polymorph=Polymorph.PERICLASE.value,
    )
    identity = replace(
        identity,
        quantity=Quantity.H_MINUS_H298,
        subtype=State.of("H(T)-H(298.15 K)"),
    )

    class _Props:
        def __init__(self, enthalpy):
            self.formula = "MgO"
            self.H_J_mol = enthalpy
            self.Cp_J_K_mol = 40.0
            self.S_J_K_mol = 30.0
            self.absences = ()

    calls = []

    def pure_phase(_engine, _symbol, temperature_K, _pressure_bar):
        calls.append(temperature_K)
        if abs(temperature_K - 298.15) < 1e-6:
            return _Props(1000.0)
        return _Props(5000.0)

    attempt = predict_thermo_attempt(
        Engine.MAGEMIN,
        F.observation("mgo", "exp-1", identity, Decimal("4")),
        pure_phase=pure_phase,
    )
    assert attempt.value == Decimal("4")
    assert attempt.unit == "kJ_per_declared_mol_basis"
    assert attempt.refusal_reason is None
    assert calls == [1000.0, 298.15]

    calls.clear()
    refused = predict_thermo_attempt(
        Engine.MAGEMIN,
        F.observation("mgo", "exp-1", identity, Decimal("4")),
        invoke_pure_phase=False,
        pure_phase=pure_phase,
    )
    assert refused.refusal_detail["reason"] == "pure-phase-call-required"
    assert calls == []


def test_thermoengine_import_failure_is_typed_in_score_and_census(monkeypatch) -> None:
    from simulator.battery import compilation_tier

    identity = _pure_phase_identity()
    observation = F.observation(
        "mgo-cp-import-failure",
        "exp-1",
        identity,
        Decimal("40"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )

    def fail_thermoengine_import(*_args, **_kwargs):
        raise ImportError("No module named 'thermoengine'")

    monkeypatch.setattr(
        compilation_tier, "default_pure_phase", fail_thermoengine_import
    )
    residual, _ = compile_residual(
        observation,
        Engine.THERMOENGINE,
        context=_context(observation),
        comparison_ids=set(),
    )
    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.ATTEMPTED_UNAVAILABLE
    assert residual.refusal.detail["reason"] == "pure-phase-unavailable"

    census = compilation_tier.compilation_tier_census(
        _context(observation),
        engines=(Engine.THERMOENGINE,),
        invoke_pure_phase=True,
        audit_compile_residual=False,
    )
    row = next(row for row in census["rows"] if row["quantity"] == Quantity.CP.value)
    assert row["numeric"] == 0
    assert row["refused"]["attempted_unavailable:pure-phase-unavailable"] == 1


def test_compilation_census_calls_only_resolved_pure_phases(monkeypatch) -> None:
    from simulator.battery.compilation_tier import compilation_tier_census

    identity = _pure_phase_identity()
    crystal = F.observation(
        "mgo-cp",
        "exp-1",
        identity,
        Decimal("40"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    liquid = F.observation(
        "na2o-cp",
        "exp-1",
        _pure_standard_identity(_na2o_liquid(Quantity.CP), Quantity.CP),
        Decimal("80"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    calls = []

    class _Props:
        formula = "MgO"
        Cp_J_K_mol = 40.0
        H_J_mol = 0.0
        S_J_K_mol = 30.0
        absences = ()

    def pure_phase(engine, symbol, temperature_K, pressure_bar):
        calls.append((engine, symbol, temperature_K, pressure_bar))
        return _Props()

    monkeypatch.setattr(
        "simulator.battery.compilation_tier.default_pure_phase", pure_phase
    )
    result = compilation_tier_census(
        _context(crystal, liquid),
        engines=(Engine.MAGEMIN,),
        invoke_pure_phase=True,
        audit_compile_residual=False,
    )

    assert len(calls) == 1
    assert calls[0][0] is Engine.MAGEMIN
    row = next(row for row in result["rows"] if row["quantity"] == Quantity.CP.value)
    assert row["numeric"] == 1
    assert row["refused"]["unsupported:pure-phase-is-crystal-only"] == 1


def test_compilation_census_does_not_call_pure_phase_by_default(monkeypatch) -> None:
    from simulator.battery import compilation_tier

    observation = F.observation(
        "mgo-cp-default-census",
        "exp-1",
        _pure_phase_identity(),
        Decimal("40"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    calls = []

    class _Props:
        formula = "MgO"
        Cp_J_K_mol = 40.0
        H_J_mol = 0.0
        S_J_K_mol = 30.0
        absences = ()

    def pure_phase(engine, symbol, temperature_K, pressure_bar):
        calls.append((engine, symbol, temperature_K, pressure_bar))
        return _Props()

    monkeypatch.setattr(compilation_tier, "default_pure_phase", pure_phase)
    result = compilation_tier.compilation_tier_census(
        _context(observation),
        engines=(Engine.MAGEMIN,),
        audit_compile_residual=False,
    )
    row = next(row for row in result["rows"] if row["quantity"] == Quantity.CP.value)

    assert calls == []
    assert row["numeric"] == 0
    assert row["refused"]["not_probed:pure-phase-call-required"] == 1


def test_compilation_census_requires_identity_equal_before_pure_phase(monkeypatch) -> None:
    from simulator.battery import compilation_tier
    from simulator.battery.enums import IdentityEqualKind
    from simulator.battery.identity import identity_equal

    identity = _pure_phase_identity(polymorph=None)
    assert identity_equal(identity, identity).kind is IdentityEqualKind.IDENTITY_UNKNOWN
    observation = F.observation(
        "mgo-cp-unknown-polymorph",
        "exp-1",
        identity,
        Decimal("40"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    calls = []

    class _Props:
        formula = "MgO"
        Cp_J_K_mol = 40.0
        H_J_mol = 0.0
        S_J_K_mol = 30.0
        absences = ()

    def pure_phase(engine, symbol, temperature_K, pressure_bar):
        calls.append((engine, symbol, temperature_K, pressure_bar))
        return _Props()

    monkeypatch.setattr(compilation_tier, "default_pure_phase", pure_phase)
    for engine in (Engine.THERMOENGINE, Engine.MAGEMIN):
        assert compilation_tier._resolve_symbol(engine, identity)[0] is not None
        result = compilation_tier.compilation_tier_census(
            _context(observation),
            engines=(engine,),
            invoke_pure_phase=True,
            audit_compile_residual=False,
        )
        row = next(
            row for row in result["rows"] if row["quantity"] == Quantity.CP.value
        )
        assert row["numeric"] == 0
        assert row["refused"]["identity_unknown:identity_equal"] == 1
    assert calls == []


def test_vaporock_gas_shomate_values_are_janaf_fidelity_checks() -> None:
    import hashlib
    from pathlib import Path

    import pytest

    pytest.importorskip("vaporock.equil")

    expected = {
        Quantity.CP: (Decimal("20.787"), Decimal("0.01")),
        Quantity.S: (Decimal("189.284"), Decimal("0.3")),
        Quantity.H_MINUS_H298: (Decimal("18.746"), Decimal("0.001")),
    }
    attempts = {}
    for quantity, (printed, tolerance) in expected.items():
        attempt = predict_thermo_attempt(
            Engine.VAPOROCK,
            _same_table_gas(
                f"kgas-{quantity.value}",
                "K",
                quantity,
                Decimal("1200"),
                printed,
            ),
        )
        assert attempt.refusal_reason is None
        assert attempt.value is not None
        assert attempt.coefficient_sources is None
        assert abs(attempt.value - printed) < tolerance
        assert "implementation-fidelity" in attempt.call_evidence
        attempts[quantity] = attempt

    evidence = attempts[Quantity.CP].call_evidence
    fields = dict(
        item.split("=", 1)
        for item in evidence.split(":")
        if "=" in item
    )
    table_path = Path(fields["janaf_csv_path"])
    assert table_path.is_file()
    table_sha256 = hashlib.sha256(table_path.read_bytes()).hexdigest()
    assert fields["janaf_csv_sha256"] == table_sha256
    assert len(fields["vaporock_git_sha"]) == 40
    assert fields["vaporock_git_dirty"] in {"true", "false"}
    assert all(
        all(f"{name}=" in attempt.call_evidence for name in fields)
        for attempt in attempts.values()
    )

    assert attempts[Quantity.CP].unit == "J_per_declared_mol_basis_per_K"
    assert attempts[Quantity.S].unit == "J_per_declared_mol_basis_per_K"
    assert attempts[Quantity.H_MINUS_H298].unit == "kJ_per_declared_mol_basis"

    reference = _same_table_gas(
        "kgas-cp-janaf",
        "K",
        Quantity.CP,
        Decimal("1200"),
        Decimal("20.787"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    residual, candidate = compile_residual(
        reference,
        Engine.VAPOROCK,
        context=_context(reference),
        comparison_ids=set(),
    )
    assert candidate is not None
    assert residual.source_relation is SourceRelation.SAME_INPUT
    from simulator.battery.compilation_tier import _prediction_from_attempt

    tier_prediction = _prediction_from_attempt(
        Engine.VAPOROCK, reference, attempts[Quantity.CP]
    )
    tier_residual, tier_candidate = compile_residual(
        reference,
        Engine.VAPOROCK,
        context=_context(reference),
        comparison_ids=set(),
        predict=lambda _engine, _observation, **_kwargs: tier_prediction,
    )
    assert tier_candidate is not None
    assert candidate.engine.coefficient_sources == tier_candidate.engine.coefficient_sources
    assert tier_candidate.engine.coefficient_sources == ("janaf-4th",)
    assert tier_residual.source_relation is residual.source_relation
    assert tier_residual.notices == residual.notices
    assert all(
        f"{name}=" in residual.execution.call_evidence for name in fields
    )
    assert any(
        notice.kind is NoticeKind.DERIVATION_USES_COMPILATION
        for notice in residual.notices
    )
    assert "implementation-fidelity" in residual.execution.call_evidence
    assert decision_band_for(Quantity.CP, SourceRelation.SAME_INPUT) is None


# Exact outputs of the base in-band resolver. K at 1800 K is the join:
# T == T_max of the lower segment, so that segment owns it. K at 6000 K
# and Fe at 4000 K are the upper segment's certified interior or end.
_VAPOROCK_IN_BAND_PIN = (
    (
        "K",
        Decimal("1200"),
        {
            Quantity.CP: Decimal("20.78466702488889"),
            Quantity.S: Decimal("189.01928920321794"),
            Quantity.H_MINUS_H298: Decimal("18.746192578133332"),
        },
    ),
    (
        "K",
        Decimal("1800"),
        {
            Quantity.CP: Decimal("20.864585592395063"),
            Quantity.S: Decimal("197.45608706041142"),
            Quantity.H_MINUS_H298: Decimal("31.232321333688887"),
        },
    ),
    (
        "K",
        Decimal("6000"),
        {
            Quantity.CP: Decimal("45.086233055555525"),
            Quantity.S: Decimal("230.09770134488363"),
            Quantity.H_MINUS_H298: Decimal("155.28008366666666"),
        },
    ),
    (
        "Fe",
        Decimal("4000"),
        {
            Quantity.CP: Decimal("29.672713874999996"),
            Quantity.S: Decimal("243.52268739094387"),
            Quantity.H_MINUS_H298: Decimal("91.8760058333333"),
        },
    ),
)


def test_vaporock_gas_in_band_outputs_are_pinned() -> None:
    """In-band Shomate Cp, S, and H−H298 stay on these exact values.

    The numbers are the base resolver's outputs for the containing
    JANAF row, not a re-fit. These rows verify as subtract-H, so the
    enthalpy convention leaves them bit-identical. The locator is that
    species' gas table; the basis gate runs before the in-band return.
    A later out-of-band change must leave them bit-identical, still
    BRIDGE, and still without a notice.
    """

    import pytest

    pytest.importorskip("vaporock.equil")

    for formula, temperature, expected in _VAPOROCK_IN_BAND_PIN:
        for quantity, pinned in expected.items():
            attempt = predict_thermo_attempt(
                Engine.VAPOROCK,
                _same_table_gas(
                    f"pin-{formula}-{quantity.value}-{temperature}",
                    formula,
                    quantity,
                    temperature,
                    pinned,
                ),
            )
            assert attempt.refusal_reason is None
            assert attempt.value == pinned
            assert attempt.authority is Authority.BRIDGE
            assert attempt.notices == ()


def _printed_janaf_node(table_id: str, temperature: Decimal, column: str) -> Decimal:
    from simulator.reference_data.janaf import TABLES_DIR, load_table_document

    document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
    for row in document["table"]["values"]:
        if Decimal(str(row["temperature"]["as_published"])) == temperature:
            return Decimal(str(row[column]["as_published"]))
    raise AssertionError(f"{table_id} has no {column} node at {temperature} K")


def _printed_janaf_bracket(
    table_id: str, temperature: Decimal, column: str
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    from simulator.reference_data.janaf import TABLES_DIR, load_table_document

    document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
    points = []
    for row in document["table"]["values"]:
        cell = row[column]
        token = cell.get("as_published")
        if token in (None, ""):
            continue
        points.append(
            (
                Decimal(str(row["temperature"]["as_published"])),
                Decimal(str(token)),
            )
        )
    points.sort()
    for (left_T, left_y), (right_T, right_y) in zip(points, points[1:]):
        if left_T <= temperature <= right_T:
            return left_T, left_y, right_T, right_y
    raise AssertionError(f"{table_id} {column} does not bracket {temperature} K")


def test_janaf_tabulated_gas_thermo_matches_printed_nodes() -> None:
    """Tabulated nodes are exact. An interior point is the printed chord.

    Cr, CaO, Na2, and MgO are the rows whose Shomate continuation is
    physically invalid. 1050 K sits between CaO's printed 1000 K and
    1100 K nodes. 6500 K is past every gas table's last node. O2's
    neutral table is the reference-phase gas O-029, not a phase-g row.
    Carbon's reference table stays crystal, so the gas id is C-003.
    """

    import pytest

    from simulator.battery.generators.janaf import (
        _janaf_gas_table_ids,
        janaf_tabulated_gas_thermo,
    )

    nodes = (
        ("Cr", Decimal("500"), "Cr-005"),
        ("CaO", Decimal("1000"), "Ca-030"),
        ("Na2", Decimal("500"), "Na-011"),
        ("MgO", Decimal("100"), "Mg-011"),
    )
    for formula, temperature, table_id in nodes:
        sample = janaf_tabulated_gas_thermo(formula, temperature)
        assert sample.table_id == table_id
        assert sample.bracket_width_K == 0
        assert sample.low_K == 0
        assert sample.high_K == Decimal("6000")
        assert sample.heat_capacity == _printed_janaf_node(
            table_id, temperature, "heat_capacity"
        )
        assert sample.entropy == _printed_janaf_node(table_id, temperature, "entropy")
        assert sample.enthalpy_increment == _printed_janaf_node(
            table_id, temperature, "enthalpy_increment"
        )
        assert sample.heat_capacity > 0
        assert sample.entropy > 0
        assert sample.enthalpy_increment.is_finite()

    interior_T = Decimal("1050")
    interior = janaf_tabulated_gas_thermo("CaO", interior_T)
    left_T, left_y, right_T, right_y = _printed_janaf_bracket(
        "Ca-030", interior_T, "heat_capacity"
    )
    chord = left_y + (right_y - left_y) * (interior_T - left_T) / (right_T - left_T)
    assert left_T < interior_T < right_T
    assert interior.bracket_width_K == right_T - left_T
    assert interior.heat_capacity == chord
    assert abs(interior.heat_capacity - left_y) <= abs(right_y - left_y)

    zero = janaf_tabulated_gas_thermo("Cr", Decimal("0"))
    assert zero.heat_capacity == 0
    assert zero.entropy == 0

    oxygen = janaf_tabulated_gas_thermo("O2", Decimal("500"))
    assert oxygen.table_id == "O-029"
    assert oxygen.bracket_width_K == 0
    assert oxygen.heat_capacity == Decimal("31.091")
    assert oxygen.entropy == Decimal("220.693")
    assert oxygen.enthalpy_increment == Decimal("6.084")
    assert oxygen.heat_capacity == _printed_janaf_node(
        "O-029", Decimal("500"), "heat_capacity"
    )
    assert oxygen.entropy == _printed_janaf_node("O-029", Decimal("500"), "entropy")
    assert oxygen.enthalpy_increment == _printed_janaf_node(
        "O-029", Decimal("500"), "enthalpy_increment"
    )

    gas_ids = _janaf_gas_table_ids()
    assert gas_ids["O2"] == ("O-029",)
    assert gas_ids["C"] == ("C-003",)

    with pytest.raises(ValueError, match="outside the JANAF table range"):
        janaf_tabulated_gas_thermo("Cr", Decimal("6500"))
    with pytest.raises(ValueError, match="no JANAF gas table"):
        janaf_tabulated_gas_thermo("NiO", Decimal("500"))
    with pytest.raises(ValueError, match="ambiguous JANAF gas tables"):
        janaf_tabulated_gas_thermo("HONO", Decimal("500"))


def test_vaporock_gas_census_preserves_same_input_relation(monkeypatch) -> None:
    import pytest

    pytest.importorskip("vaporock.equil")

    from simulator.battery import score as score_module
    from simulator.battery.compilation_tier import compilation_tier_census

    reference = _same_table_gas(
        "kgas-cp-janaf",
        "K",
        Quantity.CP,
        Decimal("1200"),
        Decimal("20.787"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    residuals = []
    compile_residual = score_module.compile_residual

    def capture_residual(*args, **kwargs):
        result = compile_residual(*args, **kwargs)
        residuals.append(result[0])
        return result

    monkeypatch.setattr(score_module, "compile_residual", capture_residual)
    census = compilation_tier_census(
        _context(reference),
        engines=(Engine.VAPOROCK,),
        audit_compile_residual=False,
    )

    row = next(
        row for row in census["rows"]
        if row["quantity"] == Quantity.CP.value
    )
    assert row["reachable"] == 1
    assert row["numeric"] == 1
    assert row["refused"] == {}
    assert len(residuals) == 1
    assert residuals[0].reference == reference.observation_id
    assert residuals[0].source_relation is SourceRelation.SAME_INPUT


def test_vaporock_gas_refuses_missing_and_charged_species() -> None:
    import pytest

    pytest.importorskip("vaporock.equil")

    identity = _gas_thermo()
    charged = replace(
        identity,
        species=replace(identity.species, charge=State.of(Decimal("1"))),
    )
    charged_attempt = predict_thermo_attempt(
        Engine.VAPOROCK,
        F.observation("k-plus-gas", "exp-1", charged, Decimal("0")),
    )
    assert charged_attempt.value is None
    assert charged_attempt.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert charged_attempt.refusal_detail["reason"] == "vaporock-charged-species-not-in-janaf-table"

    missing = predict_thermo_attempt(
        Engine.VAPOROCK,
        F.observation("krypton", "exp-1", _gas_thermo("Kr"), Decimal("0")),
    )
    assert missing.value is None
    assert missing.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert missing.refusal_detail["reason"] == "vaporock-species-not-in-janaf-table"


_OUTSIDE_SHOMATE_TABLE_CASES = (
    ("Cr", Decimal("500"), "Cr-005"),
    ("CaO", Decimal("1000"), "Ca-030"),
    ("Na2", Decimal("500"), "Na-011"),
    ("MgO", Decimal("100"), "Mg-011"),
    ("K", Decimal("1000"), "K-005"),
    ("Fe", Decimal("2200"), "Fe-008"),
    ("O2", Decimal("500"), "O-029"),
)


def test_vaporock_gas_outside_shomate_uses_janaf_table() -> None:
    """Outside the Shomate row, the attempt returns the printed gas-table node.

    Cr 500 K, CaO 1000 K, Na2 500 K, and MgO 100 K are the continuations
    that go non-positive on the polynomial. K 1000 K is 39.54 K below its
    first Shomate row. Fe 2200 K is the lunar shape, below its only row.
    O2 at 500 K is below its first coefficient row and on O-029, the
    reference-phase gas table. Each temperature is a printed JANAF node,
    so the interpolation remainder is 0.
    """

    import pytest

    pytest.importorskip("vaporock.equil")

    from simulator.battery.compilation_tier import _JANAF_TABLE_NOTICE
    from simulator.battery.records import Locator

    columns = {
        Quantity.CP: "heat_capacity",
        Quantity.S: "entropy",
        Quantity.H_MINUS_H298: "enthalpy_increment",
    }
    for formula, temperature, table_id in _OUTSIDE_SHOMATE_TABLE_CASES:
        for quantity, column in columns.items():
            printed = _printed_janaf_node(table_id, temperature, column)
            observation = replace(
                F.observation(
                    f"table-{formula}-{quantity.value}-{temperature}",
                    "exp-1",
                    _gas_thermo(formula, quantity, temperature),
                    printed,
                    source_id="nist-janaf-4th",
                ),
                locator=Locator(table=table_id, record=table_id),
            )
            attempt = predict_thermo_attempt(Engine.VAPOROCK, observation)
            assert attempt.refusal_reason is None
            assert attempt.value == printed
            assert attempt.authority is Authority.BRIDGE
            assert attempt.authority is not Authority.EXTRAPOLATED
            assert len(attempt.notices) == 1
            notice = attempt.notices[0]
            assert notice.kind is NoticeKind.DERIVATION_USES_COMPILATION
            assert notice.authority is Authority.BRIDGE
            assert notice.reason == f"{_JANAF_TABLE_NOTICE}; table_id={table_id}"
            assert notice.band == "[0, 6000] K"
            assert f"janaf_table_id={table_id}" in attempt.call_evidence


def test_vaporock_gas_outside_janaf_table_stays_refused() -> None:
    """Past the tabulated gas table, the refusal is the base token.

    K at 6500 K is above its Shomate end and above K-005. No continuation
    and no clamp.
    """

    import pytest

    pytest.importorskip("vaporock.equil")

    for quantity in (Quantity.CP, Quantity.S, Quantity.H_MINUS_H298):
        outside = predict_thermo_attempt(
            Engine.VAPOROCK,
            _same_table_gas(
                f"potassium-above-table-{quantity.value}",
                "K",
                quantity,
                Decimal("6500"),
                Decimal("0"),
            ),
        )
        assert outside.value is None
        assert outside.refusal_reason is RefusalReason.UNSUPPORTED
        assert (
            outside.refusal_detail["reason"]
            == "vaporock-temperature-outside-janaf-row"
        )


def test_tabulated_gas_attempt_refuses_nonpositive_heat_capacity(monkeypatch) -> None:
    """Cp > 0, S > 0, and a finite H are one gate for every quantity.

    The installed positive-temperature nodes of these gas tables are
    admissible. Cr-005 at 0 K is not (Cp = 0, S = 0), and a non-positive
    temperature never reaches this path. Substitute that inadmissible
    sample at a positive temperature and require Cp, S, and H−H298 all
    to refuse with no number.
    """

    from simulator.battery.compilation_tier import _vaporock_tabulated_gas_attempt
    from simulator.battery.generators import janaf as janaf_module
    from simulator.battery.generators.janaf import JANAFTabulatedGas
    from simulator.battery.records import Locator

    def nonpositive(_formula: str, _temperature: Decimal) -> JANAFTabulatedGas:
        return JANAFTabulatedGas(
            table_id="Cr-005",
            heat_capacity=Decimal("0"),
            entropy=Decimal("0"),
            enthalpy_increment=Decimal("-6.197"),
            low_K=Decimal("0"),
            high_K=Decimal("6000"),
            bracket_width_K=Decimal("0"),
        )

    monkeypatch.setattr(janaf_module, "janaf_tabulated_gas_thermo", nonpositive)
    for quantity in (Quantity.CP, Quantity.S, Quantity.H_MINUS_H298):
        observation = replace(
            F.observation(
                f"chromium-nonpositive-{quantity.value}",
                "exp-1",
                _gas_thermo("Cr", quantity, Decimal("500")),
                Decimal("0"),
                source_id="nist-janaf-4th",
            ),
            locator=Locator(table="Cr-005", record="Cr-005"),
        )
        attempt = _vaporock_tabulated_gas_attempt(
            "Cr",
            quantity,
            Decimal("500"),
            observation=observation,
            origin="chromium-nonpositive",
            species_name="Cr(g)",
            call_evidence="test",
        )
        assert attempt.value is None
        assert attempt.refusal_reason is RefusalReason.METRIC_DOMAIN
        assert (
            attempt.refusal_detail["reason"]
            == "vaporock-gas-thermo-not-admissible"
        )


def test_vaporock_gas_table_refuses_a_different_reference_basis() -> None:
    """A gas table answers only the observation's own reference basis.

    Mg-001 at 1366.104 K is the reference-state gas: published H−H298 is
    169.298, which includes vaporization. Mg-005 at that node is 22.198.
    The Robie magnesium reference-state entropy at 298.15 K is the crystal
    (32.67704 J/(mol·K)). B1452 magnesium at 1361 K is still that condensed
    zero, even where S is close to the gas table. All three refuse.
    B1259 and B1452 oxygen are the ideal gas at 298.15 K, so those rows
    return the printed O-029 node. A same-table gas row is covered by the
    printed-node test.
    """

    import pytest

    pytest.importorskip("vaporock.equil")

    from simulator.battery.records import Locator

    magnesium = replace(
        F.observation(
            "mg-001-enthalpy-at-boiling",
            "exp-1",
            _gas_thermo("Mg", Quantity.H_MINUS_H298, Decimal("1366.104")),
            Decimal("169.298"),
            source_id="nist-janaf-4th",
        ),
        locator=Locator(table="Mg-001", record="Mg-001"),
    )
    attempt = predict_thermo_attempt(Engine.VAPOROCK, magnesium)
    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert attempt.refusal_detail["reason"] == "tabulated_table_basis_mismatch"
    assert attempt.refusal_detail["observation_table"] == "Mg-001"
    assert attempt.refusal_detail["gas_table"] == "Mg-005"
    assert attempt.refusal_detail["gas_phase"] == Phase.G.value

    robie = replace(
        F.observation(
            "robie-magnesium-reference-entropy",
            "exp-1",
            _gas_thermo("Mg", Quantity.S, Decimal("298.15")),
            Decimal("32.67704"),
            source_id="robie-waldbaum-1968-usgs-b1259",
        ),
        locator=Locator(
            table="b1259-ht-0027-magnesium-reference-state",
            record="b1259-ht-0027-magnesium-reference-state",
        ),
    )
    robie_attempt = predict_thermo_attempt(Engine.VAPOROCK, robie)
    assert robie_attempt.value is None
    assert robie_attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert (
        robie_attempt.refusal_detail["reason"] == "tabulated_table_basis_mismatch"
    )
    assert robie_attempt.refusal_detail["gas_table"] == "Mg-005"
    assert (
        robie_attempt.refusal_detail["observation_table"]
        == "b1259-ht-0027-magnesium-reference-state"
    )

    near_boiling = replace(
        F.observation(
            "b1452-magnesium-near-boiling-entropy",
            "exp-1",
            _gas_thermo("Mg", Quantity.S, Decimal("1361")),
            Decimal("180.2"),
            source_id="robie-hemingway-fisher-1978-usgs-b1452",
        ),
        locator=Locator(table="b1452-magnesium", record="b1452-magnesium"),
    )
    near_boiling_attempt = predict_thermo_attempt(Engine.VAPOROCK, near_boiling)
    assert near_boiling_attempt.value is None
    assert near_boiling_attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert (
        near_boiling_attempt.refusal_detail["reason"]
        == "tabulated_table_basis_mismatch"
    )
    assert near_boiling_attempt.refusal_detail["gas_table"] == "Mg-005"

    from simulator.battery.generators.usgs_b1259 import (
        STANDARD_PRESSURE_PA as b1259_pressure,
    )
    from simulator.battery.generators.usgs_b1452 import (
        STANDARD_PRESSURE_PA as b1452_pressure,
    )

    oxygen_printed = _printed_janaf_node(
        "O-029", Decimal("500"), "enthalpy_increment"
    )
    # B1259's standard pressure is 1 atm. H−H298 does not depend on it.
    oxygen = replace(
        F.observation(
            "b1259-oxygen-enthalpy",
            "exp-1",
            replace(
                _gas_thermo("O2", Quantity.H_MINUS_H298, Decimal("500")),
                standard_pressure_Pa=State.of(b1259_pressure),
            ),
            oxygen_printed,
            source_id="robie-waldbaum-1968-usgs-b1259",
        ),
        locator=Locator(
            table="b1259-ht-0034-oxygen-reference-state",
            record="b1259-ht-0034-oxygen-reference-state",
        ),
    )
    oxygen_attempt = predict_thermo_attempt(Engine.VAPOROCK, oxygen)
    assert oxygen_attempt.refusal_reason is None
    assert oxygen_attempt.value == oxygen_printed
    assert oxygen_attempt.authority is Authority.BRIDGE

    oxygen_entropy = _printed_janaf_node("O-029", Decimal("298.15"), "entropy")
    # B1452's standard pressure is 1 bar, the JANAF gas-table pressure.
    b1452_oxygen = replace(
        F.observation(
            "b1452-oxygen-entropy",
            "exp-1",
            replace(
                _gas_thermo("O2", Quantity.S, Decimal("298.15")),
                standard_pressure_Pa=State.of(b1452_pressure),
            ),
            oxygen_entropy,
            source_id="robie-hemingway-fisher-1978-usgs-b1452",
        ),
        locator=Locator(table="b1452-oxygen", record="b1452-oxygen"),
    )
    b1452_attempt = predict_thermo_attempt(Engine.VAPOROCK, b1452_oxygen)
    assert b1452_attempt.refusal_reason is None
    assert b1452_attempt.value == oxygen_entropy
    assert b1452_attempt.authority is Authority.BRIDGE
    assert b1452_pressure == Decimal("100000")
    assert b1259_pressure == Decimal("101325")
    assert b1259_pressure != b1452_pressure


def test_vaporock_gas_entropy_refuses_one_atmosphere_against_janaf() -> None:
    """The stored B1259 oxygen entropy is 1 atm. JANAF's gas table is 0.1 MPa.

    b1259-298k-0060-oxygen S at 298.15 K is 204.999264 J/(mol·K) with
    standard_pressure_Pa 101325. O-029's entropy is the 1 bar value.
    S refuses and the detail carries both pressures. Cp at that same
    declared pressure still returns the printed O-029 node.
    """

    import pytest

    pytest.importorskip("vaporock.equil")

    from pathlib import Path

    from simulator.battery.generators.janaf import STANDARD_PRESSURE_PA
    from simulator.battery.migrate import load_yaml, observation_from_plain
    from simulator.battery.records import as_decimal

    root = Path(__file__).resolve().parents[2]
    document = load_yaml(
        root
        / "data/literature/observations-v2"
        / "compilations-robie-waldbaum-1968-usgs-b1259"
        / "b1259-298k-0060-oxygen.yaml"
    )
    raw = next(
        row
        for row in document["observations"]
        if row["identity"]["quantity"]["value"] == "S"
    )
    observation = observation_from_plain(raw)
    declared = as_decimal(observation.identity.standard_pressure_Pa.value)
    assert declared == Decimal("101325")
    assert observation.value.point == Decimal("204.999264")

    attempt = predict_thermo_attempt(Engine.VAPOROCK, observation)
    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert attempt.refusal_detail["reason"] == "tabulated_table_basis_mismatch"
    assert attempt.refusal_detail["observation_pressure_Pa"] == "101325"
    assert attempt.refusal_detail["gas_pressure_Pa"] == "100000"
    assert attempt.refusal_detail["gas_pressure_Pa"] == format(STANDARD_PRESSURE_PA, "f")
    assert attempt.refusal_detail["gas_table"] == "O-029"

    printed_cp = _printed_janaf_node("O-029", Decimal("500"), "heat_capacity")
    heat_capacity = replace(
        observation,
        observation_id=f"{observation.observation_id}:cp-500",
        identity=replace(
            observation.identity,
            quantity=Quantity.CP,
            temperature_K=State.of(Decimal("500")),
        ),
        value=Value.point_of(printed_cp),
    )
    heat_attempt = predict_thermo_attempt(Engine.VAPOROCK, heat_capacity)
    assert heat_attempt.refusal_reason is None
    assert heat_attempt.value == printed_cp
    assert heat_attempt.authority is Authority.BRIDGE


def _b1259_point(filename: str, observation_id: str):
    from pathlib import Path

    from simulator.battery.migrate import load_yaml, observation_from_plain

    root = Path(__file__).resolve().parents[2]
    document = load_yaml(
        root
        / "data/literature/observations-v2"
        / "compilations-robie-waldbaum-1968-usgs-b1259"
        / filename
    )
    raw = next(
        row for row in document["observations"] if row["observation_id"] == observation_id
    )
    return observation_from_plain(raw)


def test_vaporock_in_band_basis_and_enthalpy_form() -> None:
    """Inside a coefficient row, basis and the H term are still gated.

    B1259 O2 entropy at 800 K is 1 atm on O2's first Shomate row, so S
    refuses with both pressures. B1259 magnesium entropy at 1500 K is on
    Mg(g)'s row, and that bulletin's 298.15 K zero is the crystal, so S
    refuses on reference basis. O2 H−H298 at 800 K follows the printed
    O-029 node. K entropy at 1200 K on K-005 still answers. Co on
    (1000, 3000] stores H = 0 and does not reproduce printed H−H298, so
    that enthalpy refuses; heat capacity on the same row still answers.
    """

    import pytest

    pytest.importorskip("vaporock.equil")

    oxygen_entropy = _b1259_point(
        "b1259-ht-0034-oxygen-reference-state.yaml",
        "robie-waldbaum-1968-usgs-b1259:b1259-ht-0034-oxygen-reference-state:"
        "S:shared:T=800:col=entropy:name=oxygen-(reference-state):v=b128b3593f",
    )
    entropy_attempt = predict_thermo_attempt(Engine.VAPOROCK, oxygen_entropy)
    assert entropy_attempt.value is None
    assert entropy_attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert entropy_attempt.refusal_detail["reason"] == "tabulated_table_basis_mismatch"
    assert entropy_attempt.refusal_detail["observation_pressure_Pa"] == "101325"
    assert entropy_attempt.refusal_detail["gas_pressure_Pa"] == "100000"
    assert entropy_attempt.refusal_detail["gas_table"] == "O-029"
    assert entropy_attempt.refusal_detail["coefficient_interval"] == "inside"
    assert entropy_attempt.refusal_detail["branch"] == "shomate"

    magnesium_entropy = _b1259_point(
        "b1259-ht-0027-magnesium-reference-state.yaml",
        "robie-waldbaum-1968-usgs-b1259:b1259-ht-0027-magnesium-reference-state:"
        "S:shared:T=1500:col=entropy:name=magnesium-(reference-state):v=48985b088a",
    )
    magnesium_attempt = predict_thermo_attempt(Engine.VAPOROCK, magnesium_entropy)
    assert magnesium_attempt.value is None
    assert magnesium_attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert (
        magnesium_attempt.refusal_detail["reason"] == "tabulated_table_basis_mismatch"
    )
    assert magnesium_attempt.refusal_detail["gas_table"] == "Mg-005"
    assert magnesium_attempt.refusal_detail["coefficient_interval"] == "inside"
    assert magnesium_attempt.refusal_detail["branch"] == "shomate"
    assert "gas_pressure_Pa" not in magnesium_attempt.refusal_detail

    oxygen_enthalpy = _b1259_point(
        "b1259-ht-0034-oxygen-reference-state.yaml",
        "robie-waldbaum-1968-usgs-b1259:b1259-ht-0034-oxygen-reference-state:"
        "H_minus_H298:shared:T=800:col=H_minus_H298:name=oxygen-(reference-state):"
        "v=29ef8ac3ab",
    )
    printed = _printed_janaf_node("O-029", Decimal("800"), "enthalpy_increment")
    assert printed == Decimal("15.835")
    enthalpy_attempt = predict_thermo_attempt(Engine.VAPOROCK, oxygen_enthalpy)
    assert enthalpy_attempt.refusal_reason is None
    assert enthalpy_attempt.value is not None
    assert abs(enthalpy_attempt.value - printed) <= Decimal("0.001")
    assert enthalpy_attempt.value < Decimal("20")
    assert enthalpy_attempt.authority is Authority.BRIDGE
    assert enthalpy_attempt.notices == ()
    assert "enthalpy_form=through_F" in enthalpy_attempt.call_evidence
    assert "green_subtract_H=" in enthalpy_attempt.call_evidence

    potassium = _same_table_gas(
        "k-005-entropy-1200",
        "K",
        Quantity.S,
        Decimal("1200"),
        Decimal("189.01928920321794"),
        source_id="nist-janaf-4th",
    )
    potassium_attempt = predict_thermo_attempt(Engine.VAPOROCK, potassium)
    assert potassium_attempt.refusal_reason is None
    assert potassium_attempt.value == Decimal("189.01928920321794")
    assert potassium_attempt.notices == ()
    assert potassium_attempt.authority is Authority.BRIDGE

    cobalt_enthalpy = _same_table_gas(
        "co-unverified-enthalpy",
        "Co",
        Quantity.H_MINUS_H298,
        Decimal("2000"),
        Decimal("0"),
        source_id="nist-janaf-4th",
    )
    cobalt_attempt = predict_thermo_attempt(Engine.VAPOROCK, cobalt_enthalpy)
    assert cobalt_attempt.value is None
    assert cobalt_attempt.refusal_reason is RefusalReason.UNSUPPORTED
    assert (
        cobalt_attempt.refusal_detail["reason"]
        == "shomate_enthalpy_normalisation_unverified"
    )
    assert cobalt_attempt.refusal_detail["coefficient_interval"] == "inside"
    cobalt_heat = _same_table_gas(
        "co-unverified-heat-capacity",
        "Co",
        Quantity.CP,
        Decimal("2000"),
        Decimal("0"),
        source_id="nist-janaf-4th",
    )
    heat_attempt = predict_thermo_attempt(Engine.VAPOROCK, cobalt_heat)
    assert heat_attempt.refusal_reason is None
    assert heat_attempt.value is not None
    assert heat_attempt.value > 0
    assert heat_attempt.notices == ()
    assert "enthalpy_form=unverified" in heat_attempt.call_evidence


def test_vaporock_in_band_shomate_uses_the_shared_admissibility_guard(
    monkeypatch,
) -> None:
    """A containing Shomate row uses the same Cp/S/H gate as the gas table.

    Cp <= 0, S <= 0, or a non-finite H refuses every quantity, including
    a request for a quantity that would otherwise be finite. An admissible
    row still returns that quantity at BRIDGE with no notice. The
    admissible numbers are the stubbed evaluator outputs after
    as_decimal(str(float(raw))), not a second formula.
    """

    import sys
    import types

    from simulator.battery import compilation_tier

    equil = types.ModuleType("vaporock.equil")
    equil.__file__ = "vaporock/equil.py"
    package = types.ModuleType("vaporock")
    package.equil = equil
    monkeypatch.setitem(sys.modules, "vaporock", package)
    monkeypatch.setitem(sys.modules, "vaporock.equil", equil)
    monkeypatch.setattr(
        compilation_tier,
        "_vaporock_gas_provenance",
        lambda _module: "test-provenance",
    )
    # The stub row is not K's coefficient row. The production classifier
    # would call it unverified. This test pins the shared numeric gate,
    # so the convention is fixed as subtract-H, which is what the stub's
    # _janaf_dH (enthalpy + H) undoes.
    monkeypatch.setattr(
        compilation_tier,
        "_shomate_enthalpy_form",
        lambda _vapor, _row, _table_id: "subtract_H",
    )

    class _Coefs:
        def __init__(self, row: dict[str, float]) -> None:
            self.loc = {"K(g)": row}

    class _Vapor:
        def __init__(self, row: dict[str, float], entropy: float, enthalpy: float) -> None:
            self.vapor_coefs = _Coefs(row)
            self.entropy = entropy
            self.enthalpy = enthalpy

        def _janaf_S(self, _temperature: float, _row: dict[str, float]) -> float:
            return self.entropy

        def _janaf_dH(self, _temperature: float, _row: dict[str, float]) -> float:
            return self.enthalpy + _row["H"]

    def install(heat_capacity: float, entropy: float, enthalpy: float) -> None:
        row = {
            "A": heat_capacity,
            "B": 0.0,
            "C": 0.0,
            "D": 0.0,
            "E": 0.0,
            "H": 0.0,
            "T_min": 100.0,
            "T_max": 2000.0,
        }
        monkeypatch.setattr(
            compilation_tier._vaporock_gas_attempt,
            "_janaf_vapor",
            _Vapor(row, entropy, enthalpy),
            raising=False,
        )

    refused = (
        (-1.0, 180.0, 10.0),
        (20.0, -1.0, 10.0),
        (20.0, 180.0, float("nan")),
    )
    for index, (heat_capacity, entropy, enthalpy) in enumerate(refused):
        install(heat_capacity, entropy, enthalpy)
        for quantity in (Quantity.CP, Quantity.S, Quantity.H_MINUS_H298):
            attempt = predict_thermo_attempt(
                Engine.VAPOROCK,
                _same_table_gas(
                    f"k-inadmissible-{index}-{quantity.value}",
                    "K",
                    quantity,
                    Decimal("1000"),
                    Decimal("0"),
                ),
            )
            assert attempt.value is None
            assert attempt.refusal_reason is RefusalReason.METRIC_DOMAIN
            assert (
                attempt.refusal_detail["reason"]
                == "vaporock-gas-thermo-not-admissible"
            )

    install(20.5, 100.25, 1.5)
    expected = {
        Quantity.CP: Decimal("20.5"),
        Quantity.S: Decimal("100.25"),
        Quantity.H_MINUS_H298: Decimal("1.5"),
    }
    for quantity, pinned in expected.items():
        attempt = predict_thermo_attempt(
            Engine.VAPOROCK,
            _same_table_gas(
                f"k-admissible-{quantity.value}",
                "K",
                quantity,
                Decimal("1000"),
                pinned,
            ),
        )
        assert attempt.refusal_reason is None
        assert attempt.value == pinned
        assert attempt.authority is Authority.BRIDGE
        assert attempt.notices == ()


def test_vaporock_import_failure_is_a_typed_unavailable_refusal(monkeypatch) -> None:
    import builtins

    from simulator.battery import compilation_tier

    monkeypatch.delattr(
        compilation_tier._vaporock_gas_attempt, "_janaf_vapor", raising=False
    )
    original_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "vaporock.equil":
            raise ImportError("test-only missing VapoRock")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    attempt = predict_thermo_attempt(
        Engine.VAPOROCK,
        F.observation("kgas-unavailable", "exp-1", _gas_thermo(), Decimal("0")),
    )
    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.ATTEMPTED_UNAVAILABLE


def test_pure_phase_refuses_non_species_molar_basis_before_engine_call() -> None:
    identity = F.oxide_identity(
        "MgO",
        Phase.CR,
        T_K=Decimal("1000"),
        per=PerBasis.MOL_ATOM,
        metal_formula="Mg",
        polymorph=Polymorph.PERICLASE.value,
    )
    identity = replace(identity, quantity=Quantity.CP)
    calls = []

    def pure_phase(*args):
        calls.append(args)
        raise AssertionError("basis mismatch must refuse before the engine call")

    attempt = predict_thermo_attempt(
        Engine.MAGEMIN,
        F.observation("mgo-per-atom", "exp-1", identity, Decimal("4")),
        pure_phase=pure_phase,
    )
    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.UNSUPPORTED
    assert attempt.refusal_detail["reason"] == "pure-phase-per-basis-mismatch"
    assert attempt.refusal_detail["expected_per"] == PerBasis.MOL_SPECIES.value
    assert attempt.refusal_detail["actual_per"] == PerBasis.MOL_ATOM.value
    assert calls == []


def test_pure_phase_refuses_non_298_enthalpy_anchor_before_engine_call() -> None:
    identity = F.oxide_identity(
        "MgO",
        Phase.CR,
        T_K=Decimal("1000"),
        per=PerBasis.MOL_SPECIES,
        metal_formula="Mg",
        polymorph=Polymorph.PERICLASE.value,
    )
    identity = replace(
        identity,
        quantity=Quantity.H_MINUS_H298,
        subtype=State.of("apparent enthalpy, stable-phase anchor"),
    )
    calls = []

    def pure_phase(*args):
        calls.append(args)
        raise AssertionError("anchor mismatch must refuse before the engine call")

    attempt = predict_thermo_attempt(
        Engine.MAGEMIN,
        F.observation("mgo-wrong-anchor", "exp-1", identity, Decimal("4")),
        pure_phase=pure_phase,
    )
    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.UNSUPPORTED
    assert attempt.refusal_detail["reason"] == "pure-phase-enthalpy-anchor-mismatch"
    assert attempt.refusal_detail["expected_anchor"] == "H(T)-H(298.15 K)"
    assert calls == []


def test_compilation_tier_is_beside_measured_and_same_source_is_flagged() -> None:
    experiment = F.tabulation_experiment()
    identity = _na2o_liquid()
    reference = F.observation(
        "na2o-janaf",
        experiment.experiment_id,
        identity,
        _PRINTED_DFG,
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        admission=AdmissionStatus.PENDING,
        source_id="nist-janaf-4th",
    )
    ctx = _context(reference)
    residual, candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        comparison_ids=set(),
    )
    assert reference.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
    assert reference.admission.status is AdmissionStatus.PENDING
    assert residual.score_eligible is False
    assert "reference_measured_evidence" in residual.exclusions
    assert residual.numeric is not None
    assert residual.numeric.unit == "kJ_per_declared_mol_basis"
    assert residual.numeric.operation.value == "absolute"
    assert residual.source_relation is SourceRelation.SAME_INPUT
    assert residual.status is ResidualStatus.NO_BAND
    assert any(
        notice.kind is NoticeKind.DERIVATION_USES_COMPILATION
        and "nist-janaf-4th" in notice.reason
        and "none" in notice.reason
        for notice in residual.notices
    )
    assert candidate is not None
    assert candidate.evidence.class_.value is EvidenceClass.ENGINE_PREDICTION
    assert comparison_candidates(ctx) == ()

    pankratz_work = replace(F.work("pankratz-work"), source_ids=("pankratz-work",))
    janaf_work = replace(
        F.work("janaf-work"),
        source_ids=("janaf-work", "janaf-4th", "nist-webbook", "nasa-glenn"),
    )
    janaf_exp = F.tabulation_experiment(experiment_id="janaf-exp", work_id="janaf-work")
    pank_exp = F.tabulation_experiment(experiment_id="pank-exp", work_id="pankratz-work")
    janaf_row = replace(reference, observation_id="janaf-row", experiment_id="janaf-exp")
    pank_row = replace(
        reference,
        observation_id="pank-row",
        experiment_id="pank-exp",
        source_id="pankratz-1984",
    )
    independent_ctx = ScoreContext(
        works={janaf_work.work_id: janaf_work, pankratz_work.work_id: pankratz_work},
        experiments={
            janaf_exp.experiment_id: janaf_exp,
            pank_exp.experiment_id: pank_exp,
        },
        observations={
            janaf_row.observation_id: janaf_row,
            pank_row.observation_id: pank_row,
        },
        hostname="test",
    )
    independent, _ = compile_residual(
        pank_row,
        Engine.INTERNAL_ANALYTICAL,
        context=independent_ctx,
        comparison_ids=set(),
    )
    assert independent.source_relation is SourceRelation.INDEPENDENT
    assert independent.score_eligible is False
    assert independent.numeric is not None
    assert independent.status is ResidualStatus.NO_BAND
    assert all(
        notice.kind is not NoticeKind.DERIVATION_USES_COMPILATION
        for notice in independent.notices
    )

    both = replace(
        independent_ctx,
        observations={
            reference.observation_id: reference,
            pank_row.observation_id: pank_row,
        },
        experiments={
            **independent_ctx.experiments,
            experiment.experiment_id: experiment,
        },
        origins={
            reference.observation_id: "compilations-janaf/janaf-Na.yaml",
            pank_row.observation_id: "compilations-pankratz-1984-usbm-b677.yaml",
        },
    )
    report = render_score_report(
        (residual, independent),
        context=both,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    assert "## Measured tier" in report
    assert "## Compilation tier" in report
    assert "never added" in report
    compilation_report = report.split("## Compilation tier", 1)[1].split(
        "## Refusal census", 1
    )[0]
    assert "implementation fidelity, not" in compilation_report
    assert (
        "| compilation | rail | engine | relation | quantity | unit | n | signed median residual | MAD | bias-to-scatter ratio | max |residual| | median abs residual | "
        "RMS residual | band value | band kind | band source n | no-band reason | in-band | "
        "out-of-band | n no band | n same-source |"
    ) in compilation_report
    assert (
        f"`compilations-janaf` | `delta_fG` | 1 | 1 | 0 | "
        f"{abs(residual.numeric.value)}"
    ) in compilation_report
    summary = next(
        row
        for row in compilation_report.splitlines()
        if row.startswith(
            "| compilations-janaf | thermochemistry | internal-analytical | same_input | delta_fG |"
        )
    )
    summary_cells = [cell.strip() for cell in summary.strip("|").split("|")]
    assert "|residual| ≤ ticket line." in compilation_report
    assert summary_cells[5] == "kJ/mol"
    assert summary_cells[6] == "1"
    assert summary_cells[7] == str(residual.numeric.value)
    assert summary_cells[17:] == ["no_band in-band=0", "no_band out-of-band=0", "1", "1"]
    assert compilation_family("ATcT.yaml", None) == "ATcT"
    measured = [
        row
        for row in headline_rows((residual, independent), context=both)
        if row["rail"] == "thermochemistry" and row["engine"] == "internal-analytical"
    ]
    assert measured[0]["n_candidates"] == 0
    assert measured[0]["n_refused"] == 0
    assert measured[0]["n_scored"] == 0
    assert "| internal-analytical | 2 |" in report


def test_unknown_relation_keeps_residual_without_band() -> None:
    from simulator.battery.records import Execution
    from simulator.battery.score import EnginePrediction

    experiment = F.tabulation_experiment()
    reference = F.observation(
        "ledger-row",
        experiment.experiment_id,
        _na2o_liquid(),
        _PRINTED_DFG,
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="nist-janaf-4th",
    )
    ctx = _context(
        reference,
        origins={reference.observation_id: "gibbs_battery_residual_ledger.yaml"},
    )
    residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        comparison_ids={reference.observation_id},
        predict=lambda engine, obs, **kwargs: EnginePrediction(
            engine=engine,
            channel="internal-analytical",
            execution=Execution(ExecutionState.PRODUCED, "test prediction"),
            value=_PRINTED_DFG,
            unit="kJ_per_declared_mol_basis",
            authority=Authority.CERTIFIED,
            coefficient_sources=("nasa-cea-thermo",),
            lineage_complete=True,
            identity=obs.identity,
        ),
    )
    assert residual.source_relation is SourceRelation.UNKNOWN
    assert residual.status is ResidualStatus.NO_BAND
    assert residual.numeric is not None
    assert residual.numeric.decision_band is None


def test_out_of_range_reaction_is_not_formation_gibbs() -> None:
    identity = F.oxide_identity(
        "K2O",
        Phase.CR,
        T_K=Decimal("298.15"),
        per=PerBasis.MOL_SPECIES,
        metal_formula="K",
        nu_metal=Fraction(4),
        nu_o2=Fraction(1),
        polymorph="beta",
    )
    identity = replace(
        identity,
        species=replace(identity.species, polymorph=State.unknown("no polymorph named")),
    )
    printed = Decimal("-322.094")
    observation = F.observation(
        "k2o-298",
        "exp-1",
        identity,
        printed,
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
    assert attempt.value is None
    assert attempt.refusal_detail["reason"] == "ellingham-formation-outside-segment"
    ctx = _context(
        observation,
        origins={observation.observation_id: "compilations-janaf/janaf-K.yaml"},
    )
    residual, _candidate = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        comparison_ids=set(),
    )
    assert residual.numeric is None
    assert residual.status is ResidualStatus.REFUSED
    report = render_score_report(
        (residual,),
        context=ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    census = report.split("## Refusal census", 1)[1].split("## Admission", 1)[0]
    assert "ellingham-formation-outside-segment" not in census
    assert "| internal-analytical | 1 | 1 |" in report
    beta = F.oxide_identity(
        "Na2O",
        Phase.CR,
        T_K=Decimal("1100"),
        per=PerBasis.MOL_SPECIES,
        metal_formula="Na",
        nu_metal=Fraction(4),
        nu_o2=Fraction(1),
        polymorph="beta",
    )
    in_band = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("na2o-beta", "exp-1", beta, Decimal("-267.206")),
    )
    assert in_band.value is not None
    assert abs(in_band.value - Decimal("-267.206")) < Decimal("0.001")
    alumina = F.oxide_identity(
        "Al2O3",
        Phase.L,
        T_K=Decimal("2900"),
        per=PerBasis.MOL_SPECIES,
        metal_formula="Al",
        nu_metal=Fraction(4, 3),
        nu_o2=Fraction(1),
    )
    past_end = predict_thermo_attempt(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("al2o3-2900", "exp-1", alumina, Decimal("-754.429")),
    )
    assert past_end.value is None
    assert past_end.refusal_detail["reason"] == "ellingham-formation-outside-segment"


def test_quoted_compilation_row_is_not_omitted() -> None:
    identity = replace(_na2o_liquid(), quantity=Quantity.DELTA_FH)
    quoted = F.observation(
        "quoted-dh",
        "exp-1",
        identity,
        Decimal("-2594.1"),
        evidence=EvidenceClass.QUOTED_ATTRIBUTED,
        source_id="hemingway-haas-robinson-1982-usgs-b1544",
    )
    origin = "compilations-hemingway-haas-robinson-1982-usgs-b1544/table.yaml"
    ctx = _context(quoted, origins={quoted.observation_id: origin})
    residual, _candidate = compile_residual(
        quoted,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        comparison_ids=set(),
    )
    assert residual.score_eligible is False
    report = render_score_report(
        (residual,),
        context=ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
    )
    assert "`compilations-hemingway-haas-robinson-1982-usgs-b1544`" in report
    assert "`delta_fH`" in report
    measured = [
        row
        for row in headline_rows((residual,), context=ctx)
        if row["rail"] == "thermochemistry" and row["engine"] == "internal-analytical"
    ]
    assert measured[0]["n_candidates"] == 0
    assert measured[0]["n_refused"] == 0


def test_nasa_glenn_is_same_source_and_pankratz_bulletins_are_not() -> None:
    nasa_exp = F.tabulation_experiment(experiment_id="nasa-exp", work_id="nasa-work")
    janaf_work = replace(
        F.work("janaf-work"),
        source_ids=("janaf-work", "janaf-4th", "nist-webbook"),
    )
    nasa_work = replace(F.work("nasa-work"), source_ids=("nasa-work", "nasa-glenn"))
    row = F.observation(
        "nasa-row",
        nasa_exp.experiment_id,
        _na2o_liquid(),
        _PRINTED_DFG,
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nasa-glenn",
    )
    ctx = ScoreContext(
        works={janaf_work.work_id: janaf_work, nasa_work.work_id: nasa_work},
        experiments={nasa_exp.experiment_id: nasa_exp},
        observations={row.observation_id: row},
        hostname="test",
    )
    residual, _candidate = compile_residual(
        row,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        comparison_ids=set(),
    )
    assert residual.source_relation is SourceRelation.SAME_INPUT
    assert any(
        notice.kind is NoticeKind.DERIVATION_USES_COMPILATION for notice in residual.notices
    )


def test_pure_phase_value_is_not_reused_at_the_next_temperature(monkeypatch) -> None:
    from simulator.battery.compilation_tier import ThermoAttempt, compilation_tier_census

    calls: list[Decimal] = []

    def fake(engine, observation, **kwargs):
        del engine, kwargs
        temperature = observation.identity.temperature_K.value
        calls.append(temperature)
        if temperature <= 0:
            return ThermoAttempt(
                value=None,
                unit=None,
                authority=Authority.REFUSED,
                notices=(),
                refusal_reason=RefusalReason.IDENTITY_UNKNOWN,
                refusal_detail={"reason": "temperature-not-positive"},
                call_evidence="t0",
            )
        return ThermoAttempt(
            value=temperature,
            unit="J_per_declared_mol_basis_per_K",
            authority=Authority.BRIDGE,
            notices=(),
            refusal_reason=None,
            refusal_detail={},
            call_evidence=f"T={temperature}",
        )

    monkeypatch.setattr("simulator.battery.compilation_tier.predict_thermo_attempt", fake)
    identity = replace(
        _pure_phase_identity(),
        temperature_K=State.unknown("series"),
    )
    series = F.observation(
        "cp-series",
        "exp-1",
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    series = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=(
                (Decimal("0"), Decimal("0")),
                (Decimal("500"), Decimal("500")),
                (Decimal("1500"), Decimal("1500")),
            ),
        ),
    )
    ctx = _context(series)
    compilation_tier_census(
        ctx,
        engines=(Engine.MAGEMIN,),
        invoke_pure_phase=True,
        audit_compile_residual=False,
    )
    assert calls == [Decimal("0"), Decimal("500"), Decimal("1500")]
    calls.clear()
    compilation_tier_census(
        ctx,
        engines=(Engine.MAGEMIN,),
        invoke_pure_phase=False,
        audit_compile_residual=False,
    )
    assert calls == [Decimal("0"), Decimal("500")]


def test_vaporock_gas_series_attempts_each_temperature(monkeypatch) -> None:
    from simulator.battery.compilation_tier import ThermoAttempt, compilation_tier_census

    calls: list[Decimal] = []

    def fake(engine, observation, **kwargs):
        del engine, kwargs
        temperature = observation.identity.temperature_K.value
        calls.append(temperature)
        return ThermoAttempt(
            value=temperature,
            unit="J_per_declared_mol_basis_per_K",
            authority=Authority.BRIDGE,
            notices=(),
            refusal_reason=None,
            refusal_detail={},
            call_evidence=f"T={temperature}",
        )

    monkeypatch.setattr("simulator.battery.compilation_tier.predict_thermo_attempt", fake)
    identity = replace(
        _gas_thermo("K", Quantity.CP),
        temperature_K=State.unknown("series"),
    )
    series = F.observation(
        "kgas-cp-series",
        "exp-1",
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    series = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=(
                (Decimal("1200"), Decimal("20.787")),
                (Decimal("1400"), Decimal("20.793")),
            ),
        ),
    )

    compilation_tier_census(
        _context(series),
        engines=(Engine.VAPOROCK,),
        invoke_pure_phase=False,
        audit_compile_residual=False,
    )

    assert calls == [Decimal("1200"), Decimal("1400")]


def test_report_rebuilt_from_payloads_keeps_the_tier_split() -> None:
    from simulator.battery.score import render_score_report_from_payloads, residual_to_plain

    experiment = F.tabulation_experiment()
    reference = F.observation(
        "na2o-janaf",
        experiment.experiment_id,
        _na2o_liquid(),
        _PRINTED_DFG,
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        admission=AdmissionStatus.PENDING,
        source_id="nist-janaf-4th",
    )
    ctx = _context(
        reference,
        origins={reference.observation_id: "compilations-janaf/janaf-Na.yaml"},
    )
    residual, _candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=ctx,
        comparison_ids=set(),
    )
    report = render_score_report_from_payloads(
        [residual_to_plain(residual)],
        engines=(Engine.INTERNAL_ANALYTICAL,),
        hostname="test",
        observations=ctx.observations,
        origins=ctx.origins,
    )
    assert "## Measured tier" in report
    assert "## Compilation tier" in report
    assert "| thermochemistry | internal-analytical | — | 0 | 0 | 0 |" in report
    assert "| internal-analytical | 1 |" in report
    census = report.split("## Refusal census", 1)[1]
    assert "internal-analytical" not in census or "0 |" in report


def test_streamed_series_payloads_keep_each_cell_in_the_compilation_tier(tmp_path) -> None:
    from simulator.battery.enums import QUANTITY_UNITS
    from simulator.battery.records import Execution
    from simulator.battery.score import (
        _ResidualJsonlRows,
        _score_store_to_jsonl,
        headline_payload_records,
        load_residuals_jsonl,
        render_score_report_from_payloads,
    )

    engine = Engine.INTERNAL_ANALYTICAL
    identity = replace(_na2o_liquid(), temperature_K=State.unknown("series"))
    series = F.observation(
        "payload-cell-series",
        "exp-1",
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    series = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=((Decimal("2100"), Decimal("-4")), (_T, _PRINTED_DFG)),
        ),
    )
    ctx = _context(
        series,
        origins={series.observation_id: "compilations-janaf/janaf-Na.yaml"},
    )

    def predict(engine, cell, **kwargs):
        del kwargs
        return EnginePrediction(
            engine,
            "internal-analytical",
            Execution(
                state=ExecutionState.PRODUCED,
                call_evidence=f"payload-cell-{cell.identity.temperature_K.value}",
            ),
            value=cell.value.point,
            unit=QUANTITY_UNITS[Quantity.DELTA_FG],
            identity=cell.identity,
        )

    path = tmp_path / "residuals.jsonl"
    count, _metadata = _score_store_to_jsonl(
        ctx,
        path,
        engines=(engine,),
        predict=predict,
    )
    partial_path = path.with_name(path.name + ".partial")
    rows = load_residuals_jsonl(partial_path)
    assert count == 2
    assert [row["reference"] for row in rows] == [
        "payload-cell-series#t2100v-4",
        "payload-cell-series#t2200v-3.886",
    ]
    records = headline_payload_records(
        rows,
        engines=(engine,),
        observations=ctx.observations,
        origins=ctx.origins,
    )
    compilation = next(
        row
        for row in records
        if row["tier"] == "compilation"
        and row["rail"] == "thermochemistry"
        and row["engine"] == engine.value
    )
    assert (
        compilation["n_candidates"],
        compilation["n_scored"],
        compilation["n_no_band"],
        compilation["n_refused"],
    ) == (2, 2, 2, 0)
    report = render_score_report_from_payloads(
        _ResidualJsonlRows(partial_path, metadata=_metadata),
        engines=(engine,),
        hostname="test",
        observations=ctx.observations,
        origins=ctx.origins,
    )
    assert "| `compilations-janaf` | `delta_fG` | 2 | 2 | 0 |" in report


def test_replaced_observation_is_not_served_from_the_work_cache() -> None:
    from simulator.battery.validate import _inputs_registered_under_work, _table_ids

    work = F.work()
    experiment = F.tabulation_experiment()
    identity = _na2o_liquid()
    first_obs = F.observation("obs-A", experiment.experiment_id, identity, Decimal("1"))
    second_obs = F.observation("obs-B", experiment.experiment_id, identity, Decimal("2"))
    observations = {first_obs.observation_id: first_obs}
    experiments = {experiment.experiment_id: experiment}
    works = {work.work_id: work}
    table_ids = _table_ids(works)
    first = _inputs_registered_under_work(work, observations, experiments, table_ids)
    assert "obs-A" in first
    observations.clear()
    observations[second_obs.observation_id] = second_obs
    second = _inputs_registered_under_work(work, observations, experiments, table_ids)
    assert "obs-A" not in second
    assert "obs-B" in second


def test_score_store_pairs_tables_from_one_index(monkeypatch) -> None:
    from simulator.battery.records import Execution
    from simulator.battery.score import score_store
    from simulator.battery.validate import _table_payloads, build_printed_thermo_index

    calls = {"full": 0, "indexed": 0}
    real = _table_payloads

    def wrapped(reference, observations, index=None):
        calls["full" if index is None else "indexed"] += 1
        return real(reference, observations, index)

    monkeypatch.setattr("simulator.battery.validate._table_payloads", wrapped)
    log_identity = replace(_na2o_liquid(), quantity=Quantity.LOG10_KF)
    delta = F.observation(
        "delta",
        "exp-1",
        _na2o_liquid(),
        Decimal("-3.886"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    log_row = F.observation(
        "logk",
        "exp-1",
        log_identity,
        Decimal("5"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    ctx = _context(
        delta,
        log_row,
        origins={
            delta.observation_id: "compilations-janaf/janaf-Na.yaml",
            log_row.observation_id: "compilations-janaf/janaf-Na.yaml",
        },
    )
    direct = _table_payloads(delta, ctx.observations)
    indexed = _table_payloads(
        delta, ctx.observations, build_printed_thermo_index(ctx.observations)
    )
    assert len(direct) == 1
    assert indexed == direct
    calls["full"] = 0
    calls["indexed"] = 0

    def predict(engine, observation, **kwargs):
        del kwargs
        return EnginePrediction(
            engine=engine,
            channel="internal-analytical",
            execution=Execution(state=ExecutionState.NOT_PROBED),
            refusal_reason=RefusalReason.UNSUPPORTED,
            refusal_detail={"reason": "test"},
            identity=observation.identity if hasattr(observation, "identity") else None,
        )

    residuals, _candidates = score_store(
        ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=predict,
    )
    refused = [row for row in residuals if row.reference in {"delta", "logk"}]
    assert len(refused) == 2
    assert all(
        row.refusal is not None and row.refusal.reason is RefusalReason.INVALID_SOURCE
        for row in refused
    )
    assert calls["full"] == 0
    assert calls["indexed"] == 2


def test_score_progress_total_is_series_cells(monkeypatch, capsys) -> None:
    from simulator.battery.records import Execution
    from simulator.battery.score import score_store

    ticks = iter([0.0, 61.0])
    monkeypatch.setattr(
        "simulator.battery.score.time.monotonic",
        lambda: next(ticks, 61.0),
    )
    identity = replace(_na2o_liquid(), temperature_K=State.unknown("series"))
    series = F.observation(
        "na2o-series",
        "exp-1",
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    series = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=((_T, _PRINTED_DFG), (Decimal("2100"), Decimal("-10"))),
        ),
    )
    ctx = _context(
        series,
        origins={series.observation_id: "compilations-janaf/janaf-Na.yaml"},
    )

    def predict(engine, observation, **kwargs):
        del kwargs
        return EnginePrediction(
            engine=engine,
            channel="internal-analytical",
            execution=Execution(state=ExecutionState.NOT_PROBED),
            refusal_reason=RefusalReason.UNSUPPORTED,
            refusal_detail={"reason": "test"},
            identity=observation.identity,
        )

    score_store(ctx, engines=(Engine.INTERNAL_ANALYTICAL,), predict=predict)
    printed = capsys.readouterr().out
    assert "score progress 1/2 " in printed
    assert "score progress 1/1 " not in printed


def test_scorer_streams_series_cells_without_child_observations(monkeypatch) -> None:
    from simulator.battery.records import Execution, Observation
    from simulator.battery.score import ENGINE_CHANNELS, score_store

    identity = replace(_na2o_liquid(), temperature_K=State.unknown("series"))
    series = F.observation(
        "cell-view-series",
        "exp-1",
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    series = replace(
        series,
        value=Value(
            ValueKind.SERIES,
            series=((Decimal("2100"), Decimal("-4")), (_T, _PRINTED_DFG)),
        ),
    )
    ctx = _context(
        series,
        origins={series.observation_id: "compilations-janaf/janaf-Na.yaml"},
    )
    references: list[str] = []

    def no_observation_construction(*args, **kwargs):
        del args, kwargs
        raise AssertionError("scoring constructed an Observation for a series cell")

    monkeypatch.setattr(Observation, "__init__", no_observation_construction)

    def predict(engine, cell, **kwargs):
        del kwargs
        assert not isinstance(cell, Observation)
        references.append(cell.observation_id)
        return EnginePrediction(
            engine,
            ENGINE_CHANNELS[engine],
            Execution(state=ExecutionState.NOT_PROBED),
            refusal_reason=RefusalReason.UNSUPPORTED,
            refusal_detail={"reason": "cell-view-test"},
            identity=cell.identity,
        )

    residuals, _ = score_store(
        ctx,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=predict,
    )
    assert references == [
        "cell-view-series#t2100v-4",
        "cell-view-series#t2200v-3.886",
    ]
    assert [row.reference for row in residuals] == references


def test_internal_predicts_janaf_fusion_enthalpy_and_entropy() -> None:
    # JANAF Mg-008/Mg-009 print ΔfH at 3000 K as −709.524/−632.496
    # kJ/mol, so ΔH_fus is 77.028 kJ/mol. At 3100 K the printed values
    # are −707.689/−629.904 kJ/mol. Printed entropies give ΔS=24.804 at
    # 3000 K (167.293−142.489) and 25.052 J/(mol K) at 3100 K
    # (169.488−144.436), independently of the enthalpy values.
    experiment = F.tabulation_experiment()
    expected = (
        (Quantity.ENTHALPY_OF_FUSION, Decimal("3000"), Decimal("77.028")),
        (
            Quantity.ENTROPY_OF_FUSION,
            Decimal("3000"),
            Decimal("24.804"),
        ),
        (
            Quantity.ENTROPY_OF_FUSION,
            Decimal("3100"),
            Decimal("25.052"),
        ),
    )
    for quantity, temperature, value in expected:
        identity = _fusion_identity(quantity, temperature)
        observation = F.observation(
            f"mg-fusion-{quantity.value}",
            experiment.experiment_id,
            identity,
            value,
            evidence=EvidenceClass.MEASURED_DIRECT,
        )
        attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
        assert attempt.value == value
        assert attempt.authority is Authority.BRIDGE
        if quantity is Quantity.ENTHALPY_OF_FUSION:
            assert attempt.notices == ()
        else:
            assert any(
                notice.reason == "janaf_fusion_transition_off_equilibrium"
                for notice in attempt.notices
            )
        assert attempt.unit == {
            Quantity.ENTHALPY_OF_FUSION: "kJ_per_declared_mol_basis",
            Quantity.ENTROPY_OF_FUSION: "J_per_declared_mol_basis_per_K",
        }[quantity]
        if quantity is Quantity.ENTROPY_OF_FUSION:
            assert any(
                notice.reason == "janaf_fusion_transition_off_equilibrium"
                and "3104.968" in notice.band
                for notice in attempt.notices
            )


def test_missing_janaf_fusion_entropy_is_typed_refusal_and_scoring_continues(
    monkeypatch,
) -> None:
    from simulator.battery.generators import janaf

    original_entropy_points = janaf._fusion_entropy_points

    def without_solid_3000_k(table_id):
        points, digest, missing = original_entropy_points(table_id)
        if table_id == "Mg-008":
            points = tuple(
                (temperature, value)
                for temperature, value in points
                if temperature != Decimal("3000")
            )
            missing = tuple(sorted((*missing, Decimal("3000"))))
        return points, digest, missing

    monkeypatch.setattr(janaf, "_fusion_entropy_points", without_solid_3000_k)
    experiment = F.tabulation_experiment()
    observation = F.observation(
        "mg-fusion-missing-entropy-node",
        experiment.experiment_id,
        _fusion_identity(Quantity.ENTROPY_OF_FUSION, Decimal("3000")),
        Decimal("24.804"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )

    residual, _ = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=_context(observation),
        comparison_ids=set(),
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.ENGINE_DOES_NOT_REPORT_FUSION
    assert residual.refusal.detail["reason"] == "JANAF-fusion-endmembers-unavailable"
    assert "3000 K" in residual.refusal.detail["detail"]


@pytest.mark.parametrize(
    "crossing_error",
    (
        "MgO: expected one JANAF cr/l crossing, got []",
        "MgO: expected one JANAF cr/l crossing, got [1, 2]",
        "2963 K is outside the JANAF table range",
    ),
)
def test_janaf_fusion_entropy_returns_prediction_when_crossing_fails(
    monkeypatch, crossing_error: str
) -> None:
    from simulator.battery.generators import janaf

    def unavailable_crossing(*_args, **_kwargs):
        raise ValueError(crossing_error)

    monkeypatch.setattr(
        janaf, "_janaf_fusion_energy_for_pair", unavailable_crossing
    )
    experiment = F.tabulation_experiment()
    observation = F.observation(
        "mg-fusion-crossing-unavailable",
        experiment.experiment_id,
        _fusion_identity(Quantity.ENTROPY_OF_FUSION, Decimal("2963")),
        Decimal("24.804"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )

    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)

    assert attempt.value == Decimal("24.7041")
    assert any(
        notice.reason == f"janaf_fusion_crossing_undetermined: {crossing_error}"
        for notice in attempt.notices
    )

def test_fusion_prediction_flags_an_unstated_solid_polymorph() -> None:
    experiment = F.tabulation_experiment()
    identity = _fusion_identity(
        Quantity.ENTHALPY_OF_FUSION, Decimal("3000")
    )
    identity = replace(
        identity,
        species=replace(
            identity.species, polymorph=State.unknown("source omits polymorph")
        ),
    )
    observation = F.observation(
        "fusion-unknown-polymorph",
        experiment.experiment_id,
        identity,
        Decimal("77.028"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )

    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)

    assert attempt.value == Decimal("77.028")
    assert attempt.authority is Authority.BRIDGE
    assert len(attempt.notices) == 1
    notice = attempt.notices[0]
    assert notice.kind is NoticeKind.DERIVATION_USES_COMPILATION
    assert notice.reason == (
        "fusion_solid_polymorph_unspecified: source does not state a solid "
        "polymorph; predictor used Mg-008 (periclase); alternatives: none "
        "defined at this temperature"
    )
    assert notice.authority is Authority.BRIDGE
    assert notice.band == "JANAF Mg-008/Mg-009 common range [298.15, 4000.0] K"
    residual, _candidate = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=_context(observation),
    )
    assert residual.numeric is not None
    assert residual.status is ResidualStatus.NO_BAND
    assert any(
        item.reason == notice.reason for item in residual.notices
    )


def test_fusion_prediction_refuses_a_polymorph_contradicting_formula() -> None:
    experiment = F.tabulation_experiment()
    identity = _fusion_identity(
        Quantity.ENTHALPY_OF_FUSION, Decimal("3000")
    )
    identity = replace(
        identity,
        species=replace(
            identity.species,
            polymorph=State.of(Polymorph.CLINOENSTATITE),
        ),
    )
    source_polymorph = identity.species.polymorph
    assert source_polymorph is not None and source_polymorph.is_unknown
    assert source_polymorph.reason == (
        "polymorph clinoenstatite does not denote formula MgO"
    )
    observation = F.observation(
        "fusion-rejected-clinoenstatite-on-mgo",
        experiment.experiment_id,
        identity,
        Decimal("77.028"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )

    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
    residual, _candidate = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=_context(observation),
    )

    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert attempt.refusal_detail["reason"] == "fusion-solid-polymorph-rejected"
    assert attempt.refusal_detail["polymorph_reason"] == source_polymorph.reason
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.IDENTITY_MISMATCH


def test_unstated_fusion_polymorph_refuses_incomplete_janaf_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery.generators import janaf

    original_points = janaf._fusion_table_points

    def missing_stable_candidate(table_id: str):
        if table_id == "O-035":
            raise ValueError(
                "O-035: needed JANAF table node row missing or changed"
            )
        return original_points(table_id)

    monkeypatch.setattr(janaf, "_fusion_table_points", missing_stable_candidate)
    experiment = F.tabulation_experiment()
    identity = _fusion_identity(
        Quantity.ENTHALPY_OF_FUSION,
        Decimal("1800"),
        formula="SiO2",
    )
    identity = replace(
        identity,
        species=replace(
            identity.species,
            polymorph=State.unknown("source omits polymorph"),
        ),
    )
    observation = F.observation(
        "fusion-unknown-polymorph-with-corrupt-stable-candidate",
        experiment.experiment_id,
        identity,
        Decimal("9.4"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )

    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
    residual, _candidate = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=_context(observation),
    )

    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.IDENTITY_UNKNOWN
    assert "O-035" in str(attempt.refusal_detail)
    assert "needed JANAF table node row missing or changed" in str(
        attempt.refusal_detail
    )
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.IDENTITY_UNKNOWN
    assert "O-035" in str(residual.refusal.detail)


def test_fusion_stability_uses_all_dictionary_alumina_crystals() -> None:
    from simulator.battery.generators.janaf import _janaf_stable_crystal_set

    stability = _janaf_stable_crystal_set("Al2O3", Decimal("1800"))
    gibbs_by_table = {
        candidate.table_id: candidate.gibbs_kJ_per_mol
        for candidate in (stability.selected, *stability.alternatives)
    }

    assert stability.selected.table_id == "Al-096"
    assert gibbs_by_table == {
        "Al-096": Decimal("-1098.841"),
        "Al-097": Decimal("-1094.690"),
        "Al-098": Decimal("-1091.274"),
        "Al-099": Decimal("-1094.275"),
    }


def test_missing_nonlocal_alumina_candidate_node_refuses_stability_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copy import deepcopy

    import pytest

    from simulator.battery.generators import janaf

    original_loader = janaf.load_table_document

    def missing_alumina_node(path):
        document = original_loader(path)
        if path.name != "Al-097.yaml":
            return document
        incomplete = deepcopy(document)
        incomplete["table"]["values"] = [
            row
            for row in incomplete["table"]["values"]
            if row["temperature"].get("value") != 1000
        ]
        return incomplete

    monkeypatch.setattr(janaf, "load_table_document", missing_alumina_node)
    janaf._fusion_table_points.cache_clear()
    janaf._janaf_pure_solid_table_temperature_range.cache_clear()

    with pytest.raises(
        janaf.JANAFFusionPolymorphUnavailable,
        match="Al-097: needed JANAF table node row missing or changed",
    ):
        janaf._janaf_stable_crystal_set("Al2O3", Decimal("1800"))


def test_fusion_prediction_refuses_a_stated_polymorph_mismatch() -> None:
    experiment = F.tabulation_experiment()
    identity = F.oxide_identity(
        "SiO2",
        Phase.CR,
        T_K=Decimal("1800"),
        per=PerBasis.MOL_SPECIES,
        polymorph=Polymorph.TRIDYMITE.value,
    )
    identity = replace(
        identity,
        quantity=Quantity.ENTHALPY_OF_FUSION,
        per=State.not_applicable("not a fusion basis"),
        standard_pressure_Pa=State.not_applicable("not pressure dependent"),
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
        subtype=State.of("solid_to_liquid"),
    )
    observation = F.observation(
        "fusion-quartz-mismatches-engine-reference",
        experiment.experiment_id,
        identity,
        Decimal("80"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )

    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)

    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert attempt.refusal_detail["reason"] == "fusion-solid-polymorph-not-in-janaf"
    assert "tridymite" in attempt.refusal_detail["detail"]


def test_fusion_notice_names_the_selected_silica_reference_among_alternatives() -> None:
    experiment = F.tabulation_experiment()
    identity = F.oxide_identity(
        "SiO2",
        Phase.CR,
        T_K=Decimal("1800"),
        per=PerBasis.MOL_SPECIES,
        polymorph=Polymorph.CRISTOBALITE_HIGH.value,
    )
    identity = replace(
        identity,
        quantity=Quantity.ENTHALPY_OF_FUSION,
        per=State.not_applicable("not a fusion basis"),
        standard_pressure_Pa=State.not_applicable("not pressure dependent"),
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
        subtype=State.of("solid_to_liquid"),
        species=replace(
            identity.species, polymorph=State.unknown("source omits polymorph")
        ),
    )
    observation = F.observation(
        "fusion-unknown-silica-polymorph",
        experiment.experiment_id,
        identity,
        Decimal("80"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )

    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)

    assert attempt.value is not None
    assert attempt.notices[0].reason.startswith(
        "fusion_solid_polymorph_unspecified: source does not state a solid "
        "polymorph; predictor used O-035 (cristobalite_high); alternatives: "
    )
    assert "quartz" in attempt.notices[0].reason

def test_non_internal_engine_has_typed_fusion_refusal() -> None:
    experiment = F.tabulation_experiment()
    identity = _fusion_identity(
        Quantity.ENTHALPY_OF_FUSION,
        Decimal("3000"),
        formula="SiO2",
        polymorph=Polymorph.QUARTZ,
    )
    observation = F.observation(
        "unsupported-fusion",
        experiment.experiment_id,
        identity,
        Decimal("77"),
    )
    attempt = predict_thermo_attempt(Engine.THERMOENGINE, observation)
    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.ENGINE_DOES_NOT_REPORT_FUSION


def test_janaf_fusion_extrapolation_is_flagged() -> None:
    experiment = F.tabulation_experiment()
    observation = F.observation(
        "extrapolated-fusion",
        experiment.experiment_id,
        _fusion_identity(Quantity.ENTHALPY_OF_FUSION, Decimal("7000")),
        Decimal("77"),
    )
    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
    assert attempt.value is not None
    assert attempt.authority is Authority.EXTRAPOLATED
    assert len(attempt.notices) == 1
    assert attempt.notices[0].kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    assert attempt.notices[0].band


def test_janaf_fusion_source_is_training_relation() -> None:
    from simulator.battery.compilation_tier import _prediction_from_attempt
    from simulator.battery.score import compile_residual

    experiment = F.tabulation_experiment()
    identity = _fusion_identity(
        Quantity.ENTHALPY_OF_FUSION,
        Decimal("3000"),
        formula="SiO2",
        polymorph=Polymorph.QUARTZ,
    )
    observation = F.observation(
        "janaf-fusion-training",
        experiment.experiment_id,
        identity,
        Decimal("77.028"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        source_id="nist-janaf-4th",
    )
    observation = replace(observation, read_from="O-037")
    experiment = F.tabulation_experiment(experiment_id="fusion-ref", work_id="ref-work")
    observation = replace(observation, experiment_id=experiment.experiment_id)
    works = {
        "ref-work": _work_with_table_assets("ref-work", ("O-037",)),
        "janaf-work": _work_with_table_assets("janaf-work", ("O-037", "O-038")),
    }
    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
    prediction = _prediction_from_attempt(
        Engine.INTERNAL_ANALYTICAL, observation, attempt
    )
    assert prediction.coefficient_sources == ("O-037", "O-038")
    residual, _ = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        prediction=prediction,
        context=ScoreContext(
            works=works,
            experiments={experiment.experiment_id: experiment},
            observations={observation.observation_id: observation},
            extract_review={observation.source_id or "": None},
            hostname="test",
        ),
    )
    assert residual.numeric is not None
    assert residual.source_relation is SourceRelation.TRAINING


def _work_with_table_assets(work_id: str, asset_ids: tuple[str, ...]):
    work = F.work(work_id)
    files = tuple(
        SourceFile(
            asset_id=asset_id,
            role=AssetRole.TABLE_CSV,
            path=f"{asset_id}.csv",
            sha256=State.of(f"sha-{asset_id}"),
        )
        for asset_id in asset_ids
    )
    return replace(
        work,
        source_ids=(work_id,),
        source_files=SourceFiles(
            corpus_repo="test",
            corpus_commit=State.of("test"),
            files=files,
        ),
    )


def test_fusion_janaf_tables_do_not_make_other_works_training() -> None:
    from simulator.battery.compilation_tier import _prediction_from_attempt
    from simulator.battery.score import compile_residual

    experiment = F.tabulation_experiment(experiment_id="independent-ref", work_id="other-work")
    identity = _fusion_identity(
        Quantity.ENTHALPY_OF_FUSION,
        Decimal("3000"),
        formula="SiO2",
        polymorph=Polymorph.QUARTZ,
    )
    observation = F.observation(
        "independent-fusion-row",
        experiment.experiment_id,
        identity,
        Decimal("77.028"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="independent-work",
    )
    observation = replace(observation, read_from="independent-table")
    works = {
        "janaf-work": _work_with_table_assets("janaf-work", ("O-037", "O-038")),
        "other-work": _work_with_table_assets("other-work", ("independent-table",)),
    }
    attempt = predict_thermo_attempt(Engine.INTERNAL_ANALYTICAL, observation)
    prediction = _prediction_from_attempt(
        Engine.INTERNAL_ANALYTICAL, observation, attempt
    )
    assert prediction.coefficient_sources == ("O-037", "O-038")
    residual, _ = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        prediction=prediction,
        context=ScoreContext(
            works=works,
            experiments={experiment.experiment_id: experiment},
            observations={observation.observation_id: observation},
            extract_review={observation.source_id or "": None},
            hostname="test",
        ),
    )
    assert residual.numeric is not None
    assert residual.source_relation is SourceRelation.INDEPENDENT
