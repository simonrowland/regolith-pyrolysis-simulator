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


def test_hashimoto_all_runs_carry_printed_nominal_run_pressure() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())

    for experiment in extract["experiments"]:
        pressure = experiment["pressure_environment"]["total_pressure_Pa"]
        assert pressure["state"] == {
            "tag": "value",
            "value": {
                "kind": "point",
                "point": "0.01333223684210526315789473684",
                "approximate": True,
            },
        }
        assert pressure["locator"] == {
            "published_page": 118,
            "pdf_page_index": 8,
            "section": "RESULTS AND DISCUSSION",
            "note": "Printed vacuum level about 10^{-4} Torr during the experiments; unit conversion only",
        }
