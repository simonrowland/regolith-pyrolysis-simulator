from __future__ import annotations

from contextvars import ContextVar
from decimal import Decimal
import cProfile
import hashlib
from itertools import count
import io
import json
import os
import pstats
import tempfile
import time
import warnings
from pathlib import Path


def profile_ia_residue_source(
    label,
    source_id,
    monkeypatch,
    *,
    observation_ids=None,
    cprofile_target=False,
    memoize=True,
):
    import scipy.integrate

    import simulator.battery.oxygen_balance as oxygen_balance
    import simulator.battery.residue as residue
    import simulator.battery.score as score
    import simulator.diagnostic_helpers.binary_pot_battery as analytical
    import simulator.melt_backend.openimcc_bridge as openimcc
    from simulator.battery.enums import AdmissionStatus, Engine, Quantity
    from simulator.battery.identity import Identity, quantity_token

    context = score.load_score_context(sources=(source_id,))
    selected_ids = None if observation_ids is None else frozenset(observation_ids)
    rows = tuple(
        row for row in context.observations.values()
        if row.source_id == source_id
        and row.admission.status is AdmissionStatus.ADMITTED
        and isinstance(row.identity, Identity)
        and quantity_token(row.identity) in {
            Quantity.RESIDUE_COMPONENT_COMPOSITION,
            Quantity.ELEMENT_RETENTION_RATIO,
        }
        and (selected_ids is None or row.observation_id in selected_ids)
    )
    assert rows, f"{label}: requested residue observation rows were not admitted"
    expected_ids = {row.observation_id for row in rows}
    original_candidates = score.comparison_candidates
    monkeypatch.setattr(
        score,
        "comparison_candidates",
        lambda loaded_context: tuple(
            row for row in original_candidates(loaded_context)
            if isinstance(row.identity, Identity)
            and quantity_token(row.identity) in {
                Quantity.RESIDUE_COMPONENT_COMPOSITION,
                Quantity.ELEMENT_RETENTION_RATIO,
            }
            and (selected_ids is None or row.observation_id in selected_ids)
        ),
    )

    active_row = ContextVar(f"ia_profile_row_{label}", default=None)
    active_rhs = ContextVar(f"ia_profile_rhs_{label}", default=None)
    active_record = ContextVar(f"ia_profile_record_{label}", default=None)
    integration_records = []

    def scope_row(original):
        def wrapped(context, reference, engine, *args, **kwargs):
            token = active_row.set(reference.observation_id)
            try:
                return original(context, reference, engine, *args, **kwargs)
            finally:
                active_row.reset(token)
        return wrapped

    monkeypatch.setattr(
        score, "_extent_residue_prediction",
        scope_row(score._extent_residue_prediction),
    )
    monkeypatch.setattr(
        score, "_hashimoto_residue_prediction",
        scope_row(score._hashimoto_residue_prediction),
    )

    original_integrate = residue.integrate_knudsen_residue_inventory
    if not memoize:
        original_key = residue._knudsen_pressure_state_key
        unique_state = count()
        monkeypatch.setattr(
            residue,
            "_knudsen_pressure_state_key",
            lambda inventory, logp, temperature: (
                original_key(inventory, logp, temperature), next(unique_state)
            ),
        )
    original_solve = scipy.integrate.solve_ivp
    original_brentq = oxygen_balance.brentq
    original_analytical = analytical.internal_analytical_residue_vapor_pressure
    # The bridge may run this function in a timeout thread; process_time includes
    # its CPU and cache hits correctly contribute zero OpenIMCC solve time.
    original_openimcc_uncached = openimcc._evaluate_uncached
    original_openimcc_gas = openimcc.evaluate_gas

    def add_active_rhs_metric(name, elapsed=0.0):
        record = active_record.get()
        if record is None or not cprofile_target:
            return
        rhs_index = active_rhs.get()
        if rhs_index is None:
            return
        metrics = record["per_rhs"].setdefault(
            str(rhs_index),
            {"root_calls": 0, "root_evaluations": 0,
             "pressure_model_calls": 0, "rhs_cpu_seconds": 0.0,
             "ia_vapor_pressure_calls": 0, "openimcc_calls": 0,
             "ia_cpu_seconds_inclusive": 0.0, "openimcc_cpu_seconds": 0.0,
             "openimcc_gas_calls": 0},
        )
        if name in {
            "root_calls", "root_evaluations", "pressure_model_calls",
            "ia_vapor_pressure_calls", "openimcc_calls", "openimcc_gas_calls",
        }:
            metrics[name] += 1
        else:
            metrics[name] += elapsed

    def analytical_probe(*args, **analytical_kwargs):
        record = active_record.get()
        if record is None:
            return original_analytical(*args, **analytical_kwargs)
        record["ia_vapor_pressure_calls"] += 1
        add_active_rhs_metric("ia_vapor_pressure_calls", 0.0)
        started = time.process_time()
        try:
            return original_analytical(*args, **analytical_kwargs)
        finally:
            elapsed = time.process_time() - started
            record["ia_vapor_pressure_cpu_seconds_inclusive"] += elapsed
            add_active_rhs_metric("ia_cpu_seconds_inclusive", elapsed)

    def openimcc_probe(*args, **open_kwargs):
        record = active_record.get()
        if record is None:
            return original_openimcc_uncached(*args, **open_kwargs)
        record["openimcc_calls"] += 1
        add_active_rhs_metric("openimcc_calls", 0.0)
        started = time.process_time()
        try:
            return original_openimcc_uncached(*args, **open_kwargs)
        finally:
            elapsed = time.process_time() - started
            record["openimcc_cpu_seconds"] += elapsed
            add_active_rhs_metric("openimcc_cpu_seconds", elapsed)

    def openimcc_gas_probe(*args, **open_kwargs):
        record = active_record.get()
        if record is None:
            return original_openimcc_gas(*args, **open_kwargs)
        record["openimcc_calls"] += 1
        record["openimcc_gas_calls"] += 1
        add_active_rhs_metric("openimcc_calls", 0.0)
        add_active_rhs_metric("openimcc_gas_calls", 0.0)
        started = time.process_time()
        try:
            return original_openimcc_gas(*args, **open_kwargs)
        finally:
            elapsed = time.process_time() - started
            record["openimcc_cpu_seconds"] += elapsed
            add_active_rhs_metric("openimcc_cpu_seconds", elapsed)

    monkeypatch.setattr(
        analytical, "internal_analytical_residue_vapor_pressure", analytical_probe
    )
    monkeypatch.setattr(openimcc, "_evaluate_uncached", openimcc_probe)
    monkeypatch.setattr(
        openimcc, "evaluate_gas", openimcc_gas_probe,
    )

    def profiled_integrate(initial, channels, pressure_model, **kwargs):
        wall_started = time.perf_counter()
        try:
            loadavg_started = os.getloadavg()
        except (AttributeError, OSError):
            loadavg_started = None
        record = {
            "observation_id": active_row.get(),
            "method": None,
            "nfev": 0,
            "njev": 0,
            "rhs_calls": 0,
            "rhs_cpu_seconds": 0.0,
            "pressure_model_calls": 0,
            "root_calls": 0,
            "root_evaluations": 0,
            "ia_vapor_pressure_calls": 0,
            "ia_vapor_pressure_cpu_seconds_inclusive": 0.0,
            "openimcc_calls": 0,
            "openimcc_gas_calls": 0,
            "openimcc_cpu_seconds": 0.0,
            "per_rhs": {},
        }
        profiler = (
            cProfile.Profile(timer=time.process_time, timeunit=1.0)
            if cprofile_target
            else None
        )
        record_token = active_record.set(record)

        def add_rhs_metric(name, elapsed):
            if not cprofile_target:
                return
            rhs_index = active_rhs.get()
            if rhs_index is None:
                return
            metrics = record["per_rhs"].setdefault(
                str(rhs_index),
                {"root_calls": 0, "root_evaluations": 0,
                 "pressure_model_calls": 0, "rhs_cpu_seconds": 0.0,
                 "ia_vapor_pressure_calls": 0, "openimcc_calls": 0,
                 "ia_cpu_seconds_inclusive": 0.0, "openimcc_cpu_seconds": 0.0,
                 "openimcc_gas_calls": 0},
            )
            if name in {
                "root_calls", "root_evaluations", "pressure_model_calls",
                "ia_vapor_pressure_calls", "openimcc_calls",
                "openimcc_gas_calls",
            }:
                metrics[name] += 1
            else:
                metrics[name] += elapsed

        def tracked_pressure(inventory, logp, temperature):
            record["pressure_model_calls"] += 1
            add_rhs_metric("pressure_model_calls", 0.0)
            started = time.process_time()
            try:
                return pressure_model(inventory, logp, temperature)
            finally:
                record.setdefault("pressure_model_cpu_seconds", 0.0)
                record["pressure_model_cpu_seconds"] += time.process_time() - started

        def solve_probe(function, t_span, y0, *args, **solve_kwargs):
            record["method"] = solve_kwargs.get("method", "RK45")

            def counted_rhs(*rhs_args, **rhs_kwargs):
                rhs_index = record["rhs_calls"]
                record["rhs_calls"] += 1
                token = active_rhs.set(rhs_index)
                started = time.process_time()
                try:
                    return function(*rhs_args, **rhs_kwargs)
                finally:
                    elapsed = time.process_time() - started
                    record["rhs_cpu_seconds"] += elapsed
                    add_rhs_metric("rhs_cpu_seconds", elapsed)
                    active_rhs.reset(token)

            solution = original_solve(
                counted_rhs, t_span, y0, *args, **solve_kwargs
            )
            record["nfev"] += int(getattr(solution, "nfev", 0) or 0)
            record["njev"] += int(getattr(solution, "njev", 0) or 0)
            return solution

        def root_probe(function, *args, **root_kwargs):
            record["root_calls"] += 1
            add_rhs_metric("root_calls", 0.0)

            def counted_root(value):
                record["root_evaluations"] += 1
                add_rhs_metric("root_evaluations", 0.0)
                return function(value)

            return original_brentq(counted_root, *args, **root_kwargs)

        scipy.integrate.solve_ivp = solve_probe
        oxygen_balance.brentq = root_probe
        started = time.process_time()
        if profiler is not None:
            profiler.enable()
        try:
            return original_integrate(
                initial, channels, tracked_pressure, **kwargs
            )
        except Exception as exc:
            record["refusal_reason"] = getattr(exc, "reason", None)
            raise
        finally:
            if profiler is not None:
                profiler.disable()
            scipy.integrate.solve_ivp = original_solve
            oxygen_balance.brentq = original_brentq
            record["process_cpu_seconds"] = time.process_time() - started
            record["wall_seconds"] = time.perf_counter() - wall_started
            record["wall_to_process_cpu_ratio"] = (
                record["wall_seconds"] / record["process_cpu_seconds"]
                if record["process_cpu_seconds"] > 0.0
                else None
            )
            try:
                record["loadavg_1_5_15_start"] = loadavg_started
                record["loadavg_1_5_15_end"] = os.getloadavg()
            except (AttributeError, OSError):
                record["loadavg_1_5_15_start"] = None
                record["loadavg_1_5_15_end"] = None
            record["per_rhs"] = [
                {"rhs_index": int(index), **metrics}
                for index, metrics in sorted(
                    record["per_rhs"].items(), key=lambda item: int(item[0])
                )
            ]
            for rhs in record["per_rhs"]:
                ia = rhs["ia_cpu_seconds_inclusive"]
                openimcc_cpu = rhs["openimcc_cpu_seconds"]
                rhs["ia_vapor_pressure_cpu_seconds_exclusive"] = max(
                    0.0, ia - openimcc_cpu
                )
                rhs["everything_else_cpu_seconds"] = max(
                    0.0,
                    rhs["rhs_cpu_seconds"] - ia,
                )
            if cprofile_target:
                per_rhs = record["per_rhs"]
                rhs_count = max(len(per_rhs), 1)
                record["per_rhs_summary"] = {
                    "rhs_count": len(per_rhs),
                    "root_calls_total": sum(row["root_calls"] for row in per_rhs),
                    "root_evaluations_total": sum(
                        row["root_evaluations"] for row in per_rhs
                    ),
                    "pressure_model_calls_total": sum(
                        row["pressure_model_calls"] for row in per_rhs
                    ),
                    "mean_root_calls_per_rhs": sum(
                        row["root_calls"] for row in per_rhs
                    ) / rhs_count,
                    "mean_root_evaluations_per_rhs": sum(
                        row["root_evaluations"] for row in per_rhs
                    ) / rhs_count,
                    "mean_pressure_model_calls_per_rhs": sum(
                        row["pressure_model_calls"] for row in per_rhs
                    ) / rhs_count,
                    "mean_ia_vapor_pressure_calls_per_rhs": sum(
                        row["ia_vapor_pressure_calls"] for row in per_rhs
                    ) / rhs_count,
                    "mean_openimcc_calls_per_rhs": sum(
                        row["openimcc_calls"] for row in per_rhs
                    ) / rhs_count,
                    "mean_rhs_cpu_seconds": sum(
                        row["rhs_cpu_seconds"] for row in per_rhs
                    ) / rhs_count,
                    "mean_ia_vapor_pressure_cpu_seconds_exclusive": sum(
                        row["ia_vapor_pressure_cpu_seconds_exclusive"]
                        for row in per_rhs
                    ) / rhs_count,
                    "mean_openimcc_cpu_seconds": sum(
                        row["openimcc_cpu_seconds"] for row in per_rhs
                    ) / rhs_count,
                    "mean_openimcc_gas_calls": sum(
                        row["openimcc_gas_calls"] for row in per_rhs
                    ) / rhs_count,
                    "mean_everything_else_cpu_seconds": sum(
                        row["everything_else_cpu_seconds"] for row in per_rhs
                    ) / rhs_count,
                }
            record["raw_rhs_metrics_path"] = None
            if cprofile_target:
                rhs_metrics_path = Path(tempfile.gettempdir()) / (
                    f"ia_rhs_{label.lower().replace(' ', '_')}_"
                    f"{len(integration_records)}_metrics.json"
                )
                rhs_metrics_path.write_text(json.dumps(record["per_rhs"], indent=2))
                record["raw_rhs_metrics_path"] = str(rhs_metrics_path)
                del record["per_rhs"]
            if profiler is not None:
                stream = io.StringIO()
                pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats(
                    "cumulative"
                ).print_stats(20)
                profile_text = stream.getvalue()
                profile_path = Path(tempfile.gettempdir()) / (
                    f"ia_rhs_{label.lower().replace(' ', '_')}_"
                    f"{len(integration_records)}_cprofile.txt"
                )
                profile_path.write_text(profile_text)
                record["cprofile_path"] = str(profile_path)
                record["cprofile_top20"] = profile_text
            integration_records.append(record)
            active_record.reset(record_token)

    monkeypatch.setattr(
        residue, "integrate_knudsen_residue_inventory", profiled_integrate
    )
    score_started = time.process_time()
    score_wall_started = time.perf_counter()
    try:
        score_loadavg_started = os.getloadavg()
    except (AttributeError, OSError):
        score_loadavg_started = None
    residuals, _candidates = score.score_store(
        context, engines=(Engine.INTERNAL_ANALYTICAL,), include_diagnostics=False,
    )
    chosen = {}
    for residual in residuals:
        if residual.reference not in expected_ids:
            continue
        previous = chosen.get(residual.reference)
        if previous is None or (
            previous.numeric is None
            and residual.numeric is not None
            and residual.refusal is None
        ):
            chosen[residual.reference] = residual
    assert set(chosen) == expected_ids, (
        f"{label}: selected residuals={len(chosen)}, expected={len(expected_ids)}"
    )
    rows_payload = []
    for observation_id, residual in sorted(chosen.items()):
        numeric = None
        if residual.numeric is not None:
            numeric = str(Decimal(str(residual.numeric.value)))
        detail = dict(residual.refusal.detail or {}) if residual.refusal else {}
        rows_payload.append({
            "observation_id": observation_id,
            "numeric": numeric,
            "refusal": str(
                detail.get("reason") or residual.refusal.reason
            ) if residual.refusal else None,
        })
    payload = "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"))
        for row in rows_payload
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    score_cpu_seconds = time.process_time() - score_started
    score_wall_seconds = time.perf_counter() - score_wall_started
    try:
        score_loadavg_ended = os.getloadavg()
    except (AttributeError, OSError):
        score_loadavg_ended = None
    integration_totals = {
        key: sum(float(record.get(key, 0.0) or 0.0) for record in integration_records)
        for key in (
            "process_cpu_seconds", "rhs_cpu_seconds", "rhs_calls", "nfev", "njev",
            "pressure_model_calls", "root_calls", "root_evaluations",
            "ia_vapor_pressure_calls", "ia_vapor_pressure_cpu_seconds_inclusive",
            "openimcc_calls", "openimcc_gas_calls", "openimcc_cpu_seconds",
        )
    }
    numeric_rows = sum(row["numeric"] is not None for row in rows_payload)
    typed_refusal_rows = sum(row["refusal"] is not None for row in rows_payload)
    report = {
        "study": label,
        "row_count": len(rows_payload),
        "numeric_rows": numeric_rows,
        "typed_refusal_rows": typed_refusal_rows,
        "residual_digest": digest,
        "budget_refusal_rows": sum(
            row["refusal"] == "residue_integration_budget_exceeded"
            for row in rows_payload
        ),
        "budget_refusal_integrations": sum(
            record.get("refusal_reason") == "residue_integration_budget_exceeded"
            for record in integration_records
        ),
        "integration_count": len(integration_records),
        "scoring_process_cpu_seconds": score_cpu_seconds,
        "scoring_process_cpu_seconds_per_residual": score_cpu_seconds / len(rows_payload),
        "scoring_wall_seconds": score_wall_seconds,
        "scoring_wall_to_process_cpu_ratio": (
            score_wall_seconds / score_cpu_seconds
            if score_cpu_seconds > 0.0
            else None
        ),
        "scoring_loadavg_1_5_15_start": score_loadavg_started,
        "scoring_loadavg_1_5_15_end": score_loadavg_ended,
        "integration_totals": integration_totals,
        "integration_cpu_seconds_per_residual": (
            integration_totals["process_cpu_seconds"] / len(rows_payload)
        ),
        "memoize": memoize,
        "rhs_cpu_seconds_per_call": (
            integration_totals["rhs_cpu_seconds"]
            / max(integration_totals["rhs_calls"], 1.0)
        ),
        "root_evaluation_scope": (
            "root_evaluations counts brentq callback calls; pressure_model_calls "
            "counts uncached endpoint, warm-bracket, and final diagnostic calls"
        ),
        "integrations": integration_records if cprofile_target else [],
    }
    warnings.warn(
        "IA_RHS_PROFILE " + json.dumps(report, sort_keys=True, separators=(",", ":")),
        UserWarning,
        stacklevel=2,
    )
    return report
