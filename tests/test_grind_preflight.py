from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from simulator.grind_preflight import (
    GrindSourceGateError,
    assert_grind_feedstock_stage0_route_coverage,
    assert_strict_vapor_config,
    assert_strict_vapor_result_payload,
    assert_strict_vapor_result_store,
)


def _eval_spec(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "campaign": "C2A_continuous",
        "vapor_pressure_provider_id": "builtin-vapor-pressure",
        "allow_fallback_vapor": False,
        "force_builtin_vapor_pressure": False,
    }
    payload.update(overrides)
    return payload


def _source_report(source: str = "builtin_authoritative") -> dict[str, object]:
    return {
        "species": {"Na": source, "SiO": source},
        "summary": {source: {"count": 2, "percentage": 100.0}},
        "total_species": 2,
    }


def test_stage0_route_coverage_accepts_subprocess_or_out_of_domain() -> None:
    feedstocks = {
        "covered": {"stage0_verdict_b_subprocess_required": True},
        "metallic_ood": {"composition_wt_pct": {"Fe": 100.0}},
    }

    assert_grind_feedstock_stage0_route_coverage(
        ["covered", "metallic_ood"],
        feedstocks,
        backend_name="alphamelts",
        context="test-grind",
    )


def test_stage0_route_coverage_rejects_uncovered_grind_feedstock() -> None:
    feedstocks = {
        "interwindow": {
            "composition_wt_pct": {
                "SiO2": 42.0,
                "Al2O3": 12.0,
                "FeO": 12.0,
                "MgO": 18.0,
                "TiO2": 0.3,
                "CaO": 10.0,
            }
        }
    }

    with pytest.raises(GrindSourceGateError, match="interwindow"):
        assert_grind_feedstock_stage0_route_coverage(
            ["interwindow"],
            feedstocks,
            backend_name="alphamelts",
            context="test-grind",
        )


def _write_result_store(
    db_path: Path,
    *,
    eval_spec: dict[str, object],
    result_blob: dict[str, object],
    notes: list[str] | None = None,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE results (
                cache_key TEXT PRIMARY KEY,
                eval_spec TEXT NOT NULL,
                result_blob TEXT NOT NULL,
                notes TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO results (cache_key, eval_spec, result_blob, notes)
            VALUES (?, ?, ?, ?)
            """,
            (
                "row-1",
                json.dumps(eval_spec, sort_keys=True),
                json.dumps(result_blob, sort_keys=True),
                json.dumps(notes or []),
            ),
        )


def test_strict_vapor_config_rejects_fallback_enabled() -> None:
    with pytest.raises(GrindSourceGateError, match="allow_fallback_vapor"):
        assert_strict_vapor_config(
            {"allow_fallback_vapor": True},
            context="profile.run",
        )

    with pytest.raises(GrindSourceGateError, match="force_builtin_vapor_pressure"):
        assert_strict_vapor_config(
            {"force_builtin_vapor_pressure": True},
            context="profile.run",
        )


def test_strict_vapor_result_store_accepts_all_builtin_authoritative_with_sio_warning(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cache.sqlite"
    _write_result_store(
        db_path,
        eval_spec=_eval_spec(),
        result_blob={
            "vapor_pressure_source_report": _source_report("builtin_authoritative"),
            "warnings": [
                "WARNING: SiO vapor pressure uses a backsolved VapoRock "
                "fallback (curve-fit), NOT first-principles"
            ],
        },
    )

    summary = assert_strict_vapor_result_store(db_path)

    assert summary == {"rows": 1, "vapor_active_rows": 1, "source_reports": 1}


def test_strict_vapor_result_store_rejects_builtin_fallback_report(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cache.sqlite"
    _write_result_store(
        db_path,
        eval_spec=_eval_spec(),
        result_blob={
            "vapor_pressure_source_report": _source_report("builtin_fallback")
        },
    )

    with pytest.raises(GrindSourceGateError, match="builtin_fallback"):
        assert_strict_vapor_result_store(db_path)


def test_strict_vapor_result_store_rejects_kernel_fallback_key(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cache.sqlite"
    _write_result_store(
        db_path,
        eval_spec=_eval_spec(),
        result_blob={
            "vapor_pressure_source_report": _source_report("builtin_authoritative"),
            "backend_diagnostics": {"kernel_fallback_used": "builtin-vapor-pressure"},
        },
    )

    with pytest.raises(GrindSourceGateError, match="kernel_fallback_used"):
        assert_strict_vapor_result_store(db_path)


def test_strict_vapor_result_payload_rejects_nested_vaporock_provider_id() -> None:
    payload = {
        "result_blob": {
            "vapor_pressure_source_report": _source_report("builtin_authoritative"),
            "backend_diagnostics": {
                "vapor_pressure_provider_id": "vaporock"
            },
        }
    }

    with pytest.raises(GrindSourceGateError, match="vaporock"):
        assert_strict_vapor_result_payload(
            payload,
            context="stored-result",
            require_source_report=True,
        )


def test_strict_vapor_result_store_requires_report_for_vapor_active_result(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cache.sqlite"
    _write_result_store(
        db_path,
        eval_spec=_eval_spec(campaign="C4"),
        result_blob={"warnings": []},
    )

    with pytest.raises(GrindSourceGateError, match="missing vapor_pressure_source_report"):
        assert_strict_vapor_result_store(db_path)
