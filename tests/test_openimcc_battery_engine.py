"""Focused contract tests for the optional openimcc battery producer."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
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
    ScoreContext,
    predict_with_engine,
    score_store,
)
from simulator.battery.migrate import load_migrated_store, load_yaml
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


def test_imcc_battery_emits_notice_for_strict_envelope_edge() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import _ImccBatteryBackend

    backend = _ImccBatteryBackend("imcc_sf04")
    for x_k2o, expects_notice in ((0.500002, True), (0.5, False)):
        result = backend.equilibrate(
            temperature_C=1800.0 - 273.15,
            composition_kg={
                "K2O": x_k2o * 94.196,
                "SiO2": (1.0 - x_k2o) * 60.0843,
            },
        )
        notices = result.diagnostics["imcc_notices"]
        matching = [
            notice for notice in notices
            if notice["kind"] == "imcc_composition_outside_validated_envelope"
        ]
        assert bool(matching) is expects_notice
        if matching:
            assert matching[0]["authority"] == "extrapolated"


def test_imcc_battery_surfaces_complex_saturation_notice() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _ImccBatteryBackend,
        _OpenImccBatteryBackend,
    )

    for name in ("imcc_sf04", "imcc_sf04_ext", "openimcc"):
        backend = (
            _OpenImccBatteryBackend("openimcc")
            if name == "openimcc"
            else _ImccBatteryBackend(name)
        )
        for x_k2o in (0.50, 0.55):
            binary_mol = {"K2O": x_k2o, "SiO2": 1.0 - x_k2o}
            binary_kg = {
                "K2O": binary_mol["K2O"] * 94.196 / 1000.0,
                "SiO2": binary_mol["SiO2"] * 60.0843 / 1000.0,
            }
            saturated = backend.equilibrate(
                temperature_C=1800.0 - 273.15,
                composition_kg=binary_kg,
                composition_mol=binary_mol,
            )
            assert any(
                row.get("kind") == "imcc_complex_saturation"
                and row.get("flag", "").startswith("species-coverage-edge")
                and row.get("acid_sink_ratio") is not None
                for row in saturated.imcc_notices
            )


@pytest.mark.parametrize(
    "composition_mol",
    [
        {"K2O": 0.500002, "SiO2": 0.499998},
        {"K2O": 0.25, "Na2O": 0.250001, "SiO2": 0.499999},
        {"K2O": 0.25, "Na2O": 0.250005, "SiO2": 0.499995},
    ],
)
def test_openimcc_battery_keeps_package_envelope_slack(composition_mol) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import _OpenImccBatteryBackend

    backend = _OpenImccBatteryBackend("openimcc")
    molar_mass = {"K2O": 94.196, "Na2O": 61.9789, "SiO2": 60.0843}
    result = backend.equilibrate(
        temperature_C=1800.0 - 273.15,
        composition_kg={
            oxide: amount * molar_mass[oxide] / 1000.0
            for oxide, amount in composition_mol.items()
        },
    )

    assert not any(
        notice["kind"] == "openimcc_composition_outside_validated_envelope"
        for notice in result.diagnostics["imcc_notices"]
    )
    assert result.diagnostics["authority"] is None


def _scratch_path() -> Path | None:
    return PLANTE_HAND_ROWS if PLANTE_HAND_ROWS.is_file() else None


def _plante_score_context() -> ScoreContext:
    source_id = "kems-042-plante-1979"
    work_file = "10.6028_nbs.sp.561v1.yaml"
    extract_file = f"{source_id}.yaml"
    coefficient_source_id = "sf04-magma-companion-workbook"
    coefficient_work_file = "10.1016_j.icarus.2003.08.023.yaml"
    coefficient_extract_file = f"{coefficient_source_id}.yaml"
    with tempfile.TemporaryDirectory(prefix="plante-score-context-") as temp_dir:
        root = Path(temp_dir)
        for relative, source in (
            (Path("data/literature/works") / work_file,
             REPO_ROOT / "data/literature/works" / work_file),
            (Path("data/literature/extracts-v2") / extract_file,
             REPO_ROOT / "data/literature/extracts-v2" / extract_file),
            (Path("data/literature/works") / coefficient_work_file,
             REPO_ROOT / "data/literature/works" / coefficient_work_file),
            (Path("data/literature/extracts-v2") / coefficient_extract_file,
             REPO_ROOT / "data/literature/extracts-v2" / coefficient_extract_file),
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
        works, experiments, observations = load_migrated_store(root)
    source_doc = load_yaml(REPO_ROOT / "data/literature/extracts" / extract_file)
    coefficient_doc = load_yaml(
        REPO_ROOT / "data/literature/extracts" / coefficient_extract_file
    )
    origins = {
        observation_id: extract_file
        for observation_id in observations
        if observation_id.startswith(f"{source_id}::")
    }
    origins.update(
        {
            observation_id: coefficient_extract_file
            for observation_id in observations
            if observation_id.startswith(f"{coefficient_source_id}::")
        }
    )
    return ScoreContext(
        works=works,
        experiments=experiments,
        observations=observations,
        origins=origins,
        extract_review={
            source_id: source_doc.get("review_status"),
            coefficient_source_id: coefficient_doc.get("review_status"),
        },
    )


def _require_simulator_janaf_gas() -> None:
    from simulator.melt_backend.imcc_sf04.gas import (
        DEFAULT_GAS_DATABASE_PATH,
        load_gas_datapack,
    )

    try:
        load_gas_datapack()
    except Exception as exc:  # noqa: BLE001 - capability probe for optional gas tables
        pytest.skip(
            f"simulator VapoRock JANAF gas tables are not resolvable at "
            f"{DEFAULT_GAS_DATABASE_PATH}: {type(exc).__name__}: {exc}"
        )


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


def test_imcc_battery_reports_single_cation_gamma() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _ImccBatteryBackend,
        _OpenImccBatteryBackend,
    )

    composition_wt = {
        "SiO2": 45.94,
        "Al2O3": 16.0,
        "FeO": 10.67,
        "MgO": 7.09,
        "CaO": 11.21,
        "TiO2": 1.79,
        "Na2O": 2.27,
        "K2O": 2.49,
    }
    composition_kg, composition_mol = composition_kg_and_mol(composition_wt)
    kwargs = {
        "temperature_C": 1673.15 - 273.15,
        "composition_kg": composition_kg,
        "composition_mol": composition_mol,
    }
    for name in ("imcc_sf04", "imcc_sf04_ext", "openimcc"):
        backend = (
            _OpenImccBatteryBackend("openimcc")
            if name == "openimcc"
            else _ImccBatteryBackend(name)
        )
        result = backend.equilibrate(**kwargs)
        gamma = result.reported_activity_coefficients["KO0.5"]
        assert gamma > 0.0
        assert result.activity_coefficient_details["KO0.5"] == {
            "value": gamma,
            "coefficient_basis": "single_cation",
            "standard_state": {
                "convention": "raoultian_pure_endmember",
                "phase": "l",
                "component_basis": "KO0.5",
            },
        }


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
import simulator
import simulator.backends
import engines.builtin.vapor_pressure
from simulator.diagnostic_helpers.binary_pot_battery import open_battery_engine
handles = {name: open_battery_engine(name) for name in ("openimcc", "imcc_sf04", "imcc_sf04_ext")}
print(json.dumps({name: {"available": handle.available, "reason": handle.unavailable_reason}
                  for name, handle in handles.items()}))
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
    assert set(payload) == {"openimcc", "imcc_sf04", "imcc_sf04_ext"}
    reasons = []
    for entry in payload.values():
        assert entry["available"] is False
        assert "openimcc_not_importable" in entry["reason"]
        assert "remedy:" in entry["reason"]
        reasons.append(entry["reason"])
    assert reasons[1:] == reasons[:1] * 2


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

    from simulator.melt_backend import openimcc_bridge

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


def test_plante_candidate_lineage_unchanged_by_kernel_switch() -> None:
    _require_simulator_janaf_gas()
    context = _plante_score_context()
    expected = {
        Engine.IMCC_SF04: (True, "independent"),
        # Measured on green 6925ccacd, which adds the t-1020 lineage mapping.
        Engine.OPENIMCC: (True, "independent"),
    }
    for engine, lineage in expected.items():
        residuals, candidates = score_store(
            context,
            engines=(engine,),
            work_id="kems-042-plante-1979",
        )
        rows = [
            (residual, candidates[residual.candidate])
            for residual in residuals
            if residual.candidate in candidates
        ]
        assert len(rows) == 162
        assert all(candidate.engine is not None for _, candidate in rows)
        assert {
            (candidate.engine.lineage_complete, residual.source_relation.value)
            for residual, candidate in rows
            if candidate.engine is not None
        } == {lineage}


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
