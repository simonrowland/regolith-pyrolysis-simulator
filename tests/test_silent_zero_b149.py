"""b-149 silent-zero class: shared typed contract + site instrumentation.

Instrument-first: zeros remain zeros; notes make the cause visible.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.condensation import _coerce_alpha_s
from simulator.silent_zero import (
    CATEGORY_PROVEN_ZERO,
    CATEGORY_REFUSE,
    SCHEMA_V1,
    ZeroBecause,
    make_note,
    note_dict,
    notes_payload,
    record_on_host,
    silent_zero_diagnostic,
)


def test_zero_because_closed_enum_values() -> None:
    values = {item.value for item in ZeroBecause}
    # Stable closed set for the class contract.
    assert "proven_empty_inventory" in values
    assert "missing_coefficient" in values
    assert "missing_thermo" in values
    assert "refused_upstream" in values
    assert "out_of_domain_marked" in values
    assert "unparseable_spec" in values
    assert "kernel_ok_empty" in values
    assert "implicit_unit_activity" in values


def test_make_note_and_payload_shape() -> None:
    note = make_note(
        ZeroBecause.KERNEL_OK_EMPTY,
        site="core.kernel_ok_empty",
        field="vapor_pressures_Pa",
        detail="authoritative empty",
        doctrine_category=CATEGORY_PROVEN_ZERO,
    )
    payload = notes_payload([note])
    assert payload["schema"] == SCHEMA_V1
    assert payload["count"] == 1
    assert payload["counts_by_reason"]["kernel_ok_empty"] == 1
    assert payload["counts_by_doctrine_category"]["3"] == 1
    assert payload["notes"][0]["site"] == "core.kernel_ok_empty"


def test_record_on_host_accumulates() -> None:
    host = SimpleNamespace()
    record_on_host(
        host,
        ZeroBecause.REFUSED_UPSTREAM,
        site="evaporation.partial_channel_refusal",
        species="Ti",
        doctrine_category=CATEGORY_REFUSE,
    )
    record_on_host(
        host,
        ZeroBecause.PROVEN_EMPTY_INVENTORY,
        site="evaporation.empty_requested_species_ids",
        doctrine_category=CATEGORY_PROVEN_ZERO,
    )
    diag = silent_zero_diagnostic(host)
    assert diag["count"] == 2
    assert diag["counts_by_reason"]["refused_upstream"] == 1
    assert diag["counts_by_reason"]["proven_empty_inventory"] == 1


def test_coerce_alpha_s_unparseable_emits_note_keeps_zero() -> None:
    evaluation: dict = {}
    value = _coerce_alpha_s(
        "definitely-not-a-float",
        species="SiO",
        T_K=1600.0,
        evaluation_out=evaluation,
    )
    assert value == 0.0
    assert evaluation.get("alpha_s_unparseable") is True
    notes = evaluation.get("silent_zero_notes") or []
    assert len(notes) == 1
    assert notes[0]["zero_because"] == ZeroBecause.UNPARSEABLE_SPEC.value
    assert notes[0]["doctrine_category"] == CATEGORY_REFUSE
    assert notes[0]["species"] == "SiO"


def test_coerce_alpha_s_valid_scalar_has_no_silent_zero_note() -> None:
    evaluation: dict = {}
    value = _coerce_alpha_s(0.42, species="Na", T_K=1400.0, evaluation_out=evaluation)
    assert value == pytest.approx(0.42)
    assert "silent_zero_notes" not in evaluation
    assert evaluation.get("alpha_s_form") == "scalar"


def test_kernel_ok_empty_note_on_diagnostic_surface() -> None:
    """Instance 2: core accepts kernel_ok_empty and emits a typed note."""
    from simulator.chemistry.kernel import ChemistryIntent
    from simulator.core import PyrolysisSimulator
    from simulator.melt_backend.base import EquilibriumResult

    calls: list = []

    def _dispatch(intent, **kwargs):
        calls.append((intent, kwargs))
        return SimpleNamespace(
            status="ok",
            diagnostic={
                "vapor_pressures_Pa": {},
                "vapor_pressures_source": {},
            },
            result={},
        )

    sim = SimpleNamespace(
        melt=SimpleNamespace(
            temperature_C=1600.0,
            ambient_pressure_mbar=0.0,
            campaign=None,
            body="",
            oxygen_reservoir=None,
            melt_fO2_log=None,
        ),
        setpoints={},
        vapor_pressures={},
        _allow_fallback_vapor=False,
        _last_vapor_pressure_diagnostic={},
        _silent_zero_notes=[],
        _dispatch_only=_dispatch,
        _record_degraded_path_engagement=lambda *a, **k: None,
        _vapor_pressure_dispatch_pO2_bar=lambda: 1e-12,
        _vapor_pressure_dispatch_intrinsic_fO2_log=lambda: None,
        _vacuum_floor_bar=lambda: 1e-12,
    )
    if not hasattr(PyrolysisSimulator, "_refresh_vapor_pressures_from_kernel"):
        pytest.skip("kernel refresh helper unavailable")

    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={"Na": 1.0},
        vapor_pressures_source={"Na": "pre"},
        status="ok",
    )
    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)
    assert result.vapor_pressures_Pa == {}
    assert sim._last_vapor_pressure_diagnostic.get(
        "vapor_pressure_zero_reason"
    ) == "kernel_ok_empty"
    notes = sim._last_vapor_pressure_diagnostic.get("silent_zero_notes") or []
    assert any(
        n.get("zero_because") == ZeroBecause.KERNEL_OK_EMPTY.value for n in notes
    )
    assert any(
        n.get("zero_because") == ZeroBecause.KERNEL_OK_EMPTY.value
        for n in (sim._silent_zero_notes or [])
    )
    assert calls and calls[0][0] == ChemistryIntent.VAPOR_PRESSURE


# ---------------------------------------------------------------------------
# Instance 1: evaporation empty-request terminus (proven_empty_inventory)
# ---------------------------------------------------------------------------


def test_evaporation_empty_request_proven_empty_inventory() -> None:
    """Instance 1: empty exact-key request proves empty parent inventory.

    Numeric neutrality: flux stays empty; the note only names the cause.
    """
    from simulator.evaporation import EvaporationMixin
    from simulator.vapour_rail.batch import VapourBatch

    batch = VapourBatch(
        requested_species_ids=frozenset(),
        channels_by_species={},
    )
    sim = SimpleNamespace(
        melt=SimpleNamespace(temperature_C=1600.0),
        setpoints={},
        vapor_pressures={},
        _last_vapor_pressure_diagnostic={},
        _last_vapour_batch_resolve_error={},
        _silent_zero_notes=[],
        _record_degraded_path_engagement=lambda *a, **k: None,
        _resolve_evaporation_vapour_batch=lambda eq, **kw: batch,
    )
    equilibrium = SimpleNamespace(liquid_fraction=1.0, diagnostics={})

    flux = EvaporationMixin._calculate_evaporation(sim, equilibrium)

    # Numeric neutrality: zeros stay zero.
    assert flux.species_kg_hr == {}
    assert flux.total_kg_hr == 0.0

    diagnostic = sim._last_evaporation_flux_diagnostic
    assert diagnostic.get("reason") == (
        "no_volatile_species_or_positive_parent_activity"
    )
    assert diagnostic.get("evaporation_flux_kg_hr") == {}

    notes = diagnostic.get("silent_zero_notes") or []
    assert len(notes) == 1
    note = notes[0]
    assert note["zero_because"] == ZeroBecause.PROVEN_EMPTY_INVENTORY.value
    assert note["doctrine_category"] == CATEGORY_PROVEN_ZERO
    assert note["site"] == "evaporation.empty_requested_species_ids"
    assert note["field"] == "requested_species_ids"
    detail = note.get("detail") or ""
    assert "build_request" in detail
    assert "parent-inventory-only" in detail

    host_notes = sim._silent_zero_notes or []
    assert any(
        n.get("zero_because") == ZeroBecause.PROVEN_EMPTY_INVENTORY.value
        and n.get("site") == "evaporation.empty_requested_species_ids"
        for n in host_notes
    )


# ---------------------------------------------------------------------------
# Instance 4: partial channel refusal (refused_upstream) + dead-write fix
# ---------------------------------------------------------------------------


def _flux_answer(species_id, pressure, flux):
    from simulator.vapour_rail.batch import VapourAnswer

    return VapourAnswer(
        species_id=species_id,
        pressure=pressure,
        selected_runtime_pressure=pressure,
        flux=flux,
        source_label="test",
        formula_id=species_id,
        source_account="process.cleaned_melt",
        solve_group_id="g",
        state_fingerprint="s",
        validation_status="pending_validation",
    )


def test_evaporation_partial_channel_refusal_notes_survive_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Instance 4 / dead-write regression: pre-dispatch refused_upstream notes
    must survive the fresh kernel-diagnostic assignment.

    Numeric neutrality: Na still debits at the kernel flux value and K stays
    absent from the flux map.
    """
    from simulator.evaporation import EvaporationMixin
    from simulator.vapour_rail.batch import (
        FluxEligible,
        FluxRefusal,
        PressureRefusal,
        PressureValue,
        VapourBatch,
    )

    na_answer = _flux_answer(
        "Na", PressureValue(pa=1.5), FluxEligible(alpha_ref="alpha:Na")
    )
    k_answer = _flux_answer(
        "K",
        PressureRefusal(code="test_refusal", detail="K refused for test"),
        FluxRefusal(code="test_refusal", detail="K refused for test"),
    )
    batch = VapourBatch(
        requested_species_ids=frozenset({"Na", "K"}),
        channels_by_species={"Na": na_answer, "K": k_answer},
        flux_active_species_ids=frozenset({"Na"}),
    )
    kernel_marker = note_dict(
        ZeroBecause.KERNEL_OK_EMPTY,
        site="kernel.stub",
        doctrine_category=CATEGORY_PROVEN_ZERO,
    )
    sim = SimpleNamespace(
        melt=SimpleNamespace(temperature_C=1600.0),
        setpoints={},
        vapor_pressures={"metals": {"Na": {"evaporation_alpha": {"value": 0.13}}}},
        _last_vapor_pressure_diagnostic={},
        _last_vapour_batch_resolve_error={},
        _silent_zero_notes=[],
        _record_degraded_path_engagement=lambda *a, **k: None,
        _resolve_evaporation_vapour_batch=lambda eq, **kw: batch,
        _dispatch_only=lambda intent, **kw: SimpleNamespace(
            status="ok",
            diagnostic={
                "evaporation_flux_kg_hr": {"Na": 0.25},
                "silent_zero_notes": [kernel_marker],
            },
            result={},
        ),
        _evaporation_bulk_partial_pressure_pa=lambda sp: 0.0,
        _build_partial_melt_offgassing_diagnostic=lambda *a, **kw: {},
        _freeze_gate_enabled=lambda: False,
    )
    equilibrium = SimpleNamespace(
        liquid_fraction=1.0,
        diagnostics={},
        temperature_C=1600.0,
        vapor_pressures_Pa={"Na": 1.5},
    )
    # Invoked as an explicit class call inside _calculate_evaporation.
    monkeypatch.setattr(
        EvaporationMixin,
        "_evaporation_flux_control_inputs",
        lambda self, eq, **kw: ({}, {}),
    )

    flux = EvaporationMixin._calculate_evaporation(sim, equilibrium)

    # Numeric neutrality: kernel flux values pass through unchanged; the
    # refused K channel stays out of the flux map.
    assert flux.species_kg_hr == {"Na": 0.25}
    assert flux.total_kg_hr == pytest.approx(0.25)
    assert "K" not in flux.species_kg_hr

    diagnostic = sim._last_evaporation_flux_diagnostic
    assert diagnostic.get("evaporation_flux_kg_hr") == {"Na": 0.25}

    # Dead-write regression: BOTH writes survive in the final diagnostic —
    # the kernel-produced marker note and the pre-dispatch refusal note.
    notes = diagnostic.get("silent_zero_notes") or []
    assert any(
        n.get("zero_because") == ZeroBecause.KERNEL_OK_EMPTY.value
        and n.get("site") == "kernel.stub"
        for n in notes
    )
    refusal_notes = [
        n
        for n in notes
        if n.get("zero_because") == ZeroBecause.REFUSED_UPSTREAM.value
    ]
    assert len(refusal_notes) == 1
    refusal = refusal_notes[0]
    assert refusal["doctrine_category"] == CATEGORY_REFUSE
    assert refusal["site"] == "evaporation.partial_channel_refusal"
    assert refusal["species"] == "K"
    assert refusal["field"] == "batch_channel_state"

    # Host list carries the refusal note as well.
    assert any(
        n.get("zero_because") == ZeroBecause.REFUSED_UPSTREAM.value
        and n.get("species") == "K"
        for n in (sim._silent_zero_notes or [])
    )


# ---------------------------------------------------------------------------
# Instance 3: analytical equilibrium omit causes (category re-tag)
# ---------------------------------------------------------------------------


def test_internal_analytical_feo_below_threshold_vs_missing_activity() -> None:
    """Instance 3: FeO below-threshold is cat-3 proven zero, not cat-1.

    One call covers both arms: Fe (parent FeO, authoritative activity 0)
    must be tagged proven_below_threshold; Mn (parent Mn2O3 unknown to
    melt_oxide_activity, zero inventory) must stay missing_activity.

    Numeric neutrality: both species stay omitted from vapor_pressures_Pa.
    """
    from simulator.equilibrium import EquilibriumMixin

    sim = SimpleNamespace(
        melt=SimpleNamespace(
            temperature_C=1426.85,  # 1700 K: Fe and Mn condensed rail
            p_total_mbar=0.0,
            composition_wt_pct=lambda: {"SiO2": 100.0},
            oxygen_reservoir=None,
            melt_fO2_log=-9.0,
        ),
        _headspace_transport_pO2_bar=lambda: 1e-12,
        _vacuum_floor_bar=lambda: 1e-12,
        atom_ledger=None,
        _ELLINGHAM_THERMO=EquilibriumMixin._ELLINGHAM_THERMO,
        vapor_pressures={
            "metals": {
                "Fe": {
                    "parent_oxide": "FeO",
                    "fit_target": "pure_component_psat",
                    "antoine": {"A": 10.0, "B": 20000.0, "C": 0.0},
                },
                "Mn": {
                    "parent_oxide": "Mn2O3",
                    "fit_target": "pure_component_psat",
                    "antoine": {"A": 9.0, "B": 18000.0, "C": 0.0},
                },
            },
            "oxide_vapors": {},
        },
        _silent_zero_notes=[],
    )

    result = EquilibriumMixin._internal_analytical_equilibrium(sim)

    assert result.status == "ok"
    # Numeric neutrality: omitted stays omitted (silent zero made visible).
    assert result.vapor_pressures_Pa == {}
    assert "Fe" not in result.vapor_pressures_Pa
    assert "Mn" not in result.vapor_pressures_Pa

    notes = result.diagnostics.get("silent_zero_notes") or []
    fe_notes = [
        n
        for n in notes
        if n.get("species") == "Fe"
        and n.get("zero_because") == ZeroBecause.PROVEN_BELOW_THRESHOLD.value
    ]
    assert len(fe_notes) == 1
    assert fe_notes[0]["doctrine_category"] == CATEGORY_PROVEN_ZERO
    assert fe_notes[0]["field"] == "a_FeO_authoritative"
    assert fe_notes[0]["site"] == "equilibrium.internal_analytical"

    mn_notes = [
        n
        for n in notes
        if n.get("species") == "Mn"
        and n.get("zero_because") == ZeroBecause.MISSING_ACTIVITY.value
    ]
    assert len(mn_notes) == 1
    assert mn_notes[0]["doctrine_category"] == CATEGORY_REFUSE
    assert mn_notes[0]["field"] == "oxide_activity"
    assert mn_notes[0]["site"] == "equilibrium.internal_analytical"

    host_notes = sim._silent_zero_notes or []
    assert any(
        n.get("zero_because") == ZeroBecause.PROVEN_BELOW_THRESHOLD.value
        and n.get("species") == "Fe"
        and n.get("doctrine_category") == CATEGORY_PROVEN_ZERO
        for n in host_notes
    )
    assert any(
        n.get("zero_because") == ZeroBecause.MISSING_ACTIVITY.value
        and n.get("species") == "Mn"
        and n.get("doctrine_category") == CATEGORY_REFUSE
        for n in host_notes
    )


# ---------------------------------------------------------------------------
# Instance 6: C7 Ca transport missing/non-finite inputs + bounded notes
# ---------------------------------------------------------------------------


def _c7_sim(ca_entry: dict) -> SimpleNamespace:
    """SimpleNamespace host for ExtractionMixin._c7_transport_extent_mol."""
    from types import MethodType

    from simulator.extraction import ExtractionMixin

    sim = SimpleNamespace(
        melt=SimpleNamespace(
            melt_surface_area_m2=0.2,
            p_total_mbar=0.05,
            stir_state=SimpleNamespace(axial=1.0),
            stir_factor=1.0,
        ),
        vapor_pressures={"metals": {"Ca": ca_entry}},
        setpoints={},
        _resolve_condensation_carrier_gas=lambda: "N2",
        overhead=SimpleNamespace(headspace_temperature_K=1000.0),
        overhead_model=SimpleNamespace(pipe_diameter_m=0.12),
        _evaporation_bulk_partial_pressure_pa=lambda sp: 0.0,
        _silent_zero_notes=[],
    )
    # Static/instance helpers resolved on the mixin class, not the host.
    sim._c7_float = ExtractionMixin._c7_float
    sim._c7_clamp = ExtractionMixin._c7_clamp
    sim._c7_bool = ExtractionMixin._c7_bool
    sim._c7_knob_diag = MethodType(ExtractionMixin._c7_knob_diag, sim)
    return sim


def test_c7_ca_missing_coefficient_and_thermo_notes_bounded() -> None:
    """Instance 6(a) + SC-50: missing alpha/Antoine keep zero extent, emit
    cat-1 notes on the returned diagnostic, and the per-call
    ``_last_c7_ca_silent_zero_notes`` surface stays bounded (replaced)."""
    from simulator.extraction import ExtractionMixin

    sim = _c7_sim({})
    cfg = {"hold_time_h": 1.0, "p_total_mbar": 0.05}

    extent_mol, diagnostic = ExtractionMixin._c7_transport_extent_mol(
        sim, cfg, ca_per_extent=1.0
    )

    # Numeric neutrality: zeros stay zero.
    assert extent_mol == 0.0
    assert diagnostic["c7_ca_alpha_intrinsic"] == 0.0
    assert diagnostic["c7_ca_p_sat_pa"] == 0.0

    notes = diagnostic.get("silent_zero_notes") or []
    by_reason = {n.get("zero_because"): n for n in notes}
    assert ZeroBecause.MISSING_COEFFICIENT.value in by_reason
    assert ZeroBecause.MISSING_THERMO.value in by_reason
    coefficient = by_reason[ZeroBecause.MISSING_COEFFICIENT.value]
    assert coefficient["doctrine_category"] == CATEGORY_REFUSE
    assert coefficient["site"] == "extraction.c7_ca"
    assert coefficient["species"] == "Ca"
    assert coefficient["field"] == "evaporation_alpha"
    thermo = by_reason[ZeroBecause.MISSING_THERMO.value]
    assert thermo["doctrine_category"] == CATEGORY_REFUSE
    assert thermo["site"] == "extraction.c7_ca"
    assert thermo["species"] == "Ca"
    assert thermo["field"] == "pure_component_antoine"

    assert sim._last_c7_ca_silent_zero_notes == {
        "silent_zero_notes": [dict(n) for n in notes]
    }

    # Second call: the per-call surface is replaced, not accumulated.
    extent_again, diagnostic_again = ExtractionMixin._c7_transport_extent_mol(
        sim, cfg, ca_per_extent=1.0
    )
    assert extent_again == 0.0
    assert len(sim._last_c7_ca_silent_zero_notes["silent_zero_notes"]) == 2
    assert len(diagnostic_again.get("silent_zero_notes") or []) == 2

    # Host list is the accumulate-everything history (2 notes per call).
    assert len(sim._silent_zero_notes) == 4


def test_c7_ca_nonfinite_alpha_emits_unparseable_spec() -> None:
    """Instance 6(b): non-finite evaporation_alpha coerces to 0.0 and is
    tagged unparseable_spec with a 'non-finite' detail (not silently)."""
    from simulator.extraction import ExtractionMixin

    sim = _c7_sim(
        {
            "evaporation_alpha": {"value": float("nan")},
            "pure_component_antoine": {"A": 10.0, "B": 15000.0, "C": 0.0},
        }
    )
    cfg = {"hold_time_h": 1.0, "p_total_mbar": 0.05}

    extent_mol, diagnostic = ExtractionMixin._c7_transport_extent_mol(
        sim, cfg, ca_per_extent=1.0
    )

    # Numeric neutrality: the coerced zero is unchanged.
    assert extent_mol == 0.0
    assert diagnostic["c7_ca_alpha_intrinsic"] == 0.0

    notes = diagnostic.get("silent_zero_notes") or []
    assert len(notes) == 1
    note = notes[0]
    assert note["zero_because"] == ZeroBecause.UNPARSEABLE_SPEC.value
    assert note["doctrine_category"] == CATEGORY_REFUSE
    assert note["site"] == "extraction.c7_ca"
    assert note["species"] == "Ca"
    assert note["field"] == "evaporation_alpha"
    assert "non-finite" in (note.get("detail") or "")


def test_c7_ca_valid_inputs_emit_no_notes() -> None:
    """Instance 6(c): valid alpha + Antoine produce no notes, an empty
    bounded surface, and pass the inputs through numerically unchanged."""
    from simulator.extraction import ExtractionMixin

    sim = _c7_sim(
        {
            "evaporation_alpha": {"value": 0.5},
            "pure_component_antoine": {"A": 10.0, "B": 15000.0, "C": 0.0},
        }
    )
    cfg = {"hold_time_h": 1.0, "p_total_mbar": 0.05}

    extent_mol, diagnostic = ExtractionMixin._c7_transport_extent_mol(
        sim, cfg, ca_per_extent=1.0
    )

    assert "silent_zero_notes" not in diagnostic
    assert sim._last_c7_ca_silent_zero_notes == {"silent_zero_notes": []}
    assert sim._silent_zero_notes == []

    # Numeric neutrality: supplied coefficients flow through unchanged.
    assert diagnostic["c7_ca_alpha_intrinsic"] == 0.5
    hold_temp_K = 1200.0 + 273.15
    expected_p_sat = 10.0 ** (10.0 - 15000.0 / (hold_temp_K + 0.0))
    assert diagnostic["c7_ca_p_sat_pa"] == pytest.approx(expected_p_sat)
    assert extent_mol >= 0.0


# ---------------------------------------------------------------------------
# Instance 7: pure-component implicit unit activity (vapour_rail _make_live)
# ---------------------------------------------------------------------------


def test_pure_component_unit_activity_note_on_answer_extra() -> None:
    """Instance 7: activity-independent evaluator (exponent 0) uses a=1.0;
    the answer's extra must type it as implicit_unit_activity while the
    pressure equals the evaluator value unchanged."""
    from simulator.vapour_rail.batch import (
        FLUX_ACTIVATION_EPOCH_RG_MANIFEST,
        FluxActivationContext,
        PressureValue,
    )
    from simulator.vapour_rail.request import (
        RequestRule,
        VapourResolveState,
        resolve_vapour_batch,
    )

    class _PureComponentEval:
        # No activity_exponent attribute: pure-component (exponent 0) path.
        def evaluate(self, temperature_K, *, source_activity=1.0, pO2_bar=None):
            return SimpleNamespace(pressure_pa=12.5)

    catalog_species = {
        "Na": SimpleNamespace(
            species_id="Na",
            evaluator=_PureComponentEval(),
            vaporisation_coefficients=SimpleNamespace(
                evaporation_alpha={"value": 1.0}
            ),
        )
    }
    rule = RequestRule(
        species_id="Na",
        source_account="process.cleaned_melt",
        parent_species_ids=frozenset({"Na2O"}),
        required_source_atoms=frozenset({"Na", "O"}),
        solve_group_id="na_test",
        applicability_predicate="applicable",
        request_rule_kind="source_inventory_present",
        origin="catalog",
        formula_id="Na",
        has_pressure_evaluator=True,
        has_alpha=True,
        has_route=True,
        has_formula=True,
        validation_status="pending_validation",
    )
    batch = resolve_vapour_batch(
        rules=(rule,),
        ledger_snapshot={"process.cleaned_melt": {"Na2O": 1.0}},
        state=VapourResolveState(temperature_K=1500.0),
        catalog_species=catalog_species,
        flux_activation_context=FluxActivationContext(
            epoch=FLUX_ACTIVATION_EPOCH_RG_MANIFEST
        ),
    )

    answer = batch.channel("Na")
    assert not answer.is_refused

    # Numeric neutrality: pressure is the evaluator value, unscaled.
    assert isinstance(answer.pressure, PressureValue)
    assert answer.pressure.pa == pytest.approx(12.5)

    extra = dict(answer.extra)
    assert extra.get("source_activity") == 1.0
    assert extra.get("source_activity_origin") == "pure_component_unit"
    notes = extra.get("silent_zero_notes") or []
    assert len(notes) == 1
    note = notes[0]
    assert note["zero_because"] == ZeroBecause.IMPLICIT_UNIT_ACTIVITY.value
    assert note["doctrine_category"] == CATEGORY_PROVEN_ZERO
    assert note["site"] == "vapour_rail.request._make_live"
    assert note["species"] == "Na"
    assert note["field"] == "source_activity"
