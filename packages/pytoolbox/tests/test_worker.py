import _thread
import multiprocessing
import operator
import os
import signal
import threading
import time
from collections.abc import Iterator
from functools import partial

import pytest

from pytoolbox import worker as worker_module
from pytoolbox.errors import InputError, OperationError
from pytoolbox.tools.dev.regex import match_pattern
from pytoolbox.worker import Worker

STUCK = partial(match_pattern, "(a+)+$", "a" * 40 + "b")


@pytest.fixture
def worker() -> Iterator[Worker]:
    with Worker() as started:
        yield started


def test_call_runs_in_another_process(worker: Worker) -> None:
    assert worker.call(partial(operator.add, 1, 2)) == 3
    assert worker.call(os.getpid) != os.getpid()


def test_the_process_stays_for_the_next_call(worker: Worker) -> None:
    assert worker.call(os.getpid) == worker.call(os.getpid)


def test_an_exception_in_the_call_is_raised_again(worker: Worker) -> None:
    with pytest.raises(InputError, match="Invalid pattern"):
        worker.call(partial(match_pattern, "(", "x"))


def test_a_stuck_match_times_out_and_the_next_call_gets_a_new_process(worker: Worker) -> None:
    first = worker.call(os.getpid)
    start = time.perf_counter()

    with pytest.raises(OperationError, match=r"Timed out after 0\.5 s"):
        worker.call(STUCK, timeout=0.5)

    assert time.perf_counter() - start < 5
    assert worker.call(os.getpid) != first


def test_a_process_that_exits_during_the_call_is_operation_error(worker: Worker) -> None:
    with pytest.raises(OperationError, match="exited during the call"):
        worker.call(partial(os._exit, 3))

    assert worker.call(partial(operator.add, 1, 1)) == 2


def test_a_process_that_died_between_calls_is_replaced(worker: Worker) -> None:
    pid = worker.call(os.getpid)
    os.kill(pid, signal.SIGTERM)
    process = worker._process
    assert process is not None
    process.join(5)

    assert worker.call(os.getpid) != pid


def test_ctrl_c_while_waiting_without_a_limit_terminates_the_process(worker: Worker) -> None:
    pid = worker.call(os.getpid)
    threading.Timer(0.3, _thread.interrupt_main).start()

    with pytest.raises(KeyboardInterrupt):
        worker.call(STUCK)

    assert worker.call(os.getpid) != pid


def test_close_without_a_process_does_nothing() -> None:
    Worker().close()


def test_serve_answers_each_call_until_the_pipe_closes() -> None:
    parent, child = multiprocessing.Pipe()
    thread = threading.Thread(target=worker_module._serve, args=(child,))
    thread.start()

    parent.send(partial(operator.add, 2, 3))
    assert parent.recv() == (True, 5)
    parent.send(partial(int, "x"))
    succeeded, error = parent.recv()
    parent.close()
    thread.join(5)

    assert not succeeded
    assert isinstance(error, ValueError)
    assert not thread.is_alive()


def test_main_ignores_ctrl_c_and_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers: list[tuple[int, object]] = []

    def record(signum: int, handler: object) -> None:
        handlers.append((signum, handler))

    monkeypatch.setattr(signal, "signal", record)
    parent, child = multiprocessing.Pipe()
    parent.close()

    worker_module._main(child)

    assert handlers == [(signal.SIGINT, signal.SIG_IGN)]
