"""Source pins and isolated migration checks for Shornikov (2019)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    NoticeKind,
    Quantity,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.identity import (
    IdentityEqualKind,
    PA_PER_ATM_DEC,
    PA_PER_BAR_DEC,
    identity_equal,
    standard_pressure_delta_g_kJ_per_mol,
)
from simulator.battery.migrate import Migrator
from simulator.battery.score import _printed_uncertainty_band
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2019-mgo-titania-melts.yaml"
SOURCE_ID = "shornikov-2019-mgo-titania-melts"


def _assert_sc456_model_class(row: dict) -> None:
    assert row["values"]["method_class"] == "model_derived"
    assert row["values"]["value"] == row["values"]["value_as_printed"]
    assert row["values"]["method_as_printed"].startswith("Calculated in the developed")
    assert "Standard Gibbs energies calculated" in row["values"]["method_description"]
    assert row["values"]["attribution"] == (
        "Cited model input references: Glushko et al. (1978–1982); Bale et al. (2016)"
    )
    assert row["derivation"]["method"]["locator"]["section"]
    assert row["derivation"]["references"]


def test_render_pins_and_isolated_migration_preserve_every_temperature():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = [
        (formula, row)
        for formula, block in document["species"].items()
        for row in block.get("observations", [])
    ]
    table_rows = [
        row
        for _, row in observations
        if row["locator"].get("table") in {"1", "2"}
    ]
    figure_rows = [row for _, row in observations if row["locator"].get("figure")]
    assert len(table_rows) == 42
    model_table_rows = [row for row in table_rows if row["locator"].get("table") == "2"]
    assert len(model_table_rows) == 20
    assert all(row["values"]["method_class"] == "model_derived" for row in model_table_rows)
    _assert_sc456_model_class(model_table_rows[0])
    flipped = deepcopy(model_table_rows[0])
    flipped["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_sc456_model_class(flipped)
    assert len(figure_rows) == 16
    assert len(observations) == 63

    # Literal reads from the rendered table pages, independent of the audit CSV.
    rendered_table_cells = (
        (
            "Mg2TiO4",
            "shornikov_2019_mgo_titania_melts_p002_t01_r03_c03",
            "-5.69",
            "±0.33",
            "1",
            2,
            86,
        ),
        (
            "MgO",
            "shornikov_2019_mgo_titania_melts_p003_t02_r03_c02",
            "-355.987",
            None,
            "2",
            3,
            87,
        ),
        (
            "O2",
            "shornikov_2019_mgo_titania_melts_p003_t02_r12_c06",
            "0.000",
            None,
            "2",
            3,
            87,
        ),
    )
    for formula, observation_id, value, uncertainty, table, page, published_page in rendered_table_cells:
        row = next(
            row
            for candidate, row in observations
            if candidate == formula and row["observation_id"] == observation_id
        )
        assert row["values"].get("value_as_printed", row["values"].get("value")) == value
        assert row["locator"]["table"] == table
        assert row["locator"]["page"] == page
        assert row["locator"]["published_page"] == published_page
        if uncertainty is not None:
            assert row["values"]["uncertainty_as_published"] == uncertainty

    interval_row = next(
        row
        for _, row in observations
        if row["observation_id"]
        == "shornikov_2019_mgo_titania_melts_p002_t01_r07_c03"
    )
    interval = interval_row["point_conditions"]["temperature_K"]
    assert interval["state"]["value"] == {
        "kind": "interval",
        "interval_low": "1700",
        "interval_high": "2030",
    }
    assert interval["locator"]["page"] == 2

    # These panels contain only calculated curves, so the figure rule requires
    # unavailable curve records and forbids sampling them as data markers.
    model_curve_pins = (
        ("shornikov_2019_mgo_titania_melts_fig2_panel_a_curve_1", "1973", "2"),
        ("shornikov_2019_mgo_titania_melts_fig3_curve_1", "1973", "3"),
        ("shornikov_2019_mgo_titania_melts_fig4_curve_1", "2300", "4"),
    )
    for observation_id, temperature, figure in model_curve_pins:
        row = next(row for _, row in observations if row["observation_id"] == observation_id)
        assert row["locator"]["figure"] == figure
        assert row["values"]["T_K"] == temperature
        assert row["values"]["method_class"] == "figure_only"
        assert row["values"]["admission_status"] == "typed_refusal"
        assert row["point_conditions"]["temperature_K"]["locator"] is not None
        assert row["point_conditions"]["composition"]["state"]["tag"] == "unknown"

    fig4_axis_reasons = {
        row["observation_id"]: row["point_conditions"]["composition"]["state"]["reason"]
        for _, row in observations
        if row["observation_id"].endswith(("fig4_curve_1", "fig4_curve_2"))
    }
    assert len(fig4_axis_reasons) == 2
    assert all("SiO2 composition axis" in reason for reason in fig4_axis_reasons.values())

    fusion_quote = next(
        row
        for _, row in observations
        if row["observation_id"].endswith("prose_mgti2o5_heat_of_fusion_bale_2016")
    )
    assert fusion_quote["values"]["delta_H_fus_kJ_mol-1"] == "54.343"
    assert fusion_quote["values"]["T_fus_K"] == "1930"

    approximate_estimate = next(
        row
        for _, row in observations
        if row["observation_id"].endswith("prose_mgtio3_delta_fh298_estimate")
    )
    assert approximate_estimate["values"]["value"] == "-856"
    assert approximate_estimate["values"]["approximate"] is True
    assert approximate_estimate["phase"] == "g"
    assert approximate_estimate["per"] == "mol_species"
    assert approximate_estimate["values"]["method_class"] == "quoted_attributed"
    assert approximate_estimate["values"]["attribution"] == "Lopatin and Semenov (2001)"

    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [row for row in result.observations.values() if row.source_id == SOURCE_ID]
    assert len(migrated) == 63
    assert sum(row.value.kind is ValueKind.POINT for row in migrated) == 37
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated) == 25
    assert sum(row.value.kind is ValueKind.CATEGORICAL for row in migrated) == 1
    assert sum(row.admission.status is AdmissionStatus.ADMITTED for row in migrated) == 22
    assert sum(row.admission.status is AdmissionStatus.REJECTED for row in migrated) == 41
    assert not result.registry_issues
    assert result.validation is not None
    assert not result.validation.hard_issues

    for row in migrated:
        temperature = row.point_conditions["temperature_K"]
        assert temperature.locator is not None
        raw_temperature = temperature.state.value
        temperature_kind = getattr(raw_temperature, "kind", ValueKind.POINT)
        if temperature.state.is_value and temperature_kind is ValueKind.POINT:
            assert row.identity.temperature_K is not None
            assert row.identity.temperature_K.is_value
            point = getattr(raw_temperature, "point", raw_temperature)
            assert row.identity.temperature_K.value == point
        else:
            assert row.identity.temperature_K is None or row.identity.temperature_K.is_unknown

    for row in migrated:
        if row.locator.figure is not None:
            assert row.value.kind is ValueKind.UNAVAILABLE
            assert row.admission.status is AdmissionStatus.REJECTED
            composition = row.point_conditions["composition"]
            assert composition.state.is_unknown
            assert composition.locator is not None

    fusion = next(row for row in migrated if row.observation_id.endswith("prose_mgti2o5_heat_of_fusion_bale_2016"))
    assert fusion.value.kind is ValueKind.POINT
    assert fusion.value.point == Decimal("54.343")
    assert fusion.identity.temperature_K.value == Decimal("1930")

    estimate = next(row for row in migrated if row.observation_id.endswith("prose_mgtio3_delta_fh298_estimate"))
    assert estimate.value.kind is ValueKind.POINT
    assert estimate.value.point == Decimal("-856")
    assert estimate.value.approximate
    assert estimate.identity.temperature_K.value == Decimal("298")


def _migrate_extract_document(document: dict, path: Path):
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _source_observation(document: dict, observation_id: str) -> dict:
    return next(
        row
        for block in document["species"].values()
        for row in block.get("observations", [])
        if row["observation_id"] == observation_id
    )


def _migrated_observation(result, observation_id: str):
    return next(
        row
        for row in result.observations.values()
        if row.observation_id.endswith(observation_id)
    )


@pytest.mark.parametrize("temperature_K", ("2173", "2973"))
def test_unprinted_standard_pressure_widens_printed_gas_band_at_row_temperature(
    tmp_path: Path, temperature_K: str
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observation_id = "shornikov_2019_mgo_titania_melts_p003_t02_r12_c06"
    row = _source_observation(document, observation_id)
    row["standard_pressure"] = "not_printed"
    row["values"]["T_K"] = temperature_K
    row["uncertainty"] = "±0.2 kJ/mol"

    result = _migrate_extract_document(document, tmp_path / EXTRACT.name)
    observation = _migrated_observation(result, observation_id)

    expected = abs(
        standard_pressure_delta_g_kJ_per_mol(
            Decimal("1"),
            Decimal(temperature_K),
            PA_PER_BAR_DEC,
            PA_PER_ATM_DEC,
        )
    )
    assert observation.uncertainty.kind is UncertaintyKind.DERIVED
    assert observation.uncertainty.value == expected
    assert observation.uncertainty.basis == "kJ/mol"
    assert observation.uncertainty.verbatim == "±0.2 kJ/mol"
    scored = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        observation.uncertainty,
        observation.value.point,
        source_observation=observation,
    )
    assert scored.refusal is None
    assert scored.band is not None
    assert scored.band.value == Decimal("0.2") + expected
    assert scored.band.band_basis == "printed_plus_derived_bound"
    if temperature_K == "2973":
        assert abs(expected - Decimal("0.33")) < Decimal("0.005")
    notice = next(
        notice for notice in observation.notices
        if notice.kind is NoticeKind.INPUT_OMITTED
    )
    assert notice.reason == "standard pressure unprinted (1 bar vs 1 atm)"


def test_unprinted_standard_pressure_is_the_derived_only_gas_band(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observation_id = "shornikov_2019_mgo_titania_melts_p003_t02_r12_c06"
    row = _source_observation(document, observation_id)
    row["standard_pressure"] = "not_printed"
    result = _migrate_extract_document(document, tmp_path / EXTRACT.name)
    observation = _migrated_observation(result, observation_id)

    expected = abs(
        standard_pressure_delta_g_kJ_per_mol(
            Decimal("1"),
            Decimal("2173"),
            PA_PER_BAR_DEC,
            PA_PER_ATM_DEC,
        )
    )
    scored = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        observation.uncertainty,
        observation.value.point,
        source_observation=observation,
    )
    assert scored.refusal is None
    assert scored.band is not None
    assert scored.band.value == expected
    assert scored.band.band_basis == "derived_bound"
    assert "only band source" in scored.band.rule


def test_unprinted_standard_pressure_condensed_row_gets_notice_only(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observation_id = "shornikov_2019_mgo_titania_melts_p003_t02_r03_c02"
    row = _source_observation(document, observation_id)
    row["standard_pressure"] = "not_printed"

    result = _migrate_extract_document(document, tmp_path / EXTRACT.name)
    observation = _migrated_observation(result, observation_id)

    assert observation.uncertainty.kind is UncertaintyKind.NONE
    scored = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        observation.uncertainty,
        observation.value.point,
        source_observation=observation,
    )
    assert scored.band is None
    assert any(
        notice.kind is NoticeKind.INPUT_OMITTED
        and notice.reason == "standard pressure unprinted (1 bar vs 1 atm)"
        for notice in observation.notices
    )


def test_absent_standard_pressure_keeps_existing_gas_uncertainty_behavior(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observation_id = "shornikov_2019_mgo_titania_melts_p003_t02_r12_c06"
    row = _source_observation(document, observation_id)
    row.pop("standard_pressure", None)

    result = _migrate_extract_document(document, tmp_path / EXTRACT.name)
    observation = _migrated_observation(result, observation_id)

    assert observation.uncertainty.kind is UncertaintyKind.NONE
    assert not any(
        notice.reason == "standard pressure unprinted (1 bar vs 1 atm)"
        for notice in observation.notices
    )


def test_standard_pressure_declaration_is_closed_and_requires_gas_temperature() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observation_id = "shornikov_2019_mgo_titania_melts_p003_t02_r12_c06"
    row = _source_observation(document, observation_id)
    row["standard_pressure"] = "one_bar"
    assert any(
        ".standard_pressure: must be 'not_printed'" in error
        for error in validate_extract_document(document, expected_source_id=SOURCE_ID)
    )

    row["standard_pressure"] = "not_printed"
    row["values"].pop("T_K")
    assert any(
        ".standard_pressure: gas delta_fG rows require a positive row values.T_K"
        in error
        for error in validate_extract_document(document, expected_source_id=SOURCE_ID)
    )


def test_table2_values_remain_a_typed_frame_gap_pending_physics(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table2_rows = [
        row
        for block in document["species"].values()
        for row in block.get("observations", [])
        if row["locator"].get("table") == "2"
    ]
    assert len(table2_rows) == 20
    assert all("identity" not in row for row in table2_rows)

    result = _migrate_extract_document(document, tmp_path / EXTRACT.name)
    for row in table2_rows:
        observation = _migrated_observation(result, row["observation_id"])
        assert observation.value.point == Decimal(row["values"]["value_as_printed"])
        for axis_name in (
            "standard_pressure_Pa",
            "reaction",
            "formation_elements",
        ):
            axis = getattr(observation.identity, axis_name)
            assert axis is None or axis.is_unknown, (row["observation_id"], axis_name)
        assert "derived_by" not in (observation.provenance or {})

    scored_source = _migrated_observation(
        result, "shornikov_2019_mgo_titania_melts_p003_t02_r03_c06"
    )
    assert scored_source.value.point == Decimal("-72.400")
    assert (
        identity_equal(scored_source.identity, scored_source.identity).kind
        is IdentityEqualKind.IDENTITY_UNKNOWN
    )
