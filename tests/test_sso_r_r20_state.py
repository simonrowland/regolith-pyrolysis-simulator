from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from simulator.accounting import resolve_species_formula
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.core import (
    FERRIC_DIVERGENCE_WARNING_THRESHOLD,
    OXYGEN_RESERVOIR_NOOP_MOL,
    PyrolysisSimulator,
)
from simulator.fe_redox import (
    KRESS91_INV_T_COEFFICIENT_K,
    KRESS91_LN_FO2_COEFFICIENT,
)
from simulator.melt_backend.base import StubBackend
from simulator.runner import build_per_hour_summary
from simulator.state import Atmosphere, CampaignPhase, MeltState
from scripts import evaporation_selectivity_map as selectivity_map


ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / "data" / name).read_text())


def _make_sim(
    feedstock_id: str = "lunar_mare_low_ti",
    *,
    additives_kg: dict[str, float] | None = None,
) -> PyrolysisSimulator:
    setpoints = _load_yaml("setpoints.yaml")
    setpoints.setdefault("chemistry_kernel", {})["allow_fallback_vapor"] = True
    sim = PyrolysisSimulator(
        StubBackend(),
        setpoints,
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch(feedstock_id, mass_kg=1000.0, additives_kg=additives_kg or {})
    return sim


def _cleaned_melt_debit_mol(sim: PyrolysisSimulator, transition, species: str) -> float:
    formula = resolve_species_formula(species, sim.species_formula_registry)
    return sum(
        float(lot.species_kg.get(species, 0.0))
        / formula.molar_mass_kg_per_mol()
        for lot in transition.debits
        if lot.account == "process.cleaned_melt"
    )


def test_melt_fO2_log_exists_and_defaults_to_intrinsic_seed() -> None:
    melt = MeltState()

    assert hasattr(melt, "melt_fO2_log")
    assert hasattr(melt, "oxygen_reservoir")
    assert melt.fO2_log == pytest.approx(-9.0)
    assert melt.melt_fO2_log == pytest.approx(-9.0)
    assert melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(-9.0)
    assert melt.oxygen_reservoir.headspace_transport_pO2_bar == pytest.approx(1e-9)


def test_load_batch_seeds_melt_fO2_log_from_intrinsic_value() -> None:
    sim = _make_sim()
    intrinsic = sim._compute_intrinsic_melt_fO2()

    assert sim.melt.fO2_log == pytest.approx(intrinsic)
    assert sim.melt.melt_fO2_log == pytest.approx(intrinsic)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        intrinsic
    )
    assert sim.melt.oxygen_reservoir.reference_T_K is None


def test_load_batch_resets_hot_reference_temperature_on_reload() -> None:
    sim = _make_sim()
    sim._overhead_headspace_config["enabled"] = False
    sim.melt.temperature_C = 1600.0

    first = sim._apply_oxygen_reservoir_exchange()
    assert first.reference_T_K == pytest.approx(1600.0 + 273.15)

    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    base_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log

    assert sim.melt.oxygen_reservoir.reference_T_K is None

    sim._overhead_headspace_config["enabled"] = False
    sim.melt.temperature_C = 1450.0
    seeded = sim._apply_oxygen_reservoir_exchange()

    assert seeded.reference_T_K == pytest.approx(1450.0 + 273.15)
    assert seeded.melt_intrinsic_fO2_log == pytest.approx(base_fO2)


def test_start_campaign_preserves_authoritative_melt_fO2_log() -> None:
    sim = _make_sim()
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -7.25
    sim._sync_oxygen_reservoir_mirror()

    sim.start_campaign(CampaignPhase.C0)

    assert sim.melt.fO2_log == pytest.approx(-7.25)
    assert sim.melt.melt_fO2_log == pytest.approx(-7.25)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        -7.25
    )


def test_step_mirrors_intrinsic_value_to_melt_fO2_log() -> None:
    sim = _make_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.melt_fO2_log = 123.0

    sim.step()

    reservoir = sim.melt.oxygen_reservoir
    assert sim.melt.fO2_log == pytest.approx(reservoir.melt_intrinsic_fO2_log)
    assert sim.melt.melt_fO2_log == pytest.approx(
        reservoir.melt_intrinsic_fO2_log
    )
    assert reservoir.headspace_transport_pO2_bar >= 1e-9


def test_references_registry_carries_sso_r_r20_redox_citations() -> None:
    registry_path = ROOT / "docs" / "references" / "references.yaml"
    references = yaml.safe_load(registry_path.read_text(encoding="utf-8"))["references"]

    expected_notes = {
        "REF-001": "Kress91 ln(XFe2O3/XFeO) relation",
        "REF-035": "log10(fO2/bar) = 8.58 - 25050/T",
        "REF-036": "log10(fO2/bar) = -27215/T + 6.57",
        "REF-037": "graphite-CO-CO2 point formula",
        "REF-038": "IW-2 .. IW",
        "REF-039": "reduced vs terrestrial",
    }

    for ref_id, expected in expected_notes.items():
        assert ref_id in references
        assert expected in references[ref_id]["coefficient_note"]


def test_melt_fO2_log_is_live_in_vapor_pressure_producer(monkeypatch) -> None:
    sim = _make_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 800.0
    sim.melt.p_total_mbar = 1.0e-3
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -6.25
    sim.melt.fO2_log = -6.25
    sim.melt.melt_fO2_log = -6.25
    seen_control_inputs: list[dict[str, Any]] = []
    original_dispatch_only = sim._dispatch_only

    def spy_dispatch_only(intent, **kwargs):
        if intent is ChemistryIntent.VAPOR_PRESSURE:
            seen_control_inputs.append(dict(kwargs["control_inputs"]))
        return original_dispatch_only(intent, **kwargs)

    monkeypatch.setattr(sim, "_dispatch_only", spy_dispatch_only)

    sim._get_equilibrium()

    assert seen_control_inputs
    assert seen_control_inputs[-1]["intrinsic_fO2_log"] == pytest.approx(-6.25)


def test_reductant_source_term_lowers_fO2_and_raises_native_drive() -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim._sync_oxygen_reservoir_mirror()
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    before_native = sim._compute_fe_redox_split_diagnostic()["native_fe_frac"]

    sim._apply_oxygen_reservoir_redox_source_terms(
        {"test_reductant_sink": -1.0},
        exchange_direction="redox_source:test_reductant_sink",
    )

    after_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    after_native = sim._compute_fe_redox_split_diagnostic()["native_fe_frac"]
    assert after_fO2 < before_fO2
    assert after_native > before_native


@pytest.mark.parametrize(
    ("terms", "capacity", "expected_reason"),
    [
        ({"test_reductant_sink": -1.0}, 0.0, "no_melt_redox_capacity"),
        (
            {
                "test_reductant_sink": -1.0,
                "test_oxidant_source": 1.0,
            },
            10.0,
            "below_threshold",
        ),
    ],
)
def test_redox_source_breakdown_marks_skipped_terms(
    monkeypatch,
    terms: dict[str, float],
    capacity: float,
    expected_reason: str,
) -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim._sync_oxygen_reservoir_mirror()
    monkeypatch.setattr(
        sim,
        "_melt_redox_capacity_mol_per_ln_fO2",
        lambda **_: capacity,
    )

    sim._apply_oxygen_reservoir_redox_source_terms(
        terms,
        exchange_direction="redox_source:test_reductant_sink",
    )

    reservoir = sim.melt.oxygen_reservoir
    breakdown = sim._redox_source_breakdown_diagnostic()
    assert breakdown["terms_mol_o2_equiv_by_label"] == pytest.approx(terms)
    assert breakdown["applied_terms_mol_o2_equiv_by_label"] == {}
    assert breakdown["skipped_terms_mol_o2_equiv_by_label"] == pytest.approx(
        terms
    )
    assert set(breakdown["skipped_reasons_by_label"].values()) == {
        expected_reason
    }
    assert breakdown["redox_source_terms_applied"] is False
    assert breakdown["redox_source_skip_reason"] == expected_reason
    assert breakdown["delta_ln_fO2"] == pytest.approx(0.0)
    assert reservoir.redox_source_terms_applied is False
    assert reservoir.redox_source_skip_reason == expected_reason
    assert reservoir.redox_source_skipped_terms_mol_o2_equiv == pytest.approx(
        terms
    )
    assert reservoir.exchange_direction.endswith(f":skipped:{expected_reason}")


def test_c3_na_source_term_comes_from_committed_transition() -> None:
    sim = _make_sim(additives_kg={"Na": 12.0})
    sim._init_shuttle_inventory(CampaignPhase.C3_NA)
    sim.melt.campaign = CampaignPhase.C3_NA
    sim.melt.temperature_C = 1150.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -11.0
    sim._sync_oxygen_reservoir_mirror()
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    before_native = sim._compute_fe_redox_split_diagnostic()["native_fe_frac"]
    transitions_before = len(sim.atom_ledger.transitions)

    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)

    transition = sim.atom_ledger.transitions[-1]
    assert len(sim.atom_ledger.transitions) == transitions_before + 1
    assert transition.name == "c3_na_shuttle_reduction"
    feo_mol = _cleaned_melt_debit_mol(sim, transition, "FeO")
    expected_source = -0.5 * feo_mol
    label = "redox_source:c3_na_shuttle_reduction"
    reservoir = sim.melt.oxygen_reservoir
    breakdown = sim._redox_source_breakdown_diagnostic()

    assert feo_mol > 0.0
    assert breakdown["terms_mol_o2_equiv_by_label"][label] == pytest.approx(
        expected_source
    )
    assert reservoir.redox_source_terms_mol_o2_equiv[label] == pytest.approx(
        expected_source
    )
    assert reservoir.redox_source_delta_ln_fO2 == pytest.approx(
        expected_source / reservoir.melt_redox_capacity_mol_per_ln_fO2
    )
    assert reservoir.melt_intrinsic_fO2_log < before_fO2
    assert sim._compute_fe_redox_split_diagnostic()["native_fe_frac"] > before_native
    assert breakdown["ferric_divergence"]["status"] == "ok"
    assert breakdown["ferric_divergence"]["sampling_context"] == (
        "current_ledger_vs_current_reservoir"
    )


def test_c3_na_source_terms_preserve_same_hour_exchange_observables() -> None:
    sim = _make_sim(additives_kg={"Na": 12.0})
    sim._init_shuttle_inventory(CampaignPhase.C3_NA)
    sim.melt.campaign = CampaignPhase.C3_NA
    sim.melt.temperature_C = 1150.0
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = 1.5
    sim.melt.p_total_mbar = 1.5
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -8.0
    sim._sync_oxygen_reservoir_mirror()
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 0.05},
        source="test finite headspace O2 holdup",
    )

    exchange = sim._apply_oxygen_reservoir_exchange()
    exchange_direction = exchange.exchange_direction
    exchange_o2_mol = exchange.exchange_o2_mol
    exchange_transition_name = exchange.exchange_transition_name
    k_O_m_s = exchange.k_O_m_s
    tau_hr = exchange.tau_hr
    ledger_pO2 = exchange.headspace_ledger_pO2_bar
    transport_pO2 = exchange.headspace_transport_pO2_bar

    assert abs(exchange_o2_mol) > OXYGEN_RESERVOIR_NOOP_MOL
    assert exchange_direction
    assert k_O_m_s > 0.0
    assert tau_hr > 0.0

    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)
    snapshot = sim._make_snapshot()
    reservoir = snapshot.oxygen_reservoir
    label = "redox_source:c3_na_shuttle_reduction"

    assert reservoir["k_O_m_s"] == pytest.approx(k_O_m_s)
    assert reservoir["tau_hr"] == pytest.approx(tau_hr)
    assert reservoir["exchange_o2_mol"] == pytest.approx(exchange_o2_mol)
    assert reservoir["exchange_transition_name"] == exchange_transition_name
    assert reservoir["headspace_ledger_pO2_bar"] == pytest.approx(ledger_pO2)
    assert reservoir["headspace_transport_pO2_bar"] == pytest.approx(
        transport_pO2
    )
    assert reservoir["exchange_direction"].split("|")[0] == exchange_direction
    assert label in reservoir["exchange_direction"].split("|")
    assert label in reservoir["redox_source_terms_mol_o2_equiv"]
    assert label in reservoir["redox_source_applied_terms_mol_o2_equiv"]
    assert reservoir["redox_source_skipped_terms_mol_o2_equiv"] == {}
    assert reservoir["redox_source_terms_applied"] is True


def test_c3_k_source_term_comes_from_committed_transition() -> None:
    sim = _make_sim(additives_kg={"K": 12.0})
    sim._init_shuttle_inventory(CampaignPhase.C3_K)
    sim.melt.campaign = CampaignPhase.C3_K
    sim.melt.temperature_C = 800.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -7.0
    sim._sync_oxygen_reservoir_mirror()

    sim._shuttle_inject_K(liquid_fraction=1.0)

    transition = sim.atom_ledger.transitions[-1]
    feo_mol = _cleaned_melt_debit_mol(sim, transition, "FeO")
    label = "redox_source:c3_k_shuttle_reduction"
    assert feo_mol > 0.0
    assert sim._redox_source_breakdown_diagnostic()[
        "terms_mol_o2_equiv_by_label"
    ][label] == pytest.approx(-0.5 * feo_mol)


def test_c3_na_cr_ti_source_term_scales_each_target_oxide() -> None:
    sim = _make_sim(additives_kg={"Na": 50.0})
    sim._init_shuttle_inventory(CampaignPhase.C3_NA)
    sim.melt.campaign = CampaignPhase.C3_NA
    sim.melt.temperature_C = 200.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -11.0
    sim._sync_oxygen_reservoir_mirror()

    sim._shuttle_inject_Na(target_stage="cr_ti", liquid_fraction=1.0)

    transition = next(
        transition
        for transition in reversed(sim.atom_ledger.transitions)
        if transition.name == "c3_na_shuttle_reduction"
    )
    label = "redox_source:c3_na_shuttle_reduction"
    feo_mol = _cleaned_melt_debit_mol(sim, transition, "FeO")
    cr2o3_mol = _cleaned_melt_debit_mol(sim, transition, "Cr2O3")
    tio2_mol = _cleaned_melt_debit_mol(sim, transition, "TiO2")
    expected_cr2o3_source = -1.5 * cr2o3_mol
    expected_tio2_source = -1.0 * tio2_mol
    expected_total_source = (
        (-0.5 * feo_mol)
        + expected_cr2o3_source
        + expected_tio2_source
    )
    cr2o3_terms = sim._cleaned_melt_reduction_source_terms_from_transition(
        transition,
        label="cr2o3_component",
        target_oxides=("Cr2O3",),
    )
    tio2_terms = sim._cleaned_melt_reduction_source_terms_from_transition(
        transition,
        label="tio2_component",
        target_oxides=("TiO2",),
    )

    assert transition.name == "c3_na_shuttle_reduction"
    assert cr2o3_mol > 0.0
    assert tio2_mol > 0.0
    assert cr2o3_terms["cr2o3_component"] == pytest.approx(
        expected_cr2o3_source
    )
    assert tio2_terms["tio2_component"] == pytest.approx(expected_tio2_source)
    assert sim._redox_source_breakdown_diagnostic()[
        "terms_mol_o2_equiv_by_label"
    ][label] == pytest.approx(expected_total_source)


def test_runner_reads_redox_source_breakdown() -> None:
    sim = _make_sim(additives_kg={"Na": 12.0})
    sim._init_shuttle_inventory(CampaignPhase.C3_NA)
    sim.melt.campaign = CampaignPhase.C3_NA
    sim.melt.temperature_C = 1150.0
    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)
    snapshot = sim._make_snapshot()
    snapshot.fe_redox_split = sim._compute_fe_redox_split_diagnostic()

    summary = build_per_hour_summary(sim, snapshot, include_fe_redox_split=True)

    label = "redox_source:c3_na_shuttle_reduction"
    breakdown = summary["redox_source_breakdown"]
    assert label in breakdown["terms_mol_o2_equiv_by_label"]
    assert breakdown["terms_mol_o2_equiv_by_label"][label] < 0.0
    assert breakdown["ferric_divergence"]["warning_threshold_abs"] == pytest.approx(
        FERRIC_DIVERGENCE_WARNING_THRESHOLD
    )
    assert breakdown["ferric_divergence"][
        "warning_threshold_ferric_fraction_abs"
    ] == pytest.approx(
        FERRIC_DIVERGENCE_WARNING_THRESHOLD
    )
    assert "fe_redox_split" in summary


def test_isochemical_temperature_ramp_references_fO2_on_kress91_curve() -> None:
    sim = _make_sim()
    sim._overhead_headspace_config["enabled"] = False
    reference_T_K = 1425.0 + 273.15
    hot_T_K = 1750.0 + 273.15
    sim.melt.temperature_C = 1750.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim.melt.oxygen_reservoir.reference_T_K = reference_T_K
    sim._sync_oxygen_reservoir_mirror()

    reservoir = sim._apply_oxygen_reservoir_exchange()

    expected_shift_log10 = -(
        KRESS91_INV_T_COEFFICIENT_K / KRESS91_LN_FO2_COEFFICIENT
    ) * ((1.0 / hot_T_K) - (1.0 / reference_T_K)) / math.log(10.0)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(
        -9.0 + expected_shift_log10,
        abs=1.0e-6,
    )
    assert reservoir.reference_T_K == pytest.approx(hot_T_K)


def test_isochemical_temperature_cooling_reverses_fO2_reference_shift() -> None:
    sim = _make_sim()
    sim._overhead_headspace_config["enabled"] = False
    reference_T_K = 1425.0 + 273.15
    hot_T_K = 1750.0 + 273.15
    sim.melt.temperature_C = 1750.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim.melt.oxygen_reservoir.reference_T_K = reference_T_K
    sim._sync_oxygen_reservoir_mirror()

    sim._apply_oxygen_reservoir_exchange()
    sim.melt.temperature_C = 1425.0
    reservoir = sim._apply_oxygen_reservoir_exchange()

    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(-9.0, abs=1.0e-6)
    assert reservoir.reference_T_K == pytest.approx(reference_T_K)


def test_load_seed_references_on_first_liquid_tick_not_low_temperature() -> None:
    sim = _make_sim()
    sim._overhead_headspace_config["enabled"] = False
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim._sync_oxygen_reservoir_mirror()

    sim.melt.temperature_C = 1250.0
    low = sim._apply_oxygen_reservoir_exchange()
    assert low.melt_intrinsic_fO2_log == pytest.approx(-9.0)
    assert low.reference_T_K is None

    sim.melt.temperature_C = 1425.0
    liquid = sim._apply_oxygen_reservoir_exchange()
    assert liquid.melt_intrinsic_fO2_log == pytest.approx(-9.0)
    assert liquid.reference_T_K == pytest.approx(1425.0 + 273.15)


def test_selectivity_map_temperature_sweep_rides_kress91_curve(monkeypatch) -> None:
    sim = _make_sim()
    sim._overhead_headspace_config["enabled"] = False
    original_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    captured_fO2: list[float] = []

    def fake_get_equilibrium():
        captured_fO2.append(sim._current_melt_redox_fO2_log())
        return SimpleNamespace(vapor_pressures_Pa={"Na": 1.0})

    def fake_calculate_evaporation(_equilibrium):
        return SimpleNamespace(species_kg_hr={"Na": 1.0})

    args = SimpleNamespace(
        additive=None,
        feedstock="lunar_mare_low_ti",
        mass_kg=1000.0,
        p_o2_mbar=0.0,
        p_total_mbar=10.0,
        species=["Na"],
        start_C=1425.0,
        stop_C=1725.0,
        step_C=150.0,
    )
    monkeypatch.setattr(selectivity_map, "_build_sim", lambda _args: sim)
    monkeypatch.setattr(sim, "_get_equilibrium", fake_get_equilibrium)
    monkeypatch.setattr(sim, "_calculate_evaporation", fake_calculate_evaporation)

    rows = selectivity_map._rows(args)

    assert rows
    assert captured_fO2 == sorted(captured_fO2)
    assert len(set(captured_fO2)) == len(captured_fO2)
    assert sim.melt.oxygen_reservoir.reference_T_K is None
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        original_fO2,
    )


def test_native_fe_split_sees_temperature_honest_unreduced_melt() -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1750.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim.melt.oxygen_reservoir.reference_T_K = 1425.0 + 273.15
    sim._sync_oxygen_reservoir_mirror()

    split = sim._apply_native_fe_saturation_split()
    native_fe_kg = sim.atom_ledger.kg_by_account(
        "terminal.drain_tap_material"
    ).get("Fe", 0.0)

    assert split["native_fe_frac"] <= 1.0e-12
    assert native_fe_kg == pytest.approx(0.0, abs=1.0e-12)


def test_headspace_exchange_cannot_instantly_erase_reductant_dose() -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = 1.5
    sim.melt.p_total_mbar = 1.5
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -8.0
    sim._sync_oxygen_reservoir_mirror()
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log

    sim._apply_oxygen_reservoir_redox_source_terms(
        {"test_reductant_sink": -1.0},
        exchange_direction="redox_source:test_reductant_sink",
    )
    reduced_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 0.05},
        source="test finite headspace O2 holdup",
    )

    reservoir = sim._apply_oxygen_reservoir_exchange()

    assert reservoir.exchange_direction == "headspace_to_melt"
    assert reservoir.exchange_clamped is True
    assert reservoir.melt_intrinsic_fO2_log > reduced_fO2
    assert reservoir.melt_intrinsic_fO2_log < before_fO2


def test_managed_o2_floor_relaxes_reducing_melt_without_real_o2_inventory() -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = 1.5
    sim.melt.p_total_mbar = 1.5
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -10.0
    sim._sync_oxygen_reservoir_mirror()
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    before_o2 = sim.atom_ledger.mol_by_account("process.overhead_gas").get(
        "O2",
        0.0,
    )

    reservoir = sim._apply_oxygen_reservoir_exchange()

    assert before_o2 == pytest.approx(0.0)
    assert reservoir.exchange_direction == "managed_headspace_to_melt"
    assert reservoir.melt_intrinsic_fO2_log > before_fO2
    assert reservoir.melt_intrinsic_fO2_log < math.log10(
        sim.melt.pO2_mbar / 1000.0
    )
    after_o2 = sim.atom_ledger.mol_by_account("process.overhead_gas").get(
        "O2",
        0.0,
    )
    assert after_o2 == pytest.approx(before_o2)


def test_pn2_sweep_without_o2_does_not_phantom_oxidize_melt() -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.pO2_mbar = 1.5
    sim.melt.p_total_mbar = 10.0
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -10.0
    sim._sync_oxygen_reservoir_mirror()
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log

    reservoir = sim._apply_oxygen_reservoir_exchange()

    assert reservoir.exchange_direction == "none:headspace_o2_clamped"
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(before_fO2)


def test_live_paths_do_not_call_intrinsic_heuristic(monkeypatch) -> None:
    sim = _make_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1150.0
    sim.melt.p_total_mbar = 1.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -6.5
    sim._sync_oxygen_reservoir_mirror()
    pressure_bar = sim.melt.p_total_mbar / 1000.0
    sim._freeze_gate_liquid_fraction_cache = {
        "key": sim._freeze_gate_cache_key(
            pressure_bar=pressure_bar,
            fO2_log=-6.5,
        ),
        "curve": {
            "source": "test_cached_curve",
            "solidus_T_C": 1000.0,
            "liquidus_T_C": 1300.0,
        },
    }
    seen_sulfsat_fO2: list[float] = []

    def fail_heuristic(*_args, **_kwargs):
        raise AssertionError("live path called intrinsic fO2 heuristic")

    def fake_sulfsat(**kwargs):
        seen_sulfsat_fO2.append(float(kwargs["fO2_log"]))
        return SimpleNamespace(calibration_status="in_range", warnings=())

    monkeypatch.setattr(sim, "_compute_intrinsic_melt_fO2", fail_heuristic)
    monkeypatch.setattr(sim, "_stage0_sulfur_input_ppm", lambda: 100.0)
    monkeypatch.setattr(sim._sulfsat_gate, "compute_sulfur_saturation", fake_sulfsat)

    assert sim._freeze_gate_curve()["source"] == "test_cached_curve"
    assert sim._stub_equilibrium().fO2_log == pytest.approx(-6.5)
    sim._attach_post_equilibrium_sulfsat(SimpleNamespace(warnings=[]))

    assert seen_sulfsat_fO2 == pytest.approx([-6.5])
    assert sim.melt.fO2_log == pytest.approx(-6.5)
    assert sim.melt.melt_fO2_log == pytest.approx(-6.5)


def test_step_does_not_reseed_live_fO2_from_heuristic() -> None:
    sim = _make_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -10.5
    sim._sync_oxygen_reservoir_mirror()
    heuristic = sim._compute_intrinsic_melt_fO2()

    sim.step()

    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log < heuristic - 1.0


def test_native_fe_split_updates_fO2_to_saturation_boundary() -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -10.0
    sim._sync_oxygen_reservoir_mirror()
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    before_native = sim._compute_fe_redox_split_diagnostic()["native_fe_frac"]

    sim._apply_native_fe_saturation_split()

    after = sim._compute_fe_redox_split_diagnostic()
    assert before_native > 0.0
    assert (
        "redox_source:native_fe_saturation_split"
        in sim.melt.oxygen_reservoir.exchange_direction.split("|")
    )
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log > before_fO2
    assert after["native_fe_frac"] <= 2.0e-12
