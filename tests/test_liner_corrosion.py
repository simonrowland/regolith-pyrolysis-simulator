from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
from types import SimpleNamespace
import warnings

import pytest

from simulator.accounting.formulas import parse_formula
import simulator.liner_corrosion as liner_corrosion
from simulator.furnace_materials import (
    InterfaceChemistryRefusal,
    load_interface_chemistry,
    load_interface_invariant_table,
    require_interface_porosity,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.liner_corrosion import (
    LinerCorrosionRefusal,
    MageminLinerSampler,
    TierAInvariantSampler,
    TabulatedLiquidFractionCurve,
    attach_liner_state_metadata,
    build_liner_state_segment,
    contaminated_interface_composition,
    contamination_mixing_fractions,
    corrosion_campaign_recurrence,
    first_liquid_tier_a,
    load_liner_corrosion_invariants,
    magemin_request_cache_key,
    post_onset_dissolution_ratio,
    reacted_layer_depths,
    tier_a_first_liquid_onset,
)
from simulator.melt_backend.liquidus import (
    LIQUIDUS_LIQUID_EPSILON,
    LIQUIDUS_SOLID_EPSILON,
)


def _formula_derived_anorthite() -> dict[str, float]:
    """Return CaAl2Si2O8's oxide mass percentages via the formula owner."""

    cao = parse_formula("CaO").molar_mass_g_per_mol()
    alumina = parse_formula("Al2O3").molar_mass_g_per_mol()
    silica = parse_formula("SiO2").molar_mass_g_per_mol()
    total = cao + alumina + 2.0 * silica
    return {
        "CaO": 100.0 * cao / total,
        "Al2O3": 100.0 * alumina / total,
        "SiO2": 200.0 * silica / total,
    }


def test_magemin_request_cache_key_covers_every_equilibrium_input():
    composition = {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0}
    key = magemin_request_cache_key(
        engine="magemin:ig",
        version=1,
        composition_wt_pct=composition,
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
        fO2_log=-9.0,
    )

    assert key == magemin_request_cache_key(
        engine="magemin:ig",
        version=1,
        composition_wt_pct={"CaO": 20.0, "SiO2": 50.0, "Al2O3": 30.0},
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
        fO2_log=-9.0,
    )
    for changed in (
        {"engine": "magemin:other"},
        {"version": 2},
        {"composition_wt_pct": {**composition, "CaO": 21.0, "SiO2": 49.0}},
        {"temperature_C": 1501.0},
        {"pressure_bar": 2.0e-6},
        {"fO2_log": -8.0},
    ):
        request = {
            "engine": "magemin:ig",
            "version": 1,
            "composition_wt_pct": composition,
            "temperature_C": 1500.0,
            "pressure_bar": 1.0e-6,
            "fO2_log": -9.0,
            **changed,
        }
        assert magemin_request_cache_key(**request) != key

    # Composition, temperature, and fO2 use six decimal places; pressure uses
    # eight significant digits so low vacuum values do not round to zero.
    assert key == magemin_request_cache_key(
        engine="magemin:ig",
        version=1,
        composition_wt_pct={
            "SiO2": 50.0000001,
            "Al2O3": 29.9999999,
            "CaO": 20.0,
        },
        temperature_C=1500.0000001,
        pressure_bar=1.00000001e-6,
        fO2_log=-9.00000001,
    )


class _FakeMageminBackend:
    def __init__(self, liquid_fraction=None):
        self.calls = []
        self.initialized = 0
        self.closed = 0
        self.liquid_fraction = liquid_fraction or (
            lambda composition: min(1.0, composition.get("Na2O", 0.0) / 20.0)
        )

    def initialize(self, config):
        self.initialized += 1
        assert config == {"database": "ig"}
        return True

    def equilibrate(
        self,
        temperature_C,
        *,
        pressure_bar,
        fO2_log,
        projected_oxide_wt_pct,
    ):
        self.calls.append(
            (temperature_C, pressure_bar, fO2_log, dict(projected_oxide_wt_pct))
        )
        liquid_fraction = self.liquid_fraction(projected_oxide_wt_pct)
        return SimpleNamespace(
            status="ok",
            liquid_fraction=liquid_fraction,
            phase_masses_kg={"liq": liquid_fraction, "solid": 1.0 - liquid_fraction},
            phase_compositions={
                "liq": dict(projected_oxide_wt_pct),
                "solid": dict(projected_oxide_wt_pct),
            },
            warnings=[],
        )

    def close(self):
        self.closed += 1

    def find_liquidus_solidus(self, *args, **kwargs):
        raise AssertionError("liner adapter must use equilibrate(), never the finder")


def test_magemin_adapter_gates_before_engine_and_checks_phase_closure():
    backend = _FakeMageminBackend()
    sampler = MageminLinerSampler(backend)
    with pytest.raises(LinerCorrosionRefusal) as refused:
        sampler({"Al2O3": 60.0, "CaO": 40.0}, 1500.0, 1.0e-6)
    assert refused.value.reason == "liner_first_melt_engine_out_of_domain"
    assert backend.calls == []
    assert backend.initialized == 0

    composition = {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0}
    assert sampler(composition, 1500.0, 1.0e-6) == 0.0
    assert sampler(composition, 1500.0, 1.0e-6) == 0.0
    assert backend.initialized == 1
    assert len(backend.calls) == 1
    assert backend.calls[0][:3] == (1500.0, 1.0e-6, -9.0)

    bad_closure = _FakeMageminBackend()
    bad_closure.equilibrate = lambda *args, **kwargs: SimpleNamespace(
        status="ok",
        liquid_fraction=0.5,
        phase_masses_kg={"liq": 1.0},
        phase_compositions={"liq": {"SiO2": 100.0}},
        warnings=[],
    )
    with pytest.raises(LinerCorrosionRefusal) as closure:
        MageminLinerSampler(bad_closure)(composition, 1500.0, 1.0e-6)
    assert closure.value.reason == "engine_phase_closure_failed"


def test_magemin_closure_uses_reported_phase_mass_and_liquid_composition():
    composition = {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0}

    class ReportedPhaseBackend(_FakeMageminBackend):
        def equilibrate(
            self,
            temperature_C,
            *,
            pressure_bar,
            fO2_log,
            projected_oxide_wt_pct,
        ):
            if temperature_C < 1553.0:
                return SimpleNamespace(
                    status="ok",
                    liquid_fraction=0.0,
                    phase_masses_kg={"anorthite": 1.0},
                    phase_compositions={},
                    liquid_composition_wt_pct={},
                    phases_present=["anorthite"],
                    warnings=[],
                )
            return SimpleNamespace(
                status="ok",
                liquid_fraction=1.0,
                phase_masses_kg={"liq": 1.0},
                phase_compositions={},
                liquid_composition_wt_pct=dict(projected_oxide_wt_pct),
                phases_present=["liq"],
                warnings=[],
            )

    sampler = MageminLinerSampler(ReportedPhaseBackend())
    assert sampler(composition, 1500.0, 1.0e-6) == 0.0
    assert "engine_phase_closure_unverified" in sampler.flags
    assert sampler(composition, 1600.0, 1.0e-6) == 1.0

    class UnclosedBackend(_FakeMageminBackend):
        def equilibrate(self, *_args, **_kwargs):
            return SimpleNamespace(
                status="ok",
                liquid_fraction=0.0,
                phase_masses_kg={"liq": 0.5, "solid-a": 0.5},
                phase_compositions={},
                liquid_composition_wt_pct={},
                phases_present=["liq", "solid-a"],
                warnings=[],
            )

    with pytest.raises(LinerCorrosionRefusal) as unclosed:
        MageminLinerSampler(UnclosedBackend())(composition, 1500.0, 1.0e-6)
    assert unclosed.value.reason == "engine_phase_closure_failed"

    class PartialContradictoryBackend(_FakeMageminBackend):
        def equilibrate(self, *_args, **_kwargs):
            return SimpleNamespace(
                status="ok",
                liquid_fraction=0.0,
                phase_masses_kg={"solid-a": 0.5, "solid-b": 0.5},
                phase_compositions={"solid-a": {"TiO2": 100.0}},
                liquid_composition_wt_pct={},
                phases_present=["solid-a", "solid-b"],
                warnings=[],
            )

    with pytest.raises(LinerCorrosionRefusal) as partial_contradiction:
        MageminLinerSampler(PartialContradictoryBackend())(
            composition,
            1500.0,
            1.0e-6,
        )
    assert partial_contradiction.value.reason == "engine_phase_closure_failed"


def test_magemin_phi_curve_is_cached_and_interpolated():
    backend = _FakeMageminBackend()
    sampler = MageminLinerSampler(backend)
    liner = {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0}
    first = sampler.curve(
        liner,
        {"Na": 0.1},
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
    )
    first_call_count = len(backend.calls)
    second = sampler.curve(
        liner,
        {"Na": 0.1},
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
    )
    assert second is first
    assert len(backend.calls) == first_call_count
    composition = {
        oxide: 0.9 * liner.get(oxide, 0.0)
        + 0.1 * first.deposit_composition_wt_pct.get(oxide, 0.0)
        for oxide in set(liner) | set(first.deposit_composition_wt_pct)
    }
    assert second(composition, 1500.0, 1.0e-6) == pytest.approx(
        min(1.0, composition.get("Na2O", 0.0) / 20.0), abs=0.02
    )
    sampler.curve(
        liner,
        {"Na": 0.1},
        temperature_C=1501.0,
        pressure_bar=1.0e-6,
    )
    assert len(backend.calls) > first_call_count


def test_magemin_phi_curve_brackets_valid_request_near_silica_domain_edge():
    backend = _FakeMageminBackend()
    sampler = MageminLinerSampler(backend)
    liner = {"SiO2": 31.0, "Al2O3": 49.0, "CaO": 20.0}
    curve = sampler.curve(
        liner,
        {"Na": 0.1},
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
    )
    deposit = curve.deposit_composition_wt_pct
    request = {
        oxide: 0.99 * liner.get(oxide, 0.0) + 0.01 * deposit.get(oxide, 0.0)
        for oxide in set(liner) | set(deposit)
    }

    assert curve(request, 1500.0, 1.0e-6) == pytest.approx(
        request.get("Na2O", 0.0) / 20.0
    )
    assert curve.points[-1][0] >= 0.01
    assert all(call[3]["SiO2"] >= 30.0 for call in backend.calls)


@pytest.mark.live_engine
@pytest.mark.xdist_group("magemin_fullrun_a")
def test_anorthite_first_liquid_anchor_requires_live_magemin():
    # CaAl2Si2O8 = CaO + Al2O3 + 2 SiO2. The literature melting point is
    # 1553 C at about 1 bar. The formula owner derives the oxide-basis bulk.
    formula_derived_anorthite = _formula_derived_anorthite()
    assert formula_derived_anorthite["CaO"] == pytest.approx(20.16, abs=0.01)
    assert formula_derived_anorthite["Al2O3"] == pytest.approx(36.65, abs=0.01)
    assert formula_derived_anorthite["SiO2"] == pytest.approx(43.19, abs=0.01)
    # Match the ruling's cited oxide precision for the live engine request;
    # formula-derived values above independently verify the stoichiometry.
    anorthite = {"CaO": 20.16, "Al2O3": 36.65, "SiO2": 43.19}
    from simulator.melt_backend.magemin import MAGEMinBackend

    backend = MAGEMinBackend()
    direct_backend = MAGEMinBackend()
    sampler = MageminLinerSampler(backend)
    try:
        try:
            if not direct_backend.initialize({"database": "ig"}):
                pytest.skip("MAGEMin is required for the anorthite anchor")
            # These are direct-backend live engine measurements, not
            # anorthite physics pins. At compositions only 0.0036 wt% apart,
            # MAGEMin reports small liquid fractions at different scan
            # temperatures; this is the d-421 continuity reproducer. The
            # expected values are the controller's studio-3 MAGEMin 1 bar / ig
            # profile, within its measured output precision. The adapter route
            # is pinned separately because its projected-oxide request gives a
            # distinct measured value from this composition_kg request.
            temperatures_C = (
                500.0,
                800.0,
                1000.0,
                1025.0,
                1200.0,
                1500.0,
                1550.0,
                1600.0,
            )
            # The exact profile uses explicit controller literals derived
            # from oxide molar masses 56.077 / 101.961 / 60.084; the rounded
            # profile uses Morey 1964 Table 26, row 6's printed 0.01 wt% values.
            measured = {
                "controller_exact_literal": (
                    {"CaO": 20.156647, "Al2O3": 36.649461, "SiO2": 43.193892},
                    (0.0, 0.0, 0.00217, 0.00218, 0.00236, 0.00786, 0.04235, 1.0),
                ),
                "morey_rounded": (
                    anorthite,
                    (0.00227, 0.0, 0.0, 0.00257, 0.00275, 0.00875, 0.04385, 1.0),
                ),
            }
            raw_profiles: dict[str, dict[float, float]] = {}
            engine_version = direct_backend.get_engine_version()
            for name, (composition, expected_fractions) in measured.items():
                raw_profiles[name] = {}
                for temperature_C, expected_fraction in zip(
                    temperatures_C, expected_fractions, strict=True
                ):
                    direct = direct_backend.equilibrate(
                        temperature_C,
                        composition_kg=dict(composition),
                        fO2_log=liner_corrosion.MAGEMIN_FO2_LOG,
                        pressure_bar=1.0,
                    )
                    assert direct.status == "ok", direct.warnings
                    raw_profiles[name][temperature_C] = direct.liquid_fraction
                    assert direct.liquid_fraction == pytest.approx(
                        expected_fraction, abs=2e-5
                    ), (
                        f"live MAGEMin {engine_version} "
                        f"profile changed for {name} at {temperature_C} C"
                    )
                assert raw_profiles[name] == pytest.approx(
                    dict(zip(temperatures_C, expected_fractions, strict=True)),
                    abs=2e-5,
                )

            # The controller's direct profile uses the 56.077-based oxide
            # mass literals above. Formula-owner atomic weights differ by less
            # than 1e-3 wt%, but the engine gives a different 500 C result;
            # keep this sensitivity example pinned on each exact composition.
            controller_exact = measured["controller_exact_literal"][0]
            assert max(
                abs(formula_derived_anorthite[oxide] - controller_exact[oxide])
                for oxide in controller_exact
            ) < 1e-3
            formula_owner_at_500 = direct_backend.equilibrate(
                500.0,
                composition_kg=dict(formula_derived_anorthite),
                fO2_log=liner_corrosion.MAGEMIN_FO2_LOG,
                pressure_bar=1.0,
            )
            assert formula_owner_at_500.status == "ok", formula_owner_at_500.warnings
            assert formula_owner_at_500.liquid_fraction == pytest.approx(
                0.0018800188, abs=2e-5
            )
            assert raw_profiles["controller_exact_literal"][500.0] == pytest.approx(
                0.0, abs=1e-12
            )

            # The sampler routes the request through projected oxide wt% and
            # measured 0.0018900189 at exact anorthite / 1000 C on studio-3;
            # keep that pin on the adapter call rather than equating its value
            # with the direct composition_kg result above (0.00217).
            assert sampler(formula_derived_anorthite, 1000.0, 1.0) == pytest.approx(
                0.0018900189, abs=2e-5
            )

            # In the adapter profile, the exact nominal first crosses at 1000
            # C, while the Morey-rounded input spuriously crosses at the 500 C
            # scan edge. For each input, the two live 0.01 wt% neighbours
            # agree on [1000, 1025] C, so d-421 takes its nominal-outlier ->
            # neighbours branch and exposes engine_composition_sensitive.
            profiles = {
                name: liner_corrosion._tier_b_reference_profile(
                    sampler,
                    composition,
                    pressure_bar=1.0,
                    literature_melting_temperature_C=1553.0,
                )
                for name, (composition, _) in measured.items()
            }
            for name, expected_nominal_bracket in (
                ("controller_exact_literal", [975.0, 1000.0]),
                ("morey_rounded", [None, 500.0]),
            ):
                continuity_profile = profiles[name]
                sensitivity = continuity_profile["engine_composition_sensitive"]
                assert continuity_profile["composition_continuity"]["status"] == "sensitive"
                assert sensitivity["nominal_bracket_C"] == expected_nominal_bracket
                assert sensitivity["neighbour_brackets_C"] == [
                    [1000.0, 1025.0],
                    [1000.0, 1025.0],
                ]
                assert sensitivity["neighbour_bracket_C"] == [1000.0, 1025.0]
                assert continuity_profile["first_liquid_bracket_C"] == [
                    1000.0,
                    1025.0,
                ]
                assert "engine_composition_sensitive" in continuity_profile["flags"]
                assert continuity_profile["engine_version"] == (
                    engine_version
                )

            profile = profiles["controller_exact_literal"]
        except LinerCorrosionRefusal as exc:
            if exc.reason == "engine_unavailable":
                pytest.skip("MAGEMin is required for the anorthite anchor")
            raise
        assert profile["first_liquid_bracket_C"] == [1000.0, 1025.0]
        assert profile["bulk_melt_bracket_C"] == [1550.0, 1575.0]
        assert profile["bulk_melt_residual_vs_literature_C"] == [-3.0, 22.0]
        assert profile["bulk_melt_residual_ticket"] == "b-1231"
        assert profile["engine_version"] == engine_version
        assert profile["engine_minor_phase_eutectic"]["temperature_bracket_C"] == [
            1000.0,
            1025.0,
        ]
        assert profile["engine_minor_phase_eutectic"]["plateau_liquid_fraction"] == (
            pytest.approx(0.0026, abs=0.0002)
        )
        assert profile["engine_minor_phase_eutectic"]["phases"]
        assert "phase_closure_unverified" in profile["flags"]
        pure_rating = liner_corrosion._tier_b_melt_activity_rating(
            formula_derived_anorthite,
            formula_derived_anorthite,
            {},
            sampler=sampler,
            pressure_bar=1.0,
            margin_C=25.0,
            literature_melting_temperature_C=1553.0,
            pure_reference=profile,
        )
        assert pure_rating["rating_temperature_C"] == 1528.0
        assert pure_rating["bulk_melt_residual_vs_literature_C"] == [-3.0, 22.0]
        assert pure_rating["engine_minor_phase_eutectic"] == profile[
            "engine_minor_phase_eutectic"
        ]
        assert "deposit_melt_below_resolution" in pure_rating["flags"]
    finally:
        sampler.close()
        direct_backend.close()


def test_tier_b_reference_uses_neighbours_when_exact_anorthite_is_an_outlier():
    exact_anorthite = _formula_derived_anorthite()
    perturbed_anorthite = {oxide: value * 0.99 for oxide, value in exact_anorthite.items()}
    perturbed_anorthite["Na2O"] = 1.0
    samples: set[tuple[tuple[str, float], ...]] = set()

    class EndmemberDiscontinuity:
        def __call__(self, composition, temperature_C, _pressure_bar):
            normalized = tuple(
                sorted((oxide, round(float(wt_pct), 8)) for oxide, wt_pct in composition.items())
            )
            samples.add(normalized)
            is_exact = all(
                math.isclose(
                    float(composition.get(oxide, 0.0)), value,
                    rel_tol=0.0, abs_tol=1.0e-8,
                )
                for oxide, value in exact_anorthite.items()
            )
            is_perturbed_nominal = all(
                math.isclose(
                    float(composition.get(oxide, 0.0)), value,
                    rel_tol=0.0, abs_tol=1.0e-8,
                )
                for oxide, value in perturbed_anorthite.items()
            )
            if is_perturbed_nominal and temperature_C < 1025.0:
                return 0.008
            if is_exact and temperature_C < 1550.0:
                return 0.008
            if (
                composition.get("Na2O", 0.0) > 0.0
                and 1025.0 <= temperature_C < 1550.0
            ):
                return 0.008
            if temperature_C <= 1000.0:
                return 0.0
            if temperature_C < 1025.0:
                return 0.0
            if temperature_C < 1550.0:
                return 0.0026
            if temperature_C < 1575.0:
                return 0.044
            return 1.0

    reference = liner_corrosion._tier_b_reference_profile(
        EndmemberDiscontinuity(),
        exact_anorthite,
        pressure_bar=1.0,
        literature_melting_temperature_C=1553.0,
    )

    assert reference["liquid_fraction_by_temperature_C"][500.0] == 0.0
    assert reference["first_liquid_bracket_C"] == [1000.0, 1025.0]
    assert reference["bulk_melt_bracket_C"] == [1550.0, 1575.0]
    assert "engine_composition_sensitive" in reference["flags"]
    sensitivity = reference["engine_composition_sensitive"]
    assert sensitivity["nominal_bracket_C"] == [None, 500.0]
    assert sensitivity["neighbour_bracket_C"] == [1000.0, 1025.0]
    assert sensitivity["delta_wt_pct"] == 0.01
    assert len(samples) == 3

    aggressiveness_sampler = EndmemberDiscontinuity()
    suppressed_at_scan_edge = liner_corrosion.species_aggressiveness_finite_difference(
        exact_anorthite,
        "Na",
        sampler=aggressiveness_sampler,
        temperature_C=500.0,
        pressure_bar=1.0,
    )
    assert suppressed_at_scan_edge["delta_liquid_fraction_per_wt_pct"] == 0.0

    positive_at_service = liner_corrosion.species_aggressiveness_finite_difference(
        exact_anorthite,
        "Na",
        sampler=aggressiveness_sampler,
        temperature_C=1200.0,
        pressure_bar=1.0,
    )
    assert positive_at_service["delta_liquid_fraction_per_wt_pct"] == pytest.approx(
        0.0054
    )
    assert liner_corrosion.ENGINE_COMPOSITION_SENSITIVE_FLAG in positive_at_service[
        "flags"
    ]
    perturbed_evidence = positive_at_service["engine_composition_sensitive"][
        "perturbed_profile"
    ]
    assert perturbed_evidence["nominal_bracket_C"] == [None, 500.0]
    assert perturbed_evidence["neighbour_brackets_C"] == [
        [1000.0, 1025.0],
        [1000.0, 1025.0],
    ]
    segment = liner_corrosion.build_liner_state_segment(
        {
            "composition": exact_anorthite,
            "criterion_tier": "B",
            "density_kg_m3": 1000.0,
            "reacted_layer_depth_m": 0.001,
            "margin_C": 0.0,
            "horizon_campaigns": 0,
            "porosity_fraction": 0.0,
        },
        {"Na": 1e-5},
        area_m2=1.0,
        service_temperature_C=1200.0,
        pressure_bar=1.0,
        sampler=aggressiveness_sampler,
        deposit_inert_kg_by_species={"Na": 0.0},
        deposit_reactive_kg_by_species={"Na": 0.0},
        x_star=0.5,
    )
    segment_aggressiveness = segment["species_aggressiveness"]["Na"]
    assert segment_aggressiveness["delta_liquid_fraction_per_wt_pct"] == pytest.approx(
        0.0054
    )
    assert segment_aggressiveness["engine_composition_sensitive"][
        "perturbed_profile"
    ]["nominal_bracket_C"] == [None, 500.0]
    assert liner_corrosion.ENGINE_COMPOSITION_SENSITIVE_FLAG in segment["flags"]


def test_tier_b_saturation_uses_continuity_corrected_curve_for_x_star():
    liner = {"CaO": 20.0, "Al2O3": 30.0, "SiO2": 50.0}
    deposit = {"Na2O": 100.0}
    unstable_nominal = liner_corrosion._composition_at_fraction(
        liner, deposit, 0.5
    )

    class UnstableSaturationSampler(liner_corrosion.CurveLiquidFractionSampler):
        def __call__(self, composition, temperature_C, _pressure_bar):
            fraction = float(composition.get("Na2O", 0.0)) / 100.0
            is_unstable_nominal = all(
                math.isclose(
                    float(composition.get(oxide, 0.0)), value,
                    rel_tol=0.0, abs_tol=1.0e-8,
                )
                for oxide, value in unstable_nominal.items()
            )
            if is_unstable_nominal and temperature_C == 1400.0:
                return 1.0
            if temperature_C < 1400.0:
                return 0.0
            if temperature_C < 1425.0:
                return fraction if fraction >= 0.9 else 0.0
            if temperature_C < 1550.0:
                return fraction
            if temperature_C < 1575.0:
                return 0.044
            return 1.0

        def curve(
            self,
            liner_composition_wt_pct,
            deposit_kg_by_species,
            *,
            temperature_C,
            pressure_bar,
        ):
            # The raw table's f=0.5 sample is the isolated engine outlier.
            # d-421's two neighbours instead give phi=0 there; the stable
            # endpoint at f=0.9995 reaches the 0.999 liquid target.
            return TabulatedLiquidFractionCurve(
                liner_composition_wt_pct=liner_composition_wt_pct,
                deposit_composition_wt_pct=deposit,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                points=((0.0, 0.0), (0.5, 1.0), (0.9995, 0.9995)),
            )

    sampler = UnstableSaturationSampler()
    pure_reference = liner_corrosion._tier_b_reference_profile(
        sampler, liner, pressure_bar=1.0
    )
    middle_reference = liner_corrosion._tier_b_reference_profile(
        sampler, unstable_nominal, pressure_bar=1.0
    )
    assert middle_reference["composition_continuity"]["status"] == "sensitive"
    assert middle_reference["engine_composition_sensitive"][
        "nominal_bracket_C"
    ] == [1375.0, 1400.0]
    assert middle_reference["engine_composition_sensitive"][
        "neighbour_brackets_C"
    ] == [[1400.0, 1425.0], [1400.0, 1425.0]]
    x_star = liner_corrosion._tier_b_saturated_deposit_fraction(
        sampler,
        liner,
        {"Na": 1.0},
        temperature_C=1400.0,
        pressure_bar=1.0,
        pure_reference_profile=pure_reference,
        invariant_table=None,
    )
    # Corrected interpolation is between (0.5, 0.0) and (0.9995, 0.9995):
    # x*=0.5+(0.999-0.0)*(0.9995-0.5)/(0.9995-0.0)~=0.9992501.
    # Using the unchecked raw point (0.5, 1.0) would put x* near 0.4995.
    assert x_star == pytest.approx(0.99925)

    first_neighbour, second_neighbour, _ = (
        liner_corrosion._tier_b_composition_neighbours(unstable_nominal)
    )

    class UnresolvedSaturationSampler(UnstableSaturationSampler):
        def __call__(self, composition, temperature_C, pressure_bar):
            if all(
                math.isclose(
                    float(composition.get(oxide, 0.0)), value,
                    rel_tol=0.0, abs_tol=1.0e-8,
                )
                for oxide, value in first_neighbour.items()
            ):
                bulk_low = 1550.0
            elif all(
                math.isclose(
                    float(composition.get(oxide, 0.0)), value,
                    rel_tol=0.0, abs_tol=1.0e-8,
                )
                for oxide, value in second_neighbour.items()
            ):
                bulk_low = 1600.0
            else:
                return super().__call__(composition, temperature_C, pressure_bar)
            if temperature_C < bulk_low:
                return 0.0
            if temperature_C < bulk_low + 25.0:
                return 0.044
            return 1.0

    unresolved_sampler = UnresolvedSaturationSampler()
    unresolved_reference = liner_corrosion._tier_b_reference_profile(
        unresolved_sampler, liner, pressure_bar=1.0
    )
    with pytest.raises(LinerCorrosionRefusal) as refused:
        liner_corrosion._tier_b_saturated_deposit_fraction(
            unresolved_sampler,
            liner,
            {"Na": 1.0},
            temperature_C=1400.0,
            pressure_bar=1.0,
            pure_reference_profile=unresolved_reference,
            invariant_table=None,
        )
    assert refused.value.reason == "tier_b_saturation_continuity_unresolved"


def test_tier_b_contaminated_profile_checks_composition_continuity():
    liner = {"CaO": 20.0, "Al2O3": 30.0, "SiO2": 50.0}
    contaminated = {"CaO": 18.0, "Al2O3": 27.0, "SiO2": 45.0, "Na2O": 10.0}
    samples: set[tuple[tuple[str, float], ...]] = set()

    class ContaminatedEndmemberDiscontinuity:
        def __call__(self, composition, temperature_C, _pressure_bar):
            normalized = tuple(
                sorted((oxide, round(float(wt_pct), 8)) for oxide, wt_pct in composition.items())
            )
            samples.add(normalized)
            is_nominal_contaminated = all(
                math.isclose(
                    float(composition.get(oxide, 0.0)), wt_pct,
                    rel_tol=0.0, abs_tol=1.0e-8,
                )
                for oxide, wt_pct in contaminated.items()
            )
            if is_nominal_contaminated and temperature_C == 500.0:
                return 0.01
            if temperature_C < 1025.0:
                return 0.0
            if temperature_C < 1550.0:
                return 0.0026 + (
                    0.01 if composition.get("Na2O", 0.0) > 5.0 else 0.0
                )
            if temperature_C < 1575.0:
                return 0.044
            return 1.0

    sampler = ContaminatedEndmemberDiscontinuity()
    pure_reference = liner_corrosion._tier_b_reference_profile(
        sampler, liner, pressure_bar=1.0, literature_melting_temperature_C=1553.0
    )
    activity = liner_corrosion._tier_b_melt_activity_rating(
        liner,
        contaminated,
        {"Na": 1.0},
        sampler=sampler,
        pressure_bar=1.0,
        margin_C=25.0,
        literature_melting_temperature_C=1553.0,
        pure_reference=pure_reference,
    )

    assert activity["contaminated_melt_bracket_C"] == [1000.0, 1025.0]
    assert "engine_composition_sensitive" in activity["flags"]
    assert activity["engine_composition_sensitive"]["nominal_bracket_C"] == [None, 500.0]
    assert activity["engine_composition_sensitive"]["neighbour_bracket_C"] == [
        1000.0,
        1025.0,
    ]
    assert len(samples) > 3


def test_tier_b_neighbour_disagreement_uses_tier_a_or_refuses():
    liner = {"CaO": 20.0, "Al2O3": 30.0, "SiO2": 50.0}
    first_neighbour, second_neighbour, _ = (
        liner_corrosion._tier_b_composition_neighbours(liner)
    )

    class NeighboursDisagree:
        def __call__(self, composition, temperature_C, _pressure_bar):
            if all(
                math.isclose(
                    composition.get(oxide, 0.0), value,
                    rel_tol=0.0, abs_tol=1.0e-8,
                )
                for oxide, value in first_neighbour.items()
            ):
                bulk_low = 1550.0
            elif all(
                math.isclose(
                    composition.get(oxide, 0.0), value,
                    rel_tol=0.0, abs_tol=1.0e-8,
                )
                for oxide, value in second_neighbour.items()
            ):
                bulk_low = 1600.0
            else:
                bulk_low = 1575.0
            if temperature_C < bulk_low:
                return 0.0026 if temperature_C > 1000.0 else 0.0
            if temperature_C < bulk_low + 25.0:
                return 0.044
            return 1.0

    table = {
        "invariants": [
            {
                "composition_bounds_wt_pct": {
                    "CaO": [0.0, 100.0],
                    "Al2O3": [0.0, 100.0],
                    "SiO2": [0.0, 100.0],
                    "Na2O": [0.0, 100.0],
                },
                "invariant_type": "congruent melting",
                "first_liquid_temperature_C": 1450.0,
            }
        ]
    }
    with pytest.raises(LinerCorrosionRefusal) as refused:
        liner_corrosion._tier_b_reference_profile(
            NeighboursDisagree(), liner, pressure_bar=1.0
        )
    assert refused.value.reason == "tier_b_composition_continuity_unresolved"

    with pytest.raises(LinerCorrosionRefusal) as aggressiveness_refused:
        liner_corrosion.species_aggressiveness_finite_difference(
            liner,
            "Na",
            sampler=NeighboursDisagree(),
            temperature_C=1200.0,
            pressure_bar=1.0,
        )
    assert aggressiveness_refused.value.reason == (
        "tier_b_composition_continuity_unresolved"
    )
    assert "Na aggressiveness baseline profile refused" in (
        aggressiveness_refused.value.detail
    )

    fallback = liner_corrosion._tier_b_reference_profile(
        NeighboursDisagree(),
        liner,
        pressure_bar=1.0,
        invariant_table=table,
    )
    assert fallback["tier_a_fallback"] is True
    assert fallback["tier_a_first_liquid_T_C"] == 1450.0
    assert "engine_composition_sensitive" in fallback["flags"]
    fallback_activity = liner_corrosion._tier_b_melt_activity_rating(
        liner,
        {"CaO": 19.8, "Al2O3": 29.7, "SiO2": 49.5, "Na2O": 1.0},
        {"Na": 1.0},
        sampler=NeighboursDisagree(),
        pressure_bar=1.0,
        margin_C=25.0,
        literature_melting_temperature_C=None,
        pure_reference=fallback,
        service_temperature_C=1500.0,
        invariant_table=table,
    )
    assert fallback_activity["tier_a_fallback"] is True
    assert fallback_activity["rating_temperature_C"] == 1475.0
    assert fallback_activity["criterion_signal_at_service_temperature"] == 1.0


def test_tier_b_reference_refuses_a_scan_that_stops_at_minor_phase_melting():
    class TruncatedMinorPhaseProfile:
        def __call__(self, _composition, temperature_C, _pressure_bar):
            return 0.0 if temperature_C <= 1000.0 else 0.0026

    with pytest.raises(LinerCorrosionRefusal) as refused:
        liner_corrosion._tier_b_reference_profile(
            TruncatedMinorPhaseProfile(),
            {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
            pressure_bar=1.0,
        )

    assert refused.value.reason == "liner_reference_unresolved"
    assert "500-2200 C" in refused.value.detail
    assert "f_liq=0.0026" in refused.value.detail


def test_tier_b_reference_extends_until_bulk_melt_reaches_liquid_threshold():
    class ExtendedBulkProfile:
        def __call__(self, _composition, temperature_C, _pressure_bar):
            if temperature_C <= 1000.0:
                return 0.0
            if temperature_C < 1650.0:
                return 0.0026
            if temperature_C < 1675.0:
                return 0.01
            return 1.0

    reference = liner_corrosion._tier_b_reference_profile(
        ExtendedBulkProfile(),
        {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
        pressure_bar=1.0,
    )

    assert reference["scan_temperature_C"][-1] == 1675.0
    assert reference["bulk_melt_bracket_C"] == [1650.0, 1675.0]
    assert reference["composition_continuity"]["status"] == "passed"
    assert "engine_composition_sensitive" not in reference["flags"]


def test_liquidus_search_defaults_pin_the_existing_2200_c_ceiling():
    from inspect import signature

    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    from simulator.melt_backend.liquidus import find_liquidus_solidus_by_fraction
    from simulator.melt_backend.magemin import MAGEMinBackend

    defaults = (
        signature(find_liquidus_solidus_by_fraction).parameters["max_T_C"].default,
        signature(MAGEMinBackend.find_liquidus_solidus).parameters[
            "max_T_C"
        ].default,
        signature(AlphaMELTSBackend.find_liquidus_solidus).parameters[
            "max_T_C"
        ].default,
    )

    assert defaults == (2200.0, 2200.0, 2200.0)


def test_tier_b_rating_uses_excess_melt_and_the_lower_temperature_bracket():
    class ProfileSampler:
        flags = ["engine_phase_closure_unverified"]

        def __call__(self, composition, temperature_C, _pressure_bar):
            if temperature_C < 1025.0:
                pure = 0.0
            elif temperature_C < 1550.0:
                pure = 0.0026
            elif temperature_C < 1575.0:
                pure = 0.044
            else:
                pure = 1.0
            excess = (
                0.01
                if composition.get("Na2O", 0.0) > 5.0
                and 1100.0 <= temperature_C < 1550.0
                else 0.0
            )
            return min(1.0, pure + excess)

        def sample_diagnostics(self, _composition, temperature_C, _pressure_bar):
            return {"phases": ["fsp", "cpx"] if temperature_C >= 1025.0 else []}

    sampler = ProfileSampler()
    liner = {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0}
    reference = liner_corrosion._tier_b_reference_profile(
        sampler,
        liner,
        pressure_bar=1.0,
    )
    pure = liner_corrosion._tier_b_melt_activity_rating(
        liner,
        liner,
        {},
        sampler=sampler,
        pressure_bar=1.0,
        margin_C=25.0,
        literature_melting_temperature_C=1553.0,
        pure_reference=reference,
    )
    contaminated = {"SiO2": 45.0, "Al2O3": 27.0, "CaO": 18.0, "Na2O": 10.0}
    rated = liner_corrosion._tier_b_melt_activity_rating(
        liner,
        contaminated,
        {"Na": 1.0},
        sampler=sampler,
        pressure_bar=1.0,
        margin_C=25.0,
        literature_melting_temperature_C=1553.0,
        pure_reference=reference,
    )

    assert pure["rating_temperature_C"] == 1528.0
    assert rated["contaminated_melt_bracket_C"] == [1075.0, 1100.0]
    # T_lit + (T_contaminated,lo - T_bulk,pure,lo) - margin
    # = 1553 + (1075 - 1550) - 25 = 1053 C; the upper cap is 1528 C.
    assert rated["rating_temperature_C"] == 1053.0
    assert rated["excess_liquid_fraction_at_upper_bracket"] == pytest.approx(
        0.01
    )
    assert rated["composition_continuity"]["status"] == "passed"
    assert "engine_composition_sensitive" not in rated["flags"]
    assert "deposit_melt_below_resolution" not in rated["flags"]


def test_tier_b_onset_at_first_scan_point_is_an_unresolved_lower_boundary():
    class BoundarySampler:
        def __call__(self, composition, temperature_C, _pressure_bar):
            if temperature_C < 1550.0:
                pure = 0.0
            elif temperature_C < 1575.0:
                pure = 0.044
            else:
                pure = 1.0
            if composition.get("Na2O", 0.0) > 0.0 and temperature_C == 500.0:
                return pure + 0.01
            return pure

    sampler = BoundarySampler()
    liner = {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0}
    reference = liner_corrosion._tier_b_reference_profile(
        sampler,
        liner,
        pressure_bar=1.0,
        literature_melting_temperature_C=1553.0,
    )
    activity = liner_corrosion._tier_b_melt_activity_rating(
        liner,
        {"SiO2": 49.0, "Al2O3": 30.0, "CaO": 20.0, "Na2O": 1.0},
        {"Na": 1.0},
        sampler=sampler,
        pressure_bar=1.0,
        margin_C=25.0,
        literature_melting_temperature_C=1553.0,
        pure_reference=reference,
        service_temperature_C=750.0,
    )

    assert activity["contaminated_melt_bracket_C"] == [None, 500.0]
    assert activity["rating_temperature_C"] is None
    assert "onset_below_scan_range" in activity["flags"]
    assert liner_corrosion._tier_b_onset_at_service_temperature(activity, 750.0)
    assert liner_corrosion._tier_b_onset_at_service_temperature(activity, 450.0)


def test_tier_b_scans_the_pure_bulk_lower_boundary_for_contaminated_onset():
    class BulkBoundarySampler:
        def __call__(self, composition, temperature_C, _pressure_bar):
            if temperature_C < 1550.0:
                pure = 0.0
            elif temperature_C < 1575.0:
                pure = 0.044
            else:
                pure = 1.0
            if composition.get("Na2O", 0.0) > 0.0 and temperature_C == 1550.0:
                return pure + 0.01
            return pure

    sampler = BulkBoundarySampler()
    liner = {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0}
    reference = liner_corrosion._tier_b_reference_profile(
        sampler,
        liner,
        pressure_bar=1.0,
        literature_melting_temperature_C=1553.0,
    )
    activity = liner_corrosion._tier_b_melt_activity_rating(
        liner,
        {"SiO2": 49.0, "Al2O3": 30.0, "CaO": 20.0, "Na2O": 1.0},
        {"Na": 1.0},
        sampler=sampler,
        pressure_bar=1.0,
        margin_C=25.0,
        literature_melting_temperature_C=1553.0,
        pure_reference=reference,
    )

    assert reference["bulk_melt_bracket_C"] == [1550.0, 1575.0]
    assert activity["contaminated_melt_bracket_C"] == [1525.0, 1550.0]
    # Independent d-419 rating arithmetic uses the lower contaminated endpoint:
    # 1553 + (1525 - 1550) - 25 = 1503 C, below the 1528 C pure cap.
    assert activity["rating_temperature_C"] == 1503.0


def test_rankin_end_member_melting_points_are_excluded_from_compound_rows():
    records = [
        row
        for row in load_liner_corrosion_invariants()
        if row["source_id"] == "rankin-wright-1915-ajs-cao-al2o3-sio2"
    ]
    table_a_ids = {
        row["observation_id"]
        for row in records
        if "_tA_" in row["observation_id"]
    }

    # Rankin & Wright Table A rows 1-3 are pure-component melting references
    # (CaO, Al2O3, SiO2), not the 12 multicomponent compound transitions.
    assert not table_a_ids.intersection(
        {
            "rankin_wright_1915_tA_r1",
            "rankin_wright_1915_tA_r2",
            "rankin_wright_1915_tA_r3",
        }
    )
    assert len(table_a_ids) == 12


def test_mixing_closed_form_and_reacted_depth_sensitivity():
    # Five kg Na becomes 5 * M(Na2O)/(2 M(Na)) kg Na2O under the declared
    # oxidation-state assumption. The liner layer weighs rho*A*h = 2 kg.
    na_oxide_kg = 5.0 * (2 * 22.98976928 + 15.999) / (2 * 22.98976928)
    total_kg = 2.0 + na_oxide_kg
    fractions = contamination_mixing_fractions(
        {"Na": 5.0},
        density_kg_m3=2.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
    )
    mixed = contaminated_interface_composition(
        {"SiO2": 60.0, "Al2O3": 40.0},
        {"Na": 5.0},
        density_kg_m3=2.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
    )
    assert fractions["Na"] == pytest.approx(na_oxide_kg / total_kg)
    assert mixed["mixing_fraction_by_species"]["Na"] == pytest.approx(
        na_oxide_kg / total_kg
    )
    assert mixed["composition_wt_pct"]["Na2O"] / 100.0 == pytest.approx(
        mixed["mixing_fraction_by_species"]["Na"]
    )
    # Independent closed form: 5 kg Na -> M(Na2O)/(2 M(Na)) kg Na2O;
    # f = M/(rho*A*h + sum M), with rho*A*h = 2 kg.
    assert mixed["mixing_fraction_by_species"]["Na"] == pytest.approx(
        na_oxide_kg / (2.0 + na_oxide_kg)
    )
    assert mixed["composition_wt_pct"]["Na2O"] == pytest.approx(
        100.0 * na_oxide_kg / total_kg
    )
    assert mixed["composition_wt_pct"]["SiO2"] == pytest.approx(
        100.0 * 1.2 / total_kg
    )
    assert reacted_layer_depths(0.03) == pytest.approx((0.003, 0.03, 0.3))


def test_post_onset_dissolution_ratio_closed_form():
    assert post_onset_dissolution_ratio(0.5) == 1.0


def _phi_from_na2o(composition, temperature_C, _pressure_bar):
    if temperature_C < 1550.0:
        pure = 0.0
    elif temperature_C < 1575.0:
        pure = 0.05
    else:
        pure = 1.0
    deposit = min(1.0, max(0.0, (composition.get("Na2O", 0.0) - 5.0) / 20.0))
    return max(pure, deposit)


def _recurrence(*, initial_na=0.0, h=1.0, inert_na=0.2, reactive_na=0.5):
    return corrosion_campaign_recurrence(
        {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
        {"Na": initial_na},
        {"Na": inert_na},
        {"Na": reactive_na},
        density_kg_m3=1.0,
        area_m2=1.0,
        reacted_layer_depth_m=h,
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
        margin_C=0.0,
        horizon=100,
        sampler=_phi_from_na2o,
    )


def test_onset_is_monotone_in_deposit_and_reacted_depth_and_bounds_ordered():
    deposits = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)
    depths = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
    for depth in depths:
        counts = [
            _recurrence(initial_na=initial_na, h=depth)[
                "campaigns_to_runaway"
            ]
            for initial_na in deposits
        ]
        assert all(
            left[0] >= right[0]
            for left, right in zip(counts, counts[1:])
        )
        assert all(low <= high for low, high in counts)
    for initial_na in deposits:
        counts = [
            _recurrence(initial_na=initial_na, h=depth)[
                "campaigns_to_runaway"
            ][0]
            for depth in depths
        ]
        assert all(left <= right for left, right in zip(counts, counts[1:]))


def test_recurrence_reduces_to_linear_when_sticking_branches_match():
    result = _recurrence(inert_na=0.5, reactive_na=0.5)
    # The phi fixture turns on above 5 wt% Na2O. For rho*A*h=1 kg, that
    # threshold needs 1*(0.05/0.95)/(M(Na2O)/(2*M(Na))) kg Na, below one
    # campaign's 0.5 kg D. Thus N=1 and N*D=0.5 kg by direct arithmetic.
    assert result["campaigns_to_runaway"] == [1, 1]
    assert result["coupled_campaigns_to_runaway"] == 1
    assert result["branch_end_deposit_kg_by_species"] == {
        "reactive_upper_bound": {"Na": 0.5},
        "inert": {"Na": 0.5},
        "coupled": {"Na": 0.5},
    }


def test_reactive_branch_refusal_keeps_the_inert_campaign_bound():
    result = corrosion_campaign_recurrence(
        {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
        {},
        {"Na": 0.2},
        None,
        density_kg_m3=1.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
        margin_C=0.0,
        horizon=100,
        sampler=_phi_from_na2o,
    )

    assert result["campaigns_to_runaway"] == [None, 1]
    assert result["reactive_branch_refusal"]["reason"] == (
        "reactive_deposit_branch_missing"
    )
    assert result["branch_end_deposit_kg_by_species"]["inert"] == {"Na": 0.2}


def test_reactive_sampling_refusal_keeps_completed_inert_bound():
    def sampler(composition, temperature_C, _pressure_bar):
        na2o_wt_pct = composition.get("Na2O", 0.0)
        if na2o_wt_pct > 30.0:
            raise LinerCorrosionRefusal(
                "liner_first_melt_engine_out_of_domain", "reactive blend leaves domain"
            )
        pure = 0.0 if temperature_C < 1550.0 else (
            0.05 if temperature_C < 1575.0 else 1.0
        )
        return max(pure, 1.0 if na2o_wt_pct >= 15.0 else 0.0)

    result = corrosion_campaign_recurrence(
        {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
        {},
        {"Na": 0.2},
        {"Na": 0.5},
        density_kg_m3=1.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
        margin_C=0.0,
        horizon=100,
        sampler=sampler,
    )

    assert result["campaigns_to_runaway"] == [None, 1]
    assert result["reactive_branch_refusal"]["reason"] == (
        "liner_first_melt_engine_out_of_domain"
    )
    assert result["branch_end_deposit_kg_by_species"]["inert"] == {"Na": 0.2}


def test_campaign_interval_orders_onset_counts_not_deposit_rates():
    def sodium_liquid_signal(composition, temperature_C, _pressure_bar):
        pure = 0.0 if temperature_C < 1550.0 else (
            0.05 if temperature_C < 1575.0 else 1.0
        )
        return max(pure, 1.0 if composition.get("Na2O", 0.0) >= 15.0 else 0.0)

    result = corrosion_campaign_recurrence(
        {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
        {},
        {"Na": 0.2},
        {"Na": 0.2, "Fe": 2.0},
        density_kg_m3=1.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
        margin_C=0.0,
        horizon=100,
        sampler=sodium_liquid_signal,
    )

    # Inert Na reaches 15 wt% Na2O after one campaign. The reactive mix keeps
    # Na at the same rate but adds FeO, so Na2O stays below 15 wt% through the
    # horizon. The campaign interval must follow onset, not total deposit mass.
    assert result["onset_campaigns_by_branch"] == {
        "inert_onset_campaign": 1,
        "reactive_onset_campaign": "> 100",
    }
    assert result["campaigns_to_runaway"] == [1, "> 100"]
    assert result["campaign_interval_branch_labels"] == {
        "lower": "inert",
        "upper": "reactive_upper_bound",
    }


def test_multispecies_campaign_interval_matches_each_branch_projection():
    def sodium_liquid_signal(composition, temperature_C, _pressure_bar):
        pure = 0.0 if temperature_C < 1550.0 else (
            0.05 if temperature_C < 1575.0 else 1.0
        )
        return max(pure, 1.0 if composition.get("Na2O", 0.0) >= 15.0 else 0.0)

    pairs = (
        ({"Na": 0.2}, {"Na": 0.2, "Fe": 2.0}),
        ({"Na": 0.2, "Fe": 0.3}, {"Na": 0.4, "Fe": 2.0}),
        ({"Na": 0.1, "K": 0.2}, {"Na": 0.2, "K": 0.7, "Mg": 0.4}),
    )
    for inert, reactive in pairs:
        combined = corrosion_campaign_recurrence(
            {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
            {},
            inert,
            reactive,
            density_kg_m3=1.0,
            area_m2=1.0,
            reacted_layer_depth_m=1.0,
            temperature_C=1500.0,
            pressure_bar=1.0e-6,
            margin_C=0.0,
            horizon=8,
            sampler=sodium_liquid_signal,
        )
        inert_only = corrosion_campaign_recurrence(
            {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
            {},
            inert,
            inert,
            density_kg_m3=1.0,
            area_m2=1.0,
            reacted_layer_depth_m=1.0,
            temperature_C=1500.0,
            pressure_bar=1.0e-6,
            margin_C=0.0,
            horizon=8,
            sampler=sodium_liquid_signal,
        )
        reactive_only = corrosion_campaign_recurrence(
            {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
            {},
            reactive,
            reactive,
            density_kg_m3=1.0,
            area_m2=1.0,
            reacted_layer_depth_m=1.0,
            temperature_C=1500.0,
            pressure_bar=1.0e-6,
            margin_C=0.0,
            horizon=8,
            sampler=sodium_liquid_signal,
        )

        branch_counts = combined["onset_campaigns_by_branch"]
        assert branch_counts["inert_onset_campaign"] == (
            inert_only["onset_campaigns_by_branch"]["inert_onset_campaign"]
        )
        assert branch_counts["reactive_onset_campaign"] == (
            reactive_only["onset_campaigns_by_branch"]["inert_onset_campaign"]
        )
        order_key = lambda count: 9 if count == "> 8" else count
        low = min(
            branch_counts["inert_onset_campaign"],
            branch_counts["reactive_onset_campaign"],
            key=order_key,
        )
        high = max(
            branch_counts["inert_onset_campaign"],
            branch_counts["reactive_onset_campaign"],
            key=order_key,
        )
        assert combined["campaigns_to_runaway"] == [low, high]


def test_zero_campaign_deposition_reports_beyond_horizon_not_infinity():
    result = _recurrence(inert_na=0.0, reactive_na=0.0)

    assert result["campaigns_to_runaway"] == ["> 100", "> 100"]
    assert "infinite" not in str(result["campaigns_to_runaway"])


def test_tier_a_uses_invariant_table_and_refuses_uncovered_fields():
    table = {
        "schema": "liner-invariant-table-v1",
        "invariants": [
            {
                "first_liquid_temperature_C": 1553.0,
                "composition_bounds_wt_pct": {
                    "CaO": [19.0, 21.0],
                    "Al2O3": [35.0, 38.0],
                    "SiO2": [42.0, 45.0],
                },
            }
        ],
    }
    composition = {"CaO": 20.0, "Al2O3": 36.0, "SiO2": 44.0}
    assert tier_a_first_liquid_onset(
        table,
        composition,
        service_temperature_C=1600.0,
        margin_C=0.0,
    )["onset"]
    with pytest.raises(LinerCorrosionRefusal) as refused:
        tier_a_first_liquid_onset(
            {"invariants": []},
            composition,
            service_temperature_C=1600.0,
            margin_C=0.0,
        )
    assert refused.value.reason == "liner_invariant_table_missing"


def test_tier_a_uses_bulk_compatibility_invariant_and_congruent_compound():
    table = load_interface_invariant_table(
        "source-backed",
        catalog={
            "source-backed": {
                "interface_chemistry": {
                    "composition": {"CaO": 59.0, "Al2O3": 41.0},
                    "citation": "Morey 1964, Table 6",
                    "invariant_table": {
                        "loader": "liner_corrosion_source_extracts",
                        "subsystems": ["CaO-Al2O3"],
                    },
                }
            }
        },
    )
    # Source pin: Morey 1964, physical p. 25 (L14), Table 6 row 2 (1395 C).
    assert first_liquid_tier_a(
        table,
        {"CaO": 59.0, "Al2O3": 41.0},
    ) == 1395.0
    assert tier_a_first_liquid_onset(
        table,
        {"CaO": 59.0, "Al2O3": 41.0},
        service_temperature_C=1500.0,
        margin_C=0.0,
    )["onset"]

    # Source pin: Morey 1964, physical p. 25 (L14), Table 6 row 5 (1600 C).
    assert first_liquid_tier_a(
        table,
        {"CaO": 35.49, "Al2O3": 64.51},
    ) == 1600.0

    # Source pin: Morey 1964, physical p. 25 (L14), Table 6 row 1 (1535 C).
    cao_mass = parse_formula("CaO").molar_mass_g_per_mol()
    alumina_mass = parse_formula("Al2O3").molar_mass_g_per_mol()
    c3a_total = 3.0 * cao_mass + alumina_mass
    c3a = {
        "CaO": 100.0 * 3.0 * cao_mass / c3a_total,
        "Al2O3": 100.0 * alumina_mass / c3a_total,
    }
    assert first_liquid_tier_a(table, c3a) == 1535.0

    # Table 6 row 2 is the C12A7-C3A eutectic at 1395 C. C3A is CaO 62.27
    # wt% by the formula owner, so 62/38 is not the pure phase at the cited
    # 0.01 wt% source resolution; it lies between 48.54 wt% C12A7 and 62.27
    # wt% C3A. Caller spelling must not alter that physical classification.
    for bulk in (
        {"CaO": 62, "Al2O3": 38},
        {"CaO": 62.0, "Al2O3": 38.0},
        {"CaO": 62.00, "Al2O3": 38.00},
    ):
        assert first_liquid_tier_a(table, bulk) == 1395.0


def test_zero_mass_deposit_preserves_tier_a_subsystem_membership():
    table = load_interface_invariant_table(
        "source-backed",
        catalog={
            "source-backed": {
                "interface_chemistry": {
                    "composition": {"CaO": 59.0, "Al2O3": 41.0},
                    "citation": "Morey 1964, Table 6",
                    "invariant_table": {
                        "loader": "liner_corrosion_source_extracts",
                        "subsystems": ["CaO-Al2O3"],
                    },
                }
            }
        },
    )
    liner = {"CaO": 59.0, "Al2O3": 41.0}
    no_deposit = contaminated_interface_composition(
        liner,
        {},
        density_kg_m3=1.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
    )["composition_wt_pct"]
    zero_na_deposit = contaminated_interface_composition(
        liner,
        {"Na": 0.0},
        density_kg_m3=1.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
    )["composition_wt_pct"]

    assert zero_na_deposit == no_deposit
    assert first_liquid_tier_a(table, no_deposit) == 1395.0
    assert first_liquid_tier_a(table, zero_na_deposit) == 1395.0


def test_tier_a_anorthite_row_supplies_cited_pure_melting_temperature():
    table = load_interface_invariant_table(
        "anorthite-liner",
        catalog={
            "anorthite-liner": {
                "interface_chemistry": {
                    "composition": {
                        "CaO": 20.16,
                        "Al2O3": 36.65,
                        "SiO2": 43.19,
                    },
                    "citation": "Morey 1964, Table 26",
                    "criterion_tier": "B",
                    "invariant_table": {
                        "loader": "liner_corrosion_source_extracts",
                        "subsystems": ["CaO-Al2O3-SiO2"],
                    },
                }
            }
        },
    )

    # Formula-derived CaO.Al2O3.2SiO2 matches Morey Table 26 row 6's printed
    # oxide values to their half-digit precision (physical p. 82 / L71).
    anorthite = _formula_derived_anorthite()
    reference = liner_corrosion._tier_a_pure_phase_melting_reference(table, anorthite)

    assert reference["temperature_C"] == 1553.0
    assert first_liquid_tier_a(table, anorthite) == 1553.0
    assert reference["citation"]["source_id"] == (
        "morey-1964-usgs-pp440l-phase-equilibria"
    )
    assert reference["citation"]["observation_id"] == "morey1964_t26_r6"
    assert reference["citation"]["locator"]["published_page"] == "L71"


def test_tier_a_refuses_when_cited_transition_lacks_solid_phase_data():
    with pytest.raises(LinerCorrosionRefusal) as refused:
        first_liquid_tier_a(
            {
                "invariants": [
                    {
                        "subsystem": "CaO-Al2O3",
                        "phases": ["liquid"],
                        "liquid_composition": {"CaO": 50.0, "Al2O3": 50.0},
                        "invariant_type": "eutectic",
                        "T_invariant_C": 1400.0,
                    }
                ]
            },
            {"CaO": 55.0, "Al2O3": 45.0},
        )

    assert refused.value.reason == "tier_a_compatibility_phases_missing"


def test_tier_a_fe_aluminate_gap_is_a_typed_refusal_that_names_the_gap():
    with pytest.raises(LinerCorrosionRefusal) as refused:
        tier_a_first_liquid_onset(
            {
                "invariants": [],
                "acquisition_gaps": {
                    "FeO-CaO-Al2O3": "no source-located values are digitized"
                },
            },
            {"FeO": 20.0, "CaO": 40.0, "Al2O3": 40.0},
            service_temperature_C=1500.0,
            margin_C=0.0,
        )
    assert refused.value.reason == "liner_tier_a_acquisition_gap"
    assert "FeO-CaO-Al2O3" in refused.value.detail


def test_production_liner_metadata_path_reports_bounded_campaign_interval(monkeypatch):
    from simulator import furnace_materials, liner_corrosion
    from simulator.condensation import VAPOR_PRESSURE_DATA
    from simulator.state import PipeSegment

    catalog = {
        "fixture-liner": {
            "interface_chemistry": {
                "composition": {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
                "citation": "test fixture composition",
                "criterion_tier": "B",
                "density_kg_m3": 1000.0,
                "reacted_layer_depth_m": 0.001,
                "margin_C": 25.0,
                "horizon_campaigns": 10,
            }
        }
    }
    monkeypatch.setattr(furnace_materials, "load_furnace_materials", lambda: catalog)

    class FixtureSampler(liner_corrosion.CurveLiquidFractionSampler):
        saturates = True
        flags = ["engine_phase_closure_unverified"]
        curve_temperatures = []

        def __call__(self, composition, temperature_C, _pressure_bar):
            if temperature_C < 1550.0:
                pure = 0.0
            elif temperature_C < 1575.0:
                pure = 0.044
            else:
                pure = 1.0
            deposit = 1.0 if composition.get("Na2O", 0.0) >= 0.1 else 0.0
            return max(pure, deposit)

        def curve(
            self,
            liner_composition_wt_pct,
            deposit_kg_by_species,
            *,
            temperature_C,
            pressure_bar,
        ):
            self.curve_temperatures.append(temperature_C)
            if temperature_C < 1550.0:
                pure = 0.0
            elif temperature_C < 1575.0:
                pure = 0.044
            else:
                pure = 1.0
            points = (
                (
                    (0.0, pure),
                    (0.001, pure),
                    (0.01, 1.0),
                    (1.0, 1.0),
                )
                if self.saturates
                else ((0.0, 0.0), (0.001, 0.0), (0.5, 0.5))
            )
            return TabulatedLiquidFractionCurve(
                liner_composition_wt_pct=liner_composition_wt_pct,
                deposit_composition_wt_pct=liner_corrosion.deposit_oxide_composition(
                    deposit_kg_by_species
                ),
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                points=points,
            )

        def close(self):
            pass

    assert isinstance(FixtureSampler(), liner_corrosion.CurveLiquidFractionSampler)
    monkeypatch.setattr(liner_corrosion, "MageminLinerSampler", FixtureSampler)
    segment = PipeSegment(
        name="liner-wall",
        upstream_stage="stage_0",
        downstream_stage="stage_1",
        wall_temperature_C=1500.0,
        length_m=1.0,
        inner_diameter_m=0.12,
    )
    model = SimpleNamespace(
        pipe_segments=(segment,),
        operating_history=({"pipe_segment_temperatures_C": {"liner-wall": 1500.0}},),
        overhead_pressure_mbar=10.0,
        materials={},
        vapor_pressure_data=VAPOR_PRESSURE_DATA,
        species_formula_registry=None,
        wall_temperature_input_refusals={},
        gas_temperature_C=1500.0,
        pipe_diameter_m=0.12,
        regime_factor=1.0,
        stir_factor=1.0,
        radial_stir_factor=1.0,
        carrier_gas="N2",
        condensation_temperatures_C={"Na": 800.0},
    )
    shadow = {
        "liner-wall": {
            "Na": {
                "available_supply_mol_s": 1.0e-5,
                "species_partial_pressure_pa": 100.0,
                "surface": {"wall_temperature_K": 1773.15},
            }
        }
    }
    trace = SimpleNamespace(
        wall_deposit_by_segment_species_kg={("liner-wall", "Na"): 1.0e-4},
        snapshots=(SimpleNamespace(duration_h=1.0, wall_deposition_rate_shadow_candidate=shadow),),
    )
    simulator = SimpleNamespace(condensation_model=model, melt=SimpleNamespace(temperature_C=1500.0))
    run_metadata = {"furnace_material_id": "fixture-liner"}

    state = attach_liner_state_metadata(
        run_metadata, "fixture-liner", simulator, trace
    )

    assert state["schema"] == "liner-state-v1"
    segment_state = state["segments"]["liner-wall"]
    lo, hi = segment_state["campaigns_to_runaway"]
    assert isinstance(lo, int) and isinstance(hi, int)
    assert lo <= hi
    # The fixture onset is 0.1 wt% Na2O. For a new liner the reacted mass is
    # 1000*pi*0.12*1.0*0.001 kg, so the closed-form Na threshold is
    # m0*(0.001/0.999)/(M(Na2O)/(2*M(Na))) ~= 2.8e-4 kg. With the measured
    # inert increment 1e-4 kg/campaign, the independent minimum is N=3.
    assert hi == 3
    assert run_metadata["liner_state"] == state
    assert segment_state["deposit_inert_kg_by_species"] == {"Na": 1.0e-4}
    assert segment_state["deposit_reactive_kg_by_species"]["Na"] >= 1.0e-4
    # phi reaches 1-epsilon on the line from (0.001, 0) to (0.01, 1):
    # x*=0.001+0.009*0.999=0.009991, so the oxide-basis ratio is
    # (1-x*)/x*=99.09008107296567. The exported raw-Na basis also multiplies
    # by M(Na2O)/(2*M(Na)), the kg oxide equivalent per kg deposited Na.
    na_to_na2o = parse_formula("Na2O").molar_mass_g_per_mol() / (
        2.0 * parse_formula("Na").molar_mass_g_per_mol()
    )
    assert segment_state["liner_consumed_kg_per_kg_deposit"] == pytest.approx(
        99.09008107296567 * na_to_na2o
    )
    accumulated_state = liner_corrosion.build_liner_state_for_run(
        "fixture-liner",
        simulator,
        trace,
        initial_deposit_kg_by_segment_species={"liner-wall": {"Na": 0.1}},
    )
    accumulated_segment = accumulated_state["segments"]["liner-wall"]
    # Prior Na is already over the fixture onset. Starting the recurrence from
    # that carried state must report campaign zero, not a new-liner forecast.
    assert accumulated_segment["campaigns_to_runaway"] == [0, 0]
    assert accumulated_segment["onset"] is True
    assert accumulated_segment["deposit_kg_by_species"]["Na"] == pytest.approx(
        0.1001
    )
    assert accumulated_segment[
        "cumulative_deposit_oxide_equivalent_kg_by_oxide"
    ]["Na2O"] == pytest.approx(0.1001 * na_to_na2o)
    assert 1500.0 in FixtureSampler.curve_temperatures
    assert "engine_phase_closure_unverified" in segment_state["flags"]

    FixtureSampler.saturates = False
    missing_saturation_metadata = {}
    missing_saturation = attach_liner_state_metadata(
        missing_saturation_metadata,
        "fixture-liner",
        simulator,
        trace,
    )
    missing_segment = missing_saturation["segments"]["liner-wall"]
    assert missing_segment["liner_consumed_kg_per_kg_deposit"] is None
    assert any(
        refusal["reason"] == "tier_b_saturated_liquid_fraction_not_reached"
        for refusal in missing_segment["refusals"]
    )


def test_production_anorthite_metadata_uses_literature_temperature_anchor(monkeypatch):
    from simulator import furnace_materials, liner_corrosion
    from simulator.state import PipeSegment

    catalog = {
        "anorthite-liner": {
            "interface_chemistry": {
                "composition": {"CaO": 20.16, "Al2O3": 36.65, "SiO2": 43.19},
                "citation": "Morey 1964, Table 26, row 6",
                "criterion_tier": "B",
                "invariant_table": {
                    "loader": "liner_corrosion_source_extracts",
                    "subsystems": ["CaO-Al2O3-SiO2"],
                },
                "density_kg_m3": 1000.0,
                "reacted_layer_depth_m": 0.001,
                "margin_C": 25.0,
                "horizon_campaigns": 3,
                "porosity_fraction": 0.0,
            }
        }
    }
    monkeypatch.setattr(furnace_materials, "load_furnace_materials", lambda: catalog)

    class SourceAnchoredSampler(liner_corrosion.CurveLiquidFractionSampler):
        flags = []

        def __call__(self, _composition, temperature_C, _pressure_bar):
            if temperature_C < 1550.0:
                return 0.0
            if temperature_C < 1575.0:
                return 0.044
            return 1.0

        def curve(self, *_args, **_kwargs):
            raise AssertionError("a deposit-free anchor needs no phi(f) curve")

        def close(self):
            pass

    monkeypatch.setattr(liner_corrosion, "MageminLinerSampler", SourceAnchoredSampler)
    segment = PipeSegment(
        name="anorthite-wall",
        upstream_stage="stage_0",
        downstream_stage="stage_1",
        wall_temperature_C=1500.0,
        length_m=1.0,
        inner_diameter_m=0.12,
    )
    model = SimpleNamespace(
        pipe_segments=(segment,),
        operating_history=({"pipe_segment_temperatures_C": {"anorthite-wall": 1500.0}},),
        overhead_pressure_mbar=10.0,
    )
    trace = SimpleNamespace(wall_deposit_by_segment_species_kg={}, snapshots=())
    simulator = SimpleNamespace(condensation_model=model)
    metadata = {"furnace_material_id": "anorthite-liner"}

    state = attach_liner_state_metadata(
        metadata,
        "anorthite-liner",
        simulator,
        trace,
    )

    segment_state = state["segments"]["anorthite-wall"]
    assert metadata["liner_state"] is state
    assert segment_state["rating_temperature_C"] == 1528.0
    activity = segment_state["melt_activity"]
    assert activity["rating_temperature_C"] == 1528.0
    assert activity["literature_reference_citation"]["observation_id"] == (
        "morey1964_t26_r6"
    )
    assert activity["bulk_melt_residual_vs_literature_C"] == [-3.0, 22.0]


def test_tier_a_invariant_signal_runs_the_campaign_recurrence_without_engine_pressure():
    table = {
        "invariants": [
            {
                "first_liquid_temperature_C": 2000.0,
                "composition_bounds_wt_pct": {"Na2O": [0.0, 9.9]},
            },
            {
                "first_liquid_temperature_C": 1400.0,
                "composition_bounds_wt_pct": {"Na2O": [9.9, 100.0]},
            },
        ]
    }
    result = corrosion_campaign_recurrence(
        {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
        {},
        {"Na": 0.1},
        {"Na": 0.2},
        density_kg_m3=1.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
        temperature_C=1500.0,
        pressure_bar=None,
        margin_C=0.0,
        horizon=5,
        sampler=TierAInvariantSampler(table),
    )
    assert result["campaigns_to_runaway"] == [1, 1]
    assert result["criterion_signal_at_horizon_or_onset"]["reactive_upper_bound"] == 1.0


def test_tier_a_dissolution_uses_the_matched_source_liquid_composition():
    liner = {"CaO": 60.0, "Al2O3": 40.0}
    deposits = {"Na": 0.01}
    mixed = contaminated_interface_composition(
        liner,
        deposits,
        density_kg_m3=1.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
    )
    table = {
        "invariants": [
            {
                "source_id": "test-source",
                "subsystem": "Na2O-CaO-Al2O3",
                "T_invariant_C": 1500.0,
                "phases": ["liquid"],
                "liquid_composition": mixed["composition_wt_pct"],
                "invariant_type": "liquidus",
                "composition_bounds_wt_pct": {
                    oxide: [wt_pct - 0.1, wt_pct + 0.1]
                    for oxide, wt_pct in mixed["composition_wt_pct"].items()
                },
                "first_liquid_temperature_C": 1400.0,
            }
        ]
    }
    chemistry = {
        "composition": liner,
        "citation": "test-source",
        "criterion_tier": "A",
        "invariant_table": table,
        "density_kg_m3": 1.0,
        "reacted_layer_depth_m": 1.0,
        "margin_C": 0.0,
        "horizon_campaigns": 2,
        "porosity_fraction": 0.0,
    }
    def build_state(x_star=None):
        return build_liner_state_segment(
            chemistry,
            deposits,
            area_m2=1.0,
            service_temperature_C=1500.0,
            pressure_bar=None,
            sampler=None,
            deposit_inert_kg_by_species=deposits,
            deposit_reactive_kg_by_species=deposits,
            x_star=x_star,
        )

    state = build_state()

    # The liner layer is 1 kg. 0.01 kg Na gives
    # 0.01*M(Na2O)/(2*M(Na)) = 0.013479591248860067 kg oxide, hence
    # x*=M/(1+M) and (1-x*)/x*=1/M=74.18622579409204 kg liner/kg oxide.
    # Since 1 kg Na produces 1.3479591248860067 kg Na2O, this is exactly
    # 100 kg liner/kg raw Na. With the independent x*=0.5 pin, the output is
    # 1*M(Na2O)/(2*M(Na)) = 1.3479591248860067 kg liner/kg raw Na.
    assert state["onset"]
    assert state["liner_consumed_kg_per_kg_deposit"] == pytest.approx(
        100.0
    )
    half_saturated = build_state(x_star=0.5)
    na_oxide_per_na = parse_formula("Na2O").molar_mass_g_per_mol() / (
        2.0 * parse_formula("Na").molar_mass_g_per_mol()
    )
    assert half_saturated["liner_consumed_kg_per_kg_deposit"] == pytest.approx(
        na_oxide_per_na
    )


def test_tier_a_liquid_composition_at_other_temperature_refuses_x_star():
    liner = {"CaO": 60.0, "Al2O3": 40.0}
    deposits = {"Na": 0.01}
    mixed = contaminated_interface_composition(
        liner,
        deposits,
        density_kg_m3=1.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
    )
    state = build_liner_state_segment(
        {
            "composition": liner,
            "citation": "test-source",
            "criterion_tier": "A",
            "invariant_table": {
                "invariants": [
                    {
                        "subsystem": "Na2O-CaO-Al2O3",
                        "T_invariant_C": 1400.0,
                        "phases": ["liquid"],
                        "liquid_composition": mixed["composition_wt_pct"],
                        "invariant_type": "liquidus",
                        "composition_bounds_wt_pct": {
                            oxide: [wt_pct - 0.1, wt_pct + 0.1]
                            for oxide, wt_pct in mixed["composition_wt_pct"].items()
                        },
                        "first_liquid_temperature_C": 1400.0,
                    }
                ]
            },
            "density_kg_m3": 1.0,
            "reacted_layer_depth_m": 1.0,
            "margin_C": 0.0,
            "horizon_campaigns": 2,
            "porosity_fraction": 0.0,
        },
        deposits,
        area_m2=1.0,
        service_temperature_C=1500.0,
        pressure_bar=None,
        sampler=None,
        deposit_inert_kg_by_species=deposits,
        deposit_reactive_kg_by_species=deposits,
    )

    assert state["onset"]
    assert state["liner_consumed_kg_per_kg_deposit"] is None
    assert any(
        refusal["reason"] == "tier_a_saturation_data_at_service_temperature_missing"
        for refusal in state["refusals"]
    )


def test_tier_a_without_source_liquid_composition_refuses_dissolution_ratio():
    chemistry = {
        "composition": {"CaO": 60.0, "Al2O3": 40.0},
        "citation": "bounds without liquid composition",
        "criterion_tier": "A",
        "invariant_table": {
            "invariants": [
                {
                    "composition_bounds_wt_pct": {
                        "CaO": [0.0, 100.0],
                        "Al2O3": [0.0, 100.0],
                    },
                    "first_liquid_temperature_C": 1400.0,
                }
            ]
        },
        "density_kg_m3": 1.0,
        "reacted_layer_depth_m": 1.0,
        "margin_C": 0.0,
        "horizon_campaigns": 2,
        "porosity_fraction": 0.0,
    }
    state = build_liner_state_segment(
        chemistry,
        {"Na": 0.01},
        area_m2=1.0,
        service_temperature_C=1500.0,
        pressure_bar=None,
        sampler=None,
        deposit_inert_kg_by_species={"Na": 0.01},
        deposit_reactive_kg_by_species={"Na": 0.01},
    )

    assert state["liner_consumed_kg_per_kg_deposit"] is None
    assert any(
        refusal["reason"] == "tier_a_saturation_data_at_service_temperature_missing"
        for refusal in state["refusals"]
    )


def test_morey_reaction_parser_handles_assemblages_chains_and_immiscible_liquids():
    sides, phases = liner_corrosion._reaction_parts(
        "2CaO·SiO2 + CaO·Al2O3 ⇌ 2CaO·Al2O3·SiO2 ⇌ L",
        "test-source",
        "chain-row",
    )
    assert len(sides) == 3
    assert phases[-1] == "liquid"

    sides, phases = liner_corrosion._reaction_parts(
        "SiO2 (cristobalite) + TiO2 + L1",
        "test-source",
        "assemblage-row",
    )
    assert len(sides) == 1
    assert phases[-1] == "liquid"

    _kind, phases = liner_corrosion._reaction_type(
        "SiO2 (cristobalite) + L2 ⇌ L1",
        "test-source",
        "immiscible-liquid-row",
    )
    assert phases.count("liquid") == 1


def test_unparsed_morey_reaction_warns_with_row_id_and_keeps_source_rows():
    def context_row(observation_id, reaction):
        return {
            "observation_id": observation_id,
            "type": "composition",
            "units": "°C wt%",
            "locator": {"page": 1, "table": "26"},
            "values": {
                "table_number": 26,
                "subsystem_as_printed": "CaO-Al2O3-SiO2",
                "temperature_C_as_printed": 1500,
                "liquid_composition_mass_percent": {
                    "CaO": 35,
                    "Al2O3": 35,
                    "SiO2": 30,
                },
                "phase_reaction_as_printed": reaction,
            },
        }

    document = {
        "species": {
            "CaO": {
                "context": [
                    context_row("good-row", "CaO + Al2O3 ⇌ L"),
                    context_row("unparsed-row", "A ⇌ B ⇌ C ⇌ L"),
                ]
            }
        }
    }

    with pytest.warns(RuntimeWarning, match="observation_id=unparsed-row"):
        records = liner_corrosion._morey_context_records(document)
    assert [row["observation_id"] for row in records] == ["good-row"]


def test_missing_composition_porosity_and_rump_artifact_are_typed():
    with pytest.raises(InterfaceChemistryRefusal) as missing:
        load_interface_chemistry(
            "missing",
            catalog={"missing": {"interface_chemistry": {"citation": "test"}}},
        )
    assert missing.value.reason == "liner_composition_missing"
    with pytest.raises(InterfaceChemistryRefusal) as generated:
        load_interface_chemistry(
            "rump",
            catalog={
                "rump": {
                    "interface_chemistry": {
                        "composition_file": "data/generated/not-created.yaml",
                        "missing_composition_reason": "rump_composition_not_generated",
                        "citation": "generated terminal rump report",
                    }
                }
            },
        )
    assert generated.value.reason == "rump_composition_not_generated"
    with pytest.raises(InterfaceChemistryRefusal) as porosity:
        require_interface_porosity({})
    assert porosity.value.reason == "liner_porosity_missing"

    with pytest.raises(InterfaceChemistryRefusal) as missing_table:
        load_interface_invariant_table(
            "tier_a",
            catalog={
                "tier_a": {
                    "interface_chemistry": {
                        "composition": {
                            "CaO": 20.0,
                            "Al2O3": 36.0,
                            "SiO2": 44.0,
                        },
                        "citation": "test invariant source",
                    }
                }
            },
        )
    assert missing_table.value.reason == "liner_invariant_table_missing"


def test_segment_porosity_refusal_uses_furnace_material_validator():
    chemistry = {
        "composition": {"CaO": 50.0, "Al2O3": 50.0},
        "citation": "test source",
        "criterion_tier": "A",
        "invariant_table": {
            "invariants": [
                {
                    "composition_bounds_wt_pct": {
                        "CaO": [0.0, 100.0],
                        "Al2O3": [0.0, 100.0],
                    },
                    "first_liquid_temperature_C": 1600.0,
                }
            ]
        },
        "density_kg_m3": 1.0,
        "reacted_layer_depth_m": 1.0,
        "margin_C": 0.0,
        "horizon_campaigns": 1,
        "porosity_fraction": "not numeric",
    }

    state = build_liner_state_segment(
        chemistry,
        {},
        area_m2=1.0,
        service_temperature_C=1500.0,
        pressure_bar=None,
        sampler=None,
    )

    assert any(
        refusal["reason"] == "liner_porosity_invalid"
        for refusal in state["refusals"]
    )


def test_siO2_free_liner_refuses_before_magemin_returns_ok():
    backend = _FakeMageminBackend()
    with pytest.raises(LinerCorrosionRefusal) as refused:
        MageminLinerSampler(backend)({"CaO": 60.0, "Al2O3": 40.0}, 1500.0, 1.0e-6)
    assert refused.value.reason == "liner_first_melt_engine_out_of_domain"
    assert backend.calls == []


def test_hot_wall_na_alpha_one_does_not_change_equilibrium_pressure():
    from simulator.condensation import _hkl_surface_deposition_flux_mol_m2_s
    from simulator.yaml_cache import load_cached_safe_yaml

    root = Path(__file__).resolve().parents[1]
    vapor_pressure_data = load_cached_safe_yaml(
        (root / "data" / "vapor_pressures.yaml").read_text(encoding="utf-8")
    )
    flux = _hkl_surface_deposition_flux_mol_m2_s(
        "Na",
        P_local_pa=10_000.0,
        T_surface_K=1500.0 + CELSIUS_TO_KELVIN_OFFSET,
        alpha_s=1.0,
        vapor_pressure_data=vapor_pressure_data,
    )
    assert flux == 0.0


def test_all_source_records_load_with_temperature_phases_and_locators() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        records = load_liner_corrosion_invariants()

    assert Counter(row["source_id"] for row in records) == {
        "rankin-wright-1915-ajs-cao-al2o3-sio2": 47,
        "morey-1964-usgs-pp440l-phase-equilibria": 50,
        "shults-1995-calcium-aluminate-thermodynamics": 6,
        "shornikov-1997-cao-al2o3": 23,
    }
    assert len(records) == 126
    source_ids = {
        "rankin-wright-1915-ajs-cao-al2o3-sio2",
        "morey-1964-usgs-pp440l-phase-equilibria",
        "shults-1995-calcium-aluminate-thermodynamics",
        "shornikov-1997-cao-al2o3",
    }
    excluded_counts = {source_id: 0 for source_id in source_ids}
    for row in records:
        if row.get("first_liquid_exclusion_reason"):
            excluded_counts[row["source_id"]] += 1
    # The extract contains 35 CaO transition observations and 12 eligible
    # Table A compound rows. Only Table A rows 11 and 19 dissociate without a
    # liquid phase, so the Rankin first-liquid exclusion count is two.
    rankin_no_liquid_ids = {
        row["observation_id"]
        for row in records
        if row["source_id"] == "rankin-wright-1915-ajs-cao-al2o3-sio2"
        and row.get("first_liquid_exclusion_reason") == "no_liquid_phase"
    }
    assert rankin_no_liquid_ids == {
        "rankin_wright_1915_tA_r11",
        "rankin_wright_1915_tA_r19",
    }
    assert excluded_counts == {
        "rankin-wright-1915-ajs-cao-al2o3-sio2": 2,
        "morey-1964-usgs-pp440l-phase-equilibria": 2,
        "shults-1995-calcium-aluminate-thermodynamics": 1,
        "shornikov-1997-cao-al2o3": 0,
    }
    morey_subsolidus = next(
        row for row in records if row["observation_id"] == "morey1964_t26_r14"
    )
    assert morey_subsolidus["invariant_type"] == "subsolidus transition"
    assert morey_subsolidus["first_liquid_exclusion_reason"] == "no_liquid_phase"
    morey_non_event = next(
        row
        for row in records
        if row["observation_id"] == "morey1964_cas_c3as_1335"
    )
    assert morey_non_event["first_liquid_exclusion_reason"] == "not_first_liquid_transition"
    shults_dissociation = next(
        row for row in records if row["observation_id"] == "shults1995_ca_dissociation"
    )
    assert shults_dissociation["first_liquid_exclusion_reason"] == "not_first_liquid_transition"
    source_row_counts = {
        "rankin-wright-1915-ajs-cao-al2o3-sio2": 412,
        "morey-1964-usgs-pp440l-phase-equilibria": 236,
        "shults-1995-calcium-aluminate-thermodynamics": 31,
        "shornikov-1997-cao-al2o3": 67,
    }
    unparsed_counts = Counter()
    for warning in caught:
        message = str(warning.message)
        if message.startswith("liner_corrosion_tier_a_unparsed "):
            fields = dict(part.split("=", 1) for part in message.split()[1:])
            unparsed_counts[fields["source_id"]] += 1
    source_census = {}
    for source_id, source_total in source_row_counts.items():
        source_records = [row for row in records if row["source_id"] == source_id]
        solid_state = sum(
            row.get("first_liquid_exclusion_reason") == "no_liquid_phase"
            for row in source_records
        )
        unparsed = unparsed_counts[source_id]
        source_census[source_id] = {
            "consumed": len(source_records) - solid_state,
            "solid_state": solid_state,
            "unparsed": unparsed,
            "skipped": source_total - len(source_records) - unparsed,
        }
    assert source_census == {
        "rankin-wright-1915-ajs-cao-al2o3-sio2": {
            "consumed": 45,
            "solid_state": 2,
            "unparsed": 0,
            "skipped": 365,
        },
        "morey-1964-usgs-pp440l-phase-equilibria": {
            "consumed": 49,
            "solid_state": 1,
            "unparsed": 0,
            "skipped": 186,
        },
        "shults-1995-calcium-aluminate-thermodynamics": {
            "consumed": 6,
            "solid_state": 0,
            "unparsed": 0,
            "skipped": 25,
        },
        "shornikov-1997-cao-al2o3": {
            "consumed": 23,
            "solid_state": 0,
            "unparsed": 0,
            "skipped": 44,
        },
    }
    for row in records:
        assert row["subsystem"]
        assert isinstance(row["T_invariant_C"], (int, float))
        assert row["phases"] is None or row["phases"]
        assert row["liquid_composition"] is None or row["liquid_composition"]
        assert row["liquid_composition_unit"] == (
            "wt%" if row["liquid_composition"] is not None else None
        )
        assert row["invariant_type"]
        assert row["locator"]["page"]
        assert row["locator"].get("table") or row["locator"].get("section")


def test_binary_aluminates_keep_source_specific_temperatures_and_compositions() -> None:
    records = {
        (row["source_id"], row["observation_id"]): row
        for row in load_liner_corrosion_invariants()
    }

    morey_c3a = records[
        (
            "morey-1964-usgs-pp440l-phase-equilibria",
            "morey1964_t6_r1",
        )
    ]
    assert morey_c3a["T_invariant_C"] == 1535
    assert morey_c3a["invariant_type"] == "peritectic"
    assert set(morey_c3a["phases"]) == {"3CaO·Al2O3", "CaO", "liquid"}
    assert morey_c3a["liquid_composition"] == {"CaO": 59, "Al2O3": 41}

    morey_binary_expected = {
        "morey1964_t6_r1": (1535, "peritectic", {"CaO": 59, "Al2O3": 41}),
        "morey1964_t6_r3": (1458, "congruent melting", {"CaO": 48.54, "Al2O3": 51.46}),
        "morey1964_t6_r5": (1600, "congruent melting", {"CaO": 35.49, "Al2O3": 64.51}),
        "morey1964_t6_r7": (1750, "congruent melting", {"CaO": 21.54, "Al2O3": 78.46}),
        "morey1964_t6_r9": (1850, "peritectic", {"CaO": 11, "Al2O3": 89}),
    }
    for observation_id, (temperature, kind, composition) in morey_binary_expected.items():
        row = records[("morey-1964-usgs-pp440l-phase-equilibria", observation_id)]
        assert row["subsystem"] == "CaO-Al2O3"
        assert row["T_invariant_C"] == temperature
        assert row["invariant_type"] == kind
        assert row["liquid_composition"] == composition

    morey_c12a7_values = {
        observation_id: records[("morey-1964-usgs-pp440l-phase-equilibria", observation_id)]
        for observation_id in (
            "morey1964_t6_c12a7_1455",
            "morey1964_t6_c12a7_1391",
        )
    }
    assert {row["T_invariant_C"] for row in morey_c12a7_values.values()} == {1455, 1391}
    assert all(row["liquid_composition"] is None for row in morey_c12a7_values.values())

    shornikov = records[
        (
            "shornikov-1997-cao-al2o3",
            "shornikov_1997_cao_al2o3_t3_ca3al2o6_eq7",
        )
    ]
    assert shornikov["T_invariant_C"] == 1534.85
    assert shornikov["temperature_uncertainty_C"] == 5
    assert shornikov["compound_as_printed"] == "3CaO·Al2O3"
    assert shornikov["phases"] is None
    assert shornikov["liquid_composition"] is None
    cited_shornikov = records[
        (
            "shornikov-1997-cao-al2o3",
            "shornikov_1997_cao_al2o3_t3_ca3al2o6_ref16",
        )
    ]
    assert cited_shornikov["T_invariant_C"] == 1534.85
    assert cited_shornikov["source_reference_as_printed"] == "[16]"

    shults_ca2 = records[
        (
            "shults-1995-calcium-aluminate-thermodynamics",
            "shults1995_ca2_melting",
        )
    ]
    assert shults_ca2["T_invariant_C"] == 1761.85
    assert shults_ca2["liquid_composition"] is None
    assert {
        observation_id: records[
            (
                "shults-1995-calcium-aluminate-thermodynamics",
                observation_id,
            )
        ]["T_invariant_C"]
        for observation_id in (
            "shults1995_ca_dissociation",
            "shults1995_c12a7_melting",
            "shults1995_ca_melting",
            "shults1995_ca2_melting",
            "shults1995_ca6_melting",
            "shults1995_c5a3_melting",
        )
    } == {
        "shults1995_ca_dissociation": 1534.85,
        "shults1995_c12a7_melting": 1459.85,
        "shults1995_ca_melting": 1603.85,
        "shults1995_ca2_melting": 1761.85,
        "shults1995_ca6_melting": 1874.85,
        "shults1995_c5a3_melting": 1479.85,
    }


def test_rankin_and_morey_retain_their_distinct_cas_rows() -> None:
    records = {
        (row["source_id"], row["observation_id"]): row
        for row in load_liner_corrosion_invariants()
    }

    rankin = records[
        (
            "rankin-wright-1915-ajs-cao-al2o3-sio2",
            "rankin_wright_1915_tD_r1_invariant",
        )
    ]
    assert rankin["T_invariant_C"] == 1170
    assert rankin["liquid_composition"] == {
        "CaO": 23.25,
        "Al2O3": 14.75,
        "SiO2": 62,
    }

    morey = records[
        (
            "morey-1964-usgs-pp440l-phase-equilibria",
            "morey1964_t26_r1",
        )
    ]
    assert morey["T_invariant_C"] == 1345
    assert morey["liquid_composition"] == {
        "CaO": 9.8,
        "Al2O3": 18.6,
        "SiO2": 71.6,
    }


def test_morey_na_and_mgo_ternaries_keep_the_printed_compositions() -> None:
    records = {
        (row["source_id"], row["observation_id"]): row
        for row in load_liner_corrosion_invariants()
    }

    na_row = records[
        (
            "morey-1964-usgs-pp440l-phase-equilibria",
            "morey1964_t12_r1",
        )
    ]
    assert na_row["subsystem"] == "Na2O-CaO-Al2O3"
    assert na_row["T_invariant_C"] == 1550
    assert na_row["liquid_composition"] == {
        "Na2O": 8.6,
        "CaO": 21.3,
        "Al2O3": 70.1,
    }

    mgo_row = records[
        (
            "morey-1964-usgs-pp440l-phase-equilibria",
            "morey1964_t21_r1",
        )
    ]
    assert mgo_row["subsystem"] == "MgO-CaO-Al2O3"
    assert mgo_row["T_invariant_C"] == 1450
    assert mgo_row["liquid_composition"] == {
        "MgO": 6.2,
        "CaO": 51.5,
        "Al2O3": 43.3,
    }
