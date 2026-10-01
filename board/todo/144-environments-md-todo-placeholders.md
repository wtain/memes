# [P2] environments/Environments.md still has TODO placeholders for DB build and migration steps

- **Priority:** P2
- **Area:** Documentation
- **GitHub issue:** https://github.com/wtain/memes/issues/144
- **Source:** docs/2026-08-13-ORR.md

## Problem

`environments/Environments.md` has two literal `TODO: put instructions (check SETUP.md)`
placeholders under "Build and run database" and "Run migrations" — this file is otherwise the
canonical per-environment setup reference (ports, env var list). Flagged in
`docs/audit/audit-2026-06-25.md` and still present verbatim as of this review.

## Why it matters

Small but concrete signal that operational docs are not being kept current — worth closing
along with the other doc gaps found in this review rather than leaving it to linger further.

## Pointers

- `environments/Environments.md`

## Scope

Fill in the two TODO sections (or link them to the real instructions in `SETUP.md` if that's
sufficient).
