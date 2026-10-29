"""Source-grounded extract and migration checks for Kushiro et al. (1971)."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kushiro-1971-apollo12-phase-equilibria.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


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
        "kushiro_1971_mixture_a_pigeonite_first_appearance_near_1170": "1170",
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

    mixture_a_onset = by_id[
        "kushiro_1971_mixture_a_pigeonite_first_appearance_near_1170"
    ]
    assert mixture_a_onset.point_conditions["oxygen_partial_pressure"].state.is_value
    assert mixture_a_onset.point_conditions["oxygen_partial_pressure"].state.value == "10^-13"

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

    # The prose does not assign pressure or oxygen conditions to these six mixture-B transitions.
    for observation_id in expected_temperatures:
        if "mixture_b" not in observation_id:
            continue
        row = by_id[observation_id]
        assert row.identity.total_pressure_Pa is not None
        assert row.identity.total_pressure_Pa.is_unknown
        assert "total_pressure_Pa" not in row.point_conditions
        assert row.point_conditions["fO2_Pa"].state.is_unknown

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
    assert interval["locator"]["page"] == 12
