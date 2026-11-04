from decimal import Decimal

from simulator.battery.generators.janaf import (
    _interpolate_formation_gibbs,
    janaf_fusion_enthalpy,
    janaf_fusion_entropy,
    janaf_fusion_energy,
)
from simulator.reference_data.janaf_fusion import d144_fusion_delta_g_kJ_per_mol


def test_mgo_janaf_fusion_gibbs_interpolation_numeric_pin() -> None:
    # These synthetic points isolate linear interpolation; they are not
    # printed JANAF values or evidence for the scorer's source tables.
    solid = ((Decimal("1800"), Decimal("-345.918")), (Decimal("1900"), Decimal("-342.212")))
    liquid = ((Decimal("1800"), Decimal("-317.447")), (Decimal("1900"), Decimal("-313.750")))
    assert _interpolate_formation_gibbs(solid, Decimal("1873")) == Decimal("-343.21262")
    assert _interpolate_formation_gibbs(liquid, Decimal("1873")) == Decimal("-314.74819")


def test_mgo_janaf_fusion_enthalpy_uses_printed_3000_k_values() -> None:
    # Mg-008/Mg-009 print −709.524/−632.496 kJ/mol at 3000 K.
    enthalpy, *_ = janaf_fusion_enthalpy("MgO", Decimal("3000"))
    assert enthalpy == Decimal("77028")


def test_mgo_janaf_fusion_entropy_uses_printed_3000_k_values() -> None:
    # Mg-008/Mg-009 print S=142.489/167.293 J/(mol K) at 3000 K.
    entropy, _crossing = janaf_fusion_entropy("MgO", Decimal("3000"))
    assert entropy == Decimal("24.804")


def test_janaf_fusion_crossing_numeric_pins() -> None:
    # Preserve the crossing resolver's published-table interpolation before
    # moving the shared calculation into reference_data.janaf_fusion.
    assert janaf_fusion_energy("MgO", Decimal("3000")).melting_temperature_K == (
        Decimal("3104.968203497615262321144674")
    )
    assert janaf_fusion_energy("CaO", Decimal("3000")).melting_temperature_K == (
        Decimal("3200.0")
    )


def test_d144_janaf_fusion_gibbs_temperature_chord_numeric_pins() -> None:
    expected_kJ_per_mol = {
        ("CaO", Decimal("1873")): Decimal("32.59870"),
        ("CaO", Decimal("1973")): Decimal("30.18797"),
        ("MgO", Decimal("1873")): Decimal("28.47061"),
        ("MgO", Decimal("1973")): Decimal("26.32615"),
    }
    for (oxide, temperature_K), expected in expected_kJ_per_mol.items():
        assert janaf_fusion_energy(oxide, temperature_K).delta_g_fus_kJ_per_mol == expected
        assert d144_fusion_delta_g_kJ_per_mol(oxide, temperature_K) == expected
