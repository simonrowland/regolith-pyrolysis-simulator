from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Engine,
    NoticeKind,
    Quantity,
    UncertaintyKind,
)
from simulator.battery.records import Composition, Located, State
from simulator.battery import score as score_module
from simulator.battery.score import (
    _printed_uncertainty_band,
    fusion_comparison_reference,
    temperature_of,
)
from tests.battery.test_migrate import _migrate_real_extract
from tests.battery.test_t1243_feo_reference import (
    _independent_banya_feto_to_feo_rs_factor,
)


ROOT_SOURCES = {
    "saito-2021-fexo-cao-silica-activity": 84,
    "snyder-carmichael-1992-olivine-liquid-feo-activity": 28,
}
TOTAL_IRON_SOURCES = {
    "banya-chiba-hikosaka-1980-feto-binary-solid-iron": 69,
    "banya-hino-takezoe-1985-na-fe-silicate-redox": 19,
    "banya-hino-yuge-1985-tetsu-feto-sio2-mno-activity": 30,
}


def _migrate(tmp_path, source_id):
    return _migrate_real_extract(
        tmp_path / source_id,
        f"{source_id}.yaml",
        write=True,
        use_repository_index_row=True,
    )


def test_saito_liquid_wustite_rows_use_existing_feo_conversion_and_solid_rows_stay_typed(
    tmp_path,
) -> None:
    migration = _migrate(tmp_path, "saito-2021-fexo-cao-silica-activity")
    rows = tuple(migration.observations.values())
    assert len(rows) == ROOT_SOURCES["saito-2021-fexo-cao-silica-activity"]
    liquids = [
        row
        for row in rows
        if row.identity.reference_state.value.endmember.phase.value.value == "l"
    ]
    solids = [
        row
        for row in rows
        if row.identity.reference_state.value.endmember.phase.value.value == "cr"
    ]
    assert len(liquids) == len(solids) == 42

    for row in liquids:
        assert row.identity.species.formula == "FeO"
        assert row.identity.reference_state.value.endmember.formula == "Fe(1-x)O"
        assert row.identity.reference_state.value.component_basis == "Fe(1-x)O"
        assert temperature_of(row.identity) == Decimal("1573")
        assert row.point_conditions["fO2_log"].locator is not None
    converted = fusion_comparison_reference(liquids[0], engine=Engine.OPENIMCC)
    assert converted.identity.reference_state.value.endmember.formula == "FeO"
    assert converted.value.point != liquids[0].value.point

    for row in solids:
        assert row.identity.species.formula == "Fe(1-x)O"
        assert row.identity.reference_state.value.endmember.formula == "Fe(1-x)O"
        refused = fusion_comparison_reference(row, engine=Engine.OPENIMCC)
        assert any(
            notice.reason == "solid_wustite_reference_conversion_not_grounded"
            for notice in refused.notices
        )


def test_snyder_solid_fe0947o_rows_remain_typed_as_ungrounded(tmp_path) -> None:
    migration = _migrate(
        tmp_path, "snyder-carmichael-1992-olivine-liquid-feo-activity"
    )
    rows = tuple(
        row
        for row in migration.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value
        in {Quantity.ACTIVITY, Quantity.ACTIVITY_COEFFICIENT}
    )
    assert len(rows) == ROOT_SOURCES[
        "snyder-carmichael-1992-olivine-liquid-feo-activity"
    ]
    for row in rows:
        assert row.identity.reference_state.value.endmember.formula == "Fe0.947O"
        assert row.identity.reference_state.value.endmember.phase.value.value == "cr"
        result = fusion_comparison_reference(row, engine=Engine.OPENIMCC)
        assert any(
            notice.reason == "solid_wustite_reference_conversion_not_grounded"
            for notice in result.notices
        )


def test_total_iron_activity_rows_migrate_and_receive_source_ferric_bands(
    tmp_path,
) -> None:
    total_rows = 0
    mutation_checked = set()
    explicit_zero_ferric_rows = 0
    for source_id, expected_count in TOTAL_IRON_SOURCES.items():
        migration = _migrate(tmp_path, source_id)
        rows = [
            row
            for row in migration.observations.values()
            if row.identity.quantity.is_value
            and row.identity.quantity.value is Quantity.ACTIVITY
            and row.identity.species.formula == "FeO"
        ]
        assert len(rows) == expected_count
        total_rows += len(rows)
        assert not migration.validation.hard_issues

        for row in rows:
            standard = row.identity.reference_state.value
            assert row.identity.species.formula == "FeO", (
                source_id,
                row.observation_id,
            )
            assert standard.endmember.formula == "Fe(1-x)O"
            assert standard.component_basis == "Fe(1-x)O"
            assert row.uncertainty.kind is UncertaintyKind.DERIVED
            assert row.uncertainty.derivation.relation == "total_iron_basis"
            assert temperature_of(row.identity) is not None
            assert row.locator is not None
            located = row.point_conditions["composition"]
            assert isinstance(located, Located)
            assert located.locator is not None
            assert located.state.is_value
            composition = located.state.value
            assert isinstance(composition, Composition)
            assert composition.amount_basis is AmountBasis.MOLE_FRACTION
            composition_components = dict(composition.components)

            resolution = _printed_uncertainty_band(
                Quantity.ACTIVITY,
                row.uncertainty,
                row.value.point,
                source_observation=row,
            )
            assert resolution.refusal is None
            assert resolution.band is not None
            assert resolution.band.band_basis == "derived_bound"
            assert resolution.notice is not None
            assert resolution.notice.kind is NoticeKind.TOTAL_IRON_REPORTED_AS_FEO
            assert resolution.notice.authority is Authority.DERIVED
            assert resolution.notice.reason.startswith("total_iron_basis:")
            ferric = composition_components.get("Fe2O3")
            if ferric == 0:
                explicit_zero_ferric_rows += 1
                assert resolution.band.value == 0

            comparison = fusion_comparison_reference(row, engine=Engine.OPENIMCC)
            assert comparison.identity.reference_state.value.endmember.formula == "FeO"
            expected_value = row.value.point * (
                _independent_banya_feto_to_feo_rs_factor(
                    temperature_of(row.identity)
                )
            )
            assert comparison.value.point == expected_value
            if row.observation_id.endswith("bch80_t1_c_b_1_activity"):
                assert row.value.point == Decimal("0.985")
                assert expected_value.quantize(Decimal("1e-10")) == Decimal(
                    "0.7706234646"
                )
                assert resolution.band.value.quantize(
                    Decimal("0.0000000001")
                ) == Decimal("0.0441221941")

            if source_id not in mutation_checked and composition_components.get(
                "Fe2O3", Decimal(0)
            ) > 0:
                changed_components = tuple(
                    (name, amount)
                    for name, amount in composition.components
                    if name != "Fe2O3"
                )
                changed_composition = replace(
                    composition, components=changed_components
                )
                changed_conditions = dict(row.point_conditions)
                changed_conditions["composition"] = replace(
                    located, state=State.of(changed_composition)
                )
                changed_row = replace(row, point_conditions=changed_conditions)
                mutated = _printed_uncertainty_band(
                    Quantity.ACTIVITY,
                    changed_row.uncertainty,
                    changed_row.value.point,
                    source_observation=changed_row,
                )
                assert resolution.band.value > 0
                assert mutated.refusal == {
                    "reason": "total_iron_basis_ferric_analysis_missing"
                }
                assert mutated.band is None
                mutation_checked.add(source_id)
    assert total_rows == 118
    assert mutation_checked == set(TOTAL_IRON_SOURCES)
    assert explicit_zero_ferric_rows > 0


def test_total_iron_band_distinguishes_missing_from_explicit_zero_ferric(tmp_path):
    migration = _migrate(
        tmp_path, "banya-chiba-hikosaka-1980-feto-binary-solid-iron"
    )
    rows = [
        row
        for row in migration.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.ACTIVITY
    ]
    positive = next(
        row
        for row in rows
        if dict(row.point_conditions["composition"].state.value.components).get(
            "Fe2O3", Decimal(0)
        )
        > 0
    )
    located = positive.point_conditions["composition"]
    composition = located.state.value
    missing_ferric = replace(
        composition,
        components=tuple(
            (name, amount)
            for name, amount in composition.components
            if name != "Fe2O3"
        ),
    )
    missing_row = replace(
        positive,
        point_conditions={
            **positive.point_conditions,
            "composition": replace(located, state=State.of(missing_ferric)),
        },
    )
    missing = score_module._total_iron_basis_band(
        Quantity.ACTIVITY, missing_row.uncertainty, missing_row
    )
    assert missing.refusal == {
        "reason": "total_iron_basis_ferric_analysis_missing"
    }
    assert missing.band is None

    explicit_zero = next(
        row
        for row in rows
        if dict(row.point_conditions["composition"].state.value.components).get(
            "Fe2O3"
        )
        == 0
    )
    zero_band = score_module._total_iron_basis_band(
        Quantity.ACTIVITY, explicit_zero.uncertainty, explicit_zero
    )
    assert zero_band.refusal is None
    assert zero_band.band is not None
    assert zero_band.band.value == 0


def test_total_iron_band_numeric_pin_detects_a_doubled_width(tmp_path, monkeypatch):
    original = score_module._total_iron_basis_band

    def doubled_width(*args, **kwargs):
        resolution = original(*args, **kwargs)
        if resolution.band is not None:
            return replace(
                resolution,
                band=replace(
                    resolution.band, value=resolution.band.value * 2
                ),
            )
        return resolution

    monkeypatch.setattr(score_module, "_total_iron_basis_band", doubled_width)
    with pytest.raises(AssertionError, match="0.0441221941"):
        test_total_iron_activity_rows_migrate_and_receive_source_ferric_bands(
            tmp_path
        )
