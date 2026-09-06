"""Drift gate and row-truth tests for the tracked empirical-corpus index."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER = REPO_ROOT / "data" / "literature" / "build_index.py"
COMMITTED_YAML = REPO_ROOT / "data" / "literature" / "INDEX.yaml"
COMMITTED_MD = REPO_ROOT / "data" / "literature" / "INDEX.md"


def _run_builder(root: Path, out_dir: Path, extra: list[str] | None = None) -> None:
    cmd = [sys.executable, str(BUILDER), "--root", str(root), "--out-dir", str(out_dir)]
    if extra:
        cmd.extend(extra)
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_regenerated_index_matches_committed(tmp_path: Path) -> None:
    """Rebuilding INDEX.yaml/md into a temp dir must match the committed files.

    Null hypothesis: the committed index can drift from the builder without CI
    noticing. Regenerating into tmp (not mutating the tree) and comparing bytes
    makes that drift fail this test. CI walks --root <repo> with no private roots.
    """
    assert BUILDER.is_file()
    assert COMMITTED_YAML.is_file()
    assert COMMITTED_MD.is_file()
    _run_builder(REPO_ROOT, tmp_path)
    got_yaml = tmp_path / "INDEX.yaml"
    got_md = tmp_path / "INDEX.md"
    assert got_yaml.read_bytes() == COMMITTED_YAML.read_bytes()
    assert got_md.read_bytes() == COMMITTED_MD.read_bytes()


def test_builder_temp_corpus_row_truth(tmp_path: Path) -> None:
    """Two fake PDFs + one extract + one preset: exact rows without the corpus."""
    root = tmp_path / "corpus"
    pdf_dir = root / "docs" / "references" / "pdfs" / "99-kems-langmuir"
    extract_dir = root / "data" / "literature" / "extracts"
    preset_dir = root / "data" / "presets" / "kems"
    pdf_dir.mkdir(parents=True)
    extract_dir.mkdir(parents=True)
    preset_dir.mkdir(parents=True)

    alpha_bytes = b"%PDF-1.4 alpha-2020 fixture\n"
    beta_bytes = b"%PDF-1.4 beta-2021 fixture\n"
    (pdf_dir / "alpha-2020.pdf").write_bytes(alpha_bytes)
    (pdf_dir / "beta-2021.pdf").write_bytes(beta_bytes)
    (extract_dir / "alpha-2020.yaml").write_text(
        "\n".join(
            [
                "schema_version: literature_extract.v1",
                "source_id: alpha-2020",
                "source:",
                "  citation: Alpha, A. (2020), Fixture Paper",
                "  doi: 10.9999/alpha-fixture",
                "  year: 2020",
                "extraction:",
                "  method: fixture",
                "  date: '2026-09-06'",
                "  worker: test",
                "review_status: draft",
                "species: {}",
                "",
            ]
        )
    )
    (preset_dir / "alpha_case.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "case_id: alpha_case",
                "source_id: alpha_author_2020",
                "doi: 10.9999/alpha-fixture",
                "observation_sidecar_path: data/literature/vacuum_pyrolysis_measurements.yaml",
                "note: docs/references/pdfs/99-kems-langmuir/beta-2021.pdf is not this source",
                "",
            ]
        )
    )

    out_dir = tmp_path / "out"
    _run_builder(root, out_dir)
    index = yaml.safe_load((out_dir / "INDEX.yaml").read_text())
    rows = {row["source_id"]: row for row in index["sources"]}
    assert set(rows) == {"alpha-2020", "beta-2021"}
    assert "alpha_author_2020" not in rows

    alpha = rows["alpha-2020"]
    beta = rows["beta-2021"]
    alpha_sha = hashlib.sha256(alpha_bytes).hexdigest()
    beta_sha = hashlib.sha256(beta_bytes).hexdigest()

    assert alpha["aliases"] == ["alpha_author_2020"]
    assert alpha["doi"] == "10.9999/alpha-fixture"
    assert alpha["pdf_status"] == "present"
    assert alpha["pdf_path"] == "docs/references/pdfs/99-kems-langmuir/alpha-2020.pdf"
    assert alpha["pdf_sha256"] == alpha_sha
    assert alpha["pdf_tracked"] == "unknown"
    assert alpha["extracts"] == [
        {"path": "data/literature/extracts/alpha-2020.yaml", "review_status": "draft"}
    ]
    assert alpha["battery_datasets"] == ["data/presets/kems/alpha_case.yaml"]
    assert alpha["copies"] == []
    assert alpha["hunt_ids"] == []
    assert alpha["pdf_last_seen"] == []

    assert beta["aliases"] == []
    assert beta["extracts"] == []
    assert beta["pdf_status"] == "present"
    assert beta["pdf_path"] == "docs/references/pdfs/99-kems-langmuir/beta-2021.pdf"
    assert beta["pdf_sha256"] == beta_sha
    assert beta["pdf_tracked"] == "unknown"
    assert beta["battery_datasets"] == []
    assert "data/presets/kems/alpha_case.yaml" not in beta["battery_datasets"]
    assert index["counts"]["sources"] == 2
    assert index["scan"]["private_roots_scanned"] == []
    assert index["scan"]["hunt_json"] is None
