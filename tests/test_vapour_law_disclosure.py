"""Guard the disclosure cache inventory against production cache-site drift."""

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cache_inventory_matches_the_production_scan() -> None:
    from simulator.vapour_rail.disclosure_cache_inventory import (
        CACHE_SITES,
        DISCLOSURE_CARRYING,
        scan_cache_sites,
    )

    scanned = scan_cache_sites(_REPO_ROOT)
    unregistered = sorted(set(scanned) - set(CACHE_SITES))
    stale = sorted(set(CACHE_SITES) - set(scanned))
    assert not unregistered and not stale, {
        "unregistered": unregistered,
        "stale": stale,
    }
    for site_id, row in CACHE_SITES.items():
        assert row["line"] == scanned[site_id]["line"], site_id
        assert row["path"] == scanned[site_id]["path"], site_id
        assert row["kind"] == scanned[site_id]["kind"], site_id
        assert row["how"]
        assert row["carries_disclosure"] is (site_id in DISCLOSURE_CARRYING)
