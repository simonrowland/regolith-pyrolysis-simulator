from __future__ import annotations

import copy
import hashlib
from dataclasses import replace
from decimal import Decimal

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
    assert all(sum(counts.values()) == 1 for counts in readiness["summary"].values())


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
