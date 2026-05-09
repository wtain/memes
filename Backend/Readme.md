Run server
```commandline
uvicorn app.main:app --reload --env-file app/.env --port 8081 
```

```commandline
uvicorn app.main:app --reload --reload-delay 1 --env-file app/.env --port 8081 --reload-dir app
```

```commandline
uvicorn app.main:app --reload --env-file app/.env --port 8081 --use-colors --loop asyncio
```

```commandline
uvicorn app.main:app --reload --reload-dir app --env-file app/.env --port 8081
```

```commandline
uvicorn app.main:app --reload --reload-dir app --reload-exclude "*.pyc" --env-file app/.env --port 8081
```

```commandline
python -m uvicorn app.main:app --reload --reload-dir app --env-file app/.env --port 8081

python -m uvicorn app.main:app --reload --reload-dir app --env-file ../Storage/.env.metal --port 8081
python -m uvicorn app.main:app --reload --reload-dir app --env-file ../Storage/.env.general --port 8082
```