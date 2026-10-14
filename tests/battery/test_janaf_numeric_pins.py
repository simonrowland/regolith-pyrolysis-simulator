from decimal import Decimal

from simulator.battery.generators.janaf import (
    _interpolate_formation_gibbs,
    janaf_fusion_enthalpy,
)


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
