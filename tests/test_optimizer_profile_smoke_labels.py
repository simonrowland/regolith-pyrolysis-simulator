import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.optimize.objective import (
    COMPOSITION_TARGET_METRIC_PREFIX,
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    SSO2_OWNER_RECIPE_ID,
    _MASS_OBJECTIVE_METRICS,
    c0_smoke_withheld_objective_note,
    c0_smoke_withheld_weight_fraction,
    compute_objectives,
    objective_definitions,
    objective_importance_evidence,
    unrenormalized_study_scalar_weights,
)
from simulator.optimize.profiles import load_profile


PROFILE_DIR = Path(__file__).resolve().parents[1] / "data" / "optimize_profiles"
# INVARIANT: a C0 smoke profile never scores a final product. The registry is
# every mass-product metric plus the Stage-3 silica certification score, not
# the handful of metrics a review already named. Each one is absent from every
# scoring path, named in the withhold notice, and never entered as 0.0.
FINAL_PRODUCT_METRICS = tuple(sorted(_MASS_OBJECTIVE_METRICS | {SSO2_OWNER_RECIPE_ID}))
PRODUCT_OBJECTIVES = frozenset(FINAL_PRODUCT_METRICS)
_SMOKE_LABEL = (
    "C0 SMOKE ONLY: product objectives are screening proxies, not realized products."
)


def test_c0_profiles_with_product_objectives_are_labeled_smoke() -> None:
    unlabeled = []
    for path in sorted(PROFILE_DIR.glob("*.yaml")):
        profile = yaml.safe_load(path.read_text())
        run = profile.get("run", {})
        metrics = {objective.get("metric") for objective in profile.get("objectives", [])}
        if run.get("campaign") != "C0" or not metrics.intersection(PRODUCT_OBJECTIVES):
            continue
        labels = (
            profile.get("description", ""),
            profile.get("objective_emphasis", ""),
        )
        if any(
            not isinstance(label, str) or not label.startswith("C0 SMOKE ONLY:")
            for label in labels
        ):
            unlabeled.append(path.name)

    assert not unlabeled, (
        "C0 does not realize these final product objectives; label the profile "
        f"as a screening smoke run: {', '.join(unlabeled)}"
    )


def _objective_row(metric: str, *, weight: float, units: str) -> dict:
    return {
        "metric": metric,
        "sense": "maximize" if units != "h" and units != "kWh" else "minimize",
        "units": units,
        "weight": weight,
        "rationale": f"test {metric}",
    }


def _smoke_profile(*product_rows: dict) -> dict:
    """C0 smoke profile: the product rows plus two retained operational metrics."""

    return {
        "profile_id": "c0-smoke-invariant",
        "description": _SMOKE_LABEL,
        "objective_emphasis": _SMOKE_LABEL,
        "objectives": [
            *product_rows,
            _objective_row(
                ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
                weight=0.10,
                units="kWh",
            ),
            _objective_row("duration_h", weight=0.06, units="h"),
        ],
        "run": {"campaign": "C0", "hours": 1, "mass_kg": 1000.0},
    }


def _stub_execution() -> SimpleNamespace:
    """Run surface whose final-product readers are zero or missing.

    A scored oxygen or Stage-3 silica objective becomes 0.0 here. The
    invariant says that 0.0 never enters the objective vector.
    """

    simulator = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
        energy_electrical_plus_evaporation_cumulative_kWh=10.0,
        melt=SimpleNamespace(hour=4),
        record=SimpleNamespace(total_hours=4.0),
    )
    return SimpleNamespace(simulator=simulator)


def _assert_withheld(profile: dict, withheld_metrics: set[str], *, label: str) -> None:
    from simulator.optimize.evaluate import _notes_with_c0_smoke_withhold

    evidence = objective_importance_evidence(profile)
    withheld_rows = [row for row in evidence if row.metric in withheld_metrics]
    retained_rows = [row for row in evidence if row.metric not in withheld_metrics]
    assert withheld_rows, label
    assert retained_rows, label
    definitions = objective_definitions(profile)
    scored = {definition.metric for definition in definitions}
    leaked = scored & withheld_metrics
    assert not leaked, f"{label} scores unclaimed products {sorted(leaked)}"
    mapping = compute_objectives(profile, _stub_execution()).as_mapping()
    leaked_values = {metric: mapping[metric] for metric in withheld_metrics if metric in mapping}
    assert not leaked_values, (
        f"{label} entered unclaimed products into the objective vector: {leaked_values}"
    )
    assert [definition.metric for definition in definitions] == [
        row.metric for row in retained_rows
    ]
    total = math.fsum(row.weight for row in evidence)
    withheld_weight = math.fsum(row.weight for row in withheld_rows)
    fraction = c0_smoke_withheld_weight_fraction(profile)
    assert fraction is not None
    assert fraction == withheld_weight / total
    assert 0.0 < fraction < 1.0, label
    note = c0_smoke_withheld_objective_note(profile)
    assert note is not None
    assert note.startswith("campaign_does_not_claim_product: ")
    assert note.count("reason=campaign_does_not_claim_product") == len(withheld_rows)
    for row in withheld_rows:
        assert (
            f"{row.metric} weight={format(row.weight, '.12g')} "
            "reason=campaign_does_not_claim_product"
        ) in note, label
    assert f"withheld_weight_fraction={format(fraction, '.12g')}" in note, label
    assert _notes_with_c0_smoke_withhold((), profile) == (note,)
    weights = unrenormalized_study_scalar_weights(profile)
    # Declared retained weights. Renormalizing would divide each by
    # (1 - fraction) and make them sum to `total`.
    assert weights == tuple(row.weight for row in retained_rows)
    assert math.isclose(
        math.fsum(weights),
        total - withheld_weight,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert not math.isclose(math.fsum(weights), total)
    # A unit score on each retained objective is the retained weight sum.
    # A withheld product must not contribute a 0.0 term to that sum.
    scalar = math.fsum(weights)
    assert scalar > 0.0
    assert all(metric not in scored for metric in withheld_metrics)


@pytest.mark.parametrize("metric", FINAL_PRODUCT_METRICS)
def test_c0_smoke_withholds_every_final_product_metric(metric: str) -> None:
    """Every final-product metric, not only glass and metal, stays off the score."""

    units = "score_0_1" if metric == SSO2_OWNER_RECIPE_ID else "kg"
    profile = _smoke_profile(_objective_row(metric, weight=0.50, units=units))
    _assert_withheld(profile, {metric}, label=metric)


def test_c0_smoke_withholds_composition_target_objectives() -> None:
    """Composition targets are a product-claim branch of the same chokepoint."""

    metric = f"{COMPOSITION_TARGET_METRIC_PREFIX}glass"
    profile = _smoke_profile(
        {
            "type": "composition_target",
            "id": "glass",
            "metric": metric,
            "sense": "maximize",
            "units": "score_0_1",
            "weight": 0.50,
            "rationale": "stage-3 glass composition",
            "target": {},
        }
    )
    _assert_withheld(profile, {metric}, label=metric)


def test_shipped_c0_smoke_profiles_withhold_every_declared_final_product() -> None:
    """Every shipped profile, every declared final-product metric."""

    paths = sorted(PROFILE_DIR.glob("*.yaml"))
    checked = 0
    for path in paths:
        profile = load_profile(path.stem)
        if (profile.get("run") or {}).get("campaign") != "C0":
            continue
        declared = {
            objective.get("metric") for objective in profile.get("objectives", [])
        }
        withheld = declared & set(FINAL_PRODUCT_METRICS)
        if not withheld:
            continue
        _assert_withheld(profile, withheld, label=path.name)
        checked += 1
    # Every shipped profile is a C0 smoke run that declares a final product.
    assert paths
    assert checked == len(paths)


def test_scoring_strategies_use_the_withhold_chokepoint() -> None:
    """Strategy objective vectors are objective_definitions, not a second parser."""

    pytest.importorskip("optuna")
    from simulator.optimize.recipe import RecipeSchema
    from simulator.optimize.strategy.bayesian import OptunaTPEStrategy
    from simulator.optimize.strategy.genetic import OptunaNSGA2Strategy
    from simulator.optimize.strategy.staged import StagedStrategy

    schema = RecipeSchema()
    strategies = (OptunaTPEStrategy, OptunaNSGA2Strategy, StagedStrategy)
    for metric in FINAL_PRODUCT_METRICS:
        units = "score_0_1" if metric == SSO2_OWNER_RECIPE_ID else "kg"
        profile = _smoke_profile(_objective_row(metric, weight=0.50, units=units))
        expected = tuple(definition.metric for definition in objective_definitions(profile))
        assert metric not in expected
        for strategy_cls in strategies:
            strategy = strategy_cls(schema, seed=0, objective_profile=profile)
            if strategy_cls is StagedStrategy:
                got = tuple(definition.metric for definition in strategy._definitions)
            else:
                got = tuple(strategy.objective_metrics)
            assert got == expected, f"{strategy_cls.__name__} {metric}"
            assert metric not in got
