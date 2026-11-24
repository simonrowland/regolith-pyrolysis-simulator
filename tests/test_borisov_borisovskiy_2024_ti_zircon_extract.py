from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/borisov-borisovskiy-2024-ti-zircon.yaml"
RECIPE_ID = "borisov_borisovskiy_2024_starting_mixture_recipe"


def test_printed_recipe_is_pinned_but_measured_glass_is_not_run_input() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    recipe = next(
        row
        for row in document["species"]["TiO2"]["context"]
        if row["observation_id"] == RECIPE_ID
    )
    assert recipe["locator"] == {
        "page": 2,
        "published_page": 498,
        "pdf_page_index": 1,
        "section": "Experimental details",
        "column": "left",
        "source_path": "tables/borisov-borisovskiy-2024-ti-zircon-starting-recipe.tsv",
        "line_range": "2-9",
    }
    assert recipe["values"]["components_as_printed"] == {
        "SiO2": "56.9",
        "Al2O3": "10.1",
        "MgO": "9.4",
        "CaO": "10.1",
        "ZrO2": "13.5",
    }
    assert recipe["values"]["TiO2_target_wt_percent_as_printed"] == {
        "T8": {"value": "8", "approximate": True},
        "T15": {"value": "15", "approximate": True},
        "T25": {"value": "25", "approximate": True},
    }
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    experiments = {row["experiment_id"]: row for row in document["experiments"]}
    new_sample_ids = {
        f"{series}-{suffix}"
        for series in ("T8", "T15", "T25")
        for suffix in ("16", "12", "14", "13", "17", "15")
    }
    new_runs = [
        experiment
        for experiment in experiments.values()
        if experiment.get("sample", {}).get("sample_id") in new_sample_ids
    ]
    assert len(new_runs) == 18
    assert all(
        not {"initial_composition", "printed_composition"}.intersection(
            experiment.get("sample", {})
        )
        for experiment in new_runs
    )

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert len(migrator.result.observations) == 198
    migrated_recipe = [
        row
        for rows in migrator.result.context_by_work.values()
        for row in rows
        if row.get("context_id", "").endswith(f"::context::{RECIPE_ID}")
    ]
    assert len(migrated_recipe) == 1
    assert migrated_recipe[0]["values"]["components_as_printed"] == recipe[
        "values"
    ]["components_as_printed"]
    by_id = {
        key.rsplit("::", 1)[-1]: row
        for key, row in migrator.result.observations.items()
    }
    assert all(row.identity.composition.is_unknown for row in by_id.values())

    new_run_product = by_id[
        "borisov-borisovskiy-2024-ti-zircon_t8_16_glass_sio2"
    ]
    assert new_run_product.value.point == Decimal("56.28")
    assert new_run_product.locator.page == 3
    assert new_run_product.identity.composition.is_unknown

    attributed_product = by_id[
        "borisov-borisovskiy-2024-ti-zircon_t11_75_glass_sio2"
    ]
    assert attributed_product.value.point == Decimal("54.03")
    assert attributed_product.identity.composition.is_unknown
