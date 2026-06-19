"""S-E5-5 confirmatory S/FeS sulfide-matte routing sweep."""

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


_DOMAIN_CARRIERS = ("S", "FeS", "FeS_troilite", "SO3")
_MELT_EFFECT_CASES = (
    ("S", "sulfide", ("Jugo et al. 2010", "SCSS")),
    ("FeS", "sulfide", ("Jugo et al. 2010", "SCSS")),
    ("SO3", "sulfate_proxy", ("Jugo", "sulfate clearance")),
)
_STAGE0_CASES = (
    ("S", "S", ("S",), True),
    ("FeS", "FeS_troilite", ("FeS", "FeS_troilite"), True),
    ("SO3", "SO3", ("SO3", "CaSO4"), False),
)
_BASALT_OXIDE_KG = {
    "SiO2": 45.0,
    "Al2O3": 15.0,
    "FeO": 12.0,
    "MgO": 10.0,
    "CaO": 10.0,
    "Na2O": 4.0,
    "K2O": 4.0,
}


def _synthetic_sulfide_feedstocks(carrier: str) -> tuple[dict, str]:
    feedstocks = deepcopy(_feedstocks())
    feedstock_id = f"se5_5_{carrier.lower()}_carrier"
    feedstock = deepcopy(feedstocks["lunar_mare_low_ti"])
    composition = dict(feedstock["composition_wt_pct"])
    for species in _DOMAIN_CARRIERS:
        composition.pop(species, None)
    composition[carrier] = 0.3
    feedstock["composition_wt_pct"] = composition
    feedstocks[feedstock_id] = feedstock
    return feedstocks, feedstock_id


@pytest.mark.parametrize("carrier", _DOMAIN_CARRIERS)
def test_se5_5_sulfide_carriers_domain_exit_forbidden_species(carrier: str):
    composition = dict(_BASALT_OXIDE_KG)
    composition[carrier] = 0.1

    for gate in (AlphaMELTSDomainGate, MAGEMinDomainGate):
        valid, warnings, reason = gate.validate_with_reason(composition)
        assert valid is False
        assert warnings
        assert reason == OutOfDomainReason.FORBIDDEN_SPECIES.value
        assert carrier in " ".join(warnings)


@pytest.mark.parametrize(("carrier", "effect_row", "source_terms"), _MELT_EFFECT_CASES)
def test_se5_5_sulfide_warning_is_ungrounded_jugo_track(
    carrier: str,
    effect_row: str,
    source_terms: tuple[str, ...],
):
    adjustment = melt_effect_adjustment(
        {carrier: 0.3},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1400.0,
    )
    phase = [
        perturbation
        for perturbation in adjustment.perturbations
        if perturbation.property == "phase"
    ]
    assert len(phase) == 1
    perturbation = phase[0]
    assert perturbation.effect_row == effect_row
    assert perturbation.grounded is False
    assert perturbation.correctable is False
    for term in source_terms:
        assert term in perturbation.source

    flags = evaluate_verdict_a(adjustment.perturbations, hour=0)
    phase_flags = [
        flag for flag in flags if flag.property == "phase" and flag.level == "WARNING"
    ]
    assert len(phase_flags) == 1
    flag = phase_flags[0]
    assert flag.contaminant == carrier
    assert flag.effect_row == effect_row
    assert flag.grounded is False
    assert flag.correctable is False
    assert flag.noise_floor_status == "noise_floor_ungrounded"


@pytest.mark.parametrize(
    ("label", "stage0_carrier", "forbidden_species", "expect_matte"),
    _STAGE0_CASES,
)
def test_se5_5_sulfide_stage0_never_leaks_into_cleaned_melt(
    label: str,
    stage0_carrier: str,
    forbidden_species: tuple[str, ...],
    expect_matte: bool,
):
    feedstocks, feedstock_id = _synthetic_sulfide_feedstocks(stage0_carrier)
    session = SimSession().start(
        _session_config(feedstock_id, feedstocks=feedstocks),
    )
    result = run_stage0_harness(session)
    assert result.early_melt_reached is True

    ledger_melt = session.simulator.atom_ledger.kg_by_account("process.cleaned_melt")
    for species in forbidden_species:
        assert result.cleaned_melt_kg.get(species, 0.0) == pytest.approx(0.0)
        assert ledger_melt.get(species, 0.0) == pytest.approx(0.0)

    inventory_matte = session.simulator.inventory.sulfide_matte_kg
    ledger_matte = session.simulator.atom_ledger.kg_by_account(
        "terminal.stage0_sulfide_matte"
    )
    if expect_matte:
        assert inventory_matte.get(stage0_carrier, 0.0) > 0.0
        assert ledger_matte.get(stage0_carrier, 0.0) > 0.0
    else:
        assert inventory_matte == {}
        assert ledger_matte == {}
        sulfate_diagnostics = [
            diagnostic
            for diagnostic in session.simulator._stage0_foulant_diagnostics
            if diagnostic.get("reaction_family") == "sulfate_decomp"
        ]
        assert sulfate_diagnostics
        assert any(
            diagnostic.get("carrier") in {"SO3", "CaSO4"}
            for diagnostic in sulfate_diagnostics
        )


def test_se5_5_existing_mars_sulfate_feedstock_keeps_so3_out_of_cleaned_melt():
    session = SimSession().start(_session_config("mars_sulfate_rich"))
    result = run_stage0_harness(session)
    assert result.early_melt_reached is True

    ledger_melt = session.simulator.atom_ledger.kg_by_account("process.cleaned_melt")
    assert result.cleaned_melt_kg.get("SO3", 0.0) == pytest.approx(0.0)
    assert ledger_melt.get("SO3", 0.0) == pytest.approx(0.0)
    assert session.simulator.inventory.sulfide_matte_kg == {}
    assert session.simulator.atom_ledger.kg_by_account(
        "terminal.stage0_sulfide_matte"
    ) == {}
    assert any(
        diagnostic.get("reaction_family") == "sulfate_decomp"
        for diagnostic in session.simulator._stage0_foulant_diagnostics
    )
