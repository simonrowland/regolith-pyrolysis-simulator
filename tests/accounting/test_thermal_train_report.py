from __future__ import annotations

import dataclasses
import json
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import yaml
import pytest

from simulator.accounting.ledger import AtomLedger
from simulator.accounting.queries import AccountingQueries
from simulator.backends import BackendSelectionPolicy
from simulator.session import SimSession, SimSessionConfig
from simulator.state import BatchRecord, EvaporationFlux, HourSnapshot, OverheadGas
from simulator.thermal_train import (
    HOT_RADIATOR_SPLIT_K,
    mass_rate_kg_hr_to_molar_rate_mol_s,
    report_from_recorded_series,
    thermal_train_parameters_from_mapping,
    thermal_train_section_ownership,
    vapor_cp_j_per_mol_k,
)


def _setpoints() -> dict:
    return yaml.safe_load(Path("data/setpoints.yaml").read_text(encoding="utf-8"))


def _na_boundary_report(monkeypatch, crossing_K: float, inlet_K: float) -> dict:
    import simulator.thermal_train as thermal_train

    monkeypatch.setattr(
        thermal_train,
        "condensation_stage_windows_K",
        lambda _setpoints: {"Na": (1.0, 3000.0)},
    )
    monkeypatch.setattr(
        thermal_train,
        "authoritative_condensation_temperature",
        lambda _species, *, setpoints: {
            "temperature_C": crossing_K - 273.15,
            "source": "boundary-test-fixture",
            "authority": "stage_window",
        },
    )
    return report_from_recorded_series(
        [{"Na": 1.0}], [0.0], [inlet_K], setpoints=_setpoints()
    )


def _na_accounted_thermal_load_W(report: dict) -> float:
    section_load = sum(
        report["sections"][name]["sensible_load_W"]
        + report["sections"][name]["latent_load_W"]
        for name in ("hot_radiator", "mid_radiator")
    )
    excluded_load = report["excluded_species"].get("Na", {}).get("heat_load_W") or 0.0
    return section_load + excluded_load


def _sim(record: BatchRecord) -> SimpleNamespace:
    return SimpleNamespace(atom_ledger=AtomLedger(), record=record, setpoints=_setpoints())


def _snapshot(hour: int, *, na_kg_hr: float, unknown_kg_hr: float, o2_mol_hr: float) -> HourSnapshot:
    return HourSnapshot(
        hour=hour,
        temperature_C=1500.0,
        evap_flux=EvaporationFlux(
            species_kg_hr={"Na": na_kg_hr, "Mystery": unknown_kg_hr},
            total_kg_hr=na_kg_hr + unknown_kg_hr,
        ),
        overhead=OverheadGas(transport_saturation_pct=12.0 + hour),
        melt_offgas_O2_mol_hr=o2_mol_hr,
        O2_vented_kg_hr=0.25 * hour,
    )


def test_thermal_train_report_uses_hot_and_separate_melt_offgas_series_fail_closed() -> None:
    record = BatchRecord(snapshots=[
        _snapshot(1, na_kg_hr=1.0, unknown_kg_hr=0.5, o2_mol_hr=100.0),
        _snapshot(2, na_kg_hr=2.0, unknown_kg_hr=0.25, o2_mol_hr=250.0),
    ])
    report = AccountingQueries(_sim(record)).thermal_train_report()
    assert report["peaks"]["hot_species_kg_hr"] == {"Mystery": 0.5, "Na": 2.0}
    assert report["peaks"]["cold_o2_mol_hr"] == 250.0
    assert report["peaks"]["cold_o2_kg_hr"] == 250.0 * 0.031998
    assert report["excluded_species_nonzero"] is True
    assert "Mystery" in report["excluded_species"]
    assert report["train_closes_for_run"] is False
    assert report["closure_status"] == "excluded_major_heat_carrier_report_incomplete"
    assert report["observed_upstream_state"]["O2_vented_peak_kg_hr"] == 0.5
    assert report["peaks"]["hot_total_vapor_kg_hr"] == pytest.approx(2.0)
    assert report["peaks"]["hot_total_vapor_basis"] == "maximum_concurrent_snapshot_total"
    assert report["peaks"]["hot_total_vapor_projection_status"] == (
        "partial_unconverted_species_excluded"
    )
    json.dumps(report, allow_nan=False, sort_keys=True)


def test_unsourced_refractory_enthalpy_has_distinct_expected_trace_status() -> None:
    report = report_from_recorded_series(
        [{"SiO": 0.001}],
        [0.0],
        [1773.15],
        setpoints=_setpoints(),
        expected_refractory_trace_species=("SiO",),
    )
    assert report["train_closes_for_run"] is False
    assert report["closure_status"] == "excluded_refractory_trace_expected"
    assert report["excluded_refractory_trace_species"] == ["SiO"]
    assert report["excluded_major_heat_carrier_species"] == []


def test_unsourced_large_sio_is_major_without_explicit_trace_authority() -> None:
    report = report_from_recorded_series(
        [{"SiO": 100.0}], [0.0], [1773.15], setpoints=_setpoints()
    )
    assert report["closure_status"] == "excluded_major_heat_carrier_report_incomplete"
    assert report["excluded_refractory_trace_species"] == []
    assert report["excluded_major_heat_carrier_species"] == ["SiO"]


def test_na_coflows_through_hot_and_mid_sections_until_its_crossing() -> None:
    report = report_from_recorded_series(
        [{"Na": 1.0}],
        [0.0],
        [1773.15],
        setpoints=_setpoints(),
    )
    rate = 1.0 / (3600.0 * 0.02298976928)
    crossing = 480.0 + 273.15
    cp = 2.5 * 8.31446261815324
    assert report["sections"]["hot_radiator"]["sensible_load_W"] == pytest.approx(
        rate * cp * (1773.15 - HOT_RADIATOR_SPLIT_K), rel=1e-10
    )
    assert report["sections"]["mid_radiator"]["sensible_load_W"] == pytest.approx(
        rate * cp * (HOT_RADIATOR_SPLIT_K - crossing), rel=1e-10
    )
    assert report["sections"]["hot_radiator"]["latent_load_W"] == 0.0
    assert report["sections"]["mid_radiator"]["latent_load_W"] > 1100.0
    assert report["sections"]["hot_radiator"]["sizing_basis"] == (
        "per_species_maxima_conservative"
    )
    assert report["sections"]["mid_radiator"]["sizing_basis"] == (
        "per_species_maxima_conservative"
    )
    assert any("conservatively sum per-species" in note for note in report["footnotes"])


def test_post_separator_o2_inlet_is_fixed_at_s_b_datum() -> None:
    report = report_from_recorded_series(
        [{}], [100.0], [1800.0], setpoints=_setpoints()
    )
    section = report["sections"]["o2_passive_radiator_night"]
    assert section["inlet_temperature_K"] == HOT_RADIATOR_SPLIT_K
    assert section["inlet_basis"] == "post_separator_S_B"
    a, b, c, d, e = (30.03235, 8.772972, -3.988133, 0.788313, -0.741599)
    expected_enthalpy = 0.0
    for lower in range(1000, 1800, 50):
        t = (lower + 25.0) / 1000.0
        expected_enthalpy += (a + b * t + c * t**2 + d * t**3 + e / t**2) * 50.0
    assert report["sections"]["hot_radiator"]["sensible_load_W"] == pytest.approx(
        (100.0 / 3600.0) * expected_enthalpy,
        rel=1e-12,
    )
    assert section["sensible_load_W"] > 0.0


def test_dew_diagnostic_cannot_change_authoritative_routing(monkeypatch) -> None:
    import simulator.thermal_train as thermal_train

    baseline = report_from_recorded_series(
        [{"Na": 1.0}], [0.0], [1773.15], setpoints=_setpoints()
    )
    monkeypatch.setattr(
        thermal_train,
        "antoine_dew_temperature_diagnostic",
        lambda *_args, **_kwargs: {
            "status": "diagnostic_only",
            "temperature_K": 1999.0,
            "routing_authority": False,
        },
    )
    changed = report_from_recorded_series(
        [{"Na": 1.0}], [0.0], [1773.15], setpoints=_setpoints()
    )
    assert changed["condensation_crossings"]["Na"]["temperature_K"] == baseline[
        "condensation_crossings"
    ]["Na"]["temperature_K"]
    assert changed["sections"]["mid_radiator"]["latent_crossings"] == baseline[
        "sections"
    ]["mid_radiator"]["latent_crossings"]
    assert changed["condensation_crossings"]["Na"]["antoine_dew_diagnostic"] == {
        "status": "diagnostic_only",
        "temperature_K": 1999.0,
        "routing_authority": False,
    }


def test_low_inlet_does_not_invent_split_to_crossing_sensible_duty() -> None:
    crossing = 480.0 + 273.15
    report = report_from_recorded_series(
        [{"Na": 1.0}], [0.0], [crossing - 10.0], setpoints=_setpoints()
    )
    assert report["sections"]["hot_radiator"]["sensible_load_W"] == 0.0
    assert report["sections"]["mid_radiator"]["sensible_load_W"] == 0.0


def test_inlet_exactly_at_crossing_keeps_latent_and_is_not_excluded() -> None:
    crossing = 480.0 + 273.15
    report = report_from_recorded_series(
        [{"Na": 1.0}], [0.0], [crossing], setpoints=_setpoints()
    )
    assert "Na" not in report["excluded_species"]
    assert "Na" in report["sections"]["mid_radiator"]["latent_crossings"]
    assert report["sections"]["mid_radiator"]["latent_load_W"] > 0.0
    assert report["sections"]["hot_radiator"]["sensible_load_W"] == 0.0
    assert report["sections"]["mid_radiator"]["sensible_load_W"] == 0.0


def test_crossing_at_split_has_hot_latent_owner_and_no_mid_sensible(monkeypatch) -> None:
    report = _na_boundary_report(
        monkeypatch,
        crossing_K=HOT_RADIATOR_SPLIT_K,
        inlet_K=HOT_RADIATOR_SPLIT_K + 10.0,
    )
    assert "Na" in report["sections"]["hot_radiator"]["latent_crossings"]
    assert "Na" not in report["sections"]["mid_radiator"]["latent_crossings"]
    assert report["sections"]["mid_radiator"]["sensible_load_W"] == 0.0


def test_crossing_at_floor_has_mid_latent_owner(monkeypatch) -> None:
    floor = thermal_train_parameters_from_mapping().T_floor_K
    report = _na_boundary_report(
        monkeypatch,
        crossing_K=floor,
        inlet_K=HOT_RADIATOR_SPLIT_K + 10.0,
    )
    assert "Na" not in report["sections"]["hot_radiator"]["latent_crossings"]
    assert "Na" in report["sections"]["mid_radiator"]["latent_crossings"]
    assert report["sections"]["mid_radiator"]["sensible_load_W"] > 0.0


def test_degenerate_split_inlet_keeps_hot_latent_panel(monkeypatch) -> None:
    report = _na_boundary_report(
        monkeypatch,
        crossing_K=HOT_RADIATOR_SPLIT_K,
        inlet_K=HOT_RADIATOR_SPLIT_K,
    )
    hot = report["sections"]["hot_radiator"]
    assert "Na" not in report["excluded_species"]
    assert "Na" in hot["latent_crossings"]
    assert hot["latent_load_W"] > 0.0
    assert hot["sensible_load_W"] == 0.0
    assert report["sections"]["mid_radiator"]["sensible_load_W"] == 0.0


@pytest.mark.parametrize(
    ("boundary_case", "inlet_K", "reason"),
    (
        (
            "below_floor",
            HOT_RADIATOR_SPLIT_K + 1.0,
            "authoritative_condensation_temperature_below_train_floor",
        ),
        (
            "above_inlet",
            HOT_RADIATOR_SPLIT_K,
            "authoritative_condensation_temperature_above_train_inlet",
        ),
    ),
)
def test_out_of_path_crossings_have_distinct_typed_exclusions(
    monkeypatch,
    boundary_case: str,
    inlet_K: float,
    reason: str,
) -> None:
    floor = thermal_train_parameters_from_mapping().T_floor_K
    crossing = floor - 1.0 if boundary_case == "below_floor" else inlet_K + 1.0
    report = _na_boundary_report(monkeypatch, crossing, inlet_K)
    assert report["excluded_species"]["Na"]["reason"] == reason
    assert report["excluded_species"]["Na"]["heat_load_status"] == (
        "latent_outside_train_temperature_path"
    )


def test_section_boundary_grid_has_total_latent_ownership_and_continuous_load(
    monkeypatch,
) -> None:
    floor = thermal_train_parameters_from_mapping().T_floor_K
    split = HOT_RADIATOR_SPLIT_K
    temperature_epsilon_K = 1e-3
    grid = (
        floor - temperature_epsilon_K,
        floor,
        floor + temperature_epsilon_K,
        split - temperature_epsilon_K,
        split,
        split + temperature_epsilon_K,
    )

    for crossing in grid:
        for inlet in grid:
            assignment = thermal_train_section_ownership(crossing, inlet, floor)
            owner_count = sum(
                assignment["latent_owner"] == owner for owner in ("hot", "mid")
            )
            exclusion_count = assignment["exclusion_reason"] is not None
            assert owner_count + exclusion_count == 1
            if floor <= crossing <= inlet:
                assert owner_count == 1
            elif crossing > inlet:
                assert assignment["exclusion_reason"] == (
                    "authoritative_condensation_temperature_above_train_inlet"
                )
            else:
                assert assignment["exclusion_reason"] == (
                    "authoritative_condensation_temperature_below_train_floor"
                )

        for boundary in (floor, split, crossing):
            below = _na_accounted_thermal_load_W(
                _na_boundary_report(monkeypatch, crossing, boundary - temperature_epsilon_K)
            )
            above = _na_accounted_thermal_load_W(
                _na_boundary_report(monkeypatch, crossing, boundary + temperature_epsilon_K)
            )
            rate = mass_rate_kg_hr_to_molar_rate_mol_s("Na", 1.0)
            load_discretization_epsilon_W = (
                rate
                * vapor_cp_j_per_mol_k("Na", boundary)
                * 2.0
                * temperature_epsilon_K
                * 1.01
            )
            assert abs(above - below) <= load_discretization_epsilon_W


def test_antoine_dew_column_reports_local_partial_pressure_and_provenance() -> None:
    report = report_from_recorded_series(
        [{"Mg": 1.0}],
        [0.0],
        [1773.15],
        setpoints=_setpoints(),
        overhead_state_series=[{
            "pressure_Pa": 100.0,
            "temperature_K": 1200.0,
            "composition_mbar": {"Mg": 1.0},
            "throat_diameter_m": 0.05,
        }],
    )
    row = report["condensation_crossings"]["Mg"]
    assert row["temperature_K"] == pytest.approx(580.0 + 273.15)
    diagnostic = row["antoine_dew_diagnostic"]
    assert diagnostic["status"] == "diagnostic_only"
    assert diagnostic["partial_pressure_Pa"] == 100.0
    assert diagnostic["temperature_K"] == pytest.approx(859.5303555)
    assert diagnostic["provenance"] == "existing_condensation_antoine_surface"
    assert diagnostic["routing_authority"] is False


def test_false_deposition_gate_captures_no_frost() -> None:
    base = thermal_train_parameters_from_mapping()
    params = replace(base, P_suction_Pa=1.0, P_discharge_Pa=100.0)
    report = report_from_recorded_series(
        [{}], [100.0], [1000.0], setpoints=_setpoints(), parameters=params
    )
    assert report["capacity"]["deposition_gate"]["frost_forms"] is False
    assert report["capacity"]["captured_batch_kg"] == 0.0
    assert report["capacity"]["capture_status"] == {
        "status": "refused",
        "reason": "deposition_gate_not_met",
    }
    assert report["sections"]["cavern_regeneration"]["total_J"] == 0.0


def test_mre_anode_o2_is_an_antagonist_not_a_cold_train_input() -> None:
    base = _snapshot(1, na_kg_hr=0.0, unknown_kg_hr=0.0, o2_mol_hr=10.0)
    antagonist = deepcopy(base)
    antagonist.overhead.mre_anode_O2_mol_hr = 1e9
    first = AccountingQueries(_sim(BatchRecord(snapshots=[base]))).thermal_train_report()
    second = AccountingQueries(_sim(BatchRecord(snapshots=[antagonist]))).thermal_train_report()
    assert first["peaks"]["cold_o2_mol_hr"] == second["peaks"]["cold_o2_mol_hr"]
    assert first["sections"]["cryo_tail"] == second["sections"]["cryo_tail"]


def test_report_computes_recorded_throat_and_duct_knudsen_anchors() -> None:
    report = report_from_recorded_series(
        [{}],
        [10.0],
        [1000.0],
        setpoints=_setpoints(),
        overhead_state_series=[{
            "pressure_Pa": 100.0,
            "temperature_K": 1000.0,
            "composition_mbar": {"O2": 1.0},
            "throat_diameter_m": 0.05,
        }],
    )
    anchors = report["knudsen_anchors"]
    assert anchors["status"] == "computed"
    assert anchors["anchors"]["throat"]["basis"] == "mixture"
    assert anchors["anchors"]["duct"]["basis"] == "mixture"
    assert anchors["anchors"]["cavern"]["status"] == "inputs_required"


def test_knudsen_location_length_and_threshold_knobs_are_consumed() -> None:
    base = thermal_train_parameters_from_mapping()
    state = [{
        "pressure_Pa": 100.0,
        "temperature_K": 1000.0,
        "composition_mbar": {"O2": 1.0},
        "throat_diameter_m": 0.05,
    }]
    baseline = report_from_recorded_series(
        [{}], [0.0], [1000.0], setpoints=_setpoints(), parameters=base,
        overhead_state_series=state,
    )
    locations = {name: dict(values) for name, values in base.knudsen_locations.items()}
    locations["duct"]["L_m"] *= 2.0
    locations["duct"]["Kn_threshold"] = 1e-20
    changed = report_from_recorded_series(
        [{}], [0.0], [1000.0], setpoints=_setpoints(),
        parameters=replace(base, knudsen_locations=locations),
        overhead_state_series=state,
    )
    before = baseline["knudsen_anchors"]["anchors"]["duct"]
    after = changed["knudsen_anchors"]["anchors"]["duct"]
    assert after["Kn"] == pytest.approx(before["Kn"] / 2.0)
    assert before["threshold_interpretation"] == "minimum_Kn_for_rarefaction"
    assert before["rarefaction_threshold_met"] is False
    assert after["rarefaction_threshold_met"] is True


def test_thermal_train_report_is_detached() -> None:
    record = BatchRecord(snapshots=[_snapshot(1, na_kg_hr=1.0, unknown_kg_hr=0.0, o2_mol_hr=10.0)])
    sim = _sim(record)
    report = AccountingQueries(sim).thermal_train_report()
    report["peaks"]["hot_species_kg_hr"]["Na"] = 999.0
    assert sim.record.snapshots[0].evap_flux.species_kg_hr["Na"] == 1.0


def test_report_call_is_run_record_byte_neutral() -> None:
    base = BatchRecord(snapshots=[_snapshot(1, na_kg_hr=1.0, unknown_kg_hr=0.0, o2_mol_hr=10.0)])
    never_called = _sim(deepcopy(base))
    called = _sim(deepcopy(base))
    before = json.dumps(dataclasses.asdict(never_called.record), sort_keys=True, default=str).encode()
    AccountingQueries(called).thermal_train_report()
    after = json.dumps(dataclasses.asdict(called.record), sort_keys=True, default=str).encode()
    assert after == before


def test_real_deterministic_runs_are_record_and_ledger_byte_neutral() -> None:
    def canonical_result_document(active: SimSession) -> dict:
        return {
            "record": dataclasses.asdict(active.simulator.record),
            "ledger": active.simulator.atom_ledger.close_report(),
            "objectives": None,
            "eval_spec": None,
        }

    def execute() -> SimSession:
        setpoints = _setpoints()
        data = Path("data")
        load = lambda name: yaml.safe_load((data / name).read_text(encoding="utf-8"))
        session = SimSession().start(SimSessionConfig(
            feedstock_id="lunar_mare_low_ti",
            feedstocks=load("feedstocks.yaml"),
            setpoints=setpoints,
            vapor_pressures=load("vapor_pressures.yaml"),
            campaign="C0",
            backend_name="stub",
            backend_policy=BackendSelectionPolicy.RUNNER_STRICT,
            hours=1,
            mass_kg=1000.0,
            additives_kg={},
            track="pyrolysis",
            c5_enabled=False,
            result_document_factory=canonical_result_document,
        ))
        session.advance()
        return session

    never_called = execute()
    called = execute()
    result_document = lambda session: json.dumps(
        session.result_document(), sort_keys=True, default=str
    ).encode()
    result_before = result_document(called)
    optimize_modules_before = {
        name for name in sys.modules if name == "simulator.optimize" or name.startswith("simulator.optimize.")
    }
    AccountingQueries(called.simulator).thermal_train_report()
    optimize_modules_after = {
        name for name in sys.modules if name == "simulator.optimize" or name.startswith("simulator.optimize.")
    }
    record_bytes = lambda session: json.dumps(
        dataclasses.asdict(session.simulator.record), sort_keys=True, default=str
    ).encode()
    ledger_bytes = lambda session: json.dumps(
        session.simulator.atom_ledger.close_report(), sort_keys=True, default=str
    ).encode()
    assert record_bytes(called) == record_bytes(never_called)
    assert ledger_bytes(called) == ledger_bytes(never_called)
    assert result_document(called) == result_before == result_document(never_called)
    assert optimize_modules_after == optimize_modules_before
    assert called.result_document()["objectives"] is None
    assert called.result_document()["eval_spec"] is None


def test_report_without_history_is_typed_no_data() -> None:
    report = AccountingQueries(_sim(BatchRecord())).thermal_train_report()
    assert report == {
        "schema_version": "thermal-train-report-v1",
        "status": "no_data",
        "reason": "no_run_history",
        "train_closes_for_run": False,
        "snapshot_count": 0,
        "excluded_species": {},
        "excluded_species_nonzero": False,
    }


def test_declared_cold_capacity_drives_separate_overflow_diagnostic() -> None:
    report = report_from_recorded_series(
        [{}],
        [1000.0],
        [1773.15],
        setpoints=_setpoints(),
        rated_cold_train_kg_hr=10.0,
    )
    assert report["peaks"]["cold_o2_kg_hr"] == pytest.approx(31.998)
    assert report["capacity"]["thermal_train_overflow_kg_hr"] == pytest.approx(21.998)
    assert report["capacity"]["basis"] == "declared_rated_capacity"
    assert report["capacity"]["deposition_gate"]["criterion"] == "p_O2 > P_sub(T_wall)"
