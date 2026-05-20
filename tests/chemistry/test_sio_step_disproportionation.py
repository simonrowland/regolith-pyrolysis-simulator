"""SiO step isolation: DISPROPORTIONATION."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engines.builtin.condensation_route import BuiltinCondensationRouteProvider
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.state import MOLAR_MASS


REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_BALANCE_ERR_PCT = 5.0e-12


def _sio_sp_data() -> dict:
    vapor_pressures = yaml.safe_load(
        (REPO_ROOT / "data" / "vapor_pressures.yaml").read_text()
    )
    return dict(vapor_pressures["oxide_vapors"]["SiO"])


def test_sio_disproportionation_mol_balance_matches_steurer_eq18():
    condensed_kg = MOLAR_MASS["SiO"] / 1000.0
    view = ProviderAccountView(
        accounts={"process.overhead_gas": {"SiO": 1.0}},
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.CONDENSATION_ROUTE,
        account_view=view,
        temperature_C=1050.0,
        pressure_bar=1.0e-6,
        control_inputs={
            "species": "SiO",
            "condensed_kg": condensed_kg,
            "sp_data": _sio_sp_data(),
            "dt_hr": 1.0,
        },
    )

    result = BuiltinCondensationRouteProvider().dispatch(request)
    assert result.transition is not None

    credits = result.transition.credits["process.condensation_train"]
    assert credits["Si"] == pytest.approx(0.5)
    assert credits["SiO2"] == pytest.approx(0.5)

    si_err = abs((credits["Si"] + credits["SiO2"]) - 1.0)
    o_err = abs((2.0 * credits["SiO2"]) - 1.0)
    atom_err = max(si_err, o_err)
    atom_err_pct = atom_err / 2.0 * 100.0
    assert atom_err_pct <= MAX_BALANCE_ERR_PCT
