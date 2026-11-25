from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/borisov-borisovskiy-2024-ti-zircon.yaml"
RECIPE_ID = "borisov_borisovskiy_2024_starting_mixture_recipe"
RECIPE_TRANSCRIPTION_PATH = (
    "text/borisov-borisovskiy-2024-ti-zircon/pdftotext-layout.txt"
)
RECIPE_TRANSCRIPTION_SHA256 = (
    "d7c967376fa893ed67cac36a9d40e507470a0765643162f557a17556ec727a58"
)


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
        "source_path": "raw/borisov-borisovskiy-2024-ti-zircon/borisov-borisovskiy-2024-ti-zircon.pdf",
        "source_sha256": "dc1a232b026b35ea7373fe881b46ac0d6d152324138d4b8cd6b84d287a6daa43",
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
    expected_fidelity_pins = {
        f"species.TiO2.context[{RECIPE_ID}].values.components_as_printed.SiO2": (
            "56.9",
            "column left; printed starting-mixture SiO2 wt.% cell",
            "98",
        ),
        f"species.TiO2.context[{RECIPE_ID}].values.components_as_printed.Al2O3": (
            "10.1",
            "column left; printed starting-mixture Al2O3 wt.% cell",
            "98",
        ),
        f"species.TiO2.context[{RECIPE_ID}].values.components_as_printed.MgO": (
            "9.4",
            "column left; printed starting-mixture MgO wt.% cell",
            "98",
        ),
        f"species.TiO2.context[{RECIPE_ID}].values.components_as_printed.CaO": (
            "10.1",
            "column left; printed starting-mixture CaO wt.% cell",
            "98",
        ),
        f"species.TiO2.context[{RECIPE_ID}].values.components_as_printed.ZrO2": (
            "13.5",
            "column left; printed starting-mixture ZrO2 wt.% cell",
            "98",
        ),
        f"species.TiO2.context[{RECIPE_ID}].values.TiO2_target_wt_percent_as_printed.T8.value": (
            "8",
            "column left; approximate starting-mixture TiO2 target for T8",
            "108",
        ),
        f"species.TiO2.context[{RECIPE_ID}].values.TiO2_target_wt_percent_as_printed.T15.value": (
            "15",
            "column left; approximate starting-mixture TiO2 target for T15",
            "108",
        ),
        f"species.TiO2.context[{RECIPE_ID}].values.TiO2_target_wt_percent_as_printed.T25.value": (
            "25",
            "column left; approximate starting-mixture TiO2 target for T25",
            "108",
        ),
    }
    recipe_pins = {
        sample["path"]: (
            sample["value"],
            sample["locator"]["note"],
            sample["locator"]["line_range"],
        )
        for sample in document["fidelity_samples"]
        if sample.get("path", "").startswith(
            f"species.TiO2.context[{RECIPE_ID}]."
        )
    }
    assert recipe_pins == expected_fidelity_pins
    assert all(
        sample["locator"]["source_path"] == RECIPE_TRANSCRIPTION_PATH
        and sample["locator"]["source_sha256"] == RECIPE_TRANSCRIPTION_SHA256
        and sample["locator"]["page"] == 2
        and sample["locator"]["published_page"] == 498
        and sample["locator"]["pdf_page_index"] == 1
        and sample["locator"]["section"] == "Experimental details"
        for sample in document["fidelity_samples"]
        if sample.get("path", "").startswith(
            f"species.TiO2.context[{RECIPE_ID}]."
        )
    )
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
