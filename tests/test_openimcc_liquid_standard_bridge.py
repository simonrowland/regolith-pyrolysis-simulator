from __future__ import annotations

from importlib import import_module, util
from typing import Any

import pytest

import openimcc
from openimcc.redox_pack import load_redox_pack

from engines.builtin import vapor_pressure
from simulator.melt_backend import openimcc_bridge
from simulator.melt_backend.openimcc_bridge import _load_pack


def _default_composition() -> dict[str, float]:
    return {oxide: 1.0 for oxide in _load_pack("default").parent_oxides}


def _liquid_standard_owner() -> Any:
    module_name = "simulator.melt_backend.openimcc_liquid_standards"
    if util.find_spec(module_name) is None:
        return None
    return import_module(module_name)


def test_shared_owner_reports_sf04_k2o_mismatch_without_keying_on_ids() -> None:
    owner_module = _liquid_standard_owner()
    assert owner_module is not None, "shared OpenIMCC liquid-standard owner is missing"
    if owner_module is None:
        return
    owner = getattr(owner_module, "assess_liquid_standards", None)
    assert callable(owner), "shared OpenIMCC liquid-standard owner is missing"
    if not callable(owner):
        return

    assessment = owner(
        _load_pack("published-core"),
        load_redox_pack(),
        None,
        {"K2O": 1.0, "SiO2": 1.0},
    )

    assert not assessment.flags
    assert any(
        item.kind == "mismatch"
        and item.oxide == "K2O"
        and item.declared_standard_id
        and item.active_standard_id
        and item.declared_standard_id != item.active_standard_id
        for item in assessment.refusals
    )


def test_bridge_refuses_sf04_k2o_mismatch_as_invalid_category_two() -> None:
    with pytest.raises(
        openimcc_bridge.OpenImccCompositionPolicyRefusal
    ) as caught:
        openimcc_bridge.evaluate(
            composition_mol={"K2O": 1.0, "SiO2": 1.0},
            temperature_K=1800.0,
            pack="published-core",
        )

    refusal = caught.value
    assert refusal.code == "openimcc_liquid_standard_refused"
    assert refusal.diagnostics["invalid_input"] is True
    assert refusal.diagnostics["category"] == 2
    failures = refusal.diagnostics["standard_failures"]
    assert any(
        item["kind"] == "mismatch"
        and item["oxide"] == "K2O"
        and item["declared_standard_id"]
        and item["active_standard_id"]
        and item["declared_standard_id"] != item["active_standard_id"]
        for item in failures
    )
    reason = str(refusal)
    assert "oxide=K2O" in reason
    assert "pack=" in reason
    assert "declared=" in reason
    assert "active=" in reason


def test_redox_bridge_refuses_melt_pack_mismatch(monkeypatch: Any) -> None:
    evaluate_redox = getattr(openimcc_bridge, "evaluate_redox", None)
    assert callable(evaluate_redox), "OpenIMCC redox bridge entry point is missing"
    if not callable(evaluate_redox):
        return
    mismatch_pack = _load_pack("published-core")
    monkeypatch.setattr(openimcc_bridge, "_load_pack", lambda _name: mismatch_pack)

    with pytest.raises(openimcc_bridge.OpenImccCompositionPolicyRefusal) as caught:
        evaluate_redox(
            {"Fe": 1.0, "O": 2.0, "K": 1.0, "Si": 1.0},
            1800.0,
            "closed",
            composition={"K2O": 1.0, "SiO2": 1.0},
        )

    assert caught.value.diagnostics["category"] == 2
    assert any(
        failure["kind"] == "mismatch" and failure["oxide"] == "K2O"
        for failure in caught.value.diagnostics["standard_failures"]
    )


def test_redox_bridge_returns_melt_and_redox_flag_records(monkeypatch: Any) -> None:
    evaluate_redox = getattr(openimcc_bridge, "evaluate_redox", None)
    assert callable(evaluate_redox), "OpenIMCC redox bridge entry point is missing"
    if not callable(evaluate_redox):
        return
    monkeypatch.setattr(
        openimcc,
        "evaluate_redox",
        lambda *_args, **_kwargs: object(),
    )
    composition = _default_composition() | {"MnO": 1.0}
    result = evaluate_redox(
        {"Fe": 1.0, "O": 2.0},
        1800.0,
        "closed",
        composition=composition,
    )

    melt_rows = {
        flag.row_key
        for flag in result.liquid_standard_flags
        if flag.pack == f"{_load_pack('default').model_id} {_load_pack('default').version}"
    }
    redox_rows = {
        flag.row_key
        for flag in result.liquid_standard_flags
        if flag.pack.startswith("openimcc-redox-pack ")
    }
    assert {"K2SiO3", "Fe2SiO4"}.issubset(melt_rows)
    assert {"MnO(l)", "MnSiO3(l)"}.issubset(redox_rows)


def test_production_vapor_pressure_caller_keeps_standard_refusal_visible(
    monkeypatch: Any,
) -> None:
    mismatch_pack = _load_pack("published-core")
    monkeypatch.setattr(vapor_pressure, "openimcc_binding_selected_by_inputs", lambda **_kwargs: True)
    monkeypatch.setattr(openimcc_bridge, "_load_pack", lambda _name: mismatch_pack)
    monkeypatch.setattr(
        vapor_pressure,
        "_same_composition_temperature_bridge",
        lambda *_args, **_kwargs: {"continuations_by_parent": {}},
    )

    authority = vapor_pressure._build_high_t_melt_activity_authority(
        composition_mol={"K2O": 1.0, "SiO2": 1.0},
        temperature_K=2000.0,
        controls={},
        below_cap_fe_activity=0.2,
        below_cap_fe_activity_basis="single_cation",
    )

    assert authority is not None
    assert authority["fallback"] is True
    assert authority["fallback_reason"]["code"] == "openimcc_liquid_standard_refused"
    assert any(
        notice.get("code") == "openimcc_liquid_standard_refused"
        for notice in authority["notices"]
    )


def test_default_bridge_returns_only_composition_dependent_flag_records() -> None:
    records = openimcc_bridge.evaluate(
        composition_mol=_default_composition(),
        temperature_K=1800.0,
    ).liquid_standard_flags

    assert len(records) == 10
    assert all(
        record.kind in {"reference-unknown", "non-source-basis"}
        and record.oxide
        and record.standard_id
        and record.basis
        and record.citation
        for record in records
    )
    assert {
        record.row_key
        for record in records
        if record.kind == "reference-unknown"
    } == {"K2SiO3", "K2Si2O5", "K2Si4O9"}

    owner_module = _liquid_standard_owner()
    assert owner_module is not None, "shared OpenIMCC liquid-standard owner is missing"
    if owner_module is None:
        return
    owner = getattr(owner_module, "assess_liquid_standards", None)
    assert callable(owner), "shared OpenIMCC liquid-standard owner is missing"
    if not callable(owner):
        return
    absent_silica = owner(_load_pack("default"), None, None, {"K2O": 1.0})
    assert "K2SiO3" not in {
        record.row_key
        for record in absent_silica.flags
        if record.kind == "reference-unknown"
    }
