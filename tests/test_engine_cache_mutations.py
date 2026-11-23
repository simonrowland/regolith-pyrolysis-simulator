"""Re-run the review of record's seven independent mutation detectors."""

import pytest

from simulator import engine_cache

pytestmark = pytest.mark.engine_cache_off


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
    from tests.test_engine_cache_refusals import test_transient_and_unknown_refusals_are_never_stored

    monkeypatch.setattr(engine_cache, "CACHEABLE_REFUSAL_CODES", engine_cache.CACHEABLE_REFUSAL_CODES | {"engine_timeout"})
    with pytest.raises(AssertionError):
        test_transient_and_unknown_refusals_are_never_stored("engine_timeout", tmp_path, monkeypatch)


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
