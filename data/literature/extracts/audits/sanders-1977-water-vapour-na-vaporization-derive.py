"""Reproduce Sanders and Haller's Eq. (13) P_t values at 50-digit precision."""

from __future__ import annotations

from decimal import Decimal, localcontext
from pathlib import Path

import yaml


SOURCE_ID = "sanders-1977-water-vapour-na-vaporization"
EXTRACT = Path(__file__).resolve().parents[1] / f"{SOURCE_ID}.yaml"


def main() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    density_rows = [
        observation
        for observation in document["species"]["Na"]["observations"]
        if "_density_" in observation["observation_id"]
    ]
    with localcontext() as context:
        context.prec = 50
        factor = Decimal("82.06") * Decimal("273.15") / Decimal("22.99")
        print("observation_id,source_density_g_STP_ml,P_t_atm")
        for density in density_rows:
            rho_t = Decimal(density["values"]["value"])
            pressure_atm = factor * rho_t
            series_and_row = density["observation_id"].removeprefix(
                "sanders_1977_table1_"
            )
            series, row = series_and_row.split("_density_")
            pressure_id = f"sanders_1977_eq13_{series}_row_{row}_p_t"
            print(
                f"{pressure_id},{rho_t},{format(pressure_atm.normalize(), 'f')}"
            )


if __name__ == "__main__":
    main()
