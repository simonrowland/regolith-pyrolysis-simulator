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

import inspect
import json
import math
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from simulator.physical_constants import (
    CATALOG_PHYSICAL_PRESSURE_CEILING_PA,
    CELSIUS_TO_KELVIN_OFFSET,
    MELT_DISSOCIATION_PO2_MIN_BAR,
    PA_PER_BAR,
)
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
    "imcc_sf04",
    "imcc_sf04_ext",
)
IMCC_ENGINE_NAMES: tuple[str, ...] = ("imcc_sf04", "imcc_sf04_ext")
IMCC_MODEL_IDS: dict[str, str] = {
    "imcc_sf04": "IMCC-SF04",
    "imcc_sf04_ext": "IMCC-SF04-EXT",
}
IMCC_DATAPACK_RELATIVE: dict[str, str] = {
    "imcc_sf04": "data/melt_activity/imcc/imcc-sf04-v1.0.2.json",
    "imcc_sf04_ext": "data/melt_activity/imcc/imcc-sf04-ext-v4.json",
}
MELTS_FAMILY_ENGINES: tuple[str, ...] = ("alphamelts", "thermoengine")
ARM_HEADLINE = "headline"
ARM_QUALIFICATION = "qualification"

QUANTITY_ACTIVITY = "melt_activity"
QUANTITY_PRESSURE = "gas_partial_pressure_Pa"

PO2_ENGINE_DEFAULT = "engine_default"
PO2_COMMANDED = "commanded"

REFUSAL_OUT_OF_BASIS = "out_of_basis"
REFUSAL_COMPOSITION_PROJECTED = "composition_projected"
REFUSAL_MAJOR_SUM = "sum_below_95_wt_pct"
REFUSAL_NO_LIQUID = "no_liquid"
REFUSAL_TIMEOUT = "engine_timeout"
REFUSAL_ENGINE_CRASH = "engine_crash"
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
    "imcc_sf04": 15.0,
    "imcc_sf04_ext": 15.0,
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

_ISOLATED_CELL_BOOTSTRAP = (
    "import json,sys;"
    "from simulator.diagnostic_helpers.binary_pot_battery import "
    "run_isolated_cell_worker;"
    "run_isolated_cell_worker(json.load(sys.stdin))"
)


class BinaryPotBatteryError(RuntimeError):
    """Raised when the pot catalog or residual inputs violate a hard contract."""


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

    def as_payload(self) -> dict[str, Any]:
        return {"mode": self.mode, "po2_bar": self.po2_bar}


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
            "melt_activities": dict(self.melt_activities),
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
            ),
            status=str(payload.get("status") or ""),
            refusal_reason=payload.get("refusal_reason"),
            engine_status=payload.get("engine_status"),
            engine_reason=payload.get("engine_reason"),
            melt_activities=dict(payload.get("melt_activities") or {}),
            gas_partial_pressures_Pa=dict(
                payload.get("gas_partial_pressures_Pa") or {}
            ),
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
    payload = yaml.safe_load(pots_path.read_text(encoding="utf-8")) or {}
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
        engine_reason = status_reason or reason_code or message
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
                gas_partial_pressures_Pa={},
            )
        )
    return rewritten


# ---------------------------------------------------------------------------
# IMCC-SF04(+EXT) battery adapter
# ---------------------------------------------------------------------------


class _ImccBatteryBackend:
    """Thin MeltBackend-shaped wrapper around ``imcc_sf04.adapter.evaluate``.

    Not registered in ``simulator.backends``: IMCC is a diagnostic shadow
    and has no ledger authority. The engine arm is the first caller.
    """

    supports_intrinsic_fO2 = False

    def __init__(self, engine_name: str) -> None:
        if engine_name not in IMCC_MODEL_IDS:
            raise BinaryPotBatteryError(f"unknown IMCC engine {engine_name!r}")
        self.engine_name = engine_name
        self.model_id = IMCC_MODEL_IDS[engine_name]
        self._pack: Any = None
        self._gas: Any = None
        self._gas_error: str | None = None
        self._identity: dict[str, str] = {
            "name": self.model_id,
            "version": "",
            "digest": "",
        }
        self._load()

    def _datapack_path(self) -> Path:
        return REPO_ROOT / IMCC_DATAPACK_RELATIVE[self.engine_name]

    def _load(self) -> None:
        from simulator.melt_backend.imcc_sf04 import load_datapack

        pack = load_datapack(self._datapack_path())
        self._pack = pack
        self._identity = {
            "name": str(pack.model_id),
            "version": str(pack.version),
            "digest": str(
                getattr(pack.kernel_datapack, "published_manifest_sha256", "") or ""
            ),
            "model_id": str(pack.model_id),
            "datapack": str(self._datapack_path().relative_to(REPO_ROOT)),
        }
        try:
            from simulator.melt_backend.imcc_sf04.gas import load_gas_datapack

            self._gas = load_gas_datapack()
            self._gas_error = None
        except Exception as exc:  # noqa: BLE001 - gas is optional; activities still run
            self._gas = None
            self._gas_error = f"{type(exc).__name__}: {exc}"

    def equilibrate(
        self,
        temperature_C: float,
        composition_kg: Mapping[str, float] | None = None,
        fO2_log: float | None = None,
        pressure_bar: float = 1.0e-6,
        *,
        composition_mol: Mapping[str, float] | None = None,
        **_unused: object,
    ) -> Any:
        from types import SimpleNamespace

        from simulator.melt_backend.imcc_sf04 import evaluate
        from simulator.melt_backend.imcc_sf04.kernel import ImccRefusal

        del composition_mol, pressure_bar
        if self._pack is None:
            raise RuntimeError("IMCC datapack failed to load")
        composition_wt = {
            str(name): float(mass_kg) * 100.0
            for name, mass_kg in dict(composition_kg or {}).items()
            if float(mass_kg) > 0.0
        }
        total = sum(composition_wt.values())
        temperature_K = float(temperature_C) + CELSIUS_TO_KELVIN_OFFSET
        enable_sp = self.engine_name == "imcc_sf04_ext"
        result = evaluate(
            composition_wt,
            temperature_K,
            self._pack,
            basis=total if total > 0.0 else None,
            basis_type="wt",
            enable_sp_extension=enable_sp,
            allow_extrapolation=True,
            allow_out_of_envelope=True,
        )
        activities: dict[str, float] = {}
        for name, value in zip(result.parent_oxides, result.parent_activity):
            number = _finite_float(value)
            if number is not None and number > 0.0:
                activities[str(name)] = number
        notices: list[dict[str, Any]] = []
        if result.extrapolated:
            notices.append(
                {
                    "kind": "imcc_temperature_extrapolated",
                    "authority": AUTHORITY_EXTRAPOLATED,
                    "reason": "T outside datapack T_domain_K; evaluate(allow_extrapolation=True)",
                }
            )
        envelope = getattr(getattr(result, "labels", None), "envelope_status", None)
        if envelope == "outside_validated":
            notices.append(
                {
                    "kind": "imcc_composition_outside_validated_envelope",
                    "authority": AUTHORITY_EXTRAPOLATED,
                    "reason": "X_Me2O above the validated 0.5 bound; evaluate(allow_out_of_envelope=True)",
                }
            )
        pressures: dict[str, float] = {}
        gas_error: str | None = None
        if self._gas is None:
            gas_error = self._gas_error or "imcc_gas_datapack_unavailable"
        else:
            try:
                from simulator.melt_backend.imcc_sf04.gas import evaluate_gas

                fo2_bar = (
                    10.0 ** float(fO2_log)
                    if fO2_log is not None and math.isfinite(float(fO2_log))
                    else 10.0 ** _DEFAULT_FO2_LOG
                )
                gas_bar = evaluate_gas(
                    activities,
                    temperature_K,
                    fo2_bar,
                    self._gas,
                    parent_oxides=result.parent_oxides,
                    allow_extrapolation=True,
                )
                for name, value in dict(gas_bar).items():
                    number = _finite_float(value)
                    if number is not None and number > 0.0:
                        pressures[str(name)] = number * PA_PER_BAR
            except ImccRefusal as exc:
                gas_error = f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"
            except Exception as exc:  # noqa: BLE001 - gas is optional
                gas_error = f"{type(exc).__name__}: {exc}"
        if gas_error:
            notices.append(
                {
                    "kind": "imcc_gas_unavailable",
                    "authority": None,
                    "reason": gas_error,
                }
            )
        labels = getattr(result, "labels", None)
        identity = dict(getattr(labels, "identity", None) or {})
        diagnostics = {
            "imcc_model_id": identity.get("model_id") or self.model_id,
            "imcc_datapack_version": identity.get("datapack_version")
            or self._identity.get("version"),
            "imcc_extrapolated": bool(result.extrapolated),
            "imcc_envelope_status": envelope,
            "imcc_notices": notices,
        }
        return SimpleNamespace(
            status="ok",
            diagnostics=diagnostics,
            warnings=[],
            activity_coefficients=activities,
            vapor_pressures_Pa=pressures,
            liquid_fraction=1.0,
            phase_assemblage_available=True,
            imcc_notices=notices,
            imcc_model_id=identity.get("model_id") or self.model_id,
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


def run_isolated_cell_worker(payload: Mapping[str, Any]) -> None:
    """Child-process entry: one cell, JSON on stdout, crash becomes a signal."""

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
    )
    handle = open_battery_engine(str(payload["engine"]))
    if (
        bool(payload.get("qualification"))
        and handle.name in MELTS_FAMILY_ENGINES
        and handle.backend is not None
    ):
        _bypass_melts_domain_gate(handle.backend)
    cell = equilibrate_cell(
        handle,
        pot,
        temperature_K=float(payload["temperature_K"]),
        po2=po2,
        timeout_s=_finite_float(payload.get("timeout_s")),
        qualification=bool(payload.get("qualification")),
        isolated=False,
        arm=str(payload.get("arm") or ARM_HEADLINE),
    )
    sys.stdout.write(json.dumps(cell.as_payload(), default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()


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
        model_id=IMCC_MODEL_IDS.get(handle.name),
    )


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
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(REPO_ROOT))
    pythonpath = env.get("PYTHONPATH") or ""
    if str(REPO_ROOT) not in pythonpath.split(os.pathsep):
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT), pythonpath] if pythonpath else [str(REPO_ROOT)]
        )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _ISOLATED_CELL_BOOTSTRAP],
            input=json.dumps(payload),
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=float(timeout_s) + 2.0,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        child = getattr(exc, "process", None)
        if child is not None and getattr(child, "pid", None):
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        return _crash_cell_from_returncode(
            handle=handle,
            pot=pot,
            temperature_K=temperature_K,
            po2=po2,
            wall0=wall0,
            cpu0=cpu0,
            hostname=hostname,
            returncode=None,
            timed_out=True,
            stderr=str(getattr(exc, "stderr", "") or ""),
            arm=arm,
            notices=notices,
            authority=authority,
            certified_band=certified_band,
        )
    if proc.returncode != 0:
        return _crash_cell_from_returncode(
            handle=handle,
            pot=pot,
            temperature_K=temperature_K,
            po2=po2,
            wall0=wall0,
            cpu0=cpu0,
            hostname=hostname,
            returncode=proc.returncode,
            timed_out=False,
            stderr=proc.stderr or proc.stdout or "",
            arm=arm,
            notices=notices,
            authority=authority,
            certified_band=certified_band,
        )
    try:
        raw = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        return _crash_cell_from_returncode(
            handle=handle,
            pot=pot,
            temperature_K=temperature_K,
            po2=po2,
            wall0=wall0,
            cpu0=cpu0,
            hostname=hostname,
            returncode=proc.returncode,
            timed_out=False,
            stderr=f"unparseable worker stdout ({exc}): {proc.stdout[:400]}",
            arm=arm,
            notices=notices,
            authority=authority,
            certified_band=certified_band,
        )
    cell = EquilibrateCell.from_payload(raw)
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

    if name in IMCC_ENGINE_NAMES:
        return _ImccBatteryBackend(name)
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
            model_id=kwargs.pop("model_id", None) or IMCC_MODEL_IDS.get(handle.name),
            **kwargs,
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
    physical_pressure_bar = _DEFAULT_PRESSURE_BAR
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
        if (
            qualification
            and status == "refusal"
            and refusal not in {REFUSAL_TIMEOUT, REFUSAL_ENGINE_CRASH}
        ):
            result_diagnostics = dict(getattr(result, "diagnostics", None) or {})
            gate_name = (
                result_diagnostics.get("backend_failure_reason_code")
                or result_diagnostics.get("backend_status_reason")
                or engine_reason
                or refusal
            )
            refusal = REFUSAL_GATE_REFUSED_IN_ADAPTER
            engine_reason = f"{gate_name}"
        activities, pressures = extract_reported_quantities(result)
        vapor_authority = extract_vapor_authority(result)
        result_notices = list(getattr(result, "imcc_notices", None) or [])
        diagnostics = dict(getattr(result, "diagnostics", None) or {})
        result_notices.extend(list(diagnostics.get("imcc_notices") or []))
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
            melt_activities=activities,
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
            notices=result_notices,
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
            "residual_shift_vs_imcc": _residual_shift_in_vs_out_of_band(
                headline_residuals,
                qual_residuals,
                engine=name,
                peer="imcc_sf04",
            ),
            "residual_shift_vs_imcc_ext": _residual_shift_in_vs_out_of_band(
                headline_residuals,
                qual_residuals,
                engine=name,
                peer="imcc_sf04_ext",
            ),
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
            ("residual_shift_vs_imcc", "imcc_sf04"),
            ("residual_shift_vs_imcc_ext", "imcc_sf04_ext"),
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
