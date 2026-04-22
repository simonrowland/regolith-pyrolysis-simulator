"""
Regolith Pyrolysis Simulator — Application Entry Point
=======================================================

Starts the Flask + SocketIO web server on port 3000.

Two interfaces:
  /                 — Simulator (full parameter control, engine selection)
  /lunar-operator   — Operator game (multi-line refinery management)

Usage:
    python app.py
    # Then open http://localhost:3000
"""

from flask import Flask
from flask_socketio import SocketIO

socketio = SocketIO()


def create_app():
    app = Flask(
        __name__,
        template_folder='web/templates',
        static_folder='web/static',
    )
    app.config['SECRET_KEY'] = 'regolith-pyrolysis-dev-key'

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


if __name__ == '__main__':
    app = create_app()
    print("Starting Regolith Pyrolysis Simulator on http://localhost:3000")
    print("  Simulator:       http://localhost:3000/")
    print("  Lunar Operator:  http://localhost:3000/lunar-operator")
    socketio.run(app, host='0.0.0.0', port=3000, debug=True,
                 allow_unsafe_werkzeug=True)
