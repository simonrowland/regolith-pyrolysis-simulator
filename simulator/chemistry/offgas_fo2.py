"""b-203: the single fO2 a reducing offgas imposes, via water-gas-shift equilibration.

THE PROBLEM. A reducing offgas does not debit oxygen from the melt; it IMPOSES an
oxygen fugacity through its ratios, exactly as an H2/H2O or CO/CO2 mix is used to
impose fO2 in the laboratory. Inverting either couple is elementary (see
``docs-private/research/2026-08-18-b203-buffer/probe_buffer_fo2.py``, which
sanity-checks the inversion against the Frost 1991 iron-wustite buffer and
independently reproduces the ~1100 K CO2/CO-vs-H2O/H2 crossover, computed at
1095.7 K):

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
    couples agree identically. The raw couples can disagree by many dex, and
    that gap DIVERGES as either couple's partner goes to zero -- measured +4 dex
    per decade of vanishing CO -- so no bounded figure belongs here. It is
    reported per call as ``raw_couple_disagreement_dex``, and is None when a
    couple is one-sided.

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
independent evidence that the physics is correct.

The non-circular check is the PUBLISHED-K_wgs comparison in
``tests/chemistry/test_offgas_fo2.py`` (external K_wgs at 800 and 1200 K,
in-domain and externally anchored). NOT the iron-wustite comparison: an earlier
version of this header headlined that probe's 0.003 dex agreement at 1873 K,
which is exactly 400 K ABOVE Frost's 1473.15 K validity ceiling. Recomputed
INSIDE the window the residual is 0.99 dex at 838 K, 0.53 at 1100 and 0.19 at
1473 K -- an in-window max ~5x this project's own 0.20 dex tolerance, and 64x to
330x worse than the headline. The near-zero at 1873 K is a CURVE CROSSING, not
agreement: fitting the in-window residual gives a +29.4 kJ/mol O2 enthalpy gap
against a +16.5 J/mol/K entropy gap, crossing zero near 1781 K -- verbatim the
compensating-errors artifact benchmarks/buffer_reproduction.py warns about, and
the same 0.2-1.1 dex band Hirschmann (2021) predicts for a JANAF-refit rail. The
module's own test had ALREADY retracted this claim and restricted itself to the
window with a deliberately loose < 1.0 dex bound; this header had not followed
(SC-130 sweep, OG-3). Read the IW comparison as a coarse sanity check good to
~0.2-1.0 dex over 838-1473 K -- enough to catch a sign flip or a wrong reaction,
not an accuracy certificate.

STATUS: computation + diagnostic only. This module imposes nothing. Wiring it to
move melt fO2 is golden-affecting and is a separate, owner-gated step.
"""

from __future__ import annotations

import math
import pathlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

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
    #: Species this module does NOT equilibrate: CH4, H2S, COS, N2 and any
    #: other inventory outside the two water-gas-shift couples. They are
    #: FROZEN — they enter no equation here (not the couple inversions, not
    #: log10_Q_over_K, not the shift extent, not the strain).
    #:
    #: READ THIS BEFORE JUDGING THE ASSUMPTION SAFE (SC-130 sweep, OG-1). An
    #: earlier version of this comment, and the runtime note beside it, said
    #: these species "change nothing" under the equilibrium assumption and
    #: "unmodelled species (CH4/H2S/COS/N2) are FROZEN: they enter no equation here. This is WGS-only equilibrium, not full internal equilibrium, and assumption_strain_dex is structurally blind to steam-methane reforming -- a small strain is NOT evidence that CH4 is harmless (measured: strain 0.00 exactly while CH4 moves true equilibrium 0.85 dex, same direction every time -- CH4 makes the truth MORE reducing than this value)". Both halves are wrong,
    #: and the second is INVERTED. The equivocation: "internally equilibrated
    #: gas" is true of FULL internal equilibrium, but what this module
    #: computes is WGS-ONLY equilibrium with CH4 frozen. assumption_strain_dex
    #: measures only the WGS deviation and is structurally blind to the
    #: steam-methane-reforming component, which is exactly where CH4 lives.
    #:
    #: Measured against an independent solver built from this module's own CEA
    #: records: fO2 and strain are BIT-IDENTICAL for CH4 = 0, 1.2, 100 and 1e6
    #: mol, up to 99.9996 mol% CH4. A gas sitting exactly at WGS equilibrium
    #: reports strain 0.00 EXACTLY while 8.9 mol% CH4 moves true internal
    #: equilibrium by 0.85 dex; another well-posed case shows a 1.18 dex error
    #: against a reported strain of 0.061, ~20x. A quantity identically zero
    #: cannot be a proportionality constant for a 0.85 dex effect.
    #:
    #: So: a SMALL STRAIN IS NOT EVIDENCE THAT CH4 IS HARMLESS. Observed sign
    #: — CH4 makes true equilibrium MORE REDUCING, and this module returns the
    #: less-reducing value. This matters because the designed input is
    #: CH4-bearing and CO-free (organics_pyrolysis emits H2/CH4/H2O/CO2/N2/
    #: H2S/COS with CO forced to zero).
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


#: Which CEA records each couple's log10 K actually reads. Domain validation is
#: per-couple because the couples do NOT share a window: on the shipped extract
#: H2O stops at 6000 K while CO, CO2 and O2 reach 20000 K, so K2 stays a genuine
#: evaluation in a band where K1 would be an extrapolation. Intersecting all five
#: records would refuse a CO-only gas at 7000 K for the unusability of a constant
#: that gas never needs -- and would say so in a message that is false about K2.
_COUPLE_RECORDS: Mapping[str, tuple[str, ...]] = {
    "H2": ("H2O", "H2", "O2"),
    "CO": ("CO2", "CO", "O2"),
}


def _effective_domain_K(
    polynomials: Mapping[str, NasaCeaPolynomial], records: Sequence[str]
) -> tuple[float, float]:
    """The temperature window in which every record in ``records`` is valid.

    One couple's log10 K is a sum over three species' records, so it is defined
    only where ALL THREE are: the usable window is the INTERSECTION of those
    records' domains, [max(T_min), min(T_max)] -- never any single record's
    range, and never the intersection over records the caller does not need.

    That distinction matters for the message as much as for the bound, though
    less uniformly than an earlier version of this docstring claimed. The
    underlying evaluator names whichever species it reached first. Through
    ``water_gas_shift_log10_K`` the CO couple goes first, so an out-of-domain
    call at 100 K reported "[200, 20000] K" -- true of CO2, false of the quantity
    requested. Through ``imposed_fo2`` the H2 couple goes first and H2O is always
    the binding record there, so the same call reported "[200, 6000] K": the
    right interval, reached for the wrong reason, and only by luck of evaluation
    order.

    Computed rather than hardcoded, so a re-extract that widens or narrows any
    record moves this bound with it instead of silently disagreeing.
    """

    return (
        max(polynomials[name].T_min_K for name in records),
        min(polynomials[name].T_max_K for name in records),
    )


def _safe_type_name(obj: object) -> str:
    """The type name of ``obj`` as a plain str, or a placeholder.

    Raises nothing ORDINARY; BaseException still propagates, deliberately (see
    the policy note in the body). "Never raises" would be the easier sentence and
    the false one.

    ``type(x).__name__`` looks total and is not: a metaclass can raise on the
    attribute. Message construction reached for it directly on the CAUGHT
    EXCEPTION, so a value whose conversion raised could still break the refusal
    path while it was being described -- the same shape as the value-rendering
    hole, one argument to the right, and not covered by the tests written for
    that one.
    """

    # Exception, NOT BaseException -- the same policy _safe_repr argues for below,
    # and for the same reason. An earlier revision caught BaseException here,
    # which meant _safe_repr's fallback (which calls this) would swallow a
    # KeyboardInterrupt after all: two adjacent helpers with opposite policies on
    # one question, the rationale written on only one.
    #
    # "".join() rather than a bare return, because guarding the LOOKUP is not
    # enough. A metaclass may return a non-str, or a str subclass whose __format__
    # raises; that object then gets formatted at the call site, OUTSIDE this try,
    # and the refusal dies there instead. join() both validates (a non-str raises
    # TypeError, caught here) and produces a plain str, which f-string formatting
    # cannot make fail.
    try:
        return "".join(type(obj).__name__)
    except Exception:  # noqa: BLE001 - ordinary failures must not break a message
        return "<unnameable>"


def _safe_repr(value: object, limit: int = 120) -> str:
    """repr() that will not raise an ordinary exception, for error messages.

    Building a refusal must not fail for an ordinary reason. Two separate defects
    got here, and merging them into one sentence -- as an earlier revision did --
    loses the second lesson:

      1. The first guard caught only (TypeError, ValueError), so on a
         10000-digit int the OverflowError from float() escaped uncaught.
      2. The revision that ADDED the OverflowError catch then interpolated the
         value directly, and repr() raised ValueError on that same input
         ("exceeds the limit (4300 digits) for integer string conversion") --
         so the refusal path threw a foreign exception one line inside the fix
         for foreign exceptions.

    Missing catch, then message construction that can itself raise. This module's
    documented defect class is inaccurate self-history, so the archaeology is
    kept exact on purpose.

    "Ordinary" is load-bearing and is not a hedge: see the scope note in the body.
    """

    # EVERYTHING is inside the try, including len() and the slice. An earlier
    # version guarded only repr(), so a str subclass whose __len__ raises escaped
    # straight past -- re-opening the exact hole the caller uses this to close.
    #
    # SCOPE, stated exactly rather than absolutely: this catches Exception, not
    # BaseException. KeyboardInterrupt, SystemExit and GeneratorExit still
    # propagate, and that is deliberate -- swallowing an interrupt to finish
    # formatting an error message would be a worse bug than the one being
    # reported. An earlier revision of this docstring claimed the function
    # "cannot itself raise" for "ANY value"; that was an overclaim of exactly the
    # kind this module is under audit for, so the contract is narrowed here to
    # what the code delivers instead of the code being widened to a promise it
    # should not make.
    try:
        # "".join for the same reason as _safe_type_name: repr() may hand back a
        # str SUBCLASS whose __format__ raises, and that would detonate at the
        # call site rather than here. join() yields a plain str.
        text = "".join(repr(value))
        return (
            text if len(text) <= limit else f"{text[:limit]}... ({len(text)} chars)"
        )
    except Exception:  # noqa: BLE001 - ordinary failures must not break a message
        return f"<unrepresentable {_safe_type_name(value)}>"


def _coerce_T_K(T_K: float) -> float:
    """Normalise a caller's temperature to a finite float, or refuse.

    Split out from the domain check because the two have different scopes. This
    part is caller-shaped and couple-independent -- a temperature that is not a
    finite number is unusable for ANY couple -- so it can run once at entry. The
    domain check is couple-DEPENDENT (the CO couple's records reach 20000 K
    while the H2 couple's stop at 6000 K), so it must stay next to the constant
    being evaluated.

    Idempotent on a float, which is what lets the entry point and the per-couple
    path both call it without the second call reconverting the caller's original
    object. That matters: ``imposed_fo2`` used to hold the RAW T_K and re-run
    ``float()`` on it when building its result, so the refusal contract held only
    because validation happened to run first on every REACHABLE path -- an
    argument that a later branch could silently invalidate. It now holds
    structurally, at BOTH public entry points: ``imposed_fo2`` rebinds T_K and
    ``water_gas_shift_log10_K`` binds a local ``T``, each to the coerced value,
    before either branches; the three result constructions that used to re-call
    ``float()`` now pass that value straight through.

    Be precise about WHAT is converted once, because two reviewers instrumented
    this and each read it a different wrong way. It is the CALLER'S OBJECT: its
    ``__float__`` runs once per SUCCESSFUL public call -- measured at 1 for the
    shift, for a CO-only gas and for a two-couple gas. On a REFUSAL it may run
    again, because the message renders the offending value and a re-entrant
    ``__repr__`` is free to call ``float(self)``; that was measured at 2 and is
    harmless, since the call still ends in OffgasFO2Unavailable. It is NOT the
    number of ``float()`` calls in the
    module -- ``_validate_T_K`` coerces again per evaluated couple, so with a
    built-in float as input you will count two or three. Those later calls
    receive an already-normalised float and are idempotent no-ops; they are kept
    deliberately, so that this function's guarantee does not depend on every
    future caller of ``_log10_K_couple`` remembering to normalise first. That
    reachability argument is exactly what failed here once already.

    Why it matters beyond tidiness: a stateful float-like handed separately to
    two evaluations produced an equilibrium constant belonging to neither
    temperature.
    """

    try:
        T = float(T_K)
    except Exception as exc:  # noqa: BLE001 - see the note below on breadth
        # Exception, not a (TypeError, ValueError, OverflowError) tuple. A
        # caller's __float__ may raise anything at all -- an ordinary
        # RuntimeError from one leaked out of both public APIs under the narrow
        # tuple. Whatever it raises, the conclusion is identical: this object
        # cannot serve as a temperature, which is precisely what this module's
        # refusal type means. Narrowing here would trade a true statement about
        # the input for a foreign exception in the caller's lap. BaseException
        # still propagates.
        # OverflowError belongs with the other two: float(10**10000) raises it,
        # and an ordinary Python int is not a caller error exotic enough to
        # deserve escaping the refusal contract this module exists to hold.
        raise OffgasFO2Unavailable(
            f"melt temperature is not convertible to a float "
            f"({_safe_repr(T_K)}): {_safe_type_name(exc)}"
        ) from exc
    if not math.isfinite(T):
        raise OffgasFO2Unavailable(
            f"melt temperature is {T!r}; the offgas fO2 coupling has no "
            "equilibrium constant at a non-finite temperature"
        )
    return T


def _validate_T_K(
    polynomials: Mapping[str, NasaCeaPolynomial],
    T_K: float,
    records: Sequence[str],
    quantity: str,
) -> float:
    """Refuse an unusable temperature in THIS module's own exception type.

    Without this guard an unvalidated T_K travels into the CEA polynomials and
    surfaces as NasaCeaDomainError (<- NasaCeaError <- ValueError), while every
    other refusal in this module is OffgasFO2Unavailable (<- Exception). Those
    two hierarchies are disjoint, so no single exception TYPE covers both. (Not
    "no single except clause" -- ``except (OffgasFO2Unavailable, NasaCeaError):``
    is one clause and does cover both; it just requires the caller to know that
    the second type exists and is reachable from here.) The consequence is the
    same: a caller handling OffgasFO2Unavailable takes the foreign ValueError as
    a crash, and one catching ValueError swallows a genuine refusal as an
    arithmetic complaint. Verified escaping before this
    guard: nan, 0.0, -5.0, 100.0, 7000.0, 1e6, inf, and 10**10000.

    Note on NaN, corrected after review: the range test below is NEGATED, so it
    already rejects NaN -- ``lo <= nan <= hi`` is False and ``not False`` raises.
    The explicit finite check is not what prevents NaN from reaching the
    evaluator; it is what makes the refusal say "non-finite temperature" instead
    of misreporting NaN as a value lying outside a numeric interval. Both are
    worth having, for different reasons.

    (An earlier revision of this docstring claimed the range test ADMITS NaN.
    It does not. That claim was itself an instance of the class this module was
    being audited for -- prose contradicting the algebra directly beneath it --
    written into the repair for that very class.)
    """

    T = _coerce_T_K(T_K)
    lo, hi = _effective_domain_K(polynomials, records)
    if not (lo <= T <= hi):
        raise OffgasFO2Unavailable(
            # repr(), not :g. At a representational boundary :g rounds the
            # temperature INTO the interval it is being refused for --
            # nextafter(20000.0, inf) printed as "20000 K is outside
            # [200, 20000] K", while the exact boundary 20000.0 is genuinely
            # evaluable. The message then contradicted itself for the one reader
            # who most needed it. repr() round-trips, so the displayed value is
            # the value that was rejected.
            f"melt temperature {T!r} K is outside the shared domain of the CEA "
            f"records {quantity} reads ({', '.join(records)}): "
            f"[{lo!r}, {hi!r}] K. {quantity} there would be an extrapolation of "
            "a fit that was never valid at that temperature rather than an "
            "evaluation of one that was."
        )
    return T


def _log10_K_couple(
    polynomials: Mapping[str, NasaCeaPolynomial], couple: str, T_K: float
) -> float:
    """log10 K for H2 + 1/2 O2 <-> H2O ('H2') or CO + 1/2 O2 <-> CO2 ('CO').

    Every temperature that reaches an EQUILIBRIUM CONSTANT funnels through here,
    so this is where the per-couple DOMAIN is checked -- rather than a domain
    guard per public entry point that the next entry point can forget to repeat.

    Not a universal chokepoint, though an earlier version of this sentence said
    so: ``imposed_fo2({"H2": 1.0}, 1500.0)`` coerces and refuses on the
    partnerless gas without calling this function at all. The architecture is
    finite-coercion at entry plus per-couple domain validation here, which is two
    guards with different scopes, not one.
    """

    try:
        records = _COUPLE_RECORDS[couple]
    except KeyError:
        raise ValueError(
            f"unknown couple {couple!r}; expected 'H2' or 'CO'"
        ) from None
    T_K = _validate_T_K(polynomials, T_K, records, f"log10 K({couple})")

    if couple == "H2":
        terms = [
            (+1.0, polynomials["H2O"].evaluate(T_K)),
            (-1.0, polynomials["H2"].evaluate(T_K)),
            (-0.5, polynomials["O2"].evaluate(T_K)),
        ]
    else:  # "CO"; _COUPLE_RECORDS lookup above rejected anything else
        terms = [
            (+1.0, polynomials["CO2"].evaluate(T_K)),
            (-1.0, polynomials["CO"].evaluate(T_K)),
            (-0.5, polynomials["O2"].evaluate(T_K)),
        ]
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

    # Coerce ONCE, here, then hand both couples the same built-in float. Passing
    # the caller's raw object to each evaluation separately let a stateful
    # float-like return 1000 K to one call and 2000 K to the other; the result
    # was a cross-temperature subtraction returned as an equilibrium constant at
    # neither temperature (observed: 6.675 where the two fixed-T values are
    # 0.157 and -0.661). Every public entry point normalises before it branches.
    T = _coerce_T_K(T_K)
    return _log10_K_couple(polynomials, "CO", T) - _log10_K_couple(
        polynomials, "H2", T
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
    # Normalise the temperature ONCE, here, and use this value everywhere below.
    # The per-couple DOMAIN check stays inside _log10_K_couple, because the two
    # couples do not share a window and this function must not refuse a CO-only
    # gas for K1's narrower records.
    T_K = _coerce_T_K(T_K)

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

    # Each couple's constant is computed ON DEMAND, and only for a couple this
    # gas actually carries. The two couples do not share a temperature domain
    # (see _COUPLE_RECORDS), so evaluating both up front would refuse a CO-only
    # gas anywhere above H2O's 6000 K ceiling because of a constant that gas
    # never uses. The single-couple branches below return before log10_K_wgs,
    # which is the one place that legitimately needs both.
    _K_cache: dict[str, float] = {}

    def log10_K(couple: str) -> float:
        if couple not in _K_cache:
            _K_cache[couple] = _log10_K_couple(polys, couple, T_K)
        return _K_cache[couple]

    def fo2_h2(h2: float, h2o: float) -> float | None:
        if h2 <= 0.0 or h2o <= 0.0:
            return None
        return 2.0 * (math.log10(h2o / h2) - log10_K("H2"))

    def fo2_co(co: float, co2: float) -> float | None:
        if co <= 0.0 or co2 <= 0.0:
            return None
        return 2.0 * (math.log10(co2 / co) - log10_K("CO"))

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
            T_K=T_K,  # already coerced at entry
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
            T_K=T_K,  # already coerced at entry
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

    log10_K_wgs = log10_K("CO") - log10_K("H2")
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
    # "the shift ran" = an extent was solved, i.e. the module committed to a
    # post-shift answer rather than reporting the raw gas.
    shift_ran = extent is not None

    # How far equilibration actually moved the answer. Prefer the H2 couple as
    # the reference when both exist; either raw couple is a valid "what you
    # would otherwise have read", and they bracket the result.
    raw_reference = raw_h2 if raw_h2 is not None else raw_co
    if raw_reference is not None:
        strain = abs(value - raw_reference)
    elif shift_ran:
        # FAIL-OPEN CLOSED (SC-130 sweep, OG-2). Both couples one-sided clears
        # the composition gate, so the shift runs and produces a confident
        # finite fO2 while no PRE-shift reference exists to measure movement
        # against. Leaving strain None there made the obvious caller guard
        #     if strain is not None and strain > threshold: reject
        # accept the worst case and reject benign ones. Measured continuity
        # probe on {CO, H2O} with a vanishing H2 trace, fO2 bit-identical
        # throughout: 1e-3 -> 5.61 dex, 1e-9 -> 17.61, 1e-20 -> 39.61,
        # 1e-30 -> 59.61, and exactly 0.0 -> None. The limit is not "unknown",
        # it DIVERGES, and the module's own rationale for refusing a single
        # one-sided couple is that it "imposes an unbounded fO2". Infinity is
        # the truthful value and it makes the natural guard fail closed.
        strain = math.inf

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
        T_K=T_K,  # already coerced at entry
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
