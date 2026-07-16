"""Flask routes for the simulator interface."""

import json
import math
import os
import re
import shutil
import sqlite3
import copy
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import quote, urlsplit

from flask import Blueprint, Response, abort, current_app, render_template, jsonify, request, send_file, send_from_directory, session
import yaml
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from simulator.backends import BackendResolutionStatus, backend_resolution_status
from simulator.backend_names import (
    ANALYTICAL_BACKEND_CLASS_DISPLAY_NAME,
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    canonical_backend_name,
)
from simulator.cost_parameters import RECIPE_COST_PARAMETERS_KEY
from simulator.campaigns import CampaignManager
from simulator.condensation import (
    BOLTZMANN_CONSTANT_J_K,
    CONTINUUM_BUFFER_KN,
    DEFAULT_PIPE_DIAMETER_M,
    N2_COLLISION_DIAMETER_M,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET, PA_PER_MBAR
from simulator.transport_constants import (
    MEAN_FREE_PATH_DENOMINATOR_FACTOR,
    MEAN_FREE_PATH_FORMULA_ID,
)
from simulator.corpus_version import current_corpus_version, interoperable_corpus_versions
from simulator.diagnostics import coating_summary_with_grounded_authority
from simulator.fidelity_vocabulary import canonicalize_fidelity_emission
from simulator.feedstock_composition import normalized_feedstock_component_masses_kg
from simulator.furnace_materials import (
    PROXY_FURNACE_GROUNDING_TIERS,
    load_furnace_materials,
    resolve_furnace_temperature_caps,
)
from simulator.mre_ladder import (
    filter_steps_up_to_max_v,
    parse_ladder_from_setpoints,
    preset_catalog as build_mre_preset_catalog,
)
from simulator.optimize import job_runner as optimizer_job_runner
from simulator.optimize.canonical import canonical_json_dumps, normalize_canonical_value
from simulator.optimize.evalspec import current_code_version
from simulator.optimize.honesty import optimizer_tier_label
from simulator.optimize.import_bundle import (
    BUNDLE_CAP_BYTES,
    ImportBundleError,
    import_study_bundle,
    imported_studies,
    imported_study_model,
    is_imported_path,
)
from simulator.optimize.objective import (
    canonical_objective_metric,
    objective_metric_aliases,
)
from simulator.optimize.results_store import (
    _deserialize_grounding_margins,
    grounded_result_feasible,
)
from simulator.optimize.save_bundle import ALLOWED_MEMBERS, export_study_bundle
from simulator.recipe_io import (
    RECIPE_LIBRARY_DIR,
    RecipeIOError,
    load_recipe_patch,
    normalize_recipe_patch,
    read_recipe_cost_parameters,
    read_recipe_metadata,
    recipe_library_path,
    write_recipe_patch,
)
from simulator.state import CondensationTrain
from simulator.wall_advisor import WALL_ZONE_TEMPERATURES_C
from web.feedstock_data import (
    debug_feedstocks_enabled,
    get_visible_feedstock,
    load_feedstock_groups,
    load_visible_feedstocks,
)
from web.advisory import (
    ceramic_rump_payload,
    vapor_pressure_authority_payload,
    wall_advisory_payload,
)
from web.run_store import (
    RunStoreCorruptionError,
    get_run_store,
    list_runs,
    load as load_run_artifact,
)

bp = Blueprint('web', __name__,
               template_folder='templates',
               static_folder='static')

DATA_DIR = Path(__file__).parent.parent / 'data'
REPORT_VIEWER_DIR = Path(__file__).parent / 'report_viewer'
THERMAL_TRAIN_DEFAULT_ARTIFACT_ID = 'thermal-train-default-v2'
THERMAL_TRAIN_DEFAULT_ARTIFACT_SCHEMA = 'thermal-train-default-artifact-v2'
THERMAL_TRAIN_DEFAULT_ARTIFACT_PATH = (
    DATA_DIR / 'fixtures' / 'thermal_train' / 'default-v2.json'
)
OPTIMIZER_CACHE_NAME = 'cache.sqlite'
OPTIMIZER_ARTIFACT_NAMES = (
    OPTIMIZER_CACHE_NAME,
    'study.manifest.json',
    'study.summary.json',
    'study.profile.yaml',
    'artifact.index.json',
    'job_status.json',
    'leaderboard.csv',
    'pareto.json',
    'search_provenance.json',
    'provenance.jsonl',
)
OPTIMIZER_JOBS_DIR_NAME = 'jobs'
OPTIMIZER_JOB_STRATEGIES = ('random', 'screen', 'bayes', 'nsga2', 'staged')
OPTIMIZER_JOB_FIDELITIES = (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    'fast',
    'high',
    'auto',
)
DEFAULT_OPTIMIZER_JOB_PARALLEL_CAP = 4
DEFAULT_OPTIMIZER_JOB_BUDGET_CAP = 256
DEFAULT_OPTIMIZER_JOB_QUEUE_CAP = 64
RUN_COMMAND_BODY_CAP_BYTES = 1024 * 1024
MAX_ADDITIVE_CALC_MASS_KG = 1_000_000_000.0
_SAFE_FILENAME_RE = re.compile(r'[^A-Za-z0-9._-]+')
_RECIPE_TITLE_MAX_CHARS = 120
_RECIPE_SLUG_RE = re.compile(r'[^a-z0-9]+')
_OPTIMIZER_SUBMIT_LOCK = threading.Lock()


@bp.record_once
def _configure_request_size_limit(state) -> None:
    configured = state.app.config.get('MAX_CONTENT_LENGTH')
    if configured is None or configured > BUNDLE_CAP_BYTES:
        state.app.config['MAX_CONTENT_LENGTH'] = BUNDLE_CAP_BYTES
    # Blueprints are also mounted by focused tests and embedders that do not
    # call create_app(); authority still comes from app.py's bind contract.
    from app import _request_authority_config
    for key, value in _request_authority_config().items():
        state.app.config.setdefault(key, value)


@bp.before_request
def _reject_unsafe_cross_origin_request():
    if not _request_host_is_configured():
        return _typed_json_error(
            'request Host does not match the configured server bind',
            'untrusted_request_host',
            403,
        )
    if request.method in {'GET', 'HEAD', 'OPTIONS'}:
        return None
    if request.content_length is not None and request.content_length > BUNDLE_CAP_BYTES:
        return _json_error('request body exceeds 512MB cap', 413)
    if _request_is_same_origin():
        return None
    return _json_error('cross-origin mutation refused', 403)


@bp.errorhandler(BadRequest)
def _bad_request_response(exc: BadRequest):
    return _json_error(exc.description, 400)


@bp.errorhandler(RequestEntityTooLarge)
def _request_too_large_response(_exc: RequestEntityTooLarge):
    return _json_error('request body exceeds 512MB cap', 413)


@bp.errorhandler(sqlite3.Error)
def _sqlite_error_response(_exc: sqlite3.Error):
    return _json_error('optimizer result store unreadable', 500)


def _configured_request_authority() -> tuple[frozenset[str], int]:
    """Return the loopback names and port owned by the actual server bind."""
    hostnames = frozenset(
        str(host).lower()
        for host in current_app.config['REGOLITH_ALLOWED_HOSTNAMES']
    )
    return hostnames, int(current_app.config['REGOLITH_BIND_PORT'])


def _request_host_is_configured() -> bool:
    hostnames, bind_port = _configured_request_authority()
    try:
        candidate = urlsplit(f'//{request.host}')
        candidate_port = candidate.port
    except ValueError:
        return False
    return (
        candidate.hostname is not None
        and candidate.hostname.lower() in hostnames
        and (candidate_port if candidate_port is not None else bind_port)
        == bind_port
        and candidate.username is None
        and candidate.password is None
    )


def _request_is_same_origin() -> bool:
    # request.host_url reflects the untrusted Host header. Pin Host and source
    # independently to the loopback names plus REGOLITH_PORT from app.py.
    if not _request_host_is_configured():
        return False
    source = request.headers.get('Origin') or request.headers.get('Referer')
    if source is None:
        return request.headers.get('Sec-Fetch-Site') != 'cross-site'
    hostnames, bind_port = _configured_request_authority()
    try:
        candidate = urlsplit(source)
        candidate_port = candidate.port
    except ValueError:
        return False
    return (
        candidate.scheme.lower() == 'http'
        and candidate.hostname is not None
        and candidate.hostname.lower() in hostnames
        and candidate_port == bind_port
        and candidate.username is None
        and candidate.password is None
    )


class _StoredBackendResolutionCarrier:
    def __init__(self, resolution: BackendResolutionStatus) -> None:
        self.backend_resolution_status = resolution


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


def _optimizer_job_parallel_cap() -> int:
    configured = current_app.config.get('OPTIMIZER_JOB_PARALLEL_CAP')
    try:
        cap = int(configured)
    except (TypeError, ValueError):
        cap = DEFAULT_OPTIMIZER_JOB_PARALLEL_CAP
    return max(1, cap)


def _optimizer_job_budget_cap() -> int:
    configured = current_app.config.get('OPTIMIZER_JOB_BUDGET_CAP')
    try:
        cap = int(configured)
    except (TypeError, ValueError):
        cap = DEFAULT_OPTIMIZER_JOB_BUDGET_CAP
    return max(1, cap)


def _optimizer_job_queue_cap() -> int:
    configured = current_app.config.get('OPTIMIZER_JOB_QUEUE_CAP')
    try:
        cap = int(configured)
    except (TypeError, ValueError):
        cap = DEFAULT_OPTIMIZER_JOB_QUEUE_CAP
    return max(1, cap)


def _optimizer_job_runner() -> optimizer_job_runner.OptimizerJobRunner:
    popen_factory = current_app.config.get('OPTIMIZER_JOB_POPEN_FACTORY')
    kwargs: dict[str, Any] = {}
    if popen_factory is not None:
        kwargs['popen_factory'] = popen_factory
    return optimizer_job_runner.get_runner(_optimizer_runs_root(), **kwargs)


def _submit_optimizer_job(
    job_request: optimizer_job_runner.OptimizerJobRequest,
) -> tuple[dict[str, Any] | None, str | None]:
    with _OPTIMIZER_SUBMIT_LOCK:
        runner = _optimizer_job_runner()
        open_jobs = sum(
            str(job.get('status') or '').lower() in {'queued', 'running'}
            for job in runner.list_jobs()
        )
        if open_jobs >= _optimizer_job_queue_cap():
            return None, 'optimizer job queue is full'
        return runner.submit(job_request), None


def _version_badge(stored_version: Any) -> dict[str, Any]:
    current = current_code_version()
    if not stored_version:
        return {
            'status': 'unknown',
            'label': 'version unknown',
            'stored_version': None,
            'current_version': current,
        }
    stored = str(stored_version)
    if stored == current:
        return {
            'status': 'current',
            'label': 'current',
            'stored_version': stored,
            'current_version': current,
        }
    return {
        'status': 'stale',
        'label': 'stale version',
        'stored_version': stored,
        'current_version': current,
    }


def _optimizer_run_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []

    run_dirs: list[Path] = []
    if (root / OPTIMIZER_CACHE_NAME).is_file() and not is_imported_path(root, root):
        run_dirs.append(root)

    for child in sorted(root.iterdir()):
        if (
            child.is_dir()
            and not is_imported_path(child, root)
            and (child / OPTIMIZER_CACHE_NAME).is_file()
        ):
            run_dirs.append(child)

    jobs_root = root / OPTIMIZER_JOBS_DIR_NAME
    if jobs_root.is_dir():
        for child in sorted(jobs_root.iterdir()):
            if (
                child.is_dir()
                and not is_imported_path(child, root)
                and (child / OPTIMIZER_CACHE_NAME).is_file()
            ):
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


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(
        row['name'] == column
        for row in conn.execute(f'PRAGMA table_info({table})')
    )


def _optimizer_version_context() -> dict[str, Any]:
    accepted = tuple(interoperable_corpus_versions())
    return {
        'gui_version': current_code_version(),
        'current_corpus_version': current_corpus_version(),
        'accepted_corpus_versions': list(accepted),
    }


def _corpus_version_value(raw: Any) -> str | None:
    if raw in (None, ''):
        return None
    text = str(raw).strip()
    return text or None


def _corpus_version_badge(
    stored_version: Any,
    accepted_versions: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    accepted = (
        accepted_versions
        if accepted_versions is not None
        else tuple(interoperable_corpus_versions())
    )
    stored = _corpus_version_value(stored_version)
    if stored is None:
        return {
            'status': 'legacy',
            'label': 'unversioned/legacy',
            'stored_version': None,
            'accepted_versions': list(accepted),
        }
    if stored in accepted:
        return {
            'status': 'accepted',
            'label': stored,
            'stored_version': stored,
            'accepted_versions': list(accepted),
        }
    return {
        'status': 'rejected',
        'label': f'incompatible: {stored}',
        'stored_version': stored,
        'accepted_versions': list(accepted),
    }


def _corpus_filter_clause(
    conn: sqlite3.Connection,
    accepted_versions: tuple[str, ...],
) -> tuple[str, tuple[Any, ...]]:
    if not _table_has_column(conn, 'results', 'corpus_version'):
        return '1 = 1', ()
    if not accepted_versions:
        return 'corpus_version IS NULL', ()
    placeholders = ', '.join('?' for _ in accepted_versions)
    return (
        f'(corpus_version IS NULL OR corpus_version IN ({placeholders}))',
        accepted_versions,
    )


def _json_value(value: Any, default: Any) -> Any:
    if value in (None, ''):
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _canonical_json(value: Any) -> str:
    return canonical_json_dumps(normalize_canonical_value(value))


def _utc_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _optimizer_run_id(run_dir: Path, root: Path) -> str:
    return _relative_to(run_dir, root).replace(os.sep, '/')


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
            values[canonical_objective_metric(metric)] = item.get('value')
    return values


def _objective_for(
    items: list[dict[str, Any]],
    metric: str | None = None,
) -> dict[str, Any] | None:
    metric_aliases = set(objective_metric_aliases(metric)) if metric is not None else None
    candidates = [
        item for item in items
        if metric_aliases is None or item.get('metric') in metric_aliases
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
        'corpus_version',
        'data_digests',
        'c5_enabled',
        'mre_max_voltage_V',
        'mre_target_species',
    )
    return {key: payload[key] for key in keys if key in payload}


def _target_thermal_windows(eval_spec: Mapping[str, Any]) -> list[dict[str, str]]:
    provenance = eval_spec.get('target_provenance')
    if not isinstance(provenance, Mapping):
        return []
    rows: list[dict[str, str]] = []
    targets = provenance.get('targets')
    if isinstance(targets, (list, tuple)):
        for target in targets:
            if not isinstance(target, Mapping):
                continue
            payload = target.get('provenance')
            if not isinstance(payload, Mapping):
                continue
            disposition = payload.get('thermal_window')
            if isinstance(disposition, str) and disposition:
                rows.append({
                    'id': str(target.get('id') or ''),
                    'thermal_window': disposition,
                })
    disposition = provenance.get('thermal_window')
    if isinstance(disposition, str) and disposition:
        rows.append({'id': '', 'thermal_window': disposition})
    return rows


def _latest_backend_status(carrier: Any) -> str | None:
    if carrier is None:
        return None
    if isinstance(carrier, Mapping):
        raw = carrier.get('backend_status')
        if raw is not None:
            return str(raw)
        for key in ('per_hour', 'hours', 'trace', 'result_blob'):
            status = _latest_backend_status(carrier.get(key))
            if status is not None:
                return status
        return None
    if isinstance(carrier, (list, tuple)):
        for item in reversed(carrier):
            status = _latest_backend_status(item)
            if status is not None:
                return status
        return None
    raw = getattr(carrier, 'backend_status', None)
    return str(raw) if raw is not None else None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if value in (0, 1):
            return bool(value)
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'true', '1'}:
            return True
        if normalized in {'false', '0'}:
            return False
        return False
    return False


def _optimizer_tier_label(
    run_reference: Mapping[str, Any],
    result_blob: Mapping[str, Any],
    *,
    backend_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return optimizer_tier_label(
        run_reference,
        result_blob,
        backend_payload=backend_payload,
    )


def _optimizer_backend_payload(
    eval_spec: Mapping[str, Any],
    result_blob: Mapping[str, Any],
    run_reference: Mapping[str, Any],
) -> dict[str, Any]:
    raw_requested = eval_spec.get('backend_name') or run_reference.get('backend_name')
    requested_token = (
        canonical_backend_name(str(raw_requested)) if raw_requested else None
    )
    requested = str(requested_token) if requested_token else 'not declared'
    stored_status = _latest_backend_status(result_blob) or _latest_backend_status(run_reference)
    if stored_status is None:
        stored_status = 'unavailable'
    internal_analyticalish = (
        requested == ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
        or canonical_backend_name(stored_status)
        == ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
    )
    backend_status = 'unavailable' if internal_analyticalish else stored_status
    emission_backend_name = (
        ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
        if internal_analyticalish
        else requested_token
    )
    backend_authoritative = _optional_bool(
        run_reference.get('backend_real_active')
        if run_reference.get('backend_real_active') is not None
        else run_reference.get('backend_authoritative')
    )
    canonical = canonicalize_fidelity_emission(
        backend_name=emission_backend_name,
        backend_status=backend_status,
        backend_authoritative=backend_authoritative,
        evidence_class=(
            run_reference.get('evidence_class')
            or result_blob.get('evidence_class')
        ),
    )
    authoritative = bool(canonical.get('backend_real_active')) and bool(
        canonical.get('certification_allowed', False)
    )
    resolution = BackendResolutionStatus(
        requested_backend=requested,
        active_backend=(
            ANALYTICAL_BACKEND_CLASS_DISPLAY_NAME
            if internal_analyticalish
            else requested
        ),
        backend_status=backend_status,
        authoritative=authoritative,
        selection_policy='stored-result',
        message='stored optimizer backend status',
    )
    payload = backend_resolution_status(_StoredBackendResolutionCarrier(resolution)).as_payload()
    payload.update(canonical)
    payload['tier_label'] = _optimizer_tier_label(
        run_reference,
        result_blob,
        backend_payload=payload,
    )
    return payload


def _result_metadata(
    row: sqlite3.Row,
    *,
    run_id: str,
    objective_metric: str | None = None,
) -> dict[str, Any]:
    objectives = _objective_items(row)
    selected = _objective_for(objectives, objective_metric)
    result_blob = _json_value(row['result_blob'], {})
    if not isinstance(result_blob, dict):
        result_blob = {}
    run_reference = _json_value(row['run_reference'], {})
    if not isinstance(run_reference, dict):
        run_reference = {}
    eval_spec = _json_value(row['eval_spec'], {})
    if not isinstance(eval_spec, dict):
        eval_spec = {}
    corpus_version = _corpus_version_value(_row_value(row, 'corpus_version'))
    corpus_badge = _corpus_version_badge(corpus_version)
    product_summary = run_reference.get('product_summary', {})
    if not isinstance(product_summary, dict):
        product_summary = {}
    product_summary = coating_summary_with_grounded_authority(product_summary)
    constraint_margins = _result_row_constraint_margins(row)

    metadata = {
        'run_id': run_id,
        'cache_key': row['cache_key'],
        'candidate_id': row['candidate_id'],
        'feedstock_id': row['feedstock_id'],
        'recipe_id': row['recipe_id'],
        'profile_id': row['profile_id'],
        'fidelity': row['fidelity'],
        'feasible': _result_row_feasible(row),
        'created_at': row['created_at'],
        'corpus_version': corpus_version,
        'corpus_version_label': corpus_badge['label'],
        'corpus_version_badge': corpus_badge,
        'objectives': _objectives_mapping(objectives),
        'objective_items': objectives,
        'selected_objective': selected,
        'run_reference': {
            'status': run_reference.get('status', ''),
            'reason': run_reference.get('reason', ''),
            'error_message': run_reference.get('error_message', ''),
            'product_summary': product_summary,
        },
        'eval_spec': _eval_spec_summary(eval_spec),
        'backend': _optimizer_backend_payload(eval_spec, result_blob, run_reference),
        'tier_label': None,
        'previously_ungated': _result_row_previously_ungated(row),
        'constraint_margins': constraint_margins,
        'constraint_margin_summary': _constraint_margin_summary(constraint_margins),
        'notes': _json_value(row['notes'], []),
    }
    metadata['constraint_label'] = (
        {
            'key': 'previously_ungated',
            'label': 'previously_ungated',
            'title': 'stored result used legacy stub_smoke constraints',
        }
        if metadata['previously_ungated']
        else None
    )
    metadata['tier_label'] = metadata['backend'].get('tier_label')
    for key in (
        'product_ledger_kg',
        'product_bins',
        'product_yield_table',
        'wall_deposit_kg_by_segment_species',
        'wall_deposit_kg_by_zone_species',
        'campaigns_to_resinter',
        'coating_status',
        'coating_authoritative',
        'coating_output_status',
        'coating_status_reason',
        'wall_deposit_sticking_authority',
    ):
        if key in product_summary:
            metadata[key] = product_summary[key]
    metadata['product_ledger_panel'] = _product_ledger_panel(product_summary)
    return metadata


def _product_ledger_panel(product_summary: Mapping[str, Any]) -> dict[str, Any]:
    product_yield_table = product_summary.get('product_yield_table')
    if isinstance(product_yield_table, Mapping):
        panel = dict(product_yield_table)
        panel.setdefault('inputs', [])
        if 'outputs' not in product_yield_table:
            panel['status'] = 'inconclusive'
            panel['reason'] = 'product_yield_table outputs missing'
            panel['outputs'] = []
        panel.setdefault('mass_closure', None)
        panel.setdefault('diagnostics', [])
        unclassified = _unclassified_product_mass(product_summary)
        if unclassified is not None:
            panel['status'] = 'inconclusive'
            panel['unclassified_product_mass'] = unclassified
            diagnostics = list(panel.get('diagnostics') or [])
            if not any(
                isinstance(row, Mapping)
                and row.get('id') == 'unclassified_product_mass'
                for row in diagnostics
            ):
                diagnostics.append({
                    'kind': 'diagnostic',
                    'id': 'unclassified_product_mass',
                    'label': 'Unclassified product mass',
                    'kg': unclassified['total_kg'],
                    'kg_by_species': unclassified['kg_by_species'],
                    'status': 'inconclusive',
                    'reason': 'product ledger species are outside named product bins',
                })
            panel['diagnostics'] = diagnostics
        return panel
    return {
        'status': 'inconclusive',
        'reason': (
            'product_yield_table missing'
            if product_summary
            else 'product summary missing'
        ),
        'inputs': [],
        'outputs': [],
        'mass_closure': None,
        'diagnostics': [],
    }


def _unclassified_product_mass(
    product_summary: Mapping[str, Any],
) -> dict[str, Any] | None:
    product_classes = product_summary.get('product_classes')
    if not isinstance(product_classes, Mapping):
        return None
    raw = product_classes.get('unclassified')
    if not isinstance(raw, Mapping):
        return None
    kg_by_species: dict[str, float] = {}
    raw_species = raw.get('kg_by_species')
    if isinstance(raw_species, Mapping):
        for species, kg in raw_species.items():
            try:
                value = float(kg)
            except (TypeError, ValueError):
                continue
            if value > 0.0:
                kg_by_species[str(species)] = value
    try:
        total_kg = float(raw.get('total_kg', sum(kg_by_species.values())))
    except (TypeError, ValueError):
        total_kg = sum(kg_by_species.values())
    if total_kg <= 0.0:
        return None
    return {
        'kg_by_species': kg_by_species,
        'total_kg': total_kg,
    }


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
    run_id = _optimizer_run_id(run_dir, root)
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
) -> tuple[list[sqlite3.Row], dict[str, Any]]:
    accepted_corpus_versions = tuple(interoperable_corpus_versions())
    with _connect_result_store(cache_path) as conn:
        digest_scopes = _current_selector_data_digest_scopes(
            conn,
            feedstock_id=feedstock_id,
            profile_id=profile_id,
            fidelity=fidelity,
            accepted_corpus_versions=accepted_corpus_versions,
        )
        if not digest_scopes:
            return [], {
                'mode': 'no_current_data_digests',
                'gui_version': current_code_version(),
                'accepted_corpus_versions': list(accepted_corpus_versions),
            }
        corpus_where, corpus_params = _corpus_filter_clause(conn, accepted_corpus_versions)
        if len(digest_scopes) == 1 or profile_id:
            selected = digest_scopes[0]
            where, params = _selector_where_with_data_digests(
                feedstock_id,
                profile_id=profile_id,
                fidelity=fidelity,
                data_digests=selected,
            )
            rows = conn.execute(
                f"""
                SELECT *
                FROM results
                WHERE {where} AND {corpus_where}
                """,
                (*params, *corpus_params),
            ).fetchall()
            return rows, {
                'mode': 'exact_data_digests',
                'gui_version': current_code_version(),
                'accepted_corpus_versions': list(accepted_corpus_versions),
                'data_digests': selected,
                'available_current_data_digest_count': len(digest_scopes),
                'narrowed_to_latest': len(digest_scopes) > 1,
            }
        where, params = _selector_where_without_data_digests(
            feedstock_id,
            profile_id=profile_id,
            fidelity=fidelity,
        )
        rows = conn.execute(
            f"""
            SELECT *
            FROM results
            WHERE {where} AND {corpus_where}
            """,
            (*params, *corpus_params),
        ).fetchall()
        return rows, {
            'mode': 'multiple_current_data_digests',
            'gui_version': current_code_version(),
            'accepted_corpus_versions': list(accepted_corpus_versions),
            'available_current_data_digest_count': len(digest_scopes),
            'data_digests': digest_scopes,
        }


def _selector_where_without_data_digests(
    feedstock_id: str | None,
    *,
    profile_id: str | None,
    fidelity: str | None,
) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (
        ('feedstock_id', feedstock_id),
        ('profile_id', profile_id),
        ('fidelity', fidelity),
    ):
        if value:
            clauses.append(f'{column} = ?')
            params.append(value)
    return ' AND '.join(clauses or ['1 = 1']), tuple(params)


def _selector_where_with_data_digests(
    feedstock_id: str | None,
    *,
    profile_id: str | None,
    fidelity: str | None,
    data_digests: Mapping[str, str],
) -> tuple[str, tuple[Any, ...]]:
    where, params = _selector_where_without_data_digests(
        feedstock_id,
        profile_id=profile_id,
        fidelity=fidelity,
    )
    return (
        f'{where} AND data_digests = ?',
        (*params, _canonical_json(data_digests)),
    )


def _current_selector_data_digest_scopes(
    conn: sqlite3.Connection,
    *,
    feedstock_id: str | None,
    profile_id: str | None,
    fidelity: str | None,
    accepted_corpus_versions: tuple[str, ...],
) -> list[Mapping[str, str]]:
    where, params = _selector_where_without_data_digests(
        feedstock_id,
        profile_id=profile_id,
        fidelity=fidelity,
    )
    corpus_where, corpus_params = _corpus_filter_clause(conn, accepted_corpus_versions)
    rows = conn.execute(
        f"""
        SELECT data_digests, MAX(created_at) AS latest_created_at
        FROM results
        WHERE {where} AND {corpus_where}
        GROUP BY data_digests
        ORDER BY latest_created_at DESC, data_digests ASC
        """,
        (*params, *corpus_params),
    ).fetchall()
    scopes: list[Mapping[str, str]] = []
    for row in rows:
        data_digests = _json_value(row['data_digests'], {})
        if not isinstance(data_digests, Mapping):
            continue
        scopes.append({str(key): str(value) for key, value in data_digests.items()})
    return scopes


def _numeric_objective_value(objective: dict[str, Any]) -> float | None:
    value = objective.get('value')
    if isinstance(value, (int, float)):
        numeric = float(value)
        if math.isfinite(numeric):
            return numeric
    return None


def _result_row_feasible(row: sqlite3.Row) -> bool:
    try:
        stored_feasible = int(row['feasible']) == 1
    except (IndexError, KeyError, TypeError, ValueError):
        return False
    margins = _deserialize_grounding_margins(
        _json_value(row['feasibility_margins'], {})
    )
    return grounded_result_feasible(
        stored_feasible,
        failure_category=_row_value(row, 'failure_category'),
        margins=margins,
    )


def _result_row_previously_ungated(row: sqlite3.Row) -> bool:
    try:
        payload = _json_value(row['feasibility_margins'], {})
    except (IndexError, KeyError, TypeError):
        return False
    return isinstance(payload, Mapping) and 'stub_smoke' in payload


def _result_row_constraint_margins(row: sqlite3.Row) -> list[dict[str, Any]]:
    try:
        payload = _json_value(row['feasibility_margins'], {})
    except (IndexError, KeyError, TypeError):
        return []
    margins = _deserialize_grounding_margins(payload)
    return [_constraint_margin_readout(margin) for margin in margins.values()]


def _constraint_margin_readout(margin: Any) -> dict[str, Any]:
    status = str(getattr(margin, 'status', '') or '')
    output_status = str(getattr(margin, 'output_status', '') or '')
    feasible = bool(getattr(margin, 'feasible', False))
    status_payload = _mapping_value(getattr(margin, 'status_payload', {}))
    status_reason = str(
        getattr(margin, 'status_reason', '')
        or status_payload.get('reason')
        or ''
    )
    if feasible:
        verdict = 'pass'
    elif status == 'not-attempted' or output_status == 'not_attempted':
        verdict = 'not-attempted'
    else:
        verdict = 'fail'
    threshold = getattr(margin, 'threshold', None)
    return {
        'gate': str(getattr(margin, 'gate', '')),
        'feasible': feasible,
        'verdict': verdict,
        'status': status,
        'output_status': output_status,
        'status_reason': status_reason,
        'status_payload': dict(status_payload),
        'predicate': str(status_payload.get('predicate') or ''),
        'detail': str(getattr(margin, 'detail', '') or ''),
        'observed': getattr(margin, 'observed', None),
        'observed_label': _display_value(getattr(margin, 'observed', None)),
        'margin': getattr(margin, 'margin', None),
        'margin_label': _display_value(getattr(margin, 'margin', None)),
        'threshold': {
            'id': str(getattr(threshold, 'id', '') or ''),
            'value': getattr(threshold, 'value', None),
            'units': str(getattr(threshold, 'units', '') or ''),
            'source': str(getattr(threshold, 'source', '') or ''),
            'source_ref': str(getattr(threshold, 'source_ref', '') or ''),
        },
    }


def _constraint_margin_summary(
    margins: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [margin for margin in margins if margin.get('verdict') != 'pass'][:3]


def _leaderboard_entries(
    run_dirs: list[Path],
    *,
    feedstock_id: str | None,
    profile_id: str | None,
    fidelity: str | None,
    objective_metric: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any], dict[str, int]]:
    rows: list[tuple[dict[str, Any], float, str]] = []
    digest_scopes: list[dict[str, Any]] = []
    excluded_counts = {
        'excluded_infeasible': 0,
        'excluded_nonfinite': 0,
    }
    selected_metric = (
        canonical_objective_metric(objective_metric)
        if objective_metric is not None
        else None
    )
    selected_sense = 'maximize'
    root = _optimizer_runs_root()

    for run_dir in run_dirs:
        run_id = _optimizer_run_id(run_dir, root)
        result_rows, digest_scope = _query_result_rows(
            run_dir / OPTIMIZER_CACHE_NAME,
            feedstock_id=feedstock_id,
            profile_id=profile_id,
            fidelity=fidelity,
        )
        digest_scope = {**digest_scope, 'run_id': run_id}
        digest_scopes.append(digest_scope)
        for row in result_rows:
            if not _result_row_feasible(row):
                excluded_counts['excluded_infeasible'] += 1
                continue
            objectives = _objective_items(row)
            if selected_metric is None:
                primary = _objective_for(objectives)
                if primary is not None:
                    selected_metric = canonical_objective_metric(str(primary.get('metric')))
            objective = _objective_for(objectives, selected_metric)
            if objective is None:
                continue
            value = _numeric_objective_value(objective)
            if value is None:
                excluded_counts['excluded_nonfinite'] += 1
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
            entry['data_digest_scope'] = {
                'mode': 'entry_data_digests',
                'data_digests': entry.get('eval_spec', {}).get('data_digests') or {},
            }
            rows.append((entry, value, selected_sense))

    reverse = selected_sense != 'minimize'
    rows.sort(key=lambda item: item[1], reverse=reverse)
    entries = []
    for rank, (entry, _value, _sense) in enumerate(rows[:limit], start=1):
        entry['rank'] = rank
        entries.append(entry)
    return (
        entries,
        selected_metric,
        _leaderboard_data_digest_scope(digest_scopes),
        excluded_counts,
    )


def _leaderboard_data_digest_scope(scopes: list[dict[str, Any]]) -> dict[str, Any]:
    if not scopes:
        return {'mode': 'no_runs_checked'}
    normalized = [
        {key: value for key, value in scope.items() if key != 'run_id'}
        for scope in scopes
    ]
    if all(scope == normalized[0] for scope in normalized):
        return scopes[0]
    return {'mode': 'per_run', 'scopes': scopes}


def _request_arg(name: str) -> str | None:
    value = request.args.get(name)
    return value.strip() if value and value.strip() else None


def _request_limit(default: int = 10, maximum: int = 100) -> int:
    raw = request.args.get('limit')
    if raw is None:
        limit = default
    else:
        limit = _strict_integer(raw)
        if limit is None:
            raise BadRequest('limit must be an integer')
    return min(max(limit, 1), maximum)


def _safe_filename_part(value: Any, fallback: str = 'result') -> str:
    clean = _SAFE_FILENAME_RE.sub('-', str(value or '')).strip('._-')
    return clean[:80] or fallback


def _positive_finite_arg(
    name: str,
    *,
    default: float,
    maximum: float,
) -> tuple[float | None, tuple[object, int] | None]:
    raw = request.args.get(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, (
            jsonify({'error': f'{name} must be a finite number > 0'}),
            400,
        )
    if not math.isfinite(value) or value <= 0.0 or value > maximum:
        return None, (
            jsonify({
                'error': f'{name} must be finite, > 0, and <= {maximum:g}',
            }),
            400,
        )
    return value, None


def _optimizer_feedstock_profiles_payload() -> dict[str, Any]:
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
            objective_metrics = list(dict.fromkeys(
                canonical_objective_metric(str(objective.get('metric')))
                for objective in objectives
                if isinstance(objective, dict) and objective.get('metric')
            ))
            constraints = payload.get('constraints') or {}
            gates = constraints.get('gates') if isinstance(constraints, dict) else ()
            constraints_gates = [
                str(gate) for gate in gates
                if isinstance(gate, str) and gate
            ]
            row = {
                'profile_id': profile_id,
                'feedstock_id': feedstock,
                'relative_path': str(path.relative_to(DATA_DIR)),
                'objective_metrics': objective_metrics,
                'constraints_gates': constraints_gates,
            }
            profiles.append(row)
            if feedstock:
                feedstocks.setdefault(feedstock, []).append(profile_id)

    return {
        'profiles': profiles,
        'feedstocks': feedstocks,
    }


def _optimizer_profile_by_id(
    feedstock_profiles: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    profiles = feedstock_profiles.get('profiles')
    if not isinstance(profiles, list):
        return {}
    return {
        str(profile.get('profile_id')): profile
        for profile in profiles
        if isinstance(profile, dict) and profile.get('profile_id')
    }


def _payload_value(payload: Mapping[str, Any], name: str, default: Any = None) -> Any:
    value = payload.get(name, default)
    if isinstance(value, str):
        return value.strip()
    return value


def _positive_int_payload(
    payload: Mapping[str, Any],
    name: str,
    *,
    default: int | None = None,
    maximum: int | None = None,
) -> tuple[int | None, str | None]:
    raw = _payload_value(payload, name, default)
    value = _strict_integer(raw)
    if value is None:
        return None, f'{name} must be a positive integer'
    if value <= 0:
        return None, f'{name} must be a positive integer'
    if maximum is not None and value > maximum:
        return None, f'{name} must be <= {maximum}'
    return value, None


def _non_negative_int_payload(
    payload: Mapping[str, Any],
    name: str,
    *,
    default: int,
) -> tuple[int | None, str | None]:
    raw = _payload_value(payload, name, default)
    value = _strict_integer(raw)
    if value is None:
        return None, f'{name} must be a non-negative integer'
    if value < 0:
        return None, f'{name} must be a non-negative integer'
    return value, None


def _strict_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r'[+-]?\d+', value):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _optimizer_job_payload() -> Mapping[str, Any]:
    if request.is_json:
        parsed = request.get_json(silent=True)
        if isinstance(parsed, Mapping):
            return parsed
    return request.form


def _parse_optimizer_job_request(
    payload: Mapping[str, Any],
) -> tuple[optimizer_job_runner.OptimizerJobRequest | None, str | None]:
    feedstock_id = str(_payload_value(payload, 'feedstock_id', '') or '')
    profile_id = str(_payload_value(payload, 'profile_id', '') or '')
    strategy = str(_payload_value(payload, 'strategy', '') or '')
    fidelity = str(
        canonical_backend_name(str(_payload_value(payload, 'fidelity', '') or ''))
    )
    feedstock_profiles = _optimizer_feedstock_profiles_payload()
    profile_by_id = _optimizer_profile_by_id(feedstock_profiles)
    feedstocks = feedstock_profiles.get('feedstocks')
    if not isinstance(feedstocks, Mapping):
        feedstocks = {}

    if feedstock_id not in feedstocks:
        return None, f'unknown feedstock_id: {feedstock_id}'
    if profile_id not in profile_by_id:
        return None, f'unknown profile_id: {profile_id}'
    allowed_profiles = feedstocks.get(feedstock_id)
    if isinstance(allowed_profiles, list) and profile_id not in allowed_profiles:
        return None, f'profile_id {profile_id} is not valid for {feedstock_id}'
    if strategy not in OPTIMIZER_JOB_STRATEGIES:
        return None, f'unknown strategy: {strategy}'
    if fidelity not in OPTIMIZER_JOB_FIDELITIES:
        return None, f'unknown fidelity: {fidelity}'

    budget, error = _positive_int_payload(
        payload,
        'budget',
        maximum=_optimizer_job_budget_cap(),
    )
    if error:
        return None, error
    parallel, error = _positive_int_payload(
        payload,
        'parallel',
        default=1,
        maximum=_optimizer_job_parallel_cap(),
    )
    if error:
        return None, error
    seed, error = _non_negative_int_payload(payload, 'seed', default=0)
    if error:
        return None, error

    profile = profile_by_id[profile_id]
    profile_arg = str(DATA_DIR / str(profile['relative_path']))
    return optimizer_job_runner.OptimizerJobRequest(
        feedstock_id=feedstock_id,
        profile_id=profile_id,
        strategy=strategy,
        fidelity=fidelity,
        budget=budget or 1,
        parallel=parallel or 1,
        seed=seed or 0,
        profile_arg=profile_arg,
    ), None


def _parse_optimizer_certify_request(
    payload: Mapping[str, Any],
) -> tuple[optimizer_job_runner.OptimizerJobRequest | None, str | None]:
    run_id = str(_payload_value(payload, 'run_id', '') or '')
    cache_key = str(_payload_value(payload, 'cache_key', '') or '')
    feedstock_id = str(_payload_value(payload, 'feedstock_id', '') or '')
    profile_id = str(_payload_value(payload, 'profile_id', '') or '')
    fidelity = str(
        canonical_backend_name(
            str(_payload_value(payload, 'fidelity', '') or 'fast')
        )
    )

    if not run_id:
        return None, 'run_id is required'
    if not cache_key:
        return None, 'cache_key is required'

    resolved = _optimizer_result_row(run_id, cache_key)
    if resolved is None:
        return None, f'optimizer result not found: {run_id}/{cache_key}'
    _root, run_dir, row = resolved

    stored_feedstock = str(row['feedstock_id'] or '')
    stored_profile = str(row['profile_id'] or '')
    stored_fidelity = str(row['fidelity'] or '')
    if feedstock_id and feedstock_id != stored_feedstock:
        return None, (
            f'feedstock_id mismatch: requested {feedstock_id}, stored {stored_feedstock}'
        )
    if profile_id and profile_id != stored_profile:
        return None, (
            f'profile_id mismatch: requested {profile_id}, stored {stored_profile}'
        )
    feedstock_id = feedstock_id or stored_feedstock
    profile_id = profile_id or stored_profile
    if fidelity not in OPTIMIZER_JOB_FIDELITIES:
        return None, f'unknown fidelity: {fidelity}'

    feedstock_profiles = _optimizer_feedstock_profiles_payload()
    profile_by_id = _optimizer_profile_by_id(feedstock_profiles)
    feedstocks = feedstock_profiles.get('feedstocks')
    if not isinstance(feedstocks, Mapping):
        feedstocks = {}
    if feedstock_id not in feedstocks:
        return None, f'unknown feedstock_id: {feedstock_id}'
    if profile_id not in profile_by_id:
        return None, f'unknown profile_id: {profile_id}'
    allowed_profiles = feedstocks.get(feedstock_id)
    if isinstance(allowed_profiles, list) and profile_id not in allowed_profiles:
        return None, f'profile_id {profile_id} is not valid for {feedstock_id}'

    profile = profile_by_id[profile_id]
    profile_arg = str(DATA_DIR / str(profile['relative_path']))
    source_store_path = str(run_dir / OPTIMIZER_CACHE_NAME)
    return optimizer_job_runner.OptimizerJobRequest(
        feedstock_id=feedstock_id,
        profile_id=profile_id,
        strategy='random',
        fidelity=fidelity,
        budget=1,
        parallel=1,
        seed=0,
        profile_arg=profile_arg,
        certify=True,
        source_store_path=source_store_path,
        certify_cache_key=cache_key,
    ), None


def _optimizer_jobs_context() -> dict[str, Any]:
    jobs = _optimizer_job_runner().list_jobs()
    return {
        'jobs': jobs,
        'jobs_dir': str(_optimizer_runs_root() / OPTIMIZER_JOBS_DIR_NAME),
    }


def _optimizer_launch_context() -> dict[str, Any]:
    return {
        'job_strategy_choices': OPTIMIZER_JOB_STRATEGIES,
        'job_fidelity_choices': OPTIMIZER_JOB_FIDELITIES,
        'job_parallel_cap': _optimizer_job_parallel_cap(),
        'job_budget_cap': _optimizer_job_budget_cap(),
        'mre_presets': _mre_preset_catalog_payload(),
        **_optimizer_jobs_context(),
    }


def _wants_json_response() -> bool:
    return request.path.startswith('/api/') or request.is_json


def _mre_preset_catalog_payload() -> list[dict[str, Any]]:
    setpoints = _load_yaml('setpoints.yaml')
    ladder = parse_ladder_from_setpoints(setpoints)
    raw_presets = [dict(preset) for preset in build_mre_preset_catalog(setpoints)]
    disabled_targets = {
        str(preset.get('mre_target_species') or '')
        for preset in raw_presets
        if preset.get('c5_enabled') and not preset.get('enabled')
    }
    presets = []
    for row in raw_presets:
        included_species: list[str] = []
        if row.get('c5_enabled'):
            for step in filter_steps_up_to_max_v(
                ladder,
                row.get('mre_max_voltage_V'),
            ):
                for species in step.get('species', ()):
                    species_name = str(species)
                    if species_name in disabled_targets:
                        continue
                    included = _oxide_target_label(species_name)
                    if included not in included_species:
                        included_species.append(included)
        row['included_species'] = included_species
        row['included_species_label'] = (
            ', '.join(included_species)
            if included_species
            else 'none'
        )
        row.setdefault('disabled_reason', '')
        presets.append(row)
    return presets


def _required_finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f'setpoints.yaml {field} must be numeric')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'setpoints.yaml {field} must be numeric') from None
    if not math.isfinite(result):
        raise ValueError(f'setpoints.yaml {field} must be finite')
    return result


def _required_finite_range_high(value: Any, field: str) -> float:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValueError(
            f'setpoints.yaml {field} must contain at least two values'
        )
    finite_values = [
        _required_finite_number(item, f'{field}[{index}]')
        for index, item in enumerate(value)
    ]
    return max(finite_values)


def _required_finite_range_pair(value: Any, field: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f'setpoints.yaml {field} must contain exactly two values')
    low = _required_finite_number(value[0], f'{field}[0]')
    high = _required_finite_number(value[1], f'{field}[1]')
    if low > high:
        raise ValueError(f'setpoints.yaml {field} must be ordered low-to-high')
    return low, high


def _format_compact_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f'{value:g}'


def _option_payload(
    value: str,
    label: str,
    label_html: str | None = None,
) -> dict[str, str]:
    payload = {'value': value, 'label': label}
    if label_html is not None:
        payload['label_html'] = label_html
    return payload


def _c4_operator_preset_payload(
    setpoints: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if setpoints is None:
        setpoints = _load_yaml('setpoints.yaml')
    campaigns = setpoints.get('campaigns', {}) if isinstance(setpoints, Mapping) else {}
    c4 = campaigns.get('C4', {}) if isinstance(campaigns, Mapping) else {}
    furnace = setpoints.get('furnace', {}) if isinstance(setpoints, Mapping) else {}
    stirring = (
        furnace.get('induction_stirring', {})
        if isinstance(furnace, Mapping)
        else {}
    )
    stir_band = (
        stirring.get('rate_acceleration_factor')
        if isinstance(stirring, Mapping)
        else None
    )
    if not (
        isinstance(stir_band, (list, tuple))
        and len(stir_band) >= 2
    ):
        raise ValueError(
            'setpoints.yaml furnace.induction_stirring.rate_acceleration_factor '
            'must contain at least two values'
        )
    stir_low = _required_finite_number(
        stir_band[0],
        'furnace.induction_stirring.rate_acceleration_factor[0]',
    )
    stir_high = _required_finite_number(
        stir_band[1],
        'furnace.induction_stirring.rate_acceleration_factor[1]',
    )
    stir_default = (stir_low + stir_high) / 2.0
    ceiling_T_C = _required_finite_range_high(
        c4.get('temp_range_C'),
        'campaigns.C4.temp_range_C',
    )
    return {
        'ceiling_T_C': _format_compact_number(ceiling_T_C),
        'pO2_mbar': _format_compact_number(
            _required_finite_number(
                c4.get('pO2_mbar_default'),
                'campaigns.C4.pO2_mbar_default',
            )
        ),
        'max_hold_hr': _format_compact_number(
            _required_finite_number(
                c4.get('max_hold_hr'),
                'campaigns.C4.max_hold_hr',
            )
        ),
        'stir_factor_default': _format_compact_number(stir_default),
        'stir_factor_label': (
            f'{_format_compact_number(stir_low)}-'
            f'{_format_compact_number(stir_high)}x'
        ),
        'pO2_options': [
            _option_payload('0.001', 'Hard vacuum (0.001)'),
            _option_payload('0.2', 'Low (0.2)'),
            _option_payload('1.0', 'Medium (1.0)'),
            _option_payload('5.0', 'High (5.0)'),
            _option_payload('50.0', 'MRE backpressure (50)'),
        ],
        'stir_factor_options': [
            _option_payload('8', 'High (8x)', 'High (8&times;)'),
            _option_payload('6', 'Medium (6x)', 'Medium (6&times;)'),
            _option_payload('4', 'Low (4x)', 'Low (4&times;)'),
            _option_payload('1', 'Off (1x)', 'Off (1&times;)'),
        ],
    }


def _furnace_material_catalog_payload() -> list[dict[str, Any]]:
    setpoints = _load_yaml('setpoints.yaml')
    requested_cap = setpoints.get('furnace_max_T_C', 1800)
    catalog = load_furnace_materials()
    materials = []
    for material_id, row in catalog.items():
        if not isinstance(row, Mapping) or row.get('enabled') is not True:
            continue
        row_id = str(row.get('id') or material_id)
        grounding = row.get('grounding')
        grounding_payload = (
            {str(key): value for key, value in grounding.items()}
            if isinstance(grounding, Mapping)
            else {}
        )
        caps = resolve_furnace_temperature_caps(
            row_id,
            requested_cap=requested_cap,
            catalog=catalog,
        )
        material_payload = {
            'id': row_id,
            'display_name': str(row.get('display_name') or row_id),
            'max_service_T_C': row.get('max_service_T_C'),
            'grounding': grounding_payload,
            **caps,
        }
        if grounding_payload.get('tier') in PROXY_FURNACE_GROUNDING_TIERS:
            material_payload['service_rating_qualifier'] = {
                'tier': grounding_payload['tier'],
                'source': grounding_payload['source'],
                'caveat': grounding_payload['caveat'],
            }
        materials.append(material_payload)
    return materials


def _knudsen_config_payload(
    setpoints: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if setpoints is None:
        setpoints = _load_yaml('setpoints.yaml')
    campaigns = setpoints.get('campaigns', {}) if isinstance(setpoints, Mapping) else {}
    c2a = (
        campaigns.get('C2A_continuous', {})
        if isinstance(campaigns, Mapping)
        else {}
    )
    pressure_band = (
        c2a.get('p_total_mbar')
        if isinstance(c2a, Mapping)
        else None
    )
    pressure_low, pressure_high = _required_finite_range_pair(
        pressure_band,
        'campaigns.C2A_continuous.p_total_mbar',
    )
    return {
        'boltzmann_constant_j_k': BOLTZMANN_CONSTANT_J_K,
        'characteristic_length_m': DEFAULT_PIPE_DIAMETER_M,
        'n2_collision_diameter_m': N2_COLLISION_DIAMETER_M,
        'continuum_buffer_kn': CONTINUUM_BUFFER_KN,
        'mean_free_path_formula_id': MEAN_FREE_PATH_FORMULA_ID,
        'mean_free_path_denominator_factor': MEAN_FREE_PATH_DENOMINATOR_FACTOR,
        'temperature_k_offset': CELSIUS_TO_KELVIN_OFFSET,
        'pressure_pa_per_mbar': PA_PER_MBAR,
        'default_pressure_band_mbar': {
            'role': 'default',
            'source_campaign': 'C2A_continuous',
            'min': pressure_low,
            'max': pressure_high,
            'label': 'viscous-flow band',
            'warning_message': (
                'pN2 sweep outside '
                f'{_format_compact_number(pressure_low)}-'
                f'{_format_compact_number(pressure_high)} mbar '
                'viscous-flow band'
            ),
        },
    }


def _condensation_train_stage_payload() -> list[dict[str, Any]]:
    stages = []
    for stage in CondensationTrain.create_default().stages:
        lo_C, hi_C = stage.temp_range_C
        stages.append({
            'stage_number': int(stage.stage_number),
            'label': stage.label,
            'temp_label': f"{float(lo_C):g}-{float(hi_C):g} C",
        })
    return stages


def _condensation_temperature_config_payload() -> dict[str, Any]:
    setpoints = _load_yaml('setpoints.yaml')
    train = setpoints.get('condensation_train', {}) or {}
    temperatures = train.get('condensation_temperatures_C', {}) or {}
    sources = train.get('condensation_temperature_sources', {}) or {}
    estimated = train.get('condensation_temperature_estimated', {}) or {}
    rows = []
    if isinstance(temperatures, Mapping):
        for species, raw_temperature in sorted(temperatures.items()):
            source = sources.get(species) if isinstance(sources, Mapping) else None
            source_text = str(source or '').strip()
            is_estimated = (
                bool(estimated.get(species))
                if isinstance(estimated, Mapping) and species in estimated
                else not bool(source_text)
            )
            rows.append({
                'species': str(species),
                'temperature_C': raw_temperature,
                'source': (
                    source_text
                    if source_text
                    else 'estimated/operator-routing override; no source row'
                ),
                'estimated': is_estimated,
            })
    return {
        'rows': rows,
        'missing_source_species': [
            row['species'] for row in rows if row['estimated']
        ],
    }


def _oxide_target_label(target_oxide: str) -> str:
    token = ''
    for char in target_oxide:
        if not token and char.isalpha():
            token = char
        elif token and char.islower():
            token += char
        elif token:
            break
    return token or target_oxide


def _float_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_quantity(value: Any, units: str = '') -> str:
    numeric = _float_value(value)
    if numeric is None:
        return 'inconclusive'
    magnitude = abs(numeric)
    if magnitude >= 100:
        text = f'{numeric:,.1f}'
    elif magnitude >= 10:
        text = f'{numeric:,.2f}'
    elif magnitude >= 1:
        text = f'{numeric:,.3f}'
    else:
        text = f'{numeric:,.4g}'
    return f'{text} {units}'.strip()


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sum_nested_numbers(value: Any) -> float | None:
    if isinstance(value, Mapping):
        total = 0.0
        found = False
        for nested in value.values():
            subtotal = _sum_nested_numbers(nested)
            if subtotal is not None:
                total += subtotal
                found = True
        return total if found else None
    if isinstance(value, (list, tuple)):
        total = 0.0
        found = False
        for nested in value:
            subtotal = _sum_nested_numbers(nested)
            if subtotal is not None:
                total += subtotal
                found = True
        return total if found else None
    return _float_value(value)


def _product_strip(result: Mapping[str, Any]) -> dict[str, Any]:
    panel = _mapping_value(result.get('product_ledger_panel'))
    if not panel:
        return {
            'status': 'inconclusive',
            'reason': 'product_ledger_panel missing from result artifact',
            'items': [],
        }

    outputs = panel.get('outputs')
    if not isinstance(outputs, list):
        return {
            'status': 'inconclusive',
            'reason': 'product_ledger_panel outputs missing',
            'items': [],
        }

    order = {
        'ingots_metals': 0,
        'glass': 1,
        'oxygen': 2,
        'captured_volatiles': 3,
        'refractory_ceramic_rump': 4,
    }
    items = []
    for row in outputs:
        if not isinstance(row, Mapping):
            continue
        row_id = str(row.get('id') or row.get('label') or '')
        if not row_id:
            continue
        items.append({
            'id': row_id,
            'label': row.get('label') or row_id,
            'kg': row.get('kg'),
            'kg_label': _format_quantity(row.get('kg'), 'kg'),
            'yield_label': _format_quantity(row.get('yield_pct'), '%')
            if row.get('yield_pct') is not None
            else 'yield inconclusive',
            'product_bin': row.get('product_bin') or row_id,
        })
    items.sort(key=lambda item: (order.get(item['id'], 99), item['label']))

    raw_status = panel.get('status')
    status = str(raw_status or '').strip().lower()
    reason = panel.get('reason')
    if panel.get('unclassified_product_mass'):
        status = 'inconclusive'
        reason = 'unclassified product mass present'
    elif status not in {'closed', 'final'}:
        stored_status = status or 'missing'
        status = 'inconclusive'
        status_reason = f'product_yield_table status {stored_status}'
        reason = (
            f'{status_reason}: {reason}'
            if reason
            else status_reason
        )
    return {
        'status': status,
        'reason': reason,
        'items': items,
        'mass_closure': panel.get('mass_closure') or {},
    }


def _coating_readout(result: Mapping[str, Any]) -> dict[str, Any]:
    result = coating_summary_with_grounded_authority(result)
    wall = (
        result.get('wall_deposit_kg_by_segment_species')
        or result.get('wall_deposit_kg_by_zone_species')
        or result.get('wall_deposit_kg')
    )
    total_kg = _sum_nested_numbers(wall)
    campaigns = result.get('campaigns_to_resinter')
    positive_deposit = total_kg is not None and total_kg > 0.0
    authority = _mapping_value(result.get('wall_deposit_sticking_authority'))
    authoritative = result.get('coating_authoritative')
    if authoritative is None and authority:
        authoritative = authority.get(
            'authoritative_for_coating',
            authority.get('authoritative_for_deposit_mass'),
        )
    parsed_authoritative = _optional_bool(authoritative)
    is_authoritative = (
        not positive_deposit
        if parsed_authoritative is None
        else parsed_authoritative
    )
    warning_reason = str(
        result.get('coating_status_reason')
        or authority.get('message')
        or (
            'wall-deposit sticking alpha authority missing'
            if positive_deposit and parsed_authoritative is None
            else ''
        )
    )
    if total_kg is None and campaigns in (None, ''):
        return {
            'status': 'inconclusive',
            'reason': 'coating artifact missing',
        }
    segment_count = len(wall) if isinstance(wall, Mapping) else None
    return {
        'status': 'available' if is_authoritative else 'warning',
        'authoritative': is_authoritative,
        'output_status': str(
            result.get('coating_output_status')
            or authority.get('output_status')
            or ('status_bearing' if not is_authoritative else 'authoritative')
        ),
        'reason': '' if is_authoritative else warning_reason,
        'total_kg': total_kg,
        'total_label': _format_quantity(total_kg, 'kg'),
        'campaigns_to_resinter': campaigns,
        'segment_count': segment_count,
    }


def _first_mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return {}


def _completeness_readout(result: Mapping[str, Any]) -> dict[str, Any]:
    product_summary = _mapping_value(
        _mapping_value(result.get('run_reference')).get('product_summary')
    )
    panel = _mapping_value(result.get('product_ledger_panel'))
    metric = _first_mapping(
        result.get('extraction_completeness'),
        product_summary.get('extraction_completeness'),
        product_summary.get('extraction_completeness_metric'),
        panel.get('extraction_completeness'),
    )
    if not metric:
        return {
            'status': 'inconclusive',
            'reason': 'extraction completeness metric missing',
        }

    status = str(metric.get('status') or 'available')
    percent = metric.get('percent')
    if percent is None:
        fraction = (
            metric.get('fraction')
            if metric.get('fraction') is not None
            else metric.get('completeness_fraction')
        )
        numeric_fraction = _float_value(fraction)
        if numeric_fraction is not None:
            percent = numeric_fraction * 100.0
    if percent is None:
        extracted = _float_value(metric.get('extracted_kg'))
        denominator = _float_value(metric.get('denominator_kg'))
        if extracted is not None and denominator and denominator > 0.0:
            percent = extracted / denominator * 100.0

    if percent is None:
        status = 'inconclusive'
    return {
        'status': status,
        'percent': percent,
        'percent_label': _format_quantity(percent, '%')
        if percent is not None
        else 'inconclusive',
        'target_species': metric.get('target_species') or metric.get('target'),
        'denominator': (
            metric.get('denominator_account')
            or metric.get('denominator')
            or metric.get('denominator_label')
        ),
        'allowed_residual': metric.get('allowed_residual'),
        'product_bin': metric.get('product_bin'),
        'reason': metric.get('reason'),
    }


def _optimizer_result_view(entry: Mapping[str, Any]) -> dict[str, Any]:
    view = dict(entry)
    view['product_strip'] = _product_strip(view)
    view['coating'] = _coating_readout(view)
    view['completeness'] = _completeness_readout(view)
    view['version_badge'] = _version_badge(
        _mapping_value(view.get('eval_spec')).get('code_version')
    )
    backend = _mapping_value(view.get('backend'))
    view['tier_label'] = view.get('tier_label') or backend.get('tier_label')
    return view


def _selector_pairs(
    run_dirs: list[Path],
    *,
    feedstock_id: str | None,
    profile_id: str | None,
    fidelity: str | None,
) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for run_dir in run_dirs:
        rows, _digest_scope = _query_result_rows(
            run_dir / OPTIMIZER_CACHE_NAME,
            feedstock_id=feedstock_id,
            profile_id=profile_id,
            fidelity=fidelity,
        )
        for row in rows:
            pairs.add((
                str(row['feedstock_id'] or ''),
                str(row['profile_id'] or ''),
            ))
    return sorted(pairs)


def _optimizer_winner_entries(
    run_dirs: list[Path],
    *,
    feedstock_id: str | None,
    profile_id: str | None,
    fidelity: str | None,
    objective_metric: str | None,
    limit: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
    entries: list[dict[str, Any]] = []
    selected_metric = objective_metric
    for pair_feedstock, pair_profile in _selector_pairs(
        run_dirs,
        feedstock_id=feedstock_id,
        profile_id=profile_id,
        fidelity=fidelity,
    ):
        winners, metric, _digest_scope, _excluded_counts = _leaderboard_entries(
            run_dirs,
            feedstock_id=pair_feedstock,
            profile_id=pair_profile,
            fidelity=fidelity,
            objective_metric=objective_metric,
            limit=1,
        )
        if metric and selected_metric is None:
            selected_metric = metric
        entries.extend(_optimizer_result_view(entry) for entry in winners)
        if len(entries) >= limit:
            break
    for rank, entry in enumerate(entries, start=1):
        entry['rank'] = rank
    return entries, selected_metric


def _optimizer_table_context() -> dict[str, Any]:
    root = _optimizer_runs_root()
    run_dirs = _optimizer_run_dirs(root)
    filters = {
        'feedstock_id': _request_arg('feedstock_id') or _request_arg('feedstock'),
        'profile_id': _request_arg('profile_id') or _request_arg('profile'),
        'fidelity': _request_arg('fidelity'),
        'objective_metric': (
            _request_arg('objective_metric')
            or _request_arg('objective')
        ),
        'limit': _request_limit(default=50),
    }
    entries, selected_metric = _optimizer_winner_entries(
        run_dirs,
        feedstock_id=filters['feedstock_id'],
        profile_id=filters['profile_id'],
        fidelity=filters['fidelity'],
        objective_metric=filters['objective_metric'],
        limit=filters['limit'],
    )
    filters['objective_metric'] = selected_metric or filters['objective_metric']
    return {
        'runs_dir': str(root),
        'entries': entries,
        'imported_entries': imported_studies(root),
        'filters': filters,
        'feedstock_profiles': _optimizer_feedstock_profiles_payload(),
        **_optimizer_version_context(),
    }


def _optimizer_result_row(
    run_id: str,
    cache_key: str,
) -> tuple[Path, Path, sqlite3.Row] | None:
    root = _optimizer_runs_root()
    for run_dir in _optimizer_run_dirs(root):
        if _optimizer_run_id(run_dir, root) != run_id:
            continue
        with _connect_result_store(run_dir / OPTIMIZER_CACHE_NAME) as conn:
            corpus_where, corpus_params = _corpus_filter_clause(
                conn,
                tuple(interoperable_corpus_versions()),
            )
            row = conn.execute(
                f"""
                SELECT *
                FROM results
                WHERE cache_key = ? AND {corpus_where}
                LIMIT 1
                """,
                (cache_key, *corpus_params),
            ).fetchone()
        if row is None:
            return None
        return root, run_dir, row
    return None


def _optimizer_run_dir_for_id(run_id: str) -> tuple[Path, Path] | None:
    root = _optimizer_runs_root()
    for run_dir in _optimizer_run_dirs(root):
        if _optimizer_run_id(run_dir, root) == run_id:
            return root, run_dir
    return None


def _display_value(value: Any) -> str:
    if value in (None, ''):
        return 'not declared'
    if isinstance(value, Mapping):
        if not value:
            return 'none'
        return ', '.join(
            f'{key}: {_display_value(nested)}'
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
        )
    if isinstance(value, (list, tuple)):
        if not value:
            return 'none'
        return ', '.join(_display_value(nested) for nested in value)
    return str(value)


def _labelled_value(label: str, value: Any, *, basis: str = '') -> dict[str, Any]:
    return {
        'label': label,
        'value': _display_value(value),
        'basis': basis,
    }


def _first_present(source: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source[key] not in (None, ''):
            return source[key]
    return None


def _recipe_stage_sections(eval_spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    overrides = _mapping_value(eval_spec.get('runtime_campaign_overrides'))
    stage_ids = list(overrides.keys())
    for stage_id in eval_spec.get('prefix_stage_ids') or ():
        if stage_id not in stage_ids:
            stage_ids.append(str(stage_id))
    if not stage_ids:
        stage_ids = [str(eval_spec.get('campaign') or 'campaign')]

    sections = []
    for stage_id in stage_ids:
        override = _mapping_value(overrides.get(stage_id))
        declared = {
            'temperature': [
                _labelled_value(
                    'Temperature ramp rate',
                    _first_present(
                        override,
                        'temperature_ramp_C_per_h',
                        'ramp_rate_C_per_h',
                        'ramp_C_per_h',
                    ),
                ),
                _labelled_value(
                    'Hold point',
                    _first_present(
                        override,
                        'hold_temperature_C',
                        'hold_temp_C',
                        'temperature_C',
                        'target_temperature_C',
                    ),
                ),
                _labelled_value(
                    'Hold duration',
                    _first_present(override, 'hold_time_h', 'duration_h'),
                ),
                _labelled_value(
                    'Wall-temp offset',
                    _first_present(
                        override,
                        'wall_temp_offset_C',
                        'wall_temperature_offset_C',
                    ),
                ),
                _labelled_value(
                    'Wall-temp zone',
                    _first_present(override, 'wall_temp_zone', 'wall_zone'),
                ),
            ],
            'atmosphere': [
                _labelled_value(
                    'Overhead pressure setpoint',
                    _first_present(
                        override,
                        'overhead_pressure_mbar',
                        'p_total_mbar',
                        'pressure_mbar',
                        'pressure_Pa',
                        'p_total_Pa',
                    ),
                ),
                _labelled_value(
                    'pO2',
                    _first_present(override, 'pO2_mbar', 'po2_mbar', 'pO2_Pa'),
                ),
                _labelled_value(
                    'pN2 sweep',
                    _first_present(
                        override,
                        'pN2_mbar',
                        'pn2_mbar',
                        'pN2_Pa',
                        'knudsen_pN2_mbar',
                    ),
                ),
            ],
            'mre_policy': [
                _labelled_value(
                    'MRE policy',
                    'enabled' if eval_spec.get('c5_enabled') else 'off',
                ),
                _labelled_value('MRE target', eval_spec.get('mre_target_species')),
                _labelled_value(
                    'MRE max voltage',
                    eval_spec.get('mre_max_voltage_V'),
                ),
            ],
            'dosing': [
                _labelled_value('Global additives', eval_spec.get('additives_kg')),
                _labelled_value(
                    'Alkali-shuttle dosing',
                    _first_present(override, 'alkali_dosing', 'dosing'),
                ),
            ],
        }
        derived = [
            _labelled_value(
                'Hours at run',
                eval_spec.get('hours'),
                basis='EvalSpec.hours',
            ),
        ]
        stage_elapsed = _first_present(override, 'hold_time_h', 'duration_h')
        if stage_elapsed is not None:
            derived.append(
                _labelled_value(
                    'Per-stage elapsed',
                    stage_elapsed,
                    basis=f'{stage_id}.hold_time_h',
                )
            )
        sections.append({
            'stage_id': stage_id,
            'declared': declared,
            'derived': derived,
        })
    return sections


def _recipe_patch(eval_spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            'heading': 'Identity',
            'lines': [
                _labelled_value('Feedstock', eval_spec.get('feedstock_id')),
                _labelled_value('Profile', eval_spec.get('profile_id')),
                _labelled_value('Recipe', eval_spec.get('recipe_id')),
                _labelled_value('Campaign', eval_spec.get('campaign')),
                _labelled_value('Track', eval_spec.get('track')),
                _labelled_value('Fidelity', eval_spec.get('fidelity')),
                _labelled_value('Backend', eval_spec.get('backend_name')),
            ],
        },
        {
            'heading': 'Batch',
            'lines': [
                _labelled_value('Mass', eval_spec.get('mass_kg')),
                _labelled_value('Hours', eval_spec.get('hours')),
                _labelled_value('Additives', eval_spec.get('additives_kg')),
            ],
        },
        {
            'heading': 'Provenance inputs',
            'lines': [
                _labelled_value('Code version', eval_spec.get('code_version')),
                _labelled_value('Data digests', eval_spec.get('data_digests')),
                _labelled_value(
                    'Chemistry kernel',
                    eval_spec.get('chemistry_kernel'),
                ),
            ],
        },
    ]


def _result_detail_model(
    root: Path,
    run_dir: Path,
    row: sqlite3.Row,
) -> dict[str, Any]:
    run_id = _optimizer_run_id(run_dir, root)
    result = _optimizer_result_view(_result_metadata(row, run_id=run_id))
    eval_spec = _json_value(row['eval_spec'], {})
    if not isinstance(eval_spec, Mapping):
        eval_spec = {}
    result_blob = _json_value(row['result_blob'], {})
    if not isinstance(result_blob, Mapping):
        result_blob = {}
    run_reference = _json_value(row['run_reference'], {})
    if not isinstance(run_reference, Mapping):
        run_reference = {}
    result['eval_spec_full'] = dict(eval_spec)
    result['result_blob'] = dict(result_blob)
    result['run_reference_full'] = dict(run_reference)
    result['recipe_patch'] = _recipe_patch(eval_spec)
    result['recipe_stages'] = _recipe_stage_sections(eval_spec)
    target_thermal_windows = _target_thermal_windows(eval_spec)
    result['target_thermal_windows'] = target_thermal_windows
    result['provenance'] = {
        'run_id': run_id,
        'run_path': _relative_to(run_dir, root),
        'cache_path': _relative_to(run_dir / OPTIMIZER_CACHE_NAME, root),
        'cache_key': row['cache_key'],
        'created_at': row['created_at'],
        'code_version': eval_spec.get('code_version'),
        'gui_version': current_code_version(),
        'corpus_version': result.get('corpus_version'),
        'corpus_version_label': result.get('corpus_version_label'),
        'accepted_corpus_versions': list(interoperable_corpus_versions()),
        'data_digests': eval_spec.get('data_digests') or {},
        'data_digests_label': _display_value(eval_spec.get('data_digests') or {}),
        'target_thermal_windows': target_thermal_windows,
        'artifacts': _artifact_metadata(run_dir, root),
    }
    return result


def _result_yaml_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'result': {
            'run_id': result.get('run_id'),
            'cache_key': result.get('cache_key'),
            'candidate_id': result.get('candidate_id'),
            'created_at': result.get('created_at'),
            'feasible': result.get('feasible'),
            'objectives': result.get('objectives'),
        },
        'eval_spec': result.get('eval_spec_full') or {},
        'recipe_patch': result.get('recipe_patch') or [],
        'recipe_stages': result.get('recipe_stages') or [],
        'provenance': result.get('provenance') or {},
    }


def _recipe_library_dir() -> Path:
    configured = current_app.config.get('RECIPE_LIBRARY_DIR')
    if configured:
        return Path(configured)
    return RECIPE_LIBRARY_DIR


def _recipe_request_payload() -> Mapping[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, Mapping):
        raise BadRequest('recipe request body must be a JSON object')
    return payload


def _validated_recipe_title(value: Any) -> str:
    if not isinstance(value, str):
        raise BadRequest('recipe title is required')
    title = value.strip()
    if not title:
        raise BadRequest('recipe title is required')
    if len(title) > _RECIPE_TITLE_MAX_CHARS:
        raise BadRequest(
            f'recipe title must be {_RECIPE_TITLE_MAX_CHARS} characters or fewer'
        )
    if any(ord(char) < 32 for char in title):
        raise BadRequest('recipe title must not contain control characters')
    return title


def _recipe_slug(title: str) -> str:
    slug = _RECIPE_SLUG_RE.sub('-', title.lower()).strip('-')
    if not slug:
        raise BadRequest('recipe title must contain at least one letter or number')
    return slug[:80].strip('-')


def _write_recipe_patch_exclusive(
    destination: Path,
    setpoints_patch: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any],
) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp_path = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f'.{destination.name}.',
        suffix='.tmp',
    )
    os.close(fd)
    temp_path = Path(raw_temp_path)
    try:
        write_recipe_patch(temp_path, setpoints_patch, metadata=metadata)
        try:
            os.link(temp_path, destination)
        except FileExistsError:
            return False
        return True
    except OSError as exc:
        raise RecipeIOError(f'could not save recipe {destination}: {exc}') from exc
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def _json_error(message: str, code: int):
    return jsonify({'error': message}), code


def _typed_json_error(message: str, error_type: str, code: int):
    return jsonify({'error': message, 'error_type': error_type}), code


def _reject_oversized_run_command():
    if (
        request.content_length is not None
        and request.content_length > RUN_COMMAND_BODY_CAP_BYTES
    ):
        return _typed_json_error(
            'run command body exceeds 1 MiB cap',
            'run_command_too_large',
            413,
        )
    request.max_content_length = RUN_COMMAND_BODY_CAP_BYTES + 1
    try:
        body = request.get_data(cache=True)
    except RequestEntityTooLarge:
        return _typed_json_error(
            'run command body exceeds 1 MiB cap',
            'run_command_too_large',
            413,
        )
    if len(body) > RUN_COMMAND_BODY_CAP_BYTES:
        return _typed_json_error(
            'run command body exceeds 1 MiB cap',
            'run_command_too_large',
            413,
        )
    return None


def _finite_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_finite(*values: Any) -> float | None:
    for value in values:
        number = _finite_or_none(value)
        if number is not None:
            return number
    return None


def _deep_merge_recipe_patch(
    base: Mapping[str, Any],
    patch: Mapping[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(dict(base))
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[str(key)] = _deep_merge_recipe_patch(current, value)
        else:
            merged[str(key)] = copy.deepcopy(value)
    return merged


def _generated_recipe_patch_from_context(context: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.get('recipe_inputs') or {}
    if not isinstance(inputs, Mapping):
        inputs = {}
    overrides = inputs.get('runtime_campaign_overrides') or {}
    if not isinstance(overrides, Mapping):
        overrides = {}

    generated: dict[str, Any] = {}
    furnace_max = _finite_or_none(inputs.get('furnace_max_T_C'))
    if furnace_max is not None:
        generated['furnace_max_T_C'] = furnace_max

    campaigns: dict[str, Any] = {}
    c4_patch: dict[str, Any] = {}
    c4_overrides = overrides.get('C4') if isinstance(overrides, Mapping) else None
    if isinstance(c4_overrides, Mapping):
        hold_temp = _finite_or_none(c4_overrides.get('hold_temp_C'))
        c4_cap = _finite_or_none(inputs.get('c4_max_temp_C'))
        if hold_temp is not None and c4_cap is not None and c4_cap >= hold_temp:
            c4_patch['temp_range_C'] = [hold_temp, c4_cap]
        elif hold_temp is not None:
            c4_patch['temp_range_C'] = [hold_temp, hold_temp]
        pO2_mbar = _finite_or_none(c4_overrides.get('pO2_mbar'))
        if pO2_mbar is not None:
            c4_patch['pO2_mbar_default'] = pO2_mbar
            c4_patch['p_total_mbar_default'] = pO2_mbar
    if c4_patch:
        campaigns['C4'] = c4_patch

    if campaigns:
        generated['campaigns'] = campaigns

    existing = copy.deepcopy(context.get('setpoints_patch') or {})
    if not isinstance(existing, Mapping):
        existing = {}
    else:
        existing = dict(existing)
    if context.get('manual_recipe_controls_supplied'):
        existing_campaigns = existing.get('campaigns')
        if isinstance(existing_campaigns, Mapping):
            clean_campaigns = copy.deepcopy(dict(existing_campaigns))
            c5 = clean_campaigns.get('C5')
            if isinstance(c5, Mapping):
                c5 = dict(c5)
                c5.pop('allow_mre_voltage_cap_V', None)
                if c5:
                    clean_campaigns['C5'] = c5
                else:
                    clean_campaigns.pop('C5', None)
            c3 = clean_campaigns.get('C3')
            if isinstance(c3, Mapping):
                c3 = dict(c3)
                c3.pop('alkali_dosing', None)
                if c3:
                    clean_campaigns['C3'] = c3
                else:
                    clean_campaigns.pop('C3', None)
            if clean_campaigns:
                existing['campaigns'] = clean_campaigns
            else:
                existing.pop('campaigns', None)
    return _deep_merge_recipe_patch(existing, generated)


def _canonical_recipe_patch_from_context(context: Mapping[str, Any]) -> dict[str, Any]:
    if context.get('manual_recipe_controls_supplied'):
        return _generated_recipe_patch_from_context(context)
    for key in ('resolved_setpoints_patch', 'setpoints_patch'):
        candidate = context.get(key) or {}
        if isinstance(candidate, Mapping) and candidate:
            return normalize_recipe_patch(candidate, source=f'{key} recipe save context')
    return _generated_recipe_patch_from_context(context)


def _recipe_text_value(value: Any) -> str:
    return str(value or '').strip()


def _recipe_non_negative_number(value: Any, field: str) -> float | None:
    if value in (None, ''):
        return None
    number = _finite_or_none(value)
    if number is None or number < 0:
        raise BadRequest(f'{field} must be a finite non-negative number')
    return number


def _recipe_positive_number(value: Any, field: str) -> float | None:
    number = _recipe_non_negative_number(value, field)
    if number is not None and number <= 0:
        raise BadRequest(f'{field} must be a finite number > 0')
    return number


def _recipe_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise BadRequest(f'{field} must be a boolean')


def _recipe_additives_from_controls(controls: Mapping[str, Any]) -> dict[str, float]:
    raw = controls.get('additives_kg')
    if raw is None:
        raw = controls.get('additives')
    if raw in (None, ''):
        return {}
    if not isinstance(raw, Mapping):
        raise BadRequest('controls.additives must be an object')
    additives: dict[str, float] = {}
    for species in ('Na', 'K', 'Mg', 'Ca', 'C'):
        amount = _recipe_non_negative_number(
            raw.get(species),
            f'controls.additives.{species}',
        )
        if amount is not None:
            additives[species] = amount
    return additives


def _recipe_runtime_overrides_from_controls(
    controls: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    raw = controls.get('runtime_campaign_overrides') or {}
    if not raw:
        return {}
    if not isinstance(raw, Mapping):
        raise BadRequest('controls.runtime_campaign_overrides must be an object')
    overrides: dict[str, dict[str, Any]] = {}
    for campaign, fields in raw.items():
        campaign_name = _recipe_text_value(campaign)
        if not campaign_name:
            raise BadRequest('controls.runtime_campaign_overrides campaign is required')
        if not isinstance(fields, Mapping):
            raise BadRequest(
                f'controls.runtime_campaign_overrides.{campaign_name} must be an object'
            )
        clean_fields: dict[str, Any] = {}
        for field, raw_value in fields.items():
            field_name = _recipe_text_value(field)
            if not field_name:
                raise BadRequest(
                    f'controls.runtime_campaign_overrides.{campaign_name} field is required'
                )
            value = _recipe_non_negative_number(
                raw_value,
                f'controls.runtime_campaign_overrides.{campaign_name}.{field_name}',
            )
            if value is not None:
                clean_fields[field_name] = value
        if clean_fields:
            overrides[campaign_name] = clean_fields
    return overrides


def _recipe_inputs_from_controls(controls: Mapping[str, Any]) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for control_key, input_key in (
        ('feedstock', 'feedstock'),
        ('track', 'track'),
        ('backend', 'backend'),
        ('mre_target_species', 'mre_target_species'),
        ('mre_preset_id', 'mre_preset_id'),
        ('furnace_material_id', 'furnace_material_id'),
    ):
        text = _recipe_text_value(controls.get(control_key))
        if text:
            inputs[input_key] = text
    for control_key, input_key in (
        ('mass_kg', 'mass_kg'),
        ('speed', 'speed'),
        ('c4_max_temp_C', 'c4_max_temp_C'),
        ('furnace_max_T_C', 'furnace_max_T_C'),
        ('mre_max_voltage_V', 'mre_max_voltage_V'),
    ):
        number = (
            _recipe_positive_number(controls.get(control_key), f'controls.{control_key}')
            if control_key in {'mass_kg', 'c4_max_temp_C', 'furnace_max_T_C'}
            else _recipe_non_negative_number(
                controls.get(control_key),
                f'controls.{control_key}',
            )
        )
        if number is not None:
            inputs[input_key] = number
    if 'c5_enabled' in controls:
        inputs['c5_enabled'] = _recipe_bool(
            controls.get('c5_enabled'),
            'controls.c5_enabled',
        )
    overrides = _recipe_runtime_overrides_from_controls(controls)
    if 'runtime_campaign_overrides' in controls:
        inputs['runtime_campaign_overrides'] = overrides
    additives = _recipe_additives_from_controls(controls)
    if 'additives' in controls or 'additives_kg' in controls:
        inputs['additives_kg'] = additives
    return inputs


def _overlay_recipe_capture_from_inputs(
    context: dict[str, Any],
    inputs: Mapping[str, Any],
) -> None:
    capture = copy.deepcopy(context.get('last_recipe_capture') or {})
    if not isinstance(capture, Mapping):
        capture = {}
    tick = copy.deepcopy(capture.get('tick') or {})
    if not isinstance(tick, Mapping):
        tick = {}
    overrides = inputs.get('runtime_campaign_overrides') or {}
    if isinstance(overrides, Mapping) and overrides:
        campaign, fields = next(iter(overrides.items()))
        tick['campaign'] = str(campaign)
        if isinstance(fields, Mapping):
            if fields.get('pO2_mbar') is not None:
                tick['pO2_mbar'] = fields.get('pO2_mbar')
            if fields.get('p_total_mbar') is not None:
                tick['p_total_mbar'] = fields.get('p_total_mbar')
    capture['tick'] = tick
    context['last_recipe_capture'] = capture


def _recipe_payload_setpoints_patch(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = payload.get('setpoints_patch')
    if raw in (None, {}, ''):
        return None
    if not isinstance(raw, Mapping):
        raise BadRequest('setpoints_patch must be an object')
    return normalize_recipe_patch(raw, source='recipes/save setpoints_patch')


def _recipe_context_from_payload(
    context: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    controls = payload.get('controls')
    setpoints_patch = _recipe_payload_setpoints_patch(payload)
    if controls in (None, '') and setpoints_patch is None:
        return copy.deepcopy(dict(context))
    if controls in (None, ''):
        controls = None
    elif not isinstance(controls, Mapping):
        raise BadRequest('controls must be an object')

    updated = copy.deepcopy(dict(context))
    if setpoints_patch is not None:
        updated['setpoints_patch'] = copy.deepcopy(setpoints_patch)
        updated['resolved_setpoints_patch'] = copy.deepcopy(setpoints_patch)
    elif controls is not None:
        updated['setpoints_patch'] = {}
        updated['resolved_setpoints_patch'] = {}

    if controls is not None:
        recipe_inputs = copy.deepcopy(updated.get('recipe_inputs') or {})
        if not isinstance(recipe_inputs, Mapping):
            recipe_inputs = {}
        recipe_inputs = dict(recipe_inputs)
        recipe_inputs.update(_recipe_inputs_from_controls(controls))
        updated['recipe_inputs'] = recipe_inputs
        updated['manual_recipe_controls_supplied'] = True
        _overlay_recipe_capture_from_inputs(updated, recipe_inputs)
    return updated


def _temperature_ladder_from_inputs(inputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    overrides = inputs.get('runtime_campaign_overrides') or {}
    if not isinstance(overrides, Mapping):
        return []
    ladder = []
    for campaign, fields in overrides.items():
        if not isinstance(fields, Mapping):
            continue
        target = (
            fields.get('target_C')
            if fields.get('target_C') is not None
            else fields.get('hold_temp_C')
        )
        ladder.append({
            'stage': str(campaign),
            'target_C': target,
            'ramp_rate_C_per_hr': (
                fields.get('ramp_rate_C_per_hr')
                if fields.get('ramp_rate_C_per_hr') is not None
                else fields.get('ramp_rate')
            ),
            'duration_h': (
                fields.get('duration_h')
                if fields.get('duration_h') is not None
                else fields.get('max_hours')
            ),
        })
    return ladder


def _temperature_ladder_from_patch(patch: Mapping[str, Any]) -> list[dict[str, Any]]:
    campaigns = patch.get('campaigns')
    if not isinstance(campaigns, Mapping):
        return []
    ladder = []
    for campaign, config in campaigns.items():
        if not isinstance(config, Mapping):
            continue
        stages = config.get('stages')
        if not isinstance(stages, list):
            continue
        for stage in stages:
            if not isinstance(stage, Mapping):
                continue
            stage_name = stage.get('name')
            label = str(campaign)
            if stage_name not in (None, ''):
                label = f'{label}.{stage_name}'
            ladder.append({
                'stage': label,
                'target_C': stage.get('target_C'),
                'ramp_rate_C_per_hr': stage.get('ramp_rate_C_per_hr'),
                'duration_h': stage.get('duration_h'),
            })
    return ladder


def _sum_nested_numbers(value: Any) -> float | None:
    if isinstance(value, Mapping):
        total = 0.0
        found = False
        for item in value.values():
            subtotal = _sum_nested_numbers(item)
            if subtotal is not None:
                total += subtotal
                found = True
        return total if found else None
    number = _finite_or_none(value)
    return number


def _headline_results_from_context(context: Mapping[str, Any]) -> dict[str, Any]:
    capture = context.get('last_recipe_capture') or {}
    tick = capture.get('tick') if isinstance(capture, Mapping) else {}
    per_hour = capture.get('per_hour_summary') if isinstance(capture, Mapping) else {}
    completion = context.get('last_completion_payload') or {}
    if not isinstance(tick, Mapping):
        tick = {}
    if not isinstance(per_hour, Mapping):
        per_hour = {}
    if not isinstance(completion, Mapping):
        completion = {}
    buckets = tick.get('process_buckets_kg') or {}
    if not isinstance(buckets, Mapping):
        buckets = {}
    products = completion.get('products') or {}
    if not isinstance(products, Mapping):
        products = {}

    wall_deposit = next((
        value
        for value in (
            per_hour.get('wall_deposit_cumulative_kg'),
            tick.get('wall_deposit_cumulative_kg'),
            tick.get('wall_deposit_kg'),
        )
        if value is not None
    ), None)
    glass_kg = _first_finite(
        products.get('glass'),
        products.get('pure_silica_glass'),
        products.get('industrial_mixed_glass'),
        tick.get('glass_kg'),
    )
    terminal_slag = buckets.get('terminal_slag')
    refractory_rump_kg = _first_finite(
        completion.get('terminal_rump_kg'),
        completion.get('terminal_slag_kg'),
        _sum_nested_numbers(terminal_slag),
    )
    metals = buckets.get('metal_alloy') if isinstance(buckets, Mapping) else {}
    return {
        'oxygen_kg': _first_finite(
            completion.get('oxygen_kg'),
            tick.get('oxygen_kg'),
        ),
        'metals_kg': copy.deepcopy(dict(metals)) if isinstance(metals, Mapping) else {},
        'glass_kg': glass_kg,
        'refractory_rump_kg': refractory_rump_kg,
        'yield_pct': _first_finite(
            completion.get('yield_pct'),
            tick.get('yield_pct'),
            per_hour.get('yield_pct'),
        ),
        'mass_balance_error_pct': (
            _finite_or_none(completion.get('mass_balance_error_pct'))
            if completion.get('mass_balance_error_pct') is not None
            else _finite_or_none(tick.get('mass_balance_error_pct'))
        ),
        'energy_electrical_plus_evaporation_kWh': _first_finite(
            completion.get('energy_electrical_plus_evaporation_kWh'),
            tick.get('energy_electrical_plus_evaporation_cumulative_kWh'),
            tick.get('energy_electrical_plus_evaporation_kWh'),
        ),
        'energy_scope': (
            completion.get('energy_scope')
            or tick.get('energy_scope')
        ),
        'furnace_heat_status': (
            completion.get('furnace_heat_status')
            or tick.get('furnace_heat_status')
        ),
        'wall_deposit_kg': _sum_nested_numbers(wall_deposit),
    }


def _metadata_from_context(title: str, context: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.get('recipe_inputs') or {}
    if not isinstance(inputs, Mapping):
        inputs = {}
    capture = context.get('last_recipe_capture') or {}
    tick = capture.get('tick') if isinstance(capture, Mapping) else {}
    if not isinstance(tick, Mapping):
        tick = {}
    setpoints_patch = (
        context.get('resolved_setpoints_patch')
        or context.get('setpoints_patch')
        or {}
    )
    if not isinstance(setpoints_patch, Mapping):
        setpoints_patch = {}
    temperature_ladder = (
        _temperature_ladder_from_patch(setpoints_patch)
        or _temperature_ladder_from_inputs(inputs)
    )
    campaign = str(
        tick.get('campaign')
        or next(iter((inputs.get('runtime_campaign_overrides') or {}) or {}), '')
    )
    headline_recipe = {
        'feedstock': inputs.get('feedstock'),
        'mass_kg': inputs.get('mass_kg'),
        'campaign': campaign,
        'track': inputs.get('track') or '',
        'backend': inputs.get('backend') or '',
        'hours': tick.get('hour'),
        'temperature_ladder': temperature_ladder,
        'pO2_mbar': tick.get('pO2_mbar'),
        'p_total_mbar': tick.get('p_total_mbar'),
        'furnace_max_T_C': inputs.get('furnace_max_T_C'),
        'mre_enabled': bool(inputs.get('c5_enabled')),
        'mre_target_species': inputs.get('mre_target_species') or '',
        'mre_max_voltage_V': inputs.get('mre_max_voltage_V'),
        'pinned_knobs': {
            'runtime_campaign_overrides': copy.deepcopy(
                inputs.get('runtime_campaign_overrides') or {}
            ),
            'c4_max_temp_C': inputs.get('c4_max_temp_C'),
            'additives_kg': copy.deepcopy(inputs.get('additives_kg') or {}),
            'furnace_material_id': inputs.get('furnace_material_id') or '',
            'mre_preset_id': inputs.get('mre_preset_id') or '',
            'speed': inputs.get('speed'),
        },
    }
    return {
        'title': title,
        'created_utc': datetime.now(timezone.utc)
        .isoformat()
        .replace('+00:00', 'Z'),
        'feedstock': str(inputs.get('feedstock') or ''),
        'campaign': campaign,
        'headline_recipe': headline_recipe,
        'headline_results': _headline_results_from_context(context),
    }


def _recipe_metadata_summary(metadata: Mapping[str, Any]) -> str:
    recipe = metadata.get('headline_recipe') or {}
    results = metadata.get('headline_results') or {}
    if not isinstance(recipe, Mapping):
        recipe = {}
    if not isinstance(results, Mapping):
        results = {}
    parts = []
    feedstock = recipe.get('feedstock') or metadata.get('feedstock')
    campaign = recipe.get('campaign') or metadata.get('campaign')
    if feedstock:
        parts.append(str(feedstock))
    if campaign:
        parts.append(str(campaign))
    oxygen = _finite_or_none(results.get('oxygen_kg'))
    energy = _finite_or_none(
        results.get('energy_electrical_plus_evaporation_kWh'))
    wall = _finite_or_none(results.get('wall_deposit_kg'))
    if oxygen is not None:
        parts.append(f'O2 {oxygen:g} kg')
    if energy is not None:
        parts.append(f'electrical+evap partial {energy:g} kWh')
    if wall is not None:
        parts.append(f'wall deposit {wall:g} kg')
    return ' | '.join(parts) if parts else 'metadata captured'


def _recipe_controls_from_patch(patch: Mapping[str, Any]) -> dict[str, Any]:
    controls: dict[str, Any] = {}
    furnace_max = _finite_or_none(patch.get('furnace_max_T_C'))
    if furnace_max is not None:
        controls['furnace_max_T_C'] = furnace_max
    campaigns = patch.get('campaigns')
    if not isinstance(campaigns, Mapping):
        return controls
    c4 = campaigns.get('C4')
    if isinstance(c4, Mapping):
        controls['lever_campaign'] = 'C4'
        pO2 = c4.get('pO2_mbar_default')
        if pO2 is None:
            pO2 = c4.get('pO2_mbar')
        if isinstance(pO2, (list, tuple)) and pO2:
            pO2 = pO2[0]
        pO2_number = _finite_or_none(pO2)
        if pO2_number is not None:
            controls['pO2_mbar'] = pO2_number
        p_total = _finite_or_none(c4.get('p_total_mbar_default'))
        if p_total is not None:
            controls['p_total_mbar'] = p_total
        hold_temp = _finite_or_none(c4.get('default_hold_T_C'))
        if hold_temp is None:
            hold_temp = _finite_or_none(c4.get('hold_temp_C'))
        if hold_temp is not None:
            controls['stage_temp_C'] = hold_temp
        temp_range = c4.get('temp_range_C')
        if isinstance(temp_range, (list, tuple)) and temp_range:
            target = _finite_or_none(temp_range[0])
            if target is not None and 'stage_temp_C' not in controls:
                controls['stage_temp_C'] = target
            cap = _finite_or_none(temp_range[-1])
            if cap is not None:
                controls['c4_max_temp_C'] = cap
    c5 = campaigns.get('C5')
    if isinstance(c5, Mapping):
        cap = _finite_or_none(c5.get('allow_mre_voltage_cap_V'))
        if cap is not None and cap > 0:
            controls['mre_enabled'] = True
            controls['mre_max_voltage_V'] = cap
    return controls


def _recipe_controls_from_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    recipe = metadata.get('headline_recipe') or {}
    if not isinstance(recipe, Mapping):
        recipe = {}
    pinned = recipe.get('pinned_knobs') or {}
    if not isinstance(pinned, Mapping):
        pinned = {}

    controls: dict[str, Any] = {}
    simple_fields = {
        'feedstock': recipe.get('feedstock') or metadata.get('feedstock'),
        'mass_kg': recipe.get('mass_kg'),
        'track': recipe.get('track'),
        'backend': recipe.get('backend'),
        'speed': pinned.get('speed'),
        'furnace_material_id': pinned.get('furnace_material_id'),
        'mre_preset_id': pinned.get('mre_preset_id'),
        'mre_enabled': recipe.get('mre_enabled'),
        'mre_target_species': recipe.get('mre_target_species'),
        'mre_max_voltage_V': recipe.get('mre_max_voltage_V'),
        'c4_max_temp_C': pinned.get('c4_max_temp_C'),
    }
    for key, value in simple_fields.items():
        if value not in (None, ''):
            controls[key] = copy.deepcopy(value)

    additives = pinned.get('additives_kg')
    if isinstance(additives, Mapping):
        controls['additives'] = copy.deepcopy(dict(additives))

    overrides = pinned.get('runtime_campaign_overrides')
    if isinstance(overrides, Mapping) and overrides:
        try:
            CampaignManager.validate_runtime_campaign_overrides(dict(overrides))
        except (TypeError, ValueError) as exc:
            raise BadRequest(
                f'invalid saved runtime_campaign_overrides: {exc}'
            ) from exc
        controls['runtime_campaign_overrides'] = copy.deepcopy(dict(overrides))
        campaign, fields = next(iter(overrides.items()))
        controls['lever_campaign'] = str(campaign)
        if isinstance(fields, Mapping):
            field_map = {
                'pO2_mbar': 'pO2_mbar',
                'hold_temp_C': 'stage_temp_C',
                'max_hours': 'stage_duration_h',
                'ramp_rate': 'stage_ramp_C_per_h',
            }
            for source_key, control_key in field_map.items():
                if fields.get(source_key) is not None:
                    controls[control_key] = copy.deepcopy(fields.get(source_key))
    return controls


def _recipe_controls_for_response(
    patch: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    controls = _recipe_controls_from_patch(patch)
    controls.update(_recipe_controls_from_metadata(metadata))
    return controls


@bp.route('/recipes', methods=['GET'])
def list_recipes():
    recipes = []
    try:
        for recipe_path in sorted(_recipe_library_dir().glob('*.yaml')):
            metadata = read_recipe_metadata(recipe_path)
            if not metadata:
                continue
            recipes.append({
                'name': recipe_path.stem,
                'title': metadata['title'],
                'summary': _recipe_metadata_summary(metadata),
            })
    except RecipeIOError as exc:
        return _json_error(str(exc), 500)
    return jsonify(recipes)


@bp.route('/recipes/save', methods=['POST'])
def save_recipe():
    try:
        payload = _recipe_request_payload()
        title = _validated_recipe_title(payload.get('title'))
        sid = str(payload.get('sid') or '').strip()
        if not sid:
            raise BadRequest('recipe save requires a socket session id')
        from web.events import RecipeStateError, recipe_save_context
        context = _recipe_context_from_payload(recipe_save_context(sid), payload)
        metadata = _metadata_from_context(title, context)
        setpoints_patch = _canonical_recipe_patch_from_context(context)
        name = _recipe_slug(title)
        destination = recipe_library_path(name, library_dir=_recipe_library_dir())
        if not _write_recipe_patch_exclusive(
            destination,
            setpoints_patch,
            metadata=metadata,
        ):
            return _json_error(f'recipe already exists: {name}', 409)
    except BadRequest as exc:
        return _json_error(exc.description, 400)
    except (RecipeIOError, RecipeStateError, ValueError) as exc:
        return _json_error(str(exc), 400)
    return jsonify({
        'name': destination.stem,
        'title': metadata['title'],
        'summary': _recipe_metadata_summary(metadata),
    })


@bp.route('/recipes/load', methods=['POST'])
def load_recipe():
    try:
        payload = _recipe_request_payload()
        raw_name = str(payload.get('name') or '').strip()
        if not raw_name:
            raise BadRequest('recipe name is required')
        source = recipe_library_path(raw_name, library_dir=_recipe_library_dir())
        setpoints_patch = load_recipe_patch(source)
        metadata = read_recipe_metadata(source)
        raw_document = yaml.safe_load(source.read_text(encoding='utf-8')) or {}
        cost_parameters = (
            read_recipe_cost_parameters(source)
            if isinstance(raw_document, Mapping)
            and RECIPE_COST_PARAMETERS_KEY in raw_document
            else None
        )
        controls = _recipe_controls_for_response(setpoints_patch, metadata)
        sid = str(payload.get('sid') or '').strip()
        applied_to_session = False
        if sid:
            from web.events import apply_loaded_recipe_patch_to_state
            applied_to_session = apply_loaded_recipe_patch_to_state(
                sid,
                setpoints_patch,
                cost_parameters=cost_parameters,
            )
    except BadRequest as exc:
        return _json_error(exc.description, 400)
    except RecipeIOError as exc:
        return _json_error(str(exc), 400)
    title = metadata.get('title') or source.stem
    summary = _recipe_metadata_summary(metadata) if metadata else ''
    response = {
        'name': source.stem,
        'title': title,
        'summary': summary,
        'setpoints_patch': setpoints_patch,
        'controls': controls,
        'applied_to_session': applied_to_session,
    }
    if cost_parameters is not None:
        response['cost_parameters'] = cost_parameters
    return jsonify(response)


@bp.route('/')
def simulator():
    """Main simulator interface."""
    session.setdefault('ledger_client_id', os.urandom(16).hex())
    feedstocks, debug_feedstocks = load_feedstock_groups()
    return render_template(
        'simulator.html',
        feedstocks=feedstocks,
        mre_presets=_mre_preset_catalog_payload(),
        c4_operator_presets=_c4_operator_preset_payload(),
        knudsen_config=_knudsen_config_payload(),
        condensation_train_stages=_condensation_train_stage_payload(),
        debug_feedstocks=debug_feedstocks,
        debug_mode=debug_feedstocks_enabled(),
    )


# Only true viewer assets are servable: send_from_directory alone would
# publish EVERY regular file under the source dir (freeze_sample.py, dotfiles).
_REPORT_VIEWER_ASSET_SUFFIXES = ('.html', '.js', '.css', '.json')


@bp.route('/report/', defaults={'asset': 'index.html'}, methods=['GET'])
@bp.route('/report/<path:asset>', methods=['GET'])
def report_viewer(asset: str):
    basename = asset.rsplit('/', 1)[-1]
    if basename.startswith('.') or not asset.lower().endswith(
        _REPORT_VIEWER_ASSET_SUFFIXES
    ):
        abort(404)
    return send_from_directory(REPORT_VIEWER_DIR, asset)


def _ledger_get(resource: str, **params):
    client_id = str(session.get('ledger_client_id') or '')
    if not client_id:
        return _json_error('ledger request requires an initialized browser session', 400)
    try:
        from web.events import read_ledger_api_for_client
        return jsonify(read_ledger_api_for_client(client_id, resource, **params))
    except LookupError as exc:
        return _json_error(str(exc), 404)
    except KeyError as exc:
        identifier = exc.args[0] if exc.args else ''
        return _json_error(f'unknown ledger {resource}: {identifier}', 404)
    except (TypeError, ValueError) as exc:
        return _json_error(str(exc), 400)


@bp.route('/api/ledger/accounts', methods=['GET'])
def ledger_accounts_api():
    return _ledger_get('accounts')


@bp.route('/api/ledger/account/<path:account_id>', methods=['GET'])
def ledger_account_api(account_id: str):
    return _ledger_get(
        'account',
        account=account_id,
        pattern=request.args.get('pattern'),
        units=request.args.get('units', 'kg'),
    )


@bp.route('/api/ledger/account', methods=['GET'])
def ledger_account_pattern_api():
    pattern = str(request.args.get('pattern') or '').strip()
    if not pattern:
        return _json_error('ledger account pattern is required', 400)
    return _ledger_get(
        'account',
        pattern=pattern,
        units=request.args.get('units', 'kg'),
    )


@bp.route('/api/ledger/snapshot', methods=['GET'])
def ledger_snapshot_api():
    return _ledger_get('snapshot')


@bp.route('/api/ledger/views/<view_name>', methods=['GET'])
def ledger_view_api(view_name: str):
    return _ledger_get('view', view=view_name)


@bp.route('/api/runs', methods=['GET'])
def runs_api():
    return jsonify(list_runs())


@bp.route('/api/runs', methods=['POST'])
def submit_run_api():
    oversized = _reject_oversized_run_command()
    if oversized is not None:
        return oversized
    payload = request.get_json(silent=True)
    if not isinstance(payload, Mapping):
        return _typed_json_error(
            'run request body must be a JSON object',
            'invalid_run_request',
            400,
        )
    session.setdefault('ledger_client_id', os.urandom(16).hex())
    client_id = str(session['ledger_client_id'])
    from app import socketio
    from web.events import RunCommandError, submit_run_command

    try:
        result = submit_run_command(socketio, payload, client_id=client_id)
    except RunCommandError as exc:
        return jsonify(exc.response_payload()), exc.status_code
    except RuntimeError as exc:
        return _typed_json_error(str(exc), 'run_command_failed', 500)
    return jsonify(result), (200 if result['idempotent_replay'] else 201)


@bp.route('/api/runs/draft', methods=['POST'])
def validate_run_draft_api():
    oversized = _reject_oversized_run_command()
    if oversized is not None:
        return oversized
    payload = request.get_json(silent=True)
    if not isinstance(payload, Mapping):
        return _typed_json_error(
            'run draft body must be a JSON object',
            'invalid_run_request',
            400,
        )
    session.setdefault('ledger_client_id', os.urandom(16).hex())
    client_id = str(session['ledger_client_id'])
    from web.events import RunCommandError, validate_run_draft

    try:
        result = validate_run_draft(payload, client_id=client_id)
    except RunCommandError as exc:
        return jsonify(exc.response_payload()), exc.status_code
    except RuntimeError as exc:
        return _typed_json_error(str(exc), 'run_command_failed', 500)
    return jsonify(result)


@bp.route('/api/runs/<run_id>/cancel', methods=['POST'])
def cancel_run_api(run_id: str):
    oversized = _reject_oversized_run_command()
    if oversized is not None:
        return oversized
    from app import socketio
    from web.events import cancel_run_command

    client_id = str(session.get('ledger_client_id') or '')
    try:
        result = cancel_run_command(
            socketio,
            run_id,
            client_id=client_id,
        )
    except RuntimeError as exc:
        return _typed_json_error(str(exc), 'run_cancel_failed', 500)
    if result is None:
        try:
            artifact = load_run_artifact(run_id)
        except ValueError as exc:
            return _typed_json_error(str(exc), 'invalid_run_id', 400)
        except RunStoreCorruptionError as exc:
            return _typed_json_error(str(exc), 'run_store_corruption', 500)
        if artifact is not None:
            return _typed_json_error(
                'run is already terminal',
                'run_not_active',
                409,
            )
        return _typed_json_error('run not found', 'run_not_found', 404)
    if result['status'] == 'terminal':
        return _typed_json_error(
            'run is already terminal',
            'run_not_active',
            409,
        )
    return jsonify(result)


@bp.route('/api/runs/<run_id>', methods=['GET'])
def run_artifact_api(run_id: str):
    try:
        artifact = load_run_artifact(run_id)
    except RunStoreCorruptionError as exc:
        return jsonify({
            'error': str(exc),
            'error_type': 'run_store_corruption',
        }), 500
    except ValueError as exc:
        return _json_error(str(exc), 400)
    if artifact is None:
        return _json_error('run artifact not found', 404)
    return jsonify(artifact)


@bp.route('/api/runs/<run_id>/run.yaml', methods=['GET'])
def run_manifest_api(run_id: str):
    try:
        artifact = load_run_artifact(run_id)
    except RunStoreCorruptionError as exc:
        return _typed_json_error(str(exc), 'run_store_corruption', 500)
    except ValueError as exc:
        return _typed_json_error(str(exc), 'invalid_run_id', 400)
    if artifact is None:
        return _typed_json_error('run artifact not found', 'run_not_found', 404)

    header = artifact['header']
    snapshot = header.get('recipe_snapshot')
    if not isinstance(snapshot, Mapping):
        return _typed_json_error(
            'artifact carries no recipe snapshot; export unavailable',
            'run_manifest_unavailable',
            409,
        )
    setpoints_patch = snapshot.get('setpoints_patch')
    pins = snapshot.get('pins')
    schema_version = snapshot.get('recipe_schema_version')
    if (
        not isinstance(setpoints_patch, Mapping)
        or not isinstance(pins, list)
        or not all(isinstance(pin, str) for pin in pins)
        or not isinstance(schema_version, str)
        or not schema_version
    ):
        return _typed_json_error(
            'artifact recipe snapshot is malformed; export unavailable',
            'run_manifest_unavailable',
            409,
        )

    manifest: dict[str, Any] = {}
    if header.get('feedstock_id') is not None:
        manifest['feedstock'] = copy.deepcopy(header['feedstock_id'])
    if header.get('charge_mass_kg') is not None:
        manifest['mass_kg'] = copy.deepcopy(header['charge_mass_kg'])
    if header.get('seed') is not None:
        manifest['seed'] = copy.deepcopy(header['seed'])
    manifest.update({
        'setpoints_patch': copy.deepcopy(dict(setpoints_patch)),
        'pins': copy.deepcopy(pins),
        'recipe_schema_version': schema_version,
    })
    body = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
    return Response(
        body,
        content_type='application/yaml; charset=utf-8',
        headers={
            'Content-Disposition': f'attachment; filename="run-{run_id}.yaml"',
        },
    )


@bp.route('/api/runs/<run_id>/meta', methods=['PATCH'])
def run_meta_api(run_id: str):
    oversized = _reject_oversized_run_command()
    if oversized is not None:
        return oversized
    payload = request.get_json(silent=True)
    if not isinstance(payload, Mapping):
        return jsonify({
            'error': 'run metadata request body must be a JSON object',
            'error_type': 'invalid_run_metadata',
        }), 400
    try:
        metadata = get_run_store().update_meta(run_id, payload)
    except FileNotFoundError:
        return jsonify({
            'error': 'run artifact not found',
            'error_type': 'run_not_found',
        }), 404
    except ValueError as exc:
        return jsonify({
            'error': str(exc),
            'error_type': 'invalid_run_metadata',
        }), 400
    except RunStoreCorruptionError as exc:
        return jsonify({
            'error': str(exc),
            'error_type': 'run_store_corruption',
        }), 500
    return jsonify({'run_id': run_id, **metadata})


@bp.route('/api/wall-risk')
def wall_risk_api():
    payload = wall_advisory_payload(
        _query_species(),
        wall_temp_offset_C=_query_wall_temp_offset_C(),
        pO2_mbar=_query_optional_float('pO2_mbar', minimum=0.0),
        p_buffer_mbar=_query_optional_float('p_buffer_mbar', minimum=0.0),
    )
    return jsonify(payload)


@bp.route('/partials/wall-risk-panel')
def wall_risk_panel_partial():
    payload = wall_advisory_payload(
        _query_species(),
        wall_temp_offset_C=_query_wall_temp_offset_C(),
        pO2_mbar=_query_optional_float('pO2_mbar', minimum=0.0),
        p_buffer_mbar=_query_optional_float('p_buffer_mbar', minimum=0.0),
    )
    return render_template('partials/wall_risk_panel.html', wall_risk=payload)


@bp.route('/api/ceramic-rump')
def ceramic_rump_api():
    payload = ceramic_rump_payload(
        _query_composition_wt_pct(),
        tolerance_wt_pct=_query_optional_float('tolerance_wt_pct', minimum=0.0),
    )
    return jsonify(payload)


@bp.route('/partials/ceramic-rump-panel')
def ceramic_rump_panel_partial():
    payload = ceramic_rump_payload(
        _query_composition_wt_pct(),
        tolerance_wt_pct=_query_optional_float('tolerance_wt_pct', minimum=0.0),
    )
    return render_template(
        'partials/ceramic_rump_panel.html',
        ceramic_rump=payload,
    )


@bp.route('/api/vapor-pressure-authority')
def vapor_pressure_authority_api():
    payload = vapor_pressure_authority_payload(
        _query_vapor_pressure_diagnostics()
    )
    return jsonify(payload)


@bp.route('/partials/vapor-pressure-authority-panel')
def vapor_pressure_authority_panel_partial():
    payload = vapor_pressure_authority_payload(
        _query_vapor_pressure_diagnostics()
    )
    return render_template(
        'partials/vapor_pressure_authority_panel.html',
        vapor_pressure_authority=payload,
    )


@bp.route('/optimizer')
def optimizer_page():
    """Optimizer results page plus async CLI launch form."""
    return render_template(
        'optimizer.html',
        **_optimizer_table_context(),
        **_optimizer_launch_context(),
    )


def _load_default_thermal_train_artifact() -> dict[str, Any] | None:
    try:
        artifact = json.loads(
            THERMAL_TRAIN_DEFAULT_ARTIFACT_PATH.read_text(encoding='utf-8')
        )
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(artifact, Mapping):
        return None
    config = artifact.get('config')
    report = artifact.get('thermal_train_report')
    if (
        artifact.get('artifact_schema_version') != THERMAL_TRAIN_DEFAULT_ARTIFACT_SCHEMA
        or artifact.get('artifact_id') != THERMAL_TRAIN_DEFAULT_ARTIFACT_ID
        or not isinstance(config, Mapping)
        or not isinstance(report, Mapping)
        or report.get('schema_version') != 'thermal-train-report-v2'
        or config.get('mass_kg') != 1000.0
        or config.get('track') != 'pyrolysis'
        or config.get('campaign') != 'C3_NA'
        or config.get('c3_shuttle_enabled') is not True
        or config.get('c5_enabled') is not False
    ):
        return None
    return copy.deepcopy(dict(artifact))


def _thermal_train_context() -> dict[str, Any]:
    run_id = str(request.args.get('run_id') or '').strip()
    cache_key = str(request.args.get('cache_key') or '').strip()
    default_artifact_id = str(request.args.get('default_artifact') or '').strip()
    if run_id or cache_key:
        if not run_id or not cache_key:
            return {
                'data_state': 'no_data',
                'report': None,
                'message': 'Both optimizer run ID and cache key are required.',
                'instructions': 'Open an optimizer result and supply both identifiers, or start a simulator run.',
                'selection': {'run_id': run_id, 'cache_key': cache_key},
            }
        resolved = _optimizer_result_row(run_id, cache_key)
        if resolved is None:
            return {
                'data_state': 'no_data',
                'report': None,
                'message': 'Selected optimizer artifact was not found or is not corpus-compatible.',
                'instructions': 'Choose a current stored result or start a simulator run.',
                'selection': {'run_id': run_id, 'cache_key': cache_key},
            }
        _root, _run_dir, row = resolved
        result_blob = _json_value(_row_value(row, 'result_blob'), {})
        report = None
        if isinstance(result_blob, Mapping):
            candidate = result_blob.get('thermal_train_report')
            if not isinstance(candidate, Mapping):
                candidate = result_blob.get('thermal_train')
            if isinstance(candidate, Mapping):
                report = copy.deepcopy(dict(candidate))
        if report is None:
            return {
                'data_state': 'artifact',
                'report': None,
                'message': 'Artifact selected, but it has no authoritative thermal-train report.',
                'instructions': 'Regenerate it with a CLI version that emits thermal_train_report; cumulative O2 is not substituted for the melt-offgas series.',
                'selection': {'run_id': run_id, 'cache_key': cache_key},
            }
        return {
            'data_state': 'artifact',
            'report': report,
            'message': 'Read-only optimizer artifact report.',
            'instructions': None,
            'selection': {'run_id': run_id, 'cache_key': cache_key},
        }

    if default_artifact_id:
        artifact = (
            _load_default_thermal_train_artifact()
            if default_artifact_id == THERMAL_TRAIN_DEFAULT_ARTIFACT_ID
            else None
        )
        if artifact is None:
            return {
                'data_state': 'no_data',
                'report': None,
                'message': 'Versioned default thermal-train artifact is unavailable or invalid.',
                'instructions': 'Regenerate it with scripts/generate_thermal_train_default_fixture.py.',
                'selection': {'run_id': '', 'cache_key': ''},
                'default_artifact': None,
            }
        return {
            'data_state': 'artifact',
            'report': artifact['thermal_train_report'],
            'message': 'Read-only versioned default artifact report.',
            'instructions': None,
            'selection': {'run_id': '', 'cache_key': ''},
            'default_artifact': {
                'artifact_id': artifact['artifact_id'],
                'artifact_schema_version': artifact['artifact_schema_version'],
                'config': artifact['config'],
                'provenance': artifact.get('provenance', {}),
            },
        }

    session.setdefault('ledger_client_id', os.urandom(16).hex())
    client_id = str(session.get('ledger_client_id') or '')
    try:
        from web.events import read_ledger_api_for_client

        view = read_ledger_api_for_client(client_id, 'view', view='thermal_train')
    except LookupError:
        view = None
    if isinstance(view, Mapping) and isinstance(view.get('data'), Mapping):
        return {
            'data_state': 'live',
            'report': copy.deepcopy(dict(view['data'])),
            'message': 'Live run history via the thermal_train named ledger view.',
            'instructions': None,
            'selection': {'run_id': '', 'cache_key': ''},
        }
    return {
        'data_state': 'no_data',
        'report': None,
        'message': 'No live run or selected optimizer artifact.',
        'instructions': 'Start a simulator run, or enter a stored optimizer run ID and cache key. This page never launches a simulation.',
        'selection': {'run_id': '', 'cache_key': ''},
        'default_artifact': {
            'artifact_id': THERMAL_TRAIN_DEFAULT_ARTIFACT_ID,
            'artifact_schema_version': THERMAL_TRAIN_DEFAULT_ARTIFACT_SCHEMA,
            'config': {
                'mass_kg': 1000.0,
                'track': 'pyrolysis',
                'campaign': 'C3_NA',
                'c3_shuttle_enabled': True,
                'c5_enabled': False,
            },
        },
    }


@bp.route('/thermal-train', methods=['GET'])
def thermal_train_page():
    """Detached downstream thermal-train diagnostic page."""
    return render_template('thermal_train.html', **_thermal_train_context())


@bp.route('/partials/thermal-train-report', methods=['GET'])
def thermal_train_report_partial():
    """HTMX partial for a live or stored thermal-train report."""
    return render_template(
        'partials/thermal_train_report.html',
        **_thermal_train_context(),
    )


def _query_species() -> list[str]:
    values = request.args.getlist('species')
    if len(values) == 1 and ',' in values[0]:
        values = values[0].split(',')
    return [value.strip() for value in values if value.strip()]


def _query_composition_wt_pct() -> dict[str, float]:
    composition: dict[str, float] = {}
    for key, value in request.args.items():
        if key == 'tolerance_wt_pct':
            continue
        amount = _optional_query_float(value, name=key)
        if amount is not None:
            if amount < 0.0:
                raise BadRequest(f'{key} must be non-negative')
            composition[key] = amount
    return composition


def _query_vapor_pressure_diagnostics() -> dict[str, object]:
    allowed = {
        'vapor_pressure_backend_status',
        'vapor_pressure_backend_status_reason',
        'vapor_pressure_fallback_source',
        'authoritative_for_requested_vapor_pressure',
    }
    diagnostics: dict[str, object] = {}
    for key in allowed:
        if key not in request.args:
            continue
        value = request.args.get(key)
        if key == 'authoritative_for_requested_vapor_pressure':
            diagnostics[key] = str(value).lower() == 'true'
        else:
            diagnostics[key] = value
    return diagnostics


def _query_wall_temp_offset_C() -> float:
    offset = _query_optional_float('wall_temp_offset_C')
    if offset is None:
        return 0.0
    # For every zone, T_K = T_zone_C + offset_C + 273.15 must stay non-negative.
    minimum = -CELSIUS_TO_KELVIN_OFFSET - min(WALL_ZONE_TEMPERATURES_C.values())
    if offset <= minimum:
        raise BadRequest(
            f'wall_temp_offset_C must be > {minimum:g} to stay above absolute zero'
        )
    return offset


def _query_optional_float(
    name: str,
    *,
    minimum: float | None = None,
) -> float | None:
    return _optional_query_float(
        request.args.get(name),
        name=name,
        minimum=minimum,
    )


def _optional_query_float(
    value: object,
    *,
    name: str,
    minimum: float | None = None,
) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BadRequest(f"{name} must be a finite number") from None
    if not math.isfinite(number):
        raise BadRequest(f"{name} must be a finite number")
    if minimum is not None and number < minimum:
        raise BadRequest(f'{name} must be >= {minimum:g}')
    return number


@bp.route('/partials/optimizer-table')
def optimizer_table_partial():
    """HTMX partial: one winner per feedstock/profile selector."""
    return render_template(
        'partials/optimizer_table.html',
        **_optimizer_table_context(),
    )


@bp.route('/partials/optimizer-jobs')
def optimizer_jobs_partial():
    """HTMX partial: current optimizer CLI job queue."""
    return render_template(
        'partials/optimizer_jobs.html',
        **_optimizer_jobs_context(),
    )


@bp.route('/partials/optimizer-jobs/<job_id>')
def optimizer_job_detail_partial(job_id: str):
    """HTMX partial: one optimizer CLI job detail panel."""
    job = _optimizer_job_runner().get_job(job_id)
    if job is None:
        return render_template('optimizer_not_found.html'), 404
    return render_template('partials/optimizer_job_detail_panel.html', job=job)


@bp.route('/api/optimizer/jobs')
def optimizer_jobs_api():
    """Return submitted optimizer CLI jobs from the disk-backed register."""
    return jsonify({
        'jobs_dir': str(_optimizer_runs_root() / 'jobs'),
        'jobs': _optimizer_job_runner().list_jobs(),
    })


@bp.route('/api/optimizer/certify', methods=['POST'])
@bp.route('/optimizer/certify', methods=['POST'])
def optimizer_certify_submit():
    """Enqueue an exact live-fill certify job for one stored optimizer result."""
    job_request, error = _parse_optimizer_certify_request(_optimizer_job_payload())
    if error is not None or job_request is None:
        if _wants_json_response():
            return jsonify({'error': error}), 400
        context = _optimizer_launch_context()
        context['job_error'] = error
        return render_template('partials/optimizer_jobs.html', **context), 400

    job, capacity_error = _submit_optimizer_job(job_request)
    if capacity_error is not None or job is None:
        if _wants_json_response():
            return jsonify({'error': capacity_error}), 429
        context = _optimizer_launch_context()
        context['job_error'] = capacity_error
        return render_template('partials/optimizer_jobs.html', **context), 429
    if _wants_json_response():
        return jsonify({'job': job}), 202
    return render_template(
        'partials/optimizer_job_detail_panel.html',
        job=job,
    ), 202


@bp.route('/api/optimizer/jobs', methods=['POST'])
@bp.route('/optimizer/jobs', methods=['POST'])
def optimizer_job_submit():
    """Validate and enqueue an optimizer CLI job without importing eval code."""
    job_request, error = _parse_optimizer_job_request(_optimizer_job_payload())
    if error is not None or job_request is None:
        if _wants_json_response():
            return jsonify({'error': error}), 400
        context = _optimizer_launch_context()
        context['job_error'] = error
        return render_template('partials/optimizer_jobs.html', **context), 400

    job, capacity_error = _submit_optimizer_job(job_request)
    if capacity_error is not None or job is None:
        if _wants_json_response():
            return jsonify({'error': capacity_error}), 429
        context = _optimizer_launch_context()
        context['job_error'] = capacity_error
        return render_template('partials/optimizer_jobs.html', **context), 429
    if _wants_json_response():
        return jsonify({'job': job}), 202
    context = _optimizer_jobs_context()
    context['submitted_job'] = job
    return render_template('partials/optimizer_jobs.html', **context), 202


@bp.route('/api/optimizer/jobs/<job_id>')
def optimizer_job_detail_api(job_id: str):
    """Return one optimizer CLI job from the disk-backed register."""
    job = _optimizer_job_runner().get_job(job_id)
    if job is None:
        return jsonify({'error': 'Optimizer job not found'}), 404
    return jsonify({'job': job})


@bp.route('/optimizer/jobs/<job_id>')
def optimizer_job_detail(job_id: str):
    """Pollable optimizer CLI job detail page."""
    job = _optimizer_job_runner().get_job(job_id)
    if job is None:
        return render_template('optimizer_not_found.html'), 404
    return render_template('optimizer_job.html', job=job)


@bp.route('/optimizer/runs/<path:run_id>/results/<cache_key>')
def optimizer_result_detail(run_id: str, cache_key: str):
    """Read-only result detail and stored recipe audit view."""
    resolved = _optimizer_result_row(run_id, cache_key)
    if resolved is None:
        return render_template('optimizer_not_found.html'), 404
    root, run_dir, row = resolved
    return render_template(
        'optimizer_detail.html',
        result=_result_detail_model(root, run_dir, row),
    )


@bp.route('/optimizer/runs/<path:run_id>/results/<cache_key>/recipe.yaml')
def optimizer_result_yaml(run_id: str, cache_key: str):
    """Download the stored EvalSpec recipe/provenance as YAML."""
    resolved = _optimizer_result_row(run_id, cache_key)
    if resolved is None:
        return jsonify({'error': 'Optimizer result not found'}), 404
    root, run_dir, row = resolved
    result = _result_detail_model(root, run_dir, row)
    body = yaml.safe_dump(
        _result_yaml_payload(result),
        sort_keys=False,
        allow_unicode=False,
    )
    filename = (
        f'{_safe_filename_part(run_id, "run")}-'
        f'{_safe_filename_part(result["candidate_id"], "candidate")}-'
        'recipe.yaml'
    )
    response = Response(
        body,
        mimetype='application/x-yaml',
    )
    response.headers.set('Content-Disposition', 'attachment', filename=filename)
    return response


@bp.route('/optimizer/runs/<path:run_id>/download.rpstudy.zip')
def optimizer_run_download(run_id: str):
    """Download one optimizer run as a save-format bundle."""
    resolved = _optimizer_run_dir_for_id(run_id)
    if resolved is None:
        return jsonify({'error': 'Optimizer run not found'}), 404
    _root, run_dir = resolved
    temp_dir = tempfile.TemporaryDirectory(prefix='regolith-study-export-')
    export_root = Path(temp_dir.name) / run_dir.name
    export_root.mkdir()
    try:
        for name in ALLOWED_MEMBERS:
            if name == 'artifact.index.json':
                continue
            source = run_dir / name
            if source.is_file():
                shutil.copy2(source, export_root / name)
        bundle_path = export_study_bundle(
            export_root,
            output_path=Path(temp_dir.name) / f'{run_dir.name}.rpstudy.zip',
        )
    except (FileNotFoundError, ValueError) as exc:
        temp_dir.cleanup()
        return jsonify({'error': str(exc)}), 409
    try:
        response = send_file(
            bundle_path,
            mimetype='application/zip',
            as_attachment=True,
            download_name=bundle_path.name,
            max_age=0,
        )
    except Exception:
        temp_dir.cleanup()
        raise
    response.call_on_close(temp_dir.cleanup)
    return response


def _uploaded_bundle_tempfile() -> tuple[Path | None, str | None]:
    upload = request.files.get('bundle')
    if upload is None or not upload.filename:
        return None, 'bundle file is required'
    suffix = '.rpstudy.zip' if str(upload.filename).endswith('.rpstudy.zip') else '.zip'
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    total = 0
    path = Path(handle.name)
    try:
        with handle:
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > BUNDLE_CAP_BYTES:
                    return path, 'bundle exceeds 512MB cap'
                handle.write(chunk)
    except OSError as exc:
        return path, f'could not store upload: {exc}'
    return path, None


@bp.route('/api/optimizer/import', methods=['POST'])
@bp.route('/optimizer/import', methods=['POST'])
def optimizer_import_upload():
    """Import an uploaded save-format bundle into runs/imported/."""
    tmp_path, error = _uploaded_bundle_tempfile()
    if error is not None:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass
        code = 413 if '512MB' in error else 400
        if _wants_json_response():
            return jsonify({'error': error}), code
        return render_template(
            'optimizer.html',
            import_error=error,
            **_optimizer_table_context(),
            **_optimizer_launch_context(),
        ), code
    assert tmp_path is not None
    try:
        imported = import_study_bundle(
            tmp_path,
            _optimizer_runs_root(),
            uploader_note=str(request.form.get('uploader_note') or ''),
            origin={'filename': request.files['bundle'].filename},
        )
    except ImportBundleError as exc:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        if _wants_json_response():
            return jsonify({'error': str(exc)}), 400
        return render_template(
            'optimizer.html',
            import_error=str(exc),
            **_optimizer_table_context(),
            **_optimizer_launch_context(),
        ), 400
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    model = imported_study_model(imported.path, _optimizer_runs_root())
    if _wants_json_response():
        status = 200 if imported.deduped else 201
        return jsonify({'imported': model, 'deduped': imported.deduped}), status
    return render_template('optimizer_imported.html', imported=model), 201


@bp.route('/api/optimizer/imported')
def optimizer_imported_runs():
    """Return imported studies from the quarantine namespace only."""
    root = _optimizer_runs_root()
    return jsonify({
        'runs_dir': str(root),
        'imported': imported_studies(root),
    })


@bp.route('/optimizer/imported/<path:study_id>')
def optimizer_imported_detail(study_id: str):
    """Dedicated imported-study view composed from summary plus overlay."""
    root = _optimizer_runs_root()
    import_dir = root / 'imported' / study_id
    if not is_imported_path(import_dir, root) or not import_dir.is_dir():
        return render_template('optimizer_not_found.html'), 404
    try:
        model = imported_study_model(import_dir, root)
    except (ImportBundleError, OSError, ValueError):
        return render_template('optimizer_not_found.html'), 404
    return render_template('optimizer_imported.html', imported=model)


@bp.route('/api/feedstocks')
def get_feedstocks():
    """Return available feedstocks as JSON."""
    return jsonify(load_visible_feedstocks(include_custom=True))


@bp.route('/api/setpoints')
def get_setpoints():
    """Return campaign setpoints as JSON."""
    return jsonify(_load_yaml('setpoints.yaml'))


@bp.route('/api/condensation-temperature-config')
def get_condensation_temperature_config():
    """Return normalized condensation-temperature override provenance."""
    return jsonify(_condensation_temperature_config_payload())


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
    return jsonify(_optimizer_feedstock_profiles_payload())


@bp.route('/api/mre-preset-catalog')
def mre_preset_catalog():
    """Return the shared MRE target preset catalog for web forms."""
    return jsonify({'presets': _mre_preset_catalog_payload()})


@bp.route('/api/furnace-material-catalog')
def furnace_material_catalog():
    """Return selectable furnace material caps for web start forms."""
    return jsonify({'materials': _furnace_material_catalog_payload()})


@bp.route('/api/knudsen-config')
def knudsen_config():
    """Return read-only Knudsen display constants from the condensation model."""
    return jsonify(_knudsen_config_payload())


@bp.route('/partials/mre-preset-catalog')
def mre_preset_catalog_partial():
    """HTMX fragment for the shared MRE target preset catalog."""
    return render_template(
        'partials/mre_preset_catalog.html',
        presets=_mre_preset_catalog_payload(),
    )


@bp.route('/api/optimizer/leaderboard')
def optimizer_leaderboard():
    """Return a top-N objective leaderboard from stored ResultStore rows."""
    root = _optimizer_runs_root()
    run_dirs = _optimizer_run_dirs(root)
    run_id = _request_arg('run_id')
    if run_id:
        run_dirs = [
            run_dir
            for run_dir in run_dirs
            if _optimizer_run_id(run_dir, root) == run_id
        ]
        if not run_dirs:
            return jsonify({'error': 'Optimizer run not found'}), 404

    objective_metric = (
        _request_arg('objective_metric')
        or _request_arg('objective')
    )
    entries, selected_metric, data_digest_scope, excluded_counts = _leaderboard_entries(
        run_dirs,
        feedstock_id=_request_arg('feedstock_id') or _request_arg('feedstock'),
        profile_id=_request_arg('profile_id') or _request_arg('profile'),
        fidelity=_request_arg('fidelity'),
        objective_metric=objective_metric,
        limit=_request_limit(),
    )
    return jsonify({
        **_optimizer_version_context(),
        'objective_metric': selected_metric,
        'limit': _request_limit(),
        'data_digest_scope': data_digest_scope,
        **excluded_counts,
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

    mass_kg, error_response = _positive_finite_arg(
        'mass_kg',
        default=1000.0,
        maximum=MAX_ADDITIVE_CALC_MASS_KG,
    )
    if error_response is not None:
        return error_response
    comp = normalized_feedstock_component_masses_kg(fs, mass_kg)

    # Absolute kg of each oxide in the batch
    FeO_kg = comp.get('FeO', 0.0)
    TiO2_kg = comp.get('TiO2', 0.0)
    Cr2O3_kg = comp.get('Cr2O3', 0.0)
    Al2O3_kg = comp.get('Al2O3', 0.0)
    P2O5_kg = comp.get('P2O5', 0.0)
    SO3_kg = comp.get('SO3', 0.0)

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
                           section=section, data=data,
                           c4_operator_presets=_c4_operator_preset_payload(
                               setpoints
                           ))
