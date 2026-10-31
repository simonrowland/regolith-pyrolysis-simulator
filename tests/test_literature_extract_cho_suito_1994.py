"""Table 5 activity cells for Cho and Suito (1994), p. 180."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
import pytest

from simulator.battery.enums import Phase, ValueKind
from simulator.battery.migrate import migrate
from tests.helpers.corpus_paths import regolith_corpus_root
from tests.literature_extract_test_utils import isolated_root

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/cho-suito-1994-cas-slag-activities.yaml"

# Table cells transcribed directly from the page 180 render. Each tuple is
# (printed log10(activity), printed standard deviation).
TABLE5_CELLS = {
    "cho_suito_1994_t5_acs2_sio2_cn": ("-0.97", "0.10"),
    "cho_suito_1994_t5_acs4_sio2_cn": ("-1.99", "0.23"),
    "cho_suito_1994_t5_cas5_sio2_cn": ("-3.39", "0.15"),
    "cho_suito_1994_t5_cas8_sio2_cn": ("-4.51", "0.22"),
    "cho_suito_1994_t5_cas9_sio2_cn": ("-2.34", "0.07"),
    "cho_suito_1994_t5_cs_sio2_cn": ("-1.20", "0.17"),
    "cho_suito_1994_t5_acs2_alumina_cn": ("-0.63", "0.16"),
    "cho_suito_1994_t5_acs4_alumina_cn": ("-0.95", "0.40"),
    "cho_suito_1994_t5_cas5_alumina_cn": ("-1.87", "0.41"),
    "cho_suito_1994_t5_cas8_alumina_cn": ("-3.35", "0.66"),
    "cho_suito_1994_t5_cas9_alumina_cn": ("-0.62", "0.13"),
    "cho_suito_1994_t5_ac_alumina_cn": ("-0.57", "0.23"),
    "cho_suito_1994_t5_ac_alumina_cn_paren": ("-0.90", "0.23"),
    "cho_suito_1994_t5_ca_alumina_cn": ("-2.82", "0.41"),
    "cho_suito_1994_t5_ca_alumina_cn_paren": ("-2.62", "0.40"),
    "cho_suito_1994_t5_acs2_lime_cn": ("-1.68", "0.13"),
    "cho_suito_1994_t5_acs4_lime_cn": ("-1.43", "0.13"),
    "cho_suito_1994_t5_cas5_lime_cn": ("-0.11", "0.33"),
    "cho_suito_1994_t5_cas8_lime_cn": ("-0.93", "0.83"),
    "cho_suito_1994_t5_cas9_lime_cn": ("-1.00", "0.49"),
    "cho_suito_1994_t5_cs_lime_cn": ("-1.60", "0.40"),
    "cho_suito_1994_t5_ac_lime_cn": ("-1.08", "0.13"),
    "cho_suito_1994_t5_ca_lime_cn": ("-0.15", "0.44"),
    "cho_suito_1994_t5_acs2_alumina_intercept": ("-0.63", "0.11"),
    "cho_suito_1994_t5_acs4_alumina_intercept": ("-0.99", "0.11"),
    "cho_suito_1994_t5_cas5_alumina_intercept": ("-1.92", "0.12"),
    "cho_suito_1994_t5_cas8_alumina_intercept": ("-3.18", "0.17"),
    "cho_suito_1994_t5_cas9_alumina_intercept": ("-0.76", "0.30"),
    "cho_suito_1994_t5_acs2_lime_intercept": ("-1.69", "0.14"),
    "cho_suito_1994_t5_acs4_lime_intercept": ("-1.44", "0.14"),
    "cho_suito_1994_t5_cas5_lime_intercept": ("-0.067", "0.35"),
    "cho_suito_1994_t5_cas8_lime_intercept": ("-0.82", "0.76"),
    "cho_suito_1994_t5_cas9_lime_intercept": ("-1.37", "0.02"),
}

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


def _table5_rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row.get("locator", {}).get("table") == "5"
        and row.get("type") == "activity_coefficient"
    }


def test_table5_cells_and_column_alignment_are_pinned_to_page_180() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _table5_rows(document)

    assert len(rows) == 33
    assert set(rows) == set(TABLE5_CELLS)
    for observation_id, (printed_log, printed_sd) in TABLE5_CELLS.items():
        row = rows[observation_id]
        assert row["locator"]["page"] == 4
        assert row["locator"]["published_page"] == 180
        assert Decimal(str(row["values"]["printed_log10_activity"])) == Decimal(
            printed_log
        )
        assert Decimal(
            str(row["values"]["printed_log10_standard_deviation"])
        ) == Decimal(printed_sd)
        assert row["uncertainty"] == (
            f"±{printed_sd} standard deviation of log10(a) as printed"
        )
        assert row["values"]["quantity"] == "activity"
        assert row["values"]["temperature_K"] == 1873
        assert row["phase"] == "l"
        assert row["standard_state"] in {
            "pure solid SiO2(s)",
            "relative to the solid standard state; SiO2(s)",
            "relative to the solid standard state; Al2O3(s)",
            "relative to the solid standard state; CaO(s)",
        }

    for observation_id in UNKNOWN_COMPOSITION_IDS:
        composition = rows[observation_id]["point_conditions"]["composition"]
        assert composition["state"]["tag"] == "unknown"
        assert composition["state"]["reason"].startswith("Table 5")


def test_table5_isolated_migration_preserves_values_temperature_and_refusals(
    tmp_path: Path,
) -> None:
    corpus = regolith_corpus_root()
    if not (
        corpus
        / "raw/cho-suito-1994-cas-slag-activities/cho-suito-1994-cas-slag-activities.pdf"
    ).is_file():
        pytest.skip("Cho and Suito source PDF is absent")

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _table5_rows(document)
    result = migrate(isolated_root(tmp_path, document), write=False)
    assert result.validation is not None
    assert not result.validation.hard_issues

    migrated = {
        observation_id.rsplit("::", 1)[-1]: observation
        for observation_id, observation in result.observations.items()
        if "::cho_suito_1994_t5_" in observation_id
    }
    assert len(migrated) == 33
    assert set(migrated) == set(TABLE5_CELLS)
    for observation_id, observation in migrated.items():
        assert observation.value.kind is ValueKind.POINT
        assert observation.identity.temperature_K.value == Decimal("1873")
        assert observation.identity.species.phase.value is Phase.L
        assert "total_pressure_Pa" not in observation.point_conditions
        assert "fO2_Pa" not in observation.point_conditions

    assert not any(
        "::cho_suito_1994_t5_" in (entry.observation_id or "")
        and "quantity" in entry.why
        for entry in result.queue
    )

    assert migrated["cho_suito_1994_t5_cas9_sio2_cn"].value.point == Decimal(
        "0.004571"
    )
    assert migrated["cho_suito_1994_t5_ac_alumina_cn"].value.point == Decimal(
        "0.2692"
    )
    assert migrated["cho_suito_1994_t5_ca_alumina_cn_paren"].value.point == Decimal(
        "0.002399"
    )

    for observation_id in UNKNOWN_COMPOSITION_IDS:
        composition = migrated[observation_id].identity.composition
        assert not composition.is_value
        expected_reason = rows[observation_id]["point_conditions"]["composition"][
            "state"
        ]["reason"]
        assert composition.reason == expected_reason
        condition = migrated[observation_id].point_conditions["composition"]
        assert not condition.state.is_value
        assert condition.locator.page == 4
        assert condition.locator.table == "5"
