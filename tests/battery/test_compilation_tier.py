"""Compilation tier: series cells as points, engine thermo, separate headline.

Printed anchors are JANAF Na-014 Na2O(l) at 2200 K: ΔfG° = −3.886 kJ/mol,
log10 Kf = 0.092. The Ellingham segment at that temperature is
4 Na(g)+O2 → 2 Na2O(l), dG = −7.4056 kJ/mol O2.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

from simulator.battery.compilation_tier import (
    _parse_product,
    compilation_family,
    compilation_series_points,
    predict_thermo_attempt,
)
from simulator.battery.enums import (
    AdmissionStatus,
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
from simulator.battery.records import Species, State, Uncertainty, Value
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


def test_series_cells_are_points_and_intervals_are_not_midpoints() -> None:
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
    points = compilation_series_points(series, "compilations-janaf/janaf-Na.yaml")
    assert [point.observation_id for point in points] == [
        "na2o-series#t2200v-3.886",
        "na2o-series#t2100v-10",
    ]
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
    assert [point.observation_id for point in compilation_series_points(swapped, "compilations-janaf/janaf-Na.yaml")] == [
        "na2o-series#t2100v-10",
        "na2o-series#t2200v-3.886",
    ]
    assert points[0].value.point == _PRINTED_DFG
    assert points[1].value.point == Decimal("-10")
    assert points[0].identity.temperature_K.value == _T
    # Not the mean of −3.886 and −10.
    assert points[0].value.point != ( _PRINTED_DFG + Decimal("-10") ) / 2
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
        ResidualStatus.MATCH,
        ResidualStatus.MATCH,
        ResidualStatus.MATCH,
        ResidualStatus.MATCH,
        ResidualStatus.MATCH,
        ResidualStatus.MATCH,
        ResidualStatus.MISMATCH,
        ResidualStatus.MISMATCH,
        ResidualStatus.MISMATCH,
        ResidualStatus.MISMATCH,
        ResidualStatus.MISMATCH,
        ResidualStatus.REFUSED,
    ]
    expected_band = DecisionBand(
        Decimal("6"),
        QUANTITY_UNITS[Quantity.DELTA_FG],
        "compilations-janaf/delta_fG residual distribution: 2x median absolute deviation; derived_n=11",
    )
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
            expected_band,
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
            )
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
    assert compilation_series_points(transition, "compilations-janaf/x.yaml") == (
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
    assert compilation_series_points(empirical, "kems.yaml") == (empirical,)


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
    assert decision_band_for(Quantity.DELTA_FG, SourceRelation.INDEPENDENT) is not None
    for quantity in (
        Quantity.CP,
        Quantity.S,
        Quantity.DELTA_FH,
        Quantity.H_MINUS_H298,
    ):
        assert decision_band_for(quantity, SourceRelation.INDEPENDENT) is None
        assert decision_band_for(quantity, SourceRelation.SAME_INPUT) is None


def test_score_store_uses_printed_cell_then_family_mad_for_unknown_relation(
    monkeypatch,
) -> None:
    from simulator.battery import score as score_module
    from simulator.battery.records import Derivation, Execution, Locator
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
        assert residual.numeric.decision_band is not None
        assert residual.numeric.decision_band.value == Decimal("2")
        assert residual.numeric.decision_band.rule.startswith(
            "compilations-usgs-b1452/cp residual distribution"
        )
        assert residual.status is (
            ResidualStatus.MISMATCH if Decimal(offset) == Decimal("10") else ResidualStatus.MATCH
        )
    printed = by_reference[(observations[10].observation_id, Engine.INTERNAL_ANALYTICAL.value)]
    assert printed.source_relation is SourceRelation.UNKNOWN
    assert printed.numeric is not None
    assert printed.numeric.decision_band is not None
    assert printed.numeric.decision_band.value == Decimal("0.1")
    assert printed.numeric.decision_band.rule == "source-printed per-cell uncertainty"
    assert printed.status is ResidualStatus.MISMATCH
    vaporock = by_reference[(observations[0].observation_id, Engine.VAPOROCK.value)]
    assert vaporock.numeric is not None
    assert vaporock.numeric.decision_band is not None
    assert vaporock.numeric.decision_band.value == Decimal("20")
    assert "derived_n=10" in vaporock.numeric.decision_band.rule
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
    assert stratum["band_kind"] == "printed"
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
    assert no_band["no_band_reason"] == "derived_band_insufficient_n"
    assert no_band["band_derived_n"] == 1
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
    assert census_stratum["band_value"] == "5.0"
    assert census_stratum["match_count"] == 6


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
                "compilation/cp residual distribution: 2x median absolute deviation; derived_n=10",
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
                Decimal("2"), "Pa", "compilation/p_sat residual distribution; derived_n=10"
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

    identity = _gas_thermo(temperature_K=Decimal("1200"))
    expected = {
        Quantity.CP: (Decimal("20.787"), Decimal("0.01")),
        Quantity.S: (Decimal("189.284"), Decimal("0.3")),
        Quantity.H_MINUS_H298: (Decimal("18.746"), Decimal("0.001")),
    }
    attempts = {}
    for quantity, (printed, tolerance) in expected.items():
        attempt = predict_thermo_attempt(
            Engine.VAPOROCK,
            F.observation(
                f"kgas-{quantity.value}",
                "exp-1",
                replace(identity, quantity=quantity),
                printed,
            ),
        )
        assert attempt.refusal_reason is None
        assert attempt.value is not None
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

    reference = F.observation(
        "kgas-cp-janaf",
        "exp-1",
        replace(identity, quantity=Quantity.CP),
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
    assert all(
        f"{name}=" in residual.execution.call_evidence for name in fields
    )
    assert any(
        notice.kind is NoticeKind.DERIVATION_USES_COMPILATION
        for notice in residual.notices
    )
    assert "implementation-fidelity" in residual.execution.call_evidence
    assert decision_band_for(Quantity.CP, SourceRelation.SAME_INPUT) is None


def test_vaporock_gas_refuses_missing_species_and_out_of_interval() -> None:
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

    outside = predict_thermo_attempt(
        Engine.VAPOROCK,
        F.observation(
            "potassium-below-table",
            "exp-1",
            _gas_thermo(temperature_K=Decimal("1000")),
            Decimal("0"),
        ),
    )
    assert outside.value is None
    assert outside.refusal_reason is RefusalReason.UNSUPPORTED
    assert outside.refusal_detail["reason"] == "vaporock-temperature-outside-janaf-row"


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
    assert residual.status is ResidualStatus.MATCH
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
    assert independent.status is ResidualStatus.MATCH
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
        "RMS residual | band value | band kind | band derived n | no-band reason | band-kind-specific matches / tail-in | "
        "band-kind-specific mismatches / tail-out | n no band | n same-source |"
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
    assert "|residual| ≤ band width." in compilation_report
    assert summary_cells[5] == "kJ/mol"
    assert summary_cells[6] == "1"
    assert summary_cells[7] == str(residual.numeric.value)
    assert summary_cells[17:] == ["legacy_fallback match=1", "legacy_fallback mismatch=0", "0", "1"]
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


def test_unknown_relation_uses_legacy_delta_fg_fallback() -> None:
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
    assert residual.status is ResidualStatus.MATCH
    assert residual.numeric is not None
    assert residual.numeric.decision_band is not None
    assert residual.numeric.decision_band.value == Decimal("1.0")
    assert residual.numeric.decision_band.rule.startswith("LEGACY fallback:")


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
    assert "| thermochemistry | internal-analytical | 0 | 0 | 0 |" in report
    assert "| internal-analytical | 1 |" in report
    census = report.split("## Refusal census", 1)[1]
    assert "internal-analytical" not in census or "0 |" in report


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
