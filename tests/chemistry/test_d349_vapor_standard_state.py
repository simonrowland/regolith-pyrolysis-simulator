"""Pins for the d-349 vapor activity reference-state correction."""

from __future__ import annotations

import math
import copy
from types import SimpleNamespace

import pytest

from engines.builtin import vapor_pressure as vapor_pressure_module
from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.chemistry.melt_activity import (
    MELT_OXIDE_ACTIVITY_REFERENCE_STATE,
    MELT_OXIDE_CRYSTALLINE_ACTIVITY_REFERENCE_STATE,
    _activity_for_crystalline_vapor_k,
)
from simulator.accounting.formulas import resolve_species_formula
from simulator.diagnostic_helpers.alphamelts_volatility import (
    _analytical_vapor_pressures_from_activities,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.vapour_rail.batch import PressureValue
from simulator.vapour_rail.batch import FluxEligible
from simulator.vapour_rail.instrumentation import EffectivePressureSource
from tests.chemistry.conftest import _build_sim

_D349_VAPOR_ROUTES = ("Mg", "Mg2", "MgO_gas", "Ca", "Ca2", "CaO_gas")
_D349_SOURCE_K_STANDARD_IDS = {
    "Mg": ("Mg-009", "Mg-010"),
    "Mg2": ("Mg-009", "Mg-010"),
    "MgO_gas": ("Mg-009", "Mg-010"),
    "Ca": ("Ca-028", "Ca-029"),
    "Ca2": ("Ca-028", "Ca-029"),
    "CaO_gas": ("Ca-028", "Ca-029"),
}
# Same T, pO2, and activity. These measured metal-route ratios preserve the
# legacy provider's rounded simulator.state R against the catalog's CODATA R.
_D349_GATEWAY_TO_PROVIDER_PRESSURE_RATIO = {
    "Mg": 1.00000782778,
    "Mg2": 1.0,
    "MgO_gas": 1.0,
    "Ca": 1.00000951030,
    "Ca2": 1.0,
    "CaO_gas": 1.0,
}


def _lunar_mare_probe(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    *,
    sim=None,
    temperature_C=1500.0,
):
    if sim is None:
        sim = _build_sim(
            "lunar_mare_low_ti",
            vapor_pressure_data,
            feedstocks_data,
            setpoints_data,
        )
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": sim.atom_ledger.mol_by_account(
                    "process.cleaned_melt"
                )
            },
            species_formula_registry=sim.species_formula_registry,
        ),
        temperature_C=temperature_C,
        pressure_bar=1.0,
        fO2_log=0.0,
        control_inputs={"pO2_bar": 1.0, "intrinsic_fO2_log": 0.0},
    )
    return BuiltinVaporPressureProvider(vapor_pressure_data), request


def _builtin_pressure_projection(
    vapor_pressure_data,
    composition_wt_pct,
    *,
    temperature_C,
    pO2_bar,
):
    composition_mol = {
        str(species): float(wt_pct)
        / resolve_species_formula(str(species)).molar_mass_kg_per_mol()
        for species, wt_pct in composition_wt_pct.items()
        if float(wt_pct) > 0.0
    }
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": composition_mol},
            species_formula_registry={},
        ),
        temperature_C=float(temperature_C),
        pressure_bar=1.0,
        fO2_log=math.log10(float(pO2_bar)),
        control_inputs={
            "pO2_bar": float(pO2_bar),
            "intrinsic_fO2_log": math.log10(float(pO2_bar)),
        },
    )
    result = BuiltinVaporPressureProvider(vapor_pressure_data).dispatch(request)
    assert result.status == "ok"
    return dict(result.diagnostic)


def _live_gateway_probe(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    *,
    temperature_C=1500.0,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.p_total_mbar = 1000.0
    provider, request = _lunar_mare_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        sim=sim,
        temperature_C=temperature_C,
    )
    diagnostic = dict(provider.dispatch(request).diagnostic)
    diagnostic["source_reaction_fO2_bar"] = 1.0
    equilibrium = SimpleNamespace(
        temperature_C=temperature_C,
        pO2_bar=1.0,
        fO2_log=0.0,
        diagnostics={},
        activity_coefficients=diagnostic["activities"],
        vapor_pressures_Pa=diagnostic["vapor_pressures_Pa"],
    )
    effective_source = EffectivePressureSource(
        "d349-live-gateway",
        diagnostic["vapor_pressures_Pa"],
    )
    return sim, diagnostic, equilibrium, effective_source


def _diagnostic_with_route_provenance(
    diagnostic,
    species,
    *,
    remove=(),
    **updates,
):
    mutated = dict(diagnostic)
    provenance = {
        route: dict(route_provenance)
        for route, route_provenance in diagnostic[
            "vapor_pressure_numerator_provenance"
        ].items()
    }
    route_provenance = provenance[species]
    for key in remove:
        route_provenance.pop(key, None)
    route_provenance.update(updates)
    mutated["vapor_pressure_numerator_provenance"] = provenance
    return mutated


def _resolve_live_gateway_batch(
    sim,
    diagnostic,
    equilibrium,
    effective_source,
    *,
    temperature_K=1773.15,
):
    sim._last_vapor_pressure_diagnostic = diagnostic
    return sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=temperature_K,
        effective_pressure_source=effective_source,
    )


def test_d349_lunar_mare_vapor_pressures_match_green_q79_probe(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Pin green a6d75d8b4 values from q79's production provider probe."""

    provider, request = _lunar_mare_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    result = provider.dispatch(request)

    # Provenance: worker-report-q79.md records these source pressures from
    # green a6d75d8b4's BuiltinVaporPressureProvider path, using this same
    # production lunar feedstock account at 1773.15 K and pO2=1 bar.
    expected = {
        "Mg": 2.1010437e-7,
        "Ca": 1.0585356e-11,
        "MgO_gas": 9.1669376e-6,
        "CaO_gas": 1.2830165e-9,
    }
    pressures = result.diagnostic["vapor_pressures_Pa"]
    assert result.status == "ok"
    for species, pressure in expected.items():
        assert pressures[species] == pytest.approx(
            pressure,
            rel=1e-6,
            abs=0,
        )

    # All six Mg/Ca parent routes use crystalline K: monatomic metal, dimers,
    # and monatomic oxide gas. The producer activity handshake itself remains
    # liquid-referenced, as declared in the catalog.
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]
    routes = {
        "Mg": ("MgO", "Mg-009", "Mg-010", 0.90173974),
        "Mg2": ("MgO", "Mg-009", "Mg-010", 0.90173974),
        "MgO_gas": ("MgO", "Mg-009", "Mg-010", 0.90173974),
        "Ca": ("CaO", "Ca-028", "Ca-029", 1.03119892),
        "Ca2": ("CaO", "Ca-028", "Ca-029", 1.03119892),
        "CaO_gas": ("CaO", "Ca-028", "Ca-029", 1.03119892),
    }
    for species, (oxide, source_id, target_id, log10_offset) in routes.items():
        notice = provenance[species]["activity_standard_state_conversion"]
        assert notice["oxide"] == oxide
        assert notice["from_standard_state_id"] == source_id
        assert notice["to_standard_state_id"] == target_id
        assert notice["temperature_K"] == pytest.approx(1773.15)
        assert notice["log10_activity_offset"] == pytest.approx(
            log10_offset, abs=1e-7
        )
        assert notice["authority"] == "bridge"
        assert notice["authority_level"] == "predict_and_flag"
        assert notice["reason"] == (
            "JANAF tabulates the liquid phase below its solid-liquid Gibbs "
            "crossing; this is JANAF's metastable (supercooled) liquid branch"
        )
        assert notice["delta_g_fus_kJ_per_mol"] == pytest.approx(
            30.6109325 if oxide == "MgO" else 35.005622,
            abs=1e-7,
        )
        assert notice["certified_band_K"] == pytest.approx(
            [298.15, 4000.0 if oxide == "MgO" else 3600.0]
        )
        assert provenance[species][
            "vapor_activity_for_crystalline_K_standard_state_id"
        ] == notice["to_standard_state_id"]
    assert result.diagnostic["activities_standard_state"]["phase"] == "liquid"


def test_d349_catalog_identity_mismatch_is_visible_on_real_vapor_route(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    _, request = _lunar_mare_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    catalog = copy.deepcopy(vapor_pressure_data.catalog_payload)
    changed = 0
    for family in catalog["families"].values():
        rows = family["physical_properties"]["species"]
        for row in rows.values():
            if row.get("parent_oxide") != "Na2O":
                continue
            reaction_id = row["pressure_models"][0]["source_reaction_id"]
            reaction = next(
                entry
                for entry in row["source_reactions"]
                if entry["id"] == reaction_id
            )
            reaction["activity_input"]["standard_state"]["id"] = (
                "synthetic_distinct_liquid_activity_source"
            )
            standard = reaction.setdefault(
                "thermodynamic_standard_state",
                copy.deepcopy(reaction["activity_input"]["standard_state"]),
            )
            standard["id"] = "synthetic_distinct_liquid_K_standard"
            changed += 1
    assert changed > 0

    result = BuiltinVaporPressureProvider(catalog).dispatch(request)

    assert result.status == "ok"
    notice = result.diagnostic["vapor_pressure_numerator_provenance"]["Na"][
        "activity_standard_state_conversion"
    ]
    assert notice["from_standard_state_id"] == (
        "synthetic_distinct_liquid_activity_source"
    )
    assert notice["to_standard_state_id"] == (
        "synthetic_distinct_liquid_K_standard"
    )
    assert notice["reason"] == "standard_mismatch_unconverted"
    assert notice["conversion_performed"] is False
    assert notice["log10_activity_offset"] is None


def test_d349_unknown_mgo_catalog_k_id_is_not_converted(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    _, request = _lunar_mare_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    catalog = copy.deepcopy(vapor_pressure_data.catalog_payload)
    reaction = next(
        row
        for row in catalog["families"]["metals_mg_family"][
            "physical_properties"
        ]["species"]["Mg"]["source_reactions"]
        if row["id"] == "liquid_mgo_to_mg_gas"
    )
    reaction["thermodynamic_standard_state"]["id"] = (
        "synthetic_distinct_K_MgO"
    )

    result = BuiltinVaporPressureProvider(catalog).dispatch(request)
    notice = result.diagnostic["vapor_pressure_numerator_provenance"]["Mg"][
        "activity_standard_state_conversion"
    ]

    assert notice["from_standard_state_id"] == "Mg-009"
    assert notice["to_standard_state_id"] == "synthetic_distinct_K_MgO"
    assert notice["reason"] == "standard_mismatch_unconverted"
    assert notice["conversion_performed"] is False
    assert notice["log10_activity_offset"] is None


def test_d349_unknown_runtime_mgo_id_is_not_relabelled(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    monkeypatch,
):
    from simulator.chemistry import melt_activity as melt_activity_module

    _, request = _lunar_mare_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    original_standard_state_id = (
        melt_activity_module._melt_oxide_activity_standard_state_id
    )

    def unknown_runtime_standard_state_id(parent_oxide):
        if parent_oxide == "MgO":
            return "synthetic_unknown_runtime_MgO"
        return original_standard_state_id(parent_oxide)

    monkeypatch.setattr(
        melt_activity_module,
        "_melt_oxide_activity_standard_state_id",
        unknown_runtime_standard_state_id,
    )
    result = BuiltinVaporPressureProvider(vapor_pressure_data).dispatch(request)

    assert result.status == "ok"
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]["Mg"]
    notice = provenance["activity_standard_state_conversion"]
    assert provenance["melt_oxide_activity_reference_state"] == (
        "synthetic_unknown_runtime_MgO"
    )
    assert notice["from_standard_state_id"] == "synthetic_unknown_runtime_MgO"
    assert notice["to_standard_state_id"] == "Mg-010"
    assert notice["reason"] == "standard_mismatch_unconverted"
    assert notice["conversion_performed"] is False
    assert notice["log10_activity_offset"] is None
    assert provenance["vapor_activity_used_for_K"] == pytest.approx(
        provenance["melt_oxide_activity"]
    )
    assert provenance["vapor_activity_standard_state_id_used_for_K"] == (
        "synthetic_unknown_runtime_MgO"
    )


def test_d349_fe_activity_conversion_is_continuous_at_1650k():
    below = _activity_for_crystalline_vapor_k(
        "FeO",
        1.0,
        1650.0 - 1.0e-6,
        source_standard_state_id="Fe-020",
        target_standard_state_id="Fe-020",
        source_standard_state_phase="liquid",
        target_standard_state_phase="crystalline",
    )[0]
    above = _activity_for_crystalline_vapor_k(
        "FeO",
        1.0,
        1650.0 + 1.0e-6,
        source_standard_state_id="Fe-020",
        target_standard_state_id="Fe-020",
        source_standard_state_phase="liquid",
        target_standard_state_phase="liquid",
    )[0]

    # JANAF's rounded transition rows give ΔG_tr=-0.00065 kJ/mol at 1650 K;
    # this relative tolerance covers that source precision without shifting
    # the branch values by an unsupported constant.
    assert below == pytest.approx(above, rel=5.0e-5, abs=0.0)


def test_d349_fe_provider_pressure_exposes_ellingham_transition_step(
    vapor_pressure_data,
):
    from simulator.chemistry.ellingham_thermo import (
        ellingham_delta_g_kj_per_mol_o2,
    )

    composition = {"FeO": 25.0, "SiO2": 75.0}
    low_T, high_T = 1650.0 - 1.0e-6, 1650.0 + 1.0e-6
    low = _builtin_pressure_projection(
        vapor_pressure_data,
        composition,
        temperature_C=low_T - 273.15,
        pO2_bar=1.0,
    )
    high = _builtin_pressure_projection(
        vapor_pressure_data,
        composition,
        temperature_C=high_T - 273.15,
        pO2_bar=1.0,
    )
    low_route = low["vapor_pressure_numerator_provenance"]["Fe"]
    high_route = high["vapor_pressure_numerator_provenance"]["Fe"]
    pressure_ratio = (
        low["vapor_pressures_Pa"]["Fe"]
        / high["vapor_pressures_Pa"]["Fe"]
    )
    dG_low = ellingham_delta_g_kj_per_mol_o2("Fe", low_T)
    dG_high = ellingham_delta_g_kj_per_mol_o2("Fe", high_T)
    k_basis_ratio = math.exp(
        (dG_low * 1000.0 / low_T - dG_high * 1000.0 / high_T)
        / (2.0 * 8.31446261815324)
    )
    activity_ratio = (
        float(low_route["vapor_activity_used_for_K"])
        / float(high_route["vapor_activity_used_for_K"])
    )

    assert low_route["activity_standard_state_conversion"][
        "conversion_performed"
    ] is True
    assert high_route["activity_standard_state_conversion"][
        "conversion_performed"
    ] is False
    assert pressure_ratio == pytest.approx(
        k_basis_ratio * activity_ratio, rel=2e-6
    )
    assert pressure_ratio != pytest.approx(1.0, rel=1e-4)
def test_d349_removing_vapor_conversion_restores_the_fusion_factor(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    monkeypatch,
):
    provider, request = _lunar_mare_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    converted = provider.dispatch(request).diagnostic["vapor_pressures_Pa"]["Mg"]

    monkeypatch.setattr(
        vapor_pressure_module,
        "_activity_for_crystalline_vapor_k",
        lambda parent_oxide, activity, temperature_K, **_standards: (
            float(activity),
            None,
            None,
        ),
    )
    unconverted = provider.dispatch(request).diagnostic["vapor_pressures_Pa"]["Mg"]

    # 0.1253892371 is q79's independent JANAF Mg-008/Mg-009
    # exp(-DeltaG_fus/RT) factor at 1773.15 K.
    assert unconverted / converted == pytest.approx(0.1253892371, rel=1e-6)


def test_d349_conversion_extrapolates_and_flags_outside_janaf_overlap():
    above_melting, above_notice, _warning = (
        vapor_pressure_module._activity_for_crystalline_vapor_k(
            "MgO",
            0.5,
            3200.0,
            source_standard_state_id="Mg-009",
            target_standard_state_id="Mg-010",
            source_standard_state_phase="liquid",
            target_standard_state_phase="crystalline",
        )
    )
    assert above_notice["delta_g_fus_kJ_per_mol"] < 0.0
    assert above_notice["log10_activity_offset"] < 0.0
    assert above_melting < 0.5

    extrapolated_activity, notice, warning = (
        vapor_pressure_module._activity_for_crystalline_vapor_k(
            "MgO",
            0.5,
            4001.0,
            source_standard_state_id="Mg-009",
            target_standard_state_id="Mg-010",
            source_standard_state_phase="liquid",
            target_standard_state_phase="crystalline",
        )
    )
    # JANAF d-144 owner value at 4001 K; independently pinned from the
    # production fusion resolver, including its terminal-chord extrapolation.
    assert extrapolated_activity == pytest.approx(0.24878736429300258, rel=1e-12)
    assert notice["authority"] == "extrapolated"
    assert notice["authority_level"] == "extrapolated"
    assert notice["temperature_K"] == pytest.approx(4001.0)
    assert notice["from_standard_state_id"] in warning
    assert notice["to_standard_state_id"] in warning
    assert "log10_activity_offset=" in warning
    assert "authority_level=extrapolated" in warning


def test_d349_fe_vapor_uses_fe020_crystal_standard_below_1650k(
    vapor_pressure_data,
    monkeypatch,
):
    temperature_K = 1500.0
    composition = {"FeO": 25.0, "SiO2": 75.0}
    converted = _builtin_pressure_projection(
        vapor_pressure_data,
        composition,
        temperature_C=temperature_K - 273.15,
        pO2_bar=1.0,
    )
    pressure = converted["vapor_pressures_Pa"]["Fe"]
    route = converted["vapor_pressure_numerator_provenance"]["Fe"]
    notice = route["activity_standard_state_conversion"]

    assert pressure > 0.0
    assert notice["from_standard_state_id"] == "Fe-020"
    assert notice["to_standard_state_id"] == "Fe-020"
    assert notice["conversion_performed"] is True

    # The mutation goes through the same production vapor route and removes
    # only the shared activity-standard conversion.
    monkeypatch.setattr(
        vapor_pressure_module,
        "_activity_for_crystalline_vapor_k",
        lambda parent_oxide, activity, temperature_K, **_standards: (
            float(activity),
            None,
            None,
        ),
    )
    raw = _builtin_pressure_projection(
        vapor_pressure_data,
        composition,
        temperature_C=temperature_K - 273.15,
        pO2_bar=1.0,
    )
    # Branch-wise JANAF Fe-019 minus crystalline Fe-020 gives 2.158 kJ/mol
    # at 1500 K; raw activity omits that full liquid-to-crystal difference.
    assert raw["vapor_pressures_Pa"]["Fe"] / pressure == pytest.approx(
        math.exp(-2.158 / (8.31446261815324e-3 * temperature_K)),
        rel=1e-9,
    )


def test_d349_alphamelts_fe_fallback_uses_same_fe020_conversion(
    vapor_pressure_data,
):
    temperature_K = 1500.0
    temperature_C = temperature_K - 273.15
    composition = {"FeO": 25.0, "SiO2": 75.0}
    expected = _builtin_pressure_projection(
        vapor_pressure_data,
        composition,
        temperature_C=temperature_C,
        pO2_bar=1.0,
    )
    backend = AlphaMELTSBackend()
    projected = backend._activities_times_antoine_or_fail(
        temperature_C,
        {"FeO": 0.2},
        composition,
        pO2_bar=1.0,
        context="d-349 FeO standard substitution test",
    )

    assert projected["Fe"] == pytest.approx(
        expected["vapor_pressures_Pa"]["Fe"],
        rel=1e-10,
        abs=0,
    )
    notice = backend._antoine_activity_standard_state_notices["Fe"]
    assert notice["from_standard_state_id"] == "Fe-020"
    assert notice["to_standard_state_id"] == "Fe-020"
    assert notice["conversion_performed"] is True
    assert notice["source_standard_state_substitution"] == (
        "alphamelts_oxide_activity_no_declared_standard -> "
        "builtin_declared_standard"
    )


def test_d349_builtin_notices_follow_mno_and_coo_declared_standards(
    vapor_pressure_data,
):
    result = _builtin_pressure_projection(
        vapor_pressure_data,
        {"MnO": 50.0, "CoO": 50.0},
        temperature_C=1500.0,
        pO2_bar=1.0,
    )
    provenance = result["vapor_pressure_numerator_provenance"]

    for species in ("Mn", "MnO_gas"):
        notice = provenance[species]["activity_standard_state_conversion"]
        assert notice["reason"] == "standard_mismatch_unconverted"
        assert notice["from_standard_state_id"] == (
            MELT_OXIDE_CRYSTALLINE_ACTIVITY_REFERENCE_STATE
        )
        assert notice["to_standard_state_id"] == (
            MELT_OXIDE_ACTIVITY_REFERENCE_STATE
        )
        assert provenance[species]["vapor_activity_used_for_K"] == pytest.approx(
            provenance[species]["melt_oxide_activity"]
        )
        assert provenance[species][
            "vapor_activity_standard_state_id_used_for_K"
        ] == MELT_OXIDE_CRYSTALLINE_ACTIVITY_REFERENCE_STATE
        assert "vapor_activity_for_crystalline_K" not in provenance[species]

    coo_notice = provenance["CoO_gas"]["activity_standard_state_conversion"]
    assert coo_notice["reason"] == "standard_mismatch_unconverted"
    assert coo_notice["from_standard_state_id"] == (
        MELT_OXIDE_ACTIVITY_REFERENCE_STATE
    )
    assert coo_notice["to_standard_state_id"] == (
        MELT_OXIDE_CRYSTALLINE_ACTIVITY_REFERENCE_STATE
    )
    assert provenance["CoO_gas"]["vapor_activity_used_for_K"] == pytest.approx(
        provenance["CoO_gas"]["melt_oxide_activity"]
    )
    assert provenance["CoO_gas"][
        "vapor_activity_standard_state_id_used_for_K"
    ] == MELT_OXIDE_ACTIVITY_REFERENCE_STATE
    assert "vapor_activity_for_crystalline_K" not in provenance["CoO_gas"]


def test_d349_live_evaporation_gateway_uses_crystalline_activity_basis(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim, diagnostic, equilibrium, effective_source = _live_gateway_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    batch = _resolve_live_gateway_batch(
        sim,
        diagnostic,
        equilibrium,
        effective_source,
    )
    assert batch is not None

    for species in _D349_VAPOR_ROUTES:
        answer = batch.channel(species)
        assert isinstance(answer.pressure, PressureValue)
        gateway_provider_ratio = (
            answer.pressure.pa
            / diagnostic["vapor_pressures_Pa"][species]
        )
        assert gateway_provider_ratio == pytest.approx(
            _D349_GATEWAY_TO_PROVIDER_PRESSURE_RATIO[species],
            rel=1e-9,
            abs=0,
        )
        source_activity = answer.source_reaction_activity
        assert source_activity is not None
        assert source_activity.standard_state.phase == "liquid"
        reported = source_activity.derivation[
            "reported_activity_provenance"
        ]
        conversion = reported["activity_standard_state_conversion"]
        assert reported["vapor_activity_for_crystalline_K_standard_state_id"] == (
            conversion["to_standard_state_id"]
        )
        source_id, k_id = _D349_SOURCE_K_STANDARD_IDS[species]
        assert conversion["from_standard_state_id"] == source_id
        assert conversion["to_standard_state_id"] == k_id

def test_d349_live_gateway_requires_the_crystalline_id_on_every_route(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim, diagnostic, equilibrium, effective_source = _live_gateway_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    required_fields = (
        "activity_standard_state_conversion",
        "vapor_activity_for_crystalline_K",
        "vapor_activity_for_crystalline_K_standard_state_id",
    )
    for species in _D349_VAPOR_ROUTES:
        mutated = _diagnostic_with_route_provenance(
            diagnostic,
            species,
            remove=required_fields,
        )
        refused_batch = _resolve_live_gateway_batch(
            sim,
            mutated,
            equilibrium,
            effective_source,
        )
        assert refused_batch is None, species
        assert sim._last_vapour_batch_resolve_error["reason"] == (
            "vapour_batch_resolve_failed"
        )
        assert species in sim._last_vapour_batch_resolve_error["detail"]


@pytest.mark.parametrize("species", ("Mg", "Fe"))
@pytest.mark.parametrize(
    "missing_field",
    (
        "vapor_k_standard_state_phase",
        "vapor_k_standard_state_id",
        "activity_standard_state_conversion",
    ),
)
def test_d349_live_gateway_uses_channel_contract_when_provenance_is_missing(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    species,
    missing_field,
):
    temperature_K = 1500.0
    sim, diagnostic, equilibrium, effective_source = _live_gateway_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        temperature_C=temperature_K - 273.15,
    )
    mutated = _diagnostic_with_route_provenance(
        diagnostic,
        species,
        remove=(missing_field,),
    )

    refused = _resolve_live_gateway_batch(
        sim,
        mutated,
        equilibrium,
        effective_source,
        temperature_K=temperature_K,
    )

    assert refused is None
    assert sim._last_vapour_batch_resolve_error["reason"] == (
        "vapour_batch_resolve_failed"
    )
    assert species in sim._last_vapour_batch_resolve_error["detail"]


def test_d349_live_gateway_raw_liquid_assignment_fails_every_route(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim, diagnostic, equilibrium, effective_source = _live_gateway_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    for species in _D349_VAPOR_ROUTES:
        mutated = _diagnostic_with_route_provenance(
            diagnostic,
            species,
            vapor_activity_for_crystalline_K=diagnostic["activities"][species],
        )
        batch = _resolve_live_gateway_batch(
            sim,
            mutated,
            equilibrium,
            effective_source,
        )
        assert batch is None, species
        assert species in sim._last_vapour_batch_resolve_error["detail"]


def test_d349_fe_conversion_reaches_live_gateway_guard_below_1650k(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    temperature_K = 1500.0
    sim, diagnostic, equilibrium, effective_source = _live_gateway_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        temperature_C=temperature_K - 273.15,
    )
    route = diagnostic["vapor_pressure_numerator_provenance"]["Fe"]
    notice = route["activity_standard_state_conversion"]
    assert notice["conversion_performed"] is True
    assert notice["from_standard_state_id"] == "Fe-020"
    assert notice["to_standard_state_id"] == "Fe-020"
    assert route["vapor_activity_used_for_K"] != diagnostic["activities"]["Fe"]

    batch = _resolve_live_gateway_batch(
        sim,
        diagnostic,
        equilibrium,
        effective_source,
        temperature_K=temperature_K,
    )
    assert batch is not None
    assert isinstance(batch.channel("Fe").pressure, PressureValue)
    assert sim.vapour_rail_catalog.species["Fe"].evaluator.activity_exponent == 0.0
    # Fe's activity-independent catalog evaluator does not consume a typed
    # source_reaction_activity. The built-in provider applies the converted
    # activity before producing Fe's effective pressure, which the gateway
    # carries as a typed pressure value.
    assert batch.channel("Fe").source_reaction_activity is None

    raw_assignment = _diagnostic_with_route_provenance(
        diagnostic,
        "Fe",
        vapor_activity_for_crystalline_K=diagnostic["activities"]["Fe"],
    )
    refused = _resolve_live_gateway_batch(
        sim,
        raw_assignment,
        equilibrium,
        effective_source,
        temperature_K=temperature_K,
    )
    assert refused is None
    assert "Fe" in sim._last_vapour_batch_resolve_error["detail"]


@pytest.mark.parametrize(
    ("composition_wt_pct", "activities", "expected_peer"),
    [
        (
            {"MgO": 50.0, "CaO": 40.0, "Na2O": 10.0},
            {"MgO": 0.4, "CaO": 0.3, "Na2O": 0.2},
            "Na",
        ),
        (
            {"MgO": 50.0, "CaO": 50.0},
            {},
            None,
        ),
    ],
    ids=("mixed-with-peer", "all-mg-ca"),
)
def test_d350_alphamelts_fallback_substitutes_builtin_mgo_cao_activity(
    vapor_pressure_data,
    composition_wt_pct,
    activities,
    expected_peer,
):
    backend = AlphaMELTSBackend()
    expected_diagnostic = _builtin_pressure_projection(
        vapor_pressure_data,
        composition_wt_pct,
        temperature_C=1500.0,
        pO2_bar=1.0,
    )
    projection = backend._activities_times_antoine_or_fail(
        1500.0,
        activities,
        composition_wt_pct,
        pO2_bar=1.0,
        context="d-350 built-in activity substitution test",
    )

    assert isinstance(projection, dict)
    for species in ("Mg", "Ca"):
        assert projection[species] == pytest.approx(
            expected_diagnostic["vapor_pressures_Pa"][species],
            rel=1e-10,
            abs=0,
        )
    if expected_peer is not None:
        assert projection[expected_peer] > 0.0

    notices = backend._antoine_activity_standard_state_notices
    assert set(notices) >= {"Mg", "Ca"}
    for species, parent_oxide in (("Mg", "MgO"), ("Ca", "CaO")):
        notice = notices[species]
        assert notice["parent_oxide"] == parent_oxide
        assert notice["reason"] == "activity_standard_state_substitution"
        assert notice["source_standard_state_substitution"] == (
            "alphamelts_oxide_activity_no_declared_standard -> "
            "builtin_declared_standard"
        )
        source_id, k_id = _D349_SOURCE_K_STANDARD_IDS[species]
        assert notice["from_standard_state_id"] == source_id
        assert notice["to_standard_state_id"] == k_id
        assert math.isfinite(float(notice["log10_activity_offset"]))

    diagnostics = backend._vapor_pressure_diagnostics({}, projection, "alphamelts")
    assert set(
        diagnostics["vapor_activity_standard_state_notices_by_species"]
    ) == set(notices)


def test_d350_alphamelts_volatility_diagnostic_uses_builtin_mgo_cao_activity(
    vapor_pressure_data,
):
    composition_wt_pct = {"MgO": 50.0, "CaO": 50.0}
    expected_diagnostic = _builtin_pressure_projection(
        vapor_pressure_data,
        composition_wt_pct,
        temperature_C=1600.0,
        pO2_bar=1.0e-9,
    )
    result = _analytical_vapor_pressures_from_activities(
        vapor_pressure_data=vapor_pressure_data,
        temperature_C=1600.0,
        pO2_bar=1.0e-9,
        melt_oxide_activities={"MgO": 0.4, "CaO": 0.3},
        composition_wt_pct=composition_wt_pct,
    )

    for species in ("Mg", "Ca"):
        assert result["species"][species]["P_eq_Pa"] == pytest.approx(
            expected_diagnostic["vapor_pressures_Pa"][species],
            rel=1e-10,
            abs=0,
        )
    for species in ("Mg", "Ca"):
        source_id, k_id = _D349_SOURCE_K_STANDARD_IDS[species]
        assert any(
            f"species={species} " in notice
            and "reason=activity_standard_state_substitution" in notice
            and "source_standard_state_substitution="
            "alphamelts_oxide_activity_no_declared_standard->"
            "builtin_declared_standard" in notice
            and source_id in notice
            and k_id in notice
            and "log10_activity_offset=" in notice
            for notice in result["warnings"]
        )


@pytest.mark.parametrize(
    ("composition_wt_pct", "activities"),
    (
        ({"MgO": 50.0, "CaO": 40.0, "Na2O": 10.0}, {"Na2O": 0.2}),
        ({"MgO": 50.0, "CaO": 50.0}, {}),
    ),
    ids=("mixed", "all-mg-ca"),
)
def test_d350_alphamelts_substitution_reaches_evaporation_gateway(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    composition_wt_pct,
    activities,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.temperature_C = 1500.0
    sim.melt.p_total_mbar = 1000.0
    expected_species = ("Mg", "Ca")
    if "Na2O" in composition_wt_pct:
        # Use the feedstock's actual Na/K/Mg/Ca/FeO proportions, normalized
        # only across these volatile parents so the FeO substitution is tested
        # through the same mixed fallback.
        account_mol = sim.atom_ledger.mol_by_account("process.cleaned_melt")
        target_oxides = ("Na2O", "K2O", "MgO", "CaO", "FeO")
        target_weights = {
            oxide: float(account_mol.get(oxide, 0.0))
            * resolve_species_formula(oxide).molar_mass_kg_per_mol()
            for oxide in target_oxides
            if float(account_mol.get(oxide, 0.0)) > 0.0
        }
        assert set(target_weights) == set(target_oxides)
        target_total = sum(target_weights.values())
        composition_wt_pct = {
            oxide: weight / target_total * 100.0
            for oxide, weight in target_weights.items()
        }
        activities = {
            "Na2O": 0.2,
            "K2O": 0.1,
            "MgO": 0.4,
            "CaO": 0.3,
            "FeO": 0.2,
        }
        expected_species = ("Na", "K", "Mg", "Ca", "Fe")
    backend = AlphaMELTSBackend()
    projection = backend._activities_times_antoine_or_fail(
        1500.0,
        activities,
        composition_wt_pct,
        pO2_bar=1.0,
        context="d-350 evaporation gateway integration test",
    )
    assert isinstance(projection, dict)
    expected = _builtin_pressure_projection(
        vapor_pressure_data,
        composition_wt_pct,
        temperature_C=1500.0,
        pO2_bar=1.0,
    )
    for species in expected_species:
        assert projection[species] == pytest.approx(
            expected["vapor_pressures_Pa"][species],
            rel=1e-10,
            abs=0,
        )

    diagnostics = backend._vapor_pressure_diagnostics(
        {
            "pO2_bar": 1.0,
            "source_reaction_fO2_bar": 1.0,
            "source_reaction_fO2_log10": 0.0,
            "source_reaction_activity_pressure_bar": 1.0,
            "source_reaction_redox_model_id": "REF-001-kress-carmichael-1991",
            "source_reaction_composition_wt_pct": dict(composition_wt_pct),
        },
        projection,
        "alphamelts_fallback",
    )
    sim._last_vapor_pressure_diagnostic = diagnostics
    sim._last_vapor_pressures_source = backend._vapor_pressure_source_map(
        projection,
        "alphamelts_fallback",
    )
    sim._last_backend_diagnostics = diagnostics
    equilibrium = SimpleNamespace(
        status="ok",
        temperature_C=1500.0,
        pressure_bar=1.0,
        liquid_fraction=0.5,
        fO2_log=0.0,
        pO2_bar=1.0,
        diagnostics=diagnostics,
        activity_coefficients=dict(diagnostics.get("activities", {})),
        vapor_pressures_Pa=projection,
        vapor_pressures_source=sim._last_vapor_pressures_source,
    )

    sim._calculate_evaporation(equilibrium)
    batch = sim._last_vapour_batch
    assert batch is not None
    for species in expected_species:
        answer = batch.channel(species)
        assert isinstance(answer.pressure, PressureValue)
        assert answer.pressure.pa > 0.0
        assert isinstance(answer.flux, FluxEligible)
        route = diagnostics["vapor_pressure_numerator_provenance"][species]
        expected_gateway_pressure = sim.vapour_rail_catalog.species[
            species
        ].evaluator.evaluate(
            1773.15,
            source_activity=float(route["vapor_activity_used_for_K"]),
            pO2_bar=1.0,
        ).pressure_pa
        assert answer.pressure.pa == pytest.approx(
            expected_gateway_pressure, rel=1e-12, abs=0
        )
        activity = diagnostics["activities"][species]
        if species != "Fe":
            assert answer.source_reaction_activity is not None
            assert answer.source_reaction_activity.as_pressure_activity() == pytest.approx(
                activity, rel=1e-12, abs=0
            )
        used_activity = float(route["vapor_activity_used_for_K"])
        assert used_activity > 0.0
        if species in {"Mg", "Ca"}:
            assert used_activity != pytest.approx(
                activity,
                rel=1e-6,
                abs=0,
            )
        elif species == "Fe":
            assert used_activity == pytest.approx(
                activity,
                rel=1e-10,
                abs=0,
            )
        else:
            assert used_activity == pytest.approx(
                activity,
                rel=1e-10,
                abs=0,
            )
        notice = diagnostics[
            "vapor_activity_standard_state_notices_by_species"
        ][species]
        assert notice["reason"] == "activity_standard_state_substitution"
        assert math.isfinite(float(notice["log10_activity_offset"]))
        if species == "Fe":
            assert notice["conversion_performed"] is False
            assert notice["source_standard_state_relation_reason"] == (
                "source_and_K_standard_states_match"
            )
        assert notice["source_standard_state_substitution"] == (
            "alphamelts_oxide_activity_no_declared_standard -> "
            "builtin_declared_standard"
        )
        if species in _D349_SOURCE_K_STANDARD_IDS:
            expected_source_id, expected_k_id = (
                _D349_SOURCE_K_STANDARD_IDS[species]
            )
        elif species == "Fe":
            expected_source_id = expected_k_id = "Fe-020"
        else:
            expected_source_id = expected_k_id = (
                MELT_OXIDE_ACTIVITY_REFERENCE_STATE
            )
        assert notice["from_standard_state_id"] == expected_source_id
        assert notice["to_standard_state_id"] == expected_k_id

    from simulator.runner import _vapor_pressure_source_report

    report = _vapor_pressure_source_report(sim)
    assert set(report["vapor_activity_standard_state_notices_by_species"]) >= set(
        expected_species
    )

    reported_flux = sim._last_evaporation_flux_diagnostic[
        "evaporation_flux_kg_hr"
    ]
    below_threshold = sim._last_evaporation_flux_diagnostic[
        "below_threshold_evaporation_flux"
    ]
    for species in set(expected_species) - {"Ca"}:
        assert reported_flux[species] > 0.0
    # Ca has a positive pressure and an eligible channel. The flux provider
    # retains its positive rate in below_threshold_evaporation_flux because
    # it is <= the existing 1e-12 kg/hr sparse-output threshold.
    assert "Ca" not in reported_flux
    assert 0.0 < below_threshold["fluxes_kg_hr"]["Ca"] <= below_threshold[
        "threshold_kg_hr"
    ]
