"""b-491: pO2 floor inversion is a typed notice, not a silent metal pressure."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

import engines.builtin.vapor_pressure as vapor_pressure_module
from engines.builtin.vapor_pressure import (
    BuiltinVaporPressureProvider,
    MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON,
    MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
    melt_dissociation_pO2_floor_inversion_notice,
    physical_melt_dissociation_pO2_bar,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.environment import (
    ASTEROID_VACUUM_FLOOR_BAR,
    DEFAULT_VACUUM_FLOOR_BAR,
)
from simulator.grind_preflight import _is_noncertifying_vapor_extrapolation
from simulator.physical_constants import (
    CATALOG_PHYSICAL_PRESSURE_CEILING_PA,
    MELT_DISSOCIATION_PO2_MAX_BAR,
    MELT_DISSOCIATION_PO2_MIN_BAR,
)
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_ACCOUNT = {"FeO": 0.30, "MgO": 0.20, "SiO2": 0.50, "Na2O": 0.02}
_T_K = 1700.0


def _yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


def _request(*, pO2_bar: float, intrinsic_fO2_log: float) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(_ACCOUNT)},
            species_formula_registry={},
        ),
        temperature_C=_T_K - 273.15,
        pressure_bar=1e-6,
        control_inputs={
            "pO2_bar": max(pO2_bar, DEFAULT_VACUUM_FLOOR_BAR),
            # Direct provider tests own both redox channels explicitly.  The
            # transport rail remains floor-clamped for the floor-inversion
            # exercise; surface release uses the physical melt/interface rail.
            "interface_pO2_bar": physical_melt_dissociation_pO2_bar(
                intrinsic_fO2_log
            )[0],
            "intrinsic_fO2_log": intrinsic_fO2_log,
            "vacuum_floor_bar": DEFAULT_VACUUM_FLOOR_BAR,
        },
    )


def _allow_floor_diagnostic_without_catalog_refusal(monkeypatch) -> None:
    # These b-491 tests inspect the floor-inversion diagnostic/provenance.  The
    # b-162 contract is tested separately below; widening only this provider
    # module's rail keeps the two assertion sites independent.
    monkeypatch.setattr(
        vapor_pressure_module,
        "CATALOG_PHYSICAL_PRESSURE_CEILING_PA",
        1.0e99,
    )


def test_helper_flags_floor_and_skips_positive_exponent() -> None:
    notice = melt_dissociation_pO2_floor_inversion_notice(
        species="Si",
        pressure_Pa=7.13e9,
        pO2_bar_used=MELT_DISSOCIATION_PO2_MIN_BAR,
        pO2_exponent=-1.0,
        pressure_rail="liquid_oxide_standard_reaction",
        fO2_log=-400.0,
        was_clamped=True,
    )
    assert notice is not None
    assert notice["reason"] == MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
    assert notice["authority_level"] == "extrapolated"
    assert notice["certified_band"]["pO2_bar"] == (
        MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
        MELT_DISSOCIATION_PO2_MAX_BAR,
    )
    assert notice["was_clamped"] is True

    suppressed = melt_dissociation_pO2_floor_inversion_notice(
        species="AlO2",
        pressure_Pa=1e-20,
        pO2_bar_used=MELT_DISSOCIATION_PO2_MIN_BAR,
        pO2_exponent=0.25,
        pressure_rail="standard_reaction_term",
        fO2_log=-400.0,
        was_clamped=True,
    )
    assert suppressed is None

    in_band = melt_dissociation_pO2_floor_inversion_notice(
        species="Si",
        pressure_Pa=1e-12,
        pO2_bar_used=ASTEROID_VACUUM_FLOOR_BAR,
        pO2_exponent=-1.0,
        pressure_rail="liquid_oxide_standard_reaction",
        fO2_log=math.log10(ASTEROID_VACUUM_FLOOR_BAR),
        was_clamped=False,
    )
    assert in_band is None


def test_exact_log10_min_is_a_floor_even_when_not_clamped() -> None:
    pO2, clamped = physical_melt_dissociation_pO2_bar(-30.0)
    assert clamped is False
    assert pO2 <= MELT_DISSOCIATION_PO2_MIN_BAR
    notice = melt_dissociation_pO2_floor_inversion_notice(
        species="Si",
        pressure_Pa=7.13e9,
        pO2_bar_used=pO2,
        pO2_exponent=-1.0,
        pressure_rail="liquid_oxide_standard_reaction",
        fO2_log=-30.0,
        was_clamped=clamped,
    )
    assert notice is not None
    assert notice["was_clamped"] is False


def test_provider_flags_floor_inversion_and_completes(monkeypatch) -> None:
    _allow_floor_diagnostic_without_catalog_refusal(monkeypatch)
    provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
    result = provider.dispatch(_request(pO2_bar=1e-30, intrinsic_fO2_log=-400.0))
    diagnostic = result.diagnostic

    assert result.status == "ok"
    assert diagnostic["melt_dissociation_pO2_clamped_to_physical_envelope"] is True
    notices = diagnostic["pO2_floor_inversion_notices_by_species"]
    pressures = diagnostic["vapor_pressures_Pa"]
    sources = diagnostic["vapor_pressures_source"]
    provenance = diagnostic["vapor_pressure_numerator_provenance"]

    assert "Si" in notices
    assert "Mg" in notices
    assert "Na" in notices
    assert "Fe" not in notices
    assert "SiO" not in notices

    for species in ("Si", "Mg", "Na"):
        notice = notices[species]
        assert notice["reason"] == MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
        assert notice["authority_level"] == "extrapolated"
        assert notice["certified_band"]["pO2_bar"] == (
            MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
            MELT_DISSOCIATION_PO2_MAX_BAR,
        )
        assert notice["was_clamped"] is True
        assert pressures[species] == pytest.approx(notice["pressure_Pa"])
        assert pressures[species] > 0.0
        assert provenance[species]["floor_inversion_notice"] == notice
        assert MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON in sources[species]
        assert _is_noncertifying_vapor_extrapolation(species, sources[species])

    assert pressures["Si"] >= CATALOG_PHYSICAL_PRESSURE_CEILING_PA
    assert any(
        MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON in str(item)
        for item in result.warnings
    )


def test_provider_predicts_and_flags_species_above_catalog_ceiling() -> None:
    provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))

    result = provider.dispatch(_request(pO2_bar=1e-30, intrinsic_fO2_log=-400.0))

    assert result.status == "ok"
    pressures = result.diagnostic["vapor_pressures_Pa"]
    notices = result.diagnostic["vapor_pressure_out_of_domain_notices"]
    assert notices
    assert result.diagnostic["vapor_pressure_species_refusals"] == {}
    assert pressures
    assert set(notices).issubset(pressures)
    for species, notice in notices.items():
        assert notice["status"] == "out_of_domain"
        assert notice["output_status"] == "status_bearing"
        assert notice["flux_status"] == "predicted"
        assert notice["availability"] == "available"
        assert notice["reason"] == "vapor_pressure_physical_pressure_ceiling"
        assert notice["pressure_Pa"] == pressures[species]
        assert notice["pressure_Pa"] > CATALOG_PHYSICAL_PRESSURE_CEILING_PA
        assert notice["ceiling_Pa"] == CATALOG_PHYSICAL_PRESSURE_CEILING_PA
        assert notice["authority_level"] == "extrapolated"
        assert notice["certified_band"]["pressure_Pa"] == (
            0.0,
            CATALOG_PHYSICAL_PRESSURE_CEILING_PA,
        )
        assert notice["certified_band"]["pO2_bar"] == (
            MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
            MELT_DISSOCIATION_PO2_MAX_BAR,
        )
        assert (
            result.diagnostic["vapor_pressure_numerator_provenance"][species][
                "physical_pressure_ceiling_notice"
            ]
            == notice
        )
        assert _is_noncertifying_vapor_extrapolation(
            species, result.diagnostic["vapor_pressures_source"][species]
        )
        assert notice["measured_zero"] is False
    assert any(
        float(pressure) > CATALOG_PHYSICAL_PRESSURE_CEILING_PA
        for pressure in pressures.values()
    )


def test_one_species_genuine_refusal_completes_runner_and_is_unavailable(monkeypatch) -> None:
    """A forced genuine refusal stays unavailable while peer fluxes live."""

    original_dispatch = BuiltinVaporPressureProvider.dispatch
    forced_species: list[str] = []

    def force_one_species_refusal(self, request):
        result = original_dispatch(self, request)
        diagnostic = dict(result.diagnostic or {})
        pressures = dict(diagnostic.get("vapor_pressures_Pa") or {})
        if not pressures:
            return result
        species = "Na" if "Na" in pressures else sorted(pressures)[0]
        pressure = float(pressures.pop(species))
        forced_species.append(species)
        refusals = dict(diagnostic.get("vapor_pressure_species_refusals") or {})
        refusals[species] = {
            "status": "refused",
            "flux_status": "refused",
            "reason": "vapor_pressure_missing_input",
            "species": species,
            "pressure_Pa": max(
                pressure,
                float(CATALOG_PHYSICAL_PRESSURE_CEILING_PA) * 2.0,
            ),
            "ceiling_Pa": float(CATALOG_PHYSICAL_PRESSURE_CEILING_PA),
            "flagged": True,
            "backlog": True,
            "availability": "unavailable",
            "measured_zero": False,
            "ledger_moved_mol": 0.0,
            "mass_moved_mol": 0.0,
        }
        diagnostic["vapor_pressures_Pa"] = pressures
        diagnostic["vapor_pressure_species_refusals"] = refusals
        return replace(result, diagnostic=diagnostic)

    monkeypatch.setattr(
        BuiltinVaporPressureProvider,
        "dispatch",
        force_one_species_refusal,
    )
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        mass_kg=1.0,
        backend_name="internal-analytical",
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        force_builtin_vapor_pressure=True,
    )
    session = run._start_session()
    session.simulator.melt.temperature_C = 1600.0
    session.simulator.melt.target_temperature_C = 1600.0
    execution = RunExecutor().execute_session(session, hours=1)
    payload = run._build_output(execution)

    assert execution.status == "ok"
    assert payload["status"] == "ok"
    assert forced_species
    species = forced_species[-1]
    row = payload["per_hour_summary"][-1]
    refusal = row["vapor_pressure_refusals"][species]
    assert refusal["status"] == "refused"
    assert refusal["measured_zero"] is False
    assert refusal["ledger_moved_mol"] == 0.0
    assert any(
        species_name != species and float(rate) > 0.0
        for species_name, rate in row["vapor_species_kg_hr"].items()
    )
    backlog = payload["run_metadata"]["flag_backlog"]
    assert backlog["count"] >= 1
    assert backlog["by_species"][species] >= 1
    ideal_train = payload["yield_disposition"]["ideal_train_melt_boundary"]
    unavailable_rows = [
        row
        for row in ideal_train["rows"]
        if any(
            refusal.get("species") == species
            and refusal.get("code") == "vapor_pressure_species_unavailable"
            for refusal in row.get("refusals", ())
        )
    ]
    assert unavailable_rows
    for row in unavailable_rows:
        assert row["status"] == "refused"
        assert row["surface_crossed_mol_atoms"] is None
        assert row["ideal_train_fraction"] is None
        assert any(
            refusal.get("species") == species
            and refusal.get("reason") == "vapor_pressure_missing_input"
            for refusal in row["refusals"]
        )
        assert row["term_provenance"]["ideal_train_fraction"]["status"] == (
            "refused"
        )


def test_provider_flags_exact_minus_30_without_clamp_warning(monkeypatch) -> None:
    _allow_floor_diagnostic_without_catalog_refusal(monkeypatch)
    provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
    result = provider.dispatch(_request(pO2_bar=1e-30, intrinsic_fO2_log=-30.0))
    diagnostic = result.diagnostic

    assert result.status == "ok"
    assert diagnostic["melt_dissociation_pO2_clamped_to_physical_envelope"] is False
    assert "Si" in diagnostic["pO2_floor_inversion_notices_by_species"]
    assert diagnostic["vapor_pressures_Pa"]["Si"] > 0.0
    clamp_warnings = [
        item
        for item in result.warnings
        if "melt_dissociation_pO2_clamped_to_physical_envelope" in str(item)
    ]
    assert clamp_warnings == []


def test_si_mg_na_mass_action_ratio_at_floor_vs_1e9(monkeypatch) -> None:
    """Hand mass-action: n_Si=-1 → 21 dex; n_Mg_eff=-0.5 → 10.5 dex; n_Na=-0.25 → 5.25 dex."""

    _allow_floor_diagnostic_without_catalog_refusal(monkeypatch)
    provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
    at_ref = provider.dispatch(
        _request(pO2_bar=1e-9, intrinsic_fO2_log=-9.0)
    ).diagnostic["vapor_pressures_Pa"]
    at_floor = provider.dispatch(
        _request(pO2_bar=1e-30, intrinsic_fO2_log=-400.0)
    ).diagnostic["vapor_pressures_Pa"]

    # Premise: p ∝ pO2^n. Algebra: P_floor/P_ref = (1e-30/1e-9)^n.
    # Unit: bar/bar. Sanity: n=-1 → 1e21; n=-0.5 → 1e10.5; n=-0.25 → 1e5.25.
    assert at_floor["Si"] / at_ref["Si"] == pytest.approx(1e21, rel=1e-9)
    assert at_floor["Mg"] / at_ref["Mg"] == pytest.approx(10 ** 10.5, rel=1e-9)
    assert at_floor["Na"] / at_ref["Na"] == pytest.approx(10 ** 5.25, rel=1e-6)


def _si_floor_notice_from_provider(monkeypatch):
    _allow_floor_diagnostic_without_catalog_refusal(monkeypatch)
    provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
    result = provider.dispatch(_request(pO2_bar=1e-30, intrinsic_fO2_log=-30.0))
    diagnostic = result.diagnostic
    notice = diagnostic["pO2_floor_inversion_notices_by_species"]["Si"]
    return provider, result, diagnostic, notice


def test_floor_notice_reaches_carrier_extra_and_pareto(monkeypatch) -> None:
    """M04: provider floor reason/band must ride extra.extrapolation_notice."""

    from simulator.diagnostics import _attach_pareto_source_notices
    from simulator.vapour_rail.batch import (
        FLUX_ACTIVATION_EPOCH_PRE_RG,
        FluxActivationContext,
        PressureValue,
    )
    from simulator.vapour_rail.request import VapourResolveState

    provider, result, diagnostic, notice = _si_floor_notice_from_provider(monkeypatch)
    provenance = diagnostic["vapor_pressure_numerator_provenance"]["Si"]
    assert result.status == "ok"
    assert diagnostic["vapor_pressures_Pa"]["Si"] > 0.0
    assert provenance["extrapolation_notice"] == notice
    assert notice["reason"] == MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
    assert notice["authority_level"] == "extrapolated"
    assert notice["certified_band"]["pO2_bar"] == (
        MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
        MELT_DISSOCIATION_PO2_MAX_BAR,
    )

    # Exact request.py copy: provenance extrapolation_notice (floor dual-write)
    # lands on the public carrier extra that Pareto reads.
    source_notice = provenance.get("extrapolation_notice")
    evaluation_extra = {}
    if source_notice is not None:
        evaluation_extra["extrapolation_notice"] = dict(source_notice)
    assert evaluation_extra["extrapolation_notice"]["reason"] == (
        MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
    )
    assert evaluation_extra["extrapolation_notice"]["certified_band"]["pO2_bar"] == (
        MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
        MELT_DISSOCIATION_PO2_MAX_BAR,
    )

    catalog = provider._vapour_rail_catalog
    state = VapourResolveState(
        temperature_K=_T_K,
        process_phase="pyrolysis",
        total_pressure_Pa=0.1,
        fO2_bar=diagnostic["pO2_bar"],
        source_reaction_fO2_bar=diagnostic["source_reaction_fO2_bar"],
        source_reaction_activities=diagnostic["activities"],
        source_reaction_activity_provider=diagnostic["activities_provider"],
        source_reaction_activity_evidence_refs={"Si": "b491-floor-replay"},
        source_reaction_activity_standard_states={
            "Si": catalog.species["Si"].source_reaction_activity.standard_state,
        },
        source_reaction_activity_provenance=diagnostic[
            "vapor_pressure_numerator_provenance"
        ],
    )
    answer = catalog.resolve_batch(
        {"process.cleaned_melt": dict(_ACCOUNT)},
        state,
        flux_activation_context=FluxActivationContext(
            epoch=FLUX_ACTIVATION_EPOCH_PRE_RG
        ),
    ).channel("Si")
    assert isinstance(answer.pressure, PressureValue)
    assert answer.pressure.pa > 0.0
    assert answer.is_flux_active
    carrier_notice = answer.extra["extrapolation_notice"]
    assert carrier_notice["reason"] == MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
    assert carrier_notice["authority_level"] == "extrapolated"
    assert carrier_notice["certified_band"]["pO2_bar"] == (
        MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
        MELT_DISSOCIATION_PO2_MAX_BAR,
    )

    by_species: dict[str, dict] = {"Si": {"status": "ok"}}
    _attach_pareto_source_notices(
        by_species,
        {
            "vapour_carrier_authority_by_species": {
                "Si": {"extra": {"extrapolation_notice": carrier_notice}},
            }
        },
    )
    pareto = by_species["Si"]["vapour_pressure_extrapolation_notice"]
    assert pareto["reason"] == MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
    assert tuple(pareto["certified_band"]["pO2_bar"]) == (
        MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
        MELT_DISSOCIATION_PO2_MAX_BAR,
    )
    assert by_species["Si"]["authority_level"] == "extrapolated"


def test_clean_in_band_timestep_publishes_without_floor_notice() -> None:
    """M04 successful-value control: log fO2=-9 stays a finite live carrier."""

    from simulator.vapour_rail.batch import (
        FLUX_ACTIVATION_EPOCH_PRE_RG,
        FluxActivationContext,
        PressureValue,
    )
    from simulator.vapour_rail.request import VapourResolveState

    provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
    result = provider.dispatch(_request(pO2_bar=1e-9, intrinsic_fO2_log=-9.0))
    diagnostic = result.diagnostic
    assert result.status == "ok"
    assert diagnostic["pO2_floor_inversion_notices_by_species"] == {}
    assert diagnostic["vapor_pressures_Pa"]["Si"] > 0.0
    si_prov = diagnostic["vapor_pressure_numerator_provenance"]["Si"]
    assert "extrapolation_notice" not in si_prov
    assert "floor_inversion_notice" not in si_prov

    catalog = provider._vapour_rail_catalog
    answer = catalog.resolve_batch(
        {"process.cleaned_melt": dict(_ACCOUNT)},
        VapourResolveState(
            temperature_K=_T_K,
            process_phase="pyrolysis",
            total_pressure_Pa=0.1,
            fO2_bar=diagnostic["pO2_bar"],
            source_reaction_fO2_bar=diagnostic["source_reaction_fO2_bar"],
            source_reaction_activities=diagnostic["activities"],
            source_reaction_activity_provider=diagnostic["activities_provider"],
            source_reaction_activity_evidence_refs={"Si": "b491-clean-replay"},
            source_reaction_activity_standard_states={
                "Si": catalog.species["Si"].source_reaction_activity.standard_state,
            },
            source_reaction_activity_provenance=diagnostic[
                "vapor_pressure_numerator_provenance"
            ],
        ),
        flux_activation_context=FluxActivationContext(
            epoch=FLUX_ACTIVATION_EPOCH_PRE_RG
        ),
    ).channel("Si")
    assert isinstance(answer.pressure, PressureValue)
    assert answer.pressure.pa > 0.0
    assert answer.is_flux_active
    assert not (answer.extra or {}).get("extrapolation_notice")


def test_early_floor_notice_survives_clean_timestep_wall_merge(monkeypatch) -> None:
    """M04: worst-severity wall merge keeps the floor reason after a clean hour."""

    from types import SimpleNamespace

    from simulator.coating_lifespan import (
        FoulingTerminalSnapshot,
        merge_run_snapshot,
    )
    from simulator.diagnostics import _attach_pareto_source_notices

    _, _, _, notice = _si_floor_notice_from_provider(monkeypatch)

    def _carrier(extra=None, *, authoritative: bool):
        record = {
            "species_id": "Si",
            "pressure": {"kind": "value", "pa": 1.0},
            "flux": {"kind": "eligible"},
            "verdict_status": (
                "authoritative"
                if authoritative
                else "status_bearing_non_authoritative"
            ),
            "certification_ceiling": (
                "validated_point" if authoritative else "never"
            ),
            "validation_status": "validated" if authoritative else "modeled-PENDING",
            "is_union_flux_eligible": True,
            "is_flux_active": True,
        }
        if extra is not None:
            record["extra"] = extra
        return {
            "vapour_carrier_authority_by_species": {"Si": record},
            "alpha_s_provenance_by_species": {
                "Si": {
                    "hot_wall": {
                        "segment": "hot_wall",
                        "species": "Si",
                        "alpha_s": 0.02,
                        "citation_status": "CITED",
                        "status": "sourced",
                        "output_status": "sourced_with_surface_proxy",
                    }
                }
            },
        }

    floor_snap = FoulingTerminalSnapshot.from_trace(
        SimpleNamespace(
            wall_deposit_by_segment_species_kg={("hot_wall", "Si"): 1e-6},
            wall_deposit_sticking_authority=_carrier(
                {"extrapolation_notice": notice},
                authoritative=False,
            ),
        )
    )
    clean_snap = FoulingTerminalSnapshot.from_trace(
        SimpleNamespace(
            wall_deposit_by_segment_species_kg={("hot_wall", "Si"): 1e-6},
            wall_deposit_sticking_authority=_carrier(authoritative=True),
        )
    )
    merged, _ = merge_run_snapshot(floor_snap, clean_snap)
    extra = merged.wall_deposit_sticking_authority[
        "vapour_carrier_authority_by_species"
    ]["Si"]["extra"]
    assert extra["extrapolation_notice"]["reason"] == (
        MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
    )
    assert extra["extrapolation_notice"]["certified_band"]["pO2_bar"] == (
        MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
        MELT_DISSOCIATION_PO2_MAX_BAR,
    )
    by_species: dict[str, dict] = {}
    _attach_pareto_source_notices(
        by_species,
        merged.wall_deposit_sticking_authority,
    )
    assert by_species["Si"]["vapour_pressure_extrapolation_notice"][
        "reason"
    ] == MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON


def test_lowest_catalog_body_floor_does_not_invert() -> None:
    provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
    result = provider.dispatch(
        _request(
            pO2_bar=ASTEROID_VACUUM_FLOOR_BAR,
            intrinsic_fO2_log=math.log10(ASTEROID_VACUUM_FLOOR_BAR),
        )
    )
    diagnostic = result.diagnostic
    assert result.status == "ok"
    assert diagnostic["pO2_floor_inversion_notices_by_species"] == {}
    assert diagnostic["vapor_pressures_Pa"]["Si"] < CATALOG_PHYSICAL_PRESSURE_CEILING_PA
    assert diagnostic["vapor_pressures_Pa"]["Mg"] < CATALOG_PHYSICAL_PRESSURE_CEILING_PA
    assert diagnostic["vapor_pressures_Pa"]["Na"] < CATALOG_PHYSICAL_PRESSURE_CEILING_PA


def test_catalog_and_channel_floor_clamps_carry_floor_notice() -> None:
    """M06: catalog evaluate and O2 clamp publish the existing floor notice."""

    from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
    from simulator.vapour_rail.channels import (
        REACTION_PLANE_MELT_INTERFACE,
        clamp_physical_pO2_bar,
        o2_potential_from_pO2_bar,
    )

    catalog = compile_vapour_rail_catalog(_yaml("vapor_pressures.yaml"))
    evaluator = catalog.evaluator_for("Si")
    assert evaluator.pO2_exponent < 0.0
    below = evaluator.evaluate(1700.0, source_activity=1e-30, pO2_bar=1e-40)
    at_floor = evaluator.evaluate(1700.0, source_activity=1e-30, pO2_bar=1e-30)
    control = evaluator.evaluate(1700.0, source_activity=1e-30, pO2_bar=1e-9)

    assert below.pressure_pa == pytest.approx(at_floor.pressure_pa)
    assert below.pressure_pa > 0.0
    assert below.pressure_pa < CATALOG_PHYSICAL_PRESSURE_CEILING_PA
    assert below.status is None
    assert below.extrapolation_notice is not None
    assert below.extrapolation_notice["reason"] == (
        MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
    )
    assert below.extrapolation_notice["authority_level"] == "extrapolated"
    assert below.extrapolation_notice["certified_band"]["pO2_bar"] == (
        MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
        MELT_DISSOCIATION_PO2_MAX_BAR,
    )
    assert at_floor.extrapolation_notice is not None
    assert control.pressure_pa > 0.0
    assert control.pressure_pa / below.pressure_pa == pytest.approx(1e-21, rel=1e-9)
    assert control.extrapolation_notice is None

    assert clamp_physical_pO2_bar(1e-40) == MELT_DISSOCIATION_PO2_MIN_BAR
    potential = o2_potential_from_pO2_bar(
        pO2_bar=1e-40,
        temperature_K=1700.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    assert potential.legacy_pO2_bar == MELT_DISSOCIATION_PO2_MIN_BAR
    assert potential.verdict.value == "Point"
    receipt = dict(potential.observation_or_setpoint_receipt)
    assert receipt["pO2_bar_input"] == pytest.approx(1e-40)
    assert receipt["pO2_bar_clamped"] == MELT_DISSOCIATION_PO2_MIN_BAR
    assert receipt["extrapolation_notice"]["reason"] == (
        MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
    )
    in_band = o2_potential_from_pO2_bar(
        pO2_bar=1e-9,
        temperature_K=1700.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    assert "extrapolation_notice" not in dict(
        in_band.observation_or_setpoint_receipt
    )


def test_positive_exponent_catalog_path_does_not_flag_floor_inversion() -> None:
    """M06 control: positive-n carriers shrink at the floor — not inversion."""

    from simulator.vapour_rail.catalog import compile_vapour_rail_catalog

    catalog = compile_vapour_rail_catalog(_yaml("vapor_pressures.yaml"))
    evaluator = catalog.evaluator_for("AlO2")
    assert evaluator.pO2_exponent > 0.0
    floor = evaluator.evaluate(1700.0, source_activity=0.5, pO2_bar=1e-40)
    control = evaluator.evaluate(1700.0, source_activity=0.5, pO2_bar=1e-9)
    assert floor.pressure_pa > 0.0
    assert control.pressure_pa > 0.0
    assert floor.pressure_pa < control.pressure_pa
    assert floor.extrapolation_notice is None
    assert control.extrapolation_notice is None


def test_adapter_antoine_projection_flags_floor_without_refusing() -> None:
    """M06: AlphaMELTS/ThermoEngine inherited projection helpers carry notice."""

    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    backend = AlphaMELTSBackend()
    T_C = 1700.0 - 273.15
    activities = {"Si": 1e-30}
    below = backend._activities_times_antoine(
        T_C, activities, {"SiO2": 100.0}, pO2_bar=1e-40
    )
    floor_notices = dict(backend._antoine_floor_inversion_notices)
    _, floor_sources = backend._finalize_antoine_projection(
        below, base_source="thermoengine"
    )
    at_floor = backend._activities_times_antoine(
        T_C, activities, {"SiO2": 100.0}, pO2_bar=1e-30
    )
    control = backend._activities_times_antoine(
        T_C, activities, {"SiO2": 100.0}, pO2_bar=1e-9
    )
    control_notices = dict(backend._antoine_floor_inversion_notices)
    _, control_sources = backend._finalize_antoine_projection(
        control, base_source="thermoengine"
    )

    assert below["Si"] == pytest.approx(at_floor["Si"])
    assert below["Si"] > 0.0
    assert below["Si"] < CATALOG_PHYSICAL_PRESSURE_CEILING_PA
    assert "Si" in floor_notices
    assert floor_notices["Si"]["reason"] == (
        MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
    )
    assert floor_notices["Si"]["certified_band"]["pO2_bar"] == (
        MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
        MELT_DISSOCIATION_PO2_MAX_BAR,
    )
    assert MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON in floor_sources["Si"]
    assert control["Si"] > 0.0
    assert control["Si"] / below["Si"] == pytest.approx(1e-21, rel=1e-6)
    assert "Si" not in control_notices
    assert MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON not in control_sources["Si"]
