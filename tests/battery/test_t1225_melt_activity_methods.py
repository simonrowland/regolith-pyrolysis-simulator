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
            ("kume_2000_table",),
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
        if any(selector in observation.observation_id for selector in observation_selectors)
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
    assert not [
        residual
        for residual in residuals
        if residual.reference in target_ids
        and residual.status is ResidualStatus.REFUSED
        and residual.refusal is not None
        and residual.refusal.reason.value == "method_unknown"
    ]
