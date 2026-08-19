from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import yaml
import pytest

from simulator.accounting.stage0_inventory import STAGE0_RELEASE_KINETICS
from simulator.runner import PyrolysisRun, main
from simulator.state import CampaignPhase
from tests.chemistry.conftest import _build_sim


DATA_DIR = Path(__file__).resolve().parent.parent / "data"

ENVELOPE_FORBIDDEN_KEYS = frozenset({
    "target_inventory",
    "_target_inventory_by_hour",
    "stage0_release_kinetics",
    "would_be_inventory_advance",
    "chnops_expansion",
    "aggregate_remaining_fraction",
})


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


def _diagnostic_setpoints() -> dict:
    setpoints = deepcopy(_load_yaml("setpoints.yaml"))
    setpoints["freeze_gate"] = dict(setpoints.get("freeze_gate", {}) or {})
    setpoints["freeze_gate"]["enabled"] = False
    return setpoints


def _c0_sim(feedstock_id: str):
    sim = _build_sim(
        feedstock_id,
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _diagnostic_setpoints(),
    )
    sim.start_campaign(CampaignPhase.C0)
    return sim


def _target(record: dict, key: str) -> dict:
    for row in record.get("targets") or []:
        if row.get("key") == key:
            return row
    raise AssertionError(f"missing target {key!r} in {record.get('targets')}")


def _collect_mapping_keys(value, found: set[str] | None = None) -> set[str]:
    found = set() if found is None else found
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(str(key))
            _collect_mapping_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_mapping_keys(item, found)
    return found


def _assert_kinetics_gap(record: dict) -> None:
    assert record["stage0_release_kinetics"] == STAGE0_RELEASE_KINETICS


def test_lunar_c0_depleted_from_load_with_kinetics_gap() -> None:
    sim = _c0_sim("lunar_mare_low_ti")
    sim.step()

    record = sim._last_target_inventory_diagnostic
    _assert_kinetics_gap(record)
    assert record["campaign"] == "C0"
    assert record["depleted"] is True
    assert record["depletion_hour"] == "load"
    assert record["would_be_inventory_advance"] is True
    assert record["exclude_elements"] == ["P"]
    assert "P" not in record["chnops_expansion"]["included_elements"]
    assert record["chnops_expansion"]["map"]["P"] == "excluded"
    assert record["chnops_expansion"]["map"]["O"] == "excluded_oxide_bound"
    assert record["chnops_expansion"]["oxide_bound_O"] == "excluded"

    h2o = _target(record, "H2O")
    co2 = _target(record, "CO2")
    s2 = _target(record, "S2")
    chnops = _target(record, "CHNOPS")
    assert h2o["status"] == "zero_denominator"
    assert co2["status"] == "zero_denominator"
    assert s2["status"] == "ok"
    assert s2["initial_kg"] == pytest.approx(
        sim.record.initial_inventory.sulfide_matte_kg["S"]
    )
    assert s2["initial_kg"] > 0.0
    assert s2["remaining_kg"] == pytest.approx(0.0, abs=1e-12)
    assert s2["remaining_fraction"] == pytest.approx(0.0, abs=1e-12)
    assert chnops["status"] == "deferred_semantic_split"
    assert {row["key"] for row in record["targets"]} == {"H2O", "CO2", "S2", "CHNOPS"}

    expansion = record["chnops_expansion"]["elements"]
    assert expansion["H"]["status"] == "absent_from_ledger"
    assert expansion["C"]["status"] == "absent_from_ledger"
    assert expansion["N"]["status"] == "absent_from_ledger"
    assert expansion["S"]["status"] == "ok"
    assert expansion["S"]["remaining_fraction"] == pytest.approx(0.0, abs=1e-12)
    assert "P" not in expansion
    assert sim.record.initial_inventory.melt_oxide_kg["P2O5"] > 0.0
    assert "Cl" not in {row["key"] for row in record["targets"]}
    assert "Cl" not in expansion
    assert "target_inventory" not in sim.record.snapshots[-1].__dict__


def test_ci_c0_larger_denominators_still_depleted_from_load() -> None:
    sim = _c0_sim("ci_carbonaceous_chondrite")
    sim.step()

    record = sim._last_target_inventory_diagnostic
    _assert_kinetics_gap(record)
    assert record["depleted"] is True
    assert record["depletion_hour"] == "load"
    assert "P" not in record["chnops_expansion"]["included_elements"]
    assert record["chnops_expansion"]["map"]["P"] == "excluded"

    h2o = _target(record, "H2O")
    co2 = _target(record, "CO2")
    s2 = _target(record, "S2")
    assert h2o["status"] == "ok"
    assert co2["status"] == "ok"
    assert s2["status"] == "ok"
    assert h2o["initial_kg"] == pytest.approx(
        sim.record.initial_inventory.gas_volatiles_kg["H2O"]
    )
    assert h2o["initial_kg"] > 100.0
    assert s2["initial_kg"] == pytest.approx(
        sim.record.initial_inventory.sulfide_matte_kg["S"]
    )
    assert s2["initial_kg"] > 30.0
    assert h2o["remaining_kg"] == pytest.approx(0.0, abs=1e-9)
    assert s2["remaining_kg"] == pytest.approx(0.0, abs=1e-9)
    assert h2o["primary_destination"] == "offgas_vented"
    assert h2o["primary_destination"] != "cleanup_sequestered"
    assert s2["primary_destination"] == "cleanup_sequestered"
    assert "P" not in record["chnops_expansion"]["elements"]
    assert float(sim.record.initial_inventory.melt_oxide_kg.get("P2O5") or 0.0) == 0.0


def test_target_inventory_not_on_hour_snapshot() -> None:
    sim = _c0_sim("lunar_mare_low_ti")
    sim.step()
    snapshot = sim.record.snapshots[-1]
    assert "target_inventory" not in snapshot.__dict__
    assert "stage0_release_kinetics" not in snapshot.__dict__
    assert "would_be_inventory_advance" not in snapshot.__dict__
    assert getattr(snapshot, "target_inventory", None) is None


def test_runner_envelope_omits_target_inventory_keys() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=1,
        force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        setpoints_patch={"freeze_gate": {"enabled": False}},
    )
    payload = run.run()
    keys = _collect_mapping_keys(payload)
    assert keys.isdisjoint(ENVELOPE_FORBIDDEN_KEYS)
    assert run._target_inventory_by_hour
    for record in run._target_inventory_by_hour:
        _assert_kinetics_gap(record)
        assert record["depletion_hour"] == "load"


def test_sibling_artifact_written_only_with_flag(tmp_path: Path) -> None:
    output = tmp_path / "case.json"
    sibling = tmp_path / "case.target_inventory.json"
    common = [
        "--feedstock", "lunar_mare_low_ti",
        "--campaign", "C0",
        "--hours", "1",
        "--force-builtin-vapor-pressure",
        "--allow-fallback-vapor",
        "--allow-unmeasured-alpha-fallback",
    ]

    rc = main([*common, "--output", str(output)])
    assert rc in {0, 1}
    assert output.exists()
    assert not sibling.exists()
    envelope = json.loads(output.read_text())
    assert _collect_mapping_keys(envelope).isdisjoint(ENVELOPE_FORBIDDEN_KEYS)

    flagged = tmp_path / "flagged.json"
    flagged_sibling = tmp_path / "flagged.target_inventory.json"
    rc = main([
        *common,
        "--output", str(flagged),
        "--write-target-inventory",
    ])
    assert rc in {0, 1}
    assert flagged_sibling.exists()
    payload = json.loads(flagged_sibling.read_text())
    assert payload["kind"] == "target_inventory"
    assert payload["stage0_release_kinetics"] == STAGE0_RELEASE_KINETICS
    assert payload["schema_version"] == 1
    assert payload["records"]
    for record in payload["records"]:
        _assert_kinetics_gap(record)
        assert "targets" in record
    flagged_envelope = json.loads(flagged.read_text())
    assert _collect_mapping_keys(flagged_envelope).isdisjoint(ENVELOPE_FORBIDDEN_KEYS)


def test_target_inventory_diagnostic_does_not_change_campaign_advancement() -> None:
    with_diagnostic = _c0_sim("lunar_mare_low_ti")
    without_diagnostic = _c0_sim("lunar_mare_low_ti")
    without_diagnostic._update_target_inventory_diagnostic = lambda: None
    for sim in (with_diagnostic, without_diagnostic):
        sim.melt.campaign_hour = 10
        sim.melt.temperature_C = 940.0

    with_diagnostic.step()
    without_diagnostic.step()

    assert (
        with_diagnostic.melt.campaign,
        with_diagnostic.melt.hour,
        with_diagnostic.melt.campaign_hour,
        len(with_diagnostic.record.snapshots),
        with_diagnostic.paused_for_decision,
    ) == (
        without_diagnostic.melt.campaign,
        without_diagnostic.melt.hour,
        without_diagnostic.melt.campaign_hour,
        len(without_diagnostic.record.snapshots),
        without_diagnostic.paused_for_decision,
    )
    assert with_diagnostic._last_target_inventory_diagnostic["campaign"] == "C0"
    _assert_kinetics_gap(with_diagnostic._last_target_inventory_diagnostic)
    assert without_diagnostic._last_target_inventory_diagnostic == {}
