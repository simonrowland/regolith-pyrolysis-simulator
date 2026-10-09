"""Pins of the engine payloads this chunk extends.

The direct MAGEMin parser pin is today's Verb=0 contract: unit-mass mode
fractions and no composition. The subprocess pin is the chunk-0 contract
on the same recorded MAGEMin 1.9.6 (04/06/2026) lunar-mare run at
0.001 kbar, buffer qfm, buffer_n=0. Ig bulk, not renormalised:

    SiO2 44.5, Al2O3 13.5, CaO 11.0, MgO 9.0, FeOt 14.662801,
    K2O 0.10, Na2O 0.4, TiO2 1.5, O 1.837199, Cr2O3 0.35, H2O 0

That vector is the adapter's FeO→FeOt+O fold of SiO2 44.5, TiO2 1.5,
Al2O3 13.5, FeO 16.5, MgO 9.0, CaO 11.0, Na2O 0.4, K2O 0.10, Cr2O3 0.35.
The seat has no engines/engines.local.toml, so these strings are the
recorded payload. AlphaMELTS was not executed; its pin is the recorded
Phase_main table the subprocess parser already accepts.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from engines.alphamelts.parser import (
    diagnostics_to_equilibrium,
    project_equilibrium_to_diagnostics,
)
from engines.alphamelts.provider import AlphaMELTSProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.base import (
    EquilibriumResult,
    liquid_fraction_from_phase_masses,
)
from simulator.melt_backend.liquidus import (
    EquilibriumCrystallizationPathResult,
    LiquidusSolidusResult,
)
from simulator.melt_backend.magemin import (
    COMPOSITION_PROJECTED,
    MAGEMinBackend,
)


# Recorded Phase/Mode blocks. Mode is a mass fraction of a unit-mass system.
_LUNAR_MARE_STDOUT = {
    1100: (
        " Phase :      liq      fsp      cpx       ol      spl      qfm \n"
        " Mode  :  0.43631  0.26143  0.11334  0.15735  0.00709  0.02448 \n"
    ),
    1200: (
        " Phase :      liq       ol      spl      qfm \n"
        " Mode  :  0.94636  0.02325  0.00699  0.02341 \n"
    ),
    1400: (
        " Phase :      liq      qfm \n"
        " Mode  :  0.97622  0.02378 \n"
    ),
}

# Lunar-mare majors as kg. Nothing here is outside the ig order, so the
# in-database batch is this sum. MnO and P2O5 are the exclusion case.
_LUNAR_MARE_MAJORS_KG = {
    "SiO2": 44.5,
    "TiO2": 1.5,
    "Al2O3": 13.5,
    "FeO": 16.5,
    "MgO": 9.0,
    "CaO": 11.0,
    "Na2O": 0.4,
    "K2O": 0.10,
    "Cr2O3": 0.35,
}

# Recorded ``Oxide compositions [wt fr]`` rows. Column order is the
# matlab header: SiO2 Al2O3 CaO MgO FeO K2O Na2O TiO2 O Cr2O3 H2O.
_OXIDE_HEADER = (
    "SiO2", "Al2O3", "CaO", "MgO", "FeO", "K2O", "Na2O",
    "TiO2", "O", "Cr2O3", "H2O",
)
_LUNAR_MARE_OXIDE_WT_FR = {
    1100: {
        "liq": (
            0.51642, 0.10227, 0.10361, 0.05646, 0.17811, 0.00227,
            0.00495, 0.03264, 0.00275, 0.00052, 0.00000,
        ),
        "fsp": (
            0.44758, 0.35595, 0.18922, 0.0, 0.0, 0.00002,
            0.00723, 0.0, 0.0, 0.0, 0.0,
        ),
        "cpx": (
            0.50793, 0.02969, 0.17401, 0.15039, 0.12659, 0.00019,
            0.00116, 0.00756, 0.00104, 0.00143, 0.00000,
        ),
        "ol": (
            0.36497, 0.0, 0.00285, 0.30288, 0.32930, 0.0,
            0.0, 0.0, 0.0, 0.0, 0.0,
        ),
        "spl": (
            0.0, 0.19548, 0.0, 0.05656, 0.36500, 0.0,
            0.0, 0.01248, 0.01004, 0.36045, 0.0,
        ),
    },
    1200: {
        "liq": (
            0.47309, 0.14381, 0.11920, 0.08657, 0.15250, 0.00108,
            0.00434, 0.01625, 0.00226, 0.00088, 0.00000,
        ),
        "ol": (
            0.39093, 0.0, 0.00258, 0.41545, 0.19104, 0.0,
            0.0, 0.0, 0.0, 0.0, 0.0,
        ),
        "spl": (
            0.0, 0.30050, 0.0, 0.12211, 0.22721, 0.0,
            0.0, 0.00100, 0.00510, 0.34408, 0.0,
        ),
    },
    1400: {
        "liq": (
            0.46743, 0.14181, 0.11555, 0.09452, 0.15403, 0.00105,
            0.00420, 0.01576, 0.00197, 0.00368, 0.00000,
        ),
    },
}


def _lunar_mare_matlab(temperature_C: int) -> str:
    lines = [
        "Oxide compositions [wt fr]:",
        " ".join(_OXIDE_HEADER),
    ]
    for name, fractions in _LUNAR_MARE_OXIDE_WT_FR[temperature_C].items():
        rendered = " ".join(f"{value:.5f}" for value in fractions)
        lines.append(f"{name} {rendered}")
    lines.append("")
    return "\n".join(lines)


# Printed mode fractions, buffer included. The parser drops the buffer.
_LUNAR_MARE_MODES = {
    1100: {
        "liq": 0.43631,
        "fsp": 0.26143,
        "cpx": 0.11334,
        "ol": 0.15735,
        "spl": 0.00709,
        "qfm": 0.02448,
    },
    1200: {
        "liq": 0.94636,
        "ol": 0.02325,
        "spl": 0.00699,
        "qfm": 0.02341,
    },
    1400: {
        "liq": 0.97622,
        "qfm": 0.02378,
    },
}


def _material_modes(temperature_C: int) -> dict[str, float]:
    return {
        name: fraction
        for name, fraction in _LUNAR_MARE_MODES[temperature_C].items()
        if name != "qfm"
    }


@pytest.mark.parametrize("temperature_C", (1100, 1200, 1400))
def test_magemin_subprocess_payload_is_unit_mass_mode_without_composition(
    temperature_C,
):
    parsed = MAGEMinBackend._parse_subprocess_stdout(
        _LUNAR_MARE_STDOUT[temperature_C]
    )

    assert set(parsed) == set(_material_modes(temperature_C))
    for name, fraction in _material_modes(temperature_C).items():
        assert set(parsed[name]) == {"mass_kg"}
        assert parsed[name]["mass_kg"] == pytest.approx(fraction)
    assert "qfm" not in parsed
    masses = {name: row["mass_kg"] for name, row in parsed.items()}
    assert liquid_fraction_from_phase_masses(masses) == pytest.approx(
        _material_modes(temperature_C)["liq"]
        / sum(_material_modes(temperature_C).values())
    )


def test_magemin_out_of_database_element_is_excluded_and_majors_solve():
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    backend._binary_path = Path("/fake/MAGEMin")
    backend._config = {}
    backend._subprocess_pool = None
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return {
            "phases": {
                "liq": {
                    "mass_kg": 1.0,
                    "composition_wt_pct": {"SiO2": 46.743},
                },
            },
            "converged": True,
        }

    backend._call_magemin = fake_call

    result = backend.equilibrate(
        1200.0,
        composition_kg={
            **_LUNAR_MARE_MAJORS_KG,
            "MnO": 0.20,
            "P2O5": 0.10,
        },
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert calls
    sent = calls[0]["bulk_projection"].composition_wt_pct
    assert "MnO" not in sent
    assert "P2O5" not in sent
    assert "SiO2" in sent
    assert calls[0]["batch_kg"] == pytest.approx(sum(_LUNAR_MARE_MAJORS_KG.values()))
    assert result.status == "ok"
    assert result.diagnostics.get("backend_status_reason") != COMPOSITION_PROJECTED
    projection = result.diagnostics["input_composition_projection"]
    assert COMPOSITION_PROJECTED not in projection
    assert "dropped_bulk_components" not in projection
    excluded = result.diagnostics["magemin_excluded_database_components_kg"]
    assert excluded["MnO"] == pytest.approx(0.20)
    assert excluded["P2O5"] == pytest.approx(0.10)
    # The fake stands in for the library bridge: its mass is not rescaled.
    assert result.phase_masses_kg["liq"] == pytest.approx(1.0)
    assert any(
        "dropped components outside documented bulk order" in warning
        for warning in result.warnings
    )
    assert not any(
        "refused projected composition" in warning
        for warning in result.warnings
    )


@pytest.mark.parametrize("temperature_C", (1100, 1200, 1400))
def test_magemin_subprocess_scales_recorded_mode_and_keeps_oxide_wt(
    temperature_C, monkeypatch,
):
    """Recorded Verb=0 stdout plus the matlab oxide table, through equilibrate."""
    captured = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = _LUNAR_MARE_STDOUT[temperature_C]

    def fake_subprocess_run(args, **kwargs):
        captured["args"] = list(args)
        dest = Path(kwargs["cwd"]) / "output" / "_matlab_output.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_lunar_mare_matlab(temperature_C), encoding="utf-8")
        return FakeCompleted()

    import simulator.melt_backend.magemin as magemin_module

    monkeypatch.setattr(magemin_module.subprocess, "run", fake_subprocess_run)
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    backend._binary_path = Path("/fake/MAGEMin")
    backend._config = {}
    backend._subprocess_pool = None

    result = backend.equilibrate(
        float(temperature_C),
        composition_kg=dict(_LUNAR_MARE_MAJORS_KG),
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert result.status == "ok", result.warnings
    assert any(arg == "--out_matlab=1" for arg in captured["args"])
    assert "qfm" not in result.phase_masses_kg
    batch_kg = sum(_LUNAR_MARE_MAJORS_KG.values())
    material = _material_modes(temperature_C)
    assert set(result.phase_masses_kg) == set(material)
    for name, fraction in material.items():
        assert result.phase_masses_kg[name] == pytest.approx(fraction * batch_kg)
        recorded = _LUNAR_MARE_OXIDE_WT_FR[temperature_C][name]
        expected = {
            oxide: weight_fraction * 100.0
            for oxide, weight_fraction in zip(_OXIDE_HEADER, recorded)
            if weight_fraction > 0.0
        }
        assert result.phase_compositions[name] == pytest.approx(expected)
        assert "Fe2O3" not in result.phase_compositions[name]
    assert result.liquid_fraction == pytest.approx(
        material["liq"] / sum(material.values())
    )


def test_alphamelts_phase_species_mol_aggregates_oxides_by_phase():
    backend = AlphaMELTSBackend()
    phase = (
        "index 1 Pressure 1.00 Temperature 1100.00 SiO2 FeO MgO\n"
        "olivine0 40.0 -100.0 10.0 12.0 5.0 "
        "(Mg0.8Fe''0.2)2SiO4 40.0 10.0 50.0\n"
        "olivine1 60.0 -200.0 20.0 18.0 7.0 "
        "(Mg0.6Fe0.4)2SiO4 35.0 30.0 35.0\n"
        "liquid1 100.0 -50.0 10.0 30.0 8.0 "
        "1.2 50.0 16.0 34.0\n"
    )
    parsed = backend._parse_phase_main_output(phase)
    instances = []
    for row in parsed["phase_instances"]:
        item = dict(row)
        item["physical_mass_kg"] = float(row["solver_basis_mass_kg"])
        instances.append(item)

    species_mol, species_kg, refusals = backend._phase_species_from_instances(
        instances
    )

    assert refusals == ()
    assert set(species_kg) == {"olivine", "liquid"}
    # Table weight percent, not the formula token. Masses are the recorded
    # solver grams converted at 1000 g/kg: olivine 40 g and 60 g, liquid 100 g.
    assert species_kg["olivine"]["SiO2"] == pytest.approx(0.04 * 0.40 + 0.06 * 0.35)
    assert species_kg["olivine"]["FeO"] == pytest.approx(0.04 * 0.10 + 0.06 * 0.30)
    assert species_kg["olivine"]["MgO"] == pytest.approx(0.04 * 0.50 + 0.06 * 0.35)
    assert species_kg["liquid"]["SiO2"] == pytest.approx(0.1 * 0.50)
    assert species_mol["liquid"]["SiO2"] > 0.0
    assert sum(species_kg["olivine"].values()) == pytest.approx(0.10)
    assert sum(species_kg["liquid"].values()) == pytest.approx(0.10)
    assert "phase_species_mol" not in parsed


def test_diagnostic_dto_carries_phase_species_mol():
    equilibrium = EquilibriumResult(
        temperature_C=1200.0,
        pressure_bar=1.0,
        liquid_fraction=0.0,
        status="ok",
        phases_present=["olivine0"],
        phase_masses_kg={"olivine0": 0.6},
        phase_species_mol={"olivine0": {"(Mg0.8Fe0.2)2SiO4": 0.01}},
        phase_species_kg={"olivine0": {"(Mg0.8Fe0.2)2SiO4": 0.6}},
    )

    diagnostic = project_equilibrium_to_diagnostics(
        equilibrium,
        mode="subprocess",
        engine_version="recorded-fixture",
    )
    payload = diagnostic.as_diagnostic()

    assert payload["phase_species_mol"]["olivine0"][
        "(Mg0.8Fe0.2)2SiO4"
    ] == pytest.approx(0.01)
    assert "phase_species_kg" not in payload
    rebuilt = type(diagnostic)(**payload)
    assert rebuilt.phase_species_mol["olivine0"][
        "(Mg0.8Fe0.2)2SiO4"
    ] == pytest.approx(0.01)
    restored = diagnostics_to_equilibrium(rebuilt, {})
    assert restored.phase_species_mol["olivine0"][
        "(Mg0.8Fe0.2)2SiO4"
    ] == pytest.approx(0.01)


def test_equilibrium_crystallization_path_has_no_phase_inventory_field():
    assert "isothermal_phase_inventories" not in (
        EquilibriumCrystallizationPathResult.__dataclass_fields__
    )


class _PinECBackend:
    def __init__(self):
        self._mode = "python_api"
        self.temperatures: list[float] = []

    def is_available(self) -> bool:
        return True

    def get_engine_version(self) -> str:
        return "pin-ec"

    def find_liquidus_solidus(self, **_kwargs):
        return LiquidusSolidusResult(
            liquidus_T_C=1300.0,
            solidus_T_C=1100.0,
            liquid_fraction=1.0,
            status="ok",
        )

    def equilibrate(self, **kwargs):
        temperature_C = float(kwargs["temperature_C"])
        self.temperatures.append(temperature_C)
        frac = max(0.0, min(1.0, (temperature_C - 1100.0) / 200.0))
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=float(kwargs["pressure_bar"]),
            liquid_fraction=frac,
            liquid_composition_wt_pct={"SiO2": 50.0, "MgO": 50.0},
            phases_present=["liquid"] if frac > 0.0 else ["olivine"],
            phase_masses_kg={"liquid": frac, "olivine": 1.0 - frac},
            fO2_log=float(kwargs["fO2_log"]),
            status="ok",
        )


def test_equilibrium_crystallization_returns_liquid_path_and_no_transition():
    backend = _PinECBackend()
    provider = AlphaMELTSProvider(backend=backend)
    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": {"SiO2": 1.0, "MgO": 1.0}},
                species_formula_registry={},
            ),
            temperature_C=1200.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
            control_inputs={},
        )
    )

    assert result.status == "ok"
    assert result.transition is None
    diagnostic = dict(result.diagnostic or {})
    path = tuple(diagnostic["liquid_fraction_path"])
    assert path[0]["temperature_C"] == pytest.approx(1100.0)
    assert path[-1]["temperature_C"] == pytest.approx(1300.0)
    assert diagnostic["liquid_fraction"] == pytest.approx(
        path[-1]["liquid_fraction"]
    )
    assert diagnostic["phase_species_mol"] == {}
    assert "isothermal_phase_inventories" not in diagnostic
    # The path samples the solidus-liquidus grid only. Request temperature
    # is not an extra isothermal inventory call.
    assert backend.temperatures == [
        point["temperature_C"] for point in path
    ]
