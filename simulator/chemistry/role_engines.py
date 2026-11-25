"""Which engine serves each prediction role, and whether the handoff matches.

The selection failure this prevents: a report names an engine the prediction
did not use, so a residual cannot be attributed to a role swap.

The handoff failure this prevents: a quantity crosses a role boundary with a
changed inventory, a mismatched activity standard state, a second fO2, or a
liquid-only consumer downstream of solids, and the value path stays silent.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from simulator.chemistry.melt_activity import normalize_high_t_melt_activity


ROLES = ("phase_equilibrium", "melt_activity", "vapour", "redox")

# Tokens the existing callable paths can name. A token outside these is a
# typed refusal: that engine cannot be called for the role.
PHASE_EQUILIBRIUM_ENGINES = frozenset({
    "internal-analytical",
    "alphamelts",
    "thermoengine",
    "magemin",
    "liquid_only",
    "vaporock",
})
MELT_ACTIVITY_ENGINES = frozenset({"openimcc", "constant_gamma"})
VAPOUR_ENGINES = frozenset({"openimcc", "internal-analytical"})
REDOX_ENGINES_RUN = frozenset({"kress91", "feo_weight_fraction"})
# The residue fO2 owner is the source-oxygen ladder. Kress is not a residue
# fO2 caller, so it is not a legal residue redox pin.
REDOX_ENGINES_RESIDUE = frozenset({"source_oxygen"})

_STANDARD_STATE_KEYS = ("convention", "phase", "component_basis")


class RoleEngineRefusal(ValueError):
    """A role pin names a role or an engine the surface cannot call."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail or code
        super().__init__(self.detail if detail == "" else f"{code}: {detail}")


@dataclass(frozen=True)
class RoleEngineDecision:
    phase_equilibrium: str
    melt_activity: str
    vapour: str
    redox: str
    override_applied: tuple[str, ...]
    surface: str

    def as_mapping(self) -> dict[str, str]:
        return {
            "phase_equilibrium": self.phase_equilibrium,
            "melt_activity": self.melt_activity,
            "vapour": self.vapour,
            "redox": self.redox,
        }


@dataclass(frozen=True)
class HandoffCrossing:
    """What one role handed the next. The checker does not replace these."""

    producer_role: str
    consumer_role: str
    producer_inventory_mol: Mapping[str, float] | None = None
    consumer_inventory_mol: Mapping[str, float] | None = None
    producer_standard_state: Mapping[str, object] | None = None
    consumer_standard_state: Mapping[str, object] | None = None
    fo2_log10_by_role: Mapping[str, float] | None = None
    liquid_fraction: float | None = None
    solid_fraction: float | None = None
    consumer_assumes_liquid_only: bool = False
    phase_engine_requested: str | None = None
    phase_engine_applied: bool = True
    activity_handoff: bool = False


@dataclass(frozen=True)
class HandoffFinding:
    code: str
    detail: str


def resolve_role_engines(
    *,
    backend_name: str | None,
    high_t_melt_activity: str,
    temperature_K: float,
    intrinsic_fO2_supplied: bool,
    vapour_openimcc_above_K: float,
    role_override: Mapping[str, str] | None = None,
    surface: str = "run",
    engine_argument: str | None = None,
    evaluate: str = "all",
    load_activity_domain_low_K: Callable[[], float] | None = None,
) -> RoleEngineDecision:
    """Choose one engine per role.

    An absent override reproduces the historical selectors. Vapour is
    ``openimcc`` only when the mode is openimcc and
    ``temperature_K > vapour_openimcc_above_K``. Melt activity is
    ``openimcc`` only when that mode is openimcc and
    ``temperature_K >=`` the loader's domain low. The loader is called only
    when that activity comparison is actually evaluated. Residue prediction
    does not call this function without a pin: the unpinned caller keeps
    the engine argument and does not consult these gates. A present override
    replaces that role after the default. A pin that omits vapour or melt
    still stores the gate's token at this temperature.
    """

    if surface not in {"run", "residue"}:
        raise RoleEngineRefusal("role_surface_unsupported", surface)
    if evaluate not in {"activity", "vapour", "all"}:
        raise RoleEngineRefusal("role_evaluate_unsupported", evaluate)
    mode = normalize_high_t_melt_activity(high_t_melt_activity)
    override = _clean_override(role_override)
    # Illegal pin tokens are refused before the activity gate can load a pack.
    _require_override_engines(surface, override)

    vapour = _vapour_default(
        surface=surface,
        mode=mode,
        temperature_K=temperature_K,
        vapour_openimcc_above_K=vapour_openimcc_above_K,
        engine_argument=engine_argument,
    )
    if "vapour" in override:
        vapour = override["vapour"]

    melt = _melt_default(
        mode=mode,
        temperature_K=temperature_K,
        evaluate=evaluate,
        override=override,
        load_activity_domain_low_K=load_activity_domain_low_K,
    )
    if "melt_activity" in override:
        melt = override["melt_activity"]

    phase = _phase_default(surface, backend_name)
    if "phase_equilibrium" in override:
        phase = override["phase_equilibrium"]

    redox = _redox_default(surface, intrinsic_fO2_supplied)
    if "redox" in override:
        redox = override["redox"]

    _require_engine("phase_equilibrium", phase, PHASE_EQUILIBRIUM_ENGINES)
    if melt != "":
        _require_engine("melt_activity", melt, MELT_ACTIVITY_ENGINES)
    _require_engine("vapour", vapour, VAPOUR_ENGINES)
    redox_engines = REDOX_ENGINES_RESIDUE if surface == "residue" else REDOX_ENGINES_RUN
    _require_engine("redox", redox, redox_engines)
    # Defaults may name different melt and vapour engines: the historical
    # vapour switch and the activity-domain gate are independent. Refuse
    # only a pin that creates the cross-basis pair.
    if (
        melt == "constant_gamma"
        and vapour == "openimcc"
        and ("melt_activity" in override or "vapour" in override)
    ):
        raise RoleEngineRefusal(
            "role_engines_incompatible",
            "constant_gamma activities are single-cation Raoultian; "
            "openimcc vapour consumes parent-oxide activities",
        )
    if surface == "residue" and vapour == "openimcc" and melt == "":
        # The evaluate_gas branch always consumes parent activities.
        melt = "openimcc"
    return RoleEngineDecision(
        phase_equilibrium=phase,
        melt_activity=melt or _residue_melt_label(surface, engine_argument, mode),
        vapour=vapour,
        redox=redox,
        override_applied=tuple(role for role in ROLES if role in override),
        surface=surface,
    )


def handoff_notices(crossing: HandoffCrossing) -> tuple[HandoffFinding, ...]:
    """Return typed findings. This function does not modify the crossing."""

    findings: list[HandoffFinding] = []
    if (
        crossing.producer_inventory_mol is not None
        and crossing.consumer_inventory_mol is not None
    ):
        findings.extend(_conservation_findings(
            crossing.producer_inventory_mol,
            crossing.consumer_inventory_mol,
        ))
    if crossing.activity_handoff or (
        crossing.producer_standard_state is not None
        or crossing.consumer_standard_state is not None
    ):
        findings.extend(_standard_state_findings(
            crossing.producer_standard_state,
            crossing.consumer_standard_state,
        ))
    findings.extend(_fo2_findings(crossing.fo2_log10_by_role))
    if crossing.consumer_assumes_liquid_only:
        solid = crossing.solid_fraction
        liquid = crossing.liquid_fraction
        if solid is not None and solid > 0.0:
            findings.append(HandoffFinding(
                "handoff_liquid_only_downstream_of_solids",
                f"liquid_fraction={liquid!r}; solid_fraction={solid!r}",
            ))
        elif (
            crossing.phase_engine_requested
            and crossing.phase_engine_requested != "liquid_only"
            and not crossing.phase_engine_applied
        ):
            findings.append(HandoffFinding(
                "handoff_phase_engine_not_applied",
                (
                    f"phase_equilibrium={crossing.phase_engine_requested}; "
                    "residue value path did not call it"
                ),
            ))
    return tuple(findings)


def _clean_override(role_override: Mapping[str, str] | None) -> dict[str, str]:
    if not role_override:
        return {}
    cleaned: dict[str, str] = {}
    for role, engine in role_override.items():
        role_name = str(role).strip()
        if role_name not in ROLES:
            raise RoleEngineRefusal("role_unknown", role_name)
        if role_name in cleaned:
            raise RoleEngineRefusal("role_repeated", role_name)
        cleaned[role_name] = str(engine).strip()
    return cleaned


def _require_engine(role: str, engine: str, allowed: frozenset[str]) -> None:
    if engine not in allowed:
        raise RoleEngineRefusal(
            "role_engine_unsupported",
            f"role={role}; engine={engine}",
        )


def _require_override_engines(surface: str, override: Mapping[str, str]) -> None:
    """Refuse a named pin before a temperature gate loads the activity domain."""

    if not override:
        return
    allowed = {
        "phase_equilibrium": PHASE_EQUILIBRIUM_ENGINES,
        "melt_activity": MELT_ACTIVITY_ENGINES,
        "vapour": VAPOUR_ENGINES,
        "redox": (
            REDOX_ENGINES_RESIDUE if surface == "residue" else REDOX_ENGINES_RUN
        ),
    }
    for role, engine in override.items():
        _require_engine(role, engine, allowed[role])


def _vapour_default(
    *,
    surface: str,
    mode: str,
    temperature_K: float,
    vapour_openimcc_above_K: float,
    engine_argument: str | None,
) -> str:
    if surface == "residue" and engine_argument not in VAPOUR_ENGINES:
        # The residue caller can only run these two vapour engines. The
        # token below is still the builtin gate, not this argument.
        raise RoleEngineRefusal(
            "role_engine_unsupported",
            f"role=vapour; engine={engine_argument}",
        )
    if mode == "openimcc" and temperature_K > vapour_openimcc_above_K:
        return "openimcc"
    return "internal-analytical"


def _melt_default(
    *,
    mode: str,
    temperature_K: float,
    evaluate: str,
    override: Mapping[str, str],
    load_activity_domain_low_K: Callable[[], float] | None,
) -> str:
    """Return the melt token, or '' when this call must not load the domain."""

    if "melt_activity" in override:
        return override["melt_activity"]
    if mode != "openimcc":
        return "constant_gamma"
    # Vapour questions load the activity domain only to reject the illegal
    # pair when the pin forces openimcc vapour. An unpinned vapour question
    # returns before any pack load, matching the historical short-circuit.
    if evaluate == "vapour" and override.get("vapour") != "openimcc":
        return ""
    if load_activity_domain_low_K is None:
        raise RoleEngineRefusal(
            "role_activity_domain_unsupplied",
            "activity gate requires the caller's domain loader",
        )
    low = load_activity_domain_low_K()
    if temperature_K >= low:
        return "openimcc"
    return "constant_gamma"


def _residue_melt_label(surface: str, engine_argument: str | None, mode: str) -> str:
    if surface == "residue" and (engine_argument == "openimcc"):
        return "openimcc"
    return mode


def _phase_default(surface: str, backend_name: str | None) -> str:
    if surface == "residue":
        return "liquid_only"
    if backend_name is None:
        return "internal-analytical"
    from simulator.backend_names import canonical_backend_name

    name = canonical_backend_name(backend_name) or "internal-analytical"
    if name in PHASE_EQUILIBRIUM_ENGINES:
        return name
    return "internal-analytical"


def _redox_default(surface: str, intrinsic_fO2_supplied: bool) -> str:
    if surface == "residue":
        return "source_oxygen"
    if intrinsic_fO2_supplied:
        return "kress91"
    return "feo_weight_fraction"


def _conservation_findings(
    producer: Mapping[str, float],
    consumer: Mapping[str, float],
) -> tuple[HandoffFinding, ...]:
    """Compare element moles and mass. Tolerances are the ledger's.

    Element moles of an inventory are ``sum over oxides of n_oxide * nu``,
    where ``nu`` is ``parse_formula(oxide).elements``. Mass is the same
    sum times ``molar_mass_kg_per_mol``. A difference fails only when it
    exceeds the larger of the ledger relative balance tolerance times the
    scale and the ledger absolute floor (atom moles, or kilograms).
    """

    from simulator.accounting.exceptions import UnknownSpeciesError
    from simulator.accounting.formulas import parse_formula
    from simulator.accounting.ledger import (
        DEFAULT_ATOM_TOLERANCE_MOL,
        DEFAULT_BALANCE_ABSOLUTE_FLOOR_KG,
        DEFAULT_BALANCE_RELATIVE_TOLERANCE,
    )

    producer_elements: dict[str, float] = {}
    consumer_elements: dict[str, float] = {}
    producer_mass = 0.0
    consumer_mass = 0.0
    findings: list[HandoffFinding] = []

    def accumulate(inventory: Mapping[str, float], elements: dict[str, float]) -> float:
        mass = 0.0
        for oxide, amount in inventory.items():
            moles = float(amount)
            if moles == 0.0:
                continue
            try:
                formula = parse_formula(str(oxide))
            except UnknownSpeciesError as exc:
                findings.append(HandoffFinding(
                    "handoff_formula_unparsed",
                    f"oxide={oxide}; {exc}",
                ))
                continue
            mass += moles * formula.molar_mass_kg_per_mol()
            for element, count in formula.elements.items():
                elements[element] = elements.get(element, 0.0) + moles * float(count)
        return mass

    producer_mass = accumulate(producer, producer_elements)
    consumer_mass = accumulate(consumer, consumer_elements)
    moved = [
        element
        for element in sorted(set(producer_elements) | set(consumer_elements))
        if _exceeds(
            producer_elements.get(element, 0.0),
            consumer_elements.get(element, 0.0),
            relative=DEFAULT_BALANCE_RELATIVE_TOLERANCE,
            absolute=DEFAULT_ATOM_TOLERANCE_MOL,
        )
    ]
    if moved:
        findings.append(HandoffFinding(
            "handoff_element_not_conserved",
            "elements=" + ",".join(moved),
        ))
    if _exceeds(
        producer_mass,
        consumer_mass,
        relative=DEFAULT_BALANCE_RELATIVE_TOLERANCE,
        absolute=DEFAULT_BALANCE_ABSOLUTE_FLOOR_KG,
    ):
        findings.append(HandoffFinding(
            "handoff_mass_not_conserved",
            f"producer_kg={producer_mass!r}; consumer_kg={consumer_mass!r}",
        ))
    return tuple(findings)


def _exceeds(left: float, right: float, *, relative: float, absolute: float) -> bool:
    difference = abs(left - right)
    scale = max(abs(left), abs(right))
    return difference > max(absolute, relative * scale)


def _standard_state_findings(
    producer: Mapping[str, object] | None,
    consumer: Mapping[str, object] | None,
) -> tuple[HandoffFinding, ...]:
    producer_state = _declared_standard_state(producer)
    consumer_state = _declared_standard_state(consumer)
    if producer_state is None or consumer_state is None:
        return (HandoffFinding(
            "handoff_activity_standard_state_undeclared",
            f"producer={producer_state!r}; consumer={consumer_state!r}",
        ),)
    mismatched = [
        key for key in _STANDARD_STATE_KEYS
        if producer_state[key] != consumer_state[key]
    ]
    if not mismatched:
        return ()
    return (HandoffFinding(
        "handoff_activity_standard_state_mismatch",
        "keys=" + ",".join(mismatched)
        + f"; producer={producer_state!r}; consumer={consumer_state!r}",
    ),)


def _declared_standard_state(
    raw: Mapping[str, object] | None,
) -> dict[str, str] | None:
    if not isinstance(raw, Mapping):
        return None
    declared = {
        key: str(raw.get(key) or "").strip()
        for key in _STANDARD_STATE_KEYS
    }
    if any(value == "" for value in declared.values()):
        return None
    return declared


def _fo2_findings(
    fo2_log10_by_role: Mapping[str, float] | None,
) -> tuple[HandoffFinding, ...]:
    if not fo2_log10_by_role or len(fo2_log10_by_role) < 2:
        return ()
    from simulator.accounting.ledger import DEFAULT_BALANCE_RELATIVE_TOLERANCE

    items = [(str(role), float(value)) for role, value in fo2_log10_by_role.items()]
    first_role, first_log = items[0]
    first_pressure = 10.0 ** first_log
    overwritten = []
    for role, log_value in items[1:]:
        pressure = 10.0 ** log_value
        if _exceeds(
            first_pressure,
            pressure,
            relative=DEFAULT_BALANCE_RELATIVE_TOLERANCE,
            absolute=0.0,
        ):
            overwritten.append(f"{first_role}={first_log!r}; {role}={log_value!r}")
    if not overwritten:
        return ()
    return (HandoffFinding(
        "handoff_fo2_overwritten",
        "; ".join(overwritten),
    ),)
