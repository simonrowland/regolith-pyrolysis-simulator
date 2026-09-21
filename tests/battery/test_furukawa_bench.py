from pathlib import Path

import yaml

from simulator.battery.enums import BenchIdentityBasis, ValueKind
from simulator.battery.migrate import Migrator
from simulator.battery.waypoints import effective_escape_area
from tests.battery.test_migrate import _write_min_tree


def test_furukawa_registry_preserves_unassigned_geometry_and_observations(tmp_path):
    path = Path(__file__).resolve().parents[2] / "data/literature/extracts/kems-119-furukawa-1975.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["source_id"] = "fixture-source"
    result = Migrator(root=_write_min_tree(tmp_path, doc)).run()
    bench, = result.benches.values()
    assert bench.identity.basis is BenchIdentityBasis.DESCRIBED_IN_THIS_WORK
    assert bench.apparatus_family.locator.published_page == 3051
    assert bench.geometry.orifice_diameter_m is None
    assert bench.geometry.orifice_channel_length_m is None
    declared = [e for e in result.experiments.values() if e.bench_id == bench.id]
    assert len(declared) == 3
    for experiment in declared:
        geometry = experiment.apparatus.geometry
        for value in (geometry.orifice_diameter_m, geometry.orifice_channel_length_m):
            assert value.state.is_unknown
            assert value.state.reason == "not_published"
        assert effective_escape_area(experiment, bench).absence is not None
        mass = experiment.sample.mass_kg.state.value
        assert mass.kind is ValueKind.INTERVAL
        assert str(mass.interval_low) == "0.001"
        assert str(mass.interval_high) == "0.003"
        assert experiment.pressure_environment.total_pressure_Pa.state.value.kind is ValueKind.INTERVAL
        duration = experiment.thermal_schedule.total_duration_s.state
        if experiment.experiment_id.endswith('fe-v-solid-scan-series'):
            assert duration.value.kind is ValueKind.POINT
            assert duration.value.approximate
            assert str(duration.value.point) == '14400'
        else:
            assert duration.is_unknown
    stability = next(f for f in bench.other_facts if f.name.startswith('temperature_fluctuation'))
    assert stability.value.state.value.kind is ValueKind.BOUND
    assert stability.value.state.value.bound_operator == '<='
    assert str(stability.value.state.value.bound_value) == '0.5'
    rows = [o for block in doc["species"].values() for o in block["observations"]]
    assert len(rows) == 21
    assert len(result.observations) == 21
    for row in rows:
        observation = result.observations[f"fixture-source::{row['observation_id']}"]
        experiment = result.experiments[observation.experiment_id]
        if "experiment" in row:
            assert experiment.bench_id == bench.id
        if "quoted_" in row["observation_id"]:
            assert experiment.bench_id is None
