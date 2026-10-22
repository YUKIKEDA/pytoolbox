"""A child process that runs one call at a time, so a call stuck in C code can still be stopped."""

import multiprocessing
import signal
import threading
from collections.abc import Callable
from multiprocessing.process import BaseProcess
from types import TracebackType
from typing import Any, Protocol, Self

from pytoolbox.errors import OperationError

_POLL_SECONDS = 0.05
"""How often a wait without a limit wakes, so Ctrl+C reaches the caller."""


class Connection(Protocol):
    """One end of a `multiprocessing` pipe: `Connection`, or `PipeConnection` on Windows."""

    def send(self, obj: object) -> None:
        """Pickle and send an object."""
        ...

    def recv(self) -> Any:  # noqa: ANN401 - an unpickled object from the other process
        """Receive and unpickle an object."""
        ...

    def poll(self, timeout: float | None = 0.0) -> bool:
        """Say whether there is data to read, waiting up to `timeout` seconds."""
        ...

    def close(self) -> None:
        """Close this end."""
        ...


class Worker:
    """A Python worker process for calls that may never return, such as a `re` match.

    The process starts on the first call and stays for the next one. A call that runs past its
    timeout, or that Ctrl+C interrupts, terminates the process; the next call starts a new one.
    Calls from several threads run one after another.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: BaseProcess | None = None
        self._connection: Connection | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def call[T](self, function: Callable[[], T], timeout: float | None = None) -> T:
        """Run a function in the worker process and return its result.

        Args:
            function: The call, picklable: a module-level function, or a `functools.partial` of one.
            timeout: Seconds to wait. `None` waits until the call returns or Ctrl+C.

        Returns:
            What the function returned.

        Raises:
            OperationError: The call timed out, or the worker process exited during it.
            KeyboardInterrupt: Ctrl+C during the wait. The worker process is terminated first.
        """
        with self._lock:
            connection = self._connect()
            try:
                connection.send(function)
                if not _wait(connection, timeout):
                    self.close()
                    msg = f"Timed out after {timeout:g} s."
                    raise OperationError(msg)
                succeeded, value = connection.recv()
            except KeyboardInterrupt:
                self.close()
                raise
            except (EOFError, OSError) as error:
                self.close()
                msg = "The worker process exited during the call."
                raise OperationError(msg) from error
        if succeeded:
            return value
        raise value

    def close(self) -> None:
        """Terminate the worker process, if one is running."""
        if self._process is not None:
            self._process.terminate()
            self._process.join()
        if self._connection is not None:
            self._connection.close()
        self._process = None
        self._connection = None

    def _connect(self) -> Connection:
        if self._process is not None and self._connection is not None:
            if self._process.is_alive():
                return self._connection
            self.close()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_main, args=(child,), daemon=True)
        process.start()
        child.close()
        self._process = process
        self._connection = parent
        return parent


def _wait(connection: Connection, timeout: float | None) -> bool:
    if timeout is not None:
        return connection.poll(timeout)
    while not connection.poll(_POLL_SECONDS):
        pass
    return True


def _main(connection: Connection) -> None:
    # Ctrl+C reaches every process on the console. The caller decides when the worker stops.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _serve(connection)


def _serve(connection: Connection) -> None:
    while True:
        try:
            function = connection.recv()
        except EOFError:
            return
        try:
            reply = (True, function())
        except Exception as error:  # noqa: BLE001 - the caller raises it again
            reply = (False, error)
        connection.send(reply)
