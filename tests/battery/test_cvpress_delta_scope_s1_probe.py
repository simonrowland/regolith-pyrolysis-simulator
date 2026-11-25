"""Remote full-store out-of-scope rows, source IDs S1."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_s1() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", (
        "saini-2023-sulfur-vanadium-borosilicate-structure",
        "saito-2021-fexo-cao-silica-activity",
        "sanders-1977-water-vapour-na-vaporization",
        "sargeant-2019-h2-isru-demo",
        "sato-1976-lunar-fo2-apollo-17",
        "sato-hickling-mclane-1973-lunar-fo2",
        "schreiber-1977-plsc8-redox-ti-zr-hf-cr-eu",
        "schreiber-1977-tetravalent-cr-diopside",
        "schreiber-haskin-1976-plsc7-chromium-in-basalts",
        "schumm-1981-alkali-aluminosilicate-thermochemical-tables",
        "sf04-magma-companion-workbook",
        "shaw-2023-vacuum-thermal-sublimation-thesis",
        "shi-2025-mno-activity-eaf-spinel",
        "shilov-1998-sno-zno-p2o5-vaporization",
        "shimaoka-nakamura-1989-nipr2-252",
        "shimaoka-nakamura-1991-iau-colloq126-79",
        "shornikov-1994-mullite-kems",
        "shornikov-1997-cao-alumina-vapor",
        "shornikov-1997-diopside-vaporization",
        "shornikov-2000-aluminosilicate-vaporization",
        "shornikov-2000-cao-silica-vaporization",
        "shornikov-2000-titania-melts",
        "shornikov-2001-mgalumina-silica-vaporization",
    ))
