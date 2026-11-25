"""Remote full-store out-of-scope rows for the first K-source group."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_k() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", (
        "kang-2007-sio2-activity-cas-low-silica",
        "kassis-frischat-1981-mixed-alkali-vapor-pressures",
        "kassis-frischat-1981-silicate-glass-vapour",
        "kawashima-1984-soda-flux-chemical-sensors",
        "kemori-1980-oxygen-liquid-copper",
        "kems-001-homma-1966",
        "kems-002-ohno-1967",
        "kems-006-zhang-2021",
        "kems-007-costa-2015",
        "kems-010-richter-2007",
        "kems-011-wetzel-gail-2013",
        "kems-012-sossi-2019",
        "kems-015-hashimoto-1983",
    ))
