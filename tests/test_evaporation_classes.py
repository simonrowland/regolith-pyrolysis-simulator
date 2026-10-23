"""Degenerate-input guards and valid-path pins for evaporation_classes."""

from __future__ import annotations

import math
from pathlib import Path

import yaml

import pytest

from simulator.accounting.exceptions import UnknownSpeciesError
from simulator.accounting.formulas import resolve_species_formula
from simulator.evaporation_classes import (
    EVAPORATION_CLASSES,
    e_down_from_s,
    flux_band_factors,
    grounding_evidence,
    interface_resistance_share,
    interface_share_s,
    report_species_class_diagnostics,
)
import simulator.evaporation_classes as evaporation_classes


NAN = float("nan")
INF = float("inf")

_SHARE_KW = dict(
    species="Fe",
    alpha=0.1,
    T_K=1800.0,
    molar_mass_kg_mol=0.055845,
    overhead_pressure_pa=1000.0,
    melt_surface_area_m2=0.2,
)


def test_flux_band_factors_valid_half_share_half_dex():
    lo, hi = flux_band_factors(0.5, 0.5)
    assert lo == pytest.approx(10.0 ** (-0.25))
    assert hi == pytest.approx(10.0 ** 0.25)


def test_flux_band_factors_limiting_cases():
    assert flux_band_factors(0.0, 0.5) == (1.0, 1.0)
    lo, hi = flux_band_factors(1.0, 0.5)
    assert lo == pytest.approx(10.0 ** (-0.5))
    assert hi == pytest.approx(10.0 ** 0.5)
    lo_hi_s, hi_hi_s = flux_band_factors(2.0, 0.5)
    assert (lo_hi_s, hi_hi_s) == (lo, hi)


@pytest.mark.parametrize(
    ("s", "residual"),
    [
        (NAN, 0.5),
        (0.5, NAN),
        (INF, 0.5),
        (0.5, INF),
        (-1.0, 0.5),
    ],
)
def test_flux_band_factors_refuses_nonfinite_or_negative_s(s, residual):
    with pytest.raises(ValueError, match="must be"):
        flux_band_factors(s, residual)


def test_e_down_from_s_valid_half_share_half_dex():
    expected = math.log10(1.0 + 0.5 * (10.0**0.5 - 1.0)) / 0.5
    assert e_down_from_s(0.5, 0.5) == pytest.approx(expected)
    assert e_down_from_s(0.0, 0.5) == 0.0
    assert e_down_from_s(1.0, 0.5) == pytest.approx(1.0)


def test_e_down_from_s_s_above_one_still_evaluates():
    # Physical s≤1 is a Bucket B proposal; this pin records current behaviour.
    assert e_down_from_s(2.0, 0.5) == pytest.approx(
        math.log10(1.0 + 2.0 * (10.0**0.5 - 1.0)) / 0.5
    )


@pytest.mark.parametrize(
    ("s", "delta"),
    [
        (NAN, 0.5),
        (0.5, NAN),
        (INF, 0.5),
        (0.5, INF),
        (-0.1, 0.5),
    ],
)
def test_e_down_from_s_refuses_nonfinite_or_negative_s(s, delta):
    with pytest.raises(ValueError, match="must be"):
        e_down_from_s(s, delta)


def test_e_down_from_s_zero_delta_still_refused():
    with pytest.raises(ValueError, match="non-zero"):
        e_down_from_s(0.5, 0.0)


def test_interface_resistance_share_follows_surface_geometry_owner():
    from engines.builtin.evaporation_flux import _series_resistance_evaporation_flux_kg_m2_s
    series = interface_resistance_share(**_SHARE_KW)
    expected = _series_resistance_evaporation_flux_kg_m2_s(
        species="Fe", P_eq_pa=1.0, P_bulk_pa=0.0, T_surface_K=1800.0,
        molar_mass_kg_mol=0.055845, alpha_i=0.1, overhead_pressure_pa=1000.0,
        melt_surface_area_m2=0.2,
    )
    assert series.flux_kg_s_m2 == expected.flux_kg_s_m2
    assert interface_share_s(series) == interface_share_s(expected)


def test_interface_resistance_share_zero_overhead_is_vacuum_hkl():
    series = interface_resistance_share(**{**_SHARE_KW, "overhead_pressure_pa": 0.0})
    assert series.r_gas == 0.0
    assert interface_share_s(series) == pytest.approx(1.0)
    assert series.flux_kg_s_m2 == pytest.approx(7.706352464035475e-05)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"overhead_pressure_pa": NAN},
        {"overhead_pressure_pa": INF},
        {"overhead_pressure_pa": -1.0},
        {"pipe_diameter_m": 0.0},
        {"pipe_diameter_m": NAN},
        {"pipe_diameter_m": -0.1},
        {"p_eq_pa": NAN},
        {"p_eq_pa": INF},
        {"p_bulk_pa": NAN},
        {"p_bulk_pa": INF},
        {"radial_stir_factor": NAN},
        {"radial_stir_factor": INF},
        {"radial_stir_factor": -1.0},
    ],
)
def test_interface_resistance_share_refuses_degenerate_transport_inputs(kwargs):
    with pytest.raises(ValueError, match="must be finite"):
        interface_resistance_share(**{**_SHARE_KW, **kwargs})


def test_report_species_class_diagnostics_class_alpha_fallback_unchanged():
    diag = report_species_class_diagnostics(
        "Fe",
        T_K=2000.0,
        overhead_pressure_pa=1000.0,
        melt_surface_area_m2=0.2,
        vapor_pressure_data={},
    )
    assert diag.alpha_runtime is None
    assert diag.alpha_runtime_note.endswith("+class_alpha_fallback_for_s")
    assert diag.series is not None
    assert diag.series.alpha_intrinsic == 0.083
    assert diag.interface_share_s == interface_share_s(diag.series)
    assert diag.r_interface == 16479.794223102974
    assert diag.r_gas == diag.series.r_gas
    assert diag.series.surface_film["formula_id"] == "isolated_disk_halfspace_8_over_pi"
    assert diag.r_melt == 0.0
    assert diag.flux_band_factor_high == flux_band_factors(diag.interface_share_s, diag.membership.residual_dex)[1]
    assert diag.flux_band_factor_low == flux_band_factors(diag.interface_share_s, diag.membership.residual_dex)[0]
    from engines.builtin.evaporation_flux import _series_resistance_evaporation_flux_kg_m2_s
    expected = _series_resistance_evaporation_flux_kg_m2_s(
        species="Fe", P_eq_pa=1.0, P_bulk_pa=0.0, T_surface_K=2000.0,
        molar_mass_kg_mol=0.055845, alpha_i=0.083,
        overhead_pressure_pa=1000.0, melt_surface_area_m2=0.2,
    )
    assert diag.series == expected


@pytest.mark.parametrize(
    ("species", "formula"),
    [
        ("Al2", "Al2"),
        ("Al2O", "Al2O"),
        ("Al2O2", "Al2O2"),
        ("Al2O3_gas", "Al2O3"),
        ("AlO", "AlO"),
        ("AlO2", "AlO2"),
        ("Ca2", "Ca2"),
        ("CrO", "CrO"),
        ("CrO2", "CrO2"),
        ("CrO3", "CrO3"),
        ("TiO", "TiO"),
        ("TiO2_gas", "TiO2"),
        ("SiO", "SiO"),
        ("Na", "Na"),
        ("Na2", "Na2"),
        ("Si", "Si"),
        ("Si2", "Si2"),
        ("Al", "Al"),
        ("Cr", "Cr"),
        ("Mn", "Mn"),
        ("MgO_gas", "MgO"),
        ("Na2O_gas", "Na2O"),
        ("K2O_gas", "K2O"),
        ("SiO2_gas", "SiO2"),
        ("CaO_gas", "CaO"),
        ("Cr2O3_gas", "Cr2O3"),
        ("Fe2O3_gas", "Fe2O3"),
        ("FeO_gas", "FeO"),
        ("P2O5_gas", "P2O5"),
    ],
)
def test_report_species_class_diagnostics_default_mass_uses_formula_owner(
    species, formula, monkeypatch
):
    formula_mass = resolve_species_formula(formula).molar_mass_kg_per_mol()
    observed_masses = []
    original_share = evaporation_classes.interface_resistance_share

    def capture_mass(**kwargs):
        observed_masses.append(kwargs["molar_mass_kg_mol"])
        # Some formula-covered species have no diagnostic transport proxy;
        # use Fe downstream while preserving the mass handed to the owner.
        return original_share(**{**kwargs, "species": "Fe"})

    monkeypatch.setattr(
        evaporation_classes, "interface_resistance_share", capture_mass
    )
    diag = report_species_class_diagnostics(
        species,
        T_K=2000.0,
        overhead_pressure_pa=1000.0,
        melt_surface_area_m2=0.2,
        alpha_override=0.1,
        vapor_pressure_data={},
    )

    assert diag.series is not None
    assert observed_masses == [formula_mass]


def test_report_species_class_diagnostics_unknown_species_refuses_typed():
    with pytest.raises(UnknownSpeciesError, match="unknown species"):
        report_species_class_diagnostics(
            "not_a_catalog_species",
            T_K=2000.0,
            overhead_pressure_pa=1000.0,
        melt_surface_area_m2=0.2,
            alpha_override=0.1,
            vapor_pressure_data={},
        )


def test_costa_redox_grounding_uses_owner_figure_series_once():
    costa = [
        row for row in grounding_evidence("silicate_melt_cation_redox")
        if "costa" in row.source_id
    ]
    assert len(costa) == 1
    assert costa[0].source_id == "kems-007-costa-2015"
    assert costa[0].observation_id == "costa_2015_fe_vaporization_coefficient_alpha_digitized"
    assert costa[0].alpha == pytest.approx(0.01978)


def test_redox_class_band_covers_every_grounded_alpha():
    cls = EVAPORATION_CLASSES["silicate_melt_cation_redox"]
    grounded = [
        row.alpha for row in grounding_evidence(cls.class_id)
        if row.alpha is not None
    ]
    assert grounded
    assert all(cls.band_low() <= alpha <= cls.band_high() for alpha in grounded)


def test_consolidated_duplicate_evidence_alpha_values_stay_pinned():
    rows = (
        *evaporation_classes._SIO_EVIDENCE,
        *evaporation_classes._REDOX_EVIDENCE,
        *evaporation_classes._MODIFIER_EVIDENCE,
        *evaporation_classes._MARKED_IDEAL_FENCES,
    )
    owner_extract = yaml.safe_load(
        (Path(__file__).parents[1] / "data/literature/extracts/kems-005-fedkin-2006.yaml")
        .read_text(encoding="utf-8")
    )
    mg_owner = next(
        row
        for row in owner_extract["species"]["Mg"]["observations"]
        if row["observation_id"] == "fedkin_2006_mg_hashimoto_langmuir_table3"
    )
    targets = {
        ("kems-005-fedkin-2006", "fedkin_2006_sio_hashimoto_langmuir_table3"): 0.17,
        ("kems-005-fedkin-2006", "fedkin_2006_fe_hashimoto_langmuir_table3"): 0.24,
        ("kems-005-fedkin-2006", "fedkin_2006_mg_hashimoto_langmuir_table3"): mg_owner[
            "values"
        ]["alpha"],
        ("kems-005-fedkin-2006", "fedkin_2006_na_yu_langmuir"): 0.26,
        ("kems-005-fedkin-2006", "fedkin_2006_k_yu_langmuir"): 0.13,
        ("kems-012-sossi-2019", "sossi_2019_na_alpha_e_authors_adopted_unity"): 1.0,
        ("kems-009-safarian-2013", "safarian_2013_si_pure_liquid_alpha_b1"): 1.0,
    }
    actual = {(row.source_id, row.observation_id): row.alpha for row in rows
              if (row.source_id, row.observation_id) in targets}
    assert actual == targets
