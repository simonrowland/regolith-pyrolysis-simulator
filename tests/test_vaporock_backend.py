import math
import types
import warnings
from pathlib import Path

import pytest
import yaml

from engines.vaporock import VapoRockProvider
import simulator.melt_backend.vaporock as vaporock_module
from simulator.accounting.formulas import resolve_species_formula
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.core import PyrolysisSimulator
from engines.domain_reason import OutOfDomainReason
from simulator.melt_backend.base import (
    DEFAULT_BACKEND_CAPABILITIES,
    InternalAnalyticalBackend,
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


def _vaporock_diagnostic_pressures(result):
    return dict(
        getattr(result, "vaporock_full_speciation_Pa", {})
        or result.vapor_pressures_Pa
        or {}
    )


# §25 calibration grid retired here under \\goal CHEMISTRY-E2E-TEST-REGIME
# (chunk 20/Phase-A): the (T, melt, species, reference_Pa) anchor table
# and the per-anchor max-error envelope now live in
# ``tests/chemistry/corpus_fixtures.py`` (the table) and
# ``tests/chemistry/test_corpus_anchored_parity.py`` (the envelope), so
# the corpus-driven framework owns the residual story. The framework's
# §25-cohort acceptance is invoked from this file via
# ``test_vaporock_iw_literature_grid_residuals_are_explicit`` below as a
# thin shim that delegates to the new corpus-anchored test machinery —
# the §25 v1 behaviour (grid evaluated at intrinsic IW fO2, residuals
# capped to documented envelope, new failures rejected) is preserved
# bit-for-bit, and a new ``benchmark-fixture.yaml`` in the corpus auto-
# extends the test surface (see
# ``tests/chemistry/test_corpus_anchored_parity.py::test_loader_auto_extends_to_new_fixture``).


def test_missing_vaporock_import_marks_backend_unavailable(monkeypatch):
    def fake_import_module(name):
        raise ImportError(name)

    monkeypatch.setattr(
        vaporock_module.importlib, "import_module", fake_import_module
    )
    backend = VapoRockBackend()

    with pytest.warns(UserWarning, match="VapoRock not available"):
        assert backend.initialize({'warm_worker': False}) is False

    assert backend.is_available() is False
    assert backend._last_error is not None
    assert "vaporock" in backend._last_error
    assert "VapoRock" in backend._last_error


def test_runtime_probe_uses_backend_initialize_without_equilibrating(monkeypatch):
    init_calls = []
    equilibrate_calls = []

    def fake_initialize(self, config):
        init_calls.append(dict(config))
        self._available = True
        return True

    def fake_equilibrate(self, *args, **kwargs):
        equilibrate_calls.append((args, kwargs))
        raise AssertionError("runtime availability probe must not solve equilibrium")

    monkeypatch.setattr(
        vaporock_module.VapoRockBackend,
        "initialize",
        fake_initialize,
    )
    monkeypatch.setattr(
        vaporock_module.VapoRockBackend,
        "equilibrate",
        fake_equilibrate,
    )

    vaporock_module.vaporock_runtime_available.cache_clear()
    try:
        assert vaporock_module.vaporock_runtime_available() is True
        assert vaporock_module.vaporock_runtime_available() is True
    finally:
        vaporock_module.vaporock_runtime_available.cache_clear()

    assert init_calls == [{"warm_worker": False}]
    assert equilibrate_calls == []


def test_runtime_probe_does_not_cache_negative_availability(monkeypatch):
    init_results = [False, True]
    init_calls = []

    def fake_initialize(self, config):
        init_calls.append(dict(config))
        available = init_results.pop(0)
        self._available = available
        return available

    monkeypatch.setattr(
        vaporock_module.VapoRockBackend,
        "initialize",
        fake_initialize,
    )

    vaporock_module.vaporock_runtime_available.cache_clear()
    try:
        assert vaporock_module.vaporock_runtime_available() is False
        assert vaporock_module.vaporock_runtime_available() is True
        assert vaporock_module.vaporock_runtime_available() is True
    finally:
        vaporock_module.vaporock_runtime_available.cache_clear()

    assert init_calls == [
        {"warm_worker": False},
        {"warm_worker": False},
    ]


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
    assert result.status == "unavailable"


def test_empty_melt_composition_marks_status_out_of_domain(monkeypatch):
    # A composition with no oxides in VapoRock's basis (only native Fe /
    # sulfide / halide species) collapses to an empty wt% projection. The
    # adapter labels this 'out_of_domain' -- the engine has nothing valid
    # to act on, not a runtime convergence failure.
    fake_module = types.SimpleNamespace(
        calc_vapor_pressures=lambda **_: {"Na": 1.0}
    )
    _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({'warm_worker': False}) is True
    result = backend.equilibrate(
        1600.0,
        composition_mol={"Fe": 1.0, "FeS": 0.5, "NaCl": 0.2},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == (
        OutOfDomainReason.FORBIDDEN_SPECIES.value
    )
    assert any("refused projected/dropped non-basis" in w for w in result.warnings)
    assert any("dropped_non_basis_melt_mass" in w for w in result.warnings)
    assert result.diagnostics["dropped_non_basis_melt_mass_kg"] > 0.0


def test_library_exception_marks_status_not_converged(monkeypatch):
    # A library-boundary exception is caught and surfaced as a warning on
    # an otherwise-empty result; the result is labelled 'not_converged'
    # (the engine ran but did not produce a usable answer).
    def boom(**_):
        raise RuntimeError("upstream vaporock convergence failure")

    fake_module = types.SimpleNamespace(calc_vapor_pressures=boom)
    _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({'warm_worker': False}) is True
    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0, "Na2O": 0.1},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.status == "not_converged"
    assert any("VapoRock equilibrate failed" in w for w in result.warnings)


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
    assert backend.initialize({'warm_worker': False}) is True
    result = backend.equilibrate(
        1550.0,
        composition_mol={"SiO2": 1.0, "Na2O": 0.25},
        fO2_log=-8.25,
        pressure_bar=2e-6,
    )

    expected = _expected_wt_pct({"SiO2": 1.0, "Na2O": 0.25})
    assert import_calls == ["vaporock"]
    assert seen["composition"].keys() == expected.keys()
    assert seen["composition"]["SiO2"] == pytest.approx(expected["SiO2"])
    assert seen["composition"]["Na2O"] == pytest.approx(expected["Na2O"])
    full = getattr(result, "vaporock_full_speciation_Pa", {})
    assert full == {
        "Na": pytest.approx(10.0),
        "SiO": pytest.approx(0.1),
    }
    assert result.vapor_pressures_Pa == {}
    assert result.status == "non_authoritative"
    assert result.liquid_fraction is None
    assert result.phase_assemblage_available is False
    assert "input_composition_projection" not in result.diagnostics


def test_vaporock_non_basis_projection_is_out_of_domain(monkeypatch):
    seen = {}

    def calc_vapor_pressures(**kwargs):
        seen.update(kwargs)
        return {"Na": 1e-4, "SiO": 1e-6}

    fake_module = types.SimpleNamespace(
        calc_vapor_pressures=calc_vapor_pressures
    )
    _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({'warm_worker': False}) is True
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

    assert seen == {}
    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == (
        OutOfDomainReason.FORBIDDEN_SPECIES.value
    )
    projection = result.diagnostics["input_composition_projection"]
    assert projection["status"] == "projected"
    assert projection["reason"] == "input_composition_projected"
    assert projection["backend"] == "VapoRock"
    assert projection["dropped_species"] == ["Fe", "FeS", "NaCl"]
    assert projection["renormalization_delta"] > 0.0


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
    assert backend.initialize({
        "temperature_units": "K",
        "pressure_units": "Pa",
        "vapor_pressure_units": "Pa",
        "warm_worker": False,
    })
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
    assert result.vapor_pressures_Pa == {}
    assert getattr(result, "vaporock_full_speciation_Pa", {}) == {
        "Na": pytest.approx(2500.0)
    }
    assert result.status == "non_authoritative"


def test_vaporock_control_audit_reports_transport_redox_separately():
    seen = {}

    class FakeBackend:
        def is_available(self):
            return True

        def get_engine_version(self):
            return "fake-vaporock"

        def equilibrate(self, **kwargs):
            seen.update(kwargs)
            return types.SimpleNamespace(
                vapor_pressures_Pa={"Na": 2.0},
                vaporock_full_speciation_Pa={"Na": 2.0},
                warnings=(),
                status="ok",
            )

    provider = VapoRockProvider(backend=FakeBackend(), vapor_pressure_data={})
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"Na2O": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=1600.0,
        pressure_bar=1e-6,
        fO2_log=-8.0,
        control_inputs={"pO2_bar": 1e-6, "intrinsic_fO2_log": -8.0},
    )

    result = provider.dispatch(request)

    assert seen["fO2_log"] == pytest.approx(-6.0)
    audit = result.control_audit
    assert audit.requested["fO2_log"] == pytest.approx(-8.0)
    assert audit.requested["intrinsic_fO2_log"] == pytest.approx(-8.0)
    assert audit.requested["transport_pO2_bar"] == pytest.approx(1e-6)
    assert audit.applied["fO2_log"] == pytest.approx(seen["fO2_log"])
    assert audit.applied["transport_fO2_log"] == pytest.approx(seen["fO2_log"])
    assert audit.applied["intrinsic_fO2_log"] == pytest.approx(-8.0)
    assert audit.applied["transport_pO2_bar"] == pytest.approx(1e-6)
    assert "transport gas fO2" in audit.notes[0]


def test_passthrough_pa_values_when_unit_declared_pa(monkeypatch):
    # With vapor_pressure_units='Pa' the upstream dict result is taken as
    # Pa verbatim -- no magnitude heuristic, no 1e5x inflation.
    def calc_vapor_pressures(**kwargs):
        return {"Na": 1500.0}

    fake_module = types.SimpleNamespace(
        calc_vapor_pressures=calc_vapor_pressures
    )
    _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({"vapor_pressure_units": "Pa", 'warm_worker': False})
    result = backend.equilibrate(
        1500.0,
        composition_mol={"Na2O": 1.0},
        pressure_bar=1e-6,
    )

    assert result.vapor_pressures_Pa == {}
    assert getattr(result, "vaporock_full_speciation_Pa", {}) == {
        "Na": pytest.approx(1500.0)
    }
    assert result.status == "non_authoritative"


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
    assert backend.initialize({'warm_worker': False})
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
    assert result.vapor_pressures_Pa == {}
    assert getattr(result, "vaporock_full_speciation_Pa", {}) == {
        "Na": pytest.approx(1000.0),
        "SiO": pytest.approx(0.1),
    }


def test_system_entrypoint_marks_pressure_non_authoritative(monkeypatch):
    class FakeSystem:
        def set_melt_comp(self, composition):
            self.composition = dict(composition)

        def eval_gas_abundances(self, temperature, log_fO2):
            return {"Na(g)": -2.0}

    fake_module = types.SimpleNamespace(System=FakeSystem)
    _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({'warm_worker': False})
    result = backend.equilibrate(
        1600.0,
        composition_mol={"Na2O": 1.0},
        fO2_log=-8.0,
        pressure_bar=42.0,
    )

    assert result.status == "non_authoritative"
    assert result.vapor_pressures_Pa == {}
    assert result.pressure_bar == pytest.approx(42.0)
    assert result.diagnostics["pressure_control_authoritative"] is False
    assert result.diagnostics["requested_pressure_bar"] == pytest.approx(42.0)
    assert any("eval_gas_abundances ignores total pressure" in w for w in result.warnings)


def test_core_does_not_consume_non_authoritative_vaporock_pressures(monkeypatch):
    class FakeSystem:
        def set_melt_comp(self, composition):
            self.composition = dict(composition)

        def eval_gas_abundances(self, temperature, log_fO2):
            return {"Na(g)": -2.0}

    fake_module = types.SimpleNamespace(System=FakeSystem)
    _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({'warm_worker': False})
    provider = VapoRockProvider(backend=backend, vapor_pressure_data={})

    sim = object.__new__(PyrolysisSimulator)
    sim.melt = types.SimpleNamespace(temperature_C=1600.0, melt_fO2_log=-8.0)
    sim._allow_fallback_vapor = True
    sim._commanded_pO2_bar = lambda: 1e-6
    sim._compute_intrinsic_melt_fO2 = lambda: -8.0

    def dispatch_only(intent, *, control_inputs, fO2_log):
        request = IntentRequest(
            intent=intent,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": {"Na2O": 1.0}},
                species_formula_registry={},
            ),
            temperature_C=sim.melt.temperature_C,
            pressure_bar=1e-6,
            fO2_log=fO2_log,
            control_inputs=control_inputs,
        )
        return provider.dispatch(request)

    sim._dispatch_only = dispatch_only
    result = backend.equilibrate(
        1600.0,
        composition_mol={"Na2O": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )
    assert result.status == "non_authoritative"
    assert result.vapor_pressures_Pa == {}
    assert getattr(result, "vaporock_full_speciation_Pa", {})["Na"] == pytest.approx(1000.0)

    sim._refresh_vapor_pressures_from_kernel(result)

    assert result.vapor_pressures_Pa == {}
    diagnostic = sim._last_vapor_pressure_diagnostic
    assert diagnostic["vapor_pressures_Pa"] == {}
    assert diagnostic["vaporock_full_speciation_Pa"]["Na"] == pytest.approx(1000.0)


def test_vaporock_shadow_parity_with_builtin_antoine_for_basalt():
    """VapoRock literature anchors plus same-basis builtin shadow parity.

    \\goal VAPOROCK-SIO-DIVERGENCE (chunk 24/Phase-2). This test replaces the
    earlier first-agreeing-species short-circuit comparison with explicit
    SiO + Na assertions against literature anchors. The short-circuit was
    hiding a 3.4-decade SiO divergence between the builtin Antoine and
    VapoRock; the §13 archive flagged this as the load-bearing diagnostic
    parity question. Builtin remains authoritative.

    The Phase 1 investigation (``docs-private/sio-parity-investigation-
    2026-05-16.md``) established three facts that shape this test:

    1. **VapoRock is the right tool.** It solves the full melt-vapor
       equilibrium (MELTS-style activity + JANAF gas thermo, same
       foundation as SF2004's MAGMA code). The builtin Antoine path is
       a per-species saturation fit and cannot do equilibrium gas
       speciation. The fO2 convention passed to ``eval_gas_abundances``
       (= ``log10(fO2/bar)``) is correctly mapped by the adapter
       (verified 2026-05-16 against ``vaporock/equil.py::System.equilibrate``
       which calls ``redox_buffer`` for an absolute logfO2 + passes it to
       ``eval_gas_abundances`` directly).

    2. **The fO2/pO2 channels are now separate.** The t-333 basis split
       keeps intrinsic melt fO2 separate from headspace transport pO2.
       The kernel feeds the VapoRock shadow headspace transport pO2,
       converted to gas ``fO2_log``; intrinsic melt fO2 is retained only
       as redox provenance. Builtin SiO suppression also reads transport
       pO2, while its result's ``fO2_log`` reports intrinsic melt redox.
       Reusing that result field for the shadow was the retired premise.

    3. **Parity must compare like with like.** VapoRock is checked against
       literature at IW, then builtin and VapoRock are compared twice with
       both channels deliberately aligned: once at IW and once at the
       1e-9-bar transport floor. Their SiO pressures must agree within
       0.45 decade on each common basis. This tests actual provider
       parity instead of the old VapoRock-to-itself ratio.
    """
    backend = VapoRockBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({'warm_worker': False})
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
        InternalAnalyticalBackend(), {"campaigns": {}}, feedstocks, vapor_pressures
    )
    sim.load_batch("basalt_analog", mass_kg=1000.0)
    sim.melt.temperature_C = 1600.0
    sim.melt.p_total_mbar = 1e-3
    sim.melt.pO2_mbar = 1e-6

    # ------------------------------------------------------------------
    # Regime A: intrinsic fO2 (IW-like) -- literature comparison
    # ------------------------------------------------------------------
    fO2_log_iw = sim._compute_intrinsic_melt_fO2(
        sim.melt.temperature_C + 273.15
    )

    vaporock_iw = backend.equilibrate(
        sim.melt.temperature_C,
        composition_mol=sim._backend_composition_mol(),
        fO2_log=fO2_log_iw,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
    )

    vaporock_iw_pressures = _vaporock_diagnostic_pressures(vaporock_iw)
    if not vaporock_iw_pressures:
        pytest.skip(
            "VapoRock returned no diagnostic vapor pressures at IW; "
            "library available but produced empty result"
        )

    # SF2004 Table 9 (back-solved via Hertz-Knudsen): p(SiO) = 0.0131 Pa
    # at 1900 K, tholeiite, MAGMA self-consistent fO2. At 1873.15 K the
    # value is slightly lower. VapoRock at IW for our parity basalt
    # gives ~0.36 Pa (1.4 decades above SF2004), which sits inside the
    # combined model-spread + temperature-offset tolerance. Sossi-Fegley
    # 2018 Fig 3 graphical readout for lunar basalt 12022 gives
    # p(SiO) ~ 0.04-0.16 Pa at 1900 K; widening to the full literature
    # range gives [0.005, 1.0] Pa as the literature-anchored target.
    p_sio_iw = vaporock_iw_pressures.get("SiO", 0.0)
    assert 0.005 <= p_sio_iw <= 1.0, (
        f"VapoRock p(SiO) at IW (logfO2={fO2_log_iw}) = {p_sio_iw:.4e} Pa "
        f"is outside the literature-anchored range [0.005, 1.0] Pa "
        f"(SF2004 anchor 0.0131 Pa; Sossi-Fegley 2018 graphical 0.04-0.16 "
        f"Pa). This indicates a real VapoRock thermodynamic divergence "
        f"from the MAGMA / MELTS+JANAF literature, NOT an adapter "
        f"convention issue."
    )

    # SF2004 Table 9 (back-solved): p(Na) = 6.0 Pa at 1900 K, tholeiite.
    # VapoRock at IW for our parity basalt gives ~34 Pa, about 0.75
    # decade above SF2004's number. We allow [1, 200] Pa to span the
    # MELTS-vs-MAGMA Na-activity spread plus the basalt-composition
    # offset between parity-test and Williams tholeiite.
    p_na_iw = vaporock_iw_pressures.get("Na", 0.0)
    assert 1.0 <= p_na_iw <= 200.0, (
        f"VapoRock p(Na) at IW (logfO2={fO2_log_iw}) = {p_na_iw:.4e} Pa "
        f"is outside the literature-anchored range [1, 200] Pa "
        f"(SF2004 anchor ~6 Pa at 1900 K)."
    )

    # Keep the two redox channels deliberately divergent here: the result's
    # redox field reports intrinsic melt fO2, not headspace transport pO2.
    floor_pO2_bar = 1.0e-9
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = fO2_log_iw
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = floor_pO2_bar
    sim.melt.fO2_log = fO2_log_iw
    sim.melt.melt_fO2_log = fO2_log_iw
    sim.melt.pO2_mbar = floor_pO2_bar * 1000.0
    builtin_divergent = sim._internal_analytical_equilibrium()
    assert builtin_divergent.fO2_log == pytest.approx(fO2_log_iw)

    vaporock_floor = backend.equilibrate(
        sim.melt.temperature_C,
        composition_mol=sim._backend_composition_mol(),
        fO2_log=math.log10(floor_pO2_bar),
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
    )
    vaporock_floor_pressures = _vaporock_diagnostic_pressures(vaporock_floor)
    if not vaporock_floor_pressures:
        pytest.skip("VapoRock returned no pressures at the transport floor")
    p_sio_floor = vaporock_floor_pressures.get("SiO", 0.0)
    assert p_sio_floor > 0.0
    # SiO2(melt) -> SiO(g) + 1/2 O2(g) gives p_SiO proportional to
    # pO2^-1/2, hence p_SiO(IW)/p_SiO(floor) =
    # (pO2_IW/pO2_floor)^-1/2. rel=1e-6 admits floating-point noise while
    # remaining far tighter than any physically meaningful scaling drift.
    expected_iw_to_floor_ratio = (
        (10.0**fO2_log_iw) / floor_pO2_bar
    ) ** -0.5
    assert p_sio_iw / p_sio_floor == pytest.approx(
        expected_iw_to_floor_ratio, rel=1.0e-6
    )

    # t-333 made the channels independent. Align them intentionally for
    # provider parity: builtin SiO reads headspace transport pO2; the direct
    # VapoRock adapter call receives that same pressure as gas fO2. The
    # pseudo-Antoine SiO row has a documented ~0.270-decade maximum residual
    # (docs/chemistry-methods.md section 2.1), versus ~0.086 observed here.
    # A 0.45-decade ceiling leaves fit/model headroom while failing loudly on
    # real drift. Both paths should retain the same residual because they
    # apply the same inverse-root pO2 scaling.
    same_basis_errors = {}
    for basis_name, basis_fO2_log in (
        ("IW", fO2_log_iw),
        ("transport floor", math.log10(1.0e-9)),
    ):
        basis_pO2_bar = 10.0**basis_fO2_log
        sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = basis_fO2_log
        sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = basis_pO2_bar
        sim.melt.fO2_log = basis_fO2_log
        sim.melt.melt_fO2_log = basis_fO2_log
        sim.melt.pO2_mbar = basis_pO2_bar * 1000.0

        builtin = sim._internal_analytical_equilibrium()
        assert builtin.fO2_log == pytest.approx(basis_fO2_log)
        vaporock = backend.equilibrate(
            sim.melt.temperature_C,
            composition_mol=sim._backend_composition_mol(),
            fO2_log=basis_fO2_log,
            pressure_bar=sim.melt.p_total_mbar / 1000.0,
        )
        vaporock_pressures = _vaporock_diagnostic_pressures(vaporock)
        if not vaporock_pressures:
            pytest.skip(f"VapoRock returned no pressures at {basis_name}")

        # SiO only: Na's pure-component Antoine certification range is
        # [924, 1118] K, below this 1873 K fixture; the IW literature band
        # above remains the load-bearing Na check.
        for species in ("SiO",):
            builtin_pressure = builtin.vapor_pressures_Pa.get(species, 0.0)
            vaporock_pressure = vaporock_pressures.get(species, 0.0)
            assert builtin_pressure > 0.0 and vaporock_pressure > 0.0
            error_decades = abs(
                math.log10(builtin_pressure / vaporock_pressure)
            )
            same_basis_errors[basis_name] = error_decades
            assert error_decades <= 0.45, (
                f"{species} builtin/VapoRock parity at {basis_name} differs "
                f"by {error_decades:.3f} decades: builtin={builtin_pressure:.4e} "
                f"Pa, VapoRock={vaporock_pressure:.4e} Pa"
            )

    assert abs(
        same_basis_errors["IW"] - same_basis_errors["transport floor"]
    ) <= 1.0e-6


def test_vaporock_iw_literature_grid_residuals_are_explicit():
    """§25 calibration grid: thin shim into the corpus-anchored framework.

    \\goal CHEMISTRY-E2E-TEST-REGIME (chunk 20/Phase-A) retired the
    hardcoded grid that used to live here in favour of the framework
    at :mod:`tests.chemistry.corpus_fixtures` +
    :mod:`tests.chemistry.test_corpus_anchored_parity`. This test stays
    in place so the §25 cohort acceptance is invocable as a single
    pytest entry, but the residual envelope, the (T, melt, species)
    table, and the dispatch path all live in the framework now.

    The shim asserts the framework's evaluation reproduces the §25 v1
    baseline (11 of 30 pass at 1-decade) and that no new failure has
    been introduced. It does NOT re-implement the grid: a new corpus
    extension or engine fix changes the framework's report, not this
    file.
    """
    backend = VapoRockBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({'warm_worker': False})
    if not available:
        pytest.skip("VapoRock optional dependency unavailable")

    repo_root = Path(__file__).resolve().parents[1]
    vapor_pressures = yaml.safe_load(
        (repo_root / "data" / "vapor_pressures.yaml").read_text()
    )
    feedstocks = yaml.safe_load(
        (repo_root / "data" / "feedstocks.yaml").read_text()
    )
    setpoints = yaml.safe_load(
        (repo_root / "data" / "setpoints.yaml").read_text()
    )

    # Local import keeps the framework dependency confined to this
    # delegating test — tests/test_vaporock_backend.py's adapter unit
    # tests above do not need the framework.
    from tests.chemistry.test_corpus_anchored_parity import (
        KNOWN_NONCONVERGED_ANCHOR_MAX_ERROR,
        _evaluate_grid_25,
    )

    report = _evaluate_grid_25(
        "vaporock",
        vapor_pressure_data=vapor_pressures,
        setpoints_data_root=setpoints,
        feedstocks_data_root=feedstocks,
    )
    assert len(report) == 30, (
        f"corpus framework returned {len(report)} grid anchors; expected 30"
    )

    failures = {
        anchor_id: entry
        for anchor_id, entry in report.items()
        if entry["status"] == "fail"
    }
    unexpected = {
        anchor_id: entry
        for anchor_id, entry in failures.items()
        if anchor_id not in KNOWN_NONCONVERGED_ANCHOR_MAX_ERROR
    }
    assert not unexpected, (
        "new calibration-grid residuals above 1 decade: "
        + ", ".join(
            f"{anchor_id}={entry['error_decades']:.2f}"
            for anchor_id, entry in unexpected.items()
        )
    )

    worsened = {
        anchor_id: entry
        for anchor_id, entry in failures.items()
        if entry["error_decades"]
        > KNOWN_NONCONVERGED_ANCHOR_MAX_ERROR[anchor_id]
    }
    assert not worsened, (
        "known calibration residuals worsened: "
        + ", ".join(
            f"{anchor_id}={entry['error_decades']:.2f}"
            for anchor_id, entry in worsened.items()
        )
    )

    passing = sum(1 for e in report.values() if e["status"] == "pass")
    assert passing >= 11, (
        f"§25 v1 baseline regressed: framework reports {passing} of 30 "
        f"passing; baseline is 11"
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
    # and core.py refuses to fall back to internal-analytical for a
    # non-analytical backend.
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
    assert backend.initialize({'warm_worker': False}) is True
    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0, "FeO": 0.2},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    keys = set(getattr(result, "vaporock_full_speciation_Pa", {}))
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


def test_get_vapor_species_cannot_drift_from_normalizer():
    # get_vapor_species()'s oxide-colliding bucket is derived from the SAME
    # OXIDE_SPECIES set _strip_gas_suffix keys on, so EVERY oxide the
    # normalizer could namespace as "<ox>_gas" is advertised -- even oxides
    # the old hand-curated list omitted (TiO2, Al2O3, ...). Nothing can
    # silently drop a vapor the normalizer would emit.
    backend = VapoRockBackend()
    advertised = set(backend.get_vapor_species())
    for ox in OXIDE_SPECIES:
        normalized = backend._strip_gas_suffix(f"{ox}(g)")
        assert normalized == f"{ox}_gas"
        assert normalized in advertised, (
            f"{ox}(g) normalizes to {normalized!r} but get_vapor_species() "
            "does not advertise it"
        )


def test_normalize_vapor_pressures_honors_declared_pa_unit(monkeypatch):
    # A legitimate already-Pa result with a sub-1e3 dominant partial
    # pressure (e.g. ~200 Pa SiO at high T) must NOT be inflated 1e5x. With
    # vapor_pressure_units='Pa' the value is taken verbatim; the old
    # max()<1e3 heuristic would have turned 200.0 into 2e7.
    fake_module = types.SimpleNamespace()
    _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({"vapor_pressure_units": "Pa", 'warm_worker': False}) is True
    assert backend._normalize_vapor_pressures({"Na": 200.0}) == {
        "Na": pytest.approx(200.0)
    }

    # The documented default ('bar') still scales bar -> Pa.
    backend_bar = VapoRockBackend()
    assert backend_bar.initialize({'warm_worker': False}) is True
    assert backend_bar._vapor_pressure_units == "bar"
    assert backend_bar._normalize_vapor_pressures({"Na": 2.0e-3}) == {
        "Na": pytest.approx(200.0)
    }


def test_unsupported_vapor_pressure_units_fails_closed(monkeypatch):
    # Ambiguity is rejected at initialize() rather than guessed later.
    fake_module = types.SimpleNamespace()
    _install_fake_import(monkeypatch, fake_module)

    backend = VapoRockBackend()
    assert backend.initialize({"vapor_pressure_units": "atm", 'warm_worker': False}) is False
    assert backend.is_available() is False
    assert backend._last_error is not None
    assert "vapor_pressure_units" in backend._last_error


# ---------------------------------------------------------------------------
# VR-5: external domain gate + warm pool (DESIGN-REV5 §4.2.1 / §5.5)
# ---------------------------------------------------------------------------

_MARE_PROBE_WT_PCT = {
    "SiO2": 45.0,
    "TiO2": 2.5,
    "Al2O3": 10.0,
    "FeO": 18.0,
    "MgO": 10.0,
    "CaO": 11.0,
    "Na2O": 0.4,
    "K2O": 0.1,
    "Cr2O3": 0.4,
    "MnO": 0.3,
}


def _fake_system_module(log10_bar_by_species, *, track=None):
    """Build a vaporock-like module with a System class for in-process tests."""

    class FakeSystem:
        instances = []

        def __init__(self):
            self.melt_compositions = []
            self.eval_calls = []
            FakeSystem.instances.append(self)
            if track is not None:
                track["constructs"] = track.get("constructs", 0) + 1

        def set_melt_comp(self, composition):
            self.melt_compositions.append(dict(composition))

        def eval_gas_abundances(self, temperature, log_fO2, P=1e-10, **_kw):
            self.eval_calls.append((float(temperature), float(log_fO2), float(P)))
            return dict(log10_bar_by_species)

    return types.SimpleNamespace(System=FakeSystem), FakeSystem


def test_domain_helpers_admit_only_1350_to_1950_K():
    from simulator.melt_backend.vaporock import (
        VAPOROCK_T_MAX_K,
        VAPOROCK_T_MIN_K,
        temperature_C_to_K,
        vaporock_liquid_fraction_admitted,
        vaporock_sum_pressure_sane,
        vaporock_temperature_in_domain,
    )

    assert VAPOROCK_T_MIN_K == 1350.0
    assert VAPOROCK_T_MAX_K == 1950.0
    assert vaporock_temperature_in_domain(1350.0) is True
    assert vaporock_temperature_in_domain(1950.0) is True
    assert vaporock_temperature_in_domain(1650.0) is True
    assert vaporock_temperature_in_domain(1349.999) is False
    assert vaporock_temperature_in_domain(1950.001) is False
    assert vaporock_temperature_in_domain(10000.0) is False
    assert vaporock_temperature_in_domain(float("nan")) is False
    # Adapter input is always Celsius; 1600 C -> 1873.15 K is admitted.
    assert vaporock_temperature_in_domain(temperature_C_to_K(1600.0)) is True
    # 10000 K as Celsius input would be absurd; domain is on Kelvin.
    assert vaporock_temperature_in_domain(temperature_C_to_K(10000.0 - 273.15)) is False

    assert vaporock_liquid_fraction_admitted(None) is True
    assert vaporock_liquid_fraction_admitted(1.0) is True
    assert vaporock_liquid_fraction_admitted(0.95) is True
    assert vaporock_liquid_fraction_admitted(0.94) is False
    assert vaporock_liquid_fraction_admitted(float("nan")) is False

    ok, sum_bar = vaporock_sum_pressure_sane({"Na": 100.0, "SiO": 50.0})  # Pa
    assert ok is True
    assert sum_bar == pytest.approx(0.0015)
    # Probe anchor ~8.3e5 bar total at 10000 K must fail the sanity gate.
    from simulator.melt_backend.vaporock import VAPOROCK_PROBE_10000K_SUM_P_BAR

    garbage_pa = {"SiO": VAPOROCK_PROBE_10000K_SUM_P_BAR * 1e5}
    ok, sum_bar = vaporock_sum_pressure_sane(garbage_pa)
    assert ok is False
    assert sum_bar == pytest.approx(VAPOROCK_PROBE_10000K_SUM_P_BAR)


def test_temperature_domain_gate_refuses_outside_envelope(monkeypatch):
    calls = {"n": 0}

    def calc_vapor_pressures(**_):
        calls["n"] += 1
        return {"Na": 1.0}

    fake_module = types.SimpleNamespace(calc_vapor_pressures=calc_vapor_pressures)
    _install_fake_import(monkeypatch, fake_module)
    backend = VapoRockBackend()
    assert backend.initialize({"warm_worker": False}) is True

    # 10000 K (probe fabrication point): refuse before any engine call.
    t_c_10000k = 10000.0 - 273.15
    result = backend.equilibrate(
        t_c_10000k,
        composition_mol={"SiO2": 1.0, "Na2O": 0.1},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )
    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == (
        OutOfDomainReason.TEMPERATURE_RANGE.value
    )
    assert result.diagnostics["temperature_K"] == pytest.approx(10000.0)
    assert result.vapor_pressures_Pa == {}
    assert getattr(result, "vaporock_full_speciation_Pa", {}) in ({}, None) or not getattr(
        result, "vaporock_full_speciation_Pa", {}
    )
    assert calls["n"] == 0
    assert any("outside admitted domain" in w for w in result.warnings)

    # Below floor.
    result_lo = backend.equilibrate(
        1350.0 - 273.15 - 1.0,
        composition_mol={"SiO2": 1.0},
        fO2_log=-8.0,
    )
    assert result_lo.status == "out_of_domain"
    assert result_lo.diagnostics["backend_status_reason"] == (
        OutOfDomainReason.TEMPERATURE_RANGE.value
    )
    assert calls["n"] == 0


def test_temperature_domain_gate_admits_envelope_edges(monkeypatch):
    seen_T = []

    class FakeSystem:
        def set_melt_comp(self, composition):
            self.composition = dict(composition)

        def eval_gas_abundances(self, temperature, log_fO2):
            seen_T.append(float(temperature))
            return {"Na(g)": -4.0}  # 1e-4 bar = 10 Pa

    fake_module = types.SimpleNamespace(System=FakeSystem)
    _install_fake_import(monkeypatch, fake_module)
    backend = VapoRockBackend()
    assert backend.initialize({"warm_worker": False}) is True

    for t_k in (1350.0, 1950.0):
        result = backend.equilibrate(
            t_k - 273.15,
            composition_mol={"SiO2": 1.0, "Na2O": 0.1},
            fO2_log=-8.0,
            pressure_bar=1e-6,
        )
        assert result.status == "non_authoritative", result.warnings
        assert result.diagnostics["backend_status_reason"] not in {
            OutOfDomainReason.TEMPERATURE_RANGE.value,
            OutOfDomainReason.SUM_PRESSURE_SANITY.value,
        } if "backend_status_reason" in result.diagnostics else True

    assert seen_T == pytest.approx([1350.0, 1950.0])


def test_liquid_fraction_gate_refuses_subliquid(monkeypatch):
    calls = {"n": 0}

    def calc_vapor_pressures(**_):
        calls["n"] += 1
        return {"Na": 1.0}

    fake_module = types.SimpleNamespace(calc_vapor_pressures=calc_vapor_pressures)
    _install_fake_import(monkeypatch, fake_module)
    backend = VapoRockBackend()
    assert backend.initialize({"warm_worker": False}) is True

    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0},
        fO2_log=-8.0,
        liquid_fraction=0.5,
    )
    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == (
        OutOfDomainReason.LIQUID_STATE.value
    )
    assert calls["n"] == 0

    # None / passing liquid fractions still reach the engine.
    result_ok = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0, "Na2O": 0.1},
        fO2_log=-8.0,
        liquid_fraction=0.99,
    )
    assert result_ok.status == "non_authoritative"
    assert calls["n"] == 1


def test_sum_pressure_sanity_refuses_probe_scale_garbage(monkeypatch):
    """Defense-in-depth: even in-domain T, absurd totals are refused.

    Fixture source: docs-private/research/2026-07-31-vaporock-probe/findings.md
    (10000 K sum_P_bar_finite ≈ 8.3e5 bar). The T gate would catch 10000 K
    first; this test injects the probe-scale total at an admitted T to pin
    the sum-pressure gate itself.
    """
    from simulator.melt_backend.vaporock import VAPOROCK_PROBE_10000K_SUM_P_BAR

    # log10(bar) such that 10**log10 * 1e5 Pa sums to the probe total.
    log10_bar = math.log10(VAPOROCK_PROBE_10000K_SUM_P_BAR)

    class FakeSystem:
        def set_melt_comp(self, composition):
            pass

        def eval_gas_abundances(self, temperature, log_fO2):
            return {"SiO(g)": log10_bar}

    fake_module = types.SimpleNamespace(System=FakeSystem)
    _install_fake_import(monkeypatch, fake_module)
    backend = VapoRockBackend()
    assert backend.initialize({"warm_worker": False}) is True

    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0, "FeO": 0.2},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )
    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == (
        OutOfDomainReason.SUM_PRESSURE_SANITY.value
    )
    assert result.diagnostics["sum_pressure_bar"] == pytest.approx(
        VAPOROCK_PROBE_10000K_SUM_P_BAR, rel=1e-9
    )
    assert result.vapor_pressures_Pa == {}
    full = getattr(result, "vaporock_full_speciation_Pa", None)
    assert not full
    assert any("sum partial pressure" in w for w in result.warnings)
    assert any("10000 K" in w for w in result.warnings)


def test_10000K_finite_fabrication_regression_refuses_before_engine(monkeypatch):
    """HI-8 regression: 10000 K fabricates ~8.3e5 bar total; we refuse.

    The probe (findings.md) measured zero typed refusals from upstream at
    10000 K with sum_P_bar ≈ 8.3e5. Our external T gate must refuse before
    the engine is invoked so the fabrication never enters diagnostics as a
    successful speciation.
    """
    from simulator.melt_backend.vaporock import VAPOROCK_PROBE_10000K_SUM_P_BAR

    engine_calls = {"n": 0}

    class FakeSystem:
        def set_melt_comp(self, composition):
            engine_calls["n"] += 1

        def eval_gas_abundances(self, temperature, log_fO2):
            engine_calls["n"] += 1
            # Would return probe-scale garbage if reached.
            return {"SiO(g)": math.log10(VAPOROCK_PROBE_10000K_SUM_P_BAR)}

    fake_module = types.SimpleNamespace(System=FakeSystem)
    _install_fake_import(monkeypatch, fake_module)
    backend = VapoRockBackend()
    assert backend.initialize({"warm_worker": False}) is True

    result = backend.equilibrate(
        10000.0 - 273.15,
        composition_mol={"SiO2": 1.0, "Na2O": 0.1, "FeO": 0.2},
        fO2_log=3.025,  # probe IW-at-10000K-ish
        pressure_bar=1e-6,
    )
    assert engine_calls["n"] == 0
    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == (
        OutOfDomainReason.TEMPERATURE_RANGE.value
    )
    assert result.diagnostics["temperature_K"] == pytest.approx(10000.0)
    # Fixture anchor remains documented for the sum-pressure gate.
    assert VAPOROCK_PROBE_10000K_SUM_P_BAR == pytest.approx(8.323344495585738e5)


def test_pressure_bar_remains_diagnostic_only(monkeypatch):
    pressures = []

    class FakeSystem:
        def set_melt_comp(self, composition):
            pass

        def eval_gas_abundances(self, temperature, log_fO2, P=1e-10, **_kw):
            pressures.append(float(P))
            return {"Na(g)": -3.0}

    fake_module = types.SimpleNamespace(System=FakeSystem)
    _install_fake_import(monkeypatch, fake_module)
    backend = VapoRockBackend()
    assert backend.initialize({"warm_worker": False}) is True

    for p_bar in (1e-6, 100.0):
        result = backend.equilibrate(
            1600.0,
            composition_mol={"Na2O": 1.0},
            fO2_log=-8.0,
            pressure_bar=p_bar,
        )
        assert result.status == "non_authoritative"
        assert result.pressure_bar == pytest.approx(p_bar)
        assert result.diagnostics["pressure_control_authoritative"] is False

    # Upstream default P only; adapter never passes requested total pressure.
    assert pressures == pytest.approx([1e-10, 1e-10])


def test_provider_remains_non_selected():
    provider = VapoRockProvider(backend=None, vapor_pressure_data={})
    profile = provider.capability_profile()
    assert profile.provider_id == "vaporock"
    assert profile.is_authoritative_for == frozenset()
    assert ChemistryIntent.VAPOR_PRESSURE in profile.intents


def test_provider_forwards_liquid_fraction_and_domain_refuses():
    seen = {}

    class FakeBackend:
        def is_available(self):
            return True

        def get_engine_version(self):
            return "fake"

        def equilibrate(self, **kwargs):
            seen.update(kwargs)
            return types.SimpleNamespace(
                vapor_pressures_Pa={},
                vaporock_full_speciation_Pa={},
                warnings=("liquid refused",),
                status="out_of_domain",
            )

    provider = VapoRockProvider(backend=FakeBackend(), vapor_pressure_data={})
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"SiO2": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=1600.0,
        pressure_bar=1e-6,
        fO2_log=-8.0,
        control_inputs={"pO2_bar": 1e-6, "liquid_fraction": 0.4},
    )
    result = provider.dispatch(request)
    assert seen["liquid_fraction"] == pytest.approx(0.4)
    # Provider stays diagnostic/non-authoritative regardless of backend status.
    assert result.status == "non_authoritative"
    assert result.transition is None
    assert provider.capability_profile().is_authoritative_for == frozenset()


def test_fresh_system_per_request_in_process(monkeypatch):
    track = {"constructs": 0}
    fake_module, FakeSystem = _fake_system_module(
        {"Na(g)": -4.0}, track=track
    )
    _install_fake_import(monkeypatch, fake_module)
    backend = VapoRockBackend()
    assert backend.initialize({"warm_worker": False, "reuse_system": False})
    for _ in range(3):
        result = backend.equilibrate(
            1600.0,
            composition_mol={"Na2O": 1.0},
            fO2_log=-8.0,
        )
        assert result.status == "non_authoritative"
    assert track["constructs"] == 3
    assert len(FakeSystem.instances) == 3


def test_system_reuse_equivalence_across_admitted_grid(monkeypatch):
    """Prove fresh-vs-reused System agreement on a small admitted grid.

    DESIGN-REV5 §5.5: reuse is only admitted after equivalence against
    fresh-System evaluation. This test drives the pure-function case
    (no carryover) so the probe passes and reuse stays admitted. The
    production default remains fresh-per-request (reuse_system=False)
    until a reviewed flip. The residue/mismatch latch is covered by
    ``test_reuse_mismatch_latches_fresh_per_request``.
    """
    # Deterministic fake: output depends only on (T, fO2, melt), not on
    # prior System state — so reuse matches fresh under the §5.5 probe.
    class PureFunctionSystem:
        instances = []

        def __init__(self):
            self._melt = None
            PureFunctionSystem.instances.append(self)

        def set_melt_comp(self, composition):
            self._melt = dict(composition)

        def eval_gas_abundances(self, temperature, log_fO2):
            sio2 = float(self._melt.get("SiO2", 0.0))
            # Pure function of inputs — equivalence holds.
            log_na = (
                -5.0
                + 0.001 * float(temperature)
                + 0.01 * float(log_fO2)
                + 1e-4 * sio2
            )
            return {"Na(g)": log_na}

    fake_module = types.SimpleNamespace(System=PureFunctionSystem)
    _install_fake_import(monkeypatch, fake_module)

    grid_T_C = [t_k - 273.15 for t_k in (1350.0, 1500.0, 1650.0, 1800.0, 1950.0)]
    melts = [
        {"SiO2": 1.0, "Na2O": 0.1},
        {"SiO2": 0.8, "MgO": 0.2, "Na2O": 0.05},
    ]
    fO2s = [-10.0, -8.0]

    def run_grid(reuse: bool):
        backend = VapoRockBackend()
        assert backend.initialize(
            {"warm_worker": False, "reuse_system": reuse}
        )
        # In-process path always constructs fresh System today; the
        # reuse flag only affects the warm worker. Equivalence is
        # therefore checked by calling the worker handler directly with
        # reuse on/off below. Here we still pin the in-process grid is
        # deterministic for the admitted domain.
        out = []
        for t_c in grid_T_C:
            for melt in melts:
                for fO2 in fO2s:
                    result = backend.equilibrate(
                        t_c,
                        composition_mol=melt,
                        fO2_log=fO2,
                        pressure_bar=1e-6,
                    )
                    assert result.status == "non_authoritative"
                    full = dict(
                        getattr(result, "vaporock_full_speciation_Pa", {}) or {}
                    )
                    out.append((t_c, tuple(sorted(melt.items())), fO2, full))
        backend.close()
        return out

    fresh = run_grid(False)
    from simulator.melt_backend.base import project_melt_to_oxide_projection
    from simulator.melt_backend.vaporock import (
        _VAPOROCK_MELT_BASIS,
        _handle_vaporock_request,
    )

    # Drive the warm-worker handler with hand-built resources so
    # fresh-vs-reuse equivalence is tested without a live spawn.
    def make_resource(reuse: bool):
        return {
            "module": fake_module,
            "system_cls": PureFunctionSystem,
            "system": None,
            "temperature_units": "C",
            "vapor_pressure_units": "bar",
            "reuse_system": reuse,
            "system_construct_count": 0,
            "reuse_equivalence_checks_done": 0,
            "reuse_latched_fresh": False,
            "reuse_mismatch_diagnostic": None,
        }

    requests = []
    for t_c in grid_T_C:
        for melt in melts:
            projection = project_melt_to_oxide_projection(
                composition_kg=None,
                composition_mol=melt,
                oxide_basis=_VAPOROCK_MELT_BASIS,
            )
            for fO2 in fO2s:
                requests.append({
                    "composition_wt_pct": dict(projection.oxide_wt_pct),
                    "temperature_K": t_c + 273.15,
                    "fO2_log": fO2,
                })

    fresh_resource = make_resource(False)
    reuse_resource = make_resource(True)
    fresh_payloads = [
        _handle_vaporock_request(fresh_resource, req, None) for req in requests
    ]
    reuse_payloads = [
        _handle_vaporock_request(reuse_resource, req, None) for req in requests
    ]
    assert [p["log10_bar"] for p in fresh_payloads] == [
        p["log10_bar"] for p in reuse_payloads
    ]
    # Fresh path constructs one System per request.
    assert fresh_resource["system_construct_count"] == len(requests)
    # Pure-function reuse: seed System once, then each subsequent request
    # probes with one fresh System (construct count = 1 + (N-1) probes).
    # No latch — outputs matched.
    assert reuse_resource["reuse_latched_fresh"] is False
    assert reuse_resource["system"] is not None
    assert reuse_resource["system_construct_count"] == len(requests)
    assert all(p.get("reuse_mismatch") is None for p in reuse_payloads) or all(
        "reuse_mismatch" not in p for p in reuse_payloads
    )
    # Grid itself was evaluated without out-of-domain refusals.
    assert len(fresh) == len(grid_T_C) * len(melts) * len(fO2s)


def test_reuse_mismatch_latches_fresh_per_request():
    """Residue-carrying System must latch fresh-per-request on mismatch.

    Null hypothesis (review-vr5-km P1-1): a bootstrap ``reuse_system``
    flag alone is not §5.5 — without an in-worker reused-vs-fresh
    comparison and latch, a stateful System whose ``set_melt_comp``
    leaves residue would silently diverge. This test:

    1. Uses a STATEFUL fake that carries prior-melt SiO2 into the next
       evaluation (cannot be pure-function-equivalent).
    2. Asserts the second (reuse) request latches ``reuse_latched_fresh``
       and returns a status-bearing ``reuse_mismatch`` diagnostic.
    3. Asserts subsequent requests construct a fresh System each time.
    4. Proves the latch is load-bearing by momentary in-memory mutation:
       clearing the latch + skipping further probes without clearing the
       stored System would re-admit residue reuse; we assert outputs then
       diverge from a true-fresh control, so removing the latch would
       fail the post-latch construct-count / mode assertions above.
    """
    from simulator.melt_backend.vaporock import _handle_vaporock_request

    class ResidueSystem:
        """set_melt_comp leaves prior SiO2 residue in the next eval."""

        instances: list = []

        def __init__(self):
            self._melt: dict = {}
            self._prior_sio2 = 0.0
            ResidueSystem.instances.append(self)

        def set_melt_comp(self, composition):
            # Residue: previous melt's SiO2 bleeds into the next solve.
            self._prior_sio2 = float(self._melt.get("SiO2", 0.0)) if self._melt else 0.0
            self._melt = dict(composition)

        def eval_gas_abundances(self, temperature, log_fO2):
            sio2 = float(self._melt.get("SiO2", 0.0))
            # Pure part + large residue term so reused ≠ fresh after melt change.
            log_na = (
                -5.0
                + 0.001 * float(temperature)
                + 0.01 * float(log_fO2)
                + 1e-4 * sio2
                + 0.5 * self._prior_sio2
            )
            return {"Na(g)": log_na}

    def make_resource(*, reuse: bool):
        return {
            "module": types.SimpleNamespace(System=ResidueSystem),
            "system_cls": ResidueSystem,
            "system": None,
            "temperature_units": "C",
            "vapor_pressure_units": "bar",
            "reuse_system": reuse,
            "system_construct_count": 0,
            "reuse_equivalence_checks_done": 0,
            "reuse_latched_fresh": False,
            "reuse_mismatch_diagnostic": None,
        }

    melt_a = {"SiO2": 1.0, "Na2O": 0.1}
    melt_b = {"SiO2": 0.5, "Na2O": 0.2, "MgO": 0.3}
    req_a = {
        "composition_wt_pct": dict(melt_a),
        "temperature_K": 1650.0,
        "fO2_log": -8.0,
    }
    req_b = {
        "composition_wt_pct": dict(melt_b),
        "temperature_K": 1650.0,
        "fO2_log": -8.0,
    }

    resource = make_resource(reuse=True)
    ResidueSystem.instances.clear()

    # Seed: first call stores a System (no prior residue → no probe needed).
    p0 = _handle_vaporock_request(resource, req_a, None)
    assert p0["reuse_mode"] == "fresh_seed"
    assert resource["reuse_latched_fresh"] is False
    assert resource["system"] is not None
    assert resource["system_construct_count"] == 1

    # Second call: reuse + probe against fresh. Residue from melt A makes
    # reused eval diverge from a brand-new System on melt B → latch.
    p1 = _handle_vaporock_request(resource, req_b, None)
    assert resource["reuse_latched_fresh"] is True, (
        "§5.5 mismatch must latch fresh-per-request for the worker lifetime"
    )
    assert resource["system"] is None, (
        "latched worker must drop the residue-carrying stored System"
    )
    assert p1["reuse_mode"] == "fresh_latched"
    assert p1.get("reuse_mismatch") is not None
    assert p1["reuse_mismatch"]["reason"] == "reused_vs_fresh_mismatch"
    assert p1["reuse_mismatch"]["reuse_latched_fresh"] is True
    # Probe constructed one fresh System for comparison (count 1→2); latch
    # returns that fresh map, so construct_count is 2 after the mismatch.
    assert resource["system_construct_count"] == 2

    # Post-latch: every request constructs a new System (fresh-per-request).
    constructs_after_latch = resource["system_construct_count"]
    for _ in range(3):
        payload = _handle_vaporock_request(resource, req_b, None)
        assert payload["reuse_mode"] == "fresh_latched"
        assert payload["reuse_latched_fresh"] is True
        assert payload.get("reuse_mismatch", {}).get("reason") == (
            "reused_vs_fresh_mismatch"
        )
        constructs_after_latch += 1
        assert resource["system_construct_count"] == constructs_after_latch
        assert resource["system"] is None

    # --- Prove the latch is load-bearing (momentary in-memory mutation) ---
    # If the latch flag were cleared and probes skipped while a residue
    # System were re-stored, reuse would silently diverge from fresh.
    # Mutate resource state to that defective configuration and show the
    # outputs diverge — so a code change that removes the latch would
    # fail the post-latch construct-count assertions above AND would
    # re-introduce this silent divergence.
    control = make_resource(reuse=False)
    control_payloads = [
        _handle_vaporock_request(control, req_a, None),
        _handle_vaporock_request(control, req_b, None),
    ]

    # Build a residue-carrying stored System by hand, then clear the latch
    # and skip probes (simulating "latch removed / always reuse").
    defective = make_resource(reuse=True)
    _handle_vaporock_request(defective, req_a, None)
    # Momentary mutation: force past the probe window and clear the latch
    # without dropping the stored System — the pre-fix failure mode.
    assert defective["system"] is not None
    defective["reuse_latched_fresh"] = False
    defective["reuse_equivalence_checks_done"] = 10_000  # skip probes
    defective_p = _handle_vaporock_request(defective, req_b, None)
    # Residue path (no probe) ≠ true fresh control on the same inputs.
    assert defective_p["log10_bar"] != control_payloads[1]["log10_bar"], (
        "residue-carrying reuse without latch/probe must diverge from "
        "fresh; if this equality holds the fake is not stateful enough"
    )
    # And the production latch path (p1) returned the fresh map, matching control.
    assert p1["log10_bar"] == control_payloads[1]["log10_bar"], (
        "on mismatch the handler must return the fresh-System result, "
        "not the residue-contaminated reused map"
    )


def test_malformed_liquid_fraction_is_typed_out_of_domain(monkeypatch):
    """Non-floatable liquid_fraction must not become provider 'unavailable'.

    review-vr5-km P3-1: diagnostics used to call float() after the admit
    helper already refused, raising into the provider's broad except.
    """
    calls = {"n": 0}

    def calc_vapor_pressures(**_):
        calls["n"] += 1
        return {"Na": 1.0}

    fake_module = types.SimpleNamespace(calc_vapor_pressures=calc_vapor_pressures)
    _install_fake_import(monkeypatch, fake_module)
    backend = VapoRockBackend()
    assert backend.initialize({"warm_worker": False}) is True

    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0},
        fO2_log=-8.0,
        liquid_fraction="not-a-number",  # type: ignore[arg-type]
    )
    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == (
        OutOfDomainReason.LIQUID_STATE.value
    )
    assert result.diagnostics["liquid_fraction"] == repr("not-a-number")
    assert calls["n"] == 0


def test_warm_pool_owns_system_lifecycle_live():
    """Live warm-pool smoke: import lives in the child; domain gate holds.

    Skips when the real vaporock package is not importable in a spawn child.
    """
    pytest.importorskip("vaporock")
    backend = VapoRockBackend()
    try:
        ok = backend.initialize({
            "warm_worker": True,
            "warm_pool_size": 1,
            "reuse_system": False,
            "worker_startup_timeout_s": 90.0,
            "warm_call_timeout_s": 60.0,
        })
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"warm pool failed to start: {exc}")
    if not ok:
        pytest.skip(
            f"warm pool unavailable: {backend._last_error}"
        )
    try:
        assert backend.uses_warm_pool is True
        # In-domain live solve.
        result = backend.equilibrate(
            1650.0 - 273.15,
            composition_kg=_MARE_PROBE_WT_PCT,
            fO2_log=-9.94,
            pressure_bar=1e-6,
        )
        assert result.status == "non_authoritative"
        full = dict(getattr(result, "vaporock_full_speciation_Pa", {}) or {})
        assert full  # real speciation present
        assert result.diagnostics.get("pressure_control_authoritative") is False
        sum_bar = float(result.diagnostics.get("sum_pressure_bar") or 0.0)
        assert sum_bar < 10.0

        # 10000 K still refused by the parent-side domain gate (no worker call).
        refused = backend.equilibrate(
            10000.0 - 273.15,
            composition_kg=_MARE_PROBE_WT_PCT,
            fO2_log=3.0,
            pressure_bar=1e-6,
        )
        assert refused.status == "out_of_domain"
        assert refused.diagnostics["backend_status_reason"] == (
            OutOfDomainReason.TEMPERATURE_RANGE.value
        )
    finally:
        backend.close()


def test_no_result_cache_on_backend():
    """Owner ruling: warm pool only — no result/calibration cache surface."""
    backend = VapoRockBackend()
    # No cache attributes or methods introduced by VR-5.
    for name in (
        "_result_cache",
        "_calibration_cache",
        "result_cache",
        "cache_get",
        "cache_put",
    ):
        assert not hasattr(backend, name)


def test_session_warm_boot_cache_reuses_identical_config(monkeypatch):
    """Process-scoped warm-boot cache shares one backend for identical keys.

    Golden-neutral: only the boot is shared; equilibrate still runs. Disabled
    when REGOLITH_VAPOROCK_SESSION_WARM is off so monkeypatched unit tests
    keep a private cold path.
    """
    from simulator.melt_backend import vaporock as vaporock_mod

    vaporock_mod.clear_session_backend_cache()
    monkeypatch.setenv(vaporock_mod.SESSION_WARM_ENV, "0")
    assert vaporock_mod.session_warm_enabled() is False
    assert vaporock_mod.get_or_create_session_backend({}) is None

    monkeypatch.setenv(vaporock_mod.SESSION_WARM_ENV, "1")
    assert vaporock_mod.session_warm_enabled() is True

    created: list[object] = []
    real_cls = vaporock_mod.VapoRockBackend

    class TrackingBackend(real_cls):  # type: ignore[misc, valid-type]
        def initialize(self, config):  # type: ignore[no-untyped-def]
            created.append(dict(config))
            # Force a cheap in-process "available" path — no real spawn.
            self._available = True
            self._config = dict(config or {})
            self._warm_worker_enabled = bool(self._config.get("warm_worker", False))
            self._warm_pool = object()  # truthy so uses_warm_pool is True
            return True

        def close(self) -> None:
            self._available = False
            self._warm_pool = None

    monkeypatch.setattr(vaporock_mod, "VapoRockBackend", TrackingBackend)
    vaporock_mod.clear_session_backend_cache()
    try:
        first = vaporock_mod.get_or_create_session_backend({})
        second = vaporock_mod.get_or_create_session_backend({})
        assert first is not None and second is not None
        assert first is second
        assert len(created) == 1
        assert created[0].get("warm_worker") is True
    finally:
        vaporock_mod.clear_session_backend_cache()
