import shlex

import pytest

from simulator.session_cli import SessionScriptRunner
from simulator.state import PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS, STOICH_RATIOS


FEEDSTOCK = "lunar_mare_low_ti"
NA_DOSE_KG = 12.0
HOT_HOLD_C = 1750.0
MASS_BALANCE_MAX_PCT = 5e-12
# 2026-07-02 SSO-R ch1(+1c): conserved fO2 integrator + Kress91 isochemical
# T re-referencing replace the per-tick intrinsic-fO2 heuristic re-seed.
# Staged wall SiO shifts -7.1% (2.9403e-4 -> 2.7316e-4); correction-class.
# 2026-07-02 SSO-R ch2c(+fix): evaporative O-loss/metal-loss source terms —
# alkali bakeout now self-oxidizes the melt (metal vapor leaves, O stays),
# raising fO2 into the staged run and suppressing SiO release; wall SiO
# 2.7316e-4 -> 2.0656e-4 (-24%). Correction-class (the lever interaction
# the model was missing).
STAGED_REACTIVE_SIO_WALL_DEPOSIT_KG = 0.0002065561796897695


def _run_script(lines: list[str]):
    runner = SessionScriptRunner()
    for line in lines:
        runner.execute(shlex.split(line), line)
    return runner.session._sim


def _run_staged():
    return _run_script([
        (
            f"start --feedstock={FEEDSTOCK} --campaign=C2A_staged "
            f"--additive=Na={NA_DOSE_KG}"
        ),
        f"adjust campaign_override C2A_staged hold_temp_C {HOT_HOLD_C}",
        "advance 30",
    ])


def _run_continuous():
    return _run_script([
        (
            f"start --feedstock={FEEDSTOCK} --campaign=C2A_continuous "
            f"--additive=Na={NA_DOSE_KG}"
        ),
        "advance 30",
    ])


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


def _staged_metrics(sim) -> tuple[float, ...]:
    products = sim.product_ledger()
    initial_fe = _fe_element_kg(sim.record.snapshots[0].inventory.raw_components_kg)
    shuttle_fe = sim.atom_ledger.kg_by_account("process.metal_phase").get("Fe", 0.0)
    sio_stage = sim.train.stages[3].collected_kg
    return (
        round(products.get("Fe", 0.0) / initial_fe, 8),
        round(shuttle_fe, 8),
        round(sio_stage.get("Si", 0.0) + sio_stage.get("SiO2", 0.0), 8),
        round(_max_mass_balance_pct(sim), 16),
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Premise (thermal Fe plateaus at an ~86% ceiling that the Na/K shuttle then "
        "breaks) is an artifact of the OLD forward-Euler evaporation integrator, which "
        "over-extracted by dumping the whole pool when flux*dt>pool. The sub-tick "
        "analytic depletion (2026-05-21, merged b84a4af) is PHYSICS-CORRECT (codex "
        "review b2ndlns3w: no regression, conservation proven, integration-only) and "
        "shows there is NO real thermal ceiling -- with enough dwell FeO depletes "
        "toward ~0. That full-depletion is itself UNPHYSICAL because the evaporation "
        "flux is not gated on liquidus/liquid_fraction (pre-existing model gap: "
        "core.py:3487/3500 gate only on campaign+temperature; AlphaMELTS liquidus is "
        "diagnostic-only, core.py:2026-2028): as Na2O/K2O/SiO2/FeO (melting-point "
        "depressants) boil off, liquidus rises and at fixed furnace T the pot freezes, "
        "trapping a residual floor. Re-enabling this acceptance requires the "
        "liquidus-gating follow-up chunk (gate flux on SILICATE_LIQUIDUS/"
        "liquid_fraction before EVAPORATION_FLUX) to establish the real freezing-floor "
        "residual and the shuttle's residual-clearing role. Do NOT retune to ~100% or "
        "to the floorless finite-cut value -- both are artifacts."
    ),
)
def test_c2a_staged_recipe_separates_products_and_k_shuttle_breaks_fe_ceiling():
    sim = _run_staged()
    products = sim.product_ledger()
    initial_fe = _fe_element_kg(sim.record.snapshots[0].inventory.raw_components_kg)
    shuttle_fe = sim.atom_ledger.kg_by_account("process.metal_phase").get("Fe", 0.0)
    thermal_fe = products.get("Fe", 0.0) - shuttle_fe

    alkali_snapshots = [
        s for s in sim.record.snapshots
        if s.campaign.name == "C2A_STAGED" and s.temperature_C <= 1250.0
    ]
    sio_snapshots = [
        s for s in sim.record.snapshots
        if (
            s.campaign.name == "C2A_STAGED"
            and 1250.0 < s.temperature_C < 1700.0
        )
    ]
    hot_snapshots = [
        s for s in sim.record.snapshots
        if s.campaign.name == "C2A_STAGED" and s.temperature_C >= HOT_HOLD_C
    ]
    shuttle_snapshots = [
        s for s in sim.record.snapshots
        if s.shuttle_phase == "inject" and s.shuttle_metal_produced_kg_hr > 0.0
    ]

    alkali_kg = sum(
        s.evap_flux.species_kg_hr.get("Na", 0.0)
        + s.evap_flux.species_kg_hr.get("K", 0.0)
        for s in alkali_snapshots
    )
    sio_kg = sum(s.evap_flux.species_kg_hr.get("SiO", 0.0)
                 for s in sio_snapshots)
    hot_fe_kg = sum(s.evap_flux.species_kg_hr.get("Fe", 0.0)
                    for s in hot_snapshots)

    assert sim.is_complete()
    assert sim.record.additives_kg["Na"] == pytest.approx(NA_DOSE_KG)
    assert sim.campaign_mgr.overrides["C2A_staged"]["hold_temp_C"] == pytest.approx(
        HOT_HOLD_C
    )
    assert alkali_kg > 3.0
    assert sio_kg > 3.0
    assert hot_fe_kg > 100.0
    assert 0.84 <= thermal_fe / initial_fe <= 0.90
    assert products.get("Fe", 0.0) / initial_fe >= 0.90
    assert shuttle_fe > 10.0
    assert max(s.temperature_C for s in shuttle_snapshots) < 1200.0
    assert _max_mass_balance_pct(sim) < MASS_BALANCE_MAX_PCT
    assert _cumulative_transition_imbalance_kg(sim) < 1e-6


def test_c2a_staged_is_deterministic_and_keeps_sio_stage_capture():
    first = _run_staged()
    second = _run_staged()
    continuous = _run_continuous()

    assert _staged_metrics(first) == _staged_metrics(second)

    staged_products = first.product_ledger()
    continuous_products = continuous.product_ledger()
    staged_sio_stage = first.train.stages[3].collected_kg
    continuous_sio_stage = continuous.train.stages[3].collected_kg
    staged_silica = staged_sio_stage.get("Si", 0.0) + staged_sio_stage.get(
        "SiO2", 0.0
    )
    continuous_silica = continuous_sio_stage.get(
        "Si", 0.0
    ) + continuous_sio_stage.get("SiO2", 0.0)

    # 2026-06-28 alpha-series source model: the staged-vs-continuous Fe ordering
    # flipped, but NOT because of evaporation transport (an earlier draft of this
    # comment wrongly attributed it to "Fe source flux continuum/melt-resistance
    # limited"). Both modes' Fe product is dominated by the Na/K metallothermic
    # shuttle (FeO -> Fe-0 in the sub-1200 C window), not Fe vapor. Probe (2026-06-29):
    # continuous peaks at ~850 C -- below the Fe-evaporation window -- with zero
    # Fe-evap and 100% shuttle Fe (metal_phase 14.69 kg); staged ramps to 1750 C
    # and is ~99% shuttle (11.91 kg) + ~0.12 kg evap. Continuous dwells longer in
    # the shuttle-reduction window than staged's scheduled ramp, so it reduces more
    # FeO -> more Fe product at this dwell. This is a shuttle-dwell effect, not the
    # alpha-series evaporation change.
    assert continuous_products.get("Fe", 0.0) > staged_products.get("Fe", 0.0)
    # Na product is now mode-independent: the alpha-series transport limit suppresses
    # main-extraction Na evaporation ~285x in BOTH modes, collapsing the staged>continuous
    # Na gap that existed under bare-alpha to machine equality (probe delta ~2e-16).
    # Assert equality, not the prior float-dust inequality.
    assert staged_products.get("Na", 0.0) == pytest.approx(
        continuous_products.get("Na", 0.0), rel=1e-9
    )
    assert staged_silica > continuous_silica
    # Builtin-authoritative vapor pressure makes Stage 3 a mixed SiO/Fe
    # hot-trap instead of the old VapoRock-dominant silica-purity surface.
    # Keep the recipe invariant honest: staged mode must create material
    # Stage 3 silica capture while the continuous warmup path captures none.
    # D4 first grounded SiO alpha_s below the legacy 0.7; the current
    # Wetzel/Gail alpha_s(T) keeps the capture threshold grounded while staged
    # remains nonzero and richer than continuous. SSO-R Phase 1 (coupled
    # melt<->headspace O2 exchange)
    # lowers it further: the melt sheds O2 to the headspace, raising transport
    # pO2 and suppressing SiO release via p(SiO) ~ 1/sqrt(pO2) (staged_silica
    # ~0.078 -> ~0.048), so this floor moves 0.05 -> 0.04 (still above the
    # grounded k_O-clamp envelope; verified not test-forcing in 2026-06-28 review).
    # Alpha-series source resistance lowers staged_silica again to ~0.00103 kg.
    # Grounded alpha_s(T) then lowers the cold/staged surface capture to
    # ~1.4e-4 kg and makes Stage 3 Fe/Mg-heavy; redox v3 Step C pushes the
    # fixed-alpha mix slightly further Fe/Mg-rich. Keep the silica fraction
    # nonzero and above the continuous warmup path without preserving the old mix.
    staged_fe_mg = sum(staged_sio_stage.get(s, 0.0) for s in ("Fe", "Mg"))
    staged_stage3_total = staged_silica + staged_fe_mg
    assert staged_silica > 1e-4
    assert staged_stage3_total > staged_silica
    assert staged_silica / staged_stage3_total > 0.02


def test_c2a_staged_pipework_has_no_upstream_cold_spot():
    sim = _run_staged()
    cold_spot_history = list(
        getattr(sim.condensation_model, "cold_spot_history", []) or []
    )
    warnings = [
        warning
        for diagnostic in cold_spot_history
        for warning in diagnostic.get("warnings", [])
    ]
    segment_wall_sio_kg = sum(
        sum(
            sim.atom_ledger.kg_by_account(account).get(species, 0.0)
            for species in ("SiO", "Si", "SiO2", "FeSi")
        )
        for account in PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS
    )

    assert warnings == []
    # No upstream cold spot is still the routing invariant. The 2026-06-29
    # reactive SiO wall-product fix keeps the expected wall magnitude nonzero.
    # 2026-06-30 cold-wall SiO uses the grounded Pound 1972 unity condensation
    # gate below the Wetzel/Gail evaporation-Arrhenius validity floor.
    # 2026-07-01 C4b stores the wall deposit as Si/SiO2/FeSi products, not SiO.
    assert segment_wall_sio_kg == pytest.approx(
        STAGED_REACTIVE_SIO_WALL_DEPOSIT_KG,
        rel=1e-9,
    )
    assert _max_mass_balance_pct(sim) < MASS_BALANCE_MAX_PCT
