import shlex

import pytest

from simulator.session_cli import SessionScriptRunner
from simulator.state import STOICH_RATIOS


FEEDSTOCK = "lunar_mare_low_ti"
K_DOSE_KG = 26.0
HOT_HOLD_C = 1750.0
MASS_BALANCE_MAX_PCT = 5e-12


def _run_script(lines: list[str]):
    runner = SessionScriptRunner()
    for line in lines:
        runner.execute(shlex.split(line), line)
    return runner.session._sim


def _run_staged():
    return _run_script([
        (
            f"start --feedstock={FEEDSTOCK} --campaign=C2A_staged "
            f"--additive=K={K_DOSE_KG}"
        ),
        f"adjust campaign_override C2A_staged hold_temp_C {HOT_HOLD_C}",
        "advance 30",
    ])


def _run_continuous():
    return _run_script([
        (
            f"start --feedstock={FEEDSTOCK} --campaign=C2A_continuous "
            f"--additive=K={K_DOSE_KG}"
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
    assert sim.record.additives_kg["K"] == pytest.approx(K_DOSE_KG)
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


def test_c2a_staged_is_deterministic_and_beats_c2a_continuous():
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

    assert staged_products.get("Fe", 0.0) > continuous_products.get("Fe", 0.0)
    assert staged_products.get("Na", 0.0) > continuous_products.get("Na", 0.0)
    assert staged_silica > continuous_silica
    assert sum(staged_sio_stage.get(s, 0.0) for s in ("Fe", "Mg")) < 1e-6
