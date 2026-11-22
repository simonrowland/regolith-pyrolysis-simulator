"""Own prepared score runs and shard scoring."""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import pickle
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping, Sequence

from simulator.battery.source_declarations import (
    HASHIMOTO_RESIDUE_PREDICTOR,
    SOSSI_RESIDUE_PREDICTOR,
    declared_residue_predictor,
)

if TYPE_CHECKING:
    from simulator.battery.score import (
        CandidateRequest,
        DecisionBand,
        Engine,
        EnginePrediction,
        Execution,
        ExecutionState,
        Fo2Mode,
        Identity,
        MethodToken,
        Observation,
        Quantity,
        Rail,
        RefusalReason,
        Residual,
        ResidualRefusal,
        ResidualStatus,
        ScoreContext,
        _ResidualJsonlStream,
        _ResidualReportMetadata,
        _ValidationIndexes,
        _ScoreShardResult,
    )


def _residue_adapter_binds(point, engine, experiment, quantity) -> bool:
    """Whether a source-specific residue predictor will run for this pair.

    Residue quantities are not in the generic capability table. Hashimoto
    and Sossi bind only on openimcc and internal-analytical. Knudsen binds
    on every engine when the experiment method is Knudsen effusion. The
    three assignments below this predicate still decide which predictor
    wins; this only keeps that pair inside the score loop.
    """

    from simulator.battery.enums import Engine, MethodToken, Quantity
    from simulator.battery.identity import Identity

    if quantity not in {
        Quantity.RESIDUE_COMPONENT_COMPOSITION,
        Quantity.ELEMENT_RETENTION_RATIO,
    }:
        return False
    if not isinstance(point.identity, Identity):
        return False
    predictor = declared_residue_predictor(point.source_id)
    if predictor == HASHIMOTO_RESIDUE_PREDICTOR and engine in {
        Engine.OPENIMCC,
        Engine.INTERNAL_ANALYTICAL,
    }:
        return True
    if predictor == SOSSI_RESIDUE_PREDICTOR and engine in {
        Engine.OPENIMCC,
        Engine.INTERNAL_ANALYTICAL,
    }:
        return True
    return bool(
        experiment is not None
        and experiment.method.is_value
        and experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    )


def _residue_cohort_selection(
    selected_experiment_ids: frozenset[str] | None,
    test_experiment_ids: frozenset[str] | None,
) -> frozenset[str] | None:
    return (
        selected_experiment_ids
        if selected_experiment_ids is not None
        else test_experiment_ids
    )


@dataclass(frozen=True)
class _PreparedScoreRun:
    context: ScoreContext
    engine_set: tuple[Engine, ...]
    refs: tuple[Observation, ...]
    fusion_diagnostic_ids: frozenset[str]
    comparison_ids: frozenset[str]
    observations: Mapping[str, Observation]
    origins: Mapping[str, str]
    live_context: ScoreContext
    condensed_frame_diagnostics: Mapping[
        tuple[str, str], dict[str, object]
    ]
    point_observations_by_experiment: Mapping[str, tuple[Observation, ...]]
    table_index: Mapping[tuple[str, Quantity], tuple[Observation, ...]]
    ratchet_missing: bool
    source_bands: Mapping[str, DecisionBand]
    source_band_reasons: Mapping[str, str]
    kems_partial_pressure_band: DecisionBand | None
    series_point_counts: Mapping[str, int]
    test_experiment_ids: frozenset[str] | None
    hashimoto_experiment_ids: frozenset[str]
    sossi_experiment_ids: frozenset[str]
    validation_indexes: _ValidationIndexes


def _report_selected_species_count(
    prepared: _PreparedScoreRun,
    species_formulas: Sequence[str],
    stream: _ResidualJsonlStream,
) -> None:
    stream.selected_reference_ids.update(obs.observation_id for obs in prepared.refs)
    if species_formulas:
        print(
            f"species references selected={len(prepared.refs)} "
            f"formulas={','.join(species_formulas)}",
            flush=True,
        )


def _prepare_score_run(
    context: ScoreContext,
    *,
    engines: Sequence[Engine] | None,
    rail: Rail | None,
    work_id: str | None,
    limit: int | None,
    include_diagnostics: bool,
    measured_only: bool,
    species_formulas: Sequence[str] = (),
    _test_experiment_ids: Sequence[str] | None = None,
) -> _PreparedScoreRun:
    """Build global score inputs once so spawned shards share one snapshot."""
    from simulator.battery.score import (
        MEASURED_EVIDENCE,
        SCORE_ENGINE_SET,
        Identity,
        Quantity,
        _build_validation_indexes,
        _condensed_element_reference_table_diagnostics,
        _has_duplicate_observation_link,
        _is_fusion_conversion_notice,
        _matches_species_filter,
        _observation_matches_run_filters,
        _partial_pressure_observations_by_experiment,
        _read_decision_band_ratchet,
        _refusal_diagnostic_references,
        _source_band_catalog,
        _validated_score_engines,
        comparison_candidates,
        derive_kems_partial_pressure_band,
        diagnostic_references,
        fusion_comparison_reference,
        quantity_token,
    )
    engine_set = (
        _validated_score_engines(tuple(engines))
        if engines is not None
        else SCORE_ENGINE_SET
    )
    comparison_observations = comparison_candidates(context)
    refs = list(comparison_observations)
    seen = {o.observation_id for o in refs}
    fusion_diagnostic_ids: set[str] = set()
    if include_diagnostics:
        for obs in diagnostic_references(context):
            if obs.observation_id not in seen:
                refs.append(obs)
                seen.add(obs.observation_id)
        for obs in context.observations.values():
            converted = fusion_comparison_reference(obs)
            if converted is not obs:
                if any(
                    _is_fusion_conversion_notice(notice) for notice in converted.notices
                ):
                    fusion_diagnostic_ids.add(obs.observation_id)
                if obs.observation_id not in seen:
                    refs.append(converted)
                    seen.add(obs.observation_id)
        for obs in _refusal_diagnostic_references(context):
            if obs.observation_id not in seen:
                refs.append(obs)
                seen.add(obs.observation_id)
    for obs in context.observations.values():
        if (
            _has_duplicate_observation_link(obs, context.experiments)
            and obs.observation_id not in seen
        ):
            refs.append(obs)
            seen.add(obs.observation_id)
    if measured_only:
        refs = [
            obs for obs in refs
            if obs.evidence.class_.is_value
            and obs.evidence.class_.value in MEASURED_EVIDENCE
        ]
    selected_species = set(species_formulas)
    species_filter = (
        (lambda observation: _matches_species_filter(observation, selected_species))
        if selected_species
        else None
    )
    if work_id or rail is not None or species_filter is not None:
        refs = [
            obs
            for obs in refs
            if _observation_matches_run_filters(
                context,
                obs,
                work_id=work_id,
                rail=rail,
                observation_filter=species_filter,
            )
        ]
    # Test-only: bypasses cohort-size guards for bounded cache parity tests.
    selected_test_experiment_ids = (
        None
        if _test_experiment_ids is None
        else frozenset(str(value) for value in _test_experiment_ids)
    )
    if selected_test_experiment_ids is not None:
        refs = [
            obs
            for obs in refs
            if obs.experiment_id in selected_test_experiment_ids
        ]
    refs.sort(key=lambda o: o.observation_id)
    if limit is not None:
        refs = refs[: int(limit)]
    hashimoto_experiment_ids = frozenset(
        obs.experiment_id
        for obs in refs
        if declared_residue_predictor(obs.source_id) == HASHIMOTO_RESIDUE_PREDICTOR
        and isinstance(obs.identity, Identity)
        and quantity_token(obs.identity)
        is Quantity.RESIDUE_COMPONENT_COMPOSITION
    )
    sossi_experiment_ids = frozenset(
        obs.experiment_id.rsplit("::", 1)[-1]
        for obs in refs
        if declared_residue_predictor(obs.source_id) == SOSSI_RESIDUE_PREDICTOR
        and isinstance(obs.identity, Identity)
        and quantity_token(obs.identity)
        is Quantity.RESIDUE_COMPONENT_COMPOSITION
    )
    from simulator.battery.compilation_tier import _compilation_series_point_count

    observations = dict(context.observations)
    origins = context.origins
    live_context = replace(context, observations=observations, origins=origins)
    condensed_frame_diagnostics = _condensed_element_reference_table_diagnostics(
        observations.values()
    )
    point_observations_by_experiment = _partial_pressure_observations_by_experiment(
        observations.values()
    )
    validation_indexes = _build_validation_indexes(
        context.works, observations, context.experiments
    )
    table_index = validation_indexes.printed_thermo_index
    ratchet_entries, ratchet_missing, _ratchet_history = _read_decision_band_ratchet(
        context.root
    )
    source_bands, _source_band_records, source_band_reasons = _source_band_catalog(
        context, ratchet_entries
    )
    kems_partial_pressure_band = derive_kems_partial_pressure_band(
        observations, context.experiments, benches=context.benches
    )
    series_point_counts = {
        obs.observation_id: _compilation_series_point_count(
            obs, origins.get(obs.observation_id)
        )
        for obs in refs
    }
    return _PreparedScoreRun(
        context=context,
        engine_set=tuple(engine_set),
        refs=tuple(refs),
        fusion_diagnostic_ids=frozenset(fusion_diagnostic_ids),
        comparison_ids=frozenset(o.observation_id for o in comparison_observations),
        observations=observations,
        origins=origins,
        live_context=live_context,
        condensed_frame_diagnostics=condensed_frame_diagnostics,
        point_observations_by_experiment=point_observations_by_experiment,
        table_index=table_index,
        ratchet_missing=ratchet_missing,
        source_bands=source_bands,
        source_band_reasons=source_band_reasons,
        kems_partial_pressure_band=kems_partial_pressure_band,
        series_point_counts=series_point_counts,
        test_experiment_ids=selected_test_experiment_ids,
        hashimoto_experiment_ids=hashimoto_experiment_ids,
        sossi_experiment_ids=sossi_experiment_ids,
        validation_indexes=validation_indexes,
    )


def _score_group_id(observation: Observation) -> str:
    if declared_residue_predictor(observation.source_id) is not None:
        return f"source:{observation.source_id}"
    if observation.experiment_id:
        return f"experiment:{observation.experiment_id}"
    if observation.source_id:
        return f"source:{observation.source_id}"
    return "unassigned"


def _partition_score_refs(
    refs: Sequence[Observation], *, workers: int
) -> tuple[tuple[Observation, ...], ...]:
    """Assign whole experiment and residue-cohort groups to stable buckets."""

    if workers < 1:
        raise ValueError("workers must be at least 1")
    group_buckets = {
        group_id: int.from_bytes(
            hashlib.sha256(group_id.encode("utf-8")).digest()[:8], "big"
        ) % workers
        for group_id in {_score_group_id(observation) for observation in refs}
    }
    chunks: list[list[Observation]] = [[] for _ in range(workers)]
    for observation in refs:
        chunks[group_buckets[_score_group_id(observation)]].append(observation)
    return tuple(tuple(chunk) for chunk in chunks)


def _score_store_with_decisions(
    context: ScoreContext,
    *,
    engines: Sequence[Engine] | None = None,
    rail: Rail | None = None,
    species_formulas: Sequence[str] = (),
    work_id: str | None = None,
    limit: int | None = None,
    include_diagnostics: bool = True,
    measured_only: bool = False,
    _test_experiment_ids: Sequence[str] | None = None,
    predict: Callable[..., EnginePrediction] | None = None,
    handles: Mapping[str, object] | None = None,
    _prepared: _PreparedScoreRun | None = None,
    _engine_set: Sequence[Engine] | None = None,
    _refs: Sequence[Observation] | None = None,
    _stream: _ResidualJsonlStream | None = None,
    _shard_failure_reason: str | None = None,
    _skip_sort_orders: frozenset[tuple[str, int, int, int]] = frozenset(),
) -> tuple[tuple[Residual, ...], dict[str, Observation], list[dict[str, object]]]:
    from simulator.battery.score import (
        ENGINE_CHANNELS,
        ENGINE_COEFFICIENT_SOURCES,
        Engine,
        EnginePrediction,
        Execution,
        ExecutionState,
        Fo2Mode,
        Identity,
        MethodToken,
        Observation,
        Quantity,
        RefusalReason,
        Residual,
        _ResidualJsonlStream,
        _ScoringLineageResolver,
        _compilation_decision_strata,
        _compile_residual_with_lineage,
        _duplicate_observation_refusal_detail,
        _group_token,
        _has_duplicate_observation_link,
        _has_intrinsic_redox_activity,
        _hashimoto_residue_prediction,
        _knudsen_residue_prediction,
        _prepare_score_run,
        _source_alignment_group_key,
        _sossi_residue_prediction,
        _validated_score_engines,
        is_compilation_source,
        is_internal_consistency,
        is_sf04_workbook,
        quantity_token,
    )
    prepared = _prepared or _prepare_score_run(
        context,
        engines=engines,
        rail=rail,
        species_formulas=species_formulas,
        work_id=work_id,
        limit=limit,
        include_diagnostics=include_diagnostics,
        measured_only=measured_only,
        _test_experiment_ids=_test_experiment_ids,
    )
    if _stream is not None and _prepared is None:
        _report_selected_species_count(prepared, species_formulas, _stream)
    if prepared.context is not context:
        raise ValueError("prepared score context does not match the scoring context")
    engine_set = (
        prepared.engine_set
        if _engine_set is None
        else _validated_score_engines(tuple(_engine_set))
    )
    engine_order = {
        engine: index for index, engine in enumerate(prepared.engine_set)
    }
    refs = prepared.refs if _refs is None else tuple(_refs)
    fusion_diagnostic_ids = prepared.fusion_diagnostic_ids
    comparison_ids = prepared.comparison_ids
    observations = prepared.observations
    origins = prepared.origins
    live_context = prepared.live_context
    point_observations_by_experiment = prepared.point_observations_by_experiment
    table_index = prepared.table_index
    ratchet_missing = prepared.ratchet_missing
    source_bands = prepared.source_bands
    source_band_reasons = prepared.source_band_reasons
    kems_partial_pressure_band = prepared.kems_partial_pressure_band
    from simulator.battery.compilation_tier import (
        _SelectedResiduals, _compilation_engines_for_scoring,
        _is_compilation_thermo_reference,
        _iter_compilation_series_cells,
        is_compilation_evidence,
    )
    from simulator.battery.engine_capability import engine_covers_quantity
    from simulator.battery.validate import _bound_work_inputs

    provided_handles = dict(handles or {})
    resolved_handles = dict(provided_handles)
    residuals: list[Residual] | None = [] if _stream is None else None
    candidates: dict[str, Observation] = {}
    validation_indexes = prepared.validation_indexes
    empirical_ids = comparison_ids
    lineage_resolver = _ScoringLineageResolver(live_context, validation_indexes)
    started = time.monotonic()
    last_progress = started
    done = 0
    skipped_capability = 0
    total = sum(
        prepared.series_point_counts.get(obs.observation_id, 1) for obs in refs
    ) * max(len(engine_set), 1)

    def report_progress_if_due() -> None:
        nonlocal last_progress
        now = time.monotonic()
        if now - last_progress < 60:
            return
        if _stream is not None:
            _stream.flush()
        progress = f"score progress {done}/{total} residuals"
        if skipped_capability:
            progress += f" skipped_capability={skipped_capability}"
        if _stream is not None:
            progress += f" written={_stream.count}"
        print(
            f"{progress} {int(now - started)}s host={context.hostname}",
            flush=True,
        )
        last_progress = now

    residue_prediction_cache: dict[
        tuple[str, Engine, frozenset[str] | None], Mapping[str, object]
    ] = {}
    try:
        with _bound_work_inputs(
            context.works,
            observations,
            context.experiments,
            _indexes=validation_indexes,
        ):
            for obs in refs:
                origin = origins.get(obs.observation_id)
                duplicate_detail = _duplicate_observation_refusal_detail(context, obs)
                for point_index, point in enumerate(
                    _iter_compilation_series_cells(obs, origin)
                ):
                    point_origin = origins.get(point.observation_id, origin or "")
                    diagnostic = (
                        obs.observation_id not in empirical_ids
                        or is_internal_consistency(point_origin)
                        or is_compilation_source(point.source_id, point_origin)
                        or is_compilation_evidence(point)
                        or is_sf04_workbook(point)
                    )
                    quantity = (
                        quantity_token(point.identity)
                        if isinstance(point.identity, Identity)
                        else None
                    )
                    compilation_thermo = _is_compilation_thermo_reference(
                        point, point_origin
                    )
                    source_tier = (
                        "compilation"
                        if is_compilation_evidence(point)
                        or is_compilation_source(point.source_id, point_origin)
                        else "empirical"
                        if obs.observation_id in comparison_ids
                        else None
                    )
                    source_band = None
                    source_band_reason = None
                    if (
                        source_tier is not None
                        and not is_internal_consistency(point_origin)
                        and not is_sf04_workbook(point)
                    ):
                        group_key = _source_alignment_group_key(
                            point, tier=source_tier
                        )
                        if group_key is not None:
                            source_key = _group_token(group_key)
                            source_band = source_bands.get(source_key)
                            source_band_reason = source_band_reasons.get(source_key)
                    derived_band = source_band
                    derived_band_reason = source_band_reason
                    experiment = context.experiments.get(point.experiment_id)
                    if (
                        derived_band is None
                        and quantity is Quantity.P_PARTIAL
                        and experiment is not None
                        and experiment.method.is_value
                        and experiment.method.value is MethodToken.KNUDSEN_EFFUSION
                    ):
                        derived_band = kems_partial_pressure_band
                        derived_band_reason = None
                    for engine in _compilation_engines_for_scoring(
                        engine_set,
                        point,
                        point_origin,
                        production_scoring=predict is None,
                    ):
                        # An engine with no adapter for this quantity is a
                        # capability-matrix cell, not a residual. Injected
                        # predictors still see every engine. Residue adapters
                        # stay in the loop even though the quantity is absent
                        # from the generic table.
                        if (
                            predict is None
                            and quantity is not None
                            and not engine_covers_quantity(engine, quantity)
                            and not _residue_adapter_binds(
                                point, engine, experiment, quantity
                            )
                        ):
                            done += 1
                            skipped_capability += 1
                            report_progress_if_due()
                            continue
                        prediction = None
                        scoring_predict = (
                            partial(
                                _score_failed_shard_prediction,
                                failure_reason=_shard_failure_reason,
                            )
                            if _shard_failure_reason is not None
                            else predict
                        )
                        lineage_id = (
                            obs.observation_id
                            if point.observation_id != obs.observation_id
                            else None
                        )
                        diagnostic_prediction = (
                            duplicate_detail is None
                            and diagnostic
                            and point.observation_id not in fusion_diagnostic_ids
                            and predict is None
                            and not compilation_thermo
                        )
                        prediction = None
                        if diagnostic_prediction:
                            prediction = EnginePrediction(
                                engine=engine,
                                channel=ENGINE_CHANNELS[engine],
                                execution=Execution(state=ExecutionState.NOT_PROBED),
                                coefficient_sources=ENGINE_COEFFICIENT_SOURCES[engine],
                                lineage_complete=False,
                                refusal_reason=RefusalReason.UNSUPPORTED,
                                refusal_detail={"reason": "diagnostic_population"},
                                identity=point.identity if isinstance(point.identity, Identity) else None,
                            )
                        if (
                            _shard_failure_reason is None
                            and duplicate_detail is None
                            and predict is None
                            and isinstance(point.identity, Identity)
                            and quantity in {
                                Quantity.RESIDUE_COMPONENT_COMPOSITION,
                                Quantity.ELEMENT_RETENTION_RATIO,
                            }
                            and declared_residue_predictor(point.source_id)
                            == HASHIMOTO_RESIDUE_PREDICTOR
                            and engine in {Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL}
                        ):
                            prediction = _hashimoto_residue_prediction(
                                context,
                                point,
                                engine,
                                residue_prediction_cache,
                                selected_experiment_ids=prepared.hashimoto_experiment_ids,
                                test_experiment_ids=prepared.test_experiment_ids,
                            )
                        if (
                            _shard_failure_reason is None
                            and duplicate_detail is None
                            and predict is None
                            and isinstance(point.identity, Identity)
                            and quantity in {
                                Quantity.RESIDUE_COMPONENT_COMPOSITION,
                                Quantity.ELEMENT_RETENTION_RATIO,
                            }
                            and declared_residue_predictor(point.source_id)
                            == SOSSI_RESIDUE_PREDICTOR
                            and engine in {Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL}
                        ):
                            prediction = _sossi_residue_prediction(
                                context,
                                point,
                                engine,
                                residue_prediction_cache,
                                selected_experiment_ids=prepared.sossi_experiment_ids,
                                test_experiment_ids=prepared.test_experiment_ids,
                            )
                        if (
                            _shard_failure_reason is None
                            and duplicate_detail is None
                            and predict is None
                            and isinstance(point.identity, Identity)
                            and quantity in {
                                Quantity.RESIDUE_COMPONENT_COMPOSITION,
                                Quantity.ELEMENT_RETENTION_RATIO,
                            }
                            and experiment is not None and experiment.method.is_value
                            and experiment.method.value is MethodToken.KNUDSEN_EFFUSION
                        ):
                            prediction = _knudsen_residue_prediction(
                                context, point, engine, residue_prediction_cache,
                            )
                        intrinsic_redox_activity = _has_intrinsic_redox_activity(
                            engine, point
                        )
                        residual, candidate = _compile_residual_with_lineage(
                            point,
                            engine,
                            context=live_context,
                            prediction=prediction,
                            comparison_ids=comparison_ids,
                            predict=scoring_predict,
                            handles=resolved_handles,
                            lineage_observation_id=lineage_id,
                            table_index=table_index,
                            derived_band=derived_band,
                            derived_band_reason=derived_band_reason,
                            ratchet_missing=ratchet_missing,
                            point_observations=point_observations_by_experiment.get(
                                point.experiment_id, ()
                            ),
                            condensed_frame_diagnostics=(
                                prepared.condensed_frame_diagnostics
                            ),
                            fo2_mode=(
                                Fo2Mode.SHARED_LADDER
                                if intrinsic_redox_activity
                                else None
                            ),
                            _source_relation_resolver=lineage_resolver,
                            _validation_indexes=validation_indexes,
                        )
                        if _shard_failure_reason is not None:
                            residual = _score_failed_shard_residual(
                                residual,
                                engine,
                                _shard_failure_reason,
                                point,
                                candidate,
                            )
                            candidate = None
                        base_sort_order = (
                            obs.observation_id,
                            point_index,
                            engine_order[engine],
                            0,
                        )
                        if _stream is None:
                            assert residuals is not None
                            residuals.append(residual)
                        else:
                            if base_sort_order not in _skip_sort_orders:
                                _stream.append(
                                    residual,
                                    candidate,
                                    sort_order=base_sort_order,
                                )
                        if (
                            residual.fo2_mode is Fo2Mode.SHARED_LADDER
                            and intrinsic_redox_activity
                            and (
                                prediction is None
                                and predict is None
                                and (
                                    _stream is None
                                    or base_sort_order not in _skip_sort_orders
                                )
                            )
                        ):
                            total += 1
                            intrinsic_residual, intrinsic_candidate = _compile_residual_with_lineage(
                                point,
                                engine,
                                context=live_context,
                                comparison_ids=comparison_ids,
                                handles=resolved_handles,
                                lineage_observation_id=(
                                    obs.observation_id
                                    if point.observation_id != obs.observation_id
                                    else None
                                ),
                                table_index=table_index,
                                derived_band=derived_band,
                                derived_band_reason=derived_band_reason,
                                ratchet_missing=ratchet_missing,
                                point_observations=point_observations_by_experiment.get(
                                    point.experiment_id, ()
                                ),
                                predict=scoring_predict,
                                condensed_frame_diagnostics=(
                                    prepared.condensed_frame_diagnostics
                                ),
                                fo2_mode=Fo2Mode.ENGINE_INTRINSIC,
                                _validation_indexes=validation_indexes,
                            )
                            if _shard_failure_reason is not None:
                                intrinsic_residual = _score_failed_shard_residual(
                                    intrinsic_residual,
                                    engine,
                                    _shard_failure_reason,
                                    point,
                                    intrinsic_candidate,
                                )
                                intrinsic_candidate = None
                            if _stream is None:
                                assert residuals is not None
                                residuals.append(intrinsic_residual)
                            else:
                                intrinsic_sort_order = (
                                    obs.observation_id,
                                    point_index,
                                    engine_order[engine],
                                    1,
                                )
                                if intrinsic_sort_order not in _skip_sort_orders:
                                    _stream.append(
                                        intrinsic_residual,
                                        intrinsic_candidate,
                                        sort_order=intrinsic_sort_order,
                                    )
                            if intrinsic_candidate is not None and _stream is None:
                                candidates[intrinsic_candidate.observation_id] = (
                                    intrinsic_candidate
                                )
                        if candidate is not None and _stream is None:
                            candidates[candidate.observation_id] = candidate
                        done += 1
                        report_progress_if_due()
    finally:
        from simulator.diagnostic_helpers.binary_pot_battery import (
            close_battery_engine_handles,
        )

        close_battery_engine_handles(
            resolved_handles,
            exclude=provided_handles,
            active_error=sys.exc_info()[1],
        )

    if _stream is not None:
        _stream.finalize(
            context=context,
            engines=engine_set,
        )
        return (), {}, []

    assert residuals is not None
    residuals.sort(
        key=lambda r: (
            "" if r.rail is None else r.rail.value,
            r.reference,
            r.key,
        )
    )
    scored = _SelectedResiduals(residuals, (obs.observation_id for obs in refs))
    return scored, candidates, _compilation_decision_strata(scored, context)


_SCORE_WORKER_SETUP: (
    tuple[
        _PreparedScoreRun,
        Callable[..., EnginePrediction] | None,
        Mapping[str, object],
    ]
) | None = None
# Forked workers inherit this. It is set only while their pool is alive.
_INHERITED_SCORE_RUN: _PreparedScoreRun | None = None
_SCORE_WORKER_REFS: Mapping[str, Observation] | None = None


@dataclass(frozen=True)
class _ScoreShardResult:
    partial_path: str | None = None
    row_count: int = 0
    counters: Mapping[str, Mapping[str, object]] | None = None
    runtime_identities: Mapping[str, str] | None = None
    failure: str | None = None
    refused_rows: int = 0


def _sum_engine_cache_counters(
    total: dict[str, dict[str, object]] | None,
    counts: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    if total is None:
        total = {}
    for engine, engine_counts in counts.items():
        accumulated = total.setdefault(engine, {})
        for name, value in engine_counts.items():
            if isinstance(value, int):
                accumulated[name] = int(accumulated.get(name, 0)) + value
    return total


def _score_failed_shard_prediction(
    engine: Engine,
    observation: Observation,
    *,
    failure_reason: str,
    **_kwargs: object,
) -> EnginePrediction:
    from simulator.battery.score import (
        ENGINE_CHANNELS,
        ENGINE_COEFFICIENT_SOURCES,
        EnginePrediction,
        Execution,
        ExecutionState,
        Identity,
        RefusalReason,
    )
    return EnginePrediction(
        engine=engine,
        channel=ENGINE_CHANNELS[engine],
        execution=Execution(
            state=ExecutionState.ATTEMPTED_UNAVAILABLE,
            call_evidence=f"score_shard:{engine.value}",
        ),
        coefficient_sources=ENGINE_COEFFICIENT_SOURCES[engine],
        refusal_reason=RefusalReason.ATTEMPTED_UNAVAILABLE,
        refusal_detail={
            "reason": "score_shard_failed",
            "detail": failure_reason,
        },
        identity=(
            observation.identity
            if isinstance(observation.identity, Identity)
            else None
        ),
    )


def _score_failed_shard_residual(
    residual: Residual,
    engine: Engine,
    failure_reason: str,
    observation: Observation,
    candidate: Observation | None,
) -> Residual:
    from simulator.battery.score import (
        CandidateRequest,
        ENGINE_CHANNELS,
        Execution,
        ExecutionState,
        Identity,
        RefusalReason,
        ResidualRefusal,
        ResidualStatus,
        quantity_token,
    )
    detail: dict[str, object] = {
        "reason": "score_shard_failed",
        "engine": engine.value,
        "detail": failure_reason,
    }
    check_refs: tuple[str, ...] = ()
    if residual.refusal is not None:
        detail["prior_refusal"] = {
            "reason": residual.refusal.reason.value,
            "detail": dict(residual.refusal.detail),
        }
        check_refs = residual.refusal.check_refs
    request = residual.candidate_request
    if request is None and candidate is not None:
        candidate_quantity = quantity_token(candidate.identity)
        if candidate_quantity is not None:
            request = CandidateRequest(
                candidate.experiment_id,
                candidate_quantity,
                engine,
                ENGINE_CHANNELS[engine],
            )
    if request is None and isinstance(observation.identity, Identity):
        observed_quantity = quantity_token(observation.identity)
        if observed_quantity is not None:
            request = CandidateRequest(
                observation.experiment_id,
                observed_quantity,
                engine,
                ENGINE_CHANNELS[engine],
            )
    return replace(
        residual,
        execution=Execution(
            state=ExecutionState.ATTEMPTED_UNAVAILABLE,
            call_evidence=f"score_shard:{engine.value}",
        ),
        status=ResidualStatus.REFUSED,
        score_eligible=False,
        candidate=None,
        candidate_request=request,
        numeric=None,
        refusal=ResidualRefusal(
            RefusalReason.ATTEMPTED_UNAVAILABLE,
            detail,
            check_refs=check_refs,
        ),
    )


def _mach_thread_count() -> int | None:
    """OS threads, the count macOS ``os.fork`` uses for its warning.

    ``mach_task_self_`` is a variable, not a function. Returns None when the
    count is unavailable; the caller then uses interpreter frames. A failed
    probe must not abort scoring.
    """

    if sys.platform != "darwin":
        return None
    import ctypes

    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        task = ctypes.c_uint32.in_dll(libc, "mach_task_self_").value
        thread_t = ctypes.c_uint32
        libc.task_threads.restype = ctypes.c_int32
        libc.task_threads.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.POINTER(thread_t)),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        threads = ctypes.POINTER(thread_t)()
        count = ctypes.c_uint32(0)
        if libc.task_threads(task, ctypes.byref(threads), ctypes.byref(count)) != 0:
            return None
        total = int(count.value)
        if total < 1:
            return None
        libc.mach_port_deallocate.restype = ctypes.c_int32
        libc.mach_port_deallocate.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        for index in range(total):
            libc.mach_port_deallocate(task, threads[index])
        libc.vm_deallocate.restype = ctypes.c_int32
        libc.vm_deallocate.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint64,
            ctypes.c_uint64,
        ]
        address = ctypes.cast(threads, ctypes.c_void_p).value or 0
        libc.vm_deallocate(task, address, total * ctypes.sizeof(thread_t))
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return total


def _fork_warning_thread_count() -> int:
    """Threads that make ``os.fork`` emit its multi-thread DeprecationWarning.

    On macOS the warning counts mach threads. ``threading.enumerate()`` misses
    a thread started with ``_thread.start_new_thread``; ``sys._current_frames``
    sees that interpreter thread. The warning fires when the count exceeds one.
    """

    frames = len(sys._current_frames())
    mach = _mach_thread_count()
    if mach is None:
        return frames
    return max(frames, mach)


def _score_fork_blockers(*, residual_sqlite_open: bool = False) -> tuple[str, ...]:
    """Why this process must not fork a score worker.

    A fresh ``--workers N`` process can inherit the prepared run. Fork only
    when that inheritance is safe. Otherwise the child can deadlock: another
    thread, the Objective-C runtime loaded by libphaseobjc, or an open sqlite
    connection would be copied into the child.
    """

    reasons: list[str] = []
    names = [thread.name for thread in threading.enumerate()]
    thread_count = _fork_warning_thread_count()
    if thread_count > 1 or names != ["MainThread"]:
        detail = []
        if names != ["MainThread"]:
            detail.append(",".join(names))
        detail.append(f"count:{thread_count}")
        reasons.append("threads:" + ";".join(detail))
    from simulator.engine_local_config import thermoengine_phaseobjc_runtime_loaded

    if thermoengine_phaseobjc_runtime_loaded():
        reasons.append("native_runtime:thermoengine_phaseobjc")
    dangerous = sorted(
        name
        for name in sys.modules
        if name in {"objc", "Foundation"}
        or name.startswith(("objc.", "Foundation.", "rubicon"))
        or name == "thermoengine"
        or name.startswith("thermoengine.")
    )
    if dangerous:
        reasons.append("modules:" + ",".join(dangerous))
    from simulator.engine_cache import _CONNECTIONS

    if _CONNECTIONS:
        reasons.append(f"cache_connections:{len(_CONNECTIONS)}")
    if residual_sqlite_open:
        reasons.append("residual sqlite is open")
    return tuple(reasons)


def _resolve_score_ref_ids(
    ref_ids: Sequence[str],
    refs: Mapping[str, Observation],
) -> tuple[Observation, ...]:
    missing = [ref_id for ref_id in ref_ids if ref_id not in refs]
    if missing:
        raise ValueError(
            "score shard reference is not in the prepared run: "
            + ", ".join(missing)
        )
    return tuple(refs[ref_id] for ref_id in ref_ids)


def _score_shard_worker_error_result(
    _task: tuple[int, Engine, tuple[str, ...], str, str | None],
    error: BaseException,
) -> _ScoreShardResult:
    return _ScoreShardResult(
        failure=f"{type(error).__name__}: {error}",
    )


def _initialize_score_worker(
    predict: Callable[..., EnginePrediction] | None,
    engine_cache_path_scope_state: tuple[bool, str | None] = (False, None),
    prepared_path: str | None = None,
    store_stamp: Mapping[str, object] | None = None,
) -> None:
    global _SCORE_WORKER_SETUP, _SCORE_WORKER_REFS
    prepared = _INHERITED_SCORE_RUN
    if prepared is None:
        if not prepared_path:
            raise RuntimeError("battery score worker inherited no prepared run")
        with open(prepared_path, "rb") as handle:
            prepared = pickle.load(handle)
    if store_stamp is None:
        raise ValueError("score worker requires the parent store stamp")
    refs: dict[str, Observation] = {}
    for observation in prepared.refs:
        if observation.observation_id in refs:
            raise ValueError(
                "prepared score run repeats observation id "
                f"{observation.observation_id}"
            )
        refs[observation.observation_id] = observation
    _SCORE_WORKER_REFS = refs
    _SCORE_WORKER_SETUP = (prepared, predict, store_stamp)
    from simulator.engine_cache import _set_engine_cache_path_scope_state

    _set_engine_cache_path_scope_state(engine_cache_path_scope_state)


def _score_group_shard(
    task: tuple[int, Engine, tuple[str, ...], str, str | None]
) -> _ScoreShardResult:
    from simulator.battery.score import (
        _ResidualJsonlStream,
        _score_store_with_decisions,
    )
    prepared, predict, store_stamp = _SCORE_WORKER_SETUP or (None, None, None)
    if prepared is None:
        raise RuntimeError("battery score worker was not initialized")
    if store_stamp is None:
        raise RuntimeError("battery score worker has no parent store stamp")
    _shard_index, engine, ref_ids, path_text, root_text = task
    if _SCORE_WORKER_REFS is None:
        raise RuntimeError("battery score worker has no reference table")
    refs = _resolve_score_ref_ids(ref_ids, _SCORE_WORKER_REFS)
    stream = _ResidualJsonlStream(
        Path(path_text),
        root=None if root_text is None else Path(root_text),
        store_stamp=store_stamp,
        sort_on_finalize=False,
    )
    try:
        from simulator.engine_cache import (
            engine_cache_counters,
            engine_cache_runtime_identities,
        )

        engine_cache_counters(reset=True)
        _score_store_with_decisions(
            prepared.context,
            predict=predict,
            _prepared=prepared,
            _engine_set=(engine,),
            _refs=refs,
            _stream=stream,
        )
        counters = engine_cache_counters(reset=True)
        runtime_identities = engine_cache_runtime_identities()
        return _ScoreShardResult(
            partial_path=str(stream.partial_path),
            row_count=stream.count,
            counters=counters,
            runtime_identities=runtime_identities,
        )
    finally:
        stream.close()
        from simulator.diagnostic_helpers.binary_pot_battery import (
            _close_isolated_cell_workers,
        )

        _close_isolated_cell_workers()


def _materialize_failed_score_shard(
    task: tuple[int, Engine, tuple[str, ...], str, str | None],
    result: _ScoreShardResult,
    prepared: _PreparedScoreRun,
    *,
    expected_header: bytes,
    store_stamp: Mapping[str, object],
    predict: Callable[..., EnginePrediction] | None,
) -> _ScoreShardResult:
    from simulator.battery.score import (
        _ResidualJsonlStream,
        _score_store_with_decisions,
        is_store_stamp_payload,
    )
    if result.failure is None:
        return result
    path = Path(task[3])
    partial_path = path.with_name(path.name + ".partial")
    order_path = partial_path.with_name(partial_path.name + ".order")
    recovered_path = path.with_name(path.name + ".recovered")
    recovered_stream = _ResidualJsonlStream(
        recovered_path,
        root=None if task[4] is None else Path(task[4]),
        store_stamp=store_stamp,
        sort_on_finalize=False,
    )
    completed_orders: set[tuple[str, int, int, int]] = set()
    try:
        if partial_path.is_file() and order_path.is_file():
            with (
                partial_path.open("rb") as shard,
                order_path.open("rb") as order_file,
            ):
                if shard.readline() == expected_header:
                    order_lines = iter(order_file)
                    for line in shard:
                        if not line.endswith(b"\n"):
                            break
                        try:
                            order_line = next(order_lines)
                        except StopIteration:
                            break
                        if not order_line.endswith(b"\n"):
                            break
                        try:
                            payload = json.loads(line)
                            order_value = json.loads(order_line)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if (
                            not isinstance(payload, Mapping)
                            or is_store_stamp_payload(payload)
                            or not isinstance(order_value, list)
                            or len(order_value) != 4
                            or not isinstance(order_value[0], str)
                            or any(
                                type(value) is not int for value in order_value[1:]
                            )
                        ):
                            continue
                        stable_order = (
                            order_value[0],
                            order_value[1],
                            order_value[2],
                            order_value[3],
                        )
                        if stable_order in completed_orders:
                            continue
                        recovered_stream.append_payload_line(
                            line,
                            sort_order=stable_order,
                        )
                        completed_orders.add(stable_order)
        _score_store_with_decisions(
            prepared.context,
            _prepared=prepared,
            _engine_set=(task[1],),
            _refs=_resolve_score_ref_ids(
                task[2],
                {observation.observation_id: observation for observation in prepared.refs},
            ),
            _stream=recovered_stream,
            predict=predict,
            _shard_failure_reason=result.failure,
            _skip_sort_orders=frozenset(completed_orders),
        )
        return replace(
            result,
            partial_path=str(recovered_stream.partial_path),
            row_count=recovered_stream.count,
            refused_rows=recovered_stream.count - len(completed_orders),
        )
    finally:
        recovered_stream.close()


def _score_store_to_jsonl(
    context: ScoreContext,
    path: Path,
    *,
    root: Path | None = None,
    engines: Sequence[Engine] | None = None,
    rail: Rail | None = None,
    species_formulas: Sequence[str] = (),
    work_id: str | None = None,
    limit: int | None = None,
    include_diagnostics: bool = True,
    measured_only: bool = False,
    _test_experiment_ids: Sequence[str] | None = None,
    predict: Callable[..., EnginePrediction] | None = None,
    handles: Mapping[str, object] | None = None,
    workers: int = 1,
    compact_static_refusals: bool = False,
) -> tuple[int, _ResidualReportMetadata]:
    """Score directly to a sorted ``.partial`` JSONL without retaining rows."""
    from simulator.battery.score import (
        _ResidualJsonlStream,
        _ResidualReportMetadata,
        _partition_score_refs,
        _prepare_score_run,
        _score_store_with_decisions,
        is_store_stamp_payload,
        supervised_process_map,
    )

    if workers < 1:
        raise ValueError("workers must be at least 1")
    if workers > 1 and handles:
        raise ValueError("spawned score workers open their own engine handles")
    stream = _ResidualJsonlStream(
        path,
        root=root,
        compact_static_refusals=compact_static_refusals,
    )
    from simulator.engine_cache import (
        _engine_cache_path_scope_state,
        engine_cache_counters,
        engine_cache_runtime_identities,
    )

    cache_path_scope_state = _engine_cache_path_scope_state()
    cache_counters: dict[str, dict[str, object]] | None = None
    cache_runtime_identities: dict[str, str] | None = None
    shard_failures: list[Mapping[str, object]] = []
    try:
        if workers == 1:
            engine_cache_counters(reset=True)
            _score_store_with_decisions(
                context,
                engines=engines,
                rail=rail,
                species_formulas=species_formulas,
                work_id=work_id,
                limit=limit,
                include_diagnostics=include_diagnostics,
                measured_only=measured_only,
                _test_experiment_ids=_test_experiment_ids,
                predict=predict,
                handles=handles,
                _stream=stream,
            )
            cache_counters = engine_cache_counters(reset=True)
            cache_runtime_identities = engine_cache_runtime_identities()
        else:
            prepared = _prepare_score_run(
                context,
                engines=engines,
                rail=rail,
                species_formulas=species_formulas,
                work_id=work_id,
                limit=limit,
                include_diagnostics=include_diagnostics,
                measured_only=measured_only,
                _test_experiment_ids=_test_experiment_ids,
            )
            _report_selected_species_count(prepared, species_formulas, stream)
            chunks = _partition_score_refs(prepared.refs, workers=workers)
            populated_chunks = [
                (index, chunk)
                for index, chunk in enumerate(chunks)
                if chunk
            ]
            if not populated_chunks or not prepared.engine_set:
                engine_cache_counters(reset=True)
                _score_store_with_decisions(
                    context,
                    _prepared=prepared,
                    _refs=(),
                    _stream=stream,
                )
                cache_counters = engine_cache_counters(reset=True)
                cache_runtime_identities = engine_cache_runtime_identities()
            else:
                root_text = None if root is None else str(root)
                with tempfile.TemporaryDirectory(
                    prefix="battery-score-shards-"
                ) as temporary_dir:
                    tasks = []
                    for index, chunk in populated_chunks:
                        for engine in prepared.engine_set:
                            shard_path = Path(temporary_dir) / (
                                f"shard-{index:04d}-{engine.value}.jsonl"
                            )
                            tasks.append(
                                (
                                    index,
                                    engine,
                                    tuple(
                                        observation.observation_id
                                        for observation in chunk
                                    ),
                                    str(shard_path),
                                    root_text,
                                )
                            )
                    engine_cache_counters(reset=True)
                    fork_blockers = _score_fork_blockers(
                        residual_sqlite_open=stream._database is not None
                    )
                    global _INHERITED_SCORE_RUN
                    if fork_blockers:
                        prepared_path = str(
                            Path(temporary_dir) / "prepared-score-run.pkl"
                        )
                        with open(prepared_path, "wb") as handle:
                            pickle.dump(
                                prepared,
                                handle,
                                protocol=pickle.HIGHEST_PROTOCOL,
                            )
                        # Spawn children load this one file. Fork is unsafe
                        # once another thread or the Objective-C runtime exists.
                        _INHERITED_SCORE_RUN = None
                        worker_context = mp.get_context("spawn")
                    else:
                        prepared_path = None
                        # Fork inherits the prepared run. Spawn would pickle
                        # it once per child, which is the supervisor's cost.
                        _INHERITED_SCORE_RUN = prepared
                        worker_context = mp.get_context("fork")
                    try:
                        shard_results = supervised_process_map(
                            tasks,
                            _score_group_shard,
                            max_workers=min(workers, len(tasks)),
                            initializer=_initialize_score_worker,
                            initargs=(
                                predict,
                                cache_path_scope_state,
                                prepared_path,
                                # The parent-derived stamp is this run's identity.
                                # Shards must carry it because a concurrent Git write
                                # can change the abbreviation length of a fresh %h.
                                stream.store_stamp,
                            ),
                            mp_context=worker_context,
                            timeout_seconds=None,
                            handle_worker_error=_score_shard_worker_error_result,
                        )
                    finally:
                        _INHERITED_SCORE_RUN = None
                    with stream.partial_path.open("rb") as output:
                        header = output.readline()
                    shard_count = 0
                    cache_counters = {}
                    cache_runtime_identities = {}
                    for task, shard_result in zip(tasks, shard_results):
                        if shard_result.failure is not None:
                            shard_result = _materialize_failed_score_shard(
                                task,
                                shard_result,
                                prepared,
                                expected_header=header,
                                store_stamp=stream.store_stamp,
                                predict=predict,
                            )
                            shard_failures.append(
                                {
                                    "chunk": task[0],
                                    "engine": task[1].value,
                                    "reason": shard_result.failure,
                                    "refused_rows": shard_result.refused_rows,
                                }
                            )
                        if shard_result.partial_path is None:
                            raise RuntimeError("score shard returned no residual file")
                        shard_count += shard_result.row_count
                        if shard_result.counters is not None:
                            cache_counters = _sum_engine_cache_counters(
                                cache_counters, shard_result.counters
                            )
                        if shard_result.runtime_identities is not None:
                            cache_runtime_identities.update(
                                shard_result.runtime_identities
                            )
                        order_path = Path(f"{shard_result.partial_path}.order")
                        with (
                            Path(shard_result.partial_path).open("rb") as shard,
                            order_path.open("r", encoding="utf-8") as order_file,
                        ):
                            if shard.readline() != header:
                                raise ValueError("score shard store stamp mismatch")
                            for line in shard:
                                if not line.strip():
                                    continue
                                payload = json.loads(line)
                                if (
                                    not isinstance(payload, dict)
                                    or is_store_stamp_payload(payload)
                                ):
                                    continue
                                order_value = json.loads(order_file.readline())
                                if (
                                    not isinstance(order_value, list)
                                    or len(order_value) != 4
                                    or not isinstance(order_value[0], str)
                                    or any(
                                        type(value) is not int
                                        for value in order_value[1:]
                                    )
                                ):
                                    raise ValueError(
                                        "score shard has an invalid serial-order entry"
                                    )
                                stream.append_payload_line(
                                    line,
                                    sort_order=(
                                        order_value[0],
                                        order_value[1],
                                        order_value[2],
                                        order_value[3],
                                    ),
                                )
                            if order_file.read(1):
                                raise ValueError(
                                    "score shard has extra serial-order entries"
                                )
                    if stream.count != shard_count:
                        raise ValueError(
                            "score shard row count does not match merged output"
                        )
                stream.finalize(context=context, engines=prepared.engine_set)
        if shard_failures:
            print(
                "score run notice: failed score shards were written as "
                "ATTEMPTED_UNAVAILABLE rows: "
                + "; ".join(
                    "chunk={chunk} engine={engine} refused_rows={refused_rows}: "
                    "{reason}".format(**failure)
                    for failure in shard_failures
                ),
                file=sys.stderr,
                flush=True,
            )
        return stream.count, _ResidualReportMetadata(
            family_band_values=dict(stream.band_values),
            headline_band_values=dict(stream.headline_band_values),
            tier_band_values=dict(stream.tier_band_values),
            aggregate=stream.report_aggregate,
            engine_cache_counters=cache_counters,
            engine_cache_runtime_identities=cache_runtime_identities,
            shard_failures=tuple(shard_failures),
            static_refusals=stream.static_refusals,
            selected_reference_count=len(stream.selected_reference_ids),
            selected_reference_ids=frozenset(stream.selected_reference_ids),
        )
    finally:
        stream.close()
