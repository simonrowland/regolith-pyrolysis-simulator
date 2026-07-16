from __future__ import annotations

import json
import sqlite3

from scripts.grid_pregrind_triage import (
    CALCULATION_BUG,
    FAITHFUL_RUMP,
    MELTS_VS_FREEZE,
    UNCLASSIFIED,
    build_triage_report,
    classify_non_eval,
    main,
)


def test_classify_non_eval_covers_closed_taxonomy_and_preserves_unknown():
    faithful = {
        "feedstock_id": "lunar_mare",
        "status": "out_of_domain",
        "status_kind": "refusal",
        "generic_requested_temperature_C": 1100.0,
        "generic_liquidus_T_C": 1250.0,
        "generic_liquid_fraction": 0.0,
        "raw_payload": {},
    }
    calculation_bug = {
        "feedstock_id": "lunar_mare",
        "status": "error",
        "status_kind": "failure",
        "failure_reason_code": "exception_runtimeerror",
        "raw_payload": {"exception": {"message": "adapter exploded"}},
    }
    disagreement = {
        "feedstock_id": "lunar_highland",
        "status": "out_of_domain",
        "status_kind": "refusal",
        "refusal_reason": "no_convergence",
        "raw_payload": {},
    }
    unknown = {
        "feedstock_id": "lunar_highland",
        "status": "out_of_domain",
        "status_kind": "refusal",
        "refusal_reason": "stale_explicit_fo2_key",
        "raw_payload": {"opaque": ["payload", 7]},
    }

    assert classify_non_eval(faithful) == FAITHFUL_RUMP
    assert classify_non_eval(calculation_bug) == CALCULATION_BUG
    assert classify_non_eval(disagreement) == MELTS_VS_FREEZE
    assert classify_non_eval(unknown) == UNCLASSIFIED

    report = build_triage_report([unknown, disagreement, calculation_bug, faithful])
    assert report == {
        "counts": {
            FAITHFUL_RUMP: 1,
            CALCULATION_BUG: 1,
            MELTS_VS_FREEZE: 1,
            UNCLASSIFIED: 1,
        },
        "per_feedstock": {
            "lunar_highland": {
                FAITHFUL_RUMP: 0,
                CALCULATION_BUG: 0,
                MELTS_VS_FREEZE: 1,
                UNCLASSIFIED: 1,
            },
            "lunar_mare": {
                FAITHFUL_RUMP: 1,
                CALCULATION_BUG: 1,
                MELTS_VS_FREEZE: 0,
                UNCLASSIFIED: 0,
            },
        },
        "total_non_eval": 4,
        "unclassified": [{"feedstock": "lunar_highland", "raw": unknown}],
    }


def test_report_ignores_success_and_uses_unassigned_feedstock_bucket():
    report = build_triage_report(
        [
            {"status": "ok", "status_kind": "success", "raw_payload": {}},
            {
                "status": "out_of_domain",
                "status_kind": "refusal",
                "refusal_reason": "fully_solid",
                "raw_payload": {},
            },
        ]
    )

    assert report["total_non_eval"] == 1
    assert report["per_feedstock"]["<unassigned>"][FAITHFUL_RUMP] == 1


def test_cli_emits_deterministic_database_summary(tmp_path, capsys):
    db_path = tmp_path / "grid.db"
    feedstocks_path = tmp_path / "feedstocks.yaml"
    feedstocks_path.write_text(
        "lunar_mare_low_ti:\n"
        "  composition_wt_pct:\n"
        "    SiO2: 50.0\n"
        "    FeO: 50.0\n"
        "non_anchor_decoy:\n"
        "  composition_wt_pct:\n"
        "    SiO2: 50.0\n"
        "    FeO: 50.0\n",
        encoding="utf-8",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE grid_keys (id INTEGER, temperature_C REAL, "
            "composition_kg_json TEXT)"
        )
        connection.execute(
            "CREATE TABLE alphamelts_outputs ("
            "id INTEGER, grid_key_id INTEGER, expedited_key TEXT, "
            "engine_epoch INTEGER, status TEXT, status_kind TEXT, "
            "refusal_reason TEXT, failure_reason_code TEXT, failure_message TEXT, "
            "raw_payload TEXT, raw_payload_format TEXT, engine_mode TEXT, "
            "engine_model TEXT, generic_requested_temperature_C REAL, "
            "generic_liquidus_T_C REAL, generic_liquid_fraction REAL, "
            "finder_liquidus_T_C REAL, finder_liquid_fraction REAL)"
        )
        connection.execute(
            "INSERT INTO grid_keys VALUES (3, 1400.0, '{\"FeO\":1.0,\"SiO2\":1.0}')"
        )
        connection.execute(
            "INSERT INTO alphamelts_outputs VALUES "
            "(1, 3, 'key-3', 2, 'error', 'failure', NULL, 'timeout', "
            "'timed out', '{}', 'test-v1', 'subprocess', 'MELTSv1.0.2', "
            "NULL, NULL, NULL, NULL, NULL)"
        )

    args = [
        "--db",
        str(db_path),
        "--engine-epoch",
        "2",
        "--feedstocks",
        str(feedstocks_path),
    ]
    assert main(args) == 0
    first = capsys.readouterr().out
    assert main(args) == 0
    second = capsys.readouterr().out

    assert first == second
    payload = json.loads(first)
    assert payload["counts"][CALCULATION_BUG] == 1
    assert payload["per_feedstock"]["lunar_mare_low_ti"][CALCULATION_BUG] == 1
    assert "non_anchor_decoy" not in payload["per_feedstock"]
    assert payload["total_non_eval"] == 1
    assert payload["unclassified"] == []
