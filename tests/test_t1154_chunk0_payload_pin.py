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
import pandas as pd

from engines.alphamelts.provider import (
    AlphaMELTSProvider,
    _inventories_from_equilibrium,
)
from simulator.accounting.formulas import parse_formula
from simulator.accounting.oxide_assignment import (
    REASON_MASS_UNCLOSED,
    REASON_MISSING_COMPOSITION,
    REASON_UNPARSED_TOKEN,
)
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


# Recorded Phase/Mode blocks. Mode is the one-atom fraction, not a mass
# fraction. The stdout parser still returns it under mass_kg; physical
# mass comes from fraction[wt] on the subprocess path.
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


# Recorded ``fraction[wt]`` from the Stable mineral assemblage table.
# These are not the Mode column. qfm prints +0.00000 and is not a phase.
_LUNAR_MARE_WEIGHT_FRACTIONS = {
    1100: {
        "liq": 0.45209,
        "fsp": 0.25402,
        "cpx": 0.11616,
        "ol": 0.16864,
        "spl": 0.00910,
    },
    1200: {
        "liq": 0.96856,
        "ol": 0.02324,
        "spl": 0.00820,
    },
    1400: {
        "liq": 1.00000,
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
    lines.append("Stable mineral assemblage:")
    lines.append("phase fraction[wt] G[J]")
    for name, weight_fraction in _LUNAR_MARE_WEIGHT_FRACTIONS[temperature_C].items():
        lines.append(f"{name} {weight_fraction:+.5f} -1.00000")
    lines.append("qfm +0.00000 -1.00000")
    lines.append("SYS -1.00000")
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
def test_magemin_subprocess_scales_recorded_weight_fraction_and_keeps_oxide_wt(
    temperature_C, monkeypatch,
):
    """Recorded Verb=0 stdout plus the matlab weight table, through equilibrate.

    Mode is the one-atom fraction. Treating it as a mass fraction rebuilt
    13.794 kg of Al2O3 from 13.5 kg at 1100 C. fraction[wt] is the hosted
    assemblage's weight fraction (the qfm row is 0 and is not reassigned).
    """
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
    weights = _LUNAR_MARE_WEIGHT_FRACTIONS[temperature_C]
    material = _material_modes(temperature_C)
    assert set(result.phase_masses_kg) == set(weights) == set(material)
    hosted_scales = []
    for name, weight_fraction in weights.items():
        # The one-atom Mode times the batch is not this phase's mass.
        assert result.phase_masses_kg[name] != pytest.approx(
            material[name] * batch_kg, rel=1.0e-4,
        )
        hosted_scales.append(result.phase_masses_kg[name] / weight_fraction)
        recorded = _LUNAR_MARE_OXIDE_WT_FR[temperature_C][name]
        expected = {
            oxide: fraction * 100.0
            for oxide, fraction in zip(_OXIDE_HEADER, recorded)
            if fraction > 0.0
        }
        assert result.phase_compositions[name] == pytest.approx(expected)
        assert "Fe2O3" not in result.phase_compositions[name]
    # One scale for every phase: fraction[wt] of the hosted assemblage.
    assert hosted_scales == pytest.approx([hosted_scales[0]] * len(hosted_scales))
    sent = MAGEMinBackend()._build_db_bulk_projection(
        dict(_LUNAR_MARE_MAJORS_KG), database="ig",
    )
    sent_kg = {
        ("FeO" if name == "FeOt" else name): (
            batch_kg * float(value) / sent.projected_sum_wt_pct
        )
        for name, value in sent.composition_wt_pct.items()
    }
    # Hosted mass sits on the batch with the unhosted excess oxygen removed,
    # not on the full batch. fraction[wt] * batch_kg is the full batch.
    assert hosted_scales[0] == pytest.approx(batch_kg - sent_kg["O"], rel=0.01)
    assert hosted_scales[0] != pytest.approx(batch_kg, rel=0.01)
    for oxide, target_kg in sent_kg.items():
        if oxide == "O" or target_kg == 0.0:
            continue
        got_kg = sum(
            result.phase_masses_kg[name]
            * result.phase_compositions[name].get(oxide, 0.0)
            / 100.0
            for name in result.phase_masses_kg
        )
        # 5-decimal matlab print. Al2O3's old 0.294 kg miss fails this.
        assert got_kg == pytest.approx(target_kg, abs=max(0.002, 0.001 * target_kg))
    hosted_oxygen_kg = sum(
        result.phase_masses_kg[name]
        * result.phase_compositions[name].get("O", 0.0)
        / 100.0
        for name in result.phase_masses_kg
    )
    assert hosted_oxygen_kg < 0.5 * sent_kg["O"]
    assert result.liquid_fraction == pytest.approx(
        weights["liq"] / sum(weights.values())
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


def test_equilibrium_crystallization_path_inventory_defaults_empty():
    assert "isothermal_phase_inventories" in (
        EquilibriumCrystallizationPathResult.__dataclass_fields__
    )
    assert (
        EquilibriumCrystallizationPathResult().isothermal_phase_inventories
        == ()
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
        olivine = 1.0 - frac
        masses = {}
        compositions = {}
        present = []
        if frac > 0.0:
            masses["liquid"] = frac
            compositions["liquid"] = {"SiO2": 50.0, "MgO": 50.0}
            present.append("liquid")
        if olivine > 0.0:
            masses["olivine"] = olivine
            compositions["olivine"] = {"SiO2": 40.0, "MgO": 60.0}
            present.append("olivine")
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=float(kwargs["pressure_bar"]),
            liquid_fraction=frac,
            liquid_composition_wt_pct={"SiO2": 50.0, "MgO": 50.0},
            phases_present=present,
            phase_masses_kg=masses,
            phase_compositions=compositions,
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
    rows = {
        row["phase"]: row
        for row in diagnostic["isothermal_phase_inventories"]
    }
    # Request temperature is 1200 C, halfway from 1100 to 1300, so each
    # phase is 0.5 kg. Oxide moles are that mass times the weight percent.
    sio2 = parse_formula("SiO2").molar_mass_kg_per_mol()
    mgo = parse_formula("MgO").molar_mass_kg_per_mol()
    assert rows["liquid"]["mass_kg"] == pytest.approx(0.5)
    assert rows["olivine"]["mass_kg"] == pytest.approx(0.5)
    assert rows["liquid"]["oxide_mol"]["SiO2"] == pytest.approx(0.25 / sio2)
    assert rows["liquid"]["oxide_mol"]["MgO"] == pytest.approx(0.25 / mgo)
    assert rows["olivine"]["oxide_mol"]["SiO2"] == pytest.approx(0.20 / sio2)
    assert rows["olivine"]["oxide_mol"]["MgO"] == pytest.approx(0.30 / mgo)
    # The path samples the solidus-liquidus grid. The request temperature
    # is one more isothermal inventory call after that grid.
    assert backend.temperatures[:-1] == [
        point["temperature_C"] for point in path
    ]
    assert backend.temperatures[-1] == pytest.approx(1200.0)


class _RefuseOlivineECBackend(_PinECBackend):
    def equilibrate(self, **kwargs):
        result = super().equilibrate(**kwargs)
        if "olivine" in result.phase_compositions:
            result.phase_compositions["olivine"] = {"fo": 100.0}
        return result


def test_equilibrium_crystallization_refuses_one_phase_without_changing_path():
    backend = _RefuseOlivineECBackend()
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
    assert diagnostic["liquid_fraction"] == pytest.approx(
        path[-1]["liquid_fraction"]
    )
    phases = {
        row["phase"] for row in diagnostic["isothermal_phase_inventories"]
    }
    assert phases == {"liquid"}
    refusals = diagnostic["backend_diagnostics"][
        "isothermal_phase_inventory_refusals"
    ]
    assert refusals[0]["phase"] == "olivine"
    assert refusals[0]["reason"] == REASON_UNPARSED_TOKEN
    assert refusals[0]["token"] == "fo"


class _FailInventoryECBackend(_PinECBackend):
    def equilibrate(self, **kwargs):
        # 1100 to 1300 at the default 50 C step is five path samples.
        # The next call is the request-temperature inventory.
        if len(self.temperatures) >= 5:
            self.temperatures.append(float(kwargs["temperature_C"]))
            raise RuntimeError("isothermal boom")
        return super().equilibrate(**kwargs)


def test_equilibrium_crystallization_inventory_failure_keeps_the_path():
    backend = _FailInventoryECBackend()
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
    assert diagnostic["liquid_fraction"] == pytest.approx(
        path[-1]["liquid_fraction"]
    )
    assert "isothermal_phase_inventories" not in diagnostic
    failure = diagnostic["backend_diagnostics"][
        "isothermal_phase_inventory_failure"
    ]
    assert failure["reason"] == "isothermal_phase_inventory_sample_failed"
    assert backend.temperatures[-1] == pytest.approx(1200.0)


# dispComposition columns from PetThermoTools MELTS.py. H2O and CO2 are
# on that table and outside MELTS_OXIDE_BASIS.
_PTT_DISPOSITION_OXIDES = (
    "SiO2", "TiO2", "Al2O3", "Fe2O3", "Cr2O3", "FeO", "MnO",
    "MgO", "CaO", "Na2O", "K2O", "P2O5", "H2O", "CO2",
)

# Recorded rhyolite-MELTS 1.0.2 payload from PetThermoTools
# equilibrate_MELTS, 1150 C, 1 bar, FMQ, offset 0. Bulk before the
# engine's own renormalisation to 100 wt%: SiO2 48.5, TiO2 1.5,
# Al2O3 14, Fe3+/FeT 0.15, FeOt 11, Cr2O3 0.05, MnO 0.18, MgO 9,
# CaO 11, Na2O 2.4, K2O 0.2, P2O5 0.12, H2O 0.2, CO2 0.
# The seat has no meltsdynamic install. The capture loaded the
# example libalphamelts.dylib shipped with PetThermoTools
# CrystallisationTests, retargeted at the machine's GSL 2.8 because
# that dylib asks for GSL 2.7. Zeros are omitted; the frame fills them.
_RECORDED_PTT_CONDITIONS_MASS_G = 99.97515958192925
_RECORDED_PTT_LOG_FO2 = -8.884507255032851
_RECORDED_PTT_MASS_G = {
    "liquid1": 54.10600955382909,
    "olivine1": 7.894108519484482,
    "clinopyroxene1": 15.09693089071845,
    "plagioclase1": 22.57850280989757,
    "spinel1": 0.13227074729596594,
    "water1": 0.16733706070372292,
}
_RECORDED_PTT_WT_PCT = {
    "liquid1": {
        "SiO2": 49.67946491038592,
        "TiO2": 2.63790161546477,
        "Al2O3": 12.331406422631424,
        "Fe2O3": 2.46917828361544,
        "Cr2O3": 0.042186441036470616,
        "FeO": 12.660168155062683,
        "MnO": 0.25164853118073366,
        "MgO": 6.5490974449363515,
        "CaO": 9.90797474775172,
        "Na2O": 2.8263355706638076,
        "K2O": 0.35245392902769596,
        "P2O5": 0.22554759051141224,
        "H2O": 0.06663635773158368,
    },
    "olivine1": {
        "SiO2": 38.48933781621879,
        "FeO": 21.60942651804958,
        "MnO": 0.5940534370333811,
        "MgO": 38.84483441024793,
        "CaO": 0.462347818450318,
    },
    "clinopyroxene1": {
        "SiO2": 51.18759882145755,
        "TiO2": 0.5852105515748945,
        "Al2O3": 3.8028301765547954,
        "Fe2O3": 1.638346608307302,
        "FeO": 7.561930691070501,
        "MgO": 16.76041529611999,
        "CaO": 18.27278526596781,
        "Na2O": 0.19088258894716956,
    },
    "plagioclase1": {
        "SiO2": 51.715900910111785,
        "Al2O3": 30.896146177742875,
        "CaO": 13.422438472151688,
        "Na2O": 3.909299117044225,
        "K2O": 0.056215322949423045,
    },
    "spinel1": {
        "TiO2": 7.426342152079585,
        "Al2O3": 11.615282688001841,
        "Fe2O3": 24.51899886732191,
        "Cr2O3": 21.185662326197995,
        "FeO": 25.884235462429416,
        "MgO": 9.36947850396925,
    },
    "water1": {"H2O": 100.0},
}


def _ptt_composition_frame(weight_percent: dict[str, float]) -> pd.DataFrame:
    row = {oxide: 0.0 for oxide in _PTT_DISPOSITION_OXIDES}
    row.update(weight_percent)
    assert sum(row.values()) == pytest.approx(100.0)
    return pd.DataFrame([row], columns=list(_PTT_DISPOSITION_OXIDES))


def test_petthermotools_schema_compositions_are_not_missing():
    """The verified schema dict is partial, so mass does not close.

    Carrying the rows must refuse ``oxide_assignment_mass_unclosed``,
    not ``oxide_assignment_missing_composition``.
    """
    backend = AlphaMELTSBackend()
    result = backend._parse_petthermotools_result(
        ({
            "Conditions": {"mass": 100.0},
            "liquid1": {"SiO2": 50.0, "Al2O3": 15.0, "FeO": 10.0},
            "liquid1_prop": {"mass": 80.0},
            "olivine1": {"SiO2": 40.0, "MgO": 50.0},
            "olivine1_prop": {"mass": 20.0},
        }, {}),
        temperature_C=1200.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={"SiO2": 50.0, "Al2O3": 15.0, "FeO": 10.0},
        total_input_kg=10.0,
    )

    _rows, extra = _inventories_from_equilibrium(result)
    refusals = extra["isothermal_phase_inventory_refusals"]
    assert {item["phase"] for item in refusals} == {"liquid1", "olivine1"}
    assert {item["reason"] for item in refusals} == {REASON_MASS_UNCLOSED}
    assert REASON_MISSING_COMPOSITION not in {item["reason"] for item in refusals}
    assert result.ledger_transition is None


def test_recorded_petthermotools_payload_inventory_closes():
    backend = AlphaMELTSBackend()
    conditions_g = _RECORDED_PTT_CONDITIONS_MASS_G
    payload = {
        "Conditions": pd.DataFrame([{
            "temperature": 1150.0,
            "pressure": 1.0,
            "mass": conditions_g,
            "logfO2": _RECORDED_PTT_LOG_FO2,
        }]),
    }
    for phase, weight_percent in _RECORDED_PTT_WT_PCT.items():
        payload[phase] = _ptt_composition_frame(weight_percent)
        payload[f"{phase}_prop"] = pd.DataFrame([{
            "mass": _RECORDED_PTT_MASS_G[phase],
        }])
    result = backend._parse_petthermotools_result(
        (payload, {}),
        temperature_C=1150.0,
        pressure_bar=1.0,
        fO2_log=_RECORDED_PTT_LOG_FO2,
        comp_wt={"SiO2": 48.5},
        total_input_kg=conditions_g / 1000.0,
    )

    assert result.status == "ok"
    assert result.ledger_transition is None
    assert set(result.phase_masses_kg) == set(_RECORDED_PTT_MASS_G)
    assert result.liquid_fraction == pytest.approx(
        _RECORDED_PTT_MASS_G["liquid1"] / conditions_g
    )
    assert "H2O" not in result.liquid_composition_wt_pct
    for phase, mass_g in _RECORDED_PTT_MASS_G.items():
        assert result.phase_masses_kg[phase] == pytest.approx(mass_g / 1000.0)
        for oxide, wt_pct in _RECORDED_PTT_WT_PCT[phase].items():
            assert result.phase_compositions[phase][oxide] == pytest.approx(
                wt_pct
            )

    rows, extra = _inventories_from_equilibrium(result)
    assert extra == {}
    by_phase = {row["phase"]: row for row in rows}
    assert set(by_phase) == set(_RECORDED_PTT_MASS_G)
    for phase, weight_percent in _RECORDED_PTT_WT_PCT.items():
        mass_kg = _RECORDED_PTT_MASS_G[phase] / 1000.0
        oxides = by_phase[phase]["oxide_mol"]
        assert by_phase[phase]["mass_kg"] == pytest.approx(mass_kg)
        rebuilt_kg = 0.0
        for oxide, wt_pct in weight_percent.items():
            molar = parse_formula(oxide).molar_mass_kg_per_mol()
            component_kg = mass_kg * wt_pct / 100.0
            assert oxides[oxide] == pytest.approx(component_kg / molar)
            rebuilt_kg += component_kg
        assert rebuilt_kg == pytest.approx(mass_kg)
