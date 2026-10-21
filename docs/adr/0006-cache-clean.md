# 0006. Cache cleanup takes explicit targets

- Status: Accepted
- Date: 2026-10-21
- Issue: #6

## Context

`docs/adr/0002-inventory-ui-license.md` left cache cleanup out of the inventory, with the other PC-maintenance tasks from the first draft. The owner wants it because deleting regenerable caches frees storage. A default that selects every known cache would delete targets the caller did not name.

## Decision

- `cache-clean` is in the inventory. Its roadmap row is M1-16, after the other CLI tools and before the GUI.
- The caller passes one or more names: `temp`, `pip`, `uv`, `npm`, `nuget`, `cargo`. Unnamed targets are not scanned. No name, or an unknown name, is `InputError`.
- The default reports the paths and the byte counts. `--apply` deletes. `--dry-run` matches the default.
- `temp` keeps files modified less than 24 hours ago. The package caches delete their contents. The cache directory itself stays. Links are not followed. A missing root is success.
- The pnpm store, browser caches, editor caches, `C:\Windows\Temp`, Windows Update, and a disk-wide `__pycache__` walk stay out of the tool.

## Consequences

The GUI row includes this command and calls the same contract. Implementation waits until M1-16 is the current row. Current work stays M1-1. The paths are in `docs/design.ja.md` §9.2.
