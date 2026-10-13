from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT, Migrator
from simulator.battery.validate import validate_corpus
from tools.validate_literature_extracts import check_all_fidelity_samples_match


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/kassis-frischat-1981-silicate-glass-vapour.yaml"
)


def test_kassis_frischat_pressure_series_migrate_and_fidelity_is_mutation_sensitive() -> None:
    migrator = Migrator(REPO_ROOT)
    migrator._migrate_extract(EXTRACT)
    result = migrator.result

    assert len(result.observations) == 24
    assert result.queue == []
    validation = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    assert validation.hard_issues == ()
    for observation in result.observations.values():
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value in {Quantity.P_PARTIAL, Quantity.P_SAT}
        assert observation.identity.temperature_K.is_value
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point is not None
        assert observation.point_conditions is not None
        assert observation.point_conditions["temperature_K"].state.is_value
        assert observation.point_conditions["temperature_C"].state.is_value

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []
    assert all(
        "experimental_conditions_as_printed" in observation["values"]
        and (
            "melt_composition_as_printed" in observation["values"]
            or "pressure_label_as_printed" in observation["values"]
        )
        for block in document["species"].values()
        for observation in block.get("observations", [])
    )
    point = document["species"]["Na"]["observations"][0]["values"]["points"][0]
    point["partial_pressure_pa"] += 0.01
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT))
    point["partial_pressure_pa"] -= 0.01
    context = document["species"]["Na"]["context"][0]["values"]
    context["pressure_range_Pa_as_printed"] = "mutated"
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT))
