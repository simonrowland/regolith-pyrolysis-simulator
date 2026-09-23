"""Printed fO2 facts land on waypoint inputs (t-951).

The author's printed log is kept as stated. A companion pressure in atm is
not the same number as log10(fO2/bar): the offset is log10(101325/100000).
Bounds and inferred values stay refusals.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import ValueKind
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
    first, second = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith("fixture-source::na_psat::")
    ]
    assert first.point_conditions["fO2_log"].state.value == Decimal("-3.5")
    assert first.point_conditions["fO2_log"].inference is None
    assert second.point_conditions["fO2_log"].state.value == Decimal("-4.25")
    bare = migrate(_write_min_tree(tmp_path / "bare", FIXTURE_EXTRACT), write=False)
    for obs in bare.observations.values():
        assert "fO2_log" not in (obs.point_conditions or {})
        assert "fO2_Pa" not in (obs.point_conditions or {})


def test_sossi_table_row_keeps_its_printed_log(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-012-sossi-2019")
    obs = next(
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(
            "kems-012-sossi-2019::"
            "sossi_2019_mn_table2_open_furnace_residue_ppm::"
        )
    )
    assert obs.point_conditions["fO2_log"].state.value == Decimal("-0.68")
    assert obs.point_conditions["fO2_log"].inference is None
    assert isinstance(obs.point_conditions["fO2_log"].state.value, Decimal)


def test_author_ratio_po2_is_derived_not_printed() -> None:
    from simulator.battery.migrate import collect_author_ratio_oxygen

    located = collect_author_ratio_oxygen(
        [{
            "po2_over_pK_as_published": 0.226,
            "P_K_atm_as_published": 6.91e-7,
            "P_O2_atm": 1.56166e-7,
        }],
        _page(279),
    )
    assert located is not None
    assert located.inference is not None
    assert "author_ratio_P_O2_atm" in located.inference.relation
    assert located.state.value.point == atm_to_pa(Decimal("1.56166e-7"))

    assert collect_author_ratio_oxygen(
        [{
            "po2_over_pK_as_published": 0.226,
            "P_K_atm_as_published": 6.91e-7,
            "P_O2_atm": 9.99e-7,
        }],
        _page(279),
    ) is None


# Printed per-run log fO2 that sat under ``rows``/``runs``, or in the unscored
# context container, never reached the store. Those tables are per-run series,
# so they use the ``series`` key the landing machinery iterates (the sossi-2019
# pattern above). Values land as printed: no buffer conversion, no shift.


def _exploded_points(result: object, parent: str) -> list:
    return [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(parent + "::")
    ]


def test_holzheid_table3_rows_land_printed_log_per_run(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path, "holzheid-1997-feo-nio-coo-activity-metal-saturated"
    )
    stem = "holzheid-1997-feo-nio-coo-activity-metal-saturated"
    parent = f"{stem}::holzheid_1997_table3a_ad_co_variable_mgo"
    points = _exploded_points(result, parent)
    assert len(points) == 8
    first = points[0]
    located = first.point_conditions["fO2_log"]
    assert located.state.value == Decimal("-9.63")
    assert located.inference is None
    # A later row keeps its own printed log. Not collapsed, not shifted to dIW.
    assert points[2].point_conditions["fO2_log"].state.value == Decimal("-9.93")
    assert points[2].point_conditions["fO2_log"].inference is None
    assert first.value.kind is not ValueKind.POINT or first.value.point != Decimal("-9.63")
    # Table 3 prints T_K per run; it lands beside the printed log.
    assert first.point_conditions["temperature_K"].state.value == Decimal("1673")
    # Every run of the five Table 3 panels carries its printed log.
    landed = [
        obs
        for obs in result.observations.values()
        if "fO2_log" in (obs.point_conditions or {})
    ]
    assert len(landed) == 33
    # The printed log resolves the oxygen_condition waypoint as PRINTED.
    experiment = result.experiments[first.experiment_id]
    bench = result.benches[experiment.bench_id]
    oxygen = oxygen_condition(experiment, bench, first)
    assert oxygen.selected is not None
    assert oxygen.selected.route == "observation_fO2_log"
    assert oxygen.selected.authority is WaypointAuthority.PRINTED
    assert oxygen.selected.value.point == Decimal("-9.63")


def test_sossi_2020_table1_rows_land_printed_log_and_celsius(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "sossi-2020-cu-zn-isotope-evap-formalism")
    stem = "sossi-2020-cu-zn-isotope-evap-formalism"
    cu_points = _exploded_points(result, f"{stem}::sossi_2020_cu_table1_measured_runs")
    zn_points = _exploded_points(result, f"{stem}::sossi_2020_zn_table1_measured_runs")
    assert len(cu_points) == 34
    assert len(zn_points) == 36
    first = cu_points[0]
    # Table 1 prints log10(fO2/bar) = -0.68 and 1500 C for run P 5/04/17b.
    located = first.point_conditions["fO2_log"]
    assert located.state.value == Decimal("-0.68")
    assert located.inference is None
    # Run P 31/07/18a is series index 10 and prints -2.74, not the air value.
    # No bar/atm shift. Look up the id, not a lexicographic sort of point:N.
    run = next(
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(
            f"{stem}::sossi_2020_cu_table1_measured_runs::"
        )
        and "row=p-31/07/18a:" in oid
    )
    assert run.point_conditions["fO2_log"].state.value == Decimal("-2.74")
    assert run.point_conditions["fO2_log"].inference is None
    assert first.value.kind is not ValueKind.POINT or first.value.point != Decimal("-0.68")
    temperature = first.point_conditions["temperature_K"]
    assert temperature.state.value == Decimal("1773.15")
    # The kelvin figure is the code's C-to-K unit conversion of the printed
    # 1500 C, so it is typed as a conversion, never as a second printed fact.
    assert temperature.inference is not None
    zn_first = zn_points[0]
    assert zn_first.point_conditions["fO2_log"].state.value == Decimal("-0.68")
    assert zn_first.point_conditions["fO2_log"].inference is None


def test_heck_run_log_lands_printed_log_per_run(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-140-heck-2025")
    parent = "kems-140-heck-2025::heck_2025_t1_experimental_conditions"
    points = _exploded_points(result, parent)
    assert len(points) == 81
    located = points[0].point_conditions["fO2_log"]
    # Ex 7 prints log10 fO2 = -0.68 (the air row) and T_K = 1673.
    assert located.state.value == Decimal("-0.68")
    assert located.inference is None
    assert points[0].point_conditions["temperature_K"].state.value == Decimal("1673")
    landed = {
        obs.point_conditions["fO2_log"].state.value
        for obs in points
        if "fO2_log" in (obs.point_conditions or {})
    }
    assert landed == {
        Decimal("-0.68"),
        Decimal("-4.3049"),
        Decimal("-4.8"),
        Decimal("-5.3049"),
        Decimal("-7.3"),
        Decimal("-7.3144"),
        Decimal("-9"),
    }


def test_thomas_table2_log_fO2_lands_printed_per_run(tmp_path: Path) -> None:
    # Table 2 header is "log f(O2)". No buffer symbol and no bar/atm unit.
    # The printed log lands unchanged. f(Cl2) stays a linear fugacity and is
    # not logged. The run log is not a Gibbs energy, so the point value is
    # unavailable rather than one of the table's other numbers.
    result = _migrate_real_extract(tmp_path, "thomas-2022-chlorine-bonding-silicate-melts")
    parent = "thomas-2022-chlorine-bonding-silicate-melts::thomas_2022_table2_experimental_conditions_and_xaf"
    points = _exploded_points(result, parent)
    assert len(points) == 43
    located = points[0].point_conditions["fO2_log"]
    assert located.state.value == Decimal("-7.17")
    assert located.inference is None
    assert points[0].point_conditions["temperature_K"].state.value == Decimal("1673.15")
    assert points[0].point_conditions["temperature_K"].inference is not None
    assert points[0].value.kind is ValueKind.UNAVAILABLE
    landed = {
        obs.point_conditions["fO2_log"].state.value
        for obs in points
        if "fO2_log" in (obs.point_conditions or {})
    }
    assert landed == {
        Decimal("-7.17"),
        Decimal("-7.9"),
        Decimal("-8.2"),
        Decimal("-6.66"),
    }
    for obs in points:
        assert "fO2_Pa" not in (obs.point_conditions or {})

def test_oxygen_condition_doc_does_not_call_a_printed_log_a_bar_value() -> None:
    """An atm-referenced printed log is kept as stated. The docstring must
    not promise that selected number is log10(fO2 / 1 bar); only derived
    pressure routes are that frame."""
    doc = oxygen_condition.__doc__ or ""
    assert "log10(fO2 / 1 bar)" not in doc
    assert "kept as the author stated" in doc
    assert "log10(pO2 Pa / 1 bar)" in doc


def test_power_of_ten_string_is_not_a_log_value() -> None:
    """A "10^-9.1" string is a pressure amount, never a log. On a log key the
    linear amount would land ~1e-9 as fO2_log and stamp oxygen_condition near
    zero; the key is skipped instead. Numeric and numeric-string logs land."""
    mapping_form = collect_printed_oxygen(
        [{
            "log_fO2": {
                "value": "10^-9.1",
                "units": "log10",
                "locator": {"published_page": 1},
            }
        }],
        _page(),
    )
    assert mapping_form.log_fO2 is None

    bare_scalar = collect_printed_oxygen([{"log_fO2": "10^-9.1"}], _page())
    assert bare_scalar.log_fO2 is None

    numeric = collect_printed_oxygen(
        [{"log_fO2": {"value": -9.1, "units": "log10", "locator": {"published_page": 1}}}],
        _page(),
    )
    assert numeric.log_fO2 is not None
    assert numeric.log_fO2.state.value == Decimal("-9.1")
    numeric_string = collect_printed_oxygen(
        [{"log_fO2": {"value": "-9.1", "units": "log10", "locator": {"published_page": 1}}}],
        _page(),
    )
    assert numeric_string.log_fO2 is not None
    assert numeric_string.log_fO2.state.value == Decimal("-9.1")


def test_printed_point_does_not_collapse_an_experiment_interval(tmp_path: Path) -> None:
    """A printed pO2 point on one observation must not overwrite the
    experiment's printed interval (Norris pattern: log range -7..-13 landed
    as a pO2_Pa interval). The interval stays; the observation keeps its own
    printed point in point_conditions."""
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["experiments"] = [
        {
            "experiment_id": "fixture-series",
            "method": "knudsen_effusion",
            "locator": {"page": 2, "section": "experimental"},
            "fO2_control": {
                "channel": {"tag": "value", "value": "gas_mix"},
                "oxygen_partial_pressure_Pa": {
                    "state": {
                        "tag": "value",
                        "value": {
                            "kind": "interval",
                            "interval_low": "1.01325e-8",
                            "interval_high": "1.01325e-2",
                        },
                    },
                    "locator": {"page": 2, "section": "experimental"},
                },
            },
        }
    ]
    observation = extract["species"]["Na"]["observations"][0]
    observation["experiment"] = "fixture-series"
    observation["values"]["oxygen_partial_pressure"] = {
        "value": "10^-9.1",
        "units": "atm",
        "locator": {"page": 2},
    }
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    experiment_id = next(
        eid for eid in result.experiments if eid.endswith("::experiment::fixture-series")
    )
    control = result.experiments[experiment_id].fO2_control
    landed = control.oxygen_partial_pressure_Pa.state.value
    assert landed.kind is ValueKind.INTERVAL
    assert landed.interval_low == Decimal("1.01325e-8")
    assert landed.interval_high == Decimal("1.01325e-2")
    landed_points = [
        obs
        for obs in result.observations.values()
        if obs.experiment_id == experiment_id and "fO2_Pa" in (obs.point_conditions or {})
    ]
    assert len(landed_points) == 2
    for obs in landed_points:
        point = obs.point_conditions["fO2_Pa"].state.value
        assert point.kind is ValueKind.POINT
        assert point.point == atm_to_pa(Decimal(10) ** Decimal("-9.1"))
