"""d-144 scores printed CaO(s) and MgO(s) as lime and periclase below T_fus.

The trigger is the closed constant, not "JANAF has one crystal table".
Kume's alumina activities are AlO1.5, which is outside the fusion pairs.
The Al2O3(s) counterexample is the formula a widened trigger would accept.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    Authority,
    Engine,
    EvidenceClass,
    NoticeKind,
    Phase,
    Polymorph,
    Quantity,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import State
from simulator.battery.score import (
    ScoreContext,
    compile_residual,
    fusion_comparison_reference,
)
from tests.battery import factories as F

_JANAF_R_J_PER_MOL_K = Decimal("8.31441")
_TABLES = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "literature"
    / "compilations"
    / "janaf"
    / "tables"
)


def printed_formation_gibbs_kJ(table_id: str, temperature_K: Decimal) -> Decimal:
    """Printed formation Gibbs node, kJ/mol, from the JANAF table YAML."""

    document = yaml.safe_load((_TABLES / f"{table_id}.yaml").read_text(encoding="utf-8"))
    for row in document["table"]["values"]:
        if Decimal(str(row["temperature"]["value"])) == temperature_K:
            return Decimal(str(row["formation_gibbs_energy"]["as_published"]))
    raise AssertionError(f"{table_id} has no formation Gibbs node at {temperature_K} K")


def hand_liquid_activity(
    solid_table: str,
    liquid_table: str,
    temperature_K: Decimal,
    activity_solid: Decimal,
) -> Decimal:
    """a_l from the 100 K JANAF chord. Independent of janaf_fusion_energy.

    G(T) = G(T0) + (G(T1) - G(T0)) * (T - T0) / (T1 - T0), with T0 and T1
    the surrounding 100 K nodes. ΔG_fus = G_liquid - G_solid, in kJ/mol.
    a_l = a_s * exp(-ΔG_fus * 1000 / (R * T)), R = 8.31441 J/(mol*K).
    """

    low = (temperature_K // Decimal(100)) * Decimal(100)
    high = low + Decimal(100)
    span = high - low
    fraction = (temperature_K - low) / span
    solid_low = printed_formation_gibbs_kJ(solid_table, low)
    solid_high = printed_formation_gibbs_kJ(solid_table, high)
    liquid_low = printed_formation_gibbs_kJ(liquid_table, low)
    liquid_high = printed_formation_gibbs_kJ(liquid_table, high)
    solid = solid_low + (solid_high - solid_low) * fraction
    liquid = liquid_low + (liquid_high - liquid_low) * fraction
    delta_g_kJ = liquid - solid
    return activity_solid * (
        -delta_g_kJ * Decimal(1000) / (_JANAF_R_J_PER_MOL_K * temperature_K)
    ).exp()


def _with_unspecified_polymorph(identity: Identity) -> Identity:
    reference_state = identity.reference_state
    assert reference_state is not None and reference_state.is_value
    endmember = reference_state.value.endmember
    return replace(
        identity,
        reference_state=State.of(
            replace(
                reference_state.value,
                endmember=replace(
                    endmember,
                    polymorph=State.unknown(
                        "solid-reference polymorph is not established"
                    ),
                ),
            )
        ),
    )


def _solid_activity(formula: str, temperature_K: Decimal, point: Decimal):
    experiment = F.kems_experiment()
    identity = _with_unspecified_polymorph(
        F.activity_identity(
            formula=formula,
            T_K=temperature_K,
            endmember_phase=Phase.CR,
            component_basis=formula,
        )
    )
    reference = F.observation(
        f"{formula}-unprinted-solid-at-{temperature_K}K",
        experiment.experiment_id,
        identity,
        point,
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-d144-counterexample",
    )
    return experiment, reference


def _assert_unprinted_refusal(reference, experiment) -> None:
    comparison = fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    assert comparison.value.point == reference.value.point
    assert comparison.identity.reference_state.value.endmember.phase.value is Phase.CR
    assert any(
        notice.reason == "fusion_source_reference_phase_unprinted"
        for notice in comparison.notices
    )
    assert not any(
        notice.reason == "single_polymorph_identity_d144"
        for notice in comparison.notices
    )
    context = ScoreContext(
        works={experiment.work_id: F.work(work_id=experiment.work_id)},
        experiments={experiment.experiment_id: experiment},
        observations={reference.observation_id: reference},
        extract_review={reference.source_id: "reviewed"},
        hostname="test",
    )
    residual, _candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context
    )
    assert residual.status is ResidualStatus.REFUSED
    assert residual.numeric is None
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
    assert residual.refusal.detail["reason"] == (
        "fusion_source_reference_phase_unprinted"
    )


def test_alumina_unprinted_solid_is_not_a_single_polymorph_identity() -> None:
    """Al2O3(s) stays refused. One JANAF crystal table is not d-144."""

    experiment, reference = _solid_activity("Al2O3", Decimal("1873"), Decimal("0.3"))
    _assert_unprinted_refusal(reference, experiment)


def test_d144_converts_kume_cao_and_mgo_and_refuses_the_other_solids(
    tmp_path: Path,
) -> None:
    from simulator.battery.generators.janaf import janaf_fusion_energy
    from tests.battery.test_migrate import _migrate_real_extract

    migrated = _migrate_real_extract(tmp_path, "kume-2000-cao-activities.yaml")

    def row(suffix: str):
        return next(
            observation
            for observation in migrated.observations.values()
            if observation.observation_id.endswith(f"::{suffix}")
        )

    def activity(formula: str):
        return next(
            observation
            for observation in migrated.observations.values()
            if isinstance(observation.identity, Identity)
            and quantity_token(observation.identity) is Quantity.ACTIVITY
            and observation.identity.species.formula == formula
            and observation.value.point is not None
            and observation.value.point > 0
        )

    cao = row("kume_2000_table2_sample_101")
    mgo = row("kume_2000_table3_sample_207_mgo_activity")
    silica = row("kume_2000_table1_sample_1_sio2_activity")
    alumina_half = activity("AlO1.5")
    context = ScoreContext(
        works=migrated.works,
        experiments=migrated.experiments,
        observations=migrated.observations,
        benches=migrated.benches,
    )

    cao_liquid = fusion_comparison_reference(cao, engine=Engine.INTERNAL_ANALYTICAL)
    assert cao_liquid.identity.reference_state.value.endmember.phase.value is Phase.L
    assert cao.value.point == Decimal("0.000228")
    # Printed Ca-027/Ca-028 nodes, 1800 K and 1900 K. 1823 K is 23/100
    # of that span: G_s = -435.91783, G_l = -402.11413, ΔG_fus = 33.80370
    # kJ/mol, a_l = 0.000228*exp(-33803.70/(8.31441*1823)).
    assert printed_formation_gibbs_kJ("Ca-027", Decimal("1800")) == Decimal("-440.375")
    assert printed_formation_gibbs_kJ("Ca-027", Decimal("1900")) == Decimal("-420.996")
    assert printed_formation_gibbs_kJ("Ca-028", Decimal("1800")) == Decimal("-406.017")
    assert printed_formation_gibbs_kJ("Ca-028", Decimal("1900")) == Decimal("-389.048")
    g_solid = Decimal("-440.375") + (
        Decimal("-420.996") - Decimal("-440.375")
    ) * Decimal(23) / Decimal(100)
    g_liquid = Decimal("-406.017") + (
        Decimal("-389.048") - Decimal("-406.017")
    ) * Decimal(23) / Decimal(100)
    delta_g = g_liquid - g_solid
    assert delta_g == Decimal("33.80370")
    hand = Decimal("0.000228") * (
        -delta_g * Decimal(1000) / (Decimal("8.31441") * Decimal("1823"))
    ).exp()
    assert cao_liquid.value.point == hand
    cao_notice = next(
        notice
        for notice in cao_liquid.notices
        if notice.reason == "single_polymorph_identity_d144"
    )
    assert cao_notice.kind is NoticeKind.DERIVATION_USES_COMPILATION
    assert cao_notice.authority is Authority.CERTIFIED
    assert cao_notice.certification
    assert "Ca-027" in cao_notice.band
    assert "lime" in cao_notice.band
    assert "T=1823" in cao_notice.band
    assert "T_fus=" in cao_notice.band

    mgo_temperature = mgo.identity.temperature_K.value
    mgo_liquid = fusion_comparison_reference(mgo, engine=Engine.OPENIMCC)
    assert mgo_liquid.identity.reference_state.value.endmember.phase.value is Phase.L
    assert mgo_liquid.value.point == hand_liquid_activity(
        "Mg-008", "Mg-009", mgo_temperature, mgo.value.point
    )
    mgo_notice = next(
        notice
        for notice in mgo_liquid.notices
        if notice.reason == "single_polymorph_identity_d144"
    )
    assert mgo_notice.authority is Authority.CERTIFIED
    assert "Mg-008" in mgo_notice.band
    assert "periclase" in mgo_notice.band
    assert f"T={mgo_temperature}" in mgo_notice.band

    # MAGEMin stays on its native crystal. CaO has no native activity, so
    # the printed solid is unchanged and the refusal stays
    # typed-refusal:magemin_no_native_activity:CaO. MgO periclase is that
    # basis: phase crystal, polymorph periclase, point the printed activity.
    from simulator.battery.generators.bench import (
        _ENGINE_REPORTED_ACTIVITY,
        _engine_reports_formula,
        _reference_matches,
    )

    magemin_cao = fusion_comparison_reference(cao, engine=Engine.MAGEMIN)
    assert (
        magemin_cao.identity.reference_state.value.endmember.phase.value is Phase.CR
    )
    assert magemin_cao.value.point == cao.value.point
    assert any(
        notice.reason == "single_polymorph_identity_d144"
        for notice in magemin_cao.notices
    )
    assert not any(
        notice.reason.startswith("MAGEMin native solid-reference conversion;")
        for notice in magemin_cao.notices
    )
    assert _engine_reports_formula("magemin", "CaO") == (
        False,
        "typed-refusal:magemin_no_native_activity:CaO",
    )

    magemin_mgo = fusion_comparison_reference(mgo, engine=Engine.MAGEMIN)
    magemin_standard = magemin_mgo.identity.reference_state.value
    assert magemin_standard.endmember.phase.value is Phase.CR
    assert magemin_standard.endmember.polymorph.value is Polymorph.PERICLASE
    assert magemin_mgo.value.point == mgo.value.point
    assert mgo.value.point == Decimal("0.103")
    magemin_mgo_notice = next(
        notice
        for notice in magemin_mgo.notices
        if notice.reason == "single_polymorph_identity_d144"
    )
    assert magemin_mgo_notice.authority is Authority.CERTIFIED
    assert "Mg-008" in magemin_mgo_notice.band
    assert "periclase" in magemin_mgo_notice.band
    assert f"T={mgo_temperature}" in magemin_mgo_notice.band
    assert _reference_matches(
        magemin_standard, _ENGINE_REPORTED_ACTIVITY["magemin"], "magemin"
    ) == (True, "raoultian_native_crystal_activity")

    benchmark = fusion_comparison_reference(cao, engine=None)
    assert benchmark.identity.reference_state.value.endmember.phase.value is Phase.L
    assert benchmark.value.point == hand

    for refused in (silica, alumina_half):
        comparison = fusion_comparison_reference(refused, engine=Engine.OPENIMCC)
        assert comparison.value.point == refused.value.point
        assert (
            comparison.identity.reference_state.value.endmember.phase.value
            is Phase.CR
        )
        assert any(
            notice.reason == "fusion_source_reference_phase_unprinted"
            for notice in comparison.notices
        )
        residual, _candidate = compile_residual(
            refused, Engine.INTERNAL_ANALYTICAL, context=context
        )
        assert residual.refusal is not None
        assert residual.refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
        assert residual.refusal.detail["reason"] == (
            "fusion_source_reference_phase_unprinted"
        )

    melting = janaf_fusion_energy("CaO", cao.identity.temperature_K.value)
    assert melting.melting_temperature_K == Decimal("3200.0")
    at_fusion = replace(
        cao,
        identity=replace(
            cao.identity,
            temperature_K=State.of(melting.melting_temperature_K),
        ),
    )
    unchanged = fusion_comparison_reference(
        at_fusion, engine=Engine.INTERNAL_ANALYTICAL
    )
    assert unchanged.value.point == cao.value.point
    assert unchanged.identity.reference_state.value.endmember.phase.value is Phase.CR
    assert any(
        notice.reason == "fusion_source_reference_phase_unprinted"
        for notice in unchanged.notices
    )
    assert not any(
        notice.reason == "single_polymorph_identity_d144"
        for notice in unchanged.notices
    )

    zero = row("kume_2000_table3_sample_226")
    assert zero.value.point == 0
    kept = fusion_comparison_reference(zero, engine=Engine.INTERNAL_ANALYTICAL)
    assert kept.value.point == 0
    assert kept.identity.reference_state.value.endmember.phase.value is Phase.CR
    assert not any(
        notice.reason == "single_polymorph_identity_d144" for notice in kept.notices
    )
