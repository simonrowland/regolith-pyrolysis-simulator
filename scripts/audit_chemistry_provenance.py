#!/usr/bin/env python3
"""Audit the chemistry provenance registry (docs/chemistry-provenance.yaml).

The registry is the comparative decision layer over the bibliography
(docs/references/references.yaml). Policy: docs/citation-policy.md.

This script is the one-command audit — for a human, and (eventually) as a CI gate:

  (a) every entry is schema-valid and its trust tier is legal;
  (b) every `code_sites` file exists, and for real (non-"pending:") sites the registry
      `value` is actually present in the code (best-effort, tolerant);
  (c) it reports counts per trust tier, per-entry citation completeness (locus + URL, the
      policy's page-granularity bar), and every open gap.

Exit status:
  0  registry valid, no hard errors (CI-green)
  1  hard errors: schema violation, illegal tier, missing chosen.ref, or a code_site file
     that does not exist / a value absent from a real code_site

Warnings (completeness gaps — locus/URL missing, pending code_sites) do NOT fail by default;
pass --strict to also fail on them once the registry is fully populated.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "chemistry-provenance.yaml"

VALID_TIERS = {"CITED", "ASSUMED", "UNCERTIFIED"}
REQUIRED_FIELDS = ("id", "quantity", "value", "units", "tier", "chosen", "code_sites")
# Fields the policy wants for a *complete* citation; missing -> completeness warning, not a hard error.
COMPLETENESS_FIELDS = ("basis", "range", "uncertainty")


def _numbers(value) -> list[str]:
    """Flatten a registry value (scalar / dict / list) into printable number strings."""
    out: list[str] = []
    if isinstance(value, dict):
        for v in value.values():
            out += _numbers(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out += _numbers(v)
    elif isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        out.append(repr(value))
    return out


def _value_present(code_text: str, value) -> bool:
    """Best-effort, tolerant: does any of the registry value's numbers appear in the file?

    Matches on the significant digits so 1.0e-3 == 1e-3 == 0.001. Non-numeric values
    (string descriptors) are not checked here (they carry no code number)."""
    nums = _numbers(value)
    if not nums:
        return True  # nothing numeric to match (e.g. a purely descriptive value)
    for n in nums:
        f = float(n)
        # build a few tolerant textual forms of the magnitude's mantissa
        mantissa = f"{f:.6g}".lstrip("-")
        digits = mantissa.replace(".", "").replace("e", "").split("+")[0].split("-")[0]
        digits = digits.lstrip("0")[:4]
        if digits and digits in code_text.replace(".", "").replace("_", ""):
            return True
    return False


def audit(strict: bool = False) -> int:
    if not REGISTRY.exists():
        print(f"ERROR: registry not found: {REGISTRY}", file=sys.stderr)
        return 1
    doc = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "values" not in doc:
        print("ERROR: registry has no top-level `values:` list", file=sys.stderr)
        return 1

    entries = doc["values"] or []
    errors: list[str] = []
    warnings: list[str] = []
    tiers: dict[str, int] = {t: 0 for t in VALID_TIERS}
    pending = 0
    complete_citations = 0
    ids: set[str] = set()

    for i, e in enumerate(entries):
        tag = e.get("id", f"<entry #{i}>")
        for field in REQUIRED_FIELDS:
            if field not in e or e[field] in (None, "", []):
                errors.append(f"{tag}: missing required field `{field}`")
        if tag in ids:
            errors.append(f"{tag}: duplicate id")
        ids.add(tag)

        tier = e.get("tier")
        if tier not in VALID_TIERS:
            errors.append(f"{tag}: illegal tier {tier!r} (must be one of {sorted(VALID_TIERS)})")
        elif tier in tiers:
            tiers[tier] += 1

        chosen = e.get("chosen") or {}
        if not chosen.get("ref"):
            errors.append(f"{tag}: chosen.ref is required")
        # completeness (policy page-granularity + public URL)
        has_locus = bool(chosen.get("locus"))
        has_url = bool(chosen.get("url"))
        if has_locus and has_url and all(e.get(f) for f in COMPLETENESS_FIELDS):
            complete_citations += 1
        else:
            miss = [f for f in COMPLETENESS_FIELDS if not e.get(f)]
            if not has_locus:
                miss.append("chosen.locus (page/table/figure)")
            if not has_url:
                miss.append("chosen.url")
            warnings.append(f"{tag}: incomplete citation — missing {', '.join(miss)}")

        # code_sites
        for site in e.get("code_sites", []):
            if isinstance(site, str) and site.startswith("pending"):
                pending += 1
                continue
            fp = str(site).split(":", 1)[0].strip()
            path = ROOT / fp
            if not path.exists():
                errors.append(f"{tag}: code_site file does not exist: {fp}")
                continue
            if not _value_present(path.read_text(encoding="utf-8", errors="ignore"), e.get("value")):
                errors.append(f"{tag}: value {e.get('value')!r} not found in {fp}")

    # report
    print(f"chemistry-provenance registry: {len(entries)} entries")
    print("  tiers: " + ", ".join(f"{t}={tiers[t]}" for t in sorted(tiers)))
    print(f"  complete citations (locus + URL + basis/range/uncertainty): {complete_citations}/{len(entries)}")
    print(f"  pending code_sites: {pending}")
    if warnings:
        print(f"\n  completeness warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    - {w}")
    if errors:
        print(f"\nHARD ERRORS ({len(errors)}):", file=sys.stderr)
        for er in errors:
            print(f"  - {er}", file=sys.stderr)
        return 1
    if strict and warnings:
        print("\n--strict: failing on completeness warnings.", file=sys.stderr)
        return 1
    print("\nOK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true", help="also fail on completeness warnings (locus/URL/basis/range/uncertainty gaps)")
    args = ap.parse_args()
    return audit(strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
