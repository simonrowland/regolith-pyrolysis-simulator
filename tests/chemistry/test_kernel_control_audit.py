"""Kernel invariant: applied T/P/fO2 must match requested -- or be explained.

A provider may not silently override the caller's temperature,
pressure, or oxygen fugacity request.  Drift between
:attr:`ControlAudit.requested` and :attr:`ControlAudit.applied` must
either fall within tolerance OR be accompanied by a free-form
:attr:`ControlAudit.notes` entry explaining the deviation.
"""

from __future__ import annotations

import pytest

from simulator.accounting.ledger import AtomLedger
from simulator.chemistry.kernel import (
    CapabilityProfile,
    ChemistryIntent,
    ChemistryKernel,
    ChemistryProvider,
    ControlAudit,
    ControlAuditMismatch,
    IntentRequest,
    IntentResult,
    ProviderRegistry,
)
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.chemistry.kernel.validation import validate_control_audit


def _build_request(
    temperature_C: float, pressure_bar: float, fO2_log: float | None = None
) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.SILICATE_LIQUIDUS,
        account_view=ProviderAccountView(accounts={}, species_formula_registry={}),
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        fO2_log=fO2_log,
        control_inputs={},
    )


def test_validate_control_audit_exact_match_passes():
    request = _build_request(1400.0, 1e-6, fO2_log=-12.0)
    audit = ControlAudit(
        requested={"temperature_C": 1400.0, "pressure_bar": 1e-6, "fO2_log": -12.0},
        applied={"temperature_C": 1400.0, "pressure_bar": 1e-6, "fO2_log": -12.0},
        notes=(),
    )
    validate_control_audit(audit, request)


def test_validate_control_audit_pressure_drift_without_notes_raises():
    request = _build_request(1400.0, 1.0)
    audit = ControlAudit(
        requested={"temperature_C": 1400.0, "pressure_bar": 1.0, "fO2_log": None},
        applied={"temperature_C": 1400.0, "pressure_bar": 50.0, "fO2_log": None},
        notes=(),  # no explanation
    )
    with pytest.raises(ControlAuditMismatch):
        validate_control_audit(audit, request)


def test_validate_control_audit_drift_with_notes_passes():
    request = _build_request(1400.0, 1.0)
    audit = ControlAudit(
        requested={"temperature_C": 1400.0, "pressure_bar": 1.0},
        applied={"temperature_C": 1400.0, "pressure_bar": 1e-6},
        notes=("pressure clamped to engine minimum 1e-6 bar",),
    )
    validate_control_audit(audit, request)


def test_validate_control_audit_temperature_drift_without_notes_raises():
    request = _build_request(1400.0, 1.0)
    audit = ControlAudit(
        requested={"temperature_C": 1400.0, "pressure_bar": 1.0},
        applied={"temperature_C": 1450.0, "pressure_bar": 1.0},
        notes=(),
    )
    with pytest.raises(ControlAuditMismatch):
        validate_control_audit(audit, request)


def test_validate_control_audit_fO2_drift_without_notes_raises():
    request = _build_request(1400.0, 1.0, fO2_log=-12.0)
    audit = ControlAudit(
        requested={"temperature_C": 1400.0, "pressure_bar": 1.0, "fO2_log": -12.0},
        applied={"temperature_C": 1400.0, "pressure_bar": 1.0, "fO2_log": -10.0},
        notes=(),
    )
    with pytest.raises(ControlAuditMismatch):
        validate_control_audit(audit, request)


def test_validate_control_audit_unspecified_fO2_request_ignored():
    """If the caller did not pin fO2, engine readback is informational."""

    request = _build_request(1400.0, 1.0, fO2_log=None)
    audit = ControlAudit(
        requested={"temperature_C": 1400.0, "pressure_bar": 1.0, "fO2_log": None},
        applied={"temperature_C": 1400.0, "pressure_bar": 1.0, "fO2_log": -8.5},
        notes=(),
    )
    validate_control_audit(audit, request)


# ---------------------------------------------------------------------------
# Kernel-level: dispatch surfaces the mismatch end-to-end.


class _PressureClampingProvider(ChemistryProvider):
    """A provider that always reports clamped pressure with NO notes."""

    name = "pressure_clamp_bad"

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="pressure_clamp_bad",
            intents=frozenset({ChemistryIntent.SILICATE_LIQUIDUS}),
            is_authoritative_for=frozenset({ChemistryIntent.SILICATE_LIQUIDUS}),
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=None,
            control_audit=ControlAudit(
                requested={
                    "temperature_C": request.temperature_C,
                    "pressure_bar": request.pressure_bar,
                    "fO2_log": request.fO2_log,
                },
                applied={
                    "temperature_C": request.temperature_C,
                    "pressure_bar": 1e-6,  # clamped, but no note
                    "fO2_log": request.fO2_log,
                },
                notes=(),
            ),
            diagnostic={},
            warnings=(),
        )


def test_kernel_dispatch_surfaces_control_audit_mismatch():
    ledger = AtomLedger()
    registry = ProviderRegistry()
    registry.register(_PressureClampingProvider(), [ChemistryIntent.SILICATE_LIQUIDUS])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    with pytest.raises(ControlAuditMismatch):
        kernel.dispatch(
            ChemistryIntent.SILICATE_LIQUIDUS,
            temperature_C=1400.0,
            pressure_bar=1.0,
            declared_accounts=frozenset({"process.cleaned_melt"}),
        )


# ---------------------------------------------------------------------------
# Builtin providers populate ControlAudit on every dispatch.
#
# Closes the F-A3 review finding: pre-fix every builtin authoritative
# provider returned IntentResult(control_audit=None), so the kernel's
# validate_control_audit (planner.py:266-267) was dead code.  After the
# fix each provider builds a ControlAudit that mirrors requested
# verbatim with a free-form note ("diagnostic only" for the engines that
# have no independent feedback loop; "anode evolves pure O2" for
# ELECTROLYSIS_STEP).  The validator passes when applied matches
# requested OR the audit carries an explanatory note.

from tests.chemistry.conftest import _build_sim


def test_vapor_pressure_provider_populates_control_audit(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Diagnostic provider: VAPOR_PRESSURE returns a ControlAudit with
    applied == requested and a non-empty note.  Drive the kernel
    end-to-end so we know the validator sees the audit.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    result = sim._chem_kernel.dispatch(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )
    assert result.control_audit is not None
    audit = result.control_audit
    assert audit.applied.get("temperature_C") == pytest.approx(1500.0)
    assert audit.applied.get("pressure_bar") == pytest.approx(1e-6)
    assert audit.notes, "diagnostic ControlAudit must carry an explanatory note"


def test_electrolysis_step_provider_populates_control_audit(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Authoritative provider: ELECTROLYSIS_STEP returns a ControlAudit
    whose applied T/P mirror requested.  Drive the kernel end-to-end so
    we know the validator sees the audit.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    # Seed a small cleaned_melt so the provider has stock to dispatch
    # against (else it short-circuits via _empty_result).  The kg amount
    # passes the 1e-6 kg gate inside the provider.
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt", {"FeO": 5.0}, source="test seed"
    )
    result = sim._chem_kernel.dispatch(
        ChemistryIntent.ELECTROLYSIS_STEP,
        temperature_C=1500.0,
        pressure_bar=1.0,
        control_inputs={
            "voltage_V": 2.5,
            "current_A": 100.0,
            "dt_hr": 1.0,
        },
    )
    assert result.control_audit is not None
    audit = result.control_audit
    assert audit.applied.get("temperature_C") == pytest.approx(1500.0)
    assert audit.applied.get("pressure_bar") == pytest.approx(1.0)
    # Anode-fO2 lives at the pure-O2 boundary by construction.
    assert audit.applied.get("fO2_log") == pytest.approx(0.0)
    assert audit.notes, "ELECTROLYSIS_STEP ControlAudit must carry the anode-O2 note"


def test_kernel_dispatch_raises_when_provider_reports_off_temperature(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Mutation test: monkeypatch the VAPOR_PRESSURE provider to report
    ``applied['temperature_C']`` 10 C off requested with no explanatory
    note; assert ``ChemistryKernel.dispatch`` raises
    :class:`ControlAuditMismatch`.

    Proves the kernel actually consults validate_control_audit at the
    dispatch boundary -- the whole point of populating ControlAudit in
    the builtin providers.
    """

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    from engines.builtin.vapor_pressure import (
        BuiltinVaporPressureProvider,
    )
    from simulator.chemistry.kernel.dto import ControlAudit as _CA

    original_dispatch = BuiltinVaporPressureProvider.dispatch

    def _drifted_dispatch(self, request):
        original = original_dispatch(self, request)
        # Replace the audit with one that drifts T by +10 C AND clears
        # the notes -- both conditions must hold to fail the validator.
        drifted_audit = _CA(
            requested={
                "temperature_C": float(request.temperature_C),
                "pressure_bar": float(request.pressure_bar),
                "fO2_log": (
                    float(request.fO2_log) if request.fO2_log is not None else None
                ),
            },
            applied={
                "temperature_C": float(request.temperature_C) + 10.0,
                "pressure_bar": float(request.pressure_bar),
                "fO2_log": (
                    float(request.fO2_log) if request.fO2_log is not None else None
                ),
            },
            notes=(),
        )
        return type(original)(
            intent=original.intent,
            status=original.status,
            transition=original.transition,
            control_audit=drifted_audit,
            diagnostic=original.diagnostic,
            warnings=original.warnings,
        )

    monkeypatch.setattr(
        BuiltinVaporPressureProvider, "dispatch", _drifted_dispatch
    )

    with pytest.raises(ControlAuditMismatch):
        sim._chem_kernel.dispatch(
            ChemistryIntent.VAPOR_PRESSURE,
            temperature_C=1500.0,
            pressure_bar=1e-6,
            control_inputs={"pO2_bar": 1e-9},
        )
