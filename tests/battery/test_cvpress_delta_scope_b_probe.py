"""Remote full-store out-of-scope rows for the first B-source group."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_b() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", (
        "baborak-2023-alkali-silicate-levitation-evaporation",
        "balabanova-lopatin-tyurnina-2025-sro-bao-al2o3-sio2-kems",
        "balasubramaniam-2009-carbothermal-kinetic-constants",
        "banya-chiba-hikosaka-1980-feto-binary-solid-iron",
        "banya-hino-takezoe-1985-na-fe-silicate-redox",
        "banya-hino-yuge-1985-tetsu-feto-sio2-mno-activity",
        "banya-watanabe-1977-feto-p2o5-solid-iron",
        "barcza-1979-mn-slag-metal-equilibrium",
        "barin-1995",
        "basu-2008-feo-steelmaking",
        "berry-2003-in-situ-cr-xanes-furnace",
        "berry-2006-chromium-composition",
        "berry-2018-morb-iron-redox",
    ))
