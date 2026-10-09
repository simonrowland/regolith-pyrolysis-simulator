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

import re
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


# Every key in PATH_VALUE_KEYS, read from the validator (the one definition;
# no copy of the set lives here). Each spelling plants exactly one absolute
# value under ``extraction`` so it is the only thing wrong with the probe.
_PLANT_FORMS = {
    "unquoted": "  {key}: {abs}\n",
    "quoted": "  {key}: '{abs}'\n",
    # Anchor on a non-path key; only the alias on the path key can refuse.
    "alias": "  probe_anchor: &probe_abs {abs}\n  {key}: *probe_abs\n",
    # List value (``corpus_tables: [tables/.../t1.csv, ...]``): elements count.
    "list_element": "  {key}: [{rel}, {abs}]\n",
    "alias_in_list": "  probe_anchor: &probe_abs {abs}\n  {key}: [{rel}, *probe_abs]\n",
}
_PLANT_ABS = "/Users/someone/Repos/regolith-corpus/raw/probe/t1.csv"
_PLANT_REL = "tables/probe/t1.csv"


def _planted_doc(key: str, form: str, value: str) -> dict:
    extra = _PLANT_FORMS[form].format(key=key, abs=value, rel=_PLANT_REL)
    return yaml.safe_load(
        _TEMPLATE.format(extraction_extra=extra, locator="{page: 1, table: '1'}")
    )


@pytest.mark.parametrize("form", sorted(_PLANT_FORMS))
@pytest.mark.parametrize("key", sorted(vle.PATH_VALUE_KEYS))
def test_absolute_value_on_every_path_key_is_refused(key: str, form: str) -> None:
    doc = _planted_doc(key, form, _PLANT_ABS)
    errors = vle.validate_extract_document(doc, expected_source_id="probe")
    refused = _absolute_errors(errors)
    assert len(refused) == 1, errors
    assert f"extraction.{key}" in refused[0]
    assert _PLANT_ABS in refused[0]
    assert errors == refused  # the refusal is the only error, not buried


@pytest.mark.parametrize("form", sorted(_PLANT_FORMS))
@pytest.mark.parametrize("key", sorted(vle.PATH_VALUE_KEYS))
def test_relative_value_on_every_path_key_validates_clean(key: str, form: str) -> None:
    doc = _planted_doc(key, form, REL)
    assert vle.validate_extract_document(doc, expected_source_id="probe") == []


def test_realistic_rows_table_csv_and_list_corpus_tables_refused() -> None:
    # kems-050-gorokhov-1977.yaml:106 values.table_csv and
    # kems-010-richter-2007.yaml corpus_tables (a list of csv paths), each
    # made absolute in place.
    doc = yaml.safe_load(_text("unquoted", REL))
    obs = doc["species"]["Fe"]["observations"][0]
    obs["values"]["table_csv"] = _PLANT_ABS
    doc["deepening"] = {"corpus_tables": ["tables/probe/table-1.csv", _PLANT_ABS]}
    errors = _absolute_errors(
        vle.validate_extract_document(doc, expected_source_id="probe")
    )
    assert any("species.Fe.observations[0].values.table_csv" in e for e in errors), errors
    assert any("deepening.corpus_tables[1]" in e for e in errors), errors
    assert len(errors) == 2


def test_prose_and_non_path_keys_are_not_refused() -> None:
    # table_transcription is printed table text (a "/K" unit token is not a
    # path); new_corpus_tables holds table numbers; the dict form of
    # transcription (kems-027-plante-hastie-1983 metadata) is walked and has
    # no absolute string.
    doc = yaml.safe_load(_text("unquoted", REL))
    doc["extraction"]["table_transcription"] = "/K"
    doc["extraction"]["new_corpus_tables"] = ["2.3", "3.1"]
    doc["extraction"]["transcription"] = {
        "page": 3,
        "table": "2",
        "title": "Vapour pressures / K",
        "method": "manual",
    }
    assert vle.validate_extract_document(doc, expected_source_id="probe") == []
    # ...but an absolute string inside that dict on a path key still refuses.
    doc["extraction"]["transcription"]["source_csv"] = _PLANT_ABS
    errors = _absolute_errors(
        vle.validate_extract_document(doc, expected_source_id="probe")
    )
    assert errors and "extraction.transcription.source_csv" in errors[0]


# A value that names a file or corpus directory: a corpus/repo root prefix, or
# a bare filename with a file extension; never whitespace (prose).
_PATH_SHAPED_RE = re.compile(
    r"^(?:(?:corpus|raw|tables|text|data|docs|docs-private)/\S*"
    r"|[^\s:/]+(?:/[^\s:]+)*\.(?:pdf|csv|md|ya?ml|txt|jsonl?|png|tsv|inp))$"
)


def _string_values_by_key(node, acc: dict[str, list[str]]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, str):
                    acc.setdefault(str(key), []).append(item)
            _string_values_by_key(value, acc)
    elif isinstance(node, list):
        for item in node:
            _string_values_by_key(item, acc)


def test_path_value_keys_match_the_extract_tree() -> None:
    """The set is checked against the data, not against a second hand list.

    Every key that carries a path-shaped value anywhere in the extract tree,
    and no prose, must be in PATH_VALUE_KEYS (so ``table_csv`` cannot fall out
    again). No member may carry prose (whitespace or multi-line text): that is
    how ``table_transcription`` came to refuse ``/K``; its one relative path
    (bencze-yazhenskikh-2016.yaml:198) sits beside four printed tables.
    ``url`` values (http/https) and dot-paths (``path``, ``metadata_path``)
    are not path-shaped here.
    """
    acc: dict[str, list[str]] = {}
    for path in vle.discover_extracts():
        _string_values_by_key(yaml.safe_load(path.read_text(encoding="utf-8")), acc)
    def is_prose(value: str) -> bool:
        return bool(re.search(r"\s", value))

    carries_paths = {
        key
        for key, values in acc.items()
        if any(_PATH_SHAPED_RE.match(v) for v in values)
        and not any(is_prose(v) for v in values)
    }
    missing = carries_paths - vle.PATH_VALUE_KEYS
    assert not missing, f"path-carrying keys outside PATH_VALUE_KEYS: {sorted(missing)}"
    prose = {
        key: v[:60] for key in vle.PATH_VALUE_KEYS for v in acc.get(key, []) if is_prose(v)
    }
    assert not prose, f"PATH_VALUE_KEYS members carrying prose: {prose}"


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
