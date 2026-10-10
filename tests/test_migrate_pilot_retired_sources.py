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


RETIRED_RECORDS = (
    "fedkin_2006_table3_fe_hashimoto_langmuir",
    "fedkin_2006_yu_na_vacuum_langmuir",
    "sossi_2019_na_open_furnace_apparent",
    "fedkin_2006_yu_k_vacuum_langmuir",
    "fedkin_2006_table3_mg_hashimoto_langmuir",
    "fedkin_2006_table3_sio_hashimoto_langmuir",
    "pound_1972_cr_langmuir_knudsen",
    "safarian_engh_2013_si_pure_langmuir",
)
RETIRED_DESTINATIONS = {
    "fedkin-grossman-ghiorso-2006.yaml",
    "sossi-et-al-2019.yaml",
    "pound-1972-cr-langmuir-knudsen.yaml",
    "safarian-engh-2013-si-pure-langmuir.yaml",
    "kems-005-fedkin-2006.yaml",
    "kems-012-sossi-2019.yaml",
    "kems-003-pound-1972.yaml",
    "kems-009-safarian-2013.yaml",
}


def _run_writer(draft, extracts, monkeypatch):
    writer = _load_writer()
    monkeypatch.setattr(writer, "EXTRACTS", extracts)
    monkeypatch.setattr(writer, "_find_research", lambda *args: draft)
    monkeypatch.setattr(writer, "ensure_fidelity_samples", lambda doc: None)
    writes = []
    monkeypatch.setattr(writer, "_dump", lambda path, doc: writes.append(path))

    written = writer.migrate_alpha_kinetics()

    return {path.name for path in writes}, written


def _draft_with_records(path, records):
    path.write_text(
        "```yaml\n" + yaml.safe_dump({"records": records}, sort_keys=False) + "```\n",
        encoding="utf-8",
    )
    return path


def test_alpha_writer_preserves_retired_sources(tmp_path, monkeypatch):
    extracts = tmp_path / "extracts"
    extracts.mkdir()
    records = [
        {"record_id": rid, "species": "Cr", "alpha_value": 0.9}
        for rid in RETIRED_RECORDS
    ]
    records.append(
        {"record_id": "costa_2015_si", "species": "Si", "alpha_value": 0.02}
    )
    draft = _draft_with_records(tmp_path / "alpha-kinetics.md", records)

    destinations, written = _run_writer(draft, extracts, monkeypatch)

    assert destinations == {"costa-jacobson-2015.yaml"}
    assert {path.name for path in written} == destinations
    assert not destinations & RETIRED_DESTINATIONS


def test_real_alpha_draft_emits_only_nonretired_destinations(tmp_path, monkeypatch):
    extracts = tmp_path / "extracts"
    extracts.mkdir()
    draft = (
        REPO_ROOT.parents[1]
        / "docs-private/research/2026-08-01-vp-acquire-6/alpha-kinetics.md"
    )
    assert draft.is_file(), f"real acquisition draft is unavailable: {draft}"

    destinations, written = _run_writer(draft, extracts, monkeypatch)

    assert destinations == {
        "costa-jacobson-2015.yaml",
        "richter-et-al-2007.yaml",
        "wetzel-gail-2013-sio-arrhenius.yaml",
    }
    assert {path.name for path in written} == destinations
    assert not destinations & RETIRED_DESTINATIONS
