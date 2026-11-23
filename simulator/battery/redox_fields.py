"""Printed field names used to resolve measured iron redox state."""

from simulator.battery.enums import Quantity

FE3_FE2_RATIO_PRINTED_FIELDS: tuple[str, ...] = ("fe3_fe2_ratio", "Fe3_over_Fe2")
FERRIC_FRACTION_FIELDS: tuple[str, ...] = ("Fe3_over_FeT", "Fe3_over_sum_Fe")

# Quantities whose printed value is the ferric result being scored.
# The fO2 ladder must not invert them for the same point (SC-458 / DP-017).
# input_provenance uses the same set for the composition-echo exemption.
SCORED_REDOX_QUANTITIES = frozenset({Quantity.FE3_FE2_RATIO, Quantity.VALENCE_RATIO})
