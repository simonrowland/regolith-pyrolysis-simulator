"""Pins the Gail et al. SiO fit, quoted rate refusals, and Table 1 rows."""

import yaml

from simulator.battery.enums import Phase, Quantity, StateTag, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tools.validate_literature_extracts import validate_extract_file
from tests.battery.factories import migrate_one_extract


EXTRACT = REPO_ROOT / "data/literature/extracts/gail-2013-sio-nucleation-silicate-dust.yaml"
RATE_REFUSALS = {
    "gail_2013_nuth_donn_smoke_onset_rate_range",
    "gail_2013_nuth_donn_average_rate_as_printed",
    "gail_2013_sio_nucleation_rate_fit_eq16",
}


def test_gail_sio_extract_migrates_with_pinned_printed_values_and_conditions() -> None:
    assert validate_extract_file(
        EXTRACT,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    block = source["species"]["SiO"]
    source_observations = {
        row["observation_id"]: row for row in block["observations"]
    }

    pressure_fit = source_observations["gail_2013_sio_vapor_pressure_fit_eq4"]
    assert pressure_fit["locator"]["page"] == 3
    assert pressure_fit["locator"]["equation"] == "4"
    assert pressure_fit["values"]["equation_as_printed"] == (
        "P = exp(−T_v/T + S_v)"
    )
    assert pressure_fit["values"]["coefficients_as_printed"] == {
        "T_v": "49 520 ± 1 400 K",
        "S_v": "32.52 ± 0.97",
    }
    assert pressure_fit["units"] == "dyn cm−2"

    onset = source_observations["gail_2013_nuth_donn_smoke_onset_rate_range"]
    assert onset["locator"]["page"] == 4
    assert onset["values"]["rate_interval_as_printed"] == (
        "between 10^8 and 10^10 particles cm−3 s−1"
    )
    average_rate = source_observations[
        "gail_2013_nuth_donn_average_rate_as_printed"
    ]
    assert average_rate["locator"]["page"] == 5
    assert average_rate["values"]["average_rate_as_printed"] == (
        "J_av = 10^(9±1) particles cm−3 s−1"
    )

    nucleation_fit = source_observations["gail_2013_sio_nucleation_rate_fit_eq16"]
    assert nucleation_fit["locator"]["page"] == 5
    assert nucleation_fit["locator"]["equation"] == "16"
    assert nucleation_fit["values"]["fit_coefficients_as_printed"] == {
        "equation_15_a": "(4.40 ± 0.61) × 10^12",
        "equation_15_b": "22.05 ± 3.14",
        "equation_16_exponent_constant": "1.33 ± 3.1",
    }
    assert nucleation_fit["point_conditions"]["total_pressure_Pa"]["state"] == {
        "tag": "unknown",
        "reason": "The printed equation is fitted across the experimental series and has no single total pressure.",
    }

    table = block["context"][0]
    table_rows = table["values"]["rows_as_printed"]
    assert table["locator"]["page"] == 7
    assert table["locator"]["table"] == "1"
    assert len(table_rows) == 13
    assert all(len(row["values_as_printed"]) == 8 for row in table_rows)
    assert table_rows[7]["values_as_printed"] == [
        "1.75", "4.85", "8.35", "16.0", "37.1", "39.5", "64.2", "118"
    ]
    assert table_rows[10]["values_as_printed"] == [
        "19.4", "26.9", "33.9", "47.2", "71.4", "62.7", "75.8", "88.3"
    ]
    assert table_rows[10]["unit_as_printed"] == "%"

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert not result.validation.hard_issues

    observations = {
        row.observation_id.split("::", 1)[-1]: row
        for row in result.observations.values()
    }
    assert set(observations) == set(source_observations)
    assert len(observations) == 4
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in observations.values())
    assert {
        key
        for key, row in observations.items()
        if row.identity.quantity.tag is StateTag.UNKNOWN
    } == RATE_REFUSALS
    assert all(
        row.identity.species.phase.is_value
        and row.identity.species.phase.value is Phase.G
        for row in observations.values()
    )

    for key, row in observations.items():
        assert row.point_conditions is not None
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.tag is StateTag.UNKNOWN
        assert temperature.locator.page == row.locator.page
        assert "total_pressure_Pa" not in row.point_conditions
        assert row.point_conditions["fO2_Pa"].state.tag is StateTag.UNKNOWN
        if key not in RATE_REFUSALS:
            assert row.identity.total_pressure_Pa.tag is StateTag.NOT_APPLICABLE
            assert row.identity.fO2_Pa.tag is StateTag.NOT_APPLICABLE
            assert row.identity.temperature_K.is_unknown
            assert row.identity.quantity.is_value
            assert row.identity.quantity.value is Quantity.P_SAT

    assert all(
        row["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
        for row in source_observations.values()
    )

    assert len(result.context_by_work) == 1
    context_rows = next(iter(result.context_by_work.values()))
    assert len(context_rows) == 1
    assert context_rows[0]["values"]["method_class"] == "model_derived"
    assert context_rows[0]["derived_from"] == [
        "gail_2013_sio_nucleation_rate_fit_eq16"
    ]
    assert len(context_rows[0]["values"]["rows_as_printed"]) == 13
