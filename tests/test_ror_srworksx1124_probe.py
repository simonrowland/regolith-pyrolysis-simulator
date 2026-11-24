"""Production-loader and index regression checks for the Sr work registrations."""

from __future__ import annotations

from pathlib import Path

import yaml

from data.literature import build_index as literature_builder
from simulator.battery.migrate import load_migrated_store


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTERED_SOURCES = {
    "brisi-abbattista-1960",
    "fields-1972",
    "ganits-1979",
    "huntelaar-1992-j-chem-thermodyn",
    "huntelaar-1992-j-phys-chem-solids",
    "jacob-shreyas-2018",
    "richet-bottinga-tequi-1984",
    "ye-2007",
}


def test_citation_only_works_load_and_regenerate_all_eight_index_rows():
    works, _, _ = load_migrated_store(REPO_ROOT, sources=REGISTERED_SOURCES)
    committed_ids = set()
    for path in (REPO_ROOT / "data/literature/works").glob("*.yaml"):
        if path.name == "ALIASES.yaml":
            continue
        work = yaml.safe_load(path.read_text())["work"]
        if set(work.get("source_ids") or []) & REGISTERED_SOURCES:
            committed_ids.add(work["work_id"])
    loaded = {work_id: works[work_id] for work_id in committed_ids}
    assert len(loaded) == 8
    assert "10.1016/s0021-9614(05)80042-4" in loaded
    assert "10.1016/0022-3697(92)90193-h" in loaded
    for work in loaded.values():
        assert len(work.source_files.files) == 1
        asset = work.source_files.files[0]
        assert asset.asset_id == f"unknown:{work.source_ids[0]}"
        assert asset.path == "unknown"

    index = literature_builder.build_index(REPO_ROOT)
    rows = {row["source_id"]: row for row in index["sources"]}
    committed = yaml.safe_load((REPO_ROOT / "data/literature/INDEX.yaml").read_text())
    committed_rows = {row["source_id"]: row for row in committed["sources"]}
    assert REGISTERED_SOURCES <= rows.keys()
    for source_id in REGISTERED_SOURCES:
        assert rows[source_id]["citation"] == committed_rows[source_id]["citation"], source_id
        assert rows[source_id]["doi"] == committed_rows[source_id]["doi"], source_id
        assert rows[source_id]["pdf_path"] == committed_rows[source_id]["pdf_path"], source_id
    assert all(rows[source_id]["citation"] for source_id in REGISTERED_SOURCES)
    assert all(rows[source_id]["pdf_path"] is None for source_id in REGISTERED_SOURCES)
    assert "Zh. Neorg. Khim. 24(2), 471–475" in rows["ganits-1979"]["citation"]
    assert "Ann. Chimica 50, 165–169" in rows["brisi-abbattista-1960"]["citation"]
    assert "801–806" in rows["huntelaar-1992-j-phys-chem-solids"]["citation"]
    assert "805–806" in rows["huntelaar-1992-j-phys-chem-solids"]["citation"]


def test_metadata_loader_removal_mutation_drops_the_eight_registered_rows(monkeypatch):
    """Mutation probe: removing the production fallback loses all eight rows."""
    actual = literature_builder.load_work_metadata(REPO_ROOT)
    monkeypatch.setattr(literature_builder, "load_work_metadata", lambda root: {})
    mutated = literature_builder.load_work_metadata(REPO_ROOT)
    assert REGISTERED_SOURCES <= actual.keys()
    assert REGISTERED_SOURCES.isdisjoint(mutated)
