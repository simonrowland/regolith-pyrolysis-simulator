"""t-1598 ideal-gas volume fractions and t-1597 wall-deposit areal rates."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP

from simulator.accounting.formulas import resolve_species_formula
from simulator.battery.composition import (
    ideal_gas_volume_amount_to_mole_fraction,
    ideal_gas_volume_scale,
)
from simulator.battery.enums import (
    AmountBasis,
    Engine,
    EvidenceClass,
    ExecutionState,
    MetricOperation,
    NoticeKind,
    PerBasis,
    Phase,
    Quantity,
    ResidualStatus,
)
from simulator.battery.identity import Identity, SweepIdentity, WallIdentity
from simulator.battery.migrate import (
    PARTICULATE_CARRYOVER_OUT_OF_MODEL_SCOPE,
    _composition_located_from_plain,
    _mass_percent_components,
    _mole_percent_components,
    convert_areal_rate_to_kg_per_m2_s,
    migrate,
)
from simulator.battery.records import Composition, Located, Species, State
from simulator.battery.score import (
    ScoreContext,
    compile_residual,
    predict_with_engine,
    refusal_census,
    score_store,
)
from tests.battery.factories import observation
from tests.battery.test_migrate import _write_min_tree

_NOTICE = NoticeKind.IDEAL_GAS_VOLUME_FRACTION_AS_MOLE_FRACTION


def _four_sig_micro(amount: Decimal) -> Decimal:
    """Four significant figures on a 1e-6-scale flux."""

    return amount.quantize(Decimal("1e-9"), rounding=ROUND_HALF_UP)


def test_areal_rate_unit_pin_uses_the_printed_milligram() -> None:
    """0.38 mg/cm2 h is 0.38/360000 kg m^-2 s^-1, 1.056e-6 at 4 figures."""

    exact = Decimal("0.38") / Decimal(360000)
    independent = Decimal("0.38") * Decimal("1e-6") / (
        Decimal("1e-4") * Decimal(3600)
    )
    converted, trail = convert_areal_rate_to_kg_per_m2_s(
        Decimal("0.38"), "mg/cm2·hr as printed"
    )
    assert trail == "mg_per_cm2_h_to_kg_per_m2_s"
    assert converted == exact
    assert converted == independent
    assert _four_sig_micro(converted) == Decimal("1.056e-6")

    high, _trail = convert_areal_rate_to_kg_per_m2_s("1.60", "mg/cm2/h")
    assert high == Decimal("1.60") / Decimal(360000)
    assert _four_sig_micro(high) == Decimal("4.444e-6")

    sulfate, sulfate_trail = convert_areal_rate_to_kg_per_m2_s(
        "2.9", "mg/m2·s as printed"
    )
    assert sulfate_trail == "mg_per_m2_s_to_kg_per_m2_s"
    assert sulfate == Decimal("2.9") * Decimal("1e-6")
    oxide, _oxide_trail = convert_areal_rate_to_kg_per_m2_s(
        Decimal("3.0"), "mg/m2/s"
    )
    assert oxide == Decimal("3.0") * Decimal("1e-6")


def test_ideal_gas_volume_scale_is_not_a_new_amount_basis() -> None:
    assert ideal_gas_volume_scale("vol-%") == "percent"
    assert ideal_gas_volume_scale("volume-ppm") == "ppm"
    assert ideal_gas_volume_scale("ppm") is None
    assert ideal_gas_volume_scale("molepercent") is None
    assert not hasattr(AmountBasis, "VOLUME_PERCENT")
    assert ideal_gas_volume_amount_to_mole_fraction(
        Decimal("15.2"), "vol-%"
    ) == Decimal("0.152")
    assert ideal_gas_volume_amount_to_mole_fraction(
        Decimal("20"), "volume-ppm"
    ) == Decimal("2e-5")


def _gas_i() -> dict[str, object]:
    return {
        "amount_basis": "mole_percent",
        "phase": "gas",
        "components": [
            ["O2", "4.5", "vol-%"],
            ["N2", "72.7", "vol-%"],
            ["H2O", "13.0", "vol-%"],
            ["CO2", "9.0", "vol-%"],
            ["Ar", "0.8", "vol-%"],
            ["Na", "125", "volume-ppm"],
            ["S", "240", "volume-ppm"],
            ["Cl", "62.5", "volume-ppm"],
        ],
    }


def test_gas_i_volume_map_becomes_mole_fraction_with_notice() -> None:
    located = _composition_located_from_plain(_gas_i())
    assert located.state.is_value
    composition = located.state.value
    assert composition.amount_basis is AmountBasis.MOLE_FRACTION
    assert composition.as_map() == {
        "O2": Decimal("4.5") / Decimal("100"),
        "N2": Decimal("72.7") / Decimal("100"),
        "H2O": Decimal("13.0") / Decimal("100"),
        "CO2": Decimal("9.0") / Decimal("100"),
        "Ar": Decimal("0.8") / Decimal("100"),
        "Na": Decimal("125") * Decimal("1e-6"),
        "S": Decimal("240") * Decimal("1e-6"),
        "Cl": Decimal("62.5") * Decimal("1e-6"),
    }
    assert located.inference is not None
    assert located.inference.relation == _NOTICE.value
    assert located.inference.inputs == ("vol-%", "volume-ppm")


def test_condensed_volume_fraction_stays_unknown() -> None:
    """SiO2/Al2O3 vol% is not an ideal-gas mole fraction without a gas marker."""

    payload = {
        "amount_basis": "mole_percent",
        "components": [["SiO2", "60", "vol-%"], ["Al2O3", "40", "vol-%"]],
    }
    for marked in (payload, {**payload, "phase": "cr"}):
        located = _composition_located_from_plain(marked)
        assert located.state.is_unknown
        assert located.state.reason == (
            "condensed-phase volume fraction is not an ideal-gas mole fraction"
        )
        assert (
            located.inference is None
            or located.inference.relation != _NOTICE.value
        )
    gas = _composition_located_from_plain(
        {
            "amount_basis": "mole_percent",
            "phase": "G",
            "components": [["O2", "100", "vol-%"]],
        }
    )
    assert gas.state.is_value
    assert gas.state.value.components == (("O2", Decimal("1")),)
    assert gas.inference is not None
    assert gas.inference.relation == _NOTICE.value


def test_plain_mole_percent_gases_have_no_volume_notice() -> None:
    located = _composition_located_from_plain(
        {
            "amount_basis": "mole_percent",
            "basis": "printed_mole_percent",
            "components": [["O2", "21"], ["N2", "79"]],
        }
    )
    assert located.state.is_value
    assert located.state.value.components == (
        ("O2", Decimal("0.21")),
        ("N2", Decimal("0.79")),
    )
    assert located.inference is None or located.inference.relation != _NOTICE.value


def test_existing_percent_component_pins_stay() -> None:
    unknown = {
        "amount_basis": "mole_percent",
        "components": [["unknown-component", "100"]],
    }
    assert _mole_percent_components(unknown) is None
    assert _mole_percent_components({**unknown, "amount_basis": "mass_percent"}) is None
    assert _mass_percent_components(
        {
            "amount_basis": "mass_percent",
            "components": [["unknown-component", "100"]],
        }
    ) is None
    oxides = _composition_located_from_plain(
        {
            "amount_basis": "mole_percent",
            "components": [["SiO2", "75"], ["MgO", "25"]],
        }
    )
    assert oxides.state.is_value
    assert oxides.state.value.components == (
        ("SiO2", Decimal("0.75")),
        ("MgO", Decimal("0.25")),
    )
    open_map = _composition_located_from_plain(
        {
            "amount_basis": "mole_percent",
            "components": [["SiO2", "90"]],
        }
    )
    assert open_map.state.is_unknown
    assert open_map.state.reason == (
        "mole-percent composition does not close to 100 (sum=90)"
    )
    rejected = _composition_located_from_plain(
        {
            "amount_basis": "mole_percent",
            "components": [["unknown-component", "10", "vol-%"]],
        }
    )
    assert rejected.state.is_unknown
    assert "unknown-component" in (rejected.state.reason or "")


def _extract() -> dict[str, object]:
    gas = _gas_i()
    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {"citation": "Fixture, A. (2026), Test Journal 1:1"},
        "experiments": [{"experiment_id": "run"}],
        "species": {
            "Na2SO4": {
                "observations": [
                    {
                        "observation_id": "dew",
                        "experiment": "run",
                        "phase": "cr",
                        "units": "K",
                        "values": {
                            "quantity": "transition_temperature",
                            "property_kind": "dew_point",
                            "method_class": "measured_direct",
                            "points": [
                                {
                                    "T_K": "1240",
                                    "point_conditions": {"composition": gas},
                                }
                            ],
                        },
                    },
                    {
                        "observation_id": "rate",
                        "experiment": "run",
                        "phase": "cr",
                        "units": "mg/cm2·hr as printed",
                        "values": {
                            "quantity": "surface_deposition_areal_mass_rate",
                            "method_class": "measured_direct",
                            "points": [
                                {
                                    "value": "0.38",
                                    "point_conditions": {"composition": gas},
                                },
                                {
                                    "value": "1.60",
                                    "point_conditions": {"composition": gas},
                                },
                            ],
                        },
                    },
                    {
                        "observation_id": "carry",
                        "experiment": "run",
                        "phase": "cr",
                        "units": "g/24 h as printed",
                        "values": {
                            "quantity": "water_cooled_paddle_carryover_mass_per_24h",
                            "method_class": "measured_direct",
                            "value": "3.3",
                        },
                    },
                ]
            },
            "K2SO4": {
                "observations": [
                    {
                        "observation_id": "k-rate",
                        "experiment": "run",
                        "phase": "cr",
                        "units": "mg/m2·s as printed",
                        "values": {
                            "quantity": "surface_deposition_areal_mass_rate",
                            "method_class": "measured_direct",
                            "points": [
                                {
                                    "value": "2.9",
                                    "point_conditions": {"composition": gas},
                                }
                            ],
                        },
                    }
                ]
            },
            "PbO": {
                "observations": [
                    {
                        "observation_id": "pb-rate",
                        "experiment": "run",
                        "phase": "cr",
                        "units": "mg/m2·s as printed",
                        "values": {
                            "quantity": "surface_deposition_areal_mass_rate",
                            "method_class": "measured_direct",
                            "points": [
                                {
                                    "value": "3.0",
                                    "point_conditions": {"composition": gas},
                                }
                            ],
                        },
                    }
                ]
            },
        },
    }


def _one(rows: list, label: str):
    assert len(rows) == 1, (label, [row.observation_id for row in rows])
    return rows[0]


def test_synthetic_beerkens_rows_bind_identity_and_quantity(tmp_path) -> None:
    result = migrate(_write_min_tree(tmp_path, _extract()), write=False)
    observations = list(result.observations.values())
    dew = _one(
        [
            row
            for row in observations
            if row.identity.quantity.is_value
            and row.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
        ],
        "dew",
    )
    assert dew.identity.subtype.value == "dew_point"
    assert dew.identity.composition.is_value
    assert dew.identity.composition.value.as_map()["O2"] == Decimal("0.045")
    assert dew.identity.composition.value.as_map()["Na"] == Decimal("0.000125")
    assert any(
        notice.kind is _NOTICE and notice.reason == "vol-%; volume-ppm"
        for notice in dew.notices
    )

    rates = [
        row
        for row in observations
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.WALL_DEPOSIT_AREAL_RATE
    ]
    by_species = {}
    for row in rates:
        by_species.setdefault(row.identity.species.formula, []).append(row)
    sodium = sorted(by_species["Na2SO4"], key=lambda row: row.value.point)
    assert [row.value.point for row in sodium] == [
        Decimal("0.38") / Decimal(360000),
        Decimal("1.60") / Decimal(360000),
    ]
    assert sodium[0].identity.per.value is PerBasis.AREA_TIME
    assert sodium[0].identity.composition.is_value
    assert any(notice.kind is _NOTICE for notice in sodium[0].notices)
    potassium = _one(by_species["K2SO4"], "K2SO4")
    lead = _one(by_species["PbO"], "PbO")
    assert potassium.value.point == Decimal("2.9") * Decimal("1e-6")
    assert lead.value.point == Decimal("3.0") * Decimal("1e-6")

    carry = _one(
        [row for row in observations if row.observation_id.endswith("::carry")],
        "carry",
    )
    assert carry.identity.quantity.is_unknown
    assert carry.identity.quantity.reason == PARTICULATE_CARRYOVER_OUT_OF_MODEL_SCOPE
    assert carry.identity.quantity.value is None


def test_predictor_refusals_are_counted_not_dropped(tmp_path) -> None:
    result = migrate(_write_min_tree(tmp_path, _extract()), write=False)
    context = ScoreContext(
        works=result.works,
        experiments=result.experiments,
        observations=result.observations,
    )
    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        include_diagnostics=False,
    )
    scored = {residual.reference for residual in residuals}
    assert {row.observation_id for row in result.observations.values()} <= scored
    assert refusal_census(residuals) == {
        "unsupported:antoine_data_unavailable": 5,
        "unsupported:particulate_carryover_out_of_model_scope": 1,
    }
    dew = next(
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
    )
    dew_residual = next(
        residual for residual in residuals if residual.reference == dew.observation_id
    )
    assert dew_residual.refusal.detail["species"] == ("Na2SO4", "NaOH")


def _areal_observation(
    *,
    formula: str = "Na2SO4",
    gas_temperature_K: str = "1373",
    wall_temperature_K: str = "900",
    pressure_Pa: str = "101325",
    fraction: str = "0.0001",
    carrier: str = "Ar",
    diameter: str | None = "0.05",
    alpha: str | None = "0.2",
    observation_id: str = "flux",
):
    rest = Decimal("1") - Decimal(fraction)
    composition = Composition(
        basis="mole_fraction",
        components=((formula, Decimal(fraction)), (carrier, rest)),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = Identity(
        quantity=Quantity.WALL_DEPOSIT_AREAL_RATE,
        species=Species(formula, Phase.CR),
        temperature_K=State.of(Decimal(gas_temperature_K)),
        per=State.of(PerBasis.AREA_TIME),
        composition=State.of(composition),
        total_pressure_Pa=State.of(Decimal(pressure_Pa)),
        sweep_gas=State.of(
            SweepIdentity(
                species=carrier,
                flow_sccm=State.not_applicable("unprinted flow"),
                partial_pressure_Pa=State.not_applicable("unprinted partial pressure"),
            )
        ),
        wall=State.of(
            WallIdentity(
                temperature_K=State.of(Decimal(wall_temperature_K)),
                material=State.of("alumina"),
            )
        ),
    )
    built = observation(
        observation_id,
        "run",
        identity,
        "1",
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    conditions = dict(built.point_conditions or {})
    if diameter is not None:
        conditions["pipe_diameter_m"] = Located(State.of(Decimal(diameter)))
    if alpha is not None:
        conditions["sticking_coefficient"] = Located(State.of(Decimal(alpha)))
    return replace(built, point_conditions=conditions)


def _admit_all(monkeypatch) -> None:
    monkeypatch.setattr(
        "simulator.condensation._condensation_admission_refusal",
        lambda _species, **_kwargs: None,
    )


def test_areal_rate_calls_the_existing_wall_flux(monkeypatch) -> None:
    """The flux call carries the wall-model contract, not the helper defaults."""

    from simulator.condensation import (
        _knudsen_number,
        _knudsen_regime_factor,
        _reactive_product_backstop_authorized,
        _species_condensation_temperature_C,
        reactive_product_backstop_enabled,
    )
    from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET

    seen: dict[str, object] = {}

    def _flux(species, p_local, t_surface, alpha, **kwargs):
        seen["species"] = species
        seen["p_local"] = p_local
        seen["t_surface"] = t_surface
        seen["alpha"] = alpha
        seen["kwargs"] = kwargs
        return 2.0

    _admit_all(monkeypatch)
    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        _flux,
    )
    gas_T = Decimal("1373")
    wall_T = Decimal("900")
    pressure = Decimal("101325")
    fraction = Decimal("0.0001")
    diameter = Decimal("0.05")
    carrier = "Ar"
    built = _areal_observation(
        gas_temperature_K=str(gas_T),
        wall_temperature_K=str(wall_T),
        pressure_Pa=str(pressure),
        fraction=str(fraction),
        carrier=carrier,
        diameter=str(diameter),
    )
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, built)
    kwargs = seen["kwargs"]
    assert isinstance(kwargs, dict)
    expected_regime = _knudsen_regime_factor(
        _knudsen_number(
            float(pressure),
            float(gas_T),
            float(diameter),
            carrier_gas=carrier,
        )
    )
    if _reactive_product_backstop_authorized("Na2SO4"):
        expected_backstop = reactive_product_backstop_enabled(
            "Na2SO4",
            float(wall_T) - float(CELSIUS_TO_KELVIN_OFFSET),
            _species_condensation_temperature_C("Na2SO4"),
        )
    else:
        expected_backstop = False
    molar_mass = resolve_species_formula("Na2SO4").molar_mass_kg_per_mol()
    assert seen["species"] == "Na2SO4"
    assert seen["p_local"] == float(fraction * pressure)
    assert seen["t_surface"] == float(wall_T)
    assert seen["t_surface"] != float(gas_T)
    assert kwargs["T_gas_K"] == float(gas_T)
    assert kwargs["overhead_pressure_pa"] == float(pressure)
    assert kwargs["carrier_gas"] == carrier
    assert kwargs["regime_factor"] == expected_regime
    assert kwargs["reactive_product_backstop"] is expected_backstop
    assert prediction.execution.state is ExecutionState.PRODUCED
    assert prediction.value == Decimal("2.0") * Decimal(str(molar_mass))
    assert prediction.refusal_reason is None
    assert prediction.notices == ()


def test_undersaturated_wall_is_zero_not_a_negative_deposit(monkeypatch) -> None:
    """p_bulk < p_sat predicts 0 with the evaporation notice. Equality does not."""

    def _flux(*_args, diagnostic_out=None, **_kwargs):
        if isinstance(diagnostic_out, dict):
            diagnostic_out["wall_saturation_pressure_pa"] = 2000.0
        return -3.0

    _admit_all(monkeypatch)
    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        _flux,
    )
    fraction = Decimal("0.01")
    pressure = Decimal("100000")
    built = _areal_observation(
        pressure_Pa=str(pressure),
        fraction=str(fraction),
        observation_id="undersaturated",
    )
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, built)
    p_bulk = fraction * pressure
    assert prediction.execution.state is ExecutionState.PRODUCED
    assert prediction.refusal_reason is None
    assert prediction.value == Decimal(0)
    assert prediction.value >= 0
    assert len(prediction.notices) == 1
    notice = prediction.notices[0]
    assert notice.kind is NoticeKind.UNDERSATURATED_WALL_NET_EVAPORATION
    assert notice.reason == f"p_bulk_Pa={p_bulk}; p_sat_T_wall_Pa={Decimal('2000.0')}"

    def _equilibrium(*_args, diagnostic_out=None, **_kwargs):
        if isinstance(diagnostic_out, dict):
            diagnostic_out["wall_saturation_pressure_pa"] = float(p_bulk)
        return 0.0

    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        _equilibrium,
    )
    equilibrium = predict_with_engine(Engine.INTERNAL_ANALYTICAL, built)
    assert equilibrium.value == Decimal(0)
    assert equilibrium.notices == ()
    assert equilibrium.refusal_reason is None


def test_undersaturated_zero_is_scored(tmp_path, monkeypatch) -> None:
    """The zero reaches the residual metric and is not a typed refusal."""

    result = migrate(_write_min_tree(tmp_path, _extract()), write=False)
    context = ScoreContext(
        works=result.works,
        experiments=result.experiments,
        observations=result.observations,
    )
    row = next(
        item
        for item in result.observations.values()
        if item.identity.quantity.is_value
        and item.identity.quantity.value is Quantity.WALL_DEPOSIT_AREAL_RATE
        and item.identity.species is not None
        and item.identity.species.formula == "Na2SO4"
        and item.value.point == Decimal("0.38") / Decimal(360000)
    )
    fraction = Decimal("0.01")
    pressure = Decimal("100000")
    identity = replace(
        row.identity,
        # A crystal with no printed polymorph is identity-unknown before any
        # metric. The gas-phase species keeps this row on the numeric path.
        species=Species("Na2SO4", Phase.G),
        temperature_K=State.of(Decimal("1373")),
        total_pressure_Pa=State.of(pressure),
        composition=State.of(
            Composition(
                basis="mole_fraction",
                components=(("Na2SO4", fraction), ("Ar", Decimal("0.99"))),
                amount_basis=AmountBasis.MOLE_FRACTION,
            )
        ),
        sweep_gas=State.of(
            SweepIdentity(
                species="Ar",
                flow_sccm=State.of(Decimal("10")),
                partial_pressure_Pa=State.of(pressure),
            )
        ),
        wall=State.of(
            WallIdentity(
                temperature_K=State.of(Decimal("900")),
                material=State.of("alumina"),
                area_m2=State.of(Decimal("0.02")),
                location=State.of("duct"),
            )
        ),
    )
    row = replace(
        row,
        identity=identity,
        point_conditions={
            **(row.point_conditions or {}),
            "pipe_diameter_m": Located(State.of(Decimal("0.05"))),
            "sticking_coefficient": Located(State.of(Decimal("0.2"))),
        },
    )

    def _flux(*_args, diagnostic_out=None, **_kwargs):
        if isinstance(diagnostic_out, dict):
            diagnostic_out["wall_saturation_pressure_pa"] = 2000.0
        return -3.0

    _admit_all(monkeypatch)
    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        _flux,
    )
    residual, _candidate = compile_residual(
        row, Engine.INTERNAL_ANALYTICAL, context=context
    )
    assert residual.refusal is None
    assert residual.status is not ResidualStatus.REFUSED
    assert residual.numeric is not None
    assert residual.numeric.operation is MetricOperation.RELATIVE
    assert residual.numeric.value == Decimal(-1)
    assert "not_flagged_stratum" not in residual.exclusions
    notices = [
        notice
        for notice in residual.notices
        if notice.kind is NoticeKind.UNDERSATURATED_WALL_NET_EVAPORATION
    ]
    assert len(notices) == 1
    assert notices[0].reason == (
        f"p_bulk_Pa={fraction * pressure}; p_sat_T_wall_Pa={Decimal('2000.0')}"
    )


def test_deposition_input_refusal_is_separate_from_saturation(monkeypatch) -> None:
    from simulator.condensation import (
        DepositionInputRefusal,
        WallSaturationPressureRefusal,
    )

    def _bad_input(*_args, **_kwargs):
        raise DepositionInputRefusal("pipe_diameter_m", 0.0, "must be > 0")

    _admit_all(monkeypatch)
    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        _bad_input,
    )
    built = _areal_observation(observation_id="bad-diameter")
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, built)
    assert prediction.refusal_detail["reason"] == "deposition_input_refused"
    assert prediction.refusal_detail["parameter"] == "pipe_diameter_m"
    assert prediction.refusal_detail["reason"] != "wall_saturation_pressure_refused"

    def _unsaturated(*_args, **_kwargs):
        raise WallSaturationPressureRefusal("Na2SO4", 900.0, "antoine_data_unavailable")

    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        _unsaturated,
    )
    saturation = predict_with_engine(Engine.INTERNAL_ANALYTICAL, built)
    assert saturation.refusal_detail["reason"] == "antoine_data_unavailable"
    assert saturation.refusal_detail["reason"] != "deposition_input_refused"


def test_missing_pipe_diameter_refuses_before_the_flux(monkeypatch) -> None:
    def _flux(*_args: object, **_kwargs: object) -> float:
        raise AssertionError("flux called without a printed pipe diameter")

    _admit_all(monkeypatch)
    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        _flux,
    )
    built = _areal_observation(
        formula="NaCl",
        gas_temperature_K="1100",
        pressure_Pa="100000",
        fraction="0.01",
        carrier="N2",
        diameter=None,
        alpha=None,
        observation_id="bare",
    )
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, built)
    assert prediction.value is None
    assert prediction.refusal_detail["reason"] == "wall_flux_inputs_unprinted"
    assert "pipe_diameter_m" in prediction.refusal_detail["missing"]
    assert "sticking_coefficient" in prediction.refusal_detail["missing"]


def test_composition_accepts_a_gas_on_a_mole_fraction_basis() -> None:
    composition = Composition(
        basis="printed_mole_fraction",
        components=(("SO2", Decimal("0.01")), ("O2", Decimal("0.99"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    assert composition.as_map()["SO2"] == Decimal("0.01")
