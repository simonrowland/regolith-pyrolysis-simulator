#!/usr/bin/env python3
"""Compare warm and forced-cold internal-analytical trajectories."""

from __future__ import annotations

import dataclasses
import argparse
import json
import math
import statistics
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import yaml


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _numbers(value: Any, path: tuple[str, ...] = ()) -> dict[str, float]:
    value = _plain(value)
    if isinstance(value, Mapping):
        return {
            key: number
            for child, item in value.items()
            for key, number in _numbers(item, (*path, str(child))).items()
        }
    if isinstance(value, list):
        return {
            key: number
            for index, item in enumerate(value)
            for key, number in _numbers(item, (*path, str(index))).items()
        }
    if isinstance(value, bool):
        return {}
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return {".".join(path): float(value)}
    return {}


def _special_values(value: Any, path: tuple[str, ...] = ()) -> dict[str, Any]:
    value = _plain(value)
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            child_path = (*path, str(key))
            if str(key).lower() in {"status", "refusal", "run_refusal", "flag", "flags"}:
                result[".".join(child_path)] = item
            result.update(_special_values(item, child_path))
        return result
    if isinstance(value, list):
        return {
            key: item
            for index, child in enumerate(value)
            for key, item in _special_values(child, (*path, str(index))).items()
        }
    return {}


def _category(path: str) -> str | None:
    key = path.lower()
    if "vapor_pressure" in key or "vapour_pressure" in key:
        return "vapour pressures"
    if "activit" in key:
        return "activities"
    if "flux" in key or "evaporated_mol" in key:
        return "fluxes"
    if "residue" in key or "composition" in key or "inventory_mol" in key:
        return "residue compositions"
    if "ledger" in key or "total" in key or "cumulative" in key:
        return "ledger totals"
    return None


def _relative_difference(left: float, right: float) -> float:
    scale = max(abs(left), abs(right))
    return 0.0 if scale == 0.0 else abs(left - right) / scale


def _comparison(warm: Any, cold: Any) -> dict[str, Any]:
    warm_values = _numbers(warm)
    cold_values = _numbers(cold)
    differences: dict[str, list[float]] = {}
    paths = set(warm_values) | set(cold_values)
    for path in paths:
        category = _category(path)
        if category is None:
            continue
        if path not in warm_values or path not in cold_values:
            difference = 1.0
        else:
            difference = _relative_difference(warm_values[path], cold_values[path])
        differences.setdefault(category, []).append(difference)
    warm_special = _special_values(warm)
    cold_special = _special_values(cold)
    special_paths = set(warm_special) | set(cold_special)
    changed_special = sorted(
        path for path in special_paths if warm_special.get(path) != cold_special.get(path)
    )
    return {
        "outputs": {
            category: {
                "max_relative_difference": max(values),
                "median_relative_difference": statistics.median(values),
                "numeric_values_compared": len(values),
            }
            for category, values in sorted(differences.items())
        },
        "changed_status_refusal_flag_rows": changed_special,
    }


def _measure(label: str, consumer: Callable[[], Any]) -> dict[str, Any]:
    from simulator.core import PyrolysisSimulator
    from simulator.chemistry.kernel.capabilities import ChemistryIntent

    dispatch = PyrolysisSimulator._dispatch_only
    refresh = PyrolysisSimulator._refresh_vapor_pressures_from_kernel

    def run(cold_start: bool) -> tuple[Any, dict[str, int], list[dict[str, Any]]]:
        counts = {"path_a_later_success": 0, "path_b_refusal_continuation": 0}
        call_outputs: list[dict[str, Any]] = []

        def traced_dispatch(core, intent, *args, **kwargs):
            result = dispatch(core, intent, *args, **kwargs)
            if intent is ChemistryIntent.VAPOR_PRESSURE:
                diagnostic = (getattr(result, "diagnostic", None) or {}).get(
                    "high_t_melt_activity", {}
                )
                request_diagnostic = getattr(result, "diagnostic", None) or {}
                call_outputs.append(
                    {
                        "vapor_pressures_Pa": request_diagnostic.get(
                            "vapor_pressures_Pa", {}
                        ),
                        "activities": diagnostic.get("activities_by_oxide", {}),
                        "status": getattr(result, "status", None),
                        "refusal": diagnostic.get("fallback_reason"),
                        "flags": diagnostic.get("openimcc_flags", ()),
                    }
                )
                current_certificates = diagnostic.get(
                    "certified_effective_gamma_by_parent", {}
                )
                if any(
                    "gamma_prev" in record
                    for record in current_certificates.values()
                ):
                    counts["path_a_later_success"] += 1
                held = diagnostic.get("held_effective_gamma_by_parent", {})
                if any(
                    row.get("method") in {
                        "extrapolated_first_order_ln_gamma",
                        "extrapolation_capped_frozen",
                    }
                    for row in held.values()
                ):
                    counts["path_b_refusal_continuation"] += 1
            return result

        def cold_refresh(core, result, *args, **kwargs):
            core._last_high_t_melt_activity = {}
            return refresh(core, result, *args, **kwargs)

        PyrolysisSimulator._dispatch_only = traced_dispatch
        if cold_start:
            PyrolysisSimulator._refresh_vapor_pressures_from_kernel = cold_refresh
        try:
            return _plain(consumer()), counts, call_outputs
        finally:
            PyrolysisSimulator._dispatch_only = dispatch
            PyrolysisSimulator._refresh_vapor_pressures_from_kernel = refresh

    warm, warm_counts, warm_calls = run(False)
    cold, cold_counts, cold_calls = run(True)
    return {
        "consumer": label,
        "calls": {"warm": warm_counts, "forced_cold": cold_counts},
        **_comparison(
            {"consumer_output": warm, "ia_request_outputs": warm_calls},
            {"consumer_output": cold, "ia_request_outputs": cold_calls},
        ),
    }


def _residue_probes(only: str | None = None) -> list[dict[str, Any]]:
    from simulator.battery.residue import (
        ResidueChannel,
        _predict_hashimoto_residue_cohort,
        _predict_sossi_residue_cohort,
        integrate_knudsen_residue_inventory,
    )

    catalog = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    hashimoto_source = yaml.safe_load(
        (DATA / "literature/extracts/kems-015-hashimoto-1983.yaml").read_text()
    )
    hashimoto_store = yaml.safe_load(
        (DATA / "literature/extracts-v2/kems-015-hashimoto-1983.yaml").read_text()
    )
    measured_prefix = "::hashimoto_1983_table3_residue_composition_series::"
    experiment_ids = sorted(
        {
            row["experiment_id"].split("::experiment::")[-1]
            for row in hashimoto_store["observations"]
            if measured_prefix in row["observation_id"]
            and row["value"].get("kind") == "point"
        }
    )
    experiments_by_id = {
        row["experiment_id"]: row for row in hashimoto_source["experiments"]
    }
    geometry = next(
        row["values"]["starting_preform_mm"]
        for row in hashimoto_source["metadata"]
        if row["observation_id"] == "hashimoto_1983_fe_fcmas_free_evap_geometry_quoted"
    )
    hashimoto = lambda: _predict_hashimoto_residue_cohort(
        (experiments_by_id[experiment_ids[0]],),
        catalog,
        preform_dimensions_mm=geometry,
        code_revision="ia-continuation-probe",
        engine="internal-analytical",
        _allow_partial_cohort_for_test=True,
    )

    sossi_source = yaml.safe_load(
        (DATA / "literature/extracts/kems-012-sossi-2019.yaml").read_text()
    )
    sossi_experiment = sossi_source["experiments"][0]
    sossi_id = sossi_experiment["experiment_id"]
    sossi_trace = {
        element: sossi_source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    sossi = lambda: _predict_sossi_residue_cohort(
        [sossi_experiment],
        catalog,
        starting_trace_ppm_by_experiment={sossi_id: sossi_trace},
        buffered_fO2_log_by_experiment={sossi_id: -10.0},
        code_revision="ia-continuation-probe",
        engine="internal-analytical",
    )

    def knudsen():
        from simulator.diagnostic_helpers.binary_pot_battery import (
            PO2_COMMANDED,
            Po2Request,
            _internal_analytical_vapor_pressure_adapter,
            _new_internal_analytical_core,
        )

        core = _new_internal_analytical_core()
        inventory = {"FeO": 1.0, "SiO2": 1.0}

        def pressures(composition, logp, temperature_K):
            response = _internal_analytical_vapor_pressure_adapter(
                core=core,
                temperature_C=temperature_K - 273.15,
                pressure_bar=1.0e-6,
                composition_kg=None,
                composition_mol=composition,
                fO2_log=logp,
                po2_request=Po2Request(
                    mode=PO2_COMMANDED, po2_bar=10.0**logp
                ),
                include_diagnostic_shadows=False,
            )
            values = dict(response.vapor_pressures_Pa)
            values.setdefault("O2", 10.0**logp * 100_000.0)
            return values

        return integrate_knudsen_residue_inventory(
            inventory,
            (
                ResidueChannel("Fe", "Fe", "FeO", 1.0, "probe"),
                ResidueChannel("O2", "O2", None, 1.0, "probe"),
            ),
            pressures,
            temperature_path=((0.10, 2000.0), (0.20, 2050.0)),
            measured_mass_loss_fraction=0.20,
            near_saturated=True,
            source_fO2_log_by_interval=(-10.0, -10.0),
            linear_temperature=True,
        )

    probes = {
        "hashimoto": ("Hashimoto residue integration", hashimoto),
        "sossi": ("Sossi residue integration", sossi),
        "knudsen": ("Knudsen residue integration", knudsen),
    }
    return [
        _measure(label, consumer)
        for key, (label, consumer) in probes.items()
        if only is None or only == key
    ]


def _runner_probes(only: str | None = None) -> list[dict[str, Any]]:
    from unittest.mock import patch

    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
    from simulator.runner import PyrolysisRun

    scenarios = (
        ("lunar_mare_low_ti_C0_24h", "lunar_mare_low_ti", "C0", 9, {}),
        ("mars_basalt_C2A_12h", "mars_basalt", "C2A", 3, {"C": 30.0}),
        (
            "ci_carbonaceous_chondrite_C2B_12h",
            "ci_carbonaceous_chondrite",
            "C2B",
            12,
            {},
        ),
    )
    output = []
    for name, feedstock, campaign, hours, additives in scenarios:
        key = {
            "lunar_mare_low_ti_C0_24h": "runner-c0",
            "mars_basalt_C2A_12h": "runner-c2a",
            "ci_carbonaceous_chondrite_C2B_12h": "runner-c2b",
        }[name]
        if only is not None and only != key:
            continue

        def run():
            flux_maps = []
            dispatch = BuiltinEvaporationFluxProvider.dispatch

            def capture(provider, request):
                result = dispatch(provider, request)
                flux_maps.append(
                    (result.diagnostic or {}).get("evaporation_flux_kg_hr")
                )
                return result

            runner = PyrolysisRun(
                feedstock_id=feedstock,
                campaign=campaign,
                hours=hours,
                additives_kg=dict(additives),
                allow_fallback_vapor=True,
                allow_unmeasured_alpha_fallback=True,
                run_metadata_overrides={
                    "started_at_utc": "2026-05-15T00:00:00Z",
                    "kernel_commit_sha": "ia-continuation-probe",
                },
            )
            with patch.object(BuiltinEvaporationFluxProvider, "dispatch", capture):
                payload = runner.run()
            return {"payload": payload, "flux_maps": flux_maps}

        output.append(_measure(f"runner {name} ({hours} hours)", run))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--consumer",
        choices=("all", "hashimoto", "sossi", "knudsen", "runner-c0", "runner-c2a", "runner-c2b"),
        default="all",
    )
    consumer = parser.parse_args().consumer
    selected = None if consumer == "all" else consumer
    probes = (
        _runner_probes(selected)
        if selected is not None and selected.startswith("runner-")
        else _residue_probes(selected)
        if selected is not None
        else _residue_probes() + _runner_probes()
    )
    print(json.dumps(probes, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
