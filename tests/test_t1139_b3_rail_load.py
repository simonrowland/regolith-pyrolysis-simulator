"""Trace-carrier membership does not open the source rail.

A missing compilation is a typed onset, not a FileNotFoundError on the
cold-spot path. Non-trace species never ask the rail.
"""

from __future__ import annotations

from simulator.condensation import (
    _trace_vapour_carrier_formulas,
    _trace_vapour_carrier_sources,
    cold_spot_diagnostic,
    trace_vapour_condensation_onset,
)
from simulator.state import CondensationTrain, PipeSegment


def _segment() -> PipeSegment:
    return PipeSegment(
        name="stage_0_to_stage_1",
        upstream_stage="stage_0",
        downstream_stage="stage_1",
        wall_temperature_C=100.0,
        length_m=1.0,
        inner_diameter_m=0.12,
    )


def test_membership_is_the_demand_manifest_not_the_rail(monkeypatch) -> None:
    def rail_must_not_load(*_args, **_kwargs):
        raise AssertionError("membership loaded the source rail")

    monkeypatch.setattr(
        "simulator.vapour_rail.source_rail.load_source_rail",
        rail_must_not_load,
    )
    _trace_vapour_carrier_formulas.cache_clear()
    formulas = _trace_vapour_carrier_formulas()
    assert "Rb" in formulas
    assert "GeO" in formulas
    assert "AlO" not in formulas
    assert "Fe" not in formulas
    diagnostic = cold_spot_diagnostic(
        [_segment()],
        {"Fe": 1.0, "AlO": 1.0},
        margin_C=0.0,
        upstream_hot_wall_min_C=None,
        species_partial_pressures_pa={"Fe": 10.0, "AlO": 1.0},
        stages=CondensationTrain.create_default().stages,
    )
    species = {finding["species"] for finding in diagnostic["findings"]}
    assert "Fe" in species
    assert "AlO" not in species


def test_missing_source_rail_is_a_typed_onset(monkeypatch) -> None:
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("janaf/manifest.yaml")

    _trace_vapour_carrier_sources.cache_clear()
    monkeypatch.setattr(
        "simulator.vapour_rail.source_rail.load_source_rail",
        missing,
    )
    onset = trace_vapour_condensation_onset("Rb", 100.0)
    assert onset.status == "source_rail_unavailable"
    assert onset.temperature_K is None
    assert onset.disposition == "unavailable"
    assert "manifest.yaml" in onset.detail
    diagnostic = cold_spot_diagnostic(
        [_segment()],
        {"Rb": 1.0},
        margin_C=0.0,
        upstream_hot_wall_min_C=None,
        species_partial_pressures_pa={"Rb": 100.0},
        stages=CondensationTrain.create_default().stages,
    )
    assert diagnostic["findings"] == []
