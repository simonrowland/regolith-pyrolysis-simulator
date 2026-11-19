"""Locator aliases and typed refusals at the migration boundaries."""

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import RefusalReason
from simulator.battery.migrate import (
    Migrator,
    _bind_same_run_initial_composition,
    _same_run_initial_melts,
    migrate,
    observation_from_plain,
    to_plain,
    wt_pct_to_mole_fraction,
)
from simulator.battery.records import Locator
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree


REPO_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.timeout(300)
_COMPILATION_GENERATOR_RECORDS = (
    ("data/literature/compilations/janaf/tables/Na-014.yaml", 33),
    (
        "data/literature/compilations/hemingway-haas-robinson-1982-usgs-b1544/records/usgs-b1544-corundum.json",
        79,
    ),
    (
        "data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/records/robie-hemingway-fisher-1978-usgs-b1452-0003.json",
        1858,
    ),
    (
        "data/literature/compilations/robie-waldbaum-1968-usgs-b1259/records/b1259-298k-0001-silver.json",
        4,
    ),
    (
        "data/literature/compilations/robie-hemingway-1995-usgs-b2131/records/high-temperature-p189.json",
        5,
    ),
)
_UPSTREAM_COMPILATION_RECORDS = (
    "data/literature/compilations/atct/records/atct-1.222-0001.json",
    "data/literature/compilations/burcat/records/BU-0001.json",
    "data/literature/compilations/fastchem-v3-1-3/records/FC-C-0001.json",
    "data/literature/compilations/nasa-glenn/records/NG-0001.json",
    "data/literature/compilations/pankratz-1984-usbm-b677/records/table-0001.json",
)


@pytest.mark.parametrize("relative_path", _UPSTREAM_COMPILATION_RECORDS)
def test_compilation_observation_source_path_is_the_ingested_record(
    relative_path: str,
) -> None:
    """Canonicalization must not replace the ingested JSON with the raw source.

    atct's ``path`` and burcat's ``path`` (and the same shape on fastchem,
    nasa-glenn, and pankratz b677) name the upstream text. That fact stays
    in the note. ``source_path`` stays the record file, which exists.
    """

    from simulator.battery.migrate import compilation_record_asset_id

    path = REPO_ROOT / relative_path
    document = json.loads(path.read_text(encoding="utf-8"))
    raw_locator = document["source_locator"]
    upstream_path = raw_locator.get("path") or raw_locator["file"]
    assert upstream_path != relative_path
    migrator = Migrator(REPO_ROOT)
    migrator._migrate_compilation_file(path)
    migrator.finalize()
    observations = list(migrator.result.observations.values())
    assert observations
    asset_id = compilation_record_asset_id(relative_path)
    for observation in observations:
        locator = observation.locator
        assert locator.source_path == relative_path
        assert (REPO_ROOT / locator.source_path).is_file()
        assert upstream_path in (locator.note or "")
        extras = locator.extras or {}
        assert all(value != upstream_path for value in extras.values())
        assert observation.read_from == asset_id
        if observation.derivation is not None:
            assert observation.derivation.inputs[0] == asset_id
    assert any(
        asset.asset_id == asset_id and asset.path == relative_path
        for work in migrator.result.works.values()
        for asset in work.source_files.files
    )


@pytest.mark.parametrize("relative_path", _UPSTREAM_COMPILATION_RECORDS)
def test_compilation_record_binding_detects_upstream_path_winning(
    relative_path: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from simulator.battery import migrate as migrate_module

    def canonicalize_upstream_into_source_path(
        raw: object, *, record_path: str, record_id: str
    ):
        locator = migrate_module._locator_from_plain(raw, refuse_invalid=True)
        locator = locator or Locator(record=record_id)
        if not locator.source_path:
            locator = replace(locator, source_path=record_path)
        return locator

    with monkeypatch.context() as mutation:
        mutation.setattr(
            migrate_module,
            "compilation_record_locator",
            canonicalize_upstream_into_source_path,
        )
        with pytest.raises(AssertionError):
            test_compilation_observation_source_path_is_the_ingested_record(
                relative_path
            )


def test_printed_labels_and_grid_aliases_survive_extract_migration(
    tmp_path: Path,
) -> None:
    assert Locator(row="R-7").has_location()
    assert Locator(column="Na").has_location()

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    locator = extract["species"]["Na"]["observations"][0]["locator"]
    locator.update(
        {
            "row_label": "R-7",
            "row": 7,
            "column_header": "Na",
            "column": 5,
            "table_row": "7",
            "table_column": "5",
        }
    )

    from tools.validate_literature_extracts import _locator_ok

    assert _locator_ok({"page": 2, "table_row": 7})
    assert _locator_ok({"page": 2, "table_column": 2})
    assert not _locator_ok({"table_row_number": 7})

    result = migrate(
        _write_min_tree(tmp_path / "row-column", extract), write=False
    )

    observation = next(iter(result.observations.values()))
    assert observation.locator is not None
    assert observation.locator.row == "R-7"
    assert observation.locator.column == "Na"
    assert observation.locator.table_row_number == 7
    assert observation.locator.table_column_number == 5


def test_distinct_locator_extras_round_trip_without_becoming_note() -> None:
    from simulator.battery.migrate import _locator_from_plain

    extras = {
        "additional_page": 3,
        "additional_published_page": "iv",
        "continued_page": 103,
        "repaired_table_path": "tables/repaired.csv",
        "quote": {"text": "same value, different locator fact"},
    }
    locator = _locator_from_plain(
        {"page": 2, "published_page": 10, **extras}, refuse_invalid=True
    )

    assert locator is not None
    assert locator.page == 2
    assert locator.published_page == 10
    assert locator.note is None
    assert locator.extras == extras

    encoded = to_plain(locator)
    assert encoded["extras"] == extras
    decoded = _locator_from_plain(encoded, refuse_invalid=True)
    assert decoded == locator


@pytest.mark.parametrize("raw", [
    {"table": "571", "table_key": "p438-t571"},
    {"section": "Experimental", "subsection": "Procedure"},
    {"published_page": [63, 64], "pdf_page_index": [98, 99]},
    {"pdf_page": [121, 122], "pdf_page_index": [120, 121]},
])
def test_distinct_and_multiple_source_addresses_are_not_alias_conflicts(raw) -> None:
    from simulator.battery.migrate import _locator_from_plain

    locator = _locator_from_plain(raw, refuse_invalid=True)
    assert locator is not None
    for name, value in raw.items():
        if name in {"table_key", "subsection"} or isinstance(value, list):
            assert locator.extras[name] == value
        else:
            assert getattr(locator, name) == value
    assert _locator_from_plain(to_plain(locator), refuse_invalid=True) == locator


@pytest.mark.parametrize("pages", [[], [1, "page two"], [True], [[1, 2]]])
def test_invalid_multiple_page_indexes_still_refuse(pages) -> None:
    from simulator.battery.migrate import _locator_from_plain

    with pytest.raises(ValueError):
        _locator_from_plain({"pdf_page_index": pages}, refuse_invalid=True)


@pytest.mark.parametrize("reverse", [False, True])
def test_native_page_list_does_not_overwrite_conflicting_extras(reverse) -> None:
    from simulator.battery.migrate import _locator_from_plain

    raw = {"pdf_page_index": [1, 2], "extras": {"pdf_page_index": [2, 3]}}
    if reverse:
        raw = dict(reversed(list(raw.items())))
    with pytest.raises(ValueError, match="conflicting locator fields"):
        _locator_from_plain(raw, refuse_invalid=True)


@pytest.mark.parametrize("reverse", [False, True])
def test_multiple_pages_still_refuse_conflicting_page_aliases(reverse) -> None:
    from simulator.battery.migrate import _locator_from_plain

    raw = {"page": [1, 2], "pdf_page": [2, 3]}
    if reverse:
        raw = dict(reversed(list(raw.items())))
    with pytest.raises(ValueError, match="conflicting locator fields"):
        _locator_from_plain(raw, refuse_invalid=True)


@pytest.mark.parametrize("reverse", [False, True])
def test_multiple_page_aliases_compare_normalized_addresses_without_coercing_extras(reverse) -> None:
    from simulator.battery.migrate import _locator_from_plain

    raw = {"page": ["1", "2"], "pdf_page": [1, 2]}
    if reverse:
        raw = dict(reversed(list(raw.items())))
    locator = _locator_from_plain(raw, refuse_invalid=True)
    assert locator.page is None
    assert locator.extras == raw
    assert _locator_from_plain(to_plain(locator), refuse_invalid=True) == locator


def test_multiple_page_alias_guard_detects_disabled_comparison(monkeypatch) -> None:
    from simulator.battery import migrate as migrate_module

    compare = migrate_module._locator_compare_value
    monkeypatch.setattr(
        migrate_module, "_locator_compare_value",
        lambda name, value: None if name == "page" else compare(name, value),
    )
    with pytest.raises(pytest.fail.Exception):
        test_multiple_pages_still_refuse_conflicting_page_aliases(False)


def test_shornikov_pixel_read_extras_keep_numeric_types_through_yaml() -> None:
    from simulator.battery.migrate import _locator_from_plain

    document = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts/shornikov-2020-mgo-feo-melts.yaml")
        .read_text(encoding="utf-8")
    )
    raw = document["species"]["MgO-FeO_melt"]["observations"][36]["locator"]
    assert raw["pixel_read_1_page"] == [4230.5, 4073.5]
    locator = _locator_from_plain(raw, refuse_invalid=True)
    encoded = yaml.safe_load(yaml.safe_dump(to_plain(locator)))
    decoded = _locator_from_plain(encoded, refuse_invalid=True)
    assert decoded.extras == locator.extras
    assert all(type(value) is float for value in decoded.extras["pixel_read_1_page"])


@pytest.mark.parametrize("shard", range(16))
def test_live_corpus_structured_locator_extras_round_trip(shard: int) -> None:
    from simulator.battery.migrate import _locator_from_plain, discover_extracts

    paths = discover_extracts(REPO_ROOT / "data/literature/extracts")[shard::16]
    for path in paths:
        pending = [yaml.safe_load(path.read_text(encoding="utf-8"))]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in {"locator", "source_locator"} and isinstance(value, dict):
                        try:
                            locator = _locator_from_plain(value, refuse_invalid=True)
                        except ValueError:
                            continue  # Invalid extracts have separate typed-refusal guards.
                        if locator is not None and locator.extras:
                            encoded = to_plain(locator)
                            decoded = _locator_from_plain(encoded, refuse_invalid=True)
                            assert decoded.extras == locator.extras, (path, value, encoded)
                    pending.append(value)
            elif isinstance(node, list):
                pending.extend(node)


def test_gibbons_continuation_page_is_a_distinct_extra_fact() -> None:
    from simulator.battery.migrate import _locator_from_plain

    document = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts/gibbons-1963-thesis-cas-slags-cas.yaml")
        .read_text(encoding="utf-8")
    )
    raw = next(
        row["locator"] for row in document["species"]["CaO"]["context"]
        if row["observation_id"] == "gibbons_1963_table_iii_lime_alumina_silica_runs"
    )
    assert raw["pdf_page_index"] == 57
    assert raw["continuation_pdf_page_index"] == 58
    locator = _locator_from_plain(raw, refuse_invalid=True)
    assert locator.pdf_page_index == 57
    assert locator.extras["continuation_pdf_page_index"] == 58
    assert _locator_from_plain(to_plain(locator), refuse_invalid=True) == locator


def test_unset_grid_indexes_preserve_engine_point_locator_repr() -> None:
    assert repr(Locator(page=2, table="I")) == (
        "Locator(page=2, published_page=None, pdf_page_index=None, "
        "page_verification=None, table_caption_page=None, table_continued=None, "
        "table='I', figure=None, paragraph=None, section=None, equation=None, "
        "line_range=None, note=None, source_path=None, record=None)"
    )


@pytest.mark.parametrize("finding", ["extras", "continuation", "persisted", "repr"])
def test_locator_fix_regressions_detect_reverted_mechanisms(
    finding: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery import migrate as migrate_module
    from simulator.battery import records

    with monkeypatch.context() as mutation:
        if finding == "extras":
            original = to_plain

            def lossy(value):
                encoded = original(value)
                if isinstance(value, Locator) and value.extras:
                    encoded["extras"] = original(value.extras)
                return encoded

            mutation.setitem(globals(), "to_plain", lossy)
            with pytest.raises(AssertionError):
                test_shornikov_pixel_read_extras_keep_numeric_types_through_yaml()
        elif finding == "continuation":
            mutation.setitem(records._LOCATOR_FIELD_ALIASES, "continuation_pdf_page_index", "pdf_page_index")
            mutation.setattr(migrate_module, "_LOCATOR_EXTRA_KEYS", records._LOCATOR_EXTRA_KEYS - {"continuation_pdf_page_index"})
            with pytest.raises(ValueError, match="conflicting locator fields"):
                test_gibbons_continuation_page_is_a_distinct_extra_fact()
        elif finding == "persisted":
            result = migrate(_write_min_tree(tmp_path), write=False)
            payload = to_plain(next(iter(result.observations.values())))
            payload["locator"].update({"row": "A", "row_label": "B"})
            with pytest.raises(ValueError):
                observation_from_plain(payload)
            mutation.setattr(migrate_module, "_validate_persisted_locator_mappings", lambda _payload: None)
            with pytest.raises(pytest.fail.Exception):
                with pytest.raises(ValueError):
                    observation_from_plain(payload)
        else:
            original_repr = Locator.__repr__
            mutation.setattr(
                Locator, "__repr__",
                lambda value: original_repr(value)[:-1] + ", table_row_number=None, table_column_number=None)",
            )
            with pytest.raises(AssertionError):
                test_unset_grid_indexes_preserve_engine_point_locator_repr()


@pytest.mark.parametrize(
    ("baseline_locator", "promoted_locator"),
    [
        ({}, {}),
        (
            {},
            {"row_label": "R-7"},
        ),
        (
            {},
            {"column_label": "Na"},
        ),
        (
            {},
            {
                "row_label": "R-7",
                "column_label": "Na",
                "table_row": "7",
                "table_row_number": 7,
                "table_column": "2",
                "table_column_number": 2,
            },
        ),
    ],
    ids=("no-coordinates", "row-only", "column-only", "both"),
)
def test_locator_alias_promotion_does_not_rekey_observations(
    tmp_path: Path,
    baseline_locator: dict[str, object],
    promoted_locator: dict[str, object],
) -> None:
    baseline_extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    alias_extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    baseline_extract["species"]["Na"]["observations"][0]["locator"].update(
        baseline_locator
    )
    alias_extract["species"]["Na"]["observations"][0]["locator"].update(
        promoted_locator
    )

    baseline = migrate(
        _write_min_tree(tmp_path / "baseline", baseline_extract), write=False
    )
    promoted = migrate(
        _write_min_tree(tmp_path / "promoted", alias_extract), write=False
    )

    assert baseline.observations
    assert promoted.observations
    assert set(promoted.observations) == set(baseline.observations)


def test_row_label_aliases_bind_initial_composition_to_final_phase_row(
    tmp_path: Path,
) -> None:
    initial_rows = [
        (
            formula,
            {
                "observation_id": f"initial melt {formula}",
                "experiment": "run-1",
                "type": "equilibrium_phase_composition",
                "units": "wt%",
                "values": {
                    "quantity": "equilibrium_phase_composition",
                    "value": amount,
                },
                "locator": {"table": "3", "row_label": "initial melt"},
            },
        )
        for formula, amount in (("SiO2", "60"), ("MgO", "40"))
    ]
    final_row = {
        "observation_id": "final phase analysis",
        "experiment": "run-1",
        "type": "equilibrium_phase_composition",
        "units": "wt%",
        "values": {
            "quantity": "equilibrium_phase_composition",
            "value": "12",
        },
        "locator": {"table": "3", "row_label": "final phase analysis"},
    }
    document = {
        "species": {
            "SiO2": {"observations": [initial_rows[0][1]]},
            "MgO": {"observations": [initial_rows[1][1]]},
            "FeO": {"observations": [final_row]},
        }
    }
    migrator = Migrator(root=tmp_path, index={}, aliases={})
    migrator._validate_extract_locators(document, source_key="phase-alias.yaml")
    rows = [*initial_rows, ("FeO", final_row)]

    assert all(
        _locator_from_mapping(row["locator"]).row
        == ("initial melt" if "initial melt" in row["observation_id"] else "final phase analysis")
        for _formula, row in rows
    )
    initial_melts = _same_run_initial_melts(rows)
    bound = _bind_same_run_initial_composition(
        final_row, {"same_run_initial_melts": initial_melts}
    )

    assert bound is not None
    expected = wt_pct_to_mole_fraction(
        {"SiO2": Decimal("60"), "MgO": Decimal("40")}
    )
    assert dict(bound.components) == dict(expected.components)
    assert migrator.result.registry_issues == []


@pytest.mark.parametrize(
    (
        "locator_fields",
        "expected_row",
        "expected_column",
        "expected_table_row",
        "expected_table_column",
    ),
    (
        ({"table_id": "I"}, None, None, None, None),
        ({"row_label": 19}, "19", None, None, None),
        ({"column_header": 2}, None, "2", None, None),
        ({"table_row_number": 19, "table_column_number": 2}, None, None, 19, 2),
    ),
    ids=("neither", "row-only", "column-only", "both"),
)
def test_series_child_ids_survive_locator_validation_without_coordinate_gain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    locator_fields: dict[str, object],
    expected_row: str | None,
    expected_column: str | None,
    expected_table_row: int | None,
    expected_table_column: int | None,
) -> None:
    extract = _extract_with_series_points(locator_fields)
    children = _assert_series_child_ids_unchanged_by_validation(
        tmp_path, monkeypatch, extract
    )

    assert len(children) == 2
    assert all(child.locator is not None for child in children.values())
    assert all(child.locator.row == expected_row for child in children.values())
    assert all(child.locator.column == expected_column for child in children.values())
    assert all(
        child.locator.table_row_number == expected_table_row
        for child in children.values()
    )
    assert all(
        child.locator.table_column_number == expected_table_column
        for child in children.values()
    )


@pytest.mark.parametrize(
    ("source_id", "relative_path"),
    (
        (
            "sato-hickling-mclane-1973-lunar-fo2",
            "data/literature/extracts/sato-hickling-mclane-1973-lunar-fo2.yaml",
        ),
        (
            "mysen-1985-cmas-redox",
            "data/literature/extracts/mysen-1985-cmas-redox.yaml",
        ),
    ),
)
def test_real_extract_series_child_ids_survive_locator_validation(
    monkeypatch: pytest.MonkeyPatch, source_id: str, relative_path: str
) -> None:
    source = REPO_ROOT / relative_path
    validated = Migrator(REPO_ROOT)
    validated._migrate_extract(source)
    validated.finalize()
    validated_ids = {
        observation_id
        for observation_id, observation in validated.result.observations.items()
        if observation.source_id == source_id
    }

    with monkeypatch.context() as baseline:
        baseline.setattr(
            Migrator,
            "_validate_extract_locators",
            lambda _migrator, _document, *, source_key: None,
        )
        unvalidated = Migrator(REPO_ROOT)
        unvalidated._migrate_extract(source)
        unvalidated.finalize()
    unvalidated_ids = {
        observation_id
        for observation_id, observation in unvalidated.result.observations.items()
        if observation.source_id == source_id
    }

    assert len(validated_ids) > 1
    assert validated_ids == unvalidated_ids


def test_series_child_id_pin_detects_locator_canonicalization_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from simulator.battery.migrate import _locator_from_plain, to_plain

    def canonicalizing_validator(
        migrator: Migrator, document: dict, *, source_key: str
    ) -> None:
        pending: list[object] = [document]
        while pending:
            current = pending.pop()
            if isinstance(current, dict):
                for field_name in ("locator", "source_locator"):
                    if field_name not in current:
                        continue
                    locator = _locator_from_plain(
                        current[field_name], refuse_invalid=True
                    )
                    if locator is not None:
                        current[field_name] = to_plain(locator)
                pending.extend(current.values())
            elif isinstance(current, list):
                pending.extend(current)

    with monkeypatch.context() as mutation:
        mutation.setattr(
            Migrator, "_validate_extract_locators", canonicalizing_validator
        )
        with pytest.raises(AssertionError):
            test_series_child_ids_survive_locator_validation_without_coordinate_gain(
                tmp_path, monkeypatch, {"table_id": "I"}, None, None, None, None
            )


def _extract_with_series_points(
    locator_fields: dict[str, object],
) -> dict:
    extract = copy.deepcopy(FIXTURE_EXTRACT)
    observation = extract["species"]["Na"]["observations"][0]
    observation["values"].pop("series")
    observation["values"]["points"] = [
        {
            "T_K": 1200,
            "pressure_atm": 1.0,
            "locator": {"page": 2, "table": "I", **locator_fields},
        },
        {
            "T_K": 1300,
            "pressure_atm": 2.0,
            "locator": {"page": 2, "table": "I", **locator_fields},
        },
    ]
    return extract


def _migrate_series_children(tmp_path: Path, extract: dict, name: str) -> dict:
    result = migrate(_write_min_tree(tmp_path / name, extract), write=False)
    return {
        observation_id: observation
        for observation_id, observation in result.observations.items()
        if observation_id.startswith("fixture-source::na_psat::T=")
    }


def _assert_series_child_ids_unchanged_by_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extract: dict,
) -> dict:
    baseline_extract = copy.deepcopy(extract)
    with monkeypatch.context() as baseline:
        baseline.setattr(
            Migrator,
            "_validate_extract_locators",
            lambda _migrator, _document, *, source_key: None,
        )
        unnormalized = _migrate_series_children(
            tmp_path, baseline_extract, "unnormalized"
        )

    validated_extract = copy.deepcopy(extract)
    validator = Migrator(root=tmp_path / "validation", index={}, aliases={})
    validator._validate_extract_locators(
        validated_extract, source_key="fixture-source.yaml"
    )
    assert validator.result.registry_issues == []
    validated = _migrate_series_children(
        tmp_path, validated_extract, "validated"
    )
    assert validated
    assert set(validated) == set(unnormalized)
    return validated


def _locator_from_mapping(payload: object) -> Locator | None:
    from simulator.battery.migrate import locator_from_mapping

    return locator_from_mapping(payload)


def test_canonical_and_alias_page_indexes_normalize_before_conflict_check() -> None:
    from simulator.battery.enums import RefusalReason
    from simulator.battery.migrate import _locator_from_plain, locator_from_mapping

    canonical = locator_from_mapping(
        {"pdf_page_index": "1", "section": "Methods"}
    )
    invalid = locator_from_mapping(
        {"pdf_page_index": "page one", "section": "Methods"}
    )
    legacy_range = locator_from_mapping(
        {"pdf_page_index": "14–15", "section": "Methods"}
    )

    assert canonical is not None and canonical.pdf_page_index == 1
    assert invalid is None
    assert legacy_range is not None and legacy_range.pdf_page_index == "14–15"
    for bad_locator in (
        {"pdf_page_index": "page one", "section": "Methods"},
    ):
        with pytest.raises(ValueError) as exc_info:
            _locator_from_plain(bad_locator, refuse_invalid=True)
        assert exc_info.value.reason is RefusalReason.INVALID_SOURCE


def test_printed_labels_and_table_grid_indexes_are_distinct_locator_facts() -> None:
    from simulator.battery.migrate import _locator_from_plain, locator_from_mapping

    locator = locator_from_mapping(
        {
            "row_label": "E",
            "table_row": "1",
            "column_label": "Na",
            "table_column_number": 2,
        }
    )
    same_grid_aliases = locator_from_mapping(
        {"table_row": "1", "table_row_number": 1, "table": "I"}
    )
    conflicting_grid_aliases = locator_from_mapping(
        {"table_row": 1, "table_row_number": 2, "table": "I"}
    )

    assert locator is not None
    assert locator.row == "E"
    assert locator.table_row_number == 1
    assert locator.column == "Na"
    assert locator.table_column_number == 2
    assert same_grid_aliases is not None
    assert same_grid_aliases.table_row_number == 1
    assert conflicting_grid_aliases == Locator(table="I")
    with pytest.raises(ValueError, match="conflicting locator fields"):
        _locator_from_plain(
            {"table_row": 1, "table_row_number": 2, "table": "I"},
            refuse_invalid=True,
        )
    with pytest.raises(ValueError) as exc_info:
        _locator_from_plain(
            {"table_row_number": 0, "table": "I"}, refuse_invalid=True
        )
    assert exc_info.value.reason is RefusalReason.INVALID_SOURCE


def test_persisted_observation_refuses_unknown_locator_keys(tmp_path: Path) -> None:
    result = migrate(_write_min_tree(tmp_path), write=False)
    payload = to_plain(next(iter(result.observations.values())))
    assert isinstance(payload, dict)
    payload["locator"]["unreviewed_locator_axis"] = "axis"

    with pytest.raises(ValueError) as exc_info:
        observation_from_plain(payload)

    assert getattr(exc_info.value, "reason", None) is RefusalReason.INVALID_SOURCE
    assert "unreviewed_locator_axis" in str(exc_info.value)


@pytest.mark.parametrize("decoder_name", [
    "work_from_plain", "bench_from_plain", "experiment_from_plain", "observation_from_plain",
])
@pytest.mark.parametrize("location_path", ["locator", "source_locator", "admission_evidence"])
def test_persisted_boundaries_refuse_conflicting_aliases(
    decoder_name: str, location_path: str,
) -> None:
    from simulator.battery import migrate as migrate_module

    conflict = {"page": 2, "row": "A", "row_label": "B"}
    payload = (
        {"admission": {"decided_by": {"evidence": conflict}}}
        if location_path == "admission_evidence"
        else {location_path: conflict}
    )
    with pytest.raises(ValueError) as exc_info:
        getattr(migrate_module, decoder_name)(payload)
    assert exc_info.value.reason is RefusalReason.INVALID_SOURCE
    assert "conflicting locator fields" in str(exc_info.value)
    assert "row_label" in str(exc_info.value)


def test_persisted_admission_decision_evidence_refuses_unknown_locator_keys(
    tmp_path: Path,
) -> None:
    result = migrate(_write_min_tree(tmp_path), write=False)
    payload = to_plain(next(iter(result.observations.values())))
    assert isinstance(payload, dict)
    payload["admission"]["status"] = "admitted"
    payload["admission"]["decided_by"] = {
        "worker": "reviewer",
        "date": "2026-11-16",
        "evidence": {"page": 2, "unreviewed_locator_axis": "axis"},
    }

    _assert_invalid_admission_evidence(payload)


def test_persisted_admission_evidence_guard_mutation_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    migrate_module = importlib.import_module("simulator.battery.migrate")
    result = migrate(_write_min_tree(tmp_path), write=False)
    payload = to_plain(next(iter(result.observations.values())))
    assert isinstance(payload, dict)
    payload["admission"]["status"] = "admitted"
    payload["admission"]["decided_by"] = {
        "worker": "reviewer",
        "date": "2026-11-16",
        "evidence": {"page": 2, "unreviewed_locator_axis": "axis"},
    }

    with monkeypatch.context() as mutation:
        mutation.setattr(
            migrate_module, "_validate_persisted_locator_mappings", lambda _payload: None
        )
        with pytest.raises(AssertionError):
            _assert_invalid_admission_evidence(payload)


def _assert_invalid_admission_evidence(payload: dict) -> None:
    try:
        observation_from_plain(payload)
    except ValueError as exc:
        assert getattr(exc, "reason", None) is RefusalReason.INVALID_SOURCE
        assert "unreviewed_locator_axis" in str(exc)
    else:
        raise AssertionError("admission evidence with an unknown locator key was accepted")


def test_compilation_refuses_unknown_locator_keys_with_invalid_source(
    tmp_path: Path,
) -> None:
    root = _write_min_tree(tmp_path)
    path = (
        root
        / "data"
        / "literature"
        / "compilations"
        / "fixture-compilation"
        / "records"
        / "record.yaml"
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_compilation.v1",
                "source_id": "fixture-compilation",
                "source": {"citation": "Fixture compilation"},
                "record_id": "record-1",
                "formula": "Fe",
                "source_locator": {
                    "page": 2,
                    "unreviewed_locator_axis": "axis",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    migrator = Migrator(root=root, index={}, aliases={})

    migrator._migrate_compilation_file(path)

    assert migrator.result.observations == {}
    assert len(migrator.result.registry_issues) == 1
    issue = migrator.result.registry_issues[0]
    assert issue.reason is RefusalReason.INVALID_SOURCE
    assert issue.path.endswith("record.yaml].source_locator")
    assert "unreviewed_locator_axis" in issue.detail


@pytest.mark.parametrize(
    ("relative_path", "expected_count"),
    _COMPILATION_GENERATOR_RECORDS,
    ids=("janaf", "b1544", "b1452", "b1259", "b2131"),
)
def test_compilation_generator_routes_migrate_real_records(
    relative_path: str, expected_count: int
) -> None:
    source_path = REPO_ROOT / relative_path
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})

    migrator._migrate_compilation_file(source_path)

    assert len(migrator.result.observations) == expected_count


@pytest.mark.parametrize(
    ("relative_path", "_expected_count"),
    _COMPILATION_GENERATOR_RECORDS,
    ids=("janaf", "b1544", "b1452", "b1259", "b2131"),
)
def test_compilation_generator_routes_refuse_unknown_source_locator_keys(
    tmp_path: Path, relative_path: str, _expected_count: int
) -> None:
    _assert_compilation_generator_refuses_unknown_locator(
        tmp_path, relative_path
    )


def test_compilation_generator_refusal_mutation_is_detected_for_every_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    migrate_module = importlib.import_module("simulator.battery.migrate")
    find_unknown = migrate_module._locator_mapping_first_unsupported_key

    def ignore_unknown(payload: object) -> str | None:
        if isinstance(payload, dict) and "unreviewed_locator_axis" in payload:
            return None
        return find_unknown(payload)

    with monkeypatch.context() as mutation:
        mutation.setattr(
            migrate_module,
            "_locator_mapping_first_unsupported_key",
            ignore_unknown,
        )
        for relative_path, _expected_count in _COMPILATION_GENERATOR_RECORDS:
            with pytest.raises(AssertionError):
                _assert_compilation_generator_refuses_unknown_locator(
                    tmp_path / Path(relative_path).stem, relative_path
                )


def _assert_compilation_generator_refuses_unknown_locator(
    tmp_path: Path, relative_path: str
) -> None:
    source_path = REPO_ROOT / relative_path
    source_doc = _load_compilation_document(source_path)
    source_locator = source_doc.get("source_locator")
    source_locator = dict(source_locator) if isinstance(source_locator, dict) else {}
    source_locator["unreviewed_locator_axis"] = "axis"
    source_doc["source_locator"] = source_locator

    path = tmp_path / relative_path
    path.parent.mkdir(parents=True)
    _write_compilation_document(path, source_doc)
    migrator = Migrator(root=tmp_path, index={}, aliases={})

    migrator._migrate_compilation_file(path)

    assert migrator.result.observations == {}
    assert len(migrator.result.registry_issues) == 1
    issue = migrator.result.registry_issues[0]
    assert issue.reason is RefusalReason.INVALID_SOURCE
    assert issue.path.endswith(".source_locator")
    assert "unreviewed_locator_axis" in issue.detail


def _load_compilation_document(path: Path) -> dict:
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_compilation_document(path: Path, document: dict) -> None:
    if path.suffix == ".json":
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def test_conflicting_locator_aliases_are_listed_as_invalid_source(
    tmp_path: Path,
) -> None:
    baseline = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    locator = extract["species"]["Na"]["observations"][0]["locator"]
    locator.update(
        {
            "row": "R-7",
            "row_label": "R-8",
            "column": "Na",
            "column_header": "MgO",
        }
    )

    result = migrate(
        _write_min_tree(tmp_path / "conflicting-locator", extract), write=False
    )

    assert result.validation is not None
    issues = [
        issue
        for issue in result.validation.hard_issues
        if "conflicting locator" in issue.detail
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.INVALID_SOURCE
    assert all(
        key in issues[0].detail
        for key in ("row", "row_label", "column", "column_header")
    )
    expected = next(
        iter(
            migrate(
                _write_min_tree(tmp_path / "baseline", baseline), write=False
            ).observations.values()
        )
    )
    migrated = next(iter(result.observations.values()))
    assert migrated.locator == Locator(page=2, table="I")
    assert migrated.value == expected.value


def test_unknown_locator_key_is_a_typed_validation_issue(tmp_path: Path) -> None:
    baseline = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    locator = extract["species"]["Na"]["observations"][0]["locator"]
    locator["unreviewed_locator_axis"] = "value"

    result = migrate(
        _write_min_tree(tmp_path / "unknown-locator", extract), write=False
    )

    assert result.validation is not None
    issues = [
        issue
        for issue in result.validation.hard_issues
        if "unreviewed_locator_axis" in issue.detail
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.INVALID_SOURCE
    assert issues[0].path.endswith(".locator")
    expected = next(
        iter(
            migrate(
                _write_min_tree(tmp_path / "baseline", baseline), write=False
            ).observations.values()
        )
    )
    migrated = next(iter(result.observations.values()))
    assert migrated.locator is None
    assert migrated.value == expected.value


def test_distinct_registry_locator_facts_do_not_drop_observation_values(
    tmp_path: Path,
) -> None:
    baseline = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["experiments"] = [
        {
            "experiment_id": "distinct-facts-run",
            "locator": {
                "page": 2,
                "row_label": "E",
                "table_row_number": "1",
                "column_label": "Na",
                "table_column_number": "2",
            },
        }
    ]

    result = migrate(
        _write_min_tree(tmp_path / "registry-conflict", extract), write=False
    )
    expected = next(
        iter(
            migrate(
                _write_min_tree(tmp_path / "baseline", baseline), write=False
            ).observations.values()
        )
    )

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert result.registry_issues == []
    experiment = next(iter(result.experiments.values()))
    assert experiment.locator is not None
    assert experiment.locator.row == "E"
    assert experiment.locator.table_row_number == 1
    assert experiment.locator.column == "Na"
    assert experiment.locator.table_column_number == 2
    migrated = next(iter(result.observations.values()))
    assert migrated.value == expected.value
