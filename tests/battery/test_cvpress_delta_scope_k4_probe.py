"""Remote full-store out-of-scope rows for the fourth K-source group."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_k4() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", (
        "kim-2014-carbon-fe-v-mo-ni",
        "kim-2018-sulfur-fe-v-mo-ni",
        "koehler-barany-kelley-1961-ri5711-ferrite-aluminate-formation",
        "kohsaka-1979-na2o-sio2-emf",
        "konar-2018-alkali-cmas-thesis",
        "kress-carmichael-1988-iron-oxidation-stoichiometry",
    ))
