from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile

import pytest
import yaml

from simulator.battery.enums import (
    AmountBasis,
    NoticeKind,
    Phase,
    PhaseRole,
    Quantity,
    RawInputStatus,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.composition import (
    _formula_molar_mass,
    _element_wt_percent_stoichiometric_oxide_projection,
)
from simulator.battery.identity import celsius_to_kelvin
from simulator.battery.input_provenance import (
    PRINTED_STARTING_BULK,
    composition_with_provenance,
)
from tests.battery.factories import migrate_one_extract
from tests.battery.literature_extract_grids import (
    assert_observation_grid_matches,
    decimal_text,
    read_observation_grid_fixture,
)
from tests.helpers.corpus_paths import regolith_corpus_root

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "xu-2026-excess-oxygen-magma"
EXTRACT = ROOT / "data/literature/extracts/xu-2026-excess-oxygen-magma.yaml"
TABLE1_GRID = ROOT / "tests/fixtures/xu-2026-table1-render-grid.tsv"
S2_GRID = ROOT / "tests/fixtures/xu-2026-table-s2-glass-grid.tsv"
GRID_FIELDS = (
    "observation_id",
    "species_formula",
    "sample_id_as_published",
    "experiment",
    "T_C",
    "total_pressure_Pa",
    "oxygen_source",
    "phase_label_as_published",
    "assemblage_as_published",
    "basis",
    "component_amount",
    "component_amount_as_published",
    "sigma",
    "page",
    "table",
    "method_class",
)
NUMERIC_FIELDS = ("T_C", "total_pressure_Pa", "component_amount", "sigma")
def _fixture(path: Path) -> list[dict[str, str]]:
    return read_observation_grid_fixture(path, NUMERIC_FIELDS)


def _pdf_page_with_proof(pdf: Path, proof: str) -> int:
    info = subprocess.run(
        ["pdfinfo", str(pdf)], check=True, capture_output=True, text=True
    )
    page_count_match = re.search(r"^Pages:\s*(\d+)$", info.stdout, re.MULTILINE)
    assert page_count_match is not None
    normalized_proof = re.sub(r"\s+", " ", proof).strip()
    for page_number in range(1, int(page_count_match.group(1)) + 1):
        page_text = subprocess.run(
            [
                "pdftotext",
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                str(pdf),
                "-",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        page_text = re.sub(r"(?m)^[ \t]*\d{1,3}[ \t]*\r?$", "", page_text)
        page_text = re.sub(r"(?m)^[ \t]*\d{1,3}[ \t]+", "", page_text)
        page_text = re.sub(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)", "", page_text)
        normalized_page_text = re.sub(r"\s+", " ", page_text).strip()
        if normalized_proof in normalized_page_text:
            return page_number
    raise AssertionError(f"PDF proof was not found: {proof}")


def _supplement_pdf(tmp_path: Path) -> Path:
    support = Path.home() / "Repos/regolith-sources/_supporting" / SOURCE_ID
    direct_pdf = support / "XuEtAl_Suppl_0820.pdf"
    if direct_pdf.is_file():
        return direct_pdf
    archive = support / "publisher-supplement.zip"
    if not archive.is_file():
        archive = (
            Path.home()
            / "Repos/regolith-sources/_incoming"
            / SOURCE_ID
            / "supporting/publisher-supplement.zip"
        )
    if not archive.is_file():
        pytest.skip("Xu supplementary PDF archive is absent")
    with ZipFile(archive) as source:
        matches = [
            name
            for name in source.namelist()
            if Path(name).name == "XuEtAl_Suppl_0820.pdf"
        ]
        assert len(matches) == 1
        target = tmp_path / "XuEtAl_Suppl_0820.pdf"
        target.write_bytes(source.read(matches[0]))
    return target


def _extract_grid(document: dict, prefixes: tuple[str, ...]) -> list[dict[str, str]]:
    rows = []
    for species_formula, species in document["species"].items():
        for observation in species.get("observations", []):
            observation_id = observation["observation_id"]
            if not observation_id.startswith(prefixes):
                continue
            values = observation["values"]
            locator = observation["locator"]
            conditions = values["point_conditions"]
            rows.append(
                {
                    "observation_id": observation_id,
                    "species_formula": values.get("formula", species_formula),
                    "sample_id_as_published": values["sample_id_as_published"],
                    "experiment": observation["experiment"],
                    "T_C": decimal_text(values["T_C"]),
                    "total_pressure_Pa": decimal_text(
                        conditions["total_pressure_Pa"]["state"]["value"]["point"]
                    ),
                    "oxygen_source": values["oxygen_source_as_published"],
                    "phase_label_as_published": values["phase_label_as_published"],
                    "assemblage_as_published": values["assemblage_as_published"],
                    "basis": values["basis"],
                    "component_amount": decimal_text(values["component_amount"]),
                    "component_amount_as_published": str(values["component_amount_as_published"]),
                    "sigma": decimal_text((observation.get("uncertainty") or {}).get("sigma")),
                    "page": str(locator.get("page", "")),
                    "table": str(locator["table"]),
                    "method_class": values["method_class"],
                }
            )
    return sorted(rows, key=lambda row: row["observation_id"])


def _assert_grid(document: dict, prefixes: tuple[str, ...], fixture: list[dict[str, str]]) -> None:
    assert_observation_grid_matches(_extract_grid(document, prefixes), fixture, GRID_FIELDS)


def test_independent_render_and_workbook_grids_reject_both_mutations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table1 = _fixture(TABLE1_GRID)
    s2 = _fixture(S2_GRID)
    assert len(table1) == 273
    assert len(s2) == 1188
    _assert_grid(document, ("xu_2026_table1_",), table1)
    _assert_grid(document, ("xu_2026_s2_",), s2)

    changed_fixture = deepcopy(table1)
    changed_fixture[0]["component_amount"] = str(
        Decimal(changed_fixture[0]["component_amount"]) + 1
    )
    with pytest.raises(AssertionError):
        _assert_grid(document, ("xu_2026_table1_",), changed_fixture)

    changed_extract = deepcopy(document)
    first_id = table1[0]["observation_id"]
    first = next(
        observation
        for species in changed_extract["species"].values()
        for observation in species.get("observations", [])
        if observation["observation_id"] == first_id
    )
    first["values"]["component_amount"] = str(
        Decimal(first["values"]["component_amount"]) + 1
    )
    with pytest.raises(AssertionError):
        _assert_grid(changed_extract, ("xu_2026_table1_",), table1)


def test_typed_glass_components_migrate_with_exact_samples_conditions_and_points(
    tmp_path: Path,
) -> None:
    table1 = _fixture(TABLE1_GRID)
    s2 = _fixture(S2_GRID)
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert "assumed_valence_Fe" not in EXTRACT.read_text(encoding="utf-8")
    experiments = {row["experiment_id"]: row for row in document["experiments"]}
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues

    observations = {
        observation.observation_id.split("::", 1)[1]: observation
        for observation in migrator.result.observations.values()
    }
    raw_observations = {
        observation["observation_id"]: observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
    }
    assert len(table1) + len(s2) == 1461
    assert len(observations) == len(table1) + len(s2) + 1
    phase_ids = {row["observation_id"] for row in table1 + s2}
    assert len({row["experiment"] for row in table1}) == 21
    fe_observation_id = "xu_2026_moessbauer_starting_glass_fe3_fet"
    numeric_observations = {
        key: row
        for key, row in observations.items()
        if key in phase_ids
        or key == fe_observation_id
        or key.startswith(f"{fe_observation_id}::")
    }
    assert len(phase_ids) == 1461
    assert len(numeric_observations) == len(phase_ids) + 1
    target_experiment_ids = {
        observations[row["observation_id"]].experiment_id
        for row in table1 + s2
    }
    assert len(target_experiment_ids) == 21
    assert all(
        experiment_id in migrator.result.experiments
        and migrator.result.experiments[experiment_id].sample.initial_composition
        is not None
        and migrator.result.experiments[
            experiment_id
        ].sample.initial_composition.state.is_value
        for experiment_id in target_experiment_ids
    )

    contexts = {
        record["observation_id"]: record
        for record in document["species"]["SiO2"]["context"]
    }
    starting_context = contexts["xu_2026_table1_starting_composition_context"]
    starting_values = starting_context["values"]
    assert starting_context["locator"]["table"] == "1"
    assert starting_context["locator"]["row"] == "MORB starting comp."
    assert starting_context["locator"]["page"] == 3
    assert starting_context["locator"]["pdf_page_index"] == 2
    assert starting_values["composition_role"] == "initial bulk"
    assert starting_values["composition_basis"] == "elemental wt.% as printed"
    assert starting_values["printed_standard_deviation_confidence"] == "one_sigma"
    assert starting_values["analysis_counts"] == {"EMPA": 10, "SIMS": 6}
    assert starting_values["element_wt_percent_means"]["Si"] == "23.54"
    assert starting_values["element_wt_percent_means"]["FeT"] == "8.36"
    assert starting_values["element_wt_percent_means"]["Cl"] == "0.00"
    assert starting_values["element_wt_percent_printed_1sd"]["FeT"] == "0.16"
    assert starting_values["element_wt_percent_means"] == {
        "Si": "23.54",
        "Ti": "0.59",
        "Al": "7.94",
        "FeT": "8.36",
        "Mg": "4.58",
        "Ca": "8.10",
        "Na": "2.03",
        "K": "0.17",
        "O": "44.46",
        "Pt": "0.00",
        "Cl": "0.00",
    }
    assert starting_values["element_wt_percent_printed_1sd"] == {
        "Si": "0.26",
        "Ti": "0.03",
        "Al": "0.10",
        "FeT": "0.16",
        "Mg": "0.03",
        "Ca": "0.06",
        "Na": "0.07",
        "K": "0.01",
        "O": "0.30",
    }
    assert starting_values["total_wt_percent"] == "99.76"
    assert starting_values["CO2_wt_percent"] == "0.00"
    assert starting_values["CO2_sigma"] == "0.00"
    assert starting_values["H2O_wt_percent"] == "0.04"
    assert starting_values["H2O_sigma"] == "0.00"
    assert starting_values["EO_wt_percent_as_published"] == "-0.4"
    first_run_si = next(
        row for row in table1 if row["observation_id"] == "xu_2026_table1_lvp_1_si"
    )
    assert first_run_si["component_amount_as_published"] == "23.40"
    product_quench_page = int(
        raw_observations[table1[0]["observation_id"]]["values"][
            "quench_statement"
        ]["locator"]["page"]
    )
    source_pdf = (
        regolith_corpus_root() / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"
    )
    if source_pdf.is_file():
        assert (
            _pdf_page_with_proof(
                source_pdf,
                "23.54 0.59 7.94 8.36 4.58 8.10 2.03 0.17 44.46 0.00 0.00 99.76",
            )
            == starting_context["locator"]["page"]
        )
        assert (
            _pdf_page_with_proof(
                source_pdf,
                "Standard deviations (1 sd) are based on multiple analyses and are given in Italics",
            )
            == 3
        )
        assert (
            _pdf_page_with_proof(
                source_pdf,
                "EO values in experimental samples are calculated assuming all iron is trivalent",
            )
            == 3
        )
        assert _pdf_page_with_proof(
            source_pdf, "no PtO2 is present in the samples after the experiments"
        ) == 7
        assert (
            _pdf_page_with_proof(
                source_pdf,
                "Some EO may have degassed during the temperature quenching process",
            )
            == product_quench_page
        )
        assert _pdf_page_with_proof(source_pdf, "Page 2 of 9") == product_quench_page
        first_run_proof = (
            "23.40 0.58 7.55 8.15 4.43 8.07 2.02 0.20 43.93 0.00 0.62 98.9"
        )
        first_run_page = _pdf_page_with_proof(source_pdf, first_run_proof)
        assert first_run_page == int(first_run_si["page"]) == 3
    table1_context = contexts["xu_2026_table1_run_product_context_lvp_1"]
    assert table1_context["experiment"] == "xu_2026_lvp_1"
    assert table1_context["units"] == "wt.% element"
    assert table1_context["locator"]["row"] == "LVP-1"
    expected_table1_elements = {
        ("FeT" if row["species_formula"] == "Fe" else row["species_formula"]): row[
            "component_amount_as_published"
        ]
        for row in table1
        if row["experiment"] == "xu_2026_lvp_1"
        and row["species_formula"] in {"Si", "Ti", "Al", "Fe", "Mg", "Ca", "Na", "K", "O", "Pt", "Cl"}
    }
    assert table1_context["values"]["element_wt_percent_as_published"] == (
        expected_table1_elements
    )
    assert table1_context["values"]["element_wt_percent_unit"] == "wt.% element"
    assert table1_context["values"]["total_wt_percent"] == "98.9"

    s2_context = contexts["xu_2026_s2_run_product_context_lvp_13"]
    assert s2_context["experiment"] == "xu_2026_lvp_13"
    s2_analysis = next(
        row
        for row in s2_context["values"]["analyses_as_published"]
        if row["source_row"] == 100
    )
    expected_s2_elements = {
        ("FeT" if row["species_formula"] == "Fe" else row["species_formula"]): row[
            "component_amount_as_published"
        ]
        for row in s2
        if row["source_cell"].endswith("100")
    }
    assert s2_analysis["element_wt_percent_as_published"] == expected_s2_elements
    assert s2_analysis["element_wt_percent_as_published"]["O"] == "48.77"
    assert s2_analysis["element_wt_percent_unit"] == "wt.% element"
    assert s2_analysis["total_wt_percent_as_published"] == "99.57"
    assert s2_analysis["source_cells"]["O"] == "K100"
    assert s2_analysis["source_cells"]["total"] == "O100"
    migrated_contexts = [
        context
        for rows in migrator.result.context_by_work.values()
        for context in rows
    ]
    migrated_s2_context = next(
        context
        for context in migrated_contexts
        if context["observation_id"] == "xu_2026_s2_run_product_context_lvp_13"
    )
    assert migrated_s2_context["experiment"] == "xu_2026_lvp_13"
    migrated_s2_analysis = next(
        row
        for row in migrated_s2_context["values"]["analyses_as_published"]
        if row["source_row"] == 100
    )
    assert migrated_s2_analysis["total_wt_percent_as_published"] == "99.57"
    assert migrated_s2_analysis["element_wt_percent_as_published"]["O"] == "48.77"

    for row in table1 + s2:
        observation = observations[row["observation_id"]]
        raw_values = raw_observations[row["observation_id"]]["values"]
        assert raw_values["oxygen_source_as_published"] == row["oxygen_source"]
        quench = raw_values["quench_statement"]
        assert quench["preservation"] == "not_stated"
        assert quench["quench_direction"] == "not_stated"
        assert quench["locator"]["page"] == product_quench_page
        assert quench["locator"]["pdf_page_index"] == 1
        assert "composition" not in raw_observations[row["observation_id"]].get(
            "point_conditions", {}
        )
        # D-305 removes the row's own glass analysis as an input; W3b keeps
        # the independently printed MORB starting bulk as the identity input.
        assert observation.identity.composition.is_value
        composition = observation.identity.composition.value
        assert composition.basis == "stoichiometric_oxide_projection"
        assert composition.amount_basis is AmountBasis.MOLE_FRACTION
        experiment = migrator.result.experiments[observation.experiment_id]
        initial_composition = experiment.sample.initial_composition
        assert initial_composition is not None and initial_composition.state.is_value
        assert composition == composition_with_provenance(
            initial_composition.state.value, PRINTED_STARTING_BULK
        )
        assert initial_composition.locator is not None
        assert initial_composition.locator.page == 3
        assert initial_composition.locator.pdf_page_index == 2
        assert initial_composition.locator.table == "1"
        assert initial_composition.inference is not None
        assert (
            initial_composition.inference.relation
            == "element_wt_percent_stoichiometric_oxide_projection"
        )
        assert initial_composition.inference.output_unit == "mole_fraction"
        assert set(initial_composition.inference.inputs) == {
            "xu-2026-excess-oxygen-magma::xu_2026_table1_starting_composition_context",
            "xu-2026-excess-oxygen-magma::xu_2026_moessbauer_starting_glass_fe3_fet",
        }
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(row["component_amount"])
        assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.identity.species.formula == row["species_formula"]
        assert observation.identity.species.phase.value is Phase.GLASS
        assert observation.identity.phase_role.value is PhaseRole.GLASS
        assert observation.identity.subtype.value == "wt_percent"
        assert observation.identity.temperature_K.value == celsius_to_kelvin(
            Decimal(row["T_C"])
        )
        assert observation.identity.total_pressure_Pa.value == Decimal(
            row["total_pressure_Pa"]
        )
        assert observation.identity.fO2_Pa.is_unknown

        temperature = observation.point_conditions["T_C"]
        assert temperature.state.value == Decimal(row["T_C"])
        assert temperature.locator is not None
        assert temperature.locator.page == 3
        assert temperature.locator.pdf_page_index == 2

        pressure = observation.point_conditions["total_pressure_Pa"]
        assert pressure.locator is not None
        assert pressure.locator.page == 3
        assert pressure.locator.pdf_page_index == 2
        pressure_value = pressure.state.value
        if hasattr(pressure_value, "point"):
            pressure_value = pressure_value.point
        assert Decimal(str(pressure_value)) == Decimal(row["total_pressure_Pa"])
        buffer_state = experiments[row["experiment"]]["fO2_control"]["buffer"]["state"]
        if row["experiment"] == "xu_2026_lvp_1":
            assert row["oxygen_source"] == ""
            assert buffer_state == {"tag": "unknown", "reason": "not_published"}
        elif row["oxygen_source"] == "PtO2":
            buffer_text = buffer_state["value"]
            assert "PtO2 powder" in buffer_text
            assert "log fO2 ≈ 0" in buffer_text
        else:
            buffer_text = buffer_state["value"]
            assert "No PtO2 powder" in buffer_text
        assert observation.identity.fO2_Pa.is_unknown
        assert not observation.notices

        if row["sigma"]:
            assert observation.uncertainty.kind is UncertaintyKind.PRINTED
            assert Decimal(str(observation.uncertainty.verbatim["sigma"])) == Decimal(
                row["sigma"]
            )
            assert observation.uncertainty.verbatim["units"] == "wt.%"
            if row["table"] == "1":
                assert (
                    observation.uncertainty.verbatim["confidence_level"]
                    == "one_sigma"
                )
        else:
            assert observation.uncertainty.kind is UncertaintyKind.NONE

        if row["table"] == "1":
            assert observation.locator.page == 3
            assert observation.locator.pdf_page_index == 2
            assert observation.locator.table == "1"
        else:
            assert observation.locator.page is None
            assert "cell " in observation.locator.record

    assert sum(bool(row["sigma"]) for row in table1) == 262

    fe = observations["xu_2026_moessbauer_starting_glass_fe3_fet"]
    assert fe.value.kind is ValueKind.POINT
    assert fe.value.point == Decimal(7) / Decimal(3)
    assert fe.identity.quantity.value is Quantity.FE3_FE2_RATIO
    assert fe.identity.species.formula == "Fe"
    assert fe.identity.species.phase.value is Phase.GLASS
    assert fe.uncertainty.kind is UncertaintyKind.NONE
    fe_source = next(
        row
        for row in document["species"]["Fe"]["observations"]
        if row["observation_id"] == "xu_2026_moessbauer_starting_glass_fe3_fet"
    )
    assert fe_source["values"]["Fe3_over_FeT"] == "0.70"
    assert fe_source["values"]["value_as_published"] == "70(3)%"
    assert "no uncertainty for the converted Fe3+/Fe2+ ratio is reported" in (
        fe_source["values"]["note"]
    )
    fe_table_context = next(
        context
        for context in document["species"]["Fe"]["context"]
        if context["observation_id"] == "xu_2026_table_s1_hyperfine_context"
    )
    assert fe_table_context["locator"]["page"] == 22
    assert fe_table_context["values"]["Fe3_plus"]["relative_area_percent"] == "70(3)"
    assert fe_source["locator"]["page"] == 22
    assert fe_source["locator"]["pdf_page_index"] == 21
    starting_glass_quench = fe_source["values"]["quench_statement"]
    assert starting_glass_quench["preservation"] == "not_stated"
    assert starting_glass_quench["quench_direction"] == "not_stated"
    assert starting_glass_quench["locator"]["page"] == 3
    assert fe.derivation is not None
    assert fe.derivation.raw_inputs is not None
    assert fe.derivation.raw_inputs.state.value is RawInputStatus.NOT_PRINTED
    assert fe.derivation.raw_inputs.locator is not None
    assert fe.derivation.raw_inputs.locator.figure == "S7"
    assert fe.derivation.raw_inputs.locator.pdf_page_index == 19
    assert fe.derivation.raw_inputs.locator.page == 20
    assert not fe.derivation.inputs
    assert fe_source["derivation"]["locator"]["pdf_page_index"] == 4
    assert fe_source["derivation"]["locator"]["page"] == 5
    assert (
        fe_source["derivation"]["locator"]["section"]
        == "Mössbauer spectroscopic analysis"
    )
    assert not fe.derived_from
    assert len(fe.notices) == 1
    method_notice = fe.notices[0]
    assert method_notice.kind is NoticeKind.METHOD_UNSTATED
    assert method_notice.affected_quantities == (Quantity.FE3_FE2_RATIO,)
    assert method_notice.reason == (
        "method_unstated: default=unspecified; "
        "original_reason=source does not state method"
    )
    assert method_notice.policy is not None
    assert method_notice.policy.id == "DP-106"
    assert method_notice.policy.value == "unspecified"

    start_elements = {
        formula: Decimal(value)
        for formula, value in starting_values["element_wt_percent_means"].items()
    }
    fe_total = start_elements.pop("FeT")
    start_elements["H2O"] = Decimal(starting_values["H2O_wt_percent"])
    base_projection = _element_wt_percent_stoichiometric_oxide_projection(
        start_elements,
        valence_by_element={
            "Si": 4,
            "Ti": 4,
            "Al": 3,
            "Mg": 2,
            "Ca": 2,
            "Na": 1,
            "K": 1,
            "Pt": 4,
        },
        retained_components=("Cl", "CO2", "H2O"),
    )
    fe3_fraction = Decimal(fe_source["values"]["Fe3_over_FeT"])
    fe2_projection = _element_wt_percent_stoichiometric_oxide_projection(
        {"Fe": fe_total * (Decimal("1") - fe3_fraction)},
        valence_by_element={"Fe": 2},
    )
    fe3_projection = _element_wt_percent_stoichiometric_oxide_projection(
        {"Fe": fe_total * fe3_fraction},
        valence_by_element={"Fe": 3},
    )
    oxide_weights = dict(base_projection.projected_component_mass_g_per_100g)
    oxide_weights.update(dict(fe2_projection.projected_component_mass_g_per_100g))
    oxide_weights.update(dict(fe3_projection.projected_component_mass_g_per_100g))
    component_moles = {
        formula: mass / _formula_molar_mass(formula)
        for formula, mass in oxide_weights.items()
    }
    mole_total = sum(component_moles.values(), Decimal(0))
    expected_starting_components = {
        formula: moles / mole_total for formula, moles in component_moles.items()
    }
    first_experiment_id = observations[first_run_si["observation_id"]].experiment_id
    first_experiment = migrator.result.experiments[first_experiment_id]
    initial = first_experiment.sample.initial_composition
    assert initial is not None and initial.state.is_value
    actual_starting_components = dict(initial.state.value.components)
    assert actual_starting_components.keys() == expected_starting_components.keys()
    assert all(
        abs(actual_starting_components[formula] - expected_starting_components[formula])
        <= Decimal("1e-24")
        for formula in expected_starting_components
    )
    assert "all-Fe3" in starting_values["iron_valence_note"]

    identity_axis_pins = (
        "xu_2026_table1_lvp_1_si",
        "xu_2026_table1_lvp_2_si",
        "xu_2026_s2_row_100_o",
    )
    for observation_id in identity_axis_pins:
        observation = observations[observation_id]
        assert observation.identity.composition.is_value
        assert observation.identity.temperature_K.is_value
        assert observation.identity.total_pressure_Pa.is_value
        assert observation.identity.fO2_Pa.is_unknown

    all_observations = list(numeric_observations.values())
    assert all(row.value.kind is ValueKind.POINT for row in all_observations)
    assert all(row.identity.quantity.is_value for row in all_observations)
    assert all(row.identity.species.phase.is_value for row in all_observations)
    assert all(
        "fO2_log" not in (row.point_conditions or {}) for row in all_observations
    )

    figures = [
        record
        for species in document["species"].values()
        for record in species.get("context", [])
        if record.get("type") == "figure_record"
    ]
    assert len(figures) == 11
    assert all(record["values"]["points"] == [] for record in figures)
    assert all(
        record["values"]["digitisation"] == "deferred to a figure pass"
        for record in figures
    )
    contexts = [
        record
        for record in document["species"]["SiO2"]["context"]
        if record.get("type") == "measured_table_context"
    ]
    s3 = next(record for record in contexts if record["locator"].get("table") == "S3")
    s4 = next(record for record in contexts if record["locator"].get("table") == "S4")
    assert len(s3["values"]["displayed_cells"]) == 763
    assert len(s4["values"]["displayed_cells"]) == 785
    assert any(cell["cell"] == "AL3" for cell in s3["values"]["displayed_cells"])
    model = next(
        record
        for record in document["species"]["SiO2"]["context"]
        if record.get("type") == "model_results"
    )
    assert len(model["values"]["printed_rows"]) == 25


def test_xu_supplement_pdf_pages_match_checked_in_locators(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    fe_source = next(
        row
        for row in document["species"]["Fe"]["observations"]
        if row["observation_id"] == "xu_2026_moessbauer_starting_glass_fe3_fet"
    )
    fe_values = fe_source["values"]
    fe_derivation = fe_source["derivation"]
    starting_context = next(
        row
        for row in document["species"]["SiO2"]["context"]
        if row["observation_id"] == "xu_2026_table1_starting_composition_context"
    )
    supplement_pdf = _supplement_pdf(tmp_path)
    assert (
        _pdf_page_with_proof(
            supplement_pdf,
            "Starting material was a synthetic mid-ocean ridge basalt (MORB)",
        )
        == starting_context["locator"]["page"]
    )
    assert (
        _pdf_page_with_proof(
            supplement_pdf,
            "Twenty-one high pressure and temperature experiments (Table 1) were conducted",
        )
        == starting_context["locator"]["page"]
    )
    assert (
        _pdf_page_with_proof(supplement_pdf, "logfO2 ≈ 0")
        == starting_context["locator"]["page"]
    )
    assert (
        _pdf_page_with_proof(
            supplement_pdf,
            "The melts were quenched rapidly by immersing the bottom of Pt crucible in water",
        )
        == fe_values["quench_statement"]["locator"]["page"]
    )
    assert (
        _pdf_page_with_proof(
            supplement_pdf,
            "Table S1. Hyperfine parameters derived from room temperature spectrum of synthetic MORB glass starting material",
        )
        == fe_source["locator"]["page"]
    )
    assert (
        _pdf_page_with_proof(supplement_pdf, "area (%) 70(3)")
        == fe_source["locator"]["page"]
    )
    assert (
        _pdf_page_with_proof(
            supplement_pdf,
            "Fe3+/ΣFe was determined from relative areas to be 0.70",
        )
        == fe_derivation["locator"]["page"]
    )
    assert (
        _pdf_page_with_proof(
            supplement_pdf,
            "Fig. S7. Room temperature Mössbauer spectrum of MORB glass starting material",
        )
        == fe_derivation["raw_inputs"]["locator"]["page"]
    )


def test_missing_xu_source_pdf_skips_pdfinfo_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_pdf = (
        regolith_corpus_root() / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"
    )
    original_is_file = Path.is_file
    original_pdf_proof = _pdf_page_with_proof

    def missing_source(path: Path) -> bool:
        if path == source_pdf:
            return False
        return original_is_file(path)

    def reject_missing_source_proof(pdf: Path, proof: str) -> int:
        if pdf == source_pdf:
            raise AssertionError("pdfinfo proof ran for a missing source PDF")
        return original_pdf_proof(pdf, proof)

    monkeypatch.setattr(Path, "is_file", missing_source)
    monkeypatch.setattr(
        sys.modules[__name__], "_pdf_page_with_proof", reject_missing_source_proof
    )

    def missing_supplement(_tmp_path: Path) -> Path:
        pytest.skip("Xu supplementary PDF archive is absent")

    monkeypatch.setattr(sys.modules[__name__], "_supplement_pdf", missing_supplement)
    try:
        test_typed_glass_components_migrate_with_exact_samples_conditions_and_points(
            tmp_path
        )
    except pytest.skip.Exception as exc:
        pytest.fail(f"checked-in regression skipped: {exc}")


def test_mutating_starting_bulk_changes_migrated_phase_identity(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    base_migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    base_migrator.finalize()
    observation_key = f"{SOURCE_ID}::xu_2026_table1_lvp_1_si"
    original = base_migrator.result.observations[observation_key]
    original_components = dict(original.identity.composition.value.components)

    changed_document = deepcopy(document)
    start = changed_document["experiments"][0]["sample"]["initial_composition"]
    components = start["state"]["value"]["components"]
    components[0][1] = str(Decimal(components[0][1]) - Decimal("0.01"))
    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(
        yaml.safe_dump(changed_document, sort_keys=False), encoding="utf-8"
    )
    mutated_migrator = migrate_one_extract(
        ROOT, mutated_extract, index={}, aliases={}
    )
    mutated_migrator.finalize()
    assert not mutated_migrator.result.validation.hard_issues
    mutated = mutated_migrator.result.observations[observation_key]
    assert dict(mutated.identity.composition.value.components) != original_components


def test_lvp1_table1_redox_cell_is_empty_in_excess_oxygen_context() -> None:
    # Main PDF page 3, Table 1: the LVP-1 redox cell is blank. The PtO2-powder
    # fact lives only in the supplement (page 5), never on this printed row.
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))

    def walk(node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    lvp1_rows = [
        node
        for node in walk(document)
        if node.get("values", {}).get("sample_id_as_published") == "LVP-1"
        and "redox_condition_as_published" in node.get("values", {})
        and (node.get("locator") or {}).get("table") == "1"
    ]
    assert len(lvp1_rows) == 1
    assert lvp1_rows[0]["values"]["redox_condition_as_published"] == ""
