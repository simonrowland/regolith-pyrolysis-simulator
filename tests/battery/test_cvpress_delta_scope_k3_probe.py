"""Remote full-store out-of-scope rows for the third K-source group."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_k3() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", (
        "kems-119-furukawa-1975",
        "kems-127-qi-1989",
        "kems-137-bischof-2023",
        "kems-139-jacobson-2024",
        "kems-140-heck-2025",
        "kems-169-nakazawa-1976",
        "kems-209-copland-2001",
        "kems-210-hoseinpur-2022",
        "kems-211-naughton-1971",
        "kems-214-hastie-1980-nbsir-80-2178",
        "khedim-2008-chromia-solubility-thesis",
        "khedim-podor-panteix-2010-binary-soda-silicate-cr-solubility",
        "kim-2004-na2o-float-glass-emf",
    ))
