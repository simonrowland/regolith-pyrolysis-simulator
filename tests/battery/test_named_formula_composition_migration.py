from __future__ import annotations

from pathlib import Path

import pytest

from simulator.battery.composition import composition_wt_pct
from simulator.battery.enums import NoticeKind, Quantity
from tests.battery.test_migrate import _migrate_real_extract


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
        assert composition_wt_pct(composition) == pytest.approx({oxide: 100.0})
        located = row.point_conditions["composition"]
        assert located.locator.page == 3
        assert located.locator.published_page == 726
        assert located.locator.table == "1"
        assert located.inference.relation == "oxide_wt_pct_from_named_formula"
        assert any(
            notice.kind is NoticeKind.FALLBACK
            and "source-named pure formula" in notice.reason
            for notice in row.notices
        )


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
            notice.kind is NoticeKind.FALLBACK
            and notice.source == "Na2O.3SiO2"
            for notice in row.notices
        )
