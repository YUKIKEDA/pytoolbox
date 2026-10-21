# pytoolbox: agent entry

Personal toolbox: a Python core library and CLI, and a web GUI planned beside it. Outside issues and pull requests are not accepted. This file is the entry point. Enforced rules are in `.cursor/rules/`. There is no `CONTRIBUTING.md`.

## Source

| What                           | Where                                                            |
| ------------------------------ | ---------------------------------------------------------------- |
| Design (draft, Japanese)       | `docs/design.ja.md`. Not a contract until a design Issue says so |
| Order and status of work       | `docs/roadmap.md`                                                |
| Modules and toolchain          | `docs/conventions.md`                                            |
| Why a decision was made        | `docs/adr/` (`0001-...`, one decision per file)                  |
| Build, test, and gate commands | `justfile`                                                       |

The current row's ID lives only in `docs/roadmap.md`. There is no current row.

## Required rules

Read the rules that match the work. `alwaysApply` rules apply to every change. After every merge comes the retrospective of `workflow`: what the work taught goes into these files, not into an agent's memory.

| Rule                   | One line                                                               |
| ---------------------- | ---------------------------------------------------------------------- |
| `contributions`        | Personal repo; do not accept or invite outside issues or pull requests |
| `workflow`             | Grill, Issue, branch, PR, human merge, retrospective; gates            |
| `consent`              | A question is not a decision; options need a recommendation            |
| `git`                  | Destructive git needs a named order; commit and push only when asked   |
| `conventional-commits` | `type(scope): subject`                                                 |
| `pull-requests`        | Title and six-heading body from the template                           |
| `github-closing`       | A closing verb before `#N` closes it; `Closes #N` alone on a line      |
| `review-comments`      | A finding is about the head it was written on                          |
| `defer`                | Deferral only as a named roadmap row                                   |
| `scope`                | Requirements decide scope; no half-shipped capability                  |
| `layout`               | uv workspace; no empty files ahead of need                             |
| `scratch`              | Remove scratch files in the turn that made them                        |
| `record-corrections`   | A correction lands in the rule, design, ADR, or skill it belongs to    |
| `similar-findings`     | Fix the same kind of problem where it also occurs                      |
| `powershell-shell`     | Shell syntax on this setup                                             |
| `language`             | English code; Japanese spec; bilingual README and roadmap              |
| `python`               | Format, lint, types, and docstrings: `docs/conventions.md`            |
| `open-decisions`       | Elementary conventions are decided in the turn, not handed back         |
| `concept`              | The spec's concept stays in the owner's words                           |

## Skills

Skills live in `.agents/skills/`. `skills-lock.json` pins the ones that come from outside.

- `grilling`: when the design branches (before an Issue).
- `github-closing`: before `gh pr create`, to check that the PR closes only what it finishes.
