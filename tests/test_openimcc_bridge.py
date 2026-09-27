"""C1 openimcc bridge and package-kernel parity checks."""

from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from simulator.melt_backend.openimcc_bridge import (
    OpenImccCompositionPolicyRefusal,
    OpenImccBridgeResult,
    OpenImccUnavailableError,
    OPENIMCC_PARENT_OXIDES,
    _cleaned_melt_wt_pct,
    evaluate as bridge_evaluate,
    evaluate_cleaned_melt,
)


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
        _cleaned_melt_wt_pct({"SiO2": 1.0, "MgO": invalid_inventory})

    refusal = exc_info.value
    assert refusal.code == "openimcc_composition_invalid_input"
    assert "MgO" in str(refusal)
    assert repr(invalid_inventory) in str(refusal)


def test_cleaned_melt_treats_zero_inventory_as_absent() -> None:
    source_wt_pct, folded_wt_pct, _ = _cleaned_melt_wt_pct(
        {"SiO2": 1.0, "MgO": 0.0}
    )

    assert "MgO" not in source_wt_pct
    assert "MgO" not in folded_wt_pct


def test_cleaned_melt_omits_absent_parent_activity() -> None:
    result = evaluate_cleaned_melt({"SiO2": 1.0}, 2200.0)

    assert "SiO2" in result.single_cation_activities
    assert "Na2O" not in result.single_cation_activities


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
    from openimcc import label_research_datapack, load_datapack
    from simulator.melt_backend.imcc_sf04.adapter import evaluate as evaluate_imcc

    fixture = json.loads("\n".join(
        line for line in GREEN_ACTIVITY_FIXTURE.read_text().splitlines()
        if not line.startswith("#")
    ))
    row = fixture["rows"][0]
    composition, basis_type = COMPOSITIONS[row["composition"]]
    pack = load_datapack()
    baseline = evaluate_imcc(
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
    mutated = evaluate_imcc(
        composition, row["temperature_K"], mutated_pack, basis_type=basis_type
    )
    mutated_hex = {
        str(name): float(value).hex()
        for name, value in zip(mutated.parent_oxides, mutated.parent_activity, strict=True)
    }
    assert mutated_hex != row["activities_hex"]


def test_bridge_envelope_matches_green_edge_decisions() -> None:
    _openimcc_or_skip()
    from openimcc import ImccCompositionOutsideValidatedEnvelopeError

    fixture = json.loads("\n".join(
        line for line in GREEN_ACTIVITY_FIXTURE.read_text().splitlines()
        if not line.startswith("#")
    ))
    assert fixture["edge_row"]["code"] == "imcc_composition_outside_validated_envelope"
    with pytest.raises(ImccCompositionOutsideValidatedEnvelopeError):
        bridge_evaluate(
            composition_mol={"K2O": 0.500002, "SiO2": 0.499998},
            temperature_K=1800.0,
        )
    inside = bridge_evaluate(
        composition_mol={"K2O": 0.5, "SiO2": 0.5}, temperature_K=1800.0
    )
    assert inside.envelope_status == "inside"
    with pytest.raises(ImccCompositionOutsideValidatedEnvelopeError):
        bridge_evaluate(
            composition_mol={"K2O": 0.500006, "SiO2": 0.499994},
            temperature_K=1800.0,
        )


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
