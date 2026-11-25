"""Inert-sweep evaporation must not oxidize the melt with its own O2.

Congruent metal evaporation (Na2O → 2 Na(g) + ½ O2, FeO → Fe(g) + ½ O2)
releases O2 at the surface. Under PN2_SWEEP that O2 leaves with the sweep.
The loss removes oxide in the melt's own Fe3+/Fe2+ proportion, so q is
unchanged and there is no fO2 tangent. The coproduct is gas.
"""

from __future__ import annotations

import math

import pytest

from simulator.engine_binding_admission import write_gate_curve_memo_record
from simulator.state import Atmosphere, CampaignPhase, EvaporationFlux
from tests.chemistry.conftest import _build_sim


_HOUR_T_C = 2000.0
_HOUR_T_K = _HOUR_T_C + 273.15


def _ferric_fraction(sim) -> float:
    melt = sim.atom_ledger.mol_by_account("process.cleaned_melt")
    feo_mol = float(melt.get("FeO", 0.0) or 0.0)
    fe2o3_mol = float(melt.get("Fe2O3", 0.0) or 0.0)
    total_fe_mol = feo_mol + 2.0 * fe2o3_mol
    if total_fe_mol <= 0.0:
        return 0.0
    return (2.0 * fe2o3_mol) / total_fe_mol


def _oxygen_atoms(sim) -> float:
    return math.fsum(
        sim.atom_ledger.atom_moles_by_account(account).get("O", 0.0)
        for account in sim.atom_ledger.mol_by_account()
    )


def _flux(species_kg_hr: dict[str, float]) -> EvaporationFlux:
    flux = EvaporationFlux(species_kg_hr=dict(species_kg_hr))
    flux.update_totals()
    return flux


def _prepare_c2a_sweep(sim, monkeypatch, *, fO2_log: float) -> None:
    sim.melt.temperature_C = _HOUR_T_C
    sim.melt.p_total_mbar = 10.0
    sim.melt.pO2_mbar = 0.0
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.campaign = CampaignPhase.C2A
    monkeypatch.setattr(
        sim,
        "_freeze_gate_curve_record",
        lambda: write_gate_curve_memo_record(
            sim,
            {
                "source": "test",
                "solidus_T_C": 1000.0,
                "liquidus_T_C": 1700.0,
                "path": ((1000.0, 0.0), (1700.0, 1.0)),
            },
            provider_outcome="produced",
        ),
    )
    sim._refresh_oxygen_reservoir_without_exchange(
        melt_intrinsic_fO2_log=fO2_log,
        reference_T_K=_HOUR_T_K,
    )
    sim._reset_redox_source_diagnostics_for_hour()


def _active_na_fe_rates(sim) -> dict[str, float]:
    """Half the Na parent and a twentieth of the Fe parent, in one hour.

    The fractions are large enough that the O2 coproduct is a real gas
    inventory, not a numerical speck. They are not a fitted yield.
    """

    rates: dict[str, float] = {}
    for species, parent_fraction in (("Na", 0.5), ("Fe", 0.05)):
        stoich = sim._evaporation_stoich(
            species, sim.vapor_pressures["metals"][species],
        )
        available_kg = sim.atom_ledger.kg_by_account(
            "process.cleaned_melt"
        ).get(stoich["parent_oxide"], 0.0)
        rate_kg_hr = (
            parent_fraction
            * available_kg
            / float(stoich["oxide_per_product_kg"])
        )
        assert rate_kg_hr > 0.0
        rates[species] = rate_kg_hr
    return rates


def _evaporate_hour(sim, rates: dict[str, float]) -> None:
    flux = _flux(rates)
    sim._configure_condensation_operating_conditions(flux)
    sim._route_to_condensation(flux)
    sim._update_melt_composition(flux)
    sim._settle_evaporative_redox_after_batch()


def _assert_sweep_did_not_oxidize(
    sim,
    *,
    q_before: float,
    fO2_before: float,
    buffer_before: float,
    oxygen_before: float,
) -> None:
    q_after = _ferric_fraction(sim)
    fO2_after = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    buffer_after = sim.atom_ledger.mol_by_account(
        "reservoir.fo2_buffer"
    ).get("O2", 0.0)
    assert q_after == pytest.approx(q_before, abs=1.0e-12)
    assert math.isfinite(fO2_after)
    # No evaporative tangent. q = 0 has no finite Kress inverse, so
    # settle publishes the Fe metal line of the post-hour composition
    # (d-463 snap): min(floor, ceiling). Na and Fe leaving change
    # a_FeO, so that line can move at fixed q. The evaporation source
    # does not move it: the oxygen-loss term is absent and the source
    # integral stays zero. A tangent would be a nonzero
    # redox_source_delta_ln_fO2.
    terms = dict(sim._redox_source_terms_this_hr)
    assert "redox_source:evaporative_metal_loss" not in terms
    assert "redox_source:evaporative_oxygen_loss" not in terms
    assert sim.melt.oxygen_reservoir.redox_source_delta_ln_fO2 == pytest.approx(
        0.0, abs=1.0e-12,
    )
    T_K = float(sim.melt.temperature_C) + 273.15
    assert fO2_after == pytest.approx(
        sim._ledger_consistent_melt_fO2_log(T_K), abs=1.0e-9,
    )
    # The line can move reduced as a_FeO falls. It cannot climb.
    assert fO2_after <= fO2_before + 1.0e-9
    assert buffer_after <= buffer_before + 1.0e-12

    # q = 0 publishes the Fe metal line. That line sits above
    # log10(numerical floor)+1, and the floor is not a headspace
    # fugacity the melt has to track. Coproduct is not a source.
    # The oxidation bug was the effervescence cap.
    assert fO2_after <= sim._melt_redox_fO2_upper_bound_log() - 1.0

    posted_overhead_o2 = False
    for transition in sim.atom_ledger.transitions:
        if not str(transition.name).startswith("evaporate_"):
            continue
        for lot in transition.credits:
            assert lot.account != "reservoir.fo2_buffer"
            if (
                lot.account == "process.overhead_gas"
                and float(lot.species_kg.get("O2", 0.0) or 0.0) > 0.0
            ):
                posted_overhead_o2 = True
    assert posted_overhead_o2

    assert _oxygen_atoms(sim) == pytest.approx(oxygen_before, abs=1.0e-6)
    assert abs(sim._make_snapshot().mass_balance_error_pct) < 5.0e-12


def test_pn2_sweep_c2a_hour_does_not_raise_q_or_melt_fo2(
    vapor_pressure_data, feedstocks_data, setpoints_data, monkeypatch,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    _prepare_c2a_sweep(sim, monkeypatch, fO2_log=-9.0)
    q_before = _ferric_fraction(sim)
    fO2_before = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    buffer_before = sim.atom_ledger.mol_by_account(
        "reservoir.fo2_buffer"
    ).get("O2", 0.0)
    oxygen_before = _oxygen_atoms(sim)

    _evaporate_hour(sim, _active_na_fe_rates(sim))

    _assert_sweep_did_not_oxidize(
        sim,
        q_before=q_before,
        fO2_before=fO2_before,
        buffer_before=buffer_before,
        oxygen_before=oxygen_before,
    )
    # Cold-train runtime enforcement is off, so the bleed's NoColdTrain
    # branch credits drained melt O2 to terminal.oxygen_melt_offgas_stored
    # and not the vacuum vent. force_drain_all is the sweep emptying the
    # holdup; a conductance-limited call can leave a later-hour remainder
    # that is still gas.
    coproduct_mol = sim.atom_ledger.mol_by_account(
        "process.overhead_gas"
    ).get("O2", 0.0)
    fO2_after_hour = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    assert coproduct_mol > 0.0
    stored_before = sim.atom_ledger.mol_by_account(
        "terminal.oxygen_melt_offgas_stored"
    ).get("O2", 0.0)
    vented_before = sim.atom_ledger.mol_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    ).get("O2", 0.0)
    sim._dispatch_overhead_bleed(force_drain_all=True)
    stored_after = sim.atom_ledger.mol_by_account(
        "terminal.oxygen_melt_offgas_stored"
    ).get("O2", 0.0)
    vented_after = sim.atom_ledger.mol_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    ).get("O2", 0.0)
    assert stored_after - stored_before == pytest.approx(coproduct_mol)
    assert vented_after == pytest.approx(vented_before, abs=1.0e-12)
    # The bleed moves gas. It does not republish the metal line.
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        fO2_after_hour, abs=1.0e-9,
    )


def test_pn2_sweep_coproduct_cannot_ferricize_at_large_redox_capacity(
    vapor_pressure_data, feedstocks_data, setpoints_data, monkeypatch,
):
    """No congruent tangent, at a huge C_m as well. The coproduct must
    not fund ferric. Published fO2 is the post-hour metal line, and the
    source integral stays zero at this C_m too.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    _prepare_c2a_sweep(sim, monkeypatch, fO2_log=-9.0)
    monkeypatch.setattr(
        sim,
        "_melt_redox_source_capacity_mol_per_ln_fO2",
        lambda **_kwargs: 1.0e6,
    )
    q_before = _ferric_fraction(sim)
    fO2_before = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    buffer_before = sim.atom_ledger.mol_by_account(
        "reservoir.fo2_buffer"
    ).get("O2", 0.0)
    oxygen_before = _oxygen_atoms(sim)

    _evaporate_hour(sim, _active_na_fe_rates(sim))

    _assert_sweep_did_not_oxidize(
        sim,
        q_before=q_before,
        fO2_before=fO2_before,
        buffer_before=buffer_before,
        oxygen_before=oxygen_before,
    )
