"""Observation ids hash identity, not locator or provenance (t-1357).

The clean-row digest is the fixture series point already stored as
``fixture-source::na_psat::T=1200.0:h=8f69f8b68978``. A locator-only edit
must leave that digest, and every id migrated from the row, unchanged.
A value or condition edit must change it.
"""

from __future__ import annotations

from pathlib import Path

from simulator.battery.stable_ids import series_point_id, series_row_extra
from tests.battery.test_migrate import _write_min_tree

# Recorded from the fixture row {T_K: 1200.0, pressure_atm: 1.0} before
# provenance was removed from the digest. Temperature is not part of the hash.
_CLEAN_ROW = {"T_K": 1200.0, "pressure_atm": 1.0}
_CLEAN_EXTRA = "h=8f69f8b68978"

# A non-mapping exploded cell has no locator. Recorded from the fallback
# digest of the scalar 1.25 so that path stays a content hash.
_SCALAR_POINT_ID = "parent::points:h=8e007eaa5059"


def test_clean_row_digest_is_the_fixture_point() -> None:
    assert series_row_extra(_CLEAN_ROW) == _CLEAN_EXTRA
    assert series_row_extra({"pressure_atm": 1.0}) == _CLEAN_EXTRA


def test_empty_mapping_stays_in_the_digest() -> None:
    assert series_row_extra({"pressure_atm": 1.0, "blank": {}}) != series_row_extra(
        {"pressure_atm": 1.0}
    )


def test_non_mapping_point_digest_is_unchanged() -> None:
    from simulator.battery.migrate import _exploded_point_id

    assert (
        _exploded_point_id("parent", {"container": "points"}, None, 1.25, None)
        == _SCALAR_POINT_ID
    )


def test_locator_page_does_not_change_row_extra() -> None:
    def row(page: int) -> dict:
        return {**_CLEAN_ROW, "locator": {"page": page, "table": "I"}}

    assert series_row_extra(row(5)) == series_row_extra(row(6)) == _CLEAN_EXTRA


def test_nested_locator_page_does_not_change_row_extra() -> None:
    def row(page: int) -> dict:
        return {
            **_CLEAN_ROW,
            "x_Mo": {"value": 0.2, "locator": {"page": page, "table": "4"}},
        }

    bare = {**_CLEAN_ROW, "x_Mo": {"value": 0.2}}
    assert series_row_extra(row(5)) == series_row_extra(row(6)) == series_row_extra(bare)
    assert series_row_extra(row(5), include_locator=False) == series_row_extra(bare)


def test_include_locator_flag_does_not_change_the_digest() -> None:
    row = {**_CLEAN_ROW, "locator": {"page": 5, "table": "I"}, "note": "printed"}
    assert series_row_extra(row, include_locator=True) == series_row_extra(
        row, include_locator=False
    )
    assert series_row_extra(row) == _CLEAN_EXTRA


def test_note_quote_and_printed_ordinal_are_not_identity() -> None:
    assert series_row_extra({**_CLEAN_ROW, "note": "printed"}) == _CLEAN_EXTRA
    assert series_row_extra({**_CLEAN_ROW, "quote": "1.0 atm"}) == _CLEAN_EXTRA
    assert series_row_extra({**_CLEAN_ROW, "row": 4}) == _CLEAN_EXTRA
    assert series_row_extra({**_CLEAN_ROW, "row_number": 4}) == _CLEAN_EXTRA
    assert series_row_extra({**_CLEAN_ROW, "row_as_printed": "4"}) == _CLEAN_EXTRA
    assert (
        series_row_extra({**_CLEAN_ROW, "pdftotext_line_range": "12-14"})
        == _CLEAN_EXTRA
    )


def test_locator_paragraph_is_not_a_row_label() -> None:
    a = {**_CLEAN_ROW, "locator": {"paragraph": "solution-1", "page": 5}}
    b = {**_CLEAN_ROW, "locator": {"paragraph": "solution-2", "page": 6}}
    assert series_row_extra(a) == series_row_extra(b) == _CLEAN_EXTRA
    assert series_row_extra(a, include_locator=False) == _CLEAN_EXTRA


def test_run_label_stays_identity_when_the_page_changes() -> None:
    def row(page: int) -> dict:
        return {"run": "V 66", "T_K": 1671, "MgO_wt_pct": 18.2, "locator": {"page": page}}

    labeled = series_row_extra({"run": "V 66", "T_K": 1671, "MgO_wt_pct": 18.2})
    assert series_row_extra(row(5)) == series_row_extra(row(6)) == labeled
    assert labeled.startswith("row=v-66:")


def test_value_edit_changes_row_extra() -> None:
    edited = series_row_extra({"T_K": 1200.0, "pressure_atm": 2.0})
    assert edited != _CLEAN_EXTRA


def test_condition_edit_changes_row_extra() -> None:
    base = series_row_extra({**_CLEAN_ROW, "log_fO2": -9.0})
    edited = series_row_extra({**_CLEAN_ROW, "log_fO2": -10.0})
    assert base != edited


def test_temperature_edit_changes_point_id_not_the_row_digest() -> None:
    assert series_row_extra({"T_K": 1300.0, "pressure_atm": 1.0}) == _CLEAN_EXTRA
    low = series_point_id("parent", temperature=1200.0, extra=_CLEAN_EXTRA)
    high = series_point_id("parent", temperature=1300.0, extra=_CLEAN_EXTRA)
    assert low == "parent::T=1200.0:h=8f69f8b68978"
    assert high == "parent::T=1300.0:h=8f69f8b68978"
    assert low != high


def test_exploded_fallback_does_not_hash_locator() -> None:
    from simulator.battery.migrate import _exploded_point_id

    def raw(page: int) -> dict:
        return {**_CLEAN_ROW, "locator": {"page": page, "table": "I"}}

    item = {"container": "rows"}
    page_5 = _exploded_point_id("parent", item, None, raw(5), None)
    page_6 = _exploded_point_id("parent", item, None, raw(6), None)
    assert page_5 == page_6
    assert page_5 == f"parent::rows:{_CLEAN_EXTRA}"


def _pressure_points(page: int, pressure: float) -> list[dict]:
    return [
        {
            "T_C": 1700.0,
            "P": 2.0e-4,
            "composition_wt_pct": {"SiO2": 45.4, "Al2O3": 28.63, "CaO": 16.39},
        },
        {
            "T_C": 1800.0,
            "P": pressure,
            "composition_wt_pct": {"SiO2": 50.0, "Al2O3": 25.0, "CaO": 25.0},
            "locator": {"page": page, "table": "2"},
            "note": "printed",
            "quote": "as published",
            "heat_no": {"value": 3, "locator": {"page": page, "table": "2"}},
        },
    ]


def _migrate_ids(root: Path, *, page: int, pressure: float = 1.0e-4) -> set[str]:
    from simulator.battery.migrate import migrate

    extract = {
        "source_id": "fixture-source",
        "source": {"citation": "Fixture (2026)", "doi": "10.1234/FIXTURE"},
        "species": {
            "Fe": {
                "observations": [
                    {
                        "observation_id": "fixture_points",
                        "type": "p_partial",
                        "locator": {"page": 2, "figure": "1"},
                        "phase": "g",
                        "units": "Pa",
                        "values": {
                            "quantity": "p_partial",
                            "method_class": "measured_direct",
                            "points": _pressure_points(page, pressure),
                        },
                    }
                ]
            }
        },
    }
    result = migrate(_write_min_tree(root, extract), write=False)
    return set(result.observations)


def test_migrated_ids_ignore_a_locator_page_edit(tmp_path: Path) -> None:
    page_5 = _migrate_ids(tmp_path / "page-5", page=5)
    page_6 = _migrate_ids(tmp_path / "page-6", page=6)
    assert page_5 == page_6
    assert len(page_5) >= 2


def test_migrated_id_changes_when_the_printed_value_changes(tmp_path: Path) -> None:
    base = _migrate_ids(tmp_path / "base", page=5, pressure=1.0e-4)
    edited = _migrate_ids(tmp_path / "edited", page=5, pressure=2.0e-4)
    assert len(base) == len(edited) >= 2
    assert base != edited


def test_migrated_id_changes_when_the_temperature_changes(tmp_path: Path) -> None:
    base = _migrate_ids(tmp_path / "base", page=5)
    hotter = _pressure_points(5, 1.0e-4)
    hotter[1]["T_C"] = 1900.0
    from simulator.battery.migrate import migrate

    extract = {
        "source_id": "fixture-source",
        "source": {"citation": "Fixture (2026)", "doi": "10.1234/FIXTURE"},
        "species": {
            "Fe": {
                "observations": [
                    {
                        "observation_id": "fixture_points",
                        "type": "p_partial",
                        "locator": {"page": 2, "figure": "1"},
                        "phase": "g",
                        "units": "Pa",
                        "values": {
                            "quantity": "p_partial",
                            "method_class": "measured_direct",
                            "points": hotter,
                        },
                    }
                ]
            }
        },
    }
    edited = set(migrate(_write_min_tree(tmp_path / "hot", extract), write=False).observations)
    assert len(base) == len(edited) >= 2
    assert base != edited
