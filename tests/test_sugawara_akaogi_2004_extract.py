"""Source-grounded regression guards for Sugawara & Akaogi (2004)."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity
from simulator.battery.identity import identity_equal
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/sugawara-akaogi-2004-liquid-calorimetry.yaml"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation(document: dict, observation_id: str) -> dict:
    return next(
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == observation_id
    )


def _context(document: dict, observation_id: str) -> dict:
    return next(
        row
        for species in document["species"].values()
        for row in species.get("context", [])
        if row["observation_id"] == observation_id
    )


def _table1_cell(document: dict, sample: str) -> dict:
    for species in document["species"].values():
        for observation in species.get("observations", []):
            for row in observation.get("values", {}).get("series", []):
                if row.get("sample") == sample:
                    return row
        for context in species.get("context", []):
            for row in context.get("values", {}).get("rows", []):
                if row.get("sample") == sample:
                    return row
    raise AssertionError(f"Table 1 sample {sample!r} is absent")


def _assert_independent_printed_cells(document: dict) -> None:
    # These facts were transcribed from the supplied PDF: Table 1, p. 1588;
    # Equations 4, 8 and 11 and their end-member evaluations, pp. 1589–1591.
    expected_rows = {
        "NF-1": (210.6, 7.9),
        "NF-2": (213.4, 6.0),
        "NFS-1": (187.2, 7.9),
        "NAc-5": (297.5, 6.7),
        "Ac-4": (216.2, 12.8),
        "Hematite": (152.2, 6.4),
    }
    for sample, (enthalpy, width) in expected_rows.items():
        row = _table1_cell(document, sample)
        assert row["enthalpy_increment"] == enthalpy
        assert row["uncertainty_kJ_mol"] == width

    expected_endmembers = {
        "suga04_eq4_na2sio3_endmember_enthalpy": (209.5, 8.9),
        "suga04_eq4_quartz_endmember_enthalpy": (76.4, 8.9),
        "suga04_eq8_hematite_endmember_enthalpy": (235.5, 8.3),
        "suga04_eq11_acmite_endmember_enthalpy": (326.5, 9.4),
    }
    for observation_id, (enthalpy, width) in expected_endmembers.items():
        values = _observation(document, observation_id)["values"]
        assert values["enthalpy_increment"] == enthalpy
        assert values["uncertainty_kJ_mol"] == width


@pytest.fixture(scope="module")
def migrated() -> Migrator:
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def test_extract_validates_and_fidelity_sample_matches() -> None:
    assert validate_extract_document(
        _document(),
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []


def test_independent_source_cells_pin_table1_and_endmembers() -> None:
    _assert_independent_printed_cells(_document())


def test_independent_pins_reject_paired_value_and_composition_mutations() -> None:
    document = _document()
    mutated_value = deepcopy(document)
    _table1_cell(mutated_value, "NF-1")["enthalpy_increment"] = 211.6
    mutated_value["fidelity_samples"][0]["value"]["enthalpy_increment"] = 211.6
    with pytest.raises(AssertionError):
        _assert_independent_printed_cells(mutated_value)

    mutated_composition = deepcopy(document)
    nf2 = _table1_cell(mutated_composition, "NF-2")
    nf2["composition_mole_fraction"]["Fe2O3"] = 0.121
    with pytest.raises(AssertionError):
        _assert_mixture_rows(mutated_composition)


def _assert_mixture_rows(document: dict) -> None:
    nf_rows = _context(document, "suga04_t1_nf_ns_nfs_mixture_enthalpies")[
        "values"
    ]
    nac_rows = _context(document, "suga04_t1_nac_mixture_enthalpies")["values"]
    assert nf_rows["quantity_as_printed"] == "H_minus_H298"
    assert nf_rows["composition_basis"] == "mole_fraction"
    assert nac_rows["quantity_as_printed"] == "H_minus_H298"
    assert nac_rows["composition_basis"] == "mole_fraction"
    nf2 = next(row for row in nf_rows["rows"] if row["sample"] == "NF-2")
    assert nf2["composition_mole_fraction"] == {
        "Na2SiO3": 0.880,
        "Fe2O3": 0.120,
        "SiO2": 0.000,
        "NaFeSi2O6": 0.000,
    }
    nfs1 = next(row for row in nf_rows["rows"] if row["sample"] == "NFS-1")
    assert nfs1["composition_mole_fraction"] == {
        "Na2SiO3": 0.692,
        "Fe2O3": 0.077,
        "SiO2": 0.231,
        "NaFeSi2O6": 0.000,
    }
    nac5 = next(row for row in nac_rows["rows"] if row["sample"] == "NAc-5")
    assert nac5["composition_mole_fraction"] == {
        "Na2SiO3": 0.200,
        "Fe2O3": 0.000,
        "SiO2": 0.000,
        "NaFeSi2O6": 0.800,
    }
    assert (nf2["enthalpy_increment"], nf2["uncertainty_kJ_mol"]) == (213.4, 6.0)
    assert (nfs1["enthalpy_increment"], nfs1["uncertainty_kJ_mol"]) == (187.2, 7.9)
    assert (nac5["enthalpy_increment"], nac5["uncertainty_kJ_mol"]) == (297.5, 6.7)


def test_mixture_rows_stay_context_with_source_composition_and_quantity(
    migrated: Migrator,
) -> None:
    document = _document()
    _assert_mixture_rows(document)
    mixture_context = [
        record
        for rows in migrated.result.context_by_work.values()
        for record in rows
        if record["observation_id"]
        in {
            "suga04_t1_nf_ns_nfs_mixture_enthalpies",
            "suga04_t1_nac_mixture_enthalpies",
        }
    ]
    assert len(mixture_context) == 2
    rows = [row for context in mixture_context for row in context["values"]["rows"]]
    assert len(rows) == 16
    assert all("composition_mole_fraction" in row for row in rows)
    assert all(row["T_K"] == 1373 and row["state"] == "Liquid" for row in rows)
    assert all(context["values"]["quantity_as_printed"] == "H_minus_H298" for context in mixture_context)
    assert len(
        [
            observation
            for observation in migrated.result.observations.values()
            if observation.locator is not None and observation.locator.table == "1"
        ]
    ) == 6
    # The H_minus_H298 profile excludes composition, so no mixture may acquire
    # a pure Na2SiO3 or NaFeSi2O6 battery identity.
    assert all(
        observation.identity.composition.tag.value == "not_applicable"
        for observation in migrated.result.observations.values()
    )


def test_pure_heat_contents_migrate_with_conditions_and_printed_uncertainty(
    migrated: Migrator,
) -> None:
    table1 = {
        (
            observation.identity.species.formula,
            int(observation.identity.temperature_K.value),
        ): observation
        for observation in migrated.result.observations.values()
        if observation.locator is not None and observation.locator.table == "1"
    }
    expected = {
        ("Na2SiO3", 1373): (210.6, 7.9, Phase.L),
        ("NaFeSi2O6", 923): (130.8, 6.6, Phase.CR),
        ("NaFeSi2O6", 1023): (168.8, 8.4, Phase.CR),
        ("NaFeSi2O6", 1123): (194.8, 10.7, Phase.CR),
        ("NaFeSi2O6", 1223): (216.2, 12.8, Phase.CR),
        ("Fe2O3", 1373): (152.2, 6.4, Phase.CR),
    }
    assert set(table1) == set(expected)
    for key, (enthalpy, width, phase) in expected.items():
        observation = table1[key]
        assert observation.identity.quantity.value is Quantity.H_MINUS_H298
        assert observation.identity.species.phase.value is phase
        assert observation.value.point == Decimal(str(enthalpy))
        assert observation.uncertainty.verbatim["sigma"] == width
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.value == Decimal(key[1])
        assert temperature.locator is not None
        assert observation.locator is not None
        assert observation.locator.table == "1"

    endpoints = {
        "suga04_eq4_na2sio3_endmember_enthalpy": ("Na2SiO3", 209.5, 8.9, "4"),
        "suga04_eq4_quartz_endmember_enthalpy": ("SiO2", 76.4, 8.9, "4"),
        "suga04_eq8_hematite_endmember_enthalpy": ("Fe2O3", 235.5, 8.3, "8"),
        "suga04_eq11_acmite_endmember_enthalpy": ("NaFeSi2O6", 326.5, 9.4, "11"),
    }
    for observation_id, (formula, enthalpy, width, equation) in endpoints.items():
        observation = migrated.result.observations[
            f"{EXTRACT.stem}::{observation_id}"
        ]
        assert observation.identity.quantity.value is Quantity.H_MINUS_H298
        assert observation.identity.species.formula == formula
        assert observation.identity.temperature_K.value == Decimal(1373)
        assert observation.value.point == Decimal(str(enthalpy))
        assert observation.uncertainty.verbatim["value"] == width
        assert observation.locator.equation == equation
        assert observation.point_conditions["temperature_K"].locator is not None
        assert observation.evidence.class_.value is EvidenceClass.AUTHOR_ESTIMATE

    assert sum(
        identity_equal(observation.identity, observation.identity).kind.value
        == "identity_unknown"
        for observation in migrated.result.observations.values()
    ) == 10
    assert not migrated.result.validation.hard_issues


def test_fits_fusion_excess_and_duplicate_comparisons_keep_distinct_meanings(
    migrated: Migrator,
) -> None:
    document = _document()
    eq4 = _context(document, "suga04_eq4_na2sio3_sio2_fit")["values"]
    eq8 = _context(document, "suga04_eq8_na2sio3_fe2o3_fit")["values"]
    eq11 = _context(document, "suga04_eq11_na2sio3_acmite_fit")["values"]
    assert (eq4["intercept_kJ_mol"], eq4["slope_kJ_mol_per_mol_percent"]) == (209.5, -1.331)
    assert (eq8["intercept_kJ_mol"], eq8["slope_kJ_mol_per_mol_percent"]) == (210.0, 0.255)
    assert (eq11["intercept_kJ_mol"], eq11["slope_kJ_mol_per_mol_percent"]) == (212.9, 1.136)
    assert all(fit["composition_scale"] == "mol_percent" for fit in (eq4, eq8, eq11))

    hematite_fusion = _context(document, "suga04_hematite_fusion_at_1373_context")
    excess = _context(document, "suga04_t3_nfs_excess_enthalpy_context")["values"]
    assert hematite_fusion["type"] == "fusion_enthalpy_context"
    assert hematite_fusion["values"]["delta_H_fusion_kJ_mol"] == 83.3
    assert excess["quantity_as_printed"] == "excess enthalpy"
    assert excess["definition_as_printed"] == "measured H minus calculated H"
    assert [row["excess_enthalpy_kJ_mol"] for row in excess["rows"]] == [
        5.6,
        -3.0,
        -2.5,
        -6.1,
    ]
    assert {o.identity.quantity.value for o in migrated.result.observations.values()} == {
        Quantity.H_MINUS_H298
    }

    figure8 = _context(document, "suga04_figure8_ko_1977_caption")
    assert figure8["values"]["fetch_candidate"] == "Ko et al. (1977) primary source"
    assert "series" not in figure8["values"] and "rows" not in figure8["values"]
    figure6 = _context(document, "suga04_figure6_entropy_fusion_comparison")
    assert figure6["values"]["points"] == [
        {
            "species": "Fe2O3",
            "Fe_O_ratio": 0.67,
            "delta_S_fusion_per_atom": 14,
            "attribution": "This study",
            "method_class": "figure_only",
        }
    ]

    table4 = _context(document, "suga04_table4_fusion_comparisons")
    diopside = next(row for row in table4["values"]["rows"] if row["name"] == "Diopside")
    assert "duplicates ammin-76-904" in diopside["comparison_as_printed"]
    assert not any(
        observation.identity.species.formula == "CaMgSi2O6"
        for observation in migrated.result.observations.values()
    )
