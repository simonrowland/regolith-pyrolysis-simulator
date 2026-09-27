"""C1 openimcc bridge and vendored-kernel parity checks."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from simulator.melt_backend.imcc_sf04 import evaluate as vendored_evaluate
from simulator.melt_backend.imcc_sf04 import load_datapack as vendored_load_datapack
from simulator.melt_backend.imcc_sf04.openimcc_bridge import (
    OpenImccCompositionPolicyRefusal,
    OpenImccBridgeResult,
    OpenImccUnavailableError,
    OPENIMCC_PARENT_OXIDES,
    _cleaned_melt_wt_pct,
    evaluate as bridge_evaluate,
    evaluate_cleaned_melt,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
VENDORED_PACKS = {
    "v1.0.2": REPO_ROOT / "data/melt_activity/imcc/imcc-sf04-v1.0.2.json",
    "ext-v4": REPO_ROOT / "data/melt_activity/imcc/imcc-sf04-ext-v4.json",
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
    import simulator.melt_backend.imcc_sf04.openimcc_bridge as bridge

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
    import simulator.melt_backend.imcc_sf04.openimcc_bridge as bridge

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
    import simulator.melt_backend.imcc_sf04.openimcc_bridge as bridge

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
from simulator.melt_backend.imcc_sf04.openimcc_bridge import evaluate

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


@pytest.mark.parametrize("pack_name", tuple(VENDORED_PACKS))
def test_openimcc_parent_activity_parity(pack_name: str) -> None:
    _openimcc_or_skip()
    vendored_pack = vendored_load_datapack(VENDORED_PACKS[pack_name])

    differences: list[str] = []
    for composition_name, (composition, basis_type) in COMPOSITIONS.items():
        for temperature_K in TEMPERATURES_K:
            enable_sp_extension = pack_name == "ext-v4"
            vendored = vendored_evaluate(
                composition,
                temperature_K,
                vendored_pack,
                basis_type=basis_type,
                enable_sp_extension=enable_sp_extension,
            )
            bridge_kwargs = {
                "temperature_K": temperature_K,
                "pack": pack_name,
            }
            if basis_type == "wt":
                bridge_kwargs["composition_kg"] = composition
            else:
                bridge_kwargs["composition_mol"] = composition
            standalone = bridge_evaluate(**bridge_kwargs)
            if tuple(vendored.parent_oxides) != tuple(standalone.parent_oxides):
                differences.append(
                    f"{pack_name} {composition_name} {temperature_K:g} K "
                    f"parent-oxide labels: vendored={tuple(vendored.parent_oxides)!r} "
                    f"bridge={tuple(standalone.parent_oxides)!r}"
                )
                continue
            if set(standalone.parent_oxide_activities) != set(vendored.parent_oxides):
                differences.append(
                    f"{pack_name} {composition_name} {temperature_K:g} K "
                    "bridge parent-oxide activity labels do not match"
                )
                continue
            for oxide, expected, _ in zip(
                vendored.parent_oxides,
                vendored.parent_activity,
                standalone.parent_oxides,
                strict=True,
            ):
                expected = float(expected)
                actual = float(standalone.parent_oxide_activities[oxide])
                absolute = abs(expected - actual)
                relative = absolute / max(abs(expected), abs(actual), 1.0e-300)
                if relative > 1.0e-10:
                    differences.append(
                        f"{pack_name} {composition_name} {temperature_K:g} K "
                        f"{oxide}: vendored={expected:.17g} "
                        f"openimcc={actual:.17g} abs={absolute:.3g} "
                        f"rel={relative:.3g}"
                    )

    assert not differences, "openimcc activity parity differences:\n" + "\n".join(
        differences
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
