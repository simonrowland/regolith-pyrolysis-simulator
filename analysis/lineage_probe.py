"""Trace OpenIMCC coefficient lineage; run with ``python -m analysis.lineage_probe``."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any


DEFAULT_OBSERVATIONS = (
    "holzheid-lodders-2001-copper-solubility::hl01_g_cuo05_recommended",
    "wood-wade-2013-trace-activities::wood_wade_2013_cuo05_activity_coefficient",
    "chen-2020-ce2o3-cmas-activity-phase-equilibria::chen20_t2_101_ce2o3_activity",
    "oneill-2002-feo-activity-coefficients-cmas::oneill_2002_table7_moo3::T=1673.15:row=a",
)


def describe_openimcc_lineage(
    context: Any,
    reference: Any | None,
    coefficient_sources: tuple[str, ...],
    resolver: Any,
    *,
    species_formula: str | None = None,
    manifest_identity: str | None = None,
    manifest_work_ids: tuple[str, ...] = (),
    scopes: tuple[str, ...] | None = None,
    score_relation: str | None = None,
    score_residual_count: int | None = None,
    score_candidate_id: str | None = None,
    engine_prediction: Any | None = None,
    prediction_error: str | None = None,
) -> str:
    """Return a JSON trace of the resolver stages for assertion messages."""

    from simulator.battery import score as score_mod
    from simulator.battery.source_lineage import (
        OPENIMCC_REVIEW_GAMMA_SOURCE_ROW_PREFIX,
        _openimcc_coefficient_ancestry,
        _species_fragment_artifact,
        coefficient_lineage_sources,
        openimcc_source_locator_matches,
        openimcc_species_source_locator_scopes,
    )

    formula = species_formula
    if formula is None and reference is not None:
        species = getattr(reference.identity, "species", None)
        formula = None if species is None else species.formula
    row_ids = tuple(
        source[len(OPENIMCC_REVIEW_GAMMA_SOURCE_ROW_PREFIX) :]
        for source in coefficient_sources
        if source.startswith(OPENIMCC_REVIEW_GAMMA_SOURCE_ROW_PREFIX)
    )
    lineage_sources = coefficient_lineage_sources(coefficient_sources)
    candidate_complete, candidate_lineage = resolver._candidate_lineage(
        coefficient_sources
    )

    artifacts = _openimcc_coefficient_ancestry()
    fragment = _species_fragment_artifact(artifacts, formula)
    fragment_identity = (
        manifest_identity
        or (None if fragment is None else str(fragment.get("identity")))
    )
    selected_work_ids = set(manifest_work_ids) | set(lineage_sources)
    manifest_rows = []
    for artifact in artifacts:
        identity = artifact.get("identity")
        if identity != fragment_identity:
            continue
        for row in artifact.get("rows", ()):
            if not isinstance(row, Mapping):
                continue
            row_id = row.get("id")
            row_work_ids = tuple(
                str(source["work_id"])
                for source in row.get("sources", ())
                if isinstance(source, Mapping)
                and isinstance(source.get("work_id"), str)
            )
            if row_ids and row_id not in row_ids:
                continue
            if not row_ids and selected_work_ids and not (
                selected_work_ids.intersection(row_work_ids)
            ):
                continue
            manifest_rows.append(
                {
                    "identity": identity,
                    "row_id": row_id,
                    "sources": row.get("sources", ()),
                }
            )

    def alias_resolution(work_id: str) -> dict[str, object]:
        work = resolver.indexes.work_by_alias.get(work_id)
        if work is None:
            return {"work_id": work_id, "resolved": False}
        return {
            "work_id": work_id,
            "resolved": True,
            "canonical_work_id": work.work_id,
            "source_ids": work.source_ids,
            "citation": work.citation,
        }

    work_ids = set(selected_work_ids)
    work_ids.update(
        str(source.get("work_id"))
        for row in manifest_rows
        for source in row.get("sources", ())
        if isinstance(source, Mapping) and source.get("work_id")
    )
    work_aliases = [alias_resolution(work_id) for work_id in sorted(work_ids)]

    observation_trace: dict[str, object] | None = None
    decision: dict[str, object] = {
        "candidate_complete": candidate_complete,
        "candidate_ancestry": candidate_lineage,
    }
    if reference is not None:
        experiment = context.experiments.get(reference.experiment_id)
        observation_work_id = None if experiment is None else experiment.work_id
        observation_alias = (
            None
            if observation_work_id is None
            else alias_resolution(observation_work_id)
        )
        reference_lineage = score_mod._reference_lineage_with_work_doi(
            reference, resolver.indexes
        )
        selected_scopes = scopes
        if selected_scopes is None:
            by_work = openimcc_species_source_locator_scopes(
                formula, source_row_id=row_ids[0] if len(row_ids) == 1 else None
            )
        else:
            by_work = {work_id: selected_scopes for work_id in selected_work_ids}
        scope_matches = {
            work_id: openimcc_source_locator_matches(row_scopes, reference.locator)
            for work_id, row_scopes in by_work.items()
        }
        scoped_sources, unresolved_scope = (
            score_mod._coefficient_sources_in_reference_scope(
                reference,
                coefficient_sources,
                reference_lineage,
                coefficient_species_formula=formula,
                _indexes=resolver.indexes,
            )
        )
        if not candidate_complete:
            reason = f"candidate ancestry incomplete: {candidate_lineage}"
            relation = "unknown"
        elif not reference_lineage.complete or reference_lineage.ids is None:
            reason = f"reference ancestry incomplete: {reference_lineage}"
            relation = "unknown"
        elif unresolved_scope:
            reason = (
                "locator scope unresolved; inspect selected row IDs, scope map, "
                "and scope_matches"
            )
            relation = "unknown"
        elif not scoped_sources:
            reason = "all candidate work IDs were scoped out of this observation"
            relation = "independent"
        else:
            scoped_complete, scoped_lineage = resolver._candidate_lineage(
                scoped_sources
            )
            shared_ids = (
                set(reference_lineage.ids).intersection(scoped_lineage.ids or ())
                if scoped_lineage is not None
                else set()
            )
            if not scoped_complete or scoped_lineage is None:
                reason = f"scoped candidate ancestry incomplete: {scoped_lineage}"
                relation = "unknown"
            elif shared_ids:
                reason = f"complete lineages share IDs: {sorted(shared_ids)}"
                relation = "same_input"
            else:
                reason = "complete reference and candidate lineages are disjoint"
                relation = "independent"
        resolver_complete, resolver_relation = resolver.for_reference(
            reference,
            coefficient_sources,
            coefficient_species_formula=formula,
        )
        decision.update(
            {
                "reason": reason,
                "predicted_relation_from_stages": relation,
                "resolver_complete": resolver_complete,
                "resolver_relation": resolver_relation,
                "reference_lineage": reference_lineage,
                "scoped_sources": scoped_sources,
                "unresolved_scope": unresolved_scope,
                "scopes_by_work": by_work,
                "scope_matches": scope_matches,
            }
        )
        observation_trace = {
            "observation_id": reference.observation_id,
            "source_id": reference.source_id,
            "experiment_work_id": observation_work_id,
            "work_alias_resolution": observation_alias,
            "locator": reference.locator,
            "species_formula": formula,
        }
    if score_relation is not None:
        decision["score_store_relation"] = score_relation
    if score_residual_count is not None:
        decision["score_store_openimcc_residual_count"] = score_residual_count
    if score_candidate_id is not None:
        decision["score_store_candidate_id"] = score_candidate_id
    prediction_trace = None
    if engine_prediction is not None:
        prediction_trace = {
            "engine": engine_prediction.engine,
            "execution": engine_prediction.execution,
            "coefficient_sources": engine_prediction.coefficient_sources,
            "lineage_complete": engine_prediction.lineage_complete,
            "value": engine_prediction.value,
            "refusal_reason": engine_prediction.refusal_reason,
            "refusal_detail": engine_prediction.refusal_detail,
        }

    return json.dumps(
        {
            "observation": observation_trace,
            "candidate_coefficient_sources": coefficient_sources,
            "candidate_lineage_sources": lineage_sources,
            "candidate_row_ids": row_ids,
            "manifest_rows": manifest_rows,
            "resolved_manifest_and_observation_works": work_aliases,
            "engine_prediction": prediction_trace,
            "engine_prediction_error": prediction_error,
            "decision": decision,
        },
        default=str,
        sort_keys=True,
    )


def _trace_observation(observation_key: str) -> None:
    from simulator.battery.enums import Engine
    from simulator.battery import score as score_mod
    from simulator.battery.score import load_score_context, score_store

    source_id, separator, observation_suffix = observation_key.partition("::")
    if not separator:
        raise ValueError("observation keys must be source_id::observation_id_suffix")
    context = load_score_context(sources=(source_id,))
    references = [
        observation
        for observation in context.observations.values()
        if observation.source_id == source_id
        and (
            observation.observation_id == observation_key
            or observation_suffix in observation.observation_id
        )
    ]
    if len(references) != 1:
        print(
            json.dumps(
                {
                    "observation_key": observation_key,
                    "matches": [row.observation_id for row in references],
                },
                sort_keys=True,
            )
        )
        return
    reference = references[0]
    context = replace(context, observations={reference.observation_id: reference})
    residuals, candidates = score_store(
        context, engines=(Engine.OPENIMCC,), include_diagnostics=False
    )
    scored = [
        residual
        for residual in residuals
        if residual.reference == reference.observation_id
        and residual.candidate_request is not None
        and residual.candidate_request.engine is Engine.OPENIMCC
    ]
    residual = scored[0] if scored else None
    candidate = (
        None
        if residual is None or residual.candidate is None
        else candidates.get(residual.candidate)
    )
    prediction = None if candidate is None else candidate.engine
    prediction_error = None
    if prediction is None:
        experiment = context.experiments.get(reference.experiment_id)
        try:
            prediction = score_mod.predict_with_engine(
                Engine.OPENIMCC,
                reference,
                experiment=experiment,
            )
        except Exception as error:
            prediction_error = f"{type(error).__name__}: {error}"
    sources = () if prediction is None else tuple(prediction.coefficient_sources)
    resolver = score_mod._ScoringLineageResolver(context)
    print(
        describe_openimcc_lineage(
            context,
            reference,
            sources,
            resolver,
            score_relation=(
                None if residual is None else residual.source_relation.value
            ),
            score_residual_count=len(scored),
            score_candidate_id=(
                None if residual is None else residual.candidate
            ),
            engine_prediction=prediction,
            prediction_error=prediction_error,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "observation_keys",
        nargs="*",
        help="source_id::observation_id_suffix; defaults to the four r8 rows",
    )
    args = parser.parse_args()
    for observation_key in args.observation_keys or DEFAULT_OBSERVATIONS:
        _trace_observation(observation_key)


if __name__ == "__main__":
    main()
