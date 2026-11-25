import shlex
from dataclasses import replace
from unittest.mock import patch

import pytest

from simulator.core import FERRIC_DIVERGENCE_WARNING_THRESHOLD
from simulator.evaporation import EvaporationFluxRefusal
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
# 2026-07-18 a91db36 repaired staged trajectory rebaseline on corrected tip
# 0990232.
# 2026-07-21 B1 wall-gate fix: with the melt at the effervescence cap the
# staged C2A segment-wall sum was zero. The intentional cold-segment
# case still lives in test_intentional_pipe_cold_spot_flags_and_changes_wall_deposit.
# 2026-11-25 d-472 amended remeasure (studio 09c22d2f): congruent
# evaporation has no fO2 tangent, so melt fO2 stays on the Fe metal line
# instead of log10(P_total). Metal-vapour p ∝ fO2^(-n), and this sum
# (SiO + Si + SiO2 + FeSi over the pipe-segment wall accounts) is the
# measured load. Each species is pinned on its own below. The dominant
# species and its segment are filled from this tree's staged receipt.
STAGED_REACTIVE_SIO_WALL_DEPOSIT_KG = 0.14929136844379295
STAGED_WALL_DEPOSIT_KG_BY_SPECIES = {
    "SiO": 0.0,
    "Si": 0.0,
    "SiO2": 0.0,
    "FeSi": 0.0,
}
_WALL_DEPOSIT_SPECIES = tuple(STAGED_WALL_DEPOSIT_KG_BY_SPECIES)


def _run_script(lines: list[str]):
    runner = SessionScriptRunner()
    for line in lines:
        runner.execute(shlex.split(line), line)
    return runner.session._sim


def _complete_recommended_path(
    runner: SessionScriptRunner,
) -> EvaporationFluxRefusal | None:
    # 2026-07-22 B1: advance regardless of a pending decision — the composed
    # canonical path (C4 window + 160 h final C2A + C6-continue) leaves the
    # sim mid-campaign between decisions, and the old early-return on
    # decision-is-None abandoned the run incomplete. 30 + 8x96 h covers the
    # full staged path with headroom.
    for _ in range(8):
        sim = runner.session._sim
        if sim.is_complete():
            return None
        decision = sim.pending_decision
        if decision is not None:
            sim.apply_decision(decision.decision_type, decision.recommendation)
        try:
            runner.execute(shlex.split("advance 96"), "advance 96")
        except EvaporationFluxRefusal as exc:
            return exc
    return None


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
        refusal = _complete_recommended_path(runner)
        return runner.session._sim, refusal
    return runner.session._sim


def _run_continuous():
    return _run_script([
        (
            f"start --feedstock={FEEDSTOCK} --campaign=C2A_continuous "
            f"--additive=Na={NA_DOSE_KG}"
        ),
        # SC-109 requires an armed depletion signal before the C2A soft exit, so
        # hour 30 is now the C2A->C3 boundary.  Advance through two complete
        # three-hour C3 shuttle bursts; otherwise no metallothermic tick runs.
        "advance 39",
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


# gate-2: module fixture advances the recommended staged path until the first
# typed transitional-Kn refusal; ceiling covers fixture setup.
# Nightly (2026-08-02 CI tiering): staged bakeout C2A campaign (~255 s junit).
@pytest.mark.nightly
@pytest.mark.xdist_group("magemin_fullrun_b")
@pytest.mark.cpu_budget(3600)
def test_c2a_staged_k_shuttle_and_conservation_remain_visible(
    staged_ceiling_case,
):
    sim, refusal = staged_ceiling_case
    shuttle_fe = _metal_phase_fe_kg(sim)
    shuttle_snapshots = [
        s for s in sim.record.snapshots
        if s.shuttle_phase == "inject" and s.shuttle_metal_produced_kg_hr > 0.0
    ]
    shuttle_produced_kg = sum(
        snapshot.shuttle_metal_produced_kg_hr for snapshot in shuttle_snapshots
    )

    # The full staged path completes on green; keep this acceptance focused on
    # the shuttle and conservation evidence named by the test.
    assert sim.is_complete()
    assert refusal is None
    assert sim.record.additives_kg["Na"] == pytest.approx(NA_DOSE_KG)
    assert sim.campaign_mgr.overrides["C2A_staged"]["hold_temp_C"] == pytest.approx(
        HOT_HOLD_C
    )
    assert shuttle_snapshots
    assert shuttle_produced_kg > 10.0
    assert shuttle_fe > 10.0
    assert max(s.temperature_C for s in shuttle_snapshots) < 1200.0
    assert _max_mass_balance_pct(sim) < MASS_BALANCE_MAX_PCT
    assert _cumulative_transition_imbalance_kg(sim) < 1e-6


# Deleted 2026-07-24 (CI-audit finding 2): a strict-xfail test
# (`test_c2a_staged_thermal_fe_ceiling_pending_liquid_fraction_gate`)
# asserted that thermal Fe plateaus at an ~86% ceiling the Na/K shuttle
# then breaks — a premise the OLD forward-Euler evaporation integrator
# produced, retired with it. As an ungrouped test it independently
# rebuilt the ~1700 s `staged_ceiling_case` module fixture on a second
# xdist worker while never enforcing anything (expected-fail). If a
# liquid-fraction-gated freezing-floor residual lands (the re-enabling
# condition its reason named), write a NEW acceptance against that
# physics rather than resurrecting the old ceiling; do not retune.


def test_c2a_staged_is_deterministic_and_keeps_sio_stage_capture():
    first = _run_staged()
    second = _run_staged()
    continuous = _run_continuous()

    assert _staged_metrics(first) == _staged_metrics(second)

    staged_products = first.product_ledger()
    continuous_products = continuous.product_ledger()
    staged_sio_stage = first.train.stages[3].collected_kg
    continuous_sio_stage = continuous.train.stages[3].collected_kg
    from tests.studio_pin_support import staged_pin_values

    pins = staged_pin_values(first)
    staged_silica = pins["staged_silica_kg"]
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
    # The repaired 510 -> 860 C continuous trajectory no longer dwells in the
    # staged high-temperature Na recovery window.
    assert staged_products.get("Na", 0.0) > continuous_products.get("Na", 0.0) > 0.0
    # 2026-07-21 B1 wall-gate fix: "Stage 3 remains Fe-only / Si goes to the
    # wall" was the unguarded hot-wall backstop artifact. With hot walls
    # honest (zero deposit above T_cond), the staged probe's Si-bearing
    # material reaches the DESIGNATED Stage 3 trap as disproportionation
    # products; the continuous probe still never reaches the hot capture
    # window. Values pinned from the executable under the quiesced gate.
    # The 2026-08-02 pins persisted through an f7bcbf79c..b24e17095
    # transport excursion (fabricated 100 Pa -> refusal -> flagged
    # extrapolation; net +0.0097%), then 82599cf81 (-0.17%), 03616bb20
    # (-0.98%), and b99fc004f (-32%). D066's package change is separate:
    # silica 0.06935019686949548 -> 0.06889793680807693 and product SiO
    # 0.0077315513914454756 -> 0.007681113790282872 (-0.652%). The current
    # pins match the Studio report emitted from this same _run_staged helper.
    assert staged_silica == pytest.approx(0.006596004558853442, rel=1e-9)
    assert continuous_silica == pytest.approx(0.0, abs=1.0e-15)
    assert set(staged_sio_stage) == {"Si", "SiO2", "Fe"}
    assert staged_sio_stage["Fe"] > 0.0
    assert staged_sio_stage["Si"] + staged_sio_stage["SiO2"] == pytest.approx(
        staged_silica, rel=1e-12
    )
    assert continuous_sio_stage == {}
    assert pins["staged_product_sio_kg"] == pytest.approx(0.0)


def test_c2a_staged_does_not_ferricize_from_evaporation():
    sim = _run_staged()

    divergence = sim.melt.oxygen_reservoir.ferric_divergence

    # Evaporation coproduct is overhead gas the sweep removes. A credit
    # of that O2 to reservoir.fo2_buffer is the inert-sweep oxidation
    # bug. Native-Fe tap O2 can still fund a metal-line ferric; that
    # inventory is closed-system saturation, not this posting, so the
    # Fe2O3 mass is not pinned here.
    for transition in sim.atom_ledger.transitions:
        if not str(transition.name).startswith("evaporate_"):
            continue
        for lot in transition.credits:
            assert lot.account != "reservoir.fo2_buffer"
    cap = sim._melt_redox_fO2_upper_bound_log()
    fo2 = float(sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log)
    # The bug pinned melt fO2 on the effervescence cap, log10(P_total).
    # One dex under that cap is the same separation the hour invariant
    # uses against headspace fugacity. It is not a measured yield.
    assert fo2 <= cap - 1.0
    assert (
        divergence["delta_abs"] <= FERRIC_DIVERGENCE_WARNING_THRESHOLD
        or divergence.get("attribution") in {
            "managed_floor_unbacked",
            "sub_liquid_respeciation_deferred",
        }
    )


def _pipe_segment_wall_kg(sim):
    """Kg of each fouling species on each pipe-segment wall account."""

    prefix = "process.wall_deposit_segment_"
    by_species = {species: 0.0 for species in _WALL_DEPOSIT_SPECIES}
    by_segment = {}
    for account in PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS:
        segment = account[len(prefix):] if account.startswith(prefix) else account
        kg = sim.atom_ledger.kg_by_account(account)
        species_kg = {}
        for species in _WALL_DEPOSIT_SPECIES:
            amount = float(kg.get(species, 0.0) or 0.0)
            by_species[species] += amount
            if amount:
                species_kg[species] = amount
        if species_kg:
            by_segment[segment] = species_kg
    return by_species, by_segment


def _dominant_wall_deposit(sim, by_segment):
    """The heaviest (segment, species) deposit, with that pipe's wall temperature.

    Stage 3 is the designated SiO condenser. ``stage_N_to_stage_M`` is
    upstream of that condenser when M <= 3. A pipe that leaves stage 3
    (``stage_3_to_stage_4``) is the condenser-adjacent segment.
    """

    dominant = None
    for segment, species_kg in by_segment.items():
        for species, amount in species_kg.items():
            if dominant is None or amount > dominant["kg"]:
                dominant = {
                    "segment": segment,
                    "species": species,
                    "kg": amount,
                }
    if dominant is None:
        return {"segment": "", "species": "", "kg": 0.0}
    temperatures = {
        segment.name: float(segment.wall_temperature_C)
        for segment in sim.condensation_model.pipe_segments
    }
    name = dominant["segment"]
    dominant["wall_temperature_C"] = temperatures.get(name)
    return dominant


def test_c2a_staged_pipework_flags_upstream_cold_spots():
    sim = _run_staged()
    cold_spot_history = list(
        getattr(sim.condensation_model, "cold_spot_history", []) or []
    )
    warnings = [
        warning
        for diagnostic in cold_spot_history
        for warning in diagnostic.get("warnings", [])
    ]
    by_species, by_segment = _pipe_segment_wall_kg(sim)
    segment_wall_sio_kg = sum(by_species.values())
    dominant = _dominant_wall_deposit(sim, by_segment)

    # d-402 evaluates pO2-free wall bounds for reactive vapours, so cold
    # upstream surfaces are reported instead of being hidden by a refusal.
    assert warnings
    assert any("CrO sees 1800.0 C before stage 2" in warning for warning in warnings)
    # The upstream cold-spot reports do not change the routed wall product.
    # 2026-06-30 cold-wall SiO uses the grounded Pound 1972 unity condensation
    # gate below the Wetzel/Gail evaporation-Arrhenius validity floor.
    # 2026-07-01 C4b stores the wall deposit as Si/SiO2/FeSi products, not SiO.
    # Species pins and the dominant segment are the measured fouling mass.
    # The dominant-species sentence is written from this tree's receipt.
    assert by_species == pytest.approx(
        STAGED_WALL_DEPOSIT_KG_BY_SPECIES,
        abs=1e-15,
    ), {"by_species_kg": by_species, "by_segment_kg": by_segment, "dominant": dominant}
    assert segment_wall_sio_kg == pytest.approx(
        STAGED_REACTIVE_SIO_WALL_DEPOSIT_KG,
        abs=1e-15,
    ), {"by_species_kg": by_species, "by_segment_kg": by_segment, "dominant": dominant}
    assert _max_mass_balance_pct(sim) < MASS_BALANCE_MAX_PCT
