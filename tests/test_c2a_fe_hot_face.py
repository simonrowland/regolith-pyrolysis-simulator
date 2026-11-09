from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from simulator.compartment_model import diagnose_compartment_hour
from simulator.condensation import _try_antoine_psat_pa
from simulator.runner import PyrolysisRun
from simulator.trace import PhysicsTrace


def test_c2a_fe_hot_face_stays_above_local_dewpoint_each_hour() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=225,
        mass_kg=1.0,
        chemistry_kernel={"liquid_fraction_provider": "analytical"},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    document = run._run_session(session)
    sim = session.simulator
    assert document["status"] == "ok"

    trace = PhysicsTrace.from_simulator(sim)
    model = sim.condensation_model
    fe_hours = []
    violations = []
    for index, entry in enumerate(model.operating_history):
        if entry.get("campaign") != "C2A":
            continue
        p_fe_pa = float(
            entry.get("species_partial_pressures_mbar", {}).get("Fe", 0.0)
        ) * 100.0
        if p_fe_pa <= 0.0:
            continue
        wall_C = float(
            entry["pipe_segment_temperatures_C"]["stage_0_to_stage_1"]
        )
        psat_pa, refused = _try_antoine_psat_pa(
            "Fe",
            wall_C + 273.15,
            vapor_pressure_data=model.vapor_pressure_data,
        )
        assert not refused and psat_pa is not None
        fe_hours.append(index)
        # For the production monotone Fe Antoine curve, p_Fe > P_sat(T_wall)
        # is the exact equivalent of the wall being below the local dew point.
        if p_fe_pa > psat_pa:
            violations.append((index, wall_C, p_fe_pa, psat_pa))

    assert fe_hours, "the 1 kg C2A hero did not produce an Fe-bearing hour"
    assert not violations, (
        "stage_0_to_stage_1 wall below local Fe dew point: "
        f"{violations!r}"
    )

    duct_deposits: dict[str, float] = defaultdict(float)
    stage1_fe_kg = 0.0
    for snapshot, condensed, wall_deposits in zip(
        trace.snapshots,
        trace.condensed_by_stage_species_delta,
        trace.wall_deposit_by_segment_species_delta,
    ):
        if getattr(snapshot.campaign, "name", str(snapshot.campaign)) != "C2A":
            continue
        for (stage_number, species), mass_kg in condensed.items():
            if int(stage_number) == 1 and species == "Fe":
                stage1_fe_kg += float(mass_kg)
        for (segment, species), mass_kg in wall_deposits.items():
            if segment == "stage_0_to_stage_1":
                duct_deposits[str(species)] += float(mass_kg)

    assert duct_deposits.get("Fe", 0.0) < 1e-9
    print(
        "C2A Fe hot-face diagnostic:",
        f"Fe hours={len(fe_hours)} supersaturation flags={len(violations)}",
        f"stage_0_to_stage_1 deposits kg={dict(duct_deposits)}",
        f"Stage-1 Fe capture kg={stage1_fe_kg:.9f}",
    )


def test_compartment_diagnostic_reads_one_real_c2a_hero_hour() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        mass_kg=1.0,
        chemistry_kernel={"liquid_fraction_provider": "analytical"},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    # C2A follows the C0 heat-up in a full campaign; initialize at its existing
    # 1400–1600 C peak window to exercise one genuine hot C2A route hour.
    session.simulator.melt.temperature_C = 1500.0
    document = run._run_session(session)
    sim = session.simulator
    assert document["status"] == "ok"

    trace = PhysicsTrace.from_simulator(sim)
    model = sim.condensation_model
    hour_index = 0
    hour_output = document["per_hour_summary"][hour_index]
    operating_snapshot = model.operating_history[hour_index]
    hour_output_before = deepcopy(hour_output)
    operating_snapshot_before = deepcopy(operating_snapshot)
    ledger_before = sim.atom_ledger.mol_by_account()
    compartment_report = diagnose_compartment_hour(
        hour_output,
        operating_snapshot,
        pipe_segments=model.pipe_segments,
        vapor_pressure_data=model.vapor_pressure_data,
        species_formula_registry=sim.species_formula_registry,
        condenser_capture_by_stage_species_kg=(
            trace.condensed_by_stage_species_delta[hour_index]
        ),
        condensation_model=model,
        evaporation_owner=sim,
    )
    ledger_after = sim.atom_ledger.mol_by_account()
    assert hour_output == hour_output_before
    assert operating_snapshot == operating_snapshot_before
    assert ledger_after == ledger_before
    assert compartment_report["status"] == "diagnostic_only"
    assert "headspace_partials_one_hour_lagged_b1146_period2_artifact" in (
        compartment_report["flags"]
    )
    assert compartment_report["species_balances"]["Fe"]["status"] == "computed"
    assert abs(
        compartment_report["species_balances"]["Fe"][
            "relative_mass_balance_error"
        ]
    ) <= 1.0e-12
    assert compartment_report["ledger_effect"] == "none"
    assert compartment_report["score_effect"] == "none"
