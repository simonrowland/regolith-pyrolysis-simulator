#!/usr/bin/env python3
"""Census the lunar 24 h fixture for the b-906 owner card.

Reads the fixture ``runner_smoke`` and ``test_condensation_green_pins``
run, plus the item-4 leaves those tests pin. Prints one JSON object.
A field this tree cannot produce is ``{"value": null, "reason": ...}``
so the same script runs at green 3736262ed.

    python3 scripts/b906_value_census.py
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import traceback
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.condensation import CondensationModel  # noqa: E402
from simulator.condensation_routing import stage_key_for_stage  # noqa: E402
from simulator.optimize import evaluate as evaluate_mod  # noqa: E402
from simulator.optimize import objective as objective_mod  # noqa: E402
from simulator.optimize import physics as physics_mod  # noqa: E402
from simulator.recipe_io import load_recipe_patch  # noqa: E402
from simulator.run_executor import RunExecutor  # noqa: E402
from simulator.runner import PyrolysisRun  # noqa: E402

SPECIES = (
    "Al2O",
    "Al2O2",
    "AlO",
    "CaO_gas",
    "CrO",
    "K2",
    "K2O_gas",
    "MgO_gas",
    "Na2",
    "Na2O_gas",
    "SiO2_gas",
    "TiO",
    "TiO2_gas",
)
_METADATA = {
    "started_at_utc": "2026-05-15T00:00:00Z",
    "kernel_commit_sha": "goal-18-fixture",
}
_BOOKING_ABSENT = "wall-supply booking is absent at this tree"
_BELOW = "below_wall_supply_upper_bound"
_UPPER = "wall_deposit_upper_bound_used"


def _leaf(value: object, reason: str | None = None) -> dict[str, object]:
    return {"value": _plain(value), "reason": reason}


def _fail(exc: BaseException) -> dict[str, object]:
    return _leaf(None, f"{type(exc).__name__}: {exc}")


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "infinite" if value > 0.0 else "-infinite"
        return value
    return str(value)


def _git_sha() -> dict[str, object]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        return _fail(exc)
    return _leaf(sha)


def _lunar_run() -> PyrolysisRun:
    # runner_smoke lunar_mare_low_ti_C0_24h and the coating-golden
    # producer. Green pins use the same feedstock, campaign, and hours.
    return PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=24,
        additives_kg={},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides=dict(_METADATA),
    )


def _c2a_run() -> PyrolysisRun:
    # test_condensation_green_pins id c2a_staged.
    patch = load_recipe_patch(
        ROOT / "data/recipes/c2a_staged_temperature_ladder.yaml"
    )
    return PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A_staged",
        hours=9,
        setpoints_patch=patch,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "t1217-green",
        },
    )


class _ExecutionCapture:
    def __init__(self) -> None:
        self.execution: object | None = None
        self._execute = RunExecutor.execute
        self._session = RunExecutor.execute_session

    def __enter__(self) -> _ExecutionCapture:
        capture = self

        def execute(executor: RunExecutor, config: object, **kwargs: object) -> object:
            execution = capture._execute(executor, config, **kwargs)
            capture.execution = execution
            return execution

        def execute_session(
            executor: RunExecutor,
            session: object,
            **kwargs: object,
        ) -> object:
            execution = capture._session(executor, session, **kwargs)
            capture.execution = execution
            return execution

        RunExecutor.execute = execute  # type: ignore[method-assign]
        RunExecutor.execute_session = execute_session  # type: ignore[method-assign]
        return self

    def __exit__(self, *exc: object) -> None:
        RunExecutor.execute = self._execute  # type: ignore[method-assign]
        RunExecutor.execute_session = self._session  # type: ignore[method-assign]


def _empty_fe() -> dict[str, float]:
    return {
        "inlet": 0.0,
        "terminal": 0.0,
        "wall": 0.0,
        **{str(stage): 0.0 for stage in range(8)},
    }


def _instrument_fe(totals: dict[str, float]) -> object:
    original = CondensationModel._route

    def measure(
        model: object,
        flux: object,
        melt: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        result = original(model, flux, melt, *args, **kwargs)
        totals["inlet"] += float(getattr(flux, "species_kg_hr", {}).get("Fe", 0.0))
        totals["terminal"] += float(
            getattr(result, "remaining_by_species", {}).get("Fe", 0.0)
        )
        totals["wall"] += float(
            getattr(result, "wall_deposit_by_species", {}).get("Fe", 0.0)
        )
        captures_by_stage = getattr(result, "condensed_by_stage_species", {})
        for stage_key, captures in captures_by_stage.items():
            stage_number = next(
                stage.stage_number
                for stage in model.train.stages
                if stage_key_for_stage(
                    stage,
                    falsey_sub_order_is_zero=True,
                    include_collector_fallback=True,
                )
                == stage_key
            )
            key = str(stage_number)
            totals[key] = totals.get(key, 0.0) + float(captures.get("Fe", 0.0))
        return result

    CondensationModel._route = measure  # type: ignore[method-assign]
    return original


def _run_fixture(
    run: PyrolysisRun,
    *,
    capture: bool,
) -> tuple[dict, object | None, dict[str, float]]:
    fe = _empty_fe()
    original = _instrument_fe(fe)
    holder = _ExecutionCapture() if capture else None
    try:
        if holder is None:
            document = run.run()
            execution = None
        else:
            with holder:
                document = run.run()
            execution = holder.execution
    finally:
        CondensationModel._route = original  # type: ignore[method-assign]
    return document, execution, fe


def _charge_kg(execution: object) -> float | None:
    reader = getattr(objective_mod, "_run_charge_mass_kg", None)
    if reader is not None:
        mass = reader(execution)
        if mass is not None:
            return float(mass)
    record = getattr(getattr(execution, "simulator", None), "record", None)
    raw = getattr(record, "batch_mass_kg", None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    mass = float(raw)
    if not math.isfinite(mass) or mass <= 0.0:
        return None
    return mass


def _resolution_kg(charge_kg: float | None) -> dict[str, object]:
    if charge_kg is None:
        return _leaf(None, "run charge mass is absent")
    helper = getattr(objective_mod, "_mass_balance_ledger_resolution_kg", None)
    if helper is not None:
        return _leaf(float(helper(charge_kg)))
    abort_pct = getattr(evaluate_mod, "MASS_BALANCE_ABORT_PCT", None)
    if abort_pct is None:
        return _leaf(None, "MASS_BALANCE_ABORT_PCT is absent at this tree")
    return _leaf(float(charge_kg) * float(abort_pct) / 100.0)


def _supply_kg(execution: object) -> tuple[dict[str, float], dict[str, str]]:
    bounds = evaluate_mod._optimizer_refused_flux_bounds(
        getattr(execution, "snapshots", ()),
        SPECIES,
        campaigns_elapsed=1.0,
    )
    present = {species: float(bounds[species]) for species in SPECIES if species in bounds}
    missing = {
        species: _withhold_reason(execution, species)
        for species in SPECIES
        if species not in bounds
    }
    return present, missing


def _withhold_reason(execution: object, species: str) -> str:
    classify = getattr(evaluate_mod, "_classify_wall_supply_interval", None)
    if classify is None:
        return (
            "refused-flux integral omitted this species; "
            "interval classifier is absent"
        )
    evidence: list[str] = []
    for snapshot in tuple(getattr(execution, "snapshots", ()) or ()):
        contribution = classify(snapshot, species)
        if getattr(contribution, "kind", "") != "unavailable":
            continue
        reason = str(getattr(contribution, "evidence", "") or "unavailable")
        if reason not in evidence:
            evidence.append(reason)
    if not evidence:
        return "refused-flux integral omitted this species"
    return "supply integral withheld: " + ", ".join(evidence)


def _refused_species(execution: object) -> list[str] | None:
    trace = getattr(execution, "trace", None)
    raw = getattr(trace, "wall_deposit_by_segment_species_kg", None)
    if not isinstance(raw, Mapping):
        return None
    authority = objective_mod._coating_authority_status(raw, execution)
    return list(physics_mod._coating_wall_refused_species(authority))


def _book(execution: object) -> dict | None:
    book = getattr(objective_mod, "_book_wall_deposits_at_vapour_supply", None)
    if book is None:
        return None
    trace = getattr(execution, "trace", None)
    raw = getattr(trace, "wall_deposit_by_segment_species_kg", None)
    if not isinstance(raw, Mapping):
        raise RuntimeError("wall_deposit_by_segment_species_kg trace is absent")
    authority = objective_mod._coating_authority_status(raw, execution)
    return book(raw, authority, execution)


def _coating_summary(execution: object) -> dict:
    summary = objective_mod._coating_product_summary(execution)
    return dict(summary)


def _species_row(
    species: str,
    *,
    supply: float | None,
    supply_reason: str | None,
    resolution: dict[str, object],
    booking: Mapping | None,
    booking_absent: bool,
    refused: list[str] | None,
    coating: Mapping | None,
    coating_reason: str | None,
) -> dict[str, object]:
    item = None
    if isinstance(booking, Mapping):
        for record in booking.get("bookings") or ():
            if isinstance(record, Mapping) and record.get("species") == species:
                item = record
                break
    if supply is None:
        supply_leaf = _leaf(None, supply_reason or "supply integral omitted this species")
    else:
        supply_leaf = _leaf(supply)

    if booking_absent:
        classification = _leaf(None, _BOOKING_ABSENT)
        flag = _leaf(None, _BOOKING_ABSENT)
        booked = _leaf(None, _BOOKING_ABSENT)
        consequence = _leaf(None, _BOOKING_ABSENT)
    elif item is not None:
        reason = str(item.get("reason") or "")
        flag_value = item.get("flag") or reason or None
        deposit = item.get("deposit_kg")
        if reason == _BELOW:
            classification = _leaf("authorized zero")
        elif reason == _UPPER:
            classification = _leaf(_UPPER)
        else:
            classification = _leaf("unavailable", reason or "booking reason is absent")
        flag = _leaf(flag_value)
        booked = _leaf(deposit)
        consequence = _consequence(
            species,
            classification["value"],
            str(classification["reason"] or "") or None,
            item,
            coating,
            coating_reason,
        )
    else:
        if supply is None:
            reason = supply_reason or "supply integral omitted this species"
        elif refused is None:
            reason = "wall-saturation refusal set is absent"
        elif species not in refused:
            reason = "not in the wall-saturation refusal set"
        else:
            reason = "refused species was not booked"
        classification = _leaf("unavailable", reason)
        flag = _leaf(reason)
        booked = _leaf(None, reason)
        consequence = _leaf(None, reason)

    used_resolution = resolution
    if item is not None and "ledger_resolution_kg" in item:
        used_resolution = _leaf(item.get("ledger_resolution_kg"))
    return {
        "supply_kg": supply_leaf,
        "ledger_resolution_kg": used_resolution,
        "booked_wall_deposit_kg": booked,
        "classification": classification,
        "flag": flag,
        "coating_cost_consequence": consequence,
    }


def _map_kg(mapping: object, species: str) -> float | None:
    if not isinstance(mapping, Mapping) or species not in mapping:
        return None
    raw = mapping[species]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    number = float(raw)
    if not math.isfinite(number):
        return None
    return number


def _consequence(
    species: str,
    classification: object,
    class_reason: str | None,
    item: Mapping,
    coating: Mapping | None,
    coating_reason: str | None,
) -> dict[str, object]:
    if classification == "unavailable":
        return _leaf(None, class_reason or "unavailable")
    if coating_reason is not None or not isinstance(coating, Mapping):
        return _leaf(None, coating_reason or "coating diagnostic is absent")
    if coating.get("coating_unavailable_reason"):
        return _leaf(None, str(coating.get("coating_unavailable_reason")))
    unavailable = None
    cost_status = coating.get("lifespan_cost_status")
    if cost_status not in (None, "available"):
        unavailable = coating.get("lifespan_cost_status_reason") or cost_status
    species_kg = _map_kg(coating.get("wall_deposit_cumulative_kg_by_species"), species)
    if species_kg is None:
        species_kg = _map_kg(coating.get("wall_deposit_kg_by_species"), species)
    if species_kg is None and "deposit_kg" in item:
        species_kg = _map_kg({"deposit": item.get("deposit_kg")}, "deposit")
    if species_kg is None and unavailable:
        return _leaf(None, str(unavailable))
    return _leaf(
        {
            "species_wall_deposit_kg": species_kg,
            "coating_lifetime_campaigns": coating.get(
                "campaigns_to_resinter_worst_segment",
                coating.get("campaigns_to_resinter"),
            ),
            "lifespan_cost_status": cost_status,
            "unavailable_reason": unavailable,
        }
    )


def _species_census(execution: object | None) -> dict[str, object]:
    if execution is None:
        missing = _leaf(None, "lunar run did not return an execution")
        return {species: {
            "supply_kg": missing,
            "ledger_resolution_kg": missing,
            "booked_wall_deposit_kg": missing,
            "classification": missing,
            "flag": missing,
            "coating_cost_consequence": missing,
        } for species in SPECIES}
    try:
        supply, missing_reason = _supply_kg(execution)
    except Exception as exc:  # noqa: BLE001 -- one probe reports every species
        supply = {}
        missing_reason = {
            species: f"{type(exc).__name__}: {exc}" for species in SPECIES
        }
    resolution = _resolution_kg(_charge_kg(execution))
    try:
        refused = _refused_species(execution)
    except Exception as exc:  # noqa: BLE001
        refused = None
        refused_error = f"{type(exc).__name__}: {exc}"
    else:
        refused_error = None
    booking_fn = getattr(objective_mod, "_book_wall_deposits_at_vapour_supply", None)
    booking_absent = booking_fn is None
    booking: dict | None = None
    booking_error = None
    if not booking_absent:
        try:
            booking = _book(execution)
        except Exception as exc:  # noqa: BLE001
            booking_error = f"{type(exc).__name__}: {exc}"
            booking_absent = True
    try:
        coating = _coating_summary(execution)
        coating_reason = None
    except Exception as exc:  # noqa: BLE001
        coating = None
        coating_reason = f"{type(exc).__name__}: {exc}"
    rows = {}
    for species in SPECIES:
        row = _species_row(
            species,
            supply=supply.get(species),
            supply_reason=missing_reason.get(species),
            resolution=resolution,
            booking=booking,
            booking_absent=booking_absent,
            refused=refused,
            coating=coating,
            coating_reason=coating_reason,
        )
        if booking_error and booking_absent:
            for key in (
                "classification",
                "flag",
                "booked_wall_deposit_kg",
                "coating_cost_consequence",
            ):
                row[key] = _leaf(None, booking_error)
        refusal_missing = (
            row["classification"]["reason"]
            == "wall-saturation refusal set is absent"
        )
        if refused_error and refusal_missing:
            row["classification"] = _leaf("unavailable", refused_error)
            row["flag"] = _leaf(None, refused_error)
        rows[species] = row
    return rows


def _runner_wall_kg(document: Mapping) -> dict[str, object]:
    final = document.get("final")
    if not isinstance(final, Mapping) or "wall_deposit_by_species_kg" not in final:
        return _leaf(None, "final.wall_deposit_by_species_kg is absent")
    raw = final.get("wall_deposit_by_species_kg")
    if not isinstance(raw, Mapping):
        return _leaf(None, "final.wall_deposit_by_species_kg is not a mapping")
    total = 0.0
    for kg in raw.values():
        if isinstance(kg, bool) or not isinstance(kg, (int, float)):
            return _leaf(None, "wall deposit kilogram is not numeric")
        number = float(kg)
        if not math.isfinite(number):
            return _leaf(None, "wall deposit kilogram is not finite")
        total += number
    return _leaf({"by_species_kg": dict(raw), "total_kg": total})


def _runner_cost(document: Mapping) -> dict[str, object]:
    metadata = document.get("run_metadata")
    if not isinstance(metadata, Mapping):
        return _leaf(None, "run_metadata is absent")
    rollup = metadata.get("cost_rollup_diagnostic")
    if not isinstance(rollup, Mapping):
        return _leaf(None, "cost_rollup_diagnostic is absent")
    cost = rollup.get("run_input_cost")
    if not isinstance(cost, Mapping):
        return _leaf(None, "run_input_cost is absent")
    return _leaf(
        {
            "allocation_status": cost.get("allocation_status"),
            "owner_ratify_money_projection": cost.get("owner_ratify_money_projection"),
            "unavailable_reason": cost.get("unavailable_reason"),
        }
    )


def _coating_digest(execution: object | None, document: Mapping) -> dict[str, object]:
    summary: Mapping | None
    summary_reason: str | None
    if execution is None:
        summary = None
        summary_reason = "lunar run did not return an execution"
    else:
        try:
            summary = _coating_summary(execution)
            summary_reason = None
        except Exception as exc:  # noqa: BLE001
            summary = None
            summary_reason = f"{type(exc).__name__}: {exc}"
    if summary is None:
        wall = _leaf(None, summary_reason)
        lifetime = _leaf(None, summary_reason)
    else:
        if "wall_deposit_total_kg" in summary:
            wall = _leaf(summary.get("wall_deposit_total_kg"))
            if wall["value"] is None and summary.get("lifespan_cost_status_reason"):
                wall = _leaf(None, str(summary.get("lifespan_cost_status_reason")))
        else:
            wall = _leaf(None, "coating wall_deposit_total_kg is absent")
        lifetime_value = summary.get(
            "campaigns_to_resinter_worst_segment",
            summary.get("campaigns_to_resinter"),
        )
        if lifetime_value is None:
            reason = (
                summary.get("coating_unavailable_reason")
                or summary.get("lifespan_cost_status_reason")
                or "coating lifetime is absent"
            )
            lifetime = _leaf(None, str(reason))
        else:
            lifetime = _leaf(lifetime_value)
    return {
        "wall_deposit_kg": wall,
        "runner_wall_deposit_kg": _runner_wall_kg(document),
        "coating_lifetime": lifetime,
        "cost": _runner_cost(document),
        "lifespan_cost_status": (
            _leaf(summary.get("lifespan_cost_status"))
            if isinstance(summary, Mapping) and "lifespan_cost_status" in summary
            else _leaf(None, summary_reason or "lifespan_cost_status is absent")
        ),
    }


def _fe_capture(run: PyrolysisRun) -> dict[str, object]:
    try:
        document, _execution, fe = _run_fixture(run, capture=False)
    except Exception as exc:  # noqa: BLE001
        return {"status": _fail(exc), "fe_kg": _fail(exc)}
    status = document.get("status") if isinstance(document, Mapping) else None
    return {"status": _leaf(status), "fe_kg": _leaf(fe)}


def _alkali_series() -> dict[str, object]:
    # tests/test_runner_smoke.py per-hour alkali series. The pinned leaf
    # is the Na+K product-ledger sum at hour index 7. series_sum_kg adds
    # that per-hour series.
    try:
        run = PyrolysisRun(
            feedstock_id="lunar_mare_low_ti",
            campaign="C2A_STAGED",
            hours=12,
            mass_kg=1.0,
            allow_fallback_vapor=True,
            allow_unmeasured_alpha_fallback=True,
        )
        session = run._start_session()
        series: list[float] = []
        for _ in range(12):
            decision = session.pending_decision()
            if decision is not None:
                session.decide(decision.recommendation or decision.options[0])
            step = session.advance()
            row = step.per_hour_summary
            values = row["product_ledger_kg_at_hour"]
            series.append(
                sum(float(values.get(species, 0.0)) for species in ("Na", "K"))
            )
    except Exception as exc:  # noqa: BLE001
        return {
            "alkali_per_hour_series_sum_kg": _fail(exc),
            "alkali_pinned_hour_na_k_kg": _fail(exc),
        }
    return {
        "pinned_hour_index": _leaf(7),
        "alkali_pinned_hour_na_k_kg": _leaf(series[7]),
        "alkali_per_hour_series_sum_kg": _leaf(sum(series)),
        "na_k_product_ledger_kg_by_hour": _leaf(series),
    }


def _sio_step_wall_deposit() -> dict[str, object]:
    try:
        from tests.studio_pin_support import (
            sio_pin_values,
            sio_wall_report_at_temperature,
        )
    except ImportError as exc:
        return {"sio_step_wall_deposit_kg": _fail(exc)}
    try:
        report = sio_wall_report_at_temperature(1050.0)
        if isinstance(report, tuple):
            report = report[0]
        values = sio_pin_values(report)
    except Exception as exc:  # noqa: BLE001
        return {"sio_step_wall_deposit_kg": _fail(exc)}
    if "sio_wall_deposit_kg" not in values:
        return {
            "sio_step_wall_deposit_kg": _leaf(
                None, "sio_pin_values has no sio_wall_deposit_kg"
            )
        }
    return {
        "liner_temperature_C": _leaf(1050.0),
        "sio_step_wall_deposit_kg": _leaf(values["sio_wall_deposit_kg"]),
        "wall_SiO_kg": _leaf(values.get("wall_SiO_kg")),
    }


def _lunar_bundle() -> dict[str, object]:
    try:
        document, execution, fe = _run_fixture(_lunar_run(), capture=True)
    except Exception as exc:  # noqa: BLE001
        failed = _fail(exc)
        return {
            "status": failed,
            "traceback": traceback.format_exc(),
            "species": _species_census(None),
            "fe_capture_kg": failed,
            "coating_diagnostic": {
                "wall_deposit_kg": failed,
                "runner_wall_deposit_kg": failed,
                "coating_lifetime": failed,
                "cost": failed,
                "lifespan_cost_status": failed,
            },
        }
    status = document.get("status") if isinstance(document, Mapping) else None
    return {
        "status": _leaf(status),
        "species": _species_census(execution),
        "fe_capture_kg": _leaf(fe),
        "coating_diagnostic": _coating_digest(execution, document),
    }


def main() -> int:
    lunar = _lunar_bundle()
    try:
        c2a = _fe_capture(_c2a_run())
    except Exception as exc:  # noqa: BLE001
        c2a = {"status": _fail(exc), "fe_kg": _fail(exc)}
    payload = {
        "tree_sha": _git_sha(),
        "fixture": _leaf("lunar_mare_low_ti_C0_24h"),
        "species": lunar["species"],
        "item4": {
            "sio_step_wall_deposit": _sio_step_wall_deposit(),
            "fe_capture_per_stage": {
                "lunar_24h": {
                    "status": lunar["status"],
                    "fe_kg": lunar["fe_capture_kg"],
                },
                "c2a_staged": c2a,
            },
            "alkali_per_hour_series": _alkali_series(),
            "coating_diagnostic": lunar["coating_diagnostic"],
        },
    }
    if "traceback" in lunar:
        payload["lunar_traceback"] = lunar["traceback"]
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
