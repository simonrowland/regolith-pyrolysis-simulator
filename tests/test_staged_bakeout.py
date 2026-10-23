import copy
import math
import shlex
from dataclasses import replace
from unittest.mock import patch

import pytest

from simulator.accounting.formulas import (
    ATOMIC_WEIGHTS_G_PER_MOL,
    resolve_species_formula,
)
from simulator.core import FERRIC_DIVERGENCE_WARNING_THRESHOLD
from simulator.evaporation import EvaporationFluxRefusal
from simulator.session_cli import SessionScriptRunner
import simulator.session_cli as session_cli_module
from simulator.state import PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS, STOICH_RATIOS
from simulator.transport_constants import (
    FREE_MOLECULAR_KNUDSEN_MIN,
    VISCOUS_KNUDSEN_MAX,
)


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
# 2026-07-21 B1 wall-gate fix: the staged C2A pipework is all hot-wall
# (above SiO T_cond), so the honest segment wall load is ZERO — the old
# nonzero pin was the unguarded backstop's phantom deposit. The genuinely
# cold-segment scenario (nonzero wall coverage) lives in
# test_intentional_pipe_cold_spot_flags_and_changes_wall_deposit.
STAGED_REACTIVE_SIO_WALL_DEPOSIT_KG = 0.0


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


def _run_staged(
    *,
    complete: bool = False,
    transport_diagnostics: list[tuple[int, dict]] | None = None,
):
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
            if line.startswith("advance ") and transport_diagnostics is not None:
                sim = runner.session._sim
                original_step = sim.step

                def instrumented_step():
                    snapshot = original_step()
                    diagnostic = getattr(
                        sim,
                        "_last_evaporation_flux_diagnostic",
                        {},
                    ) or {}
                    transport_diagnostics.append((
                        int(snapshot.hour),
                        {
                            "continuum_extrapolation_notice": copy.deepcopy(
                                diagnostic.get("continuum_extrapolation_notice")
                            ),
                            "evaporation_series_resistance": {
                                str(species): {
                                    key: copy.deepcopy(item[key])
                                    for key in (
                                        "knudsen_number",
                                        "gas_resistance_weight",
                                        "continuum_extrapolation_notice",
                                    )
                                    if key in item
                                }
                                for species, item in (
                                    diagnostic.get(
                                        "evaporation_series_resistance", {}
                                    ) or {}
                                ).items()
                                if isinstance(item, dict)
                            },
                        },
                    ))
                    return snapshot

                sim.step = instrumented_step
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


def _stage_product_composition_report(sim) -> dict[int, dict]:
    """Report cation purity and free-Si / (free-Si + SiO2) per stage."""
    report = {}
    for stage in sim.train.stages:
        element_mass_kg: dict[str, float] = {}
        free_si_product_kg = 0.0
        sio2_product_kg = 0.0

        for species, raw_mass_kg in stage.collected_kg.items():
            mass_kg = float(raw_mass_kg)
            formula = resolve_species_formula(
                str(species), sim.species_formula_registry
            )
            molar_mass_g_mol = float(formula.molar_mass_g_mol)
            elements = formula.elements
            species_element_mass_kg = {}
            for element, count in elements.items():
                element_mass = (
                    mass_kg
                    * float(count)
                    * ATOMIC_WEIGHTS_G_PER_MOL[element]
                    / molar_mass_g_mol
                )
                species_element_mass_kg[element] = element_mass
                element_mass_kg[element] = (
                    element_mass_kg.get(element, 0.0) + element_mass
                )

            if set(elements) == {"Si"}:
                free_si_product_kg += mass_kg
            if (
                set(elements) == {"Si", "O"}
                and math.isclose(float(elements["Si"]), 1.0)
                and math.isclose(float(elements["O"]), 2.0)
            ):
                sio2_product_kg += mass_kg

            assert sum(species_element_mass_kg.values()) == pytest.approx(
                mass_kg, rel=1e-12, abs=1e-12
            ), f"formula mass did not close for stage {stage.stage_number} {species}"

        total_deposit_kg = sum(float(mass) for mass in stage.collected_kg.values())
        total_cation_mass_kg = sum(
            mass for element, mass in element_mass_kg.items() if element != "O"
        )
        si_cation_mass_kg = element_mass_kg.get("Si", 0.0)
        non_si_cation_mass_kg = total_cation_mass_kg - si_cation_mass_kg
        assert total_cation_mass_kg == pytest.approx(
            si_cation_mass_kg + non_si_cation_mass_kg,
            rel=1e-12,
            abs=1e-12,
        )
        si_bearing_product_kg = free_si_product_kg + sio2_product_kg
        report[int(stage.stage_number)] = {
            "total_deposit_kg": total_deposit_kg,
            "element_mass_kg_by_element": dict(sorted(element_mass_kg.items())),
            "total_cation_mass_kg": total_cation_mass_kg,
            "non_si_cation_mass_kg": non_si_cation_mass_kg,
            "non_si_cation_mass_fraction": (
                non_si_cation_mass_kg / total_cation_mass_kg
                if total_cation_mass_kg > 0.0
                else None
            ),
            "free_si_product_kg": free_si_product_kg,
            "sio2_product_kg": sio2_product_kg,
            "free_si_fraction_of_free_si_plus_sio2": (
                free_si_product_kg / si_bearing_product_kg
                if si_bearing_product_kg > 0.0
                else None
            ),
        }

    return report


def _assert_stage_product_composition_contract(sim) -> dict[int, dict]:
    report = _stage_product_composition_report(sim)
    assert set(report) == {int(stage.stage_number) for stage in sim.train.stages}
    for stage in sim.train.stages:
        stage_report = report[int(stage.stage_number)]
        assert "non_si_cation_mass_fraction" in stage_report
        assert "free_si_fraction_of_free_si_plus_sio2" in stage_report
        assert sum(
            stage_report["element_mass_kg_by_element"].values()
        ) == pytest.approx(
            stage_report["total_deposit_kg"], rel=1e-12, abs=1e-12
        ), f"element mass did not close for stage {stage.stage_number}"
        if stage_report["total_cation_mass_kg"] > 0.0:
            fraction = stage_report["non_si_cation_mass_fraction"]
            assert fraction is not None
            assert fraction == pytest.approx(
                stage_report["non_si_cation_mass_kg"]
                / stage_report["total_cation_mass_kg"]
            )
            assert 0.0 <= fraction <= 1.0
        si_product_mass = (
            stage_report["free_si_product_kg"] + stage_report["sio2_product_kg"]
        )
        if si_product_mass > 0.0:
            fraction = stage_report[
                "free_si_fraction_of_free_si_plus_sio2"
            ]
            assert fraction is not None
            assert fraction == pytest.approx(
                stage_report["free_si_product_kg"] / si_product_mass
            )
            assert 0.0 <= fraction <= 1.0

    assert report[3]["non_si_cation_mass_fraction"] is not None
    assert report[3]["free_si_fraction_of_free_si_plus_sio2"] is not None
    return report


def _stage_capture_history_kg(sim, stage_number: int) -> dict[str, float]:
    # AtomLedger's condensation_train account is run-wide; hourly snapshots
    # retain the stage key needed to check a stage projection independently.
    history: dict[str, float] = {}
    for snapshot in sim.record.snapshots:
        for (number, species), mass_kg in (
            snapshot.condensed_by_stage_species_delta.items()
        ):
            if int(number) == stage_number:
                history[species] = history.get(species, 0.0) + float(mass_kg)
    return history


def _assert_stage_projection_matches_capture_history(
    stage_number: int,
    collected_kg: dict[str, float],
    capture_history_kg: dict[str, float],
) -> None:
    assert set(collected_kg) == set(capture_history_kg), (
        f"stage {stage_number} collected species differ from capture history"
    )
    for species, history_kg in capture_history_kg.items():
        assert collected_kg[species] == pytest.approx(
            history_kg, rel=1e-12, abs=1e-12
        ), f"stage {stage_number} {species} differs from capture history"


def _assert_transitional_kn_hours_are_flagged(
    transport_diagnostics: list[tuple[int, dict]],
) -> list[int]:
    flagged_hours = []
    for hour, diagnostic in transport_diagnostics:
        series = diagnostic.get("evaporation_series_resistance", {}) or {}
        transitional_species = []
        for species, item in series.items():
            if not isinstance(item, dict):
                continue
            knudsen_number = item.get("knudsen_number")
            resistance_weight = item.get("gas_resistance_weight", 0.0)
            if (
                isinstance(knudsen_number, (int, float))
                and not isinstance(knudsen_number, bool)
                and isinstance(resistance_weight, (int, float))
                and not isinstance(resistance_weight, bool)
                and VISCOUS_KNUDSEN_MAX <= float(knudsen_number)
                < FREE_MOLECULAR_KNUDSEN_MIN
                and float(resistance_weight) > 0.0
            ):
                transitional_species.append((str(species), item))
        if not transitional_species:
            continue

        flagged_hours.append(hour)
        global_notice = diagnostic.get("continuum_extrapolation_notice")
        for species, item in transitional_species:
            notice = item.get("continuum_extrapolation_notice")
            if not isinstance(notice, dict) and isinstance(global_notice, dict):
                if species in global_notice.get("affected_species", ()):
                    notice = global_notice
            assert isinstance(notice, dict), (
                f"transitional Kn at hour {hour} for {species} lacked a notice"
            )
            assert notice.get("status") == "extrapolated"
            assert notice.get("authority_level") == "extrapolated"
            assert notice.get("reason") == (
                "viscous_p_bulk_transport_out_of_domain"
            )

    assert flagged_hours, "the staged path must reach transitional-Kn transport"
    return flagged_hours


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
    transport_diagnostics = []
    sim, refusal = _run_staged(
        complete=True,
        transport_diagnostics=transport_diagnostics,
    )
    return sim, refusal, transport_diagnostics


# gate-2: module fixture advances the recommended staged path to completion;
# the transport assertions require extrapolated notices for transitional-Kn hours.
# Nightly (2026-08-02 CI tiering): staged bakeout C2A campaign (~255 s junit).
@pytest.mark.nightly
@pytest.mark.xdist_group("magemin_fullrun_b")
@pytest.mark.timeout(3600)
def test_c2a_staged_k_shuttle_and_conservation_remain_visible(
    staged_ceiling_case,
):
    sim, refusal, transport_diagnostics = staged_ceiling_case
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
    composition_report = _assert_stage_product_composition_contract(sim)
    stage3 = sim.train.stages[3]
    stage3_capture_history = _stage_capture_history_kg(sim, 3)
    _assert_stage_projection_matches_capture_history(
        3, stage3.collected_kg, stage3_capture_history
    )

    # Mutation check: removing a captured carrier from the stage projection
    # must fail against the independent per-hour capture history.
    dropped_species_projection = dict(stage3.collected_kg)
    dropped_species = max(
        dropped_species_projection, key=dropped_species_projection.get
    )
    dropped_species_projection.pop(dropped_species)
    with pytest.raises(AssertionError, match="stage 3 collected species"):
        _assert_stage_projection_matches_capture_history(
            3, dropped_species_projection, stage3_capture_history
        )

    assert composition_report[3]["total_deposit_kg"] == pytest.approx(
        sum(stage3_capture_history.values()), rel=1e-12, abs=1e-12
    )
    assert _assert_transitional_kn_hours_are_flagged(transport_diagnostics)


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


@pytest.mark.nightly
@pytest.mark.timeout(1800)
def test_c2a_staged_is_deterministic_and_keeps_sio_stage_capture():
    first = _run_staged()
    second = _run_staged()
    continuous = _run_continuous()

    assert _staged_metrics(first) == _staged_metrics(second)

    staged_products = first.product_ledger()
    continuous_products = continuous.product_ledger()
    staged_sio_stage = first.train.stages[3].collected_kg
    continuous_sio_stage = continuous.train.stages[3].collected_kg
    _assert_stage_product_composition_contract(first)
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
    # 0.0077315513914454756 -> 0.007681113790282872 (-0.652%). These pins
    # are rerun from the current 62d9d561a green.
    assert staged_silica == pytest.approx(0.06888507997517511, rel=1e-9)
    assert continuous_silica == pytest.approx(0.0, abs=1.0e-15)
    assert staged_sio_stage["Fe"] > 0.0
    assert staged_sio_stage["Si"] + staged_sio_stage["SiO2"] == pytest.approx(
        staged_silica, rel=1e-12
    )
    assert continuous_sio_stage == {}
    assert staged_products["SiO"] == pytest.approx(0.0076796816652895455)


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
        abs=1e-15,
    )
    assert _max_mass_balance_pct(sim) < MASS_BALANCE_MAX_PCT
