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
    NoticeKind,
    PerBasis,
    Phase,
    Quantity,
)
from simulator.battery.identity import Identity
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


def test_areal_rate_calls_the_existing_wall_flux(monkeypatch) -> None:
    calls: list[str] = []

    def _admitted(species: str, **_kwargs: object) -> None:
        return None

    def _flux(species: str, *_args: object, **_kwargs: object) -> float:
        calls.append(species)
        return 2.0

    monkeypatch.setattr(
        "simulator.condensation._condensation_admission_refusal", _admitted
    )
    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        _flux,
    )
    composition = Composition(
        basis="vol-%",
        components=(
            ("Na2SO4", Decimal("0.0001")),
            ("N2", Decimal("0.9999")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = Identity(
        quantity=Quantity.WALL_DEPOSIT_AREAL_RATE,
        species=Species("Na2SO4", Phase.CR),
        temperature_K=State.of(Decimal("1373")),
        per=State.of(PerBasis.AREA_TIME),
        composition=State.of(composition),
        total_pressure_Pa=State.of(Decimal("101325")),
    )
    built = observation(
        "flux",
        "run",
        identity,
        "1",
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    built = replace(
        built,
        point_conditions={
            **(built.point_conditions or {}),
            "pipe_diameter_m": Located(State.of(Decimal("0.05"))),
            "sticking_coefficient": Located(State.of(Decimal("0.2"))),
        },
    )
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, built)
    molar_mass = resolve_species_formula("Na2SO4").molar_mass_kg_per_mol()
    assert calls == ["Na2SO4"]
    assert prediction.value == Decimal("2.0") * Decimal(str(molar_mass))
    assert prediction.refusal_reason is None


def test_missing_pipe_diameter_refuses_before_the_flux(monkeypatch) -> None:
    def _admitted(species: str, **_kwargs: object) -> None:
        return None

    def _flux(*_args: object, **_kwargs: object) -> float:
        raise AssertionError("flux called without a printed pipe diameter")

    monkeypatch.setattr(
        "simulator.condensation._condensation_admission_refusal", _admitted
    )
    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        _flux,
    )
    composition = Composition(
        basis="vol-%",
        components=(("NaCl", Decimal("0.01")), ("N2", Decimal("0.99"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = Identity(
        quantity=Quantity.WALL_DEPOSIT_AREAL_RATE,
        species=Species("NaCl", Phase.CR),
        temperature_K=State.of(Decimal("1100")),
        per=State.of(PerBasis.AREA_TIME),
        composition=State.of(composition),
        total_pressure_Pa=State.of(Decimal("100000")),
    )
    built = observation(
        "bare",
        "run",
        identity,
        "1",
        evidence=EvidenceClass.MEASURED_DIRECT,
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
