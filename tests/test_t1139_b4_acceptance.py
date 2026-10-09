"""Full-run conservation and uncaptured trace condensable acceptance."""

import math

import pytest

from simulator.accounting.ledger import DEFAULT_BALANCE_ABSOLUTE_FLOOR_KG
from simulator.physical_constants import CATALOG_PHYSICAL_PRESSURE_CEILING_PA
from tests.test_t1139_b4_live_pins import SCENARIOS_B4, capture_run


@pytest.mark.parametrize("scenario", SCENARIOS_B4[:6], ids=lambda row: row["name"])
def test_live_full_run_closes_each_element_and_reports_condensables(scenario, monkeypatch):
    capture, payload, sim = capture_run(scenario, monkeypatch)
    assert payload["status"] == "ok", payload.get("error_message")
    assert len(sim.record.snapshots) == scenario["hours"]
    assert math.fsum(s.duration_h for s in sim.record.snapshots) == scenario["hours"]
    assert all(abs(s.mass_balance_error_pct) < 5e-12 for s in sim.record.snapshots)
    channels = sim._last_vapour_batch_report["channels_by_species"]
    trace_values = [row["pressure"]["pa"] for row in channels.values()
                    if row["extra"].get("request_rule") == "trace_source_inventory"
                    and row["pressure"]["kind"] == "value"]
    assert trace_values
    assert all(math.isfinite(value) and 0 <= value <= CATALOG_PHYSICAL_PRESSURE_CEILING_PA
               for value in trace_values)

    drift = sim.atom_ledger.element_atom_drift_report()
    assert drift["unit"] == "mol-atoms"
    assert drift["sign_convention"] == "final_minus_input"
    for surface in ("accepted_transition_residual_mol_atoms",
                    "whole_run_boundary_residual_mol_atoms"):
        assert "O" in drift[surface]
        assert all(math.isfinite(value) and abs(value) < 1e-9
                   for value in drift[surface].values()), drift[surface]
    assert all(value >= -DEFAULT_BALANCE_ABSOLUTE_FLOOR_KG
               for account in capture["ledger_kg"].values()
               for value in account.values())

    for species in ("Rb", "Cs"):
        emitted = math.fsum(snapshot.evap_flux.species_kg_hr.get(species, 0)
                            * snapshot.duration_h for snapshot in sim.record.snapshots)
        assert emitted > 0
        assert capture["ledger_kg"]["terminal.offgas"][species] == pytest.approx(emitted)
        flagged_mass = 0.0
        for hour, snapshot in zip(payload["per_hour_summary"], sim.record.snapshots):
            row = hour.get("condensation_refusals_by_species", {}).get(species, {})
            if row.get("reason") == "flagged_uncaptured_condensable":
                assert row["mass_disposition"] == "flagged_uncaptured_condensable"
                assert row["remaining_mass_kg_hr"] == row["input_mass_kg_hr"]
                flagged_mass += row["input_mass_kg_hr"] * snapshot.duration_h
        assert 0 < flagged_mass <= emitted
