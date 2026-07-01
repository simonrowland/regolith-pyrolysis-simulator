#!/usr/bin/env python3
"""Populate the PT-1 reduced-real cache from real simulator trajectories.

Opt-in batch driver. It attaches ``PT0DeterminismStore(db_path=...)`` to
normal ``PyrolysisSimulator.step()`` runs and records only compact metrics.
Correct reduced-real population uses ``--backend alphamelts --require-magemin``;
stub-backed equilibrium rows are rejected before persistent merge.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import math
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.backend_names import canonical_backend_name
from simulator.backends import BackendSelectionPolicy
from simulator.chemistry.kernel import ChemistryIntent
from simulator.config import load_config_bundle
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.reduced_real_determinism import (
    ControlQuantization,
    PT0DeterminismStore,
    PT0NonFinitePayload,
    PT1_EQUILIBRIUM_TABLE,
    PT1PersistentEquilibriumStore,
    validate_reduced_real_equilibrium_record_key,
)
from simulator.grind_preflight import (
    GrindSourceGateError,
    assert_grind_feedstock_stage0_route_coverage,
    assert_strict_vapor_config,
    assert_strict_vapor_pt1_row,
    assert_strict_vapor_source_report,
    vapor_pressure_source_report_from_sim,
)
from simulator.session import SimSession, SimSessionConfig


DEFAULT_PROFILE = REPO_ROOT / "data" / "optimize_profiles" / "lunar_mare_low_ti.yaml"
DEFAULT_DB = REPO_ROOT / "docs-private" / "reviews" / "2026-06-04-tier-pt3" / "pt3-capped.db"
OPTIMIZE_PROFILE_DIR = REPO_ROOT / "data" / "optimize_profiles"
MASS_BALANCE_GATE_PCT = 5e-12
FIRST_FLIP_CAMPAIGNS = ("C2A_continuous", "C2B", "C4")
CAL_FEEDSTOCKS = ("lunar_mare_low_ti", "mars_perchlorate_rich", "ci_carbonaceous_chondrite")
KREEP_FEEDSTOCK = "lunar_pkt_kreep_average"
MAGEMIN_PROVIDER_ID = "magemin-shadow"
# TODO: replace message-prefix match with a typed exception once the in-flight
# evaporation.py work lands. Pinned by
# tests/test_populate_reduced_real_cache_driver.py against the upstream literal
# in simulator/evaporation.py.
GATE_LIQUIDUS_UNAVAILABLE_PREFIX = (
    "freeze_gate.enabled requires a liquid_fraction(T) source"
)


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text()) or {}


def _resolve_profile(path: Path) -> Path:
    if path.exists():
        return path.resolve()
    candidate = OPTIMIZE_PROFILE_DIR / path
    if candidate.exists():
        return candidate.resolve()
    if candidate.suffix != ".yaml":
        candidate = candidate.with_suffix(".yaml")
    if candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(f"profile not found: {path}")


def _profile_run(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    run = profile.get("run") or {}
    if not isinstance(run, Mapping):
        raise ValueError("profile.run must be a mapping when provided")
    return run


def _coerce_campaigns(value: Any, *, source: str) -> tuple[str, ...]:
    if isinstance(value, str):
        campaigns = (value,)
    elif isinstance(value, (list, tuple)):
        campaigns = tuple(str(item) for item in value)
    else:
        raise ValueError(f"{source} must be a campaign string or list")
    if not campaigns or any(not campaign for campaign in campaigns):
        raise ValueError(f"{source} must contain at least one non-empty campaign")
    return campaigns


def _profile_campaigns(profile: Mapping[str, Any]) -> tuple[str, ...]:
    seed_recipes = profile.get("seed_recipes") or ()
    if seed_recipes:
        if not isinstance(seed_recipes, list):
            raise ValueError("profile.seed_recipes must be a list when provided")
        first_seed = seed_recipes[0]
        if not isinstance(first_seed, Mapping):
            raise ValueError("profile.seed_recipes entries must be mappings")
        if "source_campaigns" in first_seed:
            return _coerce_campaigns(
                first_seed["source_campaigns"],
                source="profile.seed_recipes[0].source_campaigns",
            )
        if "source_campaign" in first_seed:
            return _coerce_campaigns(
                first_seed["source_campaign"],
                source="profile.seed_recipes[0].source_campaign",
            )

    run = _profile_run(profile)
    if "campaigns" in run:
        return _coerce_campaigns(run["campaigns"], source="profile.run.campaigns")
    if "campaign" in run:
        return _coerce_campaigns(run["campaign"], source="profile.run.campaign")
    return ("C2A_continuous",)


def _coerce_additives(value: Any, *, source: str) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{source} must be a mapping of species to kg")
    additives: dict[str, float] = {}
    for species, kg in value.items():
        if not isinstance(species, str) or not species:
            raise ValueError(f"{source} keys must be non-empty strings")
        amount = float(kg)
        if not math.isfinite(amount) or amount < 0.0:
            raise ValueError(f"{source}.{species} must be a finite non-negative kg value")
        additives[species] = amount
    return additives


def _parse_additive_arg(raw: str) -> tuple[str, float]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("--additive must use SPECIES=KG")
    species, kg_text = raw.split("=", 1)
    if not species:
        raise argparse.ArgumentTypeError("--additive species must be non-empty")
    try:
        kg = float(kg_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--additive {raw!r} has non-numeric kg value"
        ) from exc
    if not math.isfinite(kg) or kg < 0.0:
        raise argparse.ArgumentTypeError(
            f"--additive {raw!r} must be finite and non-negative"
        )
    return species, kg


def _parse_control_quantization_arg(raw: str) -> ControlQuantization:
    text = str(raw).strip()
    if not text:
        raise argparse.ArgumentTypeError("control quantization must be non-empty")
    if text.startswith("{"):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise argparse.ArgumentTypeError(
                "--control-quantization JSON dict is invalid"
            ) from exc
        if not isinstance(value, Mapping):
            raise argparse.ArgumentTypeError(
                "--control-quantization JSON must be an object"
            )
        expected = {
            "t_k_quantum",
            "pressure_bar_quantum",
            "log_fo2_quantum",
            "composition_sig_figs",
        }
        keys = set(value)
        if keys != expected:
            missing = ", ".join(sorted(expected - keys)) or "none"
            extra = ", ".join(sorted(str(key) for key in keys - expected)) or "none"
            raise argparse.ArgumentTypeError(
                "--control-quantization JSON must contain exactly "
                f"{', '.join(sorted(expected))}; missing={missing}; extra={extra}"
            )
        try:
            return ControlQuantization(
                t_k_quantum=float(value["t_k_quantum"]),
                pressure_bar_quantum=float(value["pressure_bar_quantum"]),
                log_fo2_quantum=float(value["log_fo2_quantum"]),
                composition_sig_figs=int(value["composition_sig_figs"]),
            )
        except (TypeError, ValueError) as exc:
            raise argparse.ArgumentTypeError(
                f"--control-quantization JSON values are invalid: {exc}"
            ) from exc
    try:
        return ControlQuantization.from_name(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _cli_additives(entries: list[tuple[str, float]] | None) -> dict[str, float]:
    additives: dict[str, float] = {}
    for species, kg in entries or ():
        additives[species] = kg
    return additives


def _profile_additives(profile: Mapping[str, Any]) -> dict[str, float]:
    return _coerce_additives(
        _profile_run(profile).get("additives_kg"),
        source="profile.run.additives_kg",
    )


def _feedstock_additives(
    feedstock: str,
    *,
    loaded_profile: Mapping[str, Any],
    cli_additives: Mapping[str, float],
) -> dict[str, float]:
    profile_feedstock = loaded_profile.get("feedstock")
    if profile_feedstock == feedstock:
        additives = _profile_additives(loaded_profile)
    else:
        feedstock_profile_path = OPTIMIZE_PROFILE_DIR / f"{feedstock}.yaml"
        if feedstock_profile_path.exists():
            feedstock_profile = _load_yaml(feedstock_profile_path)
            declared_feedstock = feedstock_profile.get("feedstock")
            if declared_feedstock not in (None, feedstock):
                raise ValueError(
                    f"{feedstock_profile_path.relative_to(REPO_ROOT)} declares "
                    f"feedstock={declared_feedstock!r}, expected {feedstock!r}"
                )
            additives = _profile_additives(feedstock_profile)
        else:
            additives = {}
    additives.update(cli_additives)
    return additives


def _additives_result(
    feedstocks: tuple[Any, ...],
    additives_by_feedstock: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    if len(feedstocks) == 1:
        feedstock = str(feedstocks[0])
        return dict(sorted(additives_by_feedstock.get(feedstock, {}).items()))
    return {
        str(feedstock): dict(
            sorted(additives_by_feedstock.get(str(feedstock), {}).items())
        )
        for feedstock in feedstocks
    }


def _magemin_status() -> dict[str, Any]:
    backend = MAGEMinBackend()
    initialized = backend.initialize({"python_bridge": "subprocess"})
    return {
        "initialized": initialized,
        "available": backend.is_available(),
        "binary_path": str(getattr(backend, "_binary_path", "") or ""),
        "last_error": str(getattr(backend, "_last_error", "") or ""),
    }


def _setpoints_for_population(setpoints: Mapping[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(dict(setpoints))
    gate = dict(copied.get("freeze_gate") or {})
    gate["enabled"] = True
    copied["freeze_gate"] = gate
    return copied


def _start_session(
    *,
    feedstock: str,
    campaign: str,
    backend_name: str,
    mass_kg: float,
    additives_kg: Mapping[str, float],
    store: PT0DeterminismStore,
    allow_stub_equilibrium: bool,
) -> SimSession:
    cfg = load_config_bundle()
    session = SimSession().start(
        SimSessionConfig(
            feedstock_id=feedstock,
            feedstocks=cfg.feedstocks,
            setpoints=_setpoints_for_population(cfg.setpoints),
            vapor_pressures=cfg.vapor_pressures,
            campaign=campaign,
            backend_name=backend_name,
            backend_policy=BackendSelectionPolicy.RUNNER_STRICT,
            mass_kg=mass_kg,
            additives_kg=additives_kg,
        )
    )
    session.simulator.configure_pt0_determinism_store(store)
    if backend_name == "stub":
        if not allow_stub_equilibrium:
            raise RuntimeError(
                "stub backend selected for gate population; pass "
                "--allow-stub-equilibrium to use stub equilibrium only as the "
                "non-gate step driver while MAGEMin populates GATE_LIQUID_FRACTION"
            )
        session.simulator.backend.is_available = lambda: True
    return session


def _disable_live_providers(session: SimSession) -> None:
    def disabled(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("PT-3 replay attempted a live provider call")

    sim = session.simulator
    sim.backend.equilibrate = disabled
    sim.backend.find_liquidus_solidus = disabled
    sim._register_freeze_gate_liquid_fraction_providers()
    for resolver in (
        sim._chem_registry.authoritative_for,
        sim._chem_registry.fallback_for,
    ):
        provider = resolver(ChemistryIntent.GATE_LIQUID_FRACTION)
        if provider is not None:
            provider.dispatch = disabled


@contextmanager
def _timed_magemin_dispatch(events: list[dict[str, Any]]) -> Iterator[None]:
    from engines.magemin.provider import MAGEMinShadowProvider

    original = MAGEMinShadowProvider.dispatch

    def timed(self: Any, request: Any) -> Any:
        started = time.perf_counter()
        status = "raised"
        try:
            result = original(self, request)
            status = str(getattr(result, "status", "unknown"))
            return result
        finally:
            events.append(
                {
                    "elapsed_s": time.perf_counter() - started,
                    "status": status,
                }
            )

    MAGEMinShadowProvider.dispatch = timed
    try:
        yield
    finally:
        MAGEMinShadowProvider.dispatch = original


def _diagnostic_keys_from_vapor_pressure_refusal(
    detail: str,
) -> tuple[str, ...] | None:
    marker = "Diagnostic keys: "
    if marker not in detail:
        return None
    raw_keys = detail.rsplit(marker, 1)[1].strip()
    try:
        keys = ast.literal_eval(raw_keys)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
        return None
    return tuple(keys)


def _known_chemistry_case_gap(exc: RuntimeError | ValueError) -> dict[str, Any] | None:
    detail = str(exc)
    if isinstance(exc, RuntimeError):
        if detail.startswith(GATE_LIQUIDUS_UNAVAILABLE_PREFIX):
            return {
                "reason": "gate_liquidus_unavailable",
                "detail": detail,
            }
        if detail.startswith("real_backend_out_of_domain:"):
            reason = detail.split(":", 2)[1].strip()
            return {
                "reason": reason or "real_backend_out_of_domain",
                "detail": detail,
            }
        diagnostic_keys = _diagnostic_keys_from_vapor_pressure_refusal(detail)
        if (
            "Authoritative VAPOR_PRESSURE dispatch returned" in detail
            and "status='out_of_domain'" in detail
            and "allow_fallback_vapor=False" in detail
            and diagnostic_keys is not None
        ):
            return {
                "reason": "vapor_pressure_out_of_domain",
                "detail": detail,
                "diagnostic_keys": list(diagnostic_keys),
            }
        return None
    if isinstance(exc, PT0NonFinitePayload) or detail.startswith(
        "non-finite value in PT-0 payload: "
    ):
        return {
            "reason": "non_finite_payload",
            "detail": detail,
        }
    return None


def _apply_pending_decision(session: SimSession) -> bool:
    decision = session.pending_decision()
    if decision is None:
        return False
    choice = decision.recommendation or (decision.options[0] if decision.options else "")
    if not choice:
        raise RuntimeError("pending decision has no auto-applicable choice")
    session.decide(choice)
    return True


def _run_case(
    *,
    feedstock: str,
    campaign: str,
    backend_name: str,
    mass_kg: float,
    additives_kg: Mapping[str, float],
    hours: int,
    wall_cap_s: float,
    db_path: Path,
    mode: str,
    disable_live: bool,
    allow_stub_equilibrium: bool,
    control_quantization: ControlQuantization | None = None,
) -> dict[str, Any]:
    store = PT0DeterminismStore(
        mode,
        db_path=db_path,
        strict_vapor_gate=True,
        control_quantization=control_quantization,
    )
    timings: list[dict[str, Any]] = []
    session = _start_session(
        feedstock=feedstock,
        campaign=campaign,
        backend_name=backend_name,
        mass_kg=mass_kg,
        additives_kg=additives_kg,
        store=store,
        allow_stub_equilibrium=allow_stub_equilibrium,
    )
    if disable_live:
        _disable_live_providers(session)

    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    stop_reason = "max_hours"
    mass_balance_failed = False
    max_abs_mass_balance_error_pct = 0.0
    failed_mass_balance_row: dict[str, Any] | None = None
    with _timed_magemin_dispatch(timings):
        for hour_index in range(1, hours + 1):
            if time.perf_counter() - started >= wall_cap_s:
                stop_reason = "wall_cap"
                break
            if session.is_complete():
                stop_reason = "session_complete"
                break
            decisions_applied = 0
            while _apply_pending_decision(session):
                decisions_applied += 1
                if session.is_complete():
                    stop_reason = "session_complete_after_decision"
                    break
            if stop_reason.startswith("session_complete"):
                break
            event_start = len(store.cache_events)
            timing_start = len(timings)
            step_started = time.perf_counter()
            step = session.advance()
            sim = session.simulator
            vapor_pressure_source_report = vapor_pressure_source_report_from_sim(sim)
            if int(vapor_pressure_source_report["total_species"]):
                assert_strict_vapor_source_report(
                    vapor_pressure_source_report,
                    context=(
                        f"{feedstock}/{campaign} hour {hour_index} "
                        "strict vapor source gate"
                    ),
                )
            mass_balance_error_pct = float(step.snapshot.mass_balance_error_pct)
            if math.isfinite(mass_balance_error_pct):
                max_abs_mass_balance_error_pct = max(
                    max_abs_mass_balance_error_pct,
                    abs(mass_balance_error_pct),
                )
            else:
                max_abs_mass_balance_error_pct = math.inf
            rows.append(
                {
                    "hour_index": hour_index,
                    "campaign": sim.melt.campaign.name,
                    "campaign_hour": float(sim.melt.campaign_hour),
                    "temperature_C": float(step.snapshot.temperature_C),
                    "mass_balance_error_pct": mass_balance_error_pct,
                    "step_elapsed_s": time.perf_counter() - step_started,
                    "decisions_applied": decisions_applied,
                    "cache_events": store.cache_events[event_start:],
                    "magemin_calls": timings[timing_start:],
                    "backend_error": step.backend_error,
                    "vapor_pressure_source_report": vapor_pressure_source_report,
                }
            )
            if not _mass_balance_closed(mass_balance_error_pct):
                mass_balance_failed = True
                stop_reason = "mass_balance_failed"
                failed_mass_balance_row = rows[-1]
                break
    return {
        "status": "failed" if mass_balance_failed else "complete",
        "case": {
            "feedstock": feedstock,
            "campaign": campaign,
            "backend_name": backend_name,
            "additives_kg": dict(sorted(additives_kg.items())),
            "mode": mode,
        },
        "stop_reason": stop_reason,
        "elapsed_s": time.perf_counter() - started,
        "hours_completed": len(rows),
        "rows": rows,
        "mass_balance_gate": {
            "threshold_pct": MASS_BALANCE_GATE_PCT,
            "passed": not mass_balance_failed,
            "max_abs_error_pct": max_abs_mass_balance_error_pct,
            "failed_row": failed_mass_balance_row,
        },
        "store_summary": store.summary(),
        "magemin_timings": timings,
        "trace_view": _trace_view(session, rows),
    }


def _mass_balance_closed(error_pct: float) -> bool:
    return math.isfinite(error_pct) and abs(error_pct) <= MASS_BALANCE_GATE_PCT


def _trace_view(session: SimSession, rows: list[dict[str, Any]]) -> dict[str, Any]:
    sim = session.simulator
    if rows:
        last = rows[-1]
        temperature_C = last["temperature_C"]
        mass_balance_error_pct = last["mass_balance_error_pct"]
    else:
        temperature_C = float(sim.melt.temperature_C)
        mass_balance_error_pct = math.nan
    return {
        "campaign": sim.melt.campaign.name,
        "campaign_hour": float(sim.melt.campaign_hour),
        "temperature_C": temperature_C,
        "mass_balance_error_pct": mass_balance_error_pct,
        "products": sim.product_ledger(),
        "rows": len(rows),
    }


def _cache_payload_rows(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        table = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (PT1_EQUILIBRIUM_TABLE,),
        ).fetchone()
        if table is None:
            return []
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})")
        }
        corpus_column = (
            "corpus_version"
            if "corpus_version" in columns
            else "NULL AS corpus_version"
        )
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT
                    key_hash,
                    artifact,
                    store_schema_version,
                    request_schema_version,
                    key_sha256,
                    payload_sha256,
                    key_bytes,
                    payload_bytes,
                    code_version,
                    {corpus_column},
                    engine_version,
                    data_digests_json,
                    created_at,
                    git_dirty
                FROM {PT1_EQUILIBRIUM_TABLE}
                """
            )
        ]


def _cache_row_summary(db_path: Path) -> dict[str, Any]:
    payload_rows = _cache_payload_rows(db_path)
    rows: list[dict[str, Any]] = []
    for row in payload_rows:
        key = json.loads(bytes(row["key_bytes"]).decode("utf-8"))
        provider = key.get("provider") or {}
        rows.append(
            {
                "key_hash": row["key_hash"],
                "artifact": row["artifact"],
                "provider_id": provider.get("resolved_provider_id", ""),
                "provider_role": provider.get("resolved_role", ""),
            }
        )
    by_artifact = Counter(row["artifact"] for row in rows)
    by_provider = Counter(
        f"{row['artifact']}|{row['provider_id']}|{row['provider_role']}"
        for row in rows
    )
    magemin_keys = {
        row["key_hash"]
        for row in rows
        if row["provider_id"] == MAGEMIN_PROVIDER_ID
    }
    return {
        "rows": len(rows),
        "by_artifact": dict(sorted(by_artifact.items())),
        "by_provider": dict(sorted(by_provider.items())),
        "unique_keys": len({row["key_hash"] for row in rows}),
        "magemin_unique_keys": len(magemin_keys),
    }


def _magemin_key_hashes(db_path: Path) -> set[str]:
    key_hashes: set[str] = set()
    for row in _cache_payload_rows(db_path):
        key = json.loads(bytes(row["key_bytes"]).decode("utf-8"))
        provider = key.get("provider") or {}
        if provider.get("resolved_provider_id", "") == MAGEMIN_PROVIDER_ID:
            key_hashes.add(str(row["key_hash"]))
    return key_hashes


def _merge_cache_shard(shard_path: Path, target_path: Path) -> dict[str, Any]:
    rows = _cache_payload_rows(shard_path)
    target_store = PT1PersistentEquilibriumStore(target_path)
    inserted_rows = 0
    with target_store._connect() as conn:
        target_store._initialize(conn)
        for row in rows:
            artifact = str(row["artifact"])
            key_hash = str(row["key_hash"])
            key_bytes = bytes(row["key_bytes"])
            payload_bytes = bytes(row["payload_bytes"])
            payload_hash = str(row["payload_sha256"])
            try:
                key = json.loads(key_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RuntimeError(
                    f"PT-1 cache shard row has invalid key bytes: {key_hash}"
                ) from exc
            try:
                payload = json.loads(payload_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RuntimeError(
                    f"PT-1 cache shard row has invalid payload bytes: {key_hash}"
                ) from exc
            if not isinstance(payload, Mapping):
                raise RuntimeError(
                    f"PT-1 cache shard row payload must be a mapping: {key_hash}"
                )
            validate_reduced_real_equilibrium_record_key(artifact, key)
            assert_strict_vapor_pt1_row(
                artifact=artifact,
                key=key,
                key_hash=key_hash,
                payload=payload,
                context=f"PT-1 cache shard {artifact}:{key_hash}",
            )
            existing = conn.execute(
                f"""
                SELECT artifact, key_sha256, payload_sha256, key_bytes, payload_bytes
                FROM {PT1_EQUILIBRIUM_TABLE}
                WHERE key_hash = ?
                """,
                (key_hash,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["artifact"]) != artifact
                    or str(existing["key_sha256"]) != str(row["key_sha256"])
                    or str(existing["payload_sha256"]) != payload_hash
                    or bytes(existing["key_bytes"]) != key_bytes
                    or bytes(existing["payload_bytes"]) != payload_bytes
                ):
                    raise RuntimeError(f"PT-1 cache collision while merging {key_hash}")
                continue
            conn.execute(
                f"""
                INSERT INTO {PT1_EQUILIBRIUM_TABLE} (
                    key_hash,
                    artifact,
                    store_schema_version,
                    request_schema_version,
                    key_sha256,
                    payload_sha256,
                    key_bytes,
                    payload_bytes,
                    code_version,
                    corpus_version,
                    engine_version,
                    data_digests_json,
                    created_at,
                    git_dirty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key_hash,
                    artifact,
                    str(row["store_schema_version"]),
                    str(row["request_schema_version"]),
                    str(row["key_sha256"]),
                    payload_hash,
                    sqlite3.Binary(key_bytes),
                    sqlite3.Binary(payload_bytes),
                    str(row["code_version"]),
                    row.get("corpus_version"),
                    row["engine_version"],
                    str(row["data_digests_json"]),
                    str(row["created_at"]),
                    int(row["git_dirty"]),
                ),
            )
            inserted_rows += 1
    return {
        "merged": True,
        "source": "temporary_shard",
        "rows": len(rows),
        "inserted_rows": inserted_rows,
        "magemin_unique_keys": len(_magemin_key_hashes(shard_path)),
    }


def _discarded_cache_shard_summary(shard_path: Path, reason: str) -> dict[str, Any]:
    rows = _cache_payload_rows(shard_path)
    return {
        "merged": False,
        "discarded": True,
        "reason": reason,
        "source": "temporary_shard",
        "rows": len(rows),
        "magemin_unique_keys": len(_magemin_key_hashes(shard_path)),
    }


def _prepare_replay_cache(target_path: Path, replay_path: Path) -> None:
    PT1PersistentEquilibriumStore(replay_path)
    if target_path.exists():
        _merge_cache_shard(target_path, replay_path)


def _replay_cache_merge_summary(shard_path: Path, replay_path: Path) -> dict[str, Any]:
    merge_summary = _merge_cache_shard(shard_path, replay_path)
    return {
        **merge_summary,
        "target": "temporary_replay_cache",
        "path": str(replay_path),
    }


def _timing_stats(timings: list[dict[str, Any]]) -> dict[str, Any]:
    values = sorted(float(item["elapsed_s"]) for item in timings)
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min_s": values[0],
        "p50_s": _percentile(values, 0.50),
        "mean_s": sum(values) / len(values),
        "p95_s": _percentile(values, 0.95),
        "max_s": values[-1],
        "statuses": dict(sorted(Counter(item["status"] for item in timings).items())),
    }


def _percentile(values: list[float], q: float) -> float:
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return values[low]
    return values[low] + (values[high] - values[low]) * (pos - low)


def _estimate(
    *,
    observed_magemin_keys: int,
    observed_hours: int,
    timings: dict[str, Any],
    include_kreep: bool,
) -> dict[str, Any]:
    cfg = load_config_bundle()
    campaigns = cfg.setpoints.get("campaigns", {})
    campaign_hours = {
        name: int((campaigns.get(name) or {}).get("max_hold_hr") or 0)
        for name in FIRST_FLIP_CAMPAIGNS
    }
    feedstock_count = len(CAL_FEEDSTOCKS) + (1 if include_kreep else 0)
    full_sim_hours = sum(campaign_hours.values()) * feedstock_count
    key_rate = observed_magemin_keys / observed_hours if observed_hours else 0.0
    projected_keys = math.ceil(key_rate * full_sim_hours) if key_rate else 0
    mean_s = float(timings.get("mean_s") or 0.0)
    p95_s = float(timings.get("p95_s") or mean_s or 0.0)
    return {
        "calibration_feedstocks": list(CAL_FEEDSTOCKS)
        + ([KREEP_FEEDSTOCK] if include_kreep else []),
        "campaigns": list(FIRST_FLIP_CAMPAIGNS),
        "campaign_max_hours": campaign_hours,
        "projected_sim_hours": full_sim_hours,
        "observed_magemin_keys": observed_magemin_keys,
        "key_rate_basis": "run_local_temporary_capture_shards",
        "observed_magemin_keys_per_sim_hour": key_rate,
        "projected_unique_magemin_keys": projected_keys,
        "projected_wall_time_mean_s": projected_keys * mean_s,
        "projected_wall_time_p95_s": projected_keys * p95_s,
        "key_rate_caveat": (
            "Projection excludes pre-existing target DB rows and assumes roughly "
            "one unique MAGEMin key per simulated hour for this calibration; "
            "treat as small-sample sizing, not a stable throughput guarantee."
        ),
        "cluster_note": (
            "Parallelize by feedstock x campaign; prefer per-worker SQLite shards "
            "then merge, because one shared SQLite DB serializes writers."
        ),
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--feedstock", action="append", dest="feedstocks")
    parser.add_argument("--campaign", action="append", dest="campaigns")
    parser.add_argument("--backend", default="alphamelts", type=canonical_backend_name)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--hours", type=int, default=1)
    parser.add_argument("--mass-kg", type=float, default=1000.0)
    parser.add_argument(
        "--additive",
        action="append",
        default=[],
        type=_parse_additive_arg,
        dest="additives",
        metavar="SPECIES=KG",
    )
    parser.add_argument("--wall-cap-s", type=float, default=3600.0)
    parser.add_argument("--validate-replay", action="store_true")
    parser.add_argument("--include-kreep", action="store_true")
    parser.add_argument("--require-magemin", action="store_true")
    parser.add_argument("--allow-stub-equilibrium", action="store_true")
    parser.add_argument(
        "--control-quantization",
        type=_parse_control_quantization_arg,
        default=None,
        metavar="TIER|JSON",
    )
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    profile_path = _resolve_profile(args.profile)
    profile = _load_yaml(profile_path)
    cfg = load_config_bundle()
    assert_strict_vapor_config(
        (cfg.setpoints.get("chemistry_kernel", {}) or {}),
        context="populate_reduced_real_cache:setpoints.chemistry_kernel",
    )
    run_options = dict(_profile_run(profile))
    assert_strict_vapor_config(
        run_options,
        context=f"{profile_path}:run",
    )
    feedstocks = tuple(args.feedstocks or (profile.get("feedstock"),))
    if not all(feedstocks):
        raise ValueError("feedstock required via --feedstock or profile.feedstock")
    campaigns = tuple(args.campaigns or _profile_campaigns(profile))
    cli_additives_kg = _cli_additives(args.additives)
    magemin = _magemin_status()
    if str(args.backend).strip().lower() == "stub":
        raise RuntimeError(
            "stub backend cannot populate the PT-1 reduced-real cache; "
            "use --backend alphamelts --require-magemin"
        )
    if args.require_magemin and not magemin["available"]:
        result = {
            "status": "blocked",
            "blocked_reason": "real MAGEMin subprocess backend unavailable",
            "magemin": magemin,
        }
        _emit(result, args.json_out)
        return 2
    assert_grind_feedstock_stage0_route_coverage(
        [str(feedstock) for feedstock in feedstocks],
        getattr(cfg, "feedstocks", {}) or {},
        backend_name=str(args.backend),
        context="populate_reduced_real_cache",
    )

    args.db.parent.mkdir(parents=True, exist_ok=True)
    live_results = []
    cache_merges: list[dict[str, Any]] = []
    run_magemin_key_hashes: set[str] = set()
    pending_shards: list[tuple[dict[str, Any], Path]] = []
    captured_cases: list[tuple[str, str]] = []
    case_gaps: list[dict[str, Any]] = []
    additives_by_feedstock: dict[str, dict[str, float]] = {}
    with tempfile.TemporaryDirectory(
        prefix="pt3-cache-work-",
        dir=args.db.parent,
    ) as work_dir:
        work_path = Path(work_dir)
        replay_db_path = args.db
        if args.validate_replay:
            replay_db_path = work_path / "replay-validation.db"
            _prepare_replay_cache(args.db, replay_db_path)

        case_index = 0
        for feedstock in feedstocks:
            feedstock_name = str(feedstock)
            additives_kg = _feedstock_additives(
                feedstock_name,
                loaded_profile=profile,
                cli_additives=cli_additives_kg,
            )
            additives_by_feedstock[feedstock_name] = additives_kg
            for campaign in campaigns:
                case_index += 1
                shard_db = work_path / f"cache-shard-{case_index}.db"
                print(
                    f"[case] feedstock={feedstock_name} campaign={campaign} start",
                    flush=True,
                )
                try:
                    case_result = _run_case(
                        feedstock=feedstock_name,
                        campaign=campaign,
                        backend_name=args.backend,
                        mass_kg=args.mass_kg,
                        additives_kg=additives_kg,
                        hours=args.hours,
                        wall_cap_s=args.wall_cap_s,
                        db_path=shard_db,
                        mode="capture",
                        disable_live=False,
                        allow_stub_equilibrium=args.allow_stub_equilibrium,
                        control_quantization=args.control_quantization,
                    )
                except (RuntimeError, ValueError) as exc:
                    gap = _known_chemistry_case_gap(exc)
                    if gap is None:
                        raise
                    gap = {
                        "feedstock": feedstock_name,
                        "campaign": campaign,
                        **gap,
                    }
                    case_gaps.append(gap)
                    print(
                        f"CASE-GAP: {feedstock_name}/{campaign} {gap['reason']}",
                        flush=True,
                    )
                    print(
                        f"[case] feedstock={feedstock_name} campaign={campaign} "
                        f"status={gap['reason']} hours=0",
                        flush=True,
                    )
                    continue
                case_status = (
                    "ok"
                    if case_result.get("status") == "complete"
                    else str(case_result.get("stop_reason") or "failed")
                )
                print(
                    f"[case] feedstock={feedstock_name} campaign={campaign} "
                    f"status={case_status} hours={case_result['hours_completed']}",
                    flush=True,
                )
                case_result["cache_shard"] = {
                    "temporary": True,
                    "path": str(shard_db),
                }
                if case_result.get("status") != "complete":
                    case_result["cache_merge"] = {
                        "merged": False,
                        "discarded": True,
                        "reason": "mass_balance_gate_failed",
                    }
                    live_results.append(case_result)
                    all_live_timings = [
                        item
                        for result in live_results
                        for item in result.get("magemin_timings", [])
                    ]
                    timing_summary = _timing_stats(all_live_timings)
                    observed_hours = sum(
                        int(result["hours_completed"]) for result in live_results
                    )
                    result = {
                        "status": "failed",
                        "failed_reason": "mass_balance_gate_failed",
                        "profile": str(profile_path.relative_to(REPO_ROOT)),
                        "campaigns": list(campaigns),
                        "additives_kg": _additives_result(
                            feedstocks,
                            additives_by_feedstock,
                        ),
                        "db": str(args.db),
                        "allow_stub_equilibrium": bool(args.allow_stub_equilibrium),
                        "magemin": magemin,
                        "live": live_results,
                        "replay": [],
                        "case_gaps": list(case_gaps),
                        "domain_gaps": list(case_gaps),
                        "domain_gap_count": len(case_gaps),
                        "cache": _cache_row_summary(args.db),
                        "cache_merges": cache_merges,
                        "magemin_timing": timing_summary,
                        "validation": None,
                        "estimate": _estimate(
                            observed_magemin_keys=len(run_magemin_key_hashes),
                            observed_hours=observed_hours,
                            timings=timing_summary,
                            include_kreep=args.include_kreep,
                        ),
                        "full_population_command": _full_population_command(
                            args,
                            profile_path,
                        ),
                    }
                    _emit(result, args.json_out)
                    return 4
                run_magemin_key_hashes.update(_magemin_key_hashes(shard_db))
                if args.validate_replay:
                    replay_merge_summary = _replay_cache_merge_summary(
                        shard_db,
                        replay_db_path,
                    )
                    case_result["cache_merge"] = {
                        "merged": False,
                        "pending_validation": True,
                        "source": "temporary_shard",
                        "rows": replay_merge_summary["rows"],
                        "magemin_unique_keys": replay_merge_summary[
                            "magemin_unique_keys"
                        ],
                        "validation_cache": replay_merge_summary,
                    }
                    pending_shards.append((case_result, shard_db))
                else:
                    merge_summary = _merge_cache_shard(shard_db, args.db)
                    case_result["cache_merge"] = merge_summary
                    cache_merges.append(merge_summary)
                live_results.append(case_result)
                captured_cases.append((feedstock_name, campaign))

        replay_results = []
        if args.validate_replay:
            for feedstock_name, campaign in captured_cases:
                additives_kg = _feedstock_additives(
                    feedstock_name,
                    loaded_profile=profile,
                    cli_additives=cli_additives_kg,
                )
                additives_by_feedstock[feedstock_name] = additives_kg
                replay_results.append(
                    _run_case(
                        feedstock=feedstock_name,
                        campaign=campaign,
                        backend_name=args.backend,
                        mass_kg=args.mass_kg,
                        additives_kg=additives_kg,
                        hours=args.hours,
                        wall_cap_s=args.wall_cap_s,
                        db_path=replay_db_path,
                        mode="replay",
                        disable_live=True,
                        allow_stub_equilibrium=args.allow_stub_equilibrium,
                        control_quantization=args.control_quantization,
                    )
                )

        all_live_timings = [
            item
            for result in live_results
            for item in result.get("magemin_timings", [])
        ]
        timing_summary = _timing_stats(all_live_timings)
        observed_hours = sum(int(result["hours_completed"]) for result in live_results)
        validation = (
            _validation_summary(live_results, replay_results) if replay_results else None
        )
        validation_failed = (
            validation is not None and not validation["cached_exact_confirmed"]
        )
        if args.validate_replay:
            cache_merges = []
            if validation_failed:
                for case_result, shard_db in pending_shards:
                    discard_summary = _discarded_cache_shard_summary(
                        shard_db,
                        "replay_validation_failed",
                    )
                    case_result["cache_merge"] = discard_summary
                    cache_merges.append(discard_summary)
            else:
                for case_result, shard_db in pending_shards:
                    merge_summary = _merge_cache_shard(shard_db, args.db)
                    case_result["cache_merge"] = merge_summary
                    cache_merges.append(merge_summary)
        cache_summary = _cache_row_summary(args.db)
        result = {
            "status": "failed" if validation_failed else "complete",
            "profile": str(profile_path.relative_to(REPO_ROOT)),
            "campaigns": list(campaigns),
            "additives_kg": _additives_result(feedstocks, additives_by_feedstock),
            "db": str(args.db),
            "allow_stub_equilibrium": bool(args.allow_stub_equilibrium),
            "magemin": magemin,
            "live": live_results,
            "replay": replay_results,
            "case_gaps": list(case_gaps),
            "domain_gaps": list(case_gaps),
            "domain_gap_count": len(case_gaps),
            "cache": cache_summary,
            "cache_merges": cache_merges,
            "magemin_timing": timing_summary,
            "validation": validation,
            "estimate": _estimate(
                observed_magemin_keys=len(run_magemin_key_hashes),
                observed_hours=observed_hours,
                timings=timing_summary,
                include_kreep=args.include_kreep,
            ),
            "full_population_command": _full_population_command(args, profile_path),
        }
        if validation_failed:
            result["failed_reason"] = "replay_validation_failed"
        _emit(result, args.json_out)
        if validation_failed:
            return 3
        return 0


def _validation_summary(
    live_results: list[dict[str, Any]],
    replay_results: list[dict[str, Any]],
) -> dict[str, Any]:
    live_view = [result["trace_view"] for result in live_results]
    replay_view = [result["trace_view"] for result in replay_results]
    trace_equal = _canonical(live_view) == _canonical(replay_view)
    mass_balance_equal = _canonical(
        _mass_balance_trace(live_results)
    ) == _canonical(_mass_balance_trace(replay_results))
    replay_live_calls = sum(
        len(row.get("magemin_calls") or [])
        for result in replay_results
        for row in result.get("rows", [])
    )
    replay_counts = Counter()
    replay_misses = 0
    for result in replay_results:
        replay_misses += int(result["store_summary"].get("misses") or 0)
        for event in result["store_summary"].get("cache_state_counts", {}).items():
            replay_counts[event[0]] += int(event[1])
    return {
        "trace_equal": trace_equal,
        "mass_balance_equal": mass_balance_equal,
        "replay_live_magemin_calls": replay_live_calls,
        "cached_exact_confirmed": trace_equal
        and mass_balance_equal
        and replay_counts["cached_exact"] > 0
        and replay_counts["live_fill"] == 0
        and replay_misses == 0
        and replay_live_calls == 0,
        "replay_cache_state_counts": dict(sorted(replay_counts.items())),
        "replay_misses": replay_misses,
    }


def _mass_balance_trace(results: list[dict[str, Any]]) -> list[float]:
    return [
        float(row["mass_balance_error_pct"])
        for result in results
        for row in result.get("rows", [])
    ]


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _full_population_command(args: argparse.Namespace, profile_path: Path) -> str:
    feedstocks = [*CAL_FEEDSTOCKS, KREEP_FEEDSTOCK]
    parts = [
        "python3",
        "scripts/populate_reduced_real_cache.py",
        "--profile",
        str(profile_path.relative_to(REPO_ROOT)),
        "--db",
        "docs-private/reviews/2026-06-04-tier-pt3/full-population.db",
        "--hours",
        "30",
        "--backend",
        "alphamelts",
        "--wall-cap-s",
        "43200",
        "--require-magemin",
    ]
    for feedstock in feedstocks:
        parts.extend(["--feedstock", feedstock])
    for campaign in FIRST_FLIP_CAMPAIGNS:
        parts.extend(["--campaign", campaign])
    return " ".join(parts)


def _emit(result: dict[str, Any], json_out: Path | None) -> None:
    text = json.dumps(result, indent=2, sort_keys=True, default=str)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
