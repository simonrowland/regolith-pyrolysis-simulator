"""PT-0 reduced-real determinism proof helpers.

Opt-in only. This module builds canonical request keys and a minimal
in-memory write-through/replay store for the determinism proof gate.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import logging
import math
import os
import sqlite3
import subprocess
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from decimal import InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

from simulator.backend_names import (
    ANALYTICAL_BACKEND_CLASS_DISPLAY_NAME,
    ANALYTICAL_BACKEND_QUALIFIED_CLASS_NAME,
    canonical_backend_class_name,
)
from simulator.chemistry.kernel import ChemistryIntent
from simulator.corpus_version import (
    current_corpus_version,
    interoperable_corpus_versions,
)
from simulator.config import functional_data_yaml_digest
from simulator.grind_preflight import (
    assert_strict_vapor_pt1_row,
)
from simulator.fe_redox import (
    KRESS91_FO2_KEY_REFERENCE_T_K,
    kress91_referenced_log_fO2,
)
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.sulfsat import SulfurSaturationResult


_LOGGER = logging.getLogger(__name__)


SCHEMA_VERSION = "pt0-reduced-real-determinism-v1"
PHYSICS_BUCKET_SCHEMA_VERSION = "pt1-reduced-real-physics-bucket-v2"
PT1_STORE_SCHEMA_VERSION = "pt1-reduced-real-equilibrium-store-v1"
PT1_EQUILIBRIUM_TABLE = "reduced_real_equilibrium_payloads"
PT1_METADATA_TABLE = "reduced_real_metadata"
PT1_READ_ONLY_BASE_ALIAS = "pt1_read_only_base"
DEFAULT_SHARD_BUSY_TIMEOUT_MS = 60_000.0
PHYSICS_BUCKET_LADDER_RUNGS = (
    ("h40", 4.0),
    ("h30", 3.0),
)
PHYSICS_BUCKET_CONTROL_LADDER_RUNGS = (
    ("h40c", 4.0),
    ("h30c", 3.0),
)
PHYSICS_BUCKET_ALL_LADDER_RUNGS = (
    PHYSICS_BUCKET_LADDER_RUNGS + PHYSICS_BUCKET_CONTROL_LADDER_RUNGS
)
CONTROL_RUNG_SIO_ERROR_BUDGET_TERM = (
    "po2_control_coarsening_sio_vapor_relative_error"
)
CONTROL_RUNG_SIO_RELATIVE_ERROR_BUDGET = 1.0e-3
CONTROL_RUNG_SIO_PO2_KNEE_BAR = 1.0e-9
CACHE_STATES = (
    "cached_exact",
    "cached_physics_bucket",
    "cached_interpolated",
    "live_fill",
)
APPROXIMATE_REDUCED_REAL_CACHE_STATES = frozenset(
    {"cached_interpolated", "cached_physics_bucket"}
)
_CLEANED_MELT_ACCOUNT = "process.cleaned_melt"
_T_K_QUANTUM = 0.01
_FO2_LOG_QUANTUM = 0.001
_PRESSURE_BAR_QUANTUM = 0.00001
_COMPOSITION_SIG_FIGS = 5
_TRACE_CUTOFF = 1.0e-12
_GATE_CURVE_KEY_T_K_SENTINEL = 298.15
_CACHEABLE_EQUILIBRIUM_STATUSES = frozenset({"ok"})
_CACHEABLE_GATE_STATUSES = frozenset({"ok"})
_CACHEABLE_GATE_CALIBRATION_STATUSES = frozenset({"in_range"})
_SOURCE_MODULE_SET_ID = "equilibrium-vapor-melt-backend-v3"
# Modules that can change the equilibrium_post_record payload: core branch
# selection/post hooks, cache serialization, kernel dispatch contracts,
# melt-backend adapters, evaporation curve shaping, AlphaMELTS diagnostics,
# and builtin/VapoRock vapor, flux, or backend-equilibrium providers. Excludes
# unrelated campaign/UI code so cross-commit cache reuse survives non-payload
# edits.
_SOURCE_MODULE_PATTERNS = (
    "simulator/core.py",
    "simulator/evaporation.py",
    "simulator/optimize/recipe.py",
    "simulator/reduced_real_determinism.py",
    "simulator/chemistry/kernel/*.py",
    "simulator/melt_backend/*.py",
    "engines/alphamelts/*.py",
    "engines/builtin/_common.py",
    "engines/builtin/backend_equilibrium.py",
    "engines/builtin/evaporation_flux.py",
    "engines/builtin/vapor_pressure.py",
    "engines/builtin/stage0_pretreatment.py",
    "engines/builtin/foulant_disposition.py",
    # data/foulant_thermo.yaml and data/stage0_carbon_partition.yaml stay
    # cache-inert while disposition remains diagnostic-only and absent from
    # cached result payloads; add explicit optional digests if bakeoff reporting
    # is ever cached.
    "engines/vaporock/*.py",
)
_ALPHAMELTS_AUTHORIZED_NAME = "alphamelts"
_ALPHAMELTS_BACKEND_NAME = "AlphaMELTSBackend"
_ALPHAMELTS_BACKEND_CLASS = (
    "simulator.melt_backend.alphamelts.AlphaMELTSBackend"
)
_ALPHAMELTS_PROVIDER_ID = "alphamelts-diagnostic"
_ALPHAMELTS_DEFAULT_MODEL = "MELTSv1.0.2"
_ALPHAMELTS_DEFAULT_MODE = "subprocess"
_THERMOENGINE_AUTHORIZED_NAME = 'thermoengine'
_THERMOENGINE_BACKEND_NAME = 'ThermoEngineBackend'
_THERMOENGINE_BACKEND_CLASS = (
    'simulator.melt_backend.thermoengine.ThermoEngineBackend'
)
_THERMOENGINE_DEFAULT_MODE = 'thermoengine'
_BUILTIN_BACKEND_EQUILIBRIUM_PROVIDER_ID = "builtin-backend-equilibrium"
_INTERNAL_ANALYTICAL_BACKEND_RUNTIME_NAME = "InternalAnalyticalBackend"
_INTERNAL_ANALYTICAL_BACKEND_SERIALIZED_NAME = ANALYTICAL_BACKEND_CLASS_DISPLAY_NAME
_INTERNAL_ANALYTICAL_BACKEND_SERIALIZED_CLASS = ANALYTICAL_BACKEND_QUALIFIED_CLASS_NAME


@dataclasses.dataclass(frozen=True)
class ControlQuantization:
    """Session-scoped reduced-real control/key quantization."""

    t_k_quantum: float
    pressure_bar_quantum: float
    log_fo2_quantum: float
    composition_sig_figs: int

    PRODUCTION: ClassVar["ControlQuantization"]

    def __post_init__(self) -> None:
        for name in (
            "t_k_quantum",
            "pressure_bar_quantum",
            "log_fo2_quantum",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be a positive finite quantum")
            object.__setattr__(self, name, value)
        sig_figs = self.composition_sig_figs
        if not isinstance(sig_figs, int) or sig_figs < 1:
            raise ValueError("composition_sig_figs must be an int >= 1")

    @classmethod
    def from_name(cls, name: str) -> "ControlQuantization":
        normalized = _normalize_control_quantization_name(name)
        try:
            return _CONTROL_QUANTIZATION_PRESETS[normalized]
        except KeyError as exc:
            choices = ", ".join(sorted(_CONTROL_QUANTIZATION_PRESETS))
            raise ValueError(
                f"unknown control quantization tier {name!r}; "
                f"expected one of {choices}"
            ) from exc

    @property
    def t_k_decimals(self) -> int:
        return _quantize_decimals(self.t_k_quantum)

    @property
    def pressure_bar_decimals(self) -> int:
        return _quantize_decimals(self.pressure_bar_quantum)

    @property
    def log_fo2_decimals(self) -> int:
        return _quantize_decimals(self.log_fo2_quantum)


def _normalize_control_quantization_name(name: str) -> str:
    normalized = str(name).strip().lower().replace("-", "_")
    if not normalized:
        raise ValueError("control quantization tier name must be non-empty")
    return normalized


_PRODUCTION_QUANTIZATION = ControlQuantization(
    t_k_quantum=_T_K_QUANTUM,
    pressure_bar_quantum=_PRESSURE_BAR_QUANTUM,
    log_fo2_quantum=_FO2_LOG_QUANTUM,
    composition_sig_figs=_COMPOSITION_SIG_FIGS,
)
_CONTROL_QUANTIZATION_PRESETS: dict[str, ControlQuantization] = {
    "xx_coarse": ControlQuantization(
        t_k_quantum=10.0,
        pressure_bar_quantum=0.01,
        log_fo2_quantum=0.1,
        composition_sig_figs=2,
    ),
    "coarse": ControlQuantization(
        t_k_quantum=1.0,
        pressure_bar_quantum=0.001,
        log_fo2_quantum=0.01,
        composition_sig_figs=3,
    ),
    "medium": ControlQuantization(
        t_k_quantum=0.1,
        pressure_bar_quantum=0.0001,
        log_fo2_quantum=0.005,
        composition_sig_figs=4,
    ),
    "fine": _PRODUCTION_QUANTIZATION,
}
ControlQuantization.PRODUCTION = _PRODUCTION_QUANTIZATION


class PT0CacheMiss(RuntimeError):
    """Replay requested a key that the write-through run did not capture."""


class PT0CacheCollision(RuntimeError):
    """One canonical key produced different payload bytes."""


class PT1PersistentStoreCorrupt(PT0CacheCollision):
    """Persistent PT-1 row failed verify-on-hit integrity checks."""


class PT0InvalidControls(RuntimeError):
    """PT-0 control quantization received a non-finite process value."""


class PT0NonFinitePayload(ValueError):
    """PT-0 payload canonicalization received a non-finite value."""


def _authoritative_melt_fO2_log(sim: Any) -> float:
    reader = getattr(sim, "_current_melt_redox_fO2_log", None)
    if callable(reader):
        value = reader()
    else:
        reservoir = getattr(getattr(sim, "melt", None), "oxygen_reservoir", None)
        value = getattr(reservoir, "melt_intrinsic_fO2_log", None)
        if value is None:
            value = getattr(getattr(sim, "melt", None), "melt_fO2_log", None)
    try:
        fO2_log = float(value)
    except (TypeError, ValueError) as exc:
        raise PT0InvalidControls(
            "missing authoritative melt fO2_log for PT-0 cache key"
        ) from exc
    if not math.isfinite(fO2_log):
        raise PT0InvalidControls(
            "non-finite authoritative melt fO2_log for PT-0 cache key: "
            f"{value!r}"
        )
    return fO2_log


def _melt_fO2_log_at_gate_key_reference_T(sim: Any, fO2_log: float) -> float:
    reference_reader = getattr(sim, "_current_melt_redox_reference_T_K", None)
    reference_T_K = reference_reader() if callable(reference_reader) else None
    return kress91_referenced_log_fO2(
        fO2_log,
        reference_T_K=reference_T_K,
        target_T_K=KRESS91_FO2_KEY_REFERENCE_T_K,
    )


class PT0DeterminismStore:
    """PT-0 capture/replay store, optionally backed by the PT-1 SQLite DB."""

    def __init__(
        self,
        mode: str = "capture",
        *,
        db_path: str | Path | None = None,
        read_only_base_db_path: str | Path | None = None,
        strict_vapor_gate: bool = False,
        control_quantization: ControlQuantization | None = None,
    ) -> None:
        if mode not in {"capture", "replay"}:
            raise ValueError("PT0DeterminismStore mode must be capture or replay")
        self.mode = mode
        self.persistent_path = Path(db_path) if db_path is not None else None
        self.read_only_base_db_path = (
            Path(read_only_base_db_path)
            if read_only_base_db_path is not None
            else None
        )
        self.persistent_store = (
            PT1PersistentEquilibriumStore(
                self.persistent_path,
                read_only_base_db_path=self.read_only_base_db_path,
                strict_vapor_gate=strict_vapor_gate,
                control_quantization=control_quantization,
            )
            if self.persistent_path is not None
            else None
        )
        self.entries: dict[str, dict[str, Any]] = {}
        self.physics_bucket_entries: dict[str, str] = {}
        self.capture_sequence: list[dict[str, Any]] = []
        self.replay_sequence: list[dict[str, Any]] = []
        self.misses: list[dict[str, Any]] = []
        self.cache_events: list[dict[str, str]] = []
        self.hits: int = 0
        self.live_fills: int = 0
        self.last_cache_state: str | None = None
        self.quantize_live_controls: bool = True
        self.cache_tier_ceiling: str = "cached_interpolated"
        self.cached_real_miss_policy: str | None = None
        self.strict_vapor_gate = bool(strict_vapor_gate)
        self._control_quantization = (
            control_quantization or _PRODUCTION_QUANTIZATION
        )

    @property
    def control_quantization(self) -> ControlQuantization:
        return self._control_quantization

    @property
    def capture_enabled(self) -> bool:
        return self.mode == "capture"

    @property
    def replay_enabled(self) -> bool:
        return self.mode == "replay"

    @property
    def write_through_enabled(self) -> bool:
        return self.capture_enabled and self.persistent_store is not None

    def clone_for_replay(self) -> "PT0DeterminismStore":
        clone = PT0DeterminismStore(
            "replay",
            db_path=self.persistent_path,
            read_only_base_db_path=self.read_only_base_db_path,
            strict_vapor_gate=self.strict_vapor_gate,
            control_quantization=self._control_quantization,
        )
        clone.entries = copy.deepcopy(self.entries)
        clone.physics_bucket_entries = copy.deepcopy(self.physics_bucket_entries)
        clone.capture_sequence = copy.deepcopy(self.capture_sequence)
        clone.quantize_live_controls = self.quantize_live_controls
        clone.cache_tier_ceiling = self.cache_tier_ceiling
        clone.cached_real_miss_policy = self.cached_real_miss_policy
        return clone

    def quantized_controls(
        self,
        sim: Any,
        *,
        fO2_log: float | None,
    ) -> dict[str, float | None]:
        melt_temperature_C = float(sim.melt.temperature_C)
        T_K = _quantize(
            melt_temperature_C + 273.15,
            self._control_quantization.t_k_quantum,
            self._control_quantization.t_k_decimals,
        )
        if T_K is None:
            raise PT0InvalidControls(
                "non-finite melt temperature passed to PT-0 quantization: "
                f"{melt_temperature_C!r}"
            )
        melt_pressure_mbar = float(sim.melt.p_total_mbar)
        pressure_bar = _quantize(
            melt_pressure_mbar / 1000.0,
            self._control_quantization.pressure_bar_quantum,
            self._control_quantization.pressure_bar_decimals,
        )
        if pressure_bar is None:
            raise PT0InvalidControls(
                "non-finite melt pressure passed to PT-0 quantization: "
                f"{melt_pressure_mbar!r} mbar"
            )
        if fO2_log is None:
            fO2_log = _authoritative_melt_fO2_log(sim)
        quantized_fO2_log = _quantize(
            fO2_log,
            self._control_quantization.log_fo2_quantum,
            self._control_quantization.log_fo2_decimals,
        )
        if quantized_fO2_log is None:
            raise PT0InvalidControls(
                "non-finite fO2_log passed to PT-0 quantization: "
                f"{fO2_log!r}"
            )
        return {
            "temperature_C": None if T_K is None else float(T_K) - 273.15,
            "pressure_bar": pressure_bar,
            "fO2_log": quantized_fO2_log,
        }

    def quantized_pO2_bar(
        self,
        sim: Any,
        *,
        pO2_bar: float | None = None,
    ) -> float:
        # _sigfig returns None on non-finite input, so a non-finite commanded
        # pO2 would otherwise leak a None into the control surface (the same
        # invalid-control class as T_K / pressure / fO2 above). Commanded 0.0
        # (controlled-O2 off) is finite and preserved by _sigfig; only NaN/inf
        # is refused.
        commanded_pO2_bar = float(
            sim._commanded_pO2_bar() if pO2_bar is None else pO2_bar
        )
        quantized = _sigfig(
            commanded_pO2_bar,
            self._control_quantization.composition_sig_figs,
        )
        if quantized is None:
            raise PT0InvalidControls(
                "non-finite commanded pO2 passed to PT-0 quantization: "
                f"{commanded_pO2_bar!r} bar"
            )
        return quantized

    def canonical_composition_mol_by_account(
        self,
        sim: Any,
        composition_by_account: Mapping[str, Mapping[str, float]] | None = None,
    ) -> dict[str, dict[str, float]]:
        if composition_by_account is None:
            accounts = sim.atom_ledger.mol_by_account()
            composition_by_account = {
                str(account): sim.atom_ledger.project_account_mol(str(account))
                for account in accounts
            }
        return canonicalized_composition_mol_by_account(
            composition_by_account,
            sig_figs=self._control_quantization.composition_sig_figs,
        )

    def canonical_composition_mol(
        self,
        sim: Any,
        composition_by_account: Mapping[str, Mapping[str, float]] | None = None,
    ) -> dict[str, float]:
        totals: dict[str, float] = {}
        for species_mol in self.canonical_composition_mol_by_account(
            sim,
            composition_by_account,
        ).values():
            for species, mol in species_mol.items():
                totals[species] = totals.get(species, 0.0) + float(mol)
        return {
            species: mol
            for species, mol in totals.items()
            if mol > 0.0
        }

    def capture_equilibrium(self, sim: Any, result: EquilibriumResult) -> None:
        if not _is_cacheable_equilibrium_result(result):
            self._mark_uncacheable_capture(sim)
            return
        intent = _equilibrium_payload_intent(sim)
        key = self._equilibrium_key(sim)
        payload = equilibrium_payload(sim, result)
        self._store(
            "equilibrium_post_record",
            key,
            payload,
            engine_version_provenance=_engine_version_provenance(sim, intent),
        )
        sim._last_reduced_real_cache_state = self.last_cache_state

    def cached_equilibrium(self, sim: Any) -> EquilibriumResult | None:
        if not self.write_through_enabled:
            return None
        key = self._equilibrium_key(sim)
        payload = self._lookup_optional(
            "equilibrium_post_record",
            key,
            physics_bucket_key=canonical_physics_bucket_key_from_replay_key(key),
        )
        if payload is None:
            return None
        return self._equilibrium_from_payload(sim, payload)

    def replay_equilibrium(self, sim: Any) -> EquilibriumResult:
        payload = self._lookup("equilibrium_post_record", self._equilibrium_key(sim))
        return self._equilibrium_from_payload(sim, payload)

    def _equilibrium_key(self, sim: Any) -> dict[str, Any]:
        intent = _equilibrium_payload_intent(sim)
        return canonical_replay_key(
            sim,
            artifact="equilibrium_post_record",
            intent=intent,
            # Equilibrium is solved at actual conditions; keep live-T fO2.
            fO2_log=_authoritative_melt_fO2_log(sim),
            fe_redox_policy="intrinsic",
            control_quantization=self._control_quantization,
        )

    def _equilibrium_from_payload(
        self,
        sim: Any,
        payload: Mapping[str, Any],
    ) -> EquilibriumResult:
        result = equilibrium_from_payload(payload)
        sim._last_reduced_real_cache_state = self.last_cache_state
        sim._last_backend_status = getattr(result, "status", "ok")
        self._apply_equilibrium_cache_authority(sim, result)
        history = getattr(sim, "_backend_status_history", None)
        if isinstance(history, list):
            history.append(str(sim._last_backend_status))
        sim._last_vapor_pressures_source = dict(
            payload.get("last_vapor_pressures_source") or {}
        )
        sim._last_vapor_pressure_diagnostic = dict(
            payload.get("last_vapor_pressure_diagnostic") or {}
        )
        sulfur = getattr(result, "sulfur_saturation", None)
        sim._last_sulfur_saturation_result = sulfur
        if getattr(result, "fO2_log", None) is not None:
            sync = getattr(sim, "_sync_oxygen_reservoir_mirror", None)
            if callable(sync):
                sync()
            else:
                fO2_log = float(result.fO2_log)
                sim.melt.fO2_log = fO2_log
                if hasattr(sim.melt, "melt_fO2_log"):
                    sim.melt.melt_fO2_log = fO2_log
        return result

    def _apply_equilibrium_cache_authority(
        self,
        sim: Any,
        result: EquilibriumResult,
    ) -> None:
        cache_state = str(self.last_cache_state or "")
        if cache_state not in APPROXIMATE_REDUCED_REAL_CACHE_STATES:
            return
        sim._backend_authoritative = False
        diagnostic = {
            "reduced_real_cache_state": cache_state,
            "reduced_real_cache_authoritative": False,
        }
        result.diagnostics = {**dict(result.diagnostics or {}), **diagnostic}
        existing = getattr(sim, "_last_backend_diagnostics", None)
        if isinstance(existing, Mapping):
            sim._last_backend_diagnostics = {**dict(existing), **diagnostic}

    def capture_gate_curve(
        self,
        sim: Any,
        *,
        fO2_log: float,
        curve: Mapping[str, Any],
    ) -> None:
        if not _is_cacheable_gate_curve(curve):
            self._mark_uncacheable_capture(sim)
            return
        provider_role = _gate_provider_role_for_capture(sim, curve)
        key = canonical_replay_key(
            sim,
            artifact="freeze_gate_curve",
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=fO2_log,
            fe_redox_policy="intrinsic",
            provider_role=provider_role,
            control_quantization=self._control_quantization,
        )
        self._store(
            "freeze_gate_curve",
            key,
            {"curve": _curve_payload(curve)},
            engine_version_provenance=_engine_version_provenance(
                sim,
                ChemistryIntent.GATE_LIQUID_FRACTION,
                provider_role=provider_role,
            ),
        )
        sim._last_reduced_real_cache_state = self.last_cache_state

    def _mark_uncacheable_capture(self, sim: Any) -> None:
        self.last_cache_state = None
        sim._last_reduced_real_cache_state = None

    def replay_gate_curve(self, sim: Any, *, fO2_log: float) -> dict[str, Any]:
        keys = tuple(
            canonical_replay_key(
                sim,
                artifact="freeze_gate_curve",
                intent=ChemistryIntent.GATE_LIQUID_FRACTION,
                fO2_log=fO2_log,
                fe_redox_policy="intrinsic",
                provider_role=provider_role,
                control_quantization=self._control_quantization,
            )
            for provider_role in _gate_provider_roles_for_replay(sim)
        )
        payload = self._lookup_first_available("freeze_gate_curve", keys)
        sim._last_reduced_real_cache_state = self.last_cache_state
        return _curve_from_payload(payload["curve"])

    def summary(self) -> dict[str, Any]:
        from simulator.interpolation_uncertainty import (
            interpolation_uncertainty_points_from_replay_sequence,
            ranked_table_drain,
        )

        by_artifact = Counter(
            record["artifact"] for record in self.capture_sequence
        )
        state_counts = Counter(
            event["cache_state"] for event in self.cache_events
        )
        state_counts_by_artifact: dict[str, Counter[str]] = {}
        for event in self.cache_events:
            artifact = event["artifact"]
            state_counts_by_artifact.setdefault(artifact, Counter())
            state_counts_by_artifact[artifact][event["cache_state"]] += 1
        summary = {
            "mode": self.mode,
            "entries": len(self.entries),
            "capture_calls": len(self.capture_sequence),
            "replay_calls": len(self.replay_sequence),
            "hits": self.hits,
            "misses": len(self.misses),
            "live_fills": self.live_fills,
            "cache_states": CACHE_STATES,
            "cache_state_counts": {
                state: state_counts.get(state, 0)
                for state in CACHE_STATES
            },
            "cache_state_counts_by_artifact": {
                artifact: {
                    state: counts.get(state, 0)
                    for state in CACHE_STATES
                }
                for artifact, counts in sorted(state_counts_by_artifact.items())
            },
            "capture_calls_by_artifact": dict(sorted(by_artifact.items())),
            "key_drift_histogram": self.key_drift_histogram(),
            "key_drift_histogram_scope": (
                "replay_mode_1_to_1_capture_replay_only"
            ),
            "first_miss": self.misses[0] if self.misses else None,
            "persistent_store": (
                None
                if self.persistent_path is None
                else {
                    "path": str(self.persistent_path),
                    "table": PT1_EQUILIBRIUM_TABLE,
                    "schema_version": PT1_STORE_SCHEMA_VERSION,
                }
            ),
        }
        uncertainty_points = interpolation_uncertainty_points_from_replay_sequence(
            self.replay_sequence
        )
        if uncertainty_points:
            summary["interpolation_uncertainty_points"] = len(uncertainty_points)
            summary["interpolation_uncertainty_ranked_table_drain"] = (
                ranked_table_drain(uncertainty_points)
            )
        return summary

    def key_drift_histogram(self) -> dict[str, int]:
        """Replay-only drift counts for 1:1 capture/replay sequence comparisons."""
        if not self.replay_enabled:
            return {}
        counts: Counter[str] = Counter()
        for index, replay in enumerate(self.replay_sequence):
            if index >= len(self.capture_sequence):
                counts["<extra_replay_call>"] += 1
                continue
            capture = self.capture_sequence[index]
            for field in _diff_top_fields(capture["key"], replay["key"]):
                counts[field] += 1
        return dict(sorted(counts.items()))

    def _store(
        self,
        artifact: str,
        key: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        engine_version_provenance: str | None = None,
    ) -> None:
        validate_reduced_real_equilibrium_record_key(artifact, key)
        key_bytes = canonical_json_bytes(key)
        payload_bytes = canonical_json_bytes(payload)
        key_hash = _sha256(key_bytes)
        payload_hash = _sha256(payload_bytes)
        physics_bucket_key = canonical_physics_bucket_key_from_replay_key(key)
        physics_bucket_bytes = canonical_json_bytes(physics_bucket_key)
        physics_bucket_hash = _sha256(physics_bucket_bytes)
        self.capture_sequence.append(
            {
                "artifact": artifact,
                "key": copy.deepcopy(dict(key)),
                "hash": key_hash,
                "physics_bucket_hash": physics_bucket_hash,
            }
        )
        existing = self.entries.get(key_hash)
        if existing is not None:
            if existing["payload_hash"] != payload_hash:
                raise PT0CacheCollision(
                    f"PT-0 {artifact} key collision with different payload: "
                    f"{key_hash}"
                )
            self._verify_entry(artifact, key, key_bytes, key_hash, existing)
            if self.persistent_store is not None:
                self.persistent_store.put(
                    artifact=artifact,
                    key=key,
                    key_bytes=key_bytes,
                    key_hash=key_hash,
                    payload=payload,
                    payload_bytes=payload_bytes,
                    payload_hash=payload_hash,
                    engine_version_provenance=engine_version_provenance,
                    physics_bucket_key=physics_bucket_key,
                    physics_bucket_bytes=physics_bucket_bytes,
                    physics_bucket_hash=physics_bucket_hash,
                )
            self.physics_bucket_entries.setdefault(physics_bucket_hash, key_hash)
            return
        if self.persistent_store is not None:
            self.persistent_store.put(
                artifact=artifact,
                key=key,
                key_bytes=key_bytes,
                key_hash=key_hash,
                payload=payload,
                payload_bytes=payload_bytes,
                payload_hash=payload_hash,
                engine_version_provenance=engine_version_provenance,
                physics_bucket_key=physics_bucket_key,
                physics_bucket_bytes=physics_bucket_bytes,
                physics_bucket_hash=physics_bucket_hash,
            )
        self.entries[key_hash] = {
            "artifact": artifact,
            "key": copy.deepcopy(dict(key)),
            "key_hash": key_hash,
            "key_bytes": key_bytes.decode("utf-8"),
            "payload": copy.deepcopy(dict(payload)),
            "payload_hash": payload_hash,
            "physics_bucket_key": copy.deepcopy(dict(physics_bucket_key)),
            "physics_bucket_hash": physics_bucket_hash,
            "cache_state": "live_fill",
        }
        self.physics_bucket_entries.setdefault(physics_bucket_hash, key_hash)
        self.live_fills += 1
        self.last_cache_state = "live_fill"
        self._record_cache_event(artifact, "live_fill")

    def _lookup(self, artifact: str, key: Mapping[str, Any]) -> dict[str, Any]:
        return self._lookup_first_available(
            artifact,
            tuple(_compatible_replay_keys(key)),
        )

    def _lookup_first_available(
        self,
        artifact: str,
        keys: tuple[Mapping[str, Any], ...],
    ) -> dict[str, Any]:
        if not keys:
            raise PT0CacheMiss(f"PT-0 cached replay miss: no keys for {artifact}")

        checked: list[tuple[Mapping[str, Any], bytes, str]] = []
        for key in _expand_compatible_replay_keys(keys):
            key_bytes = canonical_json_bytes(key)
            key_hash = _sha256(key_bytes)
            checked.append((key, key_bytes, key_hash))
            entry = self._entry_for_key(artifact, key, key_bytes, key_hash)
            if entry is None:
                continue
            self.replay_sequence.append(
                {
                    "artifact": artifact,
                    "key": copy.deepcopy(dict(key)),
                    "hash": key_hash,
                    "cache_state": "cached_exact",
                }
            )
            sequence_index = len(self.replay_sequence) - 1
            if len(self.capture_sequence) <= sequence_index:
                self.capture_sequence.append(
                    {
                        "artifact": artifact,
                        "key": copy.deepcopy(dict(entry["key"])),
                        "hash": key_hash,
                        "persistent": True,
                    }
                )
            self.hits += 1
            self.last_cache_state = "cached_exact"
            self._record_cache_event(artifact, "cached_exact")
            return copy.deepcopy(entry["payload"])

        key, _key_bytes, key_hash = checked[0]
        self.replay_sequence.append(
            {"artifact": artifact, "key": copy.deepcopy(dict(key)), "hash": key_hash}
        )
        miss = {
            "artifact": artifact,
            "key_hash": key_hash,
            "sequence_index": len(self.replay_sequence) - 1,
            "drift_fields": self._drift_fields_for_latest_replay(),
        }
        if len(checked) > 1:
            miss["alternate_key_hashes"] = [
                alternate_hash for _, _, alternate_hash in checked[1:]
            ]
        self.misses.append(miss)
        self.last_cache_state = None
        raise PT0CacheMiss(f"PT-0 cached replay miss: {miss}")

    def _lookup_optional(
        self,
        artifact: str,
        key: Mapping[str, Any],
        *,
        physics_bucket_key: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        tier_ceiling = str(
            getattr(self, "cache_tier_ceiling", "cached_interpolated")
        )
        for candidate_key in _compatible_replay_keys(key):
            key_bytes = canonical_json_bytes(candidate_key)
            key_hash = _sha256(key_bytes)
            entry = self._entry_for_key(artifact, candidate_key, key_bytes, key_hash)
            cache_state = "cached_exact"
            candidate_physics_bucket_key = (
                physics_bucket_key
                if candidate_key == key and physics_bucket_key is not None
                else canonical_physics_bucket_key_from_replay_key(candidate_key)
            )
            physics_bucket_hash: str | None = None
            physics_bucket_rung: str | None = None
            if entry is None and tier_ceiling != "cached_exact":
                entry = self._entry_for_physics_bucket(
                    artifact,
                    candidate_physics_bucket_key,
                )
                cache_state = "cached_physics_bucket"
                physics_bucket_hash = _sha256(
                    canonical_json_bytes(candidate_physics_bucket_key)
                )
                if entry is None:
                    for rung_tag, _sig_figs in PHYSICS_BUCKET_LADDER_RUNGS:
                        rung_key = canonical_physics_ladder_bucket_key_from_replay_key(
                            candidate_key,
                            rung_tag,
                        )
                        entry = self._entry_for_physics_ladder_bucket(
                            artifact,
                            rung_tag,
                            rung_key,
                        )
                        if entry is not None:
                            cache_state = "cached_physics_bucket"
                            candidate_physics_bucket_key = rung_key
                            physics_bucket_hash = _sha256(
                                canonical_json_bytes(rung_key)
                            )
                            physics_bucket_rung = rung_tag
                            break
                if entry is None:
                    for rung_tag, _sig_figs in PHYSICS_BUCKET_CONTROL_LADDER_RUNGS:
                        rung_key = canonical_physics_ladder_bucket_key_from_replay_key(
                            candidate_key,
                            rung_tag,
                        )
                        entry = self._entry_for_physics_ladder_bucket(
                            artifact,
                            rung_tag,
                            rung_key,
                            query_key=candidate_key,
                        )
                        if entry is not None:
                            cache_state = "cached_physics_bucket"
                            candidate_physics_bucket_key = rung_key
                            physics_bucket_hash = _sha256(
                                canonical_json_bytes(rung_key)
                            )
                            physics_bucket_rung = rung_tag
                            break
            if entry is not None:
                break
            if tier_ceiling == "cached_interpolated":
                interpolated = self._lookup_interpolated(artifact, candidate_key)
                if interpolated is not None:
                    return interpolated
        else:
            return None
        replay_event = {
            "artifact": artifact,
            "key": copy.deepcopy(dict(candidate_key)),
            "hash": key_hash,
            "cache_state": cache_state,
        }
        if (
            cache_state == "cached_physics_bucket"
            and candidate_physics_bucket_key is not None
        ):
            replay_event["physics_bucket_hash"] = physics_bucket_hash
            if physics_bucket_rung is not None:
                replay_event["physics_bucket_rung"] = physics_bucket_rung
                if _physics_ladder_coarsens_controls(physics_bucket_rung):
                    replay_event["physics_bucket_error_budget"] = (
                        physics_control_rung_error_budget(
                            candidate_key,
                            entry["key"],
                            physics_bucket_rung,
                            source_payload=entry.get("payload"),
                        )
                    )
            replay_event["source_key_hash"] = str(entry["key_hash"])
        self.replay_sequence.append(replay_event)
        self.hits += 1
        self.last_cache_state = cache_state
        self._record_cache_event(artifact, cache_state)
        return copy.deepcopy(entry["payload"])

    def _entry_for_key(
        self,
        artifact: str,
        key: Mapping[str, Any],
        key_bytes: bytes,
        key_hash: str,
    ) -> dict[str, Any] | None:
        entry = self.entries.get(key_hash)
        if entry is None and self.persistent_store is not None:
            entry = self.persistent_store.get(
                artifact=artifact,
                key=key,
                key_bytes=key_bytes,
                key_hash=key_hash,
            )
            if entry is not None:
                self.entries[key_hash] = copy.deepcopy(entry)
        if entry is not None:
            self._verify_entry(artifact, key, key_bytes, key_hash, entry)
        return entry

    def _entry_for_physics_bucket(
        self,
        artifact: str,
        physics_bucket_key: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        physics_bucket_bytes = canonical_json_bytes(physics_bucket_key)
        physics_bucket_hash = _sha256(physics_bucket_bytes)
        key_hash = self.physics_bucket_entries.get(physics_bucket_hash)
        entry = self.entries.get(key_hash) if key_hash is not None else None
        if entry is None and self.persistent_store is not None:
            entry = self.persistent_store.get_by_physics_bucket(
                artifact=artifact,
                physics_bucket_key=physics_bucket_key,
                physics_bucket_bytes=physics_bucket_bytes,
                physics_bucket_hash=physics_bucket_hash,
            )
            if entry is not None:
                row_hash = str(entry["key_hash"])
                self.entries[row_hash] = copy.deepcopy(entry)
                self.physics_bucket_entries.setdefault(
                    physics_bucket_hash,
                    row_hash,
                )
        return entry

    def _record_cache_event(self, artifact: str, cache_state: str) -> None:
        self.cache_events.append(
            {"artifact": str(artifact), "cache_state": str(cache_state)}
        )

    def _lookup_interpolated(
        self,
        artifact: str,
        key: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        from simulator.reduced_real_cache_interpolation import (
            attempt_cached_interpolation,
            replay_scope_for_interpolation,
        )

        if self.persistent_store is None:
            return None
        key_bytes = canonical_json_bytes(key)
        key_hash = _sha256(key_bytes)
        candidates = self.persistent_store.list_interpolation_candidates(
            artifact=artifact,
            replay_scope_sha256=replay_scope_for_interpolation(key),
            exclude_key_hash=key_hash,
        )
        for candidate in candidates:
            self.entries[str(candidate["key_hash"])] = copy.deepcopy(candidate)
        attempt = attempt_cached_interpolation(key, candidates)
        if attempt is None:
            return None
        replay_event = {
            "artifact": artifact,
            "key": copy.deepcopy(dict(key)),
            "hash": key_hash,
            "cache_state": "cached_interpolated",
            "interpolation_neighbor_key_hashes": [
                str(neighbor.get("key_hash", ""))
                for neighbor in attempt["neighbors"]
            ],
            "interpolation_mode": attempt["weight_info"]["mode"],
            "interpolation_error_estimate": attempt["error_estimate"],
            "interpolation_uncertainty": attempt["uncertainty"],
            "interpolation_gate": attempt["gate"],
        }
        self.replay_sequence.append(replay_event)
        self.hits += 1
        self.last_cache_state = "cached_interpolated"
        self._record_cache_event(artifact, "cached_interpolated")
        return copy.deepcopy(attempt["payload"])

    def _entry_for_physics_ladder_bucket(
        self,
        artifact: str,
        rung_tag: str,
        rung_key: Mapping[str, Any],
        *,
        query_key: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        rung_bytes = canonical_json_bytes(rung_key)
        rung_hash = _sha256(rung_bytes)
        if self.persistent_store is None:
            return None
        entry = self.persistent_store.get_by_physics_ladder_bucket(
            artifact=artifact,
            rung_tag=rung_tag,
            rung_key=rung_key,
            rung_hash=rung_hash,
            query_key=query_key,
        )
        if entry is not None:
            self.entries[str(entry["key_hash"])] = copy.deepcopy(entry)
        return entry

    def _verify_entry(
        self,
        artifact: str,
        key: Mapping[str, Any],
        key_bytes: bytes,
        key_hash: str,
        entry: Mapping[str, Any],
    ) -> None:
        if entry.get("artifact") != artifact:
            raise PT0CacheCollision(
                f"PT-0 stored artifact mismatch for {artifact}: {key_hash}"
            )
        if entry.get("key_hash") not in {None, key_hash}:
            raise PT0CacheCollision(
                f"PT-0 stored key hash mismatch for {artifact}: {key_hash}"
            )
        stored_key_bytes = entry.get("key_bytes")
        if isinstance(stored_key_bytes, str):
            stored_key_bytes = stored_key_bytes.encode("utf-8")
        if stored_key_bytes is None:
            stored_key_bytes = canonical_json_bytes(entry["key"])
        if stored_key_bytes != key_bytes or canonical_json_bytes(key) != key_bytes:
            raise PT0CacheCollision(
                f"PT-0 stored key bytes mismatch for {artifact}: {key_hash}"
            )
        payload_bytes = canonical_json_bytes(entry["payload"])
        if entry.get("payload_hash") != _sha256(payload_bytes):
            raise PT0CacheCollision(
                f"PT-0 stored payload bytes mismatch for {artifact}: {key_hash}"
            )

    def _drift_fields_for_latest_replay(self) -> list[str]:
        index = len(self.replay_sequence) - 1
        if index >= len(self.capture_sequence):
            return ["<extra_replay_call>"]
        return _diff_top_fields(
            self.capture_sequence[index]["key"],
            self.replay_sequence[index]["key"],
        )


def _paths_refer_to_same_file(left: Path, right: Path) -> bool:
    left_resolved = left.resolve()
    right_resolved = right.resolve()
    if left_resolved.exists() and right_resolved.exists():
        left_stat = os.stat(left_resolved)
        right_stat = os.stat(right_resolved)
        return (left_stat.st_dev, left_stat.st_ino) == (
            right_stat.st_dev,
            right_stat.st_ino,
        )
    return os.path.normcase(str(left_resolved)) == os.path.normcase(
        str(right_resolved)
    )


class PT1PersistentEquilibriumStore:
    """Content-addressed SQLite store for PT-0 exact reduced-real payloads."""

    def __init__(
        self,
        db_path: Path,
        *,
        read_only_base_db_path: Path | None = None,
        shard_busy_timeout_ms: float = DEFAULT_SHARD_BUSY_TIMEOUT_MS,
        strict_vapor_gate: bool = False,
        control_quantization: ControlQuantization | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.read_only_base_db_path = (
            Path(read_only_base_db_path)
            if read_only_base_db_path is not None
            else None
        )
        self._shard_busy_timeout_ms = float(shard_busy_timeout_ms)
        self.strict_vapor_gate = bool(strict_vapor_gate)
        self._control_quantization = (
            control_quantization or _PRODUCTION_QUANTIZATION
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Epoch provenance is captured when the store opens. Rows written later
        # intentionally keep this value even if the worktree changes mid-epoch.
        self._epoch_git_dirty = _git_dirty()
        with self._connect() as conn:
            self._initialize(conn)

    @property
    def control_quantization(self) -> ControlQuantization:
        return self._control_quantization

    def put(
        self,
        *,
        artifact: str,
        key: Mapping[str, Any],
        key_bytes: bytes,
        key_hash: str,
        payload: Mapping[str, Any],
        payload_bytes: bytes,
        payload_hash: str,
        engine_version_provenance: str | None = None,
        physics_bucket_key: Mapping[str, Any] | None = None,
        physics_bucket_bytes: bytes | None = None,
        physics_bucket_hash: str | None = None,
    ) -> None:
        validate_reduced_real_equilibrium_record_key(artifact, key)
        if self.strict_vapor_gate:
            assert_strict_vapor_pt1_row(
                artifact=artifact,
                key=key,
                payload=payload,
                key_hash=key_hash,
            )
        if physics_bucket_key is None:
            physics_bucket_key = canonical_physics_bucket_key_from_replay_key(key)
        if physics_bucket_bytes is None:
            physics_bucket_bytes = canonical_json_bytes(physics_bucket_key)
        if physics_bucket_hash is None:
            physics_bucket_hash = _sha256(physics_bucket_bytes)
        ladder_values = _physics_ladder_values_from_replay_key(key)
        with self._connect() as conn:
            self._initialize(conn)
            existing = self._fetch(conn, key_hash)
            if existing is not None:
                entry = self._entry_from_row(
                    existing,
                    artifact=artifact,
                    key=key,
                    key_bytes=key_bytes,
                    key_hash=key_hash,
                )
                if (
                    entry["payload_hash"] != payload_hash
                    or canonical_json_bytes(entry["payload"]) != payload_bytes
                ):
                    raise PT1PersistentStoreCorrupt(
                        f"PT-1 payload collision for {artifact}: {key_hash}"
                    )
                self._update_physics_bucket_columns(
                    conn,
                    key_hash=key_hash,
                    physics_bucket_key=physics_bucket_key,
                    physics_bucket_bytes=physics_bucket_bytes,
                    physics_bucket_hash=physics_bucket_hash,
                    ladder_values=ladder_values,
                )
                return
            conn.execute(
                f"""
                INSERT INTO {PT1_EQUILIBRIUM_TABLE} (
                    key_hash,
                    artifact,
                    store_schema_version,
                    request_schema_version,
                    key_sha256,
                    payload_sha256,
                    key_bytes,
                    payload_bytes,
                    code_version,
                    corpus_version,
                    engine_version,
                    data_digests_json,
                    physics_bucket_schema_version,
                    physics_bucket_sha256,
                    replay_scope_sha256,
                    physics_key_bytes,
                    physics_bucket_h40_sha256,
                    physics_bucket_h40_distance,
                    physics_bucket_h30_sha256,
                    physics_bucket_h30_distance,
                    physics_bucket_h40c_sha256,
                    physics_bucket_h40c_distance,
                    physics_bucket_h30c_sha256,
                    physics_bucket_h30c_distance,
                    created_at,
                    git_dirty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key_hash,
                    artifact,
                    PT1_STORE_SCHEMA_VERSION,
                    str(key.get("schema_version")),
                    key_hash,
                    payload_hash,
                    sqlite3.Binary(key_bytes),
                    sqlite3.Binary(payload_bytes),
                    _code_version(),
                    str(key.get("corpus_version")),
                    _none_or_str(engine_version_provenance),
                    canonical_json_bytes(key.get("data_digests", {})).decode("utf-8"),
                    str(physics_bucket_key.get("schema_version")),
                    physics_bucket_hash,
                    _replay_scope_hash(physics_bucket_key),
                    sqlite3.Binary(physics_bucket_bytes),
                    ladder_values["h40"]["sha256"],
                    ladder_values["h40"]["distance"],
                    ladder_values["h30"]["sha256"],
                    ladder_values["h30"]["distance"],
                    ladder_values["h40c"]["sha256"],
                    ladder_values["h40c"]["distance"],
                    ladder_values["h30c"]["sha256"],
                    ladder_values["h30c"]["distance"],
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    self._epoch_git_dirty,
                ),
            )

    def get(
        self,
        *,
        artifact: str,
        key: Mapping[str, Any],
        key_bytes: bytes,
        key_hash: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            self._initialize(conn)
            row = self._fetch(conn, key_hash)
            if row is None:
                return None
            return self._entry_from_row(
                row,
                artifact=artifact,
                key=key,
                key_bytes=key_bytes,
                key_hash=key_hash,
            )

    def get_by_physics_bucket(
        self,
        *,
        artifact: str,
        physics_bucket_key: Mapping[str, Any],
        physics_bucket_bytes: bytes,
        physics_bucket_hash: str,
    ) -> dict[str, Any] | None:
        query = f"""
            SELECT *
            FROM {{table}}
            WHERE artifact = ?
              AND physics_bucket_schema_version = ?
              AND physics_bucket_sha256 = ?
              AND replay_scope_sha256 = ?
            ORDER BY key_hash
            LIMIT 1
            """
        params = (
            artifact,
            str(physics_bucket_key.get("schema_version")),
            physics_bucket_hash,
            _replay_scope_hash(physics_bucket_key),
        )
        with self._connect() as conn:
            self._initialize(conn)
            row = conn.execute(
                query.format(table=PT1_EQUILIBRIUM_TABLE),
                params,
            ).fetchone()
            if row is None:
                read_only_table = self._read_only_equilibrium_table(conn)
                if read_only_table is not None:
                    row = conn.execute(
                        query.format(table=read_only_table),
                        params,
                    ).fetchone()
            if row is None:
                return None
            return self._entry_from_physics_bucket_row(
                row,
                artifact=artifact,
                physics_bucket_key=physics_bucket_key,
                physics_bucket_bytes=physics_bucket_bytes,
                physics_bucket_hash=physics_bucket_hash,
            )

    def list_interpolation_candidates(
        self,
        *,
        artifact: str,
        replay_scope_sha256: str,
        exclude_key_hash: str | None = None,
    ) -> list[dict[str, Any]]:
        query = f"""
            SELECT *
            FROM {{table}}
            WHERE artifact = ?
              AND replay_scope_sha256 = ?
            ORDER BY key_hash
            """
        params = (artifact, replay_scope_sha256)
        with self._connect() as conn:
            self._initialize(conn)
            candidates: list[dict[str, Any]] = []
            seen_hashes: set[str] = set()
            tables = [PT1_EQUILIBRIUM_TABLE]
            read_only_table = self._read_only_equilibrium_table(conn)
            if read_only_table is not None:
                tables.append(read_only_table)
            for table in tables:
                rows = conn.execute(query.format(table=table), params)
                for row in rows:
                    row_hash = str(row["key_hash"])
                    if exclude_key_hash is not None and row_hash == exclude_key_hash:
                        continue
                    if row_hash in seen_hashes:
                        continue
                    seen_hashes.add(row_hash)
                    row_key_bytes = _sqlite_bytes(row["key_bytes"])
                    row_key = json.loads(row_key_bytes.decode("utf-8"))
                    entry = self._entry_from_row(
                        row,
                        artifact=artifact,
                        key=row_key,
                        key_bytes=row_key_bytes,
                        key_hash=row_hash,
                    )
                    row_replay_scope = _replay_scope_hash(
                        canonical_physics_bucket_key_from_replay_key(entry["key"])
                    )
                    if row_replay_scope != replay_scope_sha256:
                        raise PT1PersistentStoreCorrupt(
                            f"PT-1 row exact key replay scope mismatch: {row_hash}"
                        )
                    candidates.append(
                        {
                            "artifact": artifact,
                            "key": copy.deepcopy(dict(entry["key"])),
                            "key_hash": row_hash,
                            "payload": copy.deepcopy(dict(entry["payload"])),
                        }
                    )
            return candidates

    def get_by_physics_ladder_bucket(
        self,
        *,
        artifact: str,
        rung_tag: str,
        rung_key: Mapping[str, Any],
        rung_hash: str,
        query_key: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        hash_column = _physics_ladder_hash_column(rung_tag)
        distance_column = _physics_ladder_distance_column(rung_tag)
        query = f"""
            SELECT *
            FROM {{table}}
            WHERE artifact = ?
              AND replay_scope_sha256 = ?
              AND {hash_column} = ?
            ORDER BY {distance_column} ASC, key_hash ASC
            """
        params = (
            artifact,
            _replay_scope_hash(rung_key),
            rung_hash,
        )
        with self._connect() as conn:
            self._initialize(conn)
            tables = [PT1_EQUILIBRIUM_TABLE]
            read_only_table = self._read_only_equilibrium_table(conn)
            if read_only_table is not None:
                tables.append(read_only_table)
            best_entry: dict[str, Any] | None = None
            best_sort: tuple[float, str] | None = None
            for table in tables:
                rows = conn.execute(query.format(table=table), params)
                for row in rows:
                    entry = self._entry_from_physics_ladder_bucket_row(
                        row,
                        artifact=artifact,
                        rung_tag=rung_tag,
                        rung_key=rung_key,
                        rung_hash=rung_hash,
                    )
                    if query_key is not None and _physics_ladder_coarsens_controls(
                        rung_tag
                    ):
                        budget = physics_control_rung_error_budget(
                            query_key,
                            entry["key"],
                            rung_tag,
                            source_payload=entry.get("payload"),
                        )
                        if not bool(budget["accepted"]):
                            continue
                    row_distance = float(
                        row[_physics_ladder_distance_column(rung_tag)]
                    )
                    sort_key = (row_distance, str(entry["key_hash"]))
                    if best_sort is None or sort_key < best_sort:
                        best_sort = sort_key
                        best_entry = entry
            return best_entry

    def _connect(self) -> sqlite3.Connection:
        timeout_sec = self._shard_busy_timeout_ms / 1000.0
        conn = sqlite3.connect(self.db_path, timeout=timeout_sec)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(self._shard_busy_timeout_ms)}")
        if self.read_only_base_db_path is not None:
            journal_mode = str(conn.execute("PRAGMA main.journal_mode").fetchone()[0])
            if journal_mode.lower() != "wal":
                conn.execute("PRAGMA main.journal_mode=WAL")
        self._attach_read_only_base(conn)
        return conn

    def _attach_read_only_base(self, conn: sqlite3.Connection) -> None:
        if self.read_only_base_db_path is None:
            return
        if _paths_refer_to_same_file(
            self.read_only_base_db_path,
            self.db_path,
        ):
            raise ValueError(
                "read_only_base_db_path must not equal db_path; attaching base-as-itself "
                "would route unqualified INSERTs into the read-only base"
            )
        if not self.read_only_base_db_path.exists():
            _LOGGER.warning(
                "read_only_base_db_path does not exist: %s; "
                "epoch will miss base-cache hits",
                self.read_only_base_db_path,
            )
            return
        uri = self._sqlite_readonly_uri(self.read_only_base_db_path)
        conn.execute(
            f"ATTACH DATABASE ? AS {PT1_READ_ONLY_BASE_ALIAS}",
            (uri,),
        )

    @staticmethod
    def _sqlite_readonly_uri(path: Path) -> str:
        return f"{path.resolve().as_uri()}?mode=ro"

    def _read_only_equilibrium_table(self, conn: sqlite3.Connection) -> str | None:
        alias = PT1_READ_ONLY_BASE_ALIAS
        row = conn.execute(
            "SELECT 1 FROM pragma_database_list WHERE name = ?",
            (alias,),
        ).fetchone()
        if row is None:
            return None
        return f"{alias}.{PT1_EQUILIBRIUM_TABLE}"

    def _initialize(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {PT1_METADATA_TABLE} (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {PT1_EQUILIBRIUM_TABLE} (
                key_hash TEXT PRIMARY KEY,
                artifact TEXT NOT NULL,
                store_schema_version TEXT NOT NULL,
                request_schema_version TEXT NOT NULL,
                key_sha256 TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                key_bytes BLOB NOT NULL,
                payload_bytes BLOB NOT NULL,
                code_version TEXT NOT NULL,
                corpus_version TEXT,
                engine_version TEXT,
                data_digests_json TEXT NOT NULL,
                physics_bucket_schema_version TEXT,
                physics_bucket_sha256 TEXT,
                replay_scope_sha256 TEXT,
                physics_key_bytes BLOB,
                physics_bucket_h40_sha256 TEXT,
                physics_bucket_h40_distance REAL,
                physics_bucket_h30_sha256 TEXT,
                physics_bucket_h30_distance REAL,
                physics_bucket_h40c_sha256 TEXT,
                physics_bucket_h40c_distance REAL,
                physics_bucket_h30c_sha256 TEXT,
                physics_bucket_h30c_distance REAL,
                created_at TEXT NOT NULL,
                git_dirty INTEGER NOT NULL
            )
            """
        )
        self._ensure_physics_bucket_columns(conn)
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{PT1_EQUILIBRIUM_TABLE}_artifact
            ON {PT1_EQUILIBRIUM_TABLE}(artifact)
            """
        )
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{PT1_EQUILIBRIUM_TABLE}_physics
            ON {PT1_EQUILIBRIUM_TABLE}(
                artifact,
                physics_bucket_schema_version,
                physics_bucket_sha256,
                replay_scope_sha256
            )
            """
        )
        for rung_tag, _sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS:
            hash_column = _physics_ladder_hash_column(rung_tag)
            distance_column = _physics_ladder_distance_column(rung_tag)
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{PT1_EQUILIBRIUM_TABLE}_{rung_tag}
                ON {PT1_EQUILIBRIUM_TABLE}(
                    artifact,
                    replay_scope_sha256,
                    {hash_column},
                    {distance_column},
                    key_hash
                )
                """
            )
        metadata = conn.execute(
            f"SELECT value FROM {PT1_METADATA_TABLE} WHERE key = ?",
            ("store_schema_version",),
        ).fetchone()
        if metadata is not None and metadata["value"] != PT1_STORE_SCHEMA_VERSION:
            raise PT1PersistentStoreCorrupt(
                "PT-1 persistent store schema version drift: "
                f"{metadata['value']} != {PT1_STORE_SCHEMA_VERSION}"
            )
        conn.execute(
            f"""
            INSERT OR IGNORE INTO {PT1_METADATA_TABLE} (key, value)
            VALUES (?, ?)
            """,
            ("store_schema_version", PT1_STORE_SCHEMA_VERSION),
        )

    def _ensure_physics_bucket_columns(self, conn: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})")
        }
        columns = {
            "corpus_version": "TEXT",
            "physics_bucket_schema_version": "TEXT",
            "physics_bucket_sha256": "TEXT",
            "replay_scope_sha256": "TEXT",
            "physics_key_bytes": "BLOB",
            "physics_bucket_h40_sha256": "TEXT",
            "physics_bucket_h40_distance": "REAL",
            "physics_bucket_h30_sha256": "TEXT",
            "physics_bucket_h30_distance": "REAL",
            "physics_bucket_h40c_sha256": "TEXT",
            "physics_bucket_h40c_distance": "REAL",
            "physics_bucket_h30c_sha256": "TEXT",
            "physics_bucket_h30c_distance": "REAL",
        }
        for name, column_type in columns.items():
            if name not in existing:
                conn.execute(
                    f"ALTER TABLE {PT1_EQUILIBRIUM_TABLE} "
                    f"ADD COLUMN {name} {column_type}"
                )

    def _update_physics_bucket_columns(
        self,
        conn: sqlite3.Connection,
        *,
        key_hash: str,
        physics_bucket_key: Mapping[str, Any],
        physics_bucket_bytes: bytes,
        physics_bucket_hash: str,
        ladder_values: Mapping[str, Mapping[str, Any]],
    ) -> None:
        conn.execute(
            f"""
            UPDATE {PT1_EQUILIBRIUM_TABLE}
            SET physics_bucket_schema_version = ?,
                physics_bucket_sha256 = ?,
                replay_scope_sha256 = ?,
                physics_key_bytes = ?,
                physics_bucket_h40_sha256 = ?,
                physics_bucket_h40_distance = ?,
                physics_bucket_h30_sha256 = ?,
                physics_bucket_h30_distance = ?,
                physics_bucket_h40c_sha256 = ?,
                physics_bucket_h40c_distance = ?,
                physics_bucket_h30c_sha256 = ?,
                physics_bucket_h30c_distance = ?
            WHERE key_hash = ?
              AND (
                  physics_bucket_sha256 IS NULL
                  OR physics_bucket_h40_sha256 IS NULL
                  OR physics_bucket_h30_sha256 IS NULL
                  OR physics_bucket_h40c_sha256 IS NULL
                  OR physics_bucket_h30c_sha256 IS NULL
              )
            """,
            (
                str(physics_bucket_key.get("schema_version")),
                physics_bucket_hash,
                _replay_scope_hash(physics_bucket_key),
                sqlite3.Binary(physics_bucket_bytes),
                ladder_values["h40"]["sha256"],
                ladder_values["h40"]["distance"],
                ladder_values["h30"]["sha256"],
                ladder_values["h30"]["distance"],
                ladder_values["h40c"]["sha256"],
                ladder_values["h40c"]["distance"],
                ladder_values["h30c"]["sha256"],
                ladder_values["h30c"]["distance"],
                key_hash,
            ),
        )

    def _fetch(
        self,
        conn: sqlite3.Connection,
        key_hash: str,
    ) -> sqlite3.Row | None:
        query = f"""
            SELECT
                key_hash,
                artifact,
                store_schema_version,
                request_schema_version,
                key_sha256,
                payload_sha256,
                key_bytes,
                payload_bytes,
                code_version,
                corpus_version,
                engine_version,
                data_digests_json
            FROM {{table}}
            WHERE key_hash = ?
            """
        row = conn.execute(
            query.format(table=PT1_EQUILIBRIUM_TABLE),
            (key_hash,),
        ).fetchone()
        if row is not None:
            return row
        read_only_table = self._read_only_equilibrium_table(conn)
        if read_only_table is None:
            return None
        return conn.execute(
            query.format(table=read_only_table),
            (key_hash,),
        ).fetchone()

    def _entry_from_row(
        self,
        row: sqlite3.Row,
        *,
        artifact: str,
        key: Mapping[str, Any],
        key_bytes: bytes,
        key_hash: str,
    ) -> dict[str, Any]:
        row_key_bytes = _sqlite_bytes(row["key_bytes"])
        row_payload_bytes = _sqlite_bytes(row["payload_bytes"])
        if row["store_schema_version"] != PT1_STORE_SCHEMA_VERSION:
            raise PT1PersistentStoreCorrupt(
                "PT-1 row store schema version drift: "
                f"{row['store_schema_version']} != {PT1_STORE_SCHEMA_VERSION}"
            )
        if row["request_schema_version"] != str(key.get("schema_version")):
            raise PT1PersistentStoreCorrupt(
                "PT-1 row request schema version drift: "
                f"{row['request_schema_version']} != {key.get('schema_version')}"
            )
        if row["artifact"] != artifact:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row artifact mismatch for {artifact}: {key_hash}"
            )
        if row["key_hash"] != key_hash or row["key_sha256"] != key_hash:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row key hash mismatch for {artifact}: {key_hash}"
            )
        if _sha256(row_key_bytes) != key_hash:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row key bytes hash mismatch for {artifact}: {key_hash}"
            )
        if row_key_bytes != key_bytes:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row canonical request bytes mismatch: {key_hash}"
            )
        if _sha256(row_payload_bytes) != row["payload_sha256"]:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row payload bytes mismatch: {key_hash}"
            )
        row_key = json.loads(row_key_bytes.decode("utf-8"))
        row_payload = json.loads(row_payload_bytes.decode("utf-8"))
        if canonical_json_bytes(row_key) != row_key_bytes:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row non-canonical request bytes: {key_hash}"
            )
        if canonical_json_bytes(row_payload) != row_payload_bytes:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row non-canonical payload bytes: {key_hash}"
            )
        if row_key != _json_ready(key):
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row canonical request mismatch: {key_hash}"
            )
        row_corpus_version = _none_or_str(row["corpus_version"])
        if row_corpus_version not in interoperable_corpus_versions():
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row corpus version is not interoperable: {key_hash}"
            )
        if row_corpus_version != _none_or_str(key.get("corpus_version")):
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row corpus version drift: {key_hash}"
            )
        data_digests_json = canonical_json_bytes(
            key.get("data_digests", {})
        ).decode("utf-8")
        if row["data_digests_json"] != data_digests_json:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row data digest drift: {key_hash}"
            )
        return {
            "artifact": artifact,
            "key": copy.deepcopy(dict(row_key)),
            "key_hash": key_hash,
            "key_bytes": row_key_bytes.decode("utf-8"),
            "payload": copy.deepcopy(dict(row_payload)),
            "payload_hash": row["payload_sha256"],
            "cache_state": "live_fill",
        }

    def _entry_from_physics_bucket_row(
        self,
        row: sqlite3.Row,
        *,
        artifact: str,
        physics_bucket_key: Mapping[str, Any],
        physics_bucket_bytes: bytes,
        physics_bucket_hash: str,
    ) -> dict[str, Any]:
        row_physics_bytes = _sqlite_bytes(row["physics_key_bytes"])
        if row["physics_bucket_schema_version"] != str(
            physics_bucket_key.get("schema_version")
        ):
            raise PT1PersistentStoreCorrupt(
                "PT-1 row physics bucket schema drift: "
                f"{row['physics_bucket_schema_version']} != "
                f"{physics_bucket_key.get('schema_version')}"
            )
        if row["physics_bucket_sha256"] != physics_bucket_hash:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row physics bucket hash mismatch: {physics_bucket_hash}"
            )
        if row["replay_scope_sha256"] != _replay_scope_hash(physics_bucket_key):
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row replay scope hash mismatch: {physics_bucket_hash}"
            )
        if row_physics_bytes != physics_bucket_bytes:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row physics bucket bytes mismatch: {physics_bucket_hash}"
            )
        row_key_bytes = _sqlite_bytes(row["key_bytes"])
        row_key = json.loads(row_key_bytes.decode("utf-8"))
        exact_physics_key = canonical_physics_bucket_key_from_replay_key(row_key)
        exact_physics_bytes = canonical_json_bytes(exact_physics_key)
        if (
            exact_physics_bytes != physics_bucket_bytes
            or _sha256(exact_physics_bytes) != physics_bucket_hash
        ):
            raise PT1PersistentStoreCorrupt(
                "PT-1 row exact key does not reproduce physics bucket: "
                f"{physics_bucket_hash}"
            )
        return self._entry_from_row(
            row,
            artifact=artifact,
            key=row_key,
            key_bytes=row_key_bytes,
            key_hash=str(row["key_hash"]),
        )

    def _entry_from_physics_ladder_bucket_row(
        self,
        row: sqlite3.Row,
        *,
        artifact: str,
        rung_tag: str,
        rung_key: Mapping[str, Any],
        rung_hash: str,
    ) -> dict[str, Any]:
        if row[_physics_ladder_hash_column(rung_tag)] != rung_hash:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row {rung_tag} physics bucket hash mismatch: {rung_hash}"
            )
        if row["replay_scope_sha256"] != _replay_scope_hash(rung_key):
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row {rung_tag} replay scope hash mismatch: {rung_hash}"
            )
        row_distance = row[_physics_ladder_distance_column(rung_tag)]
        if row_distance is None:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row {rung_tag} distance is missing: {rung_hash}"
            )
        row_key_bytes = _sqlite_bytes(row["key_bytes"])
        row_key = json.loads(row_key_bytes.decode("utf-8"))
        row_rung_key = canonical_physics_ladder_bucket_key_from_replay_key(
            row_key,
            rung_tag,
        )
        if _sha256(canonical_json_bytes(row_rung_key)) != rung_hash:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row exact key does not reproduce {rung_tag} hash: {rung_hash}"
            )
        expected_distance = physics_ladder_bucket_distance_from_replay_key(
            row_key,
            rung_tag,
        )
        if float(row_distance) != expected_distance:
            raise PT1PersistentStoreCorrupt(
                f"PT-1 row {rung_tag} distance mismatch: {rung_hash}"
            )
        return self._entry_from_row(
            row,
            artifact=artifact,
            key=row_key,
            key_bytes=row_key_bytes,
            key_hash=str(row["key_hash"]),
        )


def canonical_replay_key(
    sim: Any,
    *,
    artifact: str,
    intent: ChemistryIntent,
    fO2_log: float | None,
    fe_redox_policy: str,
    provider_role: str | None = None,
    control_quantization: ControlQuantization | None = None,
) -> dict[str, Any]:
    quantization = control_quantization or _PRODUCTION_QUANTIZATION
    if intent == ChemistryIntent.GATE_LIQUID_FRACTION:
        if provider_role is None:
            provider_role = _gate_provider_role_for_key(sim)
        else:
            _register_gate_providers_for_key(sim)
    live_T_K = _quantize(
        float(sim.melt.temperature_C) + 273.15,
        quantization.t_k_quantum,
        quantization.t_k_decimals,
    )
    if intent == ChemistryIntent.GATE_LIQUID_FRACTION:
        # Gate curves are composition artifacts. Keep the legacy T_K field as
        # a schema sentinel; the redox control below carries the T_STD key.
        T_K = _quantize(
            _GATE_CURVE_KEY_T_K_SENTINEL,
            quantization.t_k_quantum,
            quantization.t_k_decimals,
        )
    else:
        T_K = live_T_K
    pressure_bar = _quantize(
        float(sim.melt.p_total_mbar) / 1000.0,
        quantization.pressure_bar_quantum,
        quantization.pressure_bar_decimals,
    )
    # Class-complete the fO2_log guard below: _quantize() returns None for any
    # non-finite control, so a NaN/inf melt temperature or pressure would
    # otherwise encode `None` straight into the cache key (and a None T_K would
    # flow into _compute_intrinsic_melt_fO2). Refuse here — finite controls
    # quantize to floats, so valid keys stay byte-identical (SC-49: guard every
    # sibling of a class-cutting invalid-control gate, not just the first).
    if live_T_K is None:
        raise PT0InvalidControls(
            "non-finite melt temperature passed to PT-0 cache key "
            f"quantization: {sim.melt.temperature_C!r}"
        )
    if pressure_bar is None:
        raise PT0InvalidControls(
            "non-finite melt pressure passed to PT-0 cache key "
            f"quantization: {sim.melt.p_total_mbar!r}"
        )
    if fO2_log is None:
        fO2_log = _authoritative_melt_fO2_log(sim)
    if intent == ChemistryIntent.GATE_LIQUID_FRACTION:
        # Gate curve replay follows the freeze-gate cache identity. Equilibrium
        # keys intentionally keep live-T fO2 because equilibrium solves actual
        # conditions, not a composition-only curve artifact.
        fO2_log = _melt_fO2_log_at_gate_key_reference_T(sim, fO2_log)
    quantized_fO2_log = _quantize(
        fO2_log,
        quantization.log_fo2_quantum,
        quantization.log_fo2_decimals,
    )
    if quantized_fO2_log is None:
        raise PT0InvalidControls(
            "non-finite fO2_log passed to PT-0 cache key quantization: "
            f"{fO2_log!r}"
        )
    # Same invalid-control class as T_K / pressure / fO2: _sigfig returns None on
    # non-finite input, so a non-finite commanded pO2 would otherwise encode a
    # None straight into the cache key. Commanded 0.0 (controlled-O2 off) is
    # finite and preserved; only NaN/inf is refused.
    commanded_pO2_bar = float(sim._commanded_pO2_bar())
    quantized_pO2_bar = _sigfig(
        commanded_pO2_bar,
        quantization.composition_sig_figs,
    )
    if quantized_pO2_bar is None:
        raise PT0InvalidControls(
            "non-finite commanded pO2 passed to PT-0 cache key quantization: "
            f"{commanded_pO2_bar!r} bar"
        )
    sulfur_inventory = {
        "salt_phase": _positive_float_map(
            getattr(sim.inventory, "salt_phase_kg", {}) or {}
        ),
        "sulfide_matte": _positive_float_map(
            getattr(sim.inventory, "sulfide_matte_kg", {}) or {}
        ),
    }
    provider = _provider_identity(
        sim,
        intent,
        provider_role=provider_role,
    )
    vapor_provider = _provider_identity(sim, ChemistryIntent.VAPOR_PRESSURE)
    model_identity = {
        "model": provider.get("model"),
        "mode": provider.get("mode"),
    }
    engine_version = _cache_key_engine_version(
        sim,
        intent,
        provider_identity=provider,
        provider_role=provider_role,
    )
    if engine_version is not None:
        model_identity["engine_version"] = engine_version

    # NOTE (cache identity is VERSION-based, NOT source-content-based — deliberate,
    # see commit 8d09d4f "corpus_version = sole cache lever"): the cache key uses
    # SCHEMA_VERSION + corpus_version + provider/backend identity, and intentionally
    # OMITS _source_module_digest(). A source-CONTENT digest in the key would make
    # the key differ across clusters/checkouts on byte-only differences (line
    # endings, paths) and break cross-cluster cache sharing. _source_module_digest()
    # is therefore provenance/coverage only (exercised by tests, which assert a
    # source byte-change leaves this key UNCHANGED). Tradeoff (accepted by policy):
    # a covered-module LOGIC change requires a MANUAL corpus_version/SCHEMA_VERSION
    # bump — there is no auto-tripwire (adding one would force a bump on every
    # covered edit). Do NOT "fix" this by adding the digest to the key.
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": str(artifact),
        "intent": intent.value,
        "composition_mol_fraction": _composition_mol_fraction(
            sim,
            sig_figs=quantization.composition_sig_figs,
        ),
        "controls": {
            "T_K": T_K,
            "log_fO2": quantized_fO2_log,
            "pressure_bar": pressure_bar,
            "pO2_bar": quantized_pO2_bar,
        },
        "redox": {
            "fe_redox_policy": str(fe_redox_policy),
            "fe_split": _fe_split(
                sim,
                sig_figs=quantization.composition_sig_figs,
            ),
        },
        "backend": _backend_identity_for_key(sim),
        "provider": provider,
        "vapor_pressure_provider": vapor_provider,
        "sulfur_side": {
            "S_input_ppm": _sigfig(sim._stage0_sulfur_input_ppm(), 6),
            "stage0_inventory_digest": _digest(sulfur_inventory),
            **_sulfsat_identity(getattr(sim, "_sulfsat_gate", None)),
        },
        "model": model_identity,
        "data_digests": _data_digests(sim),
        "corpus_version": current_corpus_version(),
    }


def _compatible_replay_keys(key: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    current = str(key.get("corpus_version") or "").strip()
    versions = (current,) + tuple(
        version
        for version in interoperable_corpus_versions()
        if version and version != current
    )
    result: list[dict[str, Any]] = []
    for version in versions:
        candidate = copy.deepcopy(dict(key))
        candidate["corpus_version"] = version
        backend = candidate.get("backend")
        if isinstance(backend, Mapping) and "corpus_version" in backend:
            backend["corpus_version"] = version
        result.append(candidate)
    return tuple(result)


def _expand_compatible_replay_keys(
    keys: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in keys:
        for candidate in _compatible_replay_keys(key):
            digest = _sha256(canonical_json_bytes(candidate))
            if digest in seen:
                continue
            seen.add(digest)
            expanded.append(candidate)
    return tuple(expanded)


def canonical_physics_bucket_key(
    sim: Any,
    *,
    artifact: str,
    intent: ChemistryIntent,
    fO2_log: float | None,
    fe_redox_policy: str,
    provider_role: str | None = None,
    control_quantization: ControlQuantization | None = None,
) -> dict[str, Any]:
    return canonical_physics_bucket_key_from_replay_key(
        canonical_replay_key(
            sim,
            artifact=artifact,
            intent=intent,
            fO2_log=fO2_log,
            fe_redox_policy=fe_redox_policy,
            provider_role=provider_role,
            control_quantization=control_quantization,
        )
    )


def canonical_physics_bucket_key_from_replay_key(
    key: Mapping[str, Any],
) -> dict[str, Any]:
    controls = key.get("controls", {})
    if not isinstance(controls, Mapping):
        controls = {}
    bucket_controls: dict[str, Any] = {
        "T_K": controls.get("T_K"),
        "pressure_bar": controls.get("pressure_bar"),
    }
    if _physics_bucket_consumes_log_fO2(key):
        bucket_controls["log_fO2"] = controls.get("log_fO2")
    if _physics_bucket_consumes_pO2_bar(key):
        bucket_controls["pO2_bar"] = controls.get("pO2_bar")

    sulfur_input_ppm = _sulfur_input_ppm_from_replay_key(key)
    bucket_sulfur: dict[str, Any] = {"S_input_ppm": sulfur_input_ppm}
    stage0_inventory_digest = _stage0_inventory_digest_from_replay_key(key)
    if stage0_inventory_digest is not None:
        bucket_sulfur["stage0_inventory_digest"] = stage0_inventory_digest
    replay_scope: dict[str, Any] = {
        "exact_replay_schema_version": key.get("schema_version"),
        "backend": _json_ready(key.get("backend", {})),
        "provider": _json_ready(key.get("provider", {})),
        "vapor_pressure_provider": _json_ready(
            key.get("vapor_pressure_provider", {})
        ),
        "corpus_version": key.get("corpus_version"),
        "data_digests": _solver_data_digests_from_key(key),
    }
    if sulfur_input_ppm and sulfur_input_ppm > 0.0:
        replay_scope["sulfsat"] = _sulfsat_scope_from_key(key)

    return {
        "schema_version": PHYSICS_BUCKET_SCHEMA_VERSION,
        "physics_bucket": {
            "artifact": str(key.get("artifact")),
            "intent": str(key.get("intent")),
            "composition_mol_fraction": _json_ready(
                key.get("composition_mol_fraction", [])
            ),
            "controls": bucket_controls,
            "sulfur": bucket_sulfur,
        },
        "replay_scope": replay_scope,
    }


def canonical_physics_ladder_bucket_key_from_replay_key(
    key: Mapping[str, Any],
    rung_tag: str,
) -> dict[str, Any]:
    sig_figs = _physics_ladder_sig_figs(rung_tag)
    bucket = canonical_physics_bucket_key_from_replay_key(key)
    physics_bucket = copy.deepcopy(dict(bucket["physics_bucket"]))
    controls = dict(physics_bucket.get("controls", {}) or {})
    controls["T_K"] = _sigfig_snap(controls.get("T_K"), sig_figs)
    if _physics_ladder_coarsens_controls(rung_tag):
        if "pressure_bar" in controls:
            controls["pressure_bar"] = _sigfig_snap(
                controls.get("pressure_bar"),
                sig_figs,
            )
        if "pO2_bar" in controls:
            controls["pO2_bar"] = _sigfig_snap(controls.get("pO2_bar"), sig_figs)
    physics_bucket["controls"] = controls
    physics_bucket["composition_mol_fraction"] = [
        [str(species), _sigfig_snap(float(fraction), sig_figs)]
        for species, fraction in _composition_items(
            physics_bucket.get("composition_mol_fraction", [])
        )
    ]
    physics_bucket["precision_rung"] = {
        "tag": rung_tag,
        "composition_mol_fraction_sig_figs": sig_figs,
        "T_K_sig_figs": sig_figs,
        "controls": (
            "pressure-pO2-sigfig-log_fO2-exact"
            if _physics_ladder_coarsens_controls(rung_tag)
            else "c1-exact-pressure-redox-sulfur-namespace"
        ),
    }
    return {
        "schema_version": bucket["schema_version"],
        "physics_bucket": physics_bucket,
        "replay_scope": copy.deepcopy(dict(bucket["replay_scope"])),
    }


def physics_ladder_bucket_distance_from_replay_key(
    key: Mapping[str, Any],
    rung_tag: str,
) -> float:
    sig_figs = _physics_ladder_sig_figs(rung_tag)
    exact_composition = {
        species: float(fraction)
        for species, fraction in _composition_items(
            key.get("composition_mol_fraction", [])
        )
    }
    rung_key = canonical_physics_ladder_bucket_key_from_replay_key(key, rung_tag)
    rung_bucket = rung_key.get("physics_bucket", {})
    if not isinstance(rung_bucket, Mapping):
        rung_bucket = {}
    snapped_composition = _composition_items(
        rung_bucket.get("composition_mol_fraction", [])
    )
    distance = 0.0
    for species, snapped_fraction in snapped_composition:
        exact_fraction = exact_composition.get(species, 0.0)
        distance += _normalized_sigfig_distance(
            exact_fraction,
            float(snapped_fraction),
            sig_figs,
        )
    controls = key.get("controls", {})
    if not isinstance(controls, Mapping):
        controls = {}
    rung_controls = rung_bucket.get("controls", {})
    if not isinstance(rung_controls, Mapping):
        rung_controls = {}
    distance += _normalized_sigfig_distance(
        float(controls.get("T_K", 0.0) or 0.0),
        float(rung_controls.get("T_K", 0.0) or 0.0),
        sig_figs,
    )
    if _physics_ladder_coarsens_controls(rung_tag):
        for control_name in ("pressure_bar", "pO2_bar"):
            if control_name not in controls or control_name not in rung_controls:
                continue
            exact_value = controls.get(control_name)
            snapped_value = rung_controls.get(control_name)
            if exact_value is None or snapped_value is None:
                continue
            distance += _normalized_sigfig_distance(
                float(exact_value),
                float(snapped_value),
                sig_figs,
            )
    return float(round(distance, 15))


def physics_control_rung_error_budget(
    query_key: Mapping[str, Any],
    source_key: Mapping[str, Any],
    rung_tag: str,
    *,
    source_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _physics_ladder_sig_figs(rung_tag)
    result: dict[str, Any] = {
        "term": CONTROL_RUNG_SIO_ERROR_BUDGET_TERM,
        "rung": rung_tag,
        "budget_relative_error": CONTROL_RUNG_SIO_RELATIVE_ERROR_BUDGET,
        "accepted": True,
        "refusal_reason": None,
    }
    if not _physics_ladder_coarsens_controls(rung_tag):
        result["relative_error"] = 0.0
        return result
    if not (
        _physics_bucket_consumes_pO2_bar(query_key)
        or _physics_bucket_consumes_pO2_bar(source_key)
    ):
        result["relative_error"] = 0.0
        return result

    query_pO2 = _control_float(query_key, "pO2_bar")
    source_pO2 = _control_float(source_key, "pO2_bar")
    result["query_pO2_bar"] = query_pO2
    result["source_pO2_bar"] = source_pO2
    if query_pO2 is None or source_pO2 is None or query_pO2 <= 0.0 or source_pO2 <= 0.0:
        result["accepted"] = False
        result["refusal_reason"] = "pO2_missing_or_nonpositive"
        result["relative_error"] = math.inf
        return result
    if _crosses_sio_po2_knee(query_pO2, source_pO2):
        result["accepted"] = False
        result["refusal_reason"] = "pO2_knee_crossing"
        result["relative_error"] = math.inf
        return result
    if _po2_bucket_spans_sio_knee(query_pO2, _physics_ladder_sig_figs(rung_tag)):
        result["accepted"] = False
        result["refusal_reason"] = "pO2_knee_bucket"
        result["relative_error"] = math.inf
        return result

    relative_error = abs(math.sqrt(query_pO2 / source_pO2) - 1.0)
    source_sio = _payload_sio_pressure(source_payload)
    if source_sio is not None:
        exact_estimate = source_sio * math.sqrt(source_pO2 / query_pO2)
        result["source_SiO_vapor_pressure_Pa"] = source_sio
        result["estimated_exact_SiO_vapor_pressure_Pa"] = exact_estimate
        result["absolute_error_Pa"] = abs(source_sio - exact_estimate)
    result["relative_error"] = float(relative_error)
    if relative_error > CONTROL_RUNG_SIO_RELATIVE_ERROR_BUDGET:
        result["accepted"] = False
        result["refusal_reason"] = "pO2_error_budget_exceeded"
    return result


def validate_reduced_real_equilibrium_record_key(
    artifact: str,
    key: Mapping[str, Any],
) -> None:
    if str(artifact) != "equilibrium_post_record":
        return
    provider = key.get("provider", {})
    if not isinstance(provider, Mapping):
        provider = {}
    provider_ids = {
        str(provider.get(field, "")).strip()
        for field in (
            "resolved_provider_id",
            "authoritative_provider_id",
            "fallback_provider_id",
        )
        if provider.get(field) is not None
    }
    if _BUILTIN_BACKEND_EQUILIBRIUM_PROVIDER_ID in provider_ids:
        backend = key.get("backend", {})
        if not isinstance(backend, Mapping):
            backend = {}
        if not _is_internal_analytical_backend_key(backend):
            return
        raise RuntimeError(
            "PT-1 reduced-real equilibrium_post_record rows require an "
            "authorized real provider; got builtin-backend-equilibrium. "
            "Populate with --backend alphamelts --require-magemin."
        )
    backend = key.get("backend", {})
    if not isinstance(backend, Mapping):
        backend = {}
    if _is_internal_analytical_backend_key(backend):
        raise RuntimeError(
            "PT-1 reduced-real equilibrium_post_record rows require "
            "an authorized real backend_name; got InternalAnalyticalBackend."
        )


def _is_internal_analytical_backend_key(backend: Mapping[str, Any]) -> bool:
    return any(
        str(canonical_backend_class_name(backend.get(field, ""))).strip().split(".")[-1]
        == _INTERNAL_ANALYTICAL_BACKEND_RUNTIME_NAME
        for field in ("backend_name", "backend_class")
    )


def equilibrium_payload(sim: Any, result: EquilibriumResult) -> dict[str, Any]:
    transition = getattr(result, "ledger_transition", None)
    if transition is not None:
        raise PT0CacheCollision(
            "PT-0 equilibrium replay does not support cached ledger transitions"
        )
    payload = {
        "equilibrium_result": {
            field.name: _json_ready(getattr(result, field.name))
            for field in dataclasses.fields(EquilibriumResult)
            if field.name != "ledger_transition"
        },
        "last_vapor_pressures_source": dict(
            getattr(sim, "_last_vapor_pressures_source", {}) or {}
        ),
        "last_vapor_pressure_diagnostic": _cache_payload_diagnostic(
            getattr(sim, "_last_vapor_pressure_diagnostic", {}) or {}
        ),
    }
    sulfur = getattr(result, "sulfur_saturation", None)
    payload["equilibrium_result"]["sulfur_saturation"] = _json_ready(sulfur)
    if hasattr(result, "alphamelts_diagnostics"):
        payload["alphamelts_diagnostics"] = _json_ready(
            getattr(result, "alphamelts_diagnostics")
        )
    return payload


def _cache_payload_diagnostic(value: Mapping[str, Any]) -> dict[str, Any]:
    return _json_ready(_strip_cache_inert_diagnostic_keys(value))


def _strip_cache_inert_diagnostic_keys(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_cache_inert_diagnostic_keys(item)
            for key, item in value.items()
            if not _is_cache_inert_diagnostic_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_strip_cache_inert_diagnostic_keys(item) for item in value]
    return value


def _is_cache_inert_diagnostic_key(key: Any) -> bool:
    text = str(key)
    return (
        text == "melt_regime_predicate_divergences"
        or text.endswith("_divergences")
    )


def equilibrium_from_payload(payload: Mapping[str, Any]) -> EquilibriumResult:
    data = dict(payload["equilibrium_result"])
    sulfur_data = data.pop("sulfur_saturation", None)
    data["ledger_transition"] = None
    result = EquilibriumResult(**data)
    if sulfur_data is not None:
        result.sulfur_saturation = SulfurSaturationResult(**dict(sulfur_data))
    if "alphamelts_diagnostics" in payload:
        setattr(result, "alphamelts_diagnostics", payload["alphamelts_diagnostics"])
    return result


def _normalized_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_cacheable_equilibrium_result(result: EquilibriumResult) -> bool:
    status = _normalized_status(getattr(result, "status", "ok"))
    return status in _CACHEABLE_EQUILIBRIUM_STATUSES


def _is_cacheable_gate_curve(curve: Mapping[str, Any]) -> bool:
    status = _normalized_status(
        curve.get("status") or curve.get("backend_status")
    )
    if status and status not in _CACHEABLE_GATE_STATUSES:
        return False
    calibration_status = _normalized_status(curve.get("calibration_status"))
    if (
        calibration_status
        and calibration_status not in _CACHEABLE_GATE_CALIBRATION_STATUSES
    ):
        return False
    return True


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_ready(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _curve_payload(curve: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": str(curve.get("source", "")),
        "solidus_T_C": _json_ready(curve.get("solidus_T_C")),
        "liquidus_T_C": _json_ready(curve.get("liquidus_T_C")),
        "path": [
            {
                "temperature_C": _json_ready(point[0]),
                "liquid_fraction": _json_ready(point[1]),
            }
            for point in tuple(curve.get("path") or ())
        ],
    }


def _curve_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": str(payload["source"]),
        "solidus_T_C": float(payload["solidus_T_C"]),
        "liquidus_T_C": float(payload["liquidus_T_C"]),
        "path": tuple(
            (float(point["temperature_C"]), float(point["liquid_fraction"]))
            for point in payload.get("path", ())
        ),
    }


def _gate_curve_provider_role(curve: Mapping[str, Any]) -> str | None:
    source = str(curve.get("source", ""))
    if ":fallback:" in source:
        return "fallback"
    if source.startswith("gate_liquid_fraction"):
        return "authoritative"
    return None


def _gate_provider_role_for_capture(
    sim: Any,
    curve: Mapping[str, Any],
) -> str:
    keyed_role = _gate_provider_role_for_key(sim)
    curve_role = _gate_curve_provider_role(curve)
    if curve_role is None:
        if keyed_role == "authoritative":
            raise PT0CacheCollision(
                "freeze gate curve source does not identify the "
                "authoritative provider; refusing cached-real capture"
            )
        return keyed_role
    if curve_role != keyed_role:
        if curve_role == "fallback" and _cached_real_config(sim) is None:
            return curve_role
        raise PT0CacheCollision(
            "freeze gate curve provider role mismatch: "
            f"keyed role={keyed_role}, curve source role={curve_role}"
        )
    return keyed_role


def _gate_provider_roles_for_replay(sim: Any) -> tuple[str, ...]:
    primary = _gate_provider_role_for_key(sim)
    roles = [primary]
    if primary == "authoritative" and _gate_fallback_provider_available_for_key(sim):
        roles.append("fallback")
    return tuple(dict.fromkeys(roles))


def _gate_fallback_provider_available_for_key(sim: Any) -> bool:
    _register_gate_providers_for_key(sim)
    registry = getattr(sim, "_chem_registry", None)
    if (
        registry is not None
        and registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION) is not None
    ):
        return True
    cached_real = _cached_real_provider_identity(
        sim,
        ChemistryIntent.GATE_LIQUID_FRACTION,
        provider_role="fallback",
        fallback_allowed=False,
    )
    return cached_real is not None


def _gate_provider_role_for_key(sim: Any) -> str:
    _register_gate_providers_for_key(sim)
    registry = getattr(sim, "_chem_registry", None)
    if (
        registry is not None
        and registry.authoritative_for(
            ChemistryIntent.GATE_LIQUID_FRACTION
        )
        is not None
    ):
        return "authoritative"
    authorized_name = _cached_real_authorized_backend_name(sim)
    if (
        _is_alphamelts_authorized_name(authorized_name)
        or _is_thermoengine_authorized_name(authorized_name)
    ):
        return "authoritative"
    return "fallback"


def _register_gate_providers_for_key(sim: Any) -> None:
    register_gate = getattr(
        sim, "_register_freeze_gate_liquid_fraction_providers", None
    )
    if callable(register_gate):
        register_gate()


def _composition_mol_fraction(
    sim: Any,
    *,
    sig_figs: int | None = None,
) -> list[tuple[str, float]]:
    cleaned = sim.atom_ledger.project_account_mol(_CLEANED_MELT_ACCOUNT)
    return _composition_mol_fraction_from_mol(cleaned, sig_figs=sig_figs)


def _composition_mol_fraction_from_mol(
    cleaned: Mapping[str, float],
    *,
    sig_figs: int | None = None,
) -> list[tuple[str, float]]:
    composition_sig_figs = sig_figs or _COMPOSITION_SIG_FIGS
    positive = {
        str(species): float(mol)
        for species, mol in dict(cleaned or {}).items()
        if float(mol) > _TRACE_CUTOFF
    }
    total = sum(positive.values())
    if total <= _TRACE_CUTOFF:
        return []
    result = []
    for species, mol in sorted(positive.items()):
        fraction = mol / total
        if fraction <= _TRACE_CUTOFF:
            continue
        result.append((species, _sigfig(fraction, composition_sig_figs)))
    return result


def canonicalized_composition_mol_by_account(
    composition_by_account: Mapping[str, Mapping[str, float]],
    *,
    sig_figs: int | None = None,
) -> dict[str, dict[str, float]]:
    canonical = {
        str(account): {
            str(species): float(mol)
            for species, mol in dict(species_mol or {}).items()
            if float(mol) > 0.0
        }
        for account, species_mol in dict(composition_by_account or {}).items()
    }
    cleaned = canonical.get(_CLEANED_MELT_ACCOUNT, {})
    fractions = _composition_mol_fraction_from_mol(cleaned, sig_figs=sig_figs)
    total_mol = sum(
        float(mol)
        for mol in cleaned.values()
        if float(mol) > _TRACE_CUTOFF
    )
    fraction_total = sum(fraction for _, fraction in fractions)
    if total_mol <= _TRACE_CUTOFF or fraction_total <= _TRACE_CUTOFF:
        canonical[_CLEANED_MELT_ACCOUNT] = {}
        return canonical
    canonical[_CLEANED_MELT_ACCOUNT] = {
        species: total_mol * float(fraction) / fraction_total
        for species, fraction in fractions
        if float(fraction) > 0.0
    }
    return canonical


def _fe_split(sim: Any, *, sig_figs: int | None = None) -> dict[str, float]:
    fractions = dict(_composition_mol_fraction(sim, sig_figs=sig_figs))
    return {
        "FeO": fractions.get("FeO", 0.0),
        "Fe2O3": fractions.get("Fe2O3", 0.0),
    }


def _provider_identity(
    sim: Any,
    intent: ChemistryIntent,
    *,
    provider_role: str | None = None,
) -> dict[str, Any]:
    registry = getattr(sim, "_chem_registry", None)
    auth = registry.authoritative_for(intent) if registry is not None else None
    fallback = registry.fallback_for(intent) if registry is not None else None
    fallback_allowed = False
    kernel = getattr(sim, "_chem_kernel", None)
    if kernel is not None:
        fallback_allowed = intent in getattr(kernel, "allow_fallback_intents", ())
    resolved = auth
    role = "authoritative"
    if provider_role == "fallback" and fallback is not None:
        resolved = fallback
        role = "fallback"
    elif provider_role == "authoritative":
        resolved = auth
        role = "authoritative"
    elif resolved is None and fallback is not None and fallback_allowed:
        resolved = fallback
        role = "fallback"
    if resolved is None:
        cached_real = _cached_real_provider_identity(
            sim,
            intent,
            provider_role=provider_role,
            fallback_allowed=fallback_allowed,
        )
        if cached_real is not None:
            return cached_real
    return {
        "resolved_provider_id": _provider_id(resolved),
        "resolved_role": role if resolved is not None else "none",
        "authoritative_provider_id": _provider_id(auth),
        "fallback_provider_id": _provider_id(fallback),
        "fallback_allowed": bool(fallback_allowed),
        "model": _provider_model(resolved),
        "mode": _provider_mode(resolved),
    }


def _engine_version_provenance(
    sim: Any,
    intent: ChemistryIntent,
    *,
    provider_role: str | None = None,
) -> str | None:
    backend = getattr(sim, "backend", None)
    config = _cached_real_config(sim)
    if config is not None:
        authorized_name = str(
            getattr(config, "authorized_backend_name", "")
        ).strip()
        if (
            _is_alphamelts_authorized_name(authorized_name)
            or _is_thermoengine_authorized_name(authorized_name)
        ):
            version = str(
                getattr(config, "authorized_backend_version", "")
            ).strip()
            return version or "unavailable"

    live_backend = getattr(backend, "_live_backend", None)
    if live_backend is not None:
        return _backend_version_for_key(live_backend)

    registry = getattr(sim, "_chem_registry", None)
    provider = registry.authoritative_for(intent) if registry is not None else None
    if provider_role == "fallback" and registry is not None:
        provider = registry.fallback_for(intent)
    provider_version = _provider_engine_version(provider)
    if provider_version:
        return provider_version
    return _backend_version_for_key(backend)


def _cached_real_provider_identity(
    sim: Any,
    intent: ChemistryIntent,
    *,
    provider_role: str | None,
    fallback_allowed: bool,
) -> dict[str, Any] | None:
    config = _cached_real_config(sim)
    if config is None:
        return None
    authorized_name = str(
        getattr(config, "authorized_backend_name", "")
    ).strip()
    is_alphamelts = _is_alphamelts_authorized_name(authorized_name)
    is_thermoengine = _is_thermoengine_authorized_name(authorized_name)
    if not (is_alphamelts or is_thermoengine):
        return None
    alphamelts_intents = {
        ChemistryIntent.SILICATE_LIQUIDUS,
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
        ChemistryIntent.GATE_LIQUID_FRACTION,
    }
    if intent not in alphamelts_intents:
        return None
    if intent == ChemistryIntent.GATE_LIQUID_FRACTION and provider_role == "fallback":
        return {
            "resolved_provider_id": "magemin-shadow",
            "resolved_role": "fallback",
            "authoritative_provider_id": None,
            "fallback_provider_id": "magemin-shadow",
            "fallback_allowed": bool(fallback_allowed),
            "model": "MAGEMinShadowProvider",
            "mode": "subprocess",
        }
    fallback_provider_id = (
        "magemin-shadow"
        if intent == ChemistryIntent.GATE_LIQUID_FRACTION
        else None
    )
    return {
        "resolved_provider_id": _ALPHAMELTS_PROVIDER_ID,
        "resolved_role": "authoritative",
        "authoritative_provider_id": _ALPHAMELTS_PROVIDER_ID,
        "fallback_provider_id": fallback_provider_id,
        "fallback_allowed": bool(fallback_allowed),
        "model": (
            str(getattr(config, "authorized_model", "")).strip()
            or _ALPHAMELTS_DEFAULT_MODEL
        ),
        "mode": (
            str(getattr(config, "authorized_mode", "")).strip()
            or (
                _THERMOENGINE_DEFAULT_MODE
                if is_thermoengine
                else _ALPHAMELTS_DEFAULT_MODE
            )
        ),
    }


def _provider_id(provider: Any) -> str | None:
    if provider is None:
        return None
    profile = provider.capability_profile()
    return str(profile.provider_id)


def _provider_model(provider: Any) -> str | None:
    if provider is None:
        return None
    backend = getattr(provider, "_backend", None)
    model = getattr(backend, "_model", None)
    if model is not None:
        model_text = str(model).strip()
        if model_text:
            return model_text
    return str(getattr(provider, "name", type(provider).__name__))


def _provider_mode(provider: Any) -> str | None:
    if provider is None:
        return None
    backend = getattr(provider, "_backend", None)
    mode = getattr(backend, "_mode", None)
    if mode is not None:
        mode_text = str(mode).strip()
        if mode_text:
            return mode_text
    if _provider_id(provider) == "magemin-shadow":
        return "subprocess"
    return str(type(provider).__name__)


def _cache_key_engine_version(
    sim: Any,
    intent: ChemistryIntent,
    *,
    provider_identity: Mapping[str, Any],
    provider_role: str | None = None,
) -> str | None:
    if not _is_alphamelts_key_identity(sim, provider_identity):
        return None
    return _engine_version_provenance(sim, intent, provider_role=provider_role)


def _is_alphamelts_key_identity(
    sim: Any,
    provider_identity: Mapping[str, Any],
) -> bool:
    provider_ids = {
        str(provider_identity.get("resolved_provider_id") or ""),
        str(provider_identity.get("authoritative_provider_id") or ""),
    }
    if _ALPHAMELTS_PROVIDER_ID in provider_ids:
        return True
    # A RESOLVED non-alphamelts provider (e.g. the magemin-shadow gate fallback) is NOT an
    # alphamelts key identity even when the cached-real config's authorized backend is
    # alphamelts. Do not fold engine_version into its key — that would change a non-alphamelts
    # cache identity (magemin-shadow / internal-analytical), which must stay
    # byte-identical.
    resolved = str(provider_identity.get("resolved_provider_id") or "").strip()
    if resolved and resolved != _ALPHAMELTS_PROVIDER_ID:
        return False
    backend = getattr(sim, "backend", None)
    live_backend = getattr(backend, "_live_backend", None)
    if _is_alphamelts_backend(live_backend) or _is_alphamelts_backend(backend):
        return True
    config = _cached_real_config(sim)
    if config is None:
        return False
    authorized_name = str(getattr(config, "authorized_backend_name", "")).strip()
    return _is_alphamelts_authorized_name(authorized_name)


def _is_alphamelts_authorized_name(value: Any) -> bool:
    text = str(value or "").strip().lower()
    leaf = text.rsplit(".", 1)[-1]
    return text == _ALPHAMELTS_AUTHORIZED_NAME or leaf == "alphameltsbackend"


def _provider_engine_version(provider: Any) -> str | None:
    if provider is None:
        return None
    getter = getattr(provider, "_engine_version", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001 - diagnostic only
            return "unavailable"
    backend = getattr(provider, "_backend", None)
    getter = getattr(backend, "get_engine_version", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001 - diagnostic only
            return "unavailable"
    return "unavailable"


def _equilibrium_payload_intent(sim: Any) -> ChemistryIntent:
    registry = getattr(sim, "_chem_registry", None)
    if (
        registry is not None
        and registry.authoritative_for(
            ChemistryIntent.SILICATE_EQUILIBRIUM
        )
        is not None
    ):
        return ChemistryIntent.SILICATE_EQUILIBRIUM
    authorized_name = _cached_real_authorized_backend_name(sim)
    if (
        _is_alphamelts_authorized_name(authorized_name)
        or _is_thermoengine_authorized_name(authorized_name)
    ):
        return ChemistryIntent.SILICATE_EQUILIBRIUM
    return ChemistryIntent.BACKEND_EQUILIBRIUM


def _sulfsat_identity(gate: Any) -> dict[str, Any]:
    return {
        "sulfsat_provider": type(gate).__name__,
        "sulfsat_available": _sulfsat_available(gate),
        "sulfsat_package_version": _sulfsat_package_version(gate),
        "sulfsat_calibration_version": _sulfsat_calibration_version(gate),
    }


def _sulfsat_available(gate: Any) -> bool:
    checker = getattr(gate, "is_available", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001 - diagnostic only
            return False
    return False


def _sulfsat_package_version(gate: Any) -> str:
    getter = getattr(gate, "package_version", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001 - diagnostic only
            return "unavailable"
    module = getattr(gate, "_module", None)
    version = getattr(module, "__version__", None)
    if version is not None:
        return str(version)
    return "unavailable"


def _sulfsat_calibration_version(gate: Any) -> str:
    getter = getattr(gate, "calibration_version", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001 - diagnostic only
            return "unavailable"
    return "unavailable"


def _cached_real_config(sim: Any) -> Any | None:
    backend = getattr(sim, "backend", None)
    if type(backend).__name__ != "CachedRealBackend":
        return None
    return getattr(backend, "config", None)


def _cached_real_authorized_backend_name(sim: Any) -> str | None:
    config = _cached_real_config(sim)
    if config is None:
        return None
    name = str(getattr(config, "authorized_backend_name", "")).strip().lower()
    return name or None


def _data_digests(sim: Any) -> dict[str, str]:
    return {
        "setpoints": functional_data_yaml_digest(getattr(sim, "setpoints", {})),
        "feedstocks": _digest(getattr(sim, "feedstocks", {})),
        "vapor_pressures": functional_data_yaml_digest(
            getattr(sim, "vapor_pressures", {})
        ),
        "species_formula_registry": _digest(
            getattr(sim, "species_formula_registry", {})
        ),
    }


def _solver_data_digests_from_key(key: Mapping[str, Any]) -> dict[str, Any]:
    data_digests = key.get("data_digests", {})
    if not isinstance(data_digests, Mapping):
        data_digests = {}
    return {
        name: data_digests.get(name)
        for name in (
            "setpoints",
            "feedstocks",
            "vapor_pressures",
            "species_formula_registry",
        )
        if data_digests.get(name) is not None
    }


def _stage0_inventory_digest_from_replay_key(key: Mapping[str, Any]) -> Any | None:
    sulfur_side = key.get("sulfur_side", {})
    if not isinstance(sulfur_side, Mapping):
        return None
    return sulfur_side.get("stage0_inventory_digest")


def _sulfur_input_ppm_from_replay_key(key: Mapping[str, Any]) -> float:
    sulfur_side = key.get("sulfur_side", {})
    if not isinstance(sulfur_side, Mapping):
        sulfur_side = {}
    try:
        value = float(sulfur_side.get("S_input_ppm", 0.0) or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return float(_sigfig(value, 6) or 0.0)


def _sulfsat_scope_from_key(key: Mapping[str, Any]) -> dict[str, Any]:
    sulfur_side = key.get("sulfur_side", {})
    if not isinstance(sulfur_side, Mapping):
        sulfur_side = {}
    return {
        name: sulfur_side.get(name)
        for name in (
            "sulfsat_provider",
            "sulfsat_available",
            "sulfsat_package_version",
            "sulfsat_calibration_version",
        )
        if sulfur_side.get(name) is not None
    }


def _physics_bucket_consumes_log_fO2(key: Mapping[str, Any]) -> bool:
    return str(key.get("intent")) in {
        ChemistryIntent.SILICATE_EQUILIBRIUM.value,
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION.value,
        ChemistryIntent.GATE_LIQUID_FRACTION.value,
        ChemistryIntent.BACKEND_EQUILIBRIUM.value,
    }


def _physics_bucket_consumes_pO2_bar(key: Mapping[str, Any]) -> bool:
    return str(key.get("artifact")) == "equilibrium_post_record" and bool(
        key.get("vapor_pressure_provider")
    )


def _control_float(key: Mapping[str, Any], control_name: str) -> float | None:
    controls = key.get("controls", {})
    if not isinstance(controls, Mapping):
        return None
    value = controls.get(control_name)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _crosses_sio_po2_knee(left: float, right: float) -> bool:
    low = min(float(left), float(right))
    high = max(float(left), float(right))
    return low < CONTROL_RUNG_SIO_PO2_KNEE_BAR < high


def _po2_bucket_spans_sio_knee(pO2_bar: float, sig_figs: float) -> bool:
    center = _sigfig_snap(pO2_bar, sig_figs)
    if center is None:
        return True
    quantum = _sigfig_quantum(float(pO2_bar), sig_figs)
    low = float(center) - 0.5 * quantum
    high = float(center) + 0.5 * quantum
    return low < CONTROL_RUNG_SIO_PO2_KNEE_BAR < high


def _payload_sio_pressure(payload: Mapping[str, Any] | None) -> float | None:
    if not isinstance(payload, Mapping):
        return None
    result = payload.get("equilibrium_result", {})
    if not isinstance(result, Mapping):
        return None
    vapor_pressures = result.get("vapor_pressures_Pa", {})
    if not isinstance(vapor_pressures, Mapping):
        return None
    value = vapor_pressures.get("SiO")
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0:
        return None
    return number


def _replay_scope_hash(physics_bucket_key: Mapping[str, Any]) -> str:
    return _digest(physics_bucket_key.get("replay_scope", {}))


def _physics_ladder_values_from_replay_key(
    key: Mapping[str, Any],
) -> dict[str, dict[str, float | str]]:
    values: dict[str, dict[str, float | str]] = {}
    for rung_tag, _sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS:
        rung_key = canonical_physics_ladder_bucket_key_from_replay_key(
            key,
            rung_tag,
        )
        values[rung_tag] = {
            "sha256": _sha256(canonical_json_bytes(rung_key)),
            "distance": physics_ladder_bucket_distance_from_replay_key(
                key,
                rung_tag,
            ),
        }
    return values


def _physics_ladder_sig_figs(rung_tag: str) -> float:
    for candidate_tag, sig_figs in PHYSICS_BUCKET_ALL_LADDER_RUNGS:
        if rung_tag == candidate_tag:
            return float(sig_figs)
    raise ValueError(f"unknown physics bucket precision rung: {rung_tag!r}")


def _physics_ladder_coarsens_controls(rung_tag: str) -> bool:
    return any(
        rung_tag == candidate_tag
        for candidate_tag, _sig_figs in PHYSICS_BUCKET_CONTROL_LADDER_RUNGS
    )


def _physics_ladder_hash_column(rung_tag: str) -> str:
    _physics_ladder_sig_figs(rung_tag)
    return f"physics_bucket_{rung_tag}_sha256"


def _physics_ladder_distance_column(rung_tag: str) -> str:
    _physics_ladder_sig_figs(rung_tag)
    return f"physics_bucket_{rung_tag}_distance"


def _composition_items(value: Any) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    for item in value or []:
        if isinstance(item, Mapping):
            species = item.get("species")
            fraction = item.get("mol_fraction")
        else:
            species, fraction = item
        items.append((str(species), float(fraction)))
    return sorted(items)


@lru_cache(maxsize=1)
def _source_module_digest() -> dict[str, Any]:
    root = _repo_root()
    rel_paths = _source_module_paths(root)
    file_digests = []
    for rel_path in rel_paths:
        file_digests.append(
            {
                "path": rel_path,
                "sha256": _sha256((root / rel_path).read_bytes()),
            }
        )
    return {
        "module_set": _SOURCE_MODULE_SET_ID,
        "algorithm": "sha256",
        "paths": list(rel_paths),
        "sha256": _sha256(canonical_json_bytes(file_digests)),
    }


def _source_module_paths(root: Path) -> tuple[str, ...]:
    rel_paths: set[str] = set()
    missing: list[str] = []
    for pattern in _SOURCE_MODULE_PATTERNS:
        matches = sorted(root.glob(pattern))
        if not matches:
            missing.append(pattern)
            continue
        rel_paths.update(path.relative_to(root).as_posix() for path in matches)
    if missing:
        raise RuntimeError(
            "PT-0 source digest module set has missing patterns: "
            + ", ".join(missing)
        )
    return tuple(sorted(rel_paths))


def _code_version() -> str:
    root = _repo_root()
    path = root / "VERSION"
    if path.exists():
        return path.read_text().strip()
    return "unknown"


def _git_dirty() -> int:
    root = _repo_root()
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--short"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:  # noqa: BLE001 - diagnostic metadata only
        return 1
    return 1 if result.stdout.strip() else 0


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _none_or_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _sqlite_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode("utf-8")
    return bytes(value)


def _qualified_type(value: Any) -> str:
    if value is None:
        return "None"
    cls = type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def _backend_identity_for_key(sim: Any) -> dict[str, str]:
    backend = getattr(sim, "backend", None)
    config = getattr(backend, "config", None)
    if type(backend).__name__ == "CachedRealBackend" and config is not None:
        authorized_name = str(
            getattr(config, "authorized_backend_name", "")
        ).strip()
        if not authorized_name:
            raise RuntimeError(
                "cached-real cache key requires configured authorized backend name"
            )
        return _authorized_backend_identity_for_key(authorized_name)
    return {
        "backend_name": _backend_name_for_key(backend),
        "backend_class": _backend_class_for_key(backend),
        "corpus_version": current_corpus_version(),
    }


def _authorized_backend_identity_for_key(
    authorized_name: str,
) -> dict[str, str]:
    name = str(authorized_name).strip()
    if _is_alphamelts_authorized_name(name):
        return {
            "backend_name": _ALPHAMELTS_BACKEND_NAME,
            "backend_class": _ALPHAMELTS_BACKEND_CLASS,
            "corpus_version": current_corpus_version(),
        }
    if _is_thermoengine_authorized_name(name):
        return {
            'backend_name': _THERMOENGINE_BACKEND_NAME,
            'backend_class': _THERMOENGINE_BACKEND_CLASS,
            'corpus_version': current_corpus_version(),
        }
    return {
        "backend_name": name,
        "backend_class": name,
        "corpus_version": current_corpus_version(),
    }


def _backend_name_for_key(backend: Any) -> str:
    if _is_alphamelts_backend(backend):
        return _ALPHAMELTS_BACKEND_NAME
    if _is_thermoengine_backend(backend):
        return _THERMOENGINE_BACKEND_NAME
    if type(backend).__name__ == _INTERNAL_ANALYTICAL_BACKEND_RUNTIME_NAME:
        return _INTERNAL_ANALYTICAL_BACKEND_SERIALIZED_NAME
    return type(backend).__name__


def _backend_class_for_key(backend: Any) -> str:
    if _is_alphamelts_backend(backend):
        return _ALPHAMELTS_BACKEND_CLASS
    if _is_thermoengine_backend(backend):
        return _THERMOENGINE_BACKEND_CLASS
    if type(backend).__name__ == _INTERNAL_ANALYTICAL_BACKEND_RUNTIME_NAME:
        return _INTERNAL_ANALYTICAL_BACKEND_SERIALIZED_CLASS
    return _qualified_type(backend)


def _backend_version_for_key(backend: Any) -> str:
    getter = getattr(backend, "get_engine_version", None)
    if callable(getter):
        try:
            version = str(getter()).strip()
        except Exception:  # noqa: BLE001 - diagnostic cache identity only
            version = "unavailable"
        if version:
            return version
    return "unavailable"


def _is_alphamelts_backend(backend: Any) -> bool:
    if backend is None:
        return False
    if bool(getattr(backend, '_legacy_alphamelts_cache_identity', False)):
        return True
    return any(
        cls.__name__ == _ALPHAMELTS_BACKEND_NAME
        for cls in type(backend).__mro__
    )


def _is_thermoengine_backend(backend: Any) -> bool:
    if backend is None or bool(
        getattr(backend, '_legacy_alphamelts_cache_identity', False)
    ):
        return False
    declared_name = (
        getattr(backend, 'backend_name', None)
        or getattr(backend, 'name', None)
    )
    if str(declared_name or '').strip().lower() == _THERMOENGINE_AUTHORIZED_NAME:
        return True
    return any(
        cls.__name__ == _THERMOENGINE_BACKEND_NAME
        for cls in type(backend).__mro__
    )


def _is_thermoengine_authorized_name(value: Any) -> bool:
    text = str(value or '').strip().lower()
    leaf = text.rsplit('.', 1)[-1]
    return text == _THERMOENGINE_AUTHORIZED_NAME or leaf == (
        _THERMOENGINE_BACKEND_NAME.lower()
    )


def _positive_float_map(value: Mapping[str, Any]) -> dict[str, float]:
    return {
        str(key): float(item)
        for key, item in dict(value or {}).items()
        if item is not None and float(item) > 0.0
    }


def _json_ready(value: Any, path: str = "$") -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_ready(
                getattr(value, field.name),
                f"{path}.{field.name}",
            )
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _json_ready(item, f"{path}.{str(key)}")
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_ready(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PT0NonFinitePayload(
                f"non-finite value in PT-0 payload at {path}: {value!r}"
            )
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return repr(value)


def _digest(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _quantize_decimals(quantum: float) -> int:
    try:
        decimal = Decimal(str(float(quantum))).normalize()
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid quantization quantum: {quantum!r}") from exc
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError(f"quantization quantum must be positive finite: {quantum!r}")
    return max(0, -decimal.as_tuple().exponent)


def _quantize(value: float | None, quantum: float, digits: int) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    bucket = round(number / quantum) * quantum
    return round(bucket, digits)


def _sigfig(value: float | None, sig_figs: int) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number == 0.0:
        return 0.0
    digits = sig_figs - int(math.floor(math.log10(abs(number)))) - 1
    return round(number, digits)


def _sigfig_quantum(value: float, sig_figs: float) -> float:
    number = abs(float(value))
    if number == 0.0:
        return 10.0 ** (1.0 - float(sig_figs))
    return 10.0 ** (math.floor(math.log10(number)) + 1.0 - float(sig_figs))


def _sigfig_snap(value: Any, sig_figs: float) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number == 0.0:
        return 0.0
    quantum = _sigfig_quantum(number, sig_figs)
    snapped = round(number / quantum) * quantum
    if snapped == 0.0:
        return 0.0
    digits = max(
        0,
        int(math.ceil(float(sig_figs) - math.floor(math.log10(abs(snapped))) - 1)),
    )
    return round(snapped, digits)


def _normalized_sigfig_distance(
    exact_value: float,
    snapped_center: float,
    sig_figs: float,
) -> float:
    quantum = _sigfig_quantum(exact_value if exact_value != 0.0 else snapped_center, sig_figs)
    if quantum == 0.0:
        return 0.0
    delta = (float(exact_value) - float(snapped_center)) / quantum
    return delta * delta


def _diff_top_fields(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    fields = []
    keys = sorted(set(left) | set(right))
    for key in keys:
        if _json_ready(left.get(key)) != _json_ready(right.get(key)):
            fields.append(str(key))
    return fields
