from __future__ import annotations

from collections import defaultdict

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
