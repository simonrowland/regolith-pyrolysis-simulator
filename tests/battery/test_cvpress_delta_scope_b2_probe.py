"""Remote full-store out-of-scope rows for the second B-source group."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_b2() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", (
        "berry-oneill-2004-chromium-xanes",
        "beyer-1980-potassium-metasilicate-disilicate-ri8410",
        "bissbort-2026-depolymerized-silicate-glass-transition-viscosity",
        "bonnand-bruand-matzen-et-al-2020-redox-chromium-isotope-magnesiochromite",
        "bonnickson-1954-calcium-magnesium-ferrite-heat-contents",
    ))
