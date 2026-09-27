"""Focused contract tests for the optional openimcc battery producer."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Engine,
    Phase,
    Quantity,
    RefusalReason,
)
from simulator.battery.score import (
    ENGINE_CHANNELS,
    SCORE_ENGINE_SET,
    candidate_observation,
    composition_wt_pct,
    predict_with_engine,
)
from simulator.battery.records import Composition, Species
from simulator.diagnostic_helpers.binary_pot_battery import (
    BATTERY_ENGINE_NAMES,
    BinaryPot,
    Po2Request,
    composition_kg_and_mol,
    equilibrate_cell,
    open_battery_engine,
)
from simulator.battery.waypoints import ENGINE_POINT_CONSUMERS, MELT_ACTIVITY_ENGINES
from tests.battery import factories as F


REPO_ROOT = Path(__file__).resolve().parents[1]
PLANTE_HAND_ROWS = REPO_ROOT / "tests/fixtures/openimcc/plante_1979_hand_rows.json"


def _vaporock_root() -> Path | None:
    """VapoRock checkout root: OPENIMCC_VAPOROCK_ROOT, else the installed package's checkout."""
    configured = os.environ.get("OPENIMCC_VAPOROCK_ROOT")
    if configured:
        return Path(configured)
    import importlib.util

    spec = importlib.util.find_spec("vaporock")
    if spec is None or spec.origin is None:
        return None
    # <root>/src/vaporock/__init__.py -> <root>
    return Path(spec.origin).resolve().parents[2]


VAPOROCK_ROOT = _vaporock_root()


def _require_openimcc() -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")


def _require_ti_gas() -> None:
    _require_openimcc()
    from openimcc import load_gas_datapack

    datapack = load_gas_datapack()
    if (
        "Ti(g)" not in datapack.gas_df.index
        or "TiO2(l)" not in datapack.oxide_df.index
    ):
        pytest.skip("installed openimcc datapack does not contain the Ti gas channel")


def _scratch_path() -> Path | None:
    return PLANTE_HAND_ROWS if PLANTE_HAND_ROWS.is_file() else None


def _binary_probe() -> BinaryPot:
    return BinaryPot(
        pot_id="openimcc-probe",
        kato_1993_table4_system=None,
        why="focused openimcc producer test",
        composition_wt_pct={"K2O": 43.94, "SiO2": 56.06},
    )


def test_openimcc_engine_is_registered() -> None:
    assert Engine.OPENIMCC in SCORE_ENGINE_SET
    assert ENGINE_CHANNELS[Engine.OPENIMCC] == "openimcc"
    assert "openimcc" in BATTERY_ENGINE_NAMES
    assert "openimcc" in ENGINE_POINT_CONSUMERS
    assert "openimcc" in MELT_ACTIVITY_ENGINES


def test_openimcc_producer_emits_activity_and_vapour_rails() -> None:
    _require_openimcc()
    handle = open_battery_engine("openimcc")
    assert handle.available, handle.unavailable_reason
    cell = equilibrate_cell(
        handle,
        _binary_probe(),
        temperature_K=2200.0,
        po2=Po2Request(mode="commanded", po2_bar=1.0e-9),
        isolated=False,
    )
    assert cell.status == "ok", cell.engine_reason
    assert cell.melt_activities["K2O"] > 0.0
    assert cell.gas_partial_pressures_Pa["K"] > 0.0
    assert "openimcc" in cell.vapor_pressures_source["K"]
    assert "gas-shomate.csv" in cell.vapor_pressures_source["K"]
    assert cell.model_id == "IMCC-SF04"


def test_openimcc_not_importable_is_typed_in_a_clean_subprocess() -> None:
    code = r'''
import importlib.abc
import json
import sys

class BlockOpenImcc(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "openimcc" or fullname.startswith("openimcc."):
            raise ImportError("openimcc blocked by focused refusal test")
        return None

sys.meta_path.insert(0, BlockOpenImcc())
from simulator.diagnostic_helpers.binary_pot_battery import open_battery_engine
handle = open_battery_engine("openimcc")
print(json.dumps({"available": handle.available, "reason": handle.unavailable_reason}))
'''
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(REPO_ROOT),
    }
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["available"] is False
    assert "openimcc_not_importable" in payload["reason"]
    assert "remedy:" in payload["reason"]


def test_openimcc_unsupported_cr_gas_species_is_typed() -> None:
    _require_openimcc()
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(
            ("TiO2", Decimal("0.2")),
            ("SiO2", Decimal("0.8")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = F.activity_identity(
        formula="TiO2",
        component_basis="TiO2",
        composition=composition,
        T_K=Decimal("2200"),
        fO2_Pa=Decimal("1e-4"),
    )
    identity = replace(
        identity,
        quantity=Quantity.P_PARTIAL,
        species=Species("Cr", Phase.G),
    )
    observation = F.observation(
        "openimcc-cr-gas",
        "openimcc-test",
        identity,
        Decimal("1"),
    )
    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        isolated=False,
    )
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert prediction.refusal_detail["reason"] == "outside_supported_species"
    assert "Cr" not in prediction.refusal_detail["reported"]
    assert "Si" in prediction.refusal_detail["reported"]


def test_openimcc_ti_gas_matches_direct_calculation() -> None:
    _require_ti_gas()
    from openimcc import evaluate, evaluate_gas, load_gas_datapack

    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(
            ("TiO2", Decimal("0.2")),
            ("SiO2", Decimal("0.8")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = F.activity_identity(
        formula="TiO2",
        component_basis="TiO2",
        composition=composition,
        T_K=Decimal("2200"),
        fO2_Pa=Decimal("1e-4"),
    )
    identity = replace(
        identity,
        quantity=Quantity.P_PARTIAL,
        species=Species("Ti", Phase.G),
    )
    observation = F.observation(
        "openimcc-ti-gas-positive",
        "openimcc-test",
        identity,
        Decimal("1"),
    )
    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        isolated=False,
    )

    from simulator.melt_backend.imcc_sf04 import openimcc_bridge

    wt_pct = composition_wt_pct(composition)
    assert wt_pct is not None
    melt = evaluate(
        wt_pct,
        2200.0,
        pack=openimcc_bridge._load_pack("v1.0.2"),
        basis_type="wt",
        allow_extrapolation=True,
        allow_out_of_envelope=True,
    )
    gas = evaluate_gas(
        dict(zip(melt.parent_oxides, melt.parent_activity, strict=True)),
        2200.0,
        1e-9,
        load_gas_datapack(),
        parent_oxides=melt.parent_oxides,
        allow_extrapolation=True,
    )
    reported = dict(gas)

    assert prediction.value is not None
    assert math.isfinite(float(prediction.value))
    assert prediction.value > 0
    assert prediction.refusal_reason is None
    assert "Ti" in reported
    # 1e-4 Pa fO2 becomes 1e-9 bar; evaluate_gas returns bar, and the scorer
    # converts P(Ti) back to Pa (1 bar = 1e5 Pa).
    assert float(prediction.value) == pytest.approx(reported["Ti"] * 1.0e5)


def test_openimcc_domain_policy_matches_legacy_imcc() -> None:
    _require_openimcc()
    pot = _binary_probe()
    po2 = Po2Request(mode="commanded", po2_bar=1.0e-9)
    legacy = equilibrate_cell(
        open_battery_engine("imcc_sf04"),
        pot,
        temperature_K=1600.0,
        po2=po2,
        isolated=False,
    )
    current = equilibrate_cell(
        open_battery_engine("openimcc"),
        pot,
        temperature_K=1600.0,
        po2=po2,
        isolated=False,
    )
    assert legacy.status == current.status == "ok"
    assert legacy.authority == current.authority == "extrapolated"
    assert "imcc_temperature_extrapolated" in {
        str(row.get("kind")) for row in legacy.notices
    }
    assert "openimcc_temperature_extrapolated" in {
        str(row.get("kind")) for row in current.notices
    }
    assert any(
        str(row.get("authority")) == "extrapolated" for row in current.notices
    )


def test_openimcc_plante_candidates_equal_packaged_hand_values() -> None:
    _require_openimcc()
    scratch = _scratch_path()
    if scratch is None:
        pytest.skip("Plante hand-value scratch file is not present")
    payload = json.loads(scratch.read_text(encoding="utf-8"))
    hand_rows = payload["hand_rows"]
    assert len(hand_rows) == 162
    handle = open_battery_engine("openimcc")
    assert handle.available, handle.unavailable_reason
    experiment = F.kems_experiment(experiment_id="openimcc-plante-test")
    deltas: list[float] = []
    measured_residuals: list[float] = []
    for row in hand_rows:
        composition_wt = {
            "K2O": float(row["K2O_wt_pct"]),
            "SiO2": float(row["SiO2_wt_pct"]),
        }
        _composition_kg, composition_mol = composition_kg_and_mol(composition_wt)
        composition = Composition(
            basis="ordered_complete_mole_inventory",
            components=tuple(
                (name, Decimal(str(amount)))
                for name, amount in composition_mol.items()
            ),
            amount_basis=AmountBasis.MOLE_FRACTION,
        )
        identity = F.activity_identity(
            formula="K2O",
            component_basis="K2O",
            composition=composition,
            T_K=Decimal(str(row["T_K"])),
            fO2_Pa=Decimal(str(0.226 * float(row["measured_P_K_Pa"]))),
        )
        identity = replace(
            identity,
            quantity=Quantity.P_PARTIAL,
            species=Species("K", Phase.G),
        )
        reference = F.observation(
            row["observation_id"],
            experiment.experiment_id,
            identity,
            Decimal(str(row["measured_P_K_Pa"])),
        )
        prediction = predict_with_engine(
            Engine.OPENIMCC,
            reference,
            handles={"openimcc": handle},
            isolated=False,
        )
        assert prediction.value is not None, prediction.refusal_detail
        predicted = float(prediction.value)
        if not deltas:
            candidate = candidate_observation(reference, prediction)
            assert candidate.engine is not None
            assert candidate.engine.version == handle.identity["version"]
            assert any(
                str(source).startswith("openimcc-pack-digest:")
                for source in candidate.engine.coefficient_sources
            )
            assert any(
                str(source).startswith("openimcc-gas-table:")
                for source in candidate.engine.coefficient_sources
            )
        hand = float(row["hand_P_K_Pa"])
        deltas.append(math.log10(predicted / hand))
        measured_residuals.append(
            math.log10(predicted / float(row["measured_P_K_Pa"]))
        )
    assert len(deltas) == 162
    assert max(abs(delta) for delta in deltas) <= 1.0e-9
    assert statistics_median(measured_residuals) == pytest.approx(0.093, abs=0.01)


def test_openimcc_gas_table_mutation_to_vaporock_breaks_row_equality(monkeypatch) -> None:
    _require_openimcc()
    scratch = _scratch_path()
    if (
        scratch is None
        or VAPOROCK_ROOT is None
        or not (VAPOROCK_ROOT / "src/vaporock/data/JANAF-vapor-data-full.csv").is_file()
    ):
        pytest.skip("Plante scratch data or VapoRock checkout is not present")
    row = json.loads(scratch.read_text(encoding="utf-8"))["hand_rows"][0]
    composition_wt = {"K2O": float(row["K2O_wt_pct"]), "SiO2": float(row["SiO2_wt_pct"])}
    composition_kg, composition_mol = composition_kg_and_mol(composition_wt)
    from simulator.diagnostic_helpers.binary_pot_battery import _OpenImccBatteryBackend

    monkeypatch.delenv("OPENIMCC_VAPOROCK_ROOT", raising=False)
    packaged = _OpenImccBatteryBackend("openimcc")
    packaged_result = packaged.equilibrate(
        temperature_C=float(row["T_K"]) - 273.15,
        composition_kg=composition_kg,
        composition_mol=composition_mol,
        fO2_log=math.log10(0.226 * float(row["measured_P_K_Pa"]) / 1.0e5),
    )
    hand = float(row["hand_P_K_Pa"])
    assert abs(math.log10(float(packaged_result.vapor_pressures_Pa["K"]) / hand)) <= 1.0e-9

    monkeypatch.setenv("OPENIMCC_VAPOROCK_ROOT", str(VAPOROCK_ROOT))
    mutated = _OpenImccBatteryBackend("openimcc")
    mutated_result = mutated.equilibrate(
        temperature_C=float(row["T_K"]) - 273.15,
        composition_kg=composition_kg,
        composition_mol=composition_mol,
        fO2_log=math.log10(0.226 * float(row["measured_P_K_Pa"]) / 1.0e5),
    )
    assert "VapoRock" in mutated._identity["gas_table_source"]
    assert abs(math.log10(float(mutated_result.vapor_pressures_Pa["K"]) / hand)) > 1.0e-9


def statistics_median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])
