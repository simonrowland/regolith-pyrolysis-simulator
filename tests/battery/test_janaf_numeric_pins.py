from decimal import Decimal

import pytest

import simulator.reference_data.janaf as janaf_reference
from simulator.battery.generators.janaf import (
    _interpolate_formation_gibbs,
    _janaf_gas_formation_gibbs_J_per_mol,
    janaf_fusion_enthalpy,
    janaf_fusion_entropy,
    janaf_fusion_energy,
    janaf_tabulated_gas_thermo,
    janaf_condensed_element_gas_delta_fG_kJ_per_mol,
)


def test_mgo_janaf_fusion_gibbs_interpolation_numeric_pin() -> None:
    # These synthetic points isolate linear interpolation; they are not
    # printed JANAF values or evidence for the scorer's source tables.
    solid = ((Decimal("1800"), Decimal("-345.918")), (Decimal("1900"), Decimal("-342.212")))
    liquid = ((Decimal("1800"), Decimal("-317.447")), (Decimal("1900"), Decimal("-313.750")))
    assert _interpolate_formation_gibbs(solid, Decimal("1873")) == Decimal("-343.21262")
    assert _interpolate_formation_gibbs(liquid, Decimal("1873")) == Decimal("-314.74819")


def test_mgo_janaf_fusion_enthalpy_uses_printed_3000_k_values() -> None:
    # Mg-008/Mg-009 print −709.524/−632.496 kJ/mol at 3000 K.
    enthalpy, *_ = janaf_fusion_enthalpy("MgO", Decimal("3000"))
    assert enthalpy == Decimal("77028")


def test_mgo_janaf_fusion_entropy_uses_printed_3000_k_values() -> None:
    # Mg-008/Mg-009 print S=142.489/167.293 J/(mol K) at 3000 K.
    entropy, _crossing = janaf_fusion_entropy("MgO", Decimal("3000"))
    assert entropy == Decimal("24.804")


def test_janaf_gas_formation_gibbs_reads_direct_couple_species() -> None:
    temperature = Decimal("1682.15")
    assert _janaf_gas_formation_gibbs_J_per_mol("CO2", temperature) == Decimal(
        "-396340.251500"
    )
    assert _janaf_gas_formation_gibbs_J_per_mol("CO", temperature) == Decimal(
        "-259266.75000"
    )
    assert _janaf_gas_formation_gibbs_J_per_mol("O2", temperature) == Decimal("0.00")


def test_battery_interpolation_callers_keep_current_values_and_provenance() -> None:
    temperature = Decimal("1850")
    fusion = janaf_fusion_energy("MgO", temperature)
    assert (
        fusion.delta_g_fus_kJ_per_mol,
        fusion.melting_temperature_K,
        fusion.accepted_melting_temperature_K,
        fusion.source_sha256,
    ) == (
        Decimal("28.9635"),
        Decimal("3104.968203497615262321144674"),
        Decimal("3100"),
        (
            "2efaf6db392a6ee21a2720b84bad3cd2e9b4cbbdd2afce0a1145fbecdae796d8",
            "f31771f1a45e7749f20bcdc70557a32e85dcb9d79a8b53f7c59c6a3cda482a41",
        ),
    )
    assert janaf_fusion_enthalpy("MgO", temperature)[0] == Decimal("68631.5000")
    assert janaf_fusion_entropy("MgO", temperature)[0] == Decimal("21.442")

    gas_temperature = Decimal("1682.15")
    gas = janaf_tabulated_gas_thermo("CO", gas_temperature)
    assert (gas.heat_capacity, gas.entropy, gas.enthalpy_increment) == (
        Decimal("35.668945"),
        Decimal("252.479797"),
        Decimal("45.30954"),
    )
    assert _janaf_gas_formation_gibbs_J_per_mol("CO", gas_temperature) == Decimal(
        "-259266.75000"
    )
    assert janaf_condensed_element_gas_delta_fG_kJ_per_mol(
        "O", gas_temperature
    ) == Decimal("142.750311")


def test_reference_formation_gibbs_reader_refuses_a_missing_grid_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_loader = janaf_reference.load_table_document
    source_document = original_loader(
        janaf_reference.TABLES_DIR / "C-093.yaml"
    )
    source_digest = source_document["extraction"]["source_sha256"]
    missing_node_table = {
        "extraction": {"source_sha256": source_digest},
        "table": {
            "table_id": "C-093",
            "units_as_published": {"formation_gibbs_energy": "kJ mol^-1"},
            "values": [
                {
                    "temperature": {"as_published": "100"},
                    "formation_gibbs_energy": {"as_published": "-10"},
                },
                {
                    "temperature": {"as_published": "200"},
                    "formation_gibbs_energy": {"as_published": None},
                },
                {
                    "temperature": {"as_published": "300"},
                    "formation_gibbs_energy": {"as_published": "-30"},
                },
            ],
        },
    }
    monkeypatch.setattr(
        janaf_reference,
        "load_table_document",
        lambda path: (
            missing_node_table
            if path.name == "C-093.yaml"
            else original_loader(path)
        ),
    )
    janaf_reference._compiled_table_by_id.cache_clear()
    with pytest.raises(ValueError, match=r"missing grid node 200 K"):
        janaf_reference._formation_gibbs_energy_kj_per_mol_at("C-093", 150)
