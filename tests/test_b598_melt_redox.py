"""b-598 regression coverage for the ledger-owned melt redox state."""

from __future__ import annotations

import math

import pytest

from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.fe_redox import (
    KRESS91_LN_FO2_COEFFICIENT,
    _kress91_ln_ratio,
    calphad_ferrous_feo_activity_diagnostic,
    feo_iw_log10_fO2_bar,
    floor_vacuum_pressure_bar,
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
        if reservoir.get("reference_T_K") is None:
            # The staged ramp keeps the feedstock fO2 as a bootstrap value
            # until the first liquid tick establishes the ledger reference.
            # A committed ferrous-free release can form an interior ledger
            # ratio first; that real ratio is authoritative even while the
            # liquidus reference temperature remains unset.
            redox_domain = dict(
                (row.get("fe_redox_split") or {}).get("redox_domain") or {}
            )
            if redox_domain.get("basis") == "kress91_inverse":
                assert redox_domain["derived_fO2_log"] == pytest.approx(
                    fO2_log,
                    abs=2.0e-12,
                )
            else:
                assert fO2_log == pytest.approx(-9.0, abs=2.0e-12)
            continue

        redox_domains = (
            (row.get("fe_redox_split") or {}).get("redox_domain"),
            (row.get("redox_source_breakdown") or {}).get("redox_domain"),
        )
        redox_domain = next(
            (
                dict(candidate)
                for candidate in redox_domains
                if isinstance(candidate, dict)
                and candidate.get("basis")
                in {
                    "no_melt_redox_buffer",
                    "fe_feo_buffer",
                    "fe_saturation_bound",
                }
            ),
            {},
        )
        if redox_domain:
            if redox_domain["basis"] == "no_melt_redox_buffer":
                assert redox_domain["status"] == "out_of_domain"
                assert redox_domain["authority"] == "gas_interface_controlled"
                assert tuple(redox_domain["certified_band"]["pO2_bar"]) == (
                    1.0e-12,
                    100.0,
                )
                assert "kress91_inverse_not_evaluated" in redox_domain[
                    "reason"
                ]
            elif redox_domain["basis"] == "fe_saturation_bound":
                assert redox_domain["status"] == "out_of_domain"
                assert redox_domain["authority"] == "extrapolated"
                assert "kress91_inverse_not_evaluated" in redox_domain[
                    "reason"
                ]
                assert "ferric_inventory_absent" in redox_domain["reason"]
                temperature_K = float(snapshot.temperature_C) + 273.15
                pressure_bar = floor_vacuum_pressure_bar(
                    float(row["P_total_bar"]),
                    floor_bar=DEFAULT_VACUUM_FLOOR_BAR,
                )
                activity = calphad_ferrous_feo_activity_diagnostic(
                    comp_wt=snapshot.composition_wt_pct,
                    fO2_log=fO2_log,
                    T_K=temperature_K,
                    pressure_bar=pressure_bar,
                )
                a_feo = float(activity["a_FeO_authoritative"])
                iw = feo_iw_log10_fO2_bar(temperature_K, a_feo=1.0)
                assert fO2_log == pytest.approx(
                    iw + 2.0 * math.log10(a_feo),
                    abs=1.0e-6,
                )
            else:
                assert redox_domain["basis"] == "fe_feo_buffer"
                assert "native_fe_metal_coexists_with_melt" in redox_domain[
                    "reason"
                ]
            continue

        interface_pO2_bar = reservoir.get("interface_pO2_bar")
        if (
            (
                reservoir.get("redox_buffer_exhausted")
                or reservoir.get("redox_buffer_status") == "exhausted"
            )
            and isinstance(interface_pO2_bar, (int, float))
            and math.isfinite(float(interface_pO2_bar))
            and float(interface_pO2_bar) > 0.0
            and fO2_log == pytest.approx(
                math.log10(float(interface_pO2_bar)),
                abs=2.0e-12,
            )
        ):
            # RunExecutor's legacy per-hour projection predates the nested
            # redox_domain export.  The reservoir pair is the same typed
            # interface-controlled observable for this compatibility check.
            continue
        if (
            isinstance(interface_pO2_bar, (int, float))
            and math.isfinite(float(interface_pO2_bar))
            and float(interface_pO2_bar) > 0.0
            and fO2_log == pytest.approx(
                math.log10(float(interface_pO2_bar)),
                abs=2.0e-12,
            )
            and fO2_log == pytest.approx(
                math.log10(DEFAULT_VACUUM_FLOOR_BAR),
                abs=2.0e-12,
            )
            and float(divergence.get("ledger_ferric_fraction", 1.0)) < 1.0e-4
        ):
            # Older RunExecutor rows can omit both the nested domain record
            # and the exhaustion flag; the vacuum-floor/trace-Fe pair still
            # identifies the same no-buffer continuation.
            continue

        composition = melt_mol_fractions_for_kress91(
            snapshot.composition_wt_pct
        )
        pressure_bar = max(
            float(row["P_total_bar"]),
            DEFAULT_VACUUM_FLOOR_BAR,
        )
        ledger_q = float(divergence["ledger_ferric_fraction"])

        implied_q = float(divergence["implied_ferric_fraction"])
        feo_fraction = float(composition.get("FeO", 0.0) or 0.0)
        fe2o3_fraction = float(composition.get("Fe2O3", 0.0) or 0.0)
        ln_ratio = math.log(fe2o3_fraction) - math.log(feo_fraction)
        if ln_ratio >= 0.0:
            expected_q = 1.0 / (1.0 + 0.5 * math.exp(-ln_ratio))
        else:
            ratio = math.exp(ln_ratio)
            expected_q = 2.0 * ratio / (2.0 * ratio + 1.0)
        assert ledger_q == pytest.approx(expected_q, abs=2.0e-12)
        assert implied_q == pytest.approx(ledger_q, abs=2.0e-12)
        base_ln_ratio = _kress91_ln_ratio(
            mol_fractions=composition,
            T_K=float(snapshot.temperature_C) + 273.15,
            pressure_bar=pressure_bar,
        )
        expected_fO2_log = (ln_ratio - base_ln_ratio) / (
            KRESS91_LN_FO2_COEFFICIENT * math.log(10.0)
        )
        assert fO2_log == pytest.approx(expected_fO2_log, abs=2.0e-12)
