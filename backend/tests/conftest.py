from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.database import Database
from app.main import create_app


class Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 24, 12, tzinfo=UTC).timestamp()

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def database(settings):
    database = Database(settings.database_url)
    yield database
    database.dispose()


@pytest.fixture
def app(database, settings, clock):
    return create_app(database=database, settings=settings, clock=clock)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def rows(database, model):
    with database.sessions() as session:
        return [row.as_dict() for row in session.scalars(select(model))]


def record(database, model, key):
    with database.sessions() as session:
        row = session.get(model, key)
        return row.as_dict() if row is not None else None


def login(client, email="maya@example.com", password="DemoPass123!"):
    response = client.post("/v1/auth/google", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.fixture
def headers(client):
    return login(client)


SOURCE = {
    "fileName": "episode.mp4",
    "mimeType": "video/mp4",
    "sizeBytes": 1_000_000,
    "durationSec": 300,
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "hasVideo": True,
    "hasAudio": True,
}


def uploaded(client, headers, **overrides):
    response = client.post("/v1/uploads", headers=headers, json={**SOURCE, **overrides})
    assert response.status_code == 200, response.text
    upload = response.json()
    response = client.post(f"/v1/uploads/{upload['uploadId']}/complete", headers=headers)
    assert response.status_code == 200, response.text
    return upload["projectId"]


def ready(client, headers, clock, **overrides):
    project_id = uploaded(client, headers, **overrides)
    response = client.post(
        f"/v1/projects/{project_id}/process", headers=headers, json={"goal": "educational"}
    )
    assert response.status_code == 200, response.text
    clock.advance(8)
    response = client.get(f"/v1/projects/{project_id}/status", headers=headers)
    assert response.json()["status"] == "ready", response.text
    return project_id


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text
