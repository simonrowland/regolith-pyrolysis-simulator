from decimal import Decimal

from simulator.battery.generators.janaf import _interpolate_formation_gibbs


def test_mgo_janaf_fusion_gibbs_interpolation_numeric_pin() -> None:
    # Mg-008 at 1800 K is −345.918 kJ/mol; Mg-009 at 1800 K is −317.447
    # kJ/mol. The scorer's 1873 K value is their JANAF table interpolation.
    solid = ((Decimal("1800"), Decimal("-345.918")), (Decimal("1900"), Decimal("-342.212")))
    liquid = ((Decimal("1800"), Decimal("-317.447")), (Decimal("1900"), Decimal("-313.750")))
    assert _interpolate_formation_gibbs(solid, Decimal("1873")) == Decimal("-343.21262")
    assert _interpolate_formation_gibbs(liquid, Decimal("1873")) == Decimal("-314.74819")
