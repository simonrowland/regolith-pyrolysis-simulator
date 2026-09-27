"""b-598 regression coverage for the ledger-owned melt redox state."""

from __future__ import annotations

import math

import pytest

from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.fe_redox import (
    KRESS91_FERRIC_FRACTION_EPSILON,
    kress91_log_fO2_from_fe3_over_sigma_fe,
    melt_mol_fractions_for_kress91,
)
from simulator.physical_constants import CATALOG_PHYSICAL_PRESSURE_CEILING_PA
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun


def test_lunar_c2a_170h_keeps_ledger_redox_bounded_and_consistent() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=170,
        mass_kg=1.0,
        backend_name="internal-analytical",
    )
    execution = RunExecutor().execute(run._session_config())

    assert execution.status == "ok"
    assert len(execution.snapshots) == 170
    assert len(execution.per_hour) == 170

    for snapshot, row in zip(execution.snapshots, execution.per_hour):
        assert abs(snapshot.mass_balance_error_pct) <= 5.0e-12
        reservoir = snapshot.oxygen_reservoir
        fO2_log = float(reservoir["melt_intrinsic_fO2_log"])
        assert math.isfinite(fO2_log)

        overlay = row.get("vapour_batch_flux_overlay") or {}
        selected_pressures = overlay.get("selected_runtime_pa_by_species") or {}
        assert all(
            math.isfinite(float(pressure))
            and float(pressure) <= CATALOG_PHYSICAL_PRESSURE_CEILING_PA
            for pressure in selected_pressures.values()
        )

        divergence = reservoir["ferric_divergence"]
        if divergence.get("warning"):
            # Before the first liquid Kress tick, the feedstock fO2 is a
            # bootstrap diagnostic and intentionally need not match the
            # FeO/Fe2O3 ledger.  It remains finite, reducing of the total
            # pressure, and inside the existing finite-candidate guard.
            assert fO2_log > -1.0e11
            assert fO2_log <= math.log10(
                max(float(row["P_total_bar"]), DEFAULT_VACUUM_FLOOR_BAR)
            )
            continue

        composition = melt_mol_fractions_for_kress91(
            snapshot.composition_wt_pct
        )
        pressure_bar = max(
            float(row["P_total_bar"]),
            DEFAULT_VACUUM_FLOOR_BAR,
        )
        ledger_q = float(divergence["ledger_ferric_fraction"])

        lower_bound = kress91_log_fO2_from_fe3_over_sigma_fe(
            fe3_over_sigma_fe=KRESS91_FERRIC_FRACTION_EPSILON,
            mol_fractions=composition,
            T_K=float(snapshot.temperature_C) + 273.15,
            pressure_bar=pressure_bar,
        )
        upper_bound = kress91_log_fO2_from_fe3_over_sigma_fe(
            fe3_over_sigma_fe=1.0 - KRESS91_FERRIC_FRACTION_EPSILON,
            mol_fractions=composition,
            T_K=float(snapshot.temperature_C) + 273.15,
            pressure_bar=pressure_bar,
        )
        assert lower_bound - 2.0e-12 <= fO2_log <= upper_bound + 2.0e-12

        implied_q = float(divergence["implied_ferric_fraction"])
        assert 1.0e-6 < ledger_q < 1.0 - 1.0e-6
        assert implied_q == pytest.approx(ledger_q, abs=2.0e-12)
        expected_fO2_log = kress91_log_fO2_from_fe3_over_sigma_fe(
            fe3_over_sigma_fe=ledger_q,
            mol_fractions=composition,
            T_K=float(snapshot.temperature_C) + 273.15,
            pressure_bar=pressure_bar,
        )
        assert fO2_log == pytest.approx(expected_fO2_log, abs=2.0e-12)
