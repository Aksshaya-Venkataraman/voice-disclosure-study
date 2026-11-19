"""Participant links, researcher token, security headers and request limits."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import sys
import time
import uuid
from contextvars import ContextVar

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from limits import parse
from limits.storage import storage_from_string
from limits.strategies import MovingWindowRateLimiter
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gateway.db import Participant, get_db

_bearer = HTTPBearer(auto_error=False)
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


# ------------------------------------------------------------------ tokens
def new_access_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)  # 256 bits: cannot be guessed or enumerated
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_participant(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)
) -> Participant:
    if credentials is None or not credentials.credentials or len(credentials.credentials) > 128:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing participant link")
    participant = db.scalar(select(Participant).where(Participant.token_hash == hash_token(credentials.credentials)))
    if participant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing participant link")
    if participant.withdrawn_at is not None:
        raise HTTPException(status.HTTP_410_GONE, "you have withdrawn from this study")
    return participant


def require_researcher(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    expected = request.app.state.settings.researcher_token.get_secret_value()
    supplied = credentials.credentials if credentials else ""
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "researcher token required", headers={"WWW-Authenticate": "Bearer"}
        )


# ------------------------------------------------------------- rate limits
class RateLimiter:
    def __init__(self, storage_uri: str) -> None:
        self._limiter = MovingWindowRateLimiter(storage_from_string(storage_uri))

    def hit(self, limit: str, scope: str, key: str) -> None:
        item = parse(limit)
        if not self._limiter.hit(item, scope, key):
            reset_at, _ = self._limiter.get_window_stats(item, scope, key)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many requests",
                headers={"Retry-After": str(max(1, int(reset_at - time.time())))},
            )

    def reset(self) -> None:
        self._limiter.storage.reset()


def limit_answers(request: Request, participant: Participant = Depends(current_participant)) -> None:
    request.app.state.rate_limiter.hit(request.app.state.settings.answer_rate_limit, "answers", f"p:{participant.id}")


def limit_researcher(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    request.app.state.rate_limiter.hit(request.app.state.settings.researcher_rate_limit, "researcher", ip)


# ------------------------------------------------------- logging & headers
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_ctx.get(),
        }
        for key in ("method", "path", "status", "duration_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


_access = logging.getLogger("gateway.access")
_VALID_ID = re.compile(r"[A-Za-z0-9._-]{8,128}")
_COMMON = (
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "no-referrer"),
    ("cross-origin-opener-policy", "same-origin"),
)
API_CSP = "default-src 'none'; frame-ancestors 'none'"
APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


class SecurityMiddleware:
    """Request IDs, access logs, security headers and a request-body size limit."""

    def __init__(self, app: ASGIApp, *, hsts: bool, max_body_bytes: int) -> None:
        self.app = app
        self.hsts = hsts
        self.max_body = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        incoming = headers.get("x-request-id", "")
        request_id = incoming if _VALID_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        is_api = scope["path"].startswith("/api/")
        status_code, started = 500, time.perf_counter()

        declared = headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_body):
            body = b'{"detail":"request body too large"}'
            await send(
                {"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]}
            )
            await send({"type": "http.response.body", "body": body})
            request_id_ctx.reset(token)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body:
                    raise HTTPException(413, "request body too large")
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                h = MutableHeaders(scope=message)
                h["x-request-id"] = request_id
                for name, value in _COMMON:
                    h[name] = value
                h["content-security-policy"] = API_CSP if is_api else APP_CSP
                h["permissions-policy"] = "camera=(), geolocation=(), microphone=(self)"
                if is_api:
                    h["cache-control"] = "no-store"
                if self.hsts:
                    h["strict-transport-security"] = "max-age=63072000; includeSubDomains"
            await send(message)

        try:
            await self.app(scope, limited_receive, send_wrapper)
        finally:
            _access.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            request_id_ctx.reset(token)
