"""MnO solid-to-liquid reference when JANAF has no MnO table.

Printed Barin checks use the page figures, not a copy of the reconstruction.
The activity ratio check uses the owner's ΔG only as a consistency check of
the one scorer formula. Chen residuals are printed; magnitudes are not pinned.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AdmissionStatus,
    Authority,
    Engine,
    ExecutionState,
    Phase,
    Quantity,
    QUANTITY_UNITS,
)
from simulator.battery.generators.janaf import JANAF_R_J_PER_MOL_K
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.migrate import Migrator
from simulator.battery.records import Execution, phase_token
from simulator.battery.score import (
    FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION,
    ENGINE_CHANNELS,
    EnginePrediction,
    ScoreContext,
    compile_residual,
    flagged_strata,
    fusion_comparison_reference,
)
from simulator.reference_data.solid_liquid_fusion import (
    barin_mno_gibbs_kJ_per_mol,
    located_fusion_when_janaf_does_not_cover,
)
from simulator.reference_data import solid_liquid_fusion as fusion_owner
from tests.battery.test_d338_reference_polymorph import _omitted_solid
from tests.battery.test_score import (
    _with_activity_reference_polymorph,
    _without_activity_reference_polymorph,
)
from tests.battery import factories as F


ROOT = Path(__file__).resolve().parents[1]
CHEN_EXTRACT = ROOT / "data/literature/extracts/chen-2022-sio2-mno-slag-activities.yaml"
CHEN_SOURCE = "chen-2022-sio2-mno-slag-activities"
_MELT_ENGINES = (
    Engine.INTERNAL_ANALYTICAL,
    Engine.ALPHAMELTS,
    Engine.THERMOENGINE,
    Engine.OPENIMCC,
    Engine.MAGEMIN,
)
_CONVERTING_ENGINES = (
    None,
    Engine.INTERNAL_ANALYTICAL,
    Engine.ALPHAMELTS,
    Engine.THERMOENGINE,
    Engine.OPENIMCC,
)


def _fusion_notice(converted):
    return next(
        notice
        for notice in converted.notices
        if notice.reason.startswith(f"{FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION};")
    )


def test_barin_constant_cp_reproduces_printed_gibbs() -> None:
    # Printed G on Barin 1995 p. 1046, not the H/S reconstruction.
    solid_1700 = barin_mno_gibbs_kJ_per_mol("solid", Decimal("1700"))
    liquid_2200 = barin_mno_gibbs_kJ_per_mol("liquid", Decimal("2200"))
    solid_tm = barin_mno_gibbs_kJ_per_mol("solid", Decimal("2115"))
    liquid_tm = barin_mno_gibbs_kJ_per_mol("liquid", Decimal("2115"))
    assert solid_1700 is not None and liquid_2200 is not None
    assert solid_tm is not None and liquid_tm is not None
    assert abs(solid_1700 - Decimal("-564.481")) < Decimal("0.001")
    assert abs(liquid_2200 - Decimal("-645.121")) < Decimal("0.001")
    assert abs(solid_tm - Decimal("-629.423")) < Decimal("0.001")
    assert abs(liquid_tm - Decimal("-629.423")) < Decimal("0.001")
    assert abs(liquid_tm - solid_tm) < Decimal("0.001")
    liquid_1700 = barin_mno_gibbs_kJ_per_mol("liquid", Decimal("1700"))
    assert liquid_1700 is not None and liquid_1700 > solid_1700
    assert barin_mno_gibbs_kJ_per_mol("solid", Decimal("1500")) is None
    assert barin_mno_gibbs_kJ_per_mol("liquid", Decimal("2600")) is None


def test_janaf_wins_when_it_covers_the_phase_and_temperature(monkeypatch) -> None:
    def consulted(oxide, temperature_K):
        raise AssertionError(f"located source consulted for {oxide} at {temperature_K}")

    monkeypatch.setattr(fusion_owner, "_first_located_solid_liquid", consulted)
    assert located_fusion_when_janaf_does_not_cover("CaO", Decimal("2000")) is None

    composition = F.activity_identity(
        formula="CaO",
        T_K=Decimal("2000"),
        endmember_phase=Phase.CR,
        component_basis="CaO",
    )
    identity = _with_activity_reference_polymorph(composition, "lime")
    reference = F.observation(
        "cao-janaf-still-first",
        F.kems_experiment().experiment_id,
        identity,
        Decimal("1"),
    )
    converted = fusion_comparison_reference(reference, engine=Engine.INTERNAL_ANALYTICAL)
    notice = _fusion_notice(converted)
    assert "tables=Ca-027/Ca-028" in notice.reason
    assert "Barin" not in notice.reason
    assert notice.authority is None


def test_omitted_cao_still_falls_through_to_janaf() -> None:
    reference = _omitted_solid("CaO", Decimal("2000"), "polymorph_not_published", point="1")
    converted = fusion_comparison_reference(
        reference, engine=Engine.INTERNAL_ANALYTICAL
    )
    text = " ".join(notice.reason for notice in converted.notices)
    assert "Barin" not in text
    assert "tables=Ca-027/Ca-028" in text


def test_ivtan_then_barin_then_robie(monkeypatch) -> None:
    calls: list[str] = []

    def ivtan(oxide, temperature_K):
        calls.append("ivtan")
        return None

    def barin(oxide, temperature_K):
        calls.append("barin")
        return None

    def robie(oxide, temperature_K):
        calls.append("robie")
        return None

    monkeypatch.setattr(fusion_owner, "_ivtan_solid_liquid", ivtan)
    monkeypatch.setattr(fusion_owner, "_barin_mno_solid_liquid", barin)
    monkeypatch.setattr(fusion_owner, "_robie_hemingway_solid_liquid", robie)
    assert located_fusion_when_janaf_does_not_cover("MnO", Decimal("1673.15")) is None
    assert calls == ["ivtan", "barin", "robie"]


def test_ivtan_gas_and_robie_crystals_do_not_supply_mno() -> None:
    assert fusion_owner._ivtan_solid_liquid("MnO", Decimal("1673.15")) is None
    assert fusion_owner._robie_hemingway_solid_liquid("MnO", Decimal("1673.15")) is None
    assert fusion_owner._robie_hemingway_solid_liquid("CaO", Decimal("1673.15")) is None
    energy = located_fusion_when_janaf_does_not_cover("MnO", Decimal("1673.15"))
    assert energy is not None
    assert energy.source_name == "Barin 1995"
    assert energy.authority == "extrapolated"
    assert located_fusion_when_janaf_does_not_cover("MnO", Decimal("1500")) is None
    assert located_fusion_when_janaf_does_not_cover("MnO", Decimal("2600")) is None
    assert located_fusion_when_janaf_does_not_cover("CoO", Decimal("1673.15")) is None


@pytest.mark.parametrize("temperature", (Decimal("1673.15"), Decimal("1773.15")))
@pytest.mark.parametrize("engine", _CONVERTING_ENGINES)
def test_omitted_mno_converts_with_a_barin_notice(temperature, engine) -> None:
    reference = _omitted_solid(
        "MnO", temperature, "polymorph_not_published", point="0.001569"
    )
    converted = fusion_comparison_reference(reference, engine=engine)
    notice = _fusion_notice(converted)
    assert notice.authority is Authority.EXTRAPOLATED
    assert "source=Barin 1995" in notice.reason
    assert "authority=extrapolated" in notice.reason
    assert "1600-2115" in notice.reason
    assert "2115-2500" in notice.reason
    assert notice.band is not None and "1600-2115" in notice.band
    assert "fusion conversion missing input" not in notice.reason
    assert phase_token(converted.identity.reference_state.value.endmember) is Phase.L
    assert converted.value.point < reference.value.point
    assert FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION in flagged_strata(
        converted.notices
    )
    energy = located_fusion_when_janaf_does_not_cover("MnO", temperature)
    assert energy is not None and energy.delta_g_fus_kJ_per_mol > 0
    ratio = (
        -energy.delta_g_fus_kJ_per_mol
        * Decimal(1000)
        / (JANAF_R_J_PER_MOL_K * temperature)
    ).exp()
    assert abs(converted.value.point / reference.value.point - ratio) < Decimal("1e-24")


def test_polymorph_never_set_still_uses_the_located_owner() -> None:
    identity = _without_activity_reference_polymorph(
        F.activity_identity(
            formula="MnO",
            T_K=Decimal("1673.15"),
            endmember_phase=Phase.CR,
            component_basis="MnO",
        )
    )
    reference = F.observation(
        "mno-polymorph-absent",
        F.kems_experiment().experiment_id,
        identity,
        Decimal("0.2"),
    )
    converted = fusion_comparison_reference(
        reference, engine=Engine.INTERNAL_ANALYTICAL
    )
    notice = _fusion_notice(converted)
    assert "Barin 1995" in notice.reason
    assert converted.value.point < reference.value.point


def test_magemin_mno_stays_a_missing_input_refusal() -> None:
    reference = _omitted_solid(
        "MnO", Decimal("1673.15"), "polymorph_not_published", point="0.001569"
    )
    converted = fusion_comparison_reference(reference, engine=Engine.MAGEMIN)
    assert converted.value.point == reference.value.point
    assert any(
        notice.reason.startswith("fusion conversion missing input for MnO")
        for notice in converted.notices
    )
    assert not any(
        notice.reason.startswith(f"{FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION};")
        for notice in converted.notices
    )


def test_mutation_without_the_located_source_refuses_mno(monkeypatch) -> None:
    monkeypatch.setattr(
        fusion_owner, "_first_located_solid_liquid", lambda oxide, temperature_K: None
    )
    reference = _omitted_solid(
        "MnO", Decimal("1673.15"), "polymorph_not_published", point="0.001569"
    )
    converted = fusion_comparison_reference(
        reference, engine=Engine.INTERNAL_ANALYTICAL
    )
    assert converted.value.point == reference.value.point
    assert any(
        notice.reason.startswith("fusion conversion missing input for MnO")
        for notice in converted.notices
    )


def _chen_rows():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(CHEN_EXTRACT)
    migrator.finalize()
    result = migrator.result
    rows = tuple(
        result.observations[f"{CHEN_SOURCE}::chen22_table2_mno_activity_row_{n:02d}"]
        for n in range(1, 38)
    )
    context = ScoreContext(
        works=result.works,
        experiments=result.experiments,
        observations=result.observations,
        benches=result.benches,
        root=ROOT,
    )
    return context, rows


def test_chen_mno_rows_leave_the_janaf_fusion_refusal() -> None:
    context, rows = _chen_rows()
    assert len(rows) == 37
    assert all(
        isinstance(row.identity, Identity)
        and quantity_token(row.identity) is Quantity.ACTIVITY
        and row.identity.species.formula == "MnO"
        and row.admission.status is AdmissionStatus.ADMITTED
        for row in rows
    )
    counts: dict[str, Counter] = {}
    for engine in _MELT_ENGINES:
        counts[engine.value] = Counter()
        for row in rows:
            residual, _candidate = compile_residual(
                row,
                engine,
                context=context,
                prediction=EnginePrediction(
                    engine=engine,
                    channel=ENGINE_CHANNELS[engine],
                    execution=Execution(
                        state=ExecutionState.PRODUCED,
                        call_evidence="chen-mno-stub",
                    ),
                    value=row.value.point,
                    unit=QUANTITY_UNITS[Quantity.ACTIVITY],
                    authority=Authority.CERTIFIED,
                    lineage_complete=True,
                    identity=row.identity,
                    coefficient_sources=("chen-mno-stub",),
                ),
            )
            refusal = residual.refusal
            reason = "scored" if refusal is None else str(
                refusal.detail.get("reason") or refusal.reason
            )
            counts[engine.value][reason] += 1
            barin = any(
                notice.reason.startswith(
                    f"{FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION};"
                )
                and "Barin 1995" in notice.reason
                for notice in residual.notices
            )
            missing_input = any(
                notice.reason.startswith("fusion conversion missing input for MnO")
                for notice in residual.notices
            )
            if engine is Engine.MAGEMIN:
                assert not barin
                assert missing_input or reason == (
                    "solid_liquid_reference_conversion_missing_input"
                )
            else:
                assert reason != "solid_liquid_reference_conversion_missing_input"
                assert barin
                assert not missing_input
                if refusal is None and residual.numeric is not None:
                    print(
                        f"STUB {engine.value} {row.observation_id} "
                        f"residual={residual.numeric.value} {residual.numeric.unit}"
                    )
    for engine_name, counter in counts.items():
        rendered = ", ".join(f"{reason}={n}" for reason, n in sorted(counter.items()))
        print(f"CHEN MnO {engine_name}: {rendered}")
