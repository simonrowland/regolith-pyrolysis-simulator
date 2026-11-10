"""Campaign product-claim gate for optimizer scoring.

INVARIANT: no product objective is scored unless the evaluated campaign's
setpoints ``products`` claim that product. Otherwise it is absent from
objective definitions, ``compute_objectives``, strategy metrics, and the study
scalar. It is named in a notice with its weight, the reason
``campaign_does_not_claim_product``, and the withheld weight fraction. It is
never entered as 0.0. Retained weights are not renormalized. Description text
is not an eligibility input. An absent, blank, or unknown campaign claims
nothing. Operational metrics (energy, duration, and dynamic species ``*_kg``
such as ``Fe_kg``) are not product objectives and stay.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.config import DEFAULT_DATA_DIR
from simulator.optimize.objective import (
    COMPOSITION_TARGET_METRIC_PREFIX,
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    SSO2_OWNER_RECIPE_ID,
    _COMPOSITION_AUTHORIZING_CLAIM_KEYS,
    _MASS_OBJECTIVE_METRICS,
    _METAL_PRODUCT_CLAIM_KEYS,
    _OBJECTIVE_PRODUCT_CLAIM_KEYS,
    _SETPOINT_CAMPAIGN_ALIASES,
    campaign_claims_product_objective,
    campaign_product_claim_keys,
    compute_objectives,
    objective_definitions,
    objective_importance_evidence,
    unclaimed_product_withheld_objective_note,
    unclaimed_product_withheld_weight_fraction,
    unrenormalized_study_scalar_weights,
)
from simulator.optimize.profiles import load_profile
from simulator.optimize.recipe import RecipePatch
from simulator.optimize.study import StudyRecord, _study_progress


PROFILE_DIR = Path(__file__).resolve().parents[1] / "data" / "optimize_profiles"
# The registry is every mass-product metric plus the Stage-3 silica
# certification score, not the handful of metrics a review already named.
FINAL_PRODUCT_METRICS = tuple(sorted(_MASS_OBJECTIVE_METRICS | {SSO2_OWNER_RECIPE_ID}))
PRODUCT_OBJECTIVES = frozenset(FINAL_PRODUCT_METRICS)
_SMOKE_LABEL = (
    "C0 SMOKE ONLY: product objectives are screening proxies, not realized products."
)
# Literal claim table. Production must match this; the membership oracle below
# uses these sets, not ``campaign_claims_product_objective``, so a shared
# mistake in the helper cannot hide a scoring leak.
_LITERAL_CLAIM_KEYS = {
    "pure_silica_glass_kg": frozenset({"SiO2_glass"}),
    "industrial_mixed_glass_kg": frozenset({"CMAS_glass", "industrial_mixed_glass"}),
    "refractory_ceramic_rump_kg": frozenset({"refractory_ceramic_rump"}),
    "metals_total_kg": frozenset({"Fe", "Mg", "Ti"}),
    "O2_kg": frozenset({"O2"}),
    "o2_kg": frozenset({"O2"}),
    "oxygen_kg": frozenset({"O2"}),
    "oxygen_stored_kg": frozenset({"O2"}),
    "oxygen_vented_kg": frozenset({"O2"}),
    SSO2_OWNER_RECIPE_ID: frozenset({"SiO2_glass"}),
}
_LITERAL_COMPOSITION_KEYS = frozenset({
    "SiO2_glass",
    "CMAS_glass",
    "industrial_mixed_glass",
    "refractory_ceramic_rump",
    "Fe",
    "Mg",
    "Ti",
    "O2",
})
_LITERAL_ALIASES = {
    "C2A": "C2A_continuous",
    "C2A_staged": "C2A_staged",
    "C3_K": "C3",
    "C3_NA": "C3",
}
# metals_plus_o2_kg is the combined class: O2 and one of Fe/Mg/Ti. One of the
# two is not that product. native_Fe is not a metal key.
_METAL_KEYS = frozenset({"Fe", "Mg", "Ti"})
_RETAINED_ENERGY_WEIGHT = 0.10
_RETAINED_DURATION_WEIGHT = 0.06
_STUB_ENERGY_KWH = 10.0
_STUB_DURATION_H = 4.0
# Furnace penalty 0 leaves the cost factor at 1. Minimized energy and duration
# are negated, so the study scalar is 0.10 * -10 + 0.06 * -4.
_SYNTHETIC_SCALAR = math.fsum((
    _RETAINED_ENERGY_WEIGHT * -_STUB_ENERGY_KWH,
    _RETAINED_DURATION_WEIGHT * -_STUB_DURATION_H,
))
_COMPOSITION_METRIC = f"{COMPOSITION_TARGET_METRIC_PREFIX}glass"
_PROSE_VARIANTS = (
    "both-smoke",
    "description-edited",
    "emphasis-edited",
    "both-edited",
    "description-missing",
    "emphasis-missing",
    "both-missing",
    "empty-strings",
    "lowercase-prefix",
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


def test_claim_table_matches_the_literal_product_map() -> None:
    assert dict(_OBJECTIVE_PRODUCT_CLAIM_KEYS) == _LITERAL_CLAIM_KEYS
    assert _METAL_PRODUCT_CLAIM_KEYS == _METAL_KEYS
    assert _COMPOSITION_AUTHORIZING_CLAIM_KEYS == _LITERAL_COMPOSITION_KEYS
    assert dict(_SETPOINT_CAMPAIGN_ALIASES) == _LITERAL_ALIASES
    assert "metals_plus_o2_kg" not in _OBJECTIVE_PRODUCT_CLAIM_KEYS


def _setpoints_product_keys() -> dict[str, frozenset[str]]:
    """Top-level ``campaigns.<id>.products`` keys, read from the YAML itself."""

    loaded = yaml.safe_load((DEFAULT_DATA_DIR / "setpoints.yaml").read_text())
    campaigns = loaded["campaigns"]
    claims: dict[str, frozenset[str]] = {}
    for name, cfg in campaigns.items():
        products = cfg.get("products") if isinstance(cfg, dict) else None
        if isinstance(products, dict):
            claims[str(name)] = frozenset(str(key) for key in products)
        else:
            claims[str(name)] = frozenset()
    return claims


def test_campaign_product_claims_match_setpoints_yaml() -> None:
    oracle = _setpoints_product_keys()
    assert oracle, "setpoints campaigns block was not read"
    for campaign_id, keys in oracle.items():
        assert campaign_product_claim_keys(campaign_id) == keys
    for alias, target in _LITERAL_ALIASES.items():
        assert campaign_product_claim_keys(alias) == oracle[target]
    assert campaign_product_claim_keys(None) == frozenset()
    assert campaign_product_claim_keys("") == frozenset()
    assert campaign_product_claim_keys("   ") == frozenset()
    assert campaign_product_claim_keys("not-a-campaign") == frozenset()


def _oracle_claims(campaign_id: str | None) -> frozenset[str]:
    if not isinstance(campaign_id, str) or not campaign_id.strip():
        return frozenset()
    oracle = _setpoints_product_keys()
    key = _LITERAL_ALIASES.get(campaign_id.strip(), campaign_id.strip())
    return oracle.get(key, frozenset())


def _oracle_claims_metric(metric: str, claim_keys: frozenset[str], *, composition: bool) -> bool:
    if composition or metric.startswith(COMPOSITION_TARGET_METRIC_PREFIX):
        return bool(claim_keys & _LITERAL_COMPOSITION_KEYS)
    if metric == "metals_plus_o2_kg":
        return "O2" in claim_keys and bool(claim_keys & _METAL_KEYS)
    required = _LITERAL_CLAIM_KEYS.get(metric)
    if required is None:
        return True
    return bool(claim_keys & required)


def _objective_row(metric: str, *, weight: float, units: str) -> dict:
    return {
        "metric": metric,
        "sense": "maximize" if units not in {"h", "kWh"} else "minimize",
        "units": units,
        "weight": weight,
        "rationale": f"test {metric}",
    }


def _composition_row(*, weight: float = 0.50) -> dict:
    return {
        "type": "composition_target",
        "id": "glass",
        "metric": _COMPOSITION_METRIC,
        "sense": "maximize",
        "units": "score_0_1",
        "weight": weight,
        "rationale": "stage-3 glass composition",
        "target": {},
    }


def _apply_prose(profile: dict, variant: str) -> dict:
    edited = copy.deepcopy(profile)
    if variant == "both-smoke":
        edited["description"] = _SMOKE_LABEL
        edited["objective_emphasis"] = _SMOKE_LABEL
    elif variant == "description-edited":
        edited["description"] = "edited description; same C0 run"
        edited["objective_emphasis"] = _SMOKE_LABEL
    elif variant == "emphasis-edited":
        edited["description"] = _SMOKE_LABEL
        edited["objective_emphasis"] = "edited emphasis; same C0 run"
    elif variant == "both-edited":
        edited["description"] = "edited description; same C0 run"
        edited["objective_emphasis"] = "edited emphasis; same C0 run"
    elif variant == "description-missing":
        edited.pop("description", None)
        edited["objective_emphasis"] = _SMOKE_LABEL
    elif variant == "emphasis-missing":
        edited["description"] = _SMOKE_LABEL
        edited.pop("objective_emphasis", None)
    elif variant == "both-missing":
        edited.pop("description", None)
        edited.pop("objective_emphasis", None)
    elif variant == "empty-strings":
        edited["description"] = ""
        edited["objective_emphasis"] = ""
    elif variant == "lowercase-prefix":
        edited["description"] = "c0 smoke only: not the eligibility prefix"
        edited["objective_emphasis"] = "c0 smoke only: not the eligibility prefix"
    else:
        raise AssertionError(variant)
    return edited


def _profile(
    *product_rows: dict,
    campaign: str | None,
    prose: str = "both-edited",
) -> dict:
    profile = {
        "profile_id": "campaign-claim-invariant",
        "description": "edited description; same run",
        "objective_emphasis": "edited emphasis; same run",
        "objectives": [
            *product_rows,
            _objective_row(
                ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
                weight=_RETAINED_ENERGY_WEIGHT,
                units="kWh",
            ),
            _objective_row("duration_h", weight=_RETAINED_DURATION_WEIGHT, units="h"),
        ],
        "run": {"hours": 1, "mass_kg": 1000.0},
    }
    if campaign is not None:
        profile["run"]["campaign"] = campaign
    return _apply_prose(profile, prose)


def _product_row(metric: str, *, weight: float = 0.50) -> dict:
    if metric == _COMPOSITION_METRIC:
        return _composition_row(weight=weight)
    units = "score_0_1" if metric == SSO2_OWNER_RECIPE_ID else "kg"
    return _objective_row(metric, weight=weight, units=units)


def _stub_execution() -> SimpleNamespace:
    """Run surface whose final-product readers are zero or missing.

    A scored oxygen or Stage-3 silica objective becomes 0.0 here. The
    invariant says that 0.0 never enters the objective vector.
    """

    simulator = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
        energy_electrical_plus_evaporation_cumulative_kWh=_STUB_ENERGY_KWH,
        melt=SimpleNamespace(hour=_STUB_DURATION_H),
        record=SimpleNamespace(total_hours=_STUB_DURATION_H),
    )
    return SimpleNamespace(simulator=simulator)


def _assert_withheld(profile: dict, withheld_metrics: set[str], *, label: str) -> None:
    from simulator.optimize.evaluate import _notes_with_unclaimed_product_withhold

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
    leaked_values = {
        metric: mapping[metric] for metric in withheld_metrics if metric in mapping
    }
    assert not leaked_values, (
        f"{label} entered unclaimed products into the objective vector: {leaked_values}"
    )
    assert [definition.metric for definition in definitions] == [
        row.metric for row in retained_rows
    ]
    total = math.fsum(row.weight for row in evidence)
    withheld_weight = math.fsum(row.weight for row in withheld_rows)
    fraction = unclaimed_product_withheld_weight_fraction(profile)
    assert fraction is not None
    assert fraction == withheld_weight / total
    assert 0.0 < fraction < 1.0, label
    note = unclaimed_product_withheld_objective_note(profile)
    assert note is not None
    assert note.startswith("campaign_does_not_claim_product: ")
    assert note.count("reason=campaign_does_not_claim_product") == len(withheld_rows)
    for row in withheld_rows:
        assert (
            f"{row.metric} weight={format(row.weight, '.12g')} "
            "reason=campaign_does_not_claim_product"
        ) in note, label
    assert f"withheld_weight_fraction={format(fraction, '.12g')}" in note, label
    assert _notes_with_unclaimed_product_withhold((), profile) == (note,)
    weights = unrenormalized_study_scalar_weights(profile)
    assert weights == tuple(row.weight for row in retained_rows)
    assert math.isclose(
        math.fsum(weights),
        total - withheld_weight,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert not math.isclose(math.fsum(weights), total)


def _assert_study_scalar(
    profile: dict,
    *,
    metrics: dict[str, float],
    weights: tuple[float, ...],
    scalar: float,
) -> None:
    """Production study scalar from literal metrics, weights, and the stub."""

    mapping = compute_objectives(profile, _stub_execution()).as_mapping()
    assert {key: mapping[key] for key in metrics} == metrics
    definitions = objective_definitions(profile)
    assert tuple(definition.metric for definition in definitions) == tuple(metrics)
    assert unrenormalized_study_scalar_weights(profile) == weights
    record = StudyRecord(
        candidate_id="claim-gate-scalar",
        patch=RecipePatch({}),
        feasible=True,
        status="ok",
        objectives=mapping,
        feasibility_margins={},
        product_summary={
            "furnace_amortization_status": "available",
            "furnace_amortization_batch_cost_equivalents": 0.0,
        },
    )
    got_scalar, signature = _study_progress((record,), definitions, weights)
    assert got_scalar == pytest.approx(scalar)
    # Penalty 0 does not move the negated minimize scores.
    assert signature == frozenset({tuple(-value for value in metrics.values())})


def _campaign_ids() -> tuple[str | None, ...]:
    oracle = _setpoints_product_keys()
    return (
        *tuple(sorted(oracle)),
        *tuple(sorted(_LITERAL_ALIASES)),
        "not-a-campaign",
        None,
    )


def _metric_cases() -> list[tuple[str | None, str, str]]:
    cases: list[tuple[str | None, str, str]] = []
    for campaign in _campaign_ids():
        for metric in (*FINAL_PRODUCT_METRICS, _COMPOSITION_METRIC):
            for prose in ("both-edited", "both-smoke", "description-edited"):
                cases.append((campaign, metric, prose))
    return cases


def _case_id(campaign: str | None, metric: str, prose: str) -> str:
    return f"{campaign or 'absent'}-{metric}-{prose}"


@pytest.mark.parametrize(
    ("campaign", "metric", "prose"),
    _metric_cases(),
    ids=[_case_id(*case) for case in _metric_cases()],
)
def test_product_objective_membership_follows_campaign_claims(
    campaign: str | None,
    metric: str,
    prose: str,
) -> None:
    """Every campaign, every product metric, with the description already edited."""

    claim_keys = _oracle_claims(campaign)
    composition = metric == _COMPOSITION_METRIC
    claimed = _oracle_claims_metric(metric, claim_keys, composition=composition)
    assert campaign_claims_product_objective(
        metric,
        claim_keys,
        composition=composition,
    ) is claimed
    profile = _profile(_product_row(metric), campaign=campaign, prose=prose)
    scored = {definition.metric for definition in objective_definitions(profile)}
    if claimed:
        assert metric in scored
        assert unclaimed_product_withheld_objective_note(profile) is None
        return
    _assert_withheld(profile, {metric}, label=f"{campaign}:{metric}:{prose}")


@pytest.mark.parametrize("prose", _PROSE_VARIANTS)
@pytest.mark.parametrize("metric", (*FINAL_PRODUCT_METRICS, _COMPOSITION_METRIC, "Fe_kg"))
def test_c0_prose_does_not_change_claim_membership(metric: str, prose: str) -> None:
    """Copy lunar_highland's C0 run and edit either prose field: still withheld."""

    row = (
        _objective_row(metric, weight=0.50, units="kg")
        if metric == "Fe_kg"
        else _product_row(metric)
    )
    profile = _profile(row, campaign="C0", prose=prose)
    scored = {definition.metric for definition in objective_definitions(profile)}
    if metric == "Fe_kg":
        assert metric in scored
        note = unclaimed_product_withheld_objective_note(profile)
        assert note is None or "Fe_kg" not in note
        return
    _assert_withheld(profile, {metric}, label=f"C0:{metric}:{prose}")


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _shipped_profiles() -> list[tuple[str, dict]]:
    loaded = []
    for path in sorted(PROFILE_DIR.glob("*.yaml")):
        profile = _plain(load_profile(path.stem))
        assert isinstance(profile, dict)
        loaded.append((path.stem, profile))
    return loaded


def _declared_retained(profile: dict) -> list[tuple[str, float]]:
    claim_keys = _oracle_claims((profile.get("run") or {}).get("campaign"))
    retained = []
    for objective in profile["objectives"]:
        metric = str(objective["metric"])
        composition = objective.get("type") == "composition_target"
        if _oracle_claims_metric(metric, claim_keys, composition=composition):
            retained.append((metric, float(objective["weight"])))
        elif metric not in PRODUCT_OBJECTIVES and not metric.startswith(
            COMPOSITION_TARGET_METRIC_PREFIX
        ):
            retained.append((metric, float(objective["weight"])))
    return retained


@pytest.mark.parametrize("prose", _PROSE_VARIANTS)
def test_shipped_profiles_withhold_unclaimed_products_for_every_prose(prose: str) -> None:
    """Every shipped profile, every declared final-product metric, every prose edit."""

    shipped = _shipped_profiles()
    assert shipped
    checked = 0
    for name, raw in shipped:
        profile = _apply_prose(raw, prose)
        if (profile.get("run") or {}).get("campaign") != "C0":
            continue
        declared = [str(objective["metric"]) for objective in profile["objectives"]]
        withheld = set(declared) & set(FINAL_PRODUCT_METRICS)
        if not withheld:
            continue
        _assert_withheld(profile, withheld, label=f"{name}:{prose}")
        retained = _declared_retained(raw)
        assert [metric for metric, _weight in retained] == [
            ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
            "duration_h",
        ]
        weights = tuple(weight for _metric, weight in retained)
        assert unrenormalized_study_scalar_weights(profile) == weights
        checked += 1
    assert checked == len(shipped)


def test_synthetic_study_scalar_uses_stub_energy_and_duration() -> None:
    """Literal metrics, weights, and scalar through ``_study_progress``.

    Retained weights are the declared 0.10 and 0.06. The stub reports energy
    10.0 kWh and duration 4.0 h. Both senses are minimize and the furnace
    penalty is 0, so the scalar is 0.10 * -10 + 0.06 * -4 = -1.24.
    """

    profile = _profile(
        _product_row("pure_silica_glass_kg", weight=0.32),
        _product_row("metals_total_kg", weight=0.28),
        _product_row("oxygen_kg", weight=0.24),
        campaign="C0",
        prose="description-edited",
    )
    assert _RETAINED_ENERGY_WEIGHT == 0.10
    assert _RETAINED_DURATION_WEIGHT == 0.06
    assert _SYNTHETIC_SCALAR == pytest.approx(-1.24)
    _assert_study_scalar(
        profile,
        metrics={
            ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC: _STUB_ENERGY_KWH,
            "duration_h": _STUB_DURATION_H,
        },
        weights=(_RETAINED_ENERGY_WEIGHT, _RETAINED_DURATION_WEIGHT),
        scalar=_SYNTHETIC_SCALAR,
    )


def test_shipped_study_scalars_use_declared_retained_weights() -> None:
    """Weights come from the profile YAML, then through ``_study_progress``."""

    for name, raw in _shipped_profiles():
        profile = _apply_prose(raw, "description-edited")
        retained = _declared_retained(raw)
        weights = tuple(weight for _metric, weight in retained)
        metrics = {
            ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC: _STUB_ENERGY_KWH,
            "duration_h": _STUB_DURATION_H,
        }
        assert [metric for metric, _weight in retained] == list(metrics)
        # yaml weights, not the helper's return value used as its own expectation
        scalar = math.fsum(
            weight * -value
            for weight, value in zip(weights, metrics.values(), strict=True)
        )
        _assert_study_scalar(profile, metrics=metrics, weights=weights, scalar=scalar)
        assert name


def test_scoring_strategies_use_the_claim_chokepoint() -> None:
    """Strategy objective vectors are objective_definitions, not a second parser."""

    pytest.importorskip("optuna")
    from simulator.optimize.recipe import RecipeSchema
    from simulator.optimize.strategy.bayesian import OptunaTPEStrategy
    from simulator.optimize.strategy.genetic import OptunaNSGA2Strategy
    from simulator.optimize.strategy.staged import StagedStrategy

    schema = RecipeSchema()
    strategies = (OptunaTPEStrategy, OptunaNSGA2Strategy, StagedStrategy)
    for metric in (*FINAL_PRODUCT_METRICS, _COMPOSITION_METRIC):
        profile = _profile(
            _product_row(metric),
            campaign="C0",
            prose="description-edited",
        )
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

    claimed = _profile(
        _product_row("pure_silica_glass_kg"),
        campaign="C2A_continuous",
        prose="both-edited",
    )
    expected = tuple(definition.metric for definition in objective_definitions(claimed))
    assert "pure_silica_glass_kg" in expected
    for strategy_cls in strategies:
        strategy = strategy_cls(schema, seed=0, objective_profile=claimed)
        if strategy_cls is StagedStrategy:
            got = tuple(definition.metric for definition in strategy._definitions)
        else:
            got = tuple(strategy.objective_metrics)
        assert got == expected
        assert "pure_silica_glass_kg" in got
