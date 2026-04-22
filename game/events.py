"""SocketIO event handlers for the Lunar Operator game mode."""

from pathlib import Path

import yaml
from flask import request

from game.refinery import RefineryManager


DATA_DIR = Path(__file__).parent.parent / 'data'


def _load_yaml(filename):
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


# Active game sessions keyed by session ID
_games: dict = {}


def register_events(socketio):
    """Register all SocketIO events for the game UI."""

    @socketio.on('game_start')
    def handle_game_start(data):
        """Initialize a new game session with N furnace lines."""
        sid = request.sid
        num_lines = int(data.get('num_lines', 15))

        feedstocks = _load_yaml('feedstocks.yaml')
        setpoints = _load_yaml('setpoints.yaml')
        vapor_pressures = _load_yaml('vapor_pressures.yaml')

        manager = RefineryManager(
            setpoints, feedstocks, vapor_pressures,
            num_lines=num_lines,
        )
        _games[sid] = manager

        socketio.emit('game_status', {
            'status': 'initialized',
            'num_lines': num_lines,
        }, room=sid)

    @socketio.on('game_add_line')
    def handle_add_line(data):
        """Add a new batch to a furnace line."""
        sid = request.sid
        manager = _games.get(sid)
        if not manager:
            socketio.emit('game_status', {
                'status': 'error', 'message': 'No game session.',
            }, room=sid)
            return

        line_id = str(data.get('line_id', '1'))
        feedstock = data.get('feedstock', 'lunar_mare_low_ti')
        mass_kg = float(data.get('mass_kg', 1000))

        try:
            manager.add_line(line_id, feedstock, mass_kg)
        except (ValueError, KeyError) as e:
            socketio.emit('game_status', {
                'status': 'error', 'message': str(e),
            }, room=sid)
            return

        socketio.emit('game_status', {
            'status': 'line_added',
            'line_id': line_id,
            'feedstock': feedstock,
        }, room=sid)

    @socketio.on('game_step')
    def handle_step():
        """Advance the game clock by one hour (all lines)."""
        sid = request.sid
        manager = _games.get(sid)
        if not manager:
            return

        results = manager.step_all()

        # Check for decisions
        decisions = manager.get_decisions_pending()
        for d in decisions:
            socketio.emit('game_decision', d, room=sid)

        # Emit tick with all line states + inventory
        socketio.emit('game_tick', {
            'game_hour': manager.game_hour,
            'lines': results,
            'inventory': manager.inventory.snapshot(),
        }, room=sid)

    @socketio.on('game_decide')
    def handle_decision(data):
        """Apply a decision to a line."""
        sid = request.sid
        manager = _games.get(sid)
        if not manager:
            return

        line_id = str(data.get('line_id', ''))
        choice = data.get('choice', '')
        manager.apply_decision(line_id, choice)

    @socketio.on('game_harvest')
    def handle_harvest(data):
        """Harvest products from a line into shared inventory."""
        sid = request.sid
        manager = _games.get(sid)
        if not manager:
            return

        line_id = str(data.get('line_id', ''))
        products = manager.harvest_products(line_id)

        socketio.emit('game_status', {
            'status': 'harvested',
            'line_id': line_id,
            'products': {k: round(v, 2) for k, v in products.items()},
            'inventory': manager.inventory.snapshot(),
        }, room=sid)

    @socketio.on('disconnect')
    def handle_game_disconnect():
        sid = request.sid
        _games.pop(sid, None)
