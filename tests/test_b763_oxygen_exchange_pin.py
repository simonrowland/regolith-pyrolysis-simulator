"""C2A oxygen-exchange contract for an adopted melt.

Reference unset: dn is 0 and the hour carries the solid-state notice.
Reference set: ledger Fe3+/FeT and Kress at the current scalar, recomputed
from the snapshot oxides and from kress91, agree within the partial-melt
tolerance. The old fixed-pressure dn series is not re-pinned.
"""

from __future__ import annotations

import pytest

from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
from simulator.core import FERRIC_PARTIAL_MELT_AGREEMENT_ABS
from simulator.fe_redox import (
    floor_vacuum_pressure_bar,
    kress91_fe3_over_sigma_fe,
    melt_mol_fractions_for_kress91,
)
from simulator.runner import (
    DATA_DIR,
    PyrolysisRun,
    _apply_sio_wall_sweep_controls,
    _prepare_sio_campaign_start,
    _required_stage0_carbon_kg,
    load_config_bundle,
)
from simulator.state import MOLAR_MASS


# One live 24 h campaign each. xdist must not coschedule them.
pytestmark = [pytest.mark.serial, pytest.mark.xdist_group("serial")]


def _ledger_ferric_fraction(comp: dict) -> float:
    feo_wt = float(comp.get("FeO", 0.0) or 0.0)
    fe2o3_wt = float(comp.get("Fe2O3", 0.0) or 0.0)
    feo_mol = feo_wt / MOLAR_MASS["FeO"]
    fe2o3_mol = fe2o3_wt / MOLAR_MASS["Fe2O3"]
    oxidized_fe = feo_mol + 2.0 * fe2o3_mol
    if oxidized_fe <= 0.0:
        raise AssertionError(
            f"adopted hour has no FeO/Fe2O3 on the snapshot: {comp!r}"
        )
    return (2.0 * fe2o3_mol) / oxidized_fe


def _implied_ferric_fraction(comp: dict, *, fO2_log: float, T_K: float, pressure_bar: float) -> float:
    mol_fractions = melt_mol_fractions_for_kress91(comp)
    if not mol_fractions:
        raise AssertionError("adopted hour has no Kress91 composition basis")
    return float(kress91_fe3_over_sigma_fe(
        fO2_log=fO2_log,
        mol_fractions=mol_fractions,
        T_K=T_K,
        pressure_bar=pressure_bar,
    ))


def _c2a_exchange_hours(feedstock_id: str) -> list[dict]:
    """Same C2A setup as the SiO-yield campaign: 24 h, 1000 kg, builtin Antoine."""
    bundle = load_config_bundle(DATA_DIR)
    feedstock = bundle.feedstocks.get(feedstock_id, {})
    stage0_carbon_kg = _required_stage0_carbon_kg(feedstock, 1000.0)
    additives_kg = {}
    if stage0_carbon_kg > 1.0e-12:
        additives_kg["C"] = stage0_carbon_kg
    run = PyrolysisRun(
        feedstock_id=feedstock_id,
        campaign="C2A_continuous",
        hours=24,
        mass_kg=1000.0,
        backend_name=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        track="pyrolysis",
        additives_kg=additives_kg,
        engines={"vapor_pressure": "builtin-antoine"},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        force_builtin_vapor_pressure=True,
    )
    session = run._start_session()
    sim = session.simulator
    _prepare_sio_campaign_start(sim, t_low_c=None, t_hold_c=None, ramp_c_per_hr=None)
    _apply_sio_wall_sweep_controls(sim, liner_temperature_c=None, pO2_mbar=None)
    result = run._run_session(session)
    assert result.get("status") == "ok", result.get("error_message")
    pressure_bar = floor_vacuum_pressure_bar(
        float(sim.melt.p_total_mbar) / 1000.0
    )
    rows = []
    for snap in sim.record.snapshots:
        reservoir = dict(getattr(snap, "oxygen_reservoir", {}) or {})
        if "exchange_o2_mol" not in reservoir:
            raise AssertionError(
                f"hour {snap.hour} oxygen_reservoir is missing exchange_o2_mol"
            )
        if "melt_intrinsic_fO2_log" not in reservoir:
            raise AssertionError(
                f"hour {snap.hour} oxygen_reservoir is missing melt_intrinsic_fO2_log"
            )
        dn_mol = float(reservoir["exchange_o2_mol"])
        fO2_log = float(reservoir["melt_intrinsic_fO2_log"])
        reference_T_K = reservoir.get("reference_T_K")
        comp = dict(getattr(snap, "composition_wt_pct", {}) or {})
        species = dict(getattr(snap.evap_flux, "species_kg_hr", {}) or {})
        ledger = None
        implied = None
        delta = None
        try:
            ledger = _ledger_ferric_fraction(comp)
            implied = _implied_ferric_fraction(
                comp,
                fO2_log=fO2_log,
                T_K=float(snap.temperature_C) + 273.15,
                pressure_bar=pressure_bar,
            )
            delta = abs(implied - ledger)
        except AssertionError:
            if reference_T_K is not None:
                raise
        if reference_T_K is None:
            assert dn_mol == pytest.approx(0.0, abs=1e-15)
            notice = dict(reservoir.get("exchange_notice") or {})
            if notice.get("message") != (
                "solid-state ferric decomposition not modelled"
            ):
                raise AssertionError(
                    f"hour {snap.hour} missing solid-state notice: {notice!r}"
                )
        else:
            if delta is None:
                raise AssertionError(
                    f"hour {snap.hour} adopted a reference but the ferric "
                    "fractions could not be recomputed"
                )
            assert delta <= FERRIC_PARTIAL_MELT_AGREEMENT_ABS
        rows.append({
            "hour": int(snap.hour),
            "T_C": float(snap.temperature_C),
            "log_fO2": fO2_log,
            "ledger": ledger,
            "implied": implied,
            "delta_abs": delta,
            "kinetic_o2_mol": float(
                reservoir.get("kinetic_exchange_o2_mol", 0.0) or 0.0
            ),
            "settled_pO2_bar": float(
                reservoir.get("settled_headspace_pO2_bar", 0.0) or 0.0
            ),
            "dn_mol": dn_mol,
            "direction": str(reservoir.get("exchange_direction", "")),
            "bleed_o2_mol": float(
                getattr(snap, "melt_offgas_O2_mol_hr", 0.0) or 0.0
            ),
            "sio_kg_h": float(species.get("SiO", 0.0) or 0.0),
            "sio2_kg_h": float(
                species.get("SiO2(g)", species.get("SiO2", 0.0)) or 0.0
            ),
            "sio_species_kg_h": {
                key: float(value)
                for key, value in species.items()
                if "SiO" in str(key)
            },
        })
    return rows


def test_mars_basalt_c2a_oxygen_exchange_dn_and_fo2_pin() -> None:
    rows = _c2a_exchange_hours("mars_basalt")
    assert len(rows) == 24


def test_ci_carbonaceous_chondrite_c2a_oxygen_exchange_dn_and_fo2_pin() -> None:
    rows = _c2a_exchange_hours("ci_carbonaceous_chondrite")
    assert len(rows) == 24


def test_lunar_mare_low_ti_c2a_oxygen_exchange_stays_unadopted() -> None:
    rows = _c2a_exchange_hours("lunar_mare_low_ti")
    assert len(rows) == 24
    assert all(row["dn_mol"] == pytest.approx(0.0, abs=1e-15) for row in rows)
