"""Knudsen extent anchors and a pre-refactor Langmuir numeric pin."""

import sys
import math
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from simulator.battery.enums import (
    AdmissionStatus, AmountBasis, Authority, Engine, EvidenceClass, ExecutionState,
    ExperimentKind, MethodToken, NoticeKind, Phase, Quantity,
    RegimeClass, UncertaintyKind,
)
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import (
    Admission, Apparatus, ApparatusGeometry, Composition, Evidence, Experiment, FlowRegime, Located, Locator, Observation,
    PressureEnvironment, Sample, Species, State, ThermalSchedule, Uncertainty, Value,
)
from simulator.battery.residue import (
    ResidueChannel, ResidueInventoryRefusal, integrate_knudsen_residue_inventory,
    integrate_residue_inventory, predict_knudsen_residue_composition,
)


@pytest.mark.parametrize("oxygen_parent", (None, ""))
def test_langmuir_pressure_refactor_numeric_pin(oxygen_parent):
    result = integrate_residue_inventory(
        {"FeO": 1.0, "MgO": 2.0},
        (
            ResidueChannel("Fe", "Fe", "FeO", 1.0, "pin"),
            ResidueChannel("Mg", "Mg", "MgO", 1.0, "pin"),
            ResidueChannel("O2", "O2", oxygen_parent, 1.0, "pin"),
        ),
        lambda n, p: {
            "Fe": 5e-8 * 10 ** (-p / 2),
            "Mg": 2e-8 * 10 ** (-p / 2),
            "O2": 10**p * 1e5,
        },
        temperature_K=2000, duration_s=10, area_evolution_m2=(0.001,) * 3,
    )
    assert {k: v.hex() for k, v in result.residue_mol.items()} == {
        "FeO": "0x1.fffffcbbc23c7p-1", "MgO": "0x1.ffffff027bd22p+0",
    }
    assert {k: v.hex() for k, v in result.evaporated_mol.items()} == {
        "Fe": "0x1.a21ee1c66bbd4p-24", "Mg": "0x1.fb085bb1c9290p-25",
        "O2": "0x1.4fd187cfa828ep-24",
    }
    assert result.pO2_bar_by_step == (float.fromhex("0x1.36ad1d112f1c7p-28"),) * 3
    assert result.atom_closure_mol == {"Fe": 0.0, "Mg": 0.0, "O": 0.0}


def test_hashimoto_exhaustion_closure_refactor_numeric_pin():
    from simulator.battery.residue import _hashimoto_integrate_geometry
    result = _hashimoto_integrate_geometry(
        {"FeO": 6.84e-10, "MgO": 1.0},
        tuple(ResidueChannel(s, s, p, 1.0, "pin")
              for s, p in (("Fe", "FeO"), ("Mg", "MgO"), ("O2", None))),
        lambda n, p: {"Fe": 5e-8 * 10**(-p / 2) * n.get("FeO", 0),
                      "Mg": 2e-8 * 10**(-p / 2) * n.get("MgO", 0), "O2": 10**p * 1e5},
        temperature_K=2000, duration_s=1, steps=1,
        geometry_policy_id="sphere_constant", initial_area_m2=0.0001,
    )
    assert {k: v.hex() for k, v in result.inventory_mol.items()} == {
        "FeO": "0x0.0p+0", "MgO": "0x1.fffffff8fbf2dp-1"}
    assert {k: v.hex() for k, v in result.atom_closure.items()} == {
        "Fe": "0x0.0p+0", "O": "-0x1.29356b3000000p-55", "Mg": "0x0.0p+0"}
    assert result.exhaustion_notices[0]["remaining_moles"] == 6.84e-10


def _channels(gas="FeO"):
    return (ResidueChannel(gas, gas, "FeO", 1.0, "equilibrium effusion"),
            ResidueChannel("O2", "O2", None, 1.0, "equilibrium effusion"))


@pytest.mark.parametrize("pressure", (1e-9, 1e6))
def test_single_volatile_limit_is_exact_and_scale_free(pressure):
    result = integrate_knudsen_residue_inventory(
        {"FeO": 1.0}, _channels(),
        lambda n, p, t: {"FeO": pressure, "O2": 0.0},
        temperature_path=((0.25, 2000.0),), measured_mass_loss_fraction=0.25,
        near_saturated=True, source_fO2_log_by_interval=(-10.0,),
    )
    # One volatile only: a quarter of the original mass is a quarter mole.
    assert result.residue_mol["FeO"] == pytest.approx(0.75, abs=2e-14)
    assert result.evaporated_mol["FeO"] == pytest.approx(0.25, abs=2e-14)
    assert result.retained_oxygen_atoms_mol == pytest.approx(0.75)


def test_oxygen_only_loss_advances_xi_without_debiting_cations():
    result = integrate_knudsen_residue_inventory(
        {"FeO": 1.0}, _channels("Fe"),
        lambda n, p, t: {"Fe": 0.0, "O2": 1.0},
        temperature_path=((0.1, 2000.0),), measured_mass_loss_fraction=0.1,
        near_saturated=True, source_fO2_log_by_interval=(-10.0,),
    )
    # CIAAW: 1 mol FeO is 71.844 g. 10% loss as O2 is 7.1844 g,
    # or 0.22452653290830677 mol O2. No Fe atoms escape.
    assert result.evaporated_mol["O2"] == pytest.approx(0.22452653290830677)
    assert result.residue_mol == {"FeO": 1.0}
    assert result.retained_oxygen_atoms_mol == pytest.approx(0.5509469341833865)
    assert max(abs(v) for v in result.atom_closure_mol.values()) < 1e-13


def test_closure_loss_counts_fe_and_oxygen_mass():
    result = integrate_knudsen_residue_inventory(
        {"FeO": 1.0}, _channels("Fe"),
        lambda n, p, t: {"Fe": 1e-8 * 10**(-p / 2), "O2": 1e5 * 10**p},
        temperature_path=((0.25, 2000.0),), measured_mass_loss_fraction=0.25,
        near_saturated=True, source_fO2_log_by_interval=(None,),
    )
    # FeO -> Fe + 1/2 O2: 25% loss is 0.25 mol Fe, 0.125 mol O2.
    assert result.evaporated_mol["Fe"] == pytest.approx(0.25, abs=1e-12)
    assert result.evaporated_mol["O2"] == pytest.approx(0.125, abs=1e-12)
    assert result.residue_mol["FeO"] == pytest.approx(0.75, abs=1e-12)


def test_composition_dependent_high_loss_matches_independent_analytic_solution():
    channels = (*_channels(), ResidueChannel("MgO", "MgO", "MgO", 1.0, "effusion"))
    result = integrate_knudsen_residue_inventory(
        {"FeO": 1.0, "MgO": 1.0}, channels,
        lambda n, p, t: {"FeO": 5 * n.get("FeO", 0) * math.sqrt(0.071844),
                         "MgO": math.sqrt(0.040304), "O2": 0.0},
        temperature_path=((0.98, 2000.0),), measured_mass_loss_fraction=0.98,
        near_saturated=True, source_fO2_log_by_interval=(-10.0,),
    )
    # Independent integration in gas-packet coordinate s: nMgO=1-s,
    # nFeO=exp(-5s). At 98% total mass loss, solving
    # 71.844*exp(-5s)+40.304*(1-s)=0.02*(71.844+40.304)
    # gives s=.9590860932527849 and FeO=26.481341729689376 wt%.
    from simulator.battery.residue import _hashimoto_project_oxide_wt_pct
    assert _hashimoto_project_oxide_wt_pct(result.residue_mol)["FeO"] == pytest.approx(26.481341729689376, abs=1e-5)


@pytest.mark.parametrize("changes,reason", (
    ({"near_saturated": False}, "knudsen_near_saturation_invalid"),
    ({"temperature_path": None}, "printed_temperature_path_missing_or_invalid"),
    ({"measured_mass_loss_fraction": None}, "measured_mass_loss_fraction_missing_or_invalid"),
    ({"initial_inventory_mol": {}}, "initial_composition_invalid"),
))
def test_extent_inputs_refuse_typed(changes, reason):
    kwargs = dict(initial_inventory_mol={"FeO": 1.0}, channels=_channels(),
                  pressure_model=lambda n, p, t: {"FeO": 1.0, "O2": 0.0},
                  temperature_path=((0.25, 2000.0),), measured_mass_loss_fraction=0.25,
                  near_saturated=True, source_fO2_log_by_interval=(-10.0,))
    with pytest.raises(ResidueInventoryRefusal) as exc:
        integrate_knudsen_residue_inventory(**(kwargs | changes))
    assert exc.value.reason == reason


def test_source_condition_cannot_supply_unweighed_oxygen():
    with pytest.raises(ResidueInventoryRefusal, match="weighed charge") as exc:
        integrate_knudsen_residue_inventory(
            {"FeO": 1.0}, _channels("Fe"), lambda n, p, t: {"Fe": 0.0, "O2": 1.0},
            temperature_path=((0.5, 2000.0),), measured_mass_loss_fraction=0.5,
            near_saturated=True, source_fO2_log_by_interval=(-10.0,),
        )
    assert exc.value.reason == "knudsen_near_saturation_oxygen_exhausted"


@pytest.fixture
def extent_point(monkeypatch):
    import simulator.battery.residue as residue
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    composition = Composition("synthetic", (("FeO", Decimal("0.5")), ("MgO", Decimal("0.5"))), AmountBasis.MOLE_FRACTION)
    locator = Locator(table="2", published_page=510)
    reference = Observation(
        "residue", "experiment", Identity(
            Quantity.RESIDUE_COMPONENT_COMPOSITION, Species("FeO", Phase.L),
            subtype=State.of("oxide_wt_percent"), temperature_K=State.of(Decimal(2100)),
            composition=State.of(composition),
        ), Value.point_of(99), Uncertainty(UncertaintyKind.NONE),
        Evidence(State.of(EvidenceClass.MEASURED_DIRECT)),
        Admission(AdmissionStatus.ADMITTED, "synthetic anchor"), (),
        source_id="synthetic", locator=locator,
    )
    rows = tuple(replace(reference, observation_id=f"mass-{t}",
                         identity=replace(reference.identity, quantity=Quantity.MASS_LOSS_FRACTION,
                                          temperature_K=State.of(Decimal(t))), value=Value.point_of(x))
                 for t, x in ((2000, "0.1"), (2100, "0.2")))
    experiment = Experiment(
        "experiment", ExperimentKind.LITERATURE, State.of(MethodToken.KNUDSEN_EFFUSION),
        Sample(initial_composition=Located(State.of(composition), locator)),
        {"temperature_K": Located(State.unknown("point temperatures are printed per row"), locator)},
        PressureEnvironment(
            Located(State.unknown("not published")), Located(State.unknown("no sweep")),
            FlowRegime(State.unknown("not published")),
        ),
        thermal_schedule=ThermalSchedule(method=Located(State.of("70 K steps, 15 minutes each"), locator)),
    )
    monkeypatch.setitem(sys.modules, "openimcc", SimpleNamespace(load_gas_datapack=lambda: object()))
    monkeypatch.setattr(battery, "_openimcc_gas_channels_and_omission_notices",
                        lambda *a: ([("Fe", ("FeO", 1, 0.5)), ("O2", (None, 1, -1))], ()))
    # Synthetic law, exact oxygen-balanced packets at every temperature.
    monkeypatch.setattr(residue, "_make_residue_pressure_model", lambda *a, **k:
                        lambda n, p: {"Fe": 1e-8 * 10**(-p / 2), "O2": 1e5 * 10**p})
    return reference, experiment, rows


def test_each_row_uses_own_extent_and_never_measured_composition(extent_point):
    reference, experiment, rows = extent_point
    result = predict_knudsen_residue_composition("internal-analytical", reference, experiment, rows)
    earlier = replace(reference, identity=replace(reference.identity, temperature_K=State.of(Decimal(2000))))
    first = predict_knudsen_residue_composition("internal-analytical", earlier, experiment, rows)
    changed = replace(reference, value=Value.point_of(1), point_conditions={"composition": Located(State.unknown("must not read"))})
    same = predict_knudsen_residue_composition("internal-analytical", changed, experiment, rows)
    assert result["escaped_mass_fraction"] == pytest.approx(0.2)
    assert first["escaped_mass_fraction"] == pytest.approx(0.1)
    assert first["composition_wt_pct"] != result["composition_wt_pct"]
    assert same["composition_wt_pct"] == result["composition_wt_pct"]
    assert result["linear_temperature_diagnostic"]["difference_from_step_hold_wt_pct"] == pytest.approx({"FeO": 0, "MgO": 0})
    reasons = [n.reason for n in result["notices"]]
    assert any("SC-360" in r and "3 dex (W) / 2.5 dex (Mo)" in r and "pO2^-nu" in r for r in reasons)
    assert any("printed step temperatures held" in r for r in reasons)
    assert {r["source"] for r in result["oxygen_sources"]} == {"d142_closure"}


@pytest.mark.parametrize("sample_has_p2o5", (False, True))
def test_openimcc_sp_knudsen_screening_uses_sample_initial_composition(
    extent_point, monkeypatch, sample_has_p2o5
):
    from simulator.battery.score import _knudsen_residue_prediction

    reference, experiment, rows = extent_point

    def composition(with_p2o5):
        components = (
            ("FeO", Decimal("0.45" if with_p2o5 else "0.5")),
            ("MgO", Decimal("0.45" if with_p2o5 else "0.5")),
        )
        if with_p2o5:
            components += (("P2O5", Decimal("0.1")),)
        return Composition(
            "ordered_complete_mole_inventory", components,
            AmountBasis.MOLE_FRACTION,
        )

    initial = composition(sample_has_p2o5)
    experiment = replace(
        experiment,
        sample=replace(
            experiment.sample,
            initial_composition=Located(State.of(initial), reference.locator),
        ),
    )
    reference = replace(
        reference,
        identity=replace(
            reference.identity,
            composition=State.of(composition(not sample_has_p2o5)),
        ),
    )
    context = SimpleNamespace(
        experiments={experiment.experiment_id: experiment},
        observations={row.observation_id: row for row in rows},
        benches={},
    )

    prediction = _knudsen_residue_prediction(
        context, reference, Engine.OPENIMCC_SP
    )

    failure = (
        f"refusal_reason={prediction.refusal_reason}; "
        f"refusal_detail={prediction.refusal_detail}"
    )
    assert prediction.execution.state is ExecutionState.PRODUCED, failure
    consumed = prediction.requested_composition
    assert consumed is not None and consumed.is_value, failure
    assert consumed.value.amount_basis is AmountBasis.MOL_INVENTORY, failure
    consumed_has_p2o5 = any(
        name == "P2O5" and amount > 0
        for name, amount in consumed.value.components
    )
    assert consumed_has_p2o5 is sample_has_p2o5, failure
    p2o5_notices = tuple(
        notice
        for notice in prediction.notices
        if notice.reason.startswith("P2O5 parent reference state:")
    )
    assert bool(p2o5_notices) is sample_has_p2o5, failure
    if sample_has_p2o5:
        assert prediction.authority is Authority.EXTRAPOLATED, failure


def test_openimcc_sp_unknown_consumed_residue_inventory_does_not_fall_back(
    extent_point, monkeypatch
):
    import simulator.battery.residue as residue
    from simulator.battery.score import _knudsen_residue_prediction

    reference, experiment, _rows = extent_point
    p2o5_composition = Composition(
        "ordered_complete_mole_inventory",
        (("FeO", Decimal("0.45")), ("MgO", Decimal("0.45")),
         ("P2O5", Decimal("0.1"))),
        AmountBasis.MOLE_FRACTION,
    )
    experiment = replace(
        experiment,
        sample=replace(
            experiment.sample,
            initial_composition=Located(State.of(p2o5_composition), reference.locator),
        ),
    )
    reference = replace(
        reference,
        identity=replace(
            reference.identity, composition=State.of(p2o5_composition)
        ),
    )
    monkeypatch.setattr(
        residue,
        "predict_knudsen_residue_composition",
        lambda *_args, **_kwargs: {
            "composition_wt_pct": {"FeO": 25.0},
            "notices": (),
            "engine_flags": (),
            "engine_metadata": {},
        },
    )
    context = SimpleNamespace(
        experiments={experiment.experiment_id: experiment},
        observations={},
        benches={},
    )

    prediction = _knudsen_residue_prediction(
        context, reference, Engine.OPENIMCC_SP
    )

    assert prediction.execution.state is ExecutionState.PRODUCED
    assert prediction.requested_composition is not None
    assert prediction.requested_composition.is_unknown
    assert any(
        notice.kind is NoticeKind.INPUT_OMITTED
        and notice.reason == "P2O5 screening input not recorded"
        for notice in prediction.notices
    )
    assert not any(
        notice.reason.startswith("P2O5 parent reference state:")
        for notice in prediction.notices
    )


@pytest.mark.parametrize("sample_has_p2o5", (False, True))
def test_openimcc_sp_sossi_screening_uses_consumed_host_and_trace_inventory(
    sample_has_p2o5, monkeypatch,
):
    """Screen the scorer with one real run because it normally integrates all 43.

    The wrapper narrows only the Sossi prediction boundary. The selected run still
    uses its recorded host and measured Mn/Ti additions through the real inventory
    builder, OpenIMCC prediction, and integrator.
    """
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    import simulator.battery.score as score
    import simulator.battery.residue as residue

    source_id = "kems-012-sossi-2019"
    context = score.load_score_context(sources=(source_id,))
    rows = sorted(
        (
            row
            for row in context.observations.values()
            if row.source_id == source_id
            and quantity_token(row.identity) is Quantity.RESIDUE_COMPONENT_COMPOSITION
            and row.identity.species.formula in {"Mn", "Ti"}
        ),
        key=lambda row: row.experiment_id,
    )
    assert rows
    source_path = score.REPO_ROOT / "data/literature/extracts" / f"{source_id}.yaml"
    original_load_yaml = score.load_yaml
    source = original_load_yaml(source_path)
    source_experiments = {
        str(item.get("experiment_id")): item
        for item in source.get("experiments", ())
        if isinstance(item, dict)
    }
    selected_run_id = "sossi-2019-run-2m-15-07-16c"
    assert selected_run_id in source_experiments
    reference = next(
        row
        for row in rows
        if row.experiment_id.rsplit("::", 1)[-1] == selected_run_id
    )
    selected_experiment = source_experiments[selected_run_id]
    printed = selected_experiment["sample"]["printed_composition"]["state"]["value"]
    if sample_has_p2o5:
        parent = next(name for name in printed if name != "P2O5")
        prior_p2o5 = float(printed.pop("P2O5", 0.0))
        # Keep the injected amount below the 1 wt% projection threshold.
        injected_p2o5_wt_pct = 0.1
        printed[parent] = (
            float(printed[parent]) - injected_p2o5_wt_pct + prior_p2o5
        )
        printed["P2O5"] = injected_p2o5_wt_pct

    def load_modified_source(path):
        if str(path) == str(source_path):
            return source
        return original_load_yaml(path)

    monkeypatch.setattr(score, "load_yaml", load_modified_source)

    original_predict_cohort = residue._predict_sossi_residue_cohort

    def predict_reference_run_only(experiments, runtime_catalog, **kwargs):
        reference_experiment = next(
            experiment
            for experiment in experiments
            if experiment["experiment_id"] == selected_run_id
        )
        run_kwargs = dict(kwargs)
        for key in (
            "starting_trace_ppm_by_experiment",
            "buffered_fO2_log_by_experiment",
        ):
            run_kwargs[key] = {selected_run_id: kwargs[key][selected_run_id]}
        return original_predict_cohort(
            [reference_experiment], runtime_catalog, **run_kwargs
        )

    monkeypatch.setattr(
        residue, "_predict_sossi_residue_cohort", predict_reference_run_only
    )

    def identity_with_p2o5(reference, present):
        components = (
            (("MnO", Decimal("0.9")), ("P2O5", Decimal("0.1")))
            if present
            else (("MnO", Decimal("1")),)
        )
        return replace(
            reference,
            identity=replace(
                reference.identity,
                composition=State.of(
                    Composition(
                        "ordered_complete_mole_inventory",
                        components,
                        AmountBasis.MOLE_FRACTION,
                    )
                ),
            ),
        )

    reference = identity_with_p2o5(reference, not sample_has_p2o5)
    integrator_initial_inventories: list[dict[str, float]] = []
    original_integrator = residue.integrate_residue_inventory

    def forward_integrator(initial_inventory_mol, *args, **kwargs):
        if not integrator_initial_inventories:
            integrator_initial_inventories.append(dict(initial_inventory_mol))
        return original_integrator(initial_inventory_mol, *args, **kwargs)

    monkeypatch.setattr(
        residue, "integrate_residue_inventory", forward_integrator
    )
    prediction = score._sossi_residue_prediction(
        context, reference, Engine.OPENIMCC_SP, {}
    )

    failure = (
        f"refusal_reason={prediction.refusal_reason}; "
        f"refusal_detail={prediction.refusal_detail}"
    )
    assert prediction.execution.state is ExecutionState.PRODUCED, failure
    consumed = prediction.requested_composition
    assert consumed is not None and consumed.is_value, failure
    assert consumed.value.amount_basis is AmountBasis.MOL_INVENTORY, failure
    consumed_components = dict(consumed.value.components)
    assert integrator_initial_inventories, failure
    integrator_initial_inventory = integrator_initial_inventories[0]
    assert {"MnO", "TiO2"} <= set(integrator_initial_inventory), failure
    assert set(consumed_components) == set(integrator_initial_inventory), failure
    for oxide, initial_moles in integrator_initial_inventory.items():
        assert float(consumed_components[oxide]) == pytest.approx(
            initial_moles, rel=1e-12, abs=1e-30
        ), (oxide, failure)
    consumed_has_p2o5 = consumed_components.get("P2O5", 0.0) > 0.0
    assert consumed_has_p2o5 is sample_has_p2o5, failure
    assert any(
        notice.reason.startswith("P2O5 parent reference state:")
        for notice in prediction.notices
    ) is sample_has_p2o5, failure


def test_printed_oxygen_precedes_selector(extent_point, monkeypatch):
    from simulator.battery import source_oxygen
    reference, experiment, rows = extent_point
    pressure = Located(State.of(Value.point_of("0.00001")), reference.locator)
    reference = replace(reference, point_conditions={"fO2_Pa": pressure})
    rows = tuple(replace(row, point_conditions={"fO2_Pa": pressure}) for row in rows)
    def must_not_resolve(*args):
        raise AssertionError("printed condition must bypass selector")
    monkeypatch.setattr(source_oxygen, "resolve_source_oxygen", must_not_resolve)
    result = predict_knudsen_residue_composition("internal-analytical", reference, experiment, rows)
    assert {r["source"] for r in result["oxygen_sources"]} == {"source_printed"}
    assert not any("SC-360" in n.reason for n in result["notices"])


def test_cell_ratio_precedes_flagged_closure(extent_point, monkeypatch):
    from simulator.battery import source_oxygen
    from simulator.battery.records import Derivation
    reference, experiment, rows = extent_point
    oxygen = replace(reference, value=Value.point_of("0.00001"),
                     derivation=Derivation("source_oxygen_condition; cell-metal activity as assumed by the source", (), (), "Pa"))
    monkeypatch.setattr(source_oxygen, "resolve_source_oxygen",
                        lambda *a: source_oxygen.SourceOxygenCondition(oxygen, frozenset({"cell-ratio"})))
    result = predict_knudsen_residue_composition("internal-analytical", reference, experiment, rows)
    assert {r["source"] for r in result["oxygen_sources"]} == {"cell_material_gas_ratio"}
    assert not any("SC-360" in n.reason for n in result["notices"])


def test_step_hold_and_linear_are_distinct_temperature_paths():
    seen = []
    def pressure(n, p, t):
        seen.append(t)
        return {"FeO": 1.0, "O2": 0.0}
    kwargs = dict(temperature_path=((0.1, 2000), (0.2, 2100)),
                  measured_mass_loss_fraction=0.2, near_saturated=True,
                  source_fO2_log_by_interval=(-10.0, -10.0))
    integrate_knudsen_residue_inventory({"FeO": 1.0}, _channels(), pressure, **kwargs)
    assert set(seen) == {2000, 2100}
    seen.clear()
    integrate_knudsen_residue_inventory({"FeO": 1.0}, _channels(), pressure, **kwargs, linear_temperature=True)
    assert min(seen) == 2000
    assert any(2000 < t < 2100 for t in seen)


def test_extent_comparison_preserves_ordinary_identity_guards(extent_point):
    from simulator.battery.enums import IdentityEqualKind
    from simulator.battery.identity import identity_equal, _same_row_knudsen_residue_identity_equal
    reference, _, _ = extent_point
    identity = reference.identity
    assert identity_equal(identity, identity).kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert _same_row_knudsen_residue_identity_equal(identity, identity).kind is IdentityEqualKind.EQUAL
    for field in ("composition", "temperature_K"):
        missing = replace(identity, **{field: State.unknown("missing")})
        assert _same_row_knudsen_residue_identity_equal(missing, missing).kind is IdentityEqualKind.IDENTITY_UNKNOWN
    changed = replace(identity, species=Species("MgO", Phase.L))
    assert _same_row_knudsen_residue_identity_equal(identity, changed).kind is IdentityEqualKind.IDENTITY_MISMATCH
    changed = replace(identity, temperature_K=State.of(Decimal(2200)))
    assert _same_row_knudsen_residue_identity_equal(identity, changed).kind is IdentityEqualKind.IDENTITY_MISMATCH
    left = replace(identity, fO2_Pa=State.of(Decimal("1e-5")))
    right = replace(identity, fO2_Pa=State.of(Decimal("1e-6")))
    assert _same_row_knudsen_residue_identity_equal(left, right).kind is IdentityEqualKind.IDENTITY_MISMATCH


def test_compile_residual_produces_extent_numeric_without_time_mass_or_pressure(extent_point):
    from simulator.battery.score import ScoreContext, compile_residual
    reference, experiment, rows = extent_point
    context = ScoreContext({}, {experiment.experiment_id: experiment},
                           {r.observation_id: r for r in (reference, *rows)})
    residual, candidate = compile_residual(reference, Engine.INTERNAL_ANALYTICAL, context=context)
    assert candidate is not None
    assert residual.numeric is not None, residual.refusal
    assert residual.numeric.operation.value == "absolute"
    assert any("SC-360" in n.reason for n in residual.notices)


@pytest.mark.parametrize("change,reason", (
    ("no_path", "printed_temperature_path_missing_or_invalid"),
    ("no_extent", "measured_mass_loss_fraction_missing_or_invalid"),
    ("no_initial", "initial_composition_not_stated"),
    ("nonmolecular", "knudsen_near_saturation_invalid"),
    ("bad_orifice_kn", "knudsen_near_saturation_invalid"),
    ("bad_printed_pressure", "knudsen_near_saturation_invalid"),
))
def test_prediction_boundary_refuses_missing_inputs(extent_point, change, reason):
    reference, experiment, rows = extent_point
    if change == "no_path":
        experiment = replace(experiment, thermal_schedule=None)
    elif change == "no_extent":
        rows = rows[:1]
    elif change == "no_initial":
        experiment = replace(experiment, sample=replace(experiment.sample, initial_composition=None))
    elif change == "nonmolecular":
        experiment = replace(experiment, pressure_environment=replace(
            experiment.pressure_environment, regime=FlowRegime(State.of(RegimeClass.VISCOUS))))
    elif change == "bad_orifice_kn":
        experiment = replace(experiment, pressure_environment=replace(
            experiment.pressure_environment, regime=FlowRegime(
                State.unknown("no class"), Located(State.of(Value.point_of(1)), reference.locator))))
    else:
        experiment = replace(experiment, apparatus=Apparatus(geometry=ApparatusGeometry(
            orifice_diameter_m=Located(State.of(Value.point_of("0.001")), reference.locator))))
        reference = replace(reference, point_conditions={
            "total_pressure_Pa": Located(State.of(Value.point_of(10)), reference.locator)})
    with pytest.raises(ResidueInventoryRefusal) as exc:
        predict_knudsen_residue_composition("internal-analytical", reference, experiment, rows)
    assert exc.value.reason == reason


def test_printed_oxygen_is_identical_on_shared_trajectory_prefix(extent_point):
    reference, experiment, rows = extent_point
    earlier = replace(reference, observation_id="earlier-residue",
                      identity=replace(reference.identity, temperature_K=State.of(Decimal(2000))),
                      point_conditions={"fO2_Pa": Located(State.of(Value.point_of("0.00001")), reference.locator)})
    observations = (*rows, earlier, reference)
    first = predict_knudsen_residue_composition("internal-analytical", earlier, experiment, observations)
    later = predict_knudsen_residue_composition("internal-analytical", reference, experiment, observations)
    assert first["oxygen_sources"][0] == later["oxygen_sources"][0]
    assert later["oxygen_sources"][0]["source"] == "source_printed"
    assert "earlier-residue" in later["oxygen_sources"][0]["used_observations"]


def test_conflicting_printed_interval_oxygen_is_flagged(extent_point):
    reference, experiment, rows = extent_point
    conflict = replace(reference, observation_id="other-column",
                       point_conditions={"fO2_Pa": Located(State.of(Value.point_of("0.0001")), reference.locator)})
    reference = replace(reference, point_conditions={"fO2_Pa": Located(State.of(Value.point_of("0.00001")), reference.locator)})
    result = predict_knudsen_residue_composition("internal-analytical", reference, experiment, (*rows, conflict))
    assert result["oxygen_sources"][-1]["source"] == "d142_closure"
    assert any("several_candidates_survive" in n.reason for n in result["notices"])


@pytest.mark.parametrize("engine_failure", (False, True))
def test_post_selection_refusal_preserves_oxygen_caveat_and_extent(extent_point, monkeypatch, engine_failure):
    import simulator.battery.residue as residue
    from simulator.battery.residue import ResidueEngineNonconvergence
    from simulator.battery.score import ScoreContext, _knudsen_residue_prediction, compile_residual
    reference, experiment, rows = extent_point
    def fail(*args):
        if engine_failure:
            raise ResidueEngineNonconvergence("internal-analytical", "after oxygen selection")
        raise ResidueInventoryRefusal("residue_pressure_channels_missing", "original channel detail")
    monkeypatch.setattr(residue, "_make_residue_pressure_model", lambda *a, **k: fail)
    context = ScoreContext({}, {experiment.experiment_id: experiment},
                           {r.observation_id: r for r in (*rows, reference)})
    prediction = _knudsen_residue_prediction(context, reference, Engine.INTERNAL_ANALYTICAL)
    detail = prediction.refusal_detail
    assert detail["reason"] == ("engine_nonconvergence" if engine_failure else "residue_pressure_channels_missing")
    assert detail["measured_mass_loss_fraction"] == 0.2
    assert detail["temperature_path"] == [(0.1, 2000), (0.2, 2100)]
    assert all(r["source"] == "d142_closure" for r in detail["oxygen_sources"])
    assert any("SC-360" in n.reason for n in prediction.notices)
    residual, _ = compile_residual(reference, Engine.INTERNAL_ANALYTICAL, context=context, prediction=prediction)
    assert residual.refusal is not None
    assert any("SC-360" in n.reason for n in residual.notices)


def _real_analytical_cmas_pressure(temperature_K, inventory=None, extra_channels=()):
    openimcc = pytest.importorskip("openimcc")
    from simulator.battery.residue import _make_residue_pressure_model
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    pack = openimcc.load_gas_datapack()
    if inventory is None:
        inventory = {"SiO2": 0.44, "Al2O3": 0.05, "FeO": 0.08, "MgO": 0.36, "CaO": 0.07}
    channels, omissions = battery._openimcc_gas_channels_and_omission_notices(tuple(inventory), pack)
    assert not omissions
    channels = tuple(channels) + tuple(extra_channels)
    pressure_model = _make_residue_pressure_model(
        "internal-analytical", channels, pack, temperature_K=temperature_K, total_pressure_Pa=0,
        engine_metadata={}, engine_flags=set(), missing_channels_reason="residue_pressure_channels_missing",
        caller="Knudsen regression",
    )
    return inventory, channels, pressure_model


def test_internal_analytical_pressure_callback_covers_real_channels_and_oxygen():
    from pathlib import Path
    from simulator.chemistry.offgas_fo2 import _oxygen_atom_partial_pressure_bar
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.physical_constants import PA_PER_BAR
    inventory, channels, pressure_model = _real_analytical_cmas_pressure(2000)
    predicted = pressure_model(inventory, -10)
    assert set(predicted) == {str(s) for s, _ in channels}
    response = battery._internal_analytical_vapor_pressure_adapter(
        core=battery._new_internal_analytical_core(), temperature_C=1726.85,
        pressure_bar=0, composition_kg=None, composition_mol=inventory, fO2_log=-10,
        po2_request=battery.Po2Request(mode=battery.PO2_COMMANDED, po2_bar=1e-10),
        include_diagnostic_shadows=False,
    )
    # Expected names come from the existing species resolver, not a test alias table.
    for channel, value in response.vapor_pressures_Pa.items():
        formula = battery._gas_formula_for_oxygen_balance(channel)
        if formula in predicted:
            assert predicted[formula] == pytest.approx(value)
    assert predicted["O2"] == pytest.approx(1e-5)
    assert predicted["O"] == pytest.approx(_oxygen_atom_partial_pressure_bar(
        2000, 1e-10, extract_path=Path(__file__).resolve().parents[2] / "data/literature/extracts/nasa-cea-thermo.yaml",
    ) * PA_PER_BAR)


@pytest.mark.parametrize("failure_kind", ("domain", "extract"))
def test_oxygen_projection_failure_has_typed_pressure_reason(monkeypatch, failure_kind):
    from simulator.battery.residue import _make_residue_pressure_model
    from simulator.chemistry import offgas_fo2
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.vapour_rail.nasa_cea import NasaCeaError
    monkeypatch.setattr(battery, "_new_internal_analytical_core", lambda: object())
    monkeypatch.setattr(battery, "_internal_analytical_vapor_pressure_adapter",
                        lambda **kwargs: SimpleNamespace(vapor_pressures_Pa={}))
    def fail(*args, **kwargs):
        raise (NasaCeaError("CEA domain") if failure_kind == "domain" else OSError("CEA extract"))
    monkeypatch.setattr(offgas_fo2, "_oxygen_atom_partial_pressure_bar", fail)
    pressure = _make_residue_pressure_model(
        "internal-analytical", [("O", (None, 1, -0.5)), ("O2", (None, 1, -1))], None,
        temperature_K=2000, total_pressure_Pa=0, engine_metadata={}, engine_flags=set(),
        missing_channels_reason="residue_pressure_channels_missing", caller="oxygen failure regression",
    )
    with pytest.raises(ResidueInventoryRefusal) as exc:
        pressure({"FeO": 1.0}, -10)
    assert exc.value.reason == "residue_oxygen_pressure_unavailable"


@pytest.mark.parametrize("oxygen_parent", (None, ""))
def test_closure_continues_after_parent_exhaustion_with_atom_and_mass_closure(oxygen_parent):
    from simulator.accounting.formulas import parse_formula
    channels = tuple(ResidueChannel(s, s, p, 1.0, "stoichiometric packets")
                     for s, p in (("Fe", "FeO"), ("Mg", "MgO"),
                                  ("O", oxygen_parent), ("O2", oxygen_parent)))
    def pressure(n, p, temperature):
        return {"Fe": 1e-8 * 10**(-p / 2) if n.get("FeO", 0) > 0 else 0.0,
                "Mg": 1e-10 * 10**(-p / 2) if n.get("MgO", 0) > 0 else 0.0,
                "O": 1e-6 * 10**(p / 2), "O2": 1e5 * 10**p}
    initial = {"FeO": 0.01, "MgO": 1.0}
    result = integrate_knudsen_residue_inventory(
        initial, channels, pressure, temperature_path=((0.2, 2000),),
        measured_mass_loss_fraction=0.2, near_saturated=True, source_fO2_log_by_interval=(None,),
    )
    # FeO is exhausted. The remaining 80% of initial mass is entirely MgO:
    # .8*(.01*.071844+.040304)/.040304 = .8142604208019056 mol.
    assert result.residue_mol["FeO"] == 0.0
    assert result.evaporated_mol["O"] > 0.0
    assert result.evaporated_mol["O2"] > 0.0
    assert result.residue_mol["MgO"] == pytest.approx(0.8142604208019056, abs=1e-10)
    assert result.retained_oxygen_atoms_mol == pytest.approx(0.8142604208019056, abs=1e-10)
    assert max(abs(v) for v in result.atom_closure_mol.values()) < 1e-12
    def mass(amounts):
        return sum(n * float(parse_formula(s).molar_mass_kg_per_mol()) for s, n in amounts.items())
    assert mass(result.evaporated_mol) / mass(initial) == pytest.approx(0.2, abs=1e-10)


@pytest.mark.parametrize("pressures,reason", (
    ({"O2": 0.0}, "channel_missing"),
    ({"FeO": float("nan"), "O2": 0.0}, "residue_pressure_invalid"),
    ({"FeO": -1.0, "O2": 0.0}, "residue_pressure_invalid"),
))
def test_active_parent_pressure_is_still_required_and_validated(pressures, reason):
    with pytest.raises(ResidueInventoryRefusal) as exc:
        integrate_knudsen_residue_inventory(
            {"FeO": 1.0}, _channels(), lambda *args: pressures,
            temperature_path=((0.2, 2000),), measured_mass_loss_fraction=0.2,
            near_saturated=True, source_fO2_log_by_interval=(-10,),
        )
    assert exc.value.reason == reason


def test_real_analytical_closure_and_extent_preserve_exponent_contract_refusal():
    from simulator.battery.residue import _channel_terms, _residue_oxygen_balance
    inventory, gas_channels, pressure = _real_analytical_cmas_pressure(1905.15)
    channels = tuple(ResidueChannel(s, s, parent, 1.0, "real analytical closure")
                     for s, (parent, _, _) in gas_channels)
    with pytest.raises(ResidueInventoryRefusal) as root:
        _residue_oxygen_balance(_channel_terms(channels), inventory,
                                lambda p: pressure(inventory, p), 1905.15)
    assert root.value.reason == "residue_pressure_law_exponent_mismatch"
    assert "'Fe': declared -0.5, observed " in root.value.detail
    with pytest.raises(ResidueInventoryRefusal) as extent:
        integrate_knudsen_residue_inventory(
            inventory, channels, lambda n, p, temperature: pressure(n, p),
            temperature_path=((0.01, 1905.15),), measured_mass_loss_fraction=0.01,
            near_saturated=True, source_fO2_log_by_interval=(None,),
        )
    assert extent.value.reason == root.value.reason
    assert extent.value.detail == root.value.detail


@pytest.mark.parametrize("oxygen_parent", (None, ""))
@pytest.mark.parametrize("oxygen_species", ("O", "O2"))
@pytest.mark.parametrize("invalid", (None, float("nan"), -1.0))
def test_parentless_oxygen_pressure_is_required_and_validated(oxygen_parent, oxygen_species, invalid):
    channels = (ResidueChannel("FeO", "FeO", "FeO", 1.0, "pressure guard"),
                ResidueChannel("O", "O", oxygen_parent, 1.0, "pressure guard"),
                ResidueChannel("O2", "O2", oxygen_parent, 1.0, "pressure guard"))
    pressures = {"FeO": 1.0, "O": 0.0, "O2": 0.0}
    if invalid is None:
        del pressures[oxygen_species]
    else:
        pressures[oxygen_species] = invalid
    with pytest.raises(ResidueInventoryRefusal) as exc:
        integrate_knudsen_residue_inventory(
            {"FeO": 1.0}, channels, lambda *args: pressures,
            temperature_path=((0.05, 2000),), measured_mass_loss_fraction=0.05,
            near_saturated=True, source_fO2_log_by_interval=(-10,),
        )
    assert exc.value.reason == ("channel_missing" if invalid is None else "residue_pressure_invalid")
    assert exc.value.detail == oxygen_species


@pytest.mark.parametrize("temperature_K,extent,wt", (
    (1748.15, 0.0146, {"SiO2": 44.02, "Al2O3": 4.56, "FeO": 8.04, "MgO": 36.44, "CaO": 6.95}),
    (1749.15, 0.0117, {"SiO2": 52.32, "Al2O3": 19.13, "FeO": 9.37, "MgO": 6.25, "CaO": 12.93}),
))
def test_real_markova_first_interval_zeroes_omitted_al_and_keeps_exponent_refusal(temperature_K, extent, wt):
    """Al is no longer a missing channel. The iron pressure-law refusal remains."""
    from simulator.accounting.formulas import parse_formula
    from simulator.battery.residue import _channel_terms, _residue_oxygen_balance
    inventory = {s: w / 100 / float(parse_formula(s).molar_mass_kg_per_mol()) for s, w in wt.items()}
    inventory, gas_channels, pressure = _real_analytical_cmas_pressure(temperature_K, inventory)
    channels = tuple(ResidueChannel(s, s, parent, 1.0, "printed Markova first interval")
                     for s, (parent, _, _) in gas_channels)
    with pytest.raises(ResidueInventoryRefusal) as root:
        _residue_oxygen_balance(_channel_terms(channels), inventory,
                                lambda p: pressure(inventory, p), temperature_K)
    assert root.value.reason == "residue_pressure_law_exponent_mismatch"
    assert root.value.detail.startswith(
        "pressure law exponent mismatch for 'Fe': declared -0.5, observed "
    )
    al_notices = [
        notice for notice in pressure.below_threshold_omission_notices
        if notice.reason.startswith("Al ")
    ]
    assert len(al_notices) == 1
    assert "below_reporting_threshold_omissions" in al_notices[0].reason
    with pytest.raises(ResidueInventoryRefusal) as result:
        integrate_knudsen_residue_inventory(
            inventory, channels, lambda n, p, t: pressure(n, p),
            temperature_path=((extent, temperature_K),), measured_mass_loss_fraction=extent,
            near_saturated=True, source_fO2_log_by_interval=(None,),
        )
    assert result.value.reason == root.value.reason
    assert result.value.detail == root.value.detail


@pytest.mark.parametrize("temperature_K,wt", (
    (1748.15, {"SiO2": 44.02, "Al2O3": 4.56, "FeO": 8.04, "MgO": 36.44, "CaO": 6.95}),
    (1749.15, {"SiO2": 52.32, "Al2O3": 19.13, "FeO": 9.37, "MgO": 6.25, "CaO": 12.93}),
))
def test_markova_bracket_top_callback_zeroes_recorded_al_omission(temperature_K, wt):
    """A recorded below-threshold Al pressure is zero, and the notice cites that proof."""
    from simulator.accounting.formulas import parse_formula
    from simulator.battery.enums import NoticeKind
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    inventory = {s: w / 100 / float(parse_formula(s).molar_mass_kg_per_mol()) for s, w in wt.items()}
    inventory, _channels, pressure = _real_analytical_cmas_pressure(temperature_K, inventory)
    predicted = pressure(inventory, 0.0)
    response = battery._internal_analytical_vapor_pressure_adapter(
        core=battery._new_internal_analytical_core(),
        temperature_C=temperature_K - 273.15,
        pressure_bar=0.0,
        composition_kg=None,
        composition_mol=inventory,
        fO2_log=0.0,
        po2_request=battery.Po2Request(mode=battery.PO2_COMMANDED, po2_bar=1.0),
        include_diagnostic_shadows=False,
    )
    omissions = response.diagnostics["vapor_pressure_diagnostic"]["below_reporting_threshold_omissions"]
    record = omissions["Al"]
    assert "Al" not in response.vapor_pressures_Pa
    assert record["species"] == "Al"
    assert record["computed_pressure_Pa"] <= record["reporting_threshold_Pa"]
    assert predicted["Al"] == 0.0
    translated = pressure.below_threshold_omission_records["Al"]
    assert translated["computed_pressure_Pa"] == record["computed_pressure_Pa"]
    assert translated["reporting_threshold_Pa"] == record["reporting_threshold_Pa"]
    assert translated["field"] == record["field"]
    assert translated["pressure_rail"] == record["pressure_rail"]
    assert translated["provenance"] == (
        "BuiltinVaporPressureProvider.below_reporting_threshold_omissions"
    )
    notices = [
        notice for notice in pressure.below_threshold_omission_notices
        if notice.reason.startswith("Al ")
    ]
    assert len(notices) == 1
    notice = notices[0]
    assert notice.kind is NoticeKind.INPUT_OMITTED
    assert f"{record['reporting_threshold_Pa']!r}" in notice.reason
    assert f"{record['computed_pressure_Pa']!r}" in notice.reason
    assert record["field"] in notice.reason
    assert record["pressure_rail"] in notice.reason
    assert "BuiltinVaporPressureProvider.below_reporting_threshold_omissions" in notice.reason


def test_real_callback_refuses_channel_the_provider_did_not_omit():
    """A required key with no omission record is still a missing channel."""
    from simulator.accounting.formulas import parse_formula
    wt = {"SiO2": 44.02, "Al2O3": 4.56, "FeO": 8.04, "MgO": 36.44, "CaO": 6.95}
    inventory = {s: w / 100 / float(parse_formula(s).molar_mass_kg_per_mol()) for s, w in wt.items()}
    inventory, _channels, pressure = _real_analytical_cmas_pressure(
        1748.15, inventory, extra_channels=(("Zr", (None, 1, 0)),),
    )
    with pytest.raises(ResidueInventoryRefusal) as exc:
        pressure(inventory, 0.0)
    assert exc.value.reason == "residue_pressure_channels_missing"
    assert exc.value.detail == "Zr"
    assert "Zr" not in pressure.below_threshold_omission_records
    assert "Al" in pressure.below_threshold_omission_records


def test_analytical_callback_at_2000k_returns_finite_al():
    """Passing real callback: Al is reported, so there is no omission record."""
    inventory, _channels, pressure = _real_analytical_cmas_pressure(2000)
    predicted = pressure(inventory, -10)
    assert "Al" in predicted
    assert math.isfinite(predicted["Al"])
    assert predicted["Al"] > 0.0
    assert pressure.below_threshold_omission_records == {}
    assert pressure.below_threshold_omission_notices == []
