# 0010. The regex screen and its worker process

- Status: Accepted
- Date: 2026-10-22
- Issue: #26

## Context

M2-2 adds `regex`. `docs/design.ja.md` §9 names the flags (`g`, `i`, `m`) and their meaning, with Python's flag names. It does not say how the screen looks. The GUI matches on every keystroke, so a half-typed pattern such as `(a+)+$` meets the text before the user finishes it. §2 says a Python tool runs in process and stops through `cancel`.

An experiment on CPython 3.12 matched `(a+)+$` against 28 `a` and one `b` in a thread. Other threads kept running, but the matching thread could not be stopped and held a CPU until the process exited. A second experiment matched the same pattern against 40 `a` and one `b` on the main thread and raised `KeyboardInterrupt` there after 1 second with `_thread.interrupt_main`; after 30 seconds the match was still running. A `re` match runs in C and checks neither `cancel` nor Ctrl+C.

## Decision

- The screen has a one-line pattern, `g`, `i`, and `m` check boxes, and the text on the left. Any change matches again. The right shows the text with matches highlighted and a list of matches: number, start and end, the matched text, and numbered and named groups. The point of a regex tester is to see where a pattern hits and what each group took.
- Matching is Python's `re`, so the CLI and the GUI give the same result. The flags stay the three of §9.
- Both the CLI and the GUI match in a worker process. This is the one exception to "a Python tool runs in process" in §2, because `re` honors neither `cancel` nor Ctrl+C. The worker is not the runner, which runs external commands and scripts.
- The GUI keeps one worker and waits 1 second. On a timeout it terminates the worker, starts a new one for the next match, and reports `OperationError`. The screen matches on every keystroke, so it must keep answering.
- The CLI starts a worker for its match and waits with no limit, because a heavy match over a large input piped to the CLI is a real use. On Ctrl+C it terminates the worker and exits `130`.
- Rejected for the CLI: the 1-second limit of the GUI, which fails a real heavy match with no way around it; and matching in process, which leaves the user no way to stop it but closing the terminal.
- The third-party `regex` package has a `timeout` argument, but it is a dependency the design does not name, and its engine differs from `re`. A length limit on the input does not help: `(a+)+$` stalls on about 30 characters.

## Consequences

`docs/design.ja.md` §2, §5, and §9 carry the decision. `docs/conventions.md` (Subprocess) holds the worker's rules. #27 builds it.
