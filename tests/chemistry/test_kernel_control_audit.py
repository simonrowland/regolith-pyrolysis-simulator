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
