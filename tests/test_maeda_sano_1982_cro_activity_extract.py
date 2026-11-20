"""Rendered source pins and canonical store loading for Maeda & Sano (1982)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import UncertaintyKind
from simulator.battery.migrate import (
    Migrator,
    load_migrated_context,
    load_migrated_store,
    write_outputs,
)
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    fidelity_source_warnings,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
SID = "maeda-sano-1982-cro-activity-coefficient-slag"
EXPERIMENT_ID = (
    "10.2355/tetsutohagane1955.68.7_759::experiment::"
    "maeda_sano_1982_cao_mgo_alumina_silica_cr_equilibrium"
)
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
PINNED_GAMMA = {
    "maeda_sano_1982_f14_al2o3_20_row01": "134",
    "maeda_sano_1982_f14_al2o3_10_row01": "108",
    "maeda_sano_1982_f14_al2o3_20_row02": "88",
    "maeda_sano_1982_f14_al2o3_20_row03": "61",
    "maeda_sano_1982_f14_al2o3_10_row02": "60",
    "maeda_sano_1982_f14_al2o3_10_row03": "33",
    "maeda_sano_1982_f14_al2o3_10_row04": "23",
    "maeda_sano_1982_f14_al2o3_10_row05": "12",
    "maeda_sano_1982_f11_row01": "46.45",
    "maeda_sano_1982_f11_row02": "46.00",
    "maeda_sano_1982_f11_row03": "45.25",
    "maeda_sano_1982_f11_row04": "44.79",
    "maeda_sano_1982_f11_row05": "44.04",
    "maeda_sano_1982_f11_row06": "57.69",
}
FIGURE11_ROWS = {
    "maeda_sano_1982_f11_row01": ("30.1", "0.0200667709", "2.88482231e-5"),
    "maeda_sano_1982_f11_row02": ("30.0", "0.0198069759", "2.91295079e-5"),
    "maeda_sano_1982_f11_row03": ("30.1", "0.0195471808", "2.96150473e-5"),
    "maeda_sano_1982_f11_row04": ("30.0", "0.0192873857", "2.99142386e-5"),
    "maeda_sano_1982_f11_row05": ("30.1", "0.0190275906", "3.04237512e-5"),
    "maeda_sano_1982_f11_row06": ("24.1", "0.0199555760", "2.32264502e-5"),
}
EXPECTED_CR_CONVENTION = (
    "スラグ中クロムはほとんどが2価であると考えられる。したがって、"
    "本論文では、スラグ中クロムをすべてCrOと表記した。"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["CrO"]["observations"]
    }


def _derivation_parameter(row: dict, name: str) -> str:
    parameters = dict(row["values"]["derivation"]["parameters"])
    return parameters[name]["state"]["value"]


def _assert_experiment_cr_convention(document: dict) -> None:
    rows = document["species"]["CrO"]["context"]
    assumption = next(
        (
            row
            for row in rows
            if row["observation_id"]
            == "maeda_sano_1982_experiment_cr_valence_convention"
        ),
        None,
    )
    assert assumption is not None
    assert assumption["type"] == "experimental_assumption"
    assert assumption["experiment"] == EXPERIMENT_ID
    assert assumption["locator"] == {
        "page": 2,
        "published_page": 760,
        "pdf_page_index": 1,
        "section": "2.1",
    }
    assert assumption["values"]["chromium_valence_assumption_as_printed"] == (
        EXPECTED_CR_CONVENTION
    )
    assert "all slag chromium as CrO" in assumption["values"][
        "chromium_valence_assumption"
    ]


def _assert_render_pins(document: dict) -> None:
    rows = _rows(document)
    assert set(rows) == set(PINNED_GAMMA)
    for observation_id, expected in PINNED_GAMMA.items():
        assert rows[observation_id]["values"]["activity_coefficient"] == expected

    expected_ppm = ("30.1", "30.0", "30.1", "30.0", "30.1", "24.1")
    expected_compositions = (
        (("CaO", "10"), ("MgO", "35"), ("Al2O3", "15"), ("SiO2", "40")),
        (("CaO", "10"), ("MgO", "30"), ("Al2O3", "20"), ("SiO2", "40")),
        (("CaO", "10"), ("MgO", "25"), ("Al2O3", "25"), ("SiO2", "40")),
        (("CaO", "10"), ("MgO", "20"), ("Al2O3", "30"), ("SiO2", "40")),
        (("CaO", "10"), ("MgO", "15"), ("Al2O3", "35"), ("SiO2", "40")),
        (("CaO", "10"), ("MgO", "30"), ("Al2O3", "25"), ("SiO2", "35")),
    )
    for index, (observation_id, (ppm, total_moles, mole_fraction)) in enumerate(
        FIGURE11_ROWS.items()
    ):
        row = rows[observation_id]
        values = row["values"]
        assert values["equilibrium_Cr_ppm_as_published"] == expected_ppm[index]
        assert values["equilibrium_Cr_ppm_as_published"] == ppm
        assert tuple(map(tuple, values["composition_wt_pct"]["components"])) == (
            expected_compositions[index]
        )
        assert values["N_CrO_as_calculated"] == mole_fraction
        assert _derivation_parameter(row, "equilibrium_Cr_ppm") == ppm
        assert (
            _derivation_parameter(row, "total_moles_per_g_AlO1.5_basis")
            == total_moles
        )
        assert _derivation_parameter(row, "chromium_molar_mass_g_per_mol") == "51.996"
        assert "footnote-3 shortcut is not used" in values["derivation"]["method"][
            "state"
        ]["value"]
        assert "activity_coefficient_uncertainty_absolute" not in values
        assert "uncertainty" not in row

    first_f14 = rows["maeda_sano_1982_f14_al2o3_20_row01"]
    assert first_f14["values"]["composition_wt_pct"]["components"] == [
        ["CaO", "46.6"],
        ["MgO", "0"],
        ["Al2O3", "20"],
        ["SiO2", "33.4"],
    ]
    assert first_f14["values"]["a_CrO_as_published"] == "3.79e-3"
    assert first_f14["uncertainty"].startswith("±3 gamma units")
    assert "not a source-reported" in first_f14["uncertainty"]
    assert "activity_coefficient_uncertainty_absolute" not in first_f14["values"]
    _assert_experiment_cr_convention(document)


def test_render_values_are_pinned_and_mutations_restore_reviewed_defects() -> None:
    document = _document()
    _assert_render_pins(document)

    old_approximation = deepcopy(document)
    _rows(old_approximation)["maeda_sano_1982_f11_row01"]["values"][
        "activity_coefficient"
    ] = "40.1"
    with pytest.raises(AssertionError):
        _assert_render_pins(old_approximation)

    changed_cr = deepcopy(document)
    _rows(changed_cr)["maeda_sano_1982_f11_row01"]["values"][
        "equilibrium_Cr_ppm_as_published"
    ] = "30.2"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_cr)

    changed_basis = deepcopy(document)
    changed_basis["species"]["CrO"]["observations"][-1]["values"]["derivation"][
        "parameters"
    ][2][1]["state"]["value"] = "0.01732"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_basis)

    missing_convention = deepcopy(document)
    missing_convention["species"]["CrO"]["context"].clear()
    with pytest.raises(AssertionError):
        _assert_experiment_cr_convention(missing_convention)


def test_fidelity_attestations_open_their_locator_and_mutations_are_caught(
    tmp_path: Path,
) -> None:
    document = _document()
    _assert_render_pins(document)
    assert all(
        sample["reviewed_by"] == "ror-maedax1120"
        and sample["reviewed_on"] == "2026-11-20"
        for sample in document["fidelity_samples"]
    )
    warnings = fidelity_source_warnings(document, label=SID, artifact_root=ROOT)
    warning_counts = {warning.kind: warning.count for warning in warnings}
    assert "unattested_sample" not in warning_counts
    assert "source_blind_pin" not in warning_counts
    assert check_all_fidelity_samples_match(document, label=SID) == []

    missing_reviewer = deepcopy(document)
    del missing_reviewer["fidelity_samples"][0]["reviewed_by"]
    warnings = fidelity_source_warnings(missing_reviewer, label=SID, artifact_root=ROOT)
    warning_counts = {warning.kind: warning.count for warning in warnings}
    assert warning_counts["unattested_sample"] == 1
    assert "source_blind_pin" not in warning_counts

    bad_source = deepcopy(document)
    relative_source = "docs/references/pdfs/02-thermochemistry/figure-checks.md"
    source_copy = tmp_path / relative_source
    source_copy.parent.mkdir(parents=True)
    source_text = (
        ROOT
        / "docs/references/pdfs/02-thermochemistry/maeda-sano-1982-figure-reading-checks.md"
    ).read_text(encoding="utf-8")
    source_copy.write_text(source_text.replace("gamma_CrO = 134", "gamma_CrO = 135"), encoding="utf-8")
    for sample in bad_source["fidelity_samples"]:
        sample["locator"]["source_path"] = relative_source
    warnings = fidelity_source_warnings(bad_source, label=SID, artifact_root=tmp_path)
    warning_counts = {warning.kind: warning.count for warning in warnings}
    assert warning_counts["source_blind_pin"] == 1


def test_extract_and_generated_store_validate_and_load_all_observations(
    tmp_path: Path,
) -> None:
    document = _document()
    _assert_render_pins(document)
    load_migrated_store(ROOT, sources=[SID])
    assert validate_extract_document(
        document, path=EXTRACT, expected_source_id=SID
    ) == []
    assert check_all_fidelity_samples_match(document, label=SID) == []

    index = {SID: {"corpus": {"raw": {"path": document["source"]["corpus_asset"]}}}}
    migrator = Migrator(root=ROOT, index=index, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len([row for row in result.observations.values() if row.source_id == SID]) == 14

    write_outputs(result, root=tmp_path)
    works, experiments, observations = load_migrated_store(tmp_path, sources=[SID])
    assert len(observations) == 14
    assert EXPERIMENT_ID in experiments
    assert experiments[EXPERIMENT_ID].work_id in works
    context = load_migrated_context(tmp_path)
    assumption = context[f"{SID}::context::maeda_sano_1982_experiment_cr_valence_convention"]
    assert assumption["experiment"] == EXPERIMENT_ID
    assert assumption["locator"]["published_page"] == 760
    assert assumption["values"]["chromium_valence_assumption_as_printed"] == (
        EXPECTED_CR_CONVENTION
    )

    source_rows = _rows(document)
    for observation in observations.values():
        local_id = observation.observation_id.removeprefix(f"{SID}::")
        source_row = source_rows[local_id]
        source_values = source_row["values"]
        assert observation.experiment_id == EXPERIMENT_ID
        assert observation.identity.temperature_K is not None
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == (
            Decimal(source_values["T_C"]) + Decimal("273.15")
        )
        assert observation.point_conditions is not None
        conditions = observation.point_conditions
        assert conditions["temperature_C"].locator is not None
        assert conditions["composition"].locator is not None
        assert conditions["composition"].state.value == observation.identity.composition.value
        assert conditions["oxygen_buffer"].state.value == (
            "a_C = 1, P_CO = 1 atm, a_Cr3C2 = 1"
        )
        assert conditions["oxygen_buffer"].locator is not None
        assert conditions["chromium_valence"].locator is not None
        assert conditions["chromium_valence_as_printed"].locator is not None
        if source_values["T_C"] == "1500":
            assert observation.uncertainty.kind is UncertaintyKind.PRINTED
            assert "±3 gamma units" in str(observation.uncertainty.verbatim)
            assert conditions["oxygen_partial_pressure_atm"].state.is_value
        else:
            assert observation.uncertainty.kind is UncertaintyKind.NONE
            assert conditions["oxygen_partial_pressure_atm"].state.is_unknown
            assert observation.derivation is not None
            assert observation.derivation.references == (
                "Eq. (13)",
                "Fig. 11",
                "§2.1 chromium convention",
            )
            assert observation.value.point == Decimal(source_values["activity_coefficient"])

    malformed_store = tmp_path / "data/literature/extracts-v2" / f"{SID}.yaml"
    generated = yaml.safe_load(malformed_store.read_text(encoding="utf-8"))
    generated["observations"][0]["uncertainty"] = {
        "kind": "derived",
        "value": "3",
        "basis": "figure_digitization_gamma_units",
    }
    malformed_store.write_text(
        yaml.safe_dump(generated, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="derived uncertainty requires a Derivation"):
        load_migrated_store(tmp_path, sources=[SID])
