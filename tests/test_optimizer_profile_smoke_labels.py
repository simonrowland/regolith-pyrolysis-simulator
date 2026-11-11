"""Campaign product-claim gate for optimizer scoring.

INVARIANT: no product objective is scored unless the executed campaign claims
THAT objective's product. Claim keys are the setpoints ``products`` keys plus
``O2`` when the campaign's hour-step routes credit O2 to a terminal account.
The executed campaign is the fidelity overlay on ``profile.run``
(EvalSpec.campaign). Otherwise the objective is absent from objective
definitions, ``compute_objectives``, strategy metrics, and the study scalar.
It is named in a notice with its weight, the reason
``campaign_does_not_claim_product``, and the withheld weight fraction. It is
never entered as 0.0. Retained weights are not renormalized. Description text
is not an eligibility input. An absent, blank, or unknown campaign claims
nothing. A missing run campaign executes as ``C0``, which claims oxygen and
does not claim glass, metals, or ceramic. A composition target is eligible
only for its own pool product. ``captured_products`` requires every extracted
species. A dynamic ``{species}_kg`` row is that ledger species. It is
claimed only when ``species`` itself is a products key: ``native_Fe`` does
not claim ``Fe_kg``, and ``SiO2_glass`` does not claim ``Si_kg``. An
energy-per-product intensity is not a ledger species. Its denominator is
``metals_plus_O2.class_total_kg``, so it is claimed only when the campaign
claims ``O2`` and a metal key (``Fe``, ``Mg``, or ``Ti``). Energy and
duration stay. File load is not an evaluation: the remaining-weight refusal
waits until the selected fidelity's campaign is known.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.campaigns import campaign_evolves_terminal_oxygen
from simulator.config import DEFAULT_DATA_DIR
from simulator.optimize.objective import (
    COMPOSITION_TARGET_METRIC_PREFIX,
    COMPOSITION_VECTOR_SPECIES,
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    SSO2_OWNER_RECIPE_ID,
    SUPPORTED_COMPOSITION_POOLS,
    _EXTRACT_SPECIES_CLAIM_KEYS,
    _MASS_OBJECTIVE_METRICS,
    _METAL_PRODUCT_CLAIM_KEYS,
    _OBJECTIVE_PRODUCT_CLAIM_KEYS,
    _POOL_PRODUCT_CLAIM_KEYS,
    _SETPOINT_CAMPAIGN_ALIASES,
    _energy_per_product_metric,
    campaign_claims_product_objective,
    campaign_product_claim_keys,
    compute_objectives,
    executed_campaign_id,
    objective_definitions,
    objective_importance_evidence,
    profile_scored_at_fidelity,
    unclaimed_product_withheld_objective_note,
    unclaimed_product_withheld_weight_fraction,
    unrenormalized_study_scalar_weights,
)
from simulator.optimize.profiles import (
    VALID_FIDELITIES,
    ProfileValidationError,
    load_profile,
)
from simulator.optimize.recipe import RecipePatch
from simulator.optimize.study import (
    StudyRecord,
    _load_profile_for_study,
    _study_progress,
    resolve_profile,
)


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
# Literal pool and extract tables. Production must match these. The membership
# oracle below uses these sets, not the production helper.
_LITERAL_POOL_KEYS = {
    "captured_stage_3_silica": frozenset({"SiO2_glass"}),
    "terminal_rump_earned": frozenset({"refractory_ceramic_rump"}),
    "residual_rump_at_stop": frozenset({"CMAS_glass", "industrial_mixed_glass"}),
    "cleaned_melt_at_stage0_exit": frozenset({"CMAS_glass", "industrial_mixed_glass"}),
}
_LITERAL_EXTRACT_KEYS = {
    "Na": frozenset({"Na"}),
    "K": frozenset({"K"}),
    "Fe": frozenset({"Fe"}),
    "Mg": frozenset({"Mg"}),
    "O2": frozenset({"O2"}),
    "Si": frozenset({"SiO2_glass"}),
    "Al": frozenset(),
    "Ca": frozenset(),
}
_LITERAL_ALIASES = {
    "C2A": "C2A_continuous",
    "C2A_staged": "C2A_staged",
    "C3_K": "C3",
    "C3_NA": "C3",
}
# Setpoint ids whose hour-step route credits terminal O2. The products map
# omits that by-product except on C2A_continuous. C6 thermite and C7
# aluminothermy keep the oxygen in the oxide and are the complement.
_TERMINAL_OXYGEN_SETPOINT_CAMPAIGNS = frozenset({
    "C0",
    "C0b_p_cleanup",
    "C2A_continuous",
    "C2A_staged",
    "C2B",
    "C3",
    "C4",
    "C5",
    "mre_baseline",
})
_NO_TERMINAL_OXYGEN_SETPOINT_CAMPAIGNS = frozenset({"C6", "C7"})
_OXYGEN_OBJECTIVE_METRICS = frozenset({
    "O2_kg",
    "o2_kg",
    "oxygen_kg",
    "oxygen_stored_kg",
    "oxygen_vented_kg",
})
# Intensities. They end in ``_kg`` and are not ledger species. Each one
# divides by metals_plus_O2.class_total_kg, so the claim is the same combined
# product as metals_plus_o2_kg: O2 and one of Fe, Mg, Ti. The production
# reader recognizes exactly this set. Component names and kWh are literals:
# the expected intensity is component_kWh / _INTENSITY_DENOMINATOR_KG.
_ENERGY_PER_PRODUCT_METRICS = frozenset({
    "energy_electrical_plus_evaporation_per_product_kWh_per_kg",
    "electrical_energy_per_product_kWh_per_kg",
    "evaporation_thermal_energy_per_product_kWh_per_kg",
    "latent_energy_per_product_kWh_per_kg",
    "dissociation_energy_per_product_kWh_per_kg",
})
_LITERAL_INTENSITY_COMPONENT = {
    "energy_electrical_plus_evaporation_per_product_kWh_per_kg": (
        "electrical_plus_evaporation"
    ),
    "electrical_energy_per_product_kWh_per_kg": "electrical",
    "evaporation_thermal_energy_per_product_kWh_per_kg": "evaporation_thermal",
    "latent_energy_per_product_kWh_per_kg": "latent",
    "dissociation_energy_per_product_kWh_per_kg": "dissociation",
}
_INTENSITY_DENOMINATOR_KG = 4.0
# Ledger mass the species tests hand to the product ledger. Not zero: a
# retained Mg_kg would add weight * this value to the study scalar.
_LEDGER_KG = 12.5
_SPECIES_WEIGHT = 0.50
# metals_plus_o2_kg is the combined class: O2 and one of Fe/Mg/Ti. One of the
# two is not that product. native_Fe is not a metal key.
_METAL_KEYS = frozenset({"Fe", "Mg", "Ti"})
_RETAINED_ENERGY_WEIGHT = 0.10
_RETAINED_DURATION_WEIGHT = 0.06
_STUB_ENERGY_KWH = 10.0
_STUB_DURATION_H = 4.0
# electrical_plus_evaporation is the retained energy objective's 10 kWh.
# The other components are the intensity numerators only.
_LITERAL_COMPONENT_KWH = {
    "electrical_plus_evaporation": _STUB_ENERGY_KWH,
    "electrical": 6.0,
    "evaporation_thermal": 4.0,
    "latent": 5.0,
    "dissociation": 7.0,
}
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
    assert dict(_POOL_PRODUCT_CLAIM_KEYS) == _LITERAL_POOL_KEYS
    assert dict(_EXTRACT_SPECIES_CLAIM_KEYS) == _LITERAL_EXTRACT_KEYS
    assert set(SUPPORTED_COMPOSITION_POOLS) == set(_LITERAL_POOL_KEYS) | {"captured_products"}
    assert COMPOSITION_VECTOR_SPECIES == frozenset(_LITERAL_EXTRACT_KEYS)
    assert dict(_SETPOINT_CAMPAIGN_ALIASES) == _LITERAL_ALIASES
    assert "metals_plus_o2_kg" not in _OBJECTIVE_PRODUCT_CLAIM_KEYS
    assert "captured_products" not in _POOL_PRODUCT_CLAIM_KEYS
    assert "Mg_kg" not in _OBJECTIVE_PRODUCT_CLAIM_KEYS
    assert "Fe_kg" not in _OBJECTIVE_PRODUCT_CLAIM_KEYS
    recognized = {
        metric
        for metric in _ENERGY_PER_PRODUCT_METRICS
        if _energy_per_product_metric(metric) is not None
    }
    assert recognized == _ENERGY_PER_PRODUCT_METRICS


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


def _route_claim_keys() -> dict[str, frozenset[str]]:
    """YAML product keys, plus ``O2`` for campaigns whose route evolves it.

    The oxygen id set is a literal. It must equal the setpoint campaigns
    whose phases ``campaign_evolves_terminal_oxygen`` accepts, so a second
    hand list in production cannot drift from the hour-step route.
    """

    yaml_keys = _setpoints_product_keys()
    assert set(yaml_keys) == (
        _TERMINAL_OXYGEN_SETPOINT_CAMPAIGNS | _NO_TERMINAL_OXYGEN_SETPOINT_CAMPAIGNS
    )
    claims: dict[str, frozenset[str]] = {}
    for name, keys in yaml_keys.items():
        evolves = campaign_evolves_terminal_oxygen(name)
        if name in _TERMINAL_OXYGEN_SETPOINT_CAMPAIGNS:
            assert evolves, name
            claims[name] = keys | frozenset({"O2"})
        else:
            assert not evolves, name
            assert "O2" not in keys, name
            claims[name] = keys
    return claims


def test_campaign_product_claims_match_setpoints_yaml() -> None:
    oracle = _route_claim_keys()
    assert oracle, "setpoints campaigns block was not read"
    for campaign_id, keys in oracle.items():
        assert campaign_product_claim_keys(campaign_id) == keys
    for alias, target in _LITERAL_ALIASES.items():
        assert campaign_product_claim_keys(alias) == oracle[target]
    # Phase names and the short C0b override are not setpoint ids. They claim
    # oxygen only when that phase's route credits a terminal account. Bare
    # "MRE" is not a phase and claims nothing. C0b does not inherit the
    # phosphorus key, which lives on C0b_p_cleanup.
    for phase_name in ("MRE_BASELINE", "C0B", "C0b", "C2A_STAGED"):
        assert campaign_evolves_terminal_oxygen(phase_name)
        assert campaign_product_claim_keys(phase_name) == frozenset({"O2"})
    assert campaign_product_claim_keys("MRE") == frozenset()
    assert campaign_evolves_terminal_oxygen("MRE") is False
    assert campaign_evolves_terminal_oxygen("C7_CA_ALUMINOTHERMIC") is False
    assert campaign_product_claim_keys(None) == frozenset()
    assert campaign_product_claim_keys("") == frozenset()
    assert campaign_product_claim_keys("   ") == frozenset()
    assert campaign_product_claim_keys("not-a-campaign") == frozenset()


def _oracle_claims(campaign_id: str | None) -> frozenset[str]:
    if not isinstance(campaign_id, str) or not campaign_id.strip():
        return frozenset()
    token = campaign_id.strip()
    key = _LITERAL_ALIASES.get(token, token)
    yaml_keys = _setpoints_product_keys()
    found = yaml_keys.get(key)
    if found is not None:
        if key in _TERMINAL_OXYGEN_SETPOINT_CAMPAIGNS:
            return found | frozenset({"O2"})
        return found
    oxygen = frozenset()
    if campaign_evolves_terminal_oxygen(token):
        oxygen = frozenset({"O2"})
    if key != token and campaign_evolves_terminal_oxygen(key):
        oxygen = oxygen | frozenset({"O2"})
    return oxygen


def _oracle_pool_claimed(
    pool: str,
    species_vector: Mapping[str, str] | None,
    claim_keys: frozenset[str],
) -> bool:
    if pool == "captured_products":
        if not species_vector:
            return False
        extracts = [species for species, role in species_vector.items() if role == "extract"]
        if not extracts:
            return False
        for species in extracts:
            required = _LITERAL_EXTRACT_KEYS.get(species)
            if not required or not (claim_keys & required):
                return False
        return True
    required = _LITERAL_POOL_KEYS.get(pool)
    if not required:
        return False
    return bool(claim_keys & required)


def _oracle_composition_claimed(target: object, claim_keys: frozenset[str]) -> bool:
    if not isinstance(target, Mapping):
        return False
    pool = target.get("pool")
    if not isinstance(pool, str) or not pool.strip():
        return False
    pools = [pool.strip()]
    window = target.get("composition_window")
    if isinstance(window, Mapping):
        window_pool = window.get("pool")
        if isinstance(window_pool, str) and window_pool.strip():
            name = window_pool.strip()
            if name not in pools:
                pools.append(name)
    vector = target.get("species_vector")
    species_vector = vector if isinstance(vector, Mapping) else None
    return all(
        _oracle_pool_claimed(name, species_vector, claim_keys) for name in pools
    )


def _oracle_ledger_species(metric: str) -> str | None:
    """Species of a dynamic ``{species}_kg`` row. Named masses are not species.

    The claim key is the species string. ``Fe_kg`` is not ``native_Fe``.
    ``Si_kg`` is not ``SiO2_glass``. Energy-per-product intensities are not
    species rows.
    """

    if metric in _ENERGY_PER_PRODUCT_METRICS:
        return None
    if metric in _LITERAL_CLAIM_KEYS or metric == "metals_plus_o2_kg":
        return None
    if not metric.endswith("_kg"):
        return None
    species = metric[:-3]
    return species or None


def _oracle_claims_metric(
    metric: str,
    claim_keys: frozenset[str],
    *,
    target: Mapping[str, object] | None = None,
) -> bool:
    if metric.startswith(COMPOSITION_TARGET_METRIC_PREFIX):
        return _oracle_composition_claimed(target, claim_keys)
    if metric == "metals_plus_o2_kg" or metric in _ENERGY_PER_PRODUCT_METRICS:
        # The intensity denominator is metals_plus_O2.class_total_kg.
        return "O2" in claim_keys and bool(claim_keys & _METAL_KEYS)
    required = _LITERAL_CLAIM_KEYS.get(metric)
    if required is not None:
        return bool(claim_keys & required)
    species = _oracle_ledger_species(metric)
    if species is not None:
        return species in claim_keys
    return True


def _objective_row(metric: str, *, weight: float, units: str) -> dict:
    return {
        "metric": metric,
        "sense": "maximize" if units not in {"h", "kWh"} else "minimize",
        "units": units,
        "weight": weight,
        "rationale": f"test {metric}",
    }


def _composition_row(*, weight: float = 0.50) -> dict:
    """Composition row with no readable target. That is not a product claim."""

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


def _composition_target_row(
    pool: str,
    *,
    species_vector: dict[str, str],
    window_pool: str | None = None,
    weight: float = 0.50,
    target_id: str = "target",
) -> dict:
    return {
        "type": "composition_target",
        "id": target_id,
        "metric": f"{COMPOSITION_TARGET_METRIC_PREFIX}{target_id}",
        "sense": "maximize",
        "units": "score_0_1",
        "weight": weight,
        "rationale": f"test {pool}",
        "target": {
            "pool": pool,
            "species_vector": dict(species_vector),
            "composition_window": {
                "pool": window_pool or pool,
                "basis": "oxide_wt_pct",
                "mode": "hard_window",
                "oxides": {"SiO2": {"min": 0.0, "max": 100.0, "weight": 1.0}},
            },
            "score_weights": {"extraction": 0.0, "composition": 1.0},
        },
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


def _stub_execution(
    ledger: Mapping[str, float] | None = None,
) -> SimpleNamespace:
    """Run surface whose final-product readers are zero or missing.

    A scored oxygen or Stage-3 silica objective becomes 0.0 here. The
    invariant says that 0.0 never enters the objective vector. ``ledger``
    supplies product-ledger species masses so a retained ``Mg_kg`` would
    score that mass instead of being absent.
    """

    masses = dict(ledger or {})
    simulator = SimpleNamespace(
        product_ledger=lambda: dict(masses),
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
        energy_electrical_plus_evaporation_cumulative_kWh=_STUB_ENERGY_KWH,
        melt=SimpleNamespace(hour=_STUB_DURATION_H),
        record=SimpleNamespace(total_hours=_STUB_DURATION_H),
    )
    return SimpleNamespace(simulator=simulator)


def _assert_withheld(
    profile: dict,
    withheld_metrics: set[str],
    *,
    label: str,
    ledger: Mapping[str, float] | None = None,
) -> None:
    evidence = objective_importance_evidence(profile)
    withheld_rows = [row for row in evidence if row.metric in withheld_metrics]
    retained_rows = [row for row in evidence if row.metric not in withheld_metrics]
    assert withheld_rows, label
    assert retained_rows, label
    definitions = objective_definitions(profile)
    scored = {definition.metric for definition in definitions}
    leaked = scored & withheld_metrics
    assert not leaked, f"{label} scores unclaimed products {sorted(leaked)}"
    mapping = compute_objectives(profile, _stub_execution(ledger)).as_mapping()
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
    execution: SimpleNamespace | None = None,
) -> None:
    """Production study scalar from literal metrics, weights, and the stub."""

    mapping = compute_objectives(
        profile,
        execution if execution is not None else _stub_execution(),
    ).as_mapping()
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
        for metric in FINAL_PRODUCT_METRICS:
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
    """Every campaign, every product metric, with the description already edited.

    The helper is asked about the raw id, so an absent campaign claims
    nothing. Scoring uses the executed campaign: a missing run campaign is
    C0, which claims oxygen.
    """

    direct_keys = _oracle_claims(campaign)
    assert campaign_claims_product_objective(metric, direct_keys) is (
        _oracle_claims_metric(metric, direct_keys)
    )
    profile = _profile(_product_row(metric), campaign=campaign, prose=prose)
    executed_keys = _oracle_claims(executed_campaign_id(profile))
    claimed = _oracle_claims_metric(metric, executed_keys)
    scored = {definition.metric for definition in objective_definitions(profile)}
    if claimed:
        assert metric in scored
        assert unclaimed_product_withheld_objective_note(profile) is None
        return
    _assert_withheld(profile, {metric}, label=f"{campaign}:{metric}:{prose}")


_TERMINAL_METRIC = f"{COMPOSITION_TARGET_METRIC_PREFIX}ceramic"


@pytest.mark.parametrize("prose", _PROSE_VARIANTS)
@pytest.mark.parametrize(
    "metric",
    (*FINAL_PRODUCT_METRICS, _TERMINAL_METRIC, "Fe_kg", "Mg_kg"),
)
def test_c0_prose_does_not_change_claim_membership(metric: str, prose: str) -> None:
    """Copy lunar_highland's C0 run and edit either prose field.

    C0 claims oxygen, so the oxygen metrics stay. ``Fe_kg`` and ``Mg_kg`` are
    ledger species. C0's ``native_Fe`` claim does not keep them, and the
    prose fields are not eligibility. Glass and ceramic stay withheld.
    """

    if metric == _TERMINAL_METRIC:
        row = _composition_target_row(
            "terminal_rump_earned",
            species_vector={"Ca": "retain"},
            target_id="ceramic",
        )
    else:
        row = _product_row(metric)
    profile = _profile(row, campaign="C0", prose=prose)
    target = row.get("target")
    claimed = _oracle_claims_metric(
        metric,
        _oracle_claims("C0"),
        target=target if isinstance(target, Mapping) else None,
    )
    if claimed:
        scored = {definition.metric for definition in objective_definitions(profile)}
        assert metric in scored
        assert unclaimed_product_withheld_objective_note(profile) is None
        return
    ledger = {metric[:-3]: _LEDGER_KG} if metric.endswith("_kg") else None
    _assert_withheld(profile, {metric}, label=f"C0:{metric}:{prose}", ledger=ledger)


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
        target = objective.get("target")
        claimed = _oracle_claims_metric(
            metric,
            claim_keys,
            target=target if isinstance(target, Mapping) else None,
        )
        if claimed:
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
        claim_keys = _oracle_claims("C0")
        withheld = {
            metric
            for metric in declared
            if metric in set(FINAL_PRODUCT_METRICS)
            and not _oracle_claims_metric(metric, claim_keys)
        }
        # C0 claims oxygen. Every other declared final product stays withheld,
        # and that set is non-empty on every shipped profile.
        for metric in declared:
            if metric in _OXYGEN_OBJECTIVE_METRICS:
                assert metric not in withheld, f"{name}:{metric}"
        assert withheld, name
        _assert_withheld(profile, withheld, label=f"{name}:{prose}")
        retained = _declared_retained(raw)
        assert [metric for metric, _weight in retained] == [
            metric for metric in declared if metric not in withheld
        ]
        weights = tuple(weight for _metric, weight in retained)
        assert unrenormalized_study_scalar_weights(profile) == weights
        checked += 1
    assert checked == len(shipped)


def test_synthetic_study_scalar_uses_stub_energy_and_duration() -> None:
    """Literal metrics, weights, and scalar through ``_study_progress``.

    C0 claims oxygen and withholds glass and metals. The stub reports 0 kg
    of oxygen, energy 10.0 kWh, and duration 4.0 h. Oxygen is maximize, so
    its term is 0.24 * 0. Energy and duration are minimize and the furnace
    penalty is 0, so the scalar stays 0.10 * -10 + 0.06 * -4 = -1.24.
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
            "oxygen_kg": 0.0,
            ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC: _STUB_ENERGY_KWH,
            "duration_h": _STUB_DURATION_H,
        },
        weights=(0.24, _RETAINED_ENERGY_WEIGHT, _RETAINED_DURATION_WEIGHT),
        scalar=_SYNTHETIC_SCALAR,
    )


def test_shipped_study_scalars_use_declared_retained_weights() -> None:
    """Weights come from the profile YAML, then through ``_study_progress``."""

    for name, raw in _shipped_profiles():
        profile = _apply_prose(raw, "description-edited")
        retained = _declared_retained(raw)
        weights = tuple(weight for _metric, weight in retained)
        # C0 keeps declared oxygen metrics. The stub's terminal oxygen is 0 kg.
        # Other final products stay withheld. Energy and duration are minimized.
        declared = [str(objective["metric"]) for objective in raw["objectives"]]
        oxygen_kept = [
            metric for metric in declared if metric in _OXYGEN_OBJECTIVE_METRICS
        ]
        assert [metric for metric, _weight in retained] == [
            *oxygen_kept,
            ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
            "duration_h",
        ]
        metrics = {
            metric: 0.0 for metric in oxygen_kept
        }
        metrics[ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC] = _STUB_ENERGY_KWH
        metrics["duration_h"] = _STUB_DURATION_H
        # yaml weights, not the helper's return value used as its own expectation
        scalar = math.fsum(
            weight * (value if metric in _OXYGEN_OBJECTIVE_METRICS else -value)
            for (metric, _declared_weight), weight, value in zip(
                retained, weights, metrics.values(), strict=True
            )
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
    # Every product class the chokepoint withholds, including intensities
    # and a ledger species. Strategy metrics are objective_definitions.
    for metric in (
        *FINAL_PRODUCT_METRICS,
        *sorted(_ENERGY_PER_PRODUCT_METRICS),
        _COMPOSITION_METRIC,
        "Mg_kg",
    ):
        row = (
            _intensity_row(metric)
            if metric in _ENERGY_PER_PRODUCT_METRICS
            else _product_row(metric)
        )
        profile = _profile(
            row,
            campaign="C0",
            prose="description-edited",
        )
        expected = tuple(definition.metric for definition in objective_definitions(profile))
        # C0 claims oxygen. Every other product class stays withheld.
        if metric in _OXYGEN_OBJECTIVE_METRICS:
            assert metric in expected
        else:
            assert metric not in expected
        for strategy_cls in strategies:
            strategy = strategy_cls(schema, seed=0, objective_profile=profile)
            if strategy_cls is StagedStrategy:
                got = tuple(definition.metric for definition in strategy._definitions)
            else:
                got = tuple(strategy.objective_metrics)
            assert got == expected, f"{strategy_cls.__name__} {metric}"
            if metric in _OXYGEN_OBJECTIVE_METRICS:
                assert metric in got
            else:
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

    # One representative profile per class. The parametrized tests cover every
    # campaign, pool, and species; strategy init is not repeated for each.
    fidelity_glass = _profile(
        _product_row("pure_silica_glass_kg"),
        campaign="C2A_continuous",
        prose="both-edited",
    )
    fidelity_glass["fidelities"] = {"fast": {"campaign": "C0"}}
    fidelity_stamped = profile_scored_at_fidelity(fidelity_glass, "fast")
    assert fidelity_glass["run"]["campaign"] == "C2A_continuous"
    expected = tuple(
        definition.metric for definition in objective_definitions(fidelity_stamped)
    )
    assert "pure_silica_glass_kg" not in expected
    ceramic = _profile(
        _composition_target_row(
            "terminal_rump_earned",
            species_vector={"Ca": "retain"},
            target_id="ceramic",
        ),
        campaign="C4",
        prose="both-edited",
    )
    ceramic_expected = tuple(definition.metric for definition in objective_definitions(ceramic))
    assert f"{COMPOSITION_TARGET_METRIC_PREFIX}ceramic" not in ceramic_expected
    melt = _profile(
        _composition_target_row(
            "residual_rump_at_stop",
            species_vector={"Ca": "retain"},
            target_id="melt",
        ),
        campaign="C2B",
        prose="both-edited",
    )
    melt_expected = tuple(definition.metric for definition in objective_definitions(melt))
    assert f"{COMPOSITION_TARGET_METRIC_PREFIX}melt" in melt_expected
    c0_mg = _profile(_product_row("Mg_kg"), campaign="C0", prose="description-edited")
    c0_mg_expected = tuple(definition.metric for definition in objective_definitions(c0_mg))
    assert "Mg_kg" not in c0_mg_expected
    c4_mg = _profile(_product_row("Mg_kg"), campaign="C4", prose="description-edited")
    c4_mg_expected = tuple(definition.metric for definition in objective_definitions(c4_mg))
    assert "Mg_kg" in c4_mg_expected
    for label, profile, want in (
        ("fidelity-c0", fidelity_stamped, expected),
        ("c4-ceramic", ceramic, ceramic_expected),
        ("c2b-melt", melt, melt_expected),
        ("c0-Mg_kg", c0_mg, c0_mg_expected),
        ("c4-Mg_kg", c4_mg, c4_mg_expected),
    ):
        for strategy_cls in strategies:
            strategy = strategy_cls(schema, seed=0, objective_profile=profile)
            if strategy_cls is StagedStrategy:
                got = tuple(definition.metric for definition in strategy._definitions)
            else:
                got = tuple(strategy.objective_metrics)
            assert got == want, f"{strategy_cls.__name__} {label}"


def _assert_membership(profile: dict, metric: str, *, claimed: bool, label: str) -> None:
    scored = {definition.metric for definition in objective_definitions(profile)}
    if claimed:
        assert metric in scored, label
        assert unclaimed_product_withheld_objective_note(profile) is None, label
        return
    _assert_withheld(profile, {metric}, label=label)


def _pool_cases() -> list[tuple[str | None, str]]:
    pools = (*sorted(SUPPORTED_COMPOSITION_POOLS), "not-a-pool")
    return [(campaign, pool) for campaign in _campaign_ids() for pool in pools]


def _window_cases() -> list[tuple[str | None, str, str]]:
    pools = (*sorted(SUPPORTED_COMPOSITION_POOLS), "not-a-pool")
    cases = []
    for campaign in _campaign_ids():
        for pool in pools:
            for window_pool in pools:
                if window_pool != pool:
                    cases.append((campaign, pool, window_pool))
    return cases


def _extract_cases() -> list[tuple[str | None, str, str]]:
    return [
        (campaign, species, role)
        for campaign in _campaign_ids()
        for species in sorted(COMPOSITION_VECTOR_SPECIES)
        for role in ("extract", "retain")
    ]


def _multi_extract_cases() -> list[tuple[str | None, tuple[str, ...]]]:
    vectors = (("Fe", "Mg"), ("Fe", "Al"), ("Si", "O2"))
    return [(campaign, species) for campaign in _campaign_ids() for species in vectors]


def _fidelity_cases() -> list[tuple[str | None, str | None, str]]:
    return [
        (run_campaign, fidelity_campaign, kind)
        for run_campaign in _campaign_ids()
        for fidelity_campaign in _campaign_ids()
        for kind in ("glass", "ceramic")
    ]


@pytest.mark.parametrize(
    ("campaign", "pool"),
    _pool_cases(),
    ids=[f"{campaign or 'absent'}-{pool}" for campaign, pool in _pool_cases()],
)
def test_composition_target_follows_its_own_product(campaign: str | None, pool: str) -> None:
    """Every campaign and every pool. A foreign product claim does not authorize it."""

    row = _composition_target_row(
        pool,
        species_vector={"Ca": "retain"},
        target_id="pool",
    )
    named_keys = _oracle_claims(campaign)
    named_claimed = _oracle_composition_claimed(row["target"], named_keys)
    assert campaign_claims_product_objective(
        row["metric"],
        named_keys,
        composition=True,
        target=row["target"],
    ) is named_claimed
    profile = _profile(row, campaign=campaign, prose="description-edited")
    executed_keys = _oracle_claims(executed_campaign_id(profile))
    _assert_membership(
        profile,
        row["metric"],
        claimed=_oracle_composition_claimed(row["target"], executed_keys),
        label=f"{campaign}:{pool}",
    )


@pytest.mark.parametrize(
    ("campaign", "pool", "window_pool"),
    _window_cases(),
    ids=[
        f"{campaign or 'absent'}-{pool}-window-{window_pool}"
        for campaign, pool, window_pool in _window_cases()
    ],
)
def test_both_composition_pools_must_be_claimed(
    campaign: str | None,
    pool: str,
    window_pool: str,
) -> None:
    """A window pool that differs from the target pool needs its own product."""

    row = _composition_target_row(
        pool,
        species_vector={"Ca": "retain"},
        window_pool=window_pool,
        target_id="split",
    )
    claim_keys = _oracle_claims(campaign)
    claimed = _oracle_composition_claimed(row["target"], claim_keys)
    assert campaign_claims_product_objective(
        row["metric"],
        claim_keys,
        composition=True,
        target=row["target"],
    ) is claimed
    profile = _profile(row, campaign=campaign, prose="description-edited")
    executed_keys = _oracle_claims(executed_campaign_id(profile))
    _assert_membership(
        profile,
        row["metric"],
        claimed=_oracle_composition_claimed(row["target"], executed_keys),
        label=f"{campaign}:{pool}:{window_pool}",
    )


@pytest.mark.parametrize(
    ("campaign", "species", "role"),
    _extract_cases(),
    ids=[
        f"{campaign or 'absent'}-{species}-{role}"
        for campaign, species, role in _extract_cases()
    ],
)
def test_captured_products_follow_extracted_species(
    campaign: str | None,
    species: str,
    role: str,
) -> None:
    """Every campaign and every composition species. Retain is not an extract."""

    row = _composition_target_row(
        "captured_products",
        species_vector={species: role},
        target_id="extract",
    )
    claim_keys = _oracle_claims(campaign)
    claimed = _oracle_composition_claimed(row["target"], claim_keys)
    assert campaign_claims_product_objective(
        row["metric"],
        claim_keys,
        composition=True,
        target=row["target"],
    ) is claimed
    profile = _profile(row, campaign=campaign, prose="description-edited")
    executed_keys = _oracle_claims(executed_campaign_id(profile))
    _assert_membership(
        profile,
        row["metric"],
        claimed=_oracle_composition_claimed(row["target"], executed_keys),
        label=f"{campaign}:{species}:{role}",
    )


@pytest.mark.parametrize(
    ("campaign", "species"),
    _multi_extract_cases(),
    ids=[
        f"{campaign or 'absent'}-{'-'.join(species)}"
        for campaign, species in _multi_extract_cases()
    ],
)
def test_captured_products_require_every_extracted_species(
    campaign: str | None,
    species: tuple[str, ...],
) -> None:
    row = _composition_target_row(
        "captured_products",
        species_vector={name: "extract" for name in species},
        target_id="multi",
    )
    claim_keys = _oracle_claims(campaign)
    claimed = _oracle_composition_claimed(row["target"], claim_keys)
    assert campaign_claims_product_objective(
        row["metric"],
        claim_keys,
        composition=True,
        target=row["target"],
    ) is claimed
    profile = _profile(row, campaign=campaign, prose="description-edited")
    executed_keys = _oracle_claims(executed_campaign_id(profile))
    _assert_membership(
        profile,
        row["metric"],
        claimed=_oracle_composition_claimed(row["target"], executed_keys),
        label=f"{campaign}:{species}",
    )


def test_empty_composition_target_is_withheld_for_every_campaign() -> None:
    """No readable product: a recognized claim key does not authorize the row."""

    for campaign in _campaign_ids():
        profile = _profile(_composition_row(), campaign=campaign, prose="both-edited")
        assert campaign_claims_product_objective(
            _COMPOSITION_METRIC,
            _oracle_claims(campaign),
            composition=True,
        ) is False
        _assert_withheld(profile, {_COMPOSITION_METRIC}, label=str(campaign))


def test_notice_keeps_a_claimed_composition_target_beside_an_unclaimed_one() -> None:
    """C2A's silica claim does not withhold a silica pool, and does not keep ceramic."""

    silica = _composition_target_row(
        "captured_stage_3_silica",
        species_vector={"Si": "retain"},
        target_id="silica",
        weight=0.20,
    )
    ceramic = _composition_target_row(
        "terminal_rump_earned",
        species_vector={"Ca": "retain"},
        target_id="ceramic",
        weight=0.30,
    )
    profile = _profile(silica, ceramic, campaign="C2A_continuous", prose="both-edited")
    scored = [definition.metric for definition in objective_definitions(profile)]
    assert silica["metric"] in scored
    assert ceramic["metric"] not in scored
    note = unclaimed_product_withheld_objective_note(profile)
    assert note is not None
    assert ceramic["metric"] in note
    assert silica["metric"] not in note
    evidence = objective_importance_evidence(profile)
    total = math.fsum(row.weight for row in evidence)
    fraction = unclaimed_product_withheld_weight_fraction(profile)
    assert fraction == pytest.approx(0.30 / total)
    weights = unrenormalized_study_scalar_weights(profile)
    assert math.isclose(math.fsum(weights), total - 0.30, rel_tol=0.0, abs_tol=1e-12)


@pytest.mark.parametrize(
    ("run_campaign", "fidelity_campaign", "kind"),
    _fidelity_cases(),
    ids=[
        f"{run or 'absent'}-fid-{fid or 'absent'}-{kind}"
        for run, fid, kind in _fidelity_cases()
    ],
)
def test_fidelity_overlay_is_the_executed_campaign(
    run_campaign: str | None,
    fidelity_campaign: str | None,
    kind: str,
) -> None:
    """Every authored campaign crossed with every fidelity campaign."""

    if kind == "glass":
        row = _product_row("pure_silica_glass_kg")
        metric = "pure_silica_glass_kg"
        target = None
    else:
        row = _composition_target_row(
            "terminal_rump_earned",
            species_vector={"Ca": "retain"},
            target_id="ceramic",
        )
        metric = row["metric"]
        target = row["target"]
    profile = _profile(row, campaign=run_campaign, prose="description-edited")
    if fidelity_campaign is None:
        profile["fidelities"] = {"fast": {"backend_name": "internal-analytical"}}
    else:
        profile["fidelities"] = {"fast": {"campaign": fidelity_campaign}}
    authored_campaign = profile["run"].get("campaign")
    stamped = profile_scored_at_fidelity(profile, "fast")
    assert profile["run"].get("campaign") == authored_campaign
    executed = executed_campaign_id(profile, fidelity="fast")
    assert stamped["run"]["campaign"] == executed
    from simulator.optimize.evaluate import _objective_profile_for_spec

    via_spec = _objective_profile_for_spec(
        profile,
        SimpleNamespace(
            campaign=executed,
            hours=1,
            runtime_campaign_overrides={},
            lab_schedule={},
        ),
    )
    assert via_spec["run"]["campaign"] == executed
    assert profile["run"].get("campaign") == authored_campaign
    claim_keys = _oracle_claims(executed)
    claimed = _oracle_claims_metric(metric, claim_keys, target=target)
    assert campaign_claims_product_objective(
        metric,
        claim_keys,
        composition=target is not None,
        target=target,
    ) is claimed
    _assert_membership(stamped, metric, claimed=claimed, label=f"{run_campaign}:{fidelity_campaign}:{kind}")


def test_shipped_fidelity_blocks_do_not_override_campaign() -> None:
    for name, raw in _shipped_profiles():
        authored = [definition.metric for definition in objective_definitions(raw)]
        for fidelity in raw.get("fidelities") or {}:
            stamped = profile_scored_at_fidelity(raw, fidelity)
            assert stamped["run"]["campaign"] == raw["run"]["campaign"], name
            assert [definition.metric for definition in objective_definitions(stamped)] == authored


def _ledger_species_names() -> tuple[str, ...]:
    """Every species the claim table and the campaign product keys can name.

    Named class metrics are omitted: ``O2_kg`` is not the species ``O2``
    falling through to the ledger. ``not_a_product`` is the class of ledger
    species no campaign claims.
    """

    names = set(COMPOSITION_VECTOR_SPECIES)
    names.update(_LITERAL_EXTRACT_KEYS)
    names.add("Ti")
    for keys in _setpoints_product_keys().values():
        names.update(keys)
    names.add("not_a_product")
    kept = [
        name
        for name in sorted(names)
        if _oracle_ledger_species(f"{name}_kg") == name
    ]
    return tuple(kept)


def _ledger_species_cases() -> list[tuple[str | None, str]]:
    return [
        (campaign, species)
        for campaign in _campaign_ids()
        for species in _ledger_species_names()
    ]


def _species_case_id(campaign: str | None, species: str) -> str:
    return f"{campaign or 'absent'}-{species}"


@pytest.mark.parametrize(
    ("campaign", "species"),
    _ledger_species_cases(),
    ids=[_species_case_id(*case) for case in _ledger_species_cases()],
)
def test_ledger_species_objective_follows_the_executed_campaign(
    campaign: str | None,
    species: str,
) -> None:
    """Every campaign and every ledger species, with the ledger mass supplied.

    Claimed scalar = 0.50 * 12.5 + (-1.24) = 5.01. The 0.50 is the declared
    species weight and is not renormalized. 12.5 kg is the ledger mass and
    the sense is maximize, so the score keeps that sign. -1.24 is the
    retained energy and duration contribution. An unclaimed species adds
    nothing: the scalar stays -1.24 and the vector has no 0.0 and no 12.5.
    """

    metric = f"{species}_kg"
    direct_keys = _oracle_claims(campaign)
    assert campaign_claims_product_objective(metric, direct_keys) is (species in direct_keys)
    profile = _profile(
        _product_row(metric, weight=_SPECIES_WEIGHT),
        campaign=campaign,
        prose="description-edited",
    )
    executed_keys = _oracle_claims(executed_campaign_id(profile))
    claimed = species in executed_keys
    assert campaign_claims_product_objective(metric, executed_keys) is claimed
    ledger = {species: _LEDGER_KG}
    if not claimed:
        _assert_withheld(
            profile,
            {metric},
            label=f"{campaign}:{species}",
            ledger=ledger,
        )
        _assert_study_scalar(
            profile,
            metrics={
                ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC: _STUB_ENERGY_KWH,
                "duration_h": _STUB_DURATION_H,
            },
            weights=(_RETAINED_ENERGY_WEIGHT, _RETAINED_DURATION_WEIGHT),
            scalar=_SYNTHETIC_SCALAR,
        )
        return
    mapping = compute_objectives(profile, _stub_execution(ledger)).as_mapping()
    assert mapping[metric] == pytest.approx(_LEDGER_KG)
    assert metric in mapping
    assert unclaimed_product_withheld_objective_note(profile) is None
    definitions = objective_definitions(profile)
    assert tuple(definition.metric for definition in definitions) == (
        metric,
        ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
        "duration_h",
    )
    weights = unrenormalized_study_scalar_weights(profile)
    assert weights == (
        _SPECIES_WEIGHT,
        _RETAINED_ENERGY_WEIGHT,
        _RETAINED_DURATION_WEIGHT,
    )
    claimed_scalar = math.fsum((_SPECIES_WEIGHT * _LEDGER_KG, _SYNTHETIC_SCALAR))
    record = StudyRecord(
        candidate_id="species-claim-scalar",
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
    assert got_scalar == pytest.approx(claimed_scalar)
    assert signature == frozenset({
        (_LEDGER_KG, -_STUB_ENERGY_KWH, -_STUB_DURATION_H),
    })


def _energy_per_product_cases() -> list[tuple[str | None, str]]:
    return [
        (campaign, metric)
        for campaign in _campaign_ids()
        for metric in sorted(_ENERGY_PER_PRODUCT_METRICS)
    ]


def _intensity_row(metric: str, *, weight: float = _SPECIES_WEIGHT) -> dict:
    """kWh/kg is minimized. ``_objective_row`` would mark it maximize."""

    row = _objective_row(metric, weight=weight, units="kWh/kg")
    row["sense"] = "minimize"
    return row


def _intensity_execution() -> SimpleNamespace:
    """Fe ledger mass is the metals_plus_O2 denominator. The O2 partition is 0.

    ``classify_products`` on a ledger without an atom ledger counts ``Fe`` as
    recovered metal. The record exists and has no oxygen totals, so the class
    total is exactly ``_INTENSITY_DENOMINATOR_KG``. Component kWh sit on the
    cumulative breakdown. The electrical-plus-evaporation objective still
    reads the cumulative 10 kWh attribute, and pumping has no diagnostic so
    its penalty is 0.
    """

    execution = _stub_execution({"Fe": _INTENSITY_DENOMINATOR_KG})
    execution.simulator.energy_cumulative_breakdown_kWh = dict(_LITERAL_COMPONENT_KWH)
    return execution


def _intensity_value(metric: str) -> float:
    component = _LITERAL_INTENSITY_COMPONENT[metric]
    return _LITERAL_COMPONENT_KWH[component] / _INTENSITY_DENOMINATOR_KG


@pytest.mark.parametrize(
    ("campaign", "metric"),
    _energy_per_product_cases(),
    ids=[
        f"{campaign or 'absent'}-{metric}"
        for campaign, metric in _energy_per_product_cases()
    ],
)
def test_energy_per_product_intensity_follows_denominator_claim(
    campaign: str | None,
    metric: str,
) -> None:
    """Every campaign and every kWh/kg intensity.

    The denominator product is metals plus O2. A campaign that does not claim
    both withholds the intensity: it is absent from the vector, named in the
    notice, and does not enter the study scalar as 0.0. A campaign that
    claims both scores component_kWh / 4.0 kg. The 0.50 weight stays declared.
    Sense is minimize, so that term is negated. -1.24 is the retained energy
    and duration contribution and is not renormalized.
    """

    assert set(_LITERAL_INTENSITY_COMPONENT) == _ENERGY_PER_PRODUCT_METRICS
    assert _oracle_ledger_species(metric) is None
    assert _energy_per_product_metric(metric) is not None
    direct_keys = _oracle_claims(campaign)
    claimed = _oracle_claims_metric(metric, direct_keys)
    assert campaign_claims_product_objective(metric, direct_keys) is claimed
    assert campaign_claims_product_objective(metric, frozenset()) is False
    profile = _profile(
        _intensity_row(metric),
        campaign=campaign,
        prose="description-edited",
    )
    executed_keys = _oracle_claims(executed_campaign_id(profile))
    assert _oracle_claims_metric(metric, executed_keys) is claimed
    if not claimed:
        _assert_withheld(
            profile,
            {metric},
            label=f"{campaign}:{metric}",
        )
        _assert_study_scalar(
            profile,
            metrics={
                ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC: _STUB_ENERGY_KWH,
                "duration_h": _STUB_DURATION_H,
            },
            weights=(_RETAINED_ENERGY_WEIGHT, _RETAINED_DURATION_WEIGHT),
            scalar=_SYNTHETIC_SCALAR,
        )
        mapping = compute_objectives(profile, _intensity_execution()).as_mapping()
        assert metric not in mapping
        assert 0.0 not in mapping.values()
        return
    value = _intensity_value(metric)
    assert value != 0.0
    execution = _intensity_execution()
    _assert_study_scalar(
        profile,
        metrics={
            metric: value,
            ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC: _STUB_ENERGY_KWH,
            "duration_h": _STUB_DURATION_H,
        },
        weights=(
            _SPECIES_WEIGHT,
            _RETAINED_ENERGY_WEIGHT,
            _RETAINED_DURATION_WEIGHT,
        ),
        scalar=math.fsum((_SPECIES_WEIGHT * -value, _SYNTHETIC_SCALAR)),
        execution=execution,
    )
    assert unclaimed_product_withheld_objective_note(profile) is None


def _fidelity_load_cases() -> list[tuple[str, str]]:
    return [
        (fidelity, campaign)
        for fidelity in VALID_FIDELITIES
        for campaign in _campaign_ids()
        if isinstance(campaign, str)
    ]


def _glass_only_authored_c0(fidelity: str, executed_campaign: str) -> dict:
    raw = yaml.safe_load((PROFILE_DIR / "lunar_highland.yaml").read_text())
    assert isinstance(raw, dict)
    raw["objectives"] = [
        row
        for row in raw["objectives"]
        if row["metric"] == "pure_silica_glass_kg"
    ]
    assert len(raw["objectives"]) == 1
    assert raw["run"]["campaign"] == "C0"
    raw["run"]["hours"] = 1
    for name in list(raw["fidelities"]):
        block = dict(raw["fidelities"][name])
        block.pop("campaign", None)
        block["hours"] = 1
        raw["fidelities"][name] = block
    selected = dict(raw["fidelities"][fidelity])
    selected["campaign"] = executed_campaign
    raw["fidelities"][fidelity] = selected
    return raw


@pytest.mark.parametrize(
    ("fidelity", "campaign"),
    _fidelity_load_cases(),
    ids=[f"{fidelity}-{campaign}" for fidelity, campaign in _fidelity_load_cases()],
)
def test_file_load_waits_for_the_executed_campaign(
    fidelity: str,
    campaign: str,
    tmp_path: Path,
) -> None:
    """A glass-only file authored as C0 loads. The selected fidelity decides.

    ``load_profile`` and the study file entry do not know the fidelity yet.
    They must not refuse ``no remaining objective weight`` on the authored
    C0 campaign. ``resolve_profile`` at that fidelity refuses only when the
    executed campaign does not claim ``SiO2_glass``.
    """

    raw = _glass_only_authored_c0(fidelity, campaign)
    path = tmp_path / "glass-only.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    loaded = load_profile(path)
    via_study = _load_profile_for_study(str(path))
    assert loaded["run"]["campaign"] == "C0"
    assert via_study["run"]["campaign"] == "C0"
    claimed = _oracle_claims_metric(
        "pure_silica_glass_kg",
        _oracle_claims(campaign),
    )
    if not claimed:
        with pytest.raises(ProfileValidationError, match="no remaining objective weight"):
            resolve_profile(
                via_study,
                expected_feedstock="lunar_highland",
                scoring_fidelity=fidelity,
            )
        return
    resolved = resolve_profile(
        via_study,
        expected_feedstock="lunar_highland",
        scoring_fidelity=fidelity,
    )
    assert resolved["run"]["campaign"] == "C0"
    assert executed_campaign_id(resolved, fidelity=fidelity) == campaign
    stamped = profile_scored_at_fidelity(resolved, fidelity)
    scored = {definition.metric for definition in objective_definitions(stamped)}
    assert "pure_silica_glass_kg" in scored
    assert unclaimed_product_withheld_objective_note(stamped) is None
