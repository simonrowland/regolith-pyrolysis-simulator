"""Remote full-store out-of-scope rows for the fifth K-source group."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_k5() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", (
        "kress-carmichael-1989-lime-iron-silicate-redox",
        "kress-carmichael-1991-iron-redox-calibration",
        "kroger-kreitlow-1956-soda-lime-silicate-calorimetry",
        "kume-2000-cao-activities",
        "kunisada-1986-na-silicate-dephosphorization",
        "kunisada-iwai-1993-na-silicate-sulfide-capacity",
    ))
