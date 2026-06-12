from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
PRESET_PATH = REPO_ROOT / "data" / "presets" / "vacuum_pyrolysis" / "robinot_2026.yaml"
MEASUREMENTS_PATH = REPO_ROOT / "data" / "literature" / "vacuum_pyrolysis_measurements.yaml"

ROBINOT_CARRIER_GAS = "Ar"
ROBINOT_PRESSURE_MBAR = pytest.approx(13.0)
ROBINOT_FLOW_NL_MIN = pytest.approx(0.3)
ROBINOT_SAMPLE_MASS_G = pytest.approx(3.38)
ROBINOT_DURATION_H = pytest.approx(1.0)
ROBINOT_PEAK_TEMPERATURE_C = pytest.approx(1800.0)
ROBINOT_O2_RELEASED_MG = pytest.approx(35.0)
ROBINOT_GLASSY_LUMP_G = pytest.approx(1.82)
ROBINOT_CONFLICT_KG = {0.0011, 0.0013}
NOT_REPORTED = "not_reported"
ROBINOT_INTERPOLATION = "piecewise_linear"
ROBINOT_INTERPOLATION_SOURCE_CLASS = "assumption_with_sensitivity_marker"
KINETICS_CAVEATS = {
    "none",
    "furnace_scale_bulk_mixing_assumption",
    "blocked_missing_gram_scale_kinetics_model",
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
        for condition_name, condition in measurement.get("conditions_reported", {}).items():
            if isinstance(condition, dict) and "reported_value" in condition:
                require_source_citation(
                    condition.get("source") or {},
                    "sidecar_value_missing_citation",
                    f"{measurement_id}:{condition_name}",
                )
        for value in measurement.get("quantitative_measurements", []):
            require_value_citation(value, measurement_id)
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


def test_robinot_preset_skeleton_carries_required_external_anchors() -> None:
    preset = load_yaml(PRESET_PATH)

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

    assert preset["lab_geometry"]["sample"]["mass_g"] == ROBINOT_SAMPLE_MASS_G
    assert "sample_mass_g" not in preset["lab_geometry"]["sample"]["named_missing_fields"]
    assert preset["pair"]["faithful"]["duration_h"] == ROBINOT_DURATION_H
    assert preset["pair"]["remediation"]["duration_h"] == ROBINOT_DURATION_H
    assert preset["pair"]["faithful"]["kinetics_caveat"] == "none"
    assert preset["pair"]["remediation"]["kinetics_caveat"] in KINETICS_CAVEATS
    assert preset["comparison_contract"]["model_evidence_class"] == "builtin_process_model"
    assert "cached-real" in preset["comparison_contract"]["fidelity_tier_allowed"]
    assert preset["comparison_contract"]["internal_analytical_used"] is False


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

    products = {row["observable"]: row for row in measurement["quantitative_measurements"]}
    assert products["oxygen_released"]["reported_value"] == ROBINOT_O2_RELEASED_MG
    assert products["oxygen_released"]["reported_unit"] == "mg"
    assert products["glassy_lump_mass"]["reported_value"] == ROBINOT_GLASSY_LUMP_G
    assert products["glassy_lump_mass"]["reported_unit"] == "g"

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

    condenser_values = locations["condenser"]["quantitative_measurements"]
    assert condenser_values[0]["reported_value"] == pytest.approx(1.1)
    assert condenser_values[0]["reported_unit"] == "g"
    assert condenser_values[0]["normalized_value_kg"] == pytest.approx(0.0011)
    assert condenser_values[0]["source"]["digest"]

    conflict = measurement["conflicting_reported_values"][0]
    assert conflict["resolution"] == "unresolved_report_both"
    assert {row["normalized_value_kg"] for row in conflict["values"]} == ROBINOT_CONFLICT_KG
    assert {row["reported_unit"] for row in conflict["values"]} == {"g"}
    assert {row["source"]["source_location"] for row in conflict["values"]} == {
        "results_section",
        "conclusion",
    }


def test_valid_vpr_skeleton_passes_test_local_schema_validator() -> None:
    validate_vpr_schema(load_yaml(PRESET_PATH), load_yaml(MEASUREMENTS_PATH))


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
