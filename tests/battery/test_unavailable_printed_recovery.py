"""Printed Ichise 1982 Table 2 activities the migrator used to leave UNAVAILABLE (b-981).

Expected amounts are the MinerU decode of Table 2 (published p. 555), not a copy of the extract YAML.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import select_declared_source
from simulator.yaml_cache import load_cached_safe_yaml
from tests.battery.test_migrate import _write_min_tree
from tests.battery.test_printed_point_conditions import (
    _EXTRACTS,
    _migrate_real_extract,
)

# Ichise, Maruo, Sasho, Ueshima and Mori, Trans. ISIJ 22 (1982) 552.
# Table 2, "Result of activity measurements at 1 823 K."
# (x_Mo, a_Fe) as printed. The starred 0.528 is still a Table 2 cell.
_ICHISE_TABLE2 = (
    (Decimal("0.096"), Decimal("0.897")),
    (Decimal("0.154"), Decimal("0.810")),
    (Decimal("0.200"), Decimal("0.800")),
    (Decimal("0.201"), Decimal("0.767")),
    (Decimal("0.240"), Decimal("0.692")),
    (Decimal("0.251"), Decimal("0.737")),
    (Decimal("0.254"), Decimal("0.741")),
    (Decimal("0.300"), Decimal("0.690")),
    (Decimal("0.302"), Decimal("0.673")),
    (Decimal("0.333"), Decimal("0.677")),
    (Decimal("0.339"), Decimal("0.652")),
    (Decimal("0.348"), Decimal("0.652")),
    (Decimal("0.434"), Decimal("0.621")),
    (Decimal("0.434"), Decimal("0.656")),
    (Decimal("0.434"), Decimal("0.528")),
    (Decimal("0.556"), Decimal("0.585")),
    (Decimal("0.613"), Decimal("0.552")),
    (Decimal("0.666"), Decimal("0.526")),
    (Decimal("0.778"), Decimal("0.556")),
    (Decimal("0.793"), Decimal("0.538")),
    (Decimal("0.842"), Decimal("0.518")),
    (Decimal("0.912"), Decimal("0.360")),
    (Decimal("0.912"), Decimal("0.383")),
)
def _migrate_real_extract_doc(tmp_path: Path, doc: dict):
    from simulator.battery.migrate import migrate

    root = _write_min_tree(tmp_path, doc)
    return migrate(root, write=False)
