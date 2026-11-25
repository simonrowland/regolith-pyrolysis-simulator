"""Remote full-store out-of-scope row decode, source IDs starting with R."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_r() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", ("R",))
