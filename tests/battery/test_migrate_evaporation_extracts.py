from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    Engine,
    EvidenceClass,
    MethodToken,
    Quantity,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.migrate import Migrator
from tests.battery.test_migrate import _migrate_real_extract


SID = "kems-012-sossi-2019"
EXTRACT = f"{SID}.yaml"
FEDKIN_SID = "kems-005-fedkin-2006"
FEDKIN_EXTRACT = f"{FEDKIN_SID}.yaml"
HASHIMOTO_SID = "kems-015-hashimoto-1983"
HASHIMOTO_EXTRACT = f"{HASHIMOTO_SID}.yaml"
RICHTER07_SID = "kems-010-richter-2007"
RICHTER07_EXTRACT = f"{RICHTER07_SID}.yaml"
RICHTER11_SID = "richter-mendybaev-christensen-etal-2011-olivine-chondrule-evaporation"
RICHTER11_EXTRACT = f"{RICHTER11_SID}.yaml"
ROOT = Path(__file__).resolve().parents[2]
TABLE2_RUNS = {
    "sossi-2019-run-2m-16-07-16e": Decimal("848.9"),
    "sossi-2019-run-2m-16-07-16a": Decimal("742.5"),
    "sossi-2019-run-2m-16-07-16b": Decimal("739.9"),
}
RICHTER07_FIT_EXPERIMENTS = {
    "cai-r-11", "cai-r-18", "cai-r-2", "cai-r-3", "cai-r-4",
    "cai-r-8", "cai-r-9", "cai-r2-1", "cai-r2-10", "cai-r2-11", "cai-r2-12",
    "cai-r2-16", "cai-r2-2", "cai-r2-3", "cai-r2-4", "cai-r2-5", "cai-r3-1",
    "cai-r3-10", "cai-r3-11", "cai-r3-13", "cai-r3-14", "cai-r3-15",
    "cai-r3-18", "cai-r3-19", "cai-r3-20", "cai-r3-21", "cai-r3-4", "cai-r3-5",
    "cai-r3-7", "cai-r3-8", "cai-r3-9",
}
REPO_ROOT = Path(__file__).resolve().parents[2]


def _context_data_row(result, source_id: str, observation_id: str) -> dict:
    def visit(value):
        if isinstance(value, dict):
            if value.get("observation_id") == observation_id or value.get(
                "source_observation_id"
            ) == observation_id:
                return value
            for child in value.values():
                match = visit(child)
                if match is not None:
                    return match
        elif isinstance(value, list):
            for child in value:
                match = visit(child)
                if match is not None:
                    return match
        return None

    for records in result.context_by_work.values():
        for record in records:
            if record.get("source_id") != source_id:
                continue
            match = visit(record)
            if match is not None:
                return match
    raise AssertionError(f"no context row for {source_id}::{observation_id}")


def _assert_migrated_rows_keep_temperature_and_conditions(result, source_id: str) -> None:
    rows = [
        row
        for row in result.observations.values()
        if row.source_id == source_id
        and row.value.kind is ValueKind.POINT
        and row.identity.temperature_K is not None
        and row.identity.temperature_K.is_value
    ]
    for row in rows:
        temperature = row.point_conditions.get("temperature_K")
        assert temperature is not None
        assert temperature.state.is_value
        assert temperature.locator is not None
        assert all(condition.locator is not None for condition in row.point_conditions.values())


def _assert_sossi_pins(result) -> None:
    assert result.validation.hard_issues == ()
    _assert_migrated_rows_keep_temperature_and_conditions(result, SID)
    observations = [
        row
        for row in result.observations.values()
        if row.source_id == SID
        and row.value.kind is ValueKind.POINT
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert len(observations) == 516
    assert all(row.value.kind is ValueKind.POINT for row in observations)
    assert all(row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION for row in observations)
    assert all(row.uncertainty.kind is UncertaintyKind.NONE for row in observations)
    assert all(row.identity.temperature_K.is_value for row in observations)
    assert all(row.identity.fO2_Pa is not None and row.identity.fO2_Pa.is_value for row in observations)
    assert all(row.identity.total_pressure_Pa is not None and row.identity.total_pressure_Pa.is_value for row in observations)

    source_document = yaml.safe_load(
        (ROOT / "data/literature/extracts" / f"{SID}.yaml").read_text(
            encoding="utf-8"
        )
    )
    source_points: Counter[tuple[str, Decimal]] = Counter()
    source_rows = []
    for formula, body in source_document["species"].items():
        for source_row in body.get("observations", []):
            values = source_row.get("values") or {}
            if (
                source_row.get("type") != "rate_series"
                or values.get("quantity")
                != "open_furnace_residue_composition_vs_time"
            ):
                continue
            points = [
                point
                for point in values.get("series", [])
                if isinstance(point, dict) and point.get("residue_ppm") is not None
            ]
            if not points:
                continue
            source_rows.append(source_row)
            assert source_row.get("uncertainty") is None
            for point in points:
                source_points[(formula, Decimal(str(point["residue_ppm"])))] += 1
    assert len(source_rows) == 96
    assert sum(source_points.values()) == 516
    assert Counter(
        (row.identity.species.formula, row.value.point) for row in observations
    ) == source_points

    manganese = [
        row
        for row in observations
        if row.identity.species.formula == "Mn"
        and "_quoted_" not in row.observation_id
    ]
    for run_id, expected in TABLE2_RUNS.items():
        matches = [
            row
            for row in manganese
            if row.experiment_id.endswith(f"::experiment::{run_id}")
        ]
        assert len(matches) == 1
        row = matches[0]
        assert row.identity.temperature_K.value == Decimal("1773.15")
        assert row.value.point == expected

    contexts = [
        context
        for records in result.context_by_work.values()
        for context in records
        if context.get("source_id") == SID
    ]
    table3 = next(
        context
        for context in contexts
        if context.get("observation_id") == "sossi_2019_table3_apparent_vaporization_constant_K_star"
    )
    assert table3["schema_gap"] == "apparent vaporization constant K*: no store quantity token"
    assert len(table3["values"]) == 18
    na = next(row for row in table3["values"] if row["element"] == "Na")
    assert na["values_by_temperature_K"] == [
        {
            "temperature_K": 1673.15,
            "log10_K_star": -4.25,
            "log10_K_star_uncertainty": 0.1,
            "N_points_at_T": 2,
        }
    ]
    cro = next(
        row
        for row in table3["values"]
        if row.get("source_observation_id") == "sossi_2019_cr_logKstar_table3_CrO1_5"
    )
    assert cro["reaction"] == "CrO1.5(l) + 1/4 O2 = CrO2(g)"
    assert cro["values_by_temperature_K"] == [
        {"temperature_K": 1673.15, "log10_K_star": -4.96, "N_points_at_T": 1},
        {
            "temperature_K": 1773.15,
            "log10_K_star": -4.41,
            "log10_K_star_uncertainty": 0.03,
            "N_points_at_T": 2,
        },
        {"temperature_K": 1823.15, "log10_K_star": -4.2, "N_points_at_T": 1},
    ]
    table5 = next(context for context in contexts if context.get("table") == "5")
    cro_table5 = next(
        row
        for row in table5["values"]
        if row.get("source_observation_id") == "sossi_2019_cr_table5_experimental_CrO1_5"
    )
    assert cro_table5["values"]["Delta_S_star_kJ_molK"] == 0.085
    assert cro_table5["values"]["Delta_S_star_uncertainty_kJ_molK"] == 0.017
    assert cro_table5["values"]["Delta_H_star_kJ_mol"] == 301.2
    assert cro_table5["values"]["Delta_H_star_uncertainty_kJ_mol"] == 29.1
    assert {context.get("table") for context in contexts if context.get("table") in {"4", "5", "6"}} == {"4", "5", "6"}
    assert all(
        context["schema_gap"] == "t-1382"
        for context in contexts
        if context.get("table") in {"4", "5", "6"}
        or context.get("observation_id")
        == "sossi_2019_narrative_evaporation_model_inferences"
    )


def test_sossi_fitted_outputs_are_context_and_run_measurements_keep_conditions(tmp_path: Path) -> None:
    # Table 3–6 fits name tables:kems-012-sossi-2019. That asset id is registered
    # on the repository index row; a stub index cannot resolve it.
    result = _migrate_real_extract(
        tmp_path, EXTRACT, use_repository_index_row=True
    )
    _assert_sossi_pins(result)


def _assert_fedkin_pins(result) -> None:
    assert result.validation.hard_issues == ()
    _assert_migrated_rows_keep_temperature_and_conditions(result, FEDKIN_SID)
    fedkin_rows = [
        row for row in result.observations.values() if row.source_id == FEDKIN_SID
    ]
    assert len(fedkin_rows) == 2
    points = {
        row.observation_id.rsplit("::T=", 1)[0].split("::")[-1]: row
        for row in result.observations.values()
        if row.source_id == FEDKIN_SID
        and row.observation_id.rsplit("::T=", 1)[0].split("::")[-1]
        in {"fedkin_2006_na_yu_langmuir", "fedkin_2006_k_yu_langmuir"}
    }
    assert set(points) == {
        "fedkin_2006_na_yu_langmuir",
        "fedkin_2006_k_yu_langmuir",
    }
    for observation_id, expected in (
        ("fedkin_2006_na_yu_langmuir", Decimal("0.26")),
        ("fedkin_2006_k_yu_langmuir", Decimal("0.13")),
    ):
        row = points[observation_id]
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == expected
        assert row.evidence.class_.is_value
        assert row.evidence.class_.value is EvidenceClass.AUTHOR_ESTIMATE
        assert row.evidence.model
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1723.0")
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("1723.0")
        assert temperature.locator is not None
        pressure = row.point_conditions["total_pressure_Pa"]
        assert pressure.state.is_value
        assert pressure.state.value.point == Decimal("1.0")
        assert pressure.locator is not None

    contexts = [
        context
        for records in result.context_by_work.values()
        for context in records
        if context.get("source_id") == FEDKIN_SID
    ]
    table3 = next(context for context in contexts if context.get("table") == "3")
    assert table3["locator"]["page"] == 7
    assert table3["locator"]["published_page"] == 212
    assert {row["element"] for row in table3["values"]} == {"Fe", "Mg", "SiO"}
    assert all(len(row["temperature_values"]) == 4 for row in table3["values"])
    fe = next(row for row in table3["values"] if row["element"] == "Fe")
    assert fe["alexander_2004_temperature_values"][-1] == {
        "T_K": 2273.0,
        "alpha": 0.28,
        "sigma": 0.01,
    }
    table4 = next(context for context in contexts if context.get("table") == "4")
    assert table4["locator"]["page"] == 14
    assert len(table4["values"]) == 4
    assert {
        context.get("observation_id") for context in contexts
    } == {
        "fedkin_2006_table3_hashimoto_fit_outputs",
        "fedkin_2006_table4_inverse_fit_outputs",
    }


def test_fedkin_real_tree_migration_keeps_yu_points_without_queue() -> None:
    migrator = Migrator(root=REPO_ROOT)
    migrator._migrate_extract(
        REPO_ROOT / "data/literature/extracts" / FEDKIN_EXTRACT
    )
    migrator.finalize()

    _assert_fedkin_pins(migrator.result)
    source_path = f"data/literature/extracts/{FEDKIN_EXTRACT}"
    assert not [
        entry for entry in migrator.result.queue if entry.source == source_path
    ]


def test_fedkin_table3_fit_outputs_are_context_and_yu_runs_keep_conditions(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, FEDKIN_EXTRACT)
    _assert_fedkin_pins(result)


def _assert_hashimoto_table3_pins(result) -> None:
    assert result.validation.hard_issues == ()
    _assert_migrated_rows_keep_temperature_and_conditions(result, HASHIMOTO_SID)
    values = {}
    for row in result.observations.values():
        if (
            row.value.kind is ValueKind.POINT
            and "::hashimoto_1983_table3_residue_composition_series::" in row.observation_id
            and ":row=c3-(2):" in row.observation_id
            and row.identity.temperature_K.is_value
            and row.identity.temperature_K.value == Decimal("1973.15")
        ):
            values[row.identity.species.formula] = row
    expected = {"SiO2": Decimal("39.09"), "Al2O3": Decimal("3.57"), "FeO": Decimal("27.92")}
    assert set(values) >= set(expected)
    for formula, value in expected.items():
        row = values[formula]
        assert row.value.point == value
        assert row.identity.temperature_K.value == Decimal("1973.15")
        assert row.identity.total_pressure_Pa is not None
        assert row.identity.fO2_Pa is not None


def test_hashimoto_table3_residue_values_keep_row_temperature_and_conditions(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, HASHIMOTO_EXTRACT)
    _assert_hashimoto_table3_pins(result)


def _assert_richter07_pins(result) -> None:
    assert result.validation.hard_issues == ()
    _assert_migrated_rows_keep_temperature_and_conditions(result, RICHTER07_SID)
    expected = {
        "R3-1": Decimal("0.265"),
        "R3-2": Decimal("0.0"),
        "R3-4": Decimal("0.333"),
    }
    points = {
        sample: next(
            row
            for row in result.observations.values()
            if row.value.kind is ValueKind.POINT
            and row.observation_id.rsplit("::T=", 1)[0].endswith(
                f"richter_2007_mg_table1_mass_loss_fraction_{sample}"
            )
        )
        for sample in expected
    }
    for sample, value in expected.items():
        row = points[sample]
        assert row.value.point == value
        assert row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        assert row.identity.temperature_K.value == Decimal("1873.15")
        assert row.identity.total_pressure_Pa is not None
        assert row.point_conditions["temperature_K"].state.value == Decimal("1873.15")
        assert row.point_conditions["temperature_K"].locator is not None

    contexts = [
        context
        for records in result.context_by_work.values()
        for context in records
        if context.get("source_id") == RICHTER07_SID
    ]
    table1 = next(context for context in contexts if context.get("table") == "1")
    assert table1["schema_gap"] == "t-1382"
    assert any(row.get("sample") == "R3-1" for row in table1["values"])
    uncertainty = table1["uncertainty"]
    assert uncertainty["relative_rate_uncertainty_percent"] == {
        "1_mm_diameter_samples": 20.0,
        "2_5_and_6_mm_diameter_samples": 10.0,
    }
    assert uncertainty["mass_loss_fraction_uncertainty"] == "not_stated"
    assert uncertainty["locator"]["published_page"] == 5550

    for species, c0, energy, sigma in (
        ("Mg", Decimal("143.0"), Decimal("121000.0"), Decimal("53000.0")),
        ("SiO", Decimal("25.3"), Decimal("92000.0"), Decimal("37000.0")),
    ):
        fit_id = f"richter_2007_{species.lower()}_cai_langmuir_alpha_arrhenius"
        fit = _context_data_row(result, RICHTER07_SID, fit_id)
        assert fit["type"] == "best_fit_model_context"
        assert fit["locator"]["published_page"] == 5553
        assert fit["locator"]["figure"] == "3"
        assert fit["T_range_K"] == [1773.15, 2173.15]
        assert set(fit["applies_to_experiments"]) == RICHTER07_FIT_EXPERIMENTS
        assert "cai-r-7" not in fit["applies_to_experiments"]
        assert len(fit["applies_to_experiments"]) == 31
        model_dependence = fit["values"]["model_dependence"]
        assert "BCAI-ss1, BCAI-ss2, BCAI-s1, BCAI-s3, BCAI-m1, and BCAI-m2" in model_dependence
        assert "1.87 × 10⁻⁴ bar H2" in model_dependence
        assert "not a complete coverage claim" in model_dependence
        alpha_form = fit["values"]["alpha_form"]
        assert Decimal(str(alpha_form["c0"])) == c0
        assert Decimal(str(alpha_form["E_J_per_mol"])) == energy
        assert Decimal(str(alpha_form["E_uncertainty_J_per_mol"])) == sigma
        assert fit["uncertainty_convention"] == "2_sigma"
        assert "Figure 2" in fit["superseded_note"]

    runs = [
        experiment
        for experiment_id, experiment in result.experiments.items()
        if experiment_id.rsplit("::experiment::", 1)[-1].startswith("cai-")
    ]
    assert len(runs) == 51
    for experiment in runs:
        schedule = experiment.thermal_schedule
        assert schedule is not None
        assert len(schedule.ramps) == 2
        assert all(ramp.rate_K_s.locator is not None for ramp in schedule.ramps)
        assert all(
            ramp.rate_K_s.state.value.point
            == Decimal("0.3333333333333333333333333333")
            for ramp in schedule.ramps
        )
        assert len(schedule.setpoints_and_holds) == 1
        assert schedule.setpoints_and_holds[0].hold_duration_s.state.value.point == Decimal("1200.0")
        assert schedule.method.locator is not None
        assert schedule.cooling_or_quench.locator is not None


def test_richter07_table1_mass_loss_and_context_keep_conditions(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, RICHTER07_EXTRACT)
    _assert_richter07_pins(result)


def test_richter11_residue_rows_migrate_temperature_and_time_conditions(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(tmp_path, RICHTER11_EXTRACT)
    residues = [
        row
        for row in result.observations.values()
        if row.source_id == RICHTER11_SID
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert residues
    for row in residues:
        assert row.value.kind is ValueKind.POINT
        temperature = row.point_conditions.get("temperature_K")
        assert temperature is not None
        assert temperature.locator is not None
        identity_temperature = row.identity.temperature_K
        if identity_temperature is not None and identity_temperature.is_value:
            assert temperature.state.is_value
            assert temperature.state.value == identity_temperature.value
        else:
            assert temperature.state.is_unknown

        run_time = row.point_conditions.get("time_min")
        assert run_time is not None
        assert run_time.locator is not None
        assert all(
            condition.locator is not None
            for condition in row.point_conditions.values()
        )


def test_richter_time_series_residue_routes_to_owner_and_dependencies(
    tmp_path: Path, monkeypatch,
) -> None:
    import simulator.battery.migrate as migrate_module
    import simulator.battery.score as score_module
    from simulator.battery.score_run import _residue_adapter_kinds

    source = yaml.safe_load(
        (ROOT / "data/literature/extracts" / RICHTER11_EXTRACT).read_text(
            encoding="utf-8"
        )
    )
    raw_residue_rows = [
        row
        for _formula, row in migrate_module.iter_extract_observations(source)
        if (row.get("values") or {}).get("quantity")
        == "residue_composition_vs_time"
    ]
    assert len(raw_residue_rows) == 325
    censored = next(
        row for row in raw_residue_rows
        if row["observation_id"] == "richter_2011_table1_iiab1-3_residue_na2o"
    )
    assert censored["values"]["series"][0]["Na2O_wt_pct"] == "<0.01"

    result = _migrate_real_extract(tmp_path, RICHTER11_EXTRACT)
    residues = [
        row
        for row in result.observations.values()
        if row.source_id == RICHTER11_SID
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    # The 325th printed cell is the censored <0.01 Na2O value above; the
    # existing numeric selector keeps that cell typed-refused.
    assert len(residues) == 324
    vacuum_rows = []
    gas_rows = []
    for row in residues:
        experiment = result.experiments[row.experiment_id]
        adapters = _residue_adapter_kinds(
            row, Engine.OPENIMCC, experiment,
            Quantity.RESIDUE_COMPONENT_COMPOSITION,
        )
        if (
            experiment.method.is_value
            and experiment.method.value is MethodToken.LANGMUIR_FREE_EVAPORATION
        ):
            assert adapters == (MethodToken.LANGMUIR_FREE_EVAPORATION,)
            vacuum_rows.append(row)
        else:
            assert experiment.method.is_value
            assert experiment.method.value is MethodToken.UNSPECIFIED
            # Selector dispatch is only for the existing typed-refusal path;
            # the unreported gas method never reaches a Langmuir engine call.
            assert adapters == (MethodToken.LANGMUIR_FREE_EVAPORATION,)
            gas_rows.append(row)
    assert len(vacuum_rows) == 167
    assert len(gas_rows) == 157

    context = score_module.ScoreContext(
        {}, dict(result.experiments), dict(result.observations), root=ROOT
    )
    for row in vacuum_rows:
        prediction = score_module._extent_residue_prediction(
            context, row, Engine.OPENIMCC
        )
        assert prediction is not None and prediction.value is None
        assert prediction.refusal_detail["reason"] == (
            "awaits_time_forward_langmuir_route"
        )
        assert prediction.refusal_detail["upstream_reason"] == (
            "measured_mass_loss_fraction_missing_or_invalid"
        )
    for row in gas_rows:
        prediction = score_module._extent_residue_prediction(
            context, row, Engine.OPENIMCC
        )
        assert prediction is not None and prediction.value is None
        assert prediction.refusal_detail["reason"] == "awaits_d475_series_flux"
        assert prediction.refusal_detail["sample_support_not_printed"] is True
    langmuir_owner = score_module._langmuir_residue_prediction

    def reject_gas_langmuir_route(*_args, **_kwargs):
        raise AssertionError("one-bar gas rows must not enter vacuum Langmuir")

    monkeypatch.setattr(
        score_module, "_langmuir_residue_prediction", reject_gas_langmuir_route
    )
    gas_prediction = score_module._extent_residue_prediction(
        context, gas_rows[0], Engine.OPENIMCC
    )
    assert gas_prediction is not None
    assert gas_prediction.refusal_detail["reason"] == "awaits_d475_series_flux"
    # Mutation probe: restore the former absent-state-only check. It misses
    # migrated UNSPECIFIED gas methods and makes the routed prediction vanish.
    with monkeypatch.context() as mutation:
        mutation.setattr(
            score_module,
            "_richter2011_method_is_unreported",
            lambda experiment: experiment is not None
            and not experiment.method.is_value,
        )
        assert score_module._extent_residue_prediction(
            context, gas_rows[0], Engine.OPENIMCC
        ) is None
    monkeypatch.setattr(
        score_module, "_langmuir_residue_prediction", langmuir_owner
    )

    richter07_root = tmp_path / "richter07"
    richter07_root.mkdir()
    result07 = _migrate_real_extract(richter07_root, RICHTER07_EXTRACT)
    residues07 = [
        row for row in result07.observations.values()
        if row.source_id == RICHTER07_SID
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert len(residues07) == 204
    context07 = score_module.ScoreContext(
        {}, dict(result07.experiments), dict(result07.observations), root=ROOT
    )
    anchor_pairs = []
    missing_anchor_rows = []
    for row in residues07:
        experiment = result07.experiments[row.experiment_id]
        assert _residue_adapter_kinds(
            row, Engine.INTERNAL_ANALYTICAL, experiment,
            Quantity.RESIDUE_COMPONENT_COMPOSITION,
        ) == (MethodToken.LANGMUIR_FREE_EVAPORATION,)
        anchors = [
            candidate for candidate in result07.observations.values()
            if candidate.source_id == RICHTER07_SID
            and candidate.experiment_id == row.experiment_id
            and candidate.identity.quantity.is_value
            and candidate.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
            and candidate.locator is not None and row.locator is not None
            and candidate.locator.table == row.locator.table
            and candidate.locator.note == row.locator.note
        ]
        if anchors:
            assert len(anchors) == 1
            anchor_pairs.append((row, experiment))
        else:
            missing_anchor_rows.append((row, experiment))
    assert len(anchor_pairs) == 200
    assert len(missing_anchor_rows) == 4
    anchored_row, anchored_experiment = anchor_pairs[0]
    prediction = score_module._extent_residue_prediction(
        context07, anchored_row, Engine.INTERNAL_ANALYTICAL
    )
    assert prediction is not None
    assert prediction.value is not None, prediction.refusal_detail
    missing_row, _missing_experiment = missing_anchor_rows[0]
    prediction = score_module._extent_residue_prediction(
        context07, missing_row, Engine.INTERNAL_ANALYTICAL
    )
    assert prediction is not None and prediction.value is None
    assert prediction.refusal_detail["reason"] == (
        "awaits_time_forward_langmuir_route"
    )

    # Mutation probe: restore the old source lock and show the real score-run
    # consumer loses both Richter residue series.
    previous_selector = score_module._extent_residue_method

    def old_source_lock(reference, experiment, quantity):
        if reference.source_id in {RICHTER07_SID, RICHTER11_SID}:
            return None
        return previous_selector(reference, experiment, quantity)

    monkeypatch.setattr(score_module, "_extent_residue_method", old_source_lock)
    for row, experiment in (
        (vacuum_rows[0], result.experiments[vacuum_rows[0].experiment_id]),
        (anchored_row, anchored_experiment),
    ):
        assert MethodToken.LANGMUIR_FREE_EVAPORATION not in _residue_adapter_kinds(
            row, Engine.INTERNAL_ANALYTICAL, experiment,
            Quantity.RESIDUE_COMPONENT_COMPOSITION,
        )
        assert score_module._extent_residue_prediction(
            context07 if row.source_id == RICHTER07_SID else context,
            row,
            Engine.INTERNAL_ANALYTICAL,
        ) is None


def test_richter02_type_b_melt_phase_is_canonical(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-037-richter-2002.yaml")
    assert result.validation.hard_issues == ()
    assert not any(
        "phase string 'silicate_melt_Type_B_CAI_like'" in item.why
        for item in result.queue
    )


def test_richter02_table1_flux_and_alpha_values_keep_conditions(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-037-richter-2002.yaml")
    assert result.validation.hard_issues == ()
    assert result.observations == {}
    gamma_context = _context_data_row(
        result,
        "kems-037-richter-2002",
        "richter_2002_table1_per_run_gamma_context",
    )
    assert gamma_context["schema_gap"] == "t-1382"
    area_record = gamma_context["values"]["per_run_avg_area_cm2"]
    assert area_record["source_observation_ids"] == [
        "richter_2002_mg_table1_per_run_areas_b1",
        "richter_2002_sio_table1_per_run_areas_b1",
    ]
    assert area_record["values_by_run"] == {
        "B-133-11": "0.12",
        "B-133-12": "0.14",
        "B-133-13": "0.15",
        "B-133-15": "0.19",
        "B-133-16": "0.13",
        "B-133-17": "0.19",
        "B-133-19": "0.26",
        "B-113-8": "0.161",
        "B-113-9": "0.135",
        "B-113-10": "0.131",
        "BCAI-ss1": "0.0672",
        "BCAI-ss2": "0.0621",
        "BCAI-s1": "0.168",
        "BCAI-s3": "0.134",
        "BCAI-m1": "0.407",
        "BCAI-m2": "0.425",
    }
    gamma_rows = [row for row in gamma_context["values"]["rows"] if row.get("run_id")]
    assert len(gamma_rows) == 32
    for row in gamma_rows:
        assert row["T_C"]
        assert row["table_csv_row"] in range(1, 22)
        assert row["locator"]["published_page"] == 525
        assert row["point_conditions"]
        for condition in row["point_conditions"].values():
            assert condition["state"]
            assert condition["locator"]
        temperature = row["point_conditions"]["temperature_K"]
        assert temperature["state"]["value"]["point"] == str(
            Decimal(row["T_C"]) + Decimal("273.15")
        )
        assert row["derivation"]["relation"] == "Hertz-Knudsen equation (1)"
        assert row["derivation"]["quote"] == (
            "J_i = Σ_{j=1}^{n} γ_ij P_{ij,sat} / √(2π m_ij R T)"
        )
        assert row["derivation"]["locator"]["published_page"] == 523
        assert "Grossman" in row["model_dependence"]
        assert "Hertz-Knudsen" in row["model_dependence"]
        assert row["values"]["method_class"] == "measured_reduced"
    expected_alpha = {
        "richter_2002_table1_mg_gamma_bcai-ss1": Decimal("0.051"),
        "richter_2002_table1_mg_gamma_bcai-ss2": Decimal("0.047"),
        "richter_2002_table1_sio_gamma_bcai-ss1": Decimal("0.058"),
    }
    for observation_id, value in expected_alpha.items():
        row = next(item for item in gamma_rows if item["source_observation_id"] == observation_id)
        assert Decimal(row["values"]["alpha"]) == value
    assert {
        row["source_observation_id"]
        for row in gamma_context["values"]["rows"]
        if row.get("source_observation_id") in {
            "richter_2002_mg_cai_langmuir_gamma",
            "richter_2002_sio_cai_langmuir_gamma",
        }
    } == {
        "richter_2002_mg_cai_langmuir_gamma",
        "richter_2002_sio_cai_langmuir_gamma",
    }

    flux_id = "richter_2002_table1_mg_flux_bcai-ss1"
    assert not any(flux_id in item.observation_id for item in result.observations.values())
    flux = _context_data_row(result, "kems-037-richter-2002", flux_id)
    assert flux["type"] == "printed_areal_flux_context"
    assert flux["units"] == "mol cm^-2 s^-1"
    assert flux["values"]["value_as_printed"] == "4.12e-08"
    assert "kg_per_s" in flux["reason_not_typed_as_evaporation_rate"]
    assert "average area separately" in flux["reason_not_typed_as_evaporation_rate"]
    assert "no total kg_per_s rate is derived" in flux["reason_not_typed_as_evaporation_rate"]


def test_markova_table2_residue_values_keep_row_temperature(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-026-markova-1984.yaml")
    assert result.validation.hard_issues == ()
    _assert_migrated_rows_keep_temperature_and_conditions(result, "kems-026-markova-1984")
    expected = {"SiO2": Decimal("44.4"), "FeO": Decimal("7.01"), "MgO": Decimal("36.92")}
    for formula, value in expected.items():
        row = next(
            item
            for item in result.observations.values()
            if "markova_1984_table2_residual_melt_VI_lherzolite" in item.observation_id
            and item.identity.species.formula == formula
            and item.identity.temperature_K.is_value
            and item.identity.temperature_K.value == Decimal("1748.15")
        )
        assert row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == value
        assert row.point_conditions["temperature_K"].state.value == Decimal("1748.15")
        assert row.point_conditions["composition"].state.value.components

    table2_rows = [
        row
        for row in result.observations.values()
        if "markova_1984_table2_residual_melt_" in row.observation_id
        and row.identity.temperature_K is not None
        and row.identity.temperature_K.is_value
    ]
    assert table2_rows
    assert all(
        row.point_conditions
        and "temperature_K" in row.point_conditions
        and row.point_conditions["temperature_K"].locator is not None
        and "composition" in row.point_conditions
        for row in table2_rows
    )

