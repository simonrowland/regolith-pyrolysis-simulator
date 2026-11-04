from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


def _vaporock_root() -> Path | None:
    configured = os.environ.get("OPENIMCC_VAPOROCK_ROOT")
    if configured:
        return Path(configured)
    spec = importlib.util.find_spec("vaporock")
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).resolve().parents[2]


def test_vaporock_gas_table_refuses_k2o_mismatch_and_flags_unknown_mgo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openimcc")
    vaporock_root = _vaporock_root()
    if (
        vaporock_root is None
        or not (
            vaporock_root / "src/vaporock/data/JANAF-vapor-data-full.csv"
        ).is_file()
    ):
        pytest.skip("VapoRock checkout is not present")

    from simulator.diagnostic_helpers.binary_pot_battery import _OpenImccBatteryBackend
    from simulator.melt_backend import openimcc_bridge
    from simulator.melt_backend.openimcc_liquid_standards import (
        assess_liquid_standards,
    )

    monkeypatch.setenv("OPENIMCC_VAPOROCK_ROOT", str(vaporock_root))
    backend = _OpenImccBatteryBackend("openimcc")

    with pytest.raises(openimcc_bridge.OpenImccCompositionPolicyRefusal) as caught:
        backend.equilibrate(
            temperature_C=1800.0 - 273.15,
            composition_mol={"K2O": 1.0, "SiO2": 1.0},
            fO2_log=-10.0,
        )

    refusal = caught.value
    assert refusal.code == "openimcc_liquid_standard_refused"
    assert refusal.diagnostics["invalid_input"] is True
    assert refusal.diagnostics["category"] == 2
    k2o_failure = next(
        item
        for item in refusal.diagnostics["standard_failures"]
        if item["oxide"] == "K2O" and item["kind"] == "mismatch"
    )
    assert k2o_failure["declared_standard_id"] == "K-012"
    assert k2o_failure["active_standard_id"] == "LAM1984"
    assert "oxide=K2O" in str(refusal)
    assert "declared=K-012" in str(refusal)
    assert "active=LAM1984" in str(refusal)
    assert "pack=" in str(refusal)

    assessment = assess_liquid_standards(
        backend._pack,
        None,
        None,
        {oxide: 1.0 for oxide in backend._pack.parent_oxides},
    )
    assert any(
        flag.kind == "reference-unknown"
        and flag.oxide == "MgO"
        and "printed Ref: LAM1987" in flag.citation
        for flag in assessment.flags
    )
    assert not any(refusal.oxide == "MgO" for refusal in assessment.refusals)
