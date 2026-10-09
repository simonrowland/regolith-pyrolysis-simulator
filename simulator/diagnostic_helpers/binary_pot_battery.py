"""Binary-pot engine-arm battery.

Every melt engine runs the same small oxide-pair (and one ternary) pots so
engine-vs-engine residuals are visible per species, per pot, per temperature.
Residuals are the result. No gate, no retune.

``divergence_label`` is a descriptive magnitude band only; never an
acceptance verdict (same posture as ``simulator.vapour_rail.engine_crosscheck``).

Backends are resolved through ``resolve_backend`` (and the existing VR-5
warm-pool opener for VapoRock). Diagnostic cells call
``MeltBackend.equilibrate()`` on the resolved adapter. They do not route
through ``PyrolysisSimulator._get_equilibrium``: that path rejects VapoRock
and MAGEMin for missing ``ledger_transition`` when selected as the active
backend (``INELIGIBLE_ACTIVE_BACKENDS``).
"""

from __future__ import annotations

import atexit
import inspect
import json
import math
import os
import re
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from simulator.physical_constants import (
    CATALOG_PHYSICAL_PRESSURE_CEILING_PA,
    CELSIUS_TO_KELVIN_OFFSET,
    MELT_DISSOCIATION_PO2_MIN_BAR,
    PA_PER_BAR,
)
from simulator.battery.enums import NoticeKind
from simulator.yaml_cache import load_cached_safe_yaml
from simulator.vapour_rail.engine_crosscheck import divergence_label


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POTS_PATH = REPO_ROOT / "data" / "binary_pots.yaml"
REPORT_DIR = (
    REPO_ROOT / "docs-private" / "research" / "2026-09-12-differential-harness"
)
REPORT_STEM = "binary-pot-engine-arm"
REPORT_SCHEMA_VERSION = 1

BATTERY_ENGINE_NAMES: tuple[str, ...] = (
    "internal-analytical",
    "alphamelts",
    "thermoengine",
    "vaporock",
    "magemin",
    "cached-real",
    "openimcc",
)
OPENIMCC_ENGINE_NAMES: tuple[str, ...] = ("openimcc",)
IMCC_ENGINE_NAMES: tuple[str, ...] = OPENIMCC_ENGINE_NAMES
OPENIMCC_MODEL_IDS: dict[str, str] = {"openimcc": "IMCC-SF04"}
ALL_IMCC_MODEL_IDS: dict[str, str] = OPENIMCC_MODEL_IDS
MELTS_FAMILY_ENGINES: tuple[str, ...] = ("alphamelts", "thermoengine")


def _openimcc_gas_channels_and_omission_notices(
    parent_oxides: Sequence[str], datapack: Any, gas_result: Any | None = None
) -> tuple[list[Any], tuple[dict[str, Any], ...]]:
    """Return the active gas reactions and typed notices for omitted channels."""

    from openimcc.gas import _default_reactions

    channels, default_omissions = _default_reactions(parent_oxides, datapack)
    omitted_channels = getattr(gas_result, "omitted_channels", default_omissions)
    table_path = str(datapack.gas_path)
    notices = tuple(
        {
            "kind": NoticeKind.INPUT_OMITTED.value,
            "authority": None,
            "reason": (
                f"gas channel {name} omitted: {reason}; table_path_diagnostic: {table_path}"
            ),
        }
        for name, reason in omitted_channels.items()
    )
    return channels, notices
ARM_HEADLINE = "headline"
ARM_QUALIFICATION = "qualification"

QUANTITY_ACTIVITY = "melt_activity"
QUANTITY_PRESSURE = "gas_partial_pressure_Pa"

PO2_ENGINE_DEFAULT = "engine_default"
PO2_COMMANDED = "commanded"
PO2_OXYGEN_BALANCE_EFFUSION = "oxygen_balance_effusion"
# Caller-stated oxygen omission, including condensed activity without a
# multivalent element, is never rewritten as engine fO2 = -9.
PO2_NOT_AN_INPUT = "not_an_input"

REFUSAL_OUT_OF_BASIS = "out_of_basis"
REFUSAL_COMPOSITION_PROJECTED = "composition_projected"
REFUSAL_MAJOR_SUM = "sum_below_95_wt_pct"
REFUSAL_NO_LIQUID = "no_liquid"
REFUSAL_TIMEOUT = "engine_timeout"
REFUSAL_ENGINE_CRASH = "engine_crash"
ENGINE_ANNOTATION_CRASH_FLOOR = "sio2_below_observed_crash_floor"
REFUSAL_GATE_REFUSED_IN_ADAPTER = "gate_refused_in_adapter"
REFUSAL_UNAVAILABLE = "unavailable"
REFUSAL_VALUE_IS_FLOOR = "value_is_floor"
AUTHORITY_EXTRAPOLATED = "extrapolated"
# Token published by engines/builtin/vapor_pressure.py on pO2-floor inversion.
_FLOOR_INVERSION_REASON = "melt_dissociation_pO2_floor_inverted_through_mass_action"

FINDING_CLASS_FALLBACK_VS_SPECIATION = "fallback_vs_speciation"
AUTHORITY_FALLBACK = "fallback"
AUTHORITY_SPECIATION = "speciation"
_ANTOINE_FALLBACK_PREFIX = "antoine_fallback_from_vaporock"

_MAJOR_SUM_TOKENS = frozenset(
    {"major_sum", "sum_below_95_wt_pct", "major oxide sum"}
)
_OUT_OF_BASIS_TOKENS = frozenset(
    {"forbidden_species", "out_of_basis", "out-of-basis", "unsupported_melts_species"}
)
_NO_LIQUID_TOKENS = frozenset(
    {"liquid_state", "no_liquid", "no liquid", "liquid_fraction"}
)
_TIMEOUT_TOKENS = frozenset(
    {"engine_timeout", "timeout", "engine_worker_timeout"}
)
_CRASH_TOKENS = frozenset(
    {
        "engine_crash",
        "subprocess_died",
        "sigabrt",
        "sigsegv",
        "sigbus",
        "sigkill",
    }
)
_UNAVAILABLE_TOKENS = frozenset(
    {"unavailable", "backend_unavailable", "not_initialized"}
)
_IMCC_OUT_OF_BASIS_CODES = frozenset(
    {
        "imcc_component_outside_domain",
        "imcc_ferric_input_unsupported",
        "imcc_sp_extension_required",
        "imcc_composition_outside_validated_envelope",
        "imcc_composition_incomplete",
    }
)

_DEFAULT_PRESSURE_BAR = 1.0e-6
_DEFAULT_FO2_LOG = -9.0
_WT_PCT_SUM_TOLERANCE = 1.0e-9

# Outer wall around equilibrate(). Engine workers already have their own
# call timeouts; this is the battery's hard stop so a wedged call becomes
# engine_timeout instead of a hang. Values sit a few seconds above the
# engine's own warm-call wall.
_ENGINE_OUTER_TIMEOUT_S: dict[str, float] = {
    "internal-analytical": 5.0,
    "alphamelts": 30.0,
    "thermoengine": 10.0,
    "vaporock": 70.0,
    "magemin": 20.0,
    "cached-real": 30.0,
    "openimcc": 15.0,
}

QUALIFICATION_SIO2_SWEEP_WT_PCT: tuple[float, ...] = (
    20.0,
    25.0,
    30.0,
    35.0,
    85.0,
    90.0,
)
QUALIFICATION_TEMPERATURES_K: tuple[float, ...] = (
    1100.0,
    1200.0,
    2400.0,
    2600.0,
)
QUALIFICATION_SWEEP_T_K = 1700.0
QUALIFICATION_SOURCE_POT_ID = "feo_mgo_sio2_30_20_50"

_ISOLATED_CELL_BOOTSTRAP = """
import json
import sys

from simulator.diagnostic_helpers.binary_pot_battery import (
    _run_isolated_cell_worker_loop,
)

_run_isolated_cell_worker_loop()
"""


class BinaryPotBatteryError(RuntimeError):
    """Raised when the pot catalog or residual inputs violate a hard contract."""


class _InternalAnalyticalInputRefusal(ValueError):
    """Typed missing/invalid input at the core VAPOR_PRESSURE adapter boundary."""

    def __init__(self, category: int, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.category = category
        self.code = reason_code


@dataclass(frozen=True)
class BinaryPot:
    pot_id: str
    kato_1993_table4_system: str | None
    why: str
    composition_wt_pct: Mapping[str, float]


@dataclass(frozen=True)
class BatteryGrid:
    temperatures_K: tuple[float, ...]
    engine_default_po2: bool
    commanded_po2_bar: tuple[float, ...]


@dataclass(frozen=True)
class Po2Request:
    mode: str
    po2_bar: float | None
    cell_material: str | None = None

    def as_payload(self) -> dict[str, Any]:
        payload = {"mode": self.mode, "po2_bar": self.po2_bar}
        if self.cell_material is not None:
            payload["cell_material"] = self.cell_material
        return payload


@dataclass
class EngineHandle:
    name: str
    backend: Any | None
    available: bool
    unavailable_reason: str | None
    takes_fo2: bool
    supports_intrinsic_fo2: bool
    identity: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class EquilibrateCell:
    pot_id: str
    engine: str
    temperature_K: float
    po2: Po2Request
    status: str
    refusal_reason: str | None
    engine_status: str | None
    engine_reason: str | None
    melt_activities: dict[str, float]
    gas_partial_pressures_Pa: dict[str, float]
    liquid_fraction: float | None
    wall_s: float
    cpu_s: float
    hostname: str
    vapor_pressures_source: dict[str, str] = field(default_factory=dict)
    vapor_pressure_backend_status: str | None = None
    vapor_pressure_backend_status_reason: str | None = None
    authoritative_for_requested_vapor_pressure: bool | None = None
    arm: str = ARM_HEADLINE
    notices: list[dict[str, Any]] = field(default_factory=list)
    authority: str | None = None
    certified_band: dict[str, Any] | None = None
    exit_signal: int | None = None
    exit_code: int | None = None
    model_id: str | None = None
    engine_annotation: str | None = None
    # gamma, where the engine reports it. Activity stays on melt_activities.
    # a = gamma * x. An empty map is not activity reused as a coefficient.
    melt_activity_coefficients: dict[str, float] = field(default_factory=dict)
    melt_activity_coefficient_details: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )

    def as_payload(self) -> dict[str, Any]:
        return {
            "pot_id": self.pot_id,
            "engine": self.engine,
            "temperature_K": self.temperature_K,
            "po2": self.po2.as_payload(),
            "status": self.status,
            "refusal_reason": self.refusal_reason,
            "engine_status": self.engine_status,
            "engine_reason": self.engine_reason,
            "engine_annotation": self.engine_annotation,
            "melt_activities": dict(self.melt_activities),
            "melt_activity_coefficients": dict(self.melt_activity_coefficients),
            "melt_activity_coefficient_details": {
                str(name): dict(row)
                for name, row in self.melt_activity_coefficient_details.items()
            },
            "gas_partial_pressures_Pa": dict(self.gas_partial_pressures_Pa),
            "liquid_fraction": self.liquid_fraction,
            "wall_s": self.wall_s,
            "cpu_s": self.cpu_s,
            "hostname": self.hostname,
            "vapor_pressures_source": dict(self.vapor_pressures_source),
            "vapor_pressure_backend_status": self.vapor_pressure_backend_status,
            "vapor_pressure_backend_status_reason": (
                self.vapor_pressure_backend_status_reason
            ),
            "authoritative_for_requested_vapor_pressure": (
                self.authoritative_for_requested_vapor_pressure
            ),
            "arm": self.arm,
            "notices": [dict(row) for row in self.notices],
            "authority": self.authority,
            "certified_band": (
                None if self.certified_band is None else dict(self.certified_band)
            ),
            "exit_signal": self.exit_signal,
            "exit_code": self.exit_code,
            "model_id": self.model_id,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "EquilibrateCell":
        po2_raw = payload.get("po2") or {}
        po2_bar = po2_raw.get("po2_bar") if isinstance(po2_raw, Mapping) else None
        notices_raw = payload.get("notices") or []
        notices = [
            dict(row) for row in notices_raw if isinstance(row, Mapping)
        ]
        certified_raw = payload.get("certified_band")
        return cls(
            pot_id=str(payload.get("pot_id") or ""),
            engine=str(payload.get("engine") or ""),
            temperature_K=float(payload.get("temperature_K") or 0.0),
            po2=Po2Request(
                mode=str(
                    (po2_raw.get("mode") if isinstance(po2_raw, Mapping) else None)
                    or PO2_ENGINE_DEFAULT
                ),
                po2_bar=None if po2_bar is None else float(po2_bar),
                cell_material=(
                    None
                    if not isinstance(po2_raw, Mapping)
                    else po2_raw.get("cell_material")
                ),
            ),
            status=str(payload.get("status") or ""),
            refusal_reason=payload.get("refusal_reason"),
            engine_status=payload.get("engine_status"),
            engine_reason=payload.get("engine_reason"),
            engine_annotation=payload.get("engine_annotation"),
            melt_activities=dict(payload.get("melt_activities") or {}),
            gas_partial_pressures_Pa=dict(
                payload.get("gas_partial_pressures_Pa") or {}
            ),
            melt_activity_coefficients={
                str(name): float(value)
                for name, value in dict(
                    payload.get("melt_activity_coefficients") or {}
                ).items()
            },
            melt_activity_coefficient_details={
                str(name): dict(value)
                for name, value in dict(
                    payload.get("melt_activity_coefficient_details") or {}
                ).items()
                if isinstance(value, Mapping)
            },
            liquid_fraction=_finite_float(payload.get("liquid_fraction")),
            wall_s=float(payload.get("wall_s") or 0.0),
            cpu_s=float(payload.get("cpu_s") or 0.0),
            hostname=str(payload.get("hostname") or ""),
            vapor_pressures_source={
                str(name): str(label)
                for name, label in dict(
                    payload.get("vapor_pressures_source") or {}
                ).items()
                if label is not None
            },
            vapor_pressure_backend_status=(
                None
                if payload.get("vapor_pressure_backend_status") is None
                else str(payload.get("vapor_pressure_backend_status"))
            ),
            vapor_pressure_backend_status_reason=(
                None
                if payload.get("vapor_pressure_backend_status_reason") is None
                else str(payload.get("vapor_pressure_backend_status_reason"))
            ),
            authoritative_for_requested_vapor_pressure=(
                None
                if payload.get("authoritative_for_requested_vapor_pressure")
                is None
                else bool(
                    payload.get("authoritative_for_requested_vapor_pressure")
                )
            ),
            arm=str(payload.get("arm") or ARM_HEADLINE),
            notices=notices,
            authority=(
                None
                if payload.get("authority") is None
                else str(payload.get("authority"))
            ),
            certified_band=(
                None if not isinstance(certified_raw, Mapping) else dict(certified_raw)
            ),
            exit_signal=_optional_int(payload.get("exit_signal")),
            exit_code=_optional_int(payload.get("exit_code")),
            model_id=(
                None
                if payload.get("model_id") is None
                else str(payload.get("model_id"))
            ),
        )


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def identity_battery_pot(
    pot_id: str,
    composition_wt_pct: Mapping[str, float],
    *,
    why: str = "v2.1 observation identity pot",
) -> BinaryPot:
    """Build a BinaryPot from an observation's solved-pot wt% vector.

    The battery scorer reuses equilibrate_cell / isolated-cell machinery
    rather than forking a second engine arm. Catalog pots stay untouched.
    """

    return BinaryPot(
        pot_id=pot_id,
        kato_1993_table4_system=None,
        why=why,
        composition_wt_pct=dict(composition_wt_pct),
    )


def temperature_grid_K(
    start: float, stop: float, step: float
) -> tuple[float, ...]:
    """Inclusive arithmetic grid. Premise: start + n*step covers stop.

    Algebra: n = round((stop-start)/step); values = start + i*step for
    i = 0..n. Unit check: kelvin. Sanity: 1500, 100, 2300 → 9 points
    ending at 2300.
    """

    start_f = float(start)
    stop_f = float(stop)
    step_f = float(step)
    if not all(math.isfinite(v) for v in (start_f, stop_f, step_f)):
        raise BinaryPotBatteryError("temperature grid bounds must be finite")
    if step_f <= 0.0:
        raise BinaryPotBatteryError("temperature grid step must be positive")
    if stop_f < start_f:
        raise BinaryPotBatteryError("temperature grid stop must be >= start")
    n = int(round((stop_f - start_f) / step_f))
    values = tuple(start_f + i * step_f for i in range(n + 1))
    if not math.isclose(values[-1], stop_f, rel_tol=0.0, abs_tol=1.0e-9):
        raise BinaryPotBatteryError(
            f"temperature grid {start_f:g}:{step_f:g}:{stop_f:g} does not land on stop"
        )
    return values


def _finite_nonneg_wt(value: Any, *, oxide: str, pot_id: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BinaryPotBatteryError(
            f"{pot_id}.{oxide} weight must be numeric"
        ) from exc
    if not math.isfinite(number) or number < 0.0:
        raise BinaryPotBatteryError(
            f"{pot_id}.{oxide} weight must be finite and non-negative"
        )
    return number


def load_binary_pots(
    path: Path | None = None,
) -> tuple[tuple[BinaryPot, ...], BatteryGrid]:
    """Load pots and the T/pO2 grid from data, not from code."""

    pots_path = Path(path) if path is not None else DEFAULT_POTS_PATH
    payload = load_cached_safe_yaml(pots_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        raise BinaryPotBatteryError(f"{pots_path} must be a mapping")

    grid_block = payload.get("temperature_grid_K")
    if not isinstance(grid_block, Mapping):
        raise BinaryPotBatteryError("temperature_grid_K must be a mapping")
    temperatures = temperature_grid_K(
        grid_block.get("start"),
        grid_block.get("stop"),
        grid_block.get("step"),
    )

    po2_block = payload.get("po2_bar") or {}
    if not isinstance(po2_block, Mapping):
        raise BinaryPotBatteryError("po2_bar must be a mapping")
    engine_default = bool(po2_block.get("engine_default", True))
    commanded_raw = po2_block.get("commanded") or []
    if not isinstance(commanded_raw, Sequence) or isinstance(commanded_raw, (str, bytes)):
        raise BinaryPotBatteryError("po2_bar.commanded must be a list")
    commanded: list[float] = []
    for item in commanded_raw:
        value = float(item)
        if not math.isfinite(value) or value <= 0.0:
            raise BinaryPotBatteryError("commanded pO2 values must be finite and positive")
        commanded.append(value)

    pots_block = payload.get("pots")
    if not isinstance(pots_block, Mapping) or not pots_block:
        raise BinaryPotBatteryError("pots mapping is missing or empty")

    pots: list[BinaryPot] = []
    for pot_id, row in pots_block.items():
        if not isinstance(row, Mapping):
            raise BinaryPotBatteryError(f"pot {pot_id!r} must be a mapping")
        composition = row.get("composition_wt_pct")
        if not isinstance(composition, Mapping) or not composition:
            raise BinaryPotBatteryError(
                f"pot {pot_id!r} has no composition_wt_pct"
            )
        wt: dict[str, float] = {}
        for oxide, value in composition.items():
            weight = _finite_nonneg_wt(value, oxide=str(oxide), pot_id=str(pot_id))
            if weight == 0.0:
                continue
            wt[str(oxide)] = weight
        if len(wt) < 2:
            raise BinaryPotBatteryError(
                f"pot {pot_id!r} must contain at least two oxides "
                "(melt engines cannot take a single species)"
            )
        total = sum(wt.values())
        if not math.isclose(total, 100.0, rel_tol=0.0, abs_tol=_WT_PCT_SUM_TOLERANCE):
            raise BinaryPotBatteryError(
                f"pot {pot_id!r} composition_wt_pct sums to {total:g}, not 100"
            )
        kato = row.get("kato_1993_table4_system")
        kato_system = None if kato in (None, "", "null") else str(kato)
        pots.append(
            BinaryPot(
                pot_id=str(pot_id),
                kato_1993_table4_system=kato_system,
                why=str(row.get("why") or "").strip(),
                composition_wt_pct=wt,
            )
        )
    if not pots:
        raise BinaryPotBatteryError("no pots loaded")
    return tuple(pots), BatteryGrid(
        temperatures_K=temperatures,
        engine_default_po2=engine_default,
        commanded_po2_bar=tuple(commanded),
    )


def composition_kg_and_mol(
    composition_wt_pct: Mapping[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    """1 kg batch: kg = wt%/100; mol = kg * 1000 / M.

    Premise: catalog weights are oxide wt% on a 100 g basis.
    Algebra: m_kg,i = w_i / 100; n_mol,i = m_kg,i * 1000 / M_i
    with M_i in g/mol. Unit check: g / (g/mol) = mol.
    Sanity: 50 wt% SiO2 on 1 kg → 0.5 kg → 8.315 mol (M=60.084).
    """

    from simulator.accounting.formulas import parse_formula
    from simulator.state import MOLAR_MASS

    kg: dict[str, float] = {}
    mol: dict[str, float] = {}
    for oxide, wt in composition_wt_pct.items():
        mass_kg = float(wt) / 100.0
        kg[oxide] = mass_kg
        if oxide in MOLAR_MASS:
            molar_mass = float(MOLAR_MASS[oxide])
        else:
            # Scoring-arm PbO-P2O5 pots: PbO is not an OXIDE_SPECIES.
            # CIAAW formula mass lets the 1 kg batch convert; domain gates
            # still refuse PbO. Do not add PbO to the runtime melt basis.
            molar_mass = float(parse_formula(oxide, species=oxide).molar_mass_g_mol)
        if not math.isfinite(molar_mass) or molar_mass <= 0.0:
            raise BinaryPotBatteryError(f"{oxide} molar mass is not usable")
        mol[oxide] = mass_kg * 1000.0 / molar_mass
    return kg, mol


def po2_requests_for_engine(
    grid: BatteryGrid, *, takes_fo2: bool
) -> tuple[Po2Request, ...]:
    requests: list[Po2Request] = []
    if grid.engine_default_po2:
        requests.append(Po2Request(mode=PO2_ENGINE_DEFAULT, po2_bar=None))
    if takes_fo2:
        for value in grid.commanded_po2_bar:
            requests.append(Po2Request(mode=PO2_COMMANDED, po2_bar=float(value)))
    if not requests:
        requests.append(Po2Request(mode=PO2_ENGINE_DEFAULT, po2_bar=None))
    return tuple(requests)


# ---------------------------------------------------------------------------
# Residuals
# ---------------------------------------------------------------------------


def residual_log10(value_a: float, value_b: float) -> float:
    """Signed dex residual log10(A/B). Antisymmetric in (A, B).

    Premise: matched engine-vs-engine comparison is a ratio of the same
    reported quantity (activity or partial pressure).
    Algebra: log10(A/B) = log10 A − log10 B.
    Unit check: dimensionless (dex).
    Sanity: A=B → 0; A=10 B → +1; swap engines → sign flip.
    """

    a = float(value_a)
    b = float(value_b)
    if not math.isfinite(a) or not math.isfinite(b) or a <= 0.0 or b <= 0.0:
        raise BinaryPotBatteryError(
            "residual_log10 requires finite positive values"
        )
    return math.log10(a / b)


def _reported_value_has_floor_provenance(
    source_label: str | None,
    backend_status_reason: str | None,
    engine_reason: str | None,
) -> bool:
    blob = " ".join(
        str(part)
        for part in (source_label, backend_status_reason, engine_reason)
        if part
    )
    if not blob:
        return False
    return (
        _FLOOR_INVERSION_REASON in blob
        or REFUSAL_VALUE_IS_FLOOR in blob
    )


def classify_reported_value(
    value: Any,
    *,
    quantity: str,
    engine: str,
    source_label: str | None = None,
    backend_status_reason: str | None = None,
    engine_reason: str | None = None,
) -> dict[str, Any] | None:
    """Typed refusal when a reported number is a floor, sentinel, or absence.

    Floor-derived pressures are identified by provenance on the cell
    (source label / backend reason / engine reason), not by matching the
    oxygen-bar clamp ``MELT_DISSOCIATION_PO2_MIN_BAR`` as if it were Pa.

    Premise: a gas partial pressure at or above
    ``CATALOG_PHYSICAL_PRESSURE_CEILING_PA`` (1e9 Pa = 10 kbar) is not a
    vacuum-pyrolysis vapor pressure; it is the pO2 clamp inverted through a
    negative mass-action exponent. Algebra: P_Si = a_SiO2 * P_ref *
    (pO2 / 1e-9) ** (-1); with pO2 floored at 1e-30 bar the pO2 term is
    1e21. Unit check: bar/bar dimensionless, P_ref in Pa. Sanity: 1e9 Pa
    is 10 kbar, already above any vacuum-pyrolysis vapor; the 1700 K
    exploded Si cell is ~4.5e20 Pa.
    """

    number = _finite_float(value)
    if number is None or number <= 0.0:
        return None
    if quantity != QUANTITY_PRESSURE:
        return None
    if number >= CATALOG_PHYSICAL_PRESSURE_CEILING_PA:
        return {
            "reason": REFUSAL_VALUE_IS_FLOOR,
            "engine": str(engine),
            "floor_value": MELT_DISSOCIATION_PO2_MIN_BAR,
            "reported_value": number,
        }
    if _reported_value_has_floor_provenance(
        source_label, backend_status_reason, engine_reason
    ):
        return {
            "reason": REFUSAL_VALUE_IS_FLOOR,
            "engine": str(engine),
            "floor_value": MELT_DISSOCIATION_PO2_MIN_BAR,
            "reported_value": number,
        }
    return None


def collect_floor_refusals(
    cells: Sequence[EquilibrateCell] | Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Per-species value_is_floor records; not scored as residuals."""

    rows: list[dict[str, Any]] = []
    for cell in cells:
        payload = cell.as_payload() if isinstance(cell, EquilibrateCell) else dict(cell)
        if payload.get("status") != "ok":
            continue
        engine = str(payload.get("engine") or "")
        po2 = payload.get("po2") or {}
        for quantity, field_name in (
            (QUANTITY_ACTIVITY, "melt_activities"),
            (QUANTITY_PRESSURE, "gas_partial_pressures_Pa"),
        ):
            for name, raw in dict(payload.get(field_name) or {}).items():
                refusal = classify_reported_value(
                    raw,
                    quantity=quantity,
                    engine=engine,
                    source_label=_source_label_for_species(payload, str(name)),
                    backend_status_reason=payload.get(
                        "vapor_pressure_backend_status_reason"
                    ),
                    engine_reason=payload.get("engine_reason"),
                )
                if refusal is None:
                    continue
                rows.append(
                    {
                        "pot_id": payload.get("pot_id"),
                        "temperature_K": payload.get("temperature_K"),
                        "po2_mode": po2.get("mode"),
                        "po2_bar": po2.get("po2_bar"),
                        "species": str(name),
                        "quantity": quantity,
                        **refusal,
                    }
                )
    rows.sort(
        key=lambda row: (
            str(row.get("pot_id") or ""),
            float(row.get("temperature_K") or 0.0),
            str(row.get("species") or ""),
            str(row.get("engine") or ""),
        )
    )
    return rows


def pairwise_residuals(
    cells: Sequence[EquilibrateCell] | Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Engine-vs-engine residual per (pot, T, pO2, species, quantity)."""

    grouped: dict[tuple[Any, ...], dict[str, EquilibrateCell | Mapping[str, Any]]] = (
        defaultdict(dict)
    )
    for cell in cells:
        payload = cell.as_payload() if isinstance(cell, EquilibrateCell) else dict(cell)
        if payload.get("status") != "ok":
            continue
        po2 = payload.get("po2") or {}
        key = (
            payload["pot_id"],
            float(payload["temperature_K"]),
            po2.get("mode"),
            po2.get("po2_bar"),
        )
        grouped[key][str(payload["engine"])] = payload

    rows: list[dict[str, Any]] = []
    for (pot_id, temperature_K, po2_mode, po2_bar), by_engine in grouped.items():
        engines = sorted(by_engine)
        for i, engine_a in enumerate(engines):
            for engine_b in engines[i + 1 :]:
                left = by_engine[engine_a]
                right = by_engine[engine_b]
                for quantity, field_name in (
                    (QUANTITY_ACTIVITY, "melt_activities"),
                    (QUANTITY_PRESSURE, "gas_partial_pressures_Pa"),
                ):
                    species = sorted(
                        set(left.get(field_name) or {})
                        & set(right.get(field_name) or {})
                    )
                    for name in species:
                        value_a = float((left.get(field_name) or {})[name])
                        value_b = float((right.get(field_name) or {})[name])
                        if classify_reported_value(
                            value_a,
                            quantity=quantity,
                            engine=engine_a,
                            source_label=_source_label_for_species(left, name),
                            backend_status_reason=left.get(
                                "vapor_pressure_backend_status_reason"
                            ),
                            engine_reason=left.get("engine_reason"),
                        ) or classify_reported_value(
                            value_b,
                            quantity=quantity,
                            engine=engine_b,
                            source_label=_source_label_for_species(right, name),
                            backend_status_reason=right.get(
                                "vapor_pressure_backend_status_reason"
                            ),
                            engine_reason=right.get("engine_reason"),
                        ):
                            continue
                        if not _pressure_pair_is_like_for_like_speciation(
                            left,
                            right,
                            species=name,
                            quantity=quantity,
                        ):
                            continue
                        try:
                            delta = residual_log10(value_a, value_b)
                        except BinaryPotBatteryError:
                            continue
                        rows.append(
                            {
                                "pot_id": pot_id,
                                "temperature_K": temperature_K,
                                "po2_mode": po2_mode,
                                "po2_bar": po2_bar,
                                "species": name,
                                "quantity": quantity,
                                "engine_a": engine_a,
                                "engine_b": engine_b,
                                "value_a": value_a,
                                "value_b": value_b,
                                "delta_log10_a_minus_b": delta,
                                "divergence_label": divergence_label(abs(delta)),
                                "finding_class": finding_class_for_pair(
                                    left, right, species=name, quantity=quantity
                                ),
                            }
                        )
    rows.sort(
        key=lambda row: (
            -abs(float(row["delta_log10_a_minus_b"])),
            row["pot_id"],
            row["species"],
            row["engine_a"],
            row["engine_b"],
        )
    )
    return rows


# ---------------------------------------------------------------------------
# Classification / extraction
# ---------------------------------------------------------------------------


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _optional_int(value: Any) -> int | None:
    if value is None or value is False:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_reported_quantities(
    result: Any,
) -> tuple[dict[str, float], dict[str, float]]:
    """Record EquilibriumResult fields the engine actually exposed.

    Melt activities live on the legacy field ``activity_coefficients``.
    Gas partial pressures live on ``vapor_pressures_Pa``. VapoRock's
    non-authoritative path keeps the live speciation on
    ``vaporock_full_speciation_Pa`` and blanks ``vapor_pressures_Pa``
    (same consumer order as ``engine_crosscheck._run_vaporock_cell``).
    Extra attributes are not invented.
    """

    activities: dict[str, float] = {}
    for name, value in dict(getattr(result, "activity_coefficients", None) or {}).items():
        number = _finite_float(value)
        if number is not None and number > 0.0:
            activities[str(name)] = number
    raw_pressures = (
        getattr(result, "vaporock_full_speciation_Pa", None)
        or getattr(result, "vapor_pressures_Pa", None)
        or {}
    )
    pressures: dict[str, float] = {}
    for name, value in dict(raw_pressures).items():
        number = _finite_float(value)
        if number is not None and number > 0.0:
            pressures[str(name)] = number
    return activities, pressures


def reported_activity_coefficients(result: Any) -> dict[str, float]:
    """Gamma, when the engine reported it separately from activity.

    a = gamma * x. The activity field is not reused as gamma.
    """

    direct = getattr(result, "reported_activity_coefficients", None)
    nested = None
    if not isinstance(direct, Mapping) or not direct:
        diagnostics = getattr(result, "diagnostics", None) or {}
        if isinstance(diagnostics, Mapping):
            nested = diagnostics.get("reported_activity_coefficients")
    source = direct if isinstance(direct, Mapping) and direct else nested
    gammas: dict[str, float] = {}
    if not isinstance(source, Mapping):
        return gammas
    for name, value in source.items():
        number = _finite_float(value)
        if number is not None and number > 0.0:
            gammas[str(name)] = number
    return gammas


def _imcc_activity_coefficient_reports(
    parent_activities: Mapping[str, float], composition_mol: Mapping[str, float]
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    """Build single-cation Gamma values and their reference-state labels."""

    from simulator.chemistry.melt_activity import (
        single_cation_activity_and_fraction,
        single_cation_component_formula,
    )

    values: dict[str, float] = {}
    details: dict[str, dict[str, Any]] = {}
    for oxide, parent_activity in parent_activities.items():
        single_activity, fraction = single_cation_activity_and_fraction(
            str(oxide), float(parent_activity), composition_mol
        )
        if fraction <= 0.0:
            continue
        component = single_cation_component_formula(str(oxide))
        gamma = single_activity / fraction
        values[component] = gamma
        details[component] = {
            "value": gamma,
            "coefficient_basis": "single_cation",
            "standard_state": {
                "convention": "raoultian_pure_endmember",
                "phase": "l",
                "component_basis": component,
            },
        }
    return values, details


def _plain_data(value: Any) -> Any:
    """JSON-safe copy. Commissioning notices carry tuples; report dumps do not."""

    if isinstance(value, Mapping):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_data(item) for item in value]
    return value


def engine_flags_from_result(
    result: Any,
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None]:
    """Read the engine's own flag, authority, and notice once.

    The IMCC battery backend stores one notice list on both
    ``diagnostics['imcc_notices']`` and ``result.imcc_notices``. Reading
    both appended every notice twice.
    """

    diagnostics = getattr(result, "diagnostics", None) or {}
    if not isinstance(diagnostics, Mapping):
        diagnostics = {}
    notices: list[dict[str, Any]] = []
    authority: str | None = None
    certified_band: dict[str, Any] | None = None

    commissioning = diagnostics.get("commissioning_notice")
    if isinstance(commissioning, Mapping):
        row = _plain_data(commissioning)
        notices.append(row)
        if row.get("authority"):
            authority = str(row["authority"])
        band = row.get("certified_band")
        if isinstance(band, Mapping):
            certified_band = dict(band)
    if authority is None and diagnostics.get("authority"):
        authority = str(diagnostics["authority"])
    if certified_band is None and isinstance(
        diagnostics.get("certified_band"), Mapping
    ):
        certified_band = _plain_data(diagnostics["certified_band"])

    if diagnostics.get("imcc_notices"):
        imcc_rows = diagnostics["imcc_notices"]
    else:
        imcc_rows = getattr(result, "imcc_notices", None) or []
    for row in imcc_rows:
        if not isinstance(row, Mapping):
            continue
        copied = _plain_data(row)
        notices.append(copied)
        if authority is None and copied.get("authority"):
            authority = str(copied["authority"])
    return notices, authority, certified_band


def cell_score_authority(cell: Any, *, refused: bool) -> str:
    """Envelope authority from the cell. Bridge only when the engine set none."""

    if refused:
        return "refused"
    authority = getattr(cell, "authority", None)
    if authority:
        return str(authority)
    for row in getattr(cell, "notices", None) or []:
        if isinstance(row, Mapping) and row.get("authority"):
            return str(row["authority"])
    return "bridge"


def cell_score_notice_kinds(cell: Any) -> tuple[str, ...]:
    kinds: list[str] = []
    for row in getattr(cell, "notices", None) or []:
        if isinstance(row, Mapping) and row.get("kind"):
            kinds.append(str(row["kind"]))
    return tuple(kinds)


def extract_vapor_authority(result: Any) -> dict[str, Any]:
    """Copy EquilibriumResult vapor-authority flags the harness used to drop.

    ThermoEngine/AlphaMELTS mark an Antoine fallback on
    ``vapor_pressures_source`` (prefix ``antoine_fallback_from_vaporock``)
    and ``diagnostics['vapor_pressure_backend_status'] == 'fallback'``.
    Standalone VapoRock blanks ``vapor_pressures_Pa`` on the
    non-authoritative path and keeps live speciation on
    ``vaporock_full_speciation_Pa``; that is still a speciation result.
    """

    diagnostics = dict(getattr(result, "diagnostics", None) or {})
    sources = {
        str(name): str(label)
        for name, label in dict(
            getattr(result, "vapor_pressures_source", None) or {}
        ).items()
        if label is not None
    }
    auth = diagnostics.get("authoritative_for_requested_vapor_pressure")
    return {
        "vapor_pressures_source": sources,
        "vapor_pressure_backend_status": diagnostics.get(
            "vapor_pressure_backend_status"
        ),
        "vapor_pressure_backend_status_reason": diagnostics.get(
            "vapor_pressure_backend_status_reason"
        ),
        "authoritative_for_requested_vapor_pressure": (
            None if auth is None else bool(auth)
        ),
    }


def _source_label_for_species(payload: Mapping[str, Any], species: str) -> str:
    sources = payload.get("vapor_pressures_source") or {}
    if not isinstance(sources, Mapping):
        return ""
    raw = sources.get(species)
    if raw is None:
        raw = sources.get(str(species))
    return str(raw or "")


def vapor_authority_kind(
    payload: Mapping[str, Any],
    *,
    species: str,
    quantity: str,
) -> str | None:
    """Return ``fallback`` or ``speciation`` from flags the engine actually set.

    Does not infer from the number. A missing flag is ``None`` — that is
    the previous harness dropping the field, not an unflagged fallback.
    """

    if quantity != QUANTITY_PRESSURE:
        return None
    source = _source_label_for_species(payload, species)
    status = str(payload.get("vapor_pressure_backend_status") or "")
    if status == "fallback" or source.startswith(_ANTOINE_FALLBACK_PREFIX):
        return AUTHORITY_FALLBACK
    if source == "vaporock" or source.startswith("vaporock"):
        return AUTHORITY_SPECIATION
    # Standalone VapoRock keeps live speciation off vapor_pressures_Pa.
    if str(payload.get("engine") or "") == "vaporock":
        return AUTHORITY_SPECIATION
    # AlphaMELTS VapoRock helper succeeds with a full gas inventory
    # (Si2/Mg2/SiO2_gas) but labels rows builtin_authoritative:* rather
    # than 'vaporock'. That is still a speciation number, not the Antoine
    # fallback ThermoEngine records as antoine_fallback_from_vaporock.
    if source.startswith("builtin_authoritative"):
        return AUTHORITY_SPECIATION
    return None


def finding_class_for_pair(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    species: str,
    quantity: str,
) -> str | None:
    """Tag a residual when one side is Antoine fallback and the other speciation."""

    kinds = {
        vapor_authority_kind(left, species=species, quantity=quantity),
        vapor_authority_kind(right, species=species, quantity=quantity),
    }
    if kinds == {AUTHORITY_FALLBACK, AUTHORITY_SPECIATION}:
        return FINDING_CLASS_FALLBACK_VS_SPECIATION
    return None


def _pressure_pair_is_like_for_like_speciation(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    species: str,
    quantity: str,
) -> bool:
    """Admit pressure residuals only on a like-for-like authority pair.

    Mixed fallback/speciation/missing maps stay on the cell payloads as
    raw diagnostics; they do not enter the ordinary speciation ranking.
    Two missing-flag cells remain comparable — absence is not fallback.
    Melt activity comparisons are not gated by vapor-authority flags.
    """

    if quantity != QUANTITY_PRESSURE:
        return True
    kind_a = vapor_authority_kind(left, species=species, quantity=quantity)
    kind_b = vapor_authority_kind(right, species=species, quantity=quantity)
    if kind_a != kind_b:
        return False
    return kind_a != AUTHORITY_FALLBACK


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit_progress(progress_log: Path | None, message: str) -> None:
    line = message.rstrip()
    print(line, flush=True)
    if progress_log is None:
        return
    path = Path(progress_log)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


def _haystack(*parts: Any) -> str:
    chunks: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, Mapping):
            chunks.append(json.dumps(part, sort_keys=True, default=str))
        elif isinstance(part, (list, tuple)):
            chunks.extend(str(item) for item in part)
        else:
            chunks.append(str(part))
    return " ".join(chunks).lower()


def _bucket_from_text(text: str) -> str | None:
    if any(token in text for token in _TIMEOUT_TOKENS):
        return REFUSAL_TIMEOUT
    if any(token in text for token in _CRASH_TOKENS):
        return REFUSAL_ENGINE_CRASH
    if any(token in text for token in _MAJOR_SUM_TOKENS):
        return REFUSAL_MAJOR_SUM
    if any(token in text for token in _OUT_OF_BASIS_TOKENS):
        return REFUSAL_OUT_OF_BASIS
    if any(token in text for token in _NO_LIQUID_TOKENS):
        return REFUSAL_NO_LIQUID
    if any(token in text for token in _UNAVAILABLE_TOKENS):
        return REFUSAL_UNAVAILABLE
    return None


def composition_projected_note(
    dropped_components: Sequence[str],
    dropped_mass_fraction: float | None,
) -> str:
    names = ", ".join(str(name) for name in dropped_components) or "unknown"
    if dropped_mass_fraction is None:
        return f"dropped {names}"
    return (
        f"dropped {names} "
        f"(mass_fraction={float(dropped_mass_fraction):.6g})"
    )


def _composition_projected_notice(
    diagnostics: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return dropped-component payload when the adapter flagged a projection.

    Match the structured ``composition_projected`` notice / status reason, not
    the shared VapoRock ``input_composition_projected`` projection reason —
    that token is present on every MAGEMin bulk, including in-basis solves.
    """

    structured = str(diagnostics.get("backend_status_reason") or "")
    projection = diagnostics.get("input_composition_projection")
    notice: Mapping[str, Any] | None = None
    if isinstance(projection, Mapping):
        raw = projection.get(REFUSAL_COMPOSITION_PROJECTED)
        if isinstance(raw, Mapping):
            notice = raw
    if structured != REFUSAL_COMPOSITION_PROJECTED and notice is None:
        return None
    dropped: list[str] = []
    fraction = None
    if notice is not None:
        dropped = [str(name) for name in (notice.get("dropped_components") or [])]
        fraction = notice.get("dropped_mass_fraction")
    if not dropped and isinstance(projection, Mapping):
        dropped = [
            str(name) for name in (projection.get("dropped_bulk_components") or [])
        ]
        if fraction is None:
            fraction = projection.get("dropped_mass_fraction")
    fraction_number = _finite_float(fraction)
    return {
        "dropped_components": dropped,
        "dropped_mass_fraction": fraction_number,
    }


def crash_floor_engine_annotation(
    diagnostics: Mapping[str, Any] | None,
) -> str | None:
    """Copy the adapter floor annotation; never overwrite the typed reason."""
    annotation = (diagnostics or {}).get("engine_reason")
    if annotation == ENGINE_ANNOTATION_CRASH_FLOOR:
        return ENGINE_ANNOTATION_CRASH_FLOOR
    return None


def classify_equilibrate_outcome(
    result: Any | None = None,
    *,
    error: BaseException | None = None,
) -> tuple[str, str | None, str | None]:
    """Return (status, refusal_reason, engine_reason).

    status is ``ok`` or ``refusal``. A refusing engine becomes a typed
    refusal row, never an exception that escapes the battery.
    """

    if error is not None:
        reason_code = str(
            getattr(error, "reason_code", "")
            or getattr(error, "backend_status_reason", "")
            or getattr(error, "code", "")
            or ""
        )
        status_reason = str(getattr(error, "backend_status_reason", "") or "")
        category = str(getattr(error, "backend_failure_category", "") or "")
        message = str(error)
        if status_reason:
            engine_reason = status_reason
        elif reason_code.startswith("imcc_gas_"):
            engine_reason = f"{reason_code}: {message}"
        else:
            engine_reason = reason_code or message
        haystack = _haystack(
            type(error).__name__, reason_code, status_reason, category, message
        )
        if "timeout" in type(error).__name__.lower() or isinstance(error, TimeoutError):
            return "refusal", REFUSAL_TIMEOUT, engine_reason
        if category == REFUSAL_ENGINE_CRASH or reason_code in {
            "subprocess_died",
            REFUSAL_ENGINE_CRASH,
        }:
            return "refusal", REFUSAL_ENGINE_CRASH, engine_reason
        if reason_code in _IMCC_OUT_OF_BASIS_CODES:
            return "refusal", REFUSAL_OUT_OF_BASIS, engine_reason
        if reason_code in {
            "openimcc_oxygen_balance_unavailable",
        } or reason_code.startswith("imcc_gas_"):
            return "refusal", reason_code, engine_reason
        bucket = _bucket_from_text(haystack) or REFUSAL_UNAVAILABLE
        return "refusal", bucket, engine_reason

    if result is None:
        return "refusal", REFUSAL_UNAVAILABLE, "no_result"

    engine_status = str(getattr(result, "status", "") or "")
    diagnostics = dict(getattr(result, "diagnostics", None) or {})
    warnings = list(getattr(result, "warnings", None) or [])
    structured = str(
        diagnostics.get("backend_status_reason")
        or diagnostics.get("empty_speciation_cause")
        or diagnostics.get("backend_failure_reason_code")
        or ""
    )
    category = str(diagnostics.get("backend_failure_category") or "")
    engine_reason = structured or ("; ".join(str(w) for w in warnings) if warnings else engine_status)
    haystack = _haystack(engine_status, structured, category, warnings, diagnostics)
    if (
        category == REFUSAL_ENGINE_CRASH
        or structured in {"subprocess_died", REFUSAL_ENGINE_CRASH}
        or engine_status == REFUSAL_ENGINE_CRASH
    ):
        return "refusal", REFUSAL_ENGINE_CRASH, engine_reason
    projected = _composition_projected_notice(diagnostics)
    if projected is not None:
        return (
            "refusal",
            REFUSAL_COMPOSITION_PROJECTED,
            composition_projected_note(
                projected["dropped_components"],
                projected["dropped_mass_fraction"],
            ),
        )

    timeout_bucket = _bucket_from_text(haystack)
    if engine_status in {"ok", "non_authoritative"}:
        liquid_fraction = _finite_float(getattr(result, "liquid_fraction", None))
        assemblage = getattr(result, "phase_assemblage_available", True)
        if assemblage and liquid_fraction is not None and liquid_fraction <= 0.0:
            return "refusal", REFUSAL_NO_LIQUID, engine_reason or "liquid_fraction<=0"
        return "ok", None, None

    if timeout_bucket == REFUSAL_TIMEOUT or engine_status in {"timeout", "engine_timeout"}:
        return "refusal", REFUSAL_TIMEOUT, engine_reason
    if engine_status in {"unavailable", "not_attempted"}:
        return "refusal", REFUSAL_UNAVAILABLE, engine_reason or engine_status
    if timeout_bucket is not None:
        return "refusal", timeout_bucket, engine_reason
    if engine_status == "out_of_domain":
        return "refusal", REFUSAL_OUT_OF_BASIS, engine_reason or engine_status
    return "refusal", engine_status or REFUSAL_UNAVAILABLE, engine_reason or engine_status


def reclassify_projected_composition_cells(
    cells: Sequence[EquilibrateCell],
    pots: Sequence[Any],
) -> list[EquilibrateCell]:
    """Rewrite MAGEMin ok-cells whose pot drops ig-order components.

    Used when regenerating reports from JSON captured before the adapter
    refused projected bulks. Does not call the MAGEMin binary.
    """

    compositions: dict[str, Mapping[str, float]] = {}
    for pot in pots:
        if isinstance(pot, Mapping):
            pot_id = str(pot.get("pot_id") or "")
            compositions[pot_id] = dict(pot.get("composition_wt_pct") or {})
            continue
        compositions[str(getattr(pot, "pot_id", ""))] = dict(
            getattr(pot, "composition_wt_pct", {}) or {}
        )

    from simulator.melt_backend.base import MeltCompositionError
    from simulator.melt_backend.magemin import MAGEMinBackend

    backend = MAGEMinBackend()
    rewritten: list[EquilibrateCell] = []
    for cell in cells:
        if (
            cell.engine != "magemin"
            or cell.status != "ok"
            or cell.refusal_reason == REFUSAL_COMPOSITION_PROJECTED
        ):
            rewritten.append(cell)
            continue
        composition = compositions.get(cell.pot_id) or {}
        try:
            projection = backend._build_db_bulk_projection(
                composition, database="ig"
            )
        except MeltCompositionError:
            rewritten.append(cell)
            continue
        if not projection.dropped_components:
            rewritten.append(cell)
            continue
        source_sum = float(projection.source_sum_wt_pct)
        fraction = (
            max(0.0, source_sum - float(projection.projected_sum_wt_pct))
            / source_sum
            if source_sum > 0.0
            else 0.0
        )
        note = composition_projected_note(
            projection.dropped_components,
            fraction,
        )
        rewritten.append(
            replace(
                cell,
                status="refusal",
                refusal_reason=REFUSAL_COMPOSITION_PROJECTED,
                engine_status="out_of_domain",
                engine_reason=note,
                melt_activities={},
                melt_activity_coefficients={},
                gas_partial_pressures_Pa={},
            )
        )
    return rewritten


# ---------------------------------------------------------------------------
# IMCC-SF04(+EXT) battery adapter
# ---------------------------------------------------------------------------


class _OxygenBalanceRefusal(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.reason_code = code
        self.backend_status_reason = f"{code}: {detail}"
        super().__init__(self.backend_status_reason)


_CELL_OXIDE_GAS_TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "W": (
        ("WO", "O-027"),
        ("WO2", "O-048"),
        ("WO3", "O-068"),
        ("W2O6", "O-088"),
        ("W3O8", "O-092"),
        ("W3O9", "O-093"),
        ("W4O12", "O-096"),
    ),
    "Mo": (("MoO", "Mo-008"), ("MoO2", "Mo-010"), ("MoO3", "Mo-017")),
}
_CELL_OXIDE_BUFFER_TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "W": (("WO2", "O-047"), ("WO3", "O-065")),
    "Mo": (("MoO2", "Mo-009"),),
}
_CELL_GAS_CONSTANT_J_MOL_K = 8.31446261815324
_CELL_ATOMIC_MASS_G_MOL = {"O": 15.999, "W": 183.84, "Mo": 95.95}


def _cell_janaf_gibbs_points(table_id: str) -> tuple[Any, str]:
    """Load JANAF ΔfG° points through the hash-checked compilation loader."""

    from simulator.battery.generators.janaf import _fusion_table_points

    return _fusion_table_points(table_id)


def _cell_oxide_thermodynamics(
    cell_material: str, temperature_K: float
) -> tuple[dict[str, float], float, dict[str, Any]]:
    """Build shared wall-oxide Kp values and the stable metal/oxide buffer.

    Premise: unit-activity metal forms M_aO_b(g) by
    ``a M(s) + b/2 O2(g) ⇌ M_aO_b(g)``. Thus
    ``p_g/bar = exp(-ΔfG°/(RT)) (pO2/bar)^(b/2)``; JANAF ΔfG° is
    interpolated in kJ/mol, converted to J/mol, and standard pressure is 1 bar.
    Unit check: ΔfG°/(RT) and each pressure ratio are dimensionless. Sanity:
    raising pO2 multiplies every cell-oxide pressure by its positive b/2
    power, so each adds a positive, monotone oxygen-effusion term.
    """

    from simulator.reference_data.janaf import (
        TABLES_DIR,
        formula_composition,
        load_table_document,
        table_formula_normalised_value,
    )
    from simulator.battery.generators.janaf import _interpolate_formation_gibbs

    if cell_material not in _CELL_OXIDE_GAS_TABLES:
        raise _OxygenBalanceRefusal(
            "oxygen_balance_cell_material_invalid",
            f"unknown cell_material {cell_material!r}; expected None, 'W', or 'Mo'",
        )
    T = float(temperature_K)
    if not math.isfinite(T) or T <= 0.0:
        raise _OxygenBalanceRefusal(
            "oxygen_balance_cell_thermodynamics_failed",
            f"temperature must be finite and positive, got {temperature_K!r}",
        )

    def read(table_id: str, expected_formula: str, expected_state: str) -> tuple[float, str]:
        document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
        table = document.get("table") or {}
        entry = table.get("index_entry") or {}
        state = str(entry.get("state") or "")
        formula = table_formula_normalised_value(table)
        if (
            state != expected_state
            or dict(formula_composition(formula) or ())
            != dict(formula_composition(expected_formula) or ())
        ):
            raise ValueError(
                f"{table_id}: expected {expected_formula}({expected_state}), "
                f"found {formula}({state})"
            )
        points, digest = _cell_janaf_gibbs_points(table_id)
        gibbs_kj = _interpolate_formation_gibbs(points, Decimal(str(T)))
        return float(gibbs_kj) * 1000.0, digest

    gas_data: dict[str, tuple[float, float]] = {}
    sources: dict[str, Any] = {}
    try:
        for species, table_id in _CELL_OXIDE_GAS_TABLES[cell_material]:
            formula = species
            atoms = re.findall(r"([A-Z][a-z]?)(\d*)", formula)
            oxygen_atoms = sum(
                int(count or "1") for element, count in atoms if element == "O"
            )
            gibbs_j, digest = read(table_id, formula, "g")
            gas_data[species] = (gibbs_j, float(oxygen_atoms))
            sources[species] = {
                "source_class": "cell_shared_janaf",
                "table_id": table_id,
                "source_sha256": digest,
            }

        buffers: list[tuple[float, str, str, str]] = []
        for formula, table_id in _CELL_OXIDE_BUFFER_TABLES[cell_material]:
            gibbs_j, digest = read(table_id, formula, "cr")
            atoms = re.findall(r"([A-Z][a-z]?)(\d*)", formula)
            oxygen_atoms = sum(
                int(count or "1") for element, count in atoms if element == "O"
            )
            log10_p = (
                2.0 * gibbs_j
                / (oxygen_atoms * _CELL_GAS_CONSTANT_J_MOL_K * T * math.log(10.0))
            )
            buffers.append((log10_p, formula, table_id, digest))
        # The first solid stable as oxygen potential rises is the M/MOx pair
        # with the lowest JANAF coexistence pressure. This checks WO2(cr) vs
        # WO3(cr) at the requested T instead of assuming one phase.
        buffer_log10_bar, buffer_formula, buffer_table, buffer_digest = min(
            buffers, key=lambda row: row[0]
        )
        sources["buffer_phase"] = {
            "source_class": "cell_shared_janaf",
            "formula": buffer_formula,
            "table_id": buffer_table,
            "source_sha256": buffer_digest,
        }
    except Exception as exc:  # noqa: BLE001 - report hash/domain errors as typed refusal
        if isinstance(exc, _OxygenBalanceRefusal):
            raise
        raise _OxygenBalanceRefusal(
            "oxygen_balance_cell_thermodynamics_failed",
            f"cannot load hash-checked JANAF cell-oxide data: {exc}",
        ) from exc

    return gas_data, buffer_log10_bar, sources


def _solve_cell_oxygen_balance(
    pressure_model: Callable[[float], Mapping[str, float]],
    species_metadata: Mapping[str, Any],
    *,
    temperature_K: float,
    cell_material: str,
    oxygen_balance_from_pressure_model: Callable[..., Any],
) -> tuple[float, Mapping[str, float], Mapping[str, Any], float, dict[str, Any]]:
    """Add shared JANAF wall gases to one engine's pressure model and solve."""

    if cell_material not in _CELL_OXIDE_GAS_TABLES:
        raise _OxygenBalanceRefusal(
            "oxygen_balance_cell_material_invalid",
            f"unknown cell_material {cell_material!r}; expected None, 'W', or 'Mo'",
        )
    gas_data, buffer_log10_bar, sources = _cell_oxide_thermodynamics(
        cell_material, temperature_K
    )
    if not species_metadata:
        raise _OxygenBalanceRefusal(
            "imcc_gas_oxygen_balance_failed",
            "engine returned no species metadata for the cell-oxide balance",
        )
    metadata_type = type(next(iter(species_metadata.values())))
    cell_metadata: dict[str, Any] = {}
    for species, (_gibbs_j, oxygen_atoms) in gas_data.items():
        formula_atoms = re.findall(r"([A-Z][a-z]?)(\d*)", species)
        if not formula_atoms or "".join(
            element + count for element, count in formula_atoms
        ) != species:
            raise _OxygenBalanceRefusal(
                "oxygen_balance_cell_thermodynamics_failed",
                f"cannot parse JANAF cell-oxide formula {species!r}",
            )
        try:
            molar_mass = math.fsum(
                _CELL_ATOMIC_MASS_G_MOL[element] * int(count or "1")
                for element, count in formula_atoms
            )
        except KeyError as exc:
            raise _OxygenBalanceRefusal(
                "oxygen_balance_cell_thermodynamics_failed",
                f"no atomic mass for JANAF cell-oxide element {exc.args[0]!r}",
            ) from exc
        cell_metadata[species] = metadata_type(
            molar_mass, oxygen_atoms, 0.0, oxygen_atoms / 2.0
        )
    all_metadata = {**species_metadata, **cell_metadata}

    def combined_pressure_model(logp: float) -> dict[str, float]:
        pressures = dict(pressure_model(logp))
        for species, (gibbs_j, _oxygen_atoms) in gas_data.items():
            log_pressure = (
                -gibbs_j / (_CELL_GAS_CONSTANT_J_MOL_K * temperature_K)
                + (math.log(10.0) * logp)
                * cell_metadata[species].pO2_exponent
            )
            pressures[species] = (
                0.0 if log_pressure < -745.0 else math.exp(log_pressure)
            )
        return pressures

    try:
        pO2_bar, pressures, balance = oxygen_balance_from_pressure_model(
            combined_pressure_model, all_metadata, bracket=(-30.0, 0.0)
        )
    except Exception as exc:  # noqa: BLE001 - preserve engine refusal as typed
        raise _OxygenBalanceRefusal(
            str(getattr(exc, "code", "") or "imcc_gas_oxygen_balance_failed"),
            str(exc),
        ) from exc

    buffer_pinned = math.log10(float(pO2_bar)) > buffer_log10_bar
    if buffer_pinned:
        pO2_bar = 10.0**buffer_log10_bar
        pressures = combined_pressure_model(buffer_log10_bar)
        oxygen_flux = math.fsum(
            row.oxygen_atoms * pressures[name] / math.sqrt(row.molar_mass)
            for name, row in all_metadata.items()
        )
        parent_flux = math.fsum(
            row.parent_oxygen_demand * pressures[name] / math.sqrt(row.molar_mass)
            for name, row in all_metadata.items()
        )
        # Pinned-branch residual = oxygen retained as condensed wall oxide.
        # f(pO2) = oxygen_flux - parent_flux rises monotonically with pO2
        # (sign rule, w*k >= 0) and vanishes at the free root. The buffer
        # pins only when that root lies ABOVE the buffer, so at the buffer
        # f < 0: the melt liberates more O than the effusing gas carries,
        # and the deficit condenses as the buffer oxide (e.g. WO2(cr)) on
        # the cell wall. The residual (parent - oxygen) / parent is therefore
        # the fraction of liberated O the wall retains. It lies in [0, 1)
        # and is 0 only at the free root. Sanity: a Mo cell with 0.5/0.5
        # K2O/SiO2 at 1933 K gives ~0.9996, i.e. almost all O goes to MoO2.
        residual = abs(oxygen_flux - parent_flux) / max(
            oxygen_flux, parent_flux, 1e-300
        )
        balance = {
            **dict(balance),
            "residual": residual,
            "bracket": list(balance["bracket"]),
            "dominant_O_carriers": sorted(
                (
                    (name, row.oxygen_atoms * pressures[name] / math.sqrt(row.molar_mass))
                    for name, row in all_metadata.items()
                    if row.oxygen_atoms > 0
                ),
                key=lambda item: item[1],
                reverse=True,
            )[:5],
            "dominant_metal_carriers": sorted(
                (
                    (name, row.parent_oxygen_demand * pressures[name] / math.sqrt(row.molar_mass))
                    for name, row in all_metadata.items()
                    if row.parent_oxygen_demand > 0
                ),
                key=lambda item: item[1],
                reverse=True,
            )[:5],
        }

    total_oxygen_flux = math.fsum(
        row.oxygen_atoms * pressures[name] / math.sqrt(row.molar_mass)
        for name, row in all_metadata.items()
    )
    cell_oxygen_flux = math.fsum(
        cell_metadata[name].oxygen_atoms
        * pressures[name]
        / math.sqrt(cell_metadata[name].molar_mass)
        for name in cell_metadata
    )
    fraction = cell_oxygen_flux / total_oxygen_flux if total_oxygen_flux else 0.0
    info = {
        "cell_material": cell_material,
        "cell_oxide_flux_fraction": fraction,
        "buffer_pinned": buffer_pinned,
        "buffer_pO2_bar": 10.0**buffer_log10_bar,
        "cell_oxide_janaf_sources": sources,
    }
    return float(pO2_bar), pressures, balance, fraction, info


# ---------------------------------------------------------------------------
# openimcc battery adapter
# ---------------------------------------------------------------------------


class _OpenImccBatteryBackend:
    """Battery-only producer for the optional openimcc package.

    The melt rail goes through the C1 bridge.  The vapour rail deliberately
    calls openimcc's gas layer directly, so it does not use the simulator's
    VapoRock-backed JANAF tables.
    """

    supports_intrinsic_fO2 = False

    def __init__(self, engine_name: str) -> None:
        if engine_name not in OPENIMCC_MODEL_IDS:
            raise BinaryPotBatteryError(f"unknown openimcc engine {engine_name!r}")
        self.engine_name = engine_name
        self.model_id = OPENIMCC_MODEL_IDS[engine_name]
        from simulator.melt_backend import openimcc_bridge

        self._bridge = openimcc_bridge
        self._package = openimcc_bridge._require_openimcc()
        self._pack_name = "v1.0.2"
        self._pack = openimcc_bridge._load_pack(self._pack_name)
        self._gas: Any = None
        self._gas_error: str | None = None
        self._identity: dict[str, Any] = {
            "name": self.model_id,
            "model_id": self.model_id,
            "version": str(getattr(self._package, "__version__", "0+unknown")),
            "pack": self._pack_name,
            "pack_version": str(getattr(self._pack, "version", self._pack_name)),
            "pack_digest": str(openimcc_bridge._pack_digest(self._pack)),
        }
        self._load_gas()

    def _load_gas(self) -> None:
        try:
            from openimcc import load_gas_datapack

            self._gas = load_gas_datapack()
        except Exception as exc:  # noqa: BLE001 - melt rail remains usable
            self._gas = None
            self._gas_error = f"{type(exc).__name__}: {exc}"
            return
        self._identity["engine_binding_identity"] = (
            self._bridge.engine_binding_identity(self._pack, self._gas)
        )
        self._gas_error = None

    def equilibrate(
        self,
        temperature_C: float,
        composition_kg: Mapping[str, float] | None = None,
        fO2_log: float | None = None,
        pressure_bar: float = 1.0e-6,
        *,
        composition_mol: Mapping[str, float] | None = None,
        po2_request: Po2Request | None = None,
        **_unused: object,
    ) -> Any:
        from types import SimpleNamespace

        del pressure_bar
        temperature_K = float(temperature_C) + CELSIUS_TO_KELVIN_OFFSET
        bridge_kwargs: dict[str, Any] = {
            "temperature_K": temperature_K,
            "pack": self._pack_name,
            "allow_extrapolation": True,
            "allow_out_of_envelope": True,
        }
        if composition_mol:
            bridge_kwargs["composition_mol"] = composition_mol
        elif composition_kg is not None:
            bridge_kwargs["composition_kg"] = composition_kg
        else:
            bridge_kwargs["composition_mol"] = composition_mol
        result = self._bridge.evaluate(**bridge_kwargs)
        self._identity["pack_version"] = result.pack_version
        self._identity["pack_digest"] = result.pack_digest

        notices: list[dict[str, Any]] = []
        authority: str | None = None
        outside_domain = bool(
            result.extrapolated or result.envelope_status != "inside"
        )
        for flag in result.flags:
            notices.append(
                {
                    "kind": "openimcc_flag",
                    "authority": AUTHORITY_EXTRAPOLATED if outside_domain else None,
                    "reason": str(flag),
                }
            )
            if outside_domain:
                authority = AUTHORITY_EXTRAPOLATED
        from simulator.melt_backend.openimcc_bridge import (
            imcc_complex_saturation_notices,
        )

        notices.extend(
            imcc_complex_saturation_notices(
                result.flags,
                result.acid_sink_ratio,
                result.parent_oxide_x_star_ratios,
            )
        )
        if result.extrapolated:
            notices.append(
                {
                    "kind": "openimcc_temperature_extrapolated",
                    "authority": AUTHORITY_EXTRAPOLATED,
                    "reason": "T outside openimcc datapack domain; evaluate(allow_extrapolation=True)",
                }
            )
            authority = AUTHORITY_EXTRAPOLATED
        if result.envelope_status != "inside":
            notices.append(
                {
                    "kind": "openimcc_composition_outside_validated_envelope",
                    "authority": AUTHORITY_EXTRAPOLATED,
                    "reason": "X_Me2O outside openimcc validated envelope; evaluate(allow_out_of_envelope=True)",
                }
            )
            authority = AUTHORITY_EXTRAPOLATED
        for notice in result.notices:
            notices.append(
                {
                    "kind": "openimcc_notice",
                    "authority": None,
                    "reason": str(notice),
                }
            )

        pressures: dict[str, float] = {}
        vapor_sources: dict[str, str] = {}
        gas_diagnostics: dict[str, Any] = {}
        engine_binding_identity = self._identity.get("engine_binding_identity") or {}
        gas_table_lineage = (
            "openimcc-gas-table:sha256:"
            f"{engine_binding_identity.get('gas_table_digest', '')}"
        )
        if (
            po2_request is not None
            and po2_request.mode == PO2_OXYGEN_BALANCE_EFFUSION
        ):
            cell_material = po2_request.cell_material
            if self._gas is None:
                raise _OxygenBalanceRefusal(
                    "openimcc_oxygen_balance_unavailable",
                    "openimcc gas datapack unavailable; remedy: install the "
                    f"solver-enabled openimcc pin {self._bridge.OPENIMCC_RECORDED_PIN}",
                )
            if cell_material is None:
                from simulator.melt_backend.openimcc_bridge import (
                    evaluate_gas_oxygen_balance,
                )

                po2_bar, gas_result, balance = evaluate_gas_oxygen_balance(
                    result.parent_oxide_activities,
                    temperature_K,
                    self._gas,
                    parent_oxides=result.parent_oxides,
                )
                cell_fraction = 0.0
                cell_info = {
                    "cell_material": None,
                    "cell_oxide_flux_fraction": 0.0,
                    "buffer_pinned": False,
                    "buffer_pO2_bar": None,
                }
                gas_pressures = dict(gas_result)
            else:
                try:
                    from openimcc import (
                        evaluate_gas,
                        oxygen_balance_from_pressure_model,
                        oxygen_balance_species_metadata,
                    )
                except (ImportError, AttributeError) as exc:
                    raise _OxygenBalanceRefusal(
                        "openimcc_oxygen_balance_unavailable",
                        "installed openimcc does not expose the generic pressure "
                        "model and oxygen-balance core needed for a reactive cell",
                    ) from exc

                channels, _omission_notices = (
                    _openimcc_gas_channels_and_omission_notices(
                        result.parent_oxides, self._gas
                    )
                )
                base_species = oxygen_balance_species_metadata(
                    {name: parent or None for name, (parent, _ng, _no2) in channels}
                )
                for name, (parent, n_gas, n_o2) in channels:
                    expected = -n_o2 / n_gas if parent else (
                        1.0 if name == "O2" else 0.5
                    )
                    if not math.isclose(
                        base_species[name].pO2_exponent, expected, abs_tol=1e-12
                    ):
                        raise _OxygenBalanceRefusal(
                            "imcc_gas_oxygen_balance_failed",
                            f"openimcc reaction exponent for {name!r} "
                            "does not match formula metadata",
                        )

                def pressure_model(logp: float) -> Mapping[str, float]:
                    return evaluate_gas(
                        result.parent_oxide_activities,
                        temperature_K,
                        10.0**logp,
                        self._gas,
                        parent_oxides=result.parent_oxides,
                        allow_extrapolation=True,
                    )

                po2_bar, all_gas, balance, cell_fraction, cell_info = (
                    _solve_cell_oxygen_balance(
                        pressure_model,
                        base_species,
                        temperature_K=temperature_K,
                        cell_material=cell_material,
                        oxygen_balance_from_pressure_model=(
                            oxygen_balance_from_pressure_model
                        ),
                    )
                )
                gas_result = evaluate_gas(
                    result.parent_oxide_activities,
                    temperature_K,
                    po2_bar,
                    self._gas,
                    parent_oxides=result.parent_oxides,
                    allow_extrapolation=True,
                )
                gas_pressures = dict(gas_result)
                gas_pressures.update(
                    {name: all_gas[name] for name in cell_info["cell_oxide_janaf_sources"] if name != "buffer_phase"}
                )
            _channels, omission_notices = (
                _openimcc_gas_channels_and_omission_notices(
                    result.parent_oxides, self._gas, gas_result
                )
            )
            notices.extend(omission_notices)
            gas_diagnostics = {
                "domain_flags": dict(getattr(gas_result, "domain_flags", {})),
                "provenance_class": dict(getattr(gas_result, "provenance_class", {})),
                "oxygen_balance": dict(balance),
            }
            solved_notice = {
                    "kind": "fo2_oxygen_balance_effusion_solved",
                    "pO2_bar": float(po2_bar),
                    "relative_residual": float(balance["residual"]),
                    "bracket_log10_bar": list(balance["bracket"]),
                    "dominant_O_carriers": list(balance["dominant_O_carriers"]),
                    "dominant_metal_carriers": list(
                        balance["dominant_metal_carriers"]
                    ),
                    "cell_material": cell_material,
                    "cell_oxide_flux_fraction": float(cell_fraction),
                    "buffer_pinned": bool(cell_info["buffer_pinned"]),
                    "buffer_pO2_bar": cell_info["buffer_pO2_bar"],
                }
            if cell_material is not None:
                solved_notice["cell_oxide_janaf_sources"] = cell_info[
                    "cell_oxide_janaf_sources"
                ]
            notices.append(solved_notice)
            for name, value in gas_pressures.items():
                number = _finite_float(value)
                if number is None or number <= 0.0:
                    continue
                pressures[str(name)] = number * PA_PER_BAR
                if cell_material is not None and name in cell_info[
                    "cell_oxide_janaf_sources"
                ]:
                    source = cell_info["cell_oxide_janaf_sources"][name]
                    vapor_sources[str(name)] = (
                        f"cell_shared_janaf:{source['table_id']}:"
                        f"{source['source_sha256']}"
                    )
                else:
                    vapor_sources[str(name)] = gas_table_lineage
            for name, flag in getattr(gas_result, "domain_flags", {}).items():
                if flag:
                    notices.append(
                        {
                            "kind": "openimcc_gas_flag",
                            "authority": AUTHORITY_EXTRAPOLATED,
                            "species": str(name),
                            "reason": str(flag),
                        }
                    )
                    authority = AUTHORITY_EXTRAPOLATED
        elif fO2_log is not None:
            if self._gas is None:
                notices.append(
                    {
                        "kind": "openimcc_gas_unavailable",
                        "authority": None,
                        "reason": self._gas_error or "openimcc gas datapack unavailable",
                    }
                )
            else:
                from openimcc import evaluate_gas

                fo2_bar = 10.0 ** float(fO2_log)
                gas_result = evaluate_gas(
                    result.parent_oxide_activities,
                    temperature_K,
                    fo2_bar,
                    self._gas,
                    parent_oxides=result.parent_oxides,
                    allow_extrapolation=True,
                )
                _channels, omission_notices = (
                    _openimcc_gas_channels_and_omission_notices(
                        result.parent_oxides, self._gas, gas_result
                    )
                )
                notices.extend(omission_notices)
                gas_diagnostics = {
                    "domain_flags": dict(gas_result.domain_flags),
                    "provenance_class": dict(gas_result.provenance_class),
                }
                for name, value in dict(gas_result).items():
                    number = _finite_float(value)
                    if number is None or number <= 0.0:
                        continue
                    pressures[str(name)] = number * PA_PER_BAR
                    vapor_sources[str(name)] = gas_table_lineage
                for name, flag in gas_result.domain_flags.items():
                    if flag:
                        notices.append(
                            {
                                "kind": "openimcc_gas_flag",
                                "authority": AUTHORITY_EXTRAPOLATED,
                                "species": str(name),
                                "reason": str(flag),
                            }
                        )
                        authority = AUTHORITY_EXTRAPOLATED

        provenance = {
            "package_version": result.openimcc_version,
            "pack": result.pack_version,
            "pack_digest": result.pack_digest,
        }
        if "engine_binding_identity" in self._identity:
            provenance["engine_binding_identity"] = self._identity[
                "engine_binding_identity"
            ]
        diagnostics = {
            "imcc_model_id": result.pack_model_id,
            "imcc_datapack_version": result.pack_version,
            "imcc_notices": notices,
            "authority": authority,
            "openimcc_provenance": provenance,
            "openimcc_gas": gas_diagnostics,
            "vapor_pressure_backend_status": "openimcc",
            "authoritative_for_requested_vapor_pressure": True,
        }
        reported_gammas = {
            str(name): float(row["value"])
            for name, row in result.activity_coefficients.items()
        }
        return SimpleNamespace(
            status="ok",
            diagnostics=diagnostics,
            warnings=[],
            activity_coefficients=dict(result.parent_oxide_activities),
            reported_activity_coefficients=reported_gammas,
            activity_coefficient_details={
                str(name): dict(row)
                for name, row in result.activity_coefficients.items()
            },
            vapor_pressures_Pa=pressures,
            vapor_pressures_source=vapor_sources,
            vapor_pressure_backend_status="openimcc",
            authoritative_for_requested_vapor_pressure=True,
            liquid_fraction=1.0,
            phase_assemblage_available=True,
            imcc_notices=notices,
            imcc_model_id=result.pack_model_id,
        )


_INTERNAL_ANALYTICAL_VAPOR_PRESSURE_PATH = (
    "PyrolysisSimulator._refresh_vapor_pressures_from_kernel"
    " -> PyrolysisSimulator._dispatch_only(ChemistryIntent.VAPOR_PRESSURE)"
    " -> BuiltinVaporPressureProvider"
)


def _new_internal_analytical_core() -> Any:
    """Build the simulator core with its product VAPOR_PRESSURE provider."""

    from simulator.core import PyrolysisSimulator
    from simulator.melt_backend.base import InternalAnalyticalBackend

    def load_data(name: str) -> Any:
        return load_cached_safe_yaml(
            (REPO_ROOT / "data" / name).read_text(encoding="utf-8")
        ) or {}

    return PyrolysisSimulator(
        InternalAnalyticalBackend(),
        load_data("setpoints.yaml"),
        {},
        load_data("vapor_pressures.yaml"),
    )


def _internal_analytical_vapor_pressure_adapter(
    *,
    core: Any,
    temperature_C: float,
    pressure_bar: float,
    composition_kg: Mapping[str, float] | None,
    composition_mol: Mapping[str, float] | None,
    fO2_log: float | None,
    po2_request: Po2Request | None,
    oxygen_balance_backend: Any | None = None,
    include_diagnostic_shadows: bool = True,
) -> Any:
    """Evaluate a battery observation through the simulator VAPOR_PRESSURE intent.

    The producer supplies only its observation-derived oxide inventory,
    temperature, pressure and existing battery oxygen-condition request.
    """

    from types import SimpleNamespace
    from simulator.state import Atmosphere

    if not composition_kg and not composition_mol:
        raise _InternalAnalyticalInputRefusal(
            1,
            "internal_analytical_missing_composition",
            "observation carries no usable oxide composition",
        )

    temperature = _finite_float(temperature_C)
    if (
        isinstance(temperature_C, bool)
        or temperature is None
        or temperature + CELSIUS_TO_KELVIN_OFFSET <= 0.0
    ):
        raise _InternalAnalyticalInputRefusal(
            2,
            "internal_analytical_invalid_temperature",
            f"temperature_C must be finite and above absolute zero; got {temperature_C!r}",
        )
    pressure = _finite_float(pressure_bar)
    if isinstance(pressure_bar, bool) or pressure is None or pressure < 0.0:
        raise _InternalAnalyticalInputRefusal(
            2,
            "internal_analytical_invalid_total_pressure",
            f"pressure_bar must be finite and non-negative; got {pressure_bar!r}",
        )

    melt_composition_kg: dict[str, float] = {}
    if composition_kg:
        for species, raw in composition_kg.items():
            value = _finite_float(raw)
            if isinstance(raw, bool) or value is None or value < 0.0:
                raise _InternalAnalyticalInputRefusal(
                    2,
                    "internal_analytical_invalid_composition",
                    f"oxide mass for {species!r} must be finite and non-negative; got {raw!r}",
                )
            if value > 0.0:
                melt_composition_kg[str(species)] = value
    else:
        from simulator.accounting.formulas import resolve_species_formula

        for species, raw in (composition_mol or {}).items():
            amount = _finite_float(raw)
            if isinstance(raw, bool) or amount is None or amount < 0.0:
                raise _InternalAnalyticalInputRefusal(
                    2,
                    "internal_analytical_invalid_composition",
                    f"oxide amount for {species!r} must be finite and non-negative; got {raw!r}",
                )
            if amount > 0.0:
                try:
                    formula = resolve_species_formula(
                        str(species), core.species_formula_registry
                    )
                    mass_kg = amount * formula.molar_mass_kg_per_mol()
                except Exception as exc:  # noqa: BLE001 - invalid oxide input
                    raise _InternalAnalyticalInputRefusal(
                        2,
                        "internal_analytical_invalid_composition",
                        f"oxide formula for {species!r} is invalid: {exc}",
                    ) from exc
                if not math.isfinite(mass_kg) or mass_kg <= 0.0:
                    raise _InternalAnalyticalInputRefusal(
                        2,
                        "internal_analytical_invalid_composition",
                        f"oxide amount for {species!r} does not yield a positive finite mass",
                    )
                melt_composition_kg[str(species)] = mass_kg
    if not melt_composition_kg:
        raise _InternalAnalyticalInputRefusal(
            2,
            "internal_analytical_invalid_composition",
            "oxide composition has no finite positive amount",
        )

    if po2_request is None:
        raise _InternalAnalyticalInputRefusal(
            1,
            "internal_analytical_missing_oxygen_condition",
            "observation carries no oxygen condition",
        )
    oxygen_notices: list[dict[str, Any]] = []
    if po2_request.mode == PO2_COMMANDED:
        po2_bar = _finite_float(po2_request.po2_bar)
        if (
            isinstance(po2_request.po2_bar, bool)
            or po2_bar is None
            or po2_bar <= 0.0
        ):
            raise _InternalAnalyticalInputRefusal(
                2,
                "internal_analytical_invalid_oxygen_condition",
                f"commanded pO2 must be finite and positive; got {po2_request.po2_bar!r}",
            )
        oxygen_source = "battery_observation_commanded_pO2"
    elif po2_request.mode == PO2_OXYGEN_BALANCE_EFFUSION:
        if oxygen_balance_backend is None:
            oxygen_balance_backend = _OpenImccBatteryBackend("openimcc")
        try:
            oxygen_result = oxygen_balance_backend.equilibrate(
                temperature_C=temperature,
                composition_kg=composition_kg,
                composition_mol=composition_mol,
                fO2_log=None,
                pressure_bar=pressure,
                po2_request=po2_request,
            )
        except Exception as exc:  # noqa: BLE001 - keep the input refusal typed
            reason_code = str(
                getattr(exc, "reason_code", "") or getattr(exc, "code", "")
            )
            if reason_code:
                raise _InternalAnalyticalInputRefusal(
                    1,
                    "internal_analytical_oxygen_balance_unavailable",
                    f"cell oxygen condition could not be derived: {reason_code}: {exc}",
                ) from exc
            raise
        solved = next(
            (
                dict(row)
                for row in (oxygen_result.diagnostics or {}).get("imcc_notices", ())
                if isinstance(row, Mapping)
                and row.get("kind") == "fo2_oxygen_balance_effusion_solved"
            ),
            None,
        )
        po2_bar = None if solved is None else _finite_float(solved.get("pO2_bar"))
        if oxygen_result.status != "ok" or po2_bar is None or po2_bar <= 0.0:
            raise _InternalAnalyticalInputRefusal(
                1,
                "internal_analytical_oxygen_balance_unavailable",
                "the existing battery oxygen-balance convention did not produce a positive pO2",
            )
        oxygen_source = "openimcc_oxygen_balance_condition_only"
        oxygen_notices.append(
            {
                **solved,
                "oxygen_condition_source": oxygen_source,
                "pressure_prediction_source": "internal-analytical core",
            }
        )
    else:
        raise _InternalAnalyticalInputRefusal(
            1,
            "internal_analytical_missing_oxygen_condition",
            f"oxygen condition is undefined for request mode {po2_request.mode!r}",
        )

    # Score maps the battery's fugacity in Pa to pO2 in bar using the stated
    # ideal-gas assumption. The effusion branch uses the already-derived pO2.
    fO2_log_used = math.log10(po2_bar)

    core.atom_ledger = core._new_atom_ledger()
    try:
        core._load_ledger_account(
            "process.cleaned_melt",
            melt_composition_kg,
            source="battery observation composition",
        )
    except Exception as exc:  # noqa: BLE001 - malformed oxides are category 2
        raise _InternalAnalyticalInputRefusal(
            2,
            "internal_analytical_invalid_composition",
            f"oxide composition cannot seed the simulator melt: {exc}",
        ) from exc
    core.melt.composition_kg = dict(melt_composition_kg)
    core.melt.update_total_mass()
    core.melt.temperature_C = temperature
    core.melt.p_total_mbar = pressure * 1000.0
    core.melt.atmosphere = Atmosphere.CONTROLLED_O2
    core.melt.pO2_mbar = po2_bar * 1000.0
    core.melt.fO2_log = fO2_log_used
    core.melt.melt_fO2_log = fO2_log_used
    core.melt.oxygen_reservoir.melt_intrinsic_fO2_log = fO2_log_used
    core.melt.oxygen_reservoir.headspace_transport_pO2_bar = po2_bar
    core.melt.oxygen_reservoir.headspace_ledger_pO2_bar = po2_bar
    core._chem_kernel = core._build_chemistry_kernel()

    equilibrium = SimpleNamespace(
        vapor_pressures_Pa={},
        vapor_pressures_source={},
        liquid_fraction=None,
    )
    if include_diagnostic_shadows:
        core._refresh_vapor_pressures_from_kernel(equilibrium)
    else:
        core._refresh_vapor_pressures_from_kernel(
            equilibrium,
            include_diagnostic_shadows=False,
        )
    core_diagnostic = dict(core._last_vapor_pressure_diagnostic)
    core_flags: list[dict[str, Any]] = []
    for field_name in (
        "extrapolated_beyond_valid_range_K",
        "ellingham_extrapolated_beyond_fit_range_K",
    ):
        field = core_diagnostic.get(field_name)
        if isinstance(field, Mapping):
            for species, detail in field.items():
                core_flags.append(
                    {
                        "kind": "out_of_certified_band",
                        "authority": AUTHORITY_EXTRAPOLATED,
                        "reason": f"{field_name}:{species}:{detail}",
                        "band": str(detail),
                    }
                )
    for authority_field in ("vapor_pressure_authority", "ellingham_authority"):
        authority = core_diagnostic.get(authority_field)
        limits = (
            authority.get("authority_limits")
            if isinstance(authority, Mapping)
            else None
        )
        if isinstance(limits, Mapping):
            for species, detail in limits.items():
                core_flags.append(
                    {
                        "kind": "out_of_certified_band",
                        "authority": AUTHORITY_EXTRAPOLATED,
                        "reason": f"{authority_field}:{species}:{detail}",
                        "band": str(detail),
                    }
                )
    floor_notices = core_diagnostic.get("pO2_floor_inversion_notices_by_species")
    if isinstance(floor_notices, Mapping):
        for species, detail in floor_notices.items():
            core_flags.append(
                {
                    "kind": "floor_inversion",
                    "authority": AUTHORITY_EXTRAPOLATED,
                    "reason": f"{_FLOOR_INVERSION_REASON}:{species}:{detail}",
                }
            )

    vapor_authority = core_diagnostic.get("vapor_pressure_authority")
    authority_status = (
        str(vapor_authority.get("status") or "unknown")
        if isinstance(vapor_authority, Mapping)
        else "unknown"
    )
    authority_is_authoritative = authority_status == "authoritative"
    provenance = {
        "code_path": _INTERNAL_ANALYTICAL_VAPOR_PRESSURE_PATH,
        "authority": authority_status,
        "vapor_pressures_source": dict(equilibrium.vapor_pressures_source),
        "core_flags": core_flags,
        "oxygen_condition_source": oxygen_source,
        "oxygen_balance": oxygen_notices,
    }
    diagnostics = {
        "internal_analytical_provenance": provenance,
        "vapor_pressure_backend_status": (
            "builtin_authoritative" if authority_is_authoritative else authority_status
        ),
        "vapor_pressure_backend_status_reason": json.dumps(
            provenance, sort_keys=True, default=str, separators=(",", ":")
        ),
        "authoritative_for_requested_vapor_pressure": authority_is_authoritative,
        "authority": (
            AUTHORITY_EXTRAPOLATED
            if core_flags
            else "bridge" if authority_is_authoritative else authority_status
        ),
        "vapor_pressure_diagnostic": core_diagnostic,
        "imcc_notices": oxygen_notices,
    }
    return SimpleNamespace(
        status="ok",
        diagnostics=diagnostics,
        warnings=[],
        activity_coefficients=dict(core_diagnostic.get("activities") or {}),
        vapor_pressures_Pa=dict(equilibrium.vapor_pressures_Pa or {}),
        vapor_pressures_source=dict(equilibrium.vapor_pressures_source or {}),
        vapor_pressure_backend_status=diagnostics["vapor_pressure_backend_status"],
        authoritative_for_requested_vapor_pressure=authority_is_authoritative,
        liquid_fraction=None,
        phase_assemblage_available=False,
    )


class _InternalAnalyticalBatteryBackend:
    """Battery adapter for the simulator core's own analytical vapor path."""

    supports_intrinsic_fo2 = True

    def __init__(self) -> None:
        self._core = _new_internal_analytical_core()
        self._oxygen_balance_backend: Any | None = None

    def equilibrate(
        self,
        temperature_C: float,
        composition_kg: Mapping[str, float] | None = None,
        fO2_log: float | None = None,
        pressure_bar: float = 1.0e-6,
        *,
        composition_mol: Mapping[str, float] | None = None,
        po2_request: Po2Request | None = None,
        **_unused: object,
    ) -> Any:
        if (
            po2_request is not None
            and po2_request.mode == PO2_OXYGEN_BALANCE_EFFUSION
            and self._oxygen_balance_backend is None
        ):
            self._oxygen_balance_backend = _OpenImccBatteryBackend("openimcc")
        return _internal_analytical_vapor_pressure_adapter(
            core=self._core,
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            composition_kg=composition_kg,
            composition_mol=composition_mol,
            fO2_log=fO2_log,
            po2_request=po2_request,
            oxygen_balance_backend=self._oxygen_balance_backend,
        )


# ---------------------------------------------------------------------------
# Qualification (out-of-domain) MELTS arm
# ---------------------------------------------------------------------------


def melts_certified_band() -> dict[str, Any]:
    """Published MELTS SiO2 + T band as the adapter/spec actually states it.

    SiO2: ``engines/alphamelts/domain.py``
    ``DEFAULT_SILICATE_NETWORK_BAND_WT_PCT`` = [30, 80] wt%, with measured
    crash floor 34.0 wt% (``_SIO2_CRASH_FLOOR_WT_PCT``). Adapter
    ``AlphaMELTSBackend._domain_gate`` uses the same 30–80 window.

    Temperature: subprocess floor
    ``ALPHAMELTS_SUBPROCESS_MIN_TEMPERATURE_C`` = 800 °C (1073.15 K) in
    ``simulator/melt_backend/alphamelts.py``; calibration ceiling
    ``T_calib_max_K`` = 1700 K in
    ``simulator/melt_backend/melt_envelope.py``
    ``MELT_ENVELOPE_CONSTANTS['MELTS-v1.0']`` (HT1-audit conservative
    top of pMELTS/rhyolite-MELTS liquid calibration).
    """

    from engines.alphamelts.domain import (
        DEFAULT_SILICATE_NETWORK_BAND_WT_PCT,
        _SIO2_CRASH_FLOOR_WT_PCT,
    )
    from simulator.melt_backend.alphamelts import (
        ALPHAMELTS_SUBPROCESS_MIN_TEMPERATURE_C,
    )
    from simulator.melt_backend.melt_envelope import MELT_ENVELOPE_CONSTANTS

    t_min_k = float(ALPHAMELTS_SUBPROCESS_MIN_TEMPERATURE_C) + CELSIUS_TO_KELVIN_OFFSET
    t_max_k = float(MELT_ENVELOPE_CONSTANTS["MELTS-v1.0"]["T_calib_max_K"])
    sio2_min, sio2_max = DEFAULT_SILICATE_NETWORK_BAND_WT_PCT
    return {
        "sio2_wt_pct": [float(sio2_min), float(sio2_max)],
        "temperature_K": [t_min_k, t_max_k],
        "sio2_crash_floor_wt_pct": float(_SIO2_CRASH_FLOOR_WT_PCT),
        "citations": {
            "sio2_band": (
                "engines/alphamelts/domain.py "
                "DEFAULT_SILICATE_NETWORK_BAND_WT_PCT"
            ),
            "sio2_crash_floor_wt_pct": (
                "engines/alphamelts/domain.py _SIO2_CRASH_FLOOR_WT_PCT"
            ),
            "T_min_C": (
                "simulator/melt_backend/alphamelts.py "
                "ALPHAMELTS_SUBPROCESS_MIN_TEMPERATURE_C"
            ),
            "T_max_K": (
                "simulator/melt_backend/melt_envelope.py "
                "MELT_ENVELOPE_CONSTANTS['MELTS-v1.0']['T_calib_max_K']"
            ),
        },
    }


def assess_qualification_gate(
    composition_wt_pct: Mapping[str, float],
    temperature_K: float,
) -> dict[str, Any]:
    """Record the MELTS domain-gate verdict without refusing the call."""

    from engines.alphamelts.domain import AlphaMELTSDomainGate

    band = melts_certified_band()
    assessment = AlphaMELTSDomainGate.assess(composition_wt_pct)
    t_min, t_max = band["temperature_K"]
    t_value = float(temperature_K)
    t_failed = t_value < float(t_min) or t_value > float(t_max)
    warnings = list(assessment.warnings)
    failed = list(assessment.failed_constraints)
    if t_failed:
        failed.append("temperature_range")
        warnings.append(
            f"temperature {t_value:g} K outside published MELTS band "
            f"[{t_min:g}, {t_max:g}] K"
        )
    sio2 = float(composition_wt_pct.get("SiO2", 0.0) or 0.0)
    crash_floor = float(band["sio2_crash_floor_wt_pct"])
    below_crash_floor = sio2 < crash_floor
    if below_crash_floor and "silicate_network_band" not in failed:
        # 30–34 wt% sliver: default band admits it; crash floor does not.
        warnings.append(
            f"SiO2 = {sio2:.3f} wt% is below the measured alphaMELTS "
            f"crash floor {crash_floor:g} wt%"
        )
    valid = bool(assessment.valid) and not t_failed
    return {
        "valid": valid,
        "warnings": warnings,
        "reason": assessment.reason,
        "failed_constraints": tuple(failed),
        "silicate_network_band_wt_pct": list(assessment.silicate_network_band_wt_pct),
        "temperature_K": t_value,
        "temperature_in_band": not t_failed,
        "sio2_wt_pct": sio2,
        "below_crash_floor": below_crash_floor,
        "authority": AUTHORITY_EXTRAPOLATED,
        "certified_band": band,
    }


def qualification_notice(gate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "melts_domain_gate",
        "authority": AUTHORITY_EXTRAPOLATED,
        "certified_band": dict(gate.get("certified_band") or {}),
        "gate_valid": bool(gate.get("valid")),
        "gate_reason": gate.get("reason"),
        "failed_constraints": list(gate.get("failed_constraints") or ()),
        "warnings": list(gate.get("warnings") or ()),
        "temperature_in_band": bool(gate.get("temperature_in_band")),
        "below_crash_floor": bool(gate.get("below_crash_floor")),
        "sio2_wt_pct": gate.get("sio2_wt_pct"),
        "run_anyway": True,
        "bypassable_from_harness": [
            "AlphaMELTSBackend._domain_gate / engines.alphamelts.domain.AlphaMELTSDomainGate (SiO2 band, major-oxide sum, oxide basis)"
        ],
        "not_bypassable_from_harness": [
            "ALPHAMELTS_SUBPROCESS_MIN_TEMPERATURE_C (certified T min; notice+run, not a pre-launch refusal)",
            "ALPHAMELTS_SUBPROCESS_MIN_PRESSURE_BAR (1 bar inline in _equilibrate_subprocess)",
            "fe_free_and_imposed_absolute_fo2 (Na2O/K2O-SiO2 family; not _domain_gate)",
        ],
    }


def scale_feo_mgo_for_sio2(
    sio2_wt_pct: float,
    source: Mapping[str, float],
) -> dict[str, float]:
    """Scale the FeO:MgO remainder of ``feo_mgo_sio2_30_20_50`` to a new SiO2.

    The named source pot has no CaO; the brief's "FeO-MgO-CaO" remainder
    is the FeO:MgO pair of that pot. Algebra: remainder = 100 − SiO2;
    FeO:MgO stays 30:20 of the original 50 wt% non-silica.
    """

    sio2 = float(sio2_wt_pct)
    if not math.isfinite(sio2) or sio2 < 0.0 or sio2 > 100.0:
        raise BinaryPotBatteryError(f"qualification SiO2 {sio2_wt_pct!r} is not in [0, 100]")
    feo = float(source.get("FeO") or 0.0)
    mgo = float(source.get("MgO") or 0.0)
    pair = feo + mgo
    if pair <= 0.0:
        raise BinaryPotBatteryError("qualification source pot has no FeO+MgO to scale")
    remainder = 100.0 - sio2
    composition = {
        "SiO2": sio2,
        "FeO": remainder * feo / pair,
        "MgO": remainder * mgo / pair,
    }
    total = sum(composition.values())
    if not math.isclose(total, 100.0, rel_tol=0.0, abs_tol=_WT_PCT_SUM_TOLERANCE):
        composition = {k: 100.0 * v / total for k, v in composition.items()}
    return composition


def qualification_sio2_sweep_pots(
    source: BinaryPot | None = None,
) -> tuple[BinaryPot, ...]:
    if source is None:
        loaded, _grid = load_binary_pots()
        source = next(
            pot for pot in loaded if pot.pot_id == QUALIFICATION_SOURCE_POT_ID
        )
    pots_out: list[BinaryPot] = []
    for sio2 in QUALIFICATION_SIO2_SWEEP_WT_PCT:
        tag = f"{sio2:g}".replace(".", "p")
        pots_out.append(
            BinaryPot(
                pot_id=f"qual_sio2_{tag}_feo_mgo",
                kato_1993_table4_system=source.kato_1993_table4_system,
                why=(
                    f"QUALIFICATION SiO2 sweep at {sio2:g} wt%; FeO:MgO scaled "
                    f"from {QUALIFICATION_SOURCE_POT_ID} (no CaO in that pot)."
                ),
                composition_wt_pct=scale_feo_mgo_for_sio2(
                    sio2, source.composition_wt_pct
                ),
            )
        )
    return tuple(pots_out)


def _cell_identity_key(cell: EquilibrateCell | Mapping[str, Any]) -> tuple[Any, ...]:
    payload = cell.as_payload() if isinstance(cell, EquilibrateCell) else dict(cell)
    po2 = payload.get("po2") or {}
    return (
        str(payload.get("pot_id") or ""),
        str(payload.get("engine") or ""),
        float(payload.get("temperature_K") or 0.0),
        str(po2.get("mode") or ""),
        None if po2.get("po2_bar") is None else float(po2.get("po2_bar")),
        str(po2.get("cell_material") or ""),
        str(payload.get("arm") or ARM_HEADLINE),
    )


def load_cells_from_report(path: Path) -> list[EquilibrateCell]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return load_cells_from_payload(payload)


def load_cells_from_payload(payload: Mapping[str, Any]) -> list[EquilibrateCell]:
    cells: list[EquilibrateCell] = []
    for key in ("cells", "qualification_cells"):
        for row in payload.get(key) or []:
            if isinstance(row, Mapping):
                cells.append(EquilibrateCell.from_payload(row))
    return cells


def load_engine_blocks_from_report(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    blocks = payload.get("engines") or {}
    return {
        str(name): dict(block)
        for name, block in blocks.items()
        if isinstance(block, Mapping)
    }


def _bypass_melts_domain_gate(backend: Any) -> None:
    """Qualification: record-and-run. Do not change gate VALUES."""

    def _pass(*_args: Any, **_kwargs: Any) -> None:
        return None

    if hasattr(backend, "_domain_gate"):
        backend._domain_gate = _pass  # type: ignore[method-assign]


def run_isolated_cell_worker(
    payload: Mapping[str, Any],
    *,
    handles: dict[str, EngineHandle] | None = None,
    protocol_stdout: Any | None = None,
) -> None:
    """Run one isolated cell and write one framed JSON response."""

    simulate = payload.get("simulate_crash")
    if simulate:
        sig_name = str(simulate)
        sig = getattr(signal, sig_name, None)
        if sig is None:
            os.kill(os.getpid(), signal.SIGABRT)
        os.kill(os.getpid(), int(sig))
        raise SystemExit(1)

    pot = BinaryPot(
        pot_id=str(payload["pot_id"]),
        kato_1993_table4_system=payload.get("kato_1993_table4_system"),
        why=str(payload.get("why") or ""),
        composition_wt_pct=dict(payload.get("composition_wt_pct") or {}),
    )
    po2_raw = payload.get("po2") or {}
    po2 = Po2Request(
        mode=str(po2_raw.get("mode") or PO2_ENGINE_DEFAULT),
        po2_bar=(
            None if po2_raw.get("po2_bar") is None else float(po2_raw["po2_bar"])
        ),
        cell_material=po2_raw.get("cell_material"),
    )
    engine_name = str(payload["engine"])
    handle = None if handles is None else handles.get(engine_name)
    if handle is None:
        handle = open_battery_engine(engine_name)
        if (
            handles is not None
            and handle.available
            and handle.backend is not None
        ):
            handles[engine_name] = handle
    if (
        bool(payload.get("qualification"))
        and handle.name in MELTS_FAMILY_ENGINES
        and handle.backend is not None
    ):
        _bypass_melts_domain_gate(handle.backend)
    if handle.name == "alphamelts" and handle.backend is not None:
        # A fresh per-cell backend used to start with an empty warning set.
        # Keep that output behavior while the native transport is reused.
        warning_seen = getattr(
            handle.backend, "_pseudo_vapor_pressure_warning_seen", None
        )
        if isinstance(warning_seen, set):
            warning_seen.clear()
    cell = equilibrate_cell(
        handle,
        pot,
        temperature_K=float(payload["temperature_K"]),
        po2=po2,
        timeout_s=_finite_float(payload.get("timeout_s")),
        physical_pressure_bar=(
            None
            if payload.get("physical_pressure_bar") is None
            else float(payload["physical_pressure_bar"])
        ),
        qualification=bool(payload.get("qualification")),
        isolated=False,
        arm=str(payload.get("arm") or ARM_HEADLINE),
    )
    output = protocol_stdout if protocol_stdout is not None else sys.stdout
    output.write("\x1e")
    output.write(json.dumps(cell.as_payload(), default=str))
    output.write("\n")
    output.flush()


def _run_isolated_cell_worker_loop() -> None:
    protocol_stdout = sys.stdout
    sys.stdout = sys.stderr
    handles: dict[str, EngineHandle] = {}
    try:
        for line in sys.stdin:
            run_isolated_cell_worker(
                json.loads(line),
                handles=handles,
                protocol_stdout=protocol_stdout,
            )
    finally:
        for handle in handles.values():
            close = getattr(handle.backend, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException:  # noqa: BLE001 - close every engine on exit
                    pass


def _crash_cell_from_returncode(
    *,
    handle: EngineHandle,
    pot: BinaryPot,
    temperature_K: float,
    po2: Po2Request,
    wall0: float,
    cpu0: float,
    hostname: str,
    returncode: int | None,
    timed_out: bool,
    stderr: str,
    arm: str,
    notices: Sequence[Mapping[str, Any]],
    authority: str | None,
    certified_band: Mapping[str, Any] | None,
) -> EquilibrateCell:
    if timed_out:
        status, refusal, engine_reason = (
            "refusal",
            REFUSAL_TIMEOUT,
            "isolated cell exceeded hard timeout",
        )
        engine_status = "TimeoutError"
        exit_signal = None
        exit_code = None
    elif returncode is not None and int(returncode) < 0:
        sig = -int(returncode)
        try:
            sig_name = signal.Signals(sig).name
        except ValueError:
            sig_name = f"signal {sig}"
        status, refusal, engine_reason = (
            "refusal",
            REFUSAL_ENGINE_CRASH,
            f"{sig_name} (returncode {returncode})",
        )
        engine_status = REFUSAL_ENGINE_CRASH
        exit_signal = sig
        exit_code = int(returncode)
    else:
        status, refusal, engine_reason = (
            "refusal",
            REFUSAL_ENGINE_CRASH,
            f"isolated worker exit_code={returncode}: {stderr.strip()[:400]}",
        )
        engine_status = REFUSAL_ENGINE_CRASH
        exit_signal = None
        exit_code = None if returncode is None else int(returncode)
    return EquilibrateCell(
        pot_id=pot.pot_id,
        engine=handle.name,
        temperature_K=float(temperature_K),
        po2=po2,
        status=status,
        refusal_reason=refusal,
        engine_status=engine_status,
        engine_reason=engine_reason,
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=None,
        wall_s=time.perf_counter() - wall0,
        cpu_s=time.process_time() - cpu0,
        hostname=hostname,
        arm=arm,
        notices=[dict(row) for row in notices],
        authority=authority,
        certified_band=None if certified_band is None else dict(certified_band),
        exit_signal=exit_signal,
        exit_code=exit_code,
        model_id=ALL_IMCC_MODEL_IDS.get(handle.name),
    )


class _IsolatedCellWorkerFailure(RuntimeError):
    def __init__(
        self,
        *,
        timed_out: bool = False,
        returncode: int | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(detail)
        self.timed_out = timed_out
        self.returncode = returncode
        self.detail = detail


class _IsolatedCellWorker:
    """Persistent engine process; a failed request retires its whole group."""

    def __init__(self, engine: str) -> None:
        self.engine = str(engine)
        self.process: subprocess.Popen[bytes] | None = None
        self.start_count = 0
        self._stdout_buffer = bytearray()
        self._lock = threading.Lock()

    def _start(self) -> None:
        env = dict(os.environ)
        env.setdefault("PYTHONPATH", str(REPO_ROOT))
        pythonpath = env.get("PYTHONPATH") or ""
        if str(REPO_ROOT) not in pythonpath.split(os.pathsep):
            env["PYTHONPATH"] = os.pathsep.join(
                [str(REPO_ROOT), pythonpath] if pythonpath else [str(REPO_ROOT)]
            )
        self.process = subprocess.Popen(
            [sys.executable, "-c", _ISOLATED_CELL_BOOTSTRAP],
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
        )
        self._stdout_buffer.clear()
        self.start_count += 1

    def _stop(self, *, kill_group: bool) -> int | None:
        process = self.process
        self.process = None
        if process is None:
            return None
        if kill_group:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                if process.poll() is None:
                    process.kill()
        elif process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    process.kill()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        return process.returncode

    def close(self) -> None:
        with self._lock:
            self._stop(kill_group=False)

    def retire(self) -> None:
        with self._lock:
            self._stop(kill_group=True)

    def _failed(self, *, timed_out: bool, detail: str) -> None:
        process = self.process
        returncode = None if process is None else process.poll()
        if process is not None and process.stdout is not None:
            detail = (
                detail
                or bytes(self._stdout_buffer[-400:]).decode("utf-8", "replace")
            )
        stopped_returncode = self._stop(kill_group=True)
        if stopped_returncode is not None:
            returncode = stopped_returncode
        raise _IsolatedCellWorkerFailure(
            timed_out=timed_out,
            returncode=returncode,
            detail=detail,
        )

    def request(
        self, payload: Mapping[str, Any], *, timeout_s: float
    ) -> dict[str, Any]:
        with self._lock:
            if self.process is not None and self.process.poll() is not None:
                self._stop(kill_group=True)
            if self.process is None:
                try:
                    self._start()
                except OSError as exc:
                    raise _IsolatedCellWorkerFailure(detail=str(exc)) from exc
            process = self.process
            assert process is not None
            if process.stdin is None or process.stdout is None:
                self._failed(timed_out=False, detail="worker pipes unavailable")
            deadline = time.monotonic() + max(0.001, float(timeout_s))
            try:
                request_bytes = (json.dumps(payload) + "\n").encode("utf-8")
                view = memoryview(request_bytes)
                while view:
                    written = os.write(process.stdin.fileno(), view)
                    view = view[written:]
            except OSError as exc:
                self._failed(timed_out=False, detail=f"worker request failed: {exc}")

            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._failed(
                            timed_out=True,
                            detail="isolated cell exceeded hard timeout",
                        )
                    if not selector.select(remaining):
                        self._failed(
                            timed_out=True,
                            detail="isolated cell exceeded hard timeout",
                        )
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        self._failed(
                            timed_out=False,
                            detail=(
                                "worker exited without a cell result "
                                f"(returncode={process.poll()})"
                            ),
                        )
                    self._stdout_buffer.extend(chunk)
                    while b"\n" in self._stdout_buffer:
                        line, _, remainder = self._stdout_buffer.partition(b"\n")
                        self._stdout_buffer = bytearray(remainder)
                        marker = line.rfind(b"\x1e")
                        if marker < 0:
                            continue
                        try:
                            result = json.loads(line[marker + 1 :])
                        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                            self._failed(
                                timed_out=False,
                                detail=f"invalid isolated worker response: {exc}",
                            )
                        if not isinstance(result, dict):
                            self._failed(
                                timed_out=False,
                                detail="isolated worker response was not an object",
                            )
                        return result


_ISOLATED_CELL_WORKERS: dict[str, _IsolatedCellWorker] = {}
_ISOLATED_CELL_WORKERS_LOCK = threading.Lock()


def _isolated_cell_worker(engine: str) -> _IsolatedCellWorker:
    with _ISOLATED_CELL_WORKERS_LOCK:
        worker = _ISOLATED_CELL_WORKERS.get(engine)
        if worker is None:
            worker = _IsolatedCellWorker(engine)
            _ISOLATED_CELL_WORKERS[engine] = worker
        return worker


def _close_isolated_cell_workers() -> None:
    with _ISOLATED_CELL_WORKERS_LOCK:
        workers = tuple(_ISOLATED_CELL_WORKERS.values())
        _ISOLATED_CELL_WORKERS.clear()
    for worker in workers:
        worker.close()


atexit.register(_close_isolated_cell_workers)


def _run_cell_in_subprocess(
    handle: EngineHandle,
    pot: BinaryPot,
    *,
    temperature_K: float,
    po2: Po2Request,
    timeout_s: float,
    qualification: bool,
    arm: str,
    notices: Sequence[Mapping[str, Any]],
    authority: str | None,
    certified_band: Mapping[str, Any] | None,
    simulate_crash: str | None = None,
    physical_pressure_bar: float | None = None,
) -> EquilibrateCell:
    hostname = _hostname()
    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    payload = {
        "pot_id": pot.pot_id,
        "kato_1993_table4_system": pot.kato_1993_table4_system,
        "why": pot.why,
        "composition_wt_pct": dict(pot.composition_wt_pct),
        "engine": handle.name,
        "temperature_K": float(temperature_K),
        "po2": po2.as_payload(),
        "timeout_s": float(timeout_s),
        "qualification": bool(qualification),
        "arm": arm,
        "simulate_crash": simulate_crash,
    }
    if physical_pressure_bar is not None:
        payload["physical_pressure_bar"] = float(physical_pressure_bar)
    worker = _isolated_cell_worker(handle.name)
    try:
        raw = worker.request(
            payload,
            timeout_s=float(timeout_s) + 2.0,
        )
    except _IsolatedCellWorkerFailure as exc:
        return _crash_cell_from_returncode(
            handle=handle,
            pot=pot,
            temperature_K=temperature_K,
            po2=po2,
            wall0=wall0,
            cpu0=cpu0,
            hostname=hostname,
            returncode=exc.returncode,
            timed_out=exc.timed_out,
            stderr=exc.detail,
            arm=arm,
            notices=notices,
            authority=authority,
            certified_band=certified_band,
        )
    try:
        cell = EquilibrateCell.from_payload(raw)
    except (KeyError, TypeError, ValueError) as exc:
        worker.retire()
        return _crash_cell_from_returncode(
            handle=handle,
            pot=pot,
            temperature_K=temperature_K,
            po2=po2,
            wall0=wall0,
            cpu0=cpu0,
            hostname=hostname,
            returncode=None,
            timed_out=False,
            stderr=f"invalid isolated worker result ({exc})",
            arm=arm,
            notices=notices,
            authority=authority,
            certified_band=certified_band,
        )
    if cell.refusal_reason == REFUSAL_TIMEOUT or cell.engine_status == "TimeoutError":
        worker.retire()
    merged_notices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in list(notices) + list(cell.notices):
        key = json.dumps(row, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        merged_notices.append(dict(row))
    return replace(
        cell,
        arm=arm,
        notices=[dict(row) for row in merged_notices],
        authority=cell.authority or authority,
        certified_band=cell.certified_band or (
            None if certified_band is None else dict(certified_band)
        ),
        wall_s=time.perf_counter() - wall0,
        hostname=cell.hostname or hostname,
    )


# ---------------------------------------------------------------------------
# Engine resolution
# ---------------------------------------------------------------------------


def _engine_identities_from_toml() -> dict[str, dict[str, str]]:
    try:
        from simulator.engine_local_config import load_config
    except Exception:  # noqa: BLE001 - identity is a receipt, not a gate
        return {}
    config = load_config()
    if config is None:
        return {}
    payload: dict[str, dict[str, str]] = {}
    for key, identity in config.identities.items():
        payload[str(key)] = {
            "name": identity.name,
            "version": identity.version,
            "digest": identity.digest,
            **{str(k): str(v) for k, v in dict(identity.extra).items()},
        }
    return payload


def _equilibrate_takes_fo2(backend: Any) -> bool:
    try:
        signature = inspect.signature(backend.equilibrate)
    except (TypeError, ValueError):
        return True
    return "fO2_log" in signature.parameters


def _open_resolved_backend(name: str) -> Any:
    from simulator.backends import (
        BackendSelectionPolicy,
        BackendUnavailableError,
        _try_backend,
        resolve_backend,
    )
    from simulator.melt_backend.magemin import MAGEMinBackend
    from simulator.vapour_rail.calibration import open_warm_vaporock_backend

    if name in OPENIMCC_ENGINE_NAMES:
        return _OpenImccBatteryBackend(name)
    if name == "internal-analytical":
        return _InternalAnalyticalBatteryBackend()
    if name == "vaporock":
        return open_warm_vaporock_backend(warm_pool_size=1)
    if name == "magemin":
        backend = _try_backend(MAGEMinBackend, {})
        if backend is None:
            raise BackendUnavailableError(
                "MAGEMin unavailable; binary missing or initialize() failed"
            )
        return backend
    return resolve_backend(name, BackendSelectionPolicy.RUNNER_STRICT)


def open_battery_engine(name: str) -> EngineHandle:
    """Resolve one battery engine. Unavailable is a typed handle, not a crash."""

    from simulator.backends import BackendUnavailableError

    identity_block = _engine_identities_from_toml().get(name, {})
    try:
        backend = _open_resolved_backend(name)
        if name in IMCC_ENGINE_NAMES and hasattr(backend, "_identity"):
            identity_block = {**identity_block, **dict(backend._identity)}
    except BackendUnavailableError as exc:
        return EngineHandle(
            name=name,
            backend=None,
            available=False,
            unavailable_reason=str(exc),
            takes_fo2=False,
            supports_intrinsic_fo2=False,
            identity=identity_block,
        )
    except Exception as exc:  # noqa: BLE001 - probe must not crash the battery
        return EngineHandle(
            name=name,
            backend=None,
            available=False,
            unavailable_reason=f"{type(exc).__name__}: {exc}",
            takes_fo2=False,
            supports_intrinsic_fo2=False,
            identity=identity_block,
        )

    # A resolved backend object is called even when is_available() is False
    # (InternalAnalyticalBackend: core.py owns the Ellingham path;
    # equilibrate() itself returns status='unavailable').
    takes_fo2 = _equilibrate_takes_fo2(backend)
    return EngineHandle(
        name=name,
        backend=backend,
        available=True,
        unavailable_reason=None,
        takes_fo2=takes_fo2,
        supports_intrinsic_fo2=bool(
            getattr(backend, "supports_intrinsic_fO2", False)
        ),
        identity=identity_block,
    )


def probe_battery_engines(
    names: Sequence[str] = BATTERY_ENGINE_NAMES,
) -> dict[str, EngineHandle]:
    return {name: open_battery_engine(name) for name in names}


# ---------------------------------------------------------------------------
# Equilibrate cells
# ---------------------------------------------------------------------------


def _call_with_hard_timeout(fn: Callable[[], Any], timeout_s: float) -> Any:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - forwarded to caller
            box["exc"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(float(timeout_s))
    if thread.is_alive():
        raise TimeoutError(
            f"equilibrate exceeded hard timeout of {timeout_s:g}s"
        )
    if "exc" in box:
        raise box["exc"]
    return box.get("result")


def _fo2_log_for_request(handle: EngineHandle, request: Po2Request) -> float | None:
    if request.mode == PO2_COMMANDED:
        assert request.po2_bar is not None
        return math.log10(float(request.po2_bar))
    if request.mode == PO2_NOT_AN_INPUT:
        return None
    if request.mode == PO2_OXYGEN_BALANCE_EFFUSION:
        return None
    if handle.supports_intrinsic_fo2:
        return None
    return _DEFAULT_FO2_LOG


def _hostname() -> str:
    env_name = os.environ.get("GOALFLIGHT_HOSTNAME") or os.environ.get("HOSTNAME")
    if env_name:
        return str(env_name)
    try:
        probed = subprocess.run(
            ["hostname"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        if probed:
            return probed
    except (OSError, subprocess.SubprocessError):
        pass
    return socket.gethostname()


def equilibrate_cell(
    handle: EngineHandle,
    pot: BinaryPot,
    *,
    temperature_K: float,
    po2: Po2Request,
    timeout_s: float | None = None,
    qualification: bool = False,
    isolated: bool | None = None,
    arm: str | None = None,
    simulate_crash: str | None = None,
    physical_pressure_bar: float | None = None,
) -> EquilibrateCell:
    """One pot × engine × T × pO2 call. Refusals are rows, never exceptions."""

    hostname = _hostname()
    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    cell_arm = arm or (ARM_QUALIFICATION if qualification else ARM_HEADLINE)
    notices: list[dict[str, Any]] = []
    authority: str | None = None
    certified_band: dict[str, Any] | None = None
    if qualification and handle.name in MELTS_FAMILY_ENGINES:
        gate = assess_qualification_gate(pot.composition_wt_pct, temperature_K)
        notices.append(qualification_notice(gate))
        authority = AUTHORITY_EXTRAPOLATED
        certified_band = dict(gate["certified_band"])

    def _done(**kwargs: Any) -> EquilibrateCell:
        extra_notices = list(kwargs.pop("notices", None) or [])
        return EquilibrateCell(
            pot_id=pot.pot_id,
            engine=handle.name,
            temperature_K=float(temperature_K),
            po2=po2,
            wall_s=time.perf_counter() - wall0,
            cpu_s=time.process_time() - cpu0,
            hostname=hostname,
            arm=cell_arm,
            notices=[*notices, *extra_notices],
            authority=kwargs.pop("authority", None) or authority,
            certified_band=kwargs.pop("certified_band", None) or certified_band,
            model_id=kwargs.pop("model_id", None) or ALL_IMCC_MODEL_IDS.get(handle.name),
            **kwargs,
        )

    if (
        po2.mode == PO2_OXYGEN_BALANCE_EFFUSION
        and po2.cell_material not in {None, "W", "Mo"}
    ):
        return _done(
            status="refusal",
            refusal_reason="oxygen_balance_cell_material_invalid",
            engine_status="input_refusal",
            engine_reason=(
                f"unknown cell_material {po2.cell_material!r}; "
                "expected None, 'W', or 'Mo'"
            ),
            melt_activities={},
            gas_partial_pressures_Pa={},
            liquid_fraction=None,
        )

    timeout = float(timeout_s or _ENGINE_OUTER_TIMEOUT_S.get(handle.name, 30.0))
    use_isolated = isolated
    if use_isolated is None:
        use_isolated = bool(
            simulate_crash
            or (qualification and handle.name in MELTS_FAMILY_ENGINES)
        )
    if use_isolated:
        return _run_cell_in_subprocess(
            handle,
            pot,
            temperature_K=temperature_K,
            po2=po2,
            timeout_s=timeout,
            qualification=qualification,
            arm=cell_arm,
            notices=notices,
            authority=authority,
            certified_band=certified_band,
            simulate_crash=simulate_crash,
            physical_pressure_bar=physical_pressure_bar,
        )

    if not handle.available or handle.backend is None:
        return _done(
            status="refusal",
            refusal_reason=REFUSAL_UNAVAILABLE,
            engine_status="unavailable",
            engine_reason=handle.unavailable_reason,
            melt_activities={},
            gas_partial_pressures_Pa={},
            liquid_fraction=None,
        )

    if qualification and handle.name in MELTS_FAMILY_ENGINES:
        _bypass_melts_domain_gate(handle.backend)

    composition_kg, composition_mol = composition_kg_and_mol(pot.composition_wt_pct)
    temperature_C = float(temperature_K) - CELSIUS_TO_KELVIN_OFFSET
    fo2_log = _fo2_log_for_request(handle, po2)
    if physical_pressure_bar is None:
        physical_pressure_bar = _DEFAULT_PRESSURE_BAR
    else:
        physical_pressure_bar = float(physical_pressure_bar)
    pressure_bar = physical_pressure_bar
    if handle.name == "alphamelts":
        from simulator.alphamelts_reference_pressure import (
            alphamelts_condensed_phase_pressure_bar,
        )

        pressure_bar = alphamelts_condensed_phase_pressure_bar(
            physical_pressure_bar,
            transport=getattr(handle.backend, "_mode", None),
        )
    kwargs: dict[str, Any] = {
        "temperature_C": temperature_C,
        "composition_kg": composition_kg,
        "composition_mol": composition_mol,
        "pressure_bar": pressure_bar,
    }
    try:
        signature = inspect.signature(handle.backend.equilibrate)
        parameters = signature.parameters
    except (TypeError, ValueError):
        parameters = {}
    if "fO2_log" in parameters:
        kwargs["fO2_log"] = fo2_log
    if "po2_request" in parameters:
        kwargs["po2_request"] = po2
    if "call_timeout_s" in parameters:
        kwargs["call_timeout_s"] = timeout
    if handle.name == "alphamelts" or "subprocess_run_mode" in parameters:
        kwargs["subprocess_run_mode"] = "isothermal"

    try:
        result = _call_with_hard_timeout(
            lambda: handle.backend.equilibrate(**kwargs),
            timeout,
        )
        status, refusal, engine_reason = classify_equilibrate_outcome(result)
        diagnostics = dict(getattr(result, "diagnostics", None) or {})
        engine_annotation = crash_floor_engine_annotation(diagnostics)
        if (
            qualification
            and status == "refusal"
            and refusal not in {REFUSAL_TIMEOUT, REFUSAL_ENGINE_CRASH}
        ):
            gate_name = (
                diagnostics.get("backend_failure_reason_code")
                or diagnostics.get("backend_status_reason")
                or engine_reason
                or refusal
            )
            refusal = REFUSAL_GATE_REFUSED_IN_ADAPTER
            engine_reason = f"{gate_name}"
            engine_annotation = None
        activities, pressures = extract_reported_quantities(result)
        coefficients = reported_activity_coefficients(result)
        coefficient_details = dict(
            getattr(result, "activity_coefficient_details", None) or {}
        )
        vapor_authority = extract_vapor_authority(result)
        flag_notices, flag_authority, flag_band = engine_flags_from_result(result)
        crash_diag = diagnostics.get("subprocess_failure") or {}
        exit_code = _optional_int(
            crash_diag.get("returncode") if isinstance(crash_diag, Mapping) else None
        )
        exit_signal = None
        if exit_code is not None and exit_code < 0:
            exit_signal = -exit_code
        return _done(
            status=status,
            refusal_reason=refusal,
            engine_status=str(getattr(result, "status", None) or status),
            engine_reason=engine_reason,
            engine_annotation=engine_annotation,
            melt_activities=activities,
            melt_activity_coefficients=coefficients,
            melt_activity_coefficient_details=coefficient_details,
            gas_partial_pressures_Pa=pressures,
            liquid_fraction=_finite_float(getattr(result, "liquid_fraction", None)),
            vapor_pressures_source=dict(vapor_authority["vapor_pressures_source"]),
            vapor_pressure_backend_status=vapor_authority[
                "vapor_pressure_backend_status"
            ],
            vapor_pressure_backend_status_reason=vapor_authority[
                "vapor_pressure_backend_status_reason"
            ],
            authoritative_for_requested_vapor_pressure=vapor_authority[
                "authoritative_for_requested_vapor_pressure"
            ],
            notices=flag_notices,
            authority=flag_authority,
            certified_band=flag_band,
            exit_signal=exit_signal,
            exit_code=exit_code,
            model_id=getattr(result, "imcc_model_id", None),
        )
    except Exception as exc:  # noqa: BLE001 - typed refusal, never a hang/crash
        status, refusal, engine_reason = classify_equilibrate_outcome(error=exc)
        # A domain refusal is a row. Closing the handle would stamp every
        # later cell unavailable (ThermoEngine fo2_requires_iron on one
        # Fe-free commanded-pO2 cell killed the rest of that column).
        is_timeout = (
            refusal == REFUSAL_TIMEOUT
            or isinstance(exc, TimeoutError)
            or "timeout" in type(exc).__name__.lower()
        )
        if is_timeout:
            closer = getattr(handle.backend, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 - close is best-effort after timeout
                    pass
            handle.backend = None
            handle.available = False
            handle.unavailable_reason = engine_reason
        return _done(
            status=status,
            refusal_reason=refusal,
            engine_status=type(exc).__name__,
            engine_reason=engine_reason,
            melt_activities={},
            gas_partial_pressures_Pa={},
            liquid_fraction=None,
        )


@dataclass(frozen=True)
class _ArmJob:
    pot: BinaryPot
    temperatures_K: tuple[float, ...]
    use_grid_po2: bool
    arm: str
    qualification: bool


def _engine_arm_jobs(
    *,
    pots: Sequence[BinaryPot],
    grid: BatteryGrid,
    include_scoring_pots: bool,
    qualification: bool,
    pots_path: Path | None,
) -> tuple[list[_ArmJob], tuple[BinaryPot, ...]]:
    jobs: list[_ArmJob] = [
        _ArmJob(
            pot=pot,
            temperatures_K=tuple(grid.temperatures_K),
            use_grid_po2=True,
            arm=ARM_HEADLINE,
            qualification=False,
        )
        for pot in pots
    ]
    extra_pots: list[BinaryPot] = []
    if include_scoring_pots:
        from simulator.diagnostic_helpers.binary_pot_scoring import load_scoring_pots

        for scoring in load_scoring_pots(pots_path):
            binary = scoring.as_binary_pot()
            extra_pots.append(binary)
            jobs.append(
                _ArmJob(
                    pot=binary,
                    temperatures_K=tuple(scoring.temperatures_K),
                    use_grid_po2=False,
                    arm=ARM_HEADLINE,
                    qualification=False,
                )
            )
    if qualification:
        source = next(
            (pot for pot in pots if pot.pot_id == QUALIFICATION_SOURCE_POT_ID),
            None,
        )
        sweep = qualification_sio2_sweep_pots(source)
        extra_pots.extend(sweep)
        for pot in sweep:
            jobs.append(
                _ArmJob(
                    pot=pot,
                    temperatures_K=(QUALIFICATION_SWEEP_T_K,),
                    use_grid_po2=False,
                    arm=ARM_QUALIFICATION,
                    qualification=True,
                )
            )
        for pot in pots:
            jobs.append(
                _ArmJob(
                    pot=pot,
                    temperatures_K=QUALIFICATION_TEMPERATURES_K,
                    use_grid_po2=False,
                    arm=ARM_QUALIFICATION,
                    qualification=True,
                )
            )
    catalog = tuple(list(pots) + extra_pots)
    return jobs, catalog


def run_engine_arm(
    *,
    pots_path: Path | None = None,
    engine_names: Sequence[str] = BATTERY_ENGINE_NAMES,
    handles: Mapping[str, EngineHandle] | None = None,
    pots: Sequence[BinaryPot] | None = None,
    grid: BatteryGrid | None = None,
    progress_log: Path | None = None,
    qualification: bool = False,
    include_scoring_pots: bool = False,
    reuse_cells: Sequence[EquilibrateCell] | None = None,
    reuse_engine_blocks: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run every pot × engine × T × pO2 cell and build the report."""

    if pots is None or grid is None:
        loaded_pots, loaded_grid = load_binary_pots(pots_path)
        pots = pots or loaded_pots
        grid = grid or loaded_grid
    jobs, catalog = _engine_arm_jobs(
        pots=pots,
        grid=grid,
        include_scoring_pots=include_scoring_pots,
        qualification=qualification,
        pots_path=pots_path,
    )
    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    hostname = _hostname()
    resolved = dict(handles) if handles is not None else probe_battery_engines(engine_names)
    reused = {
        _cell_identity_key(cell): cell
        for cell in (reuse_cells or ())
    }

    n_expected = 0
    for job in jobs:
        for name in engine_names:
            handle = resolved.get(name) or open_battery_engine(name)
            resolved[name] = handle
            takes_fo2 = handle.takes_fo2 or bool(
                (reuse_engine_blocks or {}).get(name, {}).get("takes_fo2")
            )
            n_po2 = (
                len(po2_requests_for_engine(grid, takes_fo2=takes_fo2))
                if job.use_grid_po2
                else 1
            )
            n_expected += len(job.temperatures_K) * n_po2
    _emit_progress(
        progress_log,
        (
            f"{_utc_stamp()} START hostname={hostname} n_expected={n_expected} "
            f"qualification={str(qualification).lower()} "
            f"scoring={str(include_scoring_pots).lower()} "
            f"reuse={len(reused)}"
        ),
    )

    cells: list[EquilibrateCell] = []
    for job in jobs:
        for name in engine_names:
            handle = resolved.get(name) or open_battery_engine(name)
            resolved[name] = handle
            takes_fo2 = handle.takes_fo2 or bool(
                (reuse_engine_blocks or {}).get(name, {}).get("takes_fo2")
            )
            requests = (
                po2_requests_for_engine(grid, takes_fo2=takes_fo2)
                if job.use_grid_po2
                else (Po2Request(mode=PO2_ENGINE_DEFAULT, po2_bar=None),)
            )
            for temperature_K in job.temperatures_K:
                for po2 in requests:
                    probe = EquilibrateCell(
                        pot_id=job.pot.pot_id,
                        engine=name,
                        temperature_K=float(temperature_K),
                        po2=po2,
                        status="",
                        refusal_reason=None,
                        engine_status=None,
                        engine_reason=None,
                        melt_activities={},
                        gas_partial_pressures_Pa={},
                        liquid_fraction=None,
                        wall_s=0.0,
                        cpu_s=0.0,
                        hostname="",
                        arm=job.arm,
                    )
                    existing = reused.get(_cell_identity_key(probe))
                    if existing is not None:
                        cell = existing
                    else:
                        cell = equilibrate_cell(
                            handle,
                            job.pot,
                            temperature_K=float(temperature_K),
                            po2=po2,
                            qualification=job.qualification,
                            arm=job.arm,
                        )
                    cells.append(cell)
                    po2_label = (
                        "default"
                        if po2.mode == PO2_ENGINE_DEFAULT
                        else f"{po2.po2_bar:g}"
                    )
                    _emit_progress(
                        progress_log,
                        (
                            f"{_utc_stamp()} {len(cells)}/{n_expected} "
                            f"arm={cell.arm} pot={cell.pot_id} engine={cell.engine} "
                            f"T={cell.temperature_K:g} po2={po2_label} "
                            f"status={cell.status} "
                            f"reason={cell.refusal_reason or '-'} "
                            f"n_act={len(cell.melt_activities)} "
                            f"n_gas={len(cell.gas_partial_pressures_Pa)} "
                            f"wall={cell.wall_s:.3f}"
                        ),
                    )
                    if not handle.available:
                        # Re-open once after a kill; if still dead, stamp the rest.
                        if handle.unavailable_reason and "timeout" in (
                            handle.unavailable_reason or ""
                        ).lower():
                            revived = open_battery_engine(name)
                            resolved[name] = revived
                            handle = revived

    report = build_report(
        pots=catalog,
        grid=grid,
        handles=resolved,
        cells=cells,
        hostname=hostname,
        wall_s=time.perf_counter() - wall0,
        cpu_s=time.process_time() - cpu0,
        engine_names=engine_names,
        reuse_engine_blocks=reuse_engine_blocks,
    )
    _emit_progress(
        progress_log,
        (
            f"{_utc_stamp()} DONE hostname={report['hostname']} "
            f"n_cells={report['n_cells']} n_ok={report['n_ok']} "
            f"n_refused={report['n_refused']} "
            f"n_matched_residuals={report['n_matched_residuals']} "
            f"n_qualification_cells={report.get('n_qualification_cells', 0)} "
            f"wall={report['receipt']['wall_s']:.3f} "
            f"cpu={report['receipt']['cpu_s']:.3f}"
        ),
    )
    return report


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _refusal_matrix(
    pots: Sequence[BinaryPot],
    engine_names: Sequence[str],
    cells: Sequence[EquilibrateCell],
) -> dict[str, dict[str, dict[str, Any]]]:
    matrix: dict[str, dict[str, dict[str, Any]]] = {}
    by_pair: dict[tuple[str, str], list[EquilibrateCell]] = defaultdict(list)
    for cell in cells:
        by_pair[(cell.pot_id, cell.engine)].append(cell)
    for pot in pots:
        matrix[pot.pot_id] = {}
        for engine in engine_names:
            group = by_pair.get((pot.pot_id, engine), [])
            reasons = Counter(
                cell.refusal_reason for cell in group if cell.status == "refusal"
            )
            n_ok = sum(1 for cell in group if cell.status == "ok")
            n_refused = sum(1 for cell in group if cell.status == "refusal")
            dominant = None
            if reasons:
                dominant = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            elif n_ok == 0 and n_refused == 0:
                dominant = REFUSAL_UNAVAILABLE
            notes = []
            if dominant == REFUSAL_COMPOSITION_PROJECTED:
                notes = sorted(
                    {
                        cell.engine_reason
                        for cell in group
                        if cell.refusal_reason == REFUSAL_COMPOSITION_PROJECTED
                        and cell.engine_reason
                    }
                )
            matrix[pot.pot_id][engine] = {
                "n_cells": len(group),
                "n_ok": n_ok,
                "n_refused": n_refused,
                "dominant_reason": dominant,
                "reasons": dict(sorted(reasons.items())),
                "note": "; ".join(notes) if notes else None,
            }
    return matrix


def _per_pot_residual_tables(
    pots: Sequence[BinaryPot],
    residuals: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    by_pot: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in residuals:
        by_pot[str(row["pot_id"])].append(row)
    for pot in pots:
        rows = by_pot.get(pot.pot_id, [])
        labels = Counter(str(row["divergence_label"]) for row in rows)
        abs_deltas = [abs(float(row["delta_log10_a_minus_b"])) for row in rows]
        pair_abs: dict[tuple[str, str], list[float]] = defaultdict(list)
        for row in rows:
            pair_abs[(str(row["engine_a"]), str(row["engine_b"]))].append(
                abs(float(row["delta_log10_a_minus_b"]))
            )
        pair_rows = []
        for (engine_a, engine_b), values in sorted(pair_abs.items()):
            pair_rows.append(
                {
                    "engine_a": engine_a,
                    "engine_b": engine_b,
                    "n": len(values),
                    "max_abs_delta_dex": max(values),
                    "median_abs_delta_dex": sorted(values)[len(values) // 2],
                    "divergence_label": divergence_label(max(values)),
                }
            )
        tables[pot.pot_id] = {
            "n_matched": len(rows),
            "max_abs_delta_dex": max(abs_deltas) if abs_deltas else None,
            "median_abs_delta_dex": (
                sorted(abs_deltas)[len(abs_deltas) // 2] if abs_deltas else None
            ),
            "divergence_labels": dict(sorted(labels.items())),
            "engine_pairs": pair_rows,
        }
    return tables


def _qualification_engine_summary(
    cells: Sequence[EquilibrateCell],
    engine_names: Sequence[str],
) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for name in engine_names:
        group = [cell for cell in cells if cell.engine == name]
        n_returned = sum(
            1
            for cell in group
            if cell.status == "ok"
            and (cell.melt_activities or cell.gas_partial_pressures_Pa)
        )
        n_crash = sum(
            1 for cell in group if cell.refusal_reason == REFUSAL_ENGINE_CRASH
        )
        n_timeout = sum(
            1 for cell in group if cell.refusal_reason == REFUSAL_TIMEOUT
        )
        summary[name] = {
            "n_cells": len(group),
            "n_returned": n_returned,
            "n_ok": sum(1 for cell in group if cell.status == "ok"),
            "n_crashed": n_crash,
            "n_timed_out": n_timeout,
            "n_refused": sum(1 for cell in group if cell.status == "refusal"),
        }
    return summary


def _residual_shift_in_vs_out_of_band(
    headline_residuals: Sequence[Mapping[str, Any]],
    qualification_residuals: Sequence[Mapping[str, Any]],
    *,
    engine: str,
    peer: str,
) -> dict[str, Any]:
    def _max_for(rows: Sequence[Mapping[str, Any]]) -> float | None:
        values = [
            abs(float(row["delta_log10_a_minus_b"]))
            for row in rows
            if engine in (row.get("engine_a"), row.get("engine_b"))
            and peer in (row.get("engine_a"), row.get("engine_b"))
        ]
        return max(values) if values else None

    in_band = _max_for(headline_residuals)
    out_of_band = _max_for(qualification_residuals)
    shift = None
    if in_band is not None and out_of_band is not None:
        shift = out_of_band - in_band
    elif out_of_band is not None:
        shift = out_of_band
    return {
        "engine": engine,
        "peer": peer,
        "largest_in_band_abs_dex": in_band,
        "largest_out_of_band_abs_dex": out_of_band,
        "out_minus_in_dex": shift,
    }


def qualification_section(
    *,
    cells: Sequence[EquilibrateCell],
    headline_residuals: Sequence[Mapping[str, Any]],
    engine_names: Sequence[str],
) -> dict[str, Any]:
    """Out-of-domain residuals. NOT headline accuracy."""

    qual_cells = [cell for cell in cells if cell.arm == ARM_QUALIFICATION]
    qual_residuals = pairwise_residuals(qual_cells)
    per_engine: dict[str, Any] = {}
    for name in engine_names:
        group = [cell for cell in qual_cells if cell.engine == name]
        rows_vs_imcc = [
            row
            for row in qual_residuals
            if name in (row.get("engine_a"), row.get("engine_b"))
            and (
                row.get("engine_a") in IMCC_ENGINE_NAMES
                or row.get("engine_b") in IMCC_ENGINE_NAMES
            )
        ]
        rows_vs_vaporock = [
            row
            for row in qual_residuals
            if name in (row.get("engine_a"), row.get("engine_b"))
            and "vaporock" in (row.get("engine_a"), row.get("engine_b"))
        ]
        per_engine[name] = {
            **_qualification_engine_summary(group, (name,))[name],
            "gate_verdicts": [
                {
                    "pot_id": cell.pot_id,
                    "temperature_K": cell.temperature_K,
                    "gate_valid": next(
                        (
                            notice.get("gate_valid")
                            for notice in cell.notices
                            if notice.get("kind") == "melts_domain_gate"
                        ),
                        None,
                    ),
                    "failed_constraints": next(
                        (
                            notice.get("failed_constraints")
                            for notice in cell.notices
                            if notice.get("kind") == "melts_domain_gate"
                        ),
                        [],
                    ),
                    "returned_number": bool(
                        cell.status == "ok"
                        and (cell.melt_activities or cell.gas_partial_pressures_Pa)
                    ),
                    "status": cell.status,
                    "refusal_reason": cell.refusal_reason,
                    "exit_signal": cell.exit_signal,
                    "exit_code": cell.exit_code,
                    "authority": cell.authority,
                }
                for cell in group
            ],
            "residuals_vs_imcc": rows_vs_imcc[:20],
            "residuals_vs_vaporock": rows_vs_vaporock[:20],
            "residual_shift_vs_vaporock": _residual_shift_in_vs_out_of_band(
                headline_residuals,
                qual_residuals,
                engine=name,
                peer="vaporock",
            ),
        }
    return {
        "label": (
            "Qualification (out-of-domain) residuals. NOT headline accuracy. "
            "MELTS-family cells record the domain-gate verdict with "
            "authority=extrapolated and certified_band, then run anyway."
        ),
        "certified_band": melts_certified_band(),
        "n_cells": len(qual_cells),
        "per_engine": per_engine,
        "summary": _qualification_engine_summary(qual_cells, engine_names),
    }


def build_report(
    *,
    pots: Sequence[BinaryPot],
    grid: BatteryGrid,
    handles: Mapping[str, EngineHandle],
    cells: Sequence[EquilibrateCell],
    hostname: str,
    wall_s: float,
    cpu_s: float,
    engine_names: Sequence[str] = BATTERY_ENGINE_NAMES,
    generated_at: str | None = None,
    reuse_engine_blocks: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    headline_cells = [cell for cell in cells if cell.arm != ARM_QUALIFICATION]
    qualification_cells = [cell for cell in cells if cell.arm == ARM_QUALIFICATION]
    residuals = pairwise_residuals(headline_cells)
    floor_refusals = collect_floor_refusals(headline_cells)
    identities = _engine_identities_from_toml()
    engines_block: dict[str, Any] = {}
    for name in engine_names:
        handle = handles.get(name)
        reused_block = dict((reuse_engine_blocks or {}).get(name) or {})
        local_available = bool(handle.available) if handle is not None else False
        if not local_available and reused_block:
            engines_block[name] = {
                "available": bool(reused_block.get("available")),
                "unavailable_reason": reused_block.get("unavailable_reason"),
                "takes_fo2": bool(reused_block.get("takes_fo2")),
                "supports_intrinsic_fo2": bool(
                    reused_block.get("supports_intrinsic_fo2")
                ),
                "identity": dict(reused_block.get("identity") or {}),
                "availability_source": "reused_studio_cells",
            }
            continue
        engines_block[name] = {
            "available": local_available,
            "unavailable_reason": (
                handle.unavailable_reason if handle is not None else "not_probed"
            ),
            "takes_fo2": bool(handle.takes_fo2) if handle is not None else False,
            "supports_intrinsic_fo2": (
                bool(handle.supports_intrinsic_fo2) if handle is not None else False
            ),
            "identity": (
                dict(handle.identity)
                if handle is not None and handle.identity
                else identities.get(name, {})
            ),
        }
    cpu = float(cpu_s)
    wall = float(wall_s)
    headline_pot_ids = {cell.pot_id for cell in headline_cells}
    headline_pots = [pot for pot in pots if pot.pot_id in headline_pot_ids] or list(pots)
    qual_payload = (
        qualification_section(
            cells=cells,
            headline_residuals=residuals,
            engine_names=engine_names,
        )
        if qualification_cells
        else None
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "kind": "binary_pot_engine_arm",
        "generated_at": generated_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "authority": "diagnostic_only",
        "certifies": False,
        "calibrates": False,
        "verdict": None,
        "posture": (
            "Measured engine-vs-engine residuals are signal, not a pass/fail "
            "gate. Signed delta is log10(value_a/value_b). "
            "divergence_label is a descriptive magnitude band only; never an "
            "acceptance verdict. No coefficient is adjusted by this harness."
        ),
        "hostname": hostname,
        "receipt": {
            "hostname": hostname,
            "engine_identities": identities,
            "wall_s": wall,
            "cpu_s": cpu,
            "wall_cpu_ratio": (wall / cpu) if cpu > 0.0 else None,
        },
        "domain": {
            "temperatures_K": list(grid.temperatures_K),
            "temperature_K": [grid.temperatures_K[0], grid.temperatures_K[-1]],
            "po2_bar": {
                "engine_default": grid.engine_default_po2,
                "commanded": list(grid.commanded_po2_bar),
            },
            "pressure_bar": _DEFAULT_PRESSURE_BAR,
        },
        "pots": [
            {
                "pot_id": pot.pot_id,
                "kato_1993_table4_system": pot.kato_1993_table4_system,
                "why": pot.why,
                "composition_wt_pct": dict(pot.composition_wt_pct),
            }
            for pot in pots
        ],
        "engines": engines_block,
        "refusal_matrix": _refusal_matrix(headline_pots, engine_names, headline_cells),
        "per_pot_residuals": _per_pot_residual_tables(headline_pots, residuals),
        "largest_in_envelope_residuals": residuals[:20],
        "n_cells": len(headline_cells),
        "n_ok": sum(1 for cell in headline_cells if cell.status == "ok"),
        "n_refused": sum(1 for cell in headline_cells if cell.status == "refusal"),
        "n_matched_residuals": len(residuals),
        "n_floor_refusals": len(floor_refusals),
        "n_fallback_vs_speciation": sum(
            1
            for row in residuals
            if row.get("finding_class") == FINDING_CLASS_FALLBACK_VS_SPECIATION
        ),
        "n_qualification_cells": len(qualification_cells),
        "floor_refusals": floor_refusals,
        "cells": [cell.as_payload() for cell in cells],
        "qualification": qual_payload,
        "note": (
            "divergence_label is a descriptive magnitude band only; "
            "never an acceptance verdict. Floor/sentinel/absent values "
            "are value_is_floor refusals and are excluded from residuals. "
            "Qualification (out-of-domain) residuals are NOT headline accuracy."
        ),
    }


def recompute_residuals_from_report(
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-score an existing engine-arm JSON (no engine re-run)."""

    pots = tuple(
        BinaryPot(
            pot_id=str(row["pot_id"]),
            kato_1993_table4_system=row.get("kato_1993_table4_system"),
            why=str(row.get("why") or ""),
            composition_wt_pct=dict(row.get("composition_wt_pct") or {}),
        )
        for row in report.get("pots") or []
        if isinstance(row, Mapping)
    )
    raw_cells = [
        EquilibrateCell.from_payload(row)
        if not isinstance(row, EquilibrateCell)
        else row
        for row in report.get("cells") or []
        if isinstance(row, (EquilibrateCell, Mapping))
    ]
    cells = reclassify_projected_composition_cells(raw_cells, pots)
    headline_cells = [cell for cell in cells if cell.arm != ARM_QUALIFICATION]
    qualification_cells = [cell for cell in cells if cell.arm == ARM_QUALIFICATION]
    residuals = pairwise_residuals(headline_cells)
    floor_refusals = collect_floor_refusals(headline_cells)
    engine_names = list((report.get("engines") or {}).keys()) or list(
        BATTERY_ENGINE_NAMES
    )
    headline_pot_ids = {cell.pot_id for cell in headline_cells}
    headline_pots = [pot for pot in pots if pot.pot_id in headline_pot_ids] or list(
        pots
    )
    updated = dict(report)
    updated["cells"] = [cell.as_payload() for cell in cells]
    updated["refusal_matrix"] = _refusal_matrix(
        headline_pots, engine_names, headline_cells
    )
    updated["n_cells"] = len(headline_cells)
    updated["n_ok"] = sum(1 for cell in headline_cells if cell.status == "ok")
    updated["n_refused"] = sum(
        1 for cell in headline_cells if cell.status == "refusal"
    )
    updated["largest_in_envelope_residuals"] = residuals[:20]
    updated["n_matched_residuals"] = len(residuals)
    updated["per_pot_residuals"] = _per_pot_residual_tables(headline_pots, residuals)
    updated["n_floor_refusals"] = len(floor_refusals)
    updated["n_fallback_vs_speciation"] = sum(
        1
        for row in residuals
        if row.get("finding_class") == FINDING_CLASS_FALLBACK_VS_SPECIATION
    )
    updated["n_qualification_cells"] = len(qualification_cells)
    updated["floor_refusals"] = floor_refusals
    updated["qualification"] = (
        qualification_section(
            cells=cells,
            headline_residuals=residuals,
            engine_names=engine_names,
        )
        if qualification_cells
        else None
    )
    updated["note"] = (
        "divergence_label is a descriptive magnitude band only; "
        "never an acceptance verdict. Floor/sentinel/absent values "
        "are value_is_floor refusals and are excluded from residuals. "
        "Qualification (out-of-domain) residuals are NOT headline accuracy."
    )
    return updated


def _fmt_dex(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):+.3f}"


def render_report_markdown(report: Mapping[str, Any]) -> str:
    """Human-readable companion in the engine_crosscheck report shape."""

    receipt = report.get("receipt") or {}
    domain = report.get("domain") or {}
    lines = [
        "# Binary-pot battery — engine arm",
        "",
        f"- generated: `{report.get('generated_at')}`",
        (
            f"- authority: `{report.get('authority')}`; "
            f"certifies: `{str(report.get('certifies')).lower()}`; "
            f"calibrates: `{str(report.get('calibrates')).lower()}`"
        ),
        "- verdict: none — this report measures engine-vs-engine residuals and cannot pass or fail the model",
        "- `divergence_label` is a descriptive magnitude band only; never an acceptance verdict",
        f"- hostname: `{report.get('hostname')}`",
        (
            f"- floor refusals: `{report.get('n_floor_refusals', 0)}` "
            f"(`{REFUSAL_VALUE_IS_FLOOR}`; excluded from residuals)"
        ),
        (
            f"- fallback vs speciation: `{report.get('n_fallback_vs_speciation', 0)}` "
            f"(`{FINDING_CLASS_FALLBACK_VS_SPECIATION}`)"
        ),
        (
            f"- wall: `{receipt.get('wall_s'):.3f} s`; "
            f"cpu: `{receipt.get('cpu_s'):.3f} s`; "
            f"wall/cpu: `{receipt.get('wall_cpu_ratio')}`"
            if receipt.get("wall_s") is not None
            else "- wall/cpu: unavailable"
        ),
        (
            f"- temperature: `{domain.get('temperature_K', ['?', '?'])[0]:g}–"
            f"{domain.get('temperature_K', ['?', '?'])[1]:g} K` "
            f"({len(domain.get('temperatures_K') or [])} points)"
        ),
        (
            "- pO2: engine default"
            + (
                " plus commanded "
                + ", ".join(
                    f"{value:g} bar"
                    for value in (domain.get("po2_bar") or {}).get("commanded") or []
                )
                if (domain.get("po2_bar") or {}).get("commanded")
                else ""
            )
        ),
        "",
        "## Engine identity digests (`engines.local.toml`)",
        "",
    ]
    identities = receipt.get("engine_identities") or {}
    if identities:
        lines.extend(
            [
                "| engine | version | digest |",
                "|---|---|---|",
            ]
        )
        for key, block in sorted(identities.items()):
            if not isinstance(block, Mapping):
                continue
            lines.append(
                f"| `{key}` | `{block.get('version', '')}` | `{block.get('digest', '')}` |"
            )
    else:
        lines.append("No `engines.local.toml` identities were readable on this host.")

    lines.extend(
        [
            "",
            "## Engine availability",
            "",
            "| engine | available | takes fO2 | unavailable_reason |",
            "|---|---|---|---|",
        ]
    )
    for name, block in (report.get("engines") or {}).items():
        lines.append(
            f"| `{name}` | `{str(block.get('available')).lower()}` | "
            f"`{str(block.get('takes_fo2')).lower()}` | "
            f"{block.get('unavailable_reason') or '—'} |"
        )

    lines.extend(
        [
            "",
            "## Refusal matrix (pot × engine)",
            "",
            "Each cell is the dominant typed refusal for that pot×engine "
            "(or `ok` when every cell answered). Counts are cells, not species.",
            "",
        ]
    )
    engine_names = list((report.get("engines") or {}).keys()) or list(BATTERY_ENGINE_NAMES)
    header = "| pot | " + " | ".join(f"`{name}`" for name in engine_names) + " |"
    sep = "|---|" + "|".join("---" for _ in engine_names) + "|"
    lines.extend([header, sep])
    matrix = report.get("refusal_matrix") or {}
    for pot in report.get("pots") or []:
        pot_id = pot["pot_id"]
        cells = []
        for name in engine_names:
            cell = (matrix.get(pot_id) or {}).get(name) or {}
            reason = cell.get("dominant_reason")
            n_ok = cell.get("n_ok", 0)
            n_refused = cell.get("n_refused", 0)
            if reason is None and n_ok:
                label = f"ok ({n_ok})"
            elif reason is None:
                label = "—"
            else:
                label = f"{reason} ({n_refused}/{cell.get('n_cells', 0)})"
                note = cell.get("note")
                if reason == REFUSAL_COMPOSITION_PROJECTED and note:
                    label = f"{reason} ({n_refused}/{cell.get('n_cells', 0)}; {note})"
            cells.append(label)
        lines.append(f"| `{pot_id}` | " + " | ".join(cells) + " |")

    lines.extend(
        [
            "",
            "## Per-pot residual tables",
            "",
            "Matched in-envelope residuals only (both engines reported a finite "
            "positive value for the same species and quantity). "
            "A floor/sentinel/absent value is a `value_is_floor` refusal "
            "and is not scored.",
            "",
        ]
    )
    per_pot = report.get("per_pot_residuals") or {}
    for pot in report.get("pots") or []:
        pot_id = pot["pot_id"]
        table = per_pot.get(pot_id) or {}
        lines.extend(
            [
                f"### `{pot_id}`",
                "",
                (
                    f"- Kato Table 4 system: `{pot.get('kato_1993_table4_system') or 'none (recipe endmember)'}`"
                ),
                f"- composition wt%: `{pot.get('composition_wt_pct')}`",
                (
                    f"- matched residuals: `{table.get('n_matched', 0)}`; "
                    f"max |Δ| dex: `{_fmt_dex(table.get('max_abs_delta_dex')).lstrip('+')}`"
                ),
                "",
                "| engine_a | engine_b | n | median |Δ| dex | max |Δ| dex | label |",
                "|---|---|---:|---:|---:|---|",
            ]
        )
        pairs = table.get("engine_pairs") or []
        if not pairs:
            lines.append("| — | — | 0 | — | — | `no_matched_points` |")
        for pair in pairs:
            lines.append(
                "| {a} | {b} | {n} | {med} | {mx} | `{label}` |".format(
                    a=pair["engine_a"],
                    b=pair["engine_b"],
                    n=pair["n"],
                    med=_fmt_dex(pair["median_abs_delta_dex"]).lstrip("+"),
                    mx=_fmt_dex(pair["max_abs_delta_dex"]).lstrip("+"),
                    label=pair["divergence_label"],
                )
            )
        lines.append("")

    lines.extend(
        [
            "## Twenty largest in-envelope residuals",
            "",
            (
                "| pot | T K | pO2 | species | quantity | engine_a | engine_b | "
                "Δ dex (a−b) | label | finding_class |"
            ),
            "|---|---:|---|---|---|---|---|---:|---|---|",
        ]
    )
    top = report.get("largest_in_envelope_residuals") or []
    if not top:
        lines.append("| — | — | — | — | — | — | — | — | `no_matched_points` | — |")
    for row in top[:20]:
        po2 = row.get("po2_bar")
        po2_label = (
            "default" if row.get("po2_mode") == PO2_ENGINE_DEFAULT else f"{po2:g} bar"
        )
        finding = row.get("finding_class") or "—"
        lines.append(
            "| `{pot}` | {T:g} | {po2} | {species} | {qty} | `{a}` | `{b}` | "
            "{delta} | `{label}` | `{finding}` |".format(
                pot=row["pot_id"],
                T=float(row["temperature_K"]),
                po2=po2_label,
                species=row["species"],
                qty=row["quantity"],
                a=row["engine_a"],
                b=row["engine_b"],
                delta=_fmt_dex(row["delta_log10_a_minus_b"]),
                label=row["divergence_label"],
                finding=finding,
            )
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            (
                "The companion JSON contains every cell, typed refusal, and "
                "matched residual. Floor/sentinel/absent values are "
                f"`{REFUSAL_VALUE_IS_FLOOR}` (floor_value="
                f"{MELT_DISSOCIATION_PO2_MIN_BAR:g} bar pO2 clamp, or a "
                f"gas partial pressure ≥ {CATALOG_PHYSICAL_PRESSURE_CEILING_PA:g} Pa) "
                "and are excluded from residuals. "
                f"`{FINDING_CLASS_FALLBACK_VS_SPECIATION}` marks a pair where "
                "one engine reported an Antoine fallback authority "
                f"(`{_ANTOINE_FALLBACK_PREFIX}` / "
                "`vapor_pressure_backend_status=fallback`) and the other a "
                "speciation result. The Pa number itself is indistinguishable "
                "from a speciation value; the harness now copies the engine "
                "flags instead of dropping them. No result is used to change "
                "a coefficient."
            ),
            "",
        ]
    )
    n_floor = int(report.get("n_floor_refusals") or 0)
    if n_floor:
        lines.extend(
            [
                "Adapter exposure (not changed in this harness): the 1e-30 bar "
                "pO2 clamp lives in `simulator/melt_backend/alphamelts.py:5139` "
                "(`max(float(pO2_bar), 1e-30)`) and "
                "`simulator/melt_backend/thermoengine.py:430` "
                "(`pO2_bar=max(10.0 ** solved_fO2_log, 1e-30)`). For Si, "
                "`_activities_times_antoine` then applies "
                "`(pO2 / pO2_ref) ** (-1)` (`pO2_ref = 1e-9 bar`), so the "
                "clamp inverts to ~4.5e20 Pa at 1700 K. "
                "`EquilibriumResult.vapor_pressures_Pa` is the field. "
                "Recipe path: `simulator/core.py` "
                "`_refresh_vapor_pressures_from_kernel` replaces backend "
                "pressures with the builtin kernel; builtin "
                "`engines/builtin/vapor_pressure.py` uses the same "
                "`MELT_DISSOCIATION_PO2_MIN_BAR = 1e-30` clamp, so an "
                "extremely reducing recipe fO2 can still explode P_Si into "
                "evaporation flux. Direct `equilibrate()` consumers "
                "(this battery, engine_crosscheck) see the adapter number.",
                "",
            ]
        )
    qualification = report.get("qualification")
    if isinstance(qualification, Mapping):
        lines.extend(_render_qualification_markdown(qualification, engine_names))
    return "\n".join(lines)


def _render_qualification_markdown(
    qualification: Mapping[str, Any],
    engine_names: Sequence[str],
) -> list[str]:
    band = qualification.get("certified_band") or {}
    lines = [
        "## Qualification (out-of-domain) residuals",
        "",
        "**NOT headline accuracy.** MELTS-family engines record the domain-gate "
        "verdict (`authority=extrapolated`, `certified_band`) and then run anyway "
        "in an isolated subprocess with a hard timeout. A SIGABRT / nonzero "
        f"exit is `{REFUSAL_ENGINE_CRASH}`, never a hang or a silent skip.",
        "",
        (
            f"- published MELTS SiO2 band: `{band.get('sio2_wt_pct')}` wt% "
            f"(crash floor `{band.get('sio2_crash_floor_wt_pct')}` wt%)"
        ),
        (
            f"- published MELTS T band: `{band.get('temperature_K')}` K "
            "(800 °C subprocess min; 1700 K `T_calib_max_K`)"
        ),
        f"- citations: `{band.get('citations')}`",
        "",
        "### Per-engine qualification summary",
        "",
        "| engine | cells run | returned a number | crashed | timed out |",
        "|---|---:|---:|---:|---:|",
    ]
    summary = qualification.get("summary") or {}
    for name in engine_names:
        row = summary.get(name) or {}
        lines.append(
            f"| `{name}` | {row.get('n_cells', 0)} | {row.get('n_returned', 0)} | "
            f"{row.get('n_crashed', 0)} | {row.get('n_timed_out', 0)} |"
        )
    lines.extend(
        [
            "",
            "### Largest in-band vs out-of-band residual shift",
            "",
            "| engine | vs | in-band max \\|Δ\\| dex | out-of-band max \\|Δ\\| dex | out−in dex |",
            "|---|---|---:|---:|---:|",
        ]
    )
    per_engine = qualification.get("per_engine") or {}
    for name in engine_names:
        block = per_engine.get(name) or {}
        for key, peer in (
            ("residual_shift_vs_vaporock", "vaporock"),
        ):
            shift = block.get(key) or {}
            lines.append(
                "| `{engine}` | `{peer}` | {in_band} | {out_band} | {delta} |".format(
                    engine=name,
                    peer=peer,
                    in_band=_fmt_dex(shift.get("largest_in_band_abs_dex")).lstrip("+"),
                    out_band=_fmt_dex(shift.get("largest_out_of_band_abs_dex")).lstrip(
                        "+"
                    ),
                    delta=_fmt_dex(shift.get("out_minus_in_dex")),
                )
            )
    lines.extend(["", "### Gate verdicts (per engine)", ""])
    for name in engine_names:
        block = per_engine.get(name) or {}
        lines.extend(
            [
                f"#### `{name}`",
                "",
                (
                    "| pot | T K | gate valid | failed constraints | returned | "
                    "status | crash/timeout |"
                ),
                "|---|---:|---|---|---|---|---|",
            ]
        )
        verdicts = block.get("gate_verdicts") or []
        if not verdicts:
            lines.append("| — | — | — | — | — | — | — |")
        for row in verdicts:
            crash = "—"
            if row.get("refusal_reason") == REFUSAL_ENGINE_CRASH:
                crash = (
                    f"engine_crash signal={row.get('exit_signal')} "
                    f"exit={row.get('exit_code')}"
                )
            elif row.get("refusal_reason") == REFUSAL_TIMEOUT:
                crash = "engine_timeout"
            lines.append(
                "| `{pot}` | {T:g} | `{valid}` | `{failed}` | `{returned}` | "
                "`{status}` | {crash} |".format(
                    pot=row.get("pot_id"),
                    T=float(row.get("temperature_K") or 0.0),
                    valid=row.get("gate_valid"),
                    failed=",".join(str(x) for x in (row.get("failed_constraints") or [])),
                    returned=str(bool(row.get("returned_number"))).lower(),
                    status=row.get("status") or row.get("refusal_reason") or "—",
                    crash=crash,
                )
            )
        lines.append("")
    return lines


def write_reports(
    report: Mapping[str, Any],
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    dest = Path(output_dir) if output_dir is not None else REPORT_DIR
    dest.mkdir(parents=True, exist_ok=True)
    json_path = dest / f"{REPORT_STEM}.json"
    md_path = dest / f"{REPORT_STEM}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    md_path.write_text(render_report_markdown(report))
    return json_path, md_path
