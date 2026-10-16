import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    EvidenceClass,
    IdentityEqualKind,
    Phase,
    Quantity,
    Rail,
    RefusalReason,
    ValenceDenominatorKind,
    ValenceValueForm,
    ValueKind,
)
from simulator.battery.identity import (
    Identity,
    IdentityRefusal,
    ValenceBasis,
    _same_row_redox_identity_equal,
    identity_equal,
    parse_valence_basis,
    profile_for,
)
from simulator.battery.migrate import (
    Migrator,
    _identity_from_plain,
    map_quantity,
    oxide_molar_mass,
    to_plain,
    wt_pct_to_mole_fraction,
)
from simulator.battery.records import Composition, Species, State
from simulator.battery.score import (
    SCORE_ENGINE_SET,
    ScoreContext,
    score_store,
)


ROOT = Path(__file__).resolve().parents[2]
SOSSI_CR = ROOT / "data/literature/extracts/kems-045-sossi-2018-pnas-cr.yaml"
JAYASURIYA_FE = ROOT / "data/literature/extracts/jayasuriya-2004-iron-mossbauer.yaml"


def _identity(basis: ValenceBasis) -> Identity:
    return Identity(
        quantity=Quantity.VALENCE_RATIO,
        species=Species("Cr", Phase.L),
        valence_basis=State.of(basis),
    )


@pytest.mark.parametrize(
    ("printed", "denominator_kind", "denominator_state", "value_form"),
    (
        (
            "Cr2plus_over_total_Cr",
            ValenceDenominatorKind.TOTAL_ELEMENT,
            None,
            ValenceValueForm.FRACTION,
        ),
        (
            "Cr3plus_over_Cr2plus",
            ValenceDenominatorKind.OXIDATION_STATE,
            2,
            ValenceValueForm.RATIO,
        ),
        (
            "log10_Cr3plus_over_Cr2plus",
            ValenceDenominatorKind.OXIDATION_STATE,
            2,
            ValenceValueForm.LOG10_RATIO,
        ),
    ),
)
def test_printed_valence_forms_parse_into_typed_identity(
    printed, denominator_kind, denominator_state, value_form
):
    basis = parse_valence_basis(printed, species_formula="Cr")
    identity = _identity(basis)

    assert basis.numerator_oxidation_states == (2 if value_form is ValenceValueForm.FRACTION else 3,)
    assert basis.denominator_kind is denominator_kind
    assert basis.denominator_oxidation_state == denominator_state
    assert basis.value_form is value_form
    assert "valence_basis" in profile_for(identity).required


def test_average_oxidation_state_has_total_element_basis_and_round_trips():
    basis = parse_valence_basis(
        "average_oxidation_state", species_formula="V"
    )
    quantity, reason = map_quantity(
        "concentration_series", {"quantity": "average_oxidation_state"}
    )
    identity = Identity(
        quantity=Quantity.VALENCE_RATIO,
        species=Species("V", Phase.L),
        valence_basis=State.of(basis),
    )

    assert basis.numerator_oxidation_states == ()
    assert basis.denominator_kind is ValenceDenominatorKind.TOTAL_ELEMENT
    assert basis.denominator_oxidation_state is None
    assert basis.value_form is ValenceValueForm.AVERAGE_OXIDATION_STATE
    assert quantity.is_value and quantity.value is Quantity.VALENCE_RATIO
    assert reason is None
    payload = to_plain(identity)
    assert isinstance(payload, dict)
    decoded = _identity_from_plain(payload)
    assert decoded.species.formula == "V"
    assert decoded.valence_basis == identity.valence_basis


def test_malformed_valence_form_has_typed_refusal():
    with pytest.raises(IdentityRefusal) as refused:
        parse_valence_basis("Cr2plus_over_total_Fe", species_formula="Cr")

    assert refused.value.reason is RefusalReason.INVALID_SOURCE


def test_multi_state_fraction_numerator_has_typed_refusal():
    with pytest.raises(IdentityRefusal) as refused:
        ValenceBasis(
            numerator_oxidation_states=(3, 4, 5),
            denominator_kind=ValenceDenominatorKind.TOTAL_ELEMENT,
            denominator_oxidation_state=None,
            value_form=ValenceValueForm.FRACTION,
        )

    assert refused.value.reason is RefusalReason.INVALID_IDENTITY
    assert refused.value.field == "valence_basis.numerator_oxidation_states"


def test_sulfide_to_sulfate_printed_ratio_accepts_negative_state():
    sulfide_to_sulfate = parse_valence_basis(
        "S2minus_over_S6plus", species_formula="S"
    )
    sulfate_fraction = parse_valence_basis(
        "S6plus_over_sum_S", species_formula="S"
    )

    assert sulfide_to_sulfate.numerator_oxidation_states == (-2,)
    assert sulfide_to_sulfate.denominator_kind is ValenceDenominatorKind.OXIDATION_STATE
    assert sulfide_to_sulfate.denominator_oxidation_state == 6
    assert sulfide_to_sulfate.value_form is ValenceValueForm.RATIO
    assert sulfate_fraction.numerator_oxidation_states == (6,)
    assert sulfate_fraction.denominator_kind is ValenceDenominatorKind.TOTAL_ELEMENT
    assert sulfate_fraction.denominator_oxidation_state is None
    assert sulfate_fraction.value_form is ValenceValueForm.FRACTION


def test_sulfur_valence_forms_are_recognized_and_migrated_as_printed(tmp_path: Path):
    printed_values = (
        ("S2minus_over_S6plus", "0.32"),
        ("S2minus_over_total_S", "0.50"),
        ("log10_S2minus_over_S6plus", "-2.0"),
    )
    for printed, _value in printed_values:
        quantity, reason = map_quantity(
            "concentration_series", {"quantity": printed}
        )
        assert quantity.is_value and quantity.value is Quantity.VALENCE_RATIO
        assert reason is None

    doc = yaml.safe_load(SOSSI_CR.read_text(encoding="utf-8"))
    observations = []
    for index, (printed, value) in enumerate(printed_values):
        observations.append(
            {
                "observation_id": f"fixture_sulfur_valence_{index}",
                "type": "concentration_series",
                "locator": {"page": 1, "table": "1"},
                "phase": "liquid",
                "units": "fraction",
                "values": {
                    "method_class": "measured_direct",
                    "quantity": printed,
                    "admission_status": "admitted",
                    "value": value,
                },
            }
        )
    doc["species"] = {"S": {"observations": observations}}
    extract = tmp_path / SOSSI_CR.name
    extract.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    migrator = Migrator(ROOT)
    migrator._migrate_extract(extract)
    migrator.finalize()
    rows = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
        if "fixture_sulfur_valence_" in row.observation_id
    }

    assert set(rows) == {
        "fixture_sulfur_valence_0",
        "fixture_sulfur_valence_1",
        "fixture_sulfur_valence_2",
    }
    assert [rows[f"fixture_sulfur_valence_{i}"].value.point for i in range(3)] == [
        Decimal("0.32"),
        Decimal("0.50"),
        Decimal("-2.0"),
    ]
    assert all(
        rows[f"fixture_sulfur_valence_{i}"].identity.quantity.value
        is Quantity.VALENCE_RATIO
        for i in range(3)
    )


def test_fe_plus_spelling_stays_on_existing_fe_ratio_quantity():
    quantity, reason = map_quantity(
        "ratio", {"quantity": "Fe3plus_over_Fe2plus"}
    )

    assert quantity.is_value
    assert quantity.value is Quantity.FE3_FE2_RATIO
    assert reason is None


def test_identity_requires_valence_basis_only_for_valence_ratio():
    species = Species("Cr", Phase.L)
    basis = State.of(parse_valence_basis("Cr2plus_over_total_Cr"))

    with pytest.raises(IdentityRefusal) as missing:
        Identity(quantity=Quantity.VALENCE_RATIO, species=species)
    assert missing.value.reason is RefusalReason.INVALID_IDENTITY

    with pytest.raises(IdentityRefusal) as extraneous:
        Identity(quantity=Quantity.ACTIVITY, species=species, valence_basis=basis)
    assert extraneous.value.reason is RefusalReason.INVALID_IDENTITY

    old_fe_identity = Identity(
        quantity=Quantity.FE3_FE2_RATIO,
        species=Species("Fe", Phase.L),
    )
    assert "valence_basis" not in to_plain(old_fe_identity)


def test_valence_basis_serializes_and_round_trips_as_a_typed_sibling():
    identity = _identity(parse_valence_basis("Cr2plus_over_total_Cr"))
    payload = to_plain(identity)
    assert isinstance(payload, dict)
    assert "valence_basis" in payload

    decoded = _identity_from_plain(payload)
    assert decoded.valence_basis == identity.valence_basis


def test_valence_basis_participates_in_identity_and_same_row_comparison():
    fraction = _identity(parse_valence_basis("Cr2plus_over_total_Cr"))
    ratio = _identity(parse_valence_basis("Cr3plus_over_Cr2plus"))

    for compare in (identity_equal, _same_row_redox_identity_equal):
        outcome = compare(fraction, ratio)
        assert outcome.kind is IdentityEqualKind.IDENTITY_MISMATCH
        assert "valence_basis" in outcome.fields


def _migrate_sossi_cr():
    migrator = Migrator(ROOT)
    migrator._migrate_extract(SOSSI_CR)
    migrator.finalize()
    return migrator.result


def test_refused_printed_valence_forms_do_not_select_scalar(tmp_path: Path):
    doc = yaml.safe_load(SOSSI_CR.read_text(encoding="utf-8"))
    template = doc["species"]["Cr"]["observations"][0]
    printed_forms = (
        "log10_Cr2plus_over_total_Cr",
        "not_a_plus_over_real",
    )
    observations = []
    for index, printed in enumerate(printed_forms):
        row = copy.deepcopy(template)
        row.update(
            {
                "observation_id": f"fixture_refused_valence_{index}",
                "phase": "liquid",
                "values": {
                    "method_class": "measured_direct",
                    "quantity": printed,
                    "admission_status": "admitted",
                    "value": "0.32",
                },
            }
        )
        observations.append(row)
    doc["species"]["Cr"]["observations"] = observations
    extract = tmp_path / SOSSI_CR.name
    extract.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    migrator = Migrator(ROOT)
    migrator._migrate_extract(extract)
    migrator.finalize()
    rows = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
        if "fixture_refused_valence_" in row.observation_id
    }

    assert set(rows) == {
        "fixture_refused_valence_0",
        "fixture_refused_valence_1",
    }
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in rows.values())


def test_sossi_2018_quoted_cr_rows_migrate_as_numeric_valence_fractions():
    result = _migrate_sossi_cr()
    rows = [
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.VALENCE_RATIO
    ]

    assert len(rows) == 2
    assert {row.value.point for row in rows if row.value.kind is ValueKind.POINT} == {
        Decimal("0.32"),
        Decimal("0.91"),
    }
    assert all(row.value.kind is ValueKind.POINT for row in rows)
    assert all(row.identity.species.phase.value is Phase.L for row in rows)
    assert all(
        row.identity.valence_basis.value
        == ValenceBasis(
            numerator_oxidation_states=(2,),
            denominator_kind=ValenceDenominatorKind.TOTAL_ELEMENT,
            denominator_oxidation_state=None,
            value_form=ValenceValueForm.FRACTION,
        )
        for row in rows
    )
    assert all(row.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED for row in rows)
    assert all(row.admission.status is AdmissionStatus.ADMITTED for row in rows)
    assert all(
        row.evidence.attribution
        == "Berry, O'Neill, Scott, Foran and Shelley 2006 Am. Mineral. 91:1901-1908 (ref. 34)"
        for row in rows
    )
    control_by_value = {
        row.value.point: row.point_conditions["fO2_control"].state.value
        for row in rows
    }
    assert control_by_value == {
        Decimal("0.32"): "near FMQ",
        Decimal("0.91"): "~1 log unit below IW",
    }
    for row in rows:
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1673.15")
        point_temperature = row.point_conditions["temperature_K"]
        assert point_temperature.state.value == Decimal("1673.15")
        assert point_temperature.locator is not None
        control = row.point_conditions["fO2_control"]
        assert control.state.is_value
        assert control.state.value in {"near FMQ", "~1 log unit below IW"}
        assert control.locator is not None
        assert control.locator.published_page == 10921


def test_jayasuriya_2004_mossbauer_cells_and_conditions_migrate():
    doc = yaml.safe_load(JAYASURIYA_FE.read_text(encoding="utf-8"))
    observations = doc["species"]["Fe"]["observations"]
    assert sum(len(obs["values"]["points"]) for obs in observations) == 38
    assert [
        point["gas_mix_as_printed"]
        for obs in observations[:2]
        for point in obs["values"]["points"]
        if point["sample_as_published"] == "FeV9"
    ] == ["O2 100.0 × 200 SCCM", "O2 100.0 × 200 SCCM"]
    rt_fractions = [
        observations[0]["values"]["points"][i]["Fe3_over_sum_Fe"] for i in (1, 15)
    ]
    assert rt_fractions == [
        0.025,
        0.851,
    ]
    high_fe_fractions = [
        observations[2]["values"]["points"][i]["Fe3_over_sum_Fe"] for i in (0, 14)
    ]
    assert high_fe_fractions == [
        0.846,
        0.218,
    ]
    assert [
        observations[0]["locator"]["published_page"],
        observations[2]["locator"]["published_page"],
    ] == [1602, 1603]

    migrator = Migrator(ROOT)
    migrator._migrate_extract(JAYASURIYA_FE)
    migrator.finalize()
    rows = [
        row
        for row in migrator.result.observations.values()
        if "jayasuriya_2004" in row.observation_id
    ]
    assert len(rows) == 38
    assert migrator.result.registry_issues == []
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in rows) == 3
    assert all(
        row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.FE3_FE2_RATIO
        and row.identity.temperature_K.is_value
        and row.identity.fO2_Pa.is_value
        and row.identity.composition.is_value
        and isinstance(row.identity.composition.value, Composition)
        and row.point_conditions["temperature_K"].state.is_value
        and row.point_conditions["fO2_log"].state.is_value
        for row in rows
    )
    fe_free_analysis = {
        "SiO2": Decimal("50.47"),
        "Al2O3": Decimal("15.12"),
        "MgO": Decimal("10.46"),
        "CaO": Decimal("23.94"),
    }
    table4 = [
        (Decimal("1.75"), Decimal("0.0134"), Decimal("0.0002")),
        (Decimal("3.67"), Decimal("0.0282"), Decimal("0.0006")),
        (Decimal("6.44"), Decimal("0.0494"), Decimal("0.0010")),
        (Decimal("9.81"), Decimal("0.0766"), Decimal("0.0005")),
        (Decimal("18.75"), Decimal("0.1498"), Decimal("0.0011")),
        (Decimal("27.06"), Decimal("0.2201"), Decimal("0.0009")),
        (Decimal("1.49"), Decimal("0.0114"), Decimal("0.0004")),
        (Decimal("2.79"), Decimal("0.0214"), Decimal("0.0007")),
        (Decimal("5.36"), Decimal("0.0414"), Decimal("0.0007")),
        (Decimal("7.96"), Decimal("0.0620"), Decimal("0.0010")),
        (Decimal("16.36"), Decimal("0.1298"), Decimal("0.0009")),
        (Decimal("3.52"), Decimal("0.0271"), Decimal("0.0008")),
        (Decimal("5.83"), Decimal("0.0451"), Decimal("0.0006")),
        (Decimal("7.96"), Decimal("0.0619"), Decimal("0.0007")),
        (Decimal("26.82"), Decimal("0.2186"), Decimal("0.0011")),
    ]
    high_fe_source_points = observations[2]["values"]["points"]
    assert len(high_fe_source_points) == len(table4)
    high_fe_rows = [
        row
        for row in rows
        if "jayasuriya_2004_rt_high_fe" in row.observation_id
    ]
    assert len(high_fe_rows) == len(table4)
    for source_point, migrated_row, table_entry in zip(
        high_fe_source_points, high_fe_rows, table4, strict=True
    ):
        feot, printed_x, printed_x_uncertainty = table_entry
        closed = {
            oxide: weight * (Decimal(100) - feot) / Decimal(100)
            for oxide, weight in fe_free_analysis.items()
        }
        closed["FeO"] = feot
        assert Decimal(str(source_point["FeOT_wt_percent"])) == feot
        assert source_point["composition_wt_pct"] == {
            **{oxide: float(weight) for oxide, weight in closed.items() if oxide != "FeO"},
            "FeOT": float(feot),
        }
        composition = migrated_row.identity.composition.value
        assert composition == wt_pct_to_mole_fraction(closed)
        mole_amounts = {
            oxide: fraction * oxide_molar_mass(oxide)
            for oxide, fraction in composition.components
        }
        recovered_feo = Decimal(100) * mole_amounts["FeO"] / sum(mole_amounts.values())
        assert abs(recovered_feo - feot) <= Decimal("0.002")
        mole_fractions = composition.as_map()
        single_cation_x = mole_fractions["FeO"] / (
            mole_fractions["SiO2"]
            + Decimal(2) * mole_fractions["Al2O3"]
            + mole_fractions["MgO"]
            + mole_fractions["CaO"]
            + mole_fractions["FeO"]
        )
        assert abs(single_cation_x - printed_x) <= printed_x_uncertainty
    low_fe = next(
        row
        for row in rows
        if "jayasuriya_2004_rt_1pct" in row.observation_id
    )
    assert low_fe.identity.composition.value == wt_pct_to_mole_fraction(
        {
            "SiO2": Decimal("50.47"),
            "Al2O3": Decimal("15.12"),
            "MgO": Decimal("10.46"),
            "CaO": Decimal("23.94"),
            "Fe2O3": Decimal("1.0"),
        }
    )
    d60 = [
        row
        for row in rows
        if row.point_conditions.get("total_pressure_Pa") is not None
    ]
    assert len(d60) == 2
    assert all(
        row.identity.total_pressure_Pa.is_unknown
        and row.point_conditions["total_pressure_Pa"].state.value.point
        == Decimal("1100000000")
        for row in d60
    )


def test_valence_ratio_scores_redox_and_counts_each_engine_refusal():
    result = _migrate_sossi_cr()
    context = ScoreContext(
        result.works,
        result.experiments,
        result.observations,
        origins={key: SOSSI_CR.name for key in result.observations},
        benches=result.benches,
    )

    residuals, _candidates = score_store(
        context,
        engines=SCORE_ENGINE_SET,
        rail=Rail.REDOX,
    )

    assert len(residuals) == 2 * len(SCORE_ENGINE_SET)
    assert len({residual.reference for residual in residuals}) == 2
    assert all(
        sum(residual.key.endswith(engine.value) for residual in residuals) == 2
        for engine in SCORE_ENGINE_SET
    )
    for residual in residuals:
        assert residual.rail is Rail.REDOX
        assert residual.numeric is None
        assert residual.refusal is not None
        assert residual.refusal.reason is RefusalReason.UNSUPPORTED
