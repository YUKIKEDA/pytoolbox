# 0007. The concept text stays the owner's

- Status: Accepted
- Date: 2026-10-21
- Issue: #9

## Context

The owner wrote the concept: PC operations and DevToys-compatible developer utilities in one toolbox. A later edit replaced that paragraph with a shorter summary, and listed the concept's tools as out of the product. The owner rejected both the summary and the status line above it.

## Decision

`docs/design.ja.md` §1 is the owner's concept, in the owner's words. Developer Tools and System Tools named there are in the product. The six existing tools and `cache-clean` are part of that concept, not a replacement for it. Their roadmap rows stay M1 and M2. The other named tools are M3.

## Consequences

Do not rewrite §1 to describe a subset of the tools. Current work stays M1-1 until that row is done.
