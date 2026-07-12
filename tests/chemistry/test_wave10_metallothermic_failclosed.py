from __future__ import annotations

import math

import pytest

from engines.builtin.metallothermic_step import (
    BuiltinMetallothermicStepProvider,
    REACTION_FAMILY_C3_NA,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.chemistry.melt_activity import (
    MELT_OXIDE_ACTIVITY_COEFFICIENTS,
    R_KJ_PER_MOL_K,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.state import MOLAR_MASS
from tests.chemistry.conftest import _build_sim


def _kg_to_mol(composition_kg: dict[str, float]) -> dict[str, float]:
    return {
        species: kg * 1000.0 / MOLAR_MASS[species]
        for species, kg in composition_kg.items()
    }


def _dispatch_na(
    sim,
    *,
    cleaned_melt_kg: dict[str, float],
    spent_residue_kg: dict[str, float] | None = None,
    reagent_available_kg: float = 30.0,
    temperature_C: float = 1150.0,
):
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": _kg_to_mol(cleaned_melt_kg),
            SPENT_REDUCTANT_RESIDUE_ACCOUNT: _kg_to_mol(
                spent_residue_kg or {}
            ),
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=temperature_C,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_NA,
            "na_target_stage": "feo_cleanup",
            "reagent_available_kg": reagent_available_kg,
            "liquid_fraction": 1.0,
            "dt_hr": 1.0,
        },
    )
    return BuiltinMetallothermicStepProvider().dispatch(request)


def test_na_activity_shift_uses_full_melt_mole_fraction(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
) -> None:
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    concentrated = _dispatch_na(
        sim,
        cleaned_melt_kg={"FeO": 10.0, "SiO2": 90.0},
        spent_residue_kg={"Na2O": 1.0},
    )
    diluted = _dispatch_na(
        sim,
        cleaned_melt_kg={"FeO": 10.0, "SiO2": 990.0},
        spent_residue_kg={"Na2O": 1.0},
    )

    gamma = MELT_OXIDE_ACTIVITY_COEFFICIENTS["Na2O"].gamma
    for result in (concentrated, diluted):
        assert result.status == "ok"
        diagnostic = result.diagnostic
        activity = diagnostic["Na2O_activity"]
        assert activity == pytest.approx(
            gamma * diagnostic["Na2O_activity_X_single_cation"]
        )
        expected_shift = (
            4.0
            * R_KJ_PER_MOL_K
            * (1150.0 + CELSIUS_TO_KELVIN_OFFSET)
            * math.log(activity)
        )
        assert diagnostic[
            "Na2O_activity_shift_kJ_per_mol_O2"
        ] == pytest.approx(expected_shift)
        assert diagnostic["thermo_deltaG_kJ_per_mol_O2"][
            "Na2O"
        ] == pytest.approx(
            diagnostic["standard_deltaG"]["Na2O"] + expected_shift
        )

    assert diluted.diagnostic["Na2O_activity"] < concentrated.diagnostic[
        "Na2O_activity"
    ]
    assert diluted.diagnostic[
        "Na2O_activity_shift_kJ_per_mol_O2"
    ] < concentrated.diagnostic["Na2O_activity_shift_kJ_per_mol_O2"]


def test_absent_na2o_product_uses_zero_activity_but_fe_raw_clamp_refuses(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
) -> None:
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    result = _dispatch_na(
        sim,
        cleaned_melt_kg={"FeO": 10.0, "SiO2": 90.0},
        temperature_C=1300.0,
    )

    assert result.status == "refused"
    assert result.transition is None
    diagnostic = result.diagnostic
    assert diagnostic["Na2O_activity_X_single_cation"] == 0.0
    assert diagnostic["Na2O_activity"] == 0.0
    shift = diagnostic["Na2O_activity_shift_kJ_per_mol_O2"]
    assert math.isinf(shift)
    assert shift < 0.0
    assert math.isinf(
        diagnostic["na_activity_shifted_margin_kJ_per_mol_O2"]["FeO"]
    )
    assert diagnostic["na_reduction_margin_kJ_per_mol_O2"]["FeO"] < 0.0


def test_na2o_solubility_cap_uses_post_reaction_melt_denominator(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
) -> None:
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    result = _dispatch_na(
        sim,
        cleaned_melt_kg={"FeO": 90.5, "Na2O": 9.5},
        reagent_available_kg=100.0,
    )

    assert result.status == "ok"
    assert result.transition is not None
    diagnostic = result.diagnostic
    assert diagnostic["na2o_post_reaction_wt_pct"] <= 10.0 + 1e-12
    assert diagnostic["spent_reductant_residue_kg"]["Na2O"] < 0.5
