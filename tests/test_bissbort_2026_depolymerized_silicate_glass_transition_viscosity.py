"""Migration and transcription checks for Bissbort et al. (2026)."""

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/bissbort-2026-depolymerized-silicate-glass-transition-viscosity.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_render_pins_and_fidelity_mutation_are_detected() -> None:
    document = _document()
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []
    original_sample = next(
        row for row in document["species"]["FeO"]["observations"]
        if row["observation_id"] == "bissbort_2026_table1_rbs_mg10_feo"
    )

    mutated = deepcopy(document)
    sample = mutated["species"]["FeO"]["observations"]
    row = next(
        row for row in sample
        if row["observation_id"] == "bissbort_2026_table1_rbs_mg10_feo"
    )
    row["values"]["value"] = "59.33"
    assert check_all_fidelity_samples_match(mutated, label=EXTRACT.stem)

    transitions = document["species"]["SiO2"]["observations"]
    table2 = {row["observation_id"]: row for row in transitions}
    lower_limit = next(
        row
        for row in document["species"]["SiO2"]["context"]
        if row["observation_id"] == "bissbort_2026_table2_mg0_01_tp"
    )
    assert "bissbort_2026_table2_mg0_01_tp" not in table2
    assert lower_limit["values"]["T_C"] == "550"
    assert lower_limit["values"]["value_as_printed"] == "550*"
    assert lower_limit["values"]["lower_limit_note"] == "This Tp value marks a lower limit."
    assert table2["bissbort_2026_table2_mg76_06_tg"]["values"]["T_C"] == "626"
    assert table2["bissbort_2026_table2_mg100_15_tp"]["values"]["value_as_printed"] == "736"
    assert original_sample["values"]["value_as_published"] == "59.32"


def test_all_table_observations_migrate_with_species_composition_and_temperature() -> None:
    document = _document()
    source_rows = {
        row["observation_id"]: (container, row)
        for container, body in document["species"].items()
        for row in body["observations"]
    }
    migrator = Migrator(
        root=ROOT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    assert migrator.result.validation.hard_issues == ()
    assert migrator.result.registry_issues == []

    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 53
    assert not any(
        observation.observation_id.endswith("bissbort_2026_table2_mg0_01_tp")
        for observation in observations
    )
    assert Counter(
        observation.identity.quantity.value
        for observation in observations
    ) == {
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION: 27,
        Quantity.TRANSITION_TEMPERATURE: 26,
    }

    for observation in observations:
        source_id = observation.observation_id.rsplit("::", 1)[-1]
        container, source = source_rows[source_id]
        assert observation.identity.species.formula == container
        assert observation.locator.pdf_page_index == source["locator"]["page"] - 1

        if source["values"]["quantity"] == "transition_temperature":
            assert observation.value.point == Decimal(source["values"]["T_C"]) + Decimal("273.15")
            composition = observation.point_conditions["composition"]
            assert composition.locator.pdf_page_index == 2
            sample = source["sample_as_published"]
            if sample in {"Mg#0", "Mg#100"}:
                assert composition.state.is_unknown
            else:
                assert composition.state.is_value
                raw_composition = source["point_conditions"]["composition"]["state"]["value"]
                assert composition.state.value.amount_basis.value == raw_composition["amount_basis"]
                assert dict(composition.state.value.components) == {
                    oxide: Decimal(value) for oxide, value in raw_composition["components"]
                }
                assert observation.identity.composition.is_not_applicable
            assert observation.point_conditions["sample_id"].locator.pdf_page_index == 8
        else:
            assert observation.point_conditions["temperature_K"].state.is_unknown

    feo = next(
        observation for observation in observations
        if observation.observation_id.endswith("bissbort_2026_table1_rbs_mg10_feo")
    )
    assert feo.value.point == Decimal("59.32")
    mg76_tg = next(
        observation for observation in observations
        if observation.observation_id.endswith("bissbort_2026_table2_mg76_06_tg")
    )
    assert mg76_tg.value.point == Decimal("899.15")
