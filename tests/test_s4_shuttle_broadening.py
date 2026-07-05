import shlex
from types import SimpleNamespace

import pytest

from engines.builtin.metallothermic_step import (
    BuiltinMetallothermicStepProvider,
    REACTION_FAMILY_C3_NA,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.session_cli import SessionScriptRunner
from simulator.state import CampaignPhase, EvaporationFlux, MOLAR_MASS, STOICH_RATIOS
from tests.chemistry.conftest import (
    _atom_check,
    _build_sim,
    _force_vaporock_unavailable_for_sim,
    _load_yaml,
)


FEEDSTOCK = "lunar_mare_low_ti"
HOT_HOLD_C = 1750.0
K_DOSE_KG = 26.0
NA_DOSE_KG = 12.0
MASS_BALANCE_MAX_PCT = 5e-12
_CROSSOVER_TOL_C = 0.05


def _kg_to_mol(species: str, kg: float) -> float:
    return kg / MOLAR_MASS[species] * 1000.0


def _apply_mol_proposal(accounts: dict[str, dict[str, float]], proposal) -> None:
    for account, species_mol in proposal.debits.items():
        account_mol = accounts.setdefault(account, {})
        for species, mol in species_mol.items():
            account_mol[species] = account_mol.get(species, 0.0) - mol
    for account, species_mol in proposal.credits.items():
        account_mol = accounts.setdefault(account, {})
        for species, mol in species_mol.items():
            account_mol[species] = account_mol.get(species, 0.0) + mol


def _run_script(lines: list[str]):
    runner = SessionScriptRunner()
    for line in lines:
        runner.execute(shlex.split(line), line)
        if line.startswith("start "):
            sim = runner.session.simulator
            sim._allow_fallback_vapor = True
            sim._chem_kernel = sim._build_chemistry_kernel()
            _force_vaporock_unavailable_for_sim(sim)
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


def test_c3_step_refreshes_equilibrium_after_shuttle_before_evaporation(
    monkeypatch,
):
    sim = _build_provider_sim()
    sim.melt.campaign = CampaignPhase.C3_NA
    sim.melt.campaign_hour = 1
    sim.record.path = "A_staged"

    calls: list[str] = []
    shuttle_state = {"injected": False}

    def fake_step_shuttle():
        calls.append("shuttle")
        shuttle_state["injected"] = True
        sim._shuttle_phase = "inject"
        sim._shuttle_injected_this_hr = 1.0
        sim._shuttle_reduced_this_hr = 2.0
        sim._shuttle_metal_this_hr = 3.0

    def fake_get_equilibrium():
        calls.append(
            "equilibrium_post_shuttle"
            if shuttle_state["injected"]
            else "equilibrium_pre_shuttle"
        )
        return {"saw_injection": shuttle_state["injected"]}

    def fake_calculate_evaporation(equilibrium):
        calls.append("evaporation")
        assert equilibrium == {"saw_injection": True}
        return EvaporationFlux()

    monkeypatch.setattr(
        sim.campaign_mgr,
        "apply_lab_schedule_controls",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(sim, "validate_lab_surface_temperature_resolver", lambda: None)
    monkeypatch.setattr(sim, "_update_temperature", lambda: None)
    monkeypatch.setattr(sim, "_apply_oxygen_reservoir_exchange", lambda: None)
    monkeypatch.setattr(sim, "_step_shuttle", fake_step_shuttle)
    monkeypatch.setattr(sim, "_get_equilibrium", fake_get_equilibrium)
    monkeypatch.setattr(sim, "_calculate_evaporation", fake_calculate_evaporation)
    monkeypatch.setattr(
        sim,
        "_apply_analytic_evaporation_depletion",
        lambda flux: flux,
    )
    monkeypatch.setattr(sim, "_update_melt_composition", lambda flux: None)
    monkeypatch.setattr(sim, "_get_turbine_spec", lambda: None)
    monkeypatch.setattr(sim, "_overhead_headspace_enabled", lambda: False)
    monkeypatch.setattr(sim, "_ledger_o2_kg", lambda account: 0.0)
    monkeypatch.setattr(
        sim.overhead_model,
        "update",
        lambda *args, **kwargs: sim.overhead,
    )
    monkeypatch.setattr(sim, "_dispatch_overhead_bleed", lambda *args, **kwargs: None)
    monkeypatch.setattr(sim, "_sync_oxygen_kg_counters", lambda: None)
    monkeypatch.setattr(
        sim.energy_tracker,
        "calculate_hour",
        lambda *args, **kwargs: SimpleNamespace(total_kWh=0.0),
    )
    monkeypatch.setattr(sim, "_update_overlap_evaporation_diagnostic", lambda flux: None)
    monkeypatch.setattr(sim, "_update_extraction_completeness_diagnostic", lambda: None)
    monkeypatch.setattr(sim, "_evap_plane_selectivity_diagnostic", lambda flux: {})
    monkeypatch.setattr(sim, "_compute_fe_redox_split_diagnostic", lambda: {})
    monkeypatch.setattr(sim.campaign_mgr, "check_endpoint", lambda *args: False)
    monkeypatch.setattr(sim, "_make_snapshot", lambda: SimpleNamespace())
    monkeypatch.setattr(sim, "_oxygen_total_kg", lambda: 0.0)
    monkeypatch.setattr(sim, "is_complete", lambda: False)

    sim.step()

    # 2026-07-02 SSO-R ch2e ratified tick order: reduction producers
    # (shuttle) run FIRST so the native split and equilibrium see the
    # dosed fO2; the old pre-shuttle equilibrium call is gone (the
    # shuttle derives from committed transitions, not a pre-equilibrium).
    # The INVARIANT this test guards — equilibrium refreshed after the
    # shuttle and before evaporation — is unchanged.
    assert calls == [
        "shuttle",
        "equilibrium_post_shuttle",
        "evaporation",
    ]
    assert calls.index("equilibrium_post_shuttle") > calls.index("shuttle")
    assert calls.index("evaporation") > calls.index("equilibrium_post_shuttle")


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


def test_v1c_janaf_alkali_shuttle_crossovers_are_documented():
    provider = BuiltinMetallothermicStepProvider

    assert provider._crossover_temperature_C("K", "Fe") == pytest.approx(
        832.0, abs=_CROSSOVER_TOL_C
    )
    assert provider._crossover_temperature_C("Na", "Fe") == pytest.approx(
        1173.4, abs=_CROSSOVER_TOL_C
    )
    assert provider._crossover_temperature_C("Na", "Cr") is None
    assert provider._crossover_temperature_C("Na", "Ti") is None


@pytest.mark.parametrize("liquid_fraction", [None, 0.25])
def test_na_shuttle_reduces_feo_to_fe_atom_balanced(liquid_fraction):
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
            "liquid_fraction": liquid_fraction,
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
    # Na2O is melt-resident but provenance-isolated from feedstock recovery.
    assert proposal.credits[SPENT_REDUCTANT_RESIDUE_ACCOUNT]["Na2O"] == pytest.approx(
        mol_feo_reduced
    )
    assert "Na2O" not in proposal.credits.get("process.reagent_inventory", {})
    assert proposal.credits["process.metal_phase"]["Fe"] == pytest.approx(
        mol_feo_reduced
    )
    _atom_check(proposal, sim.species_formula_registry, tol=1e-12)


def test_na_shuttle_spent_residue_fills_solubility_cap_across_ticks():
    sim = _build_provider_sim()
    provider = BuiltinMetallothermicStepProvider()
    accounts = {
        "process.cleaned_melt": {"FeO": _kg_to_mol("FeO", 100.0)},
        "process.metal_phase": {},
        "process.reagent_inventory": {"Na": _kg_to_mol("Na", 100.0)},
        SPENT_REDUCTANT_RESIDUE_ACCOUNT: {},
    }

    def dispatch():
        return provider.dispatch(
            IntentRequest(
                intent=ChemistryIntent.METALLOTHERMIC_STEP,
                account_view=ProviderAccountView(
                    accounts=accounts,
                    species_formula_registry=sim.species_formula_registry,
                ),
                temperature_C=1150.0,
                pressure_bar=1e-6,
                control_inputs={
                    "reaction_family": REACTION_FAMILY_C3_NA,
                    "na_target_stage": "feo_cleanup",
                    "reagent_available_kg": 100.0,
                    "liquid_fraction": 1.0,
                    "dt_hr": 1.0,
                },
            )
        )

    first = dispatch()
    assert first.status == "ok"
    assert first.transition is not None
    assert first.transition.credits[SPENT_REDUCTANT_RESIDUE_ACCOUNT]["Na2O"] > 0.0
    _apply_mol_proposal(accounts, first.transition)

    second = dispatch()

    assert second.transition is None
    assert second.status == "ok"
    assert "Na2O above 10 wt% solubility limit" in second.diagnostic["reason_skipped"]


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
    assert broadened._c3_alkali_credit_drawn_kg_by_species == {}
    assert broadened._c3_alkali_credit_outstanding_kg_by_species() == {}
    assert broadened_fe > k_only_fe
    assert increment > 0.005
    assert na_fe > 1.0
    assert max(s.temperature_C for s in shuttle_snapshots) < 1200.0
    assert _max_mass_balance_pct(k_only) < MASS_BALANCE_MAX_PCT
    assert _max_mass_balance_pct(broadened) < MASS_BALANCE_MAX_PCT
    assert _cumulative_transition_imbalance_kg(broadened) < 1e-6
    assert round(k_only_recovery, 10) < round(broadened_recovery, 10)


def test_s1c_step_shuttle_recycles_condensed_na_into_reagent_inventory():
    """S1c (2026-05-27, post-0.5.0): intra-C3 self-re-flux. The shuttle
    used to recover condensed alkali only at start-of-phase (per
    `_init_shuttle_inventory`); intra-cycle the alkali that recondensed
    on the train sat idle. CLAUDE.md §4 says ``the same Na inventory
    amplifies across multiple batches before final recovery`` -- read
    across the inject/bakeout sub-phases within a single C3 phase as
    well. This test exercises the hook directly: seed the
    condensation train with Na, place the sim in C3_NA, call
    ``_step_shuttle()`` once, and assert the Na transferred back into
    ``process.reagent_inventory`` (via the canonical
    ``_transfer_condensed_species`` helper).
    """
    sim = _build_provider_sim()
    # Seed the condensation train with 5 kg of recovered Na, as if the
    # previous bakeout tick had condensed it back onto the train.
    # ``load_external`` takes species_kg (kg) directly, not mol.
    seeded_kg = 5.0
    sim.atom_ledger.load_external(
        "process.condensation_train",
        {"Na": seeded_kg},
        source="s1c test seed",
    )
    # Park the sim at the start of a C3_NA tick.  The cool-window
    # Na/Fe margin is positive at 1150 °C (post-V1c-JANAF), so the
    # shuttle inject path is reachable.
    sim.melt.campaign = CampaignPhase.C3_NA
    sim.record.path = "A_staged"
    sim.melt.temperature_C = 1150.0
    sim.melt.hour = 24
    sim.melt.campaign_hour = 1

    # Pre-condition: train holds the seed, reagent_inventory empty.
    pre_train_kg = sim.atom_ledger.kg_by_account(
        "process.condensation_train").get("Na", 0.0)
    pre_reagent_kg = sim.atom_ledger.kg_by_account(
        "process.reagent_inventory").get("Na", 0.0)
    assert pre_train_kg == pytest.approx(seeded_kg, rel=1e-9)
    assert pre_reagent_kg == pytest.approx(0.0, abs=1e-12)

    # Total Na atoms in the system pre-tick (across all accounts):
    # elemental Na + Na bound up in Na2O (the feedstock starts with
    # some Na2O in the silicate melt). ``kg_by_account()`` with no
    # args returns the full ``{account: {species: kg}}`` mapping.
    na_in_na2o_per_kg = MOLAR_MASS["Na"] * 2 / MOLAR_MASS["Na2O"]
    pre_full = sim.atom_ledger.kg_by_account()
    pre_total_na_kg = sum(
        balances.get("Na", 0.0)
        + balances.get("Na2O", 0.0) * na_in_na2o_per_kg
        for balances in pre_full.values()
    )

    sim._step_shuttle()

    # Post-condition: train drained back to 0 (the recycle moved the
    # condensed Na out of condensation_train). Reagent_inventory holds
    # the residual that was NOT consumed by this tick's FeO reduction
    # (the feedstock provides residual FeO via the default melt
    # composition; the shuttle inject path is positive-margin at 1150 °C
    # so SOME of the recycled Na is consumed in the same tick).
    post_train_kg = sim.atom_ledger.kg_by_account(
        "process.condensation_train").get("Na", 0.0)
    post_reagent_kg = sim.atom_ledger.kg_by_account(
        "process.reagent_inventory").get("Na", 0.0)
    assert post_train_kg == pytest.approx(0.0, abs=1e-9), (
        "S1c recycle should drain the condensation_train Na completely "
        f"(saw {post_train_kg!r})"
    )
    # The recycle DID happen (reagent saw the move); the strict
    # invariant is that the train is empty + total Na is conserved.
    assert post_reagent_kg > 0.0, (
        "S1c recycle should leave at least some Na in reagent_inventory "
        "(any consumption by inject must be <= the seed)"
    )

    # Mass conservation: total Na atoms across all accounts unchanged
    # after the tick. The recycle is a within-system move (train ->
    # reagent_inventory); inject moves Na out of reagent_inventory and
    # into the melt-resident spent-reductant residue as Na2O, so the Na
    # ATOM count is conserved across (Na + the Na portion of Na2O).
    # Within FP tolerance.
    post_full = sim.atom_ledger.kg_by_account()
    post_total_na_kg = sum(
        balances.get("Na", 0.0)
        + balances.get("Na2O", 0.0) * na_in_na2o_per_kg
        for balances in post_full.values()
    )
    assert post_total_na_kg == pytest.approx(pre_total_na_kg, rel=1e-9), (
        f"S1c recycle/inject combo must conserve Na atoms; "
        f"pre={pre_total_na_kg!r} post={post_total_na_kg!r}"
    )

    # Atom-balance + cumulative imbalance still tight (the move
    # transition is mass-conserving by construction).
    assert _cumulative_transition_imbalance_kg(sim) < 1e-9
