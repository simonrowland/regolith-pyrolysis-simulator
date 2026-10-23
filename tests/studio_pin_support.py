"""Shared live quantities for the Studio pin emitter and its invariant tests."""

import json
from pathlib import Path
from unittest.mock import patch

from simulator.runner import build_sio_yield_report


def sio_evolved_baseline(feedstock: str) -> float:
    path = Path(__file__).parent / "fixtures" / "sio_yield" / f"{feedstock}_c2a.json"
    return json.loads(path.read_text(encoding="utf-8"))["sio_evolved_kg"]


def sio_report_at_wall_temperature(liner_temperature_c: float, *, feedstock="lunar_mare_low_ti"):
    return build_sio_yield_report(
        feedstock_id=feedstock,
        hours=24,
        mass_kg=1000.0,
        include_diagnostics=True,
        liner_temperature_c=liner_temperature_c,
        pO2_mbar=None,
        allow_unmeasured_alpha_fallback=True,
    )


def silica_product_kg(products: dict) -> float:
    return float(products.get("Si", 0.0)) + float(products.get("SiO2", 0.0))


def sio_wall_report_at_temperature(liner_temperature_c: float):
    """The wall-step regression deliberately exercises unavailable MAGEMin.

    This is a fallback-path test probe, not real-engine golden evidence.
    Keep it separate from the live-engine evolved-yield/stage-3 probes.
    """
    from simulator.melt_backend.magemin import MAGEMinBackend

    with patch.object(MAGEMinBackend, "initialize", return_value=False):
        return sio_report_at_wall_temperature(liner_temperature_c)


def sio_pin_values(report: dict) -> dict:
    wall = report["wall_deposit_kg"]
    return {
        "sio_evolved_kg": float(report["sio_evolved_kg"]),
        "sio_stage3_silica_kg": float(report["sio_to_silica_fume_kg"]["stage_3_sio_zone_product"]),
        "sio_wall_deposit_kg": silica_product_kg(wall),
        "wall_SiO_kg": float(wall.get("SiO", 0.0)),
    }


def capacity_pin_values(snapshot) -> dict:
    # The older carrier pin excludes these three P channels; the head-result
    # tuple pins the full flux separately. Both quantities are kg/hr.
    p_flux = sum(snapshot.evap_flux.species_kg_hr[species] for species in ("PO", "PO2", "P2"))
    return {
        "hour": snapshot.hour,
        "temperature_C": snapshot.temperature_C,
        "total_kg_hr": float(snapshot.evap_flux.total_kg_hr),
        "non_phosphorus_kg_hr": snapshot.evap_flux.total_kg_hr - p_flux,
        "transport_saturation_pct": float(snapshot.overhead.transport_saturation_pct),
        "melt_mass_kg": float(snapshot.melt_mass_kg),
    }


def staged_pin_values(sim) -> dict:
    return {
        "staged_silica_kg": silica_product_kg(sim.train.stages[3].collected_kg),
        "staged_product_sio_kg": float(sim.product_ledger().get("SiO", 0.0)),
    }
