from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

import yaml
from scipy.optimize import brentq

from simulator.accounting.formulas import parse_formula
from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa


STANDARD_PRESSURE_PA = 100_000.0
_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "refractory_vapor_species.yaml"
_LOG10_ACTIVITY_MIN = -80.0
_LOG10_ACTIVITY_MAX = 10.0
_SOLVER_RESIDUAL_TOLERANCE = 2.0e-7
_T406_MAX_DELTA_T_K = 300.0
_T406_MAX_DELTA_LOG10_KF = 1.5
_T406_MAX_DELTA_INVERSE_T_K_INV = 2.0e-4


class CongruentVaporizationError(ValueError):
    pass


@dataclass(frozen=True)
class ThermoRangeUse:
    species: str
    source_temperature_min_K: float
    source_temperature_max_K: float
    requested_temperature_K: float
    status: str
    delta_temperature_K: float
    delta_log10_kf: float
    delta_inverse_temperature_K_inv: float
    within_t406_input_distance_bounds: bool
    source_conflict: str | None


@dataclass(frozen=True)
class VaporSpeciesFlux:
    species: str
    partial_pressure_pa: float
    net_molar_flux_mol_m2_s: float
    net_mass_flux_kg_m2_s: float
    molar_mass_kg_mol: float


@dataclass(frozen=True)
class CongruentVaporizationResult:
    material: str
    temperature_K: float
    oxygen_mode: str
    ambient_pO2_pa: float
    imposed_pO2_pa: float | None
    surface_pO2_pa: float
    element_activities: Mapping[str, float]
    species: tuple[VaporSpeciesFlux, ...]
    formula_unit_flux_mol_m2_s: float
    condensed_mass_flux_kg_m2_s: float
    total_gas_mass_flux_kg_m2_s: float
    total_surface_pressure_pa: float
    external_oxygen_atom_flux_mol_m2_s: float
    evaporation_coefficient: float
    flux_classification: str
    certification_status: str
    certification_blockers: tuple[str, ...]
    transport_applicability: str
    self_buffered_root_count: int | None
    solver_residual_norm: float
    thermo_range_uses: tuple[ThermoRangeUse, ...]
    source_conflicts: tuple[str, ...]
    unmodeled_species: tuple[str, ...]

    def species_pressure_pa(self, species: str) -> float:
        for item in self.species:
            if item.species == species:
                return item.partial_pressure_pa
        raise KeyError(species)

    def recession_mm_per_1000h(self, density_kg_m3: float) -> float:
        density = float(density_kg_m3)
        if not math.isfinite(density) or density <= 0.0:
            raise ValueError("density_kg_m3 must be finite and positive")
        return self.condensed_mass_flux_kg_m2_s * 3.6e9 / density

    @property
    def cation_bearing_surface_pressure_pa(self) -> float:
        return sum(
            item.partial_pressure_pa
            for item in self.species
            if item.species not in {"O", "O2"}
        )


@lru_cache(maxsize=1)
def _thermo_data() -> dict:
    with _DATA_PATH.open() as handle:
        data = yaml.safe_load(handle)
    if float(data["standard_pressure_pa"]) != STANDARD_PRESSURE_PA:
        raise CongruentVaporizationError(
            "refractory vapor sidecar standard pressure must be 100000 Pa"
        )
    return data


def refractory_vapor_species(material: str) -> tuple[str, ...]:
    entry = _material_entry(material)
    return tuple(str(item) for item in entry["gas_species"])


def refractory_vapor_species_gaps(material: str) -> tuple[str, ...]:
    entry = _material_entry(material)
    return tuple(sorted(str(item) for item in entry.get("unmodeled_species", {})))


def refractory_log10_kf(
    species: str,
    temperature_K: float,
    *,
    condensed: bool = False,
) -> tuple[float, ThermoRangeUse]:
    """Interpolate JANAF log10(Kf); extend in 1/T with typed t-406 bounds."""
    temperature = _positive_temperature(temperature_K)
    section = "condensed_phases" if condensed else "gas_species"
    try:
        entry = _thermo_data()[section][species]
    except KeyError as exc:
        raise CongruentVaporizationError(
            f"no refractory thermodynamics for {species!r} in {section}"
        ) from exc
    points = sorted(
        (float(key), float(value)) for key, value in entry["log10_kf"].items()
    )
    if len(points) < 2:
        raise CongruentVaporizationError(f"{species!r} requires at least two log10(Kf) points")
    low_temperature, high_temperature = points[0][0], points[-1][0]
    if low_temperature <= temperature <= high_temperature:
        status = "in_range"
        delta_temperature = 0.0
        delta_log10_kf = 0.0
        delta_inverse_temperature = 0.0
        within_t406_input_distance_bounds = True
        for left, right in zip(points, points[1:]):
            if left[0] <= temperature <= right[0]:
                break
        fraction = (temperature - left[0]) / (right[0] - left[0])
        value = left[1] + fraction * (right[1] - left[1])
    else:
        if temperature < low_temperature:
            boundary, neighbor = points[0], points[1]
        else:
            boundary, neighbor = points[-1], points[-2]
        inverse_fraction = (
            (1.0 / temperature) - (1.0 / boundary[0])
        ) / ((1.0 / neighbor[0]) - (1.0 / boundary[0]))
        value = boundary[1] + inverse_fraction * (neighbor[1] - boundary[1])
        delta_temperature = abs(temperature - boundary[0])
        delta_log10_kf = abs(value - boundary[1])
        delta_inverse_temperature = abs((1.0 / temperature) - (1.0 / boundary[0]))
        within_t406_input_distance_bounds = (
            delta_temperature <= _T406_MAX_DELTA_T_K
            and delta_log10_kf <= _T406_MAX_DELTA_LOG10_KF
            and delta_inverse_temperature <= _T406_MAX_DELTA_INVERSE_T_K_INV
        )
        status = (
            "extrapolated_input_projection_within_t406_distance_bounds"
            if within_t406_input_distance_bounds
            else "extrapolated_model_limited"
        )
    return value, ThermoRangeUse(
        species=species,
        source_temperature_min_K=low_temperature,
        source_temperature_max_K=high_temperature,
        requested_temperature_K=temperature,
        status=status,
        delta_temperature_K=delta_temperature,
        delta_log10_kf=delta_log10_kf,
        delta_inverse_temperature_K_inv=delta_inverse_temperature,
        within_t406_input_distance_bounds=within_t406_input_distance_bounds,
        source_conflict=(
            str(entry["source_conflict"]) if entry.get("source_conflict") else None
        ),
    )


def solve_congruent_vaporization(
    material: str,
    temperature_K: float,
    *,
    oxygen_mode: str = "self_buffered",
    ambient_pO2_pa: float = 0.0,
    imposed_pO2_pa: float | None = None,
) -> CongruentVaporizationResult:
    """
    Solve the free-molecular pure-oxide congruent-vaporization boundary.

    Premise and stoichiometry:
      For condensed C = Π_j M_j^c_j O^c_O and gas species
      i = Π_j M_j^n_ij O^n_i, JANAF formation constants give

        log10(p_i/p°) = log10(Kf_i)
                          + Σ_j n_ij log10(a_Mj)
                          + (n_i/2) log10(pO2/p°),                 (1)
        Σ_j c_j log10(a_Mj) + (c_O/2) log10(pO2/p°)
                          = -log10(Kf_C).                         (2)

      For the single cation in an authoritative pure oxide, its atom flux
      fixes the formula-unit flux L = F_M/c_M. In self-buffered vacuum the
      oxygen released by L closes locally:

        c_O*L - Σ_(metal gases i) n_i*N_i = N_O + 2*N_O2.        (3)

       Equation (3) determines surface pO2. ``oxygen_mode="imposed"``
       instead pins pO2 to the external pipework value and removes (3).
       Only O2 has that imposed return pressure; O(g) still effuses. The
       returned external-oxygen flux is positive when the reservoir must
       supply oxygen atoms and negative when it must absorb them.

      Multication line compounds are deliberately refused. One compound
      Gibbs relation cannot determine every component chemical potential,
      and direct spinel KEMS refutes a forced congruent Mg:Al flux.

    Flux algebra and units:
      Hertz-Knudsen with alpha_i=1 gives the equilibrium-effusion kinetic
      ceiling for each *included* carrier:
        j_i = alpha_i*Δp_i*sqrt(M_i/(2*pi*R*T)) [Pa*sqrt(kg mol-1 /
               (J mol-1 K-1 K))] = kg m-2 s-1.
      Therefore N_i = j_i/M_i and total vapor mass flux is Σ_i j_i over
      the registry gas_species only, each with its own M_i. Condensed
      recession uses L*M_C [kg m-2 s-1], not a monatomic-metal surrogate.

      Bound claim (refused): alpha=1 does NOT justify labeling the sum an
      absolute upper bound on total recession. A deliberately omitted
      carrier (see unmodeled_species) can both raise mass loss and shift
      the self-buffered pO2 of every included species. Results are
      therefore classified as an included-carrier sum only.

    Sanity anchors are external tests, not coefficients: the Al2O3 species
    sum crosses the NASA TM-2005-213625 ~1e-10 atm vacuum screen near
    1800 K, while CaO is compared without tuning against Shornikov KEMS.
    """
    temperature = _positive_temperature(temperature_K)
    ambient = float(ambient_pO2_pa)
    if not math.isfinite(ambient) or ambient < 0.0:
        raise ValueError("ambient_pO2_pa must be finite and nonnegative")
    if oxygen_mode not in {"self_buffered", "imposed"}:
        raise ValueError("oxygen_mode must be 'self_buffered' or 'imposed'")
    if oxygen_mode == "self_buffered":
        if imposed_pO2_pa is not None:
            raise ValueError("imposed_pO2_pa is incompatible with self_buffered mode")
        if ambient != 0.0:
            raise ValueError(
                "self_buffered mode currently represents open vacuum only; "
                "finite return pressure belongs to the transport boundary model"
            )
        fixed_log10_pO2_ratio = None
    else:
        if ambient != 0.0:
            raise ValueError(
                "ambient_pO2_pa must be zero when imposed_pO2_pa defines the boundary"
            )
        if imposed_pO2_pa is None:
            raise ValueError("imposed mode requires imposed_pO2_pa")
        imposed = float(imposed_pO2_pa)
        if not math.isfinite(imposed) or imposed <= 0.0:
            raise ValueError("imposed_pO2_pa must be finite and positive")
        fixed_log10_pO2_ratio = math.log10(imposed / STANDARD_PRESSURE_PA)

    material_entry = _material_entry(material)
    if material_entry.get("execution_model") != "pure_oxide":
        reason = str(material_entry.get("refusal_reason", "not authoritative"))
        raise CongruentVaporizationError(
            f"{material!r} refractory vaporization refused: {reason}"
        )
    condensed_formula = parse_formula(material_entry["formula"])
    metal_elements = tuple(
        sorted(element for element in condensed_formula.elements if element != "O")
    )
    if not metal_elements or "O" not in condensed_formula.elements:
        raise CongruentVaporizationError(f"{material!r} is not a cation oxide")
    if len(metal_elements) != 1:
        raise CongruentVaporizationError(
            f"{material!r} has multiple cations and requires a sourced phase/activity model"
        )

    gas_names = tuple(str(name) for name in material_entry["gas_species"])
    if "O" not in gas_names or "O2" not in gas_names:
        raise CongruentVaporizationError(f"{material!r} must include O and O2")
    gas_formulas = {name: parse_formula(_gas_entry(name)["formula"]) for name in gas_names}
    for name, formula in gas_formulas.items():
        unexpected = set(formula.elements) - set(metal_elements) - {"O"}
        if unexpected:
            raise CongruentVaporizationError(
                f"{name!r} contains elements absent from {material!r}: {sorted(unexpected)}"
            )

    range_uses: list[ThermoRangeUse] = []
    condensed_log10_kf, use = refractory_log10_kf(
        material, temperature, condensed=True
    )
    range_uses.append(use)
    gas_log10_kf: dict[str, float] = {}
    for name in gas_names:
        value, use = refractory_log10_kf(name, temperature)
        gas_log10_kf[name] = value
        range_uses.append(use)

    metal_element = metal_elements[0]
    condensed_metal_count = float(condensed_formula.elements[metal_element])
    condensed_oxygen_count = float(condensed_formula.elements["O"])

    def state(
        oxygen_log: float,
    ) -> tuple[dict[str, float], dict[str, float], dict[str, float], float]:
        metal_activity_log = (
            -condensed_log10_kf - 0.5 * condensed_oxygen_count * oxygen_log
        ) / condensed_metal_count
        if not _LOG10_ACTIVITY_MIN <= metal_activity_log <= _LOG10_ACTIVITY_MAX:
            raise CongruentVaporizationError(
                f"{material} solution requires non-physical log10({metal_element} activity) "
                f"{metal_activity_log:.6g}"
            )
        activities = {metal_element: metal_activity_log}
        pressures: dict[str, float] = {}
        fluxes: dict[str, float] = {}
        for name, formula in gas_formulas.items():
            log_ratio = gas_log10_kf[name]
            for element in metal_elements:
                log_ratio += float(formula.elements.get(element, 0.0)) * activities[element]
            log_ratio += 0.5 * float(formula.elements.get("O", 0.0)) * oxygen_log
            pressure = STANDARD_PRESSURE_PA * 10.0 ** max(
                -300.0, min(300.0, log_ratio)
            )
            bulk_pressure = (
                float(imposed_pO2_pa)
                if oxygen_mode == "imposed" and name == "O2"
                else 0.0
            )
            molar_mass = formula.molar_mass_kg_per_mol()
            mass_coefficient = hertz_knudsen_k_kg_s_m2_pa(
                temperature,
                molar_mass,
            )
            net_pressure = max(pressure - bulk_pressure, 0.0)
            pressures[name] = pressure
            fluxes[name] = net_pressure * mass_coefficient / molar_mass

        cation_flux = sum(
            float(formula.elements.get(metal_element, 0.0)) * fluxes[name]
            for name, formula in gas_formulas.items()
        )
        formula_flux = cation_flux / condensed_metal_count
        return activities, pressures, fluxes, formula_flux

    def oxygen_residual(oxygen_log: float) -> float:
        _, _, fluxes, formula_flux = state(oxygen_log)
        oxygen_from_condensed = condensed_oxygen_count * formula_flux
        oxygen_in_cation_vapor = sum(
            float(gas_formulas[name].elements.get("O", 0.0)) * fluxes[name]
            for name in gas_names
            if gas_formulas[name].elements.get(metal_element, 0.0) > 0.0
        )
        oxygen_excess = oxygen_from_condensed - oxygen_in_cation_vapor
        oxygen_effusion = fluxes["O"] + 2.0 * fluxes["O2"]
        scale = abs(oxygen_excess) + abs(oxygen_effusion) + 1.0e-300
        return 2.0 * (oxygen_excess - oxygen_effusion) / scale

    if oxygen_mode == "self_buffered":
        scan_points = [
            _LOG10_ACTIVITY_MIN
            + index * (_LOG10_ACTIVITY_MAX - _LOG10_ACTIVITY_MIN) / 720.0
            for index in range(721)
        ]
        brackets: list[tuple[float, float]] = []
        exact_roots: list[float] = []
        previous_x: float | None = None
        previous_value: float | None = None
        for point in scan_points:
            try:
                value = oxygen_residual(point)
            except CongruentVaporizationError:
                previous_x = None
                previous_value = None
                continue
            if not math.isfinite(value):
                previous_x = None
                previous_value = None
                continue
            if abs(value) <= 1.0e-14:
                exact_roots.append(point)
                previous_x = None
                previous_value = None
                continue
            if previous_value is not None and value * previous_value < 0.0:
                brackets.append((previous_x, point))
            previous_x = point
            previous_value = value
        candidate_roots = list(exact_roots)
        for bracket in brackets:
            try:
                candidate_roots.append(
                    brentq(
                        oxygen_residual,
                        bracket[0],
                        bracket[1],
                        xtol=1.0e-12,
                        rtol=1.0e-12,
                        maxiter=200,
                    )
                )
            except (RuntimeError, ValueError) as exc:
                # Fail loud with typed refusal — never return a bracket mid-point
                # or last residual evaluation (SC-109 completion-from-absence).
                raise CongruentVaporizationError(
                    f"congruent pO2 root solver failed for {material} at "
                    f"{temperature:g} K (bracket={bracket[0]:.6g}..{bracket[1]:.6g}): "
                    f"{exc}"
                ) from exc
        unique_roots: list[float] = []
        for root in sorted(candidate_roots):
            if not unique_roots or abs(root - unique_roots[-1]) > 1.0e-8:
                unique_roots.append(root)
        if len(unique_roots) != 1:
            raise CongruentVaporizationError(
                f"congruent solve found {len(unique_roots)} physical pO2 roots for "
                f"{material} at {temperature:g} K"
            )
        oxygen_log = unique_roots[0]
        self_buffered_root_count: int | None = len(unique_roots)
        residual_norm = abs(oxygen_residual(oxygen_log))
    else:
        assert fixed_log10_pO2_ratio is not None
        oxygen_log = fixed_log10_pO2_ratio
        self_buffered_root_count = None
        residual_norm = 0.0
    if not math.isfinite(residual_norm) or residual_norm > _SOLVER_RESIDUAL_TOLERANCE:
        raise CongruentVaporizationError(
            f"congruent solve failed for {material} at {temperature:g} K: "
            f"residual={residual_norm:.3g}"
        )

    activity_logs, pressures, fluxes, formula_flux = state(oxygen_log)
    surface_pO2 = STANDARD_PRESSURE_PA * 10.0 ** oxygen_log
    if not math.isfinite(surface_pO2) or surface_pO2 <= 0.0:
        raise CongruentVaporizationError("solver returned non-physical surface pO2")
    if oxygen_mode == "self_buffered" and surface_pO2 + 1.0e-18 < ambient:
        raise CongruentVaporizationError("self-buffered surface pO2 fell below ambient")

    species_results = []
    for name in gas_names:
        formula = gas_formulas[name]
        molar_mass = formula.molar_mass_kg_per_mol()
        molar_flux = fluxes[name]
        species_results.append(
            VaporSpeciesFlux(
                species=name,
                partial_pressure_pa=pressures[name],
                net_molar_flux_mol_m2_s=molar_flux,
                net_mass_flux_kg_m2_s=molar_flux * molar_mass,
                molar_mass_kg_mol=molar_mass,
            )
        )
    condensed_mass_flux = formula_flux * condensed_formula.molar_mass_kg_per_mol()
    total_gas_mass_flux = sum(item.net_mass_flux_kg_m2_s for item in species_results)
    if oxygen_mode == "self_buffered" and not math.isclose(
        condensed_mass_flux,
        total_gas_mass_flux,
        rel_tol=2.0e-6,
        abs_tol=1.0e-18,
    ):
        raise CongruentVaporizationError(
            f"self-buffered gas/solid mass flux mismatch for {material}: "
            f"solid={condensed_mass_flux:.12g}, gas={total_gas_mass_flux:.12g}"
        )
    oxygen_atom_flux = sum(
        float(gas_formulas[name].elements.get("O", 0.0)) * fluxes[name]
        for name in gas_names
    )
    external_oxygen_atom_flux = (
        oxygen_atom_flux - condensed_oxygen_count * formula_flux
        if oxygen_mode == "imposed"
        else 0.0
    )
    source_conflicts = tuple(
        sorted(
            {
                use.source_conflict
                for use in range_uses
                if use.source_conflict is not None
            }
        )
    )
    certification_blockers = [
        str(material_entry.get("certification_status", "provisional_unclassified"))
    ]
    if any(use.status == "extrapolated_model_limited" for use in range_uses):
        certification_blockers.append("model_limited_extrapolation")
    elif any(use.status != "in_range" for use in range_uses):
        certification_blockers.append(
            "model_limited_extrapolation_no_resultant_pressure_envelope"
        )
    certification_status = "+".join(certification_blockers)

    return CongruentVaporizationResult(
        material=material,
        temperature_K=temperature,
        oxygen_mode=oxygen_mode,
        ambient_pO2_pa=ambient,
        imposed_pO2_pa=(
            float(imposed_pO2_pa) if oxygen_mode == "imposed" else None
        ),
        surface_pO2_pa=surface_pO2,
        element_activities={
            element: 10.0 ** value for element, value in activity_logs.items()
        },
        species=tuple(species_results),
        formula_unit_flux_mol_m2_s=formula_flux,
        condensed_mass_flux_kg_m2_s=condensed_mass_flux,
        total_gas_mass_flux_kg_m2_s=total_gas_mass_flux,
        total_surface_pressure_pa=sum(pressures.values()),
        external_oxygen_atom_flux_mol_m2_s=external_oxygen_atom_flux,
        evaporation_coefficient=1.0,
        # alpha=1 is only a kinetic ceiling on the carriers in gas_species;
        # unmodeled_species can raise loss and shift self-buffered pO2, so
        # this is deliberately NOT an absolute total-recession upper bound.
        flux_classification="included_carrier_equilibrium_effusion_sum",
        certification_status=certification_status,
        certification_blockers=tuple(certification_blockers),
        transport_applicability="requires_external_Knudsen_number",
        self_buffered_root_count=self_buffered_root_count,
        solver_residual_norm=residual_norm,
        thermo_range_uses=tuple(range_uses),
        source_conflicts=source_conflicts,
        unmodeled_species=refractory_vapor_species_gaps(material),
    )


def _material_entry(material: str) -> dict:
    try:
        return _thermo_data()["materials"][material]
    except KeyError as exc:
        raise CongruentVaporizationError(
            f"unknown refractory material {material!r}"
        ) from exc


def _gas_entry(species: str) -> dict:
    try:
        return _thermo_data()["gas_species"][species]
    except KeyError as exc:
        raise CongruentVaporizationError(
            f"unknown refractory vapor species {species!r}"
        ) from exc


def _positive_temperature(temperature_K: float) -> float:
    temperature = float(temperature_K)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ValueError("temperature_K must be finite and positive")
    return temperature
