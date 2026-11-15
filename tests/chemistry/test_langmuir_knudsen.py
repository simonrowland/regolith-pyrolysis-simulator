"""Langmuir / Knudsen analytical flux model tests."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from engines.builtin.evaporation_flux import _series_resistance_evaporation_flux_kg_m2_s
from engines.builtin.vapor_pressure import VaporPressureComputationError
import simulator.chemistry.langmuir_knudsen as langmuir_knudsen
from simulator.chemistry.langmuir_knudsen import (
    grounded_alpha,
    knudsen_effusion_flux,
    knudsen_effusion_molar_flux,
    langmuir_flux,
    langmuir_molar_flux,
    pseudo_antoine_p_eq_pa,
    series_flux,
    species_molar_mass_kg_mol,
    validate_against_baseline,
)
from simulator.condensation import GAS_CONSTANT_J_MOL_K
from simulator.transport_constants import FREE_MOLECULAR_KNUDSEN_MIN


_K_BASE = {
    "species": "K",
    "p_eq_pa": 80.0,
    "p_bulk_pa": 5.0,
    "T_surface_K": 1800.0,
    "molar_mass_kg_mol": 0.0390983,
    "alpha": 0.13,
}
_REPO_ROOT = Path(__file__).resolve().parents[2]
_VALIDATION_SIDECAR = (
    _REPO_ROOT / "data/literature/langmuir_knudsen_flux_validation.yaml"
)


def _patch_antoine_row(monkeypatch, coefficients):
    monkeypatch.setattr(
        langmuir_knudsen,
        "_species_row",
        lambda _species: {"antoine": coefficients},
    )


def test_langmuir_flux_matches_hk_formula():
    molar_mass = 0.0390983
    T_K = 1800.0
    alpha = 0.13
    p_eq = 80.0
    p_bulk = 5.0
    delta_p = p_eq - p_bulk
    k_hk = math.sqrt(
        molar_mass / (2.0 * math.pi * GAS_CONSTANT_J_MOL_K * T_K)
    )
    expected = alpha * delta_p * k_hk
    assert langmuir_flux(
        "K",
        T_K,
        p_eq,
        p_bulk,
        alpha,
        molar_mass_kg_mol=molar_mass,
    ) == pytest.approx(expected, rel=1e-12)

    expected_molar = alpha * delta_p / math.sqrt(
        2.0 * math.pi * molar_mass * GAS_CONSTANT_J_MOL_K * T_K
    )
    assert langmuir_molar_flux(
        T_K,
        p_eq,
        p_bulk,
        alpha,
        molar_mass_kg_mol=molar_mass,
    ) == pytest.approx(expected_molar, rel=1e-12)
    assert knudsen_effusion_molar_flux(
        T_K,
        p_eq,
        p_bulk_pa=p_bulk,
        molar_mass_kg_mol=molar_mass,
    ) == pytest.approx(expected_molar / alpha, rel=1e-12)


def test_r04_inline_hk_call_sites_pre_move_hex_table():
    """Record the four independent HK expressions before consolidating them."""
    from simulator.battery.oxygen_balance import (
        _VacuumOxygenChannel,
        _solve_vacuum_oxygen_balance,
    )
    from simulator.chemistry.oxygen_interface import (
        InterfaceOxygenChannel,
        solve_interface_oxygen,
    )
    from simulator.condensation import _hkl_impingement_flux_mol_m2_s
    from simulator.physical_constants import PA_PER_BAR

    temperatures = (1000.0, 1800.0, 2500.0)
    molar_masses = (0.002, 0.055845, 0.250)
    alpha = 0.37
    output = {}
    for temperature in temperatures:
        for molar_mass in molar_masses:
            label = (temperature, molar_mass)
            channels = (
                _VacuumOxygenChannel(
                    "A", molar_mass, 0.0, 1.0, -0.5, alpha,
                    "R-04 pre-move pin",
                ),
                _VacuumOxygenChannel(
                    "O2", 0.031998, 2.0, 0.0, 1.0, 1.0,
                    "R-04 pre-move pin",
                ),
            )

            def vacuum_pressure_model(logp):
                po2_bar = 10.0**logp
                return {
                    "A": 1.0e-3 / math.sqrt(po2_bar),
                    "O2": 1.0e5 * po2_bar,
                }

            vacuum = _solve_vacuum_oxygen_balance(
                vacuum_pressure_model,
                channels,
                temperature_K=temperature,
            )
            output[("battery", *label)] = vacuum.channel_fluxes_mol_m2_s[
                "A"
            ].hex()

            interface_channel = InterfaceOxygenChannel(
                "A", molar_mass, 0.0, 1.0, -0.5, alpha,
            )

            def interface_pressure_model(logp):
                return {
                    "A": PA_PER_BAR * 1.0e-6 / math.sqrt(10.0**logp)
                }

            interface = solve_interface_oxygen(
                interface_pressure_model,
                (interface_channel,),
                reservoir_log10_fO2_bar=-12.0,
                melt_capacity_mol_per_ln_fO2=0.0,
                exchange_time_s=1.0,
                overhead_pO2_bar=0.0,
                area_m2=1.0,
                temperature_K=temperature,
                oxygen_alpha=0.8,
            )
            output[("interface_product", *label)] = (
                interface.product_fluxes_mol_m2_s["A"].hex()
            )
            output[("interface_oxygen", *label)] = (
                interface.gas_source_mol_s.hex()
            )

            condensation = _hkl_impingement_flux_mol_m2_s(
                "PIN",
                1200.0,
                temperature,
                vapor_pressure_data={
                    "metals": {
                        "PIN": {"molar_mass_g_mol": molar_mass * 1000.0}
                    }
                },
            )
            output[("condensation", *label)] = condensation.hex()

    expected = (
        "0x1.30574244fdae0p-6",
        "0x1.7c6d12d63d280p-2",
        "0x1.7c6d12d63d281p-3",
        "0x1.d59724a995919p+6",
        "0x1.9144d5660bfafp-8",
        "0x1.f5960abf8eedep-4",
        "0x1.f5960abf8eed2p-5",
        "0x1.637821a6caecep+4",
        "0x1.e6f203a19915ap-9",
        "0x1.30574244fdb9ap-4",
        "0x1.30574244fdb9cp-5",
        "0x1.5002f44c2f171p+3",
        "0x1.c5af3f52c6959p-7",
        "0x1.1b8d8793bc285p-2",
        "0x1.1b8d8793bc286p-3",
        "0x1.5e0313cf5bb7fp+6",
        "0x1.2b1691fec3a03p-8",
        "0x1.75dc367e747f7p-4",
        "0x1.75dc367e747edp-5",
        "0x1.08f37e489d570p+4",
        "0x1.6af2990f07a68p-9",
        "0x1.c5af3f52c6a6ep-5",
        "0x1.c5af3f52c6a72p-6",
        "0x1.f4e57c709f8a4p+2",
        "0x1.80f6d037f83edp-7",
        "0x1.e1348445f660dp-3",
        "0x1.e1348445f660fp-4",
        "0x1.28febb7be2293p+6",
        "0x1.fb91c74fbbe9ap-9",
        "0x1.3d3b1c91d56a7p-4",
        "0x1.3d3b1c91d56a1p-5",
        "0x1.c1a312b6f745ap+3",
        "0x1.33f8a6932f04bp-9",
        "0x1.80f6d037f84d7p-5",
        "0x1.80f6d037f84dap-6",
        "0x1.a90653b61f8ebp+2",
    )
    assert tuple(output.values()) == expected


def test_knudsen_effusion_is_alpha_one_langmuir():
    molar_mass = species_molar_mass_kg_mol("Na")
    T_K = 1700.0
    p_eq = 0.596
    assert knudsen_effusion_flux(
        "Na",
        T_K,
        p_eq,
        molar_mass_kg_mol=molar_mass,
    ) == pytest.approx(
        langmuir_flux(
            "Na",
            T_K,
            p_eq,
            0.0,
            1.0,
            molar_mass_kg_mol=molar_mass,
        ),
        rel=1e-12,
    )


def test_series_flux_reduces_to_langmuir_as_kn_goes_to_infinity():
    molar_mass = _K_BASE["molar_mass_kg_mol"]
    result = series_flux(
        species=_K_BASE["species"],
        p_eq_pa=_K_BASE["p_eq_pa"],
        p_bulk_pa=_K_BASE["p_bulk_pa"],
        T_surface_K=_K_BASE["T_surface_K"],
        molar_mass_kg_mol=molar_mass,
        alpha=_K_BASE["alpha"],
        knudsen_number=FREE_MOLECULAR_KNUDSEN_MIN * 1000.0,
        axial_stir_factor=0.0,
        melt_resistance_enabled=False,
        gas_resistance_enabled=False,
    )
    expected = langmuir_flux(
        _K_BASE["species"],
        _K_BASE["T_surface_K"],
        _K_BASE["p_eq_pa"],
        _K_BASE["p_bulk_pa"],
        _K_BASE["alpha"],
        molar_mass_kg_mol=molar_mass,
    )
    assert result.r_gas == 0.0
    assert result.r_melt == 0.0
    assert result.gas_resistance_weight == 0.0
    assert result.flux_kg_s_m2 == pytest.approx(expected, rel=1e-9)
    assert result.langmuir_flux_kg_s_m2 == pytest.approx(expected, rel=1e-12)


def test_series_flux_continuum_limit_is_gas_transport_limited():
    result = series_flux(
        species=_K_BASE["species"],
        p_eq_pa=_K_BASE["p_eq_pa"],
        p_bulk_pa=_K_BASE["p_bulk_pa"],
        T_surface_K=_K_BASE["T_surface_K"],
        molar_mass_kg_mol=_K_BASE["molar_mass_kg_mol"],
        alpha=1.0,
        knudsen_number=1.0e-7,
        overhead_pressure_pa=1000.0,
        axial_stir_factor=0.0,
        radial_stir_factor=1.0,
        melt_resistance_enabled=False,
    )
    delta_p = _K_BASE["p_eq_pa"] - _K_BASE["p_bulk_pa"]
    assert result.r_gas > result.r_interface * 50.0
    assert result.gas_resistance_weight == pytest.approx(1.0, rel=1e-5)
    assert result.flux_kg_s_m2 == pytest.approx(delta_p / result.r_gas, rel=0.02)
    assert result.flux_kg_s_m2 < result.langmuir_flux_kg_s_m2


def test_grounded_alpha_and_p_eq_match_provider_wiring():
    T_K = 1700.0
    alpha_na, _ = grounded_alpha("Na", T_K)
    alpha_k, _ = grounded_alpha("K", T_K)
    alpha_sio, _ = grounded_alpha("SiO", T_K)
    assert alpha_na == pytest.approx(1.0)
    assert alpha_k == pytest.approx(0.13)
    assert alpha_sio == pytest.approx(0.52 * math.exp(-3685.0 / T_K))
    # t-383: Na joins K/SiO on standard_reaction_term (L&H liquid-NaO0.5
    # coherent pair). Raw pseudo-Antoine evaluation would omit melt activity
    # and pO2 context — refuse and take effective P_eq from the builtin
    # vapor-pressure provider. Fe remains a pure-Antoine wiring probe.
    with pytest.raises(ValueError, match="standard_reaction_term"):
        pseudo_antoine_p_eq_pa("Na", T_K)
    with pytest.raises(ValueError, match="standard_reaction_term"):
        pseudo_antoine_p_eq_pa("SiO", T_K)
    alpha_fe, _ = grounded_alpha("Fe", T_K)
    p_fe = pseudo_antoine_p_eq_pa("Fe", T_K)
    assert p_fe > 0.0
    provider = _series_resistance_evaporation_flux_kg_m2_s(
        species="Fe",
        P_eq_pa=p_fe,
        P_bulk_pa=0.0,
        T_surface_K=T_K,
        molar_mass_kg_mol=species_molar_mass_kg_mol("Fe"),
        alpha_i=alpha_fe,
        knudsen_number=math.inf,
        axial_stir_factor=0.0,
        gas_resistance_enabled=False,
        melt_resistance_enabled=False,
    )
    assert provider.flux_kg_s_m2 == pytest.approx(
        langmuir_flux(
            "Fe",
            T_K,
            p_fe,
            0.0,
            alpha_fe,
            molar_mass_kg_mol=species_molar_mass_kg_mol("Fe"),
        ),
        rel=1e-12,
    )


def test_pseudo_antoine_rejects_k_standard_reaction_without_context():
    with pytest.raises(ValueError, match="standard_reaction_term"):
        pseudo_antoine_p_eq_pa("K", 1429.0)


@pytest.mark.parametrize(
    "missing", ("A", "B", "C"), ids=lambda key: f"missing_{key}"
)
def test_pseudo_antoine_missing_coefficient_is_refused(monkeypatch, missing):
    coefficients = {"A": 5.0, "B": 1000.0, "C": -200.0}
    del coefficients[missing]
    _patch_antoine_row(monkeypatch, coefficients)

    with pytest.raises(
        VaporPressureComputationError, match="missing_antoine_coefficient"
    ):
        pseudo_antoine_p_eq_pa("probe", 1000.0)


@pytest.mark.parametrize(
    ("coefficient", "value"),
    (("A", math.nan), ("B", math.inf), ("C", -math.inf)),
    ids=("A_nan", "B_pos_inf", "C_neg_inf"),
)
def test_pseudo_antoine_nonfinite_coefficient_is_refused(
    monkeypatch, coefficient, value
):
    coefficients = {"A": 5.0, "B": 1000.0, "C": -200.0}
    coefficients[coefficient] = value
    _patch_antoine_row(monkeypatch, coefficients)

    with pytest.raises(
        VaporPressureComputationError, match="nonfinite_antoine_coefficient"
    ):
        pseudo_antoine_p_eq_pa("probe", 1000.0)


@pytest.mark.parametrize(
    "temperature_K",
    (0.0, -1.0, math.nan, math.inf, -math.inf),
    ids=("zero", "negative", "nan", "pos_inf", "neg_inf"),
)
def test_pseudo_antoine_invalid_temperature_is_refused(monkeypatch, temperature_K):
    _patch_antoine_row(
        monkeypatch, {"A": 5.0, "B": 1000.0, "C": -200.0}
    )

    with pytest.raises(
        VaporPressureComputationError, match="invalid_antoine_temperature"
    ):
        pseudo_antoine_p_eq_pa("probe", temperature_K)


@pytest.mark.parametrize(
    ("a_coefficient", "expected_pressure_pa"),
    (
        (0.0, 0.03162277660168379),
        (-1.0, 0.0031622776601683794),
    ),
    ids=("zero_A", "negative_A"),
)
def test_pseudo_antoine_nonpositive_a_uses_finite_antoine_pressure(
    monkeypatch, a_coefficient, expected_pressure_pa
):
    _patch_antoine_row(
        monkeypatch,
        {"A": a_coefficient, "B": 1200.0, "C": -200.0},
    )

    assert pseudo_antoine_p_eq_pa("probe", 1000.0) == pytest.approx(
        expected_pressure_pa, rel=1e-15
    )


def test_validate_against_baseline_reports_ratios_without_tuning():
    rows = validate_against_baseline()
    assert len(rows) == 3
    by_species = {row.species: row for row in rows}
    for species in ("Cr", "K", "Fe"):
        row = by_species[species]
        assert row.measured_ratio_low <= row.modeled_langmuir_to_effusion_ratio
        assert row.modeled_langmuir_to_effusion_ratio <= row.measured_ratio_high
        assert abs(row.relative_error_percent) <= row.allowed_error_percent + 1e-12
        assert row.source

    assert by_species["Cr"].relative_error_percent == pytest.approx(0.0)
    assert by_species["K"].relative_error_percent == pytest.approx(0.0)
    assert by_species["Fe"].relative_error_percent == pytest.approx(
        29.0322580645
    )


def test_validation_observations_live_in_literature_sidecar():
    payload = yaml.safe_load(_VALIDATION_SIDECAR.read_text())
    assert payload["schema_version"] == "langmuir_knudsen_flux_validation.v1"
    assert len(payload["measurements"]) == 3
    for row in payload["measurements"].values():
        source = row["source"]
        assert source["citation_id"].startswith("REF-")
        assert source.get("doi") or source.get("url")
        assert source["extraction_note"]
        assert row["measured_langmuir_to_effusion_flux_ratio"]
