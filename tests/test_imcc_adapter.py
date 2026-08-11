"""Adapter-level gates for IMCC-SF04 (chunk 3)."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

import simulator.melt_backend.imcc_sf04 as imcc_sf04
from simulator.backend_names import canonical_backend_name
from simulator.fidelity_vocabulary import (
    CERTIFICATION_DENYLIST,
    backend_name_denies_authority,
)
from simulator.melt_backend.imcc_sf04 import (
    ImccAdapterLabels,
    ImccComponentOutsideDomainError,
    ImccCompositionOutsideValidatedEnvelopeError,
    ImccCompositionIncompleteError,
    ImccFerricInputUnsupportedError,
    ImccLoadedDatapack,
    ImccMalformedDatapackError,
    ImccNonconvergenceError,
    ImccTOutsideDatapackDomainError,
    evaluate,
    load_datapack,
)
from simulator.melt_backend.imcc_sf04.kernel import ImccRefusal


DATAPACK_PATH = Path(
    "docs-private/research/2026-08-09-upstream-mission/IMCC-impl/datapack/datapack.json"
)


def _make_uniform_composition(pack: ImccLoadedDatapack) -> dict[str, float]:
    return {name: 0.125 for name in pack.parent_oxides}


def _make_alkali_composition(
    pack: ImccLoadedDatapack, x_me2o: float
) -> dict[str, float]:
    composition = {name: 0.0 for name in pack.parent_oxides}
    composition["SiO2"] = 1.0 - x_me2o
    composition["Na2O"] = x_me2o
    return composition


def test_load_datapack_roundtrip() -> None:
    pack = load_datapack(DATAPACK_PATH)
    assert isinstance(pack, ImccLoadedDatapack)
    assert pack.version == "1.0.2"
    assert pack.parent_oxides == (
        "SiO2",
        "MgO",
        "FeO",
        "CaO",
        "Al2O3",
        "TiO2",
        "Na2O",
        "K2O",
    )
    assert len(pack.domain_basis) == 38
    assert set(pack.domain_basis) <= {
        "paper-demonstrated",
        "SF04-as-exercised",
        (
            "sf04-exercised-ADOPTED (v1.0.2: FC87 fits were exercised by "
            "SF04 over 1700-3000 K; the FC87-paper-demonstrated span is "
            "preserved in T_domain_paper_demonstrated_K)"
        ),
    }

    kernel = pack.kernel_datapack
    assert kernel.n_parents == 8
    assert kernel.n_complexes == 38
    assert kernel.n_species == 46
    assert kernel.version == "1.0.2"

    # Exact rationals: 0.5 survives as one-half (fractional Na/K/Al stoichiometry).
    half = Fraction(1, 2)
    by_name = {name: i for i, name in enumerate(kernel.reactions)}
    for complex_name in (
        "NaAlSiO4",
        "NaAlSi3O8",
        "NaAlO2",
        "NaAlSi2O6",
    ):
        idx = by_name[complex_name]
        assert Fraction(kernel.nu[pack.parent_oxides.index("Al2O3"), idx]) == half
        assert Fraction(kernel.nu[pack.parent_oxides.index("Na2O"), idx]) == half
        assert Fraction(kernel.nu[pack.parent_oxides.index("K2O"), idx]) == 0
    for complex_name in (
        "KAlSiO4",
        "KAlSi3O8",
        "KAlO2",
        "KAlSi2O6",
        "KCaAlSi2O7",
    ):
        idx = by_name[complex_name]
        assert Fraction(kernel.nu[pack.parent_oxides.index("Al2O3"), idx]) == half
        assert Fraction(kernel.nu[pack.parent_oxides.index("K2O"), idx]) == half
        assert Fraction(kernel.nu[pack.parent_oxides.index("Na2O"), idx]) == 0

    # The three corrected A signs from datapack v1.0.1 errata.
    assert kernel.A[by_name["Mg2SiO4"]] == pytest.approx(-0.94)
    assert kernel.A[by_name["CaTiO3"]] == pytest.approx(-0.08)
    assert kernel.A[by_name["Na2Si2O5"]] == pytest.approx(-1.39)


def test_wt_to_mol_known_value() -> None:
    # Derivation: molar mass SiO2 = 60.0843 g/mol, FeO = 71.844 g/mol.
    # 60.0843 g SiO2 = 1.0000 mol SiO2; 71.844 g FeO = 1.0000 mol FeO.
    # A 60.0843 g + 71.844 g feed at 100 wt-% basis therefore yields exactly
    # 1 mol SiO2 + 1 mol FeO = 2 mol total, with the other six parents zero.
    pack = load_datapack(DATAPACK_PATH)
    composition = {
        "SiO2": 60.0843,
        "MgO": 0.0,
        "FeO": 71.844,
        "CaO": 0.0,
        "Al2O3": 0.0,
        "TiO2": 0.0,
        "Na2O": 0.0,
        "K2O": 0.0,
    }
    result = evaluate(
        composition,
        2500.0,
        pack,
        basis=60.0843 + 71.844,
        basis_type="wt",
    )
    # parent_oxides order: SiO2=0, MgO=1, FeO=2, ...
    assert np.isclose(result.parent_mol[0], 1.0, rtol=1.0e-12)
    assert np.isclose(result.parent_mol[1], 0.0, atol=1.0e-15)
    assert np.isclose(result.parent_mol[2], 1.0, rtol=1.0e-12)
    assert np.isclose(result.basis, 2.0, rtol=1.0e-12)
    assert result.labels.identity["datapack_version"] == "1.0.2"


def test_mol_basis_with_declared_basis() -> None:
    pack = load_datapack(DATAPACK_PATH)
    composition = {name: 0.125 for name in pack.parent_oxides}
    result = evaluate(composition, 2500.0, pack, basis=1.0, basis_type="mol")
    assert np.isclose(result.basis, 1.0, rtol=1.0e-12)
    assert np.isclose(result.parent_mol.sum(), 1.0, rtol=1.0e-12)


def test_refusal_composition_outside_validated_envelope() -> None:
    pack = load_datapack(DATAPACK_PATH)
    with pytest.raises(ImccCompositionOutsideValidatedEnvelopeError) as exc:
        evaluate(_make_alkali_composition(pack, 0.51), 2500.0, pack)
    assert exc.value.code == "imcc_composition_outside_validated_envelope"
    assert "X_Me2O=0.51" in str(exc.value)
    assert "bound 0.5" in str(exc.value)


def test_composition_envelope_boundary_is_inside() -> None:
    pack = load_datapack(DATAPACK_PATH)
    result = evaluate(_make_alkali_composition(pack, 0.5), 2500.0, pack)
    assert result.labels.envelope_status == "inside"


def test_allow_out_of_envelope_labels_result() -> None:
    pack = load_datapack(DATAPACK_PATH)
    result = evaluate(
        _make_alkali_composition(pack, 0.51),
        2500.0,
        pack,
        allow_out_of_envelope=True,
    )
    assert result.labels.envelope_status == "outside_validated"


def test_in_envelope_composition_labels_result() -> None:
    pack = load_datapack(DATAPACK_PATH)
    result = evaluate(_make_uniform_composition(pack), 2500.0, pack)
    assert result.labels.envelope_status == "inside"


def test_refusal_ferric_input_in_composition() -> None:
    pack = load_datapack(DATAPACK_PATH)
    composition = {"SiO2": 1.0, "Fe2O3": 0.1}
    with pytest.raises(ImccFerricInputUnsupportedError):
        evaluate(composition, 2500.0, pack)


def test_refusal_component_outside_domain_in_composition() -> None:
    pack = load_datapack(DATAPACK_PATH)
    composition = {"SiO2": 1.0, "P2O5": 0.1}
    with pytest.raises(ImccComponentOutsideDomainError):
        evaluate(composition, 2500.0, pack)


def test_refusal_composition_incomplete_basis_mismatch() -> None:
    pack = load_datapack(DATAPACK_PATH)
    composition = {"SiO2": 0.5, "FeO": 0.6}
    with pytest.raises(ImccCompositionIncompleteError):
        evaluate(composition, 2500.0, pack, basis=1.0)


def test_refusal_composition_incomplete_negative_value() -> None:
    pack = load_datapack(DATAPACK_PATH)
    composition = {"SiO2": -0.1, "FeO": 1.1}
    with pytest.raises(ImccCompositionIncompleteError):
        evaluate(composition, 2500.0, pack)


def test_refusal_T_outside_domain() -> None:
    pack = load_datapack(DATAPACK_PATH)
    composition = _make_uniform_composition(pack)
    with pytest.raises(ImccTOutsideDatapackDomainError):
        evaluate(composition, 500.0, pack)


def test_refusal_nonconvergence() -> None:
    pack = load_datapack(DATAPACK_PATH)
    composition = _make_uniform_composition(pack)
    with pytest.raises(ImccNonconvergenceError) as exc:
        evaluate(composition, 2500.0, pack, max_iter=0)
    assert exc.value.diagnostics["iterations"] == 0


def test_refusal_malformed_datapack(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps({"invalid": True}))
    with pytest.raises(ImccMalformedDatapackError):
        load_datapack(bad_path)


def test_refusal_malformed_datapack_bad_json(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("not json")
    with pytest.raises(ImccMalformedDatapackError):
        load_datapack(bad_path)


def test_refusal_contaminated_published_core_extension_row(tmp_path: Path) -> None:
    data = json.loads(DATAPACK_PATH.read_text())
    data["rows"][0].update(
        {
            "complex": "FeS",
            "nu": {"FeO": 1, "S": 1},
            "provenance_class": "extension-compound-thermo",
        }
    )
    bad_path = tmp_path / "contaminated.json"
    bad_path.write_text(json.dumps(data))

    with pytest.raises(ImccMalformedDatapackError) as exc:
        load_datapack(bad_path)
    assert exc.value.code == "imcc_malformed_datapack"
    assert "published core row 0" in str(exc.value)
    assert "unknown key 'S'" in str(exc.value)


def test_refusal_published_core_unknown_nu_key(tmp_path: Path) -> None:
    data = json.loads(DATAPACK_PATH.read_text())
    data["rows"][1]["nu"]["MnO"] = 1
    bad_path = tmp_path / "unknown-nu.json"
    bad_path.write_text(json.dumps(data))

    with pytest.raises(ImccMalformedDatapackError) as exc:
        load_datapack(bad_path)
    assert "published core row 1" in str(exc.value)
    assert "unknown key 'MnO'" in str(exc.value)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("complex", "FeS", "complex 'FeS'"),
        (
            "nu",
            {
                "SiO2": 1,
                "MgO": 3,
                "FeO": 0,
                "CaO": 0,
                "Al2O3": 0,
                "TiO2": 0,
                "Na2O": 0,
                "K2O": 0,
            },
            "nu['MgO']=3",
        ),
    ],
)
def test_refusal_published_core_manifest_deviation(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    data = json.loads(DATAPACK_PATH.read_text())
    data["rows"][0][field] = value
    bad_path = tmp_path / "manifest-deviation.json"
    bad_path.write_text(json.dumps(data))

    with pytest.raises(ImccMalformedDatapackError) as exc:
        load_datapack(bad_path)
    assert "published core row 0" in str(exc.value)
    assert message in str(exc.value)


def test_refusal_classes_inherit_from_imcc_refusal() -> None:
    for cls in (
        ImccComponentOutsideDomainError,
        ImccCompositionOutsideValidatedEnvelopeError,
        ImccCompositionIncompleteError,
        ImccFerricInputUnsupportedError,
        ImccNonconvergenceError,
        ImccTOutsideDatapackDomainError,
        ImccMalformedDatapackError,
    ):
        assert issubclass(cls, ImccRefusal)


def test_label_block_fields_and_denylist() -> None:
    pack = load_datapack(DATAPACK_PATH)
    composition = _make_uniform_composition(pack)
    result = evaluate(composition, 2500.0, pack)
    labels = result.labels
    assert isinstance(labels, ImccAdapterLabels)
    assert labels.identity["model_id"] == "IMCC-SF04"
    assert labels.identity["datapack_version"] == "1.0.2"
    assert labels.trust == canonical_backend_name("internal-analytical")
    assert labels.trust in CERTIFICATION_DENYLIST
    assert backend_name_denies_authority(labels.trust)
    for name in result.species_names:
        assert labels.coverage[name] == "A-published-imcc"


def test_package_exports_only_denied_adapter_solve_path() -> None:
    assert not any(name.startswith("solve_") for name in imcc_sf04.__all__)
    assert not any(name.startswith("solve_") for name in dir(imcc_sf04))
    assert not hasattr(imcc_sf04, "solve_imcc_sf04")

    pack = load_datapack(DATAPACK_PATH)
    result = imcc_sf04.evaluate(_make_uniform_composition(pack), 2500.0, pack)
    assert backend_name_denies_authority(result.labels.trust)


def test_extrapolation_flag() -> None:
    pack = load_datapack(DATAPACK_PATH)
    # Binary MgO-SiO2 at 1600 K: only Mg-silicate complexes are active, and
    # 1600 K is just below the SF04-exercised domain [1700, 3000] K.
    composition = {
        "SiO2": 0.5,
        "MgO": 0.5,
        "FeO": 0.0,
        "CaO": 0.0,
        "Al2O3": 0.0,
        "TiO2": 0.0,
        "Na2O": 0.0,
        "K2O": 0.0,
    }
    # Default: refusal.
    with pytest.raises(ImccTOutsideDatapackDomainError):
        evaluate(composition, 1600.0, pack)
    # Extrapolation flag: evaluate and mark.
    result = evaluate(composition, 1600.0, pack, allow_extrapolation=True)
    assert result.extrapolated is True
    assert isinstance(result.labels, ImccAdapterLabels)
