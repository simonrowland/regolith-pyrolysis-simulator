"""
Melt Backend — Abstract Interface & Data Classes
=================================================

Defines the abstract MeltBackend interface and EquilibriumResult
that all thermodynamic backends must implement:
AlphaMELTSBackend, VapoRockBackend, MAGEMinBackend, and
StubBackend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Mapping, Optional


BACKEND_CAPABILITY_KEYS = (
    'silicate_melt',
    'gas_volatiles',
    'salt_phase',
    'sulfide_matte',
    'metal_alloy',
)

DEFAULT_BACKEND_CAPABILITIES = {
    key: (key == 'silicate_melt')
    for key in BACKEND_CAPABILITY_KEYS
}


class MeltBackendError(RuntimeError):
    """Base class for melt backend contract violations."""


class LiquidFractionInvalidError(MeltBackendError):
    """Raised when an ok equilibrium result lacks a real liquid fraction."""


class MeltCompositionError(MeltBackendError):
    """Raised when backend phase/composition output is physically unusable."""


_LIQUID_PHASE_NAMES = frozenset({
    'liq', 'liquid', 'LIQUID', 'melt', 'Melt',
})


def liquid_fraction_from_phase_masses(
    phase_masses_kg: Mapping[str, float],
) -> Optional[float]:
    """Return liquid mass / total phase mass, or None when mass is unknown."""
    total_mass_kg = 0.0
    liquid_mass_kg = 0.0
    for phase, mass_kg in phase_masses_kg.items():
        try:
            mass = float(mass_kg)
        except (TypeError, ValueError) as exc:
            raise LiquidFractionInvalidError(
                f'phase_mass_invalid: {phase}={mass_kg!r}'
            ) from exc
        if not math.isfinite(mass) or mass < 0.0:
            raise LiquidFractionInvalidError(
                f'phase_mass_invalid: {phase}={mass_kg!r}'
            )
        if mass == 0.0:
            continue
        phase_name = str(phase)
        total_mass_kg += mass
        if (
            phase_name in _LIQUID_PHASE_NAMES
            or phase_name.lower().startswith('liq')
            or phase_name.endswith('_Liq')
        ):
            liquid_mass_kg += mass
    if total_mass_kg <= 0.0:
        return None
    return liquid_mass_kg / total_mass_kg


def normalize_backend_capabilities(value: Any = None) -> Dict[str, bool]:
    """
    Normalize backend capability config.

    Accepted forms:
    - None: default silicate melt only
    - mapping: {"silicate_melt": true, "gas_volatiles": false}
    - sequence/string: enabled capability names
    """
    capabilities = dict(DEFAULT_BACKEND_CAPABILITIES)
    if value is None:
        return capabilities

    if isinstance(value, str):
        raw_items = [(value, True)]
    elif isinstance(value, Mapping):
        raw_items = value.items()
    elif isinstance(value, (list, tuple, set)):
        raw_items = [(item, True) for item in value]
    else:
        raise ValueError('backend capabilities must be a mapping or list')

    for item in raw_items:
        name, enabled = item
        key = str(name).strip()
        if key not in BACKEND_CAPABILITY_KEYS:
            raise ValueError(f'unknown backend capability: {key}')
        capabilities[key] = bool(enabled)
    return capabilities


# Cleaned silicate melt is the only ledger account the silicate-oxide
# adapters (VapoRock, MAGEMin) may consume; every other account is
# filtered out before the upstream library is called (binding spec §7).
CLEANED_MELT_ACCOUNT = 'process.cleaned_melt'


def split_cleaned_melt_account(
    composition_mol_by_account: Mapping[str, Mapping[str, float]],
) -> tuple[Dict[str, float], List[str]]:
    """
    Extract the cleaned-melt account; report every other account.

    Returns ``(melt_species_mol, dropped_account_names)``.  The
    silicate-oxide adapters only consume ``process.cleaned_melt``; any
    other account that carries positive material is reported back so the
    caller can record a warning (binding spec §7 — these adapters must
    not receive metal / sulfide / salt / halide accounts).
    """
    melt_mol: Dict[str, float] = {}
    for species, mol in (
        composition_mol_by_account.get(CLEANED_MELT_ACCOUNT, {}) or {}
    ).items():
        value = float(mol)
        if value > 0.0:
            melt_mol[str(species)] = melt_mol.get(str(species), 0.0) + value

    dropped: List[str] = []
    for account, species_mol in composition_mol_by_account.items():
        if str(account) == CLEANED_MELT_ACCOUNT:
            continue
        if any(float(mol) > 0.0 for mol in (species_mol or {}).values()):
            dropped.append(str(account))
    return melt_mol, sorted(dropped)


@dataclass(frozen=True)
class MeltOxideProjection:
    oxide_wt_pct: Dict[str, float]
    dropped_mass_kg_by_species: Dict[str, float]
    warnings: tuple[str, ...] = ()

    @property
    def diagnostics(self) -> Dict[str, Any]:
        total = sum(self.dropped_mass_kg_by_species.values())
        if total <= 0.0:
            return {}
        return {
            "dropped_non_basis_melt_mass_kg_by_species": dict(
                self.dropped_mass_kg_by_species
            ),
            "dropped_non_basis_melt_mass_kg": total,
        }


def project_melt_to_oxide_wt_pct(
    *,
    composition_kg: Optional[Dict[str, float]],
    composition_mol: Optional[Dict[str, float]],
    oxide_basis: tuple,
    species_formula_registry: Optional[Mapping[str, Any]] = None,
) -> Dict[str, float]:
    return project_melt_to_oxide_projection(
        composition_kg=composition_kg,
        composition_mol=composition_mol,
        oxide_basis=oxide_basis,
        species_formula_registry=species_formula_registry,
    ).oxide_wt_pct


def project_melt_to_oxide_projection(
    *,
    composition_kg: Optional[Dict[str, float]],
    composition_mol: Optional[Dict[str, float]],
    oxide_basis: tuple,
    species_formula_registry: Optional[Mapping[str, Any]] = None,
) -> MeltOxideProjection:
    """
    Project the simulator's mol/kg melt composition to oxide wt% in the
    given oxide basis.

    Shared by the silicate-oxide adapters (VapoRock, MAGEMin), whose
    upstream bases are identical to MELTS for the oxides shared with the
    simulator, so this is a straight rename + normalisation.  Any species
    not in ``oxide_basis`` is dropped after being recorded in diagnostics.

    ``species_formula_registry`` is the simulator's formula registry
    (threaded through from the layered ABC) used for the mol -> kg
    projection; ``None`` falls back to the builtin formula table.
    """
    from simulator.accounting.formulas import resolve_species_formula

    if composition_mol is not None:
        kg_by_species: Dict[str, float] = {}
        for species, mol in composition_mol.items():
            value = float(mol)
            if value <= 0.0:
                continue
            kg = value * resolve_species_formula(
                species, species_formula_registry).molar_mass_kg_per_mol()
            kg_by_species[species] = kg
    else:
        kg_by_species = {
            species: float(value)
            for species, value in (composition_kg or {}).items()
            if float(value) > 0.0
        }

    filtered = {
        species: kg
        for species, kg in kg_by_species.items()
        if species in oxide_basis
    }

    dropped = {
        str(species): float(kg)
        for species, kg in kg_by_species.items()
        if species not in oxide_basis and float(kg) > 0.0
    }

    total = sum(filtered.values())
    if total <= 0:
        oxide_wt_pct: Dict[str, float] = {}
    else:
        oxide_wt_pct = {
            species: kg / total * 100.0
            for species, kg in filtered.items()
        }

    warnings_out: tuple[str, ...] = ()
    if dropped:
        total_dropped = sum(dropped.values())
        species_text = ", ".join(
            f"{species}={kg:.12g} kg" for species, kg in sorted(dropped.items())
        )
        warnings_out = (
            "dropped_non_basis_melt_mass: "
            f"total={total_dropped:.12g} kg; species={species_text}",
        )

    return MeltOxideProjection(
        oxide_wt_pct=oxide_wt_pct,
        dropped_mass_kg_by_species=dict(sorted(dropped.items())),
        warnings=warnings_out,
    )


@dataclass
class EquilibriumResult:
    """
    Result of a thermodynamic equilibrium calculation.

    Returned by MeltBackend.equilibrate() with phase assemblage,
    species mol inventories where available, kg projections for external
    reporting, thermodynamic activities, and vapor pressures.
    """
    temperature_C: float = 0.0
    pressure_bar: float = 0.0

    # Phase assemblage
    phases_present: List[str] = field(default_factory=list)
    phase_masses_kg: Dict[str, float] = field(default_factory=dict)
    phase_species_mol: Dict[str, Dict[str, float]] = field(default_factory=dict)
    phase_species_kg: Dict[str, Dict[str, float]] = field(default_factory=dict)
    phase_compositions: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # Liquid state
    liquid_fraction: Optional[float] = None
    # False for vapor-only backends (e.g. VapoRock) that never solve a melt
    # phase assemblage; ``__post_init__`` then permits ``liquid_fraction=None``
    # on ``status='ok'``.  Melt-solving backends must leave this True.
    phase_assemblage_available: bool = True
    liquid_composition_wt_pct: Dict[str, float] = field(default_factory=dict)
    liquid_viscosity_Pa_s: Optional[float] = None

    # Vapor pressures (Pa) for each volatile species
    vapor_pressures_Pa: Dict[str, float] = field(default_factory=dict)

    # Per-species source for ``vapor_pressures_Pa`` values.
    vapor_pressures_source: Dict[str, str] = field(default_factory=dict)

    # Thermodynamic activities in the melt. Field name is legacy.
    activity_coefficients: Dict[str, float] = field(default_factory=dict)

    # Oxygen fugacity
    fO2_log: float = -9.0  # log10(fO2 / 1 bar)

    # Backend diagnostics
    warnings: List[str] = field(default_factory=list)

    # Optional atom-conserving redistribution emitted by thermodynamic backends.
    # Backends that report phase species after equilibrium must provide this so
    # AtomLedger remains authoritative.
    ledger_transition: Any | None = None

    # Per-call backend outcome.  Unlike engine identity (known from the
    # user's backend selection at web/events.py::_get_backend) and intent
    # authority (an engine x intent lookup against the binding-spec matrix),
    # status is a genuinely per-call runtime signal:
    #
    #   'ok'            - the engine ran and produced a usable result.
    #   'not_converged' - the engine ran but did not converge / produce one.
    #   'out_of_domain' - a DomainGate / account filter rejected the input.
    #   'unavailable'   - the engine/library/binary is absent for this call.
    #
    # status is descriptive: it surfaces existing state at the consumption
    # point and never gates a new control-flow branch.
    status: str = 'ok'

    # Optional sulfur-saturation gate (SULFUR_SATURATION_GATE intent) result,
    # attached by ``simulator/core.py::_get_equilibrium`` after a backend
    # equilibration succeeds and Stage 0 sulfide/sulfate inventory is
    # non-zero.  Typed as ``Any`` to avoid an import cycle between this
    # module and ``simulator.melt_backend.sulfsat``; the concrete type is
    # ``simulator.melt_backend.sulfsat.SulfurSaturationResult``.  Backends
    # do not populate this themselves — the SulfSat gate runs *outside*
    # the backend (it is a post-equilibrium gate, not a ``MeltBackend``).
    sulfur_saturation: Any | None = None

    # Optional liquidus temperature (°C) for this composition + pressure.
    # Populated by backends that compute a liquidus alongside the per-T
    # equilibration (e.g., AlphaMELTS subprocess parses one from its
    # findLiq output; ThermoEngine python_api gets one from
    # PetThermoTools.findLiq). Read by the AlphaMELTS provider's diagnostic
    # projection (``engines/alphamelts/parser.py``) and by C6 termination /
    # freeze-margin diagnostics.
    #
    # 0.5.4 W6 (M3 historical-audit closure, 2026-05-28): pre-W6, the
    # AlphaMELTS subprocess path wrote the value ONLY as a warning string
    # (``eq.warnings.append('AlphaMELTS liquidus_C=...')``) which downstream
    # consumers had to regex-parse out. The structured field is the
    # canonical source going forward; the warning is still emitted for
    # legacy log consumers but the field is what kernel + provider read.
    # Field name mirrors ``LiquidusSolidusResult.liquidus_T_C`` in
    # ``simulator/melt_backend/liquidus.py`` — that dataclass remains the
    # canonical surface for the *dedicated* liquidus-finder workflow; this
    # field carries the per-equilibration liquidus only when a backend
    # opportunistically computes one alongside the equilibration request.
    #
    # 0.5.4.1 (2026-05-28 post-push P2 fix): field landed at the END of
    # the dataclass to preserve positional-constructor ABI for external
    # callers. Initial 0.5.4 placement (between ``warnings`` and
    # ``ledger_transition``) silently shifted positional indices for
    # ``ledger_transition``, ``status``, and ``sulfur_saturation``;
    # placing the new field last keeps the historic order intact.
    liquidus_T_C: Optional[float] = None

    # Optional per-call structured backend diagnostics. Kept at the ABI-safe
    # tail for the same positional-constructor reason as ``liquidus_T_C``.
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status != 'ok':
            return
        if self.liquid_fraction is None:
            if not self.phase_assemblage_available:
                return
            raise LiquidFractionInvalidError('liquid_fraction_missing')
        try:
            liquid_fraction = float(self.liquid_fraction)
        except (TypeError, ValueError) as exc:
            raise LiquidFractionInvalidError(
                f'liquid_fraction_invalid: {self.liquid_fraction!r}'
            ) from exc
        if (
            not math.isfinite(liquid_fraction)
            or liquid_fraction < 0.0
            or liquid_fraction > 1.0
        ):
            raise LiquidFractionInvalidError(
                f'liquid_fraction_invalid: {self.liquid_fraction!r}'
            )
        self.liquid_fraction = liquid_fraction


class MeltBackend(ABC):
    """
    Abstract interface for thermodynamic melt calculations.

    Implementations wrap different thermodynamic engines:
    - AlphaMELTSBackend (silicate phase equilibrium via PetThermoTools
      or subprocess)
    - VapoRockBackend (vapor-melt equilibrium / vapor-side only)
    - MAGEMinBackend (silicate phase equilibrium, shadow second opinion)
    - StubBackend (no phase equilibrium; core.py owns Antoine fallback)
    """

    @abstractmethod
    def initialize(self, config: dict) -> bool:
        """
        Initialize the backend with configuration.

        Returns True if the backend is ready to use.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is installed and functional."""

    @abstractmethod
    def equilibrate(self, temperature_C: float,
                    composition_kg: Optional[Dict[str, float]] = None,
                    fO2_log: float = -9.0,
                    pressure_bar: float = 1e-6,
                    *,
                    composition_mol: Optional[Dict[str, float]] = None,
                    composition_mol_by_account: Optional[
                        Mapping[str, Mapping[str, float]]
                    ] = None,
                    species_formula_registry: Optional[Mapping[str, Any]] = None,
                    ) -> EquilibriumResult:
        """
        Calculate thermodynamic equilibrium at given conditions.

        Args:
            temperature_C:   Melt temperature (°C)
            composition_kg:  External kg projection of melt species
            composition_mol: Canonical melt species inventory in mol
            composition_mol_by_account:
                              Canonical per-ledger-account species inventory
            species_formula_registry: Simulator formula registry for kg adapters
            fO2_log:         log10(oxygen fugacity / 1 bar)
            pressure_bar:    Total pressure (bar)

        Returns:
            EquilibriumResult with phases, activities, vapor pressures
        """

    @abstractmethod
    def get_vapor_species(self) -> List[str]:
        """Return list of vapor species this backend can calculate."""

    def capabilities(self) -> Dict[str, bool]:
        """Return chemistry/process coverage exposed by this backend."""
        return dict(DEFAULT_BACKEND_CAPABILITIES)

    def ledger_account_policies(self) -> tuple[Any, ...]:
        """Return backend-required AtomLedger account policies."""
        return ()

    def capability_summary(self) -> str:
        """Human-readable capability status."""
        enabled = [
            key.replace('_', ' ')
            for key, value in self.capabilities().items()
            if value
        ]
        if enabled == ['silicate melt']:
            return 'silicate melt only'
        return ', '.join(enabled) if enabled else 'none'


class StubBackend(MeltBackend):
    """
    Minimal stub backend for development and testing.

    Returns empty equilibrium results.  The simulator's
    _stub_equilibrium() method handles Antoine-equation
    vapor pressures independently of this class.
    """

    def initialize(self, config: dict) -> bool:
        return True

    def is_available(self) -> bool:
        return False  # Signals core.py to use its own stub logic

    def equilibrate(self, temperature_C, composition_kg=None,
                    fO2_log=-9.0, pressure_bar=1e-6, *,
                    composition_mol=None, composition_mol_by_account=None,
                    species_formula_registry=None):
        # The stub wraps no real engine; is_available() is False and
        # core.py runs its own Ellingham/Antoine path instead.
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            status='unavailable',
            liquid_fraction=None,
            phase_assemblage_available=False,
        )

    def get_vapor_species(self):
        return ['Na', 'K', 'Fe', 'Mg', 'Ca', 'SiO']
