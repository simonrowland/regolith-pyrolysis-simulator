"""Flask routes for the simulator interface."""

from pathlib import Path
from flask import Blueprint, render_template, jsonify, request
import yaml

bp = Blueprint('web', __name__,
               template_folder='templates',
               static_folder='static')

DATA_DIR = Path(__file__).parent.parent / 'data'


def _load_yaml(filename):
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


@bp.route('/')
def simulator():
    """Main simulator interface."""
    feedstocks = _load_yaml('feedstocks.yaml')
    return render_template('simulator.html', feedstocks=feedstocks)


@bp.route('/api/feedstocks')
def get_feedstocks():
    """Return available feedstocks as JSON."""
    feedstocks = _load_yaml('feedstocks.yaml')
    custom = _load_yaml('custom_compositions.yaml')
    if custom:
        feedstocks.update(custom)
    return jsonify(feedstocks)


@bp.route('/api/setpoints')
def get_setpoints():
    """Return campaign setpoints as JSON."""
    return jsonify(_load_yaml('setpoints.yaml'))


@bp.route('/api/feedstock/<key>')
def get_feedstock(key):
    """Return a single feedstock's details."""
    feedstocks = _load_yaml('feedstocks.yaml')
    custom = _load_yaml('custom_compositions.yaml')
    data = feedstocks.get(key) or (custom.get(key) if custom else None)
    if data is None:
        return jsonify({'error': 'Feedstock not found'}), 404
    return jsonify(data)


@bp.route('/partials/feedstock-card/<key>')
def feedstock_card(key):
    """HTMX partial: composition table for a feedstock."""
    feedstocks = _load_yaml('feedstocks.yaml')
    custom = _load_yaml('custom_compositions.yaml')
    data = feedstocks.get(key) or (custom.get(key) if custom else None)
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
    feedstocks = _load_yaml('feedstocks.yaml')
    custom = _load_yaml('custom_compositions.yaml')
    fs = feedstocks.get(key) or (custom.get(key) if custom else None)
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
