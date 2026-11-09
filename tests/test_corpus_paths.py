import runpy
from pathlib import Path

import pytest

from simulator.corpus_paths import regolith_corpus_root


def test_regolith_corpus_root_uses_override_and_home_default(monkeypatch, tmp_path):
    configured_root = tmp_path / "configured-corpus"
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(configured_root))
    assert regolith_corpus_root() == configured_root

    monkeypatch.delenv("REGOLITH_CORPUS_ROOT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert regolith_corpus_root() == tmp_path / "Repos/regolith-corpus"


def test_regolith_corpus_ctl_root_uses_configured_root_or_legacy_default(
    monkeypatch, tmp_path
):
    from simulator.corpus_paths import regolith_corpus_ctl_root

    configured_root = tmp_path / "custom-basename"
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(configured_root))
    assert regolith_corpus_ctl_root() == configured_root

    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", "relative-corpus")
    assert regolith_corpus_ctl_root() == (Path.cwd() / "relative-corpus").resolve()

    monkeypatch.delenv("REGOLITH_CORPUS_ROOT")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert regolith_corpus_ctl_root() == tmp_path / "Repos/regolith-corpus-ctl"


@pytest.mark.parametrize("override", ["custom-corpus", "relative-corpus"])
def test_corpus_consumers_honor_configured_roots(monkeypatch, tmp_path, override):
    from simulator.corpus_paths import regolith_corpus_ctl_root
    from simulator.reference_data import (
        hemingway_haas_robinson_1982_usgs_b1544_loader as b1544,
        robie_waldbaum_1968_usgs_b1259_loader as b1259,
    )

    configured_root = Path(override)
    expected_root = (
        configured_root.resolve()
        if not configured_root.is_absolute()
        else configured_root
    )
    if override == "custom-corpus":
        configured_root = tmp_path / override
        expected_root = configured_root
        monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(configured_root))
    else:
        monkeypatch.setenv("REGOLITH_CORPUS_ROOT", override)

    assert b1544.pdf_candidates()[0] == (
        expected_root / "raw" / b1544.SOURCE_ID / b1544.SOURCE_PDF_NAME
    )
    assert b1544.pdf_candidates()[1] == b1544.pdf_candidates()[0]
    assert b1259._pdf_paths()[0] == (
        expected_root / "raw" / b1259.SOURCE_ID / f"{b1259.SOURCE_ID}.pdf"
    )
    assert b1259._pdf_paths()[1] == b1259._pdf_paths()[0]
    assert regolith_corpus_ctl_root() == expected_root


@pytest.mark.parametrize("override", ["custom-corpus", "relative-corpus"])
def test_harvest_and_mineru_defaults_honor_configured_roots(
    monkeypatch, tmp_path, override
):
    if override == "custom-corpus":
        configured_root = tmp_path / override
        monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(configured_root))
    else:
        configured_root = (Path.cwd() / override).resolve()
        monkeypatch.setenv("REGOLITH_CORPUS_ROOT", override)

    from tools import (
        harvest_burcat_compilation as burcat,
        mineru_table_draft as draft,
        mineru_table_qa as qa,
    )

    assert burcat.default_source() == configured_root / "raw/burcat-third-millennium"
    assert draft.default_corpus_root() == configured_root / "raw"
    assert qa.default_corpus_root() == configured_root

    summary = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "data/literature/compilations/robie-hemingway-1995-usgs-b2131/summary/harvest_summary.py"
        ),
        run_name="corpus_path_test",
    )
    assert summary["source_pdf"]() == (
        configured_root
        / "raw/robie-hemingway-1995-usgs-b2131/robie-hemingway-1995-usgs-b2131.pdf"
    )


@pytest.mark.parametrize("override", ["custom-corpus", "relative-corpus"])
def test_pankratz_cli_defaults_to_shared_root_and_keeps_explicit_argument(
    monkeypatch, tmp_path, override
):
    from tools import ingest_pankratz_1987_usbm_b689 as pankratz

    if override == "custom-corpus":
        configured_root = tmp_path / override
        monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(configured_root))
    else:
        configured_root = (Path.cwd() / override).resolve()
        monkeypatch.setenv("REGOLITH_CORPUS_ROOT", override)
    assert pankratz._parse_args([]).corpus == configured_root
    explicit_root = tmp_path / "explicit-corpus"
    assert pankratz._parse_args(["--corpus", str(explicit_root)]).corpus == explicit_root


@pytest.mark.parametrize("override", ["custom-corpus", "relative-corpus"])
def test_both_literature_index_builders_use_shared_default_but_keep_explicit_root(
    monkeypatch, tmp_path, override
):
    from data.literature import build_index as builder

    if override == "custom-corpus":
        configured_root = tmp_path / override
        monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(configured_root))
    else:
        configured_root = (Path.cwd() / override).resolve()
        monkeypatch.setenv("REGOLITH_CORPUS_ROOT", override)
    index = builder.build_index(tmp_path / "sim")
    assert index["scan"]["corpus_root"] == str(configured_root)

    status = builder.build_source_status(tmp_path / "sim", corpus=tmp_path / "explicit")
    assert status["scan"]["corpus_root"] == str(tmp_path / "explicit")

    monkeypatch.delenv("REGOLITH_CORPUS_ROOT")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    expected_default = tmp_path / "Repos/regolith-corpus"
    assert builder.build_index(tmp_path / "sim")["scan"]["corpus_root"] == str(
        expected_default
    )
    assert builder.build_source_status(tmp_path / "sim")["scan"]["corpus_root"] == str(
        expected_default
    )


def test_consumer_defaults_preserve_legacy_owner_paths(monkeypatch, tmp_path):
    from simulator.corpus_paths import regolith_corpus_ctl_root
    from tools import (
        harvest_burcat_compilation as burcat,
        ingest_pankratz_1987_usbm_b689 as pankratz,
        mineru_table_draft as draft,
        mineru_table_qa as qa,
    )
    from simulator.reference_data import (
        hemingway_haas_robinson_1982_usgs_b1544_loader as b1544,
        robie_waldbaum_1968_usgs_b1259_loader as b1259,
    )

    monkeypatch.delenv("REGOLITH_CORPUS_ROOT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    corpus_root = tmp_path / "Repos/regolith-corpus"
    ctl_root = regolith_corpus_ctl_root()

    assert burcat.default_source() == ctl_root / "raw/burcat-third-millennium"
    assert draft.default_corpus_root() == corpus_root / "raw"
    assert qa.default_corpus_root() == corpus_root
    assert b1544.pdf_candidates() == (
        corpus_root / "raw" / b1544.SOURCE_ID / b1544.SOURCE_PDF_NAME,
        ctl_root / "raw" / b1544.SOURCE_ID / b1544.SOURCE_PDF_NAME,
    )
    assert b1259._pdf_paths() == [
        corpus_root / "raw" / b1259.SOURCE_ID / f"{b1259.SOURCE_ID}.pdf",
        ctl_root / "raw" / b1259.SOURCE_ID / f"{b1259.SOURCE_ID}.pdf",
    ]
    assert pankratz._parse_args([]).corpus == corpus_root

    summary = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "data/literature/compilations/robie-hemingway-1995-usgs-b2131/summary/harvest_summary.py"
        ),
        run_name="corpus_path_default_test",
    )
    assert summary["source_pdf"]() == (
        ctl_root
        / "raw/robie-hemingway-1995-usgs-b2131/robie-hemingway-1995-usgs-b2131.pdf"
    )
