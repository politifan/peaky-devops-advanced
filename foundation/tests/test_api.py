import pytest
from fastapi.testclient import TestClient
from app.main import create_app


def test_health_does_not_claim_database_readiness(tmp_path):
    url = "sqlite:///" + str(tmp_path / "unmigrated.sqlite3").replace("\\", "/")
    with TestClient(create_app(url)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503


def test_ready_after_migration(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_create_then_read(client):
    created = client.post("/tickets", json={"title": "  Check the release  "})
    assert created.status_code == 201
    ticket = created.json()
    assert ticket["title"] == "Check the release"
    assert ticket["status"] == "open"
    response = client.get(f"/tickets/{ticket['id']}")
    assert response.status_code == 200
    assert response.json() == ticket


@pytest.mark.parametrize("title", ["", "   ", "x" * 101])
def test_reject_invalid_title(client, title):
    assert client.post("/tickets", json={"title": title}).status_code == 422
    assert client.get("/tickets").json() == []


def test_close_ticket(client):
    ticket = client.post("/tickets", json={"title": "Close this"}).json()
    response = client.patch(f"/tickets/{ticket['id']}", json={"status": "closed"})
    assert response.status_code == 200
    assert response.json()["status"] == "closed"
    assert client.get(f"/tickets/{ticket['id']}").json()["status"] == "closed"


def test_reject_invalid_status(client):
    ticket = client.post("/tickets", json={"title": "Keep open"}).json()
    response = client.patch(f"/tickets/{ticket['id']}", json={"status": "deleted"})
    assert response.status_code == 422
    assert client.get(f"/tickets/{ticket['id']}").json()["status"] == "open"


def test_missing_ticket(client):
    assert client.get("/tickets/99999").status_code == 404
    assert client.patch("/tickets/99999", json={"status": "closed"}).status_code == 404


def test_list_is_ordered_and_paginated(client):
    first = client.post("/tickets", json={"title": "first"}).json()
    second = client.post("/tickets", json={"title": "second"}).json()
    assert client.get("/tickets?limit=1&offset=0").json() == [first]
    assert client.get("/tickets?limit=1&offset=1").json() == [second]
    assert client.get("/tickets?offset=-1").status_code == 422


def test_data_survives_new_application(migrated_url):
    with TestClient(create_app(migrated_url)) as first_client:
        ticket = first_client.post("/tickets", json={"title": "Persist me"}).json()
    with TestClient(create_app(migrated_url)) as second_client:
        assert second_client.get(f"/tickets/{ticket['id']}").json() == ticket


def test_missing_schema_yields_service_error(tmp_path):
    url = "sqlite:///" + str(tmp_path / "missing.sqlite3").replace("\\", "/")
    with TestClient(create_app(url)) as client:
        response = client.get("/tickets")
        assert response.status_code == 503
        assert response.json() == {"detail": "database_unavailable"}
