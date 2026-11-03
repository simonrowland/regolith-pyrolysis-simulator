"""Independent render pins and migration checks for Kress & Carmichael (1989)."""

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import (
    Migrator,
    _BARE_LOG_BAR_NOTICE,
    _BARE_LOG_SCALE_UNKNOWN,
    mass_fraction_pair_to_valence_ratio,
    wt_pct_to_mole_fraction,
)
from simulator.battery.oxygen_identity import log10_fo2_to_pa


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / (
    "data/literature/extracts/"
    "kress-carmichael-1989-lime-iron-silicate-redox.yaml"
)
OXIDES = ("SiO2", "Fe2O3", "FeO", "CaO")
IN_BAND_LOGS = {
    "93A": "-7.32",
    "98B": "-8.98",
    "99A": "-8.98",
    "101A": "-10.46",
}
_SCALE_UNKNOWN = "fO2 reference scale not printed (absolute vs buffer-relative)"
_BAR_NOTICE = (
    "fO2 reference state not printed; 1 bar convention assumed "
    "(1 atm alternative: +0.0057 dex)"
)

# Independent 600 dpi render transcription of Table 1, PDF page 3 (published
# page 2885), in printed column order after the run identifier.
TABLE1_RENDER = """
1A 1240.6 -0.68 46.87 25.69 2.78 25.60
8B 1287.3 -0.68 28.52 35.77 2.21 33.83
11A 1337.6 -0.68 46.84 24.51 3.73 25.58
23A 1437.4 -0.68 46.92 22.45 5.70 25.93
24B 1437.4 -0.68 28.88 33.66 4.22 34.93
38D 1485.8 -0.68 40.32 20.94 4.75 35.90
45A 1535.3 -0.68 47.13 20.46 7.59 26.69
47C 1535.3 -0.68 32.67 36.61 10.43 22.08
48D 1535.3 -0.68 39.96 20.13 5.56 35.47
55D 1584.8 -0.68 42.16 16.21 5.27 37.50
57C 1584.8 -0.68 32.94 34.03 12.60 21.75
93A 1343.4 -7.32 47.67 6.11 20.30 26.55
98B 1343.4 -8.98 29.81 6.74 27.26 35.69
99A 1343.4 -8.98 50.19 2.09 21.95 25.95
101A 1339.1 -10.46 48.50 1.39 22.82 27.60
123A 1293.5 -0.68 47.94 23.60 3.22 25.02
124B 1293.5 -0.68 28.24 35.34 2.27 33.92
126B 1292.6 -0.68 28.30 35.49 2.36 33.96
127A 1292.6 -0.68 46.01 24.49 3.38 25.71
129A 1293.2 -0.68 48.02 23.53 3.22 25.07
130B 1293.2 -0.68 28.21 35.58 2.28 34.02
134B 1293.7 -0.68 27.41 35.45 2.04 33.73
135A 1293.7 -0.68 46.28 24.31 3.31 25.68
138B 1292.9 -0.68 28.15 35.44 2.36 33.93
149A 1293.7 -0.68 46.47 24.58 3.31 25.68
150B 1293.7 -0.68 28.32 35.13 2.32 33.89
154B 1634.2 -0.68 28.90 29.12 7.96 34.73
155A 1634.2 -0.68 48.64 16.30 9.62 25.45
157A 1635.1 -0.68 47.38 16.73 10.07 26.08
161A 1634.6 -0.68 49.49 15.60 9.86 25.02
162B 1634.6 -0.68 28.54 29.19 8.23 34.43
166D 1589.1 -0.68 41.68 16.04 5.92 36.75
169B 1589.1 -0.68 28.91 29.84 7.36 34.73
173A 1589.1 -0.68 48.30 17.49 8.75 25.30
175C 1485.6 -0.68 32.94 36.80 9.12 22.41
180D 1486.6 -0.68 39.88 20.52 5.00 35.13
183A 1486.6 -0.68 46.70 20.45 7.12 25.87
185C 1391.4 -0.68 31.65 40.75 7.03 21.73
193A 1391.4 -0.68 47.64 22.02 5.05 25.46
194B 1391.4 -0.68 28.41 34.57 3.60 34.14
196D 1438.6 -0.68 39.56 22.49 3.86 34.77
199C 1438.6 -0.68 32.62 38.44 8.08 22.11
203A 1438.6 -0.68 47.00 22.15 5.82 25.99
204B 1438.6 -0.68 28.57 33.51 4.46 34.24
205AF3 1487.5 -0.68 38.82 35.68 11.84 15.07
206AF 1487.5 -0.68 41.83 30.33 10.26 18.40
229G 1544.0 -0.68 39.33 7.81 1.25 49.34
230H 1544.0 -0.68 57.46 6.76 2.87 31.95
231G 1444.0 -0.68 39.68 8.64 1.01 49.12
232H 1444.0 -0.68 57.33 7.95 1.89 31.84
245B 1543.7 -0.68 28.95 30.25 6.03 34.28
246B 1543.7 -0.68 28.79 30.28 5.99 34.38
"""


def rendered_rows():
    return [tuple(line.split()) for line in TABLE1_RENDER.strip().splitlines()]


def load_extract():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def observation_map(document):
    return {
        observation["observation_id"]: observation
        for group in document["species"].values()
        for observation in group.get("observations", [])
    }


def _assert_table1_matches_render(document):
    observations = observation_map(document)
    rows = rendered_rows()
    assert len(rows) == 52
    assert len({row[0] for row in rows}) == 52
    expected_ids = set()
    for sample, temp_c, log_fo2, *amounts in rows:
        composition = dict(zip(OXIDES, amounts, strict=True))
        row_locator = {"page": 3, "published_page": 2885, "table": "1", "row": sample}
        for oxide in OXIDES:
            observation_id = f"kc89_table1_{sample}_{oxide}"
            expected_ids.add(observation_id)
            observation = observations[observation_id]
            values = observation["values"]
            assert values["component_amount"] == composition[oxide]
            assert values["T_C"] == temp_c
            assert values["row_as_printed"] == sample
            assert observation["locator"]["page"] == 3
            assert observation["locator"]["published_page"] == 2885
            assert observation["locator"]["table"] == "1"
            assert observation["locator"]["row"] == sample
            assert observation["point_conditions"]["fO2_log"]["state"]["value"] == log_fo2
            assert observation["point_conditions"]["fO2_log"]["units"] == ""
            assert "note" not in observation["point_conditions"]["fO2_log"]
            pressure = observation["point_conditions"]["total_pressure_Pa"]
            assert pressure["state"]["tag"] == "unknown"
            assert "does not report total experimental pressure" in pressure["state"]["reason"]
            printed_map = observation["point_conditions"]["composition"]["state"]["value"]
            assert printed_map["amount_basis"] == "mass_percent"
            assert dict(printed_map["components"]) == composition
            assert observation["point_conditions"]["quench_state"]["state"]["value"] == "asserted_preserved"
            assert observation["phase"] == "glass"
            assert observation["regime"] == "quench_equilibration"
            assert values["method_class"] == (
                "author_derived" if oxide == "Fe2O3" else "measured_direct"
            )
            if oxide == "Fe2O3":
                assert "derivation" not in values
            else:
                assert values["method_as_printed"]

        fe2o3_id = f"kc89_table1_{sample}_Fe2O3"
        feo_id = f"kc89_table1_{sample}_FeO"
        ratio_id = f"kc89_table1_{sample}_Fe3plus_over_Fe2plus"
        expected_ids.add(ratio_id)
        ratio = observations[ratio_id]
        assert ratio["values"]["value"] == str(
            mass_fraction_pair_to_valence_ratio(
                Decimal(composition["Fe2O3"]),
                Decimal(composition["FeO"]),
                element="Fe",
                mass_fraction_basis=("Fe2O3", "FeO"),
            )
        )
        assert ratio["derived_from"] == [fe2o3_id, feo_id]
        assert ratio["values"]["valence_basis"]["numerator_oxidation_states"] == [3]
        assert ratio["values"]["valence_basis"]["denominator_kind"] == "oxidation_state"
        assert ratio["values"]["valence_basis"]["denominator_oxidation_state"] == 2
        assert ratio["values"]["valence_basis"]["value_form"] == "ratio"
        assert ratio["locator"]["page"] == 3
        assert ratio["locator"]["published_page"] == 2885
        assert ratio["locator"]["table"] == "1"
        assert ratio["locator"]["row"] == sample
        assert ratio["point_conditions"]["composition"]["state"]["value"]["components"] == [
            [oxide, composition[oxide]] for oxide in OXIDES
        ]
    actual_ids = {
        observation_id
        for observation_id in observations
        if observation_id.startswith("kc89_table1_")
    }
    assert actual_ids == expected_ids
    assert len(actual_ids) == 260


def test_every_table1_numeric_cell_matches_600dpi_render():
    _assert_table1_matches_render(load_extract())


def test_interior_render_cell_mutation_is_rejected():
    document = deepcopy(load_extract())
    observations = observation_map(document)
    observations["kc89_table1_11A_Fe2O3"]["values"]["component_amount"] = "24.52"
    with pytest.raises(AssertionError):
        _assert_table1_matches_render(document)


def _assert_bare_log_identity(observation, sample: str, log_fo2: str) -> None:
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None
    if sample in IN_BAND_LOGS:
        assert log_fo2 == IN_BAND_LOGS[sample]
        assert oxygen.is_value
        assert oxygen.value == log10_fo2_to_pa(Decimal(log_fo2), Decimal("100000"))
        assert any(notice.reason == _BAR_NOTICE for notice in observation.notices)
    else:
        assert log_fo2 == "-0.68"
        assert oxygen.is_unknown
        assert oxygen.reason == _SCALE_UNKNOWN
        assert all(notice.reason != _BAR_NOTICE for notice in observation.notices)


def test_isolated_migration_binds_every_value_condition_and_render_locator():
    assert _BARE_LOG_SCALE_UNKNOWN == _SCALE_UNKNOWN
    assert _BARE_LOG_BAR_NOTICE == _BAR_NOTICE
    rows = rendered_rows()
    assert sum(row[2] == "-0.68" for row in rows) == 48
    assert {row[0]: row[2] for row in rows if row[0] in IN_BAND_LOGS} == IN_BAND_LOGS
    document = load_extract()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 260
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert result.registry_issues == []
    assert sum(obs.value.kind is ValueKind.UNAVAILABLE for obs in result.observations.values()) == 0
    assert all(obs.identity.quantity.is_value for obs in result.observations.values())
    assert all(obs.identity.species.phase.is_value for obs in result.observations.values())
    assert {obs.identity.quantity.value for obs in result.observations.values()} == {
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
        Quantity.VALENCE_RATIO,
    }

    local_observations = {
        observation_id.rsplit("::", 1)[-1]: observation
        for observation_id, observation in result.observations.items()
    }
    for sample, temp_c, log_fo2, *amounts in rendered_rows():
        composition = dict(zip(OXIDES, amounts, strict=True))
        expected_composition = wt_pct_to_mole_fraction(
            {oxide: Decimal(amount) for oxide, amount in composition.items()}
        )
        expected_temperature = Decimal(temp_c) + Decimal("273.15")
        for oxide in OXIDES:
            local_id = f"kc89_table1_{sample}_{oxide}"
            observation = local_observations[local_id]
            assert observation.value.kind is ValueKind.POINT
            assert observation.value.point == Decimal(composition[oxide])
            assert observation.identity.temperature_K.value == expected_temperature
            _assert_bare_log_identity(observation, sample, log_fo2)
            assert observation.identity.composition.value == expected_composition
            assert observation.identity.total_pressure_Pa.is_unknown
            assert observation.point_conditions["temperature_K"].state.value == expected_temperature
            assert observation.point_conditions["fO2_log"].state.value == Decimal(log_fo2)
            assert observation.point_conditions["composition"].state.value == expected_composition
            assert observation.point_conditions["total_pressure_Pa"].state.is_unknown
            assert observation.point_conditions["quench_state"].state.value.value == "asserted_preserved"
            assert observation.locator.page == 3
            assert observation.locator.published_page == 2885
            assert observation.locator.table == "1"
            assert f"Table 1 sample {sample}" in (observation.locator.note or "")

        ratio_id = f"kc89_table1_{sample}_Fe3plus_over_Fe2plus"
        ratio = local_observations[ratio_id]
        assert ratio.value.point == mass_fraction_pair_to_valence_ratio(
            Decimal(composition["Fe2O3"]),
            Decimal(composition["FeO"]),
            element="Fe",
            mass_fraction_basis=("Fe2O3", "FeO"),
        )
        assert ratio.identity.temperature_K.value == expected_temperature
        _assert_bare_log_identity(ratio, sample, log_fo2)
        assert ratio.identity.composition.value == expected_composition
        assert ratio.identity.total_pressure_Pa.is_unknown
        assert ratio.point_conditions["temperature_K"].state.value == expected_temperature
        assert ratio.point_conditions["fO2_log"].state.value == Decimal(log_fo2)
        assert ratio.point_conditions["composition"].state.value == expected_composition
        # The migrator removes an unprinted total-pressure point condition from
        # valence-ratio rows; its required identity axis remains explicitly unknown.
        assert "total_pressure_Pa" not in ratio.point_conditions
        assert ratio.point_conditions["quench_state"].state.value.value == "asserted_preserved"
        assert f"Table 1 sample {sample}" in (ratio.locator.note or "")

    # Pin the distinct table/page locators after migration; context is lifted
    # only from species.<formula>.context by extract_context.py.
    context = {
        row["context_id"].rsplit("::", 1)[-1]: row
        for rows in result.context_by_work.values()
        for row in rows
    }
    assert set(context) == {
        "kc89_table2_equation3_fit_coefficients",
        "kc89_table3_measured_predicted_residual_comparison",
        "kc89_table4_equations9_10_fit_parameters",
        "kc89_table5_dingwell_brearley_comparison",
        "kc89_methods_conditions_and_quench_context",
        "kc89_figure_read_candidates",
    }
    table_locators = {
        (row["locator"]["page"], row["locator"]["table"])
        for row in context.values()
        if row["locator"].get("table") is not None
    }
    assert table_locators == {(5, "2"), (5, "3"), (6, "4"), (7, "5")}
    assert context["kc89_table2_equation3_fit_coefficients"]["values"]["coefficients_as_printed"] == {
        "a": "0.203",
        "b/1000": "12.909",
        "c": "-5.701",
        "d_Si": "-2.735",
        "d_Ca": "1.461",
    }
    assert context["kc89_table4_equations9_10_fit_parameters"]["values"]["parameters_as_printed"] == {
        "y": "0.3",
        "K2": "0.4",
        "ΔH0_kJ": "-145.9",
        "ΔS0_J_per_K": "-64.6",
        "ΔW_Si_kJ": "46.5",
        "ΔW_Ca_kJ": "-45.9",
    }
    assert len(context["kc89_table3_measured_predicted_residual_comparison"]["values"]["rows"]) == 52
    assert len(context["kc89_table5_dingwell_brearley_comparison"]["values"]["rows"]) == 18
    assert context["kc89_methods_conditions_and_quench_context"]["values"]["experiment_gases_as_printed"] == [
        "air",
        "CO-CO2 gas mixture",
    ]
    assert context["kc89_figure_read_candidates"]["values"]["digitisation"] == "undigitised"

    # The source-calculated Fe2O3 cells have unprinted total-iron inputs, so
    # the migrator's conditional evidence queue is retained and explained.
    assert Counter(entry.why for entry in result.queue) == Counter(
        {"conditional method_class author_derived lacks measured lineage": 52}
    )
