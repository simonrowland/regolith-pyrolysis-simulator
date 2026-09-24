"""Schema review regressions: source authority, lineage, and consumer boundaries."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest
import yaml

from simulator.battery import migrate as M
from simulator.battery.consumer_inputs import collect_consumer_inputs
from simulator.battery.enums import AdmissionStatus, EvidenceClass, Quantity
from simulator.battery.generators.bench import engine_point_requests
from simulator.battery.waypoints import charge_moles_by_species, normalized_composition
from tests.battery import factories as F
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree


def _row(name, method="calculated", parents=None, relation="author_mass_balance"):
    row = deepcopy(FIXTURE_EXTRACT["species"]["Na"]["observations"][0])
    row["observation_id"] = name
    row["values"] = {
        "quantity": "pure_Psat", "method_class": method,
        "admission_status": "admitted", "pressure_atm": 1,
    }
    if parents is not None:
        row["values"]["derived_from"] = parents
    if relation is not None:
        row["values"]["derivation"] = {
            "relation": relation, "inputs": list(parents) if parents else ["fixture-source::missing"],
            "output_unit": "Pa",
        }
    return row


def _migrate(tmp_path, rows):
    extract = deepcopy(FIXTURE_EXTRACT)
    extract["species"]["Na"]["observations"] = rows
    return M.migrate(_write_min_tree(tmp_path, extract), write=False)


def _child(result, name="child"):
    return next(o for key, o in result.observations.items()
                if key == f"fixture-source::{name}" or key.startswith(f"fixture-source::{name}::"))


def _series(row, unit="atm"):
    row["values"].pop("pressure_atm", None)
    row["values"]["series"] = [{"T_K": 1200, f"pressure_{unit}": 1}]
    row["units"] = unit
    return row


@pytest.mark.parametrize("method", ["calculated", "author_derived"])
@pytest.mark.parametrize("shape", ["scalar", "atm_series", "Pa_series", "top_level", "regime", "spaced_regime"])
def test_author_reduction_positive_controls(tmp_path, method, shape):
    parent = _row("parent", "measured_direct", relation=None)
    child = _row("child", method, ["parent"])
    if shape.endswith("series"):
        _series(child, shape.split("_")[0])
    elif shape == "top_level":
        for key in ("method_class", "derivation", "derived_from"):
            child[key] = child["values"].pop(key)
    elif shape in ("regime", "spaced_regime"):
        child["regime"] = child["values"].pop("method_class")
        if shape == "spaced_regime":
            child["regime"] = f" {child['regime']} "
    result = _migrate(tmp_path, [parent, child])
    observation = _child(result)
    assert observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert observation.derivation.inputs == ("fixture-source::parent",)
    assert not result.validation.hard_issues
    if shape == "atm_series":
        original = dict(observation.derivation.parameters)["original"]
        assert original.inference.relation == "atm_to_Pa"
        assert "original_unit=atm" in original.inference.inputs
        assert observation.value.point == Decimal("101325")
        loaded = M.observation_from_plain(yaml.safe_load(yaml.safe_dump(M.to_plain(observation))))
        assert loaded.derivation == observation.derivation


@pytest.mark.parametrize("relation", [
    "unit_conversion", "identity", "identity:Pa", "as_published", "atm_to_Pa",
    "wt_pct_to_mole_fraction", "buffer_to_fO2", "author_mass_balance",
])
@pytest.mark.parametrize("origin", ["derivation", "inference", "top_inference"])
def test_author_origin_not_relation_spelling(tmp_path, relation, origin):
    parent = _row("parent", "measured_direct", relation=None)
    child = _row("child", parents=["parent"], relation=relation)
    if origin != "derivation":
        record = child["values"].pop("derivation")
        (child if origin == "top_inference" else child["values"])["inference"] = record
    result = _migrate(tmp_path, [parent, child])
    evidence = _child(result).evidence.class_
    if origin == "derivation":
        assert evidence.value is EvidenceClass.MEASURED_REDUCED
    else:
        assert evidence.is_unknown
        provenance = dict(_child(result).derivation.parameters)["conversion"].inference
        assert provenance.relation == relation
    assert not result.validation.hard_issues


def test_actual_extractor_composition_derivation_cannot_supply_author_origin(tmp_path):
    parent = _row("parent", "measured_direct", relation=None)
    child = _row("child", parents=["parent"], relation=None)
    child["values"]["inference"] = M.to_plain(M.wt_pct_to_mole_fraction_derivation(
        {"SiO2": Decimal("60"), "MgO": Decimal("40")}, None))
    assert _child(_migrate(tmp_path, [parent, child])).evidence.class_.is_unknown


@pytest.mark.parametrize("method", ["calculated", "author_derived"])
@pytest.mark.parametrize("regime", [False, True])
@pytest.mark.parametrize("has_author", [False, True])
def test_conditional_evaluator_uses_author_admission_rule(tmp_path, method, regime, has_author):
    parent = _row("parent", "measured_direct", relation=None)
    child = _row("child", method, parents=["parent"], relation="author_mass_balance" if has_author else None)
    if regime:
        child["regime"] = child["values"].pop("method_class")
    child["values"]["evaluator_family"] = "test_family"
    result = _migrate(tmp_path, [parent, child])
    if has_author:
        assert _child(result).evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    else:
        assert _child(result).evidence.class_.is_unknown
    assert not result.validation.hard_issues


def test_author_inputs_may_be_strict_subset_of_admitted_lineage(tmp_path):
    parent = _row("parent", "measured_direct", relation=None)
    other = _row("other", "measured_direct", relation=None)
    child = _row("child", parents=["parent", "other"])
    child["values"]["derivation"]["inputs"] = ["parent"]
    result = _migrate(tmp_path, [parent, other, child])
    assert _child(result).evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert not result.validation.hard_issues


@pytest.mark.parametrize("method", ["calculated", "author_derived"])
@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_root_evidence_and_input_admission_remain_separate(tmp_path, method, status):
    parent = _row("parent", "measured_direct", relation=None)
    root = _row("root", method, ["parent"])
    root["values"]["admission_status"] = status
    child = _row("child", parents=["root"])
    result = _migrate(tmp_path, [child, root, parent])
    assert _child(result, "root").evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert _child(result, "root").admission.status.value == status
    assert _child(result).evidence.class_.is_unknown
    assert not result.validation.hard_issues


@pytest.mark.parametrize("method", ["directly_reduced_measurement", "authors_preferred_average_of_kems_derived_gammas"])
def test_conversion_only_reduced_ancestor_cannot_supply_author_lineage(tmp_path, method):
    parent = _row("parent", "measured_direct", relation=None)
    middle = _series(_row("middle", method, ["parent"], relation=None))
    middle_id = _child(_migrate(tmp_path / "ids", [parent, middle]), "middle").observation_id
    result = _migrate(tmp_path / "run", [parent, middle, _row("child", parents=[middle_id])])
    assert _child(result).evidence.class_.is_unknown
    assert not result.validation.hard_issues


@pytest.mark.parametrize("failure", [
    "no_lineage", "fake_parent", "partial_unresolved", "no_author_scalar", "no_author_series",
    "mixed_model", "input_model", "input_rejected", "input_not_declared", "recipe", "aimed",
    "located_recipe", "regime_without_lineage", "author_regime_without_lineage",
])
def test_invalid_author_reductions_stay_unknown(tmp_path, failure):
    parent = _row("parent", "measured_direct", relation=None)
    other = _row("other", "measured_direct", relation=None)
    child = _row("child", parents=["parent"])
    if failure == "no_lineage":
        child["values"].pop("derived_from")
    elif failure == "fake_parent":
        child["values"]["derived_from"] = ["fixture-source::missing"]
    elif failure == "partial_unresolved":
        child["values"]["derived_from"].append("nonresolving prose")
    elif failure.startswith("no_author"):
        child["values"].pop("derivation")
        if failure.endswith("series"):
            _series(child)
    elif failure in ("mixed_model", "input_model"):
        other["values"]["method_class"] = "derived"
        if failure == "mixed_model":
            child["values"]["derived_from"].append("other")
        else:
            child["values"]["derivation"]["inputs"] = ["other"]
    elif failure in ("input_rejected", "input_not_declared"):
        child["values"]["derivation"]["inputs"] = ["other"]
        if failure == "input_rejected":
            other["values"]["admission_status"] = "rejected"
    elif failure in ("recipe", "aimed"):
        transform = child["values"].pop("derivation")
        transform["relation"] = "calculated_from_printed_recipe_or_aimed_target"
        child["values"]["inference"] = transform
    elif failure == "located_recipe":
        child["values"].pop("derivation")
        _series(child)
        child["values"]["series"][0]["point_conditions"] = {
            "composition": {**_located({"amount_basis": "mole_fraction", "components": [["SiO2", "1"]]}),
                            "inference": {"relation": "calculated_from_printed_recipe_or_aimed_target",
                                          "inputs": ["printed recipe"], "output_unit": "mole_fraction"}}}
    else:
        child["regime"] = "author_derived" if failure.startswith("author") else "calculated"
        for key in ("method_class", "derived_from", "derivation"):
            child["values"].pop(key)
    assert _child(_migrate(tmp_path, [parent, other, child])).evidence.class_.is_unknown


@pytest.mark.parametrize("method,parent_status,series", [
    (method, status, series)
    for method in ("calculated", "author_derived", "directly_reduced_measurement",
                   "authors_preferred_average_of_kems_derived_gammas")
    for status in ("admitted", "pending", "rejected", "superseded", "model")
    for series in (False, True)
    if series or status != "admitted" or method in ("calculated", "author_derived")
])
def test_every_reduced_ancestor_must_pass_admission(tmp_path, method, parent_status, series):
    parent = _row("parent", "measured_direct", relation=None)
    if parent_status == "model":
        parent["values"]["method_class"] = "derived"
    else:
        parent["values"]["admission_status"] = parent_status
    middle = _row("middle", method, ["parent"])
    middle_id = "middle"
    if series:
        _series(middle)
        middle_id = _child(_migrate(tmp_path / "ids", [parent, middle]), "middle").observation_id
    child = _row("child", parents=[middle_id])
    result = _migrate(tmp_path / "run", [child, middle, parent])
    evidence = _child(result).evidence.class_
    if parent_status == "admitted":
        assert evidence.value is EvidenceClass.MEASURED_REDUCED
        assert not result.validation.hard_issues
    else:
        assert evidence.is_unknown


@pytest.mark.parametrize("method,admitted", [("measured_direct", True), ("measured_tabulated", False)])
def test_only_direct_measurements_terminate_reduction_ancestry(tmp_path, method, admitted):
    rejected = _row("rejected", "measured_direct", relation=None)
    rejected["values"]["admission_status"] = "rejected"
    parent = _row("parent", method, ["rejected"])
    result = _migrate(tmp_path, [rejected, parent, _row("child", parents=["parent"])])
    assert (_child(result).evidence.class_.value is EvidenceClass.MEASURED_REDUCED) == admitted


@pytest.mark.parametrize("shape", ["forward", "reverse", "diamond", "cycle"])
def test_lineage_graph_order_and_cycles(tmp_path, shape):
    rows = [_row("parent", "measured_direct", relation=None)]
    rows += [_row(f"c{i}", parents=["parent" if i == 0 else f"c{i-1}"]) for i in range(8)]
    rows.append(_row("child", parents=["c7", "c2"] if shape == "diamond" else ["c7"]))
    if shape == "cycle":
        rows[1]["values"]["derived_from"] = ["child"]
        rows[1]["values"]["derivation"]["inputs"] = ["child"]
    if shape != "forward":
        rows.reverse()
    result = _migrate(tmp_path, rows)
    for observation in result.observations.values():
        if observation.evidence.original_method_class != "calculated":
            continue
        if shape == "cycle":
            assert observation.evidence.class_.is_unknown
        else:
            assert observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    if shape == "cycle":
        assert {i.reason.value for i in result.validation.hard_issues} == {"cyclic_derivation"}
    else:
        assert not result.validation.hard_issues


@pytest.mark.parametrize("method,expected", [("derived", EvidenceClass.MODEL_DERIVED), ("method_only", None)])
@pytest.mark.parametrize("evaluator", [False, True])
def test_nonmeasured_method_routes(tmp_path, method, expected, evaluator):
    row = _row("child", method, relation=None)
    if evaluator:
        row["values"]["evaluator_family"] = "test_family"
        expected = EvidenceClass.COMPILATION_ASSESSED
    assert _child(_migrate(tmp_path, [row])).evidence.class_.value is expected


@pytest.mark.parametrize("field", ["status", "admission_status", "method_class"])
def test_typed_refusal_preserves_reason(tmp_path, field):
    row = _row("child", "method_only", relation=None)
    row["values"].pop("admission_status")
    row["values"].update({field: "typed_refusal", "reason": "no printed numeric value"})
    admission = _child(_migrate(tmp_path, [row])).admission
    assert admission.status is AdmissionStatus.REJECTED
    assert admission.reason == "no printed numeric value"


def _located(value):
    return {"state": {"tag": "value", "value": value}, "locator": {"page": 2, "table": "I"}}


def _typed(components):
    return _located({"basis": "printed_oxides", "amount_basis": "mass_percent", "components": components})


@pytest.mark.parametrize("field", ["initial_composition", "printed_composition"])
@pytest.mark.parametrize("components,valid", [
    ([["SiO2", "60"], ["MgO", "40"]], True),
    ([["SiO2", "60"], ["Cl", "40"]], False),
    ([["SiO2", "60"], ["FeOT", "40"]], False),
    ([["SiO2", "-1"], ["MgO", "101"]], False),
    ([["SiO2", "0"], ["MgO", "0"]], False),
])
@pytest.mark.parametrize("existing_unknown", [False, True])
def test_typed_sample_composition_survives_serialized_consumption(field, components, valid, existing_unknown):
    raw = {field: _typed(components), "mass_kg": _located({"kind": "point", "point": "0.001"})}
    if field == "printed_composition" and existing_unknown:
        raw["initial_composition"] = {"state": {"tag": "unknown", "reason": "not printed on a molar basis"}}
    sample = M._sample_from_plain(raw)
    sample = M._sample_from_plain(yaml.safe_load(yaml.safe_dump(M.to_plain(sample))))
    experiment = replace(F.tabulation_experiment(), sample=sample)
    assert (normalized_composition(experiment, None).selected is not None) == valid
    assert bool(charge_moles_by_species(experiment, None).by_species) == valid
    assert sample.printed_composition.state.is_value
    if not valid:
        assert sample.initial_composition.state.is_unknown
    elif sample.initial_composition.state.is_value:
        from simulator.battery.score import composition_wt_pct

        assert composition_wt_pct(sample.initial_composition.state.value) == pytest.approx({"SiO2": 60, "MgO": 40})


def _point_result(monkeypatch, conditions):
    source = M.REPO_ROOT / "data/literature/extracts/holzheid-1997-feo-nio-coo-activity-metal-saturated.yaml"
    document = M.load_yaml(source)
    row = deepcopy(document["species"]["CoO"]["observations"][0])
    document["species"] = {"CoO": {"observations": [row]}}
    row["values"]["series"] = row["values"]["series"][:1]
    if conditions is not None:
        row["values"]["series"][0]["point_conditions"] = conditions
    migrator = M.Migrator(M.REPO_ROOT)
    load = M.load_yaml
    with monkeypatch.context() as patch:
        patch.setattr(M, "load_yaml", lambda path: document if path == source else load(path))
        migrator._migrate_extract(source)
    observation = next(iter(migrator.result.observations.values()))
    experiment = migrator.result.experiments[observation.experiment_id]
    bench = migrator.result.benches[experiment.bench_id]
    def roundtrip(value, loader):
        return loader(yaml.safe_load(yaml.safe_dump(M.to_plain(value))))
    observation = roundtrip(observation, M.observation_from_plain)
    experiment = roundtrip(experiment, M.experiment_from_plain)
    bench = roundtrip(bench, M.bench_from_plain)
    return observation, engine_point_requests(collect_consumer_inputs(experiment, bench, observation))


def _conditions():
    return {"temperature_K": _located({"kind": "point", "point": "1773.15"}),
            "total_pressure_Pa": _located({"kind": "point", "point": "100000"}),
            "fO2_log": _located({"kind": "point", "point": "-8"}),
            "composition": _located({"amount_basis": "mole_fraction", "components": [["SiO2", "0.6"], ["MgO", "0.4"]]}),
            "printed_composition": _located({"SiO2": "60", "MgO": "40"})}


@pytest.mark.parametrize("component", ["MgO", "Cl", "FeOT"])
def test_row_printed_composition_engine_boundary(monkeypatch, component):
    conditions = _conditions()
    del conditions["composition"]
    conditions["printed_composition"] = _typed([["SiO2", "60"], [component, "40"]])
    _, requests = _point_result(monkeypatch, conditions)
    assert len(requests) == 8
    for request in requests:
        assert (request.payload is not None) == (component == "MgO")
        if component != "MgO":
            assert request.readiness.status.value == "gap"


@pytest.mark.parametrize("mode", ["unmodified", "point", "pressure_interval", "pressure_bound", "temperature_interval", "oxygen_bound", "approximate"])
def test_row_conditions_reach_all_engine_requests(monkeypatch, mode):
    conditions = _conditions()
    key = None
    if mode.endswith("interval"):
        key = "total_pressure_Pa" if mode.startswith("pressure") else "temperature_K"
        conditions[key] = _located({"kind": "interval", "interval_low": "10", "interval_high": "20"})
    elif mode.endswith("bound"):
        key = "total_pressure_Pa" if mode.startswith("pressure") else "fO2_log"
        conditions[key] = _located({"kind": "bound", "bound_operator": "<", "bound_value": "20"})
    elif mode == "approximate":
        conditions["total_pressure_Pa"]["state"]["value"]["approximate"] = True
    observation, requests = _point_result(monkeypatch, None if mode == "unmodified" else conditions)
    assert len(requests) == 8
    if key:
        assert observation.point_conditions[key].state.value.kind.value == mode.split("_")[-1]
    if mode == "approximate":
        assert observation.point_conditions["total_pressure_Pa"].state.value.approximate
    for request in requests:
        if mode in ("point", "approximate"):
            payload = M.to_plain(request.payload)
            assert float(payload["temperature_C"]) == 1500
            assert float(payload["pressure_bar"]) == 1
            assert float(payload["fO2_log"]) == -8
            assert {k: float(v) for k, v in payload["composition_mol"].items()} == {"SiO2": .6, "MgO": .4}
        else:
            assert request.payload is None
            assert request.readiness.status.value == "gap"


@pytest.mark.parametrize("unit,factor", [
    ("kg/m2", "1"), ("g/m2", ".001"), ("mg/m2", ".000001"),
    ("g/cm2", "10"), ("mg/cm2", ".01"), ("kg m^-2", "1"),
    ("g m^-2", ".001"), ("mg m^-2", ".000001"), ("g cm^-2", "10"),
    ("mg cm^-2", ".01"), ("mg/cm²", ".01"), ("mg/cm²; printed units", ".01"),
    ("mg/cm²/s", None), ("mg/m", None), ("kg", None), ("mg/cm3", None),
])
def test_areal_unit_routes_agree(unit, factor):
    converted, _ = M.convert_areal_mass_to_kg_per_m2("5.55184", unit)
    quantity, issue = M.map_quantity("mass_loss", {"quantity": "mass_loss_areal_density", "delta_q": "5.55184"}, unit, {})
    if factor is None:
        assert converted is None
        assert quantity.is_unknown and issue
    else:
        assert converted == Decimal("5.55184") * Decimal(factor)
        assert quantity.value is Quantity.MASS_LOSS_AREAL_DENSITY and issue is None


@pytest.mark.parametrize("source,recipes", [
    ("yam1983", 7), ("kems-118-yamamoto-1983", 10),
    ("deguzman-2026-simulant-physicochemical", 0), ("kems-020-hastie-1981-nbsir", 0),
    ("ntrs-19650014783", 0), ("kems-093-piacente-1975", 0),
])
def test_real_source_evidence_arms(source, recipes):
    path = M.REPO_ROOT / "data/literature/extracts" / f"{source}.yaml"
    migrator = M.Migrator(M.REPO_ROOT)
    migrator._migrate_extract(path)
    migrator.finalize()
    result = migrator.result
    transforms = [e.sample.initial_composition for e in result.experiments.values()
                  if e.sample.initial_composition and e.sample.initial_composition.inference
                  and e.sample.initial_composition.inference.relation == "calculated_from_printed_recipe_or_aimed_target"]
    assert len(transforms) == recipes
    assert all(not hasattr(c, "evidence") for c in transforms)
    for observation in result.observations.values():
        method = observation.evidence.original_method_class
        if method == "derived":
            assert observation.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        elif method in ("method_only", "author_derived"):
            assert observation.evidence.class_.is_unknown
    raw_rows = dict((row["observation_id"], row) for _, row in M.iter_extract_observations(M.load_yaml(path)))
    for observation in result.observations.values():
        if observation.admission.status is not AdmissionStatus.REJECTED:
            continue
        raw = raw_rows.get(observation.observation_id.removeprefix(f"{source}::"), {})
        values = raw.get("values", {})
        reason = values.get("refusal_reason") or values.get("reason") or raw.get("refusal_reason") or raw.get("reason")
        if reason:
            assert observation.admission.reason == reason


def test_real_sample_volume_and_unadopted_fugacity_are_preserved():
    from simulator.battery.identity import quantity_token

    for source in ("kems-120-ueshima-1984", "thomas-2022-chlorine-bonding-silicate-melts"):
        migrator = M.Migrator(M.REPO_ROOT)
        migrator._migrate_extract(M.REPO_ROOT / "data/literature/extracts" / f"{source}.yaml")
        migrator.finalize()
        result = migrator.result
        if source.startswith("kems"):
            volumes = [e.sample.volume_m3 for e in result.experiments.values() if e.sample.volume_m3]
            assert volumes
            for volume in volumes:
                assert volume.state.value.point == Decimal("2.5e-7")
                assert volume.locator is not None
                assert volume.inference.relation == "cm3_to_m3"
        else:
            assert len(result.observations) == 46
            assert not any(quantity_token(o.identity) is Quantity.FUGACITY for o in result.observations.values())
            rows = [o for o in result.observations.values() if "table2_experimental_conditions_and_xaf" in o.observation_id]
            assert len(rows) == 43
            assert all(o.value.kind.value == "unavailable" for o in rows)


def test_fugacity_storage_cannot_open_or_score_engine(monkeypatch):
    from simulator.battery.enums import Engine, Phase, SourceRelation
    from simulator.battery.records import Species, State
    from simulator.battery.score import SCORE_ENGINE_SET, ScoreContext, populate_numeric, predict_with_engine, score_store

    identity = M.fill_identity(Quantity.FUGACITY, Species("Cl2", Phase.G), temperature_K=State.of(Decimal("1200")))
    experiment = F.tabulation_experiment()
    reference = F.observation("fugacity-ref", experiment.experiment_id, identity, "33.7",
                              evidence=EvidenceClass.MEASURED_DIRECT, source_id="independent-experiment")
    context = ScoreContext(works={"work-1": F.work()}, experiments={experiment.experiment_id: experiment},
                           observations={reference.observation_id: reference})
    def no_engine(*args, **kwargs):
        pytest.fail("storage-only fugacity opened an engine")
    monkeypatch.setattr("simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine", no_engine)
    for engine in SCORE_ENGINE_SET:
        prediction = predict_with_engine(engine, reference)
        assert prediction.value is None
        assert prediction.refusal_detail["reason"] == "quantity_has_no_engine_pot"
    residuals, _ = score_store(context, engines=(Engine.MAGEMIN,), include_diagnostics=False)
    assert residuals and all(not r.score_eligible and r.numeric is None for r in residuals)
    numeric, reason, _ = populate_numeric(quantity=Quantity.FUGACITY, candidate=Decimal("33.7"),
                                           reference=Decimal("33.7"), source_relation=SourceRelation.INDEPENDENT)
    assert numeric is None
    assert reason.value == "metric_domain"


def test_geometry_context_keeps_original_source_record():
    source = M.REPO_ROOT / "data/literature/extracts/kems-029-yakovlev-shornikov-2011.yaml"
    raw = M.load_yaml(source)
    original = next(row for body in raw["species"].values() for row in body.get("context", [])
                    if row["observation_id"].endswith("kems_method_geometry"))
    migrator = M.Migrator(M.REPO_ROOT)
    migrator._migrate_extract(source)
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 5
    context = next(row for rows in result.context_by_work.values() for row in rows
                   if row["observation_id"] == original["observation_id"])
    assert context["values"] == original["values"]
    assert context["locator"] == original["locator"]


@pytest.mark.parametrize("source", [
    "boulliung-2025-mercury-volatile-metals-magmatic", "itoh-hino-banya-1998-spinel",
    "wimpenny-2019-zn-isotope-evaporation-extreme-t",
])
def test_ancestry_lookup_preserves_nonconditional_derivation_emission(source):
    migrator = M.Migrator(M.REPO_ROOT)
    migrator._migrate_extract(M.REPO_ROOT / "data/literature/extracts" / f"{source}.yaml")
    migrator.finalize()
    assert not any(issue.reason.value == "referential_integrity"
                   for issue in migrator.result.validation.hard_issues)
