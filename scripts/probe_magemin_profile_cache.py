#!/usr/bin/env python3
"""Measure MAGEMin dispatches and freeze-gate memo admission for one profile."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stdout
from functools import wraps
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from typing import Any, Mapping


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--target-id", default="pc-extract-mg")
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    sys.path.insert(0, str(repo_root))
    os.chdir(repo_root)

    import simulator.engine_binding_admission as binding_admission
    import simulator.reduced_real_determinism as determinism
    import scripts.make_recipe_db_profile as generator
    import yaml
    from simulator.melt_backend.magemin import MAGEMinBackend
    from simulator.optimize.evaluate import evaluate
    from simulator.optimize.recipe import RecipePatch
    from simulator.evaporation import EvaporationMixin

    counts: Counter[str] = Counter()
    identities: Counter[str] = Counter()
    miss_reasons: Counter[str] = Counter()
    local = threading.local()
    original_record = EvaporationMixin._freeze_gate_curve_record
    original_key = EvaporationMixin._freeze_gate_cache_key_and_domain
    original_dispatch = EvaporationMixin._freeze_gate_curve_from_gate_dispatch
    original_magemin_call = MAGEMinBackend._call_magemin
    original_cacheable = determinism.gate_curve_memo_is_cacheable
    original_reusable = binding_admission.gate_curve_memo_reuse_eligible

    def stack() -> list[dict[str, Any]]:
        active = getattr(local, "records", None)
        if active is None:
            active = []
            local.records = active
        return active

    @wraps(original_key)
    def key_probe(self: Any, *pos: Any, **kw: Any) -> Any:
        result = original_key(self, *pos, **kw)
        active = stack()
        if active and active[-1]["sim"] is self:
            identity = getattr(
                self, "_freeze_gate_current_magemin_domain_identity", None
            )
            active[-1]["keys"].append((result[0], result[1], identity))
        return result

    @wraps(original_cacheable)
    def cacheable_probe(*pos: Any, **kw: Any) -> Any:
        result = original_cacheable(*pos, **kw)
        active = stack()
        if active:
            active[-1]["admission"].append(("cacheable", bool(result)))
        return result

    @wraps(original_reusable)
    def reusable_probe(*pos: Any, **kw: Any) -> Any:
        result = original_reusable(*pos, **kw)
        active = stack()
        if active:
            active[-1]["admission"].append(("reuse_eligible", bool(result)))
        return result

    @wraps(original_dispatch)
    def dispatch_probe(self: Any, *pos: Any, **kw: Any) -> Any:
        counts["freeze_gate_dispatches"] += 1
        return original_dispatch(self, *pos, **kw)

    @wraps(original_magemin_call)
    def magemin_call_probe(self: Any, *pos: Any, **kw: Any) -> Any:
        counts["magemin_solver_dispatches"] += 1
        return original_magemin_call(self, *pos, **kw)

    @wraps(original_record)
    def record_probe(self: Any, *pos: Any, **kw: Any) -> Any:
        before_local = getattr(
            self, "_freeze_gate_liquid_fraction_cache", None
        )
        before_shared = getattr(
            self, "_freeze_gate_shared_curve_cache", None
        )
        entry = {
            "sim": self,
            "keys": [],
            "admission": [],
            "local_key": before_local.get("key")
            if isinstance(before_local, Mapping)
            else None,
            "shared_keys": set(before_shared)
            if isinstance(before_shared, Mapping)
            else set(),
        }
        active = stack()
        active.append(entry)
        try:
            result = original_record(self, *pos, **kw)
        finally:
            active.pop()
        counts["gate_record_calls"] += 1
        disposition = str(result.get("disposition", "unknown")) if isinstance(
            result, Mapping
        ) else "non_mapping"
        counts[f"gate_record_disposition:{disposition}"] += 1
        if disposition != "live" or not entry["keys"]:
            return result

        key, domain_reason, identity = entry["keys"][0]
        identity_label = (
            "missing" if identity is None
            else f"{identity.silica_verdict}/{identity.projection_threshold_verdict}"
        )
        identities[identity_label] += 1
        reasons = []
        if domain_reason is not None:
            reasons.append("domain_refusal")
        elif identity is None or not identity.reusable:
            reasons.append("identity_not_reusable")
        if entry["local_key"] is None:
            reasons.append("no_local_memo")
        elif entry["local_key"] != key:
            reasons.append("local_key_mismatch")
        if not entry["shared_keys"]:
            reasons.append("no_shared_memo")
        elif key not in entry["shared_keys"]:
            reasons.append("shared_key_mismatch")
        for check, passed in entry["admission"]:
            if not passed:
                reasons.append(f"{check}_rejected")
        miss_reasons["+".join(dict.fromkeys(reasons)) or "candidate_not_accepted"] += 1
        return result

    EvaporationMixin._freeze_gate_cache_key_and_domain = key_probe
    EvaporationMixin._freeze_gate_curve_from_gate_dispatch = dispatch_probe
    EvaporationMixin._freeze_gate_curve_record = record_probe
    MAGEMinBackend._call_magemin = magemin_call_probe
    determinism.gate_curve_memo_is_cacheable = cacheable_probe
    binding_admission.gate_curve_memo_reuse_eligible = reusable_probe

    error = None
    try:
        with tempfile.TemporaryDirectory(prefix="magemin-profile-probe-") as temp:
            temp_path = Path(temp)
            profile_path = temp_path / f"{args.target_id}.yaml"
            runtime_identity = generator._runtime_engine_identity
            generator._runtime_engine_identity = lambda: ("stub-engine", "test")
            try:
                with redirect_stdout(io.StringIO()):
                    generator.main([
                        "lunar_mare_low_ti",
                        "--target",
                        args.target_id,
                        "--db",
                        str(temp_path / "cache.db"),
                        "--out",
                        str(profile_path),
                    ])
            finally:
                generator._runtime_engine_identity = runtime_identity
            profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
            evaluate(
                RecipePatch({}),
                "lunar_mare_low_ti",
                "internal-analytical",
                profile=profile,
                candidate_id=f"smoke-{args.target_id}",
            )
    except Exception as exc:  # Keep counters useful when the profile aborts.
        error = {"class": type(exc).__name__, "message": str(exc)}
    finally:
        EvaporationMixin._freeze_gate_cache_key_and_domain = original_key
        EvaporationMixin._freeze_gate_curve_from_gate_dispatch = original_dispatch
        EvaporationMixin._freeze_gate_curve_record = original_record
        MAGEMinBackend._call_magemin = original_magemin_call
        determinism.gate_curve_memo_is_cacheable = original_cacheable
        binding_admission.gate_curve_memo_reuse_eligible = original_reusable

    print(json.dumps({
        "target_id": args.target_id,
        "repo_root": str(repo_root),
        "counts": dict(sorted(counts.items())),
        "identity_classifications": dict(sorted(identities.items())),
        "cache_miss_reasons": dict(sorted(miss_reasons.items())),
        "error": error,
    }, sort_keys=True))
    return 0 if error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
