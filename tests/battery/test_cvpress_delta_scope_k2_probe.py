"""Remote full-store out-of-scope rows for the second K-source group."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_k2() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", (
        "kems-020-hastie-1981-nbsir",
        "kems-022-demaria-1971",
        "kems-024-gibson-hubbard-1972",
        "kems-028-yakovlev-1984",
        "kems-029-yakovlev-shornikov-2011",
        "kems-038-matchett-2006",
        "kems-045-sossi-2018-pnas-cr",
        "kems-051-allibert-1981",
        "kems-053-stolyarova-1991",
        "kems-088-ichise-1975",
        "kems-111-ichise-1982",
        "kems-112-ichise-1989",
        "kems-118-yamamoto-1983",
    ))
