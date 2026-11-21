"""Rendered source pins and canonical store loading for Maeda & Sano (1982)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
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
FIGURE11_CONTEXT_ID = (
    f"{SID}::context::maeda_sano_1982_experiment_cr_valence_convention"
)
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
FORMULA_MASSES = {
    "CaO": "56.0774",
    "MgO": "40.3044",
    "Al2O3": "101.9613",
    "SiO2": "60.0843",
}
FIGURE11_DIGITIZATION_BANDS = {
    "maeda_sano_1982_f11_row01": "0.12",
    "maeda_sano_1982_f11_row02": "0.12",
    "maeda_sano_1982_f11_row03": "0.12",
    "maeda_sano_1982_f11_row04": "0.12",
    "maeda_sano_1982_f11_row05": "0.12",
    "maeda_sano_1982_f11_row06": "0.15",
}
ATOMIC_WEIGHT_SOURCE = (
    "External constant: IUPAC 2007 Periodic Table of the Elements; "
    "chromium standard atomic weight 51.9961 rounded to 51.996."
)
FORMULA_MASS_SOURCE = (
    "Calculated from IUPAC 2007 standard atomic weights (Ca 40.078, "
    "Mg 24.3050, Al 26.9815386, Si 28.0855, O 15.9994): "
    "CaO 56.0774, MgO 40.3044, Al2O3 101.9613, SiO2 60.0843 g/mol."
)


def _fig11_digitization_band(row: dict) -> Decimal:
    # Premise: Fig. 11 gamma uses the AlO1.5 basis. A 1 wt-point MgO-to-Al2O3
    # exchange changes total moles by 0.01/M_MgO - 2*0.01/M_Al2O3 mol/g,
    # because each Al2O3 contributes two AlO1.5 formula units. With activity
    # and Cr ppm fixed, gamma = a/N and N = (ppm*1e-6/M_Cr)/n_total imply
    # gamma is proportional to n_total. Thus |delta gamma| = gamma *
    # |delta n_total| / n_total. Both n terms have mol/g units, so their ratio
    # is dimensionless and the result is in gamma units. Row 1 is about 0.12
    # (~0.26% of gamma 46.45), a useful sanity check.
    parameters = dict(row["values"]["derivation"]["parameters"])
    total_moles = Decimal(
        parameters["total_moles_per_g_AlO1.5_basis"]["state"]["value"]
    )
    mg_o_mass = Decimal(parameters["MgO_formula_mass_g_per_mol"]["state"]["value"])
    al_o_mass = Decimal(parameters["Al2O3_formula_mass_g_per_mol"]["state"]["value"])
    cr_mass = Decimal(parameters["chromium_molar_mass_g_per_mol"]["state"]["value"])
    cr_ppm = Decimal(parameters["equilibrium_Cr_ppm"]["state"]["value"])
    activity = Decimal(parameters["a_CrO"]["state"]["value"])
    exchanged_moles = Decimal("0.01") / mg_o_mass - Decimal("0.02") / al_o_mass
    cr_mole_fraction = (cr_ppm * Decimal("1e-6") / cr_mass) / total_moles
    gamma = activity / cr_mole_fraction
    return (gamma * abs(exchanged_moles) / total_moles).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
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
    assert "chromium_valence_assumption_as_printed" not in assumption["values"]
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
        assert row["derived_from"] == [FIGURE11_CONTEXT_ID]
        assert values["derivation"]["inputs"] == [FIGURE11_CONTEXT_ID]
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
        parameters = dict(row["values"]["derivation"]["parameters"])
        chromium_mass = parameters["chromium_molar_mass_g_per_mol"]
        assert chromium_mass["locator"] == {"note": ATOMIC_WEIGHT_SOURCE}
        for oxide, formula_mass in FORMULA_MASSES.items():
            parameter = parameters[f"{oxide}_formula_mass_g_per_mol"]
            assert parameter["state"]["value"] == formula_mass
            assert parameter["locator"] == {"note": FORMULA_MASS_SOURCE}
        assert "footnote-3 shortcut is not used" in values["derivation"]["method"][
            "state"
        ]["value"]
        assert "activity_coefficient_uncertainty_absolute" not in values
        assert row["uncertainty"]["kind"] == "derived"
        assert row["uncertainty"]["value"] == FIGURE11_DIGITIZATION_BANDS[
            observation_id
        ]
        assert _fig11_digitization_band(row) == Decimal(
            FIGURE11_DIGITIZATION_BANDS[observation_id]
        )
        assert row["uncertainty"]["basis"] == "gamma_CrO_absolute_units"
        assert row["uncertainty"]["derivation"]["relation"] == (
            "propagated_fig11_composition_digitization"
        )
        uncertainty_method = row["uncertainty"]["derivation"]["method"][
            "state"
        ]["value"]
        assert "AlO1.5 basis" in uncertainty_method
        assert "Al2O3 contributes two AlO1.5 formula units" in uncertainty_method

    first_f14 = rows["maeda_sano_1982_f14_al2o3_20_row01"]
    assert first_f14["values"]["composition_wt_pct"]["components"] == [
        ["CaO", "46.6"],
        ["MgO", "0"],
        ["Al2O3", "20"],
        ["SiO2", "33.4"],
    ]
    assert first_f14["values"]["a_CrO_as_published"] == "3.79e-3"
    for observation_id in PINNED_GAMMA:
        if "_f14_" not in observation_id:
            continue
        uncertainty = rows[observation_id]["uncertainty"]
        assert uncertainty["kind"] == "derived"
        assert uncertainty["value"] == "3"
        assert uncertainty["basis"] == "gamma_CrO_absolute_units"
        assert uncertainty["derivation"]["relation"] == (
            "figure_digitization_bound"
        )
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

    false_mass_source = deepcopy(document)
    mass_parameter = dict(
        _rows(false_mass_source)["maeda_sano_1982_f11_row01"]["values"][
            "derivation"
        ]["parameters"]
    )["chromium_molar_mass_g_per_mol"]
    mass_parameter["locator"] = {
        "page": 6,
        "published_page": 764,
        "pdf_page_index": 5,
        "equation": "13",
    }
    with pytest.raises(AssertionError):
        _assert_render_pins(false_mass_source)

    wrong_formula_mass = deepcopy(document)
    mass_parameter = dict(
        _rows(wrong_formula_mass)["maeda_sano_1982_f11_row01"]["values"][
            "derivation"
        ]["parameters"]
    )["CaO_formula_mass_g_per_mol"]
    mass_parameter["state"]["value"] = "56.077"
    with pytest.raises(AssertionError):
        _assert_render_pins(wrong_formula_mass)

    false_printed_assumption = deepcopy(document)
    false_printed_assumption["species"]["CrO"]["context"][0]["values"][
        "chromium_valence_assumption_as_printed"
    ] = "normalized source text"
    with pytest.raises(AssertionError):
        _assert_render_pins(false_printed_assumption)

    wrong_fig14_uncertainty = deepcopy(document)
    _rows(wrong_fig14_uncertainty)["maeda_sano_1982_f14_al2o3_20_row01"][
        "uncertainty"
    ]["kind"] = "printed"
    with pytest.raises(AssertionError):
        _assert_render_pins(wrong_fig14_uncertainty)

    wrong_fig11_uncertainty = deepcopy(document)
    _rows(wrong_fig11_uncertainty)["maeda_sano_1982_f11_row01"]["uncertainty"][
        "value"
    ] = "0.18"
    with pytest.raises(AssertionError):
        _assert_render_pins(wrong_fig11_uncertainty)

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
    resolvable_record_ids = set(observations) | set(context)
    assumption = context[f"{SID}::context::maeda_sano_1982_experiment_cr_valence_convention"]
    assert assumption["experiment"] == EXPERIMENT_ID
    assert assumption["locator"]["published_page"] == 760
    assert "chromium_valence_assumption_as_printed" not in assumption["values"]

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
            assert observation.uncertainty.kind is UncertaintyKind.DERIVED
            assert observation.uncertainty.value == Decimal("3")
            assert observation.uncertainty.basis == "gamma_CrO_absolute_units"
            assert observation.uncertainty.derivation is not None
            assert observation.uncertainty.derivation.relation == (
                "figure_digitization_bound"
            )
            assert conditions["oxygen_partial_pressure_atm"].state.is_value
        else:
            assert observation.uncertainty.kind is UncertaintyKind.DERIVED
            assert observation.uncertainty.value == Decimal(
                FIGURE11_DIGITIZATION_BANDS[local_id]
            )
            assert observation.uncertainty.basis == "gamma_CrO_absolute_units"
            assert observation.uncertainty.derivation is not None
            assert observation.uncertainty.derivation.relation == (
                "propagated_fig11_composition_digitization"
            )
            assert conditions["oxygen_partial_pressure_atm"].state.is_unknown
            assert observation.derivation is not None
            assert observation.derived_from == (FIGURE11_CONTEXT_ID,)
            assert observation.derivation.inputs == (FIGURE11_CONTEXT_ID,)
            resolved_references = (
                *(observation.derived_from or ()),
                *observation.derivation.inputs,
            )
            assert all(
                reference in resolvable_record_ids for reference in resolved_references
            )
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
