"""C7 source and consumer regressions against the reviewed chunk-2 receipts.

These are refactor pins, not experimental validation. The independent oxygen
balance and d-259 tests supply the physical and guard checks.
"""
from copy import deepcopy
from contextlib import closing
import json
import math
from pathlib import Path

import pytest

from simulator.config import load_config_bundle
from simulator.accounting.formulas import resolve_species_formula
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import CampaignPhase
from tests.magemin_test_support import close_test_owned_magemin_backend
from tests.test_c7_oxygen_pins import hex_tree
from tests.chemistry.test_vaporock_authority_promotion import _force_vaporock_available


# Reuse the stateful redox suite's serial engine lifecycle contract.
pytestmark = [pytest.mark.serial, pytest.mark.xdist_group("serial")]
PINS = Path(__file__).parent / "fixtures/c7_closure_routing.json"
SURFACE_PINS = Path(__file__).parent / "fixtures/c7_surface_routing.json"
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
            material_origin="feedstock", source="C7 routing initial gas",
        )
        raw = (sim._headspace_ledger_pO2_bar_from_o2_mol(o2_mol)
               if legacy_sweep else sim._physical_sweep_boundary()["partials_bar"].get("O2", 0.0))
        assert raw == pytest.approx(oxygen, rel=1e-12, abs=0.0)
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        sim._reset_redox_source_diagnostics_for_hour()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        boundary = sim._solve_evaporative_oxygen_boundary()
        sim._reaction_pO2_bar = boundary.reaction_pO2_bar
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
            "closure": {"reaction": boundary.reaction_pO2_bar,
                        "gas_face": boundary.gas_face_pO2_bar,
                        "bulk": boundary.bulk_pO2_bar,
                        "relative_residual": abs(boundary.residual) / math.fsum(abs(term) for term in boundary.balance_terms)},
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
                "closure": sim._last_surface_oxygen_diagnostic,
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
    receipt = consumer_receipt(oxygen)
    assert receipt['raw'] == json.loads(PINS.read_text())["consumers"][str(oxygen)]['raw']
    assert_consumer_values(receipt, json.loads(SURFACE_PINS.read_text())["consumers"][str(oxygen)])
    assert_solved_controls(receipt)


def assert_consumer_values(receipt, expected):
    for field in ('raw', 'intrinsic'):
        assert receipt[field] == expected[field], field
    # Reviewed roots used log10(p/bar) Brent tolerances of 1e-12. For
    # P proportional to 10**(e*z), dP/P = ln(10)*e*dz; the unitless
    # 1e-10 pressure budget covers that root precision in these cases.
    # dz -> 0 recovers identity. Keep all independent controls exact.
    for actual, old in zip(receipt['legacy_pressures_Pa'], expected['legacy_pressures_Pa'], strict=True):
        assert actual.keys() == old.keys()
        assert {s: float.fromhex(v) for s, v in actual.items()} == pytest.approx(
            {s: float.fromhex(v) for s, v in old.items()}, rel=1e-10, abs=0.0)
    audit = deepcopy(receipt['vaporock_transport_audit'])
    old_audit = expected['vaporock_transport_audit']
    for actual, old in zip(audit, old_audit, strict=True):
        for plane, fields in (('requested', ('transport_pO2_bar',)),
                              ('applied', ('transport_pO2_bar', 'transport_fO2_log', 'fO2_log'))):
            for field in fields:
                is_log = field.endswith('_log')
                assert float.fromhex(actual[plane][field]) == pytest.approx(
                    float.fromhex(old[plane][field]),
                    rel=0.0 if is_log else 1e-10, abs=1e-10 if is_log else 0.0)
                actual[plane][field] = old[plane][field]
    assert audit == old_audit


def assert_solved_controls(receipt):
    oxygen = float.fromhex(receipt['closure']['reaction'])
    assert oxygen > 0.0
    assert float.fromhex(receipt['closure']['relative_residual']) < math.sqrt(math.ulp(1.0))
    assert receipt['dispatch'] == receipt['flux_oxygen'] == receipt['closure']['reaction']
    for request in receipt['requests']:
        assert request['controls']['pO2_bar'] == request['resolved'] == receipt['dispatch']
    # PT0 may canonicalize other inputs; it must not round solved oxygen.
    assert receipt['requests'][0]['controls']['pO2_admission'] == 'surface_closure'
    for audit in receipt['vaporock_transport_audit']:
        assert audit['requested']['transport_pO2_bar'] == audit['applied']['transport_pO2_bar'] == receipt['dispatch']
        assert float.fromhex(audit['applied']['transport_fO2_log']) == math.log10(oxygen)
        assert audit['applied']['intrinsic_fO2_log'] == receipt['intrinsic']


def test_fixed_state_source_pressure_exponents_at_solved_oxygen():
    sim = simulation()
    try:
        sim.melt.temperature_C = 1600.0
        sim._reset_redox_source_diagnostics_for_hour()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
        boundary = sim._solve_evaporative_oxygen_boundary()
        cases = []
        for oxygen in (1e-9, boundary.reaction_pO2_bar):
            trial = sim._detached_evaporation_projection()
            trial._reaction_pO2_bar = oxygen
            trial._frozen_evaporation_carriers = boundary.payload[2]._frozen_evaporation_carriers
            result = trial._get_equilibrium()
            diagnostic = trial._last_vapor_pressure_diagnostic
            assert diagnostic['source_reaction_fO2_bar'] == 10.0 ** sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
            cases.append((result.vapor_pressures_Pa, diagnostic['activities']))
        assert cases[0][1] == cases[1][1]
        # SiO's oxygen enters the gas, so its declared half-O2 power
        # follows the surface. Metal coproduct remains in the melt buffer;
        # fixed intrinsic potential gives identical metal pressures even
        # when the gas surface changes. Depletion is excluded here.
        ratio = boundary.reaction_pO2_bar / 1e-9
        assert ratio != 1.0
        for species, exponent in (('SiO', -.5), ('Fe', 0.0), ('Mg', 0.0),
                                  ('Na', 0.0), ('K', 0.0)):
            assert cases[1][0][species] / cases[0][0][species] == pytest.approx(
                ratio ** exponent, rel=1e-12, abs=0.0), species
    finally:
        close_test_owned_magemin_backend(sim)


@pytest.mark.parametrize("legacy_sweep,quantized", ((True, False), (False, True)))
@pytest.mark.parametrize("oxygen", (0.0, 1.23456789123e-12, 1.23456789123e-6))
def test_real_controls_alternate_routing_receipt(oxygen, legacy_sweep, quantized):
    key = f"{legacy_sweep}:{quantized}:{oxygen}"
    receipt = consumer_receipt(oxygen, legacy_sweep=legacy_sweep, quantized=quantized)
    assert receipt['raw'] == json.loads(PINS.read_text())["alternate_consumers"][key]['raw']
    assert_consumer_values(receipt, json.loads(SURFACE_PINS.read_text())["alternate_consumers"][key])
    assert_solved_controls(receipt)


@pytest.mark.parametrize("hour", range(1, 25))
def test_lunar_24_hour_sources_and_oxygen(lunar_trajectory, hour):
    campaign, receipt = lunar_trajectory
    row = receipt(hour)
    assert row['hour'] == hour
    expected = json.loads(SURFACE_PINS.read_text())['reviewed_hourly_sources'][campaign]['hours'][hour - 1]
    # The reviewed one-hour evolved receipt includes the native Fe parcel.
    # Add native mol * kg/mol once to the ordinary kg/h * 1 h source;
    # all other species already match the reviewed interval quantity.
    mass = resolve_species_formula('Fe').molar_mass_kg_per_mol()
    evolved = {s: float.fromhex(row['source_kg_hr'][s]) + (
        float.fromhex(row['native_fe_vapor_mol']) * mass if s == 'Fe' else 0.0)
        for s in SPECIES}
    # The reviewed trajectory used the same sqrt(epsilon) signed-flux
    # acceptance. Compare its source values at that numerical resolution,
    # retaining exact zeros (abs=0); the new delta roots close more tightly.
    assert evolved == pytest.approx({s: float.fromhex(expected['evolved_kg'][s])
                                    for s in SPECIES}, rel=math.sqrt(math.ulp(1.0)), abs=0.0)
    assert row['native_fe_vapor_mol'] == expected['native_fe_vapor_mol']
    assert float.fromhex(row['intrinsic_log_fO2']) == pytest.approx(
        float.fromhex(expected['intrinsic_log_fO2']), rel=0.0, abs=1e-12)
    for rate in row['source_kg_hr'].values():
        assert math.isfinite(float.fromhex(rate)) and float.fromhex(rate) >= 0.0
    closure = row['closure']
    assert float.fromhex(closure['relative_residual']) <= math.sqrt(math.ulp(1.0))
    reaction, face, bulk = (float.fromhex(closure[key]) for key in
                           ('reaction_pO2_bar', 'gas_face_pO2_bar', 'bulk_pO2_bar'))
    assert min(reaction, face, bulk) >= 0.0
    tolerance = 8 * math.fsum(math.ulp(value) for value in (reaction, face, bulk))
    assert min(reaction, bulk) - tolerance <= face <= max(reaction, bulk) + tolerance
