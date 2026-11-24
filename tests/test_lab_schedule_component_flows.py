from __future__ import annotations

from copy import deepcopy

import pytest

from simulator.lab_schedule import (
    LabScheduleValidationError,
    lab_schedule_digests,
    normalize_lab_schedule,
)


def _schedule(*, component_flows: bool) -> dict:
    gas_boundary = {
        "background_gas": {
            "reported_status": "not_reported",
            "source_class": "literature_sidecar",
            "citation_id": "sossi-2019",
            "digest": "table-2",
            "reason": "The CO and CO2 components are listed separately.",
        },
        "imposed_flow": {
            "reported_status": "not_reported",
            "source_class": "literature_sidecar",
            "citation_id": "sossi-2019",
            "digest": "table-2",
            "reason": "Separate component flows are retained below.",
        },
        "pressure_control": {
            "mode": "controlled",
            "pressure_driver": "imposed",
            "source_class": "literature_sidecar",
            "citation_id": "sossi-2019",
        },
    }
    if component_flows:
        gas_boundary.update(
            {
                "component_flows": {
                    "CO": {
                        "value": 130,
                        "unit": "sccm",
                        "source_class": "literature_sidecar",
                        "citation_id": "sossi-2019",
                        "digest": "table-2",
                    },
                    "CO2": {
                        "value": 65,
                        "unit": "sccm",
                        "source_class": "literature_sidecar",
                        "citation_id": "sossi-2019",
                        "digest": "table-2",
                    },
                },
                "reported_total_flow": {
                    "value": 200,
                    "unit": "sccm",
                    "qualifier": "approximate",
                    "source_class": "literature_sidecar",
                    "citation_id": "sossi-2019",
                    "digest": "methods",
                },
                "flow_reference_state": {
                    "reported_status": "not_reported",
                    "source_class": "literature_sidecar",
                    "citation_id": "sossi-2019",
                    "digest": "methods",
                    "reason": "sccm_reference_state_unreported",
                    "conversion_role": "diagnostic_only",
                    "conversion_note": (
                        "Pressure is imposed; fO2 uses the CO/CO2 ratio, so "
                        "absolute sccm conversion is not load-bearing."
                    ),
                },
            }
        )
    return {
        "id": "sossi_gas_schedule",
        "duration_h": 1.0,
        "interpolation": "piecewise_linear",
        "interpolation_source_class": "test_fixture",
        "furnace_ceiling_C": 1500.0,
        "melt_temperature_C": [
            {"t_h": 0.0, "value": 1300.0, "unit": "C"},
            {"t_h": 1.0, "value": 1300.0, "unit": "C"},
        ],
        "chamber_pressure_mbar": [
            {"t_h": 0.0, "value": 1013.25, "unit": "mbar"},
            {"t_h": 1.0, "value": 1013.25, "unit": "mbar"},
        ],
        "gas_boundary": gas_boundary,
    }


def test_component_flows_keep_printed_sccm_and_unreported_state_as_diagnostic() -> None:
    schedule = normalize_lab_schedule(_schedule(component_flows=True))
    gas = schedule["gas_boundary"]

    assert gas["component_flows"]["CO"]["value"] == 130
    assert gas["component_flows"]["CO2"]["value"] == 65
    assert gas["component_flows"]["CO"]["unit"] == "sccm"
    assert gas["component_flows"]["CO2"]["unit"] == "sccm"
    assert gas["reported_total_flow"]["value"] == 200
    assert gas["reported_total_flow"]["qualifier"] == "approximate"
    assert gas["flow_reference_state"]["reason"] == (
        "sccm_reference_state_unreported"
    )
    assert gas["flow_reference_state"]["conversion_role"] == "diagnostic_only"
    assert "not load-bearing" in gas["flow_reference_state"]["conversion_note"]
    assert gas["pressure_control"]["pressure_driver"] == "imposed"
    assert "knob_defaulted" not in gas["flow_reference_state"]


def test_single_species_zero_co_flow_is_retained_without_nl_min_conversion() -> None:
    raw = _schedule(component_flows=True)
    raw["gas_boundary"]["component_flows"]["CO"]["value"] = 0
    raw["gas_boundary"]["component_flows"]["CO2"]["value"] = 27
    raw["gas_boundary"].pop("reported_total_flow")

    schedule = normalize_lab_schedule(raw)

    assert schedule["gas_boundary"]["component_flows"]["CO"]["value"] == 0
    assert schedule["gas_boundary"]["component_flows"]["CO2"]["value"] == 27
    assert schedule["gas_boundary"]["imposed_flow"]["reported_status"] == (
        "not_reported"
    )


def test_sccm_component_flows_cannot_drive_apparatus_pressure() -> None:
    raw = _schedule(component_flows=True)
    raw["gas_boundary"]["pressure_control"]["pressure_driver"] = "apparatus"

    with pytest.raises(
        LabScheduleValidationError,
        match="lab_schedule_component_flows_require_imposed_pressure",
    ):
        normalize_lab_schedule(raw)


def test_missing_sccm_reference_state_stays_diagnostic_and_unconverted() -> None:
    raw = _schedule(component_flows=True)
    raw["gas_boundary"].pop("flow_reference_state")

    gas = normalize_lab_schedule(raw)["gas_boundary"]

    assert gas["flow_reference_state"]["reason"] == (
        "sccm_reference_state_unreported"
    )
    assert gas["flow_reference_state"]["conversion_role"] == "diagnostic_only"
    assert gas["component_flows"]["CO"]["value"] == 130.0
    assert gas["component_flows"]["CO2"]["value"] == 65.0


def test_partial_sccm_reference_state_stays_diagnostic_and_unconverted() -> None:
    raw = _schedule(component_flows=True)
    raw["gas_boundary"]["flow_reference_state"] = {
        "reported_status": "reported",
        "standard_temperature_K": 273.15,
        "source_class": "literature_primary",
        "citation_id": "sossi-2019",
        "source_ref": "Table 2 methods citation",
    }

    gas = normalize_lab_schedule(raw)["gas_boundary"]

    assert gas["flow_reference_state"]["reason"] == (
        "sccm_reference_state_incomplete"
    )
    assert gas["flow_reference_state"]["standard_temperature_K"] == 273.15
    assert gas["flow_reference_state"]["conversion_role"] == "diagnostic_only"
    assert gas["component_flows"]["CO"]["value"] == 130.0
    assert gas["component_flows"]["CO2"]["value"] == 65.0


def test_reported_sccm_reference_state_accepts_source_supported_temperature_and_pressure() -> None:
    raw = _schedule(component_flows=True)
    raw["gas_boundary"]["flow_reference_state"] = {
        "reported_status": "reported",
        "standard_temperature_K": 273.15,
        "standard_pressure_Pa": 101325,
        "source_class": "literature_primary",
        "citation_id": "sossi-2019",
        "source_ref": "Table 2 methods citation",
    }

    schedule = normalize_lab_schedule(raw)

    assert schedule["gas_boundary"]["flow_reference_state"][
        "standard_temperature_K"
    ] == 273.15
    assert schedule["gas_boundary"]["flow_reference_state"][
        "standard_pressure_Pa"
    ] == 101325


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("wrong_unit", "component_flow_unit_must_be_sccm:CO"),
        ("negative_flow", "gas_boundary_component_flow_CO_must_be_non_negative"),
        ("missing_conversion_note", "sccm_conversion_diagnostic_missing_note"),
    ],
)
def test_sccm_component_flow_inputs_fail_closed(mutation: str, expected: str) -> None:
    raw = _schedule(component_flows=True)
    if mutation == "wrong_unit":
        raw["gas_boundary"]["component_flows"]["CO"]["unit"] = "NL_min"
    elif mutation == "negative_flow":
        raw["gas_boundary"]["component_flows"]["CO"]["value"] = -1
    else:
        raw["gas_boundary"]["flow_reference_state"].pop("conversion_note")

    with pytest.raises(LabScheduleValidationError, match=expected):
        normalize_lab_schedule(raw)


def test_unset_component_flow_fields_keep_existing_normalized_schedule() -> None:
    raw = _schedule(component_flows=False)
    original = deepcopy(raw)

    normalized = normalize_lab_schedule(raw)

    assert raw == original
    assert set(normalized["gas_boundary"]) == {
        "background_gas",
        "imposed_flow",
        "pressure_control",
    }
    assert lab_schedule_digests(normalized) == lab_schedule_digests(
        normalize_lab_schedule(original)
    )
