from __future__ import annotations

import json
import os
from types import SimpleNamespace

import simulator.wall_advisor as wall_advisor
from simulator.runner import _wall_deposit_cumulative_kg_at_snapshot
from web.advisory import wall_advisory_payload


def test_wall_advisory_parses_one_material_revision_for_repeated_ticks(
    monkeypatch,
) -> None:
    wall_advisor._load_wall_materials_cached.cache_clear()
    original_safe_load = wall_advisor.yaml.safe_load
    parse_calls = 0

    def counted_safe_load(stream):
        nonlocal parse_calls
        parse_calls += 1
        return original_safe_load(stream)

    monkeypatch.setattr(wall_advisor.yaml, "safe_load", counted_safe_load)
    first = wall_advisory_payload(["SiO", "Na"], pO2_mbar=0.1)
    second = wall_advisory_payload(["SiO", "Na"], pO2_mbar=0.1)

    assert parse_calls == 1
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    wall_advisor._load_wall_materials_cached.cache_clear()


def test_public_wall_material_loader_observes_same_size_rewrite(tmp_path) -> None:
    data_path = tmp_path / "wall-materials.yaml"
    data_path.write_text("materials:\n  aaa: {}\n")
    original_stat = data_path.stat()
    first = wall_advisor.load_wall_materials(data_path)

    data_path.write_text("materials:\n  bbb: {}\n")
    os.utime(
        data_path,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    second = wall_advisor.load_wall_materials(data_path)

    assert set(first["materials"]) == {"aaa"}
    assert set(second["materials"]) == {"bbb"}


class _HashableSimulator:
    def __init__(self) -> None:
        self.record = SimpleNamespace(snapshots=[])


class _CountingDelta(dict):
    def __init__(self, counter: list[int], value: float) -> None:
        super().__init__({("hot", "SiO"): value})
        self._counter = counter

    def items(self):
        self._counter[0] += 1
        return super().items()


def _cumulative_delta_visits(hours: int) -> tuple[int, dict[str, object]]:
    counter = [0]
    sim = _HashableSimulator()
    result: dict[str, object] = {}
    for hour in range(1, hours + 1):
        snapshot = SimpleNamespace(
            hour=hour,
            wall_deposit_by_segment_species_delta=_CountingDelta(counter, 0.25),
        )
        sim.record.snapshots.append(snapshot)
        result = _wall_deposit_cumulative_kg_at_snapshot(sim, snapshot)
    return counter[0], result


def test_wall_deposit_cumulative_summary_cost_stays_linear_as_hours_grow() -> None:
    small_hours = 64
    small_visits, small_result = _cumulative_delta_visits(small_hours)
    large_visits, large_result = _cumulative_delta_visits(small_hours * 2)

    assert small_result == {"hot": {"SiO": 16.0}}
    assert large_result == {"hot": {"SiO": 32.0}}
    assert small_visits == small_hours
    assert large_visits <= small_visits * 2 + 2


def test_wall_deposit_cumulative_cache_matches_rescan_for_varied_and_old_rows() -> None:
    sim = _HashableSimulator()
    cumulative: dict[tuple[str, str], float] = {}
    snapshots = []
    deltas = (
        {("hot", "SiO"): 0.1},
        {("rest", "Fe"): 0.2},
        {("hot", "SiO"): -0.025, ("rest", "Fe"): 0.05},
        {},
    )
    expected_by_hour = []
    for hour, delta in enumerate(deltas, start=1):
        snapshot = SimpleNamespace(
            hour=hour,
            wall_deposit_by_segment_species_delta=delta,
        )
        snapshots.append(snapshot)
        sim.record.snapshots.append(snapshot)
        for key, value in delta.items():
            cumulative[key] = cumulative.get(key, 0.0) + value
        expected = {
            segment: {
                species: value
                for (candidate_segment, species), value in cumulative.items()
                if candidate_segment == segment
            }
            for segment in sorted({key[0] for key in cumulative})
        }
        expected_by_hour.append(expected)
        assert _wall_deposit_cumulative_kg_at_snapshot(sim, snapshot) == expected

    assert (
        _wall_deposit_cumulative_kg_at_snapshot(sim, snapshots[1])
        == expected_by_hour[1]
    )
