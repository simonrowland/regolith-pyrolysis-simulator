"""Remote full-store Zhao 2019 row decode for fold 22."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_zhao_2019_decode() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes(
        "source", ("zhao-2019-ce2o3-ternary-thermodynamics",)
    )
