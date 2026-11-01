"""Render-anchored source pins and isolated migration for Ban-ya et al. (1985)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity, ValueKind
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/banya-hino-yuge-1985-tetsu-feto-sio2-mno-activity.yaml"
RUNS = [
    ("101", "0.082", "5.46", "0.46", "29.14", "63.64", "0.076", "0.100"),
    ("102", "0.083", "6.73", "0.90", "34.26", "57.06", "0.120", "0.100"),
    ("103", "0.082", "9.02", "1.23", "39.09", "50.41", "0.123", "0.100"),
    ("104", "0.083", "4.03", "1.62", "23.88", "71.42", "0.362", "0.101"),
    ("301", "0.246", "18.82", "0.52", "26.06", "54.29", "0.025", "0.299"),
    ("302", "0.245", "22.73", "0.98", "33.16", "42.98", "0.039", "0.299"),
    ("303", "0.245", "28.69", "2.29", "36.57", "33.10", "0.072", "0.299"),
    ("304", "0.245", "32.92", "3.41", "40.73", "23.76", "0.093", "0.298"),
    ("306", "0.244", "25.18", "2.43", "33.01", "40.30", "0.087", "0.297"),
    ("501", "0.409", "35.31", "2.13", "25.76", "36.42", "0.054", "0.499"),
    ("502", "0.412", "39.98", "3.10", "26.81", "29.79", "0.070", "0.500"),
    ("504", "0.408", "53.03", "3.59", "30.83", "13.14", "0.061", "0.496"),
    ("505", "0.409", "58.21", "2.56", "34.41", "4.80", "0.040", "0.499"),
    ("506", "0.411", "56.14", "2.54", "32.48", "8.67", "0.041", "0.499"),
    ("508", "0.411", "29.89", "3.45", "18.97", "47.57", "0.104", "0.500"),
    ("509", "0.411", "57.09", "4.01", "34.44", "4.77", "0.063", "0.501"),
    ("510", "0.411", "30.84", "3.28", "23.05", "43.60", "0.096", "0.499"),
    ("575", "0.410", "45.40", "3.97", "27.74", "22.27", "0.079", "0.499"),
    ("500", "0.413", "43.98", "4.87", "28.96", "23.10", "0.100", "0.502"),
    ("701", "0.576", "54.44", "4.54", "20.52", "20.50", "0.075", "0.702"),
    ("702", "0.575", "60.41", "4.43", "22.66", "12.62", "0.066", "0.700"),
    ("703", "0.574", "66.35", "4.80", "23.41", "4.46", "0.065", "0.699"),
    ("704", "0.574", "54.64", "5.16", "15.28", "24.40", "0.085", "0.700"),
    ("705", "0.577", "52.03", "6.38", "14.83", "26.97", "0.110", "0.700"),
    ("706", "0.575", "52.70", "4.69", "12.55", "29.10", "0.080", "0.700"),
    ("901", "0.738", "77.65", "8.03", "10.37", "3.44", "0.093", "0.899"),
    ("902", "0.739", "76.56", "8.98", "11.86", "2.45", "0.106", "0.901"),
    ("903", "0.738", "76.92", "9.24", "13.00", "1.00", "0.108", "0.899"),
    ("904", "0.736", "76.08", "10.12", "9.03", "5.61", "0.120", "0.896"),
    ("905", "0.741", "76.89", "9.64", "4.70", "8.82", "0.113", "0.902"),
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _rows(document: dict) -> list[dict]:
    return document["species"]["Fe_tO"]["observations"]


def _redox_rows(document: dict) -> list[dict]:
    return document["species"]["Fe"]["observations"]


def _render_pins(document: dict) -> None:
    rows = _rows(document)
    redox_rows = _redox_rows(document)
    assert len(rows) == len(RUNS) == 30, "Table 1 render contains 30 runs"
    assert len(redox_rows) == len(RUNS) == 30, "Table 1 render contains 30 redox values"
    for row, redox_row, expected in zip(rows, redox_rows, RUNS, strict=True):
        run, gas_ratio, feo, fe2o3, sio2, mno, redox_ratio, activity = expected
        values = row["values"]
        assert values["run_number_as_printed"] == run
        assert values["PH2O_over_PH2_as_printed"] == gas_ratio
        assert values["FeO_wt_percent_as_printed"] == feo
        assert values["Fe2O3_wt_percent_as_printed"] == fe2o3
        assert values["SiO2_wt_percent_as_printed"] == sio2
        assert values["MnO_wt_percent_as_printed"] == mno
        assert values["a_FetO_as_printed"] == activity
        assert values["method_class"] == "measured_tabulated"
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
        assert redox_row["values"]["quantity"] == "fe3_fe2_ratio"
        assert redox_row["values"]["method_class"] == "measured_tabulated"
        assert redox_row["values"]["run_number_as_printed"] == run
        assert redox_row["values"]["Fe3_over_Fe2"] == redox_ratio
        assert redox_row["locator"]["page"] == 2
        assert redox_row["locator"]["table"] == "1"
    figure4 = next(
        row
        for row in document["species"]["Fe_tO"]["context"]
        if row["observation_id"] == "banya_hino_yuge_1985_figure_4_context"
    )
    assert figure4["locator"] == {
        "page": 4,
        "pdf_page_index": 3,
        "published_page": 856,
        "figure": "4",
    }


def test_table1_render_pins_cover_every_row_and_detect_mutation() -> None:
    document = _document()
    assert check_all_fidelity_samples_match(document) == []
    _render_pins(document)

    mutated = deepcopy(document)
    mutated["species"]["Fe"]["observations"][8]["values"]["Fe3_over_Fe2"] = "0.088"
    with pytest.raises(AssertionError):
        _render_pins(mutated)


def test_isolated_migration_binds_table_rows_and_keeps_unprinted_pressure_unknown() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 60
    observations = list(result.observations.values())
    print(
        "isolated migration:",
        f"obs={len(observations)}",
        f"hard={len(result.validation.hard_issues)}",
        f"unavailable={sum(o.value.kind is ValueKind.UNAVAILABLE for o in observations)}",
    )
    activity_observations = [o for o in observations if o.identity.quantity.value is Quantity.ACTIVITY]
    redox_observations = [o for o in observations if o.identity.quantity.value is Quantity.FE3_FE2_RATIO]
    assert len(activity_observations) == len(redox_observations) == 30
    for observation in activity_observations:
        assert observation.identity.quantity is not None
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.ACTIVITY
        assert observation.identity.species.formula == "Fe_tO"
        assert observation.identity.species.phase.is_value
        assert observation.identity.species.phase.value is Phase.L
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_TABULATED
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
        observation = next(o for o in activity_observations if f"run_{run}" in o.observation_id)
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

    for row, expected in zip(_redox_rows(_document()), RUNS, strict=True):
        run, _, _, _, _, _, ratio, _ = expected
        observation = next(o for o in redox_observations if f"run_{run}_" in o.observation_id)
        assert observation.identity.quantity.value is Quantity.FE3_FE2_RATIO
        assert observation.identity.species.formula == "Fe"
        assert observation.identity.species.phase.is_value and observation.identity.species.phase.value is Phase.L
        assert observation.value.kind is ValueKind.POINT
        assert str(observation.value.point) == ratio
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_TABULATED
        assert observation.identity.temperature_K.is_value
        assert observation.identity.composition.is_value
        assert observation.point_conditions["composition"].state.is_value
        assert observation.point_conditions["gas_mix"].state.is_value
        assert observation.point_conditions["T_C"].locator.page == 2
        assert "total_pressure_Pa" not in observation.point_conditions
