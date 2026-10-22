from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery import migrate as migrate_module
from simulator.battery.enums import PerBasis, Phase, Quantity
from simulator.battery.migrate import Migrator, compilation_column_series_from_record


ROOT = Path(__file__).resolve().parents[2]
SOURCE_IDS = (
    "pankratz-1984-usbm-b677",
    "pankratz-1987-usbm-b689",
    "pankratz-1994-usbm-b696",
    "kelley-king-1961-usbm-b592",
)


@pytest.fixture(scope="module")
def migrated_compilations():
    observations = {}
    for source_id in SOURCE_IDS:
        migrator = Migrator(ROOT)
        migrator.migrate_compilations(
            ROOT / "data/literature/compilations" / source_id
        )
        observations[source_id] = migrator.result.observations
    return observations


def _assert_molar_gas_observation(observations, source_id, observation_id, formula):
    observation = observations[source_id][observation_id]
    assert observation.identity.species.formula == formula
    assert observation.identity.species.phase.is_value
    assert observation.identity.species.phase.value is Phase.G
    assert observation.identity.per is not None
    assert observation.identity.per.is_value
    assert observation.identity.per.value is PerBasis.MOL_SPECIES


def test_pankratz_1984_gas_row_uses_mol_species(migrated_compilations):
    _assert_molar_gas_observation(
        migrated_compilations,
        "pankratz-1984-usbm-b677",
        "pankratz-1984-usbm-b677:table-0008:delta_fG",
        "Ag",
    )


def test_pankratz_vaporock_quantities_use_mol_species(migrated_compilations):
    for source_id, record_id, formula, entropy_key in (
        ("pankratz-1984-usbm-b677", "table-0008", "Ag", "S"),
        ("pankratz-1987-usbm-b689", "page-0003", "AgS", "S:entropy"),
        ("pankratz-1994-usbm-b696", "page-0006", "AgO", "S:entropy"),
    ):
        for quantity_key, expected_quantity in (
            ("cp", Quantity.CP),
            (entropy_key, Quantity.S),
            ("H_minus_H298", Quantity.H_MINUS_H298),
        ):
            observation_id = f"{source_id}:{record_id}:{quantity_key}"
            _assert_molar_gas_observation(
                migrated_compilations,
                source_id,
                observation_id,
                formula,
            )
            identity_quantity = migrated_compilations[source_id][
                observation_id
            ].identity.quantity
            assert identity_quantity.is_value
            assert identity_quantity.value is expected_quantity


def test_converter_refusal_does_not_assign_mol_species(monkeypatch):
    source_id = "pankratz-1984-usbm-b677"
    record_id = "table-0008"
    original_series = compilation_column_series_from_record
    assert migrate_module._convert_compilation_amount(
        Decimal("1"), Quantity.CP, "gram-atom"
    ) is None

    def with_refused_unit(doc):
        series = original_series(doc)
        if doc.get("record_id") == record_id:
            return tuple(
                replace(item, source_units=("gram-atom",) * len(item.source_units))
                for item in series
            )
        return series

    monkeypatch.setattr(
        migrate_module, "compilation_column_series_from_record", with_refused_unit
    )
    migrator = Migrator(ROOT)
    migrator.migrate_compilations(ROOT / "data/literature/compilations" / source_id)
    cp = migrator.result.observations[f"{source_id}:{record_id}:cp"]
    assert cp.identity.per is not None
    assert cp.identity.per.is_unknown


def test_pankratz_1987_gas_row_uses_mol_species(migrated_compilations):
    _assert_molar_gas_observation(
        migrated_compilations,
        "pankratz-1987-usbm-b689",
        "pankratz-1987-usbm-b689:page-0003:delta_fG",
        "AgS",
    )
    _assert_molar_gas_observation(
        migrated_compilations,
        "pankratz-1987-usbm-b689",
        "pankratz-1987-usbm-b689:page-0003",
        "AgS",
    )
    log_k = migrated_compilations["pankratz-1987-usbm-b689"][
        "pankratz-1987-usbm-b689:page-0003:log10_Kf"
    ]
    assert log_k.identity.per is None or log_k.identity.per.is_unknown


def test_pankratz_1994_gas_row_uses_mol_species(migrated_compilations):
    _assert_molar_gas_observation(
        migrated_compilations,
        "pankratz-1994-usbm-b696",
        "pankratz-1994-usbm-b696:page-0006:delta_fG",
        "AgO",
    )
    _assert_molar_gas_observation(
        migrated_compilations,
        "pankratz-1994-usbm-b696",
        "pankratz-1994-usbm-b696:page-0006",
        "AgO",
    )


def test_kelley_king_table_6_gas_row_uses_mol_species(migrated_compilations):
    _assert_molar_gas_observation(
        migrated_compilations,
        "kelley-king-1961-usbm-b592",
        "kelley-king-1961-usbm-b592:table-006-0003:S:entropy_recommended",
        "Al(g)",
    )


def test_kelley_king_table_4_without_printed_basis_stays_unknown(
    migrated_compilations,
):
    # Table 4 labels the entropy columns but prints no denominator.
    observation = migrated_compilations["kelley-king-1961-usbm-b592"][
        "kelley-king-1961-usbm-b592:table-004-0001"
    ]
    assert observation.identity.species.formula == "$\\text{HCl}(g)$"
    assert observation.identity.species.phase.value is Phase.G
    assert observation.identity.per is None or observation.identity.per.is_unknown
