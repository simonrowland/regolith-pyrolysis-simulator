"""C1 openimcc bridge and package-kernel parity checks."""

from __future__ import annotations

import os
import json
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from simulator.melt_backend.openimcc_bridge import (
    OpenImccBindingDigestUnavailableError,
    OpenImccCompositionPolicyRefusal,
    OpenImccBridgeResult,
    OpenImccUnavailableError,
    OPENIMCC_PARENT_OXIDES,
    _load_pack,
    _pack_digest,
    _cleaned_melt_projection,
    evaluate as bridge_evaluate,
    evaluate_cleaned_melt,
)
from simulator.state import MOLAR_MASS


REPO_ROOT = Path(__file__).resolve().parents[1]
GREEN_ACTIVITY_FIXTURE = REPO_ROOT / "tests/fixtures/imcc_green_d9bd25f0b_activities.json"
PACKS = {
    "v1.0.2": None,
    "ext-v4": "imcc-sf04-ext-v4.json",
}
TEMPERATURES_K = (1700.0, 1950.0, 2200.0, 2500.0, 3000.0)

COMPOSITIONS = {
    "lunar_mare_low_ti": (
        {
            "SiO2": 44.5,
            "TiO2": 1.5,
            "Al2O3": 13.5,
            "FeO": 16.5,
            "MgO": 9.0,
            "CaO": 11.0,
            "Na2O": 0.4,
            "K2O": 0.1,
        },
        "wt",
    ),
    "lunar_highland": (
        {
            "SiO2": 45.0,
            "TiO2": 0.45,
            "Al2O3": 26.5,
            "FeO": 5.0,
            "MgO": 6.0,
            "CaO": 15.5,
            "Na2O": 0.5,
            "K2O": 0.1,
        },
        "wt",
    ),
    "mars_basalt": (
        {
            "SiO2": 45.5,
            "TiO2": 1.0,
            "Al2O3": 10.5,
            "FeO": 18.0,
            "MgO": 8.5,
            "CaO": 7.0,
            "Na2O": 3.0,
            "K2O": 0.5,
        },
        "wt",
    ),
    "kreep_like_bulk": (
        {
            "SiO2": 50.5,
            "TiO2": 2.25,
            "Al2O3": 16.0,
            "FeO": 10.0,
            "MgO": 8.5,
            "CaO": 10.0,
            "Na2O": 0.7,
            "K2O": 1.0,
        },
        "wt",
    ),
    "Na2O-SiO2": ({"SiO2": 0.95, "Na2O": 0.05}, "mol"),
    "K2O-SiO2": ({"SiO2": 0.95, "K2O": 0.05}, "mol"),
}


def _openimcc_or_skip():
    return pytest.importorskip(
        "openimcc",
        reason=(
            "openimcc is not importable; install it or put an openimcc "
            "checkout's src/ directory on PYTHONPATH before running the parity check"
        ),
    )


@pytest.mark.parametrize(
    "invalid_inventory",
    (-1.0, float("nan"), float("inf"), True, "1.0"),
)
def test_cleaned_melt_rejects_invalid_inventory(invalid_inventory) -> None:
    with pytest.raises(OpenImccCompositionPolicyRefusal) as exc_info:
        _cleaned_melt_projection({"SiO2": 1.0, "MgO": invalid_inventory})

    refusal = exc_info.value
    assert refusal.code == "openimcc_composition_invalid_input"
    assert "MgO" in str(refusal)
    assert repr(invalid_inventory) in str(refusal)


@pytest.mark.parametrize("iron_oxide", ("FeO", "Fe2O3"))
@pytest.mark.parametrize(
    "invalid_inventory",
    (-1.0, float("nan"), float("inf"), True, "1.0", [1.0, 3.0]),
)
def test_cleaned_melt_rejects_invalid_iron_inventory(
    iron_oxide: str, invalid_inventory
) -> None:
    with pytest.raises(OpenImccCompositionPolicyRefusal) as exc_info:
        _cleaned_melt_projection({"SiO2": 2.0, iron_oxide: invalid_inventory})

    refusal = exc_info.value
    assert refusal.code == "openimcc_composition_invalid_input"
    assert iron_oxide in str(refusal)
    assert repr(invalid_inventory) in str(refusal)


def test_cleaned_melt_treats_zero_inventory_as_absent() -> None:
    source_wt_pct, cleaned_mol = _cleaned_melt_projection(
        {"SiO2": 1.0, "MgO": 0.0}
    )

    assert "MgO" not in source_wt_pct
    assert "MgO" not in cleaned_mol


def test_cleaned_melt_passes_exact_parent_moles_to_openimcc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.melt_backend.openimcc_bridge as bridge

    original_evaluate = bridge.evaluate
    observed = []

    def capture(**kwargs):
        observed.append(kwargs)
        return original_evaluate(**kwargs)

    monkeypatch.setattr(bridge, "evaluate", capture)
    result = evaluate_cleaned_melt({"Na2O": 0.5, "SiO2": 0.5}, 1800.0)

    assert observed[-1]["composition_mol"] == {"Na2O": 0.5, "SiO2": 0.5}
    assert "composition_kg" not in observed[-1]
    assert result.bridge.envelope_status == "inside"


@pytest.mark.parametrize(
    "composition_mol",
    (
        {"SiO2": 1.0},
        {"SiO2": 0.8, "FeO": 0.1, "Fe2O3": 0.05},
        {
            "SiO2": 0.78,
            "Na2O": 0.1,
            "K2O": 0.1,
            "Fe2O3": 0.019,
            "Cr2O3": 0.0005,
            "MnO": 0.0005,
        },
    ),
)
def test_cleaned_melt_wt_pct_describes_the_openimcc_mole_input(
    monkeypatch: pytest.MonkeyPatch,
    composition_mol: dict[str, float],
) -> None:
    import simulator.melt_backend.openimcc_bridge as bridge

    original_evaluate = bridge.evaluate
    observed = []

    def capture(**kwargs):
        observed.append(kwargs["composition_mol"])
        return original_evaluate(**kwargs)

    monkeypatch.setattr(bridge, "evaluate", capture)
    result = evaluate_cleaned_melt(composition_mol, 2200.0)

    package_moles = observed[-1]
    masses = {
        oxide: amount * float(MOLAR_MASS[oxide])
        for oxide, amount in package_moles.items()
    }
    total_mass = sum(masses.values())
    recovered_wt_pct = {
        oxide: mass / total_mass * 100.0
        for oxide, mass in masses.items()
    }
    assert result.composition_wt_pct == pytest.approx(
        recovered_wt_pct, rel=0.0, abs=1e-14
    )
    if "Fe2O3" in composition_mol:
        assert package_moles["FeO"] == pytest.approx(
            composition_mol.get("FeO", 0.0) + 2.0 * composition_mol["Fe2O3"]
        )


def test_cleaned_melt_omits_absent_parent_activity() -> None:
    result = evaluate_cleaned_melt({"SiO2": 1.0}, 2200.0)

    assert "SiO2" in result.single_cation_activities
    assert "Na2O" not in result.single_cation_activities


@pytest.mark.parametrize(
    ("composition_mol", "expected"),
    (
        ({"SiO2": 0.8, "FeO_total": 0.2}, {"SiO2": 0.8, "FeO": 0.2}),
        (
            {"SiO2": 0.8, "FeO": 0.1, "Fe2O3": 0.05},
            {"SiO2": 0.8, "FeO": 0.2},
        ),
    ),
)
def test_cleaned_melt_preserves_feo_equivalent_moles(
    monkeypatch: pytest.MonkeyPatch,
    composition_mol: dict[str, float],
    expected: dict[str, float],
) -> None:
    import simulator.melt_backend.openimcc_bridge as bridge

    original_evaluate = bridge.evaluate
    observed = []

    def capture(**kwargs):
        observed.append(kwargs)
        return original_evaluate(**kwargs)

    monkeypatch.setattr(bridge, "evaluate", capture)
    evaluate_cleaned_melt(composition_mol, 2200.0)

    assert observed[-1]["composition_mol"] == expected


def test_cleaned_melt_refuses_missing_present_parent_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.melt_backend.openimcc_bridge as bridge

    monkeypatch.setattr(
        bridge,
        "evaluate",
        lambda **kwargs: OpenImccBridgeResult(
            parent_oxide_activities={"SiO2": 1.0},
            parent_oxides=("SiO2",),
            flags=(),
            notices=(),
            acid_sink_ratio=None,
            pack_model_id="fake",
            pack_version="fake",
            pack_digest="fake",
            openimcc_version="fake",
            envelope_status="in_domain",
            extrapolated=False,
            labels=SimpleNamespace(),
            coverage={},
        ),
    )

    with pytest.raises(OpenImccCompositionPolicyRefusal) as exc_info:
        evaluate_cleaned_melt({"SiO2": 1.0, "Na2O": 0.1}, 2200.0)

    refusal = exc_info.value
    assert refusal.code == "openimcc_result_shape"
    assert "Na2O" in str(refusal)


@pytest.mark.parametrize("invalid_activity", (0.0, -1.0, float("nan")))
def test_cleaned_melt_refuses_invalid_present_parent_activity(
    monkeypatch: pytest.MonkeyPatch,
    invalid_activity: float,
) -> None:
    import simulator.melt_backend.openimcc_bridge as bridge

    activities = {oxide: 1.0 for oxide in OPENIMCC_PARENT_OXIDES}
    activities["Na2O"] = invalid_activity
    monkeypatch.setattr(
        bridge,
        "evaluate",
        lambda **kwargs: OpenImccBridgeResult(
            parent_oxide_activities=activities,
            parent_oxides=OPENIMCC_PARENT_OXIDES,
            flags=(),
            notices=(),
            acid_sink_ratio=None,
            pack_model_id="fake",
            pack_version="fake",
            pack_digest="fake",
            openimcc_version="fake",
            envelope_status="in_domain",
            extrapolated=False,
            labels=SimpleNamespace(),
            coverage={},
        ),
    )

    with pytest.raises(OpenImccCompositionPolicyRefusal) as exc_info:
        evaluate_cleaned_melt({"SiO2": 1.0, "Na2O": 0.1}, 2200.0)

    refusal = exc_info.value
    assert refusal.code == "openimcc_result_shape"
    assert "Na2O" in str(refusal)
    assert repr(invalid_activity) in str(refusal)


def test_missing_openimcc_is_a_typed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    import simulator.melt_backend.openimcc_bridge as bridge

    monkeypatch.setattr(bridge, "_openimcc", None)
    monkeypatch.setattr(bridge, "_OPENIMCC_IMPORT_ERROR", ModuleNotFoundError("openimcc"))

    with pytest.raises(OpenImccUnavailableError) as exc_info:
        bridge_evaluate(composition_mol={"SiO2": 1.0}, temperature_K=2200.0)

    refusal = exc_info.value
    assert refusal.reason_code == "openimcc_unavailable"
    assert refusal.code == "openimcc_not_importable"
    assert "reason=openimcc_not_importable" in str(refusal)
    assert "remedy:" in str(refusal)


def test_missing_openimcc_blocked_subprocess_is_a_typed_refusal() -> None:
    script = """
import importlib.abc
import sys


class _BlockOpenImcc(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'openimcc' or fullname.startswith('openimcc.'):
            raise ImportError('test-only blocked openimcc import')
        return None


sys.meta_path.insert(0, _BlockOpenImcc())
from simulator.melt_backend.openimcc_bridge import evaluate

try:
    evaluate(composition_mol={'SiO2': 1.0}, temperature_K=2200.0)
except Exception as exc:
    print(type(exc).__name__)
    print(getattr(exc, 'code', ''))
    print(str(exc))
else:
    raise SystemExit('openimcc unexpectedly importable in clean environment')
"""
    env = os.environ.copy()
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "OpenImccUnavailableError" in completed.stdout
    assert "openimcc_not_importable" in completed.stdout
    assert "remedy:" in completed.stdout
    assert "openimcc @ git+https://github.com/simonrowland/openimcc" in completed.stdout
    assert "PYTHONPATH" in completed.stdout


def test_bridge_maps_mol_kg_and_returns_labels() -> None:
    _openimcc_or_skip()
    composition_mol = {"SiO2": 0.7, "FeO": 0.2, "Na2O": 0.1}
    mol_result = bridge_evaluate(
        composition_mol=composition_mol,
        temperature_K=2200.0,
    )
    kg_result = bridge_evaluate(
        composition_kg={
            "SiO2": 0.7 * 60.0843,
            "FeO_total": 0.2 * 71.844,
            "Na2O": 0.1 * 61.9789,
        },
        temperature_K=2200.0,
    )

    assert mol_result.parent_oxide_activities == kg_result.parent_oxide_activities
    assert mol_result.pack_model_id == "IMCC-SF04"
    assert mol_result.pack_version == "1.0.2"
    assert len(mol_result.pack_digest) == 64
    assert mol_result.openimcc_version
    assert mol_result.flags
    assert mol_result.notices
    assert mol_result.acid_sink_ratio is not None


@pytest.mark.parametrize("pack_name", tuple(PACKS))
def test_openimcc_parent_activity_parity(pack_name: str) -> None:
    _openimcc_or_skip()
    fixture = json.loads("\n".join(
        line for line in GREEN_ACTIVITY_FIXTURE.read_text().splitlines()
        if not line.startswith("#")
    ))
    rows = [row for row in fixture["rows"] if row["pack"] == pack_name]
    assert len(rows) >= 20
    assert len({row["composition"] for row in rows}) >= 5
    assert len({row["temperature_K"] for row in rows}) >= 4
    for row in rows:
        composition, basis_type = COMPOSITIONS[row["composition"]]
        bridge_kwargs = {"temperature_K": row["temperature_K"], "pack": pack_name}
        bridge_kwargs["composition_kg" if basis_type == "wt" else "composition_mol"] = composition
        bridge_result = bridge_evaluate(**bridge_kwargs)
        actual = bridge_result.parent_oxide_activities
        expected_parent_oxides = (
            "SiO2", "MgO", "FeO", "CaO", "Al2O3", "TiO2", "Na2O", "K2O"
        ) + (("S", "P2O5") if pack_name == "ext-v4" else ())
        assert tuple(bridge_result.parent_oxides) == expected_parent_oxides
        assert {name: float(value).hex() for name, value in actual.items()} == row["activities_hex"]
        expected_labels = row["labels_legacy"]
        labels = bridge_result.labels
        assert dict(labels.coverage) == expected_labels["coverage"]
        assert labels.trust == expected_labels["trust"]
        assert labels.envelope_status == expected_labels["envelope_status"]
        identity = dict(labels.identity)
        expected_identity = dict(expected_labels["identity"])
        if pack_name == "ext-v4":
            # The package's ext-v4 is sp-2; green carried sp-1. Version remains
            # package-owned and honest while all other green identity fields match.
            expected_identity.pop("datapack_version")
        assert {key: identity[key] for key in expected_identity} == expected_identity


def test_green_fixture_detects_an_in_memory_coefficient_mutation() -> None:
    _openimcc_or_skip()
    from dataclasses import replace
    import openimcc
    from openimcc import label_research_datapack, load_datapack

    fixture = json.loads("\n".join(
        line for line in GREEN_ACTIVITY_FIXTURE.read_text().splitlines()
        if not line.startswith("#")
    ))
    row = fixture["rows"][0]
    composition, basis_type = COMPOSITIONS[row["composition"]]
    pack = load_datapack()
    baseline = openimcc.evaluate(
        composition, row["temperature_K"], pack, basis_type=basis_type
    )
    baseline_hex = {
        str(name): float(value).hex()
        for name, value in zip(baseline.parent_oxides, baseline.parent_activity, strict=True)
    }
    assert baseline_hex == row["activities_hex"]
    kernel = replace(pack.kernel_datapack, A=pack.kernel_datapack.A.copy())
    kernel.A[0] += 0.01
    mutated_pack = label_research_datapack(
        kernel, model_id="IMCC-SF04-mutation", coverage="mutation-probe"
    )
    mutated = openimcc.evaluate(
        composition, row["temperature_K"], mutated_pack, basis_type=basis_type
    )
    mutated_hex = {
        str(name): float(value).hex()
        for name, value in zip(mutated.parent_oxides, mutated.parent_activity, strict=True)
    }
    assert mutated_hex != row["activities_hex"]


def test_pack_digest_preserves_the_v1_binding_value() -> None:
    _openimcc_or_skip()
    expected = "f2b479cd54e3c82704a5863fcc06836f72045375d9a8c7f8d2fad19e98f75d05"
    assert _pack_digest(_load_pack("v1.0.2")) == expected


def test_pack_digest_uses_ext_binding_digest() -> None:
    _openimcc_or_skip()
    expected = "4cbec2ee85cd95314a5f62f5ce4e00cbe660935bde12df3a7e5daa425612ad03"
    assert _pack_digest(_load_pack("ext-v4")) == expected


def test_pack_digest_uses_research_binding_digest() -> None:
    _openimcc_or_skip()
    from openimcc import label_research_datapack

    loaded_pack = _load_pack("v1.0.2")
    kernel = replace(
        loaded_pack.kernel_datapack,
        A=loaded_pack.kernel_datapack.A.copy(),
    )
    kernel.A[0] += 0.01
    research_pack = label_research_datapack(
        kernel,
        model_id="IMCC-SF04-mutation",
        coverage="mutation-probe",
    )
    research_digest = _pack_digest(research_pack)
    assert research_digest != _pack_digest(_load_pack("v1.0.2"))
    assert research_digest != _pack_digest(_load_pack("ext-v4"))


@pytest.mark.parametrize(
    "pack",
    (SimpleNamespace(binding_digest=None), SimpleNamespace()),
    ids=("none", "unsupported-attribute"),
)
def test_pack_digest_refuses_missing_binding_digest(pack) -> None:
    with pytest.raises(OpenImccBindingDigestUnavailableError) as exc_info:
        _pack_digest(pack)

    assert exc_info.value.reason_code == "openimcc_binding_digest_unavailable"
    assert "install the recorded pin" in str(exc_info.value)


def test_engine_binding_identity_changes_with_condensate_coefficient(
    tmp_path: Path,
) -> None:
    _openimcc_or_skip()
    import csv
    import shutil

    import openimcc
    from simulator.melt_backend import openimcc_bridge

    melt_pack = _load_pack("v1.0.2")
    packaged_gas = openimcc.load_gas_datapack()
    baseline = openimcc_bridge.engine_binding_identity(melt_pack, packaged_gas)

    condensate_copy = tmp_path / "condensate.csv"
    shutil.copyfile(packaged_gas.oxide_path, condensate_copy)
    with condensate_copy.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames
        rows = list(reader)
    assert columns is not None and rows
    rows[0]["dG_A"] = str(float(rows[0]["dG_A"]) + 0.125)
    with condensate_copy.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    changed_gas = openimcc.load_gas_datapack(
        gas_path=packaged_gas.gas_path,
        oxide_path=condensate_copy,
    )
    changed = openimcc_bridge.engine_binding_identity(melt_pack, changed_gas)

    assert changed["condensate_table_digest"] != baseline["condensate_table_digest"]
    assert changed["engine_binding_digest"] != baseline["engine_binding_digest"]
    assert changed["melt_binding_digest"] == baseline["melt_binding_digest"]
    assert changed["gas_table_digest"] == baseline["gas_table_digest"]


def test_engine_binding_identity_ignores_table_paths(tmp_path: Path) -> None:
    _openimcc_or_skip()
    import shutil

    import openimcc
    from simulator.melt_backend import openimcc_bridge

    melt_pack = _load_pack("v1.0.2")
    packaged_gas = openimcc.load_gas_datapack()
    relocated_gas_path = tmp_path / "gas-shomate.csv"
    relocated_condensate_path = tmp_path / "condensate.csv"
    shutil.copyfile(packaged_gas.gas_path, relocated_gas_path)
    shutil.copyfile(packaged_gas.oxide_path, relocated_condensate_path)
    relocated_gas = openimcc.load_gas_datapack(
        gas_path=relocated_gas_path,
        oxide_path=relocated_condensate_path,
    )

    assert str(relocated_gas.gas_path) != str(packaged_gas.gas_path)
    assert str(relocated_gas.oxide_path) != str(packaged_gas.oxide_path)
    assert openimcc_bridge.engine_binding_identity(
        melt_pack, relocated_gas
    ) == openimcc_bridge.engine_binding_identity(melt_pack, packaged_gas)


@pytest.mark.parametrize("owner_mode", ("missing", "raising"))
def test_engine_binding_identity_refuses_without_path_fallback(
    owner_mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.melt_backend import openimcc_bridge

    package = SimpleNamespace()
    if owner_mode == "raising":
        def raise_identity_error(*_args):
            raise RuntimeError("package identity failed")

        package.engine_binding_identity = raise_identity_error
    monkeypatch.setattr(openimcc_bridge, "_openimcc", package)

    with pytest.raises(OpenImccBindingDigestUnavailableError) as exc_info:
        openimcc_bridge.engine_binding_identity(
            SimpleNamespace(),
            SimpleNamespace(
                gas_path="/host/a/gas-shomate.csv",
                oxide_path="/host/a/condensate.csv",
            ),
        )

    assert exc_info.value.reason_code == "openimcc_binding_digest_unavailable"


def test_bridge_envelope_matches_green_edge_decisions() -> None:
    _openimcc_or_skip()
    from openimcc import ImccCompositionOutsideValidatedEnvelopeError

    fixture = json.loads("\n".join(
        line for line in GREEN_ACTIVITY_FIXTURE.read_text().splitlines()
        if not line.startswith("#")
    ))
    assert fixture["edge_row"]["code"] == "imcc_composition_outside_validated_envelope"
    inside_slack = bridge_evaluate(
        composition_mol={"K2O": 0.500002, "SiO2": 0.499998},
        temperature_K=1800.0,
    )
    assert inside_slack.envelope_status == "inside"
    inside = bridge_evaluate(
        composition_mol={"K2O": 0.5, "SiO2": 0.5}, temperature_K=1800.0
    )
    assert inside.envelope_status == "inside"
    within_package_slack = bridge_evaluate(
        composition_mol={"K2O": 0.500005, "SiO2": 0.499995},
        temperature_K=1800.0,
    )
    assert within_package_slack.envelope_status == "inside"
    with pytest.raises(ImccCompositionOutsideValidatedEnvelopeError):
        bridge_evaluate(
            composition_mol={"K2O": 0.500006, "SiO2": 0.499994},
            temperature_K=1800.0,
        )


def test_species_coverage_edge_flag_contract_and_typed_notice() -> None:
    _openimcc_or_skip()
    from simulator.melt_backend.openimcc_bridge import (
        imcc_complex_saturation_notices,
    )

    for alkali in ("Na2O", "K2O"):
        for fraction in (0.50, 0.55):
            result = bridge_evaluate(
                composition_mol={alkali: fraction, "SiO2": 1.0 - fraction},
                temperature_K=1800.0,
                allow_out_of_envelope=True,
            )
            notices = imcc_complex_saturation_notices(
                result.flags, result.acid_sink_ratio, result.parent_oxide_ratios
            )
            assert len(notices) == 1
            notice = notices[0]
            assert notice["kind"] == "imcc_complex_saturation"
            assert notice["flag"].startswith("species-coverage-edge")
            assert notice["acid_sink_ratio"] == result.acid_sink_ratio
            assert set(notice) == {
                "kind", "flag", "reason", "acid_sink_ratio"
            }

    pinned = bridge_evaluate(
        composition_mol={"K2O": 0.55, "SiO2": 0.45},
        temperature_K=1800.0,
        allow_out_of_envelope=True,
    )
    assert any(flag.startswith("species-coverage-edge") for flag in pinned.flags)

    lunar = {
        "SiO2": 44.5,
        "TiO2": 1.5,
        "Al2O3": 13.5,
        "FeO": 16.5,
        "MgO": 9.0,
        "CaO": 11.0,
        "Na2O": 0.4,
        "K2O": 0.1,
    }
    for temperature_K in (1700.0, 2200.0):
        result = bridge_evaluate(
            composition_kg=lunar,
            temperature_K=temperature_K,
            allow_extrapolation=True,
        )
        assert imcc_complex_saturation_notices(
            result.flags, result.acid_sink_ratio, result.parent_oxide_ratios
        ) == ()


@pytest.mark.parametrize(
    "composition_mol, expected_sinks",
    [
        ({"CaO": 0.9, "Al2O3": 0.1}, ("Al2O3",)),
        (
            {"Na2O": 0.2, "CaO": 0.4, "Al2O3": 0.2, "TiO2": 0.2},
            ("Al2O3", "TiO2"),
        ),
    ],
)
def test_each_exhausted_non_silica_sink_gets_its_own_notice(
    composition_mol, expected_sinks
) -> None:
    _openimcc_or_skip()
    from simulator.melt_backend.openimcc_bridge import (
        imcc_complex_saturation_notices,
    )

    result = bridge_evaluate(
        composition_mol=composition_mol,
        temperature_K=1800.0,
        allow_extrapolation=True,
        allow_out_of_envelope=True,
    )
    edge_flags = tuple(
        flag for flag in result.flags
        if flag.startswith("species-coverage-edge")
    )
    actual_sinks = tuple(
        sink for sink in expected_sinks
        if any(f"x*({sink})" in flag for flag in edge_flags)
    )
    if actual_sinks != expected_sinks:
        pytest.skip(
            "installed openimcc does not emit the requested multi-sink coverage-edge flags"
        )

    notices = imcc_complex_saturation_notices(
        result.flags, result.acid_sink_ratio, result.parent_oxide_ratios
    )
    assert len(notices) == len(expected_sinks)
    assert tuple(
        next(sink for sink in expected_sinks if f"x*({sink})" in row["flag"])
        for row in notices
    ) == expected_sinks
    for sink, notice in zip(expected_sinks, notices, strict=True):
        assert notice["sink_name"] == sink
        assert notice["sink_ratio"] == result.parent_oxide_ratios[sink]
        assert "acid_sink_ratio" not in notice


def test_bridge_extrapolation_and_envelope_flags_are_explicit() -> None:
    openimcc = _openimcc_or_skip()
    with pytest.raises(openimcc.ImccTOutsideDatapackDomainError) as temperature_refusal:
        bridge_evaluate(
            composition_mol={"SiO2": 0.5, "MgO": 0.5},
            temperature_K=1600.0,
        )
    assert "temperature" in str(temperature_refusal.value).lower()

    extrapolated = bridge_evaluate(
        composition_mol={"SiO2": 0.5, "MgO": 0.5},
        temperature_K=1600.0,
        allow_extrapolation=True,
    )
    assert extrapolated.extrapolated is True

    with pytest.raises(
        openimcc.ImccCompositionOutsideValidatedEnvelopeError
    ) as envelope_refusal:
        bridge_evaluate(
            composition_mol={"SiO2": 0.49, "Na2O": 0.51},
            temperature_K=2200.0,
        )
    assert "bound 0.5" in str(envelope_refusal.value)

    outside = bridge_evaluate(
        composition_mol={"SiO2": 0.49, "Na2O": 0.51},
        temperature_K=2200.0,
        allow_out_of_envelope=True,
    )
    assert outside.envelope_status == "outside_validated"


def test_bridge_reports_single_cation_gamma_zhang_n_morb_anchor() -> None:
    _openimcc_or_skip()
    composition = {
        "SiO2": 45.94,
        "Al2O3": 16.0,
        "FeO": 10.67,
        "MgO": 7.09,
        "CaO": 11.21,
        "TiO2": 1.79,
        "Na2O": 2.27,
        "K2O": 2.49,
    }
    expected = {
        1473.15: {"KO0.5": 3.49e-9, "NaO0.5": 6.87e-5},
        1673.15: {"KO0.5": 2.45e-8, "NaO0.5": 2.76e-4},
    }
    for temperature_K, anchors in expected.items():
        result = bridge_evaluate(
            composition_kg=composition,
            temperature_K=temperature_K,
            allow_extrapolation=True,
        )
        for component, anchor in anchors.items():
            row = result.activity_coefficients[component]
            assert row["value"] == pytest.approx(anchor, rel=1e-2)
            assert row["coefficient_basis"] == "single_cation"
            assert row["standard_state"] == {
                "convention": "raoultian_pure_endmember",
                "phase": "l",
                "component_basis": component,
            }


def test_single_cation_gamma_ideal_pure_oxide_limit() -> None:
    _openimcc_or_skip()
    result = bridge_evaluate(
        composition_mol={"SiO2": 1.0}, temperature_K=1800.0
    )
    assert result.activity_coefficients["SiO2"]["value"] == 1.0


@pytest.mark.parametrize(
    "composition",
    [
        {"SiO2": 2.0, "FeO": 1.0, "Fe2O3": 1.0},
        {"SiO2": 2.0, "Fe2O3": 1.0, "FeO": 1.0},
        {"SiO2": 2.0, "FeO_total": 1.0, "Fe2O3": 1.0},
        {"SiO2": 2.0, "Fe2O3": 1.0, "FeO_total": 1.0},
    ],
)
def test_cleaned_melt_fe_fold_is_order_independent(composition) -> None:
    # Fe-atom conservation: FeO_eq = FeO + 2*Fe2O3 = 1 + 2*1 = 3 mol, whatever the
    # mapping insertion order and whether FeO arrives as FeO or the FeO_total alias.
    from simulator.melt_backend.openimcc_bridge import _cleaned_melt_projection

    _wt, moles = _cleaned_melt_projection(composition)
    assert moles["FeO"] == pytest.approx(3.0, rel=0, abs=1e-12)
    assert moles["SiO2"] == pytest.approx(2.0, rel=0, abs=1e-12)


def test_openimcc_measured_ferric_split_keeps_feo_equivalent_projection_notice():
    from simulator.melt_backend.openimcc_bridge import _cleaned_melt_policy

    composition = {"SiO2": 2.0, "FeO": 1.0, "Fe2O3": 0.5}
    _source_wt, moles = _cleaned_melt_projection(composition)
    _policy_wt, policy = _cleaned_melt_policy(composition)

    assert moles["FeO"] == pytest.approx(2.0)
    assert "Fe2O3" not in moles
    assert policy["fe_redox_notice"] == {
        "code": "openimcc_ferric_component_collapsed",
        "message": "ferric component collapsed until d-072",
    }


def test_recorded_openimcc_pin_matches_pyproject_extra() -> None:
    # The refusal remedies cite OPENIMCC_RECORDED_PIN; it must name the same openimcc
    # commit as the pyproject `imcc` optional extra, so a remedy can never point at a
    # revision that lacks the features the code requires.
    import tomllib
    from pathlib import Path

    from simulator.melt_backend.openimcc_bridge import OPENIMCC_RECORDED_PIN

    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )
    extra = pyproject["project"]["optional-dependencies"]["imcc"]
    assert extra == [OPENIMCC_RECORDED_PIN]
