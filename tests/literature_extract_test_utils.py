"""Shared temporary-root setup for isolated literature extract migrations."""
from __future__ import annotations

from pathlib import Path

import yaml

def isolated_root(tmp_path: Path, document: dict) -> Path:
    literature = tmp_path / "data/literature"
    extracts = literature / "extracts"
    extracts.mkdir(parents=True)
    (literature / "compilations").mkdir()
    (literature / "INDEX.yaml").write_text(
        "schema_version: literature_index.v1\nsources: []\n", encoding="utf-8"
    )
    (extracts / f"{document['source_id']}.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path
