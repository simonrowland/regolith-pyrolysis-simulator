"""engine_point is not_applicable when a row declares pure_substance_reference.

The generator stamps derivation.pure_substance_reference on pure-substance
engine-reference rows. Readiness selects on that field, together with
composition not_applicable and the engine-reference role stamps. It does not
match a source id, and it does not match the printed-cell accounting clause.
Peer Gibbs tables that share the role stamps but do not declare the field stay
engine_point gaps. Declaring the same field on one of those rows is what would
flip it.
"""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import AssetRole, EvidenceClass, Quantity
from simulator.battery.generators.bench import engine_point_requests
from simulator.battery.generators.janaf import generate_table
from simulator.battery.identity import quantity_token
from simulator.battery.migrate import dump_yaml, to_plain
from simulator.battery.records import (
    Derivation,
    Locator,
    SourceFile,
    SourceFiles,
    State,
    Work,
)
from simulator.battery.waypoints import (
    GapReason,
    ReadinessStatus,
    consumer_readiness,
    is_pure_substance_engine_reference,
    pure_substance_engine_point_gap,
)
from simulator.reference_data.janaf import (
    COMPILATION_ROLE,
    TABLES_DIR,
    load_table_document,
)
from tests.battery import factories

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "bench_readiness_t955", _ROOT / "scripts" / "bench_readiness.py"
)
_bench_readiness = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_bench_readiness)


ROLE_STAMPS = (
    "compilation_role engine_reference_input=true, scoring_eligible=false; "
    "circularity_warning=Do not validate an engine against a compilation it consumes."
)
# Printed-cell accounting is a JANAF generator sentence. It is not a selector.
PROSE_RELATION = f"Direct tabulation; 0 printed cells blank or INFINITE; {ROLE_STAMPS}"
DECLARED_RELATION = f"Direct tabulation; {ROLE_STAMPS}"
TRANSITION_RELATION = (
    'Direct labelled row "CRYSTAL <--> LIQUID"; '
    f"{ROLE_STAMPS}"
)
PEER_USGS_RELATION = f"Direct B1259 tabulation; {ROLE_STAMPS}"


def _pure_substance_observation(
    *,
    relation: str = DECLARED_RELATION,
    composition_na: bool = True,
    declared: bool = True,
    experiment_id: str = "exp-pure-1",
    observation_id: str = "obs-pure-1",
    source_id: str = "some-compilation",
    table: str = "I",
):
    from simulator.battery.enums import AmountBasis
    from simulator.battery.records import Composition

    identity = factories.o2_identity()
    if not composition_na:
        identity = replace(
            identity,
            composition=State.of(
                Composition(
                    basis="oxide",
                    components=(("CaO", Decimal("1")),),
                    amount_basis=AmountBasis.MOLE_FRACTION,
                )
            ),
        )
    observation = factories.observation(
        observation_id,
        experiment_id,
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        derivation=Derivation(
            relation=relation,
            inputs=("unknown:source",),
            parameters=(),
            output_unit="kJ_per_declared_mol_basis",
            pure_substance_reference=True if declared else None,
        ),
        source_id=source_id,
    )
    # Series rows carry no point_conditions, so the live report never passes
    # the observation into consumer_readiness. The store override is the path
    # that types them.
    return replace(
        observation,
        point_conditions=None,
        locator=Locator(table=table, record=table),
    )


def _bench_for(experiment, bench_id: str = "bench-1"):
    from simulator.battery.enums import BenchIdentityBasis
    from simulator.battery.records import Bench, BenchIdentity

    return Bench(
        id=bench_id,
        work_id=experiment.work_id or "work-1",
        identity=BenchIdentity(
            BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE,
            reason="test",
        ),
    )


def _work(work_id: str, source_ids: tuple[str, ...]) -> Work:
    return Work(
        work_id=work_id,
        citation="test compilation",
        source_ids=source_ids,
        source_files=SourceFiles(
            corpus_repo="regolith-corpus",
            corpus_commit=State.unknown("test"),
            files=(
                SourceFile(
                    asset_id=f"tables:{source_ids[0]}",
                    role=AssetRole.TABLE_CSV,
                    path="tables/test",
                    sha256=State.unknown("test"),
                ),
            ),
        ),
    )


def _experiment(experiment_id: str, work_id: str, bench_id: str, table: str):
    return replace(
        factories.tabulation_experiment(experiment_id, work_id),
        bench_id=bench_id,
        locator=Locator(table=table, record=table),
    )


def test_predicate_selects_on_the_declared_field() -> None:
    obs = _pure_substance_observation()
    assert is_pure_substance_engine_reference(obs)

    assert not is_pure_substance_engine_reference(
        _pure_substance_observation(relation="scoring_eligible=false only")
    )
    assert not is_pure_substance_engine_reference(
        _pure_substance_observation(
            relation="compilation_role engine_reference_input=true, scoring_eligible=true"
        )
    )
    assert not is_pure_substance_engine_reference(
        _pure_substance_observation(composition_na=False)
    )
    # Printed-cell clause without the declared field is not a match.
    assert not is_pure_substance_engine_reference(
        _pure_substance_observation(relation=PROSE_RELATION, declared=False)
    )
    # Transition rows have no printed-cell clause. The field covers them.
    assert is_pure_substance_engine_reference(
        _pure_substance_observation(relation=TRANSITION_RELATION, declared=True)
    )
    # Peer Gibbs table: same role stamps, field absent.
    assert not is_pure_substance_engine_reference(
        _pure_substance_observation(relation=PEER_USGS_RELATION, declared=False)
    )
    # Same peer row declaring the field is selected. No source-id branch.
    assert is_pure_substance_engine_reference(
        _pure_substance_observation(relation=PEER_USGS_RELATION, declared=True)
    )
    assert "janaf" not in obs.derivation.relation.lower()
    assert obs.source_id == "some-compilation"


def test_engine_point_not_applicable_with_typed_reason() -> None:
    experiment = factories.tabulation_experiment(experiment_id="exp-pure-1")
    bench = _bench_for(experiment)
    observation = _pure_substance_observation()
    readiness = {
        (item.consumer, item.engine): item
        for item in consumer_readiness(experiment, bench, observation)
    }
    engines = [item for key, item in readiness.items() if key[0] == "engine_point"]
    assert engines
    assert all(item.status is ReadinessStatus.NOT_APPLICABLE for item in engines)
    gap = pure_substance_engine_point_gap()
    for item in engines:
        assert item.gaps == (gap,)
        assert item.gaps[0].reason is GapReason.PURE_SUBSTANCE_REFERENCE
        assert "pure-substance reference table" in item.gaps[0].missing[0]
        assert "pure-phase" in item.gaps[0].missing[0]
    from simulator.battery.consumer_inputs import collect_consumer_inputs

    assert all(
        result.payload is None
        for result in engine_point_requests(
            collect_consumer_inputs(experiment, bench, observation)
        )
    )


def test_without_markers_engine_point_still_gaps_on_missing_composition() -> None:
    experiment = factories.tabulation_experiment(experiment_id="exp-melt-1")
    bench = _bench_for(experiment)
    engines = [
        item for item in consumer_readiness(experiment, bench) if item.consumer == "engine_point"
    ]
    assert engines
    assert all(item.status is ReadinessStatus.GAP for item in engines)
    assert all(
        any(gap.reason is GapReason.MISSING_EVIDENCE for gap in item.gaps)
        for item in engines
    )


def test_mutation_predicate_is_what_drives_not_applicable(monkeypatch) -> None:
    """Revert the predicate → readiness falls back to GAP. Mutation proof."""

    experiment = factories.tabulation_experiment(experiment_id="exp-pure-1")
    bench = _bench_for(experiment)
    observation = _pure_substance_observation()

    import simulator.battery.waypoints as waypoints

    monkeypatch.setattr(waypoints, "is_pure_substance_engine_reference", lambda _obs: False)
    engines = [
        item
        for item in waypoints.consumer_readiness(experiment, bench, observation)
        if item.consumer == "engine_point"
    ]
    assert engines
    assert all(item.status is ReadinessStatus.GAP for item in engines), (
        "mutation: disabling the pure-substance predicate must restore GAP"
    )


def test_generator_declares_marker_on_series_and_transition_rows() -> None:
    assert COMPILATION_ROLE.get("pure_substance_reference") is True
    generated = generate_table(load_table_document(TABLES_DIR / "Al-001.yaml"))
    assert generated.observations
    transitions = []
    series = []
    for observation in generated.observations:
        assert observation.derivation is not None
        assert observation.derivation.pure_substance_reference is True
        assert is_pure_substance_engine_reference(observation)
        if quantity_token(observation.identity) is Quantity.TRANSITION_TEMPERATURE:
            transitions.append(observation)
            assert "printed cells" not in observation.derivation.relation.lower()
        else:
            series.append(observation)
    assert transitions
    assert series


def _write_store(root: Path, bundles) -> None:
    works_dir = root / "data" / "literature" / "works"
    obs_dir = root / "data" / "literature" / "observations-v2"
    extracts = root / "data" / "literature" / "extracts"
    works_dir.mkdir(parents=True)
    obs_dir.mkdir(parents=True)
    extracts.mkdir(parents=True)
    observations = []
    for work, experiments, benches, rows in bundles:
        dump_yaml(
            {
                "schema_version": "battery_work.v2.1",
                "work": to_plain(work),
                "experiments": [to_plain(item) for item in experiments],
                "benches": [to_plain(item) for item in benches],
            },
            works_dir / f"{work.work_id}.yaml",
        )
        observations.extend(rows)
    dump_yaml(
        {
            "schema_version": "battery_observations.v2.1",
            "observations": [to_plain(item) for item in observations],
        },
        obs_dir / "rows.yaml",
    )


def _engines(payload: dict, experiment_id: str) -> list:
    for source in payload["sources"]:
        for experiment in source["experiments"]:
            if experiment["experiment_id"] == experiment_id:
                return experiment["engines"]
    raise AssertionError(experiment_id)


def _assert_not_applicable(payload: dict, experiment_id: str) -> None:
    engines = _engines(payload, experiment_id)
    assert engines
    assert all(item["status"] == ReadinessStatus.NOT_APPLICABLE.value for item in engines)
    assert all(
        any(gap["reason"] == GapReason.PURE_SUBSTANCE_REFERENCE.value for gap in item["gaps"])
        for item in engines
    )


def _assert_gap(payload: dict, experiment_id: str) -> None:
    engines = _engines(payload, experiment_id)
    assert engines
    assert all(item["status"] == ReadinessStatus.GAP.value for item in engines)
    assert all(
        all(gap["reason"] != GapReason.PURE_SUBSTANCE_REFERENCE.value for gap in item["gaps"])
        for item in engines
    )


def test_report_override_types_only_rows_that_declare_the_marker(tmp_path: Path) -> None:
    """The live report calls consumer_readiness without an observation.

    JANAF series rows have no point_conditions, so the waypoints branch never
    sees them. Disabling the store override leaves the declared row a gap.
    """

    janaf_work = _work("work-janaf", ("janaf-4th", "nist-janaf-4th"))
    usgs_work = _work("work-usgs", ("robie-waldbaum-1968-usgs-b1259",))
    janaf_bench = "bench-janaf"
    usgs_bench = "bench-usgs"
    marked_id = "nist-janaf-4th:Al-001:tabulation"
    legacy_id = "work-janaf::Al-001"
    unmarked_id = "work-janaf::melt"
    usgs_id = "robie-waldbaum-1968-usgs-b1259:rec:tabulation"
    usgs_declared_id = "robie-waldbaum-1968-usgs-b1259:declared:tabulation"
    marked = _experiment(marked_id, janaf_work.work_id, janaf_bench, "Al-001")
    legacy = _experiment(legacy_id, janaf_work.work_id, janaf_bench, "Al-001")
    unmarked = _experiment(unmarked_id, janaf_work.work_id, janaf_bench, "melt")
    usgs = _experiment(usgs_id, usgs_work.work_id, usgs_bench, "b1259-rec")
    usgs_declared = _experiment(
        usgs_declared_id, usgs_work.work_id, usgs_bench, "b1259-declared"
    )
    marked_row = _pure_substance_observation(
        relation=DECLARED_RELATION,
        declared=True,
        experiment_id=marked_id,
        observation_id="obs-marked",
        source_id="nist-janaf-4th",
        table="Al-001",
    )
    assert marked_row.point_conditions is None
    assert "printed cells" not in marked_row.derivation.relation.lower()
    legacy_row = _pure_substance_observation(
        relation="legacy extract; no compilation role",
        declared=False,
        experiment_id=legacy_id,
        observation_id="obs-legacy",
        source_id="janaf-4th",
        table="Al-001",
    )
    usgs_row = _pure_substance_observation(
        relation=PEER_USGS_RELATION,
        declared=False,
        experiment_id=usgs_id,
        observation_id="obs-usgs",
        source_id="robie-waldbaum-1968-usgs-b1259",
        table="b1259-rec",
    )
    usgs_declared_row = _pure_substance_observation(
        relation=PEER_USGS_RELATION,
        declared=True,
        experiment_id=usgs_declared_id,
        observation_id="obs-usgs-declared",
        source_id="robie-waldbaum-1968-usgs-b1259",
        table="b1259-declared",
    )
    _write_store(
        tmp_path,
        (
            (
                janaf_work,
                (marked, legacy, unmarked),
                (_bench_for(marked, janaf_bench),),
                (marked_row, legacy_row),
            ),
            (
                usgs_work,
                (usgs, usgs_declared),
                (_bench_for(usgs, usgs_bench),),
                (usgs_row, usgs_declared_row),
            ),
        ),
    )
    payload = _bench_readiness.report(tmp_path)
    _assert_not_applicable(payload, marked_id)
    _assert_gap(payload, legacy_id)
    _assert_gap(payload, unmarked_id)
    _assert_gap(payload, usgs_id)
    _assert_not_applicable(payload, usgs_declared_id)
    for source in payload["sources"]:
        for experiment in source["experiments"]:
            if experiment["experiment_id"] != marked_id:
                continue
            for consumer in experiment["consumers"]:
                if consumer["consumer"] == "engine_point":
                    continue
                assert all(
                    gap["reason"] != GapReason.PURE_SUBSTANCE_REFERENCE.value
                    for gap in consumer["gaps"]
                )
