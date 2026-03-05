### Create baseline migration

See database credentials in Dockerfile

```commandline
pg_dump --schema-only --no-owner --no-privileges --format=plain --file=migrations/baseline.sql postgresql://ocr:ocr@localhost:5432/ocrdb
```

Create migration

```commandline
alembic revision --autogenerate -m "add something"
```

Apply migrations

```commandline
alembic upgrade head
```

Create volume and copy from existing
```commandline
docker volume create --name ocrdbvol --opt type=none --opt device=./data --opt o=bind

// docker run --rm -v 92f459889b00901e81b74192ef994dc64a547e833ba9f8a3397b3fec217b7560:/ -v ocrdbvol:/ alpine sh -c "cd / && cp -a . /"
```

Dump

Requires password

```commandline
pg_dump -h localhost -p 5432 -U ocr -Fc -f ./backups/ocrdb-2026-03-02.dump ocrdb
```

Dump schema-only

```commandline
pg_dump --schema-only --no-owner --no-privileges --no-comments --no-publications --no-subscriptions --file=./alembic/versions/baseline.sql postgresql://ocr:ocr@localhost/ocrdb
```

New database

```commandline
alembic upgrade head
```