from __future__ import annotations

import copy
from collections.abc import Mapping
from math import isclose
from pathlib import Path

import pytest
import yaml

from simulator.equipment import EquipmentDesigner
from simulator.lab_geometry import parse_lab_geometry
from simulator.lab_schedule import normalize_lab_schedule
from simulator.teststand import parse_teststand
from simulator import runner as runner_module
from simulator.state import MeltState


REPO_ROOT = Path(__file__).resolve().parents[1]
PRESET_DIR = REPO_ROOT / "data" / "presets" / "vacuum_pyrolysis"
PRESET_PATH = PRESET_DIR / "robinot_2026.yaml"
POMEROY_PRESET_PATH = PRESET_DIR / "pomeroy_cardiff_2006.yaml"
SESKO_PRESET_PATH = PRESET_DIR / "sesko_2024.yaml"
MEASUREMENTS_PATH = REPO_ROOT / "data" / "literature" / "vacuum_pyrolysis_measurements.yaml"
FEEDSTOCKS_PATH = REPO_ROOT / "data" / "feedstocks.yaml"

ROBINOT_CARRIER_GAS = "Ar"
ROBINOT_PRESSURE_MBAR = pytest.approx(13.0)
ROBINOT_FLOW_NL_MIN = pytest.approx(0.3)
ROBINOT_SAMPLE_MASS_G = pytest.approx(3.38)
ROBINOT_DURATION_H = pytest.approx(1.0)
ROBINOT_RUN_DURATION_H = pytest.approx(59.0 / 60.0)
ROBINOT_PEAK_TEMPERATURE_C = pytest.approx(1800.0)
ROBINOT_O2_RELEASED_MG = pytest.approx(35.0)
ROBINOT_GLASSY_LUMP_G = pytest.approx(1.82)
ROBINOT_TOTAL_CAPTURED_G = pytest.approx(1.1)
ROBINOT_UNACCOUNTED_G = pytest.approx(0.27)
ROBINOT_MASS_RECOVERY_PERCENT = pytest.approx(92.0)
ROBINOT_CONFLICT_KG = {0.0011, 0.0013}
ROBINOT_FIG4C_IMAGE_PATH = (
    "docs-private/research/2026-06-12-vpr-sources/figures/fig4-composite.jpg"
)
ROBINOT_FIG4C_LOCATION_MASSES_G = {
    "glass_lump_on_holder": 1.82,
    "holder": 0.2,
    "window": 0.35,
    "condenser": 0.2,
    "filter": 0.51,
    "O2": 0.035,
    "discrepancy": 0.265,
}
ROBINOT_FIG4C_CAPTURED_LOCATION_IDS = {"holder", "window", "condenser", "filter"}
ROBINOT_FIG4C_CAPTURED_G = pytest.approx(1.26)
ROBINOT_FIG4C_SOURCE_CLASS = "digitized_from_figure"
FIGURE_MASS_SOURCE_CLASSES = {ROBINOT_FIG4C_SOURCE_CLASS}
NOT_REPORTED = "not_reported"
ROBINOT_INTERPOLATION = "piecewise_linear"
ROBINOT_INTERPOLATION_SOURCE_CLASS = "assumption_with_sensitivity_marker"
KINETICS_CAVEATS = {
    "none",
    "furnace_scale_bulk_mixing_assumption",
    "blocked_missing_gram_scale_kinetics_model",
}
ROBINOT_EAC1_ELEMENTAL_WT_PCT = {
    "O": 44.0,
    "Na": 2.0,
    "Mg": 6.0,
    "Al": 1.0,
    "P": 0.0,
    "Si": 25.0,
    "K": 3.0,
    "Ca": 10.0,
    "Ti": 2.0,
    "Fe": 3.0,
}
SESKO_PRESSURE_REPORTED_MIN_MBAR = pytest.approx(1.0e-7)
SESKO_PRESSURE_REPORTED_MAX_MBAR = pytest.approx(1.0e-2)
SESKO_PRESSURE_RUNTIME_FLOOR_MBAR = pytest.approx(1.0e-3)
SESKO_PRESSURE_RUNTIME_DELTA_ID = (
    "pressure_floor_delta_reported_uhv_min_to_runtime_floor"
)
SESKO_TEMP_MIN_C = pytest.approx(1476.85)
SESKO_TEMP_MAX_C = pytest.approx(1626.85)
SESKO_SAMPLE_MASS_G = pytest.approx(10.0)
SESKO_WINDOW_APERTURE_MM = pytest.approx(98.6)
SESKO_WINDOW_AREA_M2 = pytest.approx(0.00763561)
SESKO_DEPOSIT_THICKNESS_UM = pytest.approx(31.3)
SESKO_TEMP_EMISSIVITY = pytest.approx(0.98)
SESKO_TRACKING_INTERVAL_MIN = pytest.approx(5)
SESKO_FIXED_MIRROR_ANGLE_DEG = pytest.approx(45)
POMEROY_DURATION_H = pytest.approx(4.0)
POMEROY_OUTGAS_END_H = pytest.approx(2.0)
POMEROY_RAMP_END_H = pytest.approx(3.6666666666666665)
POMEROY_HOLD_TEMPERATURE_C = pytest.approx(1400.0)
POMEROY_MASS_LOSS_FRACTION = pytest.approx(0.0117)
MLS_1A_MAJOR_OXIDES = {
    "SiO2": 42.8,
    "TiO2": 6.77,
    "Al2O3": 12.1,
    "FeO": 16.3,
    "MgO": 6.19,
    "MnO": 0.22,
    "CaO": 11.1,
    "Na2O": 2.22,
    "K2O": 0.20,
    "P2O5": 0.04,
}


class SchemaValidationError(AssertionError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def require(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise SchemaValidationError(code, detail)


def validate_vpr_schema(preset: dict, measurements: dict) -> None:
    schedule = preset.get("lab_schedule", {})
    require(
        bool(schedule.get("interpolation")),
        "missing_schedule_interpolation",
        "lab_schedule.interpolation required",
    )
    require(
        bool(schedule.get("interpolation_source_class")),
        "schedule_interpolation_missing_source_class",
        "lab_schedule.interpolation_source_class required",
    )
    if schedule.get("interpolation_source_class") == "assumption_with_sensitivity_marker":
        require(
            bool(schedule.get("interpolation_citation_id") and schedule.get("interpolation_extraction_note")),
            "schedule_interpolation_missing_assumption_note",
            "assumed interpolation requires citation and extraction note",
        )
    require_schedule_point_units(schedule)

    gas_boundary = schedule.get("gas_boundary", {})
    gas = gas_boundary.get("background_gas")
    require_gas_boundary_value(gas, ("species",), "missing_carrier_gas", "background_gas")
    require_gas_boundary_source(gas, "background_gas")

    imposed_flow = gas_boundary.get("imposed_flow")
    require_gas_boundary_value(
        imposed_flow,
        ("value", "unit"),
        "missing_gas_boundary_imposed_flow",
        "imposed_flow",
    )
    require_gas_boundary_source(imposed_flow, "imposed_flow")

    pressure_control = gas_boundary.get("pressure_control")
    require_gas_boundary_value(
        pressure_control,
        ("mode",),
        "missing_gas_boundary_pressure_control",
        "pressure_control",
    )
    require_gas_boundary_source(pressure_control, "pressure_control")

    for pair_name, pair in preset.get("pair", {}).items():
        caveat = pair.get("kinetics_caveat")
        require(caveat in KINETICS_CAVEATS, "invalid_kinetics_caveat", pair_name)
        cover = pair.get("mitigation", {})
        if cover == "none":
            continue
        pO2_cover = cover.get("pO2_cover", {})
        if not pO2_cover.get("enabled"):
            continue
        setpoint = pO2_cover.get("setpoint_mbar")
        p_total = pO2_cover.get("p_total_mbar")
        achieved = pO2_cover.get("effective_pO2_achieved_mbar")
        if setpoint is not None and p_total is not None and setpoint > p_total:
            require(
                achieved is not None,
                "po2_setpoint_exceeds_total_pressure_without_effective_po2",
                pair_name,
            )
            require(
                achieved <= p_total,
                "po2_achieved_exceeds_total_pressure",
                pair_name,
            )
            require(
                pO2_cover.get("limited_by_total_pressure") is True,
                "po2_clipping_requires_total_pressure_flag",
                pair_name,
            )
            require(
                pO2_cover.get("status") == "clipped_to_total_pressure",
                "po2_clipping_status_inconsistent",
                pair_name,
            )

    for row in preset.get("sticking_provenance", {}).get("species_surface_pairs", []):
        if "sticking_coefficient" in row:
            require(
                bool(row.get("source_class")),
                "sticking_coefficient_missing_source_class",
                f"{row.get('species')}:{row.get('surface_id')}",
            )
            require(
                bool(row.get("citation_id") or row.get("source_ref") or row.get("extraction_note")),
                "sticking_coefficient_missing_source_detail",
                f"{row.get('species')}:{row.get('surface_id')}",
            )
            require(
                bool(row.get("digest")),
                "sticking_coefficient_missing_digest",
                f"{row.get('species')}:{row.get('surface_id')}",
            )

    for measurement_id, measurement in measurements.get("measurements", {}).items():
        material_context = measurement.get("material_context", {})
        suspect_elementals = material_context.get("feedstock_elemental_wt_pct")
        if isinstance(suspect_elementals, dict):
            require(
                suspect_elementals.get("extraction_status") == "extraction_suspect",
                "feedstock_elementals_must_be_marked_extraction_suspect",
                measurement_id,
            )
            require_source_citation(
                suspect_elementals.get("source") or {},
                "feedstock_elementals_missing_source",
                measurement_id,
            )
            require(
                bool(suspect_elementals.get("values")),
                "feedstock_elementals_missing_values",
                measurement_id,
            )
            cross_check = suspect_elementals.get("cross_check") or {}
            require(
                bool(cross_check.get("findings_ref") and cross_check.get("evidence")),
                "feedstock_elementals_missing_cross_check",
                measurement_id,
            )
        for condition_name, condition in measurement.get("conditions_reported", {}).items():
            if isinstance(condition, dict) and "reported_value" in condition:
                require_source_citation(
                    condition.get("source") or {},
                    "sidecar_value_missing_citation",
                    f"{measurement_id}:{condition_name}",
                )
        for value in measurement.get("quantitative_measurements", []):
            require_value_citation(value, measurement_id)
        fig4c_table = (measurement.get("figure_extraction") or {}).get(
            "fig4c_per_location_mass_table"
        ) or {}
        for row in fig4c_table.get("rows", []):
            detail = f"{measurement_id}:fig4c:{row.get('location_id')}"
            require(
                row.get("source_class") in FIGURE_MASS_SOURCE_CLASSES,
                "figure_mass_invalid_source_class",
                detail,
            )
            require(
                bool(row.get("extraction_note")),
                "figure_mass_missing_extraction_note",
                detail,
            )
            require(
                row.get("reported_value") is not None and row.get("reported_unit") == "g",
                "figure_mass_missing_reported_value",
                detail,
            )
            require_source_citation(
                row.get("source") or {},
                "figure_mass_missing_citation",
                detail,
            )
        for location in measurement.get("observed_locations", []):
            composition = location.get("reported_species_or_composition") or {}
            require(
                bool(composition),
                "qualitative_composition_missing",
                f"{measurement_id}:{location.get('id')}",
            )
            for species, entry in composition.items():
                require(
                    entry.get("measurement_type") == "qualitative",
                    "qualitative_composition_missing_measurement_type",
                    f"{measurement_id}:{location.get('id')}:{species}",
                )
                require_source_citation(
                    entry.get("source") or {},
                    "qualitative_composition_missing_citation",
                    f"{measurement_id}:{location.get('id')}:{species}",
                )
            for value in location.get("quantitative_measurements", []):
                require_value_citation(value, measurement_id)
        for conflict in measurement.get("conflicting_reported_values", []):
            for value in conflict.get("values", []):
                require_value_citation(value, measurement_id)


def is_not_reported(row: dict) -> bool:
    return isinstance(row, dict) and row.get("reported_status") == NOT_REPORTED


def require_gas_boundary_value(row: dict, keys: tuple[str, ...], code: str, detail: str) -> None:
    require(isinstance(row, dict), code, f"gas_boundary.{detail} required")
    if is_not_reported(row):
        require_not_reported_gas_boundary(row, detail)
        return
    for key in keys:
        require(row.get(key) is not None and row.get(key) != "", code, f"{detail}.{key} required")


def require_not_reported_gas_boundary(row: dict, detail: str) -> None:
    require(row.get("source_class") == NOT_REPORTED, "gas_boundary_not_reported_missing_source_class", detail)
    require(bool(row.get("citation_id")), "gas_boundary_not_reported_missing_citation", detail)
    require(bool(row.get("extraction_note") or row.get("reason")), "gas_boundary_not_reported_missing_reason", detail)
    require(bool(row.get("digest")), "gas_boundary_not_reported_missing_digest", detail)


def require_schedule_point_units(schedule: dict) -> None:
    require_points_have_unit(schedule.get("melt_temperature_C"), "C", "melt_temperature_C")
    if schedule.get("chamber_pressure_mbar") is None:
        profile = schedule.get("reported_exposure_profile")
        reported_pressure = (
            profile.get("pressure_mbar") if isinstance(profile, dict) else None
        )
        if isinstance(reported_pressure, dict) and {
            "reported_min_value",
            "reported_max_value",
            "reported_unit",
        } <= set(reported_pressure):
            require(
                reported_pressure["reported_unit"] == "mbar"
                and reported_pressure["reported_min_value"]
                <= reported_pressure["reported_max_value"],
                "invalid_reported_pressure_range",
                "reported_exposure_profile.pressure_mbar",
            )
        else:
            pressure_control = (schedule.get("gas_boundary") or {}).get(
                "pressure_control"
            )
            require(
                is_not_reported(pressure_control)
                and pressure_control.get("source_class") == NOT_REPORTED,
                "missing_schedule_pressure_must_be_not_reported",
                "chamber_pressure_mbar may be omitted only when pressure is not reported",
            )
    else:
        require_points_have_unit(
            schedule.get("chamber_pressure_mbar"),
            "mbar",
            "chamber_pressure_mbar",
        )
    for surface_id, points in (schedule.get("surface_temperature_C") or {}).items():
        require_points_have_unit(points, "C", f"surface_temperature_C.{surface_id}")


def require_points_have_unit(points: object, expected: str, detail: str) -> None:
    require(isinstance(points, list), "schedule_points_missing", detail)
    for index, point in enumerate(points):
        require(isinstance(point, dict), "schedule_point_not_mapping", f"{detail}[{index}]")
        if "unit" not in point:
            raise SchemaValidationError(
                "schedule_point_missing_unit",
                f"{detail}[{index}].unit required",
            )
        require(
            point.get("unit") == expected,
            "schedule_point_unit_mismatch",
            f"{detail}[{index}] expected {expected}",
        )


def require_gas_boundary_source(row: dict, detail: str) -> None:
    if is_not_reported(row):
        require_not_reported_gas_boundary(row, detail)
        return
    require(bool(row.get("source_class")), "gas_boundary_missing_source_class", detail)
    require(bool(row.get("source_ref")), "gas_boundary_missing_source_ref", detail)
    require(bool(row.get("citation_id")), "gas_boundary_missing_citation_id", detail)
    require(bool(row.get("digest")), "gas_boundary_missing_digest", detail)


def require_source_citation(source: dict, code: str, detail: str) -> None:
    require(
        bool(source.get("citation_id") and source.get("source_location") and source.get("digest")),
        code,
        detail,
    )


def require_value_citation(value: dict, measurement_id: str) -> None:
    require_source_citation(
        value.get("source") or {},
        "sidecar_value_missing_citation",
        f"{measurement_id}:{value.get('observable', 'conflicting_value')}",
    )


def test_robinot_preset_carries_required_external_anchors() -> None:
    preset = load_yaml(PRESET_PATH)
    sample = preset["lab_geometry"]["sample"]

    schedule = preset["lab_schedule"]
    assert schedule["interpolation"] == ROBINOT_INTERPOLATION
    assert schedule["interpolation_source_class"] == ROBINOT_INTERPOLATION_SOURCE_CLASS
    assert schedule["interpolation_citation_id"] == "robinot_2026"
    assert "not a paper-reported T(t) profile" in schedule["interpolation_extraction_note"]

    gas_boundary = preset["lab_schedule"]["gas_boundary"]
    assert gas_boundary["background_gas"]["species"] == ROBINOT_CARRIER_GAS
    assert gas_boundary["background_gas"]["source_class"] == "literature_sidecar"
    assert gas_boundary["imposed_flow"]["value"] == ROBINOT_FLOW_NL_MIN
    assert gas_boundary["imposed_flow"]["unit"] == "NL_min"
    assert preset["lab_schedule"]["chamber_pressure_mbar"][0]["value"] == ROBINOT_PRESSURE_MBAR
    assert preset["lab_schedule"]["chamber_pressure_mbar"][0]["unit"] == "mbar"
    assert preset["lab_schedule"]["duration_h"] == ROBINOT_DURATION_H
    assert preset["lab_schedule"]["furnace_ceiling_C"] == ROBINOT_PEAK_TEMPERATURE_C
    assert preset["lab_schedule"]["melt_temperature_C"][-1]["value"] == ROBINOT_PEAK_TEMPERATURE_C
    assert preset["lab_schedule"]["melt_temperature_C"][-1]["unit"] == "C"
    assert preset["lab_schedule"]["window_semantics"]["deposit_sample_basis"] == "after_cooldown"
    assert preset["digests"]["gas_boundary_digest"]

    roles = {row["id"]: row["role"] for row in preset["lab_geometry"]["surfaces"]}
    assert roles == {
        "holder": "sample_holder",
        "window": "transparent_wall",
        "condenser": "collector",
        "filter": "downstream_filter",
    }

    pO2_cover = preset["pair"]["remediation"]["mitigation"]["pO2_cover"]
    assert pO2_cover["p_total_mbar"] == ROBINOT_PRESSURE_MBAR
    assert pO2_cover["effective_pO2_achieved_mbar"] == pO2_cover["setpoint_mbar"]
    assert pO2_cover["status"] == "achieved_as_setpoint"

    assert sample["mass_g"] == ROBINOT_SAMPLE_MASS_G
    assert "sample_mass_g" not in sample["named_missing_fields"]
    assert sample["exposed_melt_area_m2"] == pytest.approx(1.327e-4)
    assert sample["exposed_melt_area_basis"] == (
        "assumed_from_printed_pellet_diameter"
    )
    assert sample["exposed_melt_area_source_ref"] == (
        "robinot_2026_methods:Section 3.2"
    )
    assert preset["pair"]["faithful"]["duration_h"] == ROBINOT_RUN_DURATION_H
    assert preset["pair"]["remediation"]["duration_h"] == ROBINOT_RUN_DURATION_H
    assert preset["pair"]["faithful"]["kinetics_caveat"] == "none"
    assert preset["pair"]["remediation"]["kinetics_caveat"] in KINETICS_CAVEATS
    assert preset["comparison_contract"]["model_evidence_class"] == "builtin_process_model"
    assert "cached-real" in preset["comparison_contract"]["fidelity_tier_allowed"]
    assert preset["comparison_contract"]["internal_analytical_used"] is False


def test_robinot_assumed_area_is_reported_by_preset_resolver(
    tmp_path: Path,
) -> None:
    preset = runner_module._load_vacuum_preset_run_spec(
        PRESET_PATH,
        "faithful",
    )
    area_notices = [
        notice
        for notice in preset.provenance.get("notices", [])
        if notice.get("path") == "lab_geometry.sample.exposed_melt_area_m2"
    ]

    assert len(area_notices) == 1
    notice = area_notices[0]
    assert notice["code"] == "knob_assumed"
    assert notice["knob"] == "exposed_melt_area_m2"
    assert notice["value"] == pytest.approx(1.327e-4)
    assert notice["basis"] == "assumed_from_printed_pellet_diameter"
    assert notice["derivation"] == "pi*(D/2)^2"
    assert notice["locator"] == "robinot_2026_methods:Section 3.2"
    assert notice["out_of_domain"] is True
    assert notice["out_of_domain_reason"] == (
        "molten_bead_is_not_a_flat_face"
    )

    document = load_yaml(PRESET_PATH)
    document["lab_geometry"]["surfaces"][0].pop("temperature_profile")
    mutated_path = tmp_path / "robinot_area_and_surface_default.yaml"
    mutated_path.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )
    mutated = runner_module._load_vacuum_preset_run_spec(
        mutated_path,
        "faithful",
    )
    mutated_notices = mutated.provenance["notices"]
    assert any(
        row["path"] == "lab_geometry.sample.exposed_melt_area_m2"
        for row in mutated_notices
    )
    assert any(
        row["path"] == "lab_geometry.surfaces.holder.temperature_C"
        for row in mutated_notices
    )


def test_pomeroy_preset_captures_recipe_and_leaves_pressure_unset() -> None:
    preset = load_yaml(POMEROY_PRESET_PATH)
    measurements = load_yaml(MEASUREMENTS_PATH)
    feedstocks = load_yaml(FEEDSTOCKS_PATH)

    schedule = preset["lab_schedule"]
    assert schedule["duration_h"] == POMEROY_DURATION_H
    assert schedule["experiment_windows"]["outgas"]["end_h"] == POMEROY_OUTGAS_END_H
    assert schedule["experiment_windows"]["ramp"]["end_h"] == POMEROY_RAMP_END_H
    assert schedule["experiment_windows"]["peak_hold"]["end_h"] == POMEROY_DURATION_H
    assert schedule["melt_temperature_C"][-1]["value"] == POMEROY_HOLD_TEMPERATURE_C
    assert preset["lab_geometry"]["sample"]["mass_g"] is None
    assert (
        "sample_mass_g"
        in preset["lab_geometry"]["sample"]["named_missing_fields"]
    )
    assert preset["lab_geometry"]["sample"]["exposed_melt_area_m2"] is None
    assert "chamber_pressure_mbar" not in schedule
    pressure_control = schedule["gas_boundary"]["pressure_control"]
    assert pressure_control["reported_status"] == NOT_REPORTED
    assert pressure_control["source_class"] == NOT_REPORTED
    assert "RGA species partial pressures" in pressure_control["extraction_note"]
    assert preset["lab_geometry"]["pump"]["pumping_speed_m3_s"] is None

    faithful = preset["pair"]["faithful"]
    assert faithful["feedstock_id"] == "lunar_mls_1a"
    assert faithful["duration_h"] == POMEROY_DURATION_H
    assert feedstocks["lunar_mls_1a"]["composition_wt_pct"] == MLS_1A_MAJOR_OXIDES
    assert feedstocks["lunar_mls_1a"]["sum_check"] == pytest.approx(97.94)
    assert "status" not in feedstocks["lunar_mls_1a"]

    measurement = measurements["measurements"][preset["measurement_id"]]
    comparison = measurement["comparison_points"][0]
    assert comparison["expected_value"] == POMEROY_MASS_LOSS_FRACTION
    assert comparison["observable_id"] == (
        "pomeroy_non_condensed_mass_loss_fraction"
    )
    assert {
        row["representation_status"]
        for row in measurement["qualitative_comparison_observations"]
    } == {"not-representable"}
    assert all(
        not {"expected_value", "actual_value", "residual", "score"}.intersection(
            row
        )
        for row in measurement["qualitative_comparison_observations"]
    )


def test_unreported_pomeroy_knobs_use_runner_defaults_and_report_them(
    tmp_path: Path,
) -> None:
    source_document = load_yaml(POMEROY_PRESET_PATH)
    assert "chamber_pressure_mbar" not in source_document["lab_schedule"]
    preset = runner_module._load_vacuum_preset_run_spec(
        POMEROY_PRESET_PATH,
        "faithful",
    )
    expected_default_kg = runner_module.PyrolysisRun(
        feedstock_id=preset.feedstock_id
    ).mass_kg
    expected_default_pressure_mbar = runner_module.CampaignManager(
        runner_module.load_config_bundle().setpoints
    )._pressure_config_float(
        runner_module.CampaignPhase.C0,
        "p_total_mbar",
        "p_total_mbar_default",
    )
    assert preset.mass_kg == expected_default_kg
    assert preset.provenance["sample_mass_g"] is None
    assert "chamber_pressure_mbar" not in preset.document["lab_schedule"]
    assert [
        (point["t_h"], point["value"], point["unit"])
        for point in preset.lab_schedule["chamber_pressure_mbar"]
    ] == [
        (0.0, expected_default_pressure_mbar, "mbar"),
        (preset.hours, expected_default_pressure_mbar, "mbar"),
    ]
    notices = {
        notice["path"]: notice for notice in preset.provenance["notices"]
    }
    assert notices == {
        "lab_schedule.chamber_pressure_mbar": {
            "code": "knob_defaulted",
            "path": "lab_schedule.chamber_pressure_mbar",
            "value": expected_default_pressure_mbar,
            "unit": "mbar",
            "default_source": "campaigns.C0.p_total_mbar_default",
            "out_of_domain": True,
        },
        "mass_kg": {
            "code": "knob_defaulted",
            "path": "mass_kg",
            "value": expected_default_kg,
            "unit": "kg",
            "default_source": "PyrolysisRun.mass_kg",
            "out_of_domain": True,
        },
    }

    mutated_document = load_yaml(POMEROY_PRESET_PATH)
    mutated_document["lab_geometry"]["sample"]["mass_g"] = 12.5
    explicit_pressure = [
        {"t_h": 0.0, "value": 2.0, "unit": "mbar"},
        {"t_h": 4.0, "value": 3.0, "unit": "mbar"},
    ]
    mutated_document["lab_schedule"]["chamber_pressure_mbar"] = explicit_pressure
    mutated_document["lab_geometry"]["surfaces"][0]["temperature_C"] = 37.0
    mutated_path = tmp_path / "pomeroy_mutated_mass.yaml"
    mutated_path.write_text(yaml.safe_dump(mutated_document), encoding="utf-8")
    mutated = runner_module._load_vacuum_preset_run_spec(
        mutated_path,
        "faithful",
    )
    assert mutated.mass_kg == pytest.approx(0.0125)
    assert mutated.lab_schedule["chamber_pressure_mbar"] == explicit_pressure
    mutated_notices = {
        notice["path"] for notice in mutated.provenance.get("notices", [])
    }
    assert "mass_kg" not in mutated_notices
    assert "lab_schedule.chamber_pressure_mbar" not in mutated_notices
    assert "lab_geometry.surfaces.crucible_wall.temperature_C" not in mutated_notices


def test_pomeroy_unreported_mass_digest_describes_the_paper() -> None:
    measurement = load_yaml(MEASUREMENTS_PATH)["measurements"][
        "pomeroy_cardiff_2006_measurements"
    ]
    sample_mass = measurement["conditions_reported"]["sample_mass_g"]

    assert sample_mass["reported_status"] == NOT_REPORTED
    assert "paper does not state" in sample_mass["source"]["digest"]
    assert "simulator" not in sample_mass["source"]["digest"]


def test_runtime_pressure_stays_within_each_printed_preset_range() -> None:
    ranged_presets = 0
    rejected_mutations = 0

    def resolve_runtime_pressure_samples(
        preset,
        schedule: Mapping,
    ) -> list[float]:
        setpoints = copy.deepcopy(runner_module.load_config_bundle().setpoints)
        setpoints["lab_geometry"] = copy.deepcopy(preset.lab_geometry)
        manager = runner_module.CampaignManager(setpoints)
        manager.overrides["C0"] = {"lab_schedule": copy.deepcopy(schedule)}
        duration_h = int(schedule["duration_h"])
        pressures = []
        for sample_time_h in range(duration_h + 1):
            melt = MeltState()
            manager.apply_lab_schedule_controls(
                melt,
                runner_module.CampaignPhase.C0,
                sample_time_h=float(sample_time_h),
            )
            pressures.append(melt.p_total_mbar)
        return pressures

    def pressure_is_within_reported_range(
        pressure: float,
        reported_min: float,
        reported_max: float,
    ) -> bool:
        return (
            pressure >= reported_min
            or isclose(pressure, reported_min, rel_tol=1e-12)
        ) and (
            pressure <= reported_max
            or isclose(pressure, reported_max, rel_tol=1e-12)
        )

    for preset_path in sorted(PRESET_DIR.glob("*.yaml")):
        source_schedule = load_yaml(preset_path)["lab_schedule"]
        reported_profile = source_schedule.get("reported_exposure_profile", {})
        reported_pressure = reported_profile.get("pressure_mbar", {})
        if not {
            "reported_min_value",
            "reported_max_value",
        } <= set(reported_pressure):
            continue

        ranged_presets += 1
        reported_min = float(reported_pressure["reported_min_value"])
        reported_max = float(reported_pressure["reported_max_value"])
        preset = runner_module._load_vacuum_preset_run_spec(
            preset_path, "faithful"
        )
        runtime_pressures = resolve_runtime_pressure_samples(
            preset,
            preset.lab_schedule,
        )
        assert all(
            pressure_is_within_reported_range(
                pressure, reported_min, reported_max
            )
            for pressure in runtime_pressures
        ), (
            f"{preset_path.name} runtime pressure {runtime_pressures!r} is "
            f"outside its printed range [{reported_min:g}, {reported_max:g}]"
        )

        if not reported_min <= 1.0 <= reported_max:
            mutated_schedule = copy.deepcopy(preset.lab_schedule)
            for point in mutated_schedule[
                "chamber_pressure_mbar"
            ]:
                point["value"] = 1.0
            mutated_pressures = resolve_runtime_pressure_samples(
                preset,
                mutated_schedule,
            )
            with pytest.raises(
                AssertionError, match="outside printed range"
            ):
                assert all(
                    pressure_is_within_reported_range(
                        pressure, reported_min, reported_max
                    )
                    for pressure in mutated_pressures
                ), "mutated runtime pressure is outside printed range"
            rejected_mutations += 1

    assert ranged_presets > 0
    assert rejected_mutations > 0


@pytest.mark.parametrize(
    ("preset_path", "backend_name"),
    [
        (POMEROY_PRESET_PATH, "thermoengine"),
        (SESKO_PRESET_PATH, "thermoengine"),
    ],
    ids=["pomeroy-cardiff-2006", "sesko-2024"],
)
def test_benchtop_presets_report_stage0_refusal_and_defaults(
    preset_path: Path,
    backend_name: str,
) -> None:
    preset = runner_module._load_vacuum_preset_run_spec(preset_path, "faithful")
    apparatus = parse_teststand(preset.lab_schedule, preset.lab_geometry)
    assert apparatus.pressure_driver == "imposed"
    assert any(
        "apparatus is not fully specified" in notice
        for notice in apparatus.notices
    )

    run = runner_module.PyrolysisRun(
        feedstock_id=preset.feedstock_id,
        campaign="C0",
        hours=preset.hours,
        mass_kg=preset.mass_kg,
        backend_name=backend_name,
        setpoints_patch={"lab_geometry": copy.deepcopy(preset.lab_geometry)},
        lab_schedule=copy.deepcopy(preset.lab_schedule),
        run_metadata_overrides={
            runner_module.PRESET_PROVENANCE_METADATA_KEY: dict(preset.provenance),
        },
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )

    bundle = runner_module.load_config_bundle()
    design = EquipmentDesigner(bundle.setpoints).design_for_batch(
        mass_kg=preset.mass_kg,
        feedstock=bundle.feedstocks[preset.feedstock_id],
        lab_geometry=preset.lab_geometry,
    )
    area_bridge = runner_module.gram_lab_exposed_melt_area_bridge(
        preset.lab_geometry,
        crucible_face_area_m2=design.crucible.melt_surface_area_m2,
    )
    area_notice = area_bridge["notices"][0]
    assert area_notice["code"] == "knob_defaulted"
    assert area_notice["path"] == (
        "lab_geometry.sample.exposed_melt_area_m2"
    )
    assert area_notice["value"] == pytest.approx(
        design.crucible.melt_surface_area_m2
    )
    assert area_notice["source_class"] == "assumption_with_sensitivity_marker"
    assert area_notice["out_of_domain"] is True

    if preset_path == SESKO_PRESET_PATH:
        assert preset.provenance["sample_mass_g"] == SESKO_SAMPLE_MASS_G
        assert preset.mass_kg == pytest.approx(0.01)
        assert not any(
            notice["path"] == "mass_kg"
            for notice in preset.provenance.get("notices", [])
        )
        reported_pressure = preset.lab_schedule["reported_exposure_profile"][
            "pressure_mbar"
        ]
        assert reported_pressure["reported_min_value"] == (
            SESKO_PRESSURE_REPORTED_MIN_MBAR
        )
        assert reported_pressure["reported_max_value"] == (
            SESKO_PRESSURE_REPORTED_MAX_MBAR
        )
        assert [
            point["value"]
            for point in preset.lab_schedule["chamber_pressure_mbar"]
        ] == [SESKO_PRESSURE_RUNTIME_FLOOR_MBAR, SESKO_PRESSURE_REPORTED_MAX_MBAR]
        assert not any(
            notice["path"] == "lab_schedule.chamber_pressure_mbar"
            for notice in preset.provenance.get("notices", [])
        )
        surface_notices = [
            notice
            for notice in preset.provenance.get("notices", [])
            if notice["path"].startswith("lab_geometry.surfaces.")
        ]
        assert surface_notices == []
        surface_schedule = preset.lab_schedule["surface_temperature_C"]
        assert surface_schedule["crucible_wall"][0]["value"] == pytest.approx(
            180.0
        )
        assert surface_schedule["quartz_window"][0]["value"] == pytest.approx(
            25.0
        )
    else:
        expected_mass_kg = runner_module.PyrolysisRun(
            feedstock_id=preset.feedstock_id
        ).mass_kg
        expected_pressure_mbar = runner_module.CampaignManager(
            bundle.setpoints
        )._pressure_config_float(
            runner_module.CampaignPhase.C0,
            "p_total_mbar",
            "p_total_mbar_default",
        )
        assert preset.provenance["sample_mass_g"] is None
        assert preset.mass_kg == pytest.approx(expected_mass_kg)
        assert expected_pressure_mbar == pytest.approx(1.0)
        assert all(
            point["value"] == pytest.approx(expected_pressure_mbar)
            for point in preset.lab_schedule["chamber_pressure_mbar"]
        )
        notices = {
            notice["path"]: notice
            for notice in preset.provenance.get("notices", [])
        }
        assert set(notices) == {
            "mass_kg",
            "lab_schedule.chamber_pressure_mbar",
        }
        mass_notice = notices["mass_kg"]
        pressure_notice = notices["lab_schedule.chamber_pressure_mbar"]
        pomeroy_surface_profiles = preset.lab_schedule[
            "surface_temperature_C"
        ]
        assert {
            surface_id: [point["value"] for point in points]
            for surface_id, points in pomeroy_surface_profiles.items()
        } == {
            "crucible_wall": [200, 1200],
            "cold_trap": [25, 25],
            "rga_line": [25, 25],
        }
        assert mass_notice["code"] == "knob_defaulted"
        assert mass_notice["value"] == pytest.approx(expected_mass_kg)
        assert mass_notice["out_of_domain"] is True
        assert pressure_notice["code"] == "knob_defaulted"
        assert pressure_notice["value"] == pytest.approx(
            expected_pressure_mbar
        )
        assert pressure_notice["out_of_domain"] is True
        assert area_notice["out_of_domain"] is True

    class ThermoEngineBackend:
        name = "thermoengine"
        _mode = "thermoengine"

    config = run._session_config()
    assert config.unavailable_error_cls is runner_module.RunnerError
    with pytest.raises(
        runner_module.RunnerError,
        match="Stage-0 route.*requires subprocess.*ThermoEngineBackend",
    ):
        runner_module.SimSession().start(
            config,
            backend=ThermoEngineBackend(),
        )


def test_robinot_preset_references_registered_loadable_eac1_feedstock() -> None:
    preset = load_yaml(PRESET_PATH)
    feedstocks = load_yaml(FEEDSTOCKS_PATH)

    feedstock_ids = {
        preset["pair"]["faithful"]["feedstock_id"],
        preset["pair"]["remediation"]["feedstock_id"],
    }
    assert feedstock_ids == {"lunar_eac_1a"}
    assert feedstock_ids <= set(feedstocks)

    entry = feedstocks["lunar_eac_1a"]
    assert "status" not in entry
    assert entry["composition_status"] == "primary_xrf_loadable"
    assert entry["sum_check"] == pytest.approx(98.4)
    assert entry["paper_aliases"] == ["EAC-1"]
    assert "primary EAC-1A XRF oxides" in preset["source_notes"][1]["digest"]
    assert (
        preset["digests"]["feedstock_digest"]
        == "lunar_eac_1a_engelschion2020_xrf_sha256_1b7cce46216cb29f_robinot_elementals_suspect"
    )


def test_measurements_sidecar_preserves_per_location_conflicts_and_citations() -> None:
    measurements = load_yaml(MEASUREMENTS_PATH)
    measurement = measurements["measurements"]["robinot_2026_deposit_measurements"]

    assert measurement["evidence_class"] == "experiment-grade"
    assert measurement["conditions_reported"]["carrier_gas"]["species"] == ROBINOT_CARRIER_GAS
    assert measurement["conditions_reported"]["pressure_mbar"]["reported_value"] == ROBINOT_PRESSURE_MBAR
    assert measurement["conditions_reported"]["flow_boundary"]["reported_value"] == ROBINOT_FLOW_NL_MIN
    assert measurement["conditions_reported"]["temperature_C"]["reported_value"] == ROBINOT_PEAK_TEMPERATURE_C
    assert measurement["conditions_reported"]["duration_h"]["reported_value"] == ROBINOT_DURATION_H
    assert measurement["conditions_reported"]["sample_mass_g"]["reported_value"] == ROBINOT_SAMPLE_MASS_G
    assert measurement["material_context"]["feedstock_id"] == "lunar_eac_1a"
    elementals = measurement["material_context"]["feedstock_elemental_wt_pct"]
    assert elementals["extraction_status"] == "extraction_suspect"
    assert elementals["values"] == ROBINOT_EAC1_ELEMENTAL_WT_PCT
    assert elementals["sum_check"] == pytest.approx(96.0)
    assert elementals["cross_check"]["findings_ref"].endswith("eac1a-composition-findings.md")
    assert "Al: oxide-implied 6.7 wt% vs extracted 1.0 wt%" in elementals["cross_check"]["evidence"]

    products = {row["observable"]: row for row in measurement["quantitative_measurements"]}
    assert products["oxygen_released"]["reported_value"] == ROBINOT_O2_RELEASED_MG
    assert products["oxygen_released"]["reported_unit"] == "mg"
    assert products["glassy_lump_mass"]["reported_value"] == ROBINOT_GLASSY_LUMP_G
    assert products["glassy_lump_mass"]["reported_unit"] == "g"
    assert products["total_vaporized_or_captured_mass"]["reported_value"] == ROBINOT_TOTAL_CAPTURED_G
    assert products["total_vaporized_or_captured_mass"]["reported_unit"] == "g"
    assert products["unaccounted_mass"]["reported_value"] == ROBINOT_UNACCOUNTED_G
    assert products["unaccounted_mass"]["reported_unit"] == "g"
    assert products["final_mass_recovery"]["reported_value"] == ROBINOT_MASS_RECOVERY_PERCENT
    assert products["final_mass_recovery"]["reported_unit"] == "percent"

    figure = measurement["figure_extraction"]["fig4c_per_location_mass_table"]
    assert figure["extraction_status"] == ROBINOT_FIG4C_SOURCE_CLASS
    assert ROBINOT_FIG4C_IMAGE_PATH in figure["checked_sources"]
    assert figure["sample_mass_sum_check_g"] == ROBINOT_SAMPLE_MASS_G
    assert figure["captured_on_apparatus_sum_g"] == ROBINOT_FIG4C_CAPTURED_G
    assert "Body text reports 1.1 g" in figure["text_vs_figure_note"]
    assert "figure rows close exactly" in figure["text_vs_figure_note"]

    fig4c_rows = {row["location_id"]: row for row in figure["rows"]}
    assert set(fig4c_rows) == set(ROBINOT_FIG4C_LOCATION_MASSES_G)
    assert sum(row["reported_value"] for row in fig4c_rows.values()) == ROBINOT_SAMPLE_MASS_G
    assert (
        sum(
            fig4c_rows[location_id]["reported_value"]
            for location_id in ROBINOT_FIG4C_CAPTURED_LOCATION_IDS
        )
        == ROBINOT_FIG4C_CAPTURED_G
    )
    for location_id, expected_mass_g in ROBINOT_FIG4C_LOCATION_MASSES_G.items():
        row = fig4c_rows[location_id]
        assert row["reported_value"] == pytest.approx(expected_mass_g)
        assert row["reported_unit"] == "g"
        assert row["normalized_value_kg"] == pytest.approx(expected_mass_g / 1000.0)
        assert row["source_class"] == ROBINOT_FIG4C_SOURCE_CLASS
        assert ROBINOT_FIG4C_IMAGE_PATH in row["extraction_note"]
        assert "1.1 g vaporized and captured" in row["extraction_note"]
        assert "1.26 g captured" in row["extraction_note"]
        assert "0.265 g discrepancy" in row["extraction_note"]
        assert row["source"]["citation_id"] == "robinot_2026"
        assert row["source"]["source_location"] == "fig4c_bar_labels"

    assert "per_location_mass_split" not in measurement["named_missing_fields"]

    locations = {row["id"]: row for row in measurement["observed_locations"]}
    assert set(locations) == {"holder", "window", "condenser", "filter"}
    for row in locations.values():
        assert row["measured_hot_vs_cooldown"] == "post_run_cooldown"
        assert row["morphology_notes"]["source"]["citation_id"] == "robinot_2026"

    expected_species = {
        "holder": {"Na": "present", "Fe": "present"},
        "window": {"Si": "present", "Fe": "present"},
        "condenser": {"Si": "about_one_third", "Fe": "present", "Mg": "present"},
        "filter": {
            "Si": "mostly",
            "Na": "moderate",
            "Fe": "moderate",
            "Mg": "trace",
            "P": "trace",
            "K": "trace",
        },
    }
    for location_id, expected in expected_species.items():
        composition = locations[location_id]["reported_species_or_composition"]
        assert {species: row["qualifier"] for species, row in composition.items()} == expected
        assert {row["measurement_type"] for row in composition.values()} == {"qualitative"}
        assert all(row["source"]["citation_id"] == "robinot_2026" for row in composition.values())

    for location_id in locations:
        assert locations[location_id]["quantitative_measurements"] == []

    conflict = measurement["conflicting_reported_values"][0]
    assert conflict["resolution"] == "unresolved_report_both"
    assert {row["normalized_value_kg"] for row in conflict["values"]} == ROBINOT_CONFLICT_KG
    assert {row["reported_unit"] for row in conflict["values"]} == {"g"}
    assert {row["source"]["source_location"] for row in conflict["values"]} == {
        "results_section",
        "conclusion",
    }


def test_sesko_preset_captures_uhv_conditions_and_geometry_sources() -> None:
    preset = load_yaml(SESKO_PRESET_PATH)
    measurements = load_yaml(MEASUREMENTS_PATH)
    measurement = measurements["measurements"]["sesko_2024_vapor_phase_pyrolysis_measurements"]

    feedstock_ids = {
        preset["pair"]["faithful"]["feedstock_id"],
        preset["pair"]["remediation"]["feedstock_id"],
    }
    assert feedstock_ids == {"lunar_eac_1a"}
    assert measurement["material_context"]["feedstock_id"] == "lunar_eac_1a"
    assert measurement["conditions_reported"]["feedstock"]["species"] == "EAC-1A"
    sample = preset["lab_geometry"]["sample"]
    assert sample["mass_g"] == SESKO_SAMPLE_MASS_G
    assert sample["crucible_material"] == "alumina"
    assert sample["exposed_melt_area_m2"] is None
    assert sample["crucible_opening_area_m2"] is None
    pressure_digest = next(
        row["digest"]
        for row in preset["source_notes"]
        if row["digest"].startswith("The reported exposure pressure range")
    )
    assert "parser-floor point" in pressure_digest
    assert "runtime floor" in pressure_digest

    schedule = preset["lab_schedule"]
    assert schedule["melt_temperature_C"][0]["value"] == SESKO_TEMP_MIN_C
    assert schedule["melt_temperature_C"][-1]["value"] == SESKO_TEMP_MAX_C
    assert schedule["melt_temperature_C"][0]["unit"] == "C"
    assert (
        schedule["chamber_pressure_mbar"][0]["value"]
        == SESKO_PRESSURE_RUNTIME_FLOOR_MBAR
    )
    assert (
        schedule["chamber_pressure_mbar"][0]["runtime_delta_annotation_id"]
        == SESKO_PRESSURE_RUNTIME_DELTA_ID
    )
    assert (
        schedule["chamber_pressure_mbar"][-1]["value"]
        == SESKO_PRESSURE_REPORTED_MAX_MBAR
    )
    reported_profile = schedule["reported_exposure_profile"]["pressure_mbar"]
    assert reported_profile["reported_min_value"] == SESKO_PRESSURE_REPORTED_MIN_MBAR
    assert reported_profile["reported_max_value"] == SESKO_PRESSURE_REPORTED_MAX_MBAR
    assert reported_profile["qualifier"] == "oscillating_range"
    runtime_delta = {
        row["id"]: row for row in schedule["runtime_schedule_annotations"]
    }[SESKO_PRESSURE_RUNTIME_DELTA_ID]
    assert runtime_delta["reported_value"] == SESKO_PRESSURE_REPORTED_MIN_MBAR
    assert runtime_delta["runtime_value"] == SESKO_PRESSURE_RUNTIME_FLOOR_MBAR
    assert runtime_delta["reason"] == "normalize_lab_schedule_floor"
    assert schedule["gas_boundary"]["background_gas"]["reported_status"] == NOT_REPORTED
    assert schedule["gas_boundary"]["imposed_flow"]["reported_status"] == NOT_REPORTED
    assert schedule["gas_boundary"]["pressure_control"]["mode"] == "pumped_dynamic_uhv_exposure"
    assert (
        schedule["temperature_measurement_basis"]["surface_temperature_reference"]["instrument"]
        == "Optris PI 1M infrared camera"
    )
    assert (
        schedule["temperature_measurement_basis"]["surface_temperature_reference"]["emissivity"]
        == SESKO_TEMP_EMISSIVITY
    )
    assert (
        schedule["experiment_windows"]["solar_exposure"]["manual_tracking_adjustment_interval_min"]["value"]
        == SESKO_TRACKING_INTERVAL_MIN
    )
    assert schedule["optical_train"]["fixed_mirror_angle_deg"]["value"] == SESKO_FIXED_MIRROR_ANGLE_DEG
    assert preset["pair"]["faithful"]["transport_model"] == "molecular_transitional_regime_p0b_blocked"
    certification_block = preset["comparison_contract"][
        "pressure_sensitive_certification_block"
    ]
    assert certification_block["status"] == "blocked"
    assert certification_block["code"] == (
        "pressure_trace_and_pump_throughput_unreported"
    )
    assert "reported_pressure_range_does_not_establish_time_series" in certification_block[
        "reasons"
    ]
    assert "runtime_schedule_clamps_values_below_1e-3_mbar" in certification_block[
        "reasons"
    ]
    assert "unclamped_time_resolved_pressure_trace" in certification_block[
        "required_evidence"
    ]
    assert "pump_throughput_and_pressure_trace_are_not_reported" in certification_block[
        "reasons"
    ]
    assert "molecular_flow_transport_reproduction" in certification_block[
        "blocked_observables"
    ]

    geometry = preset["lab_geometry"]
    assert geometry["sample"]["mass_g"] == SESKO_SAMPLE_MASS_G
    surfaces = {row["id"]: row for row in geometry["surfaces"]}
    assert surfaces["quartz_window"]["source_class"] == "literature_sidecar"
    assert surfaces["quartz_window"]["equivalent_diameter_m"] == pytest.approx(
        SESKO_WINDOW_APERTURE_MM.expected / 1000.0
    )
    assert surfaces["quartz_window"]["area_m2"] == SESKO_WINDOW_AREA_M2
    assert (
        sum(row["source_class"] == "literature_sidecar" for row in geometry["surfaces"])
        == 1
    )
    assert (
        sum(
            row["source_class"] == "assumption_with_sensitivity_marker"
            for row in geometry["surfaces"]
        )
        == 2
    )

    conditions = measurement["conditions_reported"]
    assert conditions["exposure_pressure_mbar"]["reported_min_value"] == SESKO_PRESSURE_REPORTED_MIN_MBAR
    assert conditions["exposure_pressure_mbar"]["reported_max_value"] == SESKO_PRESSURE_REPORTED_MAX_MBAR
    assert conditions["exposure_surface_temperature_K"]["reported_min_value"] == 1750
    assert conditions["exposure_surface_temperature_K"]["reported_max_value"] == 1900
    assert (
        conditions["surface_temperature_reference_camera"]["reported_value"]
        == "Optris PI 1M infrared camera"
    )
    assert (
        conditions["surface_temperature_reference_emissivity"]["reported_value"]
        == SESKO_TEMP_EMISSIVITY
    )
    assert (
        conditions["manual_solar_tracking_adjustment_interval_min"]["reported_value"]
        == SESKO_TRACKING_INTERVAL_MIN
    )
    assert conditions["fixed_mirror_angle_deg"]["reported_value"] == SESKO_FIXED_MIRROR_ANGLE_DEG
    assert conditions["window_aperture_diameter_mm"]["reported_value"] == SESKO_WINDOW_APERTURE_MM

    products = {row["observable"]: row for row in measurement["quantitative_measurements"]}
    assert products["crucible_wall_deposit_thickness_um"]["reported_value"] == SESKO_DEPOSIT_THICKNESS_UM
    assert products["modeled_eac1a_o2_plus_o_yield_at_1e-5_mbar_T_vapor"][
        "reported_value"
    ] == pytest.approx(0.30)
    assert products["modeled_eac1a_o2_plus_o_yield_at_1e-5_mbar_T_O2_max"][
        "reported_value"
    ] == pytest.approx(0.11)

    locations = {row["id"]: row for row in measurement["observed_locations"]}
    assert "crucible_wall" in locations
    assert locations["crucible_wall"]["reported_species_or_composition"]["Na"][
        "qualifier"
    ] == "high_in_needle_like_crystals"
    assert locations["crucible_wall"]["reported_species_or_composition"]["Fe"][
        "qualifier"
    ] == "brown_lower_layer_present"


def test_valid_vpr_skeleton_passes_test_local_schema_validator() -> None:
    validate_vpr_schema(load_yaml(PRESET_PATH), load_yaml(MEASUREMENTS_PATH))


def test_all_vpr_presets_validate_and_round_trip_runtime_parsers() -> None:
    measurements = load_yaml(MEASUREMENTS_PATH)
    for preset_path in sorted(PRESET_DIR.glob("*.yaml")):
        preset = load_yaml(preset_path)
        validate_vpr_schema(preset, measurements)
        measurement_id = preset["measurement_id"]
        assert measurement_id in measurements["measurements"]

        geometry = parse_lab_geometry(
            preset["lab_geometry"],
            allow_temperature_profiles=True,
        )
        runtime_schedule = preset["lab_schedule"]
        if "chamber_pressure_mbar" not in runtime_schedule:
            runtime_schedule = runner_module._load_vacuum_preset_run_spec(
                preset_path,
                "faithful",
            ).lab_schedule
        normalized_schedule = normalize_lab_schedule(
            runtime_schedule,
            required_surface_profiles=[
                surface.surface_id
                for surface in geometry.surfaces
                if surface.temperature_profile
            ],
        )

        assert normalized_schedule["id"] == preset["lab_schedule"]["id"]
        assert geometry.geometry_id == preset["lab_geometry"]["id"]


def test_sparse_paper_gas_boundary_accepts_explicit_not_reported_disposition() -> None:
    preset = copy.deepcopy(load_yaml(PRESET_PATH))
    gas_boundary = preset["lab_schedule"]["gas_boundary"]

    def not_reported(field: str) -> dict:
        return {
            "reported_status": NOT_REPORTED,
            "source_class": NOT_REPORTED,
            "citation_id": "sesko_sparse_uhv",
            "extraction_note": f"Paper reports UHV pressure anchors but does not report {field}.",
            "digest": f"Sparse-paper gas-boundary field not reported: {field}.",
        }

    gas_boundary["background_gas"] = not_reported("carrier gas species")
    gas_boundary["imposed_flow"] = not_reported("imposed flow")
    gas_boundary["pressure_control"] = not_reported("pressure-control mode")

    validate_vpr_schema(preset, load_yaml(MEASUREMENTS_PATH))


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("drop_schedule_interpolation", "missing_schedule_interpolation"),
        (
            "drop_schedule_interpolation_source_class",
            "schedule_interpolation_missing_source_class",
        ),
        (
            "drop_schedule_interpolation_note",
            "schedule_interpolation_missing_assumption_note",
        ),
        ("drop_schedule_point_unit", "schedule_point_missing_unit"),
        ("drop_carrier_gas", "missing_carrier_gas"),
        ("drop_imposed_flow", "missing_gas_boundary_imposed_flow"),
        ("drop_pressure_control", "missing_gas_boundary_pressure_control"),
        ("drop_gas_boundary_source_ref", "gas_boundary_missing_source_ref"),
        ("drop_gas_boundary_citation_id", "gas_boundary_missing_citation_id"),
        ("drop_gas_boundary_digest", "gas_boundary_missing_digest"),
        (
            "drop_effective_po2",
            "po2_setpoint_exceeds_total_pressure_without_effective_po2",
        ),
        ("impossible_achieved_po2", "po2_achieved_exceeds_total_pressure"),
        ("clipping_flag_false", "po2_clipping_requires_total_pressure_flag"),
        ("clipping_status_claims_achieved", "po2_clipping_status_inconsistent"),
        ("drop_condition_value_citation", "sidecar_value_missing_citation"),
        ("drop_sidecar_value_citation", "sidecar_value_missing_citation"),
        (
            "drop_sticking_source_class",
            "sticking_coefficient_missing_source_class",
        ),
        (
            "label_only_sticking_source",
            "sticking_coefficient_missing_source_detail",
        ),
        (
            "drop_qualitative_composition",
            "qualitative_composition_missing",
        ),
        (
            "drop_qualitative_measurement_type",
            "qualitative_composition_missing_measurement_type",
        ),
        (
            "drop_qualitative_citation",
            "qualitative_composition_missing_citation",
        ),
    ],
)
def test_vpr_schema_fail_loud_rules_are_named(
    mutation: str, expected_code: str
) -> None:
    preset = copy.deepcopy(load_yaml(PRESET_PATH))
    measurements = copy.deepcopy(load_yaml(MEASUREMENTS_PATH))

    if mutation == "drop_schedule_interpolation":
        del preset["lab_schedule"]["interpolation"]
    elif mutation == "drop_schedule_interpolation_source_class":
        del preset["lab_schedule"]["interpolation_source_class"]
    elif mutation == "drop_schedule_interpolation_note":
        del preset["lab_schedule"]["interpolation_extraction_note"]
    elif mutation == "drop_schedule_point_unit":
        del preset["lab_schedule"]["melt_temperature_C"][0]["unit"]
    elif mutation == "drop_carrier_gas":
        del preset["lab_schedule"]["gas_boundary"]["background_gas"]["species"]
    elif mutation == "drop_imposed_flow":
        del preset["lab_schedule"]["gas_boundary"]["imposed_flow"]
    elif mutation == "drop_pressure_control":
        del preset["lab_schedule"]["gas_boundary"]["pressure_control"]
    elif mutation == "drop_gas_boundary_source_ref":
        del preset["lab_schedule"]["gas_boundary"]["background_gas"]["source_ref"]
    elif mutation == "drop_gas_boundary_citation_id":
        del preset["lab_schedule"]["gas_boundary"]["background_gas"]["citation_id"]
    elif mutation == "drop_gas_boundary_digest":
        del preset["lab_schedule"]["gas_boundary"]["background_gas"]["digest"]
    elif mutation == "drop_effective_po2":
        preset["pair"]["remediation"]["mitigation"]["pO2_cover"]["p_total_mbar"] = 1.0e-6
        del preset["pair"]["remediation"]["mitigation"]["pO2_cover"][
            "effective_pO2_achieved_mbar"
        ]
    elif mutation == "impossible_achieved_po2":
        cover = preset["pair"]["remediation"]["mitigation"]["pO2_cover"]
        cover["p_total_mbar"] = 1.0e-6
        cover["effective_pO2_achieved_mbar"] = cover["setpoint_mbar"]
        cover["limited_by_total_pressure"] = True
        cover["status"] = "clipped_to_total_pressure"
    elif mutation == "clipping_flag_false":
        cover = preset["pair"]["remediation"]["mitigation"]["pO2_cover"]
        cover["p_total_mbar"] = 1.0e-6
        cover["effective_pO2_achieved_mbar"] = 1.0e-6
        cover["limited_by_total_pressure"] = False
        cover["status"] = "clipped_to_total_pressure"
    elif mutation == "clipping_status_claims_achieved":
        cover = preset["pair"]["remediation"]["mitigation"]["pO2_cover"]
        cover["p_total_mbar"] = 1.0e-6
        cover["effective_pO2_achieved_mbar"] = 1.0e-6
        cover["limited_by_total_pressure"] = True
        cover["status"] = "achieved_as_setpoint"
    elif mutation == "drop_condition_value_citation":
        del measurements["measurements"]["robinot_2026_deposit_measurements"][
            "conditions_reported"
        ]["sample_mass_g"]["source"]["citation_id"]
    elif mutation == "drop_sidecar_value_citation":
        del measurements["measurements"]["robinot_2026_deposit_measurements"][
            "conflicting_reported_values"
        ][0]["values"][0]["source"]["citation_id"]
    elif mutation == "drop_sticking_source_class":
        del preset["sticking_provenance"]["species_surface_pairs"][0]["source_class"]
    elif mutation == "label_only_sticking_source":
        row = preset["sticking_provenance"]["species_surface_pairs"][0]
        for key in ("citation_id", "source_ref", "extraction_note", "digest"):
            row.pop(key, None)
    elif mutation == "drop_qualitative_composition":
        measurements["measurements"]["robinot_2026_deposit_measurements"][
            "observed_locations"
        ][0].pop("reported_species_or_composition", None)
    elif mutation == "drop_qualitative_measurement_type":
        measurements["measurements"]["robinot_2026_deposit_measurements"][
            "observed_locations"
        ][0]["reported_species_or_composition"]["Na"].pop("measurement_type", None)
    elif mutation == "drop_qualitative_citation":
        measurements["measurements"]["robinot_2026_deposit_measurements"][
            "observed_locations"
        ][0]["reported_species_or_composition"]["Na"]["source"].pop("citation_id", None)
    else:
        raise AssertionError(mutation)

    with pytest.raises(SchemaValidationError) as excinfo:
        validate_vpr_schema(preset, measurements)
    assert excinfo.value.code == expected_code
