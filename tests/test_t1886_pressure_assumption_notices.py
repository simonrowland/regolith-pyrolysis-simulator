from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from simulator.condensation import CondensationModel
from simulator.core import PyrolysisSimulator
from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import (
    Atmosphere,
    CondensationTrain,
    EvaporationFlux,
    MeltState,
)
from simulator.wall_advisor import resolve_wall_operating_point


ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(name: str):
    return yaml.safe_load((ROOT / "data" / name).read_text(encoding="utf-8"))


def _assert_notice(records, *, code: str):
    matches = [record for record in records if record.get("code") == code]
    assert len(matches) == 1
    notice = matches[0]
    assert notice.get("reason")
    assert notice.get("authority_level")
    assert notice.get("certified_band")
    return notice


def test_condensation_fallback_atmospheric_pressure_is_flagged_and_mutation_proven():
    model = CondensationModel(CondensationTrain.create_default())
    model.condensation_temperatures_C.pop("Fe")
    model.configure_operating_conditions(
        overhead_pressure_mbar=1013.25,
        species_partial_pressures_mbar={"Fe": 1.0},
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    flux = EvaporationFlux(species_kg_hr={"Fe": 1.0}, total_kg_hr=1.0)
    result = model.route(
        flux,
        MeltState(temperature_C=1500.0, p_total_mbar=1013.25),
    )

    notice_map = result.sticking_alpha_provenance_notice[
        "condensation_pressure_assumption_notices_by_species"
    ]
    notice = _assert_notice(
        notice_map.values(),
        code="condensation_routing_pressure_assumption",
    )
    assert notice["kind"] == "out_of_domain_physics"
    assert notice["authority_level"] == "extrapolated"
    assert notice["certified_band"]["partial_pressure_mbar"] == [1.0, 1.0]
    assert notice["running_total_pressure_mbar"] == pytest.approx(1013.25)

    mutated = copy.deepcopy(result.sticking_alpha_provenance_notice)
    mutated.pop("condensation_pressure_assumption_notices_by_species")
    with pytest.raises(AssertionError):
        notices = mutated.get(
            "condensation_pressure_assumption_notices_by_species", {}
        )
        _assert_notice(notices.values(), code="condensation_routing_pressure_assumption")


def test_inert_o2_floor_atmospheric_pressure_is_flagged_and_mutation_proven():
    backend = InternalAnalyticalBackend()
    sim = PyrolysisSimulator(
        backend,
        _load_yaml("setpoints.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.p_total_mbar = 1013.25
    sim.melt.pO2_mbar = 0.0
    sim.melt.background_gas_species = "N2"
    sim.melt.background_gas_mole_fraction = 1.0

    assert sim._commanded_pO2_bar() == pytest.approx(DEFAULT_VACUUM_FLOOR_BAR)
    result = sim._get_equilibrium()
    notices = result.diagnostics["pressure_assumption_notices"]
    notice = _assert_notice(notices, code="inert_cover_numerical_o2_floor")
    assert notice["authority_level"] == "numerical_guard"
    assert notice["certified_band"]["pO2_bar"] == [0.0, DEFAULT_VACUUM_FLOOR_BAR]
    assert notice["running_total_pressure_mbar"] == pytest.approx(1013.25)

    mutated = copy.deepcopy(result.diagnostics)
    mutated["pressure_assumption_notices"] = []
    with pytest.raises(AssertionError):
        _assert_notice(
            mutated["pressure_assumption_notices"],
            code="inert_cover_numerical_o2_floor",
        )


def test_wall_advisor_reports_viscous_above_sweep_atmospheric_pressure_and_mutation_proven():
    operating_point = resolve_wall_operating_point(
        pO2_mbar=0.0,
        p_buffer_mbar=1013.25,
    )
    assert operating_point.pressure_regime == "viscous_above_sweep"

    # Preserve the existing vacuum and 5–15 mbar sweep classifications.
    assert resolve_wall_operating_point(p_buffer_mbar=4.99).pressure_regime == "vacuum"
    assert resolve_wall_operating_point(p_buffer_mbar=5.0).pressure_regime == "millibar_sweep"
    assert resolve_wall_operating_point(p_buffer_mbar=15.0).pressure_regime == "millibar_sweep"

    mutated = replace(operating_point, pressure_regime="millibar_sweep")
    with pytest.raises(AssertionError):
        assert mutated.pressure_regime == "viscous_above_sweep"
