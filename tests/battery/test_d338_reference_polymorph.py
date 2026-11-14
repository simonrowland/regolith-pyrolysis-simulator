"""d-338: the scorer infers the stable polymorph of an unnamed pure solid oxide.

The printed record stays unknown. Expected phases and transition temperatures
come from the JANAF readers the scorer uses, not from a hard-coded 1743/1996 K.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import (
    Engine,
    EvidenceClass,
    Phase,
    PolymorphRejectionKind,
    Quantity,
)
from simulator.battery.generators.janaf import (
    _fusion_table_points,
    _interpolate_formation_gibbs,
    _janaf_fusion_energy_for_pair,
    _janaf_stable_crystal_set,
    janaf_fusion_energy,
    janaf_solid_stability_transitions,
)
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import State, phase_token
from simulator.battery.score import (
    FEO1_5_OUTSIDE_HEMATITE_FIELD_REASON,
    FLAGGED_STRATUM_REFERENCE_POLYMORPH_INFERRED,
    REFERENCE_POLYMORPH_INFERENCE_MARGIN_K,
    REFERENCE_POLYMORPH_INFERRED_REASON,
    REFERENCE_POLYMORPH_NEAR_TRANSITION_REASON,
    REFERENCE_POLYMORPH_QUENCH_OR_METASTABLE_REASON,
    REFERENCE_SOLID_INFERRED_REASON,
    _rt_ln_10_j_per_mol_K,
    flagged_strata,
    fusion_comparison_reference,
)
from tests.battery import factories as F
from tests.battery.factories import migrate_one_extract
from tests.battery.test_score import _with_activity_reference_polymorph

ROOT = Path(__file__).resolve().parents[2]
EXTRACTS = ROOT / "data" / "literature" / "extracts"


_SIO2_NOTICE_KEYS = {
    "comparison_phase",
    "comparison_table_id",
    "janaf_tridymite",
    "phase",
    "sizing_dex",
    "table_id",
}


def _omitted_solid(
    formula: str,
    temperature: Decimal,
    reason: str,
    point: str = "0.3",
    *,
    fO2_Pa: Decimal | None = None,
    omit_fO2: bool = False,
):
    experiment = F.kems_experiment()
    identity = _with_activity_reference_polymorph(
        F.activity_identity(
            formula=formula,
            T_K=temperature,
            endmember_phase=Phase.CR,
            component_basis=formula,
        ),
        None,
    )
    endmember = identity.reference_state.value.endmember
    identity = replace(
        identity,
        reference_state=State.of(
            replace(
                identity.reference_state.value,
                endmember=replace(endmember, polymorph=State.unknown(reason)),
            )
        ),
    )
    if omit_fO2:
        identity = replace(identity, fO2_Pa=State.unknown("fO2 not printed"))
    elif fO2_Pa is not None:
        identity = replace(identity, fO2_Pa=State.of(fO2_Pa))
    reference = F.observation(
        f"{formula}-omitted-{temperature}",
        experiment.experiment_id,
        identity,
        Decimal(point),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-d338",
    )
    return reference


def _inference(comparison):
    notice = next(
        item
        for item in comparison.notices
        if item.reason.startswith(f"{REFERENCE_POLYMORPH_INFERRED_REASON}:")
    )
    return notice, json.loads(notice.reason.split(":", 1)[1])


def _table_sizing(formula: str, temperature: Decimal) -> tuple[object, str]:
    stable = _janaf_stable_crystal_set(formula, temperature)
    if not stable.alternatives:
        return stable, "not sized"
    nearest = min(
        stable.alternatives,
        key=lambda item: abs(item.gibbs_kJ_per_mol - stable.selected.gibbs_kJ_per_mol),
    )
    selected_points, _digest = _fusion_table_points(stable.selected.table_id)
    nearest_points, _digest = _fusion_table_points(nearest.table_id)
    gap = abs(
        _interpolate_formation_gibbs(selected_points, temperature)
        - _interpolate_formation_gibbs(nearest_points, temperature)
    )
    return stable, str(gap * Decimal(1000) / _rt_ln_10_j_per_mol_K(temperature))


def _silica_identity_change():
    transitions = [
        item
        for item in janaf_solid_stability_transitions("SiO2")
        if item.stable_below.polymorph != item.stable_above.polymorph
    ]
    assert len(transitions) == 1
    return transitions[0]


def test_silica_transition_is_read_from_the_janaf_crystal_tables() -> None:
    change = _silica_identity_change()
    assert change.stable_below.polymorph.value == "quartz"
    assert change.stable_below.table_id == "O-037"
    assert change.stable_above.polymorph.value == "cristobalite_high"
    assert change.stable_above.table_id == "O-035"
    # 1933 K and 1973 K are outside the 25 K solid-solid window.
    for temperature in (Decimal("1933"), Decimal("1973")):
        assert abs(temperature - change.temperature_K) > (
            REFERENCE_POLYMORPH_INFERENCE_MARGIN_K
        )


def test_inferred_phase_tracks_the_table_on_both_sides_of_the_transition() -> None:
    """A hard-coded polymorph fails one of these two temperatures.

    The low side is quartz and the high side is cristobalite in the JANAF
    tables. Removing the table read and writing one phase makes this red.
    """

    change = _silica_identity_change()
    for offset in (Decimal(-30), Decimal(30)):
        temperature = change.temperature_K + offset
        stable, sizing = _table_sizing("SiO2", temperature)
        reference = _omitted_solid("SiO2", temperature, "polymorph not named")
        comparison = fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
        _notice, detail = _inference(comparison)
        nearest = min(
            stable.alternatives,
            key=lambda item: abs(
                item.gibbs_kJ_per_mol - stable.selected.gibbs_kJ_per_mol
            ),
        )
        assert set(detail) == _SIO2_NOTICE_KEYS
        assert detail["phase"] == stable.selected.polymorph.value
        assert detail["table_id"] == stable.selected.table_id
        assert detail["comparison_phase"] == nearest.polymorph.value
        assert detail["comparison_table_id"] == nearest.table_id
        assert detail["janaf_tridymite"] == "absent"
        assert detail["sizing_dex"] == sizing
        assert comparison.identity.reference_state.value.endmember.phase.value is Phase.L
        assert reference.identity.reference_state.value.endmember.polymorph.is_unknown


def test_within_25k_of_a_table_transition_refuses_typed() -> None:
    """Deleting the 25 K comparison makes the inner temperatures infer and this fails."""

    change = _silica_identity_change()
    for offset in (
        -REFERENCE_POLYMORPH_INFERENCE_MARGIN_K,
        Decimal(0),
        REFERENCE_POLYMORPH_INFERENCE_MARGIN_K,
    ):
        temperature = change.temperature_K + offset
        reference = _omitted_solid("SiO2", temperature, "polymorph not named")
        comparison = fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
        assert comparison.value.point == reference.value.point
        assert comparison.identity.reference_state.value.endmember.phase.value is Phase.CR
        assert any(
            notice.reason == REFERENCE_POLYMORPH_NEAR_TRANSITION_REASON
            for notice in comparison.notices
        )
        assert not any(
            notice.reason.startswith("reference_converted_via_fusion;")
            for notice in comparison.notices
        )


def test_quench_or_metastable_reference_statement_refuses_typed() -> None:
    temperature = Decimal("1933")
    for reason in (
        "source states a metastable phase for this reference",
        "the reference solid was quenched from a higher temperature",
    ):
        reference = _omitted_solid("SiO2", temperature, reason)
        comparison = fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
        assert comparison.value.point == reference.value.point
        assert any(
            notice.reason == REFERENCE_POLYMORPH_QUENCH_OR_METASTABLE_REASON
            for notice in comparison.notices
        )

    negated = _omitted_solid(
        "SiO2",
        temperature,
        "no quench observation; the paper does not name the polymorph",
    )
    comparison = fusion_comparison_reference(negated, engine=Engine.OPENIMCC)
    _notice, detail = _inference(comparison)
    stable, _sizing = _table_sizing("SiO2", temperature)
    assert detail["phase"] == stable.selected.polymorph.value


def test_unnamed_solid_above_its_melting_crossing_refuses_without_relabeling() -> None:
    temperature = Decimal("1973")
    fusion = janaf_fusion_energy("SiO2", temperature)
    # 1973 K is inside 25 K of the JANAF melting crossing and still scores:
    # melting is not part of the solid-solid guard.
    assert abs(temperature - fusion.melting_temperature_K) <= (
        REFERENCE_POLYMORPH_INFERENCE_MARGIN_K
    )
    below = _omitted_solid("SiO2", temperature, "polymorph not named")
    scored = fusion_comparison_reference(below, engine=Engine.OPENIMCC)
    _notice, detail = _inference(scored)
    assert detail["phase"] == "cristobalite_high"
    assert scored.identity.reference_state.value.endmember.phase.value is Phase.L

    above_temperature = fusion.melting_temperature_K + Decimal(1)
    above = _omitted_solid("SiO2", above_temperature, "polymorph not named")
    refused = fusion_comparison_reference(above, engine=Engine.OPENIMCC)
    assert refused.value.point == above.value.point
    assert refused.identity.reference_state.value.endmember.phase.value is Phase.CR
    assert any(
        notice.reason == "printed_solid_reference_above_melting"
        for notice in refused.notices
    )


def test_stated_polymorph_is_not_replaced_by_the_stable_crystal() -> None:
    reference = _omitted_solid("SiO2", Decimal("1600"), "unused")
    quartz = _with_activity_reference_polymorph(reference.identity, "quartz")
    reference = replace(reference, identity=quartz)
    comparison = fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    assert any(
        "tables=O-037/O-038" in notice.reason
        for notice in comparison.notices
    )
    assert not any(
        notice.reason.startswith(f"{REFERENCE_POLYMORPH_INFERRED_REASON}:")
        for notice in comparison.notices
    )


def _migrated_omitted_activities(filename: str, formula: str, temperature: Decimal):
    migrator = migrate_one_extract(
        ROOT, EXTRACTS / filename, index={}, aliases={}
    )
    rows = []
    for observation in migrator.result.observations.values():
        identity = observation.identity
        if not isinstance(identity, Identity):
            continue
        if quantity_token(identity) is not Quantity.ACTIVITY:
            continue
        stated = identity.temperature_K
        if stated is None or not stated.is_value or stated.value != temperature:
            continue
        state = identity.reference_state
        if state is None or not state.is_value:
            continue
        endmember = state.value.endmember
        if endmember.formula != formula or phase_token(endmember) is not Phase.CR:
            continue
        polymorph = endmember.polymorph
        if (
            polymorph is None
            or not polymorph.is_unknown
            or polymorph.rejection_kind is not PolymorphRejectionKind.OMITTED
        ):
            continue
        rows.append(observation)
    return rows


def test_stolyarova_and_kambayashi_unnamed_silica_score_with_the_flag() -> None:
    stolyarova_text = (
        EXTRACTS / "stolyarova-1996-cao-alumina-silica-kems.yaml"
    ).read_text(encoding="utf-8")
    assert "inferred: true" in stolyarova_text
    assert "secondary evidence" in stolyarova_text
    kambayashi_text = (
        EXTRACTS / "kambayashi-kato-1984-mgo-sio2-1973k.yaml"
    ).read_text(encoding="utf-8")
    assert "does not name its polymorph" in kambayashi_text

    cases = (
        (
            "stolyarova-1996-cao-alumina-silica-kems.yaml",
            Decimal("1933"),
            30,
            True,
        ),
        (
            "kambayashi-kato-1984-mgo-sio2-1973k.yaml",
            Decimal("1973"),
            16,
            False,
        ),
    )
    for filename, temperature, count, solid_inferred in cases:
        rows = _migrated_omitted_activities(filename, "SiO2", temperature)
        assert len(rows) == count
        stable, sizing = _table_sizing("SiO2", temperature)
        fusion = _janaf_fusion_energy_for_pair(
            "SiO2",
            stable.selected.table_id,
            janaf_fusion_energy("SiO2", temperature).liquid_table,
            temperature,
        )
        assert temperature < fusion.melting_temperature_K
        for observation in rows:
            assert observation.identity.reference_state.value.endmember.polymorph.is_unknown
            comparison = fusion_comparison_reference(
                observation, engine=Engine.OPENIMCC
            )
            notice, detail = _inference(comparison)
            assert notice.kind.value == "derivation_uses_compilation"
            assert set(detail) == _SIO2_NOTICE_KEYS
            assert detail["janaf_tridymite"] == "absent"
            assert detail["phase"] == stable.selected.polymorph.value
            assert detail["table_id"] == stable.selected.table_id
            assert detail["sizing_dex"] == sizing
            assert comparison.identity.reference_state.value.endmember.phase.value is (
                Phase.L
            )
            assert comparison.value.point != observation.value.point
            assert any(
                item.reason.startswith("reference_converted_via_fusion;")
                for item in comparison.notices
            )
            assert (
                any(
                    item.reason == REFERENCE_SOLID_INFERRED_REASON
                    for item in comparison.notices
                )
                is solid_inferred
            )
            assert observation.identity.reference_state.value.endmember.polymorph.is_unknown


def test_sizing_at_1873_names_the_janaf_partner_with_literal_dex() -> None:
    """Dex values are the printed JANAF gap, not a second copy of the formula.

    SiO2 at 1873 K is cristobalite_high O-035 versus quartz O-037.
    Al2O3 at 1873 K is corundum Al-096 versus delta Al-097.
    JANAF has no tridymite table, and the silica notice says so.
    """

    silica = fusion_comparison_reference(
        _omitted_solid("SiO2", Decimal("1873"), "polymorph not named"),
        engine=Engine.OPENIMCC,
    )
    _notice, detail = _inference(silica)
    assert detail["phase"] == "cristobalite_high"
    assert detail["table_id"] == "O-035"
    assert detail["comparison_phase"] == "quartz"
    assert detail["comparison_table_id"] == "O-037"
    assert detail["janaf_tridymite"] == "absent"
    assert detail["sizing_dex"] == "0.03963421821224556086346039045"

    alumina = fusion_comparison_reference(
        _omitted_solid("Al2O3", Decimal("1873"), "polymorph not named"),
        engine=Engine.OPENIMCC,
    )
    _notice, detail = _inference(alumina)
    assert detail["phase"] == "corundum"
    assert detail["table_id"] == "Al-096"
    assert detail["comparison_phase"] == "delta"
    assert detail["comparison_table_id"] == "Al-097"
    assert "janaf_tridymite" not in detail
    assert detail["sizing_dex"] == "0.1036086765831357903632982043"


def test_alo1_5_squares_onto_corundum_before_the_fusion_shift() -> None:
    """Removing the square leaves the printed 0.0253 as the solid activity."""

    printed = Decimal("0.0253")
    reference = _omitted_solid(
        "AlO1.5", Decimal("1873"), "polymorph not named", point="0.0253"
    )
    comparison = fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    _notice, detail = _inference(comparison)
    assert detail["printed_formula"] == "AlO1.5"
    assert detail["dimer_formula"] == "Al2O3"
    assert detail["phase"] == "corundum"
    assert detail["table_id"] == "Al-096"
    assert Decimal(detail["dimer_solid_activity"]) == Decimal("0.00064009")
    assert comparison.value.point == Decimal(
        "0.0001883871712739844995859130963"
    )
    assert reference.value.point == printed
    assert any(
        "source_activity_solid=0.00064009" in notice.reason
        for notice in comparison.notices
    )


def test_cro1_5_squares_onto_eskolaite() -> None:
    reference = _omitted_solid(
        "CrO1.5", Decimal("1873"), "polymorph not named", point="0.2"
    )
    comparison = fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    _notice, detail = _inference(comparison)
    assert detail["phase"] == "eskolaite"
    assert detail["table_id"] == "Cr-014"
    assert detail["dimer_formula"] == "Cr2O3"
    assert Decimal(detail["dimer_solid_activity"]) == Decimal("0.04")
    assert detail["sizing_dex"] == "not sized"
    assert detail["comparison_phase"] is None
    assert comparison.identity.reference_state.value.endmember.phase.value is Phase.L
    assert comparison.value.point == Decimal(
        "0.004340183395386964107579969876"
    )
    assert any(
        "source_activity_solid=0.04" in notice.reason
        for notice in comparison.notices
    )
    assert reference.value.point == Decimal("0.2")


def test_feo1_5_outside_the_hematite_field_refuses_typed() -> None:
    """Deleting the fO2 comparison lets a reducing row proceed to Fe2O3."""

    # Hayashi prints 10^-9 atm = 0.000101325 Pa. That is below the
    # hematite-magnetite equilibrium at 1573 K.
    printed = Decimal("0.000101325")
    reference = _omitted_solid(
        "FeO1.5",
        Decimal("1573"),
        "polymorph not named",
        point="0.2",
        fO2_Pa=printed,
    )
    comparison = fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    assert comparison.value.point == reference.value.point
    assert comparison.identity.reference_state.value.endmember.phase.value is Phase.CR
    notice = next(
        item
        for item in comparison.notices
        if item.reason == FEO1_5_OUTSIDE_HEMATITE_FIELD_REASON
    )
    assert "Fe-030/Fe-032" in notice.band
    assert "6 Fe2O3 = 4 Fe3O4 + O2" in notice.band
    assert f"printed fO2={printed} Pa" in notice.band
    assert not any(
        item.reason.startswith(f"{REFERENCE_POLYMORPH_INFERRED_REASON}:")
        for item in comparison.notices
    )

    absent = _omitted_solid(
        "FeO1.5",
        Decimal("1573"),
        "polymorph not named",
        omit_fO2=True,
    )
    refused = fusion_comparison_reference(absent, engine=Engine.OPENIMCC)
    assert refused.value.point == absent.value.point
    assert any(
        item.reason == FEO1_5_OUTSIDE_HEMATITE_FIELD_REASON
        and "printed fO2=absent" in item.band
        for item in refused.notices
    )


def test_feo1_5_inside_the_hematite_field_still_needs_a_fusion_pair() -> None:
    """1 bar is above the 1573 K equilibrium. Fe2O3 has no JANAF liquid table."""

    reference = _omitted_solid(
        "FeO1.5",
        Decimal("1573"),
        "polymorph not named",
        fO2_Pa=Decimal("100000"),
    )
    comparison = fusion_comparison_reference(reference, engine=Engine.OPENIMCC)
    assert comparison.value.point == reference.value.point
    assert not any(
        item.reason == FEO1_5_OUTSIDE_HEMATITE_FIELD_REASON
        for item in comparison.notices
    )
    assert any(
        "no JANAF fusion table pair for Fe2O3" in item.reason
        for item in comparison.notices
    )


def test_inferred_polymorph_is_a_flagged_stratum_for_magemin() -> None:
    """Dropping the stratum registration leaves this MAGEMin comparison unflagged."""

    silica = fusion_comparison_reference(
        _omitted_solid("SiO2", Decimal("1873"), "polymorph not named"),
        engine=Engine.MAGEMIN,
    )
    assert FLAGGED_STRATUM_REFERENCE_POLYMORPH_INFERRED in flagged_strata(
        silica.notices
    )
    assert silica.value.point == Decimal("0.2738337894904822878984987920")

    lime = fusion_comparison_reference(
        _omitted_solid("CaO", Decimal("1873"), "polymorph not named"),
        engine=Engine.MAGEMIN,
    )
    assert FLAGGED_STRATUM_REFERENCE_POLYMORPH_INFERRED in flagged_strata(
        lime.notices
    )
    assert lime.value.point == Decimal("0.3")
    assert lime.identity.reference_state.value.endmember.phase.value is Phase.CR
