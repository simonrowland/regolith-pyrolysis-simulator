from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from tools import validate_literature_extracts as vle


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/shornikov-2025-ca-al-inclusion-evaporation.yaml"
)


def test_table1_render_values_and_context_migration() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = next(
        item
        for item in document["species"]["CaO"]["context"]
        if item["observation_id"] == "shornikov_2025_table1_initial_compositions"
    )
    rows = context["values"]["rows"]

    assert len(rows) == 4
    assert context["values"]["row_count"] == 4
    assert context["values"]["source_numeric_cell_count"] == 32
    assert context["values"]["numeric_cell_count"] == 28
    assert context["locator"]["page"] == 2
    assert context["locator"]["published_page"] == 552
    assert context["locator"]["pdf_page_index"] == 1
    assert context["locator"]["table"] == "1"

    # These values and row/column assignments were confirmed on the 600 dpi render.
    assert rows[0]["A_nazarov_1984_mol_pct"] == "43.4"
    assert rows[1]["B_grossman_2000_mol_pct_range"] == "12.7–27.4"
    assert rows[2]["A_ivanova_2023_mol_pct_range"] == "23.8–25.4"
    assert rows[3]["FUN1_mendybaev_2013_mol_pct"] == "33.1"
    assert all("M_Dyakonova_1979_mol_pct" not in row for row in rows)

    samples = {sample["path"]: sample for sample in document["fidelity_samples"]}
    for sample in samples.values():
        assert sample["locator"]["page"] == 2
        assert sample["locator"]["published_page"] == 552
        assert sample["locator"]["table"] == "1"
        assert not vle.check_fidelity_sample_matches(document, sample)

    mutated = deepcopy(document)
    mutated["species"]["CaO"]["context"][0]["values"]["rows"][0][
        "A_nazarov_1984_mol_pct"
    ] = "43.5"
    mutation_errors = vle.check_fidelity_sample_matches(
        mutated, samples[next(iter(samples))]
    )
    assert any("mismatch" in error for error in mutation_errors)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    contexts = [
        item
        for work_contexts in result.context_by_work.values()
        for item in work_contexts
    ]
    assert len(result.observations) == 0
    assert len(contexts) == 9
    assert len(result.queue) == 1
    assert result.queue[0].why == "extract yielded no observations"
    assert not result.validation.hard_issues
    assert all(
        observation.identity.quantity.is_value
        for observation in result.observations.values()
    )
    assert all(
        observation.identity.species.phase.value != "unknown"
        for observation in result.observations.values()
    )

    migrated_contexts = {
        item["observation_id"]: item for item in contexts
    }
    migrated_table = migrated_contexts["shornikov_2025_table1_initial_compositions"]
    migrated_rows = migrated_table["values"]["rows"]
    assert migrated_table["locator"]["page"] == 2
    assert migrated_table["locator"]["table"] == "1"
    assert migrated_rows[0]["A_nazarov_1984_mol_pct"] == "43.4"
    assert migrated_rows[1]["B_grossman_2000_mol_pct_range"] == "12.7–27.4"
    assert migrated_rows[3]["FUN1_mendybaev_2013_mol_pct"] == "33.1"

    migrated_summary = migrated_contexts[
        "shornikov_2025_residue_and_pressure_prose_summary"
    ]
    assert migrated_summary["locator"]["page"] == 5
    assert migrated_summary["values"]["facts"][2]["locator"]["page"] == 6

    figure4 = next(
        item
        for item in contexts
        if item["observation_id"] == "shornikov_2025_fig4_cai_partial_pressure_curves"
    )
    assert figure4["values"]["method_class"] == "measured_reduced"
    assert figure4["values"]["gas_species_as_printed"] == "Ca, Mg, Al, SiO"
    assert figure4["values"]["not_plotted_as_pressure"] == "Na, K, Fe"
    assert "points" not in figure4["values"]

    figure5 = next(
        item
        for item in contexts
        if item["observation_id"] == "shornikov_2025_fig5_residual_composition_curves"
    )
    assert figure5["values"]["method_class"] == "model_derived"
    assert figure5["values"]["axes_as_printed"]["x"] == "T, °C"
    assert figure5["values"]["weight_loss_axis_as_printed"].startswith("none;")

    method = next(
        item
        for item in contexts
        if item["observation_id"] == "shornikov_2025_experimental_method_context"
    )
    unknown_conditions = method["values"]["stated_unmeasured_run_conditions"]
    assert unknown_conditions["total_pressure"]["state"]["reason"] == (
        "not_printed_as_an_experiment_condition"
    )
    assert unknown_conditions["fO2"]["state"]["tag"] == "unknown"
    assert unknown_conditions["sample_composition"]["state"]["tag"] == "unknown"
    assert unknown_conditions["sweep_gas"]["state"]["tag"] == "unknown"
    assert method["values"]["output_bound_not_a_condition"][
        "total_vapor_pressure_over_melt_atm"
    ] == "<10^-4"

    result_summary = migrated_summary
    facts = result_summary["values"]["facts"]
    assert "Figure 5 calculations" in result_summary["values"]["attribution"]
    assert "Figure 8 O2/O trend" in result_summary["values"]["attribution"]
    assert facts[0]["value_as_printed"] == "zero"
    assert facts[0]["locator"]["page"] == 5
    assert facts[1]["fact"].endswith("2300°C, at low content.")
    assert facts[2]["value_as_printed"] == "~20"
    assert facts[2]["locator"]["page"] == 6
    assert facts[3]["value_as_printed"] == "~16.5"
    assert facts[3]["locator"]["page"] == 6
