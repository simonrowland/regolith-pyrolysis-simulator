"""Report contracts, using synthetic observations rather than engine goldens."""
import json

import pytest

from scripts.calibration_battery import (
    CERTIFIED_DERIVABLE, envelope, headline, headline_notes, score_table,
    scored_value_twins, summarize,
)


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


@pytest.mark.parametrize("markers", [
    *[{key: role} for key in ("class", "class_tag", "scientific_class")
      for role in ("model", "derived", "inverse", "figure_only", "compilation_only")],
    {"admission_status": "model_output_not_measurement"},
    {"admission_status": "rejected_model_output_not_measurement"},
    {"method_class": "model_derived"},
    {"method_class": "derived_gibbs_duhem"},
    {"method_class": "secondary_compilation"},
])
@pytest.mark.parametrize("nested", [False, True])
def test_extract_roles_cannot_select_or_score_numeric_records(extract_comparator, markers, nested):
    from scripts.calibration_battery import extract_rows
    obs = extract_comparator("match")
    (obs.values if nested else obs.admission_metadata).update(markers)
    # Even a comparator returning numbers cannot bypass source-role admission.
    rows = extract_rows()
    assert len(rows) == 2
    assert all(not row["selected"] and not row["score_eligible"] for row in rows)
    assert all(set(row["signed_residual"].values()) == {None} for row in rows)


def test_superseded_extract_is_outside_selected_denominator(extract_comparator, monkeypatch):
    from dataclasses import replace
    from scripts.calibration_battery import extract_rows
    from simulator.diagnostic_helpers import extract_reproduction as e
    obs = replace(extract_comparator("match"), adoption_basis="superseded")
    monkeypatch.setattr(e, "load_adopted_observations", lambda: [obs])
    rows = extract_rows()
    assert rows and all(not row["selected"] and not row["score_eligible"] for row in rows)


@pytest.mark.parametrize("key", ["class", "class_tag", "scientific_class", "evidence_kind",
                                  "admission_status", "measurement_status", "status",
                                  "alpha_role", "method_class"])
@pytest.mark.parametrize("nested", [False, True])
def test_unknown_evidence_marker_never_scores(extract_comparator, key, nested):
    from scripts.calibration_battery import extract_rows, finalize_rows
    from simulator.diagnostic_helpers import extract_reproduction as e
    evaluate = e.evaluate_observation
    obs = extract_comparator("match")
    token = "novel_evidence_b481"
    (obs.values if nested else obs.admission_metadata)[key] = token
    reason = f"unknown_evidence_marker:{key}={token}"
    assert e.observation_admission_reason(obs) == reason
    result = evaluate(obs)
    assert result.skip_reason == f"typed-refusal:{reason}"
    assert not any(r.status in {"match", "mismatch"} for r in result.records)
    # The fixture returns numeric comparator matches to test the battery gate independently.
    rows = finalize_rows(extract_rows())
    assert rows and all(not r["selected"] and not r["score_eligible"] for r in rows)
    assert all(r["terminal_bucket"] != "scored" for r in rows)
    assert all(set(r["signed_residual"].values()) == {None} for r in rows)


def test_equal_measurements_without_supersession_remain_independent(extract_comparator, monkeypatch):
    from dataclasses import replace
    from scripts.calibration_battery import extract_rows
    from simulator.diagnostic_helpers import extract_reproduction as e
    first = extract_comparator("match")
    second = replace(first, observation_id="independent_class_measurement")
    original_evaluate = e.evaluate_observation
    def evaluate(obs, **kwargs):
        result = original_evaluate(obs, **kwargs)
        records = [replace(r, case_id=obs.case_id,
                           observable_id=r.observable_id.replace(first.observation_id, obs.observation_id))
                   for r in result.records]
        return e.ObservationEvaluation(obs, records=records)
    monkeypatch.setattr(e, "evaluate_observation", evaluate)
    monkeypatch.setattr(e, "load_adopted_observations", lambda: [first, second])
    rows = extract_rows()
    assert len(rows) == 4
    assert all(row["selected"] and row["score_eligible"] for row in rows)
    assert {row["source_observation_id"] for row in rows} == {
        first.observation_id, second.observation_id,
    }


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


def test_selected_equals_scored_plus_refused_plus_excluded(observation):
    rows = [
        envelope(**observation),
        envelope(**{**observation, "observation_id": "train", "status": "self-agreement-excluded"}),
        envelope(**{**observation, "observation_id": "refused", "status": "refused", "predicted": 0.0}),
        envelope(**{**observation, "observation_id": "out", "selected": False}),
        envelope(**{**observation, "observation_id": "melt", "rail": "melt activities",
                    "observable": "activity_coefficient", "units": "dimensionless"}),
        envelope(**{**observation, "observation_id": "melt-train", "rail": "melt activities",
                    "observable": "activity_coefficient", "units": "dimensionless",
                    "status": "self-agreement-excluded"}),
    ]
    report = summarize(rows)
    for rail in report["coverage"]:
        assert rail["N"] == rail["N_scored"] + rail["N_refused"] + rail["N_excluded"]
        assert rail["authorities_population"] == "scored"
    vapour = next(r for r in report["coverage"] if r["rail"] == "vapour")
    melt = next(r for r in report["coverage"] if r["rail"] == "melt activities")
    assert vapour["N"] == 3 and vapour["N_scored"] == 1 and vapour["N_refused"] == 1 and vapour["N_excluded"] == 1
    assert melt["N"] == 2 and melt["N_scored"] == 1 and melt["N_excluded"] == 1
    assert vapour["exclusion_reasons"] == {"self-agreement-excluded": 1}


def test_headline_authority_columns_count_scored_population(observation):
    rows = [
        envelope(**observation),
        envelope(**{**observation, "observation_id": "train", "status": "self-agreement-excluded"}),
        envelope(**{**observation, "observation_id": "refused", "status": "unsupported-observable", "predicted": None}),
    ]
    report = summarize(rows)
    vapour = next(r for r in report["coverage"] if r["rail"] == "vapour")
    assert vapour["authorities"]["bridge"] == 2
    assert vapour["authorities_scored"]["bridge"] == 1
    assert vapour["authorities_scored"]["certified"] == 0
    text = headline(report)
    assert "Bridge (scored)" in text
    assert "Certified (scored; not yet derivable)" in text
    vapour_line = next(line for line in text.splitlines() if line.startswith("| vapour |"))
    assert "| 3 | 1 | 1 | 1 (self-agreement-excluded:1) | 0 | 1 | 0 |" in vapour_line


def test_hashimoto_alpha_rate_twins_are_not_double_scored(observation):
    shared = {**observation, "dataset_id": "fedkin-grossman-ghiorso-2006", "species": "Fe",
              "measured": 0.23, "predicted": 0.02, "units": "alpha",
              "conditions": {"temperature_K": 1973.0}}
    alpha = envelope(**{**shared, "observation_id": "fedkin_2006_table3_fe_hashimoto_langmuir:T=1973",
                        "observable": "evaporation_alpha"})
    twin = envelope(**{**shared, "observation_id": "fedkin_2006_table3_fe_hashimoto_langmuir_per_T_alpha_series:T=1973",
                       "observable": "evaporation_rate"})
    flux = envelope(**{**shared, "observation_id": "measured_flux", "observable": "evaporation_rate",
                       "units": "mol m^-2 s^-1", "measured": 1e-4, "predicted": 2e-4})
    report = summarize([alpha, twin, flux])
    vapour = next(r for r in report["coverage"] if r["rail"] == "vapour")
    assert vapour["N"] == 2 and vapour["N_scored"] == 2 and vapour["N_excluded"] == 0
    assert twin["selected"] is False and twin["score_eligible"] is False
    assert twin["duplicate_of"] == alpha["observation_id"]
    assert alpha["score_eligible"] is True and flux["score_eligible"] is True
    rate_alpha = [s for s in report["scores"]
                  if s["observable"] == "evaporation_rate" and s["units"] == "alpha" and s["N_scored"]]
    assert rate_alpha == []
    rate_flux = [s for s in report["scores"]
                 if s["observable"] == "evaporation_rate" and s["units"] == "mol m^-2 s^-1" and s["N_scored"]]
    assert len(rate_flux) == 1 and rate_flux[0]["N_scored"] == 1


def test_cited_validated_row_does_not_become_certified(observation):
    row = envelope(**{**observation, "raw": {
        "evidence_class": "CITED", "verdict": "validated",
        "certification_scope": "pure-component Antoine NBP identity"}})
    assert row["authority"] == "bridge"
    assert CERTIFIED_DERIVABLE is False
    report = summarize([row])
    vapour = next(r for r in report["coverage"] if r["rail"] == "vapour")
    assert vapour["certified_derivable"] is False
    assert vapour["authorities_scored"]["certified"] == 0
    assert vapour["authorities_scored"]["bridge"] == 1
    notes = "\n".join(headline_notes(report))
    assert "not yet derivable" in notes
    assert "structurally zero" in notes


def test_vapour_species_coverage_excludes_sio_evolution_rail(observation):
    vapour_sio = envelope(**{**observation, "species": "SiO", "observation_id": "vp-sio"})
    evo = envelope(**{**observation, "species": "SiO", "rail": "SiO evolution", "observation_id": "evo-sio",
                      "observable": "evaporation_alpha", "units": "alpha", "status": "out-of-domain"})
    unselected = envelope(**{**observation, "species": "Gd", "selected": False, "observation_id": "gd"})
    report = summarize([vapour_sio, evo, unselected])
    assert [s["species"] for s in report["vapour_by_species"]] == ["SiO"]
    assert report["vapour_by_species"][0]["N"] == 1
    assert [s["species"] for s in report["sio_evolution_by_species"]] == ["SiO"]
    assert report["sio_evolution_by_species"][0]["N"] == 1
    assert report["sio_evolution_by_species"][0]["N_scored"] == 1
    text = headline(report)
    assert "builtin-antoine" not in text


def test_fallback_engine_is_named_on_headline_and_scores(observation):
    row = envelope(**{**observation, "dataset_id": "vp30", "raw": {
        "engine": "builtin-antoine",
        "vaporock_error": "VapoRock: No module named 'VapoRock'; LiquidMelts missing",
        "observed_Pa": 100.0}})
    extract = envelope(**{**observation, "observation_id": "kems-row",
                          "dataset_id": "kems-005-fedkin-2006",
                          "conditions": {"temperature_K": 1600.0}})
    report = summarize([row, extract])
    vapour = next(r for r in report["coverage"] if r["rail"] == "vapour")
    assert vapour["marker"] == "builtin-antoine fallback"
    assert vapour["engine_fallback_N_scored"] == 1
    assert vapour["engines_scored"]["builtin-antoine (fallback; VapoRock unavailable)"] == 1
    assert vapour["engines_scored"]["extract/KEMS"] == 1
    text = headline(report)
    assert "builtin-antoine fallback" in text
    notes = "\n".join(headline_notes(report))
    assert "VapoRock" in notes and "ThermoEngine" in notes and "LiquidMelts" in notes
    assert "builtin-antoine fallback" in notes
    table = score_table(report)
    assert "builtin-antoine (fallback; VapoRock unavailable)" in table
    assert "extract/KEMS" in table


def test_cross_ingest_physical_point_twins_are_not_double_scored(observation):
    rows = []
    for temperature, measured in ((1973.0, 0.23), (2273.0, 0.23), (2173.0, 0.24)):
        shared = dict(species="Fe", observable="evaporation_alpha", units="alpha",
                      measured=measured, predicted=0.02,
                      conditions={"temperature_K": temperature})
        rows.append(envelope(**{**observation, **shared,
            "observation_id": f"fedkin_2006_table3_fe_hashimoto_langmuir:T={int(temperature)}",
            "dataset_id": "fedkin-grossman-ghiorso-2006", "evidence": "direct experiment"}))
        rows.append(envelope(**{**observation, **shared,
            "observation_id": f"fedkin_2006_fe_hashimoto_langmuir_table3:T={int(temperature)}",
            "dataset_id": "kems-005-fedkin-2006", "evidence": "derived measurement"}))
    class_b1 = envelope(**{**observation, "species": "Fe", "observable": "evaporation_alpha",
        "units": "alpha", "measured": 0.24, "predicted": 0.02,
        "conditions": {"temperature_K": 2123.0},
        "observation_id": "fedkin_2006_fe_class_b1:T=2123",
        "dataset_id": "kems-005-fedkin-2006", "evidence": "derived measurement"})
    rows.append(class_b1)
    richter = []
    for temperature, measured in ((1873.0, 0.060358617843786475), (2023.0, 0.10738819534888526),
                                  (2173.0, 0.1764531077711509)):
        shared = dict(species="Mg", observable="evaporation_alpha", units="alpha",
                      measured=measured, predicted=0.2, evidence="literature correlation",
                      conditions={"temperature_K": temperature})
        kems = envelope(**{**observation, **shared,
            "observation_id": f"richter_2007_mg_cai_langmuir_alpha_arrhenius:T={int(temperature)}",
            "dataset_id": "kems-010-richter-2007"})
        extract = envelope(**{**observation, **shared,
            "observation_id": f"richter_2007_mg_cai_arrhenius_langmuir:T={int(temperature)}",
            "dataset_id": "richter-et-al-2007"})
        richter.extend([kems, extract])
        rows.extend([kems, extract])
    flux = envelope(**{**observation, "species": "Mg", "observable": "evaporation_rate",
        "units": "mol m^-2 s^-1", "measured": 1e-4, "predicted": 2e-4,
        "observation_id": "richter_geometry_flux", "dataset_id": "richter-et-al-2007"})
    rows.append(flux)
    sio_extract = envelope(**{**observation, "species": "SiO", "rail": "SiO evolution",
        "observable": "evaporation_alpha", "units": "alpha", "measured": 0.12, "predicted": 0.08,
        "conditions": {"temperature_K": 1973.0},
        "observation_id": "fedkin_2006_table3_sio_hashimoto_langmuir:T=1973",
        "dataset_id": "fedkin-grossman-ghiorso-2006", "evidence": "direct experiment"})
    sio_kems = envelope(**{**observation, "species": "SiO", "rail": "SiO evolution",
        "observable": "evaporation_alpha", "units": "alpha", "measured": 0.12, "predicted": 0.08,
        "conditions": {"temperature_K": 1973.0},
        "observation_id": "fedkin_2006_sio_hashimoto_table3_complete_b1:T=1973",
        "dataset_id": "kems-005-fedkin-2006", "evidence": "derived measurement"})
    rows.extend([sio_extract, sio_kems])

    report = summarize(rows)
    assert scored_value_twins(rows) == {}
    vapour = next(r for r in report["coverage"] if r["rail"] == "vapour")
    sio = next(r for r in report["coverage"] if r["rail"] == "SiO evolution")
    assert vapour["N"] == vapour["N_scored"] + vapour["N_refused"] + vapour["N_excluded"]
    assert sio["N"] == sio["N_scored"] + sio["N_refused"] + sio["N_excluded"]
    assert vapour["N_scored"] == 8 and sio["N_scored"] == 1

    scored_fe = [r for r in rows if r["score_eligible"] and r["species"] == "Fe"]
    fe_023 = {(r["conditions"]["temperature_K"], r["dataset_id"]) for r in scored_fe if r["measured"] == 0.23}
    assert fe_023 == {(1973.0, "fedkin-grossman-ghiorso-2006"), (2273.0, "fedkin-grossman-ghiorso-2006")}
    assert class_b1["score_eligible"] is True and class_b1["selected"] is True
    hashimoto_2173 = next(r for r in rows if r["observation_id"].endswith("langmuir:T=2173")
                          and r["dataset_id"] == "fedkin-grossman-ghiorso-2006")
    assert hashimoto_2173["score_eligible"] is True
    kems_hashimoto = [r for r in rows if r["dataset_id"] == "kems-005-fedkin-2006"
                      and "class_b1" not in r["observation_id"]]
    assert all(r["selected"] is False and r["duplicate_of"] for r in kems_hashimoto)
    assert all(r["duplicate_of"].startswith("fedkin_2006_table3_") for r in kems_hashimoto)
    assert "direct experiment beats derived measurement" in kems_hashimoto[0]["notices"][-1]

    for kems, extract in zip(richter[0::2], richter[1::2]):
        assert extract["score_eligible"] is True and kems["selected"] is False
        assert kems["duplicate_of"] == extract["observation_id"]
        assert "primary extract beats compilation" in kems["notices"][-1]
    assert flux["score_eligible"] is True
    assert sio_extract["score_eligible"] is True and sio_kems["selected"] is False
    assert sio_kems["duplicate_of"] == sio_extract["observation_id"]
    notes = "\n".join(headline_notes(report))
    assert "Direct experiment beats derived measurement" in notes
    assert "primary extract beats a compilation" in notes
    assert "never averaged" in notes
    fe_direct = [s for s in report["scores"] if s["species"] == "Fe" and s["measurement_kind"] == "direct experiment"
                 and s["N_scored"]]
    fe_derived = [s for s in report["scores"] if s["species"] == "Fe" and s["measurement_kind"] == "derived measurement"
                  and s["N_scored"]]
    assert len(fe_direct) == 1 and fe_direct[0]["N_scored"] == 3
    assert len(fe_derived) == 1 and fe_derived[0]["N_scored"] == 1
