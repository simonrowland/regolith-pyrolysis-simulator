"""One-off Studio diagnostic for t-1872's source-interval backlog report."""

from collections import Counter
import json

import pytest

from simulator.battery.compilation_tier import (
    is_compilation_evidence,
    predict_thermo_attempt,
)
from simulator.battery.enums import Engine, PURE_STANDARD_THERMO
from simulator.battery.identity import quantity_token
from simulator.battery.score import is_compilation_source, load_score_context


def test_openimcc_interval_backlog_counts_by_source() -> None:
    context = load_score_context()
    produced: Counter[str] = Counter()
    out_of_interval: Counter[tuple[str, str, str]] = Counter()
    reference_interval: Counter[str] = Counter()
    refusals: Counter[tuple[str, str]] = Counter()

    for observation in context.observations.values():
        identity = observation.identity
        quantity = quantity_token(identity)
        if quantity not in PURE_STANDARD_THERMO:
            continue
        origin = context.origins.get(observation.observation_id)
        if not (
            is_compilation_evidence(observation)
            or is_compilation_source(observation.source_id, origin)
        ):
            continue
        attempt = predict_thermo_attempt(Engine.OPENIMCC, observation)
        source = observation.source_id or "source-unknown"
        if attempt.value is not None:
            produced[source] += 1
            continue
        detail = attempt.refusal_detail
        if detail.get("reason") == "openimcc_species_thermo_outside_interval":
            target_temperature = (
                str(identity.temperature_K.value)
                if identity.temperature_K is not None and identity.temperature_K.is_value
                else None
            )
            if detail.get("requested_T_K") == target_temperature:
                key = (
                    source,
                    str(detail.get("source_row_id") or "source-row-unknown"),
                    str(detail.get("phase") or "phase-unknown"),
                )
                out_of_interval[key] += 1
            else:
                reference_interval[source] += 1
        else:
            refusals[(source, str(detail.get("reason") or "reason-unknown"))] += 1

    report = {
        "produced_by_observation_source": dict(sorted(produced.items())),
        "target_out_of_interval_by_source_row_phase": {
            "|".join(key): value for key, value in sorted(out_of_interval.items())
        },
        "reference_temperature_outside_interval_by_source": dict(
            sorted(reference_interval.items())
        ),
        "other_refusals_by_source_reason": {
            "|".join(key): value for key, value in sorted(refusals.items())
        },
    }
    pytest.skip("T1872_INTERVAL_REPORT=" + json.dumps(report, sort_keys=True))
