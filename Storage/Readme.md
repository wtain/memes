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

Windows (cmd): dumps all three environments (metal 5432, general 5434, it 5436) to
`backups/ocrdb-<yyyy-MM-dd>-<env>.dump`. The script sets `PGPASSWORD=ocr` unless it is already set in the session.

```commandline
backup.cmd
```

Dump schema-only

```commandline
pg_dump --schema-only --no-owner --no-privileges --no-comments --no-publications --no-subscriptions --file=./alembic/versions/baseline.sql postgresql://ocr:ocr@localhost/ocrdb
```

New environment

(Windows)

```powershell
Get-Content ..\environments\.env.general | foreach {
    $name, $value = $_.split('=')
    set-content env:\$name $value
}
    
alembic upgrade head    
```

```powershell
Get-Content ..\environments\.env.metal | foreach {
    $name, $value = $_.split('=')
    set-content env:\$name $value
}
    
alembic upgrade head
```

```powershell
Get-Content ..\environments\.env.it | foreach {
    $name, $value = $_.split('=')
    set-content env:\$name $value
}
    
alembic upgrade head
```


New database

```commandline
alembic upgrade head
```


