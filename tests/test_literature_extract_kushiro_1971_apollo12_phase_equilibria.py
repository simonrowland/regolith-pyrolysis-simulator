"""Source-grounded extract and migration checks for Kushiro et al. (1971)."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kushiro-1971-apollo12-phase-equilibria.yaml"
ROCK_12065_QUOTE = (
    "The results of the 1 atm quenching experiments on mixture A indicate that "
    "chromite in rock 12065 would have begun to crystallize at temperatures "
    "between 1200° and 1170°C and pigeonite near 1170°C."
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_rock_12065_estimate_binding(row: dict) -> None:
    assert row["experiment"] == "kushiro-1971-rock-12065-45"
    assert row["values"]["method_class"] == "authors_estimate"
    assert row["values"]["sentence_as_printed"] == ROCK_12065_QUOTE
    assert (row["locator"]["page"], row["locator"]["published_page"]) == (13, 493)
    assert row["point_conditions"]["composition"]["state"] == {
        "tag": "unknown",
        "reason": "not_published",
    }
    for condition in ("oxygen_partial_pressure", "total_pressure_Pa", "fO2_Pa"):
        assert row["point_conditions"][condition]["state"]["tag"] == "unknown"


def _assert_mixture_b_source_conditions(row: dict) -> None:
    conditions = row["point_conditions"]
    assert conditions["total_pressure_Pa"]["state"] == {
        "tag": "value",
        "value": "101325",
    }
    assert conditions["oxygen_partial_pressure"]["state"] == {
        "tag": "value",
        "value": "10^-13",
    }
    assert conditions["oxygen_partial_pressure"]["units"] == "atm"
    assert conditions["fO2_Pa"]["state"]["tag"] == "unknown"
    locator = conditions["total_pressure_Pa"]["locator"]
    assert (locator["page"], locator["published_page"], locator["table"]) == (
        11,
        491,
        "4",
    )
    assert conditions["oxygen_partial_pressure"]["locator"] == locator


def test_extract_validates_and_render_pins_detect_mutation() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    mutated = deepcopy(document)
    liquidus = next(
        row
        for row in mutated["species"]["SiO2"]["observations"]
        if row["observation_id"] == "kushiro_1971_mixture_b_chromite_liquidus"
    )
    liquidus["values"]["T_C"] = 1331
    assert check_all_fidelity_samples_match(mutated, label=EXTRACT.stem)


def test_isolated_migration_maps_compositions_temperatures_and_unknown_conditions() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    records = list(result.observations.values())

    assert len(records) == len(document["species"]["SiO2"]["observations"]) == 8
    assert not result.validation.hard_issues
    assert not result.registry_issues
    assert len(result.queue) == 1
    assert result.queue[0].observation_id.endswith(
        "kushiro_1971_mixture_a_chromite_first_appearance_bracket"
    )
    assert result.queue[0].axes == ["value"]
    assert all(row.identity.quantity.is_value for row in records)
    assert all(
        row.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
        for row in records
    )
    assert all(row.identity.species.phase.is_value for row in records)
    assert all(row.identity.species.phase.value.value == "l" for row in records)

    by_id = {row.observation_id.rsplit("::", 1)[-1]: row for row in records}
    expected_temperatures = {
        "kushiro_1971_mixture_b_chromite_liquidus": "1330",
        "kushiro_1971_mixture_b_olivine_first_appearance": "1280",
        "kushiro_1971_mixture_b_ca_poor_pyroxene_first_appearance": "1210",
        "kushiro_1971_mixture_b_augite_plagioclase_first_appearance": "1130",
        "kushiro_1971_mixture_b_ilmenite_first_appearance": "1100",
        "kushiro_1971_mixture_b_solidus": "1070",
    }
    # The identity subtype names the printed phase event so distinct first
    # appearances at one composition and pressure remain distinct transitions.
    expected_subtypes = {
        "kushiro_1971_mixture_b_chromite_liquidus": "liquidus",
        "kushiro_1971_mixture_b_olivine_first_appearance": "first_appearance_olivine",
        "kushiro_1971_mixture_b_ca_poor_pyroxene_first_appearance": "first_appearance_ca_poor_pyroxene",
        "kushiro_1971_mixture_b_augite_plagioclase_first_appearance": "first_appearance_augite_and_plagioclase",
        "kushiro_1971_mixture_b_ilmenite_first_appearance": "first_appearance_ilmenite",
        "kushiro_1971_mixture_b_solidus": "solidus",
    }
    for observation_id, temperature_c in expected_temperatures.items():
        row = by_id[observation_id]
        assert row.value.kind is ValueKind.POINT
        assert row.point_conditions is not None
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal(temperature_c) + Decimal("273.15")
        assert temperature.locator is not None
        assert temperature.locator.page == 12
        assert temperature.locator.published_page == 492
        assert row.identity.species.phase.value.value == "l"
        assert row.identity.subtype is not None and row.identity.subtype.is_value
        assert row.identity.subtype.value == expected_subtypes[observation_id]

    estimate_ids = (
        "kushiro_1971_mixture_a_chromite_first_appearance_bracket",
        "kushiro_1971_mixture_a_pigeonite_first_appearance_near_1170",
    )
    source_rows = {
        row["observation_id"]: row
        for row in document["species"]["SiO2"]["observations"]
    }
    for observation_id in estimate_ids:
        source_row = source_rows[observation_id]
        _assert_rock_12065_estimate_binding(source_row)
        migrated = by_id[observation_id]
        assert migrated.experiment_id == "kushiro-1971-rock-12065-45"
        assert migrated.evidence.class_.is_value
        assert migrated.evidence.class_.value is EvidenceClass.AUTHOR_ESTIMATE
        assert migrated.identity.composition is not None
        assert migrated.identity.composition.is_unknown
        assert migrated.identity.total_pressure_Pa is not None
        assert migrated.identity.total_pressure_Pa.is_unknown
        assert migrated.point_conditions["composition"].state.is_unknown
        assert migrated.point_conditions["oxygen_partial_pressure"].state.is_unknown
        assert migrated.point_conditions["fO2_Pa"].state.is_unknown
        assert migrated.locator is not None
        assert (migrated.locator.page, migrated.locator.published_page) == (13, 493)

    pigeonite = by_id["kushiro_1971_mixture_a_pigeonite_first_appearance_near_1170"]
    assert pigeonite.value.kind is ValueKind.POINT
    assert pigeonite.point_conditions["temperature_K"].state.value == Decimal("1443.15")
    assert source_rows["kushiro_1971_mixture_a_pigeonite_first_appearance_near_1170"]["values"]["approximate"] is True

    # Mutations of the reviewed bindings are rejected by the focused pins.
    for key, value in (
        ("experiment", "kushiro-1971-mixture-a"),
        ("method_class", "measured_direct"),
    ):
        mutated = deepcopy(source_rows[estimate_ids[1]])
        if key == "method_class":
            mutated["values"][key] = value
        else:
            mutated[key] = value
        with pytest.raises(AssertionError):
            _assert_rock_12065_estimate_binding(mutated)
    mutated = deepcopy(source_rows[estimate_ids[1]])
    mutated["point_conditions"]["composition"]["state"] = {
        "tag": "value",
        "value": {"SiO2_wt_pct": "47.1"},
    }
    with pytest.raises(AssertionError):
        _assert_rock_12065_estimate_binding(mutated)

    mixture_b_row = by_id["kushiro_1971_mixture_b_chromite_liquidus"]
    expected_mixture_b = {
        "SiO2": "46.0", "Al2O3": "9.7", "TiO2": "3.0", "Cr2O3": "0.6",
        "FeO": "20.0", "MnO": "0.25", "MgO": "10.2", "CaO": "10.0",
        "Na2O": "0.27", "K2O": "0.06",
    }
    expected_moles = wt_pct_to_mole_fraction(
        {oxide: Decimal(value) for oxide, value in expected_mixture_b.items()}
    )
    assert mixture_b_row.identity.composition is not None
    assert mixture_b_row.identity.composition.is_value
    assert mixture_b_row.identity.composition.value.as_map() == expected_moles.as_map()
    composition_condition = mixture_b_row.point_conditions["composition"]
    assert composition_condition.state.is_value
    assert composition_condition.locator is not None
    # The transition link is stated on p. 492; the experiment's composition is
    # independently located to Table 3 on p. 490.
    assert composition_condition.locator.page == 12
    assert composition_condition.locator.published_page == 492
    mixture_b_initial = result.experiments[
        mixture_b_row.experiment_id
    ].sample.initial_composition
    assert mixture_b_initial is not None and mixture_b_initial.state.is_value
    assert mixture_b_initial.locator is not None
    assert mixture_b_initial.locator.page == 10
    assert mixture_b_initial.locator.published_page == 490
    assert mixture_b_initial.locator.table == "3"

    mixture_b_ids = [key for key in expected_temperatures if "mixture_b" in key]
    assert len(mixture_b_ids) == 6
    for observation_id in mixture_b_ids:
        row = by_id[observation_id]
        assert row.identity.total_pressure_Pa is not None
        assert row.identity.total_pressure_Pa.is_value
        assert row.identity.total_pressure_Pa.value == Decimal("101325")
        pressure = row.point_conditions["total_pressure_Pa"]
        assert pressure.state.is_value
        assert pressure.state.value == Decimal("101325")
        assert pressure.locator is not None
        assert (pressure.locator.page, pressure.locator.published_page, pressure.locator.table) == (11, 491, "4")
        oxygen = row.point_conditions["oxygen_partial_pressure"]
        assert oxygen.state.is_value
        assert oxygen.state.value == "10^-13"
        assert oxygen.locator == pressure.locator
        assert row.point_conditions["fO2_Pa"].state.is_unknown

    for observation_id in mixture_b_ids:
        source_row = source_rows[observation_id]
        _assert_mixture_b_source_conditions(source_row)
        for condition in ("total_pressure_Pa", "oxygen_partial_pressure"):
            mutated = deepcopy(source_row)
            del mutated["point_conditions"][condition]
            with pytest.raises((AssertionError, KeyError)):
                _assert_mixture_b_source_conditions(mutated)

    bracket = by_id["kushiro_1971_mixture_a_chromite_first_appearance_bracket"]
    assert bracket.identity.quantity.is_value
    assert bracket.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
    assert bracket.value.kind is ValueKind.UNAVAILABLE

    # Table 4 has 35 printed runs; merged pressure cells are associated with their run rows.
    context = next(
        item
        for item in document["species"]["SiO2"]["context"]
        if item["observation_id"] == "kushiro_1971_table4_quenching_runs"
    )
    runs = context["values"]["rows"]
    assert context["locator"]["page"] == 11
    assert context["locator"]["published_page"] == 491
    assert context["locator"]["pdf_page_index"] == 10
    assert context["locator"]["table"] == "4"
    assert len(runs) == context["values"]["row_count"] == 35
    assert runs[0]["temperature_C_as_printed"] == "1230"
    assert runs[1]["phase_assemblage_as_printed"] == (
        "Gl + Sp + Cpx(Ca5.6Mg72.2Fe22.2 and Ca5.0Mg73.0Fe22.0) + Ol?"
    )
    high_pressure = runs[19]
    assert high_pressure["pressure_as_printed"] == "10 kb"
    assert high_pressure["temperature_C_as_printed"] == "1400 ± 10"
    assert high_pressure["oxygen_condition_status"].startswith("not stated")
    assert "oxygen_condition_as_printed" not in high_pressure
    assert high_pressure["experiment"] == "kushiro-1971-mixture-b"
    assert runs[0]["experiment"] == "kushiro-1971-mixture-a"

    natural = next(
        item
        for item in document["species"]["SiO2"]["context"]
        if item["observation_id"] == "kushiro_1971_rock_12065_45_narrative_runs"
    )
    assert len(natural["values"]["runs"]) == 4
    natural_experiment = next(
        item for item in document["experiments"] if item["experiment_id"] == "kushiro-1971-rock-12065-45"
    )
    assert natural_experiment["sample"]["initial_composition"]["state"] == {
        "tag": "unknown",
        "reason": "not_published",
    }
    assert natural["values"]["runs"][0]["temperature_C_as_printed"] == "1200°"


def test_rendered_table_and_prose_locators_are_preserved() -> None:
    document = _document()
    mixture_a = next(item for item in document["experiments"] if item["experiment_id"] == "kushiro-1971-mixture-a")
    mixture_b = next(item for item in document["experiments"] if item["experiment_id"] == "kushiro-1971-mixture-b")
    assert mixture_a["sample"]["initial_composition"]["state"]["value"]["components"][0] == ["SiO2", "47.1"]
    assert mixture_b["sample"]["initial_composition"]["state"]["value"]["components"][0] == ["SiO2", "46.0"]

    rows = next(
        item["values"]["rows"]
        for item in document["species"]["SiO2"]["context"]
        if item["observation_id"] == "kushiro_1971_table4_quenching_runs"
    )
    for run_number in (1, 20, 35):
        locator = rows[run_number - 1]["locator"]
        assert (locator["page"], locator["published_page"], locator["pdf_page_index"], locator["table"]) == (11, 491, 10, "4")
    assert rows[0]["locator"]["row"] == 1
    assert rows[19]["locator"]["row"] == 20
    assert rows[34]["locator"]["row"] == 35

    transition = next(
        item
        for item in document["species"]["SiO2"]["observations"]
        if item["observation_id"] == "kushiro_1971_mixture_b_chromite_liquidus"
    )
    assert transition["locator"]["page"] == 12
    assert transition["locator"]["published_page"] == 492
    assert transition["values"]["T_C"] == 1330

    interval = next(
        item
        for item in document["species"]["SiO2"]["observations"]
        if item["observation_id"] == "kushiro_1971_mixture_a_chromite_first_appearance_bracket"
    )
    assert interval["values"]["temperature_as_printed"] == "between 1200° and 1170°C"
    assert interval["experiment"] == "kushiro-1971-rock-12065-45"
    assert interval["values"]["method_class"] == "authors_estimate"
    assert (interval["locator"]["page"], interval["locator"]["published_page"]) == (13, 493)
    assert interval["values"]["sentence_as_printed"] == ROCK_12065_QUOTE
    assert interval["point_conditions"]["composition"]["state"] == {
        "tag": "unknown",
        "reason": "not_published",
    }

    pigeonite = next(
        item
        for item in document["species"]["SiO2"]["observations"]
        if item["observation_id"] == "kushiro_1971_mixture_a_pigeonite_first_appearance_near_1170"
    )
    assert pigeonite["experiment"] == "kushiro-1971-rock-12065-45"
    assert pigeonite["values"]["method_class"] == "authors_estimate"
    assert pigeonite["values"]["approximate"] is True
    assert (pigeonite["locator"]["page"], pigeonite["locator"]["published_page"]) == (13, 493)
    assert pigeonite["values"]["sentence_as_printed"] == ROCK_12065_QUOTE
