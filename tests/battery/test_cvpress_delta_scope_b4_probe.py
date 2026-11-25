"""Remote full-store out-of-scope rows for the fourth B-source group."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_b4() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", (
        "borisov-2018-compilation",
        "borisov-behrens-holtz-2013-ti-p-fe-redox",
        "borisov-behrens-holtz-2015-melt-composition-fe-redox",
        "borisov-veksler-2021-k-fe-supplement",
        "boulliung-wood-2022-so2-solubility-silicate-melts",
        "burnham-2015-europium-redox",
    ))
