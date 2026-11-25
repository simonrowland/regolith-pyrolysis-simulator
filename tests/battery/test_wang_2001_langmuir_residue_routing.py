"""Route Wang's measured Langmuir residues through the shared scorer owner."""

from collections import Counter, defaultdict
from math import sqrt
import warnings

from simulator.battery.enums import AmountBasis, Engine, MethodToken, Quantity, Rail
from simulator.battery.residue import _langmuir_residue_row_id
from simulator.battery.score import ScoreContext
from tests.battery.wang_2001_langmuir_support import (
    ROOT,
    SOURCE_ID,
    wang_all_residue_rows,
    wang_context,
    wang_residue_rows,
    wang_sc1_residue_pair,
)


def _benchm1_residual_report(residuals, references):
    rows = []
    oxide_residuals = defaultdict(list)
    method_status = Counter()
    flag_counts = Counter()
    refusal_counts = Counter()
    oxides = ("MgO", "Al2O3", "SiO2", "CaO", "TiO2", "FeO", "WO3")
    runs = set()
    residual_by_reference = {
        residual.reference: residual for residual in residuals
    }
    for reference in references.values():
        observation_id = reference.observation_id
        method = (
            "EMP" if "electron_microprobe" in observation_id
            else "IMP" if (
                "ion_microprobe" in observation_id
                or "_residue_concentration" in observation_id
            )
            else "unknown"
        )
        assert method in {"EMP", "IMP"}
        run = _langmuir_residue_row_id(reference.locator) or "unknown"
        runs.add(run)
        formula = reference.identity.species.formula
        unit = "wt%" if formula in oxides else "ppm"
        printed = reference.value.point
        residual = residual_by_reference.get(reference.observation_id)
        if residual is None:
            assert formula == "WO3"
            assert reference.admission.status.value == "rejected"
            model = delta = None
            outcome = "C"
            detail = reference.admission.reason
        elif residual.numeric is None:
            method_status[(method, residual.status.value)] += 1
            for notice in residual.notices:
                flag_counts[(notice.kind.value, notice.reason)] += 1
            model = delta = None
            outcome = "R"
            raw_detail = None if residual.refusal is None else residual.refusal.detail
            reason = (
                raw_detail.get("reason", str(raw_detail))
                if isinstance(raw_detail, dict)
                else str(raw_detail or residual.status.value)
            )
            detail = (
                raw_detail.get("detail", reason)
                if isinstance(raw_detail, dict)
                else reason
            )
            refusal_counts[reason] += 1
        else:
            method_status[(method, residual.status.value)] += 1
            for notice in residual.notices:
                flag_counts[(notice.kind.value, notice.reason)] += 1
            delta = float(residual.numeric.value)
            # The production residual is model minus printed; reverse it to
            # report the model without evaluating the owner a second time.
            model = float(printed) + delta
            outcome = "S" if residual.status.value == "scored" else "F"
            detail = ""
            if formula in oxides:
                oxide_residuals[(method, run, formula)].append(delta)
        time_condition = (reference.point_conditions or {}).get("time_min")
        time_min = (
            time_condition.state.value
            if time_condition is not None and time_condition.state.is_value
            else "—"
        )
        temperature = reference.identity.temperature_K
        temperature_K = temperature.value if temperature.is_value else "—"
        rows.append((method, run, time_min, temperature_K, formula, unit,
                     printed, model, delta, outcome, detail))

    output = [
        "## Full residue table",
        "| Method | Run | Time (min) | T (K) | Species | Unit | Printed | Model | Residual | Outcome | Detail |",
        "|---|---|---:|---:|---|---|---:|---:|---:|---|---|",
    ]
    for row in rows:
        method, run, time_min, temperature, formula, unit, printed, model, delta, outcome, detail = row
        model_text = "—" if model is None else f"{model:.10g}"
        delta_text = "—" if delta is None else f"{delta:.10g}"
        output.append(
            f"| {method} | {run.upper()} | {time_min} | {temperature} | {formula} | "
            f"{unit} | {printed} | {model_text} | {delta_text} | {outcome} | {detail} |"
        )

    output.extend((
        "## Per-method, per-run oxide residuals (RMS / maximum absolute)",
        "| Method | Run | " + " | ".join(f"{oxide} RMS / max" for oxide in oxides) + " |",
        "|---|---|" + "---:|" * len(oxides),
    ))
    for method in ("EMP", "IMP"):
        for run in sorted(runs, key=lambda item: int(item.removeprefix("sc"))):
            cells = []
            for oxide in oxides:
                values = oxide_residuals[(method, run, oxide)]
                if values:
                    # RMS = sqrt(sum(r_i^2) / n); max is max(abs(r_i)).
                    rms = sqrt(sum(value * value for value in values) / len(values))
                    maximum = max(abs(value) for value in values)
                    cells.append(f"{len(values)}; {rms:.10g} / {maximum:.10g}")
                else:
                    cells.append("—")
            output.append(f"| {method} | {run.upper()} | " + " | ".join(cells) + " |")
    output.append(
        "Outcome legend: S=scored, F=flagged numeric, R=typed refusal, "
        "C=condition-only (not a score event)."
    )
    for (method, status), count in sorted(method_status.items()):
        output.append(f"WANG_STATUS_COUNT={method}|{status}|{count}")
    for (kind, reason), count in sorted(flag_counts.items()):
        output.append(f"WANG_FLAG_COUNT={kind}|{count}|{reason}")
    for reason, count in sorted(refusal_counts.items()):
        output.append(f"WANG_REFUSAL_COUNT={count}|{reason}")
    output.extend((
        f"WANG_RESIDUE_POINTS={len(references)}",
        f"WANG_ADMITTED_RESIDUE_POINTS={sum(row.admission.status.value == 'admitted' for row in references.values())}",
        f"WANG_SCORER_EVENTS={len(residuals)}",
    ))
    warnings.warn("\n".join(output), UserWarning, stacklevel=1)


def test_wang_residue_cells_emit_one_production_score_event_each(monkeypatch):
    import simulator.battery.residue as residue_module
    import simulator.battery.score as score_module

    context, observations = wang_context()
    all_references = wang_all_residue_rows(observations)
    references = wang_residue_rows(observations)
    assert len(all_references) == 432
    assert len(references) == 416
    condition_rows = {
        key: row for key, row in all_references.items()
        if row.admission.status.value != "admitted"
    }
    assert len(condition_rows) == 16
    assert all(
        row.identity.species.formula == "WO3"
        and row.admission.status.value == "rejected"
        and row.admission.reason.startswith(
            "container_contamination_not_evaporation_observable"
        )
        and "derived from either the heating element in the furnace or the heat shields "
        "surrounding the sample and heating element" in row.admission.reason
        for row in condition_rows.values()
    )
    experiment_ids = {reference.experiment_id for reference in all_references.values()}
    assert len(experiment_ids) == 1
    experiment = context.experiments[next(iter(experiment_ids))]
    starting = experiment.sample.initial_composition
    assert starting is not None and starting.state.is_value
    assert starting.state.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert starting.locator is not None
    assert starting.locator.page == 4 and starting.locator.table == "1"
    for reference in all_references.values():
        assert reference.provenance["printed_phase"] == "unknown"
        temperature = reference.identity.temperature_K
        time = (reference.point_conditions or {}).get("time_min")
        assert temperature is not None and temperature.is_value
        assert time is not None and time.state.is_value
        assert time.locator is not None and time.locator.table == "2"

    starting_ppm = {
        "La": "87.1", "Ce": "82.3", "Pr": "85.1", "Nd": "86.4",
        "Sm": "85.6", "Eu": "86.9", "Gd": "88.6", "Tb": "91.3",
        "Dy": "88.6", "Ho": "86.1", "Er": "90.2", "Tm": "87.7",
        "Yb": "88.4", "Lu": "88.5",
    }
    for reference in references.values():
        formula = reference.identity.species.formula
        subtype = reference.identity.subtype
        if formula not in starting_ppm:
            continue
        assert subtype is not None and subtype.is_value
        assert subtype.value == "element_ppm_by_mass"
        start = (reference.point_conditions or {}).get("starting_component_ppm")
        assert start is not None and start.state.is_value
        assert str(start.state.value) == starting_ppm[formula]
        assert start.locator is not None
        assert (
            start.locator.page == 4
            and start.locator.published_page == 482
            and start.locator.table == "1"
            and start.locator.note == "Table 1 measured starting abundance in column b."
        )
    assert all(
        score_module._extent_residue_method(
            reference,
            context.experiments[reference.experiment_id],
            Quantity.RESIDUE_COMPONENT_COMPOSITION,
        )
        is MethodToken.LANGMUIR_FREE_EVAPORATION
        for reference in references.values()
    )

    # One run row determines every owner input: its temperature and pressure
    # are shared across analyte series; experiment and Table 2 provide the
    # common starting inventory and measured mass-loss anchor.
    by_run_row = {}
    for reference in references.values():
        row_id = _langmuir_residue_row_id(reference.locator)
        assert row_id is not None
        temperature = reference.identity.temperature_K
        pressure = (reference.point_conditions or {}).get("total_pressure_Pa")
        assert temperature is not None and temperature.is_value
        assert pressure is not None and pressure.state.is_value
        signature = (temperature.value, repr(pressure.state.value))
        by_run_row.setdefault(row_id, set()).add(signature)
    assert len(by_run_row) == 16
    assert all(len(signatures) == 1 for signatures in by_run_row.values())

    production_predictor = residue_module.predict_langmuir_residue_composition
    predictions_by_run_row = Counter()

    def count_real_owner_calls(engine, reference, experiment, owner_observations):
        row_id = _langmuir_residue_row_id(reference.locator)
        predictions_by_run_row[row_id] += 1
        return production_predictor(
            engine, reference, experiment, owner_observations
        )

    monkeypatch.setattr(
        residue_module,
        "predict_langmuir_residue_composition",
        count_real_owner_calls,
    )
    score_store = score_module.score_store
    residuals, _ = score_store(
        context,
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    by_reference = {residual.reference: residual for residual in residuals}
    assert set(by_reference) == set(references)
    assert len(residuals) == 416
    assert predictions_by_run_row == Counter({row_id: 1 for row_id in by_run_row})

    # Mutate the source selector back to its previous Mendybaev-only rule.
    # Keep this independent of numeric model success: the source-routing
    # contract is that this already-emitted Wang event disappears on mutation.
    reference, anchor = wang_sc1_residue_pair(references, observations)
    assert reference.observation_id in by_reference
    isolated = ScoreContext(
        {},
        {reference.experiment_id: context.experiments[reference.experiment_id]},
        {reference.observation_id: reference, anchor.observation_id: anchor},
        root=ROOT,
    )
    production_selector = score_module._extent_residue_method

    def selector_with_old_wang_exclusion(point, experiment, quantity):
        if point.source_id == SOURCE_ID:
            return None
        return production_selector(point, experiment, quantity)

    monkeypatch.setattr(
        score_module, "_extent_residue_method", selector_with_old_wang_exclusion
    )
    mutated, _ = score_store(
        isolated,
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    assert all(residual.reference != reference.observation_id for residual in mutated)

    _benchm1_residual_report(residuals, all_references)
    numeric = [residual for residual in residuals if residual.numeric is not None]
    assert len(numeric) == 192
    ree_refusals = [
        residual for residual in residuals
        if references[residual.reference].identity.species.formula
        in starting_ppm
    ]
    assert len(ree_refusals) == 224
    for residual in ree_refusals:
        assert residual.numeric is None
        assert residual.refusal is not None
        assert residual.refusal.detail == {
            "reason": "awaits_ree_channels_sossipar",
            "detail": "awaits_ree_channels_sossipar",
        }
    assert not any(
        references[residual.reference].identity.species.formula == "WO3"
        for residual in residuals
    )
    assert sum(
        notice.reason.startswith("initial_oxide_valence_choice:")
        for residual in ree_refusals
        for notice in residual.notices
    ) == 32
    assert all(
        any(
            notice.kind.value == "input_omitted"
            and "congruent vapour-only zero-storage closure" in notice.reason
            and "source_oxygen_fallback=no_oxygen_condition_printed"
            in notice.reason
            for notice in residual.notices
        )
        and any(
            notice.kind.value == "input_omitted"
            and "ΣLn2O3=0.142 wt%" in notice.reason
            and "WO3 is blank" in notice.reason
            for notice in residual.notices
        )
        for residual in numeric
    )
