# Roadmap

The current row's ID lives only in this file. Acceptance text lives on the Issue.

Start the current row only. `docs/design.ja.md` §8 names the stages and §9 the initial tools. Their order is this table.

## Current work

M2-5.

| ID   | Title                                                            | Issue | Status      |
| ---- | ---------------------------------------------------------------- | ----- | ----------- |
| M1-1 | Add the foundation: error and result types, tool list, GUI shell | #12   | Done        |
| M2-1 | Add text-count                                                   | #3    | Done        |
| M2-2 | Add regex                                                        | #27   | Done        |
| M2-3 | Add resx-compare                                                 | #35   | Done        |
| M2-4 | Add hardcoding                                                   | #47   | Done        |
| M2-5 | Add folder-size                                                  |       | Next        |
| M2-6 | Add udp                                                          |       | Not started |
| M3-1 | Add the per-target lock                                          |       | Not started |
| M3-2 | Add the run history and its page                                 |       | Not started |
| M4-1 | Add csv-gen                                                      |       | Not started |
| M4-2 | Add json-gen                                                     |       | Not started |
| M4-3 | Add mock-image                                                   |       | Not started |
| M4-4 | Add file-copy                                                    |       | Not started |
| M4-5 | Add folder-copy                                                  |       | Not started |
| M4-6 | Add pdf-split                                                    |       | Not started |
| M4-7 | Add file-merge                                                   |       | Not started |
| M4-8 | Add image-clip                                                   |       | Not started |
| M4-9 | Add cache-clean                                                  |       | Not started |
| M5-1 | Add the subprocess runner                                        |       | Not started |
| M5-2 | Add ping and port                                                |       | Not started |

Each milestone is one stage of `docs/design.ja.md` §8. Each tool row ships its CLI command and its GUI screen together. A row does not start while an earlier row is open.

## Intentionally out of scope

- A CLI command or GUI screen that runs a native script by name or path, and script auto-detection. A script is a tool's implementation ([ADR 0008](adr/0008-cli-safeguards-order.md)).
- Starting in the background at PC login ([ADR 0008](adr/0008-cli-safeguards-order.md)).
- json-format, base64, and uuid. They are not needed ([ADR 0012](adr/0012-no-json-format-base64-uuid.md)).
