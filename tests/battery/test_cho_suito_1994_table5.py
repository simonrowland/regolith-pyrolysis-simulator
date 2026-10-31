"""Pins for Cho & Suito 1994 Table 5 derived oxide maps.

The 20 ACS2/ACS4/CAS5/CAS8 Table 5 activity rows store oxide mole fractions
derived from Table 2 elemental mass percent. Those maps must be marked
inferred so score.py::_printed_point_composition returns None.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.score import _printed_point_composition
from tests.battery.test_migrate import _migrate_real_extract


REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACT = (
    REPO_ROOT
    / "data"
    / "literature"
    / "extracts"
    / "cho-suito-1994-cas-slag-activities.yaml"
)

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

INFERENCE = (
    "oxide mole fractions are derived from the Table 2 elemental (Ca), (Si), "
    "(Al) mass percent by oxide stoichiometry"
)


def _table5_observations(doc: dict) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for block in doc.get("species", {}).values():
        if not isinstance(block, dict):
            continue
        for row in block.get("observations") or []:
            oid = row.get("observation_id")
            if oid in TABLE5_DERIVED_COMPOSITION_IDS:
                found[oid] = row
    return found


def test_table5_derived_oxide_maps_are_marked_inferred() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _table5_observations(doc)
    assert set(rows) == set(TABLE5_DERIVED_COMPOSITION_IDS)
    for oid, row in rows.items():
        composition = (row.get("point_conditions") or {}).get("composition")
        assert isinstance(composition, dict), oid
        assert composition.get("inferred") is True, oid
        assert INFERENCE in str(composition.get("inference") or ""), oid


def test_table5_printed_point_composition_is_none_after_inference(
    tmp_path: Path,
) -> None:
    migrated = _migrate_real_extract(
        tmp_path, "cho-suito-1994-cas-slag-activities.yaml"
    )
    rows = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrated.observations.values()
        if row.observation_id.rsplit("::", 1)[-1]
        in TABLE5_DERIVED_COMPOSITION_IDS
    }
    assert set(rows) == set(TABLE5_DERIVED_COMPOSITION_IDS)
    for oid, row in rows.items():
        point_composition, _notice = _printed_point_composition(row)
        assert point_composition is None, oid
        composition = row.identity.composition
        assert composition is not None and composition.is_value, oid
        assert composition.value.basis == (
            "derived_from_table2_element_mass_percent"
        )
        temperature = row.identity.temperature_K
        assert temperature is not None and temperature.is_value
        assert str(temperature.value) == "1873"
        point = (row.point_conditions or {}).get("composition")
        assert point is not None
        assert point.inference is not None
        assert "inferred=true" in point.inference.inputs
