"""d-032: context rows must not survive in an extracts-v2 observation list.

The leak is not a read of ``context`` during the lift. An extract that yields
zero observations is omitted from the extracts-v2 write set, so a sibling
written when those rows were still observations is left in place. The stale
sibling is the setup this test has to start from; a fresh tree never writes it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import migrate


def _extract(source_id: str, species_body: dict) -> dict:
    return {
        "schema_version": "literature_extract.v1",
        "source_id": source_id,
        "source": {
            "citation": f"Fixture, A. (2026), {source_id}, Test Journal 1:1",
        },
        "extraction": {"method": "unit_test", "date": "2026-09-21", "worker": "pytest"},
        "review_status": "draft",
        "species": {"Na": species_body},
    }


def _context_row(observation_id: str) -> dict:
    return {
        "observation_id": observation_id,
        "type": "apparatus",
        "locator": {"page": 1, "section": "experimental"},
        "values": {"quantity": "apparatus_note", "method_class": "method_only"},
    }


def _stale_sibling(source_id: str, *observation_ids: str) -> dict:
    return {
        "schema_version": "battery_observations.v2.1",
        "sources": [f"data/literature/extracts/{source_id}.yaml"],
        "observations": [
            {"observation_id": f"{source_id}::{observation_id}"}
            for observation_id in observation_ids
        ],
    }


def _bases(path: Path) -> list[str]:
    if not path.is_file():
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    bases: list[str] = []
    for obs in (doc or {}).get("observations") or []:
        oid = str(obs["observation_id"])
        local = oid.split("::", 1)[1] if "::" in oid else oid
        bases.append(local.split("::", 1)[0])
    return bases


def test_zero_observation_extract_does_not_emit_context_rows(tmp_path: Path) -> None:
    context_id = "bench_context_note"
    empty_id = "all-context-empty-obs"
    missing_id = "all-context-missing-key"
    mixed_id = "mixed-obs-and-context"
    extracts = tmp_path / "data" / "literature" / "extracts"
    extracts.mkdir(parents=True)
    (tmp_path / "data" / "literature" / "compilations").mkdir()
    (tmp_path / "data" / "literature" / "INDEX.yaml").write_text(
        "schema_version: literature_index.v1\nsources: []\n",
        encoding="utf-8",
    )
    docs = {
        empty_id: _extract(
            empty_id,
            {"observations": [], "context": [_context_row(context_id)]},
        ),
        missing_id: _extract(
            missing_id,
            {"context": [_context_row(context_id)]},
        ),
        mixed_id: _extract(
            mixed_id,
            {
                "observations": [
                    {
                        "observation_id": "na_psat",
                        "type": "psat_series",
                        "locator": {"table": "I", "page": 2},
                        "phase": "gas",
                        "regime": "knudsen_effusion",
                        "units": "atm",
                        "values": {
                            "quantity": "pure_Psat",
                            "method_class": "measured_direct",
                            "admission_status": "admitted",
                            "series": [
                                {"T_K": 1200.0, "pressure_atm": 1.0},
                                {"T_K": 1300.0, "pressure_atm": 2.0},
                            ],
                        },
                    }
                ],
                "context": [_context_row(context_id)],
            },
        ),
    }
    for source_id, doc in docs.items():
        (extracts / f"{source_id}.yaml").write_text(
            yaml.safe_dump(doc, sort_keys=False),
            encoding="utf-8",
        )
    v2 = tmp_path / "data" / "literature" / "extracts-v2"
    v2.mkdir()
    for source_id in (empty_id, missing_id):
        (v2 / f"{source_id}.yaml").write_text(
            yaml.safe_dump(_stale_sibling(source_id, context_id), sort_keys=False),
            encoding="utf-8",
        )
    (v2 / f"{mixed_id}.yaml").write_text(
        yaml.safe_dump(
            _stale_sibling(mixed_id, "na_psat", context_id),
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = migrate(tmp_path, write=True)

    for source_id in (empty_id, missing_id, mixed_id):
        bases = _bases(v2 / f"{source_id}.yaml")
        assert context_id not in bases, (source_id, bases)
        assert all(context_id not in base for base in bases)
    mixed_bases = _bases(v2 / f"{mixed_id}.yaml")
    assert set(mixed_bases) == {"na_psat"}
    assert len(mixed_bases) == 2

    carried = {
        str(row["context_id"])
        for rows in result.context_by_work.values()
        for row in rows
    }
    for source_id in (empty_id, missing_id, mixed_id):
        assert f"{source_id}::context::{context_id}" in carried
    assert not any(context_id in oid and "::context::" not in oid for oid in result.observations)
