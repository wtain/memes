### Create baseline migration

See database credentials in Dockerfile

```commandline
pg_dump --schema-only --no-owner --no-privileges --format=plain --file=migrations/baseline.sql postgresql://ocr:ocr@localhost:5432/ocrdb
```