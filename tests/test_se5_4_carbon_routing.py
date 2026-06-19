"""S-E5-4 confirmatory residual-C routing sweep."""

from __future__ import annotations

from copy import deepcopy

import pytest

from engines.alphamelts.domain import AlphaMELTSDomainGate
from engines.builtin.melt_effect_adjustment import (
    evaluate_verdict_a,
    melt_effect_adjustment,
    strip_non_oxide_residuals,
)
from engines.domain_reason import OutOfDomainReason
from engines.magemin.domain import MAGEMinDomainGate
from simulator.session import SimSession
from simulator.stage0_harness import run_stage0_harness
from tests.test_stage0_harness import _feedstocks, _session_config


_DOMAIN_CARRIERS = ("C", "graphite", "carbonaceous_organic")
_REDOX_CARRIERS = ("C", "graphite")
_REAL_CARBONACEOUS_FEEDSTOCKS = (
    "ci_carbonaceous_chondrite",
    "cm_carbonaceous_chondrite",
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


def _synthetic_elemental_carbon_feedstocks() -> tuple[dict, str]:
    feedstocks = deepcopy(_feedstocks())
    feedstock_id = "se5_4_elemental_c_carrier"
    feedstock = deepcopy(feedstocks["lunar_mare_low_ti"])
    composition = dict(feedstock["composition_wt_pct"])
    for species in _DOMAIN_CARRIERS:
        composition.pop(species, None)
    composition["C"] = 0.3
    feedstock["composition_wt_pct"] = composition
    feedstocks[feedstock_id] = feedstock
    return feedstocks, feedstock_id


@pytest.mark.parametrize("carrier", _DOMAIN_CARRIERS)
def test_se5_4_carbon_carriers_domain_exit_forbidden_species(carrier: str):
    composition = dict(_BASALT_OXIDE_KG)
    composition[carrier] = 0.1

    for gate in (AlphaMELTSDomainGate, MAGEMinDomainGate):
        valid, warnings, reason = gate.validate_with_reason(composition)
        assert valid is False
        assert warnings
        assert reason == OutOfDomainReason.FORBIDDEN_SPECIES.value
        assert carrier in " ".join(warnings)


@pytest.mark.parametrize("carrier", _REDOX_CARRIERS)
def test_se5_4_carbon_redox_warning_stays_ungrounded(carrier: str):
    adjustment = melt_effect_adjustment(
        {carrier: 2.0},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1400.0,
    )
    redox = [
        perturbation
        for perturbation in adjustment.perturbations
        if perturbation.property == "redox"
    ]
    assert len(redox) == 1
    perturbation = redox[0]
    assert perturbation.effect_row == "residual_carbon"
    assert perturbation.metric == "delta_log10_fO2"
    assert perturbation.interval == pytest.approx((0.20, 1.00))
    assert perturbation.perturbation_after == pytest.approx(0.40)
    assert perturbation.grounded is False
    assert perturbation.correctable is False
    assert "Brooker" in perturbation.source
    assert "Sephton" in perturbation.source
    assert any("noise_floor_ungrounded" in warning for warning in adjustment.warnings)

    flags = evaluate_verdict_a(adjustment.perturbations, hour=0)
    redox_flags = [
        flag for flag in flags if flag.property == "redox" and flag.level == "WARNING"
    ]
    assert len(redox_flags) == 1
    flag = redox_flags[0]
    assert flag.contaminant == carrier
    assert flag.effect_row == "residual_carbon"
    assert flag.grounded is False
    assert flag.correctable is False
    assert flag.noise_floor_status == "noise_floor_ungrounded"


def test_se5_4_elemental_c_is_stripped_and_never_enters_cleaned_melt():
    stripped = strip_non_oxide_residuals({**_BASALT_OXIDE_KG, "C": 0.3})
    assert stripped.stripped_kg["C"] == pytest.approx(0.3)
    assert stripped.provenance
    assert stripped.provenance[0].species == "C"
    assert stripped.provenance[0].reason == "non_oxide_residual_stripped_before_engine"

    feedstocks, feedstock_id = _synthetic_elemental_carbon_feedstocks()
    session = SimSession().start(
        _session_config(feedstock_id, feedstocks=feedstocks),
    )
    result = run_stage0_harness(session)
    assert result.early_melt_reached is True

    ledger_melt = session.simulator.atom_ledger.kg_by_account("process.cleaned_melt")
    for species in _DOMAIN_CARRIERS:
        assert result.cleaned_melt_kg.get(species, 0.0) == pytest.approx(0.0)
        assert ledger_melt.get(species, 0.0) == pytest.approx(0.0)


@pytest.mark.parametrize("feedstock_id", _REAL_CARBONACEOUS_FEEDSTOCKS)
def test_se5_4_real_carbonaceous_feedstocks_keep_carbon_out_of_cleaned_melt(
    feedstock_id: str,
):
    session = SimSession().start(_session_config(feedstock_id))
    result = run_stage0_harness(session)
    assert result.early_melt_reached is True

    ledger_melt = session.simulator.atom_ledger.kg_by_account("process.cleaned_melt")
    for species in _DOMAIN_CARRIERS:
        assert result.cleaned_melt_kg.get(species, 0.0) == pytest.approx(0.0)
        assert ledger_melt.get(species, 0.0) == pytest.approx(0.0)

    diagnostics = [
        diagnostic
        for diagnostic in session.simulator._stage0_foulant_diagnostics
        if diagnostic.get("reaction_family") == "partition_carbon"
    ]
    assert diagnostics
    assert {diagnostic.get("carrier") for diagnostic in diagnostics} == {
        "carbonaceous_organic"
    }

    burned_events = [
        event
        for entry in result.disposition_timeline
        for event in entry.by_group["trapped_gasses"]
        if event.get("reaction_family") == "partition_carbon"
        and event.get("disposition") == "burned"
    ]
    residual_events = [
        event
        for entry in result.disposition_timeline
        for event in entry.by_group["refractory_carbon"]
        if event.get("reaction_family") == "partition_carbon"
        and event.get("disposition") == "residual"
    ]
    assert burned_events
    assert residual_events
