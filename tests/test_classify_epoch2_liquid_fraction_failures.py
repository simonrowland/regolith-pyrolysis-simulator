from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.classify_epoch2_liquid_fraction_failures import (
    BELOW_LIQUIDUS_HONEST_REFUSAL,
    EXPECTED_RETAINED_COUNTS,
    NOW_RESOLVED,
    REFERENCE_MISSING,
    SOLIDUS_MISSING,
    SUBPROCESS_DIED_ENGINE_FAILURE,
    UNVERIFIED,
    UNCLASSIFIED_PRESERVING_RAW,
    build_report,
    classify_retained_row,
    load_retained_rows,
    main,
)


def _retained(**overrides):
    row = {
        "engine_epoch": 2,
        "grid_key_id": 101,
        "expedited_key": "epoch2-real-shaped-key",
        "status": "error",
        "status_kind": "failure",
        "refusal_reason": "LiquidFractionInvalidError",
        "temperature_C": 1200.0,
        "reference_liquidus_C": 1300.0,
        "reference_solidus_C": None,
        "current_engine_epoch": 4,
        "current_status": "error",
        "current_status_kind": "failure",
        "current_backend_status_reason": "LiquidFractionInvalidError",
        "current_identity_matches": True,
        "raw_payload": json.dumps(
            {
                "engine_invoked": True,
                "exception": {
                    "type": "LiquidFractionInvalidError",
                    "message": "liquid_fraction_mismatch: supplied=0.505938 "
                    "phase_masses=0.5059150694794936",
                },
                "format": "alphamelts-subprocess-v1",
            }
        ),
        "alpha_backend_diagnostics_json": json.dumps(
            {
                "message": "liquid_fraction_mismatch: supplied=0.505938 "
                "phase_masses=0.5059150694794936",
                "type": "LiquidFractionInvalidError",
            }
        ),
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("row", "expected"),
    (
        (
            _retained(
                current_status="out_of_domain",
                current_status_kind="refusal",
                current_refusal_reason="no_convergence",
            ),
            BELOW_LIQUIDUS_HONEST_REFUSAL,
        ),
        (_retained(), SOLIDUS_MISSING),
        (
            _retained(reference_liquidus_C=None),
            REFERENCE_MISSING,
        ),
        (
            _retained(current_status="ok", current_status_kind="success"),
            NOW_RESOLVED,
        ),
        (
            _retained(temperature_C=1400.0),
            UNCLASSIFIED_PRESERVING_RAW,
        ),
    ),
)
def test_classifies_every_closed_set_outcome_from_real_shaped_rows(row, expected):
    assert classify_retained_row(row) == expected


def test_subprocess_death_is_classified_as_direct_engine_failure():
    row = _retained(
        current_status="error",
        current_status_kind="failure",
        current_backend_status_reason="subprocess_died",
    )

    assert classify_retained_row(row) == SUBPROCESS_DIED_ENGINE_FAILURE


def test_absent_or_identity_mismatched_later_output_is_unverified():
    absent = _retained(current_identity_matches=False)
    mismatched = _retained(
        current_status="ok",
        current_status_kind="success",
        current_identity_matches=False,
    )

    assert classify_retained_row(absent) == UNVERIFIED
    assert classify_retained_row(mismatched) == UNVERIFIED


def test_report_is_deterministic_zero_filled_and_preserves_unknown_raw():
    unknown = _retained(grid_key_id=2, temperature_C=1400.0, raw_marker="keep-me")
    resolved = _retained(
        grid_key_id=1,
        current_status="ok",
        current_status_kind="success",
    )

    first = build_report([unknown, resolved])
    second = build_report([resolved, unknown])

    assert first == second
    assert first["counts"] == {
        BELOW_LIQUIDUS_HONEST_REFUSAL: 0,
        SOLIDUS_MISSING: 0,
        REFERENCE_MISSING: 0,
        NOW_RESOLVED: 1,
        UNVERIFIED: 0,
        SUBPROCESS_DIED_ENGINE_FAILURE: 0,
        UNCLASSIFIED_PRESERVING_RAW: 1,
    }
    assert first["unclassified"] == [{"raw": unknown}]
    assert [row["grid_key_id"] for row in first["rows"]] == [1, 2]


def _create_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE grid_keys (
            id INTEGER PRIMARY KEY,
            temperature_C REAL NOT NULL
        );
        CREATE TABLE alphamelts_outputs (
            id INTEGER PRIMARY KEY,
            grid_key_id INTEGER NOT NULL,
            expedited_key TEXT,
            engine_epoch INTEGER NOT NULL,
            status TEXT,
            status_kind TEXT,
            refusal_reason TEXT,
            raw_payload TEXT,
            alpha_backend_status_reason TEXT,
            alpha_backend_diagnostics_json TEXT,
            engine_mode TEXT,
            engine_model TEXT,
            run_mode TEXT,
            native_input_json TEXT,
            curve_liquidus_T_C REAL,
            finder_liquidus_T_C REAL,
            alpha_liquidus_T_C REAL,
            generic_liquidus_T_C REAL,
            curve_solidus_T_C REAL,
            finder_solidus_T_C REAL,
            alpha_solidus_T_C REAL
        );
        """
    )
    # Shapes/counts: docs-private/research/2026-07-12-epoch2-failure-taxonomy/
    # findings.md:15,25,146-147. Rows retain the production identity fields used
    # to distinguish a matching replay from an unrelated later grid-key output.
    controls = json.dumps({"pressure_bar": 1.0, "temperature_C": 1200.0})
    next_id = 1
    for grid_key_id in range(1, 58):
        key = f"key-{grid_key_id}"
        connection.execute(
            "INSERT INTO grid_keys VALUES (?, ?)", (grid_key_id, 1200.0)
        )
        reference_liquidus = 1300.0 if grid_key_id <= 48 else None
        connection.execute(
            """INSERT INTO alphamelts_outputs (
                id, grid_key_id, expedited_key, engine_epoch, status, status_kind,
                raw_payload, engine_mode, engine_model, run_mode, native_input_json,
                curve_liquidus_T_C
            ) VALUES (?, ?, ?, 1, 'ok', 'success', '{}', 'alphamelts',
                      'pMELTS', 'isothermal', ?, ?)""",
            (next_id, grid_key_id, key, controls, reference_liquidus),
        )
        next_id += 1
        connection.execute(
            """INSERT INTO alphamelts_outputs (
                id, grid_key_id, expedited_key, engine_epoch, status, status_kind,
                refusal_reason, raw_payload, alpha_backend_status_reason,
                alpha_backend_diagnostics_json, engine_mode, engine_model,
                run_mode, native_input_json
            ) VALUES (?, ?, ?, 2, 'error', 'failure',
                      'LiquidFractionInvalidError', '{"engine_invoked":true}',
                      'LiquidFractionInvalidError',
                      '{"type":"LiquidFractionInvalidError"}', 'alphamelts',
                      'pMELTS', 'isothermal', ?)""",
            (next_id, grid_key_id, key, controls),
        )
        next_id += 1
        connection.execute(
            """INSERT INTO alphamelts_outputs (
                id, grid_key_id, expedited_key, engine_epoch, status, status_kind,
                raw_payload, engine_mode, engine_model, run_mode, native_input_json
            ) VALUES (?, ?, ?, 4, 'ok', 'success', '{}', 'alphamelts',
                      'pMELTS', 'isothermal', ?)""",
            (next_id, grid_key_id, key, controls),
        )
        next_id += 1
    connection.commit()
    connection.close()


def test_read_only_loader_joins_references_and_latest_replay(tmp_path):
    db_path = tmp_path / "epoch.db"
    _create_database(db_path)

    rows = load_retained_rows(db_path)

    assert len(rows) == 57
    assert rows[0]["reference_liquidus_C"] == 1300.0
    assert rows[0]["reference_solidus_C"] is None
    assert rows[0]["current_engine_epoch"] == 4
    assert all(row["current_identity_matches"] for row in rows)
    input_counts = {
        SOLIDUS_MISSING: sum(
            row["reference_liquidus_C"] is not None for row in rows
        ),
        REFERENCE_MISSING: sum(
            row["reference_liquidus_C"] is None for row in rows
        ),
    }
    assert input_counts == EXPECTED_RETAINED_COUNTS
    assert all(classify_retained_row(row) == NOW_RESOLVED for row in rows)


@pytest.mark.parametrize("mutation", ("fewer", "more"))
def test_loader_fails_loud_when_retained_cohort_size_is_wrong(tmp_path, mutation):
    db_path = tmp_path / "epoch.db"
    _create_database(db_path)
    with sqlite3.connect(db_path) as connection:
        if mutation == "fewer":
            connection.execute(
                "DELETE FROM alphamelts_outputs WHERE grid_key_id = 57"
            )
        else:
            connection.execute(
                """INSERT INTO alphamelts_outputs (
                    id, grid_key_id, expedited_key, engine_epoch, status,
                    status_kind, refusal_reason, raw_payload,
                    alpha_backend_status_reason, engine_mode, engine_model,
                    run_mode, native_input_json
                ) VALUES (1000, 1, 'key-1', 2, 'error', 'failure',
                          'LiquidFractionInvalidError', '{}',
                          'LiquidFractionInvalidError', 'alphamelts', 'pMELTS',
                          'isothermal',
                          '{"pressure_bar":1.0,"temperature_C":1200.0}')"""
            )

    with pytest.raises(RuntimeError, match="expected total=57"):
        load_retained_rows(db_path)


def test_loader_ignores_newer_identity_mismatch_and_finds_matching_replay(tmp_path):
    db_path = tmp_path / "epoch.db"
    _create_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """INSERT INTO alphamelts_outputs (
                id, grid_key_id, expedited_key, engine_epoch, status, status_kind,
                raw_payload, engine_mode, engine_model, run_mode, native_input_json
            ) VALUES (1000, 1, 'key-1', 5, 'ok', 'success', '{}', 'alphamelts',
                      'rhyolite-MELTS', 'path', '{"temperature_C":999}')"""
        )

    rows = load_retained_rows(db_path)

    assert rows[0]["current_engine_epoch"] == 4
    assert classify_retained_row(rows[0]) == NOW_RESOLVED


def test_loader_marks_row_without_matching_replay_unverified(tmp_path):
    db_path = tmp_path / "epoch.db"
    _create_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE alphamelts_outputs SET run_mode = 'path' "
            "WHERE grid_key_id = 1 AND engine_epoch = 4"
        )

    rows = load_retained_rows(db_path)

    assert rows[0]["current_engine_epoch"] is None
    assert classify_retained_row(rows[0]) == UNVERIFIED


def test_cli_emits_deterministic_report(tmp_path, capsys):
    db_path = tmp_path / "epoch.db"
    _create_database(db_path)

    assert main(["--db", str(db_path)]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["classification_mode"] == "recorded-payload-no-live-engine"
    assert report["consumer"] == "grind-campaign-controller"
    assert report["counts"][NOW_RESOLVED] == 57
    assert report["total_retained"] == 57
