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
import socket
import subprocess
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

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
)

QUANTITY_ACTIVITY = "melt_activity"
QUANTITY_PRESSURE = "gas_partial_pressure_Pa"

PO2_ENGINE_DEFAULT = "engine_default"
PO2_COMMANDED = "commanded"

REFUSAL_OUT_OF_BASIS = "out_of_basis"
REFUSAL_MAJOR_SUM = "sum_below_95_wt_pct"
REFUSAL_NO_LIQUID = "no_liquid"
REFUSAL_TIMEOUT = "engine_timeout"
REFUSAL_UNAVAILABLE = "unavailable"

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
_UNAVAILABLE_TOKENS = frozenset(
    {"unavailable", "backend_unavailable", "not_initialized"}
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
}


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
        }


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

    from simulator.state import MOLAR_MASS

    kg: dict[str, float] = {}
    mol: dict[str, float] = {}
    for oxide, wt in composition_wt_pct.items():
        mass_kg = float(wt) / 100.0
        kg[oxide] = mass_kg
        if oxide not in MOLAR_MASS:
            raise BinaryPotBatteryError(
                f"{oxide} has no simulator molar mass"
            )
        molar_mass = float(MOLAR_MASS[oxide])
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


def extract_reported_quantities(
    result: Any,
) -> tuple[dict[str, float], dict[str, float]]:
    """Record EquilibriumResult fields the engine actually exposed.

    Melt activities live on the legacy field ``activity_coefficients``.
    Gas partial pressures live on ``vapor_pressures_Pa``. Extra attributes
    are not invented.
    """

    activities: dict[str, float] = {}
    for name, value in dict(getattr(result, "activity_coefficients", None) or {}).items():
        number = _finite_float(value)
        if number is not None and number > 0.0:
            activities[str(name)] = number
    pressures: dict[str, float] = {}
    for name, value in dict(getattr(result, "vapor_pressures_Pa", None) or {}).items():
        number = _finite_float(value)
        if number is not None and number > 0.0:
            pressures[str(name)] = number
    return activities, pressures


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
    if any(token in text for token in _MAJOR_SUM_TOKENS):
        return REFUSAL_MAJOR_SUM
    if any(token in text for token in _OUT_OF_BASIS_TOKENS):
        return REFUSAL_OUT_OF_BASIS
    if any(token in text for token in _NO_LIQUID_TOKENS):
        return REFUSAL_NO_LIQUID
    if any(token in text for token in _UNAVAILABLE_TOKENS):
        return REFUSAL_UNAVAILABLE
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
        reason_code = str(getattr(error, "reason_code", "") or "")
        status_reason = str(getattr(error, "backend_status_reason", "") or "")
        message = str(error)
        engine_reason = status_reason or reason_code or message
        haystack = _haystack(type(error).__name__, reason_code, status_reason, message)
        if "timeout" in type(error).__name__.lower() or isinstance(error, TimeoutError):
            return "refusal", REFUSAL_TIMEOUT, engine_reason
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
        or ""
    )
    engine_reason = structured or ("; ".join(str(w) for w in warnings) if warnings else engine_status)
    haystack = _haystack(engine_status, structured, warnings, diagnostics)

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
) -> EquilibrateCell:
    """One pot × engine × T × pO2 call. Refusals are rows, never exceptions."""

    hostname = _hostname()
    wall0 = time.perf_counter()
    cpu0 = time.process_time()

    def _done(**kwargs: Any) -> EquilibrateCell:
        return EquilibrateCell(
            pot_id=pot.pot_id,
            engine=handle.name,
            temperature_K=float(temperature_K),
            po2=po2,
            wall_s=time.perf_counter() - wall0,
            cpu_s=time.process_time() - cpu0,
            hostname=hostname,
            **kwargs,
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

    composition_kg, composition_mol = composition_kg_and_mol(pot.composition_wt_pct)
    temperature_C = float(temperature_K) - 273.15
    fo2_log = _fo2_log_for_request(handle, po2)
    timeout = float(timeout_s or _ENGINE_OUTER_TIMEOUT_S.get(handle.name, 30.0))
    kwargs: dict[str, Any] = {
        "temperature_C": temperature_C,
        "composition_kg": composition_kg,
        "composition_mol": composition_mol,
        "pressure_bar": _DEFAULT_PRESSURE_BAR,
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

    try:
        result = _call_with_hard_timeout(
            lambda: handle.backend.equilibrate(**kwargs),
            timeout,
        )
        status, refusal, engine_reason = classify_equilibrate_outcome(result)
        activities, pressures = extract_reported_quantities(result)
        return _done(
            status=status,
            refusal_reason=refusal,
            engine_status=str(getattr(result, "status", None) or status),
            engine_reason=engine_reason,
            melt_activities=activities,
            gas_partial_pressures_Pa=pressures,
            liquid_fraction=_finite_float(getattr(result, "liquid_fraction", None)),
        )
    except Exception as exc:  # noqa: BLE001 - typed refusal, never a hang/crash
        status, refusal, engine_reason = classify_equilibrate_outcome(error=exc)
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


def run_engine_arm(
    *,
    pots_path: Path | None = None,
    engine_names: Sequence[str] = BATTERY_ENGINE_NAMES,
    handles: Mapping[str, EngineHandle] | None = None,
    pots: Sequence[BinaryPot] | None = None,
    grid: BatteryGrid | None = None,
) -> dict[str, Any]:
    """Run every pot × engine × T × pO2 cell and build the report."""

    if pots is None or grid is None:
        loaded_pots, loaded_grid = load_binary_pots(pots_path)
        pots = pots or loaded_pots
        grid = grid or loaded_grid
    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    hostname = _hostname()
    resolved = dict(handles) if handles is not None else probe_battery_engines(engine_names)

    cells: list[EquilibrateCell] = []
    for pot in pots:
        for name in engine_names:
            handle = resolved.get(name) or open_battery_engine(name)
            resolved[name] = handle
            requests = po2_requests_for_engine(grid, takes_fo2=handle.takes_fo2)
            for temperature_K in grid.temperatures_K:
                for po2 in requests:
                    cells.append(
                        equilibrate_cell(
                            handle,
                            pot,
                            temperature_K=float(temperature_K),
                            po2=po2,
                        )
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
        pots=pots,
        grid=grid,
        handles=resolved,
        cells=cells,
        hostname=hostname,
        wall_s=time.perf_counter() - wall0,
        cpu_s=time.process_time() - cpu0,
        engine_names=engine_names,
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
            matrix[pot.pot_id][engine] = {
                "n_cells": len(group),
                "n_ok": n_ok,
                "n_refused": n_refused,
                "dominant_reason": dominant,
                "reasons": dict(sorted(reasons.items())),
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
) -> dict[str, Any]:
    residuals = pairwise_residuals(cells)
    identities = _engine_identities_from_toml()
    engines_block: dict[str, Any] = {}
    for name in engine_names:
        handle = handles.get(name)
        engines_block[name] = {
            "available": bool(handle.available) if handle is not None else False,
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
        "refusal_matrix": _refusal_matrix(pots, engine_names, cells),
        "per_pot_residuals": _per_pot_residual_tables(pots, residuals),
        "largest_in_envelope_residuals": residuals[:20],
        "n_cells": len(cells),
        "n_ok": sum(1 for cell in cells if cell.status == "ok"),
        "n_refused": sum(1 for cell in cells if cell.status == "refusal"),
        "n_matched_residuals": len(residuals),
        "cells": [cell.as_payload() for cell in cells],
        "note": (
            "divergence_label is a descriptive magnitude band only; "
            "never an acceptance verdict."
        ),
    }


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
            cells.append(label)
        lines.append(f"| `{pot_id}` | " + " | ".join(cells) + " |")

    lines.extend(
        [
            "",
            "## Per-pot residual tables",
            "",
            "Matched in-envelope residuals only (both engines reported a finite "
            "positive value for the same species and quantity).",
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
                "Δ dex (a−b) | label |"
            ),
            "|---|---:|---|---|---|---|---|---:|---|",
        ]
    )
    top = report.get("largest_in_envelope_residuals") or []
    if not top:
        lines.append("| — | — | — | — | — | — | — | — | `no_matched_points` |")
    for row in top[:20]:
        po2 = row.get("po2_bar")
        po2_label = (
            "default" if row.get("po2_mode") == PO2_ENGINE_DEFAULT else f"{po2:g} bar"
        )
        lines.append(
            "| `{pot}` | {T:g} | {po2} | {species} | {qty} | `{a}` | `{b}` | "
            "{delta} | `{label}` |".format(
                pot=row["pot_id"],
                T=float(row["temperature_K"]),
                po2=po2_label,
                species=row["species"],
                qty=row["quantity"],
                a=row["engine_a"],
                b=row["engine_b"],
                delta=_fmt_dex(row["delta_log10_a_minus_b"]),
                label=row["divergence_label"],
            )
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            (
                "The companion JSON contains every cell, typed refusal, and "
                "matched residual. No result is clipped or used to change a "
                "coefficient."
            ),
            "",
        ]
    )
    return "\n".join(lines)


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
