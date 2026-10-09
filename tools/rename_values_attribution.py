#!/usr/bin/env python3
"""Rename observation-level ``values.source_attribution`` / ``values.quoted_from``
to the canonical ``values.attribution`` in literature extracts (ruling #17, t-1117).

Scope: ``species.<formula>.observations[].values`` mappings only (plus a
path-form ``fidelity_samples[]`` entry that mirrors a renamed values block). Row-level,
nested (``values.rows[]``, ``values.<sub>.quoted_from``), ``context[]`` and
non-species keys are left alone. A list-form value (non-empty strings only) is
joined with "; ". A values block that already has ``attribution`` (or carries
both alias keys) is reported as a conflict and left unchanged.

The edit is textual (key token / value span from the YAML node marks), so
comments, quoting and layout elsewhere are untouched; YAML anchors are edited
once at the anchor. Each rewritten file is re-parsed and checked to equal the
intended transform of the original document.

    .venv/bin/python tools/rename_values_attribution.py            # dry run
    .venv/bin/python tools/rename_values_attribution.py --write
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"
ALIASES = ("source_attribution", "quoted_from")
CANONICAL = "attribution"


def _get(node: yaml.MappingNode, key: str):
    for k, v in node.value:
        if isinstance(k, yaml.ScalarNode) and k.value == key:
            return k, v
    return None, None


def _values_nodes(root):
    """Yield (observation_id, values MappingNode) for species observations."""
    if not isinstance(root, yaml.MappingNode):
        return
    _, species = _get(root, "species")
    if not isinstance(species, yaml.MappingNode):
        return
    for _, body in species.value:
        if not isinstance(body, yaml.MappingNode):
            continue
        _, observations = _get(body, "observations")
        if not isinstance(observations, yaml.SequenceNode):
            continue
        for obs in observations.value:
            if not isinstance(obs, yaml.MappingNode):
                continue
            _, oid = _get(obs, "observation_id")
            _, values = _get(obs, "values")
            if isinstance(values, yaml.MappingNode):
                yield (oid.value if isinstance(oid, yaml.ScalarNode) else None), values


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def plan(text: str, name: str):
    """Return (edits, renames, conflicts, skipped) for one extract's text."""
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    edits, renames, conflicts, skipped = [], [], [], []
    seen: set[int] = set()
    for oid, values in _values_nodes(root):
        if id(values) in seen:  # YAML alias of an anchored observation
            continue
        seen.add(id(values))
        present = [k for k in ALIASES if _get(values, k)[0] is not None]
        if not present:
            continue
        if _get(values, CANONICAL)[0] is not None or len(present) > 1:
            conflicts.append((name, oid, present + ([CANONICAL] if _get(values, CANONICAL)[0] is not None else [])))
            continue
        key, val = _get(values, present[0])
        if isinstance(val, yaml.ScalarNode) and val.tag.endswith(":str") and val.value.strip():
            edits.append((key.start_mark.index, key.end_mark.index, CANONICAL))
            renames.append((name, oid, present[0], "str", val.value))
        elif (
            isinstance(val, yaml.SequenceNode)
            and val.value
            and all(isinstance(i, yaml.ScalarNode) and i.tag.endswith(":str") and i.value.strip() for i in val.value)
        ):
            joined = "; ".join(i.value.strip() for i in val.value)
            end = val.end_mark.index if val.flow_style else val.value[-1].end_mark.index
            span = text[key.start_mark.index:end]
            if "#" in span:
                skipped.append((name, oid, present[0], "comment inside list span"))
                continue
            edits.append((key.start_mark.index, end, f"{CANONICAL}: {_quote(joined)}"))
            renames.append((name, oid, present[0], "list", joined))
        else:
            skipped.append((name, oid, present[0], f"not a name ({type(val).__name__})"))
    # A path-form fidelity sample that mirrors a renamed values block moves with it,
    # so the validator's sample-vs-extract match keeps holding.
    renamed = {r[1]: r for r in renames}
    _, samples = _get(root, "fidelity_samples") if isinstance(root, yaml.MappingNode) else (None, None)
    for sample in samples.value if isinstance(samples, yaml.SequenceNode) else []:
        _, spath = _get(sample, "path") if isinstance(sample, yaml.MappingNode) else (None, None)
        _, svalue = _get(sample, "value") if isinstance(sample, yaml.MappingNode) else (None, None)
        if not (isinstance(spath, yaml.ScalarNode) and isinstance(svalue, yaml.MappingNode)):
            continue
        hit = next((r for oid, r in renamed.items() if spath.value.endswith(f"[{oid}].values")), None)
        if hit is None:
            continue
        key, val = _get(svalue, hit[2])
        if key is None or not isinstance(val, yaml.ScalarNode):
            continue
        if _get(svalue, CANONICAL)[0] is not None:
            conflicts.append((name, f"fidelity_samples:{hit[1]}", [hit[2], CANONICAL]))
            continue
        edits.append((key.start_mark.index, key.end_mark.index, CANONICAL))
        renames.append((name, f"fidelity_samples:{hit[1]}", hit[2], "str", val.value))
    return edits, renames, conflicts, skipped


def _expected(doc, renames_by_oid):
    out = copy.deepcopy(doc)
    for body in (out.get("species") or {}).values():
        for obs in (body or {}).get("observations") or []:
            vals = obs.get("values")
            r = renames_by_oid.get(obs.get("observation_id"))
            if not isinstance(vals, dict) or r is None:
                continue
            key, _, joined = r
            if key in vals:
                vals[CANONICAL] = joined
                del vals[key]
    for sample in out.get("fidelity_samples") or []:
        if not isinstance(sample, dict) or not isinstance(sample.get("value"), dict):
            continue
        path = str(sample.get("path") or "")
        for oid, (key, _, joined) in renames_by_oid.items():
            mirrored = f"fidelity_samples:{oid}"
            if mirrored in renames_by_oid and path.endswith(f"[{oid}].values") and key in sample["value"]:
                sample["value"][CANONICAL] = joined
                del sample["value"][key]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, help="extract files (default: every data/literature/extracts/*.yaml)")
    ap.add_argument("--write", action="store_true", help="rewrite files in place")
    args = ap.parse_args(argv)
    files = args.files or sorted(EXTRACTS.glob("*.yaml"))
    total, all_conflicts, all_skipped, touched = 0, [], [], 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        if not any(a in text for a in ALIASES):
            continue
        edits, renames, conflicts, skipped = plan(text, path.name)
        all_conflicts += conflicts
        all_skipped += skipped
        for r in renames:
            print("RENAME", *r[:4], r[4][:100], sep=" | ")
        if not edits:
            continue
        new = text
        for start, end, repl in sorted(edits, reverse=True):
            new = new[:start] + repl + new[end:]
        before = yaml.safe_load(text)
        by_oid = {oid: (key, shape, joined) for _, oid, key, shape, joined in renames}
        if yaml.safe_load(new) != _expected(before, by_oid):
            print(f"ABORT {path.name}: re-parsed document differs from intended transform", file=sys.stderr)
            return 2
        total += len(edits)
        touched += 1
        if args.write:
            path.write_text(new, encoding="utf-8")
    for c in all_conflicts:
        print("CONFLICT", *c, sep=" | ")
    for s in all_skipped:
        print("SKIPPED", *s, sep=" | ")
    print(f"edits={total} files={touched} conflicts={len(all_conflicts)} skipped={len(all_skipped)} write={args.write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
