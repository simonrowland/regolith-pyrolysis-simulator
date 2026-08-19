"""b-203: the single fO2 a reducing offgas imposes, via water-gas-shift equilibration.

THE PROBLEM. A reducing offgas does not debit oxygen from the melt; it IMPOSES an
oxygen fugacity through its ratios, exactly as an H2/H2O or CO/CO2 mix is used to
impose fO2 in the laboratory. Inverting either couple is elementary (see
``docs-private/research/2026-08-18-b203-buffer/probe_buffer_fo2.py``, which
validates the inversion against the Frost 1991 iron-wustite buffer to 0.003 dex at
1873 K and independently reproduces the ~1100 K crossover):

    H2 + 1/2 O2 <-> H2O    log10 fO2 = 2 * [ log10(p_H2O/p_H2) - log10 K1 ]
    CO + 1/2 O2 <-> CO2    log10 fO2 = 2 * [ log10(p_CO2/p_CO) - log10 K2 ]

But a real pyrolysis offgas contains BOTH couples, and they DISAGREE. Two numbers,
one melt. Something has to reconcile them before fO2 can be imposed at all.

(An earlier draft of this docstring quoted "up to 6.7 dex" from a 2026-08-18
probe. Reviewers correctly flagged that figure as unsourced HERE and, worse, as
the value you get from a near-absent CO -- which this project's own organics
module treats as a proven zero for primary pyrolysis. The magnitude is therefore
composition-specific and is now REPORTED per call as ``assumption_strain_dex``
rather than asserted as a headline.)

THE RECONCILIATION, and why it introduces no fitted parameter. The two couples are
linked by the water-gas shift, which is just their difference:

    CO + H2O <-> CO2 + H2      Q = (p_CO2 * p_H2) / (p_CO * p_H2O)

    K_wgs = K2 / K1, because
        K2/K1 = [a_CO2/(a_CO a_O2^1/2)] * [(a_H2 a_O2^1/2)/a_H2O]
              = (a_CO2 a_H2)/(a_CO a_H2O)                        <- a_O2 cancels
    so K_wgs comes from the SAME CEA records already used for K1 and K2. Nothing
    new is introduced, fitted, or assumed about rates.

    The disagreement between the couples is then exactly

        log10 fO2(CO) - log10 fO2(H2) = 2 * log10(Q / K_wgs)

    (substitute both inversions and collect; the K1, K2 terms combine into
    K2/K1 = K_wgs). A gas AT water-gas-shift equilibrium has Q = K_wgs and the two
    couples agree identically. The 6.7 dex spread is a measure of how far the raw
    offgas sits from that equilibrium -- nothing more exotic.

So: equilibrate the offgas via the shift at melt temperature, then read one fO2.

THE ASSUMPTION, STATED SO IT CAN BE ATTACKED. This assumes the offgas internally
equilibrates at melt temperature before imposing fO2. That is an assumption, not a
derivation -- whether the shift actually reaches equilibrium depends on residence
time and catalysis. ``log10_Q_over_K`` is reported alongside every result precisely
so the assumption is auditable: it says how far the raw gas was from the state this
model assumes it reaches.

★ WHAT WE MUST NOT DO, and it has now presented twice in different clothes: answer
"does the shift actually equilibrate?" with a WGS RATE CONSTANT. That would plant a
new ungrounded extent factor on fO2 -- one of the three mandate levers -- which is
the identical error to the rejected "what fraction of offgas contacts the melt"
factor. State the equilibrium assumption, report the deviation, and let the owner
rule on whether it holds for this furnace. Do not fit your way past it.

★ AND NOTE WHAT THE POST-EQUILIBRATION AGREEMENT IS NOT. After equilibration the
two couples return the same fO2 to numerical precision. That is ALGEBRAICALLY
GUARANTEED by the construction above -- setting Q = K_wgs forces it -- so it tests
that this module's solver converged and its algebra is coded right. It is NOT
independent evidence that the physics is correct. The non-circular check is the
probe's iron-wustite comparison, which uses no part of this reconciliation.

STATUS: computation + diagnostic only. This module imposes nothing. Wiring it to
move melt fO2 is golden-affecting and is a separate, owner-gated step.
"""

from __future__ import annotations

import math
import pathlib
from dataclasses import dataclass, field
from typing import Any, Mapping

import yaml

from simulator.vapour_rail.nasa_cea import (
    Nasa9Segment,
    NasaCeaPolynomial,
    reaction_equilibrium_constant,
)

#: Species whose CEA records this module needs. Both couples plus O2.
BUFFER_SPECIES: tuple[str, ...] = ("H2", "H2O", "O2", "CO", "CO2")

DEFAULT_CEA_EXTRACT = pathlib.Path("data/literature/extracts/nasa-cea-thermo.yaml")

#: Coupling modes, closed vocabulary.
WGS_EQUILIBRATED = "wgs_equilibrated"
H2_COUPLE_ONLY = "h2_couple_only"
CO_COUPLE_ONLY = "co_couple_only"

#: Status vocabulary. Deliberately NOT "ok": this module never verifies that the
#: shift actually ran, so a bare "ok" would claim more than it can support and a
#: caller could read it as endorsement in a regime where uncatalysed shift is
#: kinetically frozen.
COMPUTED_ASSUMPTION_UNVERIFIED = "computed_assumption_unverified"
COMPUTED_NO_RECONCILIATION = "computed_no_reconciliation"

#: Redox-active offgas species this module does NOT model. The four it does
#: model are H2/H2O and CO/CO2. The rest are counted and REPORTED rather than
#: dropped -- see `unmodelled_species_mol` on the record for what that means.
UNMODELLED_REDOX_SPECIES: tuple[str, ...] = ("CH4", "H2S", "O2", "SO2", "COS", "NH3")


class OffgasFO2Unavailable(Exception):
    """Raised when the offgas cannot impose an fO2 and no value may be invented."""


@dataclass(frozen=True)
class OffgasFO2:
    """One imposed fO2, plus everything needed to audit how it was obtained."""

    schema: str = "offgas_imposed_fo2.v1"
    status: str = COMPUTED_ASSUMPTION_UNVERIFIED
    coupling: str = WGS_EQUILIBRATED
    T_K: float = 0.0
    log10_fO2: float | None = None
    #: log10(Q/K_wgs) for the RAW gas. None when only one couple is present --
    #: the shift is undefined without both, and reporting 0.0 there would falsely
    #: assert "at equilibrium".
    log10_Q_over_K: float | None = None
    #: Shift extent in mol (positive = CO + H2O -> CO2 + H2).
    extent_mol: float | None = None
    #: Pre-equilibration couple values, kept so the reconciliation is visible.
    log10_fO2_h2_couple_raw: float | None = None
    log10_fO2_co_couple_raw: float | None = None
    #: log10 fO2(CO) - log10 fO2(H2) before equilibration; equals 2*log10(Q/K).
    raw_couple_disagreement_dex: float | None = None
    #: HOW MUCH THE ASSUMPTION MOVED THE ANSWER, in dex: the distance from the
    #: fO2 the raw gas's own couple implied to the fO2 reported after
    #: equilibration. It needs no invented threshold -- a caller reads the
    #: magnitude and decides -- and it is ALWAYS defined whenever the shift ran.
    #:
    #: An earlier definition used |2*log10(Q/K)|, the spread between the two raw
    #: couples. That reads well but goes blind in the case that matters most:
    #: this project's own primary-pyrolysis gas carries CO2 with CO a proven
    #: zero, so the raw Q is infinite and the spread is undefined -- while the
    #: REVERSE shift still consumes most of the CO2 and moves fO2 by ~0.3 dex.
    #: The assumption was doing real work and the field designed to report that
    #: work said "unmeasurable". The spread is still reported separately as
    #: `raw_couple_disagreement_dex`; this field is the one that never goes
    #: blind while the assumption is active.
    assumption_strain_dex: float | None = None
    #: Always set. This module never verifies that the shift actually ran; it
    #: cannot, because equilibration depends on residence time and catalysis,
    #: neither of which is an input here.
    equilibrium_assumption_verified: bool = False
    equilibrated_mol: dict[str, float] = field(default_factory=dict)
    #: Redox-active species present in the input that this module does not model
    #: (CH4, H2S, free O2, ...). Reported, never silently dropped.
    #:
    #: WHAT THEIR PRESENCE MEANS, stated precisely because it is easy to get
    #: wrong in both directions. Under this module's OWN equilibrium assumption
    #: they change nothing: an internally equilibrated gas has its CH4, H2S and
    #: O2 already consistent with the same fO2 the H2/H2O and CO/CO2 ratios
    #: imply, so reading it off one couple is not an approximation. To exactly
    #: the extent that assumption FAILS, they are unaccounted reducing (or
    #: oxidising) capacity that the couple ratios cannot see. So the honest
    #: reading is: these matter in proportion to `assumption_strain_dex`, and
    #: the two fields must be read together.
    unmodelled_species_mol: dict[str, float] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "coupling": self.coupling,
            "T_K": self.T_K,
            "log10_fO2": self.log10_fO2,
            "log10_Q_over_K": self.log10_Q_over_K,
            "extent_mol": self.extent_mol,
            "log10_fO2_h2_couple_raw": self.log10_fO2_h2_couple_raw,
            "log10_fO2_co_couple_raw": self.log10_fO2_co_couple_raw,
            "raw_couple_disagreement_dex": self.raw_couple_disagreement_dex,
            "assumption_strain_dex": self.assumption_strain_dex,
            "equilibrium_assumption_verified": self.equilibrium_assumption_verified,
            "equilibrated_mol": dict(self.equilibrated_mol),
            "unmodelled_species_mol": dict(self.unmodelled_species_mol),
            "notes": list(self.notes),
        }


def load_buffer_polynomials(
    extract_path: pathlib.Path | str = DEFAULT_CEA_EXTRACT,
) -> dict[str, NasaCeaPolynomial]:
    """Load the five CEA records this module needs.

    Two API details that are not obvious from the call site and cost real time
    when first found (recorded in the b-203 probe, repeated here so this module
    stands alone):
      1. coefficients nest under ``observation['values']`` (plural), and the
         segment key is ``a_coefficients`` while ``Nasa9Segment``'s field is
         ``coefficients``;
      2. ``observation['standard_state']`` is a verbose provenance string
         ("CEA/JANAF P deg=100000.0 Pa; phase_flag=0") which ``NasaCeaPolynomial``
         REJECTS. The token it wants is ``observation['phase']`` ('gas').
    """

    extract = yaml.safe_load(pathlib.Path(extract_path).read_text())
    polynomials: dict[str, NasaCeaPolynomial] = {}
    for name in BUFFER_SPECIES:
        try:
            entry = extract["species"][name]
        except (KeyError, TypeError) as exc:
            raise OffgasFO2Unavailable(
                f"CEA extract has no record for {name!r}; the offgas fO2 coupling "
                "needs all of " + ", ".join(BUFFER_SPECIES)
            ) from exc
        observation = next(
            (o for o in entry["observations"] if o.get("type") == "gibbs_table"),
            None,
        )
        if observation is None:
            raise OffgasFO2Unavailable(
                f"CEA record for {name!r} carries no gibbs_table observation"
            )
        values = observation["values"]
        polynomials[name] = NasaCeaPolynomial(
            name=name,
            family=values["evaluator_family"],
            standard_state=str(observation["phase"]),
            segments=tuple(
                Nasa9Segment(
                    T_min_K=float(s["T_min_K"]),
                    T_max_K=float(s["T_max_K"]),
                    coefficients=tuple(s["a_coefficients"]),
                    b1=float(s["b1"]),
                    b2=float(s["b2"]),
                    exponents=tuple(s["exponents"]),
                )
                for s in values["segments"]
            ),
            formula=values.get("formula"),
            molecular_weight_g_per_mol=values.get("molecular_weight_g_per_mol"),
            delta_f_H_298_15_J_per_mol=values.get("delta_f_H_298_15_J_per_mol"),
            citation=values.get("citation"),
            source_ref_code=values.get("source_ref_code"),
            reference_pressure_Pa=float(
                values.get("reference_pressure_Pa", 100_000.0)
            ),
        )
    return polynomials


def _log10_K_couple(
    polynomials: Mapping[str, NasaCeaPolynomial], couple: str, T_K: float
) -> float:
    """log10 K for H2 + 1/2 O2 <-> H2O ('H2') or CO + 1/2 O2 <-> CO2 ('CO')."""

    if couple == "H2":
        terms = [
            (+1.0, polynomials["H2O"].evaluate(T_K)),
            (-1.0, polynomials["H2"].evaluate(T_K)),
            (-0.5, polynomials["O2"].evaluate(T_K)),
        ]
    elif couple == "CO":
        terms = [
            (+1.0, polynomials["CO2"].evaluate(T_K)),
            (-1.0, polynomials["CO"].evaluate(T_K)),
            (-0.5, polynomials["O2"].evaluate(T_K)),
        ]
    else:
        raise ValueError(f"unknown couple {couple!r}; expected 'H2' or 'CO'")
    return math.log10(reaction_equilibrium_constant(terms, T_K=T_K))


def water_gas_shift_log10_K(
    polynomials: Mapping[str, NasaCeaPolynomial], T_K: float
) -> float:
    """log10 K for CO + H2O <-> CO2 + H2, as K2/K1.

    Derived, not fitted: K_wgs = K2/K1 (see module docstring -- a_O2 cancels), so
    in logs it is a subtraction of two quantities this module already computes.

    Sanity: the shift is mildly exothermic, so K_wgs falls with temperature and
    crosses unity (log10 K = 0) near ~1100 K. Both are asserted in the tests.
    """

    return _log10_K_couple(polynomials, "CO", T_K) - _log10_K_couple(
        polynomials, "H2", T_K
    )


def shift_extent(
    n_CO: float, n_H2O: float, n_CO2: float, n_H2: float, K: float
) -> float:
    """Extent xi of CO + H2O -> CO2 + H2 that brings Q to K.

    Derivation
    ----------
    Premise: the shift is mole-conserving (2 -> 2), so total moles are unchanged
    and partial pressures are proportional to moles. Q may therefore be written in
    moles directly, with no total-pressure term:

        n_CO = a - xi,  n_H2O = b - xi,  n_CO2 = c + xi,  n_H2 = d + xi

    Setting Q = K:

        K (a - xi)(b - xi) = (c + xi)(d + xi)
        K[ab - (a+b) xi + xi^2] = cd + (c+d) xi + xi^2
        (K - 1) xi^2 - [K(a+b) + (c+d)] xi + (K a b - c d) = 0

    Units: xi in mol; K dimensionless (the shift is mole-balanced, so no P deg
    factor survives). Sanity: at K = 1 the quadratic degenerates to a linear
    equation, handled explicitly below.

    Root selection: xi is physically confined to (-min(c,d), +min(a,b)) -- beyond
    either end a species would go negative. Q(xi) rises monotonically from 0 at
    the lower end to +inf at the upper end, so exactly ONE root lies inside, and
    the interval test selects it without needing a sign convention on the
    discriminant. If neither root lies inside, that is a solver failure and we
    refuse rather than clamp -- a clamped extent is a fabricated equilibrium.
    """

    if not (K > 0.0) or not math.isfinite(K):
        raise OffgasFO2Unavailable(
            f"water-gas-shift constant must be positive and finite, got {K!r}; "
            "a non-positive K has no equilibrium and the boundary root the "
            "quadratic would return is not one"
        )
    a, b, c, d = float(n_CO), float(n_H2O), float(n_CO2), float(n_H2)
    lower, upper = -min(c, d), min(a, b)

    A = K - 1.0
    B = -(K * (a + b) + (c + d))
    C = K * a * b - c * d

    if abs(A) < 1e-12:  # K == 1 exactly: linear
        if abs(B) < 1e-300:
            raise OffgasFO2Unavailable(
                "degenerate water-gas-shift system: no extent is determined"
            )
        roots = [-C / B]
    else:
        disc = B * B - 4.0 * A * C
        if disc < 0.0:
            raise OffgasFO2Unavailable(
                f"water-gas-shift has no real extent (discriminant {disc:.3e}); "
                "refusing rather than returning a fabricated equilibrium"
            )
        sqrt_disc = math.sqrt(disc)
        # Numerically stable pair: compute the well-conditioned root first, then
        # the other by Vieta (x1*x2 = C/A) to avoid catastrophic cancellation
        # when 4AC << B^2, which is the common case for a strongly-shifted gas.
        q = -0.5 * (B + math.copysign(sqrt_disc, B))
        roots = [q / A] + ([C / q] if q != 0.0 else [])

    # The pad must scale with the INTERVAL, not with max(1.0, ...). Under the
    # old form any inventory whose moles were <~1e-13 got a pad WIDER than the
    # physical interval itself: both algebraic roots were then accepted, the
    # unphysical one was taken, the clamp below walked it to the wall, and a
    # finite "equilibrated" fO2 came back ~0.66 dex wrong -- precisely the
    # fabricated equilibrium this module claims to refuse. That window includes
    # the simulator's own OXYGEN_RESERVOIR_NOOP_MOL = 1e-15.
    width = upper - lower
    if not (width > 0.0) or not math.isfinite(width):
        raise OffgasFO2Unavailable(
            f"water-gas-shift interval is degenerate ([{lower:.6g}, {upper:.6g}]); "
            "no extent is determined and none may be invented"
        )
    tol = 1e-12 * width
    inside = [r for r in roots if lower - tol <= r <= upper + tol]
    if not inside:
        raise OffgasFO2Unavailable(
            f"no physical water-gas-shift extent in [{lower:.6g}, {upper:.6g}]; "
            f"roots were {roots}"
        )
    # Clamp only within the numerical tolerance, never across a real gap.
    return min(max(inside[0], lower), upper)


def imposed_fo2(
    species_mol: Mapping[str, float],
    T_K: float,
    polynomials: Mapping[str, NasaCeaPolynomial] | None = None,
) -> OffgasFO2:
    """The single fO2 an offgas imposes at ``T_K``, after shift equilibration.

    Refuses rather than invents when the gas carries no complete redox couple:
    a gas of pure H2 with no oxidised partner imposes an unboundedly low fO2, and
    there is no honest finite number to return.
    """

    polys = polynomials if polynomials is not None else load_buffer_polynomials()

    def amount(name: str) -> float:
        """Moles of one species, refusing corrupt records rather than zeroing.

        An earlier version mapped NaN / inf / negative / non-numeric to 0.0.
        That was a fabrication path, not a tolerance: with one corrupt entry and
        one clean couple surviving, the shift would still run, INVENT the
        missing species, and return status=ok with a finite fO2. A corrupt mole
        count is missing input -- category (1) of the three fail-closed
        categories -- so it refuses. An ABSENT key is genuinely absent and
        stays 0.0; that distinction is the whole point.
        """

        if name not in species_mol:
            return 0.0
        value = species_mol[name]
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise OffgasFO2Unavailable(
                f"offgas amount for {name!r} is not numeric ({value!r}); "
                "refusing rather than treating a corrupt record as absent"
            ) from None
        if not math.isfinite(value):
            raise OffgasFO2Unavailable(
                f"offgas amount for {name!r} is {value!r}; refusing rather than "
                "treating a corrupt record as absent"
            )
        if value < 0.0:
            raise OffgasFO2Unavailable(
                f"offgas amount for {name!r} is negative ({value!r}); a negative "
                "mole count is a corrupt record, not an absence"
            )
        return value

    n_H2, n_H2O = amount("H2"), amount("H2O")
    n_CO, n_CO2 = amount("CO"), amount("CO2")

    # Count what we are NOT modelling, so the caller sees a gas description that
    # matches the gas they handed in. `amount` refuses corrupt values here too,
    # so a NaN in CH4 is caught rather than quietly excluded from the report.
    unmodelled = {
        name: amount(name)
        for name in UNMODELLED_REDOX_SPECIES
        if amount(name) > 0.0
    }

    has_h2_couple = n_H2 > 0.0 and n_H2O > 0.0
    has_co_couple = n_CO > 0.0 and n_CO2 > 0.0
    # The shift needs BOTH a carbon carrier and a hydrogen carrier to move at
    # all; a gas with only H2/H2O has no shift to run, and one with only CO/CO2
    # likewise. These two flags are set below and drive the branch order --
    # an earlier version keyed the H2-only branch off `not shift_possible`,
    # which swallowed the CO-only case into the H2 branch and refused it.
    shift_possible = (n_CO > 0.0 or n_CO2 > 0.0) and (n_H2 > 0.0 or n_H2O > 0.0)

    if not (has_h2_couple or has_co_couple) and not shift_possible:
        raise OffgasFO2Unavailable(
            "offgas carries no complete redox couple (need H2 with H2O, or CO "
            f"with CO2): H2={n_H2:g} H2O={n_H2O:g} CO={n_CO:g} CO2={n_CO2:g}. "
            "A one-sided couple imposes an unbounded fO2 and must refuse."
        )

    log10_K1 = _log10_K_couple(polys, "H2", T_K)
    log10_K2 = _log10_K_couple(polys, "CO", T_K)

    def fo2_h2(h2: float, h2o: float) -> float | None:
        if h2 <= 0.0 or h2o <= 0.0:
            return None
        return 2.0 * (math.log10(h2o / h2) - log10_K1)

    def fo2_co(co: float, co2: float) -> float | None:
        if co <= 0.0 or co2 <= 0.0:
            return None
        return 2.0 * (math.log10(co2 / co) - log10_K2)

    raw_h2 = fo2_h2(n_H2, n_H2O)
    raw_co = fo2_co(n_CO, n_CO2)

    # Single-couple gases: nothing to reconcile, and log10(Q/K) is UNDEFINED.
    # Reporting 0.0 there would assert "at shift equilibrium", which is a claim
    # about a reaction that cannot even proceed.
    has_carbon = n_CO > 0.0 or n_CO2 > 0.0
    has_hydrogen = n_H2 > 0.0 or n_H2O > 0.0

    if not has_carbon:
        if raw_h2 is None:
            raise OffgasFO2Unavailable(
                "offgas has no carbon carrier and an incomplete H2/H2O couple"
            )
        return OffgasFO2(
            coupling=H2_COUPLE_ONLY,
            T_K=float(T_K),
            log10_fO2=raw_h2,
            log10_Q_over_K=None,
            extent_mol=None,
            log10_fO2_h2_couple_raw=raw_h2,
            log10_fO2_co_couple_raw=None,
            raw_couple_disagreement_dex=None,
            equilibrated_mol={"H2": n_H2, "H2O": n_H2O},
            unmodelled_species_mol=unmodelled,
            status=COMPUTED_NO_RECONCILIATION,
            notes=(
                "no carbon carrier: water-gas shift cannot proceed, so Q/K is "
                "undefined rather than unity",
                "nothing was reconciled, so no equilibrium assumption was made "
                "and assumption_strain_dex is None rather than zero",
            ),
        )
    if not has_hydrogen:
        if raw_co is None:
            raise OffgasFO2Unavailable(
                "offgas has no hydrogen carrier and an incomplete CO/CO2 couple"
            )
        return OffgasFO2(
            coupling=CO_COUPLE_ONLY,
            T_K=float(T_K),
            log10_fO2=raw_co,
            log10_Q_over_K=None,
            extent_mol=None,
            log10_fO2_h2_couple_raw=None,
            log10_fO2_co_couple_raw=raw_co,
            raw_couple_disagreement_dex=None,
            equilibrated_mol={"CO": n_CO, "CO2": n_CO2},
            unmodelled_species_mol=unmodelled,
            status=COMPUTED_NO_RECONCILIATION,
            notes=(
                "no hydrogen carrier: water-gas shift cannot proceed, so Q/K is "
                "undefined rather than unity",
                "nothing was reconciled, so no equilibrium assumption was made "
                "and assumption_strain_dex is None rather than zero",
            ),
        )

    log10_K_wgs = log10_K2 - log10_K1
    K_wgs = 10.0 ** log10_K_wgs

    log10_Q_over_K: float | None = None
    disagreement: float | None = None
    if n_CO > 0.0 and n_H2O > 0.0 and n_CO2 > 0.0 and n_H2 > 0.0:
        log10_Q = math.log10((n_CO2 * n_H2) / (n_CO * n_H2O))
        log10_Q_over_K = log10_Q - log10_K_wgs
        if raw_h2 is not None and raw_co is not None:
            disagreement = raw_co - raw_h2

    extent = shift_extent(n_CO, n_H2O, n_CO2, n_H2, K_wgs)
    eq = {
        "CO": n_CO - extent,
        "H2O": n_H2O - extent,
        "CO2": n_CO2 + extent,
        "H2": n_H2 + extent,
    }

    eq_h2 = fo2_h2(eq["H2"], eq["H2O"])
    eq_co = fo2_co(eq["CO"], eq["CO2"])
    # Prefer whichever couple is well-conditioned after the shift; they agree by
    # construction, so this is a numerical choice and not a physical one.
    value = eq_h2 if eq_h2 is not None else eq_co
    if value is None:
        raise OffgasFO2Unavailable(
            "water-gas-shift equilibration left no complete couple; refusing"
        )

    # Placeholder; computed below once the equilibrated value exists.
    strain: float | None = None

    # How far equilibration actually moved the answer. Prefer the H2 couple as
    # the reference when both exist; either raw couple is a valid "what you
    # would otherwise have read", and they bracket the result.
    raw_reference = raw_h2 if raw_h2 is not None else raw_co
    if raw_reference is not None:
        strain = abs(value - raw_reference)

    notes: list[str] = [
        "fO2 read after assuming the offgas reaches water-gas-shift equilibrium "
        "at melt temperature; log10_Q_over_K reports how far the raw gas was "
        "from that state",
        "redox-active species outside H2/H2O and CO/CO2 are counted in "
        "unmodelled_species_mol, not dropped: under the equilibrium assumption "
        "they are already consistent with this fO2, and they represent "
        "unaccounted reducing capacity exactly insofar as that assumption fails",
        "shift KINETICS are not modelled: uncatalysed gas-phase water-gas shift "
        "is slow, and whether it equilibrates depends on residence time and "
        "catalysis, neither of which is an input here. assumption_strain_dex "
        "says how many dex the assumption is worth on this gas -- treat a large "
        "strain as evidence AGAINST the assumption, not as a wider error bar",
    ]
    if log10_Q_over_K is None:
        notes.append(
            "raw gas had an incomplete couple (one of CO/CO2/H2/H2O absent), so "
            "its Q is not finite and log10_Q_over_K is None rather than zero. "
            "The shift STILL RAN -- a reverse shift generates the missing "
            "partner -- so read assumption_strain_dex, which reports how far "
            "that moved the answer and does not go blind here"
        )

    return OffgasFO2(
        coupling=WGS_EQUILIBRATED,
        T_K=float(T_K),
        log10_fO2=value,
        log10_Q_over_K=log10_Q_over_K,
        extent_mol=extent,
        log10_fO2_h2_couple_raw=raw_h2,
        log10_fO2_co_couple_raw=raw_co,
        raw_couple_disagreement_dex=disagreement,
        assumption_strain_dex=strain,
        equilibrium_assumption_verified=False,
        equilibrated_mol=eq,
        unmodelled_species_mol=unmodelled,
        status=COMPUTED_ASSUMPTION_UNVERIFIED,
        notes=tuple(notes),
    )


__all__ = [
    "BUFFER_SPECIES",
    "WGS_EQUILIBRATED",
    "H2_COUPLE_ONLY",
    "CO_COUPLE_ONLY",
    "COMPUTED_ASSUMPTION_UNVERIFIED",
    "COMPUTED_NO_RECONCILIATION",
    "UNMODELLED_REDOX_SPECIES",
    "OffgasFO2",
    "OffgasFO2Unavailable",
    "load_buffer_polynomials",
    "water_gas_shift_log10_K",
    "shift_extent",
    "imposed_fo2",
]
