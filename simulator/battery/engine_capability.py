"""Declared engine × quantity capability.

One table. Thermochemistry routing reads the thermo slice. The production
score loop emits a residual only when the observation's quantity is in
that engine's set, or a source-specific residue adapter binds. A pair
outside the set is a capability-matrix cell, not a refusal. Residue
quantities stay out of that matrix: the adapter is the source, not the
engine. Diagnostic and duplicate refs the loop skips are counted on
their own line.

The predict-time ladder (``_engine_lacks_quantity_capability``) is a
different question: it still enters predict for fusion on every engine
and for MAGEMin vapour, and those paths return a non-numeric refusal.
This module does not replace that ladder.

No quantity was added by looking at residuals. Membership is which
adapter the engine actually dispatches.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping, Sequence

from simulator.battery.enums import (
    FORMATION_QUANTITIES,
    FUSION_QUANTITIES,
    PHASE_ROW_QUANTITIES,
    PURE_STANDARD_THERMO,
    EQUILIBRIUM_FIT_QUANTITIES,
    Engine,
    Quantity,
    ResidualStatus,
)
from simulator.battery.validate import _VAPOUR_EQUILIBRIUM

# Formation, equilibrium-fit, pure-phase, and fusion quantities. log10_K_star
# is in the equilibrium-fit set and in no engine's declared set.
THERMO_QUANTITIES = (
    FORMATION_QUANTITIES
    | EQUILIBRIUM_FIT_QUANTITIES
    | PURE_STANDARD_THERMO
    | FUSION_QUANTITIES
)
# Ellingham ΔfG and log10 Kf. Internal Analytical is the only owner.
ELLINGHAM_QUANTITIES = frozenset({Quantity.DELTA_FG, Quantity.LOG10_KF})
# Cp, S, and H(T)−H(298.15 K). VapoRock, ThermoEngine, MAGEMin, and NASA.
PURE_PHASE_QUANTITIES = frozenset(
    {Quantity.CP, Quantity.S, Quantity.H_MINUS_H298}
)

# Generic cell dispatch: melt activity, phase rows, and the shared
# attempt quantities. Vapour pressures are added per engine. MAGEMin has
# no vapour adapter: predict returns engine_missing_observable and no value.
_ACTIVITY = frozenset({Quantity.ACTIVITY, Quantity.ACTIVITY_COEFFICIENT})
_SHARED_ATTEMPT = frozenset(
    {
        Quantity.MASS_LOSS_FRACTION,
        Quantity.FE3_FE2_RATIO,
        Quantity.VALENCE_RATIO,
        Quantity.WALL_DEPOSIT_AREAL_RATE,
        # Dew-point rows return a gas-thermo refusal, not a number. The
        # quantity stays covered so that refusal is still emitted. A
        # non-dew-point row remains quantity_not_predicted inside predict.
        Quantity.TRANSITION_TEMPERATURE,
    }
)
_CELL_BASE = _ACTIVITY | PHASE_ROW_QUANTITIES | _SHARED_ATTEMPT

# NASA's predict path returns before the cell dispatch unless the quantity
# is Fe3+/Fe2+ or in the thermo set plus saturation pressure.
_NASA_QUANTITIES = frozenset(
    {
        Quantity.CP,
        Quantity.S,
        Quantity.H_MINUS_H298,
        Quantity.DELTA_FH,
        Quantity.DELTA_FG,
        Quantity.P_SAT,
        Quantity.FE3_FE2_RATIO,
    }
)

NO_HEADLINE_RAIL = "no_headline_rail"


def _cell_quantities(*, vapour: bool, thermo: frozenset[Quantity]) -> frozenset[Quantity]:
    quantities = set(_CELL_BASE)
    if vapour:
        quantities |= set(_VAPOUR_EQUILIBRIUM)
    quantities |= set(thermo)
    return frozenset(quantities)


# Residue quantities are intentionally absent. A Hashimoto, Sossi, or
# Knudsen residue adapter is a call-site exception, not a generic cell.
# The matrix walk skips these too, so a bound residual is not counted
# as a pair the score loop did not emit.
_RESIDUE_QUANTITIES = frozenset(
    {
        Quantity.RESIDUE_COMPONENT_COMPOSITION,
        Quantity.ELEMENT_RETENTION_RATIO,
    }
)
ENGINE_QUANTITIES: dict[Engine, frozenset[Quantity]] = {
    Engine.VAPOROCK: _cell_quantities(vapour=True, thermo=PURE_PHASE_QUANTITIES),
    Engine.ALPHAMELTS: _cell_quantities(vapour=True, thermo=frozenset()),
    Engine.THERMOENGINE: _cell_quantities(vapour=True, thermo=PURE_PHASE_QUANTITIES),
    Engine.MAGEMIN: _cell_quantities(vapour=False, thermo=PURE_PHASE_QUANTITIES),
    Engine.OPENIMCC: _cell_quantities(vapour=True, thermo=frozenset()),
    Engine.INTERNAL_ANALYTICAL: _cell_quantities(
        vapour=True, thermo=ELLINGHAM_QUANTITIES | FUSION_QUANTITIES
    ),
    Engine.NASA_CEA_9: _NASA_QUANTITIES,
    Engine.CATALOG_EVALUATOR: frozenset(_VAPOUR_EQUILIBRIUM),
}


def engine_quantities(engine: Engine) -> frozenset[Quantity]:
    return ENGINE_QUANTITIES.get(engine, frozenset())


def engine_covers_quantity(engine: Engine, quantity: Quantity) -> bool:
    """Whether this engine's declared adapter set includes the quantity."""

    return quantity in engine_quantities(engine)


def engine_thermo_quantities(engine: Engine) -> frozenset[Quantity]:
    """Thermochemistry slice of the declared set. Empty when undeclared."""

    return engine_quantities(engine) & THERMO_QUANTITIES


def any_declared_engine_covers(quantity: Quantity) -> bool:
    return any(quantity in quantities for quantities in ENGINE_QUANTITIES.values())


def _coverage_text(scored: int, callable_observations: int) -> str:
    if callable_observations == 0:
        return "—"
    return f"{scored / callable_observations:.3f}"


def capability_summary(
    *,
    observations: Mapping[str, object],
    origins: Mapping[str, str] | None,
    experiments: Mapping[str, object] | None,
    engines: Sequence[Engine],
    scored_observation_ids: Iterable[str],
    n_records: int,
    n_scored_records: int,
    n_refused_records: int,
) -> dict[str, object]:
    """Engine × quantity × rail counts for comparison candidates.

    The walk is ``comparison_candidates``, the same population as the live
    candidate census. It is not multiplied until the engine axis below.
    """

    from simulator.battery.compilation_tier import _compilation_engines_for_scoring
    from simulator.battery.identity import Identity
    from simulator.battery.score import (
        ScoreContext,
        _has_duplicate_observation_link,
        _refusal_diagnostic_references,
        comparison_candidates,
        diagnostic_references,
        quantity_token,
        rail_for_quantity,
    )
    from simulator.battery.score_run import _residue_adapter_binds, _scoring_quantity

    context = ScoreContext(
        {},
        experiments or {},
        observations,
        origins or {},
    )
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    uncovered: dict[tuple[str, str], int] = defaultdict(int)
    callable_ids: set[str] = set()
    n_quantity_unknown = 0
    engine_list = tuple(engines)
    candidates = comparison_candidates(context)
    for observation in candidates:
        identity = observation.identity
        quantity = quantity_token(identity) if isinstance(identity, Identity) else None
        if quantity is None:
            n_quantity_unknown += 1
            continue
        if quantity in _RESIDUE_QUANTITIES:
            continue
        quantity = _scoring_quantity(observation, quantity)
        formula = (
            identity.species.formula
            if isinstance(identity, Identity) and identity.species is not None
            else ""
        )
        rail = rail_for_quantity(quantity, species_formula=formula)
        rail_token = NO_HEADLINE_RAIL if rail is None else rail.value
        if any(engine_covers_quantity(engine, quantity) for engine in engine_list):
            callable_ids.add(observation.observation_id)
        if not any_declared_engine_covers(quantity):
            uncovered[(quantity.value, rail_token)] += 1
        for engine in engine_list:
            counts[(engine.value, quantity.value, rail_token)] += 1
    seen = {observation.observation_id for observation in candidates}
    outside: list[object] = []
    for observation in diagnostic_references(context):
        if observation.observation_id not in seen:
            outside.append(observation)
            seen.add(observation.observation_id)
    for observation in _refusal_diagnostic_references(context):
        if observation.observation_id not in seen:
            outside.append(observation)
            seen.add(observation.observation_id)
    for observation in context.observations.values():
        if observation.observation_id in seen:
            continue
        if _has_duplicate_observation_link(observation, context.experiments):
            outside.append(observation)
            seen.add(observation.observation_id)
    n_outside_census_not_emitted = 0
    for observation in outside:
        identity = observation.identity
        quantity = quantity_token(identity) if isinstance(identity, Identity) else None
        if quantity is None:
            continue
        quantity = _scoring_quantity(observation, quantity)
        offered = _compilation_engines_for_scoring(
            engine_list,
            observation,
            context.origins.get(observation.observation_id),
            production_scoring=True,
        )
        experiment = context.experiments.get(observation.experiment_id)
        for engine in offered:
            if engine_covers_quantity(engine, quantity):
                continue
            if _residue_adapter_binds(observation, engine, experiment, quantity):
                continue
            n_outside_census_not_emitted += 1
    rows = [
        {
            "covered": engine_covers_quantity(Engine(engine), Quantity(quantity)),
            "engine": engine,
            "n_observations": n,
            "quantity": quantity,
            "rail": rail,
        }
        for (engine, quantity, rail), n in sorted(counts.items())
    ]
    scored = callable_ids.intersection(scored_observation_ids)
    cross_product_total = sum(row["n_observations"] for row in rows)
    uncovered_pair_total = sum(
        row["n_observations"] for row in rows if not row["covered"]
    )
    return {
        "capability_matrix": {
            "cross_product_total": cross_product_total,
            "engines": [engine.value for engine in engine_list],
            "n_candidate_observations": cross_product_total // len(engine_list)
            if engine_list
            else 0,
            "n_outside_census_not_emitted": n_outside_census_not_emitted,
            "n_quantity_unknown": n_quantity_unknown,
            "quantities_no_engine_predicts": [
                {"n_observations": n, "quantity": quantity, "rail": rail}
                for (quantity, rail), n in sorted(uncovered.items())
            ],
            "rows": rows,
            "uncovered_pair_total": uncovered_pair_total,
        },
        "coverage": {
            "callable_observations": len(callable_ids),
            "coverage": _coverage_text(len(scored), len(callable_ids)),
            "n_records": n_records,
            "n_refused_records": n_refused_records,
            "n_scored_observations": len(scored),
            "n_scored_records": n_scored_records,
        },
    }


def residual_record_stats(
    residuals: Iterable[object],
) -> tuple[int, int, int, frozenset[str]]:
    n = n_scored = n_refused = 0
    scored: set[str] = set()
    for residual in residuals:
        n += 1
        if residual.numeric is not None:
            n_scored += 1
            scored.add(str(residual.reference))
        if residual.status is ResidualStatus.REFUSED:
            n_refused += 1
    return n, n_scored, n_refused, frozenset(scored)


def payload_record_stats(
    rows: Iterable[Mapping[str, object]],
) -> tuple[int, int, int, frozenset[str]]:
    n = n_scored = n_refused = 0
    scored: set[str] = set()
    refused = ResidualStatus.REFUSED.value
    for row in rows:
        n += 1
        numeric = row.get("numeric")
        if isinstance(numeric, Mapping) and numeric.get("value") is not None:
            n_scored += 1
            scored.add(str(row.get("reference") or ""))
        if row.get("status") == refused:
            n_refused += 1
    return n, n_scored, n_refused, frozenset(scored)
