"""Chamber pressure from a declared test-stand apparatus.

The balance and the driver split are specified in design-teststand.md.
Conductances and the series speed law are the ones in transport_regime.
This module does not own a timestep. The process hour passes dt_s=3600
and asks for the steady state; the analytic exponential is available for
a caller that already has a step length.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from simulator.physical_constants import (
    CELSIUS_TO_KELVIN_OFFSET,
    GAS_CONSTANT,
    PA_PER_MBAR,
    STANDARD_ATMOSPHERE_PA,
)
from simulator.scalar_boundary import is_declared_real_scalar
from simulator.transport_regime import (
    FREE_MOLECULAR_KNUDSEN_MIN,
    LONG_TUBE_L_OVER_D_MIN,
    KnudsenRegime,
    TransportRegimeRefusal,
    beskok_karniadakis_rarefaction_factor,
    long_tube_molecular_conductance_m3_s,
    molecular_aperture_conductance_m3_s,
    poiseuille_conductance_m3_s,
    series_effective_speed_m3_s,
    single_species_mean_free_path,
)

FORM_STEADY_STATE = "steady_state"
FORM_ANALYTIC = "analytic"
DRIVER_IMPOSED = "imposed"
DRIVER_APPARATUS = "apparatus"
FLOW_UNIT_NL_MIN = "NL_min"
NORMAL_LITRE_TEMPERATURE_K = CELSIUS_TO_KELVIN_OFFSET
# Overhead O2 counters are mol per existing process hour. This converts
# those counters to moles during dt. It is not a timestep setting.
_HOUR_S = 3600.0
_BK_PASS_LIMIT = 8
_SETTLE_RELATIVE = 1e-8

_ELEMENT_KINDS = frozenset({"orifice", "tube", "filter"})
_FORMULAS_CONSTANT = frozenset({"molecular"})
_FORMULAS_PRESSURE_DEPENDENT = frozenset({"poiseuille", "bk"})


class TeststandRefusal(ValueError):
    """Typed refusal for an apparatus that cannot set chamber pressure."""

    __test__ = False

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        notices: tuple[str, ...] = (),
    ) -> None:
        self.code = code
        self.reason = code
        self.notices = tuple(notices)
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ConductanceElement:
    """One path element, chamber end toward the pump."""

    index: int
    declared_kind: str
    model_kind: str
    diameter_m: float
    open_area_m2: float | None
    length_m: float | None
    dynamic_viscosity_pa_s: float | None
    allow_transitional: bool


@dataclass(frozen=True)
class PumpSpec:
    nominal_speed_m3_s: float | None
    ultimate_pressure_pa: float | None
    speed_curve_pa_to_m3_s: tuple[tuple[float, float], ...] | None


@dataclass(frozen=True)
class TeststandApparatus:
    pressure_driver: str
    balance_temperature_K: float | None
    chamber_volume_m3: float | None
    carrier_species: str | None
    carrier_mol_per_s: float | None
    pump: PumpSpec | None
    elements: tuple[ConductanceElement, ...] | None
    charge_mass_g: float | None
    exposed_melt_area_m2: float | None
    crucible_material: str | None
    printed_vacuum_limit_mbar: float | None
    path_placeholder: bool
    notices: tuple[str, ...]


@dataclass(frozen=True)
class _PreparedElement:
    index: int
    declared_kind: str
    model_kind: str
    formula: str
    regime: str
    knudsen_number: float
    diameter_m: float
    conductance_m3_s: float | None
    poiseuille_k_m3_s_per_pa: float | None
    rarefaction: float | None
    viscosity_pa_s: float | None
    length_m: float | None


@dataclass(frozen=True)
class ChamberPressureStep:
    pressure_pa: float
    pressure_mbar: float
    form: str
    dt_s: float
    q_pa_m3_s: float
    s_eff_m3_s: float
    pump_inlet_pressure_pa: float
    notices: tuple[str, ...]


def normal_litres_per_minute_to_mol_per_s(flow_nl_min: float) -> float:
    """Convert a normal-litre flow at 1 atm and 0 C to mol/s.

    A normal litre is 1e-3 m^3 at P_std and T_std. The balance temperature
    is applied later, when the throughput is n_dot * R * T_balance.
    """
    flow_nl_min = _require_non_negative_number(
        flow_nl_min,
        code="invalid_carrier_flow",
        label="imposed_flow",
    )
    return (
        flow_nl_min
        * 1.0e-3
        / 60.0
        * STANDARD_ATMOSPHERE_PA
        / (GAS_CONSTANT * NORMAL_LITRE_TEMPERATURE_K)
    )


def parse_teststand(
    schedule: Mapping[str, Any] | None,
    geometry: Mapping[str, Any] | None,
) -> TeststandApparatus:
    """Read a preset schedule and raw lab geometry into one apparatus."""
    if not isinstance(schedule, Mapping):
        raise TeststandRefusal(
            "missing_lab_schedule",
            "a test stand requires lab_schedule",
        )
    gas = _mapping(schedule.get("gas_boundary"), code="missing_gas_boundary")
    background = gas.get("background_gas")
    species = _carrier_species(background)
    carrier_mol_per_s = _carrier_mol_per_s(gas.get("imposed_flow"))
    driver = _pressure_driver(gas.get("pressure_control"), geometry)
    pump_raw = _pump_mapping(geometry)
    pump = _parse_pump(pump_raw) if isinstance(pump_raw, Mapping) else None
    elements, placeholder = _parse_path(pump_raw)
    sample = _optional_mapping(_geometry_mapping(geometry).get("sample"))
    chamber = _optional_mapping(_geometry_mapping(geometry).get("chamber"))
    notices = _declaration_notices(
        driver=driver,
        pump=pump,
        elements=elements,
        placeholder=placeholder,
        pump_raw=pump_raw if isinstance(pump_raw, Mapping) else None,
        chamber=chamber,
        sample=sample,
    )
    return TeststandApparatus(
        pressure_driver=driver,
        balance_temperature_K=_optional_positive_field(
            pump_raw.get("balance_temperature_K")
            if isinstance(pump_raw, Mapping)
            else None,
            code="invalid_balance_temperature",
            label="balance_temperature_K",
        ),
        chamber_volume_m3=_optional_positive_field(
            chamber.get("volume_m3") if isinstance(chamber, Mapping) else None,
            code="invalid_chamber_volume",
            label="chamber.volume_m3",
        ),
        carrier_species=species,
        carrier_mol_per_s=carrier_mol_per_s,
        pump=pump,
        elements=elements,
        charge_mass_g=_optional_positive_field(
            sample.get("mass_g") if isinstance(sample, Mapping) else None,
            code="invalid_charge_mass",
            label="sample.mass_g",
        ),
        exposed_melt_area_m2=_optional_positive_field(
            sample.get("exposed_melt_area_m2")
            if isinstance(sample, Mapping)
            else None,
            code="invalid_exposed_melt_area",
            label="sample.exposed_melt_area_m2",
        ),
        crucible_material=_optional_text(
            sample.get("crucible_material") if isinstance(sample, Mapping) else None
        ),
        printed_vacuum_limit_mbar=_optional_non_negative_field(
            pump_raw.get("vacuum_limit_mbar")
            if isinstance(pump_raw, Mapping)
            else None,
            code="invalid_vacuum_limit",
            label="vacuum_limit_mbar",
        ),
        path_placeholder=placeholder,
        notices=notices,
    )


def advance_chamber_pressure(
    apparatus: TeststandApparatus,
    *,
    dt_s: float,
    evolved_noncondensable_mol: float,
    form: str,
    carrier_mol: float | None = None,
    pressure_pa: float | None = None,
) -> ChamberPressureStep:
    """Advance the chamber one step of length dt_s.

    ``carrier_mol`` and ``evolved_noncondensable_mol`` are moles during
    that step. Omit ``carrier_mol`` to use the declared NL/min rate times
    dt_s. ``form`` is ``steady_state`` or ``analytic``.
    """
    if apparatus.pressure_driver != DRIVER_APPARATUS:
        raise TeststandRefusal(
            "pressure_driver_is_not_apparatus",
            "chamber pressure is imposed by the schedule and is not solved",
            notices=apparatus.notices,
        )
    dt_s = _require_positive_number(dt_s, code="invalid_dt", label="dt_s")
    if form not in (FORM_STEADY_STATE, FORM_ANALYTIC):
        raise TeststandRefusal(
            "unknown_pressure_form",
            f"form must be {FORM_STEADY_STATE} or {FORM_ANALYTIC}",
        )
    evolved = _require_non_negative_number(
        evolved_noncondensable_mol,
        code="invalid_molar_load",
        label="evolved_noncondensable_mol",
    )
    if carrier_mol is None:
        if apparatus.carrier_mol_per_s is None:
            raise TeststandRefusal(
                "missing_carrier_flow",
                "apparatus mode requires imposed_flow in NL_min or carrier_mol",
            )
        carrier = apparatus.carrier_mol_per_s * dt_s
    else:
        carrier = _require_non_negative_number(
            carrier_mol,
            code="invalid_molar_load",
            label="carrier_mol",
        )
    temperature_K = apparatus.balance_temperature_K
    if temperature_K is None:
        raise TeststandRefusal(
            "missing_balance_temperature",
            "pump.balance_temperature_K is required in apparatus mode",
        )
    pump = apparatus.pump
    if pump is None:
        raise TeststandRefusal(
            "missing_pump_speed",
            "apparatus mode requires a nominal pump speed or a speed curve",
        )
    _require_unique_pump(pump)
    if apparatus.elements is None:
        raise TeststandRefusal(
            "missing_conductance_path",
            "conductance_path must be a list; null is not an empty line",
            notices=apparatus.notices,
        )
    if apparatus.elements and not apparatus.carrier_species:
        raise TeststandRefusal(
            "missing_balance_species",
            "a conductance element requires background_gas.species",
        )
    n_dot = (carrier + evolved) / dt_s
    throughput = n_dot * GAS_CONSTANT * temperature_K
    inlet = _pump_inlet_pressure_pa(pump, throughput)
    prepared = _prepare_elements(
        apparatus.elements,
        pressure_pa=inlet,
        species=apparatus.carrier_species,
        temperature_K=temperature_K,
    )
    chamber_pa, prepared = _chamber_pressure_pa(
        inlet,
        throughput,
        prepared,
        species=apparatus.carrier_species,
        temperature_K=temperature_K,
    )
    s_eff = _effective_speed_m3_s(pump, prepared, throughput, chamber_pa)
    if form == FORM_ANALYTIC:
        chamber_pa = _analytic_pressure_pa(
            apparatus,
            pump=pump,
            prepared=prepared,
            s_eff_m3_s=s_eff,
            dt_s=dt_s,
            pressure_pa=pressure_pa,
            steady_pressure_pa=chamber_pa,
            species=apparatus.carrier_species,
            temperature_K=temperature_K,
        )
    return ChamberPressureStep(
        pressure_pa=chamber_pa,
        pressure_mbar=chamber_pa / PA_PER_MBAR,
        form=form,
        dt_s=dt_s,
        q_pa_m3_s=throughput,
        s_eff_m3_s=s_eff,
        pump_inlet_pressure_pa=inlet,
        notices=apparatus.notices,
    )


def apply_process_chamber_pressure(sim: Any, *, dt_s: float = _HOUR_S) -> None:
    """Solve the hour's chamber pressure when the driver is apparatus.

    Imposed and legacy schedules return without writing ``p_total_mbar``.
    The steady form is the large-dt limit. ``dt_s`` is the caller's step
    length; the hour passes the existing 3600 s hour and does not invent
    a shorter step.
    """
    schedule = _schedule_from_sim(sim)
    if schedule is None:
        return
    geometry = _geometry_from_sim(sim)
    apparatus = parse_teststand(schedule, geometry)
    melt = sim.melt
    schedule_pressure_mbar = float(melt.p_total_mbar)
    if apparatus.pressure_driver != DRIVER_APPARATUS:
        sim.last_teststand_pressure = {
            "pressure_driver": apparatus.pressure_driver,
            "form": None,
            "applied": False,
            "pressure_pa": None,
            "schedule_pressure_mbar": schedule_pressure_mbar,
            "dt_s": float(dt_s),
            "notices": list(apparatus.notices),
        }
        return
    _refuse_campaign_conflict(melt)
    overhead = sim.overhead
    evolved_mol_hr = float(overhead.melt_offgas_O2_mol_hr) + float(
        overhead.mre_anode_O2_mol_hr
    )
    evolved_mol = evolved_mol_hr * (float(dt_s) / _HOUR_S)
    step = advance_chamber_pressure(
        apparatus,
        dt_s=float(dt_s),
        evolved_noncondensable_mol=evolved_mol,
        form=FORM_STEADY_STATE,
    )
    solved_mbar = step.pressure_mbar
    pO2_mbar = float(melt.pO2_mbar)
    from simulator.state import partial_pressure_exceeds_total

    if partial_pressure_exceeds_total(pO2_mbar, solved_mbar):
        raise TeststandRefusal(
            "partial_pressure_exceeds_solved_total",
            f"pO2_mbar={pO2_mbar:.12g} exceeds solved total {solved_mbar:.12g}",
        )
    melt.p_total_mbar = solved_mbar
    validator = getattr(melt, "validate_melt_pressures", None)
    if callable(validator):
        validator()
    sim.last_teststand_pressure = {
        "pressure_driver": DRIVER_APPARATUS,
        "form": step.form,
        "applied": True,
        "pressure_pa": step.pressure_pa,
        "pressure_mbar": step.pressure_mbar,
        "schedule_pressure_mbar": schedule_pressure_mbar,
        "s_eff_m3_s": step.s_eff_m3_s,
        "q_pa_m3_s": step.q_pa_m3_s,
        "dt_s": step.dt_s,
        "notices": list(step.notices),
    }


def _schedule_from_sim(sim: Any) -> Mapping[str, Any] | None:
    manager = getattr(sim, "campaign_mgr", None)
    getter = getattr(manager, "_lab_schedule", None)
    melt = getattr(sim, "melt", None)
    if not callable(getter) or melt is None:
        return None
    schedule = getter(melt.campaign)
    if schedule is None:
        return None
    if not isinstance(schedule, Mapping):
        raise TeststandRefusal(
            "lab_schedule_must_be_mapping",
            "campaign lab_schedule is not a mapping",
        )
    return schedule


def _geometry_from_sim(sim: Any) -> Mapping[str, Any] | None:
    setpoints = getattr(sim, "setpoints", None)
    if not isinstance(setpoints, Mapping):
        return None
    geometry = setpoints.get("lab_geometry")
    if geometry is None:
        return None
    if not isinstance(geometry, Mapping):
        raise TeststandRefusal(
            "invalid_lab_geometry",
            "setpoints lab_geometry is not a mapping",
        )
    return geometry


def _refuse_campaign_conflict(melt: Any) -> None:
    from simulator.state import CampaignPhase

    if melt.campaign in (CampaignPhase.C2A, CampaignPhase.C2A_STAGED):
        raise TeststandRefusal(
            "campaign_pressure_control_conflicts_with_apparatus",
            f"{melt.campaign.name} rewrites total pressure after the schedule",
        )


def _pressure_driver(raw: Any, geometry: Mapping[str, Any] | None) -> str:
    if not isinstance(raw, Mapping):
        raise TeststandRefusal(
            "missing_gas_boundary_pressure_control",
            "pressure_control is required",
        )
    declared = raw.get("pressure_driver")
    if declared is None or declared == "":
        if _has_solvable_pump_spec(geometry):
            raise TeststandRefusal(
                "pressure_driver_ambiguous",
                "a pump speed or conductance element is present and "
                "pressure_driver is absent",
            )
        return DRIVER_IMPOSED
    if declared == DRIVER_IMPOSED:
        return DRIVER_IMPOSED
    if declared == DRIVER_APPARATUS:
        return DRIVER_APPARATUS
    raise TeststandRefusal(
        "unknown_pressure_driver",
        f"pressure_driver {declared!r} is not {DRIVER_IMPOSED} or {DRIVER_APPARATUS}",
    )


def _has_solvable_pump_spec(geometry: Mapping[str, Any] | None) -> bool:
    pump = _pump_mapping(geometry)
    if not isinstance(pump, Mapping):
        return False
    speed = pump.get("pumping_speed_m3_s")
    if _is_strictly_positive_number(speed):
        return True
    curve = pump.get("speed_curve")
    if isinstance(curve, list) and len(curve) > 0:
        return True
    path = pump.get("conductance_path")
    if isinstance(path, list):
        return any(
            isinstance(item, Mapping) and str(item.get("kind") or "").strip()
            for item in path
        )
    return False


def _declaration_notices(
    *,
    driver: str,
    pump: PumpSpec | None,
    elements: tuple[ConductanceElement, ...] | None,
    placeholder: bool,
    pump_raw: Mapping[str, Any] | None,
    chamber: Mapping[str, Any] | None,
    sample: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    if driver != DRIVER_IMPOSED:
        return ()
    missing: list[str] = []
    if pump is None or (
        pump.nominal_speed_m3_s is None and pump.speed_curve_pa_to_m3_s is None
    ):
        missing.append("pumping_speed_m3_s")
    if elements is None:
        missing.append("conductance_path")
    if not isinstance(chamber, Mapping) or chamber.get("volume_m3") is None:
        missing.append("chamber.volume_m3")
    if not isinstance(pump_raw, Mapping) or pump_raw.get("balance_temperature_K") is None:
        missing.append("balance_temperature_K")
    if isinstance(sample, Mapping) and sample.get("exposed_melt_area_m2") is None:
        missing.append("exposed_melt_area_m2")
    notices = []
    if missing or placeholder:
        listed = ", ".join(missing) if missing else "conductance_path"
        notices.append(
            "pressure_driver is imposed; the apparatus is not fully specified "
            f"({listed}) and chamber pressure is the reported schedule"
        )
    if placeholder:
        notices.append(
            "conductance_path is a named-missing placeholder, not a solvable line"
        )
    if pump is not None and pump.nominal_speed_m3_s is not None:
        notices.append(
            "a pump speed is recorded and is not applied because "
            "pressure_driver is imposed"
        )
    return tuple(notices)


def _parse_pump(raw: Mapping[str, Any]) -> PumpSpec:
    speed = _optional_positive_field(
        raw.get("pumping_speed_m3_s"),
        code="invalid_pump_speed",
        label="pumping_speed_m3_s",
    )
    ultimate = _optional_non_negative_field(
        raw.get("ultimate_pressure_pa"),
        code="invalid_ultimate_pressure",
        label="ultimate_pressure_pa",
    )
    curve = _parse_speed_curve(raw.get("speed_curve"))
    if speed is not None and curve is not None:
        raise TeststandRefusal(
            "ambiguous_pump_speed",
            "pumping_speed_m3_s and speed_curve are both set",
        )
    if curve is not None and ultimate is not None:
        raise TeststandRefusal(
            "ambiguous_pump_ultimate",
            "a speed curve already contains the ultimate; "
            "ultimate_pressure_pa must be null",
        )
    return PumpSpec(
        nominal_speed_m3_s=speed,
        ultimate_pressure_pa=ultimate,
        speed_curve_pa_to_m3_s=curve,
    )


def _parse_speed_curve(raw: Any) -> tuple[tuple[float, float], ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise TeststandRefusal(
            "invalid_speed_curve",
            "speed_curve must be a list of pressure_pa and speed_m3_s",
        )
    if len(raw) == 0:
        return None
    if len(raw) < 2:
        raise TeststandRefusal(
            "invalid_speed_curve",
            "speed_curve needs at least two points",
        )
    points: list[tuple[float, float]] = []
    for index, row in enumerate(raw):
        if not isinstance(row, Mapping):
            raise TeststandRefusal(
                "invalid_speed_curve",
                f"speed_curve[{index}] must be a mapping",
            )
        pressure = _require_non_negative_number(
            row.get("pressure_pa"),
            code="invalid_speed_curve",
            label=f"speed_curve[{index}].pressure_pa",
        )
        speed = _require_non_negative_number(
            row.get("speed_m3_s"),
            code="invalid_speed_curve",
            label=f"speed_curve[{index}].speed_m3_s",
        )
        points.append((pressure, speed))
    for index in range(1, len(points)):
        if points[index][0] <= points[index - 1][0]:
            raise TeststandRefusal(
                "pump_speed_curve_pressure_not_strict",
                "speed_curve pressures must increase",
            )
    return tuple(points)


def _parse_path(
    pump_raw: Mapping[str, Any] | None,
) -> tuple[tuple[ConductanceElement, ...] | None, bool]:
    if not isinstance(pump_raw, Mapping) or "conductance_path" not in pump_raw:
        return None, False
    raw = pump_raw.get("conductance_path")
    if raw is None:
        return None, False
    if isinstance(raw, Mapping):
        if _is_placeholder_path(raw):
            return None, True
        raise TeststandRefusal(
            "conductance_path_not_a_list",
            "conductance_path must be a list of elements",
        )
    if not isinstance(raw, list):
        raise TeststandRefusal(
            "conductance_path_not_a_list",
            "conductance_path must be a list of elements",
        )
    return tuple(_parse_element(index, item) for index, item in enumerate(raw)), False


def _is_placeholder_path(raw: Mapping[str, Any]) -> bool:
    if str(raw.get("kind") or "").strip():
        return False
    geometry_keys = (
        "diameter_m",
        "length_m",
        "open_area_m2",
        "line_diameter_m",
        "line_length_m",
    )
    for key in geometry_keys:
        if _is_strictly_positive_number(raw.get(key)):
            return False
    return "named_missing_fields" in raw


def _parse_element(index: int, raw: Any) -> ConductanceElement:
    if not isinstance(raw, Mapping):
        raise TeststandRefusal(
            "invalid_conductance_element",
            f"conductance_path[{index}] must be a mapping",
        )
    declared = str(raw.get("kind") or "").strip()
    if declared not in _ELEMENT_KINDS:
        raise TeststandRefusal(
            "unknown_conductance_element",
            f"conductance_path[{index}].kind {declared!r} is not certified",
        )
    diameter = _optional_positive_field(
        raw.get("diameter_m"),
        code="invalid_geometry",
        label=f"conductance_path[{index}].diameter_m",
    )
    length = _optional_positive_field(
        raw.get("length_m"),
        code="invalid_geometry",
        label=f"conductance_path[{index}].length_m",
    )
    area = _optional_positive_field(
        raw.get("open_area_m2"),
        code="invalid_geometry",
        label=f"conductance_path[{index}].open_area_m2",
    )
    viscosity = _optional_positive_field(
        raw.get("dynamic_viscosity_pa_s"),
        code="invalid_dynamic_viscosity",
        label=f"conductance_path[{index}].dynamic_viscosity_pa_s",
    )
    allow = _parse_allow_transitional(raw.get("allow_transitional", False), index)
    model, diameter, area = _resolve_element_geometry(
        index,
        declared,
        diameter_m=diameter,
        length_m=length,
        open_area_m2=area,
    )
    return ConductanceElement(
        index=index,
        declared_kind=declared,
        model_kind=model,
        diameter_m=diameter,
        open_area_m2=area,
        length_m=length,
        dynamic_viscosity_pa_s=viscosity,
        allow_transitional=allow,
    )


def _parse_allow_transitional(value: Any, index: int) -> bool:
    if value is False or value is None:
        return False
    if value is True:
        return True
    raise TeststandRefusal(
        "invalid_allow_transitional",
        f"conductance_path[{index}].allow_transitional must be boolean",
    )


def _resolve_element_geometry(
    index: int,
    declared: str,
    *,
    diameter_m: float | None,
    length_m: float | None,
    open_area_m2: float | None,
) -> tuple[str, float, float | None]:
    if declared == "orifice":
        return (
            "orifice",
            *_orifice_area_and_diameter(index, diameter_m, open_area_m2),
        )
    if declared == "tube":
        if diameter_m is None or length_m is None:
            raise TeststandRefusal(
                "missing_tube_geometry",
                f"conductance_path[{index}] tube requires diameter_m and length_m",
            )
        if open_area_m2 is not None:
            raise TeststandRefusal(
                "tube_area_not_a_conductance",
                f"conductance_path[{index}] tube conductance uses diameter and length",
            )
        return "tube", diameter_m, None
    has_tube = diameter_m is not None and length_m is not None
    if has_tube and open_area_m2 is not None:
        circle = math.pi * diameter_m ** 2 / 4.0
        if not _close(open_area_m2, circle):
            raise TeststandRefusal(
                "ambiguous_filter_geometry",
                f"conductance_path[{index}] filter area does not match its bore",
            )
    if has_tube:
        return "tube", diameter_m, open_area_m2
    if open_area_m2 is not None or diameter_m is not None:
        diameter, area = _orifice_area_and_diameter(index, diameter_m, open_area_m2)
        return "orifice", diameter, area
    notice = (
        f"element {index} filter is out of its certified regime: "
        "no pore diameter and length, and no open area, so no conductance "
        "is certified"
    )
    raise TeststandRefusal(
        "filter_conductance_not_specified",
        notice,
        notices=(notice,),
    )


def _orifice_area_and_diameter(
    index: int,
    diameter_m: float | None,
    open_area_m2: float | None,
) -> tuple[float, float]:
    if diameter_m is None and open_area_m2 is None:
        raise TeststandRefusal(
            "missing_orifice_geometry",
            f"conductance_path[{index}] orifice requires diameter_m or open_area_m2",
        )
    if diameter_m is not None and open_area_m2 is not None:
        circle = math.pi * diameter_m ** 2 / 4.0
        if not _close(open_area_m2, circle):
            raise TeststandRefusal(
                "orifice_area_diameter_disagree",
                f"conductance_path[{index}] open area does not match pi D^2/4",
            )
    if diameter_m is None:
        diameter_m = math.sqrt(4.0 * open_area_m2 / math.pi)
    if open_area_m2 is None:
        open_area_m2 = math.pi * diameter_m ** 2 / 4.0
    return diameter_m, open_area_m2


def _require_unique_pump(pump: PumpSpec) -> None:
    has_speed = pump.nominal_speed_m3_s is not None
    has_curve = pump.speed_curve_pa_to_m3_s is not None
    if has_speed and has_curve:
        raise TeststandRefusal(
            "ambiguous_pump_speed",
            "pumping_speed_m3_s and speed_curve are both set",
        )
    if not has_speed and not has_curve:
        raise TeststandRefusal(
            "missing_pump_speed",
            "pumping_speed_m3_s is null and speed_curve is absent",
        )
    if has_speed and pump.ultimate_pressure_pa is None:
        raise TeststandRefusal(
            "missing_ultimate_pressure",
            "a nominal pump speed requires ultimate_pressure_pa, "
            "including an explicit 0",
        )


def _pump_inlet_pressure_pa(pump: PumpSpec, throughput_pa_m3_s: float) -> float:
    if pump.speed_curve_pa_to_m3_s is not None:
        return _curve_inlet_pressure_pa(
            pump.speed_curve_pa_to_m3_s,
            throughput_pa_m3_s,
        )
    speed = pump.nominal_speed_m3_s
    ultimate = pump.ultimate_pressure_pa
    if speed is None or ultimate is None:
        raise TeststandRefusal(
            "missing_pump_speed",
            "nominal pump speed and ultimate pressure are required",
        )
    if throughput_pa_m3_s == 0.0:
        return ultimate
    return ultimate + throughput_pa_m3_s / speed


def _curve_inlet_pressure_pa(
    curve: tuple[tuple[float, float], ...],
    throughput_pa_m3_s: float,
) -> float:
    roots: list[float] = []
    for index in range(len(curve) - 1):
        p0, s0 = curve[index]
        p1, s1 = curve[index + 1]
        d_p = p1 - p0
        d_s = s1 - s0
        a = d_s * d_p
        b = s0 * d_p + d_s * p0
        c = s0 * p0 - throughput_pa_m3_s
        if a == 0.0 and b == 0.0:
            if c == 0.0:
                raise TeststandRefusal(
                    "pump_speed_curve_root_not_unique",
                    "S(P)*P equals the throughput on a whole curve segment",
                )
            continue
        for root in _roots_on_segment(a, b, c, p0, d_p):
            if all(not _close(root, kept) for kept in roots):
                roots.append(root)
    if not roots:
        raise TeststandRefusal(
            "pump_speed_curve_out_of_range",
            "no speed-curve pressure satisfies S(P)*P = Q; the curve is not extended",
        )
    if len(roots) > 1:
        raise TeststandRefusal(
            "pump_speed_curve_root_not_unique",
            "S(P)*P = Q has more than one pressure on the curve",
        )
    return roots[0]


def _roots_on_segment(
    a: float,
    b: float,
    c: float,
    p0: float,
    d_p: float,
) -> tuple[float, ...]:
    if a == 0.0:
        if b == 0.0:
            return ()
        t = -c / b
        return (_segment_pressure(t, p0, d_p),) if _t_in_unit(t) else ()
    discriminant = b * b - 4.0 * a * c
    if discriminant < 0.0 or not math.isfinite(discriminant):
        if discriminant < 0.0:
            return ()
        raise TeststandRefusal(
            "pressure_root_not_converged",
            "the speed-curve quadratic discriminant is not finite",
        )
    sqrt_disc = math.sqrt(discriminant)
    roots: list[float] = []
    for sign in (-1.0, 1.0):
        t = (-b + sign * sqrt_disc) / (2.0 * a)
        if not math.isfinite(t):
            raise TeststandRefusal(
                "pressure_root_not_converged",
                "the speed-curve root is not finite",
            )
        if _t_in_unit(t):
            roots.append(_segment_pressure(t, p0, d_p))
    return tuple(roots)


def _t_in_unit(t: float) -> bool:
    return -1.0e-12 <= t <= 1.0 + 1.0e-12


def _segment_pressure(t: float, p0: float, d_p: float) -> float:
    clamped = min(1.0, max(0.0, t))
    return p0 + clamped * d_p


def _prepare_elements(
    elements: tuple[ConductanceElement, ...],
    *,
    pressure_pa: float,
    species: str | None,
    temperature_K: float,
) -> tuple[_PreparedElement, ...]:
    return tuple(
        _prepare_element(
            element,
            pressure_pa=pressure_pa,
            species=species,
            temperature_K=temperature_K,
        )
        for element in elements
    )


def _prepare_element(
    element: ConductanceElement,
    *,
    pressure_pa: float,
    species: str | None,
    temperature_K: float,
) -> _PreparedElement:
    if species is None:
        raise TeststandRefusal(
            "missing_balance_species",
            "a conductance element requires background_gas.species",
        )
    probed = _transport(
        single_species_mean_free_path,
        species,
        pressure_pa,
        temperature_K,
        element.diameter_m,
    )
    regime = probed.regime
    knudsen = probed.knudsen_number
    if element.model_kind == "orifice":
        return _prepare_orifice(element, regime, knudsen, temperature_K, species)
    return _prepare_tube(
        element,
        regime,
        knudsen,
        pressure_pa=pressure_pa,
        temperature_K=temperature_K,
        species=species,
    )


def _prepare_orifice(
    element: ConductanceElement,
    regime: KnudsenRegime,
    knudsen: float,
    temperature_K: float,
    species: str,
) -> _PreparedElement:
    if regime is not KnudsenRegime.FREE_MOLECULAR:
        notice = _regime_notice(
            element,
            knudsen,
            regime,
            f"free-molecular orifice conductance (Kn>={FREE_MOLECULAR_KNUDSEN_MIN:g})",
        )
        raise TeststandRefusal(
            "orifice_out_of_certified_regime",
            notice,
            notices=(notice,),
        )
    from simulator.transport_regime import MOLAR_MASSES_KG_PER_MOL

    conductance = _transport(
        molecular_aperture_conductance_m3_s,
        element.open_area_m2,
        temperature_K,
        MOLAR_MASSES_KG_PER_MOL[species],
        knudsen_number=knudsen,
    )
    return _prepared(
        element,
        formula="molecular",
        regime=regime.value,
        knudsen_number=knudsen,
        conductance_m3_s=conductance,
    )


def _prepare_tube(
    element: ConductanceElement,
    regime: KnudsenRegime,
    knudsen: float,
    *,
    pressure_pa: float,
    temperature_K: float,
    species: str,
) -> _PreparedElement:
    length_m = element.length_m
    if length_m is None:
        raise TeststandRefusal(
            "missing_tube_geometry",
            f"element {element.index} tube requires length_m",
        )
    if regime is KnudsenRegime.VISCOUS:
        viscosity = _require_viscosity(element)
        coefficient = _poiseuille_k(element, pressure_pa, viscosity)
        return _prepared(
            element,
            formula="poiseuille",
            regime=regime.value,
            knudsen_number=knudsen,
            poiseuille_k_m3_s_per_pa=coefficient,
            viscosity_pa_s=viscosity,
        )
    if regime is KnudsenRegime.FREE_MOLECULAR:
        length_over_diameter = length_m / element.diameter_m
        if length_over_diameter < LONG_TUBE_L_OVER_D_MIN:
            notice = (
                f"element {element.index} {element.declared_kind} is out of its "
                f"certified regime: L/D={length_over_diameter:.6g} is below "
                f"{LONG_TUBE_L_OVER_D_MIN:g}, and the short-tube Clausing factor "
                "is not certified"
            )
            raise TeststandRefusal(
                "clausing_short_tube_not_certified",
                notice,
                notices=(notice,),
            )
        from simulator.transport_regime import MOLAR_MASSES_KG_PER_MOL

        conductance = _transport(
            long_tube_molecular_conductance_m3_s,
            element.diameter_m,
            length_m,
            temperature_K,
            MOLAR_MASSES_KG_PER_MOL[species],
            knudsen_number=knudsen,
        )
        return _prepared(
            element,
            formula="molecular",
            regime=regime.value,
            knudsen_number=knudsen,
            conductance_m3_s=conductance,
        )
    notice = _regime_notice(
        element,
        knudsen,
        regime,
        "transitional tube conductance only with allow_transitional true",
    )
    if not element.allow_transitional:
        raise TeststandRefusal(
            "transitional_conductance_not_authorized",
            notice,
            notices=(notice,),
        )
    viscosity = _require_viscosity(element)
    coefficient = _poiseuille_k(element, pressure_pa, viscosity)
    rarefaction = _transport(
        beskok_karniadakis_rarefaction_factor,
        knudsen,
    )
    return _prepared(
        element,
        formula="bk",
        regime=regime.value,
        knudsen_number=knudsen,
        poiseuille_k_m3_s_per_pa=coefficient,
        rarefaction=rarefaction,
        viscosity_pa_s=viscosity,
    )


def _prepared(
    element: ConductanceElement,
    *,
    formula: str,
    regime: str,
    knudsen_number: float,
    conductance_m3_s: float | None = None,
    poiseuille_k_m3_s_per_pa: float | None = None,
    rarefaction: float | None = None,
    viscosity_pa_s: float | None = None,
) -> _PreparedElement:
    return _PreparedElement(
        index=element.index,
        declared_kind=element.declared_kind,
        model_kind=element.model_kind,
        formula=formula,
        regime=regime,
        knudsen_number=knudsen_number,
        diameter_m=element.diameter_m,
        conductance_m3_s=conductance_m3_s,
        poiseuille_k_m3_s_per_pa=poiseuille_k_m3_s_per_pa,
        rarefaction=rarefaction,
        viscosity_pa_s=viscosity_pa_s if viscosity_pa_s is not None else element.dynamic_viscosity_pa_s,
        length_m=element.length_m,
    )


def _require_viscosity(element: ConductanceElement) -> float:
    if element.dynamic_viscosity_pa_s is None:
        raise TeststandRefusal(
            "missing_dynamic_viscosity",
            f"element {element.index} {element.declared_kind} requires "
            "dynamic_viscosity_pa_s; air viscosity is not assumed",
        )
    return element.dynamic_viscosity_pa_s


def _poiseuille_k(
    element: ConductanceElement,
    pressure_pa: float,
    viscosity_pa_s: float,
) -> float:
    reference_pa = pressure_pa if pressure_pa > 0.0 else 1.0
    conductance = _transport(
        poiseuille_conductance_m3_s,
        element.diameter_m,
        element.length_m,
        reference_pa,
        viscosity_pa_s,
    )
    return conductance / reference_pa


def _chamber_pressure_pa(
    inlet_pa: float,
    throughput_pa_m3_s: float,
    prepared: tuple[_PreparedElement, ...],
    *,
    species: str | None,
    temperature_K: float,
) -> tuple[float, tuple[_PreparedElement, ...]]:
    if not prepared:
        return inlet_pa, prepared
    if not any(element.formula == "bk" for element in prepared):
        chamber_pa, ends = _walk_upstream(inlet_pa, throughput_pa_m3_s, prepared)
        _recheck_regimes(
            prepared,
            ends,
            species=species,
            temperature_K=temperature_K,
        )
        return chamber_pa, prepared
    current = prepared
    previous = inlet_pa
    for _ in range(_BK_PASS_LIMIT):
        chamber_pa, ends = _walk_upstream(inlet_pa, throughput_pa_m3_s, current)
        current = _retune_bk(
            current,
            ends,
            species=species,
            temperature_K=temperature_K,
        )
        if abs(chamber_pa - previous) <= _SETTLE_RELATIVE * max(1.0, abs(chamber_pa)):
            _recheck_regimes(
                current,
                ends,
                species=species,
                temperature_K=temperature_K,
            )
            return chamber_pa, current
        previous = chamber_pa
    raise TeststandRefusal(
        "pressure_root_not_converged",
        "the transitional fixed point did not settle within "
        f"{_BK_PASS_LIMIT} passes",
    )


def _walk_upstream(
    inlet_pa: float,
    throughput_pa_m3_s: float,
    prepared: tuple[_PreparedElement, ...],
) -> tuple[float, tuple[tuple[float, float], ...]]:
    pressure = inlet_pa
    ends_rev: list[tuple[float, float]] = []
    for element in reversed(prepared):
        upstream = _upstream_pressure(pressure, throughput_pa_m3_s, element)
        ends_rev.append((upstream, pressure))
        pressure = upstream
    ends = tuple(reversed(ends_rev))
    return pressure, ends


def _upstream_pressure(
    downstream_pa: float,
    throughput_pa_m3_s: float,
    element: _PreparedElement,
) -> float:
    if throughput_pa_m3_s == 0.0:
        return downstream_pa
    if element.formula == "molecular":
        if element.conductance_m3_s is None or element.conductance_m3_s <= 0.0:
            raise TeststandRefusal(
                "invalid_conductance",
                f"element {element.index} conductance is not positive",
            )
        return downstream_pa + throughput_pa_m3_s / element.conductance_m3_s
    coefficient = element.poiseuille_k_m3_s_per_pa
    rarefaction = 1.0 if element.rarefaction is None else element.rarefaction
    if coefficient is None or coefficient <= 0.0 or rarefaction <= 0.0:
        raise TeststandRefusal(
            "invalid_conductance",
            f"element {element.index} Poiseuille coefficient is not positive",
        )
    return math.sqrt(
        downstream_pa ** 2
        + 2.0 * throughput_pa_m3_s / (coefficient * rarefaction)
    )


def _retune_bk(
    prepared: tuple[_PreparedElement, ...],
    ends: tuple[tuple[float, float], ...],
    *,
    species: str | None,
    temperature_K: float,
) -> tuple[_PreparedElement, ...]:
    retuned: list[_PreparedElement] = []
    for element, (upstream, downstream) in zip(prepared, ends):
        if element.formula != "bk":
            retuned.append(element)
            continue
        mean_pa = _certification_pressure(element.model_kind, upstream, downstream)
        regime, knudsen = _regime_at(
            species,
            mean_pa,
            temperature_K,
            element.diameter_m,
        )
        if regime is not KnudsenRegime.TRANSITIONAL:
            notice = _regime_notice(
                element,
                knudsen,
                regime,
                "the transitional law chosen at the pump-only pressure",
            )
            raise TeststandRefusal(
                "regime_changed_between_guess_and_solution",
                notice,
                notices=(notice,),
            )
        rarefaction = _transport(beskok_karniadakis_rarefaction_factor, knudsen)
        retuned.append(
            _PreparedElement(
                index=element.index,
                declared_kind=element.declared_kind,
                model_kind=element.model_kind,
                formula=element.formula,
                regime=regime.value,
                knudsen_number=knudsen,
                diameter_m=element.diameter_m,
                conductance_m3_s=element.conductance_m3_s,
                poiseuille_k_m3_s_per_pa=element.poiseuille_k_m3_s_per_pa,
                rarefaction=rarefaction,
                viscosity_pa_s=element.viscosity_pa_s,
                length_m=element.length_m,
            )
        )
    return tuple(retuned)


def _recheck_regimes(
    prepared: tuple[_PreparedElement, ...],
    ends: tuple[tuple[float, float], ...],
    *,
    species: str | None,
    temperature_K: float,
) -> None:
    for element, (upstream, downstream) in zip(prepared, ends):
        mean_pa = _certification_pressure(element.model_kind, upstream, downstream)
        regime, knudsen = _regime_at(
            species,
            mean_pa,
            temperature_K,
            element.diameter_m,
        )
        if regime.value != element.regime:
            notice = _regime_notice(
                element,
                knudsen,
                regime,
                f"the {element.formula} law frozen at the pump-only pressure",
            )
            raise TeststandRefusal(
                "regime_changed_between_guess_and_solution",
                notice,
                notices=(notice,),
            )


def _certification_pressure(model_kind: str, upstream_pa: float, downstream_pa: float) -> float:
    if model_kind == "orifice":
        return max(upstream_pa, downstream_pa)
    return 0.5 * (upstream_pa + downstream_pa)


def _regime_at(
    species: str | None,
    pressure_pa: float,
    temperature_K: float,
    diameter_m: float,
) -> tuple[KnudsenRegime, float]:
    if species is None:
        raise TeststandRefusal(
            "missing_balance_species",
            "regime classification requires a carrier species",
        )
    probed = _transport(
        single_species_mean_free_path,
        species,
        pressure_pa,
        temperature_K,
        diameter_m,
    )
    return probed.regime, probed.knudsen_number


def _effective_speed_m3_s(
    pump: PumpSpec,
    prepared: tuple[_PreparedElement, ...],
    throughput_pa_m3_s: float,
    chamber_pa: float,
) -> float:
    if pump.nominal_speed_m3_s is not None and all(
        element.formula in _FORMULAS_CONSTANT for element in prepared
    ):
        speed = pump.nominal_speed_m3_s
        for element in prepared:
            speed = series_effective_speed_m3_s(speed, element.conductance_m3_s)
        return speed
    ultimate = 0.0 if pump.ultimate_pressure_pa is None else pump.ultimate_pressure_pa
    if chamber_pa > ultimate and throughput_pa_m3_s > 0.0:
        return throughput_pa_m3_s / (chamber_pa - ultimate)
    if pump.nominal_speed_m3_s is not None:
        return pump.nominal_speed_m3_s
    if pump.speed_curve_pa_to_m3_s is not None:
        return _curve_speed_m3_s(pump.speed_curve_pa_to_m3_s, chamber_pa)
    raise TeststandRefusal(
        "missing_pump_speed",
        "no pump speed is available for the effective-speed report",
    )


def _curve_speed_m3_s(
    curve: tuple[tuple[float, float], ...],
    pressure_pa: float,
) -> float:
    if pressure_pa <= curve[0][0]:
        return curve[0][1]
    for (p0, s0), (p1, s1) in zip(curve, curve[1:]):
        if pressure_pa <= p1:
            span = p1 - p0
            if span == 0.0:
                return s1
            return s0 + (pressure_pa - p0) / span * (s1 - s0)
    return curve[-1][1]


def _analytic_pressure_pa(
    apparatus: TeststandApparatus,
    *,
    pump: PumpSpec,
    prepared: tuple[_PreparedElement, ...],
    s_eff_m3_s: float,
    dt_s: float,
    pressure_pa: float | None,
    steady_pressure_pa: float,
    species: str | None,
    temperature_K: float,
) -> float:
    if pump.speed_curve_pa_to_m3_s is not None or any(
        element.formula in _FORMULAS_PRESSURE_DEPENDENT for element in prepared
    ):
        raise TeststandRefusal(
            "analytic_form_requires_pressure_independent_speed",
            "the exponential update needs a constant S_eff; use steady_state",
        )
    if apparatus.chamber_volume_m3 is None:
        raise TeststandRefusal(
            "missing_chamber_volume",
            "the analytic update requires chamber.volume_m3",
        )
    if pressure_pa is None:
        raise TeststandRefusal(
            "missing_initial_pressure",
            "the analytic update requires pressure_pa",
        )
    initial_pa = _require_non_negative_number(
        pressure_pa,
        code="invalid_initial_pressure",
        label="pressure_pa",
    )
    if not math.isfinite(s_eff_m3_s) or s_eff_m3_s <= 0.0:
        raise TeststandRefusal(
            "invalid_pump_speed",
            "the analytic update requires a positive S_eff",
        )
    # steady_pressure_pa is P_ult + Q/S_eff for this constant-S case.
    decay = math.exp(-s_eff_m3_s * dt_s / apparatus.chamber_volume_m3)
    updated_pa = steady_pressure_pa + (initial_pa - steady_pressure_pa) * decay
    _refuse_if_chamber_regime_leaves(
        prepared,
        (initial_pa, updated_pa),
        species=species,
        temperature_K=temperature_K,
    )
    return updated_pa


def _refuse_if_chamber_regime_leaves(
    prepared: tuple[_PreparedElement, ...],
    pressures_pa: tuple[float, ...],
    *,
    species: str | None,
    temperature_K: float,
) -> None:
    for probe_pa in pressures_pa:
        for element in prepared:
            regime, knudsen = _regime_at(
                species,
                probe_pa,
                temperature_K,
                element.diameter_m,
            )
            if regime.value != element.regime:
                notice = _regime_notice(
                    element,
                    knudsen,
                    regime,
                    f"the {element.formula} law frozen at the pump-only pressure",
                )
                raise TeststandRefusal(
                    "regime_changed_between_guess_and_solution",
                    notice,
                    notices=(notice,),
                )


def _regime_notice(
    element: ConductanceElement | _PreparedElement,
    knudsen: float,
    regime: KnudsenRegime,
    certified: str,
) -> str:
    kind = element.declared_kind
    return (
        f"element {element.index} {kind} is out of its certified regime: "
        f"Kn={knudsen:.6g} is {regime.value}; certified law is {certified}"
    )


def _transport(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except TransportRegimeRefusal as exc:
        raise TeststandRefusal(exc.category, str(exc)) from exc


def _carrier_species(raw: Any) -> str | None:
    if not isinstance(raw, Mapping):
        return None
    if str(raw.get("reported_status") or "").strip() == "not_reported":
        return None
    species = str(raw.get("species") or "").strip()
    return species or None


def _carrier_mol_per_s(raw: Any) -> float | None:
    if not isinstance(raw, Mapping):
        return None
    if str(raw.get("reported_status") or "").strip() == "not_reported":
        return None
    if raw.get("value") is None:
        return None
    unit = raw.get("unit")
    if unit != FLOW_UNIT_NL_MIN:
        raise TeststandRefusal(
            "unsupported_flow_unit",
            f"imposed_flow unit {unit!r} is not {FLOW_UNIT_NL_MIN}",
        )
    flow = _require_non_negative_number(
        raw.get("value"),
        code="invalid_carrier_flow",
        label="imposed_flow.value",
    )
    return normal_litres_per_minute_to_mol_per_s(flow)


def _geometry_mapping(geometry: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if geometry is None:
        return {}
    if not isinstance(geometry, Mapping):
        raise TeststandRefusal(
            "invalid_lab_geometry",
            "lab_geometry must be a mapping",
        )
    return geometry


def _pump_mapping(geometry: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    raw = _geometry_mapping(geometry).get("pump")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TeststandRefusal("invalid_pump", "lab_geometry.pump must be a mapping")
    return raw


def _mapping(raw: Any, *, code: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise TeststandRefusal(code, code)
    return raw


def _optional_mapping(raw: Any) -> Mapping[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TeststandRefusal("invalid_lab_geometry", "expected a mapping")
    return raw


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_positive_field(value: Any, *, code: str, label: str) -> float | None:
    if value is None:
        return None
    return _require_positive_number(value, code=code, label=label)


def _optional_non_negative_field(value: Any, *, code: str, label: str) -> float | None:
    if value is None:
        return None
    return _require_non_negative_number(value, code=code, label=label)


def _require_positive_number(value: Any, *, code: str, label: str) -> float:
    number = _require_real(value, code=code, label=label)
    if number <= 0.0:
        raise TeststandRefusal(code, f"{label} must be finite and > 0")
    return number


def _require_non_negative_number(value: Any, *, code: str, label: str) -> float:
    number = _require_real(value, code=code, label=label)
    if number < 0.0:
        raise TeststandRefusal(code, f"{label} must be finite and >= 0")
    return number


def _require_real(value: Any, *, code: str, label: str) -> float:
    if not is_declared_real_scalar(value, allow_numeric_str=False):
        raise TeststandRefusal(code, f"{label} must be a real number")
    number = float(value)
    if not math.isfinite(number):
        raise TeststandRefusal(code, f"{label} must be finite")
    return number


def _is_strictly_positive_number(value: Any) -> bool:
    if not is_declared_real_scalar(value, allow_numeric_str=False):
        return False
    number = float(value)
    return math.isfinite(number) and number > 0.0


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= 1.0e-9 * max(1.0, abs(left), abs(right))
