import re
import subprocess
from pathlib import Path

from simulator.accounting import load_species_formulas


def test_local_factsage_exports_are_gitignored():
    repo = Path(__file__).parent.parent
    paths = [
        "config/local-factsage-export.cst",
        "config/local-factsage-export.dat",
        "config/license.lic",
        "config/factsage-license.txt",
    ]

    result = subprocess.run(
        ["git", "check-ignore", *paths],
        cwd=repo,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    ignored = set(result.stdout.splitlines())
    assert ignored == set(paths)


def test_simulator_has_no_forbidden_internal_kg_mutations():
    repo = Path(__file__).parent.parent
    forbidden = [
        r"oxygen_cumulative_kg \+=",
        r"O2_vented_cumulative_kg \+=",
        r"train\.stages\[6\].*O2",
        r"stages\[6\]\.collected_kg\['O2'\]",
        r"composition_kg\[[^\]]+\]\s*=",
        r"collected_kg\[[^\]]+\]\s*=",
        r"shuttle_.*inventory_kg\s*-=",
        r"thermite_Mg_inventory_kg\s*-=",
        r"terminal\.oxygen_stored",
        r"terminal\.oxygen_vented_to_vacuum",
    ]

    result = subprocess.run(
        ["rg", "-n", "|".join(forbidden), "simulator"],
        cwd=repo,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1, result.stdout


def test_forbidden_internal_kg_mutation_patterns_match_samples():
    samples = {
        r"oxygen_cumulative_kg \+=": "self.oxygen_cumulative_kg += 1.0",
        r"O2_vented_cumulative_kg \+=": "self.O2_vented_cumulative_kg += vented",
        r"composition_kg\[[^\]]+\]\s*=": "self.melt.composition_kg['FeO'] = 0",
        r"collected_kg\[[^\]]+\]\s*=": "stage.collected_kg['O2'] = 1",
        r"terminal\.oxygen_stored": "terminal.oxygen_stored",
    }

    for pattern, sample in samples.items():
        assert re.search(pattern, sample), pattern


def test_species_catalog_loads_case_sensitive_species_ids():
    repo = Path(__file__).parent.parent

    formulas = load_species_formulas(repo / "data" / "species_catalog.yaml")

    assert dict(formulas["Co"].elements) == {"Co": 1.0}
    assert dict(formulas["CO"].elements) == {"C": 1.0, "O": 1.0}
