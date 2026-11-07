"""Source pins and isolated migration checks for Shornikov (2019)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    NoticeKind,
    Phase,
    QUANTITY_UNITS,
    Quantity,
    ReferenceStateConvention,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.identity import (
    Identity,
    IdentityEqualKind,
    PA_PER_ATM_DEC,
    PA_PER_BAR_DEC,
    identity_equal,
    standard_pressure_delta_g_kJ_per_mol,
    quantity_token,
)
from simulator.battery.migrate import Migrator
from simulator.battery.records import Execution, phase_token
from simulator.battery.score import (
    ENGINE_CHANNELS,
    EnginePrediction,
    ScoreContext,
    _condensed_element_reference_table_diagnostics,
    _condensed_element_reference_table_key,
    _printed_uncertainty_band,
    _resolve_condensed_element_reference,
    compile_residual,
)
from simulator.battery.generators.janaf import (
    janaf_condensed_element_gas_delta_fG_kJ_per_mol,
    janaf_condensed_element_gas_diagnostic_uncertainty_terms,
)
from simulator.reference_data.janaf import table_fusion_transition_for_table_id
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2019-mgo-titania-melts.yaml"
SOURCE_ID = "shornikov-2019-mgo-titania-melts"
_CONDENSED_REFERENCE_SOURCE_PATHS = {
    "shornikov-2008-mgo-aluminosilicate-melts": ROOT
    / "data/literature/extracts/shornikov-2008-mgo-aluminosilicate-melts.yaml",
    "shornikov-2011-cao-mgo-alumina-melts": ROOT
    / "data/literature/extracts/shornikov-2011-cao-mgo-alumina-melts.yaml",
    "shornikov-2019-ca-feo-vapor": ROOT
    / "data/literature/extracts/shornikov-2019-ca-feo-vapor.yaml",
    SOURCE_ID: EXTRACT,
    "shornikov-2020-mgo-feo-melts": ROOT
    / "data/literature/extracts/shornikov-2020-mgo-feo-melts.yaml",
}


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


def _migrate_condensed_reference_sources(tmp_path: Path):
    results = {}
    observations = []
    for source_id, path in _CONDENSED_REFERENCE_SOURCE_PATHS.items():
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        result = _migrate_extract_document(document, tmp_path / path.name)
        results[source_id] = result
        observations.extend(result.observations.values())
    diagnostics = _condensed_element_reference_table_diagnostics(observations)
    return results, observations, diagnostics


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


def test_condensed_element_frame_diagnostics_and_injected_compiler_predictions(
    tmp_path: Path,
) -> None:
    results, observations, diagnostics = _migrate_condensed_reference_sources(tmp_path)
    expected_keys = {
        ("shornikov-2008-mgo-aluminosilicate-melts", "unnumbered:2:1"),
        ("shornikov-2011-cao-mgo-alumina-melts", "table:1"),
        ("shornikov-2019-ca-feo-vapor", "table:1"),
        (SOURCE_ID, "table:2"),
        ("shornikov-2020-mgo-feo-melts", "table:2"),
    }
    assert set(diagnostics) == expected_keys
    rejected_2011_key = (
        "shornikov-2011-cao-mgo-alumina-melts",
        "table:1",
    )
    accepted_keys = expected_keys - {rejected_2011_key}
    assert all(diagnostics[key]["accepted"] for key in accepted_keys)
    assert not diagnostics[rejected_2011_key]["accepted"]
    assert [
        row["formula"]
        for row in diagnostics[rejected_2011_key]["contradicting_rows"]
    ] == ["Ca"]
    table_diagnostic_pins = (
        (
            ("shornikov-2008-mgo-aluminosilicate-melts", "unnumbered:2:1"),
            "Mg",
            "shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r12_c06",
            Decimal("0.35091975521285301480427524"),
            True,
        ),
        (
            rejected_2011_key,
            "Mg",
            "shornikov_2011_cao_mgo_alumina_melts_p001_t01_r12_c06",
            Decimal("0.3104"),
            True,
        ),
        (
            ("shornikov-2011-cao-mgo-alumina-melts", "table:1"),
            "Ca",
            "shornikov_2011_cao_mgo_alumina_melts_p001_t01_r10_c06",
            Decimal("-2.0726"),
            True,
        ),
        (
            ("shornikov-2019-ca-feo-vapor", "table:1"),
            "Ca",
            "shornikov_2019_ca_feo_vapor_p003_t01_r03_c06",
            Decimal("-1.68288"),
            True,
        ),
        (
            ("shornikov-2019-ca-feo-vapor", "table:1"),
            "Fe",
            "shornikov_2019_ca_feo_vapor_p003_t01_r06_c06",
            Decimal("-1.29457"),
            False,
        ),
        (
            (SOURCE_ID, "table:2"),
            "Mg",
            "shornikov_2019_mgo_titania_melts_p003_t02_r03_c06",
            Decimal("0.3988030760135288711447779"),
            True,
        ),
        (
            (SOURCE_ID, "table:2"),
            "Ti",
            "shornikov_2019_mgo_titania_melts_p003_t02_r06_c06",
            Decimal("0.30671"),
            False,
        ),
        (
            ("shornikov-2020-mgo-feo-melts", "table:2"),
            "Mg",
            "shornikov_2020_mgo_feo_melts_p002_t03_r03_c06",
            Decimal("0.5629336104482892795225964"),
            True,
        ),
        (
            ("shornikov-2020-mgo-feo-melts", "table:2"),
            "Fe",
            "shornikov_2020_mgo_feo_melts_p002_t03_r06_c06",
            Decimal("-1.324"),
            False,
        ),
    )
    for (
        table_key,
        formula,
        observation_id,
        residual,
        frame_discriminating,
    ) in table_diagnostic_pins:
        diagnostic_row = next(
            (
                row
                for row in diagnostics[table_key]["diagnostic_rows"]
                if row.get("formula") == formula
                and row["observation"].observation_id.endswith(observation_id)
            ),
            None,
        )
        assert diagnostic_row is not None, (table_key, formula, observation_id)
        assert diagnostic_row["formula"] == formula
        assert diagnostic_row["observation"].observation_id.endswith(observation_id)
        assert abs(diagnostic_row["residual_kJ_per_mol"] - residual) < Decimal(
            "1e-6"
        )
        assert diagnostic_row["frame_discriminating"] is frame_discriminating
        tolerance = diagnostic_row["tolerance_kJ_per_mol"]
        if tolerance is not None:
            terms = diagnostic_row["tolerance_terms_kJ_per_mol"]
            assert terms["JANAF_delta_fH_plus_fusion_kJ_per_mol"] == (
                terms["gas_delta_fH_component_kJ_per_mol"]
                + terms["fusion_transition_component_kJ_per_mol"]
            )
            sigma_terms = (
                terms["printed_rounding_kJ_per_mol"],
                terms["JANAF_delta_fH_plus_fusion_kJ_per_mol"],
                terms["gas_interpolation_kJ_per_mol"],
                terms["liquid_interpolation_kJ_per_mol"],
                terms["liquid_extrapolation_kJ_per_mol"],
            )
            assert tolerance == Decimal(2) * sum(
                (term**2 for term in sigma_terms), Decimal(0)
            ).sqrt()
            assert diagnostic_row["frame_discriminating"] is (
                abs(diagnostic_row["frame_offset_kJ_per_mol"]) > tolerance
            )
        if table_key == rejected_2011_key:
            assert diagnostics[table_key]["accepted_row"] is None
            if formula == "Ca":
                # The derived budget leaves the −2.0726 kJ/mol Ca residual
                # outside tolerance, so it contradicts and refuses Table 1.
                assert not (
                    abs(diagnostic_row["residual_kJ_per_mol"]) <= tolerance
                )
        if formula == "Mg" and table_key == (SOURCE_ID, "table:2"):
            assert terms["printed_rounding_kJ_per_mol"] == Decimal("0.0005")
    accepted_row_formulas = {
        table_key: diagnostics[table_key]["accepted_row"]["formula"]
        for table_key in accepted_keys
    }
    assert accepted_row_formulas == {
        ("shornikov-2008-mgo-aluminosilicate-melts", "unnumbered:2:1"): "Mg",
        ("shornikov-2019-ca-feo-vapor", "table:1"): "Ca",
        (SOURCE_ID, "table:2"): "Mg",
        ("shornikov-2020-mgo-feo-melts", "table:2"): "Mg",
    }


    # The 2019 paper's three independent checks are recomputed from the JANAF
    # owner, rather than copied into the conversion path as expected values.
    # O(g) checks its oxygen reference but is excluded from metal-frame acceptance.
    janaf_pins = {
        "Mg": (
            "shornikov_2019_mgo_titania_melts_p003_t02_r03_c06",
            "-72.400",
            "0.399",
        ),
        "Ti": (
            "shornikov_2019_mgo_titania_melts_p003_t02_r06_c06",
            "170.472",
            "0.316",
        ),
        "O": (
            "shornikov_2019_mgo_titania_melts_p003_t02_r11_c06",
            "110.091",
            "0.122",
        ),
    }
    for element, (observation_id, printed, expected_residual) in janaf_pins.items():
        row = next(
            item
            for item in observations
            if item.observation_id.endswith(observation_id)
        )
        prediction = janaf_condensed_element_gas_delta_fG_kJ_per_mol(
            element, row.identity.temperature_K.value
        )
        residual = row.value.point - prediction
        assert row.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        assert row.value.point == Decimal(printed)
        assert abs(residual - Decimal(expected_residual)) < Decimal("0.01")

    for row in observations:
        table_key = _condensed_element_reference_table_key(row)
        if (
            table_key not in expected_keys
            or not isinstance(row.identity, Identity)
            or quantity_token(row.identity) is not Quantity.DELTA_FG
            or row.value.kind is not ValueKind.POINT
            or row.value.point is None
        ):
            continue
        resolved = _resolve_condensed_element_reference(
            row,
            observations=observations,
            table_diagnostics=diagnostics,
        )
        if table_key == rejected_2011_key:
            assert resolved.identity.reaction.is_unknown
            assert resolved.identity.formation_elements.is_unknown
            assert not (resolved.provenance or {}).get("derived_by")
            continue
        assert resolved.identity.standard_pressure_Pa.is_value, row.observation_id
        assert resolved.identity.reaction.is_value, row.observation_id
        assert resolved.identity.formation_elements.is_value, row.observation_id
        assert resolved.value.point == row.value.point
        derived_by = resolved.provenance["derived_by"]
        assert derived_by["standard_pressure_Pa"] == "d351_nominal_p0"
        assert derived_by["reaction"] == "d338_reference_state_owner"
        assert derived_by["formation_elements"] == "d338_reference_state_owner"
        assert any(
            notice.reason == "standard pressure unprinted (1 bar vs 1 atm)"
            for notice in resolved.notices
        )
        if phase_token(row.identity.species) is Phase.G:
            assert resolved.uncertainty.kind is UncertaintyKind.DERIVED
        else:
            assert resolved.uncertainty == row.uncertainty

    mgo_solid = next(
        row
        for row in results[SOURCE_ID].observations.values()
        if row.observation_id.endswith(
            "shornikov_2019_mgo_titania_melts_p003_t02_r03_c02"
        )
    )
    resolved_mgo_solid = _resolve_condensed_element_reference(
        mgo_solid,
        observations=results[SOURCE_ID].observations.values(),
        table_diagnostics=diagnostics,
    )
    assert resolved_mgo_solid.identity.species.polymorph.is_value
    assert (
        resolved_mgo_solid.provenance["derived_by"]["species.polymorph"]
        == "d338_reference_state_owner"
    )

    # Compiler-only proof: feed independent JANAF predictions through the
    # ordinary residual compiler. Production prediction is tested separately.
    for table_key in sorted(accepted_keys):
        diagnostic_row = diagnostics[table_key]["accepted_row"]
        assert diagnostic_row is not None
        raw = diagnostic_row["observation"]
        result = results[table_key[0]]
        resolved = _resolve_condensed_element_reference(
            raw,
            observations=result.observations.values(),
            table_diagnostics=diagnostics,
        )
        assert resolved.identity.standard_pressure_Pa.is_value
        assert resolved.identity.standard_pressure_Pa.value == Decimal("100000")
        assert resolved.identity.reaction.is_value
        assert resolved.identity.formation_elements.is_value
        assert resolved.uncertainty.kind is UncertaintyKind.DERIVED
        expected_pressure_width = abs(
            standard_pressure_delta_g_kJ_per_mol(
                Decimal("1"),
                resolved.identity.temperature_K.value,
                PA_PER_BAR_DEC,
                PA_PER_ATM_DEC,
            )
        )
        assert abs(resolved.uncertainty.value - expected_pressure_width) < Decimal(
            "1e-20"
        )
        assert (
            resolved.provenance["formation_reference_convention"]
            == ReferenceStateConvention.CONDENSED_ELEMENT_REFERENCE.value
        )
        assert (
            resolved.provenance["derived_by"]["standard_pressure_Pa"]
            == "d351_nominal_p0"
        )
        assert resolved.notices
        engine = Engine.INTERNAL_ANALYTICAL
        prediction = EnginePrediction(
            engine=engine,
            channel=ENGINE_CHANNELS[engine],
            execution=Execution(state=ExecutionState.PRODUCED),
            value=diagnostic_row["predicted_kJ_per_mol"],
            unit=QUANTITY_UNITS[Quantity.DELTA_FG],
            coefficient_sources=("janaf-condensed-element-reference",),
            lineage_complete=True,
            identity=resolved.identity,
        )
        context = ScoreContext(
            works=result.works,
            experiments=result.experiments,
            observations=result.observations,
            root=ROOT,
        )
        residual, _candidate = compile_residual(
            resolved,
            engine,
            context=context,
            prediction=prediction,
            condensed_frame_diagnostics=diagnostics,
        )
        assert resolved.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        assert residual.numeric is not None, (table_key, residual.refusal)
        assert residual.numeric.value == (
            diagnostic_row["predicted_kJ_per_mol"] - raw.value.point
        )
        assert any(
            notice.kind is NoticeKind.FIGURE_ONLY
            and notice.reason == "evidence_class:model_derived"
            for notice in residual.notices
        )

    # d-351's family check explicitly retains the 2020 MgO(cr) disagreement:
    # the independent JANAF condensed-frame prediction is -320.338 kJ/mol,
    # while the printed model value is -355.557 kJ/mol. This is numeric evidence,
    # not a reason to tune the source value or turn the row into a refusal.
    mgo_2020 = next(
        row
        for row in results["shornikov-2020-mgo-feo-melts"].observations.values()
        if row.observation_id.endswith(
            "shornikov_2020_mgo_feo_melts_p002_t03_r03_c02"
        )
    )
    result_2020 = results["shornikov-2020-mgo-feo-melts"]
    resolved_mgo_2020 = _resolve_condensed_element_reference(
        mgo_2020,
        observations=result_2020.observations.values(),
        table_diagnostics=diagnostics,
    )
    prediction_2020 = EnginePrediction(
        engine=Engine.INTERNAL_ANALYTICAL,
        channel=ENGINE_CHANNELS[Engine.INTERNAL_ANALYTICAL],
        execution=Execution(state=ExecutionState.PRODUCED),
        value=Decimal("-320.338"),
        unit=QUANTITY_UNITS[Quantity.DELTA_FG],
        coefficient_sources=("janaf-condensed-element-reference",),
        lineage_complete=True,
        identity=resolved_mgo_2020.identity,
    )
    residual_2020, _candidate = compile_residual(
        resolved_mgo_2020,
        Engine.INTERNAL_ANALYTICAL,
        context=ScoreContext(
            works=result_2020.works,
            experiments=result_2020.experiments,
            observations=result_2020.observations,
            root=ROOT,
        ),
        prediction=prediction_2020,
        condensed_frame_diagnostics=diagnostics,
    )
    assert residual_2020.numeric is not None, residual_2020.refusal
    assert abs(residual_2020.numeric.value - Decimal("35.219")) < Decimal("0.001")
    assert any(
        notice.kind is NoticeKind.FIGURE_ONLY
        and notice.reason == "evidence_class:model_derived"
        for notice in residual_2020.notices
    )

    mg_row = next(
        row["observation"]
        for row in diagnostics[(SOURCE_ID, "table:2")]["diagnostic_rows"]
        if row.get("formula") == "Mg"
    )
    resolved_mg = _resolve_condensed_element_reference(
        mg_row,
        observations=observations,
        table_diagnostics=diagnostics,
    )
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.reason == "condensed_element_reference_extrapolated"
        and notice.authority is Authority.EXTRAPOLATED
        for notice in resolved_mg.notices
    )

    failed_table_key = (SOURCE_ID, "table:2")
    failing_observation_ids = {
        row["observation"].observation_id
        for row in diagnostics[failed_table_key]["diagnostic_rows"]
        if "residual_kJ_per_mol" in row
    }
    shifted_observations = [
        replace(
            observation,
            value=replace(
                observation.value,
                point=observation.value.point + Decimal("100"),
            ),
        )
        if observation.observation_id in failing_observation_ids
        else observation
        for observation in observations
    ]
    failed_diagnostics = _condensed_element_reference_table_diagnostics(
        shifted_observations
    )
    assert not failed_diagnostics[failed_table_key]["accepted"]
    frame_gap = next(
        row
        for row in shifted_observations
        if row.source_id == SOURCE_ID
        and row.locator is not None
        and row.locator.table == "2"
        and row.value.kind is ValueKind.POINT
        and row.identity.species.formula == "MgO"
    )
    unresolved = _resolve_condensed_element_reference(
        frame_gap,
        observations=shifted_observations,
        table_diagnostics=failed_diagnostics,
    )
    assert unresolved.identity.reaction.is_unknown
    assert unresolved.identity.formation_elements.is_unknown
    assert not (unresolved.provenance or {}).get("derived_by")


def test_janaf_diagnostic_uncertainty_terms_come_from_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transitions = {
        table_id: table_fusion_transition_for_table_id(table_id)
        for table_id in ("Ca-004", "Mg-003", "Fe-006", "Ti-004")
    }
    assert transitions["Ca-004"]["temperature_K"] == Decimal("1115")
    assert transitions["Ca-004"]["temperature_uncertainty_K"] == Decimal("2")
    assert transitions["Ca-004"]["enthalpy_kJ_per_mol"] == Decimal("8.540")
    assert transitions["Ca-004"]["enthalpy_uncertainty_kJ_per_mol"] == Decimal("0.10")
    assert "p. 706" in transitions["Ca-004"]["enthalpy_locator"]
    assert transitions["Mg-003"]["temperature_K"] == Decimal("923")
    assert transitions["Mg-003"]["temperature_uncertainty_K"] == Decimal("1")
    assert transitions["Mg-003"]["enthalpy_kJ_per_mol"] == Decimal("8.477")
    assert transitions["Mg-003"]["enthalpy_uncertainty_kJ_per_mol"] == Decimal("0.418")
    assert "p. 1531" in transitions["Mg-003"]["enthalpy_locator"]
    assert transitions["Fe-006"]["temperature_K"] == Decimal("1809")
    assert transitions["Fe-006"]["temperature_uncertainty_K"] == Decimal("5")
    assert transitions["Fe-006"]["enthalpy_kJ_per_mol"] == Decimal("13.807")
    assert transitions["Fe-006"]["enthalpy_uncertainty_kJ_per_mol"] == Decimal("0.4")
    assert "p. 1224" in transitions["Fe-006"]["enthalpy_locator"]
    assert transitions["Ti-004"]["temperature_K"] == Decimal("1936")
    assert transitions["Ti-004"]["temperature_uncertainty_K"] == Decimal("10")
    assert transitions["Ti-004"]["enthalpy_kJ_per_mol"] == Decimal("14.146")
    assert transitions["Ti-004"]["enthalpy_uncertainty_kJ_per_mol"] == Decimal("5.0")
    assert "p. 1910" in transitions["Ti-004"]["enthalpy_locator"]

    mg_at_boiling = janaf_condensed_element_gas_diagnostic_uncertainty_terms(
        "Mg", Decimal("2173")
    )
    assert mg_at_boiling["gas_delta_fH_kJ_per_mol"] == Decimal("0.80")
    mg_transition = transitions["Mg-003"]
    temperature_K = Decimal("2173")
    assert mg_at_boiling["fusion_transition_kJ_per_mol"] == (
        abs(
            (Decimal(1) - temperature_K / mg_transition["temperature_K"])
            * mg_transition["enthalpy_uncertainty_kJ_per_mol"]
        )
        + abs(
            mg_transition["enthalpy_kJ_per_mol"]
            * temperature_K
            / mg_transition["temperature_K"] ** 2
            * mg_transition["temperature_uncertainty_K"]
        )
    )
    assert isinstance(mg_at_boiling["gas_interpolation_kJ_per_mol"], Decimal)
    assert mg_at_boiling["extrapolated"] is True
    assert mg_at_boiling["liquid_extrapolation_kJ_per_mol"] > 0

    mg_interpolated_in_liquid_table = (
        janaf_condensed_element_gas_diagnostic_uncertainty_terms(
            "Mg", Decimal("1965")
        )
    )
    assert mg_interpolated_in_liquid_table["extrapolated"] is False
    assert mg_interpolated_in_liquid_table["liquid_interpolation_kJ_per_mol"] > 0

    mg_extrapolation_sanity = janaf_condensed_element_gas_diagnostic_uncertainty_terms(
        "Mg", Decimal("2973")
    )
    ca_extrapolation_sanity = janaf_condensed_element_gas_diagnostic_uncertainty_terms(
        "Ca", Decimal("2973")
    )
    assert Decimal("0.6") < mg_extrapolation_sanity[
        "liquid_extrapolation_kJ_per_mol"
    ] < Decimal("0.8")
    assert Decimal("0.1") < ca_extrapolation_sanity[
        "liquid_extrapolation_kJ_per_mol"
    ] < Decimal("0.2")

    oxygen = janaf_condensed_element_gas_diagnostic_uncertainty_terms(
        "O", Decimal("2173")
    )
    assert oxygen["gas_delta_fH_kJ_per_mol"] == Decimal("0.1")
    assert oxygen["unavailable_reason"] is None

    from simulator.battery.generators import janaf as janaf_owner

    monkeypatch.setattr(
        janaf_owner, "table_fusion_transition_for_table_id", lambda _table_id: None
    )
    missing_transition = (
        janaf_owner.janaf_condensed_element_gas_diagnostic_uncertainty_terms(
            "Mg", Decimal("2173")
        )
    )
    assert missing_transition["fusion_transition_kJ_per_mol"] is None
    assert "no printed fusion transition uncertainty" in missing_transition[
        "unavailable_reason"
    ]


def test_condensed_element_frame_uses_production_predictions_or_typed_refusals(
    tmp_path: Path,
) -> None:
    results, _observations, diagnostics = _migrate_condensed_reference_sources(
        tmp_path
    )
    table_keys = {
        "shornikov-2008-mgo-aluminosilicate-melts": (
            "shornikov-2008-mgo-aluminosilicate-melts",
            "unnumbered:2:1",
        ),
        "shornikov-2011-cao-mgo-alumina-melts": (
            "shornikov-2011-cao-mgo-alumina-melts",
            "table:1",
        ),
        "shornikov-2019-ca-feo-vapor": (
            "shornikov-2019-ca-feo-vapor",
            "table:1",
        ),
        SOURCE_ID: (SOURCE_ID, "table:2"),
        "shornikov-2020-mgo-feo-melts": (
            "shornikov-2020-mgo-feo-melts",
            "table:2",
        ),
    }
    expected = {
        "shornikov-2008-mgo-aluminosilicate-melts": (
            42,
            {"shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r15_c04"},
            {
                ("unsupported", "ellingham-phase-or-polymorph-not-in-fit"): 6,
                ("unsupported", "engine-thermo-does-not-emit"): 23,
                ("identity_incomplete", "solid_liquid_reference_conversion_missing_input"): 10,
                ("identity_mismatch", "identity-mismatch-element-reference-phase"): 1,
                ("method_unknown", "method_unknown"): 1,
            },
        ),
        "shornikov-2011-cao-mgo-alumina-melts": (
            32,
            set(),
            {
                ("unsupported", "ellingham-phase-or-polymorph-not-in-fit"): 7,
                ("unsupported", "engine-thermo-does-not-emit"): 23,
                ("identity_unknown", None): 2,
            },
        ),
        "shornikov-2019-ca-feo-vapor": (
            16,
            {"shornikov_2019_ca_feo_vapor_p003_t01_r05_c04"},
            {
                ("identity_mismatch", "identity-mismatch-element-reference-phase"): 1,
                ("unsupported", "ellingham-phase-or-polymorph-not-in-fit"): 3,
                ("unsupported", "engine-thermo-does-not-emit"): 9,
                ("identity_incomplete", "solid_liquid_reference_conversion_missing_input"): 2,
            },
        ),
        SOURCE_ID: (
            20,
            {"shornikov_2019_mgo_titania_melts_p003_t02_r07_c04"},
            {
                ("identity_incomplete", "solid_liquid_reference_conversion_missing_input"): 4,
                ("unsupported", "engine-thermo-does-not-emit"): 11,
                ("identity_mismatch", "identity-mismatch-element-reference-phase"): 1,
                ("unsupported", "ellingham-phase-or-polymorph-not-in-fit"): 3,
            },
        ),
        "shornikov-2020-mgo-feo-melts": (
            17,
            {"shornikov_2020_mgo_feo_melts_p002_t03_r06_c04"},
            {
                ("identity_mismatch", "identity-mismatch-element-reference-phase"): 1,
                ("unsupported", "ellingham-phase-or-polymorph-not-in-fit"): 3,
                ("unsupported", "engine-thermo-does-not-emit"): 9,
                ("identity_incomplete", "solid_liquid_reference_conversion_missing_input"): 3,
            },
        ),
    }

    for source_id, table_key in table_keys.items():
        result = results[source_id]
        context = ScoreContext(
            works=result.works,
            experiments=result.experiments,
            observations=result.observations,
            root=ROOT,
        )
        row_count = 0
        numeric_ids = set()
        refusals = {}
        for row in result.observations.values():
            if (
                _condensed_element_reference_table_key(row) != table_key
                or not isinstance(row.identity, Identity)
                or quantity_token(row.identity) is not Quantity.DELTA_FG
                or row.value.kind is not ValueKind.POINT
            ):
                continue
            row_count += 1
            residual, _candidate = compile_residual(
                row,
                Engine.INTERNAL_ANALYTICAL,
                context=context,
                condensed_frame_diagnostics=diagnostics,
            )
            if residual.numeric is not None:
                numeric_ids.add(row.observation_id.split("::")[-1])
                assert residual.numeric.value.is_finite()
                assert row.evidence.class_.value is EvidenceClass.MODEL_DERIVED
                assert any(
                    notice.kind is NoticeKind.FIGURE_ONLY
                    and notice.reason == "evidence_class:model_derived"
                    for notice in residual.notices
                )
                continue
            assert residual.refusal is not None, row.observation_id
            key = (
                residual.refusal.reason.value,
                residual.refusal.detail.get("reason"),
            )
            refusals[key] = refusals.get(key, 0) + 1

        expected_rows, expected_numeric_ids, expected_refusals = expected[source_id]
        assert row_count == expected_rows, source_id
        assert numeric_ids == expected_numeric_ids, source_id
        assert refusals == expected_refusals, source_id


def test_condensed_element_frame_requires_discriminating_reproduction(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = _migrate_extract_document(document, tmp_path / EXTRACT.name)
    observations = list(result.observations.values())
    table_key = (SOURCE_ID, "table:2")
    mg_observation_id = "shornikov_2019_mgo_titania_melts_p003_t02_r03_c06"

    # The stable-element frame prints ΔfG(Mg(g)) = 0 at this T. The Mg row
    # therefore contradicts the liquid-element frame; Ti(l) and O(g) have no
    # frame offset and must not rescue the table.
    stable_frame_values = [
        replace(
            row,
            value=replace(row.value, point=Decimal("0")),
        )
        if row.observation_id.endswith(mg_observation_id)
        else row
        for row in observations
    ]
    stable_diagnostics = _condensed_element_reference_table_diagnostics(
        stable_frame_values
    )
    assert not stable_diagnostics[table_key]["accepted"]
    assert [
        row["formula"]
        for row in stable_diagnostics[table_key]["contradicting_rows"]
    ] == ["Mg"]
    ti_row = next(
        row
        for row in stable_diagnostics[table_key]["diagnostic_rows"]
        if row.get("formula") == "Ti"
    )
    assert ti_row["frame_offset_kJ_per_mol"] == Decimal("0")
    assert not ti_row["frame_discriminating"]

    # With only the stable-frame Ti row and shared-reference oxygen check,
    # there is no evidence that can select the condensed-metal frame.
    nondiscriminating_rows = [
        row
        for row in observations
        if _condensed_element_reference_table_key(row) == table_key
        and isinstance(row.identity, Identity)
        and row.identity.species is not None
        and row.identity.species.formula in {"Ti", "O"}
    ]
    absent_diagnostics = _condensed_element_reference_table_diagnostics(
        nondiscriminating_rows
    )
    assert set(absent_diagnostics) == {table_key}
    assert not absent_diagnostics[table_key]["accepted"]
    assert absent_diagnostics[table_key]["accepted_row"] is None
    assert not any(
        row.get("frame_discriminating")
        for row in absent_diagnostics[table_key]["diagnostic_rows"]
    )
