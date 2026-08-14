"""W0-SENS patched-build evaluator and production synthetic-gate callbacks.

Frozen by PREREGISTRATION-wave0.md step 2 (line 37) and step 4 (line 39).
Two of the four seams the aborted 2026-08-14 attempt found missing
(``sensitivity-screen/SCREEN-RESULTS.md:47-56``) live here:

- :class:`PatchedBuildEvaluator` — the seam that routes a benchmark
  evaluation THROUGH a mutator-produced build. It calls
  :meth:`MutationBuildRecord.require_row` at the evaluation seam (nothing
  else does, outside the gate) and drives the custodian worker's
  ``evaluate-corpus`` command against exactly one build prefix's ``lib/``.
- :class:`SyntheticGateCallbacks` — the PRODUCTION ``evaluate`` / ``analytic``
  pair the mandatory signed synthetic gate
  (:func:`benchmarks.w0_sens.w_mutator.synthetic_response_gate`) requires.
  The repository previously carried synthetic test callbacks only.

Nothing here ranks, reads a quarantined W value, or runs a screen.

--------------------------------------------------------------------------
THE ANALYTIC RESPONSE — DERIVATION (the gate's whole content)
--------------------------------------------------------------------------
Step 2 requires "a synthetic row with an analytic response [that]
must reproduce both signs to 1e-8 relative or 1e-12 absolute before any
candidate is screened". The preregistration does not state the analytic
form, so it is derived here from the MELTS liquid model's own structure and
recorded as AMBIGUITY A-5 in ``benchmarks/w0_sens/screen.py``.

PREMISE. The MELTS ``liq_mod=v1.0`` liquid is a symmetric (Margules)
regular solution over its liquid endmembers, with molar excess Gibbs energy

    G_ex = sum_{i<j} W_ij X_i X_j                                     (1)

and the ``referenceValuesOfModelParameters[]`` array in
``src/LiquidMelts.m`` holding the W_ij. STRUCTURAL CHECK of that premise on
the live build: the phase reports 15 liquid endmembers and exactly 105
parameters, and 15*14/2 = 105 — one W per unordered endmember pair, every
live parameter name parsing as ``W(<endmember>,<endmember>)`` with all 105
pairs distinct. A model carrying ternary or asymmetric terms could not have
that count.

ALGEBRA. For n_k moles of endmember k and n = sum_i n_i, the total excess
is G_ex_tot = n * G_ex = sum_{i<j} W_ij n_i n_j / n, so the partial molar
excess of endmember k is

    mu_k^ex = d(G_ex_tot)/dn_k
            = sum_{i<j} W_ij [ (delta_ik n_j + delta_jk n_i)/n
                               - n_i n_j / n^2 ]
            = sum_{i != k} W_ki X_i - sum_{i<j} W_ij X_i X_j.          (2)

Differentiating (2) with respect to ONE named constant W_AB (A != B), at
FIXED composition, gives the target-free unit-W basis for endmember k:

    b_k = d(mu_k)/d(W_AB)
        = [A == k] X_B + [B == k] X_A - X_A X_B.                       (3)

Because (2) is exactly LINEAR in each W_ij, (3) is not a first-order
approximation: substituting W_AB -> value_J changes the endmember chemical
potential by exactly

    mu_k(value_J) - mu_k(0) = value_J * b_k.                           (4)

(4) is the analytic response this gate tests. The measured side is the
difference between the patched build's chemical potential and the SAME
quantity from a ``0 J`` control build at the SAME synthetic mole vector and
T, so the reference state, the ideal RT ln X term, and every OTHER W_ij
cancel identically — no quarantined value is read or inferred.

UNIT CHECK. value_J is J/mol; b_k is dimensionless (a product/sum of mole
fractions); the response is J/mol, matching a chemical potential
difference. No gas constant and no temperature enter, so the gate is also
free of any disagreement between the engine's internal R and CODATA — which
at 1e-8 relative tolerance would otherwise dominate.

SANITY CHECKS. (i) value_J = 0 gives zero response. (ii) X_A -> 0 (the
named partner absent) gives b_k -> 0 for k = B: a constant that multiplies
an absent component cannot move anything. (iii) With the frozen synthetic
state below (base 0.01 mol on all 15 endmembers, +0.30 on A, +0.55 on B,
target k = A) b_A = X_B - X_A X_B = 0.56 - 0.31*0.56 = 0.3864, so the
+/-10,000 J responses are +/-3,864 J/mol — three orders of magnitude above
any plausible arithmetic noise, i.e. the gate is not vacuous. A vacuous
basis is refused outright (:data:`MIN_ANALYTIC_BASIS`).
(iv) The response is temperature independent, consistent with the
preregistration's own statement (line 148) that the held snapshot exposes
``W_H`` only, with no ``W_S``/``W_V`` terms.

WHAT A GATE FAILURE MEANS. Either the substitution is numerically silent
(the failure step 2 exists to catch) or premise (1) is wrong for this
build. Both are ``ABORT-W-MUTATOR`` under step 2 and both must stop the
screen; the emitted record states the measured and expected responses so an
auditor can tell which.

WHAT THIS GATE DOES NOT ESTABLISH. The gate checks the build's response at
exactly the two frozen probe magnitudes, ``+10,000 J`` and ``-10,000 J``.
A nonlinear response engineered to agree with ``W * b_k`` at exactly those
two points passes BOTH probes while differing everywhere else, so the gate
cannot generally validate the symmetric regular-solution premise (1); it
establishes only that THIS build responds exactly linearly at the two
frozen magnitudes (anti-symmetrically, since both signs are checked). The
structural check above (15 endmembers, 105 = 15*14/2 parameters) is
necessary-but-not-sufficient evidence for premise (1) and is NOT
re-verified by the gate at runtime — the gate's own runtime checks are the
endmember-count agreement against the control, the mole-fraction drift
guard, and the vacuous-basis floor. A third probe at a different magnitude
would narrow (never close) this gap, and it is deliberately NOT added:
every non-frozen magnitude is outside the preregistered perturbation set
``{0, +10000, -10000} J``, which ``W0WMutator.make_build`` refuses to
build (AMBIGUITY A-15). The limit is therefore disclosed, in this
docstring and in the emitted gate record
(``SyntheticGateCallbacks.as_record``), rather than narrowed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from benchmarks.w0_sens.corpus import CellOutcome, EligiblePoint
from benchmarks.w0_sens.w_mutator import (
    CONTROL_J,
    MINUS_J,
    PLUS_J,
    SYNTHETIC_GATE_ROW_ID,
    AbortWMutator,
    MutationBuildRecord,
    W0WMutator,
    _spawn_worker,
    observed_value_fingerprint,
)


# Frozen synthetic state point. Deliberately NOT a benchmark composition:
# every liquid endmember carries the same base charge and the two named
# components of the screened parameter carry the rest, so the state point is
# a pure fabrication with no chemical target in it. Strictly positive in
# every endmember because the phase nudges exact zeros (see the worker).
SYNTHETIC_BASE_MOL = 0.01
SYNTHETIC_COMPONENT_I_MOL = 0.30
SYNTHETIC_COMPONENT_J_MOL = 0.55
SYNTHETIC_TEMPERATURE_K = 1673.15
SYNTHETIC_PRESSURE_BAR = 1.0

# A basis this small would make the gate vacuous (it would pass whether or
# not the substitution took effect). Refuse rather than emit a green gate.
MIN_ANALYTIC_BASIS = 0.05

_MOLE_FRACTION_TOLERANCE = 1.0e-12


@dataclass(frozen=True)
class SyntheticGateState:
    """The fabricated state point the step-2 analytic response is defined at."""

    label: str
    component_i: str
    component_j: str
    target_endmember: str
    base_mol: float = SYNTHETIC_BASE_MOL
    component_i_mol: float = SYNTHETIC_COMPONENT_I_MOL
    component_j_mol: float = SYNTHETIC_COMPONENT_J_MOL
    temperature_K: float = SYNTHETIC_TEMPERATURE_K
    pressure_bar: float = SYNTHETIC_PRESSURE_BAR

    @property
    def component_mol(self) -> dict[str, float]:
        return {
            self.component_i: float(self.component_i_mol),
            self.component_j: float(self.component_j_mol),
        }

    def mole_fractions(self, n_endmembers: int) -> dict[str, float]:
        """Mole fractions of the two named components, from OUR arithmetic.

        Only the endmember COUNT comes from the engine; the composition is
        the custodian's own fabrication, so the analytic reference never
        borrows a number from the model it is checking.
        """
        count = int(n_endmembers)
        if count < 2:
            raise AbortWMutator(
                f"live liquid phase reports {count} endmembers; the synthetic "
                "state point needs at least two"
            )
        extras = self.component_mol
        total = count * float(self.base_mol) + sum(extras.values())
        if total <= 0.0:
            raise AbortWMutator("synthetic state point has non-positive total moles")
        return {
            name: (float(self.base_mol) + extras[name]) / total for name in extras
        }

    def analytic_basis(self, n_endmembers: int) -> float:
        """``b_k = [A==k] X_B + [B==k] X_A - X_A X_B`` — see module docstring (3)."""
        fractions = self.mole_fractions(n_endmembers)
        x_i = fractions[self.component_i]
        x_j = fractions[self.component_j]
        basis = (
            (x_j if self.target_endmember == self.component_i else 0.0)
            + (x_i if self.target_endmember == self.component_j else 0.0)
            - x_i * x_j
        )
        if abs(basis) < MIN_ANALYTIC_BASIS:
            raise AbortWMutator(
                f"synthetic analytic basis {basis!r} for target "
                f"{self.target_endmember!r} is below {MIN_ANALYTIC_BASIS}; the "
                "gate would be vacuous"
            )
        return basis


def default_synthetic_state(param_name: str) -> SyntheticGateState:
    """The frozen synthetic state point for one ``W(A,B)`` parameter.

    The target endmember is ``A`` (the first named component), which is
    always present at the synthetic composition, so the basis (3) keeps its
    leading ``X_B`` term and stays far from zero.
    """
    mutator_name_parts = _split_param_name(param_name)
    component_i, component_j = mutator_name_parts
    return SyntheticGateState(
        label=f"synthetic-binary::{component_i}+{component_j}",
        component_i=component_i,
        component_j=component_j,
        target_endmember=component_i,
    )


def _split_param_name(param_name: str) -> tuple[str, str]:
    from benchmarks.w0_sens.w_mutator import PARAM_NAME_RE

    match = PARAM_NAME_RE.fullmatch(str(param_name))
    if match is None:
        raise AbortWMutator(f"unrecognized MELTS W parameter name: {param_name!r}")
    return match.group(1), match.group(2)


@dataclass(frozen=True)
class SyntheticProbe:
    """One synthetic-state chemical potential read off one build."""

    mu_J_per_mol: float
    readback_J: float
    n_endmembers: int
    mole_fractions: Mapping[str, float]


class PatchedBuildEvaluator:
    """Routes a benchmark evaluation through one mutator-produced build.

    Production requires the default worker seam: an injected
    ``worker_runner`` flips :attr:`production` to ``False`` and the screen
    runner then refuses to emit a released aggregate, mirroring
    ``W0WMutator``'s :class:`NonProductionMutationBuild` typing.
    """

    def __init__(
        self,
        *,
        worker_runner: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self._worker_runner = (
            worker_runner if worker_runner is not None else _spawn_worker
        )
        self.production = worker_runner is None

    def _run(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        payload = self._worker_runner(spec)
        if not isinstance(payload, dict) or "ok" not in payload:
            raise AbortWMutator(
                f"custodian worker returned a malformed envelope: {payload!r}"
            )
        if not payload["ok"]:
            raise AbortWMutator(
                "custodian worker aborted: "
                f"{payload.get('abort', 'ABORT-W-MUTATOR')}: "
                f"{payload.get('detail', '<no detail>')}"
            )
        return payload

    @staticmethod
    def _bind(build: MutationBuildRecord, row_id: str) -> Path:
        # The evaluation seam is the ONLY place that enforces the frozen
        # fresh-build-per-(row, perturbation) binding; the mutator cannot,
        # because it never evaluates (w_mutator.py:240-247).
        build.require_row(row_id)
        return Path(build.prefix) / "lib"

    def _check_readback(self, build: MutationBuildRecord, payload: Mapping[str, Any]) -> None:
        readback = float(payload["readback_J"])
        if readback != float(build.perturbation_J):
            # MISMATCH FACT, never the value: a readback that is not the
            # frozen substitution may be the quarantined held W.
            raise AbortWMutator(
                f"evaluation-time readback for {build.param_name!r} (slot "
                f"{build.slot_index}) does not equal the build's "
                f"substituted value {float(build.perturbation_J)!r} J; "
                f"observed_value_sha256={observed_value_fingerprint(readback)}"
            )

    def evaluate_corpus(
        self,
        build: MutationBuildRecord,
        *,
        row_id: str,
        points: Sequence[EligiblePoint],
    ) -> dict[str, CellOutcome]:
        """Evaluate every eligible row against this build, in one process."""
        prefix_lib = self._bind(build, row_id)
        if not points:
            raise AbortWMutator("the eligible corpus is empty; nothing to evaluate")
        payload = self._run(
            {
                "command": "evaluate-corpus",
                "prefix_lib": str(prefix_lib),
                "param_name": build.param_name,
                "expected_value_J": float(build.perturbation_J),
                "points": [
                    {
                        "id": point.point_id,
                        "composition_id": point.composition_id,
                        "temperature_K": point.temperature_K,
                        "observable": point.observable,
                        "parent_oxide": point.parent_oxide,
                        "species": point.species,
                        "measured": point.measured,
                        "composition_wt_pct": dict(point.composition_wt_pct),
                        **(
                            {}
                            if point.fO2_bar is None
                            else {"fO2_bar": float(point.fO2_bar)}
                        ),
                    }
                    for point in points
                ],
            }
        )
        self._check_readback(build, payload)
        cells: dict[str, CellOutcome] = {}
        for entry in list(payload["cells"]):
            point_id = str(entry["point_id"])
            raw = entry.get("value")
            cells[point_id] = CellOutcome(
                point_id=point_id,
                value=None if raw is None else float(raw),
                status=str(entry["status"]),
                reason=str(entry.get("reason") or ""),
            )
        missing = sorted({point.point_id for point in points} - set(cells))
        if missing:
            # Missing input refuses: an unreported row is not a typed missing
            # cell, it is an incomplete evaluation.
            raise AbortWMutator(
                f"custodian worker reported no cell for eligible rows {missing}"
            )
        return cells

    def synthetic_probe(
        self, build: MutationBuildRecord, *, state: SyntheticGateState
    ) -> SyntheticProbe:
        """Chemical potential of the target endmember at the synthetic point."""
        prefix_lib = self._bind(build, SYNTHETIC_GATE_ROW_ID)
        payload = self._run(
            {
                "command": "synthetic-chem-potential",
                "prefix_lib": str(prefix_lib),
                "param_name": build.param_name,
                "expected_value_J": float(build.perturbation_J),
                "base_mol": float(state.base_mol),
                "component_mol": state.component_mol,
                "target_endmember": state.target_endmember,
                "temperature_K": float(state.temperature_K),
                "pressure_bar": float(state.pressure_bar),
            }
        )
        self._check_readback(build, payload)
        probe = SyntheticProbe(
            mu_J_per_mol=float(payload["mu_J_per_mol"]),
            readback_J=float(payload["readback_J"]),
            n_endmembers=int(payload["n_endmembers"]),
            mole_fractions=dict(payload["mole_fractions"]),
        )
        expected = state.mole_fractions(probe.n_endmembers)
        for name, value in expected.items():
            reported = float(probe.mole_fractions.get(name, float("nan")))
            if abs(reported - value) > _MOLE_FRACTION_TOLERANCE:
                raise AbortWMutator(
                    f"synthetic mole fraction for {name!r} drifted between the "
                    f"custodian arithmetic ({value!r}) and the build "
                    f"({reported!r}); the analytic basis would not apply"
                )
        return probe


class SyntheticGateCallbacks:
    """PRODUCTION ``evaluate`` / ``analytic`` pair for the step-2 gate.

    ``evaluate`` returns the SIGNED CHEMICAL-POTENTIAL RESPONSE of the
    patched build against a dedicated ``0 J`` control build at the same
    synthetic state point; ``analytic`` returns ``value_J * b_k`` from
    equation (4) of the module docstring. The control is built once, under
    the reserved synthetic row identity, so it consumes no candidate's
    ``(row, perturbation)`` slot.
    """

    def __init__(
        self,
        *,
        mutator: W0WMutator,
        evaluator: PatchedBuildEvaluator,
        state: SyntheticGateState | None = None,
    ) -> None:
        self._mutator = mutator
        self._evaluator = evaluator
        self._state = (
            state if state is not None else default_synthetic_state(mutator.param_name)
        )
        self._control: SyntheticProbe | None = None
        self._control_build: MutationBuildRecord | None = None
        self._basis: float | None = None

    @property
    def state(self) -> SyntheticGateState:
        return self._state

    @property
    def analytic_basis(self) -> float:
        if self._basis is None:
            raise AbortWMutator(
                "the synthetic gate control has not been built; the analytic "
                "basis is not yet defined"
            )
        return self._basis

    @property
    def control_build(self) -> MutationBuildRecord:
        if self._control_build is None:
            raise AbortWMutator("the synthetic gate control has not been built")
        return self._control_build

    def build_control(self) -> SyntheticProbe:
        """Build and read the ``0 J`` synthetic control (idempotent)."""
        if self._control is not None:
            return self._control
        build = self._mutator.make_build(CONTROL_J, row_id=SYNTHETIC_GATE_ROW_ID)
        probe = self._evaluator.synthetic_probe(build, state=self._state)
        self._control_build = build
        self._control = probe
        self._basis = self._state.analytic_basis(probe.n_endmembers)
        return probe

    def evaluate(self, build: MutationBuildRecord) -> float:
        control = self.build_control()
        probe = self._evaluator.synthetic_probe(build, state=self._state)
        if probe.n_endmembers != control.n_endmembers:
            raise AbortWMutator(
                "the patched build reports a different endmember count than "
                f"the control ({probe.n_endmembers} vs {control.n_endmembers})"
            )
        return probe.mu_J_per_mol - control.mu_J_per_mol

    def analytic(self, value_J: float) -> float:
        return float(value_J) * self.analytic_basis

    def as_record(self) -> dict[str, Any]:
        """Releasable description of the gate's state point and basis."""
        return {
            "state_label": self._state.label,
            "component_i": self._state.component_i,
            "component_j": self._state.component_j,
            "target_endmember": self._state.target_endmember,
            "base_mol": self._state.base_mol,
            "component_mol": self._state.component_mol,
            "temperature_K": self._state.temperature_K,
            "pressure_bar": self._state.pressure_bar,
            "analytic_basis_dimensionless": self._basis,
            "analytic_form": "mu_k(W) - mu_k(0) = W * b_k",
            "probe_magnitudes_J": [PLUS_J, MINUS_J],
            "premise_validation_limit": (
                "The gate verifies the build's response at exactly the two "
                "frozen probe magnitudes (+/-10,000 J). A nonlinear response "
                "engineered to match W*b_k at exactly those two points would "
                "pass both probes while differing elsewhere, so the gate "
                "does NOT generally validate the symmetric regular-solution "
                "premise; the 15-endmember/105-parameter structural count is "
                "necessary-but-not-sufficient and is not re-verified by the "
                "gate. A third probe at another magnitude would narrow "
                "(never close) this gap, but the frozen perturbation set "
                "{0, +10000, -10000} J forbids it, so the limit is "
                "disclosed rather than narrowed."
            ),
            "control_build_prefix": (
                None if self._control_build is None else self._control_build.prefix
            ),
        }


def canonical_json(payload: Any) -> str:
    """Deterministic JSON for hashed custodian artifacts."""
    return json.dumps(payload, sort_keys=True, indent=2, default=str) + "\n"
