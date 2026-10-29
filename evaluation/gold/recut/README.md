# Recut labelled sample

Owner judgments of proposed cuts, used by the R2 spike and later by R7. Image ids are database-local (`general`).

- `spike-round1-judgments.json`: sheet item -> grade. Cuts: 2 use as is, 1 slightly off, 0 wrong, s skip. No-proposal items: y needs a cut, n fine as is.
- `spike-round2-judgments.json` / `spike-round2-key.json`: round 2, detector A only, below confidence 1.0 (key adds band and confidence); grades 2/1/0/s.
- `spike-round1-key.json`: sheet item -> image id, box in source pixels, and the approaches that proposed it (A classical, B vision-model box, C = A gated by the vision model).

Findings: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-24-r2-spike-findings.md

Precision report: `python -m evaluation recut-report` (no database needed; see docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-29-r7-operations-and-evaluation-design.md).
