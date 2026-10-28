from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest


def _seeded_output(source: str, seed: str) -> str:
    environment = dict(os.environ, PYTHONHASHSEED=seed)
    return subprocess.check_output(
        [sys.executable, "-c", source],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=environment,
        text=True,
    ).strip()


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """import json\nfrom simulator.core import PyrolysisSimulator\nsim = object.__new__(PyrolysisSimulator)\nsim.atom_ledger = type('Ledger', (), {'total_kg_by_account': lambda self: {'reservoir.a': 1e16, 'reservoir.b': 1.0, 'reservoir.c': 1.0}})()\nprint(json.dumps(sim._flow_mass_out_kg().hex()))""",
            id="flow-mass-sum-defensive",
        ),
        pytest.param(
            """from simulator.chemistry.phase_context import _composition_distance\nprint(_composition_distance({'a': 1e8, 'b': 1.0, 'c': 1.0}, {} ).hex())""",
            id="phase-cache-distance-defensive",
        ),
        pytest.param(
            """from scripts.grid_pregrind_triage import _nearest_feedstock\nprint(_nearest_feedstock({'a': 1e16, 'b': 1.0, 'c': 1.0}, {'anchor': {'a': 50.0}}))""",
            id="nearest-feedstock-distance-defensive",
        ),
        pytest.param(
            """from engines.builtin.overhead_gas_equilibrium import BuiltinOverheadGasEquilibriumProvider\np = BuiltinOverheadGasEquilibriumProvider\nprint(p.compute_element_partial_pressures_bar({'a': 1e16, 'b': 1.0, 'c': 1.0}, element_species={'X': {'a', 'b', 'c'}})['X'].hex())""",
            id="overhead-partial-pressure-sum-defensive",
        ),
        pytest.param(
            """import json\nfrom engines.builtin.condensation_route import BuiltinCondensationRouteProvider as P\nprint(json.dumps(list(P._wall_delta_kg_by_account({'account.c': {'x': 1.0}, 'account.a': {'x': 2.0}}, {'account.b': {'x': 3.0}}))))""",
            id="condensation-route-diagnostic-order",
        ),
        pytest.param(
            """from types import SimpleNamespace\nimport engines.builtin.vapor_pressure as vp\nimport simulator.melt_backend.openimcc_bridge as bridge\nbridge.evaluate_cleaned_melt = lambda **kwargs: SimpleNamespace(single_cation_activities={'SiO2': 1.0, 'MgO': 1.0, 'FeO': 1.0}, bridge=SimpleNamespace(pack_model_id='x', pack_version='x', pack_digest='x'))\nvp._high_t_activity_for_parent = lambda *args, **kwargs: SimpleNamespace(activity_model='openimcc_single_cation_activity', effective_gamma=1.0)\nvp._regular_solution_gamma_fit = lambda observations, temperature: {'fit': True}\nresult = vp._same_composition_temperature_bridge({'SiO2': 1.0, 'MgO': 1.0, 'FeO': 1.0}, 1000.0, (), activity_basis_mol={'SiO2': 1.0, 'MgO': 1.0, 'FeO': 1.0})\nprint(','.join(result['fits']))""",
            id="vapor-pressure-parent-notice-order-defensive",
        ),
        pytest.param(
            """import json\nfrom tests.test_runner_smoke import SCENARIOS, _run_scenario\ns = next(s for s in SCENARIOS if s['name'] == 'lunar_mare_low_ti_C0_24h')\np = _run_scenario(s)\nprint(json.dumps(p, sort_keys=True, separators=(',', ':'), allow_nan=False))""",
            id="capture-relaxation-defensive",
        ),
    ],
)
def test_hash_seed_does_not_change_site_output(source: str) -> None:
    """Compare production consumers under different process hash orders."""
    assert _seeded_output(source, "0") == _seeded_output(source, "1")


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """from simulator.subsolidus_bakeout import throttle_mass_rate_kg_hr
values = {'Na': 1373.7356370603416, 'SO2': 4.4178619452227434e-10,
          'K': 65.25457231186826, 'S2': 1743125144.2783368,
          'H2O': 0.041713315944588936}
print(throttle_mass_rate_kg_hr(values, {}).hex())""",
            id="volatile-throttle",
        ),
        pytest.param(
            """from simulator.condensation import _wall_route_species_order
print(_wall_route_species_order({s: 1.0 for s in {'Cr', 'Mn', 'Ca', 'Ti', 'TiO', 'SiO', 'Mg', 'Fe', 'Na', 'K'}}))""",
            id="wall-route-non-c4b-tail",
        ),
        pytest.param(
            """import json
from types import SimpleNamespace
from unittest.mock import patch
from tests.test_extraction_ledger import _sim, _enable_c5_mre
from simulator.condensation import CondensationModel
from simulator.state import CampaignPhase, CondensationTrain, EvaporationFlux, MeltState
sim = _sim({'feo': {'label': 'FeO', 'composition_wt_pct': {'FeO': 100.0}}})
sim.load_batch('feo', mass_kg=1000.0)
_enable_c5_mre(sim, target_species='FeO', max_voltage_V=0.75)
sim.melt.campaign = CampaignPhase.C5
sim._mre_voltage_sequence = [{'voltage': 0.75, 'species': ['FeO'], 'min_hold_hours': 1}]
diagnostic = {'gas_products_produced_kg': {s: 1.0 for s in {'Na', 'K', 'Mg'}},
              'gas_products_produced_mol': {'Mg': 1.0}, 'energy_kWh': 0.0}
reads = {}
def inventory(account, species):
    key = (account, species)
    reads[key] = reads.get(key, 0) + 1
    return float(account == 'process.overhead_gas' and reads[key] > 1)
def route(gases, **kwargs):
    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(wall_temperature_C=900.0,
        overhead_pressure_mbar=10.0, pipe_diameter_m=0.12,
        species_partial_pressures_mbar={s: 1.0 for s in gases},
        stage_area_m2_by_stage={str(s.stage_number): 1.0 for s in model.train.stages})
    flux = EvaporationFlux(species_kg_hr=gases)
    flux.update_totals()
    result = model.route(flux, MeltState(temperature_C=1700.0))
    assert result.antoine_extrapolation_warnings
    print(json.dumps([list(gases), result.antoine_extrapolation_warnings]))
    raise SystemExit(0)
with patch.object(sim._chem_kernel, 'dispatch', return_value=SimpleNamespace(
        diagnostic=diagnostic, transition=None, status='ok')), \\
     patch.object(sim, '_ledger_account_species_kg', side_effect=inventory), \\
     patch.object(sim, '_route_mre_gas_products_to_condensation', side_effect=route):
    sim._step_mre()
raise AssertionError('MRE gas route was not reached')""",
            id="mre-gas-antoine-warning-order",
        ),
        pytest.param(
            """from simulator.overhead import _mean_molar_mass_kg_mol
values = {'Na': 1373.7356370603416, 'SO2': 4.4178619452227434e-10,
          'K': 65.25457231186826, 'S2': 1743125144.2783368,
          'H2O': 0.041713315944588936}
mixture = {species: values[species] for species in set(values)}
print(_mean_molar_mass_kg_mol(mixture).hex())""",
            id="pipe-mixture-reduction",
        ),
        pytest.param(
            """from simulator.accounting.ledger import AtomLedger
from simulator.chemistry.kernel.account_filters import build_provider_account_view
view = build_provider_account_view(AtomLedger(),
    frozenset({'process.cleaned_melt', 'process.metal_phase', 'process.overhead_gas'}),
    species_formula_registry={})
print(list(view.accounts))""",
            id="provider-account-vector",
        ),
        pytest.param(
            """from simulator.accounting.ledger import AtomLedger
ledger = AtomLedger()
ledger.load_external('process.cleaned_melt', {'SiO2': 1.0}, material_origin='feedstock')
print(list(ledger.external_origin_atom_moles()))""",
            id="origin-output",
        ),
        pytest.param(
            """from simulator.thermal_train import thermal_train_parameters_from_mapping
params = thermal_train_parameters_from_mapping()
for values in (params.cold_train.rating, params.cold_train.orifice, params.cold_train.relief):
    print(list(values))""",
            id="cold-train-output",
        ),
        pytest.param(
            """from simulator.chemistry.sgte_unary import coverage_table
print(list(coverage_table([], {'Si', 'Al', 'Ca', 'Fe', 'Na', 'K'})))""",
            id="element-coverage-output",
        ),
        pytest.param(
            """from simulator.battery.identity import Identity
from simulator.battery.enums import Quantity
from simulator.battery.validate import _check_identity
issues = []
_check_identity('identity', Identity(quantity=Quantity.CP, species=None), issues)
print([(issue.path, issue.detail) for issue in issues])""",
            id="required-axis-diagnostics",
        ),
        pytest.param(
            """from simulator.run_executor import _json_safe
print(_json_safe({'species': frozenset({'Na', 'K', 'SiO', 'Fe'})}))""",
            id="json-set-output",
        ),
        pytest.param(
            """from types import SimpleNamespace as NS
from simulator.diagnostic_helpers import extract_reproduction as e
e.parse_ordering_claim = lambda obs: {
    'kind': 'strict_sequence', 'sequence': ['Na', 'K', 'Fe'], 'asserted': 'fixture'}
e.rail_alpha_comparability = lambda obs: (True, None, {})
e._temperature_grid = lambda obs: [1500.0]
e._melt_recipe_mol = lambda obs: ({'SiO2': 1.0}, 'fixture', None)
e._engine_species_rank_metric = lambda *args: (1.0, None, {})
e._compare_point = lambda **kwargs: NS(runtime=kwargs['runtime'])
obs = NS(species_id='Na', observation_id='fixture')
result = e._evaluate_ordering(obs, e.ObservationEvaluation(obs),
    vp_data={'fixture': True}, pO2=1e-6)
print(list(result.records[0].runtime['metric_by_T'][0]['metrics']))""",
            id="ordering-metric-output",
        ),
    ],
)
def test_t1462_hash_seeds_agree(source: str) -> None:
    assert _seeded_output(source, "1") == _seeded_output(source, "5")


def test_t1462_origin_iteration_permutations(monkeypatch) -> None:
    import simulator.accounting.ledger as ledger_module

    outputs = []
    for origins in (("reagent", "feedstock"), ("feedstock", "reagent")):
        monkeypatch.setattr(ledger_module, "MATERIAL_ORIGINS", origins)
        ledger = ledger_module.AtomLedger()
        for origin in ("feedstock", "reagent"):
            ledger.load_external(
                "process.cleaned_melt", {"SiO2": 1.0}, material_origin=origin
            )
        transition = ledger.move(
            "move", "process.cleaned_melt", "process.condensation_train",
            {"SiO2": 2.0},
        )
        outputs.append(json.dumps({
            "external": ledger.external_origin_atom_moles(),
            "debit_origins": list(transition.debits[0].origin_atom_moles),
            "credit_origins": list(transition.credits[0].origin_atom_moles),
            "balances": ledger.origin_atom_moles_by_account(),
        }))
    assert outputs[0] == outputs[1]
