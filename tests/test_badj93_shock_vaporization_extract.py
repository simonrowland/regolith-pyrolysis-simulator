from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import IdentityEqualKind, Quantity, StateTag
from simulator.battery.identity import identity_equal
from simulator.battery.migrate import REPO_ROOT
from simulator.battery.validity import underdetermined_apparatus
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/badjukov-petrova-1993-shock-vaporization.yaml"
)


def test_textual_shock_results_survive_real_migration_and_fidelity_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    observations = {
        observation.identity.species.formula: observation
        for observation in migrator.result.observations.values()
    }
    expected = {
        "Albite": (Decimal("0.04"), True),
        "Jadeite": (Decimal("0.04"), True),
        "Pyrex glass": (Decimal("0.036"), False),
    }
    assert set(observations) == set(expected)
    for observation_id, (fraction, approximate) in expected.items():
        observation = observations[observation_id]
        experiment = migrator.result.experiments[observation.experiment_id]
        assert document["species"][observation_id]["observations"][0]["regime"] == (
            "shock_fusion_vaporization"
        )
        assert experiment.method.is_unknown
        assert "shock_fusion_vaporization" in (experiment.method.reason or "")
        method_queue = [
            entry
            for entry in migrator.result.queue
            if entry.observation_id == observation.observation_id
            and entry.axes == ["method"]
        ]
        assert len(method_queue) == 1
        assert "shock_fusion_vaporization" in method_queue[0].why
        assert (
            underdetermined_apparatus(
                experiment, observation.identity.quantity.value, observation=observation
            ).reason.value
            == "method_unknown"
        )
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        assert observation.value.point == fraction
        assert observation.value.approximate is approximate
        assert observation.uncertainty.verbatim["note"].startswith(
            "No numerical uncertainty or confidence level is stated"
        )
    assert len(
        [
            entry
            for entry in migrator.result.queue
            if entry.observation_id
            in {observation.observation_id for observation in observations.values()}
            and entry.axes == ["method"]
        ]
    ) == 3

    for observation_id in (
        "Albite",
        "Jadeite",
    ):
        observation = observations[observation_id]
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == Decimal("5273.15")
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.tag is StateTag.VALUE
        assert temperature.state.value == Decimal("5273.15")
        assert temperature.locator is not None
        assert temperature.locator.pdf_page_index == 0

    pyrex = observations["Pyrex glass"]
    assert pyrex.identity.temperature_K.is_unknown
    pyrex_temperature = pyrex.point_conditions["temperature_K"]
    assert pyrex_temperature.state.is_unknown
    assert pyrex_temperature.state.reason == "not_stated"
    assert pyrex_temperature.locator is not None
    assert pyrex_temperature.locator.pdf_page_index == 0
    identity_unknown = [
        observation
        for observation in observations.values()
        if identity_equal(observation.identity, observation.identity).kind
        is IdentityEqualKind.IDENTITY_UNKNOWN
    ]
    assert len(identity_unknown) == 3

    contexts = [
        row
        for rows in migrator.result.context_by_work.values()
        for row in rows
    ]
    vapor_ratios = {
        row["observation_id"]: row["values"]["atomic_Na_over_Al"]
        for row in contexts
        if "atomic_Na_over_Al" in row.get("values", {})
    }
    assert vapor_ratios == {
        "badj93_albite_atomic_vapor_na_al_ratio": 1.3,
        "badj93_jadeite_atomic_vapor_na_al_ratio": 1.3,
        "badj93_pyrex_atomic_vapor_na_al_ratio": 26,
    }

    for sample in document["fidelity_samples"]:
        mutated = deepcopy(document)
        species = mutated["species"][sample["species"]]
        rows = species.get("observations", []) + species.get("context", [])
        row = next(
            row for row in rows if row["observation_id"] == sample["observation_id"]
        )
        field = sample["field"]
        if field == "points":
            row["values"][field][0]["mass_loss_pct"] += 0.001
        else:
            value = row["values"][field]
            if isinstance(value, list):
                value[0] += 1
            elif isinstance(value, (int, float)):
                row["values"][field] = value + 1
            else:
                row["values"][field] = f"{value} changed"
        assert check_all_fidelity_samples_match(
            mutated, label=f"mutated badj93 {sample['observation_id']}"
        )
