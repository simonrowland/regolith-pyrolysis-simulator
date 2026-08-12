#!/usr/bin/env python3
"""Prove t-609 additivity against the actual cf4a499 compiler and catalog.

The baseline and candidate are compiled in isolated Python processes. The
baseline process imports from an immutable Git archive (or a verified clean
detached checkout), so this proof cannot exercise the candidate compiler twice.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import CodeType
from typing import Any

import yaml


BASE_REVISION = "cf4a499dff2beee6741f1b4da6fa43b61b6ecaa2"
EXPECTED_ADDITIONS = ("FeO_association_gas", "NiO_gas")
PO2_GRID_BAR = (1.0e-30, 1.0e-9, 1.0, 100.0)
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = (
    ROOT / "validation-data" / "pin-evidence" / "t609_additivity_2026-08-11.yaml"
)


class ProofFailure(RuntimeError):
    """Raised when candidate output is not a strict additive extension."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_code(code: CodeType) -> dict[str, Any]:
    """Fingerprint executable semantics without path/line-number noise."""

    return {
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "nlocals": code.co_nlocals,
        "stacksize": code.co_stacksize,
        "flags": code.co_flags,
        "bytecode": code.co_code.hex(),
        "constants": _canonical(code.co_consts),
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
    }


def _canonical(value: Any) -> Any:
    """Return an exact, JSON-safe representation of compiled values."""

    if isinstance(value, Enum):
        cls = type(value)
        return {
            "__enum__": f"{cls.__module__}.{cls.__qualname__}",
            "value": _canonical(value.value),
        }
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return {"__float_hex__": value.hex()}
    if isinstance(value, CodeType):
        return {"__code__": _canonical_code(value)}
    if is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        return {
            "__dataclass__": f"{cls.__module__}.{cls.__qualname__}",
            "fields": {
                field.name: _canonical(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if isinstance(value, Mapping):
        return {
            "__mapping__": [
                [_canonical(key), _canonical(item)]
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            ]
        }
    if isinstance(value, tuple):
        return {"__tuple__": [_canonical(item) for item in value]}
    if isinstance(value, list):
        return {"__list__": [_canonical(item) for item in value]}
    if isinstance(value, (set, frozenset)):
        items = [_canonical(item) for item in value]
        items.sort(key=_canonical_json)
        return {"__set__": items, "frozen": isinstance(value, frozenset)}
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if isinstance(value, Path):
        return {"__path__": str(value)}
    if inspect.isfunction(value):
        closure = []
        for cell in value.__closure__ or ():
            try:
                closure.append(_canonical(cell.cell_contents))
            except ValueError:
                closure.append({"__empty_cell__": True})
        return {
            "__function__": f"{value.__module__}.{value.__qualname__}",
            "code": _canonical_code(value.__code__),
            "defaults": _canonical(value.__defaults__),
            "kwdefaults": _canonical(value.__kwdefaults__),
            "closure": closure,
        }
    raise TypeError(
        f"unsupported compiled value {type(value).__module__}."
        f"{type(value).__qualname__}"
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _evaluation_or_exception(
    evaluator: Any, temperature_K: float, pO2_bar: float
) -> dict[str, Any]:
    try:
        result = evaluator.evaluate(
            temperature_K,
            source_activity=1.0,
            pO2_bar=pO2_bar,
        )
    except Exception as exc:  # Exact exception type and args are proof payload.
        cls = type(exc)
        return {
            "kind": "exception",
            "type": f"{cls.__module__}.{cls.__qualname__}",
            "args": _canonical(exc.args),
        }
    return {"kind": "evaluation", "value": _canonical(result)}


def _t583_receipt(payload: Mapping[str, Any], catalog: Any) -> dict[str, Any]:
    status_species: list[str] = []
    status_ledger_pairs = 0
    for family in payload["families"].values():
        metadata = family.get("code_metadata", {})
        if metadata.get("t583_status_only_composed") is not True:
            continue
        for species_id, row in family["physical_properties"]["species"].items():
            status_species.append(str(species_id))
            coverage = row.get("t583_composition", row)
            status_ledger_pairs += int(coverage["coverage_ledger_pair_count"])

    existing_species: list[str] = []
    existing_ledger_pairs = 0
    for species_id, species in catalog.species.items():
        receipt = species.code_metadata.raw.get("t583_existing_executable_composed")
        if not isinstance(receipt, Mapping):
            continue
        existing_species.append(str(species_id))
        existing_ledger_pairs += int(receipt["coverage_ledger_pair_count"])

    return {
        "status_only_species": sorted(status_species),
        "status_only_ledger_pair_compositions": status_ledger_pairs,
        "existing_executable_species": sorted(existing_species),
        "existing_executable_ledger_pair_compositions": existing_ledger_pairs,
        "total_compositions": status_ledger_pairs + existing_ledger_pairs,
    }


def _build_snapshot(import_root: Path) -> dict[str, Any]:
    root_text = str(import_root.resolve())
    sys.path.insert(0, root_text)

    from simulator.vapour_rail import catalog as catalog_module

    imported_catalog = Path(catalog_module.__file__).resolve()
    expected_catalog = (
        import_root.resolve() / "simulator" / "vapour_rail" / "catalog.py"
    )
    if imported_catalog != expected_catalog:
        raise ProofFailure(
            "compiler import escaped requested root: "
            f"{imported_catalog} != {expected_catalog}"
        )

    payload_path = import_root / "data" / "vapor_pressures.yaml"
    payload = yaml.safe_load(payload_path.read_text(encoding="utf-8"))
    catalog = catalog_module.compile_vapour_rail_catalog(
        payload, emit_u0_request_rules=False
    )

    compiled: dict[str, Any] = {}
    evaluations: dict[str, Any] = {}
    evaluation_count = 0
    exception_count = 0
    for species_id in sorted(catalog.species):
        species = catalog.species[species_id]
        compiled[species_id] = _canonical(species)
        evaluator = species.evaluator
        if evaluator is None:
            evaluations[species_id] = []
            continue
        low, high = evaluator.valid_temperature_K
        temperatures = (
            ("low", low),
            ("mid", 0.5 * (low + high)),
            ("high", high),
        )
        cases = []
        for temperature_label, temperature_K in temperatures:
            for pO2_bar in PO2_GRID_BAR:
                outcome = _evaluation_or_exception(evaluator, temperature_K, pO2_bar)
                cases.append(
                    {
                        "temperature_position": temperature_label,
                        "temperature_K": _canonical(temperature_K),
                        "pO2_bar": _canonical(pO2_bar),
                        "outcome": outcome,
                    }
                )
                evaluation_count += 1
                exception_count += outcome["kind"] == "exception"
        evaluations[species_id] = cases

    return {
        "catalog_sha256": _sha256_file(payload_path),
        "compiler_sha256": _sha256_file(imported_catalog),
        "compiled_species": compiled,
        "evaluations": evaluations,
        "counts": {
            "compiled_species": len(compiled),
            "evaluator_species": sum(bool(cases) for cases in evaluations.values()),
            "evaluation_cases": evaluation_count,
            "exceptions": exception_count,
        },
        "t583": _t583_receipt(payload, catalog),
    }


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_worker(import_root: Path, snapshot_path: Path) -> dict[str, Any]:
    _run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            "--worker-root",
            str(import_root),
            "--worker-output",
            str(snapshot_path),
        ],
        cwd=import_root,
    )
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


def _assert_equal(label: str, baseline: Any, candidate: Any) -> None:
    if baseline != candidate:
        raise ProofFailure(
            f"{label} differs: baseline {_digest(baseline)}, "
            f"candidate {_digest(candidate)}"
        )


def _generate_evidence(
    candidate_root: Path, baseline_root: Path | None = None
) -> dict[str, Any]:
    candidate_root = candidate_root.resolve()
    base_commit = _run(
        ["git", "rev-parse", f"{BASE_REVISION}^{{commit}}"], cwd=candidate_root
    ).stdout.strip()

    with tempfile.TemporaryDirectory(prefix="t609-additivity-") as temp_text:
        temp_root = Path(temp_text)
        if baseline_root is not None:
            baseline_root = baseline_root.resolve()
            baseline_commit = _run(
                ["git", "rev-parse", "HEAD"], cwd=baseline_root
            ).stdout.strip()
            baseline_branch = _run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=baseline_root
            ).stdout.strip()
            baseline_status = _run(
                ["git", "status", "--porcelain"], cwd=baseline_root
            ).stdout.strip()
            if baseline_commit != base_commit:
                raise ProofFailure(
                    f"baseline root is {baseline_commit}, expected {base_commit}"
                )
            if baseline_branch != "HEAD" or baseline_status:
                raise ProofFailure(
                    "baseline root must be a clean detached checkout"
                )
            baseline = _run_worker(baseline_root, temp_root / "baseline.json")
            candidate = _run_worker(candidate_root, temp_root / "candidate.json")
        else:
            temporary_baseline_root = temp_root / "baseline"
            temporary_baseline_root.mkdir()
            baseline_archive = temp_root / "baseline.tar"
            _run(
                [
                    "git",
                    "archive",
                    "--format=tar",
                    "--output",
                    str(baseline_archive),
                    base_commit,
                ],
                cwd=candidate_root,
            )
            with tarfile.open(baseline_archive, mode="r") as archive:
                archive.extractall(temporary_baseline_root, filter="data")
            baseline = _run_worker(
                temporary_baseline_root, temp_root / "baseline.json"
            )
            candidate = _run_worker(candidate_root, temp_root / "candidate.json")

    baseline_ids = set(baseline["compiled_species"])
    candidate_ids = set(candidate["compiled_species"])
    additions = sorted(candidate_ids - baseline_ids)
    removals = sorted(baseline_ids - candidate_ids)
    if additions != list(EXPECTED_ADDITIONS) or removals:
        raise ProofFailure(
            f"species delta is additions={additions}, removals={removals}; expected "
            f"additions={list(EXPECTED_ADDITIONS)}, removals=[]"
        )

    for species_id in sorted(baseline_ids):
        _assert_equal(
            f"compiled dataclass for {species_id}",
            baseline["compiled_species"][species_id],
            candidate["compiled_species"][species_id],
        )
        _assert_equal(
            f"evaluation grid for {species_id}",
            baseline["evaluations"][species_id],
            candidate["evaluations"][species_id],
        )

    _assert_equal("t-583 composition receipt", baseline["t583"], candidate["t583"])
    t583 = baseline["t583"]
    if (
        len(t583["status_only_species"]) != 151
        or t583["status_only_ledger_pair_compositions"] != 250
        or t583["existing_executable_species"] != ["H2S"]
        or t583["existing_executable_ledger_pair_compositions"] != 1
        or t583["total_compositions"] != 251
    ):
        raise ProofFailure(f"unexpected t-583 receipt: {t583}")

    baseline_compiled = baseline["compiled_species"]
    candidate_preexisting_compiled = {
        species_id: candidate["compiled_species"][species_id]
        for species_id in sorted(baseline_ids)
    }
    baseline_evaluations = baseline["evaluations"]
    candidate_preexisting_evaluations = {
        species_id: candidate["evaluations"][species_id]
        for species_id in sorted(baseline_ids)
    }
    added_digests = {
        species_id: {
            "compiled_dataclass_sha256": _digest(
                candidate["compiled_species"][species_id]
            ),
            "evaluation_grid_sha256": _digest(candidate["evaluations"][species_id]),
        }
        for species_id in EXPECTED_ADDITIONS
    }

    return {
        "schema_version": 1,
        "proof_id": "t609_cross_revision_additivity_2026-08-11",
        "generated_by": "scripts/prove_t609_cross_revision_additivity.py",
        "result": "pass",
        "method": {
            "baseline_revision": base_commit,
            "baseline_materialization": (
                "immutable_git_export_or_verified_clean_detached_checkout"
            ),
            "isolated_python_imports": True,
            "compiler_common_mode": False,
            "source_activity": 1.0,
            "temperature_positions": ["low", "mid", "high"],
            "pO2_grid_bar": list(PO2_GRID_BAR),
            "comparison": (
                "exact canonical dataclass values and exception/result values"
            ),
            "float_encoding": "IEEE-754 float.hex",
        },
        "source_digests": {
            "baseline_catalog_sha256": baseline["catalog_sha256"],
            "baseline_compiler_sha256": baseline["compiler_sha256"],
            "candidate_catalog_sha256": candidate["catalog_sha256"],
            "candidate_compiler_sha256": candidate["compiler_sha256"],
        },
        "species_delta": {
            "baseline_compiled_species": baseline["counts"]["compiled_species"],
            "candidate_compiled_species": candidate["counts"]["compiled_species"],
            "additions": additions,
            "removals": removals,
        },
        "preexisting_equivalence": {
            "compiled_species_compared": len(baseline_ids),
            "evaluator_species_compared": baseline["counts"]["evaluator_species"],
            "evaluation_cases_compared": baseline["counts"]["evaluation_cases"],
            "baseline_exceptions": baseline["counts"]["exceptions"],
            "candidate_exceptions": sum(
                case["outcome"]["kind"] == "exception"
                for cases in candidate_preexisting_evaluations.values()
                for case in cases
            ),
            "compiled_dataclasses_exact": True,
            "evaluation_results_and_exceptions_exact": True,
            "baseline_compiled_dataclasses_sha256": _digest(baseline_compiled),
            "candidate_compiled_dataclasses_sha256": _digest(
                candidate_preexisting_compiled
            ),
            "baseline_evaluation_grid_sha256": _digest(baseline_evaluations),
            "candidate_evaluation_grid_sha256": _digest(
                candidate_preexisting_evaluations
            ),
        },
        "t583_coverage": {
            "status_only_distinct_species": len(t583["status_only_species"]),
            "status_only_ledger_pair_compositions": t583[
                "status_only_ledger_pair_compositions"
            ],
            "existing_executable_species": t583["existing_executable_species"],
            "existing_executable_ledger_pair_compositions": t583[
                "existing_executable_ledger_pair_compositions"
            ],
            "total_t583_compositions_covered": t583["total_compositions"],
        },
        "added_species_digests": added_digests,
    }


def _render_evidence(evidence: Mapping[str, Any]) -> str:
    return yaml.safe_dump(
        dict(evidence),
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, default=ROOT)
    parser.add_argument(
        "--baseline-root",
        type=Path,
        help="optional clean detached checkout of the pinned baseline revision",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--worker-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker_root is not None:
        if args.worker_output is None:
            parser.error("--worker-root requires --worker-output")
        snapshot = _build_snapshot(args.worker_root)
        args.worker_output.write_text(
            _canonical_json(snapshot) + "\n", encoding="utf-8"
        )
        return 0

    evidence = _generate_evidence(args.candidate_root, args.baseline_root)
    rendered = _render_evidence(evidence)
    if args.check:
        if (
            not args.output.is_file()
            or args.output.read_text(encoding="utf-8") != rendered
        ):
            raise ProofFailure(f"evidence is stale: regenerate {args.output}")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")

    equivalence = evidence["preexisting_equivalence"]
    print(
        "PASS: "
        f"{equivalence['compiled_species_compared']} pre-existing species, "
        f"{equivalence['evaluation_cases_compared']} exact grid cases, "
        f"{evidence['t583_coverage']['total_t583_compositions_covered']} "
        "t-583 compositions covered"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except (ProofFailure, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
