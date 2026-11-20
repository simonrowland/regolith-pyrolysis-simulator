"""Re-run cache regression mutation detectors, including refusal admissions."""

import pytest

from simulator import engine_cache


def _marker_only_admission(result):
    if not isinstance(result, dict) or result.get("status") != "refusal":
        return False
    detail = " ".join(
        str(result.get(name) or "")
        for name in ("refusal_reason", "engine_status", "reason", "engine_reason")
    ).lower()
    return (
        engine_cache._has_deterministic_engine_refusal(detail)
        or "thermoengine_nonfinite_field" in detail
    )


def test_dropped_temperature_is_detected(monkeypatch):
    from tests.test_engine_cache_format import test_natural_key_is_exact_canonical_and_local

    original = engine_cache.engine_cache_key
    monkeypatch.setattr(engine_cache, "engine_cache_key", lambda engine, version, request: original(
        engine, version, {key: value for key, value in request.items() if key != "temperature_K"},
    ))
    with pytest.raises(AssertionError):
        test_natural_key_is_exact_canonical_and_local()


def test_signed_zero_loss_is_detected(tmp_path, monkeypatch):
    from tests.test_engine_cache_simple import test_hot_round_trip_preserves_float_hex_exactly

    original = engine_cache.pack

    def lose_zero(value, *, key=False):
        if not key and isinstance(value, float) and value == 0.0:
            value = 0.0
        return original(value, key=key)

    monkeypatch.setattr(engine_cache, "pack", lose_zero)
    with pytest.raises(AssertionError):
        test_hot_round_trip_preserves_float_hex_exactly(tmp_path, monkeypatch)


def test_timeout_admission_is_detected(tmp_path, monkeypatch):
    from tests.test_engine_cache_refusals import (
        test_timeout_and_crash_markers_never_admit_a_refusal,
    )

    monkeypatch.setattr(engine_cache, "cacheable_refusal", _marker_only_admission)
    with pytest.raises(AssertionError):
        test_timeout_and_crash_markers_never_admit_a_refusal(
            "engine_timeout", "speciation_no_finite_values no_finite_values",
            tmp_path, monkeypatch,
        )


def test_thermoengine_nonfinite_admission_is_detected():
    from tests.test_engine_cache_refusals import (
        test_thermoengine_nonfinite_marker_does_not_admit_unavailable,
    )

    original = engine_cache.cacheable_refusal
    engine_cache.cacheable_refusal = _marker_only_admission
    try:
        with pytest.raises(AssertionError):
            test_thermoengine_nonfinite_marker_does_not_admit_unavailable()
    finally:
        engine_cache.cacheable_refusal = original


def test_missing_engine_marker_admission_is_detected(tmp_path, monkeypatch):
    from tests.test_engine_cache_refusals import (
        test_unavailable_with_deterministic_markers_is_never_cached,
    )

    original = engine_cache.cacheable_refusal
    engine_cache.cacheable_refusal = _marker_only_admission
    try:
        with pytest.raises(AssertionError):
            test_unavailable_with_deterministic_markers_is_never_cached(
                "engine install missing; speciation_no_finite_values no_finite_values",
                None,
                tmp_path,
                monkeypatch,
            )
    finally:
        engine_cache.cacheable_refusal = original


def test_stolyarova_absolute_population_pin_is_detected(tmp_path, monkeypatch):
    from tests.battery import test_engine_cache_stolyarova_refusals as battery_test

    monkeypatch.setattr(battery_test, "load_score_context", lambda *_args, **_kwargs: object())
    calls = []

    def short_slice(*_args, engines, **_kwargs):
        calls.append(1)
        hits = 0 if len(calls) == 1 else 1
        misses = 1 if len(calls) == 1 else 0
        counts = {
            engine.value: {"hits": hits, "misses": misses}
            for engine in engines
        }
        return ["same row"], counts

    monkeypatch.setattr(battery_test, "_run_slice", short_slice)
    with pytest.raises(AssertionError):
        battery_test.test_stolyarova_vaporock_and_thermoengine_refusals_hit_warm_with_parity(
            tmp_path
        )


@pytest.mark.parametrize("marker", [
    "speciation_no_finite_values", "no_finite_values",
])
def test_deterministic_engine_refusal_marker_removal_is_detected(marker, monkeypatch):
    original = engine_cache._DETERMINISTIC_ENGINE_REFUSAL_MARKER_GROUPS
    monkeypatch.setattr(
        engine_cache,
        "_DETERMINISTIC_ENGINE_REFUSAL_MARKER_GROUPS",
        tuple(
            filtered
            for group in original
            if (filtered := tuple(item for item in group if item != marker))
        ),
    )
    from tests.test_engine_cache_refusals import (
        test_vaporock_refusal_requires_both_empty_speciation_codes,
    )
    with pytest.raises(AssertionError):
        test_vaporock_refusal_requires_both_empty_speciation_codes()


def test_sequence_reuse_is_detected(tmp_path, monkeypatch):
    from tests.test_engine_cache_boxes import test_per_box_sequence_ranges_persist_without_max_scans

    monkeypatch.setattr(engine_cache, "_next_row_seq", lambda connection, box: engine_cache.ENGINE_CACHE_BOX_RANGES[box][0])
    with pytest.raises(AssertionError):
        test_per_box_sequence_ranges_persist_without_max_scans(tmp_path, monkeypatch)


def test_stale_wal_negative_is_detected(tmp_path, monkeypatch):
    from tests.test_engine_cache_hit_path import test_readonly_reader_sees_external_wal_inserts

    monkeypatch.setattr(engine_cache, "_definitely_absent", lambda connection, key: not engine_cache._BLOOMS[connection].maybe_contains(key))
    with pytest.raises(ZeroDivisionError):
        test_readonly_reader_sees_external_wal_inserts(tmp_path, monkeypatch)


def test_uninterned_notices_are_detected(tmp_path, monkeypatch):
    from tests.test_engine_cache_hot import test_long_notices_are_interned_once_and_not_in_hot

    monkeypatch.setattr(engine_cache, "_encode_hot", lambda value: (engine_cache.pack(engine_cache.live_hot(value)), b""))
    with pytest.raises(AssertionError):
        test_long_notices_are_interned_once_and_not_in_hot(tmp_path, monkeypatch)


def test_raw_cold_diagnostics_are_detected(tmp_path, monkeypatch):
    from simulator.melt_backend import base
    from tests.test_engine_cache_hot import test_equilibrium_hot_round_trip_is_exact_and_cold_is_numeric

    monkeypatch.setattr(base, "_EQUILIBRIUM_COLD_FIELDS", (*base._EQUILIBRIUM_COLD_FIELDS, "diagnostics"))
    monkeypatch.setattr(base, "_cold_numbers", lambda value: value)
    with pytest.raises(AssertionError):
        test_equilibrium_hot_round_trip_is_exact_and_cold_is_numeric(tmp_path, monkeypatch)
