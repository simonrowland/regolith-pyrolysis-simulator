#!/usr/bin/env python3
"""Audit the chemistry provenance registry (docs/chemistry-provenance.yaml).

The registry is the comparative decision layer over the bibliography
(docs/references/references.yaml). Policy: docs/citation-policy.md.

Strict mode is the CI-intended default. Pass --lenient for exploratory reports.
The gate enforces:

  (a) schema validity and legal trust tiers;
  (b) every chosen REF-xxx and alternative REF-xxx resolves in references.yaml;
  (c) chosen loci include a page/table/figure/equation/table-id style anchor;
  (d) every non-pending code_site path/line/symbol resolves;
  (e) registry numeric values are present at their declared code_sites;
  (f) load-bearing basis fields present in code also appear in registry basis text.

Exit status:
  0  registry valid, strict completeness checks satisfied
  1  hard errors, or strict-mode completeness/policy warnings
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "chemistry-provenance.yaml"
REFERENCES = ROOT / "docs" / "references" / "references.yaml"

VALID_TIERS = {"CITED", "ASSUMED", "UNCERTIFIED"}
REQUIRED_FIELDS = ("id", "quantity", "value", "units", "tier", "chosen", "code_sites")
COMPLETENESS_FIELDS = ("basis", "range", "uncertainty")
REF_RE = re.compile(r"\bREF-\d{3}\b")
LOCUS_ANCHOR_RE = re.compile(
    r"(?i)(?:\bp\.|\bpp\.|\bpages?\b|\btables?\b|\bfig(?:ure)?\.?\b|"
    r"\beqn\.?\b|\bequation\b|§|\b[A-Z][a-z]?-?\d{3}\b|\b\d{2,5}\s*-\s*\d{2,5}\b)"
)
NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
BASIS_SITE_FIELDS = ("oxide_activity_exponent", "pO2_exponent")


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{k} {_as_text(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(v) for v in value)
    return str(value)


def _numbers(value, prefix: str = "value") -> list[tuple[str, float]]:
    """Flatten a registry value into (path, float) pairs."""
    out: list[tuple[str, float]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            out += _numbers(item, f"{prefix}.{key}")
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            out += _numbers(item, f"{prefix}[{i}]")
    elif isinstance(value, bool):
        return out
    elif isinstance(value, (int, float)):
        out.append((prefix, float(value)))
    return out


def _code_numbers(code_text: str) -> list[float]:
    nums: list[float] = []
    for match in NUMBER_RE.finditer(code_text.replace("_", "")):
        try:
            nums.append(float(match.group(0)))
        except ValueError:
            continue
    return nums


def _number_present(target: float, code_nums: list[float]) -> bool:
    for observed in code_nums:
        if math.isclose(observed, target, rel_tol=1e-5, abs_tol=1e-8):
            return True
    return False


def _number_present_or_derived(target: float, code_nums: list[float]) -> bool:
    if _number_present(target, code_nums):
        return True
    # Some registry values are phase-change primitives while code stores the
    # resulting high/low reaction-coefficient deltas. Accept simple local deltas,
    # including stoichiometric multiples visible in the same resolved site.
    multipliers = (1.0, 2.0, 4.0 / 3.0)
    for i, left in enumerate(code_nums):
        for right in code_nums[i + 1 :]:
            delta = abs(left - right)
            for m in multipliers:
                if math.isclose(delta, abs(target * m), rel_tol=1e-5, abs_tol=1e-8):
                    return True
    return False


def _missing_values(code_texts: list[str], value) -> list[str]:
    nums = _numbers(value)
    if not nums:
        return []
    code_nums: list[float] = []
    for text in code_texts:
        code_nums.extend(_code_numbers(text))
    missing: list[str] = []
    for path, number in nums:
        if not _number_present_or_derived(number, code_nums):
            missing.append(f"{path}={number:g}")
    return missing


def _load_references(errors: list[str]) -> set[str]:
    if not REFERENCES.exists():
        errors.append(f"references file not found: {REFERENCES.relative_to(ROOT)}")
        return set()
    refs_doc = yaml.safe_load(REFERENCES.read_text(encoding="utf-8"))
    refs = refs_doc.get("references") if isinstance(refs_doc, dict) else None
    if not isinstance(refs, dict):
        errors.append("references.yaml has no top-level `references:` mapping")
        return set()
    return set(refs)


def _check_refs(tag: str, label: str, raw_ref, known_refs: set[str], errors: list[str]) -> None:
    text = _as_text(raw_ref)
    refs = REF_RE.findall(text)
    if label == "chosen.ref" and not refs and not text.startswith("ASSUMED:"):
        errors.append(f"{tag}: chosen.ref must be a REF-xxx or ASSUMED marker, got {text!r}")
    for ref in refs:
        if ref not in known_refs:
            errors.append(f"{tag}: {label} references unknown {ref}")


def _site_parts(site: str) -> tuple[str, str, int | None, str | None]:
    file_part, _, locator = site.partition(":")
    locator = locator.strip()
    line_no = None
    line_match = re.match(r"(\d+)", locator)
    if line_match:
        line_no = int(line_match.group(1))
    symbol = None
    paren_match = re.search(r"\(([^)]+)\)", locator)
    if paren_match:
        candidate = paren_match.group(1).strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", candidate):
            symbol = candidate
    elif locator and line_no is None:
        symbol = locator
    return file_part.strip(), locator, line_no, symbol


def _yaml_path_node(path: Path, symbol: str):
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    node = data
    for part in symbol.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
            continue
        return None
    return node


def _resolve_code_site(tag: str, site: str, errors: list[str]) -> str | None:
    fp, locator, line_no, symbol = _site_parts(site)
    path = ROOT / fp
    if not path.exists():
        errors.append(f"{tag}: code_site file does not exist: {fp}")
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    scoped_text = text
    if line_no is not None and not (1 <= line_no <= len(lines)):
        errors.append(f"{tag}: code_site line {line_no} outside {fp} length {len(lines)}")
    elif line_no is not None:
        start = max(0, line_no - 8)
        end = min(len(lines), line_no + 240)
        scoped_text = "\n".join(lines[start:end])
    if symbol:
        if path.suffix in {".yaml", ".yml"} and "." in symbol:
            node = _yaml_path_node(path, symbol)
            if node is None:
                errors.append(f"{tag}: code_site YAML path not found: {fp}:{symbol}")
            else:
                scoped_text = yaml.safe_dump(node, sort_keys=False)
        elif symbol not in text:
            tail = symbol.rsplit(".", 1)[-1]
            if tail not in text:
                errors.append(f"{tag}: code_site symbol not found: {fp}:{symbol}")
    elif locator and line_no is None:
        errors.append(f"{tag}: code_site lacks resolvable line or symbol: {site}")
    return scoped_text


def _requires_url(chosen_ref) -> bool:
    return bool(REF_RE.search(_as_text(chosen_ref)))


def audit(strict: bool = True) -> int:
    if not REGISTRY.exists():
        print(f"ERROR: registry not found: {REGISTRY}", file=sys.stderr)
        return 1
    load_errors: list[str] = []
    known_refs = _load_references(load_errors)
    doc = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "values" not in doc:
        print("ERROR: registry has no top-level `values:` list", file=sys.stderr)
        return 1

    entries = doc["values"] or []
    errors: list[str] = load_errors
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
        else:
            _check_refs(tag, "chosen.ref", chosen.get("ref"), known_refs, errors)

        for j, alt in enumerate(e.get("alternatives") or []):
            if isinstance(alt, dict) and alt.get("ref"):
                _check_refs(tag, f"alternatives[{j}].ref", alt.get("ref"), known_refs, errors)

        has_locus = bool(chosen.get("locus"))
        has_locus_anchor = has_locus and bool(LOCUS_ANCHOR_RE.search(str(chosen.get("locus"))))
        has_url = bool(chosen.get("url")) or not _requires_url(chosen.get("ref"))
        has_basis_fields = all(e.get(f) for f in COMPLETENESS_FIELDS)
        if has_locus_anchor and has_url and has_basis_fields:
            complete_citations += 1
        else:
            miss = [f for f in COMPLETENESS_FIELDS if not e.get(f)]
            if not has_locus:
                miss.append("chosen.locus (page/table/figure)")
            elif not has_locus_anchor:
                miss.append("chosen.locus page/table/figure anchor")
            if not has_url:
                miss.append("chosen.url")
            warnings.append(f"{tag}: incomplete citation — missing {', '.join(miss)}")

        # code_sites
        code_texts: list[str] = []
        for site in e.get("code_sites", []):
            if isinstance(site, str) and site.startswith("pending"):
                pending += 1
                continue
            site_text = _resolve_code_site(tag, str(site), errors)
            if site_text is not None:
                code_texts.append(site_text)

        if code_texts:
            missing_values = _missing_values(code_texts, e.get("value"))
            if missing_values:
                errors.append(f"{tag}: registry value(s) not found in declared code_sites: {', '.join(missing_values)}")

            basis_text = _as_text(e.get("basis")) + " " + _as_text(e.get("value"))
            joined_code = "\n".join(code_texts)
            for field in BASIS_SITE_FIELDS:
                if field in joined_code and field not in basis_text:
                    warnings.append(f"{tag}: code_site contains `{field}` but registry basis does not mention it")

    # report
    print(f"chemistry-provenance registry: {len(entries)} entries")
    print("  tiers: " + ", ".join(f"{t}={tiers[t]}" for t in sorted(tiers)))
    print(f"  complete citations (anchored locus + URL/basis/range/uncertainty): {complete_citations}/{len(entries)}")
    print(f"  pending code_sites: {pending}")
    if warnings:
        print(f"\n  policy warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    - {w}")
    if errors:
        print(f"\nHARD ERRORS ({len(errors)}):", file=sys.stderr)
        for er in errors:
            print(f"  - {er}", file=sys.stderr)
        return 1
    if strict and warnings:
        print("\n--strict: failing on policy warnings.", file=sys.stderr)
        return 1
    print("\nOK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", dest="strict", action="store_true", default=True, help="fail on policy warnings (default)")
    ap.add_argument("--lenient", dest="strict", action="store_false", help="report policy warnings without failing")
    args = ap.parse_args()
    return audit(strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
