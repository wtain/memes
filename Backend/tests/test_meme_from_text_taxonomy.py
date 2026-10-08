import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.meme_from_text import router
from Backend.app.services.meme_from_text_taxonomy import taxonomy_payload
from taxonomy import load

LANGS = ("en", "ru", "es")


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_the_endpoint_serves_the_loaded_taxonomy(client):
    tax = load()

    response = client.get("/api/meme-from-text/taxonomy")

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == tax.version
    assert [axis["name"] for axis in body["axes"]] == list(tax.axes)
    by_name = {axis["name"]: axis for axis in body["axes"]}
    assert by_name["situation"]["type"] == "free_text" and by_name["situation"]["maxChars"] == 160
    assert "values" not in by_name["situation"] and "maxValues" not in by_name["situation"]
    assert by_name["emotion"]["maxValues"] == 3 and by_name["stance"]["maxValues"] == 2
    assert by_name["topic"]["maxValues"] == 3 and by_name["tone"]["maxValues"] == 2


def test_every_vocabulary_value_has_a_definition_and_all_three_labels(client):
    body = client.get("/api/meme-from-text/taxonomy").json()
    tax = load()
    for axis in body["axes"]:
        if axis["type"] != "vocabulary":
            continue
        assert [v["key"] for v in axis["values"]] == list(tax.values[axis["name"]])
        for value in axis["values"]:
            assert value["definition"].strip()
            assert set(value["labels"]) == set(LANGS)
            assert all(value["labels"][lang].strip() for lang in LANGS)


def test_the_response_can_be_cached_briefly(client):
    response = client.get("/api/meme-from-text/taxonomy")
    assert "max-age" in response.headers["cache-control"]


def test_payload_builder_is_pure_and_matches_the_endpoint(client):
    tax = load()
    assert taxonomy_payload(tax)["version"] == client.get("/api/meme-from-text/taxonomy").json()["version"]
    assert taxonomy_payload(tax) == taxonomy_payload(tax)
