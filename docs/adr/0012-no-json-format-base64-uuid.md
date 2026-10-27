# 0012. No JSON format, Base64, or UUID tools

- Status: Accepted
- Date: 2026-10-27
- Issue: #39

## Context

`docs/design.ja.md` §8 had a stage 6, "DevTools 系", with JSON format, Base64, and UUID generation, and the roadmap had M6-1 to M6-3 for them. [ADR 0008](0008-cli-safeguards-order.md) put them last in the build order. §2, §3, §4.1, §5.2, and §6 used them as examples.

## Decision

- The three tools are not built. The owner's reason: they are not needed.
- §8 drops stage 6, and the roadmap drops M6-1 to M6-3 and lists the three under Intentionally out of scope. This replaces the last item of the order in ADR 0008; ADR 0008 is not edited.
- §5.1 drops Base64, JSON, and UUID from the sidebar tree. URL, JWT, YAML, Hash, Password, and the other items stay; they are outside this decision.
- The examples in §2, §3, §4.1, §5.2, and §6 use built or remaining tools (`text-count`, `regex`, YAML, Hash), so no example reads as a plan to build the three.
- §1.1, the owner's concept, stays as written. Text count, regex, resource compare, and the other developer tools keep it true.

## Consequences

A later wish for one of the three is a new Grill and Issue, and it removes the out-of-scope entry.
