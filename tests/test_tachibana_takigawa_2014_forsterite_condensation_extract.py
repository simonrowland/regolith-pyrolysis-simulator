"""Printed pins and isolated migration for Tachibana and Takigawa (2014)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AdmissionStatus, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT, Migrator
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    check_fidelity_sample_matches,
    validate_extract_document,
)


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/tachibana-takigawa-2014-forsterite-condensation.yaml"
)
SOURCE_ID = "tachibana-takigawa-2014-forsterite-condensation"

THIS_WORK_POINT_TEMPERATURES = {
    "tt2014_fo_alpha_c_1275": Decimal("1275"),
    "tt2014_fo_S_1320": Decimal("1320"),
    "tt2014_fo_S_1275": Decimal("1275"),
    "tt2014_fo_S_1160": Decimal("1160"),
    "tt2014_fo_fig10_run12_S": Decimal("1275"),
    "tt2014_fo_fig10_run13_S": Decimal("1275"),
    "tt2014_fo_fig10_run14_S": Decimal("1275"),
    "tt2014_fo_fig10_run15_S": Decimal("1275"),
    "tt2014_fo_fig10_run16_S": Decimal("1275"),
    "tt2014_fo_thickness_1160": Decimal("1160"),
    "tt2014_fo_grain_size_1160": Decimal("1160"),
    "tt2014_fo_grain_size_1275_over_40h": Decimal("1275"),
    "tt2014_fo_grain_size_1275_ten_um": Decimal("1275"),
    "tt2014_fo_alpha_e_0p03_1426": Decimal("1426"),
    "tt2014_fe_alpha_c_1235": Decimal("1235"),
    "tt2014_fe_alpha_c_1340": Decimal("1340"),
}

REFUSED_QUANTITIES = {
    "tt2014_fo_alpha_c_1275": "condensation_coefficient",
    "tt2014_fo_S_1320": "supersaturation",
    "tt2014_fo_S_1275": "supersaturation",
    "tt2014_fo_S_1160": "supersaturation",
    "tt2014_fo_fig10_run12_S": "supersaturation",
    "tt2014_fo_fig10_run13_S": "supersaturation",
    "tt2014_fo_fig10_run14_S": "supersaturation",
    "tt2014_fo_fig10_run15_S": "supersaturation",
    "tt2014_fo_fig10_run16_S": "supersaturation",
    "tt2014_fo_thickness_1160": "condensate_film_thickness",
    "tt2014_fo_grain_size_1160": "condensate_grain_size",
    "tt2014_fo_grain_size_1275_over_40h": "condensate_grain_size",
    "tt2014_fo_grain_size_1275_ten_um": "condensate_grain_size",
    "tt2014_fe_alpha_c_vacuum_summary": "condensation_coefficient",
    "tt2014_fe_alpha_c_1235": "condensation_coefficient",
    "tt2014_fe_alpha_c_1340": "condensation_coefficient",
}

EVAPORATION_ALPHA_IDS = {
    "tt2014_fo_alpha_e_range",
    "tt2014_fo_alpha_e_0p03_1426",
    "tt2014_fe_alpha_e_vacuum",
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> list[tuple[str, dict]]:
    return [
        (species, observation)
        for species, body in document["species"].items()
        for observation in body["observations"]
    ]


def _by_id(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        observation["observation_id"]: (species, observation)
        for species, observation in _observations(document)
    }


def _migrate(path: Path):
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _stored(result):
    return {
        observation.observation_id.removeprefix(f"{SOURCE_ID}::"): observation
        for observation in result.observations.values()
    }


def test_validator_accepts_the_extract_and_pins_match() -> None:
    document = _document()
    errors = validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert errors == []
    assert check_all_fidelity_samples_match(document, label=SOURCE_ID) == []


def test_printed_coefficients_supersaturations_and_run_labels() -> None:
    rows = _by_id(_document())

    alpha_c = rows["tt2014_fo_alpha_c_1275"][1]
    assert alpha_c["values"]["alpha_low_as_printed"] == "0.006"
    assert alpha_c["values"]["alpha_high_as_printed"] == "0.09"
    assert alpha_c["values"]["interval_as_printed_abstract"] == "0.09–0.006"
    assert alpha_c["values"]["pH2_Pa_as_printed"] == "5.6"
    assert alpha_c["T_K"] == 1275
    assert alpha_c["values"]["quantity"] == "condensation_coefficient"

    assert rows["tt2014_fo_S_1320"][1]["values"]["supersaturation_as_printed"] == "1–5"
    assert rows["tt2014_fo_S_1275"][1]["values"]["supersaturation_as_printed"] == "7–190"
    assert rows["tt2014_fo_S_1160"][1]["values"]["supersaturation_high_as_printed"] == "8000"
    assert rows["tt2014_fo_S_1320"][1]["values"]["condensate_observed"] is False

    run_labels = {
        "tt2014_fo_fig10_run12_S": "13-62",
        "tt2014_fo_fig10_run13_S": "12-56",
        "tt2014_fo_fig10_run14_S": "14-66",
        "tt2014_fo_fig10_run15_S": "18-89",
        "tt2014_fo_fig10_run16_S": "39-188",
    }
    for observation_id, printed in run_labels.items():
        assert rows[observation_id][1]["values"]["supersaturation_as_printed"] == printed

    assert rows["tt2014_fo_thickness_1160"][1]["values"]["thickness_as_printed"] == "~30-150 nm"
    assert rows["tt2014_fo_thickness_1160"][1]["values"]["duration_h_as_printed"] == "93"
    assert rows["tt2014_fo_alpha_e_range"][1]["values"]["alpha_range"] == ["0.02", "0.1"]
    assert rows["tt2014_fo_alpha_e_range"][1]["values"]["interval_as_printed_page_5"] == "0.1-0.02"
    assert rows["tt2014_fo_alpha_e_0p03_1426"][1]["values"]["alpha"] == "0.03"
    assert rows["tt2014_fo_alpha_e_0p03_1426"][1]["T_K"] == 1426
    assert rows["tt2014_fo_alpha_e_0p03_1426"][1]["values"]["pH2_Pa_as_printed"] == "2"
    assert "tt2014_fo_alpha_e_0p03_1275" not in rows
    assert "crystallographic orientations" in rows["tt2014_fo_alpha_e_range"][1]["values"][
        "orientation_dependence_as_printed"
    ]
    context = {
        row["observation_id"]: row
        for body in _document()["species"].values()
        for row in body["context"]
    }
    figure_10_flux = context["tt2014_fo_fig10_evaporation_flux"]["values"]
    assert figure_10_flux["flux_temperature_K_as_printed"] == "1275"
    assert figure_10_flux["citation_as_printed"] == "[8]"
    assert "[10]" not in figure_10_flux["citation_as_printed"]
    section_44 = context["tt2014_fo_s44_flux_compared_in_fig11"]["values"]
    assert section_44["citation_as_printed"] == "[10]"
    assert "[8]" not in section_44["citation_as_printed"]
    assert section_44["compared_in_as_printed"] == "Fig. 11"
    assert "alpha" not in rows["tt2014_fe_alpha_e_vacuum"][1]["values"]
    assert rows["tt2014_fe_alpha_e_vacuum"][1]["values"]["alpha_as_printed"] == "close to unity"
    assert rows["tt2014_fe_alpha_c_1340"][1]["T_K"] == 1340
    assert (
        rows["tt2014_fe_alpha_c_1340"][1]["values"][
            "figure_4_caption_substrate_temperature_K_as_printed"
        ]
        == "1337"
    )

    figures = [
        row
        for body in _document()["species"].values()
        for row in body["context"]
        if row["type"] == "figure_record"
    ]
    assert [row["locator"]["figure"] for row in figures] == [
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "11",
        "1",
        "2",
        "3",
        "4",
    ]
    assert all("digitisation" in row["values"] for row in figures)
    assert all("figure_digitised" not in row["values"] for row in figures)
    assert "10^-9" in figures[5]["values"]["axes_as_printed"]["y_tick_labels"]
    assert figures[5]["values"]["printed_S_labels"][0]["S"] == "13-62"


def test_mutating_the_printed_lower_bound_breaks_the_pin() -> None:
    document = _document()
    sample = next(
        item
        for item in document["fidelity_samples"]
        if item["value"] == "0.006"
    )
    assert check_fidelity_sample_matches(document, sample) == []

    mutated = _document()
    target = _by_id(mutated)["tt2014_fo_alpha_c_1275"][1]
    target["values"]["alpha_low_as_printed"] = "0.007"
    errors = check_fidelity_sample_matches(mutated, sample)
    assert errors
    with pytest.raises(AssertionError):
        assert errors == []


def test_isolated_migration_keeps_temperature_and_refuses_untyped_quantities() -> None:
    result = _migrate(EXTRACT)
    assert not result.validation.hard_issues, [
        f"{issue.path}: {issue.reason.value}: {issue.detail}"
        for issue in result.validation.hard_issues
    ]
    stored = _stored(result)
    assert set(stored) == set(THIS_WORK_POINT_TEMPERATURES) | set(REFUSED_QUANTITIES) | {
        "tt2014_fo_alpha_e_range",
        "tt2014_fe_alpha_e_vacuum",
    }
    # The refused map already includes the point-temperature refusals.
    assert set(stored) == set(REFUSED_QUANTITIES) | EVAPORATION_ALPHA_IDS

    for observation_id, observation in stored.items():
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.locator is not None
        if observation_id in THIS_WORK_POINT_TEMPERATURES:
            assert observation.identity.temperature_K.is_value
            assert observation.identity.temperature_K.value == THIS_WORK_POINT_TEMPERATURES[
                observation_id
            ]
            assert temperature.state.is_value
        else:
            assert temperature.state.is_unknown
            assert observation.identity.temperature_K is None or (
                observation.identity.temperature_K.is_unknown
            )

    alpha_c = stored["tt2014_fo_alpha_c_1275"]
    assert alpha_c.identity.quantity.is_unknown
    assert "condensation_coefficient" in (alpha_c.identity.quantity.reason or "")
    assert alpha_c.admission.status is AdmissionStatus.REJECTED
    assert alpha_c.point_conditions["pH2_Pa"].state.is_value
    assert alpha_c.point_conditions["pH2_Pa"].state.value == Decimal("5.6")
    assert alpha_c.point_conditions["pH2_Pa"].locator is not None

    thickness = stored["tt2014_fo_thickness_1160"]
    assert thickness.point_conditions["duration_h"].state.value == Decimal("93")
    assert thickness.point_conditions["duration_h"].locator is not None
    assert "condensate_film_thickness" in (thickness.identity.quantity.reason or "")

    run12 = stored["tt2014_fo_fig10_run12_S"]
    assert run12.point_conditions["supersaturation"].state.is_value
    assert run12.point_conditions["supersaturation"].locator is not None
    assert run12.admission.status is AdmissionStatus.REJECTED

    alpha_range = stored["tt2014_fo_alpha_e_range"]
    assert alpha_range.identity.quantity.is_value
    assert alpha_range.identity.quantity.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert alpha_range.value.kind is ValueKind.INTERVAL
    assert alpha_range.value.interval_low == Decimal("0.02")
    assert alpha_range.value.interval_high == Decimal("0.1")
    assert alpha_range.admission.status is not AdmissionStatus.REJECTED

    alpha_1426 = stored["tt2014_fo_alpha_e_0p03_1426"]
    assert alpha_1426.identity.quantity.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert alpha_1426.value.kind is ValueKind.POINT
    assert alpha_1426.value.point == Decimal("0.03")
    assert alpha_1426.identity.temperature_K.value == Decimal("1426")
    assert alpha_1426.point_conditions["pH2_Pa"].state.value == Decimal("2")
    assert alpha_1426.admission.status is AdmissionStatus.ADMITTED
    assert "tt2014_fo_alpha_e_0p03_1275" not in stored
    alpha_points_at_1275 = [
        observation_id
        for observation_id, observation in stored.items()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
        and observation.value.kind is ValueKind.POINT
        and observation.identity.temperature_K is not None
        and observation.identity.temperature_K.is_value
        and observation.identity.temperature_K.value == Decimal("1275")
    ]
    assert alpha_points_at_1275 == []

    iron_evaporation = stored["tt2014_fe_alpha_e_vacuum"]
    assert iron_evaporation.identity.quantity.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert iron_evaporation.value.kind is ValueKind.UNAVAILABLE
    assert iron_evaporation.point_conditions["pFe_Pa"].state.value == Decimal("0")

    iron_1340 = stored["tt2014_fe_alpha_c_1340"]
    assert iron_1340.point_conditions["figure_4_caption_temperature_K"].state.value == Decimal(
        "1337"
    )
    assert iron_1340.identity.temperature_K.value == Decimal("1340")


def test_mutating_the_printed_temperature_changes_the_migrated_identity(
    tmp_path: Path,
) -> None:
    original = _stored(_migrate(EXTRACT))["tt2014_fo_alpha_c_1275"]
    assert original.identity.temperature_K.value == Decimal("1275")

    document = _document()
    row = _by_id(document)["tt2014_fo_alpha_c_1275"][1]
    row["T_K"] = 1276
    row["point_conditions"]["temperature_K"]["state"]["value"] = "1276"
    mutated_path = tmp_path / EXTRACT.name
    mutated_path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    mutated = _stored(_migrate(mutated_path))["tt2014_fo_alpha_c_1275"]
    assert mutated.identity.temperature_K.value == Decimal("1276")
    assert mutated.identity.temperature_K.value != original.identity.temperature_K.value
