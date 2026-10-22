"""Knudsen extent anchors and a pre-refactor Langmuir numeric pin."""

from simulator.battery.residue import ResidueChannel, integrate_residue_inventory


def test_langmuir_pressure_refactor_numeric_pin():
    result = integrate_residue_inventory(
        {"FeO": 1.0, "MgO": 2.0},
        (
            ResidueChannel("Fe", "Fe", "FeO", 1.0, "pin"),
            ResidueChannel("Mg", "Mg", "MgO", 1.0, "pin"),
            ResidueChannel("O2", "O2", None, 1.0, "pin"),
        ),
        lambda n, p: {
            "Fe": 5e-8 * 10 ** (-p / 2),
            "Mg": 2e-8 * 10 ** (-p / 2),
            "O2": 10**p * 1e5,
        },
        temperature_K=2000, duration_s=10, area_evolution_m2=(0.001,) * 3,
    )
    assert {k: v.hex() for k, v in result.residue_mol.items()} == {
        "FeO": "0x1.fffffcbbc23c7p-1", "MgO": "0x1.ffffff027bd22p+0",
    }
    assert {k: v.hex() for k, v in result.evaporated_mol.items()} == {
        "Fe": "0x1.a21ee1c66bbd4p-24", "Mg": "0x1.fb085bb1c9290p-25",
        "O2": "0x1.4fd187cfa828ep-24",
    }
    assert result.pO2_bar_by_step == (float.fromhex("0x1.36ad1d112f1c7p-28"),) * 3
    assert result.atom_closure_mol == {"Fe": 0.0, "Mg": 0.0, "O": 0.0}
