from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import tests.battery.engine_cache_score_equivalence as cache_equivalence
from simulator.battery.enums import AdmissionStatus, Quantity
from simulator.battery.identity import quantity_token

pytestmark = pytest.mark.engine_cache_off


def test_scored_sossi_residues_match_off_cold_warm_and_parallel_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = "kems-012-sossi-2019"
    test_experiment_ids = (
        "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-15-07-16c",
        "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps2",
    )
    load_context = cache_equivalence.load_score_context

    def load_context_with_missing_mn(*args, **kwargs):
        context = load_context(*args, **kwargs)
        observations = dict(context.observations)
        changed = 0
        for observation_id, observation in observations.items():
            identity = observation.identity
            if (
                observation.experiment_id != test_experiment_ids[0]
                or observation.admission.status is not AdmissionStatus.ADMITTED
                or quantity_token(identity) is not Quantity.RESIDUE_COMPONENT_COMPOSITION
                or identity.species is None
                or identity.species.formula != "Mn"
            ):
                continue
            conditions = dict(observation.point_conditions or {})
            if "starting_component_ppm" not in conditions:
                continue
            conditions.pop("starting_component_ppm")
            observations[observation_id] = replace(
                observation, point_conditions=conditions
            )
            changed += 1
        assert changed, "selected Sossi cohort must have a measured Mn starting ppm"
        return replace(context, observations=observations)

    monkeypatch.setattr(
        cache_equivalence, "load_score_context", load_context_with_missing_mn
    )
    cache_equivalence.assert_residue_score_cache_equivalence(
        tmp_path,
        source=source,
        # Short 900 s runs at 1573.15 K and 1673.15 K. Each has supported Mn/Ti
        # cells and admitted Gd/La/Sc/V/Yb/Zr cells refused by the scorer. The
        # fixture removes Mn's starting input from the first run to exercise
        # the per-species refusal while keeping its Ti prediction in the cohort.
        test_experiment_ids=test_experiment_ids,
        required_refusal_reason="outside_supported_species",
    )


def test_sossi_cache_invariant_rejects_restored_whole_run_refusal_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.residue as residue

    original_initial_inventory = residue._sossi_initial_inventory

    def reject_missing_mn(experiment, starting_trace_ppm):
        if starting_trace_ppm.get("Mn") is None:
            raise residue.ResidueInventoryRefusal(
                "starting_component_ppm_missing"
            )
        return original_initial_inventory(experiment, starting_trace_ppm)

    monkeypatch.setattr(residue, "_sossi_initial_inventory", reject_missing_mn)
    with pytest.raises(AssertionError, match="same Sossi cohort's Ti row"):
        test_scored_sossi_residues_match_off_cold_warm_and_parallel_cache(
            tmp_path, monkeypatch
        )
