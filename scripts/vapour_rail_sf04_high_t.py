#!/usr/bin/env python3
"""Research-only VapoRock comparison against the SF04 MAGMA workbook grid.

This deliberately bypasses the runtime adapter's 1950 K domain gate. It is a
diagnostic probe for deciding that gate, never a runtime pressure source. The
SF04 paper prints no numerical high-temperature species table; the anchor is
the preserved ``Schaefer2004-MAGMA-valid.xlsx`` companion-workbook grid named
by the reviewed extract.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import csv
import hashlib
from importlib.machinery import ModuleSpec
import importlib.metadata
import importlib.util
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.yaml_cache import load_cached_safe_yaml  # noqa: E402

DEFAULT_EXTRACT = (
    ROOT
    / "data"
    / "literature"
    / "extracts"
    / "sf04-magma-companion-workbook.yaml"
)
DEFAULT_OUTPUT = (
    ROOT / "validation-data" / "vapour_rail_sf04_high_t_residuals.csv"
)
DEFAULT_TEMPERATURES_K = (1900.0, 2000.0, 2100.0, 2200.0, 2300.0, 2473.0)
DEFAULT_SPECIES = ("SiO", "Fe", "Mg", "Na", "K", "O", "O2")
DEFAULT_RESIDUAL_THRESHOLD_DEX = 0.5

_VAPOROCK_DIGEST_FILES = (
    ("vaporock_equil_py_sha256", "equil.py"),
    ("vaporock_chemistry_py_sha256", "chemistry.py"),
    (
        "vaporock_janaf_vapor_data_full_csv_sha256",
        "data/JANAF-vapor-data-full.csv",
    ),
    (
        "vaporock_janaf_vapor_data_csv_sha256",
        "data/JANAF-vapor-data.csv",
    ),
    (
        "vaporock_janaf0_vapor_data_csv_sha256",
        "data/JANAF0-vapor-data.csv",
    ),
)
# The SF04/MAGMA tholeiite rows are initial saturated-vapour states.  Summing
# all 32 species in the pinned workbook gives 7.241957794e-5 bar at 1900 K
# and 1.329977055e-2 bar at 2500 K.  The bracket below rounds that upper state
# up to 2e-2 bar and retains VapoRock's 1e-10 bar vacuum limit at the low end.
# The reviewed probe evaluates both endpoints; the recorded primary result is
# the low-pressure endpoint only after the pressure-insensitivity check.
PRESSURE_SENSITIVITY_BRACKET_BAR = (1.0e-10, 2.0e-2)
VAPOROCK_PRESSURE_BAR = PRESSURE_SENSITIVITY_BRACKET_BAR[0]


class VapoRockInputMutationError(RuntimeError):
    """Raised when VapoRock inputs change during an evaluation sweep."""


class VapoRockProvenanceError(ValueError):
    """Raised when a residual row lacks validated VapoRock provenance."""


def _vaporock_input_digests(source_directory: Path) -> dict[str, str]:
    return {
        field: hashlib.sha256(
            (source_directory / relative).read_bytes()
        ).hexdigest()
        for field, relative in _VAPOROCK_DIGEST_FILES
    }


def _vaporock_checkout_identity(source_path: Path) -> dict[str, str]:
    commit = "unavailable"
    checkout_state = "unavailable"
    for parent in source_path.parents:
        if not (parent / ".git").exists():
            continue
        head_before = subprocess.run(
            ["git", "-C", str(parent), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        commit_before = (
            head_before.stdout.strip()
            if head_before.returncode == 0 and head_before.stdout.strip()
            else None
        )
        status = subprocess.run(
            [
                "git",
                "-C",
                str(parent),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        head_after = subprocess.run(
            ["git", "-C", str(parent), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        commit_after = (
            head_after.stdout.strip()
            if head_after.returncode == 0 and head_after.stdout.strip()
            else None
        )
        if commit_before != commit_after:
            raise VapoRockProvenanceError(
                "VapoRock checkout identity changed while sampling checkout state"
            )
        if commit_before is not None:
            commit = commit_before
        if status.returncode == 0:
            checkout_state = "dirty" if status.stdout.strip() else "clean"
        break
    return {
        "vaporock_commit": commit,
        "vaporock_checkout_state": checkout_state,
    }


def load_extract(path: Path = DEFAULT_EXTRACT) -> dict[str, Any]:
    document = load_cached_safe_yaml(path.read_text(encoding="utf-8")) or {}
    if document.get("source_id") != "sf04-magma-companion-workbook":
        raise ValueError(f"unexpected SF04 extract at {path}")
    return document


def pressure_series_by_species(
    document: Mapping[str, Any],
    species: Sequence[str] = DEFAULT_SPECIES,
) -> dict[str, list[dict[str, float]]]:
    result: dict[str, list[dict[str, float]]] = {}
    species_blocks = document.get("species") or {}
    for species_id in species:
        observations = (species_blocks.get(species_id) or {}).get("observations") or []
        matches = [obs for obs in observations if obs.get("type") == "psat_series"]
        if len(matches) != 1:
            raise ValueError(
                f"expected one psat_series for {species_id}, found {len(matches)}"
            )
        rows = [
            {
                "T_K": float(row["T_K"]),
                "pressure_bar": float(row["pressure_bar"]),
                "log10_pressure_bar": float(row["log10_pressure_bar"]),
            }
            for row in matches[0]["values"]
        ]
        if rows != sorted(rows, key=lambda row: row["T_K"]):
            raise ValueError(f"SF04 {species_id} temperature series is not sorted")
        result[species_id] = rows
    return result


def interpolate_log10_pressure(
    series: Sequence[Mapping[str, float]],
    temperature_K: float,
) -> tuple[float, str, tuple[float, float]]:
    """Interpolate log10(P_bar) linearly in reciprocal temperature.

    The paper's Table 7 uses ``log10(P_bar) = A + B/T`` for total pressure;
    applying the same local coordinate to adjacent species-grid points avoids
    inventing a global fit. Extrapolation is refused.
    """

    target = float(temperature_K)
    points = sorted(
        (
            float(row["T_K"]),
            float(row["log10_pressure_bar"]),
        )
        for row in series
    )
    if not points or not math.isfinite(target):
        raise ValueError("temperature and SF04 series must be finite and non-empty")
    for point_t, point_logp in points:
        if math.isclose(target, point_t, rel_tol=0.0, abs_tol=1.0e-9):
            return point_logp, "workbook_exact", (point_t, point_t)
    if target < points[0][0] or target > points[-1][0]:
        raise ValueError(
            f"T={target:g} K outside SF04 workbook grid "
            f"[{points[0][0]:g}, {points[-1][0]:g}] K"
        )
    for (low_t, low_logp), (high_t, high_logp) in zip(points, points[1:]):
        if low_t < target < high_t:
            low_x = 1.0 / low_t
            high_x = 1.0 / high_t
            target_x = 1.0 / target
            weight = (target_x - low_x) / (high_x - low_x)
            value = low_logp + weight * (high_logp - low_logp)
            return value, "reciprocal_T_interpolation", (low_t, high_t)
    raise AssertionError(f"failed to bracket T={target:g} K")


def evaluate_vaporock(
    *,
    composition_wt_pct: Mapping[str, float],
    temperatures_K: Sequence[float],
    oxygen_series: Sequence[Mapping[str, float]],
    species: Sequence[str] = DEFAULT_SPECIES,
) -> tuple[
    dict[float, dict[str, float]],
    dict[str, str],
    dict[str, float],
]:
    """Evaluate VapoRock with MAGMA O2 and bound its pressure sensitivity.

    The copied snapshot isolates evaluation from ordinary concurrent edits to
    the source checkout and records a digest for each copied file. ThermoEngine
    code and model inputs remain outside the snapshot boundary. This is not a
    defence against an actor with write access to the process's temporary
    directory.
    """

    spec = importlib.util.find_spec("vaporock")
    if spec is None or spec.origin is None:
        raise ImportError("cannot resolve the installed VapoRock source directory")
    source_directory = Path(spec.origin).resolve().parent
    source_path = Path(spec.origin).resolve()
    pre_snapshot_identity = _vaporock_checkout_identity(source_path)
    source_input_digests = _vaporock_input_digests(source_directory)

    with tempfile.TemporaryDirectory(
        prefix="vapour-rail-vaporock-snapshot-"
    ) as temporary_directory:
        snapshot_directory = Path(temporary_directory)
        for _, relative in _VAPOROCK_DIGEST_FILES:
            snapshot_path = snapshot_directory / relative
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_directory / relative, snapshot_path)
        input_digests = _vaporock_input_digests(snapshot_directory)
        copied_input_mismatches = [
            field
            for field, digest in source_input_digests.items()
            if input_digests[field] != digest
        ]
        if copied_input_mismatches:
            raise VapoRockProvenanceError(
                "copied VapoRock inputs differ from the pre-copy source "
                "vector: " + ", ".join(copied_input_mismatches)
            )
        cut_source_digests = _vaporock_input_digests(source_directory)
        changed_source_inputs = [
            field
            for field, digest in source_input_digests.items()
            if cut_source_digests[field] != digest
        ]
        if changed_source_inputs:
            raise VapoRockProvenanceError(
                "VapoRock source inputs changed while snapshotting: "
                + ", ".join(changed_source_inputs)
            )
        checkout_identity = _vaporock_checkout_identity(source_path)
        if (
            checkout_identity["vaporock_commit"]
            != pre_snapshot_identity["vaporock_commit"]
        ):
            raise VapoRockProvenanceError(
                "VapoRock checkout identity changed while snapshotting inputs"
            )
        for _, relative in _VAPOROCK_DIGEST_FILES:
            (snapshot_directory / relative).chmod(0o444)

        package_name = "_vapour_rail_sf04_snapshot"
        module_name = f"{package_name}.equil"
        chemistry_module_name = f"{package_name}.chemistry"
        snapshot_package = ModuleType(package_name)
        snapshot_package.__path__ = [str(snapshot_directory)]
        snapshot_package.__package__ = package_name
        snapshot_package.__spec__ = ModuleSpec(
            package_name,
            loader=None,
            is_package=True,
        )
        snapshot_spec = importlib.util.spec_from_file_location(
            module_name,
            snapshot_directory / "equil.py",
        )
        if snapshot_spec is None or snapshot_spec.loader is None:
            raise ImportError("cannot load the snapshotted VapoRock equil.py")
        snapshot_equil = importlib.util.module_from_spec(snapshot_spec)
        snapshot_module_names = (
            package_name,
            chemistry_module_name,
            module_name,
        )
        previous_modules = {
            name: sys.modules.get(name) for name in snapshot_module_names
        }
        sys.modules[package_name] = snapshot_package
        sys.modules[module_name] = snapshot_equil
        try:
            try:
                snapshot_spec.loader.exec_module(snapshot_equil)
            except PermissionError as exc:
                raise VapoRockInputMutationError(
                    "VapoRock attempted to mutate its read-only input snapshot"
                ) from exc
        finally:
            for name in reversed(snapshot_module_names):
                previous_module = previous_modules[name]
                if previous_module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous_module

        def snapshot_resource_path(package: str, resource: str) -> Any:
            if package != "vaporock.data":
                raise ValueError(f"unexpected VapoRock resource package: {package}")
            return nullcontext(snapshot_directory / "data" / resource)

        snapshot_equil.resources = SimpleNamespace(path=snapshot_resource_path)
        try:
            system = snapshot_equil.System(vapor_database="JANAF")
            system.set_melt_comp(
                {oxide: float(value) for oxide, value in composition_wt_pct.items()}
            )
        except PermissionError as exc:
            raise VapoRockInputMutationError(
                "VapoRock attempted to mutate its read-only input snapshot"
            ) from exc
        evaluated_by_pressure: dict[float, dict[float, dict[str, float]]] = {}
        for pressure_bar in PRESSURE_SENSITIVITY_BRACKET_BAR:
            evaluated: dict[float, dict[str, float]] = {}
            for temperature_K in temperatures_K:
                logfO2, _, _ = interpolate_log10_pressure(
                    oxygen_series, float(temperature_K)
                )
                try:
                    table = system.eval_gas_abundances(
                        float(temperature_K), logfO2, P=pressure_bar
                    )
                except PermissionError as exc:
                    raise VapoRockInputMutationError(
                        "VapoRock attempted to mutate its read-only input snapshot"
                    ) from exc
                row: dict[str, float] = {}
                for species_id in species:
                    value = float(table.loc[f"{species_id}(g)"].iloc[0])
                    if not math.isfinite(value):
                        raise ValueError(
                            f"VapoRock returned non-finite {species_id} at "
                            f"{temperature_K:g} K"
                        )
                    row[species_id] = value
                evaluated[float(temperature_K)] = row
            evaluated_by_pressure[pressure_bar] = evaluated
        low_pressure, high_pressure = PRESSURE_SENSITIVITY_BRACKET_BAR
        pressure_sensitivity = {
            species_id: max(
                abs(
                    evaluated_by_pressure[high_pressure][float(temperature_K)][
                        species_id
                    ]
                    - evaluated_by_pressure[low_pressure][float(temperature_K)][
                        species_id
                    ]
                )
                for temperature_K in temperatures_K
            )
            for species_id in species
        }
        post_evaluation_identity = _vaporock_checkout_identity(source_path)
        if (
            post_evaluation_identity["vaporock_commit"]
            != checkout_identity["vaporock_commit"]
        ):
            raise VapoRockProvenanceError(
                "VapoRock checkout identity changed during evaluation"
            )
        try:
            post_evaluation_digests = _vaporock_input_digests(snapshot_directory)
        except OSError as exc:
            raise VapoRockInputMutationError(
                "a snapshotted VapoRock input became unreadable during evaluation"
            ) from exc
        changed_inputs = [
            field
            for field, digest in input_digests.items()
            if post_evaluation_digests[field] != digest
        ]
        if changed_inputs:
            raise VapoRockInputMutationError(
                "snapshotted VapoRock inputs changed during VapoRock evaluation: "
                + ", ".join(changed_inputs)
            )
    try:
        version = importlib.metadata.version("VapoRock")
    except importlib.metadata.PackageNotFoundError:
        version = "unavailable"
    provenance = {
        "vapor_database": "JANAF",
        "vaporock_version": version,
        **checkout_identity,
        **input_digests,
    }
    return evaluated_by_pressure[VAPOROCK_PRESSURE_BAR], provenance, pressure_sensitivity


def compute_residual_rows(
    *,
    anchor_series: Mapping[str, Sequence[Mapping[str, float]]],
    model_log10_pressure_bar: Mapping[float, Mapping[str, float]],
    temperatures_K: Sequence[float] = DEFAULT_TEMPERATURES_K,
    species: Sequence[str] = DEFAULT_SPECIES,
    threshold_dex: float = DEFAULT_RESIDUAL_THRESHOLD_DEX,
    pressure_sensitivity_by_species: Mapping[str, float],
    provenance: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return signed residuals, defined as model minus workbook anchor."""

    if not math.isfinite(threshold_dex) or threshold_dex < 0.0:
        raise ValueError("threshold_dex must be finite and non-negative")
    run_provenance = {
        "vapor_database": "not_recorded",
        "vaporock_version": "not_recorded",
        "vaporock_commit": "not_recorded",
        "vaporock_checkout_state": "not_recorded",
        **{field: "not_recorded" for field, _ in _VAPOROCK_DIGEST_FILES},
        **dict(provenance or {}),
    }
    rows: list[dict[str, Any]] = []
    for temperature_K in temperatures_K:
        model_row = model_log10_pressure_bar[float(temperature_K)]
        for species_id in species:
            anchor, method, bracket = interpolate_log10_pressure(
                anchor_series[species_id], float(temperature_K)
            )
            model = float(model_row[species_id])
            residual = model - anchor
            recommendation_evidence = species_id not in {"O", "O2"}
            rows.append(
                {
                    "temperature_K": float(temperature_K),
                    "species": species_id,
                    "log10_pressure_sf04_workbook_bar": anchor,
                    "log10_pressure_vaporock_bar": model,
                    "delta_log10_pressure_dex": residual,
                    "threshold_dex": float(threshold_dex),
                    "within_threshold": (
                        abs(residual) <= threshold_dex
                        if recommendation_evidence
                        else None
                    ),
                    "recommendation_evidence": recommendation_evidence,
                    "evidence_role": (
                        "model_anchor_residual"
                        if recommendation_evidence
                        else (
                            "gas_equilibrium_consistency"
                            if species_id == "O"
                            else "fO2_pinned_identity"
                        )
                    ),
                    "vaporock_pressure_bar": VAPOROCK_PRESSURE_BAR,
                    "pressure_sensitivity_bracket_low_bar": (
                        PRESSURE_SENSITIVITY_BRACKET_BAR[0]
                    ),
                    "pressure_sensitivity_bracket_high_bar": (
                        PRESSURE_SENSITIVITY_BRACKET_BAR[1]
                    ),
                    "max_abs_delta_delta_log10_pressure_dex": float(
                        pressure_sensitivity_by_species[species_id]
                    ),
                    **run_provenance,
                    "anchor_method": method,
                    "anchor_bracket_low_K": bracket[0],
                    "anchor_bracket_high_K": bracket[1],
                }
            )
    return rows


def _parse_csv_boolean(
    value: Any,
    *,
    field: str,
) -> bool:
    if isinstance(value, bool):
        return value
    if value == "True":
        return True
    if value == "False":
        return False
    raise VapoRockProvenanceError(f"{field} must be exactly True or False")


def _require_hex_field(
    row: Mapping[str, Any],
    *,
    field: str,
    length: int,
) -> None:
    value = row.get(field)
    valid = (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value.lower())
    )
    if not valid:
        raise VapoRockProvenanceError(
            f"{field} must be exactly {length} hexadecimal characters"
        )


def write_csv(
    rows: Sequence[Mapping[str, Any]],
    output: Path,
    *,
    allow_dirty_source: bool = False,
    legacy_input: bool = False,
) -> None:
    """Write rows with ``legacy_input`` reserved for historical replay.

    The mode exists solely to replay the pre-existing artifact and must not be
    used to write new evidence.
    """
    normalized_rows = [dict(row) for row in rows]
    if not isinstance(legacy_input, bool):
        raise VapoRockProvenanceError("legacy_input must be exactly True or False")
    chemistry_digest_field = "vaporock_chemistry_py_sha256"
    if legacy_input and any(
        chemistry_digest_field in row for row in normalized_rows
    ):
        raise VapoRockProvenanceError(
            "legacy_input requires every row to omit " + chemistry_digest_field
        )
    for row in normalized_rows:
        checkout_state = row.get("vaporock_checkout_state")
        if not isinstance(checkout_state, str) or checkout_state not in {
            "clean",
            "dirty",
        }:
            raise VapoRockProvenanceError(
                "vaporock_checkout_state must be exactly 'clean' or 'dirty'"
            )
        _require_hex_field(row, field="vaporock_commit", length=40)
        for field, _ in _VAPOROCK_DIGEST_FILES:
            if legacy_input and field == chemistry_digest_field:
                continue
            _require_hex_field(row, field=field, length=64)
        row["recommendation_evidence"] = _parse_csv_boolean(
            row.get("recommendation_evidence"),
            field="recommendation_evidence",
        )
        if row["recommendation_evidence"]:
            row["within_threshold"] = _parse_csv_boolean(
                row.get("within_threshold"),
                field="within_threshold",
            )
        else:
            within_threshold = row.get("within_threshold")
            if within_threshold is not None and not (
                isinstance(within_threshold, str) and within_threshold == ""
            ):
                raise VapoRockProvenanceError(
                    "within_threshold must be empty when recommendation_evidence "
                    "is False"
                )
            row["within_threshold"] = None
    dirty_evidence = any(
        row.get("recommendation_evidence") is True
        and row.get("vaporock_checkout_state") == "dirty"
        for row in normalized_rows
    )
    if dirty_evidence and not allow_dirty_source:
        raise ValueError(
            "refusing recommendation evidence from a dirty VapoRock checkout; "
            "pass --allow-dirty-source to record the exact source digests"
        )
    fieldnames = list(normalized_rows[0]) if normalized_rows else []
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(normalized_rows)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", type=Path, default=DEFAULT_EXTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--temperatures-K",
        nargs="+",
        type=float,
        default=list(DEFAULT_TEMPERATURES_K),
    )
    parser.add_argument(
        "--threshold-dex",
        type=float,
        default=DEFAULT_RESIDUAL_THRESHOLD_DEX,
    )
    parser.add_argument(
        "--allow-dirty-source",
        action="store_true",
        help=(
            "allow recommendation evidence from a dirty VapoRock checkout; "
            "all rows still record exact source digests"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    document = load_extract(args.extract)
    series = pressure_series_by_species(document)
    model, provenance, pressure_sensitivity = evaluate_vaporock(
        composition_wt_pct=document["composition_wt_pct"],
        temperatures_K=args.temperatures_K,
        oxygen_series=series["O2"],
    )
    rows = compute_residual_rows(
        anchor_series=series,
        model_log10_pressure_bar=model,
        temperatures_K=args.temperatures_K,
        threshold_dex=float(args.threshold_dex),
        pressure_sensitivity_by_species=pressure_sensitivity,
        provenance=provenance,
    )
    write_csv(
        rows,
        args.output,
        allow_dirty_source=bool(args.allow_dirty_source),
    )
    print(f"wrote {len(rows)} residual rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
