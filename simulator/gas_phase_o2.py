"""Gas-phase O2 trajectory.

One owner emits the three analyzer views of the same oxygen:

* molar flow, from the melt-offgas bleed step integral
* partial pressure, both of the carrier mixture and of the furnace gas
* analyzer-visible ppm, Eq. (1) of Robinot et al. 2026 with a first-order lag

Sauerborn's furnace pO2 reads ``furnace_p_O2_Pa`` from this function.
It does not grow a second converter.

The bleed writer stores step moles in ``melt_offgas_O2_mol_hr``
(``PyrolysisSimulator._project_overhead_bleed_partition`` assigns
``melt_o2_bled_mol``). On a one-hour step that number is also mol/h.
This module divides by the step duration, so a shorter step stays a rate.

``process.condensation_train`` is not an analyzer account. Oxygen
booked there is reported and is not added to the flow. The terminal
accounts the bleed writer fills are the melt-offgas stored, vented,
and captured accounts.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from simulator.account_ids import (
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MELT_OFFGAS_CAPTURED_ACCOUNT,
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
)
from simulator.physical_constants import PA_PER_MBAR
from simulator.process_timestep import exponential_approach_fraction


# Normal state in Robinot Eq. (3): 101325 Pa and 273.15 K.
# RT/P = 22.414 L/mol. The paper's outlet integration uses that state.
STANDARD_MOLAR_VOLUME_L_PER_MOL = 22.414
CONDENSATION_TRAIN_ACCOUNT = "process.condensation_train"
ANALYZER_VISIBLE_TERMINAL_ACCOUNTS = (
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,
    OXYGEN_MELT_OFFGAS_CAPTURED_ACCOUNT,
)
OWNER_NAME = "simulator.gas_phase_o2.gas_phase_o2_trajectory"


class GasPhaseO2Error(ValueError):
    """A gas-phase O2 sample cannot be turned into a trajectory."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason)
        message = self.reason if not detail else f"{self.reason}: {detail}"
        super().__init__(message)


def o2_mole_fraction_in_carrier(
    o2_release_mol_min: float,
    *,
    carrier_flow_nl_min: float,
) -> float:
    """Mole fraction of O2 in the carrier, Robinot Eq. (1).

    Premise: the outlet is the carrier plus the oxygen that left with
    it, both as volumetric flows at the same normal state. Eq. (1)
    writes Q_tot = Q_carrier / (1 - X_O2), so
    X_O2 = n_dot_O2 / (n_dot_O2 + n_dot_carrier).

    Algebra: n_dot_carrier = Q_NL_min / 22.414 mol/min.

    Units: both rates in mol/min, fraction dimensionless.

    Sanity: n_dot_O2 = n_dot_carrier / 99 is 1 percent, 10000 ppm.
    """

    o2_rate = float(o2_release_mol_min)
    carrier_flow = float(carrier_flow_nl_min)
    if not math.isfinite(o2_rate) or o2_rate < 0.0:
        raise GasPhaseO2Error(
            "o2_release_not_nonnegative",
            "O2 molar flow must be finite and non-negative",
        )
    if not math.isfinite(carrier_flow) or carrier_flow <= 0.0:
        raise GasPhaseO2Error(
            "carrier_flow_not_positive",
            "carrier NL/min must be finite and positive",
        )
    n_dot_carrier = carrier_flow / STANDARD_MOLAR_VOLUME_L_PER_MOL
    return o2_rate / (n_dot_carrier + o2_rate)


def analyzer_visible_ppm(
    o2_release_mol_min: float,
    *,
    carrier_flow_nl_min: float,
) -> float:
    """Instantaneous analyzer ppm from the carrier mole fraction."""

    return (
        o2_mole_fraction_in_carrier(
            o2_release_mol_min,
            carrier_flow_nl_min=carrier_flow_nl_min,
        )
        * 1_000_000.0
    )


def _optional_float(value: Any, reason: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise GasPhaseO2Error(reason, f"value {value!r} is not finite")
    return number


def _mapping_value(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def step_records_from_snapshots(
    snapshots: Sequence[Any],
) -> list[dict[str, Any]]:
    """Copy the located snapshot fields. This does not compute ppm.

    ``melt_offgas_O2_mol_hr`` is the bleed step integral, in moles.
    ``overhead.composition['O2']`` is a partial pressure in mbar.
    """

    records: list[dict[str, Any]] = []
    for index, snapshot in enumerate(snapshots):
        t_s = _mapping_value(snapshot, "t_s")
        duration_h = _mapping_value(snapshot, "duration_h")
        hour = _mapping_value(snapshot, "hour")
        if t_s is None:
            if hour is None or duration_h is None:
                raise GasPhaseO2Error(
                    "snapshot_time_missing",
                    f"snapshots[{index}] has no t_s and no hour",
                )
            time_s = float(hour) * 3600.0
            dt_s = float(duration_h) * 3600.0
        else:
            time_s = float(t_s)
            if duration_h is None:
                raise GasPhaseO2Error(
                    "snapshot_dt_missing",
                    f"snapshots[{index}] has t_s and no duration_h",
                )
            dt_s = float(duration_h) * 3600.0
        overhead = _mapping_value(snapshot, "overhead")
        composition = _mapping_value(overhead, "composition") if overhead is not None else None
        partial_mbar = None
        if isinstance(composition, Mapping) and "O2" in composition:
            partial_mbar = _optional_float(
                composition["O2"],
                "overhead_o2_partial_not_finite",
            )
        condensation = _mapping_value(snapshot, "condensation_totals") or {}
        condensation_o2_kg = None
        if isinstance(condensation, Mapping) and "O2" in condensation:
            condensation_o2_kg = _optional_float(
                condensation["O2"],
                "condensation_train_o2_not_finite",
            )
        records.append(
            {
                "time_s": time_s,
                "dt_s": dt_s,
                "melt_offgas_o2_step_mol": _optional_float(
                    _mapping_value(snapshot, "melt_offgas_O2_mol_hr"),
                    "melt_offgas_step_mol_not_finite",
                ),
                "overhead_o2_partial_mbar": partial_mbar,
                "condensation_train_o2_kg": condensation_o2_kg,
                "source_side_o2_kg": _optional_float(
                    _mapping_value(snapshot, "oxygen_produced_kg"),
                    "source_side_o2_not_finite",
                ),
            }
        )
    return records


def _molar_flow_mol_min(step: Mapping[str, Any], index: int) -> float | None:
    explicit = step.get("o2_release_mol_min")
    if explicit is not None:
        rate = _optional_float(explicit, "o2_release_not_nonnegative")
        if rate is None or rate < 0.0:
            raise GasPhaseO2Error(
                "o2_release_not_nonnegative",
                f"steps[{index}] molar flow is {explicit!r}",
            )
        return rate
    step_mol = step.get("melt_offgas_o2_step_mol")
    if step_mol is None:
        return None
    moles = _optional_float(step_mol, "melt_offgas_step_mol_not_finite")
    if moles is None:
        return None
    if moles < 0.0:
        raise GasPhaseO2Error(
            "o2_release_not_nonnegative",
            f"steps[{index}] step moles are {moles}",
        )
    dt_s = _optional_float(step.get("dt_s"), "snapshot_dt_missing")
    if dt_s is None or dt_s <= 0.0:
        raise GasPhaseO2Error(
            "snapshot_dt_missing",
            f"steps[{index}] dt_s is {step.get('dt_s')!r}",
        )
    return moles * 60.0 / dt_s


def gas_phase_o2_trajectory(
    steps: Sequence[Mapping[str, Any]],
    *,
    carrier_flow_nl_min: float | None,
    chamber_pressure_mbar: float | None,
    analyzer_tau_s: float | None,
    analyzer_notice: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit molar flow, partial pressure, and analyzer-visible ppm.

    Callers pass the located step records. This function decides the
    three numbers. A missing step integral stays missing. It is not
    replaced with zero. An explicit zero is the booked zero.

    The lag is the exact step response of a first-order hold:
    y = y_prev + (1 - exp(-dt / tau)) * (x - y_prev). The paper's
    published series is above the stabilized baseline, so the lag
    starts at zero rather than at the argon impurity.
    """

    notices: list[dict[str, Any]] = []
    if analyzer_notice:
        notices.append(dict(analyzer_notice))
    carrier = _optional_float(carrier_flow_nl_min, "carrier_flow_not_positive")
    if carrier is None:
        notices.append(
            {
                "kind": "typed_unknown",
                "knob": "carrier_flow_nl_min",
                "reason": "carrier_flow_missing",
                "source": (
                    "No carrier flow was passed. ppm is not computed. "
                    "Furnace partial pressure is still emitted."
                ),
            }
        )
    elif carrier <= 0.0:
        raise GasPhaseO2Error(
            "carrier_flow_not_positive",
            f"carrier flow is {carrier}",
        )
    chamber_mbar = _optional_float(
        chamber_pressure_mbar,
        "chamber_pressure_not_finite",
    )
    chamber_pa = None if chamber_mbar is None else chamber_mbar * PA_PER_MBAR
    if chamber_pa is None:
        notices.append(
            {
                "kind": "typed_unknown",
                "knob": "chamber_pressure_mbar",
                "reason": "chamber_pressure_missing",
                "source": (
                    "Flow-mixture p_O2 needs the chamber pressure. "
                    "ppm does not: Robinot Eq. (3) uses the normal state."
                ),
            }
        )
    tau = _optional_float(analyzer_tau_s, "analyzer_tau_not_positive")
    if tau is None:
        notices.append(
            {
                "kind": "typed_unknown",
                "knob": "analyzer_time_constant_s",
                "reason": "analyzer_response_time_not_printed",
                "source": "ppm_analyzer is withheld until a tau is stated.",
            }
        )
    elif tau <= 0.0:
        raise GasPhaseO2Error(
            "analyzer_tau_not_positive",
            f"tau is {tau}",
        )

    lagged = 0.0
    points: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            raise GasPhaseO2Error(
                "step_record_malformed",
                f"steps[{index}] must be a mapping",
            )
        time_s = _optional_float(step.get("time_s"), "snapshot_time_missing")
        if time_s is None:
            raise GasPhaseO2Error(
                "snapshot_time_missing",
                f"steps[{index}] has no time_s",
            )
        rate = _molar_flow_mol_min(step, index)
        mole_fraction = None
        ppm_instant = None
        if rate is not None and carrier is not None:
            mole_fraction = o2_mole_fraction_in_carrier(
                rate,
                carrier_flow_nl_min=carrier,
            )
            ppm_instant = mole_fraction * 1_000_000.0
        ppm_analyzer = None
        if ppm_instant is not None and tau is not None:
            if step.get("anchor") == "pre_step_baseline":
                lagged = ppm_instant
            else:
                dt_s = _optional_float(step.get("dt_s"), "snapshot_dt_missing")
                if dt_s is None or dt_s <= 0.0:
                    raise GasPhaseO2Error(
                        "snapshot_dt_missing",
                        f"steps[{index}] dt_s is {step.get('dt_s')!r}",
                    )
                lagged = lagged + exponential_approach_fraction(tau, dt_s) * (
                    ppm_instant - lagged
                )
            ppm_analyzer = lagged
        elif ppm_instant is not None and step.get("anchor") == "pre_step_baseline":
            lagged = ppm_instant
        flow_pa = None
        if mole_fraction is not None and chamber_pa is not None:
            flow_pa = mole_fraction * chamber_pa
        partial_mbar = _optional_float(
            step.get("overhead_o2_partial_mbar"),
            "overhead_o2_partial_not_finite",
        )
        furnace_pa = None if partial_mbar is None else partial_mbar * PA_PER_MBAR
        points.append(
            {
                "time_s": time_s,
                "time_min": time_s / 60.0,
                "dt_s": step.get("dt_s"),
                "o2_release_mol_min": rate,
                "mole_fraction": mole_fraction,
                "ppm_instant": ppm_instant,
                "ppm_analyzer": ppm_analyzer,
                "p_O2_flow_Pa": flow_pa,
                "furnace_p_O2_Pa": furnace_pa,
                "condensation_train_o2_kg": step.get("condensation_train_o2_kg"),
                "source_side_o2_kg": step.get("source_side_o2_kg"),
                "anchor": step.get("anchor"),
            }
        )
    return {
        "owner": OWNER_NAME,
        "accounts": {
            "analyzer_visible_terminal": list(ANALYZER_VISIBLE_TERMINAL_ACCOUNTS),
            "not_the_analyzer": CONDENSATION_TRAIN_ACCOUNT,
            "step_integral_field": "melt_offgas_O2_mol_hr",
            "source_side_total_field": "oxygen_produced_kg",
        },
        "carrier_flow_nl_min": carrier,
        "chamber_pressure_mbar": chamber_mbar,
        "analyzer_tau_s": tau,
        "notices": notices,
        "points": points,
    }
