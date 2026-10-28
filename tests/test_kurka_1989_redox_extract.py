from __future__ import annotations

import shutil
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity, RefusalReason
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kurka-kupcak-lesko-1989-ca-fe-si-redox-basicity"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def _extract_observations(document: dict, species: str) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"][species]["observations"]
    }


def test_kurka_table1_cells_and_isolated_redox_migration():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    iron = _extract_observations(document, "Fe")
    feo = _extract_observations(document, "FeO")
    fe2o3 = _extract_observations(document, "Fe2O3")
    basicity_context = document["species"]["Fe"]["context"][0]["values"]
    assert "quantity" not in basicity_context
    assert "simulator/battery/enums.py::Quantity" in basicity_context["schema_gap"]

    assert len(iron) == 16
    assert len(feo) == len(fe2o3) == 16
    assert [row["values"]["points"][0]["T_C"] for row in iron.values()] == [
        1400, 1400, 1400, 1500, 1500, 1500, 1500,
        1600, 1600, 1600, 1600, 1600, 1600, 1600, 1600, 1600,
    ]

    # Render-pinned input cells: PDF p. 636, Table 1 rows 1 and 9.
    assert feo["kurka_1989_table1_row_1_feo_mole_fraction"]["values"]["points"][0]["component_amount"] == "0.019"
    assert fe2o3["kurka_1989_table1_row_1_fe2o3_mole_fraction"]["values"]["points"][0]["component_amount"] == "0.130"
    assert fe2o3["kurka_1989_table1_row_16_fe2o3_mole_fraction"]["values"]["points"][0]["component_amount"] == "0.008"

    first = iron["kurka_1989_table1_row_1_ferric_fraction"]["values"]["points"][0]
    # Independent hand check from the two cells above: 2(.130)/(2(.130)+.019).
    expected_f3 = Decimal("0.260") / Decimal("0.279")
    assert Decimal(first["value"]) == expected_f3.quantize(Decimal("0.000001"))
    assert first["Fe2_fraction_of_total_Fe"] == "0.068100"
    assert first["j_as_published"] == "0.872483"
    assert first["j_definition_as_published"] == "x(Fe2O3)/(x(Fe2O3)+x(FeO)); oxide formula mole fractions"

    with tempfile.TemporaryDirectory(prefix="kurka-isolated-") as temp_dir:
        extract_dir = Path(temp_dir)
        shutil.copy2(EXTRACT, extract_dir / EXTRACT.name)
        migrator = Migrator()
        migrator.extracts_dir = extract_dir
        migrator.migrate_extracts()
        migrator.finalize()
        result = migrator.result

    migrated = list(result.observations.values())
    ratios = [row for row in migrated if row.identity.quantity.is_value and row.identity.quantity.value is Quantity.VALENCE_RATIO]
    assert len(migrated) == 48
    assert len(ratios) == 16
    assert Counter(row.identity.quantity.value for row in migrated if row.identity.quantity.is_value) == {
        Quantity.VALENCE_RATIO: 16,
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION: 32,
    }
    assert all(row.identity.quantity.is_value for row in migrated)
    assert all(row.identity.species.phase.is_value for row in migrated)
    assert all(row.identity.species.phase.value is Phase.GLASS for row in migrated)
    assert all(row.identity.temperature_K.is_value for row in migrated)
    assert all(row.identity.fO2_Pa.is_value for row in migrated)
    assert all(row.identity.composition.is_value for row in migrated)
    assert all(row.identity.total_pressure_Pa.is_unknown for row in migrated)
    assert all(row.evidence.class_.is_value for row in ratios)
    assert all(row.evidence.class_.value is EvidenceClass.MEASURED_REDUCED for row in ratios)
    assert all(row.value.point is not None for row in ratios)

    migrated_by_suffix = {
        row.observation_id.rsplit("row_", 1)[-1].split("_", 1)[0]: row for row in ratios
    }
    migrated_first = next(row for row in ratios if "row_1_ferric_fraction" in row.observation_id)
    assert migrated_first.value.point == Decimal("0.931900")
    assert len(migrated_first.derived_from) == 2
    assert all("row_1_" in parent for parent in migrated_first.derived_from)
    assert migrated_first.identity.fO2_Pa.is_value
    assert migrated_first.identity.fO2_Pa.value == Decimal("21278.25")
    assert migrated_first.identity.temperature_K.is_value
    assert migrated_first.identity.temperature_K.value == Decimal("1673.15")
    assert migrated_first.identity.composition.is_value
    assert dict(migrated_first.identity.composition.value.components)["FeO"] == Decimal("0.019")
    assert migrated_first.identity.total_pressure_Pa.is_unknown
    for suffix, temperature in (("1", Decimal("1673.15")), ("8", Decimal("1873.15")), ("16", Decimal("1873.15"))):
        row = migrated_by_suffix[suffix]
        assert row.identity.quantity.value is Quantity.VALENCE_RATIO
        assert row.identity.species.phase.value is Phase.GLASS
        assert row.identity.temperature_K.value == temperature
        assert row.identity.fO2_Pa.value == Decimal("21278.25")
        assert row.identity.composition.is_value
        assert row.identity.total_pressure_Pa.is_unknown

    # The source states air and p(O2)/p° but no furnace total pressure.
    assert all(row.identity.total_pressure_Pa.is_unknown for row in migrated)
    assert not result.queue
    assert not result.registry_issues
    assert len(result.validation.issues) == 48
    assert all(issue.reason is RefusalReason.IDENTITY_INCOMPLETE for issue in result.validation.issues)
    assert all(issue.path.endswith("identity.total_pressure_Pa") for issue in result.validation.issues)

    quench = iron["kurka_1989_table1_row_1_ferric_fraction"]["quench_statement"]
    assert quench["preservation"] == "not_stated"
    assert quench["locator"]["page"] == 3
