"""Recapture the vendored-oracle fixture from the d9bd25f0b checkout.

Run from the d9bd25f0b checkout so its adapter and datapacks supply the oracle:
    /Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python \
        /Users/simonrowland/Repos/regolith-pyrolysis-simulator/worktrees/s-2/tests/fixtures/capture_imcc_green_d9bd25f0b_activities.py \
        --oracle-root /private/tmp/openimcc-c5-before \
        > tests/fixtures/imcc_green_d9bd25f0b_activities.json
"""

from __future__ import annotations

import json
import argparse
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--oracle-root", type=Path, default=Path.cwd())
ROOT = parser.parse_args().oracle_root.resolve()
sys.path.insert(0, str(ROOT))

from simulator.melt_backend.imcc_sf04.adapter import evaluate, load_datapack
from tests import test_openimcc_bridge as bridge_tests

COMPOSITIONS = bridge_tests.COMPOSITIONS
PACKS = getattr(bridge_tests, "PACKS", None) or getattr(
    bridge_tests, "VENDORED_PACKS"
)
TEMPERATURES_K = bridge_tests.TEMPERATURES_K


def _capture() -> dict[str, object]:
    rows = []
    for pack_name, resource_name in PACKS.items():
        path = resource_name or ROOT / "data/melt_activity/imcc/imcc-sf04-v1.0.2.json"
        pack = load_datapack(path)
        for composition_name, (composition, basis_type) in COMPOSITIONS.items():
            for temperature_K in TEMPERATURES_K:
                result = evaluate(
                    composition,
                    temperature_K,
                    pack,
                    basis_type=basis_type,
                    enable_sp_extension=pack_name == "ext-v4",
                )
                labels = result.labels
                rows.append(
                    {
                        "activities_hex": {
                            str(name): float(value).hex()
                            for name, value in zip(
                                result.parent_oxides, result.parent_activity, strict=True
                            )
                        },
                        "composition": composition_name,
                        "labels_legacy": {
                            "coverage": dict(labels.coverage),
                            "envelope_status": labels.envelope_status,
                            "identity": dict(labels.identity),
                            "trust": labels.trust,
                        },
                        "pack": pack_name,
                        "temperature_K": temperature_K,
                    }
                )

    edge_row = {
        "code": "imcc_composition_outside_validated_envelope",
        "composition": "K2O-SiO2-envelope-edge",
        "composition_mol": {"K2O": 0.500002, "SiO2": 0.499998},
        "status": "refused",
        "temperature_K": 1800.0,
    }
    return {"edge_row": edge_row, "rows": rows}


if __name__ == "__main__":
    print("# source: green d9bd25f0b vendored IMCC kernel")
    print(
        "# capture command: see tests/fixtures/"
        "capture_imcc_green_d9bd25f0b_activities.py docstring"
    )
    print(json.dumps(_capture(), sort_keys=True, separators=(",", ":")))
