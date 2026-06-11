"""Terminal-product taxonomy data loader and fail-closed classifier."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from simulator.state import MOLAR_MASS


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_TAXONOMY_PATH = DATA_DIR / "ceramics_taxonomy.yaml"

WT_BASIS = "oxide_wt_pct"
MOL_BASIS = "oxide_mol"
NORMALIZED_WT_BASIS = "oxide_wt_pct_normalized_volatiles_free"
USER_LABEL_TERM = "terminal product"
UNCLASSIFIED_CLASS = "unclassified_concentrate"
EPSILON = 1e-9


@dataclass(frozen=True)
class _Fit:
    nodes: tuple[dict[str, Any], ...]
    fractions: tuple[float, ...]
    total_l1: float
    max_abs: float
    residual_oxides: dict[str, float]
    score: float


def load_terminal_product_taxonomy(
    path: Path | str = DEFAULT_TAXONOMY_PATH,
) -> dict[str, Any]:
    with Path(path).open() as handle:
        data = yaml.safe_load(handle)
    _validate_taxonomy(data, path)
    return data


def taxonomy_nodes_by_id(
    path: Path | str = DEFAULT_TAXONOMY_PATH,
) -> dict[str, dict[str, Any]]:
    data = load_terminal_product_taxonomy(path)
    return {node["id"]: node for node in data["nodes"]}


def classify_terminal_product(
    composition: Mapping[str, float],
    *,
    basis: str = WT_BASIS,
    residue_mass_kg: float | int | None = None,
    furnace_ceiling_c: float | int | None = None,
    temperature_profile_id: str | None = None,
    run_id: str | None = None,
    feedstock_id: str | None = None,
    terminal_product_account_or_artifact: str | None = None,
    taxonomy_path: Path | str = DEFAULT_TAXONOMY_PATH,
) -> dict[str, Any]:
    taxonomy = load_terminal_product_taxonomy(taxonomy_path)
    policy = taxonomy["match_policy"]
    oxide_wt_pct = _normalize_to_wt_pct(composition, basis)
    grade = _grade_block(oxide_wt_pct, taxonomy, residue_mass_kg)
    candidates = _classifiable_nodes(taxonomy)
    active_oxides = _active_oxide_set(candidates)
    ignored_oxides = set(policy.get("ignored_oxide_residuals", ()))
    ignored = {
        oxide: value
        for oxide, value in oxide_wt_pct.items()
        if oxide not in active_oxides
    }
    blocking = {
        oxide: value
        for oxide, value in ignored.items()
        if oxide not in ignored_oxides
        and value > float(policy["max_nonignored_unmodeled_oxide_wt_pct"])
    }
    modeled = {
        oxide: value
        for oxide, value in oxide_wt_pct.items()
        if oxide in active_oxides
    }
    modeled_total = sum(modeled.values())
    provenance = _provenance(
        taxonomy,
        policy,
        basis,
        furnace_ceiling_c,
        temperature_profile_id,
        run_id,
        feedstock_id,
        terminal_product_account_or_artifact,
        ignored,
        residual_wt_pct=None,
    )
    if blocking or modeled_total < float(policy["min_modeled_basis_wt_pct"]):
        return _unclassified_result(oxide_wt_pct, provenance, grade)

    modeled_target = _normalize_values(modeled)
    fits = _valid_fits(modeled_target, candidates, policy)
    if not fits:
        return _unclassified_result(oxide_wt_pct, provenance, grade)

    fits.sort(key=lambda fit: fit.score)
    best = fits[0]
    if len(fits) > 1:
        min_delta = float(policy["ambiguity"]["min_score_delta_wt_pct"])
        if best.total_l1 > 0.05 and fits[1].score - best.score < min_delta:
            return _unclassified_result(oxide_wt_pct, provenance, grade)

    residual = dict(best.residual_oxides)
    residual.update({oxide: value for oxide, value in ignored.items() if value > EPSILON})
    provenance = _provenance(
        taxonomy,
        policy,
        basis,
        furnace_ceiling_c,
        temperature_profile_id,
        run_id,
        feedstock_id,
        terminal_product_account_or_artifact,
        ignored,
        residual_wt_pct=sum(abs(value) for value in residual.values()),
    )
    provenance["caveat"] = "Normative composition match; not equilibrium proof."
    node_classes = {node["product_class"] for node in best.nodes}
    product_class = node_classes.pop() if len(node_classes) == 1 else "mixed"
    matched_nodes = [
        {
            "id": node["id"],
            "label": node["label"],
            "normative_fraction_wt_pct": round(fraction, 6),
            "product_class": node["product_class"],
            "evidence_tier": node["evidence_tier"],
        }
        for node, fraction in zip(best.nodes, best.fractions)
        if fraction > EPSILON
    ]
    assemblage = _assemblage(matched_nodes)
    return {
        "product_class": product_class,
        "match_status": "matched_single" if len(matched_nodes) == 1 else "matched_mixture",
        "user_label_term": USER_LABEL_TERM,
        "display_name": _display_name(furnace_ceiling_c, assemblage),
        "assemblage": assemblage,
        "grade": grade,
        "matched_nodes": matched_nodes,
        "evidence_tiers": {node["id"]: node["evidence_tier"] for node in best.nodes},
        "residual": {
            "total_l1_wt_pct": round(best.total_l1, 6),
            "max_major_oxide_abs_residual_wt_pct": round(best.max_abs, 6),
            "residual_oxides": {
                oxide: round(value, 6)
                for oxide, value in sorted(residual.items())
                if abs(value) > EPSILON
            },
        },
        "properties_panel": {
            "show": True,
            "property_basis": "matched_nodes",
        },
        "provenance": provenance,
    }


def _validate_taxonomy(data: Any, path: Path | str) -> None:
    if not isinstance(data, dict):
        raise ValueError(f"terminal-product taxonomy is malformed: {path}")
    for key in ("version", "taxonomy_name", "product_classes", "match_policy", "nodes", "sources"):
        if key not in data:
            raise ValueError(f"terminal-product taxonomy missing {key!r}: {path}")
    if data["taxonomy_name"] != "terminal_product_taxonomy":
        raise ValueError(f"unexpected taxonomy_name in {path}")
    nodes = data["nodes"]
    if not isinstance(nodes, list) or not nodes:
        raise ValueError(f"terminal-product taxonomy has no nodes: {path}")
    ids: set[str] = set()
    product_classes = set(data["product_classes"])
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError(f"terminal-product taxonomy node is malformed: {path}")
        for key in ("id", "product_class", "label", "match", "properties", "evidence_tier", "sources"):
            if key not in node:
                raise ValueError(f"terminal-product taxonomy node missing {key!r}: {node!r}")
        if node["id"] in ids:
            raise ValueError(f"duplicate terminal-product taxonomy node {node['id']!r}")
        ids.add(node["id"])
        if node["product_class"] not in product_classes:
            raise ValueError(f"unknown product_class for node {node['id']!r}")
        signature = node.get("oxide_signature_wt_pct")
        oxide_only_allowed = bool(node["match"].get("oxide_only_match_allowed"))
        if oxide_only_allowed and not isinstance(signature, dict):
            raise ValueError(f"oxide-matchable node missing oxide signature: {node['id']!r}")
        if signature is not None:
            total = sum(float(value) for value in signature.values())
            if abs(total - 100.0) > 0.2:
                raise ValueError(f"oxide signature does not close to 100 wt%: {node['id']!r}")


def _classifiable_nodes(taxonomy: Mapping[str, Any]) -> list[dict[str, Any]]:
    nodes = []
    for node in taxonomy["nodes"]:
        match = node.get("match", {})
        if (
            match.get("oxide_only_match_allowed")
            and node.get("oxide_signature_wt_pct")
            and node.get("status") != "future_out_of_domain_today"
        ):
            nodes.append(node)
    return nodes


def _active_oxide_set(nodes: Sequence[Mapping[str, Any]]) -> set[str]:
    oxides: set[str] = set()
    for node in nodes:
        oxides.update(node["oxide_signature_wt_pct"])
    return oxides


def _normalize_to_wt_pct(composition: Mapping[str, float], basis: str) -> dict[str, float]:
    if basis not in {WT_BASIS, MOL_BASIS, NORMALIZED_WT_BASIS}:
        raise ValueError(f"unsupported terminal-product composition basis: {basis}")
    positive = {oxide: float(value) for oxide, value in composition.items() if float(value) > 0.0}
    if not positive:
        raise ValueError("terminal-product composition must contain positive oxide values")
    if basis == MOL_BASIS:
        weighted: dict[str, float] = {}
        for oxide, mol in positive.items():
            if oxide not in MOLAR_MASS:
                raise ValueError(f"cannot convert oxide mol basis for unknown species {oxide!r}")
            weighted[oxide] = mol * float(MOLAR_MASS[oxide])
        return _normalize_values(weighted)
    return _normalize_values(positive)


def _normalize_values(values: Mapping[str, float]) -> dict[str, float]:
    total = sum(float(value) for value in values.values())
    if total <= 0.0:
        raise ValueError("cannot normalize zero terminal-product composition")
    return {key: float(value) / total * 100.0 for key, value in values.items()}


def _grade_block(
    oxide_wt_pct: Mapping[str, float],
    taxonomy: Mapping[str, Any],
    residue_mass_kg: float | int | None,
) -> dict[str, Any]:
    policy = taxonomy.get("value_grade_policy")
    if not isinstance(policy, Mapping):
        return {
            "basis": NORMALIZED_WT_BASIS,
            "residue_mass_kg": _normalized_residue_mass_kg(residue_mass_kg),
            "value_buckets": {},
            "coverage": {
                "reported_species": [],
                "omitted_value_buckets": [],
                "note": "No value-grade policy configured; no grade buckets reported.",
            },
        }
    mass_kg = _normalized_residue_mass_kg(residue_mass_kg)
    value_buckets: dict[str, dict[str, Any]] = {}
    omitted: list[dict[str, Any]] = []
    reported_species: set[str] = set()
    for bucket_id, bucket in policy.get("buckets", {}).items():
        species = [str(item) for item in bucket.get("species", ())]
        present_species = [
            species_id
            for species_id in species
            if float(oxide_wt_pct.get(species_id, 0.0)) > EPSILON
        ]
        if str(bucket.get("status", "")) == "future_out_of_domain_today":
            omitted.append(
                {
                    "bucket": bucket_id,
                    "label": bucket["label"],
                    "status": "future_out_of_domain_today",
                    "reason": "future_out_of_domain_today",
                    "coverage_note": bucket["coverage_note"],
                }
            )
            continue
        if not present_species:
            omitted.append(
                {
                    "bucket": bucket_id,
                    "label": bucket["label"],
                    "tracked_species": species,
                    "reason": "source_species_absent",
                    "coverage_note": bucket["coverage_note"],
                }
            )
            continue
        species_wt_pct = {
            species_id: round(float(oxide_wt_pct[species_id]), 6)
            for species_id in present_species
        }
        wt_pct = sum(float(oxide_wt_pct[species_id]) for species_id in present_species)
        reported_species.update(present_species)
        value_buckets[str(bucket_id)] = {
            "label": bucket["label"],
            "species_wt_pct": species_wt_pct,
            "wt_pct_of_residue": round(wt_pct, 6),
            "mass_kg": None if mass_kg is None else round(mass_kg * wt_pct / 100.0, 6),
        }
    return {
        "basis": policy.get("basis", NORMALIZED_WT_BASIS),
        "residue_mass_kg": mass_kg,
        "value_buckets": value_buckets,
        "coverage": {
            "reported_species": sorted(reported_species),
            "omitted_value_buckets": omitted,
            "note": (
                "Value buckets are reported only when the normalized input residue "
                "composition contains tracked species; omitted buckets are not zero grades."
            ),
        },
    }


def _normalized_residue_mass_kg(value: float | int | None) -> float | None:
    if value is None:
        return None
    mass = float(value)
    if mass < 0.0:
        raise ValueError("terminal-product residue_mass_kg must be non-negative")
    return round(mass, 6)


def _valid_fits(
    target: Mapping[str, float],
    candidates: Sequence[dict[str, Any]],
    policy: Mapping[str, Any],
) -> list[_Fit]:
    fits: list[_Fit] = []
    single_policy = policy["single_leaf"]
    mixture_policy = policy["mixture"]
    for node in candidates:
        if not node["match"].get("single_leaf_allowed", False):
            continue
        fit = _fit_subset((node,), target)
        if (
            fit
            and fit.total_l1 <= float(single_policy["max_total_l1_residual_wt_pct"])
            and fit.max_abs <= float(single_policy["max_major_oxide_abs_residual_wt_pct"])
        ):
            fits.append(fit)

    mixture_nodes = [node for node in candidates if node["match"].get("mixture_allowed", False)]
    max_phases = int(mixture_policy["max_phases"])
    for size in range(2, min(max_phases, len(mixture_nodes)) + 1):
        for subset in combinations(mixture_nodes, size):
            fit = _fit_subset(subset, target)
            if not fit:
                continue
            if fit.total_l1 > float(mixture_policy["max_total_l1_residual_wt_pct"]):
                continue
            if fit.max_abs > float(mixture_policy["max_major_oxide_abs_residual_wt_pct"]):
                continue
            if not _phase_fractions_allowed(fit, mixture_policy):
                continue
            fits.append(fit)
    return fits


def _fit_subset(
    nodes: Sequence[dict[str, Any]],
    target: Mapping[str, float],
) -> _Fit | None:
    oxides = sorted(
        set(target).union(
            oxide
            for node in nodes
            for oxide in node["oxide_signature_wt_pct"]
        )
    )
    matrix = [
        [float(node["oxide_signature_wt_pct"].get(oxide, 0.0)) / 100.0 for node in nodes]
        for oxide in oxides
    ]
    vector = [float(target.get(oxide, 0.0)) / 100.0 for oxide in oxides]
    sum_weight = 10.0
    matrix.append([sum_weight for _node in nodes])
    vector.append(sum_weight)
    weights = _least_squares(matrix, vector)
    if weights is None or any(weight < -1e-7 for weight in weights):
        return None
    weights = [0.0 if abs(weight) < 1e-7 else weight for weight in weights]
    weight_sum = sum(weights)
    if weight_sum <= EPSILON:
        return None
    weights = [weight / weight_sum for weight in weights]
    fitted: dict[str, float] = {}
    for oxide in oxides:
        fitted[oxide] = sum(
            weight * float(node["oxide_signature_wt_pct"].get(oxide, 0.0))
            for weight, node in zip(weights, nodes)
        )
    residual = {oxide: float(target.get(oxide, 0.0)) - fitted[oxide] for oxide in oxides}
    total_l1 = sum(abs(value) for value in residual.values())
    max_abs = max((abs(value) for value in residual.values()), default=0.0)
    phase_penalty = 0.01 * len(nodes)
    score = total_l1 + max_abs * 0.1 + phase_penalty
    ordered = sorted(
        zip(nodes, (weight * 100.0 for weight in weights)),
        key=lambda item: item[1],
        reverse=True,
    )
    return _Fit(
        nodes=tuple(node for node, _fraction in ordered),
        fractions=tuple(fraction for _node, fraction in ordered),
        total_l1=total_l1,
        max_abs=max_abs,
        residual_oxides=residual,
        score=score,
    )


def _least_squares(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float] | None:
    if not matrix:
        return None
    cols = len(matrix[0])
    normal = [[0.0 for _ in range(cols)] for _ in range(cols)]
    rhs = [0.0 for _ in range(cols)]
    for row, value in zip(matrix, vector):
        for i in range(cols):
            rhs[i] += row[i] * value
            for j in range(cols):
                normal[i][j] += row[i] * row[j]
    return _solve_linear_system(normal, rhs)


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    n = len(vector)
    augmented = [row[:] + [value] for row, value in zip(matrix, vector)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-12:
            return None
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        pivot_value = augmented[col][col]
        for idx in range(col, n + 1):
            augmented[col][idx] /= pivot_value
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            if abs(factor) < 1e-15:
                continue
            for idx in range(col, n + 1):
                augmented[row][idx] -= factor * augmented[col][idx]
    return [augmented[row][n] for row in range(n)]


def _phase_fractions_allowed(fit: _Fit, policy: Mapping[str, Any]) -> bool:
    min_nontrace = float(policy["min_nontrace_phase_fraction_wt_pct"])
    trace_min, trace_max = [float(value) for value in policy["trace_phase_fraction_range_wt_pct"]]
    for node, fraction in zip(fit.nodes, fit.fractions):
        if fraction >= min_nontrace:
            continue
        if node["match"].get("trace_allowed") and trace_min <= fraction <= trace_max:
            continue
        return False
    return True


def _assemblage(matched_nodes: Sequence[Mapping[str, Any]]) -> str:
    labels = [str(node["label"]).split(" / ")[0] for node in matched_nodes]
    if len(labels) == 1:
        return labels[0]
    return "mixture of " + " + ".join(labels)


def _display_name(furnace_ceiling_c: float | int | None, assemblage: str | None = None) -> str:
    if furnace_ceiling_c is None:
        prefix = USER_LABEL_TERM
    else:
        prefix = f"{USER_LABEL_TERM} at {_format_temperature(furnace_ceiling_c)} C"
    return f"{prefix}: {assemblage}" if assemblage else prefix


def _format_temperature(value: float | int) -> str:
    value_float = float(value)
    return str(int(value_float)) if value_float.is_integer() else f"{value_float:g}"


def _provenance(
    taxonomy: Mapping[str, Any],
    policy: Mapping[str, Any],
    basis: str,
    furnace_ceiling_c: float | int | None,
    temperature_profile_id: str | None,
    run_id: str | None,
    feedstock_id: str | None,
    terminal_product_account_or_artifact: str | None,
    ignored_oxides: Mapping[str, float],
    residual_wt_pct: float | None,
) -> dict[str, Any]:
    normalized_basis = (
        NORMALIZED_WT_BASIS if basis != MOL_BASIS else f"{NORMALIZED_WT_BASIS}_from_oxide_mol"
    )
    return {
        "taxonomy_version": taxonomy["version"],
        "furnace_ceiling_c": furnace_ceiling_c,
        "temperature_profile_id": temperature_profile_id,
        "basis": normalized_basis,
        "classifier_policy_id": policy["policy_id"],
        "run_id": run_id,
        "feedstock_id": feedstock_id,
        "terminal_product_account_or_artifact": terminal_product_account_or_artifact,
        "ignored_oxides": {
            oxide: round(value, 6)
            for oxide, value in sorted(ignored_oxides.items())
            if value > EPSILON
        },
        "residual_wt_pct": None if residual_wt_pct is None else round(residual_wt_pct, 6),
    }


def _unclassified_result(
    oxide_wt_pct: Mapping[str, float],
    provenance: Mapping[str, Any],
    grade: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "grade": dict(grade),
        "product_class": UNCLASSIFIED_CLASS,
        "match_status": "no_match",
        "user_label_term": USER_LABEL_TERM,
        "display_name": _display_name(provenance["furnace_ceiling_c"]),
        "composition_only": True,
        "oxide_wt_pct": {
            oxide: round(value, 6)
            for oxide, value in sorted(oxide_wt_pct.items())
            if value > EPSILON
        },
        "evidence_tiers": {},
        "provenance": dict(provenance),
    }
