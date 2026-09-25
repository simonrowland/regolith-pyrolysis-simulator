"""Loader: rows and non-yield points explode on the series point path.

Explicit ``point_conditions`` on the row overwrite inferred conditions.
Yield tables stay on their existing path and are not emitted twice.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.consumer_inputs import collect_consumer_inputs
from simulator.battery.enums import EvidenceClass, ValueKind
from simulator.battery.generators.bench import engine_point_requests
from simulator.battery.identity import atm_to_pa
from simulator.battery.migrate import (
    Migrator,
    _sample_from_plain,
    migrate,
    wt_pct_to_mole_fraction,
)
from simulator.battery.records import Sample, as_decimal
from simulator.battery.waypoints import (
    GapReason,
    _molar_mass_kg_mol,
    consumer_readiness,
    normalized_composition,
)
from tests.battery import factories
from tests.battery.test_bench_generators import case
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree
from tests.battery.test_schema_admission import _child, _migrate, _row
from tests.battery.test_waypoints import _bench


def _migrate_obs(tmp_path: Path, values: dict):
    extract = deepcopy(FIXTURE_EXTRACT)
    row = extract["species"]["Na"]["observations"][0]
    row["values"] = {
        "quantity": "pure_Psat",
        "method_class": "measured_direct",
        "admission_status": "admitted",
        **values,
    }
    root = tmp_path / "tree"
    root.mkdir(parents=True)
    result = migrate(_write_min_tree(root, extract), write=False)
    return row, result


def test_exploded_row_parent_derived_from_points_at_children(tmp_path: Path) -> None:
    extract = deepcopy(FIXTURE_EXTRACT)
    first = extract["species"]["Na"]["observations"][0]
    first["values"] = {
        "quantity": "pure_Psat",
        "method_class": "measured_direct",
        "admission_status": "admitted",
        "rows": [
            {"T_K": 1200, "pressure_atm": 1, "run": "a"},
            {"T_K": 1300, "pressure_atm": 2, "run": "b"},
        ],
    }
    extract["species"]["Na"]["observations"].append(
        {
            "observation_id": "from_table",
            "type": "psat_series",
            "locator": {"table": "II", "page": 3},
            "phase": "gas",
            "regime": "knudsen_effusion",
            "units": "atm",
            "values": {
                "quantity": "pure_Psat",
                "method_class": "measured_direct",
                "admission_status": "admitted",
                "derived_from": "na_psat",
                "rows": [{"T_K": 1400, "pressure_atm": 1, "run": "c"}],
            },
        }
    )
    result = migrate(_write_min_tree(tmp_path / "tree", extract), write=False)
    child = next(obs for obs in result.observations.values() if "from_table" in obs.observation_id)
    assert child.derived_from
    assert "fixture-source::na_psat" not in child.derived_from
    assert all(item.startswith("fixture-source::na_psat::") for item in child.derived_from)
    dangling = [
        issue for issue in result.validation.hard_issues
        if issue.reason.value == "referential_integrity"
    ]
    assert not dangling


def test_rows_explode_through_series_point_path(tmp_path: Path) -> None:
    parent, result = _migrate_obs(
        tmp_path,
        {
            "rows": [
                {"T_K": 1400, "pressure_atm": 1, "run": "a"},
                {"T_K": 1500, "pressure_atm": 2, "run": "b"},
            ]
        },
    )
    children = [
        obs
        for obs in result.observations.values()
        if obs.observation_id.startswith("fixture-source::na_psat")
    ]
    assert len(children) == 2
    assert "fixture-source::na_psat" not in result.observations
    temperatures = sorted(
        obs.identity.temperature_K.value for obs in children if obs.identity.temperature_K.is_value
    )
    assert temperatures == [Decimal("1400"), Decimal("1500")]
    pressures = sorted(obs.value.point for obs in children)
    assert pressures == [atm_to_pa("1"), atm_to_pa("2")]
    assert all(
        obs.point_conditions and obs.point_conditions["temperature_K"].state.value == obs.identity.temperature_K.value
        for obs in children
    )


def test_nonyield_points_explode_once_and_yield_points_stay_single(tmp_path: Path) -> None:
    _, points = _migrate_obs(
        tmp_path / "points",
        {"points": [{"T_K": 1200, "pressure_atm": 1}, {"T_K": 1300, "pressure_atm": 2}]},
    )
    exploded = [
        obs
        for obs in points.observations.values()
        if obs.observation_id.startswith("fixture-source::na_psat")
    ]
    assert len(exploded) == 2
    assert "fixture-source::na_psat" not in points.observations

    _, yielded = _migrate_obs(
        tmp_path / "yield",
        {
            "points": [
                {"T_C": 1000, "mass_loss_pct": 10},
                {"T_C": 1100, "mass_loss_pct": 20},
            ]
        },
    )
    yield_children = [
        obs
        for obs in yielded.observations.values()
        if "na_psat" in obs.observation_id
    ]
    assert len(yield_children) == 2


def test_explicit_row_point_conditions_win_over_inferred(tmp_path: Path) -> None:
    _, result = _migrate_obs(
        tmp_path,
        {
            "rows": [
                {
                    "T_K": 1400,
                    "total_pressure_Pa": 5,
                    "run": "explicit",
                    "point_conditions": {
                        "temperature_K": {
                            "state": {"tag": "value", "value": {"kind": "point", "point": "1500"}}
                        },
                        "total_pressure_Pa": {
                            "state": {
                                "tag": "value",
                                "value": {
                                    "kind": "interval",
                                    "interval_low": "10",
                                    "interval_high": "20",
                                },
                            }
                        },
                    },
                }
            ]
        },
    )
    obs = next(iter(result.observations.values()))
    temperature = obs.point_conditions["temperature_K"].state.value
    assert getattr(temperature, "point", temperature) == Decimal("1500")
    pressure = obs.point_conditions["total_pressure_Pa"]
    assert pressure.state.value.kind is ValueKind.INTERVAL
    assert pressure.state.value.interval_low == as_decimal("10")
    assert pressure.state.value.interval_high == as_decimal("20")
    experiment = factories.kems_experiment()
    readiness = consumer_readiness(
        experiment, _bench(), replace(obs, experiment_id=experiment.experiment_id)
    )
    pressure_gap = next(
        item
        for item in readiness
        if item.consumer == "engine_point" and item.engine == "internal-analytical"
    )
    assert any(
        gap.waypoint == "pressure_boundary" and gap.reason is GapReason.INTERVAL_NEEDS_POINT
        for gap in pressure_gap.gaps
    )


def _printed_experiment(raw: object):
    experiment, bench, observation = case()
    experiment = replace(
        experiment,
        sample=Sample(printed_composition=factories.located(raw)),
    )
    return experiment, bench, observation


def test_feot_maps_to_feo_with_notice_and_printed_map_keeps_feot() -> None:
    raw = {
        "amount_basis": "mass_percent",
        "components": [["SiO2", "60"], ["Al2O3", "10"], ["FeOT", "14.58"], ["MgO", "15.42"]],
    }
    experiment, bench, observation = _printed_experiment(raw)
    selected = normalized_composition(experiment, _bench()).selected
    assert selected is not None
    assert "FeOT" not in selected.value
    assert set(selected.value) == {"SiO2", "FeO", "Al2O3", "MgO"}
    weights = {"SiO2": Decimal("60"), "Al2O3": Decimal("10"), "FeO": Decimal("14.58"), "MgO": Decimal("15.42")}
    moles = {name: weight / _molar_mass_kg_mol(name) for name, weight in weights.items()}
    total = sum(moles.values())
    assert selected.value == {name: amount / total for name, amount in moles.items()}
    assert selected.notice is not None
    assert "total_iron_as_FeO" in selected.notice
    assert "total iron reported as FeO; Fe3+/Fe2+ not printed" in selected.notice
    assert selected.inference is not None
    assert selected.inference.relation == "total_iron_as_FeO"
    assert selected.inference.inputs == ("FeOT",)
    assert "FeOT" in str(experiment.sample.printed_composition.state.value)
    assert experiment.sample.printed_composition.inference is None
    requests = engine_point_requests(collect_consumer_inputs(experiment, bench, observation))
    assert len(requests) == 8
    assert all(item.payload is not None for item in requests)
    assert all(
        "total_iron_as_FeO" in item.payload["composition_notice"]
        and "total iron reported as FeO; Fe3+/Fe2+ not printed" in item.payload["composition_notice"]
        for item in requests
    )
    assert all("FeO" in item.payload["composition_mol"] and "FeOT" not in item.payload["composition_mol"] for item in requests)
    assert all(
        item.provenance["output_routes"]["composition_mol"]["relation"] == "total_iron_as_FeO"
        for item in requests
    )


def test_printed_feo_fe2o3_pair_is_not_rewritten() -> None:
    raw = {"SiO2": Decimal("50"), "FeO": Decimal("30"), "Fe2O3": Decimal("20")}
    experiment, _bench_unused, _observation = _printed_experiment(raw)
    selected = normalized_composition(experiment, _bench()).selected
    assert selected is not None
    assert set(selected.value) == {"SiO2", "FeO", "Fe2O3"}
    assert selected.notice is None or "total_iron_as_FeO" not in selected.notice


def test_feot_beside_feo_stays_unsupported() -> None:
    raw = {"SiO2": Decimal("40"), "FeO": Decimal("30"), "FeOT": Decimal("30")}
    experiment, _bench_unused, _observation = _printed_experiment(raw)
    result = normalized_composition(experiment, _bench())
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM


def test_trace_non_oxides_are_omitted_and_named() -> None:
    raw = {"SiO2": Decimal("50"), "MgO": Decimal("49.2"), "S": "0.5", "Cl": "0.3"}
    experiment, bench, observation = _printed_experiment(raw)
    selected = normalized_composition(experiment, _bench()).selected
    assert selected is not None
    assert set(selected.value) == {"SiO2", "MgO"}
    expected = wt_pct_to_mole_fraction({"SiO2": Decimal("50"), "MgO": Decimal("49.2")})
    for name, amount in expected.components:
        assert abs(selected.value[name] - amount) / amount < Decimal("1e-9")
    assert selected.notice is not None
    assert "S 0.5 wt%" in selected.notice
    assert "Cl 0.3 wt%" in selected.notice
    printed = str(experiment.sample.printed_composition.state.value)
    assert "S" in printed and "Cl" in printed
    requests = engine_point_requests(collect_consumer_inputs(experiment, bench, observation))
    assert all(
        item.payload is not None
        and "S 0.5 wt%" in item.payload["composition_notice"]
        and "Cl 0.3 wt%" in item.payload["composition_notice"]
        for item in requests
    )


def test_non_oxide_above_one_weight_percent_stays_refused() -> None:
    raw = {"SiO2": Decimal("50"), "MgO": Decimal("39"), "Cl": Decimal("11")}
    experiment, bench, observation = _printed_experiment(raw)
    result = normalized_composition(experiment, _bench())
    assert result.selected is None
    assert result.absence is not None
    assert result.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    requests = engine_point_requests(collect_consumer_inputs(experiment, bench, observation))
    assert len(requests) == 8
    assert all(item.payload is None for item in requests)
    assert all(
        any(gap.reason is GapReason.UNSUPPORTED_PRINT_FORM for gap in item.readiness.gaps)
        for item in requests
    )


def _pressure(obs):
    located = obs.point_conditions["total_pressure_Pa"]
    value = located.state.value
    return getattr(value, "point", value), located.inference


def test_row_pressure_columns_convert_through_the_unit_route(tmp_path: Path) -> None:
    _, gpa = _migrate_obs(tmp_path / "gpa", {"rows": [{"T_K": 1673.15, "P_GPa": 1.5, "run": "a"}]})
    obs = next(iter(gpa.observations.values()))
    pascals, inference = _pressure(obs)
    assert pascals == Decimal("1500000000")
    assert inference is not None
    assert inference.relation == "GPa_to_Pa"
    assert "1e9" in " ".join(inference.inputs) or any(
        "1e9" in str(item) or "1000000000" in str(item) for item in inference.inputs
    )

    _, atm = _migrate_obs(
        tmp_path / "atm",
        {"rows": [{"T_K": 1673.15, "total_pressure_atm": 1, "run": "b"}]},
    )
    obs = next(iter(atm.observations.values()))
    pascals, inference = _pressure(obs)
    assert pascals == Decimal("101325")
    assert inference is not None
    assert inference.relation == "atm_to_Pa"

    _, bare = _migrate_obs(
        tmp_path / "bare",
        {"rows": [{"T_K": 1673.15, "total_pressure_Pa": 1, "run": "c"}]},
    )
    obs = next(iter(bare.observations.values()))
    pascals, inference = _pressure(obs)
    assert pascals == Decimal("1")
    assert inference is None or not str(inference.relation).endswith("atm_to_Pa")

    _, vapor = _migrate_obs(
        tmp_path / "vapor",
        {"rows": [{"T_K": 1673.15, "P_atm": 1, "run": "d"}]},
    )
    obs = next(iter(vapor.observations.values()))
    assert "total_pressure_Pa" not in (obs.point_conditions or {})
    assert obs.value.point == atm_to_pa("1")


def _basis_sample(raw: dict):
    experiment, bench, observation = case()
    experiment = replace(
        experiment,
        sample=_sample_from_plain({
            "printed_composition": {
                "state": {"tag": "value", "value": raw},
                "locator": {"table": "review-composition"},
            }
        }),
    )
    return experiment, bench, observation


def test_amount_basis_is_enforced_before_mass_conversion() -> None:
    """Reviewer probe: mole_fraction must not be read as wt%.

    mass_percent still gets the 1 wt% omission. mole_fraction is moles.
    A non-oxide above zero in mole terms refuses. Unknown bases refuse.
    """

    oxides = {"amount_basis": "mole_fraction", "components": [["SiO2", "0.6"], ["MgO", "0.4"]]}
    experiment, _bench_unused, _observation = _basis_sample(oxides)
    selected = normalized_composition(experiment, _bench()).selected
    assert selected is not None
    assert selected.value["SiO2"] == Decimal("0.6")
    assert selected.value["MgO"] == Decimal("0.4")
    assert selected.notice is None or "wt%" not in selected.notice

    chlorine = {
        "amount_basis": "mole_fraction",
        "components": [["SiO2", "0.5"], ["MgO", "0.25"], ["Cl", "0.25"]],
    }
    experiment, bench, observation = _basis_sample(chlorine)
    refused = normalized_composition(experiment, _bench())
    assert refused.selected is None
    assert refused.absence is not None
    assert refused.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    requests = engine_point_requests(collect_consumer_inputs(experiment, bench, observation))
    assert len(requests) == 8
    assert all(item.payload is None for item in requests)

    for basis in ("mol_inventory", "not_printed"):
        experiment, bench, observation = _basis_sample({
            "amount_basis": basis,
            "components": [["SiO2", "0.5"], ["MgO", "0.25"], ["Cl", "0.25"]],
        })
        unknown = normalized_composition(experiment, _bench())
        assert unknown.selected is None
        assert unknown.absence is not None
        assert unknown.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
        requests = engine_point_requests(collect_consumer_inputs(experiment, bench, observation))
        assert all(item.payload is None for item in requests)

    bare = {"components": [["SiO2", "50"], ["MgO", "50"]]}
    missing = normalized_composition(_basis_sample(bare)[0], _bench())
    assert missing.selected is None
    assert missing.absence is not None
    assert missing.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM

    mass = {
        "amount_basis": "mass_percent",
        "components": [["SiO2", "60"], ["MgO", "39.75"], ["Cl", "0.25"]],
    }
    experiment, bench, observation = _basis_sample(mass)
    selected = normalized_composition(experiment, _bench()).selected
    assert selected is not None
    assert set(selected.value) == {"SiO2", "MgO"}
    assert selected.notice is not None and "Cl 0.25 wt%" in selected.notice
    requests = engine_point_requests(collect_consumer_inputs(experiment, bench, observation))
    assert all(item.payload is not None for item in requests)


def test_exploded_rows_inherit_parent_temperature_provenance(tmp_path: Path) -> None:
    _, result = _migrate_obs(
        tmp_path,
        {
            "T_C": 1300,
            "rows": [
                {"pressure_atm": 1, "run": "inherited"},
                {"T_K": 1800, "pressure_atm": 2, "run": "printed"},
            ],
        },
    )
    by_run = {obs.observation_id: obs for obs in result.observations.values()}
    inherited = next(obs for oid, obs in by_run.items() if "row=inherited" in oid)
    printed = next(obs for oid, obs in by_run.items() if "row=printed" in oid)
    temperature = inherited.point_conditions["temperature_K"]
    assert temperature.state.value == Decimal("1573.15")
    assert temperature.inference is not None
    assert temperature.inference.relation == "celsius_to_kelvin"
    originals = [
        located.state.value
        for name, located in temperature.inference.parameters
        if name == "original"
    ]
    assert originals == [Decimal("1300")]
    override = printed.point_conditions["temperature_K"]
    assert override.state.value == Decimal("1800")
    assert override.inference is None or override.inference.relation != "celsius_to_kelvin"
    assert "fixture-source::na_psat" not in result.observations


def test_kambayashi_table3_children_keep_1573_K() -> None:
    """The 1300 C table loses its waypoint when rows replace the parent."""

    path = (
        Path(__file__).resolve().parents[2]
        / "data/literature/extracts/kems-057-kambayashi-1985.yaml"
    )
    migrator = Migrator()
    migrator._migrate_extract(path)
    stem = "kambayashi_1985_pbo_table3_ion_current_ratios_1300c"
    children = [
        obs
        for oid, obs in migrator.result.observations.items()
        if stem in oid and oid.rsplit("::", 1)[-1] != stem
    ]
    assert len(children) == 3
    assert all("::point:" not in obs.observation_id for obs in children)
    for obs in children:
        located = obs.point_conditions["temperature_K"]
        assert located.state.value == Decimal("1573")
        experiment = migrator.result.experiments[obs.experiment_id]
        bench = migrator.result.benches.get(experiment.bench_id)
        if bench is None:
            from scripts.bench_readiness import _implicit_bench

            bench = _implicit_bench(
                experiment, migrator.result.works.get(experiment.work_id)
            )
        assert bench is not None
        selected = collect_consumer_inputs(experiment, bench, obs).waypoints["temperature_K"].selected
        assert selected is not None
        assert selected.value.point == Decimal("1573")


def test_no_temperature_ids_follow_the_printed_row(tmp_path: Path) -> None:
    source_path = (
        Path(__file__).resolve().parents[2]
        / "data/literature/extracts/usgs-lunar-sourcebook-tab8-2.yaml"
    )
    import yaml

    source = yaml.safe_load(source_path.read_text())
    source["species"] = {"Li": source["species"]["Li"]}
    rows = source["species"]["Li"]["observations"][0]["values"]["rows"]
    source["species"]["Li"]["observations"][0]["values"]["rows"] = rows[:2]

    def located(reverse: bool) -> dict:
        extract = deepcopy(source)
        if reverse:
            extract["species"]["Li"]["observations"][0]["values"]["rows"].reverse()
        result = migrate(_write_min_tree(tmp_path / ("rev" if reverse else "fwd"), extract), write=False)
        assert all("::point:" not in oid for oid in result.observations)
        # `row` is not a Locator field; it is kept on the note.
        return {key: obs.locator.note for key, obs in result.observations.items()}

    forward = located(False)
    backward = located(True)
    assert forward == backward
    assert len(forward) == 2
    assert any("Apollo 11 MBAS Average" in (note or "") for note in forward.values())
    assert any("Apollo 12 MBAS Average" in (note or "") for note in forward.values())


def test_flemetakis_list_rows_are_not_collapsed() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "data/literature/extracts/ta-flemetakis-2024.yaml"
    )
    migrator = Migrator()
    migrator._migrate_extract(path)
    # Eight observations, four of them list-row tables (68 printed rows).
    # Replacing each table with one child would leave 8. Each printed list
    # row has to remain its own observation.
    assert len(migrator.result.observations) == 72
    assert not migrator.result.dedupe_aliases


def test_list_rows_keep_distinct_printed_payloads(tmp_path: Path) -> None:
    parent = _row("parent", "measured_direct", relation=None)
    parent["values"].pop("pressure_atm")
    parent["values"]["rows"] = [
        ["fO2", "B1a", 1245, 8.43],
        ["time", "B1a", 1245, 8.43],
    ]
    result = _migrate(tmp_path, [parent])
    assert len(result.observations) == 2
    assert len(result.dedupe_aliases) == 0
    assert all("::point:" not in oid for oid in result.observations)


def test_rows_and_points_without_temperature_do_not_merge(tmp_path: Path) -> None:
    parent = _row("parent", "measured_direct", relation=None)
    parent["values"].pop("pressure_atm")
    parent["values"].update({
        "rows": [{"pressure_atm": 1, "run": "a"}],
        "points": [{"pressure_atm": 1, "run": "b"}],
    })
    result = _migrate(tmp_path, [parent])
    assert len(result.observations) == 2
    assert not result.dedupe_aliases
    assert sorted(obs.value.point for obs in result.observations.values()) == [
        atm_to_pa("1"),
        atm_to_pa("1"),
    ]
    assert all("::point:" not in oid for oid in result.observations)
    rows_id = next(oid for oid in result.observations if "::rows:" in oid)
    points_id = next(oid for oid in result.observations if "::points:" in oid)
    assert rows_id != points_id


def test_author_lineage_retargets_before_admission_closure(tmp_path: Path) -> None:
    for shape in ("rows", "points"):
        parent = _row("parent", "measured_direct", relation=None)
        parent["values"][shape] = [
            {"T_K": 1200, "pressure_atm": 1, "run": "a"},
            {"T_K": 1300, "pressure_atm": 2, "run": "b"},
        ]
        result = _migrate(tmp_path / shape, [parent, _row("child", parents=["parent"])])
        child = _child(result)
        children = [
            oid for oid in result.observations if oid.startswith("fixture-source::parent::")
        ]
        assert len(children) == 2
        assert child.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        assert set(child.derived_from) == set(children)
        assert set(child.derivation.inputs) == set(children)
        assert "fixture-source::parent" not in child.derived_from
        assert "fixture-source::parent" not in child.derivation.inputs
        dangling = [
            issue for issue in result.validation.hard_issues
            if str(issue.reason.value if hasattr(issue.reason, "value") else issue.reason)
            == "referential_integrity"
        ]
        assert not dangling


def test_lineage_retarget_does_not_prefix_match(tmp_path: Path) -> None:
    result = _migrate(tmp_path, [
        _row("parent::unrelated", "measured_direct", relation=None),
        _row("child", "measured_direct", ["fixture-source::parent"], relation=None),
    ])
    child = _child(result)
    assert child.derived_from == ("fixture-source::parent",)
    assert all("unrelated" not in item for item in child.derived_from)
    dangling = [
        issue for issue in result.validation.hard_issues
        if str(issue.reason.value if hasattr(issue.reason, "value") else issue.reason)
        == "referential_integrity"
    ]
    assert dangling


def test_non_oxide_total_at_one_weight_percent_is_omitted() -> None:
    raw = {"SiO2": "60", "MgO": "39", "F": "1.0"}
    experiment, _bench_unused, _observation = _printed_experiment(raw)
    selected = normalized_composition(experiment, _bench()).selected
    assert selected is not None
    assert set(selected.value) == {"SiO2", "MgO"}
    assert selected.notice is not None
    assert "F 1.0 wt%" in selected.notice
    over = {"SiO2": "60", "MgO": "38.99", "S": "0.6", "Cl": "0.41"}
    refused = normalized_composition(_printed_experiment(over)[0], _bench())
    assert refused.selected is None
    assert refused.absence is not None
    assert refused.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
