from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AdmissionStatus,
    AmountBasis,
    EvidenceClass,
    MethodToken,
    Quantity,
    RefusalReason,
)
from simulator.battery.migrate import (
    experiment_from_plain,
    iter_observation_store_paths,
    load_yaml,
    observation_from_plain,
)
from simulator.battery.records import (
    INITIAL_CHARGE_ONLY_PROXY_FLAG,
    Composition,
    Experiment,
    Located,
    Observation,
    State,
)
from simulator.battery.validity import (
    comparison_method_cell_constant_cancels,
    run_validity_gates,
    underdetermined_apparatus,
)
from simulator.source_ids import ZHANG_2021_ACSESC_SOURCE_ID
from tests.battery import factories as F


@pytest.mark.parametrize(
    ("calibration_field", "calibration_reason", "expected_record_status"),
    (
        ("standard", "not printed", "not_printed"),
        ("standard", "not_published", "not_published"),
        ("reference_substance", "not_published", "not_published"),
        ("calibration_substance", "not_published", "not_published"),
    ),
)
def test_uncalibrated_kems_partial_pressure_flags_unpublished_calibration(
    calibration_field: str,
    calibration_reason: str,
    expected_record_status: str,
) -> None:
    experiment = F.kems_experiment(calibrated=False)
    assert experiment.apparatus is not None
    experiment = replace(
        experiment,
        apparatus=replace(
            experiment.apparatus,
            calibration={
                calibration_field: Located(
                    State.unknown(calibration_reason), locator=F.loc(page=4)
                )
            },
        ),
    )

    outcome = underdetermined_apparatus(experiment, Quantity.P_PARTIAL)

    assert outcome.passed
    calibration_check = next(
        check for check in outcome.checks if check.name == "kems_calibration"
    )
    assert calibration_check.detail["flag"] == "calibration_not_grounded"
    assert (
        calibration_check.detail["calibration_record_status"]
        == expected_record_status
    )


def test_unpublished_auxiliary_without_printed_standard_is_flagged() -> None:
    experiment = F.kems_experiment(calibrated=False)
    assert experiment.apparatus is not None
    experiment = replace(
        experiment,
        apparatus=replace(
            experiment.apparatus,
            calibration={
                "multiplier_isotope_corrections": Located(
                    State.unknown("not_published"), locator=F.loc(page=4)
                )
            },
        ),
    )

    outcome = underdetermined_apparatus(experiment, Quantity.P_PARTIAL)

    assert outcome.passed
    calibration_check = next(
        check for check in outcome.checks if check.name == "kems_calibration"
    )
    assert calibration_check.detail["calibration_record_status"] == "not_published"
    assert calibration_check.detail["calibration_notice"] == (
        "The source record says the calibration standard was not published."
    )


@pytest.mark.parametrize(
    "pairing_kind",
    (None, "not_printed", "not_reported", "unknown", "same_cell"),
)
def test_comparison_pairing_cancellation_golden_pin(pairing_kind: str | None) -> None:
    provenance = {
        "comparison_method": {"kind": "comparison_ratio"},
        "common_knudsen_cell_constant": {"cancels": True},
        "melt_reference_pairing": {"kind": pairing_kind},
    }

    assert comparison_method_cell_constant_cancels(provenance) is True


@pytest.mark.parametrize("comparison_kind", ("ratio", "comparison_ratio"))
def test_comparison_ratio_kind_cancellation_golden_pin(
    comparison_kind: str,
) -> None:
    provenance = {
        "comparison_method": {"kind": comparison_kind},
        "common_knudsen_cell_constant": {"cancels": True},
        "melt_reference_pairing": {"kind": "same_cell"},
    }

    assert comparison_method_cell_constant_cancels(provenance) is True


@pytest.mark.parametrize(
    ("orifice_area", "clausing", "expected_missing", "expected_invalid"),
    (
        (None, None, {"orifice_area_m2", "clausing_factor"}, set()),
        (Decimal("-1"), None, {"clausing_factor"}, {"orifice_area_m2"}),
        (None, Decimal("2"), {"orifice_area_m2"}, {"clausing_factor"}),
    ),
)
def test_kems_activity_geometry_reports_absence_and_invalid_values_separately(
    orifice_area: Decimal | None,
    clausing: Decimal | None,
    expected_missing: set[str],
    expected_invalid: set[str],
) -> None:
    experiment = F.kems_experiment(
        orifice_area=orifice_area,
        clausing=clausing,
        kn=None,
        calibrated=True,
    )

    outcome = underdetermined_apparatus(experiment, Quantity.ACTIVITY)

    assert outcome.passed is False
    assert outcome.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    check = next(
        check for check in outcome.checks if check.name == "geometry_determinants"
    )
    assert set(check.detail["missing"]) == expected_missing
    assert set(check.detail.get("invalid", ())) == expected_invalid


def test_comparison_ratio_does_not_admit_activity_coefficient() -> None:
    provenance = {
        "comparison_method": {"kind": "comparison_ratio"},
        "common_knudsen_cell_constant": {"cancels": True},
        "melt_reference_pairing": {"kind": "same_effective_setup"},
    }
    experiment = F.kems_experiment(
        orifice_area=None, clausing=None, kn=None, calibrated=True
    )
    reference = replace(
        F.observation(
            "comparison-activity-coefficient",
            experiment.experiment_id,
            replace(F.activity_identity(), quantity=Quantity.ACTIVITY_COEFFICIENT),
            "0.2",
            evidence=EvidenceClass.MEASURED_DIRECT,
        ),
        provenance=provenance,
    )

    gate = underdetermined_apparatus(
        experiment, Quantity.ACTIVITY_COEFFICIENT, observation=reference
    )

    assert gate.passed is False
    assert gate.reason is RefusalReason.UNDERDETERMINED_APPARATUS
    assert gate.primary_check == "geometry_determinants"
    assert not any(
        check.name == "comparison_method_cell_constant_cancels" and check.passed
        for check in gate.checks
    )


@pytest.mark.parametrize(
    ("source_id", "has_row_area", "expected_pass"),
    (
        (ZHANG_2021_ACSESC_SOURCE_ID, False, True),
        (ZHANG_2021_ACSESC_SOURCE_ID, True, True),
        ("other-source", True, False),
    ),
)
def test_time_forward_mass_loss_defers_row_geometry_to_predictor(
    source_id: str, has_row_area: bool, expected_pass: bool,
) -> None:
    experiment = F.tabulation_experiment(
        method=MethodToken.LANGMUIR_FREE_EVAPORATION
    )
    identity = replace(
        F.activity_identity(), quantity=Quantity.MASS_LOSS_FRACTION
    )
    reference = F.observation(
        "time-forward-mass-loss",
        experiment.experiment_id,
        identity,
        "0.01",
        source_id=source_id,
    )
    if has_row_area:
        reference = replace(
            reference,
            point_conditions={
                "exposed_area_initial_m2": F.located(Decimal("1e-5"))
            },
        )

    outcome = underdetermined_apparatus(
        experiment, Quantity.MASS_LOSS_FRACTION, observation=reference
    )

    assert outcome.passed is expected_pass
    if not expected_pass:
        assert outcome.reason is RefusalReason.UNDERDETERMINED_APPARATUS


_VALIDITY_BUCKET_COUNT = 8


def _validity_observation_paths(root: Path) -> list[Path]:
    literature = root / "data" / "literature"
    return sorted(
        (
            path
            for directory in (
                literature / "extracts-v2",
                literature / "observations-v2",
            )
            for path in iter_observation_store_paths(directory)
        ),
        key=lambda path: path.relative_to(literature).as_posix(),
    )


def _validity_observation_buckets(root: Path) -> tuple[tuple[Path, ...], ...]:
    """Assign sorted observation shards to deterministic size-balanced buckets."""

    buckets: list[list[Path]] = [[] for _ in range(_VALIDITY_BUCKET_COUNT)]
    sizes = [0] * _VALIDITY_BUCKET_COUNT
    for path in _validity_observation_paths(root):
        bucket = min(
            range(_VALIDITY_BUCKET_COUNT), key=lambda index: (sizes[index], index)
        )
        buckets[bucket].append(path)
        sizes[bucket] += path.stat().st_size
    return tuple(tuple(bucket) for bucket in buckets)


def _validity_context_for_paths(
    root: Path, paths: tuple[Path, ...]
) -> tuple[dict[str, Experiment], dict[str, Observation]]:
    literature = root / "data" / "literature"
    experiments: dict[str, Experiment] = {}
    for path in sorted((literature / "works").glob("*.yaml")):
        if path.name == "ALIASES.yaml":
            continue
        document = load_yaml(path)
        if isinstance(document, Mapping):
            for raw_experiment in document.get("experiments") or ():
                experiment = experiment_from_plain(raw_experiment)
                experiments[experiment.experiment_id] = experiment

    observations: dict[str, Observation] = {}
    for path in paths:
        document = load_yaml(path)
        if not isinstance(document, Mapping):
            continue
        for raw_observation in document.get("observations") or ():
            observation = observation_from_plain(raw_observation)
            observations[observation.observation_id] = observation
    return experiments, observations


def test_kems_gate_treats_missing_point_conditions_as_empty() -> None:
    experiment = F.kems_experiment(
        orifice_area=None, clausing=None, kn=None, calibrated=False
    )
    identity = F.psat_identity("Na")
    observation = F.observation(
        "missing-point-conditions",
        experiment.experiment_id,
        identity,
        "1",
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.ADMITTED,
    )
    missing = replace(observation, point_conditions=None)
    empty = replace(observation, point_conditions={})

    missing_outcome = run_validity_gates(experiment, missing)
    empty_outcome = run_validity_gates(experiment, empty)

    assert missing_outcome == empty_outcome
    assert missing_outcome.passed is False
    assert missing_outcome.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED


def test_kems_apparatus_gates_apply_to_measured_rows() -> None:
    experiment = F.kems_experiment(
        experiment_id="kems-missing-apparatus",
        orifice_area=None,
        clausing=None,
        kn=None,
        calibrated=False,
    )
    observation = F.observation(
        "measured-row",
        experiment.experiment_id,
        F.psat_identity("Na"),
        "1",
        evidence=EvidenceClass.MEASURED_DIRECT,
    )

    outcome = run_validity_gates(experiment, observation)

    assert outcome.passed is False
    assert outcome.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
    assert any(
        check.name == "in_cell_partial_pressure_sum" and not check.passed
        for check in outcome.checks
    )
    assert any(
        check.name == "kems_calibration" and not check.passed
        for check in outcome.checks
    )


def test_initial_charge_proxy_cannot_verify_in_cell_pressure_sum() -> None:
    experiment = F.kems_experiment(
        experiment_id="kems-initial-charge-proxy",
        kn=None,
    )
    unflagged_starting_charge = Composition(
        basis="printed_mole_fraction",
        components=(("CaO", Decimal("0.25")), ("SiO2", Decimal("0.75"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    experiment = replace(
        experiment,
        sample=replace(
            experiment.sample,
            initial_composition=Located(
                State.of(unflagged_starting_charge), locator=F.loc(page=1)
            ),
        ),
    )
    proxy = Composition(
        basis="printed_mole_fraction",
        components=(("CaO", Decimal("0.25")), ("SiO2", Decimal("0.75"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
        proxy_flag=INITIAL_CHARGE_ONLY_PROXY_FLAG,
    )
    identity = replace(
        F.psat_identity("Na"),
        quantity=Quantity.P_PARTIAL,
        composition=State.of(proxy),
        reservoir=State.not_applicable("mixture partial pressure"),
    )
    observation = F.observation(
        "initial-charge-proxy-pressure",
        experiment.experiment_id,
        identity,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    observation = replace(
        observation,
        point_conditions={
            **(observation.point_conditions or {}),
            "composition": Located(State.of(proxy), locator=F.loc(page=1)),
        },
    )

    outcome = run_validity_gates(
        experiment,
        observation,
        point_observations=(observation,),
    )

    pressure_sum = next(
        check
        for check in outcome.checks
        if check.name == "in_cell_partial_pressure_sum"
    )
    assert pressure_sum.passed is False
    assert pressure_sum.detail["reason"] == (
        "composition is unknown; printed species coverage cannot be established"
    )


def test_kems_apparatus_gates_do_not_apply_to_model_derived_rows() -> None:
    experiment = F.kems_experiment(
        experiment_id="kems-missing-apparatus",
        orifice_area=None,
        clausing=None,
        kn=None,
        calibrated=False,
    )
    observation = F.observation(
        "model-derived-row",
        experiment.experiment_id,
        F.psat_identity("Na"),
        "1",
        evidence=EvidenceClass.MODEL_DERIVED,
    )

    outcome = run_validity_gates(experiment, observation)

    assert outcome.passed is True
    assert any(
        check.name == "apparatus_applicability"
        and check.passed
        and check.detail["reason"]
        == "apparatus gate not applicable: non-measured evidence"
        for check in outcome.checks
    )
    assert not any(
        check.name in {"in_cell_partial_pressure_sum", "kems_calibration"}
        for check in outcome.checks
    )


def test_validity_observation_buckets_partition_store() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = _validity_observation_paths(root)
    buckets = _validity_observation_buckets(root)
    flattened = [path for bucket in buckets for path in bucket]

    assert len(buckets) == _VALIDITY_BUCKET_COUNT
    assert sorted(flattened) == paths
    assert len(flattened) == len(set(flattened))


@pytest.mark.parametrize("bucket_index", range(_VALIDITY_BUCKET_COUNT))
def test_full_store_validity_gates_do_not_raise(bucket_index: int) -> None:
    root = Path(__file__).resolve().parents[2]
    paths = _validity_observation_buckets(root)[bucket_index]
    experiments, observations = _validity_context_for_paths(root, paths)

    failures: list[str] = []
    for observation in observations.values():
        try:
            experiment = experiments[observation.experiment_id]
            run_validity_gates(experiment, observation)
        except Exception as exc:
            failures.append(f"{observation.observation_id}: {exc}")
    assert not failures, "\n".join(failures)
