"""Load-time iron split: citation, oxygen, Kress91 inversion, seed branch.

Expected masses come from resolve_species_formula. Expected fO2 and ferric
fractions come from the production IW and the production Kress91 forward
relation. This file does not paste the inversion algebra.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator.accounting.formulas import (
    ATOMIC_WEIGHTS_G_PER_MOL,
    resolve_species_formula,
)
from simulator.core import PyrolysisSimulator
from types import SimpleNamespace

from simulator.fe_redox import (
    BULK_ROCK_ON_LIQUID_RELATION_REASON,
    LOAD_FE_SPLIT_PRESSURE_BAR,
    KRESS91_FO2_KEY_REFERENCE_T_K,
    KRESS91_LIQUID_CALIBRATION_MAX_T_C,
    KRESS91_LIQUID_CALIBRATION_MIN_T_C,
    MEASURED_FRACTION_SEED_DISSOCIATION_ENVELOPE,
    MEASURED_FRACTION_SEED_KRESS91,
    fe3_fraction_for_prior,
    feo_fe2o3_kg_from_feot,
    feo_iw_log10_fO2_bar,
    intrinsic_melt_fO2,
    kress91_fO2_log_for_fe3_fraction,
    kress91_fe3_over_sigma_fe,
    kress91_temperature_band_case,
    measured_fe3_fraction_seed,
    melt_mol_fractions_for_kress91,
    omitted_ferric_oxygen_kg,
    resolve_load_fe_redox,
)
from simulator.physical_constants import (
    MELT_DISSOCIATION_PO2_MAX_BAR,
    MELT_DISSOCIATION_PO2_MIN_BAR,
)
from simulator.feedstock_composition import (
    FeRedoxPrior,
    resolve_feedstock_composition,
    total_oxygen_bounds,
)
from simulator.melt_backend.base import InternalAnalyticalBackend


DATA = Path(__file__).resolve().parents[1] / "data"

_CITATION = {
    "source_id": "unit-test-source",
    "locator": "Table 1",
}


def _feo_formula_masses() -> tuple[float, float, float]:
    feo = resolve_species_formula("FeO").molar_mass_g_per_mol()
    fe2o3 = resolve_species_formula("Fe2O3").molar_mass_g_per_mol()
    oxygen = float(ATOMIC_WEIGHTS_G_PER_MOL["O"])
    return feo, fe2o3, oxygen


def _measured_block(value: float, **extra: object) -> dict:
    block = {"value": value, **_CITATION, **extra}
    return {"measured_fe3_fraction": block}


def _delta_block(value: float = -1.01) -> dict:
    return {
        "delta_iw": {
            "value": value,
            "range": [-1.41, -0.60],
            **_CITATION,
        }
    }


def _ferrous_entry(**extra: object) -> dict:
    return {
        "composition_wt_pct": {"SiO2": 70.0, "FeO": 10.0, "Na2O": 1.0},
        "composition_basis": {
            "fe_reporting_convention": "total Fe as FeO",
        },
        **extra,
    }


def _feedstocks() -> dict:
    return yaml.safe_load((DATA / "feedstocks.yaml").read_text()) or {}


def _sim(feedstocks: dict) -> PyrolysisSimulator:
    return PyrolysisSimulator(
        InternalAnalyticalBackend(),
        yaml.safe_load((DATA / "setpoints.yaml").read_text()) or {},
        feedstocks,
        yaml.safe_load((DATA / "vapor_pressures.yaml").read_text()) or {},
    )


def test_prior_without_citation_is_refused() -> None:
    entry = _ferrous_entry(
        fe_redox_prior={"measured_fe3_fraction": {"value": 0.2}}
    )
    with pytest.raises(ValueError, match="requires source_id and locator"):
        resolve_feedstock_composition(entry)


def test_prior_requires_exactly_one_kind() -> None:
    both = _ferrous_entry(
        fe_redox_prior={**_measured_block(0.2), **_delta_block()}
    )
    neither = _ferrous_entry(fe_redox_prior={"source_id": "x"})
    with pytest.raises(ValueError, match="exactly one of"):
        resolve_feedstock_composition(both)
    with pytest.raises(ValueError, match="exactly one of"):
        resolve_feedstock_composition(neither)


def test_flag_plus_prior_is_refused() -> None:
    entry = _ferrous_entry(
        fe_redox_split_unknown=True,
        fe_redox_prior=_measured_block(0.2),
    )
    with pytest.raises(ValueError, match="cannot be combined with fe_redox_prior"):
        resolve_feedstock_composition(entry)


def test_measured_fraction_outside_unit_interval_is_refused() -> None:
    entry = _ferrous_entry(fe_redox_prior=_measured_block(1.2))
    with pytest.raises(ValueError, match="between 0 and 1"):
        resolve_feedstock_composition(entry)


def test_boolean_prior_value_is_refused() -> None:
    entry = _ferrous_entry(fe_redox_prior=_measured_block(True))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite number"):
        resolve_feedstock_composition(entry)


def test_prior_plus_declared_fe2o3_is_refused() -> None:
    entry = {
        "composition_wt_pct": {"SiO2": 70.0, "FeO": 8.0, "Fe2O3": 2.0},
        "composition_basis": {
            "FeO": {"method": "wet chemistry", "source": "synthetic assay"},
            "Fe2O3": {"method": "Mössbauer", "source": "synthetic assay"},
        },
        "fe_redox_prior": _measured_block(0.2),
    }
    with pytest.raises(ValueError, match="cannot be combined with declared Fe2O3"):
        resolve_feedstock_composition(entry)


def test_delta_iw_value_must_lie_inside_its_range() -> None:
    entry = _ferrous_entry(
        fe_redox_prior={
            "delta_iw": {
                "value": -2.0,
                "range": [-1.41, -0.60],
                **_CITATION,
            }
        }
    )
    with pytest.raises(ValueError, match="must lie inside range"):
        resolve_feedstock_composition(entry)


def test_omitted_oxygen_matches_the_species_formula() -> None:
    """f = 1 moves every Fe atom from FeO to Fe2O3 and adds 0.5 O per Fe."""

    feot_kg = 100.0
    molar_feo, molar_fe2o3, molar_oxygen = _feo_formula_masses()
    n_fe = feot_kg / molar_feo
    oxygen_kg = n_fe * 0.5 * molar_oxygen
    fe2o3_kg = n_fe * molar_fe2o3 / 2.0

    assert omitted_ferric_oxygen_kg(feot_kg, 1.0) == pytest.approx(oxygen_kg)
    ferrous_kg, ferric_kg, credited = feo_fe2o3_kg_from_feot(feot_kg, 1.0)
    assert ferrous_kg == pytest.approx(0.0)
    assert ferric_kg == pytest.approx(fe2o3_kg)
    assert credited == pytest.approx(oxygen_kg)
    assert ferrous_kg + ferric_kg == pytest.approx(feot_kg + oxygen_kg)
    # The dispatch text's 8.0e-3 coefficient is not this molar result.
    assert oxygen_kg != pytest.approx(0.008 * feot_kg)


def test_kress91_inversion_reproduces_the_forward_fraction() -> None:
    composition = {"SiO2": 45.0, "FeO": 16.5, "Al2O3": 10.0, "MgO": 10.0}
    mol_fractions = melt_mol_fractions_for_kress91(composition)
    fO2_log = -10.5
    fraction = kress91_fe3_over_sigma_fe(
        fO2_log=fO2_log,
        mol_fractions=mol_fractions,
        T_K=KRESS91_FO2_KEY_REFERENCE_T_K,
        pressure_bar=LOAD_FE_SPLIT_PRESSURE_BAR,
    )
    inverted = kress91_fO2_log_for_fe3_fraction(
        fe3_fraction=fraction,
        composition_wt_pct=composition,
        T_K=KRESS91_FO2_KEY_REFERENCE_T_K,
        pressure_bar=LOAD_FE_SPLIT_PRESSURE_BAR,
    )
    assert inverted == pytest.approx(fO2_log)
    reproduced = kress91_fe3_over_sigma_fe(
        fO2_log=inverted,
        mol_fractions=mol_fractions,
        T_K=KRESS91_FO2_KEY_REFERENCE_T_K,
        pressure_bar=LOAD_FE_SPLIT_PRESSURE_BAR,
    )
    assert reproduced == pytest.approx(fraction)


def test_endpoint_fractions_have_no_finite_inversion() -> None:
    composition = {"SiO2": 45.0, "FeO": 16.5}
    for fraction in (0.0, 1.0):
        with pytest.raises(ValueError, match="strictly between 0 and 1"):
            kress91_fO2_log_for_fe3_fraction(
                fe3_fraction=fraction,
                composition_wt_pct=composition,
                T_K=KRESS91_FO2_KEY_REFERENCE_T_K,
                pressure_bar=LOAD_FE_SPLIT_PRESSURE_BAR,
            )


def test_endpoint_fractions_seed_the_dissociation_envelope_on_load() -> None:
    """f = 0 and f = 1 must not fall through to the same IW-plus-alkali seed."""

    composition = {"SiO2": 45.0, "FeO": 16.5, "Na2O": 1.0}
    bounds = {
        0.0: MELT_DISSOCIATION_PO2_MIN_BAR,
        1.0: MELT_DISSOCIATION_PO2_MAX_BAR,
    }
    iw_alkali = intrinsic_melt_fO2(composition, KRESS91_FO2_KEY_REFERENCE_T_K)
    for fraction, bound_bar in bounds.items():
        seeded, domain = measured_fe3_fraction_seed(
            fe3_fraction=fraction,
            composition_wt_pct=composition,
            T_K=KRESS91_FO2_KEY_REFERENCE_T_K,
            pressure_bar=LOAD_FE_SPLIT_PRESSURE_BAR,
        )
        assert domain == MEASURED_FRACTION_SEED_DISSOCIATION_ENVELOPE
        assert seeded == pytest.approx(math.log10(bound_bar))
        assert seeded != pytest.approx(iw_alkali)

        feedstocks = _feedstocks()
        entry = dict(feedstocks["lunar_mare_low_ti"])
        entry.pop("fe_redox_prior", None)
        feedstocks["lunar_mare_low_ti"] = entry
        plain = _sim(feedstocks)
        plain.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
        feot_kg = float(plain.inventory.melt_oxide_kg["FeO"])
        fallthrough = intrinsic_melt_fO2(
            plain._melt_oxide_wt_pct(),
            25.0 + 273.15,
        )

        feedstocks["lunar_mare_low_ti"] = {
            **entry,
            "fe_redox_prior": _measured_block(fraction),
        }
        sim = _sim(feedstocks)
        sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
        assert sim.melt.fO2_log == pytest.approx(math.log10(bound_bar))
        assert sim.melt.fO2_log != pytest.approx(fallthrough)
        notice = sim.melt_fO2_seed_run_notice()
        assert notice is not None
        assert notice["seed_domain"] == MEASURED_FRACTION_SEED_DISSOCIATION_ENVELOPE
        assert "not a Kress91" in notice["message"]
        assert "not a Kress91 root" in notice["seed_domain_reason"]
        assert notice["fe3_fraction"] == pytest.approx(fraction)

        melt = sim.inventory.melt_oxide_kg
        molar_feo, molar_fe2o3, _molar_oxygen = _feo_formula_masses()
        n_fe = (
            float(melt.get("FeO", 0.0)) / molar_feo
            + 2.0 * float(melt.get("Fe2O3", 0.0)) / molar_fe2o3
        )
        assert n_fe == pytest.approx(feot_kg / molar_feo)
        if fraction == 0.0:
            assert float(melt.get("Fe2O3", 0.0)) == 0.0
            assert "feot_omitted_ferric_oxygen" not in (
                sim.inventory.stage0_external_inputs_kg
            )
        else:
            _ferrous, ferric, oxygen_kg = feo_fe2o3_kg_from_feot(feot_kg, 1.0)
            assert float(melt.get("FeO", 0.0)) == pytest.approx(0.0)
            assert float(melt.get("Fe2O3", 0.0)) == pytest.approx(ferric)
            assert sim.inventory.stage0_external_inputs_kg[
                "feot_omitted_ferric_oxygen"
            ] == pytest.approx(oxygen_kg)

    interior, interior_domain = measured_fe3_fraction_seed(
        fe3_fraction=0.2,
        composition_wt_pct=composition,
        T_K=KRESS91_FO2_KEY_REFERENCE_T_K,
        pressure_bar=LOAD_FE_SPLIT_PRESSURE_BAR,
    )
    assert interior_domain == MEASURED_FRACTION_SEED_KRESS91
    assert interior == pytest.approx(
        kress91_fO2_log_for_fe3_fraction(
            fe3_fraction=0.2,
            composition_wt_pct=composition,
            T_K=KRESS91_FO2_KEY_REFERENCE_T_K,
            pressure_bar=LOAD_FE_SPLIT_PRESSURE_BAR,
        )
    )


def test_delta_iw_seed_is_production_iw_plus_the_offset_without_alkali() -> None:
    composition = {"SiO2": 45.0, "FeO": 16.5, "Na2O": 5.0, "K2O": 2.0}
    prior = FeRedoxPrior(
        kind="delta_iw",
        value=-1.01,
        source_id="unit-test-source",
        locator="Table 1",
        range_low=-1.41,
        range_high=-0.60,
    )
    temperature_K = 1673.15
    seeded = intrinsic_melt_fO2(
        composition, temperature_K, fe_redox_prior=prior
    )
    assert seeded == pytest.approx(
        feo_iw_log10_fO2_bar(temperature_K) + prior.value
    )
    no_prior = intrinsic_melt_fO2(composition, temperature_K)
    assert no_prior != pytest.approx(seeded)


def test_seed_notice_carries_the_kress_band_and_does_not_refuse() -> None:
    """A cold or bulk-rock seed is flagged. The load still completes."""

    feedstocks = _feedstocks()
    lunar = _sim(feedstocks)
    lunar.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    cold_band = kress91_temperature_band_case(lunar.melt.temperature_C)
    notice = lunar.melt_fO2_seed_run_notice()
    assert notice is not None
    assert notice["seed_temperature_K"] == pytest.approx(
        lunar.melt.temperature_C + 273.15
    )
    assert notice["adopting_temperature_K"] is None
    assert notice["kress91_calibration_min_T_C"] == KRESS91_LIQUID_CALIBRATION_MIN_T_C
    assert notice["kress91_calibration_max_T_C"] == KRESS91_LIQUID_CALIBRATION_MAX_T_C
    assert notice["kress91_temperature_band"]["case"] == cold_band["case"]
    assert notice["extrapolated"] is True
    assert cold_band["status"] in notice["extrapolation_reasons"]
    assert BULK_ROCK_ON_LIQUID_RELATION_REASON not in notice["extrapolation_reasons"]

    lunar.melt.temperature_C = KRESS91_LIQUID_CALIBRATION_MIN_T_C
    inside = lunar.melt_fO2_seed_run_notice()
    assert inside is not None
    assert inside["extrapolated"] is False
    assert inside["extrapolation_reasons"] == []
    lunar.melt.oxygen_reservoir = SimpleNamespace(reference_T_K=1600.65)
    adopted = lunar.melt_fO2_seed_run_notice()
    assert adopted is not None
    assert adopted["adopting_temperature_K"] == pytest.approx(1600.65)

    measured_entry = dict(feedstocks["lunar_mare_low_ti"])
    measured_entry["fe_redox_prior"] = _measured_block(0.2)
    feedstocks["lunar_mare_low_ti"] = measured_entry
    measured = _sim(feedstocks)
    measured.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    measured.melt.temperature_C = 1400.0
    bulk = measured.melt_fO2_seed_run_notice()
    assert bulk is not None
    assert bulk["extrapolated"] is True
    assert bulk["extrapolation_reasons"] == [BULK_ROCK_ON_LIQUID_RELATION_REASON]
    measured.melt.temperature_C = 25.0
    both = measured.melt_fO2_seed_run_notice()
    assert both is not None
    floor = kress91_temperature_band_case(25.0)
    assert floor["status"] in both["extrapolation_reasons"]
    assert BULK_ROCK_ON_LIQUID_RELATION_REASON in both["extrapolation_reasons"]

    endpoint_entry = dict(feedstocks["ci_carbonaceous_chondrite"])
    endpoint_entry["fe_redox_prior"] = _measured_block(0.0)
    feedstocks["ci_carbonaceous_chondrite"] = endpoint_entry
    endpoint = _sim(feedstocks)
    endpoint.load_batch("ci_carbonaceous_chondrite", mass_kg=1000.0)
    endpoint.melt.temperature_C = 1400.0
    endpoint_notice = endpoint.melt_fO2_seed_run_notice()
    assert endpoint_notice is not None
    assert endpoint_notice["seed_domain"] == (
        MEASURED_FRACTION_SEED_DISSOCIATION_ENVELOPE
    )
    assert endpoint_notice["extrapolation_reasons"] == [
        BULK_ROCK_ON_LIQUID_RELATION_REASON
    ]

    unsplit = _feedstocks()
    plain = dict(unsplit["lunar_mare_low_ti"])
    plain.pop("fe_redox_prior", None)
    unsplit["lunar_mare_low_ti"] = plain
    lower_sim = _sim(unsplit)
    lower_sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    lower = lower_sim.melt_fO2_seed_run_notice()
    assert lower is not None
    assert lower["authority"] == "lower_bound"
    assert lower["extrapolated"] is False
    assert lower["extrapolation_reasons"] == []
    assert lower["adopting_temperature_K"] is None


def test_declared_ferric_pair_is_not_a_lower_bound() -> None:
    oxides = {"SiO2": 70.0, "FeO": 8.0, "Fe2O3": 2.0}
    resolved = resolve_load_fe_redox(dict(oxides), None)
    assert resolved.authority is None
    assert resolved.added_oxygen_kg == 0.0
    assert oxides["FeO"] == 8.0
    assert oxides["Fe2O3"] == 2.0


def test_iron_free_melt_has_no_resolution_notice() -> None:
    resolved = resolve_load_fe_redox({"SiO2": 100.0}, None)
    assert resolved.authority is None
    assert resolved.fe3_fraction is None


def test_measured_load_keeps_iron_atoms_and_credits_oxygen() -> None:
    fraction = 0.2
    feedstocks = _feedstocks()
    # The catalog seats a lunar prior. This test's baseline is the unsplit
    # FeOT ledger, so that prior is removed before the plain load.
    unsplit = dict(feedstocks["lunar_mare_low_ti"])
    unsplit.pop("fe_redox_prior", None)
    feedstocks["lunar_mare_low_ti"] = unsplit
    plain = _sim(feedstocks)
    plain.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    feot_kg = float(plain.inventory.melt_oxide_kg["FeO"])
    pre_split_wt = plain._melt_oxide_wt_pct()

    feedstocks["lunar_mare_low_ti"] = {
        **feedstocks["lunar_mare_low_ti"],
        "fe_redox_prior": _measured_block(fraction, uncertainty=0.01),
    }
    sim = _sim(feedstocks)
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    melt = sim.inventory.melt_oxide_kg
    molar_feo, molar_fe2o3, _molar_oxygen = _feo_formula_masses()
    n_fe = (
        float(melt.get("FeO", 0.0)) / molar_feo
        + 2.0 * float(melt.get("Fe2O3", 0.0)) / molar_fe2o3
    )
    assert n_fe == pytest.approx(feot_kg / molar_feo)
    oxygen_kg = omitted_ferric_oxygen_kg(feot_kg, fraction)
    assert sim.inventory.stage0_external_inputs_kg[
        "feot_omitted_ferric_oxygen"
    ] == pytest.approx(oxygen_kg)
    assert (
        float(melt.get("FeO", 0.0)) + float(melt.get("Fe2O3", 0.0))
    ) == pytest.approx(feot_kg + oxygen_kg)
    _ferrous, ferric, _credited = feo_fe2o3_kg_from_feot(feot_kg, fraction)
    assert float(melt.get("Fe2O3", 0.0)) == pytest.approx(ferric)

    notice = sim.melt_fO2_seed_run_notice()
    assert notice is not None
    assert notice["authority"] == "measured"
    assert notice["source_id"] == "unit-test-source"
    assert notice["locator"] == "Table 1"
    assert notice["value"] == pytest.approx(fraction)
    assert notice["fe3_fraction"] == pytest.approx(fraction)
    assert sim.melt.fO2_log == pytest.approx(
        kress91_fO2_log_for_fe3_fraction(
            fe3_fraction=fraction,
            composition_wt_pct=sim._melt_oxide_wt_pct(),
            T_K=25.0 + 273.15,
            pressure_bar=LOAD_FE_SPLIT_PRESSURE_BAR,
        )
    )
    # The split fraction is the cited value, independent of the 25 C seed.
    assert fe3_fraction_for_prior(pre_split_wt, sim._load_fe_redox.prior) == (
        pytest.approx(fraction)
    )


def test_delta_iw_load_splits_at_the_kress_reference_and_seeds_iw_plus_offset() -> None:
    delta = -1.01
    feedstocks = _feedstocks()
    unsplit = dict(feedstocks["lunar_mare_low_ti"])
    unsplit.pop("fe_redox_prior", None)
    feedstocks["lunar_mare_low_ti"] = unsplit
    plain = _sim(feedstocks)
    plain.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    pre_split_wt = plain._melt_oxide_wt_pct()
    expected_fraction = kress91_fe3_over_sigma_fe(
        fO2_log=feo_iw_log10_fO2_bar(KRESS91_FO2_KEY_REFERENCE_T_K) + delta,
        mol_fractions=melt_mol_fractions_for_kress91(pre_split_wt),
        T_K=KRESS91_FO2_KEY_REFERENCE_T_K,
        pressure_bar=LOAD_FE_SPLIT_PRESSURE_BAR,
    )

    feedstocks["lunar_mare_low_ti"] = {
        **feedstocks["lunar_mare_low_ti"],
        "fe_redox_prior": _delta_block(delta),
    }
    sim = _sim(feedstocks)
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    resolution = sim._load_fe_redox
    assert resolution.authority == "prior"
    assert resolution.fe3_fraction == pytest.approx(expected_fraction)
    assert resolution.added_oxygen_kg > 0.0
    assert sim.melt.fO2_log == pytest.approx(
        feo_iw_log10_fO2_bar(25.0 + 273.15) + delta
    )
    notice = sim.melt_fO2_seed_run_notice()
    assert notice is not None
    assert notice["authority"] == "prior"
    assert notice["kind"] == "delta_iw"
    assert notice["value"] == pytest.approx(delta)
    assert "lower bound" not in notice["message"].lower()


def test_prior_oxygen_bound_is_a_point_on_the_same_coefficient() -> None:
    entry = _ferrous_entry(fe_redox_prior=_measured_block(0.25))
    bounds = total_oxygen_bounds(entry)
    resolved = resolve_feedstock_composition(entry)
    assert bounds.upper.value_wt_pct == pytest.approx(bounds.lower.value_wt_pct)
    assert bounds.upper.refused_reason is None
    ferrous = _ferrous_entry()
    ferrous_oxygen = total_oxygen_bounds(ferrous).lower.value_wt_pct
    assert bounds.lower.value_wt_pct == pytest.approx(
        ferrous_oxygen
        + omitted_ferric_oxygen_kg(resolved.total_fe, 0.25)
    )
