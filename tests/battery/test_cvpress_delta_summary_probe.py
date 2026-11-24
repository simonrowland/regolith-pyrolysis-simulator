"""Remote full-store per-source summary for fold 22."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_per_source_summary() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("summary", ("A-M",))
