#!/usr/bin/env python3
"""Print the active vapour-head pressure sources at one temperature."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.evaporation import _pre_rg_effective_pressure_source  # noqa: E402
from simulator.state import CampaignPhase  # noqa: E402
from simulator.vapour_rail.batch import PressureValue  # noqa: E402
from simulator.vapour_rail.instrumentation import (  # noqa: E402
    flux_pressures_from_batch,
)
from tests.chemistry.conftest import _build_sim  # noqa: E402


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "data" / name).read_text())


def _active_pressure_rows(temperature_C: float) -> list[dict[str, object]]:
    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = temperature_C
    equilibrium = sim._get_equilibrium()
    effective_source = _pre_rg_effective_pressure_source(
        sim.vapor_pressures,
        equilibrium,
    )
    batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=equilibrium.temperature_C + 273.15,
        effective_pressure_source=effective_source,
    )
    if batch is None:
        raise RuntimeError("vapour batch resolution returned no batch")
    selected_pressures, overlay = flux_pressures_from_batch(
        batch,
        effective_pressure_source=effective_source,
    )

    rows: list[dict[str, object]] = []
    for species_id in sorted(batch.flux_active_species_ids):
        answer = batch.channel(species_id)
        if not isinstance(answer.pressure, PressureValue):
            raise RuntimeError(f"active channel {species_id} has no point pressure")
        selected_pa = float(selected_pressures[species_id])
        continuation_pa = float(answer.pressure.pa)
        seam_value = effective_source.pressure_pa(species_id)
        seam_pa = None if seam_value is None else float(seam_value)
        if not all(
            math.isfinite(value)
            for value in (selected_pa, continuation_pa)
        ):
            raise RuntimeError(f"non-finite pressure for {species_id}")
        rows.append(
            {
                "species": species_id,
                "selected_source": overlay[
                    "selected_pressure_source_by_species"
                ][species_id],
                "selected_pa": selected_pa,
                "seam_pa": seam_pa,
                "continuation_pa": continuation_pa,
                "out_of_range": bool(answer.extra.get("out_of_range", False)),
            }
        )
    return rows


def _pressure_text(value: object) -> str:
    return "absent" if value is None else repr(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--temperature-c", type=float, default=1650.0)
    args = parser.parse_args()

    print(f"active vapour-head pressures at {args.temperature_c:g} C")
    print(
        "| species | selected source | selected Pa | seam Pa | "
        "continuation Pa | out_of_range |"
    )
    print("|---|---|---:|---:|---:|---|")
    for row in _active_pressure_rows(args.temperature_c):
        print(
            f"| {row['species']} | {row['selected_source']} | "
            f"{_pressure_text(row['selected_pa'])} | "
            f"{_pressure_text(row['seam_pa'])} | "
            f"{_pressure_text(row['continuation_pa'])} | "
            f"{str(row['out_of_range']).lower()} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
