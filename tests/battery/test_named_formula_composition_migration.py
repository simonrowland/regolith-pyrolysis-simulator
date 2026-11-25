from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.composition import composition_wt_pct
from simulator.battery.enums import (
    NoticeKind,
    Quantity,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
)
from simulator.battery.score import build_conjuncts
from simulator.battery.validity import GateOutcome
from tests.battery.test_migrate import REPO_ROOT, _migrate_real_extract


def _partial_pressure_rows(result: object, source_id: str) -> list[object]:
    return [
        row
        for row in result.observations.values()
        if row.source_id == source_id
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.P_PARTIAL
    ]


def test_shornikov_2008_pure_oxide_rows_derive_from_the_named_formulas(
    tmp_path: Path,
) -> None:
    source_id = "shornikov-2008-redox-oxide-evaporation"
    result = _migrate_real_extract(tmp_path, f"{source_id}.yaml", write=False)

    rows = [
        row
        for row in _partial_pressure_rows(result, source_id)
        if "::shornikov_2008_t1_" in row.observation_id
    ]
    assert len(rows) == 10
    expected_oxide = {
        "Ca": "CaO",
        "Mg": "MgO",
        "Fe": "FeO",
        "Al": "Al2O3",
        "SiO": "SiO2",
    }
    for row in rows:
        composition = row.identity.composition.value
        oxide = expected_oxide[row.identity.species.formula]
        assert composition.proxy_flag == "calculated_from_named_formula"
        assert composition.proxy_source == oxide
        assert composition.basis == "pure_oxide_system"
        assert composition.components == ((oxide, Decimal("1")),)
        located = row.point_conditions["composition"]
        assert located.locator.page == 3
        assert located.locator.published_page == 726
        assert located.locator.table == "1"
        assert located.inference.relation == "oxide_wt_pct_from_named_formula"
        assert any(
            notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
            and "composition_input_calculated_from_named_formula" in notice.reason
            and oxide in notice.reason
            for notice in row.notices
        )
        if row.identity.species.formula == "Fe":
            assert any(
                "nominal ferrous starting charge; no valence ratio or fO2 inferred"
                in notice.reason
                for notice in row.notices
            )
            assert row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value


def test_kassis_na2o_silicate_recipe_derives_hand_computed_oxide_weights(
    tmp_path: Path,
) -> None:
    source_id = "kassis-frischat-1981-silicate-glass-vapour"
    result = _migrate_real_extract(tmp_path, f"{source_id}.yaml", write=False)

    rows = [
        row
        for row in _partial_pressure_rows(result, source_id)
        if row.identity.species.formula == "Na"
    ]
    assert len(rows) == 4
    # Hand calculation from Na=22.98976928, Si=28.085, O=15.999 g/mol:
    # Na2O=61.97853856 and 3SiO2=180.249 g per formula recipe.
    expected_wt_pct = {"Na2O": 25.5869, "SiO2": 74.4131}
    for row in rows:
        composition = row.identity.composition.value
        assert composition.proxy_flag == "calculated_from_named_formula"
        assert composition.proxy_source == "Na2O.3SiO2"
        assert composition_wt_pct(composition) == pytest.approx(
            expected_wt_pct, abs=0.0001
        )
        located = row.point_conditions["composition"]
        assert located.locator.page == 2
        assert located.locator.published_page == "C-29"
        assert located.locator.section == "Experimental method"
        assert located.inference.relation == "oxide_wt_pct_from_named_formula"
        assert any(
            notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
            and "Na2O.3SiO2" in notice.reason
            for notice in row.notices
        )


def test_jacobson_table_1_printed_charge_is_linked_with_basis_default_flag(
    tmp_path: Path,
) -> None:
    source_id = "kems-139-jacobson-2024"
    result = _migrate_real_extract(tmp_path, f"{source_id}.yaml", write=False)
    rows = [
        row
        for row in _partial_pressure_rows(result, source_id)
        if "jacobson_2024_t1_factsage_2500k_predicted_psat" in row.observation_id
    ]

    assert len(rows) == 21
    row = next(row for row in rows if row.identity.species.formula == "SiO")
    composition = row.identity.composition.value
    assert composition.proxy_flag == "assumed_mass_percent_basis"
    assert "no printed wt% or mol% header" in composition.proxy_source
    assert set(composition_wt_pct(composition) or {}) == {
        "SiO2",
        "TiO2",
        "Al2O3",
        "FeO",
        "MnO",
        "MgO",
        "CaO",
        "Na2O",
        "K2O",
        "P2O5",
    }
    located = row.point_conditions["composition"]
    assert located.locator.page == 19
    assert located.locator.published_page == 18
    assert located.locator.table == "1"
    assert any(
        notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
        and "assumed_mass_percent_basis" in notice.reason
        and "no printed wt% or mol% header" in notice.reason
        for notice in row.notices
    )


def test_named_formula_caveat_does_not_set_blocking_qualification_conjunct(
    tmp_path: Path,
) -> None:
    source_id = "shornikov-2008-redox-oxide-evaporation"
    result = _migrate_real_extract(tmp_path, f"{source_id}.yaml", write=False)
    row = next(
        row
        for row in _partial_pressure_rows(result, source_id)
        if "shornikov_2008_t1_ca_reducing" in row.observation_id
    )

    assert row.identity.composition.value.proxy_flag == "calculated_from_named_formula"
    assert any(
        notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
        and "calculated_from_named_formula" in notice.reason
        for notice in row.notices
    )
    # This pins only the notice's qualification behavior. Source evidence and
    # pressure requirements remain separate gates for overall eligibility.
    conjuncts = build_conjuncts(
        status=ResidualStatus.NO_BAND,
        reference=row,
        candidate=None,
        numeric=None,
        source_relation=SourceRelation.UNKNOWN,
        lineage_complete=False,
        gates=GateOutcome(passed=True),
        extract_review_status=None,
        comparison_ids=set(),
        notices=tuple(
            notice
            for notice in row.notices
            if notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
        ),
    )
    assert conjuncts.no_blocking_qualification


def test_named_rubidium_charge_is_composed_for_all_partial_pressure_rows(
    tmp_path: Path,
) -> None:
    source_id = "kassis-frischat-1981-silicate-glass-vapour"
    result = _migrate_real_extract(tmp_path, f"{source_id}.yaml", write=False)
    rows = [
        row
        for row in _partial_pressure_rows(result, source_id)
        if row.identity.species.formula == "Rb"
    ]

    assert len(rows) == 4
    for row in rows:
        composition = row.identity.composition.value
        assert composition.proxy_flag == "calculated_from_named_formula"
        assert composition.proxy_source == "Rb2O.3SiO2"
        # M(Rb2O)=186.9346 g/mol and 3 M(SiO2)=180.249 g/mol.
        assert composition_wt_pct(composition) == pytest.approx(
            {"Rb2O": 50.91, "SiO2": 49.09}, abs=0.01
        )


def test_natural_murchison_charge_stays_refused_even_with_an_oxide_label(
    tmp_path: Path,
) -> None:
    source_id = "kems-028-yakovlev-1984"
    path = REPO_ROOT / "data" / "literature" / "extracts" / f"{source_id}.yaml"
    extract_doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    observation = next(
        item
        for item in extract_doc["species"]["SiO"]["observations"]
        if item["observation_id"] == "yakovlev_1984_fig1_Murchison_SiO_digitized"
    )
    observation["values"]["points"][0]["oxide_as_printed"] = "SiO2"

    result = _migrate_real_extract(
        tmp_path,
        f"{source_id}.yaml",
        write=False,
        extract_doc=extract_doc,
    )
    row = next(
        row
        for row in _partial_pressure_rows(result, source_id)
        if "yakovlev_1984_fig1_Murchison_SiO_digitized" in row.observation_id
    )
    assert row.identity.composition.is_unknown
    assert row.identity.composition.reason == "numerical_bulk_composition_not_published"
    assert row.point_conditions["composition"].state.is_unknown
    assert row.point_conditions["composition"].state.reason == (
        "numerical_bulk_composition_not_published"
    )


def test_malformed_shornikov_charge_keeps_its_typed_refusal(
    tmp_path: Path,
) -> None:
    source_id = "shornikov-2008-redox-oxide-evaporation"
    path = REPO_ROOT / "data" / "literature" / "extracts" / f"{source_id}.yaml"
    extract_doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    observation = next(
        item
        for item in extract_doc["species"]["Ca"]["observations"]
        if item["observation_id"] == "shornikov_2008_t1_ca_reducing"
    )
    observation["values"]["points"][0]["composition_mol"]["CaO"] = "not-a-number"

    result = _migrate_real_extract(
        tmp_path,
        f"{source_id}.yaml",
        write=False,
        extract_doc=extract_doc,
    )
    row = next(
        row
        for row in _partial_pressure_rows(result, source_id)
        if "shornikov_2008_t1_ca_reducing" in row.observation_id
    )
    assert row.identity.composition.is_unknown
    assert "malformed declared amount" in row.identity.composition.reason
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and "not-a-number" in (issue.detail or "")
        for issue in result.validation.hard_issues
    )


def test_conflicting_shornikov_mole_map_is_a_typed_refusal(
    tmp_path: Path,
) -> None:
    source_id = "shornikov-2008-redox-oxide-evaporation"
    path = REPO_ROOT / "data" / "literature" / "extracts" / f"{source_id}.yaml"
    extract_doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    observation = next(
        item
        for item in extract_doc["species"]["Ca"]["observations"]
        if item["observation_id"] == "shornikov_2008_t1_ca_reducing"
    )
    point = observation["values"]["points"][0]
    point["composition_mol"] = {"MgO": 1.0}
    point["oxide_as_printed"] = "CaO"

    result = _migrate_real_extract(
        tmp_path,
        f"{source_id}.yaml",
        write=False,
        extract_doc=extract_doc,
    )
    row = next(
        row
        for row in _partial_pressure_rows(result, source_id)
        if "shornikov_2008_t1_ca_reducing" in row.observation_id
    )
    assert row.identity.composition.is_unknown
    assert "named formula composition conflict" in row.identity.composition.reason
    assert row.point_conditions["composition"].state.reason == (
        row.identity.composition.reason
    )
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and "CaO conflicts with printed composition_mol" in (issue.detail or "")
        for issue in result.validation.hard_issues
    )


def test_explicit_kassis_row_composition_not_applicable_wins(
    tmp_path: Path,
) -> None:
    source_id = "kassis-frischat-1981-silicate-glass-vapour"
    path = REPO_ROOT / "data" / "literature" / "extracts" / f"{source_id}.yaml"
    extract_doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    observation = next(
        item
        for item in extract_doc["species"]["Na"]["observations"]
        if item["observation_id"] == "kassis_1981_fig2_pna"
    )
    observation["values"]["points"][0]["point_conditions"] = {
        "composition": {
            "state": {
                "tag": "not_applicable",
                "reason": "explicit row-level composition disposition",
            },
            "locator": {
                "page": 2,
                "published_page": "C-29",
                "figure": "2",
            },
        }
    }

    result = _migrate_real_extract(
        tmp_path,
        f"{source_id}.yaml",
        write=False,
        extract_doc=extract_doc,
    )
    row = next(
        row
        for row in _partial_pressure_rows(result, source_id)
        if "kassis_1981_fig2_pna" in row.observation_id
        and row.point_conditions["composition"].state.is_not_applicable
    )
    assert row.point_conditions["composition"].state.reason == (
        "explicit row-level composition disposition"
    )
    assert row.identity.composition.is_not_applicable
