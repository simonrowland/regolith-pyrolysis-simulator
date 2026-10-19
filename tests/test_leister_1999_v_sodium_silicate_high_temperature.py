from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    EvidenceClass,
    Phase,
    Quantity,
    UncertaintyKind,
    ValenceValueForm,
    ValueKind,
)
from simulator.battery.migrate import Migrator
from simulator.battery.score import _printed_uncertainty_band


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "leister-1999-v-sodium-silicate-high-temperature"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text())


def _migrate() -> Migrator:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def test_table2_valence_cells_render_pins_and_migrate_with_conditions() -> None:
    document = _document()
    species = document["species"]["V"]
    observations = species["observations"]
    assert len(observations) == 7

    points = [
        point
        for observation in observations
        for point in observation["values"]["points"]
    ]
    assert len(points) == 32
    assert all(isinstance(point["value"], str) for point in points)
    assert all(point["value"] == point["as_printed"].rstrip("%") for point in points)
    assert all(
        (point["point_conditions"]["atmosphere"]["state"]["value"] == "N2")
        == (point["quench_status"]["state"]["value"] == "not_stated")
        for point in points
    )
    optical_observations = [
        observation
        for observation in observations
        if observation["method_as_printed"] == "Optical absorption spectroscopy"
    ]
    assert len(optical_observations) == 5
    assert all(
        Decimal(observation["uncertainty"]["value"]) == Decimal("10")
        and observation["uncertainty"]["units"] == "%"
        and observation["uncertainty"]["as_printed"] == "±10%"
        and observation["uncertainty"]["confidence_level"] == "not_stated"
        and observation["uncertainty"]["notice"] == "confidence level not stated"
        for observation in optical_observations
    )
    assert all(
        observation["values"]["method_class"] == "measured_reduced"
        and observation["values"]["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
        for observation in optical_observations
    )

    # Independent 600 dpi render pins from Table 2, page 322. The YAML keeps
    # the printed percentage; the migrator converts it to the fraction form.
    disilicate_v5 = observations[0]["values"]["points"]
    assert disilicate_v5[0]["value"] == "52"
    assert disilicate_v5[0]["as_printed"] == "52%"
    disilicate_esr_v4 = observations[2]["values"]["points"]
    assert disilicate_esr_v4[2]["value"] == "3"
    assert disilicate_esr_v4[2]["as_printed"] == "3%"
    silica_rich_v3 = observations[5]["values"]["points"]
    assert silica_rich_v3[0]["value"] == "72"
    assert silica_rich_v3[0]["as_printed"] == "72%"

    migrated = _migrate()
    rows = list(migrated.result.observations.values())
    assert len(rows) == 32
    assert not migrated.result.validation.hard_issues
    assert all(row.value.kind is ValueKind.POINT for row in rows)
    assert all(row.identity.quantity.value is Quantity.VALENCE_RATIO for row in rows)
    assert any(row.value.point == Decimal("0.52") for row in rows)
    assert any(row.value.point == Decimal("0.03") for row in rows)
    assert any(row.value.point == Decimal("0.72") for row in rows)

    for row in rows:
        assert row.identity.species.phase.value is Phase.GLASS
        assert row.identity.temperature_K.is_value
        assert row.point_conditions is not None
        assert row.point_conditions["atmosphere"].locator is not None
        assert row.point_conditions["composition"].locator is not None
        assert row.point_conditions["composition"].state.is_value
        assert row.identity.composition.is_value
        assert row.identity.valence_basis.is_value
        assert row.identity.valence_basis.value.value_form is ValenceValueForm.FRACTION
        assert row.evidence.class_.value is EvidenceClass.MEASURED_REDUCED

    for observation in optical_observations:
        migrated_row = next(
            row for row in rows
            if row.observation_id.startswith(
                f"{SOURCE_ID}::{observation['observation_id']}::"
            )
        )
        assert migrated_row.uncertainty.verbatim["value"] == "10"
        assert migrated_row.uncertainty.verbatim["units"] == "%"
        assert migrated_row.uncertainty.verbatim["as_printed"] == "±10%"
        assert migrated_row.uncertainty.verbatim["confidence_level"] == "not_stated"
        assert migrated_row.uncertainty.kind is UncertaintyKind.PRINTED
        resolution = _printed_uncertainty_band(
            Quantity.VALENCE_RATIO,
            migrated_row.uncertainty,
            migrated_row.value.point,
            source_observation=migrated_row,
        )
        assert resolution.refusal is None
        assert resolution.band is not None and resolution.band.value > 0
        assert resolution.band.band_basis == "row_printed"
        assert resolution.notice is not None
        assert resolution.notice.reason == "confidence_level_not_stated"

    esr_observations = [
        observation for observation in observations
        if observation["method_as_printed"] == "Electron spin resonance spectroscopy"
    ]
    assert len(esr_observations) == 2
    for observation in esr_observations:
        uncertainty = observation["uncertainty"]
        assert uncertainty["form"] == "relative_bound"
        assert uncertainty["bound_value"] == "≤15%"
        assert "value" not in uncertainty
        migrated_rows = [
            row for row in rows
            if row.observation_id.startswith(
                f"{SOURCE_ID}::{observation['observation_id']}::"
            )
        ]
        assert migrated_rows
        assert all(
            row.uncertainty.verbatim["form"] == "relative_bound"
            and row.uncertainty.verbatim["bound_value"] == "≤15%"
            and "value" not in row.uncertainty.verbatim
            for row in migrated_rows
        )

    compositions = {
        tuple(row.identity.composition.value.components)
        for row in rows
    }
    assert compositions == {
        (("Na2O", Decimal("0.33")), ("SiO2", Decimal("0.67"))),
        (("Na2O", Decimal("0.15")), ("SiO2", Decimal("0.85"))),
    }

    # Ten n.d. optical cells and one below-detection V5+ cell stay unscored
    # with their conditions and printed Table 2 values in the context record.
    unreported = next(
        item for item in species["context"]
        if item["observation_id"] == "leister99_table2_unreported_optical_cells"
    )
    unreported_rows = unreported["values"]["rows"]
    assert len(unreported_rows) == 11
    assert sum(row["status"] == "not_detected" for row in unreported_rows) == 10
    assert sum(row["status"] == "below_detection_limit" for row in unreported_rows) == 1
    assert any(row.get("optical_V5_as_printed") == "<0.5%" for row in unreported_rows)
    assert any(row.get("ESR_V4_as_printed") == "—" for row in unreported_rows)

    method_conditions = next(
        item for item in species["context"]
        if item["observation_id"] == "leister99_textual_melting_and_method_conditions"
    )
    assert any(
        row.get("sodium_oxide_relative_loss_pct") == "10"
        for row in method_conditions["values"]["rows"]
    )
    assert any(
        row.get("relative_error_as_printed") == "≤15%"
        for row in method_conditions["values"]["rows"]
    )
    oxygen_pressures = next(
        item for item in species["context"]
        if item["observation_id"] == "leister99_textual_melt_oxygen_partial_pressures"
    )["values"]["rows"]
    assert oxygen_pressures[0]["p_O2_kPa_as_printed"] == "21"
    assert oxygen_pressures[1]["p_O2_kPa_range_as_printed"] == "0.5–1.5"

    figures = [item for item in species["context"] if item["type"] == "figure"]
    assert len(figures) == 10
    assert all(item["values"]["points"] == [] for item in figures)
    assert all(item["values"]["digitisation"] == "deferred to a figure pass" for item in figures)


def test_figures_and_model_outputs_are_context_only() -> None:
    context = _document()["species"]["V"]["context"]
    swv = next(item for item in context if item["observation_id"] == "leister99_table2_swv_equilibrium_percentages")
    assert swv["values"]["method_class"] == "model_derived"
    assert len(swv["values"]["rows"]) == 7
    assert len(swv["values"]["unreported_rows"]) == 8
    assert all(
        row["V5_pct"] == row["V4_pct"] == row["V3_pct"] == "—"
        for row in swv["values"]["unreported_rows"]
    )

    table4 = next(item for item in context if item["observation_id"] == "leister99_table4_redox_thermodynamics")
    assert len(table4["values"]["rows"]) == 4
    assert "not formation enthalpy or absolute entropy" in table4["values"]["note"]

    viscosity = next(item for item in context if item["observation_id"] == "leister99_vft_viscosity_values")
    assert viscosity["values"]["method_class"] == "model_derived"
    assert len(viscosity["values"]["rows"]) == 4
