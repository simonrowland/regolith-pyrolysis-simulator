from decimal import Decimal
from pathlib import Path

import yaml
from copy import deepcopy

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/lee-bruckner-1984-cr-mn-glass-redox.yaml"


def test_lee_bruckner_table_values_migrate_as_species_bound_points():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 35
    assert not migrator.result.validation.hard_issues
    assert all(observation.value.kind is ValueKind.POINT for observation in observations)
    for observation in observations:
        if observation.identity.quantity.value is Quantity.VALENCE_RATIO:
            expected_species = "Cr" if "::lee_bruckner_1984_table1_" in observation.observation_id else "Mn"
            assert observation.identity.species.formula == expected_species
        else:
            assert observation.identity.quantity.value is Quantity.DENSITY
            assert observation.identity.species.formula == "unknown"
        assert observation.point_conditions["T_C"].state.tag.value == "unknown"
        assert observation.point_conditions["T_C"].locator is not None
        assert observation.point_conditions["composition"].state.tag.value == "unknown"
        assert observation.point_conditions["composition"].locator is not None
        if observation.identity.quantity.value is Quantity.VALENCE_RATIO:
            assert observation.point_conditions["atmosphere_as_printed"].state.value == "air"

    cr6 = next(
        observation for observation in observations
        if "::lee_bruckner_1984_table1_cr6_total::" in observation.observation_id
    )
    assert cr6.value.point == Decimal("15.4")
    assert cr6.locator.page == 2
    assert cr6.locator.published_page == 8
    density = next(
        observation for observation in observations
        if "::lee_bruckner_1984_table1_glass_density::" in observation.observation_id
    )
    assert density.value.point == Decimal("2.388")
    assert density.identity.species.formula == "unknown"

    assert check_all_fidelity_samples_match(source, label=EXTRACT.stem) == []
    mutated = deepcopy(source)
    mutated["species"]["Cr"]["observations"][1]["values"]["points"][0]["value"] = "15.5"
    assert check_all_fidelity_samples_match(mutated, label=EXTRACT.stem)
