from pathlib import Path

import pytest
import yaml

from simulator.accounting import AccountingError
from simulator.chemistry.kernel import ChemistryIntent
from simulator.core import PyrolysisSimulator

from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import Atmosphere, CampaignPhase


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
MASS_BALANCE_LIMIT_PCT = 5.0e-12


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text()) or {}


def _force_builtin_vapor_pressure_fallback(sim: PyrolysisSimulator) -> None:
    provider = sim._chem_registry.authoritative_for(
        ChemistryIntent.VAPOR_PRESSURE
    )
    backend = getattr(provider, "_backend", None)
    if backend is not None and hasattr(backend, "is_available"):
        backend.is_available = lambda: False  # type: ignore[assignment]
    provider._ensure_backend = lambda: backend  # type: ignore[attr-defined]


def test_high_temp_fallback_routes_fe_as_metallic_fe_without_accountingerror():
    vapor_pressures = _load_yaml("vapor_pressures.yaml")
    assert "FeO_vapor" not in (vapor_pressures.get("oxide_vapors") or {})

    feedstocks = {
        "feo_regression": {
            "label": "FeO high-temperature regression feedstock",
            "composition_wt_pct": {
                "SiO2": 20.0,
                "FeO": 45.0,
                "MgO": 20.0,
                "Al2O3": 10.0,
                "CaO": 5.0,
            },
        }
    }
    setpoints = {
        "campaigns": {},
        "chemistry_kernel": {"allow_fallback_vapor": True},
    }
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(backend, setpoints, feedstocks, vapor_pressures)
    sim.load_batch("feo_regression", mass_kg=1000.0)
    sim.melt.campaign = CampaignPhase.C2A
    sim.melt.atmosphere = Atmosphere.HARD_VACUUM
    sim.melt.temperature_C = 1600.0
    sim.melt.pO2_mbar = 0.0
    _force_builtin_vapor_pressure_fallback(sim)

    try:
        equilibrium = sim._get_equilibrium()
        flux = sim._calculate_evaporation(equilibrium)
        flux = sim._apply_analytic_evaporation_depletion(flux)
        sim._route_to_condensation(flux)
        sim._update_melt_composition(flux)
    except AccountingError as exc:
        pytest.fail(f"high-temperature Fe fallback raised AccountingError: {exc}")

    assert "FeO_vapor" not in equilibrium.vapor_pressures_Pa
    assert "FeO_vapor" not in flux.species_kg_hr
    assert flux.species_kg_hr.get("Fe", 0.0) > 0.0

    products = sim.train.total_by_species()
    assert products.get("Fe", 0.0) > 0.0
    assert products.get("FeO_vapor", 0.0) == 0.0

    for refractory_oxide in ("Al2O3", "MgO", "CaO"):
        assert sim.melt.composition_kg.get(refractory_oxide, 0.0) > 0.0

    snapshot = sim._make_snapshot()
    assert abs(snapshot.mass_balance_error_pct) <= MASS_BALANCE_LIMIT_PCT


def test_internal_analytical_equilibrium_feo_activity_ignores_neutral_total_pressure():
    # Kress91 pressure terms are pressure-sensitive redox-split corrections.
    # Legacy vapor equilibrium uses fixed T/fO2/composition FeO activity, not
    # neutral overhead p_total.
    vapor_pressures = _load_yaml("vapor_pressures.yaml")
    feedstocks = {
        "feo_regression": {
            "label": "FeO neutral-pressure regression feedstock",
            "composition_wt_pct": {
                "SiO2": 20.0, "FeO": 45.0, "MgO": 20.0, "Al2O3": 10.0, "CaO": 5.0,
            },
        }
    }
    setpoints = {
        "campaigns": {},
        "chemistry_kernel": {"allow_fallback_vapor": True},
    }
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(backend, setpoints, feedstocks, vapor_pressures)
    sim.load_batch("feo_regression", mass_kg=1000.0)
    sim.melt.campaign = CampaignPhase.C2A
    sim.melt.atmosphere = Atmosphere.HARD_VACUUM
    sim.melt.temperature_C = 1600.0
    sim.melt.pO2_mbar = 0.0
    _force_builtin_vapor_pressure_fallback(sim)

    p_eq_by_total_mbar: dict[float, dict[str, float]] = {}
    for p_total_mbar in (5.0, 10.0, 15.0):
        sim.melt.p_total_mbar = p_total_mbar
        equilibrium = sim._internal_analytical_equilibrium()
        p_eq_by_total_mbar[p_total_mbar] = {
            species: equilibrium.vapor_pressures_Pa[species]
            for species in ("Fe", "SiO")
        }

    reference = p_eq_by_total_mbar[5.0]
    assert p_eq_by_total_mbar[10.0] == reference
    assert p_eq_by_total_mbar[15.0] == reference
