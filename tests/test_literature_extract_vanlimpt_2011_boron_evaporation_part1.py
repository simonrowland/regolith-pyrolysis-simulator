from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/vanlimpt-2011-boron-evaporation-part1.yaml"
SID = "vanlimpt-2011-boron-evaporation-part1"


def test_gas_film_method_is_context_only_and_migrates_without_observations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_document(
        document,
        expected_source_id=SID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    species = document["species"]["Na"]
    assert species["observations"] == []
    contexts = {row["observation_id"]: row for row in species["context"]}
    apparatus = contexts["vanlimpt_2011_transpiration_gas_film_method_context"]
    assert apparatus["locator"]["page"] == 5
    assert "not numerically reported" in apparatus["values"]["gas_flow_and_composition"]
    film = contexts["vanlimpt_2011_gas_film_relation_context"]
    assert film["locator"]["page"] == 2
    assert "Qg,i = Sh (Di/Lg)" in film["values"]["gas_film_relation_as_printed"]
    conditions = contexts["vanlimpt_2011_experimental_range_and_species_scope_context"]
    assert conditions["locator"]["page"] == 6
    assert conditions["values"]["temperature_range"] == (
        "Experiments were performed at about 1400, 1450, and 1500 °C. "
        "The apparatus maximum is about 1500 °C."
    )
    assert "No measured Na(g) vapour pressure" in conditions["values"]["sodium_data_scope"]

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert migrator.result.observations == {}
    assert not migrator.result.validation.hard_issues
    assert sum(map(len, migrator.result.context_by_work.values())) == 3
