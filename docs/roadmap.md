# Roadmap

The current row's ID lives only in this file. Acceptance text lives on the Issue.

Start rows whose prerequisites are done; independent rows and design work may run in parallel. `docs/design.ja.md` §8 names the stages and §9 the initial tools. Their order is this table.

## Current work

M4-3.

| ID   | Title                                                            | Issue | Status      |
| ---- | ---------------------------------------------------------------- | ----- | ----------- |
| M1-1 | Add the foundation: error and result types, tool list, GUI shell | #12   | Done        |
| M2-1 | Add text-count                                                   | #3    | Done        |
| M2-2 | Add regex                                                        | #27   | Done        |
| M2-3 | Add resx-compare                                                 | #35   | Done        |
| M2-4 | Add hardcoding                                                   | #47   | Done        |
| M2-5 | Add folder-size                                                  | #53   | Done        |
| M2-6 | Add udp                                                          | #59   | Done        |
| M3-1 | Add the per-target lock                                          | #64   | Done        |
| M3-2 | Add the run history and its page                                 | #70   | Done        |
| M4-1 | Add csv-gen                                                      | #78   | Done        |
| M4-2 | Add json-gen                                                     | #84   | Done        |
| M4-3 | Add mock-image                                                   | #88   | Done        |
| M4-4 | Add file-copy                                                    | #89   | Done        |
| M4-5 | Add folder-copy                                                  |       | Not started |
| M4-6 | Add pdf-split                                                    |       | Not started |
| M4-7 | Add file-merge                                                   | #95   | Done        |
| M4-8 | Add image-clip                                                   |       | Not started |
| M4-9 | Add cache-clean                                                  | #83   | Done        |
| M5-1 | Add the subprocess runner                                        | #69   | Done        |
| M5-2 | Add ping and port                                                | #77   | Done        |

Each milestone is one stage of `docs/design.ja.md` §8. Each tool row ships its CLI command and its GUI screen together. Rows with no open dependency may run in parallel.

## Intentionally out of scope

- A CLI command or GUI screen that runs a native script by name or path, and script auto-detection. A script is a tool's implementation ([ADR 0008](adr/0008-cli-safeguards-order.md)).
- Starting in the background at PC login ([ADR 0008](adr/0008-cli-safeguards-order.md)).
- json-format, base64, and uuid. They are not needed ([ADR 0012](adr/0012-no-json-format-base64-uuid.md)).
