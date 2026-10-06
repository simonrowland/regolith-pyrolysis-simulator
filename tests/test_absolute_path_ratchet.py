"""Absolute machine-local paths in literature extracts are refused (b-713).

The gate is the parsed walk inside ``validate_extract_document``: every
absolute value on a path-valued key (``PATH_VALUE_KEYS``) is an error for that
one extract, whatever its YAML spelling (unquoted, quoted, or an alias of an
anchor such as Gibson & Hubbard's ``original_scan: &scan`` reused as
``source_path: *scan``). These tests drive ``validate_extract_document``,
``validate_extract_file`` and ``validate_all`` so removing the wiring fails
here, not only in the (separately red) whole-tree green test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools import validate_literature_extracts as vle

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"

ABS = "/Users/someone/Repos/regolith-corpus/raw/probe/probe.pdf"
REL = "corpus/raw/probe/probe.pdf"

# Minimal extract that validate_extract_document accepts with zero errors;
# ``{extraction_extra}`` and ``{locator}`` are the spelling under test.
_TEMPLATE = """\
schema_version: literature_extract.v1
source_id: probe
source:
  citation: Probe et al. (2026), Test Journal 1:1
extraction:
  method: unit_test
  date: '2026-10-05'
  worker: pytest
{extraction_extra}review_status: draft
fidelity_samples:
- path: species.Fe.observations[fe_alpha_1].values.alpha
  value: 0.24
  note: fixture fidelity sample
  locator: {{page: 1, table: '1'}}
species:
  Fe:
    observations:
    - observation_id: fe_alpha_1
      type: alpha
      locator: {locator}
      T_range_K: [1700.0, 1800.0]
      phase: silicate_melt
      regime: langmuir_free_evaporation
      units: dimensionless
      uncertainty: {{note: fixture band, relative: 0.1}}
      values: {{alpha: 0.24}}
"""


def _forms(path: str) -> dict[str, tuple[str, str]]:
    """(extraction_extra, locator) per YAML spelling of one source path."""
    return {
        "unquoted": ("", "{source_path: " + path + ", page: 1, table: '1'}"),
        "quoted": ("", '{source_path: "' + path + '", page: 1, table: \'1\'}'),
        "gibson_anchor_alias": (
            f"  original_scan: &scan {path}\n",
            "{source_path: *scan, page: 1, table: '1'}",
        ),
        "provenance_path": (
            f"  provenance_path: '{path}'\n",
            "{page: 1, table: '1'}",
        ),
    }


def _text(form: str, path: str) -> str:
    extraction_extra, locator = _forms(path)[form]
    return _TEMPLATE.format(extraction_extra=extraction_extra, locator=locator)


def _absolute_errors(errors: list[str]) -> list[str]:
    return [e for e in errors if "absolute/machine-local path refused" in e]


@pytest.mark.parametrize("form", sorted(_forms(ABS)))
def test_validate_extract_document_refuses_every_spelling(form: str) -> None:
    doc = yaml.safe_load(_text(form, ABS))
    errors = vle.validate_extract_document(doc, expected_source_id="probe")
    assert _absolute_errors(errors), errors
    # Nothing else is wrong with the probe, so the refusal is not buried.
    assert errors == _absolute_errors(errors)


@pytest.mark.parametrize("form", sorted(_forms(REL)))
def test_relative_spelling_validates_clean(form: str) -> None:
    doc = yaml.safe_load(_text(form, REL))
    assert vle.validate_extract_document(doc, expected_source_id="probe") == []


@pytest.mark.parametrize("form", ["quoted", "gibson_anchor_alias"])
def test_validate_all_refuses_one_extract(tmp_path: Path, form: str) -> None:
    probe = tmp_path / "probe.yaml"
    probe.write_text(_text(form, ABS), encoding="utf-8")
    errors = vle.validate_all(
        [probe], check_priority=False, check_fidelity_policy=False
    )
    assert _absolute_errors(errors), errors
    assert all(str(probe) in e for e in _absolute_errors(errors))


def test_alias_expansion_counts_every_reuse() -> None:
    # One absolute anchor read by three locators: three parsed values, one
    # line of text. The old line-regex ceiling counted this as 1 (or 0 when
    # the anchor sat on a non-path key).
    doc = yaml.safe_load(
        "scan: &scan " + ABS + "\n"
        "a: {source_path: *scan}\n"
        "b: [{source_path: *scan}, {source_path: *scan}]\n"
    )
    assert len(vle._walk_absolute_path_values(doc)) == 3
    errors = vle._absolute_path_value_errors(doc, "probe")
    assert len(errors) == 1
    assert "+2 more via YAML aliases/repeats" in errors[0]


def test_unaliased_original_scan_is_refused() -> None:
    doc = yaml.safe_load(_text("gibson_anchor_alias", REL))
    doc["extraction"]["original_scan"] = ABS
    errors = vle.validate_extract_document(doc, expected_source_id="probe")
    assert any("extraction.original_scan" in e for e in _absolute_errors(errors))


def test_repo_extracts_carry_no_absolute_path_values() -> None:
    """Whole tree, same function the validator uses; unreadable files fail."""
    files = vle.discover_extracts()
    assert len(files) > 200
    offenders: list[str] = []
    for path in files:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        offenders.extend(vle._absolute_path_value_errors(doc, path.name))
    assert offenders == [], "\n".join(offenders[:20])


def test_path_value_keys_cover_provenance_locator_and_path_fields() -> None:
    assert vle.PATH_VALUE_KEYS == frozenset(
        {
            "provenance_path",
            "source_path",
            "source_pdf",
            "source_pdf_path",
            "original_scan",
            "chapter_pdf",
            "corpus_asset",
            "repaired_table_path",
            "table_transcription",
            "verified_data_path",
        }
    )


def test_line_regex_ceiling_is_gone() -> None:
    # A second pass/fail signal could report clean while parsed values are
    # absolute (ROR-b713); the parsed walk is the only rule.
    for name in (
        "ABSOLUTE_PATH_COUNT_CEILING",
        "_ABS_PATH_FIELD_LINE_RE",
        "count_absolute_paths_in_extracts",
        "check_absolute_path_count_ceiling",
    ):
        assert not hasattr(vle, name), name
