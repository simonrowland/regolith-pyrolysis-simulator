"""Report contracts, using synthetic observations rather than engine goldens."""
import json

import pytest

from scripts.calibration_battery import envelope, summarize


@pytest.fixture
def observation():
    return dict(dataset_id="experiment-1", observation_id="row-1", species="Na",
                observable="partial_pressure", units="Pa", measured=10.0,
                predicted=100.0, status="match", conditions={"temperature_K": 1500.0},
                raw={"provider_status": "ok"}, source_doi="fixture:independent",
                uncertainty={"kind": "relative_fraction", "value": 0.1})


def test_envelope_schema_round_trips(observation):
    row = envelope(**observation)
    decoded = json.loads(json.dumps(row, allow_nan=False))
    assert decoded == row
    assert decoded["signed_residual"] == {"absolute": 90.0, "relative": 9.0, "dex": 1.0}
    assert decoded["conditions"]["temperature_K"] == 1500.0


@pytest.mark.parametrize("status", ["refused", "failed-to-run", "unsupported-observable", "unsupported-speciation"])
def test_refused_comparator_has_no_numeric_residual(observation, status):
    row = envelope(**{**observation, "status": status, "predicted": 0.0})
    assert row["authority"] == "refused"
    assert row["predicted"] is None
    assert row["raw_prediction"] == 0.0
    assert set(row["signed_residual"].values()) == {None}
    assert not row["score_eligible"]


def test_aggregation_never_averages_authorities(observation):
    rows = [envelope(**{**observation, "predicted": predicted}, authority=authority)
            for authority, predicted in [("certified", 10.0), ("bridge", 100.0), ("extrapolated", 1000.0)]]
    rows.append(envelope(**{**observation, "status": "refused", "predicted": 0.0}))
    report = summarize(rows)
    scores = {r["authority"]: r for r in report["scores"]}
    assert {a: s["metrics"]["dex"]["RMSE"] for a, s in scores.items()} == {
        "certified": 0.0, "bridge": 1.0, "extrapolated": 2.0, "refused": None}
    assert all(s["N_selected"] == 1 for s in scores.values())
    coverage = next(r for r in report["coverage"] if r["rail"] == "vapour")
    assert coverage["N"] == 4
    assert coverage["refused_fraction"] == 0.25


def test_zero_uncertainty_placeholder_is_missing_and_true_zero_is_not_refusal(observation):
    row = envelope(**{**observation, "predicted": 0.0,
        "uncertainty": {"kind": "absolute", "value": 0, "reported_status": "not_reported"}})
    assert row["uncertainty"]["status"] == "missing"
    assert row["authority"] == "bridge"
    assert row["signed_residual"]["relative"] == -1
    score = next(s for s in summarize([row])["scores"] if s["authority"] == "bridge")
    assert score["metrics"]["dex"]["RMSE"] == "infinite"
    assert score["metrics"]["dex"]["zero_prediction_count"] == 1


def test_measurement_kinds_units_and_splits_stay_separate(observation):
    rows = [envelope(**observation), envelope(**observation, evidence="model reference"),
            envelope(**observation, split="holdout"), envelope(**{**observation, "units": "K"})]
    scores = [s for s in summarize(rows)["scores"] if s["N_selected"]]
    assert len(scores) == 4
    assert all(s["N_selected"] == 1 for s in scores)


def test_inadmissible_candidate_is_visible_outside_selected_denominator(observation):
    row = envelope(**observation, selected=False)
    assert row["signed_residual"]["dex"] == 1.0
    assert not row["score_eligible"]
    rail = next(r for r in summarize([row])["coverage"] if r["rail"] == "vapour")
    assert rail["N"] == 0 and rail["outside_selected_N"] == 1
    assert rail["refused_fraction"] is None


@pytest.mark.parametrize("status", ["ordering-pass", "ordering-fail", "out-of-domain"])
def test_ordering_pair_counts_are_categorical_not_measurements(observation, status):
    row = envelope(**{**observation, "status": status, "measured": 3, "predicted": 2, "units": "ordering_pairs"})
    assert row["categorical_outcome"] == status
    assert set(row["signed_residual"].values()) == {None}
    assert not row["score_eligible"]
    assert not row["selected"]


@pytest.fixture
def extract_comparator(monkeypatch):
    from simulator.diagnostic_helpers import extract_reproduction as e
    from simulator.diagnostic_helpers.reproduction_compare import compare_values
    obs = e.AdoptedObservation(species_id="Fe", source_id="fixture", observation_id="measured_alpha",
        obs_type="alpha", review_status="reviewed", phase="solid", regime="vacuum", standard_state=None,
        T_range_K=[1500, 1600], units="alpha", uncertainty={"kind": "absolute", "value": 0.1},
        locator="Table 1", values={}, equipment={}, disagreement_dex=None, is_priority_winner=True,
        geometry_assumption="no geometry needed")

    def install(status, extrapolated=False):
        records = [compare_values(case_id=obs.case_id, source_id=obs.source_id,
            observable_id=f"measured_alpha:T={t}", species="Fe", coordinate={"temperature_K": t},
            expected_value=0.1, expected_uncertainty=obs.uncertainty, actual_value=0.2,
            units="alpha", evidence_scope="extract-store-adopted", source_locator="Table 1",
            recipe={}, observation={}, runtime={}, status_override=status) for t in (1500, 1600)]
        monkeypatch.setattr(e, "load_adopted_observations", lambda: [obs])
        monkeypatch.setattr(e, "load_vapor_pressure_data", lambda: {"fixture": True})
        monkeypatch.setattr(e, "evaluate_observation", lambda *a, **k: e.ObservationEvaluation(obs, records=records))
        monkeypatch.setattr(e, "_engine_alpha", lambda *a: (0.2, None, {"alpha_s_extrapolated": extrapolated}))
        return obs
    return install


def test_material_incompatibility_preserves_raw_alpha_without_scoring(extract_comparator):
    from scripts.calibration_battery import extract_rows
    extract_comparator("out-of-domain")
    rows = extract_rows()
    assert len(rows) == 2
    assert all(r["authority"] == "refused" and r["raw_prediction"] == 0.2 for r in rows)
    assert all(r["signed_residual"]["dex"] is None for r in rows)


def test_alpha_domain_flag_and_quantity_aggregation_survive_adapter(extract_comparator):
    from scripts.calibration_battery import extract_rows
    extract_comparator("match", extrapolated=True)
    rows = extract_rows()
    scores = [r for r in summarize(rows)["scores"] if r["N_selected"]]
    assert len(scores) == 1
    assert scores[0]["authority"] == "extrapolated"
    assert scores[0]["N_scored"] == 2
    assert scores[0]["metrics"]["dex"]["RMSE"] == pytest.approx(0.3010299956639812)


def test_failed_harness_is_refused_with_error_and_backlog():
    from scripts.calibration_battery import failure_row
    row = failure_row("sso-smoke", {"exit_code": 1, "command": "fixture command", "error": "Mg wall refusal"}, "redox")
    assert row["comparator_status"] == "failed-to-run"
    assert row["authority"] == "refused" and row["predicted"] is None
    assert "Mg wall refusal" in row["notices"]
    assert row["closure"]["projects"]


def test_measured_zero_absolute_error_is_visible_in_markdown(observation):
    from scripts.calibration_battery import score_table
    row = envelope(**{**observation, "measured": 0.0, "predicted": 2.0})
    assert "1 / 2 (absolute Pa)" in score_table(summarize([row]))


def test_kems_failure_preserves_each_registered_target(monkeypatch):
    from simulator.diagnostic_helpers import kems
    from scripts.calibration_battery import kems_rows
    def fail(*args, **kwargs):
        raise RuntimeError("fixture provider unavailable")
    monkeypatch.setattr(kems.KEMSAdapter, "evaluate", fail)
    rows = kems_rows()
    assert len(rows) == 6
    assert len({r["dataset_id"] for r in rows}) == 2
    assert all(r["authority"] == "refused" and r["comparator_status"] == "failed-to-run" for r in rows)
    assert all("RuntimeError: fixture provider unavailable" in r["notices"] for r in rows)
