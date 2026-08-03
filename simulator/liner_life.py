"""Non-binding inversion from target liner life to a temperature diagnostic.

The result is diagnostic only: this module does not alter optimizer bounds,
recipe validation, furnace-envelope guards, or any applied temperature.

Recession physics come from ``simulator.refractory_vaporization`` (t-477):
congruent free-molecular vaporization with alpha=1 on included carriers only.
That sum is status-bearing, not a proven total-recession upper bound - omitted
carriers can raise loss and shift self-buffered pO2. Wall-local pressure (t-475)
remains a pending dependency for process-overhead pO2 fidelity.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from simulator.furnace_materials import load_furnace_materials
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.refractory_vaporization import (
    CongruentVaporizationError,
    solve_congruent_vaporization,
)


DIAGNOSTIC_AUTHORITY = "diagnostic_only"
DIAGNOSTIC_DEPENDENCIES = ("t-475",)
TARGET_LIFE_CANONICAL_UNIT = "runs"
# Free-molecular pO2 boundary: 1 bar = 1e5 Pa matches the JANAF standard
# pressure used by the refractory congruent-vaporization solver
# (STANDARD_PRESSURE_PA = 100000).
_BAR_TO_PA = 100_000.0
DEFAULT_DIAGNOSTIC_TARGET_LIFE_RUNS = 100.0
DEFAULT_DIAGNOSTIC_HOT_HOURS_PER_RUN = 10.0
DEFAULT_DIAGNOSTIC_MATERIAL_ID = "dense_alumina_continuous"


class LinerLifeInputRefusal(ValueError):
    """Typed refusal for invalid operator/configuration input."""

    terminal_refusal = True

    def __init__(self, reason: str, diagnostic: Mapping[str, Any]):
        self.reason = reason
        self.diagnostic = dict(diagnostic)
        super().__init__(reason)


class LinerLifeRefusal(RuntimeError):
    """Typed refusal when no trustworthy ceiling can be emitted."""

    terminal_refusal = True

    def __init__(self, reason: str, diagnostic: Mapping[str, Any]):
        self.reason = reason
        self.diagnostic = dict(diagnostic)
        super().__init__(reason)


class RecessionDataUnavailable(RuntimeError):
    """Evaluator signal for an uncharacterized material/atmosphere."""


@dataclass(frozen=True)
class AnalyticRecessionScreen:
    """Conservative recession upper bound from the cheap analytic tier."""

    upper_bound_mm_per_1000h: float
    basis: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "upper_bound_mm_per_1000h",
            _nonnegative(
                self.upper_bound_mm_per_1000h,
                "analytic_screen_upper_bound_mm_per_1000h",
            ),
        )
        if not str(self.basis).strip():
            raise _input_refusal(
                "invalid_analytic_screen",
                detail="basis must be non-empty",
            )


@dataclass(frozen=True)
class RecessionMonotonicityEvidence:
    """Evaluator-owned proof status for one material/pO2/T interval."""

    monotone_increasing: bool
    basis: str

    def __post_init__(self) -> None:
        if not isinstance(self.monotone_increasing, bool):
            raise _input_refusal(
                "invalid_monotonicity_evidence",
                detail="monotone_increasing must be bool",
            )
        if not str(self.basis).strip():
            raise _input_refusal(
                "invalid_monotonicity_evidence",
                detail="basis must be non-empty",
            )


class RecessionEvaluator(Protocol):
    """Cheap analytic screen plus the full recession evaluator."""

    def analytic_screen_recession_mm_per_1000h(
        self,
        *,
        material_id: str,
        temperature_C: float,
        pO2_bar: float,
    ) -> AnalyticRecessionScreen | None: ...

    def recession_mm_per_1000h(
        self,
        *,
        material_id: str,
        temperature_C: float,
        pO2_bar: float,
    ) -> float: ...

    def monotonicity_evidence(
        self,
        *,
        material_id: str,
        lower_temperature_C: float,
        upper_temperature_C: float,
        pO2_bar: float,
    ) -> RecessionMonotonicityEvidence: ...


@dataclass(frozen=True)
class _RecessionMaterialInputs:
    material_id: str
    refractory_material: str
    density_kg_m3: float
    density_citation_status: str
    density_provenance: str
    source: str


class CongruentVaporizationRecessionEvaluator:
    """RecessionEvaluator over pure-oxide congruent vaporization (t-477).

    ``alpha=1`` bounds only the included gas_species carriers. Results carry
    ``flux_classification=included_carrier_equilibrium_effusion_sum`` and must
    not be treated as a proven total-recession upper bound.
    """

    def __init__(
        self,
        *,
        catalog: Mapping[str, Any] | None = None,
        configuration_source: str | None = None,
    ) -> None:
        self._catalog = catalog
        self._configuration_source = configuration_source
        self._last_status: dict[str, Any] | None = None

    @property
    def last_status(self) -> dict[str, Any] | None:
        """Status-bearing fields from the most recent full recession solve."""

        return None if self._last_status is None else dict(self._last_status)

    def analytic_screen_recession_mm_per_1000h(
        self,
        *,
        material_id: str,
        temperature_C: float,
        pO2_bar: float,
    ) -> AnalyticRecessionScreen | None:
        # Included-carrier alpha=1 is not a conservative total-loss envelope, so
        # the short-circuit screen stays unavailable until a true bound exists.
        del material_id, temperature_C, pO2_bar
        return None

    def recession_mm_per_1000h(
        self,
        *,
        material_id: str,
        temperature_C: float,
        pO2_bar: float,
    ) -> float:
        inputs = self._resolve_material(material_id)
        temperature_K = _finite(temperature_C, "temperature_C") + CELSIUS_TO_KELVIN_OFFSET
        pO2 = _nonnegative(pO2_bar, "pO2_bar")
        try:
            if pO2 == 0.0:
                result = solve_congruent_vaporization(
                    inputs.refractory_material,
                    temperature_K,
                    oxygen_mode="self_buffered",
                )
            else:
                result = solve_congruent_vaporization(
                    inputs.refractory_material,
                    temperature_K,
                    oxygen_mode="imposed",
                    imposed_pO2_pa=pO2 * _BAR_TO_PA,
                )
            rate = result.recession_mm_per_1000h(inputs.density_kg_m3)
        except (CongruentVaporizationError, ValueError) as exc:
            self._last_status = {
                "material_id": inputs.material_id,
                "refractory_material": inputs.refractory_material,
                "status": "recession_data_unavailable",
                "detail": str(exc),
            }
            raise RecessionDataUnavailable(str(exc)) from exc

        self._last_status = {
            "material_id": inputs.material_id,
            "refractory_material": inputs.refractory_material,
            "temperature_C": float(temperature_C),
            "temperature_K": float(temperature_K),
            "pO2_bar": float(pO2),
            "oxygen_mode": result.oxygen_mode,
            "density_kg_m3": inputs.density_kg_m3,
            "density_citation_status": inputs.density_citation_status,
            "density_provenance": inputs.density_provenance,
            "density_source": inputs.source,
            "recession_mm_per_1000h": float(rate),
            "flux_classification": result.flux_classification,
            "certification_status": result.certification_status,
            "certification_blockers": list(result.certification_blockers),
            "evaporation_coefficient": float(result.evaporation_coefficient),
            "unmodeled_species": list(result.unmodeled_species),
            "transport_applicability": result.transport_applicability,
            # Status-bearing: alpha=1 is NOT a total recession upper bound.
            "upper_bound_claim": "included_carriers_only",
        }
        return float(rate)

    def monotonicity_evidence(
        self,
        *,
        material_id: str,
        lower_temperature_C: float,
        upper_temperature_C: float,
        pO2_bar: float,
    ) -> RecessionMonotonicityEvidence:
        # Resolve so unknown/unconfigured materials refuse before bisection.
        self._resolve_material(material_id)
        del lower_temperature_C, upper_temperature_C, pO2_bar
        return RecessionMonotonicityEvidence(
            True,
            basis=(
                "congruent free-molecular vaporization (included-carrier sum) "
                "is monotone increasing in temperature at fixed pO2; "
                "the inversion also samples the bracket empirically"
            ),
        )

    def _resolve_material(self, material_id: str) -> _RecessionMaterialInputs:
        canonical = self._catalog is None
        raw_catalog = load_furnace_materials() if canonical else self._catalog
        materials = raw_catalog.get("furnace_materials", raw_catalog)
        material = materials.get(str(material_id))
        if not isinstance(material, Mapping):
            raise RecessionDataUnavailable(
                f"unknown liner material for recession evaluation: {material_id}"
            )
        config = material.get("liner_life_diagnostic")
        if not isinstance(config, Mapping):
            raise RecessionDataUnavailable(
                f"liner_life_diagnostic unavailable for material {material_id}"
            )
        refractory = config.get("refractory_material")
        if refractory is None or not str(refractory).strip():
            raise RecessionDataUnavailable(
                f"furnace_materials.{material_id}.liner_life_diagnostic."
                "refractory_material is required"
            )
        density = config.get("density_kg_m3")
        try:
            density_kg_m3 = _positive(density, "density_kg_m3")
        except LinerLifeInputRefusal as exc:
            raise RecessionDataUnavailable(
                f"furnace_materials.{material_id}.liner_life_diagnostic."
                f"density_kg_m3 invalid: {exc.reason}"
            ) from exc
        citation = str(config.get("density_citation_status") or "uncited").strip()
        provenance = str(config.get("density_provenance") or "").strip()
        if not provenance:
            provenance = (
                f"furnace_materials.{material_id}.liner_life_diagnostic.density_kg_m3"
            )
        source = self._configuration_source or (
            f"data/furnace_materials.yaml:furnace_materials.{material_id}."
            "liner_life_diagnostic"
            if canonical
            else f"caller catalog:furnace_materials.{material_id}.liner_life_diagnostic"
        )
        return _RecessionMaterialInputs(
            material_id=str(material_id),
            refractory_material=str(refractory).strip(),
            density_kg_m3=density_kg_m3,
            density_citation_status=citation,
            density_provenance=provenance,
            source=source,
        )


def build_liner_life_run_diagnostic(
    *,
    material_id: str | None = None,
    target_life_runs: Any = DEFAULT_DIAGNOSTIC_TARGET_LIFE_RUNS,
    hot_hours_per_run: Any | None = None,
    pO2_bar: Any = 0.0,
    evaluator: RecessionEvaluator | None = None,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Best-effort, info-level liner-life diagnostic for run artifacts.

    Never raises into the runner: refusals become status-bearing records.
    Does not gate recipes, optimizer bounds, or furnace envelopes.

    Requires an explicit ``material_id`` — never fabricates a default wall
    identity (golden-neutral: unselected runs omit the diagnostic entirely).
    ``DEFAULT_DIAGNOSTIC_MATERIAL_ID`` is the catalogue default for *callers*
    that intentionally select one, not a silent fallback.
    """

    selected_material = (
        str(material_id).strip()
        if material_id is not None and str(material_id).strip()
        else ""
    )
    hours = (
        DEFAULT_DIAGNOSTIC_HOT_HOURS_PER_RUN
        if hot_hours_per_run is None
        else hot_hours_per_run
    )
    envelope: dict[str, Any] = {
        "status": "computed_diagnostic_not_applied",
        "authority": DIAGNOSTIC_AUTHORITY,
        "level": "info",
        "binding": False,
        "gating": False,
        "material_id": selected_material or None,
        "material_id_source": "explicit" if selected_material else "missing",
        "pending_dependencies": list(DIAGNOSTIC_DEPENDENCIES),
        "target_life_runs": None,
        "hot_hours_per_run": None,
        "pO2_bar": None,
        "ceiling": None,
        "recession_model": None,
        "refusal": None,
    }
    if not selected_material:
        # Null-hypothesis: fabricating dense_alumina would break golden-neutral
        # contracts and invent provenance for an unselected wall. Refuse instead.
        envelope["status"] = "refused"
        envelope["refusal"] = {
            "reason": "furnace_material_id_required",
            "diagnostic": {
                "detail": (
                    "liner_life_diagnostic requires an explicit furnace_material_id; "
                    "no default material is invented"
                ),
            },
        }
        return envelope

    active_evaluator = evaluator or CongruentVaporizationRecessionEvaluator(
        catalog=catalog
    )
    try:
        diagnostic = liner_temperature_ceiling_diagnostic(
            material_id=selected_material,
            target_life_value=target_life_runs,
            target_life_unit=TARGET_LIFE_CANONICAL_UNIT,
            hot_hours_per_run=hours,
            pO2_bar=pO2_bar,
            evaluator=active_evaluator,
            catalog=catalog,
        )
    except (LinerLifeInputRefusal, LinerLifeRefusal) as exc:
        envelope["status"] = "refused"
        envelope["refusal"] = {
            "reason": exc.reason,
            "diagnostic": dict(exc.diagnostic),
        }
        if isinstance(active_evaluator, CongruentVaporizationRecessionEvaluator):
            envelope["recession_model"] = active_evaluator.last_status
        return envelope
    except Exception as exc:  # noqa: BLE001 -- instrument-only path must not abort runs
        envelope["status"] = "failed"
        envelope["refusal"] = {
            "reason": "liner_life_diagnostic_failed",
            "detail": f"{type(exc).__name__}: {exc}",
        }
        return envelope

    envelope["target_life_runs"] = diagnostic.target_life_runs
    envelope["hot_hours_per_run"] = float(hours)
    envelope["pO2_bar"] = diagnostic.pO2_bar
    envelope["ceiling"] = diagnostic.as_dict()
    if isinstance(active_evaluator, CongruentVaporizationRecessionEvaluator):
        envelope["recession_model"] = active_evaluator.last_status
    return envelope


@dataclass(frozen=True)
class LinerLifeTarget:
    """Operator target normalized to the canonical unit, runs."""

    runs: float
    source_value: float
    source_unit: str
    conversion: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "runs", _positive(self.runs, "target_life_runs"))
        object.__setattr__(
            self,
            "source_value",
            _positive(self.source_value, "target_life_source_value"),
        )

    @classmethod
    def from_input(
        cls,
        value: Any,
        *,
        unit: str = TARGET_LIFE_CANONICAL_UNIT,
        hot_hours_per_run: Any | None = None,
        runs_per_campaign: Any | None = None,
    ) -> LinerLifeTarget:
        source = _positive(value, "target_liner_life")
        normalized = str(unit).strip().lower().replace("-", "_")
        if normalized in {"run", "runs"}:
            return cls(source, source, "runs", f"{source:g} runs")
        if normalized in {"campaign", "campaigns"}:
            scale = _positive(runs_per_campaign, "runs_per_campaign")
            runs = source * scale
            return cls(
                runs,
                source,
                "campaigns",
                f"{source:g} campaigns * {scale:g} runs/campaign = {runs:g} runs",
            )
        if normalized in {"hot_hour", "hot_hours", "h", "hr"}:
            scale = _positive(hot_hours_per_run, "hot_hours_per_run")
            runs = source / scale
            return cls(
                runs,
                source,
                "hot_hours",
                f"{source:g} hot h / {scale:g} hot h/run = {runs:g} runs",
            )
        raise _input_refusal(
            "unsupported_liner_life_unit",
            field="unit",
            value=unit,
            accepted_units=("runs", "campaigns", "hot_hours"),
            canonical_unit=TARGET_LIFE_CANONICAL_UNIT,
        )


@dataclass(frozen=True)
class LinerLifeConfiguration:
    """Catalogue/process inputs required by the inversion."""

    material_id: str
    liner_thickness_mm: float
    wear_budget_fraction: float
    hot_hours_per_run: float
    lowest_useful_temperature_C: float
    structural_limit_C: float
    analytic_screen_threshold_fraction: float
    bisection_tolerance_C: float
    bisection_max_iterations: int
    monotonicity_samples: int
    source: str

    def __post_init__(self) -> None:
        positive_fields = (
            "liner_thickness_mm",
            "wear_budget_fraction",
            "hot_hours_per_run",
            "bisection_tolerance_C",
        )
        finite_fields = ("lowest_useful_temperature_C", "structural_limit_C")
        for field in positive_fields:
            object.__setattr__(self, field, _positive(getattr(self, field), field))
        for field in finite_fields:
            object.__setattr__(self, field, _finite(getattr(self, field), field))
        object.__setattr__(
            self,
            "analytic_screen_threshold_fraction",
            _nonnegative(
                self.analytic_screen_threshold_fraction,
                "analytic_screen_threshold_fraction",
            ),
        )
        object.__setattr__(
            self,
            "bisection_max_iterations",
            _integer(self.bisection_max_iterations, "bisection_max_iterations", 1),
        )
        object.__setattr__(
            self,
            "monotonicity_samples",
            _integer(self.monotonicity_samples, "monotonicity_samples", 3),
        )
        if not str(self.material_id).strip() or not str(self.source).strip():
            raise _input_refusal(
                "invalid_liner_life_configuration",
                detail="material_id and source must be non-empty",
            )
        if self.wear_budget_fraction > 1.0:
            raise _input_refusal(
                "invalid_liner_life_configuration",
                detail="wear_budget_fraction must be <= 1",
            )
        if self.analytic_screen_threshold_fraction > 1.0:
            raise _input_refusal(
                "invalid_liner_life_configuration",
                detail="analytic_screen_threshold_fraction must be <= 1",
            )
        if self.lowest_useful_temperature_C > self.structural_limit_C:
            raise _input_refusal(
                "invalid_liner_life_configuration",
                detail="lowest useful temperature exceeds structural limit",
            )

    @classmethod
    def from_material_catalogue(
        cls,
        material_id: str,
        *,
        hot_hours_per_run: Any,
        catalog: Mapping[str, Any] | None = None,
        source: str | None = None,
    ) -> LinerLifeConfiguration:
        canonical = catalog is None
        raw_catalog = load_furnace_materials() if canonical else catalog
        materials = raw_catalog.get("furnace_materials", raw_catalog)
        material = materials.get(str(material_id))
        if not isinstance(material, Mapping):
            raise _input_refusal("unknown_liner_material", material_id=material_id)
        config = material.get("liner_life_diagnostic")
        if not isinstance(config, Mapping):
            raise _input_refusal(
                "liner_life_configuration_unavailable",
                material_id=material_id,
                required_catalogue_path=(
                    f"furnace_materials.{material_id}.liner_life_diagnostic"
                ),
            )
        prefix = f"furnace_materials.{material_id}.liner_life_diagnostic"
        return cls(
            material_id=str(material_id),
            liner_thickness_mm=config.get("liner_thickness_mm"),
            wear_budget_fraction=config.get("wear_budget_fraction"),
            hot_hours_per_run=hot_hours_per_run,
            lowest_useful_temperature_C=config.get("lowest_useful_temperature_C"),
            structural_limit_C=material.get("max_service_T_C"),
            analytic_screen_threshold_fraction=config.get(
                "analytic_screen_threshold_fraction"
            ),
            bisection_tolerance_C=config.get("bisection_tolerance_C"),
            bisection_max_iterations=config.get("bisection_max_iterations"),
            monotonicity_samples=config.get("monotonicity_samples"),
            source=source
            or (
                f"data/furnace_materials.yaml:{prefix}"
                if canonical
                else f"caller catalog:{prefix}"
            ),
        )


@dataclass(frozen=True)
class LinerTemperatureCeilingDiagnostic:
    """Successful non-binding diagnostic, including its active bound."""

    status: str
    authority: str
    material_id: str
    target_life_runs: float
    target_canonical_unit: str
    target_source_unit: str
    target_conversion: str
    pO2_bar: float
    wear_budget_mm_per_1000h: float
    ceiling_T_C: float
    binding_bound: str
    structural_limit_C: float
    recession_limited_T_C: float | None
    analytic_screen_upper_bound_mm_per_1000h: float | None
    analytic_screen_basis: str | None
    analytic_screen_threshold_mm_per_1000h: float
    analytic_screen_status: str
    monotonicity_basis: str | None
    solver_initial_bracket_C: tuple[float, float] | None
    solver_final_bracket_C: tuple[float, float] | None
    solver_tolerance_C: float
    solver_iterations: int
    full_evaluation_count: int
    configuration_source: str
    pending_dependencies: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def wear_budget_mm_per_1000h(
    *,
    target_life_runs: Any,
    liner_thickness_mm: Any,
    wear_budget_fraction: Any,
    hot_hours_per_run: Any,
) -> float:
    """Maximum allowable recession rate in mm per 1,000 hot hours."""

    runs = _positive(target_life_runs, "target_life_runs")
    thickness = _positive(liner_thickness_mm, "liner_thickness_mm")
    fraction = _positive(wear_budget_fraction, "wear_budget_fraction")
    hours = _positive(hot_hours_per_run, "hot_hours_per_run")
    if fraction > 1.0:
        raise _input_refusal(
            "invalid_wear_budget_fraction",
            value=fraction,
            required_range="(0, 1]",
        )

    # Premise: disposable depth d = thickness_mm * wear_fraction over
    # N target runs, each with h hot hours.
    # Algebra: rate_1000h = d / (N * h) * 1000.
    # Unit check: mm / (runs * h/run) * (1000 h) -> mm per 1000 h.
    # Sanity anchor: 10 mm across 100 runs * 10 h/run = 10 mm/1000 h.
    return thickness * fraction / (runs * hours) * 1000.0


def liner_temperature_ceiling_diagnostic(
    *,
    material_id: str,
    target_life_value: Any,
    target_life_unit: str,
    hot_hours_per_run: Any,
    pO2_bar: Any,
    evaluator: RecessionEvaluator,
    runs_per_campaign: Any | None = None,
    catalog: Mapping[str, Any] | None = None,
    configuration_source: str | None = None,
) -> LinerTemperatureCeilingDiagnostic:
    """Normalize operator input, load config, and emit the diagnostic."""

    target = LinerLifeTarget.from_input(
        target_life_value,
        unit=target_life_unit,
        hot_hours_per_run=hot_hours_per_run,
        runs_per_campaign=runs_per_campaign,
    )
    configuration = LinerLifeConfiguration.from_material_catalogue(
        material_id,
        hot_hours_per_run=hot_hours_per_run,
        catalog=catalog,
        source=configuration_source,
    )
    return derive_liner_temperature_ceiling(
        target=target,
        configuration=configuration,
        pO2_bar=pO2_bar,
        evaluator=evaluator,
    )


def derive_liner_temperature_ceiling(
    *,
    target: LinerLifeTarget,
    configuration: LinerLifeConfiguration,
    pO2_bar: Any,
    evaluator: RecessionEvaluator,
) -> LinerTemperatureCeilingDiagnostic:
    """Back-solve a diagnostic ceiling; never apply it to runtime state."""

    pO2 = _nonnegative(pO2_bar, "pO2_bar")
    budget = wear_budget_mm_per_1000h(
        target_life_runs=target.runs,
        liner_thickness_mm=configuration.liner_thickness_mm,
        wear_budget_fraction=configuration.wear_budget_fraction,
        hot_hours_per_run=configuration.hot_hours_per_run,
    )
    screen_threshold = (
        budget * configuration.analytic_screen_threshold_fraction
    )
    low = configuration.lowest_useful_temperature_C
    high = configuration.structural_limit_C

    def refuse(reason: str, mechanism: str, **extra: Any) -> LinerLifeRefusal:
        diagnostic = {
            "status": "refused",
            "reason": reason,
            "authority": DIAGNOSTIC_AUTHORITY,
            "material_id": configuration.material_id,
            "target_life_runs": target.runs,
            "target_canonical_unit": TARGET_LIFE_CANONICAL_UNIT,
            "target_source_unit": target.source_unit,
            "target_conversion": target.conversion,
            "pO2_bar": pO2,
            "wear_budget_mm_per_1000h": budget,
            "structural_limit_C": configuration.structural_limit_C,
            "lowest_useful_temperature_C": (
                configuration.lowest_useful_temperature_C
            ),
            "binding_mechanism": mechanism,
            "solver_tolerance_C": configuration.bisection_tolerance_C,
            "configuration_source": configuration.source,
            "pending_dependencies": DIAGNOSTIC_DEPENDENCIES,
        }
        diagnostic.update(extra)
        return LinerLifeRefusal(reason, diagnostic)

    def checked_rate(value: Any, tier: str) -> float:
        try:
            return _nonnegative(value, f"{tier}_recession_mm_per_1000h")
        except LinerLifeInputRefusal as exc:
            raise refuse(
                "invalid_recession_evaluator_output",
                "invalid_recession_model",
                evaluator_tier=tier,
                detail=exc.diagnostic,
            ) from exc

    monotonicity_evidence_cache: RecessionMonotonicityEvidence | None = None

    def certified_monotonicity() -> RecessionMonotonicityEvidence:
        nonlocal monotonicity_evidence_cache
        if monotonicity_evidence_cache is not None:
            return monotonicity_evidence_cache
        try:
            evidence = evaluator.monotonicity_evidence(
                material_id=configuration.material_id,
                lower_temperature_C=low,
                upper_temperature_C=high,
                pO2_bar=pO2,
            )
        except RecessionDataUnavailable as exc:
            raise refuse(
                "recession_monotonicity_unavailable",
                "invalid_recession_model",
                detail=str(exc),
                solver_initial_bracket_C=(low, high),
            ) from exc
        except LinerLifeInputRefusal as exc:
            raise refuse(
                "invalid_recession_evaluator_output",
                "invalid_recession_model",
                evaluator_tier="monotonicity_evidence",
                detail=exc.diagnostic,
            ) from exc
        except AttributeError as exc:
            raise refuse(
                "recession_monotonicity_unavailable",
                "invalid_recession_model",
                detail="evaluator does not provide monotonicity_evidence",
                solver_initial_bracket_C=(low, high),
            ) from exc
        if not isinstance(evidence, RecessionMonotonicityEvidence):
            raise refuse(
                "invalid_recession_evaluator_output",
                "invalid_recession_model",
                evaluator_tier="monotonicity_evidence",
                detail="expected RecessionMonotonicityEvidence",
            )
        if not evidence.monotone_increasing:
            raise refuse(
                "non_monotone_recession_model",
                "invalid_recession_model",
                detail=evidence.basis,
                solver_initial_bracket_C=(low, high),
            )
        monotonicity_evidence_cache = evidence
        return evidence

    screen_upper_bound = None
    screen_basis = None
    screen_status = "unavailable_full_solve_used"
    try:
        raw_screen = evaluator.analytic_screen_recession_mm_per_1000h(
            material_id=configuration.material_id,
            temperature_C=configuration.structural_limit_C,
            pO2_bar=pO2,
        )
        if raw_screen is not None:
            if not isinstance(raw_screen, AnalyticRecessionScreen):
                raise refuse(
                    "invalid_recession_evaluator_output",
                    "invalid_recession_model",
                    evaluator_tier="analytic_screen",
                    detail=(
                        "analytic screen must return "
                        "AnalyticRecessionScreen or None"
                    ),
                )
            screen_upper_bound = raw_screen.upper_bound_mm_per_1000h
            screen_basis = raw_screen.basis
            screen_status = "above_negligible_threshold"
    except RecessionDataUnavailable:
        pass
    except LinerLifeInputRefusal as exc:
        raise refuse(
            "invalid_recession_evaluator_output",
            "invalid_recession_model",
            evaluator_tier="analytic_screen",
            detail=exc.diagnostic,
        ) from exc

    if (
        screen_upper_bound is not None
        and screen_upper_bound <= screen_threshold
    ):
        monotonicity = certified_monotonicity()
        return _result(
            target,
            configuration,
            pO2,
            budget,
            ceiling_C=configuration.structural_limit_C,
            binding="structural_limit",
            screen_upper_bound=screen_upper_bound,
            screen_basis=screen_basis,
            screen_threshold=screen_threshold,
            screen_status="negligible_structural_short_circuit",
            monotonicity_basis=monotonicity.basis,
        )

    cache: dict[float, float] = {}

    def full_rate(temperature_C: float) -> float:
        if temperature_C in cache:
            return cache[temperature_C]
        try:
            raw = evaluator.recession_mm_per_1000h(
                material_id=configuration.material_id,
                temperature_C=temperature_C,
                pO2_bar=pO2,
            )
        except RecessionDataUnavailable as exc:
            raise refuse(
                "recession_data_unavailable",
                "recession_model_unavailable",
                detail=str(exc),
                achievable_life_at_structural_limit_runs=None,
            ) from exc
        cache[temperature_C] = checked_rate(raw, "full_evaluator")
        return cache[temperature_C]

    high_rate = full_rate(high)
    if high_rate <= budget:
        monotonicity = certified_monotonicity()
        return _result(
            target,
            configuration,
            pO2,
            budget,
            ceiling_C=high,
            binding="structural_limit",
            screen_upper_bound=screen_upper_bound,
            screen_basis=screen_basis,
            screen_threshold=screen_threshold,
            screen_status=screen_status,
            full_evaluations=len(cache),
            monotonicity_basis=monotonicity.basis,
        )

    monotonicity = certified_monotonicity()

    points = _temperature_samples(low, high, configuration.monotonicity_samples)
    rates = [full_rate(point) for point in points]
    for left_T, right_T, left_rate, right_rate in zip(
        points, points[1:], rates, rates[1:]
    ):
        if right_rate < left_rate:
            raise refuse(
                "non_monotone_recession_model",
                "invalid_recession_model",
                detail=(
                    f"recession fell from {left_rate:g} at {left_T:g} C "
                    f"to {right_rate:g} at {right_T:g} C"
                ),
                solver_initial_bracket_C=(low, high),
                monotonicity_basis=monotonicity.basis,
            )

    low_rate = full_rate(low)
    if low_rate > budget:
        raise refuse(
            "no_recession_limited_temperature_solution",
            "recession_below_lowest_useful_temperature",
            achievable_life_at_structural_limit_runs=_achievable_runs(
                configuration, high_rate
            ),
            solver_initial_bracket_C=(low, high),
            recession_rate_bracket_mm_per_1000h=(low_rate, high_rate),
            monotonicity_basis=monotonicity.basis,
        )

    initial_bracket = (low, high)
    iterations = 0
    while high - low > configuration.bisection_tolerance_C:
        if iterations >= configuration.bisection_max_iterations:
            raise refuse(
                "recession_bisection_non_convergence",
                "recession_solver",
                achievable_life_at_structural_limit_runs=_achievable_runs(
                    configuration, high_rate
                ),
                solver_initial_bracket_C=initial_bracket,
                solver_final_bracket_C=(low, high),
                recession_rate_bracket_mm_per_1000h=(
                    full_rate(low),
                    full_rate(high),
                ),
                monotonicity_basis=monotonicity.basis,
            )
        midpoint = (low + high) / 2.0
        low_endpoint_rate = full_rate(low)
        midpoint_rate = full_rate(midpoint)
        high_endpoint_rate = full_rate(high)
        if not low_endpoint_rate <= midpoint_rate <= high_endpoint_rate:
            raise refuse(
                "non_monotone_recession_model",
                "invalid_recession_model",
                detail=(
                    f"rates at {low:g}/{midpoint:g}/{high:g} C are "
                    f"{low_endpoint_rate:g}/{midpoint_rate:g}/{high_endpoint_rate:g}"
                ),
                solver_initial_bracket_C=initial_bracket,
                solver_final_bracket_C=(low, high),
                monotonicity_basis=monotonicity.basis,
            )
        if midpoint_rate <= budget:
            low = midpoint
        else:
            high = midpoint
        iterations += 1

    return _result(
        target,
        configuration,
        pO2,
        budget,
        ceiling_C=low,
        binding="recession",
        recession_ceiling_C=low,
        screen_upper_bound=screen_upper_bound,
        screen_basis=screen_basis,
        screen_threshold=screen_threshold,
        screen_status=screen_status,
        initial_bracket=initial_bracket,
        final_bracket=(low, high),
        iterations=iterations,
        full_evaluations=len(cache),
        monotonicity_basis=monotonicity.basis,
    )


def _result(
    target: LinerLifeTarget,
    config: LinerLifeConfiguration,
    pO2_bar: float,
    budget: float,
    *,
    ceiling_C: float,
    binding: str,
    recession_ceiling_C: float | None = None,
    screen_upper_bound: float | None,
    screen_basis: str | None,
    screen_threshold: float,
    screen_status: str,
    initial_bracket: tuple[float, float] | None = None,
    final_bracket: tuple[float, float] | None = None,
    iterations: int = 0,
    full_evaluations: int = 0,
    monotonicity_basis: str | None = None,
) -> LinerTemperatureCeilingDiagnostic:
    return LinerTemperatureCeilingDiagnostic(
        status="computed_diagnostic_not_applied",
        authority=DIAGNOSTIC_AUTHORITY,
        material_id=config.material_id,
        target_life_runs=target.runs,
        target_canonical_unit=TARGET_LIFE_CANONICAL_UNIT,
        target_source_unit=target.source_unit,
        target_conversion=target.conversion,
        pO2_bar=pO2_bar,
        wear_budget_mm_per_1000h=budget,
        ceiling_T_C=ceiling_C,
        binding_bound=binding,
        structural_limit_C=config.structural_limit_C,
        recession_limited_T_C=recession_ceiling_C,
        analytic_screen_upper_bound_mm_per_1000h=screen_upper_bound,
        analytic_screen_basis=screen_basis,
        analytic_screen_threshold_mm_per_1000h=screen_threshold,
        analytic_screen_status=screen_status,
        monotonicity_basis=monotonicity_basis,
        solver_initial_bracket_C=initial_bracket,
        solver_final_bracket_C=final_bracket,
        solver_tolerance_C=config.bisection_tolerance_C,
        solver_iterations=iterations,
        full_evaluation_count=full_evaluations,
        configuration_source=config.source,
        pending_dependencies=DIAGNOSTIC_DEPENDENCIES,
    )


def _temperature_samples(low: float, high: float, count: int) -> list[float]:
    step = (high - low) / (count - 1)
    return [low + index * step for index in range(count)]


def _achievable_runs(
    config: LinerLifeConfiguration,
    recession_mm_per_1000h: float,
) -> float:
    if recession_mm_per_1000h == 0.0:
        return math.inf
    disposable_mm = config.liner_thickness_mm * config.wear_budget_fraction
    return (
        disposable_mm
        * 1000.0
        / recession_mm_per_1000h
        / config.hot_hours_per_run
    )


def _input_refusal(reason: str, **diagnostic: Any) -> LinerLifeInputRefusal:
    return LinerLifeInputRefusal(reason, {"reason": reason, **diagnostic})


def _declared_float(value: Any, field: str) -> float:
    # SC-95 allowlist: bool is an int subclass and numpy.bool_ float-coerces,
    # so reject bool first and then accept only declared scalar types.
    if isinstance(value, bool) or not isinstance(value, (numbers.Real, str)):
        raise _input_refusal(
            "invalid_declared_scalar",
            field=field,
            received_type=type(value).__name__,
        )
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise _input_refusal(
            "invalid_declared_scalar",
            field=field,
            received_type=type(value).__name__,
        ) from exc


def _finite(value: Any, field: str) -> float:
    result = _declared_float(value, field)
    if not math.isfinite(result):
        raise _input_refusal("non_finite_scalar", field=field, value=result)
    return result


def _positive(value: Any, field: str) -> float:
    result = _finite(value, field)
    if result <= 0.0:
        raise _input_refusal("non_positive_scalar", field=field, value=result)
    return result


def _nonnegative(value: Any, field: str) -> float:
    result = _finite(value, field)
    if result < 0.0:
        raise _input_refusal("negative_scalar", field=field, value=result)
    return result


def _integer(value: Any, field: str, minimum: int) -> int:
    numeric = _finite(value, field)
    integer = int(numeric)
    if numeric != integer or integer < minimum:
        raise _input_refusal(
            "invalid_integer_scalar",
            field=field,
            value=numeric,
            minimum=minimum,
        )
    return integer


__all__ = (
    "DIAGNOSTIC_AUTHORITY",
    "DIAGNOSTIC_DEPENDENCIES",
    "DEFAULT_DIAGNOSTIC_HOT_HOURS_PER_RUN",
    "DEFAULT_DIAGNOSTIC_MATERIAL_ID",
    "DEFAULT_DIAGNOSTIC_TARGET_LIFE_RUNS",
    "TARGET_LIFE_CANONICAL_UNIT",
    "AnalyticRecessionScreen",
    "CongruentVaporizationRecessionEvaluator",
    "LinerLifeConfiguration",
    "LinerLifeInputRefusal",
    "LinerLifeRefusal",
    "LinerLifeTarget",
    "LinerTemperatureCeilingDiagnostic",
    "RecessionDataUnavailable",
    "RecessionEvaluator",
    "RecessionMonotonicityEvidence",
    "build_liner_life_run_diagnostic",
    "derive_liner_temperature_ceiling",
    "liner_temperature_ceiling_diagnostic",
    "wear_budget_mm_per_1000h",
)
