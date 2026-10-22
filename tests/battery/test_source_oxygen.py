"""d-216 source conditions: printed pressures and independent JANAF arithmetic."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import Engine, EvidenceClass, Phase, Quantity
from simulator.battery.records import Species, State
from simulator.battery.score import _bench_for_score, compile_residual, load_score_context
from simulator.battery.source_oxygen import (
    SourceOxygenCondition,
    SourceOxygenFallback,
    SourceOxygenFallbackReason as FallbackReason,
    resolve_source_oxygen,
)
from simulator.diagnostic_helpers import binary_pot_battery as battery


@pytest.fixture(scope="module")
def source_context():
    return load_score_context(
        Path(__file__).resolve().parents[2],
        sources=("kems-053-stolyarova-1991", "shornikov-1994-mullite-kems", "stolyarova-1996-cao-alumina-silica-kems"),
    )


def _row(context, source, formula="SiO"):
    return next(
        row for row in context.observations.values()
        if row.source_id == source and row.identity.species.formula == formula
        and row.identity.temperature_K.is_value and row.identity.temperature_K.value == 1933
        and (
            source != "kems-053-stolyarova-1991"
            or dict(row.identity.composition.value.components).get("CaO") == Decimal("0.6")
        )
    )


def _resolve(context, row, rows=None):
    experiment = context.experiments[row.experiment_id]
    return resolve_source_oxygen(
        row, context.observations.values() if rows is None else rows,
        _bench_for_score(experiment, context.benches),
    )


def _capture_engine(monkeypatch, row):
    requests = []
    handle = battery.EngineHandle(
        name="openimcc", backend=object(), available=True, unavailable_reason=None,
        takes_fo2=False, supports_intrinsic_fo2=True,
    )

    def equilibrate(_handle, pot, *, temperature_K, po2, **kwargs):
        requests.append(po2)
        return battery.EquilibrateCell(
            pot_id=pot.pot_id, engine="openimcc", temperature_K=temperature_K,
            po2=po2, status="ok", refusal_reason=None, engine_status="ok",
            engine_reason=None, melt_activities={},
            gas_partial_pressures_Pa={row.identity.species.formula: float(row.value.point)},
            liquid_fraction=1.0, wall_s=0.0, cpu_s=0.0, hostname="test",
        )

    monkeypatch.setattr(battery, "equilibrate_cell", equilibrate)
    return requests, {"openimcc": handle}


def _fallback_notice(context, monkeypatch, row, rows):
    requests, handles = _capture_engine(monkeypatch, row)
    residual, _ = compile_residual(
        row, Engine.OPENIMCC, context=context, handles=handles,
        point_observations=tuple(rows),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_OXYGEN_BALANCE_EFFUSION
    return next(
        notice for notice in residual.notices
        if notice.reason.startswith("model assumption: vapour-only zero-storage closure;")
    )


@pytest.mark.parametrize(
    "source,expected_bar",
    (("kems-053-stolyarova-1991", 3.0730622426279393e-12),
     ("shornikov-1994-mullite-kems", 4.6508175e-11)),
)
def test_linked_row_commands_source_oxygen(source_context, monkeypatch, source, expected_bar):
    # 1991 Table Ib pO=6.8e-10 atm; JANAF O-001 interpolates to
    # G(1933)=126.02894 kJ/mol. 1994 Table 2 directly prints pO2=4.59e-11 atm.
    row = _row(source_context, source)
    requests, handles = _capture_engine(monkeypatch, row)
    residual, candidate = compile_residual(
        row, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=tuple(r for r in source_context.observations.values() if r.experiment_id == row.experiment_id),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(expected_bar, rel=1e-10)
    assert residual.numeric is not None
    assert candidate is not None
    condition = candidate.provenance["source_oxygen_condition"]
    assert condition["derived_from"]
    assert condition["derivation"]["inputs"] == condition["derived_from"]
    assert "cell-metal activity as assumed by the source" in condition["derivation"]["relation"]
    assert float(condition["value"]["point"]) / 1e5 == pytest.approx(expected_bar, rel=1e-10)


def test_conditioning_cell_gases_remain_visible_but_are_not_scored(source_context):
    row = _row(source_context, "shornikov-1994-mullite-kems", "MoO2")
    residual, candidate = compile_residual(
        row, Engine.OPENIMCC, context=source_context,
        point_observations=tuple(r for r in source_context.observations.values() if r.experiment_id == row.experiment_id),
    )
    assert residual.numeric is None and candidate is None
    assert residual.refusal.detail["reason"] == "used_as_scoring_condition"
    assert residual.refusal.detail["derivation"]["inputs"]


def test_untyped_1996_alternative_oxygen_columns_keep_closure(source_context, monkeypatch):
    row = _row(source_context, "stolyarova-1996-cao-alumina-silica-kems", "AlO")
    # The primary lineage is untyped; other columns depend on sample gases.
    # Multiple raw columns are not multiple independently eligible conditions.
    assert _resolve(source_context, row).reason is FallbackReason.UNTYPED_LINEAGE
    notice = _fallback_notice(source_context, monkeypatch, row, source_context.observations.values())
    assert "source_oxygen_fallback=lineage_untyped" in notice.reason


def test_no_oxygen_in_supplied_point_records_is_reported(source_context, monkeypatch):
    row = _row(source_context, "kems-053-stolyarova-1991")
    assert _resolve(source_context, row, (row,)).reason is FallbackReason.NO_PRINTED_CONDITION
    notice = _fallback_notice(source_context, monkeypatch, row, (row,))
    assert "source_oxygen_fallback=no_oxygen_condition_printed" in notice.reason


@pytest.mark.parametrize("axis", ("temperature_K", "composition"))
def test_missing_point_axes_report_untyped_linkage(source_context, axis):
    row = _row(source_context, "kems-053-stolyarova-1991")
    row = replace(row, identity=replace(row.identity, **{axis: State.unknown("not printed")}))
    assert _resolve(source_context, row).reason is FallbackReason.UNTYPED_LINEAGE


@pytest.mark.parametrize("axis", ("source", "temperature", "composition"))
def test_source_oxygen_never_matches_another_point(source_context, axis):
    row = _row(source_context, "kems-053-stolyarova-1991")
    if axis == "source":
        row = replace(row, source_id="another-source")
    elif axis == "temperature":
        row = replace(row, identity=replace(row.identity, temperature_K=State.of(Decimal("1934"))))
    else:
        composition = replace(row.identity.composition.value, components=(("CaO", Decimal("0.9")), ("SiO2", Decimal("0.1"))))
        row = replace(row, identity=replace(row.identity, composition=State.of(composition)))
    assert isinstance(_resolve(source_context, row), SourceOxygenFallback)


def test_multiple_measured_oxygen_conditions_are_not_averaged(source_context, monkeypatch):
    row = _row(source_context, "kems-053-stolyarova-1991")
    condition = _resolve(source_context, row)
    original = source_context.observations[next(iter(condition.used_observations))]
    measured = replace(
        original,
        evidence=replace(original.evidence, class_=State.of(EvidenceClass.MEASURED_DIRECT)),
        identity=replace(original.identity, reaction=State.unknown("direct pressure")),
    )
    # Two measured atomic-O rows stay ambiguous. This copy used to be
    # restamped to O2; that pair is now the co-measured condition below.
    other = replace(measured, observation_id="other-printed-oxygen")
    rows = (row, measured, other)
    assert _resolve(source_context, row, rows).reason is FallbackReason.AMBIGUOUS
    notice = _fallback_notice(source_context, monkeypatch, row, rows)
    assert "source_oxygen_fallback=several_candidates_survive" in notice.reason


def test_oxygen_from_any_sample_target_is_excluded(source_context, monkeypatch):
    row = _row(source_context, "kems-053-stolyarova-1991")
    condition = _resolve(source_context, row)
    original = source_context.observations[next(iter(condition.used_observations))]
    magnesium = replace(row, observation_id="sample-Mg", identity=replace(row.identity, species=Species("Mg", Phase.G)))
    oxygen = replace(original, derived_from=(magnesium.observation_id,))
    rows = (row, magnesium, oxygen)
    assert _resolve(source_context, row, rows).reason is FallbackReason.SAMPLE_DERIVED
    notice = _fallback_notice(source_context, monkeypatch, row, rows)
    assert "source_oxygen_fallback=derived_from_scored_sample_species" in notice.reason


def test_typed_source_inconsistency_prevents_conditioning(source_context, monkeypatch):
    from simulator.battery.enums import NoticeKind
    from simulator.battery.records import Notice, SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX

    row = _row(source_context, "kems-053-stolyarova-1991")
    original = source_context.observations[next(iter(_resolve(source_context, row).used_observations))]
    oxygen = replace(original, notices=(*original.notices, Notice(
        kind=NoticeKind.SOURCE_DISAGREEMENT,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason=SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX + " conflicting printed O/O2",
        origin="source table consistency audit",
    )))
    rows = (row, oxygen)
    assert _resolve(source_context, row, rows).reason is FallbackReason.SOURCE_INCONSISTENT
    notice = _fallback_notice(source_context, monkeypatch, row, rows)
    assert "source_oxygen_fallback=source_inconsistent" in notice.reason


def test_printed_command_is_preserved(source_context, monkeypatch):
    # The selector now runs for a printed fO2 as well, so a condition input
    # is refused either way (d-216 fix round 1). This SiO row is not an
    # input, and the printed command is still what the engine receives.
    row = _row(source_context, "kems-053-stolyarova-1991")
    row = replace(row, identity=replace(row.identity, fO2_Pa=State.of(Decimal("1e-5"))))
    requests, handles = _capture_engine(monkeypatch, row)
    compile_residual(
        row, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=tuple(r for r in source_context.observations.values() if r.experiment_id == row.experiment_id),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(1e-10)


def test_measured_oxygen_precedes_cell_ratio(source_context):
    row = _row(source_context, "kems-053-stolyarova-1991")
    original = source_context.observations[next(iter(_resolve(source_context, row).used_observations))]
    measured = replace(
        original, observation_id="directly-measured-O2",
        identity=replace(original.identity, species=Species("O2", Phase.G), reaction=State.unknown("direct measurement")),
        evidence=replace(original.evidence, class_=State.of(EvidenceClass.MEASURED_DIRECT)),
    )
    selected = _resolve(source_context, row, (row, original, measured))
    assert selected.observation.value.point == measured.value.point
    assert selected.used_observations == frozenset({measured.observation_id})


def test_squared_atomic_oxygen_does_not_reuse_atomic_uncertainty(source_context):
    from simulator.battery.enums import UncertaintyKind
    from simulator.battery.records import Uncertainty

    row = _row(source_context, "kems-053-stolyarova-1991")
    original = source_context.observations[next(iter(_resolve(source_context, row).used_observations))]
    oxygen = replace(
        original,
        uncertainty=Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="atomic O uncertainty", value=Decimal("1e-7"), basis="Pa"),
    )
    selected = _resolve(source_context, row, (row, oxygen))
    assert selected.observation.uncertainty.kind is UncertaintyKind.NONE
    assert selected.observation.uncertainty.value is None
    assert oxygen.observation_id in selected.observation.derived_from


@pytest.mark.parametrize("key", ("fO2_Pa", "oxygen_partial_pressure_Pa"))
def test_unit_conversion_of_printed_oxygen_is_still_printed(source_context, key):
    from simulator.battery.migrate import conversion_derivation
    from simulator.battery.records import Located, Value
    from simulator.battery.score import _has_printed_fo2, _source_oxygen_pressure_for_score

    row = _row(source_context, "shornikov-1994-mullite-kems")
    row = replace(
        row,
        point_conditions={key: Located(
            State.of(Value.point_of(Decimal("0.0000046508175"))), locator=row.locator,
            inference=conversion_derivation("atm_to_Pa", Decimal("4.59e-11"), row.locator),
        )},
    )
    assert _has_printed_fo2(row, row.identity)
    assert _source_oxygen_pressure_for_score(row) == Decimal("0.0000046508175")


def test_shornikov_2020_all_printed_oxygen_stamps_are_accepted():
    from simulator.battery.score import _has_printed_fo2

    context = load_score_context(Path(__file__).resolve().parents[2], sources=("shornikov-2020-cao-tio2-melts",))
    rows = [
        row for row in context.observations.values()
        if "fO2_Pa" in (row.point_conditions or {})
        and row.identity.quantity.value is Quantity.P_PARTIAL
    ]
    assert len(rows) == 240
    assert all(_has_printed_fo2(row, row.identity) for row in rows)


def test_printed_point_oxygen_keeps_unmodelled_reactive_cell_notice(source_context, monkeypatch):
    from simulator.battery.enums import CellMaterial, NoticeKind
    from simulator.battery.records import Located, Value
    from simulator.battery.score import predict_with_engine

    row = _row(source_context, "shornikov-1994-mullite-kems")
    row = replace(row, point_conditions={"fO2_Pa": Located(State.of(Value.point_of("4.6508175e-6")), locator=row.locator)})
    experiment = source_context.experiments[row.experiment_id]
    bench = replace(_bench_for_score(experiment, source_context.benches), cell_materials=(Located(State.of(CellMaterial.TA), locator=row.locator),))
    requests = []
    request_type = battery.Po2Request

    def capture_request(**kwargs):
        request = request_type(**kwargs)
        requests.append(request)
        return request

    monkeypatch.setattr(battery, "Po2Request", capture_request)
    monkeypatch.setattr(
        battery, "open_battery_engine",
        lambda name: battery.EngineHandle(name=name, backend=None, available=False, unavailable_reason="test", takes_fo2=False, supports_intrinsic_fo2=True),
    )
    prediction = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment, bench=bench)
    assert any(request.mode == battery.PO2_COMMANDED and request.po2_bar == pytest.approx(4.6508175e-11) for request in requests)
    assert any(notice.kind is NoticeKind.REACTIVE_CELL_NOT_MODELLED for notice in prediction.notices)


# Shornikov's printed pair at 1933 K: O = 2.65e-9 atm, O2 = 4.59e-11 atm.
_ATM_PA = Decimal("101325")
_SHORNIKOV_O_PA = Decimal("2.65e-9") * _ATM_PA
_SHORNIKOV_O2_PA = Decimal("4.59e-11") * _ATM_PA


def _melt_composition():
    from simulator.battery.enums import AmountBasis
    from simulator.battery.records import Composition

    return State.of(Composition(
        basis="printed_mole_fraction",
        components=(("CaO", Decimal("0.6")), ("SiO2", Decimal("0.4"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    ))


def _partial_pressure(observation_id, formula, value_pa, *, evidence=EvidenceClass.MEASURED_DIRECT):
    from simulator.battery.identity import Identity
    from tests.battery.factories import observation

    row = observation(
        observation_id,
        "exp-comeasured",
        Identity(
            quantity=Quantity.P_PARTIAL,
            species=Species(formula, Phase.G),
            temperature_K=State.of(Decimal("1933")),
            composition=_melt_composition(),
        ),
        value_pa,
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-comeasured",
    )
    if evidence is not EvidenceClass.MEASURED_DIRECT:
        row = replace(row, evidence=replace(row.evidence, class_=State.of(evidence)))
    return row


def _pair_rows(o_uncertainty=None, o2_uncertainty=None):
    sample = _partial_pressure("sample-sio", "SiO", Decimal("1"))
    # O is measured_reduced with no derived_from: the factory would invent
    # a parent, which fails measured eligibility.
    atomic = _partial_pressure(
        "printed-o", "O", _SHORNIKOV_O_PA, evidence=EvidenceClass.MEASURED_REDUCED,
    )
    molecular = _partial_pressure("printed-o2", "O2", _SHORNIKOV_O2_PA)
    if o_uncertainty is not None:
        atomic = replace(atomic, uncertainty=o_uncertainty)
    if o2_uncertainty is not None:
        molecular = replace(molecular, uncertainty=o2_uncertainty)
    return sample, (sample, atomic, molecular)


def _selected_pair(o_uncertainty=None, o2_uncertainty=None):
    sample, rows = _pair_rows(o_uncertainty, o2_uncertainty)
    return resolve_source_oxygen(sample, rows, None)


def _printed_uncertainty(verbatim, value=None, basis=None):
    from simulator.battery.enums import UncertaintyKind
    from simulator.battery.records import Uncertainty

    return Uncertainty(kind=UncertaintyKind.PRINTED, verbatim=verbatim, value=value, basis=basis)


def _pressure_percent_verbatim(percent):
    return _printed_uncertainty({
        "kind": "printed",
        "verbatim": (
            "Table 1 pressures have an error no greater than "
            f"{percent}%; no point-specific uncertainty is printed."
        ),
    })


def _structured_percent(percent):
    # The 2020 extract shape: the percent lives in the verbatim mapping,
    # and the prose does not say "pressure".
    return _printed_uncertainty({
        "relative_error_percent": {
            "bound_operator": "<",
            "bound_value": Decimal(percent),
            "units": "%",
        },
        "verbatim": f"error less than {percent}%",
    })


def _consistency_notice(selected):
    return next(
        (
            notice for notice in selected.observation.notices
            if notice.origin == "co-measured oxygen consistency"
        ),
        None,
    )


def _independent_shornikov_1933_dex():
    """Shornikov's 1933 K pair from the JANAF table, not the production helper.

    JANAF O-001 is 1/2 O2 = O, so pO/(1 bar) = K_O * sqrt(pO2/(1 bar)) and
    K_O = exp(-ΔfG/(R T)) with ΔfG converted from kJ/mol to J/mol. Gibbs at
    1933 K is the linear interpolation of the bracketing tabulated points.
    """
    import math

    from simulator.physical_constants import GAS_CONSTANT
    from simulator.reference_data.janaf import load_table_document

    path = (
        Path(__file__).resolve().parents[2]
        / "data/literature/compilations/janaf/tables/O-001.yaml"
    )
    document = load_table_document(path)
    points = []
    for row in document["table"]["values"]:
        temperature = row.get("temperature") if isinstance(row, dict) else None
        gibbs = row.get("formation_gibbs_energy") if isinstance(row, dict) else None
        t_value = temperature.get("value") if isinstance(temperature, dict) else None
        g_value = gibbs.get("value") if isinstance(gibbs, dict) else None
        if t_value is None or g_value is None:
            continue
        points.append((Decimal(str(t_value)), Decimal(str(g_value))))
    points.sort()
    temperature = Decimal("1933")
    left, right = next(
        (earlier, later)
        for earlier, later in zip(points, points[1:])
        if earlier[0] <= temperature <= later[0]
    )
    span = right[0] - left[0]
    gibbs = left[1] + (right[1] - left[1]) * (temperature - left[0]) / span
    constant = math.exp(-float(gibbs) * 1000.0 / (GAS_CONSTANT * float(temperature)))
    # Decimal square, not the production float expression: q = (pO_bar / K_O)^2.
    o_bar = _SHORNIKOV_O_PA / Decimal(100000)
    implied_pa = (o_bar / Decimal(str(constant))) ** 2 * Decimal(100000)
    dex = (_SHORNIKOV_O2_PA / implied_pa).ln() / Decimal(10).ln()
    gap = abs(_SHORNIKOV_O2_PA / implied_pa - 1)
    return dex, gap


def test_comeasured_pair_selects_printed_o2():
    selected = _selected_pair()
    assert isinstance(selected, SourceOxygenCondition)
    assert selected.observation.identity.species.formula == "O2"
    assert selected.observation.value.point == _SHORNIKOV_O2_PA
    relation = selected.observation.derivation.relation
    assert "printed pO2 in Pa (recorded source-unit conversion)" in relation
    assert "cell-metal activity" not in relation
    assert "printed-o" in selected.observation.derivation.inputs
    assert selected.observation.derivation.inputs == selected.observation.derived_from


def test_comeasured_pair_records_o_input_and_independent_dex():
    selected = _selected_pair()
    assert isinstance(selected, SourceOxygenCondition)
    derivation = selected.observation.derivation
    assert "printed-o" in derivation.inputs
    assert "printed-o2" in derivation.inputs
    parameters = dict(derivation.parameters)
    for name in ("pO2_from_O_Pa", "consistency_dex", "K_O", "JANAF_O_001_delta_fG_kJ_mol"):
        assert parameters[name].locator.source_path.endswith("/O-001.yaml")
        assert parameters[name].locator.record
    dex = parameters["consistency_dex"].state.value
    independent, gap = _independent_shornikov_1933_dex()
    assert abs(dex - independent) < Decimal("1e-8")
    assert independent < 0
    assert abs(independent - Decimal("-0.0016")) < Decimal("0.0002")
    assert Decimal("0.003") < gap < Decimal("0.004")


@pytest.mark.parametrize(
    ("o_uncertainty", "o2_uncertainty"),
    (
        (None, _pressure_percent_verbatim("0.0001")),
        (None, _printed_uncertainty("stated relative pressure uncertainty", Decimal("1e-8"), "relative")),
        (None, _structured_percent("0.0001")),
    ),
    ids=("verbatim-0.0001-percent", "relative-1e-8", "structured-0.0001-percent"),
)
def test_comeasured_consistency_notice_fires(o_uncertainty, o2_uncertainty):
    from simulator.battery.enums import NoticeKind, UncertaintyKind
    from simulator.battery.records import SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX

    selected = _selected_pair(o_uncertainty, o2_uncertainty)
    assert isinstance(selected, SourceOxygenCondition)
    notice = _consistency_notice(selected)
    assert notice is not None
    assert notice.kind is NoticeKind.SOURCE_DISAGREEMENT
    assert notice.affected_quantities == (Quantity.P_PARTIAL,)
    assert notice.reason.startswith("co_measured_o_o2_consistency:")
    assert not notice.reason.startswith(SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX)
    if o2_uncertainty is not None:
        assert selected.observation.uncertainty == o2_uncertainty
        assert selected.observation.uncertainty.kind is not UncertaintyKind.NONE


@pytest.mark.parametrize(
    ("o_uncertainty", "o2_uncertainty"),
    (
        (None, None),
        (_pressure_percent_verbatim("8"), _pressure_percent_verbatim("8")),
        (
            None,
            _printed_uncertainty(
                "Table pressures have an error no greater than 0.0001%",
                Decimal("0.5"),
                "relative",
            ),
        ),
        (_printed_uncertainty("stated relative pressure uncertainty", Decimal(1) - Decimal(10) ** Decimal("-0.001"), "relative"), None),
        (None, _structured_percent("10")),
    ),
    ids=(
        "none",
        "verbatim-8-percent",
        "relative-0.5-beats-tiny-verbatim",
        "atomic-squared-envelope",
        "structured-10-percent",
    ),
)
def test_comeasured_consistency_notice_does_not_fire(o_uncertainty, o2_uncertainty):
    selected = _selected_pair(o_uncertainty, o2_uncertainty)
    assert isinstance(selected, SourceOxygenCondition)
    assert _consistency_notice(selected) is None
    names = [name for name, _located in selected.observation.derivation.parameters]
    assert "consistency_dex" in names
    assert "pO2_from_O_Pa" in names


@pytest.mark.parametrize(
    "formulas",
    (("O", "O"), ("O2", "O2"), ("O", "O2", "O2")),
    ids=("two-o", "two-o2", "three"),
)
def test_other_oxygen_multiplicities_stay_ambiguous(formulas):
    sample = _partial_pressure("sample-sio", "SiO", Decimal("1"))
    rows = [sample]
    for index, formula in enumerate(formulas):
        rows.append(_partial_pressure(f"oxygen-{index}", formula, Decimal("1e-6") * (index + 1)))
    selected = resolve_source_oxygen(sample, rows, None)
    assert isinstance(selected, SourceOxygenFallback)
    assert selected.reason is FallbackReason.AMBIGUOUS


@pytest.mark.parametrize(
    "case",
    (
        "rejected-o",
        "rejected-o2",
        "tabulated-o",
        "derived-o2",
        "unlocated-o",
        "both-rejected",
    ),
)
def test_ineligible_oxygen_partner_is_not_the_co_measured_condition(monkeypatch, case):
    from simulator.battery.enums import AdmissionStatus

    sample = _partial_pressure("sample-sio", "SiO", Decimal("1"))
    atomic = _partial_pressure("printed-o", "O", _SHORNIKOV_O_PA)
    molecular = _partial_pressure("printed-o2", "O2", _SHORNIKOV_O2_PA)
    if case == "rejected-o":
        atomic = replace(atomic, admission=replace(atomic.admission, status=AdmissionStatus.REJECTED))
    elif case == "rejected-o2":
        molecular = replace(molecular, admission=replace(molecular.admission, status=AdmissionStatus.REJECTED))
    elif case == "tabulated-o":
        atomic = replace(
            atomic,
            evidence=replace(atomic.evidence, class_=State.of(EvidenceClass.MEASURED_TABULATED)),
        )
    elif case == "derived-o2":
        molecular = replace(molecular, derived_from=("printed-o",))
    elif case == "unlocated-o":
        atomic = replace(atomic, locator=None)
    elif case == "both-rejected":
        atomic = replace(atomic, admission=replace(atomic.admission, status=AdmissionStatus.REJECTED))
        molecular = replace(
            molecular, admission=replace(molecular.admission, status=AdmissionStatus.REJECTED),
        )
    else:
        raise AssertionError(case)

    def fail_janaf(*_args, **_kwargs):
        raise AssertionError("an ineligible partner must not evaluate JANAF K_O")

    monkeypatch.setattr("simulator.battery.generators.janaf._fusion_table_points", fail_janaf)
    selected = resolve_source_oxygen(sample, (sample, atomic, molecular), None)
    assert isinstance(selected, SourceOxygenFallback)
    assert selected.reason is FallbackReason.AMBIGUOUS


@pytest.mark.parametrize(
    ("pressure_atm", "expected_pa"),
    (
        ("6.8e-10", "3.073062242627939E-7"),
        ("2.65e-9", "4.667080363074115E-6"),
    ),
    ids=("W-6.8e-10-atm", "Shornikov-2.65e-9-atm"),
)
def test_atomic_oxygen_conversion_is_bit_identical(pressure_atm, expected_pa):
    """One measured atomic-O row at 1933 K, with no O2 partner.

    The stored pascal values are the atomic branch's Decimal(str(float))
    output. W 6.8e-10 atm is 3.073062242627939e-7 Pa, which is the
    3.0730622426279393e-12 bar figure after the pascal string round-trip.
    Shornikov's 2.65e-9 atm is 4.667080363074115e-6 Pa
    (4.6670803630741150e-11 bar). The literals are those stored numbers.
    """
    from simulator.battery.enums import AmountBasis
    from simulator.battery.identity import Identity
    from simulator.battery.records import Composition
    from tests.battery.factories import observation

    composition = State.of(Composition(
        basis="printed_mole_fraction",
        components=(("CaO", Decimal("0.6")), ("SiO2", Decimal("0.4"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    ))
    temperature = State.of(Decimal("1933"))

    def partial(observation_id, formula, value_pa):
        return observation(
            observation_id,
            "exp-atomic-pin",
            Identity(
                quantity=Quantity.P_PARTIAL,
                species=Species(formula, Phase.G),
                temperature_K=temperature,
                composition=composition,
            ),
            value_pa,
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="synthetic-atomic-pin",
        )

    sample = partial("sample-sio", "SiO", Decimal("1"))
    atomic = partial("printed-o", "O", Decimal(pressure_atm) * Decimal("101325"))
    selected = resolve_source_oxygen(sample, (sample, atomic), None)
    assert isinstance(selected, SourceOxygenCondition)
    assert selected.observation.value.point == Decimal(expected_pa)
    assert selected.observation.identity.species.formula == "O2"
    assert "pO2_Pa=100000*(pO_Pa/100000/K_O)^2" in selected.observation.derivation.relation


def test_printed_fo2_condition_input_is_not_scored(source_context):
    row = _row(source_context, "shornikov-1994-mullite-kems", "MoO2")
    stamped = replace(row, identity=replace(row.identity, fO2_Pa=State.of(Decimal("1e-5"))))
    residual, candidate = compile_residual(
        stamped, Engine.OPENIMCC, context=source_context,
        point_observations=tuple(
            r for r in source_context.observations.values()
            if r.experiment_id == row.experiment_id
        ),
    )
    assert residual.numeric is None and candidate is None
    assert residual.refusal.detail["reason"] == "used_as_scoring_condition"
    assert residual.refusal.detail["derivation"]["inputs"]


def test_stolyarova_sio_pressure_equal_to_the_o2_condition_is_scored(source_context, monkeypatch):
    """A sample gas whose pressure equals the oxygen condition is still scored.

    Real Stolyarova 1933 K, X(CaO)=0.6 SiO. Printing identity fO2 equal to
    that SiO pressure used to refuse the row by numeric coincidence. Setting
    the SiO pressure itself equal to the selected O2 condition does too.
    The row is in neither derivation. A matching number is not an input.
    A synthetic 100000 Pa total completes the printed-command identity.
    The synthetic equal-pressure sample has no published uncertainty.
    """
    from simulator.battery.enums import UncertaintyKind
    from simulator.battery.records import Uncertainty, Value

    row = _row(source_context, "kems-053-stolyarova-1991")
    point_rows = tuple(
        item for item in source_context.observations.values()
        if item.experiment_id == row.experiment_id
    )
    selected = _resolve(source_context, row, point_rows)
    assert isinstance(selected, SourceOxygenCondition)
    assert row.observation_id not in selected.used_observations
    assert row.observation_id not in selected.observation.derivation.inputs

    stamped = replace(row, identity=replace(
        row.identity,
        fO2_Pa=State.of(row.value.point),
        total_pressure_Pa=State.of(Decimal("100000")),
    ))
    requests, handles = _capture_engine(monkeypatch, stamped)
    residual, candidate = compile_residual(
        stamped, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=point_rows,
    )
    assert residual.refusal is None
    assert residual.numeric is not None and candidate is not None
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(float(row.value.point) / 1e5, rel=1e-12)

    equal = replace(
        row,
        value=Value.point_of(selected.observation.value.point),
        uncertainty=Uncertainty(kind=UncertaintyKind.NONE),
    )
    requests, handles = _capture_engine(monkeypatch, equal)
    residual, candidate = compile_residual(
        equal, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=point_rows,
    )
    assert residual.refusal is None
    assert residual.numeric is not None and candidate is not None
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED


@pytest.mark.parametrize("units", ("atm", "Pa"))
@pytest.mark.parametrize("qualified", (False, True), ids=("local-id", "qualified-id"))
def test_migrated_printed_fo2_source_is_excluded_by_scorer(tmp_path, monkeypatch, units, qualified):
    """Real migrated rows/provenance, with complete synthetic KEMS apparatus.

    Ca's typed width comes from its preserved published percent envelope.
    """
    import yaml

    from simulator.battery.migrate import convert_pressure_to_pa
    from simulator.battery.score import ScoreContext, _convert_uncertainty_width, _has_printed_fo2, _printed_fo2_names_observation
    from tests.battery.factories import kems_experiment, migrate_one_extract

    root = Path(__file__).resolve().parents[2]
    path = root / "data/literature/extracts/shornikov-2020-cao-tio2-melts.yaml"
    document = yaml.safe_load(path.read_text())
    source_id = document["source_id"]
    oxygen_rows = [
        row for row in document["species"]["O2"]["observations"]
        if row["locator"].get("table") == "3"
    ]
    assert len(oxygen_rows) == 15
    for row in oxygen_rows:
        node = row["values"]["oxygen_partial_pressure"]
        token = row["observation_id"]
        node["source_observation_id"] = f"{source_id}::{token}" if qualified else token
        if units == "Pa":
            pressure, _trail = convert_pressure_to_pa(Decimal(str(node["value"])), node["units"])
            node.update(value=str(pressure), units="Pa")
    sample = next(
        row for row in document["species"]["Ca"]["observations"]
        if row["observation_id"] == "shornikov_2020_table3_ca_partial_pressure_2278k"
    )
    source_ref = oxygen_rows[0]["values"]["oxygen_partial_pressure"]["source_observation_id"]
    sample["values"]["oxygen_partial_pressure"]["source_observation_id"] = source_ref
    fixture = tmp_path / path.name
    fixture.write_text(yaml.safe_dump(document, sort_keys=False))
    migrated = migrate_one_extract(root, fixture).result
    assert not migrated.registry_issues
    sample_row = migrated.observations[f"{source_id}::{sample['observation_id']}"]
    printed_width = sample_row.uncertainty.verbatim["relative_error_percent"]
    width, basis = _convert_uncertainty_width(
        Quantity.P_PARTIAL, Decimal(str(printed_width["bound_value"])), printed_width["units"],
    )
    sample_row = replace(sample_row, uncertainty=replace(sample_row.uncertainty, value=width, basis=basis))
    prerequisites = kems_experiment()
    context = ScoreContext(
        works=migrated.works,
        experiments={
            key: replace(
                experiment,
                apparatus=prerequisites.apparatus,
                pressure_environment=replace(
                    experiment.pressure_environment,
                    regime=prerequisites.pressure_environment.regime,
                ),
            )
            for key, experiment in migrated.experiments.items()
        },
        observations={**migrated.observations, sample_row.observation_id: sample_row},
        benches=migrated.benches, root=root,
    )
    requests, handles = _capture_engine(monkeypatch, sample_row)
    point_rows = tuple(context.observations.values())
    for original in oxygen_rows:
        row = migrated.observations[f"{source_id}::{original['observation_id']}"]
        assert _has_printed_fo2(row, row.identity)
        inference = row.point_conditions["fO2_Pa"].inference
        assert row.observation_id in inference.inputs
        assert _printed_fo2_names_observation(row)
        assert inference.relation == ("atm_to_Pa" if units == "atm" else "Pa_to_Pa")
        parameters = dict(inference.parameters)
        assert parameters["factor"].state.value == (Decimal("101325") if units == "atm" else Decimal(1))
        assert parameters["original"].state.value == Decimal(str(original["values"]["oxygen_partial_pressure"]["value"]))
        residual, candidate = compile_residual(
            row, Engine.OPENIMCC, context=context, handles=handles,
            point_observations=point_rows,
        )
        assert residual.refusal.detail["reason"] == "used_as_scoring_condition"
        assert residual.refusal.detail["match"] == "printed_fo2_names_observation"
        assert residual.numeric is None and candidate is None
    assert requests == []
    row = sample_row
    assert _has_printed_fo2(row, row.identity)
    assert f"{source_id}::{oxygen_rows[0]['observation_id']}" in row.point_conditions["fO2_Pa"].inference.inputs
    assert not _printed_fo2_names_observation(row)
    residual, candidate = compile_residual(
        row, Engine.OPENIMCC, context=context, handles=handles,
        point_observations=point_rows,
    )
    assert residual.refusal is None
    assert residual.numeric is not None and candidate is not None
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(float(row.identity.fO2_Pa.value) / 1e5)


@pytest.mark.parametrize(
    "source_ref",
    (None, "", 123, "missing-oxygen", "another-source::oxygen",
     "shornikov_2020_table3_ca_partial_pressure_2278k", "not-printed"),
    ids=("unset", "empty", "wrong-type", "missing", "foreign-source", "sample-species", "not-printed"),
)
def test_migrated_printed_fo2_source_reference_validation(tmp_path, source_ref):
    import yaml

    from simulator.battery.enums import RefusalReason
    from simulator.battery.score import _printed_fo2_names_observation
    from tests.battery.factories import migrate_one_extract

    root = Path(__file__).resolve().parents[2]
    path = root / "data/literature/extracts/shornikov-2020-cao-tio2-melts.yaml"
    document = yaml.safe_load(path.read_text())
    row = document["species"]["O2"]["observations"][0]
    node = row["values"]["oxygen_partial_pressure"]
    node["source_observation_id"] = row["observation_id"] if source_ref == "not-printed" else source_ref
    if source_ref == "not-printed":
        node["inferred"] = True
    fixture = tmp_path / path.name
    fixture.write_text(yaml.safe_dump(document, sort_keys=False))
    migrated = migrate_one_extract(root, fixture).result
    target = migrated.observations[f"{document['source_id']}::{row['observation_id']}"]
    assert not _printed_fo2_names_observation(target)
    if source_ref is None:
        assert not migrated.registry_issues
    else:
        assert len(migrated.registry_issues) == 1
        issue = migrated.registry_issues[0]
        assert issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        assert issue.path.endswith(".values.oxygen_partial_pressure.source_observation_id")


def test_comeasured_consistency_notice_reaches_scored_residual(source_context, monkeypatch):
    from simulator.battery.enums import NoticeKind
    from simulator.battery.records import Notice

    row = _row(source_context, "kems-053-stolyarova-1991")
    _sample, pair = _pair_rows(o2_uncertainty=_pressure_percent_verbatim("0.0001"))
    unrelated = Notice(
        kind=NoticeKind.FIGURE_ONLY,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason="not the consistency check",
        origin="unrelated-o2-source-notice",
    )
    oxygen = []
    for item in pair:
        if item.identity.species.formula not in {"O", "O2"}:
            continue
        item = replace(
            item,
            experiment_id=row.experiment_id,
            source_id=row.source_id,
            identity=replace(
                row.identity,
                species=Species(item.identity.species.formula, Phase.G),
                reaction=State.unknown("printed pressure"),
            ),
        )
        if item.identity.species.formula == "O2":
            item = replace(item, notices=(unrelated,))
        oxygen.append(item)
    requests, handles = _capture_engine(monkeypatch, row)
    residual, _candidate = compile_residual(
        row, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=(row, *oxygen),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert any(notice.origin == "co-measured oxygen consistency" for notice in residual.notices)
    assert not any(notice.origin == "unrelated-o2-source-notice" for notice in residual.notices)
    assert any(
        notice.reason.startswith("co_measured_o_o2_consistency:") for notice in residual.notices
    )


def test_comeasured_consistency_notice_reaches_printed_fo2_residual(source_context, monkeypatch):
    """A printed command stays. The pair's consistency notice still arrives.

    Shornikov's 1933 K O2 is 4.59e-11 atm = 4.6508175e-11 bar. The stated
    molecular relative width is 1e-8, which is inside the notice bound.
    """
    from simulator.battery.enums import NoticeKind
    from simulator.battery.records import Notice

    row = _row(source_context, "kems-053-stolyarova-1991")
    _sample, pair = _pair_rows(
        o2_uncertainty=_printed_uncertainty(
            "stated relative pressure uncertainty", Decimal("1e-8"), "relative",
        ),
    )
    unrelated = Notice(
        kind=NoticeKind.FIGURE_ONLY,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason="not the consistency check",
        origin="unrelated-o2-source-notice",
    )
    oxygen = []
    for item in pair:
        if item.identity.species.formula not in {"O", "O2"}:
            continue
        item = replace(
            item,
            experiment_id=row.experiment_id,
            source_id=row.source_id,
            identity=replace(
                row.identity,
                species=Species(item.identity.species.formula, Phase.G),
                reaction=State.unknown("printed pressure"),
            ),
        )
        if item.identity.species.formula == "O2":
            item = replace(item, notices=(unrelated,))
        oxygen.append(item)
    printed = replace(row, identity=replace(row.identity, fO2_Pa=State.of(_SHORNIKOV_O2_PA)))
    requests, handles = _capture_engine(monkeypatch, printed)
    residual, _candidate = compile_residual(
        printed, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=(printed, *oxygen),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(4.6508175e-11, rel=1e-12)
    assert any(notice.origin == "co-measured oxygen consistency" for notice in residual.notices)
    assert not any(notice.origin == "unrelated-o2-source-notice" for notice in residual.notices)
    assert any(
        notice.reason.startswith("co_measured_o_o2_consistency:") for notice in residual.notices
    )
