"""Researcher endpoints: invite participants, monitor progress, export data."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gateway.db import Answer, Participant, get_db
from gateway.security import limit_researcher, new_access_token, require_researcher
from interview.script import voice_order

router = APIRouter(
    prefix="/api/researcher",
    tags=["researcher"],
    dependencies=[Depends(require_researcher), Depends(limit_researcher)],
)

EXPORT_COLUMNS = [
    "participant",
    "native_english",
    "voice_order",
    "answer_id",
    "prompt_id",
    "kind",
    "block",
    "voice",
    "position",
    "modality",
    "duration_seconds",
    "asr_confidence",
    "prompt_text",
    "transcript",
    "created_at",
]


class InviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=1, le=200)


@router.post("/participants", status_code=status.HTTP_201_CREATED)
def invite(body: InviteRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Create participants. Their links contain a secret token and are shown only once."""
    base = request.app.state.settings.public_url
    start = (db.scalar(select(func.max(Participant.sequence))) or 0) + 1
    created = []
    for sequence in range(start, start + body.count):
        token, token_hash = new_access_token()
        participant = Participant(
            pseudonym=f"P{sequence:03d}",
            token_hash=token_hash,
            sequence=sequence,
            voice_order=voice_order(sequence - 1),
            state={},
        )
        db.add(participant)
        # The token goes in the URL fragment, which browsers never send to servers or in Referer headers.
        created.append(
            {
                "participant": participant.pseudonym,
                "voice_order": participant.voice_order,
                "link": f"{base}/#code={token}",
            }
        )
    db.commit()
    return {"participants": created, "note": "Store these links securely; they cannot be shown again."}


@router.get("/participants")
def list_participants(db: Session = Depends(get_db)) -> dict:
    counts = dict(db.execute(select(Answer.participant_id, func.count()).group_by(Answer.participant_id)).all())
    rows = db.scalars(select(Participant).order_by(Participant.sequence)).all()
    return {
        "participants": [
            {
                "participant": p.pseudonym,
                "status": p.status,
                "voice_order": p.voice_order,
                "native_english": p.native_english,
                "answers": counts.get(p.id, 0),
                "consent_at": p.consent_at.isoformat() if p.consent_at else None,
                "completed_at": p.completed_at.isoformat() if p.completed_at else None,
            }
            for p in rows
        ]
    }


@router.delete("/participants/{pseudonym}", status_code=status.HTTP_204_NO_CONTENT)
def delete_participant(pseudonym: str, db: Session = Depends(get_db)) -> None:
    participant = db.scalar(select(Participant).where(Participant.pseudonym == pseudonym))
    if participant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "participant not found")
    db.delete(participant)
    db.commit()


def safe_cell(value: object) -> object:
    """Neutralise spreadsheet formulas (CSV injection) in free-text cells."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@router.get("/export.csv")
def export_csv(db: Session = Depends(get_db)) -> Response:
    # Built inside the request: the database session closes when this handler returns.
    query = (
        select(Answer, Participant)
        .join(Participant, Answer.participant_id == Participant.id)
        .where(Participant.withdrawn_at.is_(None))
        .order_by(Participant.sequence, Answer.id)
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    for answer, participant in db.execute(query):
        writer.writerow(
            [
                safe_cell(v)
                for v in (
                    participant.pseudonym,
                    participant.native_english,
                    " ".join(participant.voice_order),
                    answer.id,
                    answer.prompt_id,
                    answer.kind,
                    answer.block,
                    answer.voice,
                    answer.position,
                    answer.modality,
                    answer.duration_seconds,
                    answer.asr_confidence,
                    answer.prompt_text,
                    answer.transcript,
                    answer.created_at.isoformat(),
                )
            ]
        )
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="responses.csv"'},
    )
