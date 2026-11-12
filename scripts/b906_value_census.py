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

import simulator.optimize.evaluate as evaluate_mod  # noqa: E402
from simulator.condensation import CondensationModel  # noqa: E402
from simulator.condensation_routing import stage_key_for_stage  # noqa: E402
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
    return _leaf(None, _exc_reason(exc))


def _exc_reason(exc: BaseException) -> str:
    # The package exports evaluate as a function. Reading a module attribute
    # off that function is not a green gap; name the missing attribute.
    if isinstance(exc, AttributeError):
        return f"required attribute is absent at this tree: {exc}"
    return f"{type(exc).__name__}: {exc}"


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
    integral = getattr(evaluate_mod, "_optimizer_refused_flux_bounds", None)
    if not callable(integral):
        reason = "refused-flux integral is absent at this tree"
        return {}, {species: reason for species in SPECIES}
    bounds = integral(
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
    authority_fn = getattr(objective_mod, "_coating_authority_status", None)
    refused_fn = getattr(physics_mod, "_coating_wall_refused_species", None)
    if not callable(authority_fn) or not callable(refused_fn):
        return None
    authority = authority_fn(raw, execution)
    return list(refused_fn(authority))


def _book(execution: object) -> dict | None:
    book = getattr(objective_mod, "_book_wall_deposits_at_vapour_supply", None)
    if book is None:
        return None
    trace = getattr(execution, "trace", None)
    raw = getattr(trace, "wall_deposit_by_segment_species_kg", None)
    if not isinstance(raw, Mapping):
        raise RuntimeError("wall_deposit_by_segment_species_kg trace is absent")
    authority_fn = getattr(objective_mod, "_coating_authority_status", None)
    if not callable(authority_fn):
        raise RuntimeError("coating authority status is absent at this tree")
    authority = authority_fn(raw, execution)
    return book(raw, authority, execution)


def _coating_summary(execution: object) -> dict:
    summary_fn = getattr(objective_mod, "_coating_product_summary", None)
    if not callable(summary_fn):
        raise RuntimeError("coating product summary is absent at this tree")
    return dict(summary_fn(execution))


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
    money: Mapping,
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

    used_resolution = resolution
    if item is not None and "ledger_resolution_kg" in item:
        used_resolution = _leaf(item.get("ledger_resolution_kg"))
    consequence = _species_consequence(species, coating, coating_reason, money)
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


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _refusal_text(payload: Mapping) -> str | None:
    refusal = payload.get("wall_deposit_refusal")
    if not isinstance(refusal, Mapping):
        return None
    reason = refusal.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    return None


def _species_wall_map(
    coating: Mapping | None,
) -> tuple[Mapping | None, str | None]:
    if not isinstance(coating, Mapping):
        return None, "coating diagnostic is absent"
    raw = coating.get("wall_deposit_kg_by_species")
    if isinstance(raw, Mapping):
        return raw, None
    if raw is None:
        reason = (
            coating.get("lifespan_cost_status_reason")
            or coating.get("coating_unavailable_reason")
            or "wall_deposit_kg_by_species is absent"
        )
        return None, str(reason)
    return None, "wall_deposit_kg_by_species is not a mapping"


def _consumption_leaf(
    coating: Mapping | None,
    coating_reason: str | None,
) -> dict[str, object]:
    if not isinstance(coating, Mapping):
        return _leaf(None, coating_reason or "coating diagnostic is absent")
    if "furnace_lifespan_consumed_fraction" not in coating:
        return _leaf(
            None,
            "furnace_lifespan_consumed_fraction is absent at this tree",
        )
    value = _finite_number(coating.get("furnace_lifespan_consumed_fraction"))
    status = coating.get("lifespan_cost_status")
    if value is not None and status in (None, "available"):
        return _leaf(value)
    typed = (
        coating.get("lifespan_cost_status_reason")
        or status
        or coating.get("coating_unavailable_reason")
        or coating_reason
        or "coating consumption is unavailable"
    )
    return _leaf(None, str(typed))


def _lifetime_leaf(
    coating: Mapping | None,
    coating_reason: str | None,
) -> dict[str, object]:
    if not isinstance(coating, Mapping):
        return _leaf(None, coating_reason or "coating diagnostic is absent")
    if "campaigns_to_resinter_worst_segment" in coating:
        value = coating.get("campaigns_to_resinter_worst_segment")
    elif "campaigns_to_resinter" in coating:
        value = coating.get("campaigns_to_resinter")
    else:
        typed = (
            coating.get("coating_unavailable_reason")
            or coating_reason
            or "coating lifetime is absent"
        )
        return _leaf(None, str(typed))
    if value is None:
        typed = (
            coating.get("coating_unavailable_reason")
            or coating.get("lifespan_cost_status_reason")
            or "coating lifetime is absent"
        )
        return _leaf(None, str(typed))
    return _leaf(value)


def _default_cost_parameters() -> tuple[object | None, str | None]:
    try:
        import simulator.cost_parameters as cost_mod
    except ImportError:
        return None, "cost parameters are absent at this tree"
    factory = getattr(cost_mod, "cost_parameters_from_mapping", None)
    if not callable(factory):
        return None, "cost_parameters_from_mapping is absent at this tree"
    try:
        return factory(), None
    except Exception as exc:  # noqa: BLE001
        return None, _exc_reason(exc)


def _call_depreciation(
    execution: object,
    sim: object | None,
    parameters: object | None,
    param_reason: str | None,
) -> tuple[object | None, str | None]:
    dep_fn = getattr(objective_mod, "_depreciation_expense_per_run_summary", None)
    if not callable(dep_fn):
        return None, "re-sinter depreciation summary is absent at this tree"
    if parameters is None:
        return None, param_reason
    if sim is None:
        return None, "run simulator is absent"
    try:
        return dep_fn(execution, sim, cost_parameters=parameters), None
    except Exception as exc:  # noqa: BLE001
        return None, _exc_reason(exc)


def _owned_lifetime(coating: Mapping) -> object:
    if "campaigns_to_resinter_worst_segment" in coating:
        return coating.get("campaigns_to_resinter_worst_segment")
    if "campaigns_to_resinter" in coating:
        return coating.get("campaigns_to_resinter")
    return None


def _call_amortization(
    sim: object | None,
    parameters: object | None,
    param_reason: str | None,
    coating: Mapping | None,
    coating_reason: str | None,
) -> tuple[object | None, str | None]:
    amort_fn = getattr(objective_mod, "_furnace_amortization_summary", None)
    if not callable(amort_fn):
        return None, "furnace amortization summary is absent at this tree"
    if parameters is None:
        return None, param_reason
    if sim is None:
        return None, "run simulator is absent"
    if not isinstance(coating, Mapping):
        return None, coating_reason or "coating diagnostic is absent"
    try:
        summary = amort_fn(
            sim,
            _owned_lifetime(coating),
            has_positive_fouling=coating.get("has_positive_qualified_fouling"),
            cost_parameters=parameters,
            coating_status=coating.get("coating_status"),
            coating_status_reason=str(coating.get("coating_status_reason") or ""),
            refusal=coating.get("wall_deposit_refusal"),
        )
    except Exception as exc:  # noqa: BLE001
        return None, _exc_reason(exc)
    return summary, None


def _coating_money(
    execution: object | None,
    coating: Mapping | None,
    coating_reason: str | None,
) -> dict[str, object]:
    """Re-sinter depreciation and furnace amortization.

    ``run_input_cost`` is furnace-input money, not a coating cost.
    """

    if execution is None:
        reason = coating_reason or "lunar run did not return an execution"
        return {
            "depreciation": None,
            "depreciation_reason": reason,
            "amortization": None,
            "amortization_reason": reason,
        }
    parameters, param_reason = _default_cost_parameters()
    sim = getattr(execution, "simulator", None)
    depreciation, dep_reason = _call_depreciation(
        execution,
        sim,
        parameters,
        param_reason,
    )
    amortization, amort_reason = _call_amortization(
        sim,
        parameters,
        param_reason,
        coating,
        coating_reason,
    )
    return {
        "depreciation": depreciation,
        "depreciation_reason": dep_reason,
        "amortization": amortization,
        "amortization_reason": amort_reason,
    }


def _resinter_leaf(money: Mapping) -> dict[str, object]:
    summary = money.get("depreciation")
    absent = money.get("depreciation_reason")
    if not isinstance(summary, Mapping):
        return _leaf(
            None,
            str(absent or "re-sinter depreciation summary is absent at this tree"),
        )
    status = summary.get("status")
    usd = _finite_number(summary.get("depreciation_expense_per_run_usd"))
    # configured_default is a parameter fallback, not the coating ratio.
    if status in {"campaigns_to_resinter", "no_wall_deposit"} and usd is not None:
        return _leaf(usd)
    typed = _refusal_text(summary) or status or absent or "re-sinter cost is unavailable"
    return _leaf(None, str(typed))


def _amortization_leaf(money: Mapping) -> dict[str, object]:
    summary = money.get("amortization")
    absent = money.get("amortization_reason")
    if not isinstance(summary, Mapping):
        return _leaf(
            None,
            str(absent or "furnace amortization summary is absent at this tree"),
        )
    status = summary.get("furnace_amortization_status")
    usd = _finite_number(summary.get("furnace_amortization_cost_per_run_usd"))
    if status == "available" and usd is not None:
        return _leaf(usd)
    typed = (
        summary.get("furnace_amortization_status_reason")
        or _refusal_text(summary)
        or status
        or absent
        or "furnace amortization is unavailable"
    )
    return _leaf(None, str(typed))


def _species_on_wall(
    species: str,
    coating: Mapping | None,
) -> tuple[float | None, str | None]:
    wall, wall_reason = _species_wall_map(coating)
    if wall is None:
        return None, wall_reason or "wall_deposit_kg_by_species is absent"
    if species not in wall:
        return None, "species is absent from wall_deposit_kg_by_species"
    species_kg = _map_kg(wall, species)
    if species_kg is None:
        return None, "species wall deposit kilogram is not finite"
    return species_kg, None


def _species_consumption(
    species: str,
    coating: Mapping | None,
    coating_reason: str | None,
) -> dict[str, object]:
    total = _consumption_leaf(coating, coating_reason)
    if total["value"] is None:
        return _leaf(None, total["reason"])
    species_kg, missing = _species_on_wall(species, coating)
    if species_kg is None:
        return _leaf(None, missing)
    threshold = _finite_number(
        coating.get("resinter_threshold_kg") if isinstance(coating, Mapping) else None
    )
    if threshold is None or threshold <= 0.0:
        return _leaf(None, "resinter_threshold_kg is unavailable")
    return _leaf(species_kg / threshold)


def _species_resinter(
    species: str,
    coating: Mapping | None,
    money: Mapping,
) -> dict[str, object]:
    total = _resinter_leaf(money)
    if total["value"] is None:
        return _leaf(None, total["reason"])
    species_kg, missing = _species_on_wall(species, coating)
    if species_kg is None:
        return _leaf(None, missing)
    summary = money.get("depreciation")
    if not isinstance(summary, Mapping):
        return _leaf(None, "re-sinter depreciation summary is absent")
    if summary.get("status") == "no_wall_deposit":
        return _leaf(0.0)
    price = _finite_number(summary.get("furnace_resinter_cost_usd"))
    threshold = _finite_number(summary.get("resinter_threshold_kg"))
    if price is None or threshold is None or threshold <= 0.0:
        return _leaf(None, "resinter price or threshold is unavailable")
    # USD/resinter * (kg species / kg threshold) is this species' USD/run.
    return _leaf(price * species_kg / threshold)


def _species_amortization(
    species: str,
    coating: Mapping | None,
    money: Mapping,
) -> dict[str, object]:
    total = _amortization_leaf(money)
    if total["value"] is None:
        return _leaf(None, total["reason"])
    species_kg, missing = _species_on_wall(species, coating)
    if species_kg is None:
        return _leaf(None, missing)
    total_usd = _finite_number(total["value"])
    if total_usd is None:
        return _leaf(None, "furnace amortization cost is not finite")
    total_kg = _finite_number(
        coating.get("wall_deposit_total_kg") if isinstance(coating, Mapping) else None
    )
    if total_kg is None or total_kg <= 0.0:
        if species_kg == 0.0 and total_usd == 0.0:
            return _leaf(0.0)
        return _leaf(
            None,
            "wall_deposit_total_kg is unavailable for an amortization share",
        )
    return _leaf(total_usd * species_kg / total_kg)


def _species_consequence(
    species: str,
    coating: Mapping | None,
    coating_reason: str | None,
    money: Mapping,
) -> dict[str, object]:
    return {
        "coating_consumption": _species_consumption(species, coating, coating_reason),
        "resinter_cost_usd": _species_resinter(species, coating, money),
        "amortization_cost_usd": _species_amortization(species, coating, money),
        "coating_lifetime_campaigns": _lifetime_leaf(coating, coating_reason),
    }


def _blank_species(reason: str) -> dict[str, object]:
    missing = _leaf(None, reason)
    return {
        "supply_kg": missing,
        "ledger_resolution_kg": missing,
        "booked_wall_deposit_kg": missing,
        "classification": missing,
        "flag": missing,
        "coating_cost_consequence": _species_consequence(
            "",
            None,
            reason,
            {
                "depreciation": None,
                "depreciation_reason": reason,
                "amortization": None,
                "amortization_reason": reason,
            },
        ),
    }


def _species_census(execution: object | None) -> dict[str, object]:
    if execution is None:
        reason = "lunar run did not return an execution"
        return {species: _blank_species(reason) for species in SPECIES}
    try:
        supply, missing_reason = _supply_kg(execution)
    except Exception as exc:  # noqa: BLE001 -- one probe reports every species
        supply = {}
        missing_reason = {species: _exc_reason(exc) for species in SPECIES}
    resolution = _resolution_kg(_charge_kg(execution))
    try:
        refused = _refused_species(execution)
    except Exception as exc:  # noqa: BLE001
        refused = None
        refused_error = _exc_reason(exc)
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
            booking_error = _exc_reason(exc)
            booking_absent = True
    try:
        coating = _coating_summary(execution)
        coating_reason = None
    except Exception as exc:  # noqa: BLE001
        coating = None
        coating_reason = _exc_reason(exc)
    money = _coating_money(execution, coating, coating_reason)
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
            money=money,
        )
        if booking_error and booking_absent:
            for key in (
                "classification",
                "flag",
                "booked_wall_deposit_kg",
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


def _wall_total_leaf(
    summary: Mapping | None,
    summary_reason: str | None,
) -> dict[str, object]:
    if not isinstance(summary, Mapping):
        return _leaf(None, summary_reason or "coating diagnostic is absent")
    if "wall_deposit_total_kg" not in summary:
        return _leaf(None, "coating wall_deposit_total_kg is absent")
    wall = _leaf(summary.get("wall_deposit_total_kg"))
    if wall["value"] is None and summary.get("lifespan_cost_status_reason"):
        return _leaf(None, str(summary.get("lifespan_cost_status_reason")))
    return wall


def _coating_diagnostic(
    execution: object | None,
    document: Mapping | None,
    *,
    summary: Mapping | None,
    summary_reason: str | None,
) -> dict[str, object]:
    money = _coating_money(execution, summary, summary_reason)
    runner = (
        _runner_wall_kg(document)
        if isinstance(document, Mapping)
        else _leaf(None, summary_reason or "lunar run did not return a document")
    )
    return {
        "wall_deposit_kg": _wall_total_leaf(summary, summary_reason),
        "runner_wall_deposit_kg": runner,
        "coating_consumption": _consumption_leaf(summary, summary_reason),
        "coating_lifetime": _lifetime_leaf(summary, summary_reason),
        "resinter_cost_usd": _resinter_leaf(money),
        "amortization_cost_usd": _amortization_leaf(money),
        "lifespan_cost_status": (
            _leaf(summary.get("lifespan_cost_status"))
            if isinstance(summary, Mapping) and "lifespan_cost_status" in summary
            else _leaf(None, summary_reason or "lifespan_cost_status is absent")
        ),
    }


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
            summary_reason = _exc_reason(exc)
    return _coating_diagnostic(
        execution,
        document,
        summary=summary,
        summary_reason=summary_reason,
    )


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
            "coating_diagnostic": _coating_diagnostic(
                None,
                None,
                summary=None,
                summary_reason=str(failed.get("reason") or "lunar run failed"),
            ),
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
