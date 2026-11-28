from __future__ import annotations

import json
import math
import os
from dataclasses import replace
from pathlib import Path
import tempfile


SOURCE_ID = "kems-026-markova-1984"
TARGET_TOKEN = "markova_1984_table2_residual_melt_VI_lherzolite"
REPORT_DIR = Path(__file__).with_name("_markova_results")
TEMPERATURES_K = (
    1748.15, 1765.15, 1905.15, 1999.15, 2069.15, 2156.15,
    2233.15, 2311.15, 2391.15, 2480.15, 2564.15,
)
SPECIES = ("Al2O3", "CaO", "FeO", "MgO", "SiO2")


def _row_payload(residual, context, candidates, activity_case):
    reference = context.observations[residual.reference]
    identity = reference.identity
    candidate = candidates.get(residual.candidate)
    numeric = residual.numeric
    return {
        "activity_case": activity_case,
        "temperature_K": (
            None if identity.temperature_K is None or not identity.temperature_K.is_value
            else str(identity.temperature_K.value)
        ),
        "species": identity.species.formula,
        "observed_wt_pct": None if reference.value.point is None else str(reference.value.point),
        "predicted_wt_pct": (
            None if candidate is None or candidate.value.point is None
            else str(candidate.value.point)
        ),
        "residual": None if numeric is None else str(numeric.value),
        "residual_unit": None if numeric is None else numeric.unit,
        "metric_operation": None if numeric is None else numeric.operation.value,
        "status": residual.status.value,
        "refusal": None if residual.refusal is None else residual.refusal.reason.value,
        "refusal_detail": (
            None
            if residual.refusal is None
            else {str(key): str(value) for key, value in residual.refusal.detail.items()}
        ),
    }


def _k_crossover_temperature() -> float | None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        _new_internal_analytical_core,
        internal_analytical_residue_vapor_pressure,
    )
    from simulator.engine_cache import _engine_cache_path_scope

    composition_mol = {
        "SiO2": 0.0073258,
        "MgO": 0.00904038,
        "FeO": 0.00111898,
        "CaO": 0.00123924,
        "Al2O3": 0.000447189,
    }
    core = _new_internal_analytical_core()

    def log_k_ratio(temperature_K: float) -> float:
        response = internal_analytical_residue_vapor_pressure(
            core=core,
            temperature_C=temperature_K - 273.15,
            pressure_bar=1.0,
            composition_kg=None,
            composition_mol=composition_mol,
            fO2_log=-9.0,
            po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=1.0e-9),
            include_diagnostic_shadows=False,
        )
        pressures = response.vapor_pressures_Pa
        activities = response.activity_coefficients
        return math.log10(
            (float(pressures["SiO"]) / float(activities["SiO2"]))
            / (float(pressures["Mg"]) / float(activities["MgO"]))
        )

    with _engine_cache_path_scope(None):
        temperatures = tuple(float(t) for t in range(1700, 2501, 50))
        values = tuple(log_k_ratio(t) for t in temperatures)
        bracket = next(
            (
                (lt, rt, ly)
                for lt, rt, ly, ry in zip(temperatures, temperatures[1:], values, values[1:])
                if ly == 0.0 or ly * ry < 0.0
            ),
            None,
        )
        if bracket is None:
            return None
        left_t, right_t, left_y = bracket
        for _ in range(24):
            middle_t = (left_t + right_t) / 2.0
            middle_y = log_k_ratio(middle_t)
            if middle_y == 0.0:
                return middle_t
            if left_y * middle_y <= 0.0:
                right_t = middle_t
            else:
                left_t, left_y = middle_t, middle_y
        return (left_t + right_t) / 2.0


def run_cold_markova_vi_internal_analytical(activity_case):
    with tempfile.TemporaryDirectory(prefix="markova-parsed-cache-") as cache_dir:
        os.environ["REGOLITH_PARSED_YAML_CACHE"] = "on"
        os.environ["REGOLITH_PARSED_YAML_CACHE_DIR"] = cache_dir

        from simulator import equilibrium as equilibrium_module
        from simulator.battery.enums import Rail
        from simulator.battery.score import Engine, load_score_context, score_store
        from simulator.engine_cache import _engine_cache_path_scope

        context = load_score_context(sources=(SOURCE_ID,))
        target_observations = {
            observation_id: observation
            for observation_id, observation in context.observations.items()
            if TARGET_TOKEN in observation_id
        }
        assert target_observations, f"No Markova VI rows selected for {TARGET_TOKEN}"
        experiment_ids = {
            observation.experiment_id for observation in target_observations.values()
        }
        assert len(experiment_ids) == 1, f"Expected one VI experiment, got {experiment_ids}"
        # The Knudsen extent solve needs every VI oxide and mass-loss anchor.
        # Keep that experiment's complete observation context, then select the
        # requested composition-series rows only from its score output.
        scoring_observations = {
            observation_id: observation
            for observation_id, observation in context.observations.items()
            if observation.source_id == SOURCE_ID
            and observation.experiment_id in experiment_ids
        }
        context = replace(context, observations=scoring_observations)

        original_activity = equilibrium_module.melt_oxide_activity
        gamma_by_oxide = (
            {"SiO2": 0.18, "MgO": 1.82} if activity_case == "Kume2000-CMS" else {}
        )

        def diagnostic_activity(parent_oxide, account_mol, **kwargs):
            activity = original_activity(parent_oxide, account_mol, **kwargs)
            if activity is None:
                return None
            gamma = gamma_by_oxide.get(parent_oxide, 1.0)
            return replace(
                activity,
                gamma=gamma,
                effective_gamma=gamma,
                activity=gamma * activity.x_single_cation,
            )

        equilibrium_module.melt_oxide_activity = diagnostic_activity
        try:
            with _engine_cache_path_scope(None):
                residuals, candidates = score_store(
                    context,
                    engines=(Engine.INTERNAL_ANALYTICAL,),
                    rail=Rail.RESIDUE_COMPOSITION,
                    work_id=SOURCE_ID,
                    include_diagnostics=False,
                )
        finally:
            equilibrium_module.melt_oxide_activity = original_activity

        target_rows = []
        for residual in residuals:
            if TARGET_TOKEN not in residual.reference:
                continue
            observation = context.observations.get(residual.reference)
            if observation is None or observation.identity.species.formula not in SPECIES:
                continue
            temperature = observation.identity.temperature_K
            if temperature is None or not temperature.is_value:
                continue
            if float(temperature.value) in TEMPERATURES_K:
                target_rows.append(residual)
        assert len(target_rows) == len(TEMPERATURES_K) * len(SPECIES), (
            f"Expected {len(TEMPERATURES_K) * len(SPECIES)} Markova VI rows, got {len(target_rows)}"
        )
        rows = sorted(
            (_row_payload(residual, context, candidates, activity_case) for residual in target_rows),
            key=lambda row: (float(row["temperature_K"] or 0), row["species"]),
        )
        parsed_files = sum(1 for path in Path(cache_dir).rglob("*.pickle") if path.is_file())
        report = {
            "activity_case": activity_case,
            "warmth": {
                "engine_result_cache": "disabled via _engine_cache_path_scope(None)",
                "parsed_yaml_cache": "fresh empty private directory per test",
                "parsed_cache_files_created_during_score": parsed_files,
                "scorer_engine": Engine.INTERNAL_ANALYTICAL.value,
                "work_filter": SOURCE_ID,
                "row_filter": TARGET_TOKEN,
                "scored_experiment_observations": len(scoring_observations),
            },
            "rows": rows,
        }
        if activity_case == "gamma=1":
            report["k_crossover"] = {
                "definition": "log10(K_SiO_liquid / K_Mg_liquid) = 0; common pO2 and activities divided out",
                "temperature_K": _k_crossover_temperature(),
            }
        REPORT_DIR.mkdir(exist_ok=True)
        report_path = REPORT_DIR / f"{activity_case.replace('=', '-')}.json"
        report_path.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
