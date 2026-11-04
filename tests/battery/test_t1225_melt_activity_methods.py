from __future__ import annotations

from decimal import Decimal
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
    ("filename", "observation_selectors", "page", "pdf_page_index", "published_page"),
    [
        (
            "kume-2000-cao-activities.yaml",
            ("kume_2000_table2_sample_101",),
            2,
            1,
            555,
        ),
        (
            "ohta-1996-cao-mgo-al2o3-activities.yaml",
            (
                "ohta_1996_al2o3_activity_mgo_spinel_saturation_1873",
                "ohta_1996_al2o3_activity_mgo_spinel_saturation_1823",
                "ohta_1996_al2o3_activity_ca2_spinel_saturation_1873",
                "ohta_1996_al2o3_activity_ca2_spinel_saturation_1823",
            ),
            1,
            0,
            983,
        ),
        (
            "seki-2011-ca-o-solubility-fe-cao-al2o3.yaml",
            ("seki_2011_table3_calculated_a_CaO",),
            2,
            1,
            1370,
        ),
    ],
)
def test_printed_quench_equilibration_reaches_melt_activity_rows(
    tmp_path: Path,
    filename: str,
    observation_selectors: tuple[str, ...],
    page: int,
    pdf_page_index: int,
    published_page: int,
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
        assert experiment.locator.published_page == published_page
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
        # d-144: CaO(s) below T_fus is lime. Printed Ca-027/Ca-028 nodes:
        # 1800 K solid -440.375, liquid -406.017 kJ/mol;
        # 1900 K solid -420.996, liquid -389.048 kJ/mol.
        # 1823 K is 23/100 of that span, so
        # G_s = -440.375 + 0.23*19.379 = -435.91783
        # G_l = -406.017 + 0.23*16.969 = -402.11413
        # ΔG_fus = 33.80370 kJ/mol
        # a_l = 0.000228 * exp(-33803.70 / (8.31441 * 1823))
        from tests.battery.test_t1616_d144_single_polymorph import (
            printed_formation_gibbs_kJ,
        )

        assert printed_formation_gibbs_kJ("Ca-027", Decimal("1800")) == Decimal(
            "-440.375"
        )
        assert printed_formation_gibbs_kJ("Ca-027", Decimal("1900")) == Decimal(
            "-420.996"
        )
        assert printed_formation_gibbs_kJ("Ca-028", Decimal("1800")) == Decimal(
            "-406.017"
        )
        assert printed_formation_gibbs_kJ("Ca-028", Decimal("1900")) == Decimal(
            "-389.048"
        )
        g_solid = Decimal("-440.375") + (
            Decimal("-420.996") - Decimal("-440.375")
        ) * Decimal(23) / Decimal(100)
        g_liquid = Decimal("-406.017") + (
            Decimal("-389.048") - Decimal("-406.017")
        ) * Decimal(23) / Decimal(100)
        delta_g_kJ = g_liquid - g_solid
        assert delta_g_kJ == Decimal("33.80370")
        hand = Decimal("0.000228") * (
            -delta_g_kJ * Decimal(1000) / (Decimal("8.31441") * Decimal("1823"))
        ).exp()
        assert converted.identity.reference_state.value.endmember.phase.value is Phase.L
        assert observation.value.point == Decimal("0.000228")
        assert converted.value.point == hand
        identity_notice = next(
            notice
            for notice in converted.notices
            if notice.reason == "single_polymorph_identity_d144"
        )
        assert "Ca-027" in identity_notice.band
        assert "lime" in identity_notice.band
        assert identity_notice.authority is not None
        assert identity_notice.authority.value == "certified"
        own_row = scored[observation.observation_id]
        assert own_row.numeric is not None
        # d-144 (5b5d2a080) converts the 1823 K CaO(s) comparison with
        # ΔG_fus=33.80370 kJ/mol to the liquid reference, lowering the
        # residual by 0.969 dex.
        assert abs(own_row.numeric.value - Decimal("0.3123171356698106")) <= Decimal("0.001")
        assert own_row.score_eligible is False
        assert not any(
            notice.reason == "fusion_source_reference_phase_unprinted"
            for notice in own_row.notices
        )
        assert any(
            notice.reason == "single_polymorph_identity_d144"
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
    # d-165 keeps the missing polymorph as a secondary refusal; measurement
    # validity remains primary when the quoted source method is unknown.
    assert residual.refusal.reason is RefusalReason.METHOD_UNKNOWN
    assert residual.refusal.detail["secondary_reasons"] == [
        "fusion_source_reference_phase_unprinted"
    ]


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
