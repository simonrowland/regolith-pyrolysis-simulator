"""Lab-schedule evaporation is booked from the melt, not the C0 label.

A benchtop schedule owns T and P. Holding the campaign at C0 must not
drop the evaporation debit. SiO2 -> SiO + 1/2 O2 credits oxygen into
the terminal account the Robinot reproduction reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun, build_per_hour_summary
from tests.test_lab_geometry_runtime import (
    dynamic_lab_schedule,
    dynamic_surface_geometry_fixture,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SIO_P_EQ_ABOVE_TOTAL_PA = 3844.0
_IMPOSED_TOTAL_PA = 1300.0


def _hot_argon_lab_schedule() -> dict:
    schedule = dynamic_lab_schedule()
    schedule["furnace_ceiling_C"] = 2000.0
    schedule["melt_temperature_C"] = [
        {"t_h": 0.0, "value": 2000.0, "unit": "C"},
        {"t_h": 2.0, "value": 2000.0, "unit": "C"},
    ]
    return schedule


def _sio_o2_per_vapor() -> float:
    catalog = yaml.safe_load(
        (_REPO_ROOT / "data" / "vapor_pressures.yaml").read_text()
    )
    return float(
        catalog["families"]["oxide_vapors_sio_family"]["physical_properties"][
            "species"
        ][
            "SiO"
        ]["stoich_O2_per_vapor"]
    )


def _sio_flux_request(*, p_eq_pa: float, commanded_pa: float) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"SiO2": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=2000.0,
        pressure_bar=commanded_pa / 1.0e5,
        control_inputs={
            "overhead_pressure_pa": commanded_pa,
            "commanded_pressure_pa": commanded_pa,
            "vapour_batch_flux_pressures_Pa": {"SiO": p_eq_pa},
            "overhead_partials_Pa": {},
            "molar_mass_kg_mol": {"SiO": 0.0440845},
            "stoich_by_species": {
                "SiO": {
                    "parent_oxide": "SiO2",
                    "oxide_per_product_kg": 1.362920787587333,
                    "O2_per_product_kg": 0.36292078758733326,
                }
            },
            "available_oxide_kg": {"SiO": 1.0},
            "melt_surface_area_m2": 1.0,
            "stir_factor": 1.0,
            "alpha": {"SiO": 0.1},
            "pipe_diameter_m": 0.02,
            "carrier_gas": "Ar",
            "evaporation_series_resistance": {
                "gas_resistance_enabled": False,
                "melt_resistance_enabled": False,
            },
        },
    )


def test_kernel_flags_p_eq_above_imposed_total_without_clipping() -> None:
    provider = BuiltinEvaporationFluxProvider()
    above = provider.dispatch(
        _sio_flux_request(
            p_eq_pa=_SIO_P_EQ_ABOVE_TOTAL_PA,
            commanded_pa=_IMPOSED_TOTAL_PA,
        )
    )
    at_total = provider.dispatch(
        _sio_flux_request(
            p_eq_pa=_IMPOSED_TOTAL_PA,
            commanded_pa=_IMPOSED_TOTAL_PA,
        )
    )
    vacuum = provider.dispatch(
        _sio_flux_request(
            p_eq_pa=_SIO_P_EQ_ABOVE_TOTAL_PA,
            commanded_pa=0.0,
        )
    )

    assert above.status == "ok"
    assert at_total.status == "ok"
    above_flux = above.diagnostic["evaporation_flux_kg_hr"]["SiO"]
    at_total_flux = at_total.diagnostic["evaporation_flux_kg_hr"]["SiO"]
    # Drive stays max(0, P_eq - P_bulk). Clipping P_eq to P_total would
    # make these two fluxes equal.
    assert above_flux / at_total_flux == pytest.approx(
        _SIO_P_EQ_ABOVE_TOTAL_PA / _IMPOSED_TOTAL_PA,
        rel=1.0e-9,
    )
    notices = above.diagnostic["equilibrium_pressure_exceeds_total_notices"]
    assert notices == [
        above.diagnostic["evaporation_series_resistance"]["SiO"][
            "total_pressure_regime_notice"
        ]
    ]
    assert notices[0]["reason"] == "equilibrium_pressure_exceeds_imposed_total"
    assert notices[0]["evidence_tier"] == "PREDICT_AND_FLAG"
    assert notices[0]["missing_regime"] == "bulk_flow_boiling_limited"
    assert notices[0]["bound"] == "hertz_knudsen_series_resistance"
    assert "equilibrium_pressure_exceeds_total_notices" not in at_total.diagnostic
    assert "equilibrium_pressure_exceeds_total_notices" not in vacuum.diagnostic


def test_lab_schedule_step_books_sio_and_coproduced_oxygen() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=1,
        mass_kg=1.0,
        backend_name="internal-analytical",
        setpoints_patch={"lab_geometry": dynamic_surface_geometry_fixture()},
        lab_schedule=_hot_argon_lab_schedule(),
        force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    execution = RunExecutor().execute(run._session_config())
    assert execution.status == "ok", execution.error_message
    assert len(execution.snapshots) == 1
    snapshot = execution.snapshots[0]
    row = build_per_hour_summary(execution.session.simulator, snapshot)

    assert row["campaign"] == "C0"
    assert row["T_C"] == pytest.approx(2000.0, abs=1.0)
    assert row["P_total_bar"] == pytest.approx(0.013, rel=1.0e-6)
    sio_kg_hr = row["vapor_species_kg_hr"]["SiO"]
    assert sio_kg_hr > 0.0
    oxygen_kg = row["O2_yield_kg_cumulative"]
    assert oxygen_kg == row["O2_source_side_potential_kg_cumulative"]
    assert oxygen_kg == pytest.approx(snapshot.oxygen_produced_kg)
    # One-hour step: booked SiO mass is the kg/h rate. The coproduct
    # floor leaves room for redox that puts some oxygen back into the melt.
    assert oxygen_kg >= sio_kg_hr * _sio_o2_per_vapor() * 0.5
