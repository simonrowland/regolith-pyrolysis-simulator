"""Phase A contracts; paper values are independent of engine replay."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import NoticeKind, UncertaintyKind, ValueKind
from simulator.battery.migrate import load_migrated_store, to_plain
from simulator.battery.records import Notice, State, Uncertainty, Value
from simulator.battery.reproduction_cards import (
    compare_point,
    evaluate_card,
    group_gate,
    load_review_scope,
    resolve_path,
    write_study,
    write_studies,
)
from simulator.battery.score import NORMAL_95_TICKET_MULTIPLIER


ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    "kems-015-hashimoto-1983",
    "kems-012-sossi-2019",
    "kems-026-markova-1984",
    "kems-010-richter-2007",
)


@pytest.fixture(scope="module")
def store():
    return load_migrated_store(ROOT, sources=SOURCES)


@pytest.fixture(scope="module")
def paper_point(store):
    # Hashimoto Table 3, physical p.6 / folio 116, C5(1), FeO = 22.74 wt%.
    return next(
        obs for obs in store[2].values()
        if "residue_composition_series_quoted__run_17c5-1::" in obs.observation_id
        and obs.identity.species.formula == "FeO"
    )


@pytest.mark.parametrize("confidence, factor", [
    ("one_sigma", NORMAL_95_TICKET_MULTIPLIER),
    ("two_sigma", Decimal(1)),
    ("ci95", Decimal(1)),
    ("not_stated", Decimal(1)),
])
def test_shared_scalar_policy(paper_point, confidence, factor):
    # Widths are comparator fixtures, never assertions about Hashimoto's errors.
    obs = replace(paper_point, uncertainty=Uncertainty(
        kind=UncertaintyKind.PRINTED,
        verbatim={"value": "1", "unit": "wt%", "confidence_level": confidence},
    ))
    row = compare_point(obs, Decimal("23.74"))
    assert row["band_refusal"] is None
    assert row["status"] == "MATCH"
    assert Decimal(row["band"]["value"]) == factor
    assert row["native_inside"] is True
    if confidence == "not_stated":
        assert row["band"]["rule"].startswith("printed_unstated:")
    if confidence == "one_sigma":
        wider = compare_point(obs, Decimal("24.24"))
        assert wider["status"] == "MATCH"
        assert wider["native_inside"] is False


def test_range_uncertainty_and_no_band(paper_point):
    point = replace(paper_point, uncertainty=Uncertainty(
        kind=UncertaintyKind.PRINTED,
        verbatim={"value": ["0", "2"], "unit": "wt%", "confidence_level": "range_half_width"},
    ))
    assert compare_point(point, Decimal("23.74"))["status"] == "MATCH"
    point = replace(point, uncertainty=Uncertainty(kind=UncertaintyKind.NONE))
    row = compare_point(point, Decimal("23.74"))
    assert row["status"] == "NO_BAND"
    assert row["residual"] == "1.00"
    assert row["band"] is None


def test_zero_width_and_interval_are_not_manufactured_bands(paper_point):
    point = replace(paper_point, uncertainty=Uncertainty(
        kind=UncertaintyKind.PRINTED,
        verbatim={"value": "0", "unit": "wt%", "confidence_level": "two_sigma"},
    ))
    assert compare_point(point, Decimal("22.74"))["status"] == "MATCH"
    assert compare_point(point, Decimal("22.74000001"))["status"] == "MISMATCH"
    interval = replace(point, value=Value(kind=ValueKind.INTERVAL,
                                        interval_low=Decimal(22), interval_high=Decimal(23)))
    row = compare_point(interval, Decimal("22.74"))
    assert row["status"] == "MISSING"
    assert row["reason"] == "observed_mass_loss_interval_not_point"


@pytest.mark.parametrize("candidate", [None, Decimal("NaN"), Decimal("Infinity")])
def test_missing_or_nonfinite_never_matches(paper_point, candidate):
    assert compare_point(paper_point, candidate)["status"] == "MISSING"


@pytest.mark.parametrize("source, count", zip(SOURCES, (31, 44, 2, 51)))
def test_real_inventory_and_foreign_keys(store, source, count):
    path = ROOT / "data/literature/reproduction-cards" / f"{source}.yaml"
    manifest = yaml.safe_load(path.read_text())
    review = yaml.safe_load((path.parent / manifest["review_ref"]).read_text())
    scope = load_review_scope(ROOT, manifest, store=store)
    assert len(manifest["runs"]) == count
    assert {run["card_id"] for run in manifest["runs"]} == set(review["runs"])
    for run in manifest["runs"]:
        frozen = review["runs"][run["card_id"]]
        assert frozen["frozen_run"] == run
        for experiment_id in run["experiment_ids"]:
            assert scope["experiments"][experiment_id]["work_id"] == manifest["work_id"]
        ids = [oid for group in run["outcomes"] for oid in group["observation_ids"]]
        assert len(ids) == len(set(ids))
        assert all(oid in store[2] for oid in ids)
        entries = frozen["entries"]
        assert all(resolve_path(scope, entry["ref"]) == entry["resolved"] for entry in entries)
        for eid in run["experiment_ids"]:
            selected_paths = {tuple(entry["ref"][2:]) for entry in entries
                              if entry["ref"][:2] == ["experiments", eid]}
            assert {("sample", "initial_composition"), ("sample", "mass_kg"),
                    ("sample", "form"), ("sample", "container"),
                    ("sample", "surface_area_m2"), ("thermal_schedule",),
                    ("pressure_environment",), ("apparatus",)} <= selected_paths
        receipt = evaluate_card(manifest, run, frozen, scope, store[2])
        assert receipt["verdict"] == "NOT_PASS"
        assert receipt["rms_band"] is None
        assert receipt["n_required_groups"] == len(run["outcomes"])
        assert not any(c["cause"] == "review_stale" for c in receipt["causes"])
    expected = {oid for oid, obs in store[2].items()
                if obs.source_id == source and obs.admission.status.value == "admitted"
                and obs.identity.quantity.is_value
                and obs.identity.quantity.value.value in {"residue_component_composition", "mass_loss_fraction"}}
    selected = {oid for run in manifest["runs"] for group in run["outcomes"] for oid in group["observation_ids"]}
    assert expected <= selected
    run = next(run for run in manifest["runs"] if any(g["observation_ids"] for g in run["outcomes"]))
    oid = next(oid for group in run["outcomes"] for oid in group["observation_ids"])
    observation = store[2][oid]
    changed = {**store[2], oid: replace(observation, identity=replace(
        observation.identity, temperature_K=State.of(Decimal("9999"))))}
    receipt = evaluate_card(manifest, run, review["runs"][run["card_id"]], scope, changed)
    assert receipt["review_current"] is False


@pytest.fixture
def complete_contract(paper_point):
    obs = replace(paper_point, notices=(), uncertainty=Uncertainty(
        kind=UncertaintyKind.PRINTED,
        verbatim={"value": "1", "unit": "wt%", "confidence_level": "two_sigma"},
    ))
    oid = obs.observation_id
    run = {"card_id": "contract", "experiment_ids": [obs.experiment_id],
           "setup_ref": obs.experiment_id, "assumptions": [],
           "outcomes": [{"group_id": "chemistry", "observation_ids": [oid],
                         "required_coordinates": 1, "comparator": "battery_point",
                         "independent": True, "blockers": []}],
           "printed_inventory": [{"group_id": "chemistry", "locator": {"page": 6}}]}
    scope = {"experiments": {obs.experiment_id: {"work_id": "work", "mass": "0.0001"}},
             "observations": {oid: to_plain(obs)}}
    entries = [{"ref": ["experiments", obs.experiment_id, "mass"],
                "resolved": "0.0001", "status": "reviewed", "reviewer": "fixture",
                "locator": {"page": 2}, "quote": "100.0 mg", "unit": "kg"},
               {"ref": ["observations", oid, "value"], "resolved": to_plain(obs.value),
                "status": "reviewed", "reviewer": "fixture", "locator": to_plain(obs.locator),
                "quote": "22.74", "unit": "wt%"}]
    for path in (["uncertainty"], ["notices"], ["identity"], ["evidence"], ["admission"]):
        entries.append({"ref": ["observations", oid, *path],
                        "resolved": resolve_path(scope, ["observations", oid, *path]),
                        "status": "reviewed", "reviewer": "fixture",
                        "locator": to_plain(obs.locator), "quote": "policy fixture", "unit": "as printed"})
    review = {"frozen_run": deepcopy(run), "entries": entries,
              "consumed_paths": [entry["ref"] for entry in entries]}
    manifest = {"work_id": "work", "source_ids": [obs.source_id],
                "authoring_revision": "fixture", "engine_policy": "source_only"}
    return manifest, run, review, scope, {oid: obs}, {oid: Decimal("22.74")}


def test_contract_can_pass_and_zero_width_rms_is_null(complete_contract):
    args = complete_contract
    receipt = evaluate_card(*args)
    assert receipt["verdict"] == "PASS"
    assert receipt["rms_band"] == "0.00"
    oid, obs = next(iter(args[4].items()))
    args[4][oid] = replace(obs, uncertainty=Uncertainty(
        kind=UncertaintyKind.PRINTED, verbatim={"value": "0", "unit": "wt%"}))
    next(entry for entry in args[2]["entries"] if entry["ref"][-1] == "uncertainty")["resolved"] = to_plain(args[4][oid].uncertainty)
    receipt = evaluate_card(*args)
    assert receipt["verdict"] == "PASS"
    assert receipt["rms_band"] is None


@pytest.mark.parametrize("mutation", ["row", "group", "band", "attestation", "assumption",
                                      "nan", "null", "hidden_flag", "locator", "path"])
def test_deletions_and_bad_inputs_cannot_improve_denominator(complete_contract, mutation):
    args = deepcopy(complete_contract)
    manifest, run, review, scope, observations, candidates = args
    oid = next(iter(observations))
    if mutation == "row":
        run["outcomes"][0]["observation_ids"] = []
    elif mutation == "group":
        run["outcomes"] = []
    elif mutation == "band":
        observations[oid] = replace(observations[oid], uncertainty=Uncertainty(kind=UncertaintyKind.NONE))
    elif mutation == "attestation":
        review["entries"][0]["reviewer"] = ""
    elif mutation == "assumption":
        run["assumptions"] = ["assumed sphere"]
    elif mutation in {"nan", "null"}:
        candidates[oid] = Decimal("NaN") if mutation == "nan" else None
    elif mutation == "hidden_flag":
        scope["experiments"][observations[oid].experiment_id]["mass"] = {"assumed": True}
    elif mutation == "locator":
        review["entries"][0]["locator"] = None
    elif mutation == "path":
        review["entries"].pop()
    receipt = evaluate_card(*args)
    assert receipt["verdict"] == "NOT_PASS"
    assert receipt["n_required_groups"] == 1
    assert receipt["n_required"] == 1


def test_setup_only_change_and_unrelated_row(complete_contract):
    args = complete_contract
    args[3]["experiments"]["unrelated"] = {"mass": "changed"}
    assert evaluate_card(*args)["verdict"] == "PASS"
    args[3]["experiments"][args[1]["setup_ref"]]["mass"] = "0.0002"
    receipt = evaluate_card(*args)
    assert receipt["verdict"] == "NOT_PASS"
    assert "review_stale" in {c["cause"] for c in receipt["causes"]}
    assert args[3]["observations"][next(iter(args[4]))]["value"]["point"] == "22.74"


def test_stale_plain_scope_cannot_hide_a_widened_band_or_source_flag(complete_contract):
    args = complete_contract
    oid, obs = next(iter(args[4].items()))
    args[5][oid] = Decimal("24.74")
    assert evaluate_card(*args)["verdict"] == "NOT_PASS"
    args[4][oid] = replace(obs, uncertainty=Uncertainty(
        kind=UncertaintyKind.PRINTED, verbatim={"value": "3", "unit": "wt%"}))
    receipt = evaluate_card(*args)
    assert receipt["verdict"] == "NOT_PASS"
    assert receipt["review_current"] is False
    notice = Notice(kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
                    affected_quantities=(obs.identity.quantity.value,),
                    reason="domain extension", origin="fixture")
    args[4][oid] = replace(obs, notices=(notice,))
    args[5][oid] = Decimal("22.74")
    receipt = evaluate_card(*args)
    assert receipt["verdict"] == "NOT_PASS"
    assert receipt["groups"][0]["coordinates"][0]["flags"]
    next(entry for entry in args[2]["entries"] if entry["ref"][-1] == "notices")["resolved"] = to_plain((notice,))
    qualified = evaluate_card(*args)
    assert qualified["verdict"] == "PASS_WITH_FLAGS"
    assert qualified["evidence_label"] == "development_validation"


def test_omitted_review_cannot_authorize_an_unreviewed_wider_band(complete_contract):
    args = complete_contract
    oid, obs = next(iter(args[4].items()))
    args[5][oid] = Decimal("24.74")
    assert evaluate_card(*args)["verdict"] == "NOT_PASS"
    args[2]["entries"] = [entry for entry in args[2]["entries"]
                           if entry["ref"][0] != "observations"]
    args[2]["consumed_paths"] = [entry["ref"] for entry in args[2]["entries"]]
    args[4][oid] = replace(obs, uncertainty=Uncertainty(
        kind=UncertaintyKind.PRINTED,
        verbatim={"value": "3", "unit": "wt%", "confidence_level": "two_sigma"}))
    receipt = evaluate_card(*args)
    assert receipt["verdict"] == "NOT_PASS"
    assert receipt["review_current"] is False
    assert any(cause.get("reason") == "uncovered_consumed_path"
               and cause["path"][:2] == ["observations", oid] for cause in receipt["causes"])


def test_group_gate_rejects_coordinate_availability_swap(complete_contract):
    args = complete_contract
    oid, obs = next(iter(args[4].items()))
    other = oid + "-second-coordinate"
    args[4][other] = replace(obs, observation_id=other)
    args[1]["outcomes"][0]["observation_ids"].append(other)
    args[1]["outcomes"][0]["required_coordinates"] = 2
    args[2]["frozen_run"] = deepcopy(args[1])
    for entry in list(args[2]["entries"]):
        if entry["ref"][0] == "observations":
            copied = deepcopy(entry)
            copied["ref"][1] = other
            args[2]["entries"].append(copied)
    args[2]["consumed_paths"] = [entry["ref"] for entry in args[2]["entries"]]
    args[5].update({oid: Decimal("24.74"), other: None})
    before = evaluate_card(*args)
    args[5].update({oid: None, other: Decimal("24.74")})
    after = evaluate_card(*args)
    assert before["n_numeric"] == after["n_numeric"] == 1
    assert before["rms_band"] is after["rms_band"] is None
    gate = group_gate(before, after)
    assert gate["accepted"] is False
    assert f"chemistry:{oid}:numeric_availability" in gate["regressions"]


def test_unrelated_outcome_attestation_preserves_reproduced_chemistry(complete_contract):
    args = complete_contract
    args[1]["outcomes"].append({
        "group_id": "isotopes", "observation_ids": [], "required_coordinates": 1,
        "independent": True, "comparator": "unsupported", "blockers": ["unsupported"]})
    args[1]["printed_inventory"].append({
        "group_id": "isotopes", "context_ref": ["extracts", "isotope_outcome"],
        "locator": {"page": 6}})
    args[2]["frozen_run"] = deepcopy(args[1])
    args[3]["extracts"] = {"isotope_outcome": "printed isotope"}
    args[2]["entries"].append({
        "ref": ["extracts", "isotope_outcome"], "resolved": "printed isotope",
        "role": "outcome", "status": "source_unattested", "reviewer": "fixture",
        "locator": {"page": 6}, "quote": "printed isotope", "unit": "permil"})
    args[2]["consumed_paths"] = [entry["ref"] for entry in args[2]["entries"]]
    receipt = evaluate_card(*args)
    assert receipt["verdict"] == "NOT_PASS"
    assert receipt["groups"][0]["reproduced"] is True
    assert receipt["groups"][0]["causes"] == []
    assert receipt["groups"][0]["rms_band"] == "0.00"
    assert receipt["group_fraction"] == {"numerator": 1, "denominator": 2}


def test_setup_review_can_expire_while_existing_observation_certification_stays_green(
    complete_contract, tmp_path,
):
    from tools.validate_literature_extracts import certify_extract_source_fidelity

    artifact = tmp_path / "tables" / "printed.txt"
    artifact.parent.mkdir()
    artifact.write_text("Published alpha 0.24\n")
    document = {
        "experiments": [{"sample": {"mass": "0.0001"}}],
        "species": {"Fe": {"observations": [{
            "observation_id": "fe_alpha_1", "type": "alpha",
            "phase": "silicate_melt", "regime": "langmuir_free_evaporation",
            "units": "dimensionless", "locator": {"page": 2, "table": "1"},
            "values": {"alpha": 0.24},
        }]}},
        "fidelity_samples": [{
            "path": "species.Fe.observations[fe_alpha_1].values.alpha", "value": 0.24,
            "reviewed_by": "source reviewer", "reviewed_on": "2026-11-13",
            "locator": {"page": 2, "source_path": "tables/printed.txt"},
        }],
    }
    assert certify_extract_source_fidelity(document, artifact_root=tmp_path) == []
    document["experiments"][0]["sample"]["mass"] = "0.0002"
    args = complete_contract
    args[3]["experiments"][args[1]["setup_ref"]]["mass"] = "0.0002"
    assert certify_extract_source_fidelity(document, artifact_root=tmp_path) == []
    assert evaluate_card(*args)["review_current"] is False


def test_report_only_group_gate_sees_perturbation_under_incomplete_card(complete_contract):
    args = complete_contract
    blocked = {"group_id": "isotopes", "observation_ids": [], "required_coordinates": 1,
               "independent": True, "comparator": "unsupported", "blockers": ["unsupported"]}
    args[1]["outcomes"].append(blocked)
    args[1]["printed_inventory"].append({"group_id": "isotopes", "locator": {"page": 6}})
    args[2]["frozen_run"] = deepcopy(args[1])
    before = evaluate_card(*args)
    args[5][next(iter(args[5]))] = Decimal("24.74")
    after = evaluate_card(*args)
    assert before["verdict"] == after["verdict"] == "NOT_PASS"
    assert before["rms_band"] is after["rms_band"] is None
    gate = group_gate(before, after)
    assert gate["mode"] == "report_only"
    assert gate["accepted"] is False
    assert "chemistry" in str(gate["regressions"])
    args[5][next(iter(args[5]))] = Decimal("22.74")
    assert group_gate(before, evaluate_card(*args))["accepted"] is True


@pytest.mark.parametrize("source, count", zip(SOURCES, (31, 44, 2, 51)))
def test_derived_receipts_and_summary(store, tmp_path, source, count):
    manifest = ROOT / "data/literature/reproduction-cards" / f"{source}.yaml"
    derived = tmp_path / "data/battery/reproduction-cards"
    summary = write_study(ROOT, manifest, derived, store=store)
    assert summary["n_cards"] == count
    assert summary["n_pass"] == 0
    assert len(list(derived.glob("*.json"))) == count + 1


def test_four_study_summary_keeps_every_card(store, tmp_path):
    manifests = [ROOT / "data/literature/reproduction-cards" / f"{source}.yaml" for source in SOURCES]
    summary = write_studies(ROOT, manifests, tmp_path, store=store)
    assert summary["n_cards"] == 128
    assert summary["n_pass"] == summary["n_pass_with_flags"] == 0
    assert len(list(tmp_path.glob("*.json"))) == 129
