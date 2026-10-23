"""Independent render pins for the measured Panda (2019) context tables."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/panda-2019-ten-oxide-thermodynamics.yaml"


def _records(extract: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in extract["species"].values()
        for row in species.get("context", [])
    }


def _assert_render_pins(extract: dict) -> None:
    records = _records(extract)
    table53 = records["panda2019_table53_fe_ti_metallic_cation_compositions"]["values"]
    assert len(table53["rows"]) == 8
    assert table53["rows"][0]["sample"] == "FT1"
    assert table53["rows"][0]["phases"][0]["Fe"] == "0.428 ± 0.012"
    assert table53["rows"][5]["phases"][1]["Fe"] == "0.836± 0.002"
    assert table53["rows"][7]["temperature_C"] == "1350"

    table63_first = records["panda2019_table63_mn_ti_metallic_cation_compositions"]["values"]
    table63_continued = records["panda2019_table63_continued_mn_ti_metallic_cation_compositions"]["values"]
    assert [row["sample"] for row in table63_first["starting_samples_and_conditions"]] == ["MT1", "MT2", "MT3", "MT4"]
    assert [row["sample"] for row in table63_continued["starting_samples_and_conditions"]] == ["MT5", "MT6", "MT7", "MT8", "MT9", "MT10"]
    assert table63_first["phase_rows"][2]["Mn"] == "0.796 ± 0.004"
    assert table63_continued["phase_rows"][6]["Ti"] == "0.207 ± 0.008"

    table64 = records["panda2019_table64_mno_activity_measurement_conditions"]["values"]["rows"]
    assert len(table64) == 6
    assert table64[0]["log_pO2_bar"] == "-12.137"
    assert table64[2]["log_pO2_bar"] == "-6 to -7"

    table94 = records["panda2019_table94_mg_mn_ti_metallic_cation_compositions"]["values"]["rows"]
    assert len(table94) == 12
    assert sum(len(row["phases"]) for row in table94) == 25
    assert table94[0]["starting_composition_mole_fraction"] == ["0.50", "0.20", "0.30"]
    assert table94[0]["temperature_C"] == "1450"
    assert table94[4]["temperature_C"] == "1400"
    assert table94[7]["phases"][0]["Mg"] == "0.260 ± 0.056"


def test_panda_tables_match_independent_600_dpi_transcription() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(extract)


def test_panda_render_pins_reject_an_extract_cell_mutation() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed = deepcopy(extract)
    table94 = _records(changed)["panda2019_table94_mg_mn_ti_metallic_cation_compositions"]
    table94["values"]["rows"][0]["starting_composition_mole_fraction"][0] = "0.60"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed)


def test_panda_extract_migrates_quoted_coefficients_and_context() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    observations = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    assert set(observations) == {
        "panda2019_quoted_gamma_mn_ag_ito_1723k",
        "panda2019_quoted_gamma_mn_ag_jung_1723k",
    }
    for observation_id, expected in {
        "panda2019_quoted_gamma_mn_ag_ito_1723k": Decimal("0.395"),
        "panda2019_quoted_gamma_mn_ag_jung_1723k": Decimal("0.596"),
    }.items():
        row = observations[observation_id]
        assert row.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
        assert row.identity.species.formula == "Mn"
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1723")
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == expected
    context_ids = {
        row["observation_id"]
        for rows in migrator.result.context_by_work.values()
        for row in rows
    }
    assert len(context_ids) == 15
    assert "panda2019_table53_fe_ti_metallic_cation_compositions" in context_ids
    assert "panda2019_table64_mno_activity_measurement_conditions" in context_ids
    assert "panda2019_figure66_mno_activity" in context_ids
    assert "panda2019_context_mn_ti_spinels_solubility" in context_ids
    assert "panda2019_context_mn_ti_ilmenite_solubility_and_dissociation" in context_ids
