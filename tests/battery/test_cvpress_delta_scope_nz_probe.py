"""Remote full-store out-of-scope row decode, source IDs starting with N."""

from tests.battery.test_migrate_split_baseline import (
    _assert_complete_store_regeneration_matches_checked_in_bytes,
    _format_store_delta_report,
    _source_in_report_filter,
)


def test_scope_report_keeps_each_matching_source_on_shared_work_rows() -> None:
    details = {
        "observations": {},
        "work": [{
            "path": "data/literature/works/10.1007_s11663-018-1471-9.yaml",
            "source_ids": [
                "costa-jacobson-2015",
                "zaitsev-2000-ms2000-045",
                "zhao-2019-ce2o3-ternary-thermodynamics",
            ],
            "changes": [{
                "field": "work.experiments[0].conditions.temperature_K.locator.note",
                "old": "<absent>",
                "new": "printed_cell=[null, null]",
            }],
        }],
        "other": [],
        "changed_store_paths": 1,
    }

    report = _format_store_delta_report(details, "scope", {"N-Z"})
    rows = report.splitlines()[1:]
    assert len(rows) == 2
    assert rows[0].startswith("zaitsev-2000-ms2000-045 | ")
    assert rows[1].startswith("zhao-2019-ce2o3-ternary-thermodynamics | ")


def test_source_id_report_ranges_cover_each_prefix_once() -> None:
    assert _source_in_report_filter("aithala-2026", {"A-C"})
    assert _source_in_report_filter("costa-2025", {"D-F"}) is False
    assert _source_in_report_filter("miki-1999", {"M-M"})
    assert _source_in_report_filter("zhao-2019", {"N-P"}) is False
    assert _source_in_report_filter("10.1007_record", {"A-C"})


def test_full_store_out_of_scope_rows_n() -> None:
    _assert_complete_store_regeneration_matches_checked_in_bytes("scope", ("N",))
