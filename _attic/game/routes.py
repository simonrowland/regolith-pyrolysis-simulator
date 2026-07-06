"""Flask routes for the Lunar Operator game interface."""

from pathlib import Path
from flask import Blueprint, render_template, jsonify
import yaml

bp = Blueprint('game', __name__,
               template_folder='templates',
               static_folder='static',
               url_prefix='/lunar-operator')

DATA_DIR = Path(__file__).parent.parent / 'data'


def _load_yaml(filename):
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


@bp.route('/')
def operator():
    """Main game interface — multi-line refinery overview."""
    feedstocks = _load_yaml('feedstocks.yaml')
    return render_template('operator.html', feedstocks=feedstocks)
