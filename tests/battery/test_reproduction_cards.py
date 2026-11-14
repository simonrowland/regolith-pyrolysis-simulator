"""Phase A contracts; paper values are independent of engine replay."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import UncertaintyKind, ValueKind
from simulator.battery.migrate import load_migrated_store
from simulator.battery.records import Uncertainty, Value
from simulator.battery.reproduction_cards import (
    compare_point,
    evaluate_card,
    group_gate,
    load_review_scope,
    write_study,
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
        verbatim={"value": "1 wt%", "confidence_level": confidence},
    ))
    row = compare_point(obs, Decimal("23.74"))
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
        verbatim={"value": "[0, 2] wt%", "confidence_level": "range_half_width"},
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
        verbatim={"value": "0 wt%", "confidence_level": "two_sigma"},
    ))
    assert compare_point(point, Decimal("22.74"))["status"] == "MATCH"
    assert compare_point(point, Decimal("22.74000001"))["status"] == "MISMATCH"
    interval = replace(point, value=Value(kind=ValueKind.INTERVAL,
                                        lower=Decimal(22), upper=Decimal(23)))
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
        receipt = evaluate_card(manifest, run, frozen, scope, store[2])
        assert receipt["verdict"] == "NOT_PASS"
        assert receipt["rms_band"] is None
        assert receipt["n_required_groups"] == len(run["outcomes"])
        assert not any(c["cause"] == "review_stale" for c in receipt["causes"])


@pytest.fixture
def complete_contract(paper_point):
    obs = replace(paper_point, notices=(), uncertainty=Uncertainty(
        kind=UncertaintyKind.PRINTED,
        verbatim={"value": "1 wt%", "confidence_level": "two_sigma"},
    ))
    oid = obs.observation_id
    run = {"card_id": "contract", "experiment_ids": [obs.experiment_id],
           "setup_ref": obs.experiment_id, "assumptions": [],
           "outcomes": [{"group_id": "chemistry", "observation_ids": [oid],
                         "required_coordinates": 1, "comparator": "battery_point",
                         "independent": True, "blockers": []}],
           "printed_inventory": [{"group_id": "chemistry", "locator": {"page": 6}}]}
    scope = {"experiments": {obs.experiment_id: {"work_id": "work", "mass": "0.0001"}},
             "observations": {oid: {"value": "22.74"}}}
    entries = [{"ref": ["experiments", obs.experiment_id, "mass"],
                "resolved": "0.0001", "status": "reviewed", "reviewer": "fixture",
                "locator": {"page": 2}, "quote": "100.0 mg", "unit": "kg"},
               {"ref": ["observations", oid, "value"], "resolved": "22.74",
                "status": "reviewed", "reviewer": "fixture", "locator": {"page": 6},
                "quote": "22.74", "unit": "wt%"}]
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
        kind=UncertaintyKind.PRINTED, verbatim={"value": "0 wt%"}))
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
    assert args[3]["observations"][next(iter(args[4]))]["value"] == "22.74"


def test_report_only_group_gate_sees_perturbation_under_incomplete_card(complete_contract):
    args = complete_contract
    blocked = {"group_id": "isotopes", "observation_ids": [], "required_coordinates": 1,
               "independent": True, "comparator": "unsupported", "blockers": ["unsupported"]}
    args[1]["outcomes"].append(blocked)
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


def test_derived_receipts_and_summary(store, tmp_path):
    manifest = ROOT / "data/literature/reproduction-cards" / f"{SOURCES[0]}.yaml"
    summary = write_study(ROOT, manifest, tmp_path, store=store)
    assert summary["n_cards"] == 31
    assert summary["n_pass"] == 0
    assert len(list(tmp_path.glob("*.json"))) == 32
