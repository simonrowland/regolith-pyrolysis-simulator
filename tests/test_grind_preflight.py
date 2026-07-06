from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from simulator.backends import (
    STAGE0_SUBPROCESS_FEEDSTOCK_IDS,
    real_backend_feedstock_domain_reason,
    requires_stage0_subprocess,
)
from simulator.config import load_config_bundle
from simulator.grind_preflight import (
    GrindSourceGateError,
    STAGE0_INPROCESS_SAFE_FEEDSTOCK_IDS,
    assert_grind_feedstock_stage0_route_coverage,
    grind_feedstock_stage0_route_coverage_violations,
    assert_strict_vapor_config,
    assert_strict_vapor_result_payload,
    assert_strict_vapor_result_store,
    assert_strict_vapor_source_report,
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


def test_stage0_route_coverage_accepts_known_safe_inprocess_feedstocks() -> None:
    feedstocks = load_config_bundle().feedstocks

    assert_grind_feedstock_stage0_route_coverage(
        sorted(STAGE0_INPROCESS_SAFE_FEEDSTOCK_IDS),
        feedstocks,
        backend_name="alphamelts",
        context="test-grind",
    )


def test_stage0_inprocess_safe_feedstock_ids_are_grounded() -> None:
    feedstocks = load_config_bundle().feedstocks
    safe_digest = hashlib.md5(
        json.dumps(
            sorted(STAGE0_INPROCESS_SAFE_FEEDSTOCK_IDS),
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()

    assert safe_digest == "36551a152768632fa5639b5a9d53d04f"
    assert "lunar_highlands_nuw_lht_5m" in STAGE0_INPROCESS_SAFE_FEEDSTOCK_IDS
    assert {
        feedstock_id
        for feedstock_id in STAGE0_INPROCESS_SAFE_FEEDSTOCK_IDS
        if feedstock_id not in feedstocks
    } == set()
    assert {
        feedstock_id
        for feedstock_id in STAGE0_INPROCESS_SAFE_FEEDSTOCK_IDS
        if requires_stage0_subprocess(feedstock_id, feedstocks)
    } == set()
    assert {
        feedstock_id
        for feedstock_id in STAGE0_INPROCESS_SAFE_FEEDSTOCK_IDS
        if real_backend_feedstock_domain_reason("alphamelts", feedstock_id, feedstocks)
        is not None
    } == set()


def test_full_catalog_feedstocks_have_stage0_route_coverage() -> None:
    feedstocks = load_config_bundle().feedstocks

    assert grind_feedstock_stage0_route_coverage_violations(
        sorted(feedstocks),
        feedstocks,
        backend_name="alphamelts",
    ) == []


def test_stage0_route_coverage_accepts_super_kreep_as_explicit_ood() -> None:
    feedstocks = load_config_bundle().feedstocks

    assert (
        real_backend_feedstock_domain_reason(
            "alphamelts",
            "targeted_super_kreep_ore",
            feedstocks,
        )
        == "unsupported_melts_species"
    )
    assert_grind_feedstock_stage0_route_coverage(
        ["targeted_super_kreep_ore"],
        feedstocks,
        backend_name="alphamelts",
        context="test-grind",
    )


def test_stage0_route_required_feedstocks_are_not_preflight_ood() -> None:
    feedstocks = load_config_bundle().feedstocks

    assert requires_stage0_subprocess("mars_perchlorate_rich", feedstocks)
    assert (
        real_backend_feedstock_domain_reason(
            "alphamelts",
            "mars_perchlorate_rich",
            feedstocks,
        )
        is None
    )
    assert {
        feedstock_id
        for feedstock_id in STAGE0_SUBPROCESS_FEEDSTOCK_IDS
        if real_backend_feedstock_domain_reason("alphamelts", feedstock_id, feedstocks)
        is not None
    } == set()


def test_stage0_preflight_ood_preserves_non_routed_rejections() -> None:
    feedstocks = load_config_bundle().feedstocks

    assert not requires_stage0_subprocess("m_type_metallic_phase", feedstocks)
    assert (
        real_backend_feedstock_domain_reason(
            "alphamelts",
            "m_type_metallic_phase",
            feedstocks,
        )
        == "non_silicate_feedstock"
    )
    assert not requires_stage0_subprocess("targeted_super_kreep_ore", feedstocks)
    assert (
        real_backend_feedstock_domain_reason(
            "alphamelts",
            "targeted_super_kreep_ore",
            feedstocks,
        )
        == "unsupported_melts_species"
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


def test_stage0_route_coverage_rejects_unlisted_normal_silicate() -> None:
    feedstocks = {
        "new_normal_silicate": {
            "composition_wt_pct": {
                "SiO2": 50.0,
                "Al2O3": 14.0,
                "FeO": 7.0,
                "MgO": 5.0,
                "TiO2": 0.2,
                "CaO": 10.0,
            }
        }
    }

    with pytest.raises(GrindSourceGateError, match="new_normal_silicate"):
        assert_grind_feedstock_stage0_route_coverage(
            ["new_normal_silicate"],
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


def _write_pt1_store(
    db_path: Path,
    *,
    source: str = "vaporock",
) -> None:
    key = {
        "schema_version": "pt1-test-key",
        "corpus_version": "test-corpus",
        "data_digests": {},
        "vapor_pressure_provider": {
            "resolved_provider_id": source,
            "authoritative_provider_id": source,
        },
    }
    payload = {
        "equilibrium_result": {},
        "last_vapor_pressures_source": {"Na": source, "SiO": source},
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE reduced_real_equilibrium_payloads (
                key_hash TEXT PRIMARY KEY,
                artifact TEXT NOT NULL,
                key_bytes BLOB NOT NULL,
                payload_bytes BLOB NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO reduced_real_equilibrium_payloads
                (key_hash, artifact, key_bytes, payload_bytes)
            VALUES (?, ?, ?, ?)
            """,
            (
                "pt1-row-1",
                "equilibrium_post_record",
                json.dumps(key, sort_keys=True).encode("utf-8"),
                json.dumps(payload, sort_keys=True).encode("utf-8"),
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


def test_strict_vapor_source_report_rejects_backsolved_vaporock_colon_label() -> None:
    source = "builtin_authoritative:backsolved_vaporock_curve_fit"
    report = {
        "species": {"K": source},
        "summary": {source: {"count": 1, "percentage": 100.0}},
        "total_species": 1,
    }

    with pytest.raises(GrindSourceGateError, match="backsolved_vaporock_curve_fit"):
        assert_strict_vapor_source_report(report, context="stored-result")


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


def test_strict_vapor_result_store_pt1_strict_off_is_explicit_noop(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cache.sqlite"
    _write_pt1_store(db_path, source="vaporock")

    summary = assert_strict_vapor_result_store(db_path, strict_vapor_gate=False)

    assert summary == {
        "rows": 1,
        "vapor_active_rows": 0,
        "source_reports": 0,
        "pt1_noop_rows": 1,
    }


def test_strict_vapor_result_store_rejects_unrecognized_schema(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cache.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE unrelated (id TEXT PRIMARY KEY)")

    with pytest.raises(GrindSourceGateError, match="unrecognized cache schema"):
        assert_strict_vapor_result_store(db_path)


def test_strict_vapor_result_store_pt1_strict_on_rejects_vaporock(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cache.sqlite"
    _write_pt1_store(db_path, source="vaporock")

    with pytest.raises(GrindSourceGateError, match="vaporock"):
        assert_strict_vapor_result_store(db_path, strict_vapor_gate=True)


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
