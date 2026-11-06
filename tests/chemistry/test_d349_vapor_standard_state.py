"""Pins for the d-349 vapor activity reference-state correction."""

from __future__ import annotations

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from tests.chemistry.conftest import _build_sim


def _lunar_mare_probe(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": sim.atom_ledger.mol_by_account(
                    "process.cleaned_melt"
                )
            },
            species_formula_registry=sim.species_formula_registry,
        ),
        temperature_C=1500.0,
        pressure_bar=1.0,
        fO2_log=0.0,
        control_inputs={"pO2_bar": 1.0, "intrinsic_fO2_log": 0.0},
    )
    return BuiltinVaporPressureProvider(vapor_pressure_data).dispatch(request)


def test_b1119_lunar_mare_vapor_pressure_output_is_pinned_before_d349(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Pin b-1119 before the behavior change, via the production provider."""

    result = _lunar_mare_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    # Exact float.hex pins from BuiltinVaporPressureProvider.dispatch at
    # b-1119 11a7c9b62, for the production lunar feedstock account at 1773.15
    # K and pO2=1 bar. The values reproduce worker-report-q79.md's b-1119
    # production probe; green a6d75d8b4 values are pinned after d-349.
    expected = {
        "Mg": "0x1.c499c5c7d1a10p-26",
        "Ca": "0x1.154c39b5a3bddp-40",
        "MgO_gas": "0x1.348c97da7c3e4p-20",
        "CaO_gas": "0x1.0694b5cb95107p-33",
    }
    pressures = result.diagnostic["vapor_pressures_Pa"]
    assert result.status == "ok"
    assert {species: pressures[species].hex() for species in expected} == expected
