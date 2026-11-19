import os
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from conftest import RESEARCHER_TOKEN, answer_text, consent, invite, make_settings
from google.api_core import exceptions as gexc
from sqlalchemy import create_engine

from gateway.asr import ASRError, GoogleSpeechToText
from gateway.db import Base
from gateway.engine import RasaEngine
from interview.script import LATIN_SQUARE, State, advance, voice_order

GATEWAY = Path(__file__).resolve().parents[1]


# ------------------------------------------------------- interview script
def test_latin_square_is_balanced():
    for column in range(4):
        assert sorted(row[column] for row in LATIN_SQUARE) == [0, 1, 2, 3]
    assert voice_order(0) == voice_order(4)


def test_script_uses_nlu_intent_when_available():
    sad = advance(State(), "mood_unhappy", "I'm great")  # intent wins over keywords
    assert sad.prompt.text.startswith("Sorry")
    nervous = advance(State("nervous"), "deny", "yes")
    assert nervous.prompt.text.startswith("Fantastic")


# -------------------------------------------------------------------- Rasa
def rasa_reply(state, prompt):
    return [
        {"recipient_id": "P001", "text": prompt["text"]},
        {"recipient_id": "P001", "custom": {"prompt": prompt, "state": state, "intent": "mood_great"}},
    ]


def test_rasa_engine_sends_state_and_reads_custom_payload():
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        prompt = {
            "id": "warmup_nervous",
            "text": "Glad to hear that.",
            "kind": "warmup",
            "block": None,
            "position": 0,
            "total": 16,
        }
        return httpx2.Response(200, json=rasa_reply({"stage": "nervous", "block": 0, "question": 0}, prompt))

    engine = RasaEngine("http://rasa", 2, transport=httpx2.MockTransport(handler))
    turn = engine.step("P001", {"stage": "mood", "block": 0, "question": 0}, "great")
    assert turn.engine == "rasa" and turn.prompt["id"] == "warmup_nervous"
    body = seen[0].read()
    assert b'"metadata":{"state":{"stage":"mood"' in body.replace(b" ", b"")


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(503),
        httpx2.Response(200, json=[{"text": "no custom payload"}]),
        httpx2.Response(200, json=[{"custom": {"prompt": {"id": 1}, "state": {}}}]),
    ],
    ids=["server-error", "missing-custom", "malformed"],
)
def test_rasa_failures_fall_back_to_the_script(response):
    engine = RasaEngine("http://rasa", 2, transport=httpx2.MockTransport(lambda r: response))
    turn = engine.step("P001", {"stage": "begin", "block": 0, "question": 0}, "ok")
    assert turn.engine == "scripted"
    assert turn.prompt["id"] == "tech_project"


def test_gateway_with_unreachable_rasa_still_runs(make_client):
    def down(request):
        raise httpx2.ConnectError("refused")

    client = make_client(engine=RasaEngine("http://rasa", 1, transport=httpx2.MockTransport(down)))
    person = invite(client)[0]
    prompt = consent(client, person["headers"])["prompt"]
    assert answer_text(client, person["headers"], prompt["id"], "great").json()["prompt"]["id"] == "warmup_nervous"


# --------------------------------------------------------------------- ASR
class FakeSpeechClient:
    def __init__(self, too_long=False):
        self.too_long = too_long
        self.configs = []

    def _response(self):
        alt = lambda t, c: SimpleNamespace(transcript=t, confidence=c)  # noqa: E731
        return SimpleNamespace(
            results=[
                SimpleNamespace(alternatives=[alt("I worked in a bank ", 0.9)]),
                SimpleNamespace(alternatives=[alt("for two years.", 0.8)]),
            ]
        )

    def recognize(self, config, audio, timeout):
        self.configs.append(config)
        if self.too_long:
            raise gexc.InvalidArgument("Sync input too long. For audio longer than 1 min use LongRunningRecognize")
        return self._response()

    def long_running_recognize(self, config, audio):
        return SimpleNamespace(result=lambda timeout: self._response())


def test_google_asr_builds_config_and_joins_results():
    fake = FakeSpeechClient()
    result = GoogleSpeechToText("en-GB", client=fake).transcribe(b"audio", "audio/webm")
    assert result.text == "I worked in a bank for two years."
    assert result.confidence == 0.85
    config = fake.configs[0]
    assert config.language_code == "en-GB" and config.sample_rate_hertz == 48000
    assert config.encoding.name == "WEBM_OPUS"


def test_google_asr_long_audio_and_errors():
    assert GoogleSpeechToText("en-GB", client=FakeSpeechClient(too_long=True)).transcribe(b"a", "audio/ogg").text
    with pytest.raises(ASRError):
        GoogleSpeechToText("en-GB", client=FakeSpeechClient()).transcribe(b"a", "audio/flac")


# ---------------------------------------------------------- web & security
def test_web_app_and_security_headers(client):
    page = client.get("/")
    assert page.status_code == 200 and '<script type="module" src="/app.js">' in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert "microphone=(self)" in page.headers["permissions-policy"]
    api = client.get("/api/health")
    assert api.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert api.headers["cache-control"] == "no-store"
    assert api.headers["x-frame-options"] == "DENY"
    assert client.get("/docs").status_code == 404
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400


def test_static_files_do_not_use_inline_script_or_innerhtml():
    static = GATEWAY / "static"
    assert "<script>" not in (static / "index.html").read_text()
    for name in ("app.js", "voices.js"):
        source = (static / name).read_text()
        assert "innerHTML" not in source and "eval(" not in source


def test_ready_reports_engine(client):
    assert client.get("/api/ready").json() == {"status": "ready", "engine": "scripted", "speech_input": True}


@pytest.mark.parametrize("token", ["short", "change-me-" + "x" * 40])
def test_weak_researcher_token_refused(token):
    with pytest.raises(ValueError):
        make_settings(researcher_token=token)


def test_settings_accept_strong_token():
    assert make_settings().researcher_token.get_secret_value() == RESEARCHER_TOKEN


def test_migrations_match_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(GATEWAY / "alembic.ini"))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    engine.dispose()
    command.downgrade(cfg, "base")
    assert os.path.exists(tmp_path / "m.db")
