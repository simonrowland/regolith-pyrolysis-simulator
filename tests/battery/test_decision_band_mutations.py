"""Mutation regressions for the source-owned decision-band policy."""

from __future__ import annotations

import ast
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    AmountBasis,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    MetricOperation,
    NoticeKind,
    PerBasis,
    Phase,
    Quantity,
    ResidualStatus,
    SourceRelation,
    UncertaintyKind,
    ValenceDenominatorKind,
    ValenceValueForm,
)
from simulator.battery.identity import Identity, ValenceBasis
from simulator.battery.records import (
    Composition,
    DecisionBand,
    Execution,
    Locator,
    Notice,
    Species,
    State,
    Uncertainty,
)
from simulator.battery.score import (
    DECISION_BAND_POLICY_VERSION,
    DECISION_BAND_PERCENTILE,
    EnginePrediction,
    NORMAL_95_TICKET_MULTIPLIER,
    ScoreContext,
    _decision_band_kind,
    _group_token,
    _printed_uncertainty_band,
    _source_alignment_group_key,
    _source_band_catalog,
    decision_band_for,
    match_status,
    populate_numeric,
    score_store,
    update_decision_band_ratchet,
)
from tests.battery import factories as F

# z95 = Φ⁻¹((1 + .95) / 2); the MAD consistency scale is
# 1 / Φ⁻¹(.75), since Φ⁻¹(.75) is the median absolute deviation of N(0, 1).
_Z95 = Decimal("1.959963984540054")
_MAD_NORMAL_SCALE = Decimal("1.482602218505602")


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
    bands, _records, _no_band_reasons = _source_band_catalog(context, {})
    keys = {
        _group_token(key)
        for row in context.observations.values()
        if (key := _source_alignment_group_key(row, tier=tier)) is not None
    }
    found = [bands[key] for key in keys if key in bands]
    return found[0] if len(found) == 1 else None


def test_printed_uncertainty_kinds_precede_derived_and_unparseable_refuses() -> None:
    def resolve(
        raw: str,
        *,
        confidence_level: str = "not_stated",
        basis: str | None = None,
        value=None,
        include_value: bool = True,
    ):
        verbatim = {"note": raw, "confidence_level": confidence_level}
        if include_value:
            verbatim["value"] = value if value is not None else "0.1"
        uncertainty = Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim=verbatim,
            value=None,
            basis=basis,
        )
        return _printed_uncertainty_band(
            Quantity.DELTA_FG,
            uncertainty,
            Decimal("2"),
        )

    one_sigma = resolve("Table 1 SD", confidence_level="one_sigma")
    assert one_sigma.band is not None
    assert one_sigma.band.value == _Z95 * Decimal("0.1")
    assert _decision_band_kind(one_sigma.band) == "printed_1sigma"

    field_name_only = resolve("sigma_x", confidence_level="not_stated")
    assert field_name_only.band is not None
    assert field_name_only.band.value == Decimal("0.1")
    assert _decision_band_kind(field_name_only.band) == "printed_unstated"
    assert field_name_only.notice is not None
    assert field_name_only.notice.reason == "confidence_level_not_stated"

    two_sigma = resolve("Table 1 2-sigma", confidence_level="two_sigma")
    assert two_sigma.band is not None
    assert two_sigma.band.value == Decimal("0.1") / Decimal(2) * _Z95
    assert _decision_band_kind(two_sigma.band) == "printed_2sigma"

    confidence_interval = resolve("Table 2 95% confidence interval", confidence_level="ci95")
    assert confidence_interval.band is not None
    assert confidence_interval.band.value == Decimal("0.1")
    assert _decision_band_kind(confidence_interval.band) == "printed_2sigma"

    bounds = resolve("range", value=(Decimal("1.0"), Decimal("1.4")))
    assert bounds.band is not None
    assert bounds.band.value == Decimal("0.2")
    assert _decision_band_kind(bounds.band) == "printed_range"
    assert bounds.notice is not None
    assert bounds.notice.reason == "confidence_level_not_stated"

    unstated = resolve("printed width")
    assert unstated.band is not None
    assert unstated.band.value == Decimal("0.1")
    assert _decision_band_kind(unstated.band) == "printed_unstated"
    assert unstated.notice is not None
    assert unstated.notice.reason == "confidence_level_not_stated"
    assert "field=" in unstated.notice.band
    assert "printed_width=0.1" in unstated.notice.band

    unparseable = resolve("uncertainty not quantified", include_value=False)
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


def test_unstated_plus_minus_defaults_to_one_sigma_with_two_sigma_alternative() -> None:
    resolved = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim="± 0.1 kJ/mol",
        ),
        Decimal("2"),
    )

    assert resolved.band is not None
    assert _decision_band_kind(resolved.band) == "printed_1sigma"
    assert resolved.band.value == Decimal("0.1") * NORMAL_95_TICKET_MULTIPLIER
    assert resolved.notice is not None
    assert resolved.notice.reason == "uncertainty_confidence_level_assumed_1sigma"
    assert resolved.two_sigma_band is not None
    # Printed half-width / k = 0.1 / 2 = 0.05 kJ/mol; the alternative
    # band tickets that standard width with the normal-95 transform.
    assert resolved.two_sigma_band.value == (
        Decimal("0.05") * NORMAL_95_TICKET_MULTIPLIER
    )


def test_coverage_normalization_callers_use_the_shared_owner() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative_path, target in (
        ("simulator/battery/score.py", "metric_standard_width"),
        ("simulator/battery/score.py", "two_sigma_measurement_dex"),
        ("simulator/battery/migrate.py", "numeric_extra_unc"),
    ):
        tree = ast.parse((root / relative_path).read_text(encoding="utf-8"))
        normalized_assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(name, ast.Name) and name.id == target
                for target_node in node.targets
                for name in ast.walk(target_node)
            )
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "standard_uncertainty_width"
        ]
        assert len(normalized_assignments) == 1, relative_path


def test_printed_confidence_text_precedes_default_and_method_override() -> None:
    resolved = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim="±0.1 kJ/mol (2 sigma)",
        ),
        Decimal("2"),
        confidence_level_override="one_sigma",
    )

    assert resolved.band is not None
    assert resolved.band.value == Decimal("0.1") / Decimal(2) * _Z95
    assert _decision_band_kind(resolved.band) == "printed_2sigma"
    assert resolved.notice is None
    assert resolved.two_sigma_band is None
    numeric, reason, detail = populate_numeric(
        quantity=Quantity.DELTA_FG,
        candidate=Decimal("2.15"),
        reference=Decimal("2"),
        source_relation=SourceRelation.UNKNOWN,
        printed_band=resolved.band,
        printed_refusal=resolved.refusal,
    )
    assert reason is None, detail
    assert numeric is not None
    assert match_status(numeric) is ResidualStatus.MISMATCH

    sossi_sd = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim="±0.1 kJ/mol (as printed; SD per Fig. 2 caption)",
        ),
        Decimal("2"),
    )
    assert sossi_sd.band is not None
    assert sossi_sd.band.value == Decimal("0.1") * NORMAL_95_TICKET_MULTIPLIER
    assert _decision_band_kind(sossi_sd.band) == "printed_1sigma"
    assert sossi_sd.notice is None
    assert sossi_sd.two_sigma_band is None


def test_printed_uncertainty_converts_units_before_setting_ticket() -> None:
    from simulator.battery.enums import QUANTITY_UNITS

    def source(unit: str, quantity: Quantity):
        return SimpleNamespace(
            locator=Locator(note=f"printed unit='{unit}'"),
            derivation=SimpleNamespace(output_unit=QUANTITY_UNITS[quantity]),
        )

    relative = _printed_uncertainty_band(
        Quantity.O2_YIELD,
        Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="2%"),
        Decimal("0.5"),
    )
    assert relative.band is not None
    assert relative.band.unit == "dimensionless"
    assert relative.band.value == Decimal("0.02")

    kcal = _printed_uncertainty_band(
        Quantity.DELTA_FG,
        Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="0.137"),
        Decimal("-1"),
        source_observation=source("kcal gfw^-1", Quantity.DELTA_FG),
    )
    assert kcal.band is not None
    assert kcal.band.unit == "kJ_per_declared_mol_basis"
    assert kcal.band.value == Decimal("0.573208")

    heat = _printed_uncertainty_band(
        Quantity.CP,
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim={
                "note": "0.24 (2-sigma)",
                "value": "0.24",
                "confidence_level": "two_sigma",
            },
        ),
        Decimal("300"),
        source_observation=source("cal deg^-1 gfw^-1", Quantity.CP),
    )
    assert heat.band is not None
    assert heat.band.unit == "J_per_declared_mol_basis_per_K"
    assert heat.band.value == Decimal("1.00416") / Decimal(2) * NORMAL_95_TICKET_MULTIPLIER

    pressure = _printed_uncertainty_band(
        Quantity.P_PARTIAL,
        Uncertainty(
            kind=UncertaintyKind.PRINTED,
            verbatim={
                "note": "1 kPa (2-sigma)",
                "value": "1 kPa",
                "confidence_level": "two_sigma",
            },
        ),
        Decimal("100000"),
        source_observation=source("Pa", Quantity.P_PARTIAL),
    )
    # A printed 1 kPa two-sigma width means endpoints at 100,500 and 99,500 Pa.
    expected_standard_dex = max(
        abs(Decimal("100500").ln() - Decimal("100000").ln()) / Decimal(10).ln(),
        abs(Decimal("99500").ln() - Decimal("100000").ln()) / Decimal(10).ln(),
    )
    # The standard dex width gets the normal-95 ticket multiplier once.
    expected_dex = expected_standard_dex * NORMAL_95_TICKET_MULTIPLIER
    assert pressure.band is not None
    assert pressure.band.unit == "dimensionless"
    assert pressure.standard_uncertainty_dex is not None
    assert abs(pressure.band.value - expected_dex) < Decimal("1e-27")
    assert (
        abs(pressure.standard_uncertainty_dex - expected_standard_dex)
        < Decimal("1e-27")
    )
    assert _decision_band_kind(pressure.band) == "printed_2sigma"


def test_log_activity_envelope_selects_the_row_temperature_without_interpolation() -> None:
    from simulator.battery.records import State

    def resolve(temperature_K: str, envelope: dict[str, str]):
        observation = SimpleNamespace(
            identity=SimpleNamespace(temperature_K=State.of(Decimal(temperature_K))),
            locator=None,
            derivation=None,
        )
        return _printed_uncertainty_band(
            Quantity.ACTIVITY,
            Uncertainty(
                kind=UncertaintyKind.PRINTED,
                verbatim={"log10_a_envelope": envelope},
            ),
            Decimal("0.1"),
            source_observation=observation,
        )

    envelope = {"1100_C": "0.16", "1200_C": "0.15", "1300_C": "0.14"}
    for temperature_K, expected in (
        ("1373.15", Decimal("0.16")),
        ("1473.15", Decimal("0.15")),
        ("1573.15", Decimal("0.14")),
    ):
        resolved = resolve(temperature_K, envelope)
        assert resolved.band is not None
        assert resolved.band.value == expected
        assert resolved.band.unit == "dimensionless"

    kelvin_key = resolve("1373.15", {"1373.15_K": "0.16"})
    assert kelvin_key.band is not None
    assert kelvin_key.band.value == Decimal("0.16")

    mismatch = resolve("1423.15", envelope)
    assert mismatch.band is None
    assert mismatch.refusal == {
        "reason": "printed_uncertainty_temperature_unavailable",
        "temperature_K": "1423.15",
        "available_temperatures": ["1100_C", "1200_C", "1300_C"],
    }


def test_incompatible_borrowed_band_is_rejected_without_losing_numeric_residual() -> None:
    numeric, reason, detail = populate_numeric(
        quantity=Quantity.CP,
        candidate=Decimal("301"),
        reference=Decimal("300"),
        source_relation=SourceRelation.INDEPENDENT,
        derived_band=DecisionBand(
            Decimal("0.2"),
            "kJ_per_declared_mol_basis",
            "borrowed delta_fG band",
        ),
    )
    assert reason is None, detail
    assert numeric is not None
    assert numeric.value == Decimal("1")
    assert numeric.decision_band is None


@pytest.mark.parametrize(
    "quantity", (Quantity.ACTIVITY, Quantity.ACTIVITY_COEFFICIENT)
)
def test_kems_pressure_band_is_not_admitted_for_dimensionless_activity(
    quantity: Quantity,
) -> None:
    numeric, reason, detail = populate_numeric(
        quantity=quantity,
        candidate=Decimal("0.5"),
        reference=Decimal("0.5"),
        source_relation=SourceRelation.INDEPENDENT,
        derived_band=DecisionBand(
            Decimal("0.146128"),
            "dimensionless",
            "KEMS p_partial measured uncertainty: test pressure envelope",
        ),
    )
    assert reason is None, detail
    assert numeric is not None
    assert numeric.decision_band is None


def test_source_scatter_requires_exact_alignment_and_distinct_sources(tmp_path: Path) -> None:
    experiment, rows, origins = _observations(
        (Decimal("10"), Decimal("11"), Decimal("12")),
        tier="empirical",
    )
    aligned = _context(tmp_path, rows, origins, experiment)
    band = _only_band(aligned, tier="empirical")
    assert band is not None
    assert _decision_band_kind(band) == "derived_scatter"
    assert band.value == _Z95 * _MAD_NORMAL_SCALE
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


def test_numeric_temperature_spellings_share_alignment_but_distinct_values_do_not(
    tmp_path: Path,
) -> None:
    experiment, rows, origins = _observations(
        (Decimal("10"), Decimal("12")),
        temperatures=(Decimal("1000"), Decimal("1000.0")),
    )
    first_key = _source_alignment_group_key(rows[0], tier="compilation")
    second_key = _source_alignment_group_key(rows[1], tier="compilation")
    assert first_key == second_key
    band = _only_band(_context(tmp_path, rows, origins, experiment), tier="compilation")
    assert band is not None
    assert band.value == _Z95 * _MAD_NORMAL_SCALE

    distinct_experiment, distinct_rows, distinct_origins = _observations(
        (Decimal("10"), Decimal("12")),
        temperatures=(Decimal("1000"), Decimal("1000.0001")),
    )
    distinct = _context(tmp_path, distinct_rows, distinct_origins, distinct_experiment)
    assert _source_alignment_group_key(
        distinct_rows[0], tier="compilation"
    ) != _source_alignment_group_key(distinct_rows[1], tier="compilation")
    assert _only_band(distinct, tier="compilation") is None


def test_source_band_catalog_preserves_series_cell_alignment_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.score as score_module
    from simulator.battery.compilation_tier import _iter_compilation_series_cells
    from simulator.battery.records import Value, ValueKind

    experiment, rows, origins = _observations(
        (Decimal("0"), Decimal("0")), source_ids=("series-a", "series-b")
    )
    series_rows = (
        replace(
            rows[0],
            value=Value(
                ValueKind.SERIES,
                series=(
                    (Decimal("1000"), Decimal("10")),
                    (Decimal("1100"), Decimal("20")),
                ),
            ),
        ),
        replace(
            rows[1],
            value=Value(
                ValueKind.SERIES,
                series=(
                    (Decimal("1000"), Decimal("12")),
                    (Decimal("1100"), Decimal("22")),
                ),
            ),
        ),
    )
    context = _context(tmp_path, series_rows, origins, experiment)
    expected_tokens = {
        _group_token(key)
        for row in series_rows
        for cell in _iter_compilation_series_cells(
            row, origins.get(row.observation_id)
        )
        if (key := _source_alignment_group_key(cell, tier="compilation")) is not None
    }
    original_group_key = score_module._source_alignment_group_key
    group_key_calls = []

    def count_group_key_calls(observation, *, tier):
        group_key_calls.append(observation.observation_id)
        return original_group_key(observation, tier=tier)

    monkeypatch.setattr(
        score_module, "_source_alignment_group_key", count_group_key_calls
    )
    import simulator.battery.compilation_tier as compilation_tier

    built_cells = []
    original_cell = compilation_tier._series_point_cell

    def count_series_cells(*args, **kwargs):
        cell = original_cell(*args, **kwargs)
        built_cells.append(cell.observation_id)
        return cell

    iterated = []
    original_iter = compilation_tier._iter_compilation_series_cells

    def count_series_expansion(observation, origin=None):
        iterated.append(observation.observation_id)
        return original_iter(observation, origin)

    monkeypatch.setattr(compilation_tier, "_series_point_cell", count_series_cells)
    monkeypatch.setattr(
        compilation_tier, "_iter_compilation_series_cells", count_series_expansion
    )

    bands, records, _no_band_reasons = _source_band_catalog(context, {})

    assert len(expected_tokens) == 2
    assert len(group_key_calls) == len(series_rows)
    assert len(built_cells) == len(series_rows)
    assert iterated == []
    assert set(bands) == expected_tokens
    assert [record["aligned_T_K"] for record in records] == ["1000", "1100"]
    assert all(band.value == _Z95 * _MAD_NORMAL_SCALE for band in bands.values())


def test_valence_basis_is_part_of_source_alignment_key() -> None:
    first_basis = ValenceBasis(
        numerator_oxidation_states=(2,),
        denominator_kind=ValenceDenominatorKind.TOTAL_ELEMENT,
        denominator_oxidation_state=None,
        value_form=ValenceValueForm.FRACTION,
    )
    second_basis = ValenceBasis(
        numerator_oxidation_states=(3,),
        denominator_kind=ValenceDenominatorKind.TOTAL_ELEMENT,
        denominator_oxidation_state=None,
        value_form=ValenceValueForm.FRACTION,
    )
    common_identity = {
        "quantity": Quantity.VALENCE_RATIO,
        "species": Species("Cr", Phase.L),
        "temperature_K": State.of(Decimal("1673.15")),
        "total_pressure_Pa": State.of(Decimal("100000")),
        "composition": State.of(
            Composition(
                "mole_fraction",
                (("Cr", Decimal("1")),),
                AmountBasis.MOLE_FRACTION,
            )
        ),
        "fO2_Pa": State.of(Decimal("1e-8")),
    }
    first = F.observation(
        "valence-first",
        "valence-exp",
        Identity(
            **common_identity,
            valence_basis=State.of(first_basis),
        ),
        Decimal("0.2"),
    )
    second = F.observation(
        "valence-second",
        "valence-exp",
        Identity(
            **common_identity,
            valence_basis=State.of(second_basis),
        ),
        Decimal("0.3"),
    )

    first_key = _source_alignment_group_key(first, tier="compilation")
    second_key = _source_alignment_group_key(second, tier="compilation")
    assert first_key is not None
    assert second_key is not None
    assert first_key != second_key
    assert first_key["axes"]["valence_basis"] != second_key["axes"]["valence_basis"]


def test_zero_mad_aligned_group_has_typed_no_band_reason(tmp_path: Path) -> None:
    experiment, rows, origins = _observations(
        (Decimal("3.00"), Decimal("3.00")),
        tier="compilation",
    )
    context = _context(tmp_path, rows, origins, experiment)
    bands, records, no_band_reasons = _source_band_catalog(context, {})
    key = _source_alignment_group_key(rows[0], tier="compilation")
    assert key is not None
    token = _group_token(key)
    assert token not in bands
    assert records == []
    assert no_band_reasons[token] == "zero_scatter_aligned_group"


def test_zero_scatter_no_band_keeps_numeric_residual_and_prediction_flag(
    tmp_path: Path,
) -> None:
    experiment, rows, origins = _observations(
        (Decimal("3.00"), Decimal("3.00")),
        tier="compilation",
    )
    context = _context(tmp_path, rows, origins, experiment)
    prediction_flag = Notice(
        kind=NoticeKind.INPUT_OMITTED,
        affected_quantities=(Quantity.DELTA_FG,),
        reason="prediction_flag_kept",
        origin="zero-scatter-test",
    )

    def predict(engine, reference, **_kwargs):
        return EnginePrediction(
            engine=engine,
            channel="internal-analytical",
            execution=Execution(
                state=ExecutionState.PRODUCED,
                call_evidence="zero-scatter-test",
            ),
            value=reference.value.point + Decimal("0.1"),
            unit="kJ_per_declared_mol_basis",
            authority=Authority.CERTIFIED,
            coefficient_sources=(),
            lineage_complete=False,
            identity=reference.identity,
            notices=(prediction_flag,),
        )

    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=predict,
    )
    scored = [row for row in residuals if row.numeric is not None]
    assert len(scored) == 2
    for row in scored:
        assert row.numeric is not None
        assert row.numeric.decision_band is None
        assert row.status.value == "no_band"
        reasons = {notice.reason for notice in row.notices}
        assert "zero_scatter_aligned_group" in reasons
        assert "prediction_flag_kept" in reasons


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
    assert first["ratchet_action"] == "new"
    assert first["old_line"] is None
    assert Decimal(first["new_line"]) == _Z95 * _MAD_NORMAL_SCALE
    assert Decimal(first["effective_line"]) == first_line
    assert first["band_kind"] == "derived_scatter"
    assert first["n_sources"] == 3
    assert first["aligned_T_K"] == "1000"
    first_history = initial["history"][-1]
    assert first_history["old_line"] is None
    assert Decimal(first_history["new_line"]) == _Z95 * _MAD_NORMAL_SCALE
    assert Decimal(first_history["effective_line"]) == first_line
    assert first_history["action"] == "new"

    wide_experiment, wide_rows, wide_origins = _observations(
        (Decimal("1"), Decimal("3"), Decimal("5")),
        tier="compilation",
    )
    wide = _context(tmp_path, wide_rows, wide_origins, wide_experiment)
    update_decision_band_ratchet(wide)
    wide_payload = yaml.safe_load(ratchet_path.read_text(encoding="utf-8"))
    unchanged = wide_payload["groups"][0]
    assert Decimal(unchanged["ticket_line"]) == first_line
    assert unchanged["source_ids"] == first["source_ids"]
    held = wide_payload["history"][-1]
    assert Decimal(held["old_line"]) == first_line
    assert Decimal(held["new_line"]) == _Z95 * (_MAD_NORMAL_SCALE * Decimal("2"))
    assert Decimal(held["effective_line"]) == first_line
    assert held["action"] == "held"

    narrow_experiment, narrow_rows, narrow_origins = _observations(
        (Decimal("1"), Decimal("1.1"), Decimal("1.2")),
        tier="compilation",
    )
    narrow = _context(tmp_path, narrow_rows, narrow_origins, narrow_experiment)
    update_decision_band_ratchet(narrow)
    narrow_payload = yaml.safe_load(ratchet_path.read_text(encoding="utf-8"))
    narrowed = narrow_payload["groups"][0]
    assert Decimal(narrowed["ticket_line"]) < first_line
    down = narrow_payload["history"][-1]
    assert Decimal(down["old_line"]) == first_line
    assert Decimal(down["new_line"]) < first_line
    assert down["effective_line"] == down["new_line"]
    assert down["action"] == "down"


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

    update_decision_band_ratchet(context)
    ratchet_path = tmp_path / "data/battery/decision-band-ratchet.yaml"
    before = ratchet_path.read_bytes()
    score_with_mutation(Decimal("1"), "normal-score-read-only")
    assert ratchet_path.read_bytes() == before


def test_zero_scatter_update_removes_previous_group_from_ratchet(tmp_path: Path) -> None:
    experiment, rows, origins = _observations(
        (Decimal("1"), Decimal("2"), Decimal("3")),
    )
    context = _context(tmp_path, rows, origins, experiment)
    assert update_decision_band_ratchet(context) == 1
    ratchet_path = tmp_path / "data/battery/decision-band-ratchet.yaml"
    assert len(yaml.safe_load(ratchet_path.read_text(encoding="utf-8"))["groups"]) == 1

    zero_experiment, zero_rows, zero_origins = _observations(
        (Decimal("2"), Decimal("2"), Decimal("2")),
    )
    zero = _context(tmp_path, zero_rows, zero_origins, zero_experiment)
    assert update_decision_band_ratchet(zero) == 0
    updated = yaml.safe_load(ratchet_path.read_text(encoding="utf-8"))
    assert updated["groups"] == []
    assert len(updated["history"]) == 1


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
