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
    Authority,
    Engine,
    NoticeKind,
    Phase,
    PerBasis,
    MetricOperation,
    Quantity,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.identity import Exposure, SweepIdentity
from simulator.battery.migrate import load_yaml
from simulator.battery.score import (
    ENGINE_CHANNELS,
    SCORE_ENGINE_SET,
    candidate_observation,
    compile_residual,
    composition_wt_pct,
    load_score_context,
    ScoreContext,
    predict_with_engine,
    score_store,
    flagged_stratum_rows,
    headline_rows,
)
from simulator.battery.records import Composition, Species, State, Value
from simulator.battery.validate import validate_corpus
from simulator.diagnostic_helpers.binary_pot_battery import (
    BATTERY_ENGINE_NAMES,
    BinaryPot,
    Po2Request,
    PO2_OXYGEN_BALANCE_EFFUSION,
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


def _require_oxygen_balance() -> None:
    # The oxygen-balance effusion solve landed in openimcc 23d7842; older installs
    # (e.g. 627bbc5) lack it and must SKIP these solve assertions. The typed
    # unavailable path is covered separately by
    # test_imcc_missing_generic_balance_solver_is_typed.
    _require_openimcc()
    import openimcc

    if not hasattr(openimcc, "oxygen_balance_from_pressure_model"):
        pytest.skip("installed openimcc lacks oxygen_balance_from_pressure_model")


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


def test_imcc_battery_passes_moles_and_preserves_genuine_wt_input(monkeypatch) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import _ImccBatteryBackend
    from simulator.melt_backend.imcc_sf04 import adapter

    backend = _ImccBatteryBackend("imcc_sf04")
    original_evaluate = adapter.evaluate
    observed = []

    def capture(composition, temperature_K, pack, **kwargs):
        observed.append((dict(composition), kwargs["basis_type"]))
        return original_evaluate(composition, temperature_K, pack, **kwargs)

    monkeypatch.setattr(adapter, "evaluate", capture)
    strict_result = adapter.evaluate(
        {"K2O": 0.5, "SiO2": 0.5},
        1800.0,
        backend._pack,
        basis=1.0,
        basis_type="mol",
    )
    assert strict_result.labels.envelope_status == "inside"
    assert strict_result.parent_mol[strict_result.parent_oxides.index("K2O")] == 0.5
    mol_result = backend.equilibrate(
        temperature_C=1800.0 - 273.15,
        composition_kg={"K2O": 0.0470978, "SiO2": 0.0300415},
        composition_mol={"K2O": 0.5, "SiO2": 0.5},
    )
    assert observed[-1] == ({"K2O": 0.5, "SiO2": 0.5}, "mol")
    assert not any(
        row["kind"] == "imcc_composition_outside_validated_envelope"
        for row in mol_result.diagnostics["imcc_notices"]
    )

    wt_result = backend.equilibrate(
        temperature_C=1800.0 - 273.15,
        composition_kg={"K2O": 0.07, "SiO2": 0.93},
    )
    assert observed[-1] == ({"K2O": 7.000000000000001, "SiO2": 93.0}, "wt")
    assert wt_result.status == "ok"
    wt_reference = original_evaluate(
        {"K2O": 7.000000000000001, "SiO2": 93.0},
        1800.0,
        backend._pack,
        basis=100.0,
        basis_type="wt",
        enable_sp_extension=False,
        allow_extrapolation=True,
        allow_out_of_envelope=True,
    )
    assert wt_result.activity_coefficients == {
        name: float(value)
        for name, value in zip(
            wt_reference.parent_oxides, wt_reference.parent_activity, strict=True
        )
        if float(value) > 0.0
    }


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


def test_openimcc_battery_prefers_supplied_mole_inventory(monkeypatch) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import _OpenImccBatteryBackend

    backend = _OpenImccBatteryBackend("openimcc")
    original_evaluate = backend._bridge.evaluate
    observed = []

    def capture(**kwargs):
        observed.append(kwargs)
        return original_evaluate(**kwargs)

    monkeypatch.setattr(backend._bridge, "evaluate", capture)
    exact_mol = {"K2O": 0.5, "SiO2": 0.5}
    result = backend.equilibrate(
        temperature_C=1800.0 - 273.15,
        composition_kg={"K2O": 0.0470978, "SiO2": 0.0300415},
        composition_mol=exact_mol,
    )
    assert observed[-1]["composition_mol"] == exact_mol
    assert "composition_kg" not in observed[-1]
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
            (Path("data/literature/extracts") / extract_file,
             REPO_ROOT / "data/literature/extracts" / extract_file),
            (Path("data/literature/works") / coefficient_work_file,
             REPO_ROOT / "data/literature/works" / coefficient_work_file),
            (Path("data/literature/extracts-v2") / coefficient_extract_file,
             REPO_ROOT / "data/literature/extracts-v2" / coefficient_extract_file),
            (Path("data/literature/extracts") / coefficient_extract_file,
             REPO_ROOT / "data/literature/extracts" / coefficient_extract_file),
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
        return load_score_context(root)


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


def _zhang_k_case(tmp_path: Path, temperature: str):
    from tests.battery.test_migrate import _migrate_real_extract

    migration = _migrate_real_extract(tmp_path, "kems-006-zhang-2021.yaml")
    reference = next(
        observation
        for observation in migration.observations.values()
        if "table4_K_evaporation_coefficients" in observation.observation_id
        and str(observation.identity.temperature_K.value) == temperature
    )
    # The source row leaves these kinetic axes unknown. Supply explicit test
    # inputs for identity matching and the real IMCC request; keep its value,
    # provenance, composition, and experiment unchanged.
    reference = replace(
        reference,
        identity=replace(
            reference.identity,
            subtype=State.of("langmuir_alpha"),
            per=State.of(PerBasis.DIMENSIONLESS),
            reservoir=State.of(Species("K", Phase.G)),
            fO2_Pa=State.of(Decimal("1e-4")),
            total_pressure_Pa=State.of(Decimal("0.1")),
            sweep_gas=State.of(
                SweepIdentity(
                    species="N2",
                    flow_sccm=State.of(Decimal("1")),
                    partial_pressure_Pa=State.of(Decimal("1")),
                )
            ),
            exposure=State.of(
                Exposure(
                    area_m2=State.of(Decimal("1")),
                    duration_s=State.of(Decimal("1")),
                )
            ),
        ),
        point_conditions={
            **(reference.point_conditions or {}),
            "fO2_Pa": F.located(Value.point_of(Decimal("1e-4"))),
        },
    )
    context = ScoreContext(
        works=migration.works,
        experiments=migration.experiments,
        observations=migration.observations,
        extract_review={"kems-006-zhang-2021": "draft"},
        hostname="test",
    )
    return context, reference


@pytest.mark.parametrize(
    ("temperature", "expected_alpha"),
    [
        ("1473.15", 20.0),
        ("1673.15", 45.0),
    ],
)
def test_openimcc_zhang_implied_alpha_uses_coefficient_details(
    tmp_path: Path,
    temperature: str,
    expected_alpha: float,
) -> None:
    _require_openimcc()
    context, reference = _zhang_k_case(tmp_path, temperature)
    handle = open_battery_engine("openimcc")
    assert handle.available, handle.unavailable_reason

    residual, candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
        handles={"openimcc": handle},
    )
    assert candidate is not None
    assert candidate.authority is Authority.EXTRAPOLATED
    assert residual.status is ResidualStatus.MISMATCH
    assert residual.numeric is not None
    assert residual.numeric.verdict == "physically_impossible"
    assert float(10 ** float(residual.numeric.value)) == pytest.approx(
        expected_alpha, rel=0.03
    )


@pytest.mark.parametrize(
    "details_mode",
    ["absent", "parent_oxide", "wrong_phase", "wrong_component_basis", "wrong_convention"],
)
def test_openimcc_zhang_implied_alpha_refuses_bad_coefficient_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    details_mode: str,
) -> None:
    _require_openimcc()
    context, reference = _zhang_k_case(tmp_path, "1673.15")
    handle = open_battery_engine("openimcc")

    from simulator.diagnostic_helpers import binary_pot_battery

    real_equilibrate_cell = binary_pot_battery.equilibrate_cell

    def altered_equilibrate_cell(*args, **kwargs):
        cell = real_equilibrate_cell(*args, **kwargs)
        details = dict(cell.melt_activity_coefficient_details)
        if details_mode == "absent":
            details.pop("KO0.5", None)
        elif details_mode == "parent_oxide":
            details["KO0.5"] = {
                **details["KO0.5"],
                "coefficient_basis": "parent_oxide",
            }
        elif details_mode == "wrong_phase":
            details["KO0.5"] = {
                **details["KO0.5"],
                "standard_state": {
                    **details["KO0.5"]["standard_state"],
                    "phase": "cr",
                },
            }
        elif details_mode == "wrong_component_basis":
            details["KO0.5"] = {
                **details["KO0.5"],
                "standard_state": {
                    **details["KO0.5"]["standard_state"],
                    "component_basis": "K2O",
                },
            }
        else:
            details["KO0.5"] = {
                **details["KO0.5"],
                "standard_state": {
                    **details["KO0.5"]["standard_state"],
                    "convention": "henrian_liquid",
                },
            }
        return replace(cell, melt_activity_coefficient_details=details)

    monkeypatch.setattr(binary_pot_battery, "equilibrate_cell", altered_equilibrate_cell)
    residual, candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
        handles={"openimcc": handle},
    )
    assert candidate is None
    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.COEFFICIENT_BASIS_MISMATCH


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


def test_openimcc_battery_solves_plante_oxygen_balance_anchor() -> None:
    _require_oxygen_balance()
    _require_openimcc()
    plante_melt = BinaryPot(
        pot_id="openimcc-plante-o2-balance",
        kato_1993_table4_system=None,
        why="Plante oxygen-balance anchor",
        composition_wt_pct={"K2O": 7.60, "SiO2": 92.40},
    )
    cell = equilibrate_cell(
        open_battery_engine("openimcc"),
        plante_melt,
        temperature_K=1500.0,
        po2=Po2Request(mode=PO2_OXYGEN_BALANCE_EFFUSION, po2_bar=None),
        isolated=False,
    )
    assert cell.status == "ok", cell.engine_reason
    notice = next(
        row for row in cell.notices
        if row.get("kind") == "fo2_oxygen_balance_effusion_solved"
    )
    ratio = notice["pO2_bar"] * 1.0e5 / cell.gas_partial_pressures_Pa["K"]
    assert ratio == pytest.approx(0.22489, abs=1e-5)
    assert notice["relative_residual"] < 1.0e-8
    assert notice["bracket_log10_bar"] == [-30.0, 0.0]
    assert notice["cell_material"] is None
    assert notice["cell_oxide_flux_fraction"] == 0.0
    assert notice["buffer_pinned"] is False
    assert notice["buffer_pO2_bar"] is None
    assert notice["dominant_O_carriers"]
    assert notice["dominant_metal_carriers"]


@pytest.mark.parametrize("engine_name", ("imcc_sf04", "imcc_sf04_ext"))
def test_janaf_imcc_solves_plante_oxygen_balance_anchor(engine_name: str) -> None:
    _require_oxygen_balance()
    plante_melt = BinaryPot(
        pot_id="openimcc-plante-o2-balance",
        kato_1993_table4_system=None,
        why="Plante oxygen-balance anchor",
        composition_wt_pct={"K2O": 7.60, "SiO2": 92.40},
    )
    cell = equilibrate_cell(
        open_battery_engine(engine_name),
        plante_melt,
        temperature_K=1500.0,
        po2=Po2Request(mode=PO2_OXYGEN_BALANCE_EFFUSION, po2_bar=None),
        isolated=False,
    )
    assert cell.status == "ok", cell.engine_reason
    notice = next(
        row for row in cell.notices
        if row.get("kind") == "fo2_oxygen_balance_effusion_solved"
    )
    ratio = notice["pO2_bar"] * 1.0e5 / cell.gas_partial_pressures_Pa["K"]
    assert ratio == pytest.approx(0.22487, abs=1e-5)
    assert notice["relative_residual"] < 1.0e-8
    assert notice["bracket_log10_bar"] == [-30.0, 0.0]
    assert notice["cell_material"] is None
    assert notice["cell_oxide_flux_fraction"] == 0.0
    assert notice["buffer_pinned"] is False
    assert notice["buffer_pO2_bar"] is None
    assert notice["dominant_O_carriers"]
    assert notice["dominant_metal_carriers"]


def test_openimcc_hot_k_rich_melt_returns_molecular_flow_refusal() -> None:
    _require_oxygen_balance()
    hot_k_rich = BinaryPot(
        pot_id="openimcc-hot-k-rich",
        kato_1993_table4_system=None,
        why="oxygen balance molecular-flow ceiling refusal",
        composition_wt_pct={"K2O": 95.0, "SiO2": 5.0},
    )
    cell = equilibrate_cell(
        open_battery_engine("openimcc"),
        hot_k_rich,
        temperature_K=2600.0,
        po2=Po2Request(mode=PO2_OXYGEN_BALANCE_EFFUSION, po2_bar=None),
        isolated=False,
    )
    assert cell.status == "refusal"
    assert cell.refusal_reason == "imcc_gas_oxygen_balance_failed"
    assert "above pO2 = 1 bar" in str(cell.engine_reason)


def test_reactive_cell_wo3_dominated_balance_matches_analytic_root(monkeypatch) -> None:
    import openimcc
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    T = 1933.0
    delta_g_j_mol = -250_000.0
    K = math.exp(-delta_g_j_mol / (8.31446261815324 * T))
    target_po2_bar = 1e-8
    si_mass = 28.085
    wo3_mass = 183.84 + 3 * 15.999
    si_o_pressure_scale = (
        3.0
        * K
        * target_po2_bar**2.5
        * math.sqrt(si_mass)
        / (2.0 * math.sqrt(wo3_mass))
    )
    monkeypatch.setattr(
        battery,
        "_cell_oxide_thermodynamics",
        lambda _material, _temperature: (
            {"WO3": (delta_g_j_mol, 3.0)},
            1.0,
            {"WO3": {"table_id": "O-068", "source_sha256": "test"}},
        ),
    )
    species = openimcc.oxygen_balance_species_metadata({"Si": "SiO2"})

    def pressure_model(logp: float) -> dict[str, float]:
        return {"Si": si_o_pressure_scale * 10.0 ** (-logp)}

    solved, pressures, _balance, fraction, info = battery._solve_cell_oxygen_balance(
        pressure_model,
        species,
        temperature_K=T,
        cell_material="W",
        oxygen_balance_from_pressure_model=openimcc.oxygen_balance_from_pressure_model,
    )

    assert solved == pytest.approx(target_po2_bar, rel=1e-8)
    assert pressures["WO3"] > 0
    assert fraction == pytest.approx(1.0, abs=1e-8)
    assert info["buffer_pinned"] is False


def test_reactive_cell_buffer_pins_at_selected_buffer(monkeypatch) -> None:
    import openimcc
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    monkeypatch.setattr(
        battery,
        "_cell_oxide_thermodynamics",
        lambda _material, _temperature: (
            {"WO3": (-250_000.0, 3.0)},
            -15.0,
            {"WO3": {"table_id": "O-068", "source_sha256": "test"}},
        ),
    )
    species = openimcc.oxygen_balance_species_metadata({"Si": "SiO2"})

    def pressure_model(logp: float) -> dict[str, float]:
        return {"Si": 1e-12 * 10.0 ** (-logp)}

    solved, _pressures, _balance, _fraction, info = battery._solve_cell_oxygen_balance(
        pressure_model,
        species,
        temperature_K=1933.0,
        cell_material="W",
        oxygen_balance_from_pressure_model=openimcc.oxygen_balance_from_pressure_model,
    )

    assert info["buffer_pinned"] is True
    assert solved == pytest.approx(10.0**-15.0)
    assert info["buffer_pO2_bar"] == pytest.approx(solved)


def test_unknown_cell_material_is_a_typed_refusal() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import EngineHandle

    cell = equilibrate_cell(
        EngineHandle(
            name="openimcc",
            backend=None,
            available=False,
            unavailable_reason="engine is unavailable",
            takes_fo2=False,
            supports_intrinsic_fo2=False,
        ),
        BinaryPot(
            pot_id="unknown-cell-material",
            kato_1993_table4_system=None,
            why="invalid reactive cell request",
            composition_wt_pct={"K2O": 43.94, "SiO2": 56.06},
        ),
        temperature_K=1933.0,
        po2=Po2Request(
            mode=PO2_OXYGEN_BALANCE_EFFUSION,
            po2_bar=None,
            cell_material="Pt",
        ),
        isolated=False,
    )
    assert cell.status == "refusal"
    assert cell.refusal_reason == "oxygen_balance_cell_material_invalid"


def test_inert_po2_request_payload_keeps_legacy_shape() -> None:
    assert Po2Request(mode=PO2_OXYGEN_BALANCE_EFFUSION, po2_bar=None).as_payload() == {
        "mode": PO2_OXYGEN_BALANCE_EFFUSION,
        "po2_bar": None,
    }


def test_cell_material_distinguishes_reused_cell_identity() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import _cell_identity_key

    base = {
        "pot_id": "reuse-key",
        "engine": "openimcc",
        "temperature_K": 1933.0,
        "po2": {"mode": PO2_OXYGEN_BALANCE_EFFUSION, "po2_bar": None},
    }
    assert _cell_identity_key(base) != _cell_identity_key(
        {**base, "po2": {**base["po2"], "cell_material": "W"}}
    )


@pytest.mark.parametrize("engine_name", ("imcc_sf04", "openimcc"))
@pytest.mark.parametrize(
    ("cell_material", "oxide_gas", "table_id"),
    (("W", "WO3", "O-068"), ("Mo", "MoO3", "Mo-017")),
)
def test_reactive_cell_adds_shared_janaf_oxides_to_engine_pressure_model(
    engine_name: str, cell_material: str, oxide_gas: str, table_id: str
) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _ImccBatteryBackend,
        _OpenImccBatteryBackend,
    )

    backend = (
        _OpenImccBatteryBackend("openimcc")
        if engine_name == "openimcc"
        else _ImccBatteryBackend("imcc_sf04")
    )
    result = backend.equilibrate(
        temperature_C=1933.0 - 273.15,
        composition_kg={"K2O": 43.94, "SiO2": 56.06},
        composition_mol={"K2O": 0.5, "SiO2": 0.5},
        po2_request=Po2Request(
            mode=PO2_OXYGEN_BALANCE_EFFUSION,
            po2_bar=None,
            cell_material=cell_material,
        ),
    )
    if engine_name == "openimcc":
        notices = result.diagnostics["imcc_notices"]
        gas_pressures = result.vapor_pressures_Pa
    else:
        notices = result.imcc_notices
        gas_pressures = result.vapor_pressures_Pa
    solved = next(
        row for row in notices
        if row.get("kind") == "fo2_oxygen_balance_effusion_solved"
    )
    assert solved["cell_material"] == cell_material
    assert solved["cell_oxide_flux_fraction"] > 0.0
    assert oxide_gas in gas_pressures
    assert solved["cell_oxide_janaf_sources"][oxide_gas]["table_id"] == table_id


def test_stolyarova_1991_w_cell_pressure_and_residual_report() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _ImccBatteryBackend,
        _OpenImccBatteryBackend,
    )

    extract = load_yaml(REPO_ROOT / "data/literature/extracts/kems-053-stolyarova-1991.yaml")

    def observation_points(species: str, observation_id: str) -> dict[float, float]:
        observation = next(
            row
            for row in extract["species"][species]["observations"]
            if row["observation_id"] == observation_id
        )
        return {
            float(point["SiO2_mole_fraction_as_printed"]): float(point["pressure_atm"])
            for point in observation["values"]["points"]
        }

    ca_atm = observation_points(
        "Ca", "stolyarova_1991_ca_partial_pressure_1993k_complete_evaporation"
    )
    sio_atm = observation_points(
        "SiO", "stolyarova_1991_sio_partial_pressure_1933k_ion_comparison"
    )
    engines = {
        "imcc_sf04": _ImccBatteryBackend("imcc_sf04"),
        "openimcc": _OpenImccBatteryBackend("openimcc"),
    }
    rows = []

    def solve(engine: str, x_sio2: float, temperature_K: float, material: str | None):
        result = engines[engine].equilibrate(
            temperature_C=temperature_K - 273.15,
            composition_mol={"CaO": 1.0 - x_sio2, "SiO2": x_sio2},
            po2_request=Po2Request(
                mode=PO2_OXYGEN_BALANCE_EFFUSION,
                po2_bar=None,
                cell_material=material,
            ),
        )
        notices = (
            result.diagnostics["imcc_notices"]
            if engine == "openimcc"
            else result.imcc_notices
        )
        solved = next(
            row
            for row in notices
            if row.get("kind") == "fo2_oxygen_balance_effusion_solved"
        )
        return solved, result.vapor_pressures_Pa

    for engine in engines:
        for x_sio2 in sorted(ca_atm.keys() & sio_atm.keys(), reverse=True):
            solved_W, gas_1933 = solve(engine, x_sio2, 1933.0, "W")
            solved_inert, _ = solve(engine, x_sio2, 1933.0, None)
            solved_Ca, gas_1993 = solve(engine, x_sio2, 1993.0, "W")
            pO2_atm = solved_W["pO2_bar"] * 1.0e5 / 101325.0
            rows.append(
                {
                    "engine": engine,
                    "X_SiO2": x_sio2,
                    "W_pO2_atm_1933K": pO2_atm,
                    "dex_vs_inferred_range_2.9e-12_to_6.6e-12": [
                        math.log10(pO2_atm / 6.6e-12),
                        math.log10(pO2_atm / 2.9e-12),
                    ],
                    "inert_to_W_pull_down_dex": math.log10(
                        solved_inert["pO2_bar"] / solved_W["pO2_bar"]
                    ),
                    "buffer_pinned": solved_W["buffer_pinned"],
                    "buffer_phase": solved_W["cell_oxide_janaf_sources"][
                        "buffer_phase"
                    ]["formula"],
                    "Ca_1993K_residual_dex": math.log10(
                        (gas_1993["Ca"] / 101325.0) / ca_atm[x_sio2]
                    ),
                    "SiO_1933K_residual_dex": math.log10(
                        (gas_1933["SiO"] / 101325.0) / sio_atm[x_sio2]
                    ),
                    "Ca_target_atm": ca_atm[x_sio2],
                    "SiO_target_atm": sio_atm[x_sio2],
                    "Ca_predicted_atm": gas_1993["Ca"] / 101325.0,
                    "SiO_predicted_atm": gas_1933["SiO"] / 101325.0,
                }
            )
    print("STOLYAROVA_W_CELL_REPORT=" + json.dumps(rows, sort_keys=True))
    assert len(rows) == 2 * len(ca_atm.keys() & sio_atm.keys())
    # Physics invariants only. The residuals against Stolyarova are REPORTED,
    # not asserted: the remaining +2.4–3.4 dex pO2 gap is a model/target
    # finding, and pinning it here would be test-forcing.
    by_point: dict[float, list[dict]] = {}
    for row in rows:
        # Cell oxides are oxygen SINKS (parentless carriers, w*k >= 0), so a
        # W cell can only lower the solved pO2 relative to an inert cell.
        assert row["inert_to_W_pull_down_dex"] >= 0.0, row
        # These roots sit below the W/WO2(cr) coexistence pO2.
        assert row["buffer_pinned"] is False, row
        by_point.setdefault(row["X_SiO2"], []).append(row)
    for x_sio2, pair in by_point.items():
        # Both engines share the same melt kernel lineage; they must agree.
        assert len(pair) == 2, x_sio2
        assert abs(
            math.log10(pair[0]["W_pO2_atm_1933K"] / pair[1]["W_pO2_atm_1933K"])
        ) < 0.01, x_sio2


def test_stolyarova_typed_w_cell_scores_through_cell_oxide_reservoir(tmp_path: Path) -> None:
    # Suppose my change is wrong in the way that matters most: restoring the
    # old reactive_cell_oxygen_reservoir refusal makes this red, because typed
    # [W] rows never request oxygen_balance_effusion with cell_material W.

    _require_simulator_janaf_gas()
    _require_oxygen_balance()
    _require_openimcc()
    from simulator.battery.oxygen_balance import IMCC_ENGINES
    from simulator.battery.score import quantity_token
    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(tmp_path, "kems-053-stolyarova-1991.yaml")
    context = ScoreContext(
        works=result.works,
        experiments=result.experiments,
        observations=result.observations,
        benches=result.benches,
        extract_review={"kems-053-stolyarova-1991": "draft"},
    )
    rows = [
        observation
        for observation in result.observations.values()
        if observation.source_id == "kems-053-stolyarova-1991"
        and quantity_token(observation.identity) is Quantity.P_PARTIAL
        and (observation.point_conditions or {}).get("composition") is not None
        and observation.point_conditions["composition"].state.is_value
    ]
    assert rows
    engines = (
        Engine.IMCC_SF04,
        Engine.IMCC_SF04_EXT,
        Engine.OPENIMCC,
    )
    assert set(engines) <= set(IMCC_ENGINES)
    blocked = {
        "missing_fO2",
        "reactive_cell_oxygen_reservoir",
        "cell_material_unknown",
    }
    residuals = []
    tagged = []
    handles = {engine.value: open_battery_engine(engine.value) for engine in engines}
    for observation in rows:
        for engine in engines:
            residual, _candidate = compile_residual(
                observation, engine, context=context, handles=handles
            )
            residuals.append(residual)
            tagged.append((engine.value, residual))
            detail = {} if residual.refusal is None else residual.refusal.detail
            reason = detail.get("reason")
            assert residual.refusal is None or (
                residual.refusal.reason is not RefusalReason.IDENTITY_INCOMPLETE
                and reason not in blocked
            ), (
                observation.observation_id,
                engine.value,
                None if residual.refusal is None else residual.refusal.reason.value,
                reason,
            )
            if residual.numeric is None:
                continue
            solved = [
                notice
                for notice in residual.notices
                if notice.origin == "engine:%s" % engine.value
                and notice.reason.startswith("fo2_oxygen_balance_effusion_solved:")
            ]
            assert len(solved) == 1, (observation.observation_id, engine.value)
            payload = json.loads(solved[0].reason.split(" ", 1)[1])
            assert payload.get("cell_material") == "W"
    report = []
    for engine in engines:
        bucket = [
            residual
            for name, residual in tagged
            if name == engine.value
            and residual.rail is not None
            and residual.rail.value == "vapour"
        ]
        scored = [residual for residual in bucket if residual.numeric is not None]
        dex = sorted(
            float(residual.numeric.value)
            for residual in scored
            if residual.numeric.operation is MetricOperation.DEX
        )
        mid = None if not dex else dex[len(dex) // 2]
        rms = None if not dex else (sum(value * value for value in dex) / len(dex)) ** 0.5
        report.append(
            {
                "engine": engine.value,
                "n": len(scored),
                "n_score_eligible": sum(1 for residual in scored if residual.score_eligible),
                "n_inside_band": sum(
                    1 for residual in scored if residual.status is ResidualStatus.MATCH
                ),
                "median_dex": mid,
                "rms_dex": rms,
            }
        )
    print("STOLYAROVA_SCORER_REPORT=" + json.dumps(report))
    print(
        "STOLYAROVA_FLAGGED="
        + json.dumps(flagged_stratum_rows(tuple(residuals), engines=engines), sort_keys=True)
    )
    assert all(row["n"] > 0 for row in report)




def test_imcc_missing_generic_balance_solver_is_typed(monkeypatch) -> None:
    import openimcc

    handle = open_battery_engine("imcc_sf04")
    assert handle.available
    original_getattr = openimcc.__getattr__

    def without_balance_solver(name: str):
        if name in {
            "oxygen_balance_from_pressure_model",
            "oxygen_balance_species_metadata",
        }:
            raise AttributeError(name)
        return original_getattr(name)

    monkeypatch.setattr(openimcc, "__getattr__", without_balance_solver)
    cell = equilibrate_cell(
        handle,
        BinaryPot(
            pot_id="imcc-missing-generic-o2-balance",
            kato_1993_table4_system=None,
            why="missing generic oxygen-balance core",
            composition_wt_pct={"K2O": 7.60, "SiO2": 92.40},
        ),
        temperature_K=1500.0,
        po2=Po2Request(mode=PO2_OXYGEN_BALANCE_EFFUSION, po2_bar=None),
        isolated=False,
    )
    assert cell.status == "refusal"
    assert cell.refusal_reason == "openimcc_oxygen_balance_unavailable", cell.engine_reason
    from simulator.melt_backend.openimcc_bridge import OPENIMCC_RECORDED_PIN

    # Assert against the recorded pin constant, not a literal sha, so the remedy
    # check tracks pin bumps (test_recorded_openimcc_pin_matches_pyproject_extra
    # keeps the constant equal to the pyproject extra).
    assert OPENIMCC_RECORDED_PIN in str(cell.engine_reason)


@pytest.mark.parametrize("engine_name", ("imcc_sf04", "imcc_sf04_ext", "openimcc"))
@pytest.mark.parametrize("temperature_K", (1500.0, 1800.0, 2200.0))
@pytest.mark.parametrize("po2_bar", (1.0e-9, 1.0e-6, 1.0e-3))
def test_commanded_po2_grid_remains_direct_gas_evaluation(
    engine_name: str,
    temperature_K: float,
    po2_bar: float,
) -> None:
    _require_openimcc()
    if engine_name == "openimcc":
        from openimcc import evaluate_gas, load_gas_datapack

        gas_pack = load_gas_datapack()
        parent_oxides = None
    else:
        from simulator.melt_backend.imcc_sf04.gas import (
            IMCC_PARENT_OXIDES,
            evaluate_gas,
            load_gas_datapack,
        )

        gas_pack = load_gas_datapack()
        parent_oxides = IMCC_PARENT_OXIDES
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _ImccBatteryBackend,
        _OpenImccBatteryBackend,
    )

    backend = (
        _OpenImccBatteryBackend("openimcc")
        if engine_name == "openimcc"
        else _ImccBatteryBackend(engine_name)
    )
    result = backend.equilibrate(
        temperature_C=temperature_K - 273.15,
        composition_kg={"K2O": 43.94, "SiO2": 56.06},
        composition_mol={"K2O": 0.5, "SiO2": 0.5},
        fO2_log=math.log10(po2_bar),
        po2_request=Po2Request(mode="commanded", po2_bar=po2_bar),
    )
    if parent_oxides is None:
        parent_oxides = tuple(result.activity_coefficients)
    expected_bar = evaluate_gas(
        result.activity_coefficients,
        temperature_K,
        po2_bar,
        gas_pack,
        parent_oxides=parent_oxides,
        allow_extrapolation=True,
    )
    assert result.vapor_pressures_Pa == {
        name: value * 1.0e5 for name, value in expected_bar.items() if value > 0.0
    }
    assert not any(
        row.get("kind") == "fo2_oxygen_balance_effusion_solved"
        for row in result.imcc_notices
    )


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

    composition_mol = dict(composition.components)
    melt = evaluate(
        composition_mol,
        2200.0,
        pack=openimcc_bridge._load_pack("v1.0.2"),
        basis_type="mol",
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


def test_openimcc_plante_candidates_match_mole_basis_package() -> None:
    _require_openimcc()
    from openimcc import evaluate, evaluate_gas
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
    # The independent per-row gate versus e87f906 is 5e-5 dex; a 1.3 J/mol
    # fit residual gives 1.3 / (8.314 * 1259 * 2.303) = 5.4e-5 dex at 1259 K.
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
        package_melt = evaluate(
            composition_mol,
            float(row["T_K"]),
            handle.backend._pack,
            basis_type="mol",
            allow_extrapolation=True,
            allow_out_of_envelope=True,
        )
        package_gas = evaluate_gas(
            dict(zip(package_melt.parent_oxides, package_melt.parent_activity, strict=True)),
            float(row["T_K"]),
            0.226 * float(row["measured_P_K_Pa"]) / 1.0e5,
            handle.backend._gas,
            parent_oxides=package_melt.parent_oxides,
            allow_extrapolation=True,
        )
        assert predicted == pytest.approx(float(package_gas["K"]) * 1.0e5, rel=1.0e-12)
        hand = float(row["hand_P_K_Pa"])
        deltas.append(math.log10(predicted / hand))
        measured_residuals.append(
            math.log10(predicted / float(row["measured_P_K_Pa"]))
        )
    assert len(deltas) == 162
    assert statistics_median(measured_residuals) == pytest.approx(0.093, abs=0.01)


def test_plante_solved_effusion_uses_the_prediction_engine_notice() -> None:
    _require_simulator_janaf_gas()
    _require_oxygen_balance()
    context = _plante_score_context()
    engines = (Engine.IMCC_SF04, Engine.IMCC_SF04_EXT, Engine.OPENIMCC)
    residuals, candidates = score_store(
        context,
        engines=engines,
        work_id="kems-042-plante-1979",
    )
    expected = {
        Engine.IMCC_SF04: (True, "independent"),
        Engine.IMCC_SF04_EXT: (True, "independent"),
        # Measured on green 6925ccacd, which adds the t-1020 lineage mapping.
        Engine.OPENIMCC: (True, "independent"),
    }
    rows = [
        (residual, candidates[residual.candidate])
        for residual in residuals
        if residual.candidate in candidates
    ]
    for engine, lineage in expected.items():
        engine_rows = [
            (residual, candidate)
            for residual, candidate in rows
            if candidate.engine is not None and candidate.engine.name is engine
        ]
        assert len(engine_rows) == 162
        assert {
            (candidate.engine.lineage_complete, residual.source_relation.value)
            for residual, candidate in engine_rows
            if candidate.engine is not None
        } == {lineage}

    validation = validate_corpus(
        context.works,
        context.experiments,
        {**context.observations, **candidates},
        residuals,
        benches=context.benches,
    )
    # Existing notice-shape issues are outside this score-eligibility regression.
    eligibility_issues = tuple(
        issue
        for issue in validation.issues
        if issue.path.startswith("residual[")
        and issue.path.endswith(".score_eligible")
    )
    assert eligibility_issues == ()

    summary = {
        row["engine"]: row
        for row in headline_rows(residuals, context=context, engines=engines)
        if row["rail"] == "vapour"
    }
    sf04 = summary[Engine.IMCC_SF04.value]
    assert sf04["n_score_eligible"] == 162
    assert sf04["n_inside_band"] == 115
    assert float(sf04["band_width_dex"]) == pytest.approx(0.1461, abs=0.00005)
    assert float(sf04["median_dex"]) == pytest.approx(0.0637, abs=0.00005)
    assert float(sf04["rms_dex"]) == pytest.approx(0.1556, abs=0.00005)
    openimcc = summary[Engine.OPENIMCC.value]
    assert openimcc["n_score_eligible"] == 162
    assert openimcc["n_inside_band"] == 112
    assert float(openimcc["band_width_dex"]) == pytest.approx(0.1461, abs=0.00005)
    assert float(openimcc["median_dex"]) == pytest.approx(0.0748, abs=0.00005)
    assert float(openimcc["rms_dex"]) == pytest.approx(0.1617, abs=0.00005)

    solved_openimcc_notice = next(
        notice
        for residual, candidate in rows
        if candidate.engine is not None and candidate.engine.name is Engine.OPENIMCC
        for notice in residual.notices
        if notice.kind is NoticeKind.SOURCE_DISAGREEMENT
        and notice.origin == "engine:openimcc"
        and notice.reason.startswith("fo2_oxygen_balance_effusion_solved:")
    )

    def predict_with_foreign_notice(engine, reference, **kwargs):
        experiment = kwargs.get("experiment")
        if experiment is not None and experiment.bench_id is not None:
            kwargs["bench"] = context.benches.get(experiment.bench_id)
        prediction = predict_with_engine(engine, reference, **kwargs)
        if engine is Engine.IMCC_SF04:
            notices = tuple(
                notice
                for notice in prediction.notices
                if not notice.reason.startswith(
                    "fo2_oxygen_balance_effusion_solved:"
                )
            )
            return replace(
                prediction,
                notices=(*notices, solved_openimcc_notice),
            )
        return prediction

    foreign_notice_residuals, _ = score_store(
        context,
        engines=(Engine.IMCC_SF04,),
        work_id="kems-042-plante-1979",
        limit=1,
        include_diagnostics=False,
        predict=predict_with_foreign_notice,
    )
    assert len(foreign_notice_residuals) == 1
    foreign_notice_residual = foreign_notice_residuals[0]
    assert solved_openimcc_notice in foreign_notice_residual.notices
    assert foreign_notice_residual.score_eligible is False
    assert "no_blocking_qualification" in foreign_notice_residual.exclusions


def test_openimcc_gas_table_mutation_to_vaporock_changes_prediction(monkeypatch) -> None:
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
    import openimcc
    from simulator.diagnostic_helpers.binary_pot_battery import _OpenImccBatteryBackend

    monkeypatch.delenv("OPENIMCC_VAPOROCK_ROOT", raising=False)
    packaged = _OpenImccBatteryBackend("openimcc")
    monkeypatch.setenv("OPENIMCC_VAPOROCK_ROOT", str(VAPOROCK_ROOT))
    mutated = _OpenImccBatteryBackend("openimcc")
    temperature_K = float(row["T_K"])
    evaluate_gas = openimcc.evaluate_gas
    gas_results = []

    def record_gas_result(*args, **kwargs):
        result = evaluate_gas(*args, **kwargs)
        gas_results.append(result)
        return result

    monkeypatch.setattr(openimcc, "evaluate_gas", record_gas_result)

    packaged_result = packaged.equilibrate(
        temperature_C=temperature_K - 273.15,
        composition_kg=composition_kg,
        composition_mol=composition_mol,
        fO2_log=math.log10(0.226 * float(row["measured_P_K_Pa"]) / 1.0e5),
    )
    mutated_result = mutated.equilibrate(
        temperature_C=temperature_K - 273.15,
        composition_kg=composition_kg,
        composition_mol=composition_mol,
        fO2_log=math.log10(0.226 * float(row["measured_P_K_Pa"]) / 1.0e5),
    )
    assert "VapoRock" in mutated._identity["gas_table_source"]
    assert len(gas_results) == 2
    _packaged_gas_result, mutated_gas_result = gas_results
    expected_omissions = {
        "Na2O": "Na2O(g)",
        "K2O": "K2O(g)",
        "Ti": "TiO2(l)",
        "TiO": "TiO2(l)",
        "TiO2": "TiO2(l)",
    }
    assert set(expected_omissions) <= set(mutated_gas_result.omitted_channels)
    for channel, missing_row in expected_omissions.items():
        reason = mutated_gas_result.omitted_channels[channel]
        assert "missing from active table" in reason
        assert missing_row in reason
        if missing_row.endswith("(g)"):
            assert missing_row not in mutated._gas.gas_df.index
        else:
            assert missing_row not in mutated._gas.oxide_df.index
    packaged_pressure = float(packaged_result.vapor_pressures_Pa["K"])
    mutated_pressure = float(mutated_result.vapor_pressures_Pa["K"])
    assert packaged_pressure > 0.0
    assert mutated_pressure > 0.0
    assert abs(
        math.log10(mutated_pressure / packaged_pressure)
    ) > 1.0e-9



def test_allibert_single_phase_table_ii_residuals_are_unchanged() -> None:
    """The 14 printed-melt rows keep the pre-typing residuals on both IMCC engines."""

    import math
    import tempfile
    from pathlib import Path

    from simulator.battery.enums import ExecutionState
    from simulator.battery.identity import quantity_token
    from tests.battery.test_migrate import _migrate_real_extract

    expected = {
        ("Al2O3", Decimal("0.352")): Decimal("-0.4352878073629644"),
        ("Al2O3", Decimal("0.414")): Decimal("-0.4368858361185386"),
        ("Al2O3", Decimal("0.438")): Decimal("-0.7676943822462646"),
        ("Al2O3", Decimal("0.495")): Decimal("-0.5785927361457213"),
        ("Al2O3", Decimal("0.548")): Decimal("-0.16244321866738143"),
        ("Al2O3", Decimal("0.578")): Decimal("-0.18296065968799388"),
        ("Al2O3", Decimal("0.645")): Decimal("-1.5247529304755827"),
        ("CaO", Decimal("0.352")): Decimal("-0.05660903970583223"),
        ("CaO", Decimal("0.414")): Decimal("0.2193957906740513"),
        ("CaO", Decimal("0.438")): Decimal("0.2974060565321931"),
        ("CaO", Decimal("0.495")): Decimal("0.20712743938341197"),
        ("CaO", Decimal("0.548")): Decimal("0.07227258108505644"),
        ("CaO", Decimal("0.578")): Decimal("-0.005642551875408192"),
        ("CaO", Decimal("0.645")): Decimal("0.7184208476485777"),
    }
    with tempfile.TemporaryDirectory() as tmp:
        result = _migrate_real_extract(Path(tmp), "kems-051-allibert-1981.yaml")
    rows = []
    for observation in result.observations.values():
        if observation.source_id != "kems-051-allibert-1981":
            continue
        if quantity_token(observation.identity) is not Quantity.ACTIVITY:
            continue
        if str(getattr(observation.locator, "table", None)) != "II":
            continue
        composition = observation.identity.composition
        assert composition is not None and composition.is_value
        cao = next(
            amount
            for name, amount in composition.value.components
            if name == "CaO"
        )
        if cao == Decimal("0.8"):
            continue
        assert observation.identity.species.phase.value is Phase.L
        rows.append(observation)
    assert len(rows) == 14
    context = ScoreContext(
        works=result.works,
        experiments=result.experiments,
        observations={observation.observation_id: observation for observation in rows},
        extract_review={"kems-051-allibert-1981": "reviewed"},
    )
    residuals, _candidates = score_store(
        context,
        engines=(Engine.IMCC_SF04, Engine.OPENIMCC),
        include_diagnostics=True,
    )
    by_id = {observation.observation_id: observation for observation in rows}
    seen: dict[tuple[str, Decimal, str], Decimal] = {}
    for residual in residuals:
        observation = by_id[residual.reference]
        composition = observation.identity.composition
        assert composition is not None and composition.value is not None
        cao = next(
            amount
            for name, amount in composition.value.components
            if name == "CaO"
        )
        engine = residual.key.rsplit("::", 1)[-1]
        assert residual.numeric is not None
        assert residual.execution.state is ExecutionState.PRODUCED
        seen[(observation.identity.species.formula, cao, engine)] = residual.numeric.value
        assert residual.status is ResidualStatus.NO_BAND
        assert abs(
            residual.numeric.value - expected[(observation.identity.species.formula, cao)]
        ) <= Decimal("1e-12")
    assert len(seen) == 28
    one_engine = [
        value
        for (formula, cao, engine), value in seen.items()
        if engine == Engine.IMCC_SF04.value
    ]
    assert len(one_engine) == 14
    rms = math.sqrt(sum(float(value) ** 2 for value in one_engine) / len(one_engine))
    assert rms == pytest.approx(0.560, abs=0.001)


def statistics_median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])
