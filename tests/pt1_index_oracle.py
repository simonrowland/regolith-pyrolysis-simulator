from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def independent_ladder_distance(
    key: Mapping[str, Any],
    *,
    sig_figs: float,
    include_controls: bool,
) -> float:
    def snap(value: float) -> float:
        if value == 0.0:
            return 0.0
        quantum = 10.0 ** (
            math.floor(math.log10(abs(value))) + 1.0 - sig_figs
        )
        rounded_center = round(value / quantum) * quantum
        if rounded_center == 0.0:
            return 0.0
        decimal_places = max(
            0,
            int(
                math.ceil(
                    sig_figs
                    - math.floor(math.log10(abs(rounded_center)))
                    - 1.0
                )
            ),
        )
        return round(rounded_center, decimal_places)

    def normalized_square(exact: float, center: float) -> float:
        reference = exact if exact != 0.0 else center
        quantum = (
            10.0 ** (
                math.floor(math.log10(abs(reference)))
                + 1.0
                - sig_figs
            )
            if reference != 0.0
            else 10.0 ** (1.0 - sig_figs)
        )
        delta = (exact - center) / quantum
        return delta * delta

    # Recompute the documented normalized offsets directly; this deliberately
    # avoids the production ladder-distance resolver used by the writer.
    components = []
    for component in key["composition_mol_fraction"]:
        if isinstance(component, Mapping):
            species = str(component["species"])
            exact = float(component["mol_fraction"])
        else:
            raw_species, raw_fraction = component
            species = str(raw_species)
            exact = float(raw_fraction)
        components.append((species, exact))

    total = 0.0
    for _species, exact in sorted(components):
        total += normalized_square(exact, snap(exact))
    controls = key.get("controls", {})
    temperature = float(controls.get("T_K", 0.0) or 0.0)
    total += normalized_square(temperature, snap(temperature))
    if include_controls:
        for control_name in ("pressure_bar", "pO2_bar"):
            if control_name not in controls:
                continue
            control_value = float(controls[control_name])
            total += normalized_square(
                control_value,
                snap(control_value),
            )
    return float(round(total, 15))
