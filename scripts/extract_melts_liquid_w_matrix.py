#!/usr/bin/env python3
"""Capture the live MELTS liquid Margules W matrix (t-586).

YAML, not JSON: ``data/`` provenance pins are YAML, the 105-edge table is a
human-facing t-647 ranking input, and a one-edge engine upgrade should be a
readable git diff. The matrix is tiny; JSON's machine-parse advantage is
irrelevant.

Source of truth is the installed ThermoEngine module the simulator itself
imports (``setup_thermoengine_dylib_path`` + ``thermoengine.model.Database``).
Do not transcribe papers or parse sibling ``ThermoEngine/src/*.m`` files.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SNAPSHOT_PATH = ROOT / "data" / "melts_liquid_w_matrix.yaml"
MELTS_MODEL = "MELTSv1.0.2"
LIQUID_MODEL = "v1.0"
EXPECTED_ENDMEMBER_COUNT = 15
EXPECTED_EDGE_COUNT = 105
PARAM_NAME_RE = re.compile(r"^W\(\s*(.+?)\s*,\s*(.+?)\s*\)$")
# Deterministic mix of large fitted edges and every live exact-zero edge.
DRIFT_GUARD_SAMPLE_INDICES = (0, 1, 10, 35, 40, 59, 75, 76, 88, 89, 93, 94, 104)


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed-as-distribution"


def _load_thermoengine() -> tuple[Any, Any]:
    """Import ThermoEngine only after the simulator dylib path is set.

    ``thermoengine.core`` / ``equilibrate`` dlopen PhaseObjC at import time.
    Importing first leaves LiquidMelts unresolved for the rest of the process.
    """
    from simulator.engine_local_config import setup_thermoengine_dylib_path

    setup_thermoengine_dylib_path()
    import thermoengine
    from thermoengine import model

    return thermoengine, model


def live_thermoengine_importable() -> bool:
    try:
        _load_thermoengine()
    except Exception:
        return False
    return True


def _open_live_liquid() -> tuple[Any, Any, Any]:
    thermoengine, model = _load_thermoengine()
    database = model.Database(
        database="Berman",
        liq_mod=LIQUID_MODEL,
        calib=True,
    )
    liquid = database.get_phase("Liq")
    return thermoengine, database, liquid


def _parse_param_name(name: str) -> tuple[str, str]:
    match = PARAM_NAME_RE.fullmatch(str(name))
    if match is None:
        raise ValueError(f"unrecognized MELTS W parameter name: {name!r}")
    return match.group(1), match.group(2)


def _classify(w_joules: float) -> str:
    # The live engine has no fitted-flag. Exact 0.0 is the operational
    # zero/absent mark MELTS treats as ideal mixing on that edge.
    return "zero_absent" if float(w_joules) == 0.0 else "fitted"


def _matrix_digest(edges: Sequence[Mapping[str, Any]]) -> str:
    payload = [
        [
            int(edge["index"]),
            str(edge["component_i"]),
            str(edge["component_j"]),
            str(edge["status"]),
            float(edge["W_joules"]),
        ]
        for edge in edges
    ]
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def extract_live_snapshot() -> dict[str, Any]:
    from simulator.engine_local_config import cache_version_for

    thermoengine, _database, liquid = _open_live_liquid()
    endmembers = [str(name) for name in liquid.endmember_names]
    if len(endmembers) != EXPECTED_ENDMEMBER_COUNT:
        raise RuntimeError(
            f"expected {EXPECTED_ENDMEMBER_COUNT} MELTS liquid endmembers, "
            f"got {len(endmembers)}: {endmembers}"
        )

    param_names = [str(name) for name in liquid.param_names]
    if len(param_names) != EXPECTED_EDGE_COUNT:
        raise RuntimeError(
            f"expected {EXPECTED_EDGE_COUNT} live W parameters, "
            f"got {len(param_names)}"
        )
    units = [str(unit) for unit in liquid.param_units(param_names=param_names)]
    values = [float(value) for value in liquid.get_param_values(param_names=param_names)]
    if len(units) != len(param_names) or len(values) != len(param_names):
        raise RuntimeError("live W name/unit/value vectors are different lengths")
    unexpected_units = sorted({unit for unit in units if unit != "joules"})
    if unexpected_units:
        raise RuntimeError(
            "live W units are not uniformly 'joules': "
            f"{unexpected_units}"
        )
    non_constant_names = [
        name
        for name in param_names
        if not name.startswith("W(")
        or name.startswith("WS(")
        or name.startswith("WV(")
    ]
    if non_constant_names:
        raise RuntimeError(
            "live parameter vector contains a T/P coefficient name; "
            "snapshot form must be recaptured, not assumed constant: "
            f"{non_constant_names[:8]}"
        )

    pairs: set[tuple[str, str]] = set()
    edges: list[dict[str, Any]] = []
    for index, (name, unit, value) in enumerate(zip(param_names, units, values, strict=True)):
        component_i, component_j = _parse_param_name(name)
        if component_i not in endmembers or component_j not in endmembers:
            raise RuntimeError(
                f"W edge {name!r} is outside the live endmember basis"
            )
        if component_i == component_j:
            raise RuntimeError(f"diagonal W parameter is not a bilateral edge: {name!r}")
        pair = tuple(sorted((component_i, component_j)))
        if pair in pairs:
            raise RuntimeError(f"duplicate live W pair: {pair}")
        pairs.add(pair)
        edges.append(
            {
                "index": index,
                "component_i": component_i,
                "component_j": component_j,
                "engine_param_name": name,
                "status": _classify(value),
                "form": "constant",
                "W_joules": value,
                "W_S_joules_per_K": None,
                "W_V_joules_per_bar": None,
                "units": unit,
            }
        )

    fitted = [edge for edge in edges if edge["status"] == "fitted"]
    zero_absent = [edge for edge in edges if edge["status"] == "zero_absent"]
    props = getattr(liquid, "props", {}) or {}
    module_path = getattr(thermoengine, "__file__", "unknown")
    cache_identity = cache_version_for("thermoengine")

    return {
        "schema_version": 1,
        "kind": "melts_liquid_w_matrix",
        "format": "yaml",
        "format_reason": (
            "YAML matches data/ provenance pins and keeps the 105-edge "
            "t-647 missing-pair table human-diffable. The matrix is small; "
            "JSON would add no numeric fidelity here."
        ),
        "description": (
            "Live ThermoEngine MELTS v1.0 liquid Margules W matrix. "
            "105 bilateral edges over the 15 LiquidMelts endmembers. "
            "Extracted from the installed module, not from papers."
        ),
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "extraction_script": "scripts/extract_melts_liquid_w_matrix.py",
        "engine": {
            "name": "thermoengine",
            "package_version": _package_version("thermoengine"),
            "melts_model": MELTS_MODEL,
            "liquid_model": LIQUID_MODEL,
            "phase_class": str(props.get("class_name") or ""),
            "phase_name": str(props.get("phase_name") or ""),
            "phase_source": str(getattr(liquid, "source", "") or ""),
            "phase_identifier": str(props.get("identifier") or ""),
            "phase_abbrev": str(props.get("abbrev") or ""),
            "cache_identity": cache_identity,
            "module_path": str(Path(module_path).resolve()) if module_path != "unknown" else module_path,
            "import_path": (
                "simulator.engine_local_config.setup_thermoengine_dylib_path; "
                "thermoengine.model.Database("
                f"database='Berman', liq_mod='{LIQUID_MODEL}', calib=True"
                ").get_phase('Liq')"
            ),
        },
        "endmember_basis": endmembers,
        "units": {
            "W": "joules",
            "source": "thermoengine Phase.getUnitsForParameterName_ / param_units",
        },
        "t_p_dependence": {
            "form": "constant",
            "W_H": "live_parameter_vector",
            "W_S": "absent_from_live_parameter_vector",
            "W_V": "absent_from_live_parameter_vector",
            "evidence": (
                "The live LiquidMelts calibration vector is 105 names of the "
                "form W(i,j), all units 'joules'. No WS/WV (or other T/P "
                "polynomial) names are exposed."
            ),
        },
        "classification": {
            "engine_has_fitted_flag": False,
            "fitted_rule": "live W_joules != 0.0",
            "zero_absent_rule": "live W_joules == 0.0 exactly",
            "note": (
                "Load-bearing for t-647. The engine stores a complete 105-slot "
                "vector and does not mark fitted vs placeholder; exact zero is "
                "the only live distinction, and MELTS treats those edges as ideal."
            ),
        },
        "counts": {
            "endmembers": len(endmembers),
            "edges": len(edges),
            "fitted": len(fitted),
            "zero_absent": len(zero_absent),
        },
        "zero_absent_pairs": [
            [edge["component_i"], edge["component_j"]] for edge in zero_absent
        ],
        "drift_guard_sample_indices": list(DRIFT_GUARD_SAMPLE_INDICES),
        "matrix_digest": _matrix_digest(edges),
        "edges": edges,
    }


def load_snapshot(path: Path | None = None) -> dict[str, Any]:
    import yaml

    snapshot = Path(path) if path is not None else SNAPSHOT_PATH
    payload = yaml.safe_load(snapshot.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{snapshot} is not a YAML mapping")
    return payload


def sample_edges(
    payload: Mapping[str, Any],
    indices: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    chosen = tuple(indices) if indices is not None else DRIFT_GUARD_SAMPLE_INDICES
    edges = list(payload["edges"])
    sample: list[dict[str, Any]] = []
    for index in chosen:
        if index < 0 or index >= len(edges):
            raise IndexError(f"drift-guard index {index} out of range")
        edge = dict(edges[index])
        if int(edge["index"]) != int(index):
            raise RuntimeError(
                f"edge at slot {index} has index {edge['index']}"
            )
        sample.append(edge)
    return sample


def _dump_snapshot(payload: Mapping[str, Any]) -> str:
    import yaml

    header = {key: value for key, value in payload.items() if key != "edges"}
    text = yaml.safe_dump(
        header,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    lines = ["edges:"]
    for edge in payload["edges"]:
        dumped = yaml.safe_dump(
            dict(edge),
            sort_keys=False,
            default_flow_style=True,
            allow_unicode=True,
            width=1000,
        ).strip()
        lines.append(f"  - {dumped}")
    return text.rstrip() + "\n" + "\n".join(lines) + "\n"


def write_snapshot(payload: Mapping[str, Any], path: Path | None = None) -> Path:
    snapshot = Path(path) if path is not None else SNAPSHOT_PATH
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(_dump_snapshot(payload), encoding="utf-8")
    return snapshot


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract the live MELTS liquid W matrix into data/."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=SNAPSHOT_PATH,
        help="snapshot path (default: data/melts_liquid_w_matrix.yaml)",
    )
    args = parser.parse_args(argv)
    payload = extract_live_snapshot()
    path = write_snapshot(payload, args.output)
    counts = payload["counts"]
    engine = payload["engine"]
    print(
        f"wrote={path} "
        f"fitted={counts['fitted']} "
        f"zero_absent={counts['zero_absent']} "
        f"edges={counts['edges']} "
        f"engine={engine['name']} {engine['package_version']} "
        f"melts={engine['melts_model']} "
        f"liq_mod={engine['liquid_model']} "
        f"digest={payload['matrix_digest']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
