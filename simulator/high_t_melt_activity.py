"""Own high-temperature melt-activity fallback aggregation and run notices."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict


def record_high_t_melt_activity_fallback(
    simulator,
    authority: Any,
    vapor_pressure_provenance: Any,
) -> None:
    if not isinstance(authority, Mapping):
        return
    if not isinstance(vapor_pressure_provenance, Mapping):
        return
    fallback_rows = [
        item
        for item in vapor_pressure_provenance.values()
        if isinstance(item, Mapping)
        and item.get('melt_activity_fallback')
        and item.get('melt_activity_parent_oxide')
    ]
    table_rows = [
        row for row in fallback_rows
        if str(row.get('melt_activity_flag') or '').endswith('_table_fallback')
    ]
    if table_rows:
        ticks = getattr(simulator, '_high_t_melt_activity_table_fallback_ticks', {})
        ticks.setdefault(int(simulator.melt.hour) + 1, set()).update(
            str(row['melt_activity_parent_oxide']) for row in table_rows
        )
        simulator._high_t_melt_activity_table_fallback_ticks = ticks
        fallback_rows = [row for row in fallback_rows if row not in table_rows]
    parent_oxides = sorted(
        {
            str(item.get('melt_activity_parent_oxide'))
            for item in fallback_rows
        }
    )
    if not parent_oxides:
        return
    reason = dict(authority.get('fallback_reason') or {})
    if not reason:
        for item in fallback_rows:
            refusal = item.get('openimcc_refusal')
            if not isinstance(refusal, Mapping):
                continue
            reason = {
                'code': refusal.get('code'),
                'detail': refusal.get('detail') or refusal.get('reason'),
            }
            break
    fallback_flags = {
        str(item.get('melt_activity_flag'))
        for item in fallback_rows
        if item.get('melt_activity_flag')
    }
    activity_fallback_notices = [
        dict(notice)
        for notice in authority.get('notices', ())
        if isinstance(notice, Mapping)
        and notice.get('parent') in parent_oxides
        and notice.get('code') in fallback_flags
    ]
    campaign = getattr(simulator.melt, 'campaign', None)
    campaign_name = str(getattr(campaign, 'name', campaign) or '')
    step = {
        'hour': int(simulator.melt.hour) + 1,
        'campaign': campaign_name,
        'parent_oxides': parent_oxides,
        'reason': reason,
        'activity_fallback_notices': activity_fallback_notices,
        'held_effective_gamma_notices': list(
            authority.get('held_effective_gamma_by_parent', {}).values()
        ) + (
            [dict(authority['projected_composition_solve'])]
            if authority.get('projected_composition_solve') else []
        ),
    }
    steps = getattr(simulator, '_high_t_melt_activity_fallback_steps', None)
    if not isinstance(steps, list):
        steps = []
        simulator._high_t_melt_activity_fallback_steps = steps
    steps.append(step)


def aggregate_high_t_melt_activity_run_notice(simulator) -> Dict[str, Any] | None:
    """Aggregate flagged high-T melt-activity substitutions for the run artifact."""

    steps = getattr(simulator, '_high_t_melt_activity_fallback_steps', ())
    if not isinstance(steps, (list, tuple)) or not steps:
        return None
    valid_steps = [step for step in steps if isinstance(step, Mapping)]
    if not valid_steps:
        return None
    hours = [int(step['hour']) for step in valid_steps if 'hour' in step]
    parent_oxides = sorted(
        {
            str(parent)
            for step in valid_steps
            for parent in step.get('parent_oxides', ())
            if str(parent)
        }
    )
    if not hours or not parent_oxides:
        return None
    notices: list[Dict[str, Any]] = []
    seen_reasons: set[tuple[tuple[str, str], ...]] = set()
    for step in valid_steps:
        reason = dict(step.get('reason') or {})
        code = str(reason.get('code') or 'openimcc_evaluation_refused')
        reason_key = tuple(
            (key, str(reason.get(key) or ''))
            for key in ('code', 'type', 'detail')
        )
        if reason_key in seen_reasons:
            continue
        seen_reasons.add(reason_key)
        notices.append({'reason': code, **reason})
    campaigns = sorted(
        {
            str(step.get('campaign'))
            for step in valid_steps
            if str(step.get('campaign') or '')
        }
    )
    reason_codes = sorted({str(item['reason']) for item in notices})
    for step in valid_steps:
        notices.extend(step.get('activity_fallback_notices', ()))
        notices.extend(step.get('held_effective_gamma_notices', ()))
    notice: Dict[str, Any] = {
        'kind': 'high_t_melt_activity_fallback',
        'authority': 'extrapolated',
        'reason': (
            reason_codes[0]
            if len(reason_codes) == 1
            else 'multiple_openimcc_refusals'
        ),
        'notices': notices,
        'affected_tick_count': len(set(hours)),
        'affected_parent_count': len(parent_oxides),
        'affected_parent_oxides': parent_oxides,
        'first_hour': min(hours),
        'last_hour': max(hours),
    }
    if len(campaigns) == 1:
        notice['campaign'] = campaigns[0]
    elif campaigns:
        notice['campaigns'] = campaigns
    return notice


def aggregate_high_t_melt_activity_table_fallback_run_notice(simulator) -> Dict[str, Any] | None:
    ticks = simulator._high_t_melt_activity_table_fallback_ticks
    parent_oxides = sorted({parent for parents in ticks.values() for parent in parents})
    if not parent_oxides:
        return None
    return {
        'kind': 'high_t_melt_activity_table_fallback',
        'affected_tick_count': len(ticks),
        'affected_parent_oxides': parent_oxides,
    }
