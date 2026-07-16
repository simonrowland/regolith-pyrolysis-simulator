#!/usr/bin/env python3
"""Generate the byte-pinned optimizer recipe vocabulary manifest."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.optimize.canonical import canonical_json_dumps
from simulator.optimize.recipe import (
    CANONICAL_TO_RUNTIME_PATH,
    PATH_ALIASES,
    RecipeSchema,
    c5_sampler_context,
)


def _path(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _guard(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "parent_paths": [_path(path) for path in value.parent_paths],
        "predicate": value.predicate,
        "threshold": value.threshold,
        "canonicalizer_id": value.canonicalizer_id,
    }


def build_manifest() -> dict[str, Any]:
    schema = RecipeSchema()
    rows = []
    for spec in sorted(schema.allowlist, key=lambda item: item.path):
        runtime = CANONICAL_TO_RUNTIME_PATH.get(spec.path)
        rows.append(
            {
                "path": _path(spec.path),
                "kind": spec.kind,
                "low": spec.low,
                "high": spec.high,
                "choices": list(spec.choices) if spec.choices is not None else None,
                "units": spec.units,
                "scale": spec.scale,
                "search_enabled": spec.search_enabled,
                "runtime_enabled": spec.runtime_enabled,
                "guard": _guard(spec.guard),
                "bounds_source": spec.bounds_source,
                "runtime_path": _path(runtime[0]) if runtime else _path(spec.path),
                "runtime_transform": runtime[1] if runtime else "identity",
            }
        )
    conditional = []
    for active in (False, True):
        context = c5_sampler_context(schema, active=active)
        conditional.append(
            {
                "id": "c5-on" if active else "c5-off",
                "dimension": 70 if active else 64,
                "digest": context.conditional_subspace_digest,
            }
        )
    payload: dict[str, Any] = {
        "manifest_schema": "optimizer-recipe-vocabulary-v1",
        "manifest_version": 1,
        "recipe_schema_version": schema.recipe_schema_version,
        "allowlist_version": schema.allowlist_version,
        "bounds_digest": schema.bounds_digest,
        "allowlist": rows,
        "aliases": [
            {
                "old_path": _path(old),
                "canonical_path": _path(alias.canonical_path),
                "transform_id": alias.transform_id,
                "deprecation_epoch": alias.deprecation_epoch,
            }
            for old, alias in sorted(PATH_ALIASES.items())
        ],
        "forbidden_prefixes": list(schema.forbidden_prefixes),
        "conditional_subspaces": conditional,
        "conditional_allocation_version": "optimizer-conditional-subspace-v1",
    }
    payload_bytes = canonical_json_dumps(payload).encode("utf-8")
    return {
        **payload,
        "payload_digest": hashlib.sha256(payload_bytes).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/optimizer_recipe_vocabulary.json"),
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json_dumps(build_manifest()) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
