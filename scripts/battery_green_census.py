#!/usr/bin/env python3
"""One-shot landing census of composition bytes against a green migrator.

This is not a permanent test. It reads the green migrator with git, which a
shallow gate tree does not have, and it pins that ref against the extracts
currently in the tree. Run it for this landing. A later extract edit is not
this comparison.

The exception grain is the observation id and the experiment id. A source
name is not an exception. The two Plante-Hastie analytical rows are absent
on purpose.

Usage:
  .venv/bin/python scripts/battery_green_census.py 4153a7c4b
  .venv/bin/python scripts/battery_green_census.py 4153a7c4b --tip-ref e0a147a7a
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import simulator.battery.migrate as migrate_module
from simulator.battery.migrate import REPO_ROOT, to_plain

# Green 4153a7c4b typed these. This tip withholds them on purpose.
# Bischof: 829ce3701 withholds conflicting owner-selected initials.
# Nominal recipe (p.4: SiO2 50.34, Al2O3 15.40, MgO 10.80, CaO 23.46)
# disagrees with Table 6 EPMA Start. Neither inventory is authoritative.
# Robinot: bad7fd5eb / 6cac821ce do not promote an untyped oxide map.
# The generated experiment is the unbound quote robinot_2026_eac1_composition_quoted_sesko.
_GREEN_TYPED_INITIAL_EXCEPTIONS = frozenset(
    {
        "10.1016/j.gca.2023.08.027::experiment::an-di-1-low",
        "10.1016/j.gca.2023.08.027::experiment::an-di-2-low",
        "10.1016/j.gca.2023.08.027::experiment::an-di-3-high",
        "10.1016/j.asr.2026.02.003::kems-044-robinot-2026",
    }
)

# Identity composition or point conditions that still differ from green
# 4153a7c4b. The grain is the observation id. These two Plante-Hastie rows
# matched green before the sorted-fingerprint fallback and must not be listed.
_PLANTE_HASTIE_ROWS_THAT_MATCH_GREEN = frozenset(
    {
        "kems-027-plante-hastie-1983::plante_hastie_1983_table1_snw_glass_composition",
        "kems-027-plante-hastie-1983::plante_hastie_1983_detection_limits_quoted",
    }
)

_GREEN_OBSERVATION_DRIFT_IDS = frozenset({
    # kems-015-hashimoto-1983 (4) Table 5 typed charge point composition
    "kems-015-hashimoto-1983::hashimoto_1983_table5_initial_vaporization_rate_F::T=1973.15:h=c0adf83d7d0d",
    "kems-015-hashimoto-1983::hashimoto_1983_table5_initial_vaporization_rate_F::T=2073.15:h=a05ce9a2cb7c",
    "kems-015-hashimoto-1983::hashimoto_1983_table5_initial_vaporization_rate_F::T=2173.15:h=d5c5b8d545f4",
    "kems-015-hashimoto-1983::hashimoto_1983_table5_initial_vaporization_rate_F::T=2273.15:h=1dfa7c3f8b3a",
    # kems-020-hastie-1981-nbsir (31) point composition carries initial_charge_only and source oxide order
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1240.4:row=1:h=d44c086a9d71",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1240.4:row=2:h=55d2b17014bc",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1295.9:row=3:h=9b28f77ec432",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1306.0:row=1:h=fb2f470793da",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1411.6:row=2:h=d5f42a6734ca",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1425.2:row=1:h=9bf1da29cbf3",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1438.3:row=3:h=c97107edf57b",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1471.3:row=1:h=c9f303cf7da6",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1499.8:row=2:h=d0f8d36d7d7e",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1522.8:row=1:h=a9105d7f2ffa",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1528.6:row=3:h=9dbc586e8df3",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1557.9:row=2:h=25483c11cf24",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1578.1:row=1:h=629fd9f26261",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1588.4:row=3:h=8ca4d4ec761a",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1603.0:row=1:h=6074cb9531e9",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1635.3:row=2:h=cc099a368661",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1657.6:row=3:h=d5692e78dd2e",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T=1748.0:row=3:h=7648e07d315d",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig11_k1_slag_k2o_gamma_tms_open_square::T=1575.0:row=1:h=86cd1ae944a1",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig4_illite_K_SiO_digitized::T=1821.7:h=73d823b8e48e",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig4_illite_K_SiO_digitized::T=1860.8:h=37b8334fa33d",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig4_illite_K_SiO_digitized::T=1868.6:h=8ea25bb94003",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig4_illite_K_SiO_digitized::T=1911.6:h=d9ef607ec5af",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig4_illite_K_SiO_digitized::T=1914.8:h=96933736e50e",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig4_illite_K_SiO_digitized::T=1946.8:h=865b11871b18",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig5_glass_Na_this_work_digitized::T=1333.3:h=0364389ce814",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig5_glass_Na_this_work_digitized::T=1333.3:h=2db4bdbaa4a6",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig5_glass_Na_this_work_digitized::T=1333.3:h=5121ebf96a04",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig5_glass_Na_this_work_digitized::T=1428.6:h=8b93bd735499",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig5_glass_Na_this_work_digitized::T=1488.8:h=5f7a8c15c78e",
    "kems-020-hastie-1981-nbsir::hastie_1981_fig5_glass_Na_this_work_digitized::T=1493.7:h=31e60450532f",
    # kems-023-demaria-1973 (8) catalogue proxy on the point composition
    "kems-023-demaria-1973::demaria_1973_na_psat_table1_this_work::T=1400.0:row=10017:h=60df169fee87",
    "kems-023-demaria-1973::demaria_1973_na_psat_table1_this_work::T=1500.0:row=10017:h=279efc049af6",
    "kems-023-demaria-1973::demaria_1973_na_psat_table1_this_work_12073::T=1400.0:row=12073:h=e710b7f1433b",
    "kems-023-demaria-1973::demaria_1973_na_psat_table1_this_work_12073::T=1500.0:row=12073:h=0cdb3eba1f97",
    "kems-023-demaria-1973::demaria_1973_tio_psat_table1_this_work::T=2100.0:row=10017:h=bc0d169f5782",
    "kems-023-demaria-1973::demaria_1973_tio_psat_table1_this_work::T=2200.0:row=10017:h=02e402d12d9e",
    "kems-023-demaria-1973::demaria_1973_tio_psat_table1_this_work_12073::T=2100.0:row=12073:h=dc8a5bc530df",
    "kems-023-demaria-1973::demaria_1973_tio_psat_table1_this_work_12073::T=2200.0:row=12073:h=80decee22021",
    # kems-027-plante-hastie-1983 (10) initial_charge_only on the analytical SNW glass; the two Table 1 rows match green and are absent
    "kems-027-plante-hastie-1983::plante_hastie_1983_csbo2_activity_1200K",
    "kems-027-plante-hastie-1983::plante_hastie_1983_csbo2_activity_quoted",
    "kems-027-plante-hastie-1983::plante_hastie_1983_libo2_activity_approx",
    "kems-027-plante-hastie-1983::plante_hastie_1983_libo2_activity_quoted",
    "kems-027-plante-hastie-1983::plante_hastie_1983_nabo2_activity_approx",
    "kems-027-plante-hastie-1983::plante_hastie_1983_nabo2_activity_quoted",
    "kems-027-plante-hastie-1983::plante_hastie_1983_o2_figure_only_quoted",
    "kems-027-plante-hastie-1983::plante_hastie_1983_o2_kms_figure_only",
    "kems-027-plante-hastie-1983::plante_hastie_1983_ru_re_sr_undetected",
    "kems-027-plante-hastie-1983::plante_hastie_1983_table3_mass_loss",
    # kems-042-plante-1979 (221) derivation input is the Table 3 note, not the literal values.composition_derivation
    "kems-042-plante-1979::plante1979_table2_s1104_r001_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r002_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r003_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r004_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r005_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r006_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r007_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r008_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r009_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r010_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r011_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r012_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r013_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r014_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r015_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r016_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r017_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r018_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r019_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r020_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r021_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r022_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r023_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r024_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r025_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r026_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r027_quoted",
    "kems-042-plante-1979::plante1979_table2_s1104_r028_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r001_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r002_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r003_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r004_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r005_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r006_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r007_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r008_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r009_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r010_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r011_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r012_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r013_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r014_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r015_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r016_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r017_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r018_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r019_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r020_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r021_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r022_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r023_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r024_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r025_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r026_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r027_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r028_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r029_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r030_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r031_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r032_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r033_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r034_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r035_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r036_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r037_quoted",
    "kems-042-plante-1979::plante1979_table2_s1110_r038_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r001_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r002_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r003_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r004_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r005_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r006_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r007_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r008_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r009_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r010_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r011_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r012_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r013_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r014_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r015_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r016_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r017_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r018_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r019_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r020_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r021_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r022_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r023_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r024_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r025_quoted",
    "kems-042-plante-1979::plante1979_table2_s1115_r026_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r001_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r002_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r003_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r004_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r005_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r006_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r007_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r008_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r009_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r010_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r011_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r012_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r013_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r014_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r015_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r016_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r017_quoted",
    "kems-042-plante-1979::plante1979_table2_s1122_r018_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r001_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r002_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r003_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r004_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r005_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r006_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r007_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r008_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r009_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r010_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r011_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r012_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r013_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r014_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r015_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r016_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r017_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r018_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r019_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r020_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r021_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r022_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r023_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r024_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r025_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r026_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r027_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r028_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r029_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r030_quoted",
    "kems-042-plante-1979::plante1979_table2_s1123_r031_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r001_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r002_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r003_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r004_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r005_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r006_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r007_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r008_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r009_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r010_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r011_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r012_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r013_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r014_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r015_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r016_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r017_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r018_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r019_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r020_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r021_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r022_quoted",
    "kems-042-plante-1979::plante1979_table2_s1126_r023_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r001_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r002_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r003_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r004_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r005_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r006_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r007_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r008_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r009_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r010_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r011_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r012_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r013_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r014_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r015_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r016_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r017_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r018_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r019_quoted",
    "kems-042-plante-1979::plante1979_table2_s1129_r020_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r001_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r002_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r003_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r004_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r005_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r006_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r007_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r008_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r009_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r010_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r011_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r012_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r013_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r014_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r015_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r016_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r017_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r018_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r019_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r020_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r021_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r022_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r023_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r024_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r025_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r026_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r027_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r028_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r029_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r030_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r031_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r032_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r033_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r034_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r035_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r036_quoted",
    "kems-042-plante-1979::plante1979_table2_s1214_r037_quoted",
    # kems-137-bischof-2023 (22) proxy_flag initial_charge_only
    "kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1586.4:h=d7dddfc9ba1e",
    "kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1598.0:h=2cd4bd7cfe96",
    "kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1608.6:h=b9dae2d4d4f2",
    "kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1619.2:h=d2f65843657e",
    "kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1630.8:h=32f9208ca45e",
    "kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1642.5:h=3d5e1b4780de",
    "kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1653.0:h=917fb8de71ee",
    "kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1664.7:h=8dd7cd739c2c",
    "kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1674.2:h=937c91dd605a",
    "kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1684.8:h=56d20ce145ed",
    "kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1695.4:h=eec90a0eba81",
    "kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1586.4:h=af73d739d4d6",
    "kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1598.0:h=973db73de518",
    "kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1608.6:h=a13ef9d20f4d",
    "kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1619.2:h=b19156e2f0a1",
    "kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1630.8:h=b900a3f72e6d",
    "kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1642.5:h=ebb53af38d3f",
    "kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1653.0:h=1c63f096aa50",
    "kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1664.7:h=0d70bcdab058",
    "kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1674.2:h=445ad36b93b3",
    "kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1684.8:h=56d20ce145ed",
    "kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1695.4:h=b02665d3b144",
    # shornikov-2025-saratov-alkali-evaporation (2) bulk charge used at the pressure datum; proxy_flag initial_charge_only
    "shornikov-2025-saratov-alkali-evaporation::shornikov_2025_k_pressure_range_1450_1500k",
    "shornikov-2025-saratov-alkali-evaporation::shornikov_2025_na_pressure_range_1450_1500k",
    # ta-flemetakis-2024 (1) typed starting glass: order, last digit, and proxy
    "ta-flemetakis-2024::flemetakis_2024_starting_glass_composition",
})

_GREEN_EXPERIMENT_INITIAL_DRIFT_IDS = frozenset({
    # kems-006-zhang-2021 (1) generated experiment gains a starting composition
    "6ae16616f5e2f8ee19ac5fce66cf1f8af1c7092013d4a4b1be22932f950ec2e4::kems-006-zhang-2021",
    # kems-012-sossi-2019 (43) typed mole fraction kept; locator note; empty parameters
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-1-3-16",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-1-3-16b",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-15-07-16a",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-15-07-16b",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-2-3-16",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps1",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps2",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps3",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps4",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps5",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps6",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-15-07-16c",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-15-07-16d",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-15-07-16g",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-15-07-16h",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16a",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16b",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16c",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16d",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16e",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16f",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16g",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-16-07-16h",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-17-07-16a",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-17-07-16b",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-17-07-16c",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-18-07-16a",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-18-07-16c",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-3m-1-3-16",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-3m-18-07-16b",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-3m-2-3-16",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-3m-29-2-16",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-3m-29-2-16b",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-c17-12-15a",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-c17-12-15b",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-c17-12-15c",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-c18-12-15",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-p08-02-17b",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-p09-06-18a",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-p09-06-18b",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-p09-06-18c",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-p11-06-18a",
    "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-p11-06-18b",
    # kems-015-hashimoto-1983 (32) Table 5 typed charge point composition
    "10.2343/geochemj.17.111::experiment::fcmas-free-evap-series",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c1",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c3-1",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c3-2",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c5-1",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c5-2",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c5-3",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c7",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17c9-2",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-17d2",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18b6-1",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18b6-2",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18b8-1",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18b8-2",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18b8-3",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c1-1",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c1-2",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c1-3",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c3-1",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c3-2",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c3-3",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c3-4",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c5-1",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-18c5-2",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-19b2",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-19b4",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-19b6",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-19b8",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-19c1",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-20b2",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-20b4",
    "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-20b6",
    # kems-020-hastie-1981-nbsir (6) point composition carries initial_charge_only and source oxide order
    "10.6028/nbs.ir.81-2279::experiment::hastie-1981-k1-kms",
    "10.6028/nbs.ir.81-2279::experiment::hastie-1981-measured-soda-lime-glass-kms",
    "10.6028/nbs.ir.81-2279::experiment::hastie-1981-measured-soda-lime-glass-tms",
    "10.6028/nbs.ir.81-2279::experiment::hastie-1981-pt-tms",
    "10.6028/nbs.ir.81-2279::experiment::hastie-1981-solgasmix-model-glass",
    "10.6028/nbs.ir.81-2279::kems-020-hastie-1981-nbsir",
    # kems-027-plante-hastie-1983 (2) initial_charge_only on the analytical SNW glass; the two Table 1 rows match green and are absent
    "10.6028/nbs.ir.83-2731::experiment::kms-vacuum-glass-series",
    "10.6028/nbs.ir.83-2731::experiment::tms-n2-glass-series",
    # kems-044-robinot-2026 (1) untyped quoted composition withheld
    "10.1016/j.asr.2026.02.003::kems-044-robinot-2026",
    # kems-137-bischof-2023 (3) proxy_flag initial_charge_only
    "10.1016/j.gca.2023.08.027::experiment::an-di-1-low",
    "10.1016/j.gca.2023.08.027::experiment::an-di-2-low",
    "10.1016/j.gca.2023.08.027::experiment::an-di-3-high",
    # norris-2017-earth-volatiles-nature (4) 12-digit typed paste kept
    "10.1038/nature23645::experiment::volatile-loss-logfo2-neg11",
    "10.1038/nature23645::experiment::volatile-loss-logfo2-neg13",
    "10.1038/nature23645::experiment::volatile-loss-logfo2-neg7",
    "10.1038/nature23645::experiment::volatile-loss-logfo2-neg9",
    # ta-flemetakis-2024 (1) typed starting glass: order, last digit, and proxy
    "10.5194/ejm-36-173-2024::ta-flemetakis-2024",
})

def _load_migrate(ref: str | None):
    if ref is None:
        return migrate_module
    source = subprocess.check_output(
        ["git", "show", f"{ref}:simulator/battery/migrate.py"],
        cwd=ROOT,
        text=True,
    )
    name = "simulator.battery._census_" + ref.replace("/", "_")
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / "simulator" / "battery" / "migrate.py")
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module


def _plain_digest(payload: object) -> str:
    encoded = json.dumps(payload, default=str, ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _census(module):
    migrator = module.Migrator(root=REPO_ROOT)
    extracts = REPO_ROOT / "data" / "literature" / "extracts"
    for path in migrate_module.discover_extracts(extracts):
        migrator._migrate_extract(path)
    for work_id in list(migrator.result.works):
        migrator._promote_universal_observation_printed_compositions(work_id)
    source_by_experiment: dict[str, str] = {}
    observations: dict[str, tuple[str, str, str]] = {}
    for observation in migrator.result.observations.values():
        if observation.experiment_id and observation.source_id:
            source_by_experiment.setdefault(
                observation.experiment_id, observation.source_id
            )
        plain = to_plain(observation)
        identity = plain.get("identity") if isinstance(plain, dict) else None
        composition = identity.get("composition") if isinstance(identity, dict) else None
        point_conditions = plain.get("point_conditions") if isinstance(plain, dict) else None
        observations[observation.observation_id] = (
            observation.source_id,
            _plain_digest(composition),
            _plain_digest(point_conditions),
        )
    source_by_work: dict[str, str] = {}
    for work_id, work in migrator.result.works.items():
        if len(work.source_ids) == 1:
            source_by_work[work_id] = work.source_ids[0]
    typed: set[str] = set()
    initials: dict[str, tuple[str | None, str]] = {}
    for experiment_id, experiment in migrator.result.experiments.items():
        initial = experiment.sample.initial_composition
        if initial is not None and initial.state.is_value:
            typed.add(experiment_id)
        initials[experiment_id] = (
            source_by_experiment.get(experiment_id)
            or source_by_work.get(experiment.work_id or ""),
            _plain_digest(to_plain(initial) if initial is not None else None),
        )
    return typed, observations, initials


def _drift(green, current):
    drifted = set()
    for item_id in set(green) | set(current):
        green_row = green.get(item_id)
        current_row = current.get(item_id)
        if green_row is None or current_row is None or green_row[1:] != current_row[1:]:
            drifted.add(item_id)
    return drifted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("green_ref", help="git ref whose migrate.py is green")
    parser.add_argument(
        "--tip-ref",
        default=None,
        help="git ref to compare. Default is the migrate.py on this tree.",
    )
    args = parser.parse_args(argv)
    hidden = _PLANTE_HASTIE_ROWS_THAT_MATCH_GREEN & _GREEN_OBSERVATION_DRIFT_IDS
    if hidden:
        print("exception list hides rows that must match green:")
        for item_id in sorted(hidden):
            print(f"  {item_id}")
        return 1
    green_typed, green_obs, green_initials = _census(_load_migrate(args.green_ref))
    tip_typed, tip_obs, tip_initials = _census(_load_migrate(args.tip_ref))
    missing = green_typed - _GREEN_TYPED_INITIAL_EXCEPTIONS - tip_typed
    unknown_exceptions = _GREEN_TYPED_INITIAL_EXCEPTIONS - green_typed
    obs_drift = _drift(green_obs, tip_obs)
    exp_drift = _drift(green_initials, tip_initials)
    unexpected_obs = obs_drift - _GREEN_OBSERVATION_DRIFT_IDS
    missing_obs = _GREEN_OBSERVATION_DRIFT_IDS - obs_drift
    unexpected_exp = exp_drift - _GREEN_EXPERIMENT_INITIAL_DRIFT_IDS
    missing_exp = _GREEN_EXPERIMENT_INITIAL_DRIFT_IDS - exp_drift
    label = args.tip_ref or "worktree"
    print(f"green {args.green_ref} tip {label}")
    print(f"observations {len(obs_drift)} expected {len(_GREEN_OBSERVATION_DRIFT_IDS)}")
    print(f"experiments {len(exp_drift)} expected {len(_GREEN_EXPERIMENT_INITIAL_DRIFT_IDS)}")
    print(f"typed_missing {sorted(missing)}")
    print(f"typed_exception_absent_from_green {sorted(unknown_exceptions)}")
    ok = not (
        missing or unknown_exceptions or unexpected_obs or missing_obs
        or unexpected_exp or missing_exp
    )
    if ok:
        print("MATCH")
        return 0
    print("MISMATCH")
    for title, rows in (
        ("unexpected observations", unexpected_obs),
        ("no longer drifting observations", missing_obs),
        ("unexpected experiments", unexpected_exp),
        ("no longer drifting experiments", missing_exp),
    ):
        if not rows:
            continue
        print(title)
        for item_id in sorted(rows):
            print(f"  {item_id}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
