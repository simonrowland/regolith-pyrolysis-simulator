from __future__ import annotations

import copy
import hashlib
from dataclasses import replace
from decimal import Decimal
import pytest
import yaml

from simulator.battery.enums import BenchIdentityBasis, RefusalReason, ValueKind
from scripts.bench_readiness import report
from simulator.battery.migrate import (
    Migrator,
    load_migrated_benches,
    to_plain,
    write_outputs,
)
from simulator.battery.records import (
    ApparatusGeometry,
    Bench,
    BenchIdentity,
    BenchReference,
    Located,
    Locator,
    State,
    ThermalSchedule,
    Value,
)
from tests.battery import factories
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree
from tests.battery.test_waypoints import _charge, _schedule, _knudsen_case


def _registry_extract() -> dict:
    extract = copy.deepcopy(FIXTURE_EXTRACT)
    cited_locator = Locator(page=7, section="apparatus")
    bench = Bench(
        id="bench-one",
        work_id="placeholder",
        identity=BenchIdentity(
            BenchIdentityBasis.CITED_BY_AUTHOR,
            BenchReference(
                None,
                "Smith (1980)",
                ("orifice_diameter_m",),
                Locator(page=3),
            ),
        ),
        geometry=ApparatusGeometry(
            orifice_diameter_m=Located(
                State.of(
                    Value(
                        ValueKind.INTERVAL,
                        interval_low=Decimal("0.0009"),
                        interval_high=Decimal("0.0011"),
                    )
                ),
                locator=cited_locator,
            )
        ),
        pumping_speed_m3_s=Located(
            State.of(
                Value(
                    ValueKind.BOUND,
                    bound_operator=">",
                    bound_value=Decimal("0.01"),
                    approximate=True,
                )
            ),
            locator=cited_locator,
        ),
    )
    experiment = replace(
        factories.kems_experiment(
            experiment_id="run-one", work_id="placeholder"
        ),
        bench_id="bench-one",
        thermal_schedule=ThermalSchedule(),
    )
    extract["benches"] = [to_plain(bench)]
    extract["experiments"] = [to_plain(experiment)]
    extract["species"]["Na"]["observations"][0]["experiment"] = "run-one"
    return extract


def test_extract_registries_lift_and_preserve_print_form(tmp_path) -> None:
    root = _write_min_tree(tmp_path, _registry_extract())
    result = Migrator(root=root).run()
    assert result.validation is not None and result.validation.ok, result.validation.hard_issues
    bench = next(iter(result.benches.values()))
    assert bench.id.endswith("::bench::bench-one")
    assert bench.identity.ref is not None
    assert bench.identity.ref.cited_as == "Smith (1980)"
    assert bench.geometry is not None and bench.geometry.orifice_diameter_m is not None
    assert bench.geometry.orifice_diameter_m.locator == Locator(
        page=7, section="apparatus"
    )
    assert bench.geometry.orifice_diameter_m.state.value.kind is ValueKind.INTERVAL
    assert bench.pumping_speed_m3_s is not None
    assert bench.pumping_speed_m3_s.state.value.kind is ValueKind.BOUND
    assert bench.pumping_speed_m3_s.state.value.approximate is True
    experiment = next(iter(result.experiments.values()))
    observation = next(iter(result.observations.values()))
    assert experiment.bench_id == bench.id
    assert observation.experiment_id == experiment.experiment_id
    write_outputs(result, root)
    assert load_migrated_benches(root) == {bench.id: bench}
    readiness = report(root)
    assert readiness["source_count"] == 1
    assert readiness["sources"][0]["experiments"][0]["bench_id"] == bench.id


def test_observation_missing_declared_experiment_is_hard_issue(tmp_path) -> None:
    extract = _registry_extract()
    extract["species"]["Na"]["observations"][0]["experiment"] = "missing"
    result = Migrator(root=_write_min_tree(tmp_path, extract)).run()
    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        and issue.path.endswith(".experiment_id")
        for issue in result.validation.hard_issues
    )


def test_experiment_missing_bench_is_hard_issue(tmp_path) -> None:
    extract = _registry_extract()
    extract["experiments"][0]["bench_id"] = "missing"
    root = _write_min_tree(tmp_path, extract)
    result = Migrator(root=root).run()
    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        and issue.path.endswith(".bench_id")
        for issue in result.validation.hard_issues
    )
    write_outputs(result, root)
    readiness = report(root)
    assert readiness["source_count"] == 1
    assert all(
        item["status"] == "gap"
        for item in readiness["sources"][0]["consumers"]
    )


def test_registry_observation_keeps_legacy_equipment_lift(tmp_path) -> None:
    extract = _registry_extract()
    extract["experiments"][0]["sample"]["surface_area_m2"] = to_plain(
        factories.located(Value.point_of("0.25"))
    )
    extract["experiments"][0]["sample"]["characterization"] = to_plain(
        factories.located("characterized powder")
    )
    row = extract["species"]["Na"]["observations"][0]
    row["equipment"] = {
        "cell_material": "UNIQUE-IRIDIUM",
        "sample_mass_mg": 12345,
    }
    result = Migrator(root=_write_min_tree(tmp_path, extract)).run()
    experiment = next(iter(result.experiments.values()))
    assert experiment.apparatus is not None
    assert experiment.apparatus.cell_material_and_liner is not None
    assert (
        experiment.apparatus.cell_material_and_liner.state.value
        == "UNIQUE-IRIDIUM"
    )
    assert experiment.sample.mass_kg is not None
    assert experiment.sample.mass_kg.state.value.point == Decimal("0.012345")
    assert experiment.sample.surface_area_m2 is not None
    assert experiment.sample.surface_area_m2.state.value.point == Decimal("0.25")
    assert experiment.sample.characterization is not None
    assert experiment.sample.characterization.state.value == "characterized powder"


def test_conflicting_duplicate_registry_ids_are_hard_issues(tmp_path) -> None:
    extract = _registry_extract()
    duplicate = copy.deepcopy(extract["benches"][0])
    duplicate["geometry"]["orifice_diameter_m"]["state"]["value"] = {
        "kind": "point",
        "point": "9",
    }
    extract["benches"].append(duplicate)
    result = Migrator(root=_write_min_tree(tmp_path, extract)).run()
    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        and issue.path.startswith("bench[")
        and "duplicate" in issue.detail
        for issue in result.validation.hard_issues
    )


def test_readiness_counts_unique_sources_not_experiments(tmp_path) -> None:
    extract = _registry_extract()
    second = copy.deepcopy(extract["experiments"][0])
    second["experiment_id"] = "run-two"
    extract["experiments"].append(second)
    root = _write_min_tree(tmp_path, extract)
    result = Migrator(root=root).run()
    write_outputs(result, root)
    readiness = report(root)
    assert readiness["source_count"] == 1
    assert len(readiness["sources"][0]["experiments"]) == 2
    assert all(
        sum(counts.values()) == 1
        for counts in readiness["summary"]["by_consumer"].values()
    )
    assert all(
        sum(counts.values()) == 1
        for counts in readiness["summary"]["by_engine"].values()
    )
    expected_statuses = {"ready", "partial", "gap", "not_applicable"}
    assert all(
        set(counts) == expected_statuses
        for counts in readiness["summary"]["by_consumer"].values()
    )
    assert all(
        set(counts) == expected_statuses
        for counts in readiness["summary"]["by_engine"].values()
    )
    for consumer in readiness["sources"][0]["consumers"]:
        for gap in consumer["gaps"]:
            assert gap["count"] == len(gap["experiment_ids"])
            assert 1 <= gap["count"] <= 2
            assert set(gap["experiment_ids"]) <= set(
                experiment["experiment_id"]
                for experiment in readiness["sources"][0]["experiments"]
            )


def _aggregation_readiness(experiment, bench, observation=None):
    from simulator.battery.waypoints import ConsumerReadiness, ReadinessGap, ReadinessStatus, GapReason, ENGINE_POINT_CONSUMERS
    gaps = []
    if experiment.sample.mass_kg is None:
        gaps.append(ReadinessGap("charge_moles_by_species", GapReason.MISSING_EVIDENCE))
    if experiment.sample.surface_area_m2 is None:
        gaps.append(ReadinessGap("surfaces", GapReason.MISSING_EVIDENCE))
    return tuple(ConsumerReadiness(consumer, ReadinessStatus.GAP if gaps else ReadinessStatus.READY,
        tuple(gaps), engine) for consumer, engine in (("kems", None), ("rps", None),
            *(("engine_point", engine) for engine in ENGINE_POINT_CONSUMERS)))


def test_mixed_source_retains_ready_experiment_and_ranks_unique_blockers(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("scripts.bench_readiness.consumer_readiness", _aggregation_readiness)
    extract = _registry_extract()
    ready = replace(factories.kems_experiment(experiment_id="ready", total_P=Decimal("0.1")),
                    bench_id="bench-one", sample=_charge(single=False), thermal_schedule=_schedule())
    extract["benches"][0]["geometry"]["clausing_factor"] = to_plain(factories.located(Value.point_of("0.5")))
    extract["experiments"] = [to_plain(ready)] + [
        to_plain(replace(ready, experiment_id=f"gap-{i:02}", sample=replace(ready.sample, mass_kg=None)))
        for i in range(49)
    ]
    extract["species"]["Na"]["observations"][0]["experiment"] = "ready"
    root = _write_min_tree(tmp_path, extract)
    write_outputs(Migrator(root=root).run(), root)
    result = report(root)
    source = result["sources"][0]
    from simulator.battery.waypoints import ENGINE_POINT_CONSUMERS
    assert set(result["summary"]["by_engine"]) == set(ENGINE_POINT_CONSUMERS)
    assert set(result["summary"]["by_consumer"]) == {"kems", "rps", "engine_point"}
    for row in source["consumers"] + source["engines"]:
        assert row["status"] == "partial"
        charge = next(gap for gap in row["gaps"] if gap["waypoint"] == "charge_moles_by_species")
        assert charge["count"] == 49
        assert len(set(charge["experiment_ids"])) == 49
    for counts in list(result["summary"]["by_consumer"].values()) + list(result["summary"]["by_engine"].values()):
        assert counts == {"ready": 0, "partial": 1, "gap": 0, "not_applicable": 0}
    ready_row = next(row for row in source["experiments"] if row["experiment_id"].endswith("ready"))
    assert all(row["status"] == "ready" for row in ready_row["consumers"] + ready_row["engines"])
    assert result["summary"]["top_blocking_waypoints"]
    assert result["summary"]["top_blocking_waypoints"][0] == {
        "waypoint": "charge_moles_by_species", "reason": "missing_evidence",
        "source_count": 1, "experiment_count": 49,
    }


def test_blockers_rank_source_count_before_experiment_count(tmp_path, monkeypatch) -> None:
    import scripts.bench_readiness as module
    monkeypatch.setattr(module, "consumer_readiness", _aggregation_readiness)
    experiment, bench = _knudsen_case(Value.point_of("1"))
    work_a = replace(factories.work("a"), source_ids=("a",))
    work_b = replace(factories.work("b"), source_ids=("b",))
    experiment = replace(experiment, bench_id=bench.id)
    experiments = {
        f"a-{i}": replace(experiment, experiment_id=f"a-{i}", work_id="a",
            sample=replace(experiment.sample, mass_kg=None,
                           surface_area_m2=None if i == 0 else experiment.sample.surface_area_m2))
        for i in range(9)
    }
    experiments["b"] = replace(experiment, experiment_id="b", work_id="b",
        sample=replace(experiment.sample, surface_area_m2=None))
    monkeypatch.setattr(module, "load_migrated_store", lambda root: ({"a": work_a, "b": work_b}, experiments, {}))
    monkeypatch.setattr(module, "load_migrated_benches", lambda root: {bench.id: bench})
    blockers = module.report(tmp_path)["summary"]["top_blocking_waypoints"]
    assert [(row["waypoint"], row["source_count"], row["experiment_count"]) for row in blockers] == [
        ("surfaces", 2, 2), ("charge_moles_by_species", 1, 9),
    ]


def test_duplicate_gap_is_counted_once_per_experiment() -> None:
    from scripts.bench_readiness import _deduplicated_gaps
    from simulator.battery.waypoints import ConsumerReadiness, ReadinessGap, ReadinessStatus, GapReason
    gap = ReadinessGap("charge", GapReason.MISSING_EVIDENCE, ("mass",))
    item = ConsumerReadiness("kems", ReadinessStatus.GAP, (gap, gap))
    assert _deduplicated_gaps([("a", item), ("b", item)]) == [{
        "waypoint": "charge", "reason": "missing_evidence", "missing": ["mass"],
        "count": 2, "experiment_ids": ["a", "b"],
    }]


def test_legacy_embedded_bench_reaches_waypoints_without_explicit_link(tmp_path) -> None:
    extract = _registry_extract()
    extract.pop("benches")
    extract["experiments"][0]["bench_id"] = None
    extract["experiments"][0]["sample"]["mass_kg"] = to_plain(
        factories.located(Value.point_of("0.0001"))
    )
    extract["experiments"][0]["sample"]["printed_composition"] = to_plain(
        factories.located({"SiO2": Decimal("100")})
    )
    root = _write_min_tree(tmp_path, extract)
    result = Migrator(root=root).run()
    write_outputs(result, root)
    readiness = report(root)
    source = readiness["sources"][0]
    experiment = source["experiments"][0]
    assert experiment["informational_gaps"] == [
        {
            "waypoint": "bench_link",
            "reason": "implicit_legacy_bench",
            "missing": ["experiment.bench_id"],
        }
    ]
    assert experiment["bench_identity"] is not None
    assert experiment["bench_identity"]["basis"] == "inferred_from_embedded_evidence"
    assert "experiment.apparatus" in experiment["bench_identity"]["reason"]
    assert source["informational_gaps"][0]["count"] == 1
    assert all(
        gap["waypoint"] != "bench"
        for consumer in experiment["consumers"]
        for gap in consumer["gaps"]
    )
    kems = next(
        item for item in experiment["consumers"] if item["consumer"] == "kems"
    )
    assert all(
        gap["waypoint"] not in {"charge_moles_by_species", "thermal_path"}
        for gap in kems["gaps"]
    )


def test_implicit_bench_external_apparatus_locator_never_claims_own_work() -> None:
    from scripts.bench_readiness import _implicit_bench
    experiment = factories.kems_experiment(work_id="paper-A")
    external = Locator(source_path="paper-B.pdf", page=7, note="Apparatus described in Smith (1980)")
    experiment = replace(experiment, apparatus=replace(experiment.apparatus, geometry=replace(
        experiment.apparatus.geometry, orifice_area_m2=Located(State.of(Value.point_of("1e-6")), locator=external))))
    bench = _implicit_bench(experiment)
    assert bench.identity.basis is BenchIdentityBasis.CITED_BY_AUTHOR
    assert bench.identity.ref.cited_as == "paper-B.pdf"
    assert bench.identity.ref.locator == external
    assert bench.geometry.orifice_area_m2.locator == external


def test_implicit_bench_cited_pumping_locator_is_not_inferred() -> None:
    from scripts.bench_readiness import _implicit_bench
    experiment = factories.kems_experiment()
    external = Locator(source_path="paper-B.pdf", page=7)
    experiment = replace(experiment, pressure_environment=replace(experiment.pressure_environment,
        pumping={"pumping_speed_m3_s": Located(State.of(Value.point_of("0.01")), locator=external)}))
    bench = _implicit_bench(experiment)
    assert bench.identity.basis is BenchIdentityBasis.CITED_BY_AUTHOR
    assert bench.identity.ref.locator == external


def test_extract_cannot_declare_inferred_identity(tmp_path) -> None:
    extract = _registry_extract()
    extract["benches"][0]["identity"] = {
        "basis": "inferred_from_embedded_evidence", "reason": "experiment.apparatus",
    }
    with pytest.raises(ValueError, match="migration-only"):
        Migrator(root=_write_min_tree(tmp_path, extract)).run()


@pytest.mark.parametrize("ledger", [False, True])
def test_explicit_or_ledger_apparatus_reference_wins_over_inferred_identity(tmp_path, ledger, monkeypatch) -> None:
    from scripts.bench_readiness import _apparatus_references, _implicit_bench
    extract = _registry_extract()
    if not ledger:
        extract["apparatus_reference"] = {"cited_as": "Smith (1980)", "locator": {"page": 3}}
    root = _write_min_tree(tmp_path, extract)
    work = replace(factories.work(), source_ids=("fixture-source",))
    if ledger:
        corpus = tmp_path / "corpus"
        (corpus / "ledger").mkdir(parents=True)
        (corpus / "ledger/apparatus-reference-leads.yaml").write_text(yaml.safe_dump({"leads": [{
            "citing": "fixture-source", "lead_as_given": "Smith (1980)",
            "for_parameters": ["orifice diameter"], "reference_list_locator": "page 3",
        }]}))
        work = replace(work, source_files=replace(work.source_files, corpus_repo=str(corpus)))
    refs = _apparatus_references(root, {work.work_id: work})
    bench = _implicit_bench(factories.kems_experiment(), work, refs["fixture-source"])
    assert bench.identity.basis is BenchIdentityBasis.CITED_BY_AUTHOR
    assert bench.identity.ref.cited_as == "Smith (1980)"


def test_ambiguous_cited_apparatuses_do_not_select_a_bench() -> None:
    from scripts.bench_readiness import _implicit_bench
    refs = [BenchReference(None, cited, (), Locator(page=1)) for cited in ("Smith (1980)", "Jones (1981)")]
    assert _implicit_bench(factories.kems_experiment(), references=refs) is None


def test_readiness_json_carries_knudsen_notice_and_inputs(tmp_path) -> None:
    experiment, bench = _knudsen_case(Value.point_of("101325"))
    extract = _registry_extract()
    extract["experiments"] = [to_plain(replace(experiment, experiment_id="run-one", bench_id="bench-one"))]
    extract["benches"] = [to_plain(replace(bench, id="bench-one"))]
    root = _write_min_tree(tmp_path, extract)
    write_outputs(Migrator(root=root).run(), root)
    row = report(root)["sources"][0]["experiments"][0]["consumers"][0]
    assert row["status"] == "gap"
    assert not any(gap["reason"] == "outside_pressure_regime" for gap in row["gaps"])
    assert row["notices"]
    notice, = row["notices"]
    assert notice["kind"] == "knudsen_regime_inconsistency"
    assert Decimal(notice["threshold"]) == 10
    assert Decimal(notice["knudsen_number"]["point"]) < 10
    assert set(notice["inputs"]) == {"d_orifice", "T", "P"}
    assert notice["inputs"]["P"]["locators"][0]["page"] == 7


def test_legacy_equipment_extract_output_has_no_empty_bench_key(tmp_path) -> None:
    extract = copy.deepcopy(FIXTURE_EXTRACT)
    extract["species"]["Na"]["observations"][0]["equipment"] = {
        "cell_material": "platinum",
        "sample_mass_mg": 100,
    }
    root = _write_min_tree(tmp_path, extract)
    result = Migrator(root=root).run()
    write_outputs(result, root)
    work_file = next(
        path
        for path in (root / "data" / "literature" / "works").glob("*.yaml")
        if path.name != "ALIASES.yaml"
    )
    text = work_file.read_text(encoding="utf-8")
    assert "\nbenches:" not in text
    assert result.measured.equipment_payloads == 1
    experiment = next(iter(result.experiments.values()))
    assert experiment.apparatus is not None
    assert experiment.apparatus.cell_material_and_liner is not None
    assert experiment.sample.mass_kg is not None
    digest = hashlib.sha256()
    for path in sorted(
        (item for item in (root / "data").rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root / "data").as_posix(),
    ):
        digest.update(path.relative_to(root / "data").as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    # Recorded by running base 46f1a69b3 over this exact untouched extract.
    assert digest.hexdigest() == (
        "b9544d3f4a80ab2814bf326d966a44df00b8ad83c2b25879a09b0ce48f3e7718"
    )


def test_aliased_work_counts_each_experiment_under_one_canonical_source(tmp_path, monkeypatch) -> None:
    """Work.source_ids lists citation aliases of one work; readiness must
    attribute each experiment to exactly one row (the first-registered
    source id) instead of fanning out one row per alias."""
    import scripts.bench_readiness as module
    monkeypatch.setattr(module, "consumer_readiness", _aggregation_readiness)
    experiment, bench = _knudsen_case(Value.point_of("1"))
    experiment = replace(experiment, bench_id=bench.id)
    work = replace(
        factories.work("janaf-work"), source_ids=("janaf-4th", "nist-janaf-4th")
    )
    experiments = {
        f"exp-{i}": replace(experiment, experiment_id=f"exp-{i}", work_id="janaf-work")
        for i in range(3)
    }
    monkeypatch.setattr(
        module,
        "load_migrated_store",
        lambda root: ({"janaf-work": work}, experiments, {}),
    )
    monkeypatch.setattr(module, "load_migrated_benches", lambda root: {bench.id: bench})
    readiness = module.report(tmp_path)
    assert readiness["source_count"] == 1
    row = readiness["sources"][0]
    assert row["source_id"] == "janaf-4th"
    assert sorted(e["experiment_id"] for e in row["experiments"]) == [
        "exp-0",
        "exp-1",
        "exp-2",
    ]
    for counts in readiness["summary"]["by_consumer"].values():
        assert sum(counts.values()) == 1
    for counts in readiness["summary"]["by_engine"].values():
        assert sum(counts.values()) == 1


def test_work_with_no_experiments_is_listed_as_unscoreable_gap(tmp_path, monkeypatch) -> None:
    """A work whose observations moved into context[] has no experiments.

    It must stay in the report under its canonical source id, as a gap for
    no scoreable observations, and must not change ready or partial counts.
    """
    import scripts.bench_readiness as module

    monkeypatch.setattr(module, "consumer_readiness", _aggregation_readiness)
    experiment, bench = _knudsen_case(Value.point_of("1"))
    experiment = replace(experiment, bench_id=bench.id, work_id="scored-work")
    scored = replace(factories.work("scored-work"), source_ids=("scored-source",))
    context_only = replace(
        factories.work("context-only"),
        source_ids=("context-only-source", "context-only-alias"),
    )

    def install(works):
        monkeypatch.setattr(
            module,
            "load_migrated_store",
            lambda root: (works, {experiment.experiment_id: experiment}, {}),
        )
        monkeypatch.setattr(module, "load_migrated_benches", lambda root: {bench.id: bench})

    install({scored.work_id: scored})
    before = module.report(tmp_path)
    install({scored.work_id: scored, context_only.work_id: context_only})
    after = module.report(tmp_path)

    assert before["source_count"] == 1
    assert after["source_count"] == 2
    by_id = {row["source_id"]: row for row in after["sources"]}
    assert set(by_id) == {"scored-source", "context-only-source"}
    missing = by_id["context-only-source"]
    assert missing["experiments"] == []
    expected_gap = {
        "waypoint": "observations",
        "reason": "no_scoreable_observations",
        "missing": ["scoreable observations"],
        "count": 0,
        "experiment_ids": [],
    }
    assert expected_gap in missing["informational_gaps"]
    for row in missing["consumers"] + missing["engines"]:
        assert row["status"] == "gap"
        assert expected_gap in row["gaps"]
    for key in ("by_consumer", "by_engine"):
        for name, counts in before["summary"][key].items():
            after_counts = after["summary"][key][name]
            assert after_counts["ready"] == counts["ready"]
            assert after_counts["partial"] == counts["partial"]
            assert after_counts["gap"] == counts["gap"] + 1
    assert any(
        item["waypoint"] == "observations"
        and item["reason"] == "no_scoreable_observations"
        and item["source_count"] == 1
        and item["experiment_count"] == 0
        for item in after["summary"]["top_blocking_waypoints"]
    )
