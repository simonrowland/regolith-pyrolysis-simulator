"""Element wt% with measured oxygen (t-1609) and the oxide-map engine split.

Expected numbers are hand arithmetic from the atomic weights. They are not
computed by calling the oxygen-demand expression back.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.composition import (
    element_mass_fraction_redox,
    element_wt_percent_of_oxide_phase,
    parse_printed_sigma,
    partition_engine_components,
    printed_oxygen_sigma_from_row,
)
from simulator.battery.enums import (
    ELEMENT_WT_PERCENT_BASIS,
    AmountBasis,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    NoticeKind,
    Phase,
    PhaseRole,
    Quantity,
    RefusalReason,
)
from simulator.battery.identity import Identity, celsius_to_kelvin
from simulator.battery.migrate import (
    _oxide_map_from_mapping,
    migrate,
    wt_pct_to_mole_fraction,
)
from simulator.battery.records import (
    Composition,
    Derivation,
    Located,
    Species,
    State,
)
from simulator.battery.score import predict_with_engine
from simulator.melt_backend.alphamelts import MELTS_OXIDE_BASIS
from simulator.melt_backend.base import EquilibriumResult
from tests.battery import factories as F
from tests.battery.test_migrate import _write_min_tree

M_SI = Decimal("28.085")
M_FE = Decimal("55.845")
M_O = Decimal("15.999")
M_TI = Decimal("47.867")
M_MN = Decimal("54.938044")
M_CR = Decimal("51.9961")
M_SIO2 = M_SI + 2 * M_O
M_FEO = M_FE + M_O
M_FE2O3 = 2 * M_FE + 3 * M_O

# Si 1 mol, Fe 1 mol, reference O demand 3 mol, measured O 3.25 mol.
HAND = {
    "Si": "28.085",
    "Fe": "55.845",
    "O": "51.99675",
    "Pt": "0.10",
    "Cl": "0.20",
}
SIGMA = Decimal("0.15999")
OUTSIDE = "oxygen_excess_outside_oxide_component_space"


def _decision(elements, **kwargs):
    return element_mass_fraction_redox(elements, **kwargs)


def test_hand_example_relabels_cations_and_derives_excess() -> None:
    decision = _decision(HAND, oxygen_wt_percent_sigma=SIGMA)

    assert decision.refusal is None
    assert decision.oxygen_excess_mol_per_100g == Decimal("0.25")
    assert decision.oxygen_excess_sigma_mol_per_100g == Decimal("0.01")
    assert decision.oxygen_uncertainty_not_printed is False
    assert decision.fe3_over_sum_fe == Decimal("0.5")
    assert decision.redox_kind == "ferric_fraction"
    assert decision.oxide_mole_fractions == (
        ("SiO2", Decimal("0.5")),
        ("FeO", Decimal("0.5")),
    )
    total = M_SIO2 + M_FEO * Decimal("0.5") + M_FE2O3 * Decimal("0.25")
    split = dict(decision.ferric_split_wt_percent)
    assert set(split) == {"SiO2", "FeO", "Fe2O3"}
    assert split["SiO2"] == Decimal(100) * M_SIO2 / total
    assert split["FeO"] == Decimal(100) * M_FEO * Decimal("0.5") / total
    assert split["Fe2O3"] == Decimal(100) * M_FE2O3 * Decimal("0.25") / total
    reasons = {notice.reason for notice in decision.notices}
    assert "pt_excluded_capsule_contamination" in reasons
    assert "cl_excluded_no_engine_chloride_component" in reasons
    assert "redox_from_measured_oxygen_fe_only_assumption" in reasons
    assert "O" not in dict(decision.oxide_mole_fractions)
    assert "Pt" not in dict(decision.oxide_mole_fractions)
    assert "Cl" not in dict(decision.oxide_mole_fractions)


def test_extra_oxygen_mass_fraction_is_one_over_oxygen_weight() -> None:
    """One extra gram of oxygen on stoichiometric FeO.

    The expected excess is 1/M_O. A demand that uses z instead of z/2
    makes this zero and fails the assertion.
    """

    decision = _decision({"Fe": "55.845", "O": "16.999"})
    # Stoichiometric FeO is 1 mol O. One extra gram is 1/M_O.
    # Decimal rounds 16.999/M_O - 1 and 1/M_O differently in the last digit,
    # so the exact pin is the extra-gram difference and the 1/M_O check is
    # the ruling's scale, well above that rounding.
    excess = decision.oxygen_excess_mol_per_100g
    assert excess == (Decimal("16.999") / M_O) - Decimal(1)
    assert abs(excess - (Decimal(1) / M_O)) < Decimal("1e-24")


def test_boundaries_are_all_ferrous_and_all_ferric() -> None:
    ferrous = _decision({"Si": "28.085", "Fe": "55.845", "O": "47.997"})
    assert ferrous.oxygen_excess_mol_per_100g == Decimal("0")
    assert ferrous.fe3_over_sum_fe == Decimal("0")
    assert ferrous.refusal is None
    assert "Fe2O3" not in dict(ferrous.ferric_split_wt_percent)
    assert "FeO" in dict(ferrous.ferric_split_wt_percent)

    ferric = _decision({"Si": "28.085", "Fe": "55.845", "O": "55.9965"})
    assert ferric.oxygen_excess_mol_per_100g == Decimal("0.5")
    assert ferric.fe3_over_sum_fe == Decimal("1")
    assert ferric.refusal is None
    assert "FeO" not in dict(ferric.ferric_split_wt_percent)
    assert "Fe2O3" in dict(ferric.ferric_split_wt_percent)


def test_below_zero_and_above_iron_capacity_are_typed_refusals() -> None:
    below = _decision({"Si": "28.085", "Fe": "55.845", "O": "31.998"})
    assert below.oxygen_excess_mol_per_100g == Decimal("-1")
    assert below.refusal == OUTSIDE
    assert below.redox_kind == "refusal"
    assert below.fe3_over_sum_fe is None
    assert below.ferric_split_wt_percent is None
    assert below.oxide_mole_fractions == (
        ("SiO2", Decimal("0.5")),
        ("FeO", Decimal("0.5")),
    )

    above = _decision({"Si": "28.085", "Fe": "55.845", "O": "59.99625"})
    assert above.oxygen_excess_mol_per_100g == Decimal("0.75")
    assert above.refusal == OUTSIDE
    assert above.oxide_mole_fractions is not None
    assert above.fe3_over_sum_fe is None


def test_missing_oxygen_uncertainty_is_flagged_and_not_invented() -> None:
    decision = _decision(HAND)
    assert decision.oxygen_excess_mol_per_100g == Decimal("0.25")
    assert decision.oxygen_excess_sigma_mol_per_100g is None
    assert decision.oxygen_uncertainty_not_printed is True
    assert any(
        notice.reason == "oxygen_uncertainty_not_printed"
        for notice in decision.notices
    )

    zero = _decision(HAND, oxygen_wt_percent_sigma=Decimal("0"))
    assert zero.oxygen_excess_sigma_mol_per_100g == Decimal("0")
    assert zero.oxygen_uncertainty_not_printed is False

    assert parse_printed_sigma("0") == Decimal("0")
    assert parse_printed_sigma("-0.1") is None
    assert parse_printed_sigma("nan") is None
    assert parse_printed_sigma({"sigma": "0.15999"}) == SIGMA

    explicit, clash = printed_oxygen_sigma_from_row(
        {
            "oxygen_wt_percent_sigma": "0.2",
            "element_wt_percent_uncertainty": {"O": {"sigma": "0.3"}},
        }
    )
    assert explicit == Decimal("0.2")
    assert clash == "oxygen_wt_percent_sigma_disagrees_with_element_uncertainty"
    nested, nested_clash = printed_oxygen_sigma_from_row(
        {"element_wt_percent_uncertainty": {"O": "0.15999"}}
    )
    assert nested == SIGMA and nested_clash is None
    ignored, ignored_clash = printed_oxygen_sigma_from_row(
        {"element_wt_percent_printed_1sd": {"O": "0.30"}}
    )
    assert ignored is None and ignored_clash is None


def test_titanium_manganese_chromium_are_listed_and_change_the_excess() -> None:
    cases = (
        ("Ti", "4.7867", Decimal("0.05"), "Ti"),
        ("Mn", "5.4938044", Decimal("0.15"), "Mn"),
        ("Cr", "5.19961", Decimal("0.10"), "Cr"),
    )
    for element, weight, excess, listed in cases:
        elements = dict(HAND)
        elements[element] = weight
        decision = _decision(elements, oxygen_wt_percent_sigma=SIGMA)
        assert decision.oxygen_excess_mol_per_100g == excess
        assert decision.refusal is None
        assert decision.redox_kind == "ferric_fraction"
        reasons = [notice.reason for notice in decision.notices]
        assert (
            f"redox_from_measured_oxygen_fe_only_assumption: {listed}" in reasons
        )


def test_chlorine_is_a_component_only_when_one_engine_chloride_is_named() -> None:
    included = _decision(HAND, chloride_components=("Cl",))
    assert "Cl" in dict(included.oxide_mole_fractions)
    assert not any("cl_excluded" in notice.reason for notice in included.notices)

    ambiguous = _decision(HAND, chloride_components=("Cl", "Cl2"))
    assert "Cl" not in dict(ambiguous.oxide_mole_fractions or ())
    assert any(
        notice.reason == "cl_excluded_ambiguous_engine_chloride_components"
        for notice in ambiguous.notices
    )


def test_circular_observable_keeps_conditions_and_drops_the_ferric_split() -> None:
    for observable in (
        "oxygen_excess",
        "fe3_fe2_ratio",
        "fe3_over_sum_fe",
        Quantity.VALENCE_RATIO,
    ):
        decision = _decision(
            HAND, oxygen_wt_percent_sigma=SIGMA, observable=observable
        )
        assert decision.redox_kind == "conditions"
        assert decision.ferric_split_wt_percent is None
        assert decision.refusal is None
        assert decision.oxide_mole_fractions is not None


def test_no_iron_matches_stoichiometry_or_refuses() -> None:
    matched = _decision({"Si": "28.085", "O": "31.998"})
    assert matched.oxygen_excess_mol_per_100g == Decimal("0")
    assert matched.redox_kind == "stoichiometry_only"
    assert matched.refusal is None
    assert matched.oxide_mole_fractions == (("SiO2", Decimal("1")),)

    extra = _decision({"Si": "28.085", "O": "47.997"})
    assert extra.oxygen_excess_mol_per_100g == Decimal("1")
    assert extra.refusal == OUTSIDE


def test_unlisted_duplicate_and_negative_amounts_refuse_without_a_partial() -> None:
    unlisted = _decision({"Si": "28.085", "S": "1", "O": "32"})
    assert unlisted.oxide_mole_fractions is None
    assert unlisted.refusal == "element_reference_valence_not_stated: S"

    duplicate = _decision({"Fe": "10", "FeT": "10", "O": "5", "Si": "28.085"})
    assert duplicate.refusal == "duplicate_total_iron"
    assert duplicate.oxide_mole_fractions is None

    negative = _decision({"Si": "-1", "O": "1"})
    assert negative.refusal.startswith("element_amount_not_a_nonnegative_number")
    assert negative.oxide_mole_fractions is None

    missing = _decision({"Si": "28.085", "Fe": "55.845"})
    assert missing.refusal == "measured_oxygen_not_printed"
    assert missing.oxide_mole_fractions == (
        ("SiO2", Decimal("0.5")),
        ("FeO", Decimal("0.5")),
    )


def test_scale_check_from_atomic_weights() -> None:
    """Ruling scale check. Weights only, so the demand mutation leaves it green."""

    n_fe = (Decimal(10) / M_FE).quantize(Decimal("0.001"))
    full_oxidation = (n_fe / 2).quantize(Decimal("0.0001"))
    one_weight_percent = (Decimal(1) / M_O).quantize(Decimal("0.0001"))
    ferric_swing = (Decimal(2) * one_weight_percent / n_fe).quantize(Decimal("0.1"))
    assert n_fe == Decimal("0.179")
    assert full_oxidation == Decimal("0.0895")
    assert one_weight_percent == Decimal("0.0625")
    assert ferric_swing == Decimal("0.7")


def test_inverse_silica_element_weight_uses_the_oxide_phase() -> None:
    silicon = element_wt_percent_of_oxide_phase({"SiO2": Decimal("100")}, "Si")
    assert silicon == Decimal(100) * M_SI / M_SIO2


def test_printed_oxide_map_keeps_identifiable_components_and_refuses_the_rest() -> None:
    printed = {
        "SiO2": Decimal("80"),
        "H2O": Decimal("5"),
        "Cl": Decimal("1"),
        "Ag2O": Decimal("2"),
        "I2O": Decimal("0.5"),
        "Re": Decimal("0.1"),
    }
    stored = wt_pct_to_mole_fraction(printed)
    assert set(dict(stored.components)) == set(printed)

    kept, excluded = partition_engine_components(
        printed,
        engine_components=MELTS_OXIDE_BASIS,
        retain_components=(),
    )
    assert dict(kept) == {"SiO2": Decimal("80")}
    assert dict(excluded) == {
        "H2O": Decimal("5"),
        "Cl": Decimal("1"),
        "Ag2O": Decimal("2"),
        "I2O": Decimal("0.5"),
        "Re": Decimal("0.1"),
    }

    engine_with_water_and_chlorine = (*MELTS_OXIDE_BASIS, "H2O", "Cl")
    kept_supported, excluded_supported = partition_engine_components(
        printed,
        engine_components=engine_with_water_and_chlorine,
        retain_components=(),
    )
    assert set(dict(kept_supported)) == {"SiO2", "H2O", "Cl"}
    assert set(dict(excluded_supported)) == {"Ag2O", "I2O", "Re"}

    unchanged_kept, unchanged_excluded = partition_engine_components(
        {"SiO2": Decimal("60"), "MgO": Decimal("40")},
        engine_components=MELTS_OXIDE_BASIS,
        retain_components=(),
    )
    assert unchanged_excluded == ()
    assert dict(unchanged_kept) == {"SiO2": Decimal("60"), "MgO": Decimal("40")}

    with pytest.raises(
        ValueError,
        match=(
            "printed oxide wt% map has unsupported numeric component\\(s\\): "
            "Na2O_plus_K2O"
        ),
    ):
        wt_pct_to_mole_fraction(
            {"SiO2": Decimal("90"), "Na2O_plus_K2O": Decimal("10")}
        )
    with pytest.raises(
        ValueError,
        match="printed oxide wt% map has unsupported numeric component\\(s\\): XxO",
    ):
        wt_pct_to_mole_fraction({"SiO2": Decimal("90"), "XxO": Decimal("10")})

    reasons: list[str] = []
    refused = _oxide_map_from_mapping(
        {"SiO2": "90", "Na2O_plus_K2O": "10"},
        refusal_reason=reasons,
        declared_map=True,
    )
    assert refused is None
    assert reasons == [
        "printed oxide wt% map has unsupported numeric component(s): Na2O_plus_K2O"
    ]
    accepted = _oxide_map_from_mapping(
        {"SiO2": "80", "H2O": "5", "Cl": "1"},
        declared_map=True,
    )
    assert accepted is not None
    assert set(accepted) == {"SiO2", "H2O", "Cl"}


def _located(value: object) -> dict:
    return {
        "state": {"tag": "value", "value": value},
        "locator": {"table": "1", "page": 4},
    }


def _element_observation(
    observation_id: str,
    formula: str,
    amount: str,
    elements: dict[str, str],
    *,
    temperature_c: str,
    sigma: str | None = None,
    experiment: str,
) -> dict:
    values = {
        "quantity": "equilibrium_phase_composition",
        "phase_label": "glass",
        "basis": "element_wt_percent",
        "component_amount": amount,
        "T_C": temperature_c,
        "method_class": "measured_direct",
        "admission_status": "admitted",
        "element_wt_percent": dict(elements),
        "point_conditions": {
            "T_C": _located(temperature_c),
            "total_pressure_Pa": _located({"kind": "point", "point": "101325"}),
            "pO2_Pa": _located(10),
        },
    }
    if sigma is not None:
        values["oxygen_wt_percent_sigma"] = sigma
    return {
        "observation_id": observation_id,
        "experiment": experiment,
        "locator": {"table": "1", "page": 4},
        "values": values,
    }


def _element_extract() -> dict:
    shared = dict(HAND)
    low = dict(HAND)
    low["O"] = "31.998"
    high = dict(HAND)
    high["O"] = "59.99625"
    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {
            "citation": "Fixture, A. (2026), Test Journal 1:1",
            "doi": "10.1234/FIXTURE",
        },
        "extraction": {"method": "unit_test", "date": "2026-11-03", "worker": "pytest"},
        "review_status": "draft",
        "species": {
            "Si": {
                "observations": [
                    _element_observation(
                        "si-1400",
                        "Si",
                        "28.085",
                        shared,
                        temperature_c="1400",
                        sigma="0.15999",
                        experiment="run-1400",
                    ),
                    _element_observation(
                        "si-low-o",
                        "Si",
                        "28.085",
                        low,
                        temperature_c="1400",
                        sigma="0.15999",
                        experiment="run-low",
                    ),
                    _element_observation(
                        "si-high-o",
                        "Si",
                        "28.085",
                        high,
                        temperature_c="1400",
                        sigma="0.15999",
                        experiment="run-high",
                    ),
                    _element_observation(
                        "si-no-sigma",
                        "Si",
                        "28.085",
                        shared,
                        temperature_c="1450",
                        experiment="run-nosigma",
                    ),
                ]
            },
            "Fe": {
                "observations": [
                    _element_observation(
                        "fe-1500",
                        "Fe",
                        "55.845",
                        shared,
                        temperature_c="1500",
                        sigma="0.15999",
                        experiment="run-1500",
                    )
                ]
            },
        },
    }


def _row(result, raw_id: str):
    matches = [
        observation
        for observation in result.observations.values()
        if observation.observation_id.endswith(f"::{raw_id}")
    ]
    assert len(matches) == 1, (
        raw_id,
        [(entry.observation_id, entry.why) for entry in result.queue],
    )
    return matches[0]


def test_migration_reaches_temperature_and_keeps_the_printed_elements(tmp_path) -> None:
    root = _write_min_tree(tmp_path / "tree", _element_extract())
    result = migrate(root, write=False)
    silicon = _row(result, "si-1400")
    iron = _row(result, "fe-1500")

    assert silicon.identity.temperature_K.value == celsius_to_kelvin("1400")
    assert iron.identity.temperature_K.value == celsius_to_kelvin("1500")
    assert silicon.identity.total_pressure_Pa.value == Decimal("101325")
    assert iron.identity.total_pressure_Pa.value == Decimal("101325")
    assert silicon.identity.fO2_Pa.value == Decimal("10")
    assert iron.identity.fO2_Pa.value == Decimal("10")
    assert silicon.identity.subtype.value == ELEMENT_WT_PERCENT_BASIS
    assert silicon.identity.phase_role.value is PhaseRole.GLASS
    assert silicon.value.point == Decimal("28.085")
    assert iron.value.point == Decimal("55.845")

    for observation, temperature in ((silicon, "1400"), (iron, "1500")):
        printed = observation.point_conditions[ELEMENT_WT_PERCENT_BASIS]
        assert printed.inference is None
        assert printed.state.value.amount_basis is AmountBasis.MASS_PERCENT
        assert printed.state.value.basis == ELEMENT_WT_PERCENT_BASIS
        amounts = dict(printed.state.value.components)
        assert amounts["Si"] == Decimal("28.085")
        assert amounts["Fe"] == Decimal("55.845")
        assert amounts["O"] == Decimal("51.99675")
        assert amounts["Pt"] == Decimal("0.10")
        assert amounts["Cl"] == Decimal("0.20")
        assert observation.point_conditions["T_C"].state.value == Decimal(temperature)
        composition = observation.identity.composition.value
        assert composition.basis == "element_wt_percent_feo_t"
        assert composition.amount_basis is AmountBasis.MOLE_FRACTION
        assert dict(composition.components) == {
            "SiO2": Decimal("0.5"),
            "FeO": Decimal("0.5"),
        }
        excess = observation.point_conditions["oxygen_excess_mol_per_100g"]
        assert excess.state.value == Decimal("0.25")
        assert excess.inference.relation == "measured_oxygen_excess"
        assert excess.inference.output_unit == "mol/100g"
        parameters = dict(excess.inference.parameters)
        assert parameters["oxygen_wt_percent_sigma"].state.value == SIGMA
        assert parameters["derived_fe3_over_sum_fe"].state.value == Decimal("0.5")
        stored_composition = observation.point_conditions["composition"]
        assert stored_composition.inference.relation == "element_wt_percent_feo_t"
        reasons = {notice.reason for notice in observation.notices}
        assert "pt_excluded_capsule_contamination" in reasons
        assert "cl_excluded_no_engine_chloride_component" in reasons
        assert "redox_from_measured_oxygen_fe_only_assumption" in reasons

    low = _row(result, "si-low-o")
    high = _row(result, "si-high-o")
    assert low.point_conditions["oxygen_excess_mol_per_100g"].state.value == Decimal("-1")
    assert high.point_conditions["oxygen_excess_mol_per_100g"].state.value == Decimal("0.75")
    assert low.identity.composition.value.basis == "element_wt_percent_feo_t"
    assert high.identity.composition.is_value
    assert any(notice.reason == OUTSIDE for notice in low.notices)
    assert any(notice.reason == OUTSIDE for notice in high.notices)
    low_parameters = dict(
        low.point_conditions["oxygen_excess_mol_per_100g"].inference.parameters
    )
    assert "derived_fe3_over_sum_fe" not in low_parameters

    unsigned = _row(result, "si-no-sigma")
    assert unsigned.identity.temperature_K.value == celsius_to_kelvin("1450")
    unsigned_excess = unsigned.point_conditions["oxygen_excess_mol_per_100g"]
    assert unsigned_excess.state.value == Decimal("0.25")
    assert "oxygen_wt_percent_sigma" not in dict(unsigned_excess.inference.parameters)
    assert any(
        notice.reason == "oxygen_uncertainty_not_printed"
        for notice in unsigned.notices
    )

    from simulator.battery.fo2_ladder import _printed_ferric_fraction

    assert _printed_ferric_fraction(silicon, None) == (None, "")


def _phase_identity(
    formula: str,
    *,
    quantity: Quantity = Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
    subtype: str | None = ELEMENT_WT_PERCENT_BASIS,
    fO2_Pa: Decimal | None = None,
    composition: Composition | None = None,
) -> Identity:
    kwargs = {
        "quantity": quantity,
        "species": Species(formula, Phase.GLASS),
        "temperature_K": State.of(celsius_to_kelvin("1400")),
        "phase_role": State.of(PhaseRole.GLASS),
        "total_pressure_Pa": State.of(Decimal("101325")),
        "composition": State.of(
            composition
            or Composition(
                basis="element_wt_percent_feo_t",
                components=(
                    ("SiO2", Decimal("0.5")),
                    ("FeO", Decimal("0.5")),
                ),
                amount_basis=AmountBasis.MOLE_FRACTION,
            )
        ),
    }
    if subtype is not None:
        kwargs["subtype"] = State.of(subtype)
    if fO2_Pa is not None:
        kwargs["fO2_Pa"] = State.of(fO2_Pa)
    return Identity(**kwargs)


def _with_elements(observation, elements, *, sigma: Decimal | None = SIGMA, fe3=Decimal("0.5")):
    printed = Composition(
        basis=ELEMENT_WT_PERCENT_BASIS,
        components=tuple((name, Decimal(amount)) for name, amount in elements.items()),
        amount_basis=AmountBasis.MASS_PERCENT,
    )
    parameters = []
    if sigma is not None:
        parameters.append(
            ("oxygen_wt_percent_sigma", F.located(sigma))
        )
    if fe3 is not None:
        parameters.append(("derived_fe3_over_sum_fe", F.located(fe3)))
    point = dict(observation.point_conditions or {})
    point[ELEMENT_WT_PERCENT_BASIS] = F.located(printed)
    point["oxygen_excess_mol_per_100g"] = Located(
        State.of(Decimal("0.25")),
        locator=F.loc(),
        inference=Derivation(
            relation="measured_oxygen_excess",
            inputs=("pdf-1",),
            parameters=tuple(parameters),
            output_unit="mol/100g",
        ),
    )
    return replace(observation, point_conditions=point)


def _record_call(
    calls: list[dict],
    engine,
    temperature_C,
    composition_kg,
    fO2_log,
    pressure_bar,
    redox_mode,
):
    calls.append(
        {
            "engine": engine,
            "temperature_C": temperature_C,
            "composition_kg": dict(composition_kg),
            "fO2_log": fO2_log,
            "pressure_bar": pressure_bar,
            "redox_mode": redox_mode,
        }
    )


def _hook(calls: list[dict]):
    def equilibrate(
        engine,
        *,
        temperature_C,
        composition_kg,
        fO2_log,
        pressure_bar,
        handles=None,
        redox_mode=None,
    ):
        _record_call(
            calls,
            engine,
            temperature_C,
            composition_kg,
            fO2_log,
            pressure_bar,
            redox_mode,
        )
        return EquilibriumResult(
            liquid_fraction=1.0,
            phase_compositions={"liquid": {"SiO2": 100.0}},
        )

    return equilibrate


def _echo(calls: list[dict]):
    """Return the engine charge renormalized to 100 wt%."""

    def equilibrate(
        engine,
        *,
        temperature_C,
        composition_kg,
        fO2_log,
        pressure_bar,
        handles=None,
        redox_mode=None,
    ):
        _record_call(
            calls,
            engine,
            temperature_C,
            composition_kg,
            fO2_log,
            pressure_bar,
            redox_mode,
        )
        total = sum(composition_kg.values())
        phase = {
            name: 100.0 * amount / total
            for name, amount in composition_kg.items()
            if amount
        }
        return EquilibriumResult(
            liquid_fraction=1.0,
            phase_compositions={"liquid": phase},
        )

    return equilibrate


def test_in_range_score_sends_the_ferric_split_and_not_fo2(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", _echo(calls)
    )
    identity = _phase_identity("Si")
    printed_si = Decimal("28.085")
    observation = _with_elements(
        F.observation(
            "si-score",
            "run-1400",
            identity,
            printed_si,
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="fixture-source",
        ),
        HAND,
    )
    prediction = predict_with_engine(Engine.ALPHAMELTS, observation)
    assert prediction.execution.state is ExecutionState.PRODUCED
    # Echo of the ferric charge (SiO2 + FeO + Fe2O3), not a pure-SiO2 phase
    # scaled by the FeO_T mass. Printed Si is the hand vector's 1 mol.
    assert prediction.value is not None
    assert abs(prediction.value - printed_si) < Decimal("1e-9")
    assert len(calls) == 1
    call = calls[0]
    assert call["fO2_log"] is None
    assert call["redox_mode"] == "not_an_input"
    assert call["temperature_C"] == 1400.0
    assert call["pressure_bar"] == pytest.approx(1.01325)
    total = M_SIO2 + M_FEO * Decimal("0.5") + M_FE2O3 * Decimal("0.25")
    expected = {
        "SiO2": float(Decimal(100) * M_SIO2 / total) / 1000.0,
        "FeO": float(Decimal(100) * M_FEO * Decimal("0.5") / total) / 1000.0,
        "Fe2O3": float(Decimal(100) * M_FE2O3 * Decimal("0.25") / total) / 1000.0,
    }
    assert set(call["composition_kg"]) == set(expected)
    for name, amount in expected.items():
        assert call["composition_kg"][name] == pytest.approx(amount)
    reasons = {notice.reason for notice in prediction.notices}
    assert "element_wt_percent_comparison_denominator_is_oxide_phase" not in reasons
    assert "redox_from_measured_oxygen_fe_only_assumption" in reasons


def test_outside_range_does_not_call_the_engine(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", _hook(calls)
    )
    low = dict(HAND)
    low["O"] = "31.998"
    observation = _with_elements(
        F.observation(
            "si-low",
            "run-low",
            _phase_identity("Si"),
            Decimal("28.085"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="fixture-source",
        ),
        low,
        fe3=None,
    )
    prediction = predict_with_engine(Engine.THERMOENGINE, observation)
    assert calls == []
    assert (
        prediction.refusal_reason
        is RefusalReason.OXYGEN_EXCESS_OUTSIDE_OXIDE_COMPONENT_SPACE
    )
    assert prediction.value is None


def _circular_refusal(monkeypatch, elements, mode: str):
    calls: list[dict] = []
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", _hook(calls)
    )

    def kress(**_kwargs):
        calls.append({"kress": True})
        return {"ratio": 0.2}

    monkeypatch.setattr("simulator.fe_redox.kress91_split", kress)
    if mode == "oxygen_excess":
        identity = _phase_identity("Si", fO2_Pa=Decimal("10"))
        engine = Engine.ALPHAMELTS
        observation = F.observation(
            "o-excess-refused",
            "run-1400",
            identity,
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="fixture-source",
        )
        observation = replace(
            observation,
            derivation=Derivation(
                relation="oxygen_excess",
                inputs=("pdf-1",),
                parameters=(),
                output_unit="mol/100g",
            ),
        )
    elif mode == "fe3_fe2_ratio":
        identity = _phase_identity(
            "Fe",
            quantity=Quantity.FE3_FE2_RATIO,
            subtype=None,
            fO2_Pa=Decimal("10"),
        )
        engine = Engine.INTERNAL_ANALYTICAL
        observation = F.observation(
            "fe-ratio-refused",
            "run-1400",
            identity,
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="fixture-source",
        )
    else:
        raise AssertionError(mode)
    observation = _with_elements(observation, elements, fe3=None)
    prediction = predict_with_engine(engine, observation)
    return prediction, calls


@pytest.mark.parametrize("mode", ["oxygen_excess", "fe3_fe2_ratio"])
def test_out_of_range_circular_row_does_not_call_the_engine(monkeypatch, mode) -> None:
    high = dict(HAND)
    high["O"] = "59.99625"
    prediction, calls = _circular_refusal(monkeypatch, high, mode)
    assert calls == []
    assert prediction.value is None
    assert (
        prediction.refusal_reason
        is RefusalReason.OXYGEN_EXCESS_OUTSIDE_OXIDE_COMPONENT_SPACE
    )


@pytest.mark.parametrize("mode", ["oxygen_excess", "fe3_fe2_ratio"])
def test_missing_oxygen_circular_row_does_not_call_the_engine(monkeypatch, mode) -> None:
    elements = {key: value for key, value in HAND.items() if key != "O"}
    prediction, calls = _circular_refusal(monkeypatch, elements, mode)
    assert calls == []
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == "measured_oxygen_not_printed"


def test_platinum_exclusion_echo_matches_the_printed_silicon(monkeypatch) -> None:
    """90 g SiO2 + 9 g FeO + 1 g Pt. An echo of that silicate residuals to 0.

    Printed silicon is the element mass in the original 100 g. The engine
    phase is the 99 g of oxide renormalized to 100 wt%. Those are different
    denominators; the scored number has to come back onto the printed one.
    """

    w_si = Decimal(90) * M_SI / M_SIO2
    w_fe = Decimal(9) * M_FE / M_FEO
    w_o = Decimal(90) * (2 * M_O) / M_SIO2 + Decimal(9) * M_O / M_FEO
    elements = {
        "Si": format(w_si, "f"),
        "Fe": format(w_fe, "f"),
        "O": format(w_o, "f"),
        "Pt": "1",
    }
    calls: list[dict] = []
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", _echo(calls)
    )
    observation = _with_elements(
        F.observation(
            "si-echo",
            "run-echo",
            _phase_identity("Si"),
            w_si,
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="fixture-source",
        ),
        elements,
        fe3=Decimal(0),
    )
    prediction = predict_with_engine(Engine.THERMOENGINE, observation)
    assert len(calls) == 1
    assert prediction.refusal_reason is None
    assert prediction.value is not None
    assert abs(prediction.value - w_si) < Decimal("1e-9")
    unscaled = (Decimal(90) / Decimal(99)) * Decimal(100) * M_SI / M_SIO2
    assert abs(unscaled - w_si) > Decimal("0.4")
    assert "element_wt_percent_comparison_denominator_is_oxide_phase" not in {
        notice.reason for notice in prediction.notices
    }


def _ferric_oxide_elements(sio2_g: Decimal, fe2o3_g: Decimal, pt_g: Decimal = Decimal(0)):
    """Printed element wt% of an oxide mixture. Grams, not the excess formula."""

    w_si = sio2_g * M_SI / M_SIO2
    w_fe = fe2o3_g * (Decimal(2) * M_FE) / M_FE2O3
    w_o = sio2_g * (Decimal(2) * M_O) / M_SIO2 + fe2o3_g * (Decimal(3) * M_O) / M_FE2O3
    elements = {
        "Si": format(w_si, "f"),
        "Fe": format(w_fe, "f"),
        "O": format(w_o, "f"),
    }
    if pt_g:
        elements["Pt"] = format(pt_g, "f")
    return elements, w_si


def _echo_silicon(monkeypatch, elements, printed_si: Decimal, *, oxygen_excess: bool):
    calls: list[dict] = []
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", _echo(calls)
    )
    identity = _phase_identity(
        "Si", fO2_Pa=Decimal("10") if oxygen_excess else None
    )
    observation = _with_elements(
        F.observation(
            "si-ferric-echo",
            "run-echo",
            identity,
            printed_si,
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="fixture-source",
        ),
        elements,
        fe3=None,
    )
    if oxygen_excess:
        observation = replace(
            observation,
            derivation=Derivation(
                relation="oxygen_excess",
                inputs=("pdf-1",),
                parameters=(),
                output_unit="mol/100g",
            ),
        )
    prediction = predict_with_engine(Engine.THERMOENGINE, observation)
    return prediction, calls


def test_all_ferric_echo_matches_the_printed_silicon(monkeypatch) -> None:
    """50 g SiO2 + 50 g Fe2O3. The ferric charge, not the FeO_T mass."""

    elements, w_si = _ferric_oxide_elements(Decimal(50), Decimal(50))
    prediction, calls = _echo_silicon(monkeypatch, elements, w_si, oxygen_excess=False)
    assert len(calls) == 1
    assert "Fe2O3" in calls[0]["composition_kg"]
    assert calls[0]["fO2_log"] is None
    assert prediction.value is not None
    assert abs(prediction.value - w_si) < Decimal("1e-9")


def test_all_ferric_echo_with_platinum_matches_the_printed_silicon(monkeypatch) -> None:
    """50 g SiO2 + 49 g Fe2O3 + 1 g Pt. Pt is out; the ferric oxide mass is in."""

    elements, w_si = _ferric_oxide_elements(Decimal(50), Decimal(49), Decimal(1))
    prediction, calls = _echo_silicon(monkeypatch, elements, w_si, oxygen_excess=False)
    assert len(calls) == 1
    assert "Fe2O3" in calls[0]["composition_kg"]
    assert "Pt" not in calls[0]["composition_kg"]
    assert prediction.value is not None
    assert abs(prediction.value - w_si) < Decimal("1e-9")


def test_oxygen_excess_echo_of_all_ferric_stays_on_feo_t(monkeypatch) -> None:
    """The same 50 g + 50 g analysis, when the observable is the excess.

    The charge is SiO2 + FeO. Scaling that echo by the FeO_T mass returns
    the printed silicon. Scaling it by the ferric mass would not.
    """

    elements, w_si = _ferric_oxide_elements(Decimal(50), Decimal(50))
    prediction, calls = _echo_silicon(monkeypatch, elements, w_si, oxygen_excess=True)
    assert len(calls) == 1
    assert calls[0]["fO2_log"] == -4.0
    assert "Fe2O3" not in calls[0]["composition_kg"]
    assert set(calls[0]["composition_kg"]) == {"SiO2", "FeO"}
    assert prediction.value is not None
    assert abs(prediction.value - w_si) < Decimal("1e-9")


def test_platinum_and_chlorine_rows_are_refused_before_the_engine(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", _hook(calls)
    )
    for formula, reason in (
        ("Pt", "pt_excluded_capsule_contamination"),
        ("Cl", "cl_excluded_no_engine_chloride_component"),
    ):
        observation = _with_elements(
            F.observation(
                formula,
                "run-1400",
                _phase_identity(formula),
                Decimal("0.1"),
                evidence=EvidenceClass.MEASURED_DIRECT,
                source_id="fixture-source",
            ),
            HAND,
        )
        prediction = predict_with_engine(Engine.MAGEMIN, observation)
        assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
        assert prediction.refusal_detail["reason"] == reason
    assert calls == []


def test_oxygen_excess_observable_sends_fo2_not_the_derived_split(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", _hook(calls)
    )
    observation = _with_elements(
        F.observation(
            "o-excess",
            "run-1400",
            _phase_identity("Si", fO2_Pa=Decimal("10")),
            Decimal("51.99675"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="fixture-source",
        ),
        HAND,
    )
    observation = replace(
        observation,
        derivation=Derivation(
            relation="oxygen_excess",
            inputs=("pdf-1",),
            parameters=(),
            output_unit="mol/100g",
        ),
    )
    prediction = predict_with_engine(Engine.ALPHAMELTS, observation)
    assert prediction.refusal_reason is None
    assert len(calls) == 1
    assert calls[0]["fO2_log"] == -4.0
    assert calls[0]["redox_mode"] is None
    assert "Fe2O3" not in calls[0]["composition_kg"]
    assert set(calls[0]["composition_kg"]) == {"SiO2", "FeO"}


def test_fe_redox_row_uses_kress_on_fo2_and_refuses_a_fed_back_fraction(
    monkeypatch,
) -> None:
    captured: dict = {}

    def fake_split(*, fO2_log, mol_fractions, T_K, pressure_bar):
        captured["fO2_log"] = fO2_log
        captured["mol"] = dict(mol_fractions)
        return {"ratio": 0.2}

    monkeypatch.setattr("simulator.fe_redox.kress91_split", fake_split)
    identity = _phase_identity(
        "Fe",
        quantity=Quantity.FE3_FE2_RATIO,
        subtype=None,
        fO2_Pa=Decimal("10"),
    )
    observation = _with_elements(
        F.observation(
            "fe-ratio",
            "run-1400",
            identity,
            Decimal("0.5"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="fixture-source",
        ),
        HAND,
    )
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, observation)
    assert prediction.refusal_reason is None
    assert prediction.value == Decimal("0.4")
    assert captured["fO2_log"] == -4.0
    assert captured["mol"].get("Fe2O3", 0.0) == 0.0
    assert captured["mol"].get("FeOt", 0.0) > 0.0

    def refuse_kress(**_kwargs):
        raise AssertionError("derived Fe3+ must not reach Kress")

    monkeypatch.setattr("simulator.fe_redox.kress91_split", refuse_kress)

    class _FedBack:
        redox_kind = "ferric_fraction"
        ferric_split_wt_percent = (("FeO", Decimal("50")), ("Fe2O3", Decimal("50")))

    monkeypatch.setattr(
        "simulator.battery.score.element_mass_fraction_redox",
        lambda *_args, **_kwargs: _FedBack(),
    )
    refused = predict_with_engine(Engine.INTERNAL_ANALYTICAL, observation)
    assert refused.refusal_reason is RefusalReason.CYCLIC_DERIVATION
    assert refused.value is None


def test_engine_boundary_excludes_unsupported_oxides_and_keeps_supported_ones(
    monkeypatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage", _hook(calls)
    )

    def score(components, engine):
        composition = Composition(
            basis="printed_oxides",
            components=tuple(components),
            amount_basis=AmountBasis.MOLE_FRACTION,
        )
        identity = _phase_identity(
            "SiO2",
            subtype="wt_percent",
            fO2_Pa=Decimal("10"),
            composition=composition,
        )
        identity = replace(identity, species=Species("SiO2", Phase.GLASS))
        observation = F.observation(
            "oxide-row",
            "run-1400",
            identity,
            Decimal("50"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="fixture-source",
        )
        return predict_with_engine(engine, observation)

    excluded = score(
        (("SiO2", Decimal("4")), ("Ag2O", Decimal("1"))),
        Engine.ALPHAMELTS,
    )
    assert excluded.execution.state is ExecutionState.PRODUCED
    assert set(calls[-1]["composition_kg"]) == {"SiO2"}
    assert calls[-1]["composition_kg"]["SiO2"] == pytest.approx(0.1)
    exclusion = next(
        notice
        for notice in excluded.notices
        if notice.kind is NoticeKind.INPUT_OMITTED
    )
    assert exclusion.reason.startswith(
        "excluded_unsupported_composition_components: Ag2O "
    )
    assert "wt%" in exclusion.reason
    assert exclusion.dropped == ("Ag2O",)

    water = score(
        (("SiO2", Decimal("1")), ("H2O", Decimal("1"))),
        Engine.MAGEMIN,
    )
    assert water.refusal_reason is None
    assert set(calls[-1]["composition_kg"]) == {"SiO2", "H2O"}

    chlorine = score(
        (("SiO2", Decimal("1")), ("Cl", Decimal("1"))),
        Engine.ALPHAMELTS,
    )
    assert set(calls[-1]["composition_kg"]) == {"SiO2"}
    assert any(
        notice.dropped == ("Cl",) and "Cl " in notice.reason
        for notice in chlorine.notices
    )

    historical = score(
        (("SiO2", Decimal("1")), ("ZrO2", Decimal("1"))),
        Engine.ALPHAMELTS,
    )
    assert set(calls[-1]["composition_kg"]) == {"SiO2", "ZrO2"}
    assert not any(
        notice.reason.startswith("excluded_unsupported_composition_components")
        for notice in historical.notices
    )

    plain_composition = Composition(
        basis="printed_oxides",
        components=(("SiO2", Decimal("1")), ("MgO", Decimal("1"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    plain = score(plain_composition.components, Engine.THERMOENGINE)
    from simulator.battery.score import composition_wt_pct

    expected_kg = {
        name: amount / 1000.0
        for name, amount in composition_wt_pct(plain_composition).items()
    }
    assert calls[-1]["composition_kg"] == expected_kg
    assert plain.authority is Authority.BRIDGE
