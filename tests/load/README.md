Synthetic load check for the P4 retrieval stage: fills the test database with synthetic images, profiles and situation
vectors, measures candidate SQL, scoring, fusion, collapse and facets over random queries, and removes its rows.
It needs `DATABASE_URL` pointing at `ocrdb_test` only (it refuses anything else) and `PYTHONPATH=.` from the repo root.
It is not collected by pytest. Run it from the repo root:

    DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" PYTHONPATH=. python tests/load/synthetic_load_check.py --images 25000 --queries 50
