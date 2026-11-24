"""Channel-scoped KEMS calibration and unchanged author pressure values."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    AmountBasis,
    Engine,
    EvidenceClass,
    Phase,
    Quantity,
    RefusalReason,
)
from simulator.battery.identity import atm_to_pa, quantity_token
from simulator.battery.records import Composition, Located, Species, State
from simulator.battery.score import (
    ScoreContext,
    _has_ungrounded_kems_partial_pressure_calibration,
    _kems_replicate_groups,
    _unverified_apparatus_notices,
    compile_residual,
)
from simulator.battery.validity import (
    GateCheck,
    GateOutcome,
    _calibration_grounded,
    effusion_regime_unverified,
    _partial_pressure_observations_by_experiment,
    underdetermined_apparatus,
)
from simulator.battery import validity
from tests.battery import factories as F
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[2]
DEMARIA_EXTRACT = ROOT / "data/literature/extracts/kems-022-demaria-1971.yaml"
COSTA_EXTRACT = ROOT / "data/literature/extracts/kems-007-costa-2015.yaml"


def _located_state(value: object | None = None, *, unknown: str | None = None, locator=True):
    state = State.unknown(unknown) if unknown is not None else State.of(value)
    return Located(state, locator=F.loc(page=4) if locator else None)


def _scoped_experiment(calibration: dict[str, Located], experiment_id: str = "qms-run"):
    experiment = F.kems_experiment(experiment_id, calibrated=False)
    assert experiment.apparatus is not None
    return replace(
        experiment,
        apparatus=replace(experiment.apparatus, calibration=calibration),
    )


def _pressure_row(experiment_id: str, observation_id: str, *, species: str = "Na", value="10"):
    identity = replace(F.pref_identity(), quantity=Quantity.P_PARTIAL)
    identity = replace(identity, composition=F.activity_identity().composition)
    if species != identity.species.formula:
        identity = replace(identity, species=Species(species, Phase.G))
    return F.observation(
        observation_id,
        experiment_id,
        identity,
        Decimal(value),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="qms-test-source",
    )


def test_scoped_calibration_does_not_accept_an_unrelated_channel_or_run() -> None:
    row = _pressure_row("qms-run", "target-na")
    unrelated_experiment = _scoped_experiment(
        {"standard@species=Fe": _located_state("silver")}
    )
    assert _calibration_grounded(
        unrelated_experiment.apparatus.calibration,
        observation=row,
        experiment_id=unrelated_experiment.experiment_id,
    ) is False
    assert _has_ungrounded_kems_partial_pressure_calibration(
        row, unrelated_experiment
    ) is True

    another_run = _scoped_experiment(
        {"standard@species=Na": _located_state("silver")},
        experiment_id="other-run",
    )
    assert _calibration_grounded(
        another_run.apparatus.calibration,
        observation=row,
        experiment_id=another_run.experiment_id,
    ) is False
    assert _has_ungrounded_kems_partial_pressure_calibration(row, another_run) is True


def test_target_channel_unknown_standard_cannot_fall_back_to_another_channel() -> None:
    row = _pressure_row("qms-run", "target-na")
    experiment = _scoped_experiment(
        {
            "standard@all_species": _located_state("silver"),
            "standard@species=Na": _located_state(unknown="not printed"),
            "standard@species=Fe": _located_state("iron vapor"),
        }
    )
    own_channel = experiment.apparatus.calibration["standard@species=Na"]
    assert own_channel.state.is_unknown
    assert not own_channel.state.is_value
    assert not _calibration_grounded(
        experiment.apparatus.calibration,
        observation=row,
        experiment_id=experiment.experiment_id,
    )
    assert _has_ungrounded_kems_partial_pressure_calibration(row, experiment)


def test_unrelated_unknown_does_not_veto_located_target_calibration() -> None:
    row = _pressure_row("qms-run", "target-na")
    experiment = _scoped_experiment(
        {
            "standard@species=Na": _located_state("silver"),
            "sensitivity_factor@species=Na": _located_state(
                unknown="numeric factor not reported"
            ),
            "standard@species=Fe": _located_state(unknown="not published"),
        }
    )
    sensitivity = experiment.apparatus.calibration[
        "sensitivity_factor@species=Na"
    ]
    assert sensitivity.state.is_unknown
    assert not sensitivity.state.is_value
    assert _calibration_grounded(
        experiment.apparatus.calibration,
        observation=row,
        experiment_id=experiment.experiment_id,
    )
    assert not _has_ungrounded_kems_partial_pressure_calibration(row, experiment)


def test_missing_locator_makes_scoped_calibration_ungrounded() -> None:
    row = _pressure_row("qms-run", "target-na")
    experiment = _scoped_experiment(
        {
            "standard@species=Na": _located_state("silver"),
            "sensitivity_factor@species=Na": _located_state(
                unknown="numeric factor not reported", locator=False
            ),
        }
    )
    assert not _calibration_grounded(
        experiment.apparatus.calibration,
        observation=row,
        experiment_id=experiment.experiment_id,
    )
    assert _has_ungrounded_kems_partial_pressure_calibration(row, experiment)


def test_reduced_kems_policy_flags_are_visible_as_scoring_notices() -> None:
    experiment = F.kems_experiment(calibrated=False, kn=None)
    row = _pressure_row(experiment.experiment_id, "policy-flag-row")
    calibration = GateCheck(
        "kems_calibration",
        True,
        {
            "flag": "calibration_not_grounded",
            "calibration_notice": "No target-channel calibration is recorded.",
            "reason": "calibration evidence is not source-attributed",
        },
    )
    assumed = GateOutcome(
        passed=True,
        checks=(
            GateCheck("in_cell_partial_pressure_sum", False, {"reason": "not printed"}),
            GateCheck(
                "effusion_regime_policy",
                True,
                {
                    "flag": "effusion_regime_assumed",
                    "reason": "molecular regime assumed because no printed check is available",
                    "covered_checks": ("in_cell_partial_pressure_sum",),
                    "thresholds": {
                        "in_cell_partial_pressure_sum": {"pressure_limit_Pa": "10"}
                    },
                    "authority": "extrapolated",
                },
            ),
            calibration,
        ),
    )
    assumption_notices = _unverified_apparatus_notices(row, experiment, assumed)
    assumed_notice = next(
        notice
        for notice in assumption_notices
        if notice.reason.startswith("effusion_regime_assumed:")
    )
    assert "molecular regime assumed" in assumed_notice.reason
    assert assumed_notice.band == "in_cell_partial_pressure_sum: pressure_limit_Pa=10"
    assert assumed_notice.authority.value == "extrapolated"
    assert any(
        notice.reason.startswith("calibration_not_grounded: kems_calibration_by_source:")
        for notice in assumption_notices
    )

    violated = GateOutcome(
        passed=True,
        checks=(
            GateCheck(
                "orifice_knudsen",
                False,
                {"knudsen_number_orifice": "1", "threshold": "10"},
            ),
            GateCheck(
                "effusion_regime_policy",
                True,
                {
                    "flag": "effusion_regime_violated",
                    "reason": "printed Kn is low",
                    "covered_checks": ("orifice_knudsen",),
                    "thresholds": {"orifice_knudsen": {"threshold": "10"}},
                    "authority": "extrapolated",
                },
            ),
        ),
    )
    reported_row = replace(
        row,
        evidence=replace(row.evidence, original_method_class="measured_direct"),
    )
    violation_notices = _unverified_apparatus_notices(
        reported_row, experiment, violated
    )
    violation_notice = next(
        notice
        for notice in violation_notices
        if notice.reason.startswith("effusion_regime_violated:")
    )
    assert "printed Kn is low" in violation_notice.reason
    assert violation_notice.band == "orifice_knudsen: threshold=10"
    assert violation_notice.authority.value == "extrapolated"

    reduced_row = replace(
        row,
        evidence=replace(
            row.evidence,
            class_=State.of(EvidenceClass.MEASURED_REDUCED),
            original_method_class="measured_reduced",
        ),
    )
    assert any(
        notice.reason.startswith("effusion_regime_assumed:")
        for notice in _unverified_apparatus_notices(reduced_row, experiment, assumed)
    )


def test_printed_kn_and_cell_sum_violations_keep_distinct_scorer_reasons() -> None:
    kn_experiment = F.kems_experiment(kn=Decimal("0.5"))
    kn_row = _pressure_row(kn_experiment.experiment_id, "printed-low-kn")
    kn_row = replace(
        kn_row,
        evidence=replace(kn_row.evidence, original_method_class="measured_direct"),
    )
    kn_gate = effusion_regime_unverified(
        kn_experiment, Quantity.P_PARTIAL, observation=kn_row
    )
    kn_notice = next(
        notice
        for notice in _unverified_apparatus_notices(kn_row, kn_experiment, kn_gate)
        if notice.reason.startswith("effusion_regime_violated:")
    )

    composition = Composition(
        basis="printed_mole_fraction",
        components=(("CaO", Decimal("0.25")), ("SiO2", Decimal("0.75"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    sum_experiment = F.kems_experiment(kn=None)
    sum_rows = []
    for species, pressure in (("O2", "6"), ("Ca", "1"), ("SiO", "5")):
        row = _pressure_row(
            sum_experiment.experiment_id,
            f"printed-high-sum-{species.lower()}",
            species=species,
            value=pressure,
        )
        identity = replace(
            row.identity,
            composition=State.of(composition),
            temperature_K=State.of(Decimal("1500")),
        )
        sum_rows.append(
            replace(
                row,
                identity=identity,
                evidence=replace(row.evidence, original_method_class="measured_direct"),
                point_conditions={
                    "temperature_K": F.located(Decimal("1500")),
                    "composition": F.located(composition),
                },
            )
        )
    sum_gate = effusion_regime_unverified(
        sum_experiment,
        Quantity.P_PARTIAL,
        observation=sum_rows[0],
        point_observations=tuple(sum_rows),
    )
    sum_notice = next(
        notice
        for notice in _unverified_apparatus_notices(
            sum_rows[0], sum_experiment, sum_gate
        )
        if notice.reason.startswith("effusion_regime_violated:")
    )
    sum_notices = _unverified_apparatus_notices(sum_rows[0], sum_experiment, sum_gate)

    assert "printed orifice Knudsen number is below" in kn_notice.reason
    assert "printed in-cell pressure sum exceeds" in sum_notice.reason
    assert "biased LOW" in kn_notice.reason
    assert "biased LOW" in sum_notice.reason
    assert kn_notice.reason != sum_notice.reason
    assert kn_notice.band != sum_notice.band
    assert sum_notices == (sum_notice,)


def test_demaria_conditional_o_calibration_predicts_with_located_default(
    published_pressure_rows,
) -> None:
    demaria, series = published_pressure_rows["kems-022-demaria-1971"]
    rows = series["demaria_1971_o_atomic_psat_table5"][2]
    assert len(rows) == 13
    context = ScoreContext(
        works=demaria.works,
        experiments=demaria.experiments,
        observations=demaria.observations,
        extract_review={"kems-022-demaria-1971": "reviewed"},
        root=ROOT,
    )
    for row in rows:
        experiment = demaria.experiments[row.experiment_id]
        gate = underdetermined_apparatus(
            experiment, Quantity.P_PARTIAL, observation=row
        )
        assert gate.passed
        check = next(item for item in gate.checks if item.name == "kems_calibration")
        assert check.detail["flag"] == "calibration_not_grounded"
        assert check.detail["calibration_record_status"] == (
            "conditional_procedure_unverified"
        )
        assert check.detail["calibration_default"] == "conditional_o_o2_applied"
        assert "used whenever possible" in check.detail["reason"]
        notices = _unverified_apparatus_notices(row, experiment, gate)
        calibration_notice = next(
            notice for notice in notices if "calibration_not_grounded:" in notice.reason
        )
        assert "used whenever possible" in calibration_notice.reason
        residual, _candidate = compile_residual(
            row,
            Engine.INTERNAL_ANALYTICAL,
            context=context,
            point_observations=tuple(context.observations.values()),
        )
        if residual.refusal is not None:
            assert residual.refusal.reason is not RefusalReason.UNDERDETERMINED_APPARATUS
        residual_notice = next(
            notice
            for notice in residual.notices
            if notice.reason.startswith("calibration_not_grounded:")
        )
        assert "conditional_o_o2_applied" in residual_notice.reason
        assert "used whenever possible" in residual_notice.reason


def test_probe_demaria_conditional_default_is_required(
    published_pressure_rows, monkeypatch
) -> None:
    demaria, series = published_pressure_rows["kems-022-demaria-1971"]
    rows = series["demaria_1971_o_atomic_psat_table5"][2]

    def conditional_contract():
        for row in rows:
            experiment = demaria.experiments[row.experiment_id]
            gate = underdetermined_apparatus(
                experiment, Quantity.P_PARTIAL, observation=row
            )
            assert gate.passed
            check = next(item for item in gate.checks if item.name == "kems_calibration")
            assert check.detail["calibration_default"] == "conditional_o_o2_applied"

    with monkeypatch.context() as patch:
        patch.setattr(validity, "_calibration_not_grounded_detail", lambda *_a, **_k: None)
        with pytest.raises(AssertionError):
            conditional_contract()


def test_current_only_observation_is_excluded_from_pressure_comparators() -> None:
    experiment = _scoped_experiment(
        {"standard@species=Na": _located_state("silver")}
    )
    first = _pressure_row(experiment.experiment_id, "pressure-1", value="10")
    second = _pressure_row(experiment.experiment_id, "pressure-2", value="20")
    current = F.observation(
        "current-only",
        experiment.experiment_id,
        replace(
            F.pref_identity(),
            quantity=Quantity.ION_INTENSITY,
            composition=F.activity_identity().composition,
        ),
        Decimal("1000"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="qms-test-source",
    )
    assert quantity_token(current.identity) is Quantity.ION_INTENSITY

    grouped = _partial_pressure_observations_by_experiment((first, second, current))
    assert grouped == {experiment.experiment_id: (first, second)}

    replicate_groups = _kems_replicate_groups(
        {row.observation_id: row for row in (first, second, current)},
        {experiment.experiment_id: experiment},
        {},
    )
    assert len(replicate_groups) == 1
    assert len(replicate_groups[0]) == 2


@pytest.fixture(scope="module")
def published_pressure_rows():
    migrated = {}
    for source, path, series_prefixes in (
        (
            "kems-022-demaria-1971",
            DEMARIA_EXTRACT,
            (
                "demaria_1971_al2o_psat_table4",
                "demaria_1971_alo_psat_table4",
                "demaria_1971_o_atomic_psat_table5",
            ),
        ),
        (
            "kems-007-costa-2015",
            COSTA_EXTRACT,
            ("costa_2015_fe_partial_pressure_ir_digitized",),
        ),
    ):
        source_migrator = migrate_one_extract(ROOT, path)
        source_migrator.finalize()
        extract = yaml.safe_load(path.read_text(encoding="utf-8"))
        series = {}
        for series_prefix in series_prefixes:
            raw_row = next(
                row
                for species in extract["species"].values()
                for row in species["observations"]
                if row.get("observation_id") == series_prefix
            )
            expected = {
                Decimal(str(point["T_K"])): (
                    atm_to_pa(Decimal(str(point["p_atm"])))
                    if point.get("p_atm") is not None
                    else Decimal(str(point["p_Pa"]))
                )
                for point in raw_row["values"]["points"]
            }
            actual = {
                observation.identity.temperature_K.value: observation.value.point
                for observation in source_migrator.result.observations.values()
                if observation.source_id == source
                and series_prefix in observation.observation_id
                and observation.identity.temperature_K is not None
                and observation.identity.temperature_K.is_value
                and observation.value.point is not None
                and quantity_token(observation.identity) is Quantity.P_PARTIAL
            }
            assert actual == expected
            rows = tuple(
                observation
                for observation in source_migrator.result.observations.values()
                if series_prefix in observation.observation_id
                and quantity_token(observation.identity) is Quantity.P_PARTIAL
            )
            series[series_prefix] = (expected, actual, rows)
        migrated[source] = (source_migrator.result, series)
    return migrated


def test_de_maria_binding_and_costa_migration_leave_published_pressures_unchanged(
    published_pressure_rows,
) -> None:
    demaria, demaria_series = published_pressure_rows["kems-022-demaria-1971"]
    demaria_count = 0
    for prefix, (expected, actual, demaria_rows) in demaria_series.items():
        assert len(demaria_rows) == len(expected)
        experiment = demaria.experiments[demaria_rows[0].experiment_id]
        assert experiment.apparatus is not None
        assert experiment.apparatus.calibration is not None
        before_binding = replace(
            experiment,
            apparatus=replace(experiment.apparatus, calibration=None),
        )
        assert all(
            _has_ungrounded_kems_partial_pressure_calibration(row, before_binding)
            for row in demaria_rows
        )
        grounded = prefix != "demaria_1971_o_atomic_psat_table5"
        assert all(
            _has_ungrounded_kems_partial_pressure_calibration(row, experiment)
            == (not grounded)
            for row in demaria_rows
        )
        assert {
            row.identity.temperature_K.value: row.value.point for row in demaria_rows
        } == expected
        assert actual == expected
        demaria_count += len(demaria_rows)

    assert demaria_count == sum(
        len(expected) for expected, _actual, _rows in demaria_series.values()
    )
    assert {
        prefix: len(rows) for prefix, (_expected, _actual, rows) in demaria_series.items()
    } == {
        "demaria_1971_al2o_psat_table4": 14,
        "demaria_1971_alo_psat_table4": 6,
        "demaria_1971_o_atomic_psat_table5": 13,
    }
    assert {
        prefix: sum(
            not _has_ungrounded_kems_partial_pressure_calibration(
                row, demaria.experiments[row.experiment_id]
            )
            for row in rows
        )
        for prefix, (_expected, _actual, rows) in demaria_series.items()
    } == {
        "demaria_1971_al2o_psat_table4": 14,
        "demaria_1971_alo_psat_table4": 6,
        "demaria_1971_o_atomic_psat_table5": 0,
    }

    costa, costa_series = published_pressure_rows["kems-007-costa-2015"]
    _costa_prefix, (costa_expected, costa_actual, costa_rows) = next(
        iter(costa_series.items())
    )
    assert len(costa_rows) == len(costa_expected)
    costa_experiment = costa.experiments[costa_rows[0].experiment_id]
    assert costa_experiment.apparatus is None or costa_experiment.apparatus.calibration is None
    assert all(
        _has_ungrounded_kems_partial_pressure_calibration(row, costa_experiment)
        for row in costa_rows
    )
    assert {row.identity.temperature_K.value: row.value.point for row in costa_rows} == costa_expected
    assert costa_actual == costa_expected


@pytest.mark.parametrize(
    ("factor_field", "factor"),
    (
        ("sensitivity_factor", Decimal("2")),
        ("cross_section_factor", Decimal("3")),
    ),
)
def test_calibration_factor_multiplication_fails_published_pressure_pin(
    published_pressure_rows, factor_field: str, factor: Decimal
) -> None:
    _result, demaria_series = published_pressure_rows["kems-022-demaria-1971"]
    expected, actual, _rows = next(iter(demaria_series.values()))
    temperature, published = next(iter(expected.items()))
    assert actual[temperature] == published
    mutated_pressure = actual[temperature] * factor
    with pytest.raises(AssertionError, match="published author pressure"):
        assert mutated_pressure == published, (
            f"{factor_field} must not be applied to published author pressure"
        )
