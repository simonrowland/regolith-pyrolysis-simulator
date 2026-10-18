"""Tests for literature extract store (t-508): validator + extract_merge + fidelity.

Each acceptance claim has a red-under-reversion pin. Null-hypotheses are stated
in the test docstrings where a prior defect is being locked out.

t-510 fidelity gate:
* ENFORCED_FOR_NEW sample presence (pre-policy allowlist is shrink-only)
* parameterized match of every fidelity sample against extract content
* mutation of a pinned value must go RED
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import math
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.yaml_cache import load_cached_safe_yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"
FIDELITY_POLICY = EXTRACTS / "_fidelity_pre_policy_allowlist.yaml"
FIDELITY_GRADUATION_LEDGER = EXTRACTS / "_fidelity_graduation_ledger.yaml"
RETIRED_DUPLICATE_OBSERVATION_ALIASES = {
    "costa-jacobson-2015::costa_jacobson_2015_fe_olivine_kems": (
        "kems-007-costa-2015::costa_2015_fe_vaporization_coefficient_alpha_digitized"
    ),
}
RETIRED_DUPLICATE_SOURCE_IDS = {
    "fedkin-grossman-ghiorso-2006",
    "sossi-et-al-2019",
    "pound-1972-cr-langmuir-knudsen",
    "pound-1972-mccabe-cr",
    "ref-016-sio-kems-1700-2000k",
    "safarian-engh-2013-si-pure-langmuir",
}
RETIRED_DUPLICATE_RESIDUAL_KEY_ALIASES = {
    "fedkin_2006_table3_mg_hashimoto_langmuir:T=1973":
        "fedkin_2006_mg_hashimoto_langmuir_table3:T=1973",
}

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import extract_merge as em  # noqa: E402
import migrate_pilot_extracts as mig  # noqa: E402
import validate_literature_extracts as vle  # noqa: E402
from simulator.battery import migrate as battery_migrate  # noqa: E402
from simulator.battery.waypoints import oxygen_condition  # noqa: E402


class _DuplicateRejectingLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


# Independent full-matrix literals transcribed from Table 1's page image,
# published p. 1063. Each tuple is sample number, material, then printed
# −log fO2 (atm) at 1000, 1050, 1100, 1150 and 1200 °C.
_SATO_1973_TABLE_PINS = {
    "sato73_12009_35_bulk_rock": (
        "12009,35", "bulk rock", ("15.6", "14.8", "14.0", "13.4", "12.8")
    ),
    "sato73_12009_35_olivine": (
        "12009,35", "olivine", ("15.5", "14.6", "13.9", "13.2", "12.4")
    ),
    "sato73_12009_35_groundmass": (
        "12009,35", "groundmass", ("16.0", "15.0", "14.3", "13.6", "12.8")
    ),
    "sato73_12009_35_bulk_plus_fe": (
        "12009,35", "bulk + Fe", ("16.0", "15.2", "14.4", "13.7", "13.0")
    ),
    "sato73_12022_36_bulk_plus_fe": (
        "12022,36", "bulk + Fe", ("15.7", "14.8", "14.0", "13.4", "12.7")
    ),
    "sato73_12053_89_bulk_rock": (
        "12053,89", "bulk rock", ("15.4", "14.6", "13.8", "13.0", "12.3")
    ),
    "sato73_12053_89_pyroxenes": (
        "12053,89", "pyroxenes", ("15.3", "14.4", "13.6", "12.8", "12.1")
    ),
    "sato73_12053_89_bulk_plus_fe": (
        "12053,89", "bulk + Fe", ("16.0", "15.2", "14.5", "13.8", "13.1")
    ),
    "sato73_14310_195_bulk_rock": (
        "14310,195", "bulk rock", ("15.4", "14.5", "13.8", "13.3", "11.5")
    ),
    "sato73_14310_207_bulk_rock": (
        "14310,207", "bulk rock", ("15.3", "14.5", "13.9", "13.2", "10.9")
    ),
    "sato73_14321_105_bulk_rock": (
        "14321,105", "bulk rock", ("15.6", "14.6", "13.9", "13.0", "12.3")
    ),
    "sato73_15058_81_bulk_rock": (
        "15058,81", "bulk rock", ("15.7", "14.9", "14.0", "13.3", "12.6")
    ),
    "sato73_15058_88_bulk_rock": (
        "15058,88", "bulk rock", ("15.7", "14.9", "14.0", "13.3", "12.5")
    ),
    "sato73_15595_25_bulk_rock": (
        "15595,25", "bulk rock", ("15.7", "14.9", "14.1", "13.2", "12.4")
    ),
    "sato73_15595_25_bulk_plus_fe": (
        "15595,25", "bulk + Fe", ("16.0", "15.1", "14.2", "13.4", "12.7")
    ),
}
_SATO_1973_TABLE_TEMPERATURES_C = ("1000", "1050", "1100", "1150", "1200")
_SATO_1973_SAMPLE_FORMS = {
    observation_id: (
        f"Lunar sample {sample}; bulk rock plus iron foil"
        if material == "bulk + Fe"
        else f"Lunar sample {sample}; {material}"
    )
    for observation_id, (sample, material, _logs) in _SATO_1973_TABLE_PINS.items()
}

_OHTA_SUITO_1995_TABLE_II_ATTRIBUTIONS = {
    **{
        observation_id: "Table II, reference [4] (Rein and Chipman, 1965)"
        for observation_id in (
            "ohta_suito_1995_table2_ac_alumina_activity",
            "ohta_suito_1995_table2_ca_alumina_activity",
            "ohta_suito_1995_table2_acs2_alumina_activity",
            "ohta_suito_1995_table2_acs4_alumina_activity",
            "ohta_suito_1995_table2_acs5_alumina_activity",
            "ohta_suito_1995_table2_cas5_alumina_activity",
            "ohta_suito_1995_table2_cas9_alumina_activity",
            "ohta_suito_1995_table2_cas10_alumina_activity",
            "ohta_suito_1995_table2_cas11_alumina_activity",
            "ohta_suito_1995_table2_acs2_silica_activity",
            "ohta_suito_1995_table2_acs4_silica_activity",
            "ohta_suito_1995_table2_acs5_silica_activity",
            "ohta_suito_1995_table2_cas5_silica_activity",
            "ohta_suito_1995_table2_cas8_silica_activity",
            "ohta_suito_1995_table2_cas9_silica_activity",
            "ohta_suito_1995_table2_cas10_silica_activity",
            "ohta_suito_1995_table2_cas11_silica_activity",
        )
    },
    "ohta_suito_1995_table2_cas8_alumina_activity": (
        "Table II, reference [1] (Lee and Suito, 1994)"
    ),
    "ohta_suito_1995_table2_cs_silica_activity": (
        "Table II, reference [7] (Inoue and Suito, 1992)"
    ),
}
_OHTA_SUITO_1995_TABLE_III = """i | j | e_i^j (r_i^j)
Al | Al | 0.045 (-0.001)
Al | Si | 0.056
Al | O | -6.6
Si | Al | 0.058
Si | Si | 0.11 (-0.0021)
Si | O | -0.23
Si | Mn | 0.002
O | Al | -3.9 (1.7)
O | Si | -0.131
O | O | -0.20
O | Mn | -0.021
Mn | Si | 0
Mn | O | -0.083
Mn | Mn | 0"""


def _assert_ohta_suito_1995_review_pins(extract: dict) -> None:
    rows = {
        row["observation_id"]: row
        for species in ("Al2O3", "SiO2")
        for row in extract["species"][species]["observations"]
    }
    assert set(rows) == set(_OHTA_SUITO_1995_TABLE_II_ATTRIBUTIONS)
    for observation_id, attribution in _OHTA_SUITO_1995_TABLE_II_ATTRIBUTIONS.items():
        row = rows[observation_id]
        assert "regime" not in row
        assert row["values"]["attribution"] == attribution

    procedure = next(
        row for row in extract["species"]["MnO"]["context"]
        if row["observation_id"] == "ohta_suito_1995_experimental_conditions"
    )
    assert procedure["values"]["quench_method_as_printed"] == (
        "the crucible was pulled out of the furnace and quenched rapidly in a He "
        "gas stream, followed by water quenching"
    )

    table_iii_observation_id = "ohta_suito_1995_table3_interaction_parameters"
    expected_table_iii_locator = {
        "page": 298, "published_page": 298, "pdf_page_index": 3, "table": "III"
    }
    table_iii_locators = [
        row["locator"]
        for species in extract["species"].values()
        for row in species.get("context", [])
        if row.get("locator", {}).get("table") == "III"
    ]
    table_iii_locators.extend(
        sample["locator"]
        for sample in extract["fidelity_samples"]
        if sample.get("observation_id") == table_iii_observation_id
    )
    assert table_iii_locators
    assert all(locator == expected_table_iii_locator for locator in table_iii_locators)
    table_iii = next(
        row for row in extract["species"]["MnO"]["context"]
        if row["observation_id"] == table_iii_observation_id
    )
    assert table_iii["values"]["table_transcription"] == _OHTA_SUITO_1995_TABLE_III

    table_i = next(
        row for row in extract["species"]["MnO"]["context"]
        if row["observation_id"] == "ohta_suito_1995_table1_compositions_and_equilibrium_analyses"
    )
    assert table_i["units"] == (
        "mass percent except soluble Al, insoluble Al, and O (mass ppm); "
        "dash as printed means blank/not reported"
    )


def test_ohta_suito_1995_source_pins_reject_each_review_regression() -> None:
    source = EXTRACTS / "ohta-suito-1995-mno-activity-slags.yaml"
    extract = yaml.load(source.read_text(), Loader=_DuplicateRejectingLoader)
    _assert_ohta_suito_1995_review_pins(extract)
    migrator = battery_migrate.Migrator(
        root=REPO_ROOT,
        index={source.stem: dict(extract["source"], source_id=source.stem)},
        aliases={},
    )
    migrator._migrate_extract(source)
    migrator.finalize()
    assert len(migrator.result.experiments) == 11
    assert all(
        experiment.method.tag.value == "unknown"
        and experiment.method.reason == "source does not state method"
        for experiment in migrator.result.experiments.values()
    )

    mutations = (
        "quoted_method",
        "cas8_alumina_reference",
        "table_iii_indices",
        "table_iii_temperature_fidelity_locator",
        "table_i_units",
    )
    for mutation in mutations:
        extract = yaml.load(source.read_text(), Loader=_DuplicateRejectingLoader)
        context = extract["species"]["MnO"]["context"]
        if mutation == "quoted_method":
            extract["species"]["Al2O3"]["observations"][0]["regime"] = (
                "quench_equilibration"
            )
        elif mutation == "cas8_alumina_reference":
            extract["species"]["Al2O3"]["observations"][6]["values"][
                "attribution"
            ] = "Table II, reference [4] (Rein and Chipman, 1965)"
        elif mutation == "table_iii_indices":
            table = next(
                row for row in context
                if row["observation_id"] == "ohta_suito_1995_table3_interaction_parameters"
            )
            table["values"]["table_transcription"] = table[
                "values"
            ]["table_transcription"].replace("e_i^j (r_i^j)", "e_j^i")
        elif mutation == "table_iii_temperature_fidelity_locator":
            sample = next(
                sample for sample in extract["fidelity_samples"]
                if sample["observation_id"] == "ohta_suito_1995_table3_interaction_parameters"
                and sample["field"] == "temperature_K"
            )
            sample["locator"] = {
                "page": 297, "published_page": 297, "pdf_page_index": 2, "table": "III"
            }
        else:
            table = next(
                row for row in context
                if row["observation_id"] == "ohta_suito_1995_table1_compositions_and_equilibrium_analyses"
            )
            table["units"] = (
                "mass percent except metal oxygen (mass ppm); "
                "dash as printed means blank/not reported"
            )
        with pytest.raises(AssertionError):
            _assert_ohta_suito_1995_review_pins(extract)


def _assert_sato_1973_table_pins(extract: dict) -> None:
    bench = extract["benches"][0]
    assert bench["temperature_measurement"]["increments"]["state"]["value"] == (
        "About 20 °C during heating and about 40 °C during cooling, "
        "after equilibration periods of about 1 h"
    )
    context = extract["species"]["O2"]["context"][0]["values"]
    assert context["measurement_atmosphere_as_printed"] == (
        "Furnace atmosphere was N2 diluent with about 1% CO2 and variable H2, "
        "servo-controlled to keep its fO2 within about 0.1 log fO2 unit of the sample."
    )
    assert context["iron_wustite_buffer_relation_as_printed"] == (
        "log fO2 = 6.824 − 27470/T"
    )
    assert context["iron_wustite_buffer_temperature_range_as_printed"] == "850–1300 °C"
    assert context["iron_wustite_buffer_temperature_unit"] == "T in K (absolute temperature)"

    rows = {
        row["observation_id"]: row
        for row in extract["species"]["O2"]["observations"]
    }
    assert set(rows) == set(_SATO_1973_TABLE_PINS)
    for observation_id, (sample, material, printed_logs) in _SATO_1973_TABLE_PINS.items():
        row = rows[observation_id]
        assert row["values"]["sample_no_as_printed"] == sample
        assert row["values"]["material_as_printed"] == material
        points = row["values"]["points"]
        assert tuple(point["temperature_C_as_printed"] for point in points) == (
            _SATO_1973_TABLE_TEMPERATURES_C
        )
        assert tuple(point["neg_log_fO2_as_printed"] for point in points) == printed_logs
        for point, printed_log in zip(points, printed_logs, strict=True):
            temperature = point["temperature_C_as_printed"]
            expected_pa = Decimal("101325") * (Decimal(10) ** -Decimal(printed_log))
            relative_error = abs(
                Decimal(point["fugacity_Pa"]) / expected_pa - Decimal(1)
            )
            assert relative_error <= Decimal("1e-6"), (observation_id, temperature)


def test_sato_1973_independently_pins_all_table_1_cells_and_equation() -> None:
    source = EXTRACTS / "sato-hickling-mclane-1973-lunar-fo2.yaml"
    extract = yaml.load(source.read_text(), Loader=_DuplicateRejectingLoader)
    _assert_sato_1973_table_pins(extract)


@pytest.mark.parametrize(
    "mutation",
    [
        "pressure_x10",
        "printed_log_plus_001",
        "coordinated_interior_log",
        "coordinated_interior_temperature",
        "coordinated_sample_identity",
        "eq5_coefficient",
        "eq5_range",
    ],
)
def test_sato_1973_source_pins_reject_mutations(mutation: str) -> None:
    source = EXTRACTS / "sato-hickling-mclane-1973-lunar-fo2.yaml"
    extract = yaml.load(source.read_text(), Loader=_DuplicateRejectingLoader)
    rows = extract["species"]["O2"]["observations"]
    row = rows[6]
    point = row["values"]["points"][0]
    if mutation == "pressure_x10":
        point["fugacity_Pa"] = str(Decimal(point["fugacity_Pa"]) * 10)
    elif mutation == "printed_log_plus_001":
        point["neg_log_fO2_as_printed"] = str(
            Decimal(point["neg_log_fO2_as_printed"]) + Decimal("0.01")
        )
    elif mutation == "coordinated_interior_log":
        point = rows[6]["values"]["points"][1]
        point["neg_log_fO2_as_printed"] = "14.5"
        point["log_fO2_as_signed"] = "-14.5"
        point["point_conditions"]["fO2_log"]["state"]["value"] = "-14.5"
        point["fugacity_Pa"] = str(Decimal("101325") * Decimal(10) ** Decimal("-14.5"))
    elif mutation == "coordinated_interior_temperature":
        point = rows[6]["values"]["points"][1]
        point["temperature_C_as_printed"] = "1060"
        point["temperature_K"] = "1333.15"
        point["point_conditions"]["temperature_K"]["state"]["value"] = "1333.15"
    elif mutation == "coordinated_sample_identity":
        row["values"]["sample_no_as_printed"] = "12053,98"
        experiment = next(
            item for item in extract["experiments"]
            if item["experiment_id"] == row["observation_id"]
        )
        experiment["sample"]["form"]["state"]["value"] = "Lunar sample 12053,98; pyroxenes"
    elif mutation == "eq5_coefficient":
        extract["species"]["O2"]["context"][0]["values"][
            "iron_wustite_buffer_relation_as_printed"
        ] = "log fO2 = 6.824 − 27471/T"
    else:
        extract["species"]["O2"]["context"][0]["values"][
            "iron_wustite_buffer_temperature_range_as_printed"
        ] = "850–1400 °C"
    with pytest.raises(AssertionError):
        _assert_sato_1973_table_pins(extract)


def test_sato_1973_all_75_conversions_and_migration_consistency() -> None:
    source = EXTRACTS / "sato-hickling-mclane-1973-lunar-fo2.yaml"
    extract = battery_migrate.load_yaml(source)
    rows = extract["species"]["O2"]["observations"]
    assert len(rows) == 15
    assert sum(len(row["values"]["points"]) for row in rows) == 75
    _assert_sato_1973_table_pins(extract)

    migrator = battery_migrate.Migrator(root=REPO_ROOT)
    migrator._migrate_extract(source)
    migrator.finalize()
    result = migrator.result
    observations = [
        obs for obs in result.observations.values()
        if "sato73_" in obs.observation_id
    ]
    assert len(observations) == 75
    assert len([obs for obs in observations if obs.value.kind.value == "point"]) == 75
    assert not result.validation.hard_issues
    assert len(result.experiments) == 15

    migrated = {obs.observation_id: obs for obs in observations}
    assert all(obs.uncertainty.kind.value == "printed" for obs in observations)
    assert all(
        "Basis: log10(fO2/atm)" in obs.uncertainty.verbatim
        for obs in observations
    )
    assert all(
        "Apollo 12" in obs.uncertainty.verbatim
        and "excepted from the reported within-0.2-log-unit scatter" in obs.uncertainty.verbatim
        and "anomalously high initial first-heating readings were omitted from the fitted curves"
        in obs.uncertainty.verbatim
        for obs in observations
        if any(sample in obs.observation_id for sample in ("12009", "12022", "12053"))
    )
    assert all(
        result.experiments[obs.experiment_id].method.value.value == "emf_cell"
        for obs in observations
    )
    extract_experiments = {
        item["experiment_id"]: item for item in extract["experiments"]
    }
    for observation_id, (sample_no, _material, _logs) in _SATO_1973_TABLE_PINS.items():
        method = extract_experiments[observation_id]["thermal_schedule"]["method"]
        history_state = method["state"]
        assert history_state["tag"] == "value"
        history = history_state["value"]
        locator_page = method["locator"]["published_page"]
        if sample_no.startswith(("12009", "12022", "12053")):
            assert "initial first-heating readings" in history
            assert "omitted from the visually fitted curves" in history
            assert locator_page == 1063
        elif sample_no.startswith("14310"):
            assert "Table 1 values for the two 14310 chips are from the first heating cycle" in history
            assert "irreversible, reproducible abrupt rise" in history
            assert locator_page == 1067
        elif sample_no.startswith("14321"):
            assert "irregular fO2 values" in history
            assert locator_page == 1067
        elif sample_no.startswith("15058"):
            assert "almost identical results" in history
            assert locator_page == 1063
        else:
            assert "heating and cooling results" in history
            assert "first heating cycle" not in history.casefold()
            assert locator_page == 1063
    resolved_count = 0
    for row in rows:
        observation_id = row["observation_id"]
        _sample_no, _material, expected_logs = _SATO_1973_TABLE_PINS[observation_id]
        assert row["measurement_basis_as_printed"] == (
            "measured_tabulated is the nearest existing method_class; no class distinguishes author-smoothed or curve-fitted measured values. Each Table 1 value is read from the authors' visually drawn best-fit curve through their measured points, not an individual reading (published p. 1063)."
        )
        assert row["values"]["method_class"] == "measured_tabulated"
        expected_count = 0
        for point, expected_log, expected_temperature in zip(
            row["values"]["points"],
            expected_logs,
            _SATO_1973_TABLE_TEMPERATURES_C,
            strict=True,
        ):
            assert point["neg_log_fO2_as_printed"] == expected_log
            assert point["temperature_C_as_printed"] == expected_temperature
            assert Decimal(point["temperature_K"]) == (
                Decimal(expected_temperature) + Decimal("273.15")
            )
            expected_pa = Decimal("101325") * (
                Decimal(10) ** -Decimal(point["neg_log_fO2_as_printed"])
            )
            consumed_pa = Decimal(point["fugacity_Pa"])
            relative_error = abs(consumed_pa / expected_pa - Decimal(1))
            assert relative_error <= Decimal("1e-6"), (
                row["observation_id"], point["temperature_C_as_printed"]
            )
            matches = [
                obs for observation_id, obs in migrated.items()
                if row["observation_id"] in observation_id
                and obs.point_conditions.get("temperature_K") is not None
                and obs.point_conditions["temperature_K"].state.value
                == Decimal(point["temperature_K"])
            ]
            assert len(matches) == 1, (
                row["observation_id"], point["temperature_C_as_printed"]
            )
            observation = matches[0]
            assert observation.value.point == consumed_pa
            experiment = result.experiments[observation.experiment_id]
            assert experiment.sample.form.state.value == _SATO_1973_SAMPLE_FORMS[observation_id]
            assert experiment.sample.characterization.state.value
            bench = result.benches[experiment.bench_id]
            resolved = oxygen_condition(experiment, bench, observation)
            assert resolved.selected is not None
            assert resolved.selected.value.point == Decimal(
                point["log_fO2_as_signed"]
            )
            resolved_count += 1
            expected_count += 1
        assert expected_count == 5
        sample_form = experiment.sample.form.state.value
        sample_description = experiment.sample.characterization.state.value
        assert sample_form == _SATO_1973_SAMPLE_FORMS[row["observation_id"]]
        sample_number = _SATO_1973_TABLE_PINS[row["observation_id"]][0]
        if sample_number.startswith("14310"):
            assert "“Igneous” rock" in sample_description
            assert "irreversible, reproducible abrupt rise" in experiment.thermal_schedule.method.state.value
        elif sample_number.startswith("14321"):
            assert "Microbreccia" in sample_description
        else:
            assert "Basaltic igneous rock" in sample_description
    assert resolved_count == 75


def test_norris_starting_composition_is_pinned_to_online_extended_table() -> None:
    doc = yaml.safe_load(
        (EXTRACTS / "norris-2017-earth-volatiles-nature.yaml").read_text()
    )
    assert doc["source"]["url"] == "https://pmc.ncbi.nlm.nih.gov/articles/PMC6485635/"
    expected = {
        "SiO2": "50.66",
        "TiO2": "0.96",
        "Al2O3": "15.11",
        "FeO": "9.69",
        "MnO": "0.20",
        "MgO": "8.90",
        "CaO": "12.28",
        "Na2O": "1.96",
        "K2O": "0.07",
    }
    for experiment in doc["experiments"]:
        composition = experiment["sample"]["printed_composition"]
        assert composition["state"] == {"tag": "value", "value": expected}
        assert composition["locator"]["table"] == "Extended Data Table 1"
        assert composition["locator"].get("pdf_page_index") is None
        assert "PMC6485635 online" in composition["locator"]["note"]


def test_okazaki_container_helium_ratio_uncertainty_is_pinned() -> None:
    doc = yaml.safe_load(
        (EXTRACTS / "okazaki-2022-ryugu-gas-treasure-box.yaml").read_text()
    )
    observation = next(
        item
        for item in doc["species"]["He"]["observations"]
        if item["observation_id"] == "okazaki_2022_container_helium_isotope_ratio"
    )

    # Source article p. 3, Fig. 2A (also cited to Table S1) prints ±0.010 × 10−4.
    printed_uncertainty = "+/- 0.010 x 10^-4 (1 sigma)"
    assert observation["uncertainty"]["ratio_3He_4He"] == printed_uncertainty
    assert "(1 sigma)" in printed_uncertainty


def test_banya_watanabe_1977_row_conditions_and_feto_reference_migrate() -> None:
    from simulator.battery.enums import Quantity
    from simulator.battery.migrate import Migrator

    path = EXTRACTS / "banya-watanabe-1977-feto-p2o5-solid-iron.yaml"
    source = yaml.safe_load(path.read_text(encoding="utf-8"))
    source_table4 = next(
        row
        for row in source["species"]["Fe_tO"]["observations"]
        if row["observation_id"].endswith("xp2o5_0_00")
    )
    assert source_table4["values"]["X_P2O5_as_printed"] == 0.00
    assert (
        source_table4["point_conditions"]["X_P2O5_as_printed"]["state"]["value"]["point"]
        == "0.00"
    )

    migrator = Migrator(REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 87
    assert not result.validation.hard_issues
    table3 = [
        row
        for row in result.observations.values()
        if "banya77_t3_run_" in row.observation_id
    ]
    table4 = [
        row
        for row in result.observations.values()
        if "banya77_t4_xp2o5_" in row.observation_id
    ]
    assert len(table3) == 75
    assert len(table4) == 12
    assert all(
        row.point_conditions["temperature_K"].state.is_value for row in table3 + table4
    )
    assert all(
        row.point_conditions["composition"].state.is_value for row in table3
    )

    first_activity = next(
        row for row in table4 if row.observation_id.endswith("xp2o5_0_00")
    )
    assert first_activity.identity.quantity.value == Quantity.ACTIVITY
    assert first_activity.identity.species.formula == "Fe_tO"
    reference = first_activity.identity.reference_state
    assert reference is not None and reference.is_value
    assert reference.value.convention.value == "raoultian_pure_endmember"
    assert reference.value.endmember.formula == "Fe_tO"
    assert first_activity.evidence.class_.value.value == "model_derived"


def test_norris_split_runs_retain_shared_sample_and_quench_facts() -> None:
    doc = yaml.safe_load(
        (EXTRACTS / "norris-2017-earth-volatiles-nature.yaml").read_text()
    )
    experiments = doc["experiments"]
    baseline = experiments[0]
    for experiment in experiments[1:]:
        for field in ("mass_kg", "form", "pretreatment"):
            assert experiment["sample"][field] == baseline["sample"][field]
        assert (
            experiment["thermal_schedule"]["cooling_or_quench"]
            == baseline["thermal_schedule"]["cooling_or_quench"]
        )


def test_sato_1976_migrates_all_numeric_table_cells() -> None:
    """Every printed numeric cell in the three tables becomes a numeric observation."""
    source = EXTRACTS / "sato-1976-lunar-fo2-apollo-17.yaml"
    extract = yaml.safe_load(source.read_text())
    table1 = extract["species"]["O2"]["observations"]
    assert len(table1) == 11
    assert all("condition_as_published" in row["values"] for row in table1)

    migrator = battery_migrate.Migrator(root=REPO_ROOT)
    migrator._migrate_extract(source)
    migrator.finalize()
    observations = list(migrator.result.observations.values())
    counts = {}
    for table in ("table1", "table2", "table3"):
        table_observations = [obs for obs in observations if table in obs.observation_id]
        counts[table] = sum(obs.value.kind.value == "point" for obs in table_observations)
    assert counts == {"table1": 55, "table2": 25, "table3": 29}
    assert sum(counts.values()) == 109
    assert len([obs for obs in observations if "table3" in obs.observation_id]) == 35
    activity_rows = (
        extract["species"]["FeO"]["observations"]
        + extract["species"]["Fe"]["observations"]
    )
    assert len(activity_rows) == 10
    assert all("phase" not in row for row in activity_rows)
    activity_observations = [
        obs
        for obs in observations
        if "table2" in obs.observation_id or "table3" in obs.observation_id
    ]
    assert all(
        obs.identity.species.phase.is_unknown
        and obs.identity.species.phase.reason == "source does not state phase"
        for obs in activity_observations
    )
    table2_70017 = next(
        row for row in extract["species"]["FeO"]["observations"]
        if row["observation_id"] == "sato_1976_table2_70017"
    )
    assert "subsolidus" in table2_70017["phase_context_as_published"]
    assert "phase_context_locator" in table2_70017

    natural = next(
        obs for obs in observations if "table1_70017_31::T=1273.15" in obs.observation_id
    )
    added_fe = next(
        obs for obs in observations if "table1_added_fe_70017_31::T=1273.15" in obs.observation_id
    )
    assert natural.identity.temperature_K == added_fe.identity.temperature_K
    assert "row=70017,31" in natural.locator.note
    assert "row=70017,31" in added_fe.locator.note
    assert natural.experiment_id != added_fe.experiment_id
    assert migrator.result.experiments[natural.experiment_id].method.value.value == "emf_cell"
    assert migrator.result.experiments[added_fe.experiment_id].method.value.value == "emf_cell"

    from decimal import Decimal

    from simulator.battery.waypoints import oxygen_condition
    from tests.battery.test_waypoints import _bench

    source_cells = {}
    bench = _bench()
    for row in table1:
        condition = "added_Fe" if "added_fe" in row["observation_id"] else "natural"
        printed_key = (
            "minus_log_fO2_atm_with_added_Fe"
            if condition == "added_Fe"
            else "minus_log_fO2_atm"
        )
        sample = row["values"]["sample_number_as_printed"]
        for point in row["values"]["series"]:
            printed_minus_log = Decimal(point[printed_key])
            expected_log = -printed_minus_log
            assert point["log_fO2"] == {
                "value": str(expected_log),
                "units": "log10(atm)",
            }
            key = (
                sample,
                Decimal(str(point["T_C"])) + Decimal("273.15"),
                condition,
            )
            assert key not in source_cells
            source_cells[key] = (row, point, expected_log)

    assert len(source_cells) == 55
    for key, (row, point, expected_log) in source_cells.items():
        sample, temperature, condition = key
        fugacity_pa = Decimal(point["fugacity_Pa"])
        expected_pa = Decimal("101325") * (Decimal(10) ** expected_log)
        relative_error = abs(fugacity_pa / expected_pa - Decimal(1))
        assert relative_error <= Decimal("1e-6"), (sample, temperature, condition)
        matches = [
            obs
            for obs in observations
            if row["observation_id"] in obs.observation_id
            and obs.identity.temperature_K.value == temperature
        ]
        assert len(matches) == 1, (sample, temperature, condition)
        observation = matches[0]
        assert observation.experiment_id.endswith(f"::{row['experiment']}")
        assert f"row={sample}" in observation.locator.note
        experiment = migrator.result.experiments[observation.experiment_id]
        assert experiment.sample.form is not None
        assert experiment.sample.form.state.value == "bulk rock"
        phase = observation.identity.species.phase
        assert phase.is_unknown
        assert phase.reason == "source does not state phase"
        assert observation.value.point == fugacity_pa
        located_log = observation.point_conditions["fO2_log"]
        assert located_log.state.value == expected_log
        route = oxygen_condition(
            experiment, bench, observation
        )
        assert route.selected is not None
        assert route.selected.route == "observation_fO2_log"
        assert route.selected.value.point == expected_log


def test_van_limpt_k2o_series_uses_section_and_figure_provenance() -> None:
    doc = yaml.safe_load((EXTRACTS / "kems-046-van-limpt-2007.yaml").read_text())
    observation = next(
        item
        for item in doc["species"]["K"]["observations"]
        if item["observation_id"]
        == "van_limpt_2007_table42_potassium_bearing_glasses"
    )
    assert observation["locator"]["section"] == "4.4.2"
    assert observation["locator"]["figure"] == "4.15"
    assert "table" not in observation["locator"]
    assert observation["values"]["K2O_mass_pct_range"] == [4.8, 7.8]
    assert observation["values"]["source_locators"] == {
        "4.8": {"page": 172, "section": "4.4.2", "figure": "4.15"},
        "7.8": {"page": 172, "section": "4.4.2", "figure": "4.15"},
    }


def test_van_limpt_fig46_keeps_measured_markers_separate_from_model() -> None:
    doc = yaml.safe_load((EXTRACTS / "kems-046-van-limpt-2007.yaml").read_text())
    observations = {
        item["observation_id"]: item
        for item in doc["species"]["Na"]["observations"]
    }
    measured = observations["van_limpt_2007_fig46_elemental_sodium_measured"]
    model = observations["van_limpt_2007_fig46_elemental_sodium_model_output"]
    assert [
        row["p_Na_Pa_approx"]
        for row in measured["values"]["rows"]
        if row["temperature_C"] == 1462
    ] == [2.2, 3.0, 3.5]
    assert model["values"]["rows"] == [
        {"temperature_C": 1462, "p_Na_Pa_approx": 2.6}
    ]
    assert model["values"]["measurement_status"] == "model_output_not_measurement"
    assert model["admission_status"] == "rejected_model_output_not_measurement"


def test_plante_split_registry_foreign_keys_resolve() -> None:
    """One loaded charge keeps its eight analysis series under one experiment."""

    doc = yaml.safe_load((EXTRACTS / "kems-042-plante-1979.yaml").read_text())
    bench_ids = {bench["id"] for bench in doc["benches"]}
    kems_bench = next(bench for bench in doc["benches"] if bench["id"] == "kems-system")
    experiments = {item["experiment_id"]: item for item in doc["experiments"]}

    assert kems_bench["cell_material_and_liner"]["state"] == {
        "tag": "value",
        "value": "Platinum effusion cell",
    }
    assert kems_bench["cell_material_and_liner"]["locator"] == {
        "page": 268,
        "section": "2. Description of Mass Spectrometric System",
    }
    assert set(experiments) == {"k2o-sio2-effusion-series"}
    assert all(item["bench_id"] in bench_ids for item in experiments.values())

    rows = [
        row
        for block in doc["species"].values()
        for section in ("observations", "context")
        for row in block.get(section, [])
        if "experiment" in row
    ]
    experiment_refs = Counter(row["experiment"] for row in rows)
    assert experiment_refs == Counter({"k2o-sio2-effusion-series": 383})

    series_refs = Counter(
        f"k2o-sio2-s{row['values']['series']}"
        for row in rows
        if row.get("values", {}).get("series") is not None
    )
    assert series_refs == Counter(
        {
            "k2o-sio2-s1104": 56,
            "k2o-sio2-s1110": 76,
            "k2o-sio2-s1115": 52,
            "k2o-sio2-s1122": 36,
            "k2o-sio2-s1123": 60,
            "k2o-sio2-s1126": 38,
            "k2o-sio2-s1129": 28,
            "k2o-sio2-s1214": 37,
        }
    )


@pytest.mark.parametrize(
    ("extract", "bench_id", "expected_material", "expected_codes", "expected_class"),
    (
        (
            "kems-020-hastie-1981-nbsir.yaml",
            "hastie-1981-kms",
            "platinum KMS cell",
            ("Pt",),
            "inert",
        ),
        ("kems-023-demaria-1973.yaml", "demaria-1973-kems", None, None, "unknown"),
        (
            "kems-025-markova-1983.yaml",
            "markova-1983-kems",
            "Tungsten (W) cell",
            None,
            "unknown",
        ),
        ("kems-026-markova-1984.yaml", "markova-1984-kems", None, None, "unknown"),
        ("kems-028-yakovlev-1984.yaml", "yakovlev-1984-kems", None, None, "unknown"),
        ("kems-051-allibert-1981.yaml", "allibert-1981-kems", "molybdenum", ("Mo",), "reactive"),
        ("kems-114-nichols-1995.yaml", "nichols-1995-kems", None, None, "unknown"),
        ("kems-201-ichise-1986.yaml", "ichise-1986-kems", None, None, "unknown"),
        (
            "bencze-yazhenskikh-2016.yaml",
            "bencze-2016-supplement-kems",
            "iridium cell with graphite-coated lid; later graphite disc",
            ("Ir", "C_graphite"),
            "reactive",
        ),
        (
            "metsoc-2019-6005.yaml",
            "shornikov-yakovlev-2019-kems",
            "Knudsen molybdenum effusion cell",
            ("Mo",),
            "reactive",
        ),
        (
            "kems-021-plante-1992-feo.yaml",
            "tungsten-iridium-kems",
            "tungsten cell with iridium inner cup",
            ("W", "Ir"),
            "reactive",
        ),
        (
            "kems-027-plante-hastie-1983.yaml",
            "kms",
            "Welded platinum cell; 0.025 cm Pt sheet",
            ("Pt",),
            "inert",
        ),
        (
            "kems-029-yakovlev-shornikov-2011.yaml",
            "yakovlev-ms1301-kems",
            "Tungsten effusion cell with rhenium boat",
            ("W", "Re"),
            "reactive",
        ),
        (
            "kems-031-halwax-2024.yaml",
            "julich-ir-kems",
            "Iridium Knudsen cell in a molybdenum container with tantalum radiation shields",
            ("Ir", "Mo", "Ta"),
            "reactive",
        ),
        ("kems-042-plante-1979.yaml", "kems-system", "Platinum effusion cell", ("Pt",), "inert"),
        ("kems-053-stolyarova-1991.yaml", "stolyarova-kems", "tungsten", ("W",), "reactive"),
        (
            "kems-057-kambayashi-1985.yaml",
            "rm6e",
            "High-purity alumina SSA-S cell; tantalum susceptor",
            ("Al2O3", "Ta"),
            "reactive",
        ),
        (
            "kems-058-ohara-1987.yaml",
            "ohara-kems",
            "Alumina Knudsen cell with electrolytic-iron inner crucible",
            ("Al2O3", "Fe"),
            "not_inert",
        ),
        (
            "kems-066-ichise-1977.yaml",
            "rm6k",
            "Alumina Knudsen cell; tantalum cell holder",
            ("Al2O3", "Ta"),
            "reactive",
        ),
        (
            "kems-067-yamada-1980.yaml",
            "rm6k",
            "homemade thoria (ThO2) Knudsen cell",
            ("ThO2",),
            "not_inert",
        ),
        (
            "kems-069-furukawa-1976.yaml",
            "thoria-kems",
            "Laboratory-made thoria Knudsen cell",
            ("ThO2",),
            "not_inert",
        ),
        (
            "kems-087-yamada-kato-1980.yaml",
            "yamada-kato-rm6k",
            "High-purity alumina Knudsen cell with tantalum susceptor and radiation shields",
            ("Al2O3", "Ta"),
            "reactive",
        ),
        (
            "kems-088-ichise-1975.yaml",
            "ichise-rm6k",
            "Alumina Knudsen cell and tantalum cell holder",
            ("Al2O3", "Ta"),
            "reactive",
        ),
        ("kems-095-ueda-1986.yaml", "ueda-kems", "Y2O3", ("Y2O3",), "not_inert"),
        (
            "kems-105-yamada-1983.yaml",
            "yamada-kato-rm6k",
            "Beryllia Knudsen cells for Fe-P-Al and Fe-P-Ti; alumina "
            "Knudsen cells for other Fe-P-i systems",
            ("BeO", "Al2O3"),
            "not_inert",
        ),
        (
            "kems-111-ichise-1982.yaml",
            "rm6k",
            "Sintered alumina crucible",
            ("Al2O3",),
            "not_inert",
        ),
        (
            "kems-112-ichise-1989.yaml",
            "ichise-kems",
            "sintered alumina crucible and tantalum holder",
            ("Al2O3", "Ta"),
            "reactive",
        ),
        (
            "kems-116-nunoue-1987.yaml",
            "rm6e",
            "High-purity alumina SSA-S Knudsen cell",
            ("Al2O3",),
            "not_inert",
        ),
        (
            "kems-118-yamamoto-1983.yaml",
            "rm-6e",
            "High-purity alumina SSA-S Knudsen-cell crucible",
            ("Al2O3",),
            "not_inert",
        ),
        (
            "kems-119-furukawa-1975.yaml",
            "rm6k",
            "High-purity alumina SSA-S; tantalum susceptor",
            ("Al2O3", "Ta"),
            "reactive",
        ),
        ("kems-120-ueshima-1984.yaml", "rm6k", "Alumina Knudsen cell", ("Al2O3",), "not_inert"),
        (
            "kems-137-bischof-2023.yaml",
            "finnigan-mat-271-ir",
            "Iridium Knudsen cell in tungsten housing with three nested tantalum heat shields",
            ("Ir", "W", "Ta"),
            "reactive",
        ),
        (
            "kems-138-bischof-2023.yaml",
            "finnigan-mat-271-ir",
            "Iridium Knudsen cell in tungsten container with three tantalum heat shields",
            ("Ir", "W", "Ta"),
            "reactive",
        ),
        (
            "kems-169-nakazawa-1976.yaml",
            "nag-530",
            "Molybdenum lid, container, and orifice plate; alumina inner crucible",
            ("Mo", "Al2O3"),
            "reactive",
        ),
        (
            "kems-184-behrens-1979.yaml",
            "elfs-quadrupole",
            "graphite",
            ("C_graphite",),
            "reactive",
        ),
        ("kems-188-nanjo-1976.yaml", "nanjo-kems", "Quartz Knudsen cell", ("SiO2",), "not_inert"),
        (
            "kems-ms2000-044.yaml",
            "alkali-silicate-kems",
            "Nb, Ta, Mo, or Ni cell material; aluminium powder sometimes mixed with test substance",
            ("Nb", "Ta", "Mo", "Ni"),
            "reactive",
        ),
        (
            "ueshima-1982-fe-mo-thermal.yaml",
            "ueshima-rm6k",
            "Gas-tight high-purity Al2O3 crucible (Nippon Kagaku Togyo SSA-S) in a Ta container",
            ("Al2O3", "Ta"),
            "reactive",
        ),
    ),
)
def test_kems_cell_material_state_survives_migration(
    tmp_path: Path,
    extract: str,
    bench_id: str,
    expected_material: str | None,
    expected_codes: tuple[str, ...] | None,
    expected_class: str,
) -> None:
    from tests.battery.test_migrate import _migrate_real_extract
    from simulator.battery.migrate import bench_from_plain, to_plain
    from simulator.battery.score import _cell_material_class

    result = _migrate_real_extract(tmp_path, extract)
    bench = next(
        bench for bench in result.benches.values()
        if bench.id.endswith(f"::bench::{bench_id}")
    )
    cell = bench.cell_material_and_liner
    assert cell is not None and cell.locator is not None
    if expected_material is None:
        assert cell.state.is_unknown
        assert cell.state.reason == "not_published"
    else:
        assert cell.state.is_value
        assert cell.state.value == expected_material

    if expected_codes is None:
        assert bench.cell_materials is None
    else:
        assert bench.cell_materials is not None
        assert tuple(item.state.value.value for item in bench.cell_materials) == expected_codes
        assert all(item.locator == cell.locator for item in bench.cell_materials)
    assert bench_from_plain(to_plain(bench)) == bench
    assert _cell_material_class(bench.cell_materials) == expected_class
    if extract == "kems-025-markova-1983.yaml":
        experiment = next(
            experiment
            for experiment in result.experiments.values()
            if experiment.experiment_id.endswith("::kems-025-markova-1983")
        )
        assert experiment.apparatus is not None
        located_material = experiment.apparatus.cell_material_and_liner
        assert located_material is not None
        assert located_material.locator is not None
        assert located_material.locator.published_page == 869


def test_plante_cell_materials_round_trip_through_work_store(tmp_path: Path) -> None:
    from tests.battery.test_migrate import _migrate_real_extract
    from simulator.battery.migrate import load_migrated_benches, write_outputs

    root = tmp_path / "tree"
    result = _migrate_real_extract(tmp_path, "kems-042-plante-1979.yaml")
    source_bench = next(
        bench
        for bench in result.benches.values()
        if bench.id.endswith("::bench::kems-system")
    )
    assert source_bench.cell_materials is not None
    assert [item.state.value.value for item in source_bench.cell_materials] == ["Pt"]
    assert all(item.locator is not None for item in source_bench.cell_materials)

    write_outputs(result, root)
    stored_bench = load_migrated_benches(root)[source_bench.id]

    assert stored_bench.cell_materials == source_bench.cell_materials
    assert [item.locator for item in stored_bench.cell_materials or ()] == [
        item.locator for item in source_bench.cell_materials
    ]


def test_tsukihashi_split_temperature_locators_use_figure7() -> None:
    doc = yaml.safe_load((EXTRACTS / "ts1985.yaml").read_text())
    experiments = [
        item
        for item in doc["experiments"]
        if item["experiment_id"].startswith("na2o-sio2-xna2o-")
    ]
    assert len(experiments) == 12
    for experiment in experiments:
        locator = experiment["conditions"]["temperature_K"]["locator"]
        assert locator["figure"] == "7"
        assert "table" not in locator
        assert locator["note"].startswith("Figure 7 isotherm at ")


def test_run_split_observations_keep_source_species_bucket() -> None:
    split_extracts = (
        "kems-200-ueshima-1983.yaml",
        "wimpenny-2019-zn-isotope-evaporation-extreme-t.yaml",
        "sossi-2020-cu-zn-isotope-evap-formalism.yaml",
        "mendybaev-2017-fun-cai-lab-evaporation.yaml",
        "ntrs-19650014783.yaml",
        "kems-015-hashimoto-1983.yaml",
        "kems-012-sossi-2019.yaml",
    )
    removed_sources = {
        "wimpenny_2019_table1_experimental_zn_rows": "Zn",
        "sossi_2020_cu_table1_measured_runs": "Cu",
        "sossi_2020_zn_table1_measured_runs": "Zn",
        "mendybaev_2017_func_table1_mass_loss": "FUNC",
        "bowles_rosenblum_1965_na_table1_static_capsule": "Na",
    }
    checked = 0
    for filename in split_extracts:
        doc = yaml.safe_load((EXTRACTS / filename).read_text())
        species_by_id = {
            observation["observation_id"]: species
            for species, block in doc["species"].items()
            for observation in (
                (block.get("observations") or []) + (block.get("context") or [])
            )
            if observation.get("observation_id")
        }
        for observation_id, species in species_by_id.items():
            delimiter = "__run_" if "__run_" in observation_id else "::"
            if delimiter not in observation_id:
                continue
            source_id = observation_id.split(delimiter, 1)[0]
            expected = species_by_id.get(source_id, removed_sources.get(source_id))
            assert expected is not None, (filename, observation_id, source_id)
            assert species == expected, (filename, observation_id, species, expected)
            checked += 1
    assert checked == 389


def test_sossi_split_compositions_point_to_table1_page() -> None:
    doc = yaml.safe_load((EXTRACTS / "kems-012-sossi-2019.yaml").read_text())
    assert len(doc["experiments"]) == 43
    for experiment in doc["experiments"]:
        locator = experiment["sample"]["printed_composition"]["locator"]
        assert locator["table"] == "1"
        assert locator["pdf_page_index"] == 52


def test_split_aggregate_locator_notes_preserve_commas() -> None:
    cases = {
        "kems-035-sauerborn-2005.yaml": (
            "jsc1-solar-series",
            "JSC-1 MS1/MS2/MS4/MS5 series; table preserves run-specific mass, "
            "peak temperature, and high-temperature duration",
        ),
        "kems-137-bischof-2023.yaml": (
            "an-di-series",
            "Runs 1_low, 2_low, and 3_high; a measurement series, not one physical charge",
        ),
    }
    for filename, (experiment_id, expected_note) in cases.items():
        doc = yaml.safe_load((EXTRACTS / filename).read_text())
        experiment = next(
            item for item in doc["experiments"]
            if item["experiment_id"] == experiment_id
        )
        assert experiment["locator"] == {
            "page": experiment["locator"]["page"],
            "table": experiment["locator"]["table"],
            "note": expected_note,
        }

# Frozen closed-set hash at t-510 policy adoption (sorted source_ids joined by \n).
# Null-hypothesis: an extract can be ADDED to the allowlist later → closed set
# grows → this hash drifts and the shrink-only test goes RED.
CLOSED_SET_SHA256 = (
    "7784107b6656e0ab1c1e2ef9c33fe95a6108b6585a91e4e8ca1b5021960c8e9b"
)
# Canonical append-only graduation history hash (sorted ids joined by \n).
# Updating this pin is an explicit review event; removing a prior tombstone
# must never be bundled into a policy rewrite.
GRADUATION_LEDGER_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_extract(**overrides):
    doc = {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {"citation": "Fixture et al. (2026), Test Journal 1:1"},
        "extraction": {
            "method": "unit_test",
            "date": "2026-08-01",
            "worker": "pytest",
        },
        "review_status": "draft",
        "fidelity_samples": [
            {
                "path": "species.Fe.observations[fe_alpha_1].values.alpha",
                "value": 0.24,
                "note": "fixture fidelity sample",
                "locator": {"page": 2, "table": "1"},
            }
        ],
        "species": {
            "Fe": {
                "observations": [
                    {
                        "observation_id": "fe_alpha_1",
                        "type": "alpha",
                        "locator": {"table": "1", "page": 2},
                        "T_range_K": [1700.0, 1800.0],
                        "phase": "silicate_melt",
                        "regime": "langmuir_free_evaporation",
                        "units": "dimensionless",
                        "uncertainty": {"note": "fixture ±10% band", "relative": 0.1},
                        "values": {"alpha": 0.24},
                    }
                ]
            }
        },
    }
    doc.update(overrides)
    return doc


@pytest.fixture
def tmp_extracts(tmp_path: Path):
    d = tmp_path / "extracts"
    d.mkdir()
    (d / "_source_priority.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_extract_source_priority.v1",
                "source_priority": {
                    # Non-lexical order: other-source must win over fixture-source
                    # so a pure lexical sort would pick the wrong winner.
                    "alpha": ["other-source", "fixture-source"],
                    "psat_series": ["fixture-source", "other-source"],
                    # 2026-08-05 carrier merge 1a1ab47 removed the non-schema
                    # partial_pressure_series family; custom-priority coverage
                    # must use the same closed family set as production data.
                    "gibbs_table": ["fixture-source", "other-source"],
                    "activity_coefficient": ["fixture-source"],
                    "rate_series": ["fixture-source"],
                    "transition_point": ["fixture-source", "other-source"],
                    # d-032 (2026-09-21): physics types admitted from the
                    # invented-type cleanup; fixture tracks the closed set.
                    "partial_pressure": ["fixture-source"],
                    "mass_loss": ["fixture-source"],
                    "heat_capacity_and_heat_content_series": ["fixture-source"],
                    "interaction_parameter": ["fixture-source"],
                    "concentration_series": ["fixture-source"],
                    "volatility_series": ["fixture-source"],
                    "phase_transition": ["fixture-source"],
                    "gas_speciation": ["fixture-source"],
                    "sulfur_solubility_and_sulfate_capacity_series": ["fixture-source"],
                    "composition_series": ["fixture-source"],
                },
            }
        ),
        encoding="utf-8",
    )
    return d


def _write_extract(directory: Path, doc: dict) -> Path:
    path = directory / f"{doc['source_id']}.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def _write_companion_table(
    tmp_path: Path,
    *,
    filename: str = "fixture-source-table-s1.yaml",
    parent_exists: bool = True,
    parent_links: bool = True,
) -> Path:
    directory = tmp_path / "data" / "literature" / "extracts"
    directory.mkdir(parents=True, exist_ok=True)
    table_path = directory / filename
    table_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_extract_table.v1",
                "source_id": "fixture-source",
                "rows": [{"value": 1}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    if parent_exists:
        parent_doc = _minimal_extract()
        if parent_links:
            parent_doc["species"]["Fe"]["observations"][0]["values"][
                "table_transcription"
            ] = table_path.relative_to(tmp_path).as_posix()
        (directory / "fixture-source.yaml").write_text(
            yaml.safe_dump(parent_doc, sort_keys=False), encoding="utf-8"
        )
    return table_path


# ---------------------------------------------------------------------------
# Validator unit tests
# ---------------------------------------------------------------------------


def test_minimal_extract_valid():
    errs = vle.validate_extract_document(
        _minimal_extract(), expected_source_id="fixture-source"
    )
    assert errs == []


def test_docs_private_reference_in_nested_prose_is_refused_but_path_is_allowed():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["locator"]["note"] = (
        "See docs-private/research/hidden.md"
    )
    doc["extraction"]["provenance_path"] = "docs-private/research/source.md"

    errors = vle.validate_extract_document(
        doc, path=Path("fixture-source.yaml"), expected_source_id="fixture-source"
    )

    assert any(
        "fixture-source.yaml:species.Fe.observations[0].locator.note" in error
        and "[docs-private-prose]" in error
        for error in errors
    )
    assert not any("extraction.provenance_path" in error for error in errors)

    clean = _minimal_extract()
    clean["extraction"]["provenance_path"] = "docs-private/research/source.md"
    assert vle.validate_extract_document(
        clean, expected_source_id="fixture-source"
    ) == []


def test_clean_extract_citation_renders_without_private_path_in_index():
    spec = importlib.util.spec_from_file_location(
        "literature_build_index", REPO_ROOT / "data/literature/build_index.py"
    )
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    citation = _minimal_extract()["source"]["citation"]
    counts = dict.fromkeys(
        (
            "sources",
            "extracts",
            "pdfs_present_in_worktree",
            "pdfs_tracked_99_kems_langmuir",
            "pdfs_without_extract",
            "extracts_without_pdf",
            "alias_groups_needing_resolution",
            "extracts_with_private_locators",
        ),
        0,
    )
    counts.update(sources=1, extracts=1, extracts_without_pdf=1)
    gap_names = (
        "pdfs_without_extract",
        "extracts_without_pdf",
        "extracts_with_private_locators",
        "untracked_files",
        "sidecars_missing_fields",
        "source_id_collisions",
    )
    index = {
        "counts": counts,
        "scan": {
            "root": ".",
            "corpus_root": ".",
            "private_roots_scanned": [],
            "private_roots_not_scanned": [],
            "hunt_json": None,
        },
        "sources": [
            {
                "source_id": "fixture-source",
                "citation": citation,
                "pdf_status": "ABSENT",
                "pdf_last_seen": [],
                "extracts": [],
                "corpus_status": "unavailable",
                "corpus": {},
            }
        ],
        "gaps": {name: [] for name in gap_names},
    }

    rendered = builder.render_md(index)

    assert "docs-private" not in rendered
    assert citation in rendered


def test_refuses_context_container_outside_species_block():
    doc = _minimal_extract()
    row = {
        "observation_id": "apparatus_note",
        "type": "apparatus",
        "locator": {"page": 1},
        "values": {"apparatus": "fixture"},
        "units": "text",
    }
    doc["context"] = [row]

    errors = vle.validate_extract_document(
        doc,
        path=Path("fixture-source.yaml"),
        expected_source_id="fixture-source",
    )

    assert any(
        "fixture-source.yaml:context" in error
        and "species.<formula>.context" in error
        and "not lifted by the migrator" in error
        for error in errors
    )


def test_validator_help_works_without_pythonpath():
    import os
    import subprocess

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(TOOLS / "validate_literature_extracts.py"), "--help"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


def test_accepts_scalar_context_description_inside_row():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"]["context"] = (
        "validation comparison with another source"
    )

    errors = vle.validate_extract_document(
        doc, expected_source_id="fixture-source"
    )

    assert not any("context container is not lifted" in error for error in errors)


def test_species_context_validates_and_migrator_lifts_context_id(tmp_path: Path):
    from simulator.battery.migrate import migrate

    doc = _minimal_extract()
    row = {
        "observation_id": "apparatus_note",
        "type": "apparatus",
        "locator": {"page": 1},
        "values": {"apparatus": "fixture"},
        "units": "text",
    }
    doc["species"]["Fe"]["context"] = [row]
    assert vle.validate_extract_document(
        doc, expected_source_id="fixture-source"
    ) == []

    literature = tmp_path / "data" / "literature"
    extracts = literature / "extracts"
    extracts.mkdir(parents=True)
    (literature / "compilations").mkdir()
    (literature / "INDEX.yaml").write_text(
        "schema_version: literature_index.v1\nsources: []\n", encoding="utf-8"
    )
    _write_extract(extracts, doc)
    result = migrate(tmp_path, write=False)

    lifted = [
        context
        for rows in result.context_by_work.values()
        for context in rows
    ]
    assert [context["context_id"] for context in lifted] == [
        "fixture-source::context::apparatus_note"
    ]


def test_refuses_bad_schema_version():
    doc = _minimal_extract(schema_version="v0")
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("schema_version" in e for e in errs)


def test_refuses_source_id_filename_mismatch(tmp_extracts: Path):
    doc = _minimal_extract()
    path = tmp_extracts / "wrong-name.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    errs = vle.validate_extract_file(path)
    assert any("does not match filename stem" in e for e in errs)


def test_accepts_linked_companion_table(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(vle, "ROOT", tmp_path)
    path = _write_companion_table(tmp_path)

    assert vle.validate_extract_file(path) == []


def test_refuses_absolute_locator_path_on_companion(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(vle, "ROOT", tmp_path)
    path = _write_companion_table(tmp_path)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["rows"][0]["locator"] = {"source_path": "/private/tmp/not-portable.pdf"}
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    errors = vle.validate_extract_file(path)

    assert any(
        "locator.source_path must be repository-relative" in error
        and "/private/tmp/not-portable.pdf" in error
        for error in errors
    )


def test_refuses_companion_table_with_unprefixed_filename(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(vle, "ROOT", tmp_path)
    path = _write_companion_table(tmp_path, filename="other-table-s1.yaml")

    errs = vle.validate_extract_file(path)

    assert any("filename stem must start with source_id" in error for error in errs)
    assert not any("parent extract does not link" in error for error in errs)


def test_refuses_companion_table_without_parent_extract(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(vle, "ROOT", tmp_path)
    path = _write_companion_table(tmp_path, parent_exists=False)

    errs = vle.validate_extract_file(path)

    assert any("companion parent extract does not exist" in error for error in errs)


def test_refuses_companion_table_not_linked_by_parent(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(vle, "ROOT", tmp_path)
    path = _write_companion_table(tmp_path, parent_links=False)

    errs = vle.validate_extract_file(path)

    assert any(
        "parent extract does not link companion table" in error for error in errs
    )


def test_main_reports_all_source_id_filename_mismatches(tmp_path: Path, capsys):
    first = tmp_path / "wrong-one.yaml"
    second = tmp_path / "wrong-two.yaml"
    text = yaml.safe_dump(_minimal_extract(), sort_keys=False)
    first.write_text(text, encoding="utf-8")
    second.write_text(text, encoding="utf-8")

    status = vle.main(
        [
            "--skip-priority",
            "--skip-fidelity-policy",
            str(first),
            str(second),
        ]
    )
    stderr = capsys.readouterr().err

    assert status == 1
    assert f"{first}: source_id" in stderr
    assert f"{second}: source_id" in stderr
    assert stderr.count("does not match filename stem") == 2


def test_refuses_unknown_observation_type():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["type"] = "antoine_fit"
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("type must be one of" in e for e in errs)


def test_refuses_observation_without_locator():
    doc = _minimal_extract()
    del doc["species"]["Fe"]["observations"][0]["locator"]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("locator is required" in e for e in errs)


def test_refuses_equipment_without_locator():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "orifice_area": {"value": 1.0e-7, "units": "m2"}  # no locator
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("missing locator" in e for e in errs)
    assert any("equipment" in e for e in errs)


def test_refuses_equipment_without_value():
    """Null-hypothesis (P2): locator-only equipment used to validate green."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "orifice_area": {"locator": {"page": 1}}  # no value
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("missing value" in e for e in errs)


def test_refuses_equipment_null_value():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "orifice_area": {"value": None, "locator": {"page": 1}}
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("must not be null" in e for e in errs)


def test_refuses_bare_scalar_equipment():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "cell_material": "Mo"  # bare scalar
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("bare" in e or "object with value+locator" in e for e in errs)


def test_refuses_misplaced_equipment_field():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["orifice_area"] = 1.0e-7
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("must live under observation.equipment" in e for e in errs)


def test_accepts_equipment_with_per_field_locator():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "orifice_area": {
            "value": 3.14e-7,
            "units": "m2",
            "locator": {"figure": "1b", "page": 4},
        },
        "sample_surface_area": {
            "value": 1.0e-4,
            "units": "m2",
            "inferred": True,
            "inference": "π(d/2)^2 from stated crucible ID 10 mm",
            "locator": {"paragraph": "experimental section", "page": 4},
        },
        "cell_material": {
            "value": "Mo",
            "locator": {"paragraph": "Knudsen cell machined from Mo", "page": 3},
        },
        "multi_orifice_series": {
            "value": False,
            "locator": {"note": "single orifice stated in apparatus"},
        },
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert errs == []


def test_heck_atmospheric_pressure_remains_inferred():
    """Atmospheric air supports inference, not a printed 101325 Pa point."""
    doc = yaml.safe_load((EXTRACTS / "kems-140-heck-2025.yaml").read_text())
    pressure = doc["experiments"][0]["pressure_environment"]["total_pressure_Pa"]
    assert pressure["state"]["value"]["point"] == "101325"
    assert pressure["inferred"] is True


def test_r16_extract_ids_resolve_and_preserve_unavailable_queue_links():
    """Rewritten observation IDs must retain experiment and queue ownership."""
    root = REPO_ROOT / "data" / "literature"
    pairs = (
        ("kems-140-heck-2025", "10.1016_j.gca.2025.05.007"),
        ("holzheid-1997-feo-nio-coo-activity-metal-saturated", "10.1016_s0009-2541(97)00030-2"),
        ("thomas-2022-chlorine-bonding-silicate-melts", "10.1016_j.chemgeo.2022.121269"),
        ("sossi-2020-cu-zn-isotope-evap-formalism", "10.1016_j.gca.2020.08.011"),
    )
    queue = (root.parent / "battery" / "migration-queue.yaml").read_text()
    for name, work_id in pairs:
        extract = yaml.safe_load((root / "extracts-v2" / f"{name}.yaml").read_text())
        work = yaml.safe_load((root / "works" / f"{work_id}.yaml").read_text())
        experiment_ids = {row["experiment_id"] for row in work["experiments"]}
        assert all(row["experiment_id"] in experiment_ids for row in extract["observations"])
        assert all(
            row["observation_id"] in queue
            for row in extract["observations"]
            if row["value"]["kind"] == "unavailable"
        )
def test_inferred_equipment_requires_derivation_note():
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "orifice_area": {
            "value": 1e-7,
            "units": "m2",
            "inferred": True,
            "locator": {"page": 1},
            # missing inference/note
        }
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("inferred" in e for e in errs)


def test_inferred_truthy_string_requires_note():
    """Null-hypothesis (P3): identity `is True` let truthy strings bypass the rule."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["equipment"] = {
        "orifice_area": {
            "value": 1e-7,
            "units": "m2",
            "inferred": "yes",
            "locator": {"page": 1},
        }
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("inferred" in e for e in errs)


def test_values_require_units():
    doc = _minimal_extract()
    del doc["species"]["Fe"]["observations"][0]["units"]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("units required" in e for e in errs)


def test_refuses_empty_values_payload():
    """Null-hypothesis (P1 empty winners): values:{} validated green and could win."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"] = {}
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("empty" in e or "null-only" in e for e in errs)


def test_duplicate_observation_id_refused():
    doc = _minimal_extract()
    obs = copy.deepcopy(doc["species"]["Fe"]["observations"][0])
    doc["species"]["Fe"]["observations"].append(obs)
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("duplicate observation_id" in e for e in errs)


def test_duplicate_observation_id_cross_species_refused():
    """Null-hypothesis (P2): seen-id set was per-species, so Fe/Na shared ids passed."""
    doc = _minimal_extract()
    doc["species"]["Na"] = {
        "observations": [
            {
                "observation_id": "fe_alpha_1",  # same as Fe
                "type": "alpha",
                "locator": {"page": 1},
                "units": "dimensionless",
                "values": {"alpha": 0.1},
            }
        ]
    }
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("duplicate observation_id" in e for e in errs)


def test_refuses_missing_fidelity_samples():
    """Null-hypothesis (t-510): new extracts without samples used to validate green."""
    doc = _minimal_extract()
    del doc["fidelity_samples"]
    # pre_policy_ids=None → samples required (non-allowlisted fixture)
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("fidelity_samples" in e for e in errs)


# ---------------------------------------------------------------------------
# d-032: context sibling container + admitted physics types (owner ruling)
# ---------------------------------------------------------------------------


def _context_row(**overrides):
    row = {
        "observation_id": "fe_bench_note",
        "type": "apparatus",
        "locator": {"page": 3, "section": "experimental"},
        "units": "as printed",
        "values": {"quantity": "apparatus_note", "method_class": "method_only", "furnace": "muffle"},
    }
    row.update(overrides)
    return row


def test_context_container_rows_validate():
    doc = _minimal_extract()
    doc["species"]["Fe"]["context"] = [_context_row()]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert errs == []


def test_context_row_requires_locator_and_values():
    doc = _minimal_extract()
    bad = _context_row()
    del bad["locator"]
    doc["species"]["Fe"]["context"] = [bad]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("locator is required" in e for e in errs)


def test_refuses_scored_type_parked_in_context():
    """Null-hypothesis (d-032 boundary): context must not shadow the scored container."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["context"] = [_context_row(type="alpha")]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("must live under observations" in e for e in errs)


def test_context_and_observations_share_id_namespace():
    doc = _minimal_extract()
    doc["species"]["Fe"]["context"] = [_context_row(observation_id="fe_alpha_1")]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("duplicate observation_id" in e for e in errs)


def test_structured_fidelity_sample_resolves_context_row():
    """Structured pins resolve rows in the sibling context[] container (d-032)."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["context"] = [_context_row()]
    sample = {
        "species": "Fe",
        "observation_id": "fe_bench_note",
        "observable": "apparatus",
        "field": "furnace",
        "value": "muffle",
        "locator": {"page": 3, "section": "experimental"},
    }
    assert vle.resolve_fidelity_sample(doc, sample) == "muffle"
    assert vle.check_fidelity_sample_matches(doc, sample) == []
    # Observable-only addressing resolves a context row the same way.
    obs_only = {
        "species": "Fe",
        "observable": "apparatus",
        "field": "furnace",
        "value": "muffle",
        "locator": {"page": 3, "section": "experimental"},
    }
    assert vle.check_fidelity_sample_matches(doc, obs_only) == []
    doc["fidelity_samples"].extend([sample, obs_only])
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert errs == [], errs


def test_structured_fidelity_sample_survives_d032_move():
    """Null-hypothesis (b-562): before the fix, moving a pinned row from
    observations[] to context[] broke the structured pin ('not found')."""
    row = {
        "observation_id": "fe_bench_note",
        "type": "apparatus",
        "locator": {"page": 3, "section": "experimental"},
        "units": "as printed",
        "values": {"quantity": "apparatus_note", "furnace": "muffle"},
    }
    sample = {
        "species": "Fe",
        "observation_id": "fe_bench_note",
        "observable": "apparatus",
        "field": "furnace",
        "value": "muffle",
        "locator": {"page": 3, "section": "experimental"},
    }
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"].append(row)
    assert vle.check_fidelity_sample_matches(doc, sample) == []
    # d-032 move: same row, same pin — the match must survive the relocation.
    doc["species"]["Fe"]["observations"].remove(row)
    doc["species"]["Fe"]["context"] = [row]
    assert vle.check_fidelity_sample_matches(doc, sample) == []


def test_structured_fidelity_sample_ambiguous_id_is_typed_error():
    """An id matching rows in BOTH containers is a typed ambiguity error,
    never a silent pick of the observation over the context row."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"].append(
        {
            "observation_id": "shared_id",
            "type": "alpha",
            "locator": {"table": "9"},
            "units": "dimensionless",
            "values": {"alpha": 0.99},
        }
    )
    doc["species"]["Fe"]["context"] = [
        _context_row(
            observation_id="shared_id",
            values={"quantity": "apparatus_note", "alpha": 0.11},
        )
    ]
    sample = {
        "species": "Fe",
        "observation_id": "shared_id",
        "field": "alpha",
        "value": 0.99,
        "locator": {"page": 3},
    }
    with pytest.raises(KeyError, match="ambiguous observation_id"):
        vle.resolve_fidelity_sample(doc, sample)
    errs = vle.check_fidelity_sample_matches(doc, sample)
    assert errs and any("ambiguous observation_id" in e for e in errs), errs
    # The duplicate id is itself refused, and the pin reports the typed
    # ambiguity instead of resolving either row's value.
    doc["fidelity_samples"].append(sample)
    doc_errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("duplicate observation_id" in e for e in doc_errs)
    assert any("ambiguous observation_id" in e for e in doc_errs), doc_errs


@pytest.mark.parametrize(
    "new_type",
    [
        "partial_pressure",
        "mass_loss",
        "heat_capacity_and_heat_content_series",
        "interaction_parameter",
        "concentration_series",
        "volatility_series",
        "phase_transition",
        "gas_speciation",
        "sulfur_solubility_and_sulfate_capacity_series",
        "composition_series",
    ],
)
def test_d032_admitted_physics_types_accepted(new_type):
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["type"] = new_type
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert not any("type must be one of" in e for e in errs)


@pytest.mark.parametrize(
    "d_type", ["model_derived", "model_comparison", "quoted_comparator"]
)
def test_d032_boundary_class_types_still_refused(d_type):
    """The engine-reference vs validation-evidence boundary is an OPEN owner
    question: these stay refused until ruled."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["type"] = d_type
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("type must be one of" in e for e in errs)


def test_pre_policy_allowlist_exempts_missing_samples():
    """Grandfathering: pilot source_ids may omit samples under ENFORCED_FOR_NEW."""
    doc = _minimal_extract()
    doc["source_id"] = "costa-jacobson-2015"
    del doc["fidelity_samples"]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="costa-jacobson-2015",
        pre_policy_ids={"costa-jacobson-2015"},
    )
    assert errs == [], errs


def test_non_allowlisted_still_refuses_missing_samples():
    """Null-hypothesis: allowlist exemption must not leak to new source_ids."""
    doc = _minimal_extract()
    doc["source_id"] = "brand-new-ocr-source-2026"
    del doc["fidelity_samples"]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="brand-new-ocr-source-2026",
        pre_policy_ids={"costa-jacobson-2015"},  # not this one
    )
    assert any("fidelity_samples" in e for e in errs)


def test_structured_fidelity_sample_accepted():
    """OCR-style structured samples (species/observable/value/locator) validate."""
    doc = _minimal_extract()
    doc["fidelity_samples"] = [
        {
            "species": "Fe",
            "observable": "alpha",
            "observation_id": "fe_alpha_1",
            "field": "alpha",
            "value": 0.24,
            "locator": {"page": 2, "table": "1"},
        }
    ]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert errs == []
    match_errs = vle.check_all_fidelity_samples_match(doc, label="fixture")
    assert match_errs == []


def test_structured_sample_requires_locator():
    doc = _minimal_extract()
    doc["fidelity_samples"] = [
        {
            "species": "Fe",
            "observable": "alpha",
            "observation_id": "fe_alpha_1",
            "value": 0.24,
            # no locator
        }
    ]
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("locator" in e for e in errs)


def test_refuses_absolute_provenance_path():
    doc = _minimal_extract()
    doc["extraction"]["provenance_path"] = "/Users/someone/Dropbox/docs-private/x.md"
    errs = vle.validate_extract_document(doc, expected_source_id="fixture-source")
    assert any("repository-relative" in e for e in errs)


def test_repo_extracts_validate_green():
    """All pilot extracts in the tree must be validator-green."""
    errors = vle.validate_all()
    assert errors == [], "\n".join(errors[:20])


def _repo_observation(filename: str, observation_id: str) -> dict:
    doc = load_cached_safe_yaml((EXTRACTS / filename).read_text())
    for species in doc["species"].values():
        for observation in species["observations"]:
            if observation["observation_id"] == observation_id:
                return observation
    raise AssertionError(f"observation not found: {filename}::{observation_id}")


def _repo_point_rows(filename: str) -> list[tuple[dict, dict]]:
    doc = load_cached_safe_yaml((EXTRACTS / filename).read_text())
    rows = []
    for species in doc["species"].values():
        for observation in species["observations"]:
            values = observation.get("values") or {}
            for container in ("points", "rows"):
                for row in values.get(container) or []:
                    if isinstance(row, dict):
                        rows.append((observation, row))
    return rows


def test_sossi_na_inference_and_ceiling_never_recreate_alpha_range():
    """Na's disagreeing inference and adopted ceiling are not measured ranges."""
    gamma = _repo_observation(
        "kems-012-sossi-2019.yaml",
        "sossi_2019_na_alpha_e_gamma_derived_range",
    )
    adopted = _repo_observation(
        "kems-012-sossi-2019.yaml",
        "sossi_2019_na_alpha_e_authors_adopted_unity",
    )

    assert "alpha_range" not in gamma["values"]
    assert gamma["values"]["gamma_derived_alpha_e_inference_at_1400C"] == [0.3, 1.7]
    assert gamma["values"]["consumer_policy"] == "report_only_not_executable_alpha_range"
    assert adopted["values"]["alpha_authority_status"] == "analytical_upper_bound"
    assert adopted["values"]["physical_alpha_upper_bound"] == 1.0
    assert adopted["values"]["alpha"] == 1.0


def test_wetzel_duplicate_is_typed_as_solid_film_growth():
    observation = _repo_observation(
        "wetzel-gail-2013-sio-arrhenius.yaml",
        "wetzel_gail_2013_sio_arrhenius",
    )
    assert observation["values"]["system_class"] == "solid_film_growth"
    assert observation["values"]["alpha_kind"] == "condensation_growth_not_evaporation"
    assert observation["condensed_form"]["state"] == "glass_amorphous"


def test_wetzel_model_tables_and_uncertainties_are_typed_first_class():
    """The six Wetzel model tables are retyped on SUPERSEDING rows (PARENT RULE,
    b-483): the parents stay byte-identical rate_series containers and each is
    superseded by a gibbs_table model row; the Table 3 printed uncertainties
    live first-class on the superseding row. No corrections entry may claim a
    parent change that is not applied."""
    doc = load_cached_safe_yaml(
        (EXTRACTS / "kems-011-wetzel-gail-2013.yaml").read_text(encoding="utf-8")
    )
    observations = doc["species"]["SiO"]["observations"]
    by_id = {o["observation_id"]: o for o in observations}
    old_note = (
        "Tabular model payload in the existing rate_series container; not a "
        "measured rate or thermodynamic table. Empty printed cells omitted. "
        "ASCII scientific notation preserves printed values without unit conversion."
    )
    for table in range(1, 7):
        parent_id = f"wetzel_gail_2013_table{table}_quoted_model"
        parent = by_id[parent_id]
        assert parent["type"] == "rate_series"
        assert parent["values"]["note"] == old_note
        children = [o for o in observations if o.get("supersedes") == parent_id]
        assert len(children) == 1, parent_id
        child = children[0]
        assert child["type"] == "gibbs_table"
        assert child["class"] == "model"
        assert child["values"]["method_class"] == "model_derived"
        assert child["values"]["admission_status"] == "model_output_not_measurement"
    table3_child = next(
        o for o in observations
        if o.get("supersedes") == "wetzel_gail_2013_table3_quoted_model"
    )
    assert "uncertainty" in table3_child["values"]
    assert table3_child["values"]["uncertainty"]["rows"]
    for correction in doc.get("corrections") or []:
        assert not (
            correction["observation_id"].startswith("wetzel_gail_2013_table")
            and correction["observation_id"].endswith("_quoted_model")
        ), "stale correction claims an unapplied parent change"

def test_recovered_qualitative_sequences_cannot_become_numeric_rates():
    hashimoto_doc = load_cached_safe_yaml(
        (EXTRACTS / "kems-015-hashimoto-1983.yaml").read_text()
    )
    ca_ids = {
        row["observation_id"]
        for row in hashimoto_doc["species"]["Ca"]["observations"]
    }
    assert {
        "hashimoto_1983_cao_al2o3_residue_enrichment",
        "hashimoto_1983_stage_iv_cao_relative_volatility",
    } <= ca_ids

    richter = _repo_observation(
        "kems-010-richter-2007.yaml", "richter_2007_ca_non_loss_until_mg_exhausted"
    )
    ordering = _repo_observation(
        "kems-015-hashimoto-1983.yaml",
        "hashimoto_1983_fcmas_qualitative_volatility_order",
    )
    ratio = _repo_observation(
        "kems-015-hashimoto-1983.yaml",
        "hashimoto_1983_stage_iv_cao_relative_volatility",
    )
    assert richter["values"]["semantics"] == "bound_not_point_ordering"
    assert (
        richter["values"]["numeric_rate_status"]
        == "not_reported_qualitative_bound_only"
    )
    assert ordering["values"]["order_tiers"] == [["Fe"], ["Mg", "Si"], ["Ca"], ["Al"]]
    assert ordering["values"]["numeric_rate_status"] == "not_reported_do_not_infer"
    assert ratio["values"]["relative_volatility_approx"] == pytest.approx(1.0 / 3.0)
    assert ratio["values"]["numeric_rate_status"] == "ratio_only_not_species_rate"


def test_stolyarova_wilson_numbers_are_gibbs_model_parameters_only():
    observation = _repo_observation(
        "kems-016-stolyarova-1992.yaml",
        "stolyarova_1992_binary_wilson_model_parameters_table2",
    )
    al_si = next(
        row for row in observation["values"]["parameters"]
        if row["system"] == "Al2O3-SiO2"
    )
    assert observation["type"] == "gibbs_table"
    assert observation["values"]["evidence_class"] == "thermodynamic_model_parameter"
    assert observation["values"]["activity_pin_eligible"] is False
    assert al_si["l210"] == 316.3


def test_stolyarova_pressure_identities_survive_migration(tmp_path: Path):
    from tests.battery.test_migrate import _migrate_real_extract
    from simulator.battery.enums import NoticeKind

    atomic_source = _repo_observation(
        "kems-053-stolyarova-1991.yaml",
        "stolyarova_1991_o_atomic_partial_pressure_1933k_equilibrium",
    )
    assert atomic_source["values"]["method_class"] == "quoted_attributed"
    assert atomic_source["values"]["evidence_class"] == "quoted_attributed"
    assert atomic_source["values"]["attribution"] == (
        "Stolyarova et al. (1991), Table Ib, Eq. [1]"
    )
    assert atomic_source["reaction_as_printed"] == "(WO3)(g) = (WO2)(g) + (O)(g)"
    assert "reservoir" not in atomic_source
    assert "reservoir_formula" not in atomic_source

    figure_source = _repo_observation(
        "kems-053-stolyarova-1991.yaml",
        "stolyarova_1991_o2_pressure_fig4",
    )
    assert figure_source["values"]["calculation_method"] == "Eq. [15]"
    assert figure_source["values"]["admission_status"] == "figure_only"
    assert figure_source["values"]["status"] == "typed_refusal"
    assert "[15]" in figure_source["derivation"]["relation"]
    assert "reaction" not in figure_source
    assert "reaction_as_printed" not in figure_source

    result = _migrate_real_extract(
        tmp_path, "kems-053-stolyarova-1991.yaml"
    )
    source = "kems-053-stolyarova-1991"
    stolyarova_benches = [
        bench for bench in result.benches.values()
        if bench.id.endswith("::bench::stolyarova-kems")
    ]
    assert len(stolyarova_benches) == 1
    cell = stolyarova_benches[0].cell_material_and_liner
    assert cell is not None and cell.state.is_value
    assert cell.state.value == "tungsten"
    assert cell.locator.published_page == 3710
    assert cell.locator.section == "Experimental"
    atomic_rows = [
        observation
        for observation in result.observations.values()
        if observation.source_id == source
        and observation.observation_id.startswith(
            f"{source}::stolyarova_1991_o_atomic_partial_pressure_1933k_equilibrium::"
        )
    ]
    figure_rows = [
        observation
        for observation in result.observations.values()
        if observation.source_id == source
        and observation.observation_id.startswith(
            f"{source}::stolyarova_1991_o2_pressure_fig4::"
        )
    ]
    cao_rows = [
        observation
        for observation in result.observations.values()
        if observation.source_id == source
        and observation.observation_id.startswith(
            f"{source}::stolyarova_1991_cao_partial_pressure"
        )
    ]
    assert len(atomic_rows) == 9
    assert len(figure_rows) == 8
    assert len(cao_rows) == 22
    assert sum("complete_evaporation" in row.observation_id for row in cao_rows) == 11
    assert sum("ion_comparison" in row.observation_id for row in cao_rows) == 11
    for observation in cao_rows:
        flags = [
            notice for notice in observation.notices
            if notice.kind is NoticeKind.SOURCE_DISAGREEMENT
        ]
        assert len(flags) == 1
        assert flags[0].reason.startswith("source_internally_inconsistent:")
        assert "3.70 dex below the printed value" in flags[0].reason
        assert "141 kJ/mol" in flags[0].reason
        assert observation.value.point is not None
        assert observation.admission.status.value == "admitted"

    for observation in atomic_rows:
        reaction = observation.identity.reaction
        assert reaction is not None and reaction.is_value
        assert [
            (term.species.formula, term.species.phase.value, str(term.coefficient))
            for term in reaction.value.terms
        ] == [("WO3", "g", "-1"), ("WO2", "g", "1"), ("O", "g", "1")]
        reservoir = observation.identity.reservoir
        assert reservoir is not None and reservoir.is_unknown

    for observation in figure_rows:
        reaction = observation.identity.reaction
        assert reaction is not None and reaction.is_unknown
        assert observation.admission.status.value == "rejected"


def test_stolyarova_printed_binary_compositions_survive_migration(tmp_path: Path):
    """Suppose my change is wrong in the way that matters most — what shows it?

    Removing one structured row composition must reduce the typed-row census;
    digitized-only Figure 3 and Figure 4 points must remain composition-unknown.
    """
    from tests.battery.test_migrate import _migrate_real_extract

    rows = _repo_point_rows("kems-053-stolyarova-1991.yaml")
    activity_source = {
        observation["observation_id"]: observation["standard_state"]
        for observation, _row in rows
        if observation.get("values", {}).get("quantity") == "activity"
    }
    assert len(activity_source) == 6
    assert all(
        "p. 3711" in standard_state and "not stated" in standard_state
        for standard_state in activity_source.values()
    )
    typed_source = [
        (observation, row)
        for observation, row in rows
        if "composition" in (row.get("point_conditions") or {})
    ]
    assert len(typed_source) == 130
    for observation, row in typed_source:
        composition = row["point_conditions"]["composition"]
        value = composition["state"]["value"]
        assert value["basis"] == "printed_mole_fraction"
        assert value["amount_basis"] == "mole_fraction"
        assert len(value["components"]) == 2
        assert "documented complement" in composition["locator"]["note"]
        assert "not printed" in composition["locator"]["note"]
        assert composition["locator"].get("table") in {"Ia", "Ib", "IIa", "IIb"}

    figure_rows = [
        row
        for observation, row in rows
        if observation["observation_id"]
        in {
            "stolyarova_1991_gibbs_integral_energy_fig3",
            "stolyarova_1991_o2_pressure_fig4",
        }
    ]
    assert figure_rows
    assert all("composition" not in (row.get("point_conditions") or {}) for row in figure_rows)

    result = _migrate_real_extract(tmp_path, "kems-053-stolyarova-1991.yaml")
    typed_migrated = [
        observation
        for observation in result.observations.values()
        if observation.source_id == "kems-053-stolyarova-1991"
        and (observation.point_conditions or {}).get("composition") is not None
        and observation.point_conditions["composition"].state.is_value
    ]
    assert len(typed_migrated) == 130
    assert all(
        observation.point_conditions["composition"].state.value.amount_basis.value
        == "mole_fraction"
        for observation in typed_migrated
    )
    from simulator.battery.identity import quantity_token

    activity_migrated = []
    for observation in result.observations.values():
        token = quantity_token(observation.identity)
        if (
            observation.source_id == "kems-053-stolyarova-1991"
            and token is not None
            and token.value == "activity"
        ):
            activity_migrated.append(observation)
    assert len(activity_migrated) == 54
    assert all(
        observation.identity.reference_state is not None
        and observation.identity.reference_state.is_unknown
        for observation in activity_migrated
    )


def test_allibert_printed_binary_compositions_survive_migration(tmp_path: Path):
    from tests.battery.test_migrate import _migrate_real_extract

    rows = _repo_point_rows("kems-051-allibert-1981.yaml")
    activity_source = {
        observation["observation_id"]: observation["standard_state"]
        for observation, _row in rows
        if observation.get("values", {}).get("quantity") == "activity"
    }
    assert len(activity_source) == 5
    assert all(
        "p. 309" in standard_state and "solid " in standard_state
        for standard_state in activity_source.values()
    )
    typed_source = [
        (observation, row)
        for observation, row in rows
        if "composition" in (row.get("point_conditions") or {})
    ]
    # Seven Fig. 2 points now carry their axis-mapped composition (b-726).
    assert len(typed_source) == 83
    fig2_source = [
        (observation, row)
        for observation, row in typed_source
        if row["point_conditions"]["composition"]["locator"].get("figure") == "2"
    ]
    assert len(fig2_source) == 7
    for observation, row in typed_source:
        composition = row["point_conditions"]["composition"]
        value = composition["state"]["value"]
        assert value["basis"] == "printed_mole_fraction"
        assert value["amount_basis"] == "mole_fraction"
        assert len(value["components"]) == 2
        if composition["locator"].get("figure") == "2":
            assert "horizontal axis" in composition["locator"]["note"]
            assert "binary complement x(CaO)" in composition["locator"]["note"]
        else:
            assert "documented complement" in composition["locator"]["note"]
            assert "not printed" in composition["locator"]["note"]
            assert composition["locator"].get("table") in {"II", "V"} or composition[
                "locator"
            ].get("figure") in {6, "6"}

    table2 = next(
        row
        for observation, row in rows
        if observation["observation_id"] == "allibert_1981_table2_cao_activity_kems"
    )
    assert table2["point_conditions"]["composition"]["state"]["value"]["components"] == [
        ["CaO", "0.645"],
        ["Al2O3", "0.355"],
    ]
    figure2_rows = [
        row
        for observation, row in rows
        if observation["observation_id"]
        == "allibert_1981_fig2_alumina_activity_gibbs_duhem"
    ]
    assert figure2_rows
    assert [
        row["point_conditions"]["composition"]["state"]["value"]["components"]
        for row in figure2_rows
    ] == [
        [["CaO", "0.8"], ["Al2O3", "0.2"]],
        [["CaO", "0.7"], ["Al2O3", "0.3"]],
        [["CaO", "0.6"], ["Al2O3", "0.4"]],
        [["CaO", "0.5"], ["Al2O3", "0.5"]],
        [["CaO", "0.4"], ["Al2O3", "0.6"]],
        [["CaO", "0.3"], ["Al2O3", "0.7"]],
        [["CaO", "0.2"], ["Al2O3", "0.8"]],
    ]

    result = _migrate_real_extract(tmp_path / "allibert-unlinked", "kems-051-allibert-1981.yaml")
    typed_migrated = [
        observation
        for observation in result.observations.values()
        if observation.source_id == "kems-051-allibert-1981"
        and (observation.point_conditions or {}).get("composition") is not None
        and observation.point_conditions["composition"].state.is_value
    ]
    assert len(typed_migrated) == 83
    assert all(
        observation.point_conditions["composition"].state.value.amount_basis.value
        == "mole_fraction"
        for observation in typed_migrated
    )
    from simulator.battery.enums import Phase
    from simulator.battery.identity import quantity_token
    from simulator.battery.records import phase_token

    activity_migrated = []
    for observation in result.observations.values():
        token = quantity_token(observation.identity)
        if (
            observation.source_id == "kems-051-allibert-1981"
            and token is not None
            and token.value == "activity"
        ):
            activity_migrated.append(observation)
    assert activity_migrated
    assert all(
            observation.identity.reference_state is not None
            and observation.identity.reference_state.is_value
            and phase_token(
                observation.identity.reference_state.value.endmember
            ) is Phase.CR
        for observation in activity_migrated
    )




def test_allibert_printed_point_phases_are_typed_at_migration(tmp_path: Path):
    from decimal import Decimal
    from simulator.battery.enums import Phase
    from simulator.battery.identity import quantity_token
    from simulator.battery.migrate import _printed_point_phase_kind
    from tests.battery.test_migrate import _migrate_real_extract

    assert _printed_point_phase_kind("melt") == "liquid"
    assert _printed_point_phase_kind("CaO + melt") == "two_phase"
    assert _printed_point_phase_kind("melt + CaO") == "two_phase"
    assert (
        _printed_point_phase_kind(
            "liquid CaO-Al2O3 melt; final row is printed as CaO + melt"
        )
        is None
    )
    assert _printed_point_phase_kind("saturated vapour") is None

    result = _migrate_real_extract(tmp_path, "kems-051-allibert-1981.yaml")
    source = [
        observation
        for observation in result.observations.values()
        if observation.source_id == "kems-051-allibert-1981"
    ]
    liquid = [
        observation
        for observation in source
        if observation.identity.species.phase.is_value
        and observation.identity.species.phase.value is Phase.L
    ]
    two_phase = [
        observation
        for observation in source
        if observation.identity.species.phase.is_unknown
        and "bulk_composition_in_two_phase_region"
        in (observation.identity.species.phase.reason or "")
    ]
    assert len(liquid) == 14
    assert len(two_phase) == 2
    assert {observation.identity.species.formula for observation in two_phase} == {
        "CaO",
        "Al2O3",
    }
    marker = "two_phase_bulk_composition_not_liquid_composition"
    assert all(
        any(notice.band == marker for notice in observation.notices)
        for observation in two_phase
    )
    assert all(
        observation.identity.composition is not None
        and observation.identity.composition.is_value
        and any(
            name == "CaO" and amount == Decimal("0.8")
            for name, amount in observation.identity.composition.value.components
        )
        for observation in two_phase
    )
    assert all(
        str(getattr(observation.locator, "table", None)) == "II"
        and quantity_token(observation.identity) is not None
        and quantity_token(observation.identity).value == "activity"
        for observation in liquid
    )


def test_spinel_activities_get_solid_phase_but_bulk_residues_stay_unknown():
    from simulator.battery.enums import Phase

    migrator = battery_migrate.Migrator(REPO_ROOT)
    migrator._migrate_extract(
        EXTRACTS / "shornikov-2018-spinel-solid-solutions.yaml"
    )
    migrator._migrate_extract(
        EXTRACTS / "shimaoka-nakamura-1989-nipr2-252.yaml"
    )

    spinel = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == "shornikov-2018-spinel-solid-solutions"
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value.value == "activity"
    ]
    assert len(spinel) == 60
    assert all(
        observation.identity.species.phase.is_value
        and observation.identity.species.phase.value is Phase.CR
        for observation in spinel
    )

    shimaoka = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == "shimaoka-nakamura-1989-nipr2-252"
    ]
    assert len(shimaoka) == 12
    # Table 2 reports bulk run-product Na; it does not identify those values
    # as melt compositions (pp. 258, 260), so their phase remains unknown.
    assert all(
        observation.identity.species.phase.is_unknown
        and observation.identity.species.phase.reason
        == "phase string 'quenched_partial_melt_run_product' is not in the closed automatic map"
        for observation in shimaoka
    )


def test_halwax_pure_solid_formation_enthalpies_are_symmetric():
    expected = {
        "halwax_2024_cao_third_law_formation_enthalpy": (-624.5, 3.5, "CaO(s)"),
        "halwax_2024_mgo_third_law_formation_enthalpy": (-598.0, 10.0, "MgO(s)"),
    }
    for observation_id, (value, uncertainty, phase) in expected.items():
        observation = _repo_observation("kems-031-halwax-2024.yaml", observation_id)
        assert observation["type"] == "gibbs_table"
        assert observation["condensed_form"]["state"] == "crystalline"
        assert observation["values"]["condensed_phase"] == phase
        assert observation["values"]["delta_f_H_kJ_mol"] == value
        assert observation["uncertainty"]["delta_f_H_kJ_mol"] == uncertainty
        assert observation["values"]["melt_activity_applicability"] == "excluded"
        assert observation["values"]["activity_pin_eligible"] is False


def _assert_halwax_figure_only_pressure_transcription(doc: dict) -> None:
    """Pin Halwax's six isothermal observations and their source locators."""
    expected = {
        "Ca": ("cao-series", "Ca(g)", "pure CaO(s)", 2077.0),
        "O": ("cao-series", "O(g)", "pure CaO(s)", 2077.0),
        "O2": ("cao-series", "O2(g)", "pure CaO(s)", 2077.0),
        "Mg": ("mgo-series", "Mg(g)", "pure MgO(s)", 1926.0),
    }
    pressure_rows = [
        (species, row)
        for species in ("Ca", "Mg")
        for row in doc["species"][species]["observations"]
        if row["type"] == "partial_pressure"
    ]
    assert len(pressure_rows) == 6
    rows = {row["observation_id"]: (species, row) for species, row in pressure_rows}
    assert len(rows) == 6
    expected_rows = {
        ("Ca", "Ca(g)"): expected["Ca"],
        ("Ca", "O(g)"): expected["O"],
        ("Ca", "O2(g)"): expected["O2"],
        ("Mg", "Mg(g)"): expected["Mg"],
        ("Mg", "O(g)"): ("mgo-series", "O(g)", "pure MgO(s)", 1926.0),
        ("Mg", "O2(g)"): ("mgo-series", "O2(g)", "pure MgO(s)", 1926.0),
    }
    restored_ids = {
        "halwax_2024_cao_isothermal_o2_pressure_figure_only": ("O2(g)", "O2", "pure CaO(s)"),
        "halwax_2024_cao_isothermal_o_pressure_figure_only": ("O(g)", "O", "pure CaO(s)"),
        "halwax_2024_mgo_isothermal_mg_pressure_figure_only": ("Mg(g)", "Mg", "pure MgO(s)"),
        "halwax_2024_mgo_isothermal_o2_pressure_figure_only": ("O2(g)", "O2", "pure MgO(s)"),
    }
    legacy_ids = {
        ("Ca", "Ca(g)"): "halwax_2024_cao_isothermal_ca_pressure_figure_only",
        ("Mg", "O(g)"): "halwax_2024_mgo_isothermal_o_pressure_figure_only",
    }
    assert set(rows) == set(restored_ids) | set(legacy_ids.values())
    for observation_id, (gas_species, formula, condensed_phase) in restored_ids.items():
        species, row = rows[observation_id]
        temperature = 2077.0 if species == "Ca" else 1926.0
        values = row["values"]
        assert values["quantity"] == "partial_pressure"
        assert values.get("gas_species") == gas_species
        assert values.get("formula") == formula
        assert values.get("condensed_phase") == condensed_phase
        assert row["T_range_K"] == [temperature, temperature]
        assert row["locator"]["page"] == 826
        assert row["locator"]["published_page"] == 826
        assert row["locator"]["pdf_page_index"] == 5
        assert row["locator"]["figure"] == "7"
        assert row["values"]["admission_status"] == "figure_only"
        assert row["values"]["caption"] == "Fig. 7—Isothermal measurement of CaO and MgO."
        assert "points" not in values
        assert "temperature_K" not in values
        assert "partial_pressure_pa" not in values
    for key, observation_id in legacy_ids.items():
        assert (rows[observation_id][0], rows[observation_id][1]["values"]["gas_species"]) == key
    for (species, gas_species), observation_id in legacy_ids.items():
        experiment, gas, oxide, temperature = expected_rows[(species, gas_species)]
        row = rows[observation_id][1]
        values = row["values"]
        assert row["experiment"] == experiment
        assert gas == gas_species
        assert values["condensed_phase"] == oxide
        assert row["T_range_K"] == [temperature, temperature]
        assert values["evidence_class"] == "figure_only"
        assert row["locator"] == {
            "page": 826,
            "published_page": 826,
            "pdf_page_index": 5,
            "figure": "7",
            "note": row["locator"]["note"],
        }
        panel = "upper" if species == "Ca" else "lower"
        assert row["locator"]["note"] == f"d-129; {panel} panel"
        assert row["units"] == "p_i [Pa] versus Time [h]"
        assert values["admission_status"] == "figure_only"
        assert values["caption"] == "Fig. 7—Isothermal measurement of CaO and MgO."
        assert values["note"].endswith("Not digitised.")
        assert "points" not in values
        assert "temperature_K" not in values
        assert "partial_pressure_pa" not in values

    geometry = next(
        row["values"]
        for species in doc["species"].values()
        for row in species["observations"]
        if "polythermal_pressure_figures" in row.get("values", {})
    )
    assert [
        (figure["figure"], figure["locator"])
        for figure in geometry["polythermal_pressure_figures"]
    ] == [
        ("8", {"page": 827, "pdf_page_index": 6}),
        ("9", {"page": 827, "pdf_page_index": 6}),
        ("10", {"page": 827, "pdf_page_index": 6}),
        ("11", {"page": 827, "pdf_page_index": 6}),
        ("12", {"page": 828, "pdf_page_index": 7}),
        ("13", {"page": 828, "pdf_page_index": 7}),
    ]

    cross_sections = next(
        row for row in doc["species"]["CaO"]["observations"]
        if row["observation_id"] == "halwax_2024_table_iii_ionization_cross_sections"
    )
    assert cross_sections["locator"]["page"] == 826
    assert cross_sections["locator"]["pdf_page_index"] == 5
    cross_sections = cross_sections["values"]
    assert cross_sections["pressure_equation_locator"] == {
        "page": 824, "pdf_page_index": 3, "equation": "1"
    }
    assert cross_sections["oxygen_molecular_cross_section_equation_locator"] == {
        "page": 826, "pdf_page_index": 5, "equation": "12"
    }
    for species in ("CaO", "MgO"):
        table_i = next(
            row for row in doc["species"][species]["observations"]
            if row["observation_id"].endswith(
                "table_i_" + species.lower() + "_sample_masses"
            )
        )
        assert table_i["locator"]["page"] == 825
        assert table_i["locator"]["pdf_page_index"] == 4
        assert all(
            item["locator"] == {"page": 825, "table": "I", "pdf_page_index": 4}
            for item in table_i["values"]["series"]
        )
    ni = next(
        row for row in doc["species"]["Ni"]["observations"]
        if row["observation_id"] == "halwax_2024_table_ii_ni_sublimation_this_study"
    )
    assert ni["locator"]["page"] == 826
    assert ni["locator"]["pdf_page_index"] == 5
    assert ni["uncertainty"]["locator"] == {
        "page": 826, "pdf_page_index": 5, "table": "II"
    }
    assert ni["values"]["pressure_calibration_factor"] == 7.6414e-9
    factor_pin = next(
        sample for sample in doc["fidelity_samples"]
        if sample.get("path", "").endswith("pressure_calibration_factor_as_printed")
    )
    assert factor_pin["value"] == "7.6414·10⁻⁹"
    assert factor_pin["locator"]["page"] == 826
    assert factor_pin["locator"]["pdf_page_index"] == 5


def test_halwax_isothermal_pressures_are_pinned_to_pdf_figures_and_identities():
    """Page 826 Fig. 7 pressure identities and pages 827–828 figures 8–13."""
    path = EXTRACTS / "kems-031-halwax-2024.yaml"
    doc = yaml.safe_load(path.read_text())
    _assert_halwax_figure_only_pressure_transcription(doc)

    # Prove the primary row set, Ca temperature, and numeric calibration are guarded.
    deleted = copy.deepcopy(doc)
    for species in ("Ca", "Mg"):
        deleted["species"][species]["observations"] = [
            row for row in deleted["species"][species]["observations"]
            if row["type"] != "partial_pressure"
        ]
    with pytest.raises(AssertionError):
        _assert_halwax_figure_only_pressure_transcription(deleted)

    wrong_temperature = copy.deepcopy(doc)
    ca_row = next(
        row for row in wrong_temperature["species"]["Ca"]["observations"]
        if row["type"] == "partial_pressure"
    )
    ca_row["values"]["temperature_K"] = 1926.0
    with pytest.raises(AssertionError):
        _assert_halwax_figure_only_pressure_transcription(wrong_temperature)

    wrong_factor = copy.deepcopy(doc)
    ni_row = next(
        row for row in wrong_factor["species"]["Ni"]["observations"]
        if row["observation_id"] == "halwax_2024_table_ii_ni_sublimation_this_study"
    )
    ni_row["values"]["pressure_calibration_factor"] = 7.6414e-8
    with pytest.raises(AssertionError):
        _assert_halwax_figure_only_pressure_transcription(wrong_factor)

    null_points = copy.deepcopy(doc)
    o2_row = next(
        row for row in null_points["species"]["Ca"]["observations"]
        if row["observation_id"] == "halwax_2024_cao_isothermal_o2_pressure_figure_only"
    )
    o2_row["values"]["points"] = []
    with pytest.raises(AssertionError):
        _assert_halwax_figure_only_pressure_transcription(null_points)

    missing_gas = copy.deepcopy(doc)
    o2_row = next(
        row for row in missing_gas["species"]["Ca"]["observations"]
        if row["observation_id"] == "halwax_2024_cao_isothermal_o2_pressure_figure_only"
    )
    del o2_row["values"]["gas_species"]
    with pytest.raises(AssertionError):
        _assert_halwax_figure_only_pressure_transcription(missing_gas)


def test_source_priority_file_present_and_valid():
    errs = vle.validate_source_priority_file()
    assert errs == []
    doc = load_cached_safe_yaml((EXTRACTS / "_source_priority.yaml").read_text())
    for fam in vle.OBSERVATION_TYPES:
        assert fam in doc["source_priority"]
        assert doc["source_priority"][fam], f"empty family {fam}"


def test_source_priority_refuses_empty_family(tmp_path: Path):
    p = tmp_path / "_source_priority.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_extract_source_priority.v1",
                "source_priority": {
                    "alpha": [],
                    "psat_series": ["x"],
                    "gibbs_table": ["x"],
                    "activity_coefficient": ["x"],
                    "rate_series": ["x"],
                    "transition_point": ["x"],
                },
            }
        ),
        encoding="utf-8",
    )
    errs = vle.validate_source_priority_file(p)
    assert any("alpha" in e and "non-empty" in e for e in errs)


# ---------------------------------------------------------------------------
# extract_merge
# ---------------------------------------------------------------------------


def test_disagreement_dex_ratio():
    dex = em.disagreement_dex([1.0, 100.0])
    assert dex == pytest.approx(2.0)


def test_disagreement_dex_single_is_none():
    """Null-hypothesis (P1): singleton groups used to receive non-null dex."""
    assert em.disagreement_dex([1.0]) is None
    assert em.disagreement_dex([]) is None


def test_disagreement_dex_no_linear_span_invent():
    """Null-hypothesis (P1): non-positive spreads used to become log10(|Δ|)."""
    assert em.disagreement_dex([-5.0, 3.0]) is None
    assert em.disagreement_dex([0.0, 0.0]) is None


def test_comparable_scalar_not_cross_unit_bag():
    """Null-hypothesis (P1): CEA row with MW+ΔfH+P° must not yield a bag dex scalar set."""
    values = {
        "molecular_weight_g_per_mol": 26.98,
        "delta_f_H_298_15_J_per_mol": 330000.0,
        "reference_pressure_Pa": 100000.0,
    }
    # gibbs prefers delta_f_H — one scalar, not three
    sc = em.comparable_scalar(values, "gibbs_table")
    assert sc == pytest.approx(330000.0)


def test_by_species_retains_competitors_and_marks_winner(tmp_extracts: Path):
    a = _minimal_extract()
    b = _minimal_extract()
    b["source_id"] = "other-source"
    b["species"]["Fe"]["observations"][0]["observation_id"] = "fe_alpha_other"
    b["species"]["Fe"]["observations"][0]["values"] = {"alpha": 0.02}
    b["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_alpha_other].values.alpha",
            "value": 0.02,
            "note": "other",
            "locator": {"page": 1},
        }
    ]
    _write_extract(tmp_extracts, a)
    _write_extract(tmp_extracts, b)

    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    priority = em.load_source_priority(tmp_extracts / "_source_priority.yaml")
    view = em.build_by_species(extracts, source_priority=priority)

    fe = view["species"]["Fe"]
    assert len(fe["observations"]) == 2
    winners = [o for o in fe["observations"] if o.get("is_priority_winner")]
    assert len(winners) == 1
    # priority list is [other-source, fixture-source] — non-lexical
    assert winners[0]["source_id"] == "other-source"

    groups = fe["observable_groups"]
    assert len(groups) == 1
    dex = groups[0]["disagreement_dex"]
    assert dex is not None
    assert dex == pytest.approx(math.log10(0.24 / 0.02))


def test_unlisted_source_cannot_win_lexically(tmp_extracts: Path):
    """Null-hypothesis (P1 VALUE-PRECEDENCE fail-open): unlisted → lexical winner."""
    a = _minimal_extract()
    a["source_id"] = "zzz-unlisted"
    a["species"]["Fe"]["observations"][0]["observation_id"] = "fe_z"
    b = _minimal_extract()
    b["source_id"] = "aaa-unlisted"
    b["species"]["Fe"]["observations"][0]["observation_id"] = "fe_a"
    b["species"]["Fe"]["observations"][0]["values"] = {"alpha": 0.01}
    b["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_a].values.alpha",
            "value": 0.01,
            "note": "n",
            "locator": {"page": 1},
        }
    ]
    a["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_z].values.alpha",
            "value": 0.24,
            "note": "n",
            "locator": {"page": 1},
        }
    ]
    _write_extract(tmp_extracts, a)
    _write_extract(tmp_extracts, b)
    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    priority = em.load_source_priority(tmp_extracts / "_source_priority.yaml")
    view = em.build_by_species(extracts, source_priority=priority)
    winners = [o for o in view["species"]["Fe"]["observations"] if o.get("is_priority_winner")]
    assert winners == []  # neither listed in alpha priority


def test_empty_payload_cannot_win(tmp_extracts: Path):
    """Null-hypothesis (P1): empty Ga2O-like row outranked populated competitor."""
    empty = _minimal_extract()
    empty["source_id"] = "other-source"  # higher priority
    empty["species"]["Fe"]["observations"][0]["observation_id"] = "fe_empty"
    # Bypass validator for this unit: inject empty after load path via build direct
    empty["species"]["Fe"]["observations"][0]["values"] = {"alpha": None, "note": None}
    # actually null-only leaves fail _has_payload_leaf if all null
    empty["species"]["Fe"]["observations"][0]["values"] = {}
    # Build without validator
    populated = _minimal_extract()
    populated["source_id"] = "fixture-source"
    extracts = [empty, populated]
    priority = em.load_source_priority(tmp_extracts / "_source_priority.yaml")
    view = em.build_by_species(extracts, source_priority=priority)
    winners = [o for o in view["species"]["Fe"]["observations"] if o.get("is_priority_winner")]
    assert len(winners) == 1
    assert winners[0]["source_id"] == "fixture-source"
    assert winners[0]["values"] == {"alpha": 0.24}


def test_regime_difference_not_merged_as_one_conflict(tmp_extracts: Path):
    a = _minimal_extract()
    b = _minimal_extract()
    b["source_id"] = "other-source"
    b["species"]["Fe"]["observations"][0]["observation_id"] = "fe_alpha_kems"
    b["species"]["Fe"]["observations"][0]["regime"] = "kems_effusion"
    b["species"]["Fe"]["observations"][0]["values"] = {"alpha": 0.02}
    b["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_alpha_kems].values.alpha",
            "value": 0.02,
            "note": "n",
            "locator": {"page": 1},
        }
    ]
    _write_extract(tmp_extracts, a)
    _write_extract(tmp_extracts, b)

    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    view = em.build_by_species(extracts)
    groups = view["species"]["Fe"]["observable_groups"]
    assert len(groups) == 2  # different regimes → separate groups


def test_phase_and_property_split_observables(tmp_extracts: Path):
    """Null-hypothesis (P1-M1): (type, regime) only mixed mp+bp / gas+solid."""
    a = _minimal_extract()
    a["species"]["Fe"]["observations"] = [
        {
            "observation_id": "fe_mp",
            "type": "transition_point",
            "locator": {"page": 1},
            "units": "K",
            "phase": "solid_liquid",
            "values": {"property": "melting_point", "T_K": 1809.0},
        },
        {
            "observation_id": "fe_bp",
            "type": "transition_point",
            "locator": {"page": 1},
            "units": "K",
            "phase": "liquid_gas",
            "values": {"property": "boiling_point", "T_K": 3134.0},
        },
    ]
    a["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_mp].values.T_K",
            "value": 1809.0,
            "note": "n",
            "locator": {"page": 1},
        }
    ]
    _write_extract(tmp_extracts, a)
    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    view = em.build_by_species(
        extracts, source_priority=em.load_source_priority(tmp_extracts / "_source_priority.yaml")
    )
    groups = view["species"]["Fe"]["observable_groups"]
    assert len(groups) == 2
    assert all(g.get("disagreement_dex") is None for g in groups)


def test_uncertainty_propagated_to_by_species(tmp_extracts: Path):
    """Owner 2026-08-02: uncertainty is first-class; merge propagates verbatim."""
    a = _minimal_extract()
    _write_extract(tmp_extracts, a)
    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    view = em.build_by_species(
        extracts, source_priority=em.load_source_priority(tmp_extracts / "_source_priority.yaml")
    )
    obs = view["species"]["Fe"]["observations"][0]
    assert obs["uncertainty"] == {"note": "fixture ±10% band", "relative": 0.1}
    group = view["species"]["Fe"]["observable_groups"][0]
    assert group["winner_uncertainty"] == obs["uncertainty"]
    assert group["uncertainties"]
    assert group["uncertainties"][0]["uncertainty"]["relative"] == 0.1


def test_confidence_level_vocabulary_is_closed_and_optional() -> None:
    doc = _minimal_extract()
    observation = doc["species"]["Fe"]["observations"][0]
    assert vle.validate_extract_document(doc) == []

    observation["uncertainty"]["confidence_level"] = "one_sigma"
    assert vle.validate_extract_document(doc) == []

    observation["uncertainty"]["confidence_level"] = "one standard deviation"
    errors = vle.validate_extract_document(doc)
    assert any("uncertainty.confidence_level" in error for error in errors)


def test_consistency_report_auto_computes_disagreement(tmp_extracts: Path):
    """Owner 2026-08-02: computed disagreement_dex, no hand curation."""
    a = _minimal_extract()
    b = _minimal_extract()
    b["source_id"] = "other-source"
    b["species"]["Fe"]["observations"][0]["observation_id"] = "fe_other"
    b["species"]["Fe"]["observations"][0]["values"] = {"alpha": 0.02}
    b["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_other].values.alpha",
            "value": 0.02,
            "note": "n",
            "locator": {"page": 1},
        }
    ]
    _write_extract(tmp_extracts, a)
    _write_extract(tmp_extracts, b)
    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    priority = em.load_source_priority(tmp_extracts / "_source_priority.yaml")
    report = em.build_consistency_report(extracts, source_priority=priority)
    assert report["kind"] == "cross_source_consistency"
    assert report["n_multi_source_groups"] >= 1
    assert report["n_with_disagreement_dex"] >= 1
    conflict = report["conflicts"][0]
    assert conflict["species_id"] == "Fe"
    assert conflict["disagreement_dex"] == pytest.approx(math.log10(0.24 / 0.02))
    assert "uncertainties" in conflict


def test_coverage_payload_aware(tmp_extracts: Path):
    """Null-hypothesis (P2-M2 / KM-M2): empty / pointer payloads counted as found.

    Empty observation lists and empty/null-only values report **empty**
    (species present, no usable datum), distinct from **absent** (species not
    listed on the source). Pending/pointer acquisition stubs also report empty.
    """
    a = _minimal_extract()
    # empty payload species via direct build (bypass validator)
    empty_doc = {
        "source_id": "empty-src",
        "species": {
            "As4O6": {
                "observations": [
                    {
                        "observation_id": "pending",
                        "type": "psat_series",
                        "values": {},
                    }
                ]
            },
            # Empty observation list: present but unusable → empty, not found.
            "Bi2O3": {"observations": []},
        },
    }
    # Structured pointer stub (quantity + planned T window) — no measured datum.
    pending_doc = {
        "source_id": "pending-as2o3-second-primary",
        "species": {
            "As4O6": {
                "observations": [
                    {
                        "observation_id": "anchor_As4O6_pure_Psat",
                        "type": "psat_series",
                        "locator": {
                            "record": "PENDING#As2O3_second_primary",
                            "note": "planned second primary",
                        },
                        "units": "as published",
                        "values": {
                            "quantity": "pure_Psat",
                            "T_range_K": [369, 730],
                            "phase": "solid_As2O3_arsenolite_to_As4O6_g",
                            "standard_state": "As4O6(g); condensed arsenolite",
                        },
                    }
                ]
            }
        },
    }
    # Explicit pointer semantics (real acquired source, pointer-only row).
    pointer_doc = {
        "source_id": "pointer-src",
        "species": {
            "NaCl": {
                "observations": [
                    {
                        "observation_id": "nacl_side",
                        "type": "gibbs_table",
                        "values": {
                            "role": "monomer_side",
                            "semantics": "pointer_to_dimer_equilibrium",
                        },
                    }
                ]
            }
        },
    }
    cov = em.build_coverage(
        [a, empty_doc, pending_doc, pointer_doc],
        manifest_ids=["Fe", "As4O6", "Bi2O3", "NaCl", "SiO"],
    )
    by_id = {r["species_id"]: r["sources"] for r in cov["rows"]}
    assert by_id["Fe"]["fixture-source"] == "found"
    # Empty values payload → empty (not found, not absent).
    assert by_id["As4O6"]["empty-src"] == "empty"
    # Empty observation list → empty, distinct from absent.
    assert by_id["Bi2O3"]["empty-src"] == "empty"
    assert by_id["Bi2O3"]["fixture-source"] == "absent"
    # Pending stub with structured pointer payload → empty, not found.
    assert by_id["As4O6"]["pending-as2o3-second-primary"] == "empty"
    # Explicit pointer semantics → empty.
    assert by_id["NaCl"]["pointer-src"] == "empty"
    # Species never listed on a source → absent.
    assert by_id["SiO"]["fixture-source"] == "absent"
    assert by_id["SiO"]["empty-src"] == "absent"
    assert cov["cells_empty"] >= 4


def test_coverage_found_absent(tmp_extracts: Path):
    a = _minimal_extract()
    _write_extract(tmp_extracts, a)
    extracts = em.load_extracts(tmp_extracts, require_valid=True)
    cov = em.build_coverage(extracts, manifest_ids=["Fe", "Na", "SiO"])
    assert cov["source_ids"] == ["fixture-source"]
    by_id = {r["species_id"]: r["sources"] for r in cov["rows"]}
    assert by_id["Fe"]["fixture-source"] == "found"
    assert by_id["Na"]["fixture-source"] == "absent"
    assert by_id["SiO"]["fixture-source"] == "absent"


def test_coverage_pending_stubs_not_found():
    """Null-hypothesis (KM-M2 STILL-OPEN): all 13 pending-* stubs report found.

    Pending acquisition stubs carry structured pointer payloads (quantity label
    + planned T_range_K) that pass ``_payload_present``; coverage must not
    overstate acquisition where acquisition is still pending.
    """
    extracts = em.load_extracts(EXTRACTS, require_valid=True)
    cov = em.build_coverage(extracts)
    pending_srcs = [s for s in cov["source_ids"] if str(s).startswith("pending-")]
    assert len(pending_srcs) == 13, f"expected 13 pending stubs, got {pending_srcs}"
    found_pending = [
        (row["species_id"], src, state)
        for row in cov["rows"]
        for src, state in row["sources"].items()
        if str(src).startswith("pending-") and state == "found"
    ]
    assert found_pending == [], f"pending stubs still report found: {found_pending}"
    # At least one pending cell is empty (species listed, no measured datum).
    empty_pending = [
        (row["species_id"], src)
        for row in cov["rows"]
        for src, state in row["sources"].items()
        if str(src).startswith("pending-") and state == "empty"
    ]
    assert len(empty_pending) == 13, empty_pending


def test_merge_cli_writes_and_uses_custom_priority(tmp_path: Path, tmp_extracts: Path):
    """Null-hypothesis (P2): custom extracts dir silently used repo-global priority."""
    a = _minimal_extract()
    b = _minimal_extract()
    b["source_id"] = "other-source"
    b["species"]["Fe"]["observations"][0]["observation_id"] = "fe_other"
    b["species"]["Fe"]["observations"][0]["values"] = {"alpha": 0.02}
    b["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_other].values.alpha",
            "value": 0.02,
            "note": "n",
            "locator": {"page": 1},
        }
    ]
    _write_extract(tmp_extracts, a)
    _write_extract(tmp_extracts, b)
    outdir = tmp_path / "out"
    rc = em.main(
        [
            "--by-species",
            "--coverage",
            "--consistency",
            "--outdir",
            str(outdir),
            "--extracts-dir",
            str(tmp_extracts),
        ]
    )
    assert rc == 0
    assert (outdir / "by_species.yaml").is_file()
    assert (outdir / "coverage.yaml").is_file()
    assert (outdir / "consistency.yaml").is_file()
    view = load_cached_safe_yaml((outdir / "by_species.yaml").read_text())
    winners = [
        o for o in view["species"]["Fe"]["observations"] if o.get("is_priority_winner")
    ]
    assert winners[0]["source_id"] == "other-source"  # custom priority, not lexical


def test_merge_refuses_invalid_extracts(tmp_extracts: Path):
    bad = _minimal_extract()
    bad["schema_version"] = "nope"
    _write_extract(tmp_extracts, bad)
    with pytest.raises(SystemExit):
        em.load_extracts(tmp_extracts, require_valid=True)


def _corpus_stems_including_aliases() -> set[str]:
    """Extract filename stems plus ``aliases:`` recorded on canonical extracts.

    One DOI = one source_id: a merged alias file is git-rm'd, but the pilot
    stem remains present as an alias on the surviving canonical extract.
    """
    present: set[str] = set()
    for path in vle.discover_extracts():
        present.add(path.stem)
        doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        aliases = doc.get("aliases") or []
        if isinstance(aliases, list):
            present.update(a for a in aliases if isinstance(a, str) and a)
    return present


def test_pilot_extract_count_exact_and_merge_smoke():
    """Pilot census (68) remains; live corpus may grow with post-policy extracts.

    Merge produces species + coverage + consistency over the full live set.
    """
    files = vle.discover_extracts()
    policy = _fidelity_policy_doc()
    closed = set(policy["closed_set_source_ids"])
    present = _corpus_stems_including_aliases()
    assert len(closed) == 68  # frozen pilot census
    assert closed - RETIRED_DUPLICATE_SOURCE_IDS <= present
    assert RETIRED_DUPLICATE_SOURCE_IDS.isdisjoint({path.stem for path in files})
    assert len(files) >= 68  # live corpus may include t-509 OCR extracts
    extracts = em.load_extracts(require_valid=True)
    assert len(extracts) == len(files)
    view = em.build_by_species(extracts)
    assert "species" in view and len(view["species"]) >= 1
    if "Fe" in view["species"]:
        types = {o["type"] for o in view["species"]["Fe"]["observations"]}
        assert "alpha" in types or "gibbs_table" in types or "transition_point" in types
    cov = em.build_coverage(extracts)
    assert cov["source_count"] == len(files)
    assert cov["cells_found"] >= 1
    report = em.build_consistency_report(extracts)
    assert report["kind"] == "cross_source_consistency"
    assert "conflicts" in report


# ---------------------------------------------------------------------------
# Migrator fidelity pins (P1-F1 / F2 / F3 / Datz / CEA)
# ---------------------------------------------------------------------------


def test_alpha_form_retained_in_pilot_extracts():
    """Null-hypothesis (P1-F1): alpha_form dropped → null alphas for Wetzel/Richter."""
    w = load_cached_safe_yaml(
        (EXTRACTS / "wetzel-gail-2013-sio-arrhenius.yaml").read_text(encoding="utf-8")
    )
    form = w["species"]["SiO"]["observations"][0]["values"].get("alpha_form")
    assert form is not None
    assert form["A"] == pytest.approx(0.52)
    assert form["B_K"] == pytest.approx(3685.0)
    unc = w["species"]["SiO"]["observations"][0].get("uncertainty") or {}
    assert unc.get("uncertainty_envelope") == [0.003, 0.067]

    r = load_cached_safe_yaml((EXTRACTS / "richter-et-al-2007.yaml").read_text(encoding="utf-8"))
    assert r["source"]["year"] == 2007
    mg = r["species"]["Mg"]["observations"][0]["values"]["alpha_form"]
    assert mg["c0"] == pytest.approx(143.0)


def test_janaf_species_not_list_index_keys():
    """Null-hypothesis (P1-F2): PO rows keyed as tabulated_delta_fG_kJ_mol[0]."""
    j = load_cached_safe_yaml((EXTRACTS / "janaf-4th.yaml").read_text(encoding="utf-8"))
    assert "PO" in j["species"]
    assert not any("[" in k or k.startswith("tabulated_") for k in j["species"])
    po_obs = j["species"]["PO"]["observations"]
    tab = next(
        o
        for o in po_obs
        if "formation_tabulation" in str(o.get("observation_id"))
        or "tabulated_delta_fG" in str(o.get("values"))
    )
    assert tab["type"] == "gibbs_table"
    assert "tabulated_delta_fG_kJ_mol" in (tab.get("values") or {})
    assert tab["locator"].get("table") or tab["locator"].get("note")
    assert tab["locator"].get("note") != "{}"


def test_datz_recorded_disagreement_retained():
    """Null-hypothesis (P1): competing JANAF/WebBook observation dropped pre-merge."""
    d = load_cached_safe_yaml(
        (EXTRACTS / "datz-and-smith-1961.yaml").read_text(encoding="utf-8")
    )
    ids = [o["observation_id"] for o in d["species"]["Na2Cl2"]["observations"]]
    assert "Datz1961_TableII_Kd" in ids
    assert any("JANAF" in i or "Shomate" in i for i in ids)


def test_cea_source_ref_and_relative_provenance():
    """Null-hypothesis (P2): Ag source_ref_code truncated; absolute Dropbox path."""
    c = load_cached_safe_yaml((EXTRACTS / "nasa-cea-thermo.yaml").read_text(encoding="utf-8"))
    assert c["species"]["Ag"]["observations"][0]["values"]["source_ref_code"] == "g10/97"
    prov = c["extraction"]["provenance_path"]
    assert not prov.startswith("/")
    assert "docs-private" in prov


def test_lh84_top_level_payloads_not_empty():
    """Null-hypothesis (P1-F3): top-level coefficients/points migrated as values:{}."""
    # Stull Se Antoine must not live under LH84 (wrong-source); coefficients retained
    # under nist-webbook or a dedicated extract.
    nist = load_cached_safe_yaml((EXTRACTS / "nist-webbook.yaml").read_text(encoding="utf-8"))
    # Se may be under Se_n_ladder
    found_coeff = False
    for sid, block in nist.get("species", {}).items():
        for obs in block.get("observations") or []:
            vals = obs.get("values") or {}
            if isinstance(vals, dict) and "coefficients" in vals:
                found_coeff = True
                coeffs = vals["coefficients"]
                assert coeffs  # non-empty
    br = EXTRACTS / "behrens-rosenblatt-1972.yaml"
    assert br.is_file()
    br_doc = load_cached_safe_yaml(br.read_text(encoding="utf-8"))
    as4 = br_doc["species"]["As4O6"]["observations"][0]["values"]
    assert "coefficients" in as4
    assert as4["coefficients"].get("A") is not None or "A" in str(as4["coefficients"])

    lh = load_cached_safe_yaml(
        (EXTRACTS / "lamoreaux-hildenbrand-1984.yaml").read_text(encoding="utf-8")
    )
    for sid, block in lh["species"].items():
        for obs in block.get("observations") or []:
            assert obs.get("values"), f"empty values under LH84 {sid} {obs.get('observation_id')}"


def test_corr_payload_helper_merges_top_level():
    """Unit pin for P1-F3 payload collection."""
    corr = {
        "id": "x",
        "kind": "pure_component_antoine",
        "coefficients": {"A": 6.3, "B": 6500, "C": 80},
        "source": {"citation": "Stull"},
        "values": {},
    }
    payload = mig._corr_payload(corr)
    assert payload["coefficients"]["A"] == pytest.approx(6.3)


def test_every_extract_has_fidelity_sample():
    """Pilot corpus currently carries path-based pins; keep them as drift fuses.

    ENFORCED_FOR_NEW permits allowlisted extracts to omit samples, but the
    landed pilot set still has auto-migrated pins — assert they remain so a
    silent strip does not go unnoticed.
    """
    for path in vle.discover_extracts():
        doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
        if doc.get("schema_version") == vle.TABLE_SCHEMA_VERSION:
            continue  # Companion tables rely on fidelity samples in their parent extract.
        n_obs = sum(
            len(b.get("observations") or [])
            for b in (doc.get("species") or {}).values()
            if isinstance(b, dict)
        )
        if n_obs:
            assert doc.get("fidelity_samples"), f"{path.name} missing fidelity_samples"


def test_costa_jacobson_retained_sio_has_fidelity_sample(monkeypatch):
    """Run the landed-pilot sample-presence guard against this extract alone."""
    path = EXTRACTS / "costa-jacobson-2015.yaml"
    monkeypatch.setattr(vle, "discover_extracts", lambda: [path])
    test_every_extract_has_fidelity_sample()


# ---------------------------------------------------------------------------
# t-510 fidelity gate: policy + parameterized match + mutation red
# ---------------------------------------------------------------------------


def _fidelity_policy_doc() -> dict:
    assert FIDELITY_POLICY.is_file(), f"missing {FIDELITY_POLICY}"
    doc = load_cached_safe_yaml(FIDELITY_POLICY.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def test_fidelity_policy_file_valid():
    """Policy file loads; active ⊆ closed; partition with graduated; fields correct."""
    policy, errs = vle.load_fidelity_policy()
    assert errs == [], errs
    assert policy["policy"] == "ENFORCED_FOR_NEW"
    assert policy["effective_date"] == "2026-08-03"
    closed = set(policy["closed_set_source_ids"])
    active = set(policy["active_pre_policy_source_ids"])
    graduated = set(policy.get("graduated_pre_policy_source_ids") or [])
    assert active <= closed
    assert graduated <= closed
    assert active & graduated == set()
    assert active | graduated == closed
    assert len(closed) == 68


def test_fidelity_graduation_ledger_matches_policy_and_hash():
    """Canonical history mirrors tombstones and is independently hash-pinned."""
    ledger, ledger_errs = vle.load_fidelity_graduation_ledger(
        FIDELITY_GRADUATION_LEDGER
    )
    assert ledger_errs == [], ledger_errs
    policy = _fidelity_policy_doc()
    assert ledger == set(policy.get("graduated_pre_policy_source_ids") or [])
    payload = "\n".join(sorted(ledger)).encode("utf-8")
    assert hashlib.sha256(payload).hexdigest() == GRADUATION_LEDGER_SHA256


def test_fidelity_closed_set_hash_pinned():
    """Null-hypothesis: closed set can grow (extract ADDED to allowlist later).

    The closed set is frozen at policy adoption. Any addition changes the
    SHA-256 of the sorted joined id list and this test goes RED.
    """
    policy = _fidelity_policy_doc()
    closed = policy["closed_set_source_ids"]
    payload = "\n".join(sorted(closed)).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    assert digest == CLOSED_SET_SHA256, (
        f"closed_set_source_ids changed (shrink-only violation or hash drift).\n"
        f"expected={CLOSED_SET_SHA256}\nactual={digest}\n"
        f"count={len(closed)}"
    )


def test_fidelity_allowlist_rejects_active_not_subset(tmp_path: Path):
    """Null-hypothesis: active can include ids outside closed (growth via active)."""
    bad = {
        "schema_version": "literature_extract_fidelity_policy.v1",
        "policy": "ENFORCED_FOR_NEW",
        "effective_date": "2026-08-03",
        "closed_set_source_ids": ["a", "b"],
        "active_pre_policy_source_ids": ["a", "sneaky-new-extract"],
        "graduated_pre_policy_source_ids": ["b"],
    }
    p = tmp_path / "_fidelity_pre_policy_allowlist.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    _policy, errs = vle.load_fidelity_policy(p)
    assert any("shrink-only" in e or "not ⊆" in e for e in errs), errs


def test_fidelity_allowlist_rejects_remove_then_readd(tmp_path: Path):
    """Null-hypothesis (P2): graduated id can return to active after removal.

    Shrink without tombstone, then re-add, must go RED — either because the
    graduated set still contains the id, or because a silent remove without
    graduation breaks the closed-set partition.
    """
    # Step 1: legal graduation of b.
    graduated = {
        "schema_version": "literature_extract_fidelity_policy.v1",
        "policy": "ENFORCED_FOR_NEW",
        "effective_date": "2026-08-03",
        "closed_set_source_ids": ["a", "b"],
        "active_pre_policy_source_ids": ["a"],
        "graduated_pre_policy_source_ids": ["b"],
    }
    p = tmp_path / "_fidelity_pre_policy_allowlist.yaml"
    ledger_path = tmp_path / "_fidelity_graduation_ledger.yaml"
    ledger_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_extract_fidelity_graduation_ledger.v1",
                "graduated_pre_policy_source_ids": ["b"],
            }
        ),
        encoding="utf-8",
    )
    p.write_text(yaml.safe_dump(graduated), encoding="utf-8")
    _ok, errs_ok = vle.load_fidelity_policy(p)
    assert errs_ok == [], errs_ok

    # Step 2: illegal re-activation of b while still tombstoned.
    reactivated = dict(graduated)
    reactivated["active_pre_policy_source_ids"] = ["a", "b"]
    p.write_text(yaml.safe_dump(reactivated), encoding="utf-8")
    _bad, errs = vle.load_fidelity_policy(p)
    assert any("re-activation" in e or "∩" in e for e in errs), errs

    # Silent remove without tombstone also refused (partition incomplete).
    silent = {
        "schema_version": "literature_extract_fidelity_policy.v1",
        "policy": "ENFORCED_FOR_NEW",
        "effective_date": "2026-08-03",
        "closed_set_source_ids": ["a", "b"],
        "active_pre_policy_source_ids": ["a"],
        "graduated_pre_policy_source_ids": [],
    }
    p.write_text(yaml.safe_dump(silent), encoding="utf-8")
    _silent, silent_errs = vle.load_fidelity_policy(p)
    assert any("graduate" in e or "missing" in e for e in silent_errs), silent_errs


def test_fidelity_allowlist_rejects_tombstone_delete_then_reactivate(tmp_path: Path):
    """Null-hypothesis (P2): deleting a tombstone permits reactivation.

    Committed Git history is independent of both mutable current-state files.
    Once ``b`` is recorded, no rewrite may make it active again, including
    deleting it from both the policy tombstones and canonical ledger together.
    """
    policy_path = tmp_path / "_fidelity_pre_policy_allowlist.yaml"
    ledger_path = tmp_path / "_fidelity_graduation_ledger.yaml"
    graduated = {
        "schema_version": "literature_extract_fidelity_policy.v1",
        "policy": "ENFORCED_FOR_NEW",
        "effective_date": "2026-08-03",
        "closed_set_source_ids": ["a", "b"],
        "active_pre_policy_source_ids": ["a"],
        "graduated_pre_policy_source_ids": ["b"],
    }
    ledger = {
        "schema_version": "literature_extract_fidelity_graduation_ledger.v1",
        "graduated_pre_policy_source_ids": ["b"],
    }
    policy_path.write_text(yaml.safe_dump(graduated), encoding="utf-8")
    ledger_path.write_text(yaml.safe_dump(ledger), encoding="utf-8")
    _ok, ok_errs = vle.load_fidelity_policy(
        policy_path,
        graduated_ledger_path=ledger_path,
        prior_graduated_source_ids={"b"},
    )
    assert ok_errs == [], ok_errs

    reactivated = dict(graduated)
    reactivated["active_pre_policy_source_ids"] = ["a", "b"]
    reactivated["graduated_pre_policy_source_ids"] = []
    policy_path.write_text(yaml.safe_dump(reactivated), encoding="utf-8")
    # Delete the mutable ledger entry too. Only external prior history can
    # distinguish this recreated initial-looking state from a legitimate one.
    ledger["graduated_pre_policy_source_ids"] = []
    ledger_path.write_text(yaml.safe_dump(ledger), encoding="utf-8")
    _bad, errs = vle.load_fidelity_policy(
        policy_path,
        graduated_ledger_path=ledger_path,
        prior_graduated_source_ids={"b"},
    )
    assert any("prior Git history" in e or "prior canonical" in e for e in errs), errs


def test_default_policy_validation_uses_committed_graduation_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Null-hypothesis (P2): prior-state checking exists only in a test-only API."""
    policy_path = tmp_path / "_fidelity_pre_policy_allowlist.yaml"
    ledger_path = tmp_path / "_fidelity_graduation_ledger.yaml"
    policy_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_extract_fidelity_policy.v1",
                "policy": "ENFORCED_FOR_NEW",
                "effective_date": "2026-08-03",
                "closed_set_source_ids": ["a", "b"],
                "active_pre_policy_source_ids": ["a", "b"],
                "graduated_pre_policy_source_ids": [],
            }
        ),
        encoding="utf-8",
    )
    ledger_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_extract_fidelity_graduation_ledger.v1",
                "graduated_pre_policy_source_ids": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(vle, "FIDELITY_POLICY_PATH", policy_path)
    monkeypatch.setattr(vle, "FIDELITY_GRADUATION_LEDGER_PATH", ledger_path)
    monkeypatch.setattr(
        vle,
        "load_committed_fidelity_graduation_history",
        lambda _path: ({"b"}, []),
    )
    _policy, errs = vle.load_fidelity_policy()
    assert any("prior Git history" in e or "prior canonical" in e for e in errs), errs


def test_fidelity_allowlist_covers_pilot_census():
    """Frozen pilot census remains present; live corpus may grow past it.

    Null-hypothesis (P1): equating live stems to the closed set blocks the
    first compliant t-509 extract. Require closed ⊆ stems instead, and allow
    additional non-allowlisted stems that carry samples.
    """
    policy = _fidelity_policy_doc()
    closed = set(policy["closed_set_source_ids"])
    active = set(policy["active_pre_policy_source_ids"])
    stems = {p.stem for p in vle.discover_extracts()}
    present = _corpus_stems_including_aliases()
    # Frozen pilot set still present in the live corpus (file stem or extract alias).
    assert closed - RETIRED_DUPLICATE_SOURCE_IDS <= present
    assert RETIRED_DUPLICATE_SOURCE_IDS.isdisjoint(stems)
    assert active <= closed
    # New (post-policy) stems are allowed and must not be on the active allowlist.
    new_stems = stems - closed
    assert new_stems.isdisjoint(active)


def test_new_extract_with_valid_sample_admitted():
    """Acceptance: a 69th non-allowlisted extract with a valid sample validates.

    Null-hypothesis (P1 census): the suite treated live corpus == closed set,
    so any new stem made the suite red even with reviewer-verified samples.
    """
    doc = _minimal_extract()
    doc["source_id"] = "brand-new-ocr-source-2026"
    # Structured observation pin (OCR form) — required for new extracts.
    doc["fidelity_samples"] = [
        {
            "species": "Fe",
            "observable": "alpha",
            "observation_id": "fe_alpha_1",
            "field": "alpha",
            "value": 0.24,
            "locator": {"page": 2, "table": "1"},
        }
    ]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="brand-new-ocr-source-2026",
        pre_policy_ids=set(),  # not allowlisted
        check_fidelity_match=True,
    )
    assert errs == [], errs


def test_new_extract_metadata_only_path_sample_refused():
    """Null-hypothesis (P1): path samples can pin metadata instead of evidence.

    A non-allowlisted extract with only a path pin on source_id used to
    validate green under ENFORCED_FOR_NEW without checking a publication value.
    """
    doc = _minimal_extract()
    doc["source_id"] = "fixture-source"
    doc["fidelity_samples"] = [
        {
            "path": "source_id",
            "value": "fixture-source",
            "note": "not an observation pin",
        }
    ]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("observation evidence" in e or "metadata" in e for e in errs), errs


def test_new_extract_mixed_metadata_path_and_structured_sample_refused():
    """Null-hypothesis (P1): structured decoration legitimizes a metadata path.

    Addressing mode must be unambiguous and every present path must identify
    observation evidence, independent of any additional sample fields.
    """
    doc = _minimal_extract()
    doc["source_id"] = "fixture-source"
    doc["fidelity_samples"] = [
        {
            "path": "source_id",
            "species": "Fe",
            "observation_id": "fe_alpha_1",
            "observable": "alpha",
            "field": "alpha",
            "value": "fixture-source",
            "locator": {"page": 2},
        }
    ]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("addressing mode" in e or "observation evidence" in e for e in errs), errs
    direct_errs = vle.check_fidelity_sample_matches(
        doc, doc["fidelity_samples"][0], label="mixed-addressing"
    )
    assert any("addressing mode" in e for e in direct_errs), direct_errs


@pytest.mark.parametrize(
    "conflicting_keys",
    [
        {"value_key": "bogus"},
        {"draft_value": 999.0},
        {"index": 0, "T_K": 1700.0},
        {"T_K": 1700.0, "T": 1800.0},
    ],
    ids=["field-value_key", "value-draft_value", "index-temperature", "T_K-T"],
)
def test_fidelity_rejects_conflicting_structured_selectors(conflicting_keys: dict):
    """Null-hypothesis (P1): precedence silently ignores contradictory selectors."""
    doc = _minimal_extract()
    sample = {
        "species": "Fe",
        "observation_id": "fe_alpha_1",
        "observable": "alpha",
        "field": "alpha",
        "value": 0.24,
        "locator": {"page": 2},
        **conflicting_keys,
    }
    doc["fidelity_samples"] = [sample]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("exactly one" in e or "not both" in e for e in errs), errs
    assert vle.check_fidelity_sample_matches(doc, sample), sample


def test_fidelity_observation_id_and_observable_must_agree():
    """Null-hypothesis (P1): observation_id precedence ignores wrong observable."""
    doc = _minimal_extract()
    sample = {
        "species": "Fe",
        "observation_id": "fe_alpha_1",
        "observable": "psat_series",
        "field": "alpha",
        "value": 0.24,
        "locator": {"page": 2},
    }
    doc["fidelity_samples"] = [sample]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("not requested observable" in e for e in errs), errs
    assert vle.check_fidelity_sample_matches(doc, sample)


@pytest.mark.parametrize(
    "source_locator",
    [{"page": 999}, None],
    ids=["contradictory", "null-decoration"],
)
def test_fidelity_rejects_locator_alias_decoration(source_locator):
    """Null-hypothesis (P1): locator precedence ignores source_locator."""
    doc = _minimal_extract()
    sample = copy.deepcopy(doc["fidelity_samples"][0])
    sample["source_locator"] = source_locator
    doc["fidelity_samples"] = [sample]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("exactly one" in e or "source_locator" in e for e in errs), errs
    assert vle.check_fidelity_sample_matches(doc, sample)


@pytest.mark.parametrize(
    "rel_tol", [True, float("inf"), float("nan"), -0.1, None, "0.1"]
)
def test_fidelity_rejects_invalid_relative_tolerance(rel_tol):
    """Null-hypothesis (P2): invalid tolerance bypasses or crashes exact match."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"] = {"alpha": 10.0}
    sample = {
        "path": "species.Fe.observations[fe_alpha_1].values.alpha",
        "value": 20.0,
        "rel_tol": rel_tol,
        "note": "invalid tolerance",
        "locator": {"page": 1},
    }
    doc["fidelity_samples"] = [sample]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("rel_tol" in e for e in errs), errs
    direct_errs = vle.check_fidelity_sample_matches(doc, sample)
    assert any("rel_tol" in e for e in direct_errs), direct_errs


def test_fidelity_accepts_bounded_relative_tolerance_without_type_coercion():
    """A finite explicit tolerance relaxes magnitude only for equal numeric types."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"] = {"alpha": 10.0}
    sample = {
        "path": "species.Fe.observations[fe_alpha_1].values.alpha",
        "value": 10.001,
        "rel_tol": 0.001,
        "note": "bounded tolerance",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(doc, sample) == []
    sample["value"] = 10
    assert vle.check_fidelity_sample_matches(doc, sample), "rel_tol must not coerce int"


def test_new_extract_null_sample_value_refused():
    """Null-hypothesis (P1): a null pin satisfies the mandatory fidelity gate."""
    doc = _minimal_extract()
    # Add a null sibling field and pin it.
    doc["species"]["Fe"]["observations"][0]["values"]["sibling_null"] = None
    doc["fidelity_samples"] = [
        {
            "species": "Fe",
            "observation_id": "fe_alpha_1",
            "observable": "alpha",
            "field": "sibling_null",
            "value": None,
            "locator": {"page": 2, "table": "1"},
        }
    ]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("null" in e for e in errs), errs


@pytest.mark.parametrize("empty_scalar", ["", "   "])
def test_new_extract_empty_scalar_sample_value_refused(empty_scalar: str):
    """Null-hypothesis (P1): empty scalar pins satisfy the fidelity gate."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"]["empty_scalar"] = empty_scalar
    doc["fidelity_samples"] = [
        {
            "species": "Fe",
            "observation_id": "fe_alpha_1",
            "observable": "alpha",
            "field": "empty_scalar",
            "value": empty_scalar,
            "locator": {"page": 2, "table": "1"},
        }
    ]
    errs = vle.validate_extract_document(
        doc,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("non-null observed pin" in e or "empty" in e for e in errs), errs


@pytest.mark.parametrize(
    "empty_scalar",
    [
        yaml.safe_load("!!binary ''"),
        yaml.safe_load("!!binary ICAg"),
        yaml.safe_load("!!set {}"),
    ],
    ids=["empty-binary", "whitespace-binary", "empty-set"],
)
def test_new_extract_serialized_empty_scalar_sample_value_refused(empty_scalar):
    """Null-hypothesis (P1): alternate YAML empty scalar types stay green."""
    doc = _minimal_extract()
    body_value = copy.deepcopy(empty_scalar)
    doc["species"]["Fe"]["observations"][0]["values"] = {
        "empty_scalar": body_value
    }
    sample = {
        "species": "Fe",
        "observation_id": "fe_alpha_1",
        "observable": "alpha",
        "field": "empty_scalar",
        "value": copy.deepcopy(empty_scalar),
        "locator": {"page": 2},
    }
    doc["fidelity_samples"] = [sample]
    loaded = yaml.safe_load(yaml.safe_dump(doc, sort_keys=False))
    errs = vle.validate_extract_document(
        loaded,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("non-null observed pin" in e or "empty" in e for e in errs), errs
    direct_errs = vle.check_fidelity_sample_matches(
        loaded, loaded["fidelity_samples"][0]
    )
    assert any("non-null observed pin" in e or "empty" in e for e in direct_errs)


def test_fidelity_shared_alias_identity_rejected():
    """Null-hypothesis (P1): YAML-aliased sample co-mutates with body → always green."""
    doc = _minimal_extract()
    values = doc["species"]["Fe"]["observations"][0]["values"]
    doc["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_alpha_1].values",
            "value": values,  # shared object identity (alias)
            "note": "aliased pin",
            "locator": {"page": 2},
        }
    ]
    # Match helper must refuse shared identity even though values "equal".
    match_errs = vle.check_all_fidelity_samples_match(doc, label="alias")
    assert any("identity" in e or "alias" in e for e in match_errs), match_errs
    # Shape path on new extracts also refuses.
    shape_errs = vle.validate_extract_document(
        doc, expected_source_id="fixture-source", pre_policy_ids=set()
    )
    assert any("identity" in e or "alias" in e for e in shape_errs), shape_errs


def test_fidelity_nested_serialized_alias_identity_rejected():
    """Null-hypothesis (P1): nested YAML aliases co-mutate and stay green.

    Alias identity is forbidden between the complete pin and resolved body
    object graphs, not merely between their roots.
    """
    doc = _minimal_extract()
    values = {"series": [{"T_K": 1700.0, "alpha": 0.02}]}
    doc["species"]["Fe"]["observations"][0]["values"] = values
    doc["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_alpha_1].values",
            "value": {"series": values["series"]},
            "note": "nested aliased pin",
            "locator": {"page": 2},
        }
    ]
    serialized = yaml.safe_dump(doc, sort_keys=False)
    assert "&id" in serialized and "*id" in serialized
    loaded = yaml.safe_load(serialized)
    expected = loaded["fidelity_samples"][0]["value"]
    actual = loaded["species"]["Fe"]["observations"][0]["values"]
    assert expected is not actual
    assert expected["series"] is actual["series"]

    baseline_errs = vle.check_all_fidelity_samples_match(loaded, label="nested-alias")
    assert any("identity" in e or "alias" in e for e in baseline_errs), baseline_errs
    shape_errs = vle.validate_extract_document(
        loaded,
        expected_source_id="fixture-source",
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("identity" in e or "alias" in e for e in shape_errs), shape_errs
    actual["series"].append({"T_K": 1800.0, "alpha": 0.03})
    mutation_errs = vle.check_all_fidelity_samples_match(loaded, label="nested-alias")
    assert any("identity" in e or "alias" in e for e in mutation_errs), mutation_errs


def test_fidelity_nested_serialized_mutable_set_alias_rejected():
    """Null-hypothesis (P1): nested YAML set aliases co-mutate and stay green."""
    doc = _minimal_extract()
    labels = {"published", "reviewed"}
    values = {"labels": labels}
    doc["species"]["Fe"]["observations"][0]["values"] = values
    doc["fidelity_samples"] = [
        {
            "path": "species.Fe.observations[fe_alpha_1].values",
            "value": {"labels": labels},
            "note": "nested set alias",
            "locator": {"page": 2},
        }
    ]
    loaded = yaml.safe_load(yaml.safe_dump(doc, sort_keys=False))
    expected = loaded["fidelity_samples"][0]["value"]
    actual = loaded["species"]["Fe"]["observations"][0]["values"]
    assert expected is not actual
    assert expected["labels"] is actual["labels"]
    baseline_errs = vle.check_all_fidelity_samples_match(loaded, label="set-alias")
    assert any("identity" in e or "alias" in e for e in baseline_errs), baseline_errs
    actual["labels"].add("mutated")
    mutation_errs = vle.check_all_fidelity_samples_match(loaded, label="set-alias")
    assert any("identity" in e or "alias" in e for e in mutation_errs), mutation_errs


def test_migrator_fidelity_deepcopy_isolates_pin():
    """Null-hypothesis (P1): reverting migrator deepcopy re-introduces aliases.

    ``_add_fidelity`` must copy the value so mutating the observation payload
    after sampling does not co-update the pin.
    """
    doc = mig._base_extract(
        "mig-fixture",
        citation="Mig et al.",
        doi=None,
        url=None,
        year=2026,
        method="test",
        date="2026-08-03",
        worker="pytest",
        provenance_path="docs-private/x.md",
    )
    values = {"alpha": 0.02, "material": "olivine"}
    mig._add_fidelity(
        doc,
        path="species.Fe.observations[x].values",
        value=values,
        note="pin",
    )
    assert doc["fidelity_samples"][0]["value"] is not values
    values["alpha"] = 99.0
    assert doc["fidelity_samples"][0]["value"]["alpha"] == 0.02


def test_fidelity_exact_match_rejects_bool_int_and_float_drift():
    """Null-hypothesis (P2): isclose / bool==int let type-drift pins stay green."""
    doc = _minimal_extract()
    # False vs 0
    doc["species"]["Fe"]["observations"][0]["values"] = {"flag": False}
    sample_bool = {
        "path": "species.Fe.observations[fe_alpha_1].values.flag",
        "value": 0,
        "note": "type drift",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(doc, sample_bool), "False must not match 0"
    # True vs 1
    doc["species"]["Fe"]["observations"][0]["values"] = {"flag": True}
    sample_true = {
        "path": "species.Fe.observations[fe_alpha_1].values.flag",
        "value": 1,
        "note": "type drift",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(doc, sample_true), "True must not match 1"
    # Small float mutation
    doc["species"]["Fe"]["observations"][0]["values"] = {"alpha": 10.0}
    sample_float = {
        "path": "species.Fe.observations[fe_alpha_1].values.alpha",
        "value": 10.000000005,
        "note": "float drift",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(
        doc, sample_float
    ), "float isclose drift must go red"
    # Exact match still green
    sample_ok = {
        "path": "species.Fe.observations[fe_alpha_1].values.alpha",
        "value": 10.0,
        "note": "exact",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(doc, sample_ok) == []


@pytest.mark.parametrize(("expected", "actual"), [(10, 10.0), (10.0, 10)])
def test_fidelity_exact_match_rejects_int_float_type_drift(expected, actual):
    """Null-hypothesis (P2): Python numeric equality hides int/float drift."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"] = {"alpha": actual}
    sample = {
        "path": "species.Fe.observations[fe_alpha_1].values.alpha",
        "value": expected,
        "note": "numeric type drift",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(doc, sample), (
        f"{type(expected).__name__} must not match {type(actual).__name__}"
    )


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        ({10: "x"}, {10.0: "x"}),
        ({10.0: "x"}, {10: "x"}),
        ({False: "x"}, {0: "x"}),
        ({0: "x"}, {False: "x"}),
    ],
    ids=["key-int-float", "key-float-int", "key-bool-int", "key-int-bool"],
)
def test_fidelity_exact_match_rejects_mapping_key_type_drift(expected, actual):
    """Null-hypothesis (P2): Python mapping-key equality hides type drift."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"] = actual
    sample = {
        "path": "species.Fe.observations[fe_alpha_1].values",
        "value": expected,
        "note": "mapping key type drift",
        "locator": {"page": 1},
    }
    loaded = yaml.safe_load(yaml.safe_dump({"doc": doc, "sample": sample}))
    assert vle.check_fidelity_sample_matches(loaded["doc"], loaded["sample"])


@pytest.mark.parametrize(("expected", "actual"), [([1], (1,)), ((1,), [1])])
def test_fidelity_exact_match_rejects_sequence_type_drift(expected, actual):
    """Null-hypothesis (P2): list and tuple structures compare as one type."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0]["values"] = {"series": actual}
    sample = {
        "path": "species.Fe.observations[fe_alpha_1].values.series",
        "value": expected,
        "note": "sequence type drift",
        "locator": {"page": 1},
    }
    assert vle.check_fidelity_sample_matches(doc, sample)


def _extract_paths_with_samples() -> list[Path]:
    paths: list[Path] = []
    for path in vle.discover_extracts():
        doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
        samples = doc.get("fidelity_samples") or []
        if samples:
            paths.append(path)
    return paths


@pytest.mark.parametrize(
    "extract_path",
    _extract_paths_with_samples(),
    ids=lambda p: p.stem,
)
def test_fidelity_sample_matches_extract(extract_path: Path):
    """Every fidelity sample still matches extract content (extraction-drift fuse).

    Null-hypothesis: a silent rewrite of a pinned field (bad regeneration,
    migrator regression) leaves samples stale while the file stays
    schema-valid — this test must go RED.
    """
    doc = load_cached_safe_yaml(extract_path.read_text(encoding="utf-8"))
    errs = vle.check_all_fidelity_samples_match(doc, label=extract_path.stem)
    assert errs == [], "\n".join(errs)


def test_bennour_1996_table_i_measurements_keep_printed_ln_data_and_conditions(tmp_path):
    """Table I's measured ln ratios, slopes, formula and row maps stay source-bound."""
    path = EXTRACTS / "bennour-1996-ternary-cs-na-silicate.yaml"
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    cs = doc["species"]["Cs2O"]
    observations = {row["observation_id"]: row for row in cs["observations"]}
    expected = {
        "bennour_1996_table_i_x_sio2_0p5_log_ion_ratio": [
            ("0.1", "-0.21", "5555"), ("0.3", "0.67", "6346"),
            ("0.5", "1.58", "4484"), ("0.7", "2.33", "3274"), ("0.9", "3.64", "4399"),
        ],
        "bennour_1996_table_i_x_sio2_0p667_log_ion_ratio": [
            ("0.1", "-0.79", "753"), ("0.3", "0.51", "1711"),
            ("0.5", "1.28", "2155"), ("0.7", "2.21", "2871"), ("0.9", "3.11", "4041"),
        ],
        "bennour_1996_table_i_x_sio2_0p75_log_ion_ratio": [
            ("0.1", "-1.27", "581"), ("0.2", "-0.69", "318"),
            ("0.3", "-0.01", "682"), ("0.4", "0.49", "452"),
            ("0.5", "0.83", "647"), ("0.6", "1.23", "1893"),
            ("0.7", "1.86", "1317"), ("0.8", "2.31", "1954"), ("0.9", "2.90", "2218"),
        ],
        "bennour_1996_table_i_x_sio2_0p85_log_ion_ratio": [
            ("0.3", "-1.21", "534"), ("0.5", "0.05", "2310"),
            ("0.7", "1.24", "2930"), ("0.9", "2.13", "3533"),
        ],
    }
    assert sum(len(points) for points in expected.values()) == 23
    assert "sha256" not in doc["source"]
    for observation_id, cells in expected.items():
        row = observations[observation_id]
        assert row["type"] == "rate_series"
        assert row["values"]["quantity"] == "ion_current_ratio"
        assert row["values"]["ratio_as_published"] == "ln(I_133Cs / I_23Na)"
        assert [
            (point["x_Cs2O_as_published"],
             point["ln_ionic_intensity_ratio_as_published"],
             point["slope_dln_ratio_d1_over_T_as_published"])
            for point in row["values"]["points"]
        ] == cells
        assert row["locator"]["page"] == 13
        assert row["locator"]["published_page"] == 240
        assert row["locator"]["pdf_page_index"] == 12
        for point in row["values"]["points"]:
            assert point["T_C"] == "1299.85"
            condition = point["point_conditions"]["composition"]
            assert condition["state"]["tag"] == "value"
            assert condition["state"]["value"]["amount_basis"] == "mole_fraction"
            assert sum(
                Decimal(amount)
                for _oxide, amount in condition["state"]["value"]["components"]
            ) == Decimal("1")
            assert condition["locator"]["pdf_page_index"] == 12

    context = {row["observation_id"]: row for row in cs["context"]}
    fits = [key for key in context if "table_ii" in key]
    assert len(fits) == 4
    assert all(context[key]["type"] == "thermodynamic_example" for key in fits)
    assert all(context[key]["locator"]["pdf_page_index"] == 12 for key in fits)
    assert all(
        context[key]["values"]["equation_as_published"]
        == "ln(I^2_Cs X_Na2O / I^2_Na X_Cs2O) = A + B x_Cs2O"
        for key in fits
    )

    figures = [row for rows in (cs["observations"], doc["species"]["Na2O"]["observations"])
               for row in rows if row["observation_id"].startswith("bennour_1996_fig7_")]
    assert len(figures) == 4
    assert all(row["locator"]["pdf_page_index"] == 13 for row in figures)
    assert {
        row["values"]["section_as_published"] for row in figures
    } == {"X = 0.85 (short-dashed curve)", "X = 0.5 (long-dashed curve)"}

    assert context["bennour_1996_experimental_apparatus"]["locator"]["pdf_page_index"] == 2
    assert context["bennour_1996_experimental_method"]["locator"]["pdf_page_index"] == 3
    assert context["bennour_1996_experimental_temperature_limit"]["locator"]["pdf_page_index"] == 5

    migrator = battery_migrate.Migrator(REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    migrated = [
        row for row in migrator.result.observations.values()
        if "bennour_1996_table_i_" in row.observation_id
    ]
    assert len(migrated) == 23
    assert all(
        row.evidence.class_.value is battery_migrate.EvidenceClass.MEASURED_DIRECT
        for row in migrated
    )
    assert not any(
        issue.reason.value == "conditional_field"
        and "bennour_1996_table_i_" in issue.path
        for issue in migrator.result.validation.hard_issues
    )
    assert all(row.value.kind.value == "unavailable" for row in migrated)
    assert all(row.identity.species.phase.is_value for row in migrated)
    assert all(row.identity.species.phase.value.value == "l" for row in migrated)
    assert all(
        row.identity.reference_state is not None
        and row.identity.reference_state.is_not_applicable
        and row.identity.reservoir is not None
        and row.identity.reservoir.is_not_applicable
        for row in migrated
    )
    assert all(
        row.identity.subtype is not None
        and row.identity.subtype.is_unknown
        and row.identity.subtype.reason == "no subtype mapped from source"
        for row in migrated
    )
    assert all(
        row.point_conditions["temperature_K"].state.value == Decimal("1573.00")
        and row.point_conditions["composition"].state.is_value
        for row in migrated
    )
    # Table I gives no per-row uncertainty column, so the migration must not
    # manufacture an uncertainty width or turn the printed logarithm linear.
    assert all(row.uncertainty.kind.value == "none" for row in migrated)

    # Reverting the four Table I classes must restore conditional_field issues.
    mutated = copy.deepcopy(doc)
    for row in mutated["species"]["Cs2O"]["observations"]:
        if row["observation_id"].startswith("bennour_1996_table_i_"):
            row["values"]["method_class"] = "measured_reduced"
    mutated_path = tmp_path / path.name
    mutated_path.write_text(yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8")
    reverted = battery_migrate.Migrator(REPO_ROOT, index={}, aliases={})
    reverted._migrate_extract(mutated_path)
    reverted.finalize()
    assert any(
        issue.reason.value == "conditional_field"
        and "bennour_1996_table_i_" in issue.path
        for issue in reverted.result.validation.hard_issues
    )

def test_richet_bottinga_1986_review_fixes_migrate_and_pin_table4() -> None:
    """Keep the reviewed provenance, pressure, widths, and context in migration."""
    from unittest.mock import patch
    from simulator.battery.enums import Phase, Polymorph, Quantity

    source_path = EXTRACTS / "richet-bottinga-1986-silicate-thermochemistry.yaml"
    source = battery_migrate.load_yaml(source_path)
    samples = source["fidelity_samples"]
    targets = {
        (sample["observation_id"], sample["field"])
        for sample in samples
    }
    assert len(samples) == len(targets) == 37

    def migrate(doc):
        migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
        original_load_yaml = battery_migrate.load_yaml

        def load_one(path):
            if Path(path) == source_path:
                return copy.deepcopy(doc)
            return original_load_yaml(path)

        with patch.object(battery_migrate, "load_yaml", side_effect=load_one):
            migrator._migrate_extract(source_path)
        migrator.finalize()
        return migrator.result

    result = migrate(source)
    source_observations = {
        row["observation_id"]: row
        for payload in source["species"].values()
        for row in payload.get("observations", [])
    }
    observations = {
        observation.observation_id.split("::")[-1]: observation
        for observation in result.observations.values()
    }
    assert len(observations) == 24
    assert len(result.experiments) == 1
    assert len(result.works) == 1
    assert sum(map(len, result.context_by_work.values())) == 12
    assert not result.validation.hard_issues
    assert result.unrecognised_polymorphs == {}

    expected_polymorphs = {
        "rb86_1986_cristobalite_transition_table5": Polymorph.CRISTOBALITE_HIGH,
        "rb86_1986_cristobalite_entropy_of_fusion_table5": Polymorph.CRISTOBALITE_HIGH,
        "rb86_1986_mgsio3_transition_table4": Polymorph.CLINOENSTATITE,
        "rb86_1986_mgsio3_heat_of_fusion_table4": Polymorph.CLINOENSTATITE,
        "rb86_1986_mgsio3_entropy_of_fusion_table4": Polymorph.CLINOENSTATITE,
        "rb86_1986_high_albite_transition_table5": Polymorph.ALBITE_HIGH,
        "rb86_1986_high_albite_entropy_of_fusion_table5": Polymorph.ALBITE_HIGH,
        "rb86_1986_high_sanidine_transition_table5": Polymorph.SANIDINE_HIGH,
        "rb86_1986_high_sanidine_entropy_of_fusion_table5": Polymorph.SANIDINE_HIGH,
        "rb86_1986_diopside_transition_table5": Polymorph.DIOPSIDE,
        "rb86_1986_diopside_entropy_of_fusion_table5": Polymorph.DIOPSIDE,
    }
    for observation_id, expected_polymorph in expected_polymorphs.items():
        polymorph = observations[observation_id].identity.species.polymorph
        assert polymorph.is_value
        assert polymorph.value is expected_polymorph

    for observation_id in (
        "rb86_1986_cristobalite_transition_table5",
        "rb86_1986_cristobalite_entropy_of_fusion_table5",
    ):
        source_row = source_observations[observation_id]
        assert source_row["locator"]["row"] == "Cristobalite"
        note = source_row["condensed_form"]["note"]
        assert "only cristobalite stable at 1999 K is high (beta) cristobalite" in note
        assert "alpha-beta inversion is near 500 K" in note
    context_row = next(
        row
        for rows in result.context_by_work.values()
        for row in rows
        if row["observation_id"] == "rb86_1986_cristobalite_table5_entropy_context"
    )
    assert context_row["locator"]["row"] == "Cristobalite"
    assert context_row["condensed_form"]["polymorph"] == "cristobalite_high"
    assert "only cristobalite stable at 1999 K is high (beta) cristobalite" in (
        context_row["condensed_form"]["note"]
    )
    assert "alpha-beta inversion is near 500 K" in (
        context_row["condensed_form"]["note"]
    )

    transition_values = {
        "rb86_1986_cristobalite_transition_table5": 1999,
        "rb86_1986_quartz_transition_table5": 1700,
        "rb86_1986_high_albite_transition_table5": 1393,
        "rb86_1986_high_sanidine_transition_table5": 1500,
        "rb86_1986_anorthite_transition_table5": 1830,
        "rb86_1986_diopside_transition_table5": 1670,
        "rb86_1986_na2si2o5_transition_table4": 1147,
        "rb86_1986_k2si2o5_transition_table4": 1318,
        "rb86_1986_k2sio3_transition_table4": 1249,
        "rb86_1986_mgsio3_transition_table4": 1834,
    }
    transition_observations = [observations[obs_id] for obs_id in transition_values]
    assert len(transition_observations) == 10
    for obs_id, expected_temperature in transition_values.items():
        observation = observations[obs_id]
        assert observation.value.point == Decimal(expected_temperature)
        assert observation.identity.total_pressure_Pa.value == Decimal("100000")
        source_row = source_observations[obs_id]
        assert source_row["values"]["pressure_basis_Pa"] == 100000
        assert source_row["pressure_basis_locator"]["page"] == 21
    assert observations["rb86_1986_na2si2o5_transition_table4"].evidence.attribution == (
        "Levin et al. [1964]"
    )
    assert observations["rb86_1986_k2si2o5_transition_table4"].evidence.attribution == (
        "Levin et al. [1964]"
    )
    assert observations["rb86_1986_k2sio3_transition_table4"].evidence.attribution == (
        "Levin et al. [1964]"
    )
    assert observations["rb86_1986_mgsio3_transition_table4"].evidence.attribution == (
        "Bowen and Andersen [1914]"
    )
    assert source_observations["rb86_1986_mgsio3_transition_table4"]["values"][
        "provenance_note"
    ].endswith("that extrapolation.")
    assert all(
        observation.evidence.class_.value.value == "quoted_attributed"
        for observation in transition_observations
    )

    expected_widths = {
        "rb86_1986_na2si2o5_heat_of_fusion_table4": (Decimal("4.4"), "kJ/mol"),
        "rb86_1986_na2si2o5_entropy_of_fusion_table4": (Decimal("3.8"), "J/mol K"),
        "rb86_1986_k2si2o5_heat_of_fusion_table4": (Decimal("4.0"), "kJ/mol"),
        "rb86_1986_k2si2o5_entropy_of_fusion_table4": (Decimal("3.0"), "J/mol K"),
        "rb86_1986_k2sio3_heat_of_fusion_table4": (Decimal("4.0"), "kJ/mol"),
        "rb86_1986_k2sio3_entropy_of_fusion_table4": (Decimal("3.2"), "J/mol K"),
        "rb86_1986_mgsio3_heat_of_fusion_table4": (Decimal("6.0"), "kJ/mol"),
        "rb86_1986_mgsio3_entropy_of_fusion_table4": (Decimal("3.3"), "J/mol K"),
    }
    expected_fusion_values = {
        "rb86_1986_na2si2o5_heat_of_fusion_table4": Decimal("37.7"),
        "rb86_1986_na2si2o5_entropy_of_fusion_table4": Decimal("32.9"),
        "rb86_1986_k2si2o5_heat_of_fusion_table4": Decimal("32.0"),
        "rb86_1986_k2si2o5_entropy_of_fusion_table4": Decimal("24.3"),
        "rb86_1986_k2sio3_heat_of_fusion_table4": Decimal("19.6"),
        "rb86_1986_k2sio3_entropy_of_fusion_table4": Decimal("15.7"),
        "rb86_1986_mgsio3_heat_of_fusion_table4": Decimal("73.2"),
        "rb86_1986_mgsio3_entropy_of_fusion_table4": Decimal("39.9"),
    }
    # Table 4 prints Tf on both fusion quantities for the first three rows.
    # The second MgSiO3 line has no Tf; neither quantity may inherit the
    # adjacent Stebbins temperature.
    expected_printed_fusion_temperatures = {
        "rb86_1986_na2si2o5_heat_of_fusion_table4": ("Na2Si2O5", 1147),
        "rb86_1986_na2si2o5_entropy_of_fusion_table4": ("Na2Si2O5", 1147),
        "rb86_1986_k2si2o5_heat_of_fusion_table4": ("K2Si2O5", 1318),
        "rb86_1986_k2si2o5_entropy_of_fusion_table4": ("K2Si2O5", 1318),
        "rb86_1986_k2sio3_heat_of_fusion_table4": ("K2SiO3", 1249),
        "rb86_1986_k2sio3_entropy_of_fusion_table4": ("K2SiO3", 1249),
    }
    expected_unknown_fusion_temperatures = (
        "rb86_1986_mgsio3_heat_of_fusion_table4",
        "rb86_1986_mgsio3_entropy_of_fusion_table4",
    )
    unknown_fusion_temperature_reason = (
        "Table 4 does not print Tf for the second MgSiO3 row (This work, appendix)"
    )

    def assert_fusion_temperatures(migrated, extract):
        migrated_rows = {
            observation.observation_id.split("::")[-1]: observation
            for observation in migrated.observations.values()
        }
        source_rows = {
            row["observation_id"]: (formula, row)
            for formula, payload in extract["species"].items()
            for row in payload.get("observations", [])
        }
        for obs_id, (formula, printed_tf) in expected_printed_fusion_temperatures.items():
            source_formula, row = source_rows[obs_id]
            assert "T_fus_K" in row["values"]
            tf = row["values"]["T_fus_K"]
            assert source_formula == formula
            assert tf["value"] == printed_tf
            assert tf["units"] == "K"
            assert tf["locator"]["table"] == "4"
            assert tf["locator"]["row"] == formula
            assert tf["locator"]["column"] == "T_f (K)"
            assert migrated_rows[obs_id].identity.temperature_K.value == Decimal(printed_tf)

        # No Tf was printed for either quantity on the second MgSiO3 line, so
        # neither identity may inherit the adjacent 1834 K Stebbins row.
        for obs_id in expected_unknown_fusion_temperatures:
            source_formula, row = source_rows[obs_id]
            assert source_formula == "MgSiO3"
            mg_tf = row["values"]["T_fus_K"]
            assert mg_tf["value"] is None
            assert mg_tf["reason"] == unknown_fusion_temperature_reason
            assert mg_tf["units"] == "K"
            assert mg_tf["locator"]["table"] == "4"
            assert mg_tf["locator"]["row"] == "MgSiO3"
            assert mg_tf["locator"]["column"] == "T_f (K)"
            assert migrated_rows[obs_id].identity.temperature_K.is_unknown

    assert_fusion_temperatures(result, source)

    def assert_widths(migrated):
        rows = {
            observation.observation_id.split("::")[-1]: observation
            for observation in migrated.observations.values()
        }
        for obs_id, (expected_value, expected_units) in expected_widths.items():
            uncertainty = rows[obs_id].uncertainty.verbatim
            assert Decimal(str(uncertainty["value"])) == expected_value
            assert uncertainty["units"] == expected_units

    assert_widths(result)
    expected_lineage = {
        "rb86_1986_na2si2o5_entropy_of_fusion_table4": (
            "rb86_1986_na2si2o5_heat_of_fusion_table4",
            "rb86_1986_na2si2o5_transition_table4",
        ),
        "rb86_1986_k2si2o5_entropy_of_fusion_table4": (
            "rb86_1986_k2si2o5_heat_of_fusion_table4",
            "rb86_1986_k2si2o5_transition_table4",
        ),
        "rb86_1986_k2sio3_entropy_of_fusion_table4": (
            "rb86_1986_k2sio3_heat_of_fusion_table4",
            "rb86_1986_k2sio3_transition_table4",
        ),
        # The second MgSiO3 Table 4 line prints no Tf, so its temperature
        # lineage must not include the preceding Stebbins transition row.
        "rb86_1986_mgsio3_entropy_of_fusion_table4": (
            "rb86_1986_mgsio3_heat_of_fusion_table4",
        ),
    }
    for obs_id, (expected_class, input_quantity) in {
        "rb86_1986_na2si2o5_heat_of_fusion_table4": ("author_estimate", "heat"),
        "rb86_1986_k2si2o5_heat_of_fusion_table4": ("author_estimate", "heat"),
        "rb86_1986_k2sio3_heat_of_fusion_table4": ("author_estimate", "heat"),
        "rb86_1986_mgsio3_heat_of_fusion_table4": ("author_estimate", "heat"),
        "rb86_1986_na2si2o5_entropy_of_fusion_table4": ("model_derived", "entropy"),
        "rb86_1986_k2si2o5_entropy_of_fusion_table4": ("model_derived", "entropy"),
        "rb86_1986_k2sio3_entropy_of_fusion_table4": ("model_derived", "entropy"),
        "rb86_1986_mgsio3_entropy_of_fusion_table4": ("model_derived", "entropy"),
    }.items():
        observation = observations[obs_id]
        assert observation.value.kind.value == "point"
        assert observation.value.point == expected_fusion_values[obs_id]
        expected_quantity = (
            Quantity.ENTHALPY_OF_FUSION
            if input_quantity == "heat"
            else Quantity.ENTROPY_OF_FUSION
        )
        assert observation.identity.quantity.value is expected_quantity
        assert observation.identity.species.phase.value is Phase.CR
        assert observation.identity.subtype.is_value
        assert observation.identity.subtype.value == "solid_to_liquid"
        if source_observations[obs_id].get("condensed_form") is None:
            assert observation.identity.species.polymorph.is_unknown
        assert observation.evidence.class_.value.value == expected_class
        if input_quantity == "heat":
            stated_model = source_observations[obs_id]["values"]["model"]
            assert "equation (23)" in stated_model.lower()
            assert "enthalpies of fusion" in stated_model.lower()
        else:
            assert observation.derivation.relation == "ΔS_f = ΔH_f/T_f"
            expected_inputs = {
                f"{source_path.stem}::{parent}"
                for parent in expected_lineage[obs_id]
            }
            assert set(observation.derived_from) == expected_inputs
            assert set(observation.derivation.inputs) == set(observation.derived_from)

    contexts = [row for rows in result.context_by_work.values() for row in rows]
    table1_contexts = [
        row for row in contexts
        if row.get("observation_id", "").endswith("_fictive_temperatures_table1")
    ]
    assert len(table1_contexts) == 5
    assert all(
        row["values"]["fast_cooling_caveat_ratio"] == 10000
        and "equation (12)" in row["values"]["fast_cooling_caveat"]
        for row in table1_contexts
    )
    configurational_contexts = [
        row for row in contexts if row.get("observation_id", "").endswith("_entropy_context")
    ]
    assert len(configurational_contexts) == 6
    assert all(
        "Equations (26)-(27)" in row["values"]["model"]
        and "Equation (25)" not in row["values"]["model"]
        for row in configurational_contexts
    )

    table4_temperature_samples = [
        sample for sample in samples
        if sample["observation_id"].endswith("_transition_table4")
    ]
    assert len(table4_temperature_samples) == 4
    for sample in table4_temperature_samples:
        mutated = copy.deepcopy(source)
        target = next(
            row
            for row in mutated["species"][sample["species"]]["observations"]
            if row["observation_id"] == sample["observation_id"]
        )
        target["values"]["T_m_K"] += 1
        assert vle.check_fidelity_sample_matches(mutated, sample)

    for obs_id in expected_widths:
        mutated = copy.deepcopy(source)
        formula = next(
            sample["species"]
            for sample in samples
            if sample["observation_id"] == obs_id
        )
        target = next(
            row for row in mutated["species"][formula]["observations"]
            if row["observation_id"] == obs_id
        )
        target["uncertainty"]["value"] += 1
        with pytest.raises(AssertionError):
            assert_widths(migrate(mutated))

def test_matzen_run_2_3_duration_seconds_matches_table_2():
    """Table 2 prints 41.2 h; pin its seconds conversion against unit drift."""
    path = EXTRACTS / "matzen-2022-martian-basalt-redox.yaml"
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    run = next(
        row for row in doc["experiments"]
        if row["experiment_id"] == "matzen-run-2-3"
    )
    assert run["conditions"]["duration_h"]["state"]["value"] == "41.2"
    assert run["thermal_schedule"]["total_duration_s"]["state"]["value"] == "148320"


def test_matzen_observations_resolve_printed_log_fo2():
    """Each Table 2 log fO2 lands on its observation and routes as printed."""
    from decimal import Decimal

    from simulator.battery.migrate import Migrator
    from simulator.battery.waypoints import oxygen_condition

    path = EXTRACTS / "matzen-2022-martian-basalt-redox.yaml"
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    expected = {
        f"{doc['source_id']}::{row['observation_id']}": Decimal(
            row["values"]["log_fO2"]
        )
        for row in doc["species"]["Fe"]["observations"]
    }
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    result = migrator.result
    observation_ids = result.observations_by_source[path.relative_to(REPO_ROOT).as_posix()]

    assert len(observation_ids) == len(expected) == 24
    for observation_id in observation_ids:
        observation = result.observations[observation_id]
        experiment = result.experiments[observation.experiment_id]
        bench = result.benches[experiment.bench_id]
        resolved = oxygen_condition(experiment, bench, observation).selected
        assert resolved is not None, observation_id
        assert resolved.route == "observation_fO2_log", observation_id
        assert resolved.value.point == expected[observation.observation_id], observation_id
        located = observation.point_conditions["fO2_log"]
        assert located.locator.page == 39 and located.locator.table == 2
def test_bencze_supplement_fidelity_sample_rejects_mutated_value():
    path = EXTRACTS / "bencze-yazhenskikh-2016.yaml"
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    sample = doc["fidelity_samples"][0]

    assert sample["locator"]["page"] == 1
    assert sample["locator"]["table"] == "S-I"
    assert sample["value"] == "1.70·10-2"
    assert vle.check_all_fidelity_samples_match(doc, label=path.stem) == []

    doc["species"]["Na"]["observations"][0]["values"]["rows"][0][
        "p_Na_at_1273_K_Pa"
    ] = "1.71·10-2"
    errors = vle.check_all_fidelity_samples_match(doc, label=path.stem)

    assert any("fidelity_samples[0]" in error and "mismatch" in error for error in errors)


def test_fidelity_sample_mutation_reds():
    """Prove the gate: mutate one extract value in memory → match test REDS.

    Pins must be independent literals (no YAML alias). Mutate the body without
    deepcopying samples — a co-mutated alias would keep the match green.
    """
    path = EXTRACTS / "kems-007-costa-2015.yaml"
    assert path.is_file(), "Costa owner extract required for mutation pin"
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    samples = doc.get("fidelity_samples") or []
    assert samples, "Costa owner extract must carry at least one fidelity sample"
    # Baseline green with independent (non-aliased) pins.
    assert vle.check_all_fidelity_samples_match(doc, label="costa-owner") == []
    # Prove samples do not share identity with body values.
    for i, s in enumerate(samples):
        exp = s.get("value") if "value" in s else s.get("draft_value")
        act = vle.resolve_fidelity_sample(doc, s)
        if isinstance(exp, (dict, list)):
            assert exp is not act, (
                f"sample[{i}] still shares identity with body — re-dump without aliases"
            )

    # Mutate the Fe alpha pin in the extract body (samples left untouched).
    target = next(
        obs for obs in doc["species"]["Fe"]["observations"]
        if obs.get("observation_id")
        == "costa_2015_fe_vaporization_coefficient_alpha_digitized"
    )
    original = copy.deepcopy(target["values"])
    target["values"]["points"][2]["alpha"] += 0.5

    errs = vle.check_all_fidelity_samples_match(doc, label="costa-owner-mutated")
    assert errs, (
        "mutation of pinned extract value must turn fidelity match RED; "
        f"original={original!r}"
    )
    assert any("mismatch" in e for e in errs), errs

    # Every Jones and Lange sample is independently mutation-sensitive.
    jones_path = EXTRACTS / "1997jonesthermo.yaml"
    jones_samples = load_cached_safe_yaml(
        jones_path.read_text(encoding="utf-8")
    ).get("fidelity_samples") or []
    assert jones_samples, "Jones extract must carry fidelity samples"
    for index, sample in enumerate(jones_samples):
        jones_doc = load_cached_safe_yaml(jones_path.read_text(encoding="utf-8"))
        assert vle.check_all_fidelity_samples_match(jones_doc, label="jones-owner") == []
        row = next(
            row
            for container in ("observations", "context")
            for row in jones_doc["species"][sample["species"]].get(container, [])
            if row.get("observation_id") == sample["observation_id"]
        )
        row["values"][sample["field"]] += 1
        errors = vle.check_all_fidelity_samples_match(
            jones_doc, label="jones-owner-mutated"
        )
        assert any(
            f"fidelity_samples[{index}]" in error and "mismatch" in error
            for error in errors
        ), errors

    lange_path = EXTRACTS / "ammin-76-904.yaml"
    lange_samples = load_cached_safe_yaml(
        lange_path.read_text(encoding="utf-8")
    ).get("fidelity_samples") or []
    assert len(lange_samples) >= 3, "Lange extract must carry table-spread samples"
    for index, sample in enumerate(lange_samples):
        lange_doc = load_cached_safe_yaml(lange_path.read_text(encoding="utf-8"))
        assert vle.check_all_fidelity_samples_match(lange_doc, label="lange-owner") == []
        resolved = vle.resolve_fidelity_sample(lange_doc, sample)
        if isinstance(resolved, list):
            resolved[0] += 1
        else:
            resolved["crystal"]["SiO2"]["value"] += 1
        errors = vle.check_all_fidelity_samples_match(
            lange_doc, label="lange-owner-mutated"
        )
        assert any(
            f"fidelity_samples[{index}]" in error and "mismatch" in error
            for error in errors
        ), errors


def test_shaw_surface_fidelity_mutation_reds():
    """Pin Shaw run and source claims; each reviewed reversal must turn red."""
    path = EXTRACTS / "shaw-2023-vacuum-thermal-sublimation-thesis.yaml"
    assert path.is_file()

    def assert_pins(candidate):
        assert vle.check_all_fidelity_samples_match(candidate, label="shaw-2023") == []
        expected_runs = [
            "blank-high-vacuum",
            "td1-nanophase-iron-oxide",
            "td2-nanophase-iron-oxide",
            "td3-ilmenite-concentrate",
            "td4-lms1-atmosphere",
            "td5-lms1",
            "td6-lms1",
            "td7-reacted-td6-feed",
        ]
        assert len(candidate["experiments"]) == 8
        assert [run["experiment_id"] for run in candidate["experiments"]] == expected_runs
        expected_result_runs = [
            ("shaw_2023_blank_results", "blank-high-vacuum"),
            ("shaw_2023_td1_results", "td1-nanophase-iron-oxide"),
            ("shaw_2023_td2_results", "td2-nanophase-iron-oxide"),
            ("shaw_2023_td3_results", "td3-ilmenite-concentrate"),
            ("shaw_2023_td4_results", "td4-lms1-atmosphere"),
            ("shaw_2023_td5_results", "td5-lms1"),
            ("shaw_2023_td6_results", "td6-lms1"),
            ("shaw_2023_td7_results", "td7-reacted-td6-feed"),
        ]
        results = [
            (row["observation_id"], row["experiment"])
            for species in candidate["species"].values()
            for row in species.get("context", [])
            if row.get("observation_id", "").endswith("_results")
        ]
        assert len(results) == 8
        assert sorted(results) == sorted(expected_result_runs)
        result_rows = {
            row["observation_id"]: row
            for species in candidate["species"].values()
            for row in species.get("context", [])
            if row.get("observation_id", "").endswith("_results")
        }
        vacuum_vent_reason = (
            "reactor_was_vented_to_atmospheric_pressure_before_removal_but_"
            "vent_gas_air_contact_and_duration_are_not_stated"
        )
        for result_id in (
            "shaw_2023_blank_results",
            "shaw_2023_td2_results",
            "shaw_2023_td3_results",
            "shaw_2023_td5_results",
            "shaw_2023_td6_results",
            "shaw_2023_td7_results",
        ):
            assert (
                result_rows[result_id]["values"]["saw_air_before_analysis"]["reason"]
                == vacuum_vent_reason
            )
        assert result_rows["shaw_2023_td1_results"]["values"]["saw_air_before_analysis"] == {
            "state": "unknown",
            "reason": "reactor_was_vented_slowly_to_atmospheric_pressure_via_vacuum_pump_before_sample_removal; post_test_air_contact_and_duration_not_reported",
        }
        assert result_rows["shaw_2023_td4_results"]["values"]["saw_air_before_analysis"] == {
            "state": "unknown",
            "reason": "TD4_ran_at_atmospheric_pressure_with_pump_detached_and_reactor_flange_open; post_test_air_contact_and_duration_not_reported",
        }
        td7_sample = candidate["experiments"][-1]["sample"]
        assert td7_sample["printed_composition"]["state"] == {
            "tag": "value",
            "value": "LMS-1",
        }
        assert td7_sample["form"]["state"] == {
            "tag": "value",
            "value": "Processed TD4",
        }
        for run in candidate["experiments"]:
            schedule = run["thermal_schedule"]
            assert schedule["total_duration_s"]["state"]["tag"] == "unknown"
            hold = schedule["setpoints_and_holds"][0]
            assert hold["hold_duration_s"]["state"]["tag"] == "value"
            assert hold["temperature_K"]["state"]["tag"] == "unknown"

        context = candidate["species"]["vacuum_reactor_blank"]["context"]
        blank_rows = [
            row for row in context
            if row.get("observation_id") == "shaw_2023_blank_results"
        ]
        assert blank_rows, "blank control context must survive YAML loading"
        blank = blank_rows[0]
        assert blank["values"]["deposit_location"] == "Reactor tube glass; general frosting"
        assert blank["values"]["sample_mass_loss"] == {
            "state": "unknown",
            "reason": "blank_test_had_no_sample",
        }
        assert "mass_loss" not in blank["values"]
        handling = next(
            row for row in context
            if row.get("observation_id") == "shaw_2023_ch8_sample_preparation"
        )["values"]
        assert handling["reactor_moved_from_furnace_below_C"] == 300
        assert "greater than 1000 C" in handling["high_temperature_definition"]
        inventory = next(
            row for row in context
            if row.get("observation_id") == "shaw_2023_ch8_deposit_surface_inventory"
        )["values"]["runs"]
        assert list(inventory) == ["blank", *expected_runs[1:]]
        assert all("exposure_and_handling" in run for run in inventory.values())
        td4_handling = inventory["td4-lms1-atmosphere"]["exposure_and_handling"]
        assert "pump was detached" in td4_handling["apparatus"]
        assert "flange was left open" in td4_handling["apparatus"]
        assert td4_handling["vacuum_pump_venting"]["state"] == "unknown"
        analysis_limits = next(
            row for row in context
            if row.get("observation_id") == "shaw_2023_ch8_deposit_surface_inventory"
        )["values"]["common_analysis_limitations"]
        assert "TD1 used a smear" in analysis_limits
        assert "TD4 had no SEM/EDS analysis" in analysis_limits
        assert inventory["td1-nanophase-iron-oxide"]["wall"]["qualification"] == (
            "Minor Zn, Ca, and P are presumed by the author to be outside contamination "
            "associated with the smear sampling; the swab method caused significantly more "
            "contamination than later fractured-glass analysis"
        )
        assert inventory["td3-ilmenite-concentrate"]["wall"]["author_identification"].startswith(
            "The thesis calls the structures “ZnO nano-rods”"
        )
        assert inventory["td5-lms1"]["wall"]["zone_findings"]["b"].startswith(
            "Consistent Na and Cl layer"
        )
        td5_a = inventory["td5-lms1"]["wall"]
        assert td5_a["band_level_composition"] == (
            "The initial deposition band (a) was composed predominantly of Na, P, and Cu."
        )
        assert td5_a["distinct_crystals"] == "Distinct crystals were composed of Na, P, and Mo."
        assert td5_a["band_level_composition_locator"]["published_page"] == 212
        assert td5_a["distinct_crystals_locator"]["published_page"] == 212
        assert inventory["td6-lms1"]["wall"]["zone_findings"]["a"] == (
            "Additional crystals were reported as composed of Na and O according to EDS."
        )
        assert inventory["td7-reacted-td6-feed"]["wall"]["scan_duration_min"] == 5

        for species in ("Fe", "Na", "K"):
            feed = candidate["species"][species]["context"][0]["values"]
            assert "Apollo #12022 feed system named in Table 7.5" in feed["inputs"]
            assert "selected Al2O3" not in feed["inputs"]
            assert "Apollo #12022" in feed["vapor_pressure_source"]

        zn = candidate["species"]["Ilmenite"]["context"][0]["values"]
        feed_assay = zn.get("feed_composition", {})
        assert feed_assay.get("Zn_wt_pct") == 0.028
        assert feed_assay.get("basis", "").startswith("feed-material XRF")
        assert zn.get("residue_composition", {}).get("state") == "unknown"

        visual_index = next(
            row for row in candidate["species"]["LMS-1"]["context"]
            if row.get("observation_id") == "shaw_2023_unextracted_ch7_ch8_visual_data_index"
        )
        assert "only material not extracted" in visual_index["units"]
        fig814 = next(
            fig for fig in visual_index["values"]["figures"] if fig["id"] == "8.14"
        )
        assert "Full SEM image not extracted" in fig814["note"]
        assert "summarized in the extract" in fig814["note"]

    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    assert_pins(doc)

    # Four independent source-backed mutations requested by the confirm review.
    mutations = []

    candidate = copy.deepcopy(doc)
    candidate["experiments"] = [
        run for run in candidate["experiments"]
        if run["experiment_id"] != "td2-nanophase-iron-oxide"
    ]
    mutations.append(("TD2 deleted", candidate))

    candidate = copy.deepcopy(doc)
    candidate["species"]["FeO/Fe2O3"]["context"].pop(1)
    mutations.append(("TD2 results context deleted", candidate))

    candidate = copy.deepcopy(doc)
    candidate["experiments"][-1]["sample"]["printed_composition"]["state"]["value"] = "Processed TD4"
    mutations.append(("TD7 feed material merged into feed form", candidate))

    candidate = copy.deepcopy(doc)
    candidate["experiments"][1]["thermal_schedule"]["total_duration_s"]["state"] = {
        "tag": "value",
        "value": {"kind": "point", "point": "6480"},
    }
    mutations.append(("numeric total duration restored", candidate))

    candidate = copy.deepcopy(doc)
    candidate["species"]["Na"]["context"][0]["values"]["inputs"] = (
        "FactSage pressure from the selected Al2O3-CaO-Na2O system"
    )
    mutations.append(("Na attributed to wrong feed system", candidate))

    candidate = copy.deepcopy(doc)
    zn = candidate["species"]["Ilmenite"]["context"][0]["values"]
    zn["residue_composition"] = zn.pop("feed_composition")
    mutations.append(("Zn feed assay moved to residue", candidate))

    for label, candidate in mutations:
        try:
            assert_pins(candidate)
        except AssertionError:
            continue
        raise AssertionError(f"{label} mutation survived the Shaw regression pins")

    inventory_record = next(
        row for row in doc["species"]["vacuum_reactor_blank"]["context"]
        if row.get("observation_id") == "shaw_2023_ch8_deposit_surface_inventory"
    )
    inventory_record["values"]["runs"]["td7-reacted-td6-feed"]["wall"]["scan_duration_min"] = 4
    with pytest.raises(AssertionError):
        assert_pins(doc)

    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    doc["species"]["vacuum_reactor_blank"]["context"] = [
        row for row in doc["species"]["vacuum_reactor_blank"]["context"]
        if row.get("observation_id") != "shaw_2023_blank_results"
    ]
    with pytest.raises(AssertionError):
        assert_pins(doc)


def test_costa_fe_summary_key_is_retired_to_owner_series():
    secondary = load_cached_safe_yaml(
        (EXTRACTS / "costa-jacobson-2015.yaml").read_text(encoding="utf-8")
    )
    owner = load_cached_safe_yaml(
        (EXTRACTS / "kems-007-costa-2015.yaml").read_text(encoding="utf-8")
    )
    retired_key, owner_key = next(iter(RETIRED_DUPLICATE_OBSERVATION_ALIASES.items()))
    retired_source, retired_observation = retired_key.split("::", 1)
    owner_source, owner_observation = owner_key.split("::", 1)
    secondary_rows = secondary["species"].get("Fe", {}).get("observations", [])
    assert retired_source == secondary["source_id"]
    assert all(row.get("observation_id") != retired_observation for row in secondary_rows)
    assert secondary["species"]["SiO"]["observations"]
    assert owner_source == owner["source_id"]
    owner_row = next(
        row for row in owner["species"]["Fe"]["observations"]
        if row.get("observation_id") == owner_observation
    )
    assert [point["alpha"] for point in owner_row["values"]["points"]] == [
        0.016269, 0.015517, 0.01978, 0.011505, 0.013045
    ]


def test_fidelity_resolve_structured_series_index():
    """Structured sample with index resolves a series point."""
    doc = _minimal_extract()
    doc["species"]["Fe"]["observations"][0] = {
        "observation_id": "fe_psat",
        "type": "psat_series",
        "locator": {"table": "2"},
        "units": "Pa",
        "values": [
            {"T_K": 1700.0, "P_Pa": 1.0},
            {"T_K": 1800.0, "P_Pa": 10.0},
        ],
    }
    sample = {
        "species": "Fe",
        "observation_id": "fe_psat",
        "observable": "psat_series",
        "index": 1,
        "field": "P_Pa",
        "value": 10.0,
        "locator": {"table": "2"},
    }
    assert vle.resolve_fidelity_sample(doc, sample) == pytest.approx(10.0)
    sample_t = {
        "species": "Fe",
        "observation_id": "fe_psat",
        "T_K": 1700.0,
        "field": "P_Pa",
        "value": 1.0,
        "locator": {"table": "2"},
    }
    assert vle.resolve_fidelity_sample(doc, sample_t) == pytest.approx(1.0)
    assert vle.check_fidelity_sample_matches(doc, sample_t) == []


def test_singleton_series_disagreement_is_none():
    """Null-hypothesis (P1): 157 singleton groups received non-null dex."""
    # Build a one-observation group and ensure dex is None
    extracts = [
        {
            "source_id": "only-src",
            "species": {
                "Fe": {
                    "observations": [
                        {
                            "observation_id": "one",
                            "type": "alpha",
                            "regime": "langmuir_free_evaporation",
                            "units": "dimensionless",
                            "values": {"alpha": 0.24},
                            "uncertainty": {"note": "x"},
                        }
                    ]
                }
            },
        }
    ]
    view = em.build_by_species(extracts, source_priority={"alpha": ["only-src"]})
    g = view["species"]["Fe"]["observable_groups"][0]
    assert g["disagreement_dex"] is None


def test_printed_experiment_bench_links_carry_locator(tmp_path: Path):
    """Experiment.bench_id is the printed cell assignment, with its locator.

    A sole typed bench is not enough: Hastie Table 2 and Allibert EMF stay
    unlinked because those rows are not the printed cell experiment.
    """
    from tests.battery.test_migrate import _migrate_real_extract

    def experiment_named(result, local):
        rows = [
            item
            for item in result.experiments.values()
            if item.experiment_id.endswith("::experiment::" + local)
        ]
        assert len(rows) == 1, local
        return rows[0]

    def observations_named(result, local):
        rows = [
            item
            for item in result.observations.values()
            if local in item.observation_id.split("::")
        ]
        assert rows, local
        return rows

    linked = (
        (
            "kems-053-stolyarova-1991.yaml",
            "stolyarova-1991-w-cell",
            "stolyarova-kems",
            "Samples evaporated from tungsten effusion cells.",
            "stolyarova_1991_ca_partial_pressure_1993k_complete_evaporation",
        ),
        (
            "kems-051-allibert-1981.yaml",
            "allibert-1981-mo-kems",
            "allibert-1981-kems",
            "Six effusion cells were drilled in a molybdenum block.",
            "allibert_1981_table2_cao_activity_kems",
        ),
        (
            "kems-020-hastie-1981-nbsir.yaml",
            "hastie-1981-pt-kms",
            "hastie-1981-kms",
            "KMS data with Pt-cell orifice diameter of 0.34 mm.",
            "hastie_1981_k2_slag_orifice_0p34mm_quoted_20260906",
        ),
        (
            "metsoc-2019-6005.yaml",
            "shornikov-2019-mo-kems",
            "shornikov-yakovlev-2019-kems",
            "Method section identifies a Knudsen molybdenum effusion cell.",
            "shornikov_yakovlev_2019_metsoc6005_perovskite_delta_fH",
        ),
        (
            "bencze-yazhenskikh-2016.yaml",
            "bencze-2016-ir-kems",
            "bencze-2016-supplement-kems",
            "there is a thin graphite coating on the inner surface of the lid of the iridium cell",
            "bencze_2016_table_s1_na_all_subsamples",
        ),
    )
    for filename, exp_local, bench_local, quote, obs_local in linked:
        result = _migrate_real_extract(tmp_path / Path(filename).stem, filename)
        experiment = experiment_named(result, exp_local)
        assert experiment.bench_id is not None
        assert experiment.bench_id.endswith("::bench::" + bench_local)
        assert experiment.locator is not None
        assert quote in (experiment.locator.note or "")
        assert experiment.bench_id in result.benches
        for observation in observations_named(result, obs_local):
            assert observation.experiment_id == experiment.experiment_id

    hastie = _migrate_real_extract(tmp_path / "hastie-unlinked", "kems-020-hastie-1981-nbsir.yaml")
    table2 = observations_named(hastie, "hastie_1981_table2_k_logP_coefficients")
    table2_experiment = hastie.experiments[table2[0].experiment_id]
    assert table2_experiment.bench_id is not None
    assert table2_experiment.bench_id.endswith("::bench::hastie-1981-tms")
    assert table2_experiment.experiment_id.endswith("::experiment::hastie-1981-pt-tms")
    tms_bench = hastie.benches[table2_experiment.bench_id]
    assert tms_bench.cell_material_and_liner is not None
    assert tms_bench.cell_material_and_liner.state.is_value
    assert (
        tms_bench.cell_material_and_liner.state.value
        == "platinum TMS reactor (boat, carrier, probe)"
    )
    nacl = observations_named(hastie, "hastie_1981_nacl_liquid_logP_prose")
    assert nacl
    assert nacl[0].experiment_id == table2_experiment.experiment_id

    allibert = _migrate_real_extract(tmp_path, "kems-051-allibert-1981.yaml")
    emf = observations_named(allibert, "allibert_1981_table3_emf_solid_cells")
    mo = experiment_named(allibert, "allibert-1981-mo-kems")
    assert all(item.experiment_id != mo.experiment_id for item in emf)
    assert allibert.experiments[emf[0].experiment_id].bench_id is None


def test_offer10_extract_locators_use_physical_pdf_pages() -> None:
    source_ids = (
        "sesko-2022-tum-thesis",
        "sutherland-2025-mare-simulant-volatile-loss",
        "ewing-1963-sodium-potassium-properties",
        "suzuki-2026-vacuum-oxygen-extraction",
        "mamiya-2000-sio-disproportionation",
    )

    def walk(value):
        if isinstance(value, dict):
            if "page" in value or "pdf_page_index" in value:
                assert value["page"] == value["pdf_page_index"] + 1
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for source_id in source_ids:
        document = yaml.safe_load((EXTRACTS / f"{source_id}.yaml").read_text())
        walk(document)

    ewing = yaml.safe_load(
        (EXTRACTS / "ewing-1963-sodium-potassium-properties.yaml").read_text()
    )
    assert ewing["fidelity_samples"][0]["locator"] == {
        "page": 12,
        "published_page": 7,
        "pdf_page_index": 11,
        "figure": "6",
    }
