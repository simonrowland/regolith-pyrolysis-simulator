"""Rendered-page pins and isolated migration for Cottrell et al. (2009)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.migrate import (
    FERRIC_FRACTION_RELATION,
    Migrator,
    fe3_fe2_ratio_from_ferric_fraction,
)

REPO = Path(__file__).resolve().parents[2]
EXTRACT = REPO / "data/literature/extracts/cottrell-2009-iron-xanes.yaml"
SOURCE_ID = "cottrell-2009-iron-xanes"


def _migrate():
    migrator = Migrator()
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator.result


def _observations_by_id(doc: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in doc["species"]["Fe"]["observations"]
    }


def test_table_cells_match_render_and_cover_all_23_glasses() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _observations_by_id(doc)

    # PDF p. 6 / published p. 172, Table 2, LW_-20 row.
    basalt_moss = rows["cottrell_2009_basalt_mossbauer"]["values"]["series"]
    assert basalt_moss[3]["sample"] == "LW_-20"
    assert basalt_moss[3]["Fe3_over_FeT"] == "0.088"
    assert basalt_moss[3]["cross_session_drift_corrected_centroid_eV"] == "7112.112"

    # PDF p. 6 / published p. 172, section 4.1: Moore et al. print
    # DT-39 as 0.31 ± 0.08 (not 0.09).
    moore_dt39 = rows["cottrell_2009_moore_wet_chemistry_quotes"]["values"]["series"][1]
    assert moore_dt39["Fe3_over_FeT"] == "0.315"
    assert moore_dt39["sigma"] == "0.08"

    # PDF p. 9 / published p. 175, Table 3, basalt centroid fit.
    basalt_fit = rows["cottrell_2009_basalt_centroid_calibration"]["values"]["fit"]
    assert basalt_fit["centroid_a_as_published"] == "22.99081"

    # PDF p. 2 / published p. 168, Table 1, AII SiO2 column.
    assert doc["source_material_compositions"]["AII"]["values_as_published"]["SiO2"] == "50.81"

    # The Table 2 method series cover the full printed row set; repeats in the
    # Kress and Moore quote series do not create additional glass samples.
    sample_rows = [
        item
        for obs in rows.values()
        for item in obs.get("values", {}).get("series", [])
        if isinstance(item, dict) and item.get("locator", {}).get("table") == "2"
    ]
    assert len({item["sample"] for item in sample_rows}) == 23


def test_real_migration_preserves_fe_ratio_lineage_and_row_conditions() -> None:
    result = _migrate()
    prefix = f"{SOURCE_ID}::"
    assert len(result.observations) == 43
    ratios = {
        oid: obs
        for oid, obs in result.observations.items()
        if oid.startswith(prefix)
        and obs.identity.quantity.is_value
        and obs.identity.quantity.value is Quantity.FE3_FE2_RATIO
    }
    assert len(ratios) == 41
    for obs in ratios.values():
        assert obs.identity.temperature_K is not None, obs.observation_id
        assert obs.point_conditions, obs.observation_id
        assert all(
            condition.locator is not None
            for condition in obs.point_conditions.values()
        ), obs.observation_id

    missing_conditions: list[str] = []
    required_conditions = {
        "temperature_C",
        "fO2_log",
        "fO2_delta_QFM",
        "total_pressure_Pa",
        "composition",
        "fO2_control",
    }
    for obs in ratios.values():
        point_conditions = obs.point_conditions or {}
        if not required_conditions.issubset(point_conditions):
            missing_conditions.append(obs.observation_id)
            continue
        for condition in required_conditions:
            assert point_conditions[condition].locator is not None, obs.observation_id
        temperature = point_conditions["temperature_C"]
        if temperature.state.is_unknown:
            assert temperature.locator is not None, obs.observation_id
        else:
            assert temperature.state.is_value, obs.observation_id
            assert "temperature_K" in point_conditions, obs.observation_id
    assert not missing_conditions

    # PDF p. 6 / published p. 172, Table 2, LW_-20: the printed fraction
    # remains the located input to the existing FE3_FE2_RATIO reduction.
    moss = next(
        obs for oid, obs in ratios.items()
        if "cottrell_2009_basalt_mossbauer::" in oid and "row=lw_-20:" in oid
    )
    assert moss.value.kind is ValueKind.POINT
    assert moss.value.point == fe3_fe2_ratio_from_ferric_fraction(Decimal("0.088"))
    assert moss.identity.quantity.value is Quantity.FE3_FE2_RATIO
    assert moss.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert moss.derivation is not None
    assert FERRIC_FRACTION_RELATION in moss.derivation.relation
    printed = dict(moss.derivation.parameters)["original_Fe3_over_FeT"]
    assert printed.state.value == Decimal("0.088")
    assert printed.locator.page == 6
    lw_conditions = moss.point_conditions
    assert lw_conditions is not None
    assert lw_conditions["temperature_C"].state.value == Decimal("1350")
    assert lw_conditions["temperature_K"].state.value == Decimal("1623.15")
    assert lw_conditions["fO2_log"].state.value == Decimal("-8.72")
    assert moss.identity.fO2_Pa.is_value
    assert moss.identity.fO2_Pa.value == Decimal(
        "0.0001905460717963247182688014184"
    )
    assert lw_conditions["fO2_delta_QFM"].state.value == Decimal("-2.00")
    assert lw_conditions["total_pressure_Pa"].state.value == Decimal("101325")
    assert lw_conditions["composition"].state.value["SiO2"] == Decimal("49.42")
    assert lw_conditions["composition"].locator.page == 2

    # The paper identifies 13 basalt and seven rhyolite calibration glasses.
    basalt_fit = result.observations[f"{prefix}cottrell_2009_basalt_centroid_calibration"]
    rhyolite_fit = result.observations[f"{prefix}cottrell_2009_rhyolite_centroid_calibration"]
    assert len(basalt_fit.derived_from or ()) == 13
    assert set(basalt_fit.derived_from or ()).issubset(result.observations)
    assert not set(basalt_fit.derived_from or ()) & {
        oid for oid in ratios if any(tag in oid for tag in ("row=aii_-35", "row=lw_-30", "row=aii_-25"))
    }
    rhyolite_parents = {
        f"{prefix}cottrell_2009_rhyolite_mossbauer::T=1823.15:row=568_2:h=59a4f4d57725",
        f"{prefix}cottrell_2009_rhyolite_mossbauer::point:1",
        f"{prefix}cottrell_2009_rhyolite_mossbauer::point:2",
        f"{prefix}cottrell_2009_moore_wet_chemistry_quotes::point:2",
        f"{prefix}cottrell_2009_moore_wet_chemistry_quotes::point:3",
        f"{prefix}cottrell_2009_moore_wet_chemistry_quotes::point:4",
        f"{prefix}cottrell_2009_moore_wet_chemistry_quotes::point:5",
    }
    assert len(rhyolite_fit.derived_from or ()) == 7
    assert set(rhyolite_fit.derived_from or ()) == rhyolite_parents
    assert rhyolite_parents.issubset(result.observations)
    moore_dt39_observation = result.observations[
        f"{prefix}cottrell_2009_moore_wet_chemistry_quotes::point:1"
    ]
    assert moore_dt39_observation.uncertainty.verbatim["sigma"] == "0.08"
    assert moore_dt39_observation.identity.fO2_Pa.is_unknown
    assert len(
        [
            obs
            for obs in ratios.values()
            if obs.point_conditions["fO2_log"].state.is_value
            and obs.identity.fO2_Pa.is_value
        ]
    ) == 33

    below_detection = [
        obs
        for oid, obs in ratios.items()
        if "cottrell_2009_basalt_mossbauer::" in oid
        and any(row in oid for row in ("row=aii_-35", "row=lw_-30", "row=aii_-25"))
    ]
    assert len(below_detection) == 3
    assert all(obs.value.kind is ValueKind.UNAVAILABLE for obs in below_detection)
    assert all(
        "b.d. (below detection)" in (obs.value.unavailable_reason or "")
        for obs in below_detection
    )
    assert rhyolite_fit.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert set(rhyolite_fit.derivation.inputs) == rhyolite_parents

    assert not result.validation.hard_issues
    assert sum(
        obs.value.kind is ValueKind.UNAVAILABLE for obs in ratios.values()
    ) == 3
    assert sum(obs.value.kind is ValueKind.UNAVAILABLE for obs in result.observations.values()) == 5
