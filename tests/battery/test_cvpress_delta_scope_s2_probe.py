"""Remote full-store out-of-scope rows, source IDs S2."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_s2() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", (
        "shornikov-2002-spinel-mgal2o4",
        "shornikov-2003-mullite-thermodynamics",
        "shornikov-2005-potassium-germanate",
        "shornikov-2007-cao-aluminosilicate-melts",
        "shornikov-2008-redox-oxide-evaporation",
        "shornikov-2011-cao-mgo-alumina-melts",
        "shornikov-2011-titania-melt-evaporation",
        "shornikov-2016-spinel-evaporation",
        "shornikov-2017-spinel-evaporation",
        "shornikov-2017-spinel-thermodynamics",
        "shornikov-2018-spinel-solid-solutions",
        "shornikov-2018-spinel-solid-solutions-eg",
        "shornikov-2019-perovskite-evaporation",
        "shornikov-2019-perovskite-evaporation-rjpc",
        "shornikov-2019-perovskite-evaporation-thermodynamics",
        "shornikov-2020-cao-tio2-melts",
        "shornikov-2021-perovskite-chapter",
        "shornikov-2023-natural-cai-evaporation",
        "shornikov-2024-wollastonite-evaporation",
        "shornikov-2025-ca-al-inclusion-evaporation",
        "shornikov-2025-calcium-oxide-evaporation",
        "shornikov-2025-perovskite-condensation",
        "shornikov-2025-saratov-alkali-evaporation",
    ))
