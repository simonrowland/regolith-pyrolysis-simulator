from __future__ import annotations

from pathlib import Path

import pytest

from simulator.battery.enums import Engine, MethodToken, Rail, ResidualStatus
from simulator.battery.score import ScoreContext, score_store
from tests.battery.test_migrate import _migrate_real_extract


@pytest.mark.parametrize(
    ("filename", "observation_selectors", "page", "pdf_page_index"),
    [
        (
            "kume-2000-cao-activities.yaml",
            ("kume_2000_table2_sample_101",),
            555,
            2,
        ),
        (
            "ohta-1996-cao-mgo-al2o3-activities.yaml",
            (
                "ohta_1996_al2o3_activity_mgo_spinel_saturation_1873",
                "ohta_1996_al2o3_activity_mgo_spinel_saturation_1823",
                "ohta_1996_al2o3_activity_ca2_spinel_saturation_1873",
                "ohta_1996_al2o3_activity_ca2_spinel_saturation_1823",
            ),
            983,
            1,
        ),
        (
            "seki-2011-ca-o-solubility-fe-cao-al2o3.yaml",
            ("seki_2011_table3_calculated_a_CaO",),
            1370,
            2,
        ),
    ],
)
def test_printed_quench_equilibration_reaches_melt_activity_rows(
    tmp_path: Path,
    filename: str,
    observation_selectors: tuple[str, ...],
    page: int,
    pdf_page_index: int,
) -> None:
    migrated = _migrate_real_extract(tmp_path, filename)
    observations = [
        observation
        for observation in migrated.observations.values()
        if any(
            selector in observation.observation_id
            and (
                filename == "seki-2011-ca-o-solubility-fe-cao-al2o3.yaml"
                or observation.observation_id.endswith(f"::{selector}")
            )
            for selector in observation_selectors
        )
    ]
    assert observations
    for observation in observations:
        experiment = migrated.experiments[observation.experiment_id]
        assert experiment.method.is_value
        assert experiment.method.value is MethodToken.QUENCH_EQUILIBRATION
        assert experiment.locator is not None
        assert experiment.locator.page == page
        assert experiment.locator.pdf_page_index == pdf_page_index
        assert experiment.locator.published_page == page
        assert experiment.locator.paragraph

    context = ScoreContext(
        works=migrated.works,
        experiments=migrated.experiments,
        observations=migrated.observations,
        benches=migrated.benches,
    )
    residuals, _ = score_store(
        context, rail=Rail.MELT_ACTIVITY, engines=(Engine.INTERNAL_ANALYTICAL,)
    )
    target_ids = {observation.observation_id for observation in observations}
    scored = {residual.reference: residual for residual in residuals}
    assert target_ids <= scored.keys()
    assert all(
        residual.refusal is None or residual.refusal.reason.value != "method_unknown"
        for residual in (scored[reference] for reference in target_ids)
    )
    if filename == "kume-2000-cao-activities.yaml":
        own_row = scored[next(iter(target_ids))]
        assert own_row.status is ResidualStatus.REFUSED
        assert own_row.refusal is not None
        assert own_row.refusal.reason.value == "unsupported"


def test_rein_chipman_quote_keeps_unknown_method_refusal(tmp_path: Path) -> None:
    migrated = _migrate_real_extract(tmp_path, "kume-2000-cao-activities.yaml")
    observation = next(
        row
        for row in migrated.observations.values()
        if row.observation_id.endswith("::kume_2000_table4_sample_301_sio2_activity")
    )
    experiment = migrated.experiments[observation.experiment_id]
    assert not experiment.method.is_value

    context = ScoreContext(
        works=migrated.works,
        experiments=migrated.experiments,
        observations=migrated.observations,
        benches=migrated.benches,
    )
    residuals, _ = score_store(
        context, rail=Rail.MELT_ACTIVITY, engines=(Engine.INTERNAL_ANALYTICAL,)
    )
    residual = next(row for row in residuals if row.reference == observation.observation_id)
    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason.value == "method_unknown"
