Synthetic load check for the P4 retrieval stage: fills the test database with synthetic images, profiles and situation
vectors, measures candidate SQL, scoring, fusion, collapse and facets over random queries, and removes its rows.
It is not collected by pytest (the file is not named `test_*.py`).

Safety: it refuses to run unless `DATABASE_URL` names the database exactly `ocrdb_test`. It never drops the schema; it
only creates tables that are missing (the test database has none outside a pytest run). Do not run it concurrently with
the integration tests. Rows are tagged with the filename prefix `synthetic-load-` and removed at the end, also on failure
(`--keep` leaves them in place; the next run removes leftovers first).

Run from the repo root (`PYTHONPATH=.` is required):

    DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" PYTHONPATH=. python tests/load/synthetic_load_check.py --images 25000 --queries 50 [--keep]

PowerShell:

    $env:DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"; $env:PYTHONPATH='.'; python tests/load/synthetic_load_check.py --images 25000 --queries 50

Method: after inserting, the tables are ANALYZEd, three untimed warm-up queries run, then each timed query uses an axis
profile of two random values per axis and a situation vector made of 0.6 x the vector of a random inserted image plus
0.8 x random noise (normalised), so some memes clear the situation floor as in real use. About 20% of the images sit in
duplicate clusters of exactly ten. It prints p50, p95 and the median candidate count, and exits 0 for PASS (p95 under
300 ms) and 1 for FAIL. The margin is thin and machine dependent: treat the verdict as a data point, not a guarantee.
