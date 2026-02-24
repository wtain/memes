build:
    docker build -t ocr-postgres .

run: build
    docker run -d -p 5432:5432 --name ocr-db ocr-postgres