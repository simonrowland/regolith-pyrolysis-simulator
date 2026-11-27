from __future__ import annotations

from contextvars import ContextVar
from decimal import Decimal
import cProfile
from dataclasses import asdict
import hashlib
from itertools import count
import io
import json
import os
import pstats
import signal
import tempfile
import time
from pathlib import Path


def clear_ia_profile_caches(monkeypatch):
    """Start one IA profile with parsed, catalog, and engine caches cold."""

    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE", "off")

    from simulator import engine_cache
    from simulator.battery import migrate, oxygen_balance, residue, score
    from simulator.chemistry import offgas_fo2
    from simulator.melt_backend import openimcc_bridge
    from simulator.vapour_rail import catalog

    for module in (migrate, score):
        for value in vars(module).values():
            clear = getattr(value, "cache_clear", None)
            if callable(clear):
                clear()
    for name in ("_load_cea_source_text", "_load_cea_polynomials_from_text"):
        clear = getattr(getattr(offgas_fo2, name, None), "cache_clear", None)
        if callable(clear):
            clear()
    catalog.clear_vapor_pressure_view_caches()
    for function in (
        getattr(oxygen_balance, "_cached_molar_flux_denominators", None),
        getattr(openimcc_bridge, "_load_pack", None),
    ):
        clear = getattr(function, "cache_clear", None)
        if callable(clear):
            clear()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache._string_bytes.cache_clear()
    engine_cache._key_float_bytes.cache_clear()
    residue._close_openimcc_evaluation_worker()


def profile_ia_residue_source(
    label,
    source_id,
    monkeypatch,
    *,
    observation_ids=None,
    cprofile_target=False,
    memoize=True,
    rhs_cap=None,
    single_hashimoto_experiment=False,
    direct_compile_residual=False,
    flush_every_n_rhs=5,
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
    active_rhs_started = ContextVar(f"ia_profile_rhs_started_{label}", default=None)
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
    original_hashimoto_prediction = score._hashimoto_residue_prediction

    def scoped_hashimoto_prediction(context, reference, engine, *args, **kwargs):
        token = active_row.set(reference.observation_id)
        try:
            if single_hashimoto_experiment:
                kwargs["test_experiment_ids"] = frozenset(
                    (reference.experiment_id,)
                )
            return original_hashimoto_prediction(
                context, reference, engine, *args, **kwargs
            )
        finally:
            active_row.reset(token)

    monkeypatch.setattr(
        score, "_hashimoto_residue_prediction", scoped_hashimoto_prediction
    )

    original_integrate = residue.integrate_knudsen_residue_inventory
    original_finite_integrate = residue.integrate_residue_inventory
    if not memoize:
        original_key = residue._residue_pressure_state_key
        unique_state = count()
        monkeypatch.setattr(
            residue,
            "_residue_pressure_state_key",
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
            "rhs_cap": rhs_cap,
            "profile_cap_reached": False,
            "openimcc_cpu_seconds": 0.0,
            "per_rhs": {},
        }
        profiler = (
            cProfile.Profile(timer=time.process_time_ns, timeunit=1e-9)
            if cprofile_target
            else None
        )
        record_token = active_record.set(record)
        profile_stem = (
            f"ia_rhs_{label.lower().replace(' ', '_')}_{os.getpid()}_"
            f"{len(integration_records)}"
        )
        rhs_metrics_path = Path(tempfile.gettempdir()) / f"{profile_stem}_metrics.json"
        profile_path = Path(tempfile.gettempdir()) / f"{profile_stem}_cprofile.txt"

        def cprofile_views():
            stats = pstats.Stats(profiler).strip_dirs()
            tottime = io.StringIO()
            stats.stream = tottime
            stats.sort_stats("tottime").print_stats(25)
            rooted = io.StringIO()
            stats.stream = rooted
            stats.sort_stats("cumulative")
            root = next(
                key for key in stats.stats
                if key[0].endswith("vapor_pressure.py")
                and key[1] == 2888
                and key[2] == "dispatch"
            )
            # pstats string restrictions are regular expressions; parentheses
            # in this rendered name otherwise turn into a capture group and
            # silently produce no match. Select the exact key to root the view.
            stats.fcn_list = [root]
            stats.print_callees()
            callers = io.StringIO()
            stats.stream = callers
            caller_targets = [
                key for key in stats.stats
                if any(
                    name in pstats.func_std_string(key)
                    for name in (
                        "deepcopy", "generic_visit", "_canonical_jsonable",
                        "compile", "_load_cea_source_text",
                        "_thermo_extract_observation", "known_override_fields",
                        "_override_fields_from_ast", "TextIOWrapper.read",
                    )
                )
            ]
            for target in sorted(caller_targets):
                stats.fcn_list = [target]
                stats.print_callers()
            function_counts = {
                pstats.func_std_string(key): {
                    "primitive_calls": values[0],
                    "calls": values[1],
                    "self_cpu_seconds": values[2],
                    "cumulative_cpu_seconds": values[3],
                }
                for key, values in stats.stats.items()
                if any(
                    name in pstats.func_std_string(key)
                    for name in (
                        "deepcopy", "generic_visit", "_canonical_jsonable",
                        "compile", "_load_cea_source_text",
                        "_thermo_extract_observation", "TextIOWrapper.read",
                    )
                )
            }
            return (
                tottime.getvalue(), rooted.getvalue(), callers.getvalue(),
                function_counts,
            )

        def persist_snapshot(*, resume_profiler=False):
            if profiler is not None:
                profiler.disable()
            if isinstance(record["per_rhs"], dict):
                rhs_rows = [
                    {"rhs_index": int(index), **metrics}
                    for index, metrics in sorted(
                        record["per_rhs"].items(), key=lambda item: int(item[0])
                    )
                ]
            else:
                rhs_rows = [dict(row) for row in record["per_rhs"]]
            active_index = active_rhs.get()
            active_started = active_rhs_started.get()
            if active_index is not None and active_started is not None:
                for row in rhs_rows:
                    if row["rhs_index"] == active_index:
                        row["rhs_cpu_seconds_in_progress"] = max(
                            0.0, time.process_time() - active_started
                        )
                        break
            for row in rhs_rows:
                ia = row.get("ia_cpu_seconds_inclusive", 0.0)
                openimcc_cpu = row.get("openimcc_cpu_seconds", 0.0)
                row["ia_vapor_pressure_cpu_seconds_exclusive"] = max(
                    0.0, ia - openimcc_cpu
                )
                row["everything_else_cpu_seconds"] = max(
                    0.0, row.get("rhs_cpu_seconds", 0.0) - ia
                )
            try:
                with rhs_metrics_path.open("w", encoding="utf-8") as stream:
                    json.dump(rhs_rows, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                if profiler is not None:
                    tottime_text, rooted_text, callers_text, _function_counts = (
                        cprofile_views()
                    )
                    with profile_path.open("w", encoding="utf-8") as output:
                        output.write("TOP 25 BY TOTTIME\n")
                        output.write(tottime_text)
                        output.write("\nCUMULATIVE VIEW ROOTED AT DISPATCH\n")
                        output.write(rooted_text)
                        output.write("\nCALLERS OF COPY, AST, AND CANONICALIZATION\n")
                        output.write(callers_text)
                        output.flush()
                        os.fsync(output.fileno())
            except OSError:
                # The in-memory warning remains useful if the scratch volume
                # itself refuses a periodic write.
                pass
            if resume_profiler and profiler is not None:
                profiler.enable()

        old_alarm_handler = None
        alarm_handler_installed = False
        if cprofile_target and hasattr(signal, "SIGALRM"):
            try:
                old_alarm_handler = signal.getsignal(signal.SIGALRM)

                def on_alarm(signum, frame):
                    persist_snapshot()
                    if callable(old_alarm_handler):
                        old_alarm_handler(signum, frame)
                    raise TimeoutError("profile snapshot written on SIGALRM")

                signal.signal(signal.SIGALRM, on_alarm)
                alarm_handler_installed = True
            except (ValueError, OSError):
                alarm_handler_installed = False

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
                residue._inherit_residue_pressure_contract(
                    tracked_pressure, pressure_model
                )
                record.setdefault("pressure_model_cpu_seconds", 0.0)
                record["pressure_model_cpu_seconds"] += time.process_time() - started

        def solve_probe(function, t_span, y0, *args, **solve_kwargs):
            record["method"] = solve_kwargs.get("method", "RK45")

            def counted_rhs(*rhs_args, **rhs_kwargs):
                rhs_index = record["rhs_calls"]
                if rhs_cap is not None and rhs_index >= rhs_cap:
                    record["profile_cap_reached"] = True
                    from simulator.battery.residue import ResidueInventoryRefusal

                    raise ResidueInventoryRefusal(
                        "ia_rhs_profile_cap_reached",
                        f"profile stopped before RHS {rhs_index + 1}; cap={rhs_cap}",
                    )
                record["rhs_calls"] += 1
                token = active_rhs.set(rhs_index)
                started = time.process_time()
                started_token = active_rhs_started.set(started)
                try:
                    return function(*rhs_args, **rhs_kwargs)
                finally:
                    elapsed = time.process_time() - started
                    record["rhs_cpu_seconds"] += elapsed
                    add_rhs_metric("rhs_cpu_seconds", elapsed)
                    active_rhs_started.reset(started_token)
                    active_rhs.reset(token)
                    if (
                        cprofile_target
                        and flush_every_n_rhs > 0
                        and record["rhs_calls"] % flush_every_n_rhs == 0
                    ):
                        persist_snapshot(resume_profiler=True)

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
            if alarm_handler_installed:
                signal.signal(signal.SIGALRM, old_alarm_handler)
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
                    0.0, rhs["rhs_cpu_seconds"] - ia
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
            record["raw_rhs_metrics_path"] = str(rhs_metrics_path)
            if cprofile_target:
                persist_snapshot()
                tottime_text, rooted_text, callers_text, function_counts = (
                    cprofile_views()
                )
                record["cprofile_path"] = str(profile_path)
                record["cprofile_top25_tottime"] = tottime_text
                record["cprofile_vapor_pressure_dispatch_callees"] = rooted_text
                record["cprofile_hot_function_callers"] = callers_text
                record["cprofile_function_counts"] = function_counts
            integration_records.append(record)
            active_record.reset(record_token)

    monkeypatch.setattr(
        residue, "integrate_knudsen_residue_inventory", profiled_integrate
    )
    if single_hashimoto_experiment:
        finite_integration_count = 0

        def profiled_finite_integrate(initial, channels, pressure_model, **kwargs):
            nonlocal finite_integration_count
            if finite_integration_count >= 1:
                integration_records[-1]["profile_cap_reached"] = True
                integration_records[-1]["profile_cap_reason"] = (
                    "stopped after one Hashimoto finite-step inventory integration"
                )
                from simulator.battery.residue import ResidueInventoryRefusal

                raise ResidueInventoryRefusal(
                    "ia_rhs_profile_cap_reached",
                    "bounded profile stops before Hashimoto integration call 2",
                )
            finite_integration_count += 1
            wall_started = time.perf_counter()
            process_started = time.process_time()
            try:
                loadavg_started = os.getloadavg()
            except (AttributeError, OSError):
                loadavg_started = None
            record = {
                "observation_id": active_row.get(),
                "method": "Hashimoto finite-step inventory update",
                "nfev": None,
                "njev": None,
                "rhs_calls": 0,
                "pressure_model_calls": 0,
                "root_calls": 0,
                "root_evaluations": 0,
                "ia_vapor_pressure_calls": 0,
                "ia_vapor_pressure_cpu_seconds_inclusive": 0.0,
                "openimcc_calls": 0,
                "openimcc_gas_calls": 0,
                "openimcc_cpu_seconds": 0.0,
                "rhs_cap": rhs_cap,
                "profile_cap_reached": False,
                "per_rhs": {},
            }
            profiler = cProfile.Profile(timer=time.process_time_ns, timeunit=1e-9)
            record_token = active_record.set(record)
            rhs_token = active_rhs.set(0)
            started_token = active_rhs_started.set(process_started)
            profile_stem = (
                f"ia_rhs_{label.lower().replace(' ', '_')}_{os.getpid()}_"
                f"{len(integration_records)}"
            )
            rhs_metrics_path = (
                Path(tempfile.gettempdir()) / f"{profile_stem}_metrics.json"
            )
            profile_path = (
                Path(tempfile.gettempdir()) / f"{profile_stem}_cprofile.txt"
            )
            metrics = record["per_rhs"].setdefault(
                "0",
                {"root_calls": 0, "root_evaluations": 0,
                 "pressure_model_calls": 0, "rhs_cpu_seconds": 0.0,
                 "ia_vapor_pressure_calls": 0, "openimcc_calls": 0,
                 "ia_cpu_seconds_inclusive": 0.0, "openimcc_cpu_seconds": 0.0,
                 "openimcc_gas_calls": 0},
            )

            def persist_finite_snapshot(*, resume_profiler=False):
                profiler.disable()
                row = {"rhs_index": 0, **metrics}
                row["ia_vapor_pressure_cpu_seconds_exclusive"] = max(
                    0.0,
                    row["ia_cpu_seconds_inclusive"] - row["openimcc_cpu_seconds"],
                )
                row["everything_else_cpu_seconds"] = max(
                    0.0, row["rhs_cpu_seconds"] - row["ia_cpu_seconds_inclusive"]
                )
                try:
                    with rhs_metrics_path.open("w", encoding="utf-8") as stream:
                        json.dump([row], stream, indent=2)
                        stream.flush()
                        os.fsync(stream.fileno())
                    stream = io.StringIO()
                    pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats(
                        "cumulative"
                    ).print_stats(20)
                    with profile_path.open("w", encoding="utf-8") as output:
                        output.write(stream.getvalue())
                        output.flush()
                        os.fsync(output.fileno())
                except OSError:
                    pass
                if resume_profiler:
                    profiler.enable()

            old_alarm_handler = signal.getsignal(signal.SIGALRM)

            def on_alarm(signum, frame):
                persist_finite_snapshot()
                if callable(old_alarm_handler):
                    old_alarm_handler(signum, frame)
                raise TimeoutError("Hashimoto profile snapshot written on SIGALRM")

            alarm_handler_installed = False
            try:
                signal.signal(signal.SIGALRM, on_alarm)
                alarm_handler_installed = True
            except (ValueError, OSError):
                pass

            def finite_pressure_probe(inventory, logp):
                if rhs_cap is not None and record["pressure_model_calls"] >= rhs_cap:
                    record["profile_cap_reached"] = True
                    from simulator.battery.residue import ResidueInventoryRefusal

                    raise ResidueInventoryRefusal(
                        "ia_rhs_profile_cap_reached",
                        f"profile stopped before pressure evaluation {rhs_cap + 1}",
                    )
                record["pressure_model_calls"] += 1
                metrics["pressure_model_calls"] += 1
                started = time.process_time()
                try:
                    return pressure_model(inventory, logp)
                finally:
                    elapsed = time.process_time() - started
                    record["pressure_model_cpu_seconds"] = (
                        record.get("pressure_model_cpu_seconds", 0.0) + elapsed
                    )
                    if record["pressure_model_calls"] % max(flush_every_n_rhs, 1) == 0:
                        metrics["rhs_cpu_seconds"] = (
                            time.process_time() - process_started
                        )
                        persist_finite_snapshot(resume_profiler=True)

            def finite_root_probe(function, *args, **root_kwargs):
                record["root_calls"] += 1
                metrics["root_calls"] += 1

                def counted_root(value):
                    record["root_evaluations"] += 1
                    metrics["root_evaluations"] += 1
                    return function(value)

                return original_brentq(counted_root, *args, **root_kwargs)

            oxygen_balance.brentq = finite_root_probe
            profiler.enable()
            try:
                result = original_finite_integrate(
                    initial, channels, finite_pressure_probe, **kwargs
                )
                return result
            except Exception as exc:
                record["refusal_reason"] = getattr(exc, "reason", None)
                raise
            finally:
                profiler.disable()
                oxygen_balance.brentq = original_brentq
                if alarm_handler_installed:
                    signal.signal(signal.SIGALRM, old_alarm_handler)
                record["process_cpu_seconds"] = time.process_time() - process_started
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
                metrics["rhs_cpu_seconds"] = record["process_cpu_seconds"]
                record["per_rhs"] = [{"rhs_index": 0, **metrics}]
                record["per_rhs_summary"] = {
                    "rhs_count": 1,
                    "root_calls_total": record["root_calls"],
                    "root_evaluations_total": record["root_evaluations"],
                    "pressure_model_calls_total": record["pressure_model_calls"],
                    "mean_root_calls_per_rhs": record["root_calls"],
                    "mean_root_evaluations_per_rhs": record["root_evaluations"],
                    "mean_pressure_model_calls_per_rhs": record["pressure_model_calls"],
                    "mean_rhs_cpu_seconds": record["process_cpu_seconds"],
                    "mean_ia_vapor_pressure_cpu_seconds_exclusive": max(
                        0.0,
                        metrics["ia_cpu_seconds_inclusive"]
                        - metrics["openimcc_cpu_seconds"],
                    ),
                    "mean_openimcc_cpu_seconds": metrics["openimcc_cpu_seconds"],
                    "mean_everything_else_cpu_seconds": max(
                        0.0,
                        record["process_cpu_seconds"]
                        - metrics["ia_cpu_seconds_inclusive"],
                    ),
                }
                record["raw_rhs_metrics_path"] = str(rhs_metrics_path)
                persist_finite_snapshot()
                stream = io.StringIO()
                pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats(
                    "cumulative"
                ).print_stats(20)
                record["cprofile_path"] = str(profile_path)
                record["cprofile_top20"] = stream.getvalue()
                integration_records.append(record)
                active_rhs_started.reset(started_token)
                active_rhs.reset(rhs_token)
                active_record.reset(record_token)

        monkeypatch.setattr(
            residue, "integrate_residue_inventory", profiled_finite_integrate
        )
    score_started = time.process_time()
    score_wall_started = time.perf_counter()
    try:
        score_loadavg_started = os.getloadavg()
    except (AttributeError, OSError):
        score_loadavg_started = None
    if direct_compile_residual:
        assert len(rows) == 1, "direct residual profiling requires one observation"
        residual, direct_candidate = score.compile_residual(
            rows[0], Engine.INTERNAL_ANALYTICAL, context=context
        )
        residuals = (residual,)
    else:
        direct_candidate = None
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
    selected_row_output = None
    if direct_candidate is not None:
        selected_row_output = {
            "candidate_value": str(direct_candidate.value.point),
            "candidate_provenance": dict(direct_candidate.provenance),
            "residual_notices": [
                asdict(notice)
                for notice in residuals[0].notices
            ],
        }
    selected_output_digest = (
        hashlib.sha256(
            json.dumps(
                selected_row_output,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
        if selected_row_output is not None
        else None
    )
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
    budget_refusal_reasons = {
        "residue_integration_budget_exceeded",
        "knudsen_extent_evaluation_limit",
    }
    report = {
        "study": label,
        "row_count": len(rows_payload),
        "numeric_rows": numeric_rows,
        "typed_refusal_rows": typed_refusal_rows,
        "residual_digest": digest,
        "budget_refusal_rows": sum(
            row["refusal"] in budget_refusal_reasons
            for row in rows_payload
        ),
        "budget_refusal_integrations": sum(
            record.get("refusal_reason") in budget_refusal_reasons
            for record in integration_records
        ),
        "selected_row_output": selected_row_output,
        "selected_row_output_digest": selected_output_digest,
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
        "profile_cap_reached": any(
            record.get("profile_cap_reached") for record in integration_records
        ),
    }
    print(
        "IA_RHS_PROFILE "
        + json.dumps(report, sort_keys=True, separators=(",", ":")),
        flush=True,
    )
    return report
