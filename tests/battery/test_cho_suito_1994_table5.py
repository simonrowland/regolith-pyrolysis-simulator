"""Pins for Cho & Suito 1994 Table 5 oxide-from-element-mass-percent maps.

The 20 ACS2/ACS4/CAS5/CAS8 Table 5 rows store oxide mole fractions computed
by stoichiometry from Table 2 printed element mass percent. score.py accepts
that exact derivation and flags it. Rows with no printed composition stay
unknown. Mole-fraction digits are the reviewed maps and must not move.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL
from simulator.battery.enums import (
    Engine,
    ExecutionState,
    NoticeKind,
    Phase,
    RefusalReason,
)
from simulator.battery.score import (
    ScoreContext,
    _printed_point_composition,
    compile_residual,
)
from tests.battery.test_migrate import _migrate_real_extract


REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACT = (
    REPO_ROOT
    / "data"
    / "literature"
    / "extracts"
    / "cho-suito-1994-cas-slag-activities.yaml"
)
SOURCE_ID = "cho-suito-1994-cas-slag-activities"

TABLE5_DERIVED_COMPOSITION_IDS = (
    "cho_suito_1994_t5_acs2_sio2_cn",
    "cho_suito_1994_t5_acs2_alumina_cn",
    "cho_suito_1994_t5_acs2_alumina_intercept",
    "cho_suito_1994_t5_acs2_lime_cn",
    "cho_suito_1994_t5_acs2_lime_intercept",
    "cho_suito_1994_t5_acs4_sio2_cn",
    "cho_suito_1994_t5_acs4_alumina_cn",
    "cho_suito_1994_t5_acs4_alumina_intercept",
    "cho_suito_1994_t5_acs4_lime_cn",
    "cho_suito_1994_t5_acs4_lime_intercept",
    "cho_suito_1994_t5_cas5_sio2_cn",
    "cho_suito_1994_t5_cas5_alumina_cn",
    "cho_suito_1994_t5_cas5_alumina_intercept",
    "cho_suito_1994_t5_cas5_lime_cn",
    "cho_suito_1994_t5_cas5_lime_intercept",
    "cho_suito_1994_t5_cas8_sio2_cn",
    "cho_suito_1994_t5_cas8_alumina_cn",
    "cho_suito_1994_t5_cas8_alumina_intercept",
    "cho_suito_1994_t5_cas8_lime_cn",
    "cho_suito_1994_t5_cas8_lime_intercept",
)

UNKNOWN_COMPOSITION_IDS = {
    "cho_suito_1994_t5_cas9_sio2_cn",
    "cho_suito_1994_t5_cas9_alumina_cn",
    "cho_suito_1994_t5_cas9_alumina_intercept",
    "cho_suito_1994_t5_cas9_lime_cn",
    "cho_suito_1994_t5_cas9_lime_intercept",
    "cho_suito_1994_t5_cs_sio2_cn",
    "cho_suito_1994_t5_cs_lime_cn",
    "cho_suito_1994_t5_ac_alumina_cn",
    "cho_suito_1994_t5_ac_alumina_cn_paren",
    "cho_suito_1994_t5_ac_lime_cn",
    "cho_suito_1994_t5_ca_alumina_cn",
    "cho_suito_1994_t5_ca_alumina_cn_paren",
    "cho_suito_1994_t5_ca_lime_cn",
}

# Reviewed oxide mole fractions already stored on these rows (ror-cho94-t5-r2).
PRINTED_MOLE_FRACTIONS = {
    "ACS2": {
        "CaO": "0.30465979",
        "SiO2": "0.30850866",
        "Al2O3": "0.38683155",
    },
    "ACS4": {
        "CaO": "0.40776673",
        "SiO2": "0.19277449",
        "Al2O3": "0.39945879",
    },
    "CAS5": {
        "CaO": "0.62951029",
        "SiO2": "0.17935483",
        "Al2O3": "0.19113488",
    },
    "CAS8": {
        "CaO": "0.72713400",
        "SiO2": "0.09285355",
        "Al2O3": "0.18001245",
    },
}

# element, oxide, cation count, oxygen count
_OXIDE_STOICHIOMETRY = (
    ("Ca", "CaO", 1, 1),
    ("Si", "SiO2", 1, 2),
    ("Al", "Al2O3", 2, 3),
)


def _table5_rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations") or []
        if (row.get("locator") or {}).get("table") == "5"
        and row.get("type") == "activity_coefficient"
    }


def _slag(observation_id: str) -> str:
    token = observation_id.removeprefix("cho_suito_1994_t5_")
    for slag in PRINTED_MOLE_FRACTIONS:
        if token.startswith(slag.lower() + "_"):
            return slag
    raise AssertionError(observation_id)


def _oxide_sum_wt_percent(assays: list[dict]) -> str:
    """CaO+SiO2+Al2O3 wt% from the mean of the printed element assays."""

    weights = ATOMIC_WEIGHTS_G_PER_MOL
    oxygen = weights["O"]
    means = {
        element: sum(float(assay[element]) for assay in assays) / len(assays)
        for element, _oxide, _cations, _oxygens in _OXIDE_STOICHIOMETRY
    }
    total = 0.0
    for element, _oxide, cations, oxygens in _OXIDE_STOICHIOMETRY:
        molar_mass = cations * weights[element] + oxygens * oxygen
        total += means[element] * molar_mass / (cations * weights[element])
    return f"{total:.6f}"


def _component_strings(composition: dict) -> dict[str, str]:
    value = composition["state"]["value"]
    return {pair[0]: pair[1] for pair in value["components"]}


def test_table5_rows_use_oxide_from_element_mass_percent() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _table5_rows(document)
    derived = {
        observation_id: row
        for observation_id, row in rows.items()
        if observation_id in TABLE5_DERIVED_COMPOSITION_IDS
    }
    assert set(derived) == set(TABLE5_DERIVED_COMPOSITION_IDS)
    assert set(rows) - set(derived) == UNKNOWN_COMPOSITION_IDS
    for observation_id, row in derived.items():
        composition = row["point_conditions"]["composition"]
        assert "inferred" not in composition, observation_id
        inference = composition["inference"]
        assert inference["relation"] == "oxide_from_element_mass_percent"
        assert inference["output_unit"] == "mole_fraction"
        locator = composition["locator"]
        slag = _slag(observation_id)
        assert locator["table"] == "2"
        assert locator["row"] == slag
        assert locator["published_page"] == 178
        locator_input = inference["inputs"][0]
        assert str(locator["page"]) in locator_input
        assert str(locator["published_page"]) in locator_input
        assert str(locator["table"]) in locator_input
        assert slag in locator_input
        assays = row["values"]["slag_element_mass_percent_as_published"]
        oxide_sum = _oxide_sum_wt_percent(assays)
        assert 99.25 <= float(oxide_sum) <= 100.23
        assert inference["inputs"][1] == (
            f"printed_oxide_sum_wt_percent: {oxide_sum}"
        )
        assert _component_strings(composition) == PRINTED_MOLE_FRACTIONS[slag]
    for observation_id in UNKNOWN_COMPOSITION_IDS:
        composition = rows[observation_id]["point_conditions"]["composition"]
        assert composition["state"]["tag"] == "unknown"
        assert "inference" not in composition or not isinstance(
            composition.get("inference"), dict
        )


def test_table5_migration_reaches_temperature_and_scored_composition(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    plain_rows = _table5_rows(document)
    migrated_result = _migrate_real_extract(
        tmp_path, "cho-suito-1994-cas-slag-activities.yaml"
    )
    assert migrated_result.validation is not None
    assert not migrated_result.validation.hard_issues
    migrated = {
        observation_id.rsplit("::", 1)[-1]: observation
        for observation_id, observation in migrated_result.observations.items()
        if observation_id.rsplit("::", 1)[-1] in plain_rows
    }
    assert set(migrated) == set(plain_rows)
    context = ScoreContext(
        works=migrated_result.works,
        experiments=migrated_result.experiments,
        observations=migrated_result.observations,
        extract_review={SOURCE_ID: "draft"},
        hostname="oxide-deriv",
        benches=migrated_result.benches,
    )

    for observation_id, observation in migrated.items():
        temperature = observation.identity.temperature_K
        assert temperature is not None and temperature.is_value, observation_id
        assert temperature.value == Decimal("1873"), observation_id
        point = (observation.point_conditions or {}).get("composition")
        assert point is not None, observation_id
        if observation_id in UNKNOWN_COMPOSITION_IDS:
            assert not point.state.is_value, observation_id
            assert not observation.identity.composition.is_value
            scored, notice = _printed_point_composition(observation)
            assert scored is None and notice is None
            continue
        slag = _slag(observation_id)
        assert point.locator is not None
        assert point.locator.table == "2"
        assert point.locator.page == 2
        assert point.locator.published_page == 178
        assert point.inference is not None
        assert point.inference.relation == "oxide_from_element_mass_percent"
        assert any(slag in str(item) for item in point.inference.inputs)
        oxide_sum = next(
            item.split(":", 1)[1].strip()
            for item in point.inference.inputs
            if str(item).startswith("printed_oxide_sum_wt_percent:")
        )
        assays = plain_rows[observation_id]["values"][
            "slag_element_mass_percent_as_published"
        ]
        assert oxide_sum == _oxide_sum_wt_percent(assays)
        scored, notice = _printed_point_composition(observation)
        assert scored is not None and scored.is_value, observation_id
        assert notice is not None
        assert (
            notice.kind is NoticeKind.COMPOSITION_OXIDE_FROM_ELEMENT_MASS_PERCENT
        )
        assert f"printed_oxide_sum_wt_percent={oxide_sum}" in notice.reason
        expected = {
            oxide: Decimal(text)
            for oxide, text in PRINTED_MOLE_FRACTIONS[slag].items()
        }
        assert scored.value.as_map() == expected
        assert observation.identity.composition.is_value
        assert observation.identity.composition.value.as_map() == expected
        reference_state = observation.identity.reference_state
        assert reference_state is not None and reference_state.is_value
        endmember = reference_state.value.endmember
        assert endmember.phase.value is Phase.CR
        assert endmember.polymorph is not None and endmember.polymorph.is_unknown
        residual, _candidate = compile_residual(
            observation,
            Engine.INTERNAL_ANALYTICAL,
            context=context,
            comparison_ids={observation.observation_id},
        )
        # Crystal with an unstated polymorph refuses before prediction, so
        # the oxide notice is not attached.
        assert residual.execution.state is ExecutionState.NOT_PROBED
        assert residual.refusal is not None
        assert residual.refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
        assert residual.refusal.detail["reason"] == (
            "fusion_source_reference_phase_unprinted"
        )
        assert all(
            item.kind is not NoticeKind.COMPOSITION_OXIDE_FROM_ELEMENT_MASS_PERCENT
            for item in residual.notices
        )
