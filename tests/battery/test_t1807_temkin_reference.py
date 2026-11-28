"""Regression pins for Distin's typed Temkin reference frame."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.battery.enums import (
    Authority,
    Engine,
    ExecutionState,
    Fo2Mode,
    Quantity,
    RefusalReason,
    ReferenceStateConvention,
)
from simulator.battery.generators.bench import (
    _ENGINE_REPORTED_ACTIVITY,
    _reference_matches,
)
from simulator.battery.migrate import load_yaml, reference_state_from_extract
from simulator.battery.records import Execution, StandardState, State
from simulator.battery.score import (
    EnginePrediction,
    SINGLE_CATION_COEFFICIENT_BASIS,
    _catalog_reference_identity_reason,
    _implied_alpha_coefficient_basis_matches,
    _predict_with_engine,
    fusion_comparison_reference,
)
from tests.battery.test_t1243_feo_reference import (
    _DISTIN,
    _conversion_notice,
    _feo_rows,
    _independent_factor,
    migration,
)
from tests.battery import factories as F


def test_distin_migrates_as_temkin_and_keeps_baseline_conversion_and_band(
    migration,
) -> None:
    """A Temkin→Raoultian mutation must fail before it can change scoring."""
    rows = tuple(row for row in _feo_rows(migration) if row.source_id == _DISTIN)
    assert len(rows) == 24
    assert all(
        row.identity.reference_state.value.convention
        is ReferenceStateConvention.TEMKIN
        for row in rows
    )
    row = next(
        item
        for item in rows
        if item.observation_id.endswith("distin1971_t1_1880_1_4")
    )
    standard = row.identity.reference_state.value
    assert isinstance(standard, StandardState)
    assert standard.convention is ReferenceStateConvention.TEMKIN

    converted = fusion_comparison_reference(row)
    notice = _conversion_notice(converted.notices)
    source, conversion = notice.reason.split("source_value=", 1)[1].split(
        "; converted_value=", 1
    )
    converted_value, _details = conversion.split("; factor=", 1)
    expected = Decimal(source) * _independent_factor(Decimal("2153.15"))
    assert Decimal(converted_value) == expected
    assert converted.value.point == expected
    assert (
        converted.identity.reference_state.value.convention
        is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
    )

    saturation = standard.iron_saturation_composition
    assert saturation is not None
    assert saturation.n_fe2_plus == Decimal(
        "0.9861097841165973149618546920"
    )
    assert saturation.band_dex == Decimal(
        "0.007436620909554859980899190015"
    )
    saturation_notice = next(
        item
        for item in converted.notices
        if item.reason.startswith("composition_derived_from_source_analyses")
    )
    assert saturation_notice.band == "0.007436620909554859980899190015 dex"


def test_structured_temkin_migrates_but_prose_remains_unknown() -> None:
    root = Path(__file__).resolve().parents[2]
    extract = load_yaml(
        root / "data/literature/extracts/distin-1971-ferrous-silicate-melts.yaml"
    )
    raw = extract["species"]["FeO"]["observations"][0]["standard_state"]
    structured = reference_state_from_extract(
        raw, species_formula="FeO", values={}
    )
    prose = reference_state_from_extract(
        "Temkin ideal ionic-solution activity of FeO(l)",
        species_formula="FeO",
        values={},
    )
    assert structured is not None and structured.is_value
    assert structured.value.convention is ReferenceStateConvention.TEMKIN
    assert prose is not None and prose.is_unknown
    assert "structured standard_state" in prose.reason


def test_unsupported_temkin_shape_is_refused_by_comparison_owner(migration) -> None:
    row = next(item for item in _feo_rows(migration) if item.source_id == _DISTIN)
    identity = row.identity
    standard = identity.reference_state.value
    unsupported = replace(
        standard,
        endmember=replace(standard.endmember, formula="FeO"),
        component_basis="FeO",
        iron_saturation_composition=None,
    )
    invalid_shape = replace(
        row,
        identity=replace(identity, reference_state=State.of(unsupported)),
    )
    result = fusion_comparison_reference(invalid_shape)
    notice = next(
        item
        for item in result.notices
        if "Temkin reference conversion requires" in item.reason
    )
    assert result.value.point == row.value.point
    assert notice.affected_quantities == (Quantity.ACTIVITY,)


def test_engine_request_preserves_and_rejects_temkin_coefficient_basis(
    monkeypatch,
) -> None:
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        EquilibrateCell,
    )
    from simulator.battery.generators.bench import GeneratedInput
    from simulator.battery.waypoints import ConsumerReadiness, ReadinessStatus
    import simulator.battery.score as score_module
    from tests.battery.test_melt_activity_requirements import _case

    composition = F.Composition(
        basis="ordered_complete_mole_inventory",
        components=(("FeO", Decimal("0.5")), ("SiO2", Decimal("0.5"))),
        amount_basis=F.AmountBasis.MOLE_FRACTION,
    )
    experiment, bench, row = _case(
        composition=composition,
        quantity=Quantity.ACTIVITY_COEFFICIENT,
        formula="FeO",
        basis="FeO",
        convention=ReferenceStateConvention.TEMKIN,
    )
    handle = EngineHandle(
        name=Engine.THERMOENGINE.value,
        backend=object(),
        available=True,
        unavailable_reason=None,
        takes_fo2=False,
        supports_intrinsic_fo2=True,
    )
    monkeypatch.setattr(battery, "open_battery_engine", lambda _name: handle)
    monkeypatch.setattr(
        score_module,
        "_activity_request_or_contract_refusal",
        lambda *_args, **_kwargs: (
            GeneratedInput(
                readiness=ConsumerReadiness(
                    consumer="melt_activity",
                    status=ReadinessStatus.READY,
                    gaps=(),
                    engine=Engine.THERMOENGINE.value,
                ),
                payload={"composition_mol": {"FeO": 0.5, "SiO2": 0.5}},
                provenance={},
            ),
            None,
        ),
    )

    def matching_engine_cell(engine_handle, pot, *, temperature_K, po2, **_kwargs):
        return EquilibrateCell(
            pot_id=pot.pot_id,
            engine=engine_handle.name,
            temperature_K=temperature_K,
            po2=po2,
            status="ok",
            refusal_reason=None,
            engine_status="ok",
            engine_reason=None,
            melt_activities={},
            gas_partial_pressures_Pa={},
            liquid_fraction=1.0,
            wall_s=0.0,
            cpu_s=0.0,
            hostname="test",
            melt_activity_coefficients={"FeO": 0.5},
            melt_activity_coefficient_details={
                "FeO": {
                    "coefficient_basis": SINGLE_CATION_COEFFICIENT_BASIS,
                    "standard_state": {
                        "convention": ReferenceStateConvention.TEMKIN.value,
                        "phase": "l",
                        "component_basis": "FeO",
                    },
                }
            },
        )

    monkeypatch.setattr(battery, "equilibrate_cell", matching_engine_cell)
    prediction = _predict_with_engine(
        Engine.THERMOENGINE,
        row,
        handles={Engine.THERMOENGINE.value: handle},
        experiment=experiment,
        bench=bench,
        fo2_mode=Fo2Mode.SHARED_LADDER,
    )
    # Engine dispatch must keep the requested source identity; its activity
    # coefficient cannot be silently compared just because it names the same
    # Temkin frame the request supplied.
    assert prediction.identity == row.identity
    assert prediction.refusal_reason is RefusalReason.COEFFICIENT_BASIS_MISMATCH


@pytest.mark.parametrize("consumer", ("fo2", "coefficient", "catalog", "bench"))
def test_temkin_rejection_arms_are_behavioral(migration, consumer: str) -> None:
    from simulator.battery.fo2_ladder import (
        metal_saturation_activity_refusal,
        resolve_row_fo2,
    )
    from simulator.battery.score import _CatalogScoreRow

    row = next(item for item in _feo_rows(migration) if item.source_id == _DISTIN)
    standard = row.identity.reference_state.value
    fo2_identity = F.activity_identity(
        formula="FeO",
        T_K=Decimal("2153.15"),
        component_basis="FeO",
        convention=ReferenceStateConvention.TEMKIN,
    )
    activity = F.observation(
        "temkin-fo2", "exp-feo", fo2_identity, Decimal("0.8")
    )
    if consumer == "fo2":
        experiment = migration.experiments[row.experiment_id]
        experiment = replace(
            experiment,
            sample=replace(
                experiment.sample,
                form=F.located("melt in equilibrium with liquid iron"),
            ),
        )
        activity = replace(
            activity,
            identity=replace(
                activity.identity,
                composition=State.unknown("no independent melt composition"),
                fO2_Pa=State.unknown("no independent fO2"),
                total_pressure_Pa=State.unknown("no row total pressure"),
            ),
        )
        assert (
            metal_saturation_activity_refusal(
                experiment, activity, Quantity.ACTIVITY
            )
            is None
        )
        # The shared ladder owns fO2 resolution now. A Temkin activity target
        # cannot supply its own fO2 through the retired saturation shortcut.
        assert (
            resolve_row_fo2(
                experiment,
                None,
                activity,
                quantity=Quantity.ACTIVITY,
            )
            is None
        )
        return
    if consumer == "bench":
        assert _reference_matches(
            standard, _ENGINE_REPORTED_ACTIVITY["openimcc"], "openimcc"
        ) == (False, _ENGINE_REPORTED_ACTIVITY["openimcc"].reported)
        return

    if consumer == "coefficient":
        prediction = EnginePrediction(
            engine=Engine.INTERNAL_ANALYTICAL,
            channel=Engine.INTERNAL_ANALYTICAL.value,
            execution=Execution(state=ExecutionState.PRODUCED),
            value=Decimal("1"),
            unit="dimensionless",
            authority=Authority.CERTIFIED,
            coefficient_sources=("test",),
            lineage_complete=True,
            identity=row.identity,
            coefficient_basis=SINGLE_CATION_COEFFICIENT_BASIS,
        )
        assert not _implied_alpha_coefficient_basis_matches(row, prediction)
        return

    declaration = SimpleNamespace(
        standard_state=SimpleNamespace(phase="l"), component_id="FeO"
    )
    catalog_row = _CatalogScoreRow(
        species_id="feo",
        formula="FeO",
        species=SimpleNamespace(source_reaction_activity=declaration),
        pure_phase_token="l",
        source_reaction=None,
        duplicate=False,
    )
    assert consumer == "catalog"
    assert (
        _catalog_reference_identity_reason(catalog_row, row.identity)
        == "catalog_reference_convention_mismatch"
    )
