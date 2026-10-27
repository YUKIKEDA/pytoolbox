# 0014. Folder size: streaming, a CSV without writing a file, and its screen

- Status: Accepted
- Date: 2026-10-27
- Issue: #52

## Context

M2-5 adds `folder-size`. `docs/design.ja.md` §9 says it lists the direct files and folders largest first, sums folders, does not follow links or junctions, counts unreadable items, takes `--top`, and gives every item as a CSV in UTF-8 with a BOM. §5 does not say how the screen looks.

The source, FolderSizer (`folder_sizer.py`), sums logical sizes, scans each direct folder in one of eight threads, shows links as size 0, and writes the CSV to a file named by `--csv FILE`.

Writing a file makes a tool one that writes, and §4.2 gives such a tool a lock per target and a history entry. Those are stage 3 (M3-1, M3-2), which comes after this row.

Measured with a size-only walk: `D:\home\Programs\PythonScripts`, 350,000 files, took 2.4 seconds; `C:\Users\yuuuu`, 2.2 million files and 1.1 TiB, took 104 seconds. An experiment in Edge: a `data:text/csv` link with `download`, holding 20,000 rows (1.6 million characters), saved the file with its BOM and every byte as sent.

## Decision

- The walk streams: `Progress` after each direct folder, and Cancel stops it. A walk can take minutes.
- The tool writes no file. The CLI's `--csv` prints the CSV to stdout, to be saved with `> file`; the GUI embeds the CSV in the result as a download link. The row stays in the read-only stage 2 and needs no lock or history. Rejected: `--csv FILE` as the source has it, which would need stage 3 first; and a download endpoint, which would keep results on the server.
- Sizes are logical, as Explorer's "Size" shows them, so the numbers can be checked against Explorer. Units step by 1024.
- A direct link or junction is a row of size 0 with kind `link`, so its absence does not puzzle.
- A cancelled walk shows the direct files, links, and the folders summed whole, marked as cancelled. A half-summed folder would show a size that is too small.
- The screen has the folder and Start on the left, and on the right the progress and Cancel, then the total, the item count, the unreadable count, the table largest first with each row's share as a percentage and a bar, and Download CSV. The table shows the first 1,000 rows; the CSV has all. The point is to see what takes the space, and a bar shows it at a glance.
- §4.2's console stays for tools that log. Folder size logs nothing, so its screen shows progress and the table.

## Consequences

`docs/design.ja.md` §5, §5.2, and §9 carry the decision. `docs/conventions.md` (Streaming, CLI, GUI HTTP) holds the stream, the output, and the CSV link. #53 builds it.
