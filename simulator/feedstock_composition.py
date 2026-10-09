"""Feedstock composition normalization helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import (
    ATOMIC_WEIGHTS_G_PER_MOL,
    parse_formula,
)
from simulator.scalar_boundary import is_declared_real_scalar
from simulator.trace_oxide_parents import (
    LIQUID_PARENT_OXIDE,
    SIDEROPHILE_IN_METAL_SCOPE_GAP,
    ledger_component_key,
)


FEOT_FROM_FE2O3 = 2.0 * 71.844 / 159.687
OXYGEN_IN_FEO = 15.999 / 71.844
DEFAULT_FEO_TO_FE2O3_EQUIVALENT_FACTOR = 1.1113
FE_REPORTING_CONVENTION_TOTAL_AS_FEO = "total Fe as FeO"


@dataclass(frozen=True)
class FeRedoxPrior:
    """One cited ferric prior. Exactly one kind is set by the parser.

    ``measured_fe3_fraction`` is Fe3+/sum-Fe on a mole basis.
    ``delta_iw`` is log10(fO2) relative to the production IW buffer.
    """

    kind: str
    value: float
    source_id: str
    locator: str
    uncertainty: float | None = None
    range_low: float | None = None
    range_high: float | None = None


@dataclass(frozen=True)
class ResolvedFeedstockComposition:
    """Canonical oxide map and iron semantics for one feedstock entry.

    ``total_fe`` is expressed as FeO-equivalent wt% on the entry's declared
    composition basis. ``fe_metal`` is elemental Fe wt% from the separate
    elemental composition field. Absent Fe2O3 is not a measured zero. A
    ``fe_redox_prior`` resolves the split at load and does not invent a
    laboratory FeO/Fe2O3 pair on this map.
    """

    canonical_wt_pct: Mapping[str, Any]
    provenance: Mapping[str, Any]
    fe_redox_split_unknown: bool
    total_fe: float
    total_fe_basis: str
    fe_metal: float | None
    fe_redox_prior: FeRedoxPrior | None = None


def _finite_prior_number(value: Any, field: str) -> float:
    if not is_declared_real_scalar(value) or isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def _parse_fe_redox_prior(feedstock: Mapping[str, Any]) -> FeRedoxPrior | None:
    """Validate the optional fe_redox_prior block.

    A value without both source_id and locator is invalid input. The block
    carries exactly one of measured_fe3_fraction or delta_iw.
    """

    if "fe_redox_prior" not in feedstock or feedstock.get("fe_redox_prior") is None:
        return None
    raw = feedstock.get("fe_redox_prior")
    if not isinstance(raw, Mapping):
        raise ValueError("fe_redox_prior must be a mapping")
    kinds = ("measured_fe3_fraction", "delta_iw")
    present = [kind for kind in kinds if kind in raw]
    if len(present) != 1 or set(raw) - set(kinds):
        raise ValueError(
            "fe_redox_prior requires exactly one of "
            "measured_fe3_fraction or delta_iw"
        )
    kind = present[0]
    block = raw[kind]
    if not isinstance(block, Mapping):
        raise ValueError(f"fe_redox_prior.{kind} must be a mapping")
    allowed = {"value", "uncertainty", "source_id", "locator"}
    if kind == "delta_iw":
        allowed = allowed | {"range"}
    if set(block) - allowed:
        raise ValueError(
            f"fe_redox_prior.{kind} contains an unsupported field"
        )
    value = _finite_prior_number(block.get("value"), f"fe_redox_prior.{kind}.value")
    source_id = block.get("source_id")
    locator = block.get("locator")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError(f"fe_redox_prior.{kind} requires source_id and locator")
    if not isinstance(locator, str) or not locator.strip():
        raise ValueError(f"fe_redox_prior.{kind} requires source_id and locator")
    uncertainty = None
    if "uncertainty" in block and block.get("uncertainty") is not None:
        uncertainty = _finite_prior_number(
            block.get("uncertainty"),
            f"fe_redox_prior.{kind}.uncertainty",
        )
        if uncertainty < 0.0:
            raise ValueError(
                f"fe_redox_prior.{kind}.uncertainty must be >= 0"
            )
    range_low = None
    range_high = None
    if kind == "measured_fe3_fraction":
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                "fe_redox_prior.measured_fe3_fraction.value "
                "must be between 0 and 1"
            )
    else:
        span = block.get("range")
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            raise ValueError("fe_redox_prior.delta_iw.range must be [low, high]")
        range_low = _finite_prior_number(span[0], "fe_redox_prior.delta_iw.range")
        range_high = _finite_prior_number(span[1], "fe_redox_prior.delta_iw.range")
        if range_low > value or value > range_high:
            raise ValueError(
                "fe_redox_prior.delta_iw.value must lie inside range"
            )
    return FeRedoxPrior(
        kind=kind,
        value=value,
        source_id=source_id.strip(),
        locator=locator.strip(),
        uncertainty=uncertainty,
        range_low=range_low,
        range_high=range_high,
    )


def resolve_feedstock_composition(
    feedstock: Mapping[str, Any],
) -> ResolvedFeedstockComposition:
    """Validate and resolve feedstock oxide values with iron provenance."""
    composition = feedstock.get("composition_wt_pct", {}) or {}
    if not isinstance(composition, Mapping):
        raise ValueError("composition_wt_pct must be a mapping")
    basis = feedstock.get("composition_basis", {}) or {}
    if not isinstance(basis, Mapping):
        raise ValueError("composition_basis must be a mapping")

    flag = feedstock.get("fe_redox_split_unknown", False)
    if not isinstance(flag, bool):
        raise ValueError("fe_redox_split_unknown must be a boolean")
    prior = _parse_fe_redox_prior(feedstock)
    if flag and prior is not None:
        raise ValueError(
            "fe_redox_split_unknown cannot be combined with fe_redox_prior"
        )

    if "FeO_total" in composition:
        raise ValueError(
            "FeO_total is not a canonical feedstock oxide; use FeO with "
            "fe_redox_split_unknown when the split is unknown"
        )
    has_feo = "FeO" in composition
    has_fe2o3 = "Fe2O3" in composition
    if "Fe" in composition or "Fe0" in composition or "Fe_metal" in composition:
        raise ValueError(
            "metallic iron must be outside composition_wt_pct"
        )
    if flag:
        if has_fe2o3:
            raise ValueError(
                "fe_redox_split_unknown cannot be combined with Fe2O3"
            )
        if not has_feo:
            raise ValueError(
                "fe_redox_split_unknown requires FeO carrying total Fe as FeO"
            )
        if basis.get("fe_reporting_convention") != (
            FE_REPORTING_CONVENTION_TOTAL_AS_FEO
        ):
            raise ValueError(
                "fe_redox_split_unknown requires composition_basis."
                "fe_reporting_convention: total Fe as FeO"
            )
    elif has_fe2o3:
        if prior is not None:
            raise ValueError(
                "fe_redox_prior cannot be combined with declared Fe2O3"
            )
        if not has_feo:
            raise ValueError("measured split requires both FeO and Fe2O3")
        for oxide in ("FeO", "Fe2O3"):
            provenance = basis.get(oxide)
            if not isinstance(provenance, Mapping) or not all(
                isinstance(provenance.get(field), str)
                and provenance[field].strip()
                for field in ("method", "source")
            ):
                raise ValueError(
                    f"measured {oxide} requires composition_basis.{oxide}."
                    "method and source"
                )

    elemental = feedstock.get("elemental_composition_wt_pct", {}) or {}
    if not isinstance(elemental, Mapping):
        raise ValueError("elemental_composition_wt_pct must be a mapping")
    elemental_status = str(feedstock.get("elemental_composition_status", ""))
    metal = (
        None
        if "cross_check_only" in elemental_status
        else _representative_number(
            elemental.get("Fe"), "elemental_composition_wt_pct.Fe"
        )
    )
    feot = feot_equivalent_wt_pct(composition)
    # FeO with no Fe2O3 used to be reported as a measured ferric zero.
    # A total-Fe analysis is not that measurement. The load split owns
    # the ferric fraction; this map keeps the declared oxides.

    return ResolvedFeedstockComposition(
        canonical_wt_pct=dict(composition),
        provenance=dict(basis),
        fe_redox_split_unknown=flag,
        total_fe=feot,
        total_fe_basis="FeO-equivalent wt% on the declared composition basis",
        fe_metal=metal,
        fe_redox_prior=prior,
    )


def total_fe(feedstock_or_composition: Mapping[str, Any]) -> float:
    """Return total iron as FeO-equivalent wt% (the explicit basis)."""
    feo, fe2o3 = iron_oxide_values(feedstock_or_composition)
    return feo + fe2o3 * FEOT_FROM_FE2O3


def iron_oxide_values(
    feedstock_or_composition: Mapping[str, Any],
) -> tuple[float, float]:
    """Read canonical FeO and Fe2O3 values through the iron owner."""
    composition = _composition_map(feedstock_or_composition)
    raw_feo = composition.get("FeO")
    raw_fe2o3 = composition.get("Fe2O3")
    feo = _representative_number(raw_feo, "composition_wt_pct.FeO")
    fe2o3 = _representative_number(raw_fe2o3, "composition_wt_pct.Fe2O3")
    if "FeO" in composition and feo is None:
        raise ValueError("invalid feedstock declaration composition_wt_pct.FeO")
    if "Fe2O3" in composition and fe2o3 is None:
        raise ValueError("invalid feedstock declaration composition_wt_pct.Fe2O3")
    return feo or 0.0, fe2o3 or 0.0


def feot_equivalent_moles(feedstock_or_composition: Mapping[str, Any]) -> float:
    """Return total Fe moles represented on the FeO-equivalent mole basis."""
    feo_moles, fe2o3_moles = iron_oxide_values(feedstock_or_composition)
    return feo_moles + 2.0 * fe2o3_moles


def fe2o3_equivalent_wt_pct(
    feedstock_or_composition: Mapping[str, Any],
    feo_to_fe2o3_factor: float = DEFAULT_FEO_TO_FE2O3_EQUIVALENT_FACTOR,
) -> float:
    """Return ferric-oxide-equivalent wt% using the caller's model factor."""
    feo, fe2o3 = iron_oxide_values(feedstock_or_composition)
    return fe2o3 + float(feo_to_fe2o3_factor) * feo


def fe_metal(feedstock: Mapping[str, Any]) -> float | None:
    return resolve_feedstock_composition(feedstock).fe_metal


def feot_equivalent_wt_pct(comp_wt: Mapping[str, Any]) -> float:
    """Return FeO-equivalent wt% for separate FeO / Fe2O3 oxide values."""
    return total_fe(comp_wt)


def _composition_map(source: Mapping[str, Any]) -> Mapping[str, Any]:
    composition = source.get("composition_wt_pct")
    if isinstance(composition, Mapping):
        return composition
    return source


def _representative_number(value: Any, field: str) -> float | None:
    if is_declared_real_scalar(value) and isinstance(value, (int, float)):
        return _valid_declared_number(float(value), field)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            if not is_declared_real_scalar(
                value[0],
                allow_numeric_str=True,
            ) or not is_declared_real_scalar(
                value[1],
                allow_numeric_str=True,
            ):
                raise TypeError
            low_raw = float(value[0])
            high_raw = float(value[1])
        except (TypeError, ValueError):
            return None
        low = _valid_declared_number(low_raw, field)
        high = _valid_declared_number(high_raw, field)
        return (low + high) / 2.0
    return None


def _valid_declared_number(number: float, field: str) -> float:
    if not math.isfinite(number):
        raise ValueError(f"invalid feedstock declaration {field}: non-finite {number!r}")
    if number < 0.0:
        raise ValueError(f"invalid feedstock declaration {field}: negative {number!r}")
    return number


@dataclass(frozen=True)
class TraceOxideConversion:
    """One element row rewritten as its parent-oxide ledger key."""

    element: str
    parent: str
    added_o_kg: float
    element_mass_kg: float
    parent_mass_kg: float


@dataclass(frozen=True)
class TraceElementCoverage:
    """An element key the bridge kept. Never dropped and never zeroed."""

    element: str
    disposition: str
    mass_kg: float


@dataclass(frozen=True)
class TraceOxideBridgeResult:
    """Pre-normalize masses plus the conversion notes and coverage list."""

    masses_kg: Mapping[str, float]
    notes: tuple[TraceOxideConversion, ...]
    coverage: tuple[TraceElementCoverage, ...]


def trace_element_disposition(element: str) -> str:
    """Classify one element key. Siderophiles win over a parent oxide."""

    if element in SIDEROPHILE_IN_METAL_SCOPE_GAP:
        return "siderophile_in_metal_scope_gap"
    if element in LIQUID_PARENT_OXIDE:
        return "oxide_parent"
    return "no_oxide_parent"


def _element_symbol(component: str) -> str | None:
    try:
        formula = parse_formula(component)
    except AccountingError:
        return None
    if len(formula.elements) != 1:
        return None
    symbol, count = next(iter(formula.elements.items()))
    if component != symbol or abs(float(count) - 1.0) > 1.0e-12:
        return None
    return str(symbol)


def _stoichiometric_parent_mass_kg(
    element: str,
    parent: str,
    element_mass_kg: float,
) -> tuple[float, float]:
    """Return ``(added oxygen kg, parent-oxide kg)`` for one element row.

    Premise. A ``composition_wt_pct`` row whose key is an element symbol is
    elemental mass. The oxygen of the generator parent was never in that
    row. The ledger key is the parent oxide, so that oxygen is real mass
    inserted before the shared rescale. The scale factor does not create it.

    Algebra. ``m_E`` is the elemental mass in kg (``batch_kg * wt_pct / 100``).
    ``n_E`` [mol] = ``m_E`` [kg] * 1000 [g/kg] / ``A_E`` [g/mol]. Parent
    ``M_a O_b`` contributes ``b/a`` mol O per mol M.
    ``m_O`` [kg] = ``n_E * (b/a) * A_O`` [g/mol] / 1000 [g/kg].
    ``m_parent = m_E + m_O``.

    ``normalize_component_masses_kg`` then scales every positive component,
    including ``m_parent``, by ``s = target / sum(components)``. The oxygen
    that remains in the batch is ``m_O * s``. The batch total stays
    ``target``, the same contract as every other component. Inserting
    ``m_O`` after the rescale would push the total off target.

    Unit check. kg * (g/kg) / (g/mol) * (g/mol) / (g/kg) = kg.

    Worked example. 5 ppm Ga is wt% 0.0005 (element ppm / 10_000). On a
    1000 kg batch, ``m_Ga = 0.005`` kg. ``A_Ga = 69.723`` g/mol,
    ``A_O = 15.999`` g/mol, and Ga2O3 has ``b/a = 1.5``.
    ``n_Ga = 0.071712347432`` mol. ``m_O = 0.00172098876985`` kg
    = 1.72098876985 g/t before the rescale.
    ``m_Ga2O3 = 0.00672098876985`` kg.
    """

    parent_formula = parse_formula(parent)
    counts = parent_formula.elements
    extra = [symbol for symbol in counts if symbol not in {element, "O"}]
    metal_count = float(counts.get(element, 0.0))
    oxygen_count = float(counts.get("O", 0.0))
    if extra or metal_count <= 0.0 or oxygen_count <= 0.0:
        raise ValueError(
            f"parent {parent!r} is not an oxide of {element!r}"
        )
    atomic = ATOMIC_WEIGHTS_G_PER_MOL
    element_mol = element_mass_kg * 1000.0 / float(atomic[element])
    added_o_kg = (
        element_mol * (oxygen_count / metal_count) * float(atomic["O"]) / 1000.0
    )
    return added_o_kg, element_mass_kg + added_o_kg


def bridge_trace_element_keys(
    masses_kg: Mapping[str, float],
) -> TraceOxideBridgeResult:
    """Rewrite oxide-parent element keys onto the generator's ledger keys.

    The parent is ``LIQUID_PARENT_OXIDE`` via ``ledger_component_key``.
    Elements with no oxide parent stay element keys and are listed in
    ``coverage``. Siderophiles (Ni, Co, Ir, Os, Pt, Au) stay element keys
    and are flagged ``siderophile_in_metal_scope_gap``; metal is not
    oxidised on paper. Returned masses are pre-normalize.
    """

    masses = {
        str(component): float(kg)
        for component, kg in masses_kg.items()
        if float(kg) > 0.0
    }
    notes: list[TraceOxideConversion] = []
    coverage: list[TraceElementCoverage] = []
    converted: list[str] = []
    for component in sorted(masses):
        symbol = _element_symbol(component)
        if symbol is None:
            continue
        disposition = trace_element_disposition(symbol)
        element_mass_kg = masses[component]
        if disposition != "oxide_parent":
            coverage.append(
                TraceElementCoverage(symbol, disposition, element_mass_kg)
            )
            continue
        parent = ledger_component_key(LIQUID_PARENT_OXIDE[symbol])
        added_o_kg, parent_mass_kg = _stoichiometric_parent_mass_kg(
            symbol, parent, element_mass_kg
        )
        notes.append(
            TraceOxideConversion(
                element=symbol,
                parent=parent,
                added_o_kg=added_o_kg,
                element_mass_kg=element_mass_kg,
                parent_mass_kg=parent_mass_kg,
            )
        )
        masses[parent] = masses.get(parent, 0.0) + parent_mass_kg
        converted.append(component)
    for component in converted:
        del masses[component]
    return TraceOxideBridgeResult(
        masses_kg=masses,
        notes=tuple(notes),
        coverage=tuple(coverage),
    )


def _raw_feedstock_component_masses_kg(
    feedstock: Mapping[str, Any],
    mass_kg: float,
) -> dict[str, float]:
    """Component masses before the trace-oxide bridge and the rescale."""

    batch_mass_kg = float(mass_kg)
    raw_masses: dict[str, float] = {}
    composition = feedstock.get("composition_wt_pct", {}) or {}

    if isinstance(composition, Mapping):
        raw_masses.update(
            {
                str(component): kg
                for component, raw_value in composition.items()
                if (
                    kg := _mass_from_wt_pct(
                        raw_value,
                        batch_mass_kg,
                        field=f"composition_wt_pct.{component}",
                    )
                )
                is not None
                and kg > 0.0
            }
        )
    iron_metal_wt_pct = fe_metal(feedstock)
    if iron_metal_wt_pct is not None and iron_metal_wt_pct > 0.0:
        raw_masses["Fe"] = raw_masses.get("Fe", 0.0) + (
            batch_mass_kg * iron_metal_wt_pct / 100.0
        )
    for section_name in ("non_oxide_components", "bulk_additions", "structural_water"):
        section = feedstock.get(section_name, {}) or {}
        if isinstance(section, Mapping):
            for raw_name, raw_value in section.items():
                name = _component_name_from_field(str(raw_name))
                kg = (
                    _mass_from_kg_per_tonne(
                        raw_value,
                        batch_mass_kg,
                        field=f"{section_name}.{raw_name}",
                    )
                    if str(raw_name).endswith("_kg_per_tonne")
                    else _mass_from_wt_pct(
                        raw_value,
                        batch_mass_kg,
                        field=f"{section_name}.{raw_name}",
                    )
                )
                if kg is not None and kg > 0.0:
                    raw_masses[name] = raw_masses.get(name, 0.0) + kg

    return raw_masses


def feedstock_trace_oxide_bridge(
    feedstock: Mapping[str, Any],
    mass_kg: float,
) -> TraceOxideBridgeResult:
    """Bridge one feedstock. Notes and coverage are this return value."""

    return bridge_trace_element_keys(
        _raw_feedstock_component_masses_kg(feedstock, mass_kg)
    )


def normalized_feedstock_component_masses_kg(
    feedstock: Mapping[str, Any],
    mass_kg: float,
) -> dict[str, float]:
    """Return ledger-normalized raw feedstock component masses.

    Trace element keys with an oxide parent are rewritten first. The added
    oxygen sits inside the parent mass before ``normalize_component_masses_kg``
    rescales the batch onto ``mass_kg``.
    """

    batch_mass_kg = float(mass_kg)
    bridged = feedstock_trace_oxide_bridge(feedstock, batch_mass_kg)
    return normalize_component_masses_kg(dict(bridged.masses_kg), batch_mass_kg)


def normalize_component_masses_kg(
    masses: dict[str, float],
    target_mass_kg: float,
) -> dict[str, float]:
    """Scale positive component masses to a ledger target, in place."""
    total = sum(kg for kg in masses.values() if kg > 0.0)
    if total <= 0.0 or target_mass_kg <= 0.0:
        return masses
    scale = target_mass_kg / total
    for component, kg in list(masses.items()):
        masses[component] = kg * scale
    return masses


def _mass_from_wt_pct(value: Any, mass_kg: float, *, field: str) -> float | None:
    number = _representative_number(value, field)
    if number is None:
        return None
    return mass_kg * number / 100.0


def _mass_from_kg_per_tonne(
    value: Any,
    batch_mass_kg: float,
    *,
    field: str,
) -> float | None:
    number = _representative_number(value, field)
    if number is None:
        return None
    return batch_mass_kg * number / 1000.0


def _component_name_from_field(raw_name: str) -> str:
    for suffix in ("_wt_pct", "_kg_per_tonne"):
        if raw_name.endswith(suffix):
            return raw_name[: -len(suffix)]
    return raw_name
