"""
Condensation Train Model
=========================

★ TIER 2: SCIENTIST-READABLE ★

Models vapor flow through the 8-stage metals condensation train.
Each stage operates at a fixed temperature range and preferentially
collects species whose condensation temperature falls within that range.

Train topology (metals train, active C2A onward):
    Stage 0  Hot duct (>1400°C)      — IR spectroscopy, no condensation
    Stage 1  Fe condenser (1100-1400°C) — liquid Fe drains to sump
    Stage 2  Cr oxide harvester (1100-1300°C) — Cr2O3 product cartridge
    Stage 3  SiO zone (900-1200°C)   — fused silica on removable baffles
    Stage 4  Alkali/Mg cyclone (350-700°C) — Na/K/Mg condensation
    Stage 5  Vortex dust filter (200-350°C) — entrained particle capture
    Stage 6  Turbine-compressor      — pressure regulation, pO₂ control
    Stage 7  Turbine outlet monitor — terminal ledger owns O2 storage

A separate volatiles train handles C0/C0b products and is sealed
by a gate valve after devolatilisation.

Key physics:
    Condensation efficiency per species per stage:             [COND-1]
        η = 1 - exp(-t_res / τ_cond)
    where:
        t_res   = residence time in the stage (s)
        τ_cond  = characteristic condensation time (s)
              = f(T_stage, T_cond_species, surface_area, α_stick)

    If T_stage << T_condense → τ_cond is very small → η → 1
    If T_stage ≈ T_condense → τ_cond is large → η → 0

The Fe → SiO separation (Stage 1 → Stage 2):
    Stage 1 at 1200-1400°C: Fe condenses as liquid, SiO passes through
    (SiO condensation T is 900-1200°C, below Stage 1 operating T).
    Chevron separator at Stage 1 exit catches entrained Fe droplets.
    Sharp T boundary (radiation gap) prevents early SiO condensation.
    Impurity: ~0.1-1% Fe passes to Stage 2; ~0.5-2% SiO condenses in Stage 1.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict

import yaml

from simulator.core import (
    CondensationTrain, CondensationStage, EvaporationFlux, MeltState,
)
from simulator.condensation_routing import (
    STAGE_KEY_BY_NUMBER,
    accepted_species_for_stage_number,
    designated_stage_number,
    is_designated_for_stage,
)
from simulator.state import (
    MOLAR_MASS,
    PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS,
    PipeSegment,
)


BOLTZMANN_CONSTANT_J_K = 1.380649e-23
AVOGADRO_MOL = 6.02214076e23
HKL_BAND_SAMPLES = 33
DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
WALL_DEPOSIT_ACCOUNT = 'process.wall_deposit'
WALL_DEPOSIT_SEGMENT_ACCOUNTS = PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS
WALL_DEPOSIT_FRACTION_KEY = '_wall_deposit_fraction'
WALL_DEPOSIT_ACCOUNT_KEY = '_wall_deposit_account'
WALL_DEPOSIT_SEGMENT_FRACTIONS_KEY = '_wall_deposit_segment_fractions'
DEFAULT_PIPE_TEMPERATURE_C = 1500.0
DEFAULT_PIPE_DIAMETER_M = 0.12
N2_COLLISION_DIAMETER_M = 3.7e-10
CONTINUUM_BUFFER_KN = 0.01
VISCOUS_KNUDSEN_MAX = CONTINUUM_BUFFER_KN
FREE_MOLECULAR_KNUDSEN_MIN = 10.0
KNUDSEN_REFUSAL_REASON = 'knudsen_outside_viscous_flow'
KNUDSEN_TRANSITION_REASON = 'knudsen_transitional_flow'
COLD_SPOT_MARGIN_C = 25.0

# Viscous-regime mass-transfer model (post-F3 follow-on, 2026-05-27).
# F3 added regime_factor = Kn/(Kn + 0.01) to the band-integrated HKL
# flux: in viscous regime (Kn << 0.01) the HKL contribution -> 0 because
# HKL is the FREE-MOLECULAR-LIMIT flux equation and is not the right
# physics there. Without a compensating term the simulator simply
# under-predicts viscous-regime stage capture (which is what F3's commit
# noted). This block adds the Bird/Stewart/Lightfoot Sherwood-number
# boundary-layer mass-transfer flux that goes to ~1 in viscous regime
# where HKL goes to 0. Total deposition is:
#
#    J_total = J_HKL * regime_factor + J_mass_transfer * (1 - regime_factor)
#
# So at Kn -> 0 (deep viscous) the mass-transfer term dominates; at
# Kn -> inf (free molecular) the HKL term dominates; the transition
# regime is a smooth blend.
#
# Sherwood number for laminar pipe flow with constant wall concentration
# (Bird/Stewart/Lightfoot 2007 "Transport Phenomena" 2nd ed., Eq 14.4-9):
#     Sh = 3.66 (asymptotic, fully developed laminar)
#
# For the simulator's overhead piping at C2A_continuous typical
# operating point (pN2 ~10 mbar, T_gas ~1700 C, D_pipe = 0.12 m), the
# Reynolds number Re = rho * v * D / mu is well below 2100 (laminar),
# so Sh = 3.66 is the right anchor. Mass-transfer coefficient:
#     k_c = Sh * D_AB / D_pipe   (m/s)
# And the deposition flux is:
#     J_mass = k_c * (P_local - P_sat) / (R * T_gas)   (mol/m^2/s)
#
# D_AB (binary diffusion coefficient of vapor A in carrier gas B) is
# pressure-inverse and weakly T-dependent. For SiO/Na/K vapor in N2
# at 10 mbar, 1700 C, Chapman-Enskog gives D_AB ~ 1.0e-2 m^2/s. The
# default below uses 1.0e-2 m^2/s as the order-of-magnitude anchor
# and documents the regime; species-specific refinements are open
# work (tickler §5 follow-on).
DEFAULT_SHERWOOD_LAMINAR = 3.66
DEFAULT_BINARY_DIFFUSION_M2_S = 1.0e-2
GAS_CONSTANT_J_MOL_K = 8.314462618


class KnudsenRegime(Enum):
    VISCOUS = 'viscous'
    TRANSITIONAL = 'transitional'
    FREE_MOLECULAR = 'free_molecular'


class KnudsenRegimeRefusal(RuntimeError):
    """Raised when viscous-flow condensation assumptions are invalid."""

    reason = KNUDSEN_REFUSAL_REASON

    def __init__(self, diagnostic: Mapping[str, Any]):
        self.diagnostic = dict(diagnostic)
        super().__init__(KNUDSEN_REFUSAL_REASON)


# Condensation temperatures at ~1 mbar partial pressure (°C)
# Used to determine where each species preferentially deposits.
CONDENSATION_TEMPS_C = {
    'Fe':  1250,
    'SiO': 1050,   # condenses as amorphous SiO₂ (disproportionation)
    'CrO2': 1250,  # condenses as Cr2O3 + O2 in the dedicated Cr stage
    'Mg':  580,
    'Na':  480,
    'K':   420,
    'Ca':  780,
    'Mn':  1000,
    'Cr':  1280,
    'Al':  1180,   # negligible at process T, but included for completeness
    'Ti':  1500,   # negligible at process T
}

# Sticking coefficients (probability of condensation on contact)
STICKING_COEFF = {
    'Fe':  0.9,
    'SiO': 0.7,    # SiO → SiO₂ disproportionation is not instantaneous
    'CrO2': 0.9,
    'Mg':  0.8,
    'Na':  0.95,
    'K':   0.95,
    'Ca':  0.85,
    'Mn':  0.85,
    'Cr':  0.9,
}


def _load_yaml_data(filename: str) -> Dict[str, Any]:
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    with path.open('r', encoding='utf-8') as f:
        loaded = yaml.safe_load(f) or {}
    return loaded if isinstance(loaded, dict) else {}


VAPOR_PRESSURE_DATA = _load_yaml_data('vapor_pressures.yaml')
MATERIALS_DATA = _load_yaml_data('materials.yaml')


@dataclass(frozen=True)
class CondensationRouteResult:
    """Per-hour routing plan; quantities are projections until ledger credit."""

    remaining_by_species: Dict[str, float] = field(default_factory=dict)
    condensed_by_stage_species: Dict[int, Dict[str, float]] = field(default_factory=dict)
    wall_deposit_by_species: Dict[str, float] = field(default_factory=dict)
    wall_deposit_by_segment_species: Dict[str, Dict[str, float]] = field(
        default_factory=dict)
    impurity_by_stage_species: Dict[int, Dict[str, float]] = field(default_factory=dict)
    cold_spot_warnings: tuple[str, ...] = ()
    knudsen_regime_diagnostic: Dict[str, Any] = field(default_factory=dict)

    def condensed_for_species(self, species: str) -> float:
        return sum(
            stage_species.get(species, 0.0)
            for stage_species in self.condensed_by_stage_species.values()
        )

    def silica_fume_fraction_of_feedstock(self, feedstock_kg: float) -> float:
        """Return SiO-derived SiO2 condensate mass divided by feedstock mass."""

        if feedstock_kg <= 0.0:
            return 0.0
        sio_condensed_kg = self.condensed_for_species('SiO')
        sio_to_sio2 = 0.5 * MOLAR_MASS['SiO2'] / MOLAR_MASS['SiO']
        return (sio_condensed_kg * sio_to_sio2) / feedstock_kg


class CondensationModel:
    """
    Routes evaporated species through the condensation train.

    For each species in the evaporation flux, calculates the
    fraction that condenses in each stage based on the stage
    temperature relative to the species' condensation temperature.
    """

    def __init__(
        self,
        train: CondensationTrain,
        vapor_pressure_data: MutableMapping[str, Any] | None = None,
        wall_surface_area_m2: float | None = None,
        wall_temperature_C: float = DEFAULT_PIPE_TEMPERATURE_C,
    ):
        self.train = train
        self.vapor_pressure_data = vapor_pressure_data
        self.wall_surface_area_m2 = (
            float(wall_surface_area_m2)
            if wall_surface_area_m2 is not None
            else _default_pipe_surface_area_m2()
        )
        self.wall_temperature_C = float(wall_temperature_C)
        self.overhead_pressure_mbar = 0.0
        self.pipe_diameter_m = DEFAULT_PIPE_DIAMETER_M
        self.gas_temperature_C = float(wall_temperature_C)
        self.knudsen_number = math.inf
        self.regime_factor = 1.0
        self.knudsen_regime = KnudsenRegime.FREE_MOLECULAR
        self._knudsen_policy_configured = False
        self._viscous_flow_required = True
        self.pipe_segments = self._build_default_pipe_segments(
            float(wall_temperature_C))
        self.cold_spot_margin_C = COLD_SPOT_MARGIN_C
        self.last_cold_spot_diagnostic: dict[str, Any] = {
            'has_cold_spot': False,
            'warnings': [],
            'findings': [],
        }
        self.last_knudsen_regime_diagnostic: dict[str, Any] = {}
        self.cold_spot_history: list[dict[str, Any]] = []
        self.operating_history: list[dict[str, Any]] = []

        # Default residence time per stage (seconds)
        # In a real design, this comes from equipment sizing
        self.residence_time_s = {
            0: 0.5,    # Hot duct — fast transit
            1: 5.0,    # Fe condenser — baffles slow the flow
            2: 240.0,  # Cr oxide harvester — dedicated hot cartridge
            3: 4.0,    # SiO zone — removable baffles
            4: 3.0,    # Cyclone — vortex residence
            5: 2.0,    # Dust filter
            6: 0.2,    # Turbine — very fast
            7: 0.0,    # Accumulator — no condensation
        }

    def configure_operating_conditions(
        self,
        *,
        wall_temperature_C: float | None = None,
        overhead_pressure_mbar: float | None = None,
        pipe_diameter_m: float | None = None,
        gas_temperature_C: float | None = None,
        pipe_segment_temperatures_C: Mapping[str, float] | None = None,
        campaign_name: str | None = None,
        campaign_hour: float | None = None,
    ) -> None:
        """Update tick-local wall and Knudsen conditions for cached models."""

        if wall_temperature_C is not None:
            self.wall_temperature_C = float(wall_temperature_C)
        if pipe_diameter_m is not None:
            self.pipe_diameter_m = max(1.0e-9, float(pipe_diameter_m))
        if gas_temperature_C is not None:
            self.gas_temperature_C = float(gas_temperature_C)
        elif wall_temperature_C is not None:
            self.gas_temperature_C = float(wall_temperature_C)
        if overhead_pressure_mbar is not None:
            self.overhead_pressure_mbar = max(0.0, float(overhead_pressure_mbar))
            self._knudsen_policy_configured = True
            self._viscous_flow_required = _campaign_requires_viscous_flow(
                campaign_name)
        pressure_pa = self.overhead_pressure_mbar * 100.0
        gas_temperature_K = max(self.gas_temperature_C + 273.15, 1.0)
        self.knudsen_number = _knudsen_number(
            pressure_pa,
            gas_temperature_K,
            self.pipe_diameter_m,
        )
        self.regime_factor = _knudsen_regime_factor(self.knudsen_number)
        self.knudsen_regime = classify_knudsen_regime(self.knudsen_number)
        if pipe_segment_temperatures_C is not None:
            self._apply_pipe_segment_temperatures(pipe_segment_temperatures_C)
        elif wall_temperature_C is not None:
            self._apply_pipe_segment_temperatures({
                segment.name: float(wall_temperature_C)
                for segment in self.pipe_segments
            })
        self.last_knudsen_regime_diagnostic = self._current_knudsen_diagnostic()
        if overhead_pressure_mbar is not None:
            self.operating_history.append(
                {
                    "campaign": str(campaign_name or ""),
                    "campaign_hour": (
                        0.0 if campaign_hour is None
                        else float(campaign_hour)
                    ),
                    "wall_temperature_C": float(self.wall_temperature_C),
                    "pipe_segment_temperatures_C": {
                        segment.name: float(segment.wall_temperature_C)
                        for segment in self.pipe_segments
                    },
                    "overhead_pressure_mbar": float(self.overhead_pressure_mbar),
                    "knudsen_number": float(self.knudsen_number),
                    "knudsen_regime": self.knudsen_regime.value,
                    "regime_factor": float(self.regime_factor),
                    "knudsen_warnings": tuple(
                        self.last_knudsen_regime_diagnostic.get(
                            "warnings", ())),
                    "knudsen_regime_diagnostic": dict(
                        self.last_knudsen_regime_diagnostic),
                }
            )

    def _build_default_pipe_segments(
        self,
        wall_temperature_C: float,
    ) -> list[PipeSegment]:
        stages = sorted(self.train.stages, key=lambda stage: stage.stage_number)
        if len(stages) < 2:
            return []
        diameter_m = max(1.0e-9, float(self.pipe_diameter_m))
        total_length_m = (
            max(0.0, float(self.wall_surface_area_m2))
            / (math.pi * diameter_m)
        )
        length_m = total_length_m / float(len(stages) - 1)
        segments: list[PipeSegment] = []
        for upstream, downstream in zip(stages, stages[1:]):
            segments.append(PipeSegment(
                name=(
                    f'stage_{upstream.stage_number}'
                    f'_to_stage_{downstream.stage_number}'
                ),
                upstream_stage=f'stage_{upstream.stage_number}',
                downstream_stage=f'stage_{downstream.stage_number}',
                wall_temperature_C=float(wall_temperature_C),
                length_m=length_m,
                inner_diameter_m=diameter_m,
            ))
        return segments

    def _apply_pipe_segment_temperatures(
        self,
        temperatures_C: Mapping[str, float],
    ) -> None:
        if not self.pipe_segments:
            self.pipe_segments = self._build_default_pipe_segments(
                self.wall_temperature_C)
        updated: list[PipeSegment] = []
        for segment in self.pipe_segments:
            raw_temperature = temperatures_C.get(
                segment.name, self.wall_temperature_C)
            updated.append(PipeSegment(
                name=segment.name,
                upstream_stage=segment.upstream_stage,
                downstream_stage=segment.downstream_stage,
                wall_temperature_C=max(0.0, float(raw_temperature)),
                length_m=segment.length_m,
                inner_diameter_m=segment.inner_diameter_m,
            ))
        self.pipe_segments = updated

    def route(self, evap_flux: EvaporationFlux, melt: MeltState):
        """
        Route all evaporated species through the train.

        For each species, walk through stages 0→6.  At each stage,
        calculate condensation fraction η.  Whatever condenses is
        added to that stage's collected_kg; the remainder passes
        to the next stage.

        O2 terminal storage is handled by the simulator atom ledger.  Stage
        collection dictionaries are UI projections and are updated only after
        the simulator commits the matching ledger transition.
        """
        remaining_by_species = {}
        condensed_by_stage_species: Dict[int, Dict[str, float]] = {}
        wall_deposit_by_species: Dict[str, float] = {}
        wall_deposit_by_segment_species: Dict[str, Dict[str, float]] = {}
        impurity_by_stage_species: Dict[int, Dict[str, float]] = {}
        knudsen_diagnostic = self._enforce_knudsen_regime()
        diagnostic = cold_spot_diagnostic(
            self.pipe_segments,
            evap_flux.species_kg_hr,
            margin_C=self.cold_spot_margin_C,
        )
        self.last_cold_spot_diagnostic = diagnostic
        self.cold_spot_history.append(diagnostic)
        cold_spot_warnings = tuple(diagnostic.get('warnings', ()))
        if self.operating_history:
            self.operating_history[-1]['cold_spot_warning_count'] = len(
                cold_spot_warnings)
            self.operating_history[-1]['cold_spot_warnings'] = cold_spot_warnings

        for species, rate_kg_hr in evap_flux.species_kg_hr.items():
            remaining_kg = rate_kg_hr  # Mass still in vapor phase
            self._record_runtime_wall_fraction(species, 0.0, {})

            T_cond = _species_condensation_temperature_C(species)
            hkl_condensed_by_stage: Dict[int, float] = {}
            remaining_after_stage: Dict[int, float] = {}

            for stage in self.train.stages:
                if remaining_kg <= 1e-15:
                    break
                if _cr_stage_isolation_blocks(stage, species):
                    continue
                # Calculate band-aware H-K-L deposition efficiency [COND-2]
                eta = self._condensation_efficiency(
                    stage=stage,
                    species=species,
                    T_cond_C=T_cond,
                    residence_s=self.residence_time_s.get(
                        stage.stage_number, 1.0),
                    alpha_s=_stage_alpha_s(stage, species),
                )

                condensed_kg = remaining_kg * eta
                if condensed_kg > 1e-15:
                    hkl_condensed_by_stage[stage.stage_number] = (
                        hkl_condensed_by_stage.get(stage.stage_number, 0.0)
                        + condensed_kg)

                remaining_kg -= condensed_kg
                remaining_after_stage[stage.stage_number] = max(
                    0.0, remaining_kg)

            hkl_condensed_total_kg = sum(hkl_condensed_by_stage.values())
            segment_supply = self._segment_supply_by_name(
                rate_kg_hr,
                remaining_after_stage,
            )
            wall_hkl_by_segment = self._wall_deposit_candidates_by_segment_kg(
                species=species,
                rate_kg_hr=rate_kg_hr,
                T_cond_C=T_cond,
                melt_temperature_C=float(getattr(melt, 'temperature_C', T_cond)),
                supply_by_segment_kg=segment_supply,
            )
            wall_hkl_kg = sum(wall_hkl_by_segment.values())
            hkl_sink_total_kg = hkl_condensed_total_kg + wall_hkl_kg
            capture_budget_kg = _pressure_isolated_capture_budget_kg(
                species,
                rate_kg_hr,
                self.train.stages,
                self.residence_time_s,
            )
            if hkl_sink_total_kg <= 1e-15:
                capture_budget_kg = 0.0
            elif capture_budget_kg > 0.0:
                wall_deposit_kg = capture_budget_kg * (
                    wall_hkl_kg / hkl_sink_total_kg
                )
                if wall_deposit_kg > 1e-15:
                    wall_deposit_by_species[species] = (
                        wall_deposit_by_species.get(species, 0.0)
                        + wall_deposit_kg
                    )
                    segment_deposits = _allocate_total_by_weights(
                        wall_deposit_kg,
                        wall_hkl_by_segment,
                    )
                    for segment_name, segment_kg in segment_deposits.items():
                        if segment_kg <= 1e-15:
                            continue
                        segment_species = (
                            wall_deposit_by_segment_species.setdefault(
                                segment_name, {}))
                        segment_species[species] = (
                            segment_species.get(species, 0.0) + segment_kg)
                wall_fraction = (
                    wall_deposit_kg / capture_budget_kg
                    if capture_budget_kg > 0.0 else 0.0
                )
                segment_fractions = _wall_segment_account_fractions(
                    wall_deposit_by_segment_species,
                    species,
                    wall_deposit_kg,
                    self.pipe_segments,
                )
                self._record_runtime_wall_fraction(
                    species, wall_fraction, segment_fractions)

                baffle_budget_kg = max(0.0, capture_budget_kg - wall_deposit_kg)
                if hkl_condensed_total_kg > 1e-15 and baffle_budget_kg > 0.0:
                    scale = baffle_budget_kg / hkl_condensed_total_kg
                    for stage_number, hkl_stage_kg in hkl_condensed_by_stage.items():
                        condensed_kg = hkl_stage_kg * scale
                        if condensed_kg <= 1e-15:
                            continue
                        stage_species = condensed_by_stage_species.setdefault(
                            stage_number, {})
                        stage_species[species] = (
                            stage_species.get(species, 0.0) + condensed_kg)
                        if not is_designated_for_stage(species, stage_number):
                            stage_impurity = (
                                impurity_by_stage_species.setdefault(
                                    stage_number, {}))
                            stage_impurity[species] = (
                                stage_impurity.get(species, 0.0)
                                + condensed_kg)

            remaining_by_species[species] = max(
                0.0, rate_kg_hr - capture_budget_kg)

        return CondensationRouteResult(
            remaining_by_species=remaining_by_species,
            condensed_by_stage_species=condensed_by_stage_species,
            wall_deposit_by_species=wall_deposit_by_species,
            wall_deposit_by_segment_species=wall_deposit_by_segment_species,
            impurity_by_stage_species=impurity_by_stage_species,
            cold_spot_warnings=cold_spot_warnings,
            knudsen_regime_diagnostic=knudsen_diagnostic,
        )

    def _current_knudsen_diagnostic(self) -> dict[str, Any]:
        diagnostic = knudsen_regime_diagnostic(
            overhead_pressure_mbar=self.overhead_pressure_mbar,
            gas_temperature_C=self.gas_temperature_C,
            pipe_diameter_m=self.pipe_diameter_m,
            pipe_segments=self.pipe_segments,
            regime_factor=self.regime_factor,
        )
        if self._knudsen_policy_configured:
            if not self._viscous_flow_required:
                relaxed = dict(diagnostic)
                relaxed['status'] = 'ok'
                relaxed['reason'] = ''
                relaxed['warnings'] = []
                relaxed['viscous_flow_required'] = False
                return relaxed
            diagnostic['viscous_flow_required'] = True
            return diagnostic
        unconfigured = dict(diagnostic)
        unconfigured['status'] = 'unconfigured'
        unconfigured['reason'] = 'knudsen_policy_unconfigured'
        return unconfigured

    def _enforce_knudsen_regime(self) -> dict[str, Any]:
        diagnostic = self._current_knudsen_diagnostic()
        self.last_knudsen_regime_diagnostic = diagnostic
        if not self._knudsen_policy_configured:
            return diagnostic
        if diagnostic.get('status') == 'refused':
            raise KnudsenRegimeRefusal(diagnostic)
        warnings = tuple(diagnostic.get('warnings', ()))
        if warnings and self.operating_history:
            self.operating_history[-1]['knudsen_warnings'] = warnings
            self.operating_history[-1]['knudsen_regime_diagnostic'] = dict(
                diagnostic)
        return diagnostic

    def _wall_deposit_candidate_kg(
        self,
        *,
        species: str,
        rate_kg_hr: float,
        T_cond_C: float,
        melt_temperature_C: float,
    ) -> float:
        return self._wall_deposit_candidate_for_surface_kg(
            species=species,
            rate_kg_hr=rate_kg_hr,
            T_cond_C=T_cond_C,
            melt_temperature_C=melt_temperature_C,
            wall_temperature_C=self.wall_temperature_C,
            surface_area_m2=self.wall_surface_area_m2,
        )

    def _wall_deposit_candidates_by_segment_kg(
        self,
        *,
        species: str,
        rate_kg_hr: float,
        T_cond_C: float,
        melt_temperature_C: float,
        supply_by_segment_kg: Mapping[str, float],
    ) -> Dict[str, float]:
        if rate_kg_hr <= 0.0 or not self.pipe_segments:
            return {}
        # Autoreview r7 P2 (2026-05-27): the equal-temperature fast
        # path used to allocate the wall-deposit candidate across
        # ``self.pipe_segments`` -- every segment, including segments
        # downstream of the species' designated condenser stage that
        # cannot physically see this species' vapor.  The
        # mixed-temperature branch already restricts to upstream-only
        # candidates via ``_mixed_temperature_wall_candidate_segments``
        # AND caps by per-segment supply; the equal-T branch must use
        # the same gate or it credits species to wall-deposit accounts
        # they cannot physically reach (mass balance still closes, but
        # the per-segment ledger and the F1 stage-routing-purity
        # report both become non-stage-honest).
        reachable_segments = self._mixed_temperature_wall_candidate_segments(species)
        if not reachable_segments:
            return {}
        temperatures = {
            float(segment.wall_temperature_C)
            for segment in reachable_segments
        }
        if len(temperatures) == 1:
            wall_temperature_C = next(iter(temperatures))
            # Per-segment surface area cap on the equal-T candidate so
            # the total is bounded by what the reachable segments can
            # physically collect, not the original full-train rate.
            reachable_surface_m2 = sum(
                max(0.0, float(segment.surface_area_m2))
                for segment in reachable_segments
            )
            total_candidate = self._wall_deposit_candidate_for_surface_kg(
                species=species,
                rate_kg_hr=rate_kg_hr,
                T_cond_C=T_cond_C,
                melt_temperature_C=melt_temperature_C,
                wall_temperature_C=wall_temperature_C,
                surface_area_m2=reachable_surface_m2,
            )
            candidates = _allocate_total_by_weights(
                total_candidate,
                {
                    segment.name: segment.surface_area_m2
                    for segment in reachable_segments
                },
            )
            # Per-segment supply cap: mirror the mixed-temperature
            # branch's invariant that no segment claims more than its
            # available vapor supply after upstream condensation.
            for segment in reachable_segments:
                supply_kg = min(
                    max(0.0, float(supply_by_segment_kg.get(
                        segment.name, rate_kg_hr))),
                    rate_kg_hr,
                )
                if segment.name in candidates:
                    candidates[segment.name] = min(
                        candidates[segment.name], supply_kg)
            return candidates

        pipe_segments = reachable_segments
        if not pipe_segments:
            return {}
        candidates: Dict[str, float] = {}
        for segment in pipe_segments:
            supply_kg = min(
                max(0.0, float(supply_by_segment_kg.get(
                    segment.name, rate_kg_hr))),
                rate_kg_hr,
            )
            # Autoreview r4 P2 (2026-05-27): pass ``supply_kg`` as the
            # rate budget for THIS segment, not the original full-train
            # ``rate_kg_hr``.  After an upstream condenser has removed
            # most vapor, the downstream segment sees only ``supply_kg``,
            # and the candidate's ``min(rate_kg_hr, rate_kg_hr * eta)``
            # cap previously over-stated downstream candidates which
            # then survived the per-segment ``min(.., supply_kg)`` floor
            # at full ``supply_kg`` for any eta >= supply_kg/rate_kg_hr.
            # That inflated the wall-deposit weights and diverted
            # capture budget from designated condenser stages into
            # wall-deposit accounts whenever per-segment temperatures
            # differed.
            candidates[segment.name] = self._wall_deposit_candidate_for_surface_kg(
                species=species,
                rate_kg_hr=supply_kg,
                T_cond_C=T_cond_C,
                melt_temperature_C=melt_temperature_C,
                wall_temperature_C=segment.wall_temperature_C,
                surface_area_m2=segment.surface_area_m2,
            )
            # ``rate_kg_hr=supply_kg`` already caps the candidate at
            # ``supply_kg``; the explicit floor stays as defence in
            # depth in case the candidate function loosens its return
            # bound later.
            candidates[segment.name] = min(candidates[segment.name], supply_kg)
        total = sum(candidates.values())
        if total > rate_kg_hr > 0.0:
            scale = rate_kg_hr / total
            candidates = {
                name: value * scale
                for name, value in candidates.items()
            }
        return candidates

    def _mixed_temperature_wall_candidate_segments(
        self,
        species: str,
    ) -> list[PipeSegment]:
        target_stage_number = designated_stage_number(species)
        if target_stage_number is None:
            return []
        segments: list[PipeSegment] = []
        for segment in self.pipe_segments:
            downstream_number = _segment_stage_number(segment.downstream_stage)
            if downstream_number is None:
                continue
            if downstream_number <= target_stage_number:
                segments.append(segment)
        return segments

    def _wall_deposit_candidate_for_surface_kg(
        self,
        *,
        species: str,
        rate_kg_hr: float,
        T_cond_C: float,
        melt_temperature_C: float,
        wall_temperature_C: float,
        surface_area_m2: float,
    ) -> float:
        if rate_kg_hr <= 0.0 or surface_area_m2 <= 0.0:
            return 0.0
        alpha_s = _wall_alpha_s(species)
        if alpha_s <= 0.0:
            return 0.0

        P_local_pa = _local_wall_species_pressure_pa(
            species, melt_temperature_C, T_cond_C,
        )
        if P_local_pa <= 0.0:
            return 0.0

        T_ref_K = max(T_cond_C + 273.15, 1.0)
        reference_flux = _hkl_impingement_flux_mol_m2_s(
            species, P_local_pa, T_ref_K,
        )
        if reference_flux <= 0.0:
            return 0.0

        T_wall_K = max(float(wall_temperature_C) + 273.15, 1.0)
        # Post-F3 follow-on (viscous-regime mass-transfer): in viscous
        # regime HKL is unphysical (free-molecular limit); the Sherwood-
        # number boundary-layer term carries the deposition. Combine via
        # regime_factor weighting so that HKL dominates at high Kn and
        # mass-transfer dominates at low Kn, with a smooth transition.
        # T_surface_K (wall) feeds P_sat; T_gas_K (bulk) feeds the
        # ideal-gas denominator in the boundary-layer flux per autoreview
        # pre-0.5.1 P2 (2026-05-27).
        T_gas_K = max(float(self.gas_temperature_C) + 273.15, 1.0)
        flux = _combined_deposition_flux_mol_m2_s(
            species, P_local_pa, T_wall_K, alpha_s, self.regime_factor,
            pipe_diameter_m=self.pipe_diameter_m,
            T_gas_K=T_gas_K,
        )
        if flux <= 0.0:
            return 0.0

        residence_s = float(self.residence_time_s.get(0, 0.5))
        rate_s_inv = (
            flux / reference_flux
        ) * max(0.0, float(surface_area_m2))
        eta = 1.0 - math.exp(-max(0.0, residence_s * rate_s_inv))
        return max(0.0, min(rate_kg_hr, rate_kg_hr * eta))

    def _segment_supply_by_name(
        self,
        rate_kg_hr: float,
        remaining_after_stage: Mapping[int, float],
    ) -> Dict[str, float]:
        supply: Dict[str, float] = {}
        for segment in self.pipe_segments:
            upstream_number = _segment_stage_number(segment.upstream_stage)
            if upstream_number is None:
                supply[segment.name] = max(0.0, float(rate_kg_hr))
            else:
                prior_numbers = [
                    stage_number
                    for stage_number in remaining_after_stage
                    if stage_number <= upstream_number
                ]
                default_supply = (
                    remaining_after_stage[max(prior_numbers)]
                    if prior_numbers else rate_kg_hr
                )
                supply[segment.name] = max(
                    0.0,
                    min(
                        float(rate_kg_hr),
                        float(remaining_after_stage.get(
                            upstream_number, default_supply)),
                    ),
                )
        return supply

    def _record_runtime_wall_fraction(
        self,
        species: str,
        fraction: float,
        segment_fractions: Mapping[str, float] | None = None,
    ) -> None:
        data = _mutable_species_vapor_data(self.vapor_pressure_data, species)
        if data is None:
            return
        data[WALL_DEPOSIT_FRACTION_KEY] = max(0.0, min(1.0, float(fraction)))
        data[WALL_DEPOSIT_ACCOUNT_KEY] = WALL_DEPOSIT_ACCOUNT
        data[WALL_DEPOSIT_SEGMENT_FRACTIONS_KEY] = {
            str(account): max(0.0, min(1.0, float(segment_fraction)))
            for account, segment_fraction in dict(
                segment_fractions or {}).items()
            if float(segment_fraction) > 0.0
        }

    def _condensation_efficiency(
        self,
        *,
        stage: CondensationStage,
        species: str,
        T_cond_C: float,
        residence_s: float,
        alpha_s: float,
    ) -> float:
        """
        Condensation efficiency for one species in one stage.

        Hertz-Knudsen-Langmuir surface deposition:

            J = alpha_s * max(0, P_local - P_sat(T_surface))
                / sqrt(2*pi*m*k*T_surface) * regime_factor(Kn)

        Chunk A keeps the existing stage residence-time surrogate for
        geometry and integrates the H-K-L driving force across the actual
        stage T-band. Chunk C replaces the constant regime factor with
        pressure/Knudsen coupling.
        """
        if residence_s <= 0.0 or alpha_s <= 0.0:
            return 0.0

        P_local_pa = _local_species_pressure_pa(species, T_cond_C)
        if P_local_pa <= 0.0:
            return 0.0

        T_ref_K = max(T_cond_C + 273.15, 1.0)
        reference_flux = _hkl_impingement_flux_mol_m2_s(
            species, P_local_pa, T_ref_K,
        )
        if reference_flux <= 0.0:
            return 0.0

        lo_C, hi_C = _stage_temp_band_C(stage)
        if hi_C < lo_C:
            lo_C, hi_C = hi_C, lo_C

        band_flux_fraction = 0.0
        width_C = hi_C - lo_C
        for sample in range(HKL_BAND_SAMPLES):
            if width_C <= 0.0:
                T_surface_C = lo_C
            else:
                T_surface_C = (
                    lo_C + width_C * (sample + 0.5) / HKL_BAND_SAMPLES
                )
            T_surface_K = max(T_surface_C + 273.15, 1.0)
            # Post-F3 follow-on (viscous-regime mass-transfer): same
            # combined-flux blend as the wall-deposit candidate path so
            # the stage-condensation band integration honors viscous
            # boundary-layer physics where HKL is unphysical.
            # T_surface_K (stage T-band sample) drives P_sat; T_gas_K
            # (bulk gas) drives the ideal-gas denominator per
            # autoreview pre-0.5.1 P2.
            T_gas_K = max(float(self.gas_temperature_C) + 273.15, 1.0)
            flux = _combined_deposition_flux_mol_m2_s(
                species, P_local_pa, T_surface_K, alpha_s, self.regime_factor,
                pipe_diameter_m=self.pipe_diameter_m,
                T_gas_K=T_gas_K,
            )
            band_flux_fraction += flux / reference_flux
        band_flux_fraction /= HKL_BAND_SAMPLES

        rate_s_inv = max(0.0, band_flux_fraction)
        eta = 1.0 - math.exp(-residence_s * rate_s_inv)
        return max(0.0, min(1.0, eta))


def _species_vapor_data(species: str) -> Mapping[str, Any]:
    for family in ('metals', 'oxide_vapors'):
        data = (VAPOR_PRESSURE_DATA.get(family, {}) or {}).get(species, {})
        if data and isinstance(data, Mapping):
            return data
    return {}


def _species_condensation_temperature_C(species: str) -> float:
    if species in CONDENSATION_TEMPS_C:
        return float(CONDENSATION_TEMPS_C[species])
    data = _species_vapor_data(species)
    try:
        return float(data.get('condensation_T_C_at_1mbar', 500.0))
    except (TypeError, ValueError):
        return 500.0


def _stage_material_config(stage: CondensationStage) -> Mapping[str, Any]:
    stages = MATERIALS_DATA.get('stages', {}) or {}
    if not isinstance(stages, Mapping):
        return {}
    config = stages.get(stage.stage_number, stages.get(str(stage.stage_number), {}))
    return config if isinstance(config, Mapping) else {}


def _stage_temp_band_C(stage: CondensationStage) -> tuple[float, float]:
    lo_C, hi_C = stage.temp_range_C
    return float(lo_C), float(hi_C)


def _stage_alpha_s(stage: CondensationStage, species: str) -> float:
    config = _stage_material_config(stage)
    alpha_by_species = config.get('alpha_s_by_species', {}) or {}
    value = (
        alpha_by_species.get(species)
        if isinstance(alpha_by_species, Mapping)
        else None
    )
    if value is None:
        value = STICKING_COEFF.get(species, 0.8)
    try:
        alpha_s = float(value)
    except (TypeError, ValueError):
        alpha_s = 0.0
    if not math.isfinite(alpha_s):
        return 0.0
    return max(0.0, min(1.0, alpha_s))


def _wall_material_config() -> Mapping[str, Any]:
    surfaces = MATERIALS_DATA.get('wall_surfaces', {}) or {}
    if not isinstance(surfaces, Mapping):
        return {}
    config = surfaces.get('interstage_duct', {}) or {}
    return config if isinstance(config, Mapping) else {}


def _wall_alpha_s(species: str) -> float:
    config = _wall_material_config()
    alpha_by_species = config.get('alpha_s_by_species', {}) or {}
    value = (
        alpha_by_species.get(species)
        if isinstance(alpha_by_species, Mapping)
        else None
    )
    if value is None:
        liner_material = config.get('liner_material')
        material_config = _liner_material_config(str(liner_material or ''))
        material_alpha = material_config.get('alpha_s_by_species', {}) or {}
        if isinstance(material_alpha, Mapping):
            value = material_alpha.get(species)
    if value is None:
        value = STICKING_COEFF.get(species, 0.8)
    try:
        alpha_s = float(value)
    except (TypeError, ValueError):
        alpha_s = 0.0
    if not math.isfinite(alpha_s):
        return 0.0
    return max(0.0, min(1.0, alpha_s))


def _liner_material_config(material: str) -> Mapping[str, Any]:
    materials = MATERIALS_DATA.get('liner_materials', {}) or {}
    if not isinstance(materials, Mapping):
        return {}
    config = materials.get(material, {}) or {}
    return config if isinstance(config, Mapping) else {}


def _default_pipe_surface_area_m2() -> float:
    from simulator.equipment import PipeSpec

    pipe = PipeSpec()
    if pipe.surface_area_m2 > 0.0:
        return float(pipe.surface_area_m2)
    return math.pi * float(pipe.diameter_m) * float(pipe.length_m)


def _mutable_species_vapor_data(
    vapor_pressure_data: MutableMapping[str, Any] | None,
    species: str,
) -> MutableMapping[str, Any] | None:
    if vapor_pressure_data is None:
        return None
    for family in ('metals', 'oxide_vapors'):
        family_data = vapor_pressure_data.get(family, {})
        if not isinstance(family_data, MutableMapping):
            continue
        data = family_data.get(species)
        if isinstance(data, MutableMapping):
            return data
    return None


def _antoine_psat_pa(species: str, T_K: float) -> float | None:
    data = _species_vapor_data(species)
    antoine = data.get('antoine', {}) if isinstance(data, Mapping) else {}
    if not isinstance(antoine, Mapping):
        return None
    try:
        A = float(antoine.get('A', 0.0))
        B = float(antoine.get('B', 0.0))
        C = float(antoine.get('C', 0.0))
        T_K = float(T_K)
    except (TypeError, ValueError):
        return None
    if not (A > 0.0 and math.isfinite(T_K) and T_K + C > 0.0):
        return None
    # Same Antoine form used by equilibrium.py and builtin vapor pressure.
    return 10.0 ** (A - B / (T_K + C))


def _local_species_pressure_pa(species: str, T_cond_C: float) -> float:
    P_local_pa = _antoine_psat_pa(species, T_cond_C + 273.15)
    if P_local_pa is not None and P_local_pa > 0.0:
        return P_local_pa
    # Existing condensation temperatures are documented at ~1 mbar.
    return 100.0


def _local_wall_species_pressure_pa(
    species: str,
    melt_temperature_C: float,
    fallback_T_cond_C: float,
) -> float:
    P_source_pa = _antoine_psat_pa(species, melt_temperature_C + 273.15)
    if P_source_pa is not None and P_source_pa > 0.0:
        return P_source_pa
    return _local_species_pressure_pa(species, fallback_T_cond_C)


def _molecular_mass_kg_per_molecule(species: str) -> float:
    data = _species_vapor_data(species)
    value = data.get('molar_mass_g_mol') if isinstance(data, Mapping) else None
    if value is None:
        value = MOLAR_MASS.get(species)
    try:
        molar_mass_g_mol = float(value)
    except (TypeError, ValueError):
        molar_mass_g_mol = 50.0
    if not math.isfinite(molar_mass_g_mol) or molar_mass_g_mol <= 0.0:
        molar_mass_g_mol = 50.0
    return (molar_mass_g_mol / 1000.0) / AVOGADRO_MOL


def _hkl_impingement_flux_mol_m2_s(
    species: str,
    pressure_pa: float,
    T_K: float,
) -> float:
    if pressure_pa <= 0.0 or T_K <= 0.0:
        return 0.0
    molecule_kg = _molecular_mass_kg_per_molecule(species)
    denominator = math.sqrt(
        2.0 * math.pi * molecule_kg * BOLTZMANN_CONSTANT_J_K * T_K
    )
    if denominator <= 0.0:
        return 0.0
    return pressure_pa / denominator / AVOGADRO_MOL


def _hkl_surface_deposition_flux_mol_m2_s(
    species: str,
    P_local_pa: float,
    T_surface_K: float,
    alpha_s: float,
    regime_factor: float = 1.0,
) -> float:
    P_sat_pa = _antoine_psat_pa(species, T_surface_K)
    if P_sat_pa is None:
        return 0.0
    driving_pressure_pa = max(0.0, P_local_pa - P_sat_pa)
    if driving_pressure_pa <= 0.0:
        return 0.0
    return (
        alpha_s
        * max(0.0, min(1.0, float(regime_factor)))
        * _hkl_impingement_flux_mol_m2_s(
            species, driving_pressure_pa, T_surface_K,
        )
    )


def _viscous_mass_transfer_flux_mol_m2_s(
    species: str,
    P_local_pa: float,
    T_surface_K: float,
    pipe_diameter_m: float = DEFAULT_PIPE_DIAMETER_M,
    sherwood: float = DEFAULT_SHERWOOD_LAMINAR,
    diffusion_coefficient_m2_s: float = DEFAULT_BINARY_DIFFUSION_M2_S,
    T_gas_K: float | None = None,
) -> float:
    """Boundary-layer mass-transfer flux of ``species`` to a cooled wall.

    Viscous-regime companion to ``_hkl_surface_deposition_flux_mol_m2_s``.
    Uses the Bird/Stewart/Lightfoot Sherwood-number correlation for
    laminar pipe flow with constant wall concentration (Sh = 3.66
    asymptotic). The mass-transfer coefficient is

        k_c = Sh * D_AB / L_pipe    [m/s]

    and the deposition flux follows from the local-vs-saturated
    concentration gradient:

        J_mass = k_c * (P_local - P_sat(T_wall)) / (R * T_gas)   [mol/m^2/s]

    This is the boundary-layer-limited deposition rate. It dominates in
    the viscous regime (Kn << 0.01) where the HKL free-molecular
    impingement flux is unphysical; the two are blended via the
    Knudsen ``regime_factor`` in the callers
    (`_wall_deposit_candidate_for_surface_kg` and
    `_condensation_efficiency`).

    Returns 0 when there is no driving force (P_local <= P_sat at
    the wall surface) or when the pipe geometry is invalid.
    """
    if pipe_diameter_m <= 0.0 or sherwood <= 0.0:
        return 0.0
    if diffusion_coefficient_m2_s <= 0.0:
        return 0.0
    P_sat_pa = _antoine_psat_pa(species, T_surface_K)
    if P_sat_pa is None:
        return 0.0
    driving_pressure_pa = max(0.0, P_local_pa - P_sat_pa)
    if driving_pressure_pa <= 0.0:
        return 0.0
    # Autoreview pre-0.5.1 P2 (2026-05-27): the ideal-gas conversion
    # ``P / (R * T)`` MUST use the BULK GAS temperature, not the wall
    # surface temperature. The two diverge in cold-wall scenarios
    # (wall at 1050 C, bulk gas at 1700 C); using T_surface here
    # overstated the boundary-layer flux by ``T_gas/T_wall`` whenever
    # the wall was cold. ``T_surface_K`` stays in P_sat (where it
    # belongs: saturation pressure is a function of the cold-surface
    # T). Callers that don't supply T_gas_K still get the old
    # behavior (T_gas := T_surface) so the change is backward-safe.
    effective_T_gas_K = max(
        float(T_gas_K) if T_gas_K is not None else T_surface_K, 1.0)
    k_c_m_s = sherwood * diffusion_coefficient_m2_s / pipe_diameter_m
    return (
        k_c_m_s * driving_pressure_pa
        / (GAS_CONSTANT_J_MOL_K * effective_T_gas_K)
    )


def _combined_deposition_flux_mol_m2_s(
    species: str,
    P_local_pa: float,
    T_surface_K: float,
    alpha_s: float,
    regime_factor: float,
    pipe_diameter_m: float = DEFAULT_PIPE_DIAMETER_M,
    T_gas_K: float | None = None,
) -> float:
    """Combine HKL + viscous-mass-transfer fluxes via ``regime_factor``.

    Post-F3 follow-on per tickler §5. ``regime_factor`` is
    ``Kn/(Kn + 0.01)``, going to 0 in viscous regime and 1 in
    free-molecular regime; we blend the two physics with that weight so
    that:

      * Kn -> 0 (viscous):       J ~= J_mass_transfer
      * Kn ~ 0.01 (transition):  J = ~0.5 * J_HKL + ~0.5 * J_mass
      * Kn >> 0.01 (free-mol):   J ~= J_HKL

    The HKL term carries ``alpha_s`` (sticking coefficient); the
    boundary-layer term does not (capture at the boundary is
    geometry-limited, not sticking-probability-limited).
    """
    weight_hkl = max(0.0, min(1.0, float(regime_factor)))
    weight_mt = max(0.0, 1.0 - weight_hkl)
    if weight_hkl <= 0.0 and weight_mt <= 0.0:
        return 0.0

    hkl = (
        _hkl_surface_deposition_flux_mol_m2_s(
            species, P_local_pa, T_surface_K, alpha_s, regime_factor=1.0,
        )
        if weight_hkl > 0.0
        else 0.0
    )
    mt = (
        _viscous_mass_transfer_flux_mol_m2_s(
            species, P_local_pa, T_surface_K,
            pipe_diameter_m=pipe_diameter_m,
            T_gas_K=T_gas_K,
        )
        if weight_mt > 0.0
        else 0.0
    )
    return weight_hkl * hkl + weight_mt * mt


def _mean_free_path_m(
    pressure_pa: float,
    T_K: float,
    molecular_diameter_m: float = N2_COLLISION_DIAMETER_M,
) -> float:
    if pressure_pa <= 0.0:
        return math.inf
    if T_K <= 0.0 or molecular_diameter_m <= 0.0:
        return 0.0
    denominator = (
        math.sqrt(2.0)
        * math.pi
        * molecular_diameter_m ** 2
        * pressure_pa
    )
    if denominator <= 0.0:
        return math.inf
    return BOLTZMANN_CONSTANT_J_K * T_K / denominator


def _knudsen_number(
    pressure_pa: float,
    T_K: float,
    characteristic_length_m: float,
) -> float:
    if characteristic_length_m <= 0.0:
        return math.inf
    return _mean_free_path_m(pressure_pa, T_K) / characteristic_length_m


def _knudsen_regime_factor(knudsen_number: float) -> float:
    if not math.isfinite(knudsen_number):
        return 1.0
    if knudsen_number <= 0.0:
        return 0.0
    factor = knudsen_number / (knudsen_number + CONTINUUM_BUFFER_KN)
    return max(0.0, min(1.0, factor))


def classify_knudsen_regime(knudsen_number: float) -> KnudsenRegime:
    if not math.isfinite(knudsen_number):
        return KnudsenRegime.FREE_MOLECULAR
    if knudsen_number < VISCOUS_KNUDSEN_MAX:
        return KnudsenRegime.VISCOUS
    if knudsen_number < FREE_MOLECULAR_KNUDSEN_MIN:
        return KnudsenRegime.TRANSITIONAL
    return KnudsenRegime.FREE_MOLECULAR


def knudsen_regime_diagnostic(
    *,
    overhead_pressure_mbar: float,
    gas_temperature_C: float,
    pipe_diameter_m: float,
    pipe_segments: list[PipeSegment] | None = None,
    regime_factor: float | None = None,
) -> dict[str, Any]:
    pressure_pa = max(0.0, float(overhead_pressure_mbar)) * 100.0
    gas_temperature_K = max(float(gas_temperature_C) + 273.15, 1.0)
    fallback_diameter_m = max(1.0e-9, float(pipe_diameter_m))
    mean_free_path_m = _mean_free_path_m(pressure_pa, gas_temperature_K)

    segments: list[dict[str, Any]] = []
    source_segments = list(pipe_segments or ())
    if not source_segments:
        source_segments = [
            PipeSegment(
                name='default_pipe',
                upstream_stage='',
                downstream_stage='',
                wall_temperature_C=float(gas_temperature_C),
                length_m=0.0,
                inner_diameter_m=fallback_diameter_m,
            )
        ]

    worst_regime = KnudsenRegime.VISCOUS
    severity = {
        KnudsenRegime.VISCOUS: 0,
        KnudsenRegime.TRANSITIONAL: 1,
        KnudsenRegime.FREE_MOLECULAR: 2,
    }
    for segment in source_segments:
        diameter_m = max(
            1.0e-9,
            float(getattr(segment, 'inner_diameter_m', fallback_diameter_m)),
        )
        knudsen_number = _knudsen_number(
            pressure_pa, gas_temperature_K, diameter_m)
        regime = classify_knudsen_regime(knudsen_number)
        if severity[regime] > severity[worst_regime]:
            worst_regime = regime
        segments.append({
            'name': str(getattr(segment, 'name', 'default_pipe')),
            'knudsen_number': _finite_or_none(knudsen_number),
            'regime': regime.value,
            'characteristic_length_m': diameter_m,
        })

    global_knudsen_number = _knudsen_number(
        pressure_pa, gas_temperature_K, fallback_diameter_m)
    global_regime = classify_knudsen_regime(global_knudsen_number)
    if severity[global_regime] > severity[worst_regime]:
        worst_regime = global_regime

    warnings: list[str] = []
    status = 'ok'
    reason = ''
    if worst_regime is KnudsenRegime.FREE_MOLECULAR:
        status = 'refused'
        reason = KNUDSEN_REFUSAL_REASON
        warnings.append(
            'Knudsen number is outside viscous-flow validity; '
            'condensation routing refused.'
        )
    elif worst_regime is KnudsenRegime.TRANSITIONAL:
        status = 'warning'
        reason = KNUDSEN_TRANSITION_REASON
        warnings.append(
            'Knudsen number is transitional; surface deposition carries '
            'extra uncertainty and uses the continuity correction.'
        )

    return {
        'status': status,
        'reason': reason,
        'regime': worst_regime.value,
        'knudsen_number': _finite_or_none(global_knudsen_number),
        'mean_free_path_m': _finite_or_none(mean_free_path_m),
        'overhead_pressure_mbar': max(0.0, float(overhead_pressure_mbar)),
        'gas_temperature_C': float(gas_temperature_C),
        'pipe_diameter_m': fallback_diameter_m,
        'regime_factor': (
            _knudsen_regime_factor(global_knudsen_number)
            if regime_factor is None else float(regime_factor)
        ),
        'segments': segments,
        'warnings': warnings,
    }


def _finite_or_none(value: float) -> float | None:
    value = float(value)
    return value if math.isfinite(value) else None


def _campaign_requires_viscous_flow(campaign_name: str | None) -> bool:
    if campaign_name is None:
        return True
    name = str(campaign_name)
    if not name:
        return True
    return name in {
        'C2A',
        'C2A_continuous',
        'C2A_STAGED',
        'C2A_staged',
    }


def _pressure_isolated_capture_budget_kg(
    species: str,
    rate_kg_hr: float,
    stages: list[CondensationStage],
    residence_time_s: Mapping[int, float],
) -> float:
    """Hold total vapor removal fixed until Chunk C pressure coupling lands."""

    remaining_kg = max(0.0, rate_kg_hr)
    T_cond_C = _species_condensation_temperature_C(species)
    alpha_s = STICKING_COEFF.get(species, 0.8)
    for stage in stages:
        if remaining_kg <= 1e-15:
            break
        if _cr_stage_isolation_blocks(stage, species):
            continue
        eta = _pressure_isolated_stage_efficiency(
            stage,
            T_cond_C,
            float(residence_time_s.get(stage.stage_number, 1.0)),
            alpha_s,
        )
        remaining_kg -= remaining_kg * eta
    return max(0.0, min(rate_kg_hr, rate_kg_hr - remaining_kg))


def _pressure_isolated_stage_efficiency(
    stage: CondensationStage,
    T_cond_C: float,
    residence_s: float,
    alpha_s: float,
) -> float:
    lo_C, hi_C = stage.temp_range_C
    T_stage_C = (float(lo_C) + float(hi_C)) / 2.0
    if T_stage_C >= T_cond_C:
        return 0.0
    delta_T = T_cond_C - T_stage_C
    tau_s = 1.0 / (
        alpha_s * max(delta_T / max(T_cond_C, 1.0), 0.01)
    )
    eta = 1.0 - math.exp(-residence_s / tau_s)
    return max(0.0, min(1.0, eta))


def _cr_stage_isolation_blocks(stage: CondensationStage, species: str) -> bool:
    chromium_stage = 'CrO2' in stage.target_species
    if species in {'Cr', 'CrO2'}:
        return not chromium_stage
    return chromium_stage


def _allocate_total_by_weights(
    total: float,
    weights: Mapping[str, float],
) -> Dict[str, float]:
    if total <= 0.0:
        return {}
    positive = [
        (str(name), float(weight))
        for name, weight in weights.items()
        if float(weight) > 0.0
    ]
    weight_total = sum(weight for _, weight in positive)
    if weight_total <= 0.0:
        return {}
    allocated: Dict[str, float] = {}
    running = 0.0
    for name, weight in positive[:-1]:
        value = float(total) * weight / weight_total
        allocated[name] = value
        running += value
    last_name = positive[-1][0]
    allocated[last_name] = max(0.0, float(total) - running)
    return allocated


def _wall_segment_account_fractions(
    wall_deposit_by_segment_species: Mapping[str, Mapping[str, float]],
    species: str,
    wall_deposit_kg: float,
    pipe_segments: list[PipeSegment],
) -> Dict[str, float]:
    if wall_deposit_kg <= 0.0:
        return {}
    segment_by_name = {segment.name: segment for segment in pipe_segments}
    fractions: Dict[str, float] = {}
    for segment_name, species_kg in wall_deposit_by_segment_species.items():
        segment_kg = float(species_kg.get(species, 0.0))
        if segment_kg <= 0.0:
            continue
        segment = segment_by_name.get(segment_name)
        if segment is None:
            continue
        fractions[segment.wall_deposit_account] = segment_kg / wall_deposit_kg
    return _allocate_total_by_weights(1.0, fractions)


def _segment_stage_number(stage_name: str) -> int | None:
    try:
        return int(str(stage_name).rsplit('_', 1)[-1])
    except (TypeError, ValueError):
        return None


def cold_spot_diagnostic(
    pipe_segments: list[PipeSegment],
    vapor_species_kg_hr: Mapping[str, float],
    *,
    margin_C: float = COLD_SPOT_MARGIN_C,
) -> dict[str, Any]:
    """Flag pipe segments colder than a flowing species' landing threshold."""

    findings: list[dict[str, Any]] = []
    for species, raw_kg_hr in vapor_species_kg_hr.items():
        kg_hr = float(raw_kg_hr)
        if kg_hr <= 1e-15:
            continue
        target_stage_number = designated_stage_number(species)
        if target_stage_number is None:
            continue
        condensation_T_C = _species_condensation_temperature_C(species)
        threshold_C = condensation_T_C - float(margin_C)
        for segment in pipe_segments:
            downstream_number = _segment_stage_number(segment.downstream_stage)
            if downstream_number is None:
                continue
            if downstream_number > target_stage_number:
                continue
            wall_T_C = float(segment.wall_temperature_C)
            if wall_T_C >= threshold_C:
                continue
            findings.append({
                'segment': segment.name,
                'account': segment.wall_deposit_account,
                'species': str(species),
                'kg_hr': kg_hr,
                'wall_temperature_C': wall_T_C,
                'condensation_temperature_C': condensation_T_C,
                'margin_C': float(margin_C),
                'target_stage_number': target_stage_number,
                'warning': (
                    f'cold spot {segment.name}: {species} sees '
                    f'{wall_T_C:.1f} C before stage {target_stage_number}; '
                    f'threshold {threshold_C:.1f} C'
                ),
            })

    warnings = [str(finding['warning']) for finding in findings]
    return {
        'has_cold_spot': bool(findings),
        'margin_C': float(margin_C),
        'warnings': warnings,
        'findings': findings,
    }


def stage_purity_report(train: CondensationTrain) -> dict[str, dict[str, Any]]:
    """Classify each stage's accumulated product as designated or impurity."""

    report: dict[str, dict[str, Any]] = {}
    for stage in train.stages:
        stage_number = int(stage.stage_number)
        stage_key = STAGE_KEY_BY_NUMBER.get(
            stage_number, f'stage_{stage_number}')
        accepted_species = accepted_species_for_stage_number(stage_number)
        designated_species_kg: dict[str, float] = {}
        impurity_species_kg: dict[str, float] = {}
        for species, kg in sorted(stage.collected_kg.items()):
            kg = float(kg)
            if abs(kg) <= 1e-12:
                continue
            if is_designated_for_stage(species, stage_number):
                designated_species_kg[species] = kg
            else:
                impurity_species_kg[species] = kg

        designated_kg = sum(designated_species_kg.values())
        impurity_kg = sum(impurity_species_kg.values())
        total_kg = designated_kg + impurity_kg
        purity_fraction = 1.0 if total_kg <= 1e-12 else designated_kg / total_kg
        if purity_fraction > 0.95:
            verdict = 'PURE'
        elif purity_fraction >= 0.80:
            verdict = 'MIXED'
        else:
            verdict = 'CONTAMINATED'

        report[stage_key] = {
            'stage_number': stage_number,
            'label': stage.label,
            'accepted_species': sorted(accepted_species),
            'designated_species_kg': designated_species_kg,
            'impurity_species_kg': impurity_species_kg,
            'designated_kg': designated_kg,
            'impurity_kg': impurity_kg,
            'total_kg': total_kg,
            'purity_fraction': purity_fraction,
            'verdict': verdict,
            'warning': (
                'non-designated condensate present'
                if impurity_kg > 1e-12 else ''
            ),
        }
    return report
