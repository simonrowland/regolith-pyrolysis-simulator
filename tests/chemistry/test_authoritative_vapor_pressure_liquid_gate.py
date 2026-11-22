from __future__ import annotations

import types

import pytest

from engines.domain_reason import OutOfDomainReason
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentResult
from simulator.core import PyrolysisSimulator
from simulator.evaporation import EvaporationFluxRefusal, EvaporationMixin
from simulator.melt_backend.base import EquilibriumResult
from simulator.vapour_rail.batch import (
    FLUX_ACTIVATION_EPOCH_PRE_RG,
    FluxEligible,
    FluxActivationContext,
    PressureValue,
    VapourAnswer,
    VapourBatch,
)
from simulator.vapour_rail.request import (
    RequestRule,
    VapourResolveState,
    resolve_vapour_batch,
)


def _resolved_liquid_gate_batch(*, parent_inventory_mol: float):
    rule = RequestRule(
        species_id='K',
        source_account='process.cleaned_melt',
        parent_species_ids=frozenset({'K2O'}),
        required_source_atoms=frozenset({'K', 'O'}),
        solve_group_id='test:K',
        applicability_predicate='applicable',
        request_rule_kind='source_inventory_present',
        origin='catalog',
        formula_id='K',
        has_pressure_evaluator=False,
        has_alpha=False,
        has_route=False,
    )
    ledger = {
        'process.cleaned_melt': (
            {'K2O': parent_inventory_mol} if parent_inventory_mol > 0.0 else {}
        )
    }
    return resolve_vapour_batch(
        rules=(rule,),
        ledger_snapshot=ledger,
        state=VapourResolveState(temperature_K=1873.15),
        catalog_species={},
        flux_activation_context=FluxActivationContext(
            epoch=FLUX_ACTIVATION_EPOCH_PRE_RG
        ),
    )


def _with_empty_vapour_batch(sim):
    sim.setpoints = {}
    sim.vapor_pressures = {}
    sim._last_vapour_batch_resolve_error = {}
    empty_batch = _resolved_liquid_gate_batch(parent_inventory_mol=0.0)
    sim._resolve_evaporation_vapour_batch = (
        lambda equilibrium, temperature_K, effective_pressure_source: empty_batch
    )
    sim._resolve_evaporation_batch_flux_state = types.MethodType(
        PyrolysisSimulator._resolve_evaporation_batch_flux_state,
        sim,
    )
    return sim


def _sim_with_vapor_dispatch(
    vapor_pressures: dict[str, float],
    vapor_sources: dict[str, str] | None = None,
    warnings: tuple[str, ...] = (),
    status: str = 'ok',
):
    calls = []
    source_by_species = vapor_sources or {
        species: 'builtin_authoritative'
        for species in vapor_pressures
    }

    def _dispatch_only(intent, **kwargs):
        calls.append((intent, kwargs))
        return IntentResult(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            status=status,
            diagnostic={
                'vapor_pressures_Pa': dict(vapor_pressures),
                'vapor_pressures_source': dict(source_by_species),
            },
            warnings=warnings,
        )

    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=1600.0),
        liquid_feo_domain_at=lambda _T_K: None,
        _allow_fallback_vapor=False,
        _last_backend_diagnostics={},
        _commanded_pO2_bar=lambda: 1e-9,
        # #94 LIVE-PO2-SWEEP: kernel refresh now reads the shared vapor
        # transport-pO2 snapshot helper instead of commanded pO2 directly.
        _vapor_pressure_dispatch_pO2_bar=lambda: 1e-9,
        _compute_intrinsic_melt_fO2=lambda: -9.0,
        _melt_metal_saturation_fO2_floor_log=lambda _temperature_K: None,
        _melt_redox_gate_authority_pin_for_current_hour=lambda: (False, None),
        _dispatch_only=_dispatch_only,
        _kernel_vapor_pressure_source=(
            PyrolysisSimulator._kernel_vapor_pressure_source
        ),
        _vapor_pressure_values_agree=(
            PyrolysisSimulator._vapor_pressure_values_agree
        ),
    )
    return _with_empty_vapour_batch(sim), calls


def test_kernel_warning_survives_core_refresh_in_retained_diagnostic():
    notice = (
        'antoine_species_skipped: species=Na '
        'site=builtin_vapor_pressure.metal_antoine '
        'reason=missing_antoine_A temperature_K=1873.150'
    )
    sim, calls = _sim_with_vapor_dispatch({'Na': 12.5}, warnings=(notice,))
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]
    assert calls[0][1]["control_inputs"]["liquid_fraction"] == 1.0
    assert result.vapor_pressures_Pa == {'Na': 12.5}
    assert sim._last_vapor_pressure_diagnostic[
        'kernel_vapor_pressure_warnings'
    ] == [notice]


def test_activity_source_is_reported_when_kernel_fallback_retains_pressure():
    from simulator.runner import _vapor_pressure_source_report

    activity_source = {
        'rung': 'published_gamma',
        'source': 'Sossi & Fegley 2018 Table 2',
        'x': 0.25,
        'reason': 'alphaMELTS supplied no oxide-component activity',
    }
    sim, calls = _sim_with_vapor_dispatch({}, status='unavailable')
    sim._allow_fallback_vapor = True
    sim._last_backend_diagnostics = {
        'activity_source_by_species': {'Na': activity_source}
    }
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={'Na': 3.0},
        vapor_pressures_source={'Na': 'builtin_authoritative'},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]
    assert result.vapor_pressures_Pa == {'Na': 3.0}
    assert sim._last_backend_diagnostics['activity_source_by_species'] == {
        'Na': activity_source
    }
    report = _vapor_pressure_source_report(sim)
    assert report['species'] == {'Na': 'builtin_authoritative'}
    assert report['activity_sources'] == {'Na': activity_source}


def test_activity_source_is_reported_when_sub_400k_refresh_retains_pressure():
    from simulator.runner import _vapor_pressure_source_report

    activity_source = {
        'rung': 'published_gamma',
        'source': 'Sossi & Fegley 2018 Table 2',
        'x': 0.25,
        'reason': 'alphaMELTS supplied no oxide-component activity',
    }
    sim, calls = _sim_with_vapor_dispatch({})
    sim.melt.temperature_C = 100.0
    sim._allow_fallback_vapor = True
    sim._last_backend_diagnostics = {
        'activity_source_by_species': {'Na': activity_source}
    }
    result = EquilibriumResult(
        temperature_C=100.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={'Na': 3.0},
        vapor_pressures_source={'Na': 'builtin_authoritative'},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    assert calls == []
    assert result.vapor_pressures_Pa == {'Na': 3.0}
    assert sim._last_backend_diagnostics['activity_source_by_species'] == {
        'Na': activity_source
    }
    report = _vapor_pressure_source_report(sim)
    assert report['species'] == {'Na': 'builtin_authoritative'}
    assert report['activity_sources'] == {'Na': activity_source}


def test_kernel_replacement_drops_backend_activity_source_from_report():
    from simulator.runner import _vapor_pressure_source_report

    activity_source = {
        'rung': 'published_gamma',
        'source': 'Sossi & Fegley 2018 Table 2',
        'x': 0.25,
        'reason': 'alphaMELTS supplied no oxide-component activity',
    }
    sim, calls = _sim_with_vapor_dispatch({'Na': 12.5})
    sim._last_backend_diagnostics = {
        'activity_source_by_species': {'Na': activity_source}
    }
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={'Na': 3.0},
        vapor_pressures_source={'Na': 'backend_source'},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]
    assert result.vapor_pressures_Pa == {'Na': 12.5}
    assert 'activity_source_by_species' not in sim._last_backend_diagnostics
    assert _vapor_pressure_source_report(sim)['activity_sources'] == {}


def test_authoritative_vapor_pressure_no_liquid_gate_zeroes_evaporation():
    sim, calls = _sim_with_vapor_dispatch({'Na': 10.0})
    sim._last_vapour_batch = object()
    sim._last_vapour_batch_report = {'stale': True}
    sim._last_vapour_batch_flux_overlay = {'stale': True}
    sim._last_vapour_batch_resolve_error = {'stale': True}
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        phases_present=['olivine'],
        phase_masses_kg={'olivine': 1.0},
        liquid_fraction=0.0,
        vapor_pressures_Pa={'Na': 10.0},
        vapor_pressures_source={'Na': 'backend_spurious'},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)
    flux = PyrolysisSimulator._calculate_evaporation(sim, result)

    assert calls == []
    assert result.vapor_pressures_Pa == {}
    assert result.vapor_pressures_source == {}
    assert sim._last_vapor_pressure_diagnostic['vapor_pressure_zero_reason'] == (
        'no_liquid_phase'
    )
    assert flux.species_kg_hr == {}
    assert flux.total_kg_hr == 0.0
    assert sim._last_vapour_batch is None
    assert sim._last_vapour_batch_report is None
    assert sim._last_vapour_batch_flux_overlay == {}
    assert sim._last_vapour_batch_resolve_error == {}


def test_internal_analytical_refresh_uses_admitted_freeze_gate_curve() -> None:
    from engines.builtin.vapor_pressure import (
        _openimcc_vapour_phase_basis,
        openimcc_binding_selected_by_inputs,
    )

    sim, calls = _sim_with_vapor_dispatch({'Na': 12.5})
    sim.melt.temperature_C = 1800.0 - 273.15
    curve = {
        'source': 'gate_liquid_fraction',
        'solidus_T_C': 1200.0,
        'liquidus_T_C': 1400.0,
        'path': ((1200.0, 0.0), (1400.0, 1.0)),
    }
    record = {
        'curve': curve,
        'origin': {},
        'repair_notices': [],
        'disposition': 'live',
    }
    sim._vapor_pressure_freeze_gate_phase_fraction = types.MethodType(
        EvaporationMixin._vapor_pressure_freeze_gate_phase_fraction,
        sim,
    )
    sim._register_freeze_gate_liquid_fraction_providers = lambda: None
    sim._melt_redox_liquidus_gate_record = lambda: record
    sim._melt_redox_gate_record_is_admitted = lambda _record: True
    sim._interpolate_freeze_gate_curve = (
        EvaporationMixin._interpolate_freeze_gate_curve
    )
    result = EquilibriumResult(
        temperature_C=sim.melt.temperature_C,
        pressure_bar=1e-6,
        liquid_fraction=None,
        phase_assemblage_available=False,
        vapor_pressures_Pa={},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    controls = calls[0][1]['control_inputs']
    assert controls['liquid_fraction'] == 1.0
    assert controls['phase_basis_source'] == (
        'freeze_gate_authority:gate_liquid_fraction'
    )
    assert controls['phase_basis_continuation'] == 'above_sampled_liquidus'
    phase_basis = _openimcc_vapour_phase_basis(controls['liquid_fraction'])
    assert phase_basis == 'liquid'
    assert openimcc_binding_selected_by_inputs(
        temperature_K=1800.0,
        controls=controls,
        quantity_class='vapour',
        phase_basis=phase_basis,
    ) is True


def test_internal_analytical_refresh_treats_freeze_gate_fallback_as_missing() -> None:
    from engines.builtin.vapor_pressure import (
        _openimcc_vapour_phase_basis,
        openimcc_binding_selected_by_inputs,
    )

    sim, calls = _sim_with_vapor_dispatch({'Na': 12.5})
    sim.melt.temperature_C = 1800.0 - 273.15
    fallback_record = {
        'curve': {
            'source': 'kress91_liquid_calibration_floor',
            'solidus_T_C': 1390.0,
            'liquidus_T_C': 1391.0,
            'path': ((1390.0, 0.0), (1391.0, 1.0)),
        },
        'origin': {},
        'repair_notices': [],
        'disposition': 'live',
    }
    sim._vapor_pressure_freeze_gate_phase_fraction = types.MethodType(
        EvaporationMixin._vapor_pressure_freeze_gate_phase_fraction,
        sim,
    )
    sim._register_freeze_gate_liquid_fraction_providers = lambda: None
    sim._melt_redox_liquidus_gate_record = lambda: fallback_record
    sim._melt_redox_gate_record_is_admitted = lambda _record: True
    result = EquilibriumResult(
        temperature_C=sim.melt.temperature_C,
        pressure_bar=1e-6,
        liquid_fraction=None,
        phase_assemblage_available=False,
        vapor_pressures_Pa={},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    controls = calls[0][1]['control_inputs']
    assert controls['liquid_fraction'] is None
    assert controls['phase_basis_source'] == (
        'freeze_gate_authority_fallback_or_projected'
    )
    phase_basis = _openimcc_vapour_phase_basis(controls['liquid_fraction'])
    assert phase_basis == 'missing'
    assert openimcc_binding_selected_by_inputs(
        temperature_K=1800.0,
        controls=controls,
        quantity_class='vapour',
        phase_basis=phase_basis,
    ) is False


@pytest.mark.parametrize(
    ('temperature_C', 'expected_fraction'),
    (
        (1100.0, 0.0),
        (1200.0, 0.0),
        (1250.0, 0.1),
        (1300.0, 0.2),
        (1350.0, 0.6000000000000001),
        (1400.0, 1.0),
        (1450.0, 1.0),
    ),
    ids=(
        'below-solidus',
        'solidus-knot',
        'first-segment',
        'middle-knot',
        'second-segment',
        'liquidus-knot',
        'above-liquidus',
    ),
)
def test_freeze_gate_curve_interpolation_matches_parent_golden(
    temperature_C,
    expected_fraction,
) -> None:
    # Parent 602581452 arithmetic: the 1250 C segment is 0 + .2 * .5 = .1;
    # the 1350 C segment is .2 + (1.0 - .2) * .5 = 0.6000000000000001.
    # Exact knots and the legacy solidus/liquidus boundary branches return
    # their sampled endpoints.
    curve = {
        'solidus_T_C': 1200.0,
        'liquidus_T_C': 1400.0,
        'path': (
            (1200.0, 0.0),
            (1300.0, 0.2),
            (1400.0, 1.0),
        ),
    }

    assert EvaporationMixin._interpolate_freeze_gate_curve(
        curve,
        temperature_C,
    ) == expected_fraction


@pytest.mark.parametrize(
    (
        'temperature_C',
        'path',
        'expected_fraction',
        'expected_continuation',
        'expected_detail',
    ),
    (
        (
            1526.85,
            ((1200.0, 0.0), (1400.0, 1.0)),
            1.0,
            'above_sampled_liquidus',
            None,
        ),
        (
            1526.85,
            ((1200.0, 0.0), (1400.0, 0.99)),
            None,
            None,
            {
                'code': 'above_range_liquid_fraction_invalid',
                'temperature_C': 1526.85,
                'last_knot': (1400.0, 0.99),
            },
        ),
        (
            1100.0,
            ((1200.0, 0.0), (1400.0, 1.0)),
            0.0,
            'below_sampled_range_solid_bearing',
            None,
        ),
        (
            1100.0,
            ((1200.0, 1.0), (1400.0, 1.0)),
            None,
            None,
            {
                'code': 'below_range_liquid_fraction_invalid',
                'temperature_C': 1100.0,
                'first_knot': (1200.0, 1.0),
            },
        ),
    ),
    ids=(
        'above-sampled-liquidus',
        'above-incomplete-samples',
        'below-sampled-range-solid-bearing',
        'below-fully-liquid-samples',
    ),
)
def test_freeze_gate_phase_fraction_requires_sampled_curve_support(
    temperature_C,
    path,
    expected_fraction,
    expected_continuation,
    expected_detail,
) -> None:
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=temperature_C),
        _register_freeze_gate_liquid_fraction_providers=lambda: None,
        _melt_redox_gate_authority_pin_for_current_hour=lambda: (False, None),
        _melt_redox_liquidus_gate_record=lambda: {
            'curve': {
                'source': 'gate_liquid_fraction',
                'path': path,
            },
            'origin': {},
            'repair_notices': [],
            'disposition': 'live',
            'provider_outcome': 'produced',
        },
        _melt_redox_gate_record_is_admitted=lambda _record: True,
    )

    phase_fraction, source, continuation, projection_notice, phase_detail = (
        EvaporationMixin._vapor_pressure_freeze_gate_phase_fraction(sim)
    )

    assert phase_fraction == expected_fraction
    assert source == (
        'freeze_gate_authority:gate_liquid_fraction'
        if expected_fraction is not None
        else 'freeze_gate_authority_missing_or_invalid'
    )
    assert continuation == expected_continuation
    assert projection_notice is None
    if expected_detail is None:
        assert phase_detail is None
    else:
        assert phase_detail is not None
        assert phase_detail['code'] == expected_detail['code']
        for field, value in expected_detail.items():
            assert phase_detail[field] == value
        assert 'diagnostic' not in phase_detail


def test_freeze_gate_phase_fraction_without_resolver_marks_evidence_absent() -> None:
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=1300.0),
        _last_melt_redox_liquidus_gate_diagnostic={
            'provider': 'previous-cell',
            'reason': 'stale evidence',
        },
    )

    phase_detail = EvaporationMixin._vapor_pressure_freeze_gate_phase_fraction(
        sim
    )[4]

    assert phase_detail == {
        'code': 'gate_resolver_missing',
    }


def test_freeze_gate_phase_fraction_admits_projected_magemin_with_notice() -> None:
    projected_source = (
        'gate_liquid_fraction:fallback:magemin-shadow:composition_projected'
    )
    projection_notice = {
        'kind': 'composition_projected',
        'reason': 'composition_projected',
        'authority': 'extrapolated',
        'certified_band': {
            'engine': 'magemin',
            'database': 'ig',
            'bulk_components': ['SiO2'],
        },
        'dropped_components': [
            {'component': 'MnO', 'mass_fraction': 0.002},
        ],
    }
    source_repair = {
        'type': 'typed_notice',
        'kind': 'freeze_gate_curve_source_replaced',
        'site': 'SR19',
        'failed_source': 'gate_liquid_fraction:fallback:magemin-shadow',
        'replacement_source': projected_source,
    }
    fallback_provider = types.SimpleNamespace(
        capability_profile=lambda: types.SimpleNamespace(
            provider_id='magemin-shadow',
        ),
    )
    record = {
        'curve': {
            'source': projected_source,
            'composition_projected_notice': projection_notice,
            'original_bulk_refusal': {
                'provider': 'magemin',
                'status': 'out_of_domain',
                'reasons': [
                    OutOfDomainReason.COMPOSITION_PROJECTED.value,
                ],
            },
            'path': ((1200.0, 0.0), (1400.0, 1.0)),
        },
        'repair_notices': [source_repair],
        'disposition': 'live',
        'provider_outcome': 'stable_refusal',
    }
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=1300.0),
        _liquid_fraction_provider='magemin',
        _chem_registry=types.SimpleNamespace(
            fallback_for=lambda _intent: fallback_provider,
        ),
        _register_freeze_gate_liquid_fraction_providers=lambda: None,
        _melt_redox_liquidus_gate_record=lambda: record,
        _melt_redox_gate_record_is_admitted=lambda _record: True,
        _melt_redox_gate_authority_pin_for_current_hour=lambda: (False, None),
    )

    phase_fraction, source, continuation, phase_notice, phase_detail = (
        EvaporationMixin._vapor_pressure_freeze_gate_phase_fraction(sim)
    )

    assert phase_fraction == 0.5
    assert source == f'freeze_gate_authority:{projected_source}'
    assert continuation is None
    assert phase_notice == projection_notice
    assert phase_detail is None

    record['repair_notices'].append({
        'type': 'typed_notice',
        'kind': 'freeze_gate_curve_repaired',
    })
    phase_fraction, source, continuation, phase_notice, phase_detail = (
        EvaporationMixin._vapor_pressure_freeze_gate_phase_fraction(sim)
    )
    assert phase_fraction is None
    assert source == 'freeze_gate_authority_missing_or_invalid'
    assert continuation is None
    assert phase_notice is None
    assert phase_detail["code"] == "gate_curve_repaired"


def test_refused_freeze_gate_phase_lookup_keeps_vapor_pressure_refresh() -> None:
    sim, calls = _sim_with_vapor_dispatch({'Na': 12.5})
    sim.melt.temperature_C = 1800.0 - 273.15
    sim._vapor_pressure_freeze_gate_phase_fraction = types.MethodType(
        EvaporationMixin._vapor_pressure_freeze_gate_phase_fraction,
        sim,
    )
    sim._register_freeze_gate_liquid_fraction_providers = lambda: None

    def refuse_phase_authority():
        raise EvaporationFluxRefusal(
            'no admitted phase authority',
            {'reason_refused': 'freeze_gate_no_liquidus_authority'},
        )

    sim._melt_redox_liquidus_gate_record = refuse_phase_authority
    sim._melt_redox_gate_record_is_admitted = lambda _record: True
    result = types.SimpleNamespace(
        temperature_C=sim.melt.temperature_C,
        pressure_bar=1e-6,
        liquid_fraction=None,
        vapor_pressures_Pa={},
        vapor_pressures_source={},
        warnings=[],
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]
    controls = calls[0][1]['control_inputs']
    assert controls['liquid_fraction'] is None
    assert controls['phase_basis_source'] == 'freeze_gate_authority_refused'
    phase_detail = sim._last_vapor_pressure_diagnostic['phase_basis_detail']
    assert phase_detail['code'] == 'no admitted phase authority'
    assert phase_detail['diagnostic']['reason_refused'] == (
        'freeze_gate_no_liquidus_authority'
    )
    assert result.vapor_pressures_Pa == {'Na': 12.5}


def test_internal_analytical_adapter_uses_real_magemin_gate_fallback(
    request,
) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        _internal_analytical_vapor_pressure_adapter,
        _new_internal_analytical_core,
        composition_kg_and_mol,
    )
    from simulator.melt_backend import openimcc_bridge
    from tests.engine_availability import magemin_binary_available

    if not magemin_binary_available():
        pytest.skip('MAGEMin binary unavailable for the real gate contract')

    core = _new_internal_analytical_core()
    assert core._liquid_fraction_provider == 'magemin'
    core._register_freeze_gate_liquid_fraction_providers()
    gate_provider = core._chem_registry.fallback_for(
        ChemistryIntent.GATE_LIQUID_FRACTION
    )
    assert gate_provider is not None
    magemin = gate_provider._ensure_backend()
    if magemin is None or not magemin.is_available():
        pytest.skip('MAGEMin backend unavailable after initialization')
    request.addfinalizer(magemin.close)
    gate_curve_rebuilds_before_refresh = int(
        getattr(core, '_freeze_gate_cache_rebuild_count', 0)
    )

    _, composition_mol = composition_kg_and_mol({
        'SiO2': 49.0,
        'Al2O3': 14.0,
        'FeO': 10.0,
        'MgO': 9.0,
        'CaO': 11.0,
        'Na2O': 3.0,
        'K2O': 1.0,
        'TiO2': 2.0,
        'Cr2O3': 1.0,
    })
    output = _internal_analytical_vapor_pressure_adapter(
        core=core,
        temperature_C=1800.0 - 273.15,
        pressure_bar=1.0,
        composition_kg=None,
        composition_mol=composition_mol,
        fO2_log=-9.0,
        po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=1.0e-9),
        include_diagnostic_shadows=False,
    )

    authority = core._last_high_t_melt_activity
    assert output.status == 'ok'
    assert authority['vapour_channel_phase_basis'] == 'liquid'
    assert authority['phase_basis_source'] == (
        'freeze_gate_authority:gate_liquid_fraction:fallback:magemin-shadow'
    )
    assert authority['vapour_channel_switch_K'] == (
        openimcc_bridge._temperature_domain_K()[0]
    )
    gate_curve_rebuilds_after_refresh = int(
        core._freeze_gate_cache_rebuild_count
    )
    assert gate_curve_rebuilds_after_refresh == gate_curve_rebuilds_before_refresh + 1
    core._melt_redox_liquidus_gate_record()
    assert core._freeze_gate_cache_rebuild_count == gate_curve_rebuilds_after_refresh


def test_internal_analytical_runner_uses_real_equilibrium_phase_result(
    monkeypatch,
    request,
) -> None:
    from pathlib import Path

    import yaml

    from engines.builtin.vapor_pressure import openimcc_binding_selected_by_inputs
    from simulator.melt_backend import openimcc_bridge
    from simulator.melt_backend.base import InternalAnalyticalBackend
    from tests.engine_availability import magemin_binary_available

    if not magemin_binary_available():
        pytest.skip('MAGEMin binary unavailable for the real runner contract')

    repo_root = Path(__file__).resolve().parents[2]
    with (repo_root / 'data' / 'feedstocks.yaml').open(
        encoding='utf-8'
    ) as stream:
        feedstocks = yaml.safe_load(stream)
    with (repo_root / 'data' / 'vapor_pressures.yaml').open(
        encoding='utf-8'
    ) as stream:
        vapor_pressures = yaml.safe_load(stream)
    sim = PyrolysisSimulator(
        InternalAnalyticalBackend(),
        {'campaigns': {}},
        {'lunar_mare_low_ti': feedstocks['lunar_mare_low_ti']},
        vapor_pressures,
    )
    sim.load_batch('lunar_mare_low_ti', mass_kg=1000.0)
    sim.melt.temperature_C = 1800.0 - 273.15
    sim.melt.p_total_mbar = 1.0e-3
    sim.melt.pO2_mbar = 1.0e-6
    assert sim._liquid_fraction_provider == 'magemin'
    sim._register_freeze_gate_liquid_fraction_providers()
    gate_provider = sim._chem_registry.fallback_for(
        ChemistryIntent.GATE_LIQUID_FRACTION
    )
    assert gate_provider is not None
    magemin = gate_provider._ensure_backend()
    if magemin is None or not magemin.is_available():
        pytest.skip('MAGEMin backend unavailable after initialization')
    request.addfinalizer(magemin.close)

    sequence_before_first_cell = sim._melt_redox_liquidus_gate_diagnostic_seq
    result = sim._get_equilibrium(run_post_equilibrium_sulfsat=False)

    assert isinstance(result, EquilibriumResult)
    assert result.liquid_fraction is None
    assert result.phase_assemblage_available is False
    authority = sim._last_high_t_melt_activity
    assert authority['vapour_channel_phase_basis'] == 'liquid'
    assert authority['phase_basis_source'] == (
        'freeze_gate_authority:'
        'gate_liquid_fraction:fallback:magemin-shadow:composition_projected'
    )
    phase_notice = authority['phase_basis_notice']
    assert phase_notice['authority'] == 'extrapolated'
    assert phase_notice['certified_band']['engine'] == 'magemin'
    assert phase_notice['certified_band']['database']
    assert {
        row['component'] for row in phase_notice['dropped_components']
    } == {'MnO', 'P2O5'}
    assert all(
        isinstance(row['mass_fraction'], float)
        and 0.0 < row['mass_fraction'] <= 1.0
        for row in phase_notice['dropped_components']
    )
    sio_band_notice = next(
        notice for notice in authority['notices']
        if notice['code'] == 'openimcc_liquid_sio_below_1950K_band'
    )
    assert sio_band_notice['phase_basis_notice'] == phase_notice
    assert authority['vapour_channel_switch_K'] == (
        openimcc_bridge._temperature_domain_K()[0]
    )
    assert openimcc_binding_selected_by_inputs(
        temperature_K=1800.0,
        controls={},
        quantity_class='vapour',
        phase_basis=authority['vapour_channel_phase_basis'],
    )

    first_cell_gate_diagnostic = dict(
        sim._last_melt_redox_liquidus_gate_diagnostic
    )
    first_cell_sequence = sim._melt_redox_liquidus_gate_diagnostic_seq
    assert first_cell_sequence > sequence_before_first_cell
    assert first_cell_gate_diagnostic

    def refuse_second_cell_registration():
        raise ImportError('gate provider unavailable for second cell')

    monkeypatch.setattr(
        sim,
        '_register_freeze_gate_liquid_fraction_providers',
        refuse_second_cell_registration,
    )
    second_result = EquilibriumResult(
        temperature_C=sim.melt.temperature_C,
        pressure_bar=1.0e-6,
        liquid_fraction=None,
        phase_assemblage_available=False,
        vapor_pressures_Pa={},
    )
    sim._refresh_vapor_pressures_from_kernel(second_result)

    second_phase_detail = sim._last_vapor_pressure_diagnostic[
        'phase_basis_detail'
    ]
    assert second_phase_detail['code'] == 'freeze_gate_provider_import_failed'
    assert 'diagnostic' not in second_phase_detail
    assert (
        sim._melt_redox_liquidus_gate_diagnostic_seq == first_cell_sequence
    )
    assert (
        sim._last_melt_redox_liquidus_gate_diagnostic
        == first_cell_gate_diagnostic
    )
    snapshot = sim._make_snapshot()
    assert (
        snapshot.liquid_fraction_gate['redox']
        == first_cell_gate_diagnostic
    )


def test_internal_analytical_real_kress_floor_stays_missing_phase() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        _internal_analytical_vapor_pressure_adapter,
        _new_internal_analytical_core,
        composition_kg_and_mol,
    )

    core = _new_internal_analytical_core()
    _, composition_mol = composition_kg_and_mol({
        'SiO2': 81.0,
        'Na2O': 19.0,
    })
    output = _internal_analytical_vapor_pressure_adapter(
        core=core,
        temperature_C=1800.0 - 273.15,
        pressure_bar=1.0,
        composition_kg=None,
        composition_mol=composition_mol,
        fO2_log=-9.0,
        po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=1.0e-9),
        include_diagnostic_shadows=False,
    )

    authority = core._last_high_t_melt_activity
    gate_record = core._freeze_gate_liquid_fraction_cache
    assert output.status == 'ok'
    assert gate_record['curve']['source'] == 'kress91_liquid_calibration_floor'
    assert gate_record['provider_outcome'].value == 'stable_refusal'
    assert authority['vapour_channel_phase_basis'] == 'missing'
    assert authority['vapour_channel_switch_K'] == 1950.0
    assert authority['phase_basis_source'] == (
        'freeze_gate_authority_fallback_or_projected'
    )
    assert any(
        notice['code'] == 'openimcc_vapour_phase_signal_missing'
        for notice in authority['notices']
    )


@pytest.mark.parametrize('temperature_C', [0.0, 1000.0, 1600.0])
def test_active_liquid_empty_vapor_pressures_fail_loud(temperature_C):
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=temperature_C),
        setpoints={},
        vapor_pressures={},
        _last_vapor_pressure_diagnostic={
            'vapor_pressure_zero_reason': 'kernel_ok_empty',
        },
        _last_vapour_batch_resolve_error={},
    )
    refused_batch = _resolved_liquid_gate_batch(parent_inventory_mol=1.0)
    sim._resolve_evaporation_vapour_batch = (
        lambda equilibrium, temperature_K, effective_pressure_source: refused_batch
    )
    sim._resolve_evaporation_batch_flux_state = types.MethodType(
        PyrolysisSimulator._resolve_evaporation_batch_flux_state,
        sim,
    )
    result = EquilibriumResult(
        temperature_C=temperature_C,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={},
        status='ok',
    )

    with pytest.raises(EvaporationFluxRefusal) as exc_info:
        PyrolysisSimulator._calculate_evaporation(sim, result)
    assert exc_info.value.reason == 'vapour_batch_no_debiting_pressure_outcome'
    assert refused_batch.requested_species_ids == frozenset({'K'})
    assert refused_batch.channel('K').is_refused


def test_kernel_ok_empty_with_proven_empty_request_is_physical_zero():
    sim, calls = _sim_with_vapor_dispatch({})
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        phases_present=['liq'],
        phase_masses_kg={'liq': 1.0},
        liquid_fraction=1.0,
        vapor_pressures_Pa={'Na': 3.0},
        vapor_pressures_source={'Na': 'backend_pre_kernel'},
        status='ok',
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)
    flux = PyrolysisSimulator._calculate_evaporation(sim, result)

    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]
    assert result.vapor_pressures_Pa == {}
    assert sim._last_vapor_pressure_diagnostic['vapor_pressure_zero_reason'] == (
        'kernel_ok_empty'
    )
    assert flux.species_kg_hr == {}
    assert flux.total_kg_hr == 0.0
    assert sim._last_vapour_batch.requested_species_ids == frozenset()
    assert sim._last_evaporation_flux_diagnostic['reason'] == (
        'no_volatile_species_or_positive_parent_activity'
    )


def test_no_volatile_species_allows_active_liquid_zero_evaporation():
    sim = _with_empty_vapour_batch(
        types.SimpleNamespace(melt=types.SimpleNamespace(temperature_C=1600.0))
    )
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={},
        status='ok',
        diagnostics={'vapor_pressure_zero_reason': 'no_volatile_species'},
    )

    flux = PyrolysisSimulator._calculate_evaporation(sim, result)

    assert flux.species_kg_hr == {}
    assert flux.total_kg_hr == 0.0


def test_subthreshold_empty_vapor_pressures_remain_physical_zero():
    sim = _with_empty_vapour_batch(
        types.SimpleNamespace(melt=types.SimpleNamespace(temperature_C=500.0))
    )
    result = EquilibriumResult(
        temperature_C=500.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={},
        status='ok',
    )

    flux = PyrolysisSimulator._calculate_evaporation(sim, result)

    assert flux.species_kg_hr == {}
    assert flux.total_kg_hr == 0.0


def test_pre_rg_subthreshold_effective_source_zero_requires_answered_batch():
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=500.0),
        setpoints={},
        vapor_pressures={},
        _last_vapor_pressure_diagnostic={
            'vapor_pressure_zero_reason': 'kernel_ok_empty',
        },
        _last_vapour_batch_resolve_error={},
    )
    pressure = PressureValue(pa=1.0)
    answered_batch = VapourBatch(
        requested_species_ids=frozenset({'K'}),
        channels_by_species={
            'K': VapourAnswer(
                species_id='K',
                pressure=pressure,
                selected_runtime_pressure=pressure,
                flux=FluxEligible(alpha_ref='test:K'),
                source_label='test',
                formula_id='K',
                source_account='process.cleaned_melt',
                solve_group_id='test:K',
                state_fingerprint='test',
                validation_status='validated',
            )
        },
        flux_active_species_ids=frozenset(),
    )
    sim._resolve_evaporation_vapour_batch = (
        lambda equilibrium, temperature_K, effective_pressure_source: answered_batch
    )
    sim._resolve_evaporation_batch_flux_state = types.MethodType(
        PyrolysisSimulator._resolve_evaporation_batch_flux_state,
        sim,
    )
    result = EquilibriumResult(
        temperature_C=500.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={},
        status='ok',
    )

    flux = PyrolysisSimulator._calculate_evaporation(sim, result)

    assert flux.species_kg_hr == {}
    assert flux.total_kg_hr == 0.0
    assert sim._last_vapour_batch_flux_overlay[
        'effective_pressure_zero_reason'
    ] == 'pre_rg_backend_below_effective_pressure_threshold'


def test_authoritative_vapor_pressure_liquid_present_dispatch_unchanged():
    sim, calls = _sim_with_vapor_dispatch({'Na': 12.5})
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        phases_present=['liq', 'olivine'],
        phase_masses_kg={'liq': 0.25, 'olivine': 0.75},
        liquid_fraction=0.25,
        vapor_pressures_Pa={'Na': 3.0},
        vapor_pressures_source={'Na': 'backend_pre_kernel'},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]
    assert "simulation_hour" not in calls[0][1]['control_inputs']
    assert result.vapor_pressures_Pa == {'Na': pytest.approx(12.5)}
    assert result.vapor_pressures_source == {'Na': 'builtin_authoritative'}


def test_authoritative_vapor_pressure_invalid_liquid_fraction_still_fails_loud():
    sim, calls = _sim_with_vapor_dispatch({'Na': 12.5})
    result = types.SimpleNamespace(
        liquid_fraction=float('nan'),
        vapor_pressures_Pa={'Na': 3.0},
        vapor_pressures_source={'Na': 'backend_pre_kernel'},
    )

    with pytest.raises(RuntimeError, match='liquid_fraction_invalid'):
        PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    assert calls == []


def test_empty_vapor_pressure_invalid_liquid_fraction_preserves_false_gate():
    sim = _with_empty_vapour_batch(
        types.SimpleNamespace(melt=types.SimpleNamespace(temperature_C=1600.0))
    )
    result = types.SimpleNamespace(
        liquid_fraction=float('nan'),
        vapor_pressures_Pa={},
        diagnostics={},
    )

    with pytest.raises(EvaporationFluxRefusal) as exc_info:
        PyrolysisSimulator._calculate_evaporation(sim, result)
    assert exc_info.value.reason == 'vapour_batch_no_debiting_pressure_outcome'

    divergence = sim._last_evaporation_flux_diagnostic[
        'melt_regime_predicate_divergences'
    ][0]
    assert divergence['site'] == (
        'evaporation.empty_vapor_pressure.liquid_fraction'
    )
    assert divergence['effective_regime'] == 'partial'
    assert divergence['liquid_fraction_invalid'] == 'non_finite'


def test_empty_vapor_pressure_string_zero_preserves_legacy_false_gate():
    sim = _with_empty_vapour_batch(
        types.SimpleNamespace(melt=types.SimpleNamespace(temperature_C=1600.0))
    )
    result = types.SimpleNamespace(
        liquid_fraction="0",
        vapor_pressures_Pa={},
        diagnostics={},
    )

    with pytest.raises(EvaporationFluxRefusal) as exc_info:
        PyrolysisSimulator._calculate_evaporation(sim, result)
    assert exc_info.value.reason == 'vapour_batch_no_debiting_pressure_outcome'

    assert sim._last_evaporation_flux_diagnostic['reason'] == (
        'vapour_batch_no_debiting_pressure_outcome'
    )
    assert (
        'melt_regime_predicate_divergences'
        not in sim._last_evaporation_flux_diagnostic
    )


def test_kernel_refresh_preserves_per_species_source_labels():
    source = (
        'vaporock_backsolved_curve_fit:'
        'backsolved_vaporock_curve_fit'
    )
    sim, calls = _sim_with_vapor_dispatch({'Na': 12.5}, {'Na': source})
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        phases_present=['liq', 'olivine'],
        phase_masses_kg={'liq': 0.25, 'olivine': 0.75},
        liquid_fraction=0.25,
        vapor_pressures_Pa={'Na': 3.0},
        vapor_pressures_source={'Na': 'backend_pre_kernel'},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]
    assert result.vapor_pressures_Pa == {'Na': pytest.approx(12.5)}
    assert result.vapor_pressures_source == {'Na': source}
    assert sim._last_vapor_pressure_diagnostic['vapor_pressures_source'] == {
        'Na': source,
    }


def test_authoritative_vapor_pressure_vapor_only_none_does_not_zero_gate():
    sim, calls = _sim_with_vapor_dispatch({'Na': 8.0})
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        liquid_fraction=None,
        phase_assemblage_available=False,
        vapor_pressures_Pa={'Na': 3.0},
        vapor_pressures_source={'Na': 'backend_pre_kernel'},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]
    assert result.vapor_pressures_Pa == {'Na': pytest.approx(8.0)}
