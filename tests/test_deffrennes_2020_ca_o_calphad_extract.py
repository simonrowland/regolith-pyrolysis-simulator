"""Source-grounded provenance pins for Deffrennes et al. (2020)."""

from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/deffrennes-2020-ca-o-calphad.yaml"


def test_external_melting_values_and_context_keep_their_provenance(tmp_path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    records = {
        row["observation_id"]: row
        for species in document["species"].values()
        for field in ("observations", "context")
        for row in species.get(field, [])
    }

    external = {
        "deffrennes_2020_manara_air_solidification": "Manara et al. [19]",
        "deffrennes_2020_manara_reducing_solidification": "Manara et al. [19]",
        "deffrennes_2020_yamada_solidification": "Yamada et al. [58]",
        "deffrennes_2020_liquid_heat_content_anchor": "Alvares et al. [37]",
    }
    for observation_id, cited_work in external.items():
        values = records[observation_id]["values"]
        assert values["method_class"] == "quoted_attributed"
        assert values["evidence_class"] == "quoted_attributed"
        assert cited_work in values["attribution"]

    context_only = (
        "deffrennes_2020_cao2_phase_results",
        "deffrennes_2020_equations_and_units",
        "deffrennes_2020_figure_8_validity_caption",
        "deffrennes_2020_liquid_heat_content_anchor",
        "deffrennes_2020_liquid_validity_quotes",
        "deffrennes_2020_melting_literature_context",
        "deffrennes_2020_positive_b_low_temperature_entropy_quote",
        "deffrennes_2020_second_generation_validity_quote",
    )
    for observation_id in context_only:
        assert records[observation_id]["values"]["evidence_class"] == "quoted_attributed"

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    for observation_id in (
        "deffrennes_2020_manara_air_solidification",
        "deffrennes_2020_manara_reducing_solidification",
        "deffrennes_2020_yamada_solidification",
    ):
        migrated = migrator.result.observations[f"{EXTRACT.stem}::{observation_id}"]
        assert migrated.evidence.class_.is_value
        assert migrated.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert not {
        f"{EXTRACT.stem}::{observation_id}" for observation_id in context_only
    }.intersection(migrator.result.observations)
