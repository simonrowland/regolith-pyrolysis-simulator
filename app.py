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

import ipaddress
import os
import secrets
import sys
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


def _env_flag(name: str) -> bool:
    return os.environ.get(name, '').lower() in ('1', 'true', 'yes', 'on')


def _is_loopback_host(host: str) -> bool:
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host.strip('[]')).is_loopback
    except ValueError:
        return False


def _run_config_from_env() -> dict:
    host = os.environ.get('REGOLITH_HOST', '127.0.0.1')
    raw_port = os.environ.get('REGOLITH_PORT', '3000')
    try:
        port = int(raw_port)
    except ValueError:
        sys.exit(f'REGOLITH_PORT must be an integer, got {raw_port!r}')

    debug = _env_flag('REGOLITH_FLASK_DEBUG')
    is_loopback = _is_loopback_host(host)
    if debug and not is_loopback:
        raise RuntimeError(
            'REGOLITH_FLASK_DEBUG may only be enabled on a loopback host; '
            f'got REGOLITH_HOST={host!r}'
        )

    return {
        'host': host,
        'port': port,
        'debug': debug,
        'allow_unsafe_werkzeug': is_loopback,
    }


def main():
    """Run the local development server."""
    run_config = _run_config_from_env()
    host = run_config['host']
    port = run_config['port']
    app = create_app()
    base_url = f"http://{host}:{port}"
    print(f"Starting Regolith Pyrolysis Simulator on {base_url}")
    print(f"  Simulator:       {base_url}/")
    print(f"  Lunar Operator:  {base_url}/lunar-operator")
    socketio.run(app, **run_config)


if __name__ == '__main__':
    main()
