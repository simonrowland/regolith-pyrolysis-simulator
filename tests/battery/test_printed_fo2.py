"""Printed fO2 facts land on waypoint inputs (t-951).

The author's printed log is kept as stated. A companion pressure in atm is
not the same number as log10(fO2/bar): the offset is log10(101325/100000).
Bounds and inferred values stay refusals.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.identity import PA_PER_ATM_DEC, atm_to_pa
from simulator.battery.migrate import (
    PrintedOxygenFacts,
    collect_printed_oxygen,
    migrate,
)
from simulator.battery.records import Locator
from simulator.battery.waypoints import WaypointAuthority, oxygen_condition
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree
from tests.battery.test_printed_point_conditions import _migrate_real_extract

_BAR_PA = Decimal("100000")
_OFFSET = (PA_PER_ATM_DEC / _BAR_PA).log10()


def _page(page: int = 1) -> Locator:
    return Locator(published_page=page)


def test_collect_rejects_bound_inferred_and_disagreement() -> None:
    bound = collect_printed_oxygen(
        [{
            "oxygen_partial_pressure": {
                "value": "<10^-10",
                "units": "atm",
                "semantics": "upper_bound_not_point",
                "locator": {"published_page": 2004},
            }
        }],
        _page(2004),
    )
    assert bound == PrintedOxygenFacts()

    inferred = collect_printed_oxygen(
        [{
            "log_fO2": {
                "value": -9.1,
                "units": "log10",
                "inferred": True,
                "locator": {"published_page": 1},
            }
        }],
        _page(),
    )
    assert inferred.log_fO2 is None

    disagreed = collect_printed_oxygen(
        [{
            "log_fO2": {"value": -9.1, "units": "log10", "locator": {"published_page": 1}},
            "oxygen_partial_pressure": {
                "value": "10^-8", "units": "atm", "locator": {"published_page": 1},
            },
        }],
        _page(),
    )
    assert disagreed == PrintedOxygenFacts()


def test_badro_printed_log_is_selected_and_atm_offset_is_recorded(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "ta-badro-2021")
    obs = result.observations["ta-badro-2021::badro_2021_vp1_delta26mg"]
    experiment = result.experiments[obs.experiment_id]
    bench = result.benches[experiment.bench_id]
    oxygen = oxygen_condition(experiment, bench, obs)

    assert oxygen.selected is not None
    assert oxygen.selected.route == "observation_fO2_log"
    assert oxygen.selected.authority is WaypointAuthority.PRINTED
    assert oxygen.selected.value.point == Decimal("-9.1")

    located = obs.point_conditions["fO2_log"]
    assert located.inference is None
    assert located.locator is not None
    assert located.locator.published_page == 105
    assert "printed log kept as stated" in (located.locator.note or "")
    assert "101325/100000" in (located.locator.note or "")

    pressure = experiment.fO2_control.oxygen_partial_pressure_Pa
    assert pressure is not None and pressure.state.is_value
    expected_pa = atm_to_pa(Decimal(10) ** Decimal("-9.1"))
    assert pressure.state.value.point == expected_pa
    assert pressure.inference is not None
    assert pressure.inference.relation == "atm_to_Pa"
    assert "printed=oxygen_partial_pressure" in pressure.inference.inputs
    assert "inferred=true" not in pressure.inference.inputs
    bar_log = (expected_pa / _BAR_PA).log10()
    assert bar_log == Decimal("-9.1") + _OFFSET
    routes = {item.route: item.value.point for item in oxygen.routes}
    assert routes["oxygen_partial_pressure_to_log_fO2"] == bar_log
    assert routes["observation_fO2_Pa_to_log_fO2"] == bar_log
    # Decimal subtracts the last digit differently from adding the offset.
    assert abs((bar_log - Decimal("-9.1")) - _OFFSET) < Decimal("1e-24")


def test_gibson_upper_bound_does_not_resolve_oxygen(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-024-gibson-hubbard-1972")
    for obs in result.observations.values():
        assert "fO2_log" not in (obs.point_conditions or {})
        assert "fO2_Pa" not in (obs.point_conditions or {})
    for experiment in result.experiments.values():
        control = experiment.fO2_control
        if control is None or control.oxygen_partial_pressure_Pa is None:
            continue
        assert not control.oxygen_partial_pressure_Pa.state.is_value


def test_series_row_log10_fO2_lands_on_the_exploded_point(tmp_path: Path) -> None:
    doc = {
        **FIXTURE_EXTRACT,
        "species": {
            "Na": {
                "observations": [{
                    **FIXTURE_EXTRACT["species"]["Na"]["observations"][0],
                    "values": {
                        **FIXTURE_EXTRACT["species"]["Na"]["observations"][0]["values"],
                        "series": [
                            {"T_K": 1200.0, "pressure_atm": 1.0, "log10_fO2": -3.5},
                            {"T_K": 1300.0, "pressure_atm": 2.0, "log10_fO2": -4.25},
                        ],
                    },
                }]
            }
        },
    }
    result = migrate(_write_min_tree(tmp_path, doc), write=False)
    first = result.observations["fixture-source::na_psat::point:0"]
    second = result.observations["fixture-source::na_psat::point:1"]
    assert first.point_conditions["fO2_log"].state.value == Decimal("-3.5")
    assert first.point_conditions["fO2_log"].inference is None
    assert second.point_conditions["fO2_log"].state.value == Decimal("-4.25")
    bare = migrate(_write_min_tree(tmp_path / "bare", FIXTURE_EXTRACT), write=False)
    for obs in bare.observations.values():
        assert "fO2_log" not in (obs.point_conditions or {})
        assert "fO2_Pa" not in (obs.point_conditions or {})


def test_sossi_table_row_keeps_its_printed_log(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-012-sossi-2019")
    obs = result.observations[
        "kems-012-sossi-2019::sossi_2019_mn_table2_open_furnace_residue_ppm::point:0"
    ]
    assert obs.point_conditions["fO2_log"].state.value == Decimal("-0.68")
    assert obs.point_conditions["fO2_log"].inference is None
    assert isinstance(obs.point_conditions["fO2_log"].state.value, Decimal)
