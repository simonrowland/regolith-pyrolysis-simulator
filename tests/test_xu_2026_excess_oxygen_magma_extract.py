from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re

import pytest
import yaml

import simulator.battery.migrate as migrate_module
from simulator.battery.enums import (
    AmountBasis,
    Phase,
    PhaseRole,
    Quantity,
    RawInputStatus,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.composition import (
    _element_wt_percent_stoichiometric_oxide_projection,
)
from simulator.battery.identity import celsius_to_kelvin
from tests.battery.factories import migrate_one_extract
from tests.battery.literature_extract_grids import (
    assert_observation_grid_matches,
    decimal_text,
    read_observation_grid_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table1 = _fixture(TABLE1_GRID)
    s2 = _fixture(S2_GRID)
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    experiments = {row["experiment_id"]: row for row in document["experiments"]}

    def source_row_key(observation: dict) -> tuple[str, str, str, str, str]:
        locator = observation.get("locator") or {}
        row = locator.get("row")
        if row is None:
            match = re.search(
                r"\bcell\s+[A-Z]+(\d+)\b",
                str(locator.get("record") or ""),
                re.IGNORECASE,
            )
            row = match.group(1) if match is not None else ""
        values = observation.get("values") or {}
        return (
            str(observation.get("experiment") or ""),
            str(locator.get("table") or ""),
            str(row),
            str(values.get("sample_id_as_published") or ""),
            str(values.get("analysis_id_as_published") or ""),
        )

    resolution_calls: defaultdict[tuple[str, str, str, str, str], int] = (
        defaultdict(int)
    )
    original_resolver = migrate_module._resolve_source_declared_composition

    def count_source_row_resolutions(source_id, observation, **kwargs):
        if (
            source_id == "xu-2026-excess-oxygen-magma"
            and kwargs.get("quantity") is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        ):
            resolution_calls[source_row_key(observation)] += 1
        return original_resolver(source_id, observation, **kwargs)

    monkeypatch.setattr(
        migrate_module,
        "_resolve_source_declared_composition",
        count_source_row_resolutions,
    )
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
    expected_projection_rows = {
        source_row_key(observation)
        for observation in raw_observations.values()
        if observation.get("values", {}).get("quantity")
        == Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value
    }
    assert set(resolution_calls) == expected_projection_rows
    assert set(resolution_calls.values()) == {1}
    assert len(observations) == len(table1) + len(s2) + 1

    contexts = {
        record["observation_id"]: record
        for record in document["species"]["SiO2"]["context"]
    }
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
        assert "composition" not in (
            raw_observations[row["observation_id"]].get("point_conditions") or {}
        )
        assert observation.identity.composition.is_unknown
        assert (
            observation.identity.composition.reason
            == "input_composition_is_scored_phase_analysis"
        )
        located_composition = observation.point_conditions["composition"]
        assert located_composition.state.is_value
        composition = located_composition.state.value
        assert composition.basis == "stoichiometric_oxide_projection"
        assert composition.amount_basis is AmountBasis.MOLE_FRACTION
        assert located_composition.inference is not None
        assert (
            located_composition.inference.relation
            == "source_composition_projection"
        )
        assert located_composition.inference.output_unit == "mole_fraction"
        assert located_composition.inference.method is not None
        assert located_composition.inference.method.state.value == "elemental wt %"
        row_projection_parameters = dict(located_composition.inference.parameters)
        assert (
            row_projection_parameters[
                "oxygen_balance_residual_wt_percent"
            ].state.is_value
        )
        assert (
            "measured O is used only for the oxygen balance residual, not projected into the composition"
            in located_composition.inference.inputs
        )
        assert (
            "weight-percent renormalized: false"
            in located_composition.inference.inputs
        )
        assert any(
            item.startswith("oxygen balance residual = measured O wt% -")
            for item in located_composition.inference.inputs
        )
        group_prefix = row["observation_id"].rsplit("_", 1)[0]
        expected_inputs = {
            observation_id
            for observation_id in raw_observations
            if observation_id.rsplit("_", 1)[0] == group_prefix
        }
        assert expected_inputs.issubset(set(located_composition.inference.inputs))
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
        else:
            assert observation.uncertainty.kind is UncertaintyKind.NONE

        if row["table"] == "1":
            assert observation.locator.page == 3
            assert observation.locator.pdf_page_index == 2
            assert observation.locator.table == "1"
        else:
            assert observation.locator.page is None
            assert "cell " in observation.locator.record

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
    assert fe_source["values"]["value_as_published"] == "70(3)%"
    assert "±0.03 applies to the ferric fraction" in fe_source["values"]["note"]
    assert fe.derivation is not None
    assert fe.derivation.raw_inputs is not None
    assert fe.derivation.raw_inputs.state.value is RawInputStatus.NOT_PRINTED
    assert fe.derivation.raw_inputs.locator is not None
    assert fe.derivation.raw_inputs.locator.pdf_page_index == 2
    assert fe.derivation.raw_inputs.locator.page == 3
    assert fe.derivation.raw_inputs.locator.section == "Mössbauer spectroscopic analysis"
    assert not fe.derived_from
    assert not fe.notices

    table1_lvp1 = [
        raw_observations[row["observation_id"]]
        for row in table1
        if row["experiment"] == "xu_2026_lvp_1"
    ]
    elemental_wt_percent = {
        row["values"]["formula"]: row["values"]["component_amount_as_published"]
        for row in table1_lvp1
    }
    lvp1_composition = observations[table1_lvp1[0]["observation_id"].split("::", 1)[-1]]
    projection_derivation = lvp1_composition.point_conditions["composition"].inference
    projection_parameters = dict(projection_derivation.parameters)
    published_lvp1_total = projection_parameters[
        "original_analytical_total_wt_pct"
    ]
    assert published_lvp1_total.state.value == Decimal("98.9")
    assert published_lvp1_total.locator is not None
    assert published_lvp1_total.locator.page == 3
    assert published_lvp1_total.locator.table == "1"
    assert "row=LVP-1" in (published_lvp1_total.locator.note or "")
    assert projection_derivation.relation == "source_composition_projection"
    assert projection_derivation.method is not None
    assert projection_derivation.method.state.value == "elemental wt %"
    expected_valences = {
        "Si": 4,
        "Ti": 4,
        "Al": 3,
        "Fe": 3,
        "Mg": 2,
        "Ca": 2,
        "Na": 1,
        "K": 1,
        "Pt": 4,
    }
    assert {
        name.removeprefix("assumed_valence_"): int(parameter.state.value)
        for name, parameter in projection_derivation.parameters
        if name.startswith("assumed_valence_")
    } == expected_valences
    assert any(
        "all experimental Fe is trivalent" in value
        for value in projection_derivation.inputs
    )
    assert projection_parameters[
        "original_analytical_total_wt_pct"
    ].state.value == Decimal("98.9")
    lvp12 = observations["xu_2026_table1_lvp_12_si"]
    lvp12_projection = lvp12.point_conditions["composition"]
    assert lvp12.point_conditions["composition"].state.is_value
    assert lvp12_projection.inference is not None
    lvp12_total = dict(lvp12_projection.inference.parameters)[
        "original_analytical_total_wt_pct"
    ]
    assert lvp12_total.state.is_unknown
    assert lvp12_total.state.reason == (
        "published analytical total is not printed for this source row"
    )
    assert dict(
        lvp1_composition.point_conditions["composition"].state.value.components
    ) == {
        "SiO2": Decimal("0.5531183156263053760382113461"),
        "TiO2": Decimal("0.008043932072763917401742288490"),
        "Al2O3": Decimal("0.09288099447174100359886515809"),
        "MgO": Decimal("0.1209998163861960873534286038"),
        "CaO": Decimal("0.1336731259807518788600345212"),
        "Na2O": Decimal("0.02916509339105920910266158605"),
        "K2O": Decimal("0.001697925910911332657241372798"),
        "Fe2O3": Decimal("0.04844176162924739410026465963"),
        "Cl": Decimal("0.01161053041421789160288423891"),
        "H2O": Decimal("0.0003685041168059092846662246242"),
    }
    assert (
        projection_parameters[
            "oxygen_balance_residual_wt_percent"
        ].state.value
        == Decimal("-0.22587040322009958874924728")
    )
    valences = {
        name.removeprefix("assumed_valence_"): int(parameter.state.value)
        for name, parameter in projection_derivation.parameters
        if name.startswith("assumed_valence_")
    }
    retained_components = tuple(
        name for name in ("Cl", "CO2", "H2O") if name in elemental_wt_percent
    )
    projected = _element_wt_percent_stoichiometric_oxide_projection(
        elemental_wt_percent,
        valence_by_element=valences,
        retained_components=retained_components,
    )
    assert dict(
        lvp1_composition.point_conditions["composition"].state.value.components
    ) == dict(projected.components)
    assert dict(projected.components)["Cl"] > 0
    assert projection_parameters[
        "oxygen_balance_residual_wt_percent"
    ].state.value == projected.oxygen_closure_residual_wt_percent
    assert projection_parameters[
        "oxygen_balance_residual_wt_percent"
    ].state.value < 0
    s2_row_100 = observations["xu_2026_s2_row_100_si"]
    s2_row_100_residual = dict(
        s2_row_100.point_conditions["composition"].inference.parameters
    )["oxygen_balance_residual_wt_percent"]
    assert s2_row_100_residual.locator == observations[
        "xu_2026_s2_row_100_o"
    ].locator
    s2_row_100_parameters = dict(
        observations["xu_2026_s2_row_100_si"]
        .point_conditions["composition"]
        .inference.parameters
    )
    published_s2_total = s2_row_100_parameters[
        "original_analytical_total_wt_pct"
    ]
    assert published_s2_total.state.value == Decimal("99.57")
    assert published_s2_total.locator is not None
    assert published_s2_total.locator.table == "S2"
    assert (published_s2_total.locator.record or "").endswith("row 100")
    assert s2_row_100_parameters[
        "original_analytical_total_wt_pct"
    ].state.value == Decimal("99.57")
    assert dict(
        s2_row_100.point_conditions["composition"].state.value.components
    ) == {
        "SiO2": Decimal("0.5564739584423422805482899035"),
        "TiO2": Decimal("0.01280391798259080036653744582"),
        "Al2O3": Decimal("0.09555106258002106035960942278"),
        "MgO": Decimal("0.1140220830846365115052100276"),
        "CaO": Decimal("0.1504297743247867273588969897"),
        "Na2O": Decimal("0.02665903840130551502189968559"),
        "K2O": Decimal("0.003833680441525691105263105007"),
        "PtO2": Decimal("0.00003414834134285263522742532755"),
        "Fe2O3": Decimal("0.009185392008859125951910507300"),
        "Cl": Decimal("0.03100694439258943514715548741"),
    }
    assert s2_row_100_parameters[
        "oxygen_balance_residual_wt_percent"
    ].state.value == Decimal("6.79527703078753007001670922")
    oxygen_changed = dict(elemental_wt_percent)
    oxygen_changed["O"] = str(Decimal(oxygen_changed["O"]) + 1)
    oxygen_check_only = _element_wt_percent_stoichiometric_oxide_projection(
        oxygen_changed,
        valence_by_element=valences,
        retained_components=retained_components,
    )
    assert oxygen_check_only.components == projected.components
    assert (
        oxygen_check_only.oxygen_closure_residual_wt_percent
        == projected.oxygen_closure_residual_wt_percent + 1
    )
    cation_changed = dict(elemental_wt_percent)
    cation_changed["Si"] = str(Decimal(cation_changed["Si"]) + 1)
    assert _element_wt_percent_stoichiometric_oxide_projection(
        cation_changed,
        valence_by_element=valences,
        retained_components=retained_components,
    ).components != projected.components

    all_observations = list(observations.values())
    assert all(row.value.kind is ValueKind.POINT for row in all_observations)
    assert all(
        "fO2_log" not in (row.point_conditions or {}) for row in all_observations
    )

    monkeypatch.setattr(
        migrate_module,
        "input_composition_echo_detail",
        lambda _identity: None,
    )
    unguarded_migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    unguarded_migrator.finalize()
    unguarded_lvp1 = next(
        observation
        for observation in unguarded_migrator.result.observations.values()
        if observation.observation_id.endswith("::xu_2026_table1_lvp_1_si")
    )
    unguarded_identity_composition = unguarded_lvp1.identity.composition
    with pytest.raises(AssertionError):
        assert (
            unguarded_identity_composition is not None
            and unguarded_identity_composition.is_unknown
            and unguarded_identity_composition.reason
            == "input_composition_is_scored_phase_analysis"
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


def test_elemental_projection_oxide_mass_factors_match_hand_computed_values() -> None:
    cases = (
        (
            "Si",
            4,
            "SiO2",
            Decimal("60.0843") / Decimal("28.0855"),
        ),
        (
            "Al",
            3,
            "Al2O3",
            (2 * Decimal("26.9815385") + 3 * Decimal("15.9994"))
            / (2 * Decimal("26.9815385")),
        ),
        (
            "Fe",
            2,
            "FeO",
            (Decimal("55.845") + Decimal("15.9994")) / Decimal("55.845"),
        ),
        (
            "Mg",
            2,
            "MgO",
            (Decimal("24.305") + Decimal("15.9994")) / Decimal("24.305"),
        ),
        (
            "Na",
            1,
            "Na2O",
            (2 * Decimal("22.98976928") + Decimal("15.9994"))
            / (2 * Decimal("22.98976928")),
        ),
    )
    for element, valence, oxide, expected_factor in cases:
        projected = _element_wt_percent_stoichiometric_oxide_projection(
            {element: Decimal("1")},
            valence_by_element={element: valence},
        )
        actual_factor = dict(projected.projected_component_mass_g_per_100g)[
            oxide
        ]
        # The hand values use the precision printed in the task's example;
        # the shared CIAAW table is rounded slightly differently.
        assert abs(actual_factor - expected_factor) < Decimal("5e-5")
