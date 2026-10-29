from __future__ import annotations

import pytest

from simulator import condensation
from simulator.state import CondensationTrain


def _stage3_efficiency(
    monkeypatch,
    *,
    band_flux_mol_m2_s: float,
    area_m2: float,
    rate_mol_s: float,
    residence_s: float,
    equilibrium_fraction: float = 0.8,
) -> float:
    pressure_pa = 100.0
    psat_pa = pressure_pa * (1.0 - equilibrium_fraction)
    monkeypatch.setattr(
        condensation,
        "_try_antoine_psat_pa",
        lambda *args, **kwargs: (pressure_pa, False),
    )
    monkeypatch.setattr(
        condensation,
        "_hkl_impingement_flux_mol_m2_s",
        lambda *args, **kwargs: 1.0,
    )

    def fixed_band_flux(*args, diagnostic_out=None, **kwargs):
        if diagnostic_out is not None:
            diagnostic_out.update({
                "wall_saturation_pressure_pa": psat_pa,
                "wall_saturation_pressure_refused": False,
            })
        return band_flux_mol_m2_s

    monkeypatch.setattr(
        condensation,
        "_series_resistance_deposition_flux_mol_m2_s",
        fixed_band_flux,
    )

    model = condensation.CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={"SiO": 0.5},
        stage_area_m2_by_stage={"3": area_m2},
    )
    model.wall_species_partial_pressures_pa = {"SiO": pressure_pa}
    stage = next(stage for stage in model.train.stages if stage.stage_number == 3)
    molar_mass_kg_mol = (
        condensation._molecular_mass_kg_per_molecule("SiO")
        * condensation.AVOGADRO_MOL
    )
    return model._condensation_efficiency(
        stage=stage,
        species="SiO",
        T_cond_C=1050.0,
        residence_s=residence_s,
        available_kg=rate_mol_s * molar_mass_kg_mol * 3600.0,
        alpha_s_value=1.0,
    )


def test_flow_capture_uses_hourly_rate_and_not_residence(monkeypatch):
    common = {
        "band_flux_mol_m2_s": 1e-8,
        "area_m2": 0.2,
    }
    one_mol_s = _stage3_efficiency(
        monkeypatch, **common, rate_mol_s=1.0, residence_s=1.0,
    )
    two_mol_s = _stage3_efficiency(
        monkeypatch, **common, rate_mol_s=2.0, residence_s=1.0,
    )
    long_residence = _stage3_efficiency(
        monkeypatch, **common, rate_mol_s=1.0, residence_s=8.0,
    )
    zero_residence = _stage3_efficiency(
        monkeypatch, **common, rate_mol_s=1.0, residence_s=0.0,
    )
    assert one_mol_s > 0.0
    assert two_mol_s / one_mol_s == pytest.approx(0.5, rel=1e-8)
    assert long_residence == pytest.approx(one_mol_s, rel=1e-8, abs=0.0)
    assert zero_residence == pytest.approx(one_mol_s, rel=1e-8, abs=0.0)


def test_flow_capture_matches_hand_computed_stage_value(monkeypatch):
    # 1 mol/s supply, 1 m2 at J=0.2 mol/m2/s, with f_eq=0.8:
    # 0.8 * (1 - exp(-0.2 / 0.8)) = 0.1769593735428761.
    eta = _stage3_efficiency(
        monkeypatch,
        band_flux_mol_m2_s=0.2,
        area_m2=1.0,
        rate_mol_s=1.0,
        residence_s=4.0,
    )

    assert eta == pytest.approx(0.1769593735428761, rel=1e-13)


def test_flow_capture_approaches_equilibrium_ceiling_at_large_area(monkeypatch):
    eta = _stage3_efficiency(
        monkeypatch,
        band_flux_mol_m2_s=0.2,
        area_m2=100.0,
        rate_mol_s=1.0,
        residence_s=4.0,
    )

    assert eta == pytest.approx(0.8, abs=1e-8)
    assert eta <= 0.8
