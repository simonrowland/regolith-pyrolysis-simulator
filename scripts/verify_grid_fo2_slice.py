#!/usr/bin/env python3
"""Go/no-go check: eight materialized fO2 keys and live engine echoes."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.grid_pregrind import build_grid_points, point_inputs
from scripts.grid_pregrind_writer import GridCacheWriter
from simulator.melt_backend.alphamelts import AlphaMELTSBackend


FO2_LEVELS = tuple(float(value) for value in range(-12, -4))
SLICE_COMPOSITION_WT_PCT = {
    "SiO2": 45.0,
    "TiO2": 0.0,
    "Al2O3": 15.0,
    "FeO": 10.0,
    "Fe2O3": 5.0,
    "MgO": 10.0,
    "CaO": 10.0,
    "Na2O": 5.0,
    "K2O": 0.0,
    "Cr2O3": 0.0,
    "MnO": 0.0,
    "P2O5": 0.0,
    "NiO": 0.0,
    "CoO": 0.0,
}


def verify_slice(
    *,
    temperature_C: float,
    timeout_s: float,
    binary: Path | None = None,
) -> dict[str, object]:
    _total, points = build_grid_points(
        [SLICE_COMPOSITION_WT_PCT],
        [float(temperature_C)],
        FO2_LEVELS,
        seed=178,
    )
    point_args = SimpleNamespace(
        model="MELTSv1.0.2",
        timeout_s=float(timeout_s),
        thermoengine_health_timeout_s=8.0,
    )
    backend = AlphaMELTSBackend()
    if binary is not None:
        if not binary.is_file():
            raise RuntimeError(f"alphaMELTS binary not found: {binary}")
        backend._mode = "subprocess"
        backend._binary_path = binary
        backend._engine_path = binary
        backend._timeout_s = point_args.timeout_s
    else:
        if not backend.initialize({
            "mode": "subprocess",
            "model": point_args.model,
            "timeout_s": point_args.timeout_s,
        }):
            raise RuntimeError("alphaMELTS subprocess unavailable")

    with tempfile.TemporaryDirectory() as tmpdir:
        database = Path(tmpdir) / "epoch3-verification.db"
        with GridCacheWriter(database, engine_epoch=3) as writer:
            batch_id = writer.ensure_batch(
                label="epoch3-verification-slice",
                kind="fixed",
                seed=178,
                params={"engine_fO2_constraint": "absolute"},
            )
            for point in points:
                writer.materialize_key(
                    point_inputs(point, point_args),
                    batch_id=batch_id,
                    shuffle_rank=point.ordinal,
                    shard=0,
                    intended_fO2_log=point.intended_fO2_log,
                )
            rows = writer.connection.execute(
                "SELECT canonical_vector, intended_fO2_log, expedited_key "
                "FROM grid_keys ORDER BY intended_fO2_log"
            ).fetchall()

        persisted = [json.loads(row[0])["fO2_log"] for row in rows]
        intended = [float(row[1]) for row in rows]
        keys = [str(row[2]) for row in rows]
        echoes = []
        for row in rows:
            inputs = json.loads(row[0])
            result = backend.equilibrate(
                temperature_C=inputs["temperature_C"],
                composition_kg=inputs["composition_kg"],
                composition_mol=inputs["composition_mol"],
                composition_mol_by_account=inputs["composition_mol_by_account"],
                species_formula_registry=inputs["species_formula_registry"],
                fO2_log=inputs["fO2_log"],
                pressure_bar=inputs["pressure_bar"],
                subprocess_run_mode=inputs["subprocess_run_mode"],
            )
            if result.status != "ok":
                raise RuntimeError(
                    f"engine slice refused fO2={inputs['fO2_log']}: "
                    f"{result.status} {result.diagnostics}"
                )
            echoes.append(float(result.diagnostics["engine_reported_fO2_log"]))

    passed = (
        persisted == intended == echoes == list(FO2_LEVELS)
        and len(set(keys)) == len(FO2_LEVELS)
    )
    return {
        "passed": passed,
        "temperature_C": float(temperature_C),
        "persisted_fO2_log": persisted,
        "intended_fO2_log": intended,
        "engine_echo_fO2_log": echoes,
        "distinct_keys": len(set(keys)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temperature-C", type=float, default=1800.0)
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args()
    report = verify_slice(
        temperature_C=args.temperature_C,
        timeout_s=args.timeout_s,
        binary=args.binary,
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
