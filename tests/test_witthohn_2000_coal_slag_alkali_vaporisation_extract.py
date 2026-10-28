from __future__ import annotations

from pathlib import Path

from simulator.battery.enums import AdmissionStatus, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/witthohn-2000-coal-slag-alkali-vaporisation.yaml"
)


def test_figure_only_measurements_remain_typed_refusals_after_migration() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    rows = {
        row.observation_id.split("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    assert len(rows) == 7
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in rows.values())
    assert all(row.admission.status is AdmissionStatus.REJECTED for row in rows.values())
    pressure_rows = [
        row for observation_id, row in rows.items() if "_g_fig" in observation_id
    ]
    assert len(pressure_rows) == 4
    assert all(row.identity.quantity.value.value == "p_partial" for row in pressure_rows)
    activity_rows = [
        row
        for observation_id, row in rows.items()
        if "_na2o_fig" in observation_id or "_k2o_fig" in observation_id
    ]
    assert len(activity_rows) == 3
    assert all(row.identity.quantity.value.value == "activity" for row in activity_rows)
    assert all(row.identity.species.phase.value.value == "l" for row in activity_rows)
    assert all(
        row.evidence.class_.value.value == "measured_reduced"
        for row in pressure_rows
    )
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()

    contexts = {
        row["observation_id"]: row
        for rows in migrator.result.context_by_work.values()
        for row in rows
    }
    sample_contexts = {
        observation_id: row
        for observation_id, row in contexts.items()
        if observation_id.startswith("witthohn_2000_table1_sample_")
    }
    assert len(sample_contexts) == 7  # printed Table 1 sample columns A-G
    assert contexts["witthohn_2000_table1_sample_A_composition"]["values"][
        "SiO2_mass_fraction_percent"
    ] == "42.4"  # rendered PDF p. 15, Table 1
    assert contexts["witthohn_2000_table1_sample_C_composition"]["values"][
        "K2O_mass_fraction_percent"
    ] == "0.5"  # rendered PDF p. 15, Table 1
    assert contexts["witthohn_2000_table1_sample_G_composition"]["values"][
        "CaO_mass_fraction_percent"
    ] == "9.7"  # rendered PDF p. 15, Table 1

    # The page and printed figure locators cover three independent figure-only
    # candidates. None has a numeric point from the paper's table text.
    assert rows["witthohn_2000_na_g_fig2_figure_only"].locator.page == 16
    assert rows["witthohn_2000_na2o_fig4_figure_only"].locator.page == 17
    assert rows["witthohn_2000_na2o_fig5_figure_only"].locator.page == 18
    assert rows["witthohn_2000_k2o_fig6_model_figure_only"].locator.page == 18
