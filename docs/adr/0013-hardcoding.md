# 0013. The hardcoding scan: what it finds, how it streams, and its screen

- Status: Accepted
- Date: 2026-10-27
- Issue: #46

## Context

M2-4 adds `hardcoding`. `docs/design.ja.md` §9 says it walks a folder by extension and returns strings, numbers of four or more digits, IPs, URLs, and email addresses with line numbers, skipping comment lines. §5 does not say how the screen looks. §4.1 says a Developer Tools screen shows its result the moment the input changes.

The source, PyDevToys (`src/services/hardcoding_service.py`), reads each file as UTF-8, skips lines that start with `#`, `//`, `/*`, `*`, or `<!--`, and returns one row per hit of five regular expressions. Its string pattern accepts a mismatched pair such as `"...'`. It excludes a path when an exclude text appears anywhere in it, so `bin` also excludes `cabinet`. It skips a file it cannot read without a word.

Measured with the source's patterns: this repository, 31 files, took 0.23 seconds. `D:\home\Programs\PythonScripts`, 33,721 files and 14.3 million lines, took 380 seconds and gave 4.06 million hits, 3.40 million of them strings and 0.63 million numbers.

An experiment in Edge with htmx 2.0.11: a form with several text fields and a submit button sends once on Enter in any field, and does not send on Tab.

## Decision

- The scan streams. It counts the files first, then reads them one by one and yields `Progress`, so the screen shows how far it has got and Cancel stops it. A scan that can take minutes cannot be a request that returns once.
- Findings are yielded one by one, not collected. The CLI prints all of them; the GUI keeps the first 1,000 for its table and counts every finding by kind. Millions of rows fit neither in memory as objects nor in a browser as HTML.
- A string is `"..."` or `'...'` with the same quote at both ends, three or more characters, and no line break. Several kinds on one spot are one finding each. The five kinds can be chosen; the default is all, because strings are 84% of the hits and bury the rest.
- Excludes match a folder name whole. The default adds `.git` and `.venv` to the source's `node_modules`, `bin`, and `obj`, because a Python virtual environment holds thousands of `.py` files. Links and junctions are not followed.
- A file is read as UTF-8, with or without a BOM, and then as CP932. Old Japanese C# and VB sources are often Shift_JIS. A file neither reads is reported by name and reason.
- The screen keeps the category of §5.1, Code, but uses the form of §4.2: fields, a Start button, progress, and Cancel. It runs on Start or Enter, not on leaving a field. §4.1 says so for a tool that walks a folder.
- A cancelled scan shows what it found so far, marked as cancelled, as a `BatchResult` does.
- The CLI prints `path:line  kind  <JSON string>`, which editors and terminals open, and no progress, so the output pipes. Unreadable files go to stderr.

## Consequences

`docs/design.ja.md` §4.1, §5, §5.2, and §9 carry the decision. `docs/conventions.md` (Streaming, CLI) holds the stream and the output. #47 builds it.
