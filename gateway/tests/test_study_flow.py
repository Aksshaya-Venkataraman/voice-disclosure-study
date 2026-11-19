import csv
import io
from collections import Counter

import pytest
from conftest import RESEARCHER, answer_text, consent, invite

from interview.script import BLOCKS, VOICES


def run_interview(client, headers, text="I enjoy solving hard problems with other people"):
    session = client.get("/api/session", headers=headers).json()
    turns = 0
    while session["status"] != "completed":
        response = answer_text(client, headers, session["prompt"]["id"], text)
        assert response.status_code == 200, response.text
        session = response.json()
        turns += 1
    return turns


def test_researcher_endpoints_need_the_token(client):
    assert client.post("/api/researcher/participants", json={"count": 1}).status_code == 401
    assert (
        client.get("/api/researcher/participants", headers={"Authorization": "Bearer wrong-token"}).status_code == 401
    )
    assert client.get("/api/researcher/export.csv").status_code == 401


def test_invites_follow_a_balanced_latin_square(client):
    people = invite(client, 4)
    orders = [p["voice_order"] for p in people]
    assert [p["participant"] for p in people] == ["P001", "P002", "P003", "P004"]
    for position in range(4):  # every voice appears once in every block position
        assert sorted(order[position] for order in orders) == sorted(VOICES)
    successions = Counter((o[i], o[i + 1]) for o in orders for i in range(3))
    assert set(successions.values()) == {1}  # each voice follows each other voice exactly once
    assert all(p["link"].startswith("https://study.example.org/#code=") for p in people)


def test_session_requires_a_valid_link(client):
    assert client.get("/api/session").status_code == 401
    assert client.get("/api/session", headers={"Authorization": "Bearer guessed-code-123456789"}).status_code == 401


def test_consent_is_required_and_validated(client):
    person = invite(client)[0]
    assert client.get("/api/session", headers=person["headers"]).json()["status"] == "invited"
    assert answer_text(client, person["headers"], "warmup_greeting", "hi").status_code == 409
    bad = client.post(
        "/api/session/consent", json={"agree": False, "adult": True, "native_english": True}, headers=person["headers"]
    )
    assert bad.status_code == 422
    session = consent(client, person["headers"])
    assert session["status"] == "in_progress"
    assert session["prompt"]["id"] == "warmup_greeting"
    assert session["prompt"]["label"].startswith("Interviewer ")
    assert session["prompt"]["display_text"] is None  # question text hidden by default


def test_full_interview_records_voice_per_block(client):
    person = invite(client, 2)[1]  # second participant: a different voice order
    consent(client, person["headers"], native=False)
    assert run_interview(client, person["headers"]) == 3 + 16

    rows = list(csv.DictReader(io.StringIO(client.get("/api/researcher/export.csv", headers=RESEARCHER).text)))
    questions = [r for r in rows if r["kind"] == "question"]
    assert len(questions) == 16 and len(rows) == 19
    expected = dict(zip([b.id for b in BLOCKS], person["voice_order"], strict=True))
    assert all(r["voice"] == expected[r["block"]] for r in questions)
    assert [int(r["position"]) for r in questions] == list(range(1, 17))
    assert {r["native_english"] for r in rows} == {"False"}

    finished = client.get("/api/session", headers=person["headers"]).json()
    assert finished["status"] == "completed"
    late = answer_text(client, person["headers"], "end", "one more thing")
    assert late.status_code == 409


def test_voice_answers_are_transcribed_not_stored(client, asr):
    person = invite(client)[0]
    session = consent(client, person["headers"])
    response = client.post(
        "/api/session/answers",
        data={"prompt_id": session["prompt"]["id"], "duration_seconds": "4.2"},
        files={"audio": ("answer.webm", b"\x1aE\xdf\xa3fake-webm-bytes", "audio/webm;codecs=opus")},
        headers=person["headers"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["transcript"] == asr.next_text
    assert asr.calls == [(len(b"\x1aE\xdf\xa3fake-webm-bytes"), "audio/webm")]
    row = next(csv.DictReader(io.StringIO(client.get("/api/researcher/export.csv", headers=RESEARCHER).text)))
    assert row["modality"] == "voice" and row["asr_confidence"] == "0.91" and row["duration_seconds"] == "4.2"


@pytest.mark.parametrize(
    ("setup", "upload", "status"),
    [
        (lambda a: None, ("a.mp4", b"x" * 100, "video/mp4"), 415),
        (lambda a: setattr(a, "fail", True), ("a.webm", b"x" * 100, "audio/webm"), 503),
        (lambda a: setattr(a, "next_text", "   "), ("a.webm", b"x" * 100, "audio/webm"), 422),
    ],
    ids=["unsupported-format", "asr-down", "silence"],
)
def test_failed_recordings_do_not_advance_the_interview(client, asr, setup, upload, status):
    person = invite(client)[0]
    prompt_id = consent(client, person["headers"])["prompt"]["id"]
    setup(asr)
    response = client.post(
        "/api/session/answers", data={"prompt_id": prompt_id}, files={"audio": upload}, headers=person["headers"]
    )
    assert response.status_code == status
    assert client.get("/api/session", headers=person["headers"]).json()["prompt"]["id"] == prompt_id


def test_recording_size_and_duration_limits(make_client):
    client = make_client(max_audio_bytes=20_000, max_audio_seconds=30)
    person = invite(client)[0]
    prompt_id = consent(client, person["headers"])["prompt"]["id"]
    big = client.post(
        "/api/session/answers",
        data={"prompt_id": prompt_id},
        files={"audio": ("a.webm", b"x" * 30_000, "audio/webm")},
        headers=person["headers"],
    )
    assert big.status_code == 413
    long = client.post(
        "/api/session/answers",
        data={"prompt_id": prompt_id, "duration_seconds": "31"},
        files={"audio": ("a.webm", b"x" * 100, "audio/webm")},
        headers=person["headers"],
    )
    assert long.status_code == 413


def test_answer_needs_exactly_one_of_text_or_audio(client):
    person = invite(client)[0]
    prompt_id = consent(client, person["headers"])["prompt"]["id"]
    neither = client.post("/api/session/answers", data={"prompt_id": prompt_id}, headers=person["headers"])
    both = client.post(
        "/api/session/answers",
        data={"prompt_id": prompt_id, "text": "hi"},
        files={"audio": ("a.webm", b"x", "audio/webm")},
        headers=person["headers"],
    )
    assert neither.status_code == both.status_code == 422


def test_duplicate_submission_is_rejected(client):
    person = invite(client)[0]
    prompt_id = consent(client, person["headers"])["prompt"]["id"]
    assert answer_text(client, person["headers"], prompt_id, "great thanks").status_code == 200
    assert answer_text(client, person["headers"], prompt_id, "great thanks").status_code == 409


def test_participants_only_reach_their_own_session(client):
    alice, bob = invite(client, 2)
    consent(client, alice["headers"])
    run_interview(client, alice["headers"])
    bob_view = client.get("/api/session", headers=bob["headers"]).json()
    assert bob_view["status"] == "invited"
    listing = client.get("/api/researcher/participants", headers=RESEARCHER).json()["participants"]
    assert {p["participant"]: p["answers"] for p in listing} == {"P001": 19, "P002": 0}


def test_withdrawal_deletes_everything(client):
    person = invite(client)[0]
    session = consent(client, person["headers"])
    answer_text(client, person["headers"], session["prompt"]["id"], "fine thanks")
    response = client.post("/api/session/withdraw", headers=person["headers"])
    assert response.json() == {"status": "withdrawn", "answers_deleted": 1}
    assert client.get("/api/session", headers=person["headers"]).status_code == 410
    export = client.get("/api/researcher/export.csv", headers=RESEARCHER).text
    assert export.strip().count("\n") == 0  # header only
    status = client.get("/api/researcher/participants", headers=RESEARCHER).json()["participants"][0]["status"]
    assert status == "withdrawn"


def test_export_neutralises_spreadsheet_formulas(client):
    person = invite(client)[0]
    session = consent(client, person["headers"])
    answer_text(client, person["headers"], session["prompt"]["id"], '=HYPERLINK("http://evil","click")')
    row = next(csv.DictReader(io.StringIO(client.get("/api/researcher/export.csv", headers=RESEARCHER).text)))
    assert row["transcript"].startswith("'=")


def test_researcher_can_delete_a_participant(client):
    invite(client, 1)
    assert client.delete("/api/researcher/participants/P001", headers=RESEARCHER).status_code == 204
    assert client.delete("/api/researcher/participants/P001", headers=RESEARCHER).status_code == 404


def test_answer_rate_limit(make_client):
    client = make_client(answer_rate_limit="2/minute")
    person = invite(client)[0]
    session = consent(client, person["headers"])
    codes = []
    for text in ["good", "no", "yes"]:
        response = answer_text(client, person["headers"], session["prompt"]["id"], text)
        codes.append(response.status_code)
        if response.status_code == 200:
            session = response.json()
    assert codes == [200, 200, 429]
