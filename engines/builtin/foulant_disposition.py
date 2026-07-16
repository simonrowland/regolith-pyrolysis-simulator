"""Pure foulant-disposition math for Stage-0 carriers.

Stateless helper: computes extents and splits only. Never mutates the ledger,
never builds LedgerTransitionProposal, never imports melt_backend or inventory.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

GAS_CONSTANT_J_PER_MOL_K = 8.314462618
PA_PER_BAR = 100_000.0
UNGROUNDABLE_PROCESS_EXTENT = "UNGROUNDABLE_PROCESS_EXTENT"
NOT_SPECIFIED = "not_speciated"
_NUMERICAL_PO2_GUARD_BAR = 1.0e-15

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_VAPOR_PRESSURES_PATH = _REPO_ROOT / "data" / "vapor_pressures.yaml"


@dataclass(frozen=True)
class EscapeSplit:
    escaped_frac: float
    retained_frac: float
    confidence: str = "partly_grounded"
    status: str = "ok"
    warning: str | None = None


@dataclass(frozen=True)
class DispositionExtent:
    extent: float
    confidence: str
    onset_K: float
    path: str


@dataclass(frozen=True)
class DispositionInterval:
    low: float
    high: float
    certified_point: float | None
    reason: str | None = None


@dataclass(frozen=True)
class FoulantCarrierEntry:
    carrier_key: str
    species: str
    aliases: tuple[str, ...]
    molar_mass_g_mol: float | None
    group: str
    reaction_family: str
    fate: Mapping[str, Any]
    gating: Mapping[str, Any]
    thermo: Mapping[str, Any]
    warning_flags: Mapping[str, Any]


@dataclass(frozen=True)
class FoulantRegistry:
    carriers: Mapping[str, FoulantCarrierEntry]
    alias_to_carrier: Mapping[str, str]
    foulant_dG: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CarbonPartition:
    labile_mol: float | str
    refractory_mol: float | str
    carbonate_mol: float | str
    process_reductant_mol: float | str
    not_speciated: tuple[str, ...] = ()
    refractory_fraction_interval: tuple[float, float] | None = None


def _resolve_carrier_key(carrier: str, registry: FoulantRegistry | None) -> str:
    if registry is None:
        return carrier
    alias_map = registry.alias_to_carrier
    return alias_map.get(carrier, alias_map.get(carrier.lower(), carrier))


def _pure_component_antoine_pa(entry: Mapping[str, Any], temperature_K: float) -> float:
    coeff = entry.get("pure_component_antoine") or entry.get("antoine")
    if not coeff:
        raise KeyError("pure_component_antoine")
    log_p = float(coeff["A"]) - float(coeff["B"]) / (
        temperature_K + float(coeff.get("C", 0.0))
    )
    return 10.0**log_p


def _uncertified_foulant_warning(carrier_key: str) -> str:
    return f"{carrier_key} foulant volatilization uncertified - not modeled"


def _valid_range_K(entry: Mapping[str, Any]) -> tuple[float, float] | None:
    raw_range = entry.get("valid_range_K")
    if raw_range is None:
        coeff = entry.get("pure_component_antoine") or entry.get("antoine") or {}
        raw_range = coeff.get("valid_range_K")
    if not isinstance(raw_range, Sequence) or isinstance(raw_range, (str, bytes)):
        return None
    if len(raw_range) != 2:
        return None
    low = float(raw_range[0])
    high = float(raw_range[1])
    if not math.isfinite(low) or not math.isfinite(high) or low > high:
        return None
    return low, high


def _temperature_range_warning(
    carrier_key: str,
    temperature_K: float,
    valid_range: tuple[float, float] | None,
) -> str | None:
    if valid_range is None:
        return None
    low, high = valid_range
    if low <= temperature_K <= high:
        return None
    return (
        f"{carrier_key} foulant volatilization at {temperature_K:.2f} K is "
        f"outside valid_range_K [{low:g}, {high:g}]; "
        "salt/halide result is non-authoritative extrapolation"
    )


def _load_vapor_pressures(path: Path | None = None) -> Mapping[str, Any]:
    yaml_path = path or _DEFAULT_VAPOR_PRESSURES_PATH
    with yaml_path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def chi_escape_salt(
    carrier: str,
    T_C: float,
    p_overhead_bar: float,
    registry: FoulantRegistry | None = None,
) -> EscapeSplit:
    """Equilibrium vapor escape fraction: chi = P_sat / (P_sat + P_total)."""
    carrier_key = _resolve_carrier_key(carrier, registry)
    vapor_data = _load_vapor_pressures()
    foulant_vapor = vapor_data.get("foulant_vapor", {}) or {}
    entry = foulant_vapor.get(carrier_key)
    if entry is None:
        raise KeyError(f"foulant_vapor row missing for carrier {carrier_key!r}")

    if entry.get("interval_required"):
        return EscapeSplit(
            escaped_frac=0.0,
            retained_frac=1.0,
            confidence="interval_only",
            status="uncertified",
            warning=_uncertified_foulant_warning(carrier_key),
        )

    temperature_K = float(T_C) + 273.15
    warning = _temperature_range_warning(
        carrier_key,
        temperature_K,
        _valid_range_K(entry),
    )
    p_sat_pa = _pure_component_antoine_pa(entry, temperature_K)
    p_total_pa = float(p_overhead_bar) * PA_PER_BAR
    if p_sat_pa < 0.0 or p_total_pa < 0.0:
        raise ValueError("pressures must be non-negative")

    denom = p_sat_pa + p_total_pa
    if denom <= 0.0:
        escaped = 0.0
    else:
        escaped = p_sat_pa / denom
    escaped = min(max(escaped, 0.0), 1.0)
    return EscapeSplit(
        escaped_frac=escaped,
        retained_frac=1.0 - escaped,
        confidence="extrapolated" if warning else "partly_grounded",
        status="out_of_range" if warning else "ok",
        warning=warning,
    )


def _validate_dg_points(dg_points: Sequence[tuple[float, float]]) -> None:
    if len(dg_points) < 2:
        raise ValueError("dG table needs at least two points")
    seen_t: set[float] = set()
    for t_k, dg_kj in dg_points:
        if not math.isfinite(t_k) or not math.isfinite(dg_kj):
            raise ValueError("dG points must be finite")
        if t_k in seen_t:
            raise ValueError(f"duplicate dG temperature {t_k}")
        seen_t.add(t_k)


def _interpolate_onset_K(dg_points: Sequence[tuple[float, float]]) -> float:
    _validate_dg_points(dg_points)
    ordered = sorted(dg_points, key=lambda row: row[0])
    for (t_lo, dg_lo), (t_hi, dg_hi) in zip(ordered, ordered[1:]):
        if dg_lo == 0.0:
            return t_lo
        if dg_hi == 0.0:
            return t_hi
        if dg_lo * dg_hi < 0.0:
            frac = abs(dg_lo) / (abs(dg_lo) + abs(dg_hi))
            return t_lo + frac * (t_hi - t_lo)
    raise ValueError("dG table has no zero crossing")


def _dg_slope_kj_per_mol_k(
    dg_points: Sequence[tuple[float, float]],
    onset_K: float,
) -> float:
    ordered = sorted(dg_points, key=lambda row: row[0])
    nearest = sorted(ordered, key=lambda row: abs(row[0] - onset_K))[:2]
    if len(nearest) < 2:
        raise ValueError("dG table needs at least two points near onset")
    (t0, dg0), (t1, dg1) = sorted(nearest, key=lambda row: row[0])
    if math.isclose(t1, t0):
        raise ValueError("degenerate dG temperature spacing")
    return (dg1 - dg0) / (t1 - t0)


def _derive_sigmoid_width_C(
    dg_points: Sequence[tuple[float, float]],
    onset_K: float,
    *,
    activity_ratio: float = math.e,
) -> float:
    slope_kj = _dg_slope_kj_per_mol_k(dg_points, onset_K)
    slope_j = slope_kj * 1000.0
    if math.isclose(slope_j, 0.0):
        raise ValueError("cannot derive sigmoid width from zero dG slope")
    # Premise: near dG=0, dG(T) ~= (dG/dT)(T - T_onset).
    # Algebra: one thermodynamic e-fold has |dG| = R*T*ln(e), so the
    # logistic scale is R*T/|dG/dT|, not the full two-sided interval.
    # Unit check: (J/mol)/(J/mol/K) = K. Sanity: +/- one returned width
    # maps to logistic odds e:+1 and e:-1 respectively.
    delta_g_j = abs(GAS_CONSTANT_J_PER_MOL_K * onset_K * math.log(activity_ratio))
    return delta_g_j / abs(slope_j)


def _parse_dg_points(dg_row: Mapping[str, Any]) -> list[tuple[float, float]]:
    points = dg_row.get("points")
    if not points:
        raise KeyError("dG row missing points")
    parsed: list[tuple[float, float]] = []
    for point in points:
        parsed.append((float(point["T_K"]), float(point["dG_kJ_per_mol"])))
    _validate_dg_points(parsed)
    return parsed


def _product_o2_stoich(reaction: object) -> float:
    if not isinstance(reaction, str) or not reaction.strip():
        raise ValueError("dG row reaction must be a non-empty string")
    sides = reaction.split("->")
    if len(sides) != 2:
        raise ValueError(f"dG row reaction must contain one '->': {reaction!r}")
    product_o2 = Fraction(0)
    for term in sides[1].split("+"):
        tokens = term.strip().split()
        if not tokens or tokens[-1] != "O2":
            continue
        if len(tokens) == 1:
            coefficient = Fraction(1)
        elif len(tokens) == 2:
            try:
                coefficient = Fraction(tokens[0])
            except (ValueError, ZeroDivisionError) as exc:
                raise ValueError(
                    f"invalid O2 coefficient in dG row reaction {reaction!r}"
                ) from exc
        else:
            raise ValueError(
                f"invalid O2 product term {term.strip()!r} in reaction {reaction!r}"
            )
        if coefficient < 0:
            raise ValueError(f"O2 product coefficient must be non-negative: {reaction!r}")
        product_o2 += coefficient
    return float(product_o2)


def _sigmoid_extent(t_c: float, onset_c: float, width_c: float) -> float:
    if width_c <= 0.0:
        raise ValueError("sigmoid width must be positive")
    x = (t_c - onset_c) / width_c
    if x > 100.0:
        return 1.0
    if x < -100.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def chi_decomp(
    carrier: str,
    T_C: float,
    pX_bar: float,
    reagent_C_available_mol: float,
    registry: FoulantRegistry,
) -> DispositionExtent:
    """Sigmoid decomposition extent from JANAF dG=0 onset; width derived from slope."""
    carrier_key = _resolve_carrier_key(carrier, registry)
    entry = registry.carriers.get(carrier_key)
    if entry is None:
        raise KeyError(f"unknown foulant carrier {carrier_key!r}")

    try:
        p_o2_bar = float(pX_bar)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"pX_bar must be numeric, got {pX_bar!r}") from exc
    if not math.isfinite(p_o2_bar) or p_o2_bar < 0.0:
        raise ValueError(f"pX_bar must be finite and non-negative, got {pX_bar!r}")

    gating = entry.gating
    if gating.get("chi_model") != "dG_sigmoid":
        raise ValueError(
            f"carrier {carrier_key!r} gating.chi_model is not dG_sigmoid "
            f"({gating.get('chi_model')!r})"
        )

    dG_pointer = entry.thermo.get("dG_row")
    if not dG_pointer:
        raise ValueError(f"carrier {carrier_key!r} missing thermo.dG_row")

    dg_key = str(dG_pointer).split(".", 1)[-1]
    dg_row = registry.foulant_dG.get(dg_key)
    if dg_row is None:
        raise KeyError(f"dG row {dg_key!r} missing from registry")

    dg_points = _parse_dg_points(dg_row)
    thermal_onset_k = _interpolate_onset_K(dg_points)
    thermal_onset_c = thermal_onset_k - 273.15
    width_c = _derive_sigmoid_width_C(dg_points, thermal_onset_k)

    requires_reagent = gating.get("requires_reagent")
    carb_onset_c = gating.get("carbothermic_onset_C")
    reagent_mol = float(reagent_C_available_mol)
    onset_c = thermal_onset_c
    reported_onset_k = thermal_onset_k
    if reagent_mol > 0.0 and carb_onset_c is not None:
        path = "carbothermic"
        onset_c = float(carb_onset_c)
        reported_onset_k = onset_c + 273.15
    elif requires_reagent == "C":
        path = "carbothermic"
        if reagent_mol <= 0.0:
            return DispositionExtent(
                extent=0.0,
                confidence=str(entry.warning_flags.get("confidence", "partly_grounded")),
                onset_K=reported_onset_k,
                path=path,
            )
        if carb_onset_c is not None:
            onset_c = float(carb_onset_c)
            reported_onset_k = onset_c + 273.15
    else:
        path = "thermal"

    o2_dependence = gating.get("o2_dependence")
    if o2_dependence == "suppresses" and path == "thermal":
        p_ref_bar = float(gating.get("o2_reference_bar", 0.2))
        if not math.isfinite(p_ref_bar) or p_ref_bar <= 0.0:
            raise ValueError("o2_reference_bar must be finite and positive")
        nu_o2 = _product_o2_stoich(dg_row.get("reaction"))
        # For A(s) -> B(s) + gases + nu_O2 O2,
        # dG = dG_ref + nu_O2*R*T*ln(pO2/p_ref). Linearizing dG_ref at
        # its zero crossing gives DeltaT = nu_O2*(R*T/|dG/dT|)*ln(...),
        # hence nu_O2*width below; no extra gas-mole divisor belongs here.
        # Zero is a commanded vacuum setpoint. Use a purely numerical guard
        # below every physical body floor (asteroid 1e-14 bar is the smallest),
        # so all physical pressures pass through unclamped while log(0) cannot.
        effective_p_o2_bar = max(p_o2_bar, _NUMERICAL_PO2_GUARD_BAR)
        onset_c += nu_o2 * width_c * math.log(effective_p_o2_bar / p_ref_bar)
        reported_onset_k = onset_c + 273.15

    extent = _sigmoid_extent(float(T_C), onset_c, width_c)

    if o2_dependence == "requires" and p_o2_bar <= 0.0:
        extent = 0.0

    confidence = str(entry.warning_flags.get("confidence", "partly_grounded"))
    return DispositionExtent(
        extent=min(max(extent, 0.0), 1.0),
        confidence=confidence,
        onset_K=reported_onset_k,
        path=path,
    )


_REFRACTORY_SCENARIOS: dict[str, dict[str, float | None]] = {
    "exposed_fine_powder_air_TGA": {
        "low": 0.9,
        "high": 1.0,
        "certified_point": None,
    },
    "graphite_process_reductant_ERE": {
        "low": 0.5,
        "high": 1.0,
        "certified_point": None,
    },
}


def chi_refractory(
    T_t_profile: Sequence[tuple[float, float]],
    pO2: float,
    scenario: str | None,
) -> DispositionInterval:
    """Refractory-C burnout interval; ungrounded unless a named scenario matches."""
    del T_t_profile, pO2  # integrated kinetics land in C5; skeleton uses scenario bands.

    if scenario == "certified_point":
        raise ValueError(
            "certified-point refractory request refused without a named grounded scenario"
        )

    if scenario is None or scenario not in _REFRACTORY_SCENARIOS:
        return DispositionInterval(
            low=0.0,
            high=1.0,
            certified_point=None,
            reason=UNGROUNDABLE_PROCESS_EXTENT,
        )

    row = _REFRACTORY_SCENARIOS[scenario]
    return DispositionInterval(
        low=float(row["low"]),
        high=float(row["high"]),
        certified_point=row["certified_point"],
        reason=None,
    )


def _fraction_from_row(row: Mapping[str, Any] | None, *, key: str) -> float | str:
    if row is None:
        return NOT_SPECIFIED
    if row.get("status") == NOT_SPECIFIED or row.get("value") is None:
        return NOT_SPECIFIED
    return float(row[key])


def refractory_fraction_interval(
    source_row: Mapping[str, Any],
) -> tuple[float, float] | None:
    refractory_row = source_row.get("f_refractory_organic_C", {}) or {}
    interval = refractory_row.get("interval")
    if not isinstance(interval, Sequence) or isinstance(interval, (str, bytes)):
        return None
    if len(interval) != 2:
        return None
    low = float(interval[0])
    high = float(interval[1])
    if not (0.0 <= low <= high <= 1.0):
        raise ValueError("refractory interval must satisfy 0 <= low <= high <= 1")
    return low, high


def partition_carbon(
    carrier: str,
    declared_mol: float,
    source_row: Mapping[str, Any],
) -> CarbonPartition:
    """Sephton-anchored organic-C partition; missing shares stay not_speciated."""
    del carrier
    total = float(declared_mol)
    if total < 0.0:
        raise ValueError("declared_mol must be non-negative")

    markers: list[str] = []

    refractory_row = source_row.get("f_refractory_organic_C", {})
    carbonate_row = source_row.get("f_carbonate_C", {})
    process_row = source_row.get("f_process_reductant_C", {})

    if refractory_row.get("floor") is not None:
        f_refractory = float(refractory_row["floor"])
    elif refractory_row.get("iom_anchor") is not None:
        f_refractory = float(refractory_row["iom_anchor"])
    else:
        f_refractory = NOT_SPECIFIED
        markers.append("f_refractory_organic_C")

    f_carbonate = _fraction_from_row(carbonate_row, key="value")
    if f_carbonate == NOT_SPECIFIED:
        markers.append("f_carbonate_C")

    f_process = _fraction_from_row(process_row, key="value")
    f_refractory_interval = refractory_fraction_interval(source_row)

    named_fractions = {
        "f_refractory_organic_C": f_refractory,
        "f_carbonate_C": f_carbonate,
        "f_process_reductant_C": f_process,
    }
    known_fractions: list[float] = []
    for name, fraction in named_fractions.items():
        if fraction == NOT_SPECIFIED:
            continue
        value = float(fraction)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be finite and within [0, 1]")
        known_fractions.append(value)
    if sum(known_fractions) > 1.0 + 1e-12:
        raise ValueError("carbon partition fractions exceed declared carbon")

    if f_refractory == NOT_SPECIFIED:
        refractory_mol = NOT_SPECIFIED
        labile_mol = NOT_SPECIFIED
    else:
        refractory_mol = total * float(f_refractory)
        labile_mol = max(total - refractory_mol, 0.0)
        if f_carbonate != NOT_SPECIFIED:
            labile_mol = max(labile_mol - total * float(f_carbonate), 0.0)
        if f_process != NOT_SPECIFIED:
            labile_mol = max(labile_mol - total * float(f_process), 0.0)

    if f_carbonate == NOT_SPECIFIED:
        carbonate_mol = NOT_SPECIFIED
    else:
        carbonate_mol = total * float(f_carbonate)

    if f_process == NOT_SPECIFIED:
        process_reductant_mol = NOT_SPECIFIED
        markers.append("f_process_reductant_C")
    else:
        process_reductant_mol = total * float(f_process)

    return CarbonPartition(
        labile_mol=labile_mol,
        refractory_mol=refractory_mol,
        carbonate_mol=carbonate_mol,
        process_reductant_mol=process_reductant_mol,
        not_speciated=tuple(markers),
        refractory_fraction_interval=f_refractory_interval,
    )


def _parse_carrier_block(carrier_key: str, block: Mapping[str, Any]) -> FoulantCarrierEntry:
    carrier = block.get("carrier", {}) or {}
    species = str(carrier.get("species", carrier_key))
    aliases = tuple(str(alias) for alias in carrier.get("aliases", []) or ())
    molar_mass = carrier.get("molar_mass_g_mol")
    return FoulantCarrierEntry(
        carrier_key=carrier_key,
        species=species,
        aliases=aliases,
        molar_mass_g_mol=float(molar_mass) if molar_mass is not None else None,
        group=str(block.get("group", "")),
        reaction_family=str(block.get("reaction_family", "")),
        fate=dict(block.get("fate", {}) or {}),
        gating=dict(block.get("gating", {}) or {}),
        thermo=dict(block.get("thermo", {}) or {}),
        warning_flags=dict(block.get("warning_flags", {}) or {}),
    )


def load_foulant_registry(foulant_thermo_yaml: str | Path) -> FoulantRegistry:
    """Load carrier identity, aliases, group, and fate names; build alias index."""
    path = Path(foulant_thermo_yaml)
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    foulant_dG = dict(payload.get("foulant_dG", {}) or {})
    carriers: dict[str, FoulantCarrierEntry] = {}
    alias_to_carrier: dict[str, str] = {}

    for key, block in payload.items():
        if key == "foulant_dG" or not isinstance(block, Mapping):
            continue
        entry = _parse_carrier_block(str(key), block)
        carriers[entry.carrier_key] = entry
        alias_to_carrier[entry.carrier_key] = entry.carrier_key
        alias_to_carrier[entry.carrier_key.lower()] = entry.carrier_key
        for alias in entry.aliases:
            alias_to_carrier[alias] = entry.carrier_key
            alias_to_carrier[alias.lower()] = entry.carrier_key

    return FoulantRegistry(
        carriers=carriers,
        alias_to_carrier=alias_to_carrier,
        foulant_dG=foulant_dG,
    )
