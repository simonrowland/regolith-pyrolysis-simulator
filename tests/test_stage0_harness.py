"""Early-melt Stage-0 harness (chunk H1)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.backends import BackendSelectionPolicy, CachedRealBackend, CachedRealConfig
from simulator.corpus_version import current_corpus_version, interoperable_corpus_versions
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.session import SimSession, SimSessionConfig
from simulator.stage0_harness import (
    FOULANT_GROUPS,
    Stage0HarnessError,
    _capture_cleaned_melt_kg,
    default_max_stage0_hours,
    run_stage0_harness,
    run_stage0_harness_from_config,
)
from simulator.state import CampaignPhase

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_yaml(name: str) -> dict:
    with (DATA_DIR / name).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _feedstocks(*, include_debug: bool = False) -> dict:
    feedstocks = _load_yaml("feedstocks.yaml")
    if include_debug:
        feedstocks.update(_load_yaml("debug_feedstocks.yaml"))
    return feedstocks


def _session_config(feedstock_id: str, **overrides) -> SimSessionConfig:
    setpoints = _load_yaml("setpoints.yaml")
    # Pending t-194 grounded Cr/Mn alphas; alpha=1.0 prototype fallback.
    setpoints.setdefault("chemistry_kernel", {})["allow_unmeasured_alpha_fallback"] = True
    values = {
        "feedstock_id": feedstock_id,
        "feedstocks": _feedstocks(),
        "setpoints": setpoints,
        "vapor_pressures": _load_yaml("vapor_pressures.yaml"),
        "campaign": "C0",
        "backend_name": "internal-analytical",
        "backend_policy": BackendSelectionPolicy.RUNNER_STRICT,
    }
    if feedstock_id == "mars_sulfate_rich":
        fs = values["feedstocks"][feedstock_id]
        values["additives_kg"] = {
            "C": PyrolysisSimulator._carbon_reductant_required_kg(fs, 1000.0),
        }
    values.update(overrides)
    return SimSessionConfig(**values)


def test_default_max_stage0_hours_derives_from_setpoints():
    setpoints = _load_yaml("setpoints.yaml")
    expected = (
        float(setpoints["campaigns"]["C0"]["max_hold_hr"])
        + float(setpoints["campaigns"]["C0b_p_cleanup"]["max_hold_hr"])
        + 8.0
    )
    assert default_max_stage0_hours(setpoints) == pytest.approx(expected)


def test_real_feedstock_stops_at_c0b_path_ab_pause(monkeypatch):
    session = SimSession().start(_session_config("lunar_mare_low_ti"))
    ledger = session.simulator.atom_ledger
    initial_p2o5_mol = ledger.mol_by_account("process.cleaned_melt").get(
        "P2O5", 0.0
    )
    initial_p_atoms = sum(
        ledger.atom_moles_by_account(account).get("P", 0.0)
        for account in ledger.mol_by_account()
    )
    original_advance = session.advance
    c0b_entry: dict[str, float | int] = {}

    def advance_with_c0b_boundary():
        previous_campaign = session.simulator.melt.campaign
        step = original_advance()
        if (
            previous_campaign != CampaignPhase.C0B
            and session.simulator.melt.campaign == CampaignPhase.C0B
        ):
            c0b_entry["p2o5_mol"] = ledger.mol_by_account(
                "process.cleaned_melt"
            ).get("P2O5", 0.0)
            c0b_entry["transition_count"] = len(ledger.transitions)
        return step

    monkeypatch.setattr(session, "advance", advance_with_c0b_boundary)
    result = run_stage0_harness(session)

    assert result.early_melt_reached is True
    assert result.stop_reason == "c0b_path_ab_pause"
    assert result.total_hours < 150
    assert session.simulator.melt.campaign == CampaignPhase.C0B
    assert session.simulator.paused_for_decision is True
    assert result.cleaned_melt_kg
    assert result.verdicts is not None
    assert result.verdicts["verdict_a"]["warn_only"] is True

    # b-133: C0b is a real P cleanup, not a label over zero removal.  The
    # atom-balanced evaporation transition must debit melt P2O5 and retain the
    # carrier phosphorus in the offgas account.  HI-2 closes the whole ledger.
    final_p2o5_mol = ledger.mol_by_account("process.cleaned_melt").get(
        "P2O5", 0.0
    )
    final_p_atoms = sum(
        ledger.atom_moles_by_account(account).get("P", 0.0)
        for account in ledger.mol_by_account()
    )
    offgas_p_atoms = ledger.atom_moles_by_account("terminal.offgas").get(
        "P", 0.0
    )
    assert initial_p2o5_mol > 0.0
    assert final_p2o5_mol < initial_p2o5_mol
    assert c0b_entry
    assert final_p2o5_mol < c0b_entry["p2o5_mol"]
    # Trace-removal regression floor, not a process-efficacy claim.  This is
    # six orders above binary64 resolution at the ~7 mol inventory scale and
    # prevents an arbitrarily tiny positive float from satisfying C0b.
    assert c0b_entry["p2o5_mol"] - final_p2o5_mol > 1.0e-9
    c0b_transitions = ledger.transitions[int(c0b_entry["transition_count"]) :]
    c0b_p_transitions = [
        transition
        for transition in c0b_transitions
        if transition.debit_atom_moles(ledger.registry).get("P", 0.0) > 0.0
    ]
    assert c0b_p_transitions
    assert all(
        transition.credit_atom_moles(ledger.registry).get("P", 0.0)
        == pytest.approx(
            transition.debit_atom_moles(ledger.registry).get("P", 0.0),
            rel=1.0e-12,
            abs=1.0e-18,
        )
        for transition in c0b_p_transitions
    )
    # Hourly overhead bleed has moved most retained carrier P into the terminal
    # product account at this pause; the remainder stays in the real overhead
    # account and is included by the exact all-account P closure above.
    assert offgas_p_atoms > 0.0
    assert final_p_atoms == pytest.approx(initial_p_atoms, rel=1.0e-10, abs=1.0e-10)
    assert ledger.close_report()["balanced"] is True
    assert abs(session.simulator._make_snapshot().mass_balance_error_pct) <= 5.0e-12

    carriers = {"PO", "PO2", "P2", "P4", "P4O6", "P4O10"}
    flux_overlay = session.simulator._last_vapour_batch_flux_overlay
    channel_states = flux_overlay["batch_channel_states"]
    assert carriers <= set(channel_states)
    assert channel_states["P2O5_gas"] == "refusal"
    assert "P2O5_gas" not in flux_overlay["batch_pa_by_species"]
    assert {species: channel_states[species] for species in carriers} == {
        species: "eligible" for species in carriers
    }
    batch_report = session.simulator._last_vapour_batch_report
    channels = batch_report["channels_by_species"]
    retired_channel = channels["P2O5_gas"]
    assert retired_channel["is_refused"] is True
    assert retired_channel["is_union_flux_eligible"] is False
    assert retired_channel["flux"]["kind"] == "refusal"
    for species in carriers:
        extra = channels[species]["extra"]
        assert extra["alpha_authority_status"] == "analytical_upper_bound"
        assert extra["alpha_inventory_policy"] == (
            "inventory_eligible_analytical_upper_bound_noncertifying"
        )
    assert "P2O5_gas" not in ledger.mol_by_account("terminal.offgas")
    assert all(
        all(
            "P2O5_gas" not in lot.species_kg
            for lot in (*transition.debits, *transition.credits)
        )
        for transition in c0b_transitions
    )


def test_debug_feedstock_stops_on_campaign_leave():
    session = SimSession().start(
        _session_config(
            "debug_pure_feo",
            feedstocks=_feedstocks(include_debug=True),
        )
    )
    result = run_stage0_harness(session)

    assert result.stop_reason == "campaign_left_stage0"
    assert session.simulator.melt.campaign == CampaignPhase.C2A_STAGED
    assert result.total_hours < 150


def test_max_stage0_hours_guard_fails_loud():
    session = SimSession().start(_session_config("lunar_mare_low_ti"))
    with pytest.raises(Stage0HarnessError) as excinfo:
        run_stage0_harness(session, max_stage0_hours=1.0)

    assert excinfo.value.reason == "stage0_did_not_converge"
    assert session.simulator.melt.campaign in (
        CampaignPhase.C0,
        CampaignPhase.C0B,
    )


def test_disposition_timeline_grouped_and_ratified_phases():
    session = SimSession().start(_session_config("lunar_mare_low_ti"))
    result = run_stage0_harness(session)

    assert result.disposition_timeline
    for entry in result.disposition_timeline:
        assert set(entry.by_group) == set(FOULANT_GROUPS)
        if entry.campaign == "C0":
            assert entry.stage0_phase == "phase_2_vacuum"
            assert entry.ratified_ceiling_C == pytest.approx(1350.0)
        elif entry.campaign == "C0B":
            assert entry.stage0_phase == "phase_1_oxidizing"
            assert entry.ratified_ceiling_C == pytest.approx(1050.0)


@pytest.mark.parametrize("feedstock_key", ["mars_sulfate_rich", "ci_carbonaceous_chondrite"])
def test_messy_feedstock_produces_nonempty_bakeoff_timeline(feedstock_key):
    result = run_stage0_harness_from_config(_session_config(feedstock_key))

    assert result.disposition_timeline
    has_group_event = any(
        any(events for events in entry.by_group.values())
        for entry in result.disposition_timeline
    )
    assert has_group_event


@pytest.mark.parametrize("feedstock_key", ["mars_sulfate_rich", "ci_carbonaceous_chondrite"])
def test_messy_harness_forces_subprocess_backend_route(monkeypatch, feedstock_key):
    calls = []

    def fake_resolve_backend(backend_name, policy, **kwargs):
        calls.append((backend_name, policy, kwargs))
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    monkeypatch.setattr("simulator.session.resolve_backend", fake_resolve_backend)
    config = _session_config(
        feedstock_key,
        backend_name="alphamelts",
        backend_config={
            "mode": "thermoengine",
            "python_bridge": "pymagemin",
            "alphamelts": {
                "mode": "thermoengine",
                "python_bridge": "pymagemin",
            },
        },
    )

    SimSession().start(config)

    assert calls
    # BUG-066/067: subprocess forcing now happens INSIDE resolve_backend (the shared
    # resolution point, so the worker/grind path is covered too, not just SimSession).
    # SimSession signals the requirement + passes the inputs; resolve_backend forces.
    # (Previously SimSession pre-forced the config dict before calling resolve_backend.)
    from simulator.backends import stage0_subprocess_backend_config

    kwargs = calls[0][2]
    assert kwargs["stage0_subprocess_required"] is True
    assert kwargs["feedstock_id"] == feedstock_key
    assert kwargs["feedstocks"] is not None
    # The shared forcing function must produce a subprocess route for these inputs.
    forced = stage0_subprocess_backend_config(
        config.backend_name,
        config.backend_config,
        subprocess_required=kwargs["stage0_subprocess_required"],
    )
    assert forced["mode"] == "subprocess"
    assert forced["python_bridge"] == "subprocess"
    assert forced["alphamelts"]["mode"] == "subprocess"
    assert forced["alphamelts"]["python_bridge"] == "subprocess"


def test_messy_harness_rejects_inprocess_backend_override():
    class UnsafeThermoEngineBackend:
        name = "alphamelts"
        _mode = "thermoengine"

    config = _session_config("ci_carbonaceous_chondrite")

    with pytest.raises(RuntimeError, match="requires subprocess"):
        SimSession().start(config, backend=UnsafeThermoEngineBackend())


def test_messy_harness_rejects_cached_real_inprocess_live_fill(tmp_path):
    class UnsafeLiveBackend:
        name = "alphamelts"
        _mode = "thermoengine"

        def is_available(self):
            return True

    cached = CachedRealBackend(
        config=CachedRealConfig(
            db_path=tmp_path / "cached-real.sqlite",
            authorized_backend_name="alphamelts",
            corpus_version=current_corpus_version(),
            interoperable_corpus_versions=interoperable_corpus_versions(),
            authorized_backend_version="test 1.0.0",
            miss_policy="live-fill",
        ),
        live_backend=UnsafeLiveBackend(),
    )
    config = _session_config(
        "ci_carbonaceous_chondrite",
        backend_name="cached-real",
        reduced_real_cache={
            "db_path": str(tmp_path / "cached-real.sqlite"),
            "authorized_backend_name": "alphamelts",
            "corpus_version": current_corpus_version(),
            "interoperable_corpus_versions": interoperable_corpus_versions(),
            "authorized_backend_version": "test 1.0.0",
            "miss_policy": "live-fill",
        },
    )

    with pytest.raises(RuntimeError, match="cached-real live-fill requires subprocess"):
        SimSession().start(config, backend=cached)


def test_mars_sulfate_diagnostic_splits_land_in_timeline():
    result = run_stage0_harness_from_config(_session_config("mars_sulfate_rich"))

    mineral_events = [
        event
        for entry in result.disposition_timeline
        for event in entry.by_group["other_mineral_contaminant"]
    ]
    sulfate_events = [
        event for event in mineral_events
        if event.get("reaction_family") == "sulfate_decomp"
    ]
    assert sulfate_events
    assert any(event.get("source") == "diagnostic" for event in sulfate_events)


def test_comet_runtime_emits_uncertain_carbon_partition_interval():
    result = run_stage0_harness_from_config(_session_config("comet_nucleus"))

    events = [
        event
        for entry in result.disposition_timeline
        for event in entry.by_group["refractory_carbon"]
        if event.get("reaction_family") == "partition_carbon"
    ]
    uncertain = [
        event
        for event in events
        if event.get("disposition") == "uncertain_partition"
    ]

    assert uncertain
    event = uncertain[0]
    assert event["interval_required"] is True
    assert event["feed_kg"] > 0.0
    assert event["declared_c_mol"] > 0.0
    assert event["declared_C_kg"] > 0.0
    assert event["refractory_fraction_interval"] == [0.0, 1.0]
    assert event["refractory_C_mol_interval"] == pytest.approx([
        0.0,
        event["declared_c_mol"],
    ])
    assert "burned_kg" not in event
    assert "refractory_C_kg" not in event


def test_carbon_burned_mass_uses_declared_c_basis_not_carrier_kg():
    session = SimSession().start(_session_config("ci_carbonaceous_chondrite"))
    result = run_stage0_harness(session)

    burned_events = [
        event
        for entry in result.disposition_timeline
        for event in entry.by_group["trapped_gasses"]
        if event.get("reaction_family") == "partition_carbon"
        and event.get("disposition") == "burned"
    ]
    residual_events = [
        event
        for entry in result.disposition_timeline
        for event in entry.by_group["refractory_carbon"]
        if event.get("reaction_family") == "partition_carbon"
        and event.get("disposition") == "residual"
    ]

    assert burned_events
    assert residual_events
    burned = burned_events[0]
    residual = residual_events[0]

    assert burned["mass_basis"] == "declared_C"
    assert burned["burned_kg"] == pytest.approx(burned["burned_C_kg"])
    assert burned["burned_kg"] == pytest.approx(burned["labile_C_kg"])
    assert burned["burned_kg"] < burned["feed_kg"]
    assert burned["labile_carrier_equivalent_kg"] < burned["feed_kg"]

    assert residual["mass_basis"] == "declared_C"
    assert residual["refractory_mol"] > 0.0
    assert residual["refractory_residual_mol"] > 0.0
    assert residual["refractory_C_kg"] > 0.0
    assert residual["refractory_residual_C_kg"] > 0.0
    assert residual["refractory_residual_C_kg"] <= residual["refractory_C_kg"]
    ledger = session.simulator.atom_ledger.kg_by_account("process.cleaned_melt")
    for species, kg in result.cleaned_melt_kg.items():
        assert ledger[species] == pytest.approx(kg, rel=0.0, abs=1e-12)


def test_cleaned_melt_matches_ledger_projection():
    session = SimSession().start(_session_config("lunar_mare_low_ti"))
    result = run_stage0_harness(session)
    ledger = session.simulator.atom_ledger.kg_by_account("process.cleaned_melt")

    for species, kg in result.cleaned_melt_kg.items():
        assert ledger[species] == pytest.approx(kg, rel=0.0, abs=1e-12)


def test_capture_cleaned_melt_does_not_mutate_melt_state():
    session = SimSession().start(_session_config("lunar_mare_low_ti"))
    session.advance()
    sim = session.simulator
    prior_comp = dict(sim.melt.composition_kg)
    prior_oxide = dict(sim.inventory.melt_oxide_kg)
    prior_total = sim.melt.total_mass_kg

    _capture_cleaned_melt_kg(sim)

    assert sim.melt.composition_kg == prior_comp
    assert sim.inventory.melt_oxide_kg == prior_oxide
    assert sim.melt.total_mass_kg == prior_total


def test_disposition_timeline_assigns_by_campaign_phase():
    result = run_stage0_harness_from_config(
        _session_config("ci_carbonaceous_chondrite"),
    )

    hours_with_diag = [
        entry.hour
        for entry in result.disposition_timeline
        if any(events for events in entry.by_group.values())
    ]
    assert len(hours_with_diag) >= 2
    assert len(set(hours_with_diag)) >= 2


def test_harness_shadow_parity_with_full_run_truncated():
    config = _session_config("lunar_mare_low_ti")
    harness_result = run_stage0_harness_from_config(config)

    session = SimSession().start(config)
    for _ in range(harness_result.total_hours):
        session.advance()

    sim = session.simulator
    assert sim.melt.hour == harness_result.total_hours
    assert _capture_cleaned_melt_kg(sim) == harness_result.cleaned_melt_kg
    assert sim.melt.campaign.name == "C0B"
    assert harness_result.stop_reason == "c0b_path_ab_pause"
