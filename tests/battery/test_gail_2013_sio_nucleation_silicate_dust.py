"""Pins the Gail et al. SiO fit, quoted rate refusals, and Table 1 rows."""

import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity, StateTag, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tools.validate_literature_extracts import validate_extract_file
from tests.battery.factories import migrate_one_extract


EXTRACT = REPO_ROOT / "data/literature/extracts/gail-2013-sio-nucleation-silicate-dust.yaml"
RATE_REFUSALS = {
    "gail_2013_nuth_donn_smoke_onset_rate_range",
    "gail_2013_nuth_donn_average_rate_as_printed",
    "gail_2013_sio_nucleation_rate_fit_eq16",
}
FIGURE_ONLY = {
    "gail_2013_figure_2_vaporisation_coefficient",
    "gail_2013_figure_3_vapor_pressure",
}
PRESSURE_FITS = {
    "gail_2013_sio_vapor_pressure_fit_eq4",
    "gail_2013_ferguson_nuth_vapor_pressure_eq6",
    "gail_2013_schick_vapor_pressure_eq7",
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

    alpha_fit = source_observations["gail_2013_sio_vaporisation_coefficient_fit_eq3"]
    assert alpha_fit["type"] == "alpha"
    assert alpha_fit["locator"]["page"] == 3
    assert alpha_fit["locator"]["equation"] == "3"
    assert alpha_fit["T_range_K"] == [1275, 1525]
    assert alpha_fit["values"]["equation_as_printed"] == (
        "α(T) = α0 exp(−T0/T)"
    )
    assert alpha_fit["values"]["alpha0_as_printed"] == "0.521 ± 0.156"
    assert alpha_fit["values"]["T0_as_printed"] == "3 686 ± 428"
    assert alpha_fit["values"]["orifice_diameters_as_printed"] == (
        "0.50 mm and 0.79 mm"
    )
    assert alpha_fit["derivation"]["raw_inputs"]["state"] == {
        "tag": "value",
        "value": "not_printed",
    }
    assert alpha_fit["derivation"]["raw_inputs"]["locator"]["figure"] == "2"

    pressure_fit = source_observations["gail_2013_sio_vapor_pressure_fit_eq4"]
    assert pressure_fit["locator"]["page"] == 3
    assert pressure_fit["locator"]["equation"] == "4"
    assert pressure_fit["T_range_K"] == [1275, 1525]
    assert pressure_fit["values"]["equation_as_printed"] == (
        "P = exp(−T_v/T + S_v)"
    )
    assert pressure_fit["values"]["method_class"] == "measured_reduced"
    assert pressure_fit["values"]["coefficients_as_printed"] == {
        "T_v": "49 520 ± 1 400 K",
        "S_v": "32.52 ± 0.97",
    }
    assert pressure_fit["uncertainty"]["coefficients_as_printed"] == (
        "T_v = 49 520 ± 1 400 K; S_v = 32.52 ± 0.97"
    )
    assert pressure_fit["derivation"]["raw_inputs"]["state"] == {
        "tag": "value",
        "value": "not_printed",
    }
    assert pressure_fit["derivation"]["raw_inputs"]["locator"]["figure"] == "3"
    assert pressure_fit["values"]["reservoir"]["tag"] == "unknown"
    assert pressure_fit["values"]["reservoir"]["reason"] == (
        "The paper prints solid silicon monoxide (solid SiO), but Phase.CR would "
        "assert crystalline SiO; no phase token represents solid without that assertion."
    )
    assert pressure_fit["values"]["reservoir"]["locator"] == {
        "page": 3,
        "equation": "4",
    }
    assert pressure_fit["units"] == "dyn cm−2"

    ferguson_nuth_fit = source_observations[
        "gail_2013_ferguson_nuth_vapor_pressure_eq6"
    ]
    assert ferguson_nuth_fit["locator"]["page"] == 4
    assert ferguson_nuth_fit["values"]["method_class"] == "quoted_attributed"
    assert ferguson_nuth_fit["values"]["coefficients_as_printed"] == {
        "T_v": "40 850 ± 1 270 K",
        "S_v": "32.93 ± 0.90",
    }
    schick_fit = source_observations["gail_2013_schick_vapor_pressure_eq7"]
    assert schick_fit["locator"]["page"] == 4
    assert schick_fit["values"]["method_class"] == "quoted_attributed"
    assert schick_fit["values"]["equation_as_printed"] == (
        "pvap = exp(26.66 − 25670/T)"
    )

    assert set(source_observations) >= FIGURE_ONLY
    for figure_id in FIGURE_ONLY:
        figure = source_observations[figure_id]
        assert figure["type"] in {"alpha", "psat_series"}
        assert figure["values"]["method_class"] == "figure_only"
        assert figure["values"]["digitisation"] == "deferred to a figure pass"

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
    assert len(observations) == 9
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
        if key in FIGURE_ONLY:
            continue
        assert row.point_conditions is not None
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.tag is StateTag.UNKNOWN
        assert temperature.locator.page == row.locator.page
        assert "total_pressure_Pa" not in row.point_conditions
        assert row.point_conditions["fO2_Pa"].state.tag is StateTag.UNKNOWN
        if key in PRESSURE_FITS:
            assert row.identity.total_pressure_Pa.tag is StateTag.NOT_APPLICABLE
            assert row.identity.fO2_Pa.tag is StateTag.NOT_APPLICABLE
            assert row.identity.temperature_K.is_unknown
            assert row.identity.quantity.is_value
            assert row.identity.quantity.value is Quantity.P_SAT
        elif key == "gail_2013_sio_vaporisation_coefficient_fit_eq3":
            assert row.identity.quantity.is_value
            assert row.identity.quantity.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA

    alpha_observation = observations[
        "gail_2013_sio_vaporisation_coefficient_fit_eq3"
    ]
    assert alpha_observation.evidence.class_.is_value
    assert alpha_observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert alpha_observation.derivation is not None
    assert alpha_observation.derivation.has_located_unprinted_raw_inputs
    assert alpha_observation.derivation.raw_inputs is not None
    assert alpha_observation.derivation.raw_inputs.state.value.value == "not_printed"
    assert alpha_observation.derivation.raw_inputs.locator.figure == "2"
    pressure_observation = observations["gail_2013_sio_vapor_pressure_fit_eq4"]
    assert pressure_observation.evidence.class_.is_value
    assert pressure_observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert pressure_observation.derivation is not None
    assert pressure_observation.derivation.has_located_unprinted_raw_inputs
    assert pressure_observation.derivation.raw_inputs is not None
    assert pressure_observation.derivation.raw_inputs.state.value.value == "not_printed"
    assert pressure_observation.derivation.raw_inputs.locator.figure == "3"
    for comparison_id in PRESSURE_FITS - {"gail_2013_sio_vapor_pressure_fit_eq4"}:
        comparison = observations[comparison_id]
        assert comparison.evidence.class_.is_value
        assert comparison.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert comparison.identity.quantity.is_value
        assert comparison.identity.quantity.value is Quantity.P_SAT

    assert pressure_observation.identity.reservoir is not None
    assert pressure_observation.identity.reservoir.is_unknown
    assert "solid silicon monoxide" in pressure_observation.identity.reservoir.reason
    assert "Phase.CR would assert crystalline SiO" in pressure_observation.identity.reservoir.reason
    assert "no reservoir mapped from source" not in pressure_observation.identity.reservoir.reason

    assert all(
        row["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
        for key, row in source_observations.items()
        if key not in FIGURE_ONLY
    )

    assert len(result.context_by_work) == 1
    context_rows = next(iter(result.context_by_work.values()))
    assert len(context_rows) == 1
    assert context_rows[0]["values"]["method_class"] == "model_derived"
    assert context_rows[0]["derived_from"] == [
        "gail_2013_sio_nucleation_rate_fit_eq16"
    ]
    assert len(context_rows[0]["values"]["rows_as_printed"]) == 13
