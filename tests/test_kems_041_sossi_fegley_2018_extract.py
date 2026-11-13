"""Pins explicit attributions on Sossi & Fegley's quoted Table 2 relations."""

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-041-sossi-fegley-2018.yaml"
EXPECTED = {
    "sossi_fegley_2018_table2_gamma_MoO3__MoO_3_": "O’Neill & Eggins 2002",
    "sossi_fegley_2018_table2_gamma_WO2__WO_2_": "O’Neill et al. 2008",
    "sossi_fegley_2018_table2_gamma_WO3__WO_3__relation_1673K": "O’Neill et al. 2008",
}
BECKETT_ROWS = {
    "sossi_fegley_2018_table2_gamma_Ca_CaO": "Beckett 2002, as cited in Table 2.",
    "sossi_fegley_2018_table2_gamma_Mg_MgO": "Beckett 2002, as cited in Table 2.",
}


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def _assert_explicit_attributions(document: dict) -> None:
    for observation_id, attribution in EXPECTED.items():
        nested = _rows(document)[observation_id]["values"]["rows"][1]
        assert nested["method_class"] == "quoted_attributed"
        assert nested.get("attribution") == attribution
    for observation_id, attribution in BECKETT_ROWS.items():
        nested = _rows(document)[observation_id]["values"]["rows"][0]
        assert nested["method_class"] == "quoted_attributed"
        assert nested.get("attribution") == attribution


def test_external_relations_name_the_attributed_papers() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_explicit_attributions(document)


def test_missing_attribution_mutation_is_rejected() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    row = _rows(mutated)[next(iter(EXPECTED))]["values"]["rows"][1]
    del row["attribution"]
    try:
        _assert_explicit_attributions(mutated)
    except AssertionError:
        return
    raise AssertionError("missing external attribution passed the source pin")


def test_beckett_model_values_are_kept_as_attributed_quotes() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_explicit_attributions(document)

    mutated = deepcopy(document)
    row = _rows(mutated)[next(iter(BECKETT_ROWS))]["values"]["rows"][0]
    row["method_class"] = "model_derived"
    try:
        _assert_explicit_attributions(mutated)
    except AssertionError:
        return
    raise AssertionError("external Beckett values passed as model_derived")


def test_isolated_migration_has_no_bad_table_lineage() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
