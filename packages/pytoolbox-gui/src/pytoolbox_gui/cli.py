"""Start the GUI server for `pytoolbox gui`."""

import socket
import threading
import time
import webbrowser
from collections.abc import Callable
from typing import Protocol

import uvicorn

from pytoolbox.errors import OperationError
from pytoolbox_gui.server import HOST, PORT, create_app


def ensure_port_free(port: int = PORT) -> None:
    """Refuse to start when another process holds the port.

    Args:
        port: The port on `127.0.0.1`.

    Raises:
        OperationError: The port is in use.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, port))
        except OSError as error:
            msg = f"Port {port} on {HOST} is in use."
            raise OperationError(msg) from error


class _Startable(Protocol):
    started: bool
    should_exit: bool


def _open_when_started(server: _Startable, url: str, open_browser: Callable[[str], object]) -> None:
    while not server.started:
        if server.should_exit:
            return
        time.sleep(0.05)
    open_browser(url)


def serve(*, open_browser: Callable[[str], object] = webbrowser.open) -> None:
    """Serve the GUI on `127.0.0.1:8000` and open it in a browser.

    Blocks until the server stops.

    Args:
        open_browser: Opens a URL. The default is the system browser.

    Raises:
        OperationError: The port is in use. No browser opens.
    """
    ensure_port_free(PORT)
    config = uvicorn.Config(create_app(), host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    url = f"http://{HOST}:{PORT}/"
    opener = threading.Thread(
        target=_open_when_started, args=(server, url, open_browser), daemon=True
    )
    opener.start()
    server.run()
