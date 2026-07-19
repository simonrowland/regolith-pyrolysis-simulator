"""S-E5-1 confirmatory Cl/NaCl/KCl routing sweep."""

from __future__ import annotations

from copy import deepcopy

import pytest

from engines.alphamelts.domain import AlphaMELTSDomainGate
from engines.builtin.melt_effect_adjustment import (
    CertifiedPointRefusedError,
    evaluate_verdict_a,
    melt_effect_adjustment,
    request_certified_point,
)
from engines.domain_reason import OutOfDomainReason
from engines.magemin.domain import MAGEMinDomainGate
from simulator.session import SimSession
from simulator.stage0_harness import run_stage0_harness
from tests.test_stage0_harness import _feedstocks, _session_config


_CHLORIDE_CARRIERS = ("Cl", "NaCl", "KCl")
_CL_MASS_FRACTION = {
    "Cl": 1.0,
    "NaCl": 35.45 / (22.98976928 + 35.45),
    "KCl": 35.45 / (39.0983 + 35.45),
}
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

    carrier_wt_pct = 0.3 / _CL_MASS_FRACTION[carrier]
    adjustment = melt_effect_adjustment(
        {carrier: carrier_wt_pct},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1300.0,
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


def test_cl_liquidus_temperature_gate_uses_demonstrated_run_envelope() -> None:
    in_range = melt_effect_adjustment(
        {"Cl": 0.3},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1300.0,
    )
    at_bound = melt_effect_adjustment(
        {"Cl": 0.7},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1340.0,
    )
    at_lower_bound = melt_effect_adjustment(
        {"Cl": 0.3},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1200.0,
    )
    outside_temperature = melt_effect_adjustment(
        {"Cl": 0.3},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1340.01,
    )
    below_temperature = melt_effect_adjustment(
        {"Cl": 0.3},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1199.99,
    )

    for covered in (in_range, at_bound, at_lower_bound):
        liquidus = next(p for p in covered.perturbations if p.property == "liquidus")
        assert liquidus.grounded is True
        assert liquidus.correctable is True
        assert "source_domain" not in liquidus.metadata

    assert in_range.adjusted_liquidus_C == pytest.approx(1370.0)
    assert at_bound.adjusted_liquidus_C == pytest.approx(1330.0)

    for outside in (outside_temperature, below_temperature):
        liquidus = next(p for p in outside.perturbations if p.property == "liquidus")
        assert liquidus.grounded is False
        assert liquidus.correctable is False
        assert liquidus.metadata["source_domain"]["status"] == "out_of_domain"
        assert outside.adjusted_liquidus_C == pytest.approx(1400.0)
        assert any("out_of_domain_source_coverage" in w for w in outside.warnings)


def test_cl_liquidus_composition_gate_converts_carrier_to_cl_wt_pct() -> None:
    nacl_cl_mass_fraction = 35.45 / (22.98976928 + 35.45)
    in_domain_nacl_wt_pct = 0.6 / nacl_cl_mass_fraction
    out_of_domain_nacl_wt_pct = 0.7001 / nacl_cl_mass_fraction

    in_domain = melt_effect_adjustment(
        {"NaCl": in_domain_nacl_wt_pct},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1300.0,
    )
    out_of_domain = melt_effect_adjustment(
        {"NaCl": out_of_domain_nacl_wt_pct},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1300.0,
    )

    in_liquidus = next(p for p in in_domain.perturbations if p.property == "liquidus")
    out_liquidus = next(
        p for p in out_of_domain.perturbations if p.property == "liquidus"
    )
    assert in_liquidus.grounded is True
    assert in_domain.adjusted_liquidus_C == pytest.approx(1340.0)
    assert out_liquidus.grounded is False
    assert out_liquidus.metadata["source_domain"]["composition_wt_pct"] == pytest.approx(
        0.7001
    )
    assert out_liquidus.metadata["source_domain"]["composition_basis"] == "Cl"
    assert out_of_domain.adjusted_liquidus_C == pytest.approx(1400.0)


def test_cl_liquidus_delta_t_uses_cl_basis_for_salt_carrier() -> None:
    nacl = melt_effect_adjustment(
        {"NaCl": 1.0},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1300.0,
    )
    elemental_cl = melt_effect_adjustment(
        {"Cl": 0.3},
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1300.0,
    )

    nacl_liquidus = next(p for p in nacl.perturbations if p.property == "liquidus")
    cl_liquidus = next(
        p for p in elemental_cl.perturbations if p.property == "liquidus"
    )
    assert nacl_liquidus.raw_value == pytest.approx(
        -100.0 * _CL_MASS_FRACTION["NaCl"]
    )
    assert nacl.adjusted_liquidus_C == pytest.approx(
        1400.0 - 100.0 * _CL_MASS_FRACTION["NaCl"]
    )
    assert cl_liquidus.raw_value == pytest.approx(-30.0)
    assert elemental_cl.adjusted_liquidus_C == pytest.approx(1370.0)
    assert request_certified_point(
        "cl_halide",
        "liquidus",
        species="NaCl",
        wt_pct=1.0,
        T_in_C=1300.0,
    ) == pytest.approx(-100.0 * _CL_MASS_FRACTION["NaCl"])
    assert request_certified_point(
        "cl_halide",
        "liquidus",
        species="Cl",
        wt_pct=0.3,
        T_in_C=1300.0,
    ) == pytest.approx(-30.0)


def test_cl_certified_point_applies_source_domain_gate() -> None:
    in_domain_nacl_wt_pct = 0.6 / (35.45 / (22.98976928 + 35.45))
    assert request_certified_point(
        "cl_halide",
        "liquidus",
        species="NaCl",
        wt_pct=in_domain_nacl_wt_pct,
        T_in_C=1300.0,
    ) == pytest.approx(-60.0)

    with pytest.raises(CertifiedPointRefusedError, match="source coverage"):
        request_certified_point(
            "cl_halide",
            "liquidus",
            species="NaCl",
            wt_pct=in_domain_nacl_wt_pct,
            T_in_C=1340.01,
        )
    with pytest.raises(CertifiedPointRefusedError, match="source coverage"):
        request_certified_point(
            "cl_halide",
            "liquidus",
            species="NaCl",
            wt_pct=0.7001 / (35.45 / (22.98976928 + 35.45)),
            T_in_C=1300.0,
        )
