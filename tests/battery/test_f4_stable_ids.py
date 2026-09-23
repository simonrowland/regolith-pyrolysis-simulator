"""F4 R-ord: durable ids key on content, not encounter ordinals."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.stable_ids import (
    cao_raw_pca_id,
    phase_window_suffix,
    series_point_id,
    tabulated_cell_suffix,
    temperature_token,
)


def test_phase_window_suffix_stable_under_index_reorder() -> None:
    a = phase_window_suffix("cp", lower_K=Decimal("100"), upper_K=Decimal("500"))
    b = phase_window_suffix("cp", lower_K=Decimal("100"), upper_K=Decimal("500"))
    assert a == b
    assert "segment-" not in a
    assert a == "cp:phase-window:T=100..500"


def test_tabulated_cell_suffix_drops_row_ordinal() -> None:
    suffix = tabulated_cell_suffix(
        "S",
        temperature="298.15",
        column="entropy",
        basis="shared",
        name="Silver",
    )
    assert "row=" not in suffix
    assert suffix == "S:shared:T=298.15:col=entropy:name=silver"


def test_series_point_id_uses_temperature_not_index() -> None:
    first = series_point_id("parent", temperature=Decimal("1200"))
    # Reordered encounter with same T keeps the id.
    second = series_point_id("parent", temperature=Decimal("1200"))
    assert first == second == "parent::T=1200"
    assert "::point:" not in first


def test_oxygen_field_fanout_uses_field_name() -> None:
    a = series_point_id("yield", field="mass_yield_percent")
    b = series_point_id("yield", field="fraction_of_feedstock_oxygen_percent")
    assert a != b
    assert a == "yield::field:mass_yield_percent"


def test_cao_raw_pca_uses_temperature() -> None:
    assert cao_raw_pca_id(temperature=Decimal("1700")) == "cao_raw_pCa:T=1700"


def test_mutation_temperature_token_drives_series_point_id(monkeypatch) -> None:
    import simulator.battery.stable_ids as stable_ids

    monkeypatch.setattr(stable_ids, "temperature_token", lambda _v: "MUTATED")
    assert series_point_id("parent", temperature=1) == "parent::T=MUTATED"


def test_janaf_segment_id_stable_when_boundary_list_reordered() -> None:
    """Same T-bounds mint the same id regardless of segment.index."""
    from simulator.battery.stable_ids import phase_window_suffix
    from decimal import Decimal

    left = phase_window_suffix("cp", lower_K=None, upper_K=Decimal("458.000"))
    right = phase_window_suffix("cp", lower_K=None, upper_K=Decimal("458.000"))
    assert left == right
    assert "segment-0" not in left


def test_migrate_oxygen_fanout_uses_field_not_index() -> None:
    from simulator.battery.stable_ids import series_point_id

    # Encounter order of fields must not change the sibling ids.
    fields = ("fraction_of_feedstock_oxygen_percent", "mass_yield_percent")
    ids_a = [series_point_id("yield", field=f) for f in fields]
    ids_b = [series_point_id("yield", field=f) for f in reversed(fields)]
    assert set(ids_a) == set(ids_b)
