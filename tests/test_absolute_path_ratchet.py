"""Ratchet for absolute provenance/locator paths in literature extracts (b-713).

After the fix, the extract tree must carry zero absolute provenance/locator
path lines, and the validator ceiling refuses any rise above zero.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools import validate_literature_extracts as vle

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"

# Post-fix absolute-path total (b-713): every hit rewritten to corpus-relative.
PINNED_ABSOLUTE_PATH_TOTAL = 0
PINNED_EXTRACT_FILES_WITH_ABS = 0


def test_absolute_path_count_matches_pin() -> None:
    total, per_file = vle.count_absolute_paths_in_extracts()
    assert total == PINNED_ABSOLUTE_PATH_TOTAL
    assert len(per_file) == PINNED_EXTRACT_FILES_WITH_ABS
    assert sum(per_file.values()) == total


def test_absolute_path_ceiling_constant_matches_pin() -> None:
    assert vle.ABSOLUTE_PATH_COUNT_CEILING == PINNED_ABSOLUTE_PATH_TOTAL


def test_absolute_path_ceiling_check_passes_at_pin() -> None:
    assert vle.check_absolute_path_count_ceiling() == []


def test_absolute_path_ceiling_refuses_a_rise(tmp_path: Path) -> None:
    """A synthetic extra absolute path must fail the ceiling (count may only fall)."""
    # Copy one real extract that already has absolute paths, then add one more.
    donor = EXTRACTS / "kems-039-wolf-2023-vaporock.yaml"
    doc = yaml.safe_load(donor.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    extraction = doc.setdefault("extraction", {})
    assert isinstance(extraction, dict)
    # Force an absolute path distinct from any rewrite target.
    extraction["provenance_path"] = "/tmp/b713-ratchet-extra/not-corpus.pdf"
    # Also plant an absolute locator source_path so PATH_VALUE_KEYS walk sees it
    # even if provenance_path were already absolute on the donor.
    species = doc.setdefault("species", {})
    if not isinstance(species, dict) or not species:
        doc["species"] = {
            "Xe": {
                "observations": [
                    {
                        "observation_id": "b713_ratchet_probe",
                        "type": "partial_pressure",
                        "locator": {"source_path": "/tmp/b713-ratchet-extra/loc.pdf"},
                        "values": {"note": "ratchet probe"},
                    }
                ]
            }
        }
    else:
        # Append a tiny absolute source_path on the first observation if present.
        first_block = next(iter(species.values()))
        if isinstance(first_block, dict):
            obs_list = first_block.get("observations")
            if isinstance(obs_list, list) and obs_list and isinstance(obs_list[0], dict):
                loc = obs_list[0].setdefault("locator", {})
                if isinstance(loc, dict):
                    loc["source_path"] = "/tmp/b713-ratchet-extra/loc.pdf"

    probe = tmp_path / "b713-ratchet-probe.yaml"
    probe.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    # Ceiling equal to the real-tree pin: adding this probe file alone must rise.
    errs = vle.check_absolute_path_count_ceiling(
        [probe],
        ceiling=0,
    )
    assert errs, "expected ceiling refusal when absolute paths are present above 0"
    assert "exceeds ceiling" in errs[0]
    assert "may only fall" in errs[0]


def test_walk_absolute_path_values_finds_nested_source_path() -> None:
    doc = {
        "extraction": {"provenance_path": "corpus/raw/x/x.pdf"},
        "species": {
            "Fe": {
                "observations": [
                    {
                        "observation_id": "o1",
                        "locator": {
                            "source_path": "/Users/someone/Repos/regolith-corpus/raw/x/x.pdf"
                        },
                        "values": {"x": 1},
                    }
                ]
            }
        },
    }
    hits = vle._walk_absolute_path_values(doc)
    assert len(hits) == 1
    assert hits[0][0].endswith("source_path")
    assert hits[0][1].startswith("/Users/")


def test_path_value_keys_cover_provenance_and_locator_fields() -> None:
    assert vle.PATH_VALUE_KEYS == frozenset(
        {
            "provenance_path",
            "source_path",
            "source_pdf",
            "source_pdf_path",
        }
    )
