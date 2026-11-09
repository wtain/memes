# B2 plan: ingestion pickup verification

Spec: docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-b2-ingestion-pickup-verification-design.md

1. Read B1 service/router and Stage 1/1.5 code; pick the entry points (`run()` of both stages, ASGI client). Done.
2. Write `tests/integration/test_upload_to_ingestion.py` (pickup, directories, duplicates, popularity, atomicity, names, Stage 1.5). Done.
3. Check each test can fail (mutation of the publish step for the atomicity test; contrast test for popularity). Done.
4. Run the new file, `-k ingest`, then the whole `tests/integration` root, each alone; `pytest tests/docs/`. Done.
5. Docs: "Phone and web uploads" in `docs/batches/use-cases/ingestion.md`; `ingest_hash_dedup` entry in CLAUDE.md. Done.
6. Commit only own paths.
