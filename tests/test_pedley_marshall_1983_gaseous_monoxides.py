from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AdmissionStatus, EvidenceClass
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/pedley-marshall-1983-gaseous-monoxides.yaml"


def test_pedley_marshall_1983_tables_4_and_5_migrate_and_pin_enthalpies():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    assert migrator.result.validation.hard_issues == ()
    assert len(migrator.result.observations) == 132
    observations = document["species"]["gaseous_monoxide_assessments"]["observations"]
    table4 = next(row for row in observations if row["observation_id"].endswith("table_4_assessed_formation_enthalpies"))
    table5 = next(row for row in observations if row["observation_id"].endswith("table_5_assessed_formation_enthalpies"))
    ag = next(row for row in table4["values"]["rows"] if row["sample"] == "AgO")
    zr = next(row for row in table5["values"]["rows"] if row["sample"] == "ZrO")
    assert ag["delta_fH"] == 75.0  # kcal/mol, Table 4 p. 982
    assert zr["delta_fH"] == 82  # kJ/mol, Table 5 p. 983

    ag_observation = next(
        observation
        for observation in migrator.result.observations.values()
        if (
            "::pedley_marshall_1983_table_4_assessed_formation_enthalpies::"
            in observation.observation_id
        )
        and observation.identity.species.formula == "AgO"
    )
    assert ag_observation.value.point == Decimal("313.8")
    assert ag_observation.derivation is not None
    assert ag_observation.derivation.relation == "thermochemical_kcal_to_kJ_exact"
    assert ag_observation.admission.status is AdmissionStatus.PENDING
    assert "unit" not in ag_observation.admission.reason.lower()
    table4_reason = table4["values"]["reason"]
    assert "no conversion is made" not in table4_reason
    assert "kcal/mol" in table4_reason and "kJ/mol" in table4_reason

    for table in (table4, table5):
        for formula in ("CdO", "OsO", "PdO", "ReO"):
            observation = next(
                observation
                for observation in migrator.result.observations.values()
                if f"::{table['observation_id']}::" in observation.observation_id
                and observation.identity.species.formula == formula
            )
            assert observation.evidence.class_.value is EvidenceClass.AUTHOR_ESTIMATE
    assert ag_observation.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
