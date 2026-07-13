"""Physics-grounded regression tests for yield-recipe-investigation R1–R3."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engines.builtin.electrolysis_step import BuiltinElectrolysisStepProvider
from engines.builtin.metallothermic_step import BuiltinMetallothermicStepProvider
from simulator import mre_ladder
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.core import PyrolysisSimulator
from simulator.electrolysis import ELECTRONS_PER_OXIDE, FARADAY, MOLAR_MASS
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.run_executor import RunExecutor
from simulator.session import SimSessionConfig
from simulator.state import CampaignPhase, STOICH_RATIOS

FEEDSTOCK = "lunar_mare_low_ti"
MASS_BALANCE_MAX_PCT = 5e-12


def _load(name: str) -> dict:
    return yaml.safe_load((Path(__file__).parent.parent / "data" / name).read_text())


def _session_config(
    *,
    hours: int = 2000,
    c5_enabled: bool = True,
    mre_target_species: str = "",
) -> SimSessionConfig:
    setpoints = _load("setpoints.yaml")
    setpoints["chemistry_kernel"] = {
        **dict(setpoints.get("chemistry_kernel", {}) or {}),
        "allow_unmeasured_alpha_fallback": True,
    }
    return SimSessionConfig(
        feedstock_id=FEEDSTOCK,
        feedstocks=_load("feedstocks.yaml"),
        setpoints=setpoints,
        vapor_pressures=_load("vapor_pressures.yaml"),
        campaign="C0",
        hours=hours,
        backend_name="stub",
        c5_enabled=c5_enabled,
        mre_target_species=mre_target_species if c5_enabled else "",
        mre_max_voltage_V=1.6 if c5_enabled else 0.0,
    )


def _run_pyrolysis_track(
    *,
    hours: int = 2000,
    c5_enabled: bool = True,
    mre_target_species: str = "",
):
    return RunExecutor().execute(
        _session_config(
            hours=hours,
            c5_enabled=c5_enabled,
            mre_target_species=mre_target_species,
        )
    )


def _initial_feo_kg(sim) -> float:
    if sim.record.snapshots:
        for snapshot in sim.record.snapshots:
            feo_pct = snapshot.composition_wt_pct.get("FeO", 0.0)
            if feo_pct > 0.0 and snapshot.melt_mass_kg > 0.0:
                return snapshot.melt_mass_kg * feo_pct / 100.0
    feed = sim.feedstocks[FEEDSTOCK]
    comp = feed.get("composition_wt_pct", {})
    feo_wt = float(comp.get("FeO", 0.0))
    return sim.record.batch_mass_kg * feo_wt / 100.0


def test_c5_limited_mre_current_matches_faraday_scale():
    assert mre_ladder.C5_LIMITED_MRE_CURRENT_A == pytest.approx(1000.0)
    n_e = ELECTRONS_PER_OXIDE["FeO"]
    moles_per_hr = 1000.0 * 3600.0 / (n_e * FARADAY)
    feo_kg_per_hr = moles_per_hr * MOLAR_MASS["FeO"] / 1000.0
    assert feo_kg_per_hr == pytest.approx(1.34, rel=0.02)


def test_c5_provider_faraday_throughput_at_limited_current():
    from tests.chemistry.conftest import _build_sim, _load_yaml

    sim = _build_sim(
        FEEDSTOCK,
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )
    provider = BuiltinElectrolysisStepProvider()
    feo_kg = sim.melt.composition_kg["FeO"]
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "FeO": feo_kg / MOLAR_MASS["FeO"] * 1000.0,
            }
        },
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.ELECTROLYSIS_STEP,
        account_view=view,
        temperature_C=1575.0,
        pressure_bar=0.05,
        control_inputs={
            "voltage_V": 1.0,
            "current_A": mre_ladder.C5_LIMITED_MRE_CURRENT_A,
            "dt_hr": 1.0,
            "allowed_oxides": ["FeO"],
        },
    )
    result = provider.dispatch(request)
    reduced = float(result.diagnostic.get("oxides_reduced_kg", {}).get("FeO", 0.0))
    n_e = ELECTRONS_PER_OXIDE["FeO"]
    faraday_cap = (
        mre_ladder.C5_LIMITED_MRE_CURRENT_A
        * 3600.0
        / (n_e * FARADAY)
        * MOLAR_MASS["FeO"]
        / 1000.0
    )
    assert reduced > 0.0
    assert reduced <= feo_kg
    assert reduced <= faraday_cap
    assert reduced >= faraday_cap * 0.30


def test_na_shuttle_janaf_feo_crossover_is_below_practical_c3_temperature():
    provider = BuiltinMetallothermicStepProvider
    crossover_C = provider._crossover_temperature_C("Na", "Fe")
    assert crossover_C == pytest.approx(1181.5, abs=0.1)
    assert crossover_C < 1200.0


def test_pyrolysis_track_c5_reduces_feo_without_additives():
    result = _run_pyrolysis_track()
    sim = result.simulator
    feo_initial = _initial_feo_kg(sim)
    feo_left = sim.melt.composition_kg.get("FeO", 0.0)
    na2o_left = sim.melt.composition_kg.get("Na2O", 0.0)
    reduced_pct = (feo_initial - feo_left) / feo_initial * 100.0

    assert feo_initial > 100.0
    assert reduced_pct > 80.0
    # 793f897 made the Nernst quotient parent-oxide stoichiometric: Na2O uses
    # the square of single-cation activity. At the 1575 C C5 hold its effective
    # requirement is 2.15044 V, so the 1.6 V cap cannot consume the remaining
    # Na2O. The scheduler now skips that unreachable rung instead of spending
    # the full C5 hold there; this residual is therefore mechanism-backed.
    assert na2o_left == pytest.approx(4.10129509, abs=1e-6)
    assert sim.melt.composition_kg.get("Al2O3", 0.0) > 100.0
    assert sim.melt.composition_kg.get("MgO", 0.0) > 50.0
    assert max(abs(s.mass_balance_error_pct) for s in result.snapshots) < MASS_BALANCE_MAX_PCT


def test_c5_targeted_feo_rung_survives_pre_reducible_low_current_hours():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        _load("setpoints.yaml"),
        _load("feedstocks.yaml"),
        _load("vapor_pressures.yaml"),
    )
    sim.load_batch(FEEDSTOCK, mass_kg=1000.0)
    sim._mre_voltage_sequence = [
        {"voltage": 0.75, "species": ["FeO"], "min_hold_hours": 3},
    ]
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "FeO"
    sim.melt.mre_max_voltage_V = 0.75
    sim._mre_hold_hours = 2
    sim._mre_effective_current_A = 0.0
    sim._mre_rung_ever_effective = False

    captured: list[dict] = []

    def fake_dispatch(_intent, *, control_inputs, **_kwargs):
        captured.append(dict(control_inputs))
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    electrolysis_controls = [
        control for control in captured if "voltage_V" in control
    ]
    assert electrolysis_controls
    assert electrolysis_controls[0]["allowed_oxides"] == ["FeO"]
    assert sim._mre_voltage_step_idx == 0
    assert sim._mre_hold_hours == 3
    assert not hasattr(sim, "_mre_c5_sequence_complete_key")
    # extraction->endpoint stamp: single-rung seq means this hold IS final
    assert sim.melt.mre_c5_on_final_rung is True


def test_c5_present_rung_unreachable_at_cap_advances_after_minimum_hold():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        _load("setpoints.yaml"),
        _load("feedstocks.yaml"),
        _load("vapor_pressures.yaml"),
    )
    sim.load_batch(FEEDSTOCK, mass_kg=1000.0)
    sim._mre_voltage_sequence = [
        {"voltage": 0.5, "species": ["K2O"], "min_hold_hours": 2},
        {"voltage": 0.9, "species": ["FeO"], "min_hold_hours": 3},
    ]
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = ""
    sim.melt.mre_max_voltage_V = 1.6
    sim._mre_hold_hours = 1
    sim._mre_effective_current_A = 0.0
    sim._mre_rung_ever_effective = False
    sim._mre_effective_voltage_margin_V_by_oxide = {"K2O": -0.25}
    sim._mre_effective_voltage_margin_temperature_C = 1500.0
    sim.melt.temperature_C = 1500.0

    captured: list[dict] = []

    def fake_dispatch(_intent, *, control_inputs, **_kwargs):
        captured.append(dict(control_inputs))
        return SimpleNamespace(
            status="ok",
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
                "mre_effective_voltage_margin_V_by_oxide": {"K2O": -0.25},
            },
            transition=None,
        )

    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    electrolysis_controls = [
        control for control in captured if "voltage_V" in control
    ]
    assert electrolysis_controls
    assert electrolysis_controls[0]["allowed_oxides"] == ["K2O"]
    assert sim._mre_voltage_step_idx == 0
    assert sim._mre_hold_hours == 2

    sim.melt.temperature_C = 1575.0
    sim._step_mre()

    assert sim._mre_voltage_step_idx == 0
    assert sim._mre_hold_hours == 3

    sim._step_mre()

    assert sim._mre_voltage_step_idx == 1
    assert sim._mre_hold_hours == 0


def test_c5_declared_ladder_hold_scopes_shared_voltage_species_before_refusal():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        _load("setpoints.yaml"),
        _load("feedstocks.yaml"),
        _load("vapor_pressures.yaml"),
    )
    sim.load_batch(FEEDSTOCK, mass_kg=1000.0)
    sim._mre_voltage_sequence = [
        {"voltage": 0.5, "species": ["Na2O"], "min_hold_hours": 3},
        {"voltage": 0.5, "species": ["K2O"], "min_hold_hours": 3},
        {"voltage": 0.75, "species": ["FeO"], "min_hold_hours": 3},
    ]
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = ""
    sim.melt.mre_max_voltage_V = 0.75
    na2o_before = sim.melt.composition_kg.get("Na2O", 0.0)

    real_dispatch = sim._dispatch_only
    captured: list[dict] = []

    def spy_dispatch(intent, **kwargs):
        captured.append(dict(kwargs.get("control_inputs") or {}))
        return real_dispatch(intent, **kwargs)

    sim._dispatch_only = spy_dispatch

    sim._step_mre()

    assert sim.melt.composition_kg.get("Na2O", 0.0) < na2o_before
    assert not hasattr(sim, "_last_mre_refusal_diagnostic")
    electrolysis_controls = [c for c in captured if "voltage_V" in c]
    assert electrolysis_controls
    # Rung-scoped selectivity: the first hold is the declared Na2O rung only,
    # dispatched at the stage cap (solved == reported voltage).
    assert electrolysis_controls[0]["allowed_oxides"] == ["Na2O"]
    assert electrolysis_controls[0]["voltage_V"] == pytest.approx(0.75)
    assert sim.melt.mre_declared_rung_V == pytest.approx(0.5)
    # extraction->endpoint stamp: first of three rungs is NOT final, so the
    # C5 low-current endpoint stays gated during this hold
    assert sim.melt.mre_c5_on_final_rung is False


def test_c5_targeted_feo_full_track_reduces_target_after_low_temperature_hours():
    result = _run_pyrolysis_track(mre_target_species="FeO")
    sim = result.simulator
    feo_initial = _initial_feo_kg(sim)
    feo_left = sim.melt.composition_kg.get("FeO", 0.0)
    reduced_pct = (feo_initial - feo_left) / feo_initial * 100.0

    assert reduced_pct > 80.0
    assert sim.product_ledger().get("Fe", 0.0) > 80.0


def test_c3_k_entry_transfers_condensed_na_without_native_melt_banking():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        _load("setpoints.yaml"),
        _load("feedstocks.yaml"),
        _load("vapor_pressures.yaml"),
    )
    sim.load_batch(FEEDSTOCK, mass_kg=1000.0)
    melt_na2o_before = sim.atom_ledger.kg_by_account("process.cleaned_melt").get(
        "Na2O", 0.0
    )
    o2_before = sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored"
    ).get("O2", 0.0)
    condensed_na_kg = 1.9
    sim.atom_ledger.load_external(
        "process.condensation_train",
        {"Na": condensed_na_kg},
        source="test recovered Na",
    )
    sim._init_shuttle_inventory(CampaignPhase.C3_K)

    reagent_na = sim.atom_ledger.kg_by_account("process.reagent_inventory").get(
        "Na", 0.0
    )
    assert reagent_na >= condensed_na_kg
    assert sim.shuttle_Na_inventory_kg == pytest.approx(reagent_na)
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt").get(
        "Na2O", 0.0
    ) == pytest.approx(melt_na2o_before)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored"
    ).get("O2", 0.0) == pytest.approx(o2_before)


def test_c3_entry_without_dose_does_not_bank_native_na2o_as_reagent():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        _load("setpoints.yaml"),
        _load("feedstocks.yaml"),
        _load("vapor_pressures.yaml"),
    )
    sim.load_batch(FEEDSTOCK, mass_kg=1000.0)
    melt_na2o_before = sim.atom_ledger.kg_by_account("process.cleaned_melt").get(
        "Na2O", 0.0
    )

    sim._init_shuttle_inventory(CampaignPhase.C3_NA)

    assert melt_na2o_before > 0.0
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt").get(
        "Na2O", 0.0
    ) == pytest.approx(melt_na2o_before)
    assert sim.atom_ledger.kg_by_account("process.reagent_inventory").get(
        "Na", 0.0
    ) == pytest.approx(0.0)
    assert getattr(sim, "_c3_alkali_credit_drawn_kg_by_species", {}) == {}


def test_c3_credit_draw_does_not_debit_native_cleaned_melt_na2o():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    setpoints = _load("setpoints.yaml")
    setpoints["campaigns"]["C3"]["alkali_dosing"]["Na_kg"] = 12.0
    sim = PyrolysisSimulator(
        backend,
        setpoints,
        _load("feedstocks.yaml"),
        _load("vapor_pressures.yaml"),
    )
    sim.load_batch(FEEDSTOCK, mass_kg=1000.0)
    melt_na2o_before = sim.atom_ledger.kg_by_account("process.cleaned_melt").get(
        "Na2O", 0.0
    )

    sim._init_shuttle_inventory(CampaignPhase.C3_NA)

    assert sim.atom_ledger.kg_by_account("process.cleaned_melt").get(
        "Na2O", 0.0
    ) == pytest.approx(melt_na2o_before)
    assert sim.atom_ledger.kg_by_account("process.reagent_inventory").get(
        "Na", 0.0
    ) == pytest.approx(12.0)
    assert sim.atom_ledger.kg_by_account("reservoir.reagent.Na").get(
        "Na", 0.0
    ) == pytest.approx(-12.0)


def test_c3_shuttle_injects_na_from_condensed_alkali_alone():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        _load("setpoints.yaml"),
        _load("feedstocks.yaml"),
        _load("vapor_pressures.yaml"),
    )
    sim.load_batch(
        FEEDSTOCK,
        mass_kg=1000.0,
        additives_kg={"K": 0.0, "Na": 0.0},
    )
    condensed_na_kg = 1.9
    sim.atom_ledger.load_external(
        "process.condensation_train",
        {"Na": condensed_na_kg},
        source="test recovered Na",
    )
    sim._init_shuttle_inventory(CampaignPhase.C3_K)
    assert sim.shuttle_Na_inventory_kg == pytest.approx(condensed_na_kg)

    sim.melt.campaign = CampaignPhase.C3_K
    sim.melt.temperature_C = 1150.0
    process_before = sim.shuttle_Na_inventory_kg
    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)

    assert sim.shuttle_Na_inventory_kg < process_before
    assert sim._shuttle_injected_this_hr > 0.0
    assert sim.atom_ledger.kg_by_account("process.metal_phase").get("Fe", 0.0) > 0.0
    sim.atom_ledger.assert_balanced()


def test_pc_extract_fe_target_has_fe_product_after_full_pyrolysis_track():
    result = _run_pyrolysis_track()
    products = result.simulator.product_ledger()
    feo_initial = _initial_feo_kg(result.simulator)
    feo_left = result.simulator.melt.composition_kg.get("FeO", 0.0)
    fe_product = products.get("Fe", 0.0)

    assert (feo_initial - feo_left) / feo_initial > 0.80
    assert fe_product > 80.0


def test_pc_extract_al_remains_infeasible_at_1p6v_c5_cap():
    result = _run_pyrolysis_track()
    al_left = result.simulator.melt.composition_kg.get("Al2O3", 0.0)
    al_product = result.simulator.product_ledger().get("Al", 0.0)
    assert al_left > 100.0
    assert al_product < 5.0
