# [P1] No secrets management process for VPS (.env.example, rotation, no-dev-defaults-in-prod)

- **Priority:** P1
- **Area:** Security
- **GitHub issue:** https://github.com/wtain/memes/issues/140
- **Source:** docs/2026-08-13-ORR.md

## Problem

Secrets live in gitignored `.env.<environment>` files with no committed template. There is no
`.env.example`, no documented credential rotation policy, and the default dev DB credentials
(`ocr:ocr`, per `Storage/docker-compose.yaml` and `SETUP.md`) need explicit confirmation they
can never reach a production instance.

## Why it matters

Onboarding friction today; real risk once a production `.env` needs provisioning on a VPS — no
documented, repeatable, safe process exists.

## Pointers

- `CLAUDE.md`'s Configuration section
- `environments/Environments.md` (env var list, no template file)
- `docs/audit/audit-2026-06-25.md` (flagged `ocr:ocr` default creds as needing rotation for
  prod, still open)

## Scope

Add a `.env.example` template, document how prod secrets get provisioned/rotated on the VPS.
Design/requirements TBD.
