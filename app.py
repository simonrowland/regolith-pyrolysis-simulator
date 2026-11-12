"""
Regolith Pyrolysis Simulator — Flask App
========================================

Creates the Flask + SocketIO web application.

Interfaces:
  /                 — Simulator (full parameter control, engine selection)

Usage:
    python regolith-pyrolysis-run.py
    # Then open http://localhost:3000
"""

import ipaddress
import os
import secrets
import stat
import sys
from pathlib import Path

from flask import Flask
from flask_socketio import SocketIO

socketio = SocketIO()


def _request_authority_config() -> dict[str, object]:
    """Build request authority metadata from the server bind environment."""
    run_config = _run_config_from_env()
    # Loopback by default; REGOLITH_ALLOWED_HOSTNAMES (comma-separated) is the
    # operator knob for serving beyond localhost — without it a LAN client's
    # Host header fails the pinned-authority check with no recourse.
    extra_hostnames = frozenset(
        host.strip().lower()
        for host in os.environ.get('REGOLITH_ALLOWED_HOSTNAMES', '').split(',')
        if host.strip()
    )
    return {
        'REGOLITH_BIND_PORT': run_config['port'],
        'REGOLITH_ALLOWED_HOSTNAMES': frozenset({
            'localhost',
            '127.0.0.1',
        }) | extra_hostnames,
    }


def _load_secret_key() -> str:
    """Load a stable local secret, generating one on first run if needed."""
    env_secret = os.environ.get('FLASK_SECRET_KEY')
    if env_secret:
        return env_secret

    secret_path = Path(__file__).parent / 'instance' / 'flask_secret_key'
    secret_path.parent.mkdir(mode=0o700, exist_ok=True)
    try:
        fd = os.open(
            secret_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        fd = os.open(secret_path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'r', encoding='utf-8') as secret_file:
            metadata = os.fstat(secret_file.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError(f'Flask secret path is not a regular file: {secret_path}')
            if metadata.st_mode & 0o077:
                os.fchmod(secret_file.fileno(), 0o600)
            secret = secret_file.read().strip()
        if not secret:
            raise RuntimeError(f'Flask secret file is empty: {secret_path}')
        return secret

    os.fchmod(fd, 0o600)
    generated = secrets.token_urlsafe(32)
    with os.fdopen(fd, 'w', encoding='utf-8') as secret_file:
        secret_file.write(generated + '\n')
    return generated


def create_app():
    app = Flask(
        __name__,
        template_folder='web/templates',
        static_folder='web/static',
    )
    app.config['SECRET_KEY'] = _load_secret_key()
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    app.config.update(_request_authority_config())

    # Register simulator web blueprint
    from web.routes import bp as web_bp
    app.register_blueprint(web_bp)

    # Initialize SocketIO
    socketio.init_app(app, async_mode='threading')

    # Register SocketIO event handlers
    from web.events import register_events as register_web_events
    register_web_events(socketio)

    return app


def _env_flag(name: str) -> bool:
    return os.environ.get(name, '').lower() in ('1', 'true', 'yes', 'on')


def _is_loopback_host(host: str) -> bool:
    if host == 'localhost':
        return True
    cleaned = host
    if cleaned.startswith('[') and cleaned.endswith(']'):
        cleaned = cleaned[1:-1]
    elif cleaned.startswith('[') or cleaned.endswith(']'):
        return False
    try:
        return ipaddress.ip_address(cleaned).is_loopback
    except ValueError:
        return False


def _run_config_from_env() -> dict:
    host = os.environ.get('REGOLITH_HOST', '127.0.0.1')
    raw_port = os.environ.get('REGOLITH_PORT', '3000')
    try:
        port = int(raw_port)
        if not 0 < port < 65536:
            raise ValueError
    except ValueError:
        sys.exit(
            f'REGOLITH_PORT must be an integer in 1..65535, got {raw_port!r}')

    if _env_flag('REGOLITH_ALLOW_UNSAFE_WERKZEUG'):
        sys.exit(
            'REGOLITH_ALLOW_UNSAFE_WERKZEUG is no longer supported; '
            'use a real WSGI server for non-loopback deployments.'
        )

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
    socketio.run(app, **run_config)


if __name__ == '__main__':
    main()
