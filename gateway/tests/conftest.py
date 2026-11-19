from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from gateway.asr import ASRError, Transcript
from gateway.config import Settings
from gateway.db import Base, make_engine
from gateway.engine import ScriptedEngine
from gateway.main import create_app

RESEARCHER_TOKEN = secrets.token_urlsafe(40)


class FakeASR:
    """Stands in for Google Speech-to-Text; returns a scripted transcript."""

    def __init__(self) -> None:
        self.next_text = "I built a small web app and learned a lot from it"
        self.fail = False
        self.calls: list[tuple[int, str]] = []

    def transcribe(self, audio: bytes, content_type: str) -> Transcript:
        self.calls.append((len(audio), content_type))
        if self.fail:
            raise ASRError("down")
        return Transcript(self.next_text, 0.91)


def make_settings(**overrides) -> Settings:
    values = {
        "environment": "test",
        "database_url": "sqlite://",
        "researcher_token": RESEARCHER_TOKEN,
        "public_url": "https://study.example.org",
        "allowed_hosts": ["testserver"],
        "interview_engine": "scripted",
        "asr_provider": "google",
        "answer_rate_limit": "1000/minute",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def asr() -> FakeASR:
    return FakeASR()


@pytest.fixture
def make_client(asr) -> Iterator[Callable[..., TestClient]]:
    clients = []

    def factory(engine=None, **overrides) -> TestClient:
        db_engine = make_engine("sqlite://")
        Base.metadata.create_all(db_engine)
        app = create_app(make_settings(**overrides), db_engine=db_engine, engine=engine or ScriptedEngine(), asr=asr)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


RESEARCHER = {"Authorization": f"Bearer {RESEARCHER_TOKEN}"}


def invite(client: TestClient, count: int = 1) -> list[dict]:
    response = client.post("/api/researcher/participants", json={"count": count}, headers=RESEARCHER)
    assert response.status_code == 201, response.text
    people = response.json()["participants"]
    for person in people:
        person["headers"] = {"Authorization": f"Bearer {person['link'].split('#code=')[1]}"}
    return people


def consent(client: TestClient, headers: dict, native: bool = True) -> dict:
    response = client.post(
        "/api/session/consent", json={"agree": True, "adult": True, "native_english": native}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def answer_text(client: TestClient, headers: dict, prompt_id: str, text: str):
    return client.post("/api/session/answers", data={"prompt_id": prompt_id, "text": text}, headers=headers)
