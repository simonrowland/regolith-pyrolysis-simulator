"""C1 openimcc bridge and vendored-kernel parity checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from simulator.melt_backend.imcc_sf04 import evaluate as vendored_evaluate
from simulator.melt_backend.imcc_sf04 import load_datapack as vendored_load_datapack
from simulator.melt_backend.imcc_sf04.openimcc_bridge import (
    OpenImccUnavailableError,
    evaluate as bridge_evaluate,
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
    openimcc = _openimcc_or_skip()
    vendored_pack = vendored_load_datapack(VENDORED_PACKS[pack_name])
    if pack_name == "v1.0.2":
        open_pack = openimcc.load_datapack()
        enable_sp_extension = False
    else:
        open_pack = openimcc.load_datapack(
            Path(openimcc.__file__).resolve().parent
            / "data/packs/imcc-sf04-ext-v4.json"
        )
        enable_sp_extension = True

    differences: list[str] = []
    for composition_name, (composition, basis_type) in COMPOSITIONS.items():
        for temperature_K in TEMPERATURES_K:
            vendored = vendored_evaluate(
                composition,
                temperature_K,
                vendored_pack,
                basis_type=basis_type,
                enable_sp_extension=enable_sp_extension,
            )
            standalone = openimcc.evaluate(
                composition,
                temperature_K,
                open_pack,
                basis_type=basis_type,
                enable_sp_extension=enable_sp_extension,
            )
            for oxide, expected, actual in zip(
                vendored.parent_oxides,
                vendored.parent_activity,
                standalone.parent_activity,
                strict=True,
            ):
                expected = float(expected)
                actual = float(actual)
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
