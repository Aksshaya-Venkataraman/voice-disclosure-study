"""Participant flow: consent -> interview turns (voice or typed) -> completion.

A participant can only ever see and change their own session: every route
resolves the participant from the secret token in their link.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from gateway.asr import FORMATS, ASRError, base_content_type
from gateway.db import Answer, Participant, get_db, utcnow
from gateway.security import current_participant, limit_answers
from interview.script import VOICES, first_prompt, voice_for

router = APIRouter(prefix="/api/session", tags=["participant"])
log = logging.getLogger(__name__)
MAX_TEXT_CHARS = 5000

# Interviewers are shown neutrally so participants are not told which voice is which.
INTERVIEWER_LABELS = {voice: f"Interviewer {chr(65 + i)}" for i, voice in enumerate(VOICES)}


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agree: Literal[True]
    adult: Literal[True]
    native_english: bool


def _prompt_out(participant: Participant, request: Request) -> dict | None:
    prompt = participant.current_prompt
    if prompt is None:
        return None
    show_text = request.app.state.settings.show_prompt_text
    return {
        **prompt,
        "label": INTERVIEWER_LABELS[prompt["voice"]],
        "display_text": prompt["text"] if show_text else None,
    }


def _session_view(participant: Participant, request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "status": participant.status,
        "prompt": _prompt_out(participant, request),
        "speech_input": settings.asr_provider != "disabled",
        "max_audio_seconds": settings.max_audio_seconds,
        "study": {"title": settings.study_title, "contact_email": settings.contact_email},
    }


@router.get("")
def get_session(request: Request, participant: Participant = Depends(current_participant)) -> dict:
    return _session_view(participant, request)


@router.post("/consent")
def give_consent(
    body: Consent,
    request: Request,
    participant: Participant = Depends(current_participant),
    db: Session = Depends(get_db),
) -> dict:
    if participant.consent_at is None:
        prompt = first_prompt().to_dict()
        participant.consent_at = utcnow()
        participant.native_english = body.native_english
        participant.state = {"stage": "mood", "block": 0, "question": 0}
        participant.current_prompt = {**prompt, "voice": voice_for(prompt, participant.voice_order)}
        db.commit()
    return _session_view(participant, request)


@router.post("/answers", dependencies=[Depends(limit_answers)])
def answer(
    request: Request,
    prompt_id: Annotated[str, Form(max_length=40)],
    text: Annotated[str | None, Form(max_length=MAX_TEXT_CHARS)] = None,
    duration_seconds: Annotated[float | None, Form(ge=0, le=600)] = None,
    audio: Annotated[UploadFile | None, File()] = None,
    participant: Participant = Depends(current_participant),
    db: Session = Depends(get_db),
) -> dict:
    settings = request.app.state.settings
    if participant.consent_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "please give consent first")
    if participant.completed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "the interview is already complete")
    current = participant.current_prompt or {}
    if prompt_id != current.get("id"):
        # Stops double submissions and answers being attached to the wrong question.
        raise HTTPException(status.HTTP_409_CONFLICT, "this question has already been answered")
    if (text is None) == (audio is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "send either a text answer or an audio recording")

    confidence = None
    if audio is not None:
        if settings.asr_provider == "disabled":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "voice answers are not enabled; please type")
        content_type = base_content_type(audio.content_type)
        if content_type not in FORMATS:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "unsupported audio format")
        if duration_seconds is not None and duration_seconds > settings.max_audio_seconds:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "recording is too long")
        data = audio.file.read(settings.max_audio_bytes + 1)
        if len(data) > settings.max_audio_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "recording is too large")
        try:
            result = request.app.state.asr.transcribe(data, content_type)
        except ASRError:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "speech recognition is unavailable; please try again or type your answer",
            ) from None
        finally:
            del data  # audio is never stored
        transcript, confidence, modality = result.text.strip(), result.confidence, "voice"
    else:
        transcript, modality = (text or "").strip(), "text"

    if not transcript:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "we could not hear an answer, please try again")

    db.add(
        Answer(
            participant_id=participant.id,
            prompt_id=current["id"],
            kind=current["kind"],
            block=current["block"],
            voice=current["voice"],
            position=current["position"],
            prompt_text=current["text"],
            transcript=transcript,
            modality=modality,
            duration_seconds=duration_seconds if modality == "voice" else None,
            asr_confidence=confidence,
        )
    )

    turn = request.app.state.engine.step(participant.pseudonym, participant.state, transcript)
    participant.state = turn.state
    participant.current_prompt = {**turn.prompt, "voice": voice_for(turn.prompt, participant.voice_order)}
    if turn.prompt["kind"] == "end":
        participant.completed_at = utcnow()
    db.commit()
    return {"transcript": transcript, **_session_view(participant, request)}


@router.post("/withdraw")
def withdraw(participant: Participant = Depends(current_participant), db: Session = Depends(get_db)) -> dict:
    """Right to withdraw: delete every answer immediately and close the session."""
    deleted = len(participant.answers)
    participant.answers.clear()
    participant.withdrawn_at = utcnow()
    participant.state, participant.current_prompt, participant.native_english = {}, None, None
    db.commit()
    log.info("participant withdrew; %d answers deleted", deleted)
    return {"status": "withdrawn", "answers_deleted": deleted}
