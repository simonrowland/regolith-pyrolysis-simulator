#!/usr/bin/env python3
"""Report which models are being used OUTSIDE their stated validity domain.

Why this exists
---------------
Running the evaporation plane hotter does not make the models hotter. Every
vapour-pressure representation in ``data/vapor_pressures.yaml`` carries a
``valid_domain.temperature_K``, and every melt engine carries a composition
window. Evaluating outside either produces a number that looks exactly like a
number produced inside it -- no exception, no flag in the result, nothing a
reader of the output could use to tell the two apart.

This is a category-2 instrument in the project's fail-closed doctrine: it
COMPUTES AND MARKS. It never refuses and never gates. Its job is to make the
sentence "we ran at 2200 C" carry its own caveat -- "...with Fe extrapolated
407 K past its band and SiO carrying no recorded band at all".

Read the two sections differently:

  EXTRAPOLATED   a band exists and T is outside it. The distance is the size of
                 the claim being made on the fit's behalf.
  NO BAND        no ``valid_domain`` is recorded. This is NOT the same as
                 in-domain, and it is strictly worse than extrapolated: an
                 extrapolation can at least be measured. Treat it as unknown.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from simulator.yaml_cache import load_cached_safe_yaml  # noqa: E402

RAIL_PATH = REPO / "data" / "vapor_pressures.yaml"


def _load_rail() -> dict[str, Any]:
    return load_cached_safe_yaml(RAIL_PATH.read_text(encoding="utf-8"))


def _species_rows(rail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Resolve species -> its pressure-model bands, by walking the real schema.

    Deliberately NOT a string match over the document. An earlier screen matched
    family names by substring and attributed a Na2Cl2 halide-dimer band to
    potassium, which is the exact failure this function exists to avoid: the
    band of a neighbour is not the band of the species.
    """
    out: dict[str, dict[str, Any]] = {}
    for family_name, family in (rail.get("families") or {}).items():
        if not isinstance(family, dict):
            continue
        species_map = ((family.get("physical_properties") or {}).get("species")
                       or {})
        if not isinstance(species_map, dict):
            continue
        for species, body in species_map.items():
            if not isinstance(body, dict):
                continue
            bands: list[tuple[float, float, str]] = []
            for model in (body.get("pressure_models") or []):
                if not isinstance(model, dict):
                    continue
                domain = (model.get("valid_domain") or {})
                span = domain.get("temperature_K")
                if not (isinstance(span, (list, tuple)) and len(span) == 2):
                    continue
                try:
                    lo = float(span[0]); hi = float(span[1])
                except (TypeError, ValueError):
                    continue
                bands.append((lo, hi, str(model.get("evaluator_family") or "?")))
            out[str(species)] = {
                "family": family_name,
                "bands": bands,
                "dormant": bool(body.get("flux_dormant")),
                "disposition": str(body.get("runtime_disposition") or ""),
                "acquisition": str(body.get("acquisition_status") or ""),
            }
    return out


def _classify(row: dict[str, Any], T_K: float) -> tuple[str, float | None]:
    """Return (verdict, distance_K). distance is None when there is no band."""
    bands = row["bands"]
    if not bands:
        return ("NO BAND", None)
    # In-domain if ANY model covers T -- the engine may legitimately pick that
    # model. The nearest miss is reported when none do.
    for lo, hi, _ in bands:
        if lo <= T_K <= hi:
            return ("in domain", 0.0)
    gaps = []
    for lo, hi, _ in bands:
        gaps.append(T_K - hi if T_K > hi else lo - T_K)
    return ("EXTRAPOLATED", min(gaps))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--temperature-C", type=float, default=2200.0)
    ap.add_argument("--species", nargs="*", default=None,
                    help="restrict to these species (default: all non-dormant)")
    ap.add_argument("--all", action="store_true",
                    help="include flux-dormant species")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    T_K = args.temperature_C + 273.15
    rows = _species_rows(_load_rail())

    selected = {}
    for name, row in rows.items():
        if args.species is not None:
            if name in args.species:
                selected[name] = row
            continue
        if row["dormant"] and not args.all:
            continue
        selected[name] = row

    results = []
    for name, row in sorted(selected.items()):
        verdict, dist = _classify(row, T_K)
        results.append({
            "species": name, "verdict": verdict, "distance_K": dist,
            "bands": [[lo, hi] for lo, hi, _ in row["bands"]],
            "family": row["family"], "dormant": row["dormant"],
        })

    if args.json:
        print(json.dumps({"temperature_C": args.temperature_C,
                          "temperature_K": T_K, "results": results}, indent=1))
        return 0

    n_in = sum(1 for r in results if r["verdict"] == "in domain")
    n_ex = sum(1 for r in results if r["verdict"] == "EXTRAPOLATED")
    n_nb = sum(1 for r in results if r["verdict"] == "NO BAND")

    print(f"DOMAIN REPORT  T = {args.temperature_C:.0f} C ({T_K:.0f} K)")
    print(f"  species considered : {len(results)}"
          f"{'' if args.all else '  (flux-dormant excluded; --all to include)'}")
    print(f"  in domain          : {n_in}")
    print(f"  EXTRAPOLATED       : {n_ex}")
    print(f"  NO BAND RECORDED   : {n_nb}   <-- unknown, not in-domain")
    print()

    off = [r for r in results if r["verdict"] != "in domain"]
    off.sort(key=lambda r: (r["verdict"] != "NO BAND",
                            -(r["distance_K"] or 0.0)))
    if off:
        print(f"{'species':16} {'verdict':14} {'past band':>10}  bands (K)")
        for r in off:
            d = "" if r["distance_K"] is None else f"{r['distance_K']:.0f} K"
            bands = ", ".join(f"[{lo:.0f},{hi:.0f}]" for lo, hi in r["bands"]) or "-"
            print(f"{r['species'][:16]:16} {r['verdict']:14} {d:>10}  {bands[:60]}")
    if n_in:
        print()
        print("in domain: " + ", ".join(r["species"] for r in results
                                        if r["verdict"] == "in domain"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
