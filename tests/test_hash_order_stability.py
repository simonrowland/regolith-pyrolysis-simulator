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
