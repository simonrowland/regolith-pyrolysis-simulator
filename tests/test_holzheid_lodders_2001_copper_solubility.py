"""Holzheid and Lodders (2001) copper solubility extract.

Pins are the printed Table 1 and Table 2 cells. Each run check requires the
printed log fO2, silicate SiO2 and silicate Cu tokens to sit on that run's
pdftotext line, so a column swap fails even when every number occurs somewhere
in the paper.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase
from tests.battery.test_migrate import _migrate_real_extract

EXTRACT_NAME = "holzheid-lodders-2001-copper-solubility.yaml"
ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME
CORPUS = (
    Path(os.environ["REGOLITH_CORPUS_ROOT"])
    if os.environ.get("REGOLITH_CORPUS_ROOT")
    else Path.home() / "Repos" / "regolith-corpus"
)
TEXT = CORPUS / "text" / "holzheid-lodders-2001-copper-solubility" / "pdftotext-layout.txt"

# (observation_id, printed snippet after minus/plus/less-than normalization, point value or None)
PINS = [('hl01_t1_AD_SiO2', '50.88 (0.7)', '50.88'), ('hl01_t1_AD_Al2O3', '13.75 (1)', '13.75'), ('hl01_t1_AD_MgO', '10.36 (0.8)', '10.36'), ('hl01_t1_AD_CaO', '24.89 (0.6)', '24.89'), ('hl01_t1_AD_FeO', '0', '0'), ('hl01_t1_BK_SiO2', '49.05 (0.5)', '49.05'), ('hl01_t1_BK_Al2O3', '14.07 (0.9)', '14.07'), ('hl01_t1_BK_MgO', '10.61 (0.9)', '10.61'), ('hl01_t1_BK_CaO', '19.24 (0.3)', '19.24'), ('hl01_t1_BK_FeO', '6.95 (0.9)', '6.95'), ('hl01_fe0_s0_CC1_metal_Cu', '100', '100'), ('hl01_fe0_s0_CC1_silicate_SiO2', '50.4 (0.8)', '50.4'), ('hl01_fe0_s0_CC1_silicate_MgO', '9.84 (0.8)', '9.84'), ('hl01_fe0_s0_CC1_silicate_Al2O3', '15.9 (0.6)', '15.9'), ('hl01_fe0_s0_CC1_silicate_CaO', '23.1 (0.9)', '23.1'), ('hl01_fe0_s0_CC1_silicate_Cu', '0.327 (12)', '0.327'), ('hl01_fe0_s0_CC9_metal_Cu', '100', '100'), ('hl01_fe0_s0_CC9_silicate_SiO2', '50.5 (0.4)', '50.5'), ('hl01_fe0_s0_CC9_silicate_MgO', '9.60 (0.6)', '9.60'), ('hl01_fe0_s0_CC9_silicate_Al2O3', '16.0 (0.5)', '16.0'), ('hl01_fe0_s0_CC9_silicate_CaO', '23.2 (0.9)', '23.2'), ('hl01_fe0_s0_CC9_silicate_Cu', '0.185 (20)', '0.185'), ('hl01_fe0_s0_CC4_metal_Cu', '100', '100'), ('hl01_fe0_s0_CC4_silicate_SiO2', '50.1 (0.4)', '50.1'), ('hl01_fe0_s0_CC4_silicate_MgO', '9.79 (1)', '9.79'), ('hl01_fe0_s0_CC4_silicate_Al2O3', '16.3 (0.6)', '16.3'), ('hl01_fe0_s0_CC4_silicate_CaO', '23.0 (0.9)', '23.0'), ('hl01_fe0_s0_CC4_silicate_Cu', '0.109 (17)', '0.109'), ('hl01_fe0_s0_CC3_metal_Cu', '100', '100'), ('hl01_fe0_s0_CC3_silicate_SiO2', '50.6 (0.6)', '50.6'), ('hl01_fe0_s0_CC3_silicate_MgO', '9.74 (0.8)', '9.74'), ('hl01_fe0_s0_CC3_silicate_Al2O3', '16.1 (0.4)', '16.1'), ('hl01_fe0_s0_CC3_silicate_CaO', '22.9 (0.9)', '22.9'), ('hl01_fe0_s0_CC3_silicate_Cu', '0.041 (29)', '0.041'), ('hl01_fe0_s0_CC2_metal_Cu', '100', '100'), ('hl01_fe0_s0_CC2_silicate_SiO2', '50.4 (0.8)', '50.4'), ('hl01_fe0_s0_CC2_silicate_MgO', '10.1 (1)', '10.1'), ('hl01_fe0_s0_CC2_silicate_Al2O3', '15.5 (1)', '15.5'), ('hl01_fe0_s0_CC2_silicate_CaO', '23.1 (0.4)', '23.1'), ('hl01_fe0_s0_CC2_silicate_Cu', '0.036 (22)', '0.036'), ('hl01_fe0_s0_CC6_metal_Cu', '100', '100'), ('hl01_fe0_s0_CC6_silicate_SiO2', '46.4 (0.6)', '46.4'), ('hl01_fe0_s0_CC6_silicate_MgO', '8.69 (0.9)', '8.69'), ('hl01_fe0_s0_CC6_silicate_Al2O3', '22.9 (0.4)', '22.9'), ('hl01_fe0_s0_CC6_silicate_CaO', '21.0 (0.4)', '21.0'), ('hl01_fe0_s0_CC6_silicate_Cu', '0.136 (14)', '0.136'), ('hl01_fe0_s0_CC8_metal_Cu', '100', '100'), ('hl01_fe0_s0_CC8_silicate_SiO2', '41.5 (2)', '41.5'), ('hl01_fe0_s0_CC8_silicate_MgO', '4.23 (62)', '4.23'), ('hl01_fe0_s0_CC8_silicate_Al2O3', '33.8 (4)', '33.8'), ('hl01_fe0_s0_CC8_silicate_CaO', '19.3 (2)', '19.3'), ('hl01_fe0_s0_CC8_silicate_Cu', '0.162 (45)', '0.162'), ('hl01_fe0_s0_CC7_metal_Cu', '100', '100'), ('hl01_fe0_s0_CC7_silicate_SiO2', '39.3 (0.8)', '39.3'), ('hl01_fe0_s0_CC7_silicate_MgO', '6.06 (1)', '6.06'), ('hl01_fe0_s0_CC7_silicate_Al2O3', '35.0 (0.6)', '35.0'), ('hl01_fe0_s0_CC7_silicate_CaO', '17.9 (0.6)', '17.9'), ('hl01_fe0_s0_CC7_silicate_Cu', '0.250 (12)', '0.250'), ('hl01_fe1_s0_CC1_metal_Cu', '98.2 (8)', '98.2'), ('hl01_fe1_s0_CC1_metal_Fe', '0.07 (86)', '0.07'), ('hl01_fe1_s0_CC1_silicate_SiO2', '48.4 (0.6)', '48.4'), ('hl01_fe1_s0_CC1_silicate_MgO', '9.48 (0.7)', '9.48'), ('hl01_fe1_s0_CC1_silicate_Al2O3', '15.7 (1)', '15.7'), ('hl01_fe1_s0_CC1_silicate_CaO', '22.6 (0.4)', '22.6'), ('hl01_fe1_s0_CC1_silicate_FeO', '2.80 (3)', '2.80'), ('hl01_fe1_s0_CC1_silicate_Cu', '0.355 (10)', '0.355'), ('hl01_fe1_s0_CC9_metal_Cu', '95.1 (9)', '95.1'), ('hl01_fe1_s0_CC9_metal_Fe', '3.23 (2)', '3.23'), ('hl01_fe1_s0_CC9_silicate_SiO2', '47.3 (0.6)', '47.3'), ('hl01_fe1_s0_CC9_silicate_MgO', '10.0 (0.6)', '10.0'), ('hl01_fe1_s0_CC9_silicate_Al2O3', '16.0 (0.6)', '16.0'), ('hl01_fe1_s0_CC9_silicate_CaO', '20.1 (0.5)', '20.1'), ('hl01_fe1_s0_CC9_silicate_FeO', '5.92 (3)', '5.92'), ('hl01_fe1_s0_CC9_silicate_Cu', '0.159 (19)', '0.159'), ('hl01_fe1_s0_CC4_metal_Cu', '97.1 (8)', '97.1'), ('hl01_fe1_s0_CC4_metal_Fe', '3.92 (2)', '3.92'), ('hl01_fe1_s0_CC4_silicate_SiO2', '48.0 (0.6)', '48.0'), ('hl01_fe1_s0_CC4_silicate_MgO', '9.97 (0.5)', '9.97'), ('hl01_fe1_s0_CC4_silicate_Al2O3', '16.1 (0.6)', '16.1'), ('hl01_fe1_s0_CC4_silicate_CaO', '22.5 (0.4)', '22.5'), ('hl01_fe1_s0_CC4_silicate_FeO', '2.70 (4)', '2.70'), ('hl01_fe1_s0_CC4_silicate_Cu', '0.098 (48)', '0.098'), ('hl01_fe1_s0_CC3_metal_Cu', '97.4 (9)', '97.4'), ('hl01_fe1_s0_CC3_metal_Fe', '1.13 (9)', '1.13'), ('hl01_fe1_s0_CC3_silicate_SiO2', '48.2 (0.2)', '48.2'), ('hl01_fe1_s0_CC3_silicate_MgO', '10.1 (0.7)', '10.1'), ('hl01_fe1_s0_CC3_silicate_Al2O3', '16.0 (0.6)', '16.0'), ('hl01_fe1_s0_CC3_silicate_CaO', '22.6 (0.4)', '22.6'), ('hl01_fe1_s0_CC3_silicate_FeO', '2.42 (4)', '2.42'), ('hl01_fe1_s0_CC3_silicate_Cu', '0.077 (31)', '0.077'), ('hl01_fe1_s0_CC2_metal_Cu', '96.4 (10)', '96.4'), ('hl01_fe1_s0_CC2_metal_Fe', '2.05 (15)', '2.05'), ('hl01_fe1_s0_CC2_silicate_SiO2', '48.0 (0.6)', '48.0'), ('hl01_fe1_s0_CC2_silicate_MgO', '10.7 (0.6)', '10.7'), ('hl01_fe1_s0_CC2_silicate_Al2O3', '16.3 (1)', '16.3'), ('hl01_fe1_s0_CC2_silicate_CaO', '22.2 (0.5)', '22.2'), ('hl01_fe1_s0_CC2_silicate_FeO', '1.98 (3)', '1.98'), ('hl01_fe1_s0_CC2_silicate_Cu', '0.053 (26)', '0.053'), ('hl01_fe1_s0_CC6_metal_Cu', '97.1 (9)', '97.1'), ('hl01_fe1_s0_CC6_metal_Fe', '2.50 (2)', '2.50'), ('hl01_fe1_s0_CC6_silicate_SiO2', '42.2 (0.5)', '42.2'), ('hl01_fe1_s0_CC6_silicate_MgO', '8.10 (11)', '8.10'), ('hl01_fe1_s0_CC6_silicate_Al2O3', '26.5 (1)', '26.5'), ('hl01_fe1_s0_CC6_silicate_CaO', '19.7 (1)', '19.7'), ('hl01_fe1_s0_CC6_silicate_FeO', '2.49 (6)', '2.49'), ('hl01_fe1_s0_CC6_silicate_Cu', '0.116 (33)', '0.116'), ('hl01_fe1_s0_CC7_metal_Cu', '94.0 (10)', '94.0'), ('hl01_fe1_s0_CC7_metal_Fe', '3.35 (1)', '3.35'), ('hl01_fe1_s0_CC7_silicate_SiO2', '40.6 (0.5)', '40.6'), ('hl01_fe1_s0_CC7_silicate_MgO', '5.60 (0.7)', '5.60'), ('hl01_fe1_s0_CC7_silicate_Al2O3', '33.9 (9)', '33.9'), ('hl01_fe1_s0_CC7_silicate_CaO', '17.4 (0.5)', '17.4'), ('hl01_fe1_s0_CC7_silicate_FeO', '1.91 (5)', '1.91'), ('hl01_fe1_s0_CC7_silicate_Cu', '0.256 (18)', '0.256'), ('hl01_fe0_s1_SII5_metal_Cu', '99.6 (10)', '99.6'), ('hl01_fe0_s1_SII5_metal_S', '<0.01', None), ('hl01_fe0_s1_SII5_sulfide_Cu', '79.7 (1)', '79.7'), ('hl01_fe0_s1_SII5_sulfide_S', '20.3 (1)', '20.3'), ('hl01_fe0_s1_SII5_silicate_SiO2', '49.1 (1)', '49.1'), ('hl01_fe0_s1_SII5_silicate_MgO', '8.29 (1)', '8.29'), ('hl01_fe0_s1_SII5_silicate_Al2O3', '16.3 (1)', '16.3'), ('hl01_fe0_s1_SII5_silicate_CaO', '24.0 (0.1)', '24.0'), ('hl01_fe0_s1_SII5_silicate_Cu', '0.337 (8)', '0.337'), ('hl01_fe0_s1_SII5_silicate_S', '<0.012', None), ('hl01_fe0_s1_SI2_metal_Cu', '100 (1)', '100'), ('hl01_fe0_s1_SI2_metal_S', '<0.01', None), ('hl01_fe0_s1_SI2_sulfide_Cu', '79.8 (1)', '79.8'), ('hl01_fe0_s1_SI2_sulfide_S', '19.8 (2)', '19.8'), ('hl01_fe0_s1_SI2_silicate_SiO2', '50.6 (0.4)', '50.6'), ('hl01_fe0_s1_SI2_silicate_MgO', '9.24 (0.9)', '9.24'), ('hl01_fe0_s1_SI2_silicate_Al2O3', '15.3 (1)', '15.3'), ('hl01_fe0_s1_SI2_silicate_CaO', '24.5 (0.2)', '24.5'), ('hl01_fe0_s1_SI2_silicate_Cu', '0.126 (13)', '0.126'), ('hl01_fe0_s1_SI2_silicate_S', '<0.004', None), ('hl01_fe0_s1_SI18_metal_Cu', '98.9 (10)', '98.9'), ('hl01_fe0_s1_SI18_metal_S', '<0.35', None), ('hl01_fe0_s1_SI18_sulfide_Cu', '79.5 (1)', '79.5'), ('hl01_fe0_s1_SI18_sulfide_S', '20.1 (1)', '20.1'), ('hl01_fe0_s1_SI18_silicate_SiO2', '50.2 (0.4)', '50.2'), ('hl01_fe0_s1_SI18_silicate_MgO', '8.66 (0.6)', '8.66'), ('hl01_fe0_s1_SI18_silicate_Al2O3', '16.6 (0.4)', '16.6'), ('hl01_fe0_s1_SI18_silicate_CaO', '24.5 (0.8)', '24.5'), ('hl01_fe0_s1_SI18_silicate_Cu', '0.116 (100)', '0.116'), ('hl01_fe0_s1_SI18_silicate_S', 'n.d.', None), ('hl01_fe0_s1_SI23_metal_Cu', '—', None), ('hl01_fe0_s1_SI23_metal_S', '—', None), ('hl01_fe0_s1_SI23_sulfide_Cu', '—', None), ('hl01_fe0_s1_SI23_sulfide_S', '—', None), ('hl01_fe0_s1_SI23_silicate_SiO2', '50.0 (0.6)', '50.0'), ('hl01_fe0_s1_SI23_silicate_MgO', '9.02 (0.8)', '9.02'), ('hl01_fe0_s1_SI23_silicate_Al2O3', '15.1 (0.7)', '15.1'), ('hl01_fe0_s1_SI23_silicate_CaO', '23.9 (0.8)', '23.9'), ('hl01_fe0_s1_SI23_silicate_Cu', '0.097 (19)', '0.097'), ('hl01_fe0_s1_SI23_silicate_S', '0.012 (33)', '0.012'), ('hl01_fe0_s1_SI25_metal_Cu', '100 (1)', '100'), ('hl01_fe0_s1_SI25_metal_S', '0.02 (1)', '0.02'), ('hl01_fe0_s1_SI25_sulfide_Cu', '80.2 (1)', '80.2'), ('hl01_fe0_s1_SI25_sulfide_S', '20.0 (10)', '20.0'), ('hl01_fe0_s1_SI25_silicate_SiO2', '49.7 (0.4)', '49.7'), ('hl01_fe0_s1_SI25_silicate_MgO', '8.50 (2)', '8.50'), ('hl01_fe0_s1_SI25_silicate_Al2O3', '16.5 (4)', '16.5'), ('hl01_fe0_s1_SI25_silicate_CaO', '23.9 (0.1)', '23.9'), ('hl01_fe0_s1_SI25_silicate_Cu', '0.089 (10)', '0.089'), ('hl01_fe0_s1_SI25_silicate_S', '0.016 (25)', '0.016'), ('hl01_fe0_s1_SI22_metal_Cu', '100 (1)', '100'), ('hl01_fe0_s1_SI22_metal_S', '0.02 (1)', '0.02'), ('hl01_fe0_s1_SI22_sulfide_Cu', '80.6 (1)', '80.6'), ('hl01_fe0_s1_SI22_sulfide_S', '19.6 (3)', '19.6'), ('hl01_fe0_s1_SI22_silicate_SiO2', '50.5 (0.6)', '50.5'), ('hl01_fe0_s1_SI22_silicate_MgO', '8.75 (0.9)', '8.75'), ('hl01_fe0_s1_SI22_silicate_Al2O3', '16.6 (0.2)', '16.6'), ('hl01_fe0_s1_SI22_silicate_CaO', '24.6 (0.1)', '24.6'), ('hl01_fe0_s1_SI22_silicate_Cu', '0.089 (10)', '0.089'), ('hl01_fe0_s1_SI22_silicate_S', '<0.004', None), ('hl01_fe0_s1_SI24_metal_Cu', '—', None), ('hl01_fe0_s1_SI24_metal_S', '—', None), ('hl01_fe0_s1_SI24_sulfide_Cu', '—', None), ('hl01_fe0_s1_SI24_sulfide_S', '—', None), ('hl01_fe0_s1_SI24_silicate_SiO2', '49.6 (0.1)', '49.6'), ('hl01_fe0_s1_SI24_silicate_MgO', '8.77 (1)', '8.77'), ('hl01_fe0_s1_SI24_silicate_Al2O3', '15.6 (1)', '15.6'), ('hl01_fe0_s1_SI24_silicate_CaO', '24.3 (0.1)', '24.3'), ('hl01_fe0_s1_SI24_silicate_Cu', '0.106 (10)', '0.106'), ('hl01_fe0_s1_SI24_silicate_S', '0.020 (20)', '0.020'), ('hl01_fe0_s1_SII7_metal_Cu', '100 (1)', '100'), ('hl01_fe0_s1_SII7_metal_S', '<0.10', None), ('hl01_fe0_s1_SII7_sulfide_Cu', '79.9 (1)', '79.9'), ('hl01_fe0_s1_SII7_sulfide_S', '18.7 (3)', '18.7'), ('hl01_fe0_s1_SII7_silicate_SiO2', '49.3 (1)', '49.3'), ('hl01_fe0_s1_SII7_silicate_MgO', '8.56 (2)', '8.56'), ('hl01_fe0_s1_SII7_silicate_Al2O3', '15.8 (0.3)', '15.8'), ('hl01_fe0_s1_SII7_silicate_CaO', '24.5 (0.2)', '24.5'), ('hl01_fe0_s1_SII7_silicate_Cu', '0.071 (10)', '0.071'), ('hl01_fe0_s1_SII7_silicate_S', '0.016 (25)', '0.016'), ('hl01_fe0_s1_SII8_metal_Cu', '100 (1)', '100'), ('hl01_fe0_s1_SII8_metal_S', '<0.05', None), ('hl01_fe0_s1_SII8_sulfide_Cu', '79.6 (1)', '79.6'), ('hl01_fe0_s1_SII8_sulfide_S', '18.9 (3)', '18.9'), ('hl01_fe0_s1_SII8_silicate_SiO2', '49.3 (0.1)', '49.3'), ('hl01_fe0_s1_SII8_silicate_MgO', '8.73 (0.7)', '8.73'), ('hl01_fe0_s1_SII8_silicate_Al2O3', '15.8 (0.5)', '15.8'), ('hl01_fe0_s1_SII8_silicate_CaO', '24.5 (0.4)', '24.5'), ('hl01_fe0_s1_SII8_silicate_Cu', '0.054 (9)', '0.054'), ('hl01_fe0_s1_SII8_silicate_S', '0.024 (17)', '0.024'), ('hl01_fe1_s1_SII5_metal_Cu', '100 (1)', '100'), ('hl01_fe1_s1_SII5_metal_Fe', '0.06 (33)', '0.06'), ('hl01_fe1_s1_SII5_metal_S', '<0.01', None), ('hl01_fe1_s1_SII5_sulfide_Cu', '79.6 (1)', '79.6'), ('hl01_fe1_s1_SII5_sulfide_Fe', '0.54 (41)', '0.54'), ('hl01_fe1_s1_SII5_sulfide_S', '20.1 (7)', '20.1'), ('hl01_fe1_s1_SII5_silicate_SiO2', '46.7 (0.4)', '46.7'), ('hl01_fe1_s1_SII5_silicate_MgO', '9.51 (0.8)', '9.51'), ('hl01_fe1_s1_SII5_silicate_Al2O3', '17.5 (1)', '17.5'), ('hl01_fe1_s1_SII5_silicate_CaO', '17.8 (0.3)', '17.8'), ('hl01_fe1_s1_SII5_silicate_FeO', '6.30 (4)', '6.30'), ('hl01_fe1_s1_SII5_silicate_Cu', '0.208 (8)', '0.208'), ('hl01_fe1_s1_SII5_silicate_S', '0.012 (33)', '0.012'), ('hl01_fe1_s1_SI2_metal_Cu', '100 (1)', '100'), ('hl01_fe1_s1_SI2_metal_Fe', '0.48 (19)', '0.48'), ('hl01_fe1_s1_SI2_metal_S', '0.13', '0.13'), ('hl01_fe1_s1_SI2_sulfide_Cu', '76.8 (1)', '76.8'), ('hl01_fe1_s1_SI2_sulfide_Fe', '2.07 (9)', '2.07'), ('hl01_fe1_s1_SI2_sulfide_S', '21.2 (0.9)', '21.2'), ('hl01_fe1_s1_SI2_silicate_SiO2', '49.0 (0.2)', '49.0'), ('hl01_fe1_s1_SI2_silicate_MgO', '10.7 (0.9)', '10.7'), ('hl01_fe1_s1_SI2_silicate_Al2O3', '14.3 (0.7)', '14.3'), ('hl01_fe1_s1_SI2_silicate_CaO', '18.6 (0.4)', '18.6'), ('hl01_fe1_s1_SI2_silicate_FeO', '6.30 (0.3)', '6.30'), ('hl01_fe1_s1_SI2_silicate_Cu', '0.080 (11)', '0.080'), ('hl01_fe1_s1_SI2_silicate_S', '<0.008', None), ('hl01_fe1_s1_SI18_metal_Cu', '97.7 (10)', '97.7'), ('hl01_fe1_s1_SI18_metal_Fe', '1.07 (0.9)', '1.07'), ('hl01_fe1_s1_SI18_metal_S', '0.15', '0.15'), ('hl01_fe1_s1_SI18_sulfide_Cu', '74.8 (1)', '74.8'), ('hl01_fe1_s1_SI18_sulfide_Fe', '3.44 (12)', '3.44'), ('hl01_fe1_s1_SI18_sulfide_S', '22.1 (0.9)', '22.1'), ('hl01_fe1_s1_SI18_silicate_SiO2', '48.3 (0.2)', '48.3'), ('hl01_fe1_s1_SI18_silicate_MgO', '9.82 (0.7)', '9.82'), ('hl01_fe1_s1_SI18_silicate_Al2O3', '17.5 (0.6)', '17.5'), ('hl01_fe1_s1_SI18_silicate_CaO', '18.4 (1)', '18.4'), ('hl01_fe1_s1_SI18_silicate_FeO', '5.83 (0.3)', '5.83'), ('hl01_fe1_s1_SI18_silicate_Cu', '0.072 (36)', '0.072'), ('hl01_fe1_s1_SI18_silicate_S', '<0.008', None), ('hl01_fe1_s1_SI14_metal_Cu', '99.5 (40)', '99.5'), ('hl01_fe1_s1_SI14_metal_Fe', '0.44 (70)', '0.44'), ('hl01_fe1_s1_SI14_metal_S', '<0.01', None), ('hl01_fe1_s1_SI14_sulfide_Cu', '70.7 (1)', '70.7'), ('hl01_fe1_s1_SI14_sulfide_Fe', '6.12 (2)', '6.12'), ('hl01_fe1_s1_SI14_sulfide_S', '22.8 (0.9)', '22.8'), ('hl01_fe1_s1_SI14_silicate_SiO2', '48.8 (0.6)', '48.8'), ('hl01_fe1_s1_SI14_silicate_MgO', '10.1 (1)', '10.1'), ('hl01_fe1_s1_SI14_silicate_Al2O3', '17.4 (0.1)', '17.4'), ('hl01_fe1_s1_SI14_silicate_CaO', '18.7 (0.2)', '18.7'), ('hl01_fe1_s1_SI14_silicate_FeO', '5.18 (1)', '5.18'), ('hl01_fe1_s1_SI14_silicate_Cu', '0.112 (63)', '0.112'), ('hl01_fe1_s1_SI14_silicate_S', '0.016 (25)', '0.016'), ('hl01_fe1_s1_SI22_metal_Cu', '98.0 (10)', '98.0'), ('hl01_fe1_s1_SI22_metal_Fe', '1.71 (4)', '1.71'), ('hl01_fe1_s1_SI22_metal_S', '<0.01', None), ('hl01_fe1_s1_SI22_sulfide_Cu', '72.1 (1)', '72.1'), ('hl01_fe1_s1_SI22_sulfide_Fe', '5.54 (3)', '5.54'), ('hl01_fe1_s1_SI22_sulfide_S', '22.9 (0.9)', '22.9'), ('hl01_fe1_s1_SI22_silicate_SiO2', '48.2 (0.4)', '48.2'), ('hl01_fe1_s1_SI22_silicate_MgO', '10.1 (0.4)', '10.1'), ('hl01_fe1_s1_SI22_silicate_Al2O3', '17.1 (0.1)', '17.1'), ('hl01_fe1_s1_SI22_silicate_CaO', '18.6 (0.1)', '18.6'), ('hl01_fe1_s1_SI22_silicate_FeO', '6.13 (0.5)', '6.13'), ('hl01_fe1_s1_SI22_silicate_Cu', '0.080 (11)', '0.080'), ('hl01_fe1_s1_SI22_silicate_S', 'n.d.', None), ('hl01_fe1_s1_SI5_metal_Cu', '100 (1)', '100'), ('hl01_fe1_s1_SI5_metal_Fe', '0.17 (12)', '0.17'), ('hl01_fe1_s1_SI5_metal_S', '<0.01', None), ('hl01_fe1_s1_SI5_sulfide_Cu', '73.3 (1)', '73.3'), ('hl01_fe1_s1_SI5_sulfide_Fe', '5.25 (5)', '5.25'), ('hl01_fe1_s1_SI5_sulfide_S', '22.6 (0.9)', '22.6'), ('hl01_fe1_s1_SI5_silicate_SiO2', '51.0 (0.4)', '51.0'), ('hl01_fe1_s1_SI5_silicate_MgO', '11.1 (0.1)', '11.1'), ('hl01_fe1_s1_SI5_silicate_Al2O3', '16.3 (0.4)', '16.3'), ('hl01_fe1_s1_SI5_silicate_CaO', '19.3 (0.3)', '19.3'), ('hl01_fe1_s1_SI5_silicate_FeO', '2.54 (0.4)', '2.54'), ('hl01_fe1_s1_SI5_silicate_Cu', '0.044 (20)', '0.044'), ('hl01_fe1_s1_SI5_silicate_S', '0.036 (11)', '0.036'), ('hl01_fe1_s1_SII7_metal_Cu', '—', None), ('hl01_fe1_s1_SII7_metal_Fe', '—', None), ('hl01_fe1_s1_SII7_metal_S', '—', None), ('hl01_fe1_s1_SII7_sulfide_Cu', '74.6 (0.9)', '74.6'), ('hl01_fe1_s1_SII7_sulfide_Fe', '4.30 (11)', '4.30'), ('hl01_fe1_s1_SII7_sulfide_S', '22.2 (2)', '22.2'), ('hl01_fe1_s1_SII7_silicate_SiO2', '47.5 (1)', '47.5'), ('hl01_fe1_s1_SII7_silicate_MgO', '9.85 (3)', '9.85'), ('hl01_fe1_s1_SII7_silicate_Al2O3', '16.5 (0.1)', '16.5'), ('hl01_fe1_s1_SII7_silicate_CaO', '18.5 (0.3)', '18.5'), ('hl01_fe1_s1_SII7_silicate_FeO', '5.30 (0.2)', '5.30'), ('hl01_fe1_s1_SII7_silicate_Cu', '0.056 (11)', '0.056'), ('hl01_fe1_s1_SII7_silicate_S', '0.032 (38)', '0.032'), ('hl01_fe1_s1_SII9_metal_Cu', '100 (1)', '100'), ('hl01_fe1_s1_SII9_metal_Fe', '0.69 (32)', '0.69'), ('hl01_fe1_s1_SII9_metal_S', '<0.01', None), ('hl01_fe1_s1_SII9_sulfide_Cu', '71.9 (2)', '71.9'), ('hl01_fe1_s1_SII9_sulfide_Fe', '5.42 (21)', '5.42'), ('hl01_fe1_s1_SII9_sulfide_S', '21.8 (5)', '21.8'), ('hl01_fe1_s1_SII9_silicate_SiO2', '48.8 (1)', '48.8'), ('hl01_fe1_s1_SII9_silicate_MgO', '10.1 (0.3)', '10.1'), ('hl01_fe1_s1_SII9_silicate_Al2O3', '17.1 (0.1)', '17.1'), ('hl01_fe1_s1_SII9_silicate_CaO', '19.0 (0.1)', '19.0'), ('hl01_fe1_s1_SII9_silicate_FeO', '3.42 (0.3)', '3.42'), ('hl01_fe1_s1_SII9_silicate_Cu', '0.080 (45)', '0.080'), ('hl01_fe1_s1_SII9_silicate_S', '0.024 (33)', '0.024')]

# (run id, tokens that must co-occur on the single pdftotext line for that run)
RUN_CHECKS = [{'run': 'C-C-1', 'tokens': ['-9.19', '0.327 (12)', '50.4 (0.8)']}, {'run': 'C-C-9', 'tokens': ['-10.66', '0.185 (20)', '50.5 (0.4)']}, {'run': 'C-C-4', 'tokens': ['-11.34', '0.109 (17)', '50.1 (0.4)']}, {'run': 'C-C-3', 'tokens': ['-12.43', '0.041 (29)', '50.6 (0.6)']}, {'run': 'C-C-2', 'tokens': ['-13.55', '0.036 (22)', '50.4 (0.8)']}, {'run': 'C-C-6', 'tokens': ['-10.20', '0.136 (14)', '46.4 (0.6)']}, {'run': 'C-C-8', 'tokens': ['-9.66', '0.162 (45)', '41.5 (2)']}, {'run': 'C-C-7', 'tokens': ['-9.10', '0.250 (12)', '39.3 (0.8)']}, {'run': 'C-C-1', 'tokens': ['-9.19', '0.355 (10)', '48.4 (0.6)']}, {'run': 'C-C-9', 'tokens': ['-10.66', '0.159 (19)', '47.3 (0.6)']}, {'run': 'C-C-4', 'tokens': ['-11.34', '0.098 (48)', '48.0 (0.6)']}, {'run': 'C-C-3', 'tokens': ['-12.43', '0.077 (31)', '48.2 (0.2)']}, {'run': 'C-C-2', 'tokens': ['-13.55', '0.053 (26)', '48.0 (0.6)']}, {'run': 'C-C-6', 'tokens': ['-10.20', '0.116 (33)', '42.2 (0.5)']}, {'run': 'C-C-7', 'tokens': ['-9.10', '0.256 (18)', '40.6 (0.5)']}, {'run': 'S-II-5', 'tokens': ['-9.52', '0.337 (8)', '49.1 (1)']}, {'run': 'S-I-2', 'tokens': ['-11.71', '0.126 (13)', '50.6 (0.4)']}, {'run': 'S-I-18', 'tokens': ['-11.72', '0.116 (100)', '50.2 (0.4)']}, {'run': 'S-I-23', 'tokens': ['-11.75', '0.097 (19)', '50.0 (0.6)']}, {'run': 'S-I-25', 'tokens': ['-11.78', '0.089 (10)', '49.7 (0.4)']}, {'run': 'S-I-22', 'tokens': ['-11.83', '0.089 (10)', '50.5 (0.6)']}, {'run': 'S-I-24', 'tokens': ['-11.91', '0.106 (10)', '49.6 (0.1)']}, {'run': 'S-II-7', 'tokens': ['-12.18', '0.071 (10)', '49.3 (1)']}, {'run': 'S-II-8', 'tokens': ['-12.63', '0.054 (9)', '49.3 (0.1)']}, {'run': 'S-II-5', 'tokens': ['-9.52', '0.208 (8)', '46.7 (0.4)']}, {'run': 'S-I-2', 'tokens': ['-11.71', '0.080 (11)', '49.0 (0.2)']}, {'run': 'S-I-18', 'tokens': ['-11.72', '0.072 (36)', '48.3 (0.2)']}, {'run': 'S-I-14', 'tokens': ['-11.74', '0.112 (63)', '48.8 (0.6)']}, {'run': 'S-I-22', 'tokens': ['-11.83', '0.080 (11)', '48.2 (0.4)']}, {'run': 'S-I-5', 'tokens': ['-11.86', '0.044 (20)', '51.0 (0.4)']}, {'run': 'S-II-7', 'tokens': ['-12.18', '0.056 (11)', '47.5 (1)']}, {'run': 'S-II-9', 'tokens': ['-12.47', '0.080 (45)', '48.8 (1)']}]


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _norm(text):
    return text.translate(str.maketrans({
        "\u2afa": "-",
        "\u2af9": "+",
        "\u2b0d": "<",
        "\u2212": "-",
    }))


def _rows(document):
    rows = {}
    for container, block in document["species"].items():
        for row in block.get("observations") or []:
            rows[row["observation_id"]] = (container, row)
        for row in block.get("context") or []:
            rows[row["observation_id"]] = (container, row)
    return rows


def test_printed_cells_bind_to_the_run_line():
    if not TEXT.is_file():
        pytest.skip(f"corpus source text absent: {TEXT}")
    document = _document()
    rows = _rows(document)
    text = _norm(TEXT.read_text(encoding="utf-8"))
    lines = [line.strip() for line in text.splitlines()]
    for check in RUN_CHECKS:
        run_id = check["run"]
        tokens = check["tokens"]
        hits = [
            line for line in lines
            if line.split()[:1] == [run_id] and all(token in line for token in tokens)
        ]
        assert len(hits) == 1, (run_id, tokens, hits)
    for observation_id, snippet, point in PINS:
        assert snippet in text, (observation_id, snippet)
        _container, row = rows[observation_id]
        published = row["values"].get("value_as_published") or row["values"].get("value")
        assert published == snippet
        if point is None:
            assert "value" not in row["values"]
            assert row["values"]["unavailable"]["verbatim"] == snippet
        else:
            assert row["values"]["value"] == point


def test_species_temperature_and_oxygen(tmp_path):
    document = _document()
    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    rows = _rows(document)
    by_source = {}
    for observation in result.observations.values():
        source_id = observation.observation_id.split("::", 1)[-1]
        by_source[source_id] = observation
    assert set(by_source) == {
        oid for oid, row in (
            (row["observation_id"], row)
            for block in document["species"].values()
            for row in block.get("observations") or []
        )
    }
    for observation_id, observation in by_source.items():
        container, row = rows[observation_id]
        expected = row["values"].get("formula") or container
        assert observation.identity.species.formula == expected
        phase = row["phase"]
        expected_phase = {"glass": Phase.GLASS, "l": Phase.L}[phase]
        assert observation.identity.species.phase.value is expected_phase
        locator = row["locator"]
        assert locator["page"] == locator["pdf_page_index"] + 1
        if observation.uncertainty is not None and observation.uncertainty.value not in (None,):
            assert observation.uncertainty.value != 0
        raw_t = row["values"].get("T_C")
        conditions = observation.point_conditions or {}
        if raw_t is not None:
            assert conditions["temperature_K"].state.is_value
            assert conditions["temperature_K"].state.value == Decimal(str(raw_t)) + Decimal("273.15")
            assert observation.identity.temperature_K is not None
            assert observation.identity.temperature_K.is_value
            assert observation.identity.temperature_K.value == Decimal(str(raw_t)) + Decimal("273.15")
        if row["values"].get("quantity") == "equilibrium_phase_composition":
            assert observation.identity.fO2_Pa is not None
            assert observation.identity.fO2_Pa.is_not_applicable
            if "fO2_log" in (row.get("point_conditions") or {}):
                assert conditions["fO2_log"].state.is_value
                assert str(conditions["fO2_log"].state.value) == str(
                    row["point_conditions"]["fO2_log"]["state"]["value"]
                )
    recommended = by_source["hl01_g_cuo05_recommended"]
    assert recommended.identity.species.formula == "CuO0.5"
    assert recommended.value.point == Decimal("10")
    assert recommended.identity.reference_state is not None
    assert recommended.identity.reference_state.is_value
    starting = by_source["hl01_t1_AD_FeO"]
    assert starting.value.point == Decimal("0")
    assert starting.identity.temperature_K is not None
    assert starting.identity.temperature_K.is_unknown
    bound = by_source["hl01_fe0_s1_SII5_metal_S"]
    assert bound.value.kind.value == "unavailable"


def test_table3_and_fits_stay_in_context_and_match_the_text():
    document = _document()
    rows = _rows(document)
    cu = rows["hl01_t3_fe0_s0_CC1_Cu"][1]
    assert cu["type"] == "partition_coefficient_row"
    assert cu["values"]["Dmet_sil_Cu"] == "306"
    valence = rows["hl01_t4_01"][1]
    assert valence["values"]["formal_valence"] == "0.96 ± 0.13"
    asymmetric = rows["hl01_t4_06"][1]
    assert asymmetric["values"]["formal_valence"] == "0.64 + 0.28"
    caption = rows["hl01_t5_01"][1]
    assert caption["values"]["fO2_window_as_printed"] == "IW-0.5 to IW-06"
    altman = rows["hl01_t6_16"][1]
    assert altman["values"]["c2_for_fO2"] == "−0.72–0.03"
    assert document["figure_policy"]
    figures = [
        row
        for block in document["species"].values()
        for row in block.get("context") or []
        if row["type"] == "figure_record"
    ]
    assert len(figures) == 6
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"]["numeric_points"] == "none" for row in figures)
    if not TEXT.is_file():
        pytest.skip(f"corpus source text absent: {TEXT}")
    text = _norm(TEXT.read_text(encoding="utf-8"))
    assert "306" in text
    assert "0.96" in text
