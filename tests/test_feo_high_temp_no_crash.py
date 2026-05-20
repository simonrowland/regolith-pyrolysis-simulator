from pathlib import Path

import pytest
import yaml

from simulator.accounting import AccountingError
from simulator.chemistry.kernel import ChemistryIntent
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
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
    backend = StubBackend()
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
