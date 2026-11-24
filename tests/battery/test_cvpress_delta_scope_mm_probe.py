"""Remote full-store out-of-scope row decode, source ID M."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_m() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", ("M-M",))
