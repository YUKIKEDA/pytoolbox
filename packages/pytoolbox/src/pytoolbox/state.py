r"""Locations for pytoolbox persistent state such as locks and history."""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

_APP_DIR = "pytoolbox"


def state_dir(
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    r"""Return the per-user state directory for locks and run history.

    Uses ``%LOCALAPPDATA%\pytoolbox`` on Windows (falling back to
    ``~/AppData/Local/pytoolbox``) and ``$XDG_STATE_HOME/pytoolbox`` elsewhere
    (falling back to ``~/.local/state/pytoolbox``).

    Args:
        env: Environment mapping to read. Defaults to ``os.environ``.
        platform: Platform name such as ``"win32"``. Defaults to ``sys.platform``.
        home: Home directory used when the environment variable is unset or empty.
            Defaults to ``Path.home()``.

    Returns:
        The state directory path.
    """
    active_env = os.environ if env is None else env
    active_platform = sys.platform if platform is None else platform
    active_home = Path.home() if home is None else home
    if active_platform == "win32":
        if local_app_data := active_env.get("LOCALAPPDATA"):
            return Path(local_app_data) / _APP_DIR
        return active_home / "AppData" / "Local" / _APP_DIR
    if xdg_state_home := active_env.get("XDG_STATE_HOME"):
        return Path(xdg_state_home) / _APP_DIR
    return active_home / ".local" / "state" / _APP_DIR
