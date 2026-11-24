"""Production loader and migration checks for citation-only Sr works."""

from pathlib import Path

import yaml

from data.literature import build_index as literature_builder
from simulator.battery.migrate import load_migrated_store, work_filename, work_id_for


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTERED_SOURCES = {
    "brisi-abbattista-1960", "fields-1972", "ganits-1979",
    "huntelaar-1992-j-chem-thermodyn", "huntelaar-1992-j-phys-chem-solids",
    "jacob-shreyas-2018", "richet-bottinga-tequi-1984", "ye-2007",
}


def test_citation_only_works_load_with_migration_identities_and_index_rows():
    works, _, _ = load_migrated_store(REPO_ROOT, sources=REGISTERED_SOURCES)
    committed_ids = {}
    for path in (REPO_ROOT / "data/literature/works").glob("*.yaml"):
        document = yaml.safe_load(path.read_text())
        work = document.get("work") if isinstance(document, dict) else None
        if not isinstance(work, dict):
            continue
        source_ids = set(work.get("source_ids") or []) & REGISTERED_SOURCES
        if source_ids:
            assert len(source_ids) == 1
            source_id = next(iter(source_ids))
            committed_ids[source_id] = work["work_id"]
            assert path.name == work_filename(work["work_id"])
            identity, _ = work_id_for(
                citation=work["citation"], doi=work.get("doi"),
                source_id=source_id, aliases={},
            )
            assert work["work_id"] == identity

    assert set(committed_ids) == REGISTERED_SOURCES
    assert set(works) >= set(committed_ids.values())
    for source_id in ("ganits-1979", "brisi-abbattista-1960"):
        assert committed_ids[source_id] == work_id_for(
            citation=next(yaml.safe_load(path.read_text())["work"]["citation"]
                          for path in (REPO_ROOT / "data/literature/works").glob("*.yaml")
                          if source_id in yaml.safe_load(path.read_text())["work"].get("source_ids", [])),
            doi=None, source_id=source_id, aliases={},
        )[0]

    index = literature_builder.build_index(REPO_ROOT)
    rows = {row["source_id"]: row for row in index["sources"]}
    committed = yaml.safe_load((REPO_ROOT / "data/literature/INDEX.yaml").read_text())
    committed_rows = {row["source_id"]: row for row in committed["sources"]}
    assert REGISTERED_SOURCES <= rows.keys()
    for source_id in REGISTERED_SOURCES:
        assert rows[source_id]["citation"] == committed_rows[source_id]["citation"]
        assert rows[source_id]["doi"] == committed_rows[source_id]["doi"]
        assert rows[source_id]["pdf_path"] is None
    assert all(rows[source_id]["citation"] for source_id in REGISTERED_SOURCES)

