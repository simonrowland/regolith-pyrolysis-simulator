from __future__ import annotations

from tests.test_reduced_real_pt0_determinism import (
    test_interpolated_equilibrium_cache_miss_keeps_campaign_flux_and_ledger as _cache_cold_warm_guard,
)


def test_cache_cold_warm_class_guard_reaches_flux_and_ledger(
    tmp_path,
    monkeypatch,
):
    _cache_cold_warm_guard(tmp_path, monkeypatch)
