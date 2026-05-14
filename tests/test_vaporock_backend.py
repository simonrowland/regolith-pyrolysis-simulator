import types
import warnings
from pathlib import Path

import pytest
import yaml

import simulator.melt_backend.vaporock as vaporock_module
from simulator.accounting.formulas import resolve_species_formula
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import (
    DEFAULT_BACKEND_CAPABILITIES,
    StubBackend,
)
from simulator.melt_backend.vaporock import VapoRockBackend
from simulator.state import OXIDE_SPECIES


def _install_fake_import(monkeypatch, fake_module):
    calls = []

    def fake_import_module(name):
        calls.append(name)
        if name == "vaporock":
            return fake_module
        raise ImportError(name)

    monkeypatch.setattr(
        vaporock_module.importlib, "import_module", fake_import_module
    )
    return calls


def _expected_wt_pct(composition_mol):
    kg_by_species = {
        species: mol * resolve_species_formula(species).molar_mass_kg_per_mol()
        for species, mol in composition_mol.items()
        if species in {"SiO2", "Na2O"}
    }
    total = sum(kg_by_species.values())
    return {
        species: kg / total * 100.0
        for species, kg in kg_by_species.items()
    }


def test_missing_vaporock_import_marks_backend_unavailable(monkeypatch):
    def fake_import_module(name):
        raise ImportError(name)

    monkeypatch.setattr(
        vaporock_module.importlib, "import_module", fake_import_module
    )
    backend = VapoRockBackend()

    with pytest.warns(UserWarning, match="VapoRock not available"):
        assert backend.initialize({}) is False

    assert backend.is_available() is False
    assert backend._last_error is not None
    assert "vaporock" in backend._last_error
    assert "VapoRock" in backend._last_error


def test_unavailable_equilibrate_returns_empty_result_with_warning():
    backend = VapoRockBackend()

    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.vapor_pressures_Pa == {}
    assert result.phases_present == []
    assert result.warnings == ["VapoRock backend not initialized"]


def test_capability_extension_is_instance_local():
    backend = VapoRockBackend()
    caps = backend.capabilities()

    assert "vapor_melt_equilibrium" not in DEFAULT_BACKEND_CAPABILITIES
    assert caps["silicate_melt"] is False
    assert caps["gas_volatiles"] is True
    assert caps["vapor_melt_equilibrium"] is True
    assert backend.capability_summary() == (
        "gas volatiles, vapor melt equilibrium"
    )


def test_fake_vaporock_receives_oxide_wt_pct_basis(monkeypatch):
    seen = {}

    def calc_vapor_pressures(**kwargs):
        seen.update(kwargs)
        return {"Na": 1e-4, "SiO": 1e-6}

    fake_module = types.SimpleNamespace(
        calc_vapor_pressures=calc_vapor_pressures
    )
    import_calls = _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({}) is True
    result = backend.equilibrate(
        1550.0,
        composition_mol={
            "SiO2": 1.0,
            "Na2O": 0.25,
            "Fe": 10.0,
            "FeS": 2.0,
            "NaCl": 3.0,
        },
        fO2_log=-8.25,
        pressure_bar=2e-6,
    )

    expected = _expected_wt_pct({"SiO2": 1.0, "Na2O": 0.25})
    assert import_calls == ["vaporock"]
    assert seen["composition"].keys() == expected.keys()
    assert seen["composition"]["SiO2"] == pytest.approx(expected["SiO2"])
    assert seen["composition"]["Na2O"] == pytest.approx(expected["Na2O"])
    assert result.vapor_pressures_Pa == {
        "Na": pytest.approx(10.0),
        "SiO": pytest.approx(0.1),
    }


def test_fake_vaporock_receives_fo2_temperature_and_pressure(monkeypatch):
    seen = {}

    def calc_vapor_pressures(**kwargs):
        seen.update(kwargs)
        return {"Na": 2500.0}

    fake_module = types.SimpleNamespace(
        calc_vapor_pressures=calc_vapor_pressures
    )
    _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({"temperature_units": "K", "pressure_units": "Pa"})
    result = backend.equilibrate(
        1400.0,
        composition_mol={"SiO2": 1.0},
        fO2_log=-7.5,
        pressure_bar=0.012,
    )

    assert seen["T_C"] is None
    assert seen["T_K"] == pytest.approx(1673.15)
    assert seen["P_bar"] is None
    assert seen["P_Pa"] == pytest.approx(1200.0)
    assert seen["log_fO2"] == pytest.approx(-7.5)
    assert result.vapor_pressures_Pa == {"Na": pytest.approx(2500.0)}


def test_passthrough_pa_values_when_pressures_already_look_like_pa(monkeypatch):
    def calc_vapor_pressures(**kwargs):
        return {"Na": 1500.0}

    fake_module = types.SimpleNamespace(
        calc_vapor_pressures=calc_vapor_pressures
    )
    _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({})
    result = backend.equilibrate(
        1500.0,
        composition_mol={"Na2O": 1.0},
        pressure_bar=1e-6,
    )

    assert result.vapor_pressures_Pa == {"Na": pytest.approx(1500.0)}


def test_canonical_system_entrypoint_converts_log10_bar_to_pa(monkeypatch):
    class FakeSystem:
        instances = []

        def __init__(self):
            self.melt_compositions = []
            self.eval_calls = []
            FakeSystem.instances.append(self)

        def set_melt_comp(self, composition):
            self.melt_compositions.append(dict(composition))

        def eval_gas_abundances(self, temperature, log_fO2):
            self.eval_calls.append((temperature, log_fO2))
            # The installed VapoRock build labels every gas species with
            # a "(g)" phase suffix; the adapter normalizes them onto the
            # simulator's bare-name vocabulary.
            return {"Na(g)": -2.0, "SiO(g)": -6.0}

    fake_module = types.SimpleNamespace(System=FakeSystem)
    _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({})
    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0, "Na2O": 0.1},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    system = FakeSystem.instances[0]
    assert system.melt_compositions[0]["SiO2"] > 0.0
    # VapoRock's System.eval_gas_abundances expects an absolute
    # temperature in Kelvin; the adapter converts 1600 C -> 1873.15 K.
    assert system.eval_calls == [(pytest.approx(1873.15), -8.0)]
    # "(g)"-suffixed VapoRock species names are normalized to bare names.
    assert result.vapor_pressures_Pa == {
        "Na": pytest.approx(1000.0),
        "SiO": pytest.approx(0.1),
    }


def test_vaporock_shadow_parity_with_builtin_antoine_for_basalt():
    backend = VapoRockBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("VapoRock optional dependency unavailable")

    repo_root = Path(__file__).resolve().parents[1]
    vapor_pressures = yaml.safe_load(
        (repo_root / "data" / "vapor_pressures.yaml").read_text()
    )
    feedstocks = {
        "basalt_analog": {
            "label": "VapoRock parity basalt analog",
            "composition_wt_pct": {
                "SiO2": 49.0,
                "TiO2": 2.0,
                "Al2O3": 15.0,
                "FeO": 10.0,
                "MgO": 8.0,
                "CaO": 11.0,
                "Na2O": 3.0,
                "K2O": 1.0,
                "P2O5": 1.0,
            },
        }
    }
    sim = PyrolysisSimulator(
        StubBackend(), {"campaigns": {}}, feedstocks, vapor_pressures
    )
    sim.load_batch("basalt_analog", mass_kg=1000.0)
    sim.melt.temperature_C = 1600.0
    sim.melt.p_total_mbar = 1e-3
    sim.melt.pO2_mbar = 1e-6

    builtin = sim._stub_equilibrium()
    vaporock = backend.equilibrate(
        sim.melt.temperature_C,
        composition_mol=sim._backend_composition_mol(),
        fO2_log=builtin.fO2_log,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
    )

    if not vaporock.vapor_pressures_Pa:
        pytest.skip("VapoRock returned no vapor pressures for parity case")

    # The adapter normalizes VapoRock's "(g)"-suffixed species names
    # ("Na(g)", "SiO(g)") onto the simulator's bare vocabulary, so the
    # builtin Antoine path and the VapoRock path now share keys. Per
    # `\goal VAPOROCK-COMPLETION`, agreement within an order of magnitude
    # for at least one common volatile species is sufficient.
    for species in ("Na", "SiO"):
        builtin_pressure = builtin.vapor_pressures_Pa.get(species, 0.0)
        vaporock_pressure = vaporock.vapor_pressures_Pa.get(species, 0.0)
        if builtin_pressure > 0.0 and vaporock_pressure > 0.0:
            ratio = vaporock_pressure / builtin_pressure
            assert 0.1 <= ratio <= 10.0
            return

    pytest.fail(
        "No common Na/SiO vapor-pressure key between VapoRock and builtin "
        "Antoine after species-name normalization; the parity fixture has "
        "nothing to compare."
    )


def test_vaporock_as_active_backend_fails_closed_with_clear_message():
    # VapoRock is not wired into any active call site. If someone DOES
    # select it as the active melt backend, core.py must fail closed with
    # a clear message rather than silently proceeding -- the adapter
    # docstring's "diagnostic" claim only holds for a dedicated vapor-side
    # consumer, never for the authoritative _get_equilibrium path.
    sim = PyrolysisSimulator(
        VapoRockBackend(),
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 100.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide", mass_kg=1.0)

    # A bare VapoRockBackend() is un-initialized (the simulator
    # constructor never calls initialize()), so is_available() is False
    # and core.py refuses to fall back to the stub for a non-stub
    # backend.
    with pytest.raises(RuntimeError, match="VapoRockBackend is unavailable"):
        sim.step()


def test_vaporock_gas_oxide_names_do_not_collide_with_melt_oxides(monkeypatch):
    # VapoRock returns gas species with a "(g)" suffix; stripping it
    # naively maps SiO2(g)/Fe2O3(g) onto the SAME strings as the condensed
    # melt oxides in OXIDE_SPECIES. The normalizer must namespace those so
    # a downstream vapor consumer cannot conflate gaseous SiO2 with melt
    # SiO2 (which would break SiO2 -> SiO + 1/2 O2 stoichiometry).
    def calc_vapor_pressures(**kwargs):
        return {
            "SiO2(g)": 1.0e-4,
            "Fe2O3(g)": 2.0e-4,
            "FeO(g)": 3.0e-4,
            "MgO(g)": 4.0e-4,
            "CaO(g)": 5.0e-4,
            "MnO(g)": 6.0e-4,
            # Non-oxide gas species stay bare so the builtin Antoine path
            # and the VapoRock path still share keys.
            "Na(g)": 7.0e-4,
            "SiO(g)": 8.0e-4,
            "Al2O(g)": 9.0e-4,
        }

    fake_module = types.SimpleNamespace(
        calc_vapor_pressures=calc_vapor_pressures
    )
    _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({}) is True
    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0, "FeO": 0.2},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    keys = set(result.vapor_pressures_Pa)
    # The whole point: no normalized vapor key is also a melt oxide name.
    assert keys.isdisjoint(OXIDE_SPECIES), (
        f"gas keys collide with melt oxides: {keys & set(OXIDE_SPECIES)}"
    )
    # Oxide-colliding gas species are namespaced with _gas.
    assert "SiO2_gas" in keys
    assert "Fe2O3_gas" in keys
    assert "FeO_gas" in keys
    assert "MgO_gas" in keys
    assert "CaO_gas" in keys
    assert "MnO_gas" in keys
    # Non-oxide gas species stay bare.
    assert "Na" in keys
    assert "SiO" in keys
    assert "Al2O" in keys
    # get_vapor_species() advertises exactly the normalizer's vocabulary.
    advertised = set(backend.get_vapor_species())
    assert advertised.isdisjoint(OXIDE_SPECIES)
    assert {"SiO2_gas", "Fe2O3_gas", "FeO_gas",
            "MgO_gas", "CaO_gas", "MnO_gas"} <= advertised
