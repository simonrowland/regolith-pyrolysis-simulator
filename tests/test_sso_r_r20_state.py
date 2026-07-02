from __future__ import annotations

import copy
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from engines.builtin.ca_aluminothermic_step import BuiltinCaAluminothermicStepProvider
from simulator.account_ids import (
    C7_AL_CREDIT_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
)
from simulator.accounting import resolve_species_formula
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.core import (
    FE_REDOX_OXYGEN_SOURCE_EVAPORATIVE_METAL_LOSS,
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
from simulator.state import Atmosphere, CampaignPhase, EvaporationFlux, MeltState
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


def _transition_account_species_mol(
    sim: PyrolysisSimulator,
    transition,
    *,
    side: str,
    account: str,
    species: str,
) -> float:
    formula = resolve_species_formula(species, sim.species_formula_registry)
    lots = transition.debits if side == "debits" else transition.credits
    return sum(
        float(lot.species_kg.get(species, 0.0))
        / formula.molar_mass_kg_per_mol()
        for lot in lots
        if lot.account == account
    )


def _transition_account_o2_equiv_mol(
    sim: PyrolysisSimulator,
    transition,
    *,
    side: str,
    account: str,
    species: str,
) -> float:
    formula = resolve_species_formula(species, sim.species_formula_registry)
    oxygen_atoms = float(formula.elements.get("O", 0.0) or 0.0)
    return 0.5 * oxygen_atoms * _transition_account_species_mol(
        sim,
        transition,
        side=side,
        account=account,
        species=species,
    )


def test_step_orders_passive_exchange_sources_native_split_and_evaporation(
    monkeypatch,
) -> None:
    sim = _make_sim(additives_kg={"Na": 12.0})
    sim.start_campaign(CampaignPhase.C3_NA)
    sim.melt.campaign_hour = 1
    order: list[str] = []

    def fake_exchange():
        order.append("passive_exchange")
        return sim.melt.oxygen_reservoir

    def fake_native_split(*, sample_time_h=None):
        order.append("native_split")
        return {}

    def fake_respeciation(**_kwargs):
        order.append("fe_redox_respeciation")
        return {}

    def fake_equilibrium():
        order.append("equilibrium")
        return SimpleNamespace()

    def fake_evaporation(_equilibrium):
        order.append("evaporation")
        flux = EvaporationFlux(species_kg_hr={"SiO": 1.0e-6})
        flux.update_totals()
        return flux

    def fake_evaporative_source(_transition, *, exchange_direction):
        order.append("evaporative_redox_source_terms")
        return None

    def fake_route(_evap_flux):
        order.append("condensation_route")
        sim._apply_evaporative_redox_source_terms(
            object(),
            exchange_direction="redox_source:evaporative_loss",
        )

    monkeypatch.setattr(sim, "_apply_oxygen_reservoir_exchange", fake_exchange)
    monkeypatch.setattr(sim, "_step_shuttle", lambda: order.append("c3_source"))
    monkeypatch.setattr(sim, "_apply_fe_redox_respeciation", fake_respeciation)
    monkeypatch.setattr(sim, "_apply_native_fe_saturation_split", fake_native_split)
    monkeypatch.setattr(sim, "_get_equilibrium", fake_equilibrium)
    monkeypatch.setattr(sim, "_calculate_evaporation", fake_evaporation)
    monkeypatch.setattr(sim, "_apply_analytic_evaporation_depletion", lambda flux: flux)
    monkeypatch.setattr(sim, "_configure_condensation_operating_conditions", lambda flux: None)
    monkeypatch.setattr(sim, "_apply_lab_surface_temperatures", lambda *, sample_time_h: None)
    monkeypatch.setattr(sim, "_route_to_condensation", fake_route)
    monkeypatch.setattr(
        sim,
        "_apply_evaporative_redox_source_terms",
        fake_evaporative_source,
    )

    sim.step()

    assert order.count("passive_exchange") == 1
    assert order.count("fe_redox_respeciation") == 2
    assert order.index("passive_exchange") < order.index("evaporation")
    assert order.index("c3_source") < order.index("fe_redox_respeciation")
    assert order.index("fe_redox_respeciation") < order.index("native_split")
    assert order.index("native_split") < order.index("evaporation")
    assert order.index("passive_exchange") < order.index(
        "evaporative_redox_source_terms"
    )
    assert order.index("evaporative_redox_source_terms") < len(order) - 1
    assert order[-1] == "fe_redox_respeciation"


@pytest.mark.parametrize(
    ("campaign", "producer_attr", "producer_marker", "producer_result"),
    [
        (CampaignPhase.C5, "_step_mre", "mre_source", 0.0),
        (CampaignPhase.MRE_BASELINE, "_step_mre", "mre_source", 0.0),
        (CampaignPhase.C6, "_step_thermite", "c6_source", None),
        (
            CampaignPhase.C7_CA_ALUMINOTHERMIC,
            "_step_c7_ca_aluminothermic",
            "c7_source",
            None,
        ),
    ],
)
def test_step_orders_source_producers_before_native_split(
    monkeypatch,
    campaign,
    producer_attr,
    producer_marker,
    producer_result,
) -> None:
    sim = _make_sim()
    sim.melt.campaign = campaign
    sim.melt.campaign_hour = 1
    if campaign == CampaignPhase.C5:
        sim.melt.c5_enabled = True
    order: list[str] = []

    def fake_native_split(*, sample_time_h=None):
        order.append("native_split")
        return {}

    def fake_respeciation(**_kwargs):
        order.append("fe_redox_respeciation")
        return {}

    def fake_producer():
        order.append(producer_marker)
        return producer_result

    monkeypatch.setattr(
        sim,
        "_apply_oxygen_reservoir_exchange",
        lambda: order.append("passive_exchange") or sim.melt.oxygen_reservoir,
    )
    monkeypatch.setattr(sim, producer_attr, fake_producer)
    monkeypatch.setattr(sim, "_apply_fe_redox_respeciation", fake_respeciation)
    monkeypatch.setattr(sim, "_apply_native_fe_saturation_split", fake_native_split)
    monkeypatch.setattr(
        sim,
        "_get_equilibrium",
        lambda: order.append("equilibrium") or SimpleNamespace(),
    )

    sim.step()

    assert order.index("passive_exchange") < order.index(producer_marker)
    assert order.index(producer_marker) < order.index("fe_redox_respeciation")
    assert order.index("fe_redox_respeciation") < order.index("native_split")
    assert order.index("native_split") < order.index("equilibrium")


def _make_custom_feedstock_sim(
    feedstocks: dict[str, Any],
    feedstock_id: str,
    *,
    additives_kg: dict[str, float] | None = None,
) -> PyrolysisSimulator:
    setpoints = _load_yaml("setpoints.yaml")
    setpoints.setdefault("chemistry_kernel", {})["allow_fallback_vapor"] = True
    sim = PyrolysisSimulator(
        StubBackend(),
        setpoints,
        feedstocks,
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch(feedstock_id, mass_kg=1000.0, additives_kg=additives_kg or {})
    return sim


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


def test_redox_source_breakdown_stamp_survives_campaign_transition_seam() -> None:
    sim = _make_sim(additives_kg={"Na": 12.0})
    sim._init_shuttle_inventory(CampaignPhase.C3_NA)
    sim.melt.campaign = CampaignPhase.C3_NA
    sim.melt.hour = 17
    sim.melt.campaign_hour = 3
    sim.melt.temperature_C = 1150.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -8.0
    sim._sync_oxygen_reservoir_mirror()

    sim._reset_redox_source_diagnostics_for_hour()
    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)
    sim.melt.hour += 1
    sim.melt.campaign_hour += 1
    sim._stamp_redox_source_context_for_current_state(force=True)
    sim.start_campaign(CampaignPhase.C4)

    snapshot = sim._make_snapshot()
    breakdown = snapshot.redox_source_breakdown

    assert snapshot.campaign == CampaignPhase.C4
    assert breakdown["source_context"] == {
        "campaign": "C3_NA",
        "hour": 18,
        "campaign_hour": 4,
    }
    assert breakdown["source_campaign"] == "C3_NA"
    assert breakdown["source_hour"] == 18
    assert breakdown["source_campaign_hour"] == 4
    assert snapshot.oxygen_reservoir["redox_source_context"] == (
        breakdown["source_context"]
    )


def test_mre_source_term_comes_from_committed_anode_o2_transition() -> None:
    sim = _make_sim()
    sim.start_campaign(CampaignPhase.C5)
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "SiO2"
    sim.melt.mre_max_voltage_V = 1.45
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim._sync_oxygen_reservoir_mirror()
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log

    sim._step_mre()

    transition = sim.atom_ledger.transitions[-1]
    label = "redox_source:mre_electrolysis_reduction"
    anode_o2_mol = _transition_account_species_mol(
        sim,
        transition,
        side="credits",
        account=OXYGEN_MRE_ANODE_ACCOUNT,
        species="O2",
    )
    breakdown = sim._redox_source_breakdown_diagnostic()

    assert transition.name == "mre_electrolysis_reduction"
    assert anode_o2_mol > 0.0
    assert breakdown["terms_mol_o2_equiv_by_label"][label] == pytest.approx(
        -anode_o2_mol
    )
    assert sim.atom_ledger.mol_by_account("process.overhead_gas").get(
        "O2", 0.0
    ) == pytest.approx(0.0)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log < before_fO2


# Mg=12 is the original scale; 50/100 kg are production-scale doses where
# the kg<->mol round-trip float residual on the 4 Al : 3 Si back-reduction
# exceeded the absolute NOOP floor and leaked a spurious back label (grok
# ch2b review). The primary pass is also net-zero in O2-equivalent because
# its Al2O3 oxygen remains in cleaned_melt as MgO.
@pytest.mark.parametrize("mg_dose_kg", [12.0, 50.0, 100.0])
def test_c6_primary_source_term_from_transition_and_back_reduction_nets_zero(
    mg_dose_kg,
) -> None:
    sim = _make_custom_feedstock_sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"Al2O3": 75.0, "SiO2": 20.0, "FeO": 5.0},
            }
        },
        "oxide",
        additives_kg={"Mg": mg_dose_kg},
    )
    sim._init_thermite_inventory()
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim._sync_oxygen_reservoir_mirror()
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log

    sim._step_thermite()

    primary = next(
        transition
        for transition in reversed(sim.atom_ledger.transitions)
        if transition.name == "c6_mg_thermite_primary"
    )
    back = next(
        transition
        for transition in reversed(sim.atom_ledger.transitions)
        if transition.name == "c6_al_si_back_reduction"
    )
    primary_label = "redox_source:c6_mg_thermite_primary"
    back_label = "redox_source:c6_mg_thermite_back_reduction"
    al2o3_mol = _cleaned_melt_debit_mol(sim, primary, "Al2O3")
    mgo_o2_equiv_mol = _transition_account_o2_equiv_mol(
        sim,
        primary,
        side="credits",
        account="process.cleaned_melt",
        species="MgO",
    )
    breakdown = sim._redox_source_breakdown_diagnostic()

    assert al2o3_mol > 0.0
    assert mgo_o2_equiv_mol == pytest.approx(1.5 * al2o3_mol)
    assert sim._cleaned_melt_reduction_source_terms_from_transition(
        primary,
        label=primary_label,
        target_oxides=("Al2O3", "MgO"),
    ) == {}
    assert primary_label not in breakdown.get(
        "terms_mol_o2_equiv_by_label", {}
    )
    assert sim._c6_back_reduction_redox_source_terms_from_transition(
        back,
        label=back_label,
    ) == {}
    assert back_label not in breakdown.get("terms_mol_o2_equiv_by_label", {})
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        before_fO2
    )


def test_c3_na_spent_residue_credit_does_not_cancel_reduction_source() -> None:
    sim = _make_sim(additives_kg={"Na": 12.0})
    sim._init_shuttle_inventory(CampaignPhase.C3_NA)
    sim.melt.campaign = CampaignPhase.C3_NA
    sim.melt.temperature_C = 1150.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -8.0
    sim._sync_oxygen_reservoir_mirror()

    sim._shuttle_inject_Na(target_stage="feo_cleanup", liquid_fraction=1.0)

    transition = next(
        transition
        for transition in reversed(sim.atom_ledger.transitions)
        if transition.name == "c3_na_shuttle_reduction"
    )
    label = "redox_source:c3_na_shuttle_reduction"
    feo_mol = _cleaned_melt_debit_mol(sim, transition, "FeO")
    residue_na2o_mol = _transition_account_species_mol(
        sim,
        transition,
        side="credits",
        account=SPENT_REDUCTANT_RESIDUE_ACCOUNT,
        species="Na2O",
    )
    cleaned_na2o_mol = _transition_account_species_mol(
        sim,
        transition,
        side="credits",
        account="process.cleaned_melt",
        species="Na2O",
    )

    assert feo_mol > 0.0
    assert residue_na2o_mol == pytest.approx(feo_mol)
    assert cleaned_na2o_mol == pytest.approx(0.0)
    assert sim._redox_source_breakdown_diagnostic()[
        "terms_mol_o2_equiv_by_label"
    ][label] == pytest.approx(-0.5 * feo_mol)


def test_c7_external_al_credit_lowers_fO2_from_committed_transition(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        BuiltinCaAluminothermicStepProvider,
        "_computed_thermo_margin_kj_per_mol_o2",
        lambda self, hold_temp_C: 2.0,
    )
    setpoints = copy.deepcopy(_load_yaml("setpoints.yaml"))
    setpoints["campaigns"]["C7"].update(
        {"enabled": True, "al_credit_limit_kg": 20.0, "extent_fraction": 0.1}
    )
    sim = PyrolysisSimulator(
        StubBackend(),
        setpoints,
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch("targeted_super_kreep_ore", mass_kg=1000.0)
    sim.start_campaign(CampaignPhase.C7_CA_ALUMINOTHERMIC)
    sim.melt.temperature_C = 1200.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim._sync_oxygen_reservoir_mirror()
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log

    sim._step_c7_ca_aluminothermic()

    transition = next(
        transition
        for transition in reversed(sim.atom_ledger.transitions)
        if transition.name == "ca_aluminothermic_c3a_credit_al"
    )
    label = "redox_source:c7_ca_aluminothermic_reduction"
    ca_mol = _transition_account_species_mol(
        sim,
        transition,
        side="credits",
        account="process.overhead_gas",
        species="Ca",
    )
    breakdown = sim._redox_source_breakdown_diagnostic()

    assert ca_mol > 0.0
    assert _transition_account_species_mol(
        sim,
        transition,
        side="debits",
        account=C7_AL_CREDIT_ACCOUNT,
        species="Al",
    ) > 0.0
    assert breakdown["terms_mol_o2_equiv_by_label"][label] == pytest.approx(
        -0.5 * ca_mol
    )
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log < before_fO2


def test_c7_in_situ_al_route_does_not_double_count_prior_reducing_power(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        BuiltinCaAluminothermicStepProvider,
        "_computed_thermo_margin_kj_per_mol_o2",
        lambda self, hold_temp_C: 2.0,
    )
    setpoints = copy.deepcopy(_load_yaml("setpoints.yaml"))
    setpoints["campaigns"]["C7"].update(
        {"enabled": True, "al_credit_limit_kg": 0.0, "extent_fraction": 0.1}
    )
    sim = PyrolysisSimulator(
        StubBackend(),
        setpoints,
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch("targeted_super_kreep_ore", mass_kg=1000.0)
    sim.atom_ledger.load_external_mol(
        "process.metal_phase",
        {"Al": 1000.0},
        source="test in-situ Al inventory",
    )
    sim.start_campaign(CampaignPhase.C7_CA_ALUMINOTHERMIC)
    sim.melt.temperature_C = 1200.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim._sync_oxygen_reservoir_mirror()
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log

    sim._step_c7_ca_aluminothermic()

    transition = next(
        transition
        for transition in reversed(sim.atom_ledger.transitions)
        if transition.name == "ca_aluminothermic_c3a_in_situ_al"
    )
    label = "redox_source:c7_ca_aluminothermic_reduction"
    breakdown = sim._redox_source_breakdown_diagnostic()

    assert _transition_account_species_mol(
        sim,
        transition,
        side="debits",
        account="process.metal_phase",
        species="Al",
    ) > 0.0
    assert label not in breakdown.get("terms_mol_o2_equiv_by_label", {})
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        before_fO2
    )


def test_c7_mixed_in_situ_and_external_al_counts_only_external_credit_sink(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        BuiltinCaAluminothermicStepProvider,
        "_computed_thermo_margin_kj_per_mol_o2",
        lambda self, hold_temp_C: 2.0,
    )
    setpoints = copy.deepcopy(_load_yaml("setpoints.yaml"))
    setpoints["campaigns"]["C7"].update(
        {"enabled": True, "al_credit_limit_kg": 20.0, "extent_fraction": 0.1}
    )
    sim = PyrolysisSimulator(
        StubBackend(),
        setpoints,
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch("targeted_super_kreep_ore", mass_kg=1000.0)
    sim.atom_ledger.load_external_mol(
        "process.metal_phase",
        {"Al": 2.0},
        source="test limited in-situ Al inventory",
    )
    sim.start_campaign(CampaignPhase.C7_CA_ALUMINOTHERMIC)
    sim.melt.temperature_C = 1200.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim._sync_oxygen_reservoir_mirror()

    sim._step_c7_ca_aluminothermic()

    in_situ = next(
        transition
        for transition in sim.atom_ledger.transitions
        if transition.name == "ca_aluminothermic_c3a_in_situ_al"
    )
    credit = next(
        transition
        for transition in sim.atom_ledger.transitions
        if transition.name == "ca_aluminothermic_c3a_credit_al"
    )
    label = "redox_source:c7_ca_aluminothermic_reduction"
    credit_ca_mol = _transition_account_species_mol(
        sim,
        credit,
        side="credits",
        account="process.overhead_gas",
        species="Ca",
    )
    total_ca_mol = credit_ca_mol + _transition_account_species_mol(
        sim,
        in_situ,
        side="credits",
        account="process.overhead_gas",
        species="Ca",
    )
    breakdown = sim._redox_source_breakdown_diagnostic()

    assert sim._c7_aluminothermic_redox_source_terms_from_transition(
        in_situ,
        label=label,
    ) == {}
    assert credit_ca_mol > 0.0
    assert total_ca_mol > credit_ca_mol
    assert breakdown["terms_mol_o2_equiv_by_label"][label] == pytest.approx(
        -0.5 * credit_ca_mol
    )
    assert breakdown["terms_mol_o2_equiv_by_label"][label] != pytest.approx(
        -0.5 * total_ca_mol
    )


def test_sio_evaporative_o_loss_source_term_from_committed_transition() -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1600.0
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
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    before_overhead_o2_mol = sim.atom_ledger.mol_by_account(
        "process.overhead_gas"
    ).get("O2", 0.0)
    sp_data = sim.vapor_pressures["oxide_vapors"]["SiO"]
    rate_kg_hr = 0.01

    credited_condensed_kg = sim._credit_evaporation_transition(
        "SiO",
        rate_kg_hr,
        rate_kg_hr,
        sp_data,
    )

    transition = sim.atom_ledger.transitions[-1]
    label = "redox_source:evaporative_oxygen_loss"
    o2_mol = _transition_account_species_mol(
        sim,
        transition,
        side="credits",
        account="process.overhead_gas",
        species="O2",
    )
    breakdown = sim._redox_source_breakdown_diagnostic()
    snapshot = sim._make_snapshot()
    summary = build_per_hour_summary(sim, snapshot)

    assert credited_condensed_kg == pytest.approx(0.0)
    assert transition.name == "evaporate_SiO"
    assert o2_mol > 0.0
    assert breakdown["terms_mol_o2_equiv_by_label"][label] == pytest.approx(
        -o2_mol
    )
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log < before_fO2
    assert sim.atom_ledger.mol_by_account("process.overhead_gas").get(
        "O2", 0.0
    ) == pytest.approx(before_overhead_o2_mol + o2_mol)
    assert sim.melt.oxygen_reservoir.exchange_o2_mol == pytest.approx(
        exchange_o2_mol
    )
    assert sim.melt.oxygen_reservoir.exchange_transition_name == (
        exchange_transition_name
    )
    assert sim.melt.oxygen_reservoir.exchange_direction.split("|")[0] == (
        exchange_direction
    )
    assert label in summary["redox_source_breakdown"][
        "terms_mol_o2_equiv_by_label"
    ]
    assert summary["redox_source_breakdown"]["ferric_divergence"][
        "warning_threshold_abs"
    ] == pytest.approx(FERRIC_DIVERGENCE_WARNING_THRESHOLD)


@pytest.mark.parametrize("species", ["Na", "K", "Fe", "Mg"])
def test_elemental_evaporation_metal_loss_oxidizes_from_committed_oxide_debit(
    species,
) -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -8.0
    sim._sync_oxygen_reservoir_mirror()
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    sp_data = sim.vapor_pressures["metals"][species]
    stoich = sim._evaporation_stoich(species, sp_data)
    parent_oxide = stoich["parent_oxide"]
    available_kg = sim.atom_ledger.kg_by_account("process.cleaned_melt").get(
        parent_oxide,
        0.0,
    )
    rate_kg_hr = min(
        0.001,
        0.1 * available_kg / float(stoich["oxide_per_product_kg"]),
    )

    sim._credit_evaporation_transition(species, rate_kg_hr, rate_kg_hr, sp_data)

    transition = sim.atom_ledger.transitions[-1]
    label = "redox_source:evaporative_metal_loss"
    parent_oxide_o2_equiv_mol = _transition_account_o2_equiv_mol(
        sim,
        transition,
        side="debits",
        account="process.cleaned_melt",
        species=parent_oxide,
    )
    overhead_o2_mol = _transition_account_species_mol(
        sim,
        transition,
        side="credits",
        account="process.overhead_gas",
        species="O2",
    )
    buffer_o2_mol = _transition_account_species_mol(
        sim,
        transition,
        side="credits",
        account="reservoir.fo2_buffer",
        species="O2",
    )
    breakdown = sim._redox_source_breakdown_diagnostic()

    assert rate_kg_hr > 0.0
    assert transition.name == f"evaporate_{species}"
    assert parent_oxide_o2_equiv_mol > 0.0
    assert overhead_o2_mol == pytest.approx(0.0)
    assert buffer_o2_mol == pytest.approx(parent_oxide_o2_equiv_mol)
    assert breakdown["terms_mol_o2_equiv_by_label"][label] == pytest.approx(
        parent_oxide_o2_equiv_mol
    )
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log > before_fO2


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


def test_fe_redox_respeciation_closes_scalar_ledger_divergence_with_real_o2() -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.p_total_mbar = 10.0
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 10_000.0},
        source="test explicit headspace oxygen for Fe redox re-speciation",
    )
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -3.0
    sim._sync_oxygen_reservoir_mirror()

    before = sim._ledger_ferric_fraction_diagnostic()
    before_o2 = sim.atom_ledger.mol_by_account("process.overhead_gas")["O2"]

    diagnostic = sim._apply_fe_redox_respeciation()

    after = sim._ledger_ferric_fraction_diagnostic()
    after_o2 = sim.atom_ledger.mol_by_account("process.overhead_gas")["O2"]
    melt_mol = sim.atom_ledger.mol_by_account("process.cleaned_melt")
    assert before["status"] == "warning"
    assert diagnostic["status"] == "ok"
    assert diagnostic["direction"] == "oxidizing"
    assert after["status"] == "ok"
    assert after["delta_abs"] <= FERRIC_DIVERGENCE_WARNING_THRESHOLD
    assert melt_mol.get("Fe2O3", 0.0) > 0.0
    assert after_o2 < before_o2


def test_fe_redox_respeciation_refuses_managed_floor_without_phantom_o2() -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = 1.5
    sim.melt.p_total_mbar = 1.5
    sim._overhead_headspace_config["enabled"] = True
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -10.0
    sim._sync_oxygen_reservoir_mirror()

    sim._apply_oxygen_reservoir_exchange()
    diagnostic = sim._apply_fe_redox_respeciation()

    assert diagnostic["status"] == "refused"
    assert diagnostic["reason"] == "fe_redox_respeciation_o2_unavailable"
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt").get(
        "Fe2O3",
        0.0,
    ) == pytest.approx(0.0)
    assert sim.atom_ledger.mol_by_account("process.overhead_gas").get(
        "O2",
        0.0,
    ) == pytest.approx(0.0)
    assert diagnostic["ferric_divergence_after"]["delta_abs"] >= 0.0
    assert diagnostic["ferric_divergence_after"]["attribution"] == (
        "managed_floor_unbacked"
    )


def test_fe_redox_respeciation_uses_evaporative_internal_o_without_overhead_draw() -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.p_total_mbar = 10.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -3.0
    sim._sync_oxygen_reservoir_mirror()
    sim._fe_redox_internal_o2_capacity_mol_this_hr = 10_000.0
    sim.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": 10_000.0},
        source="test evaporative internal O carrier",
    )
    sim._redox_source_terms_this_hr = {
        "redox_source:evaporative_metal_loss": 10_000.0,
    }
    sim._redox_source_applied_terms_this_hr = {
        "redox_source:evaporative_metal_loss": 10_000.0,
    }
    before_o2 = sim.atom_ledger.mol_by_account("process.overhead_gas").get(
        "O2",
        0.0,
    )
    before_buffer_o2 = sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get(
        "O2",
        0.0,
    )

    diagnostic = sim._apply_fe_redox_respeciation(
        oxygen_source=FE_REDOX_OXYGEN_SOURCE_EVAPORATIVE_METAL_LOSS,
    )

    melt_mol = sim.atom_ledger.mol_by_account("process.cleaned_melt")
    buffer_mol = sim.atom_ledger.mol_by_account("reservoir.fo2_buffer")
    after_o2 = sim.atom_ledger.mol_by_account("process.overhead_gas").get(
        "O2",
        0.0,
    )
    assert diagnostic["status"] == "ok"
    assert diagnostic["direction"] == "oxidizing"
    assert diagnostic["oxygen_source"] == "evaporative_metal_loss_internal"
    assert melt_mol.get("Fe2O3", 0.0) > 0.0
    assert buffer_mol.get("O2", 0.0) < before_buffer_o2
    assert buffer_mol.get("O2", 0.0) >= 0.0
    assert after_o2 == pytest.approx(before_o2)
    breakdown = sim._redox_source_breakdown_diagnostic()
    attempts = breakdown["fe_redox_respeciation_attempts"]
    assert attempts[-1]["oxygen_source"] == (
        "evaporative_metal_loss_internal"
    )
    assert attempts[-1]["status"] == "ok"


def test_fe_redox_respeciation_skips_below_liquid_calibration_band() -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 265.0
    sim.melt.p_total_mbar = 10.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -3.0
    sim._sync_oxygen_reservoir_mirror()
    sim._fe_redox_internal_o2_capacity_mol_this_hr = 1_000.0
    sim.atom_ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": 1_000.0},
        source="test sub-liquid internal O carrier",
    )
    before_melt = dict(sim.atom_ledger.mol_by_account("process.cleaned_melt"))
    before_buffer_o2 = sim.atom_ledger.mol_by_account("reservoir.fo2_buffer")[
        "O2"
    ]

    diagnostic = sim._apply_fe_redox_respeciation(
        oxygen_source=FE_REDOX_OXYGEN_SOURCE_EVAPORATIVE_METAL_LOSS,
    )

    after_melt = sim.atom_ledger.mol_by_account("process.cleaned_melt")
    after_buffer_o2 = sim.atom_ledger.mol_by_account("reservoir.fo2_buffer")[
        "O2"
    ]
    assert diagnostic["respeciation_status"] == "skipped_solid"
    assert diagnostic["reason"] == "fe_redox_respeciation_not_liquid"
    assert after_melt.get("Fe2O3", 0.0) == pytest.approx(
        before_melt.get("Fe2O3", 0.0),
    )
    assert after_buffer_o2 == pytest.approx(before_buffer_o2)


def test_full_c2a_step_reports_closed_ferric_divergence_after_respeciation() -> None:
    sim = _make_sim()
    sim.start_campaign(CampaignPhase.C2A)
    sim.melt.temperature_C = 1600.0
    sim.melt.target_temperature_C = 1600.0
    sim.melt.p_total_mbar = 10.0
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 10_000.0},
        source="test explicit headspace oxygen for full-step Fe redox re-speciation",
    )
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -3.0
    sim._sync_oxygen_reservoir_mirror()

    snapshot = sim.step()
    summary = build_per_hour_summary(sim, snapshot)

    divergence = snapshot.oxygen_reservoir["ferric_divergence"]
    assert divergence["status"] == "ok"
    assert divergence["delta_abs"] <= FERRIC_DIVERGENCE_WARNING_THRESHOLD
    assert snapshot.composition_wt_pct["Fe2O3"] > 0.0
    assert summary["fe_redox_split"]["ferric_frac"] == pytest.approx(
        divergence["ledger_ferric_fraction"],
        abs=FERRIC_DIVERGENCE_WARNING_THRESHOLD,
    )


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


def test_native_fe_partition_vacuum_exceeds_pn2_and_small_pool_vaporizes() -> None:
    sim = _make_sim()
    sim.melt.temperature_C = 1700.0
    sim.melt.p_total_mbar = 0.0
    sim.overhead.pressure_mbar = 0.0
    sim.overhead.composition = {"Fe": 0.0}

    vacuum_small = sim._native_fe_partition_diagnostic(0.5)
    vacuum_pool = sim._native_fe_partition_diagnostic(100.0)

    sim.melt.p_total_mbar = 10.0
    sim.overhead.pressure_mbar = 10.0
    sim.overhead.composition = {"N2": 10.0, "Fe": 0.0}
    sim.melt.background_gas_species = "N2"
    pn2_pool = sim._native_fe_partition_diagnostic(100.0)

    assert vacuum_small["native_fe_vapor_mol"] == pytest.approx(0.5)
    assert vacuum_small["native_fe_tap_mol"] == pytest.approx(0.0)
    assert vacuum_pool["native_fe_vapor_escape_fraction_of_pool"] > pn2_pool[
        "native_fe_vapor_escape_fraction_of_pool"
    ]


def test_pn2_native_fe_partition_e2e_drains_tap_and_reports_stage3_fe_wt() -> None:
    sim = _make_sim()
    sim.campaign_mgr.overrides["C2A_staged"] = {"hold_temp_C": 1700.0}
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    sim.melt.temperature_C = 1650.0
    sim.melt.campaign_hour = 7
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -10.0
    sim.melt.oxygen_reservoir.reference_T_K = sim.melt.temperature_C + 273.15
    sim._sync_oxygen_reservoir_mirror()

    snapshot = sim.step()
    summary = build_per_hour_summary(sim, snapshot, include_fe_redox_split=True)
    partition = snapshot.fe_redox_split["native_fe_partition"]
    tap_mol = sim.atom_ledger.mol_by_account("terminal.drain_tap_material")

    assert snapshot.campaign == CampaignPhase.C2A_STAGED
    assert 1650.0 <= snapshot.temperature_C <= 1700.0
    assert summary["P_total_bar"] == pytest.approx(0.01)
    assert snapshot.overhead.composition["N2"] == pytest.approx(10.0)
    assert partition["native_fe_pool_mol"] > 0.0
    assert partition["native_fe_tap_mol"] > partition["native_fe_vapor_mol"]
    assert partition["native_fe_vapor_escape_fraction_of_pool"] < 0.001
    assert partition["overhead_pressure_pa"] == pytest.approx(1000.0)
    assert partition["carrier_gas"] == "N2"
    assert tap_mol["Fe"] == pytest.approx(partition["native_fe_tap_mol"])
    assert sim.train.stages[1].collected_kg.get("Fe", 0.0) > 0.0
    assert "stage_3_fe_wt_pct" not in partition
    stage_3_capture = summary["stage_3_capture"]
    stage_3_non_fe_kg = stage_3_capture["total_kg"] - stage_3_capture["Fe_kg"]
    assert snapshot.evap_flux.species_kg_hr["SiO"] > 1.0e-7
    assert sim.train.stages[3].collected_kg.get("SiO2", 0.0) > 1.0e-8
    assert stage_3_capture["Fe_kg"] > 0.0
    assert stage_3_non_fe_kg > 1.0e-8
    assert stage_3_capture["Fe_wt_pct"] == pytest.approx(
        100.0 * stage_3_capture["Fe_kg"] / stage_3_capture["total_kg"]
    )
    assert stage_3_capture["Fe_wt_pct"] < 100.0
    assert abs(snapshot.mass_balance_error_pct) <= 5e-12
