"""Mutation regressions for the source-owned decision-band policy."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    MetricOperation,
    PerBasis,
    Phase,
    Quantity,
    SourceRelation,
    UncertaintyKind,
)
from simulator.battery.records import DecisionBand, Execution, Uncertainty
from simulator.battery.score import (
    DECISION_BAND_POLICY_VERSION,
    DECISION_BAND_PERCENTILE,
    NORMAL_95_TICKET_MULTIPLIER,
    EnginePrediction,
    ScoreContext,
    _decision_band_kind,
    _group_token,
    _printed_uncertainty_band,
    _source_alignment_group_key,
    _source_band_catalog,
    decision_band_for,
    score_store,
    update_decision_band_ratchet,
)
from tests.battery import factories as F


def _observations(
    values: tuple[Decimal, ...],
    *,
    tier: str = "compilation",
    source_ids: tuple[str, ...] | None = None,
    temperatures: tuple[Decimal, ...] | None = None,
):
    experiment = F.tabulation_experiment()
    source_ids = source_ids or tuple(f"source-{index}" for index in range(len(values)))
    temperatures = temperatures or (Decimal("1000"),) * len(values)
    rows = []
    origins = {}
    for index, (value, source_id, temperature) in enumerate(
        zip(values, source_ids, temperatures, strict=True)
    ):
        identity = F.oxide_identity(
            "CaO",
            Phase.L,
            T_K=temperature,
            per=PerBasis.MOL_O2,
            metal_formula="Ca",
            nu_metal=Fraction(1),
            nu_o2=Fraction(1, 2),
        )
        observation = F.observation(
            f"{source_id}-row-{index}",
            experiment.experiment_id,
            identity,
            value,
            evidence=(
                EvidenceClass.COMPILATION_ASSESSED
                if tier == "compilation"
                else EvidenceClass.MEASURED_DIRECT
            ),
            admission=AdmissionStatus.ADMITTED,
            source_id=source_id,
        )
        rows.append(observation)
        if tier == "compilation":
            origins[observation.observation_id] = f"compilations-janaf/{source_id}.yaml"
    return experiment, tuple(rows), origins


def _context(
    tmp_path: Path,
    rows,
    origins,
    experiment,
) -> ScoreContext:
    return ScoreContext(
        works={F.work().work_id: F.work()},
        experiments={experiment.experiment_id: experiment},
        observations={row.observation_id: row for row in rows},
        origins=origins,
        root=tmp_path,
        hostname="test",
    )


def _only_band(context: ScoreContext, *, tier: str) -> DecisionBand | None:
    bands, _records = _source_band_catalog(context, {})
    keys = {
        _group_token(key)
        for row in context.observations.values()
        if (key := _source_alignment_group_key(row, tier=tier)) is not None
    }
    found = [bands[key] for key in keys if key in bands]
    return found[0] if len(found) == 1 else None


def test_printed_uncertainty_kinds_precede_derived_and_unparseable_refuses() -> None:
    def resolve(raw: str, *, basis: str | None = None, value=None):
        uncertainty = Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim=raw,
            value=value,
            basis=basis,
        )
        return _printed_uncertainty_band(
            Quantity.DELTA_FG,
            uncertainty,
            Decimal("2"),
        )

    one_sigma = resolve("± 0.1 kJ/mol (1σ)")
    assert one_sigma.band is not None
    assert one_sigma.band.value == NORMAL_95_TICKET_MULTIPLIER * Decimal("0.1")
    assert _decision_band_kind(one_sigma.band) == "printed_1sigma"

    standard_error = resolve("0.1", basis="standard_error", value=Decimal("0.1"))
    assert standard_error.band is not None
    assert standard_error.band.value == one_sigma.band.value
    assert _decision_band_kind(standard_error.band) == "printed_1sigma"

    two_sigma = resolve("0.1 (2-sigma)")
    assert two_sigma.band is not None
    assert two_sigma.band.value == Decimal("0.1")
    assert _decision_band_kind(two_sigma.band) == "printed_2sigma"

    confidence_interval = resolve("95% confidence interval: 0.1 kJ/mol")
    assert confidence_interval.band is not None
    assert confidence_interval.band.value == Decimal("0.1")
    assert _decision_band_kind(confidence_interval.band) == "printed_2sigma"

    bounds = resolve("[1.0, 1.4] kJ/mol range")
    assert bounds.band is not None
    assert bounds.band.value == Decimal("0.2")
    assert _decision_band_kind(bounds.band) == "printed_range"

    unstated = resolve("±0.1 kJ/mol")
    assert unstated.band is not None
    assert unstated.band.value == one_sigma.band.value
    assert _decision_band_kind(unstated.band) == "printed_unstated"
    assert unstated.notice is not None
    assert unstated.notice.reason == "uncertainty_kind_unstated"
    assert unstated.notice.band == "±0.1 kJ/mol"

    unparseable = resolve("uncertainty not quantified")
    assert unparseable.band is None
    assert unparseable.refusal is not None
    assert unparseable.refusal["reason"] == "unparseable_printed_uncertainty"

    derived = DecisionBand(Decimal("10"), "kJ_per_declared_mol_basis", "derived_scatter: source-only")
    assert decision_band_for(
        Quantity.DELTA_FG,
        SourceRelation.UNKNOWN,
        printed_band=two_sigma.band,
        derived_band=derived,
    ) == two_sigma.band
    assert decision_band_for(
        Quantity.DELTA_FG,
        SourceRelation.UNKNOWN,
        printed_refusal=unparseable.refusal,
        derived_band=derived,
    ) is None


def test_printed_uncertainty_converts_units_before_setting_ticket() -> None:
    from simulator.battery.enums import QUANTITY_UNITS

    def source(unit: str, quantity: Quantity):
        return SimpleNamespace(
            locator=SimpleNamespace(note=f"printed unit='{unit}'"),
            derivation=SimpleNamespace(output_unit=QUANTITY_UNITS[quantity]),
        )

    relative = _printed_uncertainty_band(
        Quantity.O2_YIELD,
        Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="2%"),
        Decimal("0.5"),
    )
    assert relative.band is not None
    assert relative.band.unit == "dimensionless"
    assert relative.band.value == NORMAL_95_TICKET_MULTIPLIER * Decimal("0.02")

    kcal = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="0.137"),
        Decimal("-1"),
        source_observation=source("kcal gfw^-1", Quantity.DELTA_FG),
    )
    assert kcal.band is not None
    assert kcal.band.unit == "kJ_per_declared_mol_basis"
    assert kcal.band.value == NORMAL_95_TICKET_MULTIPLIER * Decimal("0.573208")

    heat = _printed_uncertainty_band(
        Quantity.CP,
        Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="0.24 (2-sigma)"),
        Decimal("300"),
        source_observation=source("cal deg^-1 gfw^-1", Quantity.CP),
    )
    assert heat.band is not None
    assert heat.band.unit == "J_per_declared_mol_basis_per_K"
    assert heat.band.value == Decimal("1.00416")

    pressure = _printed_uncertainty_band(
        Quantity.P_PARTIAL,
        Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="1 kPa (2-sigma)"),
        Decimal("100000"),
        source_observation=source("Pa", Quantity.P_PARTIAL),
    )
    expected_dex = max(
        abs(Decimal("1.01").ln() / Decimal(10).ln()),
        abs(Decimal("0.99").ln() / Decimal(10).ln()),
    )
    assert pressure.band is not None
    assert pressure.band.unit == "dimensionless"
    assert pressure.band.value == expected_dex
    assert _decision_band_kind(pressure.band) == "printed_2sigma"


def test_source_scatter_requires_exact_alignment_and_distinct_sources(tmp_path: Path) -> None:
    experiment, rows, origins = _observations(
        (Decimal("10"), Decimal("11"), Decimal("12")),
        tier="empirical",
    )
    aligned = _context(tmp_path, rows, origins, experiment)
    band = _only_band(aligned, tier="empirical")
    assert band is not None
    assert _decision_band_kind(band) == "derived_scatter"
    assert "n_sources=3" in band.rule
    assert "percentile=95" in band.rule

    duplicate = replace(
        rows[0],
        observation_id="duplicate-source-row",
    )
    duplicated_context = _context(
        tmp_path,
        (*rows, duplicate),
        origins,
        experiment,
    )
    assert _only_band(duplicated_context, tier="empirical") == band

    one_source = _context(tmp_path, rows[:1], {}, experiment)
    assert _only_band(one_source, tier="empirical") is None

    misaligned_experiment, misaligned_rows, misaligned_origins = _observations(
        (Decimal("10"), Decimal("11")),
        tier="empirical",
        temperatures=(Decimal("1000"), Decimal("1001")),
    )
    misaligned = _context(
        tmp_path,
        misaligned_rows,
        misaligned_origins,
        misaligned_experiment,
    )
    assert _only_band(misaligned, tier="empirical") is None

    conflict_experiment, conflict_rows, conflict_origins = _observations(
        (Decimal("10"), Decimal("11"), Decimal("12")),
        tier="empirical",
        source_ids=("source-a", "source-a", "source-b"),
    )
    conflict = _context(tmp_path, conflict_rows, conflict_origins, conflict_experiment)
    assert _only_band(conflict, tier="empirical") is None


def test_zero_mad_uses_printed_precision_floor(tmp_path: Path) -> None:
    experiment, rows, origins = _observations(
        (Decimal("3.00"), Decimal("3.00")),
        tier="compilation",
    )
    context = _context(tmp_path, rows, origins, experiment)
    band = _only_band(context, tier="compilation")
    assert band is not None
    assert band.value > 0
    assert "mad=0" in band.rule
    assert "sigma=0.005" in band.rule
    assert "precision_floor_sigma=0.005" in band.rule


def test_ratchet_only_moves_down_and_preserves_the_accepted_record(tmp_path: Path) -> None:
    experiment, rows, origins = _observations(
        (Decimal("1"), Decimal("2"), Decimal("3")),
        tier="compilation",
    )
    context = _context(tmp_path, rows, origins, experiment)
    assert update_decision_band_ratchet(context) == 1
    ratchet_path = tmp_path / "data/battery/decision-band-ratchet.yaml"
    initial = yaml.safe_load(ratchet_path.read_text(encoding="utf-8"))
    assert initial["policy_version"] == DECISION_BAND_POLICY_VERSION
    assert initial["percentile"] == DECISION_BAND_PERCENTILE
    first = initial["groups"][0]
    first_line = Decimal(first["ticket_line"])
    assert first["band_kind"] == "derived_scatter"
    assert first["n_sources"] == 3
    assert first["aligned_T_K"] == "1000"

    wide_experiment, wide_rows, wide_origins = _observations(
        (Decimal("1"), Decimal("3"), Decimal("5")),
        tier="compilation",
    )
    wide = _context(tmp_path, wide_rows, wide_origins, wide_experiment)
    update_decision_band_ratchet(wide)
    unchanged = yaml.safe_load(ratchet_path.read_text(encoding="utf-8"))["groups"][0]
    assert Decimal(unchanged["ticket_line"]) == first_line
    assert unchanged["source_ids"] == first["source_ids"]

    narrow_experiment, narrow_rows, narrow_origins = _observations(
        (Decimal("1"), Decimal("1.1"), Decimal("1.2")),
        tier="compilation",
    )
    narrow = _context(tmp_path, narrow_rows, narrow_origins, narrow_experiment)
    update_decision_band_ratchet(narrow)
    narrowed = yaml.safe_load(ratchet_path.read_text(encoding="utf-8"))["groups"][0]
    assert Decimal(narrowed["ticket_line"]) < first_line


def test_engine_and_fit_mutations_do_not_move_source_band(tmp_path: Path) -> None:
    experiment, rows, origins = _observations(
        (Decimal("10"), Decimal("11"), Decimal("12")),
        tier="compilation",
    )
    context = _context(tmp_path, rows, origins, experiment)

    def score_with_mutation(shift: Decimal, fit_id: str):
        def predict(engine, reference, **kwargs):
            del kwargs
            return EnginePrediction(
                engine=engine,
                channel="internal-analytical",
                execution=Execution(
                    state=ExecutionState.PRODUCED,
                    call_evidence=f"mutation:{fit_id}",
                ),
                value=reference.value.point + shift,
                unit="kJ_per_declared_mol_basis",
                authority=Authority.CERTIFIED,
                coefficient_sources=(fit_id,),
                lineage_complete=False,
                identity=reference.identity,
            )

        residuals, _candidates = score_store(
            context,
            engines=(Engine.INTERNAL_ANALYTICAL,),
            predict=predict,
        )
        return {
            row.reference: (
                row.numeric.value,
                row.numeric.decision_band,
                any(
                    notice.reason == "decision_band_ratchet_missing_first_run"
                    for notice in row.notices
                ),
            )
            for row in residuals
            if row.numeric is not None
        }

    original = score_with_mutation(Decimal("0"), "fit-original")
    mutated = score_with_mutation(Decimal("100"), "fit-mutated")
    assert set(original) == set(mutated) == {row.observation_id for row in rows}
    assert all(original[key][1] == mutated[key][1] for key in original)
    assert all(original[key][2] and mutated[key][2] for key in original)
    assert any(original[key][0] != mutated[key][0] for key in original)


@pytest.mark.parametrize("tier", ("compilation", "empirical"))
def test_alignment_group_includes_tier(tmp_path: Path, tier: str) -> None:
    experiment, rows, origins = _observations(
        (Decimal("10"), Decimal("11")),
        tier=tier,
    )
    context = _context(tmp_path, rows, origins, experiment)
    key = _source_alignment_group_key(rows[0], tier=tier)
    assert key is not None
    assert key["comparison_set"] == tier
    assert _only_band(context, tier=tier) is not None
