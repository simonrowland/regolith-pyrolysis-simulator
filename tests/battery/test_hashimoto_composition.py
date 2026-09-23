from pathlib import Path

import yaml


EXTRACT = Path(__file__).parents[2] / "data/literature/extracts/kems-015-hashimoto-1983.yaml"


def test_hashimoto_runs_carry_table_1_average_starting_composition() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    experiments = extract["experiments"]
    expected = {"SiO2": 35.43, "Al2O3": 3.16, "FeO": 35.04, "MgO": 23.84, "CaO": 2.53}

    assert len(experiments) == 31
    for experiment in experiments:
        composition = experiment["sample"]["printed_composition"]
        assert composition["state"]["value"] == expected
        assert composition["locator"]["published_page"] == 113
        assert composition["locator"]["table"] == "1"
