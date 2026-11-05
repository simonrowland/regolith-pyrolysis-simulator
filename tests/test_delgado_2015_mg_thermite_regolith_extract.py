from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import migrate, wt_pct_to_mole_fraction
from tools import validate_literature_extracts as vle


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT_PATH = (
    REPO_ROOT
    / "data"
    / "literature"
    / "extracts"
    / "delgado-2015-mg-thermite-regolith.yaml"
)

TABLE1_VALUES = {
    "SiO2": ("45.7", "43.48", "49.4"),
    "Al2O3": ("16.2", "22.09", "17.10"),
    "Fe2O3": ("12.4", "16.08", "10.87"),
    "CaO": ("10.0", "6.05", "10.45"),
    "MgO": ("8.7", "4.22", "6.08"),
    "Na2O": ("3.2", "2.34", "3.28"),
    "TiO2": ("1.9", "3.62", "1.09"),
}
SIMULANT_LABELS = ("JSC-1A [9]", "JSC-Mars-1A [10]", "Mojave Mars [10]")
SIMULANT_SUFFIXES = ("jsc_1a", "jsc_mars_1a", "mojave_mars")


def _isolated_migration(tmp_path: Path, source_doc: dict):
    root = tmp_path / "tree"
    extracts = root / "data" / "literature" / "extracts"
    extracts.mkdir(parents=True)
    (root / "data" / "literature" / "compilations").mkdir(parents=True)
    source = source_doc["source"]
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "sources": [
                    {
                        "source_id": source_doc["source_id"],
                        "citation": source["citation"],
                        "doi": source["doi"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (extracts / EXTRACT_PATH.name).write_text(
        yaml.safe_dump(source_doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return migrate(root, write=False)


def _source_composition(observation: dict) -> dict[str, str]:
    printed = observation["point_conditions"]["composition"]["state"]["value"]
    assert printed["amount_basis"] == "mass_percent"
    return dict(printed["components"])


def test_delgado_table_cells_conditions_and_typed_refusals(tmp_path: Path) -> None:
    source = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    assert source["source_id"] == "delgado-2015-mg-thermite-regolith"
    assert vle.check_all_fidelity_samples_match(source, label=EXTRACT_PATH.stem) == []

    table_cells = []
    raw_compositions = {}
    for formula, expected_values in TABLE1_VALUES.items():
        observations = source["species"][formula]["observations"]
        assert len(observations) == 3
        for column, (label, suffix) in enumerate(
            zip(SIMULANT_LABELS, SIMULANT_SUFFIXES)
        ):
            observation = observations[column]
            observation_id = f"delgado_2015_table1_{formula.lower()}_{suffix}"
            assert observation["observation_id"] == observation_id
            assert observation["type"] == "composition_series"
            assert observation["phase"] == "condensed_solid"
            assert observation["units"] == "wt%"
            assert observation["locator"]["page"] == 3
            assert observation["locator"]["table"] == "1"

            points = observation["values"]["points"]
            assert len(points) == 1
            point = points[0]
            row_number = 3 + list(TABLE1_VALUES).index(formula)
            assert point == {
                "sample": label,
                "value": expected_values[column],
                "table_row_number": row_number,
                "table_column_number": column + 2,
            }
            assert observation["values"]["method_class"] == "quoted_attributed"
            assert observation["values"]["admission_status"] == "typed_refusal"

            raw_composition = _source_composition(observation)
            expected_raw_composition = {
                oxide: values[column] for oxide, values in TABLE1_VALUES.items()
            }
            assert raw_composition == expected_raw_composition
            assert sum(Decimal(value) for value in raw_composition.values()) < 100
            raw_compositions[observation_id] = expected_raw_composition
            table_cells.append((observation, label, row_number, column + 2))

    assert len(table_cells) == 21
    # A mutation to an interior cell must fail the source-page fidelity check.
    mutated = copy.deepcopy(source)
    mutated["species"]["Na2O"]["observations"][1]["values"]["points"][0][
        "value"
    ] = "2.35"
    assert any(
        "fidelity_samples[16]" in error and "mismatch" in error
        for error in vle.check_all_fidelity_samples_match(
            mutated, label="delgado-interior-mutation"
        )
    )
    mutated_legend = copy.deepcopy(source)
    xrd_context = next(
        context
        for context in mutated_legend["species"]["Mg"]["context"]
        if context["observation_id"]
        == "delgado_2015_xrd_phase_identification_jsc1a_mg"
    )
    xrd_context["values"]["phase_labels_as_printed"][1] = "Mg3O4"
    assert any(
        "fidelity_samples[22]" in error and "mismatch" in error
        for error in vle.check_all_fidelity_samples_match(
            mutated_legend, label="delgado-xrd-legend-mutation"
        )
    )

    result = _isolated_migration(tmp_path, source)
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 22
    quantity_unknown = {
        item.observation_id
        for item in result.observations.values()
        if item.identity.quantity.is_unknown
    }
    assert quantity_unknown == {
        item.observation_id for item in result.observations.values()
    }
    assert len(quantity_unknown) == 22
    assert all(
        item.value.kind is ValueKind.UNAVAILABLE
        for item in result.observations.values()
    )
    assert not any(
        item.identity.species.phase.is_unknown
        for item in result.observations.values()
    )

    migrated_by_source_id = {
        item.observation_id.split("::", maxsplit=2)[1]: item
        for item in result.observations.values()
    }
    assert len(migrated_by_source_id) == 22
    table_rows = [
        (observation, label, row_number, column_number)
        for observation, label, row_number, column_number in table_cells
    ]
    assert len(table_rows) == 21
    for observation, label, row_number, column_number in table_rows:
        observation_id = observation["observation_id"]
        item = migrated_by_source_id[observation_id]
        source_conditions = observation["point_conditions"]
        for key in ("total_pressure_Pa", "fO2_Pa"):
            assert key in source_conditions
            assert source_conditions[key]["state"]["tag"] == "unknown"

        conditions = item.point_conditions
        assert conditions["temperature_K"].state.is_unknown
        assert conditions["temperature_K"].locator.page == 3
        assert conditions["temperature_K"].locator.table == "1"
        pressure = conditions.get("total_pressure_Pa")
        assert pressure is None or pressure.state.is_unknown
        if pressure is not None:
            assert pressure.locator.table == "1"
        fugacity = conditions.get("fO2_Pa")
        assert fugacity is None or fugacity.state.is_unknown
        assert (
            item.identity.temperature_K is None
            or item.identity.temperature_K.is_unknown
        )
        assert (
            item.identity.total_pressure_Pa is None
            or item.identity.total_pressure_Pa.is_unknown
        )
        assert item.identity.fO2_Pa is None or item.identity.fO2_Pa.is_unknown

        expected_composition = wt_pct_to_mole_fraction(
            {
                oxide: Decimal(amount)
                for oxide, amount in raw_compositions[observation_id].items()
            }
        )
        assert item.identity.composition is not None
        assert item.identity.composition.is_value
        assert item.identity.composition.value.as_map() == expected_composition.as_map()
        point_composition = conditions["composition"]
        assert point_composition.state.is_value
        assert point_composition.state.value.as_map() == expected_composition.as_map()
        assert point_composition.locator.page == 3
        assert point_composition.locator.table == "1"
        assert point_composition.locator.note == (
            f"Table 1 row {row_number} ({observation['locator']['row']}), "
            f"column {column_number} ({label})."
        )

    # Pin distinct source coordinates after migration, including two columns
    # from the same printed row.
    first_locator = migrated_by_source_id[
        "delgado_2015_table1_sio2_jsc_1a"
    ].point_conditions["composition"].locator
    second_locator = migrated_by_source_id[
        "delgado_2015_table1_sio2_jsc_mars_1a"
    ].point_conditions["composition"].locator
    assert first_locator.note == "Table 1 row 3 (SiO2), column 2 (JSC-1A [9])."
    assert second_locator.note == (
        "Table 1 row 3 (SiO2), column 3 (JSC-Mars-1A [10])."
    )

    peak = next(
        item
        for item in result.observations.values()
        if item.observation_id.endswith("::delgado_2015_jsc1a_cdta_reaction_peak")
    )
    peak_source = next(
        item
        for item in source["species"]["Mg"]["observations"]
        if item["observation_id"] == "delgado_2015_jsc1a_cdta_reaction_peak"
    )
    assert peak.locator.page == 4
    assert peak.locator.figure == "3"
    assert peak.point_conditions["temperature_K"].state.is_unknown
    assert peak.point_conditions["temperature_K"].locator.figure == "3"
    printed_composition = peak_source["point_conditions"]["composition"]["state"][
        "value"
    ]
    assert printed_composition["amount_basis"] == "mass_percent"
    assert printed_composition["components"] == [["Mg", "26"], ["JSC-1A", "74"]]
    pressure = peak.point_conditions.get("total_pressure_Pa")
    assert pressure is None or pressure.state.is_unknown
    assert peak.point_conditions["fO2_Pa"].state.is_unknown
    assert peak.point_conditions["atmosphere"].state.value == "Argon flow"
    assert peak.point_conditions["heating_rate_C_min"].state.value == 10
    assert peak.point_conditions["argon_flow_mL_min"].state.value == 20
    assert peak.point_conditions["composition"].state.is_unknown
    assert peak.point_conditions["composition"].state.reason == (
        "mass-percent composition contains invalid, duplicate, unsupported or ambiguous components"
    )
    assert peak.identity.composition is None

    contexts = next(iter(result.context_by_work.values()))
    thermal_runs = next(
        context
        for context in contexts
        if context["observation_id"]
        == "delgado_2015_thermal_runs_with_26_wt_percent_mg"
    )
    mixtures = thermal_runs["values"]["mixtures"]
    assert [row["simulant_as_printed"] for row in mixtures] == [
        "JSC-Mars-1A",
        "Mojave Mars",
        "JSC-1A",
    ]
    assert [row["Mg_wt_percent"] for row in mixtures] == ["26", "26", "26"]
    assert all("simulant_wt_percent" not in row for row in mixtures[:2])
    assert mixtures[2]["simulant_wt_percent"] == "74"

    xrd = next(
        context
        for context in contexts
        if context["observation_id"] == "delgado_2015_xrd_phase_identification_jsc1a_mg"
    )
    assert xrd["locator"]["page"] == 4
    assert xrd["locator"]["figure"] == "4"
    assert xrd["values"]["phase_labels_as_printed"] == ["Mg", "MgO"]
    assert xrd["values"]["heating_stop_temperatures_C"] == ["500", "550", "590"]
    assert xrd["values"]["at_500_C"] == "No MgO."
    assert xrd["values"]["at_550_C"] == "Partial conversion of Mg to MgO."
    assert xrd["values"]["at_590_C"] == "All Mg converted to MgO."

    # Figure 4 labels are fidelity-pinned independently of context values;
    # Figure 3's approximate prose value is also source-pinned.
    assert source["fidelity_samples"][-2]["value"] == "about 560"
    assert source["fidelity_samples"][-1]["value"] == ["Mg", "MgO"]
