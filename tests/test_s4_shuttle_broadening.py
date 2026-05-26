import shlex

import pytest

from engines.builtin.metallothermic_step import (
    BuiltinMetallothermicStepProvider,
    REACTION_FAMILY_C3_NA,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.session_cli import SessionScriptRunner
from simulator.state import MOLAR_MASS, STOICH_RATIOS
from tests.chemistry.conftest import _atom_check, _build_sim, _load_yaml


FEEDSTOCK = "lunar_mare_low_ti"
HOT_HOLD_C = 1750.0
K_DOSE_KG = 26.0
NA_DOSE_KG = 12.0
MASS_BALANCE_MAX_PCT = 5e-12


def _kg_to_mol(species: str, kg: float) -> float:
    return kg / MOLAR_MASS[species] * 1000.0


def _run_script(lines: list[str]):
    runner = SessionScriptRunner()
    for line in lines:
        runner.execute(shlex.split(line), line)
    return runner.session._sim


def _run_staged(*, na_dose_kg: float = 0.0):
    additive = f"--additive=K={K_DOSE_KG}"
    if na_dose_kg > 0.0:
        additive = f"{additive} --additive=Na={na_dose_kg}"
    return _run_script([
        f"start --feedstock={FEEDSTOCK} --campaign=C2A_staged {additive}",
        f"adjust campaign_override C2A_staged hold_temp_C {HOT_HOLD_C}",
        "advance 30",
    ])


def _build_provider_sim():
    return _build_sim(
        FEEDSTOCK,
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )


def _fe_element_kg(oxides_kg: dict[str, float]) -> float:
    return (
        oxides_kg.get("FeO", 0.0) * STOICH_RATIOS["FeO"][0]
        + oxides_kg.get("Fe2O3", 0.0) * STOICH_RATIOS["Fe2O3"][0]
    )


def _max_mass_balance_pct(sim) -> float:
    return max(abs(s.mass_balance_error_pct) for s in sim.record.snapshots)


def _cumulative_transition_imbalance_kg(sim) -> float:
    registry = sim.atom_ledger.registry
    return sum(
        abs(t.debit_mass_kg(registry) - t.credit_mass_kg(registry))
        for t in sim.atom_ledger.transitions
    )


def test_na_shuttle_reduces_feo_to_fe_atom_balanced():
    sim = _build_provider_sim()
    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {"FeO": _kg_to_mol("FeO", 10.0)},
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=1150.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_NA,
            "na_target_stage": "feo_cleanup",
            "reagent_available_kg": 12.0,
            "dt_hr": 1.0,
        },
    )

    result = provider.dispatch(request)
    proposal = result.transition

    assert proposal is not None
    assert result.diagnostic["target_priority"] == ["FeO"]
    assert result.diagnostic["na_reduction_margin_kJ_per_mol_O2"]["FeO"] > 0.0
    mol_na_used = proposal.debits["process.reagent_inventory"]["Na"]
    mol_feo_reduced = proposal.debits["process.cleaned_melt"]["FeO"]
    assert mol_na_used == pytest.approx(2.0 * mol_feo_reduced)
    assert proposal.credits["process.cleaned_melt"]["Na2O"] == pytest.approx(
        mol_feo_reduced
    )
    assert proposal.credits["process.metal_phase"]["Fe"] == pytest.approx(
        mol_feo_reduced
    )
    _atom_check(proposal, sim.species_formula_registry, tol=1e-12)


def test_na_cr_stage_refuses_cr_ti_with_negative_margins():
    sim = _build_provider_sim()
    provider = BuiltinMetallothermicStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "FeO": _kg_to_mol("FeO", 50.0),
                "Cr2O3": _kg_to_mol("Cr2O3", 1.5),
                "TiO2": _kg_to_mol("TiO2", 20.0),
            },
            "process.metal_phase": {},
            "process.reagent_inventory": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.METALLOTHERMIC_STEP,
        account_view=view,
        temperature_C=1300.0,
        pressure_bar=1e-6,
        control_inputs={
            "reaction_family": REACTION_FAMILY_C3_NA,
            "reagent_available_kg": 30.0,
            "dt_hr": 1.0,
        },
    )

    result = provider.dispatch(request)
    proposal = result.transition

    assert result.status == "refused"
    assert proposal is None
    assert result.diagnostic["target_stage"] == "cr_ti"
    assert result.diagnostic["target_priority"] == ["Cr2O3", "TiO2"]
    assert result.diagnostic["accepted_targets"] == []
    refused = result.diagnostic["refused_targets"]
    assert set(refused) == {"Cr2O3", "TiO2"}
    assert refused["Cr2O3"]["margin_kJ_per_mol_O2"] < 0.0
    assert refused["TiO2"]["margin_kJ_per_mol_O2"] < 0.0


def test_c2a_staged_k_plus_na_shuttle_beats_k_only_and_stays_cool():
    k_only = _run_staged()
    broadened = _run_staged(na_dose_kg=NA_DOSE_KG)

    initial_fe = _fe_element_kg(
        broadened.record.snapshots[0].inventory.raw_components_kg
    )
    k_only_fe = k_only.product_ledger().get("Fe", 0.0)
    broadened_fe = broadened.product_ledger().get("Fe", 0.0)
    k_only_recovery = k_only_fe / initial_fe
    broadened_recovery = broadened_fe / initial_fe
    increment = broadened_recovery - k_only_recovery
    na_fe = broadened.atom_ledger.kg_by_account("process.metal_phase").get(
        "Fe",
        0.0,
    ) - k_only.atom_ledger.kg_by_account("process.metal_phase").get("Fe", 0.0)
    shuttle_snapshots = [
        s
        for s in broadened.record.snapshots
        if s.shuttle_phase == "inject" and s.shuttle_metal_produced_kg_hr > 0.0
    ]

    assert broadened.record.additives_kg["Na"] == pytest.approx(NA_DOSE_KG)
    assert broadened_fe > k_only_fe
    assert increment > 0.005
    assert na_fe > 1.0
    assert max(s.temperature_C for s in shuttle_snapshots) < 1200.0
    assert _max_mass_balance_pct(k_only) < MASS_BALANCE_MAX_PCT
    assert _max_mass_balance_pct(broadened) < MASS_BALANCE_MAX_PCT
    assert _cumulative_transition_imbalance_kg(broadened) < 1e-6
    assert round(k_only_recovery, 10) < round(broadened_recovery, 10)
