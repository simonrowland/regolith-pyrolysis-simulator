"""b-214: continuum-transport guard applicability (Stage 0 vs pyrolysis).

Production path: ``_evaporation_flux_control_inputs`` threads campaign_name
into the live EVAPORATION_FLUX provider, which consults
``assess_continuum_formula_validity``. Direct provider tests without a
campaign still refuse (missing stage is category 1).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.evaporation import (
    EvaporationFluxRefusal,
    refuse_viscous_p_bulk_out_of_domain,
    viscous_p_bulk_out_of_domain_diagnostic,
)
from simulator.state import CampaignPhase
from simulator.transport_regime import (
    ProcessRegime,
    continuum_validity_refuses,
)
from tests.chemistry.conftest import (
    _build_sim,
    _load_yaml,
)
from tests.chemistry.test_builtin_evaporation_flux_provider import (
    _w3_result_with_controls,
)


_TRANSITIONAL_PA = 3.632
_TRANSITIONAL_T_K = 2023.15
_PIPE_M = 0.12


def _c0_sim():
    return _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )


def test_c0_sim_threads_campaign_name_into_evaporation_flux_controls():
    """Production control builder, not a reconstructed dict.

    Pre-fix code has no campaign_name key on this map; this assertion is
    red-by-revert on the wiring alone.
    """

    sim = _c0_sim()
    sim.start_campaign(CampaignPhase.C0)
    equilibrium = SimpleNamespace(
        vapor_pressures_Pa={},
        vapor_pressures_source={},
        activity_coefficients={},
    )
    controls, _ = sim._evaporation_flux_control_inputs(
        equilibrium,
        overhead_partials_Pa={"Na": 0.0},
        overhead_pressure_pa=_TRANSITIONAL_PA,
        vapour_batch_flux_pressures_Pa={},
    )
    assert controls["campaign_name"] == "C0"
    assert sim.melt.campaign is CampaignPhase.C0


@pytest.mark.parametrize("campaign_name", ["C0", "C0B", "C0b_p_cleanup"])
def test_stage0_provider_computes_and_marks_transitional_kn(campaign_name):
    """Live BuiltinEvaporationFluxProvider.dispatch — same class core uses.

    Pre-fix ignores campaign_name and returns status=refused with
    evaporation_flux_kg_hr is None. This test cannot pass on that code.
    """

    result = _w3_result_with_controls(
        1.0,
        overhead_pressure_pa=_TRANSITIONAL_PA,
        pipe_diameter_m=_PIPE_M,
        gas_temperature_K=_TRANSITIONAL_T_K,
        campaign_name=campaign_name,
    )
    assert result.status == "ok"
    flux = result.diagnostic["evaporation_flux_kg_hr"]
    assert flux["Na"] > 0.0
    notes = result.diagnostic["silent_zero_notes"]
    assert any(
        note["zero_because"] == "out_of_domain_marked"
        and note["doctrine_category"] == 2
        for note in notes
    )
    assert result.diagnostic["continuum_formula_status"] == "out_of_domain"
    assert result.diagnostic["process_regime"] == (
        ProcessRegime.STAGE0_BAKEOUT.value
    )
    assert result.diagnostic["campaign_name"] == campaign_name
    assert result.diagnostic["stage"] == campaign_name
    assert result.diagnostic["asking_site"] == (
        "engines.builtin.evaporation_flux"
    )
    assert not continuum_validity_refuses(result.diagnostic)


def test_pyrolysis_provider_still_refuses_transitional_kn():
    """Same production provider; C4 must still fail-close.

    Pre-fix refuses but does not name campaign/process_regime/asking_site.
    Those fields make this red-by-revert without weakening the guard.
    """

    result = _w3_result_with_controls(
        1.0,
        overhead_pressure_pa=_TRANSITIONAL_PA,
        pipe_diameter_m=_PIPE_M,
        gas_temperature_K=_TRANSITIONAL_T_K,
        campaign_name="C4",
    )
    assert result.status == "refused"
    assert result.diagnostic["reason"] == (
        "viscous_p_bulk_transport_out_of_domain"
    )
    assert result.diagnostic["evaporation_flux_status"] == "not_evaluated"
    assert result.diagnostic["evaporation_flux_kg_hr"] is None
    assert result.diagnostic["campaign_name"] == "C4"
    assert result.diagnostic["stage"] == "C4"
    assert result.diagnostic["process_regime"] == (
        ProcessRegime.PYROLYSIS_EXTRACTION.value
    )
    assert result.diagnostic["asking_site"] == (
        "engines.builtin.evaporation_flux"
    )
    assert result.diagnostic["doctrine_category"] == 1
    assert continuum_validity_refuses(result.diagnostic)


def test_missing_campaign_still_refuses_transitional_kn():
    """Omitting stage is category 1, not a silent bakeout carve-out."""

    result = _w3_result_with_controls(
        1.0,
        overhead_pressure_pa=_TRANSITIONAL_PA,
        pipe_diameter_m=_PIPE_M,
        gas_temperature_K=_TRANSITIONAL_T_K,
    )
    assert result.status == "refused"
    assert result.diagnostic["process_regime"] == ProcessRegime.UNKNOWN.value
    assert continuum_validity_refuses(result.diagnostic)


def test_c0_production_controls_drive_provider_mark_not_refusal():
    """End-to-end: production control map + production provider.

    This is the path ``_calculate_evaporation`` actually takes. Pre-fix
    either lacks campaign_name or refuses after seeing C0.
    """

    sim = _c0_sim()
    sim.start_campaign(CampaignPhase.C0)
    equilibrium = SimpleNamespace(
        vapor_pressures_Pa={},
        vapor_pressures_source={},
        activity_coefficients={},
    )
    controls, _ = sim._evaporation_flux_control_inputs(
        equilibrium,
        overhead_partials_Pa={"Na": 0.0},
        overhead_pressure_pa=_TRANSITIONAL_PA,
        vapour_batch_flux_pressures_Pa={"Na": 100.0},
    )
    assert controls["campaign_name"] == "C0"
    controls["pipe_diameter_m"] = _PIPE_M
    controls["gas_temperature_K"] = _TRANSITIONAL_T_K
    controls["alpha"] = 0.5
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"SiO2": 10.0, "Na2O": 1.0}},
        species_formula_registry={},
    )
    result = BuiltinEvaporationFluxProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            account_view=view,
            temperature_C=1750.0,
            pressure_bar=1e-6,
            fO2_log=None,
            control_inputs=controls,
        )
    )
    assert result.status == "ok"
    assert result.diagnostic["evaporation_flux_kg_hr"]["Na"] > 0.0
    assert any(
        note["zero_because"] == "out_of_domain_marked"
        for note in result.diagnostic["silent_zero_notes"]
    )


def test_viscous_p_bulk_helper_marks_bakeout_and_refuses_pyrolysis():
    bakeout = viscous_p_bulk_out_of_domain_diagnostic(
        knudsen_number=0.1,
        overhead_pressure_pa=_TRANSITIONAL_PA,
        pipe_diameter_m=_PIPE_M,
        gas_temperature_K=_TRANSITIONAL_T_K,
        campaign_name="C0",
        asking_site="tests.viscous_p_bulk",
    )
    assert bakeout is not None
    assert bakeout["status"] == "out_of_domain"
    assert bakeout["ledger_yields_authorized"] is True
    assert bakeout["silent_zero_notes"][0]["doctrine_category"] == 2
    assert not continuum_validity_refuses(bakeout)
    refuse_viscous_p_bulk_out_of_domain(
        knudsen_number=0.1,
        overhead_pressure_pa=_TRANSITIONAL_PA,
        pipe_diameter_m=_PIPE_M,
        gas_temperature_K=_TRANSITIONAL_T_K,
        campaign_name="C0",
    )

    with pytest.raises(EvaporationFluxRefusal) as exc_info:
        refuse_viscous_p_bulk_out_of_domain(
            knudsen_number=0.1,
            overhead_pressure_pa=_TRANSITIONAL_PA,
            pipe_diameter_m=_PIPE_M,
            gas_temperature_K=_TRANSITIONAL_T_K,
            campaign_name="C4",
        )
    assert exc_info.value.reason == "viscous_p_bulk_transport_out_of_domain"
    assert exc_info.value.diagnostic["campaign_name"] == "C4"
    assert exc_info.value.diagnostic["process_regime"] == (
        ProcessRegime.PYROLYSIS_EXTRACTION.value
    )
