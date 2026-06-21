"""pO2 lever placement regressions for extraction vapor pressure + flux."""

from __future__ import annotations

import math

import pytest

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from engines.vaporock.provider import VapoRockProvider
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.melt_backend.base import EquilibriumResult
from simulator.state import MOLAR_MASS


_PO2_LEVELS = (1.0e-4, 1.0e-6, 1.0e-9)


def _account_view() -> ProviderAccountView:
    return ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "Na2O": 1.0,
                "K2O": 1.0,
                "FeO": 1.0,
                "MgO": 1.0,
                "CaO": 1.0,
                "MnO": 1.0,
                "Al2O3": 1.0,
                "Cr2O3": 1.0,
                "TiO2": 1.0,
                "SiO2": 1.0,
            }
        },
        species_formula_registry={},
    )


def _request(
    intent: ChemistryIntent,
    pO2_bar: float,
    *,
    temperature_C: float,
    control_inputs: dict | None = None,
    fO2_log: float | None = 0.0,
) -> IntentRequest:
    controls = dict(control_inputs or {})
    controls.setdefault("pO2_bar", pO2_bar)
    return IntentRequest(
        intent=intent,
        account_view=_account_view(),
        temperature_C=temperature_C,
        pressure_bar=1.0,
        fO2_log=fO2_log,
        control_inputs=controls,
    )


def _slope(xs: tuple[float, float, float], ys: tuple[float, float, float]) -> tuple[float, float]:
    return (
        math.log10(ys[1] / ys[0]) / math.log10(xs[1] / xs[0]),
        math.log10(ys[2] / ys[1]) / math.log10(xs[2] / xs[1]),
    )


@pytest.mark.parametrize(
    ("species", "temperature_C", "expected_slope"),
    [
        ("Na", 1100.0, -0.25),
        ("K", 1100.0, -0.25),
        ("Fe", 1100.0, -0.5),
        ("Mg", 1100.0, -0.5),
        ("Ca", 1100.0, -0.5),
        ("Mn", 1100.0, -0.5),
        ("Al", 1500.0, -0.75),
        ("Cr", 1500.0, -0.75),
        ("Ti", 1700.0, -1.0),
        ("SiO", 1300.0, -0.5),
        ("CrO2", 1300.0, 0.25),
    ],
)
def test_builtin_vapor_pressure_po2_slope_once(
    vapor_pressure_data,
    species: str,
    temperature_C: float,
    expected_slope: float,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    pressures = []
    for pO2_bar in _PO2_LEVELS:
        result = provider.dispatch(
            _request(
                ChemistryIntent.VAPOR_PRESSURE,
                pO2_bar,
                temperature_C=temperature_C,
            )
        )
        pressures.append(result.diagnostic["vapor_pressures_Pa"][species])

    for observed in _slope(_PO2_LEVELS, tuple(pressures)):
        assert observed == pytest.approx(expected_slope, abs=1.0e-9)


@pytest.mark.parametrize(
    ("species", "expected_slope"),
    [
        ("Na", -0.25),
        ("Fe", -0.5),
        ("Al", -0.75),
        ("Ti", -1.0),
        ("SiO", -0.5),
        ("CrO2", 0.25),
    ],
)
def test_evaporation_flux_preserves_vapor_pressure_po2_slope_once(
    species: str,
    expected_slope: float,
):
    provider = BuiltinEvaporationFluxProvider()
    rates = []
    for pO2_bar in _PO2_LEVELS:
        pressure_pa = 10.0 * (pO2_bar ** expected_slope)
        species_molar_mass = MOLAR_MASS[species] / 1000.0
        controls = {
            "vapor_pressures_Pa": {species: pressure_pa},
            "overhead_partials_Pa": {species: 0.0},
            "molar_mass_kg_mol": {species: species_molar_mass},
            "stoich_by_species": {
                species: {
                    "oxide_per_product_kg": 1.0,
                    "O2_per_product_kg": (
                        -expected_slope
                        * (MOLAR_MASS["O2"] / 1000.0)
                        / species_molar_mass
                    ),
                }
            },
            "available_oxide_kg": {species: 1.0e30},
            "melt_surface_area_m2": 1.0,
            "stir_factor": 1.0,
            "alpha": {"*": 1.0},
            "gas_pO2_bar": pO2_bar,
            "intrinsic_pO2_bar": 1.0e-4,
        }
        result = provider.dispatch(
            _request(
                ChemistryIntent.EVAPORATION_FLUX,
                pO2_bar,
                temperature_C=1500.0,
                control_inputs=controls,
            )
        )
        rates.append(result.diagnostic["evaporation_flux_kg_hr"][species])

    for observed in _slope(_PO2_LEVELS, tuple(rates)):
        assert observed == pytest.approx(expected_slope, abs=1.0e-9)


class _PDependentVapoRockBackend:
    def __init__(self) -> None:
        self.fO2_logs: list[float] = []

    def is_available(self) -> bool:
        return True

    def get_engine_version(self) -> str:
        return "test-double"

    def equilibrate(
        self,
        temperature_C,
        pressure_bar,
        composition_mol_by_account,
        species_formula_registry,
        fO2_log,
    ):
        self.fO2_logs.append(float(fO2_log))
        pO2_bar = 10.0 ** float(fO2_log)
        return EquilibriumResult(
            temperature_C=float(temperature_C),
            pressure_bar=float(pressure_bar),
            phase_assemblage_available=False,
            vapor_pressures_Pa={"SiO": pO2_bar ** -0.5},
            fO2_log=float(fO2_log),
            status="ok",
        )


def test_vaporock_provider_routes_commanded_po2_to_vapor_fO2(vapor_pressure_data):
    backend = _PDependentVapoRockBackend()
    provider = VapoRockProvider(
        backend=backend,
        vapor_pressure_data=vapor_pressure_data,
    )

    result = provider.dispatch(
        _request(
            ChemistryIntent.VAPOR_PRESSURE,
            1.0e-6,
            temperature_C=1500.0,
            fO2_log=0.0,
        )
    )

    assert result.status == "non_authoritative"
    assert backend.fO2_logs == [pytest.approx(-6.0)]
    assert result.diagnostic["pO2_bar"] == pytest.approx(1.0e-6)
    assert result.diagnostic["vapor_pressures_Pa"] == {}
    assert result.diagnostic["vaporock_full_speciation_Pa"]["SiO"] == pytest.approx(
        1000.0
    )


def test_vaporock_provider_vapor_po2_slope_once(vapor_pressure_data):
    backend = _PDependentVapoRockBackend()
    provider = VapoRockProvider(
        backend=backend,
        vapor_pressure_data=vapor_pressure_data,
    )

    pressures = []
    for pO2_bar in _PO2_LEVELS:
        result = provider.dispatch(
            _request(
                ChemistryIntent.VAPOR_PRESSURE,
                pO2_bar,
                temperature_C=1500.0,
                fO2_log=0.0,
            )
        )
        assert result.status == "non_authoritative"
        assert result.diagnostic["vapor_pressures_Pa"] == {}
        pressures.append(
            result.diagnostic["vaporock_full_speciation_Pa"]["SiO"]
        )

    for observed in _slope(_PO2_LEVELS, tuple(pressures)):
        assert observed == pytest.approx(-0.5, abs=1.0e-9)
