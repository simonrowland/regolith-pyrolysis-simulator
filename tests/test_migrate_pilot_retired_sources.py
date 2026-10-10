"""Retired alpha draft sources must not overwrite their retained owners."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_writer():
    path = REPO_ROOT / "tools" / "migrate_pilot_extracts.py"
    spec = importlib.util.spec_from_file_location("migrate_pilot_extracts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_alpha_writer_preserves_retained_fedkin_and_sossi_owner_extracts(
    tmp_path, monkeypatch
):
    writer = _load_writer()
    extracts = tmp_path / "extracts"
    extracts.mkdir()
    original_owners = {}
    for source_id in ("kems-005-fedkin-2006", "kems-012-sossi-2019"):
        path = extracts / f"{source_id}.yaml"
        payload = {
            "source_id": source_id,
            "species": {
                "Mg": {
                    "observations": [
                        {"observation_id": f"{source_id}-curated-row"}
                    ]
                }
            },
        }
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        original_owners[path] = path.read_bytes()

    draft = tmp_path / "alpha-kinetics.md"
    records = [
        {"record_id": "fedkin_2006_mg", "species": "Mg", "alpha_value": 0.27},
        {"record_id": "sossi_2019_na", "species": "Na", "alpha_value": 1.0},
        {"record_id": "costa_2015_si", "species": "Si", "alpha_value": 0.02},
    ]
    draft.write_text(
        "```yaml\n"
        + yaml.safe_dump({"records": records}, sort_keys=False)
        + "```\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(writer, "EXTRACTS", extracts)
    monkeypatch.setattr(writer, "_find_research", lambda *args: draft)
    monkeypatch.setattr(writer, "ensure_fidelity_samples", lambda doc: None)
    writes = []
    monkeypatch.setattr(writer, "_dump", lambda path, doc: writes.append(path))

    written = writer.migrate_alpha_kinetics()

    assert written == [extracts / "costa-jacobson-2015.yaml"]
    assert writes == written
    assert not {path.name for path in writes} & {
        "kems-005-fedkin-2006.yaml",
        "kems-012-sossi-2019.yaml",
    }
    assert {path: path.read_bytes() for path in original_owners} == original_owners
