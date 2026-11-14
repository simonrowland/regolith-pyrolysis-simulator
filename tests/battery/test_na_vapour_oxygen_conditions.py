"""Source-condition selection for the Rego and Ryś sodium vapour rows."""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AmountBasis, NoticeKind, Phase, Quantity
from simulator.battery.fo2_ladder import fo2_tier_notice, resolve_row_fo2
from simulator.battery.identity import Identity
from simulator.battery.records import Composition, Notice, Species, State, Value
from simulator.physical_constants import PA_PER_BAR
from tests.battery import factories as F

ROOT = Path(__file__).resolve().parents[2]
REGO_SOURCE = "rego-1985-na2o-sio2-transpiration"
RYS_SOURCE = "rys-2007-alkali-oxide-thermodynamics"


def _na_partial_row(
    *,
    source_id: str,
    observation_id: str,
    experiment_id: str,
    temperature_K: str,
    pressure_Pa: str,
    total_pressure_Pa: str | None = None,
    composition: Composition | None = None,
):
    identity = Identity(
        quantity=State.of(Quantity.P_PARTIAL),
        species=Species("Na", Phase.G),
        temperature_K=State.of(Decimal(temperature_K)),
        composition=None if composition is None else State.of(composition),
        fO2_Pa=State.unknown("no printed oxygen value on this row"),
        total_pressure_Pa=(
            None
            if total_pressure_Pa is None
            else State.of(Decimal(total_pressure_Pa))
        ),
    )
    return F.observation(
        observation_id,
        experiment_id,
        identity,
        Decimal(pressure_Pa),
        source_id=source_id,
    )


def test_rego_table_i_row_selects_flagged_two_species_c_co_condition() -> None:
    # Table I run 1: 1300 C, P=1.015 atm, pNa=0.00224 atm. The migrated
    # source values are 102844.875 Pa total and 226.968 Pa sodium.
    experiment = F.tabulation_experiment(
        "rego-run-01",
        T_K=Decimal("1573.15"),
        total_P=Decimal("102844.875"),
    )
    row = _na_partial_row(
        source_id=REGO_SOURCE,
        observation_id="rego-run-01-pNa",
        experiment_id=experiment.experiment_id,
        temperature_K="1573.15",
        pressure_Pa="226.968",
        total_pressure_Pa="102844.875",
    )

    selected = resolve_row_fo2(
        experiment,
        None,
        row,
        quantity=Quantity.P_PARTIAL,
    )

    assert selected is not None
    assert selected.route == "rego_c_co_two_species_approximation"
    assert selected.tier == 3
    assert selected.log10_fO2_bar == pytest.approx(-16.6550589855, abs=1e-10)

    # Mutation proof: the C/CO condition follows the row's printed Table I
    # total pressure. Increasing it raises pCO and therefore raises fO2.
    changed_identity = replace(
        row.identity,
        total_pressure_Pa=State.of(Decimal("103844.875")),
    )
    changed = resolve_row_fo2(
        experiment,
        None,
        replace(row, identity=changed_identity),
        quantity=Quantity.P_PARTIAL,
    )
    assert changed is not None
    assert changed.log10_fO2_bar > selected.log10_fO2_bar


def test_rys_na_row_without_oxygen_selects_effusion_closure() -> None:
    # Ryś Table 10a, NaKAlSi_1 row 8 (1742.385 K, 0.01475 Pa Na).
    composition = Composition(
        "oxides",
        (
            ("Na2O", Decimal("0.0380")),
            ("Al2O3", Decimal("0.0647")),
            ("SiO2", Decimal("0.8659")),
            ("K2O", Decimal("0.0314")),
        ),
        AmountBasis.MOLE_FRACTION,
    )
    experiment = F.kems_experiment("rys-na-row-8")
    row = _na_partial_row(
        source_id=RYS_SOURCE,
        observation_id="rys-na-row-8-pNa",
        experiment_id=experiment.experiment_id,
        temperature_K="1742.385",
        pressure_Pa="0.01475",
        composition=composition,
    )

    selected = resolve_row_fo2(
        experiment,
        None,
        row,
        quantity=Quantity.P_PARTIAL,
    )

    assert selected is not None
    assert selected.route == (
        "effusion_oxygen_balance; rys_pO2_provenance_unknown"
    )
    assert selected.tier == 3
    assert selected.physics_rung == 3
    assert selected.closure is True
    assert selected.po2_bar is None

    # A prediction must not depend on the observed target that it predicts.
    changed = resolve_row_fo2(
        experiment,
        None,
        replace(row, value=Value.point_of(row.value.point * 2)),
        quantity=Quantity.P_PARTIAL,
    )
    assert changed is not None
    assert changed.route == selected.route
    assert changed.closure is True
    assert changed.po2_bar is None


@pytest.mark.parametrize("status", ("calculated_from_pNa", "unknown"))
def test_rys_na_without_independent_oxygen_closes_independently_of_target(
    status: str,
) -> None:
    composition = Composition(
        "oxides",
        (
            ("Na2O", Decimal("0.0380")),
            ("Al2O3", Decimal("0.0647")),
            ("SiO2", Decimal("0.8659")),
            ("K2O", Decimal("0.0314")),
        ),
        AmountBasis.MOLE_FRACTION,
    )
    experiment = F.kems_experiment(f"rys-{status}")
    row = _na_partial_row(
        source_id=RYS_SOURCE,
        observation_id=f"rys-{status}-pNa",
        experiment_id=experiment.experiment_id,
        temperature_K="1742.385",
        pressure_Pa="0.01475",
        composition=composition,
    )
    if status == "calculated_from_pNa":
        row = replace(
            row,
            identity=replace(
                row.identity, fO2_Pa=State.of(Decimal("0.00002"))
            ),
            notices=(
                Notice(
                    kind=NoticeKind.PRESSURE_PROVENANCE_UNKNOWN,
                    affected_quantities=(Quantity.P_PARTIAL,),
                    reason=(
                        "fO2_Pa is a DERIVED condition from observed pNa via "
                        "source congruent vaporization"
                    ),
                    origin="rys_extract",
                ),
            ),
        )

    selected = resolve_row_fo2(
        experiment,
        None,
        row,
        quantity=Quantity.P_PARTIAL,
    )
    changed = resolve_row_fo2(
        experiment,
        None,
        replace(row, value=Value.point_of(row.value.point * 1.7)),
        quantity=Quantity.P_PARTIAL,
    )

    assert selected is not None and changed is not None
    assert selected.closure is True
    assert selected.po2_bar is None
    assert changed.closure is True
    assert changed.po2_bar is None
    assert changed.route == selected.route
    tier_notice = fo2_tier_notice(Quantity.P_PARTIAL, selected)
    if status == "calculated_from_pNa":
        assert selected.route.endswith("rys_pO2_calculated_from_pNa_not_independent")
        assert tier_notice.kind is NoticeKind.INPUT_OMITTED
        assert "source-calculated pO2 from observed pNa" in tier_notice.reason
    else:
        assert selected.route.endswith("rys_pO2_provenance_unknown")
        assert tier_notice.kind is NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
        assert "row-level pO2 provenance unknown" in tier_notice.reason


def test_rys_measured_and_source_linked_oxygen_survive_target_mutation() -> None:
    experiment = F.kems_experiment("rys-linked-oxygen")
    row = _na_partial_row(
        source_id=RYS_SOURCE,
        observation_id="rys-linked-oxygen-pNa",
        experiment_id=experiment.experiment_id,
        temperature_K="1742.385",
        pressure_Pa="0.01475",
    )
    measured = replace(
        row,
        identity=replace(row.identity, fO2_Pa=State.of(Decimal("0.00002"))),
    )
    changed_measured = replace(
        measured, value=Value.point_of(measured.value.point * 1.7)
    )
    selected_measured = resolve_row_fo2(
        experiment, None, measured, quantity=Quantity.P_PARTIAL
    )
    changed_selected_measured = resolve_row_fo2(
        experiment, None, changed_measured, quantity=Quantity.P_PARTIAL
    )
    assert selected_measured is not None and changed_selected_measured is not None
    assert selected_measured.po2_bar == changed_selected_measured.po2_bar
    assert selected_measured.log10_fO2_bar == changed_selected_measured.log10_fO2_bar

    selected_linked = resolve_row_fo2(
        experiment,
        None,
        row,
        quantity=Quantity.P_PARTIAL,
        source_oxygen_bar=2.0e-8,
    )
    changed_selected_linked = resolve_row_fo2(
        experiment,
        None,
        replace(row, value=Value.point_of(row.value.point * 1.7)),
        quantity=Quantity.P_PARTIAL,
        source_oxygen_bar=2.0e-8,
    )
    assert selected_linked is not None and changed_selected_linked is not None
    assert selected_linked.route == "source_linked_oxygen_condition"
    assert selected_linked.po2_bar == changed_selected_linked.po2_bar
    assert selected_linked.log10_fO2_bar == changed_selected_linked.log10_fO2_bar


@pytest.mark.xfail(
    strict=True,
    reason="Rego pCO=P_total-pNa_obs; physics b-1195/d-405 owns the fix",
)
def test_rego_na_oxygen_condition_is_independent_of_observed_target() -> None:
    experiment = F.tabulation_experiment(
        "rego-target-mutation",
        T_K=Decimal("1573.15"),
        total_P=Decimal("102844.875"),
    )
    row = _na_partial_row(
        source_id=REGO_SOURCE,
        observation_id="rego-target-mutation-pNa",
        experiment_id=experiment.experiment_id,
        temperature_K="1573.15",
        pressure_Pa="226.968",
        total_pressure_Pa="102844.875",
    )
    selected = resolve_row_fo2(
        experiment, None, row, quantity=Quantity.P_PARTIAL
    )
    changed = resolve_row_fo2(
        experiment,
        None,
        replace(row, value=Value.point_of(row.value.point * 1.7)),
        quantity=Quantity.P_PARTIAL,
    )
    assert selected is not None and changed is not None
    assert changed.log10_fO2_bar == selected.log10_fO2_bar


def test_rys_table10a_child_restores_nakalsi_1_row_8() -> None:
    legacy_path = (
        ROOT
        / "data/literature/extracts/rys-2007-alkali-oxide-thermodynamics.yaml"
    )
    legacy = yaml.safe_load(legacy_path.read_text(encoding="utf-8"))
    table10a = next(
        row
        for row in legacy["species"]["Na"]["observations"]
        if row["observation_id"] == "rys_2007_appendix_na_partial_pressures_table10a"
    )
    row8 = [
        point
        for point in table10a["values"]["points"]
        if point.get("source_row") == 8
    ]
    assert len(row8) == 1
    assert row8[0]["sample_as_printed"] == "NaKAlSi_1"
    assert row8[0]["T_K"] == "1742.385"
    assert row8[0]["pressure_Pa"] == "0.01475"

    migrated_path = (
        ROOT
        / "data/literature/extracts-v2/rys-2007-alkali-oxide-thermodynamics.yaml"
    )
    migrated = migrated_path.read_text(encoding="utf-8")
    child_ref = re.search(
        r"(?m)^  - (rys-2007-alkali-oxide-thermodynamics::"
        r"rys_2007_appendix_na_partial_pressures_table10a::"
        r"T=1742\.385:h=[0-9a-f]{12})$",
        migrated,
    )
    assert child_ref is not None
    assert migrated.count(child_ref.group(1)) >= 2


def test_rys_na_extract_keeps_unclassified_row_oxygen_unknown() -> None:
    migrated_path = (
        ROOT
        / "data/literature/extracts-v2/rys-2007-alkali-oxide-thermodynamics.yaml"
    )
    migrated = yaml.load(
        migrated_path.read_text(encoding="utf-8"),
        Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader),
    )
    rows = [
        row
        for row in migrated["observations"]
        if row["identity"]["species"]["formula"] == "Na"
        and row["identity"]["quantity"]["value"] == "p_partial"
    ]
    assert rows
    assert all(row["identity"]["fO2_Pa"]["tag"] == "unknown" for row in rows)
    assert all(
        row["identity"]["fO2_Pa"]["reason"] == "no fO2_Pa mapped from source"
        for row in rows
    )


def test_every_rys_na_source_row_has_target_independent_oxygen_input() -> None:
    from simulator.battery.score import load_score_context
    from simulator.battery.source_oxygen import (
        SourceOxygenCondition,
        resolve_source_oxygen,
    )

    context = load_score_context(sources=(RYS_SOURCE,))
    source_rows = tuple(context.observations.values())
    targets = tuple(
        row for row in source_rows
        if row.source_id == RYS_SOURCE
        and row.identity.species.formula == "Na"
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.P_PARTIAL
    )
    assert targets

    measured_link_count = 0
    closure_count = 0
    for row in targets:
        experiment = context.experiments[row.experiment_id]
        bench = context.benches[experiment.bench_id]
        selected_source = resolve_source_oxygen(row, source_rows, bench)
        source_oxygen_bar = (
            float(selected_source.observation.value.point) / PA_PER_BAR
            if isinstance(selected_source, SourceOxygenCondition)
            else None
        )
        measured_link_count += isinstance(selected_source, SourceOxygenCondition)
        resolved = resolve_row_fo2(
            experiment,
            bench,
            row,
            quantity=Quantity.P_PARTIAL,
            source_oxygen_bar=source_oxygen_bar,
        )
        changed = resolve_row_fo2(
            experiment,
            bench,
            replace(
                row,
                value=Value.point_of(row.value.point * Decimal("1.7")),
            ),
            quantity=Quantity.P_PARTIAL,
            source_oxygen_bar=source_oxygen_bar,
        )
        if isinstance(selected_source, SourceOxygenCondition):
            assert resolved is not None
            assert resolved.route == "source_linked_oxygen_condition"
            assert resolved.closure is False
        else:
            assert resolved is not None and resolved.closure
            assert resolved.po2_bar is None
            assert resolved.route.endswith("rys_pO2_provenance_unknown")
            assert fo2_tier_notice(Quantity.P_PARTIAL, resolved).kind is (
                NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
            )
            closure_count += 1
        assert (
            None if resolved is None else (
                resolved.route,
                resolved.tier,
                resolved.physics_rung,
                resolved.po2_bar,
                resolved.closure,
                resolved.cell_material,
                None if resolved.closure else resolved.log10_fO2_bar,
            )
        ) == (
            None if changed is None else (
                changed.route,
                changed.tier,
                changed.physics_rung,
                changed.po2_bar,
                changed.closure,
                changed.cell_material,
                None if changed.closure else changed.log10_fO2_bar,
            )
        )
    assert len(targets) == 330
    assert measured_link_count == 25
    assert closure_count == 305
