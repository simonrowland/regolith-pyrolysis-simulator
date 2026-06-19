"""S-E5-1 confirmatory Cl/NaCl/KCl routing sweep."""

from __future__ import annotations

from copy import deepcopy

import pytest

from engines.alphamelts.domain import AlphaMELTSDomainGate
from engines.builtin.melt_effect_adjustment import (
    evaluate_verdict_a,
    melt_effect_adjustment,
)
from engines.domain_reason import OutOfDomainReason
from engines.magemin.domain import MAGEMinDomainGate
from simulator.session import SimSession
from simulator.stage0_harness import run_stage0_harness
from tests.test_stage0_harness import _feedstocks, _session_config


_CHLORIDE_CARRIERS = ("Cl", "NaCl", "KCl")
_BASALT_OXIDE_KG = {
    "SiO2": 45.0,
    "Al2O3": 15.0,
    "FeO": 12.0,
    "MgO": 10.0,
    "CaO": 10.0,
    "Na2O": 4.0,
    "K2O": 4.0,
}


def _synthetic_chloride_feedstocks(carrier: str) -> tuple[dict, str]:
    feedstocks = deepcopy(_feedstocks())
    feedstock_id = f"se5_1_{carrier.lower()}_carrier"
    feedstock = deepcopy(feedstocks["lunar_mare_low_ti"])
    composition = dict(feedstock["composition_wt_pct"])
    for species in _CHLORIDE_CARRIERS:
        composition.pop(species, None)
    composition[carrier] = 0.3
    feedstock["composition_wt_pct"] = composition
    feedstocks[feedstock_id] = feedstock
    return feedstocks, feedstock_id


@pytest.mark.parametrize("carrier", _CHLORIDE_CARRIERS)
def test_se5_1_cl_carriers_domain_exit_warn_and_never_enter_cleaned_melt(
    carrier: str,
):
    composition = dict(_BASALT_OXIDE_KG)
    composition[carrier] = 0.1
    for gate in (AlphaMELTSDomainGate, MAGEMinDomainGate):
        valid, warnings, reason = gate.validate_with_reason(composition)
        assert valid is False
        assert warnings
        assert reason == OutOfDomainReason.FORBIDDEN_SPECIES.value
        assert carrier in " ".join(warnings)

    adjustment = melt_effect_adjustment(
        {carrier: 0.3},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1400.0,
    )
    liquidus = [
        perturbation
        for perturbation in adjustment.perturbations
        if perturbation.property == "liquidus"
    ]
    assert len(liquidus) == 1
    perturbation = liquidus[0]
    assert perturbation.effect_row == "cl_halide"
    assert perturbation.grounded is True
    assert perturbation.correctable is True
    assert "Filiberto" in perturbation.source
    assert "Treiman" in perturbation.source
    assert adjustment.adjusted_liquidus_provenance == (
        {
            "contaminant": carrier,
            "effect_row": "cl_halide",
            "source": perturbation.source,
            "delta_T_C": pytest.approx(-30.0),
            "grounded": True,
        },
    )

    flags = evaluate_verdict_a(adjustment.perturbations, hour=0)
    liquidus_flags = [
        flag for flag in flags if flag.property == "liquidus" and flag.level == "WARNING"
    ]
    assert len(liquidus_flags) == 1
    flag = liquidus_flags[0]
    assert flag.contaminant == carrier
    assert flag.effect_row == "cl_halide"
    assert flag.grounded is True
    assert flag.correctable is True
    assert flag.noise_floor_status == "proposed"

    feedstocks, feedstock_id = _synthetic_chloride_feedstocks(carrier)
    session = SimSession().start(
        _session_config(feedstock_id, feedstocks=feedstocks),
    )
    result = run_stage0_harness(session)
    assert result.early_melt_reached is True

    ledger_melt = session.simulator.atom_ledger.kg_by_account("process.cleaned_melt")
    for species in _CHLORIDE_CARRIERS:
        assert result.cleaned_melt_kg.get(species, 0.0) == pytest.approx(0.0)
        assert ledger_melt.get(species, 0.0) == pytest.approx(0.0)

    chloride_diagnostics = [
        diagnostic
        for diagnostic in session.simulator._stage0_foulant_diagnostics
        if diagnostic.get("reaction_family") == "volatilization"
        and (
            diagnostic.get("source_component") == carrier
            or diagnostic.get("carrier") == carrier
        )
    ]
    assert chloride_diagnostics
