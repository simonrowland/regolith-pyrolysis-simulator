"""Runtime import surface for the stdlib-only engine install configuration."""

import engine_install_config as _owner

from engine_install_config import *  # noqa: F403
from engine_install_config import __all__


def config_path():
    """Delegate each lookup so tests can patch the owner's single path seam."""
    return _owner.config_path()
