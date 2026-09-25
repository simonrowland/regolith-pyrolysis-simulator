from __future__ import annotations

import math

import pytest

from simulator import condensation
from simulator.accounting.queries import wall_deposit_candidate_for_surface_kg
from simulator.state import (
    CondensationStage,
    CondensationTrain,
    EvaporationFlux,
    MeltState,
    PipeSegment,
)


def _stage3_unavailable_psat_model():
    model = condensation.CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        wall_temperature_C=1500.0, overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={"SiO": 1.0},
        stage_area_m2_by_stage={str(stage.stage_number): 1.0 for stage in model.train.stages},
        pipe_segment_temperatures_C={segment.name: 1500.0 for segment in model.pipe_segments},
    )
    model.wall_species_partial_pressures_pa = {"SiO": 100.0}
    return model


def test_stage3_unavailable_psat_predicts_flagged_capture():
    model = _stage3_unavailable_psat_model()
    outcomes = []
    stage = next(stage for stage in model.train.stages if stage.stage_number == 3)
    eta = model._condensation_efficiency(
        stage=stage, species="SiO", T_cond_C=1050.0, residence_s=1.0,
        available_kg=1.0, alpha_s_value=1.0, efficiency_outcomes=outcomes,
    )
    assert 0.0 < eta <= 1.0
    flag = next(item for item in outcomes if item["status"] == "extrapolated")
    assert flag["authority_level"] == "extrapolated"
    assert flag["reason"] == "antoine_psat_unavailable_at_T"
    assert flag["original_reason"]
    assert flag["valid_range_K"]
    assert flag["eta"] == eta


def test_stage3_unavailable_psat_missing_flow_refuses():
    model = _stage3_unavailable_psat_model()
    model.wall_species_partial_pressures_pa = {}
    stage = next(stage for stage in model.train.stages if stage.stage_number == 3)
    with pytest.raises(condensation.DepositionInputRefusal):
        model._condensation_efficiency(
            stage=stage, species="SiO", T_cond_C=1050.0, residence_s=1.0,
            available_kg=1.0, alpha_s_value=1.0,
        )


@pytest.mark.parametrize("case", ["positive", "zero_flow", "zero_area"])
def test_declared_non_sio_extension_preserves_physical_zeros(case):
    model = _stage3_unavailable_psat_model()
    model.wall_species_partial_pressures_pa = {"Na2": 0.0 if case == "zero_flow" else 100.0}
    stage = next(stage for stage in model.train.stages if stage.stage_number == 4)
    if case == "zero_area":
        model.stage_area_m2_by_stage = {"4": 0.0}
    outcomes = []
    eta = model._condensation_efficiency(
        stage=stage, species="Na2", T_cond_C=1000.0, residence_s=1.0,
        available_kg=1.0, alpha_s_value=1.0, efficiency_outcomes=outcomes,
    )
    if case == "positive":
        assert 0.0 < eta <= 1.0
        assert any(item["authority_level"] == "extrapolated" for item in outcomes)
    else:
        assert eta == 0.0
        assert any(item["status"] == "pass_through" for item in outcomes)


@pytest.mark.parametrize("area", [None, -1.0, float("nan"), float("inf"), "invalid"],
                         ids=["missing", "negative", "nan", "infinite", "nonnumeric"])
@pytest.mark.parametrize("stage_number", [0, 4, 5, 6])
def test_declared_non_sio_extension_invalid_area_refuses(area, stage_number):
    model = _stage3_unavailable_psat_model()
    model.wall_species_partial_pressures_pa = {"Na2": 100.0}
    model.stage_area_m2_by_stage = (
        {} if area is None and stage_number == 4 else {str(stage_number): area}
    )
    stage = next(stage for stage in model.train.stages if stage.stage_number == stage_number)
    outcomes = []
    with pytest.raises(condensation.DepositionInputRefusal) as caught:
        model._condensation_efficiency(
            stage=stage, species="Na2", T_cond_C=1000.0, residence_s=1.0,
            available_kg=1.0, alpha_s_value=1.0, efficiency_outcomes=outcomes,
        )
    assert caught.value.parameter == "stage_area_m2"
    assert caught.value.terminal_refusal is True
    assert not any(item["status"] == "pass_through" for item in outcomes)


@pytest.mark.parametrize("stage_number", [0, 5, 6])
def test_declared_non_sio_extension_noncollector_has_topology_evidence(stage_number):
    model = _stage3_unavailable_psat_model()
    model.wall_species_partial_pressures_pa = {"Na2": 100.0}
    model.stage_area_m2_by_stage = {}
    stage = next(stage for stage in model.train.stages if stage.stage_number == stage_number)
    outcomes = []
    eta = model._condensation_efficiency(
        stage=stage, species="Na2", T_cond_C=1000.0, residence_s=1.0,
        available_kg=1.0, alpha_s_value=1.0, efficiency_outcomes=outcomes,
    )
    assert eta == 0.0
    outcome = next(item for item in outcomes if item["status"] == "pass_through")
    assert outcome["reason"] == "no_capture_surface_in_train_topology"
    assert outcome["applicability_evidence"] == {
        "stage_number": stage_number, "target_species": [], "configured_capture_surface": False,
    }


def test_declared_non_sio_extension_explicit_noncollector_area_captures():
    model = _stage3_unavailable_psat_model()
    model.wall_species_partial_pressures_pa = {"Na2": 100.0}
    model.stage_area_m2_by_stage = {"stage_5": 1.0}
    stage = next(stage for stage in model.train.stages if stage.stage_number == 5)
    eta = model._condensation_efficiency(
        stage=stage, species="Na2", T_cond_C=1000.0, residence_s=1.0,
        available_kg=1.0, alpha_s_value=1.0,
    )
    assert 0.0 < eta <= 1.0


def test_declared_non_sio_extension_invalid_area_aborts_hour(monkeypatch):
    from simulator.runner import PyrolysisRun
    from simulator.run_executor import RunExecutor

    original = condensation.CondensationModel._condensation_efficiency

    def missing_area(self, **kwargs):
        areas = self.stage_area_m2_by_stage
        if kwargs["species"] != "SiO":
            self.stage_area_m2_by_stage = {}
        try:
            return original(self, **kwargs)
        finally:
            self.stage_area_m2_by_stage = areas

    monkeypatch.setattr(condensation.CondensationModel, "_condensation_efficiency", missing_area)
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti", campaign="C0", hours=24,
        additives_kg={}, allow_fallback_vapor=True, allow_unmeasured_alpha_fallback=True,
    )
    execution = RunExecutor().execute(run._session_config())
    assert execution.status == "failed"
    assert isinstance(execution.failure_exception, condensation.DepositionInputRefusal)
    assert execution.failure_exception.parameter == "stage_area_m2"
    assert execution.failure_exception.terminal_refusal is True
    assert execution.session.simulator.melt.hour == len(execution.snapshots)
    assert len(execution.snapshots) < 24


def test_lunar_c0_24h_mass_and_oxygen_closure():
    from simulator.runner import PyrolysisRun
    from simulator.run_executor import RunExecutor
    from simulator.accounting import AccountingQueries

    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti", campaign="C0", hours=24,
        additives_kg={}, allow_fallback_vapor=True, allow_unmeasured_alpha_fallback=True,
    )
    execution = RunExecutor().execute(run._session_config())
    assert execution.status == "ok", execution.error_message
    assert len(execution.snapshots) == 24
    assert max(abs(row.mass_balance_error_pct) for row in execution.snapshots) <= 5e-12
    sim = execution.session.simulator
    report = sim.atom_ledger.close_report()
    oxygen_mol = sum(atoms.get("O", 0.0) for atoms in report["atom_moles_by_account"].values())
    assert oxygen_mol > 0.0
    residual = report["element_atom_drift"]["whole_run_boundary_residual_mol_atoms"].get("O", 0.0)
    assert abs(residual) / oxygen_mol * 100.0 <= 5e-12
    partition = AccountingQueries(sim).oxygen_terminal_partition_kg()
    assert abs(partition["total"] - partition["stored"] - partition["vented"] - partition["captured"]) / max(partition["total"], 1e-300) * 100.0 <= 5e-12


def test_predict_flag_wall_certified_value_retains_bits():
    notices = {}
    pressure = condensation._antoine_psat_pa("Mg", 1000.0, antoine_extrapolations=notices)
    # Exact 1daa41b0 result for the admitted Mg fit, unchanged arithmetic.
    assert pressure.hex() == "0x1.7e0cc19ec4f89p+10"
    assert notices == {}


@pytest.mark.parametrize(("temperature", "pressure_hex", "band"), [
    (500.0, "0x1.8f7e06aa69f40p-17", [701.0, 1361.0]),
    (1500.0, "0x1.17121f336b398p+18", [701.0, 1361.0]),
])
def test_predict_flag_wall_values_keep_source_fit_and_authority(temperature, pressure_hex, band):
    diagnostic = {}
    condensation._wall_deposition_driving_pressure_pa(
        "Mg", 1.0e6, temperature, diagnostic_out=diagnostic,
    )
    assert diagnostic["wall_saturation_pressure_pa"].hex() == pressure_hex
    notice = diagnostic["wall_saturation_pressure_notice"]
    assert notice["authority_level"] == "extrapolated"
    assert notice["valid_range_K"] == band
    assert notice["temperature_K"] == temperature
    assert "metal_vapor_pressure_out_of_source_certified_range" in notice["reason"]
    assert diagnostic["wall_saturation_pressure_refused"] is False


def test_predict_flag_fe_below_band_keeps_antoine_bits_and_records_construction_band():
    expected = 10.0 ** (10.670336 - 17759.430841 / 900.0)
    notices = {}
    pressure = condensation._antoine_psat_pa(
        "Fe", 900.0, antoine_extrapolations=notices,
    )
    assert pressure == expected
    assert notices["Fe"]["valid_range_K"] == [1809.0, 3134.0]
    assert notices["Fe"]["continuation"] == "antoine"
    assert notices["Fe"]["output_status"] == "status_bearing"

    diagnostic = {}
    driving = condensation._wall_deposition_driving_pressure_pa(
        "Fe", 100.0, 900.0, diagnostic_out=diagnostic,
    )
    assert driving == pytest.approx(100.0 - expected, rel=1e-12)
    assert diagnostic["wall_saturation_pressure_notice"]["valid_range_K"] == [
        1809.0, 3134.0,
    ]


def test_predict_flag_fe_boiling_anchor_outside_construction_band_only():
    outside_K = 2862.0 + condensation.CELSIUS_TO_KELVIN_OFFSET
    expected_outside = 10.0 ** (10.670336 - 17759.430841 / outside_K)
    outside_notices = {}
    outside_pressure = condensation._antoine_psat_pa(
        "Fe", outside_K, antoine_extrapolations=outside_notices,
    )
    assert outside_pressure == expected_outside
    assert outside_notices["Fe"]["valid_range_K"] == [1809.0, 3134.0]

    inside_K = 3133.0
    expected_inside = 10.0 ** (10.670336 - 17759.430841 / inside_K)
    inside_notices = {}
    inside_pressure = condensation._antoine_psat_pa(
        "Fe", inside_K, antoine_extrapolations=inside_notices,
    )
    assert inside_pressure.hex() == expected_inside.hex()
    assert inside_notices == {}


def test_predict_flag_cold_na_pole_continues_and_flags_positive_deposition():
    diagnostic = {}
    driving = condensation._wall_deposition_driving_pressure_pa(
        "Na", 100.0, 298.15, diagnostic_out=diagnostic,
    )
    assert diagnostic["wall_saturation_pressure_pa"] == pytest.approx(
        4.637229568365543e-11, rel=1e-9,
    )
    assert driving == pytest.approx(100.0, rel=1e-9)
    assert diagnostic["wall_saturation_pressure_refused"] is False
    notice = diagnostic["wall_saturation_pressure_notice"]
    assert notice["authority_level"] == "extrapolated"
    assert notice["status"] == "extrapolated"
    assert notice["output_status"] == "status_bearing"
    assert notice["continuation"] == "clausius_clapeyron_band_edge"
    assert notice["valid_range_K"] == [924.0, 1118.0]
    assert "T+C <= 0" in notice["original_reason"]

    model = condensation.CondensationModel(
        CondensationTrain.create_default(), wall_temperature_C=25.0,
    )
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Na": 1.0},
        gas_temperature_C=1700.0,
        campaign_name="C0",
    )
    candidate_kg = wall_deposit_candidate_for_surface_kg(
        model,
        species="Na",
        rate_kg_hr=1.0,
        T_cond_C=model.condensation_temperatures_C["Na"],
        melt_temperature_C=1700.0,
        wall_temperature_C=25.0,
        surface_area_m2=1.0,
    )
    assert isinstance(candidate_kg, float)
    assert candidate_kg > 0.0
    candidate_notice = model.last_sticking_alpha_provenance_notice[
        "wall_saturation_pressure_extrapolations_by_species"
    ]["Na"]["default_pipe"]
    assert candidate_notice["continuation"] == "clausius_clapeyron_band_edge"
    assert candidate_notice["original_reason"] == notice["original_reason"]


@pytest.mark.parametrize("invalid_value", [None, float("nan")], ids=["missing_B", "nan_B"])
def test_predict_flag_invalid_wall_input_is_typed_and_terminal(monkeypatch, invalid_value):
    from simulator.runner import PyrolysisRun
    from simulator.run_executor import RunExecutor
    from tests.test_lab_geometry_runtime import dynamic_lab_schedule, dynamic_surface_geometry_fixture

    original = condensation._species_vapor_data

    def missing_coefficient(species, **kwargs):
        import copy
        data = copy.deepcopy(original(species, **kwargs))
        if species == "Mg":
            if invalid_value is None:
                del data["pure_component_antoine"]["B"]
            else:
                data["pure_component_antoine"]["B"] = invalid_value
        return data

    monkeypatch.setattr(condensation, "_species_vapor_data", missing_coefficient)
    with pytest.raises(condensation.DepositionInputRefusal):
        condensation._antoine_psat_pa("Mg", 1000.0)
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti", campaign="C2A", hours=1,
        mass_kg=1000.0, backend_name="internal-analytical",
        setpoints_patch={"lab_geometry": dynamic_surface_geometry_fixture()},
        lab_schedule=dynamic_lab_schedule(), force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True, allow_unmeasured_alpha_fallback=True,
    )
    execution = RunExecutor().execute(run._session_config())
    assert execution.status == "failed"
    assert len(execution.snapshots) == 0
    assert "DepositionInputRefusal" in execution.error_message
    assert "pure_component_antoine.B" in execution.error_message


def test_predict_flag_finite_below_band_wall_input_completes():
    from simulator.runner import PyrolysisRun
    from simulator.run_executor import RunExecutor
    from tests.test_lab_geometry_runtime import dynamic_lab_schedule, dynamic_surface_geometry_fixture

    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti", campaign="C2A", hours=1,
        mass_kg=1000.0, backend_name="internal-analytical",
        setpoints_patch={"lab_geometry": dynamic_surface_geometry_fixture()},
        lab_schedule=dynamic_lab_schedule(), force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True, allow_unmeasured_alpha_fallback=True,
    )
    execution = RunExecutor().execute(run._session_config())
    assert execution.status == "ok", execution.error_message
    assert len(execution.snapshots) == 1
    document = run._build_output(execution)
    authority = document["per_hour_summary"][0]["vapour_batch_summary"]["metadata"]["wall_deposit_sticking_authority"]
    notices = authority["wall_saturation_pressure_extrapolations_by_species"]["Mg"]
    assert any(record["temperature_K"] < record["valid_range_K"][0]
               and record["authority_level"] == "extrapolated"
               and record["reason"] for record in notices.values())


@pytest.mark.parametrize("hours", [2, 24])
def test_predict_flag_rh03_recipe_completes_with_public_flags(hours):
    from simulator.runner import PyrolysisRun
    from simulator.run_executor import RunExecutor
    from tests.test_lab_geometry_runtime import dynamic_lab_schedule, dynamic_surface_geometry_fixture

    schedule = dynamic_lab_schedule()
    schedule["id"] = "RH03-paired-temperature"
    schedule["furnace_ceiling_C"] = 2200.0
    for knot in schedule["melt_temperature_C"]:
        knot["value"] = 2200.0
    if hours > schedule["duration_h"]:
        schedule["duration_h"] = float(hours)
        for profile in (schedule["melt_temperature_C"], schedule["chamber_pressure_mbar"],
                        *schedule["surface_temperature_C"].values()):
            profile.append({**profile[-1], "t_h": float(hours)})
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti", campaign="C2A", hours=hours,
        mass_kg=1000.0, backend_name="internal-analytical",
        setpoints_patch={"lab_geometry": dynamic_surface_geometry_fixture()},
        lab_schedule=schedule, force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True, allow_unmeasured_alpha_fallback=True,
    )
    execution = RunExecutor().execute(run._session_config())
    assert execution.status == "ok", execution.error_message
    assert len(execution.snapshots) == hours
    document = run._build_output(execution)
    for row in document["per_hour_summary"]:
        if row["T_C"] == 2200.0:
            melt_notice = row["vapour_batch_summary"]["metadata"]["extrapolation_notices_by_species"]["SiO"]
            assert melt_notice["authority_level"] == "extrapolated"
            assert melt_notice["temperature_K"] == 2473.15
            assert melt_notice["valid_range_K"] == [1400.0, 2273.15]
        wall = row["vapour_batch_summary"]["metadata"]["wall_deposit_sticking_authority"]
        assert wall["authoritative_for_coating"] is False
        assert wall["wall_saturation_pressure_extrapolations_by_species"]["Mg"]
        na_extrapolations = wall["wall_saturation_pressure_extrapolations_by_species"]["Na"]
        assert any(
            record["continuation"] == "clausius_clapeyron_band_edge"
            and "T+C <= 0" in record["original_reason"]
            for record in na_extrapolations.values()
        )
        refused = wall["wall_saturation_pressure_refusals_by_species"]
        assert "Na" not in refused
        assert "Al2" in refused
        # Al2 has no reactive-product backstop, so reactivity metadata is not
        # applicable.  Its real reversible wall route instead refuses because
        # the available reaction-term source is not a wall saturation curve.
        assert any(
            "no extrapolation available" in record["reason"]
            and record.get("refusal_type") == "WallSaturationPressureRefusal"
            for record in refused["Al2"].values()
        ), [
            (record["reason"], record.get("refusal_type"))
            for record in refused["Al2"].values()
        ]
    assert document["per_hour_summary"][0]["T_C"] == 2200.0
    if hours == 24:
        assert not any(record.get("refusal_type") == "DepositionInputRefusal"
                       for records in refused.values() for record in records.values())
        transport = wall["evaporation_transport_notices_by_species"]
        assert transport["SiO"]["evaporation"]["authority_level"] == "extrapolated"
        assert "Kn < 0.01" in transport["SiO"]["evaporation"]["model_domain"]
        assert transport["Si"]["evaporation"]["authority_level"] == "unavailable"
        assert transport["Si"]["evaporation"]["refusal_type"] == "EvaporationFluxConfigurationError"
    pareto = document["run_metadata"]["pressure_coating_pareto_diagnostic"]["by_species"]
    assert pareto["Mg"]["authority_level"] == "extrapolated"
    assert pareto["Na"]["authority_level"] == "extrapolated"
    assert pareto["Al2"]["status"] == "unavailable"
    assert pareto["SiO"]["vapour_pressure_extrapolation_notice"]["authority_level"] == "extrapolated"
    if hours == 24:
        assert pareto["SiO"]["evaporation_transport_notices"] == transport["SiO"]
        assert pareto["Si"]["evaporation_transport_notices"] == transport["Si"]


@pytest.mark.parametrize("all_missing", [False, True], ids=["partial", "all"])
def test_missing_runtime_wall_profile_keeps_aggregate_unavailable(monkeypatch, all_missing):
    from types import MethodType, SimpleNamespace
    from simulator.core import PyrolysisSimulator
    from simulator.lab_geometry import parse_lab_geometry
    from simulator.run_executor import RunExecutor
    from simulator.runner import PyrolysisRun
    from tests.test_lab_geometry_runtime import dynamic_lab_schedule, dynamic_surface_geometry_fixture

    model = condensation.CondensationModel(CondensationTrain(stages=[]), wall_temperature_C=25.0)
    geometry = model.configure_lab_geometry(parse_lab_geometry(
        dynamic_surface_geometry_fixture(), allow_temperature_profiles=True,
    ))
    prior = {segment.name: segment.wall_temperature_C for segment in model.pipe_segments}
    model.operating_history = [{}]
    schedule = dynamic_lab_schedule()["surface_temperature_C"]
    del schedule["holder_profile"]
    if all_missing:
        del schedule["condenser_profile"]
    sim = SimpleNamespace(
        lab_geometry=geometry, condensation_model=model,
        _active_surface_temperature_schedule=lambda: schedule,
    )
    sim._resolve_lab_surface_temperatures = MethodType(
        PyrolysisSimulator._resolve_lab_surface_temperatures, sim,
    )
    PyrolysisSimulator._apply_lab_surface_temperatures(sim, sample_time_h=0.0)

    assert model.wall_temperature_C is None
    history = model.operating_history[-1]
    assert history["wall_temperature_C"] is None
    missing = {"holder": "lab_schedule_missing_surface_temperature: holder_profile"}
    if all_missing:
        missing["condenser"] = "lab_schedule_missing_surface_temperature: condenser_profile"
    assert history["wall_temperature_input_refusals"] == missing
    assert history["pipe_segment_temperatures_C"] == ({} if all_missing else {"condenser": 1500.0})
    for segment in model.pipe_segments:
        if segment.name in missing:
            assert segment.wall_temperature_C == prior[segment.name]

    schedule.update(dynamic_lab_schedule()["surface_temperature_C"])
    PyrolysisSimulator._apply_lab_surface_temperatures(sim, sample_time_h=0.0)
    assert model.wall_temperature_C == 25.0
    assert model.wall_temperature_input_refusals == {}
    assert "wall_temperature_input_refusals" not in history

    original = PyrolysisSimulator._resolve_lab_surface_temperatures

    def omit_runtime(self, surface_schedule, *, sample_time_h, unavailable=None):
        if unavailable is not None:
            surface_schedule = {
                key: value for key, value in surface_schedule.items()
                if key != "holder_profile" and not all_missing
            }
        return original(
            self, surface_schedule, sample_time_h=sample_time_h, unavailable=unavailable,
        )

    monkeypatch.setattr(PyrolysisSimulator, "_resolve_lab_surface_temperatures", omit_runtime)
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti", campaign="C2A", hours=1,
        mass_kg=1000.0, backend_name="internal-analytical",
        setpoints_patch={"lab_geometry": dynamic_surface_geometry_fixture()},
        lab_schedule=dynamic_lab_schedule(), force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True, allow_unmeasured_alpha_fallback=True,
    )
    execution = RunExecutor().execute(run._session_config())
    assert execution.status == "failed"
    assert len(execution.snapshots) == 0
    assert isinstance(execution.failure_exception, condensation.DepositionInputRefusal)
    assert execution.failure_exception.terminal_refusal is True
    assert execution.failure_exception.parameter == "T_wall_K"
    assert "holder_profile" in execution.error_message


@pytest.mark.parametrize("missing_knot", [False, True], ids=["complete", "missing_knot"])
def test_runtime_wall_profile_uses_run_time_after_campaign_transition(monkeypatch, missing_knot):
    from simulator.core import PyrolysisSimulator
    from simulator.run_executor import RunExecutor
    from simulator.runner import PyrolysisRun
    from simulator.state import CampaignPhase
    from tests.test_lab_geometry_runtime import dynamic_lab_schedule, dynamic_surface_geometry_fixture

    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti", campaign="C2A", hours=2,
        mass_kg=1000.0, backend_name="internal-analytical",
        setpoints_patch={"lab_geometry": dynamic_surface_geometry_fixture()},
        lab_schedule=dynamic_lab_schedule(), force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True, allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    session.advance()
    sim = session.simulator
    assert sim.melt.hour == 1
    sim.start_campaign(CampaignPhase.C3_K)
    assert sim.melt.campaign_hour == 0
    original = PyrolysisSimulator._resolve_lab_surface_temperatures

    def omit_runtime_knot(self, surface_schedule, *, sample_time_h, unavailable=None):
        if missing_knot and unavailable is not None:
            surface_schedule = {
                **surface_schedule,
                "holder_profile": surface_schedule["holder_profile"][:-1],
            }
        return original(
            self, surface_schedule, sample_time_h=sample_time_h, unavailable=unavailable,
        )

    monkeypatch.setattr(PyrolysisSimulator, "_resolve_lab_surface_temperatures", omit_runtime_knot)
    execution = RunExecutor().execute_session(session, hours=1)
    if missing_knot:
        assert execution.status == "failed"
        assert len(execution.snapshots) == 0
        assert isinstance(execution.failure_exception, condensation.DepositionInputRefusal)
        assert execution.failure_exception.terminal_refusal is True
        assert execution.failure_exception.parameter == "T_wall_K"
        assert "holder_profile" in execution.error_message
        assert "lab_schedule_sample_time_outside_declared_window" in execution.error_message
        assert sim.melt.hour == 1
    else:
        assert execution.status == "ok", execution.error_message
        assert len(execution.snapshots) == 1
        assert sim.melt.hour == 2
        assert sim.condensation_model.operating_history[-1]["pipe_segment_temperatures_C"] == {
            "holder": 1500.0, "condenser": 25.0,
        }


def test_sio_wall_temperature_diagnostic_keeps_missing_value_unavailable(monkeypatch):
    from simulator.runner import PyrolysisRun, build_sio_yield_report

    original = PyrolysisRun._run_session

    def missing_history_temperature(self, session):
        result = original(self, session)
        assert result["status"] == "ok"
        history = session.simulator.condensation_model.operating_history
        assert history
        for entry in history:
            entry["wall_temperature_C"] = None
        return result

    monkeypatch.setattr(PyrolysisRun, "_run_session", missing_history_temperature)
    _, diagnostics = build_sio_yield_report(
        feedstock_id="lunar_mare_low_ti", hours=1,
        include_diagnostics=True, allow_unmeasured_alpha_fallback=True,
    )
    assert diagnostics["wall_deposit_liner_temperature_C"] is None


@pytest.mark.parametrize(("species", "temperature", "band"), [
    ("Na", 417.0, [924.0, 1118.0]),
    ("Mg", 115.0, [701.0, 1361.0]),
])
def test_predict_flag_underflow_continues_from_band_edge(species, temperature, band):
    diagnostic = {}
    condensation._wall_deposition_driving_pressure_pa(species, 100.0, temperature, diagnostic_out=diagnostic)
    assert diagnostic["wall_saturation_pressure_pa"] > 0.0
    assert diagnostic["wall_saturation_pressure_refused"] is False
    notice = diagnostic["wall_saturation_pressure_notice"]
    assert notice["continuation"] == "clausius_clapeyron_band_edge"
    assert notice["valid_range_K"] == band


def test_predict_flag_sio_cold_backstop_records_extrapolation_and_hot_wall_refuses():
    cold_model = condensation.CondensationModel(
        CondensationTrain.create_default(), wall_temperature_C=900.0,
    )
    cold_model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"SiO": 1.0},
        gas_temperature_C=1700.0,
        campaign_name="C0",
    )
    cold_candidate = wall_deposit_candidate_for_surface_kg(
        cold_model,
        species="SiO",
        rate_kg_hr=1.0,
        T_cond_C=1400.0,
        melt_temperature_C=1700.0,
        wall_temperature_C=900.0,
        surface_area_m2=1.0,
    )
    assert isinstance(cold_candidate, float)
    assert cold_candidate > 0.0
    cold_notice = cold_model.last_sticking_alpha_provenance_notice[
        "wall_saturation_pressure_extrapolations_by_species"
    ]["SiO"]["default_pipe"]
    assert cold_notice["output_status"] == "status_bearing"
    assert cold_notice["authority_level"] == "extrapolated"
    assert cold_notice["saturation_pressure_policy"] == "reactive_product_backstop"
    assert cold_notice["wall_saturation_pressure_pa"] == 0.0
    assert cold_notice["original_reason"]

    hot_model = condensation.CondensationModel(
        CondensationTrain.create_default(), wall_temperature_C=1500.0,
    )
    hot_model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"SiO": 1.0},
        gas_temperature_C=1700.0,
        campaign_name="C0",
    )
    hot_candidate = wall_deposit_candidate_for_surface_kg(
        hot_model,
        species="SiO",
        rate_kg_hr=1.0,
        T_cond_C=1050.0,
        melt_temperature_C=1700.0,
        wall_temperature_C=1500.0,
        surface_area_m2=1.0,
    )
    assert hot_candidate["status"] == "unavailable"
    assert hot_model.last_sticking_alpha_provenance_notice[
        "wall_saturation_pressure_refusals_by_species"
    ]["SiO"]["default_pipe"]


def test_condensation_efficiency_stage7_na_band_is_positive():
    model = condensation.CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Na": 1.0},
        gas_temperature_C=1700.0,
        campaign_name="C0",
        stage_area_m2_by_stage={"7": 1.0},
    )
    stage = next(stage for stage in model.train.stages if stage.stage_number == 7)
    outcomes = []
    eta = model._condensation_efficiency(
        stage=stage,
        species="Na",
        T_cond_C=model.condensation_temperatures_C["Na"],
        residence_s=1.0,
        available_kg=1.0,
        alpha_s_value=1.0,
        efficiency_outcomes=outcomes,
    )
    assert eta > 0.0
    assert not any(item["status"] == "refused" for item in outcomes)


def test_condensation_efficiency_uses_hourly_vapour_rate_units(monkeypatch):
    model = condensation.CondensationModel(CondensationTrain.create_default())
    area_m2 = 2.0
    rate_kg_hr = 1.0e-3
    flux_mol_m2_s = 2.0e-6
    residence_s = 3.0
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Na": 1.0},
        gas_temperature_C=1700.0,
        campaign_name="C0",
        stage_area_m2_by_stage={"7": area_m2},
    )
    stage = next(stage for stage in model.train.stages if stage.stage_number == 7)

    monkeypatch.setattr(
        condensation,
        "_series_resistance_deposition_flux_mol_m2_s",
        lambda *args, **kwargs: flux_mol_m2_s,
    )
    molar_mass_kg_mol = (
        condensation._molecular_mass_kg_per_molecule("Na")
        * condensation.AVOGADRO_MOL
    )
    expected_eta = (
        flux_mol_m2_s * area_m2 * 3600.0 * molar_mass_kg_mol
        / rate_kg_hr
    )
    assert 0.0 < expected_eta < 1.0

    eta = model._condensation_efficiency(
        stage=stage,
        species="Na",
        T_cond_C=model.condensation_temperatures_C["Na"],
        residence_s=residence_s,
        available_kg=rate_kg_hr,
        alpha_s_value=1.0,
    )

    assert eta == pytest.approx(expected_eta)


def test_condensation_efficiency_does_not_average_refused_sample_as_zero(monkeypatch):
    model = condensation.CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Na": 1.0},
        gas_temperature_C=1700.0,
        campaign_name="C0",
        stage_area_m2_by_stage={"7": 1.0},
    )
    stage = next(stage for stage in model.train.stages if stage.stage_number == 7)
    calls = 0

    def one_refused_sample(*args, **kwargs):
        nonlocal calls
        calls += 1
        diagnostic = kwargs["diagnostic_out"]
        if calls == 1:
            diagnostic.update({
                "wall_saturation_pressure_refused": True,
                "wall_saturation_pressure_refusal_reason": "sample refused",
                "wall_saturation_pressure_refusal_type": "WallSaturationPressureRefusal",
                "wall_saturation_pressure_notice": {
                    "reason": "sample refused",
                },
            })
            return 0.0
        return 1.0

    monkeypatch.setattr(
        condensation,
        "_series_resistance_deposition_flux_mol_m2_s",
        one_refused_sample,
    )
    available_kg = (
        condensation._molecular_mass_kg_per_molecule("Na")
        * condensation.AVOGADRO_MOL
        * 4000.0
    )
    outcomes = []
    eta = model._condensation_efficiency(
        stage=stage,
        species="Na",
        T_cond_C=model.condensation_temperatures_C["Na"],
        residence_s=1.0,
        available_kg=available_kg,
        alpha_s_value=1.0,
        efficiency_outcomes=outcomes,
    )
    assert eta == pytest.approx(0.9)
    assert calls == condensation.HKL_BAND_SAMPLES
    refused = [item for item in outcomes if item["status"] == "refused"]
    assert len(refused) == 1
    assert refused[0]["authority_level"] == "unavailable"
    assert refused[0]["original_reason"] == "sample refused"


def test_predict_flag_pareto_unavailable_keeps_extrapolation():
    from types import SimpleNamespace
    from simulator.diagnostics import pressure_coating_pareto_diagnostic

    notice = {"authority_level": "extrapolated", "temperature_K": 500.0,
              "valid_range_K": [701.0, 1361.0], "reason": "outside source band"}
    sim = SimpleNamespace(condensation_model=SimpleNamespace(
        last_sticking_alpha_provenance_notice={
            "wall_saturation_pressure_extrapolations_by_species": {"Mg": {"wall": notice}}
        }))
    result = pressure_coating_pareto_diagnostic(sim)
    entry = result["by_species"]["Mg"]
    assert entry["status"] == "unavailable"
    assert entry["wall_saturation_pressure_extrapolations"]["wall"] == notice


def test_predict_flag_wall_history_keeps_both_source_band_misses():
    model = condensation.CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0, species_partial_pressures_mbar={"Mg": 1.0},
        gas_temperature_C=1700.0, campaign_name="C0",
    )
    for temperature in (500.0, 1500.0, 1000.0):
        wall_deposit_candidate_for_surface_kg(
            model, species="Mg", rate_kg_hr=1.0, T_cond_C=1200.0,
            melt_temperature_C=1700.0, wall_temperature_C=temperature - 273.15,
            surface_area_m2=1.0,
        )
    records = model.last_sticking_alpha_provenance_notice[
        "wall_saturation_pressure_extrapolations_by_species"]["Mg"]
    assert {record["temperature_K"] for record in records.values()} == {500.0, 1500.0}
    assert all(record["authority_level"] == "extrapolated" for record in records.values())


def test_scalar_alpha_s_range_extrapolation_is_honest_without_value_change():
    out_context: dict[str, object] = {}
    out_value = condensation.alpha_s("Fe", 1600.0, out_context)
    out_eval = out_context["alpha_s_evaluation"]

    assert out_value.hex() == float(0.02).hex()
    assert out_eval["alpha_s_form"] == "scalar"
    assert out_eval["alpha_s_extrapolated"] is True
    assert out_eval["alpha_s_valid_range_K"] == [1700.0, 1800.0]
    assert out_eval["alpha_s_temperature_range_K"] == [1700.0, 1800.0]
    assert out_eval["alpha_s_temperature_below_valid_range"] is True
    assert "Fe alpha_s scalar coefficient extrapolated" in out_eval[
        "alpha_s_extrapolation_warning"
    ]
    assert "[1700, 1800]" in out_eval["alpha_s_extrapolation_warning"]

    in_context: dict[str, object] = {}
    in_value = condensation.alpha_s("Fe", 1750.0, in_context)
    in_eval = in_context["alpha_s_evaluation"]

    assert in_value.hex() == float(0.02).hex()
    assert in_eval["alpha_s_extrapolated"] is False
    assert in_eval["alpha_s_valid_range_K"] == [1700.0, 1800.0]
    assert "alpha_s_extrapolation_warning" not in in_eval


def test_scalar_alpha_s_range_travels_through_stage_provenance():
    stage = CondensationStage(
        stage_number=1,
        label="Fe below source range",
        temp_range_C=(1326.85, 1326.85),
        target_species=["Fe"],
    )

    record = condensation._stage_alpha_record(stage, "Fe")

    assert record["alpha_s"].hex() == float(0.02).hex()
    assert record["temperature_range_K"] == [1700, 1800]
    assert record["alpha_s_valid_range_K"] == [1700.0, 1800.0]
    assert record["alpha_s_extrapolated"] is True
    assert record["alpha_s_domain_status"] == "out_of_domain"
    assert record["output_status"] == "status_bearing"
    assert "Fe alpha_s scalar coefficient extrapolated" in record[
        "alpha_s_extrapolation_warning"
    ]


def test_wall_antoine_applied_path_reports_extrapolation_without_value_change():
    """Wall pure-Antoine honesty uses a metal rail (Ca), not SiO.

    SiO is a melt standard-reaction term (fit_target=standard_reaction_term),
    not a pure-species wall P_sat. Its oxide Antoine coefficients are not
    wall-condensation certified; the reactive wall path uses the product
    backstop. Probe Antoine telemetry on Ca, which still has a pure-component
    condensed rail with a declared valid_range_K.
    """
    T_wall_K = 1900.0  # above Ca pure-component certified band
    local_pressure_pa = 100.0
    certified_range_K = condensation.VAPOR_PRESSURE_DATA["metals"]["Ca"][
        "valid_range_K"
    ]
    assert certified_range_K[0] < T_wall_K or certified_range_K[1] < T_wall_K
    baseline_pressure = condensation._wall_deposition_driving_pressure_pa(
        "Ca",
        local_pressure_pa,
        T_wall_K,
        reactive_product_backstop=False,
    )
    antoine_extrapolations: dict[str, dict[str, object]] = {}
    antoine_warnings: list[str] = []

    instrumented_pressure = condensation._wall_deposition_driving_pressure_pa(
        "Ca",
        local_pressure_pa,
        T_wall_K,
        reactive_product_backstop=False,
        antoine_extrapolations=antoine_extrapolations,
        antoine_extrapolation_warnings=antoine_warnings,
    )

    assert instrumented_pressure.hex() == baseline_pressure.hex()
    assert antoine_extrapolations["Ca"]["temperature_K"] == pytest.approx(
        T_wall_K
    )
    assert any(
        "Ca metal Antoine fit extrapolated beyond valid_range_K" in warning
        for warning in antoine_warnings
    )

    # SiO standard-reaction term: no pure wall Antoine, no false extrapolation
    # telemetry, and non-reactive driving pressure is zero without a product
    # backstop (documented; not a silent pure-SiO Psat).
    sio_range = condensation.VAPOR_PRESSURE_DATA["oxide_vapors"]["SiO"][
        "valid_range_K"
    ]
    assert sio_range == [1400, 2273.15]
    sio_extrap: dict[str, dict[str, object]] = {}
    sio_warns: list[str] = []
    with pytest.raises(
        condensation.WallSaturationPressureRefusal,
        match="reason=source_certified_range_refused",
    ):
        condensation._wall_deposition_driving_pressure_pa(
            "SiO",
            local_pressure_pa,
            1173.15,
            reactive_product_backstop=False,
            antoine_extrapolations=sio_extrap,
            antoine_extrapolation_warnings=sio_warns,
        )
    refusal = sio_extrap["SiO#wall:1173.15"]
    assert refusal["authority_level"] == "unavailable"
    assert "no extrapolation available" in refusal["reason"]


def test_wall_deposit_query_reports_out_of_domain_antoine_prediction():
    magnesium_vapor = condensation._species_vapor_data(
        "Mg",
        vapor_pressure_data=condensation.VAPOR_PRESSURE_DATA,
    )
    pure_range_K = magnesium_vapor["pure_component_antoine"][
        "source_certified_range_K"
    ]
    # The midpoint stays inside Mg's total certified data rail while exceeding
    # the pure-component range required by the wall-saturation consumer.
    wall_temperature_K = (
        float(pure_range_K[1])
        + float(magnesium_vapor["total_source_certified_range_K"][1])
    ) / 2.0
    assert wall_temperature_K > float(pure_range_K[1])
    assert wall_temperature_K <= float(
        magnesium_vapor["total_source_certified_range_K"][1]
    )

    model = condensation.CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=(
            wall_temperature_K - condensation.CELSIUS_TO_KELVIN_OFFSET
        ),
    )
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Mg": 1.0},
        campaign_name="C0",
    )
    warnings: list[str] = []

    candidate_kg = wall_deposit_candidate_for_surface_kg(
        model,
        species="Mg",
        rate_kg_hr=1.0,
        T_cond_C=model.condensation_temperatures_C["Mg"],
        melt_temperature_C=1700.0,
        wall_temperature_C=(
            wall_temperature_K - condensation.CELSIUS_TO_KELVIN_OFFSET
        ),
        surface_area_m2=model.wall_surface_area_m2,
        antoine_extrapolation_warnings=warnings,
    )

    assert candidate_kg == 0.0
    notice = model.last_sticking_alpha_provenance_notice[
        "wall_saturation_pressure_extrapolations_by_species"
    ]["Mg"]["default_pipe"]
    assert notice["authority_level"] == "extrapolated"
    assert notice["valid_range_K"] == pure_range_K
    assert notice["temperature_K"] == pytest.approx(wall_temperature_K)
    assert any(
        "metal_vapor_pressure_out_of_source_certified_range: species=Mg"
        in warning
        for warning in warnings
    )


def test_antoine_extrapolation_records_count_same_species_stage_and_wall():
    records: dict[str, dict[str, object]] = {}
    warnings: list[str] = []
    data = {"valid_range_K": (1400.0, 2200.0)}

    condensation._record_antoine_extrapolation(
        "SiO",
        1173.15,
        data,
        antoine_extrapolations=records,
        antoine_extrapolation_warnings=warnings,
    )
    condensation._record_antoine_extrapolation(
        "SiO",
        2300.0,
        data,
        antoine_extrapolations=records,
        antoine_extrapolation_warnings=warnings,
    )
    condensation._record_antoine_extrapolation(
        "SiO",
        2300.0,
        data,
        antoine_extrapolations=records,
        antoine_extrapolation_warnings=warnings,
    )
    condensation._record_antoine_extrapolation(
        "Ca",
        2300.0,
        data,
        antoine_extrapolations=records,
        antoine_extrapolation_warnings=warnings,
    )

    assert len(records) == 3
    assert "SiO" in records
    assert "Ca" in records
    assert {record["temperature_K"] for record in records.values()} == {
        1173.15,
        2300.0,
    }
    assert len(warnings) == 3


def test_wall_deposition_flux_telemetry_keeps_applied_flux_identical():
    # Use Ca pure-component rail (has wall Antoine). SiO is a melt
    # standard-reaction term without pure wall P_sat (see SiO row disposition).
    T_wall_K = 1900.0
    kwargs = dict(
        species="Ca",
        P_local_pa=100.0,
        T_surface_K=T_wall_K,
        alpha_s=0.02,
        pipe_diameter_m=0.05,
        T_gas_K=1700.0,
        overhead_pressure_pa=100.0,
        reactive_product_backstop=False,
    )
    baseline_flux = condensation._series_resistance_deposition_flux_mol_m2_s(
        **kwargs
    )
    antoine_extrapolations: dict[str, dict[str, object]] = {}
    antoine_warnings: list[str] = []

    instrumented_flux = condensation._series_resistance_deposition_flux_mol_m2_s(
        **kwargs,
        antoine_extrapolations=antoine_extrapolations,
        antoine_extrapolation_warnings=antoine_warnings,
    )

    assert instrumented_flux.hex() == baseline_flux.hex()
    assert "Ca" in antoine_extrapolations
    assert antoine_warnings


def test_sio_high_side_arrhenius_warning_surfaces_without_value_change():
    T_hot_K = 1900.0
    context: dict[str, object] = {}
    value = condensation.alpha_s("SiO", T_hot_K, context)
    expected = 0.52 * math.exp(-3685.0 / T_hot_K)
    evaluation = context["alpha_s_evaluation"]

    assert value.hex() == expected.hex()
    assert evaluation["alpha_s_extrapolated"] is True
    assert evaluation["alpha_s_temperature_above_valid_range"] is True
    assert evaluation["alpha_s_valid_range_K"] == [1000.0, 1800.0]
    assert "SiO alpha_s arrhenius coefficient extrapolated" in evaluation[
        "alpha_s_extrapolation_warning"
    ]
    assert "[1000, 1800]" in evaluation["alpha_s_extrapolation_warning"]

    in_context: dict[str, object] = {}
    in_value = condensation.alpha_s("SiO", 1500.0, in_context)
    in_expected = 0.52 * math.exp(-3685.0 / 1500.0)
    in_evaluation = in_context["alpha_s_evaluation"]

    assert in_value.hex() == in_expected.hex()
    assert in_evaluation["alpha_s_extrapolated"] is False
    assert "alpha_s_extrapolation_warning" not in in_evaluation


def test_sio_high_side_arrhenius_warning_reaches_alpha_provenance_report():
    segment = PipeSegment(
        name="hot_sio_wall",
        upstream_stage="stage_2",
        downstream_stage="stage_3",
        wall_temperature_C=1626.85,
        length_m=1.0,
        inner_diameter_m=0.05,
    )
    record = condensation._wall_alpha_record("SiO", segment=segment)

    assert record["alpha_s"].hex() == (
        0.52 * math.exp(-3685.0 / 1900.0)
    ).hex()
    assert record["alpha_s_extrapolated"] is True
    assert "alpha_s_extrapolation_warning" in record

    model = condensation.CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        wall_temperature_C=1626.85,
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={"SiO": 1.0},
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
        pipe_segment_temperatures_C={
            segment.name: 1626.85 for segment in model.pipe_segments
        },
    )
    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1700.0),
    )
    warnings = route.sticking_alpha_provenance_notice.get(
        "alpha_s_extrapolation_warnings",
        [],
    )

    assert any(
        "SiO alpha_s arrhenius coefficient extrapolated" in warning
        and "[1000, 1800]" in warning
        for warning in warnings
    )


def test_cold_spot_stage_parser_rejects_numeric_lab_surface_suffix():
    segments = [
        PipeSegment(
            name="lab_surface",
            upstream_stage="lab_surface_1",
            downstream_stage="lab_surface_2",
            wall_temperature_C=500.0,
            length_m=1.0,
            inner_diameter_m=0.12,
        ),
        PipeSegment(
            name="production_stage",
            upstream_stage="stage_1",
            downstream_stage="stage_2",
            wall_temperature_C=500.0,
            length_m=1.0,
            inner_diameter_m=0.12,
        ),
    ]

    diagnostic = condensation.cold_spot_diagnostic(
        segments,
        {"SiO": 1.0},
        upstream_hot_wall_min_C=None,
    )

    assert {row["segment"] for row in diagnostic["findings"]} == {
        "production_stage"
    }


def test_free_molecular_hkl_incidence_uses_gas_temperature(monkeypatch):
    monkeypatch.setattr(
        condensation,
        "_wall_deposition_driving_pressure_pa",
        lambda *args, **kwargs: 1.0,
    )
    kwargs = {
        "species": "SiO",
        "P_local_pa": 1.0,
        "T_surface_K": 900.0,
        "alpha_s": 0.5,
        "regime_factor": 1.0,
    }

    cold_gas_flux = condensation._series_resistance_deposition_flux_mol_m2_s(
        **kwargs, T_gas_K=400.0
    )
    hot_gas_flux = condensation._series_resistance_deposition_flux_mol_m2_s(
        **kwargs, T_gas_K=1600.0
    )

    # J_inc is proportional to T_gas^-1/2 at fixed pressure and molecular mass.
    assert cold_gas_flux / hot_gas_flux == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("source_class", "expected_status", "expected_output_status"),
    [
        ("internal-analytical", "UNCERTIFIED", "status_bearing"),
        ("cited_hkl_accommodation", "sourced", "sourced_with_surface_proxy"),
    ],
)
def test_cold_wall_source_class_controls_certification(
    monkeypatch, source_class, expected_status, expected_output_status
):
    monkeypatch.setattr(
        condensation,
        "_cold_wall_condensation_spec",
        lambda species: {
            "value": 1.0,
            "source": "test cold-wall source",
            "source_url": "https://example.invalid/source",
            "source_class": source_class,
            "status": "CITED",
            "output_status": "sourced_with_surface_proxy",
            "uncertainty_envelope": [0.5, 1.0],
            "uncertainty_flag": "test",
        },
    )
    record = condensation._cold_wall_condensation_record_payload("SiO", {})

    assert record["status"] == expected_status
    assert record["output_status"] == expected_output_status
    if source_class == "internal-analytical":
        assert "cannot certify" in record["certification_status_reason"]
