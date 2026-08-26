#!/usr/bin/env python3
"""Replay t-735 grind-cache lookup identity and timing on a live accumulator.

The committed unit tests cover a synthetic fixture. This script is the
replayable live-corpus harness for the claims in
``docs-private/research/2026-08-26-t735-phasecontext-perf/findings.md``:

- identity of BETWEEN+residual vs the frozen pre-t735 ABS SQL over the
  real accumulator (default
  ``docs-private/recipe-db/grind-accumulator.db``, 45,198 rows)
- wall/CPU timing of uncached lookups with ``lru_cache`` cleared; after
  numbers must come from an indexed copy (``--index-copy``), never from
  mutating the production file. ``--index-copy`` applies ``CREATE INDEX``
  on a raw sqlite3 connection (not ``GridCacheWriter``), so it works on
  the live pre-v2 accumulator.

The frozen ABS SQL matches
``tests/chemistry/test_phase_context_lookup_identity.py::_LEGACY_SELECT``.

Examples::

  .venv/bin/python scripts/replay_phase_context_lookup_identity.py \\
    --db docs-private/recipe-db/grind-accumulator.db --identity

  .venv/bin/python scripts/replay_phase_context_lookup_identity.py \\
    --db docs-private/recipe-db/grind-accumulator.db --bench --index-copy /tmp/t735-indexed
"""

from __future__ import annotations

import argparse
import math
import resource
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.grid_pregrind_writer import install_phase_context_lookup_indexes
from simulator.chemistry import phase_context as pc
from simulator.chemistry.phase_context import (
    CONTROL_MATCH_TOLERANCE,
    DEFAULT_GRIND_CACHE,
    LIQUIDUS_MATCH_TOLERANCE_C,
    _uncached_grind_cache_lookup,
)

_LEGACY_SELECT = """
        SELECT o.id
          FROM alphamelts_outputs o
          JOIN grid_keys g ON g.id = o.grid_key_id
         WHERE o.status = 'ok'
           AND o.status_kind = 'success'
           AND o.generic_phase_assemblage_available = 1
           AND g.artifact_kind = 'equilibrium'
           AND o.engine_epoch {epoch_predicate}
           AND ABS({temperature_column} - ?) <= ?
           AND ABS(g.pressure_bar - ?) <= ?
           AND ABS(g.fO2_log - ?) <= ?
         ORDER BY ABS({temperature_column} - ?), o.id
"""

_NEW_SELECT = """
        SELECT o.id
          FROM alphamelts_outputs o
          JOIN grid_keys g ON g.id = o.grid_key_id
         WHERE o.status = 'ok'
           AND o.status_kind = 'success'
           AND o.generic_phase_assemblage_available = 1
           AND g.artifact_kind = 'equilibrium'
           AND o.engine_epoch {epoch_predicate}
           AND {temperature_column} BETWEEN ? AND ?
           AND ABS({temperature_column} - ?) <= ?
           AND g.pressure_bar BETWEEN ? AND ?
           AND ABS(g.pressure_bar - ?) <= ?
           AND g.fO2_log BETWEEN ? AND ?
           AND ABS(g.fO2_log - ?) <= ?
         ORDER BY ABS({temperature_column} - ?), o.id
"""


def _cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return float(usage.ru_utime + usage.ru_stime)


def _candidate_ids(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple,
    *,
    include_isothermal: bool,
) -> list[int]:
    rows: list[int] = []
    if include_isothermal:
        rows.extend(
            int(row[0])
            for row in connection.execute(
                sql.format(
                    temperature_column="o.generic_temperature_C",
                    epoch_predicate=">= 2",
                ),
                params,
            )
        )
    rows.extend(
        int(row[0])
        for row in connection.execute(
            sql.format(
                temperature_column=(
                    "COALESCE(o.alpha_liquidus_T_C, o.generic_liquidus_T_C)"
                ),
                epoch_predicate="= 1",
            ),
            params,
        )
    )
    return rows


def _query_params(
    temperature_C: float, pressure_bar: float, fO2_log: float
) -> tuple[tuple, tuple]:
    t_lo, t_hi = pc._sargable_bounds(temperature_C, LIQUIDUS_MATCH_TOLERANCE_C)
    p_lo, p_hi = pc._sargable_bounds(pressure_bar, CONTROL_MATCH_TOLERANCE)
    f_lo, f_hi = pc._sargable_bounds(fO2_log, CONTROL_MATCH_TOLERANCE)
    legacy = (
        temperature_C,
        LIQUIDUS_MATCH_TOLERANCE_C,
        pressure_bar,
        CONTROL_MATCH_TOLERANCE,
        fO2_log,
        CONTROL_MATCH_TOLERANCE,
        temperature_C,
    )
    rewritten = (
        t_lo,
        t_hi,
        temperature_C,
        LIQUIDUS_MATCH_TOLERANCE_C,
        p_lo,
        p_hi,
        pressure_bar,
        CONTROL_MATCH_TOLERANCE,
        f_lo,
        f_hi,
        fO2_log,
        CONTROL_MATCH_TOLERANCE,
        temperature_C,
    )
    return legacy, rewritten


def _copy_and_index(source: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    shutil.copy2(source, dest)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(source) + suffix)
        if sidecar.is_file():
            shutil.copy2(sidecar, dest_dir / sidecar.name)
    # GridCacheWriter(existing_only=True) refuses pre-v2 caches
    # (missing input_payload_json). CREATE INDEX is additive and is
    # the path that works on the live grind-accumulator.db.
    with sqlite3.connect(dest) as connection:
        install_phase_context_lookup_indexes(connection)
        connection.commit()
    return dest


def _identity(path: Path) -> int:
    uri = f"file:{path.resolve()}?mode=ro"
    mismatches = 0
    compared = 0
    with sqlite3.connect(uri, uri=True) as connection:
        max_epoch = int(
            connection.execute(
                "SELECT MAX(engine_epoch) FROM alphamelts_outputs"
            ).fetchone()[0]
            or 0
        )
        include_isothermal = max_epoch >= 2
        row_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM alphamelts_outputs"
            ).fetchone()[0]
        )
        centers = connection.execute(
            """
            SELECT DISTINCT
                   COALESCE(o.alpha_liquidus_T_C, o.generic_liquidus_T_C),
                   g.pressure_bar,
                   g.fO2_log
              FROM alphamelts_outputs o
              JOIN grid_keys g ON g.id = o.grid_key_id
             WHERE o.status = 'ok'
               AND o.status_kind = 'success'
               AND o.generic_phase_assemblage_available = 1
               AND g.artifact_kind = 'equilibrium'
               AND COALESCE(o.alpha_liquidus_T_C, o.generic_liquidus_T_C)
                   IS NOT NULL
            """
        ).fetchall()
        queries: list[tuple[float, float, float]] = [
            (float(t), float(p), float(fo2)) for t, p, fo2 in centers
        ]
        extra: list[tuple[float, float, float]] = []
        for temperature_C, pressure_bar, fO2_log in queries[:120]:
            extra.extend(
                [
                    (
                        temperature_C - LIQUIDUS_MATCH_TOLERANCE_C,
                        pressure_bar,
                        fO2_log,
                    ),
                    (
                        temperature_C + LIQUIDUS_MATCH_TOLERANCE_C,
                        pressure_bar,
                        fO2_log,
                    ),
                    (
                        math.nextafter(
                            temperature_C + LIQUIDUS_MATCH_TOLERANCE_C,
                            float("inf"),
                        ),
                        pressure_bar,
                        fO2_log,
                    ),
                ]
            )
        extra.extend(
            [
                (1000.0, 1.0, -9.0),
                (1300.0, 99.0, -9.0),
                (1300.0, 1.0, 0.0),
                (2000.0, 1.0, -9.0),
            ]
        )
        seen: set[tuple[float, float, float]] = set()
        for query in queries + extra:
            if query in seen:
                continue
            seen.add(query)
            temperature_C, pressure_bar, fO2_log = query
            legacy_params, new_params = _query_params(
                temperature_C, pressure_bar, fO2_log
            )
            legacy_ids = _candidate_ids(
                connection,
                _LEGACY_SELECT,
                legacy_params,
                include_isothermal=include_isothermal,
            )
            new_ids = _candidate_ids(
                connection,
                _NEW_SELECT,
                new_params,
                include_isothermal=include_isothermal,
            )
            compared += 1
            if legacy_ids != new_ids:
                mismatches += 1
                if mismatches <= 5:
                    print(
                        f"mismatch T={temperature_C} P={pressure_bar} "
                        f"fO2={fO2_log} legacy={legacy_ids[:8]} "
                        f"new={new_ids[:8]}",
                        file=sys.stderr,
                    )
    print(
        f"identity: rows={row_count} queries={compared} mismatches={mismatches} "
        f"max_epoch={max_epoch} db={path}"
    )
    return 1 if mismatches else 0


def _bench(path: Path, temperature_C: float, repeats: int) -> None:
    composition = {"SiO2": 1.0, "MgO": 1.0}
    kwargs = dict(
        temperature_C=temperature_C,
        pressure_bar=1.0,
        composition_mol=composition,
        fO2_log=-9.0,
        liquidus_temperature_C=None,
    )
    pc._cached_grind_cache_lookup.cache_clear()
    pc._max_engine_epoch.cache_clear()
    pc._close_grind_cache_connections()

    def _once() -> tuple[int | None, int]:
        pc._cached_grind_cache_lookup.cache_clear()
        result, _prov = _uncached_grind_cache_lookup(path, **kwargs)
        selected = None if result is None else int(result["id"])
        return selected, len(result or {})

    wall0 = time.perf_counter()
    cpu0 = _cpu_seconds()
    selected, _ = _once()
    first_wall = time.perf_counter() - wall0
    first_cpu = _cpu_seconds() - cpu0

    warm_wall = []
    warm_cpu = []
    for _ in range(repeats):
        wall0 = time.perf_counter()
        cpu0 = _cpu_seconds()
        _once()
        warm_wall.append(time.perf_counter() - wall0)
        warm_cpu.append(_cpu_seconds() - cpu0)

    print(
        f"bench db={path} T={temperature_C} selected={selected} "
        f"first_wall_s={first_wall:.6f} first_cpu_s={first_cpu:.6f} "
        f"warm_wall_s={min(warm_wall):.6f}..{max(warm_wall):.6f} "
        f"warm_cpu_s={min(warm_cpu):.6f}..{max(warm_cpu):.6f} "
        f"repeats={repeats} work=1_uncached_lookup lru_cleared=1"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_GRIND_CACHE)
    parser.add_argument("--identity", action="store_true")
    parser.add_argument("--bench", action="store_true")
    parser.add_argument(
        "--index-copy",
        type=Path,
        default=None,
        help=(
            "copy the DB here and CREATE INDEX (legacy and v2; "
            "does not mutate --db; does not use GridCacheWriter)"
        ),
    )
    parser.add_argument("--temperature", type=float, default=1317.77)
    parser.add_argument("--repeats", type=int, default=8)
    args = parser.parse_args()
    if not args.identity and not args.bench:
        parser.error("specify --identity and/or --bench")
    path = args.db
    if not path.is_file():
        print(f"database missing: {path}", file=sys.stderr)
        return 2
    if args.index_copy is not None:
        path = _copy_and_index(path, args.index_copy)
        print(f"indexed copy: {path}")
    status = 0
    if args.identity:
        status = _identity(path)
    if args.bench:
        _bench(path, args.temperature, args.repeats)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
