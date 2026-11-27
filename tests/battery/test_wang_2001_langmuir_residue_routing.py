"""Route Wang's measured Langmuir residues through the shared scorer owner."""

from collections import Counter, defaultdict
from dataclasses import replace
from decimal import Decimal
from math import sqrt
import hashlib
import json
import warnings

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Engine,
    MethodToken,
    MetricOperation,
    NoticeKind,
    Quantity,
    Rail,
    RefusalReason,
)
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


_WANG_REE_FORMULAS = (
    "La", "Ce", "Pr", "Nd", "Sm", "Eu", "Gd",
    "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
)

# Captured from the untouched scorer on c661581627 (remote receipt
# b8fae130d643406ebe4ce7146f9e614a, mac-studio-256-1). The move of the
# retention owner must keep this digest.
_WANG_REE_DEX_PIN = (
    '{"Ce": {"mean_dex": 1.098450330314588, "n": 16, "rms_dex": 1.6918878256617227}, '
    '"Dy": {"mean_dex": -0.053187794662897456, "n": 16, "rms_dex": 0.08086684352839037}, '
    '"Er": {"mean_dex": -0.06933190744119572, "n": 16, "rms_dex": 0.0928269221034844}, '
    '"Eu": {"mean_dex": 0.02167316007913287, "n": 16, "rms_dex": 0.060073410332808504}, '
    '"Gd": {"mean_dex": 0.0015607187712339972, "n": 16, "rms_dex": 0.06097663226175852}, '
    '"Ho": {"mean_dex": -0.05074188341727137, "n": 16, "rms_dex": 0.08057807921803438}, '
    '"La": {"mean_dex": -0.04685128372487344, "n": 16, "rms_dex": 0.07244807048291114}, '
    '"Lu": {"mean_dex": -0.07906856852966322, "n": 16, "rms_dex": 0.10280373320512891}, '
    '"Nd": {"mean_dex": 0.04063707604026164, "n": 16, "rms_dex": 0.0683384490404025}, '
    '"Pr": {"mean_dex": -0.007085826587320151, "n": 16, "rms_dex": 0.08906975662683633}, '
    '"Sm": {"mean_dex": 0.017017888729405406, "n": 16, "rms_dex": 0.05828799700344608}, '
    '"Tb": {"mean_dex": -0.05785322010487203, "n": 16, "rms_dex": 0.08357424032574698}, '
    '"Tm": {"mean_dex": -0.041494490115031556, "n": 16, "rms_dex": 0.072308265861075}, '
    '"Yb": {"mean_dex": -0.023713315581647305, "n": 16, "rms_dex": 0.06368871753551417}}'
)


def _wang_ree_dex_summary(residuals, references):
    """Mean and RMS dex for each Wang REE, in reference order."""

    residual_by_reference = {
        residual.reference: residual for residual in residuals
    }
    dex_by_formula: dict[str, list[float]] = defaultdict(list)
    for reference in references.values():
        formula = reference.identity.species.formula
        if formula not in _WANG_REE_FORMULAS:
            continue
        residual = residual_by_reference.get(reference.observation_id)
        if residual is None or residual.numeric is None:
            continue
        dex_by_formula[formula].append(float(residual.numeric.value))
    summary = {}
    for formula in _WANG_REE_FORMULAS:
        values = dex_by_formula[formula]
        if not values:
            summary[formula] = {"n": 0}
            continue
        mean = sum(values) / len(values)
        rms = sqrt(sum(value * value for value in values) / len(values))
        summary[formula] = {
            "n": len(values),
            "mean_dex": mean,
            "rms_dex": rms,
        }
    return summary


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
            # Absolute residuals are model minus printed. Element ppm is dex,
            # log10(model / printed), so recover the model from that ratio.
            if residual.numeric.operation is MetricOperation.DEX:
                model = float(printed) * (10 ** delta)
            else:
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
    ree_summary = _wang_ree_dex_summary(residuals, references)
    output.extend((
        f"WANG_RESIDUE_POINTS={len(references)}",
        f"WANG_ADMITTED_RESIDUE_POINTS={sum(row.admission.status.value == 'admitted' for row in references.values())}",
        f"WANG_SCORER_EVENTS={len(residuals)}",
        "WANG_REE_DEX " + json.dumps(ree_summary, sort_keys=True),
    ))
    warnings.warn("\n".join(output), UserWarning, stacklevel=1)


def _owner_notices_before_pressure_model(
    monkeypatch, reference, experiment, observations
):
    import simulator.battery.residue as residue_module

    from simulator.battery.residue import ResidueInventoryRefusal

    def refuse_before_integration(*_args, **_kwargs):
        raise ResidueInventoryRefusal("test_notice_probe")

    monkeypatch.setattr(
        residue_module, "_make_residue_pressure_model", refuse_before_integration
    )
    try:
        residue_module.predict_langmuir_residue_composition(
            "openimcc", reference, experiment, observations
        )
    except ResidueInventoryRefusal as exc:
        assert exc.reason == "test_notice_probe"
        return exc.notices
    raise AssertionError("expected the owner to stop before pressure integration")


def _assert_wang_imp_basis_notice(notices, *, expected):
    basis_notices = [
        notice for notice in notices
        if notice.reason.startswith("wang_imp_closure_basis:")
    ]
    assert len(basis_notices) == (1 if expected else 0)
    if expected:
        notice = basis_notices[0]
        assert notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
        assert "model basis is six-oxide wt% summing to 100" in notice.reason
        assert "printed basis is total ion-microprobe wt%" in notice.reason
        assert "including ΣLn2O3 and WO3" in notice.reason
        assert "WO3 stays blank" not in notice.reason


def _is_wang_ree_ppm_row(row) -> bool:
    identity = row.identity
    subtype = None if identity is None else identity.subtype
    formula = (
        None if identity is None or identity.species is None
        else identity.species.formula
    )
    return (
        subtype is not None
        and subtype.is_value
        and subtype.value == "element_ppm_by_mass"
        and formula in _WANG_REE_FORMULAS
    )


def _is_mass_loss_row(row) -> bool:
    identity = row.identity
    return (
        identity is not None
        and identity.quantity.value is Quantity.MASS_LOSS_FRACTION
    )


def _wang_scoring_context(context, observations, selected_rows):
    selected_ids = {row.observation_id for row in selected_rows}
    kept = {
        key: row
        for key, row in observations.items()
        if row.observation_id in selected_ids or _is_mass_loss_row(row)
    }
    return ScoreContext({}, context.experiments, kept, root=ROOT)


def _oxide_prediction_digest(residuals, references, candidates) -> tuple[str, int]:
    """sha256 of the scored oxide predictions, one canonical line each."""

    by_id = {row.observation_id: row for row in references.values()}
    lines = []
    for residual in residuals:
        reference = by_id.get(residual.reference)
        if reference is None or residual.numeric is None:
            continue
        formula = reference.identity.species.formula
        if formula in _WANG_REE_FORMULAS or formula == "WO3":
            continue
        predicted = candidates[residual.candidate].value.point
        lines.append(
            f"{residual.reference}\t{formula}\t{format(predicted, 'f')}"
        )
    payload = "\n".join(sorted(lines))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest, len(lines)


# Oxide cells are one Langmuir solve per run row, with REE parents left
# out of that inventory. REE vapour is a separate test so this one stays
# inside the default 300 s CPU budget. No timeout mark: the suite-shape
# ceiling is 300 s, and the post-fix receipt was 272 s.
def test_wang_residue_cells_emit_one_production_score_event_each(
    monkeypatch, pytestconfig,
):
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
        and not row.admission.reason.startswith(
            "container_contamination_not_evaporation_observable"
        )
        and row.admission.reason.startswith(
            "tungsten_attributed_to_heating_element_or_heat_shields:"
        )
        and "derived from either the heating element in the furnace or the heat shields "
        "surrounding the sample and heating element" in row.admission.reason
        and "The presence of tungsten in the residues (Table 1)" in (
            row.admission.reason
        )
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
        del owner_observations
        row_id = _langmuir_residue_row_id(reference.locator)
        predictions_by_run_row[row_id] += 1
        # The scored context omits REE rows so this test does not also
        # integrate the vapour stratum. The owner still receives every
        # observation, including the printed REE ppm that DP-118 would
        # otherwise put on the oxide inventory.
        return production_predictor(
            engine, reference, experiment, observations.values()
        )

    monkeypatch.setattr(
        residue_module,
        "predict_langmuir_residue_composition",
        count_real_owner_calls,
    )
    oxide_rows = {
        key: row for key, row in references.items()
        if not _is_wang_ree_ppm_row(row)
    }
    score_store = score_module.score_store
    residuals, candidates = score_store(
        _wang_scoring_context(context, observations, oxide_rows.values()),
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    by_reference = {residual.reference: residual for residual in residuals}
    assert set(by_reference) == set(oxide_rows)
    assert len(residuals) == 192
    # Print before the call-count assert. xdist keeps the warnings summary
    # when the count is wrong, and drops worker stdout when the run is green.
    digest, digest_n = _oxide_prediction_digest(
        residuals, all_references, candidates
    )
    digest_line = f"WANG_OXIDE_PREDICTION_DIGEST {digest} n={digest_n}"
    reporter = pytestconfig.pluginmanager.getplugin("terminalreporter")
    warnings.warn(digest_line, UserWarning, stacklevel=1)
    if reporter is not None:
        reporter.write_line(digest_line)
    else:
        print(digest_line)
    # One oxide solve per run row. REE vapour is a different cache stratum,
    # so it does not integrate this bead again. The IMP closure notice is
    # attached on read.
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

    report_references = {
        key: row for key, row in all_references.items()
        if not _is_wang_ree_ppm_row(row)
    }
    _benchm1_residual_report(residuals, report_references)
    oxide_numeric = [
        residual for residual in residuals if residual.numeric is not None
    ]
    assert len(oxide_numeric) == 192
    assert digest_n == 192
    assert not any(
        references[residual.reference].identity.species.formula == "WO3"
        for residual in residuals
    )
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
            and "not oxide-speciated" in notice.reason
            for notice in residual.notices
        )
        and not any(
            notice.reason.startswith("trace_components_outside_melt_model:")
            or notice.reason.startswith(
                "ree_oxide_activity_raoultian_default:"
            )
            for notice in residual.notices
        )
        for residual in oxide_numeric
    )
    for residual in oxide_numeric:
        reference = references[residual.reference]
        series_id = reference.observation_id.split("::", maxsplit=2)[1]
        is_imp = series_id == (
            "wang_2001_table1_ion_microprobe_residue_compositions"
        )
        is_emp = series_id == (
            "wang_2001_table1_electron_microprobe_residue_compositions"
        )
        assert is_imp or is_emp
        _assert_wang_imp_basis_notice(residual.notices, expected=is_imp)


def test_wang_ree_vapour_rows_keep_the_dp118_stratum(
    monkeypatch, pytestconfig,
):
    """REE ppm stays the flagged vapour stratum, off the oxide projection."""

    import simulator.battery.residue as residue_module
    import simulator.battery.score as score_module

    context, observations = wang_context()
    references = wang_residue_rows(observations)
    ree_reference_rows = {
        key: row for key, row in references.items()
        if _is_wang_ree_ppm_row(row)
    }
    assert len(ree_reference_rows) == 224
    by_run_row = {
        _langmuir_residue_row_id(row.locator)
        for row in ree_reference_rows.values()
    }
    assert len(by_run_row) == 16
    predictions_by_run_row = Counter()
    production_predictor = residue_module.predict_langmuir_residue_composition

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
    residuals, candidates = score_module.score_store(
        _wang_scoring_context(
            context, observations, ree_reference_rows.values()
        ),
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    assert {residual.reference for residual in residuals} == set(
        ree_reference_rows
    )
    assert len(residuals) == 224
    vapour_dex = _wang_ree_dex_summary(residuals, references)
    exhausted_by_formula = Counter(
        references[residual.reference].identity.species.formula
        for residual in residuals
        if residual.numeric is None
    )
    reporter = pytestconfig.pluginmanager.getplugin("terminalreporter")
    vapour_line = "WANG_REE_VAPOUR_DEX " + json.dumps(vapour_dex, sort_keys=True)
    baseline_line = "WANG_REE_RETAINED_BASELINE " + _WANG_REE_DEX_PIN
    exhausted_line = "WANG_REE_EXHAUSTED " + json.dumps(
        dict(exhausted_by_formula), sort_keys=True,
    )
    warnings.warn(
        vapour_line + "\n" + baseline_line + "\n" + exhausted_line,
        UserWarning,
        stacklevel=1,
    )
    if reporter is not None:
        reporter.write_line(vapour_line)
        reporter.write_line(baseline_line)
        reporter.write_line(exhausted_line)
    else:
        print(vapour_line)
        print(baseline_line)
        print(exhausted_line)
    assert predictions_by_run_row == Counter({row_id: 1 for row_id in by_run_row})

    ree_rows = list(residuals)
    ree_scored = [residual for residual in ree_rows if residual.numeric is not None]
    # A vapour law that exhausts the parent predicts 0 ppm. Dex is undefined
    # there (log10 of zero), so the row is metric_domain, not a missing score.
    ree_exhausted = [residual for residual in ree_rows if residual.numeric is None]
    assert len(ree_scored) + len(ree_exhausted) == 224
    for residual in ree_exhausted:
        assert residual.refusal is not None
        assert residual.refusal.reason is RefusalReason.METRIC_DOMAIN
        assert candidates[residual.candidate].value.point == 0
    for residual in ree_rows:
        formula = references[residual.reference].identity.species.formula
        if residual.numeric is not None:
            assert residual.refusal is None
            assert residual.numeric.operation is MetricOperation.DEX
        default_notices = [
            notice for notice in residual.notices
            if notice.kind is NoticeKind.KNOB_DEFAULTED
            and notice.reason.startswith(
                "ree_oxide_activity_raoultian_default:"
            )
        ]
        assert len(default_notices) == 1
        assert default_notices[0].policy is not None
        assert default_notices[0].policy.id == "DP-118"
        assert default_notices[0].policy.status == "proposed"
        assert default_notices[0].policy.value == "1"
        assert "gamma=1" in default_notices[0].reason
        assert "not fitted" in default_notices[0].reason
        assert not any(
            notice.reason.startswith(
                "ree_vapour_channel_missing_default_retained:"
            )
            or notice.reason.startswith("ree_channels_absent_no_valence_selected:")
            or notice.reason.startswith("initial_oxide_valence_choice:")
            for notice in residual.notices
        )
        if formula == "Ce":
            assert "ree_ce_all_as_ce2o3_default:" in default_notices[0].reason
        if formula == "Eu":
            assert any(
                notice.reason.startswith("ree_speciation_limited:")
                and "integrated parent Eu2O3" in notice.reason
                and "unprinted vacuum closure" in notice.reason
                and "treated as reduced" not in notice.reason
                and "2+" not in notice.reason
                for notice in residual.notices
            )
        if formula == "Yb":
            assert any(
                notice.reason.startswith("ree_speciation_limited:")
                and "integrated parent Yb2O3" in notice.reason
                and "unprinted vacuum closure" in notice.reason
                and "treated as reduced" not in notice.reason
                and "2+" not in notice.reason
                for notice in residual.notices
            )
        if formula == "Dy":
            assert any(
                notice.reason.startswith("dyo_formation_enthalpy_uncertainty:")
                and notice.band
                for notice in residual.notices
            )
        assert any(
            notice.reason.startswith("ree_liquid_reference_extrapolated:")
            for notice in residual.notices
        )
    ce_sc1 = next(
        residual for residual in ree_scored
        if references[residual.reference].identity.species.formula == "Ce"
        and _langmuir_residue_row_id(
            references[residual.reference].locator
        ) == "sc1"
    )
    ce_candidate = candidates[ce_sc1.candidate]
    ce_vapour = ce_candidate.provenance["ree_vapour_channel"]
    assert ce_vapour["element"] == "Ce"
    assert ce_vapour["activity"] == "ree_oxide_activity_raoultian_default"
    ce_predicted = Decimal(ce_vapour["predicted_ppm"])
    assert ce_predicted.is_finite() and ce_predicted >= 0
    assert ce_candidate.value.point == ce_predicted
    assert "ree_retention_default" not in ce_candidate.provenance
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
            and "sesquioxides carried" in notice.reason
            for notice in residual.notices
        )
        and any(
            notice.kind is NoticeKind.INPUT_OMITTED
            and notice.reason.startswith(
                "trace_components_outside_melt_model:"
            )
            and "withheld from openimcc evaluate()" in notice.reason
            and "0.01 dex applies" in notice.reason
            for notice in residual.notices
        )
        for residual in (*ree_scored, *ree_exhausted)
    )

    def without_langmuir_ramp(residual):
        return replace(
            residual,
            notices=tuple(
                notice
                for notice in residual.notices
                if not (
                    notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
                    and notice.origin == "Langmuir residue extent"
                )
            ),
        )

    stripped = [without_langmuir_ramp(residual) for residual in ree_rows]
    assert all(
        not (
            notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
            and notice.origin == "Langmuir residue extent"
        )
        for residual in stripped
        for notice in residual.notices
    )
    for residual in stripped:
        strata = score_module.flagged_strata(residual.notices)
        assert "proposed-policy" in strata
        assert "knob_defaulted" in strata
    assert score_module._measured_residuals(
        stripped, context, include_engine_out_of_band=True
    ) == []
    headlines = score_module.headline_rows(stripped, context=context)
    assert headlines
    assert all(int(row["n_candidates"]) == 0 for row in headlines)

    def without_retention_notice(residual):
        return replace(
            residual,
            notices=tuple(
                notice
                for notice in residual.notices
                if not notice.reason.startswith(
                    "ree_oxide_activity_raoultian_default:"
                )
            ),
        )

    for residual in (
        without_retention_notice(residual) for residual in stripped
    ):
        strata = score_module.flagged_strata(
            score_module._score_eligibility_notices(residual.notices)
        )
        assert "proposed-policy" not in strata, strata


def test_wang_imp_closure_notice_preserves_printed_wt_percent(monkeypatch):
    from decimal import Decimal

    context, observations = wang_context()
    references = wang_residue_rows(observations)
    imp_reference = next(
        reference
        for reference in references.values()
        if reference.identity.species.formula == "SiO2"
        and reference.observation_id.split("::", maxsplit=2)[1]
        == "wang_2001_table1_ion_microprobe_residue_compositions"
        and _langmuir_residue_row_id(reference.locator) == "sc12"
    )
    emp_reference = next(
        reference
        for reference in references.values()
        if reference.identity.species.formula == "SiO2"
        and reference.observation_id.split("::", maxsplit=2)[1]
        == "wang_2001_table1_electron_microprobe_residue_compositions"
        and _langmuir_residue_row_id(reference.locator) == "sc12"
    )
    assert imp_reference.value.point == Decimal("35.1")
    experiment = context.experiments[imp_reference.experiment_id]
    imp_notices = _owner_notices_before_pressure_model(
        monkeypatch, imp_reference, experiment, observations.values()
    )
    _assert_wang_imp_basis_notice(imp_notices, expected=True)
    imp_flag = next(
        notice for notice in imp_notices
        if notice.reason.startswith("wang_imp_closure_basis:")
    )
    assert "without renormalization" in imp_flag.reason
    assert imp_reference.value.point == Decimal("35.1")

    emp_notices = _owner_notices_before_pressure_model(
        monkeypatch,
        emp_reference,
        context.experiments[emp_reference.experiment_id],
        observations.values(),
    )
    _assert_wang_imp_basis_notice(emp_notices, expected=False)


def test_wang_imp_series_key_mutation_is_detected(monkeypatch):
    import simulator.battery.residue as residue_module

    context, observations = wang_context()
    reference = next(
        row
        for row in wang_residue_rows(observations).values()
        if row.identity.species.formula == "SiO2"
        and row.observation_id.split("::", maxsplit=2)[1]
        == "wang_2001_table1_ion_microprobe_residue_compositions"
        and _langmuir_residue_row_id(row.locator) == "sc12"
    )
    experiment = context.experiments[reference.experiment_id]
    notices = _owner_notices_before_pressure_model(
        monkeypatch, reference, experiment, observations.values()
    )
    _assert_wang_imp_basis_notice(notices, expected=True)

    with monkeypatch.context() as mutation:
        mutation.setattr(
            residue_module,
            "_WANG_IMP_RESIDUE_SERIES_ID",
            "wang_2001_table1_electron_microprobe_residue_compositions",
        )
        mutated_notices = _owner_notices_before_pressure_model(
            monkeypatch, reference, experiment, observations.values()
        )
        try:
            _assert_wang_imp_basis_notice(mutated_notices, expected=True)
        except AssertionError:
            pass
        else:
            raise AssertionError(
                "changing the selected series key must remove the IMP flag"
            )


def test_wang_starting_aggregate_notice_tracks_located_extract_cell(monkeypatch):
    from dataclasses import replace
    from decimal import Decimal

    from simulator.battery.records import State

    context, observations = wang_context()
    references = wang_residue_rows(observations)
    reference = next(
        row
        for row in references.values()
        if row.identity.species.formula == "SiO2"
        and _langmuir_residue_row_id(row.locator) == "sc12"
    )
    experiment = context.experiments[reference.experiment_id]
    located_aggregate = experiment.conditions["starting_aggregate_ln2o3_wt_pct"]
    assert located_aggregate.state.value == Decimal("0.142")
    assert located_aggregate.locator is not None
    assert (
        located_aggregate.locator.page,
        located_aggregate.locator.published_page,
        located_aggregate.locator.table,
        located_aggregate.locator.note,
    ) == (
        4,
        482,
        "1",
        # The extract note is the Wang sentence. The condition has no
        # as_printed string, so the printed-cell owner appends a located absence.
        "Table 1 starting ΣLn2O3 aggregate in column b.; printed_cell=[null, null]",
    )
    changed_aggregate = replace(
        located_aggregate, state=State.of(Decimal("0.143"))
    )
    mutated_experiment = replace(
        experiment,
        conditions={
            **experiment.conditions,
            "starting_aggregate_ln2o3_wt_pct": changed_aggregate,
        },
    )

    notices = _owner_notices_before_pressure_model(
        monkeypatch, reference, mutated_experiment, observations.values()
    )
    aggregate_notice = next(
        notice
        for notice in notices
        if notice.kind is NoticeKind.INPUT_OMITTED
        and "starting inventory uses the six named major oxides" in notice.reason
    )
    assert "ΣLn2O3=0.143 wt%" in aggregate_notice.reason
    assert "ΣLn2O3=0.142 wt%" not in aggregate_notice.reason


def test_removing_the_ree_retention_default_keeps_the_vapour_score(
    monkeypatch,
):
    import simulator.battery.residue as residue_module
    import simulator.battery.score as score_module

    context, observations = wang_context()
    references = wang_residue_rows(observations)
    reference = next(
        row for row in references.values()
        if row.identity.species.formula == "Ce"
        and _langmuir_residue_row_id(row.locator) == "sc1"
    )
    anchor = next(
        row for row in observations.values()
        if row.experiment_id == reference.experiment_id
        and row.source_id == SOURCE_ID
        and row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        and _langmuir_residue_row_id(row.locator) == "sc1"
        and row.admission.status.value == "admitted"
    )

    def no_retention_default(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        residue_module,
        "wang_ree_full_retention_prediction",
        no_retention_default,
    )
    isolated = ScoreContext(
        {},
        {reference.experiment_id: context.experiments[reference.experiment_id]},
        {reference.observation_id: reference, anchor.observation_id: anchor},
        root=ROOT,
    )
    residuals, _candidates = score_module.score_store(
        isolated,
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    assert [residual.reference for residual in residuals] == [
        reference.observation_id
    ]
    residual = residuals[0]
    assert residual.numeric is not None
    assert residual.refusal is None
    assert any(
        notice.kind is NoticeKind.KNOB_DEFAULTED
        and notice.policy is not None
        and notice.policy.id == "DP-118"
        and notice.reason.startswith("ree_oxide_activity_raoultian_default:")
        for notice in residual.notices
    )
    assert not any(
        notice.reason.startswith("ree_vapour_channel_missing_default_retained:")
        or notice.reason.startswith("ree_channels_absent_no_valence_selected:")
        or notice.reason.startswith("initial_oxide_valence_choice:")
        for notice in residual.notices
    )


def test_wang_ce_eu_refusals_name_absent_channel_without_selecting_valence(
    monkeypatch,
):
    import simulator.battery.residue as residue_module
    import simulator.battery.score as score_module

    from simulator.battery.residue import ResidueInventoryRefusal

    context, observations = wang_context()
    references = wang_residue_rows(observations)
    selected = {
        formula: next(
            reference
            for reference in references.values()
            if reference.identity.species.formula == formula
        )
        for formula in ("Ce", "Eu")
    }

    def refuse_for_missing_channel(*_args, **_kwargs):
        raise ResidueInventoryRefusal("awaits_ree_channels_sossipar")

    monkeypatch.setattr(
        residue_module,
        "predict_langmuir_residue_composition",
        refuse_for_missing_channel,
    )
    for reference in selected.values():
        prediction = score_module._langmuir_residue_prediction(
            context, reference, Engine.OPENIMCC
        )
        assert prediction.refusal_detail["reason"] == (
            "awaits_ree_channels_sossipar"
        )
        assert any(
            notice.kind is NoticeKind.INPUT_OMITTED
            and notice.reason
            == (
                "ree_channels_absent_no_valence_selected: the REE vapor "
                "channel is absent; no oxide valence was selected"
            )
            for notice in prediction.notices
        )
        assert all(
            not notice.reason.startswith("initial_oxide_valence_choice:")
            for notice in prediction.notices
        )
