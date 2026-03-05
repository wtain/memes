build:
    docker build -t ocr-postgres .

create-volume:
    docker volume create memes_pgdata

run: build
    docker run -d -p 5432:5432 --name ocr-db -v memes_pgdata:/var/lib/postgresql/data ocr-postgres

run-compose:
    docker compose up -d