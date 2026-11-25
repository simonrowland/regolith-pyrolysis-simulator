"""Remote full-store out-of-scope rows, source IDs S3."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
)


def test_full_store_out_of_scope_rows_s3() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", (
        "shornikov-2026-hatrurite-evaporation",
        "shornikov-2026-wollastonite-kems",
        "shtenberg-2020-alkaline-oxide-thermodynamic-regression",
        "shugurov-2026-ceo2-tio2-kems",
        "shults-1994-mullite-evaporation",
        "shults-1995-calcium-aluminate-thermodynamics",
        "simeonov-sano-1985-na-bearing-mno-activities",
        "sineva-2023-ni-sn-zn-slag-copper",
        "slag-003-hino-kitagawa-banya-1993",
        "smythe-2013-cerium-thesis",
        "smythe-brenan-2015-cerium-redox",
        "snyder-carmichael-1992-olivine-liquid-feo-activity",
        "sossi-2020-cu-zn-isotope-evap-formalism",
        "sossi-2020-magma-ocean-redox",
        "spaargaren-2025-disk-element-volatility",
        "stebbins-carmichael-weill-1983",
        "steurer-1985-vapor-phase-pyrolysis",
        "stolyarova-1995-cao-alumina-kems",
        "sublimation-kinetics-2023-minerals",
        "sugawara-2011-sodium-silicate-fusion-mixing",
        "sutherland-2025-mare-simulant-volatile-loss",
        "sutton-2005-vanadium-xanes",
        "suzuki-2026-vacuum-oxygen-extraction",
    ))
