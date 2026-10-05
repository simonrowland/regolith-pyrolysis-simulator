"""Source-data pins committed before adding fit-quantity migration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def test_fegley_2023_table_2_printed_rows_are_pinned() -> None:
    path = ROOT / "data/literature/extracts/fegley-2023-chemical-equilibrium-calculations-bu.yaml"
    doc = yaml.safe_load(path.read_text())
    rows = doc["species"]["BSE"]["observations"][0]["values"]["rows_as_printed"]

    assert len(rows) == 82
    assert _sha256(rows) == "76e809f15c6338a5db8afb761ab8d683e627c95b85601e7ca4ece62c635af3e4"


def test_sossi_fegley_2018_table_2_printed_rows_are_pinned() -> None:
    path = ROOT / "data/literature/extracts/kems-041-sossi-fegley-2018.yaml"
    doc = yaml.safe_load(path.read_text())
    rows = [
        [formula, obs["observation_id"], obs.get("T_range_K"), obs.get("standard_state"), obs.get("values")]
        for formula, species in doc["species"].items()
        for obs in species.get("observations", [])
        if str((obs.get("locator") or {}).get("table")) == "2"
    ]

    assert len(rows) == 31
    assert _sha256(rows) == "49112e165ef0da3f7c2aa8b0287550db0dfc3ca3b441ea36aeb6dcb5cef99df3"
