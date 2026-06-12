from pathlib import Path

import pytest
import yaml

from simulator.feedstock_guard import assert_feedstock_loadable, is_blocked_feedstock


DATA_PATH = Path(__file__).parent.parent / "data" / "feedstocks.yaml"

P4_FEEDSTOCKS = (
    "lunar_highlands_lhs1",
    "lunar_mare_lms1",
    "mars_global_mgs1",
    "lunar_mare_oprl2n",
    "lunar_highlands_nuw_lht_5m",
    "lunar_mare_jsc_1a_legacy",
    "lunar_eac_1a",
    "lunar_mls_1a",
    "lunar_highlands_nu_lht_2m",
)

STAGE0_BUCKETS = (
    "h2o_wt_pct",
    "organics_wt_pct",
    "carbon_wt_pct",
    "sulfur_wt_pct",
    "halides_wt_pct",
    "perchlorates_wt_pct",
    "salts_wt_pct",
    "native_metals_wt_pct",
    "sulfides_wt_pct",
    "refractory_trace_wt_pct",
)

SOURCE_SPOTS = {
    "lunar_highlands_lhs1": {
        "SiO2": 49.12,
        "Al2O3": 26.29,
        "CaO": 13.52,
    },
    "lunar_mare_lms1": {
        "SiO2": 48.22,
        "FeO": 8.79,
        "MgO": 15.97,
    },
    "mars_global_mgs1": {
        "SiO2": 49.00,
        "TiO2": 0.51,
        "MgO": 16.53,
    },
}


@pytest.fixture(scope="module")
def feedstocks():
    return yaml.safe_load(DATA_PATH.read_text()) or {}


def _p4_blocked_keys(feedstocks):
    return {
        key for key in P4_FEEDSTOCKS
        if is_blocked_feedstock(feedstocks[key])
    }


def _p4_loadable_keys(feedstocks):
    return set(P4_FEEDSTOCKS) - _p4_blocked_keys(feedstocks)


def test_vpr_p4_entries_exist(feedstocks):
    assert set(P4_FEEDSTOCKS).issubset(feedstocks)


def test_vpr_p4_entries_keep_required_comments():
    raw = DATA_PATH.read_text().splitlines()
    for key in P4_FEEDSTOCKS:
        index = next(i for i, line in enumerate(raw) if line == f"{key}:")
        preamble = "\n".join(raw[max(0, index - 3):index])
        assert "# buy:" in preamble, key
        assert "# source:" in preamble, key


def test_vpr_p4_entries_have_machine_validated_provenance(feedstocks):
    for key in P4_FEEDSTOCKS:
        entry = feedstocks[key]
        provenance = entry["provenance"]
        assert entry["class"] in {"lunar_simulant", "mars_simulant"}
        assert provenance["composition_citation"], key
        assert provenance["composition_digest"].startswith("sha256:"), key
        assert provenance["lot_or_batch"], key
        assert provenance["buy_url"], key
        assert provenance["buy_url_checked"] == "2026-06-11", key
        if provenance["buy_url"] == "not_available":
            assert provenance["buy_url_reason"], key
        assert entry["glass_fraction"]["value"] is not None, key
        assert entry["glass_fraction"]["note"], key
        assert entry["simulant_vs_real_caveat"]["report_placement"] == "measurement_sidecar", key
        assert "not real" in entry["simulant_vs_real_caveat"]["text"].lower(), key


def test_vpr_p4_entries_declare_stage0_buckets(feedstocks):
    for key in P4_FEEDSTOCKS:
        buckets = feedstocks[key]["stage0_buckets"]
        assert set(STAGE0_BUCKETS) == set(buckets), key
        assert all(value is not None for value in buckets.values()), key


def test_vpr_p4_numeric_oxide_sums_match_declared_sum_check(feedstocks):
    for key in P4_FEEDSTOCKS:
        entry = feedstocks[key]
        composition = entry.get("composition_wt_pct") or {}
        if not composition:
            assert entry["sum_check"] is None, key
            continue
        total = sum(float(value) for value in composition.values())
        assert total == pytest.approx(entry["sum_check"], abs=0.005), key


def test_vpr_p4_mgs1_declares_volatile_free_normalized_basis(feedstocks):
    entry = feedstocks["mars_global_mgs1"]
    basis = entry["composition_basis"]

    assert entry["stage0_profile"] == "bulk_preservation"
    assert "stage0_carbon_cleanup" not in entry
    assert entry["sum_check"] == pytest.approx(100.0, abs=0.005)
    assert basis["mode"] == "declared_anhydrous_volatile_free_normalized"
    assert basis["source_oxide_subset_wt_pct"] == pytest.approx(89.59)
    assert basis["normalized_sum_check_wt_pct"] == pytest.approx(100.0)
    assert basis["excluded_classes"] == [
        "sulfur",
        "halides",
        "perchlorates",
        "salts",
        "sulfides",
    ]
    assert "Long-Fox" in basis["citation"]
    buckets = entry["stage0_buckets"]
    for bucket in ("sulfur_wt_pct", "halides_wt_pct", "perchlorates_wt_pct", "salts_wt_pct"):
        assert buckets[bucket] == "not_reported"

    raw = DATA_PATH.read_text()
    assert "oxide subset sums to 89.59 wt%" in raw
    assert "TODO(vpr-p4): capture lot-variant volatile sidecar" in raw


def test_vpr_p4_spot_values_match_cited_research_capture(feedstocks):
    assert set(SOURCE_SPOTS) == _p4_loadable_keys(feedstocks)
    for key, expected in SOURCE_SPOTS.items():
        composition = feedstocks[key]["composition_wt_pct"]
        for oxide, value in expected.items():
            assert composition[oxide] == pytest.approx(value), f"{key}.{oxide}"


def test_vpr_p4_blocked_entries_fail_closed(feedstocks):
    blocked = _p4_blocked_keys(feedstocks)
    assert blocked
    for key in blocked:
        entry = feedstocks[key]
        assert str(entry["status"]).startswith("blocked_"), key
        assert entry["blocked_reason"], key
        assert "process_notes" in entry, key
        with pytest.raises(ValueError, match=str(entry["status"])):
            assert_feedstock_loadable(key, entry)
        assert key not in SOURCE_SPOTS
        if entry.get("composition_wt_pct"):
            assert str(entry.get("composition_status", "")).endswith("_not_loadable")
            assert entry["simulant_vs_real_caveat"]["report_placement"] == "measurement_sidecar"

    for key in _p4_loadable_keys(feedstocks):
        assert "status" not in feedstocks[key], key
