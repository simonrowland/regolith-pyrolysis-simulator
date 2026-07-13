import shlex
from dataclasses import replace
from unittest.mock import patch

import pytest

from simulator.core import FERRIC_DIVERGENCE_WARNING_THRESHOLD
from simulator.session_cli import SessionScriptRunner
import simulator.session_cli as session_cli_module
from simulator.state import PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS, STOICH_RATIOS


FEEDSTOCK = "lunar_mare_low_ti"
NA_DOSE_KG = 12.0
HOT_HOLD_C = 1750.0
MASS_BALANCE_MAX_PCT = 5e-12
METAL_PHASE_FE_ACCOUNTS = (
    "process.metal_phase",
    "process.metal_phase_bottom_pool",
    "process.metal_phase_float_layer",
)
# 2026-07-02 SSO-R ch1(+1c): conserved fO2 integrator + Kress91 isochemical
# T re-referencing replace the per-tick intrinsic-fO2 heuristic re-seed.
# Staged wall SiO shifts -7.1% (2.9403e-4 -> 2.7316e-4); correction-class.
# 2026-07-02 SSO-R ch2c(+fix): evaporative O-loss/metal-loss source terms —
# alkali bakeout now self-oxidizes the melt (metal vapor leaves, O stays),
# raising fO2 into the staged run and suppressing SiO release; wall SiO
# 2.7316e-4 -> 2.0656e-4 (-24%). Correction-class (the lever interaction
# the model was missing).
# 2026-07-02 re-speciation (#82): micro-drift through the coupled route.
# 2026-07-03 LIVE-PO2-SWEEP (#94): sweep-transport pO2 no longer sees
# pre-bleed holdup O2 during vapor dispatch, so the staged C2A run's SiO
# release (and hence wall deposit) roughly doubles (2.0656e-4 -> 4.7792e-4).
# Correction-class: the old pin encoded the holdup-O2 suppression.
# 2026-07-05 CF-2-lite (t-001): Si(l) Ellingham fit extended to ~2200 K (covers the
# 1750-1800 C staged recipe) shifts the SiO wall deposit +2.11e-10 kg (+4.4e-5%);
# JANAF-grounded physics change, verified controller-side, not a behaviour regression.
# 2026-07-06 CF-3: constant single-cation gamma*X alkali activity lowers
# Na/K vapor, shifting the coupled SiO wall trace while mass balance remains
# closed.
# 2026-07-07 t-141 L&H K standard-term regen: reactive SiO wall deposit
# shifts -1.3605e-7 kg via the K-coupled headspace path (delta matches
# docs-private/research/2026-07-07-t141-kmox/golden-deltas.json).
# 2026-07-11 0.5.10 E-MOVE: phase-basis/two-rail vapor plus K/S fO2 and
# alkali-path changes lower the staged reactive wall deposit.
# 2026-07-11 wave-08 accounting-closure: the old wall-capture pseudo-efficiency
# divided a reference HKL flux into the deposited flux and folded area into a
# dimensionally invalid exponential pseudo-rate; the closed form converts the
# deposited molar wall flux directly (kg/h = J[mol/m2/s] * A[m2] * M[kg/mol]
# * 3600[s/h], per reachable segment, supply-capped). Recomputed by rerun on
# main; mechanism attribution:
# docs-private/reviews/2026-07-11-wave08/runtime-golden-attribution.md
# +1.98e-6 kg (0.0035%) on top of the attribution's worktree value from the
# native-Fe metallic tap fold (shared condensation-train competition).
STAGED_REACTIVE_SIO_WALL_DEPOSIT_KG = 0.056588484701026474


def _run_script(lines: list[str]):
    runner = SessionScriptRunner()
    for line in lines:
        runner.execute(shlex.split(line), line)
    return runner.session._sim


def _complete_recommended_path(runner: SessionScriptRunner) -> None:
    for _ in range(8):
        sim = runner.session._sim
        if sim.is_complete():
            return
        decision = sim.pending_decision
        if decision is None:
            return
        sim.apply_decision(decision.decision_type, decision.recommendation)
        runner.execute(shlex.split("advance 96"), "advance 96")


def _run_staged(*, complete: bool = False):
    runner = SessionScriptRunner()
    for line in [
        (
            f"start --feedstock={FEEDSTOCK} --campaign=C2A_staged "
            f"--additive=Na={NA_DOSE_KG}"
        ),
        f"adjust campaign_override C2A_staged hold_temp_C {HOT_HOLD_C}",
        "advance 30",
    ]:
        if line.startswith("start "):
            original_load_config_bundle = session_cli_module.load_config_bundle

            def load_config_bundle_with_alpha_fallback(*args, **kwargs):
                bundle = original_load_config_bundle(*args, **kwargs)
                setpoints = dict(bundle.setpoints)
                kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
                # Pending t-194 grounded Cr/Mn alphas; alpha=1.0 prototype fallback.
                kernel_config["allow_unmeasured_alpha_fallback"] = True
                setpoints["chemistry_kernel"] = kernel_config
                return replace(bundle, setpoints=setpoints)

            with patch.object(
                session_cli_module,
                "load_config_bundle",
                load_config_bundle_with_alpha_fallback,
            ):
                runner.execute(shlex.split(line), line)
        else:
            runner.execute(shlex.split(line), line)
    if complete:
        _complete_recommended_path(runner)
    return runner.session._sim


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


def _metal_phase_fe_kg(sim) -> float:
    return sum(
        sim.atom_ledger.kg_by_account(account).get("Fe", 0.0)
        for account in METAL_PHASE_FE_ACCOUNTS
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
    shuttle_fe = _metal_phase_fe_kg(sim)
    sio_stage = sim.train.stages[3].collected_kg
    return (
        round(products.get("Fe", 0.0) / initial_fe, 8),
        round(shuttle_fe, 8),
        round(sio_stage.get("Si", 0.0) + sio_stage.get("SiO2", 0.0), 8),
        round(_max_mass_balance_pct(sim), 16),
    )


@pytest.fixture(scope="module")
def staged_ceiling_case():
    return _run_staged(complete=True)


def test_c2a_staged_k_shuttle_and_conservation_remain_visible(
    staged_ceiling_case,
):
    sim = staged_ceiling_case
    shuttle_fe = _metal_phase_fe_kg(sim)
    shuttle_snapshots = [
        s for s in sim.record.snapshots
        if s.shuttle_phase == "inject" and s.shuttle_metal_produced_kg_hr > 0.0
    ]
    shuttle_produced_kg = sum(
        snapshot.shuttle_metal_produced_kg_hr for snapshot in shuttle_snapshots
    )

    assert sim.is_complete()
    assert sim.record.additives_kg["Na"] == pytest.approx(NA_DOSE_KG)
    assert sim._c3_alkali_credit_drawn_kg_by_species == {}
    assert sim._c3_alkali_credit_outstanding_kg_by_species() == {}
    assert sim.campaign_mgr.overrides["C2A_staged"]["hold_temp_C"] == pytest.approx(
        HOT_HOLD_C
    )
    assert shuttle_snapshots
    assert shuttle_produced_kg > 10.0
    assert shuttle_fe > 10.0
    assert max(s.temperature_C for s in shuttle_snapshots) < 1200.0
    assert _max_mass_balance_pct(sim) < MASS_BALANCE_MAX_PCT
    assert _cumulative_transition_imbalance_kg(sim) < 1e-6


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Premise (thermal Fe plateaus at an ~86% ceiling that the Na/K shuttle then "
        "breaks) is an artifact of the OLD forward-Euler evaporation integrator. "
        "Re-enabling this one acceptance requires liquid-fraction-gated evaporation "
        "to establish a physical freezing-floor residual; do not retune the ceiling."
    ),
)
def test_c2a_staged_thermal_fe_ceiling_pending_liquid_fraction_gate(
    staged_ceiling_case,
):
    sim = staged_ceiling_case
    products = sim.product_ledger()
    initial_fe = _fe_element_kg(sim.record.snapshots[0].inventory.raw_components_kg)
    shuttle_fe = _metal_phase_fe_kg(sim)
    thermal_fe = products.get("Fe", 0.0) - shuttle_fe

    assert 0.84 <= thermal_fe / initial_fe <= 0.90


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
    assert continuous_products.get("Fe", 0.0) > 0.0
    assert continuous_products.get("Fe", 0.0) > staged_products.get("Fe", 0.0)
    # CF-3 constant gamma*X activity removes the old mode-equality pin: lower
    # alkali vapor makes the staged/continuous thermal histories visible again.
    # 2026-07-11 ordering corrected: the staged>continuous flip committed with
    # the v0.5.9 epoch (6d72725) was a test-history defect — live physics
    # already produced continuous > staged at that commit. Mechanism: the
    # continuous path dwells longer in the low-T shuttle/condensed-rail window
    # (same dwell effect as Fe above), and the E-08 two-rail contract removed
    # the gas-row P_sat double-count that had inflated staged high-T Na
    # (gas-standard rows use activity_root * p_standard). Attribution:
    # docs-private/reviews/2026-07-11-wave08/runtime-golden-attribution.md
    assert continuous_products.get("Na", 0.0) > staged_products.get("Na", 0.0) > 0.0
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


def test_c2a_staged_respeciates_evaporative_metal_loss_internal_o():
    sim = _run_staged()

    melt_kg = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    divergence = sim.melt.oxygen_reservoir.ferric_divergence

    assert melt_kg.get("Fe2O3", 0.0) > 0.0
    assert (
        divergence["delta_abs"] <= FERRIC_DIVERGENCE_WARNING_THRESHOLD
        or divergence.get("attribution") in {
            "managed_floor_unbacked",
            "sub_liquid_respeciation_deferred",
        }
    )


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
