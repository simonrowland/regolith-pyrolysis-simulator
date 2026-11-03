"""Full cell pins and isolated migration for Doyle (1989)."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT, wt_pct_to_mole_fraction
from simulator.battery.oxygen_identity import log10_fo2_to_pa
from tools.validate_literature_extracts import validate_extract_document

EXTRACT = REPO_ROOT / "data/literature/extracts/doyle-1989-tio2-feo-activity-magma.yaml"
SOURCE_ID = "doyle-1989-tio2-feo-activity-magma"
OXIDES = ("SiO2", "TiO2", "Al2O3", "FeO", "CaO", "K2O")
TABLE1A_ROWS = (('4A', '1', '0.20', '48.0', '5.1', '9.3', '31.3', '5.6', '0.0', 'Lo'),
 ('4A', '2', '0.20', '50.1', '3.0', '9.6', '31.3', '5.7', '0.0', 'Lo'),
 ('4A', '3', '0.20', '44.8', '10.0', '9.1', '30.6', '5.4', '0.0', 'Lo'),
 ('4A', '4', '0.20', '50.2', '2.1', '9.3', '32.2', '5.6', '0.0', 'Lo'),
 ('4A', '5', '0.20', '35.1', '18.1', '9.0', '34.7', '4.9', '0.0', 'Lo'),
 ('4A', '6', '0.20', '39.5', '14.4', '9.1', '33.2', '5.3', '0.0', 'Lo'),
 ('4A', '7', '0.20', '47.1', '8.9', '10.0', '28.8', '5.7', '0.0', 'Lo'),
 ('4A', '8', '0.20', '50.4', '1.9', '9.2', '32.9', '5.5', '0.0', 'Hi'),
 ('4A', '9', '0.20', '38.7', '14.2', '8.9', '34.4', '5.1', '0.0', 'Hi'),
 ('4A', '10', '0.30', '44.4', '2.3', '7.8', '40.7', '4.7', '0.0', 'Lo'),
 ('4A', '11', '0.30', '37.7', '7.4', '7.5', '42.7', '4.5', '0.0', 'Lo'),
 ('4A', '12', '0.30', '40.8', '5.0', '7.6', '42.0', '4.5', '0.0', 'Lo'),
 ('4A', '13', '0.30', '35.9', '8.9', '7.7', '43.2', '4.4', '0.0', 'Lo'),
 ('4A', '14', '0.30', '42.4', '3.5', '8.1', '41.5', '4.7', '0.0', 'Lo'),
 ('4A', '15', '0.30', '44.8', '1.8', '8.3', '41.3', '4.8', '0.0', 'Lo'),
 ('4A', '16', '0.30', '41.6', '4.5', '8.1', '41.8', '4.6', '0.0', 'Lo'),
 ('4A', '17', '0.30', '34.0', '10.2', '7.3', '44.8', '4.5', '0.0', 'Hi'),
 ('4A', '18', '0.30', '38.3', '6.4', '7.4', '44.5', '4.5', '0.0', 'Hi'),
 ('4A', '19', '0.30', '33.8', '11.1', '7.7', '43.6', '4.4', '0.0', 'Hi'),
 ('4A', '20', '0.30', '41.2', '4.1', '8.2', '41.8', '4.6', '0.0', 'Hi'),
 ('4A', '21', '0.30', '31.8', '12.9', '7.7', '44.3', '4.3', '0.0', 'Hi'),
 ('4A', '22', '0.40', '34.8', '4.1', '6.6', '52.0', '4.0', '0.0', 'Lo'),
 ('4A', '23', '0.40', '31.5', '7.3', '6.4', '52.2', '3.9', '0.0', 'Lo'),
 ('4A', '24', '0.40', '35.4', '2.7', '6.6', '51.9', '4.0', '0.0', 'Lo'),
 ('4A', '25', '0.40', '38.0', '0.7', '6.6', '51.3', '4.0', '0.0', 'Lo'),
 ('4A', '26', '0.40', '37.2', '1.7', '7.0', '51.4', '4.0', '0.0', 'Lo'),
 ('4A', '27', '0.40', '38.3', '0.8', '6.9', '50.9', '4.0', '0.0', 'Lo'),
 ('4A', '28', '0.40', '34.5', '4.3', '6.8', '51.5', '4.0', '0.0', 'Lo'),
 ('4A', '29', '0.40', '34.6', '4.2', '6.9', '51.1', '4.0', '0.0', 'Lo'),
 ('4A', '30', '0.40', '29.0', '10.0', '6.7', '51.5', '3.8', '0.0', 'Lo'),
 ('4A', '31', '0.40', '32.7', '6.1', '6.6', '52.0', '3.9', '0.0', 'Hi'),
 ('4A', '32', '0.40', '29.4', '9.6', '6.4', '52.3', '3.9', '0.0', 'Hi'),
 ('4A', '33', '0.40', '36.1', '2.5', '6.7', '51.9', '4.0', '0.0', 'Hi'),
 ('4A', '34', '0.40', '37.3', '1.4', '6.8', '51.4', '4.0', '0.0', 'Hi'),
 ('4A', '35', '0.40', '28.3', '12.1', '6.7', '51.2', '4.0', '0.0', 'Hi'),
 ('4A', '36', '0.40', '34.8', '3.9', '6.8', '51.1', '3.8', '0.0', 'Hi'),
 ('4A', '37', '0.40', '29.3', '10.6', '6.7', '51.8', '3.8', '0.0', 'Hi'),
 ('4A', '38', '0.50', '32.0', '2.3', '5.9', '59.2', '3.6', '0.0', 'Lo'),
 ('4A', '39', '0.50', '30.8', '3.1', '5.9', '59.5', '3.6', '0.0', 'Lo'),
 ('4A', '40', '0.50', '32.6', '1.5', '5.6', '58.9', '3.4', '0.0', 'Lo'),
 ('4A', '41', '0.50', '30.2', '3.7', '5.7', '59.0', '3.3', '0.0', 'Lo'),
 ('4A', '42', '0.50', '28.5', '5.3', '5.9', '59.8', '3.2', '0.0', 'Lo'),
 ('4A', '43', '0.50', '27.5', '7.1', '6.0', '57.4', '3.6', '0.0', 'Lo'),
 ('4A', '44', '0.50', '27.8', '6.5', '5.9', '58.5', '3.6', '0.0', 'Lo'),
 ('4A', '45', '0.50', '32.5', '1.4', '6.1', '58.7', '3.5', '0.0', 'Lo'),
 ('4A', '46', '0.50', '28.7', '4.6', '5.5', '61.4', '3.0', '0.0', 'Hi'),
 ('4A', '47', '0.50', '30.2', '3.9', '6.1', '59.0', '3.5', '0.0', 'Hi'),
 ('4A', '48', '0.50', '31.0', '2.8', '5.9', '58.7', '3.5', '0.0', 'Hi'),
 ('4A', '49', '0.50', '31.0', '2.8', '6.2', '59.0', '3.5', '0.0', 'Hi'),
 ('4A', '50', '0.50', '26.9', '9.8', '6.0', '58.1', '3.6', '0.0', 'Hi'),
 ('4A', '51', '0.60', '26.7', '3.8', '5.9', '62.7', '3.9', '0.0', 'Hi'),
 ('4A', '52', '0.60', '26.9', '3.0', '6.1', '63.5', '3.4', '0.0', 'Hi'),
 ('4A', '53', '0.60', '24.6', '4.6', '6.5', '63.9', '2.6', '0.0', 'Hi'),
 ('4A', '54', '0.60', '25.8', '5.1', '5.1', '63.1', '3.3', '0.0', 'Hi'),
 ('4A', '55', '0.60', '27.3', '2.4', '5.5', '64.5', '3.3', '0.0', 'Hi'),
 ('4A', '56', '0.60', '25.0', '5.4', '5.8', '63.1', '3.1', '0.0', 'Hi'),
 ('4A', '57', '0.60', '27.0', '4.1', '6.1', '62.3', '3.7', '0.0', 'Hi'),
 ('4A', '58', '0.60', '24.0', '5.6', '6.6', '63.4', '3.0', '0.0', 'Hi'),
 ('4B', '1', '0.20', '56.8', '3.0', '14.4', '15.9', '5.8', '4.1', 'Lo'),
 ('4B', '2', '0.20', '51.8', '5.4', '14.2', '19.2', '6.4', '3.3', 'Lo'),
 ('4B', '3', '0.20', '45.3', '10.4', '13.6', '23.2', '6.1', '3.1', 'Lo'),
 ('4B', '4', '0.20', '42.9', '9.9', '13.1', '27.2', '5.8', '1.1', 'Lo'),
 ('4B', '5', '0.20', '46.7', '9.4', '13.8', '22.6', '6.2', '3.1', 'Lo'),
 ('4B', '6', '0.20', '54.5', '1.8', '14.5', '19.5', '6.2', '3.3', 'Lo'),
 ('4B', '7', '0.20', '50.6', '6.8', '14.2', '20.6', '6.4', '3.3', 'Lo'),
 ('4B', '8', '0.20', '52.9', '2.9', '14.4', '19.9', '6.5', '3.1', 'Lo'),
 ('4B', '9', '0.20', '52.4', '4.2', '14.2', '19.0', '6.3', '3.0', 'Hi'),
 ('4B', '10', '0.20', '51.8', '5.4', '14.2', '20.6', '6.1', '3.0', 'Hi'),
 ('4B', '11', '0.20', '35.5', '17.3', '12.7', '27.8', '5.6', '2.0', 'Hi'),
 ('4B', '12', '0.20', '40.1', '13.5', '13.2', '27.5', '5.8', '1.8', 'Hi'),
 ('4B', '13', '0.30', '47.2', '2.7', '12.2', '29.8', '5.3', '2.9', 'Lo'),
 ('4B', '14', '0.30', '44.6', '4.4', '11.9', '31.1', '5.3', '2.8', 'Lo'),
 ('4B', '15', '0.30', '48.7', '1.7', '12.7', '28.5', '5.7', '3.0', 'Lo'),
 ('4B', '16', '0.30', '38.6', '8.6', '11.2', '37.0', '5.1', '1.9', 'Lo'),
 ('4B', '17', '0.30', '47.1', '2.3', '12.4', '30.1', '5.4', '3.0', 'Lo'),
 ('4B', '18', '0.30', '42.2', '5.7', '12.0', '32.9', '5.2', '2.6', 'Lo'),
 ('4B', '19', '0.30', '47.3', '2.4', '12.5', '30.3', '5.4', '2.8', 'Lo'),
 ('4B', '20', '0.30', '32.6', '13.0', '10.8', '39.1', '4.8', '1.5', 'Lo'),
 ('4B', '21', '0.30', '40.7', '6.3', '11.8', '33.5', '5.1', '2.4', 'Lo'),
 ('4B', '22', '0.30', '31.3', '14.6', '10.9', '38.4', '4.8', '1.6', 'Hi'),
 ('4B', '23', '0.40', '36.4', '4.1', '9.9', '45.5', '4.4', '1.5', 'Lo'),
 ('4B', '24', '0.40', '40.0', '1.4', '10.3', '43.7', '4.6', '2.0', 'Lo'),
 ('4B', '25', '0.40', '39.1', '2.4', '10.2', '43.7', '4.6', '1.9', 'Lo'),
 ('4B', '26', '0.40', '33.9', '5.6', '9.6', '46.3', '4.2', '1.1', 'Lo'),
 ('4B', '27', '0.40', '35.5', '5.3', '10.1', '45.3', '4.4', '1.8', 'Lo'),
 ('4B', '28', '0.40', '30.0', '10.9', '9.6', '47.2', '4.3', '1.1', 'Lo'),
 ('4B', '29', '0.40', '38.0', '3.0', '10.2', '44.3', '4.6', '1.7', 'Lo'),
 ('4B', '30', '0.40', '36.5', '4.8', '10.1', '44.9', '4.6', '1.9', 'Lo'),
 ('4B', '31', '0.40', '37.0', '3.1', '10.0', '46.1', '4.4', '1.6', 'Lo'),
 ('4B', '32', '0.40', '29.3', '7.6', '9.5', '51.2', '3.9', '1.5', 'Hi'),
 ('4B', '33', '0.40', '34.5', '5.1', '9.2', '49.4', '4.1', '1.1', 'Hi'),
 ('4B', '34', '0.40', '40.6', '1.3', '10.5', '42.6', '4.6', '2.0', 'Hi'),
 ('4B', '35', '0.40', '31.2', '9.1', '9.7', '47.6', '4.3', '0.9', 'Hi'),
 ('4B', '36', '0.40', '28.8', '10.9', '9.4', '49.2', '4.2', '0.4', 'Hi'),
 ('4B', '37', '0.50', '28.9', '5.3', '8.6', '53.9', '3.8', '0.6', 'Lo'),
 ('4B', '38', '0.50', '30.4', '3.6', '8.9', '52.5', '3.9', '0.8', 'Lo'),
 ('4B', '39', '0.50', '32.4', '2.1', '8.8', '53.2', '3.9', '1.0', 'Lo'),
 ('4B', '40', '0.50', '28.0', '6.7', '8.4', '53.1', '4.0', '0.5', 'Lo'),
 ('4B', '41', '0.50', '28.1', '6.5', '8.5', '53.9', '3.6', '0.7', 'Lo'),
 ('4B', '42', '0.50', '29.9', '4.5', '9.0', '53.8', '3.9', '0.8', 'Lo'),
 ('4B', '43', '0.50', '32.3', '2.5', '9.0', '52.3', '4.0', '1.1', 'Lo'),
 ('4B', '44', '0.50', '26.9', '6.7', '8.4', '54.4', '3.7', '0.2', 'Hi'),
 ('4B', '45', '0.50', '28.9', '4.6', '8.9', '54.5', '3.9', '0.3', 'Hi'),
 ('4B', '46', '0.50', '32.7', '1.2', '8.5', '54.0', '3.8', '0.5', 'Hi'),
 ('4B', '47', '0.50', '31.6', '2.3', '8.6', '53.8', '3.9', '0.6', 'Hi'),
 ('4B', '48', '0.50', '25.9', '6.5', '11.4', '53.2', '3.1', '0.3', 'Hi'),
 ('4B', '49', '0.50', '29.6', '3.6', '8.9', '54.8', '3.5', '0.4', 'Hi'),
 ('4B', '50', '0.50', '25.4', '8.5', '8.5', '55.5', '3.6', '0.1', 'Hi'),
 ('4C', '1', '0.20', '42.5', '10.1', '16.6', '18.7', '6.1', '5.4', 'Lo'),
 ('4C', '2', '0.20', '51.7', '2.7', '18.1', '13.5', '6.4', '6.4', 'Lo'),
 ('4C', '3', '0.20', '48.3', '5.3', '17.3', '16.3', '6.3', '6.0', 'Lo'),
 ('4C', '4', '0.20', '52.9', '1.2', '18.0', '13.0', '6.4', '6.5', 'Lo'),
 ('4C', '5', '0.20', '48.6', '4.1', '17.1', '17.5', '6.5', '5.5', 'Lo'),
 ('4C', '6', '0.20', '47.5', '6.0', '17.0', '16.5', '6.2', '6.2', 'Lo'),
 ('4C', '7', '0.20', '36.0', '15.6', '15.8', '21.3', '5.8', '4.9', 'Lo'),
 ('4C', '8', '0.20', '46.2', '6.4', '16.8', '17.0', '6.2', '5.8', 'Lo'),
 ('4C', '9', '0.20', '52.6', '1.6', '17.7', '13.9', '6.4', '6.3', 'Lo'),
 ('4C', '10', '0.20', '43.5', '8.0', '16.6', '20.1', '6.1', '5.1', 'Hi'),
 ('4C', '11', '0.20', '49.9', '3.8', '17.7', '15.3', '6.2', '5.6', 'Hi'),
 ('4C', '12', '0.20', '46.6', '6.7', '16.9', '16.6', '6.1', '5.6', 'Hi'),
 ('4C', '13', '0.20', '33.8', '16.8', '15.6', '24.1', '5.7', '3.8', 'Hi'),
 ('4C', '14', '0.20', '38.1', '13.5', '15.9', '21.6', '5.8', '4.4', 'Hi'),
 ('4C', '15', '0.30', '43.4', '4.6', '15.6', '26.1', '5.6', '5.1', 'Lo'),
 ('4C', '16', '0.30', '45.4', '2.5', '15.8', '25.2', '5.7', '5.2', 'Lo'),
 ('4C', '17', '0.30', '47.5', '1.2', '16.0', '23.6', '5.7', '5.6', 'Lo'),
 ('4C', '18', '0.30', '37.9', '8.8', '14.6', '30.9', '5.2', '3.9', 'Lo'),
 ('4C', '19', '0.30', '32.3', '13.8', '13.8', '33.7', '5.0', '3.3', 'Lo'),
 ('4C', '20', '0.30', '46.4', '2.2', '15.6', '24.5', '5.6', '5.4', 'Lo'),
 ('4C', '21', '0.30', '42.5', '5.2', '15.3', '26.9', '5.6', '4.8', 'Lo'),
 ('4C', '22', '0.30', '44.3', '4.1', '15.7', '25.6', '5.5', '5.5', 'Lo'),
 ('4C', '23', '0.30', '45.2', '3.2', '15.7', '24.7', '5.6', '5.3', 'Lo'),
 ('4C', '24', '0.30', '45.7', '2.2', '15.7', '26.0', '5.5', '4.2', 'Hi'),
 ('4C', '25', '0.30', '42.7', '4.2', '15.2', '28.0', '4.6', '4.1', 'Hi'),
 ('4C', '26', '0.30', '34.6', '11.2', '14.1', '32.8', '5.3', '3.6', 'Hi'),
 ('4C', '27', '0.30', '41.0', '5.3', '15.1', '27.6', '5.5', '4.6', 'Hi'),
 ('4C', '28', '0.40', '39.6', '2.3', '13.9', '35.4', '5.0', '4.3', 'Lo'),
 ('4C', '29', '0.40', '41.8', '1.1', '14.1', '33.5', '5.1', '4.8', 'Lo'),
 ('4C', '30', '0.40', '37.1', '4.2', '13.7', '36.4', '4.9', '3.9', 'Lo'),
 ('4C', '31', '0.40', '32.7', '7.7', '12.7', '41.2', '4.6', '2.9', 'Lo'),
 ('4C', '32', '0.40', '29.0', '11.5', '12.5', '41.6', '4.6', '2.6', 'Lo'),
 ('4C', '33', '0.40', '41.3', '1.8', '14.0', '34.0', '5.1', '4.7', 'Lo'),
 ('4C', '34', '0.40', '38.3', '4.0', '13.6', '35.9', '4.9', '4.3', 'Lo'),
 ('4C', '35', '0.40', '35.9', '5.7', '13.3', '37.6', '4.8', '3.8', 'Lo'),
 ('4C', '36', '0.40', '31.3', '9.2', '12.5', '42.0', '4.7', '2.8', 'Lo'),
 ('4C', '37', '0.40', '29.2', '9.6', '12.2', '44.9', '4.4', '1.5', 'Hi'),
 ('4C', '38', '0.40', '38.8', '2.6', '13.8', '37.8', '4.9', '3.6', 'Hi'),
 ('4C', '39', '0.40', '37.0', '3.6', '13.3', '38.9', '4.8', '3.1', 'Hi'),
 ('4C', '40', '0.40', '34.5', '5.3', '12.9', '41.2', '4.7', '2.6', 'Hi'),
 ('4C', '41', '0.40', '38.9', '2.4', '13.6', '36.7', '4.9', '3.9', 'Hi'),
 ('4C', '42', '0.40', '43.0', '0.7', '14.3', '32.8', '5.2', '4.9', 'Hi'),
 ('4C', '43', '0.40', '27.1', '12.3', '11.9', '45.5', '4.4', '1.2', 'Hi'),
 ('4C', '44', '0.50', '28.8', '5.9', '10.9', '50.6', '4.2', '1.6', 'Lo'),
 ('4C', '45', '0.50', '33.1', '1.9', '11.3', '48.1', '4.2', '2.5', 'Lo'),
 ('4C', '46', '0.50', '32.3', '2.3', '11.4', '49.0', '4.1', '2.2', 'Lo'),
 ('4C', '47', '0.50', '30.7', '4.1', '11.0', '49.5', '4.1', '2.1', 'Lo'),
 ('4C', '48', '0.50', '33.8', '1.8', '11.8', '46.7', '4.2', '3.0', 'Lo'),
 ('4C', '49', '0.50', '34.2', '1.3', '11.8', '46.9', '4.3', '2.7', 'Lo'),
 ('4C', '50', '0.50', '27.8', '7.7', '10.9', '49.8', '4.1', '2.2', 'Lo'),
 ('4C', '51', '0.50', '31.0', '3.9', '11.2', '48.5', '4.1', '2.7', 'Lo'),
 ('4C', '52', '0.50', '30.2', '4.8', '11.2', '49.5', '4.1', '2.3', 'Lo'),
 ('4C', '53', '0.50', '33.2', '1.4', '11.4', '48.7', '4.1', '2.3', 'Hi'),
 ('4C', '54', '0.50', '32.0', '3.1', '11.3', '49.1', '4.2', '2.3', 'Hi'),
 ('4C', '55', '0.50', '29.5', '4.8', '10.9', '50.6', '4.1', '1.6', 'Hi'),
 ('4C', '56', '0.50', '26.8', '7.6', '10.4', '51.2', '4.0', '1.8', 'Hi'),
 ('4C', '57', '0.50', '24.7', '9.8', '10.3', '53.2', '4.0', '0.9', 'Hi'),
 ('4C', '58', '0.50', '25.5', '8.7', '10.4', '52.4', '3.9', '1.0', 'Hi'),
 ('4C', '59', '0.50', '31.7', '3.0', '11.4', '48.5', '4.1', '2.3', 'Hi'))
TABLE1B_ROWS = (('1', '0.20', '0.01', '11.92', '15.0', '100.0', '150.0'),
 ('2', '0.30', '0.01', '11.57', '21.6', '100.0', '150.0'),
 ('3', '0.40', '0.02', '11.32', '27.6', '100.0', '150.0'),
 ('4', '0.50', '0.02', '11.12', '33.6', '100.0', '150.0'),
 ('5', '0.60', '0.02', '10.96', '39.0', '100.0', '150.0'))
TABLE2_ROWS = (('1', 'Tholeiitic Basalt', 'a', 'a - Nockolds (1954)', '0.74', '0.74', '0.02', '0.08', '0.16'),
 ('2', 'Mid-Ocean Ridge Basalt', 'a', 'a - Nockolds (1954)', '0.73', '0.72', '0.02', '0.08', '0.18'),
 ('3', 'Alkali Basalt', 'a', 'a - Nockolds (1954)', '0.69', '0.70', '0.03', '0.11', '0.18'),
 ('4', 'Calc Alkali Basalt', 'a', 'a - Nockolds (1954)', '0.79', '0.70', '0.01', '0.09', '0.20'),
 ('5', 'Basaltic Andesite', 'a', 'a - Nockolds (1954)', '0.83', '0.71', '0.01', '0.10', '0.19'),
 ('6', 'Tholeiitic Picrite', 'a', 'a - Nockolds (1954)', '0.59', '0.78', '0.02', '0.05', '0.15'),
 ('7', 'Komatiite', 'b', 'b - Arndt (1977)', '0.50', '0.83', '0.01', '0.01', '0.15'),
 ('8', 'Low-TiO2 Mare Basalt', 'c', 'c - Longhi et al. (1978)', '0.60', '0.79', '0.03', '0.01', '0.17'),
 ('9', 'High-TiO2 Mare Basalt', 'c', 'c - Longhi et al. (1978)', '0.59', '0.66', '0.17', '0.01', '0.16'))


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _by_id(rows: list[dict], observation_id: str) -> dict:
    return next(row for row in rows if row["observation_id"] == observation_id)


def _table1a_id(panel: str, row: str) -> str:
    return f"doyle_1989_table1a_fig{panel.lower()}_r{int(row):02d}"


def _setpoint_id(activity: str) -> str:
    return f"doyle_1989_table1b_setpoint_{activity.replace('.', '')}"


def _assert_source_matches_every_render_pin(document: dict) -> None:
    species = document["species"]["FeO"]
    observations = species["observations"]
    context = species["context"]
    table1a = [row for row in observations if row["observation_id"].startswith("doyle_1989_table1a_")]
    assert len(table1a) == len(TABLE1A_ROWS) == 167
    assert [row["observation_id"] for row in table1a] == [
        _table1a_id(panel, row_number) for panel, row_number, *_ in TABLE1A_ROWS
    ]
    context_by_id = {row["observation_id"]: row for row in context}
    observations_by_id = {row["observation_id"]: row for row in observations}
    table1b_context = {
        row["observation_id"]: row for row in context
        if row["observation_id"].startswith("doyle_1989_table1b_setpoint_")
    }
    assert len(table1b_context) == len(TABLE1B_ROWS) == 5

    setpoints = {activity: (row_number, uncertainty, log_fo2, co2, h2, n2)
                 for row_number, activity, uncertainty, log_fo2, co2, h2, n2 in TABLE1B_ROWS}
    for expected in TABLE1A_ROWS:
        panel, row_number, activity, *tail = expected
        composition = tail[:6]
        starting_material = tail[6]
        source = observations_by_id[_table1a_id(panel, row_number)]
        assert source["type"] == "activity_coefficient"
        assert source["locator"] == {
            "page": 4, "pdf_page_index": 3, "published_page": 2634,
            "table": "1A", "row": row_number, "panel": panel,
        }
        values = source["values"]
        assert values["activity"] == activity
        assert values["activity_as_printed"] == activity
        assert values["activity_cell_suffix_as_printed"] == "no suffix printed"
        assert values["fO2_uncertainty_as_printed"] == "±0.02"
        assert values["fO2_uncertainty_confidence"] == "confidence level not stated"
        assert values["activity_form_as_printed"] == "a_FeO"
        assert values["composition_wt_percent_as_printed"] == dict(zip(OXIDES, composition, strict=True))
        assert values["starting_material_FeO_relative_as_printed"] == starting_material
        assert values["T_K"] == "1600"
        assert values["temperature_as_printed"] == "1600±2 K"
        assert values["temperature_uncertainty_K"] == "2"
        assert values["standard_state_as_printed"] == "molten Fe0.947O"
        assert values["standard_state_schema_gap"].endswith("simulator/battery/enums.py::ReferenceStateConvention.")
        assert values["method_class"] == values["evidence_class"] == "measured_reduced"
        assert source["phase"] == "l"

        conditions = source["point_conditions"]
        temperature = conditions["T_K"]
        assert temperature["state"]["value"] == {"kind": "point", "point": "1600"}
        assert temperature["locator"]["page"] == 4 and temperature["locator"]["table"] == "1A"
        raw_composition = conditions["composition"]["state"]["value"]
        assert raw_composition["amount_basis"] == "mass_percent"
        assert raw_composition["components"] == [[oxide, value] for oxide, value in zip(OXIDES, composition, strict=True)]
        assert conditions["composition"]["locator"]["row"] == row_number
        assert conditions["total_pressure_Pa"]["state"] == {
            "tag": "unknown", "reason": "The paper does not state the experimental total pressure."
        }

        setpoint = setpoints[activity]
        table1b_row, uncertainty, positive_log, co2, h2, n2 = setpoint
        parent_id = _setpoint_id(activity)
        assert source["derived_from"] == [f"{SOURCE_ID}::context::{parent_id}"]
        assert source["derivation"]["inputs"] == source["derived_from"]
        assert "Eq. (8)" in source["derivation"]["relation"]
        assert source["derivation"]["locator"]["page"] == 3
        assert source["derivation"]["locator"]["equation"] == "8"
        assert source["uncertainty"]["value"] == uncertainty
        assert source["uncertainty"]["verbatim"] == f"±{uncertainty}; confidence level not stated"
        assert source["uncertainty"]["locator"]["page"] == 6
        fugacity = conditions["fO2_log"]
        assert fugacity["uncertainty_as_printed"] == "±0.02"
        assert fugacity["uncertainty_note"] == "confidence level not stated"
        assert fugacity["uncertainty_locator"]["page"] == 6
        assert fugacity["uncertainty_locator"]["table"] == "1B"
        assert "Shared header ±0.02 applies to every -log10 fO2 row." in fugacity["uncertainty_locator"]["note"]
        assert fugacity["state"]["value"] == {"kind": "point", "point": f"-{positive_log}"}
        assert fugacity["locator"]["page"] == 6
        assert fugacity["locator"]["table"] == "1B"
        assert fugacity["locator"]["row"] == table1b_row
        assert f"exact printed a_FeO key {activity}" in fugacity["locator"]["note"]
        assert "no pressure reference" in fugacity["locator"]["note"]
        parent = table1b_context[parent_id]
        assert parent["type"] == "typed_context"
        assert parent["locator"]["page"] == 6 and parent["locator"]["table"] == "1B"
        parent_values = parent["values"]
        assert parent_values["activity_as_printed"] == activity
        assert parent_values["uncertainty_as_printed"] == uncertainty
        assert parent_values["minus_log10_fO2_as_printed"] == positive_log
        assert parent_values["fO2_as_printed"] == f"-log10 fO2 = {positive_log}±0.02"
        assert parent_values["fO2_uncertainty_as_printed"] == "±0.02"
        assert parent_values["fO2_uncertainty_confidence"] == "confidence level not stated"
        assert parent_values["fO2_log"]["uncertainty_as_printed"] == "±0.02"
        assert parent_values["fO2_log"]["uncertainty_locator"]["page"] == 6
        assert parent_values["fO2_pressure_reference_as_printed"] == "not stated"
        assert parent_values["gas_flows_cc_per_min_as_printed"] == {"CO2": co2, "H2": h2, "N2": n2}
        assert parent_values["fO2_log"]["state"]["value"] == {"kind": "point", "point": f"-{positive_log}"}

    for row_number, magma, marker, attribution, x_matrix, x_sio2, x_tio2, x_nakal, x_ca in TABLE2_ROWS:
        source = context_by_id[f"doyle_1989_table2_r{int(row_number):02d}"]
        assert source["locator"]["page"] == 6 and source["locator"]["table"] == "2"
        assert source["locator"]["row"] == row_number
        values = source["values"]
        assert values["magma_type_as_printed"] == magma
        assert values["source_marker_as_printed"] == marker
        assert values["attribution_as_printed"] == attribution
        assert values["X_Matrix_as_printed"] == x_matrix
        assert values["Matrix_composition_mole_fraction_as_printed"] == {
            "SiO2": x_sio2, "TiO2": x_tio2, "(Na,K)AlO2": x_nakal, "Ca0.5AlO2": x_ca,
        }
        assert values["row_as_printed"] == {
            "X_Matrix": x_matrix, "SiO2": x_sio2, "TiO2": x_tio2,
            "(Na,K)AlO2": x_nakal, "Ca0.5AlO2": x_ca,
        }
        assert "normalized Matrix pseudocomponent" in values["schema_gap"]
        assert "point_conditions" not in source

    equations = context_by_id["doyle_1989_equations_6_6a_and_interaction_fits"]["values"]
    assert equations["equation_6_as_printed"] == "a_FeO^R = a_FeO^si · exp(-w s t/RT)"
    assert equations["equation_6a_as_printed"] == "a_FeO^si = a_FeO^R / exp(-w s t/RT)"
    assert equations["interaction_fit_values_as_printed"] == {
        "figure_2_contours": "w = 105 KJ/mole",
        "figure_3_projection": "w = 105 KJ/mole",
        "figure_5A_projection": "w = 42 KJ/mole",
        "figure_5B_5C_projection": "w = 105 KJ/mole",
    }


def test_every_render_checked_cell_and_interior_mutation_are_pinned() -> None:
    document = _document()
    assert validate_extract_document(
        document, expected_source_id=SOURCE_ID, pre_policy_ids=set(), check_fidelity_match=True
    ) == []
    _assert_source_matches_every_render_pin(document)

    mutated = deepcopy(document)
    interior = _by_id(
        mutated["species"]["FeO"]["observations"],
        "doyle_1989_table1a_fig4b_r25",
    )
    interior["values"]["activity"] = "0.41"
    with pytest.raises(AssertionError):
        _assert_source_matches_every_render_pin(mutated)

    mutated_condition = deepcopy(document)
    interior_condition = _by_id(
        mutated_condition["species"]["FeO"]["observations"],
        "doyle_1989_table1a_fig4b_r25",
    )["point_conditions"]["fO2_log"]["state"]["value"]
    interior_condition["point"] = "-11.31"
    with pytest.raises(AssertionError):
        _assert_source_matches_every_render_pin(mutated_condition)


def test_isolated_migration_binds_all_activity_values_and_conditions() -> None:
    document = _document()
    _assert_source_matches_every_render_pin(document)
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation.hard_issues == ()
    assert not result.registry_issues
    assert len(result.observations) == 167
    assert len(result.queue) == 334

    observations = result.observations
    assert set(observations) == {
        f"{SOURCE_ID}::{_table1a_id(panel, row_number)}"
        for panel, row_number, *_ in TABLE1A_ROWS
    }
    assert all(row.value.kind is ValueKind.POINT for row in observations.values())
    assert all(row.identity.quantity.value is Quantity.ACTIVITY for row in observations.values())
    assert all(row.identity.species.formula == "FeO" for row in observations.values())
    assert all(row.identity.species.phase.value is Phase.L for row in observations.values())
    assert all(row.evidence.class_.value is EvidenceClass.MEASURED_REDUCED for row in observations.values())
    assert all(row.identity.reference_state is None or row.identity.reference_state.is_unknown for row in observations.values())
    assert all(row.identity.total_pressure_Pa.is_unknown for row in observations.values())
    assert sum(row.identity.composition.is_value for row in observations.values()) == 134
    closure_refusals = 0
    assert all(row.identity.temperature_K.value == Decimal("1600") for row in observations.values())
    assert not [row for row in observations.values() if row.identity.quantity.is_unknown]
    assert not [row for row in observations.values() if row.identity.species.phase.is_unknown]
    assert not [row for row in observations.values() if row.value.point is None]
    assert Counter(entry.why for entry in result.queue) == Counter({
        "source standard_state is not typed: molten Fe0.947O": 167,
        "source does not state method": 167,
    })

    source_rows = {
        row["observation_id"]: row for row in document["species"]["FeO"]["observations"]
    }
    for panel, row_number, activity, *tail in TABLE1A_ROWS:
        composition_values = tail[:6]
        uncertainty = next(row[2] for row in TABLE1B_ROWS if row[1] == activity)
        positive_log = next(row[3] for row in TABLE1B_ROWS if row[1] == activity)
        parent_id = _setpoint_id(activity)
        source = source_rows[_table1a_id(panel, row_number)]
        migrated = observations[f"{SOURCE_ID}::{source['observation_id']}"]
        assert migrated.value.point == Decimal(activity)
        assert migrated.uncertainty.verbatim == source["uncertainty"]
        assert migrated.uncertainty.verbatim["value"] == uncertainty
        assert migrated.uncertainty.verbatim["verbatim"] == f"±{uncertainty}; confidence level not stated"
        assert migrated.uncertainty.verbatim["locator"]["page"] == 6
        assert migrated.derived_from == (f"{SOURCE_ID}::context::{parent_id}",)
        assert migrated.derivation.inputs == migrated.derived_from
        assert "Eq. (8)" in migrated.derivation.relation

        identity = migrated.identity
        assert identity.temperature_K.value == Decimal("1600")
        expected_fugacity_log = Decimal(f"-{positive_log}")
        assert identity.fO2_Pa.value == log10_fo2_to_pa(expected_fugacity_log)
        assert migrated.point_conditions["T_K"].state.value.point == Decimal("1600")
        assert migrated.point_conditions["T_K"].locator.page == 4
        assert migrated.point_conditions["fO2_log"].state.value.point == expected_fugacity_log
        assert migrated.point_conditions["fO2_log"].locator.page == 6
        assert migrated.point_conditions["fO2_log"].locator.table == "1B"
        assert f"exact printed a_FeO key {activity}" in migrated.point_conditions["fO2_log"].locator.note
        assert f"-log10 fO2 = {positive_log}±0.02" in migrated.point_conditions["fO2_log"].locator.note
        assert "total_pressure_Pa" not in migrated.point_conditions
        assert migrated.identity.total_pressure_Pa.is_unknown
        assert migrated.locator.page == 4
        assert migrated.locator.table == "1A"

        printed_composition = {
            oxide: Decimal(value)
            for oxide, value in zip(OXIDES, composition_values, strict=True)
        }
        kept_sum = sum(printed_composition.values(), Decimal("0"))
        composition_condition = migrated.point_conditions["composition"]
        if kept_sum > Decimal("102"):
            closure_refusals += 1
            assert composition_condition.state.is_unknown
            assert (
                "exceeds the accepted kept-sum limit"
                in composition_condition.state.reason
            )
            assert identity.composition == composition_condition.state
        else:
            assert Decimal("95") <= kept_sum <= Decimal("102")
            expected_composition = wt_pct_to_mole_fraction(printed_composition)
            assert identity.composition.value == expected_composition
            assert composition_condition.state.value == expected_composition
        assert migrated.point_conditions["composition"].locator.page == 4
        assert identity.reference_state.is_unknown
        assert source["standard_state"] == "molten Fe0.947O"
        assert "ReferenceStateConvention" in source["values"]["standard_state_schema_gap"]

    assert closure_refusals == 33

    # Render-backed locators from both printed tables reach migrated values.
    first = observations[f"{SOURCE_ID}::doyle_1989_table1a_fig4a_r01"]
    middle = observations[f"{SOURCE_ID}::doyle_1989_table1a_fig4b_r25"]
    last = observations[f"{SOURCE_ID}::doyle_1989_table1a_fig4c_r59"]
    assert (first.value.point, first.locator.page, first.locator.table) == (
        Decimal("0.20"), 4, "1A"
    )
    assert (middle.value.point, middle.locator.page, middle.locator.table) == (
        Decimal("0.40"), 4, "1A"
    )
    assert (last.value.point, last.locator.page, last.locator.table) == (
        Decimal("0.50"), 4, "1A"
    )
    assert first.identity.composition.value.components == (
        ("SiO2", Decimal("0.5363533193686149898776297670")),
        ("TiO2", Decimal("0.04287212642346588034698394932")),
        ("Al2O3", Decimal("0.06123703270131157116655978668")),
        ("FeO", Decimal("0.2924927986513158938905799942")),
        ("CaO", Decimal("0.06704472285529166471824650286")),
        ("K2O", Decimal("0E+27")),
    )
