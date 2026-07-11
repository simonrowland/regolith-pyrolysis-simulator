#!/usr/bin/env python3
"""Record alphaMELTS-solved VapoRock vs Antoine shadow vapor pressures."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.metadata
import json
import math
from pathlib import Path
import sqlite3
from statistics import median
import subprocess
import sys
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MAX_SOLVES = 50

import yaml  # noqa: E402

from simulator.melt_backend.alphamelts import AlphaMELTSBackend  # noqa: E402
from simulator.melt_backend.vaporock import VapoRockBackend  # noqa: E402
from simulator.optimize.canonical import (  # noqa: E402
    canonical_json_dumps,
    normalize_canonical_value,
)

DB_PATH = REPO_ROOT / "docs-private" / "shadow-matrix" / "shadow.db"
VERDICT_PATH = REPO_ROOT / "docs-private" / "shadow-matrix" / "verdict.md"
BUILD_SUMMARY_PATH = (
    REPO_ROOT / "docs-private" / "shadow-matrix" / "build-summary.md"
)
DEFAULT_PROFILE_FILES = (
    "lunar_mare_low_ti.yaml",
    "lunar_highland.yaml",
    "mars_basalt.yaml",
)
FOCUS_SPECIES = ("Na", "K", "SiO", "Fe", "Mg", "Si", "O", "Ca")
SMOKE_SPECIES = ("Na", "SiO", "Fe")
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Cell:
    profile_id: str
    profile_path: Path
    feedstock_id: str
    composition_wt_pct: Mapping[str, float]
    composition_digest: str
    t_k: float
    fO2_log10_bar: float
    pressure_bar: float
    pressure_context: Mapping[str, Any]
    temperature_source: str


@dataclass(frozen=True)
class Row:
    species: str
    vaporock_pa: float | None
    antoine_pa: float | None
    log10_delta: float | None
    flags: tuple[str, ...]


class ShadowStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shadow_rows (
                    cache_key TEXT NOT NULL,
                    species TEXT NOT NULL,
                    cell_key TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    composition_digest TEXT NOT NULL,
                    T_K REAL NOT NULL,
                    fO2_log10_bar REAL NOT NULL,
                    pressure REAL NOT NULL,
                    pressure_context_json TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    engine_versions_json TEXT NOT NULL,
                    VERSION TEXT NOT NULL,
                    data_digests_json TEXT NOT NULL,
                    vaporock_Pa REAL,
                    antoine_Pa REAL,
                    log10_delta REAL,
                    flags TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    git_dirty INTEGER NOT NULL,
                    PRIMARY KEY (cache_key, species)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shadow_rows_cell_key
                ON shadow_rows(cell_key)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shadow_rows_species
                ON shadow_rows(species)
                """
            )
            conn.execute(
                """
                INSERT INTO metadata(key, value) VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

    def cell_present(self, cell_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM shadow_rows WHERE cell_key = ? LIMIT 1",
                (cell_key,),
            ).fetchone()
        return row is not None

    def upsert_rows(
        self,
        *,
        cell: Cell,
        rows: Sequence[Row],
        backend: str,
        engine_versions: Mapping[str, str],
        repo_version: str,
        data_digests: Mapping[str, str],
        git_dirty: bool,
        created_at: str,
    ) -> list[sqlite3.Row]:
        cell_key = make_cell_key(
            cell=cell,
            backend=backend,
            engine_versions=engine_versions,
            repo_version=repo_version,
            data_digests=data_digests,
        )
        inserted: list[sqlite3.Row] = []
        with self._connect() as conn:
            for row in rows:
                cache_key = make_cache_key(
                    cell=cell,
                    species=row.species,
                    backend=backend,
                    engine_versions=engine_versions,
                    repo_version=repo_version,
                    data_digests=data_digests,
                )
                conn.execute(
                    """
                    INSERT INTO shadow_rows (
                        cache_key, species, cell_key, profile_id,
                        composition_digest, T_K, fO2_log10_bar, pressure,
                        pressure_context_json, backend, engine_versions_json,
                        VERSION, data_digests_json, vaporock_Pa, antoine_Pa,
                        log10_delta, flags, created_at, git_dirty
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key, species) DO UPDATE SET
                        cell_key = excluded.cell_key,
                        profile_id = excluded.profile_id,
                        composition_digest = excluded.composition_digest,
                        T_K = excluded.T_K,
                        fO2_log10_bar = excluded.fO2_log10_bar,
                        pressure = excluded.pressure,
                        pressure_context_json = excluded.pressure_context_json,
                        backend = excluded.backend,
                        engine_versions_json = excluded.engine_versions_json,
                        VERSION = excluded.VERSION,
                        data_digests_json = excluded.data_digests_json,
                        vaporock_Pa = excluded.vaporock_Pa,
                        antoine_Pa = excluded.antoine_Pa,
                        log10_delta = excluded.log10_delta,
                        flags = excluded.flags,
                        created_at = excluded.created_at,
                        git_dirty = excluded.git_dirty
                    """,
                    (
                        cache_key,
                        row.species,
                        cell_key,
                        cell.profile_id,
                        cell.composition_digest,
                        cell.t_k,
                        cell.fO2_log10_bar,
                        cell.pressure_bar,
                        canonical_json(cell.pressure_context),
                        backend,
                        canonical_json(engine_versions),
                        repo_version,
                        canonical_json(data_digests),
                        row.vaporock_pa,
                        row.antoine_pa,
                        row.log10_delta,
                        canonical_json(list(row.flags)),
                        created_at,
                        int(git_dirty),
                    ),
                )
            conn.commit()
            for row in rows:
                cache_key = make_cache_key(
                    cell=cell,
                    species=row.species,
                    backend=backend,
                    engine_versions=engine_versions,
                    repo_version=repo_version,
                    data_digests=data_digests,
                )
                stored = conn.execute(
                    """
                    SELECT cache_key, profile_id, species, vaporock_Pa,
                           antoine_Pa, log10_delta, flags
                    FROM shadow_rows
                    WHERE cache_key = ? AND species = ?
                    """,
                    (cache_key, row.species),
                ).fetchone()
                if stored is not None:
                    inserted.append(stored)
        return inserted

    def verdict_rows(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT species, log10_delta, flags, profile_id, T_K
                    FROM shadow_rows
                    ORDER BY species, profile_id, T_K
                    """
                )
            )

    def rows_for_cell(self, cell_key: str, species: Sequence[str]) -> list[sqlite3.Row]:
        placeholders = ", ".join("?" for _ in species)
        with self._connect() as conn:
            return list(
                conn.execute(
                    f"""
                    SELECT cache_key, profile_id, species, vaporock_Pa,
                           antoine_Pa, log10_delta, flags
                    FROM shadow_rows
                    WHERE cell_key = ? AND species IN ({placeholders})
                    ORDER BY species
                    """,
                    (cell_key, *species),
                )
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Record diagnostic-only VapoRock vs Antoine vapor pressures at "
            "alphaMELTS-solved melt states."
        )
    )
    parser.add_argument("--smoke", action="store_true", help="run exactly one cell")
    parser.add_argument(
        "--force",
        action="store_true",
        help="recompute cells even when their content-addressed rows already exist",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help="SQLite store path",
    )
    parser.add_argument(
        "--profiles",
        nargs="+",
        default=list(DEFAULT_PROFILE_FILES),
        help="profile yaml names or paths under data/optimize_profiles",
    )
    parser.add_argument(
        "--temperatures-per-profile",
        type=int,
        default=9,
        help="temperature samples per profile for non-smoke runs",
    )
    parser.add_argument(
        "--large-delta-log10",
        type=float,
        default=1.0,
        help="report-only flag threshold; never used as pass/fail",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected_profiles = resolve_profile_paths(args.profiles)
    print(
        "profiles="
        + ", ".join(path.relative_to(REPO_ROOT).as_posix() for path in selected_profiles),
        flush=True,
    )

    feedstocks = load_yaml(REPO_ROOT / "data" / "feedstocks.yaml")
    setpoints = load_yaml(REPO_ROOT / "data" / "setpoints.yaml")
    cells = build_cells(
        selected_profiles,
        feedstocks=feedstocks,
        setpoints=setpoints,
        temperatures_per_profile=args.temperatures_per_profile,
        smoke=args.smoke,
    )
    if len(cells) > MAX_SOLVES:
        dropped = cells[MAX_SOLVES:]
        cells = cells[:MAX_SOLVES]
        print(
            "MAX_SOLVES truncate dropped="
            + ", ".join(
                f"{cell.profile_id}@{cell.t_k:.2f}K" for cell in dropped
            ),
            flush=True,
        )

    repo_version = read_repo_version()
    git_dirty = is_git_dirty()
    engine_versions = collect_engine_versions()
    backend = "alphamelts_solved_melt+vaporock+antoine_activity"
    store = ShadowStore(args.db)

    alpha = AlphaMELTSBackend()
    vaporock = VapoRockBackend()
    alpha_ready = alpha.initialize({})
    vaporock_ready = vaporock.initialize({})
    engine_versions = {
        **engine_versions,
        "alphamelts_backend": alpha.get_engine_version(),
        "alphamelts_mode": str(getattr(alpha, "_mode", None)),
        "vaporock_backend": vaporock_version_label(vaporock_ready),
    }

    smoke_rows: list[sqlite3.Row] = []
    skipped = 0
    solved = 0
    failures = 0
    for cell in cells:
        data_digests = data_digests_for(cell.profile_path)
        cell_key = make_cell_key(
            cell=cell,
            backend=backend,
            engine_versions=engine_versions,
            repo_version=repo_version,
            data_digests=data_digests,
        )
        if store.cell_present(cell_key) and not args.force:
            skipped += 1
            print(
                f"skip existing cell {cell.profile_id} {cell.t_k:.2f}K",
                flush=True,
            )
            if args.smoke:
                smoke_rows = store.rows_for_cell(cell_key, SMOKE_SPECIES)
                print_smoke_rows(smoke_rows)
            continue

        rows = solve_cell(
            alpha=alpha,
            vaporock=vaporock,
            alpha_ready=alpha_ready,
            vaporock_ready=vaporock_ready,
            cell=cell,
            large_delta_log10=args.large_delta_log10,
        )
        if any(flag.startswith("cell_failed:") for row in rows for flag in row.flags):
            failures += 1
        else:
            solved += 1
        stored = store.upsert_rows(
            cell=cell,
            rows=rows,
            backend=backend,
            engine_versions=engine_versions,
            repo_version=repo_version,
            data_digests=data_digests,
            git_dirty=git_dirty,
            created_at=utc_now(),
        )
        if args.smoke:
            smoke_rows = [
                row for row in stored if str(row["species"]) in SMOKE_SPECIES
            ]
            print_smoke_rows(smoke_rows)

    write_verdict(store, VERDICT_PATH)
    write_build_summary(
        path=BUILD_SUMMARY_PATH,
        db_path=args.db,
        profiles=selected_profiles,
        cells=cells,
        engine_versions=engine_versions,
        smoke=args.smoke,
        smoke_rows=smoke_rows,
        skipped=skipped,
        solved=solved,
        failures=failures,
    )
    if args.smoke and not smoke_rows and skipped == 0:
        raise SystemExit("smoke did not record/query Na/SiO/Fe rows")
    print(f"verdict={VERDICT_PATH.relative_to(REPO_ROOT)}", flush=True)
    print(f"build_summary={BUILD_SUMMARY_PATH.relative_to(REPO_ROOT)}", flush=True)
    return 0


def solve_cell(
    *,
    alpha: AlphaMELTSBackend,
    vaporock: VapoRockBackend,
    alpha_ready: bool,
    vaporock_ready: bool,
    cell: Cell,
    large_delta_log10: float,
) -> list[Row]:
    if not alpha_ready or not alpha.is_available():
        return failure_rows(cell, "engine_unavailable:alphamelts")
    if not vaporock_ready or not vaporock.is_available():
        return failure_rows(cell, "engine_unavailable:vaporock")

    try:
        result = alpha.equilibrate(
            temperature_C=cell.t_k - 273.15,
            composition_kg=dict(cell.composition_wt_pct),
            fO2_log=cell.fO2_log10_bar,
            pressure_bar=cell.pressure_bar,
            subprocess_run_mode="isothermal",
        )
    except Exception as exc:  # noqa: BLE001
        return failure_rows(cell, f"cell_failed:alphamelts_exception:{type(exc).__name__}:{exc}")

    if result.status != "ok":
        detail = "; ".join(result.warnings) or result.status
        return failure_rows(cell, f"cell_failed:alphamelts_status:{detail}")

    melt_wt = {
        str(species): float(value)
        for species, value in (result.liquid_composition_wt_pct or {}).items()
        if finite_positive(value)
    }
    if not melt_wt:
        return failure_rows(cell, "cell_failed:species_mapping:no_solved_liquid_composition")

    activities = dict(result.activity_coefficients or {})
    try:
        vaporock_result = vaporock.equilibrate(
            temperature_C=cell.t_k - 273.15,
            composition_kg=melt_wt,
            fO2_log=cell.fO2_log10_bar,
            pressure_bar=cell.pressure_bar,
        )
    except Exception as exc:  # noqa: BLE001
        return failure_rows(cell, f"cell_failed:vaporock_exception:{type(exc).__name__}:{exc}")

    vaporock_pressures = dict(vaporock_result.vapor_pressures_Pa or {})
    antoine_pressures = alpha._activities_times_antoine(  # noqa: SLF001
        cell.t_k - 273.15,
        activities,
        melt_wt,
    )
    if vaporock_result.status != "ok":
        detail = "; ".join(vaporock_result.warnings) or vaporock_result.status
        return rows_from_pressures(
            vaporock_pressures,
            antoine_pressures,
            ("cell_failed:vaporock_status:" + detail,),
            large_delta_log10,
        )

    volatile_bearing = alpha._melt_has_antoine_vapor_precursor(  # noqa: SLF001
        melt_wt,
        alpha._load_vapor_pressure_table(),  # noqa: SLF001
    )
    if volatile_bearing and not vaporock_pressures and not antoine_pressures:
        return failure_rows(
            cell,
            "cell_failed:no_pressures_for_volatile_bearing_melt",
        )
    return rows_from_pressures(
        vaporock_pressures,
        antoine_pressures,
        (),
        large_delta_log10,
    )


def rows_from_pressures(
    vaporock_pressures: Mapping[str, Any],
    antoine_pressures: Mapping[str, Any],
    cell_flags: tuple[str, ...],
    large_delta_log10: float,
) -> list[Row]:
    species_names = sorted(
        {normalize_species_name(s) for s in vaporock_pressures}
        | {normalize_species_name(s) for s in antoine_pressures}
        | set(FOCUS_SPECIES)
    )
    rows: list[Row] = []
    for species in species_names:
        flags = list(cell_flags)
        vaporock_pa = finite_pressure_or_none(vaporock_pressures.get(species))
        antoine_pa = finite_pressure_or_none(antoine_pressures.get(species))
        if species in vaporock_pressures and vaporock_pa is None:
            flags.append("nonfinite_vaporock")
        if species in antoine_pressures and antoine_pa is None:
            flags.append("nonfinite_antoine")
        if vaporock_pa is None:
            flags.append("missing_vaporock")
        if antoine_pa is None:
            flags.append("missing_antoine")
        log10_delta = None
        if vaporock_pa is not None and antoine_pa is not None:
            log10_delta = math.log10(vaporock_pa / antoine_pa)
            if abs(log10_delta) >= large_delta_log10:
                flags.append(
                    f"report_only_large_delta_abs_log10_ge_{large_delta_log10:g}"
                )
        rows.append(
            Row(
                species=species,
                vaporock_pa=vaporock_pa,
                antoine_pa=antoine_pa,
                log10_delta=log10_delta,
                flags=tuple(sorted(set(flags))),
            )
        )
    return rows


def failure_rows(cell: Cell, reason: str) -> list[Row]:
    flag = reason if reason.startswith("cell_failed:") else f"cell_failed:{reason}"
    return [
        Row(
            species=species,
            vaporock_pa=None,
            antoine_pa=None,
            log10_delta=None,
            flags=(flag,),
        )
        for species in FOCUS_SPECIES
    ]


def build_cells(
    profile_paths: Sequence[Path],
    *,
    feedstocks: Mapping[str, Any],
    setpoints: Mapping[str, Any],
    temperatures_per_profile: int,
    smoke: bool,
) -> list[Cell]:
    cells: list[Cell] = []
    profiles_to_use = profile_paths[:1] if smoke else profile_paths
    for profile_path in profiles_to_use:
        profile = load_yaml(profile_path)
        profile_id = str(profile["profile_id"])
        feedstock_id = str(profile["feedstock"])
        feedstock = feedstocks.get(feedstock_id)
        if not isinstance(feedstock, Mapping):
            raise ValueError(f"{profile_id}: unknown feedstock {feedstock_id!r}")
        composition = normalize_composition(feedstock.get("composition_wt_pct"))
        composition_digest = sha256_canonical(composition)
        pressure_bar, pressure_context = pressure_for_profile(profile, setpoints)
        grid, temperature_source = temperature_grid_for_profile(
            profile,
            setpoints,
            count=temperatures_per_profile,
        )
        if smoke:
            grid = [max(grid)]
        for t_k in grid:
            cells.append(
                Cell(
                    profile_id=profile_id,
                    profile_path=profile_path,
                    feedstock_id=feedstock_id,
                    composition_wt_pct=composition,
                    composition_digest=composition_digest,
                    t_k=float(t_k),
                    fO2_log10_bar=intrinsic_melt_fO2(composition, float(t_k)),
                    pressure_bar=pressure_bar,
                    pressure_context=pressure_context,
                    temperature_source=temperature_source,
                )
            )
    return cells


def temperature_grid_for_profile(
    profile: Mapping[str, Any],
    setpoints: Mapping[str, Any],
    *,
    count: int,
) -> tuple[list[float], str]:
    temp_range_c, source = campaign_value(profile, setpoints, "temp_range_C")
    if not temp_range_c:
        return linear_grid(1200.0, 2200.0, max(1, count)), "fallback_1200_2200K"
    start_c, end_c = float(temp_range_c[0]), float(temp_range_c[1])
    return (
        linear_grid(start_c + 273.15, end_c + 273.15, max(1, count)),
        f"{source}.temp_range_C",
    )


def pressure_for_profile(
    profile: Mapping[str, Any],
    setpoints: Mapping[str, Any],
) -> tuple[float, Mapping[str, Any]]:
    default_mbar, source = campaign_value(profile, setpoints, "p_total_mbar_default")
    if default_mbar is not None:
        pressure_bar = float(default_mbar) / 1000.0
        return pressure_bar, {
            "pressure_bar": pressure_bar,
            "source": f"{source}.p_total_mbar_default",
            "units": "bar",
        }
    range_mbar, source = campaign_value(profile, setpoints, "p_total_mbar")
    if range_mbar:
        pressure_bar = (float(range_mbar[0]) + float(range_mbar[1])) / 2000.0
        return pressure_bar, {
            "pressure_bar": pressure_bar,
            "source": f"{source}.p_total_mbar_midpoint",
            "units": "bar",
        }
    pressure_bar = 1.0e-6
    return pressure_bar, {
        "pressure_bar": pressure_bar,
        "source": "fallback_hard_vacuum",
        "units": "bar",
    }


def campaign_value(
    profile: Mapping[str, Any],
    setpoints: Mapping[str, Any],
    key: str,
) -> tuple[Any | None, str]:
    campaigns = (setpoints.get("campaigns") or {}) if isinstance(setpoints, Mapping) else {}
    for seed in profile.get("seed_recipes") or ():
        names: list[str] = []
        if seed.get("source_campaign"):
            names.append(str(seed["source_campaign"]))
        names.extend(str(name) for name in seed.get("source_campaigns") or ())
        patch_campaigns = ((seed.get("patch") or {}).get("campaigns") or {})
        for name in names:
            patched = patch_campaigns.get(name) or {}
            if key in patched:
                return patched[key], f"profile:{profile['profile_id']}:seed:{seed.get('id')}:{name}"
            configured = campaigns.get(name) or {}
            if key in configured:
                return configured[key], f"setpoints:{name}"
    run_campaign = (profile.get("run") or {}).get("campaign")
    if run_campaign:
        configured = campaigns.get(str(run_campaign)) or {}
        if key in configured:
            return configured[key], f"setpoints:{run_campaign}"
    return None, "none"


def intrinsic_melt_fO2(composition_wt_pct: Mapping[str, float], temperature_k: float) -> float:
    if temperature_k <= 0.0:
        return -9.0
    feo = max(0.0, float(composition_wt_pct.get("FeO", 0.0)))
    fe2o3 = max(0.0, float(composition_wt_pct.get("Fe2O3", 0.0)))
    alkali = max(0.0, float(composition_wt_pct.get("Na2O", 0.0))) + max(
        0.0,
        float(composition_wt_pct.get("K2O", 0.0)),
    )
    log_iw = -27215.0 / temperature_k + 6.57
    redox_offset = 0.0
    if feo > 0.0 and fe2o3 > 0.0:
        redox_offset += 0.25 * math.log10(max(fe2o3 / feo, 1.0e-12))
    redox_offset += min(0.15, alkali * 0.01)
    return max(-9.0, min(0.0, log_iw + redox_offset))


def resolve_profile_paths(values: Sequence[str]) -> list[Path]:
    base = REPO_ROOT / "data" / "optimize_profiles"
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            path = base / value
        if not path.exists():
            raise FileNotFoundError(path)
        paths.append(path)
    return paths


def normalize_composition(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError("composition_wt_pct must be a mapping")
    composition: dict[str, float] = {}
    for species, amount in value.items():
        number = float(amount)
        if number > 0.0 and math.isfinite(number):
            composition[str(species)] = number
    if not composition:
        raise ValueError("composition_wt_pct is empty")
    return composition


def load_yaml(path: Path) -> Any:
    with path.open() as handle:
        return yaml.safe_load(handle) or {}


def data_digests_for(profile_path: Path) -> dict[str, str]:
    return {
        "feedstocks": file_sha256(REPO_ROOT / "data" / "feedstocks.yaml"),
        "profile": file_sha256(profile_path),
        "setpoints": file_sha256(REPO_ROOT / "data" / "setpoints.yaml"),
        "vapor_pressures": file_sha256(REPO_ROOT / "data" / "vapor_pressures.yaml"),
    }


def collect_engine_versions() -> dict[str, str]:
    return {
        "thermoengine": package_or_module_version("thermoengine"),
        "petthermotools": package_or_module_version("petthermotools"),
        "PetThermoTools": package_or_module_version("PetThermoTools"),
        "vaporock": package_or_module_version("vaporock"),
    }


def package_or_module_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        module = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001
        return f"unavailable:{type(exc).__name__}:{exc}"
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    return "installed:version_unknown"


def vaporock_version_label(ready: bool) -> str:
    return ("available:" if ready else "unavailable:") + package_or_module_version("vaporock")


def read_repo_version() -> str:
    version_path = REPO_ROOT / "VERSION"
    if version_path.exists():
        return version_path.read_text().strip()
    return "unknown"


def is_git_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:  # noqa: BLE001
        return True
    return bool(result.stdout.strip())


def make_cell_key(
    *,
    cell: Cell,
    backend: str,
    engine_versions: Mapping[str, str],
    repo_version: str,
    data_digests: Mapping[str, str],
) -> str:
    payload = cache_payload(
        cell=cell,
        species=None,
        backend=backend,
        engine_versions=engine_versions,
        repo_version=repo_version,
        data_digests=data_digests,
    )
    return sha256_canonical(payload)


def make_cache_key(
    *,
    cell: Cell,
    species: str,
    backend: str,
    engine_versions: Mapping[str, str],
    repo_version: str,
    data_digests: Mapping[str, str],
) -> str:
    payload = cache_payload(
        cell=cell,
        species=normalize_species_name(species),
        backend=backend,
        engine_versions=engine_versions,
        repo_version=repo_version,
        data_digests=data_digests,
    )
    return sha256_canonical(payload)


def cache_payload(
    *,
    cell: Cell,
    species: str | None,
    backend: str,
    engine_versions: Mapping[str, str],
    repo_version: str,
    data_digests: Mapping[str, str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "composition_digest": cell.composition_digest,
        "T_K": cell.t_k,
        "fO2_log10_bar": cell.fO2_log10_bar,
        "pressure_context": dict(cell.pressure_context),
        "backend": backend,
        "engine_versions": dict(engine_versions),
        "VERSION": repo_version,
        "data_digests": dict(data_digests),
    }
    if species is not None:
        payload["species"] = species
    return payload


def canonical_json(value: Any) -> str:
    return canonical_json_dumps(normalize_canonical_value(value))


def sha256_canonical(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_species_name(species: Any) -> str:
    value = str(species).strip()
    if not value:
        raise ValueError("empty species label")
    return value


def finite_positive(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0.0


def finite_pressure_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(number) and number > 0.0:
        return number
    return None


def linear_grid(start: float, end: float, count: int) -> list[float]:
    if count <= 1:
        return [float(end)]
    if end <= start:
        raise ValueError(f"invalid temperature range {start}..{end}")
    step = (end - start) / float(count - 1)
    return [start + step * i for i in range(count)]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def print_smoke_rows(rows: Sequence[sqlite3.Row]) -> None:
    by_species = {str(row["species"]): row for row in rows}
    for species in SMOKE_SPECIES:
        row = by_species.get(species)
        if row is None:
            print(f"smoke {species}: not stored", flush=True)
            continue
        print(
            "smoke {species}: vaporock_Pa={vaporock} "
            "antoine_Pa={antoine} log10_delta={delta} flags={flags}".format(
                species=species,
                vaporock=row["vaporock_Pa"],
                antoine=row["antoine_Pa"],
                delta=row["log10_delta"],
                flags=row["flags"],
            ),
            flush=True,
        )
    print(f"smoke rows landed={len(rows)}", flush=True)


def write_verdict(store: ShadowStore, path: Path) -> None:
    rows = store.verdict_rows()
    values_by_species: dict[str, list[float]] = {}
    flag_counts: Counter[str] = Counter()
    for row in rows:
        if row["log10_delta"] is not None:
            values_by_species.setdefault(str(row["species"]), []).append(
                float(row["log10_delta"])
            )
        for flag in json.loads(row["flags"] or "[]"):
            flag_counts[str(flag)] += 1

    lines = [
        "# VapoRock vs Antoine Shadow Verdict",
        "",
        f"Generated: {utc_now()}",
        "",
        "Report-only diagnostic. No pass/fail acceptance threshold is applied.",
        "",
        "| species | rows | median log10_delta | min | max |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for species in sorted(values_by_species):
        values = values_by_species[species]
        lines.append(
            f"| {species} | {len(values)} | {median(values):.6g} | "
            f"{min(values):.6g} | {max(values):.6g} |"
        )
    if not values_by_species:
        lines.append("| _none_ | 0 |  |  |  |")
    lines.extend(["", "## Flags", ""])
    if flag_counts:
        for flag, count in sorted(flag_counts.items()):
            lines.append(f"- {flag}: {count}")
    else:
        lines.append("- none")
    path.write_text("\n".join(lines) + "\n")


def write_build_summary(
    *,
    path: Path,
    db_path: Path,
    profiles: Sequence[Path],
    cells: Sequence[Cell],
    engine_versions: Mapping[str, str],
    smoke: bool,
    smoke_rows: Sequence[sqlite3.Row],
    skipped: int,
    solved: int,
    failures: int,
) -> None:
    schema = (
        "cache_key, species, cell_key, profile_id, composition_digest, T_K, "
        "fO2_log10_bar, pressure, pressure_context_json, backend, "
        "engine_versions_json, VERSION, data_digests_json, vaporock_Pa, "
        "antoine_Pa, log10_delta, flags, created_at, git_dirty"
    )
    lines = [
        "# VapoRock Antoine Shadow Matrix Build Summary",
        "",
        f"Generated: {utc_now()}",
        "",
        "Diagnostic-only recorder. It solves alphaMELTS melt states and records "
        "VapoRock vapor pressures beside alphaMELTS activity x Antoine fallback "
        "pressures. It does not touch AtomLedger or simulator inventory state.",
        "",
        f"DB: `{db_path.relative_to(REPO_ROOT)}`",
        f"Verdict: `{VERDICT_PATH.relative_to(REPO_ROOT)}`",
        f"Mode: `{'smoke' if smoke else 'matrix'}`",
        f"MAX_SOLVES: `{MAX_SOLVES}`",
        "",
        "## Chosen Profiles",
        "",
    ]
    for profile in profiles:
        lines.append(f"- `{profile.relative_to(REPO_ROOT)}`")
    lines.extend(
        [
            "",
            "## Cells",
            "",
            f"- queued: {len(cells)}",
            f"- solved: {solved}",
            f"- skipped_existing: {skipped}",
            f"- failed_recorded: {failures}",
            "",
            "## Engine Versions",
            "",
        ]
    )
    for key, value in sorted(engine_versions.items()):
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Schema", "", f"`{schema}`", "", "## Smoke Rows", ""])
    if smoke_rows:
        for row in smoke_rows:
            lines.append(
                "- {species}: vaporock_Pa={vaporock}, antoine_Pa={antoine}, "
                "log10_delta={delta}, flags={flags}".format(
                    species=row["species"],
                    vaporock=row["vaporock_Pa"],
                    antoine=row["antoine_Pa"],
                    delta=row["log10_delta"],
                    flags=row["flags"],
                )
            )
    else:
        lines.append("- none")
    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
