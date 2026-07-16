from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.classify_epoch2_liquid_fraction_failures import (
    BELOW_LIQUIDUS_HONEST_REFUSAL,
    NOW_RESOLVED,
    REFERENCE_MISSING,
    SOLIDUS_MISSING,
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


def test_arbitrary_refusal_is_not_relabelled_as_honest():
    row = _retained(
        current_status="out_of_domain",
        current_status_kind="refusal",
        current_refusal_reason="subprocess_died",
    )

    assert classify_retained_row(row) == SOLIDUS_MISSING


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
            curve_liquidus_T_C REAL,
            finder_liquidus_T_C REAL,
            alpha_liquidus_T_C REAL,
            generic_liquidus_T_C REAL,
            curve_solidus_T_C REAL,
            finder_solidus_T_C REAL,
            alpha_solidus_T_C REAL
        );
        INSERT INTO grid_keys VALUES (7, 1200.0);
        INSERT INTO alphamelts_outputs (
            id, grid_key_id, expedited_key, engine_epoch, status, status_kind,
            refusal_reason, curve_liquidus_T_C
        ) VALUES (1, 7, 'key-7', 1, 'ok', 'success', NULL, 1300.0);
        INSERT INTO alphamelts_outputs (
            id, grid_key_id, expedited_key, engine_epoch, status, status_kind,
            refusal_reason, raw_payload, alpha_backend_status_reason,
            alpha_backend_diagnostics_json
        ) VALUES (
            2, 7, 'key-7', 2, 'error', 'failure',
            'LiquidFractionInvalidError', '{"engine_invoked":true}',
            'LiquidFractionInvalidError',
            '{"type":"LiquidFractionInvalidError"}'
        );
        INSERT INTO alphamelts_outputs (
            id, grid_key_id, expedited_key, engine_epoch, status, status_kind
        ) VALUES (3, 7, 'key-7', 4, 'ok', 'success');
        """
    )
    connection.commit()
    connection.close()


def test_read_only_loader_joins_references_and_latest_replay(tmp_path):
    db_path = tmp_path / "epoch.db"
    _create_database(db_path)

    rows = load_retained_rows(db_path)

    assert len(rows) == 1
    assert rows[0]["reference_liquidus_C"] == 1300.0
    assert rows[0]["reference_solidus_C"] is None
    assert rows[0]["current_engine_epoch"] == 4
    assert classify_retained_row(rows[0]) == NOW_RESOLVED


def test_cli_emits_deterministic_report(tmp_path, capsys):
    db_path = tmp_path / "epoch.db"
    _create_database(db_path)

    assert main(["--db", str(db_path)]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["classification_mode"] == "recorded-payload-no-live-engine"
    assert report["consumer"] == "grind-campaign-controller"
    assert report["counts"][NOW_RESOLVED] == 1
    assert report["total_retained"] == 1
