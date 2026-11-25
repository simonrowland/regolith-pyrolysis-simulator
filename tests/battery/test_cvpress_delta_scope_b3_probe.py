"""Remote full-store out-of-scope rows for the third B-source group."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_b3() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", (
        "borisov-2004-karrooite-liquidus-redox",
        "borisov-2004-tio2-pd-ni-fe-solubilities",
        "borisov-2008-k-na-miscible-liquids",
        "borisov-2009-alkali-activity-coefficients",
        "borisov-2017-network-modifiers-fe-redox",
    ))
