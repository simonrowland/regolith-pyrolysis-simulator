#!/usr/bin/env python3
"""Re-run and compactly summarize the t-605 oxidative sweep for t-622."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import io
import math
from pathlib import Path
import re
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.chemistry.melt_activity import melt_oxide_activity  # noqa: E402
from simulator.physical_constants import (  # noqa: E402
    GAS_CONSTANT,
    MELT_DISSOCIATION_PO2_MAX_BAR,
    MELT_DISSOCIATION_PO2_MIN_BAR,
)
from simulator.state import MOLAR_MASS  # noqa: E402
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog  # noqa: E402
from simulator.vapour_rail.domain_policy import (  # noqa: E402
    declared_domain_transition,
)


DEFAULT_OUTPUT = (
    ROOT
    / "docs-private"
    / "research"
    / "2026-08-12-t622-mno-coo"
    / "oxidative-window-envelope.csv"
)
TARGETS = ("Cr", "Fe", "Mg", "Na", "K", "Si", "Ti", "Al", "Mn", "Co", "Ni", "P")
TEMPERATURES_K = (1400.0, 1600.0, 1800.0, 2000.0, 2200.0)
LOG_FO2_STEP = 0.1
CANDIDATE_BY_ELEMENT = {"Mn": "MnO_gas", "Co": "CoO_gas"}
SIGMA_G_J_PER_MOL = {
    "MnO_gas": 7531.2,
    "CoO_gas": 12543.093176,
}
FIELDS = (
    "scenario",
    "temperature_K",
    "element",
    "fO2_grid_points",
    "log10_fO2_min_bar",
    "log10_fO2_max_bar",
    "modeled_carriers",
    "element_coverage_status",
    "interpretation_note",
    "window_state_counts",
    "dominant_nominal_carriers",
    "dominant_lower_envelope_carriers",
    "dominant_upper_envelope_carriers",
    "nominal_modeled_element_pressure_min_Pa",
    "nominal_modeled_element_pressure_max_Pa",
    "lower_modeled_element_pressure_min_Pa",
    "upper_modeled_element_pressure_max_Pa",
    "candidate_carrier",
    "candidate_evaluable_grid_points",
    "candidate_refusal_codes",
    "candidate_sigma_log10_pressure_dex",
    "candidate_nominal_pressure_min_Pa",
    "candidate_nominal_pressure_max_Pa",
    "candidate_lower_pressure_min_Pa",
    "candidate_upper_pressure_max_Pa",
    "candidate_nominal_fraction_min",
    "candidate_nominal_fraction_max",
    "candidate_conservative_lower_fraction_min",
    "candidate_conservative_upper_fraction_max",
    "candidate_validation_status",
    "candidate_authority_class",
)


def _formula_counts(formula: str) -> dict[str, float]:
    neutral = re.sub(r"\([^()]+\)$", "", formula)
    matches = list(re.finditer(r"([A-Z][a-z]?)(\d+(?:\.\d+)?|\.\d+)?", neutral))
    if not matches or "".join(match.group(0) for match in matches) != neutral:
        raise ValueError(f"unsupported formula {formula!r}")
    counts: dict[str, float] = defaultdict(float)
    for match in matches:
        counts[match.group(1)] += float(match.group(2) or 1.0)
    return dict(counts)


def _source_activity(
    compiled,
    raw_row: dict,
    account_mol: dict[str, float],
    temperature_K: float,
) -> tuple[float | None, str | None]:
    evaluator = compiled.evaluator
    if evaluator is None or not evaluator.activity_exponent:
        return None, None
    declaration = compiled.source_reaction_activity
    if declaration is None:
        raise ValueError(f"{compiled.species_id}: activity exponent without declaration")
    parent_oxide = str(raw_row.get("parent_oxide") or declaration.component_id)
    activity = melt_oxide_activity(
        parent_oxide,
        account_mol,
        temperature_K=temperature_K,
    )
    if activity is None or activity.activity <= 0.0:
        return None, "missing_positive_mare_activity"
    if raw_row.get("source_activity_basis") == "parent_oxide":
        return activity.thermodynamic_parent_activity(), None
    return activity.activity, None


def _window_state(results: list[dict]) -> str:
    computable = [row for row in results if "evaluation" in row]
    if computable:
        return (
            "WINDOWS-COMPUTABLE-IN-DECLARED-DOMAIN"
            if len(computable) != len(results)
            else "WINDOWS-COMPUTABLE"
        )
    if not results:
        return "WINDOWS-UNKNOWABLE-UNTIL-MODELED"
    refusal_codes = {row.get("refusal_code") for row in results}
    if refusal_codes == {"missing_positive_mare_activity"}:
        return "WINDOWS-NOT-APPLICABLE-NO-SOURCE-INVENTORY"
    if refusal_codes and refusal_codes <= {"outside_declared_evaluator_domain"}:
        return "WINDOWS-REFUSED-OUTSIDE-DECLARED-DOMAIN"
    return "WINDOWS-UNKNOWABLE-UNTIL-MODELED"


def _joined(values) -> str:
    return ";".join(sorted({str(value) for value in values if value}))


def _minimum(values):
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _maximum(values):
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _render() -> str:
    payload = yaml.safe_load((ROOT / "data" / "vapor_pressures.yaml").read_text())
    feedstocks = yaml.safe_load((ROOT / "data" / "feedstocks.yaml").read_text())
    composition = feedstocks["lunar_mare_low_ti"]["composition_wt_pct"]
    account_mol = {
        oxide: float(wt) / float(MOLAR_MASS[oxide])
        for oxide, wt in composition.items()
        if oxide in MOLAR_MASS
        and (
            oxide.endswith("O")
            or oxide in {"SiO2", "TiO2", "Al2O3", "Fe2O3", "Cr2O3", "P2O5"}
        )
    }
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    species_by_element: dict[str, list] = defaultdict(list)
    raw_rows: dict[str, dict] = {}
    for species_id, compiled in catalog.species.items():
        if compiled.evaluator is None:
            continue
        if compiled.code_metadata.source_account != "process.cleaned_melt":
            continue
        counts = _formula_counts(compiled.formula)
        elements = set(counts) - {"O"}
        if len(elements) != 1:
            continue
        element = next(iter(elements))
        if element not in TARGETS:
            continue
        species_by_element[element].append(compiled)
        raw_rows[species_id] = payload["families"][compiled.family_id][
            "physical_properties"
        ]["species"][species_id]

    log_min = math.log10(MELT_DISSOCIATION_PO2_MIN_BAR)
    log_max = math.log10(MELT_DISSOCIATION_PO2_MAX_BAR)
    log_grid = tuple(
        round(log_min + index * LOG_FO2_STEP, 10)
        for index in range(int(round((log_max - log_min) / LOG_FO2_STEP)) + 1)
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()

    for scenario in ("unit_activity", "mare_activity_proxy"):
        for temperature_K in TEMPERATURES_K:
            aggregates: dict[str, list[dict]] = defaultdict(list)
            for log_fO2 in log_grid:
                pO2_bar = 10.0**log_fO2
                for element in TARGETS:
                    results: list[dict] = []
                    for compiled in species_by_element[element]:
                        evaluator = compiled.evaluator
                        assert evaluator is not None
                        raw_row = raw_rows[compiled.species_id]
                        activity = None
                        activity_refusal = None
                        if evaluator.activity_exponent:
                            if scenario == "unit_activity":
                                activity = 1.0
                            else:
                                activity, activity_refusal = _source_activity(
                                    compiled, raw_row, account_mol, temperature_K
                                )
                        transition = declared_domain_transition(compiled, temperature_K)
                        counts = _formula_counts(compiled.formula)
                        n_element = counts[element]
                        sigma_dex = SIGMA_G_J_PER_MOL.get(compiled.species_id, 0.0) / (
                            GAS_CONSTANT * temperature_K * math.log(10.0)
                        )
                        result = {
                            "compiled": compiled,
                            "raw_row": raw_row,
                            "n_element": n_element,
                            "sigma_dex": sigma_dex,
                            "refusal_code": transition.refusal_code or activity_refusal or "",
                        }
                        if transition.refuses or activity_refusal:
                            results.append(result)
                            continue
                        evaluation = evaluator.evaluate(
                            temperature_K,
                            source_activity=activity,
                            pO2_bar=(pO2_bar if evaluator.pO2_exponent else None),
                        )
                        nominal = evaluation.pressure_pa
                        result.update(
                            {
                                "evaluation": evaluation,
                                "nominal": nominal,
                                "lower": nominal * 10.0**-sigma_dex,
                                "upper": nominal * 10.0**sigma_dex,
                            }
                        )
                        results.append(result)

                    computable = [row for row in results if "evaluation" in row]
                    nominal_total = sum(
                        row["nominal"] * row["n_element"] for row in computable
                    )
                    lower_total = sum(
                        row["lower"] * row["n_element"] for row in computable
                    )
                    upper_total = sum(
                        row["upper"] * row["n_element"] for row in computable
                    )
                    dominant = lambda field: (  # noqa: E731
                        max(computable, key=lambda row: row[field])["compiled"].species_id
                        if computable
                        else ""
                    )
                    candidate_id = CANDIDATE_BY_ELEMENT.get(element)
                    candidate = next(
                        (
                            row
                            for row in results
                            if row["compiled"].species_id == candidate_id
                        ),
                        None,
                    )
                    point = {
                        "state": _window_state(results),
                        "nominal_total": nominal_total if computable else None,
                        "lower_total": lower_total if computable else None,
                        "upper_total": upper_total if computable else None,
                        "dominant_nominal": dominant("nominal"),
                        "dominant_lower": dominant("lower"),
                        "dominant_upper": dominant("upper"),
                        "candidate": candidate,
                    }
                    if candidate is not None and "evaluation" in candidate:
                        n_element = candidate["n_element"]
                        point.update(
                            {
                                "candidate_nominal_fraction": (
                                    candidate["nominal"] * n_element / nominal_total
                                    if nominal_total > 0.0
                                    else None
                                ),
                                "candidate_lower_fraction": (
                                    candidate["lower"] * n_element / upper_total
                                    if upper_total > 0.0
                                    else None
                                ),
                                "candidate_upper_fraction": (
                                    min(1.0, candidate["upper"] * n_element / lower_total)
                                    if lower_total > 0.0
                                    else None
                                ),
                            }
                        )
                    aggregates[element].append(point)

            for element in TARGETS:
                points = aggregates[element]
                incomplete_co = element == "Co"
                states = Counter(point["state"] for point in points)
                candidate_rows = [
                    point["candidate"]
                    for point in points
                    if point["candidate"] is not None
                ]
                candidate_evaluable = [
                    row for row in candidate_rows if "evaluation" in row
                ]
                candidate_template = candidate_rows[0] if candidate_rows else None
                writer.writerow(
                    {
                        "scenario": scenario,
                        "temperature_K": f"{temperature_K:.1f}",
                        "element": element,
                        "fO2_grid_points": len(log_grid),
                        "log10_fO2_min_bar": f"{log_grid[0]:.1f}",
                        "log10_fO2_max_bar": f"{log_grid[-1]:.1f}",
                        "modeled_carriers": _joined(
                            compiled.species_id for compiled in species_by_element[element]
                        ),
                        "element_coverage_status": (
                            "incomplete_atomic_Co_not_compiled"
                            if incomplete_co
                            else "catalog_carriers_only"
                        ),
                        "interpretation_note": (
                            "CoO pressure is computable, but the embedded Co base screen is not a standalone "
                            "compiled carrier; Co dominance, selectivity, totals, and carrier fractions are not claimed."
                            if incomplete_co
                            else "Full t-605 catalog-carrier comparison; unmodeled carriers remain outside this screen."
                        ),
                        "window_state_counts": _joined(
                            f"{state}:{count}" for state, count in states.items()
                        ),
                        "dominant_nominal_carriers": _joined(
                            ()
                            if incomplete_co
                            else (point["dominant_nominal"] for point in points)
                        ),
                        "dominant_lower_envelope_carriers": _joined(
                            ()
                            if incomplete_co
                            else (point["dominant_lower"] for point in points)
                        ),
                        "dominant_upper_envelope_carriers": _joined(
                            ()
                            if incomplete_co
                            else (point["dominant_upper"] for point in points)
                        ),
                        "nominal_modeled_element_pressure_min_Pa": _minimum(
                            ()
                            if incomplete_co
                            else (point["nominal_total"] for point in points)
                        ),
                        "nominal_modeled_element_pressure_max_Pa": _maximum(
                            ()
                            if incomplete_co
                            else (point["nominal_total"] for point in points)
                        ),
                        "lower_modeled_element_pressure_min_Pa": _minimum(
                            ()
                            if incomplete_co
                            else (point["lower_total"] for point in points)
                        ),
                        "upper_modeled_element_pressure_max_Pa": _maximum(
                            ()
                            if incomplete_co
                            else (point["upper_total"] for point in points)
                        ),
                        "candidate_carrier": CANDIDATE_BY_ELEMENT.get(element, ""),
                        "candidate_evaluable_grid_points": len(candidate_evaluable),
                        "candidate_refusal_codes": _joined(
                            row["refusal_code"] for row in candidate_rows
                        ),
                        "candidate_sigma_log10_pressure_dex": (
                            candidate_template["sigma_dex"]
                            if candidate_template is not None
                            else None
                        ),
                        "candidate_nominal_pressure_min_Pa": _minimum(
                            row.get("nominal") for row in candidate_evaluable
                        ),
                        "candidate_nominal_pressure_max_Pa": _maximum(
                            row.get("nominal") for row in candidate_evaluable
                        ),
                        "candidate_lower_pressure_min_Pa": _minimum(
                            row.get("lower") for row in candidate_evaluable
                        ),
                        "candidate_upper_pressure_max_Pa": _maximum(
                            row.get("upper") for row in candidate_evaluable
                        ),
                        "candidate_nominal_fraction_min": _minimum(
                            ()
                            if incomplete_co
                            else (
                                point.get("candidate_nominal_fraction") for point in points
                            )
                        ),
                        "candidate_nominal_fraction_max": _maximum(
                            ()
                            if incomplete_co
                            else (
                                point.get("candidate_nominal_fraction") for point in points
                            )
                        ),
                        "candidate_conservative_lower_fraction_min": _minimum(
                            ()
                            if incomplete_co
                            else (
                                point.get("candidate_lower_fraction") for point in points
                            )
                        ),
                        "candidate_conservative_upper_fraction_max": _maximum(
                            ()
                            if incomplete_co
                            else (
                                point.get("candidate_upper_fraction") for point in points
                            )
                        ),
                        "candidate_validation_status": (
                            candidate_template["compiled"].validation_status.value
                            if candidate_template is not None
                            else ""
                        ),
                        "candidate_authority_class": (
                            candidate_template["raw_row"].get("authority_class")
                            if candidate_template is not None
                            else ""
                        ),
                    }
                )
    return output.getvalue()


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = _render()
    if args.check:
        if not args.output.is_file() or args.output.read_text() != rendered:
            raise SystemExit(f"stale oxidative-window evidence: {args.output}")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    grid_evaluations = 2 * len(TEMPERATURES_K) * 321 * len(TARGETS)
    print(
        "PASS: full t-605 grid rerun with MnO/CoO D0 envelopes; "
        f"{grid_evaluations} element-grid evaluations summarized in "
        f"{2 * len(TEMPERATURES_K) * len(TARGETS)} rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
