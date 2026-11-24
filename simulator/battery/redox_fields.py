"""Printed field names used to resolve measured iron redox state."""

from simulator.battery.enums import Quantity

FE3_FE2_RATIO_PRINTED_FIELDS: tuple[str, ...] = ("fe3_fe2_ratio", "Fe3_over_Fe2")
FERRIC_FRACTION_FIELDS: tuple[str, ...] = ("Fe3_over_FeT", "Fe3_over_sum_Fe")

# Redox observables with ferric evidence, used by input_provenance for its
# composition-echo exemption. The ladder applies its self-inversion exception
# only when the target itself is ferric iron.
# Only a ferric-iron target is forbidden from using its own value as an
# independent oxygen condition. Named non-Fe couples still need that Fe
# evidence to evaluate the couple.
SCORED_REDOX_QUANTITIES = frozenset({Quantity.FE3_FE2_RATIO, Quantity.VALENCE_RATIO})
