"""t-586 MELTS liquid W-matrix pin + live-engine drift guard."""

from __future__ import annotations

from itertools import combinations

import pytest

from scripts.extract_melts_liquid_w_matrix import (
    DRIFT_GUARD_SAMPLE_INDICES,
    EXPECTED_EDGE_COUNT,
    EXPECTED_ENDMEMBER_COUNT,
    SNAPSHOT_PATH,
    _matrix_digest,
    extract_live_snapshot,
    live_thermoengine_importable,
    load_snapshot,
    sample_edges,
)


def _snapshot() -> dict:
    return load_snapshot(SNAPSHOT_PATH)


def test_snapshot_is_complete_15x105_bilateral_matrix() -> None:
    payload = _snapshot()
    endmembers = list(payload["endmember_basis"])
    edges = list(payload["edges"])
    counts = payload["counts"]

    assert payload["kind"] == "melts_liquid_w_matrix"
    assert payload["engine"]["name"] == "thermoengine"
    assert payload["engine"]["melts_model"] == "MELTSv1.0.2"
    assert payload["engine"]["liquid_model"] == "v1.0"
    assert payload["units"]["W"] == "joules"
    assert payload["t_p_dependence"]["form"] == "constant"
    assert payload["classification"]["engine_has_fitted_flag"] is False
    assert endmembers == [
        "SiO2",
        "TiO2",
        "Al2O3",
        "Fe2O3",
        "MgCr2O4",
        "Fe2SiO4",
        "MnSi0.5O2",
        "Mg2SiO4",
        "NiSi0.5O2",
        "CoSi0.5O2",
        "CaSiO3",
        "Na2SiO3",
        "KAlSiO4",
        "Ca3(PO4)2",
        "H2O",
    ]
    assert len(endmembers) == EXPECTED_ENDMEMBER_COUNT
    assert len(edges) == EXPECTED_EDGE_COUNT
    assert EXPECTED_EDGE_COUNT == len(list(combinations(endmembers, 2)))
    assert counts["endmembers"] == EXPECTED_ENDMEMBER_COUNT
    assert counts["edges"] == EXPECTED_EDGE_COUNT
    assert counts["fitted"] + counts["zero_absent"] == EXPECTED_EDGE_COUNT

    pairs = []
    for edge in edges:
        assert edge["form"] == "constant"
        assert edge["units"] == "joules"
        assert edge["W_S_joules_per_K"] is None
        assert edge["W_V_joules_per_bar"] is None
        assert edge["status"] in {"fitted", "zero_absent"}
        if edge["status"] == "zero_absent":
            assert float(edge["W_joules"]) == 0.0
        else:
            assert float(edge["W_joules"]) != 0.0
        pair = tuple(sorted((edge["component_i"], edge["component_j"])))
        pairs.append(pair)
        assert edge["component_i"] in endmembers
        assert edge["component_j"] in endmembers
        assert edge["component_i"] != edge["component_j"]

    assert len(set(pairs)) == EXPECTED_EDGE_COUNT
    assert counts["fitted"] == sum(1 for edge in edges if edge["status"] == "fitted")
    assert counts["zero_absent"] == sum(
        1 for edge in edges if edge["status"] == "zero_absent"
    )
    assert payload["zero_absent_pairs"] == [
        [edge["component_i"], edge["component_j"]]
        for edge in edges
        if edge["status"] == "zero_absent"
    ]


def test_live_engine_sample_matches_pinned_snapshot() -> None:
    if not live_thermoengine_importable():
        pytest.skip("live ThermoEngine/MELTS is not importable")

    pinned = _snapshot()
    live = extract_live_snapshot()

    assert live["endmember_basis"] == pinned["endmember_basis"]
    assert live["counts"] == pinned["counts"]
    assert live["t_p_dependence"]["form"] == pinned["t_p_dependence"]["form"]
    # Self-consistency: recompute the digest FROM the pinned edges. A hand edit to
    # an edge value that leaves the stored digest stale must fail here — comparing
    # only stored digest against stored digest lets unsampled mutations through
    # (review 2026-08-13, mutate-and-run scenario B).
    assert _matrix_digest(pinned["edges"]) == pinned["matrix_digest"]
    assert live["matrix_digest"] == pinned["matrix_digest"]
    assert live["engine"]["package_version"] == pinned["engine"]["package_version"]
    assert live["engine"]["melts_model"] == pinned["engine"]["melts_model"]
    assert live["engine"]["liquid_model"] == pinned["engine"]["liquid_model"]
    assert live["engine"]["phase_class"] == pinned["engine"]["phase_class"]

    live_sample = sample_edges(live, DRIFT_GUARD_SAMPLE_INDICES)
    pinned_sample = sample_edges(pinned, DRIFT_GUARD_SAMPLE_INDICES)
    assert [edge["index"] for edge in live_sample] == list(DRIFT_GUARD_SAMPLE_INDICES)
    for live_edge, pinned_edge in zip(live_sample, pinned_sample, strict=True):
        assert live_edge["engine_param_name"] == pinned_edge["engine_param_name"]
        assert live_edge["component_i"] == pinned_edge["component_i"]
        assert live_edge["component_j"] == pinned_edge["component_j"]
        assert live_edge["status"] == pinned_edge["status"]
        assert live_edge["form"] == pinned_edge["form"]
        assert live_edge["W_joules"] == pinned_edge["W_joules"]
        assert live_edge["W_S_joules_per_K"] == pinned_edge["W_S_joules_per_K"]
        assert live_edge["W_V_joules_per_bar"] == pinned_edge["W_V_joules_per_bar"]
