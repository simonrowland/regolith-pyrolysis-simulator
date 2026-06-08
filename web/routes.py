"""Flask routes for the simulator interface."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from flask import Blueprint, current_app, render_template, jsonify, request
import yaml

from web.feedstock_data import (
    debug_feedstocks_enabled,
    get_visible_feedstock,
    load_feedstock_groups,
    load_visible_feedstocks,
)

bp = Blueprint('web', __name__,
               template_folder='templates',
               static_folder='static')

DATA_DIR = Path(__file__).parent.parent / 'data'
OPTIMIZER_CACHE_NAME = 'cache.sqlite'
OPTIMIZER_ARTIFACT_NAMES = (
    OPTIMIZER_CACHE_NAME,
    'leaderboard.csv',
    'pareto.json',
    'provenance.jsonl',
)


def _load_yaml(filename):
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _optimizer_runs_root() -> Path:
    configured = (
        current_app.config.get('OPTIMIZER_RUNS_DIR')
        or os.environ.get('OPTIMIZER_RUNS_DIR')
    )
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / 'runs'


def _optimizer_run_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []

    run_dirs: list[Path] = []
    if (root / OPTIMIZER_CACHE_NAME).is_file():
        run_dirs.append(root)

    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / OPTIMIZER_CACHE_NAME).is_file():
            run_dirs.append(child)

    unique = {path.resolve(): path for path in run_dirs}
    return sorted(
        unique.values(),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )


def _sqlite_uri(path: Path) -> str:
    return 'file:' + quote(str(path.resolve()), safe='/') + '?mode=ro'


def _connect_result_store(cache_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_sqlite_uri(cache_path), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _json_value(value: Any, default: Any) -> Any:
    if value in (None, ''):
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _utc_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _artifact_metadata(run_dir: Path, root: Path) -> list[dict[str, Any]]:
    artifacts = []
    for name in OPTIMIZER_ARTIFACT_NAMES:
        path = run_dir / name
        if not path.is_file():
            continue
        artifacts.append({
            'name': name,
            'relative_path': _relative_to(path, root),
            'size_bytes': path.stat().st_size,
            'modified_at': _utc_mtime(path),
        })
    return artifacts


def _objective_items(row: sqlite3.Row) -> list[dict[str, Any]]:
    payload = _json_value(row['objectives'], [])
    return payload if isinstance(payload, list) else []


def _objectives_mapping(items: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in items:
        metric = item.get('metric')
        if isinstance(metric, str):
            values[metric] = item.get('value')
    return values


def _objective_for(
    items: list[dict[str, Any]],
    metric: str | None = None,
) -> dict[str, Any] | None:
    candidates = [
        item for item in items
        if metric is None or item.get('metric') == metric
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.get('ordinal', 0))[0]


def _eval_spec_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    keys = (
        'feedstock_id',
        'recipe_id',
        'profile_id',
        'fidelity',
        'code_version',
        'data_digests',
        'c5_enabled',
        'mre_max_voltage_V',
        'mre_target_species',
    )
    return {key: payload[key] for key in keys if key in payload}


def _result_metadata(
    row: sqlite3.Row,
    *,
    run_id: str,
    objective_metric: str | None = None,
) -> dict[str, Any]:
    objectives = _objective_items(row)
    selected = _objective_for(objectives, objective_metric)
    run_reference = _json_value(row['run_reference'], {})
    if not isinstance(run_reference, dict):
        run_reference = {}
    product_summary = run_reference.get('product_summary', {})
    if not isinstance(product_summary, dict):
        product_summary = {}

    metadata = {
        'run_id': run_id,
        'cache_key': row['cache_key'],
        'candidate_id': row['candidate_id'],
        'feedstock_id': row['feedstock_id'],
        'recipe_id': row['recipe_id'],
        'profile_id': row['profile_id'],
        'fidelity': row['fidelity'],
        'feasible': bool(row['feasible']),
        'created_at': row['created_at'],
        'objectives': _objectives_mapping(objectives),
        'objective_items': objectives,
        'selected_objective': selected,
        'run_reference': {
            'status': run_reference.get('status', ''),
            'reason': run_reference.get('reason', ''),
            'error_message': run_reference.get('error_message', ''),
            'product_summary': product_summary,
        },
        'eval_spec': _eval_spec_summary(_json_value(row['eval_spec'], {})),
        'notes': _json_value(row['notes'], []),
    }
    for key in (
        'wall_deposit_kg_by_segment_species',
        'wall_deposit_kg_by_zone_species',
        'campaigns_to_resinter',
    ):
        if key in product_summary:
            metadata[key] = product_summary[key]
    return metadata


def _read_cache_summary(cache_path: Path, run_id: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        'result_count': 0,
        'selectors': [],
        'latest_result': None,
    }
    try:
        with _connect_result_store(cache_path) as conn:
            summary['result_count'] = conn.execute(
                'SELECT COUNT(*) FROM results'
            ).fetchone()[0]
            selector_rows = conn.execute(
                """
                SELECT feedstock_id, profile_id, fidelity, COUNT(*) AS count
                FROM results
                GROUP BY feedstock_id, profile_id, fidelity
                ORDER BY feedstock_id, profile_id, fidelity
                """
            ).fetchall()
            summary['selectors'] = [dict(row) for row in selector_rows]
            latest = conn.execute(
                """
                SELECT *
                FROM results
                ORDER BY created_at DESC, cache_key ASC
                LIMIT 1
                """
            ).fetchone()
            if latest is not None:
                summary['latest_result'] = _result_metadata(latest, run_id=run_id)
    except sqlite3.Error as exc:
        summary['error'] = str(exc)
    return summary


def _optimizer_run_metadata(run_dir: Path, root: Path) -> dict[str, Any]:
    cache_path = run_dir / OPTIMIZER_CACHE_NAME
    run_id = run_dir.name
    metadata = {
        'id': run_id,
        'relative_path': _relative_to(run_dir, root),
        'cache': {
            'relative_path': _relative_to(cache_path, root),
            'size_bytes': cache_path.stat().st_size,
            'modified_at': _utc_mtime(cache_path),
        },
        'artifacts': _artifact_metadata(run_dir, root),
    }
    metadata.update(_read_cache_summary(cache_path, run_id))
    return metadata


def _query_result_rows(
    cache_path: Path,
    *,
    feedstock_id: str | None,
    profile_id: str | None,
    fidelity: str | None,
) -> list[sqlite3.Row]:
    clauses = []
    params: list[str] = []
    for column, value in (
        ('feedstock_id', feedstock_id),
        ('profile_id', profile_id),
        ('fidelity', fidelity),
    ):
        if value:
            clauses.append(f'{column} = ?')
            params.append(value)
    where = ' AND '.join(clauses) if clauses else '1 = 1'
    with _connect_result_store(cache_path) as conn:
        return conn.execute(
            f"""
            SELECT *
            FROM results
            WHERE {where}
            """,
            params,
        ).fetchall()


def _numeric_objective_value(objective: dict[str, Any]) -> float | None:
    value = objective.get('value')
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _leaderboard_entries(
    run_dirs: list[Path],
    *,
    feedstock_id: str | None,
    profile_id: str | None,
    fidelity: str | None,
    objective_metric: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    rows: list[tuple[dict[str, Any], float, str]] = []
    selected_metric = objective_metric
    selected_sense = 'maximize'

    for run_dir in run_dirs:
        run_id = run_dir.name
        try:
            result_rows = _query_result_rows(
                run_dir / OPTIMIZER_CACHE_NAME,
                feedstock_id=feedstock_id,
                profile_id=profile_id,
                fidelity=fidelity,
            )
        except sqlite3.Error:
            continue
        for row in result_rows:
            objectives = _objective_items(row)
            if selected_metric is None:
                primary = _objective_for(objectives)
                if primary is not None:
                    selected_metric = str(primary.get('metric'))
            objective = _objective_for(objectives, selected_metric)
            if objective is None:
                continue
            value = _numeric_objective_value(objective)
            if value is None:
                continue
            selected_sense = str(objective.get('sense') or selected_sense)
            entry = _result_metadata(
                row,
                run_id=run_id,
                objective_metric=selected_metric,
            )
            entry['objective_metric'] = selected_metric
            entry['objective_value'] = value
            entry['objective_sense'] = selected_sense
            rows.append((entry, value, selected_sense))

    reverse = selected_sense != 'minimize'
    rows.sort(key=lambda item: item[1], reverse=reverse)
    entries = []
    for rank, (entry, _value, _sense) in enumerate(rows[:limit], start=1):
        entry['rank'] = rank
        entries.append(entry)
    return entries, selected_metric


def _request_arg(name: str) -> str | None:
    value = request.args.get(name)
    return value.strip() if value and value.strip() else None


def _request_limit(default: int = 10, maximum: int = 100) -> int:
    try:
        limit = int(request.args.get('limit', default))
    except (TypeError, ValueError):
        limit = default
    return min(max(limit, 1), maximum)


@bp.route('/')
def simulator():
    """Main simulator interface."""
    feedstocks, debug_feedstocks = load_feedstock_groups()
    return render_template(
        'simulator.html',
        feedstocks=feedstocks,
        debug_feedstocks=debug_feedstocks,
        debug_mode=debug_feedstocks_enabled(),
    )


@bp.route('/api/feedstocks')
def get_feedstocks():
    """Return available feedstocks as JSON."""
    return jsonify(load_visible_feedstocks(include_custom=True))


@bp.route('/api/setpoints')
def get_setpoints():
    """Return campaign setpoints as JSON."""
    return jsonify(_load_yaml('setpoints.yaml'))


@bp.route('/api/feedstock/<key>')
def get_feedstock(key):
    """Return a single feedstock's details."""
    data = get_visible_feedstock(key, include_custom=True)
    if data is None:
        return jsonify({'error': 'Feedstock not found'}), 404
    return jsonify(data)


@bp.route('/api/optimizer/runs')
def optimizer_runs():
    """Return optimizer run directories and read-only ResultStore metadata."""
    root = _optimizer_runs_root()
    runs = [
        _optimizer_run_metadata(run_dir, root)
        for run_dir in _optimizer_run_dirs(root)
    ]
    return jsonify({
        'runs_dir': str(root),
        'runs': runs,
    })


@bp.route('/api/optimizer/feedstock-profiles')
def optimizer_feedstock_profiles():
    """Scan optimize profile YAML files into a feedstock/profile lookup."""
    profiles_dir = DATA_DIR / 'optimize_profiles'
    profiles = []
    feedstocks: dict[str, list[str]] = {}

    if profiles_dir.is_dir():
        for path in sorted(profiles_dir.glob('*.yaml')):
            with path.open() as f:
                payload = yaml.safe_load(f) or {}
            profile_id = payload.get('profile_id') or path.stem
            feedstock = payload.get('feedstock') or payload.get('feedstock_id')
            objectives = payload.get('objectives') or ()
            objective_metrics = [
                objective.get('metric')
                for objective in objectives
                if isinstance(objective, dict) and objective.get('metric')
            ]
            row = {
                'profile_id': profile_id,
                'feedstock_id': feedstock,
                'relative_path': str(path.relative_to(DATA_DIR)),
                'objective_metrics': objective_metrics,
            }
            profiles.append(row)
            if feedstock:
                feedstocks.setdefault(feedstock, []).append(profile_id)

    return jsonify({
        'profiles': profiles,
        'feedstocks': feedstocks,
    })


@bp.route('/api/optimizer/leaderboard')
def optimizer_leaderboard():
    """Return a top-N objective leaderboard from stored ResultStore rows."""
    root = _optimizer_runs_root()
    run_dirs = _optimizer_run_dirs(root)
    run_id = _request_arg('run_id')
    if run_id:
        run_dirs = [run_dir for run_dir in run_dirs if run_dir.name == run_id]
        if not run_dirs:
            return jsonify({'error': 'Optimizer run not found'}), 404

    objective_metric = (
        _request_arg('objective_metric')
        or _request_arg('objective')
    )
    entries, selected_metric = _leaderboard_entries(
        run_dirs,
        feedstock_id=_request_arg('feedstock_id') or _request_arg('feedstock'),
        profile_id=_request_arg('profile_id') or _request_arg('profile'),
        fidelity=_request_arg('fidelity'),
        objective_metric=objective_metric,
        limit=_request_limit(),
    )
    return jsonify({
        'objective_metric': selected_metric,
        'limit': _request_limit(),
        'entries': entries,
    })


@bp.route('/partials/feedstock-card/<key>')
def feedstock_card(key):
    """HTMX partial: composition table for a feedstock."""
    data = get_visible_feedstock(key, include_custom=True)
    if data is None:
        return '<p>Feedstock not found.</p>', 404
    return render_template('partials/feedstock_card.html',
                           key=key, feedstock=data)


@bp.route('/api/additive-calc/<key>')
def additive_calc(key):
    """
    Compute stoichiometric additive masses from feedstock composition.

    Returns JSON {Na, K, Mg, Ca, C} in kg, sized for the batch with ~20% margin.

    Stoichiometry:
      K  — for C3 K-shuttle (reduces FeO): K_kg = FeO_kg × (2×39.10/71.84) × 0.25 × 1.2
      Na — for C3 Na-shuttle (reduces TiO₂ + Cr₂O₃):
           Na_kg = (TiO₂_kg × (4×22.99/79.87) + Cr₂O₃_kg × (6×22.99/151.99)) × 0.25 × 1.2
      Mg — for C6 thermite (reduces Al₂O₃): Mg_kg = Al₂O₃_kg × (3×24.31/101.96) × 1.2
      Ca — default 0 (extracted, not added)
      C  — for feedstocks with P₂O₅ or SO₃: C_kg = P₂O₅_kg × 0.5 + SO₃_kg × 0.3
    """
    fs = get_visible_feedstock(key, include_custom=True)
    if fs is None:
        return jsonify({'error': 'Feedstock not found'}), 404

    mass_kg = float(request.args.get('mass_kg', 1000))
    comp = fs.get('composition_wt_pct', {})

    # Absolute kg of each oxide in the batch
    FeO_kg = mass_kg * comp.get('FeO', 0) / 100.0
    TiO2_kg = mass_kg * comp.get('TiO2', 0) / 100.0
    Cr2O3_kg = mass_kg * comp.get('Cr2O3', 0) / 100.0
    Al2O3_kg = mass_kg * comp.get('Al2O3', 0) / 100.0
    P2O5_kg = mass_kg * comp.get('P2O5', 0) / 100.0
    SO3_kg = mass_kg * comp.get('SO3', 0) / 100.0

    MARGIN = 1.2
    SHUTTLE_LOSS = 0.25  # ~25% loss per cycle

    # K for C3-K shuttle
    K_kg = FeO_kg * (2 * 39.10 / 71.84) * SHUTTLE_LOSS * MARGIN

    # Na for C3-Na shuttle
    Na_kg = ((TiO2_kg * (4 * 22.99 / 79.87)
              + Cr2O3_kg * (6 * 22.99 / 151.99))
             * SHUTTLE_LOSS * MARGIN)

    # Mg for C6 thermite
    Mg_kg = Al2O3_kg * (3 * 24.31 / 101.96) * MARGIN

    # C for P₂O₅/SO₃ feedstocks
    C_kg = P2O5_kg * 0.5 + SO3_kg * 0.3

    return jsonify({
        'Na': round(Na_kg, 1),
        'K': round(K_kg, 1),
        'Mg': round(Mg_kg, 1),
        'Ca': 0.0,
        'C': round(C_kg, 1),
    })


@bp.route('/partials/disclosure/<section>')
def disclosure_section(section):
    """HTMX partial: disclosure triangle content for a campaign or section."""
    setpoints = _load_yaml('setpoints.yaml')
    campaigns = setpoints.get('campaigns', {})
    data = campaigns.get(section, {})
    return render_template('partials/disclosure.html',
                           section=section, data=data)
