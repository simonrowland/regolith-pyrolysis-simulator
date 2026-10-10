"""Pin: knudsen_effusion chamber background must not enter observation identity
or engine-point system pressure.

REQ-fix-kems-background-not-identity / review/kems-background-not-identity.

Finding 3: deterministic numeric-point Knudsen fixture (independent of any
extract edit) covering BOTH migrator inheritance sites.
Finding 1: engine-point must not project printed_run_pressure chamber
background into pressure_bar for knudsen_effusion.
Non-effusion controls (langmuir + transpiration) keep experiment→identity
inheritance. Plante scored population is pinned by the existing openimcc tests.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.consumer_inputs import collect_consumer_inputs
from simulator.battery.enums import MethodToken, Quantity, ValueKind
from simulator.battery.generators.bench import (
    engine_point_requests,
    kems_case,
)
from simulator.battery.migrate import migrate
from simulator.battery.records import State, Value
from simulator.battery.waypoints import (
    Waypoint,
    WaypointAuthority,
    WaypointResult,
    _prediction_pressure_waypoint,
    pressure_boundary,
)
from tests.battery.test_bench_generators import case, complete_kems
from tests.battery.test_migrate import _migrate_real_extract
from tests.battery import factories as f
from dataclasses import replace

REPO_ROOT = Path(__file__).resolve().parents[2]

# Chamber background used by the deterministic fixture and the engine-point pin.
_CHAMBER_PA = Decimal("0.0001")
_CHAMBER_AS_BAR = float(_CHAMBER_PA / Decimal(100000))  # 1e-9


def _knudsen_numeric_fixture() -> dict:
    """Deterministic knudsen_effusion extract with a numeric chamber total.

    Covers both migrator inheritance sites:
    - primary ``_experiment_identity_fields`` via a p_partial row
    - residue identity fill via residue_composition_vs_time cells
    """

    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-knudsen-bg",
        "source": {
            "citation": "Fixture, A. (2026), Knudsen background pin, DOI 10.1234/KNUDSEN-BG",
            "doi": "10.1234/KNUDSEN-BG",
        },
        "extraction": {
            "method": "unit_test",
            "date": "2026-10-04",
            "worker": "pytest",
        },
        "review_status": "draft",
        "benches": [
            {
                "id": "cell-1",
                "identity": {"basis": "described_in_this_work"},
            }
        ],
        "experiments": [
            {
                "experiment_id": "effusion-run",
                "bench_id": "cell-1",
                "method": "knudsen_effusion",
                "locator": {"page": 1},
                "sample": {
                    "printed_composition": {
                        "state": {
                            "tag": "value",
                            "value": {"MgO": 50, "SiO2": 50},
                        },
                        "locator": {"page": 1},
                    },
                    "mass_kg": {
                        "state": {
                            "tag": "value",
                            "value": {"kind": "point", "point": "0.0001"},
                        },
                        "locator": {"page": 1},
                    },
                },
                "pressure_environment": {
                    "total_pressure_Pa": {
                        "state": {
                            "tag": "value",
                            "value": {
                                "kind": "point",
                                "point": str(_CHAMBER_PA),
                            },
                        },
                        "locator": {
                            "page": 1,
                            "note": "chamber background for pin",
                        },
                    },
                    "sweep_gas": {
                        "state": {
                            "tag": "not_applicable",
                            "reason": "not_applicable",
                        },
                        "locator": {"page": 1},
                    },
                    "regime": {
                        "regime_class": {
                            "tag": "unknown",
                            "reason": "not_published",
                        }
                    },
                },
            }
        ],
        "species": {
            "Mg": {
                "observations": [
                    {
                        "observation_id": "partial_row",
                        "experiment": "effusion-run",
                        "type": "rate_series",
                        "locator": {"page": 1},
                        "phase": "gas",
                        "regime": "knudsen_effusion",
                        "units": "Pa",
                        "values": {
                            "quantity": "p_partial",
                            "method_class": "measured_direct",
                            "admission_status": "admitted",
                            "series": [
                                {"T_K": 1500.0, "pressure_Pa": 1.0},
                            ],
                        },
                    },
                    {
                        "observation_id": "residue_row",
                        "experiment": "effusion-run",
                        "type": "rate_series",
                        "locator": {"page": 2},
                        "phase": "liquid",
                        "regime": "knudsen_effusion",
                        "units": "wt % (100% normalized) as published",
                        "values": {
                            "quantity": "residue_composition_vs_time",
                            "method_class": "measured_direct",
                            "admission_status": "admitted",
                            "series": [
                                {
                                    "T_K": 1500.0,
                                    "t_min": 10.0,
                                    "run_id": "effusion-run",
                                    "MgO_wt_pct": 40.0,
                                    "SiO2_wt_pct": 60.0,
                                }
                            ],
                        },
                    },
                ]
            }
        },
    }


def _migrate_fixture(tmp_path: Path, extract: dict):
    root = tmp_path / "tree"
    extracts = root / "data" / "literature" / "extracts"
    extracts.mkdir(parents=True)
    (root / "data" / "literature" / "compilations").mkdir(parents=True)
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "sources": [
                    {
                        "source_id": extract["source_id"],
                        "citation": extract["source"]["citation"],
                        "doi": extract["source"]["doi"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (extracts / f"{extract['source_id']}.yaml").write_text(
        yaml.safe_dump(extract, sort_keys=False),
        encoding="utf-8",
    )
    return migrate(root, write=False)


def _assert_identity_not_chamber(observation, chamber: Decimal = _CHAMBER_PA) -> None:
    identity_pressure = observation.identity.total_pressure_Pa
    if identity_pressure is not None and identity_pressure.is_value:
        value = identity_pressure.value
        if isinstance(value, Value) and value.kind is ValueKind.POINT:
            assert value.point != chamber, (
                f"{observation.observation_id} inherited chamber background "
                f"{chamber} Pa as identity.total_pressure_Pa"
            )
        else:
            assert value != chamber, (
                f"{observation.observation_id} inherited chamber background "
                f"{chamber} Pa as identity.total_pressure_Pa"
            )


def test_prediction_pressure_selection_prefers_first_non_chamber_route() -> None:
    """Prediction pressure preserves the order of eligible alternatives."""

    experiment, bench, observation = case(pressure=str(_CHAMBER_PA))
    chamber = Waypoint(
        "pressure_boundary", Value.point_of(_CHAMBER_PA), "printed_run_pressure",
        WaypointAuthority.PRINTED, ("experiment.pressure_environment.total_pressure_Pa",),
    )
    first = Waypoint(
        "pressure_boundary", Value.point_of(1), "first_eligible", WaypointAuthority.DERIVED, (),
    )
    second = Waypoint(
        "pressure_boundary", Value.point_of(2), "second_eligible", WaypointAuthority.DERIVED, (),
    )
    result = WaypointResult("pressure_boundary", chamber, (chamber, first, second))
    assert _prediction_pressure_waypoint(result, experiment.method) == first


def test_knudsen_effusion_numeric_fixture_not_inherited_at_both_sites(
    tmp_path: Path,
) -> None:
    """Deterministic pin: both migrator inheritance sites refuse chamber fill."""

    result = _migrate_fixture(tmp_path, _knudsen_numeric_fixture())
    experiment = next(iter(result.experiments.values()))
    assert experiment.method.is_value
    assert experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    total = experiment.pressure_environment.total_pressure_Pa
    assert total.state.is_value
    value = total.state.value
    assert isinstance(value, Value) and value.kind is ValueKind.POINT
    assert value.point == _CHAMBER_PA

    partial_rows = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.P_PARTIAL
    ]
    residue_rows = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value
        is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert partial_rows, "expected at least one p_partial row (primary inheritance site)"
    assert residue_rows, "expected residue rows (second inheritance site)"

    for observation in partial_rows:
        _assert_identity_not_chamber(observation)
        identity_pressure = observation.identity.total_pressure_Pa
        assert identity_pressure is None or identity_pressure.is_unknown, (
            f"{observation.observation_id} primary site wrongly valued: "
            f"{identity_pressure!r}"
        )

    for observation in residue_rows:
        _assert_identity_not_chamber(observation)
        identity_pressure = observation.identity.total_pressure_Pa
        assert identity_pressure is None or identity_pressure.is_unknown, (
            f"{observation.observation_id} residue site wrongly valued: "
            f"{identity_pressure!r}"
        )
        # Residue point_conditions must also not carry chamber background
        # (feeds pressure_boundary → engine-point if left ungated).
        located = (observation.point_conditions or {}).get("total_pressure_Pa")
        if located is not None and located.state.is_value:
            pc_value = located.state.value
            if isinstance(pc_value, Value) and pc_value.kind is ValueKind.POINT:
                assert pc_value.point != _CHAMBER_PA, (
                    f"{observation.observation_id} residue point_conditions "
                    f"copied chamber background {_CHAMBER_PA} Pa"
                )
            else:
                assert pc_value != _CHAMBER_PA, (
                    f"{observation.observation_id} residue point_conditions "
                    f"copied chamber background {_CHAMBER_PA} Pa"
                )


def test_knudsen_engine_point_does_not_use_chamber_as_pressure_bar() -> None:
    """Finding 1 pin: engine-point pressure_bar must not be chamber background.

    Uses a deterministic kems_experiment fixture (not Halwax). On the pre-fix
    tip this fails because printed_run_pressure (0.0001 Pa) becomes 1e-9 bar.
    Exterior-chamber / kems consumers must still see the chamber pressure.
    """

    from tests.battery.test_bench_generators import _clear_sample_pressure

    experiment, bench, observation = case(pressure=str(_CHAMBER_PA))
    observation = _clear_sample_pressure(observation)
    assert experiment.method.is_value
    assert experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    assert "total_pressure_Pa" not in (observation.point_conditions or {})

    # Waypoint still exposes chamber for exterior consumers.
    boundary = pressure_boundary(experiment, bench, observation)
    assert boundary.selected is not None
    assert boundary.selected.route == "printed_run_pressure"
    assert boundary.selected.value.point == _CHAMBER_PA

    inputs = collect_consumer_inputs(experiment, bench, observation)
    results = list(engine_point_requests(inputs))
    # Pre-fix tip emits ready payloads with pressure_bar == chamber-as-bar.
    # After the fix those payloads are absent (GAP) or carry a non-chamber
    # pressure. Either is acceptable; chamber-as-bar is not.
    chamber_hits = [
        result.payload
        for result in results
        if result.payload is not None
        and result.payload.get("pressure_bar") == _CHAMBER_AS_BAR
    ]
    assert not chamber_hits, (
        f"engine-point payload carried knudsen chamber background as "
        f"pressure_bar={_CHAMBER_AS_BAR!r} (from experiment total "
        f"{_CHAMBER_PA} Pa); must be absent or a non-chamber source "
        f"(got {len(chamber_hits)} chamber hits of {len(results)} results)"
    )

    # Exterior / kems consumer still reads chamber pressure from the waypoint.
    exp_k, bench_k, obs_k = complete_kems()
    exp_k = replace(
        exp_k,
        pressure_environment=replace(
            exp_k.pressure_environment,
            total_pressure_Pa=f.located(_CHAMBER_PA),
        ),
    )
    obs_k = replace(
        obs_k,
        point_conditions={
            key: value
            for key, value in obs_k.point_conditions.items()
            if key != "total_pressure_Pa"
        },
    )
    kems_payload = kems_case(collect_consumer_inputs(exp_k, bench_k, obs_k)).payload
    assert kems_payload is not None
    exterior = kems_payload["exterior_chamber_pressure"]
    assert float(exterior["value_pa"]) == float(_CHAMBER_PA)


def test_knudsen_engine_point_keeps_source_grounded_row_pressure() -> None:
    """Row-level point_conditions pressure still feeds engine-point (Plante path)."""

    experiment, bench, observation = case(pressure=str(_CHAMBER_PA))
    row_pa = Decimal("50")
    observation = replace(
        observation,
        point_conditions={
            **observation.point_conditions,
            "total_pressure_Pa": f.located(row_pa),
        },
    )
    inputs = collect_consumer_inputs(experiment, bench, observation)
    payloads = [result.payload for result in engine_point_requests(inputs) if result.payload]
    assert payloads
    expected_bar = float(row_pa / Decimal(100000))
    for payload in payloads:
        assert payload["pressure_bar"] == expected_bar


def test_langmuir_free_evaporation_still_inherits_experiment_total(
    tmp_path: Path,
) -> None:
    """CORRECT langmuir control (Fedkin 2006): identity inheritance unchanged."""

    result = _migrate_real_extract(tmp_path, "kems-005-fedkin-2006.yaml")
    expected = Decimal("0.001300000")
    inherited = []
    for experiment_id, experiment in result.experiments.items():
        if not (
            experiment.method.is_value
            and experiment.method.value is MethodToken.LANGMUIR_FREE_EVAPORATION
        ):
            continue
        total = experiment.pressure_environment.total_pressure_Pa
        if not total.state.is_value:
            continue
        value = total.state.value
        if not (isinstance(value, Value) and value.kind is ValueKind.POINT):
            continue
        if value.point != expected:
            continue
        for observation in result.observations.values():
            if observation.experiment_id != experiment_id:
                continue
            identity_pressure = observation.identity.total_pressure_Pa
            if (
                identity_pressure is not None
                and identity_pressure.is_value
                and identity_pressure.value == expected
            ):
                inherited.append(observation.observation_id)
                break
    assert inherited, "expected at least one langmuir observation to inherit experiment total"


def test_transpiration_still_inherits_experiment_total(tmp_path: Path) -> None:
    """CORRECT transpiration control (Dacko/Conradt): identity inheritance unchanged."""

    result = _migrate_real_extract(
        tmp_path, "ta-dacko-conradt-low-p-transpiration.yaml"
    )
    expected = Decimal("100000")
    inherited = []
    for experiment_id, experiment in result.experiments.items():
        if not (
            experiment.method.is_value
            and experiment.method.value is MethodToken.TRANSPIRATION
        ):
            continue
        total = experiment.pressure_environment.total_pressure_Pa
        if not total.state.is_value:
            continue
        value = total.state.value
        if not (isinstance(value, Value) and value.kind is ValueKind.POINT):
            continue
        if value.point != expected:
            continue
        for observation in result.observations.values():
            if observation.experiment_id != experiment_id:
                continue
            identity_pressure = observation.identity.total_pressure_Pa
            if (
                identity_pressure is not None
                and identity_pressure.is_value
                and identity_pressure.value == expected
            ):
                inherited.append(observation.observation_id)
                break
    assert inherited, "expected at least one transpiration observation to inherit experiment total"


def test_knudsen_chamber_vacuum_not_commanded_oxygen_for_melt_activity() -> None:
    """P1 pin (r2): Knudsen chamber background must not become commanded sample oxygen.

    Film: FeO–SiO2 liquid activity, no stated sample oxygen, chamber total
    0.0001 Pa with a during-run vacuum locator. Pre-fix tip supplies
    fO2_log=-9 via vacuum_total_pressure_upper_bound → melt-activity payload →
    Po2Request(mode='commanded'). That route must close for closed-token
    knudsen_effusion; chamber evidence stays on the pressure_boundary waypoint.
    """

    from simulator.battery.generators.bench import (
        activity_request_for_engine,
        melt_activity_requests,
    )
    from simulator.battery.waypoints import oxygen_condition
    from tests.battery.test_melt_activity_requirements import _case, _composition

    chamber = _CHAMBER_PA
    experiment, bench, observation = _case(
        composition=_composition(("FeO", "0.2"), ("SiO2", "0.8")),
        formula="SiO2",
    )
    experiment = replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment,
            total_pressure_Pa=f.located(
                Value.point_of(chamber),
                note="chamber pressure during evaporation / printed vacuum during run",
            ),
        ),
    )
    observation = replace(
        observation,
        point_conditions={
            key: value
            for key, value in observation.point_conditions.items()
            if key != "fO2_log"
        },
    )
    assert experiment.method.is_value
    assert experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    assert "fO2_log" not in (observation.point_conditions or {})
    assert "total_pressure_Pa" not in (observation.point_conditions or {})

    # Chamber remains exposed for exterior / validity consumers.
    boundary = pressure_boundary(experiment, bench, observation)
    assert boundary.selected is not None
    assert boundary.selected.route == "printed_run_pressure"
    assert boundary.selected.value.point == chamber

    oxygen = oxygen_condition(experiment, bench, observation)
    vacuum_hits = [
        route
        for route in oxygen.routes
        if route.route == "vacuum_total_pressure_upper_bound"
        and "experiment.pressure_environment.total_pressure_Pa" in route.inputs
    ]
    assert not vacuum_hits, (
        "knudsen chamber background reached vacuum_total_pressure_upper_bound "
        f"(routes={[route.route for route in oxygen.routes]!r})"
    )
    assert oxygen.selected is None or oxygen.selected.route != "vacuum_total_pressure_upper_bound"

    inputs = collect_consumer_inputs(experiment, bench, observation)
    melt = list(melt_activity_requests(inputs))
    commanded = [
        item.payload
        for item in melt
        if item.payload is not None and "fO2_log" in item.payload
    ]
    assert not commanded, (
        "melt-activity payload carried commanded fO2_log from knudsen chamber "
        f"vacuum bound: {[item.get('fO2_log') for item in commanded]!r}"
    )
    openimcc = activity_request_for_engine(experiment, observation, "openimcc")
    assert openimcc is not None
    assert openimcc.payload is None or "fO2_log" not in openimcc.payload


def _assert_supported_melt_activity_payloads(rows, expected_fO2_log: float) -> None:
    """internal-analytical may refuse SiO2. The other three engines may not."""
    by_engine = {}
    for item in rows:
        engine = item.readiness.engine
        assert engine not in by_engine, engine
        by_engine[engine] = item
    assert by_engine["internal-analytical"].payload is None
    for engine in ("alphamelts", "thermoengine", "openimcc"):
        payload = by_engine[engine].payload
        assert payload is not None, engine
        assert payload.get("fO2_log") == expected_fO2_log


def test_non_knudsen_vacuum_bound_and_knudsen_row_oxygen_controls() -> None:
    """Controls: non-Knudsen vacuum bound still works; Knudsen row oxygen preserved."""

    from simulator.battery.generators.bench import melt_activity_requests
    from simulator.battery.waypoints import oxygen_condition
    from tests.battery.test_melt_activity_requirements import _case, _composition

    # Non-Knudsen (langmuir): chamber vacuum upper bound remains a usable oxygen route.
    experiment, bench, observation = _case(
        composition=_composition(("FeO", "0.2"), ("SiO2", "0.8")),
        formula="SiO2",
    )
    experiment = replace(
        experiment,
        method=State.of(MethodToken.LANGMUIR_FREE_EVAPORATION),
        pressure_environment=replace(
            experiment.pressure_environment,
            total_pressure_Pa=f.located(
                Value.point_of(_CHAMBER_PA),
                note="printed vacuum during run",
            ),
        ),
    )
    observation = replace(
        observation,
        point_conditions={
            key: value
            for key, value in observation.point_conditions.items()
            if key != "fO2_log"
        },
    )
    selected = oxygen_condition(experiment, bench, observation).selected
    assert selected is not None
    assert selected.route == "vacuum_total_pressure_upper_bound"
    assert selected.value.point == Decimal("-9")
    melt = melt_activity_requests(collect_consumer_inputs(experiment, bench, observation))
    # 7cd3173c9 added internal-analytical as a melt-activity engine. It
    # refuses SiO2 (not a trace parent) and must not invent an fO2. That
    # exemption is only that engine: AlphaMELTS, ThermoEngine, and OpenIMCC
    # still have to carry the vacuum-bound -9.
    _assert_supported_melt_activity_payloads(melt, -9.0)
    for engine in ("alphamelts", "thermoengine", "openimcc"):
        mutated = tuple(
            replace(item, payload=None)
            if item.readiness.engine == engine
            else item
            for item in melt
        )
        try:
            _assert_supported_melt_activity_payloads(mutated, -9.0)
        except AssertionError:
            continue
        raise AssertionError(f"dropped {engine} payload was accepted")

    # Knudsen with source-grounded printed sample oxygen still commands fO2_log.
    knudsen, bench_k, obs_k = _case(
        composition=_composition(("FeO", "0.2"), ("SiO2", "0.8")),
        oxygen=Decimal("-7"),
        formula="SiO2",
    )
    knudsen = replace(
        knudsen,
        pressure_environment=replace(
            knudsen.pressure_environment,
            total_pressure_Pa=f.located(
                Value.point_of(_CHAMBER_PA),
                note="printed vacuum during run",
            ),
        ),
    )
    selected_k = oxygen_condition(knudsen, bench_k, obs_k).selected
    assert selected_k is not None
    assert selected_k.route == "observation_fO2_log"
    melt_k = melt_activity_requests(collect_consumer_inputs(knudsen, bench_k, obs_k))
    _assert_supported_melt_activity_payloads(melt_k, -7.0)
    for engine in ("alphamelts", "thermoengine", "openimcc"):
        mutated_k = tuple(
            replace(item, payload=None)
            if item.readiness.engine == engine
            else item
            for item in melt_k
        )
        try:
            _assert_supported_melt_activity_payloads(mutated_k, -7.0)
        except AssertionError:
            continue
        raise AssertionError(f"dropped {engine} payload was accepted")
