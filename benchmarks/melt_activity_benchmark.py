#!/usr/bin/env python3
"""Repeatable melt-activity comparison and composition-domain benchmark.

The harness reads a tracked YAML bench set, runs a configurable engine set,
captures every point as ``ok``, ``out_of_domain``, ``crash``, ``refused``,
``observable_unavailable``, or ``unavailable``, and writes deterministic
CSV/JSON/Markdown artifacts. For
gas observables it converts parent-formula activities to each rail's
single-cation basis, then holds the tracked analytical gas layer constant and
swaps only that converted melt activity. Activity coefficients are normalized
to ``gamma = a/x`` on the parent-oxide formula-unit basis. Engine crashes are
data, not process failures.

Examples
--------
Run the complete tracked benchmark::

    .venv/bin/python benchmarks/melt_activity_benchmark.py

Generate only the stripping-trajectory coverage map::

    .venv/bin/python benchmarks/melt_activity_benchmark.py --mode coverage

Regeneration is guarded against silent shrinkage: a run refuses to replace
the output set with a smaller one — e.g. disabling the live VapoRock anchor
check, which is on by default, or narrowing ``--mode`` on a populated
directory — unless each dropped artifact is explicitly retired with
``--retire-artifact NAME`` (logged as a warning and in run-metadata.json).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import warnings
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.regeneration_guard import (
    RegenerationShrinkageError,
    regeneration_guard,
)

DEFAULT_BENCH_SET = REPO_ROOT / "data/melt_activity/basalt-bench-set-v1.yaml"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "benchmarks/results/melt_activity"
DEFAULT_ENGINES = (
    "imcc-published",
    "imcc-ext",
    "internal_analytic",
    "alphamelts",
    "thermoengine",
    "vaporock",
)
# Rail-owned MELTS SiO2 trust window. Default matches the historical
# engine-imposed [30, 80] so coverage goldens stay put. This is not the
# adapter's two-component alkali-silica crash guard.
DEFAULT_MELTS_SILICATE_NETWORK_BAND_WT_PCT = (30.0, 80.0)
VAPOROCK_PRESSURE_OBSERVABLES = frozenset({"partial_pressure", "evaporation_flux"})
SF04_ANCHOR_SPECIES = ("SiO2", "FeO", "Fe", "Mg", "SiO", "K", "Na", "O2")
SF04_SHEET_COMPOSITIONS = {
    "tho": "sf04_tholeiite",
    "aba": "sf04_alkali_basalt",
    "kom": "sf04_komatiite",
    "dun": "sf04_dunite",
}
POINT_STATUSES = frozenset(
    {
        "ok",
        "out_of_domain",
        "crash",
        "refused",
        "observable_unavailable",
        "unavailable",
    }
)
COVERAGE_PRESSURE_FAMILIES = frozenset({"partial_pressure", "evaporation_flux"})


@dataclass(frozen=True)
class EngineResult:
    """One engine result on the parent-oxide formula-unit reference basis."""

    status: str
    activities: Mapping[str, float] = field(default_factory=dict)
    gammas: Mapping[str, float] = field(default_factory=dict)
    reason: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)
    partial_pressures: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in POINT_STATUSES:
            raise ValueError(f"unknown benchmark status {self.status!r}")


class MeltActivityEngine(Protocol):
    name: str

    def evaluate(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_K: float,
        fO2_bar: float | None,
    ) -> EngineResult: ...

    def coverage(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_K: float,
    ) -> EngineResult: ...


def _repo_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _positive_finite(values: Mapping[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    nonfinite: list[str] = []
    for key, raw in values.items():
        value = float(raw)
        if not math.isfinite(value):
            nonfinite.append(f"{key}={value!r}")
            continue
        if value > 0.0:
            result[str(key)] = value
    if nonfinite:
        raise ValueError(
            "non-finite melt-activity value refused (would silently shrink "
            f"the sample): {', '.join(nonfinite)}"
        )
    return result


def _normalize_wt(values: Mapping[str, Any]) -> dict[str, float]:
    positive = _positive_finite(values)
    total = sum(positive.values())
    if total <= 0.0:
        return {}
    return {key: 100.0 * value / total for key, value in positive.items()}


def _wt_to_mol(values: Mapping[str, Any]) -> dict[str, float]:
    from simulator.accounting.formulas import resolve_species_formula

    return {
        oxide: (float(wt) / 100.0)
        / resolve_species_formula(oxide).molar_mass_kg_per_mol()
        for oxide, wt in _positive_finite(values).items()
    }


def _oxide_mole_fractions(values: Mapping[str, Any]) -> dict[str, float]:
    mol = _wt_to_mol(values)
    total = sum(mol.values())
    return {key: value / total for key, value in mol.items()} if total else {}


def _oxide_cations_per_formula(parent_oxide: str) -> float:
    from simulator.accounting.formulas import resolve_species_formula

    formula = resolve_species_formula(str(parent_oxide))
    cations = [
        float(count)
        for element, count in formula.elements.items()
        if element != "O"
    ]
    if "O" not in formula.elements or len(cations) != 1:
        raise ValueError(
            f"expected a one-cation-family oxide formula, got {parent_oxide!r}"
        )
    return cations[0]


def _single_cation_gas_activities(
    parent_oxide_activities: Mapping[str, float],
) -> dict[str, float]:
    converted: dict[str, float] = {}
    for parent_oxide, activity in parent_oxide_activities.items():
        cations = _oxide_cations_per_formula(parent_oxide)
        # Premise: M_nO_m contains n units of the single-cation component
        # MO_(m/n). Algebra: a_parent = a_single**n, so
        # a_single = a_parent**(1/n). Sanity: K2O/Na2O/Li2O use sqrt(a_M2O).
        converted[str(parent_oxide)] = float(activity) ** (1.0 / cations)
    return _positive_finite(converted)


def _reason_line(value: Any) -> str:
    return " ".join(str(value or "").split())[:800]


def coverage_observable_is_pressure(result: EngineResult) -> bool:
    """Whether this coverage result's observable is a pressure map.

    Declared non-pressure families (activity, domain_gate) are not
    pressure-scored. An undeclared family fails closed: ok + empty
    pressures must not stay ok.
    """
    family = result.details.get("observable_family")
    if family in COVERAGE_PRESSURE_FAMILIES:
        return True
    if family:
        return False
    return True


def coverage_cell_accepted(row: Mapping[str, Any], engine: str) -> bool:
    """Accepted coverage cell: ok, and not a recorded hollow prediction."""
    if row.get(f"{engine}_status") != "ok":
        return False
    if row.get(f"{engine}_finite_prediction") is False:
        return False
    return True


def _alphamelts_failure_details(values: Mapping[str, Any]) -> dict[str, Any]:
    failure = dict(values.get("subprocess_failure", {}) or {})
    return {
        key: value
        for key, value in {
            "backend_failure_category": values.get("backend_failure_category"),
            "backend_failure_reason_code": values.get("backend_failure_reason_code"),
            "backend_status_reason": values.get("backend_status_reason"),
            "returncode": failure.get("returncode"),
            "signal": failure.get("signal"),
            "stage": failure.get("stage"),
        }.items()
        if value is not None
    }


def _keep_handle_exception_types() -> tuple[type[BaseException], ...]:
    """Typed row-local exceptions that must not become status=unavailable.

    Classification is by type, not message text. A keep-handle exception
    whose str() happens to contain "unavailable" is still a row-local
    refusal; the structural latch detector trusts status=unavailable as
    adapter-absence, so a substring test here would re-introduce the
    prose coupling one layer up.
    """
    from engines.alphamelts.thermoengine import (
        ThermoEngineFO2UndefinedError,
        ThermoEngineNonFiniteField,
    )
    from simulator.engine_pool import EngineWorkerTimeout

    return (
        EngineWorkerTimeout,
        ThermoEngineFO2UndefinedError,
        ThermoEngineNonFiniteField,
    )


def classify_engine_exception(exc: BaseException) -> tuple[str, str]:
    """Map an engine-boundary exception into the benchmark status vocabulary."""

    text = _reason_line(f"{type(exc).__name__}: {exc}")
    if isinstance(exc, _keep_handle_exception_types()):
        return "refused", text
    lowered = text.lower()
    category = str(getattr(exc, "backend_failure_category", "") or "").lower()
    if category == "engine_crash" or any(
        token in lowered for token in ("sigsegv", "sigabrt", "signal 11", "signal 6")
    ):
        return "crash", text
    if "unavailable" in lowered or category == "backend_unavailable":
        return "unavailable", text
    if "outside" in lowered or "out_of_domain" in lowered:
        return "out_of_domain", text
    return "refused", text


def execute_engine(
    engine: MeltActivityEngine,
    composition_wt_pct: Mapping[str, float],
    temperature_K: float,
    fO2_bar: float | None,
) -> EngineResult:
    """Run one engine without allowing a provider crash to abort the harness."""

    try:
        return engine.evaluate(composition_wt_pct, temperature_K, fO2_bar)
    except Exception as exc:
        status, reason = classify_engine_exception(exc)
        return EngineResult(status=status, reason=reason)


class ImccEngine:
    """Published or explicitly labelled research IMCC-SF04 adapter."""

    def __init__(
        self,
        name: str,
        pack_path: Path,
        *,
        published: bool,
        allow_extrapolation: bool = False,
        allow_out_of_envelope: bool = False,
    ) -> None:
        self.name = name
        self.pack_path = pack_path
        self.published = published
        self.allow_extrapolation = allow_extrapolation
        self.allow_out_of_envelope = allow_out_of_envelope
        self._pack: Any | None = None

    def _load(self) -> Any:
        if self._pack is not None:
            return self._pack
        from simulator.melt_backend.imcc_sf04 import (
            ImccDatapack,
            label_research_datapack,
            load_datapack,
        )

        if self.published:
            self._pack = load_datapack(self.pack_path)
            return self._pack
        raw = json.loads(self.pack_path.read_text(encoding="utf-8"))
        parents = tuple(str(value) for value in raw["parents"])
        rows = list(raw["rows"])
        datapack = ImccDatapack(
            reactions=[str(row["complex"]) for row in rows],
            nu=np.asarray(
                [
                    [float(row["nu"].get(parent, 0.0)) for row in rows]
                    for parent in parents
                ],
                dtype=float,
            ),
            A=np.asarray([float(row["A"]) for row in rows], dtype=float),
            B=np.asarray([float(row["B"]) for row in rows], dtype=float),
            domains=[tuple(float(v) for v in row["T_domain_K"]) for row in rows],
            version=str(raw["imcc_sf04_datapack_version"]),
            parent_oxides=parents,
        )
        self._pack = label_research_datapack(
            datapack,
            model_id="IMCC-SF04-EXT",
            coverage="reviewed-central-table-research-extension",
        )
        return self._pack

    def evaluate(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_K: float,
        fO2_bar: float | None,
    ) -> EngineResult:
        del fO2_bar
        from simulator.melt_backend.imcc_sf04 import (
            ImccCompositionOutsideValidatedEnvelopeError,
            ImccComponentOutsideDomainError,
            ImccCompositionIncompleteError,
            ImccFerricInputUnsupportedError,
            ImccNonconvergenceError,
            ImccRefusal,
            ImccTOutsideDatapackDomainError,
            evaluate,
        )

        pack = self._load()
        try:
            result = evaluate(
                composition_wt_pct,
                float(temperature_K),
                pack,
                basis_type="wt",
                enable_sp_extension=not self.published,
                allow_extrapolation=self.allow_extrapolation,
                allow_out_of_envelope=self.allow_out_of_envelope,
            )
        except (
            ImccCompositionOutsideValidatedEnvelopeError,
            ImccComponentOutsideDomainError,
            ImccCompositionIncompleteError,
            ImccFerricInputUnsupportedError,
            ImccTOutsideDatapackDomainError,
        ) as exc:
            return EngineResult(status="out_of_domain", reason=_reason_line(exc))
        except ImccNonconvergenceError as exc:
            return EngineResult(status="refused", reason=_reason_line(exc))
        except ImccRefusal as exc:
            return EngineResult(status="refused", reason=_reason_line(exc))
        activities = {
            oxide: float(value)
            for oxide, value in zip(result.parent_oxides, result.parent_activity)
        }
        gammas = {
            oxide: float(value)
            for oxide, value in zip(result.parent_oxides, result.parent_gamma)
        }
        labels = result.labels
        details: dict[str, Any] = {
            "model_id": labels.identity["model_id"],
            "datapack_version": labels.identity["datapack_version"],
            "trust": labels.trust,
            "envelope_status": labels.envelope_status,
            "pack_sha256": _sha256(self.pack_path),
            "observable_family": "activity",
        }
        if self.allow_extrapolation or self.allow_out_of_envelope:
            # Temperature mark is orthogonal to envelope_status. Publish
            # it only on the flagged pass so the strict details JSON
            # stays bit-identical.
            details["extrapolated"] = bool(result.extrapolated)
        return EngineResult(
            status="ok",
            activities=activities,
            gammas=gammas,
            details=details,
        )

    def coverage(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_K: float,
    ) -> EngineResult:
        return self.evaluate(composition_wt_pct, temperature_K, 1.0e-9)


class InternalAnalyticalEngine:
    """Adapter for the simulator's active builtin analytical fallback."""

    name = "internal_analytic"

    def __init__(self) -> None:
        self._vapor_pressure_data: dict[str, Any] | None = None

    def _load_vapor_pressure_data(self) -> dict[str, Any]:
        if self._vapor_pressure_data is None:
            self._vapor_pressure_data = yaml.safe_load(
                (REPO_ROOT / "data/vapor_pressures.yaml").read_text(encoding="utf-8")
            )
        return self._vapor_pressure_data

    def evaluate(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_K: float,
        fO2_bar: float | None,
    ) -> EngineResult:
        from simulator.core import PyrolysisSimulator
        from simulator.melt_backend.base import InternalAnalyticalBackend

        backend = InternalAnalyticalBackend()
        backend.initialize({})
        sim = PyrolysisSimulator(
            backend,
            {"campaigns": {}},
            {
                "benchmark": {
                    "label": "Melt-activity benchmark composition",
                    "composition_wt_pct": dict(composition_wt_pct),
                }
            },
            self._load_vapor_pressure_data(),
        )
        sim.load_batch("benchmark", mass_kg=100.0)
        sim.melt.temperature_C = float(temperature_K) - 273.15
        if fO2_bar is not None:
            sim.melt.p_total_mbar = max(1.0e-3, float(fO2_bar) * 1000.0)
            sim.melt.pO2_mbar = float(fO2_bar) * 1000.0
            sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = math.log10(
                float(fO2_bar)
            )
            sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = float(fO2_bar)
        result = sim._get_equilibrium()
        raw_status = str(result.status)
        status = (
            raw_status
            if raw_status in POINT_STATUSES
            else "refused"
        )
        diagnostics = dict(result.diagnostics or {})
        provenance = dict(diagnostics.get("activity_provenance", {}) or {})
        activities: dict[str, float] = {}
        gammas: dict[str, float] = {}
        parent_mole_fractions = _oxide_mole_fractions(composition_wt_pct)
        for section in ("metals", "oxide_vapors"):
            for species, rail in sim.vapor_pressures.get(section, {}).items():
                parent = str(rail.get("parent_oxide", "") or "")
                activity = dict(provenance.get(species, {}) or {})
                if not parent or not activity:
                    continue
                activity_value = activity.get("melt_oxide_activity")
                gamma_value = activity.get("melt_oxide_effective_gamma")
                x_single_value = activity.get("melt_oxide_X_single_cation")
                cations = _oxide_cations_per_formula(parent)
                if activity_value is not None:
                    activities[parent] = float(activity_value) ** cations
                x_parent = float(parent_mole_fractions.get(parent, 0.0) or 0.0)
                if (
                    gamma_value is not None
                    and x_single_value is not None
                    and x_parent > 0.0
                ):
                    # Internal provenance reports gamma_single on X_single.
                    # Since a_single=gamma_single*X_single and
                    # a_parent=a_single**n, gamma_parent=a_parent/X_parent.
                    single_activity = float(gamma_value) * float(x_single_value)
                    gammas[parent] = single_activity**cations / x_parent
        return EngineResult(
            status=status,
            activities=_positive_finite(activities),
            gammas=_positive_finite(gammas),
            reason=(
                ""
                if status == "ok"
                else _reason_line("; ".join(str(value) for value in result.warnings))
            ),
            details={
                "backend": "internal-analytical",
                "activities_provider": diagnostics.get("activities_provider"),
                "activity_standard_state": diagnostics.get(
                    "activities_standard_state"
                ),
                "source_activity_basis": "gamma_x_single_cation",
                "benchmark_activity_basis": "parent_oxide_formula_unit",
                "gas_path": "shared_tracked_analytical",
                "warning_count": len(result.warnings),
                "observable_family": "activity",
            },
        )

    def coverage(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_K: float,
    ) -> EngineResult:
        return self.evaluate(composition_wt_pct, temperature_K, 1.0e-9)


class AlphaMeltsEngine:
    """AlphaMELTS diagnostic provider with typed subprocess failure capture."""

    name = "alphamelts"

    def __init__(
        self,
        timeout_s: float = 30.0,
        silicate_network_band: tuple[float, float] | None = None,
    ) -> None:
        self.timeout_s = float(timeout_s)
        self.silicate_network_band = (
            DEFAULT_MELTS_SILICATE_NETWORK_BAND_WT_PCT
            if silicate_network_band is None
            else (float(silicate_network_band[0]), float(silicate_network_band[1]))
        )
        self._provider: Any | None = None
        self._initialization_error = ""

    def _initialize(self) -> Any | None:
        if self._provider is not None or self._initialization_error:
            return self._provider
        from engines.alphamelts.provider import AlphaMELTSProvider
        from simulator.melt_backend.alphamelts import AlphaMELTSBackend

        try:
            backend = AlphaMELTSBackend()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                available = backend.initialize(
                    {"execution_mode": "subprocess", "timeout_s": self.timeout_s}
                )
            if not available:
                self._initialization_error = "AlphaMELTS backend unavailable"
                return None
            self._provider = AlphaMELTSProvider(backend)
        except Exception as exc:
            self._initialization_error = _reason_line(exc)
        return self._provider

    def evaluate(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_K: float,
        fO2_bar: float | None,
    ) -> EngineResult:
        provider = self._initialize()
        if provider is None:
            return EngineResult(status="unavailable", reason=self._initialization_error)
        from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
        from simulator.chemistry.kernel.dto import ProviderAccountView

        fO2_log = None if fO2_bar is None else math.log10(float(fO2_bar))
        request = IntentRequest(
            intent=ChemistryIntent.SILICATE_EQUILIBRIUM,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": _wt_to_mol(composition_wt_pct)},
                species_formula_registry={},
            ),
            temperature_C=float(temperature_K) - 273.15,
            pressure_bar=1.0,
            fO2_log=fO2_log,
            control_inputs={},
        )
        result = provider.dispatch(request)
        diagnostic = dict(result.diagnostic or {})
        backend_diag = dict(diagnostic.get("backend_diagnostics", {}) or {})
        status = str(result.status)
        if str(backend_diag.get("backend_failure_category", "")) == "engine_crash":
            return EngineResult(
                status="crash",
                reason=_reason_line(
                    backend_diag.get("backend_status_reason_message")
                    or "; ".join(result.warnings)
                ),
                details=_alphamelts_failure_details(backend_diag),
            )
        if status == "not_converged":
            reason = _reason_line(
                backend_diag.get("backend_status_reason")
                or diagnostic.get("backend_status_reason")
                or "; ".join(result.warnings)
            )
            if "sig" in reason.lower():
                return EngineResult(
                    status="crash",
                    reason=reason,
                    details=_alphamelts_failure_details(backend_diag),
                )
            return EngineResult(status="refused", reason=reason, details=backend_diag)
        if status in {"out_of_domain", "unavailable"}:
            return EngineResult(
                status=status,
                reason=_reason_line("; ".join(result.warnings)),
                details=backend_diag,
            )
        if status != "ok":
            return EngineResult(
                status="refused",
                reason=_reason_line(f"provider status {status}: {'; '.join(result.warnings)}"),
                details=backend_diag,
            )
        reported_activities = _positive_finite(
            backend_diag.get("diagnostic_reported_activities", {}) or {}
        )
        activities = _positive_finite(
            backend_diag.get("diagnostic_oxide_activities", {}) or {}
        )
        activity_label_map = dict(
            backend_diag.get("diagnostic_activity_label_map", {}) or {}
        )
        unmapped_activity_labels = sorted(
            label
            for label, label_details in activity_label_map.items()
            if not dict(label_details or {}).get("oxide_activity")
        )
        if not activities:
            return EngineResult(
                status="ok",
                details={
                    "equilibrium_completed": True,
                    "execution_status": "completed_without_observable",
                    "observable_supported": False,
                    "finite_prediction": False,
                    "mode": diagnostic.get("mode"),
                    "engine_version": diagnostic.get("engine_version"),
                    "activity_basis": backend_diag.get("diagnostic_activity_basis"),
                    "reported_activity_labels": sorted(reported_activities),
                    "unmapped_activity_labels": unmapped_activity_labels,
                },
            )
        x = _oxide_mole_fractions(composition_wt_pct)
        gammas = {
            oxide: activity / x[oxide]
            for oxide, activity in activities.items()
            if x.get(oxide, 0.0) > 0.0
        }
        return EngineResult(
            status="ok",
            activities=activities,
            gammas=gammas,
            details={
                "equilibrium_completed": True,
                "execution_status": "converged",
                "observable_supported": True,
                "finite_prediction": True,
                "mode": diagnostic.get("mode"),
                "engine_version": diagnostic.get("engine_version"),
                "activity_basis": backend_diag.get("diagnostic_activity_basis"),
                "reported_activity_labels": sorted(reported_activities),
                "unmapped_activity_labels": unmapped_activity_labels,
            },
        )

    def coverage(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_K: float,
    ) -> EngineResult:
        del temperature_K
        from engines.alphamelts.domain import AlphaMELTSDomainGate

        assessment = AlphaMELTSDomainGate.assess(
            composition_wt_pct,
            silicate_network_band=self.silicate_network_band,
        )
        return EngineResult(
            status="ok" if assessment.valid else "out_of_domain",
            reason=(
                ""
                if assessment.valid
                else _reason_line("; ".join(assessment.warnings))
            ),
            details={
                "domain_reason": assessment.reason,
                "failed_constraints": list(assessment.failed_constraints),
                "silicate_network_band_wt_pct": list(
                    assessment.silicate_network_band_wt_pct
                ),
                "observable_family": "domain_gate",
            },
        )


class ThermoEngineMeltActivityEngine(AlphaMeltsEngine):
    """In-process ThermoEngine MELTS diagnostic with intrinsic-fO2 support."""

    name = "thermoengine"

    def transport_close_count(self) -> int:
        """How many times this instance tore down a live transport."""
        provider = self._provider
        if provider is None:
            return 0
        getter = getattr(provider, "transport_close_count", None)
        if callable(getter):
            return int(getter())
        return 0

    def transport_closed_mid_run(self) -> bool:
        return self.transport_close_count() > 0

    def _initialize(self) -> Any | None:
        if self._provider is not None or self._initialization_error:
            return self._provider
        from engines.alphamelts.provider import AlphaMELTSProvider
        from simulator.melt_backend.thermoengine import ThermoEngineBackend

        try:
            backend = ThermoEngineBackend()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                available = backend.initialize({
                    "thermoengine_equilibrate_timeout_s": self.timeout_s,
                })
            if not available:
                self._initialization_error = "ThermoEngine backend unavailable"
                return None
            self._provider = AlphaMELTSProvider(backend)
        except Exception as exc:
            self._initialization_error = _reason_line(exc)
        return self._provider


class VapoRockEngine:
    """VapoRock vapour-pressure (offgas) leg.

    VapoRock emits log10 partial pressures, not per-oxide melt
    activities. Activity/gamma calls are typed as a missing observable
    without asking the library for a quantity it does not produce.
    """

    name = "vaporock"

    def __init__(self) -> None:
        self._backend: Any | None = None
        self._initialization_error = ""

    def _initialize(self) -> Any | None:
        if self._backend is not None or self._initialization_error:
            return self._backend
        try:
            from simulator.melt_backend.vaporock import VapoRockBackend

            backend = VapoRockBackend()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                available = bool(backend.initialize({}))
            if not available:
                self._initialization_error = "VapoRock backend unavailable"
                return None
            self._backend = backend
        except Exception as exc:
            self._initialization_error = _reason_line(exc)
        return self._backend

    def evaluate(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_K: float,
        fO2_bar: float | None,
    ) -> EngineResult:
        backend = self._initialize()
        if backend is None:
            return EngineResult(
                status="unavailable",
                reason=f"VapoRock unavailable: {self._initialization_error}",
                details={
                    "dependency_available": False,
                    "observable_family": "partial_pressure",
                },
            )
        if fO2_bar is None:
            return EngineResult(
                status="ok",
                reason=(
                    "VapoRock emits log10 partial pressures, not per-oxide "
                    "melt activities"
                ),
                details={
                    "dependency_available": True,
                    "observable_family": "partial_pressure",
                    "observable_supported": False,
                    "finite_prediction": False,
                    "execution_status": "completed_without_observable",
                },
            )
        if float(fO2_bar) <= 0.0 or not math.isfinite(float(fO2_bar)):
            return EngineResult(
                status="refused",
                reason="VapoRock vapour-pressure solve requires a positive finite fO2 pin",
                details={
                    "dependency_available": True,
                    "observable_family": "partial_pressure",
                    "observable_supported": True,
                },
            )
        result = backend.equilibrate(
            temperature_C=float(temperature_K) - 273.15,
            composition_kg={
                str(oxide): float(wt) for oxide, wt in composition_wt_pct.items()
            },
            fO2_log=math.log10(float(fO2_bar)),
            pressure_bar=1.0e-6,
        )
        raw_status = str(result.status)
        raw_diagnostics = dict(getattr(result, "diagnostics", None) or {})
        cause = raw_diagnostics.get("empty_speciation_cause")
        pressures = _positive_finite(
            getattr(result, "vaporock_full_speciation_Pa", None)
            or result.vapor_pressures_Pa
            or {}
        )
        details = {
            "dependency_available": True,
            "observable_family": "partial_pressure",
            "observable_supported": True,
            "finite_prediction": bool(pressures),
            "backend_status": raw_status,
            "units": "Pa",
            "source": "vaporock_eval_gas_abundances",
        }
        if cause:
            details["empty_speciation_cause"] = cause
        if raw_status == "unavailable":
            return EngineResult(
                status="unavailable",
                reason=_reason_line("; ".join(result.warnings)),
                details=details,
            )
        if raw_status == "out_of_domain":
            return EngineResult(
                status="out_of_domain",
                reason=_reason_line("; ".join(result.warnings)),
                details=details,
            )
        # Hollow token (or success-shaped empty dict) is missing-output,
        # not a provider refusal. Check before remapping not_converged.
        if cause or (
            not pressures and raw_status in {"ok", "non_authoritative"}
        ):
            from simulator.melt_backend.vaporock import (
                EmptySpeciationCause,
                empty_speciation_reason,
            )

            reason = _reason_line("; ".join(result.warnings))
            if cause:
                derived = empty_speciation_reason(EmptySpeciationCause(cause))
                if derived not in reason:
                    reason = _reason_line(
                        f"{reason}; {derived}" if reason else derived
                    )
            return EngineResult(
                status="observable_unavailable",
                reason=reason,
                details={
                    **details,
                    "execution_status": "completed_without_observable",
                    "finite_prediction": False,
                },
            )
        if raw_status not in {"ok", "non_authoritative"}:
            return EngineResult(
                status="refused",
                reason=_reason_line(
                    f"provider status {raw_status}: {'; '.join(result.warnings)}"
                ),
                details=details,
            )
        return EngineResult(
            status="ok",
            partial_pressures=pressures,
            details={
                **details,
                "execution_status": "converged",
                "species": sorted(pressures),
            },
        )

    def coverage(
        self,
        composition_wt_pct: Mapping[str, float],
        temperature_K: float,
    ) -> EngineResult:
        return self.evaluate(composition_wt_pct, temperature_K, 1.0e-9)


def load_bench_set(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != "melt-activity-bench.v1":
        raise ValueError(f"unsupported melt activity bench set: {path}")
    if not data.get("compositions") or not data.get("points"):
        raise ValueError(f"bench set lacks compositions or points: {path}")
    return data


def build_engines(
    names: Sequence[str], fixture: Mapping[str, Any], *, alphamelts_timeout_s: float
) -> list[MeltActivityEngine]:
    packs = dict(fixture["packs"])
    constructors = {
        "imcc-published": lambda: ImccEngine(
            "imcc-published", _repo_path(packs["imcc-published"]), published=True
        ),
        "imcc-ext": lambda: ImccEngine(
            "imcc-ext", _repo_path(packs["imcc-ext"]), published=False
        ),
        "internal_analytic": InternalAnalyticalEngine,
        "alphamelts": lambda: AlphaMeltsEngine(
            timeout_s=alphamelts_timeout_s,
            silicate_network_band=DEFAULT_MELTS_SILICATE_NETWORK_BAND_WT_PCT,
        ),
        "thermoengine": lambda: ThermoEngineMeltActivityEngine(
            timeout_s=alphamelts_timeout_s
        ),
        "vaporock": VapoRockEngine,
    }
    unknown = sorted(set(names) - set(constructors))
    if unknown:
        raise ValueError(f"unknown engines: {', '.join(unknown)}")
    return [constructors[name]() for name in names]


def _native_partial_pressure_pa(
    result: EngineResult, species: str
) -> float | None:
    pressures = result.partial_pressures or {}
    if species in pressures:
        return float(pressures[species])
    namespaced = f"{species}_gas"
    if namespaced in pressures:
        return float(pressures[namespaced])
    return None


def _hertz_knudsen_flux_mol_m2_s(
    pressure_pa: float, species: str, temperature_K: float
) -> float:
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.physical_constants import GAS_CONSTANT

    molar_mass = resolve_species_formula(species).molar_mass_kg_per_mol()
    # Premise: Hertz–Knudsen molar flux into vacuum is
    # J = P / sqrt(2 π M R T). Algebra: P has units kg m^-1 s^-2;
    # sqrt(M R T) = sqrt((kg/mol)·(J mol^-1 K^-1)·K) = kg m s^-1 mol^-1;
    # so J has units mol m^-2 s^-1. Sanity: flux rises with P and falls
    # with sqrt(T); this is the same form as the shared analytical path.
    return pressure_pa / math.sqrt(
        2.0 * math.pi * molar_mass * GAS_CONSTANT * float(temperature_K)
    )


def _prediction_for_point(
    point: Mapping[str, Any], result: EngineResult
) -> tuple[float | None, str]:
    if result.status != "ok":
        return None, result.reason
    observable = str(point["observable"])
    parent = str(point["parent_oxide"])
    if observable == "activity":
        value = result.activities.get(parent)
    elif observable == "activity_coefficient":
        value = result.gammas.get(parent)
    elif observable in {"partial_pressure", "evaporation_flux"}:
        native_pa = _native_partial_pressure_pa(result, str(point["species"]))
        if native_pa is not None:
            value = (
                native_pa
                if observable == "partial_pressure"
                else _hertz_knudsen_flux_mol_m2_s(
                    native_pa, str(point["species"]), float(point["temperature_K"])
                )
            )
            if value is None or not math.isfinite(float(value)) or float(value) <= 0.0:
                return None, (
                    f"engine returned no positive {observable} for {parent}"
                )
            return float(value), ""
        if "fO2_bar" not in point:
            return None, "gas comparison refused: observation has no independent fO2 pin"
        try:
            from simulator.diagnostic_helpers.alphamelts_volatility import (
                _analytical_vapor_pressures_from_activities,
                _load_default_vapor_pressure_data,
            )

            gas = _analytical_vapor_pressures_from_activities(
                vapor_pressure_data=_load_default_vapor_pressure_data(),
                temperature_C=float(point["temperature_K"]) - 273.15,
                pO2_bar=float(point["fO2_bar"]),
                melt_oxide_activities=_single_cation_gas_activities(
                    result.activities
                ),
                composition_wt_pct=point["composition_wt_pct"],
            )["species"].get(str(point["species"]), {})
            pressure_pa = float(gas["P_eq_Pa"])
            if observable == "partial_pressure":
                value = pressure_pa
            else:
                value = _hertz_knudsen_flux_mol_m2_s(
                    pressure_pa,
                    str(point["species"]),
                    float(point["temperature_K"]),
                )
        except Exception as exc:
            return None, f"shared gas layer refused: {_reason_line(exc)}"
    else:
        return None, f"unsupported observable {observable!r}"
    if value is None or not math.isfinite(float(value)) or float(value) <= 0.0:
        unmapped_labels = tuple(result.details.get("unmapped_activity_labels", ()))
        if observable in {"activity", "activity_coefficient"} and unmapped_labels:
            return None, (
                f"engine completed equilibrium but computed activity under "
                f"unmapped endmember/component label(s) "
                f"{', '.join(str(label) for label in unmapped_labels)}; "
                f"no authoritative {parent} basis conversion"
            )
        if result.details.get("observable_supported") is False and result.reason:
            return None, result.reason
        return None, f"engine returned no positive {observable} for {parent}"
    return float(value), ""


def run_points(
    fixture: Mapping[str, Any], engines: Sequence[MeltActivityEngine]
) -> list[dict[str, Any]]:
    compositions = dict(fixture["compositions"])
    cache: dict[tuple[str, str, float, float | None], EngineResult] = {}
    rows: list[dict[str, Any]] = []
    for point in fixture["points"]:
        composition_id = str(point["composition_id"])
        composition = _normalize_wt(compositions[composition_id]["composition_wt_pct"])
        temperature_K = float(point["temperature_K"])
        activity_observable = str(point["observable"]) in {
            "activity",
            "activity_coefficient",
        }
        fO2_bar = (
            None
            if activity_observable or point.get("fO2_bar") is None
            else float(point["fO2_bar"])
        )
        enriched = {**point, "composition_wt_pct": composition}
        for engine in engines:
            key = (engine.name, composition_id, temperature_K, fO2_bar)
            if key not in cache:
                cache[key] = execute_engine(engine, composition, temperature_K, fO2_bar)
            result = cache[key]
            if (
                not bool(point.get("score", True))
                and point.get("dropped_reason")
                and result.status == "ok"
            ):
                result = EngineResult(
                    status="refused",
                    reason=str(point["dropped_reason"]),
                    details={**result.details, "evaluation_status": "ok"},
                )
            prediction, prediction_reason = _prediction_for_point(enriched, result)
            point_status = (
                "observable_unavailable"
                if result.status == "ok" and prediction is None
                else result.status
            )
            measured = float(point["measured"])
            residual = (
                math.log10(prediction / measured)
                if prediction is not None and measured > 0.0 and bool(point.get("score", True))
                else None
            )
            rows.append(
                {
                    "point_id": point["id"],
                    "population": point["population"],
                    "material_class": point["material_class"],
                    "composition_id": composition_id,
                    "temperature_K": temperature_K,
                    "species": point["species"],
                    "parent_oxide": point["parent_oxide"],
                    "observable": point["observable"],
                    "measured": measured,
                    "units": point["units"],
                    "engine": engine.name,
                    "status": point_status,
                    "prediction": prediction,
                    "residual_dex": residual,
                    "score": bool(point.get("score", True)),
                    "reason": prediction_reason or result.reason,
                    "details": json.dumps(result.details, sort_keys=True, default=str),
                }
            )
    return rows


def _imcc_engines_with_extrapolation(
    engines: Sequence[MeltActivityEngine],
) -> list[ImccEngine]:
    flagged: list[ImccEngine] = []
    for engine in engines:
        if not isinstance(engine, ImccEngine):
            continue
        flagged.append(
            ImccEngine(
                engine.name,
                engine.pack_path,
                published=engine.published,
                allow_extrapolation=True,
                allow_out_of_envelope=True,
            )
        )
    return flagged


def as_imcc_informational_row(
    row: Mapping[str, Any],
    *,
    extrapolated: bool,
    envelope_status: str,
) -> dict[str, Any]:
    """Promote a computed IMCC row to the non-certifying extrapolated tier.

    ``residual_dex`` is forcibly ``None`` so ``summarize_metrics`` and
    ``summarize_paired_decisions`` cannot ingest it. The numeric residual
    lives only in ``informational_residual_dex``. Both marks are required
    columns: ``extrapolated`` is the temperature-domain mark, and
    ``envelope_status`` is the composition test.
    """

    measured = row.get("measured")
    prediction = row.get("prediction")
    informational = row.get("residual_dex")
    if informational is None and prediction is not None and measured is not None:
        measured_f = float(measured)
        pred_f = float(prediction)
        if pred_f > 0.0 and measured_f > 0.0:
            informational = math.log10(pred_f / measured_f)
    out = dict(row)
    out["residual_dex"] = None
    out["informational_residual_dex"] = informational
    out["extrapolated"] = bool(extrapolated)
    out["envelope_status"] = str(envelope_status)
    return out


def run_imcc_extrapolated_points(
    fixture: Mapping[str, Any],
    engines: Sequence[MeltActivityEngine],
) -> list[dict[str, Any]]:
    """Second IMCC pass: compute-and-mark. Never writes a scored residual."""

    flagged = _imcc_engines_with_extrapolation(engines)
    if not flagged:
        return []
    rows: list[dict[str, Any]] = []
    for row in run_points(fixture, flagged):
        details = json.loads(str(row.get("details") or "{}"))
        if "extrapolated" not in details:
            continue
        extrapolated = bool(details["extrapolated"])
        envelope_status = details.get("envelope_status")
        if envelope_status is None:
            continue
        if not extrapolated and envelope_status != "outside_validated":
            continue
        rows.append(
            as_imcc_informational_row(
                row,
                extrapolated=extrapolated,
                envelope_status=str(envelope_status),
            )
        )
    return rows


def summarize_informational_imcc_extrapolation(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Informational RMSE by engine × envelope. Never reads residual_dex."""

    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        key = (str(row["engine"]), str(row.get("envelope_status") or ""))
        counts[key] += 1
        residual = row.get("informational_residual_dex")
        if residual is None or not bool(row.get("score", True)):
            continue
        groups[key].append(float(residual))
    summary: list[dict[str, Any]] = []
    for key in sorted(set(counts) | set(groups)):
        engine, envelope_status = key
        residuals = groups.get(key, [])
        summary.append(
            {
                "engine": engine,
                "envelope_status": envelope_status,
                "row_count": counts[key],
                "scored_informational_count": len(residuals),
                "informational_rmse_dex": _rmse(residuals),
            }
        )
    return summary


def _render_imcc_extrapolated_tier(
    rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    if not rows:
        return []
    summary = summarize_informational_imcc_extrapolation(rows)
    lines = [
        "",
        "## IMCC extrapolated tier (computed-and-marked, not validated)",
        "",
        "These rows are a second IMCC pass with `allow_extrapolation` and "
        "`allow_out_of_envelope` enabled. They are category-2 out-of-domain "
        "physics: compute and mark. They are **not** a validated domain "
        "widening and do **not** certify. Residuals live only in "
        "`informational_residual_dex` and do not enter the scored RMSE table "
        "or the decision column.",
        "",
        "Marks are orthogonal and both appear on every row: `extrapolated` is "
        "the temperature-domain mark (`ImccResult.extrapolated`); "
        "`envelope_status` is the X_Me2O ≤ 0.5 composition test.",
        "",
        "### Informational RMSE by composition envelope",
        "",
        "| Engine | Envelope | n (tier) | n (scored informational) | Informational RMSE (dex) |",
        "|---|---|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            "| {engine} | {envelope_status} | {row_count} | {scored_informational_count} | {rmse} |".format(
                **row,
                rmse=_fmt(row["informational_rmse_dex"]),
            )
        )
    lines.extend(
        [
            "",
            "### Per-row computed-and-marked results",
            "",
            "| point_id | engine | T_K | species | observable | extrapolated | envelope_status | informational residual (dex) | prediction | measured |",
            "|---|---|---:|---|---|---|---|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            "| {point_id} | {engine} | {temperature} | {species} | {observable} | {extrapolated} | {envelope_status} | {residual} | {prediction} | {measured} |".format(
                point_id=row["point_id"],
                engine=row["engine"],
                temperature=_fmt(row.get("temperature_K")),
                species=row["species"],
                observable=row["observable"],
                extrapolated=row["extrapolated"],
                envelope_status=row["envelope_status"],
                residual=_fmt(row.get("informational_residual_dex")),
                prediction=_fmt(row.get("prediction")),
                measured=_fmt(row.get("measured")),
            )
        )
    return lines


def run_composition_probes(
    fixture: Mapping[str, Any], engines: Sequence[MeltActivityEngine]
) -> list[dict[str, Any]]:
    compositions = dict(fixture["compositions"])
    rows: list[dict[str, Any]] = []
    for probe in fixture.get("composition_probes", []):
        composition_id = str(probe["composition_id"])
        composition_meta = compositions[composition_id]
        composition = _normalize_wt(composition_meta["composition_wt_pct"])
        for engine in engines:
            result = execute_engine(
                engine,
                composition,
                float(probe["temperature_K"]),
                float(probe.get("fO2_bar", 1.0e-9)),
            )
            rows.append(
                {
                    "probe_id": probe["id"],
                    "composition_id": composition_id,
                    "material_class": composition_meta["material_class"],
                    "temperature_K": float(probe["temperature_K"]),
                    "SiO2_wt_pct": composition.get("SiO2", 0.0),
                    "engine": engine.name,
                    "status": result.status,
                    "reason": result.reason,
                    "details": json.dumps(result.details, sort_keys=True, default=str),
                }
            )
    return rows


def stripping_trajectory(
    composition_wt_pct: Mapping[str, Any], steps: int
) -> Iterable[tuple[str, int, float, dict[str, float]]]:
    base = _normalize_wt(composition_wt_pct)
    if steps < 2:
        raise ValueError("coverage steps must be >= 2")
    for trajectory in ("remove_silica", "strip_modifiers"):
        for step in range(steps):
            fraction = 0.95 * step / (steps - 1)
            changed = dict(base)
            if trajectory == "remove_silica":
                changed["SiO2"] = changed.get("SiO2", 0.0) * (1.0 - fraction)
            else:
                changed = {
                    oxide: value if oxide == "SiO2" else value * (1.0 - fraction)
                    for oxide, value in changed.items()
                }
            yield trajectory, step, fraction, _normalize_wt(changed)


def run_coverage_map(
    fixture: Mapping[str, Any], engines: Sequence[MeltActivityEngine], steps: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for composition_id, meta in fixture["compositions"].items():
        if meta["material_class"] != "literal_basalt":
            continue
        for trajectory, step, fraction, composition in stripping_trajectory(
            meta["composition_wt_pct"], steps
        ):
            row: dict[str, Any] = {
                "composition_id": composition_id,
                "trajectory": trajectory,
                "step": step,
                "stripped_fraction": fraction,
                "SiO2_wt_pct": composition.get("SiO2", 0.0),
                "major_oxide_sum_wt_pct": sum(composition.values()),
                "composition_json": json.dumps(composition, sort_keys=True),
            }
            for engine in engines:
                try:
                    result = engine.coverage(composition, 1900.0)
                except Exception as exc:
                    status, reason = classify_engine_exception(exc)
                    result = EngineResult(status=status, reason=reason)
                n_pressures = len(result.partial_pressures or {})
                if n_pressures > 0:
                    finite = True
                elif "finite_prediction" in result.details:
                    finite = bool(result.details["finite_prediction"])
                else:
                    finite = None
                status = result.status
                reason = result.reason
                is_pressure = coverage_observable_is_pressure(result)
                # Generic hollow-ok close: a success with no finite
                # values of a pressure observable (declared or
                # undeclared) is not ok. Activity / domain_gate
                # engines declare a non-pressure family and skip this.
                if status == "ok" and is_pressure and (
                    finite is False or n_pressures == 0
                ):
                    status = "observable_unavailable"
                    finite = False
                    if not reason:
                        reason = "engine produced no finite prediction"
                row[f"{engine.name}_status"] = status
                row[f"{engine.name}_reason"] = reason
                if is_pressure:
                    row[f"{engine.name}_finite_prediction"] = finite
                    row[f"{engine.name}_n_pressures"] = n_pressures
            rows.append(row)
    return rows


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def run_reference_anchors(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Rejoin frozen IMCC and VapoRock rows and retain empirical KEMS evidence."""

    config = dict(fixture["reference_anchors"])
    imcc_path = _repo_path(config["imcc_magma"]["path"])
    vaporock_path = _repo_path(config["vaporock_magma_kems"]["path"])
    for name, path in (
        ("imcc_magma", imcc_path),
        ("vaporock_magma_kems", vaporock_path),
    ):
        expected_hash = str(config[name]["tracked_sha256"])
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            raise ValueError(
                f"{name} tracked snapshot hash mismatch: "
                f"expected {expected_hash}, got {actual_hash}"
            )
    imcc_rows = _read_csv(imcc_path)
    vaporock_rows = _read_csv(vaporock_path)
    vaporock_model: dict[tuple[str, str, float], Mapping[str, str]] = {}
    for row in vaporock_rows:
        if row["anchor_class"] != "model_model_MAGMA":
            continue
        sheet = str(row["composition_ref"]).removeprefix("SF04-")
        key = (sheet, str(row["species"]), float(row["T_K"]))
        if key in vaporock_model:
            raise ValueError(f"duplicate VapoRock MAGMA anchor key: {key}")
        vaporock_model[key] = row

    rows: list[dict[str, Any]] = []
    seen_imcc: set[tuple[str, str, float]] = set()
    for imcc in imcc_rows:
        key = (str(imcc["sheet"]), str(imcc["species"]), float(imcc["T_K"]))
        if key in seen_imcc:
            raise ValueError(f"duplicate IMCC MAGMA anchor key: {key}")
        seen_imcc.add(key)
        vaporock = vaporock_model.get(key)
        if vaporock is None:
            continue
        imcc_reference = float(imcc["log10P_workbook"])
        vaporock_reference = float(vaporock["log10P_anchor"])
        imcc_prediction = float(imcc["log10P_shadow"])
        vaporock_prediction = float(vaporock["log10P_model"])
        rows.append(
            {
                "evidence_class": "model_model_MAGMA",
                "sheet": key[0],
                "composition_ref": vaporock["composition_ref"],
                "temperature_K": key[2],
                "species": key[1],
                "imcc_reference_log10P": imcc_reference,
                "vaporock_reference_log10P": vaporock_reference,
                "reference_difference_dex": imcc_reference - vaporock_reference,
                "imcc_prediction_log10P": imcc_prediction,
                "vaporock_prediction_log10P": vaporock_prediction,
                "imcc_residual_dex": imcc_prediction - imcc_reference,
                "vaporock_residual_dex": vaporock_prediction - vaporock_reference,
                "reference_status": vaporock["status"],
                "source": vaporock["source"],
                "note": vaporock["note"],
            }
        )

    expected = int(config["expected_shared_cells"])
    if len(rows) != expected:
        raise ValueError(
            f"reference anchor join produced {len(rows)} cells; expected {expected}"
        )

    for vaporock in vaporock_rows:
        if vaporock["anchor_class"] != "experimental_KEMS":
            continue
        measured = _optional_float(vaporock["log10P_anchor"])
        predicted = _optional_float(vaporock["log10P_model"])
        residual = _optional_float(vaporock["residual"])
        rows.append(
            {
                "evidence_class": "experimental_KEMS",
                "sheet": "",
                "composition_ref": vaporock["composition_ref"],
                "temperature_K": float(vaporock["T_K"]),
                "species": vaporock["species"],
                "imcc_reference_log10P": None,
                "vaporock_reference_log10P": measured,
                "reference_difference_dex": None,
                "imcc_prediction_log10P": None,
                "vaporock_prediction_log10P": predicted,
                "imcc_residual_dex": None,
                "vaporock_residual_dex": residual,
                "reference_status": vaporock["status"],
                "source": vaporock["source"],
                "note": vaporock["note"],
            }
        )
    return rows


def _rmse(values: Iterable[float]) -> float | None:
    materialized = list(values)
    if not materialized:
        return None
    return math.sqrt(sum(value * value for value in materialized) / len(materialized))


def summarize_reference_anchors(
    fixture: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    model_rows = [
        row for row in rows if row["evidence_class"] == "model_model_MAGMA"
    ]
    empirical_rows = [
        row for row in rows if row["evidence_class"] == "experimental_KEMS"
    ]
    per_species: list[dict[str, Any]] = []
    for species in SF04_ANCHOR_SPECIES:
        model_group = [row for row in model_rows if row["species"] == species]
        empirical_group = [
            row
            for row in empirical_rows
            if row["species"] == species
            and row.get("vaporock_residual_dex") is not None
        ]
        per_species.append(
            {
                "species": species,
                "shared_magma_count": len(model_group),
                "imcc_magma_rmse_dex": _rmse(
                    float(row["imcc_residual_dex"]) for row in model_group
                ),
                "vaporock_magma_rmse_dex": _rmse(
                    float(row["vaporock_residual_dex"]) for row in model_group
                ),
                "empirical_kems_count": len(empirical_group),
                "vaporock_kems_rmse_dex": _rmse(
                    float(row["vaporock_residual_dex"]) for row in empirical_group
                ),
            }
        )
    controller_pool = [
        row for row in model_rows if row["species"] not in {"K", "Na"}
    ]
    expected = fixture["reference_anchors"][
        "expected_non_alkali_pooled_rmse_dex"
    ]
    imcc_pool = _rmse(float(row["imcc_residual_dex"]) for row in controller_pool)
    vaporock_pool = _rmse(
        float(row["vaporock_residual_dex"]) for row in controller_pool
    )
    assert imcc_pool is not None and vaporock_pool is not None
    return {
        "shared_magma_count": len(model_rows),
        "max_reference_difference_dex": max(
            abs(float(row["reference_difference_dex"])) for row in model_rows
        ),
        "per_species": per_species,
        "empirical_kems_total": len(empirical_rows),
        "empirical_kems_scored": sum(
            row.get("vaporock_residual_dex") is not None for row in empirical_rows
        ),
        "controller_pool_count": len(controller_pool),
        "controller_pool_imcc_rmse_dex": imcc_pool,
        "controller_pool_vaporock_rmse_dex": vaporock_pool,
        "controller_anchor_reproduced": (
            abs(imcc_pool - float(expected["imcc"])) < 5.0e-4
            and abs(vaporock_pool - float(expected["vaporock"])) < 5.0e-4
        ),
    }


def run_live_vaporock_anchor_check(
    fixture: Mapping[str, Any],
    reference_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Compare the installed VapoRock build with the frozen tracked snapshot."""

    os.environ.setdefault("MPLCONFIGDIR", "/tmp/regolith-mpl-cache")
    try:
        from vaporock import System
    except Exception as exc:
        return [
            {
                "sheet": "",
                "temperature_K": None,
                "species": "",
                "status": "unavailable",
                "reason": _reason_line(exc),
            }
        ]
    model_rows = [
        row
        for row in reference_rows
        if row["evidence_class"] == "model_model_MAGMA"
    ]
    by_state: dict[tuple[str, float], list[Mapping[str, Any]]] = defaultdict(list)
    for row in model_rows:
        by_state[(str(row["sheet"]), float(row["temperature_K"]))].append(row)
    output: list[dict[str, Any]] = []
    system = System(vapor_database="JANAF")
    compositions = fixture["compositions"]
    for sheet in SF04_SHEET_COMPOSITIONS:
        states = sorted(
            (key, group) for key, group in by_state.items() if key[0] == sheet
        )
        temperatures = np.asarray([key[1] for key, _ in states], dtype=float)
        fO2 = np.asarray(
            [
                float(next(row for row in group if row["species"] == "O2")[
                    "vaporock_prediction_log10P"
                ])
                for _, group in states
            ],
            dtype=float,
        )
        composition = compositions[SF04_SHEET_COMPOSITIONS[sheet]][
            "vaporock_anchor_composition_wt_pct"
        ]
        try:
            system.set_melt_comp(composition)
            live = system.eval_gas_abundances(temperatures, fO2)
        except Exception as exc:
            for (state_sheet, temperature), group in states:
                for row in group:
                    output.append(
                        {
                            "sheet": state_sheet,
                            "temperature_K": temperature,
                            "species": row["species"],
                            "status": "refused",
                            "reason": _reason_line(exc),
                        }
                    )
            continue
        for (_, temperature), group in states:
            columns = np.asarray(live.columns, dtype=float)
            column = live.columns[int(np.argmin(np.abs(columns - temperature)))]
            for row in group:
                species = str(row["species"])
                key = f"{species}(g)"
                value = (
                    _optional_float(live.loc[key, column])
                    if key in live.index
                    else None
                )
                frozen = float(row["vaporock_prediction_log10P"])
                output.append(
                    {
                        "sheet": sheet,
                        "temperature_K": temperature,
                        "species": species,
                        "status": "ok" if value is not None else "refused",
                        "frozen_vaporock_log10P": frozen,
                        "live_vaporock_log10P": value,
                        "live_minus_frozen_dex": (
                            value - frozen if value is not None else None
                        ),
                        "reason": "" if value is not None else "species missing",
                    }
                )
    return output


def summarize_metrics(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["species"]), str(row["observable"]), str(row["engine"]))].append(row)
    summary: list[dict[str, Any]] = []
    for key in sorted(groups):
        species, observable, engine = key
        group = groups[key]
        residuals = [
            float(row["residual_dex"])
            for row in group
            if row.get("residual_dex") is not None
        ]
        counts = Counter(str(row["status"]) for row in group)
        summary.append(
            {
                "species": species,
                "observable": observable,
                "engine": engine,
                "scored_count": len(residuals),
                "rmse_dex": (
                    math.sqrt(sum(value * value for value in residuals) / len(residuals))
                    if residuals
                    else None
                ),
                "median_signed_residual_dex": (
                    float(np.median(residuals)) if residuals else None
                ),
                **{f"{status}_count": counts[status] for status in sorted(POINT_STATUSES)},
            }
        )
    return summary


def summarize_paired_decisions(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Compare IMCC and internal analytical errors on identical scored points."""

    internal = {
        str(row["point_id"]): row
        for row in rows
        if row["engine"] == "internal_analytic"
        and row.get("residual_dex") is not None
    }
    groups: dict[tuple[str, str, str], list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        engine = str(row["engine"])
        paired = internal.get(str(row["point_id"]))
        if (
            not engine.startswith("imcc-")
            or paired is None
            or row.get("residual_dex") is None
        ):
            continue
        groups[(str(row["species"]), str(row["observable"]), engine)].append(
            (float(row["residual_dex"]), float(paired["residual_dex"]))
        )
    decisions: list[dict[str, Any]] = []
    for (species, observable, engine), residual_pairs in sorted(groups.items()):
        imcc_rmse = _rmse(pair[0] for pair in residual_pairs)
        internal_rmse = _rmse(pair[1] for pair in residual_pairs)
        assert imcc_rmse is not None and internal_rmse is not None
        if math.isclose(imcc_rmse, internal_rmse, rel_tol=0.0, abs_tol=1.0e-12):
            decision = "tie"
        elif imcc_rmse < internal_rmse:
            decision = engine
        else:
            decision = "internal_analytic"
        decisions.append(
            {
                "species": species,
                "observable": observable,
                "imcc_engine": engine,
                "paired_count": len(residual_pairs),
                "imcc_rmse_dex": imcc_rmse,
                "internal_analytic_rmse_dex": internal_rmse,
                "imcc_closer_point_count": sum(
                    abs(pair[0]) < abs(pair[1]) for pair in residual_pairs
                ),
                "internal_analytic_closer_point_count": sum(
                    abs(pair[1]) < abs(pair[0]) for pair in residual_pairs
                ),
                "tie_point_count": sum(
                    math.isclose(
                        abs(pair[0]), abs(pair[1]), rel_tol=0.0, abs_tol=1.0e-12
                    )
                    for pair in residual_pairs
                ),
                "decision": decision,
            }
        )
    return decisions


def _paired_verdict(decisions: Sequence[Mapping[str, Any]]) -> str:
    if not decisions:
        return "No convention-valid measured point was produced by both engine families."
    clauses: list[str] = []
    for engine in sorted({str(row["imcc_engine"]) for row in decisions}):
        group = [row for row in decisions if row["imcc_engine"] == engine]
        imcc_wins = sum(row["decision"] == engine for row in group)
        internal_wins = sum(row["decision"] == "internal_analytic" for row in group)
        ties = sum(row["decision"] == "tie" for row in group)
        if imcc_wins and internal_wins:
            verdict = "mixed by species/observable"
        elif imcc_wins and ties:
            verdict = "IMCC better or tied on every comparable group"
        elif imcc_wins:
            verdict = "IMCC better on every comparable group"
        elif internal_wins and ties:
            verdict = "internal_analytic better or tied on every comparable group"
        elif internal_wins:
            verdict = "internal_analytic better on every comparable group"
        else:
            verdict = "tied on every comparable group"
        clauses.append(
            f"`{engine}`: {verdict} ({imcc_wins} IMCC, "
            f"{internal_wins} internal, {ties} tied)"
        )
    return "; ".join(clauses) + "."


def summarize_rump_coverage(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    low_silica = [row for row in rows if float(row["SiO2_wt_pct"]) < 30.0]
    summary: list[dict[str, Any]] = []
    for engine in sorted({
        key.removesuffix("_status")
        for row in rows
        for key in row
        if key.startswith("imcc-") and key.endswith("_status")
    }):
        both = sum(
            row.get("internal_analytic_status") == "ok"
            and row.get(f"{engine}_status") == "ok"
            for row in low_silica
        )
        internal_only = sum(
            row.get("internal_analytic_status") == "ok"
            and row.get(f"{engine}_status") != "ok"
            for row in low_silica
        )
        imcc_only = sum(
            row.get("internal_analytic_status") != "ok"
            and row.get(f"{engine}_status") == "ok"
            for row in low_silica
        )
        neither = len(low_silica) - both - internal_only - imcc_only
        summary.append(
            {
                "imcc_engine": engine,
                "below_30_count": len(low_silica),
                "both_accept_count": both,
                "internal_analytic_only_count": internal_only,
                "imcc_only_count": imcc_only,
                "neither_count": neither,
            }
        )
    return summary


def engines_with_ok_and_adapter_unavailable(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Engines that both answered ok and reported themselves unavailable.

    Structural run-level guard (t-683, pulled forward): an engine that
    produced at least one ``ok`` row cannot also report
    ``status=unavailable`` in the same run without contradicting itself.
    Reason text is not consulted, so editing unavailable prose cannot
    blind this predicate.
    """
    flags: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        engine = str(row.get("engine") or "")
        if not engine:
            continue
        status = str(row.get("status") or "")
        if status == "ok":
            flags[engine].add("ok")
        elif status == "unavailable":
            flags[engine].add("unavailable")
    return tuple(
        sorted(
            engine
            for engine, seen in flags.items()
            if seen >= {"ok", "unavailable"}
        )
    )


def _short_latch_reason(text: str) -> str:
    compact = " ".join(str(text or "").split())
    head = compact.split("Traceback")[0].strip(" :")
    if len(head) > 220:
        head = head[:217] + "..."
    return head or compact[:220]


def _git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _row_identity(row: Mapping[str, Any]) -> str:
    return str(row.get("point_id") or row.get("probe_id") or "")


def detect_thermoengine_adapter_latch(
    point_rows: Sequence[Mapping[str, Any]],
    *,
    transport_closed_mid_run: bool = False,
) -> dict[str, Any] | None:
    """Detect a one-process adapter death that poisons later ThermoEngine rows.

    Fires when either:

    1. The producer recorded a mid-run transport close
       (``transport_closed_mid_run``). That covers die-on-first-row,
       where no ``ok`` exists for a consumer-side contradiction to see.
    2. The row set is self-contradictory (at least one ``ok`` and at
       least one ``status=unavailable``).

    Reason text is never consulted. Sequential keep-handle is typed for
    ``EngineWorkerTimeout``, ``ThermoEngineNonFiniteField``, and
    ``ThermoEngineFO2UndefinedError`` only. Other close causes still
    latch the one-process adapter. Isolated retry remains required; any
    combined sequential+isolated count is an isolated-mode ceiling, not
    a sequential-mode result of the typed keep-handle.
    """

    te_rows = [row for row in point_rows if row.get("engine") == "thermoengine"]
    if not te_rows:
        return None
    contradiction = "thermoengine" in engines_with_ok_and_adapter_unavailable(
        te_rows
    )
    if not contradiction and not transport_closed_mid_run:
        return None
    latch_at: int | None = None
    for index, row in enumerate(te_rows):
        if row.get("status") == "unavailable":
            latch_at = index
            break
    if latch_at is None:
        if not transport_closed_mid_run:
            return None
        latch_at = 0
    previous = te_rows[latch_at - 1] if latch_at else None
    latched = te_rows[latch_at:]
    sequential_usable = sum(
        row.get("status") == "ok" and row.get("prediction") is not None for row in te_rows
    )
    yamaguchi_latched = [
        row
        for row in latched
        if "yamaguchi" in str(row.get("point_id") or "").lower()
    ]
    return {
        "detected": True,
        "latch_after_point_id": (
            _row_identity(previous) if previous is not None else None
        ),
        "latch_after_status": (
            str(previous.get("status") or "") if previous is not None else ""
        ),
        "latch_after_reason": (
            str(previous.get("reason") or "") if previous is not None else ""
        ),
        "latch_first_point_id": _row_identity(latched[0]),
        "sequential_usable": sequential_usable,
        "sequential_total": len(te_rows),
        "latched_count": len(latched),
        "latched_point_ids": [_row_identity(row) for row in latched],
        "yamaguchi_latched_count": len(yamaguchi_latched),
        "transport_closed_mid_run": bool(transport_closed_mid_run),
        "ok_unavailable_contradiction": contradiction,
    }


def _adapter_unavailable_result(result: EngineResult) -> bool:
    """True when this call reported the adapter absent. Status only."""
    return result.status == "unavailable"


def _engine_transport_close_count(engine: Any) -> int:
    """Producer mid-run close count. 0 when the engine has no marker."""
    getter = getattr(engine, "transport_close_count", None)
    if callable(getter):
        return int(getter())
    flag = getattr(engine, "transport_closed_mid_run", None)
    if callable(flag):
        return 1 if flag() else 0
    return 0


def _thermoengine_transport_closed_mid_run(
    engines: Sequence[Any],
) -> bool:
    """True when any ThermoEngine producer recorded a mid-run close."""
    for engine in engines:
        if getattr(engine, "name", None) != "thermoengine":
            continue
        if _engine_transport_close_count(engine) > 0:
            return True
        flag = getattr(engine, "transport_closed_mid_run", None)
        if callable(flag) and flag():
            return True
    return False


def _adapter_killed_this_call(engine: Any, close_count_before: int) -> bool:
    """True when this call tore down a live transport. Producer count only."""
    return _engine_transport_close_count(engine) > close_count_before


def _thermoengine_point_prediction_row(
    point: Mapping[str, Any],
    composition: Mapping[str, float],
    result: EngineResult,
) -> dict[str, Any]:
    enriched = {**point, "composition_wt_pct": composition}
    if (
        not bool(point.get("score", True))
        and point.get("dropped_reason")
        and result.status == "ok"
    ):
        result = EngineResult(
            status="refused",
            reason=str(point["dropped_reason"]),
            details={**result.details, "evaluation_status": "ok"},
        )
    prediction, prediction_reason = _prediction_for_point(enriched, result)
    point_status = (
        "observable_unavailable"
        if result.status == "ok" and prediction is None
        else result.status
    )
    measured = float(point["measured"])
    residual = (
        math.log10(prediction / measured)
        if prediction is not None and measured > 0.0 and bool(point.get("score", True))
        else None
    )
    return {
        "point_id": point["id"],
        "population": point["population"],
        "composition_id": str(point["composition_id"]),
        "temperature_K": float(point["temperature_K"]),
        "species": point["species"],
        "observable": point["observable"],
        "engine": "thermoengine",
        "status": point_status,
        "prediction": prediction,
        "residual_dex": residual,
        "reason": prediction_reason or result.reason,
    }


def measure_isolated_thermoengine_points(
    fixture: Mapping[str, Any],
    point_ids: Sequence[str],
    *,
    timeout_s: float = 30.0,
    engine_factory: Any | None = None,
) -> dict[str, Any]:
    """Re-evaluate ThermoEngine points with a fresh adapter after each death.

    This is a separate measurement from the sequential one-process CSV. It does
    not rewrite sequential rows. ``engine_factory`` is a test seam.
    """

    wanted = set(point_ids)
    points = [point for point in fixture["points"] if point["id"] in wanted]
    compositions = dict(fixture["compositions"])
    factory = engine_factory or (
        lambda: ThermoEngineMeltActivityEngine(timeout_s=timeout_s)
    )
    engine = factory()
    cache: dict[tuple[str, float, float | None], EngineResult] = {}
    rows: list[dict[str, Any]] = []
    restarts = 0
    for point in points:
        composition_id = str(point["composition_id"])
        composition = _normalize_wt(
            compositions[composition_id]["composition_wt_pct"]
        )
        temperature_K = float(point["temperature_K"])
        activity_observable = str(point["observable"]) in {
            "activity",
            "activity_coefficient",
        }
        fO2_bar = (
            None
            if activity_observable or point.get("fO2_bar") is None
            else float(point["fO2_bar"])
        )
        key = (composition_id, temperature_K, fO2_bar)
        if key not in cache:
            close_count_before = _engine_transport_close_count(engine)
            result = execute_engine(engine, composition, temperature_K, fO2_bar)
            if _adapter_unavailable_result(result):
                engine = factory()
                restarts += 1
                close_count_before = _engine_transport_close_count(engine)
                result = execute_engine(engine, composition, temperature_K, fO2_bar)
            if _adapter_killed_this_call(engine, close_count_before):
                engine = factory()
                restarts += 1
            cache[key] = result
        rows.append(
            _thermoengine_point_prediction_row(point, composition, cache[key])
        )
    usable = sum(
        row["status"] == "ok" and row["prediction"] is not None for row in rows
    )
    yamaguchi = [
        row for row in rows if "yamaguchi" in str(row["point_id"]).lower()
    ]
    yamaguchi_usable = sum(
        row["status"] == "ok" and row["prediction"] is not None for row in yamaguchi
    )
    return {
        "usable": usable,
        "total": len(rows),
        "yamaguchi_usable": yamaguchi_usable,
        "yamaguchi_total": len(yamaguchi),
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "restarts": restarts,
        "rows": rows,
    }


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def generate_report(
    fixture: Mapping[str, Any],
    point_rows: Sequence[Mapping[str, Any]],
    probe_rows: Sequence[Mapping[str, Any]],
    coverage_rows: Sequence[Mapping[str, Any]],
    reference_rows: Sequence[Mapping[str, Any]] = (),
    live_vaporock_rows: Sequence[Mapping[str, Any]] = (),
    thermoengine_latch: Mapping[str, Any] | None = None,
    thermoengine_probe_latch: Mapping[str, Any] | None = None,
    extrapolated_rows: Sequence[Mapping[str, Any]] = (),
) -> str:
    metrics = summarize_metrics(point_rows)
    paired_decisions = summarize_paired_decisions(point_rows)
    point_engines = {str(row["engine"]) for row in point_rows}
    probe_engines = {str(row["engine"]) for row in probe_rows}
    coverage_engines = {
        key.removesuffix("_status")
        for row in coverage_rows
        for key in row
        if key.endswith("_status")
    }
    present_engines = point_engines | probe_engines | coverage_engines
    lines = [
        "# Melt-activity benchmark report",
        "",
        "## Evidence boundary",
        "",
        f"Literal SF04 basalt empirical points: **{fixture['provenance']['literal_basalt_empirical_point_count']}**. "
        "The scored experimental population is six Hastie-1981 KEMS gas-pressure "
        "points, six Richter-2007 Type-B CAI-like CMAS gamma targets, 12 "
        "Tsaplin-2000 Na2O-SiO2 a(SiO2) targets, and 28 Yamaguchi-1983 "
        "Na2O-SiO2 liquid-reference a(SiO2) targets. "
        "SF04 workbook pressures are scored only as an explicitly non-empirical regression anchor.",
        "",
        "Residual convention: `log10(predicted/measured)`; positive means overprediction. "
        "No coefficient tuning was performed.",
        "",
        "## Per-species comparison",
        "",
        "| Species | Observable | Engine | n | RMSE (dex) | Median residual | ok | OOD | crash | refused | observable unavailable | unavailable |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    activity_metrics = []
    vaporock_pressure_metrics = []
    for row in metrics:
        if (
            row["engine"] == "vaporock"
            and row["observable"] not in VAPOROCK_PRESSURE_OBSERVABLES
        ):
            continue
        if row["engine"] == "vaporock":
            vaporock_pressure_metrics.append(row)
        activity_metrics.append(row)
    for row in activity_metrics:
        lines.append(
            "| {species} | {observable} | {engine} | {scored_count} | {rmse} | {median} | {ok} | {ood} | {crash} | {refused} | {observable_unavailable} | {unavailable} |".format(
                **row,
                rmse=_fmt(row["rmse_dex"]),
                median=_fmt(row["median_signed_residual_dex"]),
                ok=row["ok_count"],
                ood=row["out_of_domain_count"],
                crash=row["crash_count"],
                refused=row["refused_count"],
                observable_unavailable=row["observable_unavailable_count"],
                unavailable=row["unavailable_count"],
            )
        )
    lines.extend(
        [
            "",
            "## IMCC versus internal_analytic decision column",
            "",
            "Only identical, convention-valid scored measurements produced by both engines enter this paired comparison.",
            "",
            "| Species | Observable | IMCC engine | Paired n | IMCC RMSE (dex) | internal_analytic RMSE (dex) | IMCC closer points | internal closer points | ties | Decision |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in paired_decisions:
        lines.append(
            "| {species} | {observable} | {imcc_engine} | {paired_count} | {imcc} | {internal} | {imcc_closer_point_count} | {internal_analytic_closer_point_count} | {tie_point_count} | {decision} |".format(
                **row,
                imcc=_fmt(row["imcc_rmse_dex"]),
                internal=_fmt(row["internal_analytic_rmse_dex"]),
            )
        )
    lines.extend(["", f"Decision verdict: {_paired_verdict(paired_decisions)}"])
    lines.extend(_render_imcc_extrapolated_tier(extrapolated_rows))
    if reference_rows:
        anchor = summarize_reference_anchors(fixture, reference_rows)
        lines.extend(
            [
                "",
                "## Frozen SF04 MAGMA regression anchor",
                "",
                f"The tracked source snapshots rejoin **{anchor['shared_magma_count']}** identical "
                "SF04 cells on `(sheet, species, T_K)`. Their MAGMA references agree to "
                f"{anchor['max_reference_difference_dex']:.4f} dex at four decimals.",
                "",
                "MAGMA is a model-reproduction anchor, not correctness evidence. "
                "The empirical-KEMS column is the independent measured-pressure check "
                "available in the frozen VapoRock validation snapshot.",
                "",
                "| Species | Shared MAGMA n | IMCC RMSE vs MAGMA | VapoRock RMSE vs MAGMA | Empirical KEMS n | VapoRock RMSE vs KEMS |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in anchor["per_species"]:
            lines.append(
                "| {species} | {shared_magma_count} | {imcc} | {vaporock} | {empirical_kems_count} | {kems} |".format(
                    **row,
                    imcc=_fmt(row["imcc_magma_rmse_dex"], 3),
                    vaporock=_fmt(row["vaporock_magma_rmse_dex"], 3),
                    kems=_fmt(row["vaporock_kems_rmse_dex"], 3),
                )
            )
        lines.extend(
            [
                "",
                "Controller regression pool (all non-alkali rows, including the O2 fO2 pin): "
                f"IMCC **{anchor['controller_pool_imcc_rmse_dex']:.3f}** vs "
                f"VapoRock **{anchor['controller_pool_vaporock_rmse_dex']:.3f}** dex; "
                f"0.274/0.503 anchor reproduced: **{'yes' if anchor['controller_anchor_reproduced'] else 'no'}**.",
                "",
                f"Experimental KEMS snapshot: **{anchor['empirical_kems_scored']} scored / "
                f"{anchor['empirical_kems_total']} retained** rows. These are independent "
                "KEMS compositions, not measurements on the four SF04 basalt sheets, and "
                "therefore do not turn the MAGMA table into empirical basalt evidence.",
            ]
        )
        if live_vaporock_rows:
            live_deltas = [
                abs(float(row["live_minus_frozen_dex"]))
                for row in live_vaporock_rows
                if row.get("live_minus_frozen_dex") is not None
            ]
            live_ok = sum(row.get("status") == "ok" for row in live_vaporock_rows)
            max_live_delta = max(live_deltas) if live_deltas else None
            lines.extend(
                [
                    "",
                    "### Installed VapoRock snapshot check",
                    "",
                    f"Live comparison produced {live_ok}/{len(live_vaporock_rows)} cells; "
                    f"maximum live-minus-frozen magnitude: {_fmt(max_live_delta, 6)} dex.",
                    (
                        "The installed VapoRock run disagrees with the frozen anchors.csv snapshot; "
                        "the difference is recorded, not reconciled."
                        if max_live_delta is None or max_live_delta > 5.0e-4
                        else "The installed VapoRock run reproduces the frozen snapshot within 0.0005 dex."
                    ),
                ]
            )
    lines.extend(
        [
            "",
            "## In-domain composition probes",
            "",
            "These are engine robustness/coverage probes, not empirical score points.",
            "",
            "| Composition | Class | Engine | Status | Reason |",
            "|---|---|---|---|---|",
        ]
    )
    for row in probe_rows:
        lines.append(
            f"| {row['composition_id']} | {row['material_class']} | {row['engine']} | {row['status']} | {_reason_line(row['reason']) or '—'} |"
        )
    if thermoengine_probe_latch:
        lines.extend(
            [
                "",
                "ThermoEngine probe-row latch detected: the probe engine set "
                f"closed its transport mid-run "
                f"(after `{thermoengine_probe_latch.get('latch_after_point_id') or 'the first probe'}`, "
                f"{thermoengine_probe_latch.get('latched_count')} later probe rows "
                "inherited `unavailable`). Isolated retry is applied to "
                "benchmark point rows only.",
            ]
        )
    lines.extend(["", "## Cross-engine verdict", ""])
    if "alphamelts" not in present_engines:
        lines.append("AlphaMELTS was not selected; no IMCC-versus-AlphaMELTS verdict was computed.")
    else:
        literal_alpha = [
            row
            for row in probe_rows
            if row["material_class"] == "literal_basalt"
            and row["engine"] == "alphamelts"
        ]
        alpha_equilibrium_completed = bool(literal_alpha) and all(
            json.loads(str(row.get("details") or "{}"))
            .get("equilibrium_completed", False)
            for row in literal_alpha
        )
        lines.append(
            "AlphaMELTS equilibrium completed on all literal SF04 basalt probes, but its provider returned no canonical per-oxide activity surface; therefore the fair melt-activity comparison was refused."
            if alpha_equilibrium_completed
            else "AlphaMELTS did not complete a usable melt-activity evaluation on all literal SF04 basalts."
        )
        imcc_engines = {
            engine for engine in present_engines if engine.startswith("imcc-")
        }
        if not imcc_engines:
            lines.extend(
                ["", "No IMCC engine was selected; no cross-engine verdict was computed."]
            )
        else:
            alpha_scored_ids = {
                str(row["point_id"])
                for row in point_rows
                if row["engine"] == "alphamelts"
                and row.get("residual_dex") is not None
            }
            imcc_scored_ids = {
                str(row["point_id"])
                for row in point_rows
                if row["engine"] in imcc_engines
                and row.get("residual_dex") is not None
            }
            shared_scored_ids = alpha_scored_ids & imcc_scored_ids
            lines.extend(
                [
                    "",
                    (
                        f"IMCC and AlphaMELTS share {len(shared_scored_ids)} scored experimental points."
                        if shared_scored_ids
                        else "IMCC-versus-AlphaMELTS empirical verdict: **none**. No point has both a convention-valid measurement and successful canonical activities from both engine families."
                    ),
                ]
            )
    if "thermoengine" in present_engines:
        thermoengine_rows = [
            row for row in point_rows if row["engine"] == "thermoengine"
        ]
        thermoengine_usable = sum(
            row.get("status") == "ok" and row.get("prediction") is not None
            for row in thermoengine_rows
        )
        latch = thermoengine_latch or detect_thermoengine_adapter_latch(point_rows)
        lines.append("")
        if latch:
            after_id = latch.get("latch_after_point_id") or "an earlier point"
            after_reason = _short_latch_reason(
                str(latch.get("latch_after_reason") or "adapter death")
            )
            yam_latched = int(latch.get("yamaguchi_latched_count") or 0)
            isolated_total = latch.get("isolated_total")
            isolated_usable = latch.get("isolated_usable")
            isolated_measured = isolated_total is not None and isolated_usable is not None
            lines.append(
                f"ThermoEngine sequential one-process yield: "
                f"{thermoengine_usable}/{len(thermoengine_rows)} usable "
                "benchmark predictions. This figure is a post-latch artifact: "
                f"after `{after_id}` the in-process adapter died "
                f"({after_reason}), and the remaining "
                f"{latch['latched_count']} ThermoEngine rows"
                + (
                    f" — including all {yam_latched} Yamaguchi 1983 points —"
                    if yam_latched
                    else " —"
                )
                + " inherited `unavailable`. "
                "Do not read the sequential count as ThermoEngine being "
                "unable to score those later points."
            )
            if isolated_measured:
                yam_iso_u = latch.get("isolated_yamaguchi_usable")
                yam_iso_t = latch.get("isolated_yamaguchi_total")
                true_usable = latch.get("true_usable")
                true_total = latch.get("true_total") or len(thermoengine_rows)
                yam_clause = ""
                if yam_iso_u is not None and yam_iso_t is not None:
                    yam_clause = f" (Yamaguchi: {yam_iso_u}/{yam_iso_t})"
                lines.append(
                    "Isolated ThermoEngine re-evaluation of the latched points "
                    "(fresh adapter after each adapter-death; not taken from "
                    f"the latched CSV) produced {isolated_usable}/{isolated_total} "
                    f"usable predictions{yam_clause}. Combined coverage of the "
                    f"{true_total}-point set is therefore "
                    f"{true_usable}/{true_total}: sequential pre-latch usable "
                    "plus isolated latched-point usable. That combined figure "
                    "is an isolated-mode ceiling, not a sequential-mode result: "
                    "sequential keep-handle is typed for EngineWorkerTimeout, "
                    "ThermoEngineNonFiniteField, and "
                    "ThermoEngineFO2UndefinedError only; other close causes "
                    "still latch, so isolated retry remains required."
                )
            else:
                lines.append(
                    "Isolated ThermoEngine re-evaluation of the latched points "
                    "was not performed in this run; the un-latched coverage is "
                    "therefore not asserted here."
                )
            lines.append(
                "Converged results without the requested canonical observable "
                "remain typed `observable_unavailable`."
            )
        else:
            lines.append(
                f"ThermoEngine produced {thermoengine_usable}/{len(thermoengine_rows)} "
                "usable benchmark predictions; converged results without the "
                "requested canonical observable remain typed "
                "`observable_unavailable`."
            )
    lines.extend(["", "## Stripping-trajectory coverage", ""])
    for engine in sorted(coverage_engines):
        accepted = sum(
            coverage_cell_accepted(row, engine) for row in coverage_rows
        )
        refused = len(coverage_rows) - accepted
        low_silica = [
            row for row in coverage_rows if float(row["SiO2_wt_pct"]) < 30.0
        ]
        low_refused = sum(
            not coverage_cell_accepted(row, engine) for row in low_silica
        )
        lines.append(
            f"- `{engine}`: {accepted}/{len(coverage_rows)} accepted; {refused} refused/unavailable; "
            f"below 30 wt% SiO2, {len(low_silica) - low_refused}/{len(low_silica)} accepted "
            f"and {low_refused}/{len(low_silica)} refused/unavailable."
        )
    if "thermoengine" in coverage_engines:
        lines.append(
            "ThermoEngine coverage rows are AlphaMELTSDomainGate assessments "
            "and do not call the ThermoEngine transport. A mid-run transport "
            "close is therefore not visible in this table and is not "
            "isolated-retried here."
        )
    alpha_rows = [row for row in coverage_rows if "alphamelts_status" in row]
    if alpha_rows:
        lines.extend(["", "AlphaMELTS trajectory boundaries:", ""])
        boundary_groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in alpha_rows:
            boundary_groups[(str(row["composition_id"]), str(row["trajectory"]))].append(row)
        for (composition_id, trajectory), group in sorted(boundary_groups.items()):
            ordered = sorted(group, key=lambda row: int(row["step"]))
            first_refusal = next(
                (row for row in ordered if row["alphamelts_status"] != "ok"), None
            )
            if first_refusal is None:
                lines.append(f"- `{composition_id}` / `{trajectory}`: no refusal in sweep.")
            else:
                lines.append(
                    f"- `{composition_id}` / `{trajectory}`: first refusal at step {first_refusal['step']}, "
                    f"SiO2={float(first_refusal['SiO2_wt_pct']):.3f} wt%."
                )
    lines.extend(
        [
            "",
            "The CSV preserves each composition step, engine status, and typed reason.",
        ]
    )
    if alpha_rows:
        lines.append(
            "It answers the rump question as a curve: AlphaMELTS rejects every normalized step below its 30 wt% SiO2 floor."
        )
    rump_comparisons = summarize_rump_coverage(coverage_rows)
    if rump_comparisons:
        lines.extend(
            [
                "",
                "Paired below-30 wt% SiO2 coverage:",
                "",
                "| IMCC engine | Both accept | internal_analytic only | IMCC only | Neither | Total |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in rump_comparisons:
            lines.append(
                "| {imcc_engine} | {both_accept_count} | {internal_analytic_only_count} | {imcc_only_count} | {neither_count} | {below_30_count} |".format(
                    **row
                )
            )
    if "vaporock" in present_engines:
        vaporock_pp = [
            row
            for row in point_rows
            if row["engine"] == "vaporock"
            and row["observable"] == "partial_pressure"
        ]
        vaporock_scored = [
            row for row in vaporock_pp if row.get("residual_dex") is not None
        ]
        lines.extend(
            [
                "",
                "## VapoRock vapour-pressure leg",
                "",
                "VapoRock is scored on native log10 partial pressures converted to Pa, "
                "not on per-oxide melt activities. Activity and gamma points are omitted "
                "from the comparison table rather than reported as a dead activity engine.",
                "",
                f"Partial-pressure points: **{len(vaporock_scored)} scored / "
                f"{len(vaporock_pp)} planned**.",
            ]
        )
        if vaporock_pressure_metrics:
            lines.extend(
                [
                    "",
                    "| Species | n | RMSE (dex) | Median residual | ok | OOD | refused |",
                    "|---|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for row in vaporock_pressure_metrics:
                if row["observable"] != "partial_pressure":
                    continue
                lines.append(
                    "| {species} | {scored_count} | {rmse} | {median} | {ok} | {ood} | {refused} |".format(
                        **row,
                        rmse=_fmt(row["rmse_dex"]),
                        median=_fmt(row["median_signed_residual_dex"]),
                        ok=row["ok_count"],
                        ood=row["out_of_domain_count"],
                        refused=row["refused_count"],
                    )
                )
    lines.extend(
        [
            "",
            "## Honest limits",
            "",
            "- No direct experimental activity or partial-pressure points exist for the four literal SF04 basalt sheets in the tracked source inventory.",
            "- Richter-2007 is an in-domain Type-B CAI-like CMAS melt, not a literal basalt; its six gamma targets are reported separately.",
            "- Four OCR-digitized Richter Mg flux points are retained but refused for scoring because no independent experimental fO2 pin closes the gas/reference-state comparison.",
            "- KEMS-008 Table 10 values are kinetic vaporization coefficients, not basalt melt activities.",
            "- Melt-activity engines score gas observables through the fixture's pinned fO2 and the shared tracked analytical gas layer. Parent-formula activities are converted to the rail's single-cation component basis first. VapoRock is the exception: it is scored on its native offgas partial pressures, not on a derived activity surface.",
            "- Activity coefficients are reported as `gamma = a/x` on the parent-oxide formula-unit basis. The internal analytical adapter converts its native single-cation activity and mole-fraction provenance before comparison.",
        ]
    )
    if "vaporock" in present_engines:
        lines.append(
            "- VapoRock is a vapour-pressure / offgas engine: native partial "
            "pressures are scored on their own leg. Activity/gamma points stay "
            "unasked (`observable_unavailable`). Frozen MAGMA/KEMS and the "
            "live-versus-frozen drift check remain."
        )
    if "alphamelts" in present_engines:
        lines.append(
            "- Where AlphaMELTS provides no canonical oxide activity or crashes, that is recorded as a first-class result; it is never replaced by a fallback model."
        )
    lines.append("")
    return "\n".join(lines)


GENERATED_ARTIFACT_NAMES = frozenset(
    {
        "bench-set.yaml",
        "benchmark-results.csv",
        "composition-probes.csv",
        "coverage-map.csv",
        "live-vaporock-check.csv",
        "paired-decisions.csv",
        "reference-anchor-results.csv",
        "report.md",
        "run-metadata.json",
    }
)


def _planned_artifact_names(mode: str, live_vaporock_anchor_check: bool) -> set[str]:
    planned = {"bench-set.yaml", "run-metadata.json"}
    if mode in {"benchmark", "all"}:
        planned |= {
            "benchmark-results.csv",
            "composition-probes.csv",
            "paired-decisions.csv",
            "reference-anchor-results.csv",
        }
        if live_vaporock_anchor_check:
            planned.add("live-vaporock-check.csv")
    if mode in {"coverage", "all"}:
        planned.add("coverage-map.csv")
    if mode == "all":
        planned.add("report.md")
    return planned


def run_benchmark(
    *,
    bench_set_path: Path = DEFAULT_BENCH_SET,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    engine_names: Sequence[str] = DEFAULT_ENGINES,
    mode: str = "all",
    coverage_steps: int = 21,
    alphamelts_timeout_s: float = 30.0,
    live_vaporock_anchor_check: bool = True,
    retired_artifacts: Sequence[str] = (),
) -> dict[str, Any]:
    fixture = load_bench_set(bench_set_path)
    engines = build_engines(
        engine_names, fixture, alphamelts_timeout_s=alphamelts_timeout_s
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    planned_names = _planned_artifact_names(mode, live_vaporock_anchor_check)
    # The context-manager form runs BOTH halves: the pre-write shrink check
    # on entry and the wrote-what-you-planned check on exit. Calling the two
    # halves by hand is what this call site used to do, and the post-check is
    # the half that is easy to drop in a later edit -- dropping it restores
    # the exact b-200 hole the guard exists to close. `with` cannot be
    # half-used, so the sequencing is structural here rather than remembered.
    with regeneration_guard(
        output_dir,
        planned_names,
        managed=GENERATED_ARTIFACT_NAMES,
        retired=retired_artifacts,
    ) as guard:
        # The guard's entry check cannot see a planned-but-unwritten artifact --
        # `planned` passes it by construction. Every planned name is unlinked
        # below, and several writes downstream are CONDITIONAL (the live-vaporock
        # CSV writes only when its row list is non-empty), so a false condition
        # would delete the previous copy and report success. The guard's EXIT
        # check, on leaving this `with`, is what closes that.
        for name in planned_names | set(retired_artifacts):
            (output_dir / name).unlink(missing_ok=True)
        point_rows: list[dict[str, Any]] = []
        probe_rows: list[dict[str, Any]] = []
        coverage_rows: list[dict[str, Any]] = []
        reference_rows: list[dict[str, Any]] = []
        live_vaporock_rows: list[dict[str, Any]] = []
        extrapolated_rows: list[dict[str, Any]] = []
        paired_decisions: list[dict[str, Any]] = []
        probe_engines: list[Any] = []
        if mode in {"benchmark", "all"}:
            point_rows = run_points(fixture, engines)
            # Additive second IMCC pass. Strict point_rows stay untouched.
            extrapolated_rows = run_imcc_extrapolated_points(fixture, engines)
            # Fresh instances so a row-local point failure cannot poison probes.
            # Makes the probe table askable; not a claim those probes score.
            probe_engines = build_engines(
                engine_names, fixture, alphamelts_timeout_s=alphamelts_timeout_s
            )
            probe_rows = run_composition_probes(fixture, probe_engines)
            reference_rows = run_reference_anchors(fixture)
            if live_vaporock_anchor_check:
                live_vaporock_rows = run_live_vaporock_anchor_check(
                    fixture, reference_rows
                )
            _write_csv(output_dir / "benchmark-results.csv", point_rows)
            paired_decisions = summarize_paired_decisions(point_rows)
            _write_csv(output_dir / "paired-decisions.csv", paired_decisions)
            _write_csv(output_dir / "composition-probes.csv", probe_rows)
            _write_csv(output_dir / "reference-anchor-results.csv", reference_rows)
            if live_vaporock_rows:
                _write_csv(output_dir / "live-vaporock-check.csv", live_vaporock_rows)
        if mode in {"coverage", "all"}:
            coverage_rows = run_coverage_map(fixture, engines, coverage_steps)
            _write_csv(output_dir / "coverage-map.csv", coverage_rows)
        shutil.copyfile(bench_set_path, output_dir / "bench-set.yaml")
        latch = detect_thermoengine_adapter_latch(
            point_rows,
            transport_closed_mid_run=_thermoengine_transport_closed_mid_run(
                engines
            ),
        )
        probe_latch = detect_thermoengine_adapter_latch(
            probe_rows,
            transport_closed_mid_run=_thermoengine_transport_closed_mid_run(
                probe_engines
            ),
        )
        if latch:
            isolated = measure_isolated_thermoengine_points(
                fixture,
                latch["latched_point_ids"],
                timeout_s=alphamelts_timeout_s,
            )
            latch = {
                **latch,
                "isolated_usable": isolated["usable"],
                "isolated_total": isolated["total"],
                "isolated_yamaguchi_usable": isolated["yamaguchi_usable"],
                "isolated_yamaguchi_total": isolated["yamaguchi_total"],
                "isolated_status_counts": isolated["status_counts"],
                "isolated_restarts": isolated["restarts"],
                "true_usable": latch["sequential_usable"] + isolated["usable"],
                "true_total": latch["sequential_total"],
            }
            # Isolated row details stay out of the sequential CSV. Status
            # counts are enough for the published claim; dropping per-point
            # isolated rows keeps run-metadata.json a run record, not a
            # second results table.
            latch.pop("latched_point_ids", None)
        metadata = {
            "schema_version": "melt-activity-benchmark-run.v1",
            "bench_set": (
                str(bench_set_path.relative_to(REPO_ROOT))
                if bench_set_path.is_relative_to(REPO_ROOT)
                else str(bench_set_path)
            ),
            "bench_set_sha256": _sha256(bench_set_path),
            "produced_at_git_head": _git_head(),
            "engines": list(engine_names),
            "mode": mode,
            "point_status_counts": dict(sorted(Counter(row["status"] for row in point_rows).items())),
            "probe_status_counts": dict(sorted(Counter(row["status"] for row in probe_rows).items())),
            "coverage_row_count": len(coverage_rows),
            "rump_coverage": summarize_rump_coverage(coverage_rows),
            "paired_decisions": paired_decisions,
            "reference_anchor": (
                summarize_reference_anchors(fixture, reference_rows)
                if reference_rows
                else None
            ),
            "live_vaporock_anchor_check": {
                "requested": live_vaporock_anchor_check,
                "row_count": len(live_vaporock_rows),
                "status_counts": dict(
                    sorted(Counter(row["status"] for row in live_vaporock_rows).items())
                ),
            },
            "thermoengine_adapter_latch": latch,
            "thermoengine_probe_latch": probe_latch,
            "imcc_extrapolated_tier": {
                "row_count": len(extrapolated_rows),
                "envelope_counts": dict(
                    sorted(Counter(str(row.get("envelope_status")) for row in extrapolated_rows).items())
                ),
                "extrapolated_true_count": sum(
                    bool(row.get("extrapolated")) for row in extrapolated_rows
                ),
            },
            "artifact_guard": {
                "planned": sorted(guard.planned),
                "retired": sorted(str(name) for name in retired_artifacts),
                "retired_removed": sorted(guard.retired_removed),
            },
        }
        (output_dir / "run-metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if mode == "all":
            (output_dir / "report.md").write_text(
                generate_report(
                    fixture,
                    point_rows,
                    probe_rows,
                    coverage_rows,
                    reference_rows,
                    live_vaporock_rows,
                    thermoengine_latch=latch,
                    thermoengine_probe_latch=probe_latch,
                    extrapolated_rows=extrapolated_rows,
                ),
                encoding="utf-8",
            )
    return {
        "point_rows": point_rows,
        "extrapolated_rows": extrapolated_rows,
        "probe_rows": probe_rows,
        "coverage_rows": coverage_rows,
        "reference_rows": reference_rows,
        "live_vaporock_rows": live_vaporock_rows,
        "paired_decisions": paired_decisions,
        "metadata": metadata,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare melt-activity engines and map stripping-trajectory domain coverage."
    )
    parser.add_argument(
        "--bench-set",
        type=Path,
        default=DEFAULT_BENCH_SET,
        help=f"tracked YAML bench set (default: {DEFAULT_BENCH_SET.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"artifact directory (default: {DEFAULT_OUTPUT_DIR.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--engines",
        default=",".join(DEFAULT_ENGINES),
        help="comma-separated engines: imcc-published,imcc-ext,internal_analytic,alphamelts,thermoengine,vaporock",
    )
    parser.add_argument(
        "--mode", choices=("benchmark", "coverage", "all"), default="all"
    )
    parser.add_argument("--coverage-steps", type=int, default=21)
    parser.add_argument("--alphamelts-timeout-s", type=float, default=30.0)
    parser.add_argument(
        "--live-vaporock-anchor-check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "compare the installed VapoRock build with the frozen tracked "
            "snapshot (default: on; --no-live-vaporock-anchor-check on a "
            "directory holding live-vaporock-check.csv refuses unless the "
            "artifact is explicitly retired)"
        ),
    )
    parser.add_argument(
        "--retire-artifact",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "declare a previously generated artifact intentionally retired; "
            "the regeneration guard warns and permits its removal (repeatable)"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    names = tuple(value.strip() for value in args.engines.split(",") if value.strip())
    try:
        result = run_benchmark(
            bench_set_path=args.bench_set.resolve(),
            output_dir=args.output_dir.resolve(),
            engine_names=names,
            mode=args.mode,
            coverage_steps=args.coverage_steps,
            alphamelts_timeout_s=args.alphamelts_timeout_s,
            live_vaporock_anchor_check=args.live_vaporock_anchor_check,
            retired_artifacts=tuple(args.retire_artifact),
        )
    except RegenerationShrinkageError as exc:
        parser.error(str(exc))
    print(
        f"wrote {args.output_dir}: {len(result['point_rows'])} point-engine rows, "
        f"{len(result['coverage_rows'])} coverage rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
