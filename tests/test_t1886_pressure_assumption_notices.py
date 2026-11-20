from __future__ import annotations

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


@pytest.mark.parametrize(
    ("total_pressure_mbar", "species_partial_pressure_mbar", "expected_notice"),
    [
        (1013.25, 1.0, False),
        (1013.25, 0.1, True),
        (1.0, 1.0, False),
        (1.0, 0.1, True),
    ],
)
def test_condensation_fallback_notice_uses_species_partial_pressure(
    total_pressure_mbar,
    species_partial_pressure_mbar,
    expected_notice,
):
    model = CondensationModel(CondensationTrain.create_default())
    model.condensation_temperatures_C.pop("Fe")
    model.configure_operating_conditions(
        overhead_pressure_mbar=total_pressure_mbar,
        species_partial_pressures_mbar={
            "Fe": species_partial_pressure_mbar,
        },
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    flux = EvaporationFlux(species_kg_hr={"Fe": 1.0}, total_kg_hr=1.0)
    result = model.route(
        flux,
        MeltState(
            temperature_C=1500.0,
            p_total_mbar=total_pressure_mbar,
        ),
    )

    assert model.wall_species_partial_pressures_pa["Fe"] == pytest.approx(
        species_partial_pressure_mbar * 100.0
    )
    notice_map = result.sticking_alpha_provenance_notice.get(
        "condensation_pressure_assumption_notices_by_species",
        {},
    )
    assert ("Fe" in notice_map) is expected_notice
    if not expected_notice:
        return
    notice = _assert_notice(notice_map.values(), code="condensation_routing_pressure_assumption")
    assert notice["kind"] == "out_of_domain_physics"
    assert notice["authority_level"] == "extrapolated"
    assert notice["certified_band"]["partial_pressure_mbar"] == [1.0, 1.0]
    assert notice["running_species_partial_pressure_mbar"] == pytest.approx(
        species_partial_pressure_mbar
    )
    assert notice["running_total_pressure_mbar"] == pytest.approx(
        total_pressure_mbar
    )


def _inert_cover_sim(
    total_pressure_mbar: float,
    *,
    atmosphere: Atmosphere = Atmosphere.PN2_SWEEP,
    cover_species: str = "N2",
) -> PyrolysisSimulator:
    backend = InternalAnalyticalBackend()
    sim = PyrolysisSimulator(
        backend,
        _load_yaml("setpoints.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    sim.melt.atmosphere = atmosphere
    sim.melt.p_total_mbar = total_pressure_mbar
    sim.melt.pO2_mbar = 0.0
    sim.melt.background_gas_species = cover_species
    sim.melt.background_gas_mole_fraction = 1.0
    return sim


def _pressure_notices(result):
    return result.diagnostics.get("pressure_assumption_notices", [])


@pytest.mark.parametrize(
    ("atmosphere", "cover_species"),
    [
        (Atmosphere.PN2_SWEEP, "N2"),
        (Atmosphere.ARGON_FLOW, "Ar"),
    ],
)
def test_inert_o2_floor_atmospheric_pressure_is_flagged_on_first_equilibrium(
    atmosphere,
    cover_species,
):
    sim = _inert_cover_sim(
        1013.25,
        atmosphere=atmosphere,
        cover_species=cover_species,
    )
    sim.melt.temperature_C = 1500.0

    assert sim._commanded_pO2_bar() == pytest.approx(DEFAULT_VACUUM_FLOOR_BAR)
    result = sim._get_equilibrium()
    assert result.status == "ok"
    assert result.vapor_pressures_Pa
    notices = _pressure_notices(result)
    notice = _assert_notice(notices, code="inert_cover_numerical_o2_floor")
    assert notice["authority_level"] == "numerical_guard"
    assert notice["certified_band"]["pO2_bar"] == [0.0, DEFAULT_VACUUM_FLOOR_BAR]
    assert notice["running_total_pressure_mbar"] == pytest.approx(1013.25)


def test_inert_o2_floor_notice_uses_refreshed_transport_reservoir():
    sim = _inert_cover_sim(1013.25)
    sim.melt.temperature_C = 1500.0
    reservoir = sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    assert reservoir.headspace_transport_pO2_bar == DEFAULT_VACUUM_FLOOR_BAR

    result = sim._get_equilibrium()

    assert result.status == "ok"
    assert result.vapor_pressures_Pa
    _assert_notice(
        _pressure_notices(result),
        code="inert_cover_numerical_o2_floor",
    )


def test_inert_o2_floor_notice_is_absent_when_transport_o2_exceeds_floor():
    sim = _inert_cover_sim(1013.25)
    sim.melt.temperature_C = 1500.0
    sim.melt.pO2_mbar = 3.7e-4
    reservoir = sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    assert reservoir.headspace_transport_pO2_bar > DEFAULT_VACUUM_FLOOR_BAR

    result = sim._get_equilibrium()

    assert result.status == "ok"
    assert result.vapor_pressures_Pa
    assert not _pressure_notices(result)


def test_inert_o2_floor_notice_does_not_leak_after_atmosphere_change():
    sim = _inert_cover_sim(1.0)
    assert sim._headspace_transport_pO2_bar() == DEFAULT_VACUUM_FLOOR_BAR
    sim.melt.atmosphere = Atmosphere.HARD_VACUUM
    sim.melt.p_total_mbar = 0.0
    sim.melt.temperature_C = 25.0

    result = sim._get_equilibrium()

    assert result.status == "ok"
    assert not _pressure_notices(result)


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
