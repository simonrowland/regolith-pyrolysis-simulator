"""
Regolith Pyrolysis Simulator — Flask App
========================================

Creates the Flask + SocketIO web application.

Two interfaces:
  /                 — Simulator (full parameter control, engine selection)
  /lunar-operator   — Operator game (multi-line refinery management)

Usage:
    python regolith-pyrolysis-run.py
    # Then open http://localhost:3000
"""

import os
import secrets
from pathlib import Path

from flask import Flask
from flask_socketio import SocketIO

socketio = SocketIO()


def _load_secret_key() -> str:
    """Load a stable local secret, generating one on first run if needed."""
    env_secret = os.environ.get('FLASK_SECRET_KEY')
    if env_secret:
        return env_secret

    secret_path = Path(__file__).parent / 'instance' / 'flask_secret_key'
    if secret_path.exists():
        return secret_path.read_text().strip()

    secret_path.parent.mkdir(mode=0o700, exist_ok=True)
    generated = secrets.token_urlsafe(32)
    secret_path.write_text(generated + '\n')
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass
    return generated


def create_app():
    app = Flask(
        __name__,
        template_folder='web/templates',
        static_folder='web/static',
    )
    app.config['SECRET_KEY'] = _load_secret_key()

    # Register simulator web blueprint
    from web.routes import bp as web_bp
    app.register_blueprint(web_bp)

    # Register game blueprint (serves from /lunar-operator)
    from game.routes import bp as game_bp
    app.register_blueprint(game_bp)

    # Initialize SocketIO
    socketio.init_app(app, async_mode='threading')

    # Register SocketIO event handlers
    from web.events import register_events as register_web_events
    from game.events import register_events as register_game_events
    register_web_events(socketio)
    register_game_events(socketio)

    return app


def main():
    """Run the local development server."""
    host = os.environ.get('REGOLITH_HOST', '127.0.0.1')
    port = int(os.environ.get('REGOLITH_PORT', '3000'))
    debug = os.environ.get('REGOLITH_FLASK_DEBUG', '').lower() in (
        '1', 'true', 'yes', 'on')
    allow_unsafe_werkzeug = (
        host in {'127.0.0.1', 'localhost', '::1'}
        or os.environ.get('REGOLITH_ALLOW_UNSAFE_WERKZEUG', '').lower()
        in ('1', 'true', 'yes', 'on')
    )
    app = create_app()
    base_url = f"http://{host}:{port}"
    print(f"Starting Regolith Pyrolysis Simulator on {base_url}")
    print(f"  Simulator:       {base_url}/")
    print(f"  Lunar Operator:  {base_url}/lunar-operator")
    socketio.run(app, host=host, port=port, debug=debug,
                 allow_unsafe_werkzeug=allow_unsafe_werkzeug)


if __name__ == '__main__':
    main()
