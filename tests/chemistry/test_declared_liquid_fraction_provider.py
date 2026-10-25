"""The gate's declared physics must not depend on engine discovery."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from engines.magemin import MAGEMinShadowProvider
from simulator.chemistry.kernel import normalize_chemistry_kernel_config
from simulator.evaporation import EvaporationFluxRefusal
from simulator.fe_redox import KRESS91_LIQUID_CALIBRATION_MIN_T_C
from simulator.engine_pool import EngineWorkerTimeout
from simulator.melt_backend.liquidus import LiquidusSampleError
from simulator.runner import PyrolysisRun, build_per_hour_summary
from simulator.accounting.run_artifact import build_run_artifact
from simulator.optimize.evalspec import EvalSpec, canonical_evalspec_json
from simulator.optimize.evaluate import _build_eval_inputs
import simulator.optimize.evaluate as evaluate_module
from simulator.config import load_config_bundle
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.state import CampaignPhase
from tests.chemistry.conftest import _build_sim
from tests.test_optimizer_evalspec import _mre_cap_profile


def _sim(provider, vapor_pressure_data, feedstocks_data, setpoints_data):
    setpoints = deepcopy(setpoints_data)
    if provider is not None:
        setpoints['chemistry_kernel']['liquid_fraction_provider'] = provider
    return _build_sim(
        'lunar_mare_low_ti', vapor_pressure_data, feedstocks_data, setpoints,
    )


@pytest.mark.parametrize('value', ['auto', 'alphamelts', '', None, True, 1])
def test_gate_provider_rejects_undeclared_values(value):
    with pytest.raises((TypeError, ValueError), match='liquid_fraction_provider'):
        normalize_chemistry_kernel_config({'liquid_fraction_provider': value})


@pytest.mark.parametrize('provider', [None, 'magemin'])
def test_missing_declared_engine_refuses_before_first_hour(
    monkeypatch, provider, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    monkeypatch.setattr(MAGEMinShadowProvider, '_ensure_backend', lambda _: None)
    sim = _sim(provider, vapor_pressure_data, feedstocks_data, setpoints_data)
    before = sim.atom_ledger.mol_by_account()
    with pytest.raises(EvaporationFluxRefusal) as error:
        sim.start_campaign(CampaignPhase.C2A_STAGED)
    assert error.value.diagnostic['reason'] == 'missing_input'
    assert error.value.diagnostic['provider'] == 'magemin'
    assert sim.melt.hour == 0
    assert sim.atom_ledger.mol_by_account() == before
    assert sim.record.snapshots == []


def test_analytical_gate_never_probes_magemin_and_flags_each_hour(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    def forbidden(*args, **kwargs):
        pytest.fail('analytical gate called MAGEMin')

    monkeypatch.setattr(MAGEMinShadowProvider, '_ensure_backend', forbidden)
    sim = _sim('analytical', vapor_pressure_data, feedstocks_data, setpoints_data)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    curve = sim._freeze_gate_curve()
    floor = KRESS91_LIQUID_CALIBRATION_MIN_T_C
    assert sim._interpolate_freeze_gate_curve(curve, floor - 1.0) == 0.0
    assert sim._interpolate_freeze_gate_curve(curve, floor + 1.0) == 1.0
    assert sim.record.liquid_fraction_provider == 'analytical'
    for _ in range(2):
        snapshot = sim.step()
        diagnostic = snapshot.liquid_fraction_gate
        assert diagnostic['provider'] == 'analytical'
        assert diagnostic['qualification'] == 'analytical_kress91_floor_prediction'
        assert build_per_hour_summary(sim, snapshot)['oxygen_reservoir'][
            'liquid_fraction_gate'
        ] == diagnostic


@pytest.mark.parametrize('status', ['unavailable', 'not_converged'])
def test_declared_magemin_failure_never_uses_another_liquidus(
    monkeypatch, status, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    monkeypatch.setattr(sim, '_dispatch_only', lambda *a, **kw: SimpleNamespace(
        status=status, diagnostic={'backend_status': status},
    ))
    calls = []
    sim.backend.find_liquidus_solidus = lambda **kw: calls.append(kw)
    monkeypatch.setattr(
        sim, '_composition_projected_freeze_gate_curve',
        lambda *a, **kw: pytest.fail('numerical failure used projected bounds'),
    )
    with pytest.raises(EvaporationFluxRefusal) as error:
        sim._freeze_gate_curve()
    assert error.value.diagnostic['provider'] == 'magemin'
    assert calls == []


def test_runner_reports_typed_startup_missing_input(monkeypatch):
    monkeypatch.setattr(MAGEMinShadowProvider, '_ensure_backend', lambda _: None)
    result = PyrolysisRun(
        feedstock_id='lunar_mare_low_ti', campaign='C2A_staged', hours=1,
    ).run()
    assert result['status'] == 'refused'
    artifact = build_run_artifact(result, run_id='missing-gate')
    assert artifact['timesteps'] == []
    metadata = artifact['terminal']['run_metadata']
    assert metadata['liquid_fraction_provider'] == 'magemin'
    assert metadata['refusal_diagnostic']['reason'] == 'missing_input'


@pytest.mark.parametrize('failure', [
    LiquidusSampleError('not_converged', (), {'reason': 'aggregate_budget_exceeded'}),
    EngineWorkerTimeout('magemin', 15.0, phase='liquidus'),
])
def test_numerical_gate_exception_never_becomes_solidification(
    monkeypatch, failure, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(sim, '_dispatch_only', fail)
    before = sim.atom_ledger.mol_by_account()
    with pytest.raises(EvaporationFluxRefusal):
        sim._melt_redox_liquid_fraction_factor(1150.0 + 273.15)
    assert sim.atom_ledger.mol_by_account() == before
    assert sim._melt_redox_liquidus_gate_fallback_summary() == {}


def test_gate_provider_is_a_run_identity_input():
    args = dict(
        recipe_id='gate', feedstock_recipe_digest='', feedstock_id='lunar_mare_low_ti',
        profile_id='gate', fidelity='test', code_version='test', data_digests={},
    )
    default = canonical_evalspec_json(EvalSpec(**args))
    magemin = canonical_evalspec_json(EvalSpec(
        **args, chemistry_kernel={'liquid_fraction_provider': 'magemin'},
    ))
    analytical = canonical_evalspec_json(EvalSpec(
        **args, chemistry_kernel={'liquid_fraction_provider': 'analytical'},
    ))
    assert default == magemin
    assert analytical != magemin


def test_optimizer_preserves_declared_gate_in_config_and_identity():
    profile = _mre_cap_profile(chemistry_kernel={'liquid_fraction_provider': 'analytical'})
    spec, config = _build_eval_inputs(
        RecipePatch({}), 'lunar_mare_low_ti', 'internal-analytical', profile, RecipeSchema(),
    )
    assert config.setpoints['chemistry_kernel']['liquid_fraction_provider'] == 'analytical'
    assert spec.chemistry_kernel['liquid_fraction_provider'] == 'analytical'


def test_optimizer_resolves_gate_declared_in_loaded_setpoints(monkeypatch):
    bundle = load_config_bundle()
    setpoints = deepcopy(bundle.setpoints)
    setpoints['chemistry_kernel']['liquid_fraction_provider'] = 'analytical'
    monkeypatch.setattr(
        evaluate_module, 'load_config_bundle',
        lambda *a, **kw: replace(bundle, setpoints=setpoints),
    )
    spec, config = _build_eval_inputs(
        RecipePatch({}), 'lunar_mare_low_ti', 'internal-analytical',
        _mre_cap_profile(), RecipeSchema(),
    )
    assert config.setpoints['chemistry_kernel']['liquid_fraction_provider'] == 'analytical'
    assert spec.chemistry_kernel['liquid_fraction_provider'] == 'analytical'
