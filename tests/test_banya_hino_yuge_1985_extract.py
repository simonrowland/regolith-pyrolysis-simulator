"""Render-anchored source pins and isolated migration for Ban-ya et al. (1985)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/banya-hino-yuge-1985-tetsu-feto-sio2-mno-activity.yaml"
RUNS = [
    ("101", "0.082", "5.46", "0.46", "29.14", "63.64", "0.100"),
    ("102", "0.083", "6.73", "0.90", "34.26", "57.06", "0.100"),
    ("103", "0.082", "9.02", "1.23", "39.09", "50.41", "0.100"),
    ("104", "0.083", "4.03", "1.62", "23.88", "71.42", "0.101"),
    ("301", "0.246", "18.82", "0.52", "26.06", "54.29", "0.299"),
    ("302", "0.245", "22.73", "0.98", "33.16", "42.98", "0.299"),
    ("303", "0.245", "28.69", "2.29", "36.57", "33.10", "0.299"),
    ("304", "0.245", "32.92", "3.41", "40.73", "23.76", "0.298"),
    ("306", "0.244", "25.18", "2.43", "33.01", "40.30", "0.297"),
    ("501", "0.409", "35.31", "2.13", "25.76", "36.42", "0.499"),
    ("502", "0.412", "39.98", "3.10", "26.81", "29.79", "0.500"),
    ("504", "0.408", "53.03", "3.59", "30.83", "13.14", "0.496"),
    ("505", "0.409", "58.21", "2.56", "34.41", "4.80", "0.499"),
    ("506", "0.411", "56.14", "2.54", "32.48", "8.67", "0.499"),
    ("508", "0.411", "29.89", "3.45", "18.97", "47.57", "0.500"),
    ("509", "0.411", "57.09", "4.01", "34.44", "4.77", "0.501"),
    ("510", "0.411", "30.84", "3.28", "23.05", "43.60", "0.499"),
    ("575", "0.410", "45.40", "3.97", "27.74", "22.27", "0.499"),
    ("500", "0.413", "43.98", "4.87", "28.96", "23.10", "0.502"),
    ("701", "0.576", "54.44", "4.54", "20.52", "20.50", "0.702"),
    ("702", "0.575", "60.41", "4.43", "22.66", "12.62", "0.700"),
    ("703", "0.574", "66.35", "4.80", "23.41", "4.46", "0.699"),
    ("704", "0.574", "54.64", "5.16", "15.28", "24.40", "0.700"),
    ("705", "0.577", "52.03", "6.38", "14.83", "26.97", "0.700"),
    ("706", "0.575", "52.70", "4.69", "12.55", "29.10", "0.700"),
    ("901", "0.738", "77.65", "8.03", "10.37", "3.44", "0.899"),
    ("902", "0.739", "76.56", "8.98", "11.86", "2.45", "0.901"),
    ("903", "0.738", "76.92", "9.24", "13.00", "1.00", "0.899"),
    ("904", "0.736", "76.08", "10.12", "9.03", "5.61", "0.896"),
    ("905", "0.741", "76.89", "9.64", "4.70", "8.82", "0.902"),
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _rows(document: dict) -> list[dict]:
    return document["species"]["Fe_tO"]["observations"]


def _render_pins(document: dict) -> None:
    rows = _rows(document)
    assert len(rows) == len(RUNS) == 30, "Table 1 render contains 30 runs"
    for row, expected in zip(rows, RUNS, strict=True):
        run, gas_ratio, feo, fe2o3, sio2, mno, activity = expected
        values = row["values"]
        assert values["run_number_as_printed"] == run
        assert values["PH2O_over_PH2_as_printed"] == gas_ratio
        assert values["FeO_wt_percent_as_printed"] == feo
        assert values["Fe2O3_wt_percent_as_printed"] == fe2o3
        assert values["SiO2_wt_percent_as_printed"] == sio2
        assert values["MnO_wt_percent_as_printed"] == mno
        assert values["a_FetO_as_printed"] == activity
        assert values["method_class"] == "quoted_attributed"
        assert values["CaO_wt_percent_as_printed"]["state"] == {
            "tag": "unknown",
            "reason": "not_reported_in_Table_1",
        }
        assert values["MgO_wt_percent_as_printed"]["state"] == {
            "tag": "unknown",
            "reason": "not_reported_in_Table_1",
        }
        assert values["Fe2O3_wt_percent_as_printed"] == fe2o3
        assert row["locator"]["page"] == 2
        assert row["locator"]["table"] == "1"
        assert row["standard_state"]["state"]["value"]["endmember"]["formula"] == "Fe_tO"
        assert row["standard_state"]["state"]["value"]["endmember"]["phase"]["value"] == "l"


def test_table1_render_pins_cover_every_row_and_detect_mutation() -> None:
    document = _document()
    assert check_all_fidelity_samples_match(document) == []
    _render_pins(document)

    mutated = deepcopy(document)
    mutated["species"]["Fe_tO"]["observations"][8]["values"]["SiO2_wt_percent_as_printed"] = "33.02"
    with pytest.raises(AssertionError):
        _render_pins(mutated)


def test_isolated_migration_binds_table_rows_and_keeps_unprinted_pressure_unknown() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 30
    observations = list(result.observations.values())
    print(
        "isolated migration:",
        f"obs={len(observations)}",
        f"hard={len(result.validation.hard_issues)}",
        f"unavailable={sum(o.value.kind is ValueKind.UNAVAILABLE for o in observations)}",
    )
    for observation in observations:
        assert observation.identity.quantity is not None
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.ACTIVITY
        assert observation.identity.species.formula == "Fe_tO"
        assert observation.identity.species.phase.is_value
        assert observation.identity.species.phase.value is Phase.L
        assert observation.identity.temperature_K is not None
        assert observation.identity.temperature_K.is_value
        assert observation.identity.composition is not None
        assert observation.identity.composition.is_value
        assert observation.value.kind is ValueKind.POINT
        assert "total_pressure_Pa" not in observation.point_conditions
        assert "fO2_Pa" not in observation.point_conditions
        assert observation.point_conditions["gas_mix"].state.is_value
        assert observation.point_conditions["T_C"].locator.page == 2
        assert {"composition", "temperature_K"} <= profile_for(observation.identity).required

    for run in ("101", "306", "905"):
        observation = next(o for o in observations if f"run_{run}" in o.observation_id)
        print(
            run,
            "required=",
            sorted(profile_for(observation.identity).required),
            "temperature_K=",
            observation.identity.temperature_K,
            "composition=",
            observation.identity.composition,
            "fO2_Pa=",
            observation.identity.fO2_Pa,
            "total_pressure_Pa=",
            observation.point_conditions.get("total_pressure_Pa"),
        )
