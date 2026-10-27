# 0011. What resx-compare compares, and its screen

- Status: Accepted
- Date: 2026-10-27
- Issue: #34

## Context

M2-3 adds `resx-compare`. `docs/design.ja.md` §9 says it compares the `data` names of two `.resx` files and returns match, mismatch, and one side only. It does not say whether a mismatch compares values, what a broken file does, or how the screen looks.

The source, PyDevToys (`src/services/resx_service.py`), compares the `<value>` text of each `data` name and puts every key in one table. A duplicate name silently keeps the last one. Any parse failure becomes one generic error. The browser reads the file as UTF-8.

An experiment on CPython 3.12.8 with expat 2.6.4 passed bytes to `xml.etree.ElementTree.fromstring`. UTF-8 with and without a BOM, and UTF-16 with a BOM, were read by their declaration and BOM. A file declared `encoding="shift_jis"` raised `ValueError: multi-byte encodings are not supported`. The [ElementTree documentation](https://docs.python.org/3.12/library/xml.etree.elementtree.html) does not say how bytes are decoded, so this was checked by experiment.

## Decision

- A name in both files is a match when the `<value>` text is exactly equal, case and spaces included, and a mismatch otherwise. `<comment>` and `type` are not compared. A missing `<value>` and an empty one are both the empty string. The point is to find untranslated keys and stale values.
- XML that does not parse, a `data` without `name`, and a name that appears twice are `InputError` with the file and the reason. Keeping the last duplicate would give a wrong result without a word, and .NET's build rejects duplicates too.
- The file goes to the parser as bytes, so the XML declaration and the BOM decide the encoding. An encoding the parser cannot read, such as Shift_JIS, is `InputError` with the file and the encoding. Visual Studio and `ResXResourceWriter` write UTF-8.
- The standard library parser is enough. The [Python XML vulnerabilities table](https://docs.python.org/3.12/library/xml.html#xml-vulnerabilities) says ElementTree does not expand external entities, expat 2.4.1 and newer resist billion laughs and quadratic blowup, and 2.6.0 and newer resist large tokens. The files are the user's own. `defusedxml` is not added.
- The screen has two path fields on the left and no button. It compares when a field's change is committed (Enter or leaving the field) and both paths are filled. Comparing on every keystroke would report a missing file for each half-typed path.
- The right shows the count of each status, and a table of the keys that differ, in key order, colored by status. Matching keys show only as a count, so a few differences are not buried in hundreds of matches.
- The CLI prints the keys that differ, and `--all` adds the matches. Differences exit `0`; `1` stays `InputError`.
- Rejected: Python decoding the declared encoding before parsing. It would read Shift_JIS, but it needs our own declaration reader and a rule for a BOM that disagrees with the declaration, for files that the .resx tools do not write.

## Consequences

`docs/design.ja.md` §5 and §9 carry the decision. `docs/conventions.md` (GUI HTTP, CLI) holds the screen's trigger and the CLI output. #35 builds it.
