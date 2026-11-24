"""The registered DOI resolves through the stable work identity."""

from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import load_aliases, work_id_for


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "holzheid-lodders-2001-copper-solubility"
REGISTERED_DOI = "10.1016/S0016-7037(01)00545-2"
STABLE_WORK_ID = "10.1016/s0016-7037(00)00545-2"


def test_registered_doi_uses_existing_stable_work_file() -> None:
    source_path = REPO_ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
    source = yaml.safe_load(source_path.read_text())
    assert source["source"]["doi"] == REGISTERED_DOI

    work_id, doi = work_id_for(
        citation=source["source"]["citation"],
        doi=source["source"]["doi"],
        source_id=SOURCE_ID,
        aliases=load_aliases(REPO_ROOT / "data/literature/works/ALIASES.yaml"),
    )

    work_path = REPO_ROOT / "data/literature/works" / f"{work_id.replace('/', '_')}.yaml"
    assert work_id == STABLE_WORK_ID
    assert work_path.is_file()
    work = yaml.safe_load(work_path.read_text())
    assert work["work"]["doi"].casefold() == REGISTERED_DOI.casefold()
    assert doi == REGISTERED_DOI.casefold()
