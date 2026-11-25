"""Migration pins for Mendybaev et al. (2013), Table 1 residue analyses."""

from decimal import Decimal, localcontext
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, MethodToken, PerBasis, Quantity, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/mendybaev-2013-fun-cai-evaporation.yaml"
MASS_LOSS_CONTEXT = (
    "mendybaev-2013-fun-cai-evaporation::context::"
    "mendybaev_2013_table1_all_printed_cells"
)
EXPECTED_MASS_LOSS = {
    "FUN1-3": ("0.0161", "0.0148", "0.0807453416149068322981366459627329192546"),
    "FUN1-12": ("0.0214", "0.0176", "0.1775700934579439252336448598130841121495"),
    "FUN1-15": ("0.0258", "0.0208", "0.1937984496124031007751937984496124031007"),
    "FUN1-13": ("0.0286", "0.0210", "0.2657342657342657342657342657342657342657"),
    "FUN1-1": ("0.0103", "0.0049", "0.5242718446601941747572815533980582524271"),
    "FUN1-10": ("0.0214", "0.0078", "0.6355140186915887850467289719626168224299"),
    "FUN1-5": ("0.0180", "0.0057", "0.6833333333333333333333333333333333333333"),
    "FUN1-11": ("0.0300", "0.0045", "0.8500000000000000000000000000000000000000"),
    "FUN1-16": ("0.0238", "0.0100", "0.5798319327731092436974789915966386554621"),
    "FUN1-4": ("0.0278", "0.0037", "0.8669064748201438848920863309352517985611"),
    "FUN1-14": ("0.0290", "0.0094", "0.6758620689655172413793103448275862068965"),
    "FUN1-9": ("0.0377", "0.0126", "0.6657824933687002652519893899204244031830"),
    "FUN1-17": ("0.0278", "0.0059", "0.7877697841726618705035971223021582733812"),
    "FUN1-18": ("0.0312", "0.0060", "0.8076923076923076923076923076923076923076"),
    "FUN2-4": ("0.0334", "0.0319", "0.0449101796407185628742514970059880239520"),
    "FUN2-5": ("0.0315", "0.0243", "0.2285714285714285714285714285714285714285"),
    "FUN2-1": ("0.0619", "0.0431", "0.3037156704361873990306946688206785137318"),
    "FUN2-2": ("0.0455", "0.0298", "0.3450549450549450549450549450549450549450"),
    "FUN2-3": ("0.0372", "0.0206", "0.4462365591397849462365591397849462365591"),
    "FUN2-8": ("0.0309", "0.0137", "0.5566343042071197411003236245954692556634"),
    "FUN2-7": ("0.0418", "0.0194", "0.5358851674641148325358851674641148325358"),
    "FUN2-6": ("0.0419", "0.0160", "0.6181384248210023866348448687350835322195"),
    "FUN2-9": ("0.0559", "0.0215", "0.6153846153846153846153846153846153846153"),
    "FUN2-10": ("0.0514", "0.0176", "0.6575875486381322957198443579766536964980"),
}
EXPECTED_RUN_MINUTES = {
    "FUN1-3": "0",
    "FUN1-12": "0",
    "FUN1-15": "5",
    "FUN1-13": "10",
    "FUN1-1": "15",
    "FUN1-10": "15",
    "FUN1-5": "20",
    "FUN1-11": "25",
    "FUN1-16": "25",
    "FUN1-4": "30",
    "FUN1-14": "30",
    "FUN1-9": "35",
    "FUN1-17": "35",
    "FUN1-18": "40",
    "FUN2-4": "0",
    "FUN2-5": "10",
    "FUN2-1": "20",
    "FUN2-2": "20",
    "FUN2-3": "25",
    "FUN2-8": "30",
    "FUN2-7": "35",
    "FUN2-6": "40",
    "FUN2-9": "50",
    "FUN2-10": "55",
}
EXPECTED_STARTING_WT_PCT = {
    "FUN1": {"MgO": "53.14", "Al2O3": "2.97", "SiO2": "41.27", "CaO": "2.36"},
    "FUN2": {"MgO": "33.03", "Al2O3": "15.18", "SiO2": "38.39", "CaO": "13.40"},
}


def _fraction_from_printed_weights(initial: str, final: str) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        return (Decimal(initial) - Decimal(final)) / Decimal(initial)


def test_vacuum_evaporation_experiments_migrate_to_langmuir_method() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    experiment_methods = {
        experiment.experiment_id.rsplit("::", 1)[-1]: experiment.method
        for experiment in migrator.result.experiments.values()
    }
    assert set(experiment_methods) == {
        "mendybaev-fun1-vacuum-evaporation",
        "mendybaev-fun2-vacuum-evaporation",
    }
    assert all(
        method.is_value
        and method.value is MethodToken.LANGMUIR_FREE_EVAPORATION
        for method in experiment_methods.values()
    )


def test_table_1_fun1_3_residue_composition_and_conditions_migrate() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    row = [
        observation
        for observation in migrator.result.observations.values()
        if observation.locator.note == "row=FUN1-3"
        and observation.identity.quantity.value
        is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert len(row) == 4
    assert all(o.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION for o in row)
    assert {o.identity.species.formula: o.value.point for o in row} == {
        "MgO": Decimal("50.01"),
        "Al2O3": Decimal("4.04"),
        "SiO2": Decimal("42.99"),
        "CaO": Decimal("2.96"),
    }
    assert all(o.identity.temperature_K.value == Decimal("2173.15") for o in row)
    assert all(
        o.point_conditions["total_pressure_Pa"].state.value.bound_value
        == Decimal("0.0001")
        for o in row
    )
    assert all(o.point_conditions["time_min"].state.value == Decimal("0") for o in row)
    assert not migrator.result.validation.hard_issues


def test_bounded_identity_conditions_stay_on_mendybaev_points(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    row = next(
        observation
        for observation in document["species"]["SiO2"]["observations"]
        if observation["observation_id"]
        == "mendybaev_2013_table1_fun1_residue_composition_series"
    )
    bounded_mass = {
        "state": {
            "tag": "value",
            "value": {
                "kind": "bound",
                "bound_operator": "<=",
                "bound_value": "0.0001",
            },
        },
        "locator": {"table": "1", "row": "FUN1-3"},
    }
    row["point_conditions"]["sample_mass_kg"] = bounded_mass
    for point in row["values"]["series"]:
        if point["sample"] == "FUN1-3":
            point["point_conditions"]["sample_mass_kg"] = {
                "state": {
                    "tag": "value",
                    "value": {
                        "kind": "bound",
                        "bound_operator": "<=",
                        "bound_value": "0.0001",
                    },
                },
                "locator": {"table": "1", "row": "FUN1-3"},
            }
    extract = tmp_path / EXTRACT.name
    extract.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(extract)
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    row_points = [
        observation
        for observation in observations
        if observation.locator.note == "row=FUN1-3"
        and observation.identity.quantity.value
        is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert len(observations) == 120
    assert len(row_points) == 4
    for observation in row_points:
        pressure = observation.point_conditions["total_pressure_Pa"]
        mass = observation.point_conditions["sample_mass_kg"]
        assert pressure.state.is_value
        assert pressure.state.value.kind is ValueKind.BOUND
        assert pressure.state.value.bound_operator == "<"
        assert observation.identity.total_pressure_Pa.is_unknown
        assert mass.state.is_value
        assert mass.state.value.kind is ValueKind.BOUND
        assert mass.state.value.bound_operator == "<="
        assert observation.identity.sample_mass_kg.is_unknown


def test_table_1_fun2_residues_migrate_to_the_fun2_experiment() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    fun1 = [
        o for o in observations
        if o.locator.note
        and o.locator.note.startswith("row=FUN1-")
        and o.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    fun2 = [
        o for o in observations
        if o.locator.note
        and o.locator.note.startswith("row=FUN2-")
        and o.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert len(fun1) == 56
    assert len(fun2) == 40
    assert all(o.experiment_id.endswith("mendybaev-fun1-vacuum-evaporation") for o in fun1)
    assert all(o.experiment_id.endswith("mendybaev-fun2-vacuum-evaporation") for o in fun2)
    fun2_10_mgo = [
        o for o in fun2
        if o.locator.note == "row=FUN2-10"
        and o.identity.species.formula == "MgO"
    ]
    assert len(fun2_10_mgo) == 1
    assert fun2_10_mgo[0].value.point == Decimal("1.55")
    assert sum(
        o.experiment_id == fun2_10_mgo[0].experiment_id
        and o.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        for o in observations
    ) == 40
    assert not migrator.result.validation.hard_issues


def test_table_1_residue_uncertainties_migrate_from_each_printed_sigma(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    series = next(
        observation["values"]["series"]
        for observation in document["species"]["SiO2"]["observations"]
        if observation["observation_id"]
        == "mendybaev_2013_table1_fun1_residue_composition_series"
    )
    fun1_mgo = next(row for row in series if row["run_id"] == "FUN1-3")
    fun2_series = next(
        observation["values"]["series"]
        for species in document["species"].values()
        for observation in species["observations"]
        if observation.get("observation_id")
        == "mendybaev_2013_table1_fun2_residue_composition_series"
    )
    fun2_cao = next(
        row
        for row in fun2_series
        if row["run_id"] == "FUN2-10" and "CaO_wt_pct" in row
    )

    def migrated(path: Path):
        migrator = Migrator(root=ROOT, index={}, aliases={})
        migrator._migrate_extract(path)
        migrator.finalize()
        assert len(migrator.result.observations) == 120
        assert not migrator.result.validation.hard_issues
        for observation in migrator.result.observations.values():
            if observation.identity.quantity.value is not Quantity.RESIDUE_COMPONENT_COMPOSITION:
                continue
            assert observation.identity.temperature_K.value == Decimal("2173.15")
            elapsed = observation.point_conditions["time_min"]
            assert elapsed.state.is_value
            assert elapsed.locator is not None
        return migrator

    def residue_uncertainty(
        migrator: Migrator, row: str, formula: str
    ) -> tuple[str, str | None]:
        observation = next(
            item
            for item in migrator.result.observations.values()
            if item.locator.note == f"row={row}"
            and item.identity.species.formula == formula
        )
        assert observation.uncertainty.kind.value == "printed"
        return (
            observation.uncertainty.verbatim["sigma"],
            observation.uncertainty.verbatim.get("confidence_level"),
        )

    original = migrated(EXTRACT)
    assert residue_uncertainty(original, "FUN1-3", "MgO") == (
        "0.31",
        "not_stated",
    )
    assert residue_uncertainty(original, "FUN2-10", "CaO") == (
        "0.10",
        "not_stated",
    )

    fun1_mgo["sigma"] = "0.99"
    fun2_cao["sigma"] = "0.77"
    fun1_observation = next(
        observation
        for observation in document["species"]["SiO2"]["observations"]
        if observation["observation_id"]
        == "mendybaev_2013_table1_fun1_residue_composition_series"
    )
    fun1_observation["uncertainty"]["confidence_level"] = "one_sigma"
    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    mutated = migrated(mutated_extract)
    assert residue_uncertainty(mutated, "FUN1-3", "MgO") == (
        "0.99",
        "one_sigma",
    )
    assert residue_uncertainty(mutated, "FUN2-10", "CaO") == (
        "0.77",
        "not_stated",
    )


def test_all_deferred_figure_records_have_separate_captions() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    figures = [
        row for row in extract["species"]["SiO2"]["context"]
        if row.get("type") == "figure_only"
    ]
    assert len(figures) == 12
    figure7 = next(row for row in figures if row["locator"]["figure"] == "7")
    figure8 = next(row for row in figures if row["locator"]["figure"] == "8")
    assert "Fig. 8." not in figure7["values"]["caption_as_printed"]
    assert "as a function of the amount of 24Mg remaining" in figure7["values"]["caption_as_printed"]
    assert "Fig. 7." not in figure8["values"]["caption_as_printed"]
    assert "Same as Fig. 7 but for 29Si/28Si" in figure8["values"]["caption_as_printed"]
    for number in (1, 3, 10, 11, 12):
        figure = next(row for row in figures if row["locator"]["figure"] == str(number))
        assert figure["values"]["caption_as_printed"].startswith(f"Fig. {number}.")
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)


def test_unique_l4b_context_values_are_retained_with_source_locators() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    contexts = {
        row["observation_id"]: row
        for section in extract["species"].values()
        for row in section.get("context", [])
    }

    factor = contexts["mendybaev_2013_abstract_unique_isotope_factor_context"]
    assert factor["values"]["value"] == "0.98372 ± 0.00041"
    assert factor["locator"]["page"] == 1
    assert factor["locator"]["published_page"] == 368

    rate_estimates = contexts["mendybaev_2013_printed_evaporation_rate_estimates_context"]
    assert len(rate_estimates["values"]["estimates_as_printed"]) == 5
    natural_estimates = contexts["mendybaev_2013_natural_cai_isotope_loss_estimates_context"]
    assert len(natural_estimates["values"]["estimates_as_printed"]) == 2
    for row in rate_estimates["values"]["estimates_as_printed"]:
        assert row["locator"]["page"] == 15
        assert row["locator"]["published_page"] == 382
    for row in natural_estimates["values"]["estimates_as_printed"]:
        assert "page" in row["locator"]
        assert "published_page" in row["locator"]


def test_table1_mass_loss_rows_retain_printed_weights_and_derived_ratios() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = document["species"]["unknown"]["observations"]
    assert len(observations) == 24

    by_sample = {row["values"]["sample_as_printed"]: row for row in observations}
    assert set(by_sample) == set(EXPECTED_MASS_LOSS)
    for sample, (initial, final, expected_fraction) in EXPECTED_MASS_LOSS.items():
        row = by_sample[sample]
        values = row["values"]
        assert row["type"] == "mass_loss"
        assert row["experiment"] == (
            "mendybaev-fun1-vacuum-evaporation"
            if sample.startswith("FUN1-")
            else "mendybaev-fun2-vacuum-evaporation"
        )
        assert row["units"] == "fraction"
        assert row["per"] == "initial_mass"
        assert values["quantity"] == "mass_loss_fraction"
        assert values["method_class"] == "measured_reduced"
        assert values["initial_weight_g"] == initial
        assert values["final_weight_g"] == final
        assert "mass_loss_fraction" not in values
        conditions = row["point_conditions"]
        expected_series = sample.split("-", 1)[0]
        assert "printed_composition" not in conditions
        assert "total_pressure_Pa" not in conditions
        assert "composition_role" not in values
        assert conditions["temperature_C"]["state"]["value"] == "1900"
        assert conditions["time_min"]["state"]["value"] == EXPECTED_RUN_MINUTES[sample]
        assert conditions["time_min"]["locator"]["column_header"] == "Run duration, min"
        assert conditions["fO2_log"]["state"]["tag"] == "unknown"
        assert abs(
            _fraction_from_printed_weights(initial, final)
            - Decimal(expected_fraction)
        ) <= Decimal("1e-39")
        assert "sample_mass_kg" not in values
        experiment = next(
            item
            for item in document["experiments"]
            if item["experiment_id"] == row["experiment"]
        )
        starting = experiment["sample"]["initial_composition"]
        assert starting["locator"]["row"] == f"{expected_series} starting"
        assert dict(starting["state"]["value"]["components"]) == EXPECTED_STARTING_WT_PCT[
            expected_series
        ]
        assert experiment["sample"]["printed_composition"]["state"]["value"] == (
            EXPECTED_STARTING_WT_PCT[expected_series]
        )
        assert "mass_kg" not in experiment["sample"]

        locator = row["locator"]
        assert locator["page"] == 5
        assert locator["published_page"] == 372
        assert locator["pdf_page_index"] == 4
        assert locator["table"] == "1"
        assert locator["row"] == sample
        assert row["derived_from"] == [MASS_LOSS_CONTEXT]
        derivation = row["derivation"]
        assert derivation["relation"] == (
            "(initial_weight_g - final_weight_g) / initial_weight_g"
        )
        assert derivation["inputs"] == [MASS_LOSS_CONTEXT]
        parameters = {
            name: entry
            for name, entry in derivation["parameters"]
        }
        assert parameters["initial_weight_g"]["state"]["value"] == initial
        assert parameters["final_weight_g"]["state"]["value"] == final
        assert (
            parameters["initial_weight_g"]["locator"]["column_header"]
            == "Initial weight, g"
        )
        assert (
            parameters["final_weight_g"]["locator"]["column_header"]
            == "Final weight, g"
        )

    zero_time = {"FUN1-3", "FUN1-12", "FUN2-4"}
    zero_time_minutes = {
        row["sample"]: row["t_min"]
        for observation in document["species"]["SiO2"]["observations"]
        for row in observation["values"]["series"]
        if row["sample"] in zero_time
    }
    assert zero_time_minutes == {sample: "0" for sample in zero_time}
    assert all(
        Decimal(EXPECTED_MASS_LOSS[sample][2]) > 0
        for sample in zero_time
    )
    assert Decimal(EXPECTED_MASS_LOSS["FUN1-3"][2]) != Decimal(
        EXPECTED_MASS_LOSS["FUN1-12"][2]
    )

    fidelity = document["fidelity_samples"]
    mass_fidelity = [
        row for row in fidelity if row["path"].startswith("species.unknown.")
    ]
    assert len(mass_fidelity) == 120
    assert all(
        row["reviewed_by"] == "Simon Rowland"
        and row["reviewed_on"] == "2026-11-23"
        and row["locator"]["source_path"].endswith("pdftotext-layout.txt")
        for row in mass_fidelity
    )


def test_all_residue_rows_receive_their_located_starting_mass_and_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    series_rows = [
        point
        for observation in document["species"]["SiO2"]["observations"]
        for point in observation["values"]["series"]
    ]
    assert len(series_rows) == 96
    for point in series_rows:
        sample = point["sample"]
        expected_kg = Decimal(EXPECTED_MASS_LOSS[sample][0]) / Decimal("1000")
        mass = point["point_conditions"]["sample_mass_kg"]
        assert mass["state"]["tag"] == "value"
        assert Decimal(mass["state"]["value"]) == expected_kg
        assert mass["locator"] == {
            "page": 5,
            "published_page": 372,
            "pdf_page_index": 4,
            "table": "1",
            "row": sample,
            "column_header": "Initial weight, g",
        }

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    residue_rows = [
        observation
        for observation in migrator.result.observations.values()
        if observation.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert len(residue_rows) == 96
    for observation in residue_rows:
        sample = observation.locator.note.removeprefix("row=")
        expected_kg = Decimal(EXPECTED_MASS_LOSS[sample][0]) / Decimal("1000")
        assert observation.identity.sample_mass_kg.is_value
        assert observation.identity.sample_mass_kg.value == expected_kg
        assert (
            observation.point_conditions["sample_mass_kg"].state.value
            == expected_kg
        )
        assert observation.identity.temperature_K.value == Decimal("2173.15")
        assert (
            observation.point_conditions["time_min"].state.value
            == Decimal(EXPECTED_RUN_MINUTES[sample])
        )

    assert len(document["experiments"]) == 2
    for experiment in document["experiments"]:
        assert "2.5 mm diameter iridium wire loops" in experiment["method_locator"]["note"]
        assert experiment["method_locator"]["published_page"] == 371
        family = experiment["experiment_id"].removeprefix("mendybaev-")[:4].upper()
        assert dict(experiment["sample"]["printed_composition"]["state"]["value"]) == (
            EXPECTED_STARTING_WT_PCT[family]
        )


def test_table1_mass_loss_rows_migrate_as_separate_fun_experiments() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    mass_loss = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in observations
        if observation.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
    }
    composition = [
        observation
        for observation in observations
        if observation.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert len(mass_loss) == 24
    assert len(composition) == 96
    assert len(observations) == 120
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert all(observation.identity.quantity.is_value for observation in observations)

    for sample, (_, _, expected_fraction) in EXPECTED_MASS_LOSS.items():
        observation_id = (
            f"mendybaev_2013_table1_{sample.lower().replace('-', '_')}_mass_loss_fraction"
        )
        observation = mass_loss[observation_id]
        assert observation.identity.species.formula == "unknown"
        assert observation.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        experiment = migrator.result.experiments[observation.experiment_id]
        assert experiment.sample.initial_composition is not None
        assert observation.identity.composition == experiment.sample.initial_composition.state
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        assert observation.value.kind is ValueKind.POINT
        assert abs(observation.value.point - Decimal(expected_fraction)) <= Decimal("1e-39")
        assert observation.derivation is not None
        assert observation.locator.page == 5
        assert observation.derivation.relation == (
            "(initial_weight_g - final_weight_g) / initial_weight_g"
        )
        assert observation.derived_from == (MASS_LOSS_CONTEXT,)
        assert observation.identity.sample_mass_kg.is_unknown
        assert observation.identity.per.is_value
        assert observation.identity.per.value is PerBasis.INITIAL_MASS
        assert observation.identity.total_pressure_Pa.is_unknown
        assert observation.identity.temperature_K.value == Decimal("2173.15")
        assert (
            observation.point_conditions["temperature_C"].state.value
            == Decimal("1900")
        )
        assert observation.point_conditions["temperature_C"].locator.page == 4
        assert observation.point_conditions["temperature_K"].state.value == Decimal("2173.15")
        experiment = migrator.result.experiments[observation.experiment_id]
        assert observation.point_conditions["fO2_log"].state.is_unknown
        assert observation.point_conditions["time_min"].state.value == Decimal(
            EXPECTED_RUN_MINUTES[sample]
        )
        assert observation.experiment_id.endswith(
            "mendybaev-fun1-vacuum-evaporation"
            if sample.startswith("FUN1-")
            else "mendybaev-fun2-vacuum-evaporation"
        )


def test_fun1_10_final_weight_mutation_moves_its_migrated_fraction(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    target = next(
        row
        for row in document["species"]["unknown"]["observations"]
        if row["locator"]["row"] == "FUN1-10"
    )
    values = target["values"]
    values["final_weight_g"] = "0.0079"
    changed_fraction = _fraction_from_printed_weights(
        values["initial_weight_g"], values["final_weight_g"]
    )
    values.pop("mass_loss_fraction", None)
    final_parameter = next(
        entry
        for name, entry in target["derivation"]["parameters"]
        if name == "final_weight_g"
    )
    final_parameter["state"]["value"] = values["final_weight_g"]

    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(mutated_extract)
    migrator.finalize()
    migrated = next(
        observation
        for observation in migrator.result.observations.values()
        if observation.observation_id.endswith(
            "mendybaev_2013_table1_fun1_10_mass_loss_fraction"
        )
    )
    assert abs(migrated.value.point - changed_fraction) <= Decimal("1e-39")
    assert migrated.value.point != Decimal(EXPECTED_MASS_LOSS["FUN1-10"][2])


def test_fun1_10_mass_loss_scalar_cannot_survive_deleted_weight_operands(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    target = next(
        row
        for row in document["species"]["unknown"]["observations"]
        if row["locator"]["row"] == "FUN1-10"
    )
    target["values"]["mass_loss_fraction"] = EXPECTED_MASS_LOSS["FUN1-10"][2]
    del target["values"]["initial_weight_g"]
    del target["values"]["final_weight_g"]

    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(mutated_extract)
    migrator.finalize()
    migrated = next(
        observation
        for observation in migrator.result.observations.values()
        if observation.observation_id.endswith(
            "mendybaev_2013_table1_fun1_10_mass_loss_fraction"
        )
    )
    assert migrated.value.kind is not ValueKind.POINT


def test_mendybaev_residue_and_xi_rows_use_their_fun_starting_composition() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    for experiment_suffix in (
        "mendybaev-fun1-vacuum-evaporation",
        "mendybaev-fun2-vacuum-evaporation",
    ):
        experiment = next(
            experiment
            for experiment in migrator.result.experiments.values()
            if experiment.experiment_id.endswith(experiment_suffix)
        )
        starting = experiment.sample.initial_composition
        assert starting is not None and starting.state.is_value
        rows = [
            observation
            for observation in migrator.result.observations.values()
            if observation.experiment_id == experiment.experiment_id
            and observation.identity.quantity.value
            in {Quantity.RESIDUE_COMPONENT_COMPOSITION, Quantity.MASS_LOSS_FRACTION}
        ]
        assert len(rows) == (70 if experiment_suffix.endswith("fun1-vacuum-evaporation") else 50)
        assert all(observation.identity.composition == starting.state for observation in rows)
        residue_rows = [
            observation for observation in rows
            if observation.identity.quantity.value
            is Quantity.RESIDUE_COMPONENT_COMPOSITION
        ]
        pressure = experiment.conditions["total_pressure_Pa"]
        assert pressure.state.value.kind is ValueKind.BOUND
        assert pressure.state.value.bound_value == Decimal("0.0001")
        assert pressure.locator.page == 4
        assert all(
            observation.identity.temperature_K.value == Decimal("2173.15")
            and "time_min" in observation.point_conditions
            and observation.point_conditions["time_min"].state.is_value
            for observation in rows
        )
        assert all(
            observation.identity.composition == starting.state
            and observation.point_conditions["composition"].state != starting.state
            for observation in residue_rows
        )


def test_mendybaev_fidelity_warnings_do_not_grow_with_xi_rows() -> None:
    from tools.validate_literature_extracts import fidelity_source_warnings

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    warnings = {warning.kind: warning.count for warning in fidelity_source_warnings(document)}
    assert warnings.get("uncovered_observation", 0) == 2
    assert warnings.get("unattested_sample", 0) == 0
