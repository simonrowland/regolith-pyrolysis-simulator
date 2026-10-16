from __future__ import annotations

from pathlib import Path

import pytest

from simulator.battery.enums import (
    Engine,
    FO2Channel,
    MethodToken,
    Phase,
    Rail,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.records import SweepGas
from simulator.battery.score import (
    ScoreContext,
    fusion_comparison_reference,
    score_store,
)
from simulator.battery.waypoints import GapReason, oxygen_condition
from tests.battery.test_migrate import _migrate_real_extract
from tests.battery.test_waypoints import _bench


@pytest.mark.parametrize(
    ("filename", "observation_selectors", "page", "pdf_page_index"),
    [
        (
            "kume-2000-cao-activities.yaml",
            ("kume_2000_table2_sample_101",),
            555,
            1,
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
        observation = observations[0]
        experiment = migrated.experiments[observation.experiment_id]
        oxygen = oxygen_condition(experiment, _bench(), observation)
        assert oxygen.selected is None
        assert oxygen.absence is not None
        assert oxygen.absence.reason is GapReason.MISSING_EVIDENCE
        converted = fusion_comparison_reference(
            observation, engine=Engine.INTERNAL_ANALYTICAL
        )
        assert converted.identity.reference_state is not None
        assert converted.identity.reference_state.is_value
        assert converted.identity.reference_state.value.endmember.phase.value is Phase.L
        assert converted.value.point != observation.value.point
        own_row = scored[observation.observation_id]
        assert own_row.numeric is not None
        assert own_row.refusal is None
        assert any(
            "reference-state conversion (not model error)" in notice.reason
            for notice in own_row.notices
        )


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


def test_kume_own_experiments_keep_published_deoxidized_argon_control(
    tmp_path: Path,
) -> None:
    migrated = _migrate_real_extract(tmp_path, "kume-2000-cao-activities.yaml")
    experiments = {
        experiment.experiment_id.rsplit("::", 1)[-1]: experiment
        for experiment in migrated.experiments.values()
    }
    own_experiments = (
        "kume-2000-table2-1823k",
        "kume-2000-table3-1873k",
        "kume-2000-table4-1873k",
    )

    for experiment_id in own_experiments:
        experiment = experiments[experiment_id]
        assert experiment.fO2_control is not None
        control = experiment.fO2_control
        assert control.channel.is_value
        assert control.channel.value is FO2Channel.GAS_MIX
        assert control.oxygen_partial_pressure_Pa is not None
        assert control.oxygen_partial_pressure_Pa.state.is_unknown
        assert control.oxygen_partial_pressure_Pa.state.reason == "not_published"
        assert control.oxygen_partial_pressure_Pa.locator is not None
        assert control.oxygen_partial_pressure_Pa.locator.published_page == 555

        environment = experiment.pressure_environment
        assert environment.total_pressure_Pa is not None
        assert environment.total_pressure_Pa.state.is_unknown
        assert environment.total_pressure_Pa.state.reason == "not_published"
        sweep = environment.sweep_gas
        assert sweep.state.is_value
        assert isinstance(sweep.state.value, SweepGas)
        assert sweep.state.value.species == "Ar"
        assert sweep.state.value.flow_sccm.is_unknown
        assert sweep.state.value.flow_sccm.reason == "not_published"
        assert sweep.state.value.partial_pressure_Pa.is_unknown
        assert sweep.state.value.partial_pressure_Pa.reason == "not_published"
        assert sweep.locator is not None
        assert sweep.locator.published_page == 555
        assert '"equilibrated under a deoxidized argon atmosphere"' in sweep.locator.note

    own_experiment_ids = {experiment.experiment_id for experiment in experiments.values()
                          if experiment.experiment_id.rsplit("::", 1)[-1] in own_experiments}
    linked_observations = [
        observation
        for observation in migrated.observations.values()
        if observation.experiment_id in own_experiment_ids
    ]
    assert linked_observations
    for observation in linked_observations:
        experiment = migrated.experiments[observation.experiment_id]
        oxygen = oxygen_condition(experiment, _bench(), observation)
        assert oxygen.selected is None
        assert oxygen.absence is not None
        assert oxygen.absence.reason is GapReason.MISSING_EVIDENCE

    excluded_experiments = (
        "kume-2000-table1-1823k",
        "kume-2000-table1-1873k",
        "kume-2000-table4-rein-chipman-1873k",
    )
    for experiment_id in excluded_experiments:
        assert experiments[experiment_id].fO2_control is None
