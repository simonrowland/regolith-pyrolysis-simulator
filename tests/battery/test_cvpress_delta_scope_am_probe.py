"""Remote full-store out-of-scope row decode, source IDs A through M."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_am() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", ("A-M",))
