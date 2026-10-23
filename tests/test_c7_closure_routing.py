"""Pre-C7 runtime receipts, captured through production controls at f2ee412fa.

These are refactor pins, not experimental validation. The independent oxygen
balance and d-259 tests supply the physical and guard checks.
"""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path

import pytest

from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import CampaignPhase
from tests.magemin_test_support import close_test_owned_magemin_backend
from tests.test_c7_oxygen_pins import hex_tree
from tests.chemistry.test_vaporock_authority_promotion import _force_vaporock_available


# Reuse the stateful redox suite's serial engine lifecycle contract.
pytestmark = [pytest.mark.serial, pytest.mark.xdist_group("serial")]
PINS = Path(__file__).parent / "fixtures/c7_closure_routing.json"
SPECIES = ("SiO", "Na", "K", "Fe", "Mg")


def simulation(feed="lunar_mare_low_ti", campaign="C2A"):
    bundle = load_config_bundle()
    sim = PyrolysisSimulator(
        InternalAnalyticalBackend(), deepcopy(bundle.setpoints),
        bundle.feedstocks, bundle.vapor_pressures,
    )
    sim.load_batch(feed, mass_kg=1000.0,
                   additives_kg={"C": 30.0} if feed == "mars_basalt" else {})
    sim.start_campaign(CampaignPhase[campaign])
    return sim


def consumer_receipt(oxygen, *, legacy_sweep=False, quantized=False):
    sim = simulation("mars_basalt")
    try:
        sim.melt.temperature_C = 1600.0
        if legacy_sweep:
            from simulator.state import Atmosphere
            sim.melt.sweep_mol_per_s = None
            sim.melt.atmosphere = Atmosphere.PN2_SWEEP
        if quantized:
            from simulator.reduced_real_determinism import PT0DeterminismStore
            sim.configure_pt0_determinism_store(PT0DeterminismStore("capture"))
        # Admit actual molecules. A swept boundary reads the ledger rather than
        # a manually assigned reservoir mirror. Ar is not an oxygen source.
        o2_mol = sim._headspace_o2_mol_for_pO2_bar(oxygen)
        total_mol = sim._headspace_o2_mol_for_pO2_bar(sim.melt.p_total_mbar / 1000.0)
        sim.atom_ledger.load_external_mol(
            "process.overhead_gas", {"O2": o2_mol, "Ar": total_mol - o2_mol},
            material_origin="reagent", source="C7 routing initial gas",
        )
        raw = (sim._headspace_ledger_pO2_bar_from_o2_mol(o2_mol)
               if legacy_sweep else sim._physical_sweep_boundary()["partials_bar"].get("O2", 0.0))
        assert raw == pytest.approx(oxygen, rel=1e-12, abs=0.0)
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        requests = []
        dispatch = sim._dispatch_only
        legacy = sim._internal_analytical_equilibrium
        legacy_pressures = []
        # Keep the real registered diagnostic provider and its native oxygen
        # translation; reuse the existing backend seam to avoid a library run.
        shadow = _force_vaporock_available(sim)
        shadow_dispatch = shadow.dispatch
        shadow_receipts = []

        def observe_shadow(request):
            result = shadow_dispatch(request)
            shadow_receipts.append({name: getattr(result.control_audit, name)
                                    for name in ("requested", "applied", "notes")})
            return result

        shadow.dispatch = observe_shadow

        def observe_legacy():
            result = legacy()
            legacy_pressures.append({name: result.vapor_pressures_Pa.get(name, 0.0)
                                     for name in SPECIES})
            return result

        def observe(intent, **kwargs):
            result = dispatch(intent, **kwargs)
            if intent.value == "vapor_pressure":
                requests.append({"controls": kwargs["control_inputs"],
                                 "resolved": result.diagnostic.get("pO2_bar")})
            return result

        sim._dispatch_only = observe
        sim._internal_analytical_equilibrium = observe_legacy
        equilibrium = sim._get_equilibrium()
        assert len(requests) == len(legacy_pressures) == len(shadow_receipts) == 1
        controls, _ = sim._evaporation_flux_control_inputs(
            equilibrium, overhead_partials_Pa={},
        )
        return hex_tree({
            "raw": raw,
            "transport": sim._headspace_transport_pO2_bar(),
            "dispatch": sim._vapor_pressure_dispatch_pO2_bar(),
            "requests": requests,
            "legacy_pressures_Pa": legacy_pressures,
            "vaporock_transport_audit": shadow_receipts,
            "flux_oxygen": controls["pO2_bar"],
            "intrinsic": sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log,
        })
    finally:
        close_test_owned_magemin_backend(sim)


def _trajectory_rows(feed, campaign):
    sim = simulation(feed, campaign)
    sim.campaign_mgr.overrides[campaign] = {"min_hold_hr": 24.0, "max_hours": 24.0}
    if campaign == "C2A":
        # A declared hot-entry hold exercises evaporation during all 24 ticks.
        # Use the production recipe override, keeping controls and flux live.
        sim.campaign_mgr.overrides["C2A"] = {
            "ramp_rate": 0.0, "min_hold_hr": 24.0, "max_hours": 24.0,
        }
        sim.melt.temperature_C = 1600.0
    sio_source_sum = 0.0
    native_sources = []
    native_split = sim._apply_native_fe_saturation_split

    def observe_native_split(**kwargs):
        result = native_split(**kwargs)
        native_sources.append(dict((result or {}).get("overhead_source_mol", {})))
        return result

    sim._apply_native_fe_saturation_split = observe_native_split
    try:
        for hour in range(1, 25):
            buffer_before = sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get("O2", 0.0)
            snapshot = sim.step()
            assert sim.melt.hour == hour
            assert len(native_sources) == hour
            reservoir = sim.melt.oxygen_reservoir
            sio_source_sum += snapshot.evap_flux.species_kg_hr.get("SiO", 0.0)
            if campaign == "C2A" and hour == 24:
                assert sio_source_sum > 0.0
            yield hex_tree({
                "hour": sim.melt.hour,
                "temperature_C": sim.melt.temperature_C,
                "campaign": sim.melt.campaign.name,
                "source_kg_hr": {name: snapshot.evap_flux.species_kg_hr.get(name, 0.0)
                                 for name in SPECIES},
                "oxygen_produced_kg": snapshot.oxygen_produced_kg,
                "redox_source_terms": sim._redox_source_breakdown_diagnostic().get("terms_mol_o2_equiv_by_label", {}),
                "bulk_pO2_bar": reservoir.headspace_transport_pO2_bar,
                "ledger_pO2_bar": reservoir.headspace_ledger_pO2_bar,
                "request_pO2_bar": sim._last_vapor_pressure_diagnostic.get("pO2_bar"),
                "intrinsic_log_fO2": reservoir.melt_intrinsic_fO2_log,
                "buffer_delta_mol": sim.atom_ledger.mol_by_account("reservoir.fo2_buffer").get("O2", 0.0) - buffer_before,
                "ferric": sim._last_fe_redox_respeciation_diagnostic,
                "native_source_mol": native_sources[-1],
                "native_fe_vapor_mol": sim._last_native_fe_partition_diagnostic.get("native_fe_vapor_mol", 0.0),
            })
    finally:
        close_test_owned_magemin_backend(sim)


def trajectory_receipt(feed, campaign):
    with closing(_trajectory_rows(feed, campaign)) as rows:
        return list(rows)


@pytest.fixture(scope="module", params=("C2A", "C0"))
def lunar_trajectory(request):
    # The serial group keeps this module fixture on one worker. Checking each
    # hour separately retains the normal 300 s test guard without placing the
    # entire 24-hour integration inside one test's timeout. Cache prefixes so
    # selecting/reordering an hour still returns that actual simulated hour.
    with closing(_trajectory_rows("lunar_mare_low_ti", request.param)) as rows:
        cache = []

        def receipt(hour):
            while len(cache) < hour:
                cache.append(next(rows))
            return cache[hour - 1]

        yield request.param, receipt


@pytest.mark.parametrize("oxygen", (0.0, 1.3e-12, 1e-9, 1e-6))
def test_real_controls_oxygen_receipt(oxygen):
    assert consumer_receipt(oxygen) == json.loads(PINS.read_text())["consumers"][str(oxygen)]


@pytest.mark.parametrize("legacy_sweep,quantized", ((True, False), (False, True)))
@pytest.mark.parametrize("oxygen", (0.0, 1.23456789123e-12, 1.23456789123e-6))
def test_real_controls_alternate_routing_receipt(oxygen, legacy_sweep, quantized):
    key = f"{legacy_sweep}:{quantized}:{oxygen}"
    assert consumer_receipt(oxygen, legacy_sweep=legacy_sweep, quantized=quantized) == json.loads(PINS.read_text())["alternate_consumers"][key]


@pytest.mark.parametrize("hour", range(1, 25))
def test_lunar_24_hour_sources_and_oxygen(lunar_trajectory, hour):
    campaign, receipt = lunar_trajectory
    assert receipt(hour) == json.loads(PINS.read_text())["trajectories"][campaign][hour - 1]
