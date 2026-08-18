"""t-586 MELTS liquid W-matrix pin + live-engine drift guard."""

from __future__ import annotations

from itertools import combinations

import pytest

from scripts.extract_melts_liquid_w_matrix import (
    DRIFT_GUARD_SAMPLE_INDICES,
    EXPECTED_EDGE_COUNT,
    EXPECTED_ENDMEMBER_COUNT,
    SNAPSHOT_PATH,
    SNAPSHOT_STATUS,
    _dump_snapshot,
    _matrix_digest,
    extract_live_snapshot,
    live_thermoengine_importable,
    load_snapshot,
    sample_edges,
)

REQUIRED_STATUS_KEYS = (
    "role",
    "runtime_input",
    "extractor",
    "source",
    "default_prediction_path_reads",
    "default_prediction_path_verified",
    "retained_for",
    "expected_consumer_when",
    "value_origin",
    "per_edge_uncertainty",
    "note",
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


def _assert_status_block(payload: dict) -> None:
    assert "status" in payload, "status block missing from MELTS W-matrix snapshot"
    status = payload["status"]
    assert isinstance(status, dict)
    missing = [key for key in REQUIRED_STATUS_KEYS if key not in status]
    assert missing == [], f"status block missing required keys: {missing}"
    assert status == SNAPSHOT_STATUS
    assert status["role"] == "reference_snapshot"
    assert status["runtime_input"] is False
    assert status["extractor"] == "scripts/extract_melts_liquid_w_matrix.py"
    assert status["source"] == "ThermoEngine"
    assert status["default_prediction_path_reads"] is False
    assert status["default_prediction_path_verified"] == "2026-08-18"
    assert status["retained_for"] == "planned_melts_extension"
    assert status["expected_consumer_when"] == "melt_chemical_potential_drives_flux"
    assert status["value_origin"] == "thermoengine_global_phase_equilibrium_regression"
    assert status["per_edge_uncertainty"] == "none"
    assert "not a runtime input" in status["note"]
    assert "2026-08-18" in status["note"]
    assert "planned MELTS-extension" in status["note"]
    assert "not direct measurements" in status["note"]
    assert "per-edge uncertainty" in status["note"]


def test_status_block_present_and_matches_extractor() -> None:
    payload = _snapshot()
    _assert_status_block(payload)
    # Regenerating via extract_live_snapshot must emit the same block.
    # The constant is the write-side source; YAML must stay in lockstep.
    assert SNAPSHOT_STATUS.keys() >= set(REQUIRED_STATUS_KEYS)


def test_status_block_absent_or_incomplete_fails() -> None:
    payload = _snapshot()
    absent = {key: value for key, value in payload.items() if key != "status"}
    with pytest.raises(AssertionError, match="status block missing"):
        _assert_status_block(absent)

    incomplete = dict(payload)
    incomplete["status"] = {
        key: value
        for key, value in payload["status"].items()
        if key != "runtime_input"
    }
    with pytest.raises(AssertionError, match="missing required keys"):
        _assert_status_block(incomplete)


def test_extractor_write_path_emits_status_block() -> None:
    import yaml

    dumped = _dump_snapshot(
        {
            "schema_version": 1,
            "kind": "melts_liquid_w_matrix",
            "format": "yaml",
            "status": dict(SNAPSHOT_STATUS),
            "edges": [],
        }
    )
    rewritten = yaml.safe_load(dumped)
    _assert_status_block(rewritten)
    assert list(rewritten.keys())[:4] == [
        "schema_version",
        "kind",
        "format",
        "status",
    ]
