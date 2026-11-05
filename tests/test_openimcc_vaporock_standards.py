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


def test_vaporock_gas_table_converts_active_parent_standards_and_flags_unknown_mgo(
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
    from simulator.melt_backend.openimcc_liquid_standards import (
        assess_liquid_standards,
        convert_parent_activities_for_gas,
    )

    monkeypatch.setenv("OPENIMCC_VAPOROCK_ROOT", str(vaporock_root))
    backend = _OpenImccBatteryBackend("openimcc")

    result = backend.equilibrate(
        temperature_C=1800.0 - 273.15,
        composition_mol={"K2O": 1.0, "SiO2": 1.0},
        fO2_log=-10.0,
    )

    assert result.status == "ok"
    assert result.vapor_pressures_Pa["K"] > 0.0
    conversion = next(
        notice
        for notice in result.diagnostics["imcc_notices"]
        if notice.get("kind") == "openimcc_liquid_standard_conversion"
        and notice.get("oxide") == "K2O"
    )
    assert conversion["standard_id"] == "K-012"
    assert conversion["active_standard_id"] == "LAM1984"
    assert conversion["log10_offset"] != 0.0

    converted, parent_conversions = convert_parent_activities_for_gas(
        {oxide: 1.0 for oxide in backend._pack.parent_oxides},
        1800.0,
        backend._pack,
        backend._gas,
    )
    assert {
        notice["oxide"] for notice in parent_conversions
    } == {"SiO2", "MgO", "CaO", "Al2O3", "Na2O", "K2O"}
    assert all(converted[notice["oxide"]] != 1.0 for notice in parent_conversions)
    assert all(
        notice["standard_id"]
        and notice["active_standard_id"]
        and notice["log10_offset"] != 0.0
        for notice in parent_conversions
    )

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
